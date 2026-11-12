"""Holocard pickup glow: a labelled preview of `pup_holocard_glow.visualeffect` in the model viewer.

The blue glow and soft "fog" around a holocard are not model geometry. The pickup actors (`pup_card_single.actor`,
`pup_card_ryno.actor`, `pup_card_pack.actor`) name the conduit `pup_holocard_vfx`, and that conduit starts this effect at the
model locator `igLoc_fx_attach` (read from the actor and conduit strings; the event wiring itself is not parsed). The single
card, the RYNO card and the card pack all have that locator, at the card's centre (joint 0, 1.195 u up) with the card's
15-degree tilt in its rotation rows.

The effect has four components: c0 kind 5 (material hash 0xa232b10838ce15f0, not in the archive), c1 the
`gbl_ethereal_glow` emitter, c2 the `pup_holocard_glow` quad and c3 the `gbl_glow_sparkle_01` emitter. All three drawn parts
are additive (SRC_ALPHA / ONE), depth-tested without depth write and unlit (instance flag 0x20 clear, L = 1). Their pixel
programs share one output: rgb = 2A m C (1 + L), alpha = sat(a m), discard where m <= 0.001 or m < 4/255, and each has its
own mask m:
- the glow quad (runtime program cb954073): uv-centred polar coordinates sample `fx_noise_holes_01` at
  (atan2(v - 0.5, u - 0.5) / 2 pi, r - 0.1 CB0[99]) (rings that scroll outward with time); m = sat(f (g.r + 0.5 g.g
  sat(1.1111 n - 0.1111))), g the `pup_holocard_glow_c` texel, f = 1 - |1.001 - sat(N.E)| with N the interpolated vertex
  normal. The quad is 1.8316 u (slot b4) square, held in the effect's local XY plane facing +Z (byte +0x101 = 1), and its
  corner normals lean 45 degrees outward (normalize(Z + the corner's direction), captured exactly), so the rim fades;
- the ethereal cloud (06ceb217): m = sat(`smoke_ethereal_01`.r x s), s = sat(5 (D - d) / size), the soft-particle fade
  against the scene depth D (k = 5 is the material constant; the size is attribute4.x, captured equal to the quad size).
  The card itself is drawn forward without depth write (captured, event 2615), so D is the scene behind the card (9.3 u
  behind a cloud at 6.9 u in the capture: s = 1 over the card). The model viewer has no scene behind the card, so s = 1;
- the sparkles (59399868): m = sat(`glow_sparkle_01`.r).
C = slot b2 x b5 of the quad = 5 x (0, 0.4969, 1); the particles carry the same colour (captured).

Captured (Research/captures/2026-11-12_holocard-glow/README.md, local): the three programs evaluated on the captured inputs
reproduce the frame's change at correlation 0.99994 (glow, 21 416 pixels), 0.9999 (cloud, 25 861 pixels) and 0.97
(sparkles, 217 pixels, implicit-LOD residual); the four textures match the frame's images byte for byte; the quad's size,
plane and corner normals; the render state.

Presentation values: CB0[115] = idle_effect.AMPLITUDE (0.277, the viewer's shared value tied to its exposure; this frame had
0.0469), the frame time CB0[99] = 0 (the rings are still), fog off, the cloud's soft fade 1 (no scene behind the card).

INFERRED STAND-INS (named in PREVIEW_LIMITATIONS and docs/viewer.md):
- the particles are the capture's still frame, not the emitters' spawn rules (not decoded): two cloud puffs at the card
  centre (sizes 1.9502 and 1.4756 u, alphas 23/255 and 248/255, turned 101.5 and 122.1 degrees about the view axis) and the 17
  sparkles at their captured offsets in the effect frame (0.03 to 0.10 u);
- the camera-distance fade sat((60 - d) / 25): 60 is component +0xD8, the 25 u range is the health crate's stand-in;
- the glow quad and the cloud move 0.2 u toward the camera: their component +0x7C is -0.2, read as the health crate's
  flare offset (decoded there, idle_effect.FLARE_PULL). Here the capture has the quad about 0.1 u in front of the card's
  forward layer (event 2615) and the clouds at the quad's centre, consistent with a pull, magnitude not checked;
- c0 (kind 5) and the faint 864-index strip drawn after the cloud (program b6f67d16) are not drawn;
- no depth test: the game depth-tests these draws, but its card is drawn without depth write (captured, event 2615), so the
  glow adds over the card; the viewer's card writes depth and would hide the ring that sits on its frame, so the quads draw over
  the model (on the card pack the tin, whose depth state is not captured, does not hide them either).
"""
from __future__ import annotations

import math

from rc2016.viewer.idle_effect import AMPLITUDE, _quad, _transform, native_to_display

