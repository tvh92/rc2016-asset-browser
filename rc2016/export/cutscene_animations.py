"""Export every real-time cutscene performance of a character as a portable animation library.

python -m rc2016.export.cutscene_animations <game or asset_archive root> --model <archive path or stem>
        [--model ...] --out <folder> [--no-textures] [--overwrite]

Characters without an idle animset (the Novalis mayor, Darla Gratch, Shiv) only move in the real-time
cutscene (``cinematic2``) assets. This command finds every cutscene clip whose joints cover the whole model
(``animation_sources.cinematic_sources``; clips are named only by an exact game CRC32 match against a string
of the same file) and writes, per model, ``<out>/<model stem>/``::

    manifest.json              every matching clip, decoded or not, with its source and decode status
    <stem>_cutscenes.glb       the skinned model and one glTF animation per decoded clip (rest pose + clips)
    export.json                the portable exporter's own record (materials, notes, skipped clips)

The GLB comes from ``rc2016.export.models`` (same geometry, skeleton, materials and native Y-up basis as the
portable model export) with the cutscene shots as the only animation source. The clips are cutscene
performances in the cutscene's own scene space, not authored idle loops; decoding covers clip versions 15 and
21 only. Archive bytes are read in memory; nothing is written outside ``--out``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

from rc2016.formats.animation_sources import CINEMATIC_LIMITATION, cinematic_sources, shot_label
from rc2016.formats.animation_transforms import model_skeleton

SCHEMA = 'rc2016-cutscene-animations-v1'
NOTE = ('These are real-time cutscene performances, not idle loops: absolute source samples in the cutscene '
        'scene space (the root may be away from the origin), one shot at a time. Clips are named only when their '
        'name hash equals the game CRC32 of a string in the same cutscene file; only clip versions 15 and 21 decode.')
STATUS_DECODED, STATUS_NOT_DECODED, STATUS_NOT_PLAYABLE, STATUS_UNNAMED = (
    'decoded', 'not_decoded', 'not_playable', 'unnamed_not_exported')


def glb_summary(path):
    """(animation count, skin count, mesh count, joint count) read from a GLB's JSON chunk."""
    with open(path, 'rb') as stream:
        magic, _version, _length = struct.unpack('<III', stream.read(12))
        if magic != 0x46546C67:
            raise ValueError('Not a GLB file: ' + str(path))
        size, kind = struct.unpack('<II', stream.read(8))
        if kind != 0x4E4F534A:
            raise ValueError('GLB has no leading JSON chunk: ' + str(path))
        doc = json.loads(stream.read(size).decode('utf-8'))
    skins = doc.get('skins', [])
    return dict(animations=len(doc.get('animations', [])), skins=len(skins), meshes=len(doc.get('meshes', [])),
                joints=len(skins[0]['joints']) if skins else 0,
                animation_names=[a.get('name') for a in doc.get('animations', [])])


def build_manifest(model_path, model_id, joint_count, sources, errors, result, digests=None, glb=None):
    """The manifest dict for one model.

    ``sources`` is ``cinematic_sources(..., include_unnamed=True)``; ``result`` the ``ModelExporter.export``
    summary (``clips`` exported, ``skipped_clips`` with reasons). Every clip of every source gets a row in
    source order: decoded (exported as a glTF animation), not decoded or not playable (with the reason), or
    unnamed (its name hash matches no string of its file; never exported, never guessed).
    """
    exported = {(c['asset'], c['offset']): c for c in result.get('clips', ())}
    skipped = {(c['asset'], c['offset']): c for c in result.get('skipped_clips', ())}
    digests = digests or {}
    rows = []
    for source in sources:
        # Manifest order: by shot number within each cutscene (the GLB keeps directory order; rows name their animation).
        for clip in sorted(source['clips'], key=lambda c: (shot_label(c['name'], source['path'])[1] is None,
                                                           shot_label(c['name'], source['path'])[1] or 0, c['offset'])):
            cutscene, shot = shot_label(clip['name'], source['path'])
            row = dict(name=clip['name'], name_hash=clip['name_hash'], name_source=clip['name_source'],
                       source_asset=source['path'], source_asset_id=source['asset_id'],
                       source_asset_sha256=digests.get(source['asset_id']),
                       cutscene=cutscene, shot=shot, clip_offset=clip['offset'],
                       format_version=clip['format_version'], frames=clip['frames'], fps=clip['fps'],
                       duration_seconds=clip['duration_seconds'], clip_joints=len(clip['joint_hashes']),
                       model_joints=joint_count)
            key = (source['path'], clip['offset'])
            if not clip['name']:
                row.update(decode_status=STATUS_UNNAMED, reason='the clip name hash matches no string of its cutscene file')
            elif key in exported:
                done = exported[key]
                row.update(decode_status=STATUS_DECODED, kind=done['kind'], frames=done['frames'], fps=done['fps'],
                           gltf_animation=done['name'])
            elif key in skipped:
                row.update(decode_status=STATUS_NOT_PLAYABLE if skipped[key].get('not_playable') else STATUS_NOT_DECODED,
                           reason=skipped[key]['reason'])
            else:
                row.update(decode_status=STATUS_NOT_DECODED, reason='the exporter did not offer this clip')
            rows.append(row)
    decoded = [r for r in rows if r['decode_status'] == STATUS_DECODED]
    named = [r for r in rows if r['decode_status'] != STATUS_UNNAMED]
    return dict(schema=SCHEMA, model=dict(path=model_path, asset_id=model_id, stem=Path(model_path).stem, joints=joint_count),
                note=NOTE, limitation=CINEMATIC_LIMITATION,
                coordinates='native Y-up, right-handed, game units (glTF convention); no presentation rotation applied',
                counts=dict(named_clips=len(named), decoded=len(decoded),
                            not_decoded=sum(r['decode_status'] == STATUS_NOT_DECODED for r in rows),
                            not_playable=sum(r['decode_status'] == STATUS_NOT_PLAYABLE for r in rows),
                            unnamed_not_exported=len(rows) - len(named),
                            decoded_frames=sum(r['frames'] for r in decoded),
                            cutscenes=len({r['source_asset'] for r in rows})),
                glb=glb, clips=rows, source_errors=list(errors),
                regenerate='python -m rc2016.export.cutscene_animations <game root> --model %s --out <folder>' % model_path)


