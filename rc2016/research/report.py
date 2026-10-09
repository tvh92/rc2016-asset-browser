"""Deterministic JSON and ranked Markdown reports, with bounded streaming analysis."""
import hashlib
import json
from pathlib import Path

from .census import Census
from .correlate import Correlations, ModelFeatures, zone_features
from .cross_reference import CrossReference, HashIndex
from .fields import Layout, select_sources
from .hypothesis import Claim, RecordSource, Runner
from .source import DATA_HEAP, INSTANCE_OFFSETS, tag_name, namespace

SCHEMA = 'rc2016-research-v1'


def build(corpus, model_features=False, progress=None):
    xref = CrossReference()
    hashes = HashIndex(corpus.paths)
    census = Census(xref)
    fingerprint = hashlib.sha256()
    for n, asset in enumerate(corpus, 1):
        census.add(asset)
        hashes.add(asset.strings.values())
        fingerprint.update((asset.path + '\0' + asset.digest + '\n').encode())
        if progress and n % 25 == 0:
            progress('census', n, len(corpus.entries))
    base = census.finish()
    sources = select_sources(base['sections'])
    layouts = {}
    for tag, (stride, evidence) in sources.items():
        layouts[tag] = Layout(tag_name(tag), stride, evidence)
    correlations = Correlations()
    metadata = ModelFeatures(corpus) if model_features else None
    errors, decoded_records = [], 0
    runner = Runner(RecordSource(), [Claim('Established model-index invariant', 0x58, 'index', target=0xC6A5905E),
                                     Claim('Established minimum record-size invariant', 0x44, 'range', minimum=0xB0)])
    record_sizes, tails, instance_namespaces = {}, {}, {}
    for n, asset in enumerate(corpus, 1):
        try:
            records = asset.instance_records()
            scope = namespace(asset.path)
            if records:
                layout = layouts.setdefault('instances/' + scope, Layout(
                    'instance offsets 0x6987F172 into heap 0x06ABCAB2 (namespace ' + scope + ')',
                    evidence='Existing zone instance boundaries; unknown fields remain candidates; namespace populations are separate'))
                layout.add(asset, records, hashes)
                summary = instance_namespaces.setdefault(scope, dict(files=0, records=0, validated_records=0,
                                                                    record_sizes={}, tail_sizes={}))
                summary['files'] += 1
                summary['records'] += len(records)
                for _, size in records:
                    summary['record_sizes'][size] = summary['record_sizes'].get(size, 0) + 1
                    tail_size = max(0, size - 0xB0)
                    summary['tail_sizes'][tail_size] = summary['tail_sizes'].get(tail_size, 0) + 1
            for offset, size in records:
                record_sizes[size] = record_sizes.get(size, 0) + 1
                tail_size = max(0, size - 0xB0)
                tails[tail_size] = tails.get(tail_size, 0) + 1
            for tag, (stride, _) in sources.items():
                if tag in asset.table:
                    offset, size = asset.table[tag]
                    if size % stride:
                        raise ValueError(f'{tag_name(tag)} does not fit candidate stride {stride}')
                    layouts[tag].add(asset, [(offset + i, stride) for i in range(0, size, stride)], hashes)
            runner.add(asset)
        except Exception as exc:
            errors.append(dict(file=asset.path, stage='fields', error=f'{type(exc).__name__}: {exc}'))
        if {DATA_HEAP, INSTANCE_OFFSETS, 0xC6A5905E} <= asset.table.keys():
            try:
                validated = zone_features(asset, correlations, metadata)
                decoded_records += validated
                if validated:
                    instance_namespaces[namespace(asset.path)]['validated_records'] += validated
            except Exception as exc:
                errors.append(dict(file=asset.path, stage='correlations', error=f'{type(exc).__name__}: {exc}'))
        if progress and n % 25 == 0:
            progress('fields', n, len(corpus.entries))
    for summary in instance_namespaces.values():
        summary['record_sizes'] = [dict(size=k, records=v) for k, v in sorted(summary['record_sizes'].items())]
        summary['tail_sizes'] = [dict(size=k, records=v) for k, v in sorted(summary['tail_sizes'].items())]
    namespaces = {}
    for path in census.files:
        scope = namespace(path)
        namespaces[scope] = namespaces.get(scope, 0) + 1
    result = dict(schema=SCHEMA, evidence_policy='Structure candidates, never decoded meanings',
                  corpus=dict(selected_files=len(corpus.entries), scanned_files=sum(base['extensions'].values()),
                              extensions=base.pop('extensions'), namespaces=dict(sorted(namespaces.items())),
                              decoded_content_sha256=fingerprint.hexdigest(),
                              extensions_selected=list(corpus.extensions), level=corpus.level, limit=corpus.limit,
                              repeat_verification=True if corpus.archive else None,
                              scan_errors=[dict(file=p, error=e) for p, e in sorted(corpus.errors.items())]),
                  confidence=dict(high='Direct byte predicate, >=20 tests and >=99% agreement, or exact constant',
                                  medium='Specific structural predicate, >=5 tests and >=90% agreement',
                                  low='Ambiguous, divisibility-only, small sample, or exploratory association',
                                  note='Confidence concerns the reported structure; it never establishes semantic meaning'),
                  **base, layouts=[layout.finish() for layout in layouts.values() if layout.total],
                  instance_summary=dict(validated_records=decoded_records, by_namespace=dict(sorted(instance_namespaces.items())),
                                        observed_record_sizes=[dict(size=s, records=n) for s, n in sorted(record_sizes.items())],
                                        observed_tail_sizes=[dict(size=s, records=n) for s, n in sorted(tails.items())]),
                  correlations=correlations.finish(), hypotheses=runner.finish(), analysis_errors=errors,
                  model_features=dict(enabled=model_features, models_tested=len(metadata.models) if metadata else 0,
                                      models_readable=sum(v is not None for v in metadata.models.values()) if metadata else 0,
                                      errors=[dict(path=p, error=e) for p, e in sorted(metadata.errors.items())] if metadata else [],
                                      note='Metadata only; graph paths are source references, not decoded shader behavior'),
                  limits=dict(field_probe_bytes=512, field_distinct_values=256,
                              layouts='One explicit divisibility/reader-stride hypothesis per eligible undecoded table',
                              examples='First deterministic counterexamples, maximum 3 per field predicate',
                              vectors='Unit float3/float4 windows and affine/orthonormal matrix candidates'))
    return result


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def markdown(report):
    corpus = report['corpus']
    lines = ['# Level/zone structural research report', '',
             'All inferred layouts and associations below are candidates. Reader compatibility does not establish game semantics.', '',
             f"Scanned {corpus['scanned_files']}/{corpus['selected_files']} selected assets: " +
             ', '.join(f'{v} {k}' for k, v in corpus['extensions'].items()) + '.', '',
             f"Namespaces: {', '.join(f'{k}: {v} assets' for k, v in corpus['namespaces'].items())}.", '',
             f"Content fingerprint: `{corpus['decoded_content_sha256']}`.", '',
             '## Open items to inspect first', '',
             '- Instance `+0x5C`: raw flag distributions and associations below.',
             '- Optional `+0xB0` tail: coverage and aligned field candidates in the instance layout.',
             '- Heap `0x06ABCAB2`: ranges not claimed by instance/decal references.',
             '- Material pairs `0x032D450A` and spawn records `0x3395AEC1`: census and candidate layouts.',
             '- dat1lib cross-check and serialized schemas: real reader executions, exceptions and unread-byte evidence.', '',
             '## Instance flags and tails', '',
             f"Existing placement invariants validated {report['instance_summary']['validated_records']} records.", '',
             'Instance layouts and correlations keep archive namespaces separate. The `levels` namespace is the main corpus; `rc1` assets are additional legacy entries.', '']
    for scope, summary in report['instance_summary']['by_namespace'].items():
        lines += [f"### Namespace {scope}", '',
                  f"{summary['validated_records']} validated records in {summary['files']} files.", '',
                  '| Tail bytes observed | Records |', '| --- | ---: |']
        rows = sorted(summary['tail_sizes'], key=lambda x: (-x['records'], x['size']))[:32]
        lines.extend(f"| {row['size']} | {row['records']} |" for row in rows)
        if len(summary['tail_sizes']) > 32:
            lines += ['', 'The table shows the 32 most frequent tail sizes; JSON contains the full distribution.']
        lines.append('')
    for layout in report['layouts']:
        if layout['source'].startswith('instance offsets'):
            field = next((f for f in layout['fields'] if f['offset'] == 0x5C and f['width'] == 4), None)
            if field:
                lines += ['', f"Raw `+0x5C` values for {cell(layout['source'])} (bounded distribution; see JSON for completeness):", '',
                          '| Raw value | Records |', '| --- | ---: |']
                lines.extend(f"| `0x{r['value']:08X}` | {r['count']} |" for r in field['value_counts'])
    lines += ['', '## Strongest per-bit associations', '', report['correlations']['caution'], '',
              '`n11`: bit and feature; `n10`: bit only; `n01`: feature only; `n00`: neither. Confidence is low for every association.', '',
              '| Bit | Feature | Phi | n11 | n10 | n01 | n00 | Pool |', '| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in report['correlations']['associations']:
        lines.append(f"| {r['bit']} | {cell(r['feature'])} | {r['phi']:.4f} | {r['n11']} | {r['n10']} | {r['n01']} | {r['n00']} | {r['pool']} |")
    heap = report['heap']
    lines += ['', '## Heap coverage', '',
              f"{heap['bytes']} bytes in {heap['files']} files; {heap['referenced_bytes']} referenced bytes, "
              f"{heap['unclaimed_bytes']} unclaimed bytes. Unclaimed ranges include alignment padding.", '',
              '| File | Absolute offset | Unclaimed bytes |', '| --- | --- | ---: |']
    lines.extend(f"| {cell(r['file'])} | `0x{r['offset']:X}` | {r['size']} |" for r in heap['examples'])
    lines += ['', '## Ranked section census', '',
              'Undecoded sections first; requested open items precede other undecoded tags. Within each group, score = files × total section bytes.', '',
              '| Tag | Files | Bytes | Min / median / max | GCD | Candidate strides | Decoder coverage | dat1lib hypothesis |',
              '| --- | ---: | ---: | --- | ---: | --- | --- | --- |']
    for r in report['sections']:
        names = ', '.join(i['comment_name'] or i['class_name'] for i in r['dat1lib'])
        strides = ', '.join(str(i['stride']) for i in r['candidate_strides'])
        coverage = 'decoded' if r['decoded'] else 'partial' if r['partial'] else 'undecoded'
        lines.append(f"| `{r['tag']}` | {r['files']} | {r['bytes']} | {r['size_min']} / {r['size_median']} / {r['size_max']} | {r['size_gcd']} | {strides} | {coverage} | {cell(names)} |")
    lines += ['', '### Pointer and parallel-table evidence', '',
              'Offset matches are range checks; relative nonzero checks exclude zero padding. Divisibility and matching counts alone have low confidence.', '']
    for r in report['sections']:
        if r['decoded'] or not (r['parallel_counts'] or r['string_offsets']['matches']):
            continue
        lines += [f"#### {r['tag']}", '']
        ptr = r['string_offsets']
        lines += [f"String-start candidates: {ptr['matches']}/{ptr['tests']} u32 words.", '']
        for p in r['parallel_counts'][:5]:
            lines.append(f"- Stride {p['source_stride']} vs {p['target']} stride {p['target_stride']}: {p['matches']}/{p['tests']} files; {p['confidence']} confidence; counterexamples `{cell(json.dumps(p['counterexamples']))}`.")
        lines.append('')
    lines += ['## dat1lib cross-check', '',
              'These are sibling-engine hypotheses. `parses_cleanly` means construction completed; consumption is unmeasured unless a stride or generic serialized trace establishes it. Opaque readers are reported separately.', '',
              '| Tag | Reader | Version hypothesis | Tests | Status counts | Leftover bytes | Unmeasured consumption |',
              '| --- | --- | --- | ---: | --- | ---: | ---: |']
    for r in report['cross_checks']:
        lines.append(f"| {r['tag']} | {r['class_name']} | {r['version_hypothesis']} | {r['tests']} | {cell(json.dumps(r['status_counts'], sort_keys=True))} | {r['leftover_bytes']} | {r['consumption_unmeasured']} |")
    for r in report['cross_checks']:
        failures = [x for x in r['examples'] if x['status'] == 'raises']
        if failures:
            f = failures[0]
            lines += ['', f"- {r['tag']} / {r['class_name']}: `{cell(f['file'])}` at `0x{f['offset']:X}`: {cell(f['error'])}"]
    lines += ['', '## Serialized schemas', '',
              'The generic dat1lib reader is called directly, including for tags whose I16 subclasses bypass reading. Only paths and types are emitted. A rejected version stays visible.', '']
    for r in report['cross_checks']:
        if r['class_name'] != 'Generic SerializedSection':
            continue
        lines += [f"### {r['tag']} / version hypothesis {r['version_hypothesis']}", '',
                  f"Status counts: `{json.dumps(r['status_counts'], sort_keys=True)}`; skipped bytes: {r['leftover_bytes']}.", '',
                  '| Key path | Observed types |', '| --- | --- |']
        for key, types in list(r['schema'].items())[:1000]:
            lines.append(f'| `{cell(key)}` | {", ".join(types)} |')
        if len(r['schema']) > 1000:
            lines += ['', f"Markdown shows 1000/{len(r['schema'])} schema paths; JSON contains all paths."]
        lines.append('')
    lines += ['## Draft layout candidates', '',
              'Overlapping 2-, 4-, and 8-byte interpretations are retained in JSON. Tables show 4-byte slots. Range checks alone do not choose between integers, offsets, flags or floats.', '']
    for layout in report['layouts']:
        lines += [f"### {layout['source']}", '', str(layout['source_evidence']), '',
                  f"{layout['records']} records in {layout['files']} files. Probe covers at most {layout['probe_bytes']} bytes; {layout['records_longer_than_probe']} records extend beyond it.", '',
                  '| Offset | Type candidate | Evidence | Confidence | Examples / counterexamples |', '| --- | --- | --- | --- | --- |']
        for field in layout['fields']:
            if field['width'] != 4:
                continue
            candidate = field['candidates'][0] if field['candidates'] else None
            name = candidate['candidate'] if candidate else 'unclassified u32'
            evidence = f"{candidate['matches']}/{candidate['tests']}" if candidate else f"{field['records']} observations"
            evidence += f"; absent {field['absent_records']}; distinct {'>=' if field['distinct_is_lower_bound'] else ''}{field['distinct']}"
            if candidate and 'float' in name:
                evidence += f"; range {field['float_range']}"
            examples = candidate['counterexamples'] if candidate and candidate['counterexamples'] else field['examples'][:1]
            lines.append(f"| `+0x{field['offset']:02X}` | {name} | {evidence} | {candidate['confidence'] if candidate else 'low'} | `{cell(json.dumps(examples))}` |")
        lines.append('')
        for s in layout['structures']:
            lines.append(f"- {s['candidate']}: {s['matches']}/{s['tests']}; {s['confidence']} confidence; counterexamples `{cell(json.dumps(s['counterexamples']))}`.")
        lines.append('')
    lines += ['## Hypothesis checks', '', '| Claim | Pass | Fail |', '| --- | ---: | ---: |']
    for r in report['hypotheses']['claims']:
        lines.append(f"| {cell(r['name'])} | {r['passes']} | {r['fails']} |")
    lines += ['', '## Coverage and limitations', '',
              f"Model metadata enrichment: {report['model_features']['enabled']}; {report['model_features']['models_readable']}/{report['model_features']['models_tested']} readable models.", '',
              json.dumps(report['limits'], sort_keys=True), '',
              f"Scan errors: {len(corpus['scan_errors'])}; analysis errors: {len(report['analysis_errors'])}; "
              f"model/material errors: {len(report['model_features']['errors'])}.", '']
    for r in corpus['scan_errors'] + report['analysis_errors'] + report['hypotheses']['source_errors']:
        lines.append('- ' + cell(json.dumps(r, sort_keys=True)))
    for name, description in report['confidence'].items():
        lines.append(f'- {name}: {description}')
    return '\n'.join(lines) + '\n'


def write(report, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    (out / 'report.md').write_text(markdown(report), encoding='utf-8')
