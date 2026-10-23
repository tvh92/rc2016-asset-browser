"""Visible light beams of the game's light-cone effects (data only, no GL; docs/levels.md, "Light beams").

The beams are the placements of the `gbl_amb_Light_Cone_*` visual effects (rc2016.levels.effects): the zone says where each
one stands and how it is oriented, and the effect names the cone model it draws (the circular cone: apex at the origin, local
+Z along the aim, 10 long and 3.75 in radius; the wide hanging cone: local -Y along the aim, 2 long and 3.75 in radius). This
module turns those placements into one triangle mesh in native space.

Decoded: which lights have a beam, where and how the cone is oriented, the cone model and its UV, its scale (the mesh
component's, 1.0 in every effect), its colour (the mesh component lists a colour parameter in slot 5 and a scalar in slot 6,
which is inferred to be its opacity) and the pixel program that draws it (rc2016.levels.cone_shader, ported in
rc2016/viewer/shading.py ``BEAM_FRAGMENT``). Inferred or fitted: ``AMPLITUDE`` (the frame constant CB0[115], 0.01 in the one
captured frame, assumed for every level), ``INTENSITY`` (the particle's HDR intensity, undecoded, fitted to one in-game frame)
and the opacity.
"""
from __future__ import annotations

AMPLITUDE = 0.01            # CB0[115], the frame constant the pixel program multiplies the colour by (0.01 in the one captured frame)
INTENSITY = 30.0            # the particle's HDR intensity, which the update code applies to the colour: a fit to one in-game frame
BEAM_GAIN = AMPLITUDE * INTENSITY   # the factor on the colour before the pixel program


def beam_mesh(cones, geometry, gain=BEAM_GAIN):
    """Triangle mesh of the cone placements ``cones`` (rc2016.levels.effects.load_light_cones) in native space: (positions
    (n, 3), normals (n, 3), uvs (n, 2), colours (n, 4), indices (m,)) as float32 and uint32 arrays. ``geometry`` is
    {model path: {'positions', 'normals', 'uvs', 'indices'}} in model space. The UVs are the model's (the pixel program reads
    ``u``, 0 at the apex and 1 at the base, and ``v``, around the cone); a vertex colour is the cone's RGB times ``gain`` and its opacity."""
    import numpy as np
    positions, normals, uvs, colours, indices = [], [], [], [], []
    count = 0
    for cone in cones:
        model = geometry[cone['model']]
        matrix = np.asarray(cone['matrix'], np.float64).reshape(4, 4)
        local = np.asarray(model['positions'], np.float64)
        rotation = matrix[:3, :3]
        positions.append(local @ rotation + matrix[3, :3])
        rotated = np.asarray(model['normals'], np.float64) @ np.linalg.inv(rotation).T
        normals.append(rotated / np.maximum(np.linalg.norm(rotated, axis=1, keepdims=True), 1e-9))
        uvs.append(np.asarray(model['uvs'], np.float64))
        colours.append(np.tile([*(np.asarray(cone['colour'], np.float64) * gain), cone['opacity']], (len(local), 1)))
        indices.append(np.asarray(model['indices'], np.int64) + count)
        count += len(local)
    if not positions:
        empty = np.zeros((0, 3), np.float32)
        return empty, empty.copy(), np.zeros((0, 2), np.float32), np.zeros((0, 4), np.float32), np.zeros(0, np.uint32)
    return (np.concatenate(positions).astype(np.float32), np.concatenate(normals).astype(np.float32),
            np.concatenate(uvs).astype(np.float32), np.concatenate(colours).astype(np.float32),
            np.concatenate(indices).astype(np.uint32))


__all__ = ['BEAM_GAIN', 'beam_mesh']
