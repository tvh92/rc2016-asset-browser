"""Deplanetizer exterior rules: dome glass 39FDC0B9, dwarf star 05C96D48, hangar glow FDF7FDFB (docs/material-decoding/
deplanetizer-exterior.md).

Synthetic checks (rule binding, uniform packing, GLSL text, closed forms, validator constants) always run. The archive checks read
the real materials and skip without RC2016_RESEARCH; the comparison with the research evaluation script skips without the local
Research/ folder.
"""
import importlib.util
import math
import os
import unittest
from pathlib import Path

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.grey_g_graphs import GREY_G_GRAPHS
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms, record_colour_encode

ROOT = Path(__file__).resolve().parents[1]
GLASS_GRAPH = 'materialgraph/environment/deplanetizer/architecture/dep_arch_glass_panels_01/dep_arch_glass_panels_01.materialgraph'
GLASS_SHA = '5cf25d6c0899f68a5e5a2b663e3d3ea56dc9d10731df0af1355d29afe0b0b62c'
GLASS_MATERIAL = 'material/environment/deplanetizer/architecture/dep_arch_glass_panels_01/rv_dep_arch_star_chamber_glass_green.material'
GLASS_TEXTURE = 'textures/environment/deplanetizer/architecture/rv_dep_arch_star_chamber_glass_01/rv_dep_arch_star_chamber_glass_01'
STAR_GRAPH = ('materialgraph/visualeffect/environment/deplanetizer/gameplay/dep_gp_dwarf_star_surface_calm_01/'
              'dep_gp_dwarf_star_surface_calm_01.materialgraph')
STAR_SHA = '5e0fcc3095eea11a8761bddadacf8c18bde55c82011a55981336074db259091f'
STAR_MATERIAL = ('material/visualeffect/environment/deplanetizer/gameplay/dep_gp_dwarf_star_surface_calm_01/'
                 'dep_gp_dwarf_star_surface_calm_01.material')
NOISE = 'textures/visualeffect/noise/'
GLOW_GRAPH = 'materialgraph/global/basic_flat_color/basic_flat_color_incandescent.materialgraph'
GLOW_SHA = '960498e0647f65882b7e00f21512ae78ea613633d1dba426469f6d1227ff74a4'
GLOW_MATERIAL = 'material/environment/metropolis/ambient/met_amb_light_filament_01/dep_arch_hangar_glow.material'
EVAL_STAR = ROOT / 'Research/captures/2026-11-11_deplanetizer-glass/star/eval_star.py'


def sat(x):
    return max(0.0, min(1.0, x))


def glass_terms(nv, rim_bias, gloss_cb, f0_base, g_r, g_g, alpha_scale=1.0):
    """39FDC0B9 material terms at N'.V: (alpha, E, gloss, F0) as the uKind 21 branch computes them."""
    rim = abs(1.001 - sat(nv)) ** 0.65
    a0 = sat(rim_bias + rim)
    e = rim ** 1.678
    return sat(a0 * alpha_scale), e, sat(gloss_cb + e * (g_r - gloss_cb)), f0_base + e * (g_g - f0_base)


def star_colour(s0x, s1, n2, n3, n4, f, t=0.0, k=0.0, amplitude=1.0 / 12.0, record=(1.0, 1.0, 1.0), fog=1.0):
    """05C96D48 colour as the uKind 22 branch computes it from the five samples (s0.z reads 0); also returns U - u, V - v."""
    du = 0.04 * (s1[0] + 0.2746773 * (s0x - s1[0]))
    dv = 0.04 * (s1[1] + 0.2746773 * (0.0 - s1[1]))
    m = min(1.0, abs(min(1.0, abs(n3 - n2)) - (0.9 * n4 - 0.7)))
    g = (0.0 if k == 0.0 else sat(abs(k) ** 0.4)) + 0.05 * math.sin(t * (5.0 * k + 3.0))
    q = max(1.0 - m, 0.0)
    nc = (q ** (4.0 - 2.5 * g), q ** (20.0 - 15.0 * g), q ** (100.0 - 70.0 * g))
    rm = (0.4072402 * f ** 0.1, 0.06847817 * f ** 3, 0.0)
    s = record_colour_encode(record)
    out = []
    for c in range(3):
        inner = rm[c] + 0.16202937 * (nc[c] - rm[c])
        out.append(3.0 * amplitude * (3.0 * g + 4.0) * fog * s[c] * (nc[c] + f * (inner - nc[c])))
    return out, (du, dv)


