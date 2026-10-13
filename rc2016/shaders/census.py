"""Archive-wide census of native shader translation blockers.

Ranks why materialgraph pixel programs do not translate, weighted by how many
materials use each graph, so translator work targets the most common gaps.
Reads the archive only; the JSON report goes where --output says.

    python -m rc2016.shaders.census --source <game-root> --output <report.json> [--materials]
"""
import argparse
from collections import Counter, defaultdict
import json
import re
import struct
import sys
import time

from rc2016.shaders.translator import compile_graph, lower_deferred

_OFFSETS = re.compile(r'\b(at|offset) -?\d+')
_REGISTERS = re.compile(r'\b(VGPR|SGPR) \d+')


def normalise(reason):
    """Group reasons that differ only by code offset or register number.

    Uninitialized registers keep their number: initial pixel-shader VGPRs are
    hardware inputs (barycentrics, position, front face), so the number says which.
    """
    reason = _OFFSETS.sub(r'\1 N', str(reason))
    return reason if reason.startswith('Uninitialized') else _REGISTERS.sub(r'\1 N', reason)


def _blocked_outputs(result):
    """{unresolved node: sorted output labels whose expressions reach it}."""
    from rc2016.shaders.ir import DAG
    ir = DAG(result['nodes']); outputs = result['outputs']
    roots = dict(albedo=outputs['albedo'], F0=outputs['F0'], gloss=[outputs['gloss']], emission=[outputs['emission']])
    if result['normal']:
        roots['normal'] = result['normal']['xyz']
    reach = {label: ir.reachable(r) for label, r in roots.items()}
    return {n['node']: sorted(label for label, nodes in reach.items() if n['node'] in nodes) for n in result['unresolved']}


def classify_graph(data):
    """Per-program status rows and the blocking reasons of one materialgraph."""
    try:
        graph = compile_graph(bytes(data))
    except (ValueError, IndexError, KeyError, struct.error) as exc:
        return dict(status='malformed', blockers=[normalise(exc)], programs=0)
    if not graph['programs']:
        return dict(status='no_pixel_program', blockers=[], programs=0)
    blockers, lowered = [], 0
    inputs = sorted({f"{p.get('ps_input_addr', 0):#06x}/{p.get('ps_input_ena', 0):#06x}" for p in graph['programs']})
    for program in graph['programs']:
        if program['status'] != 'compiled':
            blockers.append('compile: ' + normalise(program['reason']))
            continue
        try:
            result = lower_deferred(program['ir'])
        except (ValueError, IndexError, KeyError) as exc:
            blockers.append('lower: ' + normalise(exc))
            continue
        if result['unresolved']:
            where = _blocked_outputs(result)
            blockers.extend(sorted({'unresolved: ' + normalise(n.get('reason', n['op'])) +
                                    ' -> ' + ','.join(where[n['node']]) for n in result['unresolved']}))
        else:
            lowered += 1
    status = 'lowered' if lowered else 'blocked'
    return dict(status=status, blockers=sorted(set(blockers)), programs=len(graph['programs']), lowered_programs=lowered,
                ps_inputs=inputs)


def material_graph_usage(archive, by_material=None):
    """{graph path: material count} from every material's asset references."""
    from rc2016.formats.archive import asset_references
    usage = Counter()
    for key, entry in archive.entries.items():
        if entry.get('type') != 'material':
            continue
        try:
            refs = asset_references(archive, key)
        except (ValueError, KeyError, IndexError, OSError, struct.error):
            continue
        graphs = sorted({r['path'] for r in refs if r['path'].endswith('.materialgraph')})
        if by_material is not None:
            by_material[entry['path']] = graphs
        for path in graphs:
            usage[path] += 1
    return usage


_NODE_IDS = re.compile(r'\bnode\d+\b')


def diagnostic_reasons(diagnostic):
    """Grouping keys of one translation diagnostic; binding errors are JSON lists of node errors."""
    reason = diagnostic['reason'] if isinstance(diagnostic, dict) else diagnostic
    try:
        rows = json.loads(reason)
    except (TypeError, ValueError):
        rows = None
    if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
        return {'bind: ' + normalise(_NODE_IDS.sub('nodeN', str(r.get('reason')))) for r in rows}
    return {normalise(_NODE_IDS.sub('nodeN', str(reason)))}


