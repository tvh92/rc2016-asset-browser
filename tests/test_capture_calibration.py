"""Captured Novalis frame constants (rc2016.levels.capture_calibration): synthetic checks, no game data."""
import math
import unittest

from rc2016.levels import capture_calibration as cc
from rc2016.levels import fog, lightgrid, scene_effects

NOVALIS = 'atmosphere/atm_Novalis/atm_Novalis.atmosphere'
PROFILE = dict(heights=(-100.0, -52.0, 386.0, 926.0, 400.0), densities=(1.0, 0.1, 0.002, 0.0, 0.0))
CAMERA_Y = 63.600929260253906


class FrameLookupTests(unittest.TestCase):
    def test_lookup_ignores_case_and_unknown_levels(self):
        self.assertIs(cc.frame(NOVALIS), cc.frame(NOVALIS.upper()))
        self.assertIsNone(cc.frame('atmosphere/atm_veldin/atm_veldin.atmosphere'))
        self.assertIsNone(cc.frame(None))

    def test_the_key_light_is_in_game_units_not_a_multiple_of_the_rgb96(self):
        key = cc.frame(NOVALIS)['key_colour']
        rgb96 = (0.7008056640625, 0.583984375, 0.5797119140625)
        ratios = [k / r for k, r in zip(key, rgb96)]
        self.assertGreater(max(ratios) - min(ratios), 1.0)


class AmbientTests(unittest.TestCase):
    def test_grid_scale_is_pi_only_where_a_frame_was_captured(self):
        self.assertAlmostEqual(lightgrid.ambient_scale(NOVALIS), math.pi)
        self.assertEqual(lightgrid.ambient_scale('atmosphere/atm_aridia/atm_aridia.atmosphere'), lightgrid.AMBIENT_SCALE)
        self.assertEqual(lightgrid.ambient_scale(None), lightgrid.AMBIENT_SCALE)

    def test_shadow_ambient_is_not_reduced_under_the_captured_frame(self):
        self.assertEqual(scene_effects.shadow_ambient(NOVALIS), 1.0)
        self.assertEqual(scene_effects.shadow_ambient('atmosphere/atm_veldin/atm_veldin.atmosphere'), scene_effects.SHADOW_AMBIENT)


class FogTests(unittest.TestCase):
    def state(self, atmosphere=NOVALIS):
        return fog.fog_state(dict(atmosphere=atmosphere, clear_sky=[(0.4, 0.7, 0.9)], fog=PROFILE), 'Level atmosphere', CAMERA_Y)

    def test_ramps_reproduce_the_captured_constant_block(self):
        r = self.state()['ramps']
        for got, want in zip(r['a'][:3], (0.0208333358, 0.0022831049, 0.0018518518)):
            self.assertAlmostEqual(got, want, places=5)
        self.assertEqual(r['a'][3], 0.0)   # the game's a is -0.0019 here; both densities of that ramp are 0, so it adds no fog
        for got, want in zip(r['b'][:3], (3.40835285, 0.26392904, -0.59703535)):
            self.assertAlmostEqual(got, want, places=4)
        for got, want in zip(r['beta'][:3], (9.2271, 0.38530, 0.009025)):
            self.assertAlmostEqual(got, want, delta=want * 2e-3)
        self.assertAlmostEqual(r['c88'], 0.0011265, delta=2e-6)
        self.assertEqual((r['exponent'], r['start']), (2.08, 0.0))

    def test_the_captured_fog_colour_replaces_the_horizon_estimate(self):
        self.assertEqual(self.state()['colour'], cc.frame(NOVALIS)['fog']['colour'])
        self.assertEqual(self.state('atmosphere/atm_veldin/atm_veldin.atmosphere')['colour'], tuple(fog.FOG_COLOUR_SCALE * c for c in (0.4, 0.7, 0.9)))

    def test_other_levels_keep_the_veldin_constants(self):
        r = self.state('atmosphere/atm_veldin/atm_veldin.atmosphere')['ramps']
        self.assertEqual((r['exponent'], r['start']), (fog.FOG_EXPONENT, fog.FOG_START))


if __name__ == '__main__':
    unittest.main()
