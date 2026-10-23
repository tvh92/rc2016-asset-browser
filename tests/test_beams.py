"""Lamp beams: which spots get one and the cone mesh (rc2016/levels/beams.py; no GL)."""
import math
import unittest

import numpy as np

from rc2016.levels import beams


def spot(position=(0.0, 0.0, 0.0), axis=(0.0, -1.0, 0.0), radius=10.0, cone=(20.0, 40.0), colour=(4.0, 2.0, 1.0)):
    return dict(position=list(position), axis=list(axis), radius=radius, cone_degrees=list(cone), colour=list(colour))


class LampSelectionTests(unittest.TestCase):
    def test_lamp_models_are_named_lights_not_strips_or_signs(self):
        for name in ('rb_ari_arch_construction_work_light_02', 'tj_ari_prop_Construction_Set_Pole_Light',
                     'tj_ari_prop_Construction_Set_Shop_Light_Base', 'rb_ari_arch_pit_construction_flood_light_01', 'street_lamp_01'):
            self.assertTrue(beams.is_lamp_model(f'environment/aridia/prop/{name}/{name}.model'), name)
        for name in ('rb_ari_light_strip_base', 'rb_ari_light_arrow_sign', 'rb_ari_arch_construction_beam_01'):
            self.assertFalse(beams.is_lamp_model(f'environment/aridia/prop/{name}/{name}.model'), name)

    def test_the_claw_lamp_is_a_lamp(self):
        self.assertTrue(beams.is_lamp_model('environment/aridia/prop/x/tj_ari_prop_Construction_Set_Light.model'))

    def test_the_folder_does_not_count(self):
        self.assertFalse(beams.is_lamp_model('environment/lamp_post/rock_01.model'))

    def test_spots_within_the_distance_of_a_lamp_instance_get_a_beam(self):
        near, far = spot((1.0, 0.0, 0.0)), spot((10.0, 0.0, 0.0))
        self.assertEqual(beams.lamp_spots([near, far], [[0.0, 0.0, 0.0]]), [near])
        self.assertEqual(beams.lamp_spots([near, far], [[0.0, 0.0, 0.0]], distance=0.5), [])
        self.assertEqual(beams.lamp_spots([near], []), [])

    def test_uplights_get_no_beam(self):
        up = spot((0.0, 0.0, 0.0), axis=(0.0, 1.0, 0.0))
        level = spot((0.0, 0.0, 0.0), axis=(1.0, 0.0, 0.0))
        self.assertEqual(beams.lamp_spots([up, level], [[0.0, 0.0, 0.0]]), [level])


class MeshTests(unittest.TestCase):
    def test_cone_starts_at_the_light_and_ends_a_range_away_with_the_mean_half_angle(self):
        positions, normals, ts, colours, indices = beams.beam_mesh([spot((5.0, 6.0, 7.0))])
        count = (beams.RINGS + 1) * beams.SEGMENTS
        self.assertEqual((len(positions), len(normals), len(ts), len(colours)), (count,) * 4)
        np.testing.assert_allclose(positions[:beams.SEGMENTS], [[5.0, 6.0, 7.0]] * beams.SEGMENTS, atol=1e-5)   # the apex ring
        base = positions[-beams.SEGMENTS:]
        np.testing.assert_allclose(base[:, 1], 6.0 - 10.0, atol=1e-5)                       # range along the aim (-Y)
        np.testing.assert_allclose(np.hypot(base[:, 0] - 5.0, base[:, 2] - 7.0), 10.0 * math.tan(math.radians(15.0)), rtol=1e-5)
        self.assertEqual((ts.min(), ts.max()), (0.0, 1.0))
        self.assertTrue(indices.max() < count and len(indices) == 6 * beams.RINGS * beams.SEGMENTS)

    def test_normals_are_unit_and_perpendicular_to_the_cone_surface(self):
        positions, normals, *_ = beams.beam_mesh([spot()])
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-5)
        along = positions[-1] - positions[-1 - beams.SEGMENTS]       # a generator line of the cone
        self.assertAlmostEqual(float(np.dot(normals[-1], along)), 0.0, delta=1e-4)

    def test_colour_is_the_light_colour_scaled_to_its_brightest_channel(self):
        colours = beams.beam_mesh([spot(colour=(8.0, 4.0, 2.0))], gain=0.5)[3]
        np.testing.assert_allclose(colours[0], [0.5, 0.25, 0.125], rtol=1e-6)

    def test_meshes_concatenate_with_offset_indices_and_an_empty_list_is_empty(self):
        one = beams.beam_mesh([spot()])
        two = beams.beam_mesh([spot(), spot((50.0, 0.0, 0.0))])
        self.assertEqual(len(two[0]), 2 * len(one[0]))
        self.assertEqual(int(two[4].max()), 2 * len(one[0]) - 1)
        self.assertEqual(len(beams.beam_mesh([])[0]), 0)

    def test_aim_along_y_still_gets_a_valid_basis(self):
        for axis in ((0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)):
            normals = beams.beam_mesh([spot(axis=axis)])[1]
            self.assertTrue(np.isfinite(normals).all())


if __name__ == '__main__':
    unittest.main()
