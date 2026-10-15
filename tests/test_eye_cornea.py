"""Eye cornea forward rule (graph hero_eye_cornea, pixel program A36A97CB).

Synthetic data only: no archive, no GPU, no Blender.
"""
import struct
import unittest
from unittest.mock import patch

from rc2016.formats.links import EXACT, rule_coverage
from rc2016.formats.materials import final_shader_status, viewer_graph_rule
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms
from tests.test_viewer_textures import FakeArchive, dat1, flags

GRAPH = 'materialgraph/character/hero/hero_eye_cornea/hero_eye_cornea.materialgraph'
KEYS = dict(fade_end=0x8CE80F85, power_far=0xB0624EB0, reflect_base=0xAF7D5E6B, power_near=0x621FAA21,
            fade_start=0xDBAD0A10, reflect_weight=0x223DC13C, env_normal=0x3F13004B, highlight_k=0xF8828985,
            highlight_normal=0xE14A15D1)
# Graph defaults (pass 0 parameter table) and the two shipped materials' own values.
DEFAULTS = {KEYS['fade_end']: 14.0, KEYS['power_far']: 4.0, KEYS['reflect_base']: 0.0, KEYS['power_near']: 30.0,
            KEYS['fade_start']: 6.0, KEYS['reflect_weight']: 0.5, KEYS['env_normal']: 0.8, KEYS['highlight_k']: 0.0,
            KEYS['highlight_normal']: 0.85}
NPC = {KEYS['power_far']: 20.0, KEYS['power_near']: 85.0, KEYS['highlight_k']: 1.2, KEYS['reflect_base']: 0.0,
       KEYS['reflect_weight']: 0.5, KEYS['env_normal']: 0.8}
RATCHET = {KEYS['power_far']: 20.0, KEYS['power_near']: 85.0, KEYS['highlight_k']: 0.8, KEYS['reflect_base']: 0.03,
           KEYS['reflect_weight']: 1.0, KEYS['env_normal']: 1.0, KEYS['highlight_normal']: 1.0}
ALPHA_OVER_CULLED = 0x3A


def row(key, value):
    return dict(key_hash=key, size=4, raw_hex=struct.pack('<f', value).hex())


def extract(material_rows, flag_word=ALPHA_OVER_CULLED):
    mat = dat1([GRAPH], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(flag_word))])
    archive = FakeArchive({'m/eye.material': ('material', mat), GRAPH: ('materialgraph', dat1([], lambda at: []))})
    params = dict(parameters=[], unsupported_parameters=material_rows)
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/eye.material')


def sat(v):
    return min(1.0, max(0.0, v))


def alpha_of(p, ndv, cos_rh, distance):
    """Alpha of the forward pass; the GLSL branch `uKind == 6` written out."""
    f4 = abs(p['fresnel_bias'] - sat(ndv)) ** p['fresnel_power']
    power = p['power_near'] + sat(p['distance_k'] * distance) * (p['power_far'] - p['power_near'])
    fade = sat((distance - p['fade_start']) / (p['fade_end'] - p['fade_start']))
    lobe = p['highlight_gain'] * sat(cos_rh) ** power * (1.0 - fade)
    return sat(lobe + sat(p['reflect_base'] + f4) + p['alpha_bias']), lobe, f4


