"""gas_rock_sediment_01 and gbl_rock_ds_sediment_01 (pass-0 writers 0440BD68 and 2CF602BD): exact-graph viewer rules
and their source-arithmetic controls.

The rule tests use synthetic data. The archive tests read the game archive (RC2016_RESEARCH) and skip without it.
"""
import math
import os
import random
import unittest
from pathlib import Path

from rc2016.viewer import textures as tp

GAS_GRAPH = 'materialgraph/environment/gaspar/rock/gas_rock_sediment_01/gas_rock_sediment_01.materialgraph'
GBL_GRAPH = 'materialgraph/environment/global/rock/gbl_rock_ds_sediment_01/gbl_rock_ds_sediment_01.materialgraph'
GAS_MATERIAL = 'material/environment/gaspar/rock/gas_rock_cliff_01/gas_rock_cliff_01.material'
GBL_MATERIAL = 'material/environment/gaspar/rock/gas_rock_cliff_04/gas_rock_cliff_04.material'
GAS_CODE = '0440bd68aae0d231c61c58149eeb930bddb155185f1d9858a9088c83a13df4a0'
GBL_CODE = '2cf602bd76956ebfd7c68509eac99f226166cd94f0e4c129fe1e8655f24474fc'
GAS_CB2 = [1.0, 25.0, 120.0, 1.0, 25.0, 30.0, 0.0, 0.0]
GBL_CB2 = [128.0, 0.2, 0.1, 1.0, 1.0, 1.0, 60.0, 0.0]
COS1, SIN1 = math.cos(1.0), math.sin(1.0)


class SedimentRuleTests(unittest.TestCase):
    def test_rules_need_the_exact_graph_hash(self):
        for graph, sha, key in ((GAS_GRAPH, tp.GAS_SEDIMENT_GRAPH_SHA256, 'gas'), (GBL_GRAPH, tp.GBL_SEDIMENT_GRAPH_SHA256, 'gbl')):
            rule = tp._graph_rule([graph], {graph: sha})
            self.assertEqual(rule['sediment_blend'], key)
            self.assertEqual((rule['graph_path'], rule['graph_sha256']), (graph, sha))
            self.assertEqual(tp._graph_rule([graph], {graph: '0' * 64}), {})

    def test_exact_rules_replace_the_grey_g_family_entries(self):
        from rc2016.formats.links import EXACT, rule_coverage
        from rc2016.viewer.grey_g_graphs import GREY_G_GRAPHS
        self.assertNotIn(GAS_GRAPH, GREY_G_GRAPHS)
        self.assertNotIn(GBL_GRAPH, GREY_G_GRAPHS)
        self.assertEqual(rule_coverage([GAS_GRAPH])[0], EXACT)
        self.assertEqual(rule_coverage([GBL_GRAPH])[0], EXACT)

    def test_term_is_registered_for_the_blender_front_end(self):
        from rc2016.formats import materials
        self.assertIn('sediment_blend', materials.VIEWER_RULE_EXTRAS)
        self.assertIn('sediment_blend', materials.VIEWER_RULE_BUILT)
        self.assertIn('sediment_blend', materials.VIEWER_LINEAR_Z_TERMS)
        self.assertEqual(len(tp.SEDIMENT_VOLUME_ROLES['gas']), 5)
        self.assertEqual(len(tp.SEDIMENT_VOLUME_ROLES['gbl']), 5)
        for roles in tp.SEDIMENT_VOLUME_ROLES.values():
            for role in roles:
                self.assertIn(role, materials.VIEWER_TERM_ROLES['sediment_blend'])


def smooth(api, u, v, channel):
    """A smooth stand-in image: any coordinate error shows in the sampled value."""
    return 0.5 + 0.45 * math.sin(2.1 * u + 1.7 * v + 0.9 * api + 1.3 * channel + 0.4)


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def overlay(a, b):
    return 2 * a * b if a < 0.5 else 1 - 2 * (1 - a) * (1 - b)


