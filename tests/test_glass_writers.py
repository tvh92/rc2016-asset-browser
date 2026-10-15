"""Forward glass writers: basic_glass (4FC5703F), character_glass_02 (4DF1D438) and jn_glass (E7118623).

Synthetic data only: no archive, no GPU, no Blender. The reference functions below restate the decoded
equations (docs/shading.md, Forward glass writers) independently of the GLSL, which is checked by text.
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

BASIC = 'materialgraph/global/basic_glass/basic_glass.materialgraph'
CG02 = 'materialgraph/global/basic_glass/character_glass_02.materialgraph'
JN = 'materialgraph/global/basic_glass/jn_glass.materialgraph'
NORMAL_SLOT = 0x49A7866B7AE34E7A

TINT, GLOSS, P3, P8, P9, BODY = 0x4B4DAB19, 0x24F4CA4C, 0x42170E5E, 0x62835AF9, 0xECE213A3, 0x7C456482
BASIC_DEFAULTS = {TINT: [0.0015176, 0.5840784, 0.9559733], P3: 0.25, BODY: [0.0, 0.0, 0.0], GLOSS: 1.0, P8: 1.0, P9: 3.245}
JN_DEFAULTS = {0x7F058428: [0.0, 0.0, 0.0], TINT: [0.0015176, 0.5840784, 0.9559733], 0xEBF9478B: 1.0, P8: 1.0, P3: 0.25, P9: 3.245, GLOSS: 1.0}
CG02_KEYS = dict(rim_colour=0xC483F1CB, tint_scale=0xEF9CE5DD, normal_v=0xAD4069BA, env_scale=0x80C092EE, normal_u=0x34493800,
                 rim_exp=0xB7BAC19F, rim_mult=0xAAC47D17, rim_cap=0x8ECB2AE3, alpha_floor=P3, env_exp=0xACE4D867, alpha_exp=P9)
CG02_DEFAULTS = {0xC483F1CB: [0.337, 1.0, 0.0545], BODY: [0.0331, 1.0, 0.0], TINT: [0.1714, 1.0, 0.0437], GLOSS: 0.1, 0xEF9CE5DD: 0.1,
                 0xAD4069BA: 1.0, 0x80C092EE: 3.0, 0x34493800: 1.0, 0xB7BAC19F: 10.0, 0xAAC47D17: 1.0, 0x8ECB2AE3: 1.0, P3: 1.0,
                 0xACE4D867: 0.3, P9: 0.0}


def row(key, values):
    values = tuple(values) if isinstance(values, (tuple, list)) else (values,)
    return dict(key_hash=key, size=4 * len(values), raw_hex=struct.pack('<%df' % len(values), *values).hex())


def extract(graph, defaults, material_rows, flag_word):
    mat = dat1([graph], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(flag_word))])
    archive = FakeArchive({'m/glass.material': ('material', mat), graph: ('materialgraph', dat1([], lambda at: []))})
    params = dict(parameters=[], unsupported_parameters=material_rows)
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/glass.material'), params


# ---- decoded equations, written out
def sat(x):
    return min(1.0, max(0.0, x))


def luma(t):
    return 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2]


def tint_k(t):
    return [sat(c / luma(t)) for c in t]


def rim_alpha(ndv, bias, exponent):
    """basic_glass and jn_glass: a = sat(P3 + sat(4 (1 - N.V)^P9))."""
    return sat(bias + sat(4.0 * max(1.0 - ndv, 1e-6) ** exponent))


def basic_f0(t, p8, ndv, exponent):
    rim = sat(4.0 * max(1.0 - ndv, 1e-6) ** exponent)
    return [c * (p8 + rim ** 10) for c in tint_k(t)]


def cg02_alpha(ndv, floor, exponent):
    return sat(floor + sat(4.0 * max(1.0 - ndv, 1e-6) ** exponent))


def cg02_rim_colour(p, ndv, ambient=(0.0, 0.0, 0.0)):
    """Q = clamp(m (C + X) (1.001 - sat(N.V))^r + X, 0, cap), X = B (1 + k P E S), P = (1.001 - N.V)^e, S = sat(4 sqrt(1 - N.V))."""
    s = sat(4.0 * math.sqrt(1.0 - ndv))
    x = [b * (1.0 + p['env_scale'] * (1.001 - ndv) ** p['env_exp'] * e * s) for b, e in zip(p['body'], ambient)]
    edge = (1.001 - sat(ndv)) ** p['rim_exp']
    return [min(p['rim_cap'], max(0.0, p['rim_mult'] * (c + xc) * edge + xc)) for c, xc in zip(p['rim_colour'], x)]


class GlassRuleTests(unittest.TestCase):
    def test_exact_graph_rules_match_only_their_graphs(self):
        for graph, kind, writer in ((BASIC, 'glass_basic', '4FC5703F'), (CG02, 'glass_cg02', '4DF1D438'), (JN, 'glass_jn', 'E7118623')):
            rule = tp._graph_rule([graph])
            self.assertEqual((rule['forward'], rule['writer']), (kind, writer))
            self.assertEqual(rule_coverage([graph])[0], EXACT)
            self.assertEqual(GLASS_KINDS[kind], {'glass_basic': 8, 'glass_cg02': 9, 'glass_jn': 10}[kind])
            self.assertEqual(tp._graph_rule([graph.replace('.materialgraph', '_extra.materialgraph')]), {})
        # sibling graphs with their own, undecoded writers
        for sibling in ('jn_glass_rain', 'jn_glass_parallax_mask', 'basic_glass_keyable', 'character_glass_03'):
            self.assertEqual(tp._graph_rule(['materialgraph/global/basic_glass/%s.materialgraph' % sibling]), {})
        self.assertEqual(tp._graph_rule(['materialgraph/global/basic_glass/character_glass.materialgraph'])['forward'], 'character_glass')

    def test_basic_glass_parameters_and_blend(self):
        rows = [row(TINT, (0.05951123684644699, 1.0, 0.18447498977184296)), row(P3, 1.0), row(P8, 0.01), row(P9, 0.0),
                row(BODY, (0.0, 0.04373503103852272, 0.005181516520678997))]
        _, got, _ = extract(BASIC, BASIC_DEFAULTS, rows, 0x3a)       # npc_brax_visor_glass
        f = got['forward']; p = f['params']
        self.assertEqual((f['kind'], f['writer'], f['blend'], f['two_sided']), ('glass_basic', '4FC5703F', 'alpha_over', False))
        self.assertAlmostEqual(p['rim_bias'], 1.0); self.assertAlmostEqual(p['f0_base'], 0.01, places=6)
        self.assertEqual(p['rim_exp'], 0.0); self.assertAlmostEqual(p['body'][1], 0.04373503, places=6)
        self.assertAlmostEqual(p['gloss'], 1.0)                      # graph default
        _, opaque, _ = extract(BASIC, BASIC_DEFAULTS, rows, 0x838)   # npc_brax_protosuit_visor_glass
        self.assertEqual(opaque['forward']['blend'], 'opaque')

    def test_jn_glass_parameters(self):
        rows = [row(0x7F058428, (0.0, 0.3049873113632202, 0.20507873594760895)), row(0xEBF9478B, 0.1), row(P8, 0.1),
                row(P3, 0.4), row(P9, 6.0), row(TINT, (0.06301001459360123, 0.2015562504529953, 0.6038273572921753))]
        _, got, _ = extract(JN, JN_DEFAULTS, rows, 0x36)             # jn_kal_glass_01: alpha-over, two-sided
        f = got['forward']; p = f['params']
        self.assertEqual((f['kind'], f['writer'], f['blend'], f['two_sided']), ('glass_jn', 'E7118623', 'alpha_over', True))
        self.assertAlmostEqual(p['weight'], 0.1, places=6); self.assertAlmostEqual(p['colour'][1], 0.30498731, places=6)
        self.assertEqual((round(p['rim_bias'], 6), p['rim_exp'], round(p['f0_base'], 6)), (0.4, 6.0, 0.1))
        self.assertNotIn('body', p)

    def test_character_glass_02_parameters_and_normal_slot(self):
        rows = [row(CG02_KEYS['rim_colour'], (1.0, 0.05448027700185776, 0.05448027700185776)), row(CG02_KEYS['rim_mult'], 10.0),
                row(CG02_KEYS['rim_exp'], 5.0), row(CG02_KEYS['alpha_floor'], 0.8), row(CG02_KEYS['alpha_exp'], 2.0)]
        archive, got, _ = extract(CG02, CG02_DEFAULTS, rows, 0x20003a)   # wpn_combuster_glass
        f = got['forward']; p = f['params']
        self.assertEqual((f['kind'], f['writer'], f['blend']), ('glass_cg02', '4DF1D438', 'alpha_over'))
        self.assertEqual((p['rim_mult'], p['rim_exp'], round(p['alpha_floor'], 6), p['alpha_exp']), (10.0, 5.0, 0.8, 2.0))
        self.assertEqual((p['rim_cap'], p['env_scale'], round(p['env_exp'], 6)), (1.0, 3.0, 0.3))   # graph defaults
        self.assertEqual(tp._graph_rule([CG02])['slots'], {'_n': NORMAL_SLOT})

    def test_kind_ids_unique(self):
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))
        self.assertEqual({name: GLASS_KINDS[name] for name in ("eye_cornea", "number_display", "glass_basic", "glass_cg02", "glass_jn")},
                         {"eye_cornea": 6, "number_display": 7, "glass_basic": 8, "glass_cg02": 9, "glass_jn": 10})

    def test_uniform_packing(self):
        rows = [row(TINT, (0.1, 0.2, 0.3)), row(P3, 0.4), row(P8, 0.5), row(P9, 6.0), row(BODY, (0.01, 0.02, 0.03)), row(GLOSS, 0.7)]
        _, got, _ = extract(BASIC, BASIC_DEFAULTS, rows, 0x3a)
        kind, a, b, c, d, e = glass_uniforms(got['forward'])
        self.assertEqual(kind, 8)
        self.assertEqual(tuple(round(x, 6) for x in a), (0.1, 0.2, 0.3, 0.4)); self.assertEqual(tuple(round(x, 6) for x in b), (0.01, 0.02, 0.03, 0.7))
        self.assertEqual(c[:2], (0.5, 6.0)); self.assertEqual((d, e), ((0.0,) * 4, (0.0,) * 4))
        _, got, _ = extract(CG02, CG02_DEFAULTS, [row(CG02_KEYS['alpha_floor'], 0.8)], 0x39)
        kind, a, b, c, d, e = glass_uniforms(got['forward'])
        p = got['forward']['params']
        self.assertEqual(kind, 9)
        self.assertEqual(a, (*p['body'], p['gloss'])); self.assertEqual(b, (*p['tint'], p['tint_scale']))
        self.assertEqual(c, (p['env_scale'], p['env_exp'], p['alpha_floor'], p['alpha_exp']))
        self.assertEqual(d, (*p['rim_colour'], p['rim_exp'])); self.assertEqual(e, (p['rim_mult'], p['rim_cap'], p['normal_u'], p['normal_v']))
        _, got, _ = extract(JN, JN_DEFAULTS, [], 0x36)
        kind, a, b, c, d, e = glass_uniforms(got['forward'])
        self.assertEqual((kind, c[2], b[3]), (10, 1.0, 1.0))
        # the legacy kinds keep their five-element packing
        self.assertEqual(len(glass_uniforms(dict(kind='character_glass', params=dict(
            body=(0, 0, 0), gloss=1, tint=(1, 1, 1), tint_scale=1, alpha_exp=1, env_scale=1, alpha_bias=0, rim_bias=0, env_exp=1, rim_exp=0)))), 5)

    def test_glsl_branches_carry_the_decoded_equations(self):
        for text in ('uKind == 8 || uKind == 10', 'uKind == 9', 'uniform vec4 uE;', 'vec3 glassSpec(', 'vec3 ggxGlass('):
            self.assertIn(text, GLASS_FRAGMENT)
        lobe = GLASS_FRAGMENT.split('vec3 ggxGlass(')[1].split('void main()')[0]
        for text in ('kk = 0.125 * (a + 1.0) * (a + 1.0)', 'F0 + (F90 - F0) * pow(1.0 - max(dot(V, H), 0.0), 5.0)', '0.25 * DV * NdL',
                     'clamp(60.0 * F0, 0.0, 1.0)'):
            self.assertIn(text, lobe)
        basic = GLASS_FRAGMENT.split('uKind == 8 || uKind == 10')[1].split('uKind == 9')[0]
        for text in ('float rim = sat(4.0 * pow(om, uC.y))', 'alpha = sat(uA.w + rim)', 'uC.x + pow(rim, 10.0)',
                     'clamp(uA.rgb / luma, 0.0, 1.0)', 'uB.rgb * Ld + S : uB.rgb + uB.w * S'):
            self.assertIn(text, basic)
        cg02 = GLASS_FRAGMENT.split('uKind == 9')[1].split('uKind == 3')[0]
        for text in ('alpha = sat(uC.z + sat(4.0 * pow(om, uC.w)))', 'pow(1.001 - ndv, uC.y)', 'sat(4.0 * sqrt(om))',
                     'clamp(uE.x * (uD.rgb + X) * pow(max(1.001 - ndv, 1e-6), uD.w) + X, 0.0, uE.y)', 'texture2D(uTexN, vUV * vec2(uE.z, uE.w))',
                     'color = Q * Ld + glassSpec('):
            self.assertIn(text, cg02)
        # character_glass (kind 1) shares the light loop: F0 = tint, F90 = sat(60 tint), no second tint factor
        default = GLASS_FRAGMENT.split('} else {\n        alpha = sat(pow(1.001 - ndv, uC.x)')[1].split('gl_FragColor')[0]
        self.assertIn('glassSpec(N, V, L1, L2, C1, C2, tint, a, ambient)', default)
        self.assertNotIn('tint * (ggx(', default)

    def test_alpha_and_fresnel_curves(self):
        # pri_amb_hallway_light_glass_01 (captured, 268 fragments): P3 0.4, P9 4
        self.assertAlmostEqual(rim_alpha(1.0, 0.4, 4.0), 0.4, places=6)
        self.assertAlmostEqual(rim_alpha(0.5, 0.4, 4.0), 0.4 + 4.0 * 0.5 ** 4, places=6)
        self.assertEqual(rim_alpha(0.0, 0.4, 4.0), 1.0)
        # jn_kal_glass_01: P3 0.4, P9 6; face-on alpha is the bias
        self.assertAlmostEqual(rim_alpha(1.0, 0.4, 6.0), 0.4, places=6)
        self.assertEqual(rim_alpha(0.2, 0.4, 6.0), 1.0)
        # Brax visor: P3 1 makes it opaque at every angle
        self.assertEqual([rim_alpha(n, 1.0, 0.0) for n in (0.0, 0.5, 1.0)], [1.0, 1.0, 1.0])
        # character_glass_02: wpn_combuster floor 0.8, exponent 2; cora floor 1.0, exponent 0 is flat
        self.assertAlmostEqual(cg02_alpha(1.0, 0.8, 2.0), 0.8, places=6)
        self.assertEqual(cg02_alpha(0.5, 0.8, 2.0), 1.0)
        self.assertEqual(cg02_alpha(1.0, 1.0, 0.0), 1.0)
        # F0 = sat(T / luma(T)) (P8 + rim^10): grows from P8 face-on to 1 at the limb
        tint = (0.05951123684644699, 1.0, 0.18447498977184296)
        self.assertAlmostEqual(luma(tint_k(tint)), luma(tint_k(tint)))
        face, limb = basic_f0(tint, 0.01, 1.0, 4.0), basic_f0(tint, 0.01, 0.0, 4.0)
        self.assertAlmostEqual(face[1], 0.01, places=6)          # sat(T / luma) = 1 for the green channel
        self.assertAlmostEqual(limb[1], sat(tint[1] / luma(tint)) * 1.01, places=6)
        self.assertTrue(all(b > a for a, b in zip(face, limb)))

    def test_character_glass_02_rim_colour_is_capped_and_vanishes_face_on(self):
        p = dict(body=[0.0, 0.2086, 0.2423], rim_colour=[0.0865, 0.9734, 0.9734], env_scale=1.0, env_exp=0.5, rim_exp=12.0,
                 rim_mult=8.0, rim_cap=0.5)
        face = cg02_rim_colour(p, 1.0, (0.3, 0.3, 0.3))
        limb = cg02_rim_colour(p, 0.0, (0.3, 0.3, 0.3))
        self.assertTrue(all(abs(f - b) < 0.01 for f, b in zip(face, [0.0, 0.2086, 0.2423])))   # (1.001 - 1)^12 kills the rim
        self.assertEqual(limb[1], 0.5); self.assertEqual(limb[2], 0.5)                            # the cap
        self.assertTrue(all(0.0 <= c <= 0.5 for c in limb))

    def test_viewer_graph_rule_and_shader_status(self):
        cases = ((BASIC, BASIC_DEFAULTS, [row(P3, 1.0)], 0x3a, 'glass_basic'), (JN, JN_DEFAULTS, [row(P9, 6.0)], 0x36, 'glass_jn'),
                 (CG02, CG02_DEFAULTS, [row(P3, 0.8)], 0x39, 'glass_cg02'))
        for graph, defaults, rows, flag_word, key in cases:
            archive, _, params = extract(graph, defaults, rows, flag_word)
            with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
                  patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
                rule = viewer_graph_rule(archive, 'm/glass.material', set())
            self.assertTrue(rule['applies'], key)
            self.assertEqual((rule['ported'], rule['unported']), ([key], []))
            self.assertEqual(rule['terms'][key]['kind'], key)
            self.assertEqual(final_shader_status('thin_glass_approximation', rule, set(), dict(status='unsupported'), None, [], [graph]),
                             'viewer_graph_rule_frontend')
            # an unported glass term leaves the documented thin-glass approximation
            self.assertEqual(final_shader_status('thin_glass_approximation', dict(rule, ported=[]), set(), dict(status='unsupported'),
                                                 None, [], [graph]), 'thin_glass_approximation')


class GlassValidationTests(unittest.TestCase):
    def rule(self, graph, defaults, rows, key, flag_word, has_n=False):
        _, got, _ = extract(graph, defaults, rows, flag_word)
        return dict(applies=True, ported=[key], terms={key: got['forward']}, textures={'_c': None, '_g': None, '_n': 't/n.texture' if has_n else None})

    def test_expected_constants_and_required_names(self):
        for graph, defaults, key, flag_word, n_names in ((BASIC, BASIC_DEFAULTS, 'glass_basic', 0x3a, 6), (JN, JN_DEFAULTS, 'glass_jn', 0x36, 7),
                                                         (CG02, CG02_DEFAULTS, 'glass_cg02', 0x39, 12)):
            rule = self.rule(graph, defaults, [], key, flag_word)
            expected = validation._viewer_expected_constants(rule)
            names, roles = validation._viewer_required(rule, False)
            self.assertEqual(len(names), n_names); self.assertEqual(roles, [])
            self.assertEqual(set(names), set(expected), key)
            self.assertTrue(all(name.startswith('VR glass ') for name in names))
        rule = self.rule(CG02, CG02_DEFAULTS, [], 'glass_cg02', 0x39, has_n=True)
        names, roles = validation._viewer_required(rule, True)
        self.assertIn('VR glass normal u', names); self.assertEqual(roles, ['_n'])
        expected = validation._viewer_expected_constants(rule)
        self.assertEqual(expected['VR glass rim mult'], rule['terms']['glass_cg02']['params']['rim_mult'])
        self.assertEqual(expected['VR glass normal v'], 1.0)

    def test_blender_front_end_builds_the_nodes_the_validator_requires(self):
        import pathlib
        text = (pathlib.Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_glass')[1].split('def apply_viewer_graph_rule')[0]
        for key, graph, defaults, flag_word in (('glass_basic', BASIC, BASIC_DEFAULTS, 0x3a), ('glass_jn', JN, JN_DEFAULTS, 0x36),
                                                ('glass_cg02', CG02, CG02_DEFAULTS, 0x39)):
            for name in validation._viewer_required(self.rule(graph, defaults, [], key, flag_word), False)[0]:
                self.assertIn(name[3:], body)
        for node in ('VR glass lit', 'VR glass specular', 'VR glass weight', 'VR glass blend additive', 'VR glass alpha weight',
                     'VR cull back faces', 'glass normal map'):
            self.assertIn(node, body)
        # Blender suffixes the source constant if a shader node claims its exact name first.
        self.assertIn("P.const3('glass colour', p['colour'])", body)
        self.assertIn("em.name = 'VR glass emission'", body)
        self.assertIn("mat.use_backface_culling = not f.get('two_sided')", body)
        self.assertIn("'DITHERED' if blend == 'opaque' else 'BLENDED'", body)
        self.assertIn("P.m('MULTIPLY', P.m('MAXIMUM', P.m('MAXIMUM', r, g), b), 60.0, clamp=True)", body)   # F90 = sat(60 F0)
        self.assertIn('GLASS_VIEWER_KINDS', text.split('def apply_viewer_graph_rule')[1])

    def test_character_glass_group_uses_the_game_fresnel(self):
        import pathlib
        text = (pathlib.Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def build_character_glass_group')[1].split('GLASS_BLEND_BY_FLAGS')[0]
        for term in ('F90 = saturate(60*F0)', 'ShaderNodeBsdfPrincipled', 'Reflection weight = F90', 'Glass tinted reflection R'):
            self.assertIn(term, body)
        self.assertIn('GLASS_GROUP_VERSION = 5', text)


if __name__ == '__main__':
    unittest.main()
