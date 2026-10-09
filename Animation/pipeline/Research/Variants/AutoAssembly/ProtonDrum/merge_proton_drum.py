"""Build/reopen-validate the source-local Proton Drum V1/V5 presentation."""
import argparse, json, sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[6]))
from Animation.pipeline.builder import validate_reopened_native_descriptor
from Animation.pipeline.builder import review_preview_lighting
from Animation.pipeline.common import load, dump, file_hash
from Animation.pipeline.Research.Variants.AutoAssembly.ProtonDrum.proton_drum_contract import (
    assert_visible_source_sections, negative_completeness_control, assert_source_joint_subset, assert_clip_mode,
    assert_unsupported_clip_mode)

def move(obj, col):
    if col not in obj.users_collection: col.objects.link(obj)
    for old in list(obj.users_collection):
        if old != col: old.objects.unlink(obj)

def matrix_error(a, b):
    return max(abs(a[r][c]-b[r][c]) for r in range(4) for c in range(4))

def state(cols, label):
    for obj in cols['PROTON_SHARED_MAIN'].objects: obj.hide_set(False); obj.hide_render=False
    for key, visible in [('PROTON_V1_PADS', label == 'V1'), ('PROTON_V5_PADS', label == 'V5')]:
        for obj in cols[key].objects: obj.hide_set(not visible); obj.hide_render=not visible

def build(j):
    main, v1, v5 = [Path(j[k]) for k in ('main_blend','v1_blend','v5_blend')]
    for p, k in ((main,'main_sha'),(v1,'v1_sha'),(v5,'v5_sha')): assert file_hash(p)==j[k], f'Input blend changed: {p}'
    bpy.ops.wm.open_mainfile(filepath=str(main)); scene=bpy.context.scene
    cols={}
    for n in ('PROTON_SHARED_MAIN','PROTON_V1_PADS','PROTON_V5_PADS','PROTON_SOURCE_PAD_RIGS'):
        c=bpy.data.collections.new(n); scene.collection.children.link(c); cols[n]=c
    def meshes(digest): return [o for o in scene.objects if o.type=='MESH' and o.get('source_model_sha256')==digest]
    mainobjs=meshes(j['models']['main']); assert {int(o.get('source_subset_index',-1)) for o in mainobjs}=={0,1}
    mainrigs={m.object for o in mainobjs for m in o.modifiers if m.type=='ARMATURE' and m.object}
    assert len(mainrigs)==1; mainrig=next(iter(mainrigs))
    for o in set(mainobjs)|mainrigs: move(o,cols['PROTON_SHARED_MAIN'])
    for blend,role,colname in ((v1,'v1','PROTON_V1_PADS'),(v5,'v5','PROTON_V5_PADS')):
        with bpy.data.libraries.load(str(blend),link=False) as (src,dst): dst.objects=list(src.objects)
        imported=[o for o in dst.objects if o]
        objs=[o for o in imported if o.type=='MESH' and o.get('source_model_sha256')==j['models'][role]]
        assert {int(o.get('source_subset_index',-1)) for o in objs}=={0}
        rigs={m.object for o in objs for m in o.modifiers if m.type=='ARMATURE' and m.object}; assert len(rigs)==1
        pad_rig=next(iter(rigs)); assert pad_rig in imported
        # The native pad rig is exported in identity axes while the main rig
        # carries the source-to-Blender basis conversion. Match that shared
        # basis after the source S/Q/T and hierarchy equality checks below.
        pad_rig.matrix_world=mainrig.matrix_world.copy()
        for bone in pad_rig.data.bones:
            assert bone.name in mainrig.data.bones
            assert matrix_error(bone.matrix_local,mainrig.data.bones[bone.name].matrix_local) < 1e-6
        for obj in objs:
            mods=[m for m in obj.modifiers if m.type=='ARMATURE']; assert len(mods)==1
            mods[0].object=mainrig; move(obj,cols[colname])
        move(pad_rig,cols['PROTON_SOURCE_PAD_RIGS']); pad_rig.hide_set(True);pad_rig.hide_render=True
    scene['proton_drum_default']='V1'
    scene['proton_drum_config_asset_id']=j['config']['asset_id'];scene['proton_drum_config_sha256']=j['config']['sha256']
    scene['proton_drum_assembly_limits']='Source-local presentation only. Actor-to-config selection and external hand attachment are unsupported. Reconstructed materials are not claimed game-accurate.'
    scene['proton_drum_clip_status']='Activate/Deactivate source-supported. Fire/Open_Idle FULL and SSC0 are sampler experiments, not proven runtime choices.'
    state(cols,'V1')
    controls=bpy.data.texts.new('Proton Drum Source Controls.py')
    controls.write('''import bpy\n\ndef switch_pad_version(version):\n    assert version in ("V1", "V5")\n    for obj in bpy.data.collections["PROTON_SHARED_MAIN"].objects: obj.hide_set(False); obj.hide_render=False\n    for name, visible in (("PROTON_V1_PADS", version == "V1"), ("PROTON_V5_PADS", version == "V5")):\n        for obj in bpy.data.collections[name].objects: obj.hide_set(not visible); obj.hide_render=not visible\n    bpy.context.scene["proton_drum_default"] = version\n\ndef play_source_clip(name):\n    action = bpy.data.actions[name]\n    rig = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE" and o.name.startswith("Source animation rig"))\n    rig.animation_data_create(); rig.animation_data.action = action\n    bpy.context.scene.frame_start = int(action.get("source_start_frame", 0))\n    bpy.context.scene.frame_end = int(action.get("source_end_frame", bpy.context.scene.frame_end))\n    bpy.context.scene.frame_set(bpy.context.scene.frame_start)\n''')
    catalog=bpy.data.texts.get('Animation Catalog.json')
    if catalog:
        try:
            cat=json.loads(catalog.as_string())
            cat['proton_drum_controls']='Proton Drum Source Controls.py'
            for row in cat.get('clips',[]):
                name=row.get('name','')
                if name.startswith(('Fire_wpn_Thumper','Open_Idle_wpn_Thumper')): row['evidence_status']='EXPERIMENTAL: FULL/SSC0 inheritance interpretations; not proven runtime choice'
                elif name.startswith(('Activate_wpn_Thumper','Deactivate_wpn_Thumper')): row['evidence_status']='SOURCE-SUPPORTED'
            catalog.clear();catalog.write(json.dumps(cat,indent=2,sort_keys=True))
        except (ValueError, TypeError): pass
    bpy.ops.file.pack_all(); bpy.ops.wm.save_as_mainfile(filepath=j['candidate'],compress=True)

