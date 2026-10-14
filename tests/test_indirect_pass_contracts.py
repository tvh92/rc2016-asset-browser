"""Synthetic contracts for bounded direct and static-table shader joins.

These fixtures exercise only the B6 pointer, its 13-record pass partition,
resource bitmap and CB guards. They do not establish runtime draw selection.
"""
import struct
import unittest
from unittest.mock import patch

from rc2016.shaders.bindings import program_contracts


SHADER_BASE = 0x1000
HEADER = SHADER_BASE + 0x80
SHADER_SIZE = 0x400


def contracts(count=2, resources=2, cb_bytes=16):
    return [dict(index=i, resources=[{} for _ in range(resources)], cb_bytes=cb_bytes)
            for i in range(count)]


def program(texture_apis=(5, 6), usage_slots=None, cb_dwords=(0, 3), *, indirect=False,
            sampler_apis=None):
    sampler_apis = texture_apis if sampler_apis is None else sampler_apis
    nodes = [dict(op="texture", api=api, **(
        {"resource_table": True, "sampler_table": True, "sampler_api": sampler_apis[i]} if indirect else {}))
        for i, api in enumerate(texture_apis)]
    nodes.extend(dict(op="input", kind="cb", api=2, dword=dword) for dword in cb_dwords)
    if usage_slots is None:
        usage_slots = [dict(kind=0, api=5, reg=0, flags=0),
                       dict(kind=0, api=6, reg=8, flags=0),
                       dict(kind=1, api=5, reg=16, flags=0),
                       dict(kind=1, api=6, reg=20, flags=0)]
    return dict(header_offset=HEADER, usage_slots=usage_slots,
                ir=dict(nodes=nodes))


def record(*, cb_mask=4, resource_mask=(1 << 5) | (1 << 6),
           relative=0x80 - 36, blob_size=64):
    return struct.pack("<5I", cb_mask, resource_mask, 0, relative, blob_size)


def invoke(rows, *, contract_list=None, shader_size=SHADER_SIZE, b6_size=None, shader_base=SHADER_BASE,
           candidate=None, allow_independent_samplers=False):
    """Place the selected program record in a realistic 13-row pass table."""
    if contract_list is None:
        contract_list = contracts()
    table = b"".join(rows)
    if b6_size is not None:
        table = table[:b6_size]
    spans = {0xB6D49474: (0x200, len(table)),
             0x873D1E6C: (shader_base, shader_size)}
    data = bytes(spans[0xB6D49474][0]) + table
    with patch("rc2016.shaders.bindings.sections", return_value=spans):
        return program_contracts(data, candidate or program(), contract_list,
                                 allow_independent_samplers=allow_independent_samplers)


def pass_rows(index=13, **changes):
    rows = [record(blob_size=0) for _ in range(26)]
    rows[index] = record(**changes)
    return rows


