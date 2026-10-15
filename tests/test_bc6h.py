"""Unsigned BC6H single-subset decoding (rc2016/formats/bc6h.py) on hand-packed blocks."""
import unittest

import numpy as np

from rc2016.formats.bc6h import WEIGHTS_4, decode_bc6h


def pack(fields):
    """128-bit little-endian block from (start bit, width, value) triples."""
    bits = 0
    for start, width, value in fields:
        if value < 0 or value >= 1 << width:
            raise ValueError('field value does not fit')
        bits |= value << start
    return bits.to_bytes(16, 'little')


def indices_field(indices):
    """Index stream of a single-subset block starting at bit 65: 3 bits for pixel 0, 4 for the rest."""
    value, offset = indices[0], 3
    for pixel in indices[1:]:
        value |= pixel << offset
        offset += 4
    return 65, 63, value


def half(bits):
    return float(np.array([bits], np.uint16).view(np.float16)[0])


def finish(value16):
    return half((value16 * 31) >> 6)


def unquantize(value, bits):
    if value == 0:
        return 0
    if value == (1 << bits) - 1:
        return 0xFFFF
    return ((value << 15) + 0x4000) >> (bits - 1)


class Mode11Tests(unittest.TestCase):
    def block(self, e0, e1, indices):
        return pack([(0, 5, 0b00011), (5, 10, e0[0]), (15, 10, e0[1]), (25, 10, e0[2]),
                     (35, 10, e1[0]), (45, 10, e1[1]), (55, 10, e1[2]), indices_field(indices)])

    def test_endpoints_and_index_weights(self):
        e0, e1 = (0, 512, 1023), (1023, 512, 0)
        indices = list(range(8)) + list(range(8, 16))
        image, unsupported = decode_bc6h(self.block(e0, e1, indices), 4, 4)
        self.assertFalse(unsupported.any())
        self.assertEqual(image.shape, (4, 4, 3))
        for pixel, index in enumerate(indices):
            weight = WEIGHTS_4[index]
            for channel in range(3):
                a, b = unquantize(e0[channel], 10), unquantize(e1[channel], 10)
                expected = finish((a * (64 - weight) + b * weight + 32) >> 6)
                self.assertEqual(image[pixel // 4, pixel % 4, channel], np.float32(expected))

    def test_extremes_are_zero_and_half_float_maximum(self):
        image, _ = decode_bc6h(self.block((0, 0, 0), (1023, 1023, 1023), [0] * 15 + [15]), 4, 4)
        self.assertEqual(image[0, 0].tolist(), [0.0, 0.0, 0.0])
        self.assertEqual(image[3, 3].tolist(), [65504.0] * 3)

    def test_pixel_zero_index_has_three_bits(self):
        # With the implicit high bit, pixel 0 can reach index 7 but never 8 or more.
        image, _ = decode_bc6h(self.block((0, 0, 0), (1023, 1023, 1023), [7] + [0] * 15), 4, 4)
        weight = WEIGHTS_4[7]
        expected = finish((0xFFFF * weight + 32) >> 6)
        self.assertEqual(image[0, 0, 0], np.float32(expected))


class Mode12Tests(unittest.TestCase):
    def test_delta_endpoint_and_eleventh_bit(self):
        base = (1030, 1500, 700)             # 11-bit endpoint 0
        delta = (-5, 7, 0)                   # 9-bit signed deltas, stored two's complement
        stored = [d & 0x1FF for d in delta]
        block = pack([(0, 5, 0b00111),
                      (5, 10, base[0] & 0x3FF), (15, 10, base[1] & 0x3FF), (25, 10, base[2] & 0x3FF),
                      (35, 9, stored[0]), (44, 1, base[0] >> 10), (45, 9, stored[1]), (54, 1, base[1] >> 10),
                      (55, 9, stored[2]), (64, 1, base[2] >> 10), indices_field([0] * 15 + [15])])
        image, unsupported = decode_bc6h(block, 4, 4)
        self.assertFalse(unsupported.any())
        for channel in range(3):
            first = finish(unquantize(base[channel], 11))
            last = finish(unquantize((base[channel] + delta[channel]) & 0x7FF, 11))
            self.assertEqual(image[0, 0, channel], np.float32(first))
            self.assertEqual(image[3, 3, channel], np.float32(last))


class Mode13Tests(unittest.TestCase):
    def test_top_base_bits_are_stored_reversed(self):
        base = (0b1000_0000_0001, 0b0100_0000_0010, 0b1100_0000_0011)   # 12-bit endpoint 0
        delta = (3, -4, 0)
        stored = [d & 0xFF for d in delta]

        def top(value):                      # bits 11 and 10 of the base, bit 11 stored first
            return (value >> 11 & 1) | (value >> 10 & 1) << 1

        block = pack([(0, 5, 0b01011),
                      (5, 10, base[0] & 0x3FF), (15, 10, base[1] & 0x3FF), (25, 10, base[2] & 0x3FF),
                      (35, 8, stored[0]), (43, 2, top(base[0])), (45, 8, stored[1]), (53, 2, top(base[1])),
                      (55, 8, stored[2]), (63, 2, top(base[2])), indices_field([0] * 15 + [15])])
        image, _ = decode_bc6h(block, 4, 4)
        for channel in range(3):
            self.assertEqual(image[0, 0, channel], np.float32(finish(unquantize(base[channel], 12))))
            self.assertEqual(image[3, 3, channel],
                             np.float32(finish(unquantize((base[channel] + delta[channel]) & 0xFFF, 12))))


class LayoutTests(unittest.TestCase):
    def test_unsupported_modes_are_zero_and_reported(self):
        two_subset = pack([(0, 2, 0b01)])            # mode 2
        mode_14 = pack([(0, 5, 0b01111)])            # not verified, so not decoded
        image, unsupported = decode_bc6h(two_subset + mode_14, 8, 4)
        self.assertEqual(unsupported.tolist(), [[True, True]])
        self.assertFalse(image.any())

    def test_blocks_fill_the_image_row_by_row(self):
        def constant(value):
            return pack([(0, 5, 0b00011), (5, 10, value), (15, 10, value), (25, 10, value),
                         (35, 10, value), (45, 10, value), (55, 10, value)])
        data = constant(1023) + constant(0) + constant(0) + constant(1023)
        image, unsupported = decode_bc6h(data, 8, 8)
        self.assertFalse(unsupported.any())
        self.assertEqual(image[0, 0, 0], 65504.0)
        self.assertEqual(image[0, 4, 0], 0.0)
        self.assertEqual(image[4, 0, 0], 0.0)
        self.assertEqual(image[7, 7, 0], 65504.0)

    def test_crops_to_the_requested_size_and_rejects_short_data(self):
        block = pack([(0, 5, 0b00011)])
        image, _ = decode_bc6h(block, 3, 2)
        self.assertEqual(image.shape, (2, 3, 3))
        with self.assertRaises(ValueError):
            decode_bc6h(block, 8, 4)


if __name__ == '__main__':
    unittest.main()
