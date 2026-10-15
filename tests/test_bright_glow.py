"""Antenna glow shell forward rule (graph hero_bright_center_light_glow_variable, pixel program 93695188).

Synthetic data only: no archive, no GPU, no Blender.
"""
import math
import struct
import unittest
from unittest.mock import patch

from rc2016.formats.links import EXACT, rule_coverage
from rc2016.formats.materials import final_shader_status, viewer_graph_rule
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms
from tests.test_viewer_textures import FakeArchive, dat1, flags

GRAPH = ('materialgraph/character/hero/hero_bright_center_light_glow/'
         'hero_bright_center_light_glow_variable.materialgraph')
KEYS = dict(colour=0x4E6E03FF, colour_lo=0x68538B05, dist_k=0xD76F8651, gain=0x5DE2C7F0,
            dist0=0xCF085666, steady=0x01B78BF6, sharp=0x60DC3D2F, rate=0xB162C569)
# Graph defaults (shipped graph parameter table) and the Qwark material's own values.
DEFAULTS = {KEYS['colour']: [0.2050787, 0.0065121, 0.0033465], KEYS['colour_lo']: [0.0331048, 0.0033465, 0.0015176],
            KEYS['dist_k']: 0.1, KEYS['gain']: 10.0, KEYS['dist0']: 6.0, KEYS['steady']: 0.15,
            KEYS['sharp']: 0.9, KEYS['rate']: 4.0}
QWARK = {KEYS['colour']: (0.76865, 0.010365, 0.0), KEYS['colour_lo']: (0.0, 0.0, 0.0), KEYS['dist_k']: 0.001,
         KEYS['gain']: 6.5, KEYS['dist0']: 6.0, KEYS['steady']: 0.9, KEYS['sharp']: 0.9, KEYS['rate']: 3.0}


def row(key, values):
    values = tuple(values) if isinstance(values, (tuple, list)) else (values,)
    return dict(key_hash=key, size=4 * len(values), raw_hex=struct.pack('<%df' % len(values), *values).hex())


def extract(material_rows, flag_word=0x200039):
    mat = dat1([GRAPH], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(flag_word))])
    archive = FakeArchive({'m/glow.material': ('material', mat), GRAPH: ('materialgraph', dat1([], lambda at: []))})
    params = dict(parameters=[], unsupported_parameters=material_rows)
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/glow.material')


def profile(p, ndv, distance=6.0):
    """(x, alpha, rgb) of the forward colour pass; the GLSL branch `uKind == 5` written out."""
    sat = lambda v: min(1.0, max(0.0, v))
    t = abs(1.00100004673 - ndv) ** p['exponent']
    x = (1.0 - p['dist_k'] * (distance - p['dist0'])) * p['weight'] * p['gain'] * (1.0 - t)
    amp = 7.0 * p['amplitude']
    return x, sat(x), [amp * (lo + (hi - lo) * x * x) for lo, hi in zip(p['colour_lo'], p['colour'])]


