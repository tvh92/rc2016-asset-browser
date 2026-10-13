"""Archive-wide census of native shader translation blockers.

Ranks why materialgraph pixel programs do not translate, weighted by how many
materials use each graph, so translator work targets the most common gaps.
Reads the archive only; the JSON report goes where --output says.

    python -m rc2016.shaders.census --source <game-root> --output <report.json> [--materials] [--jobs N]
        [--show REGEX]   (per-material blockers and interpolant sources for matching material paths)
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
    from rc2016.shaders.interpolants import shader_headers
    try:
        layouts = sorted(f"{h.get('layout')}/{h.get('export_entry_bytes')}" for h in shader_headers(bytes(data)) if h['type'] != 2)
    except (ValueError, IndexError, struct.error) as exc:
        layouts = ['error: ' + normalise(exc)]
    if not graph['programs']:
        return dict(status='no_pixel_program', blockers=[], programs=0, vertex_layouts=layouts)
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
                ps_inputs=inputs, vertex_layouts=layouts)


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


_SUMMARY = 'No portable deferred writer with a bounded source pass contract'


_ATTRIBUTE = re.compile(r'input attribute(\d+)\.')


def engine_terms(data, limit=6):
    """Smallest expressions that consume an engine CB0 word or pixel position, per compiled program."""
    from rc2016.shaders.bindings import compiled
    from rc2016.shaders.ir import DAG
    found = []
    for program in compiled(bytes(data))['programs']:
        if program['status'] != 'compiled':
            continue
        ir = DAG(program['ir']['nodes'])
        engine = {i for i, n in enumerate(ir.nodes) if n['op'] == 'input' and
                  (n.get('kind') == 'ps_input' or (n.get('kind') == 'cb' and n.get('api') == 0))}
        for i, n in enumerate(ir.nodes):
            if n['op'] in ('add', 'mul', 'sub', 'u_and') and engine & set(n.get('args', [])):
                text = ir.describe(i, 3)
                if text not in found:
                    found.append(text)
            if len(found) >= limit:
                return found
    return found


def world_position(data):
    """Best reconstruction analysis over the graph's lowered pixel programs (fewest residual engine inputs)."""
    from rc2016.shaders.bindings import compiled, _lowered
    from rc2016.shaders.engine_inputs import analyse
    best = None
    for program in compiled(bytes(data))['programs']:
        if program['status'] != 'compiled':
            continue
        try:
            lowered = _lowered(program)
        except (ValueError, IndexError, KeyError):
            continue
        o = lowered['outputs']
        roots = [*o['albedo'], *o['F0'], o['gloss'], o['emission']] + (lowered['normal']['xyz'] if lowered['normal'] else [])
        result = analyse(lowered['nodes'], roots)
        if best is None or len(result['residual']) < len(best['residual']):
            best = result
    return best


def material_detail(archive, path, result, reasons):
    """Blockers of one material plus where each blocking interpolant comes from."""
    from rc2016.formats.archive import asset_data, asset_references
    from rc2016.shaders.interpolants import interpolant_sources
    detail = dict(material=path, status=result.get('status'), reasons=reasons, graphs=[], interpolants={})
    try:
        ids = [i for i in archive.resolve(path) if archive.entries[i]['type'] == 'material']
        graphs = sorted({r['path'] for r in asset_references(archive, ids[0]) if r['path'].endswith('.materialgraph')})
        detail['graphs'] = graphs
        attributes = {int(m.group(1)) for r in reasons for m in _ATTRIBUTE.finditer(r)}
        if any('CB0[' in r or 'ps.POS' in r for r in reasons) and graphs:
            gid = [i for i in archive.resolve(graphs[0]) if archive.entries[i]['type'] == 'materialgraph'][0]
            data = asset_data(archive, gid)
            detail['engine_terms'] = engine_terms(data)
            detail['world_position'] = world_position(data)
        for graph in graphs[:1] if attributes else []:
            gid = [i for i in archive.resolve(graph) if archive.entries[i]['type'] == 'materialgraph'][0]
            detail['interpolants'] = interpolant_sources(asset_data(archive, gid), attributes)
    except (ValueError, KeyError, IndexError, OSError, struct.error) as exc:
        detail['error'] = f'{type(exc).__name__}: {exc}'
    return detail


def world_position_outcome(archive, path):
    """Experimental world-position adapter on a material that is not ready: 'experimental' or what still blocks it."""
    from rc2016.viewer.experimental_materials import world_position_translation
    try:
        result = world_position_translation(archive, path)
    except (ValueError, KeyError, IndexError, OSError, struct.error) as exc:
        return normalise(f'{type(exc).__name__}: {exc}')
    if result.get('status') == 'experimental':
        return 'experimental'
    # Programs that do not compile fail the same way with or without the adapter.
    reasons = sorted({r for d in result.get('diagnostics', []) for r in diagnostic_reasons(d)
                      if not r.startswith('compile: ')} - {_SUMMARY})
    return ' + '.join(reasons) or _SUMMARY


