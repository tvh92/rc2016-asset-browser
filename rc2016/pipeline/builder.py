"""Blender background build and reopen validation of one model. Launched by run.py inside Blender."""
import argparse
from contextlib import contextmanager
import gzip
import json
import math
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy
import numpy as np
from mathutils import Matrix,Vector
from rc2016.pipeline.animation_bake import make_bake_rig
from rc2016.formats.animation_samples import decode_clip
from rc2016.formats.animation_transforms import sample_transforms
from rc2016.common import dump,load,read,file_hash,sha,unzip_json
from rc2016.pipeline.blender_support import (bake,targets,target_array,structural_snapshot,add_curve,
    read_values,all_constant_interpolation,curve_keys)
from rc2016.pipeline.bulk_checks import (max_abs_error,first_mismatch,split_polygons,constant_curve_values,
    pose_matrices,matrix_errors,sample_frames,cached_frames_match)
from rc2016.pipeline.channel_library import ChannelLibrary,validate_channel_library
from rc2016.pipeline.timeline import timeline
from rc2016.pipeline.geometry import import_fbx,import_native,texture_connection_coverage
from rc2016.formats.native_model import blender_safe_triangles,decode_model,validate_mesh_position_correction
from rc2016.pipeline.lighting_blender import apply_game_lighting
from rc2016.formats.texture_transport import validate_reopened_texture_bindings
from rc2016.shaders.validation import validate_polygon_used_material_preflights
from rc2016.pipeline.material_presentation_collector import collect_material_presentation_status
from rc2016.pipeline.scene_defaults import (apply_new_scene_defaults,audit_scene,_configure_optix_gpu,check_shared_lighting,load_shared_lighting,
    fit_render_region,fit_render_size,frame_camera_zoom,visible_mesh_points,pack_scene_images_except_shared_hdri)


