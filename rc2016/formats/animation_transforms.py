"""Independently reconstructed PS4 S/Q/T sampler, validated first on Clank packs.

Quaternion values are homogeneous coordinates: add integer channel values,
then normalize. Scale and translation use their own per-joint binary exponents.
Collapsed-transform rotation sentinels are exposed, never normalized as poses.
"""
from __future__ import annotations
import math
import struct
from pathlib import Path
from rc2016.formats.container import read_container, inspect, game_crc32, section_table
from rc2016.formats.animation_samples import decode_clip, require


def model_skeleton(path: Path | bytes):
    """Joint rows of a decoded model (a path, or the model bytes)."""
    data = bytes(path) if isinstance(path, (bytes, bytearray, memoryview)) else path.read_bytes()
    sections = {int(s['label_hash'], 16): s for s in section_table(data, 0)}
    joints, transforms = sections[0x15DF9D3B], sections[0xDCC88A19]
    require(joints['size'] % 16 == 0, 'misaligned model joint section')
    count = joints['size'] // 16
    require(transforms['size'] >= count * 48, 'truncated model transform section')
    rows = []
    for index in range(count):
        offset = joints['offset'] + index * 16
        parent = struct.unpack_from('<h', data, offset)[0]
        flags = struct.unpack_from('<H', data, offset + 6)[0]
        ptr = struct.unpack_from('<I', data, offset + 12)[0]
        require(0 <= ptr < len(data), 'invalid bone name pointer')
        end = data.find(b'\0', ptr)   # NUL-terminated; no copy of the rest of the file per bone
        name = data[ptr:end if end >= 0 else len(data)].decode('ascii')
        values = struct.unpack_from('<12f', data, transforms['offset'] + 48*index)
        require(-1 <= parent < index, 'model parent is not topologically ordered')
        rows.append(dict(name=name, hash=f'0x{game_crc32(name):08X}', parent=parent, flags=flags,
                         scale=list(values[:3]), rotation_xyzw=list(values[4:8]),
                         translation=list(values[8:11])))
    require(len({r['hash'] for r in rows}) == count, 'duplicate model joint hash')
    return rows


def embedded_clips(path: Path, bones, prefixes=()):
    data = path.read_bytes()
    names = {int(b['hash'], 16): b['name'] for b in bones}
    audit = inspect(path)
    require(not audit['errors'], str(audit['errors']))
    clips = []
    for c in audit['containers']:
        if not c['metadata']:
            continue
        if prefixes and not (c['name'] or '').startswith(tuple(prefixes)):
            continue
        require(c['metadata'].get('name_hash_matches_name'), 'clip name hash mismatch')
        clips.append(decode_clip(data, c['offset'], names))
    return clips


