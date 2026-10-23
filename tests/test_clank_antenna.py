"""Clank's antenna light (graph hero_bright_center_light, deferred writer 7BEC2D2F) and the D14A48D7 blink audit.

The rule tests use synthetic data. The archive tests read the game archive (RC2016_RESEARCH) and evaluate the compiled
writers' lowered DAGs against the closed-form equations in docs/material-decoding/clank-antenna.md.
"""
import hashlib
import math
import os
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2016.viewer import textures as tp
from tests.test_viewer_textures import FakeArchive, dat1

GRAPH = 'materialgraph/character/hero/hero_bright_center_light/hero_bright_center_light.materialgraph'
VARIANT = 'materialgraph/character/hero/hero_bright_center_light/hero_bright_center_light_variable.materialgraph'
MATERIAL = 'material/character/hero/hero_clank_antenna/hero_bright_center_light_clank.material'
GRAPH_SHA = 'd3e43dc01666699bfbaefedf8b018eb9db4e34583d1ad859cf7e10e75ce4d28d'
MATERIAL_SHA = '5f3f16412e2c6f760a8e3cf9e59bcda3db7dc090280134fa3fe945d570c87a8f'
CODE_SHA = 'cd0b14ef9dba2b2174fafdda728e143f79bd2f6ae1dc939b164744f1a992e4e1'
VARIANT_SHA = 'e5089d1dc2f4006ca73ccf4287b221f5cee196f5e60267e5663d23e7455e6481'
VARIANT_CODE_SHA = 'f991e41e71170d9686935396c072f4484bbdf0b6d6e108ff4a745923d31ebf57'
# Graph parameter table defaults (the Clank material stores none of these keys).
DEFAULTS = {0x16B8AB81: [0.0, 0.0, 0.0], 0x5BC2D1F0: [1.0, 0.5271151065826416, 0.46207699179649353],
            0x9E351D7A: 0.009999999776482582, 0xB7305C21: 15.0, 0x9872D04A: 0.15000000596046448,
            0x08FE9F29: 0.20000000298023224}


def row(key, values):
    values = tuple(values) if isinstance(values, (tuple, list)) else (values,)
    return dict(key_hash=key, size=4 * len(values), raw_hex=struct.pack('<%df' % len(values), *values).hex())


def extract(rows, sha=None):
    graph_blob = dat1([], lambda at: [])
    mat = dat1([GRAPH], lambda at: [])
    archive = FakeArchive({'m/a.material': ('material', mat), GRAPH: ('materialgraph', graph_blob)})
    digest = sha or hashlib.sha256(b'1TAD' + graph_blob).hexdigest()
    rules = tuple((p, dict(r, _graph_sha256=digest) if r.get('bright_center') == 'clank' else r) for p, r in tp.GRAPH_RULES)
    params = dict(parameters=[], unsupported_parameters=rows)
    with (patch.object(tp, 'GRAPH_RULES', rules),
          patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return tp.material_texture_paths(archive, 'm/a.material')


@patch.object(tp, 'CLANK_ANTENNA_QWARK_TIP_PREVIEW', False)  # these test the decoded 7BEC2D2F rule
class ClankRuleTests(unittest.TestCase):
    def test_rule_is_bound_to_the_exact_graph_hash(self):
        self.assertEqual(tp.CLANK_ANTENNA_GRAPH_SHA256, GRAPH_SHA)
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})['bright_center'], 'clank')
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})
        # The Qwark sibling keeps its own, unchanged rule.
        self.assertEqual(tp._graph_rule([VARIANT]),
                         dict(self_lit='bright_center', self_lit_key=0xB7305C21, bright_center=True))
        self.assertEqual(extract([], sha='0' * 64).get('bright_center'), None)

    def test_defaults_give_the_decoded_constants(self):
        got = extract([row(0x66EFA55B, 6.0), row(0x24F4CA4C, 0.2), row(0x62835AF9, 0.1)])   # stale rows of another graph
        bc = got['bright_center']
        self.assertEqual((got['self_lit'], got['k'], got['spec'], got['gloss']), ('bright_center', 15.0, 1.0, 1.0))
        self.assertEqual(bc['edge'], [0.0, 0.0, 0.0])
        for a, b in zip(bc['centre'], (10.0, 5.271151, 4.620770)):
            self.assertAlmostEqual(a, b, places=5)
        self.assertAlmostEqual(bc['exponent'], 0.01, places=7)       # direct key, not 0.2 (1 - S)
        self.assertAlmostEqual(bc['weight'], 0.575)                   # B + (1 - B) 0.5 at sin = 0
        self.assertAlmostEqual(bc['f0'], 0.2, places=7)
        self.assertAlmostEqual(bc['gloss_sq'], 0.35, places=6)
        self.assertEqual(bc['writer'], '7BEC2D2F')
        self.assertEqual(bc['frozen_inputs'], dict(CB0_99=0.0))
        self.assertFalse(bc['game_equivalence'])
        self.assertIn('CB0[99] is frozen at 0', bc['limitation'])
        self.assertIn('TBUFFER', bc['limitation'])

    def test_material_values_override_graph_defaults(self):
        bc = extract([row(0x9E351D7A, 0.5), row(0x9872D04A, 0.5), row(0x5BC2D1F0, (0.5, 0.25, 0.0)), row(0xB7305C21, 3.0)])
        self.assertEqual(bc['k'], 3.0)
        bc = bc['bright_center']
        self.assertAlmostEqual(bc['exponent'], 0.5)
        self.assertAlmostEqual(bc['weight'], 0.75)
        self.assertEqual(bc['centre'], [5.0, 2.5, 0.0])


