"""Blarg turret energy barrier 19D0F1FA and targeting display 678C5C0E (docs/material-decoding/turret-barrier.md).

Synthetic checks (rule binding, uniform packing, GLSL text, validator constants) always run. The archive-backed checks pin the
program identities, evaluate the project's own GCN decode of both programs (rc2016.research.forward_loops, independent of the
shadPS4 IR the rules were first read from) against the closed forms restated below, and read the viewer rules from the
archive; they skip without RC2016_RESEARCH / RC2016_GAME_ROOT.
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

SHIELD_GRAPH = 'materialgraph/character/enemy/enm_blarg_turret_shield_lg/enm_blarg_turret_shield_lg.materialgraph'
SHIELD_MATERIAL = 'material/character/enemy/enm_Blarg_Turret_Shield_Lg/enm_Blarg_Turret_Shield_Lg.material'
SHIELD_SHA = 'ac43cf5fe059d5f4f9a4cfc00573a951e2422ecb045e1fed2178017ce5f075d0'
SHIELD_CODE_SHA = 'a8e4b42600762fe3d80721afe3cc40ac427cf96d78006580fe0a931e54ea6c0a'
HUD_GRAPH = 'materialgraph/visualeffect/equipment/gadget/gdgt_blarg_turret_hud/gdgt_blarg_turret_hud.materialgraph'
HUD_MATERIAL = 'material/visualeffect/equipment/gadget/gdgt_Blarg_Turret_HUD/gdgt_Blarg_Turret_HUD.material'
HUD_SHA = 'c295721478181b38b70797848e61846046563039dd5d903857d9415201ceafc4'
HUD_CODE_SHA = '4f89f7011c699b42e13cd0da6a6c2ca948961f65b5261aaf991dde1a5c755bc8'
SHIELD_TEXTURES = ('textures/visualeffect/noise/fx_noisetile03.texture', 'textures/visualeffect/noise/fx_noisetile02.texture',
                   'textures/visualeffect/pattern/hex_grid_02.texture',
                   'textures/character/enemy/enm_blarg_turret/enm_blarg_turret_energy_shield/enm_blarg_turret_shield_lg_v2.texture')
HUD_TEXTURES = ('textures/visualeffect/pattern/hex_grid_02.texture',
                'visualeffect/equipment/gadget/gdgt_blarg_turret_hud/gdgt_blarg_turret_hud.texture',
                'textures/visualeffect/noise/hydro_noise_cell_sm.texture')


def sat(x):
    return max(0.0, min(1.0, x))


def shield_closed_form(inp, s):
    """Exports of 19D0F1FA from the sample values s[(api, channel)] (APIs 5..9 = the graph's five resources, 8 and 9 one image):
    returns (colour components, alpha, coverage). See the uKind 18 comment in viewer/shading.py."""
    u, v, t = inp['attribute0.0'], inp['attribute0.1'], inp['CB0[99]']
    R = sat(math.sqrt(u * u + (v - 0.475) ** 2) ** 1.25)
    q1 = min(1.0, (3 * (R - 0.2 * t) % 1.0) ** 2) - 0.5
    D = sat(0.3 * abs(1.0 - s[(5, 0)] - s[(6, 0)]))
    q2 = min(1.0, (3 * (sat(R + D) - 0.2 * t) % 1.0) ** 2) - 0.5
    ring = sat(0.5 * sat(1.0 - 0.5 * math.sqrt(q1 * q1 + 3 * q2 * q2)))
    line = sat(sat(1.0 - 20.0 * abs(s[(7, 0)])) / 3.0)
    total = (ring + line) * math.sqrt(abs(1.0 - u)) + s[(8, 0)] + s[(8, 2)] + sat(4.0 * s[(9, 1)] - 3.0)
    ks = inp['CB2[0]'] * total
    e = inp['CB0[115]'] * inp['attribute1.3'] * ks * (20.0 * inp['CB2[1]'] + 5.0)
    fog = (inp['CB0[112]'], inp['CB0[113]'], inp['CB0[114]'])
    colour = [f * (1.0 - inp['attribute1.3']) + c * e for f, c in zip(fog, (36.0, 18.0 * 0.3492948, 18.0 * 0.01713626))]
    cover = sat(max(0.0, ks))
    return colour, sat(inp['attribute0.3'] * cover), cover


def hud_coverage_closed_form(inp, s):
    """Coverage of 678C5C0E (unlit branch, instance flag bit 0x80 clear): see the uKind 19 comment in viewer/shading.py."""
    u, v, t = inp['attribute0.0'], inp['attribute0.1'], inp['CB0[99]']
    d = math.hypot(u - 0.5, v - 0.5)
    ring = sat(((t + d) % 1.0) ** 0.1)
    hex_a = sat(1.0 - 2.2222222 * abs(s[(5, 0)]))
    hex_b = sat(1.0 - 20.0 * abs(s[(5, 1)]))
    hxx = (2.857143 * hex_b + 0.6666667 * hex_a) * (0.05 + d)
    g = 1.5 * s[(6, 1)] + 5.0 * s[(6, 2)] * (1.0 - ring)
    return sat(max(0.05 * hxx, (g + 1.5 * s[(6, 0)]) * (0.7 + 0.3 * s[(7, 0)])))


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hashes_bind_the_rules(self):
        rule = tp._graph_rule([SHIELD_GRAPH], {SHIELD_GRAPH: SHIELD_SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('turret_shield', '19D0F1FA', SHIELD_SHA))
        self.assertEqual(rule['slots'], {'_window_mask': 0x49A7866B4AC18E9F, '_window_n1': 0x49A7866B5DBA9ADC,
                                         '_window_n2': 0x49A7866BBB8E69C8, '_window_c': 0x49A7866BF44DF0BB})
        self.assertEqual(tp._graph_rule([SHIELD_GRAPH], {SHIELD_GRAPH: '0' * 64}), {})
        rule = tp._graph_rule([HUD_GRAPH], {HUD_GRAPH: HUD_SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('turret_hud', '678C5C0E', HUD_SHA))
        self.assertEqual(rule['slots'], {'_window_mask': 0x49A7866B0056CBD0, '_window_n1': 0x49A7866BF582C34E,
                                         '_window_n2': 0x49A7866B2EA0E356})
        self.assertEqual(tp._graph_rule([HUD_GRAPH], {}), {})

    def test_kinds_and_uniform_packing(self):
        self.assertEqual((GLASS_KINDS['turret_shield'], GLASS_KINDS['turret_hud']), (18, 19))
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))
        shield = dict(kind='turret_shield', params=dict(gain=1.0, emission=0.25, amplitude=0.5, time=0.0, alpha_cut=0.1,
                                                        alpha_scale=0.2, fog_transmittance=1.0))
        self.assertEqual(glass_uniforms(shield), (18, (1.0, 0.25, 0.5, 0.0), (0.1, 0.2, 1.0, 0.0), (0.0,) * 4, (0.0,) * 4, (0.0,) * 4))
        self.assertEqual(glass_uniforms(shield, 3.5)[1], (1.0, 0.25, 0.5, 3.5))   # the effect clock replaces CB0[99]
        hud = dict(kind='turret_hud', params=dict(variant=0.0, amplitude=0.5, time=0.0, alpha_cut=0.1, alpha_scale=0.2,
                                                  fog_transmittance=1.0))
        self.assertEqual(glass_uniforms(hud), (19, (0.0, 0.5, 0.0, 0.0), (0.1, 0.2, 1.0, 0.0), (0.0,) * 4, (0.0,) * 4, (0.0,) * 4))
        self.assertEqual(glass_uniforms(hud, 2.0)[1], (0.0, 0.5, 2.0, 0.0))

    def test_fitted_amplitude_and_hdr_blending(self):
        # CB0[115] (times the fog transmittance) fitted to the Gaspar capture's final image, one frame value for both programs.
        self.assertEqual(tp.TURRET_BARRIER_FITTED_AMPLITUDE, 0.20)
        self.assertEqual((tp.TURRET_SHIELD_CONSTANTS['amplitude'], tp.TURRET_HUD_CONSTANTS['amplitude']), (0.20, 0.20))
        # Both blend in the viewer's float scene target (tonemapped once, as the game's HDR target), not per fragment.
        from rc2016.viewer.shading import HDR_RESOLVE_FRAGMENT
        from rc2016.viewer.viewport import ModelViewport
        self.assertEqual(ModelViewport.HDR_FORWARD_KINDS, frozenset({'turret_shield', 'turret_hud'}))
        self.assertIn('max(cover, max(rgb.r, max(rgb.g, rgb.b)))', HDR_RESOLVE_FRAGMENT)   # straight alpha keeps added light

    def test_glsl_branches_carry_the_decoded_equations(self):
        shield = GLASS_FRAGMENT.split('uKind == 18')[1].split('uKind == 19')[0]
        for text in ('vec2(u - 0.035 * t, 1.5 * v + 0.05 * t)', 'vec2(0.5 + 0.5 * u - 0.03 * t, 0.5 + 0.5 * v - 0.05 * t)',
                     'vec2(10.0 * u, 10.0 * v)', 'sat(pow(length(vec2(u, v - 0.475)), 1.25))',
                     'float f1 = fract(3.0 * (R - 0.2 * t))', 'sat(0.3 * abs(1.0 - n3 - n2))',
                     'sat(0.5 * sat(1.0 - 0.5 * sqrt(q1 * q1 + 3.0 * q2 * q2)))', 'sat(sat(1.0 - 20.0 * abs(hx)) / 3.0)',
                     '(ring + hexLine) * sqrt(abs(1.0 - u)) + sc.r + sc.b + sat(4.0 * sc.g - 3.0)', 'if (cover < uB.x) discard',
                     'alpha = sat(uB.y * cover)', 'uA.z * uB.z * ks * (20.0 * uA.y + 5.0) * vec3(36.0, 6.2873064, 0.30845268)'):
            self.assertIn(text, shield)
        hud = GLASS_FRAGMENT.split('uKind == 19')[1].split('uKind == 14')[0]
        for text in ('vec2(15.2 * u - 0.1, 15.2 * v - 0.1)', 'vec2(15.0 * u, 15.0 * v)', 'vec2(0.01 * u - 0.1 * t, v + 0.1 * t)',
                     'sat(pow(fract(t + d), 0.1))', 'sat(1.0 - 2.2222222 * abs(h1))', 'sat(1.0 - 20.0 * abs(h2))',
                     '(2.857143 * hexB + 0.6666667 * hexA) * (0.05 + d)', 'float g = 1.5 * th.g + 5.0 * th.b * (1.0 - ring)',
                     'sat(max(0.05 * hxx, (g + 1.5 * th.r) * (0.7 + 0.3 * nz)))', 'if (cover < uB.x) discard',
                     'float G = 0.25 + 0.75 * sat(g + 0.05 * hxx)', 'vec3(pow(G, 0.1), uA.x * (G50 - G) + G, uA.x * (G200 - G100) + G100)',
                     '3.0 * uA.y * (5.0 * (0.9 + 0.2 * sin(PI * t)) + 1.0) * c'):
            self.assertIn(text, hud)

    def test_blender_front_end_and_validator_agree(self):
        self.assertEqual(validation.GLASS_CONSTANTS['turret_shield'],
                         ('gain', 'emission', 'amplitude', 'fog transmittance', 'time', 'alpha cut', 'alpha scale'))
        self.assertEqual(validation.GLASS_CONSTANTS['turret_hud'],
                         ('variant', 'amplitude', 'fog transmittance', 'time', 'alpha cut', 'alpha scale'))
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        self.assertIn("'turret_shield', 'turret_hud'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])
        body = text.split("elif key in ('turret_shield', 'turret_hud'):")[1].split("elif key == 'glass_taxi':")[0]
        for key, kind in (('turret_shield', dict(gain=1.0, emission=0.0, amplitude=0.1, time=0.0, alpha_cut=0.1, alpha_scale=0.2,
                                                  fog_transmittance=1.0)),
                          ('turret_hud', dict(variant=0.0, amplitude=0.1, time=0.0, alpha_cut=0.1, alpha_scale=0.2, fog_transmittance=1.0))):
            rule = dict(applies=True, ported=[key],
                        terms={key: dict(kind=key, params=kind, blend='alpha_over', two_sided=True)},
                        textures={'_c': None, '_g': None, '_n': None, '_window_mask': 'a', '_window_n1': 'b', '_window_n2': 'c',
                                  '_window_c': 'd'})
            names, roles = validation._viewer_required(rule, False)
            self.assertTrue(set(names) <= set(validation._viewer_expected_constants(rule)))
            self.assertEqual(roles, ['_window_mask', '_window_n1', '_window_n2'] + (['_window_c'] if key == 'turret_shield' else []))
        for name in ('glass time', 'glass amplitude', 'glass gain', 'glass emission', 'glass fog transmittance', 'glass alpha cut',
                     'glass alpha scale', 'glass variant', "'noisetile03'", "'noisetile02'", "'hex grid'",
                     "'shield image'", "'HUD image'", "'noise cell'", 'VR glass emission'):
            self.assertIn(name, body)


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
        cls.shield_graph = asset_bytes(cls.archive, SHIELD_GRAPH)
        cls.hud_graph = asset_bytes(cls.archive, HUD_GRAPH)
        cls.shield = next(p for p in translator.pixel_programs(cls.shield_graph) if p['shader_id'] == '0x19d0f1fa')
        cls.hud = next(p for p in translator.pixel_programs(cls.hud_graph) if p['shader_id'] == '0x678c5c0e')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_identities(self):
        self.assertEqual(hashlib.sha256(self.shield_graph).hexdigest(), SHIELD_SHA)
        self.assertEqual(hashlib.sha256(self.hud_graph).hexdigest(), HUD_SHA)
        self.assertEqual(self.shield['code_sha256'], SHIELD_CODE_SHA)
        self.assertEqual(self.hud['code_sha256'], HUD_CODE_SHA)

    def test_shield_matches_the_program_numerically(self):
        from rc2016.research import forward_loops as loops
        compiler = loops.compile_opaque(self.shield)
        roots = compiler.exports[0]
        rng = random.Random(20261107)
        worst = 0.0
        for _ in range(80):
            inp = {'CB0[%d]' % k: rng.uniform(0.0, 1.0) for k in (112, 113, 114)}
            inp.update({'CB0[115]': rng.uniform(0.01, 0.3), 'CB0[99]': rng.uniform(0.0, 20.0), 'CB2[0]': rng.uniform(0.5, 2.0),
                        'CB2[1]': rng.uniform(0.0, 1.0), 'attribute0.0': rng.uniform(0.0, 0.9), 'attribute0.1': rng.uniform(0.0, 1.0),
                        'attribute0.3': rng.uniform(0.05, 1.0), 'attribute1.3': rng.uniform(0.2, 1.0)})
            samples = {(api, ch): rng.uniform(0, 1) for api in range(5, 10) for ch in range(4)}
            colour, alpha, _cover = shield_closed_form(inp, samples)
            for k in range(3):
                worst = max(worst, abs(loops.evaluate(compiler.ir, roots[k], inp, samples) - colour[k]) / max(1.0, abs(colour[k])))
            worst = max(worst, abs(loops.evaluate(compiler.ir, roots[3], inp, samples) - alpha))
        self.assertLess(worst, 5e-5)

    def test_hud_alpha_matches_the_program_numerically(self):
        from rc2016.research import forward_loops as loops
        compiler = loops.compile_opaque(self.hud)
        roots, _subs = loops.bind_frozen(compiler, flags=0x0, time=0.0)
        rng = random.Random(20261107)
        worst = 0.0
        for t in (0.0, 3.3):
            roots, _subs = loops.bind_frozen(compiler, flags=0x0, time=t)
            for _ in range(80):
                inp = {'CB0[99]': t, 'attribute0.0': rng.uniform(0.0, 1.0), 'attribute0.1': rng.uniform(0.0, 1.0),
                       'attribute0.3': rng.uniform(0.05, 1.0)}
                samples = {(api, ch): rng.uniform(0, 1) for api in (5, 6, 7, 33) for ch in range(4)}
                expected = sat(inp['attribute0.3'] * hud_coverage_closed_form(inp, samples))
                worst = max(worst, abs(loops.evaluate(compiler.ir, roots[3], inp, samples) - expected))
        self.assertLess(worst, 5e-5)

    def test_viewer_rules_from_the_archive(self):
        from rc2016.levels.scene import asset_bytes
        for material, graph_sha, kind, writer, flags, textures, alpha_scale in (
                (SHIELD_MATERIAL, SHIELD_SHA, 'turret_shield', '19D0F1FA', 0x20000E, SHIELD_TEXTURES, 19 / 255.0),
                (HUD_MATERIAL, HUD_SHA, 'turret_hud', '678C5C0E', 0x20023E, HUD_TEXTURES, 191 / 255.0)):
            with self.subTest(material=material):
                paths = tp.material_texture_paths(self.archive, material)
                forward = paths['forward']
                p = forward['params']
                self.assertEqual(material_flags(data=asset_bytes(self.archive, material)), flags)
                self.assertEqual((forward['kind'], forward['writer'], forward['graph_sha256'], forward['blend'], forward['two_sided']),
                                 (kind, writer, graph_sha, 'alpha_over', True))
                self.assertEqual(tuple(paths[role] for role in ('_window_mask', '_window_n1', '_window_n2', '_window_c')[:len(textures)]),
                                 textures)
                self.assertTrue(all(role not in paths for role in ('_c', '_g', '_n', '_pick')))
                self.assertAlmostEqual(p['alpha_cut'], 4.0 / 255.0, places=7)
                self.assertAlmostEqual(p['alpha_scale'], alpha_scale, places=7)
                self.assertEqual((p['time'], p['fog_transmittance'], p['game_equivalence']), (0.0, 1.0, False))
                self.assertEqual((p['amplitude'], p['amplitude_source']), (0.20, 'fitted to the Gaspar capture image'))
                if kind == 'turret_shield':
                    self.assertEqual((p['gain'], p['emission']), (1.0, 0.0))   # material 0xEF782F74 = 1, key 0x2F7DD4F2 at its default
                else:
                    self.assertEqual(p['variant'], 0.0)
                if kind == 'turret_shield':   # the program reads API 8 and 9 as one image: a material that splits them does not bind
                    with patch.object(tp, 'texture_slots', side_effect=lambda data, tag: {0x49A7866B0242AAC5: SHIELD_TEXTURES[0]}
                                      if tag == tp.MATERIAL_SLOT_SECTION else {}):
                        self.assertNotIn('forward', tp.material_texture_paths(self.archive, material))


if __name__ == '__main__':
    unittest.main()