def packed_scales(clip):
    """Each 16-byte pair: sx0,sx1,sy0,sy1,sz0,sz1,destination0,destination1.

Four joints per 32-byte block, with exact duplicate last records for padding.
Unlisted scales default to one. Negative packed scales are not yet validated.
"""
    tail = bytes.fromhex(clip['base_tail_hex'])
    require(len(tail) == clip['metadata']['unknown_u32_28'] * 32, 'base scale block count mismatch')
    rows = clip['joints']
    scales = [[2**r['packed_base_control_bytes'][0]] * 3 for r in rows]
    seen = {}
    for pos in range(0, len(tail), 16):
        pair = struct.unpack_from('<8H', tail, pos)
        require(all(v < 32768 for v in pair[:6]), 'signed/high-bit packed scales not validated')
        for side in range(2):
            destination = pair[6 + side]
            require(destination % 48 == 0 and destination // 48 < len(rows), 'invalid scale destination')
            joint = destination // 48
            value = [pair[side + 2*axis] for axis in range(3)]
            require(joint not in seen or seen[joint] == value, 'conflicting duplicate scale records')
            seen[joint] = value
            scales[joint] = value
    return scales


def _sample_transforms_loop(clip):
    """Reference per-frame/per-joint loop; also the exact error source for malformed clips."""
    scales = packed_scales(clip)
    base = clip['joints']
    frames = []
    for frame in range(clip['metadata']['original_frame_count']):
        raw = [s.copy() + r['packed_base_i16'][:4] + r['packed_base_i16'][4:7]
               for s, r in zip(scales, base)]
        # Runtime records have padding after S and T; quaternion occupies 16..28.
        for channel in clip['channels']:
            offset = channel['component_byte_offset']
            require(offset in (0, 4, 8, 16, 20, 24, 28, 32, 36, 40), 'unknown SQT component')
            component = offset // 4 if offset < 12 else offset // 4 - 1
            raw[channel['joint_index']][component] += channel['unsigned_deltas'][frame]
        result = []
        for row, values in zip(base, raw):
            scale_exp, translation_exp = row['packed_base_control_bytes']
            require(scale_exp <= 30 and translation_exp <= 30, 'unsupported transform exponent')
            scale = [math.ldexp(v, -scale_exp) for v in values[:3]]
            q = values[3:7]
            sentinel = q == [-32768] * 4
            require(not sentinel or min(scale) == 0, 'rotation sentinel on noncollapsed joint')
            length = math.sqrt(sum(v*v for v in q))
            require(length > 0, 'zero quaternion')
            result.append(dict(name=row['name'], hash=row['hash'], scale=scale,
                               rotation_xyzw=None if sentinel else [v/length for v in q],
                               rotation_sentinel=sentinel,
                               translation=[math.ldexp(v, -translation_exp) for v in values[7:]]))
        frames.append(result)
    return frames


_COMPONENTS = (0, 4, 8, 16, 20, 24, 28, 32, 36, 40)   # S xyz, padding, Q xyzw, padding, T xyz byte offsets


def _sample_transforms_fast(clip):
    """Batched numpy sampler (F, J, 10 integer channels); None when the data needs the exact loop."""
    import numpy as np
    base, frames_n = clip['joints'], clip['metadata']['original_frame_count']
    if not frames_n or not base:
        return None
    exps = np.array([r['packed_base_control_bytes'] for r in base], np.int64).reshape(-1, 2)
    if (exps > 30).any() or (exps < 0).any():
        return None
    scales = packed_scales(clip)
    raw = np.empty((frames_n, len(base), 10), np.int64)
    raw[:, :, :3] = np.array(scales, np.int64).reshape(-1, 3)[None]
    raw[:, :, 3:] = np.array([r['packed_base_i16'][:7] for r in base], np.int64).reshape(-1, 7)[None]
    for channel in clip['channels']:
        offset = channel['component_byte_offset']
        require(offset in _COMPONENTS, 'unknown SQT component')
        component = offset // 4 if offset < 12 else offset // 4 - 1
        deltas = channel['unsigned_deltas']
        if len(deltas) < frames_n:
            return None
        raw[:, channel['joint_index'], component] += np.asarray(deltas[:frames_n], np.int64)
    q = raw[:, :, 3:7]
    sentinel = (q == -32768).all(axis=2)
    length = np.sqrt((q * q).sum(axis=2).astype(np.float64))
    scale = np.ldexp(raw[:, :, :3].astype(np.float64), -exps[None, :, :1].astype(np.int32))
    if (length <= 0).any() or (sentinel & (scale.min(axis=2) != 0)).any():
        return None
    rotation = q / length[:, :, None]
    translation = np.ldexp(raw[:, :, 7:].astype(np.float64), -exps[None, :, 1:].astype(np.int32))
    return scale.tolist(), rotation.tolist(), sentinel.tolist(), translation.tolist()


def sample_transforms(clip):
    try:
        fast = _sample_transforms_fast(clip)
    except (ImportError, OverflowError, TypeError, ValueError, IndexError, KeyError):
        fast = None   # e.g. no numpy, huge deltas, malformed rows: the loop reproduces the exact outcome
    if fast is None:
        return _sample_transforms_loop(clip)
    scale, rotation, sentinel, translation = fast
    base = clip['joints']
    frames = []
    for f in range(len(scale)):
        frames.append([dict(name=row['name'], hash=row['hash'], scale=scale[f][j],
                            rotation_xyzw=None if sentinel[f][j] else rotation[f][j],
                            rotation_sentinel=sentinel[f][j], translation=translation[f][j])
                       for j, row in enumerate(base)])
    return frames


def main():
    """Model-independent extraction; Blender scene construction is separate."""
    import argparse
    import hashlib
    import json
    import re
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('animset',type=Path)
    parser.add_argument('--model',type=Path,required=True,help='the corresponding original model')
    parser.add_argument('--prefix',action='append',default=[],help='optional clip-name prefix; repeatable')
    parser.add_argument('--out',type=Path,required=True,help='new output directory')
    args=parser.parse_args()
    bones=model_skeleton(args.model)
    clips=embedded_clips(args.animset,bones,args.prefix)
    require(bool(clips),'no matching embedded clips')
    decoded=[sample_transforms(c) for c in clips]
    data=args.animset.read_bytes()
    args.out.mkdir(parents=True,exist_ok=False)
    manifest={'schema':'ps4-v15-source-sqt-v1','animset':str(args.animset),'model':str(args.model),
              'animset_sha256':hashlib.sha256(data).hexdigest(),
              'model_sha256':hashlib.sha256(args.model.read_bytes()).hexdigest(),
              'skeleton':bones,'rotation_order':'xyzw',
              'notes':['Source sample times and source units are preserved.',
                       'A null quaternion explicitly marks a collapsed-rotation sentinel.',
                       'Model flags are raw; their scale-inheritance interpretation needs per-rig validation.',
                       'No Blender or presentation transforms are included.'], 'clips':[]}
    for index,(clip,frames) in enumerate(zip(clips,decoded)):
        stem=f"{index:03d}_"+re.sub(r'[^A-Za-z0-9_-]','_',clip['name'])[:100]
        output={'name':clip['name'],'metadata':clip['metadata'],'times_seconds':clip['sample_times_seconds'],
                'joints':[{'name':r['name'],'hash':r['hash']} for r in clip['joints']],
                'frame_component_order':['scale_xyz','rotation_xyzw_or_null','translation_xyz'],
                'frames':[[[r['scale'],r['rotation_xyzw'],r['translation']] for r in frame] for frame in frames]}
        (args.out/(stem+'.json')).write_text(json.dumps(output,separators=(',',':'))+'\n',encoding='utf-8')
        blob=data[clip['source_offset']:clip['source_offset']+clip['source_size']]
        (args.out/(stem+'.animclip')).write_bytes(blob)
        manifest['clips'].append({'name':clip['name'],'json':stem+'.json','raw_clip':stem+'.animclip',
                                  'raw_sha256':hashlib.sha256(blob).hexdigest(),'frame_count':len(frames),
                                  'matched_model_joints':sum(r['name'] is not None for r in clip['joints'])})
    (args.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(f"Extracted {len(clips)} source SQT clips to {args.out}")


if __name__=='__main__':
    main()
