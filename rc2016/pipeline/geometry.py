"""Native PS4 geometry/material builder plus the evidence-bound FBX adapter."""
import hashlib
import json
from pathlib import Path
import bpy
import numpy as np
from mathutils import Matrix
from rc2016.formats.container import game_crc32
from rc2016.common import asset_path,file_hash
from rc2016.formats.texture_transport import image_interpretation_identity,image_interpretation_key
from rc2016.pipeline.blender_support import targets,structural_snapshot,read_values,write_values
from rc2016.pipeline.bulk_checks import per_loop,signed_int32,weight_batches
from rc2016.formats.native_model import blender_safe_triangles,decode_model,validate_mesh_position_correction


def import_fbx(job,rig):
    descriptor=job.get('geometry')
    if not descriptor:return None
    path=Path(descriptor['path'])
    if file_hash(path)!=descriptor['sha256']:raise ValueError('FBX source changed')
    old=set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path),use_anim=False,automatic_bone_orientation=False)
    imported=set(bpy.data.objects)-old
    skeletons=[o for o in imported if o.type=='ARMATURE']
    meshes=[o for o in imported if o.type=='MESH']
    if len(skeletons)!=1 or not meshes:raise ValueError('FBX adapter needs one unambiguous armature and at least one mesh')
    original=skeletons[0];rows=job['model']['skeleton'];by_hash={int(r['hash'],16):r for r in rows}
    mapping={}
    for bone in original.data.bones:
        h=game_crc32(bone.name)
        if h in by_hash:mapping[bone.name]=by_hash[h]['name']
    if len(mapping)!=len(rows):raise ValueError('FBX skeleton does not contain all selected source joint hashes')
    # Registration estimates scale and basis from this model, rather than assuming an exporter convention.
    rest=targets(rows,[],dict(kind='absolute skeletal',inheritance=job['rest_inheritance']))
    source=np.array([list(rest[mapping[n]].translation) for n in mapping])
    exported=np.array([list(original.matrix_world@original.data.bones[n].head_local) for n in mapping])
    a=source-source.mean(axis=0);b=exported-exported.mean(axis=0)
    if np.linalg.matrix_rank(a,tol=1e-8)<2:raise ValueError('FBX rest heads do not determine a unique coordinate basis; profile-specific adapter required')
    u,s,vt=np.linalg.svd(a.T@b);rotation=vt.T@u.T
    if np.linalg.det(rotation)<0:raise ValueError('Reflected FBX basis requires a validated geometry profile')
    scale=float(s.sum()/np.sum(a*a));translation=exported.mean(axis=0)-scale*rotation@source.mean(axis=0)
    predicted=(scale*(rotation@source.T)).T+translation
    error=float(np.max(np.abs(predicted-exported)))
    extent=max(float(np.ptp(exported,axis=0).max()),1e-8)
    if error>extent*1e-4:raise ValueError('FBX rest registration does not match this model: '+str(error))
    convert=np.eye(4);convert[:3,:3]=scale*rotation;convert[:3,3]=translation
    inverse=Matrix(np.linalg.inv(convert).tolist())
    imported_baseline=structural_snapshot(bpy.context.scene)
    unweighted=0
    for obj in meshes:
        transform=inverse@obj.matrix_world
        normals=np.array([list(n.vector) for n in obj.data.corner_normals])
        normal_transform=np.asarray(transform.to_3x3().inverted().transposed())
        expected_normals=(normal_transform@normals.T).T
        lengths=np.linalg.norm(expected_normals,axis=1)
        expected_normals/=np.maximum(lengths[:,None],1e-30)
        for vertex in obj.data.vertices:
            weights=[g.weight for g in vertex.groups if obj.vertex_groups[g.group].name in mapping]
            unknown=[obj.vertex_groups[g.group].name for g in vertex.groups if g.weight>0 and obj.vertex_groups[g.group].name not in mapping]
            if unknown:raise ValueError('FBX vertex weights reference unresolved joints: '+repr(unknown))
            if not weights:unweighted+=1
            elif abs(sum(weights)-1)>.005:raise ValueError('FBX source weights are not normalized; not silently repaired')
        obj.parent=None;obj.matrix_world=Matrix.Identity(4);obj.data.transform(transform);obj.data.update()
        actual_normals=np.array([list(n.vector) for n in obj.data.corner_normals])
        if normals.size and np.max(np.abs(actual_normals-expected_normals))>1e-4:
            raise ValueError('Source FBX normals were not preserved under coordinate registration')
        for group in obj.vertex_groups:
            if group.name in mapping:group.name=mapping[group.name]
        modifiers=[m for m in obj.modifiers if m.type=='ARMATURE']
        if not modifiers:raise ValueError('FBX mesh lacks a source skin modifier')
        for modifier in modifiers:modifier.object=rig
        obj['source_fbx_sha256']=descriptor['sha256']
    original.hide_set(True);original.hide_render=True
    for obj in imported:
        if obj.type in ('LIGHT','CAMERA'):bpy.data.objects.remove(obj,do_unlink=True)
    for image in bpy.data.images:
        if image.source=='FILE' and image.filepath.lower().endswith('.dds'):
            image.alpha_mode='CHANNEL_PACKED'
            stem=Path(image.filepath).stem.lower()
            image.colorspace_settings.name='Non-Color' if stem.endswith(('_g','_n','_m')) else 'sRGB'
        if image.source=='FILE' and not image.packed_file:
            try:image.pack()
            except RuntimeError:pass
    # Coordinates/normals are transformed by the inferred similarity only; topology, UVs and weights stay unchanged.
    after=structural_snapshot(bpy.context.scene)
    for obj in meshes:
        for field in ('vertices','polygons','topology','uvs','weights','materials','polygon_materials'):
            if imported_baseline['meshes'][obj.name][field]!=after['meshes'][obj.name][field]:
                raise ValueError('FBX preservation check failed: '+obj.name+' '+field)
    return dict(status='imported_fbx',sha256=descriptor['sha256'],mesh_count=len(meshes),
        inferred_source_to_fbx_scale=scale,source_to_fbx_basis=rotation.tolist(),rest_head_error=error,
        unweighted_vertices=unweighted,source_topology_uv_weights_material_assignments_preserved=True,
        materials='FBX materials preserved; exact game shader reconstruction is not implemented')


