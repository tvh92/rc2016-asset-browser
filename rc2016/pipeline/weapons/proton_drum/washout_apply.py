"""Apply the hash/structure-bound Proton presentation recipe to a fresh source-linked build."""
import bpy,hashlib,json,sys
from array import array
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
PROFILE_PATH=Path(__file__).with_name('washout_structure_profile.json')
PROFILE=json.loads(PROFILE_PATH.read_text(encoding='utf-8-sig'))
PROFILE_SHA=hashlib.sha256(PROFILE_PATH.read_bytes()).hexdigest()
PARSER_ARGS=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);parser.add_argument('--output-dir');args=parser.parse_args(PARSER_ARGS)
INPUT=Path(args.input).resolve();SOURCE_ROOT=INPUT.parent.parent
OUT_DIR=Path(args.output_dir).resolve() if args.output_dir else SOURCE_ROOT/'proton_presentation27';OUTPUT=OUT_DIR/'final'/'Proton_Drum_V1_V5_switchable_proton_presentation.blend'
OUT_DIR.joinpath('final').mkdir(parents=True,exist_ok=True)
REFERENCE_PATH=PROFILE_PATH.with_name('washout_structure_reference.json')
STATE_REFERENCE_PATH=PROFILE_PATH.with_name('washout_state_reference.json')

def sha(data):return hashlib.sha256(data).hexdigest()
def file_sha(path):return sha(Path(path).read_bytes())
def image_sig(im):
    px=array('f',[0.0])*len(im.pixels);im.pixels.foreach_get(px)
    return {'name':im.name,'size':list(im.size),'colorspace':im.colorspace_settings.name,'alpha_mode':im.alpha_mode,
      'packed':bool(im.packed_file),'packed_data_sha256':sha(im.packed_file.data) if im.packed_file else None,'pixel_f32_sha256':sha(px.tobytes())}
def image_identity_multiset(rows):
    return sorted((tuple(r['size']),r['colorspace'],r['alpha_mode'],r['packed_data_sha256'],r['pixel_f32_sha256']) for r in rows)
def object_sig(o):
    mesh=o.data;co=array('f',[0.0])*(len(mesh.vertices)*3);mesh.vertices.foreach_get('co',co)
    polys=[tuple(p.vertices) for p in mesh.polygons];mi=array('i',[p.material_index for p in mesh.polygons]);weights=[]
    for v in mesh.vertices:
        for g in v.groups:weights.append((v.index,g.group,round(g.weight,8)))
    uv=[]
    for layer in mesh.uv_layers:
        vals=array('f',[0.0])*(len(layer.data)*2);layer.data.foreach_get('uv',vals)
        uv.append({'name':layer.name,'count':len(layer.data),'sha256':sha(vals.tobytes())})
    return {'name':o.name,'source_model_sha256':o.get('source_model_sha256'),'source_subset_index':o.get('source_subset_index'),
      'vertices':len(mesh.vertices),'polygons':len(mesh.polygons),'positions_sha256':sha(co.tobytes()),
      'polygon_indices_sha256':sha(json.dumps(polys,separators=(',',':')).encode()),'material_indices_sha256':sha(mi.tobytes()),'uv':uv,
      'vertex_groups':[g.name for g in o.vertex_groups],'skin_weight_count':len(weights),
      'skin_weights_sha256':sha(json.dumps(weights,separators=(',',':')).encode()),
      'armature_modifiers':[{'name':m.name,'object':m.object.name if m.object else None,'show_viewport':m.show_viewport,'show_render':m.show_render} for m in o.modifiers if m.type=='ARMATURE'],
      'materials':[s.material.name if s.material else None for s in o.material_slots]}
