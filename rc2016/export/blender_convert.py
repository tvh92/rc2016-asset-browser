"""Blender side of rc2016.export.models: import one .glb, write FBX / USD / .blend. Run by Blender only.

    blender --background --factory-startup --python blender_convert.py -- --glb in.glb [--fbx out.fbx] [--usd out.usdc] [--blend out.blend]

Factory settings, nothing saved to the user's preferences. The .blend gets the repository's
shared scene defaults (rc2016.pipeline.scene_defaults.apply_new_scene_defaults) and packs its images.
"""
import argparse
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repository root, for rc2016.pipeline.scene_defaults

args = argparse.ArgumentParser()
args.add_argument('--glb', required=True)
args.add_argument('--fbx')
args.add_argument('--usd')
args.add_argument('--blend')
args = args.parse_args(sys.argv[sys.argv.index('--') + 1:])

bpy.ops.wm.read_factory_settings(use_empty=True)
# No bone-display Icosphere: FBX/USD would export it as a mesh.
bpy.ops.import_scene.gltf(filepath=args.glb, disable_bone_shape=True)
actions = list(bpy.data.actions)
for action in actions:
    action.use_fake_user = True   # every clip stays in the file, not only the active one

if args.fbx:
    bpy.ops.export_scene.fbx(filepath=args.fbx, path_mode='COPY', embed_textures=True, add_leaf_bones=False,
                             bake_anim=bool(actions), bake_anim_use_all_actions=True, bake_anim_use_nla_strips=False,
                             bake_anim_force_startend_keying=True)
    print('EXPORTED', args.fbx, flush=True)
if args.usd:
    bpy.ops.wm.usd_export(filepath=args.usd, export_textures_mode='NEW', export_animation=bool(actions))
    print('EXPORTED', args.usd, flush=True)
if args.blend:
    from rc2016.pipeline.scene_defaults import apply_new_scene_defaults
    apply_new_scene_defaults(bpy.context.scene)
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=args.blend, compress=True)
    print('EXPORTED', args.blend, flush=True)