# Source images by their custom-property identity, so each binding looks up candidates
# instead of scanning bpy.data.images. Only _image sets these properties; the index is
# rebuilt whenever the image count changes behind its back. The colourspace, alpha mode
# and conversion parts of the key are still read live from each candidate.
_IMAGE_INDEX=dict(count=-1,source={},payload={})


def _index_image(image):
    try:source=(asset_path(image.get('source_texture_path','')),str(image.get('source_texture_sha256','')).lower())
    except (ValueError,AttributeError):source=None
    if source is not None:_IMAGE_INDEX['source'].setdefault(source,[]).append(image)
    payload=(image.get('source_texture_payload_sha256'),image.get('source_texture_payload_conversion'))
    _IMAGE_INDEX['payload'].setdefault(payload,[]).append(image)


def _image_index():
    if _IMAGE_INDEX['count']!=len(bpy.data.images):
        _IMAGE_INDEX.update(count=len(bpy.data.images),source={},payload={})
        for image in bpy.data.images:_index_image(image)
    return _IMAGE_INDEX


def _image(binding):
    identity=image_interpretation_identity(binding)
    key=image_interpretation_key(binding)
    try:
        index=_image_index();existing=[]
        for image in index['source'].get(key[:2],()):
            try:
                candidate=(asset_path(image.get('source_texture_path','')),
                    str(image.get('source_texture_sha256','')).lower(),
                    image.colorspace_settings.name,image.alpha_mode,
                    image.get('blender_conversion') or 'DDS_NATIVE_V1')
            except (ValueError,AttributeError):
                continue
            if candidate==key:existing.append(image)
        prototypes=[image for image in index['payload'].get((identity['dds_sha256'],identity['conversion']),())
            if image.get('source_texture_payload_sha256')==identity['dds_sha256'] and
            image.get('source_texture_payload_conversion')==identity['conversion']]
    except ReferenceError:   # an indexed image was removed and replaced: rebuild once
        _IMAGE_INDEX['count']=-1
        return _image(binding)
    if len(existing)>1:raise ValueError('Duplicate Blender images share one texture interpretation identity: '+repr(identity))
    if existing:return existing[0]
    source_path=Path(binding['dds_path']).resolve()
    payload_prototype_name=None
    if prototypes:
        prototype=min(prototypes,key=lambda image:image.name)
        payload_prototype_name=prototype.name
        image=prototype.copy()
        image.filepath=str(source_path.with_suffix('.blender.png') if binding.get('format') in (49,61) else source_path)
    elif binding.get('format')==49:
        width=binding['width'];height=binding['height'];raw=source_path.read_bytes()
        expected=width*height*2
        if len(raw)<148+expected:raise ValueError('Truncated R8G8_UNORM DDS: '+str(source_path))
        rg=np.frombuffer(raw[148:148+expected],dtype=np.uint8).reshape(height,width,2)
        rgba=np.empty((height,width,4),dtype=np.float32);rgba[:,:,:2]=rg/255.0;rgba[:,:,3]=1.0
        if binding.get('role')=='normal':
            xy=rgba[:,:,:2]*2.0-1.0
            rgba[:,:,2]=(np.sqrt(np.maximum(0.0,1.0-np.sum(xy*xy,axis=2)))+1.0)*0.5
        else:rgba[:,:,2]=0.0
        # DDS rows are top-to-bottom; Blender image pixels are bottom-to-top.
        rgba=np.flipud(rgba)
        image=bpy.data.images.new(source_path.name,width=width,height=height,alpha=True,float_buffer=False)
        image.pixels.foreach_set(rgba.ravel())
        packed_path=source_path.with_suffix('.blender.png')
        image.filepath_raw=str(packed_path);image.file_format='PNG';image.save()
        image['packed_container']='PNG with exact R/G and shader-reconstructed normal Z from R8G8_UNORM DDS'
    elif binding.get('format')==61:
        width=binding['width'];height=binding['height'];raw=source_path.read_bytes()
        expected=width*height
        if len(raw)<148+expected:raise ValueError('Truncated R8_UNORM DDS: '+str(source_path))
        red=np.frombuffer(raw[148:148+expected],dtype=np.uint8).reshape(height,width).astype(np.float32)/255.0
        rgba=np.empty((height,width,4),dtype=np.float32)
        rgba[:,:,:3]=red[:,:,None];rgba[:,:,3]=1.0
        rgba=np.flipud(rgba)
        image=bpy.data.images.new(source_path.name,width=width,height=height,alpha=True,float_buffer=False)
        image.pixels.foreach_set(rgba.ravel())
        packed_path=source_path.with_suffix('.blender.png')
        image.filepath_raw=str(packed_path);image.file_format='PNG';image.save()
        image['packed_container']='PNG with exact R8_UNORM scalar replicated to RGB'
    else:image=bpy.data.images.load(str(source_path),check_existing=False)
    image.alpha_mode=identity['alpha_mode']
    image.colorspace_settings.name=identity['colorspace']
    image['source_texture_path']=identity['source_path'];image['source_texture_sha256']=identity['dds_sha256']
    image['source_texture_interpretation']=json.dumps(identity,sort_keys=True)
    image['source_texture_payload_sha256']=identity['dds_sha256']
    image['source_texture_payload_conversion']=identity['conversion']
    if payload_prototype_name:image['source_texture_payload_copied_from']=payload_prototype_name
    if binding.get('format')!=49:image['blender_conversion']=identity['conversion']
    image.pack()
    if binding.get('format')==49:
        image['blender_conversion']='R8G8_UNORM_DDS_TO_RGBA8_PNG_V1'
        image['blender_packed_sha256']=hashlib.sha256(bytes(image.packed_file.data)).hexdigest()
        image['blender_dimensions']=list(image.size)
    elif binding.get('format')==61:
        image['blender_conversion']='R8_UNORM_DDS_TO_RGBA8_PNG_V1'
        image['blender_packed_sha256']=hashlib.sha256(bytes(image.packed_file.data)).hexdigest()
        image['blender_dimensions']=list(image.size)
    if _IMAGE_INDEX['count']==len(bpy.data.images)-1:
        _IMAGE_INDEX['count']+=1;_index_image(image)
    return image


