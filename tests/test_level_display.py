"""The captured game display chain of the Level atmosphere lighting (rc2016.levels.display; no GL)."""
import re
import unittest

import numpy as np

from rc2016.levels import display
from rc2016.levels.viewport import LevelViewport
from rc2016.viewer import shading
from rc2016.viewer.viewport import ModelViewport


class CurveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.curve = display.read_curve()

    def test_curve_reproduces_the_captured_mid_grey_and_is_monotonic(self):
        # rc_game_view.json "mid_grey_018_out": the unexposed 0.18 through the table (fixes the log2 [-8, 5] domain)
        self.assertAlmostEqual(float(display.tone(np.array(0.18), self.curve)), 0.014100135469496514, places=7)
        self.assertTrue((np.diff(self.curve) >= 0).all())
        self.assertEqual((self.curve[0], self.curve[-1]), (0.0, 1.0))

    def test_exposure_constant_matches_the_captured_view(self):
        self.assertEqual(display.EXPOSURE_MULTIPLIER, display.exposure_from_view_json())
        found = re.search(r'const float GAME_EXPOSURE = ([0-9.]+);', shading.LIGHTING)
        self.assertEqual(float(found.group(1)), display.EXPOSURE_MULTIPLIER)

    def test_decimated_curve_stays_within_a_few_1e_4(self):
        x = np.logspace(-3, 0.8, 400)
        small = display.decimate_curve(self.curve)
        self.assertLess(float(np.abs(display.tone(x, small) - display.tone(x, self.curve)).max()), 5e-4)

    def test_game_curve_is_darker_in_the_mid_tones_than_the_aces_fit(self):
        x = np.array(0.18)
        game = float(display.srgb_encode(display.tone(x * display.EXPOSURE_MULTIPLIER, self.curve)))
        aces = float((x * (2.51 * x + 0.03) / (x * (2.43 * x + 0.59) + 0.14)) ** (1 / 2.2))
        self.assertLess(game, aces - 0.1)


def raw_grade(grade_bgr):
    """R8G8B8A8 bytes of a (b, g, r, 3) float grade."""
    out = np.zeros(grade_bgr.shape[:3] + (4,), np.uint8)
    out[..., :3] = np.round(grade_bgr * 255)
    out[..., 3] = 255
    return out.tobytes()


class GradeTests(unittest.TestCase):
    def test_layout_is_red_fastest_then_green_then_blue(self):
        grade = display.identity_grade()
        got = display.grade_from_bytes(raw_grade(grade))
        self.assertLess(float(np.abs(got - grade).max()), 1 / 255)
        self.assertAlmostEqual(float(got[0, 0, 31, 0]), 1.0)     # red varies along the last axis
        self.assertAlmostEqual(float(got[0, 31, 0, 1]), 1.0)
        self.assertAlmostEqual(float(got[31, 0, 0, 2]), 1.0)
        header = b'\0' * display.GRADE_OFFSET + raw_grade(grade) + b'\0' * 64
        self.assertTrue(np.array_equal(display.grade_from_header(header), got))

    def test_identity_grade_is_a_no_op_and_interpolates_trilinearly(self):
        rgb = np.random.default_rng(1).random((200, 3))
        self.assertLess(float(np.abs(display.apply_grade(rgb, display.identity_grade()) - rgb).max()), 1e-9)
        grade = display.identity_grade() * 0.5
        self.assertLess(float(np.abs(display.apply_grade(rgb, grade) - rgb * 0.5).max()), 1e-9)

    def test_table_packs_the_slabs_side_by_side_and_the_curve_in_the_last_row(self):
        grade = np.random.default_rng(2).random((32, 32, 32, 3))
        table = display.display_table(grade)
        self.assertEqual(table.shape, (display.TABLE_HEIGHT, display.TABLE_WIDTH, 3))
        for b, g, r in ((0, 0, 0), (5, 17, 30), (31, 31, 31), (12, 3, 9)):
            np.testing.assert_allclose(table[g, 32 * b + r], grade[b, g, r], atol=1e-6)
        curve = table[display.GRADE_SIZE, :display.CURVE_SAMPLES, 0]
        self.assertEqual((curve[0], curve[-1]), (0.0, 1.0))
        np.testing.assert_array_equal(table[display.GRADE_SIZE, :display.CURVE_SAMPLES, 1], curve)

    def test_cpu_display_chain(self):
        identity = display.identity_grade()
        self.assertLess(float(display.display(np.zeros(3), identity).max()), 1e-6)
        np.testing.assert_allclose(display.display(np.full(3, 50.0), identity), 1.0, atol=1e-6)
        ramp = display.display(np.stack([np.linspace(0.001, 2.0, 50)] * 3, -1), identity)[:, 0]
        self.assertTrue((np.diff(ramp) >= 0).all())


class ShaderContractTests(unittest.TestCase):
    def test_shader_geometry_matches_the_table_layout(self):
        src = shading.LIGHTING
        self.assertIn(f'(u * {float(display.CURVE_SAMPLES - 1)} + 0.5) / {float(display.CURVE_SAMPLES)}', src)
        self.assertIn(f'{display.GRADE_SIZE + 0.5} / {float(display.TABLE_HEIGHT)}', src)
        self.assertIn('vec3 p = clamp(c, 0.0, 1.0) * 31.0;', src)
        self.assertIn('/ 13.0', src)    # log2 domain [-8, 5]
        self.assertEqual(display.CURVE_STOPS, (-8.0, 5.0))
        self.assertIn('uGradeTex', shading.LIGHTING_UNIFORMS)
        self.assertGreater(shading.GRADE_TEXTURE_UNIT, 17)

    def test_the_sky_uses_the_game_chain_only_in_that_mode(self):
        self.assertIn('if (uTonemap == 2) return display(color);', shading.SKY_FRAGMENT)
        self.assertIn('if (uTonemap == 2) return gameDisplay(color);', shading.LIGHTING)


class ViewportTests(unittest.TestCase):
    def test_only_the_level_viewer_offers_the_game_tonemap(self):
        self.assertEqual(LevelViewport.TONEMAPS[ModelViewport.GAME_TONEMAP], 'Game (captured)')
        self.assertNotIn('Game (captured)', ModelViewport.TONEMAPS)
        self.assertEqual(ModelViewport.TONEMAPS, LevelViewport.TONEMAPS[:2])    # the other indices are unchanged

    def test_display_table_key_follows_the_level_grade(self):
        from tests.test_level_atmosphere_lighting import level_view
        self.assertIsNone(level_view(None)._display_table_key())
        self.assertIsNone(level_view(dict(grade=None))._display_table_key())
        view = level_view(dict(grade=raw_grade(display.identity_grade())))
        self.assertIsNotNone(view._display_table_key())
        self.assertEqual(view._display_table_build().shape, (display.TABLE_HEIGHT, display.TABLE_WIDTH, 3))
        self.assertIsNone(ModelViewport._display_table_key(None))


if __name__ == '__main__':
    unittest.main()
