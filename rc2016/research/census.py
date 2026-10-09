"""Whole-corpus section sizes, pointer candidates and executable dat1lib cross-checks."""
from collections import Counter, defaultdict
import math
import statistics
import numpy as np

from .source import DECODED, PRIORITY, STRIDES, DATA_HEAP, tag_name


class Census:
    def __init__(self, cross_reference):
        self.xref = cross_reference
        self.rows = {}
        self.files = {}
        self.extensions = Counter()
        self.cross_checks = {}
        self.heap = {'files': 0, 'bytes': 0, 'referenced_bytes': 0, 'unclaimed_bytes': 0,
                     'gap_sizes': Counter(), 'examples': [], 'errors': []}

    def add(self, asset):
        self.extensions[asset.extension] += 1
        self.files[asset.path] = {tag: size for tag, (_, size) in asset.table.items()}
        for tag, (offset, size) in sorted(asset.table.items()):
            row = self.rows.setdefault(tag, dict(sizes=[], extensions=set(), pointer_tests=0,
                                               string_hits=0, pointers=Counter(), examples=[]))
            row['sizes'].append(size)
            row['extensions'].add(asset.extension)
            if len(row['examples']) < 3:
                row['examples'].append(dict(file=asset.path, offset=offset, size=size))
            values = np.frombuffer(asset.data, dtype='<u4', count=size // 4, offset=offset)
            row['pointer_tests'] += len(values)
            if asset.strings and len(values):
                row['string_hits'] += int(np.isin(values, list(asset.strings)).sum())
            for other, (start, length) in sorted(asset.table.items()):
                if not length or other == tag:
                    continue
                # Zero is intentionally excluded: otherwise zero padding mimics a relative pointer.
                absolute = (values >= start) & (values < start + length)
                relative = (values > 0) & (values < length)
                row['pointers'][(other, 'absolute')] += int(absolute.sum())
                row['pointers'][(other, 'relative_nonzero')] += int(relative.sum())
            for check in self.xref.probe(asset, tag):
                key = (tag, check['class_name'], check.get('version_hypothesis'))
                total = self.cross_checks.setdefault(key, dict(status_counts=Counter(), examples=[],
                                                               schema=defaultdict(set), tests=0,
                                                               leftover_bytes=0, unmeasured=0))
                total['tests'] += 1
                total['status_counts'][check['status']] += 1
                total['leftover_bytes'] += check.get('leftover_bytes') or 0
                total['unmeasured'] += check.get('consumption_evidence') == 'not measured'
                if sum(x['status'] == check['status'] for x in total['examples']) < 3:
                    total['examples'].append(dict(file=asset.path, offset=offset,
                                                  **{k: v for k, v in check.items() if k != 'schema'}))
                for name, types in check.get('schema', {}).items():
                    total['schema'][name].update(types)
        self._heap(asset)

    def _heap(self, asset):
        if DATA_HEAP not in asset.table:
            return
        base, size = asset.table[DATA_HEAP]
        self.heap['files'] += 1
        self.heap['bytes'] += size
        try:
            spans = [(off - base, off - base + length) for off, length in asset.instance_records()]
            if 0xBCF43558 in asset.table:
                spans += [(off, min(off + 0x28C, size)) for off in asset.words(0xBCF43558)
                          if off < size]
            cursor, covered = 0, 0
            for start, end in sorted(spans):
                if start > cursor:
                    self.heap['gap_sizes'][start - cursor] += 1
                    if len(self.heap['examples']) < 8:
                        self.heap['examples'].append(dict(file=asset.path, offset=base + cursor,
                                                          size=start - cursor))
                covered += max(0, end - max(cursor, start))
                cursor = max(cursor, end)
            if cursor < size:
                self.heap['gap_sizes'][size - cursor] += 1
                if len(self.heap['examples']) < 8:
                    self.heap['examples'].append(dict(file=asset.path, offset=base + cursor, size=size - cursor))
            self.heap['referenced_bytes'] += covered
            self.heap['unclaimed_bytes'] += size - covered
        except Exception as exc:
            self.heap['errors'].append(dict(file=asset.path, error=str(exc)))

    def _parallel(self, tag, strides):
        source_files = [(p, sizes) for p, sizes in self.files.items() if tag in sizes]
        pairs = defaultdict(lambda: dict(tests=0, matches=0, counterexamples=[]))
        for name, sizes in source_files:
            for target, target_size in sorted(sizes.items()):
                if target == tag:
                    continue
                target_strides = {4}
                target_strides.update(i['entry_size'] for i in self.xref.metadata(target) if i['entry_size'])
                if target == 0x657512BB:
                    target_strides.add(88)
                for target_stride in sorted(target_strides):
                    if target_size == 0 or target_size % target_stride:
                        continue
                    for stride in strides:
                        pair = pairs[(stride, target, target_stride)]
                        pair['tests'] += 1
                        match = sizes[tag] > 0 and sizes[tag] % stride == 0 and sizes[tag] // stride == target_size // target_stride
                        pair['matches'] += match
                        if not match and len(pair['counterexamples']) < 3:
                            pair['counterexamples'].append(dict(file=name, source_size=sizes[tag],
                                                               target_size=target_size))
        candidates = []
        for (stride, target, target_stride), pair in pairs.items():
            if not pair['matches'] or pair['matches'] / pair['tests'] < .75:
                continue
            confidence = 'low'  # Equal counts establish a candidate relationship, not record boundaries.
            candidates.append(dict(source_stride=stride, target=tag_name(target), target_stride=target_stride,
                                   confidence=confidence, **pair))
        return sorted(candidates, key=lambda p: (-p['matches'], p['source_stride'], p['target'], p['target_stride']))[:20]

    def finish(self):
        result = []
        for tag, row in self.rows.items():
            sizes = row['sizes']
            gcd = math.gcd(*sizes)
            library_strides = {i['entry_size'] for i in self.xref.metadata(tag) if i['entry_size']}
            strides = sorted(set(STRIDES) | library_strides)
            candidates = [dict(stride=s, matches=sum(v > 0 and v % s == 0 for v in sizes),
                               tests=sum(v > 0 for v in sizes), confidence='low',
                               evidence='Divisibility only; no record boundary is established')
                          for s in strides if any(v > 0 for v in sizes) and all(v % s == 0 for v in sizes)]
            decoded = {ext: DECODED.get(ext, {}).get(tag) for ext in sorted(row['extensions'])}
            partial = any(v and v.startswith('partially') for v in decoded.values())
            known = all(decoded.values()) and not partial
            tests = row['pointer_tests']
            pointers = [dict(target=tag_name(target), addressing=mode, matches=hits, tests=tests,
                             ratio=hits / tests if tests else 0, confidence='low')
                        for (target, mode), hits in row['pointers'].items() if hits and tests and hits / tests >= .25]
            result.append(dict(tag=tag_name(tag), files=len(sizes), bytes=sum(sizes),
                               rank_score=len(sizes) * sum(sizes), decoded=known, partial=partial,
                               decoder_coverage=decoded, priority=tag in PRIORITY,
                               size_min=min(sizes), size_median=statistics.median(sizes), size_max=max(sizes),
                               size_gcd=gcd, candidate_strides=candidates,
                               parallel_counts=self._parallel(tag, [x['stride'] for x in candidates]),
                               string_offsets=dict(matches=row['string_hits'], tests=tests,
                                                   ratio=row['string_hits'] / tests if tests else 0,
                                                   confidence='low', evidence='NUL-terminated printable string starts'),
                               section_offsets=sorted(pointers, key=lambda p: (-p['ratio'], p['target'], p['addressing']))[:12],
                               dat1lib=self.xref.metadata(tag), examples=row['examples']))
        result.sort(key=lambda r: (r['decoded'], not r['priority'], -r['rank_score'], r['tag']))
        checks = []
        for (tag, cls, version), row in sorted(self.cross_checks.items(), key=lambda x: str(x[0])):
            checks.append(dict(tag=tag_name(tag), class_name=cls, version_hypothesis=version,
                               tests=row['tests'], status_counts=dict(sorted(row['status_counts'].items())),
                               leftover_bytes=row['leftover_bytes'], consumption_unmeasured=row['unmeasured'],
                               schema={k: sorted(v) for k, v in sorted(row['schema'].items())}, examples=row['examples']))
        heap = dict(self.heap)
        heap['gap_sizes'] = [dict(size=k, count=v) for k, v in sorted(heap['gap_sizes'].items())]
        heap['confidence'] = 'high'
        heap['evidence'] = 'Byte ranges referenced by existing instance/decal layouts; unclaimed includes alignment padding'
        return dict(sections=result, cross_checks=checks, heap=heap,
                    extensions=dict(sorted(self.extensions.items())))
