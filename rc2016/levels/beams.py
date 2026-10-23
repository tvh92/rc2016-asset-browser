"""Visible light beams of lamp spot lights (data only, no GL; docs/levels.md, "Lamp beams").

The game draws a soft cone around some spot lights (the lighting zone lists ``gbl_amb_Light_Cone_Circular_*`` visual
effects, particle systems whose placement is not decoded). This module is an inferred stand-in: the spot lights that
sit on a lamp model and aim level or downward get one additive cone along their aim axis, as long as their range (at most ``MAX_LENGTH``) and as
wide as ``BEAM_ANGLE_SCALE`` times the mean of their inner and outer half angles.

Inferred, not decoded: that a spot within ``LAMP_DISTANCE`` of an instance of a lamp model carries a beam (the claw lamp's
spot lies 3.1 units from its instance origin), that upward-aimed spots (``MAX_AIM_Y``) do not, which models are lamps
(``LAMP_MODEL``), the cone extent (the light's range, and ``BEAM_ANGLE_SCALE`` times the mean of its inner and outer
half angles: 17 degrees for the 22 and 46 degree claw lamp, which is about the width of that lamp's beam in one in-game
frame; the half angles themselves are decoded in ``rc2016.levels.scene_lighting``, the scale is a fit) and the look (``BEAM_GAIN`` and the shaping in ``BEAM_FRAGMENT``,
rc2016/viewer/shading.py, are visual choices). Whether the lamps' beams are a visual effect or part of the light is unknown.
"""
from __future__ import annotations

import math
import re

LAMP_MODEL = re.compile(r'(work|pole|shop|flood|street|set)_?light(?!_?(strip|arrow|sign))|lamp', re.IGNORECASE)
MAX_AIM_Y = 0.1             # spots aimed higher than this (aim axis y, native Y-up) are uplights, not lamps
LAMP_DISTANCE = 3.5          # a spot this close to a lamp instance (native units) belongs to it
SEGMENTS = 24                # sides of a cone
RINGS = 6                    # rings along a cone, the apex ring excluded
MAX_LENGTH = 14.0            # beams are cut to this length so long ranges do not glow against the open sky
BEAM_ANGLE_SCALE = 0.5       # beam half angle over the light's mean half angle (a fit to one in-game frame, not decoded)
BEAM_GAIN = 0.20             # display-space strength of the brightest beam texel (a visual choice)


def is_lamp_model(path):
    """Whether a model path names a lamp: the basename matches ``LAMP_MODEL``."""
    return LAMP_MODEL.search(path.replace('\\', '/').rsplit('/', 1)[-1]) is not None


def lamp_spots(spots, lamp_positions, distance=LAMP_DISTANCE):
    """The spots with a lamp instance within ``distance`` of their position: ``lamp_positions`` is an (n, 3) array of
    native instance positions."""
    import numpy as np
    positions = np.asarray(lamp_positions, np.float64).reshape(-1, 3)
    if not len(positions):
        return []
    return [spot for spot in spots
            if spot['axis'][1] <= MAX_AIM_Y and float(np.min(np.linalg.norm(positions - np.asarray(spot['position'], np.float64), axis=1))) <= distance]


def _basis(axis):
    """Two unit vectors perpendicular to the unit ``axis`` and to each other."""
    import numpy as np
    axis = np.asarray(axis, np.float64)
    helper = np.array([0.0, 1.0, 0.0]) if abs(axis[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(axis, u)


def beam_mesh(spots, gain=BEAM_GAIN):
    """Triangle mesh of the cones of ``spots`` in native space: (positions (n, 3), normals (n, 3), t (n,), colours (n, 3),
    indices (m,)) as float32 and uint32 arrays. ``t`` is 0 at the apex and 1 at the base ring; a vertex colour is the
    light colour scaled to its brightest channel 1. The cone is as long as the light's range with the half angle of the
    mean cone angle; it is open at the base."""
    import numpy as np
    positions, normals, ts, colours, indices = [], [], [], [], []
    for spot in spots:
        axis = np.asarray(spot['axis'], np.float64)
        axis /= np.linalg.norm(axis)
        half = math.radians(min(max(sum(spot['half_angle_degrees']) / 2.0 * BEAM_ANGLE_SCALE, 1.0), 85.0))
        length = min(float(spot['radius']), MAX_LENGTH)
        u, v = _basis(axis)
        colour = np.asarray(spot['colour'], np.float64)
        colour = colour / max(float(colour.max()), 1e-6) * gain
        base = len(positions)
        for ring in range(RINGS + 1):
            t = ring / RINGS
            for k in range(SEGMENTS):
                phi = 2.0 * math.pi * k / SEGMENTS
                radial = math.cos(phi) * u + math.sin(phi) * v
                positions.append(np.asarray(spot['position'], np.float64) + axis * length * t
                                 + radial * length * t * math.tan(half))
                normals.append(radial * math.cos(half) - axis * math.sin(half))
                ts.append(t)
                colours.append(colour)
        for ring in range(RINGS):
            for k in range(SEGMENTS):
                a = base + ring * SEGMENTS + k
                b = base + ring * SEGMENTS + (k + 1) % SEGMENTS
                indices.extend((a, b, a + SEGMENTS, b, b + SEGMENTS, a + SEGMENTS))
    return (np.asarray(positions, np.float32).reshape(-1, 3), np.asarray(normals, np.float32).reshape(-1, 3),
            np.asarray(ts, np.float32), np.asarray(colours, np.float32).reshape(-1, 3), np.asarray(indices, np.uint32))


__all__ = ['BEAM_GAIN', 'LAMP_DISTANCE', 'beam_mesh', 'is_lamp_model', 'lamp_spots']
