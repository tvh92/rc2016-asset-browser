"""Reflection-capture volumes of a lighting zone (data only, no GL; docs/levels.md, "Reflection volumes").

A lighting zone (``lighting.zone``, or ``lgt.zone`` in Veldin) holds box-volume records in section
``0xBDAB2B0D`` (u32 offsets into the ``0x06ABCAB2`` heap, as for instances). A record whose flag word (``+0x48``)
has bit ``0x20`` set is a reflection capture: ``+0x00`` is a row-vector 4x4 matrix whose rows 0 to 2 are the box
half-axes (lengths are half extents) and row 3 the centre, ``+0x44`` the record size, ``+0x50`` the u64 volume id,
and the capture's texture path sits inline from ``+0x70``
(``\\textures\\lgt\\volumereflections\\<id>\\reflection.texture``). Other flag values (fog and similar volumes) are
not read here.

Inferred, not decoded: that local ``+-1`` spans the box (the camera of the Veldin capture lies inside the volume
that the lit pass used, and no other volume contains it), and that a level wants one capture per view (the game
selects probes per lightgrid cell; the viewer has a single probe).
"""
from __future__ import annotations

import re
import struct

from rc2016.levels.zone_format import sections

HEAP = 0x06ABCAB2
VOLUME_INDEX = 0xBDAB2B0D
GRID_COORDINATES = 0xD6E15BC0       # lightgrid tile centres, (x, y, z floats, u32 ordinal) records
FLAG_REFLECTION = 0x20
RECORD_PATH = 0x70
REFLECTION_PATH = re.compile(rb'[\x20-\x7e]*volumereflections[\x20-\x7e]+\.texture')


def _inverse3(m):
    """Inverse of a 3x3 (rows of floats), or None when singular."""
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if not abs(det) > 1e-12:
        return None
    return ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det), \
           ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det), \
           ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det)


def reflection_volumes(zone_data):
    """The reflection-capture volumes of a zone, in record order: [{'index', 'flags', 'id', 'path', 'centre',
    'half_extents', 'volume', 'inverse'}]. ``path`` has ``/`` separators and no leading ``\\`` or ``?``; ``volume``
    is the box volume; ``inverse`` is the inverse of the 3x3 axes (the local frame of ``contains``). An empty list
    when the zone has no volume section. Records that are truncated, singular, non-finite or whose path is not a
    reflection texture are skipped."""
    found = sections(zone_data)
    if VOLUME_INDEX not in found or HEAP not in found:
        return []
    heap_start, heap_size = found[HEAP]
    index_start, index_size = found[VOLUME_INDEX]
    result = []
    for index, offset in enumerate(struct.unpack_from(f'<{index_size // 4}I', zone_data, index_start)):
        if offset + RECORD_PATH > heap_size:
            continue
        base = heap_start + offset
        size = struct.unpack_from('<I', zone_data, base + 0x44)[0]
        flags = struct.unpack_from('<I', zone_data, base + 0x48)[0]
        if not flags & FLAG_REFLECTION or size <= RECORD_PATH or offset + size > heap_size:
            continue
        m = struct.unpack_from('<16f', zone_data, base)
        if not all(abs(v) < 1e7 for v in m):     # also rejects NaN
            continue
        axes = (m[0:3], m[4:7], m[8:11])
        inverse = _inverse3(axes)
        text = REFLECTION_PATH.match(zone_data[base + RECORD_PATH:base + size].split(b'\0')[0])
        if inverse is None or text is None:
            continue
        half = tuple(sum(v * v for v in row) ** 0.5 for row in axes)
        result.append(dict(index=index, flags=flags, id=struct.unpack_from('<Q', zone_data, base + 0x50)[0],
                           path=text.group(0).decode().replace('\\', '/').lstrip('?/'), centre=tuple(m[12:15]),
                           half_extents=half, volume=8.0 * half[0] * half[1] * half[2], inverse=inverse))
    return result


def contains(volume, point):
    """Whether native world ``point`` lies inside the volume's box (local ``|x|, |y|, |z| <= 1``)."""
    d = [point[k] - volume['centre'][k] for k in range(3)]
    inv = volume['inverse']
    return all(abs(sum(d[r] * inv[r][c] for r in range(3))) <= 1.0 for c in range(3))


def grid_centres(zone_data):
    """Lightgrid tile centres (x, y, z) listed by a lighting zone, native units; [] when it lists none."""
    found = sections(zone_data)
    if GRID_COORDINATES not in found:
        return []
    start, size = found[GRID_COORDINATES]
    return [struct.unpack_from('<3f', zone_data, start + 16 * i) for i in range(size // 16)]


def choose_reflection(volumes, centres=(), position=None):
    """The one volume that represents the level, or None.

    ``volumes`` are ``reflection_volumes`` rows in a stable order (zone path, record index). With a native
    ``position`` the volume containing it wins (the smallest box if several do). Otherwise, or when no volume
    contains it, the default applies: the only volume, or of several the one whose box encloses the most
    lightgrid tile ``centres`` (the probe that covers most of the lit space; a viewer choice, not decoded), the
    earlier one on a tie; with no tile inside any box there is no default."""
    if not volumes:
        return None
    if position is not None:
        inside = [v for v in volumes if contains(v, position)]
        if inside:
            return min(inside, key=lambda v: v['volume'])
    if len(volumes) == 1:
        return volumes[0]
    counts = [sum(contains(v, c) for c in centres) for v in volumes]
    best = max(counts)
    return volumes[counts.index(best)] if best > 0 else None
