"""Decoded pok_uniq_water closure (F4D47ECE; docs/material-decoding/novalis-water.md).

The synthetic tests pin the closed forms in rc2016.shaders.water, the viewer rule binding, the uniform packing, the GLSL
branch and the Blender/validator agreement. The archive tests (skipped without RC2016_RESEARCH / RC2016_GAME_ROOT) bind
the material through the pass-0 table and compare the decoded alpha with the loop-opaque translation of the program.
"""
import math
import os
import random
import unittest
from pathlib import Path

from rc2016.shaders import validation
from rc2016.shaders import water as W
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import (GLASS_FRAGMENT, GLASS_UNIFORMS, SCENE_DEPTH_TEXTURE_UNIT, WATER_FOAM_TEXTURE_UNITS,
                                   glass_uniforms, water_surface_uniforms)

MATERIAL = 'material/visualeffect/environment/pokitaru/unique/pok_uniq_water/pok_uniq_water.material'
CODE_SHA = '89b494be6b573eb0f5e7ed2af9385498eb8eff08c1fc017edb07007930d72d20'   # F4D47ECE, also the sampling contract key
# pok_uniq_water.material through the pass-0 table; these equal the CB2 words bound by the captured river draw.
DECODED = dict(shallow_colour=[0.0, 1.0, 0.8227857351303101], body=[0.0, 0.177888423204422, 0.2158605009317398],
               depth_fade=4.0, grazing_power=600.0, near_fade_start=0.375, near_fade_end=0.5, displacement_b=0.025,
               strength_distance=144.0, crest_scale=2.0, strength_a=0.25, strength_b=0.5, foam_height_a=0.333,
               foam_height_b=1.0, reflection_distance=144.0, foam_scale_u=2.625, foam_scale_v=2.375,
               foam_displacement_a=0.017, foam_displacement_b=0.025, foam_scroll_au=0.031, foam_scroll_av=0.021,
               foam_scroll_bu=-0.021, foam_scroll_bv=-0.031, foam_power=6.0, thickness_power=3.0,
               thickness_scale=0.075, displacement_a=0.025, crest_colour=0.25, environment_scale=0.2,
               environment_luminance=0.0, specular_weight=0.5, foam_scale=1.0, far_reflection=0.75, far_strength=0.5)


def surface(**changes):
    return dict(DECODED, **W.WATER_SURFACE_PRESENTATION, **changes)


