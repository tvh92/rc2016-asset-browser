"""Lightgrid indirect light of the Level atmosphere lighting (rc2016.levels.lightgrid; synthetic cells, no game data, no GL)."""
import unittest

import numpy as np

from rc2016.levels import lightgrid as lg
from rc2016.levels import scene_effects
from rc2016.viewer import shading


def word(r, g, b):
    """R11G11B10 word of exponent-only channels: exponent fields r, g (6-bit mantissa) and b (5-bit mantissa)."""
    return (r << 6) | ((g << 6) << 11) | ((b << 5) << 22)


ONE = word(15, 15, 15)    # exponent 15, mantissa 0: 1.0 in every channel
TWO = word(16, 16, 16)
HALF = word(14, 14, 14)


def blank_tiles(n):
    cells = np.zeros((n, lg.CELLS_PER_TILE, lg.CELL_BYTES), np.uint8)
    cells[..., 0:3] = (128, 128, 255)                                       # direction about +z
    cells[..., 16], cells[..., 17], cells[..., 18], cells[..., 19] = 128, 255, 128, 255   # default plane, open
    return cells


def set_words(cells, a=0, b=0, c=0):
    view = cells.view('<u4').reshape(cells.shape[:-1] + (6,))
    view[..., 1], view[..., 2], view[..., 3] = a, b, c


class DecodeTests(unittest.TestCase):
    def test_r11g11b10_values(self):
        colour = lg.decode_r11g11b10(np.array([ONE, word(14, 15, 16), ONE | 32], np.uint32))
        np.testing.assert_allclose(colour[0], (1.0, 1.0, 1.0))
        np.testing.assert_allclose(colour[1], (0.5, 1.0, 2.0))
        self.assertAlmostEqual(float(colour[2][0]), 1.5)                    # red mantissa 32 of 64

    def test_subnormal_zero_and_nonfinite_channels(self):
        colour = lg.decode_r11g11b10(np.array([0, 1, word(31, 31, 31)], np.uint32))
        np.testing.assert_array_equal(colour[0], (0.0, 0.0, 0.0))
        self.assertAlmostEqual(float(colour[1][0]), 2.0 ** -14 / 64)        # smallest subnormal of an 11-bit channel
        np.testing.assert_array_equal(colour[2], (0.0, 0.0, 0.0))           # infinity and NaN read as no light

    def test_cell_fields(self):
        cells = blank_tiles(1)[0, :1].copy()
        cells[0, 0:3] = (255, 0, 255)
        cells[0, 16:20] = (51, 102, 153, 204)
        set_words(cells, a=ONE)
        fields = lg.decode_cells(cells)
        np.testing.assert_allclose(fields['d'][0], (1.0, -1.0, 1.0))
        np.testing.assert_allclose(fields['q'][0], (0.2, 0.4, 0.6), atol=1e-6)
        self.assertAlmostEqual(float(fields['w'][0]), 0.8)
        np.testing.assert_allclose(fields['A'][0], (1, 1, 1))


