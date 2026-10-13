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


class NormalisedAttributeTests(unittest.TestCase):
    def check(self, unit):
        from unittest import mock
        from rc2016.shaders import interpolants
        from rc2016.shaders.ir import DAG
        ir = DAG()
        v = [ir.node('input', name=f'n{j}', kind='test') for j in range(3)]
        scale = ir.op('rsqrt', ir.op('add', ir.op('mul', v[0], v[0]), ir.op('mul', v[1], v[1])))
        channels = [ir.op('mul', scale, x) if unit else x for x in v] + [None]
        headers = [dict(type=interpolants.PIXEL, inputs=[dict(semantic=15), dict(semantic=17)]),
                   dict(type=1, header_offset=0, layout='verified', exports=[dict(semantic=17, slot=2)])]
        with mock.patch.object(interpolants, 'shader_headers', return_value=headers), \
                mock.patch.object(interpolants, 'vertex_exports', return_value=(({2: channels}, ir), None)):
            return interpolants.normalised_attributes(b'')

    def test_rsqrt_normalised_export_is_reported(self):
        self.assertEqual(self.check(True), {1})

    def test_plain_export_is_not(self):
        self.assertEqual(self.check(False), set())


if __name__ == '__main__':
    unittest.main()