def frame_normal(inputs, rg):
    n, t = unit(inputs['n']), unit(inputs['t'])
    hand = (inputs['hand'] > 0) - (inputs['hand'] < 0)
    cross = [n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0]]
    b = unit([c * hand for c in cross])
    sigma = 1.0 if inputs['front'] else -1.0
    x, y = 2 * rg[0] - 1, 2 * rg[1] - 1
    z = 1 - x * x - y * y
    v = [n[i] * z + x * t[i] + y * b[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in v))
    return [sigma * c / length for c in v]


# (p axis, q axis, normal axis) per face: p, q are signed native-world components; the slag-normal columns follow.
def cube_face(inputs):
    """Dominant native-axis face of the front-face-corrected normal: planar (p, q) and the slag normal matrix."""
    n = unit(inputs['n'])
    sigma = 1.0 if inputs['front'] else -1.0
    a = [abs(c) for c in n]
    wx, wy, wz = inputs['wp']
    ns = [sigma * c for c in n]
    xd = a[0] >= a[1] and a[0] >= a[2]
    yd = not xd and a[1] >= a[0] and a[1] >= a[2]
    c, s = COS1, SIN1
    if xd:
        sg = 1.0 if ns[0] > 0 else -1.0
        pq = (-sg * wz, -wy)
        cols = ((0, s, -c), (0, c, s), (1, 0, 0)) if sg > 0 else ((0, s, c), (0, c, -s), (-1, 0, 0))
    elif yd:
        sg = 1.0 if ns[1] > 0 else -1.0
        pq = (wx, sg * wz)
        cols = ((c, 0, -s), (-s, 0, -c), (0, 1, 0)) if sg > 0 else ((c, 0, s), (-s, 0, c), (0, -1, 0))
    else:
        sg = 1.0 if ns[2] > 0 else -1.0
        pq = (sg * wx, -wy)
        cols = ((c, s, 0), (-s, c, 0), (0, 0, 1)) if sg > 0 else ((c, -s, 0), (s, c, 0), (0, 0, -1))
    uv = (0.125 * (c * pq[0] - s * pq[1]), 0.125 * (s * pq[0] + c * pq[1]))
    return uv, cols


def gas_kernel(inputs, tex=smooth):
    """The decoded surface of writer 0440BD68 (gas_rock_sediment_01 pass 0): albedo, F0, gloss and the encoded normal."""
    y = inputs['wp'][1]
    cb = GAS_CB2
    sat = lambda x: min(1.0, max(0.0, x))
    w = sat((y - cb[5]) / (cb[4] - cb[5]))
    d = y - cb[1]
    cma = [tex(8, 0.005 * inputs['wp'][0], 0.005 * inputs['wp'][2], ch) for ch in (0, 2)]   # R, B
    e = sat(d / (cb[2] - cb[1]))
    g = sat(d / (40 * (cma[1] + e * (cma[0] - cma[1])) + cb[2] - cb[1]))
    uv, cols = cube_face(inputs)
    slag_rg = [tex(6, uv[0], uv[1], ch) for ch in (0, 1)]
    sx, sy = 2 * slag_rg[0] - 1, 2 * slag_rg[1] - 1
    sz = 1 - sx * sx - sy * sy
    slag = unit([sx * cols[0][i] + sy * cols[1][i] + sz * cols[2][i] for i in range(3)])
    ty = unit(inputs['t'])[1]
    u0, v0 = inputs['uv']
    albedo = []
    for ch in range(3):
        ov = overlay(tex(9, g, g, ch), tex(10, u0, v0, ch))
        gma = tex(12, tex(11, uv[0], uv[1], 0), ty, ch)
        albedo.append(ov + w * (gma - ov))
    gr, gg = tex(5, u0, v0, 0), tex(5, u0, v0, 1)
    f0 = cb[3] * (1 - w) * gg
    gloss = math.sqrt(sat(cb[0] * (1 - w) * gr))
    rock = frame_normal(inputs, [tex(7, u0, v0, 0), tex(7, u0, v0, 1)])
    mixed = [rock[i] + w * (slag[i] - rock[i]) for i in range(3)]
    return albedo, f0, gloss, [0.5 * c + 0.5 for c in mixed]


