"""Build and separately validate a native Combuster V1/V5 scene."""
import argparse, json, sys
from pathlib import Path
import bpy
from mathutils import Matrix
sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from rc2016.common import file_hash,load,dump,read,unzip_json
from rc2016.pipeline.builder import validate_reopened_native_descriptor,review_preview_lighting,_configure_optix_gpu,matrix_error
from rc2016.pipeline.blender_support import targets
from rc2016.formats.animation_samples import decode_clip
from rc2016.formats.animation_transforms import sample_transforms
from rc2016.pipeline.weapons.combuster.contract import (
    assert_joint_subset,assert_visible,negative_completeness_control,assert_clip_coverage)

def materr(a,b): return max(abs(a[i][j]-b[i][j]) for i in range(4) for j in range(4))

def move(obj,col):
    if col not in obj.users_collection: col.objects.link(obj)
    for old in list(obj.users_collection):
        if old!=col: old.objects.unlink(obj)

def state(cols,label):
    for obj in cols['COMBUSTER_SHARED_MAIN'].objects: obj.hide_set(False);obj.hide_render=False
    for key,on in (('COMBUSTER_VARIANT_V1',label=='V1'),('COMBUSTER_VARIANT_V5',label=='V5')):
        for obj in cols[key].objects: obj.hide_set(not on);obj.hide_render=not on

