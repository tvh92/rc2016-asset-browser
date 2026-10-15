"""Novalis glow mushroom writer (graph nov_plant_mushroom_glow_fresnel_01, pixel program F69ADC40).

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

GRAPH = ('materialgraph/environment/novalis/plant/nov_plant_mushroom_glow_fresnel_01/'
         'rp_nov_plant_mushroom_glow_fresnel_01.materialgraph')
COLOUR, EXPONENT, SELF_LIT = 0x3DA84DFD, 0x764792E8, 0xA71C1591
DEFAULTS = {COLOUR: [0.38132601976394653, 0.68668532371521, 1.0], EXPONENT: 0.25, SELF_LIT: 2.0}


def row(key, values):
    values = tuple(values) if isinstance(values, (tuple, list)) else (values,)
    return dict(key_hash=key, size=4 * len(values), raw_hex=struct.pack('<%df' % len(values), *values).hex())


def extract(material_rows=(), flag_word=0x230):
    mat = dat1([GRAPH], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(flag_word))])
    archive = FakeArchive({'m/mush.material': ('material', mat), GRAPH: ('materialgraph', dat1([], lambda at: []))})
    params = dict(parameters=[], unsupported_parameters=list(material_rows))
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/mush.material')


class GlowMushroomRuleTests(unittest.TestCase):
    def test_exact_graph_rule(self):
        rule = tp._graph_rule([GRAPH])
        self.assertTrue(rule['fresnel_tint'])
        self.assertEqual((rule['self_lit'], rule['self_lit_key'], rule['g_mode']), ('g_blue', SELF_LIT, 'grey'))
        self.assertEqual(tp._graph_rule([GRAPH.replace('rp_nov_plant_mushroom_glow_fresnel_01.', 'rp_other.')]), {})

    def test_defaults_then_material_values(self):
        _, got = extract()
        f = got['fresnel_tint']
        self.assertEqual(f['colour'], DEFAULTS[COLOUR]); self.assertAlmostEqual(f['exponent'], 0.25)
        self.assertEqual(got['self_lit'], 'g_blue'); self.assertAlmostEqual(got['k'], 2.0)
        self.assertEqual(f['writer'], 'F69ADC40')
        _, got = extract([row(COLOUR, (0.1, 0.2, 0.3)), row(EXPONENT, 1.5)])
        self.assertEqual(got['fresnel_tint']['colour'], [0.10000000149011612, 0.20000000298023224, 0.30000001192092896])
        self.assertAlmostEqual(got['fresnel_tint']['exponent'], 1.5)

    def test_glsl_albedo_branch(self):
        branch = FRAGMENT.split('uFresnelTint)')[1].split('vec3 metalSpecTint')[0]
        for text in ('1.00100004673 - ndv', 'pow(', 'uFresnelE', 'mix(uFresnelC.r, albedo.r, fresnel)',
                     'mix(uFresnelC.g, albedo.g, fresnel)', 'uFresnelC.b'):
            self.assertIn(text, branch)

    def test_viewer_texture_entry_keeps_fresnel_term(self):
        archive, paths = extract()
        with (patch.object(tp, 'material_texture_paths', return_value=paths),
              patch('rc2016.viewer.native_glsl.selected_translation', return_value=None)):
            entry = tp.material_textures(archive, [dict(index=0, path='m/mush.material')], {}, previews=False)[0]
        self.assertEqual(entry['fresnel_tint'], paths['fresnel_tint'])

    def test_viewer_rule_ports_the_term(self):
        archive, _ = extract()
        rule = viewer_graph_rule(archive, 'm/mush.material', set())
        self.assertTrue(rule.get('applies'), rule)
        self.assertIn('fresnel_tint', rule['ported'])
        self.assertNotIn('fresnel_tint', rule.get('unported', []))


class GlowMushroomValidationTests(unittest.TestCase):
    def test_expected_constants_and_required_names(self):
        _, got = extract()
        rule = dict(applies=True, ported=['fresnel_tint'], terms={'fresnel_tint': got['fresnel_tint']},
                    textures={'_c': 'x', '_g': 'y', '_n': 'z'})
        expected = validation._viewer_expected_constants(rule)
        names, roles = validation._viewer_required(rule, True)
        self.assertEqual(sorted(names), ['VR mushroom fresnel colour', 'VR mushroom fresnel exponent'])
        self.assertIn('_c', roles)
        self.assertAlmostEqual(expected['VR mushroom fresnel exponent'], 0.25)
        self.assertEqual(expected['VR mushroom fresnel colour'], DEFAULTS[COLOUR])


if __name__ == '__main__':
    unittest.main()
