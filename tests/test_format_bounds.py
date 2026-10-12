"""Bounds checks for the outer-asset LZ4/zlib decoder and the 1TAD container reader.

All inputs are synthetic byte strings built here; no game bytes are embedded.
"""
import struct
import unittest
import zlib

from rc2016.formats import archive, container


def lz4_block_python(data, size):
    return archive._lz4_block_python(data, size)


def outer_header(built_size, appended_size, body):
    return b'\0\0\0\0' + struct.pack('<II', built_size, appended_size) + body


def make_container(sections, flags=0, declared_size=None, section_count=None):
    """A 1TAD container. ``sections`` is a list of (label_hash, payload)."""
    count = len(sections) if section_count is None else section_count
    table_end = 16 + 12 * len(sections)
    payload = bytearray()
    entries = []
    for label, body in sections:
        entries.append(struct.pack('<III', label, table_end + len(payload), len(body)))
        payload += body
    total = table_end + len(payload)
    header = b'1TAD' + struct.pack('<II', 0, total if declared_size is None else declared_size)
    header += struct.pack('<HH', count, flags)
    return header + b''.join(entries) + bytes(payload)


class Lz4BlockTests(unittest.TestCase):
    def test_literal_only_block(self):
        self.assertEqual(lz4_block_python(b'\x30abc', 3), b'abc')

    def test_overlapping_backreference_repeats_last_byte(self):
        # One literal 'a', then a match of length 4 at distance 1.
        self.assertEqual(lz4_block_python(b'\x10a\x01\x00', 5), b'aaaaa')

    def test_zero_distance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'backreference'):
            lz4_block_python(b'\x10a\x00\x00', 5)

    def test_distance_before_start_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'backreference'):
            lz4_block_python(b'\x10a\x05\x00', 5)

    def test_literal_longer_than_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Literal exceeds'):
            lz4_block_python(b'\x50abc', 5)

    def test_literal_longer_than_declared_size_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Literal exceeds'):
            lz4_block_python(b'\x30abc', 2)

    def test_truncated_literal_length_extension(self):
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            lz4_block_python(b'\xf0', 100)

    def test_truncated_match_distance(self):
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            lz4_block_python(b'\x10a\x01', 5)

    def test_decoded_length_must_match_header(self):
        with self.assertRaisesRegex(ValueError, 'differs from header'):
            lz4_block_python(b'\x30abc', 4)

    def test_declared_size_bounds(self):
        with self.assertRaisesRegex(ValueError, 'Invalid/unsupported'):
            archive.lz4_block(b'\x30abc', 0)
        with self.assertRaisesRegex(ValueError, 'Invalid/unsupported'):
            archive.lz4_block(b'\x30abc', 17, maximum=16)


class OuterDecompressTests(unittest.TestCase):
    def test_uncompressed_magics_pass_through(self):
        data = b'1TAD' + b'\0' * 20
        self.assertEqual(archive.decompress(data), (data, 'uncompressed'))

    def test_truncated_outer_header(self):
        with self.assertRaisesRegex(ValueError, 'Truncated outer'):
            archive.decompress(b'\0' * 8)

    def test_declared_size_over_maximum(self):
        data = outer_header(10, 0, b'\0' * 40)
        with self.assertRaisesRegex(ValueError, 'Invalid/unsupported'):
            archive.decompress(data, maximum=5)

    def test_zlib_wrapper_round_trip(self):
        payload = b'hello world'
        data = outer_header(len(payload), 0, zlib.compress(payload))
        self.assertEqual(archive.decompress(data), (payload, 'ig-tools-zlib-12'))

    def test_zlib_size_mismatch_is_rejected(self):
        data = outer_header(3, 0, zlib.compress(b'hello'))
        with self.assertRaisesRegex(ValueError, 'Zlib size'):
            archive.decompress(data)

    def test_unsupported_compression_header_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported outer'):
            archive.decompress(outer_header(4, 0, b'\0' * 10))


class ContainerTests(unittest.TestCase):
    def test_section_table_reports_container_relative_offsets(self):
        blob = make_container([(0x11111111, b'ABCD'), (0x22222222, b'EF')])
        padded = b'\xAA' * 32 + blob
        sections = container.section_table(padded, start=32)
        self.assertEqual([s['label_hash'] for s in sections], ['0x11111111', '0x22222222'])
        self.assertEqual([s['size'] for s in sections], [4, 2])
        self.assertEqual(padded[sections[0]['offset']:sections[0]['offset'] + 4], b'ABCD')

    def test_light_read_has_no_hashes(self):
        blob = make_container([(0x11111111, b'ABCD')])
        result = container.read_container(blob, 0, {}, light=True)
        self.assertEqual(result['section_count'], 1)
        self.assertNotIn('sha256_16', result['sections'][0])

    def test_full_read_of_plain_container(self):
        blob = make_container([(0x11111111, b'ABCD')])
        result = container.read_container(blob, 0, {})
        self.assertIsNone(result['metadata'])
        self.assertEqual(result['bone_hashes'], [])
        self.assertTrue(result['sections'][0]['in_bounds'])

    def test_bad_magic(self):
        with self.assertRaisesRegex(ValueError, 'does not begin with 1TAD'):
            container.section_table(b'XXXX' + b'\0' * 20)

    def test_truncated_header(self):
        with self.assertRaisesRegex(ValueError, 'truncated header'):
            container.section_table(b'1TAD' + b'\0' * 6)

    def test_declared_size_beyond_file(self):
        blob = make_container([(0x11111111, b'ABCD')], declared_size=10_000)
        with self.assertRaisesRegex(ValueError, 'invalid container size'):
            container.section_table(blob)

    def test_declared_size_smaller_than_header(self):
        blob = make_container([], declared_size=8)
        with self.assertRaisesRegex(ValueError, 'invalid container size'):
            container.section_table(blob)

    def test_section_table_larger_than_container(self):
        blob = make_container([], section_count=500)
        with self.assertRaisesRegex(ValueError, 'truncated section table'):
            container.section_table(blob)

    def test_section_overlapping_table_is_rejected(self):
        blob = bytearray(make_container([(0x11111111, b'ABCD')]))
        struct.pack_into('<I', blob, 16 + 4, 0)   # relative offset 0 points into the header
        with self.assertRaisesRegex(ValueError, 'outside container payload'):
            container.section_table(bytes(blob))

    def test_section_past_container_end_is_rejected(self):
        blob = bytearray(make_container([(0x11111111, b'ABCD')]))
        struct.pack_into('<I', blob, 16 + 8, 100)   # size larger than the payload
        with self.assertRaisesRegex(ValueError, 'outside container payload'):
            container.read_container(bytes(blob), 0, {})

    def test_truncated_clip_metadata_section(self):
        blob = make_container([(0x9DF23F77, b'\0' * 40)])
        with self.assertRaisesRegex(ValueError, 'truncated clip metadata'):
            container.read_container(blob, 0, {})

    def test_unaligned_bone_section_is_ignored(self):
        blob = make_container([(0xA3B26640, b'\x01\x02\x03\x04\x05\x06')])
        self.assertEqual(container.read_container(blob, 0, {})['bone_hashes'], [])


if __name__ == '__main__':
    unittest.main()
