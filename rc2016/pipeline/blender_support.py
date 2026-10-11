"""Generic buffered form of the existing affine SVD baker. No exporter scale or actor names."""
import copy
import hashlib
import json
import numpy as np
import bpy
from mathutils import Matrix,Quaternion
from rc2016.formats.skeleton import worlds
from rc2016.pipeline.bulk_checks import split_polygons


def _relative_frame(rows,frame,entry):
    if entry['kind']=='relative/additive-style layer':
        frame=copy.deepcopy(frame);rest={r['hash']:r for r in rows}
        for sample in frame:
            if sample['hash'] not in rest:continue
            r=rest[sample['hash']];q=r['rotation_xyzw'];dq=sample['rotation_xyzw']
            # A collapsed delta rotation uses identity, so the preview stays at source rest rotation.
            dq=dq or [0,0,0,1]
            result=Quaternion((q[3],*q[:3]))@Quaternion((dq[3],*dq[:3]));result.normalize()
            sample['rotation_xyzw']=[result.x,result.y,result.z,result.w]
            sample['translation']=[a+b for a,b in zip(r['translation'],sample['translation'])]
            sample['scale']=[a*b for a,b in zip(r['scale'],sample['scale'])]
    return frame


def targets(rows,frame,entry):
    frame=_relative_frame(rows,frame,entry)
    return {r['name']:Matrix(m.tolist()) for r,m in zip(rows,worlds(rows,frame,entry['inheritance']))}


def target_array(rows,frame,entry):
    """targets() as a (joints, 4, 4) float64 array in row order."""
    frame=_relative_frame(rows,frame,entry)
    result=worlds(rows,frame,entry['inheritance'])
    return np.asarray(result,dtype=np.float64).reshape(len(rows),4,4)


def keyframe_interpolation_value(name='CONSTANT'):
    """RNA enum value of a keyframe interpolation (CONSTANT is BEZT_IPO_CONST, 0)."""
    return bpy.types.Keyframe.bl_rna.properties['interpolation'].enum_items[name].value


def read_values(collection,prop,width=1,dtype=np.float32):
    """collection.foreach_get into a flat array; per-item reads if raw access is refused."""
    out=np.empty(len(collection)*width,dtype=dtype)
    try:
        collection.foreach_get(prop,out)
        return out
    except (TypeError,RuntimeError,AttributeError):
        values=[getattr(item,prop) for item in collection]
        return np.asarray([v for value in values for v in value] if width>1 else values,dtype=dtype).reshape(-1)


def write_values(collection,prop,values,width=1):
    """collection.foreach_set; per-item writes if raw access is refused."""
    values=np.ascontiguousarray(values).reshape(-1)
    try:
        collection.foreach_set(prop,values)
    except (TypeError,RuntimeError,AttributeError):
        rows=values.reshape(-1,width).tolist() if width>1 else values.tolist()
        for item,value in zip(collection,rows):setattr(item,prop,value)


def set_constant_interpolation(curve):
    """Make every key of an F-curve CONSTANT in one call."""
    points=curve.keyframe_points
    try:
        points.foreach_set('interpolation',np.full(len(points),keyframe_interpolation_value(),dtype=np.int32))
    except (TypeError,RuntimeError,AttributeError):
        for key in points:
            if key.interpolation!='CONSTANT':key.interpolation='CONSTANT'


def all_constant_interpolation(curve):
    points=curve.keyframe_points
    try:
        values=np.empty(len(points),dtype=np.int32);points.foreach_get('interpolation',values)
        return bool(np.all(values==keyframe_interpolation_value()))
    except (TypeError,RuntimeError,AttributeError):
        return all(key.interpolation=='CONSTANT' for key in points)


def curve_keys(curve):
    """(frames, values) of an F-curve's keyframe points as float64 arrays."""
    co=read_values(curve.keyframe_points,'co',2).reshape(-1,2).astype(np.float64)
    return co[:,0],co[:,1]


