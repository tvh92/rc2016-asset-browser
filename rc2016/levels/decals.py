"""Scorch-mark decals of the level zones (data only, no GL; docs/levels.md, "Decals").

A zone's decal table (`zone_format.decode_decals`) holds pre-clipped meshes in native world space. This module keeps the ones whose
material is `wpn_explosion_scorchmark` and merges them into one triangle mesh for the viewport (``SCORCH_FRAGMENT`` in
rc2016/viewer/shading.py). Every other decal material is counted, not drawn.

Decoded: the meshes and their UVs (as the zone stores them; the V flip is the models' own), the material, and its graph's two
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
    (zone_format.decode_decals rows), float32 and uint32; UV V is flipped like the models' (scene.decode_geometry)."""
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
        uvs.append(np.stack((uv[:, 0], 1.0 - uv[:, 1]), axis=1))
        indices.append(np.asarray(decal['triangles'], np.uint32).reshape(-1) + np.uint32(count))
        count += len(position)
    if not positions:
        return (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 2), np.float32),
                np.zeros(0, np.uint32))
    return (np.concatenate(positions), np.concatenate(normals), np.concatenate(uvs).astype(np.float32),
            np.concatenate(indices).astype(np.uint32))


def load_scorch_decals(archive, zone_paths):
    """{'mesh': scorch_mesh() of every zone, 'count': scorch decals, 'undrawn': {material path: decals},
    'mask', 'emissive': textures.load_texture() results} for the zones that have a `.zone_static`. Textures are
    loaded only when a scorch decal exists."""
    from rc2016.levels.scene import asset_bytes
    from rc2016.levels.zone_format import decode_decals
    from rc2016.viewer import textures
    scorch, undrawn, seen = [], {}, set()
    for original in zone_paths:
        path = original.replace('\\', '/').lstrip('?')
        if not path.lower().endswith('.zone') or path.lower() in seen or len(archive.resolve(path + '_static')) != 1:
            continue
        seen.add(path.lower())
        for decal in decode_decals(asset_bytes(archive, path), asset_bytes(archive, path + '_static')):
            if is_scorch(decal['material']):
                scorch.append(decal)
            else:
                material = decal['material'].replace('\\', '/').lower()
                undrawn[material] = undrawn.get(material, 0) + 1
    result = dict(mesh=scorch_mesh(scorch), count=len(scorch), undrawn=undrawn, mask=None, emissive=None)
    if scorch:
        result['mask'] = textures.load_texture(archive, MASK_TEXTURE)
        result['emissive'] = textures.load_texture(archive, EMISSIVE_TEXTURE)
    return result


__all__ = ['EMISSIVE_TEXTURE', 'MASK_TEXTURE', 'SCORCH_MATERIAL', 'is_scorch', 'load_scorch_decals', 'scorch_mesh']
