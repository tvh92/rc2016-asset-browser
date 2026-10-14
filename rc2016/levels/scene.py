"""Level viewer data: a zone's model instances and GPU-ready model geometry, read straight from the archive.

No GL here. Geometry stays in the models' native Y-up space and instance matrices stay in the
zone's row-vector convention (docs/levels.md); the viewport
applies the presentation basis once, in its camera matrix.
"""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import sys
import tempfile

from rc2016.formats.archive import Archive, decompress, locate

from rc2016.levels.level_format import decode_level
from rc2016.levels.zone_format import decode_zone_arrays, name_crc32

# The Blender assembler keeps these but hides them (docs/levels.md).
HIDDEN_MARKERS = ('collision', 'environment/template/')


def open_archive(source_root):
    """Archive for a source root that holds the level assets (the `Image0` tree)."""
    root = locate(Path(source_root).expanduser().resolve())
    archive = Archive(root, Path(tempfile.gettempdir()) / 'rcps4-level-view', lambda *_a, **_k: None)
    archive.verify_repeats = False   # preview only: skip re-reading every repeated copy of an asset
    from rc2016.viewer.textures import TextureDiskCache
    from rc2016.viewer.native_glsl import TranslationDiskCache
    cache = Path(__file__).resolve().parents[2] / '.cache'
    archive.texture_disk_cache = TextureDiskCache(cache / 'texture_preview')
    archive.translation_disk_cache = TranslationDiskCache(cache / 'native_translation')
    return archive


def asset_bytes(archive, path):
    ids = archive.resolve(path.replace('\\', '/'))
    if len(ids) != 1:
        raise ValueError(f'{path}: expected one archive entry, found {len(ids)}')
    return decompress(archive.bytes(ids[0])[0])[0]


def hidden_by_default(model_path):
    lower = model_path.replace('\\', '/').lower()
    return any(marker in lower for marker in HIDDEN_MARKERS)


