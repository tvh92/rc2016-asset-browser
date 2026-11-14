"""Rift Apart (PC) ``.animclip`` skeletal clips, in the dict schema of ``formats.animation_samples.decode_clip``.

A clip is its own asset: the 36-byte toc header, then a DAT1 of type 0x0F64FFE8 at +36 with the same
section ids as an R&C 2016 clip. Base records, the base scale tail, the 48-byte destination layout and
the frame-major bit packing are the 2016 version-15 layout, so ``animation_transforms.sample_transforms``
turns the result into scale, rotation and translation per joint (frame 0 of a clip matches the model's
rest SQT within quantization; docs/rift-apart.md). Four things differ from 2016:

  0x9DF23F77 built      +0 u32 is the clip name's string offset, not a format version; +4 name hash
                        (game CRC-32 of the name); +8 flags (bit 0x40: additive, bit 0x10000: frame
                        table); +12 f32 fps; +16 f32 seconds; +32 u16 joints, u16 frames, u16 elements,
                        u16 0; +40 u32 bytes per stored sample; +44 u16 base scale-tail blocks, u16
                        count of 4-byte records after the tail (u16 joint, u16 value; seen once, on an
                        additive clip, and not read); +48 u16 stored samples
  0xD070D358 elements   u32 destination per element (not u16), then u8 bit width - 1 each, zero-padded
                        to a 4-byte multiple
  0x3A7B4855 samples    with flag 0x10000: round(seconds * fps) + 1 u16 stored-sample indices (one per
                        frame, plus a closing one when the frame count leaves out the end pose),
                        zero-padded to 16 bytes, then the stored samples (a clip that repeats poses
                        stores each once); without it, frame i is stored sample i
  0xA3B26640 joints     joint hashes (game CRC-32 of the joint names) with an 0xFFFFFFFF terminator

Clips without joint tracks (only custom float tracks, sections 0x14014CB6/0x116EB684) are not skeletal and
raise ``UnsupportedClipError``. Root motion (0x3976E44C, 0xE08AA35F), triggers, morph and face sections
are not read.
"""
from __future__ import annotations

import hashlib
import math
import struct

import numpy as np

from rc2016.games.rcra.toc import sections as dat1_sections

CLIP_MAGIC = 0x0F64FFE8
HEADER = 36
BUILT, BASE, ELEMENTS, SAMPLES, JOINT_HASHES = 0x9DF23F77, 0x2BB5BC8F, 0xD070D358, 0x3A7B4855, 0xA3B26640
CUSTOM_TRACKS = 0x14014CB6
FLAG_ADDITIVE, FLAG_FRAME_TABLE = 0x40, 0x10000
# Runtime S/Q/T byte offsets inside a 48-byte joint record (scale xyz, quaternion xyzw, translation xyz).
COMPONENTS = frozenset((0, 4, 8, 16, 20, 24, 28, 32, 36, 40))
SCHEMA = 'rcra-pc-animclip-v1'


class UnsupportedClipError(ValueError):
    """The asset is a clip this decoder does not read (no joint tracks, or not an animclip)."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _align(value, step):
    return (value + step - 1) // step * step


def clip_dat1(data):
    """(DAT1 bytes, {tag: (offset, size)}) of a clip asset (toc header + DAT1)."""
    if len(data) < HEADER + 16 or struct.unpack_from('<I', data, 0)[0] != CLIP_MAGIC:
        raise UnsupportedClipError('Not a Rift Apart animclip (expected magic 0x%08X)' % CLIP_MAGIC)
    dat1 = data[HEADER:]
    if dat1[:4] != b'1TAD' or struct.unpack_from('<I', dat1, 4)[0] != CLIP_MAGIC:
        raise UnsupportedClipError('Rift Apart animclip without its DAT1 at +36')
    return dat1, dat1_sections(dat1)


def _name(dat1, table, name_hash):
    """The clip's own name: the NUL-terminated string after 'animclip built' in the string pool."""
    from rc2016.formats.container import game_crc32
    first = min((offset for offset, _size in table.values()), default=len(dat1))
    pool = dat1[16 + 12 * len(table):first]
    marker = pool.find(b'animclip built\0')
    if marker < 0:
        return None
    start = marker + len(b'animclip built\0')
    end = pool.find(b'\0', start)
    try:
        text = pool[start:end if end >= 0 else len(pool)].decode('ascii')
    except UnicodeDecodeError:
        return None
    return text if text and game_crc32(text) == name_hash else None


