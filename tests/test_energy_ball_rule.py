"""Test dummy energy ball DB658C65 (enm_test_dummy_energy_ball; docs/material-decoding/test-dummy-energy-ball.md).

Synthetic checks (rule binding, uniform packing, GLSL text, closed form, validator constants, Blender front-end text) always run.
The archive checks read the real material and skip without RC2016_RESEARCH.
"""
import math
import os
import unittest
from pathlib import Path

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, frame_fade, glass_uniforms

ROOT = Path(__file__).resolve().parents[1]
GRAPH = 'materialgraph/character/enemy/enm_test_dummy_energy_ball/enm_test_dummy_energy_ball.materialgraph'
SHA = '8a7ed8e33dcdd3eeef03b9b13341cfdda4ab386a83a850bfde544b2fe3596cc3'
MATERIAL = 'material/character/enemy/enm_test_dummy_energy_ball/enm_test_dummy_energy_ball.material'
MODEL = 'character/enemy/enm_test_dummy/enm_test_dummy.model'
ELECTRICITY = 'textures/visualeffect/electricity/electricity_line_01/electricity_line_01.texture'
BALL = 'textures/visualeffect/character/enm/test_dummy_energy_ball/test_dummy_energy_ball.texture'
STAR_MATERIAL = ('material/visualeffect/environment/deplanetizer/gameplay/dep_gp_dwarf_star_surface_calm_01/'
                 'dep_gp_dwarf_star_surface_calm_01.material')
COLOUR = (0.8894421, 5.0, 4.523306)


def ball(e_rgb, n1, n2, ndv, r, amplitude=0.18306638300418854, fog=1.0, cut=4 / 255, opacity=1.0):
    """DB658C65 from its three samples: (rgb, alpha), or None where the program discards."""
    pick = math.floor(3.0 * r)
    e = e_rgb[0] if pick < 1 else e_rgb[1] if pick < 2 else e_rgb[2]
    s_sum = e + n1 + 0.219 * n2 + min(1.0, abs(1.001 - ndv))
    s = max(0.0, min(1.0, s_sum))
    if s < cut:
        return None
    return tuple(amplitude * fog * (1.0 + s_sum) * c for c in COLOUR), max(0.0, min(1.0, opacity * s))


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hash_binds_the_rule(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('energy_ball', 'DB658C65', SHA))
        self.assertEqual(rule['slots'], {'_window_mask': 0x49A7866B2EA0E356, '_window_n1': 0x49A7866B0056CBD0,
                                         '_window_n2': 0x49A7866BABDAD780})
        self.assertEqual(rule['params'], ())   # the graph has no CB2
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})

    def test_captured_frame_values(self):
        c = tp.ENERGY_BALL_CONSTANTS
        # CB0 dwords 115, 99 and 107 of the captured draw (bits 0x3E3B75C1, 0x42EA1FDC, 0x3EB95810)
        self.assertEqual((c['amplitude'], c['time'], c['frame_random']), (0.18306638300418854, 117.06222534179688, 0.3619999885559082))
        self.assertEqual((c['fog_transmittance'], c['frame_rate'], c['presentation'], c['game_equivalence']), (1.0, 30.0, False, False))
        self.assertEqual((c['writer'], c['captured_program']), ('DB658C65', '817D25E0'))

    def test_loader_uploads_every_forward_window_role(self):
        self.assertEqual(tp.FORWARD_WINDOW_ROLES, ('_window_mask', '_window_n1', '_window_n2', '_window_c', '_window_g'))
        from rc2016.viewer.viewport import GL_TEXTURE_ROLES
        self.assertTrue(set(tp.FORWARD_WINDOW_ROLES) <= set(GL_TEXTURE_ROLES))


class UniformTests(unittest.TestCase):
    PARAMS = dict(amplitude=0.18306638300418854, time=117.06222534179688, frame_random=0.3619999885559082, frame_rate=30.0,
                  fog_transmittance=1.0, alpha_cut=4 / 255, alpha_scale=1.0)

    def test_kind(self):
        self.assertEqual(GLASS_KINDS['energy_ball'], 25)
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))

    def test_packing_and_clock(self):
        p = self.PARAMS
        self.assertEqual(glass_uniforms(dict(kind='energy_ball', params=p)),
                         (25, (p['amplitude'], p['time'], p['frame_random'], 0.0), (1.0, 4 / 255, 1.0, 0.0), (0.0,) * 4, (0.0,) * 4,
                          (0.0,) * 4))
        animated = glass_uniforms(dict(kind='energy_ball', params=p), 2.5)[1]
        self.assertEqual(animated[1:3], (2.5, frame_fade(2.5, 30.0)))   # the effect clock and the per-frame CB0[107] stand-in
        from rc2016.viewer.viewport import ModelViewport
        self.assertIn('energy_ball', ModelViewport.EFFECT_KINDS)
        self.assertIn('energy_ball', ModelViewport.HDR_FORWARD_KINDS)