def build(j):
    roles=('main','v1','v5')
    for role in roles:
        p=Path(j[role+'_blend']);assert file_hash(p)==j[role+'_blend_sha256'],f'{role} input blend hash mismatch'
    bpy.ops.wm.open_mainfile(filepath=j['main_blend']);scene=bpy.context.scene
    assert scene.camera and scene.camera.data.type=='PERSP' and scene.world
    cam=scene.camera; world=scene.world
    camera_matrix=cam.matrix_world.copy(); camera_data=(cam.data.type,cam.data.lens,cam.data.shift_x,cam.data.shift_y)
    render_state=(scene.render.engine,scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage,scene.render.fps,scene.render.fps_base)
    cols={}
    for n in ('COMBUSTER_SHARED_MAIN','COMBUSTER_VARIANT_V1','COMBUSTER_VARIANT_V5','COMBUSTER_SOURCE_RIGS'):
        c=bpy.data.collections.new(n);scene.collection.children.link(c);cols[n]=c
    jobs={r:load(j[r+'_job']) for r in roles}; hashes=j['models']
    def by_hash(d):return [o for o in scene.objects if o.type=='MESH' and o.get('source_model_sha256')==d]
    mainobjs=by_hash(hashes['main']);assert {int(o.get('source_subset_index',-1)) for o in mainobjs}=={0,1}
    mainrigs={m.object for o in mainobjs for m in o.modifiers if m.type=='ARMATURE' and m.object};assert len(mainrigs)==1
    mainrig=next(iter(mainrigs))
    for obj in set(mainobjs)|mainrigs:move(obj,cols['COMBUSTER_SHARED_MAIN'])
    mapping={}
    main_rows=jobs['main']['model']['skeleton']
    for role in ('v1','v5'):
        with bpy.data.libraries.load(j[role+'_blend'],link=False) as (src,dst):dst.objects=list(src.objects)
        imported=[o for o in dst.objects if o]
        objs=[o for o in imported if o.type=='MESH' and o.get('source_model_sha256')==hashes[role]]
        wanted={0,1} if role=='v1' else {0,1,2}
        assert {int(o.get('source_subset_index',-1)) for o in objs}==wanted,(role,[o.name for o in objs])
        source_rigs={m.object for o in objs for m in o.modifiers if m.type=='ARMATURE' and m.object};assert len(source_rigs)==1
        source_rig=next(iter(source_rigs))
        # append-from-library may unlink scene objects before their saved object
        # basis has propagated. All three pipeline inputs use this same explicit
        # Y-up to Blender Z-up presentation matrix; restore that saved main basis.
        source_rig.matrix_world=mainrig.matrix_world.copy()
        part_rows=jobs[role]['model']['skeleton'];mapping[role]=assert_joint_subset(main_rows,part_rows)
        for row in part_rows:
            bone=source_rig.data.bones.get(row['name']);main_bone=mainrig.data.bones.get(row['name'])
            assert bone and main_bone and materr(bone.matrix_local,main_bone.matrix_local)<1e-5,(role,row['name'])
        for obj in objs:
            obj.matrix_world=mainrig.matrix_world.copy()
            assert {g.name for g in obj.vertex_groups}<=set(mainrig.data.bones.keys())
            mods=[m for m in obj.modifiers if m.type=='ARMATURE'];assert len(mods)==1
            mods[0].object=mainrig;world_matrix=obj.matrix_world.copy();obj.parent=mainrig;obj.matrix_world=world_matrix
            move(obj,cols['COMBUSTER_VARIANT_'+role.upper()])
        move(source_rig,cols['COMBUSTER_SOURCE_RIGS']);source_rig.hide_set(True);source_rig.hide_render=True
    included=jobs['main'].get('clips',[]);unsupported=jobs['main'].get('limitations',{}).get('unsupported_clips',[])
    animation=assert_clip_coverage(included,unsupported,bool(j.get('experimental_ambiguous_inheritance')))
    assert animation==j['animation']
    scene['combuster_default_variant']='V1';scene['combuster_config_asset_id']=j['config']['asset_id'];scene['combuster_config_sha256']=j['config']['sha256']
    scene['combuster_evidence']='Exact source config V1/V5 links; native source-local geometry and matched rig rest SQT. Runtime actor choice and weapon mount matrix are not established.'
    scene['combuster_unsupported_animation']=json.dumps(animation['unsupported'],sort_keys=True)
    scene['combuster_experimental_animation']=json.dumps(animation['experimental'],sort_keys=True)
    scene['combuster_animation_mode']=animation['mode']
    controls=bpy.data.texts.new('Combuster Variant Controls.py')
    controls.write('''import bpy\ndef switch_combuster_version(version):\n    assert version in ("V1", "V5")\n    for obj in bpy.data.collections["COMBUSTER_SHARED_MAIN"].objects: obj.hide_set(False); obj.hide_render=False\n    for name, visible in (("COMBUSTER_VARIANT_V1", version == "V1"), ("COMBUSTER_VARIANT_V5", version == "V5")):\n        for obj in bpy.data.collections[name].objects: obj.hide_set(not visible); obj.hide_render=not visible\n    bpy.context.scene["combuster_default_variant"] = version\n''')
    cat=bpy.data.texts.get('Animation Catalog.json')
    if cat:
        data=json.loads(cat.as_string());data['combuster_unsupported_clips']=animation['unsupported'];data['combuster_experimental_clips']=animation['experimental'];data['combuster_variant_controls']='Combuster Variant Controls.py'
        cat.clear();cat.write(json.dumps(data,indent=2,sort_keys=True))
    start=bpy.data.texts.get('START HERE')
    if start:start.write('\n\nCOMBUSTER V1/V5: Run Combuster Variant Controls.py once, then call switch_combuster_version("V1") or ("V5"). Default mode includes source-supported Turn_360 only. Opt-in FULL and SSC0 Actions are labelled experimental sampler interpretations; runtime choice remains unproven.\n')
    state(cols,'V1');bpy.ops.file.pack_all();bpy.ops.wm.save_as_mainfile(filepath=j['candidate'],compress=True)

