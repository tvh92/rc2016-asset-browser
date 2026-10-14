"""Scalar memory addresses retain integer word values and diagnostic provenance."""
import struct
import unittest

from rc2016.shaders import gcn
from rc2016.shaders.translator import Compiler, decoder
from tests.test_gcn_control_flow import sop1, sop2, program


def load(dst, base, offset, immediate=False):
    op = gcn.enum_value("GcnOpcodeSMRD", "GCN_S_BUFFER_LOAD_DWORD")
    return struct.pack("<I", (0x18 << 27) | (op << 22) | (dst << 15) |
                       ((base // 2) << 9) | (int(immediate) << 8) | offset)


class ScalarLoadOffsetTests(unittest.TestCase):
    def test_integer_arithmetic_register_offset_selects_correct_constant_word(self):
        # Known integer arithmetic preserves the byte address 20, selecting
        # dword 5. This controls the existing scalar-word representation.
        code = program(sop1("S_MOV_B32", 4, 128 + 16),
                       sop2("S_ADD_U32", 4, 4, 128 + 4), load(7, 0, 4))
        c = Compiler(code, [dict(kind=2, api=0, reg=0, flags=0)])
        c.run(decoder().decode_stream(code, 0))
        self.assertEqual(c.scalar_bits[4], 20)
        self.assertEqual(c.ir.value(c.s[4]), 20)
        self.assertEqual(c.ir.nodes[c.s[7]]["name"], "CB0[5]")

    def test_float_inline_register_offset_uses_bits_not_numeric_float(self):
        # SSRC 244 is the F32 inline constant 2.0 (word 0x40000000).
        # A memory address consumes that word, rather than the numeric 2.0.
        code = program(sop1("S_MOV_B32", 4, 244), load(7, 0, 4))
        c = Compiler(code, [dict(kind=2, api=0, reg=0, flags=0)])
        c.run(decoder().decode_stream(code, 0))
        self.assertEqual(c.ir.value(c.s[4]), 2.0)
        self.assertEqual(c.scalar_bits[4], 0x40000000)
        self.assertEqual(c.ir.nodes[c.s[7]]["name"], "CB0[268435456]")

    def test_numeric_scalar_without_word_proof_does_not_become_an_address(self):
        code = load(7, 0, 4)
        c = Compiler(code, [dict(kind=2, api=0, reg=0, flags=0)])
        c.s[4] = c.ir.const(20)
        c.run(decoder().decode_stream(code, 0))
        self.assertEqual(c.ir.nodes[c.s[7]]["op"], "unsupported")
        self.assertIsNone(c.ir.nodes[c.s[7]]["load_offset"])

    def test_diagnostic_buffer_read_preserves_descriptor_and_address_without_binding(self):
        code = load(7, 8, 60, immediate=True)
        c = Compiler(code, [dict(kind=0, api=50, reg=8, flags=0)])
        c.runtime_conditions = True
        c.run(decoder().decode_stream(code, 0))
        node = c.ir.nodes[c.s[7]]
        access = node["scalar_memory_access"]
        self.assertEqual(access["instruction_words"], list(struct.unpack("<I", code)))
        self.assertEqual(access["byte_offset"], 240)
        self.assertEqual(access["descriptor_proof"]["api"], 50)
        self.assertFalse(access["memory_evaluated"])
        self.assertEqual(access["bounds"], "unproven")
        self.assertEqual(node["op"], "unsupported")

    def test_path_dependent_descriptor_does_not_regain_scalar_read_proof(self):
        code = load(7, 8, 60, immediate=True)
        c = Compiler(code, [dict(kind=0, api=50, reg=8, flags=0)])
        c.runtime_conditions = True
        c.buffer_descriptor_taint.add(9)
        c.run(decoder().decode_stream(code, 0))
        access = c.ir.nodes[c.s[7]]["scalar_memory_access"]
        self.assertIsNone(access["descriptor_proof"])
        self.assertFalse(access["memory_evaluated"])


if __name__ == "__main__":
    unittest.main()
