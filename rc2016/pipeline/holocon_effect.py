"""A holocon device's hologram in a Blender scene: the decoded icon, rays and glow of `rc2016/viewer/holocon_effect.py`
as emission quads.

Without this an extracted weapon vendor, Hydrodisplacer port, Trespasser port, teleport pad, crank, jetpack pad, turret or
Hologuise button has no hologram: it is the effect `*_UI_Holocon.visualeffect` that the device's conduit starts at a model
locator, not model geometry.

What is built (every value is the viewer's, see holocon_effect.py there for what is decoded, captured and inferred):
- the icon: a 1 x 1 quad scaled to the icon size, facing the camera (Copy Rotation on the scene camera), with the icon
  program (runtime 7628b331) as nodes at the viewer's still frame: the three offset icon samples, the trigger value (a constant
  of the frame, 0 at the presentation seed), the scan-line distortion and brightness, the background, the hue colour, and
  alpha sat(a m) with the 4/255 discard;
- the rays: one quad per live ray of the viewer's still frame, its apex edge on the emitter and its length along the ray,
  turned about that axis toward the camera (Locked Track: the game's rays are axial billboards), with the rays program
  (c5646808): colour A P2 (1 + 5 r), alpha sat(a r), r the ray's laser cone image;
- the soft glow, where the effect has one: the health crate's glow_soft program with its soft-particle fade toward the
  emitter's plane (``make_glow_material``);
- the camera-distance fade sat((150 - d) / 25) as a driver on every quad's ``fx_alpha``; the effect light (the
  viewer's inferred colour and radius) when the description asks for it, as the pipeline build does.

The images are written next to the output (``prepare_textures``): the icon, the background, the scan lines, the trigger curve
and the three laser cones, as 8-bit PNGs of the decoded top mips (Non-Color). Not built: the scan-line scroll and the trigger
glitch over time (the frame is still), particle motion, the soft-particle depth term, mip filtering. ``bpy`` is imported
inside the functions that need it, so the data helpers import anywhere.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

FAMILY = 'holocon_effect'
EFFECT_PASS_INDEX = 2                    # the health effect's Object Index: the bloom mask (health_effect.add_bloom) reads it
ROLES = ('icon', 'background', 'scan', 'trigger', 'laser1', 'laser2', 'laser3')


def _trigger_value(archive, path, x):
    """The trigger curve's R at coordinate x (bilinear, wrapped), as the icon program samples it."""
    import numpy as np
    image = _decoded(archive, path)[..., 0]
    w = image.shape[1]
    p = (x % 1.0) * w - 0.5
    i = int(math.floor(p))
    f = p - i
    return float(image[0, i % w] * (1.0 - f) + image[0, (i + 1) % w] * f)


def _decoded(archive, path):
    """float32 (h, w, 4) RGBA of a texture's decoded top mip (the HD mip when there is one)."""
    import io
    import numpy as np
    from PIL import Image
    from rc2016.formats.texture import dds_bytes
    from rc2016.viewer.textures import load_texture
    texture = load_texture(archive, path, hd=True)
    image = Image.open(io.BytesIO(dds_bytes(texture['width'], texture['height'], texture['format'], texture['pixels'])))
    image.load()
    return np.asarray(image.convert('RGBA'), np.float32) / 255.0