def state_sig():
    sc=bpy.context.scene;hs={v['sha256'] for v in PROFILE['source_models'].values()}
    objs=[object_sig(o) for o in sc.objects if o.type=='MESH' and o.get('source_model_sha256') in hs]
    acts=[{'name':a.name,'frame_range':[float(a.frame_range[0]),float(a.frame_range[1])],'users':a.users} for a in bpy.data.actions]
    nla=[]
    for o in sc.objects:
        if o.animation_data:
            nla.extend({'object':o.name,'track':t.name,'strips':[{'name':x.name,'action':x.action.name if x.action else None,'frame_start':x.frame_start,'frame_end':x.frame_end} for x in t.strips]} for t in o.animation_data.nla_tracks)
    cam=sc.camera;camera={'name':cam.name,'type':cam.data.type,'matrix':[[float(x) for x in row] for row in cam.matrix_world],
      'lens':cam.data.lens,'shift':[cam.data.shift_x,cam.data.shift_y],'ortho_scale':cam.data.ortho_scale}
    world=sc.world;wi=None;strength=None
    if world and world.node_tree:
        for n in world.node_tree.nodes:
            if n.type=='TEX_ENVIRONMENT' and n.image:wi=image_sig(n.image)
            if n.type=='BACKGROUND':strength=float(n.inputs['Strength'].default_value)
    vis={}
    for name in ['PROTON_V1_PADS','PROTON_V5_PADS']:
        vis[name]=[{'name':o.name,'hide_get':o.hide_get(),'hide_render':o.hide_render} for o in bpy.data.collections[name].objects]
    return {'objects':sorted(objs,key=lambda x:x['name']),'actions':sorted(acts,key=lambda x:x['name']),'nla':nla,
      'camera':camera,'world':{'name':world.name if world else None,'image':wi,'strength':strength},'collections':vis,
      'packed_images':sorted([image_sig(im) for im in bpy.data.images if im.packed_file],key=lambda x:x['name']),
      'render':{'engine':sc.render.engine,'resolution':[sc.render.resolution_x,sc.render.resolution_y,sc.render.resolution_percentage],'fps':sc.render.fps,'filepath':sc.render.filepath},
      'colour_management':[sc.view_settings.view_transform,sc.view_settings.look,sc.view_settings.exposure,sc.view_settings.gamma]}
def node_data(n):
    r={'name':n.name,'type':n.type,'label':n.label}
    for attr in ('operation','blend_type','data_type','mode','use_clamp','interpolation','projection','extension','space'):
        if hasattr(n,attr):
            value=getattr(n,attr)
            if isinstance(value,(str,int,float,bool)):r[attr]=value
    def sv(v):
        try:return [round(float(x),9) for x in v]
        except:return round(float(v),9) if isinstance(v,(int,float)) else str(v)
    r['inputs']=[{'index':i,'name':s.name,'default':sv(s.default_value)} for i,s in enumerate(n.inputs) if hasattr(s,'default_value') and not s.is_linked]
    if n.type=='TEX_IMAGE':
        im=n.image;r['image']={'packed_sha256':sha(im.packed_file.data) if im and im.packed_file else None,'size':list(im.size) if im else None,
          'colorspace':im.colorspace_settings.name if im else None,'alpha_mode':im.alpha_mode if im else None}
    return r
def graph_data(m):
    nodes=sorted([node_data(n) for n in m.node_tree.nodes],key=lambda x:x['name']);links=[]
    for l in m.node_tree.links:
        links.append({'from_node':l.from_node.name,'from_index':list(l.from_node.outputs).index(l.from_socket),'from_socket':l.from_socket.name,
          'to_node':l.to_node.name,'to_index':list(l.to_node.inputs).index(l.to_socket),'to_socket':l.to_socket.name})
    data={'nodes':nodes,'links':sorted(links,key=lambda x:(x['to_node'],x['to_index'],x['from_node'],x['from_index'])),
          'active_outputs':[n.name for n in m.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]}
    data['graph_sha256']=sha(json.dumps(data,sort_keys=True,separators=(',',':')).encode());return data
def img_node(m,role):
    found=[n for n in m.node_tree.nodes if n.type=='TEX_IMAGE' and role in n.name.lower() and 'default_pink' not in n.label.lower()]
    assert len(found)==1,(m.name,role);return found[0]
def upstream(socket):
    assert len(socket.links)==1,(socket.name,len(socket.links));return socket.links[0].from_node

def relative_manifest(old_relative):
    return SOURCE_ROOT.joinpath(*Path(old_relative).parts)