def texture_connection_coverage(material, bindings):
    """Report exact image-binding reachability into an active Material Output."""
    tree=material.node_tree if material and material.use_nodes else None
    outputs=[n for n in tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output] if tree else []
    reachable=set();active_inputs=[]
    for output in outputs:
        for socket in output.inputs:
            if socket.is_linked:
                active_inputs.append(socket.name)
                reachable.update(link.from_node for link in socket.links)
    pending=list(reachable)
    while pending:
        node=pending.pop()
        for socket in node.inputs:
            for link in socket.links:
                upstream=link.from_node
                if upstream not in reachable:
                    reachable.add(upstream);pending.append(upstream)
    # The generated frontend has all five outputs linked to the shared BRDF.
    # Its source images live inside the group; include their actual upstream
    # links rather than reporting only the retained outer image copies.
    image_nodes=[n for n in tree.nodes if n.type=='TEX_IMAGE'] if tree else []
    for front in list(reachable):
        if front.type!='GROUP' or front.name!='Native translated material':continue
        group=front.node_tree
        image_nodes.extend(n for n in group.nodes if n.type=='TEX_IMAGE')
        pending=[n for n in group.nodes if n.type=='GROUP_OUTPUT' and n.is_active_output]
        seen=set()
        while pending:
            node=pending.pop()
            if node in seen:continue
            seen.add(node);reachable.add(node)
            for socket in node.inputs:pending.extend(l.from_node for l in socket.links)
    rows=[]
    for binding in bindings:
        path=binding.get('path');digest=binding.get('dds_sha256')
        if not path or not digest or not tree:
            status='unresolved';reason='Source binding identity or node tree is unavailable';matches=[]
        else:
            path_matches=[n for n in image_nodes if
                n.get('source_texture_path')==path]
            matches=[n for n in path_matches if n.get('source_texture_sha256')==digest]
            image_identity_ok=all(n.image and n.image.get('source_texture_sha256')==digest for n in matches)
            if len(outputs)!=1:
                status='unresolved';reason='Expected exactly one active Material Output; found %d'%len(outputs)
            elif not matches:
                status='unresolved';reason=('Image node path exists but source hash differs' if path_matches else
                    'No image node retained this exact source binding identity')
            elif not image_identity_ok:
                status='unresolved';reason='Image node and packed image source identities disagree'
            elif any(n in reachable for n in matches):
                status='connected';reason='Image node is upstream of an active Material Output input'
            else:
                status='disconnected';reason='Exact image node is not upstream of the active Material Output'
        image_identity=image_interpretation_identity(binding) if path and digest else None
        identity_matches=[]
        for node in matches:
            if not node.image:continue
            try:
                candidate=dict(source_path=asset_path(node.image.get('source_texture_path') or ''),
                    dds_sha256=str(node.image.get('source_texture_sha256') or '').lower(),
                    colorspace=node.image.colorspace_settings.name,alpha_mode=node.image.alpha_mode,
                    conversion=node.image.get('blender_conversion') or 'DDS_NATIVE_V1')
                if candidate==image_identity:identity_matches.append(node)
            except ValueError:pass
        if image_identity and matches and not identity_matches:
            status='unresolved';reason='Image nodes match source path/hash but not the requested interpretation identity'
        elif identity_matches and len(outputs)==1:
            matches=identity_matches
            image_identity_ok=all(n.image and n.image.get('source_texture_sha256')==digest for n in matches)
            if not image_identity_ok:
                status='unresolved';reason='Image node and packed image source identities disagree'
            elif any(n in reachable for n in matches):
                status='connected';reason='Exact image interpretation is upstream of an active Material Output input'
            else:
                status='disconnected';reason='Exact image interpretation is not upstream of the active Material Output'
        rows.append(dict(path=path,role=binding.get('role'),dds_sha256=digest,
            image_identity=image_identity,
            source_references=binding.get('source_references',[]),status=status,reason=reason,
            matching_image_node_count=len(matches),reachable_image_node_count=sum(n in reachable for n in matches),
            image_source_paths=sorted({n.image.get('source_texture_path','') for n in matches if n.image}),
            active_output_names=[n.name for n in outputs],active_output_inputs=active_inputs))
    counts={key:sum(row['status']==key for row in rows) for key in ('connected','disconnected','unresolved')}
    return dict(schema='source-texture-node-reachability-v1',status='reported',summary=dict(
        binding_count=len(rows),connected=counts['connected'],disconnected=counts['disconnected'],
        unresolved=counts['unresolved']),bindings=rows,
        scope='Structural upstream-link reachability only; it does not establish shader meaning or runtime equivalence.')


