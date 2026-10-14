"""Hand-built decoded-instruction tests for indirect texture descriptors.

These test compiler bookkeeping and static control-flow guards; they do not
decode a real shader or establish runtime draw selection.
"""
import unittest

from rc2016.shaders.translator import Compiler


RESOURCE_POINTER = dict(kind=0x13, api=0, reg=12, flags=16)
SAMPLER_POINTER = dict(kind=0x15, api=0, reg=14, flags=16)


def compiler(allow_independent_samplers=False):
    return Compiler(b"", [RESOURCE_POINTER.copy(), SAMPLER_POINTER.copy()],
                    allow_independent_samplers=allow_independent_samplers)


def decoded_load(base, destination, byte_offset, words=4):
    """Represent one decoded S_LOAD_DWORD[Xn] with an immediate byte offset."""
    name = "S_LOAD_DWORD" if words == 1 else f"S_LOAD_DWORDX{words}"
    word = ((base // 2) << 9) | (destination << 15) | 0x100 | (byte_offset // 4)
    return dict(relative=0, kind=10, word=word, wide=0, fields={}, name=name, length=0)


def decoded_sample(resource_base=32, sampler_base=40):
    wide = ((resource_base // 4) << 48) | ((sampler_base // 4) << 53) | (0xF << 8) | (8 << 40)
    return dict(relative=0, kind=14, word=0, wide=wide, fields={}, name="IMAGE_SAMPLE", length=0)


def load_complete_pair(c, *, resource_offsets=(0, 16), sampler_offset=0):
    c.instruction(decoded_load(12, 32, resource_offsets[0]))
    c.instruction(decoded_load(12, 36, resource_offsets[1]))
    c.instruction(decoded_load(14, 40, sampler_offset))
    c.v[0] = c.ir.const(0.25)
    c.v[1] = c.ir.const(0.75)


def descriptor(kind, api, reg, flags, word):
    return dict(kind=kind, api=api, reg=reg, flags=flags, word=word)


class IndirectDescriptorLoadTests(unittest.TestCase):
    def test_split_x4_resource_loads_assemble_one_complete_x8_descriptor(self):
        c = compiler()
        load_complete_pair(c)
        resource = c.texture_descriptor(32, 0, 8)
        self.assertIsNotNone(resource)
        self.assertEqual(resource["api"], 0)
        self.assertEqual([c.descriptors[32 + i]["word"] for i in range(8)], list(range(8)))
        c.instruction(decoded_sample())
        self.assertEqual(c.samples[-1]["api"], 0)
        self.assertTrue(c.samples[-1]["resource_table"])
        self.assertEqual(c.samples[-1]["sampler_api"], 0)
        self.assertTrue(any(n["op"] == "texture" and n.get("resource_table") is True
                            for n in c.ir.nodes))

    def test_partial_or_shifted_resource_words_do_not_form_a_texture(self):
        for offsets in ((0, None), (4, 20)):
            with self.subTest(offsets=offsets):
                c = compiler()
                c.instruction(decoded_load(12, 32, offsets[0]))
                if offsets[1] is not None:
                    c.instruction(decoded_load(12, 36, offsets[1]))
                c.instruction(decoded_load(14, 40, 0))
                c.v[0] = c.ir.const(0.25); c.v[1] = c.ir.const(0.75)
                self.assertIsNone(c.texture_descriptor(32, 0, 8))
                c.instruction(decoded_sample())
                self.assertIsNone(c.samples[-1]["api"])
                self.assertFalse(any(n["op"] == "texture" for n in c.ir.nodes))

    def test_sampler_api_mismatch_and_stale_partial_pointer_fail_closed(self):
        c = compiler()
        load_complete_pair(c, sampler_offset=32)
        c.instruction(decoded_sample())
        self.assertIsNone(c.samples[-1]["api"])
        self.assertFalse(any(n["op"] == "texture" for n in c.ir.nodes))

        c = compiler()
        c.descriptors[13]["word"] = 0  # the second pointer word is stale/misaligned
        c.instruction(decoded_load(12, 32, 0))
        c.instruction(decoded_load(12, 36, 16))
        c.instruction(decoded_load(14, 40, 0))
        c.v[0] = c.ir.const(0.25); c.v[1] = c.ir.const(0.75)
        self.assertIsNone(c.texture_descriptor(32, 0, 8))
        c.instruction(decoded_sample())
        self.assertIsNone(c.samples[-1]["api"])


    def test_independent_complete_tables_keep_resource_api_and_sampler_api_separate(self):
        # API 17 names the texture resource. API 3 names its independent sampler.
        c = compiler(allow_independent_samplers=True)
        load_complete_pair(c, resource_offsets=(17 * 32, 17 * 32 + 16),
                           sampler_offset=3 * 16)
        c.instruction(decoded_sample())
        self.assertEqual(c.samples[-1]["api"], 17)
        self.assertEqual(c.samples[-1]["sampler_api"], 3)
        self.assertTrue(c.samples[-1]["resource_table"])
        self.assertTrue(c.samples[-1]["sampler_table"])
        textures = [n for n in c.ir.nodes if n["op"] == "texture"]
        self.assertTrue(textures)
        self.assertTrue(all(n["api"] == 17 and n["sampler_api"] == 3
                            and n["resource_table"] is True and n["sampler_table"] is True
                            for n in textures))
        self.assertFalse(any(n["op"] == "texture" and n["api"] == 3 for n in c.ir.nodes))

    def test_independent_mode_rejects_partial_wrong_origin_and_guarded_descriptors(self):
        for failure in ("partial_resource", "partial_sampler", "wrong_pointer_api", "wrong_flags", "unproved_guard"):
            with self.subTest(failure=failure):
                c = compiler(allow_independent_samplers=True)
                if failure == "wrong_pointer_api":
                    for r in (14, 15): c.descriptors[r]["api"] = 1
                if failure == "wrong_flags":
                    for r in (14, 15): c.descriptors[r]["flags"] = 0
                load_complete_pair(c, resource_offsets=(17 * 32, 17 * 32 + 16),
                                   sampler_offset=3 * 16)
                if failure == "partial_resource": c.descriptors.pop(39)
                if failure == "partial_sampler": c.descriptors.pop(43)
                if failure == "unproved_guard":
                    guard = c.ir.node("input", name="unknown_branch", kind="runtime_flag")
                    for r in range(40, 44): c.descriptors[r]["guard"] = guard
                c.instruction(decoded_sample())
                self.assertIsNone(c.samples[-1]["api"])
                self.assertFalse(any(n["op"] == "texture" for n in c.ir.nodes))

    def test_independent_api_pair_remains_strict_by_default(self):
        c = compiler()
        load_complete_pair(c, resource_offsets=(17 * 32, 17 * 32 + 16),
                           sampler_offset=3 * 16)
        c.instruction(decoded_sample())
        self.assertIsNone(c.samples[-1]["api"])
        self.assertFalse(any(n["op"] == "texture" for n in c.ir.nodes))

    def test_independent_sampler_api_over_31_fails_closed(self):
        c = compiler(allow_independent_samplers=True)
        load_complete_pair(c, resource_offsets=(17 * 32, 17 * 32 + 16),
                           sampler_offset=32 * 16)
        c.instruction(decoded_sample())
        self.assertIsNone(c.samples[-1]["api"])
        self.assertFalse(any(n["op"] == "texture" for n in c.ir.nodes))


class ConditionalDescriptorTests(unittest.TestCase):
    def test_conditional_partial_pointer_overwrite_breaks_descriptor_identity(self):
        c = compiler()
        condition = c.ir.node("input", name="branch", kind="runtime_flag")
        c.bp = condition
        before = dict(c.descriptors)
        after = dict(before)
        after[13] = descriptor(0x13, 1, 12, 16, 1)
        c.descriptors = dict(after)
        c.merge_table_descriptors(condition, before, after, [(13, 1)])
        self.assertIsNone(c.texture_descriptor(12, 0x13, 2))

    def test_branch_guard_proves_descriptor_only_on_the_overwrite_path(self):
        c = compiler()
        condition = c.ir.node("input", name="branch", kind="runtime_flag")
        before = {12: descriptor(0x13, 0, 12, 16, 0),
                  13: descriptor(0x13, 0, 12, 16, 1)}
        after = {12: descriptor(0x13, 1, 12, 16, 0),
                 13: descriptor(0x13, 1, 12, 16, 1)}
        c.descriptors = dict(after)
        c.merge_table_descriptors(condition, before, after, [(12, 1), (13, 1)])

        c.bp = condition
        self.assertIsNotNone(c.texture_descriptor(12, 0x13, 2))
        c.bp = c.ir.const(1.)
        self.assertIsNone(c.texture_descriptor(12, 0x13, 2))

    def test_same_identity_on_both_branch_paths_proves_unconditional_pointer(self):
        c = compiler()
        condition = c.ir.node("input", name="branch", kind="runtime_flag")
        before = {12: descriptor(0x13, 0, 12, 16, 0),
                  13: descriptor(0x13, 0, 12, 16, 1)}
        after = dict(before)
        c.descriptors = dict(after)
        c.merge_table_descriptors(condition, before, after, [(12, 0), (13, 0)])
        c.bp = c.ir.const(1.)
        self.assertIsNotNone(c.texture_descriptor(12, 0x13, 2))


if __name__ == "__main__":
    unittest.main()
