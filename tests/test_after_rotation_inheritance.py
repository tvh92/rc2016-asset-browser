"""The profile-only bit-0 rule that divides the parent scale out after the rotation (synthetic rigs only)."""
import unittest
import zlib

import numpy as np

from rc2016.formats.clips import worlds_batch
from rc2016.formats.skeleton import (AFTER_ROTATION, INVERSE_BIND_TOLERANCE, check_profile_inheritance,
                                     inverse_bind_error, rotation, worlds)
from tests.test_perf_wp2 import make_frames, make_rig

SSC0 = 'segment_scale_compensation_bit0'


def quarter_turn_z():
    return [0., 0., np.sqrt(.5), np.sqrt(.5)]


def joint(name, parent, flags, scale, rotation_xyzw, translation):
    return dict(name=name, hash=f'0x{zlib.crc32(name.encode()):08X}', parent=parent, flags=flags, scale=list(scale),
                rotation_xyzw=list(rotation_xyzw), translation=list(translation))


def arm(child_flags=1, parent_scale=(1.2, .9, .8)):
    """root -> upper (non-uniform scale, turned) -> lower (turned, own scale)."""
    return [joint('root', -1, 0, (1, 1, 1), (0, 0, 0, 1), (0, 0, 0)),
            joint('upper', 0, 0, parent_scale, quarter_turn_z(), (1, 0, 0)),
            joint('lower', 1, child_flags, (1.1, 1.0, .95), (0, np.sqrt(.5), 0, np.sqrt(.5)), (0, 2, 0))]


class AfterRotationWorlds(unittest.TestCase):
    def test_bit0_basis_is_rotation_times_scale_over_parent_scale(self):
        rows = arm()
        world = worlds(rows, inheritance=AFTER_ROTATION)
        local = np.eye(4)
        local[:3, :3] = rotation(rows[2]['rotation_xyzw']) @ np.diag(np.array(rows[2]['scale']) / rows[1]['scale'])
        local[:3, 3] = rows[2]['translation']
        np.testing.assert_allclose(world[2], world[1] @ local, atol=1e-12)

    def test_differs_from_default_ssc0_only_for_non_uniform_parent_scale(self):
        rows = arm()
        self.assertGreater(np.abs(np.array(worlds(rows, inheritance=AFTER_ROTATION)) -
                                  np.array(worlds(rows, inheritance=SSC0))).max(), 1e-3)
        rows = arm(parent_scale=(1.3, 1.3, 1.3))
        np.testing.assert_allclose(worlds(rows, inheritance=AFTER_ROTATION), worlds(rows, inheritance=SSC0), atol=1e-12)

    def test_joints_without_bit0_follow_full_inheritance(self):
        rows = arm(child_flags=2)
        np.testing.assert_allclose(worlds(rows, inheritance=AFTER_ROTATION), worlds(rows, inheritance='full'), atol=1e-12)

    def test_zero_parent_scale_collapses_instead_of_dividing(self):
        rows = arm(parent_scale=(0., 1., 1.))
        world = np.array(worlds(rows, inheritance=AFTER_ROTATION))
        self.assertTrue(np.isfinite(world).all())

    def test_sampled_parent_scale_is_the_one_divided_out(self):
        rows = arm()
        sample = [dict(hash=rows[1]['hash'], scale=[2., 1., 1.], rotation_xyzw=rows[1]['rotation_xyzw'],
                       translation=rows[1]['translation'])]
        world = worlds(rows, sample, AFTER_ROTATION)
        local = np.eye(4)
        local[:3, :3] = rotation(rows[2]['rotation_xyzw']) @ np.diag(np.array(rows[2]['scale']) / [2., 1., 1.])
        local[:3, 3] = rows[2]['translation']
        np.testing.assert_allclose(world[2], world[1] @ local, atol=1e-12)

    def test_batch_matches_worlds(self):
        rng = np.random.default_rng(7)
        rows = make_rig(rng, 14)
        frames = make_frames(rng, rows, 5, drop={3, 7}, sentinel={2, 9})
        batch = worlds_batch(rows, frames, AFTER_ROTATION)
        for f, frame in enumerate(frames):
            np.testing.assert_allclose(batch[f], np.array(worlds(rows, frame, AFTER_ROTATION)), atol=1e-12)
        np.testing.assert_allclose(worlds_batch(rows, [()], AFTER_ROTATION)[0],
                                   np.array(worlds(rows, (), AFTER_ROTATION)), atol=1e-12)

    def test_batch_zero_parent_scale(self):
        rng = np.random.default_rng(8)
        rows = make_rig(rng, 4)
        rows[1].update(parent=0, flags=1)
        frames = make_frames(rng, rows, 2)
        frames[0][0]['scale'] = [0.0, 1.0, 1.0]
        np.testing.assert_allclose(worlds_batch(rows, frames, AFTER_ROTATION)[0],
                                   np.array(worlds(rows, frames[0], AFTER_ROTATION)), atol=1e-12)


class ProfileFitCheck(unittest.TestCase):
    def inverses(self, rows, rule):
        return [np.linalg.inv(m).astype(np.float32).astype(float) for m in worlds(rows, inheritance=rule)]

    def test_rule_that_generated_the_binds_fits_and_the_default_does_not(self):
        rows = arm(parent_scale=(1.0032, .9992, .9992))
        rows[2]['translation'] = [-.54, 0., 0.]
        binds = self.inverses(rows, AFTER_ROTATION)
        self.assertLess(check_profile_inheritance(rows, binds, AFTER_ROTATION), 1e-5)
        self.assertGreater(inverse_bind_error(rows, binds, SSC0), INVERSE_BIND_TOLERANCE / 4)
        self.assertGreater(inverse_bind_error(rows, binds, 'full'), INVERSE_BIND_TOLERANCE / 4)

    def test_misfit_missing_and_mismatched_binds_are_rejected(self):
        rows = arm()
        for binds in (self.inverses(rows, 'full'), [], self.inverses(rows, AFTER_ROTATION)[:2]):
            with self.assertRaises(ValueError):
                check_profile_inheritance(rows, binds, AFTER_ROTATION)


if __name__ == '__main__':
    unittest.main()
