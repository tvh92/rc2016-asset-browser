"""Pure validation and camera math tests for the Tk mesh viewport."""
import math
import unittest

from rc2016.viewer.viewport import (MeshPayloadError, back_to_front, batch_centres, camera_fit, forward_order, mesh_bounds,
                       perspective_matrix, split_forward, validate_mesh)


class LeafCutoutShaderTests(unittest.TestCase):
    def test_fragment_and_pick_shaders_discard_below_the_threshold_and_the_uniforms_are_linked(self):
        from rc2016.viewer import shading
        self.assertIn("if (uHasC && c.a < uAlphaCut) discard;", shading.FRAGMENT)
        self.assertIn("uAlphaCut", shading.OPAQUE_UNIFORMS)
        # The cut-out sits right after `_c` is sampled, before any lighting term reads it.
        self.assertLess(shading.FRAGMENT.index("c.a < uAlphaCut"), shading.FRAGMENT.index("vec3 albedo ="))
        self.assertIn("texture2D(uTexC, vUV).a < uPickCut", shading.PICK_FRAGMENT)
        self.assertIn("discard", shading.PICK_FRAGMENT)

    def test_gl_entry_carries_the_cutout(self):
        from rc2016.viewer.viewport import ModelViewport

        class Upload:
            def _upload_texture(self, texture): return 5
        uploaded = ModelViewport._gl_entry(Upload(), dict(alpha_cut=0.5, _c=dict(path='t/leaf_c.texture')), {})
        self.assertEqual(uploaded['alpha_cut'], 0.5)
        self.assertNotIn('alpha_cut', ModelViewport._gl_entry(Upload(), dict(_c=dict(path='t/leaf_c.texture')), {}))


