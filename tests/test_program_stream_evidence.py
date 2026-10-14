"""Per-pixel squared-stream provenance using synthetic source headers and IR."""
import unittest
from unittest import mock

from rc2016.shaders import bindings, interpolants
from rc2016.shaders.ir import DAG


def _channel_ir(source, ir=None):
    """Four export channels; source maps channel -> (vertex semantic, lane)."""
    ir = ir or DAG()
    channels = []
    for channel in range(4):
        if channel not in source:
            channels.append(ir.node('unsupported', reason='synthetic unproved lane'))
            continue
        semantic, lane = source[channel]
        value = ir.node('input', name=f'vertex.semantic{semantic}.{"xyzw"[lane]}',
                        kind='vertex_input', semantic=semantic, channel=lane)
        channels.append(ir.op('mul', value, value))
    return channels, ir


def _vertex_result(params, ir):
    """Match vertex_exports: ((params_by_slot, DAG), None)."""
    return (params, ir), None


class SquaredStreamEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.pixels = [
            dict(type=interpolants.PIXEL, header_offset=100,
                 inputs=[dict(semantic=15), dict(semantic=17)]),
            dict(type=interpolants.PIXEL, header_offset=200,
                 inputs=[dict(semantic=17), dict(semantic=15)]),
        ]
        self.vs = [
            dict(type=1, header_offset=300, layout='verified',
                 exports=[dict(semantic=15, slot=0), dict(semantic=17, slot=1)]),
            dict(type=1, header_offset=400, layout='verified',
                 exports=[dict(semantic=17, slot=2), dict(semantic=15, slot=3)]),
        ]
        self.headers = self.pixels + self.vs
        self.ir_a = DAG()
        slot15_a, _ = _channel_ir({0: (6, 0)}, self.ir_a)
        slot17_a, _ = _channel_ir({0: (5, 0), 1: (5, 1)}, self.ir_a)
        self.ir_b = DAG()
        slot17_b, _ = _channel_ir({0: (5, 0), 1: (5, 1)}, self.ir_b)
        slot15_b, _ = _channel_ir({0: (6, 0)}, self.ir_b)
        self.exports = {
            300: _vertex_result({0: slot15_a, 1: slot17_a}, self.ir_a),
            400: _vertex_result({2: slot17_b, 3: slot15_b}, self.ir_b),
        }

    def _patched_analysis(self, headers=None):
        calls = []
        def vertex_exports(row):
            calls.append(row['header_offset'])
            return self.exports.get(row['header_offset'], (None, 'synthetic failure'))
        return mock.patch.object(interpolants, 'shader_headers', return_value=headers or self.headers), \
               mock.patch.object(interpolants, 'vertex_exports', side_effect=vertex_exports), calls

    def test_reordered_pixel_semantics_remain_keyed_to_header(self):
        patch_headers, patch_exports, calls = self._patched_analysis()
        with patch_headers, patch_exports:
            common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        self.assertEqual(by_header[100][(1, 0)], (5, 0))
        self.assertEqual(by_header[100][(1, 1)], (5, 1))
        self.assertEqual(by_header[200][(0, 0)], (5, 0))
        self.assertEqual(by_header[200][(0, 1)], (5, 1))
        self.assertEqual(by_header[100][(0, 0)], (6, 0))
        self.assertEqual(by_header[200][(1, 0)], (6, 0))
        self.assertEqual(common, {})  # no same-index semantic agrees across variants
        self.assertCountEqual(calls, [300, 400])  # compile/cache each VS row only once

    def test_common_mapping_is_cross_header_agreement_and_compat_returns_it(self):
        pixels = [
            dict(type=interpolants.PIXEL, header_offset=100, inputs=[dict(semantic=17)]),
            dict(type=interpolants.PIXEL, header_offset=200, inputs=[dict(semantic=17)]),
        ]
        headers = pixels + [dict(type=1, header_offset=300, layout='verified',
                                 exports=[dict(semantic=17, slot=0)])]
        rows, ir = _channel_ir({0: (5, 0), 1: (5, 1)})
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
             mock.patch.object(interpolants, 'vertex_exports', return_value=_vertex_result({0: rows}, ir)):
            common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        expected = {(0, 0): (5, 0), (0, 1): (5, 1)}
        self.assertEqual(common, expected)
        self.assertEqual(by_header, {100: expected, 200: expected})
        with mock.patch.object(interpolants, 'squared_stream_evidence', return_value=(common, by_header)):
            self.assertEqual(interpolants.squared_stream_channels(b'synthetic graph'), common)

    def test_disagreement_between_matching_vertex_programs_rejects_lanes(self):
        other, _ = _channel_ir({0: (5, 2), 1: (5, 1)}, self.ir_b)
        self.exports[400][0][0][2] = other
        headers = [self.pixels[0], self.vs[0], self.vs[1]]
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
             mock.patch.object(interpolants, 'vertex_exports', side_effect=lambda row: self.exports[row['header_offset']]):
            _common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        self.assertNotIn((1, 0), by_header.get(100, {}))
        self.assertEqual(by_header[100][(1, 1)], (5, 1))
        self.assertEqual(by_header[100][(0, 0)], (6, 0))

    def test_duplicate_matching_exports_must_agree(self):
        ir = DAG()
        good, _ = _channel_ir({0: (5, 0), 1: (5, 1)}, ir)
        duplicate, _ = _channel_ir({0: (5, 0), 1: (5, 1)}, ir)
        headers = [self.pixels[0], dict(type=1, header_offset=300, layout='verified',
            exports=[dict(semantic=17, slot=1), dict(semantic=17, slot=4)])]
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
             mock.patch.object(interpolants, 'vertex_exports', return_value=_vertex_result({1: good, 4: duplicate}, ir)):
            _common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        self.assertEqual(by_header[100][(1, 0)], (5, 0))
        self.assertEqual(by_header[100][(1, 1)], (5, 1))

        disagree, _ = _channel_ir({0: (5, 2), 1: (5, 1)}, ir)
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
             mock.patch.object(interpolants, 'vertex_exports', return_value=_vertex_result({1: good, 4: disagree}, ir)):
            _common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        self.assertNotIn((1, 0), by_header.get(100, {}))
        self.assertEqual(by_header[100][(1, 1)], (5, 1))

    def test_unverified_or_unresolved_matching_exporter_rejects_lane(self):
        pixel = self.pixels[0]
        verified = dict(type=1, header_offset=300, layout='verified',
                        exports=[dict(semantic=17, slot=0)])
        uncertain = dict(type=1, header_offset=400, layout='unverified',
                         exports=[dict(semantic=17, slot=1)])
        good, ir = _channel_ir({0: (5, 0)})
        headers = [pixel, verified, uncertain]
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
             mock.patch.object(interpolants, 'vertex_exports', return_value=_vertex_result({0: good}, ir)):
            _common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        self.assertNotIn((1, 0), by_header.get(100, {}))

        uncertain['layout'] = 'verified'
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
             mock.patch.object(interpolants, 'vertex_exports', side_effect=[_vertex_result({0: good}, ir), (None, 'decode failure')]):
            _common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        self.assertNotIn((1, 0), by_header.get(100, {}))

    def test_nonmatching_unverified_vertex_row_does_not_poison_evidence(self):
        pixel = self.pixels[0]
        matching = dict(type=1, header_offset=300, layout='verified',
                        exports=[dict(semantic=17, slot=0)])
        unrelated = dict(type=1, header_offset=400, layout='unverified',
                         exports=[dict(semantic=99, slot=7)])
        channels, ir = _channel_ir({0: (5, 0)})
        with mock.patch.object(interpolants, 'shader_headers', return_value=[pixel, matching, unrelated]), \
             mock.patch.object(interpolants, 'vertex_exports', return_value=_vertex_result({0: channels}, ir)):
            _common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        self.assertEqual(by_header[100][(1, 0)], (5, 0))

    def test_unknown_unverified_vertex_layout_blocks_every_pixel_mapping(self):
        pixels = [
            dict(type=interpolants.PIXEL, header_offset=100, inputs=[dict(semantic=17)]),
            dict(type=interpolants.PIXEL, header_offset=200, inputs=[dict(semantic=17)]),
        ]
        verified = dict(type=1, header_offset=300, layout='verified',
                        exports=[dict(semantic=17, slot=0)])
        unknown = dict(type=1, header_offset=400, layout='unverified', exports=[])
        channels, ir = _channel_ir({0: (5, 0), 1: (5, 1)})
        headers = pixels + [verified, unknown]
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
             mock.patch.object(interpolants, 'vertex_exports', return_value=_vertex_result({0: channels}, ir)):
            common, by_header = interpolants.squared_stream_evidence(b'synthetic graph')
        self.assertEqual(set(by_header), {100, 200})
        self.assertEqual(by_header, {100: {}, 200: {}})
        self.assertEqual(common, {})
    def test_graph_stream_evidence_requires_exact_pixel_header(self):
        common = {(0, 0): (5, 0)}
        by_header = {100: {(1, 0): (5, 1)}, 200: {(0, 0): (6, 0)}}
        bindings._stream_evidence.cache_clear()
        self.addCleanup(bindings._stream_evidence.cache_clear)
        with mock.patch.object(bindings, '_stream_evidence', return_value=(set(), common, by_header)):
            normals, missing = bindings.graph_stream_evidence(
                b'synthetic graph', program={'header_offset': 999})
            self.assertEqual((normals, missing), (set(), {}))
            _normals, exact = bindings.graph_stream_evidence(
                b'synthetic graph', program={'header_offset': 200})
            self.assertEqual(exact, {(0, 0): (6, 0)})


if __name__ == '__main__':
    unittest.main()
