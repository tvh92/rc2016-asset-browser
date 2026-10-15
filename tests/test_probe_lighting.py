"""Level atmosphere lighting maps (rc2016/levels/probe_lighting.py) on synthetic cubes."""
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from rc2016.levels import probe_lighting as pl

FACE_COLOURS = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 1.0, 0.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0)]


def constant_cube(colours, size=16):
    return [np.full((size, size, 3), colour, np.float32) for colour in colours]


def analytic_cube(function, size=32):
    """Cube whose texel centres hold function(unit native direction)."""
    c = (np.arange(size) + 0.5) / (size * 0.5) - 1.0
    u, v = np.meshgrid(c, c)
    faces = []
    for face in range(6):
        d = np.stack([np.broadcast_to(np.asarray(x, float), u.shape) for x in pl.face_direction(face, u, v)], axis=-1)
        faces.append(function(d / np.linalg.norm(d, axis=-1, keepdims=True)).astype(np.float32))
    return faces


def irradiance(sh, n):
    x, y, z = n
    basis = np.array([0.282095, 0.488603 * y, 0.488603 * z, 0.488603 * x, 1.092548 * x * y, 1.092548 * y * z,
                      0.315392 * (3 * z * z - 1), 1.092548 * x * z, 0.546274 * (x * x - y * y)])
    return basis @ sh


class CubeMappingTests(unittest.TestCase):
    def test_face_axes_follow_the_native_to_blender_basis(self):
        image = pl.latlong(constant_cube(FACE_COLOURS), 64, 32)
        np.testing.assert_allclose(image[0, 5], FACE_COLOURS[2])      # up is native +Y
        np.testing.assert_allclose(image[-1, 5], FACE_COLOURS[3])     # down is native -Y
        np.testing.assert_allclose(image[15, 32], FACE_COLOURS[0])    # Blender +X is native +X
        np.testing.assert_allclose(image[15, 0], FACE_COLOURS[1])     # Blender -X is native -X
        np.testing.assert_allclose(image[15, 16], FACE_COLOURS[5])    # Blender +Y is native -Z
        np.testing.assert_allclose(image[15, 48], FACE_COLOURS[4])    # Blender -Y is native +Z

    def test_sample_cube_reads_the_texel_a_direction_hits(self):
        faces = [np.zeros((4, 4, 3), np.float32) for _ in range(6)]
        faces[2][3, 0] = (7.0, 8.0, 9.0)      # +Y face, bottom-left texel: u = -1, v = +1 -> x < 0, z > 0
        got = pl.sample_cube(faces, [(-0.9, 1.0, 0.9)])
        np.testing.assert_allclose(got[0], (7.0, 8.0, 9.0))

    def test_sun_direction_points_towards_the_sun_in_blender_space(self):
        self.assertEqual(pl.sun_direction_blender((0.0, -1.0, 0.0)), (-0.0, 0.0, 1.0))
        got = pl.sun_direction_blender((0.0776798203587532, -0.6691306233406067, -0.7390738129615784))
        np.testing.assert_allclose(got, (-0.0776798, -0.7390738, 0.6691306), atol=1e-6)


class SeamTests(unittest.TestCase):
    def test_smooth_cube_is_continuous(self):
        light = np.array([0.3, 0.5, 0.8])
        faces = analytic_cube(lambda d: 1.0 + 0.5 * (d @ light)[..., None] * np.ones(3))
        self.assertLess(pl.seam_ratio(faces), pl.SEAM_RATIO_LIMIT)

    def test_unrelated_faces_fail(self):
        self.assertGreater(pl.seam_ratio(constant_cube(FACE_COLOURS)), pl.SEAM_RATIO_LIMIT)


class MapTests(unittest.TestCase):
    def test_uniform_cube_gives_uniform_irradiance_and_radiance(self):
        faces = constant_cube([(0.5, 0.25, 0.125)] * 6)
        maps = pl.environment_from_faces(faces, (0.0, -1.0, 0.0), (0.7, 0.6, 0.5), 1.5)
        for normal in ((0, 0, 1), (1, 0, 0), (0, -1, 0), (0.6, 0.0, -0.8)):
            np.testing.assert_allclose(irradiance(maps['sh'], normal), math.pi * np.array([0.5, 0.25, 0.125]), rtol=0.02)
        np.testing.assert_allclose(maps['bands'].reshape(-1, 3).mean(axis=0), (0.5, 0.25, 0.125), rtol=0.02)
        np.testing.assert_allclose(maps['sun'], (0.7, 0.6, 0.5), rtol=1e-6)
        np.testing.assert_allclose(maps['sun_direction'], (0.0, 0.0, 1.0), atol=1e-6)
        self.assertEqual((maps['strength'], maps['exposure']), (1.0, 1.5))

    def test_probe_glare_is_left_to_the_atmosphere_sun(self):
        plain = constant_cube([(0.5, 0.5, 0.5)] * 6, 128)
        glared = constant_cube([(0.5, 0.5, 0.5)] * 6, 128)
        glared[2][:32, :32] = 100.0
        up = (0, 0, 1)
        reference = irradiance(pl.environment_from_faces(plain, (0, -1, 0), (1, 1, 1), 0)['sh'], up)
        clipped = irradiance(pl.environment_from_faces(glared, (0, -1, 0), (1, 1, 1), 0)['sh'], up)
        self.assertLess(float(np.max(clipped - reference)), 0.1)           # unclipped it would add about 3
        self.assertGreater(float(np.min(clipped - reference)), -1e-3)       # and the glare is not removed outright

    def test_fill_unsupported_copies_only_flagged_blocks(self):
        face = np.zeros((8, 8, 3), np.float32)
        fallback = np.ones((8, 8, 3), np.float32)
        pl.fill_unsupported(face, np.array([[False, True], [False, False]]), fallback)
        self.assertEqual(face[:4, 4:].min(), 1.0)
        self.assertEqual(face[:4, :4].max(), 0.0)
        self.assertEqual(face[4:].max(), 0.0)


