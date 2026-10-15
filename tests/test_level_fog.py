import math
import unittest

from rc2016.levels import fog


class LevelFogTests(unittest.TestCase):
    def test_transmittance(self):
        self.assertEqual(fog.fog_transmittance(0.0), 1.0)
        self.assertAlmostEqual(fog.fog_transmittance(math.log(2.0) / 0.001, 0.001), 0.5)
        self.assertLess(fog.fog_transmittance(2000.0), fog.fog_transmittance(1000.0))
        self.assertEqual(fog.fog_transmittance(100.0, -1.0), 1.0)   # negative density is no fog

    def test_apply_fog_blends_towards_the_fog_colour(self):
        colour, haze = (0.2, 0.4, 0.1), (0.4, 0.7, 0.9)
        for a, b in zip(fog.apply_fog(colour, 0.0, haze), colour):
            self.assertAlmostEqual(a, b)
        far = fog.apply_fog(colour, 1e6, haze)
        for a, b in zip(far, haze):
            self.assertAlmostEqual(a, b)
        mid = fog.apply_fog(colour, 500.0, haze, 0.001)
        t = math.exp(-0.5)
        for m, c, h in zip(mid, colour, haze):
            self.assertAlmostEqual(m, h + (c - h) * t)

    def test_horizon_colour_is_the_lowest_bin(self):
        self.assertEqual(fog.horizon_colour([(0.4, 0.7, 0.9), (0.1, 0.2, 0.3)]), (0.4, 0.7, 0.9))
        self.assertIsNone(fog.horizon_colour(None))

    def test_fog_state_only_for_level_atmosphere(self):
        sky = dict(clear_sky=[(0.4, 0.7, 0.9), (0.1, 0.2, 0.3)])
        self.assertEqual(fog.fog_state(sky, 'Level atmosphere'), (fog.FOG_DENSITY, (0.4, 0.7, 0.9)))
        self.assertEqual(fog.fog_state(sky, 'Level atmosphere', enabled=False)[0], 0.0)
        self.assertEqual(fog.fog_state(sky, 'Shared HDRI')[0], 0.0)
        self.assertEqual(fog.fog_state(sky, 'Viewer rig')[0], 0.0)
        self.assertEqual(fog.fog_state(dict(clear_sky=None), 'Level atmosphere')[0], 0.0)
        self.assertEqual(fog.fog_state(None, 'Level atmosphere')[0], 0.0)


if __name__ == '__main__':
    unittest.main()