# The game stores assets in Y-up coordinates.  Keep source mesh/bone/action data in
# that native basis for exact validation, and rotate the presentation objects into
# Blender's Z-up world.  Game (x,y,z) -> Blender (x,-z,y).
SOURCE_TO_BLENDER=Matrix(((1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))


def timeline_strip_label(index,entry):
    """Keep strip labels unique, readable, and within Blender's 63-character ID limit."""
    ordinal=f"{index+1:04d} | "
    variant=(" | EXP FULL" if entry.get('experimental_inheritance_preview') and entry.get('experimental_selected_inheritance')=='full' else
        " | EXP SSC0" if entry.get('experimental_inheritance_preview') else '')
    available=max(0,63-len(ordinal)-len(variant))
    return ordinal+entry['name'][:available]+variant


@contextmanager
def review_preview_lighting(scene):
    """Temporarily light review renders with shared Golden Gate HDRI (Veldin-look scenes keep their own)."""
    if scene.get('lighting_source')=='veldin_capture':
        original_render=(scene.render.resolution_x,scene.render.resolution_y,
            scene.render.resolution_percentage,scene.cycles.samples,scene.render.filepath,scene.frame_current)
        try:
            yield dict(source='saved_veldin_capture_look',presentation_only=True,game_lighting_equivalence_claim=False,
                saved_scene_world=scene.world.name if scene.world else None,saved_lighting_source='veldin_capture',
                view_transform=scene.view_settings.view_transform,exposure=scene.view_settings.exposure)
        finally:
            (scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage,
             scene.cycles.samples,scene.render.filepath,frame)=original_render
            scene.frame_set(frame)
        return
    # The scene's recorded shared lighting, else the current assets/lighting/shared_lighting.json.
    try:settings=json.loads(scene['shared_lighting'])
    except (KeyError,TypeError,ValueError):settings=load_shared_lighting()
    hdri=Path(settings['hdri_path']).resolve();strength=float(settings['world_strength'])
    if not hdri.is_file():
        raise FileNotFoundError('Shared preview HDRI is missing: '+str(hdri))
    original_world=scene.world
    original_render=(scene.render.resolution_x,scene.render.resolution_y,
        scene.render.resolution_percentage,scene.cycles.samples,scene.render.filepath,scene.frame_current)
    light_states=[(obj,obj.hide_render) for obj in scene.objects if obj.type=='LIGHT']
    temporary_world=None;temporary_image=None
    try:
        source='saved_shared_golden_gate_hdri'
        if scene.get('lighting_source') in {'novalis_game_reflection_probe','novalis_game_reflection_probe_plus_atmosphere_sun'}:
            source='shared_golden_gate_hdri_presentation_override'
            temporary_world=bpy.data.worlds.new('Validation Preview | Golden Gate Hills')
            temporary_world.use_nodes=True
            nodes=temporary_world.node_tree.nodes;nodes.clear()
            output=nodes.new('ShaderNodeOutputWorld');background=nodes.new('ShaderNodeBackground')
            environment=nodes.new('ShaderNodeTexEnvironment');environment.name='Shared Golden Gate Review HDRI'
            temporary_image=bpy.data.images.load(str(hdri),check_existing=True)
            if temporary_image.packed_file:
                raise RuntimeError('Shared preview HDRI unexpectedly loaded packed: '+str(hdri))
            temporary_image.filepath=str(hdri)
            temporary_image['shared_external_hdri_path']=str(hdri)
            environment.image=temporary_image
            background.inputs['Strength'].default_value=strength
            temporary_world.node_tree.links.new(environment.outputs['Color'],background.inputs['Color'])
            temporary_world.node_tree.links.new(background.outputs['Background'],output.inputs['Surface'])
            output.is_active_output=True
            scene.world=temporary_world
        else:
            output=next(n for n in scene.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
            background=output.inputs['Surface'].links[0].from_node
            environment=background.inputs['Color'].links[0].from_node
            image=environment.image
            current_path=Path(bpy.path.abspath(image.filepath)).resolve() if image else None
            if (environment.type!='TEX_ENVIRONMENT' or image is None or image.packed_file or
                    current_path!=hdri or abs(background.inputs['Strength'].default_value-strength)>1e-5):
                raise RuntimeError('Review previews require the shared external HDRI world')
        for light,_ in light_states:
            light.hide_render=True
        yield dict(source=source,path=str(hdri),sha256=file_hash(hdri),world_strength=strength,
            presentation_only=True,game_lighting_equivalence_claim=False,
            saved_scene_world=original_world.name if original_world else None,
            saved_lighting_source=scene.get('lighting_source','shared_external_hdri'),
            disabled_separate_lights=len(light_states),saved_scene_world_restored=True)
    finally:
        scene.world=original_world
        for light,hide_render in light_states:
            light.hide_render=hide_render
        (scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage,
         scene.cycles.samples,scene.render.filepath,frame)=original_render
        scene.frame_set(frame)
        if temporary_world and temporary_world.name in bpy.data.worlds:
            bpy.data.worlds.remove(temporary_world)
        if temporary_image and temporary_image.users==0 and not temporary_image.packed_file:
            bpy.data.images.remove(temporary_image)


def _uv0_loop_error(mesh,uv,section):
    """Largest per-component |UV0 loop value - source vertex UV| (0.0 without loops)."""
    loop_vertices=read_values(mesh.loops,'vertex_index',1,np.int32)
    actual=read_values(uv.data,'uv',2).reshape(-1,2)
    expected=np.asarray(section['uv0'],dtype=np.float64).reshape(-1,2)
    return max_abs_error(actual,expected[loop_vertices]) if len(loop_vertices) else 0.0


def _int_attribute(attr):
    return read_values(attr.data,'value',1,np.int32).astype(np.int64)


def validate_reopened_native_descriptor(descriptor,objects,joint_names=None):
    """Fail closed against a fresh source decode and used material identities."""
    correction=descriptor.get('mesh_position_correction')
    source=decode_model(descriptor['path'],descriptor['lod'],descriptor.get('look',0),
                        expected_sha256=descriptor['sha256'],preserve_source_positions=bool(correction))
    correction_validation=validate_mesh_position_correction(correction,source['source_sha256'],source['meshes'])
    position_factor=float(correction['scale']) if correction_validation else 1.0
    by_subset={int(obj.get('source_subset_index',-1)):obj for obj in objects
               if obj.get('source_model_sha256')==descriptor['sha256']}
    assert len(by_subset)==len(objects),'Duplicate or foreign native subset objects'
    expected={int(section['index']):section for section in source['meshes']}
    assert set(by_subset)==set(expected),('Native subset identity/count differs after reopen',set(by_subset),set(expected))
    material_rows=descriptor['materials']['materials']
    summary=[]
    semantic_rows=validate_reopened_native_semantics(source,objects)
    semantic_by_subset={row['subset']:row for row in semantic_rows}
    for index,section in expected.items():
        obj=by_subset[index];mesh=obj.data
        assert obj.get('source_material_index')==section['material_index'],('Native source material index changed',index)
        assert len(mesh.vertices)==len(section['positions']),('Native vertex count changed',index)
        source_triangles,dropped=blender_safe_triangles(section['positions'],section['triangles'])
        recorded=json.loads(obj['source_degenerate_triangles_dropped']) if 'source_degenerate_triangles_dropped' in obj else []
        assert recorded==dropped,('Recorded degenerate-triangle drops differ from a fresh decode',index)
        assert len(mesh.polygons)==len(source_triangles),('Native triangle count changed',index)
        positions=read_values(mesh.vertices,'co',3).reshape(-1,3)
        assert max_abs_error(positions,np.asarray(section['positions'],dtype=np.float64).reshape(-1,3)*position_factor)<=1e-6,(
            'Native positions changed',index)
        if correction_validation:
            assert obj.get('native_mesh_position_scale_factor')==position_factor
            assert obj.get('native_mesh_position_scale_profile')==correction['schema']
            for axis,label in enumerate(('X','Y','Z')):
                attr=mesh.attributes.get('SourcePositionI16_'+label)
                assert attr and attr.domain=='POINT' and len(attr.data)==len(section['positions']),(
                    'Corrected native mesh lacks exact raw position attribute',index,label)
                raw=np.asarray(section['source_positions_i16'],dtype=np.int64).reshape(-1,3)[:,axis]
                assert first_mismatch(_int_attribute(attr),raw) is None,(
                    'Raw native position attribute changed after reopen',index,label)
        actual_faces=[tuple(face) for face in split_polygons(read_values(mesh.loops,'vertex_index',1,np.int32),
            read_values(mesh.polygons,'loop_start',1,np.int32),read_values(mesh.polygons,'loop_total',1,np.int32))]
        assert actual_faces==source_triangles,('Native topology changed',index)
        uv=mesh.uv_layers.get('UV0')
        assert uv is not None,'Native mesh lacks UV0: '+obj.name
        # Native source UVs are authoritative here. Section 2's FBX effect-UV
        # exception applies only to the FBX adapter, as documented in Sheepinator README.
        assert len(uv.data)==len(mesh.loops),'Native UV0 loop count changed'
        uv0_error=_uv0_loop_error(mesh,uv,section)
        assert uv0_error<=1e-6,('Native UV0 changed',index)
        tangent=mesh.attributes.get('SourceTangentPacked')
        assert tangent is not None and tangent.domain=='POINT' and len(tangent.data)==len(section['packed_tangents']),(
            'Native packed tangent attribute is missing or malformed',index)
        vertex_index=first_mismatch(_int_attribute(tangent)&0xffffffff,np.asarray(section['packed_tangents'],dtype=np.int64))
        assert vertex_index is None,('Native packed tangent word changed',index,vertex_index)
        # Builds made before the +12 tangent decode lack SourceTangent; their
        # exact packed words above still validate, and the absence is recorded.
        # When present, the attribute must match a fresh decode.
        decoded_tangent=mesh.attributes.get('SourceTangent')
        if decoded_tangent is None:
            decoded_tangent_state='absent_pre_tangent_decode_build'
        else:
            assert decoded_tangent.domain=='POINT' and decoded_tangent.data_type=='FLOAT_VECTOR' \
                and len(decoded_tangent.data)==len(section['tangents']),('Native decoded tangent attribute is malformed',index)
            assert max_abs_error(read_values(decoded_tangent.data,'vector',3).reshape(-1,3),
                np.asarray(section['tangents'],dtype=np.float64).reshape(-1,3))<=1e-6,('Native decoded tangent changed',index)
            decoded_tangent_state='matches_fresh_decode'
        source_weights=section['weights']
        if source_weights and any(weights is not None for weights in source_weights):
            names=joint_names or descriptor.get('joint_names')
            assert names,'Native source weights have no source joint-name mapping'
            name_to_groups={}
            for group in obj.vertex_groups:name_to_groups.setdefault(group.name,[]).append(group.index)
            assert all(len(indices)==1 for indices in name_to_groups.values()),'Ambiguous reopened vertex-group names'
            group_to_name={indices[0]:name for name,indices in name_to_groups.items()}
            for vertex_index,(vertex,want_rows) in enumerate(zip(mesh.vertices,source_weights)):
                actual_rows={group_to_name[item.group]:float(item.weight)
                             for item in vertex.groups if item.weight>0 and item.group in group_to_name}
                if want_rows is None:
                    assert not actual_rows,('Unexpected reopened skin weights',index,vertex_index)
                    continue
                want_rows={names[int(joint)]:float(weight) for joint,weight in want_rows if weight>0}
                assert set(actual_rows)==set(want_rows),('Native skin influence identities changed',index,vertex_index)
                for name,want in want_rows.items():
                    assert math.isfinite(actual_rows[name]) and abs(actual_rows[name]-want)<=1e-5,(
                        'Native skin weight changed',index,vertex_index,name,actual_rows[name],want)
        else:
            assert not any(vertex.groups for vertex in mesh.vertices),('Unexpected reopened skin weights',index)
        used=sorted(set(read_values(mesh.polygons,'material_index',1,np.int32).tolist()))
        assert used,'Native subset has no polygon-used material slot'
        for local_slot in used:
            assert local_slot<len(mesh.materials),'Polygon references missing local material slot'
            actual=mesh.materials[local_slot]
            expected_index=int(section['material_index'])
            assert expected_index<len(material_rows),'Source material index outside descriptor table'
            want=material_rows[expected_index]
            assert actual is not None and actual.get('source_material_path')==want['path'] and \
                actual.get('source_material_sha256')==want.get('source_sha256',''),(
                    'Polygon-used slot resolves to wrong source material path/hash',index,local_slot,
                    actual.get('source_material_path') if actual else None)
        summary.append(dict(subset=index,source_model_sha256=descriptor['sha256'],
                            vertices=len(mesh.vertices),triangles=len(mesh.polygons),
                            source_triangles=len(section['triangles']),degenerate_triangles_dropped=dropped,
                            local_position_scale_factor=position_factor,
                            raw_source_i16_attributes_exact=bool(correction_validation),
                            mesh_position_correction_validation=correction_validation,
                            source_material_index=section['material_index'],used_local_slots=used,
                            uv0_primary_loop_roundtrip_max_error=uv0_error,
                            uv0_secondary_words_exact=semantic_by_subset[index]['uv0_secondary_words_exact'],
                            byte_and_roundtrip_integrity=semantic_by_subset[index]['byte_and_roundtrip_integrity'],
                            semantic_evidence=semantic_by_subset[index]['semantic_evidence'],
                            packed_tangent_words_exact=True,decoded_tangent_attribute=decoded_tangent_state,
                            source_skin_weights_compared=bool(
                                source_weights and any(weights is not None for weights in source_weights))))
    return summary


def native_material_texture_connection_coverage(descriptors, objects):
    """Report node-link reachability for materials used by actual native polygons."""
    material_reports=[]
    for descriptor in descriptors:
        rows=descriptor.get('materials',{}).get('materials',[])
        by_index={int(row['index']):row for row in rows}
        usage={index:dict(polygons=0,slot_mismatch_polygons=0,sections=[],slot_materials=[]) for index in by_index}
        for obj in objects:
            if obj.get('source_model_sha256')!=descriptor.get('sha256'):
                continue
            source_index=int(obj.get('source_material_index',-1))
            if source_index not in usage:
                continue
            for polygon in obj.data.polygons:
                usage[source_index]['polygons']+=1
                usage[source_index]['sections'].append(int(obj.get('source_subset_index',-1)))
                if polygon.material_index>=len(obj.data.materials):
                    usage[source_index]['slot_mismatch_polygons']+=1
                    continue
                assigned=obj.data.materials[polygon.material_index]
                expected=by_index[source_index]
                if assigned and assigned.get('source_material_path')==expected.get('path') and \
                        assigned.get('source_material_sha256')==expected.get('source_sha256'):
                    usage[source_index]['slot_materials'].append(assigned)
                else:
                    usage[source_index]['slot_mismatch_polygons']+=1
        for index,source in by_index.items():
            used=usage[index]
            if not used['polygons']:
                continue
            materials=list(dict.fromkeys(used['slot_materials']))
            material=materials[0] if len(materials)==1 and not used['slot_mismatch_polygons'] else None
            connection=texture_connection_coverage(material,source.get('textures',[]))
            material_reports.append(dict(source_model_path=descriptor.get('path'),
                source_model_sha256=descriptor.get('sha256'),material_index=index,
                source_material_path=source.get('path'),source_material_sha256=source.get('source_sha256'),
                polygon_used=True,used_polygon_count=used['polygons'],
                slot_mismatch_polygon_count=used['slot_mismatch_polygons'],
                used_section_indices=sorted(set(used['sections'])),
                matched_blender_material_count=len(materials),texture_connection_coverage=connection))
    totals={key:sum(row['texture_connection_coverage']['summary'][key] for row in material_reports)
        for key in ('connected','disconnected','unresolved')}
    return dict(schema='native-material-texture-node-reachability-v1',status='reported',
        summary=dict(polygon_used_material_count=len(material_reports),binding_count=sum(totals.values()),
            connected=totals['connected'],disconnected=totals['disconnected'],unresolved=totals['unresolved']),
        materials=material_reports,
        scope='Only materials assigned to actual polygons in reopened native model sections are included. Status means Blender node-link reachability, not graph execution or game shader equivalence.')


def validate_reopened_native_semantics(source,objects):
    """Assert saved object evidence matches fresh source decode; report claims separately from round trips."""
    by_subset={int(obj.get('source_subset_index',-1)):obj for obj in objects
               if obj.get('source_model_sha256')==source['source_sha256']}
    expected={int(section['index']):section for section in source['meshes']}
    assert set(by_subset)==set(expected),'Native semantic evidence object/subset coverage differs'
    rows=[]
    for index,section in expected.items():
        obj=by_subset[index];mesh=obj.data;uv=mesh.uv_layers.get('UV0')
        assert uv is not None,'Native mesh lacks UV0: '+obj.name
        primary_error=_uv0_loop_error(mesh,uv,section)
        expected_evidence=section['semantic_evidence']
        stored=json.loads(obj.get('source_semantic_evidence','{}'))
        assert stored==expected_evidence,('Saved semantic evidence differs from fresh decode',index,stored,expected_evidence)
        assert primary_error<=1e-6,('Native UV0 loop values changed after reopen',index,primary_error)
        secondary_exact=None
        if section['uv0_secondary_packed'] is not None:
            u=mesh.attributes.get('SourceUV0SecondaryU');v=mesh.attributes.get('SourceUV0SecondaryV')
            assert u and v,'Native secondary UV words are missing after reopen'
            pairs=np.asarray(section['uv0_secondary_packed'],dtype=np.int64).reshape(-1,2)
            u_words=_int_attribute(u);v_words=_int_attribute(v)
            # A shorter attribute fails, as the former per-index read did.
            secondary_exact=(len(u_words)>=len(pairs) and len(v_words)>=len(pairs) and
                first_mismatch(u_words[:len(pairs)],pairs[:,0]) is None and first_mismatch(v_words[:len(pairs)],pairs[:,1]) is None)
            assert secondary_exact,('Native secondary UV words changed after reopen',index)
        rows.append(dict(subset=index,uv0_primary_loop_roundtrip_max_error=primary_error,
                         uv0_secondary_words_exact=secondary_exact,
                         byte_and_roundtrip_integrity='passed',
                         semantic_evidence=expected_evidence))
    return rows


def _attach_native_semantics(geometry,job):
    """Persist per-subset evidence in the candidate .blend and its source report."""
    descriptors=[d for d in [job.get('native_geometry'),*job.get('native_parts',[])] if d]
    parts=geometry.get('parts',[]) if geometry else []
    all_rows=[]
    for descriptor,part in zip(descriptors,parts):
        source=decode_model(descriptor['path'],descriptor['lod'],descriptor.get('look',0),
                            expected_sha256=descriptor['sha256'])
        objects=[o for o in bpy.data.objects if o.type=='MESH' and
                 o.get('source_model_sha256')==descriptor['sha256']]
        by_subset={int(o.get('source_subset_index',-1)):o for o in objects}
        rows=[]
        for section in source['meshes']:
            index=int(section['index']);obj=by_subset.get(index)
            assert obj is not None,('Native subset missing while saving semantic evidence',descriptor['sha256'],index)
            evidence=section['semantic_evidence']
            obj['source_semantic_evidence']=json.dumps(evidence,sort_keys=True)
            rows.append(dict(subset=index,semantic_evidence=evidence))
        part['subset_semantic_evidence']=rows
        part['uv0_semantic_status']=('unproven_8_byte_mapping' if source['source_units']['uv0_record_stride']==8
                                     else 'validated_4_byte_uv0_mapping')
        all_rows.extend(rows)
    if geometry:
        has_uv8=any(row['semantic_evidence']['uv0']['record_stride_bytes']==8 for row in all_rows)
        geometry['semantic_evidence_status']='structural_integrity_and_semantic_confidence_reported_separately'
        geometry['game_accurate_uv_claim']=not has_uv8
        geometry['uv8_mapping_unproven']=has_uv8
        geometry['structural_gate_scope']='record storage, topology, and Blender round-trip only; see per-subset semantic_evidence for mapping confidence'
    return geometry


def embed(name,value):
    text=bpy.data.texts.get(name) or bpy.data.texts.new(name);text.clear()
    text.write(value if isinstance(value,str) else json.dumps(value,indent=2))


def apply_presentation_basis(objects):
    relevant=[obj for obj in objects if obj.type in {'ARMATURE','MESH'}]
    for obj in relevant:
        obj.matrix_world=SOURCE_TO_BLENDER.copy()
        obj['presentation_basis']='game_y_up_to_blender_z_up'
    bpy.context.scene['presentation_basis']='game_y_up_to_blender_z_up'
    return relevant


def diagnostic_mesh(rig,rows,rest):
    """Explicit skeleton visualization; this is never labeled as source geometry."""
    points=np.array([list(m.translation) for m in rest.values()]);extent=float(np.linalg.norm(np.ptp(points,axis=0))) or 1
    verts=[];faces=[];groups=[]
    for r in rows:
        head=rest[r['name']].translation;radius=extent*.008
        start=len(verts)
        verts += [tuple(head+Vector(v)*radius) for v in [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]]
        faces += [tuple(start+i for i in tri) for tri in [(0,2,4),(2,1,4),(1,3,4),(3,0,4),(2,0,5),(1,2,5),(3,1,5),(0,3,5)]]
        groups.append((r['name'],list(range(start,start+6))))
    mesh=bpy.data.meshes.new('Diagnostic joint markers');mesh.from_pydata(verts,[],faces);mesh.update()
    obj=bpy.data.objects.new('DIAGNOSTIC SKELETON | source geometry unavailable',mesh);bpy.context.scene.collection.objects.link(obj)
    obj['purpose']='Generated joint markers for manual motion review; not extracted model geometry'
    for name,indices in groups:obj.vertex_groups.new(name=name).add(indices,1,'REPLACE')
    modifier=obj.modifiers.new('Source joint motion','ARMATURE');modifier.object=rig
    material=bpy.data.materials.new('Diagnostic skeleton cyan');material.use_nodes=True
    material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=(.025,.4,.55,1)
    mesh.materials.append(material)


def make_rig(job):
    before=set(bpy.data.objects)
    rows=job['model']['skeleton'];entry=dict(kind='absolute skeletal',inheritance=job['rest_inheritance'])
    if not rows:
        geometry=import_native(job,None) or import_fbx(job,None)
        if not geometry:raise ValueError('Static model has neither native nor validated adapter geometry')
        if job.get('native_geometry') or job.get('native_parts'):geometry=_attach_native_semantics(geometry,job)
        presented=apply_presentation_basis(set(bpy.data.objects)-before)
        geometry['presentation_basis']='game_y_up_to_blender_z_up';geometry['presented_objects']=[o.name for o in presented]
        embed('Source geometry validation.json',geometry)
        return None
    rest=targets(rows,[],entry)
    data=bpy.data.armatures.new('Source skeleton');source=bpy.data.objects.new('Original source skeleton',data)
    bpy.context.scene.collection.objects.link(source);bpy.context.view_layer.objects.active=source;source.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    extent=max((m.translation.length for m in rest.values()),default=1) or 1
    for row in rows:
        bone=data.edit_bones.new(row['name']);bone.head=rest[row['name']].translation
        bone.tail=bone.head+Vector((0,max(extent*.025,1e-5),0))
        if row['parent']>=0:bone.parent=data.edit_bones[rows[row['parent']]['name']]
    bpy.ops.object.mode_set(mode='OBJECT')
    for row in rows:
        source.data.bones[row['name']]['source_joint_hash']=row['hash']
    rig=make_bake_rig(source,rows,'Source animation rig')
    # Keep the animated armature in the scene for Actions and modifier evaluation,
    # while hiding the skeleton display in saved Blender files by default.
    rig.hide_set(True);rig.hide_render=True;rig.show_in_front=True
    helpers=rig.data.collections.new('Affine helpers')
    for bone in rig.data.bones:
        if bone.name.startswith('__sample__'):
            for collection in list(bone.collections):collection.unassign(bone)
            helpers.assign(bone)
    helpers.is_visible=False
    right={}
    for row in rows:
        name=row['name']
        try:right[name]=rest[name].inverted()@rig.data.bones[name].matrix_local
        except ValueError:raise ValueError('Singular model rest transform requires a supported model profile: '+name)
    rig['source_right_corrections']=json.dumps({n:[list(r) for r in m] for n,m in right.items()})
    geometry=import_native(job,rig) or import_fbx(job,rig)
    if geometry and (job.get('native_geometry') or job.get('native_parts')):geometry=_attach_native_semantics(geometry,job)
    if geometry:embed('Source geometry validation.json',geometry)
    else:diagnostic_mesh(rig,rows,rest)
    presented=apply_presentation_basis(set(bpy.data.objects)-before)
    if geometry:
        geometry['presentation_basis']='game_y_up_to_blender_z_up';geometry['presented_objects']=[o.name for o in presented]
        embed('Source geometry validation.json',geometry)
    return rig


def build(job):
    candidate=Path(job['candidate']);checkpoint=Path(job['checkpoint'])
    if checkpoint.exists():
        bpy.ops.wm.open_mainfile(filepath=str(checkpoint))
        if bpy.context.scene.get('pipeline_fingerprint') != job['fingerprint']:
            raise ValueError('Blender checkpoint belongs to different inputs/code; use a new OutputRoot or -Force')
        rig=bpy.data.objects.get('Source animation rig');_configure_optix_gpu(bpy)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene=bpy.context.scene
        before=audit_scene(scene)
        apply_new_scene_defaults(scene,job.get('hdri'),configure_world=not (job.get('game_lighting') or job.get('veldin_look')),
            lighting=job.get('shared_lighting'))
        if job.get('game_lighting'):
            lighting_validation=apply_game_lighting(scene,job['game_lighting'],job['game_lighting_exr'])
            embed('Game lighting validation.json',lighting_validation)
        rig=make_rig(job);scene['pipeline_fingerprint']=job['fingerprint']
        scene['source_model_sha256']=job['model']['model_sha256']
        embed('Build preflight.json',before)
    scene=bpy.context.scene;rows=job['model']['skeleton']
    channel_library=ChannelLibrary(rig,bpy.data.actions,add_curve) if rig else None
    if rig and not bpy.data.actions.get('Original source rest pose'):
        bake(rig,rows,dict(action='Original source rest pose',name='Original source rest pose',kind='absolute skeletal',
            inheritance=job['rest_inheritance'],frames=1,fps=30),[rows],channel_library=channel_library)
    # Visible meshes provide the existing camera framing after baking.
    # Avoid retaining unused animated joint positions and duplicate world
    # evaluation; keep the original post-bake mesh-bound evaluation point.
    has_visible_mesh=any(obj.type=='MESH' and not obj.hide_render for obj in scene.objects)
    bounds=[]
    need_motion_bounds=scene.camera is None and not has_visible_mesh
    for index,entry in enumerate(job['clips']):
        frames=unzip_json(entry['samples'])['frames']
        if need_motion_bounds:
            for frame in frames:
                points=np.asarray([list(SOURCE_TO_BLENDER@m.translation) for m in targets(rows,frame,entry).values()])
                if len(points):
                    bounds=[np.minimum(bounds[0],points.min(axis=0)).tolist(),np.maximum(bounds[1],points.max(axis=0)).tolist()] if bounds else [points.min(axis=0).tolist(),points.max(axis=0).tolist()]
        if not bpy.data.actions.get(entry['action']):bake(rig,rows,entry,frames,channel_library=channel_library)
        if (index+1)%25==0:
            bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint),compress=True)
        print('BAKED',index+1,len(job['clips']),entry['name'],flush=True)
    # Frame the model itself: rest-shape bounds of the rendered meshes. Joint motion across
    # every clip (swims, turns, root travel) is only a fallback for mesh-less rigs.
    mesh_bounds=[list(obj.matrix_world@Vector(corner)) for obj in scene.objects
                 if obj.type=='MESH' and not obj.hide_render for corner in obj.bound_box]
    if mesh_bounds:bounds=mesh_bounds
    if channel_library:embed('Animation allocation.json',channel_library.summary())
    if not bounds:bounds=[r['translation'] for r in rows] or [[0,0,0]]
    # With an active clip, frame the camera on that clip's first frame rather than on the last baked Action.
    active_clip=job.get('active_clip_index')
    if rig and active_clip is not None:
        active_action=bpy.data.actions[job['clips'][active_clip]['action']]
        rig.animation_data.action=active_action;rig.animation_data.action_slot=active_action.slots[0]
        scene.frame_set(1)
    # New scene only: fit one perspective camera to the bounding sphere, without changing samples.
    if scene.camera is None:
        points=np.asarray(bounds);lo=points.min(axis=0);hi=points.max(axis=0)
        center=Vector(((lo+hi)/2).tolist());radius=max(float(np.linalg.norm(hi-lo))/2,.05)
        camera_data=bpy.data.cameras.new('Source motion review camera');camera_data.type='PERSP';camera_data.lens=50
        camera=bpy.data.objects.new('Source motion review camera',camera_data);scene.collection.objects.link(camera)
        camera_data.clip_start=max(.0001,radius/10000);camera_data.clip_end=radius*100
        scene.camera=camera
        # Every build keeps the reference angle; the camera is dollied along it so the model fills the frame.
        model_points=visible_mesh_points(scene)
        embed('Camera zoom.json',frame_camera_zoom(scene,model_points if len(model_points) else np.asarray(bounds)))
    # Render Region around the model in the zoomed camera, with padding (applied after the zoom).
    region_receipt=fit_render_region(scene)
    # Render size follows the texture detail: resolution_percentage only, X/Y and the crop stay as fitted.
    region_receipt['render_size']=fit_render_size(scene)
    embed('Render region.json',region_receipt)
    if job.get('veldin_look') and scene.get('lighting_source')!='veldin_capture':
        toolkit=str(Path(job['veldin_look']['view_dir']).parent)
        if toolkit not in sys.path:sys.path.insert(0,toolkit)
        import game_look
        embed('Veldin game look.json',game_look.apply_veldin_look(scene,job['veldin_look']['view_dir']))
    intervals,fps=timeline(job['clips'])
    for marker in list(scene.timeline_markers):scene.timeline_markers.remove(marker)
    if rig:
        for track in list(rig.animation_data.nla_tracks):rig.animation_data.nla_tracks.remove(track)
        rig.animation_data.action=None
        track=rig.animation_data.nla_tracks.new();track.name='All Source Clips | sequential timeline'
        for entry,interval in zip(job['clips'],intervals):
            action=bpy.data.actions[entry['action']]
            label=timeline_strip_label(interval['index'],entry)
            strip=track.strips.new(label,interval['start'],action);strip.name=label;strip.action_slot=action.slots[0]
            strip.use_sync_length=False;strip.action_frame_start=1;strip.action_frame_end=interval['nla_action_end']
            strip.frame_start=interval['start'];strip.frame_end=interval['nla_end'];strip.scale=interval['sample_step']
            strip.repeat=1;strip.blend_type='REPLACE';strip.extrapolation='HOLD_FORWARD';strip.blend_in=0;strip.blend_out=0;strip.use_auto_blend=False
            marker=scene.timeline_markers.new(label,frame=interval['start']);interval['marker']=marker.name;interval['strip']=strip.name
    catalog=dict(rig=rig.name if rig else None,rest_action='Original source rest pose' if rig else None,clips=job['clips'],timeline=intervals,overview_fps=fps)
    # Only metadata paths are retained in the inventory; the controls have no filesystem dependencies.
    catalog['animation_selection']=job.get('animation_selection',dict(mode='all_supported'))
    catalog['source_catalog']=job.get('clip_catalog',job['clips'])
    embed('Animation Catalog.json',catalog)
    embed('Source Animation Catalog.json',catalog['source_catalog'])
    if rig:
        embed('Animation Controls.py',Path(__file__).with_name('controls.py').read_text())
        # Register the Animations panel when the file opens, so no manual Run Script is needed.
        bpy.data.texts['Animation Controls.py'].use_module=True
    embed('Source Model Inventory.json',job['model']);embed('Unsupported and unresolved.json',job['limitations'])
    geometry_note=('Native PS4 mesh, UVs, normals, skin weights, material slots and decoded source textures are included. See Source geometry validation.json.\n\n'
        if job.get('native_geometry') else 'Source FBX geometry is included; registration, topology, UVs and weights were checked numerically. See Source geometry validation.json.\n\n'
        if job.get('geometry') else 'This delivery contains source armature/actions and explicitly labeled diagnostic joint markers. The selected model did not expose the supported PS4 geometry sections.\n\n')
    experimental_count=sum(1 for entry in job['clips'] if entry.get('experimental_inheritance_preview'))
    experimental_note=(f"Experimental animation coverage admitted {experimental_count} candidate Actions across ambiguous source clips blocked only by unresolved full/SSC0 inheritance. Both interpretations are available in the selector; SSC0 is the working preview choice. Neither interpretation is established as game/runtime accurate. Raw ambiguity and source hashes remain in the embedded catalog and Unsupported and unresolved.json.\n\n"
        if experimental_count else '')
    embed('START HERE','Press Space/Play: the saved sequential timeline works immediately.\n\n'
        'For selection, jump-to-boundary and rest restoration: use N > Animations. The panel registers when the file opens; if Blender asks about automatic script execution for this file, allow it once.\n\n'
        +experimental_note+geometry_note+
        'Relative/additive clips are standalone source-rest previews, not exact in-game blends. See embedded inventory for classifications and limitations.\n'
        'Only structural/numerical validation is performed; no images are rendered. Visual inspection is yours.\n')
    ns={}
    if rig:
        exec(bpy.data.texts['Animation Controls.py'].as_string(),ns)
        ns['select_clip'](active_clip) if job['clips'] and active_clip is not None else ns['play_all']() if job['clips'] else ns['restore_rest']()
    for obj in scene.objects:obj.select_set(False)
    if rig:
        rig.select_set(True);bpy.context.view_layer.objects.active=rig
    else:
        meshes=[o for o in scene.objects if o.type=='MESH']
        if meshes:meshes[0].select_set(True);bpy.context.view_layer.objects.active=meshes[0]
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                space=area.spaces.active;space.lock_camera=True
                space.region_3d.view_perspective='CAMERA'
    bpy.context.view_layer.update()
    # Columns in the shader editor (rc2016/pipeline/node_layout.py): built in, so no Blender add-on is needed.
    from rc2016.pipeline.node_layout import arrange_tree
    for material in bpy.data.materials:
        if material.use_nodes and material.node_tree:arrange_tree(material.node_tree)
    for group in bpy.data.node_groups:arrange_tree(group)
    assert not audit_scene(scene, selected_lighting=job.get('shared_lighting'))['mismatches'],audit_scene(scene, selected_lighting=job.get('shared_lighting'))['mismatches']
    embed('Structural baseline.json',structural_snapshot(scene))
    if not (job.get('game_lighting') or job.get('veldin_look')):
        pack_scene_images_except_shared_hdri(scene)
    else:
        bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(candidate),compress=True,relative_remap=False)
    print('BUILT',candidate,flush=True)


