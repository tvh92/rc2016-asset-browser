"""pok_gnd_coral_underwater_blend_01 (writer 35571F99): exact-graph viewer rule and its source-arithmetic control.

The rule tests use synthetic data. The archive tests read the game archive (RC2016_RESEARCH) and skip without it.
"""
import math
import os
import random
import unittest
from pathlib import Path

from rc2016.viewer import textures as tp

GRAPH = 'materialgraph/environment/pokitaru/ground/pok_gnd_coral_underwater_blend_01/pok_gnd_coral_underwater_blend_01.materialgraph'
MATERIAL = 'material/environment/pokitaru/ground/pok_gnd_coral_underwater_blend_01/pok_gnd_coral_underwater_blend_01.material'
CODE_SHA = '3ca119e33063cae178490321ed00985757a2e2c04af34eacc12cb546be7bdc32'
TEXTURES = 'textures/environment/pokitaru/natural/'
CB2 = [0.0, 0.25, 2.0, 2.4, -2.4, -2.6, 0.105, 0.095, 0.21, 0.19, 0.03, 1.0, 1.0, 0.5, 223.0, 2.0, 0.0, 2.6, 4.0, 0.0]


class CoralBlendRuleTests(unittest.TestCase):
    def test_rule_needs_the_exact_graph_hash(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: tp.CORAL_BLEND_GRAPH_SHA256})
        self.assertIs(rule['coral_blend'], True)
        self.assertEqual((rule['graph_path'], rule['graph_sha256']), (GRAPH, tp.CORAL_BLEND_GRAPH_SHA256))
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH]), {})

    def test_term_is_registered_for_the_blender_front_end(self):
        from rc2016.formats import materials
        self.assertIn('coral_blend', materials.VIEWER_RULE_EXTRAS)
        self.assertIn('coral_blend', materials.VIEWER_RULE_BUILT)
        self.assertIn('coral_blend', materials.VIEWER_LINEAR_Z_TERMS)
        self.assertEqual(materials.VIEWER_TERM_ROLES['coral_blend'], tp.CORAL_BLEND_VOLUME_ROLES)
        self.assertEqual(len(tp.CORAL_BLEND_VOLUME_ROLES), 10)

    def test_volume_keeps_every_slice_and_its_scale(self):
        class Image:
            def __init__(self, size, value):
                self.size, self.value = size, value
        slices = []
        for size, value in ((2, 10), (4, 20), (4, 30)):
            slices.append(dict(path='t%d' % value, width=size, height=size, srgb=False,
                               pixels=bytes([value, value, value, 255]) * size * size, format=0, slices=[]))
        original = tp.to_image
        try:
            from PIL import Image as PilImage
            tp.to_image = lambda t, index=0: PilImage.frombytes('RGBA', (t['width'], t['height']), t['pixels'])
            volume = tp.rock_texture_volume(slices)
        finally:
            tp.to_image = original
        self.assertEqual((volume['width'], volume['height'], volume['depth']), (4, 4, 3))
        self.assertEqual(len(volume['pixels']), 4 * 4 * 4 * 3)
        self.assertEqual(volume['scales'], [(0.5, 0.5), (1.0, 1.0), (1.0, 1.0)])


