"""Health (nanotech) crate idle effect: a labelled preview of `pup_health_crate_idle.visualeffect` in the model viewer.

The crate's actor conduit starts this effect attached to the model locator `igLoc_health_FX` (read from the conduit's
strings; the event wiring itself is inferred). The effect is ten components: one light (kind 2), seven sprites (kind 0)
and two particle emitters (kind 1). This module turns the decoded component values into camera-facing quads and a point
light for ModelViewport; the quads are drawn with the decoded sprite pixel programs (shading.EFFECT_FRAGMENT).

Decoded (game data, eboot code or pixel-program IR) and captured on the crate's own draws in three RenderDoc captures:
- placement at the locator (0, 0.5003, 0) in native Y-up model space (captured: crate position + 0.5 on 5 crates);
- per component the particle colour P2 = slot b2 x slot b5, the full quad size = b4 and the alpha / discard bytes = b6
  (component 5 captured exactly: 3.959 x 0.2189 u, alpha 18/255), the five pixel programs and their masks;
- the alpha-over blend (SRC_ALPHA / ONE_MINUS_SRC_ALPHA), depth test without depth write, depth test off for the flares,
  the flares' centre 2.5 u toward the camera (component +0x7C = -2.5) and the draw order;
- the camera-distance fade sat((100 - d) / 25) on every alpha and on the light, with no effect beyond 100 u;
- the light's hue (0.447, 0.786, 1.0) = b5 of component 0, and its colour (0.100125, 0.176066, 0.223995) in the game's
  light-record units at fade 1 (Rilgar capture);
- the emitter curves: orb size 0.3906 -> 0.0842 and ring size 0.25 -> 0 over the particle's normalised age t, alpha 1 - t,
  the ring discard threshold t, and the emitter colour 10 x (0.1505, 0.6939, 1.0).

Presentation values (chosen, not the game's per-frame values): CB0[115] (the sprites' amplitude A) is the 0.277088 captured
in the Novalis frame (0.082 and 0.442 were captured elsewhere). It is consistent with the Gaspar streak, which fixes only the
product of Gaspar's exposure E and A: E x A ~ 1.18 (0.82..1.26) under the Veldin tone curve and an identity grade
(captured-derived; the viewer's 4.554 x 0.277 = 1.26); A alone is unknown. Also: fog transmittance 1; the lit particle path
off (L = 1); the soft-particle and shockwave depth terms 1 (the model viewer has no scene depth); the shockwave instance
colour rec[44..46] = 1; the frame time CB0[99] = 0.

The sprites alpha-blend in HDR with the crate and its panes and are tonemapped once, as in the game's scene target
(ModelViewport._hdr_begin, shading.HDR_RESOLVE_FRAGMENT): the Gaspar streak matches only that order.

INFERRED STAND-INS (not decoded; every one is named in PREVIEW_LIMITATIONS and docs/viewer.md):
- SPAWN_RATE, ORB_LIFE, RING_LIFE and DRIFT_SPEED of the two emitters, fitted to the captured particle counts and ages;
- SPIN_RATE of the spinning glow (slot b3 = 360, read as degrees per second);
- SNAPSHOT_AGE, the effect age of the still frame, and the random spawn directions (a fixed seed);
- LIGHT_RADIUS and the falloff (1 - d / R)^4, and LIGHT_UNIT_SCALE from light-record units to viewer units.

Round flares 1 to 4 are drawn at close range: the Gaspar streak (d = 8 u) is components 3 + 1 + 5 (captured-derived from
pixels); what removes them in the far captures (60+ u) is unknown. Not reproduced: that gate, the per-frame slots b0 / b3
beyond the spin and the break effect. Bloom is drawn by the viewport (rc2016/viewer/bloom.py).
Research/captures/2026-11-05_gaspar-crates/nanotech_verify/README.md, nanotech_full/README.md and nanotech_glow/README.md
(local).
"""
from __future__ import annotations

import math
import random

EFFECT_PATH = 'visualeffect/equipment/pickup/pup_health_crate_idle/pup_health_crate_idle.visualeffect'
CONDUIT_PATH = 'conduit/environment/global/gameplay/gbl_gp_crate_health_vfx/gbl_gp_crate_health_vfx.conduit'
LOCATOR = 'igLoc_health_FX'

# Sprite pixel programs (shading.EFFECT_FRAGMENT uEffectKind) and their materials.
GLOW_SOFT, FLARE_ROUND, FLARE_SQUARE, GLOW_SPIN, SHOCKWAVE = range(5)
PROGRAMS = {GLOW_SOFT: 'EBC94A93', FLARE_ROUND: '5FE9EF08', FLARE_SQUARE: 'B1310A54', GLOW_SPIN: '41E80E53',
            SHOCKWAVE: '094D0CAE'}