def _material(source):
    material=bpy.data.materials.new(source['name']);material.use_nodes=True;material.use_fake_user=True
    material['source_material_path']=source['path'];material['source_material_sha256']=source.get('source_sha256','')
    material['source_texture_bindings']=json.dumps([dict(path=x.get('path'),role=x.get('role'),
        dds_sha256=x.get('dds_sha256'),image_identity=image_interpretation_identity(x),
        source_references=x.get('source_references',[]))
        for x in source.get('textures',[])],sort_keys=True)
    material['shader_status']=source.get('shader_status','failed')
    material['graph_semantics']=source.get('graph_semantics','ordinary_surface')
    profile=source.get('validated_profile')
    recipe=profile.get('recipe',{}) if profile else {}
    presentation=recipe.get('presentation_overrides') if profile else None
    if presentation:
        expected=profile.get('source_texture_bindings',[])
        actual={(x.get('role'),x.get('path'),x.get('dds_sha256'),x.get('colorspace'))
                for x in source.get('textures',[])}
        pinned={(x.get('role'),x.get('path'),x.get('dds_sha256'),x.get('colorspace'))
                for x in expected}
        if actual.intersection(pinned)!=pinned:
            raise ValueError('Presentation profile source texture references/hashes do not match material bindings')
    if profile:
        material['validated_profile_material_sha256']=profile['material_sha256']
        material['validated_profile_sha256']=profile['profile_sha256']
        material['validated_recipe']=json.dumps(recipe,sort_keys=True)
        material['automation_evidence']=profile['evidence']
    nodes=material.node_tree.nodes;links=material.node_tree.links;nodes.clear()
    output=nodes.new('ShaderNodeOutputMaterial');output.name='Material Output'
    principled=nodes.new('ShaderNodeBsdfPrincipled');principled.name='Source-channel Principled'
    principled.inputs['Metallic'].default_value=0.0
    principled.inputs['Roughness'].default_value=.5
    principled.inputs['Specular IOR Level'].default_value=.5
    if source.get('status')=='unset':
        # Conspicuous, labelled placeholder for a source slot with no material.
        material['source_material_unset']=True
        principled.label='UNSET SOURCE MATERIAL SLOT (placeholder)'
        principled.inputs['Base Color'].default_value=(1.0,0.0,1.0,1.0)
    role_nodes={};path_nodes={}
    for index,entry in enumerate(source.get('textures',[])):
        node=nodes.new('ShaderNodeTexImage');node.name=f"Source texture {index:03d} | {entry['role']}";node.label=entry['path'];node.image=_image(entry)
        node['source_material_path']=source['path'];node['source_material_sha256']=source.get('source_sha256','')
        node['source_texture_path']=entry['path'];node['source_texture_sha256']=entry.get('dds_sha256','')
        node['source_texture_interpretation']=json.dumps(image_interpretation_identity(entry),sort_keys=True)
        node['source_texture_references']=json.dumps(entry.get('source_references',[]),sort_keys=True)
        refs=entry.get('source_references',[])
        node['source_graph_fallback']=bool(refs) and all(ref.get('graph_fallback') for ref in refs)
        old=role_nodes.get(entry['role'])
        if old is None or (old.get('source_graph_fallback') and not node['source_graph_fallback']):
            role_nodes[entry['role']]=node
        path_nodes[entry['path'].lower()]=node
    base=role_nodes.get('base_color');gloss=role_nodes.get('colored_specular_gloss');normal=role_nodes.get('normal')

    if not profile and 'shader_preflight' in source:
        # A preview is built only after exact graph-family preflight. Keep the
        # native texture interpretation; unknown graphs use a labelled colour
        # preview rather than guessing specular/gloss/emission channel meanings.
        if base:links.new(base.outputs['Color'],principled.inputs['Base Color'])
        if gloss:links.new(gloss.outputs['Color'],principled.inputs['Specular Tint'])
        if normal:
            nm=nodes.new('ShaderNodeNormalMap');nm.inputs['Strength'].default_value=1.0
            links.new(normal.outputs['Color'],nm.inputs['Color']);links.new(nm.outputs['Normal'],principled.inputs['Normal'])
        links.new(principled.outputs['BSDF'],output.inputs['Surface'])
        from rc2016.pipeline import game_shader
        from rc2016.shaders.nodes import apply_translated_material
        # materials.py decides: a fully ported viewer rule outranks the native translation.
        viewer_first=source.get('shader_status')=='viewer_graph_rule_frontend'
        applied=viewer_first and game_shader.apply_viewer_graph_rule(material,source)
        if not applied:applied=apply_translated_material(material,source)
        if not applied:applied=game_shader.apply_preflight_material(material,source)
        # No exact registered family: use the asset viewer's per-graph reading on the same BRDF.
        if not applied and not viewer_first:applied=game_shader.apply_viewer_graph_rule(material,source)
        if not applied and source.get('graph_semantics')!='thin_glass':
            for link in list(principled.inputs['Specular Tint'].links):links.remove(link)
            for link in list(principled.inputs['Normal'].links):links.remove(link)
            principled.inputs['Specular IOR Level'].default_value=0.0
            real_base=base is not None and not base.get('source_graph_fallback')
            if not real_base:
                for link in list(principled.inputs['Base Color'].links):links.remove(link)
            material['shader_preview_status']=('source_colour_only; unsupported graph equations' if real_base
                                               else 'no_base_color; unsupported graph equations')
            material['game_equivalence']=False
        if applied or source.get('graph_semantics')!='thin_glass':
            material['source_texture_connection_coverage']=json.dumps(
                texture_connection_coverage(material,source.get('textures',[])),sort_keys=True)
            return material

    kind=recipe.get('kind')
    if kind=='thin_glass' or (not profile and source.get('graph_semantics')=='thin_glass'):
        glass=recipe or dict(tint=[.04,.28,.36,1],reflection_mix=.2,roughness=.12,backfaces_transparent=True)
        tint=glass['tint']
        transparent=nodes.new('ShaderNodeBsdfTransparent');transparent.name='Thin glass transparency';transparent.inputs['Color'].default_value=tint
        glossy=nodes.new('ShaderNodeBsdfGlossy');glossy.name='Thin glass reflection';glossy.inputs['Color'].default_value=tint;glossy.inputs['Roughness'].default_value=glass['roughness']
        front=nodes.new('ShaderNodeMixShader');front.name='Thin glass front blend';front.inputs[0].default_value=glass['reflection_mix']
        links.new(transparent.outputs[0],front.inputs[1]);links.new(glossy.outputs[0],front.inputs[2]);final=front.outputs[0]
        if glass.get('backfaces_transparent'):
            geometry=nodes.new('ShaderNodeNewGeometry');geometry.name='Transparent back faces'
            back=nodes.new('ShaderNodeMixShader');back.name='Front surface / transparent backface'
            links.new(geometry.outputs['Backfacing'],back.inputs[0]);links.new(final,back.inputs[1]);links.new(transparent.outputs[0],back.inputs[2]);final=back.outputs[0]
        material['thin_glass_reflection_mix']=glass['reflection_mix'];material['thin_glass_roughness']=glass['roughness']
        if 'source_glossiness' in glass:
            material['source_glossiness']=glass['source_glossiness']
            material['thin_glass_roughness_mapping']='source Glossiness inverted into Blender roughness'
        if not profile:material['automation_evidence']='Recognized authoritative materialgraph family; conservative thin-glass approximation'
        links.new(final,output.inputs['Surface'])
        material['source_texture_connection_coverage']=json.dumps(
            texture_connection_coverage(material,source.get('textures',[])),sort_keys=True)
        return material

    if kind=='transparent_energy_layer':
        tint=recipe['tint'];principled.inputs['Base Color'].default_value=tint;principled.inputs['Emission Color'].default_value=tint
        principled.inputs['Emission Strength'].default_value=1.0;principled.inputs['Roughness'].default_value=.25
        transparent=nodes.new('ShaderNodeBsdfTransparent');transparent.name='Energy transparency'
        noise=[]
        for token in recipe['noise_texture_tokens']:
            matches=[node for path,node in path_nodes.items() if token.lower() in path]
            if len(matches)!=1:raise ValueError('Validated energy texture token must resolve exactly once: '+token)
            grayscale=nodes.new('ShaderNodeRGBToBW');grayscale.name='Energy mask '+token
            links.new(matches[0].outputs['Color'],grayscale.inputs['Color']);noise.append(grayscale.outputs['Val'])
        combined=nodes.new('ShaderNodeMath');combined.name='Multiply validated energy noise masks';combined.operation='MULTIPLY'
        links.new(noise[0],combined.inputs[0]);links.new(noise[1],combined.inputs[1])
        scale=nodes.new('ShaderNodeMath');scale.name='Energy opacity scale';scale.operation='MULTIPLY';scale.inputs[1].default_value=recipe['noise_opacity_scale']
        links.new(combined.outputs[0],scale.inputs[0])
        offset=nodes.new('ShaderNodeMath');offset.name='Energy minimum opacity';offset.operation='ADD';offset.inputs[1].default_value=recipe['minimum_opacity']
        links.new(scale.outputs[0],offset.inputs[0])
        front=nodes.new('ShaderNodeMixShader');front.name='Transparent / source energy';links.new(offset.outputs[0],front.inputs[0]);links.new(transparent.outputs[0],front.inputs[1]);links.new(principled.outputs[0],front.inputs[2]);final=front.outputs[0]
        if recipe.get('backfaces_transparent'):
            geometry=nodes.new('ShaderNodeNewGeometry');geometry.name='Transparent back faces'
            back=nodes.new('ShaderNodeMixShader');back.name='Energy front / transparent backface'
            links.new(geometry.outputs['Backfacing'],back.inputs[0]);links.new(final,back.inputs[1]);links.new(transparent.outputs[0],back.inputs[2]);final=back.outputs[0]
        material['energy_minimum_opacity']=recipe['minimum_opacity'];material['energy_noise_opacity_scale']=recipe['noise_opacity_scale']
        links.new(final,output.inputs['Surface'])
        material['source_texture_connection_coverage']=json.dumps(
            texture_connection_coverage(material,source.get('textures',[])),sort_keys=True)
        return material

    final=principled.outputs['BSDF']
    if base:
        color_for_principled=base.outputs['Color']
        hue_settings=recipe.get('principled_base_hue_saturation')
        if hue_settings:
            hue=nodes.new('ShaderNodeHueSaturation');hue.name='Validated Principled-only color tuning'
            hue.inputs['Hue'].default_value=hue_settings['hue'];hue.inputs['Saturation'].default_value=hue_settings['saturation']
            hue.inputs['Value'].default_value=hue_settings['value'];hue.inputs['Fac'].default_value=hue_settings['factor']
            links.new(base.outputs['Color'],hue.inputs['Color']);color_for_principled=hue.outputs['Color']
        links.new(color_for_principled,principled.inputs['Base Color'])
        links.new(base.outputs['Color'],principled.inputs['Emission Color'])
        threshold=nodes.new('ShaderNodeMath');threshold.name='_c alpha emission threshold';threshold.operation='GREATER_THAN';threshold.inputs[1].default_value=.5
        links.new(base.outputs['Alpha'],threshold.inputs[0]);links.new(threshold.outputs[0],principled.inputs['Emission Strength'])
    if gloss:
        one_minus=nodes.new('ShaderNodeMath');one_minus.name='Roughness = 1 - _g alpha';one_minus.operation='SUBTRACT';one_minus.inputs[0].default_value=1.0
        links.new(gloss.outputs['Alpha'],one_minus.inputs[1]);roughness=one_minus.outputs[0]
        if 'roughness_minimum' in recipe:
            floor=nodes.new('ShaderNodeMath');floor.name='Validated roughness floor';floor.operation='MAXIMUM';floor.inputs[1].default_value=recipe['roughness_minimum']
            links.new(roughness,floor.inputs[0]);roughness=floor.outputs[0]
        links.new(roughness,principled.inputs['Roughness'])
        if base:
            white_minus=nodes.new('ShaderNodeVectorMath');white_minus.name='White minus _g';white_minus.operation='SUBTRACT';white_minus.inputs[0].default_value=(1,1,1)
            diffuse_color=nodes.new('ShaderNodeVectorMath');diffuse_color.name='_c times (1 - _g)';diffuse_color.operation='MULTIPLY'
            links.new(gloss.outputs['Color'],white_minus.inputs[1]);links.new(base.outputs['Color'],diffuse_color.inputs[0]);links.new(white_minus.outputs[0],diffuse_color.inputs[1])
            diffuse=nodes.new('ShaderNodeBsdfDiffuse');diffuse.name='Recovered diffuse';links.new(diffuse_color.outputs['Vector'],diffuse.inputs['Color'])
            glossy=nodes.new('ShaderNodeBsdfGlossy');glossy.name='Recovered colored glossy';links.new(gloss.outputs['Color'],glossy.inputs['Color']);links.new(roughness,glossy.inputs['Roughness'])
            add=nodes.new('ShaderNodeAddShader');add.name='Recovered Diffuse plus Glossy';links.new(diffuse.outputs[0],add.inputs[0]);links.new(glossy.outputs[0],add.inputs[1])
            factor=recipe.get('reconstruction_mix',1.0)
            mix=nodes.new('ShaderNodeMixShader');mix.name='Principled / recovered reconstruction';mix.inputs[0].default_value=factor
            links.new(principled.outputs['BSDF'],mix.inputs[1]);links.new(add.outputs[0],mix.inputs[2]);final=mix.outputs[0]
            material['reconstruction_mix_factor']=factor
            material['reconstruction_factor_status']='Validated hash-bound profile' if profile else 'Raw reconstruction branch; model-specific display/tuning factor unresolved'
    if 'principled_specular_ior_level' in recipe:
        principled.inputs['Specular IOR Level'].default_value=recipe['principled_specular_ior_level']
    if normal:
        normal_map=nodes.new('ShaderNodeNormalMap');normal_map.name='DirectX tangent normal (source green retained)';normal_map.inputs['Strength'].default_value=1.0
        links.new(normal.outputs['Color'],normal_map.inputs['Color']);links.new(normal_map.outputs['Normal'],principled.inputs['Normal'])
        for node_name in ('Recovered diffuse','Recovered colored glossy'):
            recovered=nodes.get(node_name)
            if recovered:links.new(normal_map.outputs['Normal'],recovered.inputs['Normal'])
    if not base:
        # Make unresolved procedural materials conspicuous and usable without silently inventing a grey texture.
        principled.inputs['Base Color'].default_value=(.05,.2,.35,1)
        material['procedural_material_limitation']='No source base-color texture; compiled graph parameters remain unresolved'
    links.new(final,output.inputs['Surface'])
    if presentation:
        # Keep a genuine unprofiled generic material as a fake-user baseline.
        # The reconstructed C/G branch is also left intact in the profiled node tree.
        baseline=material.copy();baseline.name=source['name']+' | generic unprofiled baseline'
        baseline.use_fake_user=True
        baseline['profile_baseline_for_material']=source['name']
        baseline['profile_baseline_material_sha256']=source.get('source_sha256','')
        baseline['profile_baseline_note']='Unprofiled generic graph, reconstruction mix restored to 1.0; presentation candidate is separate.'
        for key in ('validated_profile_material_sha256','validated_profile_sha256','validated_recipe'):
            if key in baseline:del baseline[key]
        baseline['automation_evidence']='Unprofiled generic presentation baseline; no game shader equivalence claim.'
        for node in baseline.node_tree.nodes:
            if node.name=='Principled / recovered reconstruction':
                node.inputs[0].default_value=1.0
                baseline['reconstruction_mix_factor']=1.0
                baseline['reconstruction_factor_status']='Generic unprofiled baseline'
        # The selected candidate neutralizes only its own Principled fallback.
        # C and N remain connected; _g and the generic reconstruction branch remain intact.
        p=material.node_tree.nodes.get('Source-channel Principled')
        if presentation['disable_c_alpha_emission']:
            socket=p.inputs['Emission Strength']
            for link in list(socket.links):material.node_tree.links.remove(link)
            socket.default_value=float(presentation['principled_emission_strength'])
        p.inputs['Roughness'].default_value=float(presentation['principled_roughness'])
        for link in list(p.inputs['Roughness'].links):material.node_tree.links.remove(link)
        p.inputs['Metallic'].default_value=float(presentation['principled_metallic'])
        material['evidence_class']=profile.get('evidence_class','presentation_tuned')
        material['game_equivalence']=bool(profile.get('game_equivalence',False))
        material['presentation_tuning_only']=True
        material['profile_baseline_material']=baseline.name
        material['profile_controls_applied']=json.dumps(presentation,sort_keys=True)
    material['source_texture_connection_coverage']=json.dumps(
        texture_connection_coverage(material,source.get('textures',[])),sort_keys=True)
    return material


