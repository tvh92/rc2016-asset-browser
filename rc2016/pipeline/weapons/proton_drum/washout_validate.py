import bpy, hashlib, json, sys
from array import array
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
PARSER_ARGS=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--candidate-dir',required=True);args=parser.parse_args(PARSER_ARGS)
OUT=Path(args.candidate_dir).resolve()
PROFILE_PATH=Path(__file__).with_name('washout_structure_profile.json')
PROFILE=json.loads(PROFILE_PATH.read_text(encoding='utf-8-sig'))
PROFILE_SHA=hashlib.sha256(PROFILE_PATH.read_bytes()).hexdigest()
STATE_REFERENCE_PATH=Path(__file__).with_name('washout_state_reference.json')
RECEIPT=json.loads((OUT/'application_receipt.json').read_text(encoding='utf-8-sig'))
BLEND=Path(RECEIPT['candidate_path'])
def file_sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def image_sig(im):
    pixels=array('f',[0.0])*len(im.pixels);im.pixels.foreach_get(pixels)
    return {'name':im.name,'size':list(im.size),'colorspace':im.colorspace_settings.name,
        'alpha_mode':im.alpha_mode,'packed':bool(im.packed_file),
        'packed_data_sha256':hashlib.sha256(im.packed_file.data).hexdigest() if im.packed_file else None,
        'pixel_f32_sha256':hashlib.sha256(pixels.tobytes()).hexdigest()}
def object_sig(o):
    mesh=o.data;co=array('f',[0.0])*(len(mesh.vertices)*3);mesh.vertices.foreach_get('co',co)
    polys=[tuple(poly.vertices) for poly in mesh.polygons]
    material_indices=array('i',[p.material_index for p in mesh.polygons])
    weights=[]
    for v in mesh.vertices:
        for g in v.groups:weights.append((v.index,g.group,round(g.weight,8)))
    uv=[]
    for layer in mesh.uv_layers:
        values=array('f',[0.0])*(len(layer.data)*2);layer.data.foreach_get('uv',values)
        uv.append({'name':layer.name,'count':len(layer.data),'sha256':hashlib.sha256(values.tobytes()).hexdigest()})
    return {'name':o.name,'source_model_sha256':o.get('source_model_sha256'),'source_subset_index':o.get('source_subset_index'),
      'vertices':len(mesh.vertices),'polygons':len(mesh.polygons),'positions_sha256':hashlib.sha256(co.tobytes()).hexdigest(),
      'polygon_indices_sha256':hashlib.sha256(json.dumps(polys,separators=(',',':')).encode()).hexdigest(),
      'material_indices_sha256':hashlib.sha256(material_indices.tobytes()).hexdigest(),'uv':uv,
      'vertex_groups':[g.name for g in o.vertex_groups],'skin_weight_count':len(weights),
      'skin_weights_sha256':hashlib.sha256(json.dumps(weights,separators=(',',':')).encode()).hexdigest(),
      'armature_modifiers':[{'name':m.name,'object':m.object.name if m.object else None,'show_viewport':m.show_viewport,'show_render':m.show_render} for m in o.modifiers if m.type=='ARMATURE'],
      'materials':[s.material.name if s.material else None for s in o.material_slots]}