MATERIALS = {GLOW_SOFT: 'gbl_glow_simp_soft', FLARE_ROUND: 'gbl_anamorphic_flare_round_soft',
             FLARE_SQUARE: 'gbl_anamorphic_flare_square_soft', GLOW_SPIN: 'gbl_glow_spin_01',
             SHOCKWAVE: 'gbl_shockwave_static_01'}
# Material constant blocks (graph defaults CB5[0], CB5[1]): the mask weight added to L and the soft-depth scale.
MASK_WEIGHT = {GLOW_SOFT: 0.44999998807907104, FLARE_SQUARE: 1.0}
TEXTURES = dict(noise='textures/visualeffect/test/noise_test/fx_graphic_noise_01.texture',
                spin='textures/visualeffect/glow/glow_spin_01/glow_spin_01.texture',
                ring='textures/visualeffect/shockwave/shockwave_static_01/shockwave_static_01_c.texture')

FADE_DISTANCE, FADE_RANGE = 100.0, 25.0   # alpha x sat((100 - d) / 25); 100 is component +0xD8 (decoded), 25 inferred as sqrt(625)
FLARE_PULL = 2.5                           # component +0x7C = -2.5: the flare centre moves this far toward the camera
# CB0[115], Novalis capture; consistent with the Gaspar streak, which fixes E x A ~ 1.18 (0.82..1.26) under the Veldin tone
# curve and an identity grade (captured-derived; A alone unknown).
AMPLITUDE = 0.277088


def _byte(value):
    """b6 values reach the particle as bytes (component 5: 0.07407 evaluated, 18/255 captured)."""
    return math.floor(max(0.0, min(1.0, value)) * 255.0) / 255.0


_TEAL_FLARE = (0.27992, 0.69051, 1.0)      # b5 of components 1 to 5
_TEAL_GLOW = (0.31852, 0.74667, 1.0)       # b5 of components 6 and 9
_EMITTER_TEAL = (0.15049, 0.69387, 1.0)    # emitter list index 10 (p10)


def _times(scale, rgb):
    return tuple(scale * c for c in rgb)


# One row per sprite component, in the captured draw order: kind, size (full w x h), P2, alpha a, discard threshold, and
# whether the quad is a flare (centre pulled toward the camera, depth test off). Values: vfx_params_idle.log (decoded);
# rc2016.levels.effects.spawn_terms reads the same sizes, colours and alphas from the effect file.
SPRITES = (
    dict(component=9, kind=GLOW_SPIN, size=(0.50166, 0.50166), colour=_times(20.0, _TEAL_GLOW), alpha=_byte(1.0),
         discard=_byte(0.00497), flare=False),
    dict(component=6, kind=GLOW_SOFT, size=(0.85519, 0.85519), colour=_times(10.0, _TEAL_GLOW), alpha=_byte(1.0),
         discard=_byte(0.00497), flare=False),
    dict(component=1, kind=FLARE_ROUND, size=(2.74747, 0.09995), colour=_times(10.0, _TEAL_FLARE), alpha=_byte(0.08078),
         discard=_byte(0.00497), flare=True),
    dict(component=2, kind=FLARE_ROUND, size=(1.45791, 0.3131), colour=_times(10.0, _TEAL_FLARE), alpha=_byte(0.12119),
         discard=_byte(0.00497), flare=True),
    dict(component=3, kind=FLARE_ROUND, size=(5.57576, 0.04986), colour=_times(10.0, _TEAL_FLARE), alpha=_byte(0.05048),
         discard=_byte(0.00497), flare=True),
    dict(component=4, kind=FLARE_ROUND, size=(2.37374, 0.93263), colour=_times(10.0, _TEAL_FLARE), alpha=_byte(0.12793),
         discard=_byte(0.00497), flare=True),
    dict(component=5, kind=FLARE_SQUARE, size=(3.9596, 0.21884), colour=_times(10.0, _TEAL_FLARE), alpha=_byte(0.07407),
         discard=_byte(0.00497), flare=True),
)
# The two kind-1 emitters (orb glows, component 8, and rings, component 7): decoded curves over the normalised age t.
EMITTER_COLOUR = _times(10.0, _EMITTER_TEAL)
ORB_SIZE, RING_SIZE = (0.39058, 0.08417), (0.24998, 0.0)
ORB_ALPHA, RING_ALPHA = (1.0, 0.0), (1.0, 0.0)
ORB_DISCARD, RING_DISCARD = (0.00497, 0.0), (0.00497, 1.0)

