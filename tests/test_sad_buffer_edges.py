"""Additional focused controls for decoded V_SAD_U32 and MUBUF/MTBUF edges.

No archive, actual buffer values, material binding, or frame semantics are
used here. The GCN3 definition is unsigned abs-difference plus src2; the result
is a 32-bit word. Avoid the signed wording in some later AMD ISA revisions.
"""
import struct
import unittest

from rc2016.shaders import gcn
from rc2016.shaders.bindings import PORTABLE_OPS
from rc2016.shaders.ir import bits, from_bits
from rc2016.shaders.translator import Compiler, decoder


def vop3(opcode, dst, src, *, abs_mask=0, neg_mask=0, omod=0, clamp=False):
    q = 0xD0000000 | (opcode << 17) | dst
    q |= src[0] << 32 | src[1] << 41 | src[2] << 50
    q |= abs_mask << 8 | int(clamp) << 11 | omod << 59 | neg_mask << 61
    return q


def decode_one(q):
    blob = struct.pack("<Q", q)
    return blob, decoder().decode_stream(blob, 0)[0]


def buffer_instruction(name, dst, *, kind=12, q=0, relative=76,
                       vaddr=4, srsrc=8, soffset=12):
    return {
        "relative": relative,
        "length": 0,
        "kind": kind,
        "name": name,
        "word": q & 0xFFFFFFFF,
        "wide": q,
        "fields": {"src": [vaddr, srsrc, soffset], "dst": [dst]},
    }


class DecodedSadU32Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.opcode = gcn.enum_value("GcnOpcodeVOP3", "GCN_V_SAD_U32_E64")

    def run_inline(self, x, y, z):
        # VOP3 inline integer constants are bit-pattern selectors 128+n.
        q = vop3(self.opcode, 9, [128 + x, 128 + y, 128 + z])
        blob, decoded = decode_one(q)
        c = Compiler(blob, [])
        c.instruction(decoded)
        return decoded, c, c.v[9]

    def test_real_decoded_vop3_exposes_three_sources_and_computes_src2(self):
        decoded, c, out = self.run_inline(7, 11, 3)
        self.assertEqual(decoded["kind"], 7)
        self.assertEqual(decoded["name"], "GCN_V_SAD_U32_E64")
        self.assertEqual(decoded["fields"]["src"], [135, 139, 131])
        self.assertEqual(decoded["fields"]["dst"], [9])
        self.assertEqual(bits(c.ir.value(out)), 7)  # |7-11| + 3

    def test_unsigned_difference_is_symmetric_but_src2_is_not_optional(self):
        _, c1, first = self.run_inline(7, 11, 3)
        _, c2, reverse = self.run_inline(11, 7, 3)
        _, c3, other_addend = self.run_inline(7, 11, 5)
        self.assertEqual(bits(c1.ir.value(first)), 7)
        self.assertEqual(bits(c2.ir.value(reverse)), 7)
        self.assertEqual(bits(c3.ir.value(other_addend)), 9)

    def test_high_unsigned_word_is_not_signed_absolute_value(self):
        q = vop3(self.opcode, 4, [256 + 1, 256 + 2, 128])
        blob, decoded = decode_one(q)
        c = Compiler(blob, [])
        c.v[1] = c.ir.const(from_bits(0x80000005))
        c.v[2] = c.ir.const(from_bits(0))
        c.instruction(decoded)
        value = c.ir.value(c.v[4])
        self.assertIsNotNone(value)
        self.assertEqual(bits(value), 0x80000005,
                         "GCN3 SAD uses unsigned distance; signed abs would produce 0x7ffffffb")

    def test_unsigned_sum_wraps_at_32_bits(self):
        q = vop3(self.opcode, 4, [256 + 1, 256 + 2, 256 + 3])
        blob, decoded = decode_one(q)
        c = Compiler(blob, [])
        c.v[1] = c.ir.const(from_bits(0x80000005))  # finite negative subnormal bit pattern
        c.v[2] = c.ir.const(from_bits(0))
        c.v[3] = c.ir.const(from_bits(0x80000005))  # same unsigned bit pattern as src0
        c.instruction(decoded)
        self.assertEqual(bits(c.ir.value(c.v[4])), 10)

    def test_dynamic_result_stays_integer_ir_and_is_not_portable(self):
        q = vop3(self.opcode, 4, [256 + 1, 256 + 2, 256 + 3])
        blob, decoded = decode_one(q)
        c = Compiler(blob, [])
        for reg, semantic in ((1, 17), (2, 5), (3, 0)):
            c.v[reg] = c.ir.node("input", name=f"vertex.semantic{semantic}.x",
                                 kind="vertex_input", semantic=semantic, channel=0)
        c.instruction(decoded)
        node = c.ir.nodes[c.v[4]]
        self.assertEqual(node["op"], "u_sad")
        self.assertEqual(node["args"], [c.v[1], c.v[2], c.v[3]])
        self.assertNotIn("u_sad", PORTABLE_OPS)
        self.assertNotEqual(c.ir.value(c.v[4]), 0.)

    def test_source_modifiers_clamp_and_omod_fail_closed(self):
        for kwargs, label in (({"abs_mask": 1}, "absolute modifier"),
                              ({"neg_mask": 2}, "negate modifier"),
                              ({"clamp": True}, "clamp"),
                              ({"omod": 1}, "omod")):
            with self.subTest(label=label):
                q = vop3(self.opcode, 9, [135, 139, 131], **kwargs)
                blob, decoded = decode_one(q)
                c = Compiler(blob, [])
                c.instruction(decoded)
                node = c.ir.nodes[c.v[9]]
                self.assertEqual(node["op"], "unsupported")
                self.assertIn("V_SAD_U32 with clamp/omod", node["reason"])


