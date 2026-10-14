"""Focused negatives for descriptor-word identity and guarded joins."""
import unittest

from rc2016.shaders.translator import Compiler


def compiler():
    slots = [dict(kind=0x13, api=0, reg=12, flags=16),
             dict(kind=0x15, api=0, reg=14, flags=16)]
    return Compiler(b"", slots)


def decoded_resource_load(destination=32, byte_offset=0, words=4):
    name = "S_LOAD_DWORD" if words == 1 else f"S_LOAD_DWORDX{words}"
    # S_LOAD uses the even SGPR pair encoded by bits 9..14; bit 8 selects
    # an immediate dword offset. The fixture starts from the resource pointer at SGPR12.
    word = ((12 // 2) << 9) | (destination << 15) | 0x100 | (byte_offset // 4)
    return dict(relative=0, kind=10, word=word, wide=0, fields={}, name=name, length=0)


def direct_resource_words(c, base=32, api=5):
    for word in range(8):
        c.descriptors[base + word] = dict(kind=0, api=api, reg=8, flags=0, word=word)


class DescriptorJoinNegativeTests(unittest.TestCase):
    def test_partial_resource_load_does_not_complete_existing_descriptor(self):
        c = compiler()
        direct_resource_words(c)
        c.step(decoded_resource_load(destination=32, byte_offset=0, words=4))
        # Four table words replaced the low direct words. The untouched high
        # direct words have a different provenance identity, so X8 is unusable.
        self.assertIsNone(c.texture_descriptor(32, 0, 8))

    def test_mixed_table_api_word_is_rejected_even_with_complete_word_numbers(self):
        c = compiler()
        for word in range(8):
            c.descriptors[32 + word] = dict(kind=0, api=0, word=word,
                table_slot=(0x13, 0, 12, 16), guard=c.ir.const(1.))
        c.descriptors[32 + 6]["api"] = 1
        self.assertIsNone(c.texture_descriptor(32, 0, 8))

    def test_step_join_guards_indirect_overwrite_and_rejects_direct_remainder(self):
        c = compiler()
        direct_resource_words(c)
        branch = c.ir.node("input", name="branch_condition", kind="runtime_flag")
        c.bp = branch
        c.step(decoded_resource_load(destination=32, byte_offset=0, words=4))

        # The real step path recorded that the table words exist only under the
        # branch. At the unconditional join, the rest of the descriptor is still
        # direct, so no single complete descriptor identity covers all eight words.
        for word in range(4):
            self.assertIn("table_slot", c.descriptors[32 + word])
            self.assertIsNotNone(c.descriptors[32 + word].get("guard"))
        for word in range(4, 8):
            self.assertNotIn("table_slot", c.descriptors[32 + word])
        c.bp = c.ir.const(1.)
        self.assertIsNone(c.texture_descriptor(32, 0, 8))


if __name__ == "__main__":
    unittest.main()