def _material_counts(archive, paths, show=None):
    from rc2016.viewer.native_glsl import prepare_material_translation
    statuses, diagnostics, combinations, details, adapted = Counter(), Counter(), Counter(), [], Counter()
    show = re.compile(show, re.I) if show else None
    for path in paths:
        result = prepare_material_translation(archive, path)
        statuses[result.get('status')] += 1
        reasons = sorted({r for d in result.get('diagnostics', []) for r in diagnostic_reasons(d)})
        for reason in reasons:
            diagnostics[reason] += 1
        if result.get('status') != 'ready':
            # The full set of blockers: binding one input unlocks a material only if it is the whole set.
            combinations[' + '.join(r for r in reasons if r != _SUMMARY) or _SUMMARY] += 1
            if any('CB0[' in r or 'ps.POS' in r for r in reasons):
                adapted[world_position_outcome(archive, path)] += 1
        if show and show.search(path):
            detail = material_detail(archive, path, result, [r for r in reasons if r != _SUMMARY])
            if result.get('status') != 'ready' and any('CB0[' in r or 'ps.POS' in r for r in reasons):
                detail['world_position_preview'] = world_position_outcome(archive, path)
            details.append(detail)
    return statuses, diagnostics, combinations, details, adapted


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
_WORKER_SHOW = None


def _worker_open(source, show=None):
    global _WORKER_ARCHIVE, _WORKER_SHOW
    _WORKER_SHOW = show
    from rc2016.levels.scene import open_archive
    _WORKER_ARCHIVE = open_archive(source)


def _worker_graphs(entries):
    return _graph_rows(_WORKER_ARCHIVE, entries)


def _worker_materials(paths):
    return _material_counts(_WORKER_ARCHIVE, paths, _WORKER_SHOW)


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


def material_translation_status(archive, by_material=None, pool=None, chunks=1, progress=None, show=None):
    """Bound per-material translation status and diagnostics (slower: binds every material).

    Materials are visited grouped by graph so the bounded compiled-graph cache is reused.
    """
    paths = sorted((e['path'] for e in archive.entries.values() if e.get('type') == 'material'),
                   key=lambda p: ((by_material or {}).get(p, []), p))
    statuses, diagnostics, combinations, details, adapted = Counter(), Counter(), Counter(), [], Counter()
    function = lambda a, items: _material_counts(a, items, show)
    for s, d, c, x, w in _run(pool, function, _worker_materials, archive, paths, chunks, progress, 'materials'):
        statuses.update(s); diagnostics.update(d); combinations.update(c); details.extend(x); adapted.update(w)
    return statuses, diagnostics, combinations, sorted(details, key=lambda x: x['material']), adapted


def census(archive, materials=False, progress=None, jobs=1, source=None, show=None):
    started = time.time()
    by_material = {}
    usage = material_graph_usage(archive, by_material)
    entries = sorted((e for e in archive.entries.values() if e.get('type') == 'materialgraph'), key=lambda e: e['path'])
    pool = ProcessPoolExecutor(jobs, initializer=_worker_open, initargs=(source, show)) if jobs > 1 and source else None
    try:
        return _census(archive, materials or bool(show), progress, pool, jobs * 4, started, by_material, usage, entries, show)
    finally:
        if pool is not None:
            pool.shutdown()


def _census(archive, materials, progress, pool, chunks, started, by_material, usage, entries, show=None):
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
    vertex_layouts = Counter(x for row in graphs.values() for x in row.get('vertex_layouts', ()))
    report = dict(
        schema='rcps4-shader-census-v1', ps_input_addr_ena=dict(ps_inputs.most_common()),
        vertex_header_layouts=dict(vertex_layouts.most_common()),
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
        statuses, diagnostics, combinations, details, adapted = material_translation_status(archive, by_material, pool, chunks, progress, show)
        report['world_position_preview'] = dict(adapted.most_common())
        if show:
            report['shown_materials'] = details
        report['material_blocker_sets'] = [dict(reasons=r, materials=c) for r, c in combinations.most_common()]
        report['seconds']['materials'] = round(time.time() - started, 1)
        report['material_status'] = dict(statuses)
        report['material_diagnostics'] = [dict(reason=r, materials=c) for r, c in diagnostics.most_common()]
    return report


def _print_shown(details):
    """Shown materials grouped by identical blockers; interpolant sources listed once per group."""
    ready = sum(d['status'] == 'ready' for d in details)
    print(f'shown materials: {len(details)} ({ready} ready)')
    shapes = Counter((json.dumps(d['world_position']['matches'], sort_keys=True), ' '.join(d['world_position']['residual']))
                     for d in details if d['status'] != 'ready' and d.get('world_position'))
    if shapes:
        print('world-position reconstruction test (materials: matched shapes | engine inputs left):')
        for (matched, residual), count in shapes.most_common(12):
            print(f'  {count:4d}  {matched[:300]} | {residual[:300] or "none"}')
    left = Counter(t for d in details if d['status'] != 'ready' and d.get('world_position_preview', 'experimental') != 'experimental'
                   for t in (d.get('world_position') or {}).get('adapter_residual_terms', []))
    if left:
        print('engine expressions left after the world-position adapter (materials):')
        for term, count in left.most_common(12):
            print(f'  {count:4d}  {term[:400]}')
    terms = Counter(t for d in details if d['status'] != 'ready' for t in d.get('engine_terms', []))
    if terms:
        print('expressions consuming engine CB0 words or pixel position (materials):')
        for term, count in terms.most_common(12):
            print(f'  {count:4d}  {term[:400]}')
    groups = defaultdict(list)
    for d in details:
        if d['status'] != 'ready':
            key = (' + '.join(d['reasons']) or '(no reason)', d.get('world_position_preview', ''),
                   json.dumps(d.get('interpolants', {}), sort_keys=True))
            groups[key].append(d['material'])
    previews = sorted(d['material'] for d in details if d.get('world_position_preview') == 'experimental')
    if previews:
        print(f'shown materials with an experimental world-position preview: {len(previews)}')
        for path in previews[:40]:
            print('  ' + path)
    for (reasons, preview, interpolants), paths in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f'\n{len(paths)} materials, e.g. {paths[0]}')
        print('  blockers:', reasons[:400])
        if preview:
            print('  world-position preview:', preview[:400])
        for name, entry in json.loads(interpolants).items():
            print(f"  {name} (PS semantic {entry['semantics']}):")
            for source in entry['sources'][:2]:
                print('    ' + source[:900])


