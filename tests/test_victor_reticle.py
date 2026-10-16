"""Exact-identity, source-equation and routing contracts for the bounded Victor reticle rule."""
import hashlib
import math
import struct
import unittest
from unittest.mock import patch

from rc2016.formats.links import EXACT, rule_coverage
from rc2016.formats.materials import final_shader_status, viewer_graph_rule
from rc2016.viewer import textures as tp
from tests.test_viewer_textures import FakeArchive, dat1, flags, slot_rows

GRAPH = ('materialgraph/visualeffect/character/enemy/enm_victor_reticle/'
         'enm_victor_reticle.materialgraph')
MATERIAL = 'm/victor_reticle.material'
GRAPH_SHA = 'e4bcaf552be55cbc14b5081b6695a600084ae162889455b87eadc6c0103d79ea'
TRIGGER_SLOT = 0x628AD5A840ED6C83
TRIGGER = 'textures/visualeffect/trigger/trigger_linear_01_c.texture'
GAIN_KEY = 0xFD5FF119
AMP = tp.VICTOR_RETICLE_PRESENTATION_AMPLITUDE
REAL_SHA256 = hashlib.sha256


def f32(key, value, size=4, raw=None):
    return dict(key_hash=key, size=size, raw_hex=raw if raw is not None else struct.pack('<f', value).hex())


def archive_for(graph=GRAPH, graph_blob=None, rows=()):
    graph_blob = graph_blob if graph_blob is not None else dat1([TRIGGER], lambda at: [
        (tp.GRAPH_SLOT_SECTION, slot_rows([(at[0], TRIGGER_SLOT)]))])
    material_blob = dat1([graph], lambda at: [
        (tp.MATERIAL_FLAGS_SECTION, flags(0x9))])
    return FakeArchive({MATERIAL: ('material', material_blob), graph: ('materialgraph', graph_blob)})


def exact_hash_for_blob(blob):
    # FakeArchive.asset_data includes its synthetic 1TAD marker before the DAT1 body.
    return lambda data=b'': _Digest(GRAPH_SHA) if data == b'1TAD' + blob else REAL_SHA256(data)


class _Digest:
    def __init__(self, value): self.value = value
    def hexdigest(self): return self.value


def extract(archive, rows=()):
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([], {})),
          patch('rc2016.formats.materials.extract_material_parameters', return_value={
              'parameters': [], 'unsupported_parameters': list(rows)})):
        return tp.material_texture_paths(archive, MATERIAL)


class VictorReticleRuleTests(unittest.TestCase):
    def test_exact_graph_prefix_and_hash_are_both_required(self):
        self.assertEqual(tp._graph_rule([GRAPH]), {})
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})['forward'], 'victor_reticle')
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(rule_coverage([GRAPH])[0], EXACT)
        self.assertEqual(tp._graph_rule([GRAPH.replace('.materialgraph', '_extra.materialgraph')]), {})
        archive = archive_for()
        graph_id = archive.resolve(GRAPH)[0]
        graph_blob = archive.blobs[graph_id][4:]
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)):
            accepted = extract(archive)
        self.assertEqual(accepted['forward']['kind'], 'victor_reticle')
        rejected_archive = archive_for(graph_blob=dat1([TRIGGER], lambda at: [
            (tp.GRAPH_SLOT_SECTION, slot_rows([(at[0], TRIGGER_SLOT)]))]))
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=REAL_SHA256):
            rejected = extract(rejected_archive)
        self.assertNotIn('forward', rejected)

    def test_api8_slot_gain_flags_and_named_presentation_input(self):
        archive = archive_for()
        graph_blob = archive.blobs[archive.resolve(GRAPH)[0]][4:]
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)):
            got = extract(archive, [f32(GAIN_KEY, 4.0)])
        self.assertEqual(got['_reticle'], TRIGGER)
        self.assertEqual(got['forward']['params']['texture_gain'], 4.0)
        self.assertEqual(got['forward']['params']['amplitude'], AMP)
        self.assertEqual(got['forward']['params']['amplitude_input'], 'CB0[115]')
        self.assertTrue(got['forward']['params']['presentation'])
        self.assertEqual((got['forward']['blend'], got['forward']['two_sided']), ('additive', False))

    def test_missing_bound_api8_does_not_claim_the_rule_is_ready(self):
        archive = archive_for()
        graph_blob = archive.blobs[archive.resolve(GRAPH)[0]][4:]
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)):
            ready = viewer_graph_rule(archive, MATERIAL, {TRIGGER})
            missing = viewer_graph_rule(archive, MATERIAL, {'textures/other.texture'})
        self.assertIn('victor_reticle', ready['ported'])
        self.assertEqual(final_shader_status('unsupported', ready, {TRIGGER}, {'status': 'unsupported'}, None, [], [GRAPH]),
                         'viewer_graph_rule_frontend')
        self.assertIn('victor_reticle', missing['unported'])
        self.assertEqual(final_shader_status('unsupported', missing, set(), {'status': 'unsupported'}, None, [], [GRAPH]),
                         'unsupported')

    def test_final_route_requires_exact_graph_writer_and_bound_api8(self):
        valid = dict(applies=True, ported=['victor_reticle'],
            terms={'victor_reticle': {'writer': 'EB9C8254', 'graph_path': GRAPH,
                'graph_sha256': GRAPH_SHA}}, textures={'_reticle': TRIGGER})
        args = ('unsupported', valid, {TRIGGER}, {'status': 'unsupported'}, None, [])
        self.assertEqual(final_shader_status(*args, [GRAPH]), 'viewer_graph_rule_frontend')
        for field, value in (('graph_path', GRAPH.replace('.materialgraph', '_other.materialgraph')),
                             ('graph_sha256', '0' * 64), ('writer', '11408DE9')):
            rejected = dict(valid)
            rejected['terms'] = {'victor_reticle': dict(valid['terms']['victor_reticle'], **{field: value})}
            self.assertEqual(final_shader_status(args[0], rejected, *args[2:], [GRAPH]), 'unsupported')
        self.assertEqual(final_shader_status(*args[:1], valid, set(), *args[3:], [GRAPH]), 'unsupported')

    def test_malformed_nonfinite_and_unsupported_explicit_values_fail_closed(self):
        cases = ([f32(GAIN_KEY, math.nan)], [f32(GAIN_KEY, math.inf)],
                 [f32(GAIN_KEY, 4.0, size=8)], [f32(GAIN_KEY, 4.0, raw='not-hex')])
        for rows in cases:
            with self.subTest(rows=rows):
                archive = archive_for()
                graph_blob = archive.blobs[archive.resolve(GRAPH)[0]][4:]
                with (patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)),
                      self.assertRaises(ValueError)):
                    extract(archive, rows)

    def test_source_equation_contract_metadata(self):
        encoded = math.exp2(5 * (0.20000000298023224 * math.log2(1 + 0.5 * 4.0))) - 1
        self.assertAlmostEqual(encoded, 2.0, places=6)
        terms = tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})['constants']
        self.assertEqual(terms['f0'], [0.0, 0.0999755859375, 0.0999755859375])
        self.assertEqual(terms['gloss'], 0.0)

    def test_blender_constant_expectations_include_all_reticle_source_values(self):
        from rc2016.shaders.validation import _viewer_expected_constants

        archive = archive_for()
        graph_blob = archive.blobs[archive.resolve(GRAPH)[0]][4:]
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)):
            term_params = extract(archive)['forward']['params']
        expected = _viewer_expected_constants({
            'ported': ['victor_reticle'],
            'terms': {'victor_reticle': {'params': term_params}},
        })
        self.assertEqual(expected['VR Victor reticle EB9C8254 F0'], term_params['f0'])
        self.assertEqual(expected['VR Victor reticle EB9C8254 gloss'], term_params['gloss'])
        self.assertEqual(expected['VR Victor reticle CB2[0] material UV phase'], term_params['uv_phase'])
        self.assertEqual(expected['VR Victor reticle CB2[1] API8 gain'], term_params['texture_gain'])
        self.assertEqual(expected['VR Victor reticle CB2[2] ring tint'], term_params['ring_tint'])
        self.assertEqual(expected['VR Victor reticle CB0[115] presentation amplitude'], term_params['amplitude'])


