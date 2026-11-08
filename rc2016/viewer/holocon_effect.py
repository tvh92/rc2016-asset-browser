"""Holocon device holograms: a labelled preview of the `*_UI_Holocon` visual effects in the model viewer.

The blue holograms over the weapon vendor, the Hydrodisplacer port, the Trespasser port, the teleport pad, the bolt and
gear cranks, the jetpack refuel pad, the enemy turret and the Hologuise button are not model geometry: each device's actor
conduit starts a `*_UI_Holocon.visualeffect` at one of its model locators. The device -> effect table below pairs each model
with the conduit of the same folder name (the pairing is by name, not decoded; the weapon vendor's is confirmed by capture).

An effect is up to four components, read from the effect file (rc2016.levels.effects):
- the icon: a quad of full size b4 at the component's local position (+0x24, decoded and captured: (0, 1, 0) on the
  vendor), facing the camera (captured on the vendor) or, when the component's byte +0x101 is 1 (the Hydrodisplacer and
  Trespasser icons), held in the effect's local XY plane facing +Z (from the data, not captured), drawn with the shared graph `gbl_icon_hologram` (runtime program 7628b331, stored 5FD7AB22); the icon
  image is the material's, the hue is the material key 0xC7438245 (0 cyan, 1 red on `lockout`);
- the rays: a particle emitter (kind 1, material `gdgt_holocon_rays`, runtime program c5646808) whose quads are axial
  billboards: the apex edge on the emitter, the length along a direction tilted off the emitter axis, the width
  perpendicular to that direction and to the view; the emitter axis is local Z of the 3 x 3 at +0x00 of section 0xC3CC2AB5
  (decoded, captured: the vendor's points up, the Hydrodisplacer's forward at its icon);
- a soft glow (the health crate's glow_simp_soft program, rc2016/viewer/idle_effect.py GLOW_SOFT) of size b4 and colour
  b2 x b5 at the origin, on the vendor, teleporter, turret, jetpack and crank effects;
- a light (kind 2) at its local position, hue b5.

Captured on the weapon vendor (Research/captures/2026-11-08_holocon-hologram/README.md, local): the icon program's output
over 22 545 pixels (correlation >= 0.99994, median error 0.6 to 1.1 %) and the rays' over 4 249 pixels (correlation 0.998,
median 1 to 3 %), the quad sizes, offsets and orientations, the alpha-over blend with depth test and no depth write, back
faces culled on the icon, the discard threshold 4/255, the lit path off (instance flag 0x20 clear, L = 1), no fog.

Presentation values (one frame's, not the game's per-frame values): the frame time CB0[99] = 0, the per-frame random
CB0[107] = 0.0763 and the icon's per-particle seed attribute3.z = 0.9059 (both captured), so the trigger curve's glitch
(9 of its 256 texels are 1) is off; CB0[115] = idle_effect.AMPLITUDE (0.277; the vendor frame had 0.343).

INFERRED STAND-INS (not decoded; named in PREVIEW_LIMITATIONS and docs/viewer.md):
- the ray length scale 1.18 per unit of icon distance (the teleporter capture, rays reaching the icon as in every
  screenshot; 0.37 in the vendor capture, cause unknown), and the tilt 5..19.5 degrees off the emitter axis (the captured
  range over 19 rays); width and length otherwise follow list entry 9 (0.52..1.0), the rate and life entries 4 and 5 and the
  apex radius section 0x3F03BE86 +0x54 (decoded, per effect);
- the light's colour 0.112 x b2 x hue and radius b4 (the health crate's one captured light: b2 = 2, b4 = 1, colour 0.224 x
  hue, radius stand-in 1 u);
- the device -> conduit pairing by folder name, and the normal (not locked) holocon as the one shown.
"""
from __future__ import annotations

import math
import random
import struct

from rc2016.viewer.idle_effect import AMPLITUDE, GLOW_SOFT, _transform, native_to_display

HOLO_ICON, HOLO_RAYS = 5, 6                      # shading.EFFECT_FRAGMENT uEffectKind
PROGRAMS = {HOLO_ICON: '7628b331', HOLO_RAYS: 'c5646808', GLOW_SOFT: '1fc6a696'}   # runtime programs drawn in the capture

