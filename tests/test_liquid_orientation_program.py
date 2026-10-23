"""Forward program F0F45D78 of test_liquid_orientation (docs/material-decoding/hydrodisplacer-liquid.md).

The archive-backed checks pin the program identities, compile F0F45D78 with the loop-opaque research compiler
and compare every material-only term numerically with the closed forms restated below, independently of the
translator. They skip without RC2016_RESEARCH / RC2016_GAME_ROOT.
"""
import hashlib
import math
import os
import random
import unittest
from pathlib import Path

GRAPH = 'materialgraph/global/test/test_liquid_orientation/test_liquid_orientation.materialgraph'
MATERIAL = 'material/global/test/test_liquid_orientation/test_liquid_orientation.material'
GRAPH_SHA = '7eb07c69c31448c0efee469b681f1e6084b13f33ef8481d8ad39b05ca6097fb3'
CODE_SHA = {'0xf0f45d78': '43f06ca907a464ebc4a8d40b316aeceec5d701a8d759bd4d10c1f730cc7f6259',
            '0x09be55f4': 'a248c12e429ac8e38d7187034ab44e1b08f944d00248d19e784b0be9097b825a'}
LIT_FLAGS = 0x2261                       # lit diffuse (0x20), lit specular (0x40) and fog (0x2000) paths; 0x80 clear
# Node numbers of the loop-opaque DAG of F0F45D78 (stable for this code hash, checked by test_identities).
NORMAL, A2, F0, F90, N_DOT_V, LEVEL = (446, 447, 448), 468, 406, 521, 510, 198
UV = {130: 'S6.u', 133: 'S6.v', 141: 'S5.u', 144: 'S5.v', 280: 'S8a.u', 283: 'S8a.v', 288: 'S8b.u', 286: 'S8b.v'}
BODY = {5946: 0.06480327248573303, 5949: 0.23839756846427917, 5952: 0.31854677200317383}
SPEC_TINT_R = 0.3126390874385834


def sat(x):
    return max(0.0, min(1.0, x))


def unit(v):
    length = math.sqrt(sum(x * x for x in v))
    return [x / length for x in v]


def cross(p, q):
    return [p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0]]


def dot(p, q):
    return sum(x * y for x, y in zip(p, q))