def matrix_error(rig,target,right):
    worst=0.
    for name,m in target.items():
        actual=rig.pose.bones[name].matrix@right[name].inverted()
        worst=max(worst,max(abs(a-b) for ra,rb in zip(actual,m) for a,b in zip(ra,rb)))
    return worst


def _matrix_array(matrix):
    return np.array([list(row) for row in matrix],dtype=np.float64)


def _bake_rig_layout(rig,rows,right):
    """Bone order, rest matrices and channel columns of the bake rig, after asserting the
    structure the numpy pose composition assumes (helper roots, full inheritance, local
    location, quaternion channels, no constraints)."""
    names=[r['name'] for r in rows];pose_names=[p for n in names for p in ('__sample__'+n,n)]
    bones=rig.data.bones
    for name in names:
        assert bones['__sample__'+name].parent is None and bones[name].parent and \
            bones[name].parent.name=='__sample__'+name,('Bake rig hierarchy changed',name)
    for name in pose_names:
        bone=bones[name];pose=rig.pose.bones[name]
        assert bone.inherit_scale=='FULL' and bone.use_inherit_rotation and bone.use_local_location and not bone.use_connect,(
            'Bake rig bone inheritance changed',name)
        assert pose.rotation_mode=='QUATERNION' and not len(pose.constraints),('Bake rig pose channel changed',name)
    columns={}
    for j,name in enumerate(pose_names):
        base='pose.bones['+json.dumps(name)+']'
        for prop,offset,n in (('location',0,3),('rotation_quaternion',3,4),('scale',7,3)):
            for k in range(n):columns[(base+'.'+prop,k)]=(j,offset+k)
    return dict(pose_names=pose_names,columns=columns,
        rest=np.stack([_matrix_array(bones[name].matrix_local) for name in pose_names]) if pose_names else np.zeros((0,4,4)),
        parents=[-1 if k%2==0 else k-1 for k in range(len(pose_names))],
        right_inverse=np.stack([np.linalg.inv(_matrix_array(right[name])) for name in names]) if names else np.zeros((0,4,4)))


