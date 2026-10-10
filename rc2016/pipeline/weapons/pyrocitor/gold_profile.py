"""Hash-bound Pyrocitor-only canonical material/display correction for the pipeline scene."""
import argparse, hashlib, json, math, os, sqlite3, sys
from array import array
from pathlib import Path
from bpy_extras.object_utils import world_to_camera_view
import bpy
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from rc2016.common import load, dump, file_hash
from rc2016.pipeline.scene_defaults import audit_scene, _configure_optix_gpu
from rc2016.pipeline.weapons import research_file
PROFILE_SCHEMA='pyrocitor-gold-presentation-profile-v1'

def sha_bytes(data): return hashlib.sha256(data).hexdigest()
def canonical_file(p): return Path(bpy.path.abspath(str(p))).resolve()
def image_file_name(img):
    try: return Path(bpy.path.abspath(img.filepath)).name.casefold()
    except Exception: return Path(img.name).name.casefold()
def used_slots(obj):
    return sorted({int(poly.material_index) for poly in obj.data.polygons})
def source_images(profile):
    result={}
    for role,entry in profile['materials'].items():
        for channel,tex in entry['textures'].items():
            rows=[img for img in bpy.data.images if img.get('source_texture_path')==tex['path']]
            if not rows: raise AssertionError(f"Missing tagged source image {tex['path']}")
            for img in rows:
                if (img.get('source_texture_sha256')!=tex['sha256'] or img.colorspace_settings.name!=tex['colorspace']
                    or img.alpha_mode!='CHANNEL_PACKED' or not img.packed_file):
                    raise AssertionError(f"Source texture identity/colour space/packed alpha changed: {tex['path']}")
            result[(role,channel)]=rows[0]
    return result

def canonical_materials(profile, imported):
    found={}
    for role,entry in profile['materials'].items():
        wanted={Path(tex['path']).stem+'.dds' for tex in entry['textures'].values()}
        candidates=[]
        for mat in imported:
            nt=mat.node_tree
            if not nt: continue
            image_names={image_file_name(n.image) for n in nt.nodes if n.type=='TEX_IMAGE' and n.image}
            if wanted.issubset(image_names): candidates.append(mat)
        structural=[]
        for candidate in candidates:
            try: assert_canonical_recipe(role,candidate,entry,require_explicit=False)
            except (AssertionError,StopIteration,IndexError): continue
            structural.append(candidate)
        if len(structural)!=1:
            raise AssertionError(f"Canonical recipe {role} must be uniquely resolved by image set plus active-output/node-link recipe; candidates={[m.name for m in candidates]}, structural={[m.name for m in structural]}")
        mat=structural[0]
        found[role]=mat
    if len({m.as_pointer() for m in found.values()})!=len(found): raise AssertionError('Canonical image-set recipes are not unique')
    return found

def node_link(nt,src,src_socket,dst,dst_socket):
    return any(l.from_node==src and l.from_socket.name==src_socket and l.to_node==dst and l.to_socket.name==dst_socket for l in nt.links)
def node_link_indices(nt,src,src_index,dst,dst_index):
    return any(l.from_node==src and list(src.outputs).index(l.from_socket)==src_index and l.to_node==dst and list(dst.inputs).index(l.to_socket)==dst_index for l in nt.links)
