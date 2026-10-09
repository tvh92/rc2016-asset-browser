import argparse, json, sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[6]))
from Animation.pipeline.builder import validate_reopened_native_descriptor
from Animation.pipeline.common import load, dump, file_hash
from Animation.pipeline.Research.Variants.AutoAssembly.Pyrocitor.pyrocitor_contract import assert_visible_source_sections, negative_completeness_control

def move(obj,col):
    if col not in obj.users_collection: col.objects.link(obj)
    for old in list(obj.users_collection):
        if old != col: old.objects.unlink(obj)
def state(cols,label):
    for o in cols['PYROCITOR_SHARED_MAIN'].objects: o.hide_set(False); o.hide_render=False
    for key,vis in [('PYROCITOR_V1_BARREL',label=='V1'),('PYROCITOR_V5_BARREL',label=='V5')]:
        for o in cols[key].objects: o.hide_set(not vis); o.hide_render=not vis

def build(j):
    main,v1,v5=[Path(j[k]) for k in ('main_blend','v1_blend','v5_blend')]
    for p,k in ((main,'main_sha'),(v1,'v1_sha'),(v5,'v5_sha')):
        assert file_hash(p)==j[k],f'Input blend changed: {p}'
    bpy.ops.wm.open_mainfile(filepath=str(main)); scene=bpy.context.scene
    assert scene.camera and scene.world
    hashes=j['models']; cols={}
    for n in ('PYROCITOR_SHARED_MAIN','PYROCITOR_V1_BARREL','PYROCITOR_V5_BARREL'):
        c=bpy.data.collections.new(n);scene.collection.children.link(c);cols[n]=c
    def meshes(digest): return [o for o in scene.objects if o.type=='MESH' and o.get('source_model_sha256')==digest]
    mainobjs=meshes(hashes['main']); assert {int(o.get('source_subset_index',-1)) for o in mainobjs}==set(range(4))
    mainrigs={m.object for o in mainobjs for m in o.modifiers if m.type=='ARMATURE' and m.object}
    for o in set(mainobjs)|mainrigs: move(o,cols['PYROCITOR_SHARED_MAIN'])
    for blend,key,colname,expected in ((v1,'v1','PYROCITOR_V1_BARREL',{0}),(v5,'v5','PYROCITOR_V5_BARREL',{0,1,2,3,4})):
        with bpy.data.libraries.load(str(blend),link=False) as (a,b): b.objects=list(a.objects)
        imported=[o for o in b.objects if o]
        objs=[o for o in imported if o.type=='MESH' and o.get('source_model_sha256')==hashes[key]]
        assert {int(o.get('source_subset_index',-1)) for o in objs}==expected,(key,[o.name for o in objs])
        rigs={m.object for o in objs for m in o.modifiers if m.type=='ARMATURE' and m.object}
        assert rigs <= set(imported)
        for o in set(objs)|rigs: move(o,cols[colname])
    scene['pyrocitor_default']='V1'; scene['pyrocitor_runtime_limits']='Actor-to-config selection unproven; external runtime attachment transform unproven. This scene presents source-local rest poses.'
    scene['pyrocitor_config_id']=j['config']['asset_id'];scene['pyrocitor_config_sha256']=j['config']['sha256']
    state(cols,'V1'); bpy.ops.file.pack_all(); bpy.ops.wm.save_as_mainfile(filepath=j['candidate'],compress=True)

def validate(j):
    bpy.ops.wm.open_mainfile(filepath=j['candidate']); scene=bpy.context.scene
    cols={n:bpy.data.collections[n] for n in ('PYROCITOR_SHARED_MAIN','PYROCITOR_V1_BARREL','PYROCITOR_V5_BARREL')}
    assert scene.world and scene.camera and scene.camera.data.type=='PERSP'
    assert scene['pyrocitor_config_sha256']==j['config']['sha256']; negative_completeness_control()
    proof={}
    for label in ('V1','V5'):
        state(cols,label); found={k:set() for k in ('main','v1','v5')}
        for obj in scene.objects:
            if obj.type!='MESH' or obj.hide_get() or obj.hide_render: continue
            h=obj.get('source_model_sha256'); idx=int(obj.get('source_subset_index',-1))
            for k in found:
                if h==j['models'][k]: found[k].add(idx)
        assert_visible_source_sections(label,found); proof[label]={k:sorted(v) for k,v in found.items()}
    sources=[]
    for k in ('main','v1','v5'):
        objs=[o for o in scene.objects if o.type=='MESH' and o.get('source_model_sha256')==j['models'][k]]
        source_job=load(j[k+'_job']); descriptor=source_job['native_geometry']; descriptor['joint_names']=[row['name'] for row in source_job['model']['skeleton']]; assert objs
        structural=validate_reopened_native_descriptor(descriptor,objs,descriptor.get('joint_names'))
        used={o.name:sorted({int(p.material_index) for p in o.data.polygons}) for o in objs}
        paths={o.name:[m.get('source_material_path') if m else None for m in o.data.materials] for o in objs}
        sources.append({'role':k,'sha256':j['models'][k],'sections':sorted(int(o.get('source_subset_index',-1)) for o in objs),'reopened_structural_proof':structural,'used_slots':used,'material_paths':paths,'rigs':sorted({m.object.name for o in objs for m in o.modifiers if m.type=='ARMATURE' and m.object})})
    imgs=[i for i in bpy.data.images if i.get('source_texture_path')]; assert imgs and all(i.packed_file for i in imgs)
    state(cols,'V1'); assert all(not o.hide_get() and not o.hide_render for o in cols['PYROCITOR_SHARED_MAIN'].objects)
    old_pct=scene.render.resolution_percentage; old_samples=scene.cycles.samples if scene.render.engine=='CYCLES' else None; old_fp=scene.render.filepath
    scene.render.resolution_percentage=25
    if old_samples is not None: scene.cycles.samples=32
    review=Path(j['validation']).parent/'review'; review.mkdir(parents=True,exist_ok=True); renders=[]
    for label in ('V1','V5'):
        state(cols,label); scene.render.filepath=str(review/f'pyrocitor_{label.lower()}_proof.png'); bpy.ops.render.render(write_still=True); renders.append({'variant':label,'path':scene.render.filepath,'sha256':file_hash(scene.render.filepath)})
    scene.render.resolution_percentage=old_pct
    if old_samples is not None: scene.cycles.samples=old_samples
    scene.render.filepath=old_fp; state(cols,'V1')
    dump(j['validation'],{'status':'passed','blend_sha256':file_hash(j['candidate']),'proof_renders':renders,'config':j['config'],'visible_sections':proof,'sources':sources,'packed_images':len(imgs),'saved_default':'V1','limitations':['Actor-to-config selection unproven.','External runtime attachment transform unproven; independently source-presented rest poses retained.'],'negative_completeness_control':'passed'})
p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--validate',action='store_true');a=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []); j=load(a.job); validate(j) if a.validate else build(j)





