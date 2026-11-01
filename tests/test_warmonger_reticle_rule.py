"""Warmonger / Peacemaker sight forward program 4C184DCC (docs/material-decoding/warmonger-reticle.md).

Synthetic checks (rule binding, uniform packing, GLSL text, validator constants) always run. The archive-backed checks pin the
program identity, run the translator's loop-opaque research mode on 4C184DCC and compare the alpha export numerically with the
closed form restated below, independently of the GLSL; they skip without RC2016_RESEARCH / RC2016_GAME_ROOT.
"""
import hashlib
import math
import os
import random
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2016.pipeline.render_state import material_flags
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms

GRAPH = 'materialgraph/equipment/weapon/wpn_warmonger_reticle/wpn_warmonger_reticle.materialgraph'
MATERIAL = 'material/equipment/weapon/wpn_Warmonger_Reticle/wpn_Warmonger_Reticle.material'
GRAPH_SHA = '78182cb38b81b861b1117bb9c4f36bbb0dc8f57e1d4593bae6a25e633d935ced'
CODE_SHA = '0d0ed5267df8da8f46775444286ce2921c1741bfe4fe9e373bbe089ec0c470d1'
CMA = 'textures/equipment/weapon/wpn_warmonger/wpn_warmonger_reticle_cma.texture'
CMB = 'textures/equipment/weapon/wpn_warmonger/wpn_warmonger_reticle_cmb.texture'
# CB2 dwords 0..6 bound at the sight draws of the 2026-11-01 Peacemaker capture (dword 5, the gloss, is the graph default).
CAPTURED_CB2 = (0.36625, 0.13843, 0.03434, 0.1, 0.272, 1.0, 0.1)


def sat(x):
    return max(0.0, min(1.0, x))


