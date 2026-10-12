"""Bounds checks for zone DAT1 section tables and decal decoding.

Synthetic containers only; no game bytes are embedded.
"""
import struct
import unittest

from rc2016.levels import zone_format as zf


def dat1(sections):
    """A 1TAD container; ``sections`` is a list of (tag, payload bytes)."""
    table_end = 16 + 12 * len(sections)
    body = bytearray()
    entries = []
    for tag, payload in sections:
        entries.append(struct.pack('<III', tag, table_end + len(body), len(payload)))
        body += payload
    header = b'1TAD' + struct.pack('<II', 0, table_end + len(body)) + struct.pack('<I', len(sections))
    return header + b''.join(entries) + bytes(body)


class SectionTableTests(unittest.TestCase):
    def test_round_trip(self):
        data = dat1([(zf.DATA_HEAP, b'\0' * 8)])
        self.assertEqual(zf.sections(data), {zf.DATA_HEAP: (16 + 12, 8)})

    def test_short_header(self):
        with self.assertRaisesRegex(ValueError, 'Not a DAT1'):
            zf.sections(b'1TAD\0\0')

    def test_table_larger_than_file(self):
        data = b'1TAD' + struct.pack('<II', 0, 0) + struct.pack('<I', 1000) + b'\0' * 8
        with self.assertRaisesRegex(ValueError, 'Truncated DAT1 section table'):
            zf.sections(data)


class DecalBoundsTests(unittest.TestCase):
    def test_decals_without_offsets_section_is_empty(self):
        self.assertEqual(zf.decode_decals(dat1([(zf.DATA_HEAP, b'\0' * 8)]), dat1([])), [])

    def test_missing_heap_is_value_error(self):
        zone = dat1([(zf.DECAL_OFFSETS, b'\0' * 4)])
        with self.assertRaisesRegex(ValueError, 'data heap'):
            zf.decode_decals(zone, dat1([]))

    def test_missing_static_mesh_section_is_value_error(self):
        zone = dat1([(zf.DATA_HEAP, b'\0' * 8), (zf.DECAL_OFFSETS, b'\0' * 4)])
        with self.assertRaisesRegex(ValueError, 'decal mesh section'):
            zf.decode_decals(zone, dat1([(0x12345678, b'\0' * 4)]))

    def test_static_mesh_section_past_end_of_file(self):
        zone = dat1([(zf.DATA_HEAP, b'\0' * 8), (zf.DECAL_OFFSETS, b'\0' * 4)])
        static = bytearray(dat1([(zf.DECAL_MESHES, b'\0' * 0x58)]))
        # Point the mesh section past the end of the file, keeping its declared size.
        struct.pack_into('<I', static, 16 + 4, len(static) + 100)
        with self.assertRaisesRegex(ValueError, 'outside file'):
            zf.decode_decals(zone, bytes(static))


if __name__ == '__main__':
    unittest.main()