_GADGET = 'visualeffect/equipment/gadget/'
EFFECTS = dict(
    vendor=_GADGET + 'gdgt_vendor_ui_holocon/gdgt_vendor_ui_holocon.visualeffect',
    hydrodisplacer=_GADGET + 'gdgt_hydrodisplacer_ui_holocon/gdgt_hydrodisplacer_ui_holocon.visualeffect',
    trespasser=_GADGET + 'gdgt_trespasser_ui_holocon/gdgt_trespasser_ui_holocon.visualeffect',
    teleporter=_GADGET + 'gdgt_tri_btn_ui_holocon/gdgt_teleporter_ui_holocon.visualeffect',
    bolt_crank=_GADGET + 'gdgt_bolt_crank_ui_holocon/gdgt_bolt_crank_ui_holocon.visualeffect',
    turret=_GADGET + 'gdgt_turret_ui_holocon/gdgt_turret_ui_holocon.visualeffect',
    hologuise=_GADGET + 'gdgt_hologuise_ui_holocon/gdgt_hologuise_ui_holocon.visualeffect',
    jetpack='visualeffect/environment/global/gameplay/gbl_gp_jetpack_refuel_ui_holocon/gbl_gp_jetpack_refuel_ui_holocon.visualeffect')
# Model file stem -> (effect, locator). Locators are read from the models; the conduits name the effect and the locator.
DEVICES = {
    'weapon_vendor_01': ('vendor', 'igLoc_holo_emit'),                     # gbl_gp_weapon_vendor_vfx (captured)
    'gbl_gp_hydrodisplacer_port_01': ('hydrodisplacer', 'igLoc_holo_emit'),  # gbl_gp_hydrodisplacer_port_vfx
    'gbl_gp_port_trespasser': ('trespasser', 'igLoc_holo_emit'),           # gbl_trespasser_fx
    'gbl_gp_teleport_pad_01': ('teleporter', 'igLoc_AttachPosition'),      # gbl_gp_teleport_pad_fx
    'gbl_gp_boltcrank_01': ('bolt_crank', 'igLoc_holo_emit'),              # gbl_bolt_crank_vfx
    'gbl_gp_gearcrank_01': ('bolt_crank', 'igLoc_holo_emit'),              # gbl_gp_gear_crank_vfx
    'gbl_gp_jetpack_refuel_pad_01': ('jetpack', 'igLoc_holo_emit'),        # gbl_gp_jetpack_refuel_pad_vfx
    'gbl_gp_enemy_turret_01': ('turret', 'igLoc_holo_emit'),               # enm_blarg_gp_turret_fx
    'dep_gp_hologuise_button_01': ('hologuise', 'igLoc_fx_attach'),        # gbl_gp_qwark_hologuise_button_vfx
}

ICON_GRAPH = 'materialgraph/visualeffect/global/gbl_icon_hologram/gbl_icon_hologram.materialgraph'
RAYS_MATERIAL = 'gdgt_holocon_rays'
GLOW_MATERIAL = 'gbl_glow_simp_soft'
# Image units of the icon program (captured bindings 10, 11 and 9) and of the rays program (bindings 8, 7, 9).
TEXTURES = dict(background='textures/visualeffect/unique/icon_holograms/icon_holograms_background.texture',
                scan='textures/visualeffect/scan_lines/scan_lines_01/scan_lines_01.texture',
                trigger='textures/visualeffect/trigger/trigger_02/trigger_02.texture',
                laser1='textures/visualeffect/lights/laser_light_cone_01.texture',
                laser2='textures/visualeffect/lights/laser_light_cone_02.texture',
                laser3='textures/visualeffect/lights/laser_light_cone_03.texture')
# Icon graph keys (CB dwords 0..6 of the program, its table order): sample offsets for the R, B and G samples and the hue.
KEY_OFFSET_R, KEY_OFFSET_B, KEY_OFFSET_G, KEY_HUE = 0x4BAF03EF, 0x371432C4, 0x7FF43CA0, 0xC7438245

