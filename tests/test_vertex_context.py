"""Synthetic controls for explicit instance-record vertex translation.

No game archive, renderer, Blender path, or production admission is used.
"""
import hashlib
import struct
import unittest
from dataclasses import FrozenInstanceError

from rc2016.shaders import gcn
from rc2016.shaders.ir import bits
from rc2016.shaders.translator import Compiler, decoder
from rc2016.shaders.vertex_context import ContextCompiler, InstanceRecordContext, RECORD_BYTES


def assert_context_read_failed(test, c, dst, count):
    """A bad supplied-CB1 read poisons its whole destination, never a prefix."""
    for reg in range(dst, dst + count):
        node_id = c.s[reg]
        test.assertEqual(c.ir.nodes[node_id]["op"], "unsupported")
        test.assertNotIn(reg, c.scalar_bits)
        test.assertNotIn(reg, c.descriptors)
    test.assertEqual(c.context_reads, [])
    test.assertTrue(c.context_failures)
    failure = c.context_failures[-1]
    test.assertIsInstance(failure.get("instruction_offset"), int)
    test.assertGreaterEqual(failure["instruction_offset"], 0)
    test.assertTrue(failure.get("reason"))
    # Current receipt represents the span as byte_offset + word count.
    test.assertIn("byte_offset", failure)
    test.assertEqual(failure.get("words"), count)


def op(name):
    return gcn.enum_value("GcnOpcodeSMRD", "GCN_" + name)