def kernel(a):
    """The decoded surface of writer 35571F99 pass 0 (CB2 above): albedo, F0, gloss and the encoded normal."""
    def unit(v):
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]
    lerp = lambda x, y, t: x + t * (y - x)
    sat = lambda x: min(1.0, max(0.0, x))
    n, t = unit(a['n']), unit(a['t'])
    hand = (a['hand'] > 0) - (a['hand'] < 0)
    cross = [n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0]]
    b = unit([c * hand for c in cross])
    sigma = 1.0 if a['front'] else -1.0

    def frame_normal(rg, strength=1.0):
        x, y = (2 * rg[0] - 1) * strength, (2 * rg[1] - 1) * strength
        z = 1 - x * x - y * y
        v = [n[i] * z + x * t[i] + y * b[i] for i in range(3)]
        length = math.sqrt(sum(c * c for c in v))
        return [sigma * c / length for c in v]
    tex, p0, p1 = a['tex'], a['p0'], a['p1']
    m = sat((tex['cma'] - (p0 - CB2[13])) / CB2[13])
    normal = [lerp(lerp(w, c, p1), r, m) for w, c, r in
              zip(frame_normal(tex['wavy_n']), frame_normal(tex['coral_n']), frame_normal(tex['rock_n']))]
    # caustic: weights from the native normal, planes (y, z), (z, x), (x, y), scroll CB2[3..7], CB2[17] times 0.333 time
    wp = a['wp']
    w = [max(0.0, 7 * abs(c) - 1.4) for c in n]
    w = [c / sum(w) for c in w]
    # the images are keyed by sample, so the plane coordinates (scale * (position + scroll * t3)) do not enter the control
    blur, noise, smoke = (sum(weight * image for weight, image in zip(w, tex[key])) for key in ('blur', 'noise', 'smoke'))
    low = CB2[14] - CB2[15]
    fade = 1.0 if wp[1] <= low else (CB2[14] - min(CB2[14], max(low, wp[1]))) / CB2[15]
    caustic = fade * sat(0.952381 * smoke + 0.047619) * sat(0.0472 - abs(blur - noise) ** 1.52)
    albedo = []
    for k in range(3):
        base = lerp(tex['wavy_c'][k], tex['coral_c'][k], p1)
        albedo.append(caustic + lerp(base, tex['rock_c'][k], m))
    f0 = lerp(CB2[11] * lerp(tex['rock_g'][1], tex['sand_g'][1], p1), CB2[1] * tex['coral_g'][1], m)
    gloss = math.sqrt(sat(lerp(CB2[12] * lerp(tex['rock_g'][0], tex['sand_g'][0], p1), CB2[2] * tex['coral_g'][0], m)))
    return albedo, f0, gloss, [0.5 * c + 0.5 for c in normal]


