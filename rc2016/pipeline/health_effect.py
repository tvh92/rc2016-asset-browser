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

Not built: the anamorphic flares (their centre is pulled 2.5 u toward the game camera and they are gated by a distance that
is not known, so they do not suit a free camera), the effect light, the soft-particle depth fade, the break effect, the
particle animation, and bloom (the game's bloom is a post-process, rc2016/viewer/bloom.py; enable the compositor's Glare
for it). ``bpy`` is imported inside the functions that need it, so the data helpers import anywhere.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

FAMILY = 'health_effect'


def effect_description(origin=None):
    """JSON-ready description of the effect for the Blender side: ``origin`` (native Y-up) and the quads, each with
    ``kind`` ('glow_soft', 'glow_spin', 'shockwave'), ``name``, ``offset`` (native, from the origin), ``size``,
    ``emit`` (the emission colour), ``alpha`` and ``discard``."""
    from rc2016.viewer import idle_effect as fx
    origin = tuple(origin) if origin is not None else fx.HEALTH_CRATE_LOCATOR
    amplitude = fx.AMPLITUDE
    quads = []
    for row in fx.SPRITES:
        if row['kind'] == fx.GLOW_SPIN:
            quads.append(dict(kind='glow_spin', name='spin glow (component %d)' % row['component'], offset=(0.0, 0.0, 0.0),
                              size=row['size'], emit=[2.0 * c for c in row['colour']], alpha=row['alpha'], discard=row['discard']))
        elif row['kind'] == fx.GLOW_SOFT:
            quads.append(dict(kind='glow_soft', name='soft glow (component %d)' % row['component'], offset=(0.0, 0.0, 0.0),
                              size=row['size'], emit=[2.0 * amplitude * c for c in row['colour']], alpha=row['alpha'],
                              discard=row['discard']))
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
    return dict(effect=fx.EFFECT_PATH, locator=fx.LOCATOR, origin=origin, amplitude=amplitude, mask_weight=fx.MASK_WEIGHT[fx.GLOW_SOFT],
                quads=quads, limitations=list(fx.PREVIEW_LIMITATIONS) + [
                    'Blender: anamorphic flares, the effect light, the soft-particle depth fade, particle animation and bloom are not built; '
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
    """The emission-over-transparent material of one sprite kind. ``images``: role -> bpy image (spin and ring kinds)."""
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


def build(scene, anchor, description, texture_paths, camera=None):
    """Add the effect quads for the object `anchor` (a crate mesh whose matrix_world maps native model space to the scene).
    Returns the new objects. A second call replaces the first (objects carry ``health_effect``)."""
    import bpy
    from mathutils import Vector
    for old in [o for o in bpy.data.objects if o.get(FAMILY)]:
        bpy.data.objects.remove(old, do_unlink=True)
    images = {}
    for role, path in texture_paths.items():
        image = bpy.data.images.get(os.path.basename(path)) or bpy.data.images.load(path, check_existing=True)
        image.colorspace_settings.name = 'Non-Color'
        image.alpha_mode = 'NONE'
        image.pack()
        images[role] = image
    materials = {kind: make_material(kind, images, description['mask_weight']) for kind in ('glow_soft', 'glow_spin', 'shockwave')}
    mesh = _quad_mesh()
    collection = next((c for c in anchor.users_collection), scene.collection)
    origin = Vector(description['origin'])
    camera = camera or scene.camera
    created = []
    if not mesh.materials:
        mesh.materials.append(None)
    for row in description['quads']:
        obj = bpy.data.objects.new('Health effect | ' + row['name'], mesh)
        obj.location = anchor.matrix_world @ (origin + Vector(row['offset']))
        obj.scale = (row['size'][0], row['size'][1], 1.0)
        obj[FAMILY] = row['kind']
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
        created.append(obj)
    scene[FAMILY] = json.dumps(dict(effect=description['effect'], limitations=description['limitations']))
    return created


def retarget_camera(scene, camera):
    """Point every effect quad's Face camera constraint at `camera` (a group build makes its own camera)."""
    for obj in scene.objects:
        if obj.get(FAMILY):
            for constraint in obj.constraints:
                if constraint.name == 'Face camera':
                    constraint.target = camera


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


def _blender_main(argv):
    import bpy
    job = json.load(open(argv[0]))
    apply_to_scene(job['description'], job['textures'])
    bpy.ops.wm.save_as_mainfile(filepath=job['output'], compress=True, relative_remap=False)   # the quads' images are packed; the file's other paths stay as they were
    print('HEALTH EFFECT', job['output'], flush=True)


if __name__ == '__main__' and 'bpy' in sys.modules:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    _blender_main(sys.argv[sys.argv.index('--') + 1:])
