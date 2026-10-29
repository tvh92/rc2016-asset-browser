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
import hashlib
import json
from pathlib import Path
import re
import statistics
import threading
import zlib

from rc2016.common import unzip_json, zip_json
from rc2016.formats import container as container_module
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


CINEMATIC_DIRECTORY_VERSION = 1
_directories = {}   # archive layout identity -> cinematic_directory result, shared by every Archive of that layout
_directory_lock = threading.Lock()


try:   # the code that reads the directory; a changed reader never reuses an old one
    _DIRECTORY_CODE = [hashlib.sha256(Path(module).read_bytes()).hexdigest() for module in (__file__, container_module.__file__)]
except OSError:   # no sources to hash (a packaged build): keep the directory per Archive only
    _DIRECTORY_CODE = None


def _directory_stamp(archive):
    """Key of the cutscene directory: the archive layout and the code that reads it; None for a stand-in archive."""
    layout = getattr(archive, 'layout_identity', None)
    if not layout or _DIRECTORY_CODE is None:
        return None
    return hashlib.sha256(repr((CINEMATIC_DIRECTORY_VERSION, layout, _DIRECTORY_CODE)).encode()).hexdigest()


def cinematic_directory(archive):
    """[(asset_id, path, clip_directory rows or None, error or None)] for every available cutscene asset, in archive order.

    Reading the 162 cutscenes takes seconds, and every model's cutscene search needs all of them, so the result is
    kept per archive layout for the whole process (the browser, the clip picker and the exporter each open their own
    Archive) and, when the archive names a folder in ``cinematic_disk_cache``, on disk across sessions.
    """
    stamp = _directory_stamp(archive)
    with _directory_lock:
        found = _directories.get(stamp) if stamp else getattr(archive, '_cinematic_directory', None)
        if found is not None:
            return found
        folder = getattr(archive, 'cinematic_disk_cache', None)
        path = Path(folder) / f'{stamp[:24]}.json.gz' if folder and stamp else None
        try:
            found = [tuple(row) for row in unzip_json(path)] if path and path.is_file() else None
        except (OSError, ValueError, TypeError):
            found = None
        if found is None:
            found = []
            entries = sorted((e for e in archive.entries.values() if e['type'] == CINEMATIC_SOURCE
                              and any(o.get('available') for o in e['occurrences'])), key=lambda e: e['order'])
            for entry in entries:
                try:
                    found.append((entry['id'], entry['path'], clip_directory(archive.asset_data(entry['id'])), None))
                except Exception as exc:
                    found.append((entry['id'], entry['path'], None, str(exc)))
            if path:
                try:
                    for old in path.parent.glob('*.json.gz'):
                        old.unlink()
                    zip_json(path, found)
                except OSError:
                    pass   # only loses the reuse next session
        if stamp:
            _directories[stamp] = found
        else:
            archive._cinematic_directory = found
        return found


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
    for asset_id, path, directory, error in cinematic_directory(archive):
        if error is not None:
            errors.append(dict(asset_id=asset_id, path=path, reason=error))
            continue
        clips = [dict(c) for c in directory if (c['name'] or include_unnamed) and joints <= set(c['joint_hashes'])]
        if clips:
            found.append(dict(asset_id=asset_id, path=path, clips=clips))
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


def animset_rig_model(data):
    """Archive path of the model an animset was built for (the first model its header names), or None.

    An animset's strings start with 'Anim Set Built File' and the path of one model (Evil Zurkon's set names
    ``enm_Evil_Zurkon.model``); its absolute clips carry that model's rest translations.
    """
    from rc2016.formats.index import references
    return next((ref['path'] for ref in references(data) if ref.get('path', '').endswith('.model')), None)


# Below this fraction of the source rig's largest rest translation, a joint offset is too short to give a length ratio.
RETARGET_MIN_LENGTH = 1e-3


def translation_retarget(rows, source_rows, tolerance=BORROW_TOLERANCE):
    """Translation mapping from the rig an animset was built for (``source_rows``) to ``rows``; None for the same rig.

    The game drives some models with an animset built for a model of other proportions: Mr. Zurkon and Zurkon Jr.
    play Evil Zurkon's clips (Mr. Zurkon's rig is Evil Zurkon's at about 2/3 scale). Absolute clips store the source
    rig's local translations, which pull such a model apart (Mr. Zurkon's antenna leaves its head). How the game
    adapts them is not decoded; this is a viewing rule: keep the model's rest translation and add the clip's offset
    from the source rest, scaled by the ratio of the two rest lengths,
    ``T = T_rest + k (T_clip - T_source_rest)`` with ``k = |T_rest| / |T_source_rest|``, and the rig's median ``k``
    where the source offset is too short to give one (``RETARGET_MIN_LENGTH``). Rotations and scales stay as decoded.
    Returns {joint hash: (rest translation, source rest translation, k)} for the joints both rigs have.
    """
    if rig_comparison(rows, source_rows, tolerance)['same_rig']:
        return None
    source = {r['hash']: r['translation'] for r in source_rows}
    length = lambda v: sum(x * x for x in v) ** .5
    shortest = RETARGET_MIN_LENGTH * max((length(t) for t in source.values()), default=0.)
    pairs = [(r['hash'], r['translation'], source[r['hash']]) for r in rows if r['hash'] in source]
    ratios = {h: length(t) / length(s) for h, t, s in pairs if length(s) > shortest}
    fallback = statistics.median(ratios.values()) if ratios else 1.
    return {h: (list(t), list(s), ratios.get(h, fallback)) for h, t, s in pairs} or None


def clip_fits_source(frame, mapping):
    """True when ``frame`` (a clip's first frame) sits on the source rig of ``mapping`` rather than on the model's.

    An animset can hold clips already made for the model's proportions (most of the Horned Toad clips the Alien
    Swarmer plays match the Swarmer's brows, not the Toad's); those are left as decoded. Over the joints whose rest
    translations differ, the median distance of the clip's translations to the source rest must be at most half the
    distance to the model's rest (Evil Zurkon's clips: 1e-4 against 0.06 on Mr. Zurkon).
    """
    extent = max((sum(x * x for x in s) ** .5 for _rest, s, _k in mapping.values()), default=0.)
    to_rest, to_source = [], []
    for row in frame:
        rest, source, _k = mapping.get(row['hash'], (None, None, None))
        if rest is None or sum((a - b) ** 2 for a, b in zip(rest, source)) ** .5 <= RETARGET_MIN_LENGTH * extent:
            continue
        to_rest.append(sum((a - b) ** 2 for a, b in zip(row['translation'], rest)) ** .5)
        to_source.append(sum((a - b) ** 2 for a, b in zip(row['translation'], source)) ** .5)
    return bool(to_rest) and statistics.median(to_source) <= .5 * statistics.median(to_rest)


def retarget_frames(frames, mapping):
    """``sample_transforms`` frames with each mapped joint's translation moved onto the model (``translation_retarget``)."""
    def moved(row):
        rest, source, k = mapping[row['hash']]
        return dict(row, translation=[r + k * (c - s) for r, s, c in zip(rest, source, row['translation'])])
    return [[moved(row) if row['hash'] in mapping else row for row in frame] for frame in frames]


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