class AddressingTests(unittest.TestCase):
    def test_cell_index_is_x_fastest(self):
        self.assertEqual(lg.cell_index(1, 0, 0), 1)
        self.assertEqual(lg.cell_index(0, 1, 0), 16)
        self.assertEqual(lg.cell_index(0, 0, 1), 256)
        self.assertEqual(lg.cell_index(15, 15, 15), 4095)

    def test_tiles_floor_and_slots_wrap_at_64(self):
        np.testing.assert_array_equal(lg.tile_of([[-1, 0, 15], [16, -16, -17]]), [[-1, 0, 0], [1, -1, -2]])
        self.assertEqual(int(lg.table_slot([1, 2, 3])), 1 + 64 * 2 + 4096 * 3)
        self.assertEqual(int(lg.table_slot([-1, 64, 65])), 63 + 0 + 4096 * 1)

    def test_pack_rejects_off_centre_collisions_and_bad_shapes(self):
        cells = blank_tiles(2)
        with self.assertRaises(ValueError):
            lg.pack_tiles(cells[:1], [[16, 8, 8]])                    # a centre is 8 mod 16
        with self.assertRaises(ValueError):
            lg.pack_tiles(cells, [[8, 8, 8], [8 + 64 * 16, 8, 8]])    # the second wraps onto the first
        with self.assertRaises(ValueError):
            lg.pack_tiles(cells[:, :10], [[8, 8, 8], [24, 8, 8]])

    def test_atlas_places_every_cell_at_its_slot(self):
        rng = np.random.default_rng(3)
        cells = rng.integers(0, 256, (70, lg.CELLS_PER_TILE, lg.CELL_BYTES), dtype=np.uint8)
        cells[..., 4:16] = rng.integers(0, 0x7C, (70, lg.CELLS_PER_TILE, 12), dtype=np.uint8)   # finite small floats
        coordinates = np.array([[8 + 16 * (i % 10), 8 + 16 * (i // 10), 8] for i in range(70)])
        grid = lg.pack_tiles(cells, coordinates)
        self.assertEqual(grid.atlas_tiles, (8, 8, 2))
        colour, dirq = grid.atlas()
        self.assertEqual(colour.shape, (32, 128, 128 * 3))
        self.assertEqual(dirq.shape, (32, 128, 128 * 2, 4))
        words = grid.cells.view('<u4').reshape(70, lg.CELLS_PER_TILE, 6)
        for tile, (x, y, z) in ((0, (0, 0, 0)), (69, (5, 3, 12)), (66, (15, 15, 15))):
            ax, ay, az = tile % 8, (tile // 8) % 8, tile // 64
            X, Y, Z = ax * 16 + x, ay * 16 + y, az * 16 + z
            index = lg.cell_index(x, y, z)
            np.testing.assert_array_equal(dirq[Z, Y, 2 * X], grid.cells[tile, index, 0:4])
            np.testing.assert_array_equal(dirq[Z, Y, 2 * X + 1], grid.cells[tile, index, 16:20])
            np.testing.assert_array_equal(colour[Z, Y, 3 * X:3 * X + 3], words[tile, index, 1:4])

    def test_table_points_at_the_atlas_tile_and_wraps(self):
        cells = blank_tiles(2)
        grid = lg.pack_tiles(cells, [[8 - 16, 8, 8 + 16 * 64], [8, 8, 8]])
        np.testing.assert_array_equal(grid.table[0, 0, 63], (0, 0, 0, 1))      # tile (-1, 0, 64) wraps to x 63, z 0
        self.assertEqual(float(grid.table[0, 0, 0, 3]), 1.0)                   # tile (0, 0, 0): atlas slot 1
        np.testing.assert_array_equal(grid.table[0, 0, 0, :3], (1, 0, 0))
        self.assertEqual(float(grid.table[0, 5, 5, 3]), 0.0)                   # no tile there

    def test_infinite_channels_are_cleared_before_upload(self):
        cells = blank_tiles(1)
        set_words(cells, a=word(31, 15, 15))
        packed = lg.pack_tiles(cells, [[8, 8, 8]])
        self.assertTrue(np.isfinite(lg.decode_cells(packed.cells)['A']).all())
        self.assertEqual(int(packed.cells.view('<u4').reshape(1, -1, 6)[0, 0, 1]), 0)


class ValidityTests(unittest.TestCase):
    Q = np.array([0.5, 1.0, 0.5])      # the default plane; q - 0.5 = (0, 0.5, 0) faces +y

    def test_open_and_blocked_cells_at_their_centre(self):
        zero = np.zeros(3)
        self.assertEqual(float(lg.corner_validity(self.Q, 1.0, zero)), 1.0)
        self.assertEqual(float(lg.corner_validity(self.Q, 0.0, zero)), 0.0)
        self.assertEqual(float(lg.corner_validity(self.Q, 7 / 16, zero)), 0.0)    # 16 w - 7 = 0 is the threshold
        self.assertAlmostEqual(float(lg.corner_validity(self.Q, 7.5 / 16, zero)), 0.5)

    def test_plane_opens_a_blocked_cell_towards_its_normal(self):
        up, down = np.array([0.0, 0.5, 0.0]), np.array([0.0, -0.5, 0.0])
        self.assertEqual(float(lg.corner_validity(self.Q, 7 / 16, up)), 1.0)      # 8 * 0.5 * 0.5 = 2, clamped
        self.assertEqual(float(lg.corner_validity(self.Q, 7 / 16, down)), 0.0)
        self.assertAlmostEqual(float(lg.corner_validity(self.Q, 7 / 16, np.array([0.0, 0.1, 0.0]))), 0.4)

    def test_matches_the_shader_form_with_the_signed_component_sum(self):
        rng = np.random.default_rng(5)
        for _ in range(20):
            q, w, o = rng.random(3), float(rng.random()), rng.random(3) - 0.5
            shader = np.clip(8 * np.dot(q, o) - 4 * o.sum() + 16 * w - 7, 0, 1)
            self.assertAlmostEqual(float(lg.corner_validity(q, w, o)), float(shader))


def uniform_grid(a=ONE, b=0, c=0):
    cells = blank_tiles(1)
    set_words(cells, a, b, c)
    return lg.pack_tiles(cells, [[8, 8, 8]])


class SampleTests(unittest.TestCase):
    def test_lerp_between_colours_follows_the_direction(self):
        grid = uniform_grid(a=ONE, b=HALF)          # A 1.0 facing +z, B 0.5 facing -z
        light, cover = grid.sample(np.full((3, 3), 8.0), [[0, 0, 1], [0, 0, -1], [1, 0, 0]], sky_scale=0.0)
        np.testing.assert_allclose(light[0], 1.0, atol=0.01)
        np.testing.assert_allclose(light[1], 0.5, atol=0.01)
        np.testing.assert_allclose(light[2], 0.75, atol=0.01)    # perpendicular: halfway
        np.testing.assert_allclose(cover, 1.0)

    def test_sky_term_scales_with_n_y(self):
        grid = uniform_grid(a=0, c=ONE)
        light, _ = grid.sample(np.full((3, 3), 8.0), [[0, 1, 0], [0, -1, 0], [1, 0, 0]], sky_scale=2.0)
        np.testing.assert_allclose(light[:, 0], [2.0, 0.0, 1.0], atol=1e-5)

    def test_outside_every_tile_has_no_coverage_and_no_light(self):
        light, cover = uniform_grid().sample([[100.0, 8, 8], [8, 8, -20.0]], [[0, 0, 1]] * 2)
        np.testing.assert_array_equal(cover, 0.0)
        np.testing.assert_array_equal(light, 0.0)

    def test_fully_blocked_cells_lose_coverage_but_keep_normalised_light(self):
        cells = blank_tiles(1)
        cells[..., 19] = 0
        set_words(cells, a=ONE)
        light, cover = lg.pack_tiles(cells, [[8, 8, 8]]).sample([[8.0, 8, 8]], [[0, 0, 1]], sky_scale=0.0)
        self.assertEqual(float(cover[0]), 0.0)
        np.testing.assert_allclose(light[0], 1.0, atol=0.01)     # the 2^-18 floor renormalises, as in the shader

    def test_a_blocked_black_corner_is_outweighed_by_open_ones(self):
        cells = blank_tiles(1)
        set_words(cells, a=ONE)
        index = lg.cell_index(8, 8, 8)                           # the cell centred at (8.5, 8.5, 8.5), relative to the tile
        cells[0, index, 19] = 0
        cells.view('<u4').reshape(1, -1, 6)[0, index, 1] = 0
        light, cover = lg.pack_tiles(cells, [[8, 8, 8]]).sample([[8.0, 8.0, 8.0]], [[0, 0, 1]], sky_scale=0.0)
        self.assertAlmostEqual(float(light[0][0]), 1.0, places=2)
        self.assertEqual(float(cover[0]), 1.0)

    def test_trilinear_blend_across_a_tile_boundary(self):
        cells = blank_tiles(2)
        set_words(cells[0], a=ONE)
        set_words(cells[1], a=TWO)
        grid = lg.pack_tiles(cells, [[8, 8, 8], [24, 8, 8]])
        light, cover = grid.sample([[16.0, 8.5, 8.5], [15.5, 8.5, 8.5], [16.5, 8.5, 8.5]], [[0, 0, 1]] * 3, sky_scale=0.0)
        np.testing.assert_allclose(light[:, 0], [1.5, 1.0, 2.0], atol=0.01)
        np.testing.assert_allclose(cover, 1.0)

    def test_table_wraps_like_the_game(self):
        # a point 1024 units away reads the same table slot (the game's table has 64 tiles per axis)
        _, cover = uniform_grid().sample([[8.0 + 1024, 8.5, 8.5]], [[0, 0, 1]], sky_scale=0.0)
        self.assertEqual(float(cover[0]), 1.0)


class ShaderMirrorTests(unittest.TestCase):
    def test_shader_constants_match_the_cpu_reference(self):
        self.assertIn(f'GRID_COVER_GAIN = {lg.COVER_GAIN}', shading.LIGHTING)
        self.assertEqual(lg.VALIDITY_FLOOR, 3.814697265625e-06)
        self.assertIn('3.814697265625e-06', shading.LIGHTING)
        self.assertIn('8.0 * dot(qw.xyz - 0.5, o) + 16.0 * qw.w - 7.0', shading.LIGHTING)
        for name in ('uGridOn', 'uGridTable', 'uGridColour', 'uGridDirQ', 'uGridFromView', 'uGridParams', 'uGridInvSize'):
            self.assertIn(name, scene_effects.SCENE_UNIFORMS)

    def test_grid_code_is_limited_to_scene_effect_programs(self):
        text = shading.LIGHTING
        self.assertLess(text.index('#ifdef RC_SCENE_EFFECTS\n// Lightgrid'), text.index('uniform bool uGridOn'))
        self.assertIn('if (uGridOn)', text)

    def test_calibration_constants_are_positive(self):
        self.assertGreater(lg.SKY_TERM_SCALE, 0.0)
        self.assertGreater(lg.AMBIENT_SCALE, 0.0)


if __name__ == '__main__':
    unittest.main()