class BufferReadEdgeMetadataTests(unittest.TestCase):
    def compiler(self, descriptors=None):
        c = Compiler(b"", [])
        if descriptors is not None:
            c.descriptors = descriptors
        return c

    @staticmethod
    def complete_descriptor(base=8, *, kind=3, api=7, reg=8, flags=0):
        identity = dict(kind=kind, api=api, reg=reg, flags=flags)
        return {base + word: dict(identity, word=word) for word in range(4)}

    def test_last_vgpr_boundary_and_tfe_overflow(self):
        c = self.compiler()
        c.instruction(buffer_instruction("BUFFER_LOAD_FORMAT_X", 255))
        self.assertEqual(c.ir.nodes[c.v[255]]["buffer_access"]["component"], 0)
        self.assertEqual(c.ir.nodes[c.v[255]]["op"], "unsupported")

        c = self.compiler()
        before = {r: c.ir.const(500 + r) for r in range(252, 256)}
        c.v.update(before)
        with self.assertRaisesRegex(ValueError, "destination span exceeds VGPR range"):
            c.instruction(buffer_instruction("BUFFER_LOAD_FORMAT_XYZW", 252, q=1 << 55))
        self.assertEqual({r: c.v[r] for r in before}, before)

    def test_mubuf_address_span_and_lds_are_rejected_before_writes(self):
        c = self.compiler()
        c.v[255] = c.ir.const(1)
        with self.assertRaisesRegex(ValueError, "address span exceeds VGPR range"):
            c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 10, q=1 << 12 | 1 << 13,
                                             vaddr=255))
        self.assertNotIn(10, c.v)

        c = self.compiler()
        c.v[15] = c.ir.const(42)
        with self.assertRaisesRegex(ValueError, "Buffer load to LDS is unsupported"):
            c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 15, q=1 << 16))
        self.assertEqual(c.ir.value(c.v[15]), 42)

    def test_addr64_rejects_index_or_offset_controls_without_register_writes(self):
        for control in (1 << 12, 1 << 13, (1 << 12) | (1 << 13)):
            c = self.compiler()
            c.v[10] = c.ir.const(42)
            with self.assertRaisesRegex(ValueError, "ADDR64.*OFFEN.*IDXEN"):
                c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 10, q=(1 << 15) | control))
            self.assertEqual(c.ir.value(c.v[10]), 42)

    def test_address_provenance_is_stable_when_unrelated_nodes_change_ids(self):
        def trace(extra):
            c = self.compiler()
            for n in range(extra): c.ir.const(1000 + n)
            address = c.ir.node("input", name="captured_address_word", kind="test")
            for n in range(7):
                address = c.ir.op("u_add", address, c.ir.const(from_bits(n + 1)))
            c.v[4] = address
            c.s[12] = address
            c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 10, q=1 << 12))
            return c.ir.nodes[c.v[10]]["buffer_access"]
        first, second = trace(0), trace(12)
        self.assertEqual(first, second)
        self.assertEqual(len(first["address_expression_sha256"]), 1)
        self.assertEqual(len(first["address_expression_sha256"][0]), 64)
        self.assertIn("expr:", first["address_expressions"][0])
        self.assertNotRegex(first["address_expressions"][0], r"node\d+")
        self.assertNotRegex(first["scalar_offset_expression"], r"node\d+")

    def test_format_fields_and_exact_instruction_words_are_preserved(self):
        dfmt, nfmt = 9, 5
        q = (dfmt << 19) | (nfmt << 23) | (1 << 55) | 0x37
        c = self.compiler()
        c.instruction(buffer_instruction("TBUFFER_LOAD_FORMAT_XY", 20, kind=13, q=q,
                                         relative=104, vaddr=3, srsrc=12, soffset=6))
        access = c.ir.nodes[c.v[20]]["buffer_access"]
        self.assertEqual(access["instruction_words"], [q & 0xFFFFFFFF, q >> 32])
        self.assertEqual(access["format"], {"source": "instruction", "data_format": dfmt,
                                            "numeric_format": nfmt})
        self.assertEqual(access["operands"], {"vaddr": 3, "vdata": 20, "srsrc": 12, "soffset": 6})
        self.assertEqual(access["data_words"], 2)
        self.assertEqual(access["bounds"], "unproven")
        self.assertFalse(access["memory_evaluated"])

    def test_conditional_direct_descriptor_identity_is_not_buffer_binding_proof(self):
        c = self.compiler(self.complete_descriptor())
        flag = c.ir.node("input", name="descriptor_branch", kind="runtime_flag")
        before = dict(c.descriptors)
        after = {r: dict(d, api=8) for r,d in before.items()}
        c.descriptors = dict(after)
        c.merge_table_descriptors(flag, before, after, [(r, None) for r in after])
        c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 20, srsrc=8))
        self.assertIsNone(c.ir.nodes[c.v[20]]["buffer_access"]["descriptor_proof"])
        self.assertFalse(c.ir.nodes[c.v[20]]["buffer_access"]["memory_evaluated"])

    def test_smov_b32_alias_propagates_taint_and_suppresses_descriptor_proof(self):
        c = self.compiler(self.complete_descriptor(base=20, reg=20))
        c.descriptors[8] = dict(c.descriptors[20])
        c.buffer_descriptor_taint.add(8)
        c.instruction({"relative": 4, "length": 0, "kind": 0, "word": 0, "wide": 0,
                       "name": "S_MOV_B32",
                       "fields": {"src": [8], "dst": [20]}})
        self.assertIn(20, c.buffer_descriptor_taint)
        c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 30, srsrc=20))
        self.assertIsNone(c.ir.nodes[c.v[30]]["buffer_access"]["descriptor_proof"])

    def test_overlapping_smov_b64_snapshots_and_propagates_source_taint(self):
        c = self.compiler()
        c.buffer_descriptor_taint.update((8, 9))
        c.instruction({"relative": 4, "length": 0, "kind": 0, "word": 0, "wide": 0,
                       "name": "S_MOV_B64",
                       "fields": {"src": [8], "dst": [9]}})
        self.assertTrue({8, 9, 10}.issubset(c.buffer_descriptor_taint))

    def test_scalar_load_propagates_tainted_base_to_descriptor_words(self):
        c = self.compiler(self.complete_descriptor())
        base = 8
        c.buffer_descriptor_taint.add(base + 1)
        c.descriptors[base] = {"kind": 0x1b, "api": 7}
        c.slots = [dict(kind=3, api=7, reg=20, flags=0)]
        word = (4 << 9) | (20 << 15) | 256  # base s[8:9], destination s[20:24]
        c.load({"word": word, "name": "S_LOAD_DWORDX4"})
        self.assertTrue(set(range(20, 24)).issubset(c.buffer_descriptor_taint))
        c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 30, srsrc=20))
        self.assertIsNone(c.ir.nodes[c.v[30]]["buffer_access"]["descriptor_proof"])

    def test_unchanged_direct_descriptor_identity_retains_diagnostic_word_proof(self):
        c = self.compiler(self.complete_descriptor())
        flag = c.ir.node("input", name="descriptor_branch", kind="runtime_flag")
        before = dict(c.descriptors)
        c.merge_table_descriptors(flag, before, before, [(r, None) for r in before])
        c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 20, srsrc=8))
        self.assertEqual(c.ir.nodes[c.v[20]]["buffer_access"]["descriptor_proof"]["api"], 7)

    def test_complete_descriptor_proof_is_recorded_and_stale_words_do_not_prove(self):
        c = self.compiler(self.complete_descriptor())
        c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 20, srsrc=8))
        access = c.ir.nodes[c.v[20]]["buffer_access"]
        self.assertEqual(access["descriptor_proof"],
                         {"kind": 3, "api": 7, "reg": 8, "flags": 0})
        self.assertEqual(access["format"], {"source": "descriptor", "decoded": False})
        self.assertEqual(access["bounds"], "unproven")
        self.assertFalse(access["memory_evaluated"])

        stale = self.complete_descriptor()
        del stale[10]
        c = self.compiler(stale)
        c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 20, srsrc=8))
        self.assertIsNone(c.ir.nodes[c.v[20]]["buffer_access"]["descriptor_proof"])

        mixed = self.complete_descriptor()
        mixed[10] = dict(mixed[10], api=8)
        c = self.compiler(mixed)
        c.instruction(buffer_instruction("BUFFER_LOAD_DWORD", 20, srsrc=8))
        self.assertIsNone(c.ir.nodes[c.v[20]]["buffer_access"]["descriptor_proof"])


if __name__ == "__main__":
    unittest.main()