def material_translation_status(archive, by_material=None):
    """Bound per-material translation status and diagnostics (slower: binds every material).

    Materials are visited grouped by graph so the bounded compiled-graph cache is reused.
    """
    from rc2016.viewer.native_glsl import prepare_material_translation
    statuses, diagnostics = Counter(), Counter()
    paths = sorted((e['path'] for e in archive.entries.values() if e.get('type') == 'material'),
                   key=lambda p: ((by_material or {}).get(p, []), p))
    for path in paths:
        result = prepare_material_translation(archive, path)
        statuses[result.get('status')] += 1
        for reason in sorted({r for d in result.get('diagnostics', []) for r in diagnostic_reasons(d)}):
            diagnostics[reason] += 1
    return statuses, diagnostics


def census(archive, materials=False, progress=None):
    started = time.time()
    by_material = {}
    usage = material_graph_usage(archive, by_material)
    graphs = {}
    entries = [e for e in archive.entries.values() if e.get('type') == 'materialgraph']
    from rc2016.formats.archive import asset_data
    for n, entry in enumerate(sorted(entries, key=lambda e: e['path'])):
        if progress and n % 200 == 0:
            progress(f'{n}/{len(entries)} graphs')
        try:
            data = asset_data(archive, entry['id'])
        except (ValueError, KeyError, IndexError, OSError, struct.error) as exc:
            graphs[entry['path']] = dict(status='unreadable', blockers=[normalise(exc)], programs=0)
            continue
        graphs[entry['path']] = classify_graph(data)
    by_status = Counter()
    weighted_status = Counter()
    blocker_graphs = defaultdict(list)
    for path, row in graphs.items():
        row['materials'] = usage.get(path, 0)
        by_status[row['status']] += 1
        weighted_status[row['status']] += row['materials']
        if row['status'] != 'lowered':
            for blocker in row['blockers']:
                blocker_graphs[blocker].append(path)
    ranked = sorted(blocker_graphs.items(), key=lambda kv: (-sum(usage.get(p, 0) for p in kv[1]), -len(kv[1]), kv[0]))
    ps_inputs = Counter(x for row in graphs.values() for x in row.get('ps_inputs', ()))
    report = dict(
        schema='rcps4-shader-census-v1', ps_input_addr_ena=dict(ps_inputs.most_common()),
        graphs=len(graphs), graph_status=dict(by_status), material_weighted_graph_status=dict(weighted_status),
        blockers=[dict(reason=reason, graphs=len(paths), materials=sum(usage.get(p, 0) for p in paths),
                       examples=sorted(paths, key=lambda p: -usage.get(p, 0))[:5]) for reason, paths in ranked],
        per_graph=graphs,
        notes=['A graph counts as lowered when at least one pixel program lowers without unresolved values; '
               'binding to a material can still fail (see material_status with --materials).',
               'Blocked graphs list every distinct blocker of every program, so one graph can appear under several reasons.'])
    report['seconds'] = dict(graphs=round(time.time() - started, 1))
    if materials:
        if progress:
            progress('binding every material')
        started = time.time()
        statuses, diagnostics = material_translation_status(archive, by_material)
        report['seconds']['materials'] = round(time.time() - started, 1)
        report['material_status'] = dict(statuses)
        report['material_diagnostics'] = [dict(reason=r, materials=c) for r, c in diagnostics.most_common()]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', required=True, help='game root holding the archive')
    parser.add_argument('--output', required=True, help='JSON report path (outside the repository)')
    parser.add_argument('--materials', action='store_true', help='also bind every material (slower)')
    parser.add_argument('--top', type=int, default=25, help='blockers to print')
    args = parser.parse_args(argv)
    from rc2016.levels.scene import open_archive
    started = time.time()
    archive = open_archive(args.source)
    try:
        report = census(archive, args.materials, progress=lambda m: print(m, file=sys.stderr))
    finally:
        archive.close()
    with open(args.output, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
    print(f"{report['graphs']} graphs in {time.time() - started:.0f} s {report['seconds']}: {report['graph_status']}")
    print(f"material-weighted: {report['material_weighted_graph_status']}")
    if 'material_status' in report:
        print(f"materials: {report['material_status']}")
        print('top material diagnostics (materials, reason):')
        for row in report['material_diagnostics'][:args.top]:
            print(f"{row['materials']:6d}  {row['reason'][:200]}")
    print('PS input address/enable words (graphs):', dict(list(report['ps_input_addr_ena'].items())[:8]))
    print('top blockers (materials, graphs, reason):')
    for row in report['blockers'][:args.top]:
        print(f"{row['materials']:6d} {row['graphs']:5d}  {row['reason']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
