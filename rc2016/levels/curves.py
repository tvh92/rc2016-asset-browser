"""Curve actors placed in zones (``gbl_curve_*_Path.actor``): kind-4 placement records holding a cubic spline.

A curve actor has no components; its spline is in its zone placement record (``actor_format.decode_zone_actors``,
``record_kind`` 4) after the usual matrix (+0x00), kind (+0x40) and byte size (+0x44). Offsets below are relative
to the record and every value is little endian:

| Offset | Type | Meaning |
| --- | --- | --- |
| +0x48 | float32[7] | not identified (the first three lie near the middle of the points) |
| +0x64 | u16 | point count N |
| +0x66 | u16 | point offset (0x78): N float32[3] control points |
| +0x68 | u16 | up-vector offset: N float32[3] |
| +0x6A | u16 | segment offset: S x 4 float32[3] polynomial coefficients a, b, c, d (S = +0x72) |
| +0x6C | u16 | segment length offset: U float32 (U = +0x74) |
| +0x6E | u16 | arc table offset: A float32 cumulative lengths, 8 samples per used segment plus one (A = +0x70) |
| +0x70 | u16 | arc table count A |
| +0x72 | u16 | stored segment count S (equals N; an open curve also stores the unused closing segment) |
| +0x74 | u16 | used segment count U (S when closed, S - 1 when open) |
| +0x76 | u8 | closed flag (1 closed, 0 open) |
| +0x77 | u8 | raw (1 or 3), not identified |

Segment ``i`` is ``p(t) = a + b t + c t^2 + d t^3`` for ``t`` in [0, 1]: ``a`` is point ``i`` and ``a + b + c + d`` point
``i + 1``. Points are in the actor's local frame (its placement matrix, row vectors). Across all zones 3,707 of the
3,716 kind-4 records satisfy every check in ``decode_curve``; the other nine (empty or another point layout) raise.
"""
from __future__ import annotations

import struct

import numpy as np

CURVE_RECORD_KIND = 4
POINTS_OFFSET = 0x78
ARC_SAMPLES_PER_SEGMENT = 8


def decode_curve(data, record_offset, record_size):
    """The spline of a kind-4 curve placement record; raises ValueError when any layout or continuity check fails."""
    if record_size < POINTS_OFFSET:
        raise ValueError('Curve record shorter than its header')
    (count, points_at, ups_at, segments_at, lengths_at, arc_at, arc_count, stored, used, closed,
     raw_flag) = struct.unpack_from('<9HBB', data, record_offset + 0x64)
    expected = dict(points=POINTS_OFFSET, ups=points_at + 12 * count, segments=ups_at + 12 * count,
                    lengths=segments_at + 48 * stored, arc=lengths_at + 4 * used, end=arc_at + 4 * arc_count)
    found = dict(points=points_at, ups=ups_at, segments=segments_at, lengths=lengths_at, arc=arc_at, end=record_size)
    wrong = [name for name in expected if expected[name] != found[name]]
    if wrong:
        raise ValueError('Curve record layout differs at ' + ', '.join(wrong))
    if count < 2 or stored != count or used != (stored if closed else stored - 1) or closed not in (0, 1):
        raise ValueError(f'Curve counts are inconsistent: {count} points, {stored} stored, {used} used, closed {closed}')
    if arc_count != ARC_SAMPLES_PER_SEGMENT * used + 1:
        raise ValueError(f'Curve arc table has {arc_count} samples for {used} segments')

    def floats(offset, n):
        return np.frombuffer(data, '<f4', n, record_offset + offset).astype(np.float64)

    points = floats(points_at, 3 * count).reshape(-1, 3)
    segments = floats(segments_at, 12 * stored).reshape(-1, 4, 3)[:used]
    lengths, arc = floats(lengths_at, used), floats(arc_at, arc_count)
    extent = max(float(np.abs(points).max()), 1.0)
    following = np.roll(points, -1, axis=0)[:used]
    if np.abs(segments[:, 0] - points[:used]).max() > 1e-4 * extent or \
            np.abs(segments.sum(axis=1) - following).max() > 1e-4 * extent:
        raise ValueError('Curve segments do not join its points')
    if abs(lengths.sum() - arc[-1]) > 1e-4 * max(arc[-1], 1.0) or np.any(np.diff(arc) < 0):
        raise ValueError('Curve lengths and arc table disagree')
    return dict(points=points, ups=floats(ups_at, 3 * count).reshape(-1, 3), segments=segments, lengths=lengths,
                arc=arc, closed=bool(closed), raw_flag=raw_flag, header_floats=floats(0x48, 7).tolist())


def sample(curve, distance):
    """(position, tangent, up) at arc length ``distance`` along the curve, in the curve actor's local frame.

    The arc table gives the length at eight even parameter steps per segment; between two steps the parameter is
    interpolated linearly in length. A closed curve wraps; an open one clamps to its ends. The tangent is the unit
    derivative and the up vector is the segment's end up vectors blended by the parameter.
    """
    arc = curve['arc']
    total = arc[-1]
    distance = distance % total if curve['closed'] else min(max(distance, 0.0), total)
    step = min(int(np.searchsorted(arc, distance, side='right')) - 1, len(arc) - 2)
    span = arc[step + 1] - arc[step]
    fraction = (distance - arc[step]) / span if span > 0 else 0.0
    segment, part = divmod(step, ARC_SAMPLES_PER_SEGMENT)
    t = (part + fraction) / ARC_SAMPLES_PER_SEGMENT
    a, b, c, d = curve['segments'][segment]
    position = a + b * t + c * t * t + d * t * t * t
    tangent = b + 2 * c * t + 3 * d * t * t
    tangent = tangent / (np.linalg.norm(tangent) or 1.0)
    ups = curve['ups']
    up = ups[segment] * (1 - t) + ups[(segment + 1) % len(ups)] * t
    return position, tangent, up / (np.linalg.norm(up) or 1.0)


def frame_matrix(position, forward, up):
    """Row-vector native matrix whose local +Z points along ``forward`` and +Y as close to ``up`` as possible."""
    side = np.cross(up, forward)
    if np.linalg.norm(side) < 1e-9:
        side = np.cross((1.0, 0.0, 0.0) if abs(forward[0]) < 0.9 else (0.0, 0.0, 1.0), forward)
    side /= np.linalg.norm(side)
    lift = np.cross(forward, side)
    matrix = np.eye(4)
    matrix[0, :3], matrix[1, :3], matrix[2, :3], matrix[3, :3] = side, lift, forward, position
    return matrix
