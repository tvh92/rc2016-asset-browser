"""Blender side of the Rift Apart surface check: bake each surface output of an emitted material on a unit plane.

    blender --background --factory-startup --python rcra_blender_check.py -- --job job.json

job.json: {recipe, forward, fixed, images {binding: {path, colourspace}}, size, uv_origin, uv_span, out}; ``recipe`` and
``forward`` are optional, ``fixed`` holds the forward recipe's view inputs (VIEW/CAMERA) as constants. The plane spans UV
[origin, origin + span]^2 in Blender's first UV
map, faces +Z with Blender's tangent +X and bitangent +Y, and images are sampled bilinearly ('Linear', as exported). Each output is baked (Cycles
EMIT, 1 sample) into a float image; the pixels go to ``out`` as JSON {term: [[r, g, b] per pixel row-major from v = 0]}.
The caller compares them with ``rc2016.games.rcra.surface.evaluate_recipe`` for the same inputs.
"""
import argparse
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rc2016.export import rcra_blender  # noqa: E402

BAKES = (('albedo', 'Albedo'), ('f0', 'F0'), ('normal', 'Normal'), ('scalars', ('Gloss', 'Occlusion', 'Emission')),
         ('alpha', 'Alpha'))
FORWARD_BAKES = (('forward_colour', 'Colour'), ('forward_alpha', 'Alpha'))


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--job', required=True)
    job = json.loads(Path(args.parse_args(sys.argv[sys.argv.index('--') + 1:]).job).read_text(encoding='utf-8'))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    size = int(job.get('size', 64))
    mesh = bpy.data.meshes.new('plane')
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='UVMap')
    lo, span = job.get('uv_origin', 0.0), job.get('uv_span', 1.0)
    for loop, coord in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = (lo + span * coord[0], lo + span * coord[1])
    bake_uv = mesh.uv_layers.new(name='Bake')   # the bake target's layout: the whole image
    for loop, coord in zip(bake_uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = coord
    mesh.uv_layers.active = bake_uv
    obj = bpy.data.objects.new('plane', mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.cycles.filter_width = 0.01   # sample the pixel centre, where the reference is evaluated
    scene.render.bake.margin = 0
    images = rcra_blender.load_images(job['images'])
    groups = []
    if job.get('recipe'):
        groups.append((rcra_blender.build_surface_group(job['recipe'], images, ['UVMap']), BAKES))
    if job.get('forward'):
        groups.append((rcra_blender.build_forward_group(job['forward'], images, ['UVMap'], fixed=job.get('fixed')),
                       FORWARD_BAKES))
    material = bpy.data.materials.new('check')
    material.use_nodes = True
    obj.data.materials.append(material)
    tree = material.node_tree
    tree.nodes.clear()
    surface = tree.nodes.new('ShaderNodeGroup')
    output = tree.nodes.new('ShaderNodeOutputMaterial')
    emission = tree.nodes.new('ShaderNodeEmission')
    emission.inputs['Strength'].default_value = 1.0
    tree.links.new(emission.outputs[0], output.inputs['Surface'])
    target = tree.nodes.new('ShaderNodeTexImage')
    results = {}
    for group, bakes in groups:
        surface.node_tree = group
        bake(tree, surface, emission, target, bakes, size, results)
    Path(job['out']).write_text(json.dumps(results), encoding='utf-8')
    print('CHECKED', job['out'], flush=True)


def bake(tree, surface, emission, target, bakes, size, results):
    for name, sockets in bakes:
        for link in list(emission.inputs['Color'].links):
            tree.links.remove(link)
        if isinstance(sockets, tuple):
            combine = tree.nodes.new('ShaderNodeCombineXYZ')
            for k, socket in enumerate(sockets):
                tree.links.new(surface.outputs[socket], combine.inputs[k])
            tree.links.new(combine.outputs[0], emission.inputs['Color'])
        else:
            tree.links.new(surface.outputs[sockets], emission.inputs['Color'])
        image = bpy.data.images.new('bake_' + name, size, size, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        target.image = image
        tree.nodes.active = target
        bpy.ops.object.bake(type='EMIT')
        pixels = list(image.pixels)
        results[name] = [pixels[i:i + 3] for i in range(0, len(pixels), 4)]


main()