def effect_description(archive, preview, light=False):
    """JSON-ready description for the Blender side from a viewer preview (holocon_effect.holocon_preview): ``origin`` (native),
    ``quads`` (``kind`` 'holo_icon' / 'holo_rays' / 'glow_soft', ``name``, ``position`` (native), ``size``, ``alpha``,
    ``discard``, ``emit``; rays also ``direction`` and ``laser``), the icon constants (``icon``), ``fade`` and ``light``."""
    from rc2016.viewer import holocon_effect as fx
    amplitude = preview['amplitude']
    frame = preview['frame']
    quads = []
    icon_constants = None
    if preview.get('icon'):
        icon = preview['icon']
        values = preview['icon_values']
        trigger = _trigger_value(archive, preview['texture_paths']['trigger'], 1.0 + 0.1 * frame['time'] + frame['seed'])
        icon_constants = dict(offset_r=values['offset_r'], offset_g=values['offset_g'], offset_b=values['offset_b'],
                              hue=values['hue'], trigger=trigger, glitch=0.01 if frame['random'] * frame['seed'] < 0.001 else 0.0,
                              time=frame['time'], random=frame['random'], seed=frame['seed'], amplitude=amplitude)
        row = dict(kind='holo_icon', name='icon (component %d)' % icon['component'],
                   position=list(fx.effect_point(preview, icon['offset'])), size=list(icon['size']), alpha=icon['alpha'],
                   discard=fx.DISCARD, emit=[1.0, 1.0, 1.0])
        if icon.get('fixed'):   # held in the effect frame: its native local axes (x, y, z), front face toward z
            row['axes'] = [list(fx._rows_apply(preview['rows'], axis)) for axis in fx.IDENTITY_ROWS]
        quads.append(row)
    for number, ray in enumerate(preview.get('rays') or ()):
        apex, direction = fx.ray_geometry(preview, ray)
        quads.append(dict(kind='holo_rays', name='ray %d (component %d)' % (number, preview['ray_emitter']['component']),
                          position=list(apex), direction=list(direction), size=[ray['width'], ray['length']], alpha=ray['alpha'],
                          discard=fx.DISCARD, emit=[amplitude * c for c in ray['colour']], laser='laser%d' % ray['laser']))
    if preview.get('glow'):
        glow = preview['glow']
        # The soft-particle term needs the surface behind the glow: the emitter's own plane (normal = the emitter axis,
        # through the origin: the pad floor, the vendor's top), the stand-in for the game's scene depth.
        normal = fx.emitter_axes(preview)[2] if preview.get('ray_emitter') else fx._rows_apply(preview['rows'], (0.0, 1.0, 0.0))
        quads.append(dict(kind='glow_soft', name='soft glow (component %d)' % glow['component'],
                          position=list(fx.effect_point(preview, glow['offset'])), size=list(glow['size']), alpha=glow['alpha'],
                          discard=glow['discard'], emit=[2.0 * amplitude * c for c in glow['colour']],
                          soft_size=glow['size'][0], plane_point=list(preview['origin']), plane_normal=list(normal)))
    effect_light = None
    if light and preview.get('light'):
        spec = preview['light']
        effect_light = dict(position=list(fx.effect_point(preview, spec['offset'])), hue=list(spec['hue']), colour=list(spec['colour']),
                            radius=spec['radius'], unit_scale=spec['unit_scale'])
    return dict(effect=preview['effect'], device=preview['device'], locator=preview['locator'], origin=list(preview['origin']),
                icon=icon_constants, quads=quads, fade=dict(distance=fx.FADE_DISTANCE, range=fx.FADE_RANGE), light=effect_light,
                limitations=list(fx.PREVIEW_LIMITATIONS) + [
                    "Blender: a still frame (no scan-line scroll, trigger glitch or ray motion); the glow's soft-particle depth is "
                    "measured to the emitter plane, not the scene; no mip filtering; the light's magnitude and radius are inferred."])


def prepare_textures(archive, preview, directory):
    """Write the hologram's images as 8-bit RGB PNGs under ``directory``; returns role -> path."""
    import numpy as np
    from PIL import Image
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for role in ROLES:
        source = preview['texture_paths'].get(role)
        if not source:
            continue
        rgba = _decoded(archive, source)
        path = directory / ('holocon_%s.png' % role)
        Image.fromarray(np.clip(rgba[..., :3] * 255.0 + 0.5, 0, 255).astype(np.uint8), 'RGB').save(path)
        paths[role] = str(path)
    return paths


# ---------------------------------------------------------------------------------------------------------------------
# Blender side.

def _channel(tree, colour, name):
    from rc2016.pipeline.health_effect import _node
    node = _node(tree, 'ShaderNodeSeparateColor')
    tree.links.new(colour, node.inputs['Color'])
    return node.outputs[name]


def _sample(tree, image, vector, channel):
    """One channel of ``image`` at a game texture coordinate (u, v), v = 0 at the image's first row. Blender puts the
    first row of a loaded image at v = 1, so the lookup is at (u, 1 - v)."""
    from rc2016.pipeline.health_effect import _image, _math, _node
    split = _node(tree, 'ShaderNodeSeparateXYZ')
    tree.links.new(vector, split.inputs['Vector'])
    flipped = _uv(tree, split.outputs['X'], _math(tree, 'SUBTRACT', 1.0, split.outputs['Y']))
    return _channel(tree, _image(tree, image, flipped), channel)


