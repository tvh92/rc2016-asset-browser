"""Archive-wide census of native shader translation blockers.

Ranks why materialgraph pixel programs do not translate, weighted by how many
materials use each graph, so translator work targets the most common gaps.
Reads the archive only; the JSON report goes where --output says.

    python -m rc2016.shaders.census --source <game-root> --output <report.json> [--materials] [--jobs N]
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
import os
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


def _material_counts(archive, paths):
    from rc2016.viewer.native_glsl import prepare_material_translation
    statuses, diagnostics = Counter(), Counter()
    for path in paths:
        result = prepare_material_translation(archive, path)
        statuses[result.get('status')] += 1
        for reason in sorted({r for d in result.get('diagnostics', []) for r in diagnostic_reasons(d)}):
            diagnostics[reason] += 1
    return statuses, diagnostics


def _graph_rows(archive, entries):
    from rc2016.formats.archive import asset_data
    rows = {}
    for entry in entries:
        try:
            data = asset_data(archive, entry['id'])
        except (ValueError, KeyError, IndexError, OSError, struct.error) as exc:
            rows[entry['path']] = dict(status='unreadable', blockers=[normalise(exc)], programs=0)
            continue
        rows[entry['path']] = classify_graph(data)
    return rows


# Worker processes open their own archive once.
_WORKER_ARCHIVE = None


def _worker_open(source):
    global _WORKER_ARCHIVE
    from rc2016.levels.scene import open_archive
    _WORKER_ARCHIVE = open_archive(source)


def _worker_graphs(entries):
    return _graph_rows(_WORKER_ARCHIVE, entries)


def _worker_materials(paths):
    return _material_counts(_WORKER_ARCHIVE, paths)


def _chunks(items, count):
    """Contiguous chunks, so materials sharing a graph stay in one worker's cache."""
    size = max(1, -(-len(items) // count))
    return [items[i:i + size] for i in range(0, len(items), size)]


def _run(pool, function, worker, archive, items, chunks, progress, label):
    if pool is None:
        return [function(archive, items)]
    results = []
    parts = _chunks(items, chunks)
    for n, result in enumerate(pool.map(worker, parts), 1):
        results.append(result)
        if progress:
            progress(f'{label}: {n}/{len(parts)} chunks')
    return results


def material_translation_status(archive, by_material=None, pool=None, chunks=1, progress=None):
    """Bound per-material translation status and diagnostics (slower: binds every material).

    Materials are visited grouped by graph so the bounded compiled-graph cache is reused.
    """
    paths = sorted((e['path'] for e in archive.entries.values() if e.get('type') == 'material'),
                   key=lambda p: ((by_material or {}).get(p, []), p))
    statuses, diagnostics = Counter(), Counter()
    for s, d in _run(pool, _material_counts, _worker_materials, archive, paths, chunks, progress, 'materials'):
        statuses.update(s); diagnostics.update(d)
    return statuses, diagnostics


def census(archive, materials=False, progress=None, jobs=1, source=None):
    started = time.time()
    by_material = {}
    usage = material_graph_usage(archive, by_material)
    entries = sorted((e for e in archive.entries.values() if e.get('type') == 'materialgraph'), key=lambda e: e['path'])
    pool = ProcessPoolExecutor(jobs, initializer=_worker_open, initargs=(source,)) if jobs > 1 and source else None
    try:
        return _census(archive, materials, progress, pool, jobs * 4, started, by_material, usage, entries)
    finally:
        if pool is not None:
            pool.shutdown()


def _census(archive, materials, progress, pool, chunks, started, by_material, usage, entries):
    graphs = {}
    for rows in _run(pool, _graph_rows, _worker_graphs, archive, entries, chunks, progress, 'graphs'):
        graphs.update(rows)
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
        statuses, diagnostics = material_translation_status(archive, by_material, pool, chunks, progress)
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
    parser.add_argument('--jobs', type=int, default=min(8, os.cpu_count() or 1),
                        help='worker processes (default: CPU count, at most 8; 1 runs in this process)')
    args = parser.parse_args(argv)
    from rc2016.levels.scene import open_archive
    started = time.time()
    archive = open_archive(args.source)
    try:
        report = census(archive, args.materials, progress=lambda m: print(m, file=sys.stderr, flush=True),
                        jobs=args.jobs, source=args.source)
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