def print_receipt(archive, path):
    """One material's native and experimental world-position receipts, summarised."""
    from rc2016.shaders.ir import DAG
    from rc2016.viewer.experimental_materials import world_position_translation, preview_with_fallback
    from rc2016.viewer.textures import material_texture_paths
    from rc2016.viewer.native_glsl import prepare_material_translation
    for label, result in (('native', prepare_material_translation(archive, path)),
                          ('world-position preview', preview_with_fallback(
                              world_position_translation(archive, path), material_texture_paths(archive, path)))):
        print(f"== {label}: {result.get('status')}")
        if result.get('status') not in ('ready', 'experimental'):
            for d in result.get('diagnostics', [])[:8]:
                print('  diagnostic:', str(d.get('reason', d) if isinstance(d, dict) else d)[:300])
            continue
        print('  shader:', result.get('shader_id'), 'pass contract:', result.get('pass_contract_index'),
              'candidates:', len(result.get('candidate_sources', [])))
        for b in result['texture_bindings']:
            print(f"  texture API{b['api']}: {b['path']} ({b['source']})")
        print('  parameters:', json.dumps(result.get('parameters'), sort_keys=True)[:600])
        ir = DAG(result['nodes'])
        for key, value in result['outputs'].items():
            for j, node in enumerate(value if isinstance(value, list) else [value]):
                print(f'  {key}[{j}] = {ir.describe(node, 6)[:500]}')
        for limitation in result.get('limitations', [])[:12]:
            print('  limitation:', limitation[:300])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', required=True, help='game root holding the archive')
    parser.add_argument('--output', help='JSON report path (outside the repository); required unless --receipt')
    parser.add_argument('--materials', action='store_true', help='also bind every material (slower)')
    parser.add_argument('--top', type=int, default=25, help='blockers to print')
    parser.add_argument('--receipt', help='print one material path\'s native and world-position preview receipts and stop')
    parser.add_argument('--show', help='regular expression of material paths to report in detail (implies --materials)')
    parser.add_argument('--jobs', type=int, default=min(16, os.cpu_count() or 1),
                        help='worker processes (default: CPU count, at most 16; 1 runs in this process)')
    args = parser.parse_args(argv)
    if not args.receipt and not args.output:
        parser.error('--output is required')
    from rc2016.levels.scene import open_archive
    started = time.time()
    archive = open_archive(args.source)
    if args.receipt:
        try:
            print_receipt(archive, args.receipt)
        finally:
            archive.close()
        return 0
    try:
        report = census(archive, args.materials, progress=lambda m: print(m, file=sys.stderr, flush=True),
                        jobs=args.jobs, source=args.source, show=args.show)
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
        print('top complete blocker sets per unsupported material (materials, reasons):')
        for row in report['material_blocker_sets'][:15]:
            print(f"{row['materials']:6d}  {row['reasons'][:300]}")
        print('experimental world-position preview on unsupported materials with engine inputs (materials, outcome):')
        for outcome, count in list(report['world_position_preview'].items())[:10]:
            print(f"{count:6d}  {outcome[:300]}")
    print('PS input address/enable words (graphs):', dict(list(report['ps_input_addr_ena'].items())[:8]))
    print('vertex header layouts (programs):', report['vertex_header_layouts'])
    if 'shown_materials' in report:
        _print_shown(report['shown_materials'])
    print('top blockers (materials, graphs, reason):')
    for row in report['blockers'][:args.top]:
        print(f"{row['materials']:6d} {row['graphs']:5d}  {row['reason']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
