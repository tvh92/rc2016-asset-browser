"""A holocard's glow in a Blender scene: the decoded glow quad, ethereal cloud and sparkles of
`rc2016/viewer/holocard_effect.py` as additive emission quads.

Without this an extracted single card, RYNO card or card pack has no blue glow: it is the effect `pup_holocard_glow` that the
pickup's conduit starts at the locator `igLoc_fx_attach`, not model geometry.

What is built (every value is the viewer's, see holocard_effect.py there for what is decoded, captured and inferred):
- the glow quad: a mesh of the decoded size in the effect's local XY plane, its four corner normals leaning 45 degrees outward
  as custom split normals (Cycles interpolates them like the GPU), with the program cb954073 as nodes: the rim facing
  f = sat(N.I) (Cycles turns N toward the viewer on the back face, as the game does), the `fx_noise_holes_01` polar rings at the still frame and the
  `pup_holocard_glow_c` red and green channels (sRGB, as the BC7 sRGB texture decodes);
- the ethereal cloud: two camera-facing quads (Copy Rotation on the scene camera; the captured turn about the view axis is in
  the mesh) with m = sat(`smoke_ethereal_01`.r), the soft-depth term 1 as in the viewer;
- the sparkles: 17 camera-facing quads with m = sat(`glow_sparkle_01`.r);
- every quad adds 2A C m (1 + L) times alpha sat(a m) (the game's SRC_ALPHA / ONE), with the 4/255 discard; the added
  light carries a coverage of its brightest channel (capped at 1) so that it shows on the transparent film, exact over
  transparency or black and darkening what is behind by that coverage (``_finish_additive``); the camera-distance fade sat((60 - d) / 25) is a driver on
  every quad's ``fx_alpha``; the glow quad and the cloud follow the camera 0.2 u toward it (location drivers, the viewer's
  +0x7C reading), which also keeps them in front of the opaque card as in the game, where the card writes no depth. No
  light: the effect has none.

The images are written next to the output (``prepare_textures``) as 8-bit PNGs of the decoded top mips: the glow texture
tagged sRGB, the others Non-Color. Not built: the rings' scroll over time (the frame is still), mip filtering, component 0
and the strip program b6f67d16. ``bpy`` is imported inside the functions that need it, so the data helpers import anywhere.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

FAMILY = 'holocard_effect'
EFFECT_PASS_INDEX = 2                    # the health effect's Object Index: the bloom mask (health_effect.add_bloom) reads it
ROLES = ('holocard_glow', 'holocard_noise', 'ethereal', 'sparkle')


def effect_description(preview):
    """JSON-ready description for the Blender side from a viewer preview (holocard_effect.holocard_preview): ``origin``
    (native), ``axes`` (the effect frame's native x, y, z), ``quads`` (``kind`` 'holocard_glow' / 'ethereal' / 'sparkle',
    ``name``, ``position`` (native), ``size``, ``alpha``, ``discard``, ``emit`` = 2A C; the glow also ``corners`` (local x, y,
    uv, normal), the clouds ``angle``), ``time``, ``fade`` and ``limitations``."""
    from rc2016.viewer import holocard_effect as fx
    amplitude = preview['amplitude']
    axes = [list(fx._rows_apply(preview['rows'], axis)) for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))]
    glow = preview['glow']
    emit = lambda colour: [2.0 * amplitude * c for c in colour]   # noqa: E731
    quads = [dict(kind='holocard_glow', name='glow quad (component %d)' % glow['component'],
                  position=list(fx.effect_point(preview, glow['offset'])), size=glow['size'], alpha=glow['alpha'],
                  discard=fx.DISCARD, emit=emit(glow['colour']), pull=glow.get('pull', 0.0),
                  corners=[dict(xy=[x - glow['offset'][0], y - glow['offset'][1]], uv=list(uv), normal=list(n))
                           for (x, y, _z), uv, n in fx.glow_corners(preview)])]
    centre = quads[0]['position']
    for number, cloud in enumerate(preview['clouds']):
        quads.append(dict(kind='ethereal', name='ethereal cloud %d (component %d)' % (number, cloud['component']), position=centre,
                          size=cloud['size'], angle=cloud['angle'], alpha=cloud['alpha'], discard=fx.DISCARD, emit=emit(cloud['colour']),
                          pull=cloud.get('pull', 0.0)))
    for number, sparkle in enumerate(preview['sparkles']):
        quads.append(dict(kind='sparkle', name='sparkle %d (component %d)' % (number, sparkle['component']),
                          position=list(fx.effect_point(preview, sparkle['offset'])), size=sparkle['size'], alpha=sparkle['alpha'],
                          discard=fx.DISCARD, emit=emit(sparkle['colour'])))
    return dict(effect=preview['effect'], locator=preview['locator'], origin=list(preview['origin']), axes=axes, quads=quads,
                time=preview['frame_time'], fade=dict(distance=fx.FADE_DISTANCE, range=fx.FADE_RANGE),
                limitations=list(fx.PREVIEW_LIMITATIONS) + [
                    'Blender: a still frame (no ring scroll); no mip filtering; the glow and cloud stay in front of the opaque '
                    'card by their 0.2 u camera pull; the added light carries a coverage of its brightest channel (exact over '
                    'the transparent film, darkens the card behind by that coverage).'])


def prepare_textures(archive, preview, directory):
    """Write the glow's images as 8-bit RGB PNGs under ``directory``; returns role -> path."""
    import numpy as np
    from PIL import Image
    from rc2016.pipeline.holocon_effect import _decoded
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for role in ROLES:
        rgba = _decoded(archive, preview['texture_paths'][role])
        path = directory / ('holocard_%s.png' % role)
        Image.fromarray(np.clip(rgba[..., :3] * 255.0 + 0.5, 0, 255).astype(np.uint8), 'RGB').save(path)
        paths[role] = str(path)
    return paths


