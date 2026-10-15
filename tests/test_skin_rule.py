"""basic_skin deferred writers 9AAD640B (basic_skin) and 0E6CC87B (basic_skin_02) as viewer rules.

Synthetic data only: no archive, no GPU, no Blender.
"""
import struct
import unittest
from unittest.mock import patch

from rc2016.formats.materials import viewer_graph_rule
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT
from tests.test_viewer_textures import FakeArchive, dat1, flags

SKIN = 'materialgraph/global/basic_skin/basic_skin.materialgraph'
QWARK = 'materialgraph/global/basic_skin/basic_skin_02.materialgraph'
SIBLING = 'materialgraph/global/basic_skin/test_basic_skin_05_newsss_03.materialgraph'
K = tp.SKIN_KEYS
DEFAULTS = {K['cap']: 0.5, K['gloss_fresnel']: 0.5, K['spec_fresnel']: 1.4, K['exponent']: 0.5, K['albedo']: 1.2,
            K['gloss']: 0.5, K['spec']: 0.2, tp.SKIN_QWARK_KEYS['gloss2']: 1.0, tp.SKIN_QWARK_KEYS['spec2']: 1.0,
            tp.SKIN_DETAIL_BLEND_KEY: 0.5, tp.SKIN_DETAIL_UV_KEY: 10.0}


def row(key, value):
    return dict(key_hash=key, size=4, raw_hex=struct.pack('<f', value).hex())


def extract(graph, material_rows=(), flag_word=0x230):
    mat = dat1([graph], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(flag_word))])
    archive = FakeArchive({'m/skin.material': ('material', mat), graph: ('materialgraph', dat1([], lambda at: []))})
    params = dict(parameters=[], unsupported_parameters=list(material_rows))
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/skin.material')


class SkinRuleTests(unittest.TestCase):
    def test_exact_graph_rules(self):
        self.assertEqual((tp._graph_rule([SKIN])['skin'], tp._graph_rule([SKIN])['self_lit_key']), (1, None))
        self.assertEqual(tp._graph_rule([QWARK])['skin'], 2)
        self.assertTrue(tp._graph_rule(['materialgraph/global/basic_skin/skin_emissive.materialgraph'])['skin'])
        sibling = tp._graph_rule([SIBLING])
        self.assertEqual(sibling, dict(g_mode='grey_skin'))   # prefix reading only: its writer is forward-only

    def test_basic_skin_defaults_then_material_values(self):
        _, got = extract(SKIN)
        s = got['skin']
        self.assertEqual((s['kind'], s['writer']), (1, '9AAD640B'))
        self.assertAlmostEqual(s['albedo'], 1.2); self.assertAlmostEqual(s['spec_fresnel'], 1.4)
        self.assertEqual(s['tint'], list(tp.SKIN_FRESNEL_TINT))
        self.assertEqual(got['k'], 0.0)
        _, got = extract(SKIN, [row(K['albedo'], 1.5), row(K['cap'], 0.25)])
        self.assertAlmostEqual(got['skin']['albedo'], 1.5); self.assertAlmostEqual(got['skin']['cap'], 0.25)

    def test_qwark_variant_reads_its_two_extra_keys(self):
        _, got = extract(QWARK, [row(tp.SKIN_QWARK_KEYS['spec2'], 0.5)])
        s = got['skin']
        self.assertEqual((s['kind'], s['writer']), (2, '0E6CC87B'))
        self.assertAlmostEqual(s['spec2'], 0.5); self.assertAlmostEqual(s['gloss2'], 1.0)
        self.assertNotIn('skin_detail', got)   # the second-normal blend is only decoded for basic_skin

    def test_glsl_branches(self):
        albedo = FRAGMENT.split('uSkinKind > 0) {')[1].split('vec3 metalSpecTint')[0] if 'uSkinKind > 0) {' in FRAGMENT else ''
        self.assertTrue(albedo)
        branch = FRAGMENT.split('if (uMode == 2 && uSkinKind > 0)')[1].split('vec3 metalSpecTint')[0]
        for text in ('1.00100004673 - ndv', 'uSkinA.w', 'mix(vec3(uSkinB.x)', '0.745404183864593505859375', 'clamp(skinTinted, 0.0, 1.0)'):
            self.assertIn(text, branch)
        spec = FRAGMENT.split('uGMode == 1 && uSkinKind == 1')[1].split('F0 *= uSpecTint')[0]
        for text in ('min(uSkinA.x, mix(sp, clamp(uSkinA.z * sp', 'skinF', 'uSkinC.x * uSkinB.z', 'uSkinB.w * uSkinB.y', '* skinTinted'):
            self.assertIn(text, spec)
        self.assertIn('uSkinC.z * bngGl.b', FRAGMENT)   # detail weight = blend * _g.B

    def test_viewer_rule_ports_the_terms(self):
        archive, _ = extract(SKIN)
        rule = viewer_graph_rule(archive, 'm/skin.material', set())
        self.assertTrue(rule.get('applies'), rule)
        self.assertIn('skin', rule['ported'])
        self.assertEqual(rule['normal_z'], '1-x*x-y*y')


class SkinValidationTests(unittest.TestCase):
    def test_expected_constants_and_required_names(self):
        for graph, kind, extra in ((SKIN, 1, 0), (QWARK, 2, 2)):
            _, got = extract(graph)
            rule = dict(applies=True, ported=['skin'], terms={'skin': got['skin']}, textures={'_c': 'x', '_g': 'y', '_n': 'z'})
            expected = validation._viewer_expected_constants(rule)
            names, _ = validation._viewer_required(rule, True)
            self.assertEqual(len(names), 8 + extra)
            self.assertTrue(set(names) <= set(expected))
            self.assertAlmostEqual(expected['VR skin fresnel exponent'], 0.5)
            self.assertEqual(expected['VR skin fresnel tint'], list(tp.SKIN_FRESNEL_TINT))
            self.assertEqual('VR skin spec2' in expected, kind == 2)

    def test_g_free_rule_requires_only_the_albedo_constants(self):
        _, got = extract(SKIN)
        rule = dict(applies=True, ported=['skin'], terms={'skin': got['skin']}, textures={'_c': 'x', '_g': None, '_n': None})
        names, _ = validation._viewer_required(rule, False)
        self.assertEqual(sorted(names), ['VR skin albedo scale', 'VR skin fresnel exponent', 'VR skin fresnel tint'])


if __name__ == '__main__':
    unittest.main()