def glow_emission(colour, k3, k5):
    """FDF7FDFB emission E = C (K3 C.r + K5)."""
    return [c * (k3 * colour[0] + k5) for c in colour]


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hashes_bind_the_rules(self):
        for graph, sha, kind, writer in ((GLASS_GRAPH, GLASS_SHA, 'glass_tinted_spec', '39FDC0B9'),
                                         (STAR_GRAPH, STAR_SHA, 'dwarf_star', '05C96D48'),
                                         (GLOW_GRAPH, GLOW_SHA, 'flat_incandescent', 'FDF7FDFB')):
            rule = tp._graph_rule([graph], {graph: sha})
            self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), (kind, writer, sha))
            self.assertEqual(tp._graph_rule([graph], {graph: '0' * 64}), {})
            self.assertEqual(tp._graph_rule([graph], {}), {})
        self.assertEqual(tp._graph_rule([GLASS_GRAPH], {GLASS_GRAPH: GLASS_SHA})['slots'],
                         {'_n': 0x49A7866B7AE34E7A, '_window_g': 0x49A7866B1576D848})
        self.assertEqual(tp._graph_rule([STAR_GRAPH], {STAR_GRAPH: STAR_SHA})['slots'],
                         {'_window_mask': 0x49A7866B734CB25A, '_window_n1': 0x49A7866B4AC18E9F, '_window_n2': 0x49A7866BABDAD780,
                          '_window_c': 0x49A7866B6437A619, '_pick': 0x49A7866B2EA0E356})
        self.assertEqual(tp.FLAT_INCANDESCENT_KEYS, dict(colour=0x7F058428, k3=0x600B83DC, f0=0x62835AF9, k5=0x77591534, gloss=0x24F4CA4C))

    def test_glass_graph_left_the_grey_fallbacks(self):
        self.assertNotIn(GLASS_GRAPH, GREY_G_GRAPHS)
        self.assertNotIn(GLASS_SHA, GREY_G_GRAPHS.values())

    def test_basic_flat_color_keeps_its_own_rule(self):
        self.assertFalse(GLOW_GRAPH.startswith('materialgraph/global/basic_flat_color/basic_flat_color.'))

    def test_labelled_presentation_values(self):
        star = tp.DWARF_STAR_CONSTANTS
        self.assertEqual((star['amplitude'], star['time'], star['record_colour'], star['game_equivalence']), (1.0 / 12.0, 0.0, (1.0, 1.0, 1.0), False))
        self.assertEqual((star['dissolve'], star['alpha_cut'], star['alpha_scale'], star['fog_transmittance']), (1.0, 0.0, 1.0, 1.0))
        glass = tp.DEPLANETIZER_GLASS_CONSTANTS
        self.assertEqual((glass['alpha_test'], glass['normal_scale'], glass['game_equivalence']), (0.0156863, 1.0, False))
        self.assertEqual((tp.FLAT_INCANDESCENT_CONSTANTS['alpha_scale'], tp.FLAT_INCANDESCENT_CONSTANTS['game_equivalence']), (1.0, False))


class UniformTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual((GLASS_KINDS['glass_tinted_spec'], GLASS_KINDS['dwarf_star'], GLASS_KINDS['flat_incandescent']), (21, 22, 23))
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))

    def test_glass_packing(self):
        p = dict(tint=(0.1, 0.2, 0.3), rim_bias=0.352, gloss=0.806, f0_base=1.0, uv_scale=0.5, normal_scale=1.0, alpha_scale=1.0,
                 alpha_test=0.0156863)
        self.assertEqual(glass_uniforms(dict(kind='glass_tinted_spec', params=p)),
                         (21, (0.1, 0.2, 0.3, 0.352), (0.806, 1.0, 0.5, 1.0), (1.0, 0.0156863, 0.0, 0.0), (0.0,) * 4, (0.0,) * 4))

    def test_star_packing_and_clock(self):
        p = dict(amplitude=1.0 / 12.0, time=0.0, k=0.0, record_colour=(1.0, 1.0, 1.0), fog_transmittance=1.0, dissolve=1.0,
                 alpha_cut=0.0, alpha_scale=1.0)
        kind, a, b, c, *_ = glass_uniforms(dict(kind='dwarf_star', params=p))
        self.assertEqual((kind, a, c), (22, (1.0 / 12.0, 0.0, 0.0, 0.0), (1.0, 0.0, 1.0, 0.0)))
        for x in b:
            self.assertAlmostEqual(x, 1.0)   # white record colour encodes to 1
        self.assertEqual(glass_uniforms(dict(kind='dwarf_star', params=p), 2.5)[1][1], 2.5)
        from rc2016.viewer.viewport import ModelViewport
        self.assertIn('dwarf_star', ModelViewport.EFFECT_KINDS)
        self.assertAlmostEqual(record_colour_encode((0.5,))[0], 1.055 * 0.5 ** (1 / 2.4) - 0.055)
        self.assertEqual(record_colour_encode((0.0,))[0], 0.0)   # max(0, -0.055): no linear segment

    def test_glow_packing(self):
        p = dict(colour=[0.049707, 0.658375, 0.693872], emission_scale=1.248535, f0=0.01, gloss=0.5, alpha_scale=1.0)
        self.assertEqual(glass_uniforms(dict(kind='flat_incandescent', params=p)),
                         (23, (0.049707, 0.658375, 0.693872, 1.248535), (0.01, 0.5, 1.0, 0.0), (0.0,) * 4, (0.0,) * 4, (0.0,) * 4))


class GlslTests(unittest.TestCase):
    def branch(self, kind, following):
        return GLASS_FRAGMENT.split('uKind == %d)' % kind)[1].split('uKind == %d)' % following)[0]

    def test_glass_branch(self):
        branch = self.branch(21, 22)
        for text in ('vec2 uvs = vUV * uB.z', 'vec3 n = normalize(vNrm), T = -Pu, B = cross(n, T)',
                     '2.0 * uB.w * (1.0 - uC.z) * (texture2D(uTexN, uvs).rg - 0.5)',
                     'face * normalize(T * xy.x + B * xy.y + n * (1.0 - dot(xy, xy)))',
                     'float rim = pow(abs(1.001 - sat(dot(Ns, V))), 0.65)', 'float a0 = sat(uA.w + rim)', 'if (a0 < uC.y) discard',
                     'alpha = sat(a0 * uC.x)', 'float E = pow(rim, 1.678)', 'texture2D(uTexWindowMask, uvs).rg',
                     'pow(2.0, -5.5 * sqrt(sat(mix(uB.x, g8.r, E))))', 'vec3 F0 = vec3(mix(uB.y, g8.g, E))',
                     'color = uA.rgb * glassSpec(Ns, V, L1, L2, C1, C2, F0, a, ambientLight(Ns))'):
            self.assertIn(text, branch)
        self.assertNotIn('Ld', branch)   # no diffuse or body term

    def test_star_branch(self):
        branch = self.branch(22, 23)
        for text in ('vec2(2.0 * u + 0.01 * t, v - 0.03 * t)', 'vec2(5.0 * u + 0.025 * t, 4.0 * v + 0.025 * t)',
                     'float s0z = 0.0', 'u + 0.04 * (s1.x + 0.2746773 * (s0x - s1.x))', 'v + 0.04 * (s1.y + 0.2746773 * (s0z - s1.y))',
                     'vec2(7.0 * U - 0.14 + 0.03 * t, 4.5 * Vs - 0.09 - 0.002 * t)', 'vec2(8.0 * U - 0.16 + 0.02 * t, 5.0 * Vs - 0.1 + 0.005 * t)',
                     'vec2(4.0 * U - 0.08 + 0.015 * t, 3.0 * Vs - 0.06 + 0.005 * t)',
                     'min(1.0, abs(min(1.0, abs(n3 - n2)) - (0.9 * n4 - 0.7)))', 'sat(0.95 - 0.25 * m)',
                     'sat(5.0 * (b - (1.0 - uC.x)) / max(1e-5, uC.x))', 'if (d < 1.0 || d < uC.y) discard',
                     '0.05 * sin(t * (5.0 * k + 3.0))', 'pow(1.001 - ndv, 3.0)',
                     'vec3(pow(q, 4.0 - 2.5 * g), pow(q, 20.0 - 15.0 * g), pow(q, 100.0 - 70.0 * g))',
                     'vec3(0.4072402 * pow(f, 0.1), 0.06847817 * f * f * f, 0.0)',
                     '3.0 * uA.x * (3.0 * g + 4.0) * uB.w * uB.rgb * mix(Nc, mix(Rm, Nc, 0.16202937), f)'):
            self.assertIn(text, branch)

    def test_glow_branch(self):
        branch = GLASS_FRAGMENT.split('uKind == 23)')[1].split('} else {')[0]
        self.assertIn('glassSpec(N, V, L1, L2, C1, C2, vec3(uB.x), a, ambientLight(N)) + uA.rgb * uA.w', branch)
        self.assertIn('pow(2.0, -5.5 * sqrt(sat(uB.y)))', branch)
        self.assertIn('alpha = sat(uB.z)', branch)


