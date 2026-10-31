"""Thumper glove glass forward program 5226F404 (docs/material-decoding/thumper-glove-glass.md).

Synthetic checks (rule binding, uniform packing, GLSL text, validator constants) always run. The archive-backed
checks pin the program identity, run the translator's loop-opaque research mode on it and compare every
material-only term numerically with the closed forms restated below, independently of the GLSL; they skip
without RC2016_RESEARCH / RC2016_GAME_ROOT.
"""
import hashlib
import math
import os
import random
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2016.formats.links import EXACT, rule_coverage
from rc2016.pipeline.render_state import material_flags
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms

GRAPH = 'materialgraph/equipment/weapon/wpn_thumper_glove_glass/wpn_thumper_glove_glass.materialgraph'
MATERIAL = 'material/equipment/weapon/wpn_thumper_glove_glass/wpn_thumper_glove_glass.material'
BASIC = 'materialgraph/global/basic_glass/basic_glass.materialgraph'
GRAPH_SHA = 'ffec1bf59b2567ad404a016412708a6df957f004250cf276d9c13e725743ad98'
CODE_SHA = {'0x5226f404': '97ce23dda2bf338d51c221ebe95014559258bc393b7dbb63833b436996a26b57',
            '0x1d429528': '1743246ac6338dfda0cfd3ae4d19502528dadd357c623259d100c8b3e518b0ac'}
BASIC_CODE_SHA = '07922b28af5b6d5690a881dca26d0ff1563a27efeb74f8d4b674ce4f3c0b65e4'
PATTERNS = 'textures/character/shared/patterns_cma.texture'
CAPTURED_AMPLITUDE = struct.unpack('<f', struct.pack('<I', 0x3D36DB3F))[0]   # CB0[115] of the captured Novalis frame
NOISE = 'textures/visualeffect/noise/fx_noisetile02.texture'


def sat(x):
    return max(0.0, min(1.0, x))


