"""Pure math tests for the level viewer: camera, presentation basis, mirrored instances, PNG capture."""
import math
import unittest
import zlib

from .level_panel import zone_names
from .level_scene import core_bounds, hidden_by_default, mirrored, zone_label
from .level_viewport import BASIS, look_vectors, overview_camera, png_bytes, view_matrix


def apply(m, p):   # column-major 4x4 times point
    return tuple(m[i] * p[0] + m[4 + i] * p[1] + m[8 + i] * p[2] + m[12 + i] for i in range(3))


class LevelCameraTests(unittest.TestCase):
    def test_basis_maps_native_y_up_to_z_up(self):
        self.assertEqual(apply(BASIS, (1, 2, 3)), (1, -3, 2))

    def test_look_vectors_are_orthonormal_and_z_up(self):
        f, r, u = look_vectors(30.0, -20.0)
        for v in (f, r, u):
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in v)), 1.0)
        self.assertAlmostEqual(sum(a * b for a, b in zip(f, r)), 0.0)
        self.assertAlmostEqual(sum(a * b for a, b in zip(f, u)), 0.0)
        self.assertGreater(u[2], 0.0)
        self.assertAlmostEqual(look_vectors(90.0, 0.0)[0][1], 1.0)

    def test_view_matrix_puts_target_on_negative_z(self):
        eye = (10.0, -5.0, 3.0)
        f, r, u = look_vectors(90.0, -30.0)
        target = tuple(e + 7.0 * x for e, x in zip(eye, f))
        view = apply(view_matrix(eye, 90.0, -30.0), target)
        self.assertAlmostEqual(view[0], 0.0, places=5); self.assertAlmostEqual(view[1], 0.0, places=5)
        self.assertAlmostEqual(view[2], -7.0, places=5)
        right = apply(view_matrix(eye, 90.0, -30.0), tuple(e + x for e, x in zip(eye, r)))
        self.assertAlmostEqual(right[0], 1.0, places=5)

    def test_overview_looks_at_bounds_centre(self):
        eye, yaw, pitch, speed = overview_camera(((0, 0, 0), (100, 20, 200)))
        f, _r, _u = look_vectors(yaw, pitch)
        centre = (50.0, -100.0, 10.0)   # presentation coordinates
        to_centre = [c - e for c, e in zip(centre, eye)]
        length = math.sqrt(sum(x * x for x in to_centre))
        self.assertAlmostEqual(sum(a * b / length for a, b in zip(to_centre, f)), 1.0, places=5)
        self.assertGreater(speed, 0)


class LevelSceneTests(unittest.TestCase):
    def test_mirrored_uses_determinant_sign(self):
        identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [5, 6, 7, 1]]
        flipped = [[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        sheared = [[1, 0.5, 0, 0], [0, 1, 0, 0], [0, 0, 2, 0], [0, 0, 0, 1]]
        self.assertFalse(mirrored(identity)); self.assertTrue(mirrored(flipped)); self.assertFalse(mirrored(sheared))

    def test_collision_and_template_models_start_hidden(self):
        self.assertTrue(hidden_by_default('gameplay\\gbl_gp_hero_collision_01.model'))
        self.assertTrue(hidden_by_default('environment/template/template_Square.model'))
        self.assertFalse(hidden_by_default('environment/novalis/architecture/tb_nov_bldg1_floor_4x4.model'))

    def test_core_bounds_trim_distant_backdrops(self):
        points = [(float(i), 0.0, -float(i)) for i in range(100)] + [(5000.0, 0.0, -5000.0)]
        low, high = core_bounds(points)
        self.assertEqual(low[0], 2.0); self.assertEqual(high[0], 98.0)
        self.assertEqual(high[2], -2.0)
        self.assertIsNone(core_bounds([]))

    def test_zone_names_disambiguate_shared_stems(self):
        zones = ['levels/Novalis/openArea/OpenWorld/default_region/art_buildings.zone',
                 'levels/Novalis/openArea/NOV_mis_1/lighting.zone', 'levels/Novalis/openArea/OpenWorld/default_region/lighting.zone']
        names = zone_names(zones)
        self.assertEqual(names[zones[0]], 'art_buildings')
        self.assertEqual(names[zones[1]], 'lighting (NOV_mis_1)')
        self.assertEqual(zone_label('levels\\x\\sound.zone'), 'sound')

    def test_png_rows_are_flipped_to_top_first(self):
        data = png_bytes(1, 2, bytes([1, 2, 3, 4, 5, 6]))   # bottom row (1,2,3), top row (4,5,6)
        self.assertTrue(data.startswith(b"\x89PNG"))
        idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
        self.assertEqual(zlib.decompress(idat), b"\x00\x04\x05\x06\x00\x01\x02\x03")


if __name__ == "__main__":
    unittest.main()
