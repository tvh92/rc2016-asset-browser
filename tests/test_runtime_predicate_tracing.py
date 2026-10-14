"""Fail-closed diagnostic provenance for unsupported wave-uniform predicates."""
import struct
import unittest

from rc2016.shaders import gcn
from rc2016.shaders.translator import Compiler, decoder


def op(table, name):
    return gcn.enum_value(table, "GCN_" + name)


def sop2(name, dst, src0, src1):
    return struct.pack("<I", (2 << 30) | (op("GcnOpcodeSOP2", name) << 23) |
                       (dst << 16) | (src1 << 8) | src0)


def sopc(name, src0, src1):
    return struct.pack("<I", (0x17e << 23) | (op("GcnOpcodeSOPC", name) << 16) |
                       (src1 << 8) | src0)


def smrd_load(dst, base, dword, *, pair=False):
    name = "S_BUFFER_LOAD_DWORDX2" if pair else "S_BUFFER_LOAD_DWORD"
    return struct.pack("<I", (0x18 << 27) | (op("GcnOpcodeSMRD", name) << 22) |
                       (dst << 15) | ((base // 2) << 9) | (1 << 8) | dword)


def sopk(name, dst, immediate):
    return struct.pack("<I", (0xb << 28) | (op("GcnOpcodeSOPK", name) << 23) |
                       (dst << 16) | (immediate & 0xffff))


def run_diagnostic(code):
    compiler = Compiler(code, [])
    compiler.runtime_conditions = True
    compiler.run(decoder().decode_stream(code, 0))
    return compiler


def runtime_flag(compiler):
    flags = [node for node in compiler.ir.nodes if node.get("kind") == "runtime_flag"]
    if len(flags) != 1:
        raise AssertionError(f"expected one runtime flag, found {len(flags)}")
    return flags[0]


class RuntimePredicateTracingTests(unittest.TestCase):
    def test_sopc_literal_is_consumed_and_provenance_stays_opaque(self):
        literal = 0x3f800000
        code = smrd_load(4, 8, 3) + sopc("S_CMP_LG_U32", 4, 255) + struct.pack("<I", literal)
        instructions = decoder().decode_stream(code, 0)
        self.assertEqual([i["length"] for i in instructions], [4, 8])
        compiler = run_diagnostic(code)
        predicate = runtime_flag(compiler)["predicate"]
        self.assertEqual(predicate["opcode"], "S_CMP_LG_U32")
        self.assertEqual(predicate["encoding"], "SOPC")
        self.assertEqual(predicate["instruction_offset"], 4)
        self.assertEqual(predicate["instruction_words"], list(struct.unpack("<2I", code[4:])))
        left, right = predicate["operands"]
        self.assertEqual(left["words"][0]["sgpr"], 4)
        self.assertIsNone(left["words"][0]["bits"])
        self.assertIn("expression_sha256", left["words"][0])
        self.assertEqual(right["words"][0]["bits"], literal)
        self.assertFalse(predicate["predicate_evaluated"])
        self.assertFalse(predicate["memory_evaluated"])
        self.assertEqual(compiler.ir.nodes[compiler.s[4]]["op"], "unsupported")

    def test_b64_bitcompare_snapshots_both_words_of_scalar_pair(self):
        code = smrd_load(4, 8, 0, pair=True) + sopc("S_BITCMP0_B64", 4, 128)
        compiler = run_diagnostic(code)
        predicate = runtime_flag(compiler)["predicate"]
        self.assertEqual(predicate["opcode"], "S_BITCMP0_B64")
        pair = predicate["operands"][0]
        self.assertEqual(pair["encoded"], 4)
        self.assertEqual(pair["width_words"], 2)
        self.assertEqual([word["sgpr"] for word in pair["words"]], [4, 5])
        self.assertTrue(all("expression_sha256" in word for word in pair["words"]))
        self.assertFalse(predicate["predicate_evaluated"])
        self.assertFalse(predicate["memory_evaluated"])

    def test_sopk_retains_signed_and_unsigned_immediate_interpretations(self):
        signed = run_diagnostic(sopk("S_CMPK_EQ_I32", 4, 0xfffe))
        unsigned = run_diagnostic(sopk("S_CMPK_EQ_U32", 4, 0xfffe))
        sp = runtime_flag(signed)["predicate"]
        up = runtime_flag(unsigned)["predicate"]
        self.assertEqual(sp["raw_simm16"], 0xfffe)
        self.assertEqual(sp["immediate_value"], -2)
        self.assertEqual(up["raw_simm16"], 0xfffe)
        self.assertEqual(up["immediate_value"], 0xfffe)
        self.assertFalse(sp["predicate_evaluated"])
        self.assertFalse(up["memory_evaluated"])

    def test_same_offset_distinguishes_load_origin_but_ignores_dag_node_ids(self):
        def trace(offset, padding=0):
            code = smrd_load(4, 8, offset) + sopc("S_CMP_LG_U32", 4, 128)
            compiler = Compiler(code, [])
            compiler.runtime_conditions = True
            for j in range(padding):
                compiler.ir.node("input", name=f"unrelated{j}", kind="test")
            compiler.run(decoder().decode_stream(code, 0))
            return runtime_flag(compiler)["predicate"]["predicate_sha256"]

        self.assertNotEqual(trace(0), trace(1))
        self.assertEqual(trace(0), trace(0, padding=3))

    def test_unsupported_sop2_snapshots_mask_literal_without_evaluation(self):
        literal = 0x83
        code = (smrd_load(4, 8, 2) + sop2("S_XOR_B32", 5, 4, 255) +
                struct.pack("<I", literal))
        compiler = run_diagnostic(code)
        result = compiler.ir.nodes[compiler.s[5]]
        trace = result.get("scalar_instruction")
        self.assertIsNotNone(trace)
        self.assertEqual(trace["opcode"], "S_XOR_B32")
        self.assertEqual(trace["instruction_offset"], 4)
        self.assertEqual(trace["instruction_words"], list(struct.unpack("<2I", code[4:])))
        self.assertEqual(trace["operands"][0]["words"][0]["sgpr"], 4)
        self.assertIn("expression_sha256", trace["operands"][0]["words"][0])
        self.assertEqual(trace["operands"][1]["words"][0]["bits"], literal)
        self.assertFalse(trace["operation_evaluated"])
        self.assertFalse(trace["memory_evaluated"])
        self.assertEqual(result["op"], "unsupported")


if __name__ == "__main__":
    unittest.main()