# INFERRED STAND-INS (fitted to the captured particles, not decoded).
SPAWN_RATE = 10.0               # particles per second per emitter (list index 4 = 10)
ORB_LIFE = (0.75, 1.0)          # seconds (list index 5: 1.0 - 0.25 rnd)
RING_LIFE = (1.0, 2.0)          # seconds (list index 5: 1.5 - 0.5 rnd)
DRIFT_SPEED = (0.35, 0.5)       # units per second (list index 2: 0.5 - 0.15 rnd)
SPIN_RATE = 360.0               # degrees per second (slot b3 = 360)
SNAPSHOT_AGE = 4.0              # effect age of the still frame, seconds
SNAPSHOT_SEED = 2016
LIGHT_RADIUS = 1.0              # units; the captured record word d56 (0.32..0.35) has no established meaning
LIGHT_UNIT_SCALE = 1.0          # light-record units -> viewer units

LIGHT_HUE = (0.44704, 0.7861, 1.0)                     # b5 of component 0 (decoded)
LIGHT_COLOUR = (0.100125, 0.176066, 0.223995)          # captured at fade 1 (Rilgar)

PREVIEW_LIMITATIONS = (
    'Idle effect preview of pup_health_crate_idle at igLoc_health_FX: decoded sizes, colours, alphas, programs, blend, '
    'flare offset and the distance fade sat((100 - d) / 25); game_equivalence false.',
    'Presentation values: CB0[115] = 0.277088 (Novalis capture; consistent with the Gaspar streak, which fixes E x A ~ 1.18 '
    '(0.82..1.26) under the Veldin tone curve and an identity grade, captured-derived; A alone unknown), fog off, unlit '
    'particles, soft-depth and shockwave depth terms 1, shockwave instance colour 1, frame time 0.',
    'Inferred stand-ins: spawn rate 10/s, orb life 0.75..1.0 s, ring life 1.0..2.0 s, drift 0.35..0.5 u/s, spin 360 deg/s, '
    'a still frame at effect age 4 s with seeded directions, light radius 1 u with (1 - d/R)^4 falloff, light units x 1.',
    'Round flares 1 to 4 are drawn at close range: the Gaspar streak (d = 8 u) is components 3 + 1 + 5 (captured-derived '
    'from pixels); what removes them in the far captures (60+ u) is unknown. Bloom is drawn by the viewport (rc2016/viewer/bloom.py).',
)


def native_to_display(p):
    """Native Y-up model space to the model viewer's display basis (x, -z, y)."""
    return (p[0], -p[2], p[1])


def effect_origin(locators):
    """The native position of the igLoc_health_FX locator, or None when the model has none."""
    for row in locators or ():
        if row.get('name') == LOCATOR:
            return tuple(float(v) for v in row['position'])
    return None


def _lerp(pair, t):
    return pair[0] + (pair[1] - pair[0]) * t


def particles(seed=SNAPSHOT_SEED, age=SNAPSHOT_AGE):
    """The live particles of both emitters at the snapshot (inferred stand-ins): dicts with ``kind`` (GLOW_SOFT for orbs,
    SHOCKWAVE for rings), ``offset`` (native, from the origin), ``size`` (full), ``alpha``, ``discard`` and ``t`` (age over
    life). Orb and ring of one spawn share its direction, as captured (every orb has a ring at its centre)."""
    rng = random.Random(seed)
    result = []
    count = int(math.floor(age * SPAWN_RATE)) + 1
    for n in range(count):
        z = rng.uniform(-1.0, 1.0); phi = rng.uniform(0.0, 2.0 * math.pi); r = math.sqrt(max(0.0, 1.0 - z * z))
        direction = (r * math.cos(phi), z, r * math.sin(phi))
        speed = rng.uniform(*DRIFT_SPEED)
        lives = dict(orb=rng.uniform(*ORB_LIFE), ring=rng.uniform(*RING_LIFE))
        spawned = n / SPAWN_RATE
        for name, kind, size, alpha, discard in (('ring', SHOCKWAVE, RING_SIZE, RING_ALPHA, RING_DISCARD),
                                                 ('orb', GLOW_SOFT, ORB_SIZE, ORB_ALPHA, ORB_DISCARD)):
            elapsed = age - spawned
            if not 0.0 <= elapsed < lives[name]:
                continue
            t = elapsed / lives[name]
            offset = tuple(d * speed * elapsed for d in direction)
            result.append(dict(kind=kind, emitter=name, component=7 if name == 'ring' else 8, offset=offset,
                               size=(_lerp(size, t),) * 2, alpha=_lerp(alpha, t), discard=_lerp(discard, t), t=t,
                               colour=EMITTER_COLOUR))
    return result


