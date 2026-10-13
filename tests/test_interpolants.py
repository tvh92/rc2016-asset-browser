"""Pixel interpolants joined to vertex-shader parameter exports, on a synthetic graph."""
import struct
import unittest

from rc2016.shaders import gcn
from rc2016.shaders.interpolants import interpolant_sources, shader_headers
from rc2016.shaders.translator import SHADER_SECTION


def opcode(table, name):
    return gcn.enum_value(table, 'GCN_' + name)


def export(target, registers):
    word = 0xF8000000 | 0xf | (target << 4) | (1 << 11)
    return struct.pack('<Q', word | sum(r << (32 + 8 * j) for j, r in enumerate(registers)))


def envelope(kind, stage_size, usage_count, body, code):
    """Shdr header, stage block (common data first) and the code with its OrbShdr footer."""
    common = struct.pack('<II', (len(code) + 28) | (usage_count << 24), 0)
    stage = (common + body).ljust(stage_size, b'\0')
    header = b'Shdr' + b'\0' * 4 + bytes([kind, stage_size // 4]) + b'\0' * 6
    footer = b'OrbShdr\0' + struct.pack('<I', len(code) << 8) + b'\0' * 4 + struct.pack('<I', 0x1234abcd) + b'\0' * 8
    return header + stage + code + footer


def graph(vertex_inputs, vertex_exports, pixel_inputs, export_width=2):
    swappc = struct.pack('<I', (0x17d << 23) | (2 << 16) | (opcode('GcnOpcodeSOP1', 'S_SWAPPC_B64') << 8) | 0)
    code = swappc + export(32, [4, 5, 6, 7]) + export(33, [8, 8, 8, 8]) + \
        struct.pack('<I', (0x17f << 23) | (opcode('GcnOpcodeSOPP', 'S_ENDPGM') << 16))
    counts = bytes([len(vertex_inputs), len(vertex_exports), 0, 0])
    tables = struct.pack('<4B', 0x12, 0, 0, 0)  # fetch-shader usage slot
    tables += b''.join(struct.pack('<4B', s, v, n, 0) for s, v, n in vertex_inputs)
    tables += b''.join(struct.pack('<2B', s, slot).ljust(export_width, b'\0') for s, slot in vertex_exports)
    size = (0x28 + 4 + 4 * len(vertex_inputs) + export_width * len(vertex_exports) + 3) & ~3
    body = b'\0' * 28 + counts + tables  # 7 stage registers, counts at 0x24, slots at 0x28
    vertex = envelope(1, size, 1, body, code)
    pixel_body = b'\0' * 48 + bytes([len(pixel_inputs), 0, 0, 0]) + b''.join(struct.pack('<H', s) for s in pixel_inputs)
    pixel = envelope(2, (0x3c + 2 * len(pixel_inputs) + 3) & ~3, 0, pixel_body, b'\0' * 4)
    shaders = b'\0' * 4 + vertex + pixel
    data = struct.pack('<I', 0x44415431) + b'\0' * 12 + struct.pack('<III', SHADER_SECTION, 28, len(shaders)) + shaders
    return data[:8] + struct.pack('<IH', len(data), 1) + data[14:]


class InterpolantTests(unittest.TestCase):
    def test_vertex_colour_passthrough_is_traced(self):
        data = graph([(5, 4, 4)], [(9, 0), (10, 1)], [10, 9])
        vertex = [h for h in shader_headers(data) if h['type'] != 2][0]
        self.assertEqual((vertex['layout'], vertex['export_entry_bytes']), ('verified', 2))
        sources = interpolant_sources(data, {1})
        self.assertEqual(sources['attribute1']['semantics'], [9])
        self.assertEqual(sources['attribute1']['sources'],
                         ['0x1234abcd param0: vertex.semantic5.x, vertex.semantic5.y, vertex.semantic5.z, vertex.semantic5.w'])

    def test_wider_export_entries_are_detected(self):
        data = graph([(5, 4, 4)], [(9, 0), (10, 1)], [10, 9], export_width=4)
        vertex = [h for h in shader_headers(data) if h['type'] != 2][0]
        self.assertEqual(vertex['export_entry_bytes'], 4)

    def test_ambiguous_layout_is_not_trusted(self):
        # One export entry: 2- and 4-byte widths pad to the same stage size.
        data = graph([(5, 4, 4)], [(9, 0)], [9])
        vertex = [h for h in shader_headers(data) if h['type'] != 2][0]
        self.assertEqual(vertex['layout'], 'unverified')
        self.assertIn('layout unverified', interpolant_sources(data, {0})['attribute0']['sources'][0])


class RotatedStreamAttributeTests(unittest.TestCase):
    """Only a per-draw matrix applied to one vertex stream is read as a transformed stream."""

    def setUp(self):
        from rc2016.shaders.ir import DAG
        self.ir = DAG()

    def stream(self, s):
        return [self.ir.node('input', name=f'vertex.semantic{s}.{c}', kind='vertex_input', semantic=s, channel=j)
                for j, c in enumerate('xyz')]

    def word(self, w):
        return self.ir.node('unsupported', [], reason='Unresolved scalar memory load', offset=40, word=w, base=0,
                            load_offset=None)

    def rotate(self, vector, base=0):
        ir = self.ir
        decoded = [ir.op('sub', ir.op('mul', ir.const(2.), v), ir.const(1.)) for v in vector]
        return [ir.op('add', ir.op('add', ir.op('mul', self.word(base + r), decoded[0]),
                                   ir.op('mul', self.word(base + 4 + r), decoded[1])),
                      ir.op('mul', self.word(base + 8 + r), decoded[2])) for r in range(3)]

    def normalise(self, v):
        ir = self.ir
        scale = ir.op('rsqrt', ir.op('add', ir.op('add', ir.op('mul', v[0], v[0]), ir.op('mul', v[1], v[1])),
                                     ir.op('mul', v[2], v[2])))
        return [ir.op('mul', scale, x) for x in v]

    def check(self, channels):
        from unittest import mock
        from rc2016.shaders import interpolants
        headers = [dict(type=interpolants.PIXEL, inputs=[dict(semantic=15), dict(semantic=17)]),
                   dict(type=1, header_offset=0, layout='verified', exports=[dict(semantic=17, slot=2)])]
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
                mock.patch.object(interpolants, 'vertex_exports',
                                  return_value=(({2: list(channels) + [None]}, self.ir), None)):
            return interpolants.rotated_stream_attributes(b'')

    def flag(self, offset):
        return self.ir.node('input', name=f'runtime_flag@{offset}', kind='runtime_flag')

    def test_rotated_stream_is_found(self):
        self.assertEqual(self.check(self.rotate(self.stream(1))), {1: 1})

    def test_normalised_rotated_stream_is_found(self):
        self.assertEqual(self.check(self.normalise(self.rotate(self.stream(1)))), {1: 1})

    def test_flag_paths_of_the_same_stream_are_found(self):
        flag = self.flag(8)
        paths = zip(self.rotate(self.stream(1)), self.normalise(self.rotate(self.stream(1), 32)))
        self.assertEqual(self.check([self.ir.op('select', flag, a, b) for a, b in paths]), {1: 1})

    def test_camera_minus_position_is_not(self):
        ir = self.ir
        camera = [self.word(100 + j) for j in range(3)]
        position = self.rotate(self.stream(0))
        self.assertEqual(self.check(self.normalise([ir.op('sub', c, p) for c, p in zip(camera, position)])), {})

    def test_a_flag_path_from_another_source_rejects(self):
        ir = self.ir
        fetched = ir.node('unsupported', [], reason='BUFFER_LOAD_FORMAT_XYZ', offset=60)
        other = [ir.op('mul', self.word(200 + j), fetched) for j in range(3)]
        flag = self.flag(8)
        self.assertEqual(self.check([ir.op('select', flag, a, b) for a, b in zip(other, self.rotate(self.stream(1)))]), {})

    def test_mixed_streams_reject(self):
        ir = self.ir
        mixed = [ir.op('add', a, b) for a, b in zip(self.rotate(self.stream(1)), self.rotate(self.stream(2), 64))]
        self.assertEqual(self.check(mixed), {})

    def test_bending_by_another_term_rejects(self):
        ir = self.ir
        bend = ir.op('sat', self.word(300))
        self.assertEqual(self.check([ir.op('mul', bend, v) for v in self.rotate(self.stream(1))]), {})


class SquaredStreamTests(unittest.TestCase):
    def test_flag_path_to_a_squared_vertex_stream_is_found(self):
        from unittest import mock
        from rc2016.shaders import interpolants
        from rc2016.shaders.ir import DAG
        ir = DAG()
        w = ir.node('input', name='vertex.semantic5.w', kind='vertex_input', semantic=5, channel=3)
        flag = ir.node('input', name='runtime_flag@8', kind='runtime_flag')
        other = ir.node('unsupported', reason='TBUFFER_LOAD_FORMAT_X')
        picked = ir.op('select', flag, other, w)
        channels = [other, other, other, ir.op('mul', picked, picked)]
        headers = [dict(type=interpolants.PIXEL, inputs=[dict(semantic=15), dict(semantic=18)]),
                   dict(type=1, header_offset=0, layout='verified', exports=[dict(semantic=18, slot=3)])]
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
                mock.patch.object(interpolants, 'vertex_exports', return_value=(({3: channels}, ir), None)):
            self.assertEqual(interpolants.squared_stream_channels(b''), {(1, 3): (5, 3)})


if __name__ == '__main__':
    unittest.main()


class VectorIdentityTests(unittest.TestCase):
    """Linear forms say which stream and matrix make a normalised vector, per flag path."""

    def setUp(self):
        from rc2016.shaders.ir import DAG
        self.ir = ir = DAG()
        self.stream = lambda s: [ir.node('input', name=f'vertex.semantic{s}.{c}', kind='vertex_input',
                                         semantic=s, channel=j) for j, c in enumerate('xyz')]
        self.cb = lambda w: ir.node('input', name=f'CB1[{w}]', kind='cb', api=1, dword=w)

    def transform(self, vector, base):
        ir = self.ir
        rows = []
        for r in range(3):
            terms = [ir.op('mul', self.cb(base + 4 * r + k), vector[k]) for k in range(3)]
            rows.append(ir.op('add', ir.op('add', terms[0], terms[1]), terms[2]))
        return rows

    def normalise(self, vector):
        ir = self.ir
        dot = ir.op('add', ir.op('add', ir.op('mul', vector[0], vector[0]), ir.op('mul', vector[1], vector[1])),
                    ir.op('mul', vector[2], vector[2]))
        scale = ir.op('rsqrt', dot)
        return [ir.op('mul', scale, v) for v in vector]

    def test_matrix_and_stream_are_named_before_normalisation(self):
        from rc2016.shaders.interpolants import vector_identity
        groups = vector_identity(self.ir, self.normalise(self.transform(self.stream(1), 16)))
        self.assertEqual(len(groups), 1)
        settings, parts, atoms = groups[0]
        self.assertEqual(settings, ['no flags'])
        self.assertTrue(all(normalised for _, normalised in parts))
        self.assertEqual(parts[0][0], 'CB1[16]*vertex.semantic1.x + CB1[17]*vertex.semantic1.y + CB1[18]*vertex.semantic1.z')
        self.assertTrue(any(a.startswith('rsqrt#') for a in atoms))

    def test_two_normalised_vectors_from_different_streams_differ(self):
        from rc2016.shaders.interpolants import vector_identity
        first = vector_identity(self.ir, self.normalise(self.transform(self.stream(1), 16)))[0][1]
        second = vector_identity(self.ir, self.normalise(self.transform(self.stream(2), 16)))[0][1]
        self.assertNotEqual(first, second)

    def test_runtime_flag_paths_are_reported_separately(self):
        from rc2016.shaders.interpolants import vector_identity
        ir = self.ir
        flag = ir.node('input', name='runtime_flag@40', kind='runtime_flag')
        world = self.transform(self.stream(1), 16)
        view = self.transform(self.stream(1), 32)
        chosen = self.normalise([ir.op('select', flag, a, b) for a, b in zip(world, view)])
        groups = vector_identity(ir, chosen)
        self.assertEqual(sorted(g[0][0] for g in groups), ['40=0', '40=1'])
        forms = {g[0][0]: g[1][0][0] for g in groups}
        self.assertIn('CB1[16]', forms['40=1'])
        self.assertIn('CB1[32]', forms['40=0'])

    def test_report_lists_inputs_and_attribute_forms(self):
        from rc2016.shaders.interpolants import attribute_report
        lines = attribute_report(graph([(5, 4, 4)], [(9, 0), (10, 1)], [10, 9]))
        self.assertIn('vertex 0x1234abcd: inputs semantic5[4]; exports semantic9->param0, semantic10->param1', lines)
        self.assertIn('attribute1 (PS semantics [9])', lines)
        self.assertIn('    x = vertex.semantic5.x', lines)

    def test_unresolved_loads_are_named_by_register_and_byte(self):
        from rc2016.shaders.interpolants import linear_form, format_form
        ir = self.ir
        words = [ir.node('unsupported', [], reason='Unresolved scalar memory load', offset=40, word=j,
                         base=8, load_offset=64) for j in range(2)]
        self.assertEqual(format_form(linear_form(ir, ir.op('sub', words[0], words[1]))),
                         'mem(s8+64)@40 - mem(s8+68)@40')

    def test_many_flags_expand_symbolically_to_the_same_paths(self):
        from rc2016.shaders.interpolants import vector_identity
        ir = self.ir
        flag = ir.node('input', name='runtime_flag@40', kind='runtime_flag')
        world = self.transform(self.stream(1), 16)
        view = self.transform(self.stream(1), 32)
        chosen = [ir.op('select', flag, a, b) for a, b in zip(world, view)]
        enumerated = {g[0][0]: g[1] for g in vector_identity(ir, chosen)}
        symbolic = {g[0][0]: g[1] for g in vector_identity(ir, chosen, max_flags=0)}
        self.assertEqual(enumerated, symbolic)

    def test_flag_logic_is_exact_on_zero_one_values(self):
        from rc2016.shaders.interpolants import linear_form, format_form
        ir = self.ir
        f, g = (ir.node('input', name=f'runtime_flag@{k}', kind='runtime_flag') for k in (1, 2))
        either = ir.op('max', f, g)
        self.assertEqual(format_form(linear_form(ir, ir.op('mul', f, f))), 'runtime_flag@1')
        self.assertEqual(format_form(linear_form(ir, either)),
                         'runtime_flag@1 + runtime_flag@2 - runtime_flag@1*runtime_flag@2')