class GlslTests(unittest.TestCase):
    def test_branch(self):
        branch = GLASS_FRAGMENT.split('uKind == 25)')[1].split('} else {')[0]
        for text in ('float t = uA.y, r = uA.z, u = vUV.x, v = vUV.y',
                     'texture2D(uTexWindowMask, vec2(1.0 - 2.0 * v - r, u + r)).rgb', 'float pick = floor(3.0 * r)',
                     'pick < 1.0 ? e3.r : (pick < 2.0 ? e3.g : e3.b)',
                     'texture2D(uTexWindowN1, vec2(0.75 * u + 0.125, v - 0.25 * t)).r',
                     'texture2D(uTexWindowN2, vec2(u, v - 0.5 * t + 0.474)).r',
                     'float S = e + n1 + 0.219 * n2 + min(1.0, abs(1.001 - ndv))', 'if (s < uB.y) discard', 'alpha = sat(uB.z * s)',
                     'color = uA.x * uB.x * (1.0 + S) * vec3(0.8894421, 5.0, 4.523306)'):
            self.assertIn(text, branch)
        self.assertNotIn('glassSpec', branch)   # the program reads no light


class ClosedFormTests(unittest.TestCase):
    def test_channel_pick_and_colour(self):
        # r = 0.362 (the captured frame): floor(3 r) = 1 picks the G bolt
        rgb, alpha = ball((0.9, 0.2, 0.7), 0.0, 0.0, 1.0, 0.362)
        self.assertAlmostEqual(rgb[1], 0.18306638300418854 * 5.0 * (1.0 + 0.2 + 0.001))   # rim = |1.001 - 1| = 0.001
        self.assertAlmostEqual(alpha, 0.201)
        self.assertAlmostEqual(ball((0.9, 0.2, 0.7), 0.0, 0.0, 1.0, 0.1)[1], 0.901)        # R bolt
        self.assertAlmostEqual(ball((0.9, 0.2, 0.7), 0.0, 0.0, 1.0, 0.9)[1], 0.701)        # B bolt
        # grazing: rim 1, alpha 1; the colour keeps the unclamped S
        rgb, alpha = ball((0.0, 0.0, 0.0), 0.5, 1.0, 0.0, 0.5)
        self.assertEqual(alpha, 1.0)
        self.assertAlmostEqual(rgb[2], 0.18306638300418854 * 4.523306 * (1.0 + 0.5 + 0.219 + 1.0))
        # facing with dark samples: below the 4/255 cut-off the program discards
        self.assertIsNone(ball((0.0, 0.0, 0.0), 0.0, 0.0, 1.0, 0.5))


class BlenderFrontEndTests(unittest.TestCase):
    def test_validator_constants_and_roles(self):
        self.assertEqual(validation.GLASS_CONSTANTS['energy_ball'],
                         ('amplitude', 'time', 'frame random', 'fog transmittance', 'alpha cut', 'alpha scale'))
        params = dict(UniformTests.PARAMS)
        rule = dict(applies=True, ported=['energy_ball'], terms={'energy_ball': dict(kind='energy_ball', params=params, blend='alpha_over')},
                    textures={'_c': None, '_g': None, '_window_mask': ELECTRICITY, '_window_n1': BALL, '_window_n2': BALL})
        names, roles = validation._viewer_required(rule, False)
        self.assertTrue(set(names) <= set(validation._viewer_expected_constants(rule)))
        self.assertEqual(roles, ['_window_mask', '_window_n1', '_window_n2'])
        text = (ROOT / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        self.assertIn("'energy_ball'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])
        body = text.split("elif key == 'energy_ball':")[1].split("elif key == 'flat_incandescent':")[0]
        for name in ['glass ' + n for n in validation.GLASS_CONSTANTS['energy_ball']] + ["'electricity'", "'noise 1'", "'noise 2'",
                                                                                         'VR glass emission', '0.8894421, 5.0, 4.523306']:
            self.assertIn(name if name.startswith(("'", 'VR', '0.')) else "'%s'" % name, body)
        self.assertIn("'dwarf_star', 'energy_ball'", text.split("return limits + ['Forward emission-only writer")[0].rsplit('\n', 2)[-2])


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

    def test_material(self):
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        forward, p = paths['forward'], paths['forward']['params']
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['two_sided'], forward['graph_sha256']),
                         ('energy_ball', 'DB658C65', 'alpha_over', False, SHA))   # flags 0xA, as the captured draw state
        self.assertEqual({role: paths[role] for role in ('_window_mask', '_window_n1', '_window_n2')},
                         {'_window_mask': ELECTRICITY, '_window_n1': BALL, '_window_n2': BALL})
        self.assertTrue(all(role not in paths for role in ('_c', '_g', '_n', '_pick')))
        # attribute0.w and .z as captured: 1.0 and 4/255
        self.assertEqual(p['alpha_scale'], 1.0)
        self.assertAlmostEqual(p['alpha_cut'], 4 / 255, places=7)

    def test_texture_loader_loads_the_window_roles(self):
        entries = tp.material_textures(self.archive, [dict(index=0, path=MATERIAL), dict(index=1, path=STAR_MATERIAL)], {}, previews=False)
        self.assertTrue(all(entries[0].get(role) for role in ('_window_mask', '_window_n1', '_window_n2')))
        self.assertTrue(all(entries[1].get(role) for role in ('_window_mask', '_window_n1', '_window_n2', '_window_c', '_pick')))

    def test_blender_rule(self):
        from rc2016.formats.materials import viewer_graph_rule
        rule = viewer_graph_rule(self.archive, MATERIAL)
        self.assertTrue(rule['applies'])
        self.assertEqual((rule['ported'], rule['unported']), (['energy_ball'], []))
        self.assertEqual({role: rule['textures'][role] for role in ('_window_mask', '_window_n1', '_window_n2')},
                         {'_window_mask': ELECTRICITY, '_window_n1': BALL, '_window_n2': BALL})


if __name__ == '__main__':
    unittest.main()