class IndirectPassJoinTests(unittest.TestCase):
    def test_direct_texture_bindings_join_expected_pass(self):
        self.assertEqual([c["index"] for c in invoke(pass_rows(), candidate=program())], [1])

    def test_static_resource_sampler_tables_join_the_same_pass(self):
        indirect = program(usage_slots=[
            dict(kind=0x13, api=0, reg=12, flags=16),
            dict(kind=0x15, api=0, reg=14, flags=16),
        ], indirect=True)
        indirect_match = invoke(pass_rows(), candidate=indirect)
        self.assertEqual([c["index"] for c in indirect_match], [1])

    def test_independent_tables_are_opt_in_and_resource_api_controls_pass_join(self):
        indirect = program(texture_apis=(16, 17), usage_slots=[
            dict(kind=0x13, api=0, reg=12, flags=16),
            dict(kind=0x15, api=0, reg=14, flags=16),
        ], indirect=True, sampler_apis=(3, 3))
        rows = pass_rows(resource_mask=(1 << 16) | (1 << 17))
        bounded = contracts(resources=13)

        # Strict/native admission remains unchanged. Experimental admission uses
        # resource APIs 16/17 against the pass span and bitmap; sampler API 3 is
        # never treated as a material resource.
        self.assertEqual(invoke(rows, contract_list=bounded, candidate=indirect), [])
        admitted = invoke(rows, contract_list=bounded, candidate=indirect,
                         allow_independent_samplers=True)
        self.assertEqual([c["index"] for c in admitted], [1])

        missing_bitmap_bit = pass_rows(resource_mask=1 << 16)
        self.assertEqual(invoke(missing_bitmap_bit, contract_list=bounded, candidate=indirect,
                                allow_independent_samplers=True), [])
        outside_resource_span = program(texture_apis=(16, 18), usage_slots=[
            dict(kind=0x13, api=0, reg=12, flags=16),
            dict(kind=0x15, api=0, reg=14, flags=16),
        ], indirect=True, sampler_apis=(3, 3))
        outside_rows = pass_rows(resource_mask=(1 << 16) | (1 << 18))
        self.assertEqual(invoke(outside_rows, contract_list=bounded, candidate=outside_resource_span,
                                allow_independent_samplers=True), [])

    def test_equal_api_pair_keeps_legacy_admission_without_sampler_table_marker(self):
        indirect = program(usage_slots=[
            dict(kind=0x13, api=0, reg=12, flags=16),
            dict(kind=0x15, api=0, reg=14, flags=16),
        ], indirect=True)
        for node in indirect["ir"]["nodes"]:
            if node["op"] == "texture":
                node.pop("sampler_table", None)
        self.assertEqual([c["index"] for c in invoke(pass_rows(), candidate=indirect)], [1])

    def test_unequal_api_pair_requires_sampler_table_proof(self):
        indirect = program(usage_slots=[
            dict(kind=0x13, api=0, reg=12, flags=16),
            dict(kind=0x15, api=0, reg=14, flags=16),
        ], indirect=True, sampler_apis=(3, 3))
        for node in indirect["ir"]["nodes"]:
            if node["op"] == "texture":
                node.pop("sampler_table", None)
        self.assertEqual(invoke(pass_rows(), candidate=indirect,
                                allow_independent_samplers=True), [])

    def test_direct_span_cannot_admit_a_mixed_table_layout(self):
        mixed=program(usage_slots=[dict(kind=0x13,api=0,reg=12,flags=16),
            dict(kind=0x15,api=0,reg=14,flags=16),dict(kind=0,api=6,reg=0,flags=0)],indirect=True)
        self.assertEqual(invoke(pass_rows(),candidate=mixed),[])

    def test_dynamic_or_unresolved_texture_api_does_not_join(self):
        candidate = program(texture_apis=(5, None))
        self.assertEqual(invoke(pass_rows(), candidate=candidate), [])

    def test_incomplete_mixed_or_mismatched_indirect_pair_does_not_join(self):
        cases = [
            ([(0x13, 0, 12, 16)], (5, 6), (5, 6)),
            ([(0x13, 0, 12, 16), (1, 6, 8, 0)], (5, 6), (5, 6)),
            ([(0x13, 0, 12, 16), (0x15, 1, 14, 16)], (5, 6), (5, 6)),
            ([(0x13, 0, 12, 16), (0x15, 0, 14, 16)], (5, 6), (5, 7)),
        ]
        for specs, texture_apis, sampler_apis in cases:
            slots = [dict(kind=kind, api=api, reg=reg, flags=flags)
                     for kind, api, reg, flags in specs]
            with self.subTest(slots=slots, sampler_apis=sampler_apis):
                candidate = program(usage_slots=slots, texture_apis=texture_apis,
                                    indirect=True, sampler_apis=sampler_apis)
                self.assertEqual(invoke(pass_rows(), candidate=candidate), [])

    def test_api_outside_resource_span_or_bitmap_does_not_join(self):
        outside_span = program(texture_apis=(5, 7))
        outside_bitmap = pass_rows(resource_mask=(1 << 5))
        self.assertEqual(invoke(pass_rows(), candidate=outside_span), [])
        self.assertEqual(invoke(outside_bitmap), [])

    def test_pointer_mismatch_and_blob_past_shader_section_do_not_join(self):
        pointer_mismatch = pass_rows(relative=0x80 - 36 + 4)
        self.assertEqual(invoke(pointer_mismatch), [])
        with self.assertRaisesRegex(ValueError, "exceeds shader section"):
            invoke(pass_rows(relative=0x80 - 36, blob_size=SHADER_SIZE), shader_size=SHADER_SIZE)

    def test_wrong_cb_mask_or_padded_span_does_not_join(self):
        self.assertEqual(invoke(pass_rows(cb_mask=0)), [])
        wrong_span = program(cb_dwords=(0, 7))
        self.assertEqual(invoke(pass_rows(), candidate=wrong_span), [])

    def test_serialized_table_must_be_exactly_thirteen_records_per_pass(self):
        with self.assertRaisesRegex(ValueError, "serialized shader/pass partition"):
            invoke(pass_rows(), b6_size=25 * 20)

    def test_duplicate_matching_rows_are_ambiguous_not_a_unique_join(self):
        rows = pass_rows()
        rows[14] = record()
        with self.assertRaisesRegex(ValueError, "[Aa]mbiguous"):
            invoke(rows)


if __name__ == "__main__":
    unittest.main()
