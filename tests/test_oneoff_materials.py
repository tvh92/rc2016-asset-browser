"""One-off material rules: basic_emissive (flat surface), the green goo floor, the coloured-gloss detail emissive
writer (bng kind 10) and the Shiv/Qwark prisoner number forward displays.

Synthetic data only: no archive, no GPU, no Blender.
"""
import struct
import unittest
from unittest.mock import patch

from rc2016.formats.materials import viewer_graph_rule
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT, GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms
from tests.test_viewer_textures import FakeArchive, dat1, flags

EMISSIVE = 'materialgraph/global/basic_emissive/basic_emissive.materialgraph'
GOO = 'materialgraph/environment/nebula/ambient/neb_amb_lava_fissure_blast_01/enm_blarg_green_goo.materialgraph'
DETAIL = 'required/materials/basic_normal_coloredgloss_detail_emissive.materialgraph'
SHIV = 'materialgraph/character/enemy/enm_shiv_prisoner_number/enm_shiv_prisoner_number.materialgraph'
QWARK = 'materialgraph/character/npc/npc_qwark_prisoner_number/npc_qwark_prisoner_number.materialgraph'


def row(key, values):
    values = tuple(values) if isinstance(values, (tuple, list)) else (values,)
    return dict(key_hash=key, size=4 * len(values), raw_hex=struct.pack('<%df' % len(values), *values).hex())


def extract(graph, defaults, material_rows=(), flag_word=0x230):
    mat = dat1([graph], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(flag_word))])
    archive = FakeArchive({'m/x.material': ('material', mat), graph: ('materialgraph', dat1([], lambda at: []))})
    params = dict(parameters=[], unsupported_parameters=list(material_rows))
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/x.material')


class RuleMatchTests(unittest.TestCase):
    def test_forward_kind_ids_are_unique_and_noncolliding(self):
        self.assertEqual(GLASS_KINDS["eye_cornea"], 6)
        self.assertEqual(GLASS_KINDS["number_display"], 7)
        self.assertEqual(len(GLASS_KINDS.values()), len(set(GLASS_KINDS.values())))

    def test_exact_graphs(self):
        self.assertEqual(tp._graph_rule([EMISSIVE])['flat_surface'], 'emissive')
        self.assertEqual(tp._graph_rule([GOO])['flat_surface'], 'goo_floor')
        self.assertEqual(tp._graph_rule([DETAIL])['bng'], 10)
        self.assertEqual(tp._graph_rule([SHIV])['constants']['variant'], 'shiv')
        self.assertEqual(tp._graph_rule([QWARK])['constants']['variant'], 'qwark')
        self.assertEqual(tp._graph_rule([EMISSIVE.replace('basic_emissive.', 'basic_emissive_foliage.')]), {})


class FlatSurfaceTests(unittest.TestCase):
    DEFAULTS = {0x7F058428: [1.0, 1.0, 1.0], 0x06F19114: 1.0, tp.SPECULAR_SCALE_KEY: 1.0, 0x4069EA80: 1.0}

    def test_basic_emissive_values(self):
        _, got = extract(EMISSIVE, self.DEFAULTS, [row(0x7F058428, (0.0, 0.0865, 0.0999)), row(0x06F19114, 0.0)])
        flat = got['flat_surface']
        self.assertEqual(flat['writer'], '452EDC8C')
        self.assertAlmostEqual(flat['colour'][1], 0.0865, places=5)
        self.assertEqual((flat['f0'], flat['gloss'], got['k']), (1.0, 1.0, 0.0))
        for role in ('_c', '_g', '_n'):
            self.assertNotIn(role, got)

    def test_goo_floor_is_clamped_and_labelled(self):
        defaults = {0x34392B3C: [0.0, 0.38132601976394653, 0.0], 0x17DE448C: 0.1, tp.GLOSS_SCALE_KEY: 0.9}
        _, got = extract(GOO, defaults, [row(0x34392B3C, (-0.5, 2.0, 0.25))])
        flat = got['flat_surface']
        self.assertEqual(flat['colour'], [0.0, 1.0, 0.25])
        self.assertTrue(flat['floor'])
        self.assertAlmostEqual(flat['f0'], 0.1, places=6)
        self.assertEqual(got['k'], 0.0)

    def test_glsl_branches(self):
        for text in ('uFlatSurface) albedo = uFlatColour', 'F0 = vec3(uFlatSpec.x)', 'sqrt(clamp(uFlatSpec.y', 'albedo * uK'):
            self.assertIn(text, FRAGMENT)

    def test_viewer_rule_ports_the_term(self):
        archive, _ = extract(EMISSIVE, self.DEFAULTS)
        rule = viewer_graph_rule(archive, 'm/x.material', set())
        self.assertTrue(rule.get('applies'), rule)
        self.assertEqual(rule['ported'], ['flat_surface'])
        names, roles = validation._viewer_required(rule, False)
        self.assertEqual(sorted(names), ['VR flat surface F0', 'VR flat surface colour', 'VR flat surface gloss',
                                         'VR flat surface self-lit mask'])   # graph default K 1 is lit
        expected = validation._viewer_expected_constants(rule)
        self.assertEqual(expected['VR flat surface colour'], [1.0, 1.0, 1.0])