HOLOCARD_GLOW, ETHEREAL_CLOUD, SPARKLE = 7, 8, 9           # shading.EFFECT_FRAGMENT uEffectKind
PROGRAMS = {HOLOCARD_GLOW: 'cb954073', ETHEREAL_CLOUD: '06ceb217', SPARKLE: '59399868'}   # runtime programs in the capture

EFFECT_PATH = 'visualeffect/equipment/pickup/pup_holocard_glow/pup_holocard_glow.visualeffect'
CONDUIT_PATH = 'conduit/equipment/pickup/pup_holocard_vfx/pup_holocard_vfx.conduit'
LOCATOR = 'igLoc_fx_attach'
# Model file stem -> the actor that names CONDUIT_PATH (decoded strings).
CARDS = {'pup_card_single': 'equipment/pickup/pup_card_pack/pup_card_single.actor',
         'pup_card_ryno': 'equipment/pickup/pup_card_pack/pup_card_ryno.actor',
         'pup_card_pack': 'equipment/pickup/pup_card_pack/pup_card_pack.actor'}
GLOW_MATERIAL = 'pup_holocard_glow'
# Image roles (the graphs' named textures, matched to the captured images by top-mip bytes). The glow texture is BC7 sRGB.
TEXTURES = dict(holocard_glow='textures/visualeffect/unique/pup_holocard_glow/pup_holocard_glow_c.texture',
                holocard_noise='textures/visualeffect/noise/fx_noise_holes_01.texture',
                ethereal='textures/visualeffect/smoke/smoke_ethereal_01/smoke_ethereal_01.texture',
                sparkle='textures/visualeffect/glow/glow_sparkle_01/glow_sparkle_01.texture')
SRGB_ROLES = frozenset({'holocard_glow'})

COMPONENT_POSITION, COMPONENT_MATERIAL, COMPONENT_FIXED = 0x24, 0x4C, 0x101
COMPONENT_PULL = 0x7C   # float: minus the distance the quad's centre moves toward the camera (idle_effect.FLARE_PULL)
DISCARD = 4.0 / 255.0                                     # attribute0.z of all three draws (captured)
CORNER_LEAN = 1.0                                         # corner normal = normalize(Z + LEAN x corner direction): 45 deg (captured)
CLOUD_COMPONENT = 1                                       # the gbl_ethereal_glow emitter
CLOUD_SOFT_K = 5.0                                        # gbl_ethereal_glow material constant CB[0] (captured); s = 1 here
FADE_DISTANCE, FADE_RANGE = 60.0, 25.0                    # +0xD8 = 60 on every component; range as idle_effect (inferred)
FRAME_TIME = 0.0                                          # CB0[99] (presentation: the rings are still)
DEFAULT_SIZE, DEFAULT_COLOUR = 1.8316, (0.0, 2.4846644, 5.0)
# The captured still frame of the two emitters (INFERRED stand-in for their spawn rules): cloud (size, alpha, view angle in
# degrees) at the effect origin, and sparkles (offset in the effect frame (x, y, z), size), all alpha 1.
CLOUDS = ((1.9502, 23.0 / 255.0, 101.5), (1.4756, 248.0 / 255.0, 122.1))
SPARKLES = (((-0.3315, -0.5905, -1.0226), 0.0684), ((0.5315, 0.04, -0.3794), 0.0699), ((-0.5566, -0.2208, -0.6733), 0.0382),
            ((-0.1552, -0.0431, 0.5713), 0.0906), ((-0.5603, -0.1511, -0.7167), 0.0287), ((-0.904, -0.2129, -0.3747), 0.0289),
            ((0.1354, 0.0063, 0.5397), 0.0965), ((-0.2038, 0.5409, -0.3852), 0.0473), ((-0.1274, -0.797, -0.4055), 0.0334),
            ((-0.4934, -0.0941, -0.4363), 0.09), ((-0.4832, -0.3038, -0.6354), 0.0858), ((-0.4605, -0.2332, -0.2509), 0.0663),
            ((-0.13, -0.1721, 0.241), 0.0712), ((-0.5437, 0.0765, -0.7155), 0.0622), ((0.0098, -0.2114, -0.3022), 0.0474),
            ((-0.3526, 0.001, -0.0075), 0.0625), ((-0.1044, -0.1272, -0.1656), 0.0859))