def validate(j):
    bpy.ops.wm.open_mainfile(filepath=j['candidate']);_configure_optix_gpu(bpy);scene=bpy.context.scene
    cols={n:bpy.data.collections[n] for n in ('COMBUSTER_SHARED_MAIN','COMBUSTER_VARIANT_V1','COMBUSTER_VARIANT_V5')}
    assert scene.camera and scene.camera.data.type=='PERSP' and scene.world
    assert scene['combuster_config_sha256']==j['config']['sha256'];negative_completeness_control()
    visible={}
    for label in ('V1','V5'):
        state(cols,label);actual={k:set() for k in ('main','v1','v5')}
        for obj in scene.objects:
            if obj.type!='MESH' or obj.hide_get() or obj.hide_render:continue
            for key in actual:
                if obj.get('source_model_sha256')==j['models'][key]:actual[key].add(int(obj.get('source_subset_index',-1)))
        assert_visible(label,actual);visible[label]={k:sorted(v) for k,v in actual.items()}
    source_proofs=[]
    for role in ('main','v1','v5'):
        objs=[o for o in scene.objects if o.type=='MESH' and o.get('source_model_sha256')==j['models'][role]]
        sj=load(j[role+'_job']);descriptor=sj['native_geometry'];names=[x['name'] for x in sj['model']['skeleton']]
        descriptor['joint_names']=names
        source_proofs.append({'role':role,'sections':sorted(int(o.get('source_subset_index',-1)) for o in objs),
            'geometry':validate_reopened_native_descriptor(descriptor,objs,names),
            'material_paths':{o.name:[m.get('source_material_path') if m else None for m in o.data.materials] for o in objs}})
    sj=load(j['main_job']);animation=assert_clip_coverage(j['clips'],j['unsupported_clips'],bool(j.get('experimental_ambiguous_inheritance')))
    assert animation==j['animation']
    rig=bpy.data.objects.get('Source animation rig');assert rig and rig.animation_data
    right={n:Matrix(m) for n,m in json.loads(rig['source_right_corrections']).items()}
    for track in rig.animation_data.nla_tracks:track.mute=True
    actions=[]
    for clip in j['clips']:
        if clip.get('status')!='included':continue
        raw=read(clip['raw_clip']);assert file_hash(clip['raw_clip'])==clip['raw_sha256'] and __import__('hashlib').sha256(raw).hexdigest()==clip['raw_sha256']
        decoded=decode_clip(raw,0,{int(x['hash'],16):x['name'] for x in sj['model']['skeleton']})
        frames=sample_transforms(decoded);assert decoded['sample_sha256']==clip['sample_sha256'] and frames==unzip_json(clip['samples'])['frames']
        assert len(frames)==clip['frames'] and abs(sj['model'].get('fps',clip['fps'])-clip['fps'])<1e-5
        action=bpy.data.actions.get(clip['action']);assert action and action.use_fake_user
        assert action.get('source_raw_sha256')==clip['raw_sha256'] and action.get('source_sample_sha256')==clip['sample_sha256']
        assert action.get('source_frame_count')==clip['frames'] and abs(action.get('source_fps')-clip['fps'])<1e-6
        assert action.get('source_animset_sha256')==clip['animset_sha256']
        if clip.get('experimental_inheritance_preview'):
            assert action.get('experimental') is True and action.get('experimental_selected_inheritance')==clip['inheritance']
            assert action.get('experimental_game_accuracy')=='unproven'
            status='experimental_'+('FULL' if clip['inheritance']=='full' else 'SSC0')
        elif clip.get('inheritance_basis')=='pipeline_default_ssc0':status='default_SSC0'
        else:status='source_supported'
        rig.animation_data.action=action
        if action.slots:rig.animation_data.action_slot=action.slots[0]
        errors=[]
        for fi,frame in enumerate(frames,1):
            scene.frame_set(fi);bpy.context.view_layer.update();errors.append(matrix_error(rig,targets(sj['model']['skeleton'],frame,clip),right))
        assert max(errors)<sj['matrix_tolerance'],(clip['action'],max(errors))
        displacement={};probe=max(2,int(len(frames)*.75))
        for label in ('V1','V5'):
            state(cols,label);scene.frame_set(1);bpy.context.view_layer.update()
            objs=[o for o in cols['COMBUSTER_VARIANT_'+label].objects if o.type=='MESH']
            before={o.name:[o.matrix_world@v.co for v in o.data.vertices] for o in objs}
            scene.frame_set(probe);bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get();maximum=0.
            for obj in objs:
                ev=obj.evaluated_get(deps);mesh=ev.to_mesh()
                try:maximum=max(maximum,max((ev.matrix_world@v.co-p).length for v,p in zip(mesh.vertices,before[obj.name])))
                finally:ev.to_mesh_clear()
            assert maximum>1e-3,(clip['action'],label,maximum);displacement[label]=maximum
        actions.append({'name':clip['name'],'action':clip['action'],'status':status,'inheritance':clip.get('inheritance'),
            'raw_sha256':clip['raw_sha256'],'sample_sha256':clip['sample_sha256'],'animset_sha256':clip['animset_sha256'],
            'frames':len(frames),'fps':clip['fps'],'max_matrix_error':max(errors),'skin_displacement':displacement,
            'reason':clip.get('experimental_reason')})
    displacement={}
    rest_frame=1;posed_frame=max(2,int(len(frames)*0.75))
    for label in ('V1','V5'):
        state(cols,label);scene.frame_set(rest_frame);bpy.context.view_layer.update()
        objs=[o for o in cols['COMBUSTER_VARIANT_'+label].objects if o.type=='MESH']
        before={o.name:[o.matrix_world@v.co for v in o.data.vertices] for o in objs}
        scene.frame_set(posed_frame);bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get();maximum=0.
        for obj in objs:
            ev=obj.evaluated_get(deps);mesh=ev.to_mesh()
            try:maximum=max(maximum,max((ev.matrix_world@v.co-p).length for v,p in zip(mesh.vertices,before[obj.name])))
            finally:ev.to_mesh_clear()
        assert maximum>1e-3,(label,maximum);displacement[label]=maximum
    for track in rig.animation_data.nla_tracks:track.mute=False
    rig.animation_data.action=None;state(cols,'V1');scene['combuster_default_variant']='V1';scene.frame_set(1)
    review=Path(j['validation']).parent/'review';review.mkdir(parents=True,exist_ok=True)
    old=(scene.render.resolution_percentage,scene.cycles.samples,scene.render.filepath,scene.frame_current);renders=[]
    with review_preview_lighting(scene) as lighting:
        scene.render.resolution_percentage=25;scene.cycles.samples=32
        for label in ('V1','V5'):
            state(cols,label);scene.frame_set(1);scene.render.filepath=str(review/f'combuster_{label.lower()}.png');bpy.ops.render.render(write_still=True)
            renders.append({'variant':label,'path':scene.render.filepath,'sha256':file_hash(scene.render.filepath),'lighting':lighting})
    scene.render.resolution_percentage,scene.cycles.samples,scene.render.filepath,frame=old;scene.frame_set(frame);state(cols,'V1')
    dump(j['validation'],{'status':'passed','blend_sha256':file_hash(j['candidate']),'config':j['config'],'visible_sections':visible,
        'sources':source_proofs,'actions':actions,'animation_mode':animation['mode'],'unsupported_clips':animation['unsupported'],'experimental_clips':animation['experimental'],'proof_renders':renders,
        'camera':scene.camera.name,'world':scene.world.name,'saved_material_identities':sorted({m.name for o in scene.objects if o.type=='MESH' for m in o.data.materials if m}),
        'placement_basis':'Copied from the freshly built main model Source animation rig object, which carries the pipeline Y-up to Blender Z-up basis; no asset-specific or external mount transform inferred.',
        'limitations':['Runtime actor version selection not established.','No external weapon mount matrix is present in config.','Material graph appearance is not game shader proof.','Native packed tangent interpretation remains unproven.'],
        'negative_completeness_control':'passed'})

p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--validate',action='store_true');a=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []);j=load(a.job);validate(j) if a.validate else build(j)
