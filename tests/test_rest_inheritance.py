"""A profile may name a rest rule separate from its clip rule (synthetic rigs only)."""
import unittest

import numpy as np

from rc2016.formats.skeleton import AFTER_ROTATION, SSC0, check_profile_rules, inverse_bind_error, worlds
from tests.test_after_rotation_inheritance import arm


def bound(rows, rule):
    """Inverse binds of the rest pose under ``rule``."""
    return [np.linalg.inv(m) for m in worlds(rows, inheritance=rule)]


def tilted_arm():
    rows = arm()
    half = np.radians(50) / 2   # a turn about a diagonal axis, so the two operand orders genuinely differ
    rows[2]['rotation_xyzw'] = [np.sin(half) * np.sqrt(.5), np.sin(half) * np.sqrt(.5), 0., np.cos(half)]
    return rows


class ProfileRules(unittest.TestCase):
    def setUp(self):
        self.rows = tilted_arm()
        self.inverses = bound(self.rows, AFTER_ROTATION)

    def test_the_fixture_separates_the_orders_at_rest(self):
        self.assertLess(inverse_bind_error(self.rows, self.inverses, AFTER_ROTATION), 1e-9)
        self.assertGreater(inverse_bind_error(self.rows, self.inverses, SSC0), .01)

    def test_after_rotation_rest_rule_with_ssc0_clip_rule_is_accepted_and_measured(self):
        error = check_profile_rules(dict(inheritance=SSC0, rest_inheritance=AFTER_ROTATION),
                                    self.rows, self.inverses, [])
        self.assertLess(error, 1e-9)

    def test_after_rotation_as_clip_rule_alone_is_still_checked_against_the_binds(self):
        self.assertLess(check_profile_rules(dict(inheritance=AFTER_ROTATION), self.rows, self.inverses, []), 1e-9)
        with self.assertRaises(ValueError):
            check_profile_rules(dict(inheritance=AFTER_ROTATION), self.rows, bound(self.rows, SSC0), [])

    def test_after_rotation_rest_rule_that_misses_the_binds_is_rejected(self):
        with self.assertRaises(ValueError):
            check_profile_rules(dict(inheritance=SSC0, rest_inheritance=AFTER_ROTATION),
                                self.rows, bound(self.rows, SSC0), [])

    def test_ordinary_rest_rule_must_be_an_inverse_bind_candidate(self):
        self.assertIsNone(check_profile_rules(dict(rest_inheritance=SSC0), self.rows, self.inverses, [SSC0]))
        with self.assertRaises(ValueError):
            check_profile_rules(dict(rest_inheritance=SSC0), self.rows, self.inverses, ['full'])

    def test_unknown_rule_names_are_rejected(self):
        for key in ('inheritance', 'rest_inheritance'):
            with self.assertRaises(ValueError):
                check_profile_rules({key: 'nonsense'}, self.rows, self.inverses, [])

    def test_clip_rule_alone_is_not_checked_against_the_binds(self):
        self.assertIsNone(check_profile_rules(dict(inheritance=SSC0), self.rows, self.inverses, []))

    def test_skin_at_the_rest_pose_misses_identity_by_the_rule_difference_only(self):
        # skin = clip world * stored inverse bind: exact when rest and clip rules agree, off by the order difference otherwise.
        exact = np.array(worlds(self.rows, inheritance=AFTER_ROTATION)) @ np.array(self.inverses)
        np.testing.assert_allclose(exact, np.broadcast_to(np.eye(4), exact.shape), atol=1e-9)
        split = np.array(worlds(self.rows, inheritance=SSC0)) @ np.array(self.inverses)
        residue = np.abs(split - np.eye(4)).max()
        difference = np.abs(np.array(worlds(self.rows, inheritance=SSC0)) - np.array(worlds(self.rows, inheritance=AFTER_ROTATION))).max()
        self.assertGreater(residue, 1e-3)
        self.assertLess(residue, 2 * difference + 1e-9)
        np.testing.assert_allclose(split[:2], exact[:2], atol=1e-9)   # joints above the bit-0 joint are unaffected


if __name__ == '__main__':
    unittest.main()