class DetailEmissiveTests(unittest.TestCase):
    DEFAULTS = {tp.SPECULAR_SCALE_KEY: 1.0, 0x49AFC3A0: 0.75, 0x06F19114: 1.0, 0xE3BD962E: 1.0, tp.GLOSS_SCALE_KEY: 1.0}

    def test_values_and_slots(self):
        _, got = extract(DETAIL, self.DEFAULTS, [row(0x06F19114, 5.0), row(0xE3BD962E, 5.0)])
        self.assertEqual(got['bng']['kind'], 10)
        self.assertEqual(got['bng']['detail'], (5.0, 5.0, 0.75))
        self.assertAlmostEqual(got['k'], 5.0)
        self.assertEqual(got['g_mode'], 'colored')

    def test_glsl_uses_m_channels(self):
        self.assertIn('uBngKind == 9 || uBngKind == 10) bngSelfLit = bngM.g * uK', FRAGMENT)
        self.assertIn('uBngKind == 10 ? bngM.r', FRAGMENT)

    def test_validation_names(self):
        rule = dict(applies=True, ported=['bng', 'bng_detail'], k=5.0,
                    terms={'bng': dict(kind=10, colour=[1, 1, 1], params=[0, 0, 0, 0], detail=[5.0, 5.0, 0.75])},
                    textures={'_c': 'c', '_g': 'g', '_n': 'n', '_h': 'm', '_bng_detail': 'd'})
        names, roles = validation._viewer_required(rule, True)
        self.assertEqual(sorted(names), ['VR bng detail scale u', 'VR bng detail scale v', 'VR bng detail strength'])
        self.assertIn('_h', roles)


class NumberDisplayTests(unittest.TestCase):
    def test_forward_entry_and_uniforms(self):
        archive, got = extract(SHIV, {})
        forward = got['forward']
        self.assertEqual((forward['kind'], forward['blend']), ('number_display', 'opaque'))
        kind, a, b, c, d = glass_uniforms(forward)
        self.assertEqual(kind, GLASS_KINDS["number_display"])
        self.assertEqual((a[0], a[3], b[3]), (0.01, 0.0, 0.5))
        _, qwark = extract(QWARK, {})
        self.assertEqual(glass_uniforms(qwark['forward'])[1][:2], (0.005, 0.2))
        self.assertEqual(glass_uniforms(qwark['forward'])[1][3], 1.0)

    def test_glsl_and_rule(self):
        for text in ('uKind == 7', '3.5 * uB.w * tt', '2.0 * vUV.y - 1.0', 'uA.w > 0.5 ? s8.a : s8.g'):
            self.assertIn(text, GLASS_FRAGMENT)
        archive, got = extract(SHIV, {})
        rule = viewer_graph_rule(archive, 'm/x.material', set())
        self.assertTrue(rule.get('applies'), rule)
        self.assertIn('number_display', rule['ported'])
        names, _ = validation._viewer_required(rule, True)
        self.assertIn('VR number amplitude', names)


if __name__ == '__main__':
    unittest.main()