def scene_sig():
    sc=bpy.context.scene;source_hashes={v['sha256'] for v in PROFILE['source_models'].values()}
    objects=[object_sig(o) for o in sc.objects if o.type=='MESH' and o.get('source_model_sha256') in source_hashes]
    actions=[{'name':a.name,'frame_range':[float(a.frame_range[0]),float(a.frame_range[1])],'users':a.users} for a in bpy.data.actions]
    nla=[]
    for o in sc.objects:
        ad=o.animation_data
        if ad:nla.extend({'object':o.name,'track':t.name,'strips':[{'name':st.name,'action':st.action.name if st.action else None,'frame_start':st.frame_start,'frame_end':st.frame_end} for st in t.strips]} for t in ad.nla_tracks)
    cam=sc.camera;camera={'name':cam.name,'type':cam.data.type,'matrix':[[float(v) for v in row] for row in cam.matrix_world],
        'lens':cam.data.lens,'shift':[cam.data.shift_x,cam.data.shift_y],'ortho_scale':cam.data.ortho_scale}
    world=sc.world;world_image=None;world_strength=None
    if world and world.node_tree:
        for n in world.node_tree.nodes:
            if n.type=='TEX_ENVIRONMENT' and n.image:world_image=image_sig(n.image)
            if n.type=='BACKGROUND':world_strength=float(n.inputs['Strength'].default_value)
    collections={}
    for coll in ['PROTON_V1_PADS','PROTON_V5_PADS']:
        c=bpy.data.collections[coll]
        collections[coll]=[{'name':o.name,'hide_get':o.hide_get(),'hide_render':o.hide_render} for o in c.objects]
    return {'objects':sorted(objects,key=lambda x:x['name']),'actions':sorted(actions,key=lambda x:x['name']),'nla':nla,
        'camera':camera,'world':{'name':world.name if world else None,'image':world_image,'strength':world_strength},
        'collections':collections,'packed_images':sorted([image_sig(im) for im in bpy.data.images if im.packed_file],key=lambda x:x['name']),
        'render':{'engine':sc.render.engine,'resolution':[sc.render.resolution_x,sc.render.resolution_y,sc.render.resolution_percentage],
            'fps':sc.render.fps,'filepath':sc.render.filepath},
        'colour_management':[sc.view_settings.view_transform,sc.view_settings.look,sc.view_settings.exposure,sc.view_settings.gamma]}
def upstream(socket):
    assert len(socket.links)==1,(socket.name,len(socket.links))
    return socket.links[0].from_node
def source_image(m,role):
    matches=[n for n in m.node_tree.nodes if n.type=='TEX_IMAGE' and role in n.name.lower() and 'default_pink' not in n.label.lower()]
    assert len(matches)==1,(m.name,role)
    return matches[0]
def sha(data):return hashlib.sha256(data).hexdigest()
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

assert BLEND.is_file() and file_sha(BLEND)==RECEIPT['candidate_sha256']
assert RECEIPT['profile_sha256']==PROFILE_SHA and RECEIPT['mode']=='source_structure_bound_fresh_build'
assert RECEIPT['source_state_reference_sha256']==PROFILE['source_state_reference_sha256']
assert file_sha(STATE_REFERENCE_PATH)==PROFILE['source_state_reference_sha256']
assert RECEIPT['reference_blend_sha256']==PROFILE['source_blend']['sha256']
assert RECEIPT['input_assembly_sha256']!=RECEIPT['reference_blend_sha256']
assert RECEIPT['input_assembly_sha256']!=RECEIPT['reference_blend_sha256']
bpy.ops.wm.open_mainfile(filepath=str(BLEND));sc=bpy.context.scene
reference=json.loads((Path(__file__).with_name('washout_structure_reference.json')).read_text(encoding='utf-8-sig'))
glass_name='wpn_Thumper_Art_wpn_Thumper_Glove_Glass'
assert graph_data(bpy.data.materials[glass_name])=={k:reference['materials'][glass_name][k] for k in graph_data(bpy.data.materials[glass_name])},'Unmodified glass source graph changed'
assert sc.get('material_presentation_profile_sha256')==PROFILE_SHA
assert sc.get('material_presentation_evidence_class')=='presentation_tuned; game_equivalence=false'
expected=RECEIPT['candidate_expected_state'];actual=scene_sig();assert actual==expected,'Fresh-reopen scene, images, geometry, skin, animation, view, camera, light, or visibility signature differs'
assert sc.world.name=='PS4 Novalis World' and abs(actual['world']['strength']-1.0)<1e-6
assert set(PROFILE['source_models'][k]['sha256'] for k in PROFILE['source_models'])=={o.get('source_model_sha256') for o in sc.objects if o.type=='MESH' and o.get('source_model_sha256')}
assert {'00000 | Activate_wpn_Thumper','00003 | Deactivate_wpn_Thumper'}<={a.name for a in bpy.data.actions}
assert actual['nla'],'Source NLA strips were lost'
# Check baseline materials remain exact and have fake users.
for name in RECEIPT['baseline_materials']:
    base=bpy.data.materials[name];assert base.use_fake_user
    assert abs(base.node_tree.nodes['Principled / recovered reconstruction'].inputs['Fac'].default_value-1.0)<1e-6
    assert not base.node_tree.nodes['Source-channel Principled'].inputs['Specular Tint'].is_linked
