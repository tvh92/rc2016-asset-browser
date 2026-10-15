"""Automatic render size rule (rc2016/pipeline/scene_defaults texel density maths); pure, no Blender."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from rc2016.pipeline import scene_defaults as sd


def right_triangle(leg):
    return [[0.0, 0.0], [leg, 0.0], [0.0, leg]]


class WeightedMedianTests(unittest.TestCase):
    def test_median_follows_the_weights_not_the_count(self):
        # Three small triangles at 5 and one large one at 1: the large one covers most of the screen.
        self.assertEqual(sd.weighted_median([5, 5, 5, 1], [1, 1, 1, 10]), 1.0)
        self.assertEqual(sd.weighted_median([5, 5, 5, 1], [10, 10, 10, 1]), 5.0)

    def test_equal_weights_give_the_ordinary_median(self):
        self.assertEqual(sd.weighted_median([3.0, 1.0, 2.0], [1, 1, 1]), 2.0)

    def test_no_positive_weight_or_no_values_is_none(self):
        self.assertIsNone(sd.weighted_median([], []))
        self.assertIsNone(sd.weighted_median([1.0, 2.0], [0.0, 0.0]))
        self.assertIsNone(sd.weighted_median([float("nan")], [1.0]))

    def test_zero_weight_values_are_ignored(self):
        self.assertEqual(sd.weighted_median([100.0, 2.0], [0.0, 1.0]), 2.0)


class TriangleTexelDensityTests(unittest.TestCase):
    def density(self, uv_leg, pixel_leg, size=(1000, 1000), flip=False):
        screen = right_triangle(pixel_leg)
        if flip:
            screen = [screen[0], screen[2], screen[1]]
        return sd.triangle_texel_density([right_triangle(uv_leg)], [screen], [size])

    def test_density_is_texels_per_pixel_along_one_axis(self):
        # UV legs 0.05 of a 1000 texel image = 50 texels, over 10 pixel legs: 5 texels per pixel.
        density, pixel_area, facing = self.density(0.05, 10.0)
        self.assertAlmostEqual(density[0], 5.0)
        self.assertAlmostEqual(pixel_area[0], 50.0)
        self.assertTrue(facing[0])

    def test_density_scales_with_texture_size_and_pixel_size(self):
        self.assertAlmostEqual(self.density(0.05, 10.0, (2000, 2000))[0][0], 10.0)
        self.assertAlmostEqual(self.density(0.05, 20.0)[0][0], 2.5)

    def test_non_square_texture_uses_width_times_height(self):
        # 100 x 400 texels: the geometric mean of the edge lengths, 200 texels per unit UV.
        self.assertAlmostEqual(self.density(0.05, 10.0, (100, 400))[0][0], 1.0)

    def test_clockwise_screen_triangles_are_back_facing(self):
        density, pixel_area, facing = self.density(0.05, 10.0, flip=True)
        self.assertFalse(facing[0])
        self.assertAlmostEqual(density[0], 5.0)  # the density itself does not depend on the facing

    def test_degenerate_screen_triangle_has_no_density_or_weight(self):
        density, pixel_area, _ = sd.triangle_texel_density(
            [right_triangle(0.05)], [[[0, 0], [5, 5], [10, 10]]], [(1000, 1000)])
        self.assertEqual((density[0], pixel_area[0]), (0.0, 0.0))

    def test_collapsed_uv_has_zero_density(self):
        density, pixel_area, _ = sd.triangle_texel_density(
            [[[0.5, 0.5]] * 3], [right_triangle(10.0)], [(1000, 1000)])
        self.assertEqual(density[0], 0.0)
        self.assertGreater(pixel_area[0], 0.0)

    def test_many_triangles_are_independent(self):
        uv = np.array([right_triangle(0.05), right_triangle(0.1)])
        screen = np.array([right_triangle(10.0), right_triangle(10.0)])
        density, _, _ = sd.triangle_texel_density(uv, screen, [(1000, 1000), (1000, 1000)])
        np.testing.assert_allclose(density, [5.0, 10.0])


class RenderPercentageTests(unittest.TestCase):
    def test_percentage_is_ceil_of_one_hundred_times_the_metric(self):
        self.assertEqual(sd.render_percentage_for_density(0.736), 74)
        self.assertEqual(sd.render_percentage_for_density(0.5), 50)
        self.assertEqual(sd.render_percentage_for_density(0.5001), 51)
        self.assertEqual(sd.render_percentage_for_density(0.9951), 100)

    def test_exact_percentages_do_not_round_up_from_float_noise(self):
        self.assertEqual(sd.render_percentage_for_density(0.29), 29)  # 100 * 0.29 = 28.999999999999996
        self.assertEqual(sd.render_percentage_for_density(0.57), 57)  # 100 * 0.57 = 56.99999999999999
        self.assertEqual(sd.render_percentage_for_density(0.58), 58)  # 100 * 0.58 = 57.99999999999999

    def test_it_never_exceeds_one_hundred(self):
        for metric in (1.0, 1.0001, 1.7, 40.0, float("inf")):
            self.assertEqual(sd.render_percentage_for_density(metric), 100)

    def test_it_never_goes_below_twenty_five(self):
        for metric in (0.0, 0.01, 0.2, 0.2499):
            self.assertEqual(sd.render_percentage_for_density(metric), 25)
        self.assertEqual(sd.render_percentage_for_density(0.25), 25)
        self.assertEqual(sd.render_percentage_for_density(0.26), 26)

    def test_no_textured_mesh_or_bad_metric_keeps_full_size(self):
        self.assertEqual(sd.render_percentage_for_density(None), 100)
        self.assertEqual(sd.render_percentage_for_density(float("nan")), 100)


class ExpectedPercentageTests(unittest.TestCase):
    def test_scenes_without_a_render_size_record_are_expected_at_one_hundred(self):
        self.assertEqual(sd.expected_render_percentage(None), 100)
        self.assertEqual(sd.expected_render_percentage(""), 100)
        self.assertEqual(sd.expected_render_percentage(json.dumps({"region": [0, 0, 1, 1]})), 100)

    def test_recorded_percentage_is_expected(self):
        text = json.dumps({"region": [0, 0, 1, 1], "render_size": {"resolution_percentage": 74}})
        self.assertEqual(sd.expected_render_percentage(text), 74)

    def test_malformed_records_never_match(self):
        for record in ({"resolution_percentage": 24}, {"resolution_percentage": 101}, {"resolution_percentage": 50.5},
                       {"resolution_percentage": True}, {"resolution_percentage": "74"}, {}, "74"):
            text = json.dumps({"render_size": record})
            self.assertIsNone(sd.expected_render_percentage(text), record)
        self.assertIsNone(sd.expected_render_percentage("{not json"))


class FitRenderSizeTests(unittest.TestCase):
    def scene(self):
        camera = SimpleNamespace(type="CAMERA")
        render = SimpleNamespace(resolution_x=3840, resolution_y=2560, resolution_percentage=100)
        return SimpleNamespace(camera=camera, render=render)

    def fit(self, density, weight, material=None, names=("a", "b")):
        samples = {"density": np.asarray(density, float), "weight": np.asarray(weight, float),
                   "material": np.asarray(material if material is not None else [0] * len(density), dtype=np.int64),
                   "materials": list(names), "triangles": 10}
        scene = self.scene()
        with patch.object(sd, "visible_texel_density", return_value=samples):
            receipt = sd.fit_render_size(scene)
        return scene, receipt

    def test_low_density_scales_the_render_and_leaves_resolution_alone(self):
        scene, receipt = self.fit([0.5, 0.6, 3.0], [10, 10, 1])
        self.assertEqual(scene.render.resolution_percentage, 60)
        self.assertEqual((scene.render.resolution_x, scene.render.resolution_y), (3840, 2560))
        self.assertEqual(receipt["status"], "scaled")
        self.assertEqual(receipt["resolution_percentage"], 60)
        self.assertAlmostEqual(receipt["metric_texel_per_pixel_at_100"], 0.6)
        self.assertAlmostEqual(receipt["texel_per_pixel_after"], 1.0)
        json.dumps(receipt)  # the receipt is embedded as JSON

    def test_dense_textures_keep_full_size(self):
        scene, receipt = self.fit([1.5, 2.0], [1, 1])
        self.assertEqual(scene.render.resolution_percentage, 100)
        self.assertEqual(receipt["status"], "full_size_texel_density_at_least_1")

    def test_very_low_density_stops_at_the_minimum(self):
        scene, receipt = self.fit([0.05], [1])
        self.assertEqual(scene.render.resolution_percentage, 25)
        self.assertEqual(receipt["status"], "scaled_to_minimum")

    def test_no_textured_mesh_resets_to_full_size(self):
        scene = self.scene()
        scene.render.resolution_percentage = 40
        samples = {"density": np.empty(0), "weight": np.empty(0), "material": np.empty(0, dtype=np.int64),
                   "materials": [], "triangles": 0}
        with patch.object(sd, "visible_texel_density", return_value=samples):
            receipt = sd.fit_render_size(scene)
        self.assertEqual(scene.render.resolution_percentage, 100)
        self.assertEqual(receipt["status"], "full_size_no_textured_mesh")
        self.assertIsNone(receipt["metric_texel_per_pixel_at_100"])

    def test_receipt_lists_materials_by_screen_share(self):
        _, receipt = self.fit([0.5, 2.0], [3, 1], material=[1, 0], names=("small", "large"))
        self.assertEqual([m["material"] for m in receipt["materials"]], ["large", "small"])
        self.assertAlmostEqual(receipt["materials"][0]["screen_share"], 0.75)
        self.assertAlmostEqual(receipt["materials"][1]["median_texel_per_pixel"], 2.0)

    def test_scene_without_a_camera_is_an_error(self):
        scene = SimpleNamespace(camera=None, render=SimpleNamespace(resolution_percentage=100))
        with self.assertRaises(RuntimeError):
            sd.fit_render_size(scene)

    def test_receipt_percentage_passes_the_audit_expectation(self):
        _, receipt = self.fit([0.5], [1])
        text = json.dumps({"render_size": receipt})
        self.assertEqual(sd.expected_render_percentage(text), receipt["resolution_percentage"])


if __name__ == "__main__":
    unittest.main()