class CoralBlendArchiveTests(unittest.TestCase):
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
        cls.program = next(p for p in compile_graph(cls.graph, allow_independent_samplers=True)['programs']
                           if p['code_sha256'] == CODE_SHA)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_material_reading_binds_the_decoded_slots_and_constants(self):
        import hashlib
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), tp.CORAL_BLEND_GRAPH_SHA256)
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        expected = dict(_n='rp_pok_rock_eroded_n', _c='rp_coral_bumpy_tile_c', _g='rp_coral_bumpy_tile_g',
                        _coral_n_wavy='rp_pok_sand_floor_wavy_n', _coral_n_coral='rp_coral_bumpy_tile_n',
                        _coral_c_wavy='rp_pok_sand_floor_wavy_c', _coral_c_rock='rp_pok_rock_eroded_c',
                        _coral_g_rock='rp_pok_rock_eroded_g')
        for role, name in expected.items():
            self.assertEqual(paths[role], TEXTURES + name + '.texture')
        self.assertEqual(paths['_coral_g_sand'], 'textures/environment/gaspar/ground/gas_ground_sand_01/gas_ground_sand_01_g.texture')
        self.assertEqual(paths['_coral_cma'], 'textures/environment/test/test_gas_rock_tile_01/test_gas_rock_tile_01_cma.texture')
        self.assertEqual((paths['_caustic_blur'], paths['_caustic_noise'], paths['_caustic_smoke']),
                         ('textures/visualeffect/noise/fx_noisetile03_blur.texture', 'textures/visualeffect/noise/fx_noisetile03.texture',
                          'textures/visualeffect/noise/fractalnoise_rollingsmoke.texture'))
        cb = paths['coral_blend']
        for key, value in (('height_width', 0.5), ('water_top', 223.0), ('water_fade', 2.0), ('coral_f0', 0.25),
                           ('coral_gloss', 2.0), ('base_f0', 1.0), ('base_gloss', 1.0)):
            self.assertAlmostEqual(cb[key], value, places=6)
        for got, want in zip(cb['noise_scale'], (0.21, 0.19, 0.03)):
            self.assertAlmostEqual(got, want, places=6)
        for got, want in zip(cb['scroll'], ((2.6, 2.4, 2.6), (-2.4, -2.6, -2.4), (0.105, 0.095, 0.105))):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=6)
        self.assertEqual((paths['g_mode'], paths['k'], paths['spec'], paths['gloss']), ('grey', 1.0, 1.0, 1.0))   # K = 1: the writer's self-lit is 0.2 log2(1 + coral _c.R)
        self.assertEqual(paths['graph_identity']['sha256'], tp.CORAL_BLEND_GRAPH_SHA256)

    def test_writer_matches_the_decoded_kernel(self):
        from rc2016.shaders.ir import DAG
        from rc2016.shaders.translator import lower_deferred
        lowered = lower_deferred(self.program['ir'])
        ir = DAG(lowered['nodes'])
        subs = {i: ir.const(0.0) for i, node in enumerate(ir.nodes) if node['op'] == 'unsupported'}   # strength fade: close range
        # the material's constant words are folded in, so the unused UV-warp branches (CB2[0], CB2[16] > 0) drop out
        subs.update({i: ir.const(CB2[int(node['name'][4:-1])]) for i, node in list(enumerate(ir.nodes))
                     if node['op'] == 'input' and node['name'].startswith('CB2[')})
        out, normal = lowered['outputs'], self.program['ir']['exports']['0']
        roots = ir.replace([*out['albedo'], out['F0'][0], out['gloss'], *normal[:3], out['emission']], subs)
        # per sample: API 5 + resource index; the three planar images per noise API follow program order (y,z), (z,x), (x,y)
        planes = {}
        for node in ir.nodes:
            if node['op'] == 'texture' and node['api'] in (13, 14, 15):
                planes.setdefault(node['api'], sorted({n['sample'] for n in ir.nodes if n['op'] == 'texture' and n['api'] == node['api']}))
        rng = random.Random(11)
        worst = 0.0
        for case in range(300):
            tex = dict(rock_n=(rng.random(), rng.random()), wavy_n=(rng.random(), rng.random()), coral_n=(rng.random(), rng.random()),
                       cma=rng.random(), rock_g=(rng.random(), rng.random()), sand_g=(rng.random(), rng.random()),
                       coral_g=(rng.random(), rng.random()), wavy_c=[rng.random() for _ in range(3)],
                       coral_c=[rng.random() for _ in range(3)], rock_c=[rng.random() for _ in range(3)],
                       blur=[rng.random() for _ in range(3)], noise=[rng.random() for _ in range(3)], smoke=[rng.random() for _ in range(3)])
            case_in = dict(n=[rng.uniform(-1, 1) for _ in range(3)], t=[rng.uniform(-1, 1) for _ in range(3)],
                           hand=rng.choice((-1.0, 1.0)), front=case % 3 != 0, p0=rng.random(), p1=rng.random(),
                           wp=[rng.uniform(-50, 50), rng.uniform(215, 225), rng.uniform(-50, 50)], time=rng.uniform(0, 100), tex=tex)
            inputs = {'attribute2.%d' % i: case_in['n'][i] for i in range(3)}
            inputs.update({'attribute3.%d' % i: case_in['t'][i] for i in range(3)})
            inputs.update({'attribute3.3': case_in['hand'], 'attribute4.0': case_in['p0'], 'attribute4.1': case_in['p1'],
                           'ps.FRONT_FACE': 1.0 if case_in['front'] else 0.0, 'attribute0.0': 0.3, 'attribute0.1': 0.6,
                           'ps.POS_W': 1.0, 'ps.POS_X': 0.0, 'ps.POS_Y': 0.0, 'CB0[95]': 1.0, 'CB0[99]': case_in['time']})
            for k in range(92, 108):
                inputs.setdefault('CB0[%d]' % k, 0.0)
            inputs.update({'CB0[28]': case_in['wp'][0], 'CB0[29]': case_in['wp'][1], 'CB0[30]': case_in['wp'][2]})
            for k, v in enumerate(CB2):
                inputs['CB2[%d]' % k] = v
            by_api = {5: tex['rock_n'], 6: tex['coral_c'], 7: (*tex['rock_g'], 0.0), 8: (*tex['sand_g'], 0.0), 9: (*tex['coral_g'], 0.0),
                      10: (tex['cma'],) * 3, 11: tex['wavy_n'], 12: tex['coral_n'], 16: tex['wavy_c'], 17: tex['rock_c']}
            textures = {}
            for node in ir.nodes:
                if node['op'] != 'texture':
                    continue
                api, channel = node['api'], node['channel']
                if api in planes:
                    value = tex[{13: 'blur', 14: 'noise', 15: 'smoke'}[api]][planes[api].index(node['sample'])]
                else:
                    values = by_api[api]
                    value = values[channel] if channel < len(values) else 1.0
                textures[(node['sample'], channel)] = value
            got = [ir.evaluate(root, inputs, textures) for root in roots]
            albedo, f0, gloss, encoded = kernel(dict(case_in, wp=case_in['wp']))
            for a, b in zip(got, [*albedo, f0, gloss, *encoded, tex['coral_c'][0]]):
                worst = max(worst, abs(a - b))
        self.assertLess(worst, 1e-5)


if __name__ == '__main__':
    unittest.main()
