"""Explicit, code-bound instance inputs for diagnostic vertex translation.

This does not assign an engine buffer or select a runtime draw. Callers supply
one complete source-CB1 record and the wave-uniform instance builtin themselves.
Ordinary vertex tracing and material admission never create this context.
"""
from dataclasses import dataclass
import hashlib
import math
import re
import struct

from rc2016.shaders.ir import bits, from_bits
from rc2016.shaders.translator import Compiler


RECORD_BYTES = 272
CONTRACT = 'explicit-source-cb1-record-v1'
_LOAD = re.compile(r'(?:GCN_)?S_BUFFER_LOAD_DWORD(?:X(2|4|8|16))?\Z')


@dataclass(frozen=True)
class InstanceRecordContext:
    """One immutable record, with its original index and explicit provenance.

    ``record`` starts at CB1 byte ``272 * instance_index``. ``instance_vgpr``
    identifies a caller-proven wave-uniform builtin, not a vertex attribute.
    ``code_sha256`` scopes the context to exactly one native vertex program.
    The origin is a caller assertion, retained in receipts rather than proof
    that a captured record belongs to a different source draw.
    """
    record: bytes
    instance_index: int
    instance_vgpr: int
    code_sha256: str
    origin: str

    def __post_init__(self):
        if type(self.record) is not bytes or len(self.record) != RECORD_BYTES:
            raise ValueError('Instance context requires exactly 272 immutable bytes')
        if (type(self.instance_index) is not int or
                not 0 <= self.instance_index <= (0xffffffff - RECORD_BYTES + 1) // RECORD_BYTES):
            raise ValueError('Instance record address exceeds the unsigned 32-bit buffer window')
        if type(self.instance_vgpr) is not int or not 0 <= self.instance_vgpr < 256:
            raise ValueError('Instance builtin VGPR must be explicitly supplied')
        if not isinstance(self.code_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.code_sha256):
            raise ValueError('Instance context requires an exact lowercase code SHA-256')
        if not isinstance(self.origin, str) or not self.origin.strip():
            raise ValueError('Instance context requires a documented caller origin')

    @property
    def byte_start(self):
        return RECORD_BYTES * self.instance_index

    def receipt(self):
        return dict(contract=CONTRACT, source_api=1, record_bytes=RECORD_BYTES,
                    byte_start=self.byte_start, instance_index=self.instance_index,
                    instance_vgpr=self.instance_vgpr, code_sha256=self.code_sha256,
                    record_sha256=hashlib.sha256(self.record).hexdigest(), origin=self.origin,
                    runtime_binding_proven=False, game_equivalence=False)


class ContextCompiler(Compiler):
    """Resolve only complete, untainted source-CB1 scalar buffer descriptors."""
    def __init__(self, code, usage_slots, *, context):
        if not isinstance(context, InstanceRecordContext):
            raise ValueError('Explicit InstanceRecordContext required')
        if hashlib.sha256(code).hexdigest() != context.code_sha256:
            raise ValueError('Instance context code SHA-256 mismatch')
        super().__init__(code, usage_slots)
        self.context = context
        self.context_reads = []
        self.context_failures = []
        self.v[context.instance_vgpr] = self.ir.const(from_bits(context.instance_index))

    def load(self, instruction):
        word = instruction['word']
        base = ((word >> 9) & 63) * 2
        candidates = [self.descriptors.get(base + j, {}) for j in range(4)]
        if not any(d.get('kind') == 2 and d.get('api') == 1 for d in candidates):
            return super().load(instruction)
        match = _LOAD.fullmatch(instruction['name'])
        count = int(instruction['name'].rsplit('X', 1)[1]) if 'DWORDX' in instruction['name'] else 1
        proof = self.texture_descriptor(base, 2, 4)
        if (match is None or proof is None or proof.get('api') != 1 or
                any(base + j in self.buffer_descriptor_taint for j in range(4))):
            return self.unresolved_context_load(instruction, count,
                'Supplied CB1 read has no complete untainted scalar buffer descriptor')
        offset = ((word & 255) * 4 if word & 256 else
                  self.scalar_operand_bits(word & 255, None))
        dst = (word >> 15) & 127
        start = self.context.byte_start
        if (offset is None or type(offset) is not int or offset % 4 or
                offset < start or offset + 4 * count > start + RECORD_BYTES or dst + count > 128):
            return self.unresolved_context_load(instruction, count,
                'Supplied CB1 read has an unknown, unaligned or out-of-record span', offset)
        # Snapshot the entire read before overwriting any descriptor/register.
        values = struct.unpack_from(f'<{count}I', self.context.record, offset - start)
        for j, value in enumerate(values):
            reg = dst + j
            self.descriptors.pop(reg, None)
            self.s[reg] = self.ir.const(value)
            self.scalar_bits[reg] = value
        self.context_reads.append(dict(instruction_offset=instruction.get('relative', self.current),
                                       byte_offset=offset, record_offset=offset - start, words=count))

    def unresolved_context_load(self, instruction, count, reason, offset=None):
        """Reject the whole data span while retaining unrelated vertex exports."""
        dst = (instruction['word'] >> 15) & 127
        if dst + count > 128:
            raise ValueError('Scalar buffer destination span exceeds SGPR range')
        failure = dict(instruction_offset=instruction.get('relative', self.current),
                       byte_offset=offset, words=count, reason=reason, memory_evaluated=False)
        for j in range(count):
            reg = dst + j
            self.descriptors.pop(reg, None)
            self.scalar_bits.pop(reg, None)
            self.s[reg] = self.unknown(reason, component=j, context_failure=failure)
        self.context_failures.append(failure)

    def instruction(self, instruction):
        name = instruction['name'].replace('GCN_', '').replace('_E64', '')
        if name == 'V_READFIRSTLANE_B32' and instruction['kind'] == 5:
            # A finite constant expression is equal in every participating lane.
            # Dynamic vertex values and non-finite bit carriers keep the ordinary
            # unsupported path; no arbitrary first-lane value is supplied.
            raw = instruction['fields']['src'][0]
            value = self.ir.value(self.getv(raw - 256)) if raw >= 256 else None
            if value is not None and math.isfinite(value):
                dst = instruction['fields']['dst'][0]
                value_bits = bits(value)
                self.descriptors.pop(dst, None)
                self.s[dst] = self.ir.const(value_bits)
                self.scalar_bits[dst] = value_bits
                return
        return super().instruction(instruction)

    def context_receipt(self):
        return dict(self.context.receipt(), reads=list(self.context_reads),
                    unresolved_reads=list(self.context_failures),
                    memory_evaluated=bool(self.context_reads),
                    scope='Only the explicitly supplied record; other buffers remain unresolved')
