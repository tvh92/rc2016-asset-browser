"""Character coverage audit: animation sources and material decode status per model (read-only).

python -m rc2016.research.npc_audit <game or asset_archive root> --out <folder> [--prefix character/npc/]

For every model under the prefix it reports the animsets the game pairs with it, the named clips of
those animsets (with idle candidates), the real-time cutscene shots of the model
(decoded, with a calmness score), other models with the same rest rig and the animsets the game pairs
with them, and, per material, the Blender front-end the pipeline would build plus a decode category.
Archive bytes are read in memory; nothing is written outside ``--out``.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from rc2016.common import dump
from rc2016.formats.animation_samples import decode_clip
from rc2016.common import sha
from rc2016.formats.animation_sources import cinematic_sources, clip_directory, rig_comparison
from rc2016.formats.animation_transforms import model_skeleton, sample_transforms
from rc2016.formats.archive import Archive, locate
from rc2016.formats.clips import classify, worlds_batch
from rc2016.formats.index import AssetIndex, references

IDLE = re.compile(r'idle', re.I)
# Idle clips that are a variation, a layer or a special state rather than the plain standing loop.
IDLE_VARIANT = re.compile(r'fidget|aim|hover|hit|react|turn|head|look|cower|lose|win|boat|bent|nervous|gesture|'
                          r'performance|talk|equip|_to_|^to_|come|closed|twitch|cover|board', re.I)
# Decode categories, from best to worst evidence.
CATEGORIES = ('native_translation', 'exact_viewer_rule', 'profile_recipe', 'prefix_viewer_rule', 'partial_family',
              'generic_default_reading', 'forward_partial', 'forward_approximation', 'colour_only',
              'missing_base_colour', 'unset', 'failed')


class MemoryIndex(AssetIndex):
    """AssetIndex reading decompressed bytes in memory instead of writing the decode cache."""

    def scan(self, key):
        if key in self.graph:
            return self.graph[key]
        try:
            refs = references(self.archive.asset_data(key))
            for ref in refs:
                ref['resolved_ids'] = [] if ref.get('resolution_error') else self.archive.resolve(ref['path'])
            self.graph[key] = refs
        except Exception as exc:
            self.errors.append(dict(asset_id=key, path=self.archive.entries[key]['path'], error=str(exc)))
            self.graph[key] = []
        return self.graph[key]

    def actor_index(self):
        for entry in self.archive.entries.values():
            if entry['type'] == 'actor':
                self.scan(entry['id'])


def suggested_idle(names):
    """The plain idle loop among clip names: 'idle' without a variant word, shortest name first."""
    plain = sorted((n for n in names if n and IDLE.search(n) and not IDLE_VARIANT.search(n)), key=len)
    return plain[0] if plain else None


def motion_score(rows, frames, inheritance):
    """Mean per-frame world displacement of every model joint, root included (source units per frame).

    A ranking between shots of one rig (lower is calmer), not a decoded property.
    """
    if len(frames) < 2:
        return 0.0
    positions = worlds_batch(rows, frames, inheritance)[:, :, :3, 3]
    return float(np.linalg.norm(np.diff(positions, axis=0), axis=2).mean())


def shot_rows(archive, source, rows, inheritance):
    data = archive.asset_data(source['asset_id'])
    names = {int(r['hash'], 16): r['name'] for r in rows}
    shots = []
    for clip in source['clips']:
        row = dict(name=clip['name'], frames=clip['frames'], fps=clip['fps'], cutscene=source['path'])
        try:
            frames = sample_transforms(decode_clip(data, clip['offset'], names))
            kind, evidence = classify(rows, frames)
            root = worlds_batch(rows, frames[:1], inheritance)[0, 0, :3, 3]
            row.update(status='decoded', kind=kind, translation_rest_median_ratio=evidence.get('translation_rest_median_ratio'),
                       motion_score=motion_score(rows, frames, inheritance),
                       root_offset_first_frame=float(np.linalg.norm(root - np.asarray(rows[0]['translation']))))
        except (ValueError, KeyError, IndexError, OverflowError) as exc:
            row.update(status='not_decoded', reason=str(exc))
        shots.append(row)
    decoded = [s for s in shots if s['status'] == 'decoded' and s['kind'] in ('absolute skeletal', 'partial absolute skeletal')]
    calmest = min(decoded, key=lambda s: s['motion_score'], default=None)
    return shots, calmest['name'] if calmest else None


def material_row(archive, path):
    """One material: graphs, rule prefix, families, translation, blend state, front-end and decode category."""
    from rc2016.formats import materials as M
    from rc2016.shaders.bindings import prepare_translation
    from rc2016.shaders.families import preflight_graph
    from rc2016.viewer import textures as T
    row = dict(path=path)
    try:
        ids = [k for k in archive.resolve(path) if archive.entries[k]['type'] == 'material']
        if len(ids) != 1:
            raise ValueError('material does not resolve to one asset')
        data = archive.asset_data(ids[0])
        instance = M.extract_material_parameters(data)
        found = references(data)
        graphs = list(dict.fromkeys(r['path'] for r in found if r['path'].endswith('.materialgraph')))
        texture_refs, textures, graph_datas, preflights = {}, [], [], []
        for ref in found:
            if ref['path'].endswith('.texture'):
                texture_refs.setdefault(ref['path'], []).append(
                    M._texture_reference(ref['path'], 'material_direct', path, ref.get('byte_offset')))
                textures.append(ref['path'])
        for graph in graphs:
            gid = [k for k in archive.resolve(graph) if archive.entries[k]['type'] == 'materialgraph'][0]
            gdata = archive.asset_data(gid)
            graph_datas.append(gdata)
            preflights.append(preflight_graph(graph, gdata, instance))
            for ref in references(gdata):
                if ref['path'].endswith('.texture'):
                    textures.append(ref['path'])
                    texture_refs.setdefault(ref['path'], []).append(
                        M._texture_reference(ref['path'], 'materialgraph_reference', graph, ref.get('byte_offset')))
        textures = list(dict.fromkeys(textures))
        textures, _unbound, roles = M.resolve_texture_slots(data, graph_datas, preflights, textures, texture_refs)
        bindings = [dict(path=t, role=roles.get(t, {}).get('role'), source_references=texture_refs.get(t, []))
                    for t in textures if archive.resolve(t)]
        profile = M.validated_profile(path, sha(data))
        status = M.shader_build_status(profile, M.graph_semantics(graphs), bindings, preflights)
        translation = prepare_translation(graph_datas, data, instance,
            source_graph_paths=graphs,source_material_path=path)
        rule = M.viewer_graph_rule(archive, path, {b['path'] for b in bindings})
        status = M.final_shader_status(status, rule, {b['path'] for b in bindings}, translation, profile, preflights, graphs)
        prefix = next((p for p, _r in T.GRAPH_RULES for g in graphs if g.startswith(p)), None)
        flags = T.material_flags(data)
        blend = T.blend_state(flags)['blend']
        diagnostics = [d.get('reason', d) if isinstance(d, dict) else d for d in translation.get('diagnostics') or ()]
        row.update(graphs=graphs, rule_prefix=prefix, families=[p.get('family') for p in preflights],
                   translation=translation.get('status'), translation_blockers=[str(d)[:200] for d in diagnostics[:4]],
                   blend=blend, fur_shell=bool(flags and flags & T.FUR_SHELL_FLAG),
                   viewer_rule=dict(applies=rule.get('applies'), unported=rule.get('unported') or [],
                                    reason=rule.get('reason')),
                   shader_status=status, category=category(status, prefix))
    except Exception as exc:
        row.update(shader_status='failed', category='failed', reason=str(exc))
    return row


def category(status, prefix):
    if status == 'native_translated_deferred_frontend':
        return 'native_translation'
    if status == 'viewer_graph_rule_frontend':
        if prefix is None:
            return 'generic_default_reading'
        return 'exact_viewer_rule' if prefix.endswith('.') else 'prefix_viewer_rule'
    if status == 'validated_hash_bound_recipe':
        return 'profile_recipe'
    if status.startswith('partial_skin') or status.startswith('partial_family'):
        return 'missing_base_colour' if status.endswith('missing_base_color') else 'partial_family'
    if status == 'partial_native_character_glass':
        return 'forward_partial'
    if status == 'thin_glass_approximation':
        return 'forward_approximation'
    if status.endswith('missing_base_color'):
        return 'missing_base_colour'
    if status.startswith(('color_only', 'ambiguous')):
        return 'colour_only'
    if status == 'unset_source_material':
        return 'unset'
    return 'failed'


def audit(archive, prefix, log=print):
    index = MemoryIndex(archive)
    index.actor_index()
    entries = archive.entries
    rigs = {}
    for entry in entries.values():
        if entry['type'] == 'model' and entry['path'].startswith('character/'):
            try:
                rigs[entry['id']] = model_skeleton(archive.asset_data(entry['id']))
            except Exception:
                pass
    targets = sorted((e for e in entries.values() if e['type'] == 'model' and e['path'].startswith(prefix)),
                     key=lambda e: e['path'])
    models, materials = [], {}
    for number, entry in enumerate(targets, 1):
        log(f'[{number}/{len(targets)}] {entry["path"]}')
        key, rows = entry['id'], rigs.get(entry['id'])
        data = archive.asset_data(key)
        row = dict(path=entry['path'], asset_id=key, joints=len(rows or ()))
        related = index.related(key)
        row['actors'] = [entries[a]['path'] for a in related['actors']]
        animsets = []
        for animset in related['animsets']:
            clips = clip_directory(archive.asset_data(animset))
            joints = {r['hash'] for r in rows or ()}
            named = [dict(name=c['name'], frames=c['frames'],
                          coverage=round(len(joints & set(c['joint_hashes'])) / len(joints), 3) if joints else None)
                     for c in clips]
            animsets.append(dict(path=entries[animset]['path'], clips=len(clips), named=named,
                                 idle_candidates=[c['name'] for c in named if c['name'] and IDLE.search(c['name'])]))
        row['animsets'] = animsets
        row['suggested_idle'] = suggested_idle([c['name'] for a in animsets for c in a['named']])
        if rows:
            inheritance = 'full'
            sources, errors = cinematic_sources(archive, rows, entry['path'])
            shots = []
            for source in sources:
                shots.extend(shot_rows(archive, source, rows, inheritance)[0])
            decoded = [s for s in shots if s['status'] == 'decoded' and s['kind'] in ('absolute skeletal', 'partial absolute skeletal')]
            calmest = min(decoded, key=lambda s: s['motion_score'], default=None)
            row['cutscene_shots'] = shots
            row['calmest_cutscene_shot'] = calmest['name'] if calmest else None
            row['cutscene_errors'] = errors
            same = []
            for other, other_rows in rigs.items():
                if other == key:
                    continue
                comparison = rig_comparison(rows, other_rows)
                if comparison['same_rig'] and comparison['shared_joints'] >= 0.9 * len(rows):
                    paired = sorted({entries[a]['path'] for k, refs in index.graph.items() if entries[k]['type'] == 'actor'
                                     and other in {i for r in refs for i in r.get('resolved_ids') or ()}
                                     for r in refs for a in r.get('resolved_ids') or () if entries[a]['type'] == 'animset'})
                    same.append(dict(model=entries[other]['path'], shared_joints=comparison['shared_joints'],
                                     absent_from_other=comparison['absent_from_anchor'],
                                     max_difference=comparison['max_difference'],
                                     borrowable_animsets=[p for p in paired
                                                          if p not in {a['path'] for a in animsets}]))
            row['same_rig_models'] = same
        row['materials'] = list(dict.fromkeys(r['path'] for r in references(data) if r['path'].endswith('.material')))
        for path in row['materials']:
            if path not in materials:
                materials[path] = material_row(archive, path)
        models.append(row)
    return dict(schema='rc2016-npc-audit-v1', prefix=prefix, models=models,
                materials=sorted(materials.values(), key=lambda m: (CATEGORIES.index(m['category']), m['path'])),
                material_categories=dict(Counter(m['category'] for m in materials.values())),
                index_errors=index.errors)


def markdown(result):
    lines = [f"# Character audit: `{result['prefix']}`", '']
    lines += ['## Animation', '', '| Model | Joints | Animsets (clips) | Suggested idle | Cutscene shots | Calmest shot | Same-rig borrowable |',
              '|---|---|---|---|---|---|---|']
    for m in result['models']:
        sets = ', '.join(f"{Path(a['path']).stem} ({a['clips']})" for a in m['animsets']) or '-'
        shots = m.get('cutscene_shots') or []
        decoded = sum(1 for s in shots if s['status'] == 'decoded')
        borrow = sorted({p for s in m.get('same_rig_models') or () for p in s['borrowable_animsets']})
        lines.append(f"| `{Path(m['path']).stem}` | {m['joints']} | {sets} | {m['suggested_idle'] or '-'} | "
                     f"{decoded}/{len(shots)} | {m.get('calmest_cutscene_shot') or '-'} | "
                     f"{', '.join(Path(p).stem for p in borrow) or '-'} |")
    lines += ['', '## Materials', '', 'Counts: ' + ', '.join(f'{k} {v}' for k, v in result['material_categories'].items()), '',
              '| Category | Material | Graphs | Blend | Status | Used by |', '|---|---|---|---|---|---|']
    users = {}
    for m in result['models']:
        for path in m['materials']:
            users.setdefault(path, []).append(Path(m['path']).stem)
    for mat in result['materials']:
        graphs = ', '.join(Path(g).stem for g in mat.get('graphs') or ()) or '-'
        lines.append(f"| {mat['category']} | `{Path(mat['path']).stem}` | {graphs} | {mat.get('blend') or '-'} | "
                     f"{mat['shader_status']} | {', '.join(users.get(mat['path'], []))} |")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('root', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--prefix', default='character/npc/')
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    out, root = args.out.resolve(), args.root.resolve()
    if out.is_relative_to(repo) or out.is_relative_to(root):
        parser.error('--out must be outside the repository and source root')
    out.mkdir(parents=True, exist_ok=True)
    archive = Archive(locate(root), out / 'archive_scratch', lambda *a, **k: None)
    archive.verify_repeats = False
    result = audit(archive, args.prefix, log=lambda text: print(text, file=sys.stderr, flush=True))
    dump(out / 'audit.json', result)
    (out / 'audit.md').write_text(markdown(result), encoding='utf-8')
    print(f"Audit: {out / 'audit.md'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