def _uv(tree, u, v):
    from rc2016.pipeline.health_effect import _node
    node = _node(tree, 'ShaderNodeCombineXYZ')
    for slot, value in ((0, u), (1, v)):
        if isinstance(value, (int, float)):
            node.inputs[slot].default_value = float(value)
        else:
            tree.links.new(value, node.inputs[slot])
    return node.outputs['Vector']


def _finish(tree, material, colour, alpha):
    """Emission (colour, strength 1) over transparent by ``alpha``."""
    from rc2016.pipeline.health_effect import _node
    emission = _node(tree, 'ShaderNodeEmission')
    tree.links.new(colour, emission.inputs['Color'])
    emission.inputs['Strength'].default_value = 1.0
    mix = _node(tree, 'ShaderNodeMixShader')
    tree.links.new(alpha, mix.inputs['Fac'])
    tree.links.new(_node(tree, 'ShaderNodeBsdfTransparent').outputs['BSDF'], mix.inputs[1])
    tree.links.new(emission.outputs['Emission'], mix.inputs[2])
    tree.links.new(mix.outputs['Shader'], _node(tree, 'ShaderNodeOutputMaterial').inputs['Surface'])
    for attribute, value in (('surface_render_method', 'BLENDED'), ('use_backface_culling', False)):
        try:
            setattr(material, attribute, value)
        except (AttributeError, TypeError):
            pass


def _new_material(name):
    import bpy
    material = bpy.data.materials.get(name)
    if material:
        bpy.data.materials.remove(material)
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.clear()
    return material


def make_icon_material(images, k):
    """The icon program 7628b331 at the description's frame constants ``k`` (holocon_effect.billboards' HOLO_ICON)."""
    from rc2016.pipeline.health_effect import _attribute, _math, _node, _vector
    material = _new_material('Holocon icon')
    tree = material.node_tree
    split = _node(tree, 'ShaderNodeSeparateXYZ')
    tree.links.new(_node(tree, 'ShaderNodeTexCoord').outputs['UV'], split.inputs['Vector'])
    u, v = split.outputs['X'], _math(tree, 'SUBTRACT', 1.0, split.outputs['Y'])     # the game's v = 0 is the top
    p = _uv(tree, u, v)
    t, rnd, seed, f = k['time'], k['random'], k['seed'], k['trigger']
    icon, bg, scan = images['icon'], images['background'], images['scan']
    background_jitter = (0.5 * rnd, 0.5 * rnd, 0.0)
    d = _math(tree, 'ADD', _sample(tree, scan, _uv(tree, u, _math(tree, 'ADD', _math(tree, 'MULTIPLY', v, 2.0), 0.2 * t)), 'Green'),
              _sample(tree, scan, _uv(tree, u, _math(tree, 'ADD', v, 0.25 * t)), 'Green'))
    if f:
        d = _math(tree, 'ADD', d, _math(tree, 'MULTIPLY', _sample(tree, bg, _vector(tree, 'ADD', p, background_jitter).outputs['Vector'], 'Red'), f))
    shift = _math(tree, 'ADD', _math(tree, 'MULTIPLY', d, 0.01), k['glitch'])
    ud = _vector(tree, 'ADD', p, _uv(tree, shift, shift)).outputs['Vector']
    icon_d = _sample(tree, icon, ud, 'Red')
    splits = []
    if f:
        offsets = (k['offset_r'], k['offset_g'], k['offset_b'])
        s = [_math(tree, 'MULTIPLY', _sample(tree, icon, _vector(tree, 'ADD', p, (o[0], o[1], 0.0)).outputs['Vector'], 'Red'), 1.0, clamp=True)
             for o in offsets]
        mask = _math(tree, 'ADD', _math(tree, 'ADD', _math(tree, 'MULTIPLY', s[0], 0.3), _math(tree, 'MULTIPLY', s[1], 0.59)),
                     _math(tree, 'MULTIPLY', s[2], 0.11))
        mraw = _math(tree, 'ADD', _math(tree, 'MULTIPLY', mask, f), icon_d)
        splits = [_math(tree, 'ADD', 1.0, _math(tree, 'MULTIPLY', _math(tree, 'SUBTRACT', c, 1.0), f)) for c in s]
    else:
        mraw = icon_d
    m = _math(tree, 'MULTIPLY', mraw, 1.0, clamp=True)
    e = _math(tree, 'ADD', _math(tree, 'MULTIPLY', _sample(tree, bg, p, 'Blue'), 0.25), icon_d)
    e = _math(tree, 'ADD', e, _math(tree, 'MULTIPLY', _sample(tree, scan, _uv(tree, _math(tree, 'ADD', u, seed),
                                                                          _math(tree, 'ADD', _math(tree, 'MULTIPLY', v, 5.0), 0.1 * t)), 'Green'), 0.841))
    if f:
        e = _math(tree, 'ADD', e, _math(tree, 'MULTIPLY', _sample(tree, bg, _vector(tree, 'ADD', ud, background_jitter).outputs['Vector'], 'Red'), f))
    h = k['hue']
    hue = (0.00151763 + 0.99848235 * h, 0.43415365 - 0.42117062 * h, 1.0 - h)
    level = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', e, _math(tree, 'ADD', mraw, 1.0)), 10.0 * k['amplitude'])
    combine = _node(tree, 'ShaderNodeCombineColor')
    for slot in range(3):
        channel = _math(tree, 'MULTIPLY', level, hue[slot])
        if splits:
            channel = _math(tree, 'MULTIPLY', channel, splits[slot])
        tree.links.new(channel, combine.inputs[slot])
    alpha_in = _attribute(tree, 'fx_alpha').outputs['Fac']
    discard = _attribute(tree, 'fx_discard').outputs['Fac']
    alpha = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', alpha_in, m, clamp=True),
                  _math(tree, 'GREATER_THAN', m, _math(tree, 'SUBTRACT', discard, 1e-6)))
    # The game culls the icon's back faces (captured); Cycles has no culling, so the back face is transparent.
    front = _math(tree, 'SUBTRACT', 1.0, _node(tree, 'ShaderNodeNewGeometry').outputs['Backfacing'])
    _finish(tree, material, combine.outputs['Color'], _math(tree, 'MULTIPLY', alpha, front))
    return material