class ClosedFormTests(unittest.TestCase):
    def test_glass_alpha_and_weight_match_the_research_evaluation(self):
        # eval_glass.log, pass 0 at UV (0.50, 0.50): (N'.V, alpha, E) for 0, 45 and 75 degrees; P3 = 0.352, attr0.w = 1
        for nv, alpha, e in ((0.9978, 0.3761, 0.00193), (0.7528, 0.7563, 0.21876), (0.3228, 1.0, 0.65475), (0.1016, 1.0, 0.89080)):
            a, weight, _, _ = glass_terms(nv, 0.352, 0.806, 1.0, 0.0, 0.0)
            self.assertAlmostEqual(a, alpha, delta=5e-4)
            self.assertAlmostEqual(weight, e, delta=5e-4)
        # E = 0: the CB values; E = 1: the _g channels
        _, _, gloss, f0 = glass_terms(1.0, 0.352, 0.806, 1.0, 0.04, 0.06)   # rim = 0.001^0.65 at N'.V = 1
        self.assertAlmostEqual(gloss, 0.806, delta=1e-3)
        self.assertAlmostEqual(f0, 1.0, delta=1e-3)
        _, _, gloss, f0 = glass_terms(0.0, 0.352, 0.806, 1.0, 0.04, 0.06)
        self.assertAlmostEqual(gloss, 0.04, delta=2e-3)   # E = 1.001^1.0907 at N'.V = 0
        self.assertAlmostEqual(f0, 0.06, delta=2e-3)

    def test_star_ramp(self):
        # m = 0 facing (f ~ 0): E = (1, 1, 1), and 12 A = 1 at the presentation amplitude
        rgb, _ = star_colour(0.0, (0.0, 0.0), 0.0, 0.0, 7.0 / 9.0, 0.0)
        self.assertEqual([round(x, 6) for x in rgb], [1.0, 1.0, 1.0])
        # grazing (f = 1): E.r = 0.4072402 (1 - 0.16202937) + 0.16202937 N.r, E.g = 0.06847817 (1 - 0.162) + 0.162 N.g, E.b = 0.162 N.b
        rgb, _ = star_colour(0.0, (0.0, 0.0), 0.0, 0.0, 7.0 / 9.0, 1.0)
        self.assertAlmostEqual(rgb[0], 0.4072402 * (1 - 0.16202937) + 0.16202937)
        self.assertAlmostEqual(rgb[1], 0.06847817 * (1 - 0.16202937) + 0.16202937)
        self.assertAlmostEqual(rgb[2], 0.16202937)
        # cooling noise: R falls as (1 - m)^4, G as (1 - m)^20, B as (1 - m)^100
        rgb, _ = star_colour(0.0, (0.0, 0.0), 0.0, 0.0, 0.0, 0.0)   # m = |0 - (-0.7)| = 0.7
        self.assertAlmostEqual(rgb[0], 0.3 ** 4)
        self.assertAlmostEqual(rgb[1], 0.3 ** 20)
        # k = 0 breathes by 0.05 sin(3 t): scale 3 A (3 g + 4)
        bright, _ = star_colour(0.0, (0.0, 0.0), 0.0, 0.0, 7.0 / 9.0, 0.0, t=math.pi / 6.0)
        self.assertAlmostEqual(bright[0], 3.0 / 12.0 * (3.0 * 0.05 + 4.0))   # sin(3 t) = 1: g = 0.05

    def test_glow_emission_matches_the_decode(self):
        e = glow_emission([0.049707, 0.658375, 0.693872], 5.0, 1.0)
        self.assertEqual([round(x, 5) for x in e], [0.06206, 0.822, 0.86632])


