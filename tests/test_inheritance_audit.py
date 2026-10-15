"""Inheritance-rule helpers shared by the viewer and the audit (synthetic rigs only)."""
import unittest

import numpy as np

from rc2016.formats.clips import worlds_batch
from rc2016.formats.skeleton import AFTER_ROTATION, SSC0, bundled_profile, inheritance_rule, rest_inheritance_rule
from rc2016.research.inheritance_audit import basis_shear, bit0_joints, clip_rows, summary
from tests.test_after_rotation_inheritance import arm

SHEEP = 'f7cdfd434c569f75a0abf7f276d58fe573064572effa21d0121e932aee33907d'


class BundledProfiles(unittest.TestCase):
    def test_bundled_profile_is_found_by_model_hash(self):
        profile = bundled_profile(SHEEP)
        self.assertEqual(profile['model_sha256'], SHEEP)
        self.assertEqual(profile['inheritance'], SSC0)
        self.assertEqual(profile['rest_inheritance'], AFTER_ROTATION)
        self.assertIsNone(bundled_profile('0' * 64))

    def test_only_the_sheep_profile_names_a_rest_rule(self):
        from rc2016.formats.skeleton import PROFILE_FOLDER
        import json
        named = {path.stem: json.loads(path.read_text(encoding='utf-8')).get('rest_inheritance')
                 for path in PROFILE_FOLDER.glob('*.json')}
        self.assertEqual({k for k, v in named.items() if v}, {SHEEP})

    def test_inheritance_rule_prefers_profile_then_single_candidate_then_ssc0(self):
        self.assertEqual(inheritance_rule(dict(inheritance=AFTER_ROTATION, inheritance_candidates=['full'])), AFTER_ROTATION)
        self.assertEqual(inheritance_rule(dict(inheritance=None, inheritance_candidates=['full'])), 'full')
        self.assertEqual(inheritance_rule(dict(inheritance=None, inheritance_candidates=['full', SSC0])), SSC0)
        self.assertEqual(inheritance_rule(dict(inheritance=None, inheritance_candidates=[])), SSC0)

    def test_rest_rule_prefers_rest_inheritance_then_the_clip_rule_then_candidates_then_full(self):
        both = dict(inheritance=SSC0, rest_inheritance=AFTER_ROTATION, inheritance_candidates=[])
        self.assertEqual((inheritance_rule(both), rest_inheritance_rule(both)), (SSC0, AFTER_ROTATION))
        self.assertEqual(rest_inheritance_rule(dict(inheritance=SSC0, inheritance_candidates=['full'])), SSC0)
        self.assertEqual(rest_inheritance_rule(dict(inheritance=None, inheritance_candidates=['full', SSC0])), 'full')
        self.assertEqual(rest_inheritance_rule(dict(inheritance=None, inheritance_candidates=[])), 'full')


class BasisShear(unittest.TestCase):
    def worlds(self, rule, parent_scale=(1.2, .9, .8)):
        rows = arm(parent_scale=parent_scale)
        half = np.radians(50) / 2   # a turn about a diagonal axis: the rotation does not map the scale axes onto themselves
        rows[2]['rotation_xyzw'] = [np.sin(half) * np.sqrt(.5), np.sin(half) * np.sqrt(.5), 0., np.cos(half)]
        return worlds_batch(rows, [()], rule)

    def test_ssc0_cancels_a_non_uniform_parent_scale_exactly_and_after_rotation_does_not(self):
        rows = arm()
        self.assertEqual(bit0_joints(rows), [2])
        cosine, condition = basis_shear(self.worlds(SSC0), [2])
        self.assertLess(cosine, 1e-12)
        self.assertLess(condition, 1.5)
        cosine, _ = basis_shear(self.worlds(AFTER_ROTATION), [2])
        self.assertGreater(cosine, .05)

    def test_orders_agree_for_a_uniform_parent_scale(self):
        scale = (1.3, 1.3, 1.3)
        np.testing.assert_allclose(basis_shear(self.worlds(SSC0, scale), [2]), basis_shear(self.worlds(AFTER_ROTATION, scale), [2]), atol=1e-12)

    def test_degenerate_basis_has_a_huge_condition_number(self):
        world = np.tile(np.eye(4), (1, 1, 1, 1))
        world[0, 0, 2, 2] = 0.0
        self.assertGreater(basis_shear(world, [0])[1], 1e9)


class SummaryCounts(unittest.TestCase):
    def test_counts_follow_the_rows(self):
        rest = [dict(path='a', errors={'full': 1.0, SSC0: 2e-3, AFTER_ROTATION: 1e-7}),
                dict(path='b', errors={'full': 1e-6, SSC0: 1e-6, AFTER_ROTATION: 1e-6})]
        rigs = [dict(path='a', clips=[dict(name='x', divergence=.5, ssc0_cos=0., ssc0_cond=1., after_cos=.4, after_cond=500.)])]
        counts = summary(dict(tolerance=.002, rest=rest, rigs=rigs))
        self.assertEqual(counts['rest_fits'], {'full': 1, SSC0: 1, AFTER_ROTATION: 2})
        self.assertEqual(counts['rest_rigs_where_after_rotation_is_strictly_better'], ['a'])
        self.assertEqual((counts['clips_ssc0_less_sheared'], counts['clips_after_rotation_less_sheared']), (1, 0))
        self.assertEqual(counts['clips_after_rotation_condition_over_100'], 1)
        self.assertEqual(counts['rigs_ssc0_median_less_sheared'], 1)

    def test_clip_rows_skips_undecodable_clips(self):
        self.assertEqual(clip_rows([], b'', {}, []), [])


if __name__ == '__main__':
    unittest.main()
