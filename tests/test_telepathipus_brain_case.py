"""Telepathipus brain case AE2B0B81, brain FBA51F6C and the inside shell (docs/material-decoding/telepathipus-brain-case.md).

Synthetic checks (rule binding, uniform packing, GLSL text, validator constants) always run. The archive-backed checks pin the
program identity, evaluate the project's own GCN decode of the brain case program (rc2016.research.forward_loops, independent of
the shadPS4 IR the rule was read from) against the closed form restated below, and read the three viewer readings from the
archive; they skip without RC2016_RESEARCH / RC2016_GAME_ROOT.
"""
import hashlib
import math
import os
import random
import unittest
from pathlib import Path

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms

CASE_GRAPH = ('materialgraph/environment/metropolis/ambient/met_amb_water_pool_01/'
              'enm_telepathipus_brain_case_exterior.materialgraph')
CASE_MATERIAL = 'material/character/enemy/enm_Telepathipus_Brain_Case/enm_Telepathipus_Brain_Case.material'
CASE_SHA = '353289b4eae8d1e0e3d943746c506664ee8ea284affc87b5dc6a8838ee4647ad'
CASE_CODE_SHA = 'eb99399526984cc1a722ba1a25d942826b3c4d4142bee8124e6e9e009496999e'
BRAIN_GRAPH = 'materialgraph/character/enemy/enm_telepathipus_brain/enm_telepathipus_brain.materialgraph'
BRAIN_MATERIAL = 'material/character/enemy/enm_Telepathipus_Brain/enm_Telepathipus_Brain.material'
BRAIN_SHA = '46c6c154732117c62abe9fe9f92ba34e7cc54335bc4aec5d5fe9829f0fbe8654'
INSIDE_MATERIAL = 'material/character/enemy/enm_Telepathipus_Brain_Case/enm_Telepathipus_Brain_Case_Inside.material'
CASE_TEXTURES = ('textures/character/enemy/enm_telepathipus/telepathipus_brain_case_g.texture',
                 'textures/character/enemy/enm_telepathipus/telepathipus_brain_case_n.texture',
                 'textures/environment/metropolis/ambient/met_amb_water_pool_flow_01_n.tga/met_amb_water_pool_flow_01_n.texture',
                 'textures/character/enemy/enm_telepathipus/telepathipus_brain_case_c.texture')


def sat(x):
    return max(0.0, min(1.0, x))


def _norm(v):
    length = math.sqrt(sum(x * x for x in v))
    return [x / length for x in v]


