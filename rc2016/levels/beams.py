"""Visible light beams of the game's light-cone effects (data only, no GL; docs/levels.md, "Light beams").

The beams are the placements of the `gbl_amb_Light_Cone_Circular_*` visual effects (rc2016.levels.effects): the zone says
where each one stands and how it is oriented, and the effect names the cone model it draws (apex at the origin, local +Z
along the aim, 10 long and 3.75 in radius). This module turns those placements into one triangle mesh in native space.

Decoded: which lights have a beam, where and how the cone is oriented, the cone model and its size at the placement's own
scale. Not decoded, so inferred: the colour (the colour of the spot light standing at the placement, scaled to its brightest
channel ``BEAM_GAIN``) and the look (the shaping in ``BEAM_FRAGMENT``, rc2016/viewer/shading.py). The game's pixel shader
reads the colour from a particle buffer and fades with scene depth, none of which is reproduced.
"""
from __future__ import annotations

BEAM_GAIN = 1.0              # display-space strength of the brightest beam texel (a visual choice)


def beam_mesh(cones, geometry, gain=BEAM_GAIN):
    """Triangle mesh of the cone placements ``cones`` (rc2016.levels.effects.load_light_cones) in native space: (positions
    (n, 3), normals (n, 3), t (n,), colours (n, 3), indices (m,)) as float32 and uint32 arrays. ``geometry`` is
    {model path: {'positions', 'normals', 'indices'}} in model space. ``t`` is 0 at the apex and 1 at the base (the model's
    local z over its largest z); a vertex colour is the light colour scaled to its brightest channel ``gain``."""
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
        ts.append(np.clip(local[:, 2] / max(float(local[:, 2].max()), 1e-9), 0.0, 1.0))
        colour = np.asarray(cone['colour'], np.float64)
        colours.append(np.tile(colour / max(float(colour.max()), 1e-6) * gain, (len(local), 1)))
        indices.append(np.asarray(model['indices'], np.int64) + count)
        count += len(local)
    if not positions:
        empty = np.zeros((0, 3), np.float32)
        return empty, empty.copy(), np.zeros(0, np.float32), empty.copy(), np.zeros(0, np.uint32)
    return (np.concatenate(positions).astype(np.float32), np.concatenate(normals).astype(np.float32),
            np.concatenate(ts).astype(np.float32), np.concatenate(colours).astype(np.float32),
            np.concatenate(indices).astype(np.uint32))


__all__ = ['BEAM_GAIN', 'beam_mesh']