def validate(j):
    bpy.ops.wm.open_mainfile(filepath=j['candidate']);scene=bpy.context.scene
    cols={n:bpy.data.collections[n] for n in ('PROTON_SHARED_MAIN','PROTON_V1_PADS','PROTON_V5_PADS')}
    assert scene.camera and scene.camera.data.type=='PERSP' and scene.world
    assert scene['proton_drum_config_sha256']==j['config']['sha256']; negative_completeness_control()
    visible={}
    for label in ('V1','V5'):
        state(cols,label); found={k:set() for k in ('main','v1','v5')}
        for obj in scene.objects:
            if obj.type!='MESH' or obj.hide_get() or obj.hide_render: continue
            for k in found:
                if obj.get('source_model_sha256')==j['models'][k]: found[k].add(int(obj.get('source_subset_index',-1)))
        assert_visible_source_sections(label,found);visible[label]={k:sorted(v) for k,v in found.items()}
    source_proofs=[]
    for k in ('main','v1','v5'):
        objs=[o for o in scene.objects if o.type=='MESH' and o.get('source_model_sha256')==j['models'][k]]
        sj=load(j[k+'_job']); descriptor=sj['native_geometry']; descriptor['joint_names']=[r['name'] for r in sj['model']['skeleton']]
        source_proofs.append({'role':k,'sections':sorted(int(o.get('source_subset_index',-1)) for o in objs),
            'structure':validate_reopened_native_descriptor(descriptor,objs,descriptor['joint_names'])})
    assert_clip_mode(j['clips'],bool(j.get('experimental_ambiguous_inheritance')))
    assert_unsupported_clip_mode(j.get('unsupported_clips',[]),bool(j.get('experimental_ambiguous_inheritance')))
    actions=[]; deformation=[]
    rig=next(o for o in scene.objects if o.type=='ARMATURE' and o.name.startswith('Source animation rig'))
    rig.animation_data_create()
    for track in rig.animation_data.nla_tracks: track.mute=True
    for row in j['clips']:
        candidates=row['actions']
        for name, status in candidates:
            action=bpy.data.actions.get(name); assert action and action.use_fake_user
            assert action.get('source_raw_sha256')==row['raw_sha256'] and action.get('source_sample_sha256')==row['sample_sha256']
            rig.animation_data.action=action
            if hasattr(action,'slots') and action.slots: rig.animation_data.action_slot=action.slots[0]
            frame=max(1,1+int(row['frames'])//2);scene.frame_set(1);bpy.context.view_layer.update()
            samples=[]
            for label in ('V1','V5'):
                state(cols,label);bpy.context.view_layer.update()
                pads=[o for key in (('PROTON_V1_PADS',) if label=='V1' else ('PROTON_V5_PADS',)) for o in cols[key].objects if o.type=='MESH']
                before={o.name:[o.matrix_world@v.co for v in o.data.vertices] for o in pads}
                scene.frame_set(frame);bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get();delta=0.0
                for obj in pads:
                    evaluated=obj.evaluated_get(deps);mesh=evaluated.to_mesh()
                    try:
                        points=[evaluated.matrix_world@v.co for v in mesh.vertices]
                        delta=max(delta,max((a-b).length for a,b in zip(before[obj.name],points)))
                    finally: evaluated.to_mesh_clear()
                assert delta>1e-4,(name,label,delta)
                samples.append({'version':label,'sample_frame':frame,'max_skin_displacement':delta})
            deformation.append({'action':name,'status':status,'samples':samples})
            actions.append({'name':name,'status':status,'raw_sha256':row['raw_sha256'],'sample_sha256':row['sample_sha256'],'frames':row['frames'],'fps':row['fps']})
    assert len(actions)==sum(len(row['actions']) for row in j['clips']),actions
    imgs=[i for i in bpy.data.images if i.get('source_texture_path')]; assert imgs and all(i.packed_file for i in imgs)
    rig.animation_data.action=None
    for track in rig.animation_data.nla_tracks: track.mute=False
    state(cols,'V1'); scene['proton_drum_default']='V1';scene.frame_set(1)
    review_dir=Path(j['validation']).parent/'review';review_dir.mkdir(parents=True,exist_ok=True);renders=[]
    original=(scene.render.resolution_percentage,scene.cycles.samples,scene.render.filepath,scene.frame_current)
    with review_preview_lighting(scene) as review_light:
        scene.render.resolution_percentage=25;scene.cycles.samples=32
        for label in ('V1','V5'):
            state(cols,label);scene.frame_set(1);scene.render.filepath=str(review_dir/f'proton_drum_{label.lower()}.png');bpy.ops.render.render(write_still=True)
            renders.append({'variant':label,'path':scene.render.filepath,'sha256':file_hash(scene.render.filepath),'lighting':review_light})
    scene.render.resolution_percentage,scene.cycles.samples,scene.render.filepath,frame=original;scene.frame_set(frame);state(cols,'V1')
    materials=sorted({m.name for o in scene.objects if o.type=='MESH' for m in o.data.materials if m})
    dump(j['validation'],{'status':'passed','blend_sha256':file_hash(j['candidate']),'config':j['config'],'visible_sections':visible,
      'sources':source_proofs,'actions':actions,'skin_deformation':deformation,'packed_images':len(imgs),'camera':scene.camera.name,'world':scene.world.name,
      'saved_material_identities':materials,'proof_renders':renders,
      'unsupported_clips':j.get('unsupported_clips',[]),
      'limitations':['Actor-to-config selection unsupported.','External hand attachment unsupported.','Fire/Open_Idle FULL/SSC0 are sampler experiments, not proven runtime choices when opted in.','Shader behavior is not claimed game-accurate.'],
      'negative_completeness_control':'passed'})

p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--validate',action='store_true');a=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []);j=load(a.job);validate(j) if a.validate else build(j)
