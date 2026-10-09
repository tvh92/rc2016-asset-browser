"""Streaming aligned field classification with competing structural hypotheses."""
from collections import Counter, defaultdict
import math
import numpy as np

from .source import tag_name

DISTINCT_LIMIT = 256
PROBE_BYTES = 512


def strength(matches, tests, specific=False):
    ratio = matches / tests if tests else 0
    if specific and tests >= 20 and ratio >= .99:
        return 'high'
    if specific and tests >= 5 and ratio >= .9:
        return 'medium'
    return 'low'


class Column:
    def __init__(self, offset, width):
        self.offset, self.width = offset, width
        self.total, self.minimum, self.maximum = 0, None, None
        self.values = Counter()
        self.distinct_capped = False
        self.bits = [0] * (width * 8)
        self.predicates = {}
        self.examples = []
        self.float_min, self.float_max = None, None
        self.hash_examples = []

    def evidence(self, name, mask, asset, offsets, values, specific=False):
        row = self.predicates.setdefault(name, dict(matches=0, tests=0, counterexamples=[], specific=specific))
        row['tests'] += len(mask)
        row['matches'] += int(mask.sum())
        for i in np.flatnonzero(~mask)[:max(0, 3 - len(row['counterexamples']))]:
            row['counterexamples'].append(dict(file=asset.path, offset=int(offsets[i]) + self.offset,
                                               value=int(values[i])))

    def add(self, values, asset, offsets, hash_index, starts, counts):
        if not len(values):
            return
        self.total += len(values)
        low, high = int(values.min()), int(values.max())
        self.minimum = low if self.minimum is None else min(low, self.minimum)
        self.maximum = high if self.maximum is None else max(high, self.maximum)
        unique, frequencies = np.unique(values, return_counts=True)
        if len(unique) > DISTINCT_LIMIT:
            self.distinct_capped = True
        for v, n in zip(unique[:DISTINCT_LIMIT], frequencies[:DISTINCT_LIMIT]):
            v = int(v)
            if v in self.values or len(self.values) < DISTINCT_LIMIT:
                self.values[v] += int(n)
            else:
                self.distinct_capped = True
        for bit in range(self.width * 8):
            self.bits[bit] += int(np.count_nonzero(values & (1 << bit)))
        for i in range(min(len(values), 3 - len(self.examples))):
            self.examples.append(dict(file=asset.path, offset=int(offsets[i]) + self.offset, value=int(values[i])))
        self.evidence('small unsigned integer (<=65535)', values <= 65535, asset, offsets, values)
        if self.width in (4, 8):
            floats = values.view('<f4' if self.width == 4 else '<f8')
            plausible = np.isfinite(floats) & (np.abs(floats) <= 1e7) & ((floats == 0) | (np.abs(floats) >= 1e-6))
            self.evidence('finite plausible float', plausible, asset, offsets, values)
            if plausible.any():
                lo, hi = float(floats[plausible].min()), float(floats[plausible].max())
                self.float_min = lo if self.float_min is None else min(lo, self.float_min)
                self.float_max = hi if self.float_max is None else max(hi, self.float_max)
            self.evidence('string offset', np.isin(values, list(asset.strings)), asset, offsets, values, True)
            matched = hash_index.match(values, self.width)
            self.evidence('known string hash', matched, asset, offsets, values, True)
            for i in np.flatnonzero(matched)[:max(0, 3 - len(self.hash_examples))]:
                string, algorithm = hash_index.maps[self.width][int(values[i])]
                self.hash_examples.append(dict(value=int(values[i]), string=string, algorithm=algorithm))
        for (tag, stride), count in sorted(counts.items()):
            if count:
                self.evidence(f'index into {tag_name(tag)} (stride {stride})', values < count,
                              asset, offsets, values, True)
        if self.width >= 4:
            for target, (base, length) in sorted(asset.table.items()):
                self.evidence(f'absolute offset into {tag_name(target)}',
                              (values >= base) & (values < base + length), asset, offsets, values, True)
                self.evidence(f'relative nonzero offset into {tag_name(target)}',
                              (values > 0) & (values < length), asset, offsets, values)
            if starts:
                self.evidence('absolute record start', np.isin(values, starts), asset, offsets, values, True)
                heap_base = asset.table.get(0x06ABCAB2, (0, 0))[0]
                self.evidence('relative nonzero record start',
                              np.isin(values, [s - heap_base for s in starts if s > heap_base]),
                              asset, offsets, values, True)

    def finish(self, source_records):
        candidates = []
        if not self.distinct_capped and len(self.values) == 1:
            value = next(iter(self.values))
            candidates.append(dict(candidate='zero padding' if value == 0 else 'constant',
                                   confidence='high', matches=self.total, tests=self.total,
                                   value=value, counterexamples=[]))
        for name, row in self.predicates.items():
            if row['matches'] / row['tests'] < .8:
                continue
            candidates.append(dict(candidate=name, confidence=strength(row['matches'], row['tests'], row['specific']),
                                   matches=row['matches'], tests=row['tests'],
                                   counterexamples=row['counterexamples']))
        if not self.distinct_capped and 1 < len(self.values) <= 64:
            candidates.append(dict(candidate='enum or bit flags', confidence='low', matches=self.total,
                                   tests=self.total, distinct=len(self.values), counterexamples=[]))
        entropy = sum(-p * math.log2(p) - (1 - p) * math.log2(1 - p)
                      for n in self.bits if 0 < (p := n / self.total) < 1) / len(self.bits)
        if entropy > .65 and (self.distinct_capped or len(self.values) > 16):
            candidates.append(dict(candidate='high-entropy integer or unmatched hash', confidence='low',
                                   matches=self.total, tests=self.total, mean_bit_entropy=entropy,
                                   counterexamples=[]))
        candidates.sort(key=lambda c: ({'high': 0, 'medium': 1, 'low': 2}[c['confidence']],
                                        -c['matches'] / c['tests'], c['candidate']))
        return dict(offset=self.offset, width=self.width, records=self.total,
                    absent_records=source_records - self.total, unsigned_min=self.minimum, unsigned_max=self.maximum,
                    float_range=[self.float_min, self.float_max] if self.float_min is not None else None,
                    distinct=len(self.values), distinct_is_lower_bound=self.distinct_capped,
                    value_counts=[dict(value=k, count=v) for k, v in sorted(self.values.items())[:32]],
                    value_counts_complete=not self.distinct_capped and len(self.values) <= 32,
                    bit_set_counts=self.bits, mean_bit_entropy=entropy, candidates=candidates[:16],
                    examples=self.examples, hash_examples=self.hash_examples)


