"""Frame constants read from a shadPS4 RenderDoc capture of one level, by atmosphere (data only, no GL).

The level viewer's lighting, display exposure and fog are runtime values in the game: none of them is stored in
the atmosphere, level or zone data. A capture holds them in the frame constant block (CB0) and the lighting and
composite passes, so the values below replace the viewer's visual calibrations for that level.

Novalis (``CUSA01073`` capture, camera at native (140.07, 63.60, 151.75), the frame's sun ray equals the Novalis
atmosphere's exactly):

- ``exposure``: composite pass buffer 0, dword 1 (the Veldin frame's was 4.5538). It follows the scene's
  brightness (the adapted exposure), so it is one frame's value, not a constant of the level.
- ``key_colour``: lighting pass CB0 dwords 124 to 126, the key light in game scene-linear units (the game's diffuse
  is ``albedo * (key * N.L + irradiance * AO)`` with no 1/pi). The atmosphere's RGB96 (0.70, 0.58, 0.58) is 5.6,
  7.7 and 5.9 times smaller, a different factor per channel, so it is no plain multiplier.
- ``grid_ambient_scale``: the game's screen-space irradiance buffer (lighting pass binding 12) equals the lightgrid
  light sampled at the pixel's position and normal with the atmosphere's own sky-term scale (1.75): least-squares
  ratio 0.92 to 0.97 on all three channels, correlation 0.85 over 6000 pixels. The grid light is therefore in game
  units and the viewer's ambient (divided by pi in its diffuse) is pi times it.
- ``sh_scale``: scale of the probe SH ambient where the grid has no cells, chosen so its up-facing value matches the
  captured irradiance buffer at a world-up normal (0.88, 0.74, 0.64): 1.31, 1.26 and 1.09 per channel, 1.25 used.
- ``shadow_ambient``: 1.0. The game's ambient is not reduced under cast shadow; the earlier 0.05 compensated for a
  sun that was too weak and an ambient that was too strong.
- ``fog``: ``scale`` is the ``-ln(1 - d)`` density scale (all three header knots give 0.0083484), ``exponent`` and
  ``start`` are CB0 dwords 90 and 91, ``colour`` dwords 112 to 114 (HDR, before exposure), ``c88_ratio`` the ratio of
  CB0 dword 88 to the ramp density at the camera height. The ramp dwords 72 to 87 reproduce the header knots and the
  camera height to 1e-7.
- ``glow_amplitude``: CB0 dword 115, which the Veldin frame held at 0.01; here it is close to 1 / exposure.

Evidence and derivation: ``Research/captures/2026-10-23_clank-antenna/NOVALIS_FRAME_CONSTANTS.md`` (local).
"""
from __future__ import annotations

import math

NOVALIS = 'atmosphere/atm_novalis/atm_novalis.atmosphere'

FRAMES = {
    NOVALIS: dict(
        exposure=3.6097702980041504,
        key_colour=(3.9477932453155518, 4.5084123611450195, 3.3891847133636475),
        grid_ambient_scale=math.pi,
        sh_scale=1.25,
        shadow_ambient=1.0,
        fog=dict(scale=0.008348431, exponent=2.08, start=0.0, colour=(1.3038157224655151, 3.3024353981018066, 4.703320026397705),
                 c88_ratio=1.7281),
        glow_amplitude=0.277088,
    ),
}


def frame(atmosphere):
    """The captured frame values for an atmosphere path (any case), or None."""
    return FRAMES.get((atmosphere or '').lower())


__all__ = ['FRAMES', 'NOVALIS', 'frame']
