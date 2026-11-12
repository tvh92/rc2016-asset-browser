"""The Nefarious mech's rocket flame in a Blender scene: the two decoded flame meshes of
`rc2016/viewer/mech_flame_effect.py` with the flame program as nodes.

Without this an extracted `enm_nefarious_mech` has no blue flame under it: it is the effect `enm_nefarious_mech_rocket_flame`
that the mech's conduit starts at the locator `igLoc_thruster`, not model geometry.

What is built (every value is the viewer's, see mech_flame_effect.py there for what is decoded, captured and inferred):
- two meshes of `enm_nefarious_mech_rocket_flame.model`, one per mesh component, with their vertices already placed in native
  model space (locator frame, component offset, captured spin, scale);
- the program 61e4c390 as nodes: n = `hydro_noise_flame_03_sm`.r at (u, 0.5 v - 2 t), q = sat((1 - v - 0.15)^4) +
  (0.3 n - 0.15)(1 - v), m = sat(q), emission 93 A L (q^2, |q|^0.75, |q|^0.01) over Transparent by alpha sat(a m), with the
  4/255 discard (the game's SRC_ALPHA / ONE_MINUS_SRC_ALPHA); seen by the camera only, both faces;
- the objects are children of the mech mesh in native space and are then mounted on the thruster joint's bone (``mount``),
  so they follow its clips: at the rest pose they keep their native placement.

The noise image is written next to the output (``prepare_textures``) as an 8-bit PNG of the decoded top mip, Non-Color. Not
built: the noise scroll over time (the frame is still), mip filtering, the effect's two blue lights. ``bpy`` is imported
inside the functions that need it, so the data helpers import anywhere.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FAMILY = 'mech_flame_effect'
EFFECT_PASS_INDEX = 2                    # the health effect's Object Index: the bloom mask (health_effect.add_bloom) reads it
ROLES = ('mech_flame_noise',)


def effect_description(preview):
    """JSON-ready description for the Blender side from a viewer preview (mech_flame_effect.mech_flame_preview): ``joint``,
    ``flames`` (``name``, ``component``, ``vertices`` (native), ``uv`` (Blender's: v = 1 - the game's), ``triangles``,
    ``emit`` = 93 A L (grey, three), ``alpha``, ``discard``), ``time`` and ``limitations``."""
    from rc2016.viewer import mech_flame_effect as fx
    mesh = preview['mesh']
    flames = []
    for component in preview['components']:
        origin, rows = fx.instance_rows(preview, component)
        vertices = [[o + d for o, d in zip(origin, fx._rows_apply(rows, p))] for p in mesh['positions']]
        flames.append(dict(name='rocket flame (component %d)' % component['component'], component=component['component'],
                           vertices=vertices, uv=[[u, 1.0 - v] for u, v in mesh['uv']],
                           triangles=[list(t) for t in mesh['triangles']],
                           emit=[93.0 * preview['amplitude'] * component['intensity']] * 3, alpha=component['alpha'],
                           discard=fx.DISCARD))
    return dict(effect=preview['effect'], locator=preview['locator'], joint=preview.get('joint'), flames=flames,
                time=preview['frame_time'],
                limitations=list(fx.PREVIEW_LIMITATIONS) + [
                    'Blender: a still frame (no noise scroll); no mip filtering; the flames follow the thruster bone of the '
                    'clips (mounted at their rest placement).'])


def prepare_textures(archive, preview, directory):
    """Write the noise image as an 8-bit RGB PNG under ``directory``; returns role -> path."""
    import numpy as np
    from PIL import Image
    from rc2016.pipeline.holocon_effect import _decoded
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for role in ROLES:
        rgba = _decoded(archive, preview['texture_paths'][role])
        path = directory / ('%s.png' % role)
        Image.fromarray(np.clip(rgba[..., :3] * 255.0 + 0.5, 0, 255).astype(np.uint8), 'RGB').save(path)
        paths[role] = str(path)
    return paths


# ---------------------------------------------------------------------------------------------------------------------
# Blender side.

def make_flame_material(image, time=0.0):
    """The flame program 61e4c390 (mech_flame_effect.billboards' MECH_FLAME), alpha-over."""
    from rc2016.pipeline.health_effect import _attribute, _math, _node
    from rc2016.pipeline.holocon_effect import _finish, _new_material, _sample, _uv
    material = _new_material('Nefarious mech rocket flame')
    tree = material.node_tree
    split = _node(tree, 'ShaderNodeSeparateXYZ')
    tree.links.new(_node(tree, 'ShaderNodeTexCoord').outputs['UV'], split.inputs['Vector'])
    u, v = split.outputs['X'], _math(tree, 'SUBTRACT', 1.0, split.outputs['Y'])     # the game's v = 0 is the image's first row
    n = _sample(tree, image, _uv(tree, u, _math(tree, 'ADD', _math(tree, 'MULTIPLY', v, 0.5), -2.0 * time)), 'Red')
    s = _math(tree, 'SUBTRACT', 1.0, v)
    s0 = _math(tree, 'ADD', s, -0.15)
    s2 = _math(tree, 'MULTIPLY', s0, s0)
    q = _math(tree, 'ADD', _math(tree, 'MULTIPLY', s2, s2, clamp=True),
              _math(tree, 'MULTIPLY', _math(tree, 'ADD', _math(tree, 'MULTIPLY', n, 0.3), -0.15), s))
    m = _math(tree, 'MULTIPLY', q, 1.0, clamp=True)
    aq = _math(tree, 'MAXIMUM', _math(tree, 'ABSOLUTE', q), 1e-8)
    combine = _node(tree, 'ShaderNodeCombineXYZ')
    tree.links.new(_math(tree, 'MULTIPLY', q, q), combine.inputs[0])
    tree.links.new(_math(tree, 'POWER', aq, 0.75), combine.inputs[1])
    tree.links.new(_math(tree, 'POWER', aq, 0.01), combine.inputs[2])
    colour = _node(tree, 'ShaderNodeVectorMath', operation='MULTIPLY')
    tree.links.new(combine.outputs['Vector'], colour.inputs[0])
    tree.links.new(_attribute(tree, 'fx_emit').outputs['Vector'], colour.inputs[1])
    keep = _math(tree, 'GREATER_THAN', m, _math(tree, 'SUBTRACT', _attribute(tree, 'fx_discard').outputs['Fac'], 1e-6))
    alpha = _math(tree, 'MULTIPLY', _math(tree, 'MULTIPLY', _attribute(tree, 'fx_alpha').outputs['Fac'], m, clamp=True), keep)
    _finish(tree, material, colour.outputs['Vector'], alpha)
    for attribute, value in (('surface_render_method', 'BLENDED'), ('use_backface_culling', False)):
        try:
            setattr(material, attribute, value)
        except (AttributeError, TypeError):
            pass
    return material


def _flame_mesh(row):
    import bpy
    mesh = bpy.data.meshes.new('Nefarious mech ' + row['name'])
    mesh.from_pydata([tuple(v) for v in row['vertices']], [], [tuple(t) for t in row['triangles']])
    uv = mesh.uv_layers.new(name='UVMap')
    for loop in mesh.loops:
        uv.data[loop.index].uv = tuple(row['uv'][loop.vertex_index])
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.update()
    return mesh


def build(scene, anchor, description, texture_paths, rig=None, bone=None):
    """Add the flame for the mech mesh ``anchor`` (whose matrix_world maps native model space to the scene), mounted on
    ``bone`` of ``rig`` when both are given. Returns the new objects; a second call replaces the first."""
    import bpy
    from mathutils import Matrix
    for old in [o for o in bpy.data.objects if o.get(FAMILY)]:
        data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
    path = texture_paths['mech_flame_noise']
    image = bpy.data.images.get(os.path.basename(path)) or bpy.data.images.load(path, check_existing=True)
    image.colorspace_settings.name = 'Non-Color'
    image.alpha_mode = 'NONE'
    image.pack()
    material = make_flame_material(image, description.get('time', 0.0))
    collection = next((c for c in anchor.users_collection), scene.collection)
    created = []
    for row in description['flames']:
        mesh = _flame_mesh(row)
        mesh.materials.append(material)
        obj = bpy.data.objects.new('Nefarious mech | ' + row['name'], mesh)
        obj[FAMILY] = 'flame'
        obj.pass_index = EFFECT_PASS_INDEX
        obj['fx_emit'] = [float(v) for v in row['emit']]
        obj['fx_alpha'] = float(row['alpha'])
        obj['fx_discard'] = float(row['discard'])
        for flag in ('visible_shadow', 'visible_diffuse', 'visible_glossy', 'visible_transmission', 'visible_volume_scatter'):
            setattr(obj, flag, False)   # the flame is seen by the camera only
        collection.objects.link(obj)
        obj.parent = anchor
        obj.matrix_parent_inverse = Matrix.Identity(4)
        created.append(obj)
    bpy.context.view_layer.update()
    if rig is not None and bone:
        mount(created, rig, bone)
    scene[FAMILY] = json.dumps(dict(effect=description['effect'], limitations=description['limitations'],
                                    mounted=bool(rig is not None and bone)))
    return created


def mount(objects, rig, bone_name):
    """Re-parent ``objects`` to bone ``bone_name`` of ``rig`` keeping their current world matrix at the rest pose (the
    parent inverse is the bone's rest tail frame, as group_blend._mount_on_joint), so they follow the bone's clips."""
    from mathutils import Matrix
    bone = rig.data.bones.get(bone_name)
    if bone is None:
        return False
    rest = rig.matrix_world @ bone.matrix_local @ Matrix.Translation((0.0, bone.length, 0.0))
    for obj in objects:
        placed = obj.matrix_world.copy()
        obj.parent, obj.parent_type, obj.parent_bone = rig, 'BONE', bone_name
        obj.matrix_parent_inverse = rest.inverted()
        obj.matrix_basis = placed
    return True