def _keyframe_poses(rig,layout,curves,frame_count):
    """(frames, joints, 4, 4) armature-space joint matrices defined by an Action's keys.

    Channels without a curve keep the pose bone's current value, as Blender leaves them.
    """
    pose_names=layout['pose_names'];by_name={pb.name:pb for pb in rig.pose.bones}
    current=np.array([[*by_name[n].location,*by_name[n].rotation_quaternion,*by_name[n].scale] for n in pose_names],
                     dtype=np.float64).reshape(len(pose_names),10)
    channels=np.repeat(current[None],frame_count,axis=0)
    frames=np.arange(1,frame_count+1,dtype=np.float64);seen=set()
    for curve in curves:
        key=(curve.data_path,curve.array_index)
        assert key in layout['columns'] and key not in seen,('Unexpected or duplicate Action channel',key)
        seen.add(key);j,column=layout['columns'][key]
        key_frames,key_values=curve_keys(curve)
        channels[:,j,column]=constant_curve_values(key_frames,key_values,frames)
    posed=pose_matrices(layout['rest'],layout['parents'],channels)
    return posed[:,1::2]


def _deformation_probe(native_objects,correction_descriptor,rig,entry,frame,frame_count):
    correction_meshes=[o for o in native_objects if o.get('source_model_sha256')==correction_descriptor['sha256']
                       and any(mod.type=='ARMATURE' and mod.object==rig for mod in o.modifiers)]
    moved=[]
    depsgraph=bpy.context.evaluated_depsgraph_get()
    for obj in correction_meshes:
        evaluated=obj.evaluated_get(depsgraph);evaluated_mesh=evaluated.to_mesh()
        try:
            count=min(len(obj.data.vertices),len(evaluated_mesh.vertices))
            base=read_values(obj.data.vertices,'co',3).reshape(-1,3)[:count].astype(np.float64)
            posed=read_values(evaluated_mesh.vertices,'co',3).reshape(-1,3)[:count].astype(np.float64)
            base_matrix=_matrix_array(obj.matrix_world);posed_matrix=_matrix_array(evaluated.matrix_world)
            base_world=base@base_matrix[:3,:3].T+base_matrix[:3,3]
            posed_world=posed@posed_matrix[:3,:3].T+posed_matrix[:3,3]
            moved.extend(np.linalg.norm(posed_world-base_world,axis=1).tolist())
        finally:evaluated.to_mesh_clear()
    if not moved or max(moved)<=1e-3:
        raise AssertionError('Pinned corrected Trooper Action did not deform the reopened mesh')
    return dict(status='passed',sample_sha256=entry['sample_sha256'],
        animset_sha256=entry['animset_sha256'],source_frame=frame,source_frame_count=frame_count,
        fps=entry['fps'],corrected_meshes=len(correction_meshes),
        moved_vertex_count=sum(value>1e-3 for value in moved),
        maximum_world_displacement=max(moved),mean_world_displacement=float(np.mean(moved)))