PREVIEW_LIMITATIONS = (
    'Holocard glow preview at igLoc_fx_attach: decoded glow quad, cloud and sparkle programs (checked against a holocard '
    'capture), quad size, plane and corner normals, colour, additive blend and discard; game_equivalence false.',
    'Presentation values: CB0[115] = 0.277 (the viewer\'s shared amplitude; the capture had 0.0469), frame time 0 (the rings '
    'are still), fog off, unlit particles (as captured), the cloud\'s soft-depth fade 1 (no scene behind the card).',
    'Inferred stand-ins: the particles are the captured still frame (two cloud puffs, 17 sparkles), not the emitters\' spawn '
    'rules; the distance fade sat((60 - d) / 25); the glow and cloud 0.2 u toward the camera (+0x7C read as on the '
    'health crate); component 0 (kind 5) and the faint strip program b6f67d16 are not drawn; no depth test against the '
    'model (the game draws the card without depth write, captured).',
)


def _model_stem(model_path):
    name = str(model_path or '').replace('\\', '/').rsplit('/', 1)[-1]
    return name[:-6].lower() if name.lower().endswith('.model') else name.lower()


def is_holocard(model_path):
    return _model_stem(model_path) in CARDS


def decode_glow(archive, effect_path=EFFECT_PATH):
    """The glow quad component of the holocard effect: ``component``, ``offset``, ``size`` (b4), ``colour`` (b2 x b5),
    ``alpha`` (b6[0]), ``fixed`` (byte +0x101), ``pull`` (-(+0x7C): 0.2 toward the camera) and ``pulls`` (that value for
    every component, by index); None when the effect has no such component."""
    import re
    import struct
    from rc2016.levels.effects import COMPONENT_SIZE, COMPONENTS, decode_effect, parameter_at
    from rc2016.levels.scene import asset_bytes
    from rc2016.levels.zone_format import path_crc64, sections
    data = asset_bytes(archive, effect_path)
    effect = decode_effect(data)
    offset = sections(data)[COMPONENTS][0]
    materials = {}
    for m in re.finditer(rb'material[\\/][ -~]+?\.material', data):
        path = m.group().decode().replace('\\', '/').lower()
        materials[path_crc64(path)] = path
    parameters = effect['parameters']
    pulls = [-struct.unpack_from('<f', data, offset + n * COMPONENT_SIZE + COMPONENT_PULL)[0] for n in range(len(effect['components']))]
    for n, component in enumerate(effect['components']):
        record = data[offset + n * COMPONENT_SIZE:offset + (n + 1) * COMPONENT_SIZE]
        material = materials.get(struct.unpack_from('<Q', record, COMPONENT_MATERIAL)[0], '')
        if component['kind'] != 0 or not material.endswith('/' + GLOW_MATERIAL + '.material'):
            continue

        def slot(k):
            index = component['slots'][k]
            return parameter_at(parameters[index], 1.0) if index < len(parameters) else None
        size, a, b = slot(4), slot(2), slot(5)
        return dict(component=n, offset=struct.unpack_from('<3f', record, COMPONENT_POSITION), size=float(size[0]),
                    colour=tuple(float(x * y) for x, y in zip(a, b)), alpha=float(slot(6)[0]), fixed=record[COMPONENT_FIXED] == 1,
                    pull=pulls[n], pulls=pulls)
    return None


def holocard_preview(archive, model_path, data, locators):
    """The labelled glow preview of a holocard model, else None: ``origin`` and ``rows`` (the native locator frame through
    its joint), the decoded ``glow``, the captured ``clouds`` and ``sparkles``, and the ``texture_paths`` to load."""
    if not is_holocard(model_path):
        return None
    from rc2016.viewer.holocon_effect import joint_rest_worlds, locator_frame
    locator = next((row for row in locators or () if row.get('name') == LOCATOR), None)
    if locator is None:
        return None
    joint_world = None
    if locator.get('joint') is not None:   # joint 0 carries the card centre (1.195 u up)
        worlds = joint_rest_worlds(data)
        if locator['joint'] < len(worlds):
            joint_world = worlds[locator['joint']]
    origin, rows = locator_frame(locator, joint_world)
    glow = decode_glow(archive)
    if glow is None:
        return None
    colour = glow['colour']
    cloud_pull = glow['pulls'][CLOUD_COMPONENT] if CLOUD_COMPONENT < len(glow['pulls']) else 0.0
    clouds = [dict(component=CLOUD_COMPONENT, size=size, alpha=alpha, angle=math.radians(angle), colour=colour, pull=cloud_pull)
              for size, alpha, angle in CLOUDS]
    sparkles = [dict(component=3, offset=offset, size=size, alpha=1.0, colour=colour) for offset, size in SPARKLES]
    return dict(family='holocard', effect=EFFECT_PATH, conduit=CONDUIT_PATH, locator=LOCATOR, origin=origin, rows=rows,
                glow=glow, clouds=clouds, sparkles=sparkles, amplitude=AMPLITUDE, frame_time=FRAME_TIME, texture_paths=dict(TEXTURES), srgb_roles=sorted(SRGB_ROLES),
                mipmap_roles=sorted(TEXTURES), programs=dict(PROGRAMS), limitations=list(PREVIEW_LIMITATIONS),
                game_equivalence=False)