# ---------------------------------------------------------------------------------------------------------------------
# Blender side.

def _finish_additive(tree, material, colour, alpha):
    """Light added over what is behind (the game's SRC_ALPHA / ONE: added = colour x alpha), given a coverage so that it
    survives the review renders' transparent film: coverage c = min(1, brightest channel of the added light), emission =
    added / c, mixed over Transparent by c. Over a transparent or black background that is exactly the added light (as the
    viewer's straight-alpha thumbnails, shading.HDR_RESOLVE_FRAGMENT); over the card it darkens what is behind by c, which
    only matters where the glow is faint (where it is bright the tone curve saturates either way)."""
    from rc2016.pipeline.health_effect import _math, _node
    added = _node(tree, 'ShaderNodeVectorMath', operation='SCALE')
    tree.links.new(colour, added.inputs[0])
    tree.links.new(alpha, added.inputs['Scale'])
    split = _node(tree, 'ShaderNodeSeparateXYZ')
    tree.links.new(added.outputs['Vector'], split.inputs['Vector'])
    cover = _math(tree, 'MINIMUM', _math(tree, 'MAXIMUM', _math(tree, 'MAXIMUM', split.outputs['X'], split.outputs['Y']), split.outputs['Z']), 1.0)
    emitted = _node(tree, 'ShaderNodeVectorMath', operation='SCALE')
    tree.links.new(added.outputs['Vector'], emitted.inputs[0])
    tree.links.new(_math(tree, 'DIVIDE', 1.0, _math(tree, 'MAXIMUM', cover, 1e-6)), emitted.inputs['Scale'])
    emission = _node(tree, 'ShaderNodeEmission')
    tree.links.new(emitted.outputs['Vector'], emission.inputs['Color'])
    emission.inputs['Strength'].default_value = 1.0
    mix = _node(tree, 'ShaderNodeMixShader')
    tree.links.new(cover, mix.inputs['Fac'])
    tree.links.new(_node(tree, 'ShaderNodeBsdfTransparent').outputs['BSDF'], mix.inputs[1])
    tree.links.new(emission.outputs['Emission'], mix.inputs[2])
    tree.links.new(mix.outputs['Shader'], _node(tree, 'ShaderNodeOutputMaterial').inputs['Surface'])
    for attribute, value in (('surface_render_method', 'BLENDED'), ('use_backface_culling', False)):
        try:
            setattr(material, attribute, value)
        except (AttributeError, TypeError):
            pass


