"""Exact source-pass joins and bounded runtime-flag square proofs."""
import struct
import unittest
from unittest import mock

from rc2016.shaders import interpolants
from rc2016.shaders.ir import DAG


def _channel_ir(flags, *, square_semantic=5, square_lane=3, alternate=None):
    """One output channel is square only under all-one flag assignment."""
    ir = DAG()
    source = ir.node('input', name=f'vertex.semantic{square_semantic}.{"xyzw"[square_lane]}',
                     kind='vertex_input', semantic=square_semantic, channel=square_lane)
    square = ir.op('mul', source, source)
    alternate_node = ir.node('unsupported', reason='unresolved branch source')
    if alternate is not None:
        semantic, lane = alternate
        other = ir.node('input', name=f'vertex.semantic{semantic}.{"xyzw"[lane]}',
                        kind='vertex_input', semantic=semantic, channel=lane)
        alternate_node = ir.op('mul', other, other)
    result = square
    for flag_number in reversed(range(flags)):
        flag = ir.node('input', name=f'runtime_flag@{flag_number}', kind='runtime_flag')
        result = ir.op('select', flag, result, alternate_node)
    return ir, result


def _b6_section(headers, *, pass_by_header=None, duplicate=(), blob_size=128,
                blob_size_by_header=None, shader_size=0x4000):
    """Synthetic 20-byte B6 rows with 13-row pass groups and valid relative extents."""
    pass_by_header = pass_by_header or {}
    blob_size_by_header = blob_size_by_header or {}
    b6 = bytearray()
    header_offsets = list(dict.fromkeys(h['header_offset'] for h in headers))
    rows_per_pass = [[] for _ in range(max(pass_by_header.values(), default=0) + 1)]
    header_types = {h['header_offset']: h['type'] for h in headers}
    for header in header_offsets:
        pass_index = pass_by_header.get(header, 0)
        rows_per_pass[pass_index].append(header)
    data = bytearray(0x10000 + shader_size)
    # A minimal valid source Shdr envelope, sufficient for pointer and blob-extent checks.
    for header in header_offsets:
        data[header:header + 4] = b'Shdr'
        data[header + 8] = header_types[header]
        data[header + 9] = 2  # eight-byte stage header
        struct.pack_into('<I', data, header + 16, 0)  # declared code size
    # Each serialized pass occupies exactly 13 records; unused records have zero blob size.
    for rows in rows_per_pass:
        first_record = len(b6) // 20
        for header in rows:
            relative = header - 0x10000 - 36
            size = blob_size_by_header.get(header, blob_size)
            b6 += struct.pack('<5I', 0, 0, 0, relative, size)
            if header in duplicate:
                b6 += struct.pack('<5I', 0, 0, 0, relative, size)
        used = len(b6) // 20 - first_record
        b6.extend(b'\0' * max(0, 13 - used) * 20)
    data[0x200:0x200 + len(b6)] = b6
    return bytes(data), {0xB6D49474: (0x200, len(b6)),
                         interpolants.SHADER_SECTION: (0x10000, shader_size)}, len(rows_per_pass)