def add_curve(bag,path,index,values):
    values=np.asarray(values,dtype=np.float32)
    keep=np.r_[True,values[1:]!=values[:-1]]
    points=np.column_stack((np.flatnonzero(keep)+1,values[keep])).astype(np.float32)
    curve=bag.fcurves.new(data_path=path,index=index);curve.keyframe_points.add(len(points))
    curve.keyframe_points.foreach_set('co',points.ravel())
    set_constant_interpolation(curve)
    curve.update()


# Relative singular-value spread (2**-19, float32-level noise) below which the
# right SVD factor is canonicalised. Matrix change is bounded by twice the spread.
CANONICAL_SPREAD=2.**-19


def bake(rig,rows,entry,frames,channel_library=None):
    names=[r['name'] for r in rows];pose_names=[p for n in names for p in ('__sample__'+n,n)]
    buffer=np.empty((len(frames),len(pose_names),10),dtype=np.float32);previous={}
    inverses={n:rig.data.bones[n].matrix_local.inverted() for n in names}
    right={n:Matrix(m) for n,m in json.loads(rig['source_right_corrections']).items()}
    if channel_library:
        # Bone rest matrices and Vt are rigid rotation bases. Native S/Q/T
        # scale (including collapse/reflection/shear) stays in the left SVD
        # singular values. No source channel is rounded or thresholded.
        for name,inverse in inverses.items():
            rotation=np.asarray(inverse.to_3x3())
            if np.max(np.abs(rotation.T@rotation-np.eye(3)))>2e-6 or abs(np.linalg.det(rotation)-1)>2e-6:
                raise ValueError('Right-factor unit scale needs a rigid bone rest basis: '+name)
    for fi,frame in enumerate(frames):
        for j,(name,target) in enumerate(targets(rows,frame,entry).items()):
            target=target@right[name]
            u,singular,vt=np.linalg.svd(np.asarray(target.to_3x3()))
            if np.linalg.det(u)<0:u[:,-1]*=-1;singular[-1]*=-1
            if np.linalg.det(vt)<0:vt[-1,:]*=-1;singular[-1]*=-1
            if channel_library and singular.min()>0 and singular.max()-singular.min()<=CANONICAL_SPREAD*singular.max():
                # Equal singular values leave Vt undetermined: SVD then returns an
                # arbitrary noise-driven basis. Use the polar rotation U@Vt and an
                # identity right factor; singular values stay unchanged.
                u=u@vt;vt=np.eye(3)
            hq=Matrix(u.tolist()).to_quaternion();basis=inverses[name]@Matrix(vt.tolist()).to_4x4()
            for k,(loc,q,scale) in enumerate(((target.translation,hq,singular.tolist()),basis.decompose())):
                pn=pose_names[j*2+k]
                if channel_library and k==1:scale=(1.,1.,1.)
                if pn in previous and q.dot(previous[pn])<0:q.negate()
                previous[pn]=q.copy();buffer[fi,j*2+k]=[*loc,*q,*scale]
    action=bpy.data.actions.new(entry['action']);action.use_fake_user=True
    slot=action.slots.new(id_type='OBJECT',name=rig.name)
    bag=action.layers.new('Exact source sample frames').strips.new(type='KEYFRAME').channelbags.new(slot)
    for j,name in enumerate(pose_names):
        rig.pose.bones[name].rotation_mode='QUATERNION';base='pose.bones['+json.dumps(name)+']'
        for prop,offset,n in [('location',0,3),('rotation_quaternion',3,4),('scale',7,3)]:
            for k in range(n):
                writer=channel_library.emit if channel_library else add_curve
                writer(bag,base+'.'+prop,k,buffer[:,j,offset+k])
    if channel_library:channel_library.finish(action)
    if channel_library:
        action['affine_right_scale_basis']='algebraic_unit_rigid_SVD_factor'
        action['affine_right_rotation_basis']='identity_when_relative_singular_spread_le_2^-19'
    action.use_frame_range=True;action.frame_start=1;action.frame_end=max(1,len(frames))
    for key,value in dict(source_clip=entry['name'],source_frame_count=len(frames),source_fps=entry['fps'],
        source_raw_sha256=entry.get('raw_sha256',''),source_sample_sha256=entry.get('sample_sha256',''),
        source_animset_sha256=entry.get('animset_sha256',''),source_name_hash=entry.get('name_hash',''),
        source_kind=entry['kind'],source_duration_seconds=entry.get('source_duration_seconds',0)).items():action[key]=value
    if entry.get('experimental_inheritance_preview'):
        action['experimental']=True
        action['experimental_selected_inheritance']=entry['inheritance']
        action['experimental_inheritance_candidates_json']=json.dumps(entry['experimental_inheritance_candidates'])
        action['experimental_inheritance_difference']=float(entry['experimental_inheritance_difference'])
        action['experimental_game_accuracy']='unproven'
        action['experimental_reason']=entry['experimental_reason']
    rig.animation_data.action=action;rig.animation_data.action_slot=slot
    return action


