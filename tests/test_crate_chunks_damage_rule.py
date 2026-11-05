"""Gameplay crates (graph crate_chunks_damage, deferred writers 32615317 and CB9B3AC2): grey _g, self-lit _g.B and the
intact-crate albedo tint. Synthetic data only: no archive, no GPU, no Blender."""
import hashlib
import struct
import unittest
from unittest.mock import patch

from rc2016.formats.materials import viewer_graph_rule
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT
from tests.test_viewer_textures import FakeArchive, dat1, flags

GRAPH = 'materialgraph/global/crate_chunks_damage/crate_chunks_damage.materialgraph'
RATE, INTENSITY = 0x41E7FE54, 0x5FD56522
DEFAULTS = {RATE: 0.0, INTENSITY: 10.0}


def row(key, value):
    return dict(key_hash=key, size=4, raw_hex=struct.pack('<f', value).hex())


def setup(material_rows=(), sha=None):
    graph_blob = dat1([], lambda at: [])
    mat = dat1([GRAPH], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(0x230))])
    archive = FakeArchive({'m/crate.material': ('material', mat), GRAPH: ('materialgraph', graph_blob)})
    digest = sha or hashlib.sha256(b'1TAD' + graph_blob).hexdigest()
    rules = tuple((p, dict(r, _graph_sha256=digest) if r.get('crate_tint') else r) for p, r in tp.GRAPH_RULES)
    params = dict(parameters=[], unsupported_parameters=list(material_rows))
    patches = (patch.object(tp, 'GRAPH_RULES', rules),
               patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
               patch('rc2016.formats.materials.extract_material_parameters', return_value=params))
    return archive, patches


def extract(material_rows=(), sha=None):
    archive, patches = setup(material_rows, sha)
    with patches[0], patches[1], patches[2]:
        return archive, tp.material_texture_paths(archive, 'm/crate.material')


class CrateRuleTests(unittest.TestCase):
    def test_rule_is_bound_to_the_exact_graph_hash(self):
        self.assertEqual(tp.CRATE_CHUNKS_DAMAGE_GRAPH_SHA256, 'cddfc855b072e2202954470cf5c4cfe43230fad9cb5c2fc758c392ff3e1399c0')
        rule = tp._graph_rule([GRAPH], {GRAPH: tp.CRATE_CHUNKS_DAMAGE_GRAPH_SHA256})
        self.assertTrue(rule['crate_tint'])
        self.assertEqual((rule['g_mode'], rule['self_lit'], rule['self_lit_key']), ('grey', 'g_blue', INTENSITY))
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})
        self.assertNotIn('crate_tint', extract(sha='0' * 64)[1])

    def test_tint_grey_g_and_self_lit_default(self):
        _, got = extract()
        self.assertEqual((got['g_mode'], got['self_lit']), ('grey', 'g_blue'))
        self.assertAlmostEqual(got['k'], 10.0)
        self.assertEqual((got['spec'], got['gloss']), (1.0, 1.0))
        tint = got['crate_tint']
        self.assertEqual(tint['colour'], [0.672443151473999, 0.533276379108429, 0.3967552185058594])
        self.assertEqual(tint['writer'], '32615317/CB9B3AC2')

    def test_material_intensity_overrides_the_graph_default(self):
        _, got = extract([row(INTENSITY, 6.0), row(RATE, 3.0)])
        self.assertAlmostEqual(got['k'], 6.0)

    def test_glsl_albedo_branch(self):
        branch = FRAGMENT.split('uCrateTint)')[1].split('}')[0]
        self.assertIn('albedo *= uCrateC', branch)

    def test_viewer_texture_entry_keeps_the_tint(self):
        archive, paths = extract()
        with (patch.object(tp, 'material_texture_paths', return_value=paths),
              patch('rc2016.viewer.native_glsl.selected_translation', return_value=None)):
            entry = tp.material_textures(archive, [dict(index=0, path='m/crate.material')], {}, previews=False)[0]
        self.assertEqual(entry['crate_tint'], paths['crate_tint'])

    def test_viewer_rule_ports_the_term(self):
        archive, patches = setup()
        with patches[0], patches[1], patches[2]:
            rule = viewer_graph_rule(archive, 'm/crate.material', set())
        self.assertTrue(rule.get('applies'), rule)
        self.assertIn('crate_tint', rule['ported'])
        self.assertNotIn('crate_tint', rule.get('unported', []))


class CrateValidationTests(unittest.TestCase):
    def test_expected_constants_and_required_names(self):
        _, got = extract()
        rule = dict(applies=True, ported=['crate_tint'], terms={'crate_tint': got['crate_tint']},
                    textures={'_c': 'x', '_g': 'y', '_n': 'z'})
        expected = validation._viewer_expected_constants(rule)
        names, roles = validation._viewer_required(rule, True)
        self.assertEqual(names, ['VR crate intact tint'])
        self.assertIn('_c', roles)
        self.assertEqual(expected['VR crate intact tint'], got['crate_tint']['colour'])


if __name__ == '__main__':
    unittest.main()
