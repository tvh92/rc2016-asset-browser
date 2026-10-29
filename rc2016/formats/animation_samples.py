"""Lossless PS4 v15 animation sample extraction; NOT a Blender motion importer.

Decode frame-major unsigned integer deltas, retaining packed base records and
runtime component offsets. No unvalidated rotation/translation conversion is
performed. Only Python's standard library is required.

python rc2016/formats/animation_samples.py file.animset --bones names.txt --out new_directory
python rc2016/formats/animation_samples.py --validate-tree . --report validation.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from pathlib import Path

from rc2016.formats.container import read_container, game_crc32, inspect


def require(condition, message):
    if not condition:
        raise ValueError(message)


# Built-header word +0. Version 21 (real-time cutscene clips) uses the version-15 sample layout: the
# hero_ratchet.animset clip pickup_predator_launcher_hero_ratchet (15) and its twin in
# predator_pickup_cine.cinematic2 (21) have byte-identical base state, joint hashes, sample elements and
# sample data and decode to identical frames; their built headers differ only in this word, the name hash
# and +0x08 bit 0x80000. Every structural check below still applies to both.
# Version 143 (the Mrs. Zurkon boss animset's Engaged_* and React_Hit clips) has the same header fields and
# passes every structural check below (stream widths, frame stride, destinations, payload size) with the
# version-15 layout; its Engaged_Idle loops exactly (first and last frame identical) and frame 0 of every
# clip matches the rest pose for the joints it does not move. No version-15 twin exists to compare bytes.
SUPPORTED_CLIP_VERSIONS = (15, 21, 143)


def decode_clip(data: bytes, start: int, names: dict[int, str], first_frames: int | None = None) -> dict:
    """One clip's samples and joints. ``first_frames`` decodes only that many leading frames (and reports that
    count as ``original_frame_count``), with the same checks on them, for callers that need the start of a clip."""
    clip = read_container(data, start, names)
    metadata = clip['metadata']
    require(metadata is not None, 'lookup target is not an animation clip')
    require(metadata['format_version'] in SUPPORTED_CLIP_VERSIONS, 'only PS4 clip versions 15, 21 and 143 are validated')
    sections = {s['type_name']: data[s['offset']:s['offset'] + s['size']]
                for s in clip['sections'] if s['type_name']}
    for key in ('anim_clip_base_state', 'anim_clip_joint_hashes'):
        require(key in sections, f'missing {key}')
    if metadata['sample_element_count'] == 0 and metadata['sample_stride_bytes'] == 0:
        sections.setdefault('anim_clip_sample_data', b'')
    else:
        for key in ('anim_clip_sample_elements', 'anim_clip_sample_data'):
            require(key in sections, f'missing {key}')
    count = metadata['bone_count']
    hashes = sections['anim_clip_joint_hashes']
    require(len(hashes) == 4 * (count + 1) and hashes[-4:] == b'\xff'*4,
            'joint count/terminator mismatch')
    require(len({b['hash'] for b in clip['bone_hashes']}) == count, 'duplicate joint hashes')
    base = sections['anim_clip_base_state']
    require(len(base) >= count * 16, 'truncated base records')
    directory = clip['stream_directory']
    if directory is None and metadata['sample_element_count'] == 0:
        require('anim_clip_sample_elements' not in sections, 'malformed empty element table')
        directory = {'entries': []}
    require(directory is not None, 'invalid sample element table')
    entries = directory['entries']
    require(len(entries) == metadata['sample_element_count'], 'sample element count mismatch')
    widths = [e['bit_width'] for e in entries]
    require(all(1 <= w <= 16 for w in widths), 'unsupported integer bit width')
    offsets = [e['raw_offset_word'] for e in entries]
    require(len(set(offsets)) == len(offsets), 'duplicate component destinations')
    require(all(o % 4 == 0 and o < count * 48 for o in offsets),
            'destination outside 48-byte joint records')
    stride = metadata['sample_stride_bytes']
    used_bits = sum(widths)
    require(stride == (used_bits + 31) // 32 * 4, 'frame stride does not match bit widths')
    frames = metadata['original_frame_count']
    fps, duration = metadata['sample_rate_fps'], metadata['duration_seconds']
    require(math.isfinite(fps) and fps > 0 and math.isfinite(duration) and duration >= 0,
            'invalid timing')
    # Looping sources also use an exclusive endpoint; do not synthesize a frame.
    require(frames > 0 and frames in (round(fps * duration), round(fps * duration) + 1),
            'frame timing mismatch')
    payload = sections['anim_clip_sample_data']
    require(len(payload) == frames * stride, 'sample payload size mismatch')
    if first_frames is not None:
        frames = min(frames, first_frames)
        payload = payload[:frames * stride]
        metadata = dict(metadata, original_frame_count=frames)
    values = [[] for _ in entries]
    rebuilt = bytearray()
    for frame in range(frames):
        packed = int.from_bytes(payload[frame*stride:(frame+1)*stride], 'little')
        require(packed >> used_bits == 0, f'nonzero padding in frame {frame}')
        bit = 0
        encoded = 0
        for channel, width in enumerate(widths):
            value = (packed >> bit) & ((1 << width) - 1)
            values[channel].append(value)
            encoded |= value << bit
            bit += width
        rebuilt.extend(encoded.to_bytes(stride, 'little'))
    require(bytes(rebuilt) == payload, 'sample re-encoding differs from source')
    channels = []
    for index, entry in enumerate(entries):
        joint, component = divmod(entry['raw_offset_word'], 48)
        channels.append({
            'joint_index': joint, **clip['bone_hashes'][joint],
            'destination_byte_offset': entry['raw_offset_word'],
            'component_byte_offset': component,
            'bit_width': widths[index], 'unsigned_deltas': values[index],
        })
    return {
        'name': clip['name'], 'source_offset': start, 'source_size': clip['declared_size'],
        'metadata': metadata, 'used_bits_per_frame': used_bits,
        'sample_times_seconds': [i / fps for i in range(frames)],
        'padding_bits_per_frame': stride * 8 - used_bits,
        'sample_sha256': hashlib.sha256(payload).hexdigest(),
        'sample_roundtrip_exact': True,
        'joints': [dict(joint_index=i, **bone,
                        packed_base_i16=list(struct.unpack_from('<7h', base, i*16)),
                        packed_base_control_bytes=list(base[i*16+14:i*16+16]))
                   for i, bone in enumerate(clip['bone_hashes'])],
        'base_tail_hex': base[count*16:].hex(),
        'channels': channels,
    }


def extract(path: Path, bone_names=()) -> tuple[dict, bytes]:
    data = path.read_bytes()
    names = {game_crc32(name): name for name in bone_names if name}
    root = read_container(data, 0, names)
    require(root['declared_size'] == len(data), 'trailing bytes outside root container')
    by_name = {s['type_name']: s for s in root['sections'] if s['type_name']}
    require('anim_clip_lookup' in by_name and 'anim_clip_data' in by_name,
            'animset has no clip lookup/data sections')
    lookup, section = by_name['anim_clip_lookup'], by_name['anim_clip_data']
    require(lookup['size'] % 16 == 0, 'misaligned clip lookup')
    clips, spans = [], []
    for pos in range(lookup['offset'], lookup['offset'] + lookup['size'], 16):
        name_hash, offset = struct.unpack_from('<II', data, pos)
        start = section['offset'] + offset
        require(section['offset'] <= start < section['offset'] + section['size'],
                'clip lookup points outside data section')
        clip = decode_clip(data, start, names)
        end = start + clip['source_size']
        require(end <= section['offset'] + section['size'], 'clip extends outside data section')
        require(all(end <= a or start >= b for a, b in spans), 'overlapping/duplicate clips')
        spans.append((start, end))
        require(clip['metadata']['name_hash'] == f'0x{name_hash:08X}', 'lookup/name hash mismatch')
        require(clip['metadata'].get('name_hash_matches_name', False), 'unresolved clip name/hash')
        clips.append(clip)
    return {
        'schema': 'ps4-animation-integer-deltas-v1',
        'status': 'integer_samples_decoded_transform_conversion_unvalidated',
        'source': str(path), 'source_sha256': hashlib.sha256(data).hexdigest(),
        'component_semantics': 'Runtime offsets only; rotation/translation/scale layout unvalidated.',
        'base_combination': 'Packed base conversion and delta scaling remain unvalidated.',
        'clips': clips,
    }, data


def write_bundle(report: dict, data: bytes, output: Path):
    # All decoding and validation finish before creating an output directory.
    output.mkdir(parents=True, exist_ok=False)
    for index, clip in enumerate(report['clips']):
        name = re.sub(r'[^A-Za-z0-9_-]', '_', clip['name'])[:100]
        filename = f'{index:03d}_{name}.animclip'
        start = clip['source_offset']
        blob = data[start:start + clip['source_size']]
        (output / filename).write_bytes(blob)
        require((output / filename).read_bytes() == blob, 'clip output verification failed')
        clip['raw_clip_file'] = filename
        clip['raw_clip_sha256'] = hashlib.sha256(blob).hexdigest()
    (output / 'integer_deltas.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('animset', type=Path, nargs='?')
    parser.add_argument('--bones', type=Path)
    parser.add_argument('--out', type=Path, help='new output directory; existing directories refused')
    parser.add_argument('--validate-tree', type=Path, help='read-only validation of every local animset')
    parser.add_argument('--report', type=Path, help='validation report destination')
    args = parser.parse_args()
    bones = args.bones.read_text(encoding='utf-8').splitlines() if args.bones else []
    if args.validate_tree:
        if args.animset or args.out:
            parser.error('--validate-tree cannot be combined with animset/--out')
        rows, errors = [], []
        for path in sorted(args.validate_tree.rglob('*.animset')):
            try:
                data = path.read_bytes()
                audit = inspect(path, bones)
                require(not audit['errors'], str(audit['errors']))
                names = {game_crc32(name): name for name in bones if name}
                clips = [decode_clip(data, c['offset'], names) for c in audit['containers']
                         if c['metadata'] is not None]
                rows.append({'source': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                             'scope': 'embedded clip decoding; root lookup export not tested',
                             'clips': len(clips),
                             'sampled_clips': sum(bool(c['channels']) for c in clips),
                             'constant_clips': sum(not c['channels'] for c in clips),
                             'frames': sum(c['metadata']['original_frame_count'] for c in clips),
                             'all_samples_roundtrip_exact': True})
            except (ValueError, OSError, struct.error) as error:
                errors.append({'source': str(path), 'error': str(error)})
        result = {'files': rows, 'errors': errors, 'file_count': len(rows),
                  'clip_count': sum(r['clips'] for r in rows),
                  'sampled_clip_count': sum(r['sampled_clips'] for r in rows),
                  'constant_clip_count': sum(r['constant_clips'] for r in rows),
                  'frame_count': sum(r['frames'] for r in rows)}
        if args.report:
            args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2))
        raise SystemExit(1 if errors or not rows else 0)
    if not args.animset or not args.out:
        parser.error('provide animset and --out, or --validate-tree')
    report, data = extract(args.animset, bones)
    write_bundle(report, data, args.out)
    print(f"Extracted {len(report['clips'])} clips to {args.out}; integer deltas only, not Blender transforms.")


if __name__ == '__main__':
    main()