def make_rays_material(image, number):
    """The rays program c5646808 with one laser cone image: colour A P2 (1 + 5 r) (``fx_emit`` holds A P2), alpha sat(a r)."""
    from rc2016.pipeline.health_effect import _attribute, _math, _node
    material = _new_material('Holocon rays %d' % number)
    tree = material.node_tree
    r = _math(tree, 'MULTIPLY', _sample(tree, image, _node(tree, 'ShaderNodeTexCoord').outputs['UV'], 'Red'), 1.0, clamp=True)
    scale = _node(tree, 'ShaderNodeVectorMath', operation='SCALE')
    tree.links.new(_attribute(tree, 'fx_emit').outputs['Vector'], scale.inputs[0])
    tree.links.new(_math(tree, 'ADD', _math(tree, 'MULTIPLY', r, 5.0), 1.0), scale.inputs['Scale'])
    alpha_in = _attribute(tree, 'fx_alpha').outputs['Fac']
    discard = _attribute(tree, 'fx_discard').outputs['Fac']
    alpha = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', alpha_in, r, clamp=True),
                  _math(tree, 'GREATER_THAN', r, _math(tree, 'SUBTRACT', discard, 1e-6)))
    _finish(tree, material, scale.outputs['Vector'], alpha)
    return material


def make_glow_material():
    """The soft glow program (1fc6a696, the health crate's glow_soft) with its soft-particle term: g = sat(1.001 (1 - r)^10
    - 0.001), colour 2A P2 (0.45 g + 1) (``fx_emit`` holds 2A P2), alpha sat(a sat(g sat(2.5 dz / s))), s = attribute4.x =
    the glow size (captured on the vendor). dz, the depth from the glow to the surface behind it, is the distance along the
    view ray to the emitter's plane (``fx_plane_point``, ``fx_plane_normal``); where the ray leaves that plane it is 1."""
    from rc2016.pipeline.health_effect import _attribute, _math, _node, _vector
    material = _new_material('Holocon glow')
    tree = material.node_tree
    uv = _node(tree, 'ShaderNodeTexCoord').outputs['UV']
    r = _vector(tree, 'LENGTH', _vector(tree, 'SUBTRACT', uv, (0.5, 0.5, 0.0)).outputs['Vector']).outputs['Value']
    shape = _math(tree, 'SUBTRACT', _math(tree, 'MULTIPLY', _math(tree, 'POWER', _math(tree, 'SUBTRACT', 1.0, r), 10.0), 1.001001),
                  0.0010010011, clamp=True)
    geometry = _node(tree, 'ShaderNodeNewGeometry')
    normal = _attribute(tree, 'fx_plane_normal').outputs['Vector']
    to_plane = _vector(tree, 'SUBTRACT', _attribute(tree, 'fx_plane_point').outputs['Vector'], geometry.outputs['Position'])
    num = _vector(tree, 'DOT_PRODUCT', to_plane.outputs['Vector'], normal).outputs['Value']
    den = _math(tree, 'MULTIPLY', _vector(tree, 'DOT_PRODUCT', geometry.outputs['Incoming'], normal).outputs['Value'], -1.0)
    depth = _math(tree, 'DIVIDE', num, den)                        # along the ray beyond the glow, to the plane
    soft = _math(tree, 'DIVIDE', _math(tree, 'MULTIPLY', depth, 2.5), _attribute(tree, 'fx_soft_size').outputs['Fac'], clamp=True)
    toward = _math(tree, 'LESS_THAN', den, 0.0)                     # 1 where the ray continues toward the plane
    soft = _math(tree, 'ADD', _math(tree, 'MULTIPLY', soft, toward), _math(tree, 'SUBTRACT', 1.0, toward))
    alpha_in = _attribute(tree, 'fx_alpha').outputs['Fac']
    alpha = _math(tree, 'MULTIPLY', alpha_in, _math(tree, 'MULTIPLY', shape, soft, clamp=True), clamp=True)
    scale = _node(tree, 'ShaderNodeVectorMath', operation='SCALE')
    tree.links.new(_attribute(tree, 'fx_emit').outputs['Vector'], scale.inputs[0])
    tree.links.new(_math(tree, 'ADD', _math(tree, 'MULTIPLY', shape, 0.44999998807907104), 1.0), scale.inputs['Scale'])
    _finish(tree, material, scale.outputs['Vector'], alpha)
    return material