# Validate modified material bindings and actual links through active Material Output.
modified=[]
for name in PROFILE['source_material_manifests']:
    m=bpy.data.materials[name];nt=m.node_tree
    mix=nt.nodes['Principled / recovered reconstruction'];assert abs(mix.inputs['Fac'].default_value-.18)<1e-6
    bs=nt.nodes['Source-channel Principled'];h=nt.nodes['Proton presentation | G specular chroma']
    assert abs(h.inputs['Saturation'].default_value-2.0)<1e-6 and len(h.inputs['Color'].links)==1
    g=source_image(m,'colored_specular_gloss');assert h.inputs['Color'].links[0].from_node==g
    tint=bs.inputs['Specular Tint'];assert len(tint.links)==1 and tint.links[0].from_node==h
    out=next(n for n in nt.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output)
    assert any(l.from_node==mix and l.from_socket==mix.outputs['Shader'] and l.to_node==out and l.to_socket==out.inputs['Surface'] for l in nt.links)
    c=source_image(m,'base_color');n=source_image(m,'normal')
    assert upstream(bs.inputs['Base Color'])==c and upstream(bs.inputs['Emission Color'])==c
    assert upstream(bs.inputs['Emission Strength']).name=='_c alpha emission threshold'
    rough=nt.nodes['Roughness = 1 - _g alpha'];gloss=nt.nodes['Recovered colored glossy']
    assert upstream(bs.inputs['Roughness'])==rough and upstream(gloss.inputs['Roughness'])==rough
    assert upstream(gloss.inputs['Color'])==g
    normal=nt.nodes['DirectX tangent normal (source green retained)']
    assert upstream(bs.inputs['Normal'])==normal and upstream(normal.inputs['Color'])==n
    # Preserve both equation inputs and all material lobes outside the new specular-tint route.
    sub=nt.nodes['White minus _g'];mul=nt.nodes['_c times (1 - _g)'];diff=nt.nodes['Recovered diffuse']
    add=nt.nodes['Recovered Diffuse plus Glossy']
    assert upstream(sub.inputs[1])==g and upstream(mul.inputs[0])==c and upstream(mul.inputs[1])==sub
    assert upstream(diff.inputs['Color'])==mul and upstream(add.inputs[0])==diff and upstream(add.inputs[1])==gloss
    assert upstream(mix.inputs[1])==bs and upstream(mix.inputs[2])==add
    for role,node,suffix in [('c',c,'_c'),('g',g,'_g'),('n',n,'_n')]:
        key=next(k for k in PROFILE['source_texture_bindings'] if k.endswith(('wpn_thumper_v5_' if name=='wpn_Thumper_V5' else 'wpn_thumper_glove_')+role+'.texture'))
        b=PROFILE['source_texture_bindings'][key]
        assert hashlib.sha256(node.image.packed_file.data).hexdigest()==b['dds_sha256']
        assert node.image.alpha_mode=='CHANNEL_PACKED'
    assert c.image.colorspace_settings.name=='sRGB' and g.image.colorspace_settings.name=='Non-Color' and n.image.colorspace_settings.name=='Non-Color'
    modified.append({'name':name,'material_source_sha256':PROFILE['all_polygon_used_materials'][name]['source_sha256'],
        'mix_factor':mix.inputs['Fac'].default_value,'g_image':g.image.name,'g_packed_sha256':hashlib.sha256(g.image.packed_file.data).hexdigest(),
        'specular_tint_route':g.name+' -> '+h.name+' -> '+bs.name+'.Specular Tint'})