class BrightGlowRuleTests(unittest.TestCase):
    def test_exact_graph_rule_matches_only_the_graph(self):
        rule = tp._graph_rule([GRAPH])
        self.assertEqual(rule['forward'], 'bright_glow')
        self.assertIsNone(rule['self_lit_key'])
        self.assertEqual({name: key for name, key, _ in rule['params']}, KEYS)
        self.assertEqual(rule_coverage([GRAPH])[0], EXACT)
        self.assertEqual(tp._graph_rule([GRAPH.replace('_variable', '_variable_extra')]), {})
        self.assertEqual(tp._graph_rule([GRAPH.replace('hero_bright_center_light_glow_variable', 'hero_bright_center_light_variable')])
                         .get('forward'), None)

    def test_parameters_use_material_values_then_graph_defaults(self):
        _, got = extract([])       # material omits every key: graph defaults
        p = got['forward']['params']
        self.assertEqual(got['forward']['kind'], 'bright_glow')
        for name in ('dist_k', 'gain', 'dist0', 'steady', 'sharp', 'rate'):
            self.assertAlmostEqual(p[name], DEFAULTS[KEYS[name]], places=6)
        self.assertEqual(p['colour'], DEFAULTS[KEYS['colour']])
        self.assertAlmostEqual(p['exponent'], 0.02)
        self.assertAlmostEqual(p['weight'], 0.575)       # 1 - 0.5 (1 - 0.15)
        self.assertEqual((got['forward']['blend'], got['forward']['two_sided']), ('additive', False))
        _, got = extract([row(k, v) for k, v in QWARK.items()])
        p = got['forward']['params']
        self.assertAlmostEqual(p['gain'], 6.5); self.assertAlmostEqual(p['dist_k'], 0.001, places=6)
        self.assertAlmostEqual(p['colour'][0], 0.76865, places=5)
        self.assertAlmostEqual(p['weight'], 0.95)
        self.assertEqual(got['k'], 0.0)

    def test_amplitude_is_the_labelled_presentation_value(self):
        _, got = extract([])
        p = got['forward']['params']
        self.assertEqual((p['amplitude'], p['captured_amplitude'], p['amplitude_input'], p['presentation']),
                         (0.5, 0.01, 'CB0 dword 115', True))   # same convention as facing_glow
        self.assertEqual(p['frozen_inputs'], dict(sin=0.0, cutoff=0.0, alpha=1.0, fog_fade=1.0))
        self.assertEqual(p['writer'], '93695188')

    def test_uniform_packing(self):
        _, got = extract([row(k, v) for k, v in QWARK.items()])
        kind, a, b, c, d = glass_uniforms(got['forward'])
        self.assertEqual(kind, GLASS_KINDS['bright_glow']); self.assertEqual(kind, 5)
        p = got['forward']['params']
        self.assertEqual(a, (*p['colour'], p['gain'])); self.assertEqual(b, (*p['colour_lo'], p['sharp']))
        self.assertEqual(c, (p['dist_k'], p['dist0'], p['weight'], p['amplitude']))
        self.assertAlmostEqual(c[2], 0.95); self.assertEqual(d, (0.0,) * 4)

    def test_glsl_branch_carries_the_decoded_equations(self):
        self.assertIn('uKind == 5', GLASS_FRAGMENT)
        branch = GLASS_FRAGMENT.split('uKind == 5')[1].split('uKind == 4')[0]
        for text in ('abs(1.00100004673 - ndv)', '0.2 * (1.0 - uB.w)', 'uC.x * (length(vPos) - uC.y)',
                     'uC.z * uA.w * (1.0 - t)', 'alpha = sat(x)', 'uC.w * 7.0 * (uB.rgb + (uA.rgb - uB.rgb) * x * x)'):
            self.assertIn(text, branch)

    def test_profile_falls_off_from_the_centre_and_alpha_clamps(self):
        _, got = extract([row(k, v) for k, v in QWARK.items()])
        p = got['forward']['params']
        ndvs = [1.0 - i / 100 for i in range(101)]
        xs = [profile(p, n)[0] for n in ndvs]
        self.assertTrue(all(a >= b - 1e-12 for a, b in zip(xs, xs[1:])), 'x must fall monotonically from the centre to the limb')
        self.assertAlmostEqual(xs[0], 6.5 * 0.95 * (1.0 - 0.001 ** 0.02), places=5)
        self.assertAlmostEqual(xs[-1], 0.0, places=3)       # at the limb |1.001|^0.02 -> 1
        luma = [profile(p, n)[1] * profile(p, n)[2][0] for n in ndvs]   # alpha * red, what the additive blend adds
        self.assertTrue(all(a >= b - 1e-12 for a, b in zip(luma, luma[1:])))
        self.assertLess(luma[50] / luma[0], 0.01)           # half-way to the limb: under 1% of the centre
        self.assertLess(luma[10] / luma[0], 0.5)            # a halo concentrated at the centre
        # alpha is clamped to 1 for a strong gain and to 0 when the distance term goes negative
        self.assertEqual(profile(dict(p, gain=100.0), 1.0)[1], 1.0)
        self.assertEqual(profile(p, 1.0, distance=6.0 + 1.0 / p['dist_k'] + 10.0)[1], 0.0)
        # fog term vanishes: the low colour is the floor
        self.assertEqual(profile(p, 0.0)[2][2], 0.0)
        self.assertAlmostEqual(profile(dict(p, colour_lo=[0.1, 0.1, 0.1]), 0.0)[2][2], 7.0 * p['amplitude'] * 0.1, places=6)

    def test_viewer_graph_rule_and_shader_status(self):
        archive, _ = extract([row(k, v) for k, v in QWARK.items()])
        rows = dict(parameters=[], unsupported_parameters=[row(k, v) for k, v in QWARK.items()])
        with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=rows)):
            rule = viewer_graph_rule(archive, 'm/glow.material', set())
        self.assertTrue(rule['applies'])
        self.assertEqual((rule['ported'], rule['unported']), (['bright_glow'], []))
        self.assertEqual(rule['terms']['bright_glow']['blend'], 'additive')
        self.assertNotIn('screen', rule['terms'])
        self.assertEqual(rule['textures'], {'_c': None, '_g': None, '_n': None})
        status = final_shader_status('color_only_preview_missing_base_color', rule, set(), dict(status='unsupported'), None, [], [GRAPH])
        self.assertEqual(status, 'viewer_graph_rule_frontend')
        # Other forward kinds stay on their own front-ends.
        other = dict(rule, ported=[], applies=True)
        self.assertEqual(final_shader_status('color_only_preview_missing_base_color', other, set(), dict(status='unsupported'), None, [], [GRAPH]),
                         'color_only_preview_missing_base_color')