EMITTER_SECTION = 0xC3CC2AB5                     # +0x00: the emitter's 3 x 3 orientation in effect space (rows = local axes)
COMPONENT_POSITION = 0x24                        # float3 local position of a component (decoded; captured on the vendor)
COMPONENT_MATERIAL = 0x4C                        # u64 CRC-64 of the component's material path
# Orientation bytes: +0x101 = 1 holds the component in the effect frame (the Hydrodisplacer and Trespasser icons; across the
# corpus the mesh components and lights), +0x102 = 1 faces the camera (the vendor's icon, captured; the other sprites).
# A fixed icon lies in the effect's local XY plane, facing +Z (its emitter axis). Read from the data, not captured.
COMPONENT_FIXED = 0x101
IDENTITY_ROWS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
FADE_DISTANCE, FADE_RANGE = 150.0, 25.0          # component +0xD8 = 150 on every holocon component; the range as idle_effect
DISCARD = 4.0 / 255.0                            # attribute0.z of the icon and of every ray (captured)
FRAME_TIME, FRAME_RANDOM, ICON_SEED = 0.0, 0.0763, 0.9059   # CB0[99] (presentation), CB0[107] and attribute3.z (captured)

# Ray emitter: decoded parameter list entries (the health crate's emitter list layout) and the captured spread.
RAY_RATE = 10.0                                  # list index 4: particles per second (default; read per effect: 20 on the ports)
RAY_LIFE = (0.9, 1.0)                            # list index 5: 1.0 - 0.1 rnd seconds
RAY_ALPHA_MAX, RAY_ALPHA_RAMP = 0.532, 0.2       # list index 11: alpha 0 -> (0.532 - 0.532 rnd) over the first 0.2 of the life
RAY_COLOUR_SCALE = 10.0                          # list index 7
RAY_HUE, RAY_HUE_SPREAD = (0.2103, 0.7035, 1.0), (-0.161, -0.162, 0.0)   # list index 10, one random for R and G (captured)
RAY_SIZE = (0.5226, 0.9966)                      # list index 9 y: 0.9966 - 0.474 rnd; the width, and the length times RAY_LENGTH_SCALE
# INFERRED stand-ins. The length scale is a runtime factor: 1.18 in the teleporter capture, whose rays reach the icon as in
# every game screenshot (lengths 0.62..1.17 = 1.18 x the list range), 0.37 in the vendor capture (0.21..0.37); what sets
# it is not found (not the camera distance, the frame time or the effect data, which is the same for both devices).
# Taken per unit of the icon's distance from the emitter (1.0 on the captured teleporter), so that the rays reach the icon
# on the Hydrodisplacer and Trespasser too (0.75), as the game's screenshots show; that scaling is inferred.
RAY_LENGTH_SCALE = 1.18
RAY_TILT_DEG = (5.0, 19.5)                       # the captured range over 19 rays in two captures
RAY_APEX_RADIUS = 0.1                            # default; read per effect from RAY_SPREAD_SECTION +0x54
RAY_SPREAD_SECTION = 0x3F03BE86
SNAPSHOT_AGE, SNAPSHOT_SEED = 4.0, 2016
LIGHT_SCALE = 0.112                              # INFERRED: the health crate's captured 0.224 x hue at b2 = 2

PREVIEW_LIMITATIONS = (
    'Holocon hologram preview at the device locator: decoded icon and ray programs (checked against the weapon vendor '
    'capture), component sizes, offsets, colours, the emitter axis, blend and discard; game_equivalence false.',
    'Presentation values: frame time 0 (the trigger glitch and the scan-line scroll are frozen), CB0[107] 0.0763 and the '
    'icon seed 0.9059 (captured), CB0[115] = 0.277 (the vendor frame had 0.343), fog off, unlit particles (as captured).',
    'Inferred stand-ins: the ray length scale 1.18 per unit of icon distance (teleporter capture; 0.37 in the vendor '
    'capture), tilt 5..19.5 deg (captured range), a still frame at effect age 4 s with seeded rays, the light colour 0.112 x b2 x hue with radius b4, the device '
    '-> conduit pairing by folder name, and the unlocked holocon.',
)


def _model_stem(model_path):
    name = str(model_path or '').replace('\\', '/').rsplit('/', 1)[-1]
    return name[:-6].lower() if name.lower().endswith('.model') else name.lower()


def device_effect(model_path):
    """(effect key, effect path, locator name) for a holocon device model, else None."""
    row = DEVICES.get(_model_stem(model_path))
    return (row[0], EFFECTS[row[0]], row[1]) if row else None