class ClosedFormTests(unittest.TestCase):
    def test_key_table_is_one_to_one(self):
        keys = [row[1] for row in W.WATER_SURFACE_KEYS]
        dwords = [d + i for _name, _key, d, n in W.WATER_SURFACE_KEYS for i in range(n)]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(dwords), len(set(dwords)))
        self.assertEqual({row[0] for row in W.WATER_SURFACE_KEYS}, set(DECODED))
        rows = {name: (key, dword) for name, key, dword, _n in W.WATER_SURFACE_KEYS}
        self.assertEqual(rows['body'], (0x1DAF77B8, 4))
        self.assertEqual(rows['displacement_a'], (0x230C6389, 36))
        self.assertEqual(rows['environment_scale'], (0xE93F97D5, 40))

    def test_crest_strengths_alpha(self):
        p = surface()
        self.assertAlmostEqual(W.water_crest(0.4, 0.5, p), 0.6)
        q1, q2 = W.water_normal_strengths(0.6, 72.0, p)          # half way to the strength distance
        self.assertAlmostEqual(q1, 0.6 * 0.1875)
        self.assertAlmostEqual(q2, 0.6 * 0.375)
        cover, alpha = W.water_surface_alpha(0.4375, 0.125, 1.0, p, 0.4)   # near fade 0.5, depth fade 0.5, no grazing term
        self.assertAlmostEqual(cover, 0.25, places=6)
        self.assertAlmostEqual(alpha, 0.1, places=6)
        self.assertEqual(W.water_surface_alpha(10.0, 5.0, 0.0, p, 0.4), (0.0, 0.0))   # grazing: (1.001)^600 saturates
        self.assertAlmostEqual(W.water_surface_alpha(10.0, 5.0, 0.5, p, 0.4)[1], 0.4, places=6)

    def test_foam_is_shore_foam(self):
        p = surface()
        self.assertAlmostEqual(W.water_foam(0.8, 0.9, 1.2, 1.5, 0.0, p), 0.72)
        self.assertEqual(W.water_foam(0.8, 0.9, 1.2, 1.5, 3.0, p), 0.0)              # 0.075 * 27 > 1: deep water has none
        shallow = W.water_foam(0.8, 0.9, 1.2, 1.5, 1.0, p)
        self.assertAlmostEqual(shallow, 0.72 * (1.0 - 0.075))
        g = 0.333 * 0.6 + 1.0 * 0.2                                                   # summed heights below 1: power 6
        self.assertAlmostEqual(W.water_foam(1.0, 1.0, 0.6, 0.2, 0.0, p), g ** 6)

    def test_reflection(self):
        p = surface()
        near = W.water_reflection(0.0, (1.0, 1.0, 1.0), 0.0, p)
        self.assertEqual([round(v, 6) for v in near], [0.2, 0.45, round(0.25 * 0.8227857351303101 + 0.2, 6)])
        far = W.water_reflection(0.0, (1.0, 1.0, 1.0), 300.0, p)
        self.assertEqual([round(v, 6) for v in far], [round(0.75 * v, 6) for v in near])
        crest = W.water_reflection(1.0, (0.0, 0.0, 0.0), 0.0, p)                      # full crest: the scatter term vanishes
        self.assertEqual(crest, [0.0, 0.0, 0.0])
        self.assertEqual(W.water_reflection(0.5, (10.0, 10.0, 10.0), 300.0, p), [0.75, 0.75, 0.75])   # saturated, then the far factor

    def test_secondary_and_foam_uvs(self):
        uv = W.water_secondary_uvs(100.0, -40.0, 1.2, 0.025, time=2.0)
        c = 0.025 * 0.2
        self.assertAlmostEqual(uv['crest_5'][0], 0.116 + 0.2625 + 0.0198)
        self.assertAlmostEqual(uv['crest_6'][1], 0.133 - 0.105 - 0.016)
        self.assertAlmostEqual(uv['pair_b_8'][0], 4.75 + c + 0.104)
        self.assertAlmostEqual(uv['pair_b_7'][1], 0.346 - 1.9 + c - 0.102)
        foam = W.water_foam_uvs(100.0, -40.0, 1.2, surface(), time=2.0)
        self.assertAlmostEqual(foam[9][0], 0.116 + 2.625 * 5.0 + 0.017 * 0.2 + 0.062)
        self.assertAlmostEqual(foam[10][1], 0.133 + 2.625 * -2.0 + 0.025 * 0.2 - 0.062)