def s_buffer_load(name, dst, base_sgpr, offset, *, immediate=True):
    """Encode the one-dword SMRD instruction used by scalar-load tests.

    `offset` is an encoded dword displacement for immediate mode, or an SGPR
    index in register-offset mode.
    """
    word = ((0x18 << 27) | (op(name) << 22) | (dst << 15) |
            ((base_sgpr // 2) << 9) | (int(immediate) << 8) | offset)
    return struct.pack("<I", word)


def sop1(name, dst, src):
    opcode = gcn.enum_value("GcnOpcodeSOP1", "GCN_" + name)
    return struct.pack("<I", (0x17d << 23) | (dst << 16) | (opcode << 8) | src)


def v_readfirstlane(dst, src_vgpr):
    opcode = gcn.enum_value("GcnOpcodeVOP1", "GCN_V_READFIRSTLANE_B32")
    return struct.pack("<I", (0x3f << 25) | (dst << 17) | (opcode << 9) | (256 + src_vgpr))


def record_words(start=0):
    return b"".join(struct.pack("<I", start + i) for i in range(RECORD_BYTES // 4))


def context_for(code, *, record=None, instance_index=0, instance_vgpr=3,
                origin="synthetic vertex-context fixture", code_sha256=None):
    record = record_words() if record is None else record
    return InstanceRecordContext(record, instance_index, instance_vgpr,
                                 hashlib.sha256(code).hexdigest() if code_sha256 is None else code_sha256,
                                 origin)


def compile_context(code, *, record=None, instance_index=0, instance_vgpr=3,
                    usage_slots=None, code_sha256=None):
    context = context_for(code, record=record, instance_index=instance_index,
                          instance_vgpr=instance_vgpr, code_sha256=code_sha256)
    slots = [dict(kind=2, api=1, reg=0, flags=0)] if usage_slots is None else usage_slots
    return ContextCompiler(code, slots, context=context)


def run(c):
    c.run(decoder().decode_stream(c.code, 0))
    return c


class InstanceRecordContextTests(unittest.TestCase):
    def test_record_identity_and_receipt_include_exact_provenance(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        data = record_words(0x1000)
        ctx = context_for(code, record=data, instance_index=2, instance_vgpr=7,
                          origin="captured CB1 bytes from synthetic test")
        receipt = ctx.receipt()
        self.assertEqual(ctx.byte_start, 2 * 272)
        self.assertEqual(receipt["contract"], "explicit-source-cb1-record-v1")
        self.assertEqual(receipt["source_api"], 1)
        self.assertEqual(receipt["record_bytes"], 272)
        self.assertEqual(receipt["byte_start"], 544)
        self.assertEqual(receipt["instance_index"], 2)
        self.assertEqual(receipt["instance_vgpr"], 7)
        self.assertEqual(receipt["code_sha256"], hashlib.sha256(code).hexdigest())
        self.assertEqual(receipt["record_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(receipt["origin"], "captured CB1 bytes from synthetic test")
        self.assertFalse(receipt["runtime_binding_proven"])
        self.assertFalse(receipt["game_equivalence"])
        with self.assertRaises(FrozenInstanceError):
            ctx.instance_index = 3

    def test_context_rejects_mutable_or_wrong_sized_records_and_bad_metadata(self):
        code_hash = "a" * 64
        bad = (
            (bytearray(RECORD_BYTES), 0, 3, code_hash, "origin"),
            (bytes(RECORD_BYTES - 1), 0, 3, code_hash, "origin"),
            (bytes(RECORD_BYTES), -1, 3, code_hash, "origin"),
            (bytes(RECORD_BYTES), True, 3, code_hash, "origin"),
            (bytes(RECORD_BYTES), (0xffffffff - 272 + 1) // 272 + 1, 3, code_hash, "origin"),
            (bytes(RECORD_BYTES), 0, 256, code_hash, "origin"),
            (bytes(RECORD_BYTES), 0, True, code_hash, "origin"),
            (bytes(RECORD_BYTES), 0, 3, "A" * 64, "origin"),
            (bytes(RECORD_BYTES), 0, 3, "bad", "origin"),
            (bytes(RECORD_BYTES), 0, 3, code_hash, "  "),
        )
        for args in bad:
            with self.subTest(args=args[1:]):
                with self.assertRaises(ValueError):
                    InstanceRecordContext(*args)

    def test_context_compiler_rejects_code_hash_mismatch(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        ctx = context_for(code, code_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "code SHA-256 mismatch"):
            ContextCompiler(code, [dict(kind=2, api=1, reg=0, flags=0)], context=ctx)

    def test_caller_supplied_instance_builtin_is_seeded_as_integer_bits(self):
        code = v_readfirstlane(10, 3)
        c = run(compile_context(code, instance_index=9, instance_vgpr=3))
        self.assertEqual(bits(c.ir.value(c.v[3])), 9)
        self.assertEqual(c.scalar_bits[10], 9)
        self.assertEqual(c.ir.value(c.s[10]), 9)


class ContextScalarLoadTests(unittest.TestCase):
    def test_supported_scalar_load_widths_resolve_from_exact_record_bytes(self):
        for width, name in ((1, "S_BUFFER_LOAD_DWORD"), (2, "S_BUFFER_LOAD_DWORDX2"),
                            (4, "S_BUFFER_LOAD_DWORDX4"), (8, "S_BUFFER_LOAD_DWORDX8"),
                            (16, "S_BUFFER_LOAD_DWORDX16")):
            with self.subTest(width=width):
                dst = 20
                code = s_buffer_load(name, dst, 0, 0)
                data = record_words(100)
                c = run(compile_context(code, record=data))
                self.assertEqual([c.scalar_bits[dst + j] for j in range(width)],
                                 list(range(100, 100 + width)))
                self.assertEqual([c.ir.value(c.s[dst + j]) for j in range(width)],
                                 list(range(100, 100 + width)))
                self.assertEqual(c.context_reads, [dict(instruction_offset=0, byte_offset=0,
                                                       record_offset=0, words=width)])

    def test_register_offset_uses_proven_integer_byte_offset(self):
        # MOV 16 into s4; register-offset S_BUFFER_LOAD consumes the integer
        # address bits and selects record dword 4, rather than float 16.0's bits.
        code = sop1("S_MOV_B32", 4, 128 + 16) + s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 4,
                                                                    immediate=False)
        c = run(compile_context(code, record=record_words(50)))
        self.assertEqual(c.scalar_bits[8], 54)
        self.assertEqual(c.context_reads[0]["byte_offset"], 16)
        self.assertEqual(c.context_reads[0]["record_offset"], 16)

    def test_instance_index_selects_only_its_272_byte_record_window(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 68)
        c = run(compile_context(code, record=record_words(700), instance_index=1))
        self.assertEqual(c.scalar_bits[8], 700)
        self.assertEqual(c.context_reads[0], dict(instruction_offset=0, byte_offset=272,
                                                 record_offset=0, words=1))
        self.assertEqual(c.context_receipt()["byte_start"], 272)

    def test_read_exactly_ending_at_record_boundary_is_valid(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORDX2", 20, 0, 66)
        c = run(compile_context(code, record=record_words(300)))
        self.assertEqual([c.scalar_bits[20], c.scalar_bits[21]], [366, 367])
        self.assertEqual(c.context_reads[0]["record_offset"], 264)

    def test_partial_overrun_writes_unsupported_for_every_destination_word(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORDX4", 20, 0, 67)
        c = compile_context(code)
        c.s[20] = c.ir.const(123.)
        c.scalar_bits[20] = 123
        run(c)
        assert_context_read_failed(self, c, 20, 4)

    def test_start_at_end_and_unknown_offset_fail_closed(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 68)
        c = run(compile_context(code))
        assert_context_read_failed(self, c, 8, 1)

        unknown = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 4, immediate=False)
        c = run(compile_context(unknown))
        assert_context_read_failed(self, c, 8, 1)

    def test_known_unaligned_register_offset_is_rejected(self):
        code = sop1("S_MOV_B32", 4, 130) + s_buffer_load(
            "S_BUFFER_LOAD_DWORD", 8, 0, 4, immediate=False)
        c = run(compile_context(code))
        assert_context_read_failed(self, c, 8, 1)

    def test_partial_mixed_and_tainted_cb1_descriptors_reject_reads(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        cases = []
        c = compile_context(code)
        del c.descriptors[2]
        cases.append(("partial", c))
        c = compile_context(code)
        c.descriptors[2] = dict(c.descriptors[2], api=2)
        cases.append(("mixed identity", c))
        c = compile_context(code)
        c.buffer_descriptor_taint.add(1)
        cases.append(("tainted descriptor", c))
        for label, c in cases:
            with self.subTest(label=label):
                run(c)
                assert_context_read_failed(self, c, 8, 1)

    def test_conditionally_guarded_cb1_descriptor_is_not_a_complete_context_proof(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        c = compile_context(code)
        guard = c.ir.node("input", name="descriptor_branch", kind="runtime_flag")
        c.descriptors[0]["guard"] = guard
        run(c)
        assert_context_read_failed(self, c, 8, 1)

    def test_other_api_and_missing_descriptor_keep_base_compiler_behavior(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        # CB0 is not the explicitly supplied API1 context: preserve the symbolic input.
        for api in (0, 2):
            with self.subTest(api=api):
                c = run(compile_context(code, usage_slots=[dict(kind=2, api=api, reg=0, flags=0)]))
                node = c.ir.nodes[c.s[8]]
                self.assertEqual(node["op"], "input")
                self.assertEqual((node["kind"], node["api"], node["dword"]), ("cb", api, 0))
                self.assertEqual(c.context_reads, [])

        # No descriptor means there is no evidence this load belongs to supplied CB1.
        c = run(compile_context(code, usage_slots=[]))
        self.assertEqual(c.ir.nodes[c.s[8]]["op"], "unsupported")
        self.assertEqual(c.context_reads, [])

    def test_non_context_compiler_keeps_cb_reads_symbolic(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        c = Compiler(code, [dict(kind=2, api=1, reg=0, flags=0)])
        run(c)
        self.assertEqual(c.ir.nodes[c.s[8]]["op"], "input")
        self.assertEqual(c.ir.nodes[c.s[8]]["name"], "CB1[0]")
        self.assertNotIn("context_reads", c.__dict__)


class ContextFirstLaneTests(unittest.TestCase):
    def test_finite_constant_first_lane_folds_to_scalar_bits(self):
        # The context supplies integer-index bits 7 to a uniform instance VGPR.
        code = v_readfirstlane(12, 3)
        c = run(compile_context(code, instance_index=7, instance_vgpr=3))
        self.assertEqual(c.ir.value(c.s[12]), 7)
        self.assertEqual(c.scalar_bits[12], 7)
        self.assertEqual(c.ir.nodes[c.s[12]]["op"], "const")

    def test_dynamic_or_nonfinite_first_lane_does_not_fold(self):
        code = v_readfirstlane(12, 10)
        for value, label in ((None, "dynamic"), (float("inf"), "infinite"),
                             (float("nan"), "nan")):
            with self.subTest(label=label):
                c = compile_context(code)
                c.v[10] = (c.unknown("dynamic instance data") if value is None else
                           c.ir.node("const", [], value=value))
                run(c)
                self.assertEqual(c.ir.nodes[c.s[12]]["op"], "unsupported")
                self.assertNotIn(12, c.scalar_bits)


class VertexExportsContextTests(unittest.TestCase):
    @staticmethod
    def row(code, inputs=None):
        return dict(code=code,
                    usage_slots=[dict(kind=2, api=1, reg=0, flags=0)],
                    inputs=[] if inputs is None else inputs)

    def test_explicit_context_is_attached_to_returned_dag(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        context = context_for(code, record=record_words(900))
        row = self.row(code)
        from rc2016.shaders.interpolants import vertex_exports
        result, reason = vertex_exports(row, context=context)
        self.assertIsNone(reason)
        self.assertIsNotNone(result)
        _, dag = result
        self.assertEqual(dag.vertex_context_receipt["contract"], "explicit-source-cb1-record-v1")
        self.assertEqual(dag.vertex_context_receipt["reads"], [dict(
            instruction_offset=0, byte_offset=0, record_offset=0, words=1)])

    def test_no_context_keeps_regular_dag_without_context_receipt(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        from rc2016.shaders.interpolants import vertex_exports
        result, reason = vertex_exports(self.row(code))
        self.assertIsNone(reason)
        self.assertIsNotNone(result)
        _, dag = result
        self.assertFalse(hasattr(dag, "vertex_context_receipt"))

    def test_explicit_builtin_overlap_with_declared_vertex_input_raises(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        context = context_for(code, instance_vgpr=3)
        row = self.row(code, [dict(vgpr=2, size=2, semantic=7)])
        from rc2016.shaders.interpolants import vertex_exports
        with self.assertRaisesRegex(ValueError, "overlaps a declared vertex input"):
            vertex_exports(row, context=context)


if __name__ == "__main__":
    unittest.main()
