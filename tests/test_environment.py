"""Pure lighting-map math of the shared review environment (rc2016/viewer/environment.py)."""
import math
from pathlib import Path
import unittest

import numpy as np

from rc2016.viewer import environment


class SharedLightingFileTests(unittest.TestCase):
    def test_settings_point_at_existing_hdri_with_world_strength_one_and_a_half(self):
        settings = environment.shared_lighting()
        self.assertTrue(Path(settings['hdri_path']).is_file())
        self.assertEqual(settings['world_strength'], 1.5)

    def test_rotate_z_turns_the_image_as_the_pipeline_mapping_node(self):
        import numpy as np
        image = np.zeros((4, 8, 3), np.float32)
        image[1, 2] = 1.0   # azimuth phi = (0.5 - u) * 360 with u = 2.5 / 8: 67.5 degrees
        turned = environment.rotate_z(image, 45.0)   # the world shows image azimuth phi + 45 at phi
        self.assertEqual(float(turned[1, 3, 0]), 1.0)   # u = 3.5 / 8: phi 22.5, which shows image azimuth 67.5
        self.assertAlmostEqual(float(environment.rotate_z(image, 22.5)[1, 2, 0]), 0.5)
        self.assertAlmostEqual(float(turned.sum()), 3.0)

    def test_hdri_is_finite_nonnegative_sky_over_ground(self):
        image = environment.read_exr(environment.shared_lighting()['hdri_path'])
        self.assertEqual(image.shape, (512, 1024, 3))
        self.assertEqual(image.dtype, np.float32)
        self.assertTrue(np.isfinite(image).all())
        self.assertTrue((image >= 0).all())
        top, bottom = image[0], image[-1]
        self.assertGreater(top[:, 2].mean(), top[:, 0].mean())      # sky: blue above red
        self.assertGreater(bottom[:, 0].mean(), bottom[:, 2].mean())  # ground: red above blue


class DirectionUvTests(unittest.TestCase):
    def assertUv(self, direction, u, v):
        got_u, got_v = environment.direction_uv(direction)
        self.assertAlmostEqual(got_u, u, places=6)
        self.assertAlmostEqual(got_v, v, places=6)

    def test_cardinal_directions(self):
        self.assertUv((1, 0, 0), 0.5, 0.5)
        self.assertUv((0, 1, 0), 0.25, 0.5)
        self.assertUv((0, -1, 0), 0.75, 0.5)

    def test_poles(self):
        self.assertAlmostEqual(environment.direction_uv((0, 0, 1))[1], 1.0, places=6)
        self.assertAlmostEqual(environment.direction_uv((0, 0, -1))[1], 0.0, places=6)

    def test_equirect_directions_are_unit_and_round_trip_texel_centres(self):
        directions = environment.equirect_directions(8, 4)
        self.assertEqual(directions.shape, (4, 8, 3))
        np.testing.assert_allclose(np.linalg.norm(directions, axis=-1), 1.0, atol=1e-5)
        for y in range(4):
            for x in range(8):
                u, v = environment.direction_uv(directions[y, x])
                self.assertLess(abs(u * 8 - 0.5 - x), 1e-5)
                self.assertLess(abs((1 - v) * 4 - 0.5 - y), 1e-5)


class ShIrradianceTests(unittest.TestCase):
    def test_constant_image_gives_pi_times_radiance_in_constant_term(self):
        coefficients = environment.sh_irradiance(np.full((32, 64, 3), 0.5, np.float32))
        self.assertEqual(coefficients.shape, (9, 3))
        self.assertAlmostEqual(float(coefficients[0, 0] * 0.282095), math.pi * 0.5, delta=0.01 * math.pi * 0.5)
        self.assertLess(float(np.abs(coefficients[1:9]).max()), 0.02)


class ExtractSunTests(unittest.TestCase):
    def test_bright_texel_moves_into_sun_and_conserves_energy(self):
        image = np.full((32, 64, 3), 0.2, np.float32)
        y, x = 10, 20
        image[y, x] = 1000.0
        residual, direction, sun = environment.extract_sun(image)
        weights = environment._solid_angles(32, 64)[..., None]
        before = (image * weights).sum(axis=(0, 1))
        after = (residual * weights).sum(axis=(0, 1)) + sun
        np.testing.assert_allclose(after, before, rtol=1e-5)
        expected = environment.equirect_directions(64, 32)[y, x]
        np.testing.assert_allclose(direction, expected, atol=1e-4)
        self.assertLessEqual(float((residual @ np.array([0.2126, 0.7152, 0.0722], np.float32)).max()),
                             environment.SUN_THRESHOLD + 1e-3)

    def test_image_without_bright_texel_has_no_sun(self):
        image = np.full((32, 64, 3), 0.2, np.float32)
        residual, _direction, sun = environment.extract_sun(image)
        np.testing.assert_array_equal(sun, np.zeros(3, np.float32))
        np.testing.assert_array_equal(residual, image)


if __name__ == '__main__':
    unittest.main()