@unittest.skipUnless(EVAL_STAR.exists() and os.environ.get('RC2016_RESEARCH'), 'Research/ evaluation script and RC2016_RESEARCH are required')
class ResearchEvaluationTests(unittest.TestCase):
    def test_star_matches_eval_star(self):
        import numpy as np
        spec = importlib.util.spec_from_file_location('eval_star', EVAL_STAR)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        rng = np.random.default_rng(7)
        for _ in range(20):
            s0x, s1x, s1y, n2, n3, n4 = rng.random(6)
            f, t = float(rng.random()), float(rng.random() * 4.0)
            tex = {0: np.full((4, 4, 4), (s0x, 0.5, 0.0, 1.0), np.float32), 1: np.full((4, 4, 4), (s1x, s1y, 0.0, 1.0), np.float32),
                   2: np.full((4, 4, 4), n2, np.float32), 3: np.full((4, 4, 4), n3, np.float32), 4: np.full((4, 4, 4), n4, np.float32)}
            uv = np.array([[0.3]], np.float32)
            reference, _, keep = module.star(tex, uv, uv, f, t)
            mine, _ = star_colour(float(np.float32(s0x)), (float(np.float32(s1x)), float(np.float32(s1y))), float(np.float32(n2)),
                                  float(np.float32(n3)), float(np.float32(n4)), f, t=t)
            self.assertTrue(bool(keep[0, 0]))
            for c in range(3):
                self.assertAlmostEqual(mine[c], float(reference[0, 0, c]), delta=1e-5)