def _ray_mesh():
    """A unit quad with its apex edge on the origin and its length along +Y (v = 0 at the apex)."""
    import bpy
    mesh = bpy.data.meshes.get('Holocon ray quad')
    if mesh:
        return mesh
    mesh = bpy.data.meshes.new('Holocon ray quad')
    mesh.from_pydata([(-0.5, 0.0, 0.0), (0.5, 0.0, 0.0), (0.5, 1.0, 0.0), (-0.5, 1.0, 0.0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='UVMap')
    for loop, coord in zip(mesh.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
        uv.data[loop.index].uv = coord
    mesh.update()
    return mesh


def build(scene, anchor, description, texture_paths, camera=None):
    """Add the hologram for the object ``anchor`` (a device mesh whose matrix_world maps native model space to the scene).
    Returns the new objects. A second call replaces the first (objects carry ``holocon_effect``)."""
    import bpy
    from mathutils import Vector
    from rc2016.pipeline import health_effect as he
    for old in [o for o in bpy.data.objects if o.get(FAMILY)]:
        data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Light):
            bpy.data.lights.remove(data)
    kinds = {row['kind'] for row in description['quads']}
    # Only the images a material reads: an image without users is dropped on save, which the reopen check would see.
    needed = {row['laser'] for row in description['quads'] if row['kind'] == 'holo_rays'}
    if 'holo_icon' in kinds:
        needed |= {'icon', 'background', 'scan'}
    images = {}
    for role in sorted(needed):
        path = texture_paths[role]
        image = bpy.data.images.get(os.path.basename(path)) or bpy.data.images.load(path, check_existing=True)
        image.colorspace_settings.name = 'Non-Color'
        image.alpha_mode = 'NONE'
        image.pack()
        images[role] = image
    materials = {}
    if 'holo_icon' in kinds:
        materials['holo_icon'] = make_icon_material(images, description['icon'])
    for role in sorted({row['laser'] for row in description['quads'] if row['kind'] == 'holo_rays'}):
        materials[role] = make_rays_material(images[role], int(role[-1]))
    if 'glow_soft' in kinds:
        materials['glow_soft'] = make_glow_material()
    quad, ray = he._quad_mesh(), _ray_mesh()
    for mesh in (quad, ray):
        if not mesh.materials:
            mesh.materials.append(None)
    collection = next((c for c in anchor.users_collection), scene.collection)
    camera = camera or scene.camera
    world, rotation = anchor.matrix_world, anchor.matrix_world.to_3x3()
    fade = description.get('fade')
    pivot = bpy.data.objects.new('Holocon | origin', None)
    pivot.empty_display_type, pivot.empty_display_size = 'PLAIN_AXES', 0.1
    pivot.location = world @ Vector(description['origin'])
    pivot[FAMILY] = 'pivot'
    collection.objects.link(pivot)
    created = [pivot]
    for row in description['quads']:
        rays = row['kind'] == 'holo_rays'
        obj = bpy.data.objects.new('Holocon | ' + row['name'], ray if rays else quad)
        obj.location = world @ Vector(row['position'])
        obj.scale = (row['size'][0], row['size'][1], 1.0)
        obj[FAMILY] = row['kind']
        obj.pass_index = EFFECT_PASS_INDEX
        obj['fx_emit'] = [float(v) for v in row['emit']]
        obj['fx_alpha'] = float(row['alpha'])
        obj['fx_discard'] = float(row['discard'])
        if row.get('plane_normal'):
            obj['fx_soft_size'] = float(row['soft_size'])
            obj['fx_plane_point'] = list(world @ Vector(row['plane_point']))
            obj['fx_plane_normal'] = list((rotation @ Vector(row['plane_normal'])).normalized())
        for flag in ('visible_shadow', 'visible_diffuse', 'visible_glossy', 'visible_transmission', 'visible_volume_scatter'):
            setattr(obj, flag, False)   # a sprite is seen by the camera only
        collection.objects.link(obj)
        obj.material_slots[0].link = 'OBJECT'
        obj.material_slots[0].material = materials[row['laser'] if rays else row['kind']]
        if rays:
            axis = (rotation @ Vector(row['direction'])).normalized()
            obj.rotation_mode = 'QUATERNION'
            obj.rotation_quaternion = axis.to_track_quat('Y', 'Z')
        elif row.get('axes'):   # a fixed icon: the quad's x, y, z are the effect frame's
            from mathutils import Matrix
            columns = [(rotation @ Vector(a)).normalized() for a in row['axes']]
            obj.rotation_mode = 'QUATERNION'
            obj.rotation_quaternion = Matrix([[c[i] for c in columns] for i in range(3)]).to_quaternion()
        if camera is not None:
            if rays:   # an axial billboard: turned about its length toward the camera
                constraint = obj.constraints.new('LOCKED_TRACK')
                constraint.target, constraint.lock_axis, constraint.track_axis = camera, 'LOCK_Y', 'TRACK_Z'
            elif not row.get('axes'):
                constraint = obj.constraints.new('COPY_ROTATION')
                constraint.target = camera
            else:
                constraint = None
            if constraint is not None:
                constraint.name = 'Face camera'
            if fade:
                he._drive(obj, '["fx_alpha"]', he._fade_expression(row['alpha'], fade), [('d', camera, pivot)])
        created.append(obj)
    if description.get('light'):
        spec = dict(description['light'])
        light_pivot = bpy.data.objects.new('Holocon | light position', None)
        light_pivot.location = world @ Vector(spec['position'])
        light_pivot[FAMILY] = 'pivot'
        collection.objects.link(light_pivot)
        light = he._make_light(dict(light=spec, fade=fade), light_pivot, collection, camera)
        light.name = 'Holocon | light'
        del light[he.FAMILY]
        light[FAMILY] = 'light'
        created += [light_pivot, light]
    raise_transparent_bounces(scene, description)
    scene[FAMILY] = json.dumps(dict(effect=description['effect'], device=description['device'], limitations=description['limitations']))
    return created


def required_transparent_bounces(description):
    """Cycles transparent bounces the stacked quads need: a ray through every quad crosses all of them, and a ray that runs
    out of bounces ends black, which shows as black blocks in the rays where many quads overlap."""
    from rc2016.pipeline.health_effect import TRANSPARENT_BOUNCES
    return max(TRANSPARENT_BOUNCES, len(description['quads']) + 8)


def raise_transparent_bounces(scene, description):
    """Raise (never lower) the scene's Cycles transparent bounce limit to ``required_transparent_bounces``."""
    try:
        scene.cycles.transparent_max_bounces = max(scene.cycles.transparent_max_bounces, required_transparent_bounces(description))
    except AttributeError:
        pass


def retarget_camera(scene, camera):
    """Point every hologram object's Face camera constraint and distance-fade driver at ``camera``."""
    for obj in scene.objects:
        if not obj.get(FAMILY):
            continue
        for constraint in obj.constraints:
            if constraint.name == 'Face camera':
                constraint.target = camera
        for holder in (obj, obj.data):
            animation = getattr(holder, 'animation_data', None)
            for fcurve in (animation.drivers if animation else ()):
                for variable in fcurve.driver.variables:
                    if variable.type == 'LOC_DIFF':
                        variable.targets[0].id = camera