def _rows_apply(rows, v):
    return tuple(sum(v[i] * rows[i][k] for i in range(3)) for k in range(3))


def effect_point(preview, local):
    """Native model-space position of an effect-local point."""
    return tuple(o + d for o, d in zip(preview['origin'], _rows_apply(preview['rows'], local)))


def _normalise(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 0.0 else (0.0, 0.0, 0.0)


def glow_corners(preview):
    """The glow quad's four corners in effect space: (local xy, uv with v = 0 at the top, local corner normal)."""
    half = preview['glow']['size'] * 0.5
    ox, oy, oz = preview['glow']['offset']
    rows = []
    for (x, y), uv in (((-half, -half), (0.0, 1.0)), ((half, -half), (1.0, 1.0)), ((half, half), (1.0, 0.0)), ((-half, half), (0.0, 0.0))):
        lean = _normalise((x, y, 0.0))
        rows.append(((ox + x, oy + y, oz), uv, _normalise((CORNER_LEAN * lean[0], CORNER_LEAN * lean[1], 1.0))))
    return rows


def distance_fade(distance):
    if distance > FADE_DISTANCE:
        return None
    return max(0.0, min(1.0, (FADE_DISTANCE - distance) / FADE_RANGE))


def billboards(preview, modelview):
    """View-space quads for one frame in the captured draw order (glow quad, cloud, sparkles), as holocon_effect.billboards,
    plus ``additive`` (SRC_ALPHA / ONE), ``normals`` (the glow quad's view-space corner normals) and ``face`` (its
    view-space +Z, which decides the front face without depending on the target's winding). No light: returns (quads, None)."""
    view = lambda p: _transform(modelview, native_to_display(p))   # noqa: E731
    centre = view(preview['origin'])
    fade = distance_fade(math.sqrt(sum(v * v for v in centre)))
    if fade is None or fade <= 0.0:
        return [], None
    quads = []
    glow = preview['glow']
    corners, normals = [], []
    for local, uv, normal in glow_corners(preview):
        point = effect_point(preview, local)
        p = view(point)
        tip = view(tuple(a + b for a, b in zip(point, _rows_apply(preview['rows'], normal))))
        corners.append((p, uv))
        normals.append(_normalise(tuple(b - a for a, b in zip(p, tip))))
    glow_centre = view(effect_point(preview, glow['offset']))
    front = view(tuple(a + b for a, b in zip(effect_point(preview, glow['offset']), _rows_apply(preview['rows'], (0.0, 0.0, 1.0)))))
    face = _normalise(tuple(b - a for a, b in zip(glow_centre, front)))   # the game flips N on back faces (IsFrontFace)
    toward = _normalise(tuple(-v for v in glow_centre))
    shift = tuple(glow.get('pull', 0.0) * t for t in toward)              # +0x7C: the centre moves toward the camera
    corners = [(tuple(a + b for a, b in zip(p, shift)), uv) for p, uv in corners]
    glow_centre = tuple(a + b for a, b in zip(glow_centre, shift))
    quads.append(dict(kind=HOLOCARD_GLOW, component=glow['component'], corners=corners, normals=normals, face=face, colour=glow['colour'],
                      alpha=glow['alpha'] * fade, discard=DISCARD, depth_test=False, cull=False, additive=True,
                      depth=-glow_centre[2]))
    origin = view(effect_point(preview, glow['offset']))
    for cloud in preview['clouds']:
        at = tuple(o + cloud.get('pull', 0.0) * t for o, t in zip(origin, toward))
        quads.append(dict(kind=ETHEREAL_CLOUD, component=cloud['component'], corners=_quad(at, (cloud['size'],) * 2, cloud['angle']),
                          colour=cloud['colour'], alpha=cloud['alpha'] * fade, discard=DISCARD, depth_test=False, cull=False,
                          additive=True, depth=-glow_centre[2]))
    for sparkle in preview['sparkles']:
        p = view(effect_point(preview, sparkle['offset']))
        quads.append(dict(kind=SPARKLE, component=sparkle['component'], corners=_quad(p, (sparkle['size'],) * 2),
                          colour=sparkle['colour'], alpha=sparkle['alpha'] * fade, discard=DISCARD, depth_test=False,
                          cull=False, additive=True, depth=-p[2]))
    return quads, None


__all__ = ['CARDS', 'EFFECT_PATH', 'ETHEREAL_CLOUD', 'HOLOCARD_GLOW', 'LOCATOR', 'SPARKLE', 'billboards', 'decode_glow',
           'effect_point', 'glow_corners', 'holocard_preview', 'is_holocard']
