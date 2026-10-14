"""Pass-scoped vector proof for an explicit ordinary-static preview policy.

The source CPU material bits select the flags for a caller-chosen deferred
static mode. Runtime pass selection and absence of alternate/deformed streams
are preview assumptions. Other record words stay symbolic, never zero-filled.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import math
import struct

from rc2016.shaders import interpolants
from rc2016.shaders.ir import bits, from_bits
from rc2016.shaders.translator import Compiler, decoder


CONTRACT = 'static-packed-vector-preview-v1'


class StaticCompiler(Compiler):
    def __init__(self, code, slots, flags):
        super().__init__(code, slots)
        self.flags = flags
        self.v[3] = self.ir.const(from_bits(0))
        self.fetch_call = True
        self.runtime_conditions = True

    def load(self, instruction):
        word = instruction['word']
        base = 2 * ((word >> 9) & 63)
        # A fresh unconditional fetch of all four declared CB1 descriptor words
        # replaces old branch-dependent contents. Keep the ordinary compiler's
        # historical taint policy; only this explicit preview can prove refresh.
        offset = (word & 255) * 4 if word & 256 else self.scalar_operand_bits(word & 255, None)
        pointer = self.texture_descriptor(base, 0x1b, 2)
        slots = [s for s in self.slots if type(offset) is int and s['reg'] == 16 + offset // 4]
        slot = slots[0] if len(slots) == 1 else None
        if (instruction['name'].removeprefix('GCN_') == 'S_LOAD_DWORDX4'
                and pointer is not None and self.ir.value(self.bp) == 1 and self.ir.value(self.exec) == 1
                and not any(base + j in self.buffer_descriptor_taint for j in range(2))
                and type(offset) is int and offset >= 0 and offset % 4 == 0
                and slot is not None and slot.get('kind') == 2 and slot.get('api') == 1):
            super().load(instruction)
            dst = (word >> 15) & 127
            if dst + 4 > 128:
                raise ValueError('Static descriptor destination span exceeds SGPR range')
            expected = [dict(slot, word=j) for j in range(4)]
            if [self.descriptors.get(dst + j) for j in range(4)] == expected:
                for reg in range(dst, dst + 4):
                    self.buffer_descriptor_taint.discard(reg)
            return
        candidates = [self.descriptors.get(base + j, {}) for j in range(4)]
        if not any(d.get('kind') == 2 and d.get('api') == 1 for d in candidates):
            return super().load(instruction)
        name = instruction['name'].removeprefix('GCN_')
        count = int(name.rsplit('X', 1)[1]) if 'DWORDX' in name else 1
        dst = (word >> 15) & 127
        if dst + count > 128:
            raise ValueError('Static CB1 destination span exceeds SGPR range')
        proof = self.texture_descriptor(base, 2, 4)
        offset = (word & 255) * 4 if word & 256 else self.scalar_operand_bits(word & 255, None)
        valid = (proof is not None and proof.get('api') == 1
                 and name in ('S_BUFFER_LOAD_DWORD', 'S_BUFFER_LOAD_DWORDX2',
                              'S_BUFFER_LOAD_DWORDX4', 'S_BUFFER_LOAD_DWORDX8', 'S_BUFFER_LOAD_DWORDX16')
                 and not any(base + j in self.buffer_descriptor_taint for j in range(4))
                 and type(offset) is int and offset % 4 == 0
                 and 0 <= offset and offset + 4 * count <= 272)
        for j in range(count):
            reg = dst + j
            self.descriptors.pop(reg, None)
            self.scalar_bits.pop(reg, None)
            if not valid:
                self.s[reg] = self.unknown('Static CB1 read lacks complete descriptor or bounded span', component=j)
            elif offset + 4 * j == 240:
                self.s[reg] = self.ir.const(self.flags)
                self.scalar_bits[reg] = self.flags
            else:
                dword = offset // 4 + j
                self.s[reg] = self.ir.node('input', name=f'CB1[{dword}]', kind='cb', api=1, dword=dword)

    def instruction(self, instruction):
        if instruction['name'].replace('GCN_','')=='V_READFIRSTLANE_B32' and instruction['kind']==5:
            raw=instruction['fields']['src'][0]
            value=self.ir.value(self.getv(raw-256)) if raw>=256 else None
            if value is not None and math.isfinite(value):
                dst=instruction['fields']['dst'][0];word=bits(value)
                self.descriptors.pop(dst,None);self.s[dst]=self.ir.const(word);self.scalar_bits[dst]=word
                return
        return super().instruction(instruction)


def static_vector_evidence(data, program, material_flags):
    """Exact pass producers must agree under both CPU material-flag branches.

    Other CB1 words remain symbolic. The default all-path adapter is unchanged.
    Neither a geometry-normal alias nor a captured record is supplied here.
    """
    if type(material_flags) is not int or material_flags & 3:
        return {}, []
    return deepcopy(_evidence(bytes(data), program['header_offset'], material_flags))


@lru_cache(maxsize=64)
def _evidence(data, header_offset, material_flags):
    flags = tuple(sorted({1, 1 | ((material_flags << 1) & 0x60)}))
    headers = interpolants.shader_headers(data)
    pixel = next((h for h in headers if h['type'] == interpolants.PIXEL
                  and h['header_offset'] == header_offset), None)
    if pixel is None:
        return {}, []
    from rc2016.shaders.translator import sections
    spans = sections(data)
    base, size = spans[0xB6D49474]
    shader_base, _ = spans[0x873D1E6C]
    if size % 20:
        return {}, []
    records = [struct.unpack_from('<5I', data, base + i * 20) for i in range(size // 20)]
    ps_indices = [i for i, r in enumerate(records) if shader_base + r[3] + 36 == header_offset]
    if len(ps_indices) != 1:
        return {}, []
    pass_index = ps_indices[0] // 13
    offsets = {shader_base + r[3] + 36 for r in records[pass_index * 13:(pass_index + 1) * 13] if r[4]}
    vertices = [h for h in headers if h['type'] != interpolants.PIXEL and h['header_offset'] in offsets]
    if not vertices:
        return {}, []
    compiled = []
    for row in vertices:
        if row.get('layout') != 'verified' or 'code' not in row:
            return {}, []
        if any(e['vgpr'] <= 3 < e['vgpr'] + e['size'] for e in row['inputs']):
            return {}, []
        variants = []
        for value in flags:
            compiler = StaticCompiler(row['code'], row['usage_slots'], value)
            for e in row['inputs']:
                for j in range(e['size']):
                    compiler.v[e['vgpr'] + j] = compiler.ir.node(
                        'input', name=f'vertex.semantic{e["semantic"]}.{"xyzw"[j]}',
                        kind='vertex_input', semantic=e['semantic'], channel=j)
            try:
                compiler.run(decoder().decode_stream(row['code'], 0))
            except (ValueError, KeyError, IndexError, struct.error):
                return {}, []
            variants.append(compiler)
        compiled.append((row, variants))
    expected = []
    for r in range(3):
        form = {}
        for c, ch in enumerate('xyz'):
            word, vertex = f'CB1[{32 + 4 * c + r}]', f'vertex.semantic1.{ch}'
            form[(word,)] = -1.
            form[tuple(sorted((word, vertex)))] = 2.
        expected.append(form)
    matched, receipts = {}, []
    for attribute, entry in enumerate(pixel['inputs']):
        semantic, proof = entry['semantic'], []
        for row, variants in compiled:
            exports = [e for e in row['exports'] if e['semantic'] == semantic]
            if len(exports) != 1:
                proof = []; break
            for compiler in variants:
                channels = compiler.exports.get(32 + exports[0]['slot'], [])[:3]
                if (len(channels) != 3 or any(n is None for n in channels)
                        or [interpolants.linear_form(compiler.ir, n, 1024) for n in channels] != expected):
                    break
            else:
                proof.append(dict(vertex_header_offset=row['header_offset'],
                                  vertex_code_sha256=hashlib.sha256(row['code']).hexdigest(),
                                  semantic=semantic, export_slot=exports[0]['slot']))
                continue
            proof = []; break
        if proof:
            matched[attribute] = semantic
            receipts.append(dict(attribute=attribute, semantic=semantic, pass_index=pass_index,
                static_flags_verified=list(flags), material_flags=material_flags, producers=proof,
                policy='Caller-selected ordinary static mode; no alternate stream, deformation or billboard',
                runtime_selection_proven=False))
    return matched, receipts
