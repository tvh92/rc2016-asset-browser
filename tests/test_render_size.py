"""Render size is always 100 percent; pure, no Blender."""
import unittest
from types import SimpleNamespace

from rc2016.pipeline import scene_defaults as sd


class FitRenderSizeTests(unittest.TestCase):
    def test_render_percentage_is_pinned_to_one_hundred(self):
        scene = SimpleNamespace(render=SimpleNamespace(resolution_x=3840, resolution_y=2560, resolution_percentage=40))
        receipt = sd.fit_render_size(scene)
        self.assertEqual(scene.render.resolution_percentage, 100)
        self.assertEqual((scene.render.resolution_x, scene.render.resolution_y), (3840, 2560))
        self.assertEqual(receipt, {"status": "full_size", "resolution_percentage": 100})


class RegionPaddingRuleTests(unittest.TestCase):
    def test_longer_pixel_axis_takes_the_long_padding(self):
        # Taller than wide in pixels: vertical long, horizontal side (at least 25 percent).
        self.assertEqual(sd.region_paddings(500, 900), (sd.REGION_PADDING_SIDE, sd.REGION_PADDING_LONG))
        # Wider than tall in pixels, judged on pixels not frame fractions.
        self.assertEqual(sd.region_paddings(900, 500), (sd.REGION_PADDING_SIDE, sd.REGION_PADDING_SHORT))

    def test_camera_and_crop_pad_the_same_axis(self):
        # A box 0.5 wide and 0.6 high of a 1920 x 1080 frame is 960 x 648 px: wider on screen, so the crop
        # pads horizontally by the side value.  The camera fit uses the same paddings, so the padded width
        # is exactly the frame width when horizontal decides the distance.
        pad_x, pad_y = sd.region_paddings(0.5 * 1920, 0.6 * 1080)
        self.assertAlmostEqual(0.5 * (1 + pad_x), 0.5 * 1.25)
        region = sd.padded_region((0.25, 0.2, 0.75, 0.8), (1920, 1080))
        self.assertAlmostEqual(region[2] - region[0], 0.5 * 1.25, places=6)


if __name__ == "__main__":
    unittest.main()