class MeshValidationTests(unittest.TestCase):
    def test_accepts_flat_or_row_vertices_and_builds_vertex_normals(self):
        payload = {"vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0], "triangles": [[0, 1, 2]]}
        vertices, triangles, normals = validate_mesh(payload)
        self.assertEqual(vertices, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
        self.assertEqual(triangles, ((0, 1, 2),))
        self.assertEqual(normals, ((0.0, 0.0, 1.0),) * 3)

    def test_accepts_array_like_and_normalizes_supplied_normals(self):
        class ArrayLike:
            def tolist(self):
                return [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        mesh = {"vertices": ArrayLike(), "triangles": [[0, 1, 2]], "normals": [[0, 0, 4]] * 3}
        _vertices, _triangles, normals = validate_mesh(mesh)
        self.assertEqual(normals, ((0.0, 0.0, 1.0),) * 3)

    def test_rejects_missing_empty_nonfinite_and_malformed_geometry(self):
        cases = [
            ({"triangles": [[0, 1, 2]]}, "missing vertices"),
            ({"vertices": [], "triangles": [[0, 1, 2]]}, "no vertices"),
            ({"vertices": [[0, 0, math.inf]], "triangles": [[0, 0, 0]]}, "non-finite"),
            ({"vertices": [[0, 0, 0]], "triangles": []}, "no triangle faces"),
            ({"vertices": [[0, 0, 0]], "triangles": [[0, 1, 0]]}, "outside the vertex array"),
            ({"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "triangles": [[0, 1, 2]], "normals": [[1, 0, 0]]}, "match the vertex count"),
            ({"vertices": [[0, 0, 0]], "triangles": [[0.5, 0, 0]]}, "non-integral index"),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(MeshPayloadError, expected):
                validate_mesh(payload)

    def test_bounds_cover_asymmetric_and_degenerate_meshes(self):
        center, radius = mesh_bounds(((-2, 0, 0), (4, 0, 0), (1, 4, 0)))
        self.assertEqual(center, (1.0, 2.0, 0.0))
        self.assertAlmostEqual(radius, math.sqrt(13))
        point_center, point_radius = mesh_bounds(((2, 3, 4),))
        self.assertEqual(point_center, (2.0, 3.0, 4.0))
        self.assertGreater(point_radius, 0)


class CameraMathTests(unittest.TestCase):
    def test_camera_fit_contains_full_bounds_with_valid_clip_planes(self):
        vertices = ((-2, -1, 0), (2, 1, 5), (0, 0, 2))
        camera = camera_fit(vertices, aspect=1.7)
        self.assertEqual(camera["center"], (0.0, 0.0, 2.5))
        self.assertGreater(camera["distance"], camera["radius"])
        self.assertGreater(camera["near"], 0)
        self.assertGreater(camera["far"], camera["near"])
        self.assertAlmostEqual(camera["right"], -camera["left"])
        self.assertAlmostEqual(camera["top"] / -camera["bottom"], 1.0)
        zoomed = camera_fit(vertices, aspect=1.7, zoom=2.0)
        self.assertLess(zoomed["distance"], camera["distance"])

    def test_perspective_matrix_maps_depth_and_rejects_invalid_frustum(self):
        camera = camera_fit(((-1, -1, -1), (1, 1, 1)), aspect=1.0)
        matrix = perspective_matrix(camera)
        self.assertEqual(len(matrix), 4)
        self.assertTrue(all(len(row) == 4 for row in matrix))
        self.assertAlmostEqual(matrix[3][2], -1.0)
        self.assertLess(matrix[2][2], -1.0)
        with self.assertRaisesRegex(ValueError, "invalid perspective frustum"):
            perspective_matrix({"left": 1, "right": -1, "bottom": -1, "top": 1, "near": 1, "far": 2})

    def test_camera_fit_rejects_bad_parameters(self):
        for aspect, zoom in ((0, 1), (1, 0), (float("nan"), 1)):
            with self.subTest(aspect=aspect, zoom=zoom), self.assertRaises(ValueError):
                camera_fit(((0, 0, 0),), aspect, zoom)


class GlassPassTests(unittest.TestCase):
    def test_forward_batches_are_split_from_opaque(self):
        textures = {0: {"k": 0.0}, 2: {"forward": {"kind": "glass_patterned"}}}
        opaque, glass = split_forward([(0, 0, 4), (2, 4, 2), (5, 6, 1)], textures)
        self.assertEqual(opaque, [(0, 0, 4), (5, 6, 1)])
        self.assertEqual(glass, [(2, 4, 2)])
        self.assertEqual(split_forward([(0, 0, 1)], None), ([(0, 0, 1)], []))

    def test_glass_batches_draw_farthest_first(self):
        vertices = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 5), (1, 0, 5), (0, 1, 5))
        triangles = ((0, 1, 2), (3, 4, 5))
        batches = [(1, 0, 1), (2, 1, 1)]
        centres = batch_centres(vertices, triangles, batches)
        self.assertAlmostEqual(centres[(1, 1)][2], 5.0)
        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -10, 1]   # camera looks down -z from z = 10
        self.assertEqual(back_to_front(batches, centres, identity), [(1, 0, 1), (2, 1, 1)])
        flipped = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 0, 0, -10, 1]  # seen from the other side
        self.assertEqual(back_to_front(batches, centres, flipped), [(2, 1, 1), (1, 0, 1)])


    def test_opaque_forward_batches_draw_before_blended_ones(self):
        # slot 1 is blended and nearer the camera than the opaque slot 2 (like the Hydrodisplacer's glass shell and liquid):
        # distance order alone would draw the opaque batch last and let it paint over the glass.
        vertices = ((0, 0, 4), (1, 0, 4), (0, 1, 4), (0, 0, 5), (1, 0, 5), (0, 1, 5))
        triangles = ((0, 1, 2), (3, 4, 5))
        batches = [(1, 0, 1), (2, 1, 1)]
        centres = batch_centres(vertices, triangles, batches)
        view = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -10, 1]    # looks down -z from z = 10: slot 2 is nearer
        opaque = lambda slot: slot == 2
        self.assertEqual(back_to_front(batches, centres, view), [(1, 0, 1), (2, 1, 1)])
        self.assertEqual(forward_order(batches, centres, view, opaque), [(2, 1, 1), (1, 0, 1)])
        flipped = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 0, 0, -10, 1]
        self.assertEqual(forward_order(batches, centres, flipped, opaque), [(2, 1, 1), (1, 0, 1)])
        self.assertEqual(forward_order(batches, centres, view, lambda slot: False), back_to_front(batches, centres, view))


if __name__ == "__main__":
    unittest.main()