class ViewerRuleTests(unittest.TestCase):
    def test_exact_graph_binds_the_decoded_surface(self):
        rule = tp._graph_rule([W.WATER_GRAPH], {W.WATER_GRAPH: W.WATER_GRAPH_SHA256})
        self.assertTrue(rule.get('water_surface') and rule.get('water_sampling'))
        glow = tp._graph_rule([W.GLOW_GRAPH], {W.GLOW_GRAPH: W.GLOW_GRAPH_SHA256})
        self.assertTrue(glow.get('water_sampling'))
        self.assertFalse(glow.get('water_surface'))
        fallback = tp._graph_rule([W.WATER_GRAPH], {W.WATER_GRAPH: '0' * 64})   # family fallback only
        self.assertEqual(fallback.get('forward'), 'water')
        self.assertFalse(fallback.get('water_sampling') or fallback.get('water_surface'))

    def test_uniform_packing(self):
        s = surface()
        forward = dict(kind='water', params=dict(tp.WATER_PRESENTATION, body=s['body']), sampling=dict(displacement=0.025),
                       surface=s)
        kind, a, b, c, d, e = glass_uniforms(forward)
        self.assertEqual(kind, 4)
        self.assertEqual(a, (*s['shallow_colour'], 0.4))
        self.assertEqual(b, (*s['body'], 0.25))
        self.assertEqual(c, (0.025, 0.025, 2.0, 0.0))
        self.assertEqual(d, (0.25, 0.5, 0.5, 144.0))
        self.assertEqual(e, (0.025, 1.0, 1.0, 0.0))
        w = water_surface_uniforms(forward)
        self.assertEqual(w[0], (4.0, 600.0, 0.375, 0.5))
        self.assertEqual(w[1], (0.2, 0.0, 0.75, 144.0))
        self.assertEqual(w[2], (1.0, 6.0, 0.075, 3.0))
        self.assertEqual(w[3], (0.333, 1.0, 2.625, 2.375))
        self.assertEqual(w[4], (0.017, 0.025, 0.0, 0.0))
        self.assertEqual(w[5], (0.031, 0.021, -0.021, -0.031))
        partial = dict(forward); partial.pop('surface')
        self.assertEqual(glass_uniforms(partial)[5][2], 0.0)                          # the partial path keeps uE.z = 0
        self.assertEqual(water_surface_uniforms(partial), [(0.0,) * 4] * 6)
        self.assertEqual(water_surface_uniforms(dict(kind='screen')), [(0.0,) * 4] * 6)

    def test_glsl_branch_carries_the_decoded_equations(self):
        branch = GLASS_FRAGMENT.split('uKind == 4 && uE.z > 0.5')[1].split('} else if (uKind == 4) {')[0]
        for text in ('float crest = sat(uC.z * (1.0 - c1) * (1.0 - c2))',
                     'vec2(0.116 + 0.002625 * w.x + 0.0099 * t, 0.002375 * w.y + 0.011 * t)',
                     'vec2 b8 = vec2(0.0475 * w.x + 0.052 * t, 0.0525 * w.y + 0.048 * t) + uC.y * hh',
                     'vec3 Nw = normalize(normalize(pa) + normalize(pb))',
                     'float Fg = sat(pow(1.001 - ndv, uW[0].y))', 'alpha = sat(uA.w * cover)',
                     'vec3 env = uW[1].x * waterEnvironment(reflect(-V, Nw))',
                     'color = clamp(pre, 0.0, 1.0) * (1.0 + sat(d / uW[1].w) * (uW[1].z - 1.0))',
                     'color += sat(uW[2].x * m * (1.0 - sat(uW[2].z * pow(max(thick, 0.0), uW[2].w))))'):
            self.assertIn(text, branch)
        self.assertIn('return normalize(N * (1.0 - dot(s, s)) + B * s.y + T * s.x)', GLASS_FRAGMENT)
        for name in ('uW', 'uWaterFoamA', 'uWaterFoamB', 'uHasWaterFoam', 'uSceneDepth', 'uHasSceneDepth', 'uSceneDepthInfo'):
            self.assertIn(name, GLASS_UNIFORMS)
        self.assertEqual(len({*WATER_FOAM_TEXTURE_UNITS, SCENE_DEPTH_TEXTURE_UNIT, 0, 1, 2, 4, 6, 18}), 9)

    def test_viewer_uploads_the_foam_tiles(self):
        from rc2016.viewer.viewport import GL_TEXTURE_ROLES, MIPMAP_ROLES
        for role in ('_water_foam_a', '_water_foam_b'):
            self.assertIn(role, GL_TEXTURE_ROLES)
            self.assertIn(role, MIPMAP_ROLES)

    def test_blender_front_end_and_validator_agree(self):
        s = surface()
        rule = dict(applies=True, ported=['water'], textures={'_water_height_a': 'a', '_water_height_b': 'b', '_n': 'n', '_pick': 'p'},
                    terms={'water': dict(kind='water', params=dict(tp.WATER_PRESENTATION, body=s['body']),
                                         sampling=dict(displacement=0.025), surface=s, blend='alpha_over')})
        names, roles = validation._viewer_required(rule, False)
        expected = validation._viewer_expected_constants(rule)
        self.assertTrue(set(names) <= set(expected))
        self.assertEqual(roles, ['_water_height_a', '_water_height_b', '_n', '_pick'])
        self.assertEqual(expected['VR water decoded shallow colour'], s['shallow_colour'])
        self.assertEqual(expected['VR water presentation opacity'], 0.4)
        self.assertNotIn('VR water presentation F0', names)
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_water_surface')[1].split('\ndef ')[0]
        for label, _key in validation.WATER_SURFACE_CONSTANTS:
            self.assertIn("'water decoded %s'" % label, body)
        for name in ('VR water API7 second normal sample', 'VR water API8 second normal sample', 'VR water API5 crest sample',
                     'VR water API6 crest sample', 'VR water decoded environment reflection', 'VR water decoded alpha-over'):
            self.assertIn(name[3:] if name.startswith('VR ') else name, body)


