"""HD top-mip transport check uses the tile-padded stream size, not the unpadded mip size.

Synthetic bytes only; the header parser is patched so no game data is needed.
"""
import os
import struct
import unittest
from unittest import mock

from rc2016.formats import texture, texture_transport


WIDTH, HEIGHT = 1920, 1080   # 480x270 block grid: 270 is not a multiple of 8


def block_format():
    return next(iter(texture.BLOCK_BYTES))


class HdTopMipTransportTests(unittest.TestCase):
    def setUp(self):
        self.fmt = block_format()
        self.size = texture.top_mip_size(WIDTH, HEIGHT, self.fmt)
        self.tiled = texture.tiled_top_mip_size(WIDTH, HEIGHT, self.fmt)
        self.assertGreater(self.tiled, self.size)   # the case under test
        self.stream = os.urandom(self.tiled)
        self.expected = texture.ps4_deswizzle_top(self.stream, WIDTH, HEIGHT, self.fmt)
        self.dds = bytearray(148 + self.size)
        self.dds[:4] = b'DDS '
        self.dds[84:88] = b'DX10'
        struct.pack_into('<II', self.dds, 12, HEIGHT, WIDTH)
        struct.pack_into('<I', self.dds, 128, self.fmt)
        self.dds[148:] = self.expected
        self.info = dict(format=self.fmt, hd_width=WIDTH, hd_height=HEIGHT, stream_size=self.tiled,
                         sd_mip_count=1, hd_mip_count=1, sd_mip_count_offset=0, hd_mip_count_offset=0)

    def outcome(self):
        with mock.patch.object(texture_transport, 'parse_header', return_value=self.info):
            return texture_transport.top_mip_outcome(b'built', self.stream, bytes(self.dds))

    def test_padded_raw_stream_matches_pipeline_dds(self):
        fields = self.outcome()['fields']
        self.assertNotEqual(fields.get('status'), 'failed', fields.get('reason'))
        self.assertEqual(fields['top_mip_bytes'], self.size)
        self.assertEqual(fields['source_top_mip_sha256'], fields['pipeline_top_mip_sha256'])

    def test_stream_shorter_than_tiled_mip_is_still_rejected(self):
        self.info['stream_size'] = self.tiled - 1
        short = self.stream[:-1]
        fields = self.outcome_with(short)['fields']
        self.assertEqual(fields['status'], 'failed')

    def outcome_with(self, stream):
        with mock.patch.object(texture_transport, 'parse_header', return_value=self.info):
            return texture_transport.top_mip_outcome(b'built', stream, bytes(self.dds))


if __name__ == '__main__':
    unittest.main()
