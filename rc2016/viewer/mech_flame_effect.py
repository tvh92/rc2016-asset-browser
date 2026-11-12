"""Nefarious mech rocket flame: a labelled preview of `enm_nefarious_mech_rocket_flame.visualeffect` in the model viewer.

The blue flame under Nefarious's mech is not model geometry. The mech's effect conduit (`enm_nefarious_mech_fx`, event
"New Thruster VFX" / `turnOnThrusters`) starts this effect at the model locator `igLoc_thruster` (joint 2, at the model origin
in the rest pose, its local Z pointing down). The effect has five components: 0 and 4 are lights (kind 2, blue
(0.047, 0.328, 1.0)) and 2 and 3 are mesh components (kind 12) that draw `enm_nefarious_mech_rocket_flame.model`, a closed
21-ring flame shape (local z from -0.12 to 1.59: widest near z = 0.4, game v = 0 at the z = -0.12 end by the nozzle and 1 at
the z = 1.59 tip) with the material of the same name.

Each mesh component is drawn at the locator, moved by its component offset (+0x24: z -0.15 and -0.35, so down from the
locator is up the mech), turned by its rotation rows (identity) and a spin about local Z, and scaled by its slot b4. Its
pixel program (graph enm_nefarious_mech_rocket_flame, archive 0x61e4c390; shadPS4 labels the same code fde05ee2):
- n = noise.r(u, 0.5 v - 2 t) with `hydro_noise_flame_03_sm` (BC7, linear, wrap) and t = CB0[99];
- s = 1 - v; q = sat((s - 0.15)^4) + (0.3 n - 0.15) s; m = sat(q); discard m < attribute0.z (4/255);
- rgb = 93 A L (q^2, |q|^0.75, |q|^0.01) (+ fog, off), alpha = sat(a m), blended SRC_ALPHA / ONE_MINUS_SRC_ALPHA, depth
  tested without depth write, no culling. L is the component's slot b2 x b5 (2.5 and 1.0), a the slot b6[0] clamped to 1 (1.0 and 2.0 stored; captured
  1.0 and 0.9956).
The flame is blue because q < 1 and the exponents favour blue.

Captured (Research/captures/2026-11-12_nefarious-mech/README.md, local): the program evaluated on the captured inputs
reproduces the frame's change at correlation 0.9992 (837 pixels, median error 2.5 %); the two instances' transforms in the
mech's model space are exactly the locator frame, the component offsets, the slot b4 scales with a random fraction of their
spread, and a spin about local Z (fit residual 0); L, a, the discard and the render state.

Presentation values: CB0[115] = idle_effect.AMPLITUDE (0.277, the viewer's shared value; this frame had 0.2158), the frame
time CB0[99] = 0 (the noise is still), fog off.

INFERRED STAND-INS (named in PREVIEW_LIMITATIONS and docs/viewer.md):
- the random terms are the capture's still frame: the scale is b4 + 0.964 x its spread (both instances drew 0.964) and the
  spins about local Z are -0.3 and -71.7 degrees (slot b3, 2500 and 3000 with a random spread, is read as a spin rate but not
  played);
- the flame stays at the locator's rest pose (the viewer's effect previews do not follow the animated joints; in the capture
  the posed locator was 0.217 u above its rest position);
- the two blue lights (components 0 and 4) are not added to the viewer's lighting.
"""
from __future__ import annotations

import math

from rc2016.viewer.idle_effect import AMPLITUDE, _transform, native_to_display

MECH_FLAME = 10                                            # shading.EFFECT_FRAGMENT uEffectKind
PROGRAMS = {MECH_FLAME: '61e4c390'}                        # archive program (runtime label fde05ee2 in the capture)

EFFECT_PATH = 'visualeffect/character/enemy/enm_nefarious_mech_rocket_flame/enm_nefarious_mech_rocket_flame.visualeffect'
FLAME_MODEL = 'visualeffect/character/enemy/enm_nefarious_mech_rocket_flame/enm_nefarious_mech_rocket_flame.model'
CONDUIT_PATH = 'conduit/character/enemy/enm_nefarious_mech_fx/enm_nefarious_mech_fx.conduit'
LOCATOR = 'igLoc_thruster'
MODELS = frozenset({'enm_nefarious_mech', 'enm_nefarious_mech_damaged', 'enm_nefarious_mech_damaged_dome'})
TEXTURES = dict(mech_flame_noise='textures/visualeffect/noise/hydro_noise_flame_03_sm.texture')

