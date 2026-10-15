"""Animation clip sources beyond a model's referenced animsets.

Both sources are opt-in and bound to exact source data:

* Real-time cutscenes (``cinematic2``). Their root carries the same anim_clip_lookup /
  anim_clip_data directory as an animset, but the embedded clips have no 'animclip built'
  name string. A clip is named only when its name hash is the game CRC32 of a printable
  string stored in the same file; unnamed clips are skipped, never guessed.
* Borrowed animsets. An animset's clips are offered to a model the game does not pair it with
  only when that model's rest rig matches a model the game does pair it with (through an actor
  that references both): same parent joints and flags, and rest S/Q/T equal within float noise,
  so the absolute source samples pose both meshes the same way.
"""
import json
import re
import zlib

from rc2016.formats.animation_transforms import model_skeleton
from rc2016.formats.container import MAGIC, PRINTABLE, read_container

CINEMATIC_SOURCE = 'cinematic2'
BORROWED_SOURCE = 'borrowed_matching_rig'
CINEMATIC_LIMITATION = ('Real-time cutscene performance: absolute source samples in the cutscene scene space '
                        '(the root may be away from the origin), not an authored idle or loop.')
BORROWED_LIMITATION = ('Clip from an animset the game pairs with another model whose rest rig matches this one '
                       '(same parents and flags, rest S/Q/T within float noise); the game does not reference it '
                       'from this model.')


def crc32(text):
    """``container.game_crc32`` (reflected CRC-32, init 0xEDB88320, no final xor) through zlib."""
    return zlib.crc32(text.encode('ascii'), 0x12477CDF) ^ 0xFFFFFFFF


def crc_string_names(data):
    """{game CRC32: string} for the printable runs in ``data``; a hash two different strings share maps to None."""
    names = {}
    for match in PRINTABLE.finditer(data):
        text = match.group().decode('ascii')
        key = crc32(text)
        if names.get(key, text) != text:
            names[key] = None
        else:
            names[key] = text
    return names


def clip_directory(data, joints=None):
    """Clip containers of an animset or cinematic2 blob in physical order (no samples decoded).

    Each row has offset, declared_size, name, name_source ('animclip_built', 'crc32_string_in_file'
    or None), name_hash, joint_hashes, frames and fps. With ``joints`` (a set of joint hashes) only
    clips whose joint hashes include all of them are returned.
    """
    rows, names = [], None
    for match in re.finditer(re.escape(MAGIC), data):
        try:
            container = read_container(data, match.start(), {})
        except ValueError:
            continue
        metadata = container['metadata']
        if not metadata:
            continue
        hashes = [h['hash'] for h in container['bone_hashes']]
        if joints is not None and not (joints and set(joints) <= set(hashes)):
            continue
        name, source = None, None
        if container['name'] and metadata.get('name_hash_matches_name'):
            name, source = container['name'], 'animclip_built'
        else:
            if names is None:
                names = crc_string_names(data)
            name = names.get(int(metadata['name_hash'], 16))
            source = 'crc32_string_in_file' if name else None
        rows.append(dict(offset=container['offset'], declared_size=container['declared_size'], name=name,
                         name_source=source, name_hash=metadata['name_hash'], joint_hashes=hashes,
                         frames=metadata['original_frame_count'], fps=metadata['sample_rate_fps'],
                         format_version=metadata['format_version'], duration_seconds=metadata['duration_seconds']))
    return rows


SHOT_NAME = re.compile(r'^(?P<cutscene>.+?_rt)_shot(?P<shot>\d+)_', re.I)


def shot_label(clip_name, asset_path):
    """(cutscene, shot number or None) for a cutscene clip.

    Clip names look like ``<cutscene>_rt_shot<N>_<performer>``; the cutscene falls back to the asset's
    file stem and the shot to None when the name does not follow that pattern.
    """
    match = SHOT_NAME.match(clip_name or '')
    stem = asset_path.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    return (match.group('cutscene'), int(match.group('shot'))) if match else (stem, None)


def cinematic_sources(archive, rows, include_unnamed=False):
    """Cutscene assets holding named clips that cover every joint of ``rows``, in archive order.

    Returns (sources, errors): each source is {asset_id, path, clips} with clip_directory rows.
    A static model (no joints) matches nothing. With ``include_unnamed`` the clips whose name hash matches
    no string of their file are listed too (name None); the pipeline never uses those.
    """
    joints = {r['hash'] for r in rows}
    if not joints:
        return [], []
    found, errors = [], []
    entries = sorted((e for e in archive.entries.values() if e['type'] == CINEMATIC_SOURCE
                      and any(o.get('available') for o in e['occurrences'])), key=lambda e: e['order'])
    for entry in entries:
        try:
            clips = [c for c in clip_directory(archive.asset_data(entry['id']), joints) if c['name'] or include_unnamed]
        except Exception as exc:
            errors.append(dict(asset_id=entry['id'], path=entry['path'], reason=str(exc)))
            continue
        if clips:
            found.append(dict(asset_id=entry['id'], path=entry['path'], clips=clips))
    return found, errors


