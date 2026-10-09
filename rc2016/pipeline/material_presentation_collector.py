"""Collect compact, reopened Blender evidence for material presentation review."""
from __future__ import annotations

import json
from rc2016.pipeline.material_presentation_gate import evaluate_material_presentation
from rc2016.formats.materials import validated_profile


def _json_prop(owner, key, fallback):
    try:
        value = owner.get(key)
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _socket_index(sockets, socket):
    for index, candidate in enumerate(sockets):
        if candidate == socket:
            return index
    raise ValueError('Linked Blender socket is absent from its node socket table')


def _channel_stats(image, cache):
    key=(image.as_pointer(), tuple(int(x) for x in image.size))
    if key in cache:
        return cache[key]
    width,height=key[1]
    if width <= 0 or height <= 0:
        return None
    pixels=__import__('numpy').empty(width*height*4,dtype='float32')
    image.pixels.foreach_get(pixels)
    rgba=pixels.reshape((-1,4))
    np=__import__('numpy')
    stats={}
    for index,channel in enumerate('RGBA'):
        values=rgba[:,index]
        stats[channel]={'mean':float(np.mean(values)),
                        'p50':float(np.median(values)),
                        'min':float(np.min(values)),
                        'max':float(np.max(values))}
    cache[key]=stats
    return stats


def _profile_receipt(material):
    source_path=material.get('source_material_path')
    source_sha=str(material.get('source_material_sha256') or '').lower()
    stored_profile_sha=str(material.get('validated_profile_sha256') or '').lower()
    claimed_material_sha=str(material.get('validated_profile_material_sha256') or '').lower()
    if not source_path or not source_sha or not stored_profile_sha or claimed_material_sha != source_sha:
        return None
    try:
        profile=validated_profile(str(source_path),source_sha)
        if profile is None or profile.get('profile_sha256') != stored_profile_sha:
            return None
        stored_recipe=json.loads(material.get('validated_recipe') or 'null')
        if stored_recipe != profile.get('recipe'):
            return None
        return {'verified':True,'source_material_path':str(source_path),
                'source_material_sha256':source_sha,
                'profile_sha256':stored_profile_sha,'recipe':profile['recipe']}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _material_record(material, image_stats_cache):
    if material is None:
        return {}
    bindings=_json_prop(material,'source_texture_bindings',[])
    nodes=[]
    node_ids={node.as_pointer():f'n{index}' for index,node in enumerate(material.node_tree.nodes)} if material.node_tree else {}
    for node in (material.node_tree.nodes if material.node_tree else []):
        row={'id':node_ids[node.as_pointer()],'type':node.type}
        if node.type=='OUTPUT_MATERIAL':
            row['active']=bool(node.is_active_output)
            try: row['surface_socket_index']=_socket_index(node.inputs,node.inputs['Surface'])
            except (KeyError,ValueError): pass
        if node.type=='MATH':
            row['operation']=node.operation
        defaults={}
        for index,socket in enumerate(node.inputs):
            if not socket.is_linked:
                value=socket.default_value if hasattr(socket,'default_value') else None
                try:
                    if hasattr(value,'__len__'): value=list(value)
                    elif value is not None: value=float(value)
                    if value is not None: defaults[str(index)]=value
                except (TypeError,ValueError):
                    pass
        if defaults: row['input_defaults']=defaults
        if node.type=='TEX_IMAGE':
            image=node.image
            info={'source_texture_path':node.get('source_texture_path'),
                  'source_texture_sha256':node.get('source_texture_sha256')}
            if image:
                info.update(colorspace=image.colorspace_settings.name,alpha_mode=image.alpha_mode)
                role=next((binding.get('role') for binding in bindings if isinstance(binding,dict)
                           and binding.get('path')==info['source_texture_path']
                           and str(binding.get('dds_sha256','')).lower()==str(info['source_texture_sha256'] or '').lower()),None)
                if role=='colored_specular_gloss':
                    try: info['channel_stats']=_channel_stats(image,image_stats_cache)
                    except (RuntimeError,ValueError,MemoryError): pass
            row['image']=info
        nodes.append(row)
    links=[]
    if material.node_tree:
        for link in material.node_tree.links:
            links.append({'from_node':node_ids[link.from_node.as_pointer()],
                          'from_socket_index':_socket_index(link.from_node.outputs,link.from_socket),
                          'to_node':node_ids[link.to_node.as_pointer()],
                          'to_socket_index':_socket_index(link.to_node.inputs,link.to_socket)})
    record={'source_material_path':material.get('source_material_path'),
            'source_material_sha256':material.get('source_material_sha256'),
            'profile_claim_present':any(material.get(key) for key in (
                'validated_profile_material_sha256','validated_profile_sha256','validated_recipe')),
            'texture_bindings':bindings if isinstance(bindings,list) else [],
            'nodes':nodes,'links':links}
    receipt=_profile_receipt(material)
    if receipt: record['exact_profile_receipt']=receipt
    return record


def collect_material_presentation_status(scene):
    """Build the evaluator report from reopened mesh slots; never edits Blender data."""
    cache={}
    meshes=[]
    for obj in scene.objects:
        if obj.type!='MESH' or obj.data is None:
            continue
        slots=[]
        for index,slot in enumerate(obj.material_slots):
            slots.append({'slot_index':index,'material':_material_record(slot.material,cache)})
        meshes.append({'polygon_material_indices':[p.material_index for p in obj.data.polygons],
                       'material_slots':slots})
    report=evaluate_material_presentation({'meshes':meshes})
    report['collector']='reopened_blender_scene'
    report['collector_stats_images']=len(cache)
    report['game_equivalence']=False
    return report


def collect_material_presentation_status_safely(scene):
    """Return a review diagnostic instead of losing collector errors in the report."""
    try:
        return collect_material_presentation_status(scene)
    except Exception as exc:
        return {'schema':'rcps4-material-presentation-gate-v1',
                'status':'collector_failed',
                'status_counts':{'review_required_full_reconstruction':1},
                'materials':[],
                'reason_codes':['reopened_material_evidence_collection_failed'],
                'diagnostic_type':type(exc).__name__,
                'collector':'reopened_blender_scene',
                'game_equivalence':False}