class BrightGlowValidationTests(unittest.TestCase):
    def rule(self):
        _, got = extract([row(k, v) for k, v in QWARK.items()])
        return dict(applies=True, ported=['bright_glow'], terms={'bright_glow': got['forward']},
                    textures={'_c': None, '_g': None, '_n': None})

    def test_expected_constants_and_required_names(self):
        rule = self.rule(); p = rule['terms']['bright_glow']['params']
        expected = validation._viewer_expected_constants(rule)
        names, roles = validation._viewer_required(rule, False)
        wanted = ['VR bright glow ' + n for n in ('colour', 'colour lo', 'dist_k', 'dist0', 'gain', 'weight', 'exponent', 'amplitude')]
        self.assertEqual(sorted(names), sorted(wanted)); self.assertEqual(roles, [])
        self.assertEqual(set(expected), set(wanted))
        self.assertEqual(expected['VR bright glow gain'], p['gain'])
        self.assertEqual(expected['VR bright glow colour lo'], p['colour_lo'])
        self.assertAlmostEqual(expected['VR bright glow exponent'], 0.02)
        self.assertEqual(expected['VR bright glow amplitude'], 0.5)

    def test_blender_front_end_builds_the_nodes_the_validator_requires(self):
        import pathlib
        text = (pathlib.Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_bright_glow')[1].split('def apply_viewer_graph_rule')[0]
        names = validation._viewer_required(self.rule(), False)[0]
        for name in names:
            self.assertIn(name[3:], body)       # constants are created under the name the validator expects
        for node in ('VR bright glow emission', 'VR bright glow blend additive', 'VR camera distance', 'VR cull back faces'):
            self.assertIn(node, body)
        self.assertIn("use_backface_culling = not f.get('two_sided')", body)
        self.assertIn("surface_render_method = 'BLENDED'", body)
        self.assertIn("'View Distance'", body)


if __name__ == '__main__':
    unittest.main()
