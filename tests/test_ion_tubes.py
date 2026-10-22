"""Victor von Ion tubes forward rule (graph enm_victor_von_ion_tubes_w_rust, pixel program 86DDE345).

The rule and front-end tests are synthetic. The archive tests read the game archive (RC2016_RESEARCH) and evaluate the
compiled forward program against the closed form the viewer and Blender front-ends implement.
"""
import hashlib
import math
import os
import pathlib
import random
import unittest
from pathlib import Path

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms

GRAPH = 'materialgraph/character/enemy/enm_victor_von_ion_tubes/enm_victor_von_ion_tubes_w_rust.materialgraph'
MATERIAL = 'material/character/enemy/enm_victor_von_ion_tubes/enm_victor_von_ion_tubes.material'
GRAPH_SHA = '8cdc2c05c2e934ecc56e6765992ee8734ada8af530dc9d42c1265578ff21b901'
MATERIAL_SHA = 'd87dabcb497abb21f04af2a8d13689a0874371d319cf693bd3e1fb5312aa9251'
FORWARD_CODE_SHA = '85dd7c3cf02d5a49960523dc611f2259296a8a542e9b092020cb04bf0fb20d15'
TEXTURE = 'textures/visualeffect/electricity/electricity_line_01/electricity_line_01.texture'
BASE = (0.0074990298599004745, 0.012286489829421043, 0.019382359459996223)
RIM = (0.030705338343977928, 0.12897680699825287, 0.19647814333438873)


def closed_form(amp, tint, gain, ndv, t5, t6):
    """The colour the viewer's `uKind == 12` branch and the Blender front-end compute."""
    m = min(1.0, max(0.0, t5 + t6))
    face = max(min(1.0, max(0.0, ndv)) - 0.00100004673, 0.0)
    return [2.0 * amp * (1.0 + m) * (BASE[i] + RIM[i] * face ** 5 + m * tint[i] * gain) for i in range(3)]


def forward(**overrides):
    params = dict(colour=[0.0953, 0.5906, 1.0], gain=6.0, power=0.0, **tp.ION_TUBES_CONSTANTS)
    params.update(overrides)
    return dict(kind='ion_tubes', params=params, blend='opaque', two_sided=False)