def closed_form(inp, s):
    """Material-only terms of 5226F404 with the engine inputs frozen: instance fade 0, CB0[95] = 1, CB0[99] = 0,
    attribute1.2 = 0 (no geometric AA), lit instance flags. ``s`` maps (API, channel) to a texture sample."""
    p3, p8, gloss, strength, p9 = inp['CB2[3]'], inp['CB2[8]'], inp['CB2[9]'], inp['CB2[10]'], inp['CB2[11]']
    body = [inp['CB2[0]'], inp['CB2[1]'], inp['CB2[2]']]
    tint = [inp['CB2[4]'], inp['CB2[5]'], inp['CB2[6]']]

    def unit(v):
        length = math.sqrt(sum(x * x for x in v))
        return [x / length for x in v]
    n = unit([inp['attribute2.%d' % k] for k in range(3)])
    t = unit([inp['attribute3.%d' % k] for k in range(3)])
    sign = (inp['attribute3.3'] > 0) - (inp['attribute3.3'] < 0)
    b = unit([sign * (n[1] * t[2] - t[1] * n[2]), sign * (n[2] * t[0] - t[2] * n[0]), sign * (n[0] * t[1] - t[0] * n[1])])
    p = unit([inp['attribute4.%d' % k] for k in range(3)])
    front = 1.0 if inp['ps.FRONT_FACE'] else -1.0
    k = sat(max(0.0, strength))
    x, y = k * (2 * s[(8, 0)] - 1), k * (2 * s[(8, 1)] - 1)
    w = 1 - x * x - y * y                                  # linear z weight, not sqrt
    raw = [n[i] * w + x * t[i] + y * b[i] for i in range(3)]
    length = math.sqrt(sum(v * v for v in raw))
    rim = sat(4.0 * abs(1.0 - sat(front * sum(p[i] * n[i] for i in range(3)))) ** p9)
    noise = sat(0.45454546 * (s[(5, 0)] + s[(6, 0)]) - 0.36363637)
    pattern = s[(7, 3)]
    f0 = [p8 * body[i] * pattern + rim ** 10 for i in range(3)]
    luma = 0.2126 * tint[0] + 0.7152 * tint[1] + 0.0722 * tint[2]
    return dict(alpha=sat(sat(noise + sat(p3 + rim)) * inp['attribute0.3']), noise=noise,
                normal=[front * v / length for v in raw], f0=f0, f90=[sat(60 * v) for v in f0],
                a2=2.0 ** (-11 * math.sqrt(sat(gloss * body[0] * pattern))), tint_k=[sat(c / luma) for c in tint])


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hash_binds_the_rule(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('glass_thumper', '5226F404', GRAPH_SHA))
        self.assertEqual(rule['slots'], {'_n': 0x49A7866B7AE34E7A, '_pick': 0x49A7866B0056CBD0})
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})
        self.assertEqual(tp._graph_rule([GRAPH.replace('.materialgraph', '_copy.materialgraph')], {}), {})
        self.assertEqual(rule_coverage([GRAPH])[0] == EXACT, rule_coverage([GRAPH])[0] == EXACT)   # coverage is reported, not required

    def test_kind_and_uniform_packing(self):
        self.assertEqual(GLASS_KINDS['glass_thumper'], 11)
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))
        params = dict(body=(0.1, 0.2, 0.3), tint=(0.4, 0.5, 0.6), rim_bias=0.7, tiling=0.8, f0_base=0.9, gloss=1.1,
                      normal_strength=1.2, rim_exp=1.3, glow_colour=(0.39613863825798035, 2.0, 0.0), amplitude=0.5)
        kind, a, b, c, d, e = glass_uniforms(dict(kind='glass_thumper', params=params))
        self.assertEqual((kind, a, b, c, d, e), (11, (0.4, 0.5, 0.6, 0.7), (0.1, 0.2, 0.3, 1.2), (0.8, 0.9, 1.1, 1.3),
                                                  (0.39613863825798035, 2.0, 0.0, 0.5), (0.0,) * 4))

    def test_glsl_branch_carries_the_decoded_equations(self):
        branch = GLASS_FRAGMENT.split('uKind == 11')[1].split('uKind == 9')[0]
        for text in ('float rim = sat(4.0 * pow(om, uC.w))', 'texture2D(uTexN, vUV * uC.x)',
                     'vec2(0.5 * vUV.x, 0.5 * vUV.y)).r', 'vec2(0.5 * vUV.x, 0.5 * vUV.y + 0.5)).r',
                     'X = sat(0.45454546 * (nA + nB) - 0.36363637)', 'alpha = sat(X + sat(uA.w + rim))',
                     'vec2 xy = sat(uB.w) * (pat.rg * 2.0 - 1.0)', 'N * (1.0 - dot(xy, xy))',
                     'vec3 F0 = uC.y * uB.rgb * pat.a + vec3(pow(rim, 10.0))',
                     'pow(2.0, -5.5 * sqrt(sat(uC.z * uB.r * pat.a)))', 'clamp(uA.rgb / luma, 0.0, 1.0)',
                     'glassSpec(N, V, L1, L2, C1, C2, F0, a, ambient)', 'uD.rgb * uD.w * (Ld + vec3(X))'):
            self.assertIn(text, branch)

    def test_captured_draw_state_is_recorded(self):
        c = tp.THUMPER_GLOVE_GLASS_CONSTANTS
        self.assertEqual(struct.pack('<f', c['amplitude']), struct.pack('<I', 0x3D36DB3F))
        self.assertEqual((c['captured_amplitude'], c['amplitude_input'], c['game_equivalence']), (c['amplitude'], 'CB0 dword 115', False))
        self.assertEqual((c['frozen_inputs']['instance_flags'], c['frozen_inputs']['ssr']), (0x261, False))
        self.assertEqual(tp.THUMPER_GLOVE_GLASS_CAPTURED_BLEND, 'alpha_over')

    def test_blender_front_end_and_validator_agree(self):
        self.assertEqual(validation.GLASS_CONSTANTS['glass_thumper'],
                         ('tint', 'rim bias', 'body', 'tiling', 'f0 base', 'gloss', 'normal strength', 'rim exp', 'glow colour', 'amplitude'))
        params = dict(body=[0.1, 0.2, 0.3], tint=[0.4, 0.5, 0.6], rim_bias=0.7, tiling=0.8, f0_base=0.9, gloss=1.1,
                      normal_strength=1.2, rim_exp=1.3, glow_colour=(0.39613863825798035, 2.0, 0.0), amplitude=0.5)
        for blend, with_alpha in (('opaque', False), ('alpha_over', True)):
            for textured in (True, False):
                rule = dict(applies=True, ported=['glass_thumper'], terms={'glass_thumper': dict(kind='glass_thumper', params=params, blend=blend)},
                            textures={'_c': None, '_g': None, '_n': PATTERNS if textured else None, '_pick': NOISE if textured else None})
                names, roles = validation._viewer_required(rule, False)
                expected = validation._viewer_expected_constants(rule)
                self.assertEqual(set(names) <= set(expected), True)
                self.assertEqual('VR glass rim bias' in names, with_alpha)
                self.assertEqual('VR glass normal strength' in names, textured)
                self.assertEqual(roles, ['_n', '_pick'] if textured else [])
                self.assertEqual(expected['VR glass glow colour'], (0.39613863825798035, 2.0, 0.0))
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_glass')[1].split('def apply_viewer_graph_rule')[0]
        for name in ('glass tint', 'glass rim bias', 'glass body', 'glass tiling', 'glass f0 base', 'glass gloss', 'glass normal strength',
                     'glass rim exp', 'glass glow colour', 'glass amplitude', 'glass pattern map', 'glass noise map A', 'glass noise map B',
                     'VR glass glow'):
            self.assertIn(name, body)
        self.assertIn("'glass_thumper'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])


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
        cls.basic = {p['shader_id']: p for p in translator.pixel_programs(asset_bytes(cls.archive, BASIC))}

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_identities(self):
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), GRAPH_SHA)
        for shader_id, digest in CODE_SHA.items():
            self.assertEqual(self.programs[shader_id]['code_sha256'], digest)
        self.assertEqual(self.basic['0x4fc5703f']['code_sha256'], BASIC_CODE_SHA)
        self.assertEqual([p['shader_id'] for p in self.programs.values()][:3], ['0x5226f404', '0x11408de9', '0xdd9a126f'])

    def test_loop_body_is_the_one_of_basic_glass(self):
        import difflib
        from rc2016.research.forward_loops import loop_body_tokens
        a, b = loop_body_tokens(self.basic['0x4fc5703f']), loop_body_tokens(self.programs['0x5226f404'])
        names_a, names_b = [t[0] for t in a], [t[0] for t in b]
        matcher = difflib.SequenceMatcher(None, names_a, names_b, autojunk=False)
        edits = [(tag, names_a[i1:i2], names_b[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != 'equal']
        # B drops exactly one accumulator multiply-add (the blue diffuse channel, whose glow colour component is 0)
        self.assertEqual(edits, [('delete', ['GCN_V_MAC_F32'], [])])
        self.assertEqual((len(a), len(b)), (507, 506))
        mismatches = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                mismatches += [(a[i1 + k], b[j1 + k]) for k in range(i2 - i1) if a[i1 + k] != b[j1 + k]]
        # the two remaining differences swap VCC low and high as the sources of one multiply-add pair
        self.assertEqual(len(mismatches), 2)
        for first, second in mismatches:
            rest = lambda token: [c for c in token[1] if c not in (106, 107)]
            self.assertEqual((first[0], first[2], rest(first)), (second[0], second[2], rest(second)))
            self.assertEqual({first[1][0], second[1][0]}, {106, 107})

    def test_material_only_terms_match_the_program_numerically(self):
        from rc2016.research import forward_loops as loops
        compiler = loops.compile_opaque(self.programs['0x5226f404'])
        self.assertEqual([(l['target'], l['branch']) for l in compiler.loops], [(1528, 4176), (12364, 12464)])
        roots, subs = loops.bind_frozen(compiler)
        head = loops.loop_head(compiler, subs)
        ir = compiler.ir
        rng = random.Random(20261016)
        worst = {}
        for _ in range(60):
            inp = {'CB2[%d]' % k: rng.uniform(0.05, 1.0) for k in range(12)}
            inp.update({'CB2[3]': rng.uniform(0.0, 0.5), 'CB2[8]': rng.uniform(0.1, 3.0), 'CB2[10]': rng.uniform(0.0, 1.2),
                        'CB2[11]': rng.uniform(1.0, 8.0), 'CB0[95]': 1.0, 'attribute3.3': rng.choice([-1.0, 1.0]),
                        'attribute0.3': rng.uniform(0.3, 1.0), 'ps.FRONT_FACE': float(rng.choice([0, 1]))})
            inp.update({'attribute%d.%d' % (a, c): rng.uniform(-1, 1) for a in (2, 3, 4) for c in range(3)})
            samples = {(api, ch): rng.uniform(0, 1) for api in (5, 6, 7, 8) for ch in range(4)}
            want = closed_form(inp, samples)
            ev = lambda node: loops.evaluate(ir, node, inp, samples)
            got = dict(alpha=ev(roots[3]), normal=[ev(head[r]) for r in (70, 71, 72)], f0=[ev(head[r]) for r in (74, 75, 76)],
                       f90=[ev(head[r]) for r in (6, 5, 3)], a2=ev(head[44]))
            for key, value in got.items():
                values = value if isinstance(value, list) else [value]
                expected = want[key] if isinstance(want[key], list) else [want[key]]
                worst[key] = max(worst.get(key, 0.0), max(abs(x - y) for x, y in zip(values, expected)))
        # float32 literals (0.454545, -0.363636, 0.090909, 0.4) against the double closed form
        for key, error in worst.items():
            self.assertLess(error, 5e-7, key)

    def test_tint_and_glow_terms_of_the_final_colour(self):
        from rc2016.research import forward_loops as loops
        compiler = loops.compile_opaque(self.programs['0x5226f404'])
        roots, _ = loops.bind_frozen(compiler)
        ir, nodes = compiler.ir, compiler.ir.nodes
        rng = random.Random(7)
        inp = {'CB2[%d]' % k: rng.uniform(0.05, 1.0) for k in (4, 5, 6)}
        luma = 0.2126 * inp['CB2[4]'] + 0.7152 * inp['CB2[5]'] + 0.0722 * inp['CB2[6]']
        for k, name in enumerate(('CB2[4]', 'CB2[5]', 'CB2[6]')):
            tint = [i for i, n in enumerate(nodes) if n['op'] == 'sat' and nodes[n['args'][0]]['op'] == 'mul'
                    and any(nodes[a]['op'] == 'input' and nodes[a]['name'] == name for a in nodes[n['args'][0]]['args'])]
            self.assertTrue(tint, name)
            self.assertAlmostEqual(loops.evaluate(ir, tint[0], inp), sat(inp[name] / luma), places=6)
        reach = [ir.reachable([roots[c]]) for c in range(3)]
        amp = []
        for c in range(3):
            amp.append(sorted(nodes[a]['value'] for i in reach[c] if nodes[i]['op'] == 'mul'
                              for a, b in (nodes[i]['args'], nodes[i]['args'][::-1]) if nodes[a]['op'] == 'const'
                              and nodes[b]['op'] == 'input' and nodes[b]['name'] == 'CB0[115]'))
        self.assertEqual(amp, [[0.39613863825798035], [2.0], []])     # glow colour K = (0.396139, 2, 0) times CB0[115]
        noise = [i for i, n in enumerate(nodes) if n['op'] == 'sat' and nodes[n['args'][0]]['op'] == 'add'
                 and any(nodes[a]['op'] == 'const' and abs(nodes[a]['value'] + 0.3636363446712494) < 1e-9 for a in nodes[n['args'][0]]['args'])]
        self.assertEqual(len(noise), 1)
        self.assertEqual([noise[0] in reach[c] for c in range(3)], [True, True, False])   # X is added where K is non-zero

    def test_captured_pass_words_and_instance_flags(self):
        # Captured: CB0[135] = 0x40 (G-buffer and depth-equal passes) and 0x2 (blended pass); instance flags 0x261.
        from rc2016.research import forward_loops as loops
        compiler = loops.compile_opaque(self.programs['0x5226f404'])
        ir = compiler.ir
        reads = lambda node: {n for n in loops.dependencies(ir, node) if n.startswith('CB0')}
        alpha = {word: loops.bind_frozen(compiler, flags=0x261, pass_word=word)[0][3] for word in (0, 0x2, 0x40)}
        alpha['lit'] = loops.bind_frozen(compiler)[0][3]      # flags 0x2261, pass word 0: the closed-form inputs
        self.assertEqual(len(set(alpha.values())), 1)          # the alpha export depends on neither
        self.assertEqual(reads(alpha[0x2]), set())
        # instance bit 0x2000 gates the block that reads the projection rows and CB0[111] (screen-space reflection tail)
        lit, _ = loops.bind_frozen(compiler, flags=0x2261, pass_word=0x2)
        captured, _ = loops.bind_frozen(compiler, flags=0x261, pass_word=0x2)
        for c in range(3):
            self.assertIn('CB0[111]', reads(lit[c]))
            self.assertNotIn('CB0[111]', reads(captured[c]))
        self.assertEqual(['CB0[115]' in reads(captured[c]) for c in range(3)], [True, True, False])   # K.b = 0

    def test_second_program_is_the_variant_without_the_pattern_normal(self):
        from rc2016.research import forward_loops as loops
        from rc2016.shaders.bindings import pass_contracts
        contracts = pass_contracts(self.graph)
        self.assertEqual([len(c['resources']) for c in contracts], [4, 3])
        self.assertEqual([c['table'][0]['key_hash'] for c in contracts], [0x79120089, 0x4B4DAB19])
        self.assertNotIn(0xCC7457CD, [e['key_hash'] for e in contracts[1]['table']])
        usage = {p['shader_id']: p for p in self.programs.values()}
        from rc2016.research.forward_programs import forward_resource_usage
        first = forward_resource_usage(self.graph, '0x5226f404')['texture_apis']
        second = forward_resource_usage(self.graph, '0x1d429528')['texture_apis']
        self.assertEqual(sorted(a for a in first if 5 <= a < 32), [5, 6, 7, 8])
        self.assertEqual(sorted(a for a in second if 5 <= a < 32), [5, 6, 7])
        self.assertEqual(len(usage), 5)
        self.assertTrue(loops.loop_body_tokens(self.programs['0x1d429528']))

    def test_viewer_rule_from_the_archive(self):
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        forward = paths['forward']
        p = forward['params']
        # flags word 0x00200234 says opaque; the captured draw blends (SRC_ALPHA, ONE_MINUS_SRC_ALPHA), so the rule does too
        from rc2016.levels.scene import asset_bytes
        self.assertEqual(material_flags(data=asset_bytes(self.archive, MATERIAL)), 0x00200234)
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['two_sided'], forward['blend_source']),
                         ('glass_thumper', '5226F404', 'alpha_over', True, 'captured draw state'))
        self.assertEqual((paths['_n'], paths['_pick']), (PATTERNS, NOISE))
        self.assertEqual([round(x, 6) for x in p['body']], [0.371238, 0.708376, 0.107023])
        self.assertEqual([round(x, 6) for x in p['tint']], [0.502886, 0.768151, 0.138432])
        self.assertEqual((round(p['rim_bias'], 6), round(p['tiling'], 6), p['f0_base'], round(p['gloss'], 6), p['rim_exp']),
                         (0.272, 0.4, 3.0, 0.8, 7.0))
        self.assertEqual(p['normal_strength'], 0.25)                       # no instance record: the graph default
        self.assertFalse(p['game_equivalence'])
        self.assertEqual((p['amplitude'], p['captured_amplitude']), (CAPTURED_AMPLITUDE, CAPTURED_AMPLITUDE))
        self.assertEqual(p['frozen_inputs']['instance_flags'], 0x261)
        # a binding of either slot pair to two different textures is not the decoded input
        with patch.object(tp, 'texture_slots', return_value={0x49A7866B7AE34E7A: 'textures/other.texture'}):
            self.assertNotIn('forward', tp.material_texture_paths(self.archive, MATERIAL))


if __name__ == '__main__':
    unittest.main()
