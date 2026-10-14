"""SOPC literal dword boundaries in real GCN instruction streams."""
import struct
import unittest

from rc2016.shaders import gcn
from rc2016.shaders.translator import decoder


def sopc(src0, src1):
    opcode = gcn.enum_value("GcnOpcodeSOPC", "GCN_S_CMP_LG_U32")
    return (0x17E << 23) | (opcode << 16) | (src1 << 8) | src0


def sopp(name):
    opcode = gcn.enum_value("GcnOpcodeSOPP", "GCN_" + name)
    return (0x17F << 23) | (opcode << 16)


def words(*items):
    return b"".join(struct.pack("<I", item) for item in items)


class SopcLiteralBoundaryTests(unittest.TestCase):
    def test_src0_and_src1_literal_consume_one_word_before_sentinel(self):
        # Use a valid S_ENDPGM-shaped dword as the literal: if it is mistaken for
        # an instruction, the expected S_NOP sentinel starts at the wrong offset.
        literal = sopp("S_ENDPGM")
        sentinel = sopp("S_NOP")
        for src0, src1 in ((255, 4), (4, 255)):
            with self.subTest(src0=src0, src1=src1):
                encoded = sopc(src0, src1)
                code = words(encoded, literal, sentinel)
                decoded = decoder().decode_stream(code, 0)
                self.assertEqual([(row["relative"], row["length"], row["name"])
                                  for row in decoded],
                                 [(0, 8, "GCN_S_CMP_LG_U32"), (8, 4, "GCN_S_NOP")])
                self.assertEqual(struct.unpack_from("<I", code, decoded[0]["relative"] + 4)[0],
                                 literal)
                self.assertEqual(gcn.literal_count(2, encoded, encoded,
                                                   gcn.enum_value("GcnOpcodeSOPC", "GCN_S_CMP_LG_U32")), 1)

    def test_both_literal_selectors_share_one_extension_dword(self):
        encoded = sopc(255, 255)
        literal = sopp("S_ENDPGM")
        sentinel = sopp("S_NOP")
        code = words(encoded, literal, sentinel)
        decoded = decoder().decode_stream(code, 0)
        self.assertEqual(gcn.literal_count(2, encoded, encoded,
                                           gcn.enum_value("GcnOpcodeSOPC", "GCN_S_CMP_LG_U32")), 1)
        self.assertEqual([(row["relative"], row["length"], row["name"])
                          for row in decoded],
                         [(0, 8, "GCN_S_CMP_LG_U32"), (8, 4, "GCN_S_NOP")])
        self.assertEqual(struct.unpack_from("<I", code, 4)[0], literal)

    def test_truncated_sopc_literal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "truncated instruction at 0"):
            decoder().decode_stream(words(sopc(4, 255)), 0)

    def test_actual_graph_sopc_without_literal_keeps_four_byte_boundary(self):
        # Raw word from the retained dep_gnd_paint_numbers_01 source graph's
        # pixel program (shader 0xa1aaac65), at graph byte offset 1184 (code-relative 36). It compares
        # s106 against inline zero; neither source selects the literal dword.
        actual = 0xBF07806A
        sentinel = sopp("S_NOP")
        decoded = decoder().decode_stream(words(actual, sentinel), 0)
        self.assertEqual(decoded[0]["name"], "GCN_S_CMP_LG_U32")
        self.assertEqual(decoded[0]["length"], 4)
        self.assertEqual(decoded[1]["relative"], 4)
        self.assertEqual(decoded[1]["name"], "GCN_S_NOP")
        self.assertEqual(actual & 0xFF, 106)
        self.assertEqual((actual >> 8) & 0xFF, 128)


if __name__ == "__main__":
    unittest.main()
