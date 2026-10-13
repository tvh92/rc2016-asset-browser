"""Small-object culling against the viewer's own projection."""
import math
import unittest

import numpy as np

from rc2016.levels.viewport import (FOV_DEGREES, frustum_planes, projection_matrix, screen_size_visibility,
                                    sphere_visibility)

IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)


def projected_diameter(radius, depth, height):
    """Reference: project the sphere's top and bottom through the GL matrix, in pixels."""
    p = np.asarray(projection_matrix(800, height, 0.1, 1000.), float).reshape(4, 4).T
    ys = []
    for y in (radius, -radius):
        clip = p @ np.array([0., y, -depth, 1.])
        ys.append(clip[1] / clip[3] * height / 2)
    return ys[0] - ys[1]


class ScreenSizeCullingTests(unittest.TestCase):
    def setUp(self):
        self.planes = frustum_planes(projection_matrix(800, 600, 0.1, 1000.), IDENTITY)

    def test_threshold_matches_the_projected_diameter(self):
        centres, radii = np.array([[0., 0., -10.]]), np.array([0.1])
        size = projected_diameter(0.1, 10., 600)
        self.assertAlmostEqual(size, 2 * 0.1 * 300 / math.tan(math.radians(FOV_DEGREES) / 2) / 10, places=6)
        self.assertTrue(screen_size_visibility(centres, radii, self.planes[4], 0.1, 600, size * 0.99)[0])
        self.assertFalse(screen_size_visibility(centres, radii, self.planes[4], 0.1, 600, size * 1.01)[0])

    def test_off_and_near_objects_are_kept(self):
        centres = np.array([[0., 0., -0.05], [0., 0., -5000.]])
        radii = np.array([0.001, 0.001])
        self.assertTrue(screen_size_visibility(centres, radii, self.planes[4], 0.1, 600, 0).all())
        kept = screen_size_visibility(centres, radii, self.planes[4], 0.1, 600, 1.0)
        self.assertEqual(kept.tolist(), [True, False])   # inside the near plane counts as near: kept

    def test_frustum_test_is_unchanged(self):
        centres, radii = np.array([[0., 0., -10.], [0., 0., 10.]]), np.array([1., 1.])
        self.assertEqual(sphere_visibility(centres, radii, self.planes).tolist(), [True, False])


if __name__ == '__main__':
    unittest.main()
