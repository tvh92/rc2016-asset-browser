"""Scorch-mark decals of the level zones (data only, no GL; docs/levels.md, "Decals").

A zone's decal table (`zone_format.decode_decals`) holds pre-clipped meshes in native world space. The `wpn_explosion_scorchmark`
decals merge into one triangle mesh for the viewport's own pass (``SCORCH_FRAGMENT`` in rc2016/viewer/shading.py). Every other decal
material that resolves a `_c` texture becomes a synthetic model per (zone, material) (``decal_models``): world-space geometry with one
identity instance, drawn through the level viewer's material path with the decal state of ``decal_state``. Materials with no `_c`
(VFX graphs such as the crate and Pyrocitor scorch marks) are counted, not drawn.

Decoded: the meshes and their UVs (as the zone stores them, which is the models' GL convention too), the material, and its graph's two
named textures, the scorch mask and the ember emissive map (single-channel BC4). Not decoded: the graph's pixel program. The
blobby-noise dissolve it also samples, the ember colour and brightness, and the engine's blend are inferred in the viewer
(alpha-over by the mask, ember map added); the captured Novalis pits darken to about 0.01 from 0.40 where the mask is 1.
"""
from __future__ import annotations

SCORCH_MATERIAL = 'material/visualeffect/equipment/weapon/wpn_explosion_scorchmark/wpn_explosion_scorchmark.material'
MASK_TEXTURE = 'textures/visualeffect/decal/explosion_scorchmark/decal_explosion_scorchmark_mask.texture'
EMISSIVE_TEXTURE = 'textures/visualeffect/decal/explosion_scorchmark/decal_explosion_scorchmark_emissive.texture'


def is_scorch(material):
    return material.replace('\\', '/').lower() == SCORCH_MATERIAL


def scorch_mesh(decals):
    """(positions (n, 3), normals (n, 3), uvs (n, 2), indices (m,)) of the scorch-mark decals in ``decals``
    (zone_format.decode_decals rows), float32 and uint32; the UVs are as stored, which is what the models' GL UVs are too."""
    import numpy as np
    positions, normals, uvs, indices = [], [], [], []
    count = 0
    for decal in decals:
        if not is_scorch(decal['material']):
            continue
        position = np.asarray(decal['positions'], np.float32).reshape(-1, 3)
        uv = np.asarray(decal['uvs'], np.float32).reshape(-1, 2)
        positions.append(position)
        normals.append(np.asarray(decal['normals'], np.float32).reshape(-1, 3))
        uvs.append(uv)
        indices.append(np.asarray(decal['triangles'], np.uint32).reshape(-1) + np.uint32(count))
        count += len(position)
    if not positions:
        return (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 2), np.float32),
                np.zeros(0, np.uint32))
    return (np.concatenate(positions), np.concatenate(normals), np.concatenate(uvs),
            np.concatenate(indices).astype(np.uint32))


def load_scorch_decals(archive, zone_paths):
    """{'mesh': scorch_mesh() of every zone, 'count': scorch decals, 'mask', 'emissive': textures.load_texture() results}
    for the zones that have a `.zone_static`. Textures are loaded only when a scorch decal exists."""
    from rc2016.levels.scene import asset_bytes
    from rc2016.levels.zone_format import decode_decals
    from rc2016.viewer import textures
    scorch, seen = [], set()
    for original in zone_paths:
        path = original.replace('\\', '/').lstrip('?')
        if not path.lower().endswith('.zone') or path.lower() in seen or len(archive.resolve(path + '_static')) != 1:
            continue
        seen.add(path.lower())
        scorch.extend(d for d in decode_decals(asset_bytes(archive, path), asset_bytes(archive, path + '_static'))
                      if is_scorch(d['material']))
    result = dict(mesh=scorch_mesh(scorch), count=len(scorch), mask=None, emissive=None)
    if scorch:
        result['mask'] = textures.load_texture(archive, MASK_TEXTURE)
        result['emissive'] = textures.load_texture(archive, EMISSIVE_TEXTURE)
    return result


MODEL_PREFIX = 'decal/'