def active_material_nodes(mat):
    outputs=[n for n in mat.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    assert len(outputs)==1, (mat.name,'active output count')
    surface=[l for l in outputs[0].inputs['Surface'].links]
    assert len(surface)==1, (mat.name,'active output Surface')
    seen=set();pending=[surface[0].from_node]
    while pending:
        node=pending.pop()
        if node in seen: continue
        seen.add(node)
        pending.extend(l.from_node for socket in node.inputs for l in socket.links)
    return outputs[0],surface[0].from_node,seen

def assert_canonical_recipe(role,mat,entry,require_explicit=True):
    nt=mat.node_tree
    outputs=[n for n in nt.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    assert len(outputs)==1, (role,'active material output count')
    surface=[l for l in nt.links if l.to_node==outputs[0] and l.to_socket.name=='Surface']
    assert len(surface)==1,(role,'active output Surface binding')
    wanted={Path(t['path']).stem+'.dds' for t in entry['textures'].values()}
    images={image_file_name(node.image):node for node in nt.nodes if node.type=='TEX_IMAGE' and node.image}
    for channel,texture in entry['textures'].items():
        tagged=[node for node in nt.nodes if node.type=='TEX_IMAGE' and node.image and node.image.get('source_texture_path')==texture['path']]
        if tagged: images[Path(texture['path']).stem+'.dds']=tagged[0]
    assert wanted.issubset(images),(role,wanted,set(images))
    c=images[Path(entry['textures']['c']['path']).stem+'.dds'];g=images[Path(entry['textures']['g']['path']).stem+'.dds'];n=images[Path(entry['textures']['n']['path']).stem+'.dds']
    normals=[x for x in nt.nodes if x.type=='NORMAL_MAP']
    assert len(normals)==1 and node_link(nt,n,'Color',normals[0],'Color'),(role,'normal image -> Normal Map')
    principled=[x for x in nt.nodes if x.type=='BSDF_PRINCIPLED']
    assert len(principled)==1,(role,'Principled count')
    p=principled[0]; assert node_link(nt,normals[0],'Normal',p,'Normal'),(role,'Normal Map -> Principled')
    if role=='pyro':
        assert node_link(nt,c,'Color',p,'Base Color'), 'Pyrociter base-color source link'
        mix=surface[0].from_node; assert mix.type=='MIX_SHADER' and abs(float(mix.inputs[0].default_value)-0.18)<1e-5
        add=next(x for x in nt.nodes if x.type=='ADD_SHADER')
        diffuse=next(x for x in nt.nodes if x.type=='BSDF_DIFFUSE');glossy=next(x for x in nt.nodes if x.type=='BSDF_GLOSSY')
        subtract=next(x for x in nt.nodes if x.type=='MATH' and x.operation=='SUBTRACT')
        rough_floor=next(x for x in nt.nodes if x.type=='MATH' and x.operation=='MAXIMUM')
        assert node_link_indices(nt,p,0,mix,1) and node_link_indices(nt,add,0,mix,2),'Principled and reconstruction branches -> 0.18 Mix Shader'
        assert node_link_indices(nt,diffuse,0,add,0) and node_link_indices(nt,glossy,0,add,1),'Diffuse + Glossy -> Add Shader'
        assert node_link_indices(nt,g,0,glossy,0),'Pyrociter gloss RGB -> Glossy colour'
        assert node_link_indices(nt,normals[0],0,diffuse,2) and node_link_indices(nt,normals[0],0,glossy,4),'Normal feeds all Pyrociter branches'
        assert node_link_indices(nt,g,1,subtract,1) and abs(float(subtract.inputs[0].default_value)-1.0)<1e-6,'Gloss alpha -> white-minus-alpha'
        assert node_link_indices(nt,subtract,0,rough_floor,0) and node_link_indices(nt,rough_floor,0,glossy,1) and node_link_indices(nt,rough_floor,0,p,2),'Gloss roughness feeds both shader branches'
        assert abs(float(rough_floor.inputs[1].default_value)-0.30)<1e-5
        if require_explicit:
            output,active_surface,active=active_material_nodes(mat)
            assert active_surface==mix and mix in active
            vector_subtract=[x for x in active if x.type=='VECT_MATH' and x.operation=='SUBTRACT']
            vector_multiply=[x for x in active if x.type=='VECT_MATH' and x.operation=='MULTIPLY']
            assert len(vector_subtract)==len(vector_multiply)==1,'Active Pyrociter branch requires explicit Vector Math SUBTRACT then MULTIPLY'
            white_minus_g=vector_subtract[0];product=vector_multiply[0]
            assert all(abs(float(a)-1.0)<1e-6 for a in white_minus_g.inputs[0].default_value),'White-minus-_g left operand must be white'
            assert node_link_indices(nt,g,0,white_minus_g,1),'_g RGB -> explicit white-minus Subtract'
            assert node_link_indices(nt,c,0,product,0) and node_link_indices(nt,white_minus_g,0,product,1),'_c and (1-_g) -> explicit Multiply'
            assert node_link_indices(nt,product,0,diffuse,0),'Explicit Multiply -> Diffuse colour'
            assert not [x for x in active if x.type in ('INVERT','MIX_RGB')],'Legacy Invert/MixRGB arithmetic remains in active Pyrociter path'
    elif role in ('tank','hose'):
        assert surface[0].from_node==p
        assert node_link(nt,c,'Color',p,'Base Color') and node_link(nt,g,'Color',p,'Specular Tint')
        subtract=next(x for x in nt.nodes if x.type=='MATH' and x.operation=='SUBTRACT')
        assert node_link_indices(nt,g,1,subtract,1) and abs(float(subtract.inputs[0].default_value)-1.0)<1e-6,(role,'gloss alpha -> white-minus-alpha')
        assert abs(float(normals[0].inputs['Strength'].default_value)-1.0)<1e-6
        rough_nodes=[x for x in nt.nodes if x.type=='MATH' and x.operation=='MAXIMUM']
        if role=='tank':
            assert len(rough_nodes)==1 and node_link_indices(nt,rough_nodes[0],0,p,2)
            assert any(abs(float(s.default_value)-0.62)<1e-5 for s in rough_nodes[0].inputs if not s.is_linked),'tank roughness floor 0.62'
        else:
            assert len(rough_nodes)==1 and node_link_indices(nt,rough_nodes[0],0,p,2)
            assert any(abs(float(s.default_value)-0.30)<1e-5 for s in rough_nodes[0].inputs if not s.is_linked),'hose roughness floor 0.30'
    else:
        assert surface[0].from_node==p
        ramp=next(x for x in nt.nodes if x.type=='VALTORGB')
        maprange=next(x for x in nt.nodes if x.type=='MAP_RANGE')
        tile=next(x for x in nt.nodes if x.type=='VECT_MATH' and x.operation=='SCALE')
        texcoord=next(x for x in nt.nodes if x.type=='TEX_COORD')
        c=images[Path(entry['textures']['c']['path']).stem+'.dds']
        assert node_link_indices(nt,c,0,ramp,0) and node_link_indices(nt,ramp,0,p,0),'grate color -> silver ramp -> Base Color'
        assert node_link_indices(nt,c,0,maprange,0) and node_link_indices(nt,maprange,0,p,1),'grate color -> metal range -> Metallic'
        assert abs(float(p.inputs['Roughness'].default_value)-0.40)<1e-5 and abs(float(p.inputs['Specular IOR Level'].default_value))<1e-5
        ramp_elements=ramp.color_ramp.elements
        assert len(ramp_elements)==2 and abs(float(ramp_elements[0].position)-0.012)<1e-5 and abs(float(ramp_elements[1].position)-0.075)<1e-5
        assert all(abs(float(a)-b)<1e-5 for a,b in zip(ramp_elements[0].color,(0.002,0.002,0.002,1.0))) and all(abs(float(a)-b)<1e-5 for a,b in zip(ramp_elements[1].color,(0.55,0.57,0.59,1.0)))
        assert abs(float(maprange.inputs['From Min'].default_value)-0.012)<1e-5 and abs(float(maprange.inputs['From Max'].default_value)-0.075)<1e-5 and abs(float(maprange.inputs['To Max'].default_value)-0.85)<1e-5
        assert abs(float(tile.inputs['Scale'].default_value)-1.0)<1e-6 and node_link_indices(nt,texcoord,2,tile,0)
        for node in (c,g,n): assert node_link_indices(nt,tile,0,node,0),'UV tile scale 1 feeds each grate source map'

def convert_pyro_diffuse_graph(mat,entry):
    """Replace only the legacy colour inversion/multiply nodes with explicit vector math."""
    nt=mat.node_tree
    def tagged(channel):
        tex=entry['textures'][channel]
        rows=[x for x in nt.nodes if x.type=='TEX_IMAGE' and x.image and
              x.image.get('source_texture_path')==tex['path'] and x.image.get('source_texture_sha256')==tex['sha256']]
        assert len(rows)==1,('hash-bound Pyrocitor texture node',channel,len(rows))
        return rows[0]
    c,g=tagged('c'),tagged('g')
    diffuse=next(x for x in nt.nodes if x.type=='BSDF_DIFFUSE')
    glossy=next(x for x in nt.nodes if x.type=='BSDF_GLOSSY')
    old_link=next((l for l in diffuse.inputs[0].links),None)
    assert old_link is not None and old_link.from_node.type=='MIX_RGB' and old_link.from_node.blend_type=='MULTIPLY', 'Expected canonical legacy diffuse color product'
    legacy_product=old_link.from_node
    invert_link=next((l for l in legacy_product.inputs[2].links),None)
    assert invert_link and invert_link.from_node.type=='INVERT', 'Expected legacy inverse-_g color node'
    legacy_invert=invert_link.from_node
    assert node_link_indices(nt,c,0,legacy_product,1) and node_link_indices(nt,g,0,legacy_invert,1), 'Legacy operation is not _c * (1-_g RGB)'
    assert node_link_indices(nt,g,0,glossy,0), 'Glossy colored reflection must keep source _g RGB'
    assert len(legacy_product.outputs[0].links)==1 and len(legacy_invert.outputs[0].links)==1, 'Legacy arithmetic nodes have unexpected consumers'

    subtract=nt.nodes.new('ShaderNodeVectorMath')
    subtract.operation='SUBTRACT';subtract.inputs[0].default_value=(1.0,1.0,1.0)
    subtract.label='Explicit white minus source _g RGB'
    multiply=nt.nodes.new('ShaderNodeVectorMath')
    multiply.operation='MULTIPLY';multiply.label='Explicit source _c times inverse _g RGB'
    nt.links.new(g.outputs['Color'],subtract.inputs[1])
    nt.links.new(c.outputs['Color'],multiply.inputs[0])
    nt.links.new(subtract.outputs[0],multiply.inputs[1])
    nt.links.remove(old_link)
    nt.links.new(multiply.outputs[0],diffuse.inputs[0])
    nt.nodes.remove(legacy_product);nt.nodes.remove(legacy_invert)
    assert_canonical_recipe('pyro',mat,entry,require_explicit=True)
    return {'subtract_operation':'SUBTRACT','subtract_links':['white','_g RGB'],'multiply_operation':'MULTIPLY',
            'multiply_links':['_c RGB','Subtract output'],'diffuse_link':'Multiply output',
            'legacy_nodes_removed':['ShaderNodeInvert','ShaderNodeMixRGB MULTIPLY']}

def geometry_signature(obj):
    mesh=obj.data
    payload={'vertices':[tuple(float(c) for c in v.co) for v in mesh.vertices],
             'polygons':[(tuple(int(i) for i in p.vertices),int(p.material_index)) for p in mesh.polygons]}
    return sha_bytes(json.dumps(payload,separators=(',',':'),allow_nan=False).encode('utf-8'))

def normalized_path(path):
    return str(path).replace('\\','/').strip('/').casefold()

def unresolved_source_records(job,profile,objects_by_hash=None):
    output_root=Path(job['report_json']).resolve().parents[2]
    db_path=output_root/'reports'/'asset_index.sqlite'
    assert db_path.is_file(),f'Archive asset index is missing for unresolved source check: {db_path}'
    records=[]
    for spec in profile.get('unresolved_source_materials',[]):
        with sqlite3.connect(str(db_path)) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute('SELECT id,path,type,built_path,available FROM assets WHERE path=?',
                                               (normalized_path(spec['source_material_path']),))]
        if rows:
            raise AssertionError(f"Unresolved material path now exists in the exact indexed archive; bind and hash it before applying: {rows}")
        main_id=profile['source_models'][spec['model_role']]['asset_id']
        model_stem=Path(profile['source_models'][spec['model_role']].get('path','equipment/weapon/wpn_pyrociter/wpn_pyrociter.model')).stem
        material_manifest=output_root/f'{model_stem}_{main_id[:8]}'/'materials'/'manifest.json'
        assert material_manifest.is_file(),f'Material lookup manifest is missing: {material_manifest}'
        manifest=json.loads(material_manifest.read_text(encoding='utf-8'))
        errors=[e for e in manifest.get('errors',[]) if normalized_path(e.get('path',''))==normalized_path(spec['source_material_path'])]
        assert len(errors)==1 and errors[0].get('status')=='failed',('Expected one explicit failed material lookup',spec['source_material_path'],errors)
        error=errors[0]
        assert 'found 0' in error.get('reason',''),('Material path resolved ambiguously or with a different failure reason',error)
        row={'status':'unresolved','model_role':spec['model_role'],'model_sha256':spec['model_sha256'],
             'section':int(spec['section']),'source_material_index':int(spec['source_material_index']),
             'source_material_path':spec['source_material_path'],'source_material_sha256':None,
             'archive_index_lookup':{'database':str(db_path),'sha256':file_hash(db_path),'exact_path_match_count':0,'matches':rows},
             'material_manifest_lookup':{'manifest':str(material_manifest),'sha256':file_hash(material_manifest),
                                         'status':error['status'],'reason':error['reason']}}
        if objects_by_hash is not None:
            obj=next((o for o in objects_by_hash[spec['model_role']]
                      if int(o.get('source_subset_index',-1))==int(spec['section'])),None)
            assert obj is not None,f"Unresolved source section missing: {spec['model_role']}/{spec['section']}"
            assert used_slots(obj)==[0],f"Unresolved source section has unexpected polygon material slots: {obj.name}"
            material=obj.data.materials[0]
            assert material and normalized_path(material.get('source_material_path',''))==normalized_path(spec['source_material_path'])
            assert not material.get('source_material_sha256'), 'Unresolved material unexpectedly acquired an unverified source hash'
            row.update({'object':obj.name,'material_name':material.name,'mesh_data_name':obj.data.name,
                        'mesh_geometry_sha256':geometry_signature(obj),'preserved_material':True,'preserved_geometry':True})
        records.append(row)
    return records

def imported_world(worlds,profile):
    matches=[]
    for world in worlds:
        nt=world.node_tree
        if not nt: continue
        env=[n for n in nt.nodes if n.type=='TEX_ENVIRONMENT' and n.image]
        bg=[n for n in nt.nodes if n.type=='BACKGROUND']
        out=[n for n in nt.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output]
        if len(env)==1 and len(bg)==1 and len(out)==1 and abs(float(bg[0].inputs['Strength'].default_value)-profile['world']['strength'])<1e-6 and image_file_name(env[0].image)==profile['world']['path'].casefold():
            matches.append((world,env[0]))
    if len(matches)!=1: raise AssertionError(f"Canonical world must be uniquely matched by packed EXR node and strength: {len(matches)}")
    return matches[0]

def scene_snapshot(scene):
    cam=scene.camera
    if not cam: raise AssertionError('Perspective presentation camera missing')
    return {'camera_name':cam.name,'camera_type':cam.data.type,'matrix_world':[[float(cam.matrix_world[i][j]) for j in range(4)] for i in range(4)],'lens':float(cam.data.lens),'shift':[float(cam.data.shift_x),float(cam.data.shift_y)],'resolution':[int(scene.render.resolution_x),int(scene.render.resolution_y),int(scene.render.resolution_percentage)],'engine':scene.render.engine,'device':scene.cycles.device,'samples':int(scene.cycles.samples),'file_format':scene.render.image_settings.file_format,'color_mode':scene.render.image_settings.color_mode,'film_transparent':bool(scene.render.film_transparent),'view_transform':scene.view_settings.view_transform,'look':scene.view_settings.look,'exposure':float(scene.view_settings.exposure),'gamma':float(scene.view_settings.gamma)}

def apply_visibility(scene,state):
    for name,visible in (('PYROCITOR_SHARED_MAIN',True),('PYROCITOR_V1_BARREL',state=='V1'),('PYROCITOR_V5_BARREL',state=='V5')):
        c=bpy.data.collections.get(name)
        if c is None or c.hide_viewport or c.hide_render: raise AssertionError(f'Variant collection missing/disabled: {name}')
        for o in c.objects: o.hide_set(not visible); o.hide_render=not visible

def selected_meshes(scene,profile):
    hashes={k:v['sha256'] for k,v in profile['source_models'].items()}; out={k:[] for k in hashes}
    for obj in scene.objects:
        if obj.type=='MESH' and obj.get('source_model_sha256') in hashes.values():
            key=next(k for k,v in hashes.items() if v==obj.get('source_model_sha256'));out[key].append(obj)
    return out

def build(job):
    profile=load(job['profile_path']); assert profile['schema']==PROFILE_SCHEMA
    assert file_hash(job['profile_path'])==job['profile_sha256']
    assert file_hash(job['source_blend'])==job['source_sha256']
    assert file_hash(research_file(profile['canonical_scene']['path']))==profile['canonical_scene']['sha256']
    assert file_hash(research_file(profile['world']['path']))==profile['world']['sha256']
    bpy.ops.wm.open_mainfile(filepath=job['source_blend']); scene=bpy.context.scene
    _configure_optix_gpu(bpy)
    before=scene_snapshot(scene)
    assert before['view_transform']=='AgX' and before['engine']=='CYCLES' and before['resolution']==[3840,2560,100]
    original_camera=before
    src_images=source_images(profile)
    canonical=(research_file(profile['canonical_scene']['path'])).resolve()
    mat_names=[entry['canonical_hint'] for entry in profile['materials'].values()]
    with bpy.data.libraries.load(str(canonical),link=False) as (src,dst):
        dst.materials=list(src.materials); dst.worlds=list(src.worlds)
    imported=[m for m in dst.materials if m]; worlds=[w for w in dst.worlds if w]
    mats=canonical_materials(profile,imported)
    world,world_env=imported_world(worlds,profile)
    # Rebind canonical recipe image nodes to this scene's exact hash-tagged source images.
    for role,mat in mats.items():
        for node in mat.node_tree.nodes:
            if node.type!='TEX_IMAGE' or not node.image: continue
            fname=image_file_name(node.image)
            for channel,texture in profile['materials'][role]['textures'].items():
                if fname== (Path(texture['path']).stem+'.dds').casefold():
                    source=src_images[(role,channel)]
                    if tuple(node.image.size)!=tuple(source.size): raise AssertionError(f"Canonical/source dimensions differ for {role}.{channel}")
                    node.image=source
        mat.use_fake_user=True
        mat['pyrocitor_gold_profile_id']=profile['profile_id'];mat['pyrocitor_gold_recipe_role']=role
        mat['source_material_path']=profile['materials'][role]['source_material_path']
        mat['source_material_sha256']=profile['materials'][role]['source_material_sha256']
        mat['pyrocitor_gold_canonical_scene_sha256']=profile['canonical_scene']['sha256']
    graph_conversion=convert_pyro_diffuse_graph(mats['pyro'],profile['materials']['pyro'])
    for role,mat in mats.items(): assert_canonical_recipe(role,mat,profile['materials'][role],require_explicit=True)
    # Exact section + source slot + material path is the selector; exported names are not consulted.
    by_hash=selected_meshes(scene,profile); applied=[]
    unresolved=unresolved_source_records(job,profile,by_hash)
    unresolved_refs=[]
    for row in unresolved:
        obj=next(o for o in by_hash[row['model_role']] if int(o.get('source_subset_index',-1))==row['section'])
        unresolved_refs.append((obj,obj.data,obj.data.materials[0]))
    for section in profile['sections']:
        key=next(k for k,v in profile['source_models'].items() if v['sha256']==section['model_sha256'])
        matches=[o for o in by_hash[key] if int(o.get('source_subset_index',-1))==section['section']]
        if len(matches)!=1: raise AssertionError(f"Expected one {key} source section {section['section']}, got {len(matches)}")
        obj=matches[0]; slots=used_slots(obj)
        if slots!=[0]: raise AssertionError(f"Section {key}/{section['section']} used material slots changed: {slots}")
        old=obj.data.materials[0]
        if not old or str(old.get('source_material_path','')).casefold()!=section['source_material_path'].casefold() or old.get('source_material_sha256')!=section['source_material_sha256']:
            raise AssertionError(f"Source material identity differs at {key}/{section['section']}")
        if int(obj.get('source_material_index',-1))!=section['source_material_index']: raise AssertionError(f"Source table material index changed at {key}/{section['section']}")
        old.use_fake_user=True
        mat=mats[section['recipe']]
        obj.data.materials[0]=mat
        applied.append({'model_sha256':section['model_sha256'],'section':section['section'],'source_material_index':section['source_material_index'],'source_material_path':section['source_material_path'],'source_material_sha256':section['source_material_sha256'],'recipe':section['recipe'],'profile_material':mat.name,'baseline_material':old.name})
    for row,(obj,mesh,material) in zip(unresolved,unresolved_refs):
        assert obj.data==mesh and obj.data.materials[0]==material,'Unresolved glass geometry/material was changed in memory'
        assert geometry_signature(obj)==row['mesh_geometry_sha256'],'Unresolved glass geometry changed in memory'
    envfile=(research_file(profile['world']['path'])).resolve()
    world_img=bpy.data.images.load(str(envfile),check_existing=False)
    world_img.colorspace_settings.name='Linear Rec.709'; world_img['pyrocitor_gold_world_path']=profile['world']['path'];world_img['pyrocitor_gold_world_sha256']=profile['world']['sha256'];world_img.pack()
    world_env.image=world_img; world.use_fake_user=True; scene.world=world
    scene['lighting_source']='hdri'
    for key in ('lighting_world_strength','lighting_probe_sha256','lighting_region'):
        if key in scene: del scene[key]
    scene.view_settings.view_transform=profile['color_management']['view_transform']
    scene.view_settings.look=profile['color_management']['look']
    scene.view_settings.exposure=float(profile['color_management']['exposure'])
    scene.view_settings.gamma=float(profile['color_management']['gamma'])
    scene['pyrocitor_gold_profile_id']=profile['profile_id'];scene['pyrocitor_gold_profile_sha256']=job['profile_sha256']
    scene['pyrocitor_gold_canonical_scene_sha256']=profile['canonical_scene']['sha256']
    scene['pyrocitor_gold_source_blend_sha256']=job['source_sha256']
    scene['pyrocitor_gold_assignments_json']=json.dumps(applied,sort_keys=True)
    scene['pyrocitor_gold_unresolved_materials_json']=json.dumps(unresolved,sort_keys=True)
    scene['pyrocitor_gold_pyro_graph_conversion_json']=json.dumps(graph_conversion,sort_keys=True)
    scene['pyrocitor_gold_camera_snapshot_json']=json.dumps(original_camera,sort_keys=True)
    bpy.ops.file.pack_all()
    candidate=Path(job['output_blend']);candidate.parent.mkdir(parents=True,exist_ok=True)
    if candidate.exists(): raise FileExistsError(f'Refusing to overwrite presentation candidate {candidate}')
    bpy.ops.wm.save_as_mainfile(filepath=str(candidate),compress=True)
    print(json.dumps({'status':'built','candidate':str(candidate),'assignments':applied},indent=2),flush=True)

def validate(job):
    profile=load(job['profile_path']);assert file_hash(job['profile_path'])==job['profile_sha256']
    assert file_hash(job['source_blend'])==job['source_sha256']
    assert file_hash(research_file(profile['canonical_scene']['path']))==profile['canonical_scene']['sha256']
    assert file_hash(research_file(profile['canonical_scene']['path']))==job['canonical_scene_sha256']
    _configure_optix_gpu(bpy); bpy.ops.wm.open_mainfile(filepath=job['output_blend']);_configure_optix_gpu(bpy)
    scene=bpy.context.scene
    assert file_hash(job['output_blend'])==job['output_sha256']
    assert scene.get('pyrocitor_gold_profile_id')==profile['profile_id'] and scene.get('pyrocitor_gold_profile_sha256')==job['profile_sha256']
    assert scene.get('pyrocitor_gold_source_blend_sha256')==job['source_sha256']
    graph_conversion=json.loads(scene['pyrocitor_gold_pyro_graph_conversion_json'])
    assert graph_conversion['subtract_operation']=='SUBTRACT' and graph_conversion['multiply_operation']=='MULTIPLY'
    unresolved_stored=json.loads(scene['pyrocitor_gold_unresolved_materials_json'])
    snap=scene_snapshot(scene); expected=json.loads(scene['pyrocitor_gold_camera_snapshot_json'])
    for key in ('camera_type','matrix_world','lens','shift','resolution','engine','device','samples','file_format','color_mode','film_transparent'):
        assert snap[key]==expected[key],(key,snap[key],expected[key])
    assert snap['camera_type']=='PERSP' and snap['resolution']==[3840,2560,100] and snap['film_transparent']
    assert snap['view_transform']==profile['color_management']['view_transform'] and snap['look']==profile['color_management']['look'] and abs(snap['exposure']-profile['color_management']['exposure'])<1e-6 and abs(snap['gamma']-profile['color_management']['gamma'])<1e-6
    assert scene.render.engine=='CYCLES' and scene.cycles.device=='GPU' and scene.cycles.samples==96
    assert audit_scene(scene)['mismatches']==[],audit_scene(scene)['mismatches']
    assert scene.world and scene.world.name
    worlds=[n for n in scene.world.node_tree.nodes if n.type=='TEX_ENVIRONMENT' and n.image]
    backgrounds=[n for n in scene.world.node_tree.nodes if n.type=='BACKGROUND']
    assert len(worlds)==len(backgrounds)==1 and worlds[0].image.packed_file
    assert worlds[0].image.get('pyrocitor_gold_world_path')==profile['world']['path'] and worlds[0].image.get('pyrocitor_gold_world_sha256')==profile['world']['sha256']
    assert abs(float(backgrounds[0].inputs['Strength'].default_value)-2.0)<1e-6
    assert worlds[0].image.size[0]==4096 and worlds[0].image.size[1]==2048
    images=source_images(profile); assert all(i.packed_file for i in images.values())
    assert not [o.name for o in scene.objects if o.type=='LIGHT'], 'Unexpected separate light objects'
    by_hash=selected_meshes(scene,profile)
    unresolved_current=unresolved_source_records(job,profile,by_hash)
    assert len(unresolved_current)==len(unresolved_stored)==len(profile.get('unresolved_source_materials',[]))
    for stored,current in zip(unresolved_stored,unresolved_current):
        for key in ('status','model_role','model_sha256','section','source_material_index','source_material_path',
                    'source_material_sha256','mesh_geometry_sha256','material_name','mesh_data_name'):
            assert stored.get(key)==current.get(key),(key,stored.get(key),current.get(key))
        assert stored['archive_index_lookup']['exact_path_match_count']==current['archive_index_lookup']['exact_path_match_count']==0
        assert stored['archive_index_lookup']['sha256']==current['archive_index_lookup']['sha256']
        assert stored['material_manifest_lookup']['sha256']==current['material_manifest_lookup']['sha256']
        assert stored['material_manifest_lookup']['status']==current['material_manifest_lookup']['status']=='failed'
        assert current['material_manifest_lookup']['reason']==stored['material_manifest_lookup']['reason']
    assignments=json.loads(scene['pyrocitor_gold_assignments_json']); seen={}
    for row in assignments:
        key=next(k for k,v in profile['source_models'].items() if v['sha256']==row['model_sha256'])
        obj=next((o for o in by_hash[key] if int(o.get('source_subset_index',-1))==row['section']),None);assert obj
        used=used_slots(obj);assert used==[0]
        mat=obj.data.materials[0]; assert mat and mat.get('pyrocitor_gold_recipe_role')==row['recipe']
        assert mat.get('source_material_path','').casefold()==row['source_material_path'].casefold() and mat.get('source_material_sha256')==row['source_material_sha256']
        assert mat.use_fake_user
        baseline=bpy.data.materials.get(row['baseline_material'])
        assert baseline and baseline!=mat and baseline.use_fake_user,('baseline material fake-user retention',row['baseline_material'])
        outputs=[n for n in mat.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
        assert len(outputs)==1
        surface=[l for l in mat.node_tree.links if l.to_node==outputs[0] and l.to_socket.name=='Surface'];assert len(surface)==1
        assert_canonical_recipe(row['recipe'],mat,profile['materials'][row['recipe']])
        bound={}
        for n in mat.node_tree.nodes:
            if n.type!='TEX_IMAGE' or not n.image: continue
            for ch,tex in profile['materials'][row['recipe']]['textures'].items():
                if n.image.get('source_texture_path')==tex['path']:
                    assert n.image.get('source_texture_sha256')==tex['sha256'] and n.image.packed_file and n.image.colorspace_settings.name==tex['colorspace']
                    bound[ch]=n.image
        assert set(bound)=={'c','g','n'},(row['recipe'],bound.keys())
        seen[(key,row['section'])]=row
    assert len(seen)==len(profile['sections'])
    # Complete V5 identity: all five native sections are still present and map only by model hash/subset.
    v5={int(o.get('source_subset_index',-1)) for o in by_hash['v5']};assert v5=={0,1,2,3,4}
    shared={int(o.get('source_subset_index',-1)) for o in by_hash['main']};assert shared=={0,1,2,3}
    v1={int(o.get('source_subset_index',-1)) for o in by_hash['v1']};assert v1=={0}
    # Original source animation reopen report is hash-bound and must still pass.
    anim=json.loads(Path(job['animation_report']).read_text(encoding='utf-8'))
    assert file_hash(job['animation_report'])==job['animation_report_sha256']
    assert anim['status']=='reopened_switchable_animation_integration_passed'
    assert [r['name'] for r in anim['clips']]==['Fire_wpn_Pyrociter','Idle_wpn_Pyrociter','Open_wpn_Pyrociter']
    for role,rigrow in anim['per_rig_animation_checks'].items():
        assert len(rigrow['direct_action'])==3 and len(rigrow['nla_overview'])==3
        assert all(r['frames'] in (15,21,13) for r in rigrow['direct_action'])
    # Recheck embedded rig Actions/NLA exist in this exact presentation blend.
    supported={'Fire_wpn_Pyrociter','Idle_wpn_Pyrociter','Open_wpn_Pyrociter'}
    action_names={a.name for a in bpy.data.actions}
    assert all(any(clip in name for name in action_names) for clip in supported), (supported,action_names)
    for role,rigrow in anim['per_rig_animation_checks'].items():
        rig=bpy.data.objects.get(rigrow['rig']);assert rig and rig.type=='ARMATURE' and rig.animation_data
        strips=[strip for track in rig.animation_data.nla_tracks for strip in track.strips]
        strip_names={strip.action.name for strip in strips if strip.action}
        assert len(strips)>=3 and all(any(clip in name for name in strip_names) for clip in supported),(role,strip_names)
    controls=bpy.data.texts.get('Animation Controls.py');catalog=bpy.data.texts.get('Pyrociter Animation Catalog.json')
    assert controls and all(token in controls.as_string() for token in ('select_clip','play_all','restore_rest'))
    assert catalog and all(clip in catalog.as_string() for clip in supported)
    # Manual-review proofs at the mandated cheap quality, then restore saved settings/state in memory.
    saved_pct=scene.render.resolution_percentage;saved_samples=scene.cycles.samples;saved_fp=scene.render.filepath;saved_state='V1';proofs=[]
    review=Path(job['report_json']).parent/'review';review.mkdir(parents=True,exist_ok=True)
    for state in ('V1','V5'):
        apply_visibility(scene,state);bpy.context.view_layer.update()
        active=[]
        for key,expected_sections in (('main',{0,1,2,3}),('v1',{0} if state=='V1' else set()),('v5',{0,1,2,3,4} if state=='V5' else set())):
            objs=by_hash[key]
            visible={int(o.get('source_subset_index',-1)) for o in objs if not o.hide_get() and not o.hide_render}
            assert visible==expected_sections,(state,key,visible,expected_sections)
            active.extend(o for o in objs if not o.hide_get() and not o.hide_render)
        coords=[world_to_camera_view(scene,scene.camera,obj.matrix_world@vert.co) for obj in active for vert in obj.data.vertices]
        assert coords and min(c.z for c in coords)>0
        bounds={axis:[min(getattr(c,axis) for c in coords),max(getattr(c,axis) for c in coords)] for axis in ('x','y')}
        assert bounds['x'][0]>=-0.02 and bounds['x'][1]<=1.02 and bounds['y'][0]>=-0.02 and bounds['y'][1]<=1.02,(state,bounds)
        scene.render.resolution_percentage=25;scene.cycles.samples=32;scene.render.filepath=str(review/f'pyrocitor_gold_{state.lower()}.png')
        bpy.ops.render.render(write_still=True)
        image=bpy.data.images.load(scene.render.filepath,check_existing=False)
        assert image.size[0]==960 and image.size[1]==640
        rgba=array('f',[0.0])*len(image.pixels);image.pixels.foreach_get(rgba)
        alpha=range(3,len(rgba),4)
        nonzero=sum(rgba[i]>0 for i in alpha);partial=sum(0<rgba[i]<1 for i in alpha)
        assert nonzero>0 and partial>0,(state,nonzero,partial)
        proofs.append({'state':state,'path':scene.render.filepath,'sha256':file_hash(scene.render.filepath),'bounds':bounds,'alpha_nonzero_pixels':nonzero,'alpha_partial_pixels':partial})
        bpy.data.images.remove(image)
    scene.render.resolution_percentage=saved_pct;scene.cycles.samples=saved_samples;scene.render.filepath=saved_fp;apply_visibility(scene,saved_state)
    assert not audit_scene(scene)['mismatches']
    report={'status':'passed','blend_sha256':file_hash(job['output_blend']),'profile_id':profile['profile_id'],'profile_sha256':job['profile_sha256'],'canonical_scene_sha256':profile['canonical_scene']['sha256'],'source_blend_sha256':job['source_sha256'],'animation_report_sha256':job['animation_report_sha256'],'assignments':assignments,'unresolved_source_materials':unresolved_current,'source_material_resolution':'incomplete_explicitly_unresolved','pyrocitor_graph_conversion':graph_conversion,'sections':{'main':sorted(shared),'v1':sorted(v1),'v5':sorted(v5)},'world':{'name':scene.world.name,'strength':2.0,'image':worlds[0].image.name,'packed':True,'source_sha256':profile['world']['sha256']},'color_management':{'view_transform':snap['view_transform'],'look':snap['look'],'exposure':snap['exposure'],'gamma':snap['gamma']},'camera':{'name':scene.camera.name,'type':snap['camera_type'],'matrix_world':snap['matrix_world'],'lens':snap['lens'],'shift':snap['shift']},'render_settings':{'resolution':snap['resolution'],'engine':snap['engine'],'device':snap['device'],'samples':snap['samples'],'film_transparent':snap['film_transparent'],'audit':audit_scene(scene)},'packed_source_images':len({i.as_pointer() for i in images.values()}),'baseline_materials_fake_user':sum(1 for m in bpy.data.materials if m.use_fake_user and m.get('source_material_path')),'source_animation_status':anim['status'],'clips':anim['clips'],'variant_states':['V1','V5'],'proof_renders':proofs,'limitations':['Canonical Blender materials/world are presentation recipes and settings, not recovered game shader/lighting equivalence.','Main section 2 glass source material path remains unresolved in the indexed archive; the incoming material and geometry were preserved without assigning a recipe or inventing a source hash.','Actor selection and external runtime attachment transform remain unproven.']}
    dump(job['report_json'],report)
    print(json.dumps({'status':'validated','report_json':job['report_json'],'proofs':proofs},indent=2),flush=True)

parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);parser.add_argument('--mode',choices=('build','validate'),required=True);args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
job=load(args.job)
build(job) if args.mode=='build' else validate(job)