MESH_KIND = 12                                             # effect component kind of a mesh component
COMPONENT_ROTATION, COMPONENT_POSITION = 0x00, 0x24        # 3 x 3 rotation rows, then the offset in the effect frame
DISCARD = 4.0 / 255.0                                      # attribute0.z of the draw (captured 0.0157)
FRAME_TIME = 0.0                                           # CB0[99] (presentation: the noise is still)
CAPTURED_SPREAD = 0.964                                    # the random fraction of the b4 spread both instances drew
CAPTURED_SPIN = {2: -0.335, 3: -71.731}                    # degrees about local Z, by component (captured still frame)

PREVIEW_LIMITATIONS = (
    'Nefarious mech rocket flame at igLoc_thruster: decoded program 61e4c390 (checked against a capture: correlation '
    '0.9992), the two mesh components\' offsets, rotation, scale and intensity L, alpha, discard and the alpha-over blend; '
    'game_equivalence false.',
    'Presentation values: CB0[115] = 0.277 (the viewer\'s shared amplitude; the capture had 0.2158), frame time 0 (the noise '
    'is still), fog off.',
    'Inferred stand-ins: the random scale fraction (0.964) and spins (-0.3 and -71.7 degrees) are the capture\'s still frame; '
    'the flame stays at the locator\'s rest pose (it does not follow the animated thruster joint); the effect\'s two blue '
    'lights are not drawn.',
)


def _model_stem(model_path):
    name = str(model_path or '').replace('\\', '/').rsplit('/', 1)[-1]
    return name[:-6].lower() if name.lower().endswith('.model') else name.lower()


def is_mech(model_path):
    return _model_stem(model_path) in MODELS


def decode_components(archive, effect_path=EFFECT_PATH):
    """The mesh components of the flame effect, in component order: ``component``, ``rows`` (rotation rows), ``offset``,
    ``scale`` (slot b4 value + CAPTURED_SPREAD x its spread), ``spin`` (radians, captured), ``intensity`` (b2 x b5, grey)
    and ``alpha`` (b6[0] clamped to [0, 1]: component 3 holds 2.0, the capture had 0.9956)."""
    import struct
    from rc2016.levels.effects import COMPONENT_SIZE, COMPONENTS, decode_effect, parameter_at
    from rc2016.levels.scene import asset_bytes
    from rc2016.levels.zone_format import sections
    data = asset_bytes(archive, effect_path)
    effect = decode_effect(data)
    offset = sections(data)[COMPONENTS][0]
    parameters = effect['parameters']
    result = []
    for n, component in enumerate(effect['components']):
        if component['kind'] != MESH_KIND or not component['asset']:   # component 1 is a mesh kind with no model
            continue
        record = data[offset + n * COMPONENT_SIZE:offset + (n + 1) * COMPONENT_SIZE]

        def slot(k):
            index = component['slots'][k]
            return parameters[index] if index < len(parameters) else None
        size, b2, b5, b6 = slot(4), slot(2), slot(5), slot(6)
        if size is None or b2 is None or b5 is None or b6 is None:
            continue
        value = parameter_at(size, 1.0)
        scale = tuple(float(v + CAPTURED_SPREAD * s) for v, s in zip(value, size['spread'][:3]))
        rows = struct.unpack_from('<9f', record, COMPONENT_ROTATION)
        result.append(dict(component=n, rows=(rows[0:3], rows[3:6], rows[6:9]),
                           offset=struct.unpack_from('<3f', record, COMPONENT_POSITION), scale=scale,
                           spin=math.radians(CAPTURED_SPIN.get(n, 0.0)),
                           intensity=float(parameter_at(b2, 1.0)[0] * parameter_at(b5, 1.0)[0]),
                           alpha=min(1.0, max(0.0, float(parameter_at(b6, 1.0)[0])))))
    return result


def flame_mesh(archive):
    """The flame model's single mesh: ``positions`` (native), ``uv`` (the game's (u, v): decode_model's v is 1 - v, checked
    against the captured vertices) and ``triangles``."""
    from rc2016.formats.native_model import decode_model
    from rc2016.levels.scene import asset_bytes
    mesh = decode_model(FLAME_MODEL, data=asset_bytes(archive, FLAME_MODEL))['meshes'][0]
    triangles = mesh['triangles']
    flat = [i for t in triangles for i in t] if triangles and not isinstance(triangles[0], int) else list(triangles)
    return dict(positions=[tuple(map(float, p)) for p in mesh['positions']], uv=[(float(t[0]), 1.0 - float(t[1])) for t in mesh['uv0']],
                triangles=[tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)])


