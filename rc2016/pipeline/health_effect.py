"""The health (nanotech) crate's idle effect in a Blender scene: camera-facing emission quads built from the decoded
sprite programs of `rc2016/viewer/idle_effect.py`.

Without this the extracted crate is an empty glass box: the glowing ball inside is not model geometry but the effect
`pup_health_crate_idle.visualeffect`, started at the model locator `igLoc_health_FX`.

What is built (every value is the viewer's, see idle_effect.py for what is decoded and what is inferred):
- the spin glow (program 41E80E53) and the soft glow (EBC94A93) at the locator, and the emitters' orb glows (EBC94A93) and
  rings (094D0CAE) of a still frame at effect age 4 s with seeded directions (inferred stand-ins);
- each as a 1 x 1 quad scaled to the sprite size, with a Copy Rotation constraint on the scene camera so it faces it, an
  emission shader (the program's colour terms) mixed with a transparent shader by the program's alpha, and the
  object's colour / alpha / discard threshold as custom properties (``fx_emit``, ``fx_alpha``, ``fx_discard``);
- the sprite textures as images next to the output (``prepare_textures``): the dissolve noise at mip 4 (a 32 x 32 box
  filter; the game samples it at LOD about 4.5 and Cycles has no mip chain), the spin image and the ring.

The anamorphic flares (the viewer's five flare quads, kinds 1 and 2) hang under a pivot empty at the locator that tracks the
camera; they sit ``FLARE_PULL`` (2.5 u, decoded) toward it, as in the game. Cycles has no depth test, so they are occluded only
by geometry inside that 2.5 u; the game draws them with the depth test off. Every quad's alpha carries the camera-distance fade
sat((100 - d) / 25) as a driver on ``fx_alpha`` (the distance from the scene camera to the pivot).

The effect light (viewer: idle_effect.billboards) is a Cycles point light at the locator, unshadowed like the viewer's. Its colour
is the captured light-record colour (hue decoded, magnitude captured) and its falloff the viewer's (1 - d / R)^4 with R = 1 u
(inferred), built from Light Falloff nodes (the Linear output at strength 1 is d, the Constant output cancels Cycles' inverse
square). The light energy is 4 pi^2 x the record colour, so that a white diffuse surface facing it returns record colour x
falloff, the viewer's ``albedo x colour x w x N.L`` (a calibration render in Cycles: P / (4 pi^2 d^2)), and carries the same
distance fade as a driver.

Bloom (``add_bloom``): compositor nodes that add the game's bloom of the effect to the health crate only. The effect quads carry
Object Index 2; a mask on it feeds the decoded bright pass (rc2016/viewer/bloom.py: channels clamped at 10.891, gate
sat(exposure L / 8 - 0.5) and chroma c / L, the exposure being the tonemap's gain) and one Gaussian for the decoded sprite chain,
measured on a point source through the decoded pyramid (weight 1, sigma 10.9 px at 1080p), scaled with the output height (drivers
on the blur sizes). With the game tonemap the result goes into the ``Bloom`` input of its compositor group, which adds it to the tone
curve output before the sRGB encode, as the game's composite does. Without it the bloom is added in scene-linear light before the
file's view transform, so its strength is a stand-in. Nothing else in the scene blooms. The game's other chain (the lit scene:
weight 0.82, sigma 76 px) is left out: the review lighting makes metal highlights pass its gate, and they would haze the crate. The
mask follows the sprites' stochastic transparency, so its edge is as noisy as the render.

With ``raise_bounces`` (the add_health_effect tool) Cycles' transparent bounce limit is raised to 64: the sprites stack more layers than its default (6 in a pipeline file) allows, and the layers past the limit render black, which dims the orb's core.

Not built: the soft-particle depth fade, the break effect, the particle animation, the flares' depth-test-off and the gate that
removes the round flares in far captures (unknown), and the bloom's exact pyramid (see above). ``bpy`` is imported inside the
functions that need it, so the data helpers import anywhere.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

FAMILY = 'health_effect'
EFFECT_PASS_INDEX = 2                            # Object Index of the effect quads, which the bloom mask reads
GAME_EXPOSURE = 4.553783893585205                # levels.display.EXPOSURE_MULTIPLIER, the viewer's bloom gate exposure
SIGMA_SPRITE_PX, SIGMA_LIT_PX = 10.9, 75.9       # decoded chains' spread on a point source, at the game's 1080p height
LIT_WEIGHT, SPRITE_WEIGHT = 0.82, 1.0            # the lit-scene chain is not built (see the docstring)
GAME_HEIGHT = 1080.0
LAYER_GAP = 0.002                                # u between the quads that share a plane, in draw order toward the camera (coplanar layers stripe in Cycles)
TRANSPARENT_BOUNCES = 64                         # Cycles transparent bounces needed for the stacked sprite layers
CHANNEL_CLAMP = 10.891                           # the bloom downsample's sat(0.09182 c) x 10.891 (rc2016/viewer/bloom.py)
GAME_VIEW_NODE = 'RC PS4 Game View (compositor)'  # rc2016/pipeline/game_view_compositor.py: its group node and "Bloom" input


def effect_description(origin=None, flares=True, light=True, raise_bounces=True):
    """JSON-ready description of the effect for the Blender side: ``origin`` (native Y-up), the quads, each with
    ``kind`` ('glow_soft', 'glow_spin', 'shockwave', 'flare_round', 'flare_square'), ``name``, ``offset`` (native, from the
    origin), ``size``, ``emit`` (the emission colour), ``alpha`` and ``discard``; the flares carry ``flare`` True (their
    offset is the pull toward the camera). ``light`` is the effect light (hue, colour, radius, unit scale) or None; ``fade`` is
    the camera-distance fade (distance, range). ``raise_bounces`` asks ``build`` to raise Cycles' transparent bounce limit; the
    pipeline's audited build leaves its defaults (6 bounces, no lights) alone and passes False for it, with ``flares`` and ``light`` False."""
    from rc2016.viewer import idle_effect as fx
    origin = tuple(origin) if origin is not None else fx.HEALTH_CRATE_LOCATOR
    amplitude = fx.AMPLITUDE
    quads = []
    for row in fx.SPRITES:
        if row['kind'] == fx.GLOW_SPIN:
            quads.append(dict(kind='glow_spin', name='spin glow (component %d)' % row['component'], offset=(0.0, 0.0, 0.0),
                              size=row['size'], emit=[2.0 * c for c in row['colour']], alpha=row['alpha'], discard=row['discard'],
                              layer=True))
        elif row['kind'] == fx.GLOW_SOFT:
            quads.append(dict(kind='glow_soft', name='soft glow (component %d)' % row['component'], offset=(0.0, 0.0, LAYER_GAP),
                              size=row['size'], emit=[2.0 * amplitude * c for c in row['colour']], alpha=row['alpha'],
                              discard=row['discard'], layer=True))
    if flares:
        kinds = {fx.FLARE_ROUND: 'flare_round', fx.FLARE_SQUARE: 'flare_square'}
        for number, row in enumerate(r for r in fx.SPRITES if r['flare']):
            quads.append(dict(kind=kinds[row['kind']], name='flare (component %d)' % row['component'],
                              offset=(0.0, 0.0, fx.FLARE_PULL + number * LAYER_GAP),
                              size=row['size'], emit=[2.0 * amplitude * c for c in row['colour']], alpha=row['alpha'],
                              discard=row['discard'], flare=True))
    for p in fx.particles():
        c = p['colour']
        if p['emitter'] == 'ring':
            k = amplitude * c[0]
            emit = [2.0 * k * (2.0 * k + 1.0), 2.0 * amplitude * c[1] * (2.0 * k + 1.0), 2.0 * amplitude * c[2] * (2.0 * k + 1.0)]
            kind = 'shockwave'
        else:
            emit = [2.0 * amplitude * v for v in c]
            kind = 'glow_soft'
        quads.append(dict(kind=kind, name='%s (component %d)' % (p['emitter'], p['component']), offset=tuple(p['offset']),
                          size=p['size'], emit=emit, alpha=p['alpha'], discard=p['discard']))
    # The viewer draws the flares after the particles (idle_effect.billboards); the particle order inside a kind is Cycles' own.
    quads.sort(key=lambda q: 1 if q.get('flare') else 0)
    effect_light = dict(hue=list(fx.LIGHT_HUE), colour=list(fx.LIGHT_COLOUR), radius=fx.LIGHT_RADIUS, unit_scale=fx.LIGHT_UNIT_SCALE) if light else None
    return dict(effect=fx.EFFECT_PATH, locator=fx.LOCATOR, origin=origin, amplitude=amplitude, mask_weight=fx.MASK_WEIGHT[fx.GLOW_SOFT],
                mask_weights=dict(glow_soft=fx.MASK_WEIGHT[fx.GLOW_SOFT], flare_square=fx.MASK_WEIGHT[fx.FLARE_SQUARE]),
                flare_pull=fx.FLARE_PULL, raise_bounces=bool(raise_bounces), fade=dict(distance=fx.FADE_DISTANCE, range=fx.FADE_RANGE), light=effect_light,
                quads=quads, limitations=list(fx.PREVIEW_LIMITATIONS) + [
                    'Blender: the soft-particle depth fade, the flares\' depth-test-off, particle animation and the lit-scene bloom chain are not built; '
                    'the flares and the effect light are built from the same values as the viewer (the light radius, falloff and units are inferred); '
                    'the bloom is a Gaussian stand-in for the decoded pyramid; '
                    'the noise image is its mip 4 (the game samples about LOD 4.5; Cycles has no mip chain).'])


