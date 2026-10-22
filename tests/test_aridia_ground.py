"""Aridia ground: rp_ari_gnd_sand_mud_blend (pass-0 writer 8A581B9D) and rp_ari_gnd_sludge_01 (forward program CC985D80).

The rule tests use synthetic data. The archive tests read the game archive (RC2016_RESEARCH) and skip without it.
"""
import math
import os
import random
import unittest
from pathlib import Path

from rc2016.viewer import textures as tp

MUD_GRAPH = 'materialgraph/environment/pokitaru/rock/pok_rock_blend_01/rp_ari_sand_mud_blend_colorbreak_01.materialgraph'
SLUDGE_GRAPH = 'materialgraph/environment/kalebo/rp_ari_sludge_puddle.materialgraph'
MUD_MATERIAL = 'material/environment/novalis/ground/nov_gnd_cave_floor_blend_01/rp_ari_gnd_sand_mud_blend.material'
SLUDGE_MATERIAL = 'material/environment/aridia/ground/ari_gnd_Sludge_01/rp_ari_gnd_Sludge_01.material'
MUD_CODE = '8a581b9d5570d076f86552fbbf7dd930f487ff9441b61bf03980400c6f7c7ae5'
SLUDGE_CODE = 'cc985d804dd6c6b2bb3e5f696482dd1525cc28c441910acbf8e17d2792b3039c'
# Pass-0 CB2 of the two materials, in serialized order (bindings.pass_contracts / _parameter_values).
MUD_CB2 = [4.0, 0.5, 1.5, 10.0, 0.8, 0.8, 1.0, 0.5, 10.0, 0.8, 2.0, 0.5]
SLUDGE_CB2 = [0.213, 0.666, 3.0, -0.1, -0.06, 5.0, 0.45, 1.0, 0.75, -0.5, -0.005, 0.02, 180.0, 0.35, 0.02, 0.035, 75.0, 1.5, -0.55]