class IonTubesRuleTests(unittest.TestCase):
    def test_rule_needs_the_exact_graph_hash(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})
        self.assertEqual((rule['forward'], rule['graph_sha256']), ('ion_tubes', GRAPH_SHA))
        self.assertEqual(rule['slots'], {'_pick': 0x49A7866B4AC18E9F})
        self.assertEqual({name: key for name, key, _ in rule['params']},
                         dict(colour=0x427C868D, gain=0x8A063007, power=0xEC98DCA7))
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH.replace('_w_rust', '_other')], {}), {})

    def test_amplitude_is_the_labelled_presentation_value(self):
        c = tp.ION_TUBES_CONSTANTS
        self.assertEqual((c['amplitude'], c['amplitude_input'], c['presentation'], c['writer']), (0.5, 'CB0 dword 115', True, '86DDE345'))
        self.assertEqual((c['time'], c['time_input']), (0.0, 'CB0 dword 99'))
        self.assertEqual(c['frozen_inputs'], dict(cb0_107=0.0, fog_blend=1.0, vertex_alpha=1.0, cb2_4_power=0.0,
                                                  cutoff='discard predicate not translated'))

    def test_uniform_packing(self):
        kind, a, b, c, d = glass_uniforms(forward())
        self.assertEqual((kind, GLASS_KINDS['ion_tubes']), (12, 12))
        self.assertEqual(a, (0.0953, 0.5906, 1.0, 6.0)); self.assertEqual(b, (0.5, 0.0, 0.0, 0.0))
        self.assertEqual(glass_uniforms(forward(), 2.5)[2], (0.5, 2.5, 0.0, 0.0))   # the viewer clock replaces the static time
        self.assertEqual(c, (*BASE, 0.0)); self.assertEqual(d, (*RIM, 0.0))

    def test_glsl_branch_carries_the_decoded_equations(self):
        branch = GLASS_FRAGMENT.split('uKind == 12')[1].split('uKind == 7')[0]
        for text in ('vec2(1.0 - 2.0 * vUV.y + uB.y, 0.25 * vUV.x - uB.y)).g', 'vec2(2.0 * vUV.x + 0.3, 0.5 * vUV.y - uB.y)).r',
                     'float m = sat(t5 + t6)', 'max(ndv - 0.00100004673, 0.0)', 'F2 * F2 * F',
                     '2.0 * uB.x * (1.0 + m) * (uC.rgb + uD.rgb * (F2 * F2 * F) + m * uA.w * uA.rgb)', 'alpha = 1.0'):
            self.assertIn(text, branch)

    def test_closed_form_limits(self):
        amp, tint, gain = 0.5, (0.0953, 0.5906, 1.0), 6.0
        dark = closed_form(amp, tint, gain, 0.0, 0.0, 0.0)           # no streak, edge-on: the base colour alone
        self.assertEqual(dark, [2.0 * amp * b for b in BASE])
        streak = closed_form(amp, tint, gain, 0.0, 0.5, 0.5)         # m = 1: (1 + m) doubles, the tint term enters
        self.assertAlmostEqual(streak[2], 4.0 * amp * (BASE[2] + tint[2] * gain), places=9)
        facing = closed_form(amp, tint, gain, 1.0, 0.0, 0.0)
        self.assertAlmostEqual(facing[2], 2.0 * amp * (BASE[2] + RIM[2] * 0.99899995327 ** 5), places=9)
        self.assertEqual(closed_form(amp, tint, gain, 0.001, 0.0, 0.0), dark)   # F clamps at 0 below the 1.001 bias

    def test_validator_names_match_the_blender_front_end(self):
        rule = dict(applies=True, ported=['ion_tubes'], terms={'ion_tubes': forward()}, textures={'_c': None, '_g': None, '_n': None, '_pick': TEXTURE})
        expected = validation._viewer_expected_constants(rule)
        names, roles = validation._viewer_required(rule, False)
        wanted = ['VR ion tubes ' + n for n in ('tint', 'gain', 'amplitude', 'base', 'rim', 'time')]
        self.assertEqual((sorted(names), roles), (sorted(wanted), ['_pick']))
        self.assertEqual(set(expected), set(wanted))
        self.assertEqual(expected['VR ion tubes tint'], [0.0953, 0.5906, 1.0])
        self.assertEqual((expected['VR ion tubes gain'], expected['VR ion tubes amplitude'], expected['VR ion tubes time']), (6.0, 0.5, 0.0))
        text = (pathlib.Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_ion_tubes')[1].split('def _finish_viewer_rule')[0]
        for name in names:
            self.assertIn(name[3:], body)
        for node in ('VR ion tubes colour (unlit)', 'VR cull back faces'):
            self.assertIn(node, body)
        self.assertIn("use_backface_culling = not f.get('two_sided')", body)
        self.assertIn("P.m('MAXIMUM', P.m('SUBTRACT', ndv, 0.00100004673), 0.0)", body)


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class IonTubesArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ['RC2016_RESEARCH']
        root = Path(os.environ.get('RC2016_GAME_ROOT', str(Path(research).parents[1] / 'Ratchet & Clank (EUR)' / 'Image0')))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import asset_bytes, open_archive
        from rc2016.shaders import translator
        cls.archive = open_archive(root)
        cls.graph = bytes(asset_bytes(cls.archive, GRAPH))
        cls.material = bytes(asset_bytes(cls.archive, MATERIAL))
        cls.programs = {}
        for program in translator.pixel_programs(cls.graph):
            compiler = translator.Compiler(program['code'], program['usage_slots'], program.get('ps_input_addr'),
                                           program.get('ps_input_ena'), program.get('vgpr_count'))
            compiler.run(translator.decoder().decode_stream(program['code'], 0))
            cls.programs.setdefault(program['shader_id'], (program, compiler))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_source_identities(self):
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), GRAPH_SHA)
        self.assertEqual(hashlib.sha256(self.material).hexdigest(), MATERIAL_SHA)
        self.assertEqual(self.programs['0x86dde345'][0]['code_sha256'], FORWARD_CODE_SHA)

    def test_deferred_writers_write_zero_colour_and_the_forward_program_is_single_target(self):
        self.assertEqual(sorted(self.programs['0x86dde345'][1].exports), [0])
        for shader in ('0x11408de9', '0x9f551dad'):
            exports = self.programs[shader][1].exports
            ir = self.programs[shader][1].ir
            for target in (1, 2):
                self.assertEqual([ir.value(n) for n in exports[target]], [0.0] * 4, shader)
            self.assertEqual(ir.value(exports[0][3]), 0.0)
        self.assertTrue(self.programs['0x9f551dad'][1].exec_masked_export)
        self.assertFalse(self.programs['0x11408de9'][1].exec_masked_export)

    def test_forward_program_equals_the_closed_form(self):
        compiler = self.programs['0x86dde345'][1]; ir = compiler.ir
        rng = random.Random(5)
        checked = 0
        for _ in range(200):
            n = [rng.uniform(-1, 1) for _ in range(3)]; v = [rng.uniform(-1, 1) for _ in range(3)]
            ndv = sum(x * y for x, y in zip(n, v)) / (math.sqrt(sum(x * x for x in n)) * math.sqrt(sum(x * x for x in v)))
            if ndv < 0.01:
                continue                       # F = 0 reaches log2(0), which the Python evaluator rejects
            amp, tint, gain = rng.uniform(0.01, 1.0), [rng.random() for _ in range(3)], rng.uniform(0.5, 7.0)
            t5, t6 = rng.random(), rng.random()
            inputs = {'ps.FRONT_FACE': 1.0, 'CB0[99]': rng.uniform(0.0, 5.0), 'CB0[107]': 0.0, 'CB0[112]': 0.3, 'CB0[113]': 0.4, 'CB0[114]': 0.5,
                      'CB0[115]': amp, 'attribute0.0': rng.random(), 'attribute0.1': rng.random(), 'attribute0.3': 1.0,
                      'attribute2.3': 1.0, 'attribute1.0': n[0], 'attribute1.1': n[1], 'attribute1.2': n[2],
                      'attribute2.0': v[0], 'attribute2.1': v[1], 'attribute2.2': v[2],
                      'CB2[0]': tint[0], 'CB2[1]': tint[1], 'CB2[2]': tint[2], 'CB2[3]': gain, 'CB2[4]': 1e-30}
            # CB0[99] only moves the sample coordinates: with the sampled values fixed, the colour does not depend on it
            got = [ir.evaluate(compiler.exports[0][j], inputs, {(0, 1): t5, (1, 0): t6}) for j in range(3)]
            for a, b in zip(got, closed_form(amp, tint, gain, ndv, t5, t6)):
                self.assertAlmostEqual(a / b, 1.0, places=9)
            checked += 1
        self.assertGreater(checked, 50)

    def test_texture_coordinates_scroll_with_time_at_zero_dissolve(self):
        compiler = self.programs['0x86dde345'][1]; ir = compiler.ir
        api5, api6 = compiler.samples
        self.assertEqual((api5['api'], api6['api']), (5, 6))
        for u, v, t in ((0.3, 0.7, 0.0), (0.3, 0.7, 0.37), (0.81, 0.12, 2.5)):
            inputs = {'CB0[99]': t, 'CB0[107]': 0.0, 'attribute0.0': u, 'attribute0.1': v}
            for got, want in zip([ir.evaluate(c, inputs) for c in api5['coordinates']], (1.0 - 2.0 * v + t, 0.25 * u - t)):
                self.assertAlmostEqual(got, want, places=6)
            for got, want in zip([ir.evaluate(c, inputs) for c in api6['coordinates']], (2.0 * u + 0.3, 0.5 * v - t)):
                self.assertAlmostEqual(got, want, places=6)

    def test_material_binds_one_image_and_the_viewer_rule_resolves(self):
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        forward_rule = paths['forward']
        self.assertEqual((forward_rule['kind'], forward_rule['blend'], forward_rule['two_sided']), ('ion_tubes', 'opaque', False))
        self.assertEqual(paths['_pick'], TEXTURE)
        for role in ('_c', '_g', '_n', '_h'):
            self.assertNotIn(role, paths)
        p = forward_rule['params']
        self.assertEqual((p['gain'], p['power']), (6.0, 0.0))
        for got, want in zip(p['colour'], (0.0953074694, 0.5906188488, 1.0)):
            self.assertAlmostEqual(got, want, places=7)

    def test_a_power_override_or_split_images_drop_the_rule(self):
        from unittest.mock import patch
        with patch.object(tp, 'ION_TUBES_POWER_KEY', 0x427C868D):      # reads the colour vector's first float: nonzero
            self.assertNotIn('forward', tp.material_texture_paths(self.archive, MATERIAL))
        with patch.object(tp, 'ION_TUBES_SLOTS', (0x49A7866B4AC18E9F, 0x49A7866B0056CBD0)):
            self.assertNotIn('forward', tp.material_texture_paths(self.archive, MATERIAL))


if __name__ == '__main__':
    unittest.main()