def _import_native_descriptor(job,rig,descriptor):
    path=Path(descriptor['path'])
    if file_hash(path)!=descriptor['sha256']:raise ValueError('Native model source changed')
    correction=descriptor.get('mesh_position_correction')
    source=decode_model(path,descriptor['lod'],descriptor.get('look',0),expected_sha256=descriptor['sha256'],
                        preserve_source_positions=bool(correction))
    if source['source_sha256']!=descriptor['sha256']:raise ValueError('Native model decoder hash mismatch')
    correction_validation=validate_mesh_position_correction(correction,source['source_sha256'],source['meshes'])
    factor=float(correction['scale']) if correction_validation else 1.0
    rows=job['model']['skeleton'];materials=[_material(x) for x in descriptor['materials']['materials']]
    joint_names=descriptor.get('joint_names') or [r['name'] for r in rows]
    collection=bpy.data.collections.new(descriptor['name']);bpy.context.scene.collection.children.link(collection)
    objects=[];weight_errors=[];dropped_triangles=[]
    for section in source['meshes']:
        mat_name=section['material']['name'] if section['material'] else f"material_{section['material_index']}"
        name=f"{descriptor['name']} | subset {section['index']:03d} | {mat_name}"
        local_positions=[tuple(float(c)*factor for c in p) for p in section['positions']]
        # Degenerate source triangles crash the custom-normal setter below; see blender_safe_triangles.
        triangles,dropped=blender_safe_triangles(section['positions'],section['triangles'])
        mesh=bpy.data.meshes.new(name);mesh.from_pydata(local_positions,[],triangles)
        validate_changed=bool(mesh.validate()) if dropped else False
        mesh.update()
        if validate_changed:raise ValueError(f'Blender validate() changed native subset {section["index"]} after degenerate triangles were dropped')
        for row in dropped:dropped_triangles.append(dict(subset=section['index'],**row))
        if correction_validation:
            raw=section.get('source_positions_i16')
            if raw is None or len(raw)!=len(local_positions):
                raise ValueError('Source raw position attributes were not preserved for the profiled mesh correction')
            raw_columns=np.asarray(raw,dtype=np.int64).reshape(-1,3).astype(np.int32)
            for axis,label in enumerate(('X','Y','Z')):
                attr=mesh.attributes.new(name='SourcePositionI16_'+label,type='INT',domain='POINT')
                write_values(attr.data,'value',np.ascontiguousarray(raw_columns[:,axis]))
        write_values(mesh.polygons,'use_smooth',np.ones(len(mesh.polygons),dtype=bool))
        # Per-loop values are gathered from the per-vertex source rows in one call per layer.
        loop_vertices=read_values(mesh.loops,'vertex_index',1,np.int32)
        uv=mesh.uv_layers.new(name='UV0')
        write_values(uv.data,'uv',per_loop(section['uv0'],loop_vertices),2)
        if section['uv1']:
            layer=mesh.uv_layers.new(name='UV1')
            write_values(layer.data,'uv',per_loop(section['uv1'],loop_vertices),2)
        if section['colors']:
            colors=mesh.color_attributes.new(name='Color',type='FLOAT_COLOR',domain='CORNER')
            write_values(colors.data,'color',per_loop(section['colors'],loop_vertices),4)
        # The +12 vertex word is kept exactly (SourceTangentPacked) and also as
        # its shader-observed 10:10:10:2 UNORM tangent decode (SourceTangent,
        # native mesh space). Blender's Normal Map node still uses its own
        # UV-derived tangents; the bitangent sign is not stored in this word.
        tangent=mesh.attributes.new(name='SourceTangentPacked',type='INT',domain='POINT')
        write_values(tangent.data,'value',signed_int32(section['packed_tangents']))
        decoded_tangent=mesh.attributes.new(name='SourceTangent',type='FLOAT_VECTOR',domain='POINT')
        write_values(decoded_tangent.data,'vector',np.asarray(section['tangents'],dtype=np.float32).reshape(-1),3)
        if section['uv0_secondary_packed'] is not None:
            mesh.attributes.new(name='SourceUV0SecondaryU',type='INT',domain='POINT')
            mesh.attributes.new(name='SourceUV0SecondaryV',type='INT',domain='POINT')
            secondary_u=mesh.attributes.get('SourceUV0SecondaryU')
            secondary_v=mesh.attributes.get('SourceUV0SecondaryV')
            secondary=np.asarray(section['uv0_secondary_packed'],dtype=np.int64).reshape(-1,2).astype(np.int32)
            write_values(secondary_u.data,'value',np.ascontiguousarray(secondary[:,0]))
            write_values(secondary_v.data,'value',np.ascontiguousarray(secondary[:,1]))
        try:mesh.normals_split_custom_set_from_vertices(section['normals'])
        except AttributeError:mesh.normals_split_custom_set([section['normals'][loop.vertex_index] for loop in mesh.loops])
        if section['material_index']>=len(materials):raise ValueError('Mesh references missing material index')
        mesh.materials.append(materials[section['material_index']])
        obj=bpy.data.objects.new(name,mesh);collection.objects.link(obj);objects.append(obj)
        obj['source_model_sha256']=source['source_sha256'];obj['source_subset_index']=section['index'];obj['source_material_index']=section['material_index']
        if correction_validation:
            obj['native_mesh_position_scale_factor']=factor
            obj['native_mesh_position_scale_profile']=correction['schema']
            obj['native_mesh_position_bound_signature_sha256']=correction_validation['bound_signature_sha256']
        if dropped:
            obj['source_degenerate_triangles_dropped']=json.dumps(dropped,sort_keys=True)
            obj['source_triangle_count']=len(section['triangles'])
        obj['source_mesh_flags']=section['flags'];obj['source_look_lod_membership']=json.dumps(section['memberships'])
        if any(w is not None for w in section['weights']):
            if not rig:raise ValueError('Skinned mesh has no model armature')
            groups={name:obj.vertex_groups.new(name=name) for name in dict.fromkeys(x for x in joint_names if x)}
            # One REPLACE add per (joint, weight) instead of per vertex and influence.
            batches,errors=weight_batches(section['weights'],joint_names)
            weight_errors.extend((section['index'],*row) for row in errors)
            for name,value,indices in batches:groups[name].add(indices,value,'REPLACE')
            modifier=obj.modifiers.new('Exact source skin','ARMATURE');modifier.object=rig
        visible=descriptor.get('visible',True)
        obj.hide_set(not visible);obj.hide_render=not visible
        obj['source_part_role']=descriptor.get('role','primary')
    if weight_errors:raise ValueError('Native skin validation errors: '+repr(weight_errors[:20]))
    return dict(status='native_ps4_geometry_decoded',sha256=source['source_sha256'],mesh_count=len(objects),
        vertices=sum(len(x.data.vertices) for x in objects),triangles=sum(len(x.data.polygons) for x in objects),
        materials=len(materials),textures=len(descriptor['materials']['textures']),lod=descriptor['lod'],
        look=source['import_look'],looks=source['looks'],
        source_topology_uv_normals_weights_material_assignments_preserved=True,
        source_uv0_record_stride=source['source_units']['uv0_record_stride'],
        mesh_position_correction=(dict(correction_validation,raw_source_vertex_attributes=['SourcePositionI16_X','SourcePositionI16_Y','SourcePositionI16_Z'])
                                  if correction_validation else None),
        source_uv0_secondary_status=('opaque_uint16_point_attributes' if any(
            section['uv0_secondary_packed'] is not None for section in source['meshes']) else 'not_present'),
        source_tangent_status='packed_uint32_retained_uninterpreted',
        degenerate_triangles_dropped=dropped_triangles,
        degenerate_triangle_policy='repeated-index and zero-area source triangles are omitted from the Blender mesh (they crash Blender 5.0 custom normals); source indices are listed per subset')


def import_native(job,rig):
    descriptors=[x for x in [job.get('native_geometry'),*job.get('native_parts',[])] if x]
    if not descriptors:return None
    results=[_import_native_descriptor(job,rig,descriptor) for descriptor in descriptors]
    return dict(status='native_ps4_geometry_decoded',parts=results,mesh_count=sum(x['mesh_count'] for x in results),
        vertices=sum(x['vertices'] for x in results),triangles=sum(x['triangles'] for x in results),
        materials=sum(x['materials'] for x in results),textures=sum(x['textures'] for x in results),
        lod=job['native_geometry']['lod'],look=job['native_geometry'].get('look',0),
        source_topology_uv_normals_weights_material_assignments_preserved=True,
        degenerate_triangles_dropped=sum(len(x['degenerate_triangles_dropped']) for x in results),
        source_tangent_status='packed_uint32_retained_uninterpreted')
