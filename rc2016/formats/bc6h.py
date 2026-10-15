"""Unsigned BC6H (DXGI 95) decoder to linear float32, for the block modes the game's probes use.

Pillow decodes BC6H to 8 bits and clamps it to [0, 1], which loses every value above 1 (sun,
bright cloud). This decoder keeps the full half-float range. It implements the single-subset
modes 11 to 14 and the two-subset mode with the 2-bit field ``01`` (the only two-subset mode the
game's reflection probes use). A block in any other mode is left black and reported in the returned
mask, so the caller decides how to fill it (``probe_lighting`` uses Pillow's clamped value).

Modes follow the Direct3D 11 functional specification (mode field: 2 bits for modes 1 and 2,
otherwise 5 bits, read little-endian from the low end of the 128-bit block):

==========  ==========  =========  ===========================================
mode        field       endpoint   status
==========  ==========  =========  ===========================================
2           0b01        7 bits     decoded (two subsets), checked against Pillow
11          0b00011     10 bits    decoded, checked against Pillow
12          0b00111     11 bits    decoded, checked against Pillow
13          0b01011     12 bits    decoded, checked against Pillow
14          0b01111     16 bits    decoded, checked against Pillow
others                             reported unsupported (the other two-subset modes)
==========  ==========  =========  ===========================================
"""
from __future__ import annotations

WEIGHTS_3 = (0, 9, 18, 27, 37, 46, 55, 64)
WEIGHTS_4 = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)
# Two-subset partitions 0-31: row-major subset id of each pixel, with 128/129 marking the anchors
# (pixel 0 for subset 0, the "fix-up" pixel for subset 1), whose index has one bit fewer.
PARTITIONS = (
    (128, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 129), (128, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 129),
    (128, 1, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1, 1, 129), (128, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 1, 1, 129),
    (128, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 129), (128, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 129),
    (128, 0, 0, 1, 0, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 129), (128, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 1, 0, 1, 1, 129),
    (128, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 129), (128, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 129),
    (128, 0, 0, 0, 0, 0, 0, 1, 0, 1, 1, 1, 1, 1, 1, 129), (128, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 1, 129),
    (128, 0, 0, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 129), (128, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 129),
    (128, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 129), (128, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 129),
    (128, 0, 0, 0, 1, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 129), (128, 1, 129, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0),
    (128, 0, 0, 0, 0, 0, 0, 0, 129, 0, 0, 0, 1, 1, 1, 0), (128, 1, 129, 1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0),
    (128, 0, 129, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0), (128, 0, 0, 0, 1, 0, 0, 0, 129, 1, 0, 0, 1, 1, 1, 0),
    (128, 0, 0, 0, 0, 0, 0, 0, 129, 0, 0, 0, 1, 1, 0, 0), (128, 1, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 0, 129),
    (128, 0, 129, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0), (128, 0, 0, 0, 1, 0, 0, 0, 129, 0, 0, 0, 1, 1, 0, 0),
    (128, 1, 129, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0), (128, 0, 129, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0),
    (128, 0, 0, 1, 0, 1, 1, 1, 129, 1, 1, 0, 1, 0, 0, 0), (128, 0, 0, 0, 1, 1, 1, 1, 129, 1, 1, 1, 0, 0, 0, 0),
    (128, 1, 129, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0), (128, 0, 129, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0),
)
BLOCK_BYTES = 16


def _unquantize(value, bits):
    if bits >= 15:
        return value
    if value == 0:
        return 0
    if value == (1 << bits) - 1:
        return 0xFFFF
    return ((value << 15) + 0x4000) >> (bits - 1)


