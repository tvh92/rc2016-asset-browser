"""Water preview rule: family-level coverage and uniform packing (no archive, no GL)."""
import unittest

from rc2016.formats.links import EXACT, rule_coverage
from rc2016.viewer.shading import GLASS_KINDS, glass_uniforms
from rc2016.viewer.textures import WATER_PRESENTATION

WATER = 'materialgraph/visualeffect/environment/pokitaru/unique/pok_uniq_water/pok_uniq_water.materialgraph'
GLOW = 'materialgraph/visualeffect/environment/pokitaru/unique/pok_uniq_water/rp_pok_uniq_water_biolumenscent.materialgraph'


class WaterPreviewTests(unittest.TestCase):
    def test_hash_bound_water_sampling_has_exact_path_coverage(self):
        for graph in (WATER, GLOW):
            kind, prefix = rule_coverage([graph])
            self.assertEqual(kind, EXACT)
            self.assertTrue(prefix.endswith('.'))

    def test_uniforms_pack_body_presentation_and_tiles(self):
        params = dict(WATER_PRESENTATION, body=[0.0, 0.18, 0.22])
        kind, a, b, c, d, e = glass_uniforms(dict(kind='water', params=params))
        self.assertEqual(e, (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(kind, GLASS_KINDS['water'])
        self.assertEqual(a, (0.0, 0.18, 0.22, WATER_PRESENTATION['opacity']))
        self.assertEqual(b[:3], (WATER_PRESENTATION['f0'], WATER_PRESENTATION['normal_strength'], WATER_PRESENTATION['gloss']))
        self.assertEqual(c, WATER_PRESENTATION['uv_a'])
        self.assertEqual(d, WATER_PRESENTATION['uv_b'])


if __name__ == '__main__':
    unittest.main()