assert INPUT.is_file(),INPUT
input_hash=file_sha(INPUT)
assert PROFILE['parent_profile_sha256']==file_sha(Path(__file__).with_name('washout_presentation_profile.json'))
assert file_sha(REFERENCE_PATH)==PROFILE['source_structure_reference_sha256'],'Pinned source/structure reference changed'
assert file_sha(STATE_REFERENCE_PATH)==PROFILE['source_state_reference_sha256'],'Pinned source state reference changed'
reference=json.loads(REFERENCE_PATH.read_text(encoding='utf-8-sig'))
state_reference=json.loads(STATE_REFERENCE_PATH.read_text(encoding='utf-8-sig'))
assert state_reference['schema']=='rcps4-proton-pipeline-state-reference-v1'
assert state_reference['reference_blend_sha256']==PROFILE['source_blend']['sha256']
assembly_path=SOURCE_ROOT/'variant_assemblies/proton_drum/assembly_receipt.json'
assembly=json.loads(assembly_path.read_text(encoding='utf-8-sig'))
assert assembly['status']=='passed' and Path(assembly['blend_path']).resolve()==INPUT
assert assembly['blend_sha256']==input_hash
sys.path.insert(0,str(ROOT))
from rc2016.pipeline.weapons.proton_drum.washout_gate import validate_source_structure,validate_model_contract
actual_models=assembly['inputs']['models']
role_models={'main':actual_models['main'],'v1_pads':actual_models['v1'],'v5_pads':actual_models['v5']}
validate_model_contract(role_models,PROFILE['source_models'])
# Resolve model-local material manifests beneath the native assembly's output root.
manifest_paths={}
for name,old_rel in PROFILE['source_material_manifests'].items():manifest_paths[name]=relative_manifest(old_rel)
manifest_paths['wpn_Thumper_Art_wpn_Thumper_Glove_Glass']=relative_manifest(PROFILE['glass_material_manifest'])
for name,expected in PROFILE['all_polygon_used_materials'].items():
    mp=manifest_paths[name];d=json.loads(mp.read_text(encoding='utf-8-sig'))
    assert (d['path'],d['source_sha256'],d['asset_id'])==(expected['path'],expected['source_sha256'],expected['asset_id']),name
    for texture_path,b in PROFILE['source_texture_bindings'].items():
        if b['material_sha256']!=expected['source_sha256']:continue
        rows=[t for t in d['textures'] if t.get('path')==texture_path];assert len(rows)==1,(name,texture_path)
        t=rows[0];assert (t['built_sha256'],t['dds_sha256'],t['source_exact_top_mip'],t['texture_transport'].get('source_top_mip_sha256'))==(b['built_sha256'],b['dds_sha256'],True,b['source_top_mip_sha256'])
    if name=='wpn_Thumper_Art_wpn_Thumper_Glove_Glass':
        for texture_path,b in PROFILE['auxiliary_glass_texture_bindings'].items():
            rows=[t for t in d['textures'] if t.get('path')==texture_path];assert len(rows)==1
            t=rows[0];assert (t['built_sha256'],t['dds_sha256'],t['source_exact_top_mip'],t['texture_transport'].get('source_top_mip_sha256'))==(b['built_sha256'],b['dds_sha256'],b['source_exact_top_mip'],b['source_top_mip_sha256'])
# Fail closed before editing: source/graph topology, polygon use and camera/world/display must match.
bpy.ops.wm.open_mainfile(filepath=str(INPUT));sc=bpy.context.scene
model_hashes={v['sha256'] for v in PROFILE['source_models'].values()}
model_objects=[o for o in sc.objects if o.type=='MESH' and o.get('source_model_sha256') in model_hashes]
assert {o.get('source_model_sha256') for o in model_objects}==model_hashes
actual_used=[]
for o in model_objects:
    counts={}
    for p in o.data.polygons:
        material=o.material_slots[p.material_index].material
        if material:counts[material.name]=counts.get(material.name,0)+1
    for mn,count in sorted(counts.items()):
        ident=PROFILE['all_polygon_used_materials'][mn]
        actual_used.append({'object':o.name,'source_model_sha256':o.get('source_model_sha256'),'source_subset_index':o.get('source_subset_index'),
          'material_name':mn,'faces':count,'source_material_path':ident['path'],'source_material_sha256':ident['source_sha256'],'source_material_asset_id':ident['asset_id']})
actual_structure={'source_blend_sha256':input_hash,'materials':{},'polygon_used_materials':sorted(actual_used,key=lambda x:(x['source_model_sha256'],x['source_subset_index'],x['material_name']))}
for name,ident in PROFILE['all_polygon_used_materials'].items():
    m=bpy.data.materials[name];actual_structure['materials'][name]={**ident,**graph_data(m)}
state_before=state_sig();cam=state_before['camera'];world=state_before['world']
actual_structure['camera']={'name':cam['name'],'type':cam['type'],'matrix':[[round(float(v),9) for v in row] for row in cam['matrix']],
  'lens':cam['lens'],'shift':cam['shift'],'ortho_scale':cam['ortho_scale']}
actual_structure['world']=world
actual_structure['colour_management']=state_before['colour_management']
validate_source_structure(actual_structure,reference)
# Camera/world/color and assembled structure are additionally bound to the previously validated presentation reference.
old_state=state_reference['state']
for key in ['objects','actions','nla','camera','world','collections','colour_management']:
    assert state_before[key]==old_state[key],('Input build changed presentation invariant',key)