def _rows_apply(rows, v):
    return tuple(sum(v[i] * rows[i][k] for i in range(3)) for k in range(3))


def instance_rows(preview, component):
    """Native model-space rows and origin of one mesh component: local p -> origin + p @ rows (row vectors), i.e. scale,
    then the spin about local Z, the component rotation and the locator frame."""
    sx, sy, sz = component['scale']
    c, s = math.cos(component['spin']), math.sin(component['spin'])
    local = ((c * sx, s * sx, 0.0), (-s * sy, c * sy, 0.0), (0.0, 0.0, sz))
    turned = [_rows_apply(component['rows'], row) for row in local]
    rows = [_rows_apply(preview['rows'], row) for row in turned]
    origin = tuple(o + d for o, d in zip(preview['origin'], _rows_apply(preview['rows'], component['offset'])))
    return origin, rows


def mech_flame_preview(archive, model_path, data, locators):
    """The labelled flame preview of a Nefarious mech model, else None: ``origin`` and ``rows`` (the native locator frame
    through its joint's rest matrix), ``joint``, the decoded ``components``, the flame ``mesh`` and the ``texture_paths``."""
    if not is_mech(model_path):
        return None
    from rc2016.viewer.holocon_effect import joint_rest_worlds, locator_frame
    locator = next((row for row in locators or () if row.get('name') == LOCATOR), None)
    if locator is None:
        return None
    joint_world = None
    if locator.get('joint') is not None:
        worlds = joint_rest_worlds(data)
        if locator['joint'] < len(worlds):
            joint_world = worlds[locator['joint']]
    origin, rows = locator_frame(locator, joint_world)
    components = decode_components(archive)
    if not components:
        return None
    return dict(family='mech_flame', effect=EFFECT_PATH, conduit=CONDUIT_PATH, locator=LOCATOR, joint=locator.get('joint'),
                origin=origin, rows=rows, components=components, mesh=flame_mesh(archive), amplitude=AMPLITUDE,
                frame_time=FRAME_TIME, texture_paths=dict(TEXTURES), srgb_roles=[], mipmap_roles=sorted(TEXTURES),
                programs=dict(PROGRAMS), limitations=list(PREVIEW_LIMITATIONS), game_equivalence=False)


def shade(u, v, noise, intensity, amplitude=AMPLITUDE, alpha=1.0):
    """The decoded program at one point, for checks: (rgb, alpha), or None where it discards. ``noise`` is the sampled
    noise.r at (u, 0.5 v - 2 t)."""
    s = 1.0 - v
    q = min(1.0, max(0.0, (s - 0.15) ** 4)) + (0.3 * noise - 0.15) * s
    m = min(1.0, max(0.0, q))
    if m < DISCARD:
        return None
    a = abs(q)
    k = 93.0 * amplitude * intensity
    return (k * q * q, k * a ** 0.75, k * a ** 0.01), min(1.0, max(0.0, alpha * m))


def billboards(preview, modelview):
    """View-space triangles of the flame instances for one frame, in the captured draw order (component 2, then 3), as
    effect quads with ``triangles`` set: alpha-over, depth-tested, no culling. No light: returns (quads, None)."""
    view = lambda p: _transform(modelview, native_to_display(p))   # noqa: E731
    mesh = preview['mesh']
    quads = []
    for component in preview['components']:
        origin, rows = instance_rows(preview, component)
        points = [view(tuple(o + d for o, d in zip(origin, _rows_apply(rows, p)))) for p in mesh['positions']]
        corners = [(points[i], mesh['uv'][i]) for triangle in mesh['triangles'] for i in triangle]
        centre = view(origin)
        quads.append(dict(kind=MECH_FLAME, component=component['component'], corners=corners, triangles=True,
                          colour=(component['intensity'],) * 3, alpha=component['alpha'], discard=DISCARD, depth_test=True,
                          cull=False, additive=False, depth=-centre[2]))
    return quads, None


__all__ = ['EFFECT_PATH', 'FLAME_MODEL', 'LOCATOR', 'MECH_FLAME', 'MODELS', 'billboards', 'decode_components', 'flame_mesh',
           'instance_rows', 'is_mech', 'mech_flame_preview', 'shade']
