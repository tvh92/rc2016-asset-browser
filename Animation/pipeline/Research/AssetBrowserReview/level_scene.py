"""Level viewer data: a zone's model instances and GPU-ready model geometry, read straight from the archive.

No GL here. Geometry stays in the models' native Y-up space and instance matrices stay in the
zone's row-vector convention (Animation/pipeline/Research/Levels/README.md); the viewport
applies the presentation basis once, in its camera matrix.
"""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import sys
import tempfile

from Animation.pipeline.archive import Archive, decompress, locate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Levels'))
from zone_instances import decode_zone  # noqa: E402
from level_zones import decode_level  # noqa: E402

# The Blender assembler keeps these but hides them (Levels README, "Assembly").
HIDDEN_MARKERS = ('collision', 'environment/template/')


def open_archive(source_root):
    """Archive for a source root that holds the level assets (the `Image0` tree)."""
    root = locate(Path(source_root).expanduser().resolve())
    archive = Archive(root, Path(tempfile.gettempdir()) / 'rcps4-level-view', lambda *_a, **_k: None)
    archive.verify_repeats = False   # preview only: skip re-reading every repeated copy of an asset
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
    """The zone asset paths a `.level` lists (Levels README, "Decoded: `.level`")."""
    return [z.replace('\\', '/') for z in decode_level(asset_bytes(archive, level_path))['zones']]


def zone_label(zone_path):
    """Short zone name for lists: the file stem (`art_buildings`)."""
    return zone_path.replace('\\', '/').rsplit('/', 1)[-1].rsplit('.', 1)[0]


def zone_scene(archive, zone_path):
    """{'zone', 'models': [{'path', 'hidden', 'count', 'groups': {mirrored: array('f') of 16-float rows}}],
    'instances', 'points'}: translations of the instances shown by default, native Y-up world space."""
    try:
        zone = decode_zone(asset_bytes(archive, zone_path))
    except ValueError as exc:
        if 'lacks instance sections' not in str(exc):
            raise
        # Sound, nav, gameplay-actor and similar zones place no models.
        return dict(zone=zone_path, models=[], instances=0, points=[])
    models = [dict(path=p.replace('\\', '/'), hidden=hidden_by_default(p), count=0, groups={})
              for p in zone['models']]
    points = []
    for instance in zone['instances']:
        rows = instance['matrix_rows']
        model = models[instance['model_index']]
        model['groups'].setdefault(mirrored(rows), array('f')).extend(x for row in rows for x in row)
        model['count'] += 1
        if not model['hidden']:
            points.append(tuple(rows[3][:3]))
    return dict(zone=zone_path, models=[m for m in models if m['count']], instances=len(zone['instances']),
                points=points)


def core_bounds(points, trim=0.02):
    """(low, high) per axis between the ``trim`` and 1 - ``trim`` quantiles, or None without points.

    Levels ring their play space with distant backdrops (Novalis: mountains ~10x farther out than
    the city), so full bounds frame mostly empty sky."""
    if not points:
        return None
    low, high = [], []
    for axis in range(3):
        values = sorted(p[axis] for p in points)
        cut = int(len(values) * trim)
        low.append(values[cut]); high.append(values[len(values) - 1 - cut])
    return tuple(low), tuple(high)


def model_geometry(archive, model_path, lod='Highest', look=0):
    """Packed native-space geometry of one model's selected look/LOD.

    {'positions', 'normals', 'uvs' (array 'f'), 'indices' (array 'I'),
     'batches': [(material slot, first triangle, triangle count, has UV0)],
     'materials': [{'index', 'name', 'path'}], 'radius', 'triangles', 'source_sha256'}
    """
    import numpy as np
    from Animation.pipeline.native_model import decode_model
    decoded = decode_model(model_path, lod=lod, look=look, data=asset_bytes(archive, model_path), as_arrays=True)
    positions, normals, uvs, indices, batches = [], [], [], [], []
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
    positions, normals = packed(positions, 'f', np.float32), packed(normals, 'f', np.float32)
    uvs, indices = packed(uvs, 'f', np.float32), packed(indices, 'I', np.uint32)
    return dict(positions=positions, normals=normals, uvs=uvs, indices=indices, batches=batches,
                materials=[dict(index=m.get('index', i), name=m.get('name'), path=m.get('path'))
                           for i, m in enumerate(decoded['materials'])],
                radius=math.sqrt(radius), triangles=len(indices) // 3, source_sha256=decoded['source_sha256'])


def material_entries(archive, materials, done, texture_cache):
    """{material path: texture_preview entry} for materials whose path is not in ``done``."""
    from .texture_preview import material_textures
    todo = [m for m in materials if m.get('path') and m['path'] not in done]
    if not todo:
        return {}
    by_index = material_textures(archive, [dict(index=i, path=m['path']) for i, m in enumerate(todo)], texture_cache)
    return {m['path']: by_index[i] for i, m in enumerate(todo)}