assert tuple([sc.view_settings.view_transform,sc.view_settings.look,sc.view_settings.exposure])==('Khronos PBR Neutral','None',-.5)
# 25% review renders use the shared review HDRI override, with saved Novalis world restored.
sys.path.insert(0,str(ROOT));from rc2016.pipeline.builder import review_preview_lighting
review_dir=OUT/'review';review_dir.mkdir(parents=True,exist_ok=True)
original=scene_sig();frame=sc.frame_current
old_res=(sc.render.engine,sc.render.resolution_x,sc.render.resolution_y,sc.render.resolution_percentage,sc.cycles.samples,sc.render.filepath)
sc.render.engine='CYCLES';sc.render.resolution_percentage=25;sc.cycles.samples=12;sc.cycles.use_denoising=True
proofs=[]
with review_preview_lighting(sc) as light:
    for version in ['V1','V5']:
        for coll_name in ['PROTON_V1_PADS','PROTON_V5_PADS']:
            visible=coll_name=='PROTON_'+version+'_PADS'
            for obj in bpy.data.collections[coll_name].objects:
                obj.hide_set(not visible);obj.hide_render=not visible
        path=review_dir/f'Proton_Drum_{version}_presentation_25pct.png';sc.render.filepath=str(path)
        bpy.ops.render.render(write_still=True)
        proofs.append({'variant':version,'path':str(path),'sha256':file_sha(path),'size':[sc.render.resolution_x*sc.render.resolution_percentage//100,sc.render.resolution_y*sc.render.resolution_percentage//100],
            'lighting':light,'camera':sc.camera.name})
# Restore object vis and all temporary render configuration exactly.
for coll_name in ['PROTON_V1_PADS','PROTON_V5_PADS']:
    for row in expected['collections'][coll_name]:
        obj=bpy.data.objects[row['name']];obj.hide_set(row['hide_get']);obj.hide_render=row['hide_render']
sc.render.engine=old_res[0];sc.render.resolution_x=old_res[1] if False else sc.render.resolution_x
# Restore from the serialized expected source scene settings, which were unchanged by the candidate edit.
sc.render.resolution_x=expected['render']['resolution'][0];sc.render.resolution_y=expected['render']['resolution'][1];sc.render.resolution_percentage=expected['render']['resolution'][2]
sc.render.filepath=expected['render']['filepath'];sc.cycles.samples=RECEIPT['before_state']['render'].get('cycles_samples',sc.cycles.samples)
sc.frame_set(frame)
assert scene_sig()==original,'Review render did not restore saved scene state'
report={'schema':'rcps4-proton-presentation-reopen-validation-v1','status':'passed','candidate_path':str(BLEND),
    'candidate_sha256':file_sha(BLEND),'profile_sha256':PROFILE_SHA,'source_state_reference_sha256':PROFILE['source_state_reference_sha256'],'evidence_class':'presentation_tuned','game_equivalence':False,'reference_blend_sha256':RECEIPT['reference_blend_sha256'],'input_assembly_sha256':RECEIPT['input_assembly_sha256'],'fresh_process_reopen':True,
    'source_models':PROFILE['source_models'],'source_material_sha256':{k:v['source_sha256'] for k,v in PROFILE['all_polygon_used_materials'].items()},
    'modified_materials':modified,'baseline_materials':RECEIPT['baseline_materials'],
    'scene_signature_match':True,'checks':['mesh topology/positions/UV/material indices','skin weights/groups/armature targets','source model hashes/subsets','Actions and NLA','packed image hashes/dimensions/colorspace/alpha mode','camera transform/projection/lens/shift','Novalis world/probe/strength restored','V1/V5 eye/render states','actual active output and source C/G/N texture links'],
    'proof_renders':proofs,'render_settings_restored':scene_sig()==original}
(OUT/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('FRESH PROCESS PROTON PRESENTATION VALIDATION PASSED',json.dumps(report),flush=True)