def _output(tree, material, m):
    """rgb = 2A C m (1 + L) with L = 1 (``fx_emit`` holds 2A C), alpha = sat(a m) with the discard, added."""
    from rc2016.pipeline.health_effect import _attribute, _math
    colour = _node_scale(tree, _attribute(tree, 'fx_emit').outputs['Vector'], _math(tree, 'MULTIPLY', m, 2.0))
    alpha_in = _attribute(tree, 'fx_alpha').outputs['Fac']
    discard = _attribute(tree, 'fx_discard').outputs['Fac']
    keep = _math(tree, 'MULTIPLY', _math(tree, 'GREATER_THAN', m, 0.001),
                 _math(tree, 'GREATER_THAN', m, _math(tree, 'SUBTRACT', discard, 1e-6)))
    alpha = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', alpha_in, m, clamp=True), keep)
    _finish_additive(tree, material, colour, alpha)


def _node_scale(tree, vector, scalar):
    from rc2016.pipeline.health_effect import _node
    node = _node(tree, 'ShaderNodeVectorMath', operation='SCALE')
    tree.links.new(vector, node.inputs[0])
    tree.links.new(scalar, node.inputs['Scale'])
    return node.outputs['Vector']


def make_glow_material(images, time=0.0):
    """The glow quad program cb954073 (holocard_effect.billboards' HOLOCARD_GLOW)."""
    from rc2016.pipeline.health_effect import _math, _node, _vector
    from rc2016.pipeline.holocon_effect import _channel, _new_material
    material = _new_material('Holocard glow quad')
    tree = material.node_tree
    split = _node(tree, 'ShaderNodeSeparateXYZ')
    tree.links.new(_node(tree, 'ShaderNodeTexCoord').outputs['UV'], split.inputs['Vector'])
    u, v = split.outputs['X'], _math(tree, 'SUBTRACT', 1.0, split.outputs['Y'])     # the game's v = 0 is the top
    pu, pv = _math(tree, 'SUBTRACT', u, 0.5), _math(tree, 'SUBTRACT', v, 0.5)
    r = _math(tree, 'SQRT', _math(tree, 'ADD', _math(tree, 'MULTIPLY', pu, pu), _math(tree, 'MULTIPLY', pv, pv)))
    angle = _math(tree, 'MULTIPLY', _math(tree, 'ARCTAN2', pv, pu), 0.15913431)
    noise = _channel(tree, _game_sample(tree, images['holocard_noise'], angle, _math(tree, 'SUBTRACT', r, 0.1 * time)), 'Red')
    glow = _game_sample(tree, images['holocard_glow'], u, v)
    # Cycles turns the shading normal toward the ray on a back face, as the game flips N when IsFrontFace is false.
    geometry = _node(tree, 'ShaderNodeNewGeometry')
    facing = _math(tree, 'MULTIPLY', _vector(tree, 'DOT_PRODUCT', geometry.outputs['Normal'], geometry.outputs['Incoming']).outputs['Value'],
                   1.0, clamp=True)
    e = _math(tree, 'SUBTRACT', 1.0, _math(tree, 'ABSOLUTE', _math(tree, 'SUBTRACT', 1.001, facing)))
    rings = _math(tree, 'ADD', _math(tree, 'MULTIPLY', noise, 1.1111112), -0.11111112, clamp=True)
    mask = _math(tree, 'ADD', _channel(tree, glow, 'Red'), _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', _channel(tree, glow, 'Green'), 0.5), rings))
    _output(tree, material, _math(tree, 'MULTIPLY', e, mask, clamp=True))
    return material


def _game_sample(tree, image, u, v):
    """The colour of ``image`` at a game texture coordinate (u, v), v = 0 at the image's first row (Blender's v = 1)."""
    from rc2016.pipeline.health_effect import _image, _math
    from rc2016.pipeline.holocon_effect import _uv
    return _image(tree, image, _uv(tree, u, _math(tree, 'SUBTRACT', 1.0, v)))


def make_sprite_material(kind, image):
    """The cloud (06ceb217, soft-depth term 1) or sparkle (59399868) program: m = sat(image.r)."""
    from rc2016.pipeline.health_effect import _image, _math, _node
    from rc2016.pipeline.holocon_effect import _channel, _new_material
    material = _new_material('Holocard ' + ('ethereal cloud' if kind == 'ethereal' else 'sparkle'))
    tree = material.node_tree
    colour = _image(tree, image, _node(tree, 'ShaderNodeTexCoord').outputs['UV'])
    _output(tree, material, _math(tree, 'MULTIPLY', _channel(tree, colour, 'Red'), 1.0, clamp=True))
    return material


def _glow_mesh(row):
    """The glow quad at its real size in the effect's local XY plane, with the corner normals as custom split normals."""
    import bpy
    mesh = bpy.data.meshes.new('Holocard glow quad')
    corners = row['corners']
    mesh.from_pydata([(c['xy'][0], c['xy'][1], 0.0) for c in corners], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='UVMap')
    for loop, corner in zip(mesh.loops, corners):
        uv.data[loop.index].uv = (corner['uv'][0], 1.0 - corner['uv'][1])   # Blender's v = 0 is the image's last row
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.normals_split_custom_set([tuple(corners[loop.vertex_index]['normal']) for loop in mesh.loops])
    mesh.update()
    return mesh


def _sprite_mesh(name, size, angle=0.0):
    """A camera-facing quad of full ``size`` in local XY, turned by ``angle`` about local Z (the view axis)."""
    import bpy
    mesh = bpy.data.meshes.new(name)
    h, c, s = size * 0.5, math.cos(angle), math.sin(angle)
    points = [(-h, -h), (h, -h), (h, h), (-h, h)]
    mesh.from_pydata([(c * x - s * y, s * x + c * y, 0.0) for x, y in points], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='UVMap')
    for loop, coord in zip(mesh.loops, ((0, 1), (1, 1), (1, 0), (0, 0))):   # game v up the quad: Blender v = 1 - v
        uv.data[loop.index].uv = coord
    mesh.update()
    return mesh


def build(scene, anchor, description, texture_paths, camera=None):
    """Add the glow for the object ``anchor`` (a card mesh whose matrix_world maps native model space to the scene). Returns
    the new objects. A second call replaces the first (objects carry ``holocard_effect``)."""
    import bpy
    from mathutils import Matrix, Vector
    from rc2016.pipeline import health_effect as he
    for old in [o for o in bpy.data.objects if o.get(FAMILY)]:
        data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
    images = {}
    for role in ROLES:
        path = texture_paths[role]
        image = bpy.data.images.get(os.path.basename(path)) or bpy.data.images.load(path, check_existing=True)
        image.colorspace_settings.name = 'sRGB' if role == 'holocard_glow' else 'Non-Color'
        image.alpha_mode = 'NONE'
        image.pack()
        images[role] = image
    materials = dict(holocard_glow=make_glow_material(images, description.get('time', 0.0)),
                     ethereal=make_sprite_material('ethereal', images['ethereal']),
                     sparkle=make_sprite_material('sparkle', images['sparkle']))
    collection = next((c for c in anchor.users_collection), scene.collection)
    camera = camera or scene.camera
    world, rotation = anchor.matrix_world, anchor.matrix_world.to_3x3()
    fade = description.get('fade')
    pivot = bpy.data.objects.new('Holocard | origin', None)
    pivot.empty_display_type, pivot.empty_display_size = 'PLAIN_AXES', 0.1
    pivot.location = world @ Vector(description['origin'])
    pivot[FAMILY] = 'pivot'
    collection.objects.link(pivot)
    created = [pivot]
    columns = [(rotation @ Vector(a)).normalized() for a in description['axes']]
    frame = Matrix([[c[i] for c in columns] for i in range(3)]).to_quaternion()
    for row in description['quads']:
        kind = row['kind']
        if kind == 'holocard_glow':
            mesh = _glow_mesh(row)
        else:
            mesh = _sprite_mesh('Holocard ' + row['name'], row['size'], row.get('angle', 0.0))
        mesh.materials.append(materials[kind])
        obj = bpy.data.objects.new('Holocard | ' + row['name'], mesh)
        obj.location = world @ Vector(row['position'])
        obj[FAMILY] = kind
        obj.pass_index = EFFECT_PASS_INDEX
        obj['fx_emit'] = [float(v) for v in row['emit']]
        obj['fx_alpha'] = float(row['alpha'])
        obj['fx_discard'] = float(row['discard'])
        for flag in ('visible_shadow', 'visible_diffuse', 'visible_glossy', 'visible_transmission', 'visible_volume_scatter'):
            setattr(obj, flag, False)   # a sprite is seen by the camera only
        collection.objects.link(obj)
        if kind == 'holocard_glow':   # held in the effect frame
            obj.rotation_mode = 'QUATERNION'
            obj.rotation_quaternion = frame
        elif camera is not None:
            constraint = obj.constraints.new('COPY_ROTATION')
            constraint.target = camera
            constraint.name = 'Face camera'
        if camera is not None and fade:
            he._drive(obj, '["fx_alpha"]', he._fade_expression(row['alpha'], fade), [('d', camera, pivot)])
        if camera is not None and row.get('pull'):
            _pull_toward(obj, camera, obj.location.copy(), float(row['pull']))
        created.append(obj)
    raise_transparent_bounces(scene, description)
    scene[FAMILY] = json.dumps(dict(effect=description['effect'], limitations=description['limitations']))
    return created


def _pull_toward(obj, camera, base, distance):
    """Drivers that keep ``obj`` at ``base`` moved ``distance`` toward the camera (the components' +0x7C offset, as the
    viewer's billboards): location = base + distance (camera - base) / |camera - base|, simple expressions only."""
    terms = ['(c%s - %r)' % (axis, float(base[i])) for i, axis in enumerate('xyz')]
    length = 'max(1e-06, sqrt(%s))' % ' + '.join('%s * %s' % (t, t) for t in terms)
    for index, axis in enumerate('xyz'):
        driver = obj.driver_add('location', index).driver
        driver.type = 'SCRIPTED'
        for name in 'xyz':
            variable = driver.variables.new()
            variable.name, variable.type = 'c' + name, 'TRANSFORMS'
            target = variable.targets[0]
            target.id, target.transform_type, target.transform_space = camera, 'LOC_' + name.upper(), 'WORLD_SPACE'
        driver.expression = '%r + %r * %s / %s' % (float(base[index]), distance, terms[index], length)


def required_transparent_bounces(description):
    """Cycles transparent bounces the stacked quads need (as holocon_effect.required_transparent_bounces)."""
    from rc2016.pipeline.health_effect import TRANSPARENT_BOUNCES
    return max(TRANSPARENT_BOUNCES, len(description['quads']) + 8)


def raise_transparent_bounces(scene, description):
    """Raise (never lower) the scene's Cycles transparent bounce limit to ``required_transparent_bounces``."""
    try:
        scene.cycles.transparent_max_bounces = max(scene.cycles.transparent_max_bounces, required_transparent_bounces(description))
    except AttributeError:
        pass


def retarget_camera(scene, camera):
    """Point every glow object's Face camera constraint and distance-fade driver at ``camera``."""
    for obj in scene.objects:
        if not obj.get(FAMILY):
            continue
        for constraint in obj.constraints:
            if constraint.name == 'Face camera':
                constraint.target = camera
        animation = getattr(obj, 'animation_data', None)
        for fcurve in (animation.drivers if animation else ()):
            for variable in fcurve.driver.variables:
                if variable.type in ('LOC_DIFF', 'TRANSFORMS'):   # the fade distance and the camera pull
                    variable.targets[0].id = camera
