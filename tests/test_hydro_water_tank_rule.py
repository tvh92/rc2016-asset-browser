"""Hydro harvester tank glass forward program 7EB73491 (docs/material-decoding/hydro-water-tank-glass.md).

Synthetic checks (rule binding, uniform packing, GLSL text, validator constants) always run. The archive-backed
checks pin the program identities, run the translator's loop-opaque research mode on 7EB73491 and compare the
material-only terms numerically with the closed forms restated below, independently of the GLSL; they skip
without RC2016_RESEARCH / RC2016_GAME_ROOT.
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

GRAPH = 'materialgraph/global/water_tank/water_tank.materialgraph'
MATERIAL = 'material/global/water_tank/hydro_water_tank.material'
OTHER_MATERIAL = 'material/global/water_tank/water_tank.material'
GRAPH_SHA = '544803a389a463926bcbe745dbedda45596dba9004a4fbbf9b814f854ca44ce9'
CODE_SHA = {'0x7eb73491': 'bd55164ca9357107496042e331df6ad6fcfccdb95cfc6dab2b6de1fd27eac0d7',
            '0xc9e3608c': '8fc5e396f8febf456a05b05f9939192dd2fcd86ba85fc637678b362cdafbb3e5'}
CMA = 'textures/environment/global/prop/gbl_prop_water_tank/gbl_prop_water_tank_cma.texture'
FLOW = 'textures/environment/metropolis/ambient/met_amb_water_pool_flow_01_n.tga/met_amb_water_pool_flow_01_n.texture'
# CB2 dwords 0..9 bound at all 13 tank draws of the 2026-10-31 cutscene capture (CB2[3] is the graph default).
CAPTURED_CB2 = (0.03689, 0.22323, 0.34191, 0.25, 1.0, 1.0, 1.0, 0.05, 0.5, 5.0)


def sat(x):
    return max(0.0, min(1.0, x))


def alpha_closed_form(inp, s):
    """alpha = sat(attr0.w sat(1 - (1 - a)(1 - S5.R)(1 - S6.R))), a = sat(CB2[3] + rim), rim from the geometry normal."""
    def unit(v):
        length = math.sqrt(sum(x * x for x in v))
        return [x / length for x in v]
    n = unit([inp['attribute2.%d' % k] for k in range(3)])
    p = unit([inp['attribute4.%d' % k] for k in range(3)])
    front = 1.0 if inp['ps.FRONT_FACE'] else -1.0
    rim = sat(4.0 * abs(1.0 - sat(front * sum(p[i] * n[i] for i in range(3)))) ** inp['CB2[9]'])
    a = sat(inp['CB2[3]'] + rim)
    return rim, sat(inp['attribute0.3'] * sat(1.0 - (1.0 - a) * (1.0 - s[(5, 0)]) * (1.0 - s[(6, 0)])))


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hash_binds_the_rule(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('glass_tank', '7EB73491', GRAPH_SHA))
        self.assertEqual(rule['slots'], {'_n': 0x49A7866B7AE34E7A, '_pick': 0x49A7866B27C84BA9})
        self.assertEqual(tp.HYDRO_WATER_TANK_CMA_SLOTS, (0x49A7866B27C84BA9, 0x49A7866B30B35FEA))
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})
        self.assertEqual(tp._graph_rule([GRAPH.replace('.materialgraph', '_copy.materialgraph')], {}), {})

    def test_kind_and_uniform_packing(self):
        self.assertEqual(GLASS_KINDS['glass_tank'], 15)
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))
        params = dict(tint=(0.1, 0.2, 0.3), body=(0.4, 0.5, 0.6), rim_bias=0.7, gloss=0.8, f0_base=0.9, rim_exp=1.1, alpha_scale=1.0)
        kind, a, b, c, d, e = glass_uniforms(dict(kind='glass_tank', params=params))
        self.assertEqual((kind, a, b, c, d, e), (15, (0.1, 0.2, 0.3, 0.7), (0.4, 0.5, 0.6, 0.8), (0.9, 1.1, 0.0, 0.0),
                                                  (1.0, 0.0, 0.0, 0.0), (0.0,) * 4))

    def test_glsl_branch_carries_the_decoded_equations(self):
        branch = GLASS_FRAGMENT.split('uKind == 15')[1].split('uKind == 14')[0]
        for text in ('float rim = sat(4.0 * pow(om, uC.y))', 'vec2(0.1 * g.x, 0.11666667 * g.y)', 'texture2D(uTexN, g / 12.0)',
                     'vec2 xy = 2.0 * (fp - fq)', 'abs(xy.x)', 'abs(xy.y)', 'N * (1.0 - dot(xy, xy))',
                     'float a0 = sat(uA.w + rim)', 'texture2D(uTexP, vUV).r', 'texture2D(uTexP, 1.5 * vUV).r',
                     'alpha = sat(uD.x * sat(1.0 - (1.0 - a0) * (1.0 - s5) * (1.0 - s6)))',
                     'vec3 F0 = clamp(uA.rgb / luma, 0.0, 1.0) * (uC.x + pow(rim, 10.0))',
                     'pow(2.0, -5.5 * sqrt(sat(uB.w)))', 'color = uB.rgb * Ld + glassSpec(N, V, L1, L2, C1, C2, F0, a, ambient)'):
            self.assertIn(text, branch)

    def test_captured_values_are_recorded(self):
        c = tp.HYDRO_WATER_TANK_CONSTANTS
        self.assertEqual((c['rim_bias'], c['alpha_scale'], c['game_equivalence'], c['writer']), (CAPTURED_CB2[3], 1.0, False, '7EB73491'))
        self.assertEqual(c['frozen_inputs']['instance_flags'], 0x261)
        self.assertEqual(tp.HYDRO_WATER_TANK_CAPTURED_BLEND, 'alpha_over')
        self.assertEqual(tp.HYDRO_WATER_TANK_MATERIAL, MATERIAL)

    def test_blender_front_end_and_validator_agree(self):
        self.assertEqual(validation.GLASS_CONSTANTS['glass_tank'],
                         ('tint', 'rim bias', 'body', 'gloss', 'f0 base', 'rim exp', 'alpha scale'))
        params = dict(tint=[0.1, 0.2, 0.3], body=[0.4, 0.5, 0.6], rim_bias=0.7, gloss=0.8, f0_base=0.9, rim_exp=1.1, alpha_scale=1.0)
        for blend, with_alpha in (('opaque', False), ('alpha_over', True)):
            rule = dict(applies=True, ported=['glass_tank'], terms={'glass_tank': dict(kind='glass_tank', params=params, blend=blend)},
                        textures={'_c': None, '_g': None, '_n': FLOW, '_pick': CMA})
            names, roles = validation._viewer_required(rule, False)
            expected = validation._viewer_expected_constants(rule)
            self.assertEqual(set(names) <= set(expected), True)
            self.assertEqual('VR glass rim bias' in names, with_alpha)
            self.assertEqual(roles, ['_n', '_pick'])
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_glass')[1].split('def apply_viewer_graph_rule')[0]
        for name in ('glass tint', 'glass rim bias', 'glass body', 'glass gloss', 'glass f0 base', 'glass rim exp', 'glass alpha scale',
                     'glass tank flow map P', 'glass tank flow map Q', 'glass tank cma S5', 'glass tank cma S6', 'VR glass lit'):
            self.assertIn(name, body)
        self.assertIn("'glass_tank'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])


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
        cls.programs = {p['shader_id']: p for p in translator.pixel_programs(cls.graph)}

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_identities(self):
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), GRAPH_SHA)
        for shader_id, digest in CODE_SHA.items():
            self.assertEqual(self.programs[shader_id]['code_sha256'], digest)

    def test_material_only_terms_match_the_program_numerically(self):
        from rc2016.research import forward_loops as loops
        compiler = loops.compile_opaque(self.programs['0x7eb73491'])
        roots, subs = loops.bind_frozen(compiler, flags=0x261, pass_word=0x2)
        head = loops.loop_head(compiler, subs)
        ir = compiler.ir
        rng = random.Random(20261031)
        worst = {'alpha': 0.0, 'rim': 0.0, 'gloss alpha': 0.0}
        for _ in range(60):
            inp = {'CB2[%d]' % k: rng.uniform(0.05, 1.0) for k in range(10)}
            inp.update({'CB2[3]': rng.uniform(0.0, 0.4), 'CB2[8]': rng.uniform(0.1, 1.5), 'CB2[9]': rng.uniform(1.0, 6.0),
                        'attribute0.3': rng.uniform(0.3, 1.0), 'ps.FRONT_FACE': float(rng.choice([0, 1]))})
            inp.update({'attribute2.%d' % k: rng.uniform(-1, 1) for k in range(3)})
            inp.update({'attribute4.%d' % k: inp['attribute2.%d' % k] + rng.uniform(-0.7, 0.7) for k in range(3)})
            samples = {(api, ch): rng.uniform(0, 1) for api in (5, 6, 7) for ch in range(4)}
            rim, alpha = alpha_closed_form(inp, samples)
            worst['alpha'] = max(worst['alpha'], abs(loops.evaluate(ir, roots[3], inp, samples) - alpha))
            worst['rim'] = max(worst['rim'], abs(loops.evaluate(ir, head[0], inp, samples) - rim))
            worst['gloss alpha'] = max(worst['gloss alpha'], abs(loops.evaluate(ir, head[73], inp, samples)
                                                                  - 2.0 ** (-5.5 * math.sqrt(sat(inp['CB2[8]'])))))
        for key, error in worst.items():
            self.assertLess(error, 5e-6, key)

    def test_viewer_rule_from_the_archive(self):
        from rc2016.levels.scene import asset_bytes
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        forward = paths['forward']
        p = forward['params']
        self.assertEqual(material_flags(data=asset_bytes(self.archive, MATERIAL)), 0x38)
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['two_sided'], forward['blend_source']),
                         ('glass_tank', '7EB73491', 'alpha_over', False, 'captured draw state'))
        self.assertEqual((paths['_n'], paths['_pick']), (FLOW, CMA))
        self.assertEqual([round(x, 5) for x in p['body']], list(CAPTURED_CB2[:3]))
        self.assertEqual([round(x, 5) for x in p['tint']], list(CAPTURED_CB2[4:7]))
        self.assertEqual((p['rim_bias'], round(p['f0_base'], 5), round(p['gloss'], 5), p['rim_exp']),
                         (CAPTURED_CB2[3], CAPTURED_CB2[7], CAPTURED_CB2[8], CAPTURED_CB2[9]))
        self.assertEqual((p['alpha_scale'], p['game_equivalence']), (1.0, False))
        # evidence covers this material only, and S5 and S6 must read the same image
        self.assertNotIn('forward', tp.material_texture_paths(self.archive, OTHER_MATERIAL))
        with patch.object(tp, 'texture_slots', return_value={0x49A7866B27C84BA9: 'textures/other.texture'}):
            self.assertNotIn('forward', tp.material_texture_paths(self.archive, MATERIAL))


if __name__ == '__main__':
    unittest.main()
