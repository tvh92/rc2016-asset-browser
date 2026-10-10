"""Pure helpers of the level and model viewports: range merging, pick ids, light frames and shader
source contracts. No window is opened; the Tk-dependent imports are skipped when Tk is missing."""
import unittest

try:
    from rc2016.levels.viewport import merge_ranges, pick_colour, pick_index
    from rc2016.viewer.viewport import RIG_TO_WORLD, column_major3, light_frames
    from rc2016.viewer import shading
    IMPORT_ERROR = None
except ImportError as exc:   # no Tk / no ctypes.wintypes on this platform
    IMPORT_ERROR = exc

IDENTITY16 = [1.0 if i % 5 == 0 else 0.0 for i in range(16)]


def scaled_identity16(scale):
    return [scale if i % 5 == 0 and i < 15 else (1.0 if i == 15 else 0.0) for i in range(16)]


@unittest.skipIf(IMPORT_ERROR is not None, f'viewport modules need Tk/ctypes: {IMPORT_ERROR}')
class MergeRangesTests(unittest.TestCase):
    def test_contiguous_ranges_merge_and_order_is_kept(self):
        self.assertEqual(merge_ranges([(0, 3), (3, 2), (9, 1)]), [(0, 5), (9, 1)])

    def test_empty(self):
        self.assertEqual(merge_ranges([]), [])


@unittest.skipIf(IMPORT_ERROR is not None, f'viewport modules need Tk/ctypes: {IMPORT_ERROR}')
class PickIdTests(unittest.TestCase):
    def test_index_zero_is_first_red_step(self):
        self.assertEqual(pick_colour(0), (1 / 255, 0, 0))

    def test_round_trip_through_bytes(self):
        for index in (0, 255, 256, 70000):
            with self.subTest(index=index):
                self.assertEqual(pick_index(bytes(round(c * 255) for c in pick_colour(index))), index)

    def test_background_is_none(self):
        self.assertIsNone(pick_index(b'\x00\x00\x00'))


@unittest.skipIf(IMPORT_ERROR is not None, f'viewport modules need Tk/ctypes: {IMPORT_ERROR}')
class LightFrameTests(unittest.TestCase):
    def test_identity_modelview_gives_identity_frames(self):
        view_to_world, rig = light_frames(IDENTITY16)
        identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        self.assertEqual(view_to_world, identity)
        self.assertEqual(rig, identity)

    def test_rig_fixed_to_world_maps_rig_to_blender_world(self):
        _view_to_world, rig = light_frames(IDENTITY16, rig_fixed_to_world=True)
        self.assertEqual(rig, RIG_TO_WORLD)

    def test_uniform_scale_is_removed(self):
        plain = light_frames(IDENTITY16, rig_fixed_to_world=True)
        scaled = light_frames(scaled_identity16(2.0), rig_fixed_to_world=True)
        self.assertEqual(scaled, plain)

    def test_column_major3_transposes_row_major_input(self):
        self.assertEqual(column_major3(((1, 2, 3), (4, 5, 6), (7, 8, 9))), [1, 4, 7, 2, 5, 8, 3, 6, 9])


@unittest.skipIf(IMPORT_ERROR is not None, f'viewport modules need Tk/ctypes: {IMPORT_ERROR}')
class ShaderSourceTests(unittest.TestCase):
    def test_lighting_placeholder_is_replaced_in_both_fragment_shaders(self):
        self.assertNotIn('// @lighting', shading.FRAGMENT)
        self.assertNotIn('// @lighting', shading.GLASS_FRAGMENT)
        self.assertIn('uniform int uLighting', shading.FRAGMENT)
        self.assertIn('uniform int uLighting', shading.GLASS_FRAGMENT)

    def test_lighting_uniforms_are_declared_for_opaque_and_glass_programs(self):
        for name in shading.LIGHTING_UNIFORMS:
            self.assertIn(name, shading.OPAQUE_UNIFORMS)
            self.assertIn(name, shading.GLASS_UNIFORMS)

    def test_pick_shader_writes_pick_colour(self):
        self.assertIn('uPickColour', shading.PICK_FRAGMENT)


if __name__ == '__main__':
    unittest.main()