def resolve_model(archive, selector):
    """(asset id, archive path) of the one model ``selector`` names: an archive path or a unique file stem."""
    models = [e for e in archive.entries.values() if e['type'] == 'model']
    exact = [e for e in models if e['path'] == selector]
    found = exact or [e for e in models if Path(e['path']).stem == selector]
    if len(found) != 1:
        hint = ': ' + ', '.join(sorted(e['path'] for e in found)[:8]) if found else ''
        raise ValueError(f'Model selector {selector!r} matches {len(found)} models{hint}')
    return found[0]['id'], found[0]['path']


def export_model(exporter, archive, selector, out, textures=True, overwrite=False, log=print):
    """Write ``<out>/<stem>/manifest.json`` and the GLB for one model; returns the manifest."""
    from rc2016.export.models import ExportOptions
    model_id, path = resolve_model(archive, selector)
    stem = Path(path).stem
    folder = out / stem
    if folder.exists():
        if not overwrite:
            raise FileExistsError(f'{folder} exists; pass --overwrite to replace it')
        shutil.rmtree(folder)
    rows = model_skeleton(archive.asset_data(model_id))
    if not rows:
        raise ValueError(f'{path} has no skeleton; it cannot hold cutscene animation')
    log(f'{path}: scanning cutscenes for clips covering all {len(rows)} joints')
    sources, errors = cinematic_sources(archive, rows, include_unnamed=True)
    if not any(c['name'] for s in sources for c in s['clips']):
        raise ValueError(f'No named cutscene clip covers every joint of {path}')
    options = ExportOptions(formats=('glb',), textures=textures, animations='all', cinematic_only=True,
                            file_suffix='_cutscenes')
    result = exporter.export(model_id, path, options, cinematic_sources=sources)
    folder = Path(result['folder'])
    glb_name = f'{stem}_cutscenes.glb'
    glb = glb_summary(folder / glb_name)
    glb.update(file=glb_name, size_bytes=(folder / glb_name).stat().st_size, notes=result.get('notes', []))
    digests = {s['asset_id']: hashlib.sha256(archive.asset_data(s['asset_id'])).hexdigest() for s in sources}
    manifest = build_manifest(path, model_id, len(rows), sources, errors, result, digests, glb)
    if glb['animations'] != manifest['counts']['decoded']:
        raise RuntimeError(f"GLB holds {glb['animations']} animations but {manifest['counts']['decoded']} clips decoded")
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    counts = manifest['counts']
    log(f"{stem}: {counts['decoded']}/{counts['named_clips']} clips decoded ({counts['decoded_frames']} frames), "
        f"{counts['unnamed_not_exported']} unnamed, GLB {glb['size_bytes'] // 1024} KiB -> {folder}")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('root', type=Path, help='game or asset_archive root')
    parser.add_argument('--model', action='append', required=True,
                        help='archive path (character/npc/npc_shiv/npc_shiv.model) or unique file stem; repeatable')
    parser.add_argument('--out', type=Path, required=True, help='library folder; one subfolder per model')
    parser.add_argument('--no-textures', action='store_true', help='skip material textures (smaller GLB, grey model)')
    parser.add_argument('--overwrite', action='store_true', help='replace an existing <out>/<model stem> folder')
    args = parser.parse_args(argv)
    from rc2016.export.models import ModelExporter
    repo = Path(__file__).resolve().parents[2]
    out, root = args.out.resolve(), args.root.resolve()
    if out.is_relative_to(repo) or out.is_relative_to(root):
        parser.error('--out must be outside the repository and the game root')
    out.mkdir(parents=True, exist_ok=True)

    def log(message):
        print(message, file=sys.stderr, flush=True)
    exporter = ModelExporter(root, out, log=log)
    archive = exporter.loader._ensure_archive(root)
    failed = 0
    for selector in args.model:
        try:
            export_model(exporter, archive, selector, out, textures=not args.no_textures, overwrite=args.overwrite, log=log)
        except Exception as error:   # one model must not stop the others
            failed += 1
            log(f'FAILED {selector}: {error}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