def header(data):
    """The built-header facts of a clip asset, without decoding samples (cheap: one section read)."""
    dat1, table = clip_dat1(data)
    require(BUILT in table, 'animclip without its built section 0x9DF23F77')
    offset, size = table[BUILT]
    require(size >= 52, 'truncated animclip built section')
    name_hash, flags, fps, seconds = struct.unpack_from('<IIff', dat1, offset + 4)
    joints, frames, elements, _zero = struct.unpack_from('<4H', dat1, offset + 32)
    stride, tail_blocks, tail_records, stored = struct.unpack_from('<IHHH', dat1, offset + 40)
    return dict(name=_name(dat1, table, name_hash), name_hash=f'0x{name_hash:08X}', flags=flags,
                additive_flag=bool(flags & FLAG_ADDITIVE), sample_rate_fps=fps, duration_seconds=seconds,
                bone_count=joints, original_frame_count=frames, sample_element_count=elements,
                sample_stride_bytes=stride, unknown_u32_28=tail_blocks, base_tail_blocks=tail_blocks,
                base_tail_records=tail_records, stored_sample_count=stored, frame_table=bool(flags & FLAG_FRAME_TABLE),
                skeletal=BASE in table and JOINT_HASHES in table)


def _unpack(payload, stride, widths, rows):
    """Unsigned values (len(widths), rows) of frame-major LSB-first bit-packed samples; checks zero padding."""
    if not widths or not rows:
        return np.zeros((len(widths), rows), np.int64)
    raw = np.frombuffer(payload, np.uint8, rows * stride).reshape(rows, stride)
    bits = np.unpackbits(raw, axis=1, bitorder='little')
    used = int(sum(widths))
    require(not bits[:, used:].any(), 'nonzero padding bits in a stored sample')
    out = np.empty((len(widths), rows), np.int64)
    position = 0
    for channel, width in enumerate(widths):
        weights = (1 << np.arange(width, dtype=np.int64))
        out[channel] = bits[:, position:position + width].astype(np.int64) @ weights
        position += width
    return out