class PassScopedEvidenceTests(unittest.TestCase):
    """Source envelope and pass associations must agree before adapting a stream."""

    def _call(self, headers, data, spans, exports, pass_count):
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
             mock.patch.object(interpolants, 'sections', return_value=spans), \
             mock.patch('rc2016.shaders.bindings.pass_contracts', return_value=[dict(index=i) for i in range(pass_count)]), \
             mock.patch.object(interpolants, 'vertex_exports', side_effect=lambda row: exports[row['header_offset']]):
            return interpolants.squared_stream_evidence(data)

    def test_vertex_conflict_in_another_pass_is_excluded(self):
        ps, good_vs, other_vs = 0x10000 + 36, 0x10000 + 0x100 + 36, 0x10000 + 0x200 + 36
        pixel = dict(type=interpolants.PIXEL, header_offset=ps, inputs=[dict(semantic=18)])
        good = dict(type=1, header_offset=good_vs, layout='verified', exports=[dict(semantic=18, slot=0)])
        unrelated_variant = dict(type=1, header_offset=other_vs, layout='verified', exports=[dict(semantic=18, slot=1)])
        ir_good, ch_good = _channel_ir(0)
        ir_other, ch_other = _channel_ir(0, square_semantic=9)
        exported = {good_vs: (({0: [ch_good]}, ir_good), None),
                    other_vs: (({1: [ch_other]}, ir_other), None)}
        headers = [pixel, good, unrelated_variant]
        data, spans, pass_count = _b6_section(headers, pass_by_header={ps: 0, good_vs: 0, other_vs: 1})
        _common, by_header = self._call(headers, data, spans, exported, pass_count)
        self.assertEqual(by_header[ps][(0, 0)], (5, 3))

    def test_same_pass_conflict_rejects_channel(self):
        ps, vs_a, vs_b = 0x10000 + 36, 0x10000 + 0x100 + 36, 0x10000 + 0x200 + 36
        pixel = dict(type=interpolants.PIXEL, header_offset=ps, inputs=[dict(semantic=18)])
        a = dict(type=1, header_offset=vs_a, layout='verified', exports=[dict(semantic=18, slot=0)])
        b = dict(type=1, header_offset=vs_b, layout='verified', exports=[dict(semantic=18, slot=1)])
        ir_a, ch_a = _channel_ir(0)
        ir_b, ch_b = _channel_ir(0, square_semantic=9)
        exported = {vs_a: (({0: [ch_a]}, ir_a), None), vs_b: (({1: [ch_b]}, ir_b), None)}
        headers = [pixel, a, b]
        data, spans, pass_count = _b6_section(headers)
        _common, by_header = self._call(headers, data, spans, exported, pass_count)
        self.assertNotIn((0, 0), by_header[ps])

    def test_unknown_b6_joins_fail_closed(self):
        ps, vs = 0x10000 + 36, 0x10000 + 0x100 + 36
        pixel = dict(type=interpolants.PIXEL, header_offset=ps, inputs=[dict(semantic=18)])
        vertex = dict(type=1, header_offset=vs, layout='verified', exports=[dict(semantic=18, slot=0)])
        ir, channel = _channel_ir(0)
        data, spans, pass_count = _b6_section([pixel], pass_by_header={ps: 0})
        exported = {vs: (({0: [channel]}, ir), None)}
        _common, by_header = self._call([pixel, vertex], data, spans, exported, pass_count)
        self.assertEqual(by_header.get(ps, {}), {})

    def test_duplicate_header_record_is_rejected(self):
        ps, vs = 0x10000 + 36, 0x10000 + 0x100 + 36
        pixel = dict(type=interpolants.PIXEL, header_offset=ps, inputs=[dict(semantic=18)])
        vertex = dict(type=1, header_offset=vs, layout='verified', exports=[dict(semantic=18, slot=0)])
        ir, channel = _channel_ir(0)
        headers = [pixel, vertex]
        data, spans, pass_count = _b6_section(headers, duplicate=(vs,))
        _common, by_header = self._call(headers, data, spans, {vs: (({0: [channel]}, ir), None)}, pass_count)
        self.assertEqual(by_header[ps], {})

    def test_out_of_blob_shader_header_is_rejected(self):
        ps = 0x10000 + 36
        pixel = dict(type=interpolants.PIXEL, header_offset=ps, inputs=[dict(semantic=18)])
        data, spans, pass_count = _b6_section([pixel], blob_size_by_header={ps: 59}, shader_size=0x400)
        _common, by_header = self._call([pixel], data, spans, {}, pass_count)
        self.assertEqual(by_header[ps], {})


class BoundedFlagProofTests(unittest.TestCase):
    def test_eight_and_twelve_flag_cases_prove_reachable_square(self):
        for count in (8, 12):
            with self.subTest(flags=count):
                ir, channel = _channel_ir(count)
                self.assertEqual(interpolants._squared_stream(ir, channel), (5, 3))

    def test_more_than_twelve_flags_fail_closed(self):
        ir, channel = _channel_ir(13)
        self.assertIsNone(interpolants._squared_stream(ir, channel))

    def test_flagged_paths_with_conflicting_square_inputs_fail_closed(self):
        ir, channel = _channel_ir(8, alternate=(9, 3))
        self.assertIsNone(interpolants._squared_stream(ir, channel))


if __name__ == '__main__':
    unittest.main()
