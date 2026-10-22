"""rp_pok_grass_blend_01 (writer A814EDCF): exact-graph viewer rule and its source-arithmetic control.

The rule tests use synthetic data. The archive tests read the game archive (RC2016_RESEARCH) and skip without it.
"""
import math
import os
import random
import unittest
from pathlib import Path

from rc2016.viewer import textures as tp

GRAPH = 'materialgraph/environment/pokitaru/rock/pok_rock_blend_01/rp_pok_grass_blend_01.materialgraph'
MATERIAL = 'material/environment/pokitaru/ground/pok_gnd_sand_floor_blend_01/rp_pok_grass_tile_blend.material'
CODE_SHA = 'ff995b9ff4248ed6620a859e75b46ba58d6b2d9e9ccfc4a4b32086083c2b6c3a'
TEXTURES = 'textures/environment/pokitaru/natural/'


class GrassBlendRuleTests(unittest.TestCase):
    def test_rule_needs_the_exact_graph_hash(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: tp.GRASS_BLEND_GRAPH_SHA256})
        self.assertIs(rule['grass_blend'], True)
        self.assertEqual((rule['graph_path'], rule['graph_sha256']), (GRAPH, tp.GRASS_BLEND_GRAPH_SHA256))
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH]), {})

    def test_term_is_registered_for_the_blender_front_end(self):
        from rc2016.formats import materials
        self.assertIn('grass_blend', materials.VIEWER_RULE_EXTRAS)
        self.assertIn('grass_blend', materials.VIEWER_RULE_BUILT)
        self.assertIn('grass_blend', materials.VIEWER_LINEAR_Z_TERMS)
        self.assertEqual(materials.VIEWER_TERM_ROLES['grass_blend'], ('_grass_r', '_grass_g', '_grass_g9'))


def kernel(attrs, cb2, tex, front):
    """The decoded surface: q from the bump-composed normal, w, albedo, F0 and gloss input."""
    def unit(v):
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]
    n, t = unit(attrs[0:3]), unit(attrs[3:6])
    cross = [n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0]]
    b = [c * math.copysign(1.0, attrs[6]) for c in unit(cross)]
    x, y = 2 * tex['n'][0] - 1, 2 * tex['n'][1] - 1
    z = 1 - x * x - y * y
    mapped = [n[i] * z + x * t[i] + y * b[i] for i in range(3)]
    q = (1.0 if front else -1.0) * mapped[1] / math.sqrt(sum(m * m for m in mapped))
    c = cb2[0]
    w = min(1.0, max(0.0, (q - 0.5 * c) / (1 - c)))
    lerp = lambda a, b2, k: [u + k * (v - u) for u, v in zip(a, b2)]
    albedo = lerp(tex['r'], lerp(tex['g'], tex['b'], w), w)
    f0 = tex['g8'][1] + w * (tex['g9'][1] - tex['g8'][1])
    gloss = math.sqrt(min(1.0, max(0.0, tex['g8'][0] + w * (tex['g9'][0] - tex['g8'][0]))))
    return albedo, f0, gloss


class GrassBlendArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_RESEARCH')
        if not research:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        root = Path(os.environ.get('RC2016_GAME_ROOT', str(Path(research).parents[1] / 'Ratchet & Clank (EUR)' / 'Image0')))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import asset_bytes, open_archive
        from rc2016.shaders.translator import compile_graph
        cls.archive = open_archive(root)
        cls.graph = bytes(asset_bytes(cls.archive, GRAPH))
        cls.material = bytes(asset_bytes(cls.archive, MATERIAL))
        cls.program = next(p for p in compile_graph(cls.graph)['programs'] if p['code_sha256'] == CODE_SHA)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_material_reading_binds_the_decoded_slots_and_constants(self):
        import hashlib
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), tp.GRASS_BLEND_GRAPH_SHA256)
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        expected = dict(_n='rp_pok_bumpy_tile_n', _g='rp_pok_rock_eroded_c', _grass_r='rp_pok_rock_eroded_c',
                        _grass_g='rp_pok_grass_tile_c', _c='rp_pok_grass_color_breakup_c')
        for role, name in expected.items():
            self.assertEqual(paths[role], TEXTURES + name + '.texture')
        self.assertEqual(paths['_grass_g9'], 'textures/environment/global/masks/mo_gbl_masks/mo_black_h.texture')
        gb = paths['grass_blend']
        for key, value in (('threshold', 0.319), ('uv_scale', 5.0), ('layer_uv_scale', 1.25), ('world_tile', 40.0)):
            self.assertAlmostEqual(gb[key], value, places=6)
        self.assertEqual((paths['g_mode'], paths['k'], paths['spec'], paths['gloss']), ('grey', 0.0, 1.0, 1.0))
        self.assertEqual(paths['graph_identity']['sha256'], tp.GRASS_BLEND_GRAPH_SHA256)

    def test_writer_matches_the_decoded_kernel(self):
        from rc2016.shaders.ir import DAG
        from rc2016.shaders.translator import lower_deferred
        lowered = lower_deferred(self.program['ir'])
        ir = DAG(lowered['nodes'])
        subs = {i: ir.const(0.0) for i, node in enumerate(ir.nodes) if node['op'] == 'unsupported'}   # fade terms: close range
        out = lowered['outputs']
        roots = ir.replace([*out['albedo'], out['F0'][0], out['gloss']], subs)
        # sample index = api - 5: 0 normal, 3 and 4 the F0/gloss images, 5 tile, 6 breakup, 7 rock colour
        cb2 = [0.319, 5.0, 1.5, 0.638, 40.0, 0.475, 1.25]
        rng = random.Random(7)
        worst = 0.0
        for case in range(300):
            front = case % 3 != 0
            normal = [rng.uniform(-1, 1) for _ in range(3)]
            tangent = [rng.uniform(-1, 1) for _ in range(3)]
            hand = rng.choice((-1.0, 1.0))
            tex = dict(n=(rng.random(), rng.random()), g8=(rng.random(), rng.random()), g9=(rng.random(), rng.random()),
                       g=[rng.random() for _ in range(3)], b=[rng.random() for _ in range(3)], r=[rng.random() for _ in range(3)])
            inputs = {'attribute2.%d' % i: normal[i] for i in range(3)}
            inputs.update({'attribute3.%d' % i: tangent[i] for i in range(3)})
            inputs.update({'attribute3.3': hand, 'CB0[95]': 1.0, 'ps.FRONT_FACE': 1.0 if front else 0.0,
                           'attribute0.0': 0.3, 'attribute0.1': 0.6, 'ps.POS_W': 1.0, 'ps.POS_X': 0.0, 'ps.POS_Y': 0.0})
            for k in range(92, 108):
                inputs.setdefault('CB0[%d]' % k, 0.0)
            for k, v in enumerate(cb2):
                inputs['CB2[%d]' % k] = v
            textures = {}
            for node in ir.nodes:
                if node['op'] != 'texture':
                    continue
                # samples 1 and 2 (normals B, C) feed only the unused MRT0 terms
                values = {0: tex['n'], 3: tex['g8'], 4: tex['g9'], 5: tex['g'], 6: tex['b'], 7: tex['r']}.get(node['sample'], (0.5,) * 3)
                textures[(node['sample'], node['channel'])] = values[node['channel']] if node['channel'] < len(values) else 1.0
            got = [ir.evaluate(root, inputs, textures) for root in roots]
            albedo, f0, gloss = kernel([*normal, *tangent, hand], cb2, tex, front)
            for a, b in zip(got, [*albedo, f0, gloss]):
                worst = max(worst, abs(a - b))
        self.assertLess(worst, 1e-5)


if __name__ == '__main__':
    unittest.main()