def decode_clip(data, names=None, first_frames=None, label=''):
    """One Rift Apart clip asset as ``animation_samples.decode_clip`` returns a 2016 clip.

    ``names`` maps joint hash (int) to joint name. ``first_frames`` decodes only that many leading frames
    (reported as ``original_frame_count``) with the same checks. The output feeds
    ``animation_transforms.sample_transforms`` unchanged."""
    names = names or {}
    dat1, table = clip_dat1(data)
    meta = header(data)
    if not meta['skeletal']:
        kind = 'custom float tracks only' if CUSTOM_TRACKS in table else 'no joint base state'
        raise UnsupportedClipError(f'Not a skeletal clip ({kind})')

    def chunk(tag):
        if tag not in table:
            return b''
        offset, size = table[tag]
        return dat1[offset:offset + size]

    count = meta['bone_count']
    fps, seconds, frames = meta['sample_rate_fps'], meta['duration_seconds'], meta['original_frame_count']
    require(math.isfinite(fps) and fps > 0 and math.isfinite(seconds) and seconds >= 0, 'invalid clip timing')
    require(frames > 0, 'clip has no frames')
    span = round(fps * seconds)
    require(frames in (span, span + 1), 'frame count does not match the clip timing')
    hashes = chunk(JOINT_HASHES)
    require(len(hashes) == 4 * (count + 1) and hashes[-4:] == b'\xff' * 4, 'joint count/terminator mismatch')
    joint_hashes = struct.unpack(f'<{count}I', hashes[:4 * count])
    require(len(set(joint_hashes)) == count, 'duplicate joint hashes')
    base = chunk(BASE)
    tail_blocks = meta['base_tail_blocks']
    require(len(base) == 16 * count + 32 * tail_blocks + 4 * meta['base_tail_records'],
            'base state size does not match joints and scale tail')
    n = meta['sample_element_count']
    stride = meta['sample_stride_bytes']
    stored = meta['stored_sample_count']
    elements, payload = chunk(ELEMENTS), chunk(SAMPLES)
    if n:
        require(len(elements) == _align(5 * n, 4) and not any(elements[5 * n:]), 'sample element table size mismatch')
        destinations = struct.unpack_from(f'<{n}I', elements, 0)
        widths = [code + 1 for code in elements[4 * n:5 * n]]
    else:
        require(not elements and not stride, 'malformed empty element table')
        destinations, widths = (), []
    require(all(1 <= w <= 16 for w in widths), 'unsupported integer bit width')
    require(len(set(destinations)) == len(destinations), 'duplicate component destinations')
    require(all(d % 48 in COMPONENTS and d < count * 48 for d in destinations), 'destination outside the joint records')
    require(stride == (sum(widths) + 31) // 32 * 4, 'stored sample stride does not match bit widths')
    if meta['frame_table']:
        entries = span + 1
        table_bytes = _align(2 * entries, 16)
        require(stored >= 1 and len(payload) == table_bytes + stored * stride, 'sample payload size mismatch')
        order = np.frombuffer(payload, '<u2', entries).astype(np.intp)
        require(not any(payload[2 * entries:table_bytes]), 'nonzero frame table padding')
        require(int(order.max()) < stored, 'frame table points past the stored samples')
        order = order[:frames]   # a closing entry repeats a pose (the first, on a loop); the header counts frames
        samples = payload[table_bytes:]
    else:
        # A clip without a frame table stores one sample per frame, sometimes one more (the closing pose).
        require(stored in (frames, frames + 1) or (not n and not payload), 'stored sample count does not match frames')
        require(len(payload) == stored * stride, 'sample payload size mismatch')
        order = np.arange(frames, dtype=np.intp)
        samples = payload
    if first_frames is not None:
        frames = max(1, min(frames, first_frames))
        order = order[:frames]
    values = _unpack(samples, stride, widths, stored if n else 0)[:, order] if n else np.zeros((0, frames), np.int64)
    channels = []
    for index, destination in enumerate(destinations):
        joint, component = divmod(destination, 48)
        channels.append(dict(joint_index=joint, hash=f'0x{joint_hashes[joint]:08X}', name=names.get(joint_hashes[joint]),
                             destination_byte_offset=destination, component_byte_offset=component,
                             bit_width=widths[index], unsigned_deltas=values[index].tolist()))
    metadata = dict(meta, format=SCHEMA, original_frame_count=frames)
    return {
        'name': meta['name'] or label, 'source_offset': 0, 'source_size': len(data), 'metadata': metadata,
        'used_bits_per_frame': int(sum(widths)), 'sample_times_seconds': [i / fps for i in range(frames)],
        'padding_bits_per_frame': stride * 8 - int(sum(widths)),
        'sample_sha256': hashlib.sha256(payload).hexdigest(), 'sample_roundtrip_exact': True,
        'joints': [dict(joint_index=i, hash=f'0x{h:08X}', name=names.get(h),
                        packed_base_i16=list(struct.unpack_from('<7h', base, 16 * i)),
                        packed_base_control_bytes=list(base[16 * i + 14:16 * i + 16]))
                   for i, h in enumerate(joint_hashes)],
        'base_tail_hex': base[16 * count:16 * count + 32 * tail_blocks].hex(),
        'channels': channels,
    }