def evaluate(program, cb2, ndv, time=0.0, w=1.0, tex=(1.0, 1.0, 1.0)):
    """Lowered writer outputs (albedo xyz, F0 xyz, gloss, emission) for N.V = ndv with the engine inputs set to review values.

    N = attribute2, V from the screen ray (0, 0, -1), flat tangent-space normal and zero specular-AA width.
    """
    from rc2016.shaders.ir import DAG
    from rc2016.shaders.translator import lower_deferred
    lowered = lower_deferred(program['ir'])
    ir = DAG(lowered['nodes'])
    angle = math.acos(ndv)
    inputs = {'attribute2.0': math.sin(angle), 'attribute2.1': 0.0, 'attribute2.2': math.cos(angle),
              'attribute3.0': 1.0, 'attribute3.1': 0.0, 'attribute3.2': 0.0, 'attribute3.3': w,
              'CB0[99]': time, 'CB0[95]': 0.0, 'ps.POS_W': 1.0, 'ps.POS_X': 0.0, 'ps.POS_Y': 0.0, 'ps.FRONT_FACE': 1.0,
              'CB0[92]': 0.0, 'CB0[93]': 0.0, 'CB0[94]': -1.0, 'CB0[139]': 0.0, 'attribute1.2': 0.0}
    for k in range(92, 108):
        inputs.setdefault('CB0[%d]' % k, 0.0)
    for k, v in cb2.items():
        inputs['CB2[%d]' % k] = v
    for name in ('attribute0.0', 'attribute0.1'):
        inputs[name] = 0.5
    subs = {i: ir.const(0.0) for i, n in enumerate(ir.nodes) if n['op'] == 'unsupported'}
    outputs = [*lowered['outputs']['albedo'], *lowered['outputs']['F0'], lowered['outputs']['gloss'], lowered['outputs']['emission']]
    outputs = ir.replace(outputs, subs)
    textures = {(n['sample'], n['channel']): (0.5 if n['api'] == 6 else tex[n['channel']] if n['channel'] < 3 else 1.0)
                for n in ir.nodes if n['op'] == 'texture'}
    return [ir.evaluate(o, inputs, textures) for o in outputs]