def structural_snapshot(scene):
    def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()
    meshes={}
    for o in scene.objects:
        if o.type!='MESH':continue
        m=o.data
        # Bulk reads give the same Python floats/ints (float32 widened) as per-item access.
        group_names=[g.name for g in o.vertex_groups]
        topology=split_polygons(read_values(m.loops,'vertex_index',1,np.int32),
            read_values(m.polygons,'loop_start',1,np.int32),read_values(m.polygons,'loop_total',1,np.int32))
        meshes[o.name]=dict(vertices=len(m.vertices),polygons=len(m.polygons),
            positions=digest(read_values(m.vertices,'co',3).reshape(-1,3).astype(np.float64).tolist()),topology=digest(topology),
            weights=digest([[(group_names[g.group],g.weight) for g in v.groups] for v in m.vertices]),
            normals=digest(read_values(m.corner_normals,'vector',3).reshape(-1,3).astype(np.float64).tolist()),
            uvs=digest([read_values(uv.data,'uv',2).reshape(-1,2).astype(np.float64).tolist() for uv in m.uv_layers]),
            materials=[x.name if x else None for x in m.materials],polygon_materials=digest(read_values(m.polygons,'material_index',1,np.int32).tolist()))
    materials={}
    for m in bpy.data.materials:
        nodes=[];links=[]
        if m.use_nodes:
            for n in m.node_tree.nodes:
                inputs=[]
                for socket in n.inputs:
                    if not hasattr(socket,'default_value'):continue
                    value=socket.default_value
                    try:value=list(value)
                    except TypeError:pass
                    inputs.append((socket.identifier,value))
                nodes.append((n.name,n.bl_idname,inputs,getattr(n,'operation',None),getattr(n,'blend_type',None),n.image.name if n.type=='TEX_IMAGE' and n.image else None))
            links=[(l.from_node.name,l.from_socket.identifier,l.to_node.name,l.to_socket.identifier) for l in m.node_tree.links]
        materials[m.name]=digest((nodes,links))
    result = dict(meshes=meshes,materials=materials,
        cameras={o.name:dict(matrix=[list(r) for r in o.matrix_world],type=o.data.type,lens=o.data.lens,
            shift=[o.data.shift_x,o.data.shift_y],constraints=[(c.name,c.type,c.mute) for c in o.constraints]) for o in scene.objects if o.type=='CAMERA'},
        objects={o.name:dict(type=o.type,hide_render=o.hide_render,eye=o.hide_get(),disable=o.hide_viewport,
            modifiers=[(m.type,m.object.name if m.type=='ARMATURE' and m.object else None) for m in o.modifiers]) for o in scene.objects},
        collections={c.name:dict(disable=c.hide_viewport,hide_render=c.hide_render) for c in bpy.data.collections},
        images={i.name:dict(size=list(i.size),alpha=i.alpha_mode,colorspace=i.colorspace_settings.name,packed=bool(i.packed_file)) for i in bpy.data.images if i.type!='RENDER_RESULT'})
    return json.loads(json.dumps(result))