class Layout:
    def __init__(self, name, stride=None, evidence=None):
        self.name, self.stride, self.evidence = name, stride, evidence
        self.columns = {}
        self.total, self.files, self.truncated = 0, 0, 0
        self.structures = {}

    @np.errstate(invalid='ignore', over='ignore', divide='ignore')
    def add(self, asset, records, hashes):
        if not records:
            return
        self.total += len(records)
        self.files += 1
        probe = min(PROBE_BYTES, max(size for _, size in records))
        probe -= probe % 4
        self.truncated += sum(size > probe for _, size in records)
        raw = np.zeros((len(records), probe), dtype=np.uint8)
        lengths = np.array([min(size, probe) for _, size in records])
        offsets = np.array([off for off, _ in records])
        for i, (off, size) in enumerate(records):
            raw[i, :min(size, probe)] = np.frombuffer(asset.data, dtype=np.uint8, count=min(size, probe), offset=off)
        starts = [off for off, _ in asset.instance_records()]
        counts = asset.counts()
        for width in (4, 2, 8):
            for offset in range(0, probe - width + 1, width):
                present = lengths >= offset + width
                if not present.any():
                    continue
                values = raw[present, offset:offset + width].copy().view(f'<u{width}').ravel()
                column = self.columns.setdefault((offset, width), Column(offset, width))
                column.add(values, asset, offsets[present], hashes, starts, counts)
        floats = raw.view('<f4').reshape(len(records), -1)
        for offset in range(0, probe - 11, 4):
            present = lengths >= offset + 12
            v = floats[present, offset // 4:offset // 4 + 3]
            finite = np.isfinite(v).all(axis=1)
            norm = np.linalg.norm(v.astype(float), axis=1)
            self._structure(f'unit float3 at +0x{offset:X}', present.sum(), finite & (np.abs(norm - 1) < .001),
                            asset, offsets[present])
            if offset + 16 <= probe:
                present4 = lengths >= offset + 16
                vector4 = floats[present4, offset // 4:offset // 4 + 4]
                unit4 = np.isfinite(vector4).all(axis=1) & (np.abs(np.linalg.norm(vector4.astype(float), axis=1) - 1) < .001)
                self._structure(f'unit float4 at +0x{offset:X}', present4.sum(), unit4, asset, offsets[present4])
        if probe >= 64:
            present = lengths >= 64
            matrix = floats[present, :16].reshape(-1, 4, 4)
            affine = np.isfinite(matrix).all(axis=(1, 2)) & (matrix[:, :, 3] == [0, 0, 0, 1]).all(axis=1)
            self._structure('affine row-vector float4x4 at +0x0', present.sum(), affine, asset, offsets[present])
            rows = matrix[:, :3, :3].astype(float)
            unit = (np.abs(np.linalg.norm(rows, axis=2) - 1) < .001).all(axis=1)
            orthogonal = (np.abs((rows[:, 0] * rows[:, 1]).sum(axis=1)) < .001)
            orthogonal &= (np.abs((rows[:, 0] * rows[:, 2]).sum(axis=1)) < .001)
            orthogonal &= (np.abs((rows[:, 1] * rows[:, 2]).sum(axis=1)) < .001)
            self._structure('orthonormal matrix rows at +0x0', present.sum(), affine & unit & orthogonal,
                            asset, offsets[present])

    def _structure(self, name, tests, mask, asset, offsets):
        if not tests:
            return
        row = self.structures.setdefault(name, dict(tests=0, matches=0, counterexamples=[]))
        row['tests'] += int(tests)
        row['matches'] += int(mask.sum())
        for i in np.flatnonzero(~mask)[:max(0, 3 - len(row['counterexamples']))]:
            row['counterexamples'].append(dict(file=asset.path, offset=int(offsets[i])))

    def finish(self):
        return dict(source=self.name, stride=self.stride, source_evidence=self.evidence,
                    records=self.total, files=self.files, probe_bytes=PROBE_BYTES,
                    records_longer_than_probe=self.truncated,
                    fields=[c.finish(self.total) for _, c in sorted(self.columns.items())],
                    structures=[dict(candidate=name, confidence=strength(row['matches'], row['tests']), **row)
                                for name, row in sorted(self.structures.items()) if row['matches'] / row['tests'] >= .8])


def select_sources(sections):
    """One explicit divisibility hypothesis per undecoded fixed table, never the heap."""
    sources = {}
    for row in sections:
        tag = int(row['tag'], 16)
        if row['decoded'] or tag == 0x06ABCAB2 or any(i['serialized'] for i in row['dat1lib']):
            continue
        candidates = [i['stride'] for i in row['candidate_strides'] if i['stride'] <= PROBE_BYTES]
        preferred = [i['entry_size'] for i in row['dat1lib'] if i['entry_size'] in candidates]
        parallel = [p for p in row['parallel_counts']
                    if p['source_stride'] in candidates and p['source_stride'] > 4
                    and p['target'] in ('0xC6A5905E', '0x6987F172', '0x2236C47A', '0x2BA33702', '0x4130D903')
                    and p['target_stride'] == 4 and p['matches'] == p['tests'] and p['tests'] >= 2]
        if tag == 0x032D450A and 8 in candidates:
            preferred = [8]
        if parallel and tag != 0x032D450A:
            stride = min(parallel, key=lambda p: (-p['tests'], p['source_stride'], p['target']))['source_stride']
            reason = 'Consistent counts against an established pointer table; candidate record boundaries'
        elif preferred:
            stride = min(preferred)
            reason = 'Sibling dat1lib record stride, compatible with corpus sizes; candidate only'
        elif candidates:
            stride = max(candidates)
            reason = 'Largest enumerated common divisor <=512; competing divisors remain in census'
        else:
            continue
        sources[tag] = (stride, reason)
    return sources
