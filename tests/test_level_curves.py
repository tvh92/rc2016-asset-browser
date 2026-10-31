"""Curve actor records (kind-4 zone placements): decoding, checks and arc-length sampling.

Records are built here from straight-line segments; no game data is used.
"""
import struct
import unittest

import numpy as np

from rc2016.levels import curves as C


def record(points, closed=True, ups=None):
    """A kind-4 placement record whose segments are straight lines between ``points``."""
    points = np.asarray(points, dtype=float)
    count = len(points)
    used = count if closed else count - 1
    segments = []
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        segments.append([a, b - a, np.zeros(3), np.zeros(3)])
    lengths = [float(np.linalg.norm(points[(i + 1) % count] - points[i])) for i in range(used)]
    arc = [0.0]
    for length in lengths:
        base = arc[-1]
        arc.extend(base + length * k / C.ARC_SAMPLES_PER_SEGMENT for k in range(1, C.ARC_SAMPLES_PER_SEGMENT + 1))
    ups = np.tile((0.0, 1.0, 0.0), (count, 1)) if ups is None else np.asarray(ups, dtype=float)
    p_at = C.POINTS_OFFSET
    u_at = p_at + 12 * count
    s_at = u_at + 12 * count
    l_at = s_at + 48 * count
    a_at = l_at + 4 * used
    size = a_at + 4 * len(arc)
    head = bytearray(p_at)
    struct.pack_into('<16f', head, 0, *np.eye(4).ravel())
    struct.pack_into('<II', head, 0x40, C.CURVE_RECORD_KIND, size)
    struct.pack_into('<9HBB', head, 0x64, count, p_at, u_at, s_at, l_at, a_at, len(arc), count, used, int(closed), 1)
    body = (points.astype('<f4').tobytes() + ups.astype('<f4').tobytes() + np.asarray(segments, '<f4').tobytes()
            + np.asarray(lengths, '<f4').tobytes() + np.asarray(arc, '<f4').tobytes())
    return bytes(head) + body


SQUARE = [(0, 0, 0), (10, 0, 0), (10, 0, 10), (0, 0, 10)]


class DecodeTests(unittest.TestCase):
    def test_closed_curve_decodes_with_every_segment(self):
        data = record(SQUARE)
        curve = C.decode_curve(data, 0, len(data))
        self.assertTrue(curve['closed'])
        self.assertEqual(len(curve['segments']), 4)
        self.assertAlmostEqual(curve['arc'][-1], 40.0, places=4)

    def test_open_curve_drops_the_closing_segment(self):
        data = record(SQUARE, closed=False)
        curve = C.decode_curve(data, 0, len(data))
        self.assertFalse(curve['closed'])
        self.assertEqual(len(curve['segments']), 3)
        self.assertAlmostEqual(curve['arc'][-1], 30.0, places=4)

    def test_a_broken_record_is_refused(self):
        data = bytearray(record(SQUARE))
        with self.assertRaisesRegex(ValueError, 'layout'):
            C.decode_curve(bytes(data), 0, len(data) - 4)
        at = C.POINTS_OFFSET + 12 * 4 * 2 + 12   # second segment's a vector: no longer the second point
        struct.pack_into('<f', data, at, 99.0)
        with self.assertRaisesRegex(ValueError, 'join'):
            C.decode_curve(bytes(data), 0, len(data))


class SampleTests(unittest.TestCase):
    def setUp(self):
        data = record(SQUARE)
        self.curve = C.decode_curve(data, 0, len(data))

    def test_distance_walks_the_segments_and_wraps(self):
        position, tangent, up = C.sample(self.curve, 15.0)
        self.assertTrue(np.allclose(position, (10, 0, 5), atol=1e-5))
        self.assertTrue(np.allclose(tangent, (0, 0, 1)))
        self.assertTrue(np.allclose(up, (0, 1, 0)))
        self.assertTrue(np.allclose(C.sample(self.curve, 45.0)[0], (5, 0, 0), atol=1e-5))

    def test_frame_points_local_z_along_the_tangent(self):
        matrix = C.frame_matrix(np.array((1.0, 2.0, 3.0)), np.array((1.0, 0.0, 0.0)), np.array((0.0, 1.0, 0.0)))
        self.assertTrue(np.allclose(matrix[2, :3], (1, 0, 0)))
        self.assertTrue(np.allclose(matrix[1, :3], (0, 1, 0)))
        self.assertAlmostEqual(np.linalg.det(matrix[:3, :3]), 1.0)
        self.assertTrue(np.allclose(matrix[3, :3], (1, 2, 3)))


if __name__ == '__main__':
    unittest.main()
