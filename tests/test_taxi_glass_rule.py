"""Taxi glass forward program 4E9B2CB3 (docs/material-decoding/taxi-glass.md).

Synthetic checks (rule binding, uniform packing, GLSL text, validator constants) always run; the archive-backed
check reads the real material and skips without RC2016_RESEARCH / RC2016_GAME_ROOT.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, TAXI_GLASS_TEXTURES, glass_uniforms

GRAPH = 'materialgraph/environment/global/architecture/mo_gbl_arch_glass_01/mo_gbl_arch_glass_01.materialgraph'
MATERIAL = 'material/environment/global/gameplay/gbl_gp_taxi_glass_01/gbl_gp_taxi_glass_01.material'
GRAPH_SHA = '8c2b13e3394d8aaeacede412ab62736e7b7ea672d70689eee59f802f19e0653a'
ALPHA_H = 'textures/environment/global/gameplay/gbl_gp_taxi/gbl_gp_taxi_glass_alpha_h.texture'
# The window glass images of the graph slots, matched to the captured bindings of APIs 5 (_c), 8 (_g), 9 (_n) by top-mip hash.
WINDOW = 'textures/environment/global/glows_glass/mo_gbl_window_glass_01/mo_gbl_window_glass_01'
WINDOW_C, WINDOW_G, WINDOW_N = WINDOW + '_c.texture', WINDOW + '_g.texture', WINDOW + '_n.texture'
# CB1 (material block) dwords 0..19 bound at the captured draws 13644 and 13669 of the 2026-11-05 taxi capture.
CAPTURED_BLOCK = (0.69387, 0.65837, 0.48515, 1.0, 0.07421, 0.0137, 0.0, 0.5, 1.0, 0.0, 0.75, 0.0, 0.0, 0.0, 0.05, 0.25, 0.05, 0.25,
                  0.25, 1.0)


def sat(x):
    return max(0.0, min(1.0, x))


def alpha_closed_form(weight, h_a, h_b, scale=1.0):
    """alpha and the discard flag for attribute4.w = 0: sat(attr0.w sat(W (A + B))), cut below attribute0.z = 4/255."""
    inner = sat(weight * (h_a + h_b))
    return sat(scale * inner), inner < 4.0 / 255.0


def offset_closed_form(depth, face, view_depth, fade_scale, fade_bias, v_dot_pu, v_dot_pv):
    """B's UV offset: depth f (1 - fade) (V.T, -V.Bt) with the game's T = -dP/du and Bt = +dP/dv."""
    k = depth * face * (1.0 - sat(view_depth * fade_scale + fade_bias))
    return (-k * v_dot_pu, -k * v_dot_pv)


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hash_binds_the_rule(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('glass_taxi', '4E9B2CB3', GRAPH_SHA))
        self.assertEqual(rule['slots'], {'_pick': 0x49A7866BA1D220DE, '_window_c': 0x49A7866BABDAD780, '_window_g': 0x49A7866B1576D848,
                                         '_n': 0x49A7866B7AE34E7A})
        self.assertEqual(tp.TAXI_GLASS_ALPHA_SLOTS, (0x49A7866BA1D220DE, 0x49A7866BE0B8D722))
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})

    def test_kind_and_uniform_packing(self):
        self.assertEqual(GLASS_KINDS['glass_taxi'], 17)
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))
        params = dict(tint=(0.1, 0.2, 0.3), height_weight=0.5, parallax_depth=0.05, fade_scale=0.07, fade_bias=-2.3, alpha_test=0.0157,
                      tint_dense=(0.4, 0.5, 0.6), env_weight=0.25, gloss_scale=1.0, f0_scale=0.75, normal_scale=1.0, alpha_scale=1.0)
        self.assertEqual(glass_uniforms(dict(kind='glass_taxi', params=params)),
                         (17, (0.1, 0.2, 0.3, 0.5), (0.05, 0.07, -2.3, 0.0157), (0.4, 0.5, 0.6, 0.25), (1.0, 1.0, 0.75, 1.0), (0.0,) * 4))
        self.assertEqual([row[:2] for row in TAXI_GLASS_TEXTURES], [(10, '_window_g'), (13, '_window_c')])

    def test_glsl_branch_carries_the_decoded_equations(self):
        branch = GLASS_FRAGMENT.split('uKind == 17')[1].split('uKind == 16')[0]
        for text in ('Pv = (dp2 * du1.x - dp1 * du2.x) * idet', 'float fade = sat(abs(vPos.z) * uB.y + uB.z)',
                     'uB.x * face * (1.0 - fade) * vec2(-dot(V, Pu), -dot(V, Pv))', 'texture2D(uTexP, vUV).r',
                     'texture2D(uTexP, vUV + off).r', 'float inner = sat(uA.w * (hA + hB))', 'if (inner < uB.w) discard',
                     'alpha = sat(uD.x * inner)', 'vec3 n = normalize(vNrm), T = -Pu, B = cross(n, T)',
                     '2.0 * uD.w * (1.0 - fade) * (texture2D(uTexN, vUV).rg - 0.5)',
                     'face * normalize(T * xy.x + B * xy.y + n * (1.0 - dot(xy, xy)))',
                     'float a = pow(2.0, -5.5 * sqrt(sat(uD.y * g8.r)))', 'vec3 F0 = vec3(uD.z * g8.g)',
                     'mix(uA.rgb, uC.rgb, hA) * c5 + uC.w * windowEnvironment(reflect(-V, Ns), a)',
                     'color = Ld * body + glassSpec(Ns, V, L1, L2, C1, C2, F0, a, ambient)'):
            self.assertIn(text, branch)

    def test_captured_values_are_recorded(self):
        c = tp.TAXI_GLASS_CONSTANTS
        self.assertEqual((c['writer'], c['game_equivalence'], c['alpha_test']), ('4E9B2CB3', False, 0.0156863))
        self.assertEqual((c['fade_scale'], c['fade_bias']), (0.071561, -2.33333))
        self.assertEqual((c['frozen_inputs']['instance_flags'], c['frozen_inputs']['attr4_w']), (0x2061, 0.0))
        self.assertEqual(tp.TAXI_GLASS_CAPTURED_BLEND, 'alpha_over')
        self.assertEqual(tp.TAXI_GLASS_MATERIAL, MATERIAL)

    def test_closed_forms(self):
        alpha, cut = alpha_closed_form(0.5, 0.2, 0.4)
        self.assertAlmostEqual(alpha, 0.3)
        self.assertFalse(cut)
        self.assertEqual(alpha_closed_form(0.5, 0.0, 0.02)[1], True)          # below 4/255 is discarded
        self.assertEqual(alpha_closed_form(0.5, 1.0, 1.0), (0.5 * 2.0, False))
        self.assertEqual(offset_closed_form(0.05, 1.0, 10.0, 0.071561, -2.33333, 0.4, 0.2)[0], -0.05 * 0.4)   # captured depths: no fade
        near, far = (offset_closed_form(0.05, 1.0, d, 0.071561, -2.33333, 1.0, 0.0)[0] for d in (10.0, 60.0))
        self.assertLess(abs(far), abs(near))

    def test_blender_front_end_and_validator_agree(self):
        self.assertEqual(validation.GLASS_CONSTANTS['glass_taxi'],
                         ('tint', 'tint dense', 'height weight', 'gloss scale', 'f0 scale', 'alpha scale', 'alpha test'))
        params = dict(tint=[0.1, 0.2, 0.3], tint_dense=[0.4, 0.5, 0.6], height_weight=0.5, parallax_depth=0.05, alpha_test=0.0157,
                      gloss_scale=1.0, f0_scale=0.75, env_weight=0.25, normal_scale=1.0, alpha_scale=1.0)
        rule = dict(applies=True, ported=['glass_taxi'], terms={'glass_taxi': dict(kind='glass_taxi', params=params, blend='alpha_over')},
                    textures={'_c': None, '_g': None, '_pick': ALPHA_H, '_n': WINDOW_N, '_window_c': WINDOW_C, '_window_g': WINDOW_G})
        names, roles = validation._viewer_required(rule, False)
        self.assertEqual(set(names) <= set(validation._viewer_expected_constants(rule)), True)
        self.assertEqual(roles, ['_n', '_pick', '_window_c', '_window_g'])
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_glass')[1].split('def apply_viewer_graph_rule')[0]
        for name in ('glass tint', 'glass tint dense', 'glass height weight', 'glass alpha test', 'glass alpha scale', 'glass f0 scale',
                     'glass gloss scale', 'glass taxi alpha_H', 'glass taxi window _c', 'glass taxi window _g', 'glass taxi normal map',
                     'VR glass lit'):
            self.assertIn(name, body)
        self.assertIn("'glass_taxi'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class ArchiveRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ.get('RC2016_GAME_ROOT', os.environ['RC2016_RESEARCH']))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(root)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_viewer_rule_from_the_archive(self):
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        forward, p = paths['forward'], paths['forward']['params']
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['blend_source'], forward['graph_sha256']),
                         ('glass_taxi', '4E9B2CB3', 'alpha_over', 'captured draw state', GRAPH_SHA))
        self.assertEqual((paths['_pick'], paths['_n'], paths['_window_c'], paths['_window_g']), (ALPHA_H, WINDOW_N, WINDOW_C, WINDOW_G))
        self.assertEqual([round(x, 5) for x in p['tint_dense']], list(CAPTURED_BLOCK[4:7]))
        self.assertEqual((p['env_weight'], p['gloss_scale'], p['f0_scale'], p['normal_scale']),
                         (CAPTURED_BLOCK[15], CAPTURED_BLOCK[8], CAPTURED_BLOCK[10], 1.0))
        self.assertEqual([round(x, 5) for x in p['tint']], list(CAPTURED_BLOCK[:3]))
        self.assertEqual((p['height_weight'], round(p['parallax_depth'], 5), p['rotation_phase'], p['rotation_rate']),
                         (CAPTURED_BLOCK[7], CAPTURED_BLOCK[14], 0.0, 0.0))
        self.assertEqual((p['alpha_scale'], p['game_equivalence']), (1.0, False))
        # evidence covers this material only, and both alpha_H slots must read one image
        with patch.object(tp, 'texture_slots', return_value={0x49A7866BA1D220DE: 'textures/other.texture'}):
            self.assertNotIn('forward', tp.material_texture_paths(self.archive, MATERIAL))


if __name__ == '__main__':
    unittest.main()
