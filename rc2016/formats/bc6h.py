"""Unsigned BC6H (DXGI 95) decoder to linear float32, for the block modes the game's probes use.

Pillow decodes BC6H to 8 bits and clamps it to [0, 1], which loses every value above 1 (sun,
bright cloud). This decoder keeps the full half-float range. Only the single-subset modes are
implemented, because the two-subset modes need the 32-entry partition table, which this project
has no source for. A block in any other mode is left black and reported in the returned mask, so
the caller decides how to fill it (``probe_lighting`` uses Pillow's clamped value).

Modes follow the Direct3D 11 functional specification (mode field: 2 bits for modes 1 and 2,
otherwise 5 bits, read little-endian from the low end of the 128-bit block):

==========  ==========  =========  ===========================================
mode        field       endpoint   status
==========  ==========  =========  ===========================================
11          0b00011     10 bits    decoded, checked against Pillow
12          0b00111     11 bits    decoded, checked against Pillow
13          0b01011     12 bits    decoded, checked against Pillow
others                             reported unsupported (the Novalis probe uses
                                   modes 2, 11, 12 and 13; mode 14 is unverified)
==========  ==========  =========  ===========================================
"""
from __future__ import annotations

WEIGHTS_4 = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)
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
    else:
        return None
    return [_unquantize(v, width) for v in e0], [_unquantize(v, width) for v in e1]


def decode_bc6h(data, width, height):
    """(image, unsupported): float32 (height, width, 3) linear RGB and a bool (height/4, width/4) mask of
    blocks in modes this decoder does not implement (their pixels are zero). ``data`` is the linear
    (not PS4-tiled) block stream, 16 bytes per 4x4 block, rows of blocks top first."""
    import numpy as np
    blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
    count = blocks_x * blocks_y
    if len(data) < count * BLOCK_BYTES:
        raise ValueError(f'BC6H data holds {len(data) // BLOCK_BYTES} blocks, {count} needed')
    e0 = np.zeros((count, 3), np.int64)
    e1 = np.zeros((count, 3), np.int64)
    index = np.zeros((count, 16), np.int64)
    unsupported = np.zeros(count, bool)
    for n in range(count):
        bits = int.from_bytes(data[n * BLOCK_BYTES:(n + 1) * BLOCK_BYTES], 'little')
        mode = bits & 3
        if mode >= 2:
            mode = bits & 31
        endpoints = _endpoints(bits, mode)
        if endpoints is None:
            unsupported[n] = True
            continue
        e0[n], e1[n] = endpoints
        stream = bits >> 65           # single subset: 3 bits for pixel 0 (implicit top bit), 4 for the rest
        index[n, 0] = stream & 7
        stream >>= 3
        for pixel in range(1, 16):
            index[n, pixel] = stream & 15
            stream >>= 4
    weight = np.array(WEIGHTS_4, np.int64)[index][:, :, None]
    value = (e0[:, None, :] * (64 - weight) + e1[:, None, :] * weight + 32) >> 6
    half = ((value * 31) >> 6).astype(np.uint16)
    pixels = half.view(np.float16).astype(np.float32)          # (blocks, 16, 3)
    pixels[unsupported] = 0.0
    image = pixels.reshape(blocks_y, blocks_x, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(blocks_y * 4, blocks_x * 4, 3)
    return image[:height, :width], unsupported.reshape(blocks_y, blocks_x)


__all__ = ['decode_bc6h']
