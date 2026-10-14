"""Independent known matrices and source singular/fast-path boundaries."""
import math
import unittest

from rc2016.shaders.instance_transform import instance_vector_matrix


def matrix(axes):
    return [v for a in axes for v in (*a, 0.)] + [7., 8., 9., 1.]


class InstanceVectorMatrixTests(unittest.TestCase):
    def test_nonuniform_scale_and_reflection_preserve_inverse_magnitudes(self):
        for x in (2., -2.):
            got = instance_vector_matrix(matrix(((x,0,0),(0,3,0),(0,0,4))), copy_linear=False)
            expected = (1./x,0,0,0,0,1./3,0,0,0,0,.25,0)
            for a,b in zip(got, expected):
                self.assertAlmostEqual(a,b,places=7)

    def test_shear_uses_inverse_transpose_in_axis_column_convention(self):
        got = instance_vector_matrix(matrix(((1,0,0),(2,1,0),(0,0,1))), copy_linear=False)
        self.assertEqual(got, (1.,-2.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.))

    def test_explicit_fast_path_copies_scaled_axes_and_zeros_padding(self):
        values = matrix(((2,.5,-.25),(0,3,.75),(0,0,.5)))
        values[3], values[7], values[11] = 3.,4.,5.
        got = instance_vector_matrix(values, copy_linear=True)
        self.assertEqual(got, (2.,.5,-.25,0.,0.,3.,.75,0.,0.,0.,.5,0.))

    def test_translation_is_not_part_of_the_vector_transform(self):
        values = matrix(((2,0,0),(0,3,0),(0,0,4)))
        before = instance_vector_matrix(values, copy_linear=False)
        values[12:16] = [-100., 250., -900., 1.]
        self.assertEqual(instance_vector_matrix(values, copy_linear=False), before)

    def test_singular_and_near_singular_fallback_retains_cofactors(self):
        for x in (0., 2.**-21, -2.**-21):
            got = instance_vector_matrix(matrix(((x,0,0),(0,1,0),(0,0,1))), copy_linear=False)
            self.assertEqual(got, (1.,0.,0.,0.,0.,x,0.,0.,0.,0.,x,0.))

    def test_exact_positive_and_negative_threshold_divide(self):
        for x in (2.**-20, -2.**-20):
            got = instance_vector_matrix(matrix(((x,0,0),(0,1,0),(0,0,1))), copy_linear=False)
            self.assertEqual(got, (1./x,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.))

    def test_missing_selector_bad_size_nonfinite_and_overflow_fail_closed(self):
        good = matrix(((1,0,0),(0,1,0),(0,0,1)))
        with self.assertRaises(TypeError):
            instance_vector_matrix(good)
        for values, selector in ((good,1),(good[:-1],False),([math.inf]*16,False),
                                 ([math.nan]*16,True),([1.e30]*16,False)):
            with self.subTest(selector=selector, value=values[0]):
                with self.assertRaises(ValueError):
                    instance_vector_matrix(values, copy_linear=selector)


if __name__ == '__main__':
    unittest.main()