def alpha_closed_form(inp, s):
    """alpha = sat(attr0.w sat(E)), E = sat(0.874 + fres) (1 - 0.6436 (1 - S7.r S5.a)(1 - S5.r)(1 - S5.b S6.g))
    (1 - (1 - S10.g)(1 - S8.b)(1 - S9.b)), fres = sat(4 (1 - N.V)^CB2[3]) from the geometry normal (attribute2) and the
    view vector (attribute3)."""
    n = [inp['attribute2.%d' % k] for k in range(3)]
    v = [inp['attribute3.%d' % k] for k in range(3)]
    face = 1.0 if inp['ps.FRONT_FACE'] else -1.0
    ndv = sat(face * sum(a * b for a, b in zip(n, v)) / math.sqrt(sum(x * x for x in n) * sum(x * x for x in v)))
    fres = sat(4.0 * abs(1.0 - ndv) ** inp['CB2[3]'])
    art = 1.0 - (1.0 - s[(5, 2)] * s[(6, 1)]) * (1.0 - s[(5, 0)])
    e = sat(0.874 + fres) * (1.0 - 0.64359987 * (1.0 - s[(7, 0)] * s[(5, 3)]) * (1.0 - art)) * \
        (1.0 - (1.0 - s[(10, 1)]) * (1.0 - s[(8, 2)]) * (1.0 - s[(9, 2)]))
    return sat(inp['attribute0.3'] * sat(e))


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hash_binds_the_rule(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('warmonger_reticle', '4C184DCC', GRAPH_SHA))
        self.assertEqual(rule['slots'], {'_pick': 0x49A7866B0056CBD0, '_n': 0x49A7866B2EA0E356})
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})

    def test_kind_and_uniform_packing(self):
        self.assertEqual(GLASS_KINDS['warmonger_reticle'], 16)
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))
        params = dict(colour=(0.1, 0.2, 0.3), fresnel_exp=0.4, f0_base=0.5, gloss=0.6, frame_rate=0.7, time=0.0,
                      alpha_cut=0.8, alpha_scale=0.9)
        forward = dict(kind='warmonger_reticle', params=params)
        self.assertEqual(glass_uniforms(forward), (16, (0.1, 0.2, 0.3, 0.4), (0.5, 0.6, 0.7, 0.0), (0.8, 0.9, 0.0, 0.0),
                                                   (0.0,) * 4, (0.0,) * 4))
        self.assertEqual(glass_uniforms(forward, 2.5)[2], (0.5, 0.6, 0.7, 2.5))   # the effect clock replaces CB0[99]

    def test_glsl_branch_carries_the_decoded_equations(self):
        branch = GLASS_FRAGMENT.split('uKind == 16')[1].split('uKind == 14')[0]
        for text in ('float th = -0.25 * t + 0.41176471 * cos(3.0 * t)', 'vec4 s5 = uHasP ? texture2D(uTexP, vUV)',
                     'texture2D(uTexP, ringUV).g', 'float f = floor(-8.0 * fract(uB.z * t))',
                     'vec2(0.625 * (vUV.x - f) - 0.465, 5.0 * (vUV.y - f))).r', 'vec2(0.125 * vUV.x, vUV.y + 0.285 * t)).b',
                     'vec2(0.125 * vUV.x, vUV.y + 0.508 * t)).b', 'vec2(0.125 * vUV.x, vUV.y)).g',
                     'float fres = sat(4.0 * pow(om, uA.w))', 'float art = 1.0 - (1.0 - s5.b * s6) * (1.0 - s5.r)',
                     'sat(0.874 + fres) * (1.0 - 0.64359987 * (1.0 - s7 * s5.a) * (1.0 - art))',
                     '(1.0 - (1.0 - s10) * (1.0 - s8) * (1.0 - s9))', 'if (sat(E) < uC.x) discard',
                     'alpha = sat(uC.y * sat(E))', 'float F = uB.x + pow(fres, 10.0)', 'vec3 F0 = vec3(F), F90 = vec3(sat(60.0 * F))',
                     'pow(2.0, -5.5 * sqrt(sat(uB.y)))', 'color = vec3(1.0, 0.47353148, 0.0) * (Ld + E) + spec'):
            self.assertIn(text, branch)

    def test_blender_front_end_and_validator_agree(self):
        self.assertEqual(validation.GLASS_CONSTANTS['warmonger_reticle'],
                         ('colour', 'fresnel exp', 'f0 base', 'gloss', 'frame rate', 'time', 'alpha cut', 'alpha scale'))
        params = dict(colour=[0.1, 0.2, 0.3], fresnel_exp=0.4, f0_base=0.5, gloss=0.6, frame_rate=0.7, time=0.0,
                      alpha_cut=0.8, alpha_scale=0.9)
        rule = dict(applies=True, ported=['warmonger_reticle'],
                    terms={'warmonger_reticle': dict(kind='warmonger_reticle', params=params, blend='alpha_over', two_sided=True)},
                    textures={'_c': None, '_g': None, '_n': CMB, '_pick': CMA})
        names, roles = validation._viewer_required(rule, False)
        expected = validation._viewer_expected_constants(rule)
        self.assertTrue(set(names) <= set(expected))
        self.assertEqual(roles, ['_n', '_pick'])
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split("elif key == 'warmonger_reticle':")[1].split("elif key == 'window_glass':")[0]
        for name in ('glass time', 'glass fresnel exp', 'glass frame rate', 'glass alpha cut', 'glass alpha scale', 'glass f0 base',
                     'glass colour', 'glass gloss', 'glass reticle cma S5', 'glass reticle cma S6 ring', 'glass reticle cmb S7 flipbook',
                     'glass reticle cmb S8 scanline', 'glass reticle cmb S9 scanline', 'glass reticle cmb S10 mask', 'VR glass lit'):
            self.assertIn(name, body)
        self.assertIn("'warmonger_reticle'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])


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
        cls.graph = asset_bytes(cls.archive, GRAPH)
        cls.program = next(p for p in translator.pixel_programs(cls.graph) if p['shader_id'] == '0x4c184dcc')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_identities(self):
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), GRAPH_SHA)
        self.assertEqual(self.program['code_sha256'], CODE_SHA)

    def test_alpha_matches_the_program_numerically(self):
        from rc2016.research import forward_loops as loops
        compiler = loops.compile_opaque(self.program)
        rng = random.Random(20261101)
        for t in (0.0, 4.2):   # the alpha export does not depend on CB0[99] (only the texture coordinates do)
            roots, _subs = loops.bind_frozen(compiler, flags=0x261, time=t, pass_word=0x0)
            worst = 0.0
            for _ in range(60):
                inp = {'CB2[%d]' % k: rng.uniform(0.05, 1.0) for k in range(7)}
                inp.update({'attribute%d.%d' % (a, c): rng.uniform(-1, 1) for a in range(4) for c in range(4)})
                inp.update({'attribute0.2': 0.0, 'attribute0.3': rng.uniform(0.3, 1.0), 'ps.FRONT_FACE': float(rng.choice([0, 1]))})
                samples = {(api, ch): rng.uniform(0, 1) for api in range(5, 12) for ch in range(4)}
                worst = max(worst, abs(loops.evaluate(compiler.ir, roots[3], inp, samples) - alpha_closed_form(inp, samples)))
            self.assertLess(worst, 5e-6)

    def test_viewer_rule_from_the_archive(self):
        from rc2016.levels.scene import asset_bytes
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        forward = paths['forward']
        p = forward['params']
        self.assertEqual(material_flags(data=asset_bytes(self.archive, MATERIAL)), 0x200236)
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['two_sided']),
                         ('warmonger_reticle', '4C184DCC', 'alpha_over', True))   # as the captured draw state
        self.assertEqual((paths['_pick'], paths['_n']), (CMA, CMB))
        self.assertNotIn('_c', paths)
        self.assertEqual([round(x, 5) for x in p['colour']], list(CAPTURED_CB2[:3]))
        self.assertEqual([round(p[k], 5) for k in ('fresnel_exp', 'f0_base', 'gloss', 'frame_rate')], list(CAPTURED_CB2[3:]))
        self.assertAlmostEqual(p['alpha_cut'], 4.0 / 255.0, places=7)   # attribute0.z in the capture
        self.assertEqual((p['alpha_scale'], p['time'], p['game_equivalence']), (1.0, 0.0, False))
        # one image per slot group, or the rule does not bind
        with patch.object(tp, 'texture_slots', side_effect=lambda data, tag: {0x49A7866B39DBF715: CMB} if tag == tp.MATERIAL_SLOT_SECTION else {}):
            self.assertNotIn('forward', tp.material_texture_paths(self.archive, MATERIAL))


if __name__ == '__main__':
    unittest.main()