def paired_models(graph, entries, animset_id):
    """Models an actor pairs with ``animset_id``: the actor's resolved references include both.

    ``graph`` is ``AssetIndex.graph`` ({asset id: references with resolved_ids}).
    """
    found = []
    for key, refs in graph.items():
        if entries[key]['type'] != 'actor':
            continue
        ids = {i for ref in refs for i in ref.get('resolved_ids') or ()}
        if animset_id in ids:
            found.extend(i for i in ids if entries[i]['type'] == 'model')
    return sorted(set(found), key=lambda k: entries[k]['order'])


# Float-noise bounds for "the same rig": the rigs the game itself shares an animset between differ
# by at most ~1e-4 of the largest rest translation and ~2e-6 per quaternion component.
BORROW_TOLERANCE = dict(translation_relative=5e-4, rotation_component=1e-4, scale=1e-5)


def rig_comparison(rows, anchor_rows, tolerance=BORROW_TOLERANCE):
    """How closely the rest rig ``rows`` reproduces ``anchor_rows`` (joints matched by hash).

    A shared joint fails when its parent joint hash or flags differ, or its rest S/Q/T differs beyond
    ``tolerance`` (translation relative to the anchor's largest rest translation; quaternions compared up
    to sign). Joints of ``rows`` absent from the anchor are listed: absolute clips leave them at rest.
    ``same_rig`` is true when nothing fails and at least one joint is shared.
    """
    anchor = {r['hash']: r for r in anchor_rows}
    anchor_hashes = [r['hash'] for r in anchor_rows]
    hashes = [r['hash'] for r in rows]
    extent = max((sum(v*v for v in r['translation'])**.5 for r in anchor_rows), default=0.) or 1.
    absent, failed, worst = [], [], dict(translation=0., rotation_component=0., scale=0.)
    for row in rows:
        other = anchor.get(row['hash'])
        if other is None:
            absent.append(row['name']); continue
        parent = hashes[row['parent']] if row['parent'] >= 0 else None
        other_parent = anchor_hashes[other['parent']] if other['parent'] >= 0 else None
        dt = max(abs(a-b) for a, b in zip(row['translation'], other['translation']))
        dq = min(max(abs(a-b) for a, b in zip(row['rotation_xyzw'], other['rotation_xyzw'])),
                 max(abs(a+b) for a, b in zip(row['rotation_xyzw'], other['rotation_xyzw'])))
        ds = max(abs(a-b) for a, b in zip(row['scale'], other['scale']))
        worst.update(translation=max(worst['translation'], dt), rotation_component=max(worst['rotation_component'], dq),
                     scale=max(worst['scale'], ds))
        reason = ('different parent joint' if parent != other_parent else
                  'different joint flags' if row.get('flags') != other.get('flags') else
                  'rest translation differs' if dt > tolerance['translation_relative']*extent else
                  'rest rotation differs' if dq > tolerance['rotation_component'] else
                  'rest scale differs' if ds > tolerance['scale'] else None)
        if reason:
            failed.append(dict(hash=row['hash'], name=row['name'], reason=reason))
    shared = len(rows) - len(absent)
    return dict(same_rig=bool(shared) and not failed, shared_joints=shared, joints=len(rows),
                absent_from_anchor=absent, failed=failed, max_difference=worst,
                tolerance=dict(tolerance, anchor_extent=extent))


def borrowed_animset(archive, graph, animset_path, rows, model_id):
    """The source row for ``--borrow-animset``: ``animset_path`` offered to model ``model_id``.

    Accepted only when the game pairs the animset with another model (``paired_models``) whose rest rig
    ``rig_comparison`` finds the same as ``rows``; otherwise ValueError lists every model tried.
    """
    ids = [k for k in archive.resolve(animset_path) if archive.entries[k]['type'] == 'animset']
    if len(ids) != 1:
        raise ValueError('Borrowed animset must resolve to exactly one animset: ' + animset_path)
    animset = ids[0]
    paired = paired_models(graph, archive.entries, animset)
    if model_id in paired:
        raise ValueError('The game already pairs this model with ' + animset_path + '; nothing to borrow')
    tried = []
    for anchor in paired:
        comparison = rig_comparison(rows, model_skeleton(archive.asset_data(anchor)))
        tried.append(dict(model=archive.entries[anchor]['path'], same_rig=comparison['same_rig'],
                          shared_joints=comparison['shared_joints'], failed=comparison['failed'][:5]))
        if comparison['same_rig']:
            return dict(asset_id=animset, source_type=BORROWED_SOURCE, limitation=BORROWED_LIMITATION,
                        provenance=dict(borrowed_animset=animset_path, rig_anchor_model=archive.entries[anchor]['path'],
                                        rig_anchor_model_id=anchor, rig_comparison=comparison))
    raise ValueError('No model the game pairs with %s has this rest rig: %s' % (animset_path, json.dumps(tried)))
