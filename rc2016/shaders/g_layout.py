"""Which `_g` channels a materialgraph's lowered deferred writers read as F0 and gloss.

The generic reading takes `_g` as coloured F0 (rgb) plus gloss (alpha). A writer that instead reads one grey F0 from G
and the gloss from R makes that reading a colour cast. This decides it from the writer's own export expressions, not
from the image statistics, so it is exact for every program that lowers.

    python -m rc2016.shaders.g_layout --source <game-root> --output <report.json>
        lists the graphs of materials that no GRAPH_RULES entry covers and whose writers read a grey _g, with the
        materials whose native translation is ready (such a graph is not a fallback candidate)
"""
import argparse
import hashlib
import json
import struct
import sys

from rc2016.shaders import translator

GREY, COLOURED, CONSTANT, OTHER = 'grey', 'coloured', 'constant', 'other'
_FAILURES = (ValueError, IndexError, KeyError, struct.error)
_PS_INPUTS = ('barycentric0', 'barycentric1', 'fragment_x', 'fragment_y', 'view_depth', 'front_face_word')


def _channels(nodes, roots):
    """{(sampler API, channel)} of every texture sample the roots depend on."""
    seen, stack, found = set(), list(roots), set()
    while stack:
        index = stack.pop()
        if index in seen:
            continue
        seen.add(index)
        node = nodes[index]
        if node['op'] == 'texture':
            found.add((node['api'], node['channel']))
        stack.extend(node.get('args', []))
    return found


def _lower(program):
    compiler = translator.Compiler(program['code'], program['usage_slots'], program.get('ps_input_addr'), program.get('ps_input_ena'),
                                   program.get('vgpr_count'), allow_independent_samplers=True)
    for register, name in enumerate(_PS_INPUTS):
        extra = dict(kind='ps_input', input='FRONT_FACE') if name == 'front_face_word' else dict(kind='preview_builtin')
        compiler.v[register] = compiler.ir.node('input', name=name, **extra)
    return translator.lower_deferred(compiler.compile())


def writer_layouts(graph_data):
    """One row per deferred pixel program that lowers: code hash, layout and the F0 and gloss texture channels."""
    rows = []
    for program in translator.pixel_programs(bytes(graph_data)):
        try:
            lowered = _lower(program)
        except _FAILURES:
            continue
        nodes, outputs = lowered['nodes'], lowered['outputs']
        f0 = [_channels(nodes, [root]) for root in outputs['F0']]
        gloss = {channel for _, channel in _channels(nodes, [outputs['gloss']])}
        f0_channels = {channel for part in f0 for _, channel in part}
        if not any(f0):
            layout = CONSTANT
        elif len(set(outputs['F0'])) == 1:
            layout = GREY if f0_channels == {1} and gloss <= {0} else OTHER
        else:
            layout = COLOURED if f0_channels >= {0, 1, 2} else OTHER
        rows.append(dict(code_sha256=program['code_sha256'], layout=layout, f0_channels=sorted(f0_channels), gloss_channels=sorted(gloss)))
    return rows


def grey_graph(rows):
    """True when at least one program reads a grey _g and every other program that lowers reads no _g for F0."""
    layouts = {row['layout'] for row in rows}
    return GREY in layouts and layouts <= {GREY, CONSTANT}


def audit(archive, progress=None):
    """Grey-_g graphs of materials with no GRAPH_RULES entry, as {graph path: dict(sha256, materials, ready)}."""
    from rc2016.formats.archive import asset_data, asset_references
    from rc2016.viewer.native_glsl import prepare_material_translation
    from rc2016.viewer.textures import _graph_rule
    by_graph = {}
    for entry in sorted(archive.entries.values(), key=lambda e: e['path']):
        if entry.get('type') != 'material':
            continue
        refs = [r['path'] for r in asset_references(archive, entry['id']) if not r.get('resolution_error')]
        graphs = [g for g in refs if g.endswith('.materialgraph')]
        hashes = {}
        for graph in graphs:
            ids = [i for i in archive.resolve(graph) if archive.entries[i]['type'] == 'materialgraph']
            if len(ids) == 1:
                hashes[graph.replace('\\', '/').lower()] = hashlib.sha256(asset_data(archive, ids[0])).hexdigest()
        if not graphs or _graph_rule(graphs, hashes) or not any(r.endswith('_g.texture') for r in refs):
            continue
        key = graphs[0].replace('\\', '/').lower()
        by_graph.setdefault(key, dict(sha256=hashes.get(key), materials=[]))['materials'].append(entry['path'])
    found = {}
    for number, (graph, row) in enumerate(sorted(by_graph.items())):
        if progress:
            progress(f'{number + 1}/{len(by_graph)} {graph}')
        ids = [i for i in archive.resolve(graph) if archive.entries[i]['type'] == 'materialgraph']
        if len(ids) != 1 or not row['sha256']:
            continue
        try:
            rows = writer_layouts(asset_data(archive, ids[0]))
        except _FAILURES:
            continue
        if grey_graph(rows):
            row['ready'] = [m for m in row['materials'] if prepare_material_translation(archive, m).get('status') == 'ready']
            row['programs'] = rows
            found[graph] = row
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', required=True, help='game root holding the archive')
    parser.add_argument('--output', required=True, help='JSON report path (outside the repository)')
    args = parser.parse_args(argv)
    from rc2016.levels.scene import open_archive
    archive = open_archive(args.source)
    try:
        found = audit(archive, progress=lambda m: print(m, file=sys.stderr, flush=True))
    finally:
        archive.close()
    with open(args.output, 'w', encoding='utf-8') as handle:
        json.dump(found, handle, indent=1, sort_keys=True)
    usable = [g for g, row in found.items() if not row['ready']]
    print(f'{len(found)} grey-_g graphs without a rule, {len(usable)} with no native-ready material')
    for graph in usable:
        print(f"{len(found[graph]['materials']):4d}  {found[graph]['sha256']}  {graph}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