def _validate_ordinary_surface_profile(material,profile):
    """Reopen-time graph assertions for an exact ordinary-surface recipe."""
    recipe=profile['recipe']
    assert abs(float(material.get('reconstruction_mix_factor',-999))-float(recipe['reconstruction_mix']))<1e-7,(
        'Profiled reconstruction factor changed')
    tree=material.node_tree;nodes=tree.nodes;links=tree.links
    if recipe.get('presentation_overrides'):
        overrides=recipe['presentation_overrides']
        outputs=[node for node in nodes if node.type=='OUTPUT_MATERIAL' and node.is_active_output]
        assert len(outputs)==1,'Presentation profile must have one active Material Output'
        surface=outputs[0].inputs.get('Surface')
        assert surface and len(surface.links)==1,'Presentation profile active surface link is missing/ambiguous'
        mix=surface.links[0].from_node
        assert mix.type=='MIX_SHADER' and mix.name=='Principled / recovered reconstruction',(
            'Presentation profile active output bypasses reconstruction Mix')
        assert abs(float(mix.inputs[0].default_value)-float(recipe['reconstruction_mix']))<1e-7
        principled=nodes.get('Source-channel Principled')
        assert principled and principled.type=='BSDF_PRINCIPLED'
        emission=principled.inputs.get('Emission Strength')
        roughness=principled.inputs.get('Roughness')
        metallic=principled.inputs.get('Metallic')
        assert emission and not emission.is_linked and abs(float(emission.default_value)-float(overrides['principled_emission_strength']))<1e-7,(
            'Presentation profile emission must be disconnected and zero')
        assert roughness and not roughness.is_linked and abs(float(roughness.default_value)-float(overrides['principled_roughness']))<1e-6,(
            'Presentation profile roughness must be disconnected and match its recipe')
        assert metallic and abs(float(metallic.default_value)-float(overrides['principled_metallic']))<1e-7
        source_bindings=profile.get('source_texture_bindings',[])
        by_role={row.get('role'):row for row in source_bindings if isinstance(row,dict)}
        base=by_role.get('base_color');normal=by_role.get('normal')
        assert base and normal,'Presentation profile C/N identities are missing'
        base_links=list(principled.inputs['Base Color'].links)
        normal_links=list(principled.inputs['Normal'].links)
        assert len(base_links)==1 and base_links[0].from_node.get('source_texture_path')==base.get('path') and \
               base_links[0].from_node.get('source_texture_sha256')==base.get('dds_sha256'),(
            'Presentation profile source C link changed')
        assert len(normal_links)==1 and normal_links[0].from_node.type=='NORMAL_MAP',(
            'Presentation profile normal-map path is missing')
        normal_map=normal_links[0].from_node
        normal_tex_links=list(normal_map.inputs['Color'].links)
        assert len(normal_tex_links)==1 and normal_tex_links[0].from_node.get('source_texture_path')==normal.get('path') and \
               normal_tex_links[0].from_node.get('source_texture_sha256')==normal.get('dds_sha256'),(
            'Presentation profile source N link changed')
        assert abs(float(normal_map.inputs['Strength'].default_value)-1.0)<1e-7
        baseline_name=material.get('profile_baseline_material')
        baseline= bpy.data.materials.get(baseline_name) if baseline_name else None
        assert baseline and baseline.use_fake_user,'Presentation profile generic baseline is absent or not retained'
        assert baseline.get('profile_baseline_material_sha256')==material.get('source_material_sha256')
        baseline_mix=baseline.node_tree.nodes.get('Principled / recovered reconstruction')
        assert baseline_mix and abs(float(baseline_mix.inputs[0].default_value)-1.0)<1e-7,(
            'Presentation profile baseline must retain factor-one generic reconstruction')
        baseline_principled=baseline.node_tree.nodes.get('Source-channel Principled')
        assert baseline_principled and baseline_principled.inputs['Emission Strength'].is_linked and \
               baseline_principled.inputs['Roughness'].is_linked,(
            'Presentation profile baseline lost generic emission/roughness links')
        return
    # Reusable reconstruction profiles retain the validated roughness-floor graph.
    assert nodes.get('Validated roughness floor'),'Validated roughness floor is absent'
    white=nodes.get('White minus _g');product=nodes.get('_c times (1 - _g)')
    assert white and white.operation=='SUBTRACT' and tuple(white.inputs[0].default_value)[:3]==(1,1,1)
    assert product and product.operation=='MULTIPLY'
    assert any(link.from_node==white and link.to_node==product for link in links)