def gbl_kernel(inputs, tex=smooth):
    """The decoded surface of writer 2CF602BD (gbl_rock_ds_sediment_01 pass 0)."""
    cb = GBL_CB2
    sat = lambda x: min(1.0, max(0.0, x))
    p = inputs['p']
    u0, v0 = inputs['uv']
    wx, wy, wz = inputs['wp']
    cma = tex(9, wx / cb[0], wz / cb[0], 0)
    h = wy / cb[6]
    u = h + cb[1] * cma + sat(2 * h - 0.5) * (cb[2] * cma - cb[1] * cma)
    wall_g, cliff_g = [tex(5, u0, v0, ch) for ch in (0, 1)], [tex(6, u0, v0, ch) for ch in (0, 1)]
    gl = sat(cb[5] * wall_g[0] + p * (cb[4] * cliff_g[0] - cb[5] * wall_g[0]))
    v = 1 - 2 ** (-11 * math.sqrt(gl))
    albedo = []
    for ch in range(3):
        colour = tex(10, u0, v0, ch) + p * (tex(11, u0, v0, ch) - tex(10, u0, v0, ch))
        albedo.append(overlay(tex(12, u, v, ch), colour))
    wall = frame_normal(inputs, [tex(7, u0, v0, 0), tex(7, u0, v0, 1)])
    cliff = frame_normal(inputs, [tex(8, u0, v0, 0), tex(8, u0, v0, 1)])
    mixed = [wall[i] + p * (cliff[i] - wall[i]) for i in range(3)]
    return albedo, cb[3] * cliff_g[1], math.sqrt(gl), [0.5 * c + 0.5 for c in mixed]