def decal_state(material_path, blend, graph):
    """{'blend': 'alpha_over'/'additive', 'two_sided': False} drawn for a decal material: additive when the `.material` flags word
    says so, otherwise alpha over by the `_c` alpha, whatever the word says. The engine's decal pass blends (docs/shading.md,
    "Render state"); a vent decal flagged opaque (`met_arch_decal_03`) has a black rectangle without it. Inferred, not decoded, for
    the materials whose word is opaque."""
    return dict(blend='additive' if blend == 'additive_alpha' else 'alpha_over', two_sided=False)


def decal_models(archive, zone_paths):
    """[{'path', 'zone', 'geometry', 'groups'}] one synthetic model per (zone, material) for the non-scorch decals whose
    material resolves a `_c` texture, plus {material path: decals} that were not drawn. The geometry is the level viewer's model
    geometry (world-space positions, one batch, one material); ``groups`` is the one identity instance in zone_scene's format."""
    import re
    from array import array
    import numpy as np
    from rc2016.levels.scene import asset_bytes
    from rc2016.levels.zone_format import decode_decals
    from rc2016.pipeline import render_state
    from rc2016.viewer import textures
    usable, undrawn, models, seen = {}, {}, [], set()

    def material_info(material):
        key = material.lower()
        if key not in usable:
            try:
                data = bytes(asset_bytes(archive, material))
                flags = render_state.material_flags(data=data)
                graph = (re.findall(rb'[ -~]+\.materialgraph', data) or [b''])[0].decode('ascii')
                blend = render_state.decode_flags(flags)['surface'] if flags is not None else None
                has_c = bool(textures.material_texture_paths(archive, material).get('_c'))
                usable[key] = decal_state(material, blend, graph) if has_c else None
            except (ValueError, KeyError, OSError):
                usable[key] = None
        return usable[key]
    for original in zone_paths:
        path = original.replace('\\', '/').lstrip('?')
        if not path.lower().endswith('.zone') or path.lower() in seen or len(archive.resolve(path + '_static')) != 1:
            continue
        seen.add(path.lower())
        by_material = {}
        for decal in decode_decals(asset_bytes(archive, path), asset_bytes(archive, path + '_static')):
            if is_scorch(decal['material']):
                continue
            if material_info(decal['material']) is None:
                key = decal['material'].replace('\\', '/').lower()
                undrawn[key] = undrawn.get(key, 0) + 1
                continue
            by_material.setdefault(decal['material'], []).append(decal)
        for material, group in by_material.items():
            position = np.concatenate([np.asarray(d['positions'], np.float32).reshape(-1, 3) for d in group])
            normal = np.concatenate([np.asarray(d['normals'], np.float32).reshape(-1, 3) for d in group])
            uv = np.concatenate([np.asarray(d['uvs'], np.float32).reshape(-1, 2) for d in group])
            offsets = np.cumsum([0] + [len(d['positions']) for d in group[:-1]])
            triangles = np.concatenate([np.asarray(d['triangles'], np.uint32).reshape(-1, 3) + np.uint32(o)
                                        for d, o in zip(group, offsets)])

            def packed(values, typecode):
                result = array(typecode)
                result.frombytes(np.ascontiguousarray(values).tobytes())
                return result
            geometry = dict(positions=packed(position, 'f'), normals=packed(normal, 'f'), raw_vectors=array('f'),
                            static_vector_eligible=False, uvs=packed(uv, 'f'), indices=packed(triangles.reshape(-1), 'I'),
                            batches=[(0, 0, len(triangles), True)], materials=[dict(index=0, name=None, path=material)],
                            radius=float(np.linalg.norm(position, axis=1).max()), triangles=len(triangles),
                            source_sha256='', skinned=False, bind_usable=True,
                            bounds=(position.min(axis=0).tolist(), position.max(axis=0).tolist()),
                            decal=material_info(material))
            models.append(dict(path=f'{MODEL_PREFIX}{path}|{material}', zone=original.replace('\\', '/'), geometry=geometry,
                               groups={(False, ()): array('f', [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])}))
    return models, undrawn


__all__ = ['EMISSIVE_TEXTURE', 'MASK_TEXTURE', 'MODEL_PREFIX', 'SCORCH_MATERIAL', 'decal_models', 'decal_state', 'is_scorch',
           'load_scorch_decals', 'scorch_mesh']
