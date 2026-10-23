"""The light cone's pixel program, read from the game (data only, no GL; docs/levels.md, "Light beams").

The graph `materialgraph/visualeffect/environment/global/ambient/gbl_amb_light_cone_circular_med` (also the one the
wide hanging cone's material points at: the three light-cone materials differ only in the model) holds the forward pixel
program 0xE8CDB312, 572 bytes of GCN code that the translator (rc2016.shaders.translator.Compiler) turns into a DAG of 136
nodes. ``cone_response`` is that DAG as Python, and rc2016/viewer/shading.py ``BEAM_FRAGMENT`` is the same arithmetic in
GLSL; tests/test_cone_shader.py evaluates the DAG itself against ``cone_response`` on random inputs.

With the interpolated attributes of the vertex stage, ``u`` and ``v`` = attribute0.x and .y (the model's UV0: ``u`` is 0 at
the apex and 1 at the base, ``v`` runs around the cone), ``alpha`` = attribute0.w, ``threshold`` = attribute0.z,
``n`` = attribute1.xyz and ``eye`` = attribute2.xyz (the surface normal and a vector to the eye, in one space),
``fog_T`` = attribute2.3, and the engine inputs ``front`` (FRONT_FACE), ``scene_depth`` (the texture of API 0 at the
pixel, compared with POS_W, the fragment's view depth), ``noise`` (the R channel of `textures/visualeffect/noise/
fx_noisetile01.texture`, API 5, sampled at (0.01 u + 0.005 t, v + 0.005 t), t = CB0[99] the game time), ``amplitude``
(CB0[115]), ``fog`` (CB0[112..114]) and ``colour`` (the three half floats read from the per-instance record):

    S     = (1 - |1 - u|^100) min(1, |1 - u|)                    apex and base fade
    nv    = sat(+-n.eye / (|n||eye|))                            + for a front face, - for a back face
    F     = sat(1 - (1.001 - nv)^2)                              facing
    D     = sat((scene_depth - POS_W) (3 - 2.7 u))               soft intersection with the scene
    P     = S (0.8 noise + 0.2) F D
    rgb   = T amplitude colour (1 + 20 S^4) + (1 - T) fog        per channel
    alpha = sat(alpha sat(P))                                    discarded when sat(P) < threshold

The program is drawn additive and two-sided (material flags 0x20D). The pixel program is decoded; the inputs the vertex stage
and the engine supply are not all known: the UV is the model's (decoded), the texture and the depth compare are as the
program reads them, but ``colour`` (the mesh component's colour parameter times an intensity the update code applies),
``alpha`` and ``threshold`` are particle values of the effect, ``amplitude`` is a frame constant (0.01 in the one captured
frame) and ``fog_T`` comes from the vertex stage.
"""
from __future__ import annotations

import math

NOISE_TEXTURE = 'textures/visualeffect/noise/fx_noisetile01.texture'
NOISE_SCROLL = 0.005        # per second of game time, on both coordinates
NOISE_U_SCALE = 0.01
PROGRAM_ID = '0xe8cdb312'
HOT_GAIN = 20.0             # the (1 + 20 S^4) term
FACING_BIAS = 1.001
DEPTH_FADE_BASE, DEPTH_FADE_SLOPE = 3.0, 2.7


def _sat(x):
    return min(max(x, 0.0), 1.0)


def fade(u):
    """S: zero at the apex (u = 0) and at the base (u = 1), 1 - u between except for the steep start."""
    a = abs(1.0 - u)
    return (1.0 - a ** 100.0) * min(1.0, a)


def facing(n, eye, front):
    """F from the normal ``n`` and the vector ``eye`` to the eye: the dot product of their directions, signed by the face."""
    dot = sum(x * y for x, y in zip(n, eye)) / (math.sqrt(sum(x * x for x in n)) * math.sqrt(sum(x * x for x in eye)))
    nv = _sat((1.0 if front else -1.0) * dot)
    return _sat(1.0 - (FACING_BIAS - nv) ** 2)


def cone_response(u, v, n, eye, front, noise, scene_depth, pos_w, alpha, threshold, fog_T, amplitude, colour, fog):
    """(rgb, alpha, keep) of one fragment: the pixel program's outputs (see the module docstring for the inputs)."""
    s = fade(u)
    p = s * (0.8 * noise + 0.2) * facing(n, eye, front) * _sat((scene_depth - pos_w) * (DEPTH_FADE_BASE - DEPTH_FADE_SLOPE * u))
    p = _sat(p)
    hot = HOT_GAIN * abs(s) ** 4
    rgb = [fog_T * amplitude * c * (1.0 + hot) + (1.0 - fog_T) * f for c, f in zip(colour, fog)]
    return rgb, _sat(alpha * p), not p < threshold


__all__ = ['NOISE_TEXTURE', 'cone_response', 'facing', 'fade']