def _sign_extend(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def _reverse_bits(value, count):
    return int(format(value, f'0{count}b')[::-1], 2)


def _endpoints(bits, mode):
    """((r0, g0, b0), (r1, g1, b1)) unquantized to 16 bits, or None for an unsupported mode."""
    def field(start, count):
        return (bits >> start) & ((1 << count) - 1)

    if mode == 0b00011:    # mode 11: 10-bit endpoints, stored raw
        e0 = [field(5, 10), field(15, 10), field(25, 10)]
        e1 = [field(35, 10), field(45, 10), field(55, 10)]
        width = 10
    elif mode == 0b00111:  # mode 12: 11-bit base, 9-bit deltas
        e0 = [field(5, 10) | field(44, 1) << 10, field(15, 10) | field(54, 1) << 10,
              field(25, 10) | field(64, 1) << 10]
        delta = [_sign_extend(field(35, 9), 9), _sign_extend(field(45, 9), 9), _sign_extend(field(55, 9), 9)]
        e1 = [(a + d) & 0x7FF for a, d in zip(e0, delta)]
        width = 11
    elif mode == 0b01011:  # mode 13: 12-bit base, 8-bit deltas; the two top base bits are stored reversed
        e0 = [field(5, 10) | _reverse_bits(field(43, 2), 2) << 10,
              field(15, 10) | _reverse_bits(field(53, 2), 2) << 10,
              field(25, 10) | _reverse_bits(field(63, 2), 2) << 10]
        delta = [_sign_extend(field(35, 8), 8), _sign_extend(field(45, 8), 8), _sign_extend(field(55, 8), 8)]
        e1 = [(a + d) & 0xFFF for a, d in zip(e0, delta)]
        width = 12
    elif mode == 0b01111:  # mode 14: 16-bit base, 4-bit deltas; the six top base bits are stored reversed
        e0 = [field(5, 10) | _reverse_bits(field(39, 6), 6) << 10,
              field(15, 10) | _reverse_bits(field(49, 6), 6) << 10,
              field(25, 10) | _reverse_bits(field(59, 6), 6) << 10]
        delta = [_sign_extend(field(35, 4), 4), _sign_extend(field(45, 4), 4), _sign_extend(field(55, 4), 4)]
        e1 = [(a + d) & 0xFFFF for a, d in zip(e0, delta)]
        width = 16
    else:
        return None
    return [_unquantize(v, width) for v in e0], [_unquantize(v, width) for v in e1]


def _two_subset_endpoints(bits):
    """(endpoints, partition) of a block with the 2-bit mode field ``01``: endpoints[subset] is
    ((r0, g0, b0), (r1, g1, b1)) unquantized to 16 bits. 7-bit base, 6-bit deltas."""
    def field(start, count):
        return (bits >> start) & ((1 << count) - 1)

    def bit(position):
        return (bits >> position) & 1

    # Bit order (Direct3D 11 specification): mode(2) gy5 gz4 gz5 rw(7) bz0 bz1 by4 gw(7) by5 bz2 gy4 bw(7) bz3 bz5 bz4
    # rx(6) gy[3:0] gx(6) gz[3:0] bx(6) by[3:0] ry(6) rz(6) partition(5), then 46 bits of indices from bit 82.
    gy = field(41, 4) | bit(24) << 4 | bit(2) << 5
    gz = field(51, 4) | bit(3) << 4 | bit(4) << 5
    bz = bit(12) | bit(13) << 1 | bit(23) << 2 | bit(32) << 3 | bit(34) << 4 | bit(33) << 5
    by = field(61, 4) | bit(14) << 4 | bit(22) << 5
    base = [field(5, 7), field(15, 7), field(25, 7)]
    deltas = [(_sign_extend(field(35, 6), 6), _sign_extend(field(45, 6), 6), _sign_extend(field(55, 6), 6)),    # rx gx bx
              (_sign_extend(field(65, 6), 6), _sign_extend(gy, 6), _sign_extend(by, 6)),                         # ry gy by
              (_sign_extend(field(71, 6), 6), _sign_extend(gz, 6), _sign_extend(bz, 6))]                         # rz gz bz
    points = [[_unquantize(v, 7) for v in base]]
    for delta in deltas:
        points.append([_unquantize((b + d) & 0x7F, 7) for b, d in zip(base, delta)])
    return [(points[0], points[1]), (points[2], points[3])], field(77, 5)


def decode_bc6h(data, width, height):
    """(image, unsupported): float32 (height, width, 3) linear RGB and a bool (height/4, width/4) mask of
    blocks in modes this decoder does not implement (their pixels are zero). ``data`` is the linear
    (not PS4-tiled) block stream, 16 bytes per 4x4 block, rows of blocks top first."""
    import numpy as np
    blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
    count = blocks_x * blocks_y
    if len(data) < count * BLOCK_BYTES:
        raise ValueError(f'BC6H data holds {len(data) // BLOCK_BYTES} blocks, {count} needed')
    ends = np.zeros((count, 2, 2, 3), np.int64)       # [block, subset, endpoint, channel]
    subset = np.zeros((count, 16), np.int64)
    weight = np.zeros((count, 16), np.int64)
    unsupported = np.zeros(count, bool)
    for n in range(count):
        bits = int.from_bytes(data[n * BLOCK_BYTES:(n + 1) * BLOCK_BYTES], 'little')
        mode = bits & 3
        if mode >= 2:
            mode = bits & 31
        if mode == 1:
            pairs, partition = _two_subset_endpoints(bits)
            for k in range(2):
                ends[n, k, 0], ends[n, k, 1] = pairs[k]
            layout = PARTITIONS[partition]
            stream = bits >> 82
            for pixel in range(16):
                anchor = layout[pixel] >= 128
                subset[n, pixel] = layout[pixel] & 1
                weight[n, pixel] = WEIGHTS_3[stream & (3 if anchor else 7)]
                stream >>= 2 if anchor else 3
            continue
        endpoints = _endpoints(bits, mode)
        if endpoints is None:
            unsupported[n] = True
            continue
        ends[n, 0, 0], ends[n, 0, 1] = endpoints
        stream = bits >> 65           # single subset: 3 bits for pixel 0 (implicit top bit), 4 for the rest
        weight[n, 0] = WEIGHTS_4[stream & 7]
        stream >>= 3
        for pixel in range(1, 16):
            weight[n, pixel] = WEIGHTS_4[stream & 15]
            stream >>= 4
    chosen = ends[np.arange(count)[:, None], subset]           # (blocks, 16, endpoint, channel)
    weight = weight[:, :, None]
    value = (chosen[:, :, 0] * (64 - weight) + chosen[:, :, 1] * weight + 32) >> 6
    half = ((value * 31) >> 6).astype(np.uint16)
    pixels = half.view(np.float16).astype(np.float32)          # (blocks, 16, 3)
    pixels[unsupported] = 0.0
    image = pixels.reshape(blocks_y, blocks_x, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(blocks_y * 4, blocks_x * 4, 3)
    return image[:height, :width], unsupported.reshape(blocks_y, blocks_x)


__all__ = ['decode_bc6h']