def closed_form(inp, rec, fade_slope, fade_offset, s):
    """Material-only terms with the engine inputs frozen (see the doc). ``s`` maps (sample offset, channel)."""
    n = unit([inp['attribute2.%d' % k] for k in range(3)])
    v = unit([inp['attribute4.%d' % k] for k in range(3)])
    t = unit([inp['attribute3.%d' % k] for k in range(3)])
    sign = (inp['attribute3.3'] > 0) - (inp['attribute3.3'] < 0)
    nv = dot(n, v)
    front = 1.0 if inp['ps.FRONT_FACE'] else -1.0
    rim = sat(4.0 * (1.0 - sat(front * nv)) ** 2)
    fade = sat(inp['ps.POS_W'] * fade_slope + fade_offset)
    m = sat(0.5 + rim) * (1 - fade) + 0.741 * fade
    r5, r6, a7 = s[(368, 0)], s[(336, 0)], s[(556, 3)]
    alpha_test = sat(a7 * (1 - (1 - m) * (1 - r5) * (1 - r6)))
    strength = inp['CB0[95]'] * (1 - fade)
    x, y = 2 * strength * (s[(832, 0)] - s[(840, 0)]), 2 * strength * (s[(832, 1)] - s[(840, 1)])
    w = 1 - x * x - y * y
    b = unit([sign * q for q in cross(n, t)])
    normal = unit([n[i] * w + b[i] * abs(y) + t[i] * abs(x) for i in range(3)])
    a0 = 2.0 ** (-11 * math.sqrt(sat(max(0.0, inp['CB2[0]']))))
    width = inp['CB0[139]'] * inp['ps.POS_W'] * inp['attribute1.2'] / max(0.1, nv)
    f0 = 1 + rim ** 10
    time = inp['CB0[99]']
    wobble = math.cos(0.1 * time)
    u0, v0 = inp['attribute0.0'], inp['attribute0.1']
    gx = rec[0] - (inp['CB0[28]'] - inp['attribute4.0'])
    gz = rec[2] - (inp['CB0[30]'] - inp['attribute4.2'])
    return dict(
        alpha=sat(alpha_test * inp['attribute0.3']), alpha_test=alpha_test, normal=normal,
        a2=1 - (1 - a0) * (2 - 1 / max(0.5, 1 - 0.25 * width * width)), f0=f0, f90=sat(60 * f0),
        n_dot_v=sat(max(0.0, dot(normal, v))),
        level=0.1 * (rec[1] - (inp['CB0[29]'] - inp['attribute4.1']) + 1.6 * inp['CB2[1]'] - 1.0),
        uv={'S6.u': 0.5 * u0 + wobble + 0.03 * time, 'S6.v': 0.5 * v0 + wobble + 0.05 * time,
            'S5.u': u0 + wobble + 0.04 * time, 'S5.v': v0 + wobble + 0.06 * time,
            'S8a.u': 0.4 * gx - 0.1 * time, 'S8a.v': 0.4666667 * gz + 0.025 * time,
            'S8b.u': gx * 0.3333333 + 0.1 * time, 'S8b.v': gz * 0.3333333 - 0.05 * time})


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class ArchiveProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ.get('RC2016_GAME_ROOT', os.environ['RC2016_RESEARCH']))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import asset_bytes, open_archive
        from rc2016.research import forward_loops
        from rc2016.shaders import translator
        cls.archive = open_archive(root)
        cls.graph = asset_bytes(cls.archive, GRAPH)
        cls.material = asset_bytes(cls.archive, MATERIAL)
        cls.programs = {p['shader_id']: p for p in translator.pixel_programs(cls.graph)}
        cls.compiler = forward_loops.compile_opaque(cls.programs['0xf0f45d78'])

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def evaluate(self, root, inp, samples, values, memo):
        from rc2016.shaders.ir import compute
        nodes = self.compiler.ir.nodes

        def visit(i):
            if i in memo:
                return memo[i]
            node = nodes[i]
            op = node['op']
            if op == 'const':
                value = node['value']
            elif op == 'input':
                value = inp[node['name']]
            elif op == 'unsupported':
                value = samples[(node['sample_offset'], node['channel_guess'])] if 'sample_offset' in node else values[i]
            else:
                value = compute(op, [visit(j) for j in node['args']])
            memo[i] = value
            return value
        return visit(root)

    def test_identities(self):
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), GRAPH_SHA)
        for shader_id, digest in CODE_SHA.items():
            self.assertEqual(self.programs[shader_id]['code_sha256'], digest)
        self.assertEqual(list(self.programs), ['0xf0f45d78', '0x11408de9', '0x09be55f4', '0x18db7530'])
        self.assertEqual([(l['target'], l['branch']) for l in self.compiler.loops], [(1700, 4344), (12456, 12556)])
        nodes = self.compiler.ir.nodes
        self.assertEqual(self.compiler.exports[0][3], 6027)
        self.assertEqual({i: next(nodes[a]['value'] for a in nodes[i]['args'] if nodes[a]['op'] == 'const') for i in BODY},
                         BODY)
        self.assertEqual(next(nodes[a]['value'] for a in nodes[5212]['args'] if nodes[a]['op'] == 'const'), SPEC_TINT_R)

    def test_material_is_all_graph_defaults(self):
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.pipeline import render_state
        from rc2016.shaders.bindings import pass_contracts
        self.assertEqual(extract_material_parameters(self.material)['status'], 'no_parameter_sections')
        contract = pass_contracts(self.graph)[0]
        self.assertEqual({e['key_hash']: contract['defaults'][e['key_hash']] for e in contract['table']},
                         {0x24F4CA4C: 1.0, 0x66583BF6: 1.0})
        self.assertEqual([e['key_hash'] for e in contract['table']], [0x24F4CA4C, 0x66583BF6])      # CB2[0], CB2[1]
        self.assertEqual({r['path'] for r in contract['resources'][:3]},
                         {'textures/equipment/gadget/gdgt_hydrodisplacer/hydrodisplacer_water_cma.texture'})
        self.assertEqual(render_state.material_flags(data=self.material), 0x23C)       # opaque, two-sided

    def test_material_only_terms_match_the_program_numerically(self):
        from rc2016.shaders.ir import from_bits
        compiler = self.compiler
        rng = random.Random(20261023)
        worst = {}

        def note(key, got, want):
            worst[key] = max(worst.get(key, 0.0), abs(got - want))
        for trial in range(40):
            inp = {'CB0[135]': 0.0, 'CB0[95]': rng.uniform(0, 1.5), 'CB0[139]': rng.uniform(0, 0.01) if trial % 2 else 0.0,
                   'CB0[29]': rng.uniform(-5, 5), 'CB0[28]': rng.uniform(-5, 5), 'CB0[30]': rng.uniform(-5, 5),
                   'CB0[99]': rng.uniform(0, 400), 'CB2[0]': rng.uniform(0, 1.2), 'CB2[1]': rng.uniform(0, 1.2),
                   'ps.POS_W': rng.uniform(0.01, 1), 'ps.FRONT_FACE': float(rng.choice([0, 1])),
                   'attribute3.3': rng.choice([-1.0, 1.0]), 'attribute0.0': rng.uniform(0, 1),
                   'attribute0.1': rng.uniform(0, 1), 'attribute0.3': rng.uniform(0.2, 1),
                   'attribute1.2': rng.uniform(0, 2) if trial % 3 else 0.0}
            inp.update({'attribute%d.%d' % (a, k): rng.uniform(-1, 1) for a in (2, 3, 4) for k in range(3)})
            rec = [rng.uniform(-3, 3) for _ in range(3)]
            slope, offset = (0.0, 0.0) if trial % 4 else (rng.uniform(-1, 1), rng.uniform(0, 1))
            samples = {key: rng.uniform(0, 1) for key in
                       ((336, 0), (368, 0), (556, 3), (696, 0), (832, 0), (832, 1), (840, 0), (840, 1))}
            values = {105: rec[0], 106: rec[1], 107: rec[2], 108: slope, 109: offset, 110: from_bits(LIT_FLAGS),
                      5982: 0.0, 6008: 0.0, 6019: 0.0}
            memo = {}

            def ev(node):
                return self.evaluate(node, inp, samples, values, memo)
            want = closed_form(inp, rec, slope, offset, samples)
            note('alpha', ev(compiler.exports[0][3]), want['alpha'])
            note('level', ev(LEVEL), want['level'])
            for k in range(3):
                note('normal', ev(NORMAL[k]), want['normal'][k])
            note('a2', ev(A2), want['a2'])
            note('f0', ev(F0), want['f0'])
            note('f90', ev(F90), want['f90'])
            note('n_dot_v', ev(N_DOT_V), want['n_dot_v'])
            for node, name in UV.items():
                note('uv', ev(node), want['uv'][name])
        for key, error in worst.items():
            self.assertLess(error, 5e-6 if key == 'uv' else 5e-7, key)      # float32 literals against the double form

    def test_alpha_test_threshold_and_fog_mix(self):
        """The exec mask discards a fragment whose alpha before the opacity product is below attribute0.2; the final
        colour is t * lit + (1 - t) * CB0[112 + c]."""
        ir = self.compiler.ir
        nodes = ir.nodes
        mask = nodes[self.compiler.export_exec[0]]
        self.assertEqual(mask['op'], 'sub')
        lt = nodes[mask['args'][1]]
        self.assertEqual(lt['op'], 'lt')
        self.assertEqual(nodes[lt['args'][1]]['name'], 'attribute0.2')
        self.assertEqual(lt['args'][0], 249)
        for channel, (fog, (lit, tint)) in enumerate(zip((5959, 5961, 5963), ((5946, 5212), (5949, 5196), (5952, 5198)))):
            lit_t, fog_name = nodes[fog]['args']
            self.assertEqual(nodes[fog_name]['name'], 'CB0[%d]' % (112 + channel))
            self.assertEqual((nodes[lit_t]['op'], 5935 in nodes[lit_t]['args']), ('mul', True))       # t * lit
            self.assertEqual(nodes[lit]['op'], 'mul')                                              # diffuse light x body
        self.assertEqual(nodes[5212]['op'], 'mul')                                                 # red specular x 0.3126


if __name__ == '__main__':
    unittest.main()
