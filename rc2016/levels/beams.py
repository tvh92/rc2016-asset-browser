"""Visible light beams of the game's light-cone effects (data only, no GL; docs/levels.md, "Light beams").

The beams are the placements of the `gbl_amb_Light_Cone_*` visual effects (rc2016.levels.effects): the zone says where each
one stands and how it is oriented, and the effect names the cone model it draws (the circular cone: apex at the origin, local
+Z along the aim, 10 long and 3.75 in radius; the wide hanging cone: local -Y along the aim, 2 long and 3.75 in radius). This
module turns those placements into one triangle mesh in native space.

Decoded: which lights have a beam, where and how the cone is oriented, the cone model, its scale (the mesh component's, 1.0
in every effect) and its colour (the mesh component lists a colour parameter in slot 5 and a scalar in slot 6, which is inferred
to be its opacity). Not decoded: how the game's pixel shader turns them into a display value (it reads them from a particle
buffer, fades with scene depth and fog, none of which is reproduced), so ``BEAM_GAIN`` is a fit and the look is the shaping in
``BEAM_FRAGMENT``, rc2016/viewer/shading.py.
"""
from __future__ import annotations

BEAM_GAIN = 34.0            # display-space gain of the colour (RGB times opacity); fitted so Aridia's Med beams (0.0295 at
                            # their peak) keep the level matched against the in-game frame, the other effects follow


def beam_mesh(cones, geometry, gain=BEAM_GAIN):
    """Triangle mesh of the cone placements ``cones`` (rc2016.levels.effects.load_light_cones) in native space: (positions
    (n, 3), normals (n, 3), t (n,), colours (n, 3), indices (m,)) as float32 and uint32 arrays. ``geometry`` is
    {model path: {'positions', 'normals', 'indices', 'axis', 'length'}} in model space (``axis`` points from the apex to the
    base). ``t`` is 0 at the apex and 1 at the base; a vertex colour is the cone's colour times ``gain``."""
    import numpy as np
    positions, normals, ts, colours, indices = [], [], [], [], []
    count = 0
    for cone in cones:
        model = geometry[cone['model']]
        matrix = np.asarray(cone['matrix'], np.float64).reshape(4, 4)
        local = np.asarray(model['positions'], np.float64)
        rotation = matrix[:3, :3]
        positions.append(local @ rotation + matrix[3, :3])
        rotated = np.asarray(model['normals'], np.float64) @ np.linalg.inv(rotation).T
        normals.append(rotated / np.maximum(np.linalg.norm(rotated, axis=1, keepdims=True), 1e-9))
        along = local @ np.asarray(model['axis'], np.float64)
        ts.append(np.clip((along - along.min()) / max(float(model['length']), 1e-9), 0.0, 1.0))
        colours.append(np.tile(np.asarray(cone['colour'], np.float64) * gain, (len(local), 1)))
        indices.append(np.asarray(model['indices'], np.int64) + count)
        count += len(local)
    if not positions:
        empty = np.zeros((0, 3), np.float32)
        return empty, empty.copy(), np.zeros(0, np.float32), empty.copy(), np.zeros(0, np.uint32)
    return (np.concatenate(positions).astype(np.float32), np.concatenate(normals).astype(np.float32),
            np.concatenate(ts).astype(np.float32), np.concatenate(colours).astype(np.float32),
            np.concatenate(indices).astype(np.uint32))


__all__ = ['BEAM_GAIN', 'beam_mesh']