def case_closed_form(inp, s, light):
    """Exports of AE2B0B81 (pass word 0, instance flags 0): rgb = fog + t (albedo D + A S), alpha = sat(attribute0.w).
    s[(api, channel)]: API 0 the scene's linear depth, 2 the scene colour, 8 _c, 9 _n; light = diffuse (VGPR 14..16) and
    specular (VGPR 22..20) loop outputs. See the uKind 20 comment in viewer/shading.py."""
    n = _norm([inp['attribute2.%d' % i] for i in range(3)])
    t = _norm([inp['attribute3.%d' % i] for i in range(3)])
    b = _norm([n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0]])
    b = [x if inp['attribute3.3'] > 0 else -x for x in b]
    x, y = 2 * s[(9, 0)] - 1, 2 * s[(9, 1)] - 1
    z = 1 - x * x - y * y
    face = 1.0 if inp['ps.FRONT_FACE'] else -1.0
    rim_normal = [face * c for c in _norm([t[i] * x + b[i] * y + n[i] * z for i in range(3)])]
    view = _norm([inp['attribute4.%d' % i] for i in range(3)])   # camera minus fragment
    e = abs(1.001 - sat(sum(p * q for p, q in zip(rim_normal, view))))
    rim = sat((10 * e ** 4) ** 5)
    k = sat((abs(s[(0, 0)] - inp['ps.POS_W']) - 1) / 3)
    out = []
    for ch, (d, sp) in enumerate(((14, 22), (15, 21), (16, 20))):
        c = s[(8, ch)]
        albedo = s[(2, ch)] + (inp['CB2[%d]' % (4 + ch)] - s[(2, ch)]) * k
        albedo += (tp.BRAIN_CASE_RIM_COLOUR[ch] * c - albedo) * rim
        albedo += (c - albedo) * s[(8, 3)]
        facing = inp['CB2[%d]' % (8 + ch)]
        tint = sat(inp['CB2[14]'] * (facing + (inp['CB2[%d]' % ch] - facing) * e ** inp['CB2[13]']))
        fog = inp['CB0[%d]' % (112 + ch)]
        out.append(fog + inp['attribute4.3'] * (albedo * light[d] + light[sp] * tint - fog))
    return out, sat(inp['attribute0.3'])


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hashes_bind_the_rules(self):
        rule = tp._graph_rule([CASE_GRAPH], {CASE_GRAPH: CASE_SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('brain_case', 'AE2B0B81', CASE_SHA))
        self.assertEqual(rule['slots'], {'_window_mask': 0x49A7866B1576D848, '_window_n1': 0x49A7866B5E5B04FB,
                                         '_window_n2': 0x49A7866B492010B8, '_window_c': 0x49A7866B0056CBD0})
        self.assertEqual(tp._graph_rule([CASE_GRAPH], {CASE_GRAPH: '0' * 64}), {})
        rule = tp._graph_rule([BRAIN_GRAPH], {BRAIN_GRAPH: BRAIN_SHA})
        self.assertEqual((rule['g_mode'], rule['self_lit_key'], rule['slot_cgn'], rule['graph_sha256']), ('grey', None, True, BRAIN_SHA))
        self.assertEqual(tp._graph_rule([BRAIN_GRAPH], {}), {})

    def test_kind_and_uniform_packing(self):
        self.assertEqual(GLASS_KINDS['brain_case'], 20)
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))
        p = dict(body=[1.0, 0.5, 0.25], time=0.0, tint_grazing=[0.1, 0.0, 0.2], tint_exp=0.2, tint_facing=[1.0, 0.1, 0.0],
                 tint_scale=5.0, gloss_open=4.0, gloss_painted=10.0, f0_open=5.0, f0_painted=1.0, alpha_scale=1.0, normal_scale=1.0)
        self.assertEqual(glass_uniforms(dict(kind='brain_case', params=p)),
                         (20, (1.0, 0.5, 0.25, 0.0), (0.1, 0.0, 0.2, 0.2), (1.0, 0.1, 0.0, 5.0), (4.0, 10.0, 5.0, 1.0), (1.0, 1.0, 0.0, 0.0)))
        self.assertEqual(glass_uniforms(dict(kind='brain_case', params=p), 2.5)[1], (1.0, 0.5, 0.25, 2.5))

    def test_glsl_branch_carries_the_decoded_equations(self):
        body = GLASS_FRAGMENT.split('uKind == 20')[1].split('uKind == 17')[0].split('} else {\n        alpha = sat(pow(')[0]
        rim = 'vec3(%s)' % ', '.join('%.8f' % c for c in tp.BRAIN_CASE_RIM_COLOUR)
        for text in ('vec2(0.3 * vUV.x - 0.1 * t, 0.3 * vUV.y + 0.025 * t)', 'vUV - 0.4 * g0.b * q * f.y * vec2(-dot(T, V), dot(B, V))',
                     'float w = 3.0 * G.b * zm', 'n * (zm * zf)', 'abs(1.001 - sat(dot(normalize(T * m.x + B * m.y + n * zm), V)))',
                     'pow(2.0, -5.5 * sqrt(sat(G.r * mix(uD.x, uD.y, c4.a))))', 'sat(G.g * mix(uD.z, uD.w, c4.a))',
                     'clamp(uC.w * mix(uC.rgb, uB.rgb, pow(e, uB.w)), 0.0, 1.0)', 'sat(pow(10.0 * pow(e, 4.0), 5.0))',
                     '- 1.0) / 3.0)', rim, 'color = (rest + wbg * bg) * Ld + S', 'alpha = sat(uE.x) * (1.0 - wbg)'):
            self.assertIn(text, body)
        from rc2016.viewer.viewport import ModelViewport
        self.assertIn('brain_case', ModelViewport.HDR_FORWARD_KINDS)   # the scene copy is scene-linear

    def test_blender_front_end_and_validator_agree(self):
        self.assertEqual(validation.GLASS_CONSTANTS['brain_case'],
                         ('body', 'tint grazing', 'tint facing', 'tint exp', 'tint scale', 'gloss open', 'gloss painted', 'f0 open',
                          'f0 painted', 'rim colour', 'alpha scale'))
        params = dict(tp.BRAIN_CASE_CONSTANTS, body=[1.0, 0.5, 0.25], tint_grazing=[0.1, 0.0, 0.2], tint_exp=0.2,
                      tint_facing=[1.0, 0.1, 0.0], tint_scale=5.0, gloss_open=4.0, gloss_painted=10.0, f0_open=5.0, f0_painted=1.0,
                      alpha_scale=1.0)
        rule = dict(applies=True, ported=['brain_case'],
                    terms={'brain_case': dict(kind='brain_case', params=params, blend='alpha_over', two_sided=False)},
                    textures={'_c': None, '_g': None, '_n': None, '_window_mask': 'a', '_window_n1': 'b', '_window_n2': 'c',
                              '_window_c': 'd'})
        names, roles = validation._viewer_required(rule, False)
        self.assertTrue(set(names) <= set(validation._viewer_expected_constants(rule)))
        self.assertEqual(roles, ['_window_mask', '_window_n1', '_window_n2', '_window_c'])
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        self.assertIn("'brain_case'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])
        body = text.split("elif key == 'brain_case':")[1].split("elif key == 'glass_taxi':")[0]
        for name in ['glass ' + n for n in validation.GLASS_CONSTANTS['brain_case']]:
            self.assertIn("'%s'" % name, body)


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class ArchiveProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ.get('RC2016_GAME_ROOT', os.environ['RC2016_RESEARCH']))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import asset_bytes, open_archive
        from rc2016.shaders import translator
        cls.archive = open_archive(root)
        cls.case_graph = asset_bytes(cls.archive, CASE_GRAPH)
        cls.case = next(p for p in translator.pixel_programs(cls.case_graph) if p['shader_id'] == '0xae2b0b81')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_identities(self):
        from rc2016.levels.scene import asset_bytes
        self.assertEqual(hashlib.sha256(self.case_graph).hexdigest(), CASE_SHA)
        self.assertEqual(hashlib.sha256(asset_bytes(self.archive, BRAIN_GRAPH)).hexdigest(), BRAIN_SHA)
        self.assertEqual(self.case['code_sha256'], CASE_CODE_SHA)

    def test_brain_case_matches_the_program_numerically(self):
        from rc2016.research import forward_loops as loops
        compiler = loops.compile_opaque(self.case)
        roots, _subs = loops.bind_frozen(compiler, flags=0x0)
        rng = random.Random(20261107)
        worst = 0.0
        for _ in range(120):
            inp = {'attribute2.%d' % i: rng.uniform(-1, 1) for i in range(3)}
            inp.update({'attribute3.%d' % i: rng.uniform(-1, 1) for i in range(4)})
            inp.update({'attribute4.%d' % i: rng.uniform(-3, 3) for i in range(3)})
            inp.update({'attribute4.3': rng.uniform(0, 1), 'attribute0.3': rng.uniform(0, 1.2), 'ps.POS_W': rng.uniform(0.05, 6.0),
                        'ps.FRONT_FACE': rng.choice((0.0, 1.0)), 'CB2[13]': rng.uniform(0.05, 3.0)})
            inp.update({'CB2[%d]' % k: rng.uniform(0, 2) for k in range(15) if k != 13})
            inp.update({'CB0[%d]' % k: rng.uniform(0, 1) for k in (112, 113, 114)})
            samples = {(0, 0): rng.uniform(0, 12)}
            samples.update({(2, ch): rng.uniform(0, 3) for ch in range(3)})
            samples.update({(8, ch): rng.uniform(0, 1) for ch in range(4)})
            samples.update({(9, ch): rng.uniform(0, 1) for ch in range(2)})
            light = {reg: rng.uniform(0, 3) for reg in (14, 15, 16, 20, 21, 22)}
            colour, alpha = case_closed_form(inp, samples, light)
            for k in range(3):
                got = loops.evaluate(compiler.ir, roots[k], inp, samples, light)
                worst = max(worst, abs(got - colour[k]) / max(1.0, abs(colour[k])))
            worst = max(worst, abs(loops.evaluate(compiler.ir, roots[3], inp, samples, light) - alpha))
        self.assertLess(worst, 5e-5)

    def test_viewer_readings_from_the_archive(self):
        paths = tp.material_texture_paths(self.archive, CASE_MATERIAL)
        forward = paths['forward']
        p = forward['params']
        self.assertEqual((forward['kind'], forward['writer'], forward['graph_sha256'], forward['blend'], forward['two_sided']),
                         ('brain_case', 'AE2B0B81', CASE_SHA, 'alpha_over', False))
        self.assertEqual(tuple(paths[role] for role in ('_window_mask', '_window_n1', '_window_n2', '_window_c')), CASE_TEXTURES)
        self.assertTrue(all(role not in paths for role in ('_c', '_g', '_n', '_pick')))
        # The texture loader must load the four window roles (the viewer reads them on the window units).
        entry = tp.material_textures(self.archive, [dict(index=0, path=CASE_MATERIAL)], {}, previews=False)[0]
        self.assertTrue(all(entry.get(role) for role in ('_window_mask', '_window_n1', '_window_n2', '_window_c')))
        expected =dict(body=(1.0, 0.381326, 0.590619), tint_grazing=(0.036889, 0.0, 0.03434), tint_facing=(1.0, 0.097587, 0.043735))
        for name, value in expected.items():
            for got, want in zip(p[name], value):
                self.assertAlmostEqual(got, want, places=5)
        self.assertEqual((p['gloss_open'], p['gloss_painted'], p['f0_open'], p['f0_painted'], p['tint_scale'], p['alpha_scale']),
                         (4.0, 10.0, 5.0, 1.0, 5.0, 1.0))
        self.assertAlmostEqual(p['tint_exp'], 0.2, places=6)
        brain = tp.material_texture_paths(self.archive, BRAIN_MATERIAL)
        self.assertEqual((brain['_c'], brain['g_mode'], brain['spec'], brain['gloss'], brain['k']),
                         ('textures/character/enemy/enm_telepathipus/telepathipus_leg_brain_c.texture', 'grey', 1.0, 2.0, 0.0))
        inside = tp.material_texture_paths(self.archive, INSIDE_MATERIAL)['flat_surface']
        self.assertEqual((inside['model'], inside['cull_back']), ('basic_flat_color', True))


if __name__ == '__main__':
    unittest.main()
