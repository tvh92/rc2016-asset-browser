"""Hair_NewSpec_01 forward colour pass (pixel program 94CE8CCE).

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

GRAPH = 'required/materials/hair_newspec_01.materialgraph'
K = tp.HAIR_KEYS
DEFAULTS = {K['albedo']: list(tp.HAIR_DEFAULTS['albedo']), K['spec']: list(tp.HAIR_DEFAULTS['spec']),
            K['rough_a']: tp.HAIR_DEFAULTS['rough_a'], K['rough_b']: tp.HAIR_DEFAULTS['rough_b'], K['rim']: tp.HAIR_DEFAULTS['rim']}


def row(key, values):
    values = tuple(values) if isinstance(values, (tuple, list)) else (values,)
    return dict(key_hash=key, size=4 * len(values), raw_hex=struct.pack('<%df' % len(values), *values).hex())


def extract(material_rows=(), flag_word=0x4):
    mat = dat1([GRAPH], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(flag_word))])
    archive = FakeArchive({'m/hair.material': ('material', mat), GRAPH: ('materialgraph', dat1([], lambda at: []))})
    params = dict(parameters=[], unsupported_parameters=list(material_rows))
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/hair.material')


class HairRuleTests(unittest.TestCase):
    def test_exact_graph_rule(self):
        rule = tp._graph_rule([GRAPH])
        self.assertTrue(rule['hair'] and rule['alpha_cut'])
        self.assertEqual((rule['g_mode'], rule['self_lit_key']), ('grey', None))
        self.assertEqual(tp._graph_rule(['required/materials/hair_other.materialgraph']), {})

    def test_slots_are_the_serialized_graph_keys(self):
        # key | type hash 0x49A7866B << 32, from the graph's section 0x1CAFE804 (c, g, n)
        self.assertEqual(tp.HAIR_SLOTS, {'_c': 0x49A7866B1776B09C, '_g': 0x49A7866B6F88A378, '_n': 0x49A7866BE189D5CA})
        self.assertEqual([k & 0xFFFFFFFF for k in tp.HAIR_SLOTS.values()], [393654428, 1871225720, 3783906762])

    def test_defaults_then_material_values(self):
        _, got = extract()
        h = got['hair']
        self.assertEqual(h['albedo'], DEFAULTS[K['albedo']]); self.assertEqual(h['spec'], DEFAULTS[K['spec']])
        self.assertAlmostEqual(h['rim_exponent'], 3.0); self.assertEqual(h['writer'], '94CE8CCE')
        self.assertAlmostEqual(h['gloss_scale'], 0.71); self.assertAlmostEqual(h['normal_strength'], 0.3)
        # sigma = 0.4 max + 0.6 min of 2^(-5.5 sqrt(g)) for the two roughness values
        a, b = 2.0 ** (-5.5 * 0.85 ** 0.5), 2.0 ** (-5.5 * 0.01 ** 0.5)
        self.assertAlmostEqual(h['diffuse_sigma'], 0.4 * max(a, b) + 0.6 * min(a, b), places=5)
        self.assertEqual((got['spec'], got['gloss'], got['k']), (1.0, 1.0, 0.0))
        _, got = extract([row(K['albedo'], (0.1, 0.2, 0.3)), row(K['rim'], 5.0), row(0x62835AF9, 0.4)])
        self.assertEqual(got['hair']['albedo'], [0.10000000149011612, 0.20000000298023224, 0.30000001192092896])
        self.assertAlmostEqual(got['hair']['rim_exponent'], 5.0)
        self.assertEqual(got['spec'], 1.0)   # the stale generic specular scale is not read by this writer

    def test_alpha_cut_comes_from_the_material_state(self):
        _, got = extract()
        self.assertIn('alpha_cut', got)

    def test_glsl_branches(self):
        for text in ('uHair', 'albedo = uHairA', 'rg *= uHairP.w', 'uHairB * gl.g',
                     'gl.r * clamp(uHairP.y * hairW', 'metalSpecTint = vec3(hairW)', '1.00100004673 - clamp(dot('):
            self.assertIn(text, FRAGMENT)

    def test_viewer_rule_ports_the_term(self):
        archive, _ = extract()
        rule = viewer_graph_rule(archive, 'm/hair.material', set())
        self.assertTrue(rule.get('applies'), rule)
        self.assertIn('hair', rule['ported'])
        self.assertEqual(rule['normal_z'], '1-x*x-y*y')


class HairValidationTests(unittest.TestCase):
    def test_expected_constants_and_required_names(self):
        _, got = extract()
        rule = dict(applies=True, ported=['hair'], terms={'hair': got['hair']}, textures={'_c': 'x', '_g': 'y', '_n': 'z'})
        expected = validation._viewer_expected_constants(rule)
        names, roles = validation._viewer_required(rule, True)
        self.assertEqual(sorted(names), sorted('VR ' + n for n in (
            'hair albedo', 'hair spec colour', 'hair rim exponent', 'hair gloss scale', 'hair normal strength')))
        self.assertIn('_g', roles)
        self.assertAlmostEqual(expected['VR hair rim exponent'], 3.0)
        self.assertEqual(expected['VR hair spec colour'], DEFAULTS[K['spec']])


if __name__ == '__main__':
    unittest.main()