def mirrored(rows):
    (a, b, c) = (r[:3] for r in rows[:3])
    det = (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
           + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return det < 0


def level_list(archive):
    """Every `.level` asset path in the archive, sorted."""
    return sorted(e['path'] for e in archive.entries.values() if e['type'] == 'level')


def level_zone_paths(archive, level_path):
    """The zone asset paths a `.level` lists (docs/levels.md)."""
    return [z.replace('\\', '/') for z in decode_level(asset_bytes(archive, level_path))['zones']]


def zone_label(zone_path):
    """Short zone name for lists: the file stem (`art_buildings`)."""
    return zone_path.replace('\\', '/').rsplit('/', 1)[-1].rsplit('.', 1)[0]


def zone_scene(archive, zone_path):
    """{'zone', 'models': [{'path', 'hidden', 'count', 'groups'}], 'instances', 'points'}: translations
    of the instances shown by default, native Y-up world space. A model whose instances carry
    position-keyed vertex colours also has 'vertex_colours': [zone_format.vertex_colour_block]
    and 'vertex_paint_blocks': {content identity: block}; its group keys append that identity
    (empty string for an unpainted instance). Paint and matrices never travel independently.

    ``groups`` is {(mirrored, overrides): array('f') of 16-float rows}, where ``overrides`` is the
    instance's raw material-override entries as ((slot hash, material path or None, material hash), ...);
    effective_groups() resolves them against the model's material slots. Rows keep instance order and
    each model's groups keep first-appearance order."""
    import numpy as np
    try:
        zone = decode_zone_arrays(asset_bytes(archive, zone_path))
    except ValueError as exc:
        if 'lacks instance sections' not in str(exc):
            raise
        # Sound, nav, gameplay-actor and similar zones place no models.
        return dict(zone=zone_path, models=[], instances=0, points=[])
    models = [dict(path=p.replace('\\', '/'), hidden=hidden_by_default(p), count=0, groups={})
              for p in zone['models']]
    matrices, model_index = zone['matrices'], zone['model_index'].astype(np.int64)
    if not len(model_index):
        return dict(zone=zone_path, models=[], instances=zone['instances'], points=[])
    flips = mirrored_array(matrices)
    # One code per (model, mirrored, overrides) key; override-bearing instances get codes past 2 * models.
    codes = model_index * 2 + flips
    keys, extra = {}, {}
    for i, entries in zone['overrides'].items():
        overrides = tuple((o['slot_hash'], o['material'], o['material_hash']) for o in entries)
        codes[i] = extra.setdefault((int(model_index[i]), bool(flips[i]), overrides), 2 * len(models) + len(extra))
    for (index, flip, overrides), code in extra.items():
        keys[code] = (index, flip, overrides)
    unique, first, inverse = np.unique(codes, return_index=True, return_inverse=True)
    order = np.argsort(inverse, kind='stable')
    ends = np.cumsum(np.bincount(inverse, minlength=len(unique)))
    for u in np.argsort(first, kind='stable').tolist():
        code = int(unique[u])
        index, flip, overrides = keys.get(code) or (code // 2, bool(code % 2), ())
        rows = array('f')
        rows.frombytes(np.ascontiguousarray(matrices[order[(ends[u - 1] if u else 0):ends[u]]]).tobytes())
        models[index]['groups'][(flip, overrides)] = rows
    for index, count in enumerate(np.bincount(model_index, minlength=len(models)).tolist()):
        models[index]['count'] = count
    for i, block in sorted(zone.get('vertex_colours', {}).items()):
        models[int(model_index[i])].setdefault('vertex_colours', []).append(block)
    # Partition only paint-bearing models; no-paint groups keep their existing shape.
    for index in sorted({int(model_index[i]) for i in zone.get('vertex_colours',{})}):
        model=models[index];model['groups']={};model['vertex_paint_blocks']={}
        for i in np.flatnonzero(model_index==index).tolist():
            block=zone.get('vertex_colours',{}).get(i)
            paint=vertex_paint_identity(block) if block is not None else ''
            if block is not None:model['vertex_paint_blocks'].setdefault(paint,block)
            overrides=tuple((o['slot_hash'],o['material'],o['material_hash']) for o in zone['overrides'].get(i,[]))
            model['groups'].setdefault((bool(flips[i]),overrides,paint),array('f')).frombytes(
                np.ascontiguousarray(matrices[i]).tobytes())
    shown = ~np.array([m['hidden'] for m in models], bool)[model_index]
    points = [tuple(p) for p in matrices[shown, 12:15].tolist()]
    return dict(zone=zone_path, models=[m for m in models if m['count']], instances=zone['instances'],
                points=points)


VERTEX_COLOUR_TOLERANCE = 0.6   # key units (0.1 model units) per axis; observed residuals -0.5..0.5625


def keyed_vertex_colours(positions, block):
    """(colours uint8 (n, 4), matched bool (n,)) for model-space ``positions`` (n, 3) from one
    zone_format.vertex_colour_block: a vertex takes the colour of the key nearest to its position
    x 10 (Chebyshev distance at most VERTEX_COLOUR_TOLERANCE); unmatched vertices are 255."""
    import numpy as np
    from rc2016.levels.zone_format import VERTEX_COLOUR_KEY_BITS as bits
    keys, colours = block
    bias = 1 << (bits - 1)
    code = lambda k: (k[..., 0] + bias) | ((k[..., 1] + bias) << bits) | ((k[..., 2] + bias) << (2 * bits))
    codes = code(np.asarray(keys, np.int64))
    order = np.argsort(codes, kind='stable'); codes = codes[order]
    scaled = np.asarray(positions, np.float64).reshape(-1, 3) * 10.0
    base = np.floor(scaled).astype(np.int64)
    best = np.full(len(scaled), np.inf); index = np.zeros(len(scaled), np.int64)
    for corner in np.array(np.meshgrid([0, 1], [0, 1], [0, 1], indexing='ij')).reshape(3, -1).T:
        candidate = base + corner
        distance = np.abs(candidate - scaled).max(axis=1)
        at = np.minimum(np.searchsorted(codes, code(candidate)), len(codes) - 1)
        hit = (codes[at] == code(candidate)) & (distance <= VERTEX_COLOUR_TOLERANCE) & (distance < best)
        best[hit] = distance[hit]; index[hit] = order[at[hit]]
    matched = np.isfinite(best)
    result = np.full((len(scaled), 4), 255, np.uint8)
    result[matched] = np.asarray(colours, np.uint8)[index[matched]]
    return result, matched


def vertex_paint_identity(block):
    """Content identity of a source block; never identifies shader meaning."""
    import hashlib
    import numpy as np
    keys,colours=block
    return hashlib.sha256(np.asarray(keys,dtype='<i8').tobytes()+np.asarray(colours,dtype='u1').tobytes()).hexdigest()


def vertex_paint_streams(geometry, blocks):
    """Decode each distinct block once; geometry and instance transforms stay shared."""
    streams={}
    for identity,block in blocks.items():
        alpha,ground,note=instance_vertex_paint(geometry,[block],1)
        streams[identity]=dict(alpha=alpha,ground=ground,note=note)
    return streams


def instance_vertex_alpha(geometry, blocks, instances):
    """Compatibility alpha-only view of instance_vertex_paint."""
    alpha, _ground, note = instance_vertex_paint(geometry, blocks, instances)
    return alpha, note


def instance_vertex_paint(geometry, blocks, instances):
    """(alpha array, candidate R/G/key-flag array, note) for a model placed ``instances`` times whose
    instances carry ``blocks`` (vertex_colour_block results). This legacy uniform adapter
    supplies a stream only when every instance carries the same block; the
    alpha is byte 3 / 255 and 1 where no key matches. The second array packs
    candidate red, green and a matched-key flag for the experimental ground
    preview. Channels are squared before interpolation in the vertex stage;
    unlisted vertices use uniform review controls. Inferred, see zone_format."""
    import numpy as np
    if not blocks:
        return None, None, None
    first = blocks[0]
    if len(blocks) != instances or any(not (np.array_equal(b[0], first[0]) and np.array_equal(b[1], first[1]))
                                       for b in blocks[1:]):
        return None, None, f'{len(blocks)} of {instances} instances carry vertex colours that differ; not shown'
    colours, matched = keyed_vertex_colours(np.frombuffer(geometry['positions'], np.float32).reshape(-1, 3), first)
    alpha = array('f'); alpha.frombytes((colours[:, 3] / 255.0).astype(np.float32).tobytes())
    # R/G byte interpretation is a candidate join to the source UNORM4x8
    # vertex buffer. Unmatched vertices retain the existing review controls.
    ground = np.column_stack((colours[:, :2] / 255.0, matched)).astype(np.float32)
    channels = array('f'); channels.frombytes(ground.tobytes())
    return alpha, channels, f'{int(matched.sum())} of {len(matched)} vertices take one of {len(first[0])} keyed colours'


def mirrored_array(matrices):
    """mirrored() of each (n, 16) row-vector matrix, evaluated in float64 in the same order."""
    import numpy as np
    m = np.asarray(matrices, np.float64).reshape(-1, 16)
    a0, a1, a2, b0, b1, b2, c0, c1, c2 = (m[:, i] for i in (0, 1, 2, 4, 5, 6, 8, 9, 10))
    det = a0 * (b1 * c2 - b2 * c1) - a1 * (b0 * c2 - b2 * c0) + a2 * (b0 * c1 - b1 * c0)
    return det < 0


# Outcomes of one override entry; only APPLIED (and SAME, which changes nothing) affect drawing.
APPLIED, SAME = 'applied', 'same as default'
NULL_MATERIAL, UNLISTED_MATERIAL = 'null replacement', 'replacement not in zone list'
MISSING_SLOT, AMBIGUOUS_SLOT, CONFLICT = 'slot not in model', 'slot name ambiguous', 'slot overridden twice'


def resolve_overrides(materials, overrides):
    """One instance's effective material changes: ((slot index, material path), ...) sorted, plus the
    outcome of each entry.

    ``materials`` is the model's material table ({'index', 'name', 'path'}); an entry applies only when
    its slot-name hash matches exactly one slot name and its replacement is a listed path. A slot that
    two entries set to different materials is left unchanged."""
    by_hash = {}
    for material in materials:
        if material.get('name') is not None:
            by_hash.setdefault(f"{name_crc32(material['name']):08X}", []).append(material)
    chosen, outcomes = {}, []
    for slot_hash, path, material_hash in overrides:
        hits = by_hash.get(slot_hash, [])
        if path is None:
            outcomes.append(NULL_MATERIAL if int(material_hash, 16) == 0 else UNLISTED_MATERIAL)
        elif not hits:
            outcomes.append(MISSING_SLOT)
        elif len(hits) > 1:
            outcomes.append(AMBIGUOUS_SLOT)
        else:
            chosen.setdefault(hits[0]['index'], []).append((path, len(outcomes)))
            outcomes.append(APPLIED)
    applied = []
    defaults = {m['index']: (m.get('path') or '').lower() for m in materials}
    for index, choices in chosen.items():
        if len({p.lower() for p, _ in choices}) > 1:
            for _p, position in choices:
                outcomes[position] = CONFLICT
        elif choices[0][0].lower() == defaults.get(index):
            for _p, position in choices:
                outcomes[position] = SAME
        else:
            applied.append((index, choices[0][0]))
    return tuple(sorted(applied)), outcomes


def effective_groups(materials, groups):
    """Regroup zone_scene groups by (mirrored, effective material changes): instances whose overrides
    resolve to the same changes share a group, instances without effective changes join the plain
    group. Returns (groups, Counter of entry outcomes, number of instances with changed materials)."""
    from collections import Counter
    result, outcomes, changed = {}, Counter(), 0
    for key, rows in groups.items():
        flip,overrides=key[:2]
        applied, results = resolve_overrides(materials, overrides) if overrides else ((), [])
        count = len(rows) // 16
        for outcome in results:
            outcomes[outcome] += count
        if applied:
            changed += count
        result.setdefault((flip, applied, *key[2:]), array('f')).extend(rows)
    return result, outcomes, changed


def core_bounds(points, trim=0.02):
    """(low, high) per axis between the ``trim`` and 1 - ``trim`` quantiles, or None without points.

    Levels ring their play space with distant backdrops (Novalis: mountains ~10x farther out than
    the city), so full bounds frame mostly empty sky. The quantiles are order statistics of the
    sorted values (np.partition), not interpolated."""
    import numpy as np
    if points is None or not len(points):
        return None
    values = np.asarray([tuple(p[:3]) for p in points] if not isinstance(points, np.ndarray) else points[:, :3],
                        dtype=np.float64)
    count = len(values)
    cut = int(count * trim)
    part = np.partition(values, sorted({cut, count - 1 - cut}), axis=0)
    return tuple(float(x) for x in part[cut]), tuple(float(x) for x in part[count - 1 - cut])


# Decoded level geometry (.npz), keyed by the model's archive bytes and the decoder sources.
GEOMETRY_CACHE_DIR = Path(__file__).resolve().parents[2] / '.cache' / 'level_geometry'
GEOMETRY_CACHE_VERSION = 1
GEOMETRY_ARRAYS = (('positions', 'f', '<f4'), ('normals', 'f', '<f4'), ('uvs', 'f', '<f4'), ('indices', 'I', '<u4'))
_fingerprint = []


def decoder_fingerprint():
    """SHA-256 of the sources that turn model bytes into level geometry (rc2016/formats, common.py and
    this module), or None when they cannot be read (frozen build): then nothing is cached."""
    if not _fingerprint:
        import hashlib
        package = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256()
        try:
            for source in sorted([*(package / 'formats').glob('*.py'), package / 'common.py', Path(__file__).resolve()]):
                digest.update(source.name.encode() + b'\0' + source.read_bytes())
            _fingerprint.append(digest.hexdigest())
        except OSError:
            _fingerprint.append(None)
    return _fingerprint[0]


def geometry_cache_key(model_path, lod, look, source_sha256, fingerprint):
    import hashlib
    identity = repr((GEOMETRY_CACHE_VERSION, fingerprint, model_path.replace('\\', '/').lower(), lod, look, source_sha256))
    return hashlib.sha256(identity.encode()).hexdigest()[:40]


def _restore_meta(meta):
    meta['batches'] = [tuple(b) for b in meta['batches']]
    meta['bounds'] = tuple(meta['bounds'])
    return meta


def save_cached_geometry(path, geometry):
    """Write ``geometry`` (model_geometry's dict) as an uncompressed .npz; atomic. Nothing is written
    when the fields would not read back equal; write errors only lose the speed-up."""
    import io
    import json
    import os
    import threading
    import numpy as np
    names = {name for name, _code, _dtype in GEOMETRY_ARRAYS}
    meta = {k: v for k, v in geometry.items() if k not in names}
    try:
        text = json.dumps(meta)
        if _restore_meta(json.loads(text)) != meta:
            return
    except (TypeError, ValueError, KeyError):
        return
    buffer = io.BytesIO()
    np.savez(buffer, meta=np.frombuffer(text.encode(), np.uint8),
             **{name: np.frombuffer(geometry[name], dtype) for name, _code, dtype in GEOMETRY_ARRAYS})
    path = Path(path)
    temp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident():x}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(buffer.getvalue())
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass


def load_cached_geometry(path):
    """model_geometry's dict from save_cached_geometry, or None if absent or unreadable."""
    import json
    import numpy as np
    try:
        with np.load(path, allow_pickle=False) as stored:
            geometry = _restore_meta(json.loads(stored['meta'].tobytes().decode()))
            for name, code, dtype in GEOMETRY_ARRAYS:
                values = array(code)
                values.frombytes(np.ascontiguousarray(stored[name], dtype).tobytes())
                geometry[name] = values
    except Exception:
        return None
    return geometry


def model_geometry(archive, model_path, lod='Highest', look=0, cache=True):
    """Packed native-space geometry of one model's selected look/LOD.

    {'positions', 'normals', 'uvs' (array 'f'), 'indices' (array 'I'),
     'batches': [(material slot, first triangle, triangle count, has UV0)],
     'materials': [{'index', 'name', 'path'}], 'radius', 'triangles', 'source_sha256'}

    With ``cache`` the result is kept under GEOMETRY_CACHE_DIR, keyed by the asset's archive bytes
    (SHA-256), the selection and decoder_fingerprint(); an unreadable file is decoded again.
    """
    ids = archive.resolve(model_path.replace('\\', '/'))
    if len(ids) != 1:
        raise ValueError(f'{model_path}: expected one archive entry, found {len(ids)}')
    raw, raw_sha256 = archive.bytes(ids[0])
    fingerprint = decoder_fingerprint() if cache else None
    target = None
    if fingerprint:
        target = GEOMETRY_CACHE_DIR / f'{geometry_cache_key(model_path, lod, look, raw_sha256, fingerprint)}.npz'
        cached = load_cached_geometry(target) if target.is_file() else None
        if cached is not None:
            return cached
    geometry = decode_geometry(model_path, decompress(raw)[0], lod, look)
    if target is not None:
        save_cached_geometry(target, geometry)
    return geometry


def decode_geometry(model_path, data, lod='Highest', look=0):
    """model_geometry from decompressed model bytes, without the cache."""
    import numpy as np
    from rc2016.formats.native_model import decode_model
    decoded = decode_model(model_path, lod=lod, look=look, data=data, as_arrays=True)
    positions, normals, uvs, indices, batches = [], [], [], [], []
    skinned = any(any(bool(pairs) for pairs in m.get('weights', [])) for m in decoded['meshes'])
    vertex_total = triangle_total = 0
    for mesh in decoded['meshes']:
        if not len(mesh['triangles']):
            continue
        count = len(mesh['positions'])
        positions.append(mesh['positions']); normals.append(mesh['normals'])
        uv0 = mesh.get('uv0')
        if uv0 is not None and len(uv0) == count:
            # The decoder flips V for Blender; textures upload top row first, so flip back.
            uvs.append(np.stack((uv0[:, 0], 1.0 - uv0[:, 1]), axis=1))
        else:
            uvs.append(np.zeros((count, 2)))
        batches.append(((mesh.get('material') or {}).get('index'), triangle_total, len(mesh['triangles']),
                        uv0 is not None and bool(len(uv0))))
        indices.append(mesh['triangles'] + vertex_total)
        vertex_total += count; triangle_total += len(mesh['triangles'])
    if not indices:
        raise ValueError('selected look/LOD has no geometry')
    positions = np.concatenate(positions)
    radius = float(np.max(positions[:, 0] * positions[:, 0] + positions[:, 1] * positions[:, 1]
                          + positions[:, 2] * positions[:, 2]))

    def packed(parts, typecode, dtype):
        result = array(typecode)
        result.frombytes(np.ascontiguousarray(np.concatenate(parts) if isinstance(parts, list) else parts,
                                              dtype=dtype).tobytes())
        return result
    bounds = (positions.min(axis=0).tolist(), positions.max(axis=0).tolist())
    triangles = np.concatenate(indices)
    edges = positions[triangles[:, 1]] - positions[triangles[:, 0]]
    sides = positions[triangles[:, 2]] - positions[triangles[:, 0]]
    usable = bool(np.isfinite(positions).all() and np.any(np.linalg.norm(np.cross(edges, sides), axis=1) > 1e-10))
    positions, normals = packed(positions, 'f', np.float32), packed(normals, 'f', np.float32)
    uvs, indices = packed(uvs, 'f', np.float32), packed(indices, 'I', np.uint32)
    return dict(positions=positions, normals=normals, uvs=uvs, indices=indices, batches=batches,
                materials=[dict(index=m.get('index', i), name=m.get('name'), path=m.get('path'))
                           for i, m in enumerate(decoded['materials'])],
                radius=math.sqrt(radius), triangles=len(indices) // 3, source_sha256=decoded['source_sha256'],
                skinned=skinned, bind_usable=usable, bounds=bounds)


def material_entries(archive, materials, done, texture_cache, previews=False):
    """{material path: texture_preview entry} for materials whose path is not in ``done``. Experimental
    previews are prepared only with ``previews`` (the level panel does that on demand)."""
    from rc2016.viewer.textures import material_textures
    todo = [m for m in materials if m.get('path') and m['path'] not in done]
    if not todo:
        return {}
    by_index = material_textures(archive, [dict(index=i, path=m['path']) for i, m in enumerate(todo)], texture_cache,
                                 previews=previews)
    return {m['path']: by_index[i] for i, m in enumerate(todo)}