# ---------------------------------------------------------------------------------------------------------------------
# Textures (plain Python): decoded BC4 images as PNG files.

def _bc4(raw, width, height):
    import numpy as np
    blocks = np.frombuffer(raw, np.uint8).reshape(-1, 8)
    e0, e1 = blocks[:, 0].astype(np.float32), blocks[:, 1].astype(np.float32)
    bits = np.zeros(len(blocks), np.uint64)
    for i in range(6):
        bits |= blocks[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    palette = np.zeros((len(blocks), 8), np.float32)
    palette[:, 0], palette[:, 1] = e0, e1
    eight = e0 > e1
    for k in range(1, 7):
        palette[:, 1 + k] = np.where(eight, ((7 - k) * e0 + k * e1) / 7.0, np.where(k <= 4, ((5 - k) * e0 + k * e1) / 5.0, 0.0))
    palette[:, 6] = np.where(eight, palette[:, 6], 0.0)
    palette[:, 7] = np.where(eight, palette[:, 7], 255.0)
    out = np.zeros((height, width), np.float32)
    per_row = (width + 3) // 4
    by, bx = np.divmod(np.arange(len(blocks)), per_row)
    for px in range(16):
        index = ((bits >> np.uint64(3 * px)) & np.uint64(7)).astype(np.int64)
        out[by * 4 + px // 4, bx * 4 + px % 4] = np.take_along_axis(palette, index[:, None], 1)[:, 0] / 255.0
    return out


def texture_images(archive):
    """role -> float32 (h, w) arrays of the effect's three sprite images: noise (mip 4), spin, ring."""
    import numpy as np
    from rc2016.viewer.idle_effect import TEXTURES
    from rc2016.viewer.textures import load_texture
    images = {}
    for role, path in TEXTURES.items():
        texture = load_texture(archive, path)
        if texture['format'] != 80:
            raise ValueError('%s is not BC4: format %s' % (path, texture['format']))
        images[role] = _bc4(texture['slices'][0], texture['width'], texture['height'])
    n = images['noise']
    mip = n.reshape(32, n.shape[0] // 32, 32, n.shape[1] // 32).mean(axis=(1, 3))
    images['noise'] = np.asarray(mip, np.float32)
    return images


def prepare_textures(archive, directory):
    """Write the three images as 8-bit greyscale PNGs under `directory`; returns role -> path."""
    import numpy as np
    from PIL import Image
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for role, image in texture_images(archive).items():
        path = directory / ('health_effect_%s.png' % role)
        Image.fromarray(np.clip(image * 255.0 + 0.5, 0, 255).astype(np.uint8), 'L').save(path)
        paths[role] = str(path)
    return paths


# ---------------------------------------------------------------------------------------------------------------------
# Blender side.

def _node(tree, kind, location=None, **properties):
    node = tree.nodes.new(kind)
    for key, value in properties.items():
        setattr(node, key, value)
    return node


def _math(tree, operation, a=None, b=None, clamp=False):
    node = _node(tree, 'ShaderNodeMath', operation=operation, use_clamp=clamp)
    for slot, value in enumerate((a, b)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            node.inputs[slot].default_value = float(value)
        else:
            tree.links.new(value, node.inputs[slot])
    return node.outputs[0]


def _vector(tree, operation, a, b=None):
    node = _node(tree, 'ShaderNodeVectorMath', operation=operation)
    for slot, value in enumerate((a, b)):
        if value is None:
            continue
        if isinstance(value, (tuple, list)):
            node.inputs[slot].default_value = tuple(value)
        else:
            tree.links.new(value, node.inputs[slot])
    return node


def _attribute(tree, name):
    return _node(tree, 'ShaderNodeAttribute', attribute_type='OBJECT', attribute_name='["%s"]' % name)


def _image(tree, image, vector, extension='REPEAT'):
    node = _node(tree, 'ShaderNodeTexImage', image=image, interpolation='Linear', extension=extension)
    tree.links.new(vector, node.inputs['Vector'])
    return node.outputs['Color']   # greyscale PNG, Non-Color: the R channel


def _separate_r(tree, colour):
    node = _node(tree, 'ShaderNodeSeparateColor')
    tree.links.new(colour, node.inputs['Color'])
    return node.outputs['Red']


def make_material(kind, images, mask_weight):
    """The emission-over-transparent material of one sprite kind. ``images``: role -> bpy image (spin and ring kinds);
    ``mask_weight``: the kind's mask weight (the soft glow's 0.45, the square flare's 1.0)."""
    import bpy
    name = 'Health effect ' + kind.replace('_', ' ')
    material = bpy.data.materials.get(name)
    if material:
        return material
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    uv = _node(tree, 'ShaderNodeTexCoord').outputs['UV']
    centred = _vector(tree, 'SUBTRACT', uv, (0.5, 0.5, 0.0))
    r = _vector(tree, 'LENGTH', centred.outputs['Vector']).outputs['Value']
    alpha_in = _attribute(tree, 'fx_alpha').outputs['Fac']
    discard = _attribute(tree, 'fx_discard').outputs['Fac']
    if kind == 'glow_soft':
        shape = _math(tree, 'SUBTRACT', _math(tree, 'MULTIPLY', _math(tree, 'POWER', _math(tree, 'SUBTRACT', 1.0, r), 10.0), 1.001001),
                      0.0010010011, clamp=True)
        strength = _math(tree, 'ADD', _math(tree, 'MULTIPLY', shape, mask_weight), 1.0)
        alpha = _math(tree, 'MULTIPLY', alpha_in, shape, clamp=True)
    elif kind == 'flare_round':   # 5FE9EF08: discard g < 1e-3; rgb = 2A P2, alpha = a g
        shape = _math(tree, 'SUBTRACT', _math(tree, 'MULTIPLY', _math(tree, 'POWER', _math(tree, 'SUBTRACT', 1.0, r), 10.0), 1.001001),
                      0.0010010011, clamp=True)
        strength = 1.0
        alpha = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', alpha_in, shape), _math(tree, 'GREATER_THAN', shape, 0.001), clamp=True)
    elif kind == 'flare_square':  # B1310A54: q = sat(4 sat(0.4 u (1 - u)) sat(v (1 - v))); discard q < 1e-3; rgb = 2A P2 (1.0 q + 1)
        split = _node(tree, 'ShaderNodeSeparateXYZ')
        tree.links.new(uv, split.inputs['Vector'])
        u, v = split.outputs['X'], split.outputs['Y']
        across = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', u, _math(tree, 'SUBTRACT', 1.0, u)), 0.4, clamp=True)
        along = _math(tree, 'MULTIPLY', v, _math(tree, 'SUBTRACT', 1.0, v), clamp=True)
        shape = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', across, along), 4.0, clamp=True)
        strength = _math(tree, 'ADD', _math(tree, 'MULTIPLY', shape, mask_weight), 1.0)
        alpha = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', alpha_in, shape), _math(tree, 'GREATER_THAN', shape, 0.001), clamp=True)
    elif kind == 'glow_spin':
        split = _node(tree, 'ShaderNodeSeparateXYZ')
        tree.links.new(centred.outputs['Vector'], split.inputs['Vector'])
        angle = _math(tree, 'MULTIPLY', _math(tree, 'ARCTAN2', split.outputs['Y'], split.outputs['X']), 0.15913431)
        polar = _node(tree, 'ShaderNodeCombineXYZ')
        tree.links.new(angle, polar.inputs['X'])
        tree.links.new(_math(tree, 'MULTIPLY', r, 4.95), polar.inputs['Y'])
        noise = _separate_r(tree, _image(tree, images['noise'], polar.outputs['Vector']))
        shift = _node(tree, 'ShaderNodeCombineXYZ')
        scaled = _math(tree, 'MULTIPLY', noise, 0.1)
        tree.links.new(scaled, shift.inputs['X'])
        tree.links.new(scaled, shift.inputs['Y'])
        warped = _vector(tree, 'ADD', uv, shift.outputs['Vector'])
        spin = _separate_r(tree, _image(tree, images['spin'], warped.outputs['Vector']))
        falloff = _math(tree, 'MULTIPLY', _math(tree, 'SQRT', r), 1.5384616, clamp=True)
        shape = _math(tree, 'SUBTRACT', spin, _math(tree, 'MULTIPLY', spin, falloff), clamp=True)
        strength = 1.0
        alpha = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', alpha_in, shape),
                      _math(tree, 'GREATER_THAN', shape, _math(tree, 'SUBTRACT', discard, 1e-6)), clamp=True)
    elif kind == 'shockwave':
        ring = _separate_r(tree, _image(tree, images['ring'], uv, extension='CLIP'))
        shape = _math(tree, 'MULTIPLY', ring, 1.0, clamp=True)
        strength = 1.0
        alpha = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', alpha_in, shape),
                      _math(tree, 'GREATER_THAN', shape, _math(tree, 'SUBTRACT', discard, 1e-6)), clamp=True)
    else:
        raise ValueError(kind)
    emission = _node(tree, 'ShaderNodeEmission')
    tree.links.new(_attribute(tree, 'fx_emit').outputs['Color'], emission.inputs['Color'])
    if isinstance(strength, float):
        emission.inputs['Strength'].default_value = strength
    else:
        tree.links.new(strength, emission.inputs['Strength'])
    mix = _node(tree, 'ShaderNodeMixShader')
    tree.links.new(alpha, mix.inputs['Fac'])
    tree.links.new(_node(tree, 'ShaderNodeBsdfTransparent').outputs['BSDF'], mix.inputs[1])
    tree.links.new(emission.outputs['Emission'], mix.inputs[2])
    out = _node(tree, 'ShaderNodeOutputMaterial')
    tree.links.new(mix.outputs['Shader'], out.inputs['Surface'])
    for attribute, value in (('surface_render_method', 'BLENDED'), ('use_backface_culling', False)):
        try:
            setattr(material, attribute, value)
        except (AttributeError, TypeError):
            pass
    return material


def _quad_mesh():
    import bpy
    mesh = bpy.data.meshes.get('Health effect quad')
    if mesh:
        return mesh
    mesh = bpy.data.meshes.new('Health effect quad')
    mesh.from_pydata([(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='UVMap')
    for loop, coord in zip(mesh.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
        uv.data[loop.index].uv = coord
    mesh.update()
    return mesh


def _drive(obj, path, expression, variables, index=-1):
    """A scripted driver on ``obj[path]`` (a custom property: path ``["name"]``) or an RNA path with ``variables``:
    list of (name, camera, centre) for a LOC_DIFF distance."""
    fcurve = obj.driver_add(path, index) if index >= 0 else obj.driver_add(path)
    driver = fcurve.driver
    driver.type = 'SCRIPTED'
    for name, first, second in variables:
        variable = driver.variables.new()
        variable.name, variable.type = name, 'LOC_DIFF'
        variable.targets[0].id, variable.targets[1].id = first, second
    driver.expression = expression
    return driver


def _fade_expression(base, fade):
    """``base`` x sat((distance - d) / range), d the LOC_DIFF variable."""
    return '%r * max(0.0, min(1.0, (%r - d) / %r))' % (float(base), float(fade['distance']), float(fade['range']))


def _make_light(description, pivot, collection, camera):
    """The effect light as a Cycles point light at the pivot (see the module docstring); returns the object."""
    import bpy
    spec = description['light']
    colour = [float(v) for v in spec['colour']]
    peak = max(colour) or 1.0
    light = bpy.data.lights.new('Health effect light', 'POINT')
    light.color = tuple(v / peak for v in colour)
    light.shadow_soft_size = 0.0          # a point: the viewer's light is one
    light.use_shadow = False              # the viewer's light is unshadowed
    light.use_nodes = True
    tree = light.node_tree
    tree.nodes.clear()
    distance = _node(tree, 'ShaderNodeLightFalloff')           # at strength 1 the Linear output is d
    distance.inputs['Strength'].default_value = 1.0
    scaled = _math(tree, 'MULTIPLY', distance.outputs['Linear'], 1.0 / float(spec['radius']))
    window = _math(tree, 'MAXIMUM', _math(tree, 'SUBTRACT', 1.0, scaled), 0.0)
    weight = _math(tree, 'POWER', window, 4.0)                  # (1 - d / R)^4, the viewer's previewLightWeight
    falloff = _node(tree, 'ShaderNodeLightFalloff')
    tree.links.new(weight, falloff.inputs['Strength'])
    emission = _node(tree, 'ShaderNodeEmission')
    tree.links.new(falloff.outputs['Constant'], emission.inputs['Strength'])   # Constant cancels the inverse square
    tree.links.new(emission.outputs['Emission'], _node(tree, 'ShaderNodeOutputLight').inputs['Surface'])
    # A white diffuse surface facing a point light returns energy x strength / (4 pi^2 d^2) in Cycles (calibration render), so
    # energy = 4 pi^2 x the record colour gives the viewer's albedo x colour x w x N.L.
    energy = 4.0 * math.pi ** 2 * peak * float(spec['unit_scale'])
    light.energy = energy
    obj = bpy.data.objects.new('Health effect | light', light)
    obj.location = pivot.location
    obj[FAMILY] = 'light'
    obj['fx_light_colour'] = colour
    collection.objects.link(obj)
    if camera is not None:
        _drive(light, 'energy', _fade_expression(energy, description['fade']), [('d', camera, pivot)])
    return obj


def build(scene, anchor, description, texture_paths, camera=None):
    """Add the effect for the object `anchor` (a crate mesh whose matrix_world maps native model space to the scene): the quads
    (the flares under a camera-tracking pivot), the effect light and the distance-fade drivers. Returns the new objects. A
    second call replaces the first (objects carry ``health_effect``)."""
    import bpy
    from mathutils import Vector
    for old in [o for o in bpy.data.objects if o.get(FAMILY)]:
        data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Light):
            bpy.data.lights.remove(data)
    images = {}
    for role, path in texture_paths.items():
        image = bpy.data.images.get(os.path.basename(path)) or bpy.data.images.load(path, check_existing=True)
        image.colorspace_settings.name = 'Non-Color'
        image.alpha_mode = 'NONE'
        image.pack()
        images[role] = image
    weights = description.get('mask_weights') or dict(glow_soft=description['mask_weight'], flare_square=1.0)
    kinds = sorted({row['kind'] for row in description['quads']})   # an unused material has no user and is dropped on save
    materials = {kind: make_material(kind, images, weights.get(kind, 0.0)) for kind in kinds}
    mesh = _quad_mesh()
    collection = next((c for c in anchor.users_collection), scene.collection)
    origin = Vector(description['origin'])
    camera = camera or scene.camera
    fade = description.get('fade')
    created = []
    if not mesh.materials:
        mesh.materials.append(None)
    centre = anchor.matrix_world @ origin
    pivot = None
    if any(row.get('flare') or row.get('layer') for row in description['quads']) or description.get('light') or fade:
        # The flares' pivot: at the locator, +Z toward the camera; the distance fade and the light read from it.
        pivot = bpy.data.objects.new('Health effect | centre', None)
        pivot.empty_display_type = 'PLAIN_AXES'
        pivot.empty_display_size = 0.1
        pivot.location = centre
        pivot[FAMILY] = 'pivot'
        collection.objects.link(pivot)
        if camera is not None:
            constraint = pivot.constraints.new('TRACK_TO')
            constraint.name = 'Face camera'
            constraint.target = camera
            constraint.track_axis, constraint.up_axis = 'TRACK_Z', 'UP_Y'
        created.append(pivot)
    for row in description['quads']:
        obj = bpy.data.objects.new('Health effect | ' + row['name'], mesh)
        if row.get('flare') or row.get('layer'):
            obj.parent = pivot
            obj.location = row['offset']        # along the pivot's camera axis: the flares' pull, or a layer gap
        else:
            obj.location = anchor.matrix_world @ (origin + Vector(row['offset']))
        obj.scale = (row['size'][0], row['size'][1], 1.0)
        obj[FAMILY] = row['kind']
        obj.pass_index = EFFECT_PASS_INDEX
        obj['fx_emit'] = [float(v) for v in row['emit']]
        obj['fx_alpha'] = float(row['alpha'])
        obj['fx_discard'] = float(row['discard'])
        for flag in ('visible_shadow', 'visible_diffuse', 'visible_glossy', 'visible_transmission', 'visible_volume_scatter'):
            setattr(obj, flag, False)   # a sprite is seen by the camera only
        collection.objects.link(obj)
        obj.material_slots[0].link = 'OBJECT'
        obj.material_slots[0].material = materials[row['kind']]
        if camera is not None:
            constraint = obj.constraints.new('COPY_ROTATION')
            constraint.name = 'Face camera'
            constraint.target = camera
            if fade and pivot is not None:
                _drive(obj, '["fx_alpha"]', _fade_expression(row['alpha'], fade), [('d', camera, pivot)])
        created.append(obj)
    if description.get('light') and pivot is not None:
        created.append(_make_light(description, pivot, collection, camera))
    if description.get('raise_bounces'):   # the sprites, panes and flares stack dozens of transparent layers; Cycles drops every layer past the limit
        try:
            scene.cycles.transparent_max_bounces = max(scene.cycles.transparent_max_bounces, TRANSPARENT_BOUNCES)
        except AttributeError:
            pass
    scene[FAMILY] = json.dumps(dict(effect=description['effect'], limitations=description['limitations']))
    return created


def retarget_camera(scene, camera):
    """Point every effect object's Face camera constraint and distance-fade driver at `camera` (a group build makes its own camera)."""
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


def apply_to_scene(description, texture_paths):
    """Build the effect on every health crate in the open scene: meshes whose name holds the crate's material
    ``gbl_gp_Crate_Nanotech`` (the body); the effect is built once, anchored on the first. Returns the quads."""
    import bpy
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    anchors = [o for o in scene.objects if o.type == 'MESH' and 'Crate_Nanotech' in o.name and not o.get(FAMILY)]
    if not anchors:
        raise RuntimeError('no health crate mesh (a name with Crate_Nanotech) in this scene')
    return build(scene, anchors[0], description, texture_paths)


def _mix(tree, blend, a, b, factor=1.0):
    node = _node(tree, 'ShaderNodeMix', data_type='RGBA', blend_type=blend)
    node.inputs['Factor'].default_value = factor
    for slot, value in ((6, a), (7, b)):
        if value is not None:
            tree.links.new(value, node.inputs[slot])
    return node.outputs[2]


def _game_view_node(tree):
    """The game tonemap's compositor group node when the scene has one with the Bloom input, else None."""
    node = tree.nodes.get(GAME_VIEW_NODE)
    return node if node is not None and 'Bloom' in node.inputs else None


def add_bloom(scene, exposure=None):
    """Add the health crate's bloom to the scene compositor (see the module docstring); returns the receipt dict.
    With the game tonemap (``apply_game_tonemap``) the bloom feeds its Bloom input, after the tone curve, and the gate uses its
    gain; otherwise it is spliced in between the render layer and whatever consumed its image, in scene-linear light, with the
    captured exposure (or `exposure`) as the gate gain."""
    import bpy
    quads = [o for o in scene.objects if o.get(FAMILY) and o.type == 'MESH']
    if not quads:
        raise RuntimeError('no health effect quads: build the effect first')
    scene.view_layers[0].use_pass_object_index = True
    tree = scene.compositing_node_group
    if tree is None:
        tree = bpy.data.node_groups.new('Health crate bloom', 'CompositorNodeTree')
        tree.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        scene.compositing_node_group = tree
    for node in [n for n in tree.nodes if n.name.startswith('Health bloom')]:
        tree.nodes.remove(node)
    layers = next((n for n in tree.nodes if n.bl_idname == 'CompositorNodeRLayers'), None) or _node(tree, 'CompositorNodeRLayers')
    image, alpha, index = layers.outputs['Image'], layers.outputs['Alpha'], layers.outputs['Object Index']
    game = _game_view_node(tree)
    if game is not None:
        source = game.inputs['Image'].links[0].from_socket if game.inputs['Image'].links else image
        image = source
        exposure = exposure or 2.0 ** float(game.inputs['Exposure (stops)'].default_value)
        consumers = []
    else:
        exposure = exposure or GAME_EXPOSURE
        consumers = [link.to_socket for link in list(image.links)]
        for link in list(image.links):
            tree.links.remove(link)
    height = scene.render.resolution_y * scene.render.resolution_percentage / 100.0
    luma_weights = (0.2126, 0.7152, 0.0722)
    total = None
    nodes_before = set(tree.nodes)
    for pass_index, weight, sigma in ((EFFECT_PASS_INDEX, SPRITE_WEIGHT, SIGMA_SPRITE_PX),):
        mask = _node(tree, 'CompositorNodeIDMask')
        mask.inputs['Index'].default_value = pass_index
        mask.inputs['Anti-Alias'].default_value = True
        tree.links.new(index, mask.inputs['ID value'])
        masked = _mix(tree, 'MULTIPLY', image, mask.outputs[0])
        clamped = _vector(tree, 'MINIMUM', masked, (CHANNEL_CLAMP,) * 3).outputs['Vector']   # the game's channel clamp before the gate
        colour = _vector(tree, 'SCALE', clamped, None)
        luma = _vector(tree, 'DOT_PRODUCT', clamped, luma_weights).outputs['Value']
        luma = _math(tree, 'MAXIMUM', luma, 1.0 / 1024.0)
        gate = _math(tree, 'MULTIPLY_ADD', luma, exposure * 0.125, clamp=True)
        gate.node.inputs[2].default_value = -0.5
        factor = _math(tree, 'MULTIPLY', _math(tree, 'DIVIDE', gate, luma), weight)
        tree.links.new(factor, colour.inputs['Scale'])
        blur = _node(tree, 'CompositorNodeBlur')
        size = 3.0 * sigma * height / GAME_HEIGHT        # Blender's Gaussian: sigma = size / 3 (measured)
        blur.inputs['Size'].default_value = (size, size)
        for axis in (0, 1):   # follow the output height, so a resolution percentage change keeps the bloom's screen size
            driver = blur.inputs['Size'].driver_add('default_value', axis).driver
            driver.type = 'SCRIPTED'
            for name, path in (('h', 'render.resolution_y'), ('p', 'render.resolution_percentage')):
                variable = driver.variables.new()
                variable.name, variable.type = name, 'SINGLE_PROP'
                variable.targets[0].id_type, variable.targets[0].id, variable.targets[0].data_path = 'SCENE', scene, path
            driver.expression = '%r * h * p / 100' % (3.0 * sigma / GAME_HEIGHT)
        tree.links.new(colour.outputs['Vector'], blur.inputs['Image'])
        total = blur.outputs[0] if total is None else _mix(tree, 'ADD', total, blur.outputs[0])
    if game is not None:
        tree.links.new(total, game.inputs['Bloom'])   # added to the tone-curve output before the sRGB encode, as the game's composite does
    else:
        summed = _mix(tree, 'ADD', image, total)
        split = _node(tree, 'CompositorNodeSeparateColor')
        tree.links.new(summed, split.inputs[0])
        combine = _node(tree, 'CompositorNodeCombineColor')
        for channel in range(3):
            tree.links.new(split.outputs[channel], combine.inputs[channel])
        tree.links.new(alpha, combine.inputs['Alpha'])
        for socket in consumers:
            tree.links.new(combine.outputs[0], socket)
        if not consumers:   # a fresh tree: the bloomed image is the output
            out = next((n for n in tree.nodes if n.bl_idname == 'NodeGroupOutput'), None) or _node(tree, 'NodeGroupOutput')
            tree.links.new(combine.outputs[0], out.inputs['Image'])
    for node in set(tree.nodes) - nodes_before:
        node.name = 'Health bloom | ' + node.bl_idname.replace('CompositorNode', '').replace('ShaderNode', '')
    scene.render.use_compositing = True
    scene['health_bloom'] = json.dumps(dict(exposure=exposure, sprite_sigma_px_1080=SIGMA_SPRITE_PX, lit_sigma_px_1080=SIGMA_LIT_PX,
                                            output_height=height, after_tone_curve=game is not None,
                                            limitation=('added to the game tone curve output; ' if game is not None else
                                                        'added in scene-linear light before the view transform; ') +
                                            'a Gaussian stand-in for the decoded pyramid'))
    return dict(quads=len(quads), output_height=height, after_tone_curve=game is not None)


def apply_game_tonemap(scene, keep_file_exposure=False):
    """Replace the file's view transform with the game's exposure and tone curve as compositor nodes (rc2016/pipeline/
    game_view_compositor.py, identity grade). The exposure is the captured multiplier alone, which is what the effect's
    brightness was decoded under (the orb core clips to white only there); ``keep_file_exposure`` adds the file's own exposure
    stops on top, as the pipeline's ``--tonemap game`` does for its HDRI review lighting."""
    from rc2016.pipeline import game_view_compositor
    from rc2016.pipeline.scene_defaults import game_tonemap_stops
    settings = json.loads(scene['shared_lighting']) if 'shared_lighting' in scene else {}
    if not keep_file_exposure:
        settings['exposure'] = 0.0
    settings.setdefault('exposure', 0.0)
    return game_view_compositor.apply(scene, exposure_stops=game_tonemap_stops(settings), grade='identity')


def repair_environment(repo_hdri=None):
    """Pack the shared HDRI so the file does not depend on a relative path (an unresolved one renders the world magenta):
    the image's own path when it exists, else `repo_hdri`. Returns the image name or None."""
    import bpy
    for image in bpy.data.images:
        if image.source != 'FILE' or image.packed_file or not image.name.lower().endswith('.exr'):
            continue
        path = Path(bpy.path.abspath(image.filepath))
        if not path.exists() and repo_hdri and Path(repo_hdri).exists():
            image.filepath = str(repo_hdri)
            path = Path(repo_hdri)
        if path.exists():
            image.pack()
            return image.name
    return None


def _blender_main(argv):
    import bpy
    job = json.load(open(argv[0]))
    apply_to_scene(job['description'], job['textures'])
    if job.get('game_tonemap', True):
        apply_game_tonemap(bpy.context.scene, job.get('keep_file_exposure', False))
    if job.get('bloom'):
        add_bloom(bpy.context.scene)
    if job.get('pack_environment', True):
        repair_environment(job.get('repo_hdri'))
    bpy.ops.wm.save_as_mainfile(filepath=job['output'], compress=True, relative_remap=False)   # the quads' images are packed; the file's other paths stay as they were
    print('HEALTH EFFECT', job['output'], flush=True)


if __name__ == '__main__' and 'bpy' in sys.modules:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    _blender_main(sys.argv[sys.argv.index('--') + 1:])