@patch.object(tp, 'CLANK_ANTENNA_QWARK_TIP_PREVIEW', False)  # these test the decoded 7BEC2D2F rule
class ClankArchiveTests(unittest.TestCase):
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
        cls.graph_data = bytes(asset_bytes(cls.archive, GRAPH))
        cls.variant_data = bytes(asset_bytes(cls.archive, VARIANT))
        cls.material_data = bytes(asset_bytes(cls.archive, MATERIAL))
        cls.clank = {p['shader_id']: p for p in compile_graph(cls.graph_data)['programs']}
        cls.qwark = {p['shader_id']: p for p in compile_graph(cls.variant_data)['programs']}

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_source_identities(self):
        self.assertEqual(hashlib.sha256(self.graph_data).hexdigest(), GRAPH_SHA)
        self.assertEqual(hashlib.sha256(self.material_data).hexdigest(), MATERIAL_SHA)
        self.assertEqual(hashlib.sha256(self.variant_data).hexdigest(), VARIANT_SHA)
        self.assertEqual(self.clank['0x7bec2d2f']['code_sha256'], CODE_SHA)
        self.assertEqual(self.qwark['0xd14a48d7']['code_sha256'], VARIANT_CODE_SHA)

    def test_material_stores_none_of_the_graph_keys_and_overrides_the_albedo_slot(self):
        from rc2016.formats.materials import extract_material_parameters
        keys = {p['key_hash'] for p in extract_material_parameters(self.material_data)['parameters']}
        keys |= {p['key_hash'] for p in extract_material_parameters(self.material_data).get('unsupported_parameters', [])}
        self.assertFalse(keys & set(DEFAULTS))
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        self.assertEqual(paths['_c'], 'textures/character/hero/clank/clank_antenna_c.texture')   # graph slot: white_c
        self.assertEqual(paths['_n'], 'textures/default_pink_shader/default_pink_shader_n.texture')
        bc = paths['bright_center']
        self.assertEqual((bc['writer'], bc['graph_sha256']), ('7BEC2D2F', GRAPH_SHA))
        self.assertAlmostEqual(bc['weight'], 0.575)
        self.assertEqual(paths['k'], 15.0)

    def test_writer_equals_the_closed_form(self):
        cb2 = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.01, 4: 1.0, 5: 0.5271151065826416, 6: 0.46207699179649353, 7: 15.0, 8: 0.15, 9: 0.2}
        program = self.clank['0x7bec2d2f']
        tex = (0.8, 0.5, 0.25)
        for ndv in (1.0, 0.9, 0.5, 0.1, 0.0):
            for time in (0.0, 0.25):
                out = evaluate(program, cb2, ndv, time=time, tex=tex)
                t = abs(1.00100004673 - ndv) ** 0.01
                phase = 4.2 * time
                w = 0.15 + 0.85 * (0.5 * math.sin(2 * math.pi * (phase / (2 * math.pi) % 1.0)) + 0.5)
                albedo = [tex[c] * (10.0 * cb2[4 + c] * w * (1.0 - t) + cb2[c] * t) for c in range(3)]
                for got, want in zip(out[:3], albedo):
                    self.assertAlmostEqual(got, want, places=5)
                self.assertEqual(out[3:6], [0.2, 0.2, 0.2])
                self.assertAlmostEqual(out[6], math.sqrt(0.35), places=6)
                self.assertAlmostEqual(out[7], 15.0 * albedo[0], places=4)   # exp2(5 * 0.2 log2(1 + K a)) - 1
        self.assertAlmostEqual(w, 0.15 + 0.85 * (0.5 * math.sin(4.2 * 0.25) + 0.5), places=6)

    def test_engine_inputs_that_block_native_translation(self):
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.shaders import bindings
        params = extract_material_parameters(self.material_data)

        def reasons(**kw):
            result = bindings.prepare_translation([self.graph_data], self.material_data, params,
                                                  source_graph_paths=[GRAPH], source_material_path=MATERIAL)
            self.assertEqual(result['status'], 'unsupported')
            return str([d for d in result['diagnostics'] if isinstance(d, dict) and d.get('shader_id') == '0x7bec2d2f'])
        text = reasons()
        for blocker in ('CB0[99]', 'attribute2.0', 'attribute3.3'):
            self.assertIn(blocker, text)
        # Freezing CB0[99] alone (the weapon/gadget convention) is not enough: the normal fade still needs engine data.
        rule = dict(graph_path=GRAPH, graph_sha256=GRAPH_SHA, material_path=MATERIAL, material_sha256=MATERIAL_SHA,
                    shader_id='0x7bec2d2f', code_sha256=CODE_SHA, input=dict(kind='cb', api=0, dword=99, value=0.0, name='CB0[99]'))
        with patch.dict(bindings.SOURCE_PREVIEW_POLICIES, {GRAPH: rule}):
            text = reasons()
        self.assertNotIn('CB0[99]', text)
        for blocker in ('attribute2.0', 'attribute3.3', 'CB0[95]', 'TBUFFER_LOAD_FORMAT_X', 'ps.POS_W'):
            self.assertIn(blocker, text)

    def test_qwark_sibling_phase_is_not_zero_at_time_zero(self):
        """Audit of D14A48D7: the blink phase is rate * time + 1000 |attribute3.w|, so sin is not 0 at time 0."""
        cb2 = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.9, 4: 1.0, 5: 0.5, 6: 0.25, 7: 15.0, 8: 0.15, 9: 4.0, 10: 0.2, 11: 0.35, 12: 5.0}
        program = self.qwark['0xd14a48d7']
        for w in (1.0, -1.0):
            for time in (0.0, 0.25):
                out = evaluate(program, cb2, 1.0, time=time, w=w)
                t = abs(1.00100004673 - 1.0) ** (0.2 * (1.0 - 0.9))
                weight = min(1.0, max(0.0, 1.0 + 0.85 * (0.5 * math.sin(4.0 * time + 1000.0 * abs(w)) - 0.5)))
                self.assertAlmostEqual(out[0], 5.0 * 1.0 * weight * (1.0 - t), places=4)
                self.assertAlmostEqual(out[6], math.sqrt(0.35), places=6)
                self.assertAlmostEqual(out[7], 15.0 * out[0], places=4)
        self.assertAlmostEqual(min(1.0, max(0.0, 1.0 + 0.85 * (0.5 * math.sin(1000.0) - 0.5))), 0.92642, places=4)
        self.assertNotAlmostEqual(math.sin(1000.0), 0.0, places=1)

    def test_blink_phase_forms(self):
        """Clank has no per-vertex phase term; the Qwark sibling has one (source expression text of the lowered DAGs)."""
        from rc2016.shaders.ir import DAG
        from rc2016.shaders.translator import lower_deferred
        def uses_attribute3w_in_phase(program):
            lowered = lower_deferred(program['ir'])
            ir = DAG(lowered['nodes'])
            for index in ir.reachable(lowered['outputs']['albedo']):
                n = ir.nodes[index]
                if n['op'] == 'sin':
                    names = {ir.nodes[j]['name'] for j in ir.reachable([index]) if ir.nodes[j]['op'] == 'input'}
                    return 'attribute3.3' in names
            return None
        self.assertFalse(uses_attribute3w_in_phase(self.clank['0x7bec2d2f']))
        self.assertTrue(uses_attribute3w_in_phase(self.qwark['0xd14a48d7']))


if __name__ == '__main__':
    unittest.main()
