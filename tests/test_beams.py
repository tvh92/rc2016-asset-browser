"""Light beams: the mesh of the decoded light-cone placements (rc2016/levels/beams.py; no GL)."""
import math
import unittest

import numpy as np

from rc2016.levels import beams


def cone_model():
    """A cone of apex (0, 0, 0), axis +Z, length 10, base radius 4 as 4 base vertices and one apex vertex, with the UV0 the
    game's cone models have: u from 0 at the apex to 1 at the base, v around."""
    positions = np.array([[0, 0, 0], [4, 0, 10], [0, 4, 10], [-4, 0, 10], [0, -4, 10]], np.float32)
    normals = np.array([[0, 0, -1], [1, 0, 0.4], [0, 1, 0.4], [-1, 0, 0.4], [0, -1, 0.4]], np.float32)
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    uvs = np.array([[0, 0], [1, 0], [1, 0.25], [1, 0.5], [1, 0.75]], np.float32)
    return dict(positions=positions, normals=normals, uvs=uvs, indices=np.array([0, 1, 2, 0, 2, 3, 0, 3, 4, 0, 4, 1], np.uint32))


def cone(position=(0.0, 0.0, 0.0), rows=((1, 0, 0), (0, 1, 0), (0, 0, 1)), colour=(4.0, 2.0, 1.0), opacity=0.5):
    matrix = [*rows[0], 0.0, *rows[1], 0.0, *rows[2], 0.0, *position, 1.0]
    return dict(model='cone.model', matrix=matrix, colour=list(colour), opacity=opacity)


GEOMETRY = {'cone.model': cone_model()}


class BeamMeshTests(unittest.TestCase):
    def test_the_placement_matrix_moves_and_turns_the_model(self):
        # local +Z turned onto world +X (row-vector convention: rows are the local axes), apex at (5, 6, 7)
        positions, *_ = beams.beam_mesh([cone((5.0, 6.0, 7.0), rows=((0, 1, 0), (0, 0, 1), (1, 0, 0)))], GEOMETRY)
        np.testing.assert_allclose(positions[0], [5, 6, 7], atol=1e-5)
        np.testing.assert_allclose(positions[1], [15, 6 + 4, 7], atol=1e-5)    # base: 10 along +X, local x on world y

    def test_the_uvs_are_the_models_and_normals_stay_unit(self):
        _positions, normals, uvs, *_ = beams.beam_mesh([cone()], GEOMETRY)
        np.testing.assert_allclose(uvs, GEOMETRY['cone.model']['uvs'])
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-5)

    def test_colour_is_the_cones_rgb_times_the_gain_and_its_opacity(self):
        colours = beams.beam_mesh([cone(colour=(8.0, 4.0, 2.0), opacity=0.2)], GEOMETRY, gain=0.5)[3]
        self.assertEqual(colours.shape, (5, 4))
        np.testing.assert_allclose(colours[0], [4.0, 2.0, 1.0, 0.2], rtol=1e-6)

    def test_the_gain_is_the_amplitude_times_the_intensity(self):
        self.assertAlmostEqual(beams.BEAM_GAIN, beams.AMPLITUDE * beams.INTENSITY)

    def test_each_cone_adds_its_vertices_and_offsets_its_indices(self):
        one = beams.beam_mesh([cone()], GEOMETRY)
        two = beams.beam_mesh([cone(), cone((50.0, 0.0, 0.0))], GEOMETRY)
        self.assertEqual(len(two[0]), 2 * len(one[0]))
        self.assertEqual(len(two[4]), 2 * len(one[4]))
        self.assertEqual(int(two[4][len(one[4]):].min()), len(one[0]))

    def test_a_scaled_placement_scales_the_cone(self):
        positions, *_ = beams.beam_mesh([cone(rows=((2, 0, 0), (0, 2, 0), (0, 0, 2)))], GEOMETRY)
        np.testing.assert_allclose(positions[1], [8, 0, 20], atol=1e-5)

    def test_no_cones_give_empty_arrays(self):
        positions, normals, uvs, colours, indices = beams.beam_mesh([], {})
        self.assertEqual((len(positions), len(normals), len(uvs), len(colours), len(indices)), (0, 0, 0, 0, 0))
        self.assertEqual((uvs.shape[1:], colours.shape[1:], indices.dtype), ((2,), (4,), np.uint32))

    def test_arrays_are_float32_and_uint32(self):
        positions, normals, uvs, colours, indices = beams.beam_mesh([cone()], GEOMETRY)
        for array in (positions, normals, uvs, colours):
            self.assertEqual(array.dtype, np.float32)
        self.assertEqual(indices.dtype, np.uint32)
        self.assertTrue(math.isfinite(float(positions.sum())))


if __name__ == '__main__':
    unittest.main()