def distance_fade(distance):
    """sat((100 - d) / 25), None beyond 100 (the effect is not drawn)."""
    if distance > FADE_DISTANCE:
        return None
    return max(0.0, min(1.0, (FADE_DISTANCE - distance) / FADE_RANGE))


def effect_preview(locators):
    """The preview description for a model with the igLoc_health_FX locator, else None: ``origin`` (native), ``sprites``,
    ``particles``, ``light`` and ``limitations``. Textures are attached by the loader (``textures``: role -> path)."""
    origin = effect_origin(locators)
    if origin is None:
        return None
    return dict(effect=EFFECT_PATH, locator=LOCATOR, origin=origin, sprites=[dict(s) for s in SPRITES],
                particles=particles(), amplitude=AMPLITUDE, texture_paths=dict(TEXTURES),
                light=dict(hue=LIGHT_HUE, colour=LIGHT_COLOUR, radius=LIGHT_RADIUS, unit_scale=LIGHT_UNIT_SCALE),
                programs=dict(PROGRAMS), limitations=list(PREVIEW_LIMITATIONS), game_equivalence=False)


def _transform(m, p):
    """Column-major 4x4 (GL) times point."""
    return tuple(m[i] * p[0] + m[4 + i] * p[1] + m[8 + i] * p[2] + m[12 + i] for i in range(3))


def _quad(centre, size, angle=0.0):
    """Four view-space corners of a camera-facing quad of full size (w, h), rotated about the view axis, with UVs."""
    hw, hh = size[0] * 0.5, size[1] * 0.5
    c, s = math.cos(angle), math.sin(angle)
    corners = []
    for (x, y), uv in (((-hw, -hh), (0.0, 0.0)), ((hw, -hh), (1.0, 0.0)), ((hw, hh), (1.0, 1.0)), ((-hw, hh), (0.0, 1.0))):
        corners.append(((centre[0] + c * x - s * y, centre[1] + s * x + c * y, centre[2]), uv))
    return corners


def billboards(preview, modelview):
    """View-space quads for one frame, in draw order: dicts with ``kind``, ``corners`` ((xyz, uv) x 4), ``colour``,
    ``alpha`` (with the distance fade), ``discard``, ``depth_test`` and ``component``. Empty beyond 100 units. Also returns
    the light as (view-space position, colour with the fade) or None."""
    origin = native_to_display(preview['origin'])
    centre = _transform(modelview, origin)
    distance = math.sqrt(sum(v * v for v in centre))
    fade = distance_fade(distance)
    if fade is None or fade <= 0.0:
        return [], None
    toward = tuple(-v / distance for v in centre) if distance > 0.0 else (0.0, 0.0, 1.0)
    quads = []

    def add(row, position, angle=0.0, depth_test=True):
        quads.append(dict(kind=row['kind'], component=row['component'], corners=_quad(position, row['size'], angle),
                          colour=tuple(row['colour']), alpha=row['alpha'] * fade, discard=row['discard'],
                          depth_test=depth_test, depth=-position[2]))

    sprites = preview['sprites']
    for row in sprites:
        if row['kind'] == GLOW_SPIN:
            add(row, centre, math.radians(SPIN_RATE * SNAPSHOT_AGE))
        elif row['kind'] == GLOW_SOFT:
            add(row, centre)
    for emitter in ('ring', 'orb'):   # captured order: rings, then orb glows; farthest first within each
        rows = [p for p in preview['particles'] if p['emitter'] == emitter]
        placed = [(row, _transform(modelview, native_to_display(tuple(o + d for o, d in zip(preview['origin'], row['offset'])))))
                  for row in rows]
        for row, position in sorted(placed, key=lambda item: item[1][2]):
            add(row, position)
    pulled = tuple(c + FLARE_PULL * t for c, t in zip(centre, toward))
    for row in sprites:
        if row['flare']:
            add(row, pulled, depth_test=False)
    light = preview.get('light')
    if light:
        scale = light['unit_scale'] * fade
        light = (centre, tuple(c * scale for c in light['colour']), light['radius'])
    return quads, light


__all__ = ['EFFECT_PATH', 'LOCATOR', 'PROGRAMS', 'SPRITES', 'billboards', 'distance_fade', 'effect_origin',
           'effect_preview', 'native_to_display', 'particles']