def _rows_apply(rows, v):
    """Row-vector convention: v x M with the rows as the local axes."""
    return tuple(sum(v[i] * rows[i][k] for i in range(3)) for k in range(3))


def _matmul4(m, p):
    return tuple(sum(m[r][c] * p[c] for c in range(3)) + m[r][3] for r in range(3))


def locator_frame(locator, joint_world=None):
    """Native position and rotation rows of a locator, through its joint's rest world matrix (4 x 4, column vectors)
    when the locator hangs off a joint other than the root."""
    position = tuple(float(v) for v in locator['position'])
    rows = [tuple(float(v) for v in row) for row in locator['rotation']]
    if joint_world is not None:
        origin = _matmul4(joint_world, (0.0, 0.0, 0.0))
        position = _matmul4(joint_world, position)
        rows = [tuple(a - b for a, b in zip(_matmul4(joint_world, row), origin)) for row in rows]
    return position, rows


def joint_rest_worlds(data):
    """Rest world matrices (4 x 4 lists, column vectors) of a model's joints: the inverses of the stored inverse binds
    (section 0xDCC88A19 after the SQT rows, as rc2016.formats.skeleton reads them); [] when absent."""
    import numpy as np
    from rc2016.formats.native_model import _sections
    sections = _sections(data)
    joints, transforms = sections.get(0x15DF9D3B), sections.get(0xDCC88A19)
    if not joints or not transforms:
        return []
    count = joints['size'] // 16
    start = ((count * 48 + 63) // 64) * 64
    if transforms['size'] < start + count * 64:
        return []
    result = []
    for i in range(count):
        m = np.asarray(struct.unpack_from('<16f', data, transforms['offset'] + start + i * 64)).reshape(4, 4).T
        result.append(np.linalg.inv(m).tolist())
    return result


def _material_values(archive, material_path):
    """The icon graph's offset and hue keys for one icon material: the material's value, else the graph default."""
    from rc2016.formats.materials import extract_material_parameters
    from rc2016.levels.scene import asset_bytes
    from rc2016.shaders.families import graph_parameter_tables
    _tables, defaults = graph_parameter_tables(asset_bytes(archive, ICON_GRAPH))
    rows = extract_material_parameters(asset_bytes(archive, material_path))
    rows = {r.get('key_hash'): r for r in rows.get('parameters', []) + rows.get('unsupported_parameters', [])}

    def value(key, count):
        row = rows.get(key)
        if row and row.get('size', 0) >= 4 * count:
            return list(struct.unpack('<%df' % count, bytes.fromhex(row['raw_hex'])[:4 * count]))
        default = defaults.get(key, [0.0] * count)
        return [float(v) for v in (default if isinstance(default, (list, tuple)) else [default])][:count]
    return dict(offset_r=value(KEY_OFFSET_R, 2), offset_g=value(KEY_OFFSET_G, 2), offset_b=value(KEY_OFFSET_B, 2),
                hue=value(KEY_HUE, 1)[0])


def _icon_texture(archive, material_path):
    from rc2016.levels.scene import asset_bytes
    from rc2016.viewer.textures import MATERIAL_SLOT_SECTION, texture_slots
    paths = [p for p in texture_slots(asset_bytes(archive, material_path), MATERIAL_SLOT_SECTION).values()
             if 'icon_holograms_' in p and not p.endswith('icon_holograms_background.texture')]
    if not paths:
        raise ValueError(f'{material_path}: no icon texture')
    return paths[0] if paths[0].endswith('.texture') else paths[0] + '.texture'


def decode_holocon(archive, effect_path):
    """The components of one holocon effect: ``icon`` (size, offset, discard, material path), ``rays`` (axis rows),
    ``glow`` (size, colour, alpha, offset) and ``light`` (offset, hue, b2, b4); missing ones are None."""
    import re
    from rc2016.levels.effects import COMPONENT_SIZE, COMPONENTS, decode_effect, parameter_at
    from rc2016.levels.scene import asset_bytes
    from rc2016.levels.zone_format import path_crc64, sections
    data = asset_bytes(archive, effect_path)
    effect = decode_effect(data)
    table = sections(data)
    offset = table[COMPONENTS][0]
    materials = {}
    for m in re.finditer(rb'material[\\/][ -~]+?\.material', data):
        path = m.group().decode().replace('\\', '/').lower()
        materials[path_crc64(path)] = path
    parameters = effect['parameters']
    result = dict(effect=effect_path, icon=None, rays=None, glow=None, light=None)

    def slot(component, k):
        index = component['slots'][k]
        return parameter_at(parameters[index], 1.0) if index < len(parameters) else None
    for n, component in enumerate(effect['components']):
        record = data[offset + n * COMPONENT_SIZE:offset + (n + 1) * COMPONENT_SIZE]
        position = struct.unpack_from('<3f', record, COMPONENT_POSITION)
        material = materials.get(struct.unpack_from('<Q', record, COMPONENT_MATERIAL)[0], '')
        name = material.rsplit('/', 1)[-1][:-len('.material')] if material else ''
        if component['kind'] == 2:
            result['light'] = dict(component=n, offset=position, hue=tuple(slot(component, 5)), b2=slot(component, 2)[0],
                                   b4=slot(component, 4)[0])
        elif component['kind'] == 1 and name == RAYS_MATERIAL:
            if EMITTER_SECTION in table:
                start = table[EMITTER_SECTION][0]
                rows = [struct.unpack_from('<3f', data, start + 12 * r) for r in range(3)]
            else:
                rows = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
            # Emitter list entries 4 (rate) and 5 (life: value + spread x rnd), and the apex radius at section 0x3F03BE86
            # +0x54 (0.1 on the vendor and teleporter, matching the captured apexes; 0 on the Hydrodisplacer and Trespasser).
            rate, life = RAY_RATE, RAY_LIFE
            if len(parameters) > 5:
                rate = parameter_at(parameters[4])[0] or RAY_RATE
                key = parameters[5]['keys'][0]
                life = (key['value'][0] + min(0.0, key['spread'][0]), key['value'][0] + max(0.0, key['spread'][0]))
            radius = RAY_APEX_RADIUS
            if RAY_SPREAD_SECTION in table and table[RAY_SPREAD_SECTION][1] >= 0x58:
                radius = struct.unpack_from('<f', data, table[RAY_SPREAD_SECTION][0] + 0x54)[0]
            result['rays'] = dict(component=n, axis_rows=rows, material=material, rate=rate, life=life, apex_radius=radius)
        elif component['kind'] == 0 and name.startswith('gbl_icon_hologram'):
            size = slot(component, 4)
            result['icon'] = dict(component=n, offset=position, size=(size[0], size[1]), alpha=slot(component, 6)[0],
                                  material=material, fixed=record[COMPONENT_FIXED] == 1)
        elif component['kind'] == 0 and name == GLOW_MATERIAL:
            size, a, b = slot(component, 4), slot(component, 2), slot(component, 5)
            result['glow'] = dict(component=n, offset=position, size=(size[0], size[1]), colour=tuple(x * y for x, y in zip(a, b)),
                                  alpha=slot(component, 6)[0], discard=math.floor(slot(component, 6)[1] * 255.0) / 255.0)
    return result


def rays(seed=SNAPSHOT_SEED, age=SNAPSHOT_AGE, emitter=None, icon_distance=1.0):
    """The live rays at the snapshot, oldest first (the captured draw order): ``tilt`` (radians), ``azimuth``, ``apex``
    (offset in the emitter's plane), ``length``, ``width``, ``alpha``, ``colour``, ``laser`` (1..3) and ``t``. ``emitter``
    (decode_holocon's ``rays``) gives the rate, life and apex radius; the length scale is RAY_LENGTH_SCALE per unit of
    ``icon_distance`` (the icon's distance from the emitter)."""
    emitter = emitter or {}
    rate, lives = emitter.get('rate', RAY_RATE), emitter.get('life', RAY_LIFE)
    apex_radius = emitter.get('apex_radius', RAY_APEX_RADIUS)
    scale = RAY_LENGTH_SCALE * icon_distance
    rng = random.Random(seed)
    result = []
    for n in range(int(math.floor(age * rate)) + 1):
        life = rng.uniform(*lives)
        tilt, azimuth = math.radians(rng.uniform(*RAY_TILT_DEG)), rng.uniform(0.0, 2.0 * math.pi)
        radius, angle = apex_radius * math.sqrt(rng.random()), rng.uniform(0.0, 2.0 * math.pi)
        length, width = scale * rng.uniform(*RAY_SIZE), rng.uniform(*RAY_SIZE)
        target = RAY_ALPHA_MAX * (1.0 - rng.random())
        hue = rng.random()
        laser = 1 + min(2, int(rng.random() * 3.0))
        elapsed = age - n / rate
        if not 0.0 <= elapsed < life:
            continue
        t = elapsed / life
        colour = tuple(RAY_COLOUR_SCALE * (c + s * hue) for c, s in zip(RAY_HUE, RAY_HUE_SPREAD))
        result.append(dict(tilt=tilt, azimuth=azimuth, apex=(radius * math.cos(angle), radius * math.sin(angle)),
                           length=length, width=width, alpha=target * min(1.0, t / RAY_ALPHA_RAMP), colour=colour,
                           laser=laser, t=t))
    result.sort(key=lambda r: -r['t'])
    return result


def holocon_preview(archive, model_path, data, locators):
    """The labelled preview of a holocon device model, else None: ``origin`` and ``rows`` (native locator frame), the
    decoded components, the still-frame rays, ``textures``-to-load (``texture_paths``) and the icon's material values."""
    device = device_effect(model_path)
    if not device:
        return None
    key, effect_path, locator_name = device
    locator = next((row for row in locators or () if row.get('name') == locator_name), None)
    if locator is None:
        return None
    joint_world = None
    if locator.get('joint'):
        worlds = joint_rest_worlds(data)
        if locator['joint'] < len(worlds):
            joint_world = worlds[locator['joint']]
    origin, rows = locator_frame(locator, joint_world)
    parts = decode_holocon(archive, effect_path)
    texture_paths = dict(TEXTURES)
    icon_values = None
    if parts['icon']:
        texture_paths['icon'] = _icon_texture(archive, parts['icon']['material'])
        icon_values = _material_values(archive, parts['icon']['material'])
    icon_distance = math.sqrt(sum(v * v for v in parts['icon']['offset'])) if parts['icon'] else 1.0
    light = None
    if parts['light']:
        hue, b2 = parts['light']['hue'], parts['light']['b2']
        light = dict(offset=parts['light']['offset'], hue=hue, colour=tuple(LIGHT_SCALE * b2 * c for c in hue),
                     radius=parts['light']['b4'], unit_scale=1.0)
    return dict(family='holocon', device=key, effect=effect_path, locator=locator_name, origin=origin, rows=rows,
                icon=parts['icon'], icon_values=icon_values, glow=parts['glow'], ray_emitter=parts['rays'],
                rays=rays(emitter=parts['rays'], icon_distance=icon_distance) if parts['rays'] else [], light=light, amplitude=AMPLITUDE, texture_paths=texture_paths,
                frame=dict(time=FRAME_TIME, random=FRAME_RANDOM, seed=ICON_SEED), programs=dict(PROGRAMS),
                limitations=list(PREVIEW_LIMITATIONS), game_equivalence=False)


def effect_point(preview, local):
    """Native model-space position of an effect-local point."""
    offset = _rows_apply(preview['rows'], local)
    return tuple(o + d for o, d in zip(preview['origin'], offset))


def emitter_axes(preview):
    """Native unit axes (x, y, z) of the ray emitter: its rows through the locator frame."""
    rows = preview['ray_emitter']['axis_rows'] if preview.get('ray_emitter') else [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    return [_rows_apply(preview['rows'], row) for row in rows]


def ray_geometry(preview, ray):
    """Native apex point and unit direction of one ray."""
    ex, ey, ez = emitter_axes(preview)
    apex = tuple(o + ray['apex'][0] * x + ray['apex'][1] * y for o, x, y in zip(preview['origin'], ex, ey))
    s = math.sin(ray['tilt'])
    direction = tuple(math.cos(ray['tilt']) * z + s * (math.cos(ray['azimuth']) * x + math.sin(ray['azimuth']) * y)
                      for x, y, z in zip(ex, ey, ez))
    return apex, direction


def distance_fade(distance):
    if distance > FADE_DISTANCE:
        return None
    return max(0.0, min(1.0, (FADE_DISTANCE - distance) / FADE_RANGE))


def _normalise(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 0.0 else (0.0, 0.0, 0.0)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def billboards(preview, modelview):
    """View-space quads for one frame in the captured draw order (icon, rays, glow), as idle_effect.billboards: dicts with
    ``kind``, ``corners`` ((xyz, uv) x 4), ``colour``, ``alpha``, ``discard``, ``depth_test``, ``cull``, ``component`` and,
    for rays, ``laser``. Also returns the light as (view-space position, colour, radius) or None."""
    origin = _transform(modelview, native_to_display(preview['origin']))
    distance = math.sqrt(sum(v * v for v in origin))
    fade = distance_fade(distance)
    if fade is None or fade <= 0.0:
        return [], None
    view = lambda p: _transform(modelview, native_to_display(p))   # noqa: E731
    quads = []
    icon = preview.get('icon')
    if icon:
        hw, hh = icon['size'][0] * 0.5, icon['size'][1] * 0.5
        layout = (((-hw, -hh), (0.0, 1.0)), ((hw, -hh), (1.0, 1.0)), ((hw, hh), (1.0, 0.0)), ((-hw, hh), (0.0, 0.0)))   # v = 0 at the top (captured)
        if icon.get('fixed'):   # in the effect's local XY plane, front face toward local +Z
            ox, oy, oz = icon['offset']
            corners = [(view(effect_point(preview, (ox + x, oy + y, oz))), uv) for (x, y), uv in layout]
            centre = view(effect_point(preview, icon['offset']))
        else:
            centre = view(effect_point(preview, icon['offset']))
            corners = [((centre[0] + x, centre[1] + y, centre[2]), uv) for (x, y), uv in layout]
        quads.append(dict(kind=HOLO_ICON, component=icon['component'], corners=corners, colour=(1.0, 1.0, 1.0),
                          alpha=icon['alpha'] * fade, discard=DISCARD, depth_test=True, cull=True, depth=-centre[2]))
    if preview.get('ray_emitter'):
        for ray in preview['rays']:
            apex, direction = ray_geometry(preview, ray)
            a = view(apex)
            b = view(tuple(p + ray['length'] * d for p, d in zip(apex, direction)))
            axis = _normalise(tuple(q - p for p, q in zip(a, b)))
            side = _normalise(_cross(axis, _normalise(a)))
            hw = ray['width'] * 0.5
            corners = [(tuple(p - hw * s for p, s in zip(a, side)), (0.0, 0.0)), (tuple(p + hw * s for p, s in zip(a, side)), (1.0, 0.0)),
                       (tuple(p + hw * s for p, s in zip(b, side)), (1.0, 1.0)), (tuple(p - hw * s for p, s in zip(b, side)), (0.0, 1.0))]
            quads.append(dict(kind=HOLO_RAYS, component=preview['ray_emitter']['component'], corners=corners,
                              colour=ray['colour'], alpha=ray['alpha'] * fade, discard=DISCARD, depth_test=True, cull=False,
                              laser='laser%d' % ray['laser'], depth=-a[2]))
    glow = preview.get('glow')
    if glow:
        centre = view(effect_point(preview, glow['offset']))
        hw, hh = glow['size'][0] * 0.5, glow['size'][1] * 0.5
        corners = [((centre[0] + x, centre[1] + y, centre[2]), (s, t))
                   for (x, y), (s, t) in (((-hw, -hh), (0.0, 0.0)), ((hw, -hh), (1.0, 0.0)), ((hw, hh), (1.0, 1.0)), ((-hw, hh), (0.0, 1.0)))]
        quads.append(dict(kind=GLOW_SOFT, component=glow['component'], corners=corners, colour=glow['colour'],
                          alpha=glow['alpha'] * fade, discard=glow['discard'], depth_test=True, cull=False, depth=-centre[2]))
    light = preview.get('light')
    if light:
        position = view(effect_point(preview, light['offset']))
        light = (position, tuple(c * light['unit_scale'] * fade for c in light['colour']), light['radius'])
    return quads, light


__all__ = ['DEVICES', 'EFFECTS', 'HOLO_ICON', 'HOLO_RAYS', 'billboards', 'decode_holocon', 'device_effect',
           'effect_point', 'holocon_preview', 'ray_geometry', 'rays']
