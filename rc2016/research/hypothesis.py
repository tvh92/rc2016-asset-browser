"""Explicit executable structural claims and whole-source counterexamples."""
from dataclasses import dataclass
import math
import struct

from .source import DATA_HEAP, INSTANCE_OFFSETS, tag_name


@dataclass(frozen=True)
class RecordSource:
    section: int = DATA_HEAP
    stride: int | None = None
    offsets_section: int | None = INSTANCE_OFFSETS
    size_field: int | None = 0x44
    minimum_size: int = 0xB0

    def records(self, asset):
        if self.section not in asset.table:
            return []
        base, size = asset.table[self.section]
        if self.offsets_section is None:
            if not self.stride or self.stride <= 0 or size % self.stride:
                raise ValueError('Fixed source size is not divisible by stride')
            return [(base + i, self.stride) for i in range(0, size, self.stride)]
        if self.offsets_section not in asset.table:
            return []
        offsets = asset.words(self.offsets_section)
        rows = []
        for i, rel in enumerate(offsets):
            end = offsets[i + 1] if i + 1 < len(offsets) else size
            length = end - rel
            if self.size_field is not None:
                if rel + self.size_field + 4 > size:
                    raise ValueError('Size field outside source')
                declared = struct.unpack_from('<I', asset.data, base + rel + self.size_field)[0]
                if declared < self.minimum_size or rel + declared > size + 7:
                    raise ValueError('Declared record size outside source')
                length = min(declared, size - rel)
            if rel < 0 or length < 0 or rel + length > size:
                raise ValueError('Record range outside source')
            rows.append((base + rel, length))
        return rows


@dataclass(frozen=True)
class Claim:
    name: str
    offset: int
    kind: str
    target: int | None = None
    target_stride: int = 4
    dtype: str = 'u32'
    minimum: float | None = None
    maximum: float | None = None

    def check(self, asset, offset, size):
        formats = {'u16': '<H', 'u32': '<I', 'u64': '<Q', 'f32': '<f'}
        if self.offset < 0:
            raise ValueError('Negative claim offset')
        if self.kind == 'float3_bounds':
            if self.offset + 12 > size:
                return False, 'float3 outside record'
            if not hasattr(asset, '_instance_bounds'):
                points = [struct.unpack_from('<3f', asset.data, off + 0x30) for off, _ in asset.instance_records()]
                asset._instance_bounds = [(min(p[i] for p in points), max(p[i] for p in points))
                                          for i in range(3)] if points else None
            if asset._instance_bounds is None:
                return False, 'No zone instance bounds'
            vector = struct.unpack_from('<3f', asset.data, offset + self.offset)
            passed = all(math.isfinite(v) and low <= v <= high
                         for v, (low, high) in zip(vector, asset._instance_bounds))
            return passed, list(vector) if all(map(math.isfinite, vector)) else [str(v) for v in vector]
        fmt = formats[self.dtype]
        if self.offset + struct.calcsize(fmt) > size:
            return False, 'Field outside record'
        value = struct.unpack_from(fmt, asset.data, offset + self.offset)[0]
        if self.kind in ('index', 'equals_count'):
            if self.target not in asset.table:
                return False, 'Target section absent'
            target_size = asset.table[self.target][1]
            if self.target_stride <= 0 or target_size % self.target_stride:
                return False, 'Target count not integral'
            count = target_size // self.target_stride
            return (value < count if self.kind == 'index' else value == count), value
        if self.kind == 'range':
            return (math.isfinite(value) and (self.minimum is None or value >= self.minimum)
                    and (self.maximum is None or value <= self.maximum)), value if math.isfinite(value) else str(value)
        raise ValueError(f'Unknown claim kind {self.kind}')


class Runner:
    def __init__(self, source, claims, examples=8):
        self.source, self.claims, self.examples = source, claims, examples
        self.results = [dict(name=c.name, kind=c.kind, offset=c.offset, dtype=c.dtype,
                             target=tag_name(c.target) if c.target is not None else None,
                             target_stride=c.target_stride, passes=0, fails=0, counterexamples=[])
                        for c in claims]
        self.files, self.records = 0, 0
        self.errors = []

    def add(self, asset):
        try:
            records = self.source.records(asset)
        except Exception as exc:
            self.errors.append(dict(file=asset.path, error=f'{type(exc).__name__}: {exc}'))
            return
        if records:
            self.files += 1
        self.records += len(records)
        for offset, size in records:
            for claim, result in zip(self.claims, self.results):
                try:
                    passed, observed = claim.check(asset, offset, size)
                except Exception as exc:
                    passed, observed = False, f'{type(exc).__name__}: {exc}'
                result['passes' if passed else 'fails'] += 1
                if not passed and len(result['counterexamples']) < self.examples:
                    result['counterexamples'].append(dict(file=asset.path, record_offset=offset,
                                                          field_offset=offset + claim.offset, observed=observed))

    def finish(self):
        return dict(files=self.files, records=self.records, source_errors=self.errors, claims=self.results)


def run(corpus, source, claims, examples=8):
    runner = Runner(source, claims, examples)
    for asset in corpus:
        runner.add(asset)
    return runner.finish()