class VictorReticleBindingTests(unittest.TestCase):
    def test_api8_alias_preserves_regular_c_and_other_sampler_units(self):
        from types import SimpleNamespace
        from rc2016.levels.viewport import _Recorder
        from rc2016.viewer.shading import OPAQUE_UNIFORMS
        from rc2016.viewer.viewport import ModelViewport

        recorder = _Recorder(object(), object())
        view = SimpleNamespace(_gl=recorder.gl, _shading=recorder.shading, native_translation=False,
                               GL_TEXTURE0=0x84C0, GL_TEXTURE_2D=0x0DE1)
        view._selected_surface = lambda entry, level: (None, {}, False)
        uniforms = {name: i for i, name in enumerate(OPAQUE_UNIFORMS)}
        # Existing environment unit 6 is not touched by per-material binding; puddle atlas retains unit 14.
        regular = dict(_c=(41, False), _puddle_atlas=(74, False))
        ModelViewport._apply_material(view, uniforms, regular, 2)
        unit0 = [op[3] for op in recorder.ops if op[0] == 't' and op[1] == 0x84C0 and op[2] == 0x0DE1]
        self.assertEqual(unit0, [41])
        self.assertFalse(any(op[0] == 't' and op[1] == 0x84C0 + 6 for op in recorder.ops))
        self.assertIn(('t', 0x84C0 + 14, 0x0DE1, 74), recorder.ops)
        self.assertFalse([op for op in recorder.ops if op[0] == 'u' and op[1] == uniforms['uVictorReticle'] and op[3] == (1,)])

        recorder.ops.clear()
        reticle = dict(forward={'kind': 'victor_reticle', 'params': {
            'uv_phase': 0.0, 'texture_gain': 4.0, 'ring_tint': 0.0, 'amplitude': 0.5}},
            _reticle=(88, False), _puddle_atlas=(74, False))
        ModelViewport._apply_material(view, uniforms, reticle, 2)
        unit0 = [op[3] for op in recorder.ops if op[0] == 't' and op[1] == 0x84C0 and op[2] == 0x0DE1]
        self.assertEqual(unit0, [0, 88])
        self.assertIn(('t', 0x84C0 + 14, 0x0DE1, 74), recorder.ops)
        self.assertTrue(any(op[0] == 'u' and op[1] == uniforms['uHasReticle'] and op[3] == (1,) for op in recorder.ops))
        self.assertTrue(any(op[0] == 'u' and op[1] == uniforms['uVictorReticle'] and op[3] == (1,) for op in recorder.ops))


if __name__ == '__main__':
    unittest.main()