class LevelEnvironmentTests(unittest.TestCase):
    sky = dict(atmosphere='atmosphere/atm_x/atm_x.atmosphere', reflection='textures/atmosphere/volumereflections/0x1/reflection.texture',
               sun_ray=(0.0, -1.0, 0.0), sun_rgb=(0.7, 0.6, 0.5))

    def faces_and_info(self):
        light = np.array([0.3, 0.5, 0.8])
        faces = analytic_cube(lambda d: 1.0 + 0.5 * (d @ light)[..., None] * np.ones(3), 32)
        return faces, dict(probe=self.sky['reflection'], probe_sha256='ab' * 32, size=32, clamped_block_fraction=0.1,
                           peak=1.5, mean=[1.0, 1.0, 1.0])

    def test_maps_carry_sun_exposure_and_provenance_and_are_cached(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(pl, 'CACHE_DIR', Path(folder)), \
                mock.patch.object(pl, 'probe_faces', side_effect=lambda *_a: self.faces_and_info()), \
                mock.patch.object(pl, 'environment_from_faces', wraps=pl.environment_from_faces) as build:
            first = pl.level_environment(None, self.sky)
            second = pl.level_environment(None, self.sky)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(len(list(Path(folder).glob('level_*.npz'))), 1)
        for maps in (first, second):
            self.assertEqual(maps['exposure'], pl.REVIEW_EXPOSURE_STOPS)
            self.assertEqual(maps['sun_rgb'], [0.7, 0.6, 0.5])
            self.assertEqual(maps['probe'], self.sky['reflection'])
            self.assertLess(maps['seam_ratio'], pl.SEAM_RATIO_LIMIT)
            np.testing.assert_allclose(maps['sun_direction'], (0.0, 0.0, 1.0), atol=1e-6)
        np.testing.assert_array_equal(first['sh'], second['sh'])

    def test_exposure_override_and_changed_sun_miss_the_cache(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(pl, 'CACHE_DIR', Path(folder)), \
                mock.patch.object(pl, 'probe_faces', side_effect=lambda *_a: self.faces_and_info()):
            self.assertEqual(pl.level_environment(None, self.sky, exposure_stops=1.0)['exposure'], 1.0)
            moved = dict(self.sky, sun_ray=(1.0, 0.0, 0.0))
            np.testing.assert_allclose(pl.level_environment(None, moved)['sun_direction'], (-1.0, 0.0, 0.0), atol=1e-6)

    def test_diffuse_scale_applies_to_sh_and_sun_after_the_cache(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(pl, 'CACHE_DIR', Path(folder)), \
                mock.patch.object(pl, 'probe_faces', side_effect=lambda *_a: self.faces_and_info()):
            unit = pl.level_environment(None, self.sky, diffuse_scale=1.0)
            default = pl.level_environment(None, self.sky)
            self.assertEqual(len(list(Path(folder).glob('level_*.npz'))), 1)   # one cache entry for both scales
        np.testing.assert_allclose(default['sh'], unit['sh'] * pl.DIFFUSE_LIGHT_SCALE, rtol=1e-6)
        np.testing.assert_allclose(default['sun'], unit['sun'] * pl.DIFFUSE_LIGHT_SCALE, rtol=1e-6)
        np.testing.assert_array_equal(default['bands'], unit['bands'])
        self.assertEqual(default['sun_rgb'], [0.7, 0.6, 0.5])

    def test_missing_probe_or_broken_cube_raises(self):
        with self.assertRaises(ValueError):
            pl.level_environment(None, dict(self.sky, reflection=None))
        with self.assertRaises(ValueError):
            pl.level_environment(None, None)
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(pl, 'CACHE_DIR', Path(folder)), \
                mock.patch.object(pl, 'probe_faces', return_value=(constant_cube(FACE_COLOURS), {'probe_sha256': 'cd' * 32})):
            with self.assertRaises(ValueError):
                pl.level_environment(None, self.sky)


if __name__ == '__main__':
    unittest.main()
