"""Validated model SQT layout plus per-model inverse-bind/inheritance evidence."""
import math
import struct
import numpy as np
from rc2016.formats.container import read_container
from rc2016.formats.animation_transforms import model_skeleton
from rc2016.common import read, sha


def rotation(q):
    x,y,z,w = np.asarray(q,dtype=float)/np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                     [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                     [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])


def worlds(rows, sampled=(), inheritance='full'):
    # For the bit-zero candidate, the compensation changes only the local
    # linear basis. Leaving m[:3,3] untouched is intentional: this is
    # algebraically the same as the Blender adapter's
    # T @ inverse(parent_scale) @ T^-1 @ (T @ R @ S) form.
    by_hash = {r['hash']:r for r in sampled}
    result = []
    for r in rows:
        s = by_hash.get(r['hash'],r)
        q = s['rotation_xyzw'] or r['rotation_xyzw']
        m = np.eye(4); m[:3,:3] = rotation(q) @ np.diag(s['scale']); m[:3,3] = s['translation']
        parent = r['parent']
        if parent >= 0:
            if inheritance == 'segment_scale_compensation_bit0' and r['flags'] & 1:
                scale = by_hash.get(rows[parent]['hash'],rows[parent])['scale']
                m[:3,:3] = np.diag([1/x if x else 0 for x in scale]) @ m[:3,:3]
            m = result[parent] @ m
        result.append(m)
    return result


def inspect_model(path, override=None):
    data = read(path); root = read_container(data,0,{})
    if root['declared_size'] != len(data):
        raise ValueError('Model has bytes outside DAT1 container')
    sections = {s['label_hash']:s for s in root['sections']}
    if len(sections) != len(root['sections']):
        raise ValueError('Duplicate model section labels')
    spans=sorted((s['offset'],s['offset']+s['size']) for s in root['sections'] if s['size'])
    if any(a[1]>b[0] for a,b in zip(spans,spans[1:])):
        raise ValueError('Overlapping model sections')
    joint_sections={'0x15DF9D3B','0xDCC88A19'}
    present=joint_sections & sections.keys()
    if present and present != joint_sections:
        raise ValueError('Unsupported model: incomplete joint/SQT section pair')
    rows = model_skeleton(path) if present else []
    for i,r in enumerate(rows):
        values = r['scale']+r['rotation_xyzw']+r['translation']
        if not all(math.isfinite(v) for v in values) or np.linalg.norm(r['rotation_xyzw']) < 1e-8:
            raise ValueError('Invalid rest SQT for joint '+str(i))
        if len(r['name'].encode('utf-8')) > 53 or not r['name'] or r['name'].startswith('__sample__'):
            # Blender truncates bone names at 63 bytes; reject instead of aliasing hashes.
            raise ValueError('Bone name requires a declarative Blender-name mapping: '+r['name'])
        offset = sections['0x15DF9D3B']['offset'] + i*16
        r['raw_joint_record_hex'] = data[offset:offset+16].hex()
        r['unknown_record_fields'] = list(struct.unpack_from('<HH',data,offset+2)) + [struct.unpack_from('<I',data,offset+8)[0]]
    transforms = sections.get('0xDCC88A19')
    start = ((len(rows)*48+63)//64)*64
    inverses, errors = [], {}
    if rows and transforms['size'] >= start + len(rows)*64:
        for i in range(len(rows)):
            vals = struct.unpack_from('<16f',data,transforms['offset']+start+i*64)
            m = np.asarray(vals).reshape(4,4).T
            if not np.isfinite(m).all() or not np.allclose(m[3],[0,0,0,1],atol=1e-5):
                inverses = []; break
            inverses.append(m)
        if inverses:
            for rule in ('full','segment_scale_compensation_bit0'):
                try:
                    errors[rule] = max(float(np.max(np.abs(np.linalg.inv(m)-inv))) for m,inv in zip(worlds(rows,inheritance=rule),inverses))
                except np.linalg.LinAlgError:
                    errors[rule] = None
    candidates = [rule for rule,error in errors.items() if error is not None and error < .002]
    profile = dict(schema='ps4-model-profile-v1',model_sha256=sha(data),
        coordinate_units='Unchanged source numeric units; physical unit scale is not encoded by the supported fields',
        source_to_blender_units=1.0, inheritance_candidates=candidates,
        inheritance=candidates[0] if len(candidates)==1 else None,
        inverse_bind_max_errors=errors, classification_overrides={},
        collapsed_rotation_policy='source_rest_rotation_only_when_source_scale_is_zero',
        unsupported_fields=['Physical length unit','Other joint flag semantics','Runtime layer blend partners and weights'],
        geometry=dict(status='native_ps4_geometry_supported',
            reason='Direct PS4 DAT1 positions, 4/8-byte UV0 records, indices, subset/material records, observed opaque mesh flags, four-slot look/LOD records, and 1/2/4/8-influence 0xDCA379A2 skin batches are decoded with source-hash and bounds checks. The second UV pair and packed vertex word are retained raw because their runtime meanings are not evidenced.'))
    if override:
        if override.get('model_sha256') != sha(data):
            raise ValueError('Profile hash does not match selected source model')
        if override.get('inheritance') not in (None,'full','segment_scale_compensation_bit0'):
            raise ValueError('Unsupported profile inheritance rule')
        if override.get('source_to_blender_units',1) != 1:
            raise ValueError('This adapter preserves source numeric units; profile unit conversion is not implemented')
        profile.update(override)
    layout=(dict(sqt_offset=transforms['offset'],sqt_stride=48,sqt_count=len(rows),
            inverse_bind_candidate_relative_offset=start,inverse_bind_count=len(inverses),
            padding_hex=data[transforms['offset']+len(rows)*48:transforms['offset']+start].hex(),
            unknown_tail_hex=data[transforms['offset']+start+len(inverses)*64:transforms['offset']+transforms['size']].hex())
            if transforms else dict(status='static_model_no_joint_sections',sqt_count=0,inverse_bind_count=0))
    return dict(skeleton=rows,joint_count=len(rows),model_sha256=sha(data),sections=root['sections'],
        transform_layout=layout,
        inverse_bind_matrices=[m.tolist() for m in inverses],profile=profile)
