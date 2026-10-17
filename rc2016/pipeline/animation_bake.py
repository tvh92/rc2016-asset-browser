"""Bake source SQT samples as affine bone matrices, without character rules.

The caller supplies the source-to-FBX bind corrections and presentation anchor.
Two SVD factors retain shear and collapsed scales at exact sample frames.
"""
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector
from rc2016.formats.animation_transforms import sample_transforms


def local_matrix(row, fallback=None):
    q = row['rotation_xyzw'] or fallback['rotation_xyzw']
    q = Quaternion((q[3], *q[:3])); q.normalize()
    return (Matrix.Translation(Vector(row['translation']) * 50)
            @ q.to_matrix().to_4x4() @ Matrix.Diagonal((*row['scale'], 1)))


def world_samples(rows, frame, anchor):
    sampled = {r['name']: r for r in frame if r['name']}
    world = {}
    for row in rows:
        name = row['name']; sample = sampled.get(name, row)
        local = local_matrix(sample, row)
        parent = rows[row['parent']]['name'] if row['parent'] >= 0 else None
        if parent and row['flags'] & 1:
            # Keep the local translation column unchanged while compensating
            # the linear basis; this matches pipeline.model.worlds(bit0).
            ps = sampled.get(parent, rows[row['parent']])['scale']
            t = Matrix.Translation(Vector(sample['translation']) * 50)
            local = t @ Matrix.Diagonal(tuple(1/x if x else 0 for x in ps)+(1,)) @ t.inverted() @ local
        world[name] = world[parent] @ local if parent else anchor @ local
    return world


def make_bake_rig(source_rig, rows, name):
    rig = source_rig.copy(); rig.data = source_rig.data.copy(); rig.name = name
    bpy.context.scene.collection.objects.link(rig)
    for obj in bpy.context.scene.objects:
        for modifier in obj.modifiers:
            if modifier.type == 'ARMATURE' and modifier.object == source_rig:
                modifier.object = rig
    source_rig.hide_set(True); source_rig.hide_render = True
    rig.animation_data_clear(); rig.animation_data_create()
    rig.hide_set(False); bpy.context.view_layer.objects.active = rig; rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    rest = {r['name']: rig.data.edit_bones[r['name']].matrix.copy() for r in rows}
    for row in rows:
        b = rig.data.edit_bones[row['name']]; b.use_connect = False; b.parent = None
        helper = rig.data.edit_bones.new('__sample__'+row['name'])
        helper.head = (0,0,0); helper.tail = (0,1,0); helper.use_deform = False
        b.parent = helper; b.matrix = rest[row['name']]
    bpy.ops.object.mode_set(mode='OBJECT')
    for b in rig.data.bones:
        b.inherit_scale = 'FULL'; b.use_inherit_rotation = True; b.use_local_location = True
    return rig


def bake_clip(rig, rows, clip, corrections, anchor, right_corrections=None):
    action = bpy.data.actions.new(clip['name']); action.use_fake_user = True
    action['source_sample_sha256'] = clip['sample_sha256']
    action['source_fps'] = clip['metadata']['sample_rate_fps']
    frames = sample_transforms(clip); action['source_frame_count'] = len(frames)
    rig.animation_data.action = action
    previous = {}; maximum = 0.
    for f, frame in enumerate(frames, 1):
        bpy.context.scene.frame_set(f)
        targets = {name: corrections[name] @ m @ (right_corrections[name] if right_corrections else Matrix.Identity(4)) for name,m in world_samples(rows,frame,anchor).items() if name in corrections}
        for name,target in targets.items():
            u, singular, vt = np.linalg.svd(np.array([list(r) for r in target.to_3x3()]))
            if np.linalg.det(u)<0: u[:,-1]*=-1; singular[-1]*=-1
            if np.linalg.det(vt)<0: vt[-1,:]*=-1; singular[-1]*=-1
            helper = rig.pose.bones['__sample__'+name]
            hq = Matrix(u.tolist()).to_quaternion()
            basis = rig.data.bones[name].matrix_local.inverted() @ Matrix(vt.tolist()).to_4x4()
            for pb,values in ((helper,(target.translation,hq,singular.tolist())), (rig.pose.bones[name],basis.decompose())):
                loc,q,scale = values
                if pb.name in previous and q.dot(previous[pb.name])<0: q.negate()
                previous[pb.name] = q.copy()
                pb.rotation_mode='QUATERNION'; pb.location=loc; pb.rotation_quaternion=q; pb.scale=scale
                for prop in ('location','rotation_quaternion','scale'):
                    pb.keyframe_insert(data_path=prop,frame=f,group=pb.name)
        bpy.context.view_layer.update()
        maximum = max(maximum, matrix_error(rig,targets))
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    for key in curve.keyframe_points: key.interpolation='CONSTANT'
    assert maximum<.001, maximum
    action['conversion_max_matrix_error']=maximum
    return action, maximum


def matrix_error(rig, targets):
    return max(abs(a-b) for name,m in targets.items()
               for ra,rb in zip(rig.pose.bones[name].matrix,m) for a,b in zip(ra,rb))
