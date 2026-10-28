"""Pure-numpy helpers behind the bulk (foreach) Blender build and reopen checks.

Nothing here imports bpy, so the arithmetic is unit-tested outside Blender.
"""
import json
import numpy as np


def weight_batches(weights, joint_names):
    """Group per-vertex skin influences into ``vertex_group.add(indices, weight, 'REPLACE')`` calls.

    A joint listed more than once for one vertex sums its weights, as the GPU skin does with one
    influence slot per row. Returns ``(batches, errors)``;
    batches is a list of ``(joint name, weight, [vertex indices])`` and errors holds
    ``(vertex index, 'missing' | weight sum)`` rows like the per-vertex loop.
    """
    final={};errors=[]
    for vertex_index,rows in enumerate(weights):
        if rows is None:
            errors.append((vertex_index,'missing'));continue
        total=sum(value for _,value in rows)
        if abs(total-1)>.005:errors.append((vertex_index,total))
        for joint,value in rows:
            if joint>=len(joint_names) or not joint_names[joint]:
                raise ValueError('Skin weight references a joint outside the selected actor skeleton')
            if value:
                key=(vertex_index,joint_names[joint]);final[key]=final.get(key,0.0)+value
    groups={}
    for (vertex_index,name),value in final.items():
        groups.setdefault((name,value),[]).append(vertex_index)
    return [(name,value,indices) for (name,value),indices in groups.items()],errors


def per_loop(values, loop_vertex_indices):
    """Flatten per-vertex rows into the per-loop buffer ``foreach_set`` expects."""
    values=np.asarray(values,dtype=np.float64)
    if not len(loop_vertex_indices):
        return np.zeros(0,dtype=np.float32)
    return values[np.asarray(loop_vertex_indices,dtype=np.int64)].astype(np.float32).ravel()


def signed_int32(words):
    """uint32 words as the signed values Blender INT attributes store."""
    words=np.asarray(words,dtype=np.int64).reshape(-1)
    return np.where(words<(1<<31),words,words-(1<<32)).astype(np.int32)


def max_abs_error(actual, expected):
    """Largest absolute component difference (0.0 for empty input)."""
    actual=np.asarray(actual,dtype=np.float64);expected=np.asarray(expected,dtype=np.float64)
    if not actual.size and not expected.size:
        return 0.0
    return float(np.max(np.abs(actual.reshape(expected.shape)-expected)))


def first_mismatch(actual, expected):
    """Index of the first unequal row, or None."""
    actual=np.asarray(actual);expected=np.asarray(expected)
    if len(actual)!=len(expected):
        return min(len(actual),len(expected))
    if not len(actual):
        return None
    bad=np.flatnonzero(~np.all(actual.reshape(len(actual),-1)==expected.reshape(len(expected),-1),axis=1))
    return int(bad[0]) if len(bad) else None


def split_polygons(loop_vertex_indices, loop_starts, loop_totals):
    """Per-polygon vertex lists, as ``[list(p.vertices) for p in mesh.polygons]``."""
    vertices=np.asarray(loop_vertex_indices,dtype=np.int64)
    return [vertices[s:s+n].tolist() for s,n in zip(np.asarray(loop_starts).tolist(),np.asarray(loop_totals).tolist())]


def constant_curve_values(key_frames, key_values, frames):
    """Value of a CONSTANT-interpolated F-curve at each frame (before the first key: the first key)."""
    key_frames=np.asarray(key_frames,dtype=np.float64);key_values=np.asarray(key_values,dtype=np.float64)
    if not len(key_frames):
        raise ValueError('F-curve has no keyframes')
    if np.any(np.diff(key_frames)<=0):
        raise ValueError('F-curve keyframes are not strictly increasing')
    index=np.searchsorted(key_frames,np.asarray(frames,dtype=np.float64),side='right')-1
    return key_values[np.clip(index,0,len(key_values)-1)]


def quaternion_matrices(q):
    """(..., 4) w,x,y,z quaternions -> (..., 3, 3) rotations, normalised first as Blender does."""
    q=np.asarray(q,dtype=np.float64)
    q=q/np.linalg.norm(q,axis=-1,keepdims=True)
    w,x,y,z=np.moveaxis(q,-1,0)
    return np.stack((
        np.stack((1-2*(y*y+z*z),2*(x*y-w*z),2*(x*z+w*y)),-1),
        np.stack((2*(x*y+w*z),1-2*(x*x+z*z),2*(y*z-w*x)),-1),
        np.stack((2*(x*z-w*y),2*(y*z+w*x),1-2*(x*x+y*y)),-1)),-2)


def channel_matrices(channels):
    """(..., 10) [location xyz, quaternion wxyz, scale xyz] -> (..., 4, 4) T @ R @ S."""
    channels=np.asarray(channels,dtype=np.float64)
    out=np.zeros(channels.shape[:-1]+(4,4))
    out[...,:3,:3]=quaternion_matrices(channels[...,3:7])*channels[...,None,7:10]
    out[...,:3,3]=channels[...,:3];out[...,3,3]=1
    return out


def pose_matrices(rest, parents, channels):
    """Armature-space pose matrices of bones with full scale/rotation inheritance and local location.

    ``rest`` (B, 4, 4) bone matrix_local, ``parents`` (B,) parent index or -1 with parents
    listed before children, ``channels`` (F, B, 10). Blender's pose evaluation for this rig:
    pose = parent_pose @ inverse(parent_rest) @ rest @ (T @ R @ S).
    """
    rest=np.asarray(rest,dtype=np.float64);basis=channel_matrices(channels)
    out=np.empty(basis.shape)
    for b,parent in enumerate(parents):
        if parent>=b:
            raise ValueError('Bone parents must precede their children')
        local=rest[b]@basis[:,b]
        out[:,b]=local if parent<0 else out[:,parent]@(np.linalg.inv(rest[parent])@local)
    return out


def matrix_errors(actual, right_inverse, targets):
    """Per-frame max |actual @ right_inverse - target| over joints and matrix entries ((F,) array)."""
    actual=np.asarray(actual,dtype=np.float64)
    if not actual.shape[0] or not actual.shape[1]:
        return np.zeros(actual.shape[0])
    diff=np.abs(actual@np.asarray(right_inverse,dtype=np.float64)-np.asarray(targets,dtype=np.float64))
    return diff.reshape(diff.shape[0],-1).max(axis=1)


def sample_frames(count, matrices=None, extra=()):
    """1-based source frames evaluated through the depsgraph: first, middle, last, largest motion.

    The largest-motion frame is the one whose matrices differ most from the first frame.
    """
    count=int(count)
    if count<1:
        raise ValueError('Clip has no frames')
    frames={1,(count+1)//2,count}
    if matrices is not None and count>1:
        matrices=np.asarray(matrices,dtype=np.float64)
        motion=np.abs(matrices-matrices[:1]).reshape(count,-1).max(axis=1)
        frames.add(int(np.argmax(motion))+1)
    frames.update(int(f) for f in extra if 1<=int(f)<=count)
    return sorted(frames)


def cached_frames_match(cached_json, frames):
    """True when a clips.zip_json payload ``{"clip":...,"frames":F}`` holds exactly these frames.

    Compares serialized text (the writer's separators and key order) instead of parsing
    the cached clip and deep-comparing every sample; equal text means equal values.
    """
    text=json.dumps(frames,separators=(',',':'),allow_nan=False).encode()
    cached_json=bytes(cached_json)
    return cached_json.startswith(b'{"clip":') and cached_json.endswith(b',"frames":'+text+b'}')