assert image_identity_multiset(state_before['packed_images'])==image_identity_multiset(old_state['packed_images']),'Packed source image identities changed'
# Ensure the supported Actions and assembled NLA exist before edit.
assert {'00000 | Activate_wpn_Thumper','00003 | Deactivate_wpn_Thumper'}<={a['name'] for a in state_before['actions']}
assert state_before['nla']
# Opaque body/V1/V5 material graphs are exact profile targets; glass remains unchanged.
target_names=set(PROFILE['source_material_manifests'].keys())
assert len(target_names)==3
materials=[bpy.data.materials[name] for name in sorted(target_names)]
for m in materials:
    mix=m.node_tree.nodes['Principled / recovered reconstruction'];bs=m.node_tree.nodes['Source-channel Principled']
    assert abs(mix.inputs['Fac'].default_value-1.0)<1e-6 and not bs.inputs['Specular Tint'].is_linked
    c=img_node(m,'base_color');g=img_node(m,'colored_specular_gloss');n=img_node(m,'normal')
    main='wpn_thumper_v5_' if m.name=='wpn_Thumper_V5' else 'wpn_thumper_glove_'
    for role,node in [('c',c),('g',g),('n',n)]:
        key=next(k for k in PROFILE['source_texture_bindings'] if k.endswith(main+role+'.texture'));bind=PROFILE['source_texture_bindings'][key]
        assert main in node.label and node.image.packed_file and sha(node.image.packed_file.data)==bind['dds_sha256']
        assert node.image.alpha_mode=='CHANNEL_PACKED'
    assert c.image.colorspace_settings.name=='sRGB' and g.image.colorspace_settings.name=='Non-Color' and n.image.colorspace_settings.name=='Non-Color'
# Make untouched fake-user recovery copies, then change only the documented Proton presentation controls.
baselines=[]
for m in materials:
    base=m.copy();base.name=m.name+' | source-structure baseline';base.use_fake_user=True;baselines.append(base.name)
    nt=m.node_tree;g=img_node(m,'colored_specular_gloss');bs=nt.nodes['Source-channel Principled'];mix=nt.nodes['Principled / recovered reconstruction']
    h=nt.nodes.new('ShaderNodeHueSaturation');h.name='Proton presentation | G specular chroma';h.label='Presentation-tuned | source-structure profile '+PROFILE_SHA[:12]
    h.inputs['Hue'].default_value=.5;h.inputs['Saturation'].default_value=PROFILE['presentation']['hue_saturation'];h.inputs['Value'].default_value=1;h.inputs['Fac'].default_value=1
    nt.links.new(g.outputs['Color'],h.inputs['Color']);nt.links.new(h.outputs['Color'],bs.inputs['Specular Tint']);mix.inputs['Fac'].default_value=PROFILE['presentation']['mix_factor']
sc.view_settings.view_transform=PROFILE['presentation']['view_transform'];sc.view_settings.look=PROFILE['presentation']['look'];sc.view_settings.exposure=PROFILE['presentation']['exposure']
sc['material_presentation_profile_sha256']=PROFILE_SHA;sc['material_presentation_evidence_class']='presentation_tuned; game_equivalence=false'
for m in materials:
    base=bpy.data.materials[m.name+' | source-structure baseline'];assert base.use_fake_user
    assert abs(base.node_tree.nodes['Principled / recovered reconstruction'].inputs['Fac'].default_value-1)<1e-6
    assert not base.node_tree.nodes['Source-channel Principled'].inputs['Specular Tint'].is_linked
    assert abs(m.node_tree.nodes['Principled / recovered reconstruction'].inputs['Fac'].default_value-.18)<1e-6
    assert m.node_tree.nodes['Source-channel Principled'].inputs['Specular Tint'].is_linked
assert (sc.view_settings.view_transform,sc.view_settings.look,sc.view_settings.exposure)==('Khronos PBR Neutral','None',-.5)
after=state_sig()
for key in ['objects','actions','nla','camera','world','collections','packed_images','render']:
    assert state_before[key]==after[key],('Unexpected non-presentation edit',key)
assert state_before['colour_management']!=after['colour_management']
receipt={'schema':'rcps4-proton-source-structure-presentation-application-v1','mode':'source_structure_bound_fresh_build',
  'profile_path':str(PROFILE_PATH),'profile_sha256':PROFILE_SHA,'source_structure_reference_sha256':PROFILE['source_structure_reference_sha256'],
  'source_state_reference_sha256':PROFILE['source_state_reference_sha256'],
  'reference_blend_sha256':PROFILE['source_blend']['sha256'],'input_assembly_path':str(INPUT),'input_assembly_sha256':input_hash,
  'source_assembly_receipt_sha256':file_sha(assembly_path),'source_models':PROFILE['source_models'],
  'polygon_used_materials':PROFILE['polygon_used_material_map'],'all_polygon_used_materials':PROFILE['all_polygon_used_materials'],
  'source_texture_bindings':PROFILE['source_texture_bindings'],'presentation':PROFILE['presentation'],
  'evidence_class':'presentation_tuned','game_equivalence':False,'baseline_materials':baselines,
  'before_state':state_before,'candidate_expected_state':after,'candidate_path':str(OUTPUT)}
OUT_DIR.joinpath('application_receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT));receipt['candidate_sha256']=file_sha(OUTPUT)
OUT_DIR.joinpath('application_receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print('SOURCE-STRUCTURE-BOUND PROTON CANDIDATE',OUTPUT,'input',input_hash,'output',receipt['candidate_sha256'],flush=True)