class BlenderFrontEndTests(unittest.TestCase):
    def test_validator_constants_and_roles(self):
        self.assertEqual(validation.GLASS_CONSTANTS['glass_tinted_spec'],
                         ('tint', 'rim bias', 'gloss', 'f0 base', 'uv scale', 'normal scale', 'alpha scale', 'alpha test'))
        self.assertEqual(validation.GLASS_CONSTANTS['dwarf_star'], ('amplitude', 'time', 'k', 'record colour', 'fog transmittance'))
        self.assertEqual(validation.GLASS_CONSTANTS['flat_incandescent'], ('colour', 'emission scale', 'f0', 'gloss', 'alpha scale'))
        glass = dict(tint=[0.1, 0.2, 0.3], rim_bias=0.352, gloss=0.806, f0_base=1.0, uv_scale=0.5, normal_scale=1.0, alpha_scale=1.0,
                     alpha_test=0.0156863)
        star = dict(amplitude=1.0 / 12.0, time=0.0, k=0.0, record_colour=[1.0, 1.0, 1.0], fog_transmittance=1.0)
        glow = dict(colour=[0.05, 0.66, 0.69], emission_scale=1.25, f0=0.01, gloss=0.5, alpha_scale=1.0)
        cases = (('glass_tinted_spec', glass, 'alpha_over', {'_n': GLASS_TEXTURE + '_n.texture', '_window_g': GLASS_TEXTURE + '_g.texture'},
                  ['_n', '_window_g']),
                 ('dwarf_star', star, 'opaque', {'_window_mask': 'a', '_window_n1': 'b', '_window_n2': 'c', '_window_c': 'd', '_pick': 'e'},
                  ['_window_mask', '_window_n1', '_window_n2', '_window_c', '_pick']),
                 ('flat_incandescent', glow, 'additive', {}, []))
        for kind, params, blend, textures, wanted in cases:
            rule = dict(applies=True, ported=[kind], terms={kind: dict(kind=kind, params=params, blend=blend)},
                        textures={'_c': None, '_g': None, **textures})
            names, roles = validation._viewer_required(rule, False)
            self.assertTrue(set(names) <= set(validation._viewer_expected_constants(rule)), kind)
            self.assertEqual(roles, wanted, kind)
        text = (ROOT / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        kinds = text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0]
        for kind in ('glass_tinted_spec', 'dwarf_star', 'flat_incandescent'):
            self.assertIn("'%s'" % kind, kinds)
        body = text.split("elif key == 'glass_tinted_spec':")[1].split("elif key == 'window_glass':")[0]
        for name in ['glass ' + n for key in ('glass_tinted_spec', 'dwarf_star', 'flat_incandescent') for n in validation.GLASS_CONSTANTS[key]]:
            self.assertIn("'%s'" % name, body)
        for name in ('glass dome normal map', 'glass dome _g', "'glass star '", "'noisetile04_n'", "'noise_3channel'", "'flame_05'",
                     "'flame_03'", "'sun_base_noise'", 'VR glass emission', 'VR glass lit'):
            self.assertIn(name, body)


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

    def test_dome_glass(self):
        paths = tp.material_texture_paths(self.archive, GLASS_MATERIAL)
        forward, p = paths['forward'], paths['forward']['params']
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['two_sided'], forward['graph_sha256']),
                         ('glass_tinted_spec', '39FDC0B9', 'alpha_over', False, GLASS_SHA))   # flags 0x3a
        self.assertEqual((paths['_n'], paths['_window_g']), (GLASS_TEXTURE + '_n.texture', GLASS_TEXTURE + '_g.texture'))
        self.assertNotIn('_c', paths)   # the material binds a _c the graph does not declare: never sampled
        self.assertEqual([round(x, 7) for x in p['tint']], [0.1169707, 0.3564001, 0.0168074])
        self.assertEqual((round(p['rim_bias'], 6), round(p['gloss'], 6), p['f0_base'], p['uv_scale'], p['alpha_scale']),
                         (0.352, 0.806, 1.0, 0.5, 1.0))
        # the graph's opaque material is not the decoded blended forward draw: it keeps the grey _g reading
        opaque = tp.material_texture_paths(self.archive, GLASS_MATERIAL.rsplit('/', 1)[0] + '/tb_nov_window_glass_01_opaque.material')
        self.assertEqual((opaque.get('forward'), opaque['g_mode']), (None, 'grey'))

    def test_dwarf_star(self):
        paths = tp.material_texture_paths(self.archive, STAR_MATERIAL)
        forward, p = paths['forward'], paths['forward']['params']
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['two_sided'], forward['graph_sha256']),
                         ('dwarf_star', '05C96D48', 'opaque', True, STAR_SHA))   # flags 0x20C
        self.assertEqual({role: paths[role] for role in ('_window_mask', '_window_n1', '_window_n2', '_window_c', '_pick')},
                         {'_window_mask': NOISE + 'fx_noisetile04_n.texture', '_window_n1': NOISE + 'fx_noise_3channel_01.texture',
                          '_window_n2': NOISE + 'hydro_noise_flame_05_sm.texture', '_window_c': NOISE + 'hydro_noise_flame_03_sm.texture',
                          '_pick': NOISE + 'sun_base_noise_ao_01.texture'})
        self.assertNotIn('_n', paths)
        self.assertEqual(p['k'], 0.0)

    def test_hangar_glow(self):
        paths = tp.material_texture_paths(self.archive, GLOW_MATERIAL)
        forward, p = paths['forward'], paths['forward']['params']
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['two_sided'], forward['graph_sha256']),
                         ('flat_incandescent', 'FDF7FDFB', 'additive', False, GLOW_SHA))   # flags 0x239
        self.assertEqual([round(x, 6) for x in p['colour']], [0.049707, 0.658375, 0.693872])
        self.assertEqual((p['k3'], p['k5'], round(p['f0'], 6), p['gloss']), (5.0, 1.0, 0.01, 0.5))
        self.assertEqual([round(x, 5) for x in glow_emission(p['colour'], p['k3'], p['k5'])], [0.06206, 0.822, 0.86632])
        self.assertAlmostEqual(p['emission_scale'], 1.248533, places=6)
        self.assertNotIn('flat_surface', paths)

    def test_blender_rule(self):
        from rc2016.formats.materials import viewer_graph_rule
        for material, kind in ((GLASS_MATERIAL, 'glass_tinted_spec'), (STAR_MATERIAL, 'dwarf_star'), (GLOW_MATERIAL, 'flat_incandescent')):
            rule = viewer_graph_rule(self.archive, material)
            self.assertTrue(rule['applies'], material)
            self.assertEqual((rule['ported'], rule['unported']), ([kind], []), material)


if __name__ == '__main__':
    unittest.main()