def validate(job):
    # This process only reads and evaluates the saved scene and never renders, so it
    # configures no GPU; the saved Cycles settings are still audited.
    bpy.ops.wm.open_mainfile(filepath=job['candidate'])
    scene=bpy.context.scene;rows=job['model']['skeleton'];rig=bpy.data.objects.get('Source animation rig')
    baseline=json.loads(bpy.data.texts['Structural baseline.json'].as_string())
    assert structural_snapshot(scene)==baseline,'Scene changed on save/reopen'
    audit=audit_scene(scene,check_gpu=False,selected_lighting=job.get('shared_lighting'))
    assert not audit['mismatches'],audit['mismatches']
    assert scene['source_model_sha256']==job['model']['model_sha256']
    assert scene.get('presentation_basis')=='game_y_up_to_blender_z_up'
    presented=[o for o in scene.objects if o.get('presentation_basis')=='game_y_up_to_blender_z_up']
    assert presented,'No source objects use the game-to-Blender presentation basis'
    for obj in presented:
        assert max(abs(a-b) for actual,expected in zip(obj.matrix_world,SOURCE_TO_BLENDER) for a,b in zip(actual,expected))<1e-7,('Presentation basis changed',obj.name)
    assert len(bpy.data.actions)==len(job['clips'])+(1 if rig else 0),'Action count'
    ns={}
    if rig:
        exec(bpy.data.texts['Animation Controls.py'].as_string(),ns);ns['register']()
        catalog=ns['catalog']();intervals=catalog['timeline']
        if 'clip_catalog' in job:
            assert catalog['source_catalog']==job['clip_catalog'],'Full source catalog changed after reopen'
            assert catalog['animation_selection']==job['animation_selection'],'Clip selection identity changed after reopen'
        if rig.get('source_channel_library_state'):
            result_allocation=validate_channel_library(rig,bpy.data.actions)
        else:result_allocation=None
    else:
        assert not job['clips'],'Static model unexpectedly received skeletal clips'
        intervals=[]
    assert len(intervals)==len(job['clips'])
    assert len(scene.timeline_markers)==len(job['clips'])
    right={n:Matrix(m) for n,m in json.loads(rig['source_right_corrections']).items()} if rig else {}
    names={int(r['hash'],16):r['name'] for r in rows}
    correction_descriptor=job.get('native_geometry') or {}
    mesh_position_correction=correction_descriptor.get('mesh_position_correction')
    deformation_probe=mesh_position_correction.get('deformation_probe') if mesh_position_correction else None
    deformation_probe_result=None
    result=dict(status='passed',validation_type='numerical_and_structural_only',visual_review_performed=False,
        presentation_basis='game_y_up_to_blender_z_up',presentation_objects=[o.name for o in presented],
        source_geometry_available=bool(job.get('geometry') or job.get('native_geometry')),geometry_validation=(json.loads(bpy.data.texts['Source geometry validation.json'].as_string()) if job.get('geometry') or job.get('native_geometry') else 'Diagnostic skeleton only; supported native geometry sections absent'),
        action_count=len(bpy.data.actions),source_action_count=len(job['clips']),clips=[],
        baseline_sha256=sha(json.dumps(baseline,sort_keys=True).encode()))
    result['animation_selection']=job.get('animation_selection',dict(mode='all_supported'))
    result['animation_allocation']=result_allocation if rig else None
    source_images=[i for i in bpy.data.images if i.get('source_texture_path')]
    for image in source_images:
        assert image.packed_file and image.size[0]>0 and image.size[1]>0,'Source texture is missing or unpacked: '+image.name
        assert image.alpha_mode=='CHANNEL_PACKED','Source texture alpha mode changed: '+image.name
    transport_rows=validate_reopened_texture_bindings(job.get('texture_transport',[]),source_images)
    failed_transport=[row for row in transport_rows if row['status']=='failed']
    result['texture_transport_validation']=dict(status='passed' if not failed_transport else 'failed',
        bindings=transport_rows,
        scope='Source identity, raw or LZ4 HD stream, supported compressed or uncompressed top-mip DDS byte equality, and reopened packed Blender image bytes/settings including tagged R8G8_UNORM PNG conversion; no game shader or lower-mip claim.')
    assert not failed_transport,'Texture transport validation failed: '+json.dumps(failed_transport,sort_keys=True)
    try:
        result['material_presentation_status']=collect_material_presentation_status(scene)
    except Exception as exc:
        # Presentation review is reported independently: a collector problem must
        # not silently certify appearance or change structural validation outcome.
        result['material_presentation_status']=dict(
            schema='rcps4-material-presentation-gate-v1',status='collector_failed',
            status_counts={'review_required_full_reconstruction':1},materials=[],
            reason_codes=['reopened_material_evidence_collection_failed'],
            diagnostic_type=type(exc).__name__,game_equivalence=False)
    if job.get('game_lighting'):
        descriptor=job['game_lighting']
        expected_source=('novalis_game_reflection_probe_plus_atmosphere_sun' if descriptor.get('atmosphere_sun') else 'novalis_game_reflection_probe')
        assert scene.get('lighting_source')==expected_source
        assert scene.get('lighting_region')==descriptor['region'] and scene.get('lighting_probe_sha256')==descriptor['source_sha256']
        output=next(n for n in scene.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
        background=output.inputs['Surface'].links[0].from_node;environment=background.inputs['Color'].links[0].from_node
        assert environment.type=='TEX_ENVIRONMENT' and environment.image and environment.image.packed_file
        assert tuple(environment.image.size)==(1024,512)
        assert environment.image.get('source_probe_sha256')==descriptor['source_sha256']
        shared_name=Path((job.get('shared_lighting') or load_shared_lighting())['hdri_path']).name.lower()
        assert not any(image.name.lower().startswith(shared_name) for image in bpy.data.images),'External HDRI survived game-lighting replacement'
        evidence=json.loads(bpy.data.texts['Game lighting validation.json'].as_string())
        expected_status=('NOVALIS_REFLECTION_PROBE_AND_ATMOSPHERE_SUN_CONNECTED' if descriptor.get('atmosphere_sun') else 'NOVALIS_REFLECTION_PROBE_CONNECTED')
        assert evidence['status']==expected_status and evidence['seam_to_interior_ratio']<=2
        assert evidence['world_strength']==1.0 and abs(background.inputs['Strength'].default_value-1.0)<1e-5
        assert abs(scene['lighting_world_strength']-1.0)<1e-5
        sun_validation=None
        if descriptor.get('atmosphere_sun'):
            source=descriptor['atmosphere_sun'];lights=[obj for obj in scene.objects if obj.type=='LIGHT']
            assert len(lights)==1 and lights[0].data.type=='SUN','Expected exactly one Novalis atmosphere Sun'
            sun=lights[0];rgb=source['surface_irradiance_rgb'];peak=max(rgb)
            assert abs(sun.data.energy-peak)<1e-6 and abs(sun.data.angle-source['angular_radius_radians'])<1e-7
            assert max(abs(a-b/peak) for a,b in zip(sun.data.color,rgb))<1e-6
            actual=(sun.rotation_euler.to_matrix()@Vector((0,0,-1))).normalized()
            expected=Vector(source['ray_direction_blender_xyz']).normalized()
            assert (actual-expected).length<1e-6,'Novalis Sun direction changed'
            assert evidence['atmosphere_sun']['source_sha256']==source['source_sha256']
            sun_validation=dict(status='passed',source_path=source['source_path'],source_sha256=source['source_sha256'],
                surface_irradiance_rgb=rgb,energy=sun.data.energy,color=list(sun.data.color),angle=sun.data.angle,
                direction_error=(actual-expected).length,semantic_status=source['status'])
        else:
            assert not any(obj.type=='LIGHT' for obj in scene.objects),'Unexpected light in probe-only Novalis scene'
        result['game_lighting_integrity']=dict(status='passed',region=descriptor['region'],probe=descriptor['source_path'],
            source_sha256=descriptor['source_sha256'],packed_environment=True,width=1024,height=512,
            world_strength=1.0,radiometric_status=evidence.get('radiometric_status','UNSCALED_GAME_LINEAR_PROBE_RUNTIME_EXPOSURE_NOT_DECODED'),
            seam_to_interior_ratio=evidence['seam_to_interior_ratio'],d4_states=evidence['d4_states'],
            atmosphere_sun=sun_validation,external_hdri_absent=True,
            limitations=['Novalis light-grid basis axes and position-dependent sampling are not decoded'])
    elif job.get('veldin_look'):
        view=json.loads(Path(job['veldin_look']['view_dir'],'rc_game_view.json').read_text(encoding='utf-8'))
        evidence=json.loads(bpy.data.texts['Veldin game look.json'].as_string())
        lights=[obj for obj in scene.objects if obj.type=='LIGHT']
        assert scene.get('lighting_source')=='veldin_capture'
        assert len(lights)==1 and lights[0].data.type=='SUN' and not lights[0].hide_render and \
            lights[0].get('lighting_role')=='veldin_captured_key_light','Expected exactly one captured Veldin key light'
        assert abs(lights[0].data.energy-evidence['sun_strength'])<1e-5
        assert max(abs(a-b) for a,b in zip(lights[0].data.color,evidence['sun_colour']))<1e-6
        tree=scene.compositing_node_group
        group=tree and tree.nodes.get('RC PS4 Game View (compositor)')
        assert scene.view_settings.view_transform=='Raw' and abs(scene.view_settings.exposure)<1e-9 and scene.render.use_compositing
        assert group and group.node_tree and group.node_tree.name=='RC PS4 Game View (compositor)','Compositor game view missing after reopen'
        assert abs(group.inputs['Exposure (stops)'].default_value-view['blender_exposure_stops'])<1e-5
        out=next(n for n in tree.nodes if n.type=='GROUP_OUTPUT')
        assert out.inputs[0].links and out.inputs[0].links[0].from_node==group,'Compositor game view does not reach the output'
        for name in ('RC PS4 tonemap 1D LUT','RC PS4 grade 3D LUT (tiled)'):
            lut=bpy.data.images.get(name)
            assert lut and lut.packed_file and lut.is_float and lut.colorspace_settings.name=='Non-Color',name
        assert scene.world and scene.world.name==evidence['world']
        result['veldin_look_integrity']=dict(status='passed',sun_strength=evidence['sun_strength'],
            exposure=view['blender_exposure_stops'],view='RC PS4 Game (compositor nodes, view transform Raw)',world=scene.world.name,
            rc_game_view_sha256=job['veldin_look']['rc_game_view_sha256'],game_lighting_equivalence_claim=False)
    else:
        output=next(n for n in scene.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
        background=output.inputs['Surface'].links[0].from_node
        environment=background.inputs['Color'].links[0].from_node
        image=environment.image
        hdri_path=Path(bpy.path.abspath(image.filepath)).resolve()
        assert environment.type=='TEX_ENVIRONMENT' and image and not image.packed_file
        assert hdri_path.is_file() and image.get('shared_external_hdri_path')==str(hdri_path)
        assert image.size[0]>0 and image.size[0]==2*image.size[1],'HDRI is not a loaded equirectangular image'
        expected=job.get('shared_lighting') or load_shared_lighting()
        problems=check_shared_lighting(scene,expected)
        assert not problems,'Scene differs from its shared lighting: %s'%problems
        view=scene.view_settings
        result['external_hdri']=dict(status='passed',path=str(hdri_path),packed=False,
            width=int(image.size[0]),height=int(image.size[1]),world_strength=float(expected['world_strength']),
            view_transform=view.view_transform,exposure=float(view.exposure),
            lighting_config=expected.get('config_path'),lighting_config_sha256=expected.get('config_sha256'),
            portability='Requires the shared EXR at the recorded absolute path')
    if job.get('native_geometry'):
        source_hashes={d['sha256'] for d in [job['native_geometry'],*job.get('native_parts',[])]}
        native_objects=[o for o in scene.objects if o.type=='MESH' and o.get('source_model_sha256') in source_hashes]
        assert native_objects,'Native geometry is absent after reopen'
        descriptors=[job['native_geometry'],*job.get('native_parts',[])]
        source_subset_proofs=[]
        for descriptor in descriptors:
            descriptor_objects=[o for o in native_objects if o.get('source_model_sha256')==descriptor['sha256']]
            source_subset_proofs.extend(validate_reopened_native_descriptor(
                descriptor,descriptor_objects,descriptor.get('joint_names') or [r['name'] for r in rows]))
        valid_groups={r['name'] for r in rows}
        weighted_vertices=0
        for obj in native_objects:
            assert obj.data.uv_layers.get('UV0') is not None,'Native mesh lacks UV0: '+obj.name
            assert obj.data.materials and all(x is not None for x in obj.data.materials),'Native mesh lacks an assigned material: '+obj.name
            assert obj.data.attributes.get('SourceTangentPacked') is not None,'Native mesh lacks preserved packed tangent word: '+obj.name
            # Vertex-group memberships have no bulk accessor; one pass covers both checks.
            object_group_names=[g.name for g in obj.vertex_groups];any_groups=False
            for vertex in obj.data.vertices:
                groups=vertex.groups
                if not len(groups):continue
                any_groups=True
                active=[g for g in groups if g.weight>0]
                if not active:continue
                weighted_vertices+=1
                group_names=[object_group_names[g.group] for g in active]
                assert set(group_names)<=valid_groups,'Native weight references an unknown joint: '+obj.name
                assert abs(sum(g.weight for g in active)-1)<.005,'Native weights are not normalized: '+obj.name
            if any_groups:
                modifiers=[m for m in obj.modifiers if m.type=='ARMATURE' and m.object==rig]
                assert modifiers,'Weighted native mesh is not bound to the selected armature: '+obj.name
        source_materials=[m for m in bpy.data.materials if m.get('source_material_path')]
        # A model whose only slots reference no material asset keeps its labelled placeholder instead.
        placeholder_materials=[m for m in bpy.data.materials if m.get('source_material_unset')]
        assert source_materials or placeholder_materials,'No source materials survived reopen'
        for material in source_materials+placeholder_materials:
            output=material.node_tree.nodes.get('Material Output')
            assert output and output.inputs['Surface'].is_linked,'Source material has no active surface: '+material.name
        result['material_texture_connection_coverage']=native_material_texture_connection_coverage(
            descriptors,native_objects)
        result['material_shader_preflight_validation']=validate_polygon_used_material_preflights(
            descriptors,native_objects)
        expected_profiles={m['validated_profile']['material_sha256']:m['validated_profile']
            for descriptor in descriptors for m in descriptor.get('materials',{}).get('materials',[])
            if m.get('validated_profile')}
        actual_profiles={m.get('validated_profile_material_sha256'):m for m in source_materials
            if m.get('validated_profile_material_sha256')}
        assert set(expected_profiles)<=set(actual_profiles),'A validated material profile was not applied after reopen'
        for source_sha256,profile in expected_profiles.items():
            material=actual_profiles[source_sha256];recipe=profile['recipe'];kind=recipe['kind']
            assert material['validated_profile_sha256']==profile['profile_sha256'],'Material profile file identity changed'
            assert json.loads(material['validated_recipe'])==recipe,'Material recipe changed in Blender'
            if kind=='thin_glass':
                assert material.node_tree.nodes.get('Thin glass front blend'),'Validated thin glass nodes are absent'
                assert abs(material['thin_glass_reflection_mix']-recipe['reflection_mix'])<1e-7
                if 'source_glossiness' in recipe:
                    assert abs(recipe['roughness']-(1.0-recipe['source_glossiness']))<1e-7
                    assert abs(material['source_glossiness']-recipe['source_glossiness'])<1e-7
                    reflection=material.node_tree.nodes.get('Thin glass reflection')
                    assert reflection and abs(reflection.inputs['Roughness'].default_value-recipe['roughness'])<1e-7
            elif kind=='transparent_energy_layer':
                assert material.node_tree.nodes.get('Multiply validated energy noise masks'),'Validated energy mask nodes are absent'
                assert abs(material['energy_minimum_opacity']-recipe['minimum_opacity'])<1e-7
            elif kind=='ordinary_surface':
                _validate_ordinary_surface_profile(material,profile)
            else:raise AssertionError('Unknown validated material recipe: '+kind)
        has_uv8=any(row['semantic_evidence']['uv0']['record_stride_bytes']==8
                    for row in source_subset_proofs)
        result['native_asset_integrity']=dict(status='structural_integrity_passed_semantics_reported_separately',
            meshes=len(native_objects),source_subset_proofs=source_subset_proofs,materials=len(source_materials),
            packed_textures=len(source_images),weighted_vertices=weighted_vertices,
            game_accurate_uv_claim=not has_uv8,
            uv_semantic_summary=('8-byte UV mapping unproven' if has_uv8 else '4-byte UV0 mapping validated' if native_objects else 'unavailable'),
            uv0_storage_and_roundtrip_integrity=True,
            source_tangents='packed_uint32_retained_uninterpreted',
            unsupported_normal_semantics='source normal interpretation remains a candidate; FBX corner normals are not an oracle',
            unsupported_tangent_semantics='raw packed tangent words retained; tangent/bitangent decode unsupported',
            weights_normalized_and_in_range=True,material_outputs_linked=True,
            validated_material_profiles=len(expected_profiles),validated_profile_hashes=sorted(expected_profiles),
            material_status='recipe_integrity_only; game_shader_equivalence_unproven')
        if mesh_position_correction:
            corrected_rows=[row for row in source_subset_proofs if row.get('mesh_position_correction_validation')]
            assert len(corrected_rows)==len(source_subset_proofs) and len(corrected_rows)==10,(
                'Corrected source subsets did not all pass the position-scale gate')
            result['mesh_position_correction_validation']=dict(status='passed',
                scale=mesh_position_correction['scale'],source_sha256=correction_descriptor['sha256'],
                bound_signature_sha256=mesh_position_correction['expected_bound_signature_sha256'],
                corrected_subset_count=len(corrected_rows),raw_source_i16_attributes_exact=True,
                coordinate_validation='reopened local vertices equal native decoded positions multiplied by the profiled factor')
    threshold=job['matrix_tolerance']
    track=rig.animation_data.nla_tracks[0] if rig else None
    if rig:
        assert len(track.strips)==len(intervals)
        layout=_bake_rig_layout(rig,rows,right)
    nla_checks=[]
    for index,(entry,interval) in enumerate(zip(job['clips'],intervals)):
        raw=read(entry['raw_clip']);assert sha(raw)==entry['raw_sha256']
        decoded=decode_clip(raw,0,names);frames=sample_transforms(decoded)
        assert decoded['sample_sha256']==entry['sample_sha256']
        assert cached_frames_match(gzip.decompress(read(entry['samples'])),frames),'Cached exact samples differ from fresh raw decoding'
        assert len(frames)==entry['frames']
        action=bpy.data.actions[entry['action']]
        assert len(action.slots)==1 and action.use_fake_user
        assert action['source_raw_sha256']==entry['raw_sha256'] and action['source_sample_sha256']==entry['sample_sha256']
        assert action['source_fps']==entry['fps'] and action['source_frame_count']==len(frames)
        assert action['source_animset_sha256']==entry['animset_sha256'] and action['source_name_hash']==entry['name_hash']
        if entry.get('experimental_inheritance_preview'):
            assert action.get('experimental') is True
            assert action.get('experimental_selected_inheritance')==entry['experimental_selected_inheritance']
            assert json.loads(action['experimental_inheritance_candidates_json'])==entry['experimental_inheritance_candidates']
            assert action.get('experimental_game_accuracy')=='unproven'
            assert entry['selector_label'].endswith(f"EXPERIMENTAL {'FULL INHERITANCE' if entry['experimental_selected_inheritance']=='full' else 'SSC0'}")
        curves=[c for layer in action.layers for strip_data in layer.strips for bag in strip_data.channelbags for c in bag.fcurves]
        assert all(all_constant_interpolation(c) for c in curves)
        strip=track.strips[index]
        assert strip.name==timeline_strip_label(index,entry)
        assert (strip.frame_start,strip.frame_end)==(interval['start'],interval['nla_end'])
        assert (strip.action_frame_start,strip.action_frame_end)==(1,interval['nla_action_end'])
        assert strip.action_slot==action.slots[0] and strip.extrapolation=='HOLD_FORWARD'
        assert strip.scale==interval['sample_step'] and strip.repeat==1 and strip.blend_in==strip.blend_out==0
        assert scene.timeline_markers[interval['marker']].frame==interval['start']
        if index:assert intervals[index-1]['end_exclusive']==interval['start']
        ns['select_clip'](index)
        assert rig.animation_data.action==action and rig.animation_data.action_slot==action.slots[0]
        assert scene.frame_end==len(frames) and abs(scene.render.fps/scene.render.fps_base-entry['fps'])<1e-5
        # Every key of every curve, at every source frame: the pose the keys define
        # (bake-rig composition in numpy) against the source matrices.
        expected=np.stack([target_array(rows,frame,entry) for frame in frames])
        posed=_keyframe_poses(rig,layout,curves,len(frames))
        key_errors=matrix_errors(posed,layout['right_inverse'],expected)
        keyframe_error=float(key_errors.max()) if len(key_errors) else 0.
        # Blender's own evaluation (Action path) on sampled frames.
        probe_frame=(int(deformation_probe['frame']) if deformation_probe and entry['sample_sha256']==deformation_probe['sample_sha256']
            and entry['animset_sha256']==deformation_probe['animset_sha256'] else None)
        sampled=sample_frames(len(frames),posed,extra=[probe_frame] if probe_frame else ())
        sampled_targets={fi:targets(rows,frames[fi-1],entry) for fi in sampled}
        action_error=keyframe_error;evaluated_error=0.
        for fi in sampled:
            scene.frame_set(fi);bpy.context.view_layer.update()
            evaluated_error=max(evaluated_error,matrix_error(rig,sampled_targets[fi],right))
            if fi==probe_frame:
                deformation_probe_result=_deformation_probe(native_objects,correction_descriptor,rig,entry,fi,len(frames))
        action_error=max(action_error,evaluated_error)
        assert action_error<threshold,(entry['name'],keyframe_error,evaluated_error,threshold)
        nla_checks.append((index,entry,interval,sampled,sampled_targets))
        result['clips'].append(dict(name=entry['name'],raw_sha256=entry['raw_sha256'],
            sample_sha256=entry['sample_sha256'],animset_sha256=entry['animset_sha256'],frames=len(frames),fps=entry['fps'],
            action_max_matrix_error=action_error,keyframe_max_matrix_error=keyframe_error,
            keyframes_checked=int(sum(len(c.keyframe_points) for c in curves)),fcurves_checked=len(curves),
            action_evaluated_frames=sampled,action_evaluated_max_matrix_error=evaluated_error,
            boundaries_passed=True,selection_passed=True))
        print('VERIFIED',index+1,len(job['clips']),entry['name'],action_error,flush=True)
    # NLA overview once: the same sampled ticks of every strip, its first tick (the
    # boundary with the previous strip) and the held final tick.
    if nla_checks:
        ns['play_all']()
        for index,entry,interval,sampled,sampled_targets in nla_checks:
            nla_error=0.
            for fi in sampled:
                scene.frame_set(interval['start']+(fi-1)*interval['sample_step']);bpy.context.view_layer.update()
                nla_error=max(nla_error,matrix_error(rig,sampled_targets[fi],right))
            last=entry['frames'];last_targets=sampled_targets[last]
            scene.frame_set(interval['end_inclusive']);bpy.context.view_layer.update()
            nla_error=max(nla_error,matrix_error(rig,last_targets,right))
            assert nla_error<threshold,(entry['name'],nla_error,threshold)
            row=result['clips'][index]
            row.update(nla_max_matrix_error=nla_error,nla_evaluated_frames=[interval['start']+(fi-1)*interval['sample_step'] for fi in sampled]+[interval['end_inclusive']])
            print('VERIFIED NLA',index+1,len(job['clips']),entry['name'],nla_error,flush=True)
        jump_checked=sorted({0,(len(nla_checks)-1)//2,len(nla_checks)-1})
        for index in jump_checked:
            ns['jump_to'](index);assert scene.frame_current==intervals[index]['start']
        result['jump_to_checked_clips']=jump_checked
    if rig:
        ns['restore_rest']()
        rest_entry=dict(kind='absolute skeletal',inheritance=job['rest_inheritance'])
        result['rest_max_matrix_error']=matrix_error(rig,targets(rows,[],rest_entry),right)
        assert result['rest_max_matrix_error']<threshold
        if mesh_position_correction:
            source_rest=targets(rows,[],rest_entry);head_errors=[]
            for row in rows:
                name=row['name']
                actual=rig.matrix_world@rig.data.bones[name].head_local
                expected=SOURCE_TO_BLENDER@source_rest[name].translation
                head_errors.append((actual-expected).length)
            result['mesh_position_correction_validation']['source_rig_rest_head_max_error']=max(head_errors)
            assert max(head_errors)<1e-5,'Mesh-only correction changed or misregistered the source rig rest heads'
            assert deformation_probe_result is not None,'Pinned corrected deformation probe did not run after reopen'
            result['mesh_position_correction_validation']['deformation_probe']=deformation_probe_result
        active_clip=job.get('active_clip_index')
        if job['clips'] and active_clip is not None:
            ns['select_clip'](active_clip)
            active_action=bpy.data.actions[job['clips'][active_clip]['action']]
            assert rig.animation_data.action==active_action and all(t.mute for t in rig.animation_data.nla_tracks)
            result['active_clip']=job['clips'][active_clip]['name']
        else:ns['play_all']() if job['clips'] else ns['restore_rest']()
    else:result['rest_max_matrix_error']=None
    assert structural_snapshot(scene)==baseline,'Controls changed scene structure'
    result['scene_preservation_passed']=True
    assert not audit_scene(scene,check_gpu=False,selected_lighting=job.get('shared_lighting'))['mismatches']
    result['matrix_tolerance']=threshold
    result['all_clips_require_user_visual_inspection']=[e['name'] for e in job['clips']]
    dump(job['validation_report'],result)
    print('VALIDATION PASSED',job['validation_report'],flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);parser.add_argument('--validate',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);job=load(args.job)
    validate(job) if args.validate else build(job)


if __name__=='__main__':
    # Session-only: no .blend1 backups next to candidates/checkpoints (not saved to user preferences).
    bpy.context.preferences.filepaths.save_version=0
    main()