class EyeCorneaRuleTests(unittest.TestCase):
    def test_exact_graph_rule_matches_only_the_graph(self):
        rule = tp._graph_rule([GRAPH])
        self.assertEqual(rule['forward'], 'eye_cornea')
        self.assertIsNone(rule['self_lit_key'])
        self.assertEqual({name: key for name, key, _ in rule['params']}, KEYS)
        self.assertEqual(rule['slots'], {'_n': 0x49A7866B7AE34E7A, '_pick': 0x49A7866BABDAD780})
        self.assertEqual(rule_coverage([GRAPH])[0], EXACT)
        self.assertEqual(tp._graph_rule([GRAPH.replace('hero_eye_cornea.', 'hero_eye_cornea_extra.')]), {})

    def test_parameters_use_material_values_then_graph_defaults(self):
        _, got = extract([])
        p = got['forward']['params']
        self.assertEqual(got['forward']['kind'], 'eye_cornea')
        for name, key in KEYS.items():
            self.assertAlmostEqual(p[name], DEFAULTS[key], places=6)
        _, npc = extract([row(k, v) for k, v in NPC.items()])
        q = npc['forward']['params']
        self.assertEqual((q['power_near'], q['power_far'], q['fade_start'], q['fade_end']), (85.0, 20.0, 6.0, 14.0))
        self.assertAlmostEqual(q['highlight_k'], 1.2, places=6)
        _, ratchet = extract([row(k, v) for k, v in RATCHET.items()])
        r = ratchet['forward']['params']
        self.assertAlmostEqual(r['reflect_base'], 0.03, places=6)
        self.assertEqual((r['reflect_weight'], r['env_normal'], r['highlight_normal']), (1.0, 1.0, 1.0))

    def test_flags_word_gives_alpha_over_with_culled_back_faces(self):
        _, got = extract([])
        self.assertEqual((got['forward']['blend'], got['forward']['two_sided']), ('alpha_over', False))

    def test_decoded_literals_and_frozen_inputs_are_recorded(self):
        _, got = extract([])
        p = got['forward']['params']
        self.assertEqual((p['fresnel_normal_scale'], p['fresnel_power'], p['half_vector_view_weight']), (0.555, 4.0, 0.49))
        self.assertEqual((p['distance_k'], p['alpha_bias'], p['highlight_gain'], p['highlight_gain_slope']), (0.08, 0.06, 4.0, 3.0))
        self.assertEqual(p['writer'], 'A36A97CB')
        self.assertEqual(p['frozen_inputs']['light_term'], 1.0)

    def test_uniform_packing(self):
        _, got = extract([row(k, v) for k, v in NPC.items()])
        kind, a, b, c, d = glass_uniforms(got['forward'])
        p = got['forward']['params']
        self.assertEqual(kind, GLASS_KINDS['eye_cornea']); self.assertEqual(kind, 6)
        self.assertEqual(a, (p['fade_start'], p['fade_end'], p['power_near'], p['power_far']))
        self.assertEqual(b, (p['reflect_base'], p['reflect_weight'], p['highlight_normal'], p['env_normal']))
        self.assertEqual((c[0], d), (p['highlight_k'], (0.0,) * 4))

    def test_glsl_branch_carries_the_decoded_equations(self):
        self.assertIn('uKind == 6', GLASS_FRAGMENT)
        branch = GLASS_FRAGMENT.split('uKind == 6')[1].split('uKind == 3')[0]
        for text in ('0.555 * nm', 'uB.z * nm', 'uB.w * nm', 'abs(1.00100004673 - sat(dot(Nf, V))), 4.0',
                     'uA.z + sat(0.08 * dist) * (uA.w - uA.z)', 'sat((dist - uA.x) / (uA.y - uA.x))',
                     'normalize(0.49 * V + 0.51 * L1)', '4.0 * pow(sat(dot(reflect(-V, Nh), H)), power) * (1.0 - fade)',
                     'alpha = sat(lobe + sat(uB.x + F4) + 0.06)', '* (uB.y + F4)', '(lens + 1.0) * lobe * (4.0 - 3.0 * uC.x)'):
            self.assertIn(text, branch)
        self.assertIn('N * (1.0 - dot(xy, xy))', GLASS_FRAGMENT)   # linear z

    def test_alpha_profile(self):
        _, got = extract([row(k, v) for k, v in NPC.items()])
        p = got['forward']['params']
        # Facing the camera, no lobe: the 0.06 bias alone.
        self.assertAlmostEqual(alpha_of(p, 1.0, 0.0, 3.0)[0], 0.06 + (1.001 - 1.0) ** 4, places=6)
        # Grazing: the Fresnel term rises to about 1.
        self.assertGreater(alpha_of(p, 0.0, 0.0, 3.0)[0], 0.99)
        # A close perfect highlight saturates; the same highlight beyond the fade end is gone.
        self.assertEqual(alpha_of(p, 1.0, 1.0, 3.0)[0], 1.0)
        self.assertAlmostEqual(alpha_of(p, 1.0, 1.0, 20.0)[1], 0.0)
        # The power falls from near to far (85 -> 20), so a tilted lobe is wider at 5 than at 1 units.
        self.assertGreater(alpha_of(p, 1.0, 0.99, 5.0)[1], alpha_of(p, 1.0, 0.99, 1.0)[1])
        # The fade is linear from start (6) to end (14): half way the lobe is halved.
        self.assertAlmostEqual(alpha_of(p, 1.0, 1.0, 10.0)[1], 4.0 * (1.0 - 0.5))

    def test_viewer_graph_rule_and_shader_status(self):
        archive, _ = extract([row(k, v) for k, v in NPC.items()])
        rows = dict(parameters=[], unsupported_parameters=[row(k, v) for k, v in NPC.items()])
        with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=rows)):
            rule = viewer_graph_rule(archive, 'm/eye.material', set())
        self.assertTrue(rule['applies'])
        self.assertEqual((rule['ported'], rule['unported']), (['eye_cornea'], []))
        self.assertEqual(rule['terms']['eye_cornea']['blend'], 'alpha_over')
        self.assertNotIn('screen', rule['terms'])
        status = final_shader_status('color_only_preview_missing_base_color', rule, set(), dict(status='unsupported'), None, [], [GRAPH])
        self.assertEqual(status, 'viewer_graph_rule_frontend')


class EyeCorneaValidationTests(unittest.TestCase):
    NAMES = ('fresnel normal scale', 'env normal scale', 'fresnel bias', 'fresnel power', 'reflect base', 'alpha bias', 'reflect weight')

    def rule(self):
        _, got = extract([row(k, v) for k, v in RATCHET.items()])
        return dict(applies=True, ported=['eye_cornea'], terms={'eye_cornea': got['forward']},
                    textures={'_c': None, '_g': None, '_n': 'textures/character/shared/hemisphere_flatter_n.texture'})

    def test_expected_constants_and_required_names(self):
        rule = self.rule(); p = rule['terms']['eye_cornea']['params']
        expected = validation._viewer_expected_constants(rule)
        names, roles = validation._viewer_required(rule, True)
        wanted = ['VR cornea ' + n for n in self.NAMES]
        self.assertEqual(sorted(names), sorted(wanted)); self.assertEqual(set(expected), set(wanted))
        self.assertEqual(roles, ['_n'])
        self.assertEqual(expected['VR cornea reflect base'], p['reflect_base'])
        self.assertEqual(expected['VR cornea env normal scale'], 1.0)
        self.assertEqual(expected['VR cornea fresnel normal scale'], 0.555)

    def test_blender_front_end_builds_the_nodes_the_validator_requires(self):
        import pathlib
        text = (pathlib.Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_eye_cornea')[1].split('def apply_viewer_graph_rule')[0]
        for name in EyeCorneaValidationTests.NAMES:
            self.assertIn(name, body)
        for node in ('VR cornea glossy', 'VR cornea blend %s', 'VR cull back faces'):
            self.assertIn(node, body)
        self.assertIn("use_backface_culling = not f.get('two_sided')", body)
        self.assertIn("surface_render_method = 'BLENDED'", body)


if __name__ == '__main__':
    unittest.main()