class AridiaGroundRuleTests(unittest.TestCase):
    def test_rules_need_the_exact_graph_hash(self):
        rule = tp._graph_rule([MUD_GRAPH], {MUD_GRAPH: tp.MUD_BLEND_GRAPH_SHA256})
        self.assertIs(rule['mud_blend'], True)
        self.assertEqual((rule['graph_path'], rule['graph_sha256']), (MUD_GRAPH, tp.MUD_BLEND_GRAPH_SHA256))
        self.assertEqual(tp._graph_rule([MUD_GRAPH], {MUD_GRAPH: '0' * 64}), {})
        rule = tp._graph_rule([SLUDGE_GRAPH], {SLUDGE_GRAPH: tp.SLUDGE_PUDDLE_GRAPH_SHA256})
        self.assertEqual(rule['puddles'], 'sludge')
        self.assertEqual(tp._graph_rule([SLUDGE_GRAPH], {SLUDGE_GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([SLUDGE_GRAPH]), {})

    def test_term_is_registered_for_the_blender_front_end(self):
        from rc2016.formats import materials
        self.assertIn('mud_blend', materials.VIEWER_RULE_EXTRAS)
        self.assertIn('mud_blend', materials.VIEWER_RULE_BUILT)
        self.assertIn('mud_blend', materials.VIEWER_LINEAR_Z_TERMS)
        self.assertEqual(materials.VIEWER_TERM_ROLES['mud_blend'], ('_mud_h', '_mud_c', '_mud_g2', '_mud_n7', '_mud_n8'))

    def test_camera_collision_proxies_are_hidden_by_default(self):
        from rc2016.levels.scene import hidden_by_default
        self.assertTrue(hidden_by_default('environment/aridia/ground/tj_ari_gnd_Sludge_camera_coll/tj_ari_gnd_Sludge_camera_coll.model'))
        self.assertFalse(hidden_by_default('environment/aridia/ground/tj_ari_gnd_Sludge_main/tj_ari_gnd_Sludge_main.model'))


def smooth(api, u, v, channel):
    """A smooth stand-in image: any coordinate error shows in the sampled value."""
    return 0.5 + 0.45 * math.sin(2.1 * u + 1.7 * v + 0.9 * api + 1.3 * channel + 0.4)


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def sat(x):
    return min(1.0, max(0.0, x))


def frame_normal(inputs, rg, z=None):
    n, t = unit(inputs['n']), unit(inputs['t'])
    hand = (inputs['hand'] > 0) - (inputs['hand'] < 0)
    cross = [n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0]]
    b = unit([c * hand for c in cross])
    sigma = 1.0 if inputs['front'] else -1.0
    x, y = 2 * rg[0] - 1, 2 * rg[1] - 1
    z = 1 - x * x - y * y if z is None else z
    v = [n[i] * z + x * t[i] + y * b[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in v))
    return [sigma * c / length for c in v]


def mud_kernel(inputs, tex=smooth):
    """The decoded surface of writer 8A581B9D: albedo, F0, gloss and the encoded normal."""
    cb = MUD_CB2
    u0, v0 = inputs['uv']
    a = inputs['alpha']                                   # attribute 4.w, the squared vertex alpha
    wx, _, wz = inputs['wp']
    m = 1.0 - sat((1.0 - a) * (6.0 - 5.0 * tex(5, u0, v0, 0)))
    target = m ** cb[6] if m > 0.0 else 0.0
    w = a + cb[1] * (target - a)
    mud = [tex(11, cb[7] * u0, cb[7] * v0, ch) for ch in range(3)]
    sand = [tex(12, wx / cb[8], wz / cb[8], ch) for ch in range(3)]
    albedo = [(1.0 - cb[5]) * mud[ch] + w * (sand[ch] - (1.0 - cb[5]) * mud[ch]) for ch in range(3)]
    mud_g = [tex(6, u0, v0, ch) for ch in (0, 1)]
    sand_g = [tex(9, cb[4] * u0, cb[4] * v0, ch) for ch in (0, 1)]
    mud_f0 = cb[3] * mud_g[1] * sat(0.3 + 0.3 * cb[11])
    f0 = mud_f0 + w * (sand_g[1] - mud_f0)
    mud_gloss = cb[0] * mud_g[0]
    gloss = math.sqrt(sat(mud_gloss + w * (sand_g[0] - mud_gloss)))
    mud_n = frame_normal(inputs, [tex(10, cb[7] * u0, cb[7] * v0, ch) for ch in (0, 1)])
    # the sand normal: grain (API7, UV0 x CB2[10]) and sand (API8, UV0 x CB2[9]) maps combined with strength CB2[2]
    g1 = [2 * tex(7, cb[10] * u0, cb[10] * v0, ch) - 1 for ch in (0, 1)]
    g2 = [2 * tex(8, cb[9] * u0, cb[9] * v0, ch) - 1 for ch in (0, 1)]
    z1, z2 = 1 - g1[0] ** 2 - g1[1] ** 2, 1 - g2[0] ** 2 - g2[1] ** 2
    combined = [g1[0] * z2 + cb[2] * z1 * g2[0], g1[1] * z2 + cb[2] * z1 * g2[1], z1 * z2]
    # the writer applies the front-face sign to the mud normal only (no sign factor on the sand branch)
    sand_n = frame_normal(dict(inputs, front=True), [0.5 * combined[0] + 0.5, 0.5 * combined[1] + 0.5], z=combined[2])
    normal = [mud_n[i] + w * (sand_n[i] - mud_n[i]) for i in range(3)]
    return albedo, f0, gloss, [0.5 * c + 0.5 for c in normal]


def sludge_kernel(noise, g, cb=SLUDGE_CB2):
    """The decoded material terms of CC985D80 before its light-list loop: F0 and the gloss input h (the program reads
    the _g sample once for both channels at this point, so one value stands in for R and G)."""
    m = sat((noise - cb[8]) / (cb[6] - cb[8]))
    rw = sat((m - 0.5 * cb[6]) / (1.0 - cb[6]))
    r = sat((noise - cb[9]) / (cb[1] - cb[9]))
    b = r + cb[18] * (1.0 - 2.0 * r)
    hin = b + cb[7] * g * (1.0 - b)
    h = math.sqrt(sat(cb[13] * hin + rw * (hin - cb[13] * hin)))
    f0_raw = g * sat(0.3 + cb[5] * m)
    return cb[14] * f0_raw + rw * (f0_raw - cb[14] * f0_raw), h


class AridiaGroundArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_RESEARCH')
        if not research:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        from rc2016.levels.scene import asset_bytes, open_archive
        from rc2016.shaders.translator import compile_graph
        try:
            cls.archive = open_archive(Path(research))
            cls.mud_graph = bytes(asset_bytes(cls.archive, MUD_GRAPH))
            cls.sludge_graph = bytes(asset_bytes(cls.archive, SLUDGE_GRAPH))
        except (OSError, ValueError):
            raise unittest.SkipTest('Source archive not available')
        cls.mud = next(p for p in compile_graph(cls.mud_graph, allow_independent_samplers=True)['programs'] if p['code_sha256'] == MUD_CODE)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_graph_hashes(self):
        import hashlib
        self.assertEqual(hashlib.sha256(self.mud_graph).hexdigest(), tp.MUD_BLEND_GRAPH_SHA256)
        self.assertEqual(hashlib.sha256(self.sludge_graph).hexdigest(), tp.SLUDGE_PUDDLE_GRAPH_SHA256)

    def test_mud_reading_binds_the_decoded_slots_and_constants(self):
        paths = tp.material_texture_paths(self.archive, MUD_MATERIAL)
        base = 'textures/environment/aridia/'
        expected = dict(_n='rp_ari_mud_bank_n', _g='rp_ari_mud_bank_g', _c='rp_ari_sand_color_breakup_c',
                        _mud_h='rp_ari_sand_mud_mask_h', _mud_c='rp_ari_sludge_c', _mud_g2='rp_ari_sand_g')
        for role, name in expected.items():
            self.assertEqual(paths[role], base + name + '.texture')
        mb = paths['mud_blend']
        for key, value in (('gloss_scale', 4.0), ('mask_strength', 0.5), ('f0_scale', 10.0), ('sand_uv_scale', 0.8),
                           ('mud_darken', 0.8), ('sharpness', 1.0), ('mud_uv_scale', 0.5), ('world_tile', 10.0), ('f0_bias', 0.5)):
            self.assertAlmostEqual(mb[key], value, places=6)
        self.assertEqual((paths['g_mode'], paths['k'], paths['spec'], paths['gloss']), ('grey', 0.0, 1.0, 1.0))
        self.assertEqual(paths['graph_identity']['sha256'], tp.MUD_BLEND_GRAPH_SHA256)

    def test_sludge_reading_binds_the_decoded_slots_and_constants(self):
        paths = tp.material_texture_paths(self.archive, SLUDGE_MATERIAL)
        base = 'textures/environment/aridia/'
        for role, name in dict(_c='rp_ari_sludge_c', _g='rp_ari_sludge_g', _n='rp_ari_sludge_n', _puddle_ripple='rp_ari_ripple_n').items():
            self.assertEqual(paths[role], base + name + '.texture')
        self.assertTrue(paths['_puddle_noise'].endswith('fractalnoise_rollingsmoke_1channel.texture'))
        puddles = paths['puddles']
        self.assertEqual((puddles['variant'], puddles['writer']), ('sludge', 'CC985D80'))
        for key, value in (('uv_scale', 3.0), ('dry_gloss', 0.35), ('dry_f0', 0.02)):
            self.assertAlmostEqual(puddles[key], value, places=6)
        # keys of the GroundPuddles parameter order: p0 0.75, p6 0.45, p9 -0.55, p10 0.035, p11 1.5, p13 0.02
        for index, value in ((0, 0.75), (6, 0.45), (9, -0.55), (10, 0.035), (11, 1.5), (13, 0.02)):
            self.assertAlmostEqual(puddles['params'][index], value, places=6)
        self.assertEqual(paths['graph_identity']['sha256'], tp.SLUDGE_PUDDLE_GRAPH_SHA256)

    def test_pass_zero_constants_are_the_pinned_cb2(self):
        from rc2016.formats.archive import asset_data
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.shaders.bindings import _parameter_values, pass_contracts
        for graph, material, expected in ((self.mud_graph, MUD_MATERIAL, MUD_CB2), (self.sludge_graph, SLUDGE_MATERIAL, SLUDGE_CB2)):
            contract = pass_contracts(graph)[0]
            instance = extract_material_parameters(asset_data(self.archive, self.archive.resolve(material)[0]))
            values, record = _parameter_values(contract, instance)
            got = {row['dword']: row['values'][0] for row in record}
            for index, value in enumerate(expected):
                self.assertAlmostEqual(got[index], value, places=5)

    def test_sludge_deferred_writers_are_black_and_pass_zero_is_the_forward_program(self):
        from rc2016.shaders.translator import compile_graph
        programs = {p['code_sha256']: p for p in compile_graph(self.sludge_graph, allow_independent_samplers=True)['programs']}
        forward = programs[SLUDGE_CODE]
        self.assertEqual(forward['status'], 'unsupported')
        self.assertIn('forward-pass program', forward['reason'])
        from rc2016.shaders.ir import DAG
        from rc2016.shaders.translator import lower_deferred
        zero = [p for p in programs.values() if p['status'] == 'compiled']
        self.assertEqual(len(zero), 2)
        for program in zero:
            lowered = lower_deferred(program['ir'])
            ir, outputs = DAG(lowered['nodes']), lowered['outputs']
            for root in (*outputs['albedo'], *outputs['F0'], outputs['gloss'], outputs['emission']):
                self.assertEqual(ir.evaluate(root), 0.0)

    def test_mud_writer_matches_the_decoded_kernel(self):
        from rc2016.shaders.ir import DAG
        from rc2016.shaders.translator import lower_deferred
        lowered = lower_deferred(self.mud['ir'])
        ir = DAG(lowered['nodes'])
        subs = {i: ir.const(0.0) for i, node in enumerate(ir.nodes) if node['op'] == 'unsupported'}   # strength fade: close range
        subs.update({i: ir.const(MUD_CB2[int(node['name'][4:-1])]) for i, node in list(enumerate(ir.nodes))
                     if node['op'] == 'input' and node['name'].startswith('CB2[')})
        out, normal = lowered['outputs'], self.mud['ir']['exports']['0']
        roots = ir.replace([*out['albedo'], out['F0'][0], out['gloss'], *normal[:3]], subs)
        cb2_inputs = {'CB2[%d]' % k: v for k, v in enumerate(MUD_CB2)}
        rng = random.Random(9)
        worst = 0.0
        for case in range(300):
            wp = [rng.uniform(-400, 400), rng.uniform(5, 160), rng.uniform(-400, 400)]
            case_in = dict(n=[rng.uniform(-1, 1) for _ in range(3)], t=[rng.uniform(-1, 1) for _ in range(3)],
                           hand=rng.choice((-1.0, 1.0)), front=case % 3 != 0, wp=wp,
                           uv=(rng.uniform(-2, 2), rng.uniform(-2, 2)), alpha=rng.uniform(0.83, 1.0))   # keeps m above 0: the IR evaluates log2(m) eagerly
            inputs = {'attribute2.%d' % i: case_in['n'][i] for i in range(3)}
            inputs.update({'attribute3.%d' % i: case_in['t'][i] for i in range(3)})
            inputs.update({'attribute3.3': case_in['hand'], 'attribute4.3': case_in['alpha'], 'attribute0.0': case_in['uv'][0],
                           'attribute0.1': case_in['uv'][1], 'ps.FRONT_FACE': 1.0 if case_in['front'] else 0.0,
                           'ps.POS_W': 1.0, 'ps.POS_X': 0.0, 'ps.POS_Y': 0.0, 'CB0[95]': 1.0, 'CB0[99]': 0.0,
                           'CB0[139]': 0.0, 'attribute1.2': 1.0})
            for k in range(92, 108):
                inputs.setdefault('CB0[%d]' % k, 0.0)
            ray = (0.3, 0.5, 0.7)   # camera ray at the origin pixel
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
            albedo, f0, gloss, encoded = mud_kernel(case_in)
            for a, b in zip(got, [*albedo, f0, gloss, *encoded]):
                worst = max(worst, abs(a - b))
        self.assertLess(worst, 1e-5)

    def test_sludge_forward_front_end_matches_the_decoded_terms(self):
        """Run the CC985D80 program up to its first light-list loop and evaluate the F0 and gloss-input registers."""
        from rc2016.shaders import translator
        program = next(p for p in translator.pixel_programs(self.sludge_graph) if p['code_sha256'] == SLUDGE_CODE)

        def compiler():
            return translator.Compiler(program['code'], program['usage_slots'], program.get('ps_input_addr'),
                                       program.get('ps_input_ena'), program.get('vgpr_count'), allow_independent_samplers=True)

        class Stop(Exception):
            pass

        def run(c, stop_at=None):
            instructions = translator.decoder().decode_stream(c.code, 0)
            c.instructions = instructions
            n = 0
            while n < len(instructions):
                i = instructions[n]
                c.position = n
                if stop_at is not None and i['relative'] == stop_at and c.ir.value(c.bp) != 0:
                    raise Stop()
                if i['relative'] in c.pending:
                    c.bp = c.p_or(c.bp, c.pending.pop(i['relative']))
                if c.ir.value(c.bp) == 0:
                    n += 1
                    continue
                c.step(i)
                if c.back is not None:
                    target, _taken = c.back
                    c.back = None
                    if stop_at is None:
                        return target
                n += 1
            return None

        target = run(compiler())
        self.assertIsNotNone(target)
        c = compiler()
        with self.assertRaises(Stop):
            run(c, stop_at=target)
        ir = c.ir
        unsupported = {offset: i for i, node in enumerate(ir.nodes) if node['op'] == 'unsupported'
                       for offset in [node.get('offset')] if 'indirect texture/sampler' in node.get('reason', '')}
        self.assertIn(960, unsupported)      # _g at UV0
        self.assertIn(1276, unsupported)     # noise at CB2[15] x (camera - position).xz
        rng = random.Random(3)
        worst = 0.0
        for _ in range(200):
            noise, g = rng.random(), rng.random()
            subs = {i: ir.const(0.0) for i, node in enumerate(ir.nodes) if node['op'] == 'unsupported'}
            subs[unsupported[960]], subs[unsupported[1276]] = ir.const(g), ir.const(noise)
            subs.update({i: ir.const(SLUDGE_CB2[int(node['name'][4:-1])]) for i, node in list(enumerate(ir.nodes))
                         if node['op'] == 'input' and node['name'].startswith('CB2[') and int(node['name'][4:-1]) < len(SLUDGE_CB2)})
            f0_root, q_root = ir.replace([c.v[73], c.v[72]], subs)
            inputs = {'CB0[139]': 0.0, 'ps.POS_W': 1.0, 'attribute1.2': 1.0, 'attribute2.0': 0.2, 'attribute2.1': 0.9,
                      'attribute2.2': 0.1, 'CB0[95]': 1.0, 'CB0[99]': 0.0}
            for node in ir.nodes:
                if node['op'] == 'input':
                    inputs.setdefault(node['name'], 0.5)
            f0, q = ir.evaluate(f0_root, inputs), ir.evaluate(q_root, inputs)
            want_f0, want_h = sludge_kernel(noise, g)
            worst = max(worst, abs(f0 - want_f0), abs(q - want_h))
        self.assertLess(worst, 1e-4)


if __name__ == '__main__':
    unittest.main()