class SedimentArchiveTests(unittest.TestCase):
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
        cls.programs = {}
        for graph, code in ((GAS_GRAPH, GAS_CODE), (GBL_GRAPH, GBL_CODE)):
            data = bytes(asset_bytes(cls.archive, graph))
            cls.programs[graph] = next(p for p in compile_graph(data, allow_independent_samplers=True)['programs'] if p['code_sha256'] == code)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_graph_hashes(self):
        import hashlib
        from rc2016.levels.scene import asset_bytes
        self.assertEqual(hashlib.sha256(bytes(asset_bytes(self.archive, GAS_GRAPH))).hexdigest(), tp.GAS_SEDIMENT_GRAPH_SHA256)
        self.assertEqual(hashlib.sha256(bytes(asset_bytes(self.archive, GBL_GRAPH))).hexdigest(), tp.GBL_SEDIMENT_GRAPH_SHA256)

    def test_material_reading_binds_the_decoded_slots_and_constants(self):
        gas = tp.material_texture_paths(self.archive, GAS_MATERIAL)
        self.assertEqual((gas['g_mode'], gas['k'], gas['spec'], gas['gloss']), ('grey', 0.0, 1.0, 1.0))
        self.assertEqual(gas['graph_identity']['sha256'], tp.GAS_SEDIMENT_GRAPH_SHA256)
        gb = gas['sediment_blend']
        self.assertEqual(gb['kind'], 'gas')
        for got, want in zip((gb['gloss_scale'], gb['f0_scale'], gb['gradient_start'], gb['gradient_end'], gb['weight_start'], gb['weight_end']),
                             (1.0, 1.0, 25.0, 120.0, 25.0, 30.0)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertTrue(gas['_c'].endswith('gas_rock_cliff_01_c.texture') and gas['_n'].endswith('gas_rock_cliff_01_n.texture'))
        self.assertTrue(gas['_sed_noise'].endswith('gbl_noise_perlin_clouds_01_cma.texture'))
        self.assertTrue(gas['_sed_slag_gma'].endswith('gas_rock_slag_01_gma.texture'))
        gbl = tp.material_texture_paths(self.archive, GBL_MATERIAL)
        self.assertEqual((gbl['g_mode'], gbl['k']), ('grey', 0.0))
        bb = gbl['sediment_blend']
        self.assertEqual(bb['kind'], 'gbl')
        for got, want in zip((bb['world_tile'], bb['noise_a'], bb['noise_b'], bb['height_tile'], bb['f0_scale'], bb['cliff_gloss_scale'], bb['wall_gloss_scale']),
                             (128.0, 0.2, 0.1, 60.0, 1.0, 1.0, 1.0)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertTrue(gbl['_c'].endswith('gas_rock_cliff_04_c.texture') and gbl['_sed_wall_c'].endswith('gas_rock_cave_wall_02_c.texture'))
        self.assertTrue(gbl['_sed_sediment'].endswith('gas_rock_cave_wall_sediment.texture'))

    def _compare(self, graph, cb2, kernel, paint):
        from rc2016.shaders.ir import DAG
        from rc2016.shaders.translator import lower_deferred
        program = self.programs[graph]
        lowered = lower_deferred(program['ir'])
        ir = DAG(lowered['nodes'])
        subs = {i: ir.const(0.0) for i, node in enumerate(ir.nodes) if node['op'] == 'unsupported'}   # strength fade: close range
        subs.update({i: ir.const(cb2[int(node['name'][4:-1])]) for i, node in list(enumerate(ir.nodes))
                     if node['op'] == 'input' and node['name'].startswith('CB2[')})
        out, normal = lowered['outputs'], program['ir']['exports']['0']
        roots = ir.replace([*out['albedo'], out['F0'][0], out['gloss'], *normal[:3]], subs)
        cb2_inputs = {'CB2[%d]' % k: v for k, v in enumerate(cb2)}
        rng = random.Random(5)
        worst = 0.0
        for case in range(300):
            wp = [rng.uniform(-400, 400), rng.uniform(5, 160), rng.uniform(-400, 400)]
            n = [rng.uniform(-1, 1) for _ in range(3)]
            if case % 4 == 0:   # axis-aligned normals exercise every face
                n = [0.0, 0.0, 0.0]
                n[case // 4 % 3] = rng.choice((-1.0, 1.0)) * rng.uniform(1.0, 2.0)
                n = [c + rng.uniform(-0.2, 0.2) for c in n]
            case_in = dict(n=n, t=[rng.uniform(-1, 1) for _ in range(3)], hand=rng.choice((-1.0, 1.0)), front=case % 3 != 0,
                           wp=wp, uv=(rng.uniform(-2, 2), rng.uniform(-2, 2)), p=rng.random())
            inputs = {'attribute2.%d' % i: case_in['n'][i] for i in range(3)}
            inputs.update({'attribute3.%d' % i: case_in['t'][i] for i in range(3)})
            inputs.update({'attribute3.3': case_in['hand'], 'attribute4.0': case_in['p'], 'attribute0.0': case_in['uv'][0],
                           'attribute0.1': case_in['uv'][1], 'ps.FRONT_FACE': 1.0 if case_in['front'] else 0.0,
                           'ps.POS_W': 1.0, 'ps.POS_X': 0.0, 'ps.POS_Y': 0.0, 'CB0[95]': 1.0, 'CB0[99]': 0.0,
                           'CB0[139]': 0.0, 'attribute1.2': 1.0})   # geometric-AA width 0
            for k in range(92, 108):
                inputs.setdefault('CB0[%d]' % k, 0.0)
            ray = (0.3, 0.5, 0.7)   # camera ray at the origin pixel: a nonzero offset keeps the geometric-AA normalisation finite
            inputs.update({'CB0[92]': ray[0], 'CB0[93]': ray[1], 'CB0[94]': ray[2]})
            inputs.update({'CB0[28]': wp[0] - ray[0], 'CB0[29]': wp[1] - ray[1], 'CB0[30]': wp[2] - ray[2]})
            inputs.update(cb2_inputs)
            values = {}
            for node in ir.nodes:
                if node['op'] != 'texture':
                    continue
                coords = [ir.evaluate(q, inputs, values) for q in node['args'][:2]]
                values[(node['sample'], node['channel'])] = smooth(node['api'], coords[0], coords[1], node['channel'])
            got = [ir.evaluate(root, inputs, values) for root in roots]
            albedo, f0, gloss, encoded = kernel(case_in)
            for a, b in zip(got, [*albedo, f0, gloss, *encoded]):
                worst = max(worst, abs(a - b))
        self.assertLess(worst, 1e-5)

    def test_gas_writer_matches_the_decoded_kernel(self):
        self._compare(GAS_GRAPH, GAS_CB2, gas_kernel, False)

    def test_gbl_writer_matches_the_decoded_kernel(self):
        self._compare(GBL_GRAPH, GBL_CB2, gbl_kernel, True)


if __name__ == '__main__':
    unittest.main()