def _archive():
    root = os.environ.get('RC2016_GAME_ROOT') or os.environ.get('RC2016_RESEARCH')
    if not root or not Path(root).exists():
        raise unittest.SkipTest('RC2016_RESEARCH is required')
    from rc2016.levels.scene import open_archive
    return open_archive(root)


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.levels.scene import asset_bytes
        cls.archive = _archive()
        cls.graph = asset_bytes(cls.archive, W.WATER_GRAPH)
        cls.material = asset_bytes(cls.archive, MATERIAL)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_material_binds_through_pass_zero(self):
        values = W.water_surface_parameters(self.graph, self.material)
        for name, value in DECODED.items():
            if isinstance(value, list):
                for got, want in zip(values[name], value):
                    self.assertAlmostEqual(got, want, places=6, msg=name)
            else:
                self.assertAlmostEqual(values[name], value, places=6, msg=name)
        rule = W.water_surface_rule(self.graph, self.material)
        self.assertEqual(rule['textures']['_water_foam_a'], 'textures/visualeffect/water/water_beachfoam_tile_01.texture')
        self.assertEqual(rule['textures']['_water_foam_b'], 'textures/visualeffect/water/water_beachfoam_tile_02.texture')
        self.assertEqual(rule['surface']['opacity'], 0.4)
        with self.assertRaisesRegex(ValueError, 'bound to pok_uniq_water only'):
            W.water_surface_parameters(self.graph + b'x', self.material)

    def test_viewer_material_carries_the_surface(self):
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        forward = paths['forward']
        self.assertEqual((forward['kind'], forward['blend'], forward['two_sided']), ('water', 'alpha_over', False))
        self.assertEqual(forward['surface']['status'], 'source_decoded_surface_partial')
        self.assertEqual(paths['_water_foam_a'], 'textures/visualeffect/water/water_beachfoam_tile_01.texture')

    def test_alpha_matches_the_loop_opaque_program(self):
        from rc2016.research import forward_loops
        from rc2016.shaders import translator
        program = next(p for p in translator.pixel_programs(self.graph) if p['shader_id'] == '0xf4d47ece')
        self.assertEqual(program['code_sha256'], CODE_SHA)
        compiler = forward_loops.compile_opaque(program)
        roots, _subs = forward_loops.bind_frozen(compiler, flags=0x2001)
        alpha = roots[3]
        self.assertEqual(forward_loops.dependencies(compiler.ir, alpha),
                         {'CB2[8]', 'CB2[9]', 'CB2[10]', 'CB2[11]', 'S0.R', 'attribute0.3', 'attribute1.0', 'attribute1.1',
                          'attribute3.0', 'attribute3.1', 'attribute3.2', 'ps.FRONT_FACE', 'ps.POS_W'})
        p = surface(grazing_power=6.0)          # a smaller exponent keeps the grazing term visible in random trials
        names = {n['name'] for n in compiler.ir.nodes if n['op'] == 'input'}
        rng = random.Random(5)
        for _ in range(40):
            inp = {k: 0.0 for k in names}
            inp.update({'CB2[8]': p['depth_fade'], 'CB2[9]': p['grazing_power'], 'CB2[10]': p['near_fade_start'],
                        'CB2[11]': p['near_fade_end'], 'ps.FRONT_FACE': 1.0, 'ps.POS_W': rng.uniform(0.5, 30.0),
                        'attribute0.3': rng.uniform(0.0, 1.0)})
            v = [rng.uniform(-3.0, 3.0) for _ in range(3)]
            n = [rng.uniform(-1.0, 1.0), rng.uniform(0.2, 1.0)]
            inp.update({'attribute3.%d' % k: v[k] for k in range(3)})
            inp.update({'attribute1.0': n[0], 'attribute1.1': n[1]})
            depth = inp['ps.POS_W'] + rng.uniform(-0.2, 0.6)
            got = forward_loops.evaluate(compiler.ir, alpha, inp, samples={(0, 0): depth})
            dist = math.sqrt(sum(x * x for x in v))
            ndv = (n[0] * v[0] + n[1] * v[1]) / math.hypot(*n) / dist
            want = W.water_surface_alpha(dist, depth - inp['ps.POS_W'], ndv, p, inp['attribute0.3'])[1]
            self.assertAlmostEqual(got, want, places=5)


if __name__ == '__main__':
    unittest.main()
