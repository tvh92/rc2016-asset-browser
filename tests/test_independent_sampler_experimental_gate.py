"""Focused cache and receipt-gate checks for independent sampler tables.

These mocks test option threading and cache separation only; they do not decode
a source graph or establish runtime draw selection or sampler-state behavior.
"""
import unittest
from unittest.mock import patch

from rc2016.shaders import bindings


class IndependentSamplerExperimentalGateTests(unittest.TestCase):
    def test_compiled_cache_separates_strict_and_experimental_modes(self):
        data = b"independent-sampler-cache-separation-test"
        seen = []
        bindings._compiled.cache_clear()
        try:
            with patch.object(bindings, "_pooled", side_effect=lambda fn, blob: fn(blob)), \
                 patch.object(bindings, "compile_graph",
                              side_effect=lambda blob, allow_independent_samplers=False:
                              seen.append((blob, allow_independent_samplers)) or
                              {"mode": allow_independent_samplers}):
                strict = bindings.compiled(data)
                strict_again = bindings.compiled(data)
                preview = bindings.compiled(data, allow_independent_samplers=True)
                preview_again = bindings.compiled(data, allow_independent_samplers=True)
            self.assertEqual(strict["mode"], False)
            self.assertEqual(strict_again, strict)
            self.assertEqual(preview["mode"], True)
            self.assertEqual(preview_again, preview)
            self.assertEqual(seen, [(data, False), (data, True)])
        finally:
            bindings._compiled.cache_clear()

    def test_one_compile_serves_both_modes_unless_the_mode_mattered(self):
        hits = [0]
        seen = []

        def compile_graph(blob, allow_independent_samplers=False):
            seen.append((blob, allow_independent_samplers))
            hits[0] += blob.startswith(b"mismatch")   # the sampler mode changed this compile
            return {"programs": [], "mode": allow_independent_samplers}
        bindings._compiled.cache_clear()
        try:
            with patch.object(bindings, "_pooled", side_effect=lambda fn, blob: fn(blob)),                  patch.object(bindings, "compile_graph", side_effect=compile_graph),                  patch.object(bindings, "sampler_mode_hits", side_effect=lambda: hits[0]):
                self.assertEqual(bindings.compiled(b"plain")["mode"], False)
                self.assertEqual(bindings.compiled(b"plain", allow_independent_samplers=True)["mode"], False)
                self.assertEqual(bindings.compiled(b"mismatch", allow_independent_samplers=True)["mode"], True)
                self.assertEqual(bindings.compiled(b"mismatch")["mode"], False)
            self.assertEqual(seen, [(b"plain", False), (b"mismatch", True), (b"mismatch", False)])
        finally:
            bindings._compiled.cache_clear()

    def test_blocked_receipt_keeps_proven_descriptor_candidate_without_claiming_ready(self):
        sample = dict(api=17, sampler_api=3, offset=2328, resource_table=True, sampler_table=True)
        source = dict(index=17, pass_index=1, resource_mask=(1 << 17))
        program = dict(status="compiled", shader_id="fixture", code_sha256="code",
                       ir=dict(samples=[sample]))
        graph = dict(programs=[program], graph_sha256="graph", translator_version=14, limitations=[])
        contract = dict(index=1, source_shader_record=source)
        with patch.object(bindings, "compiled", return_value=graph), \
             patch.object(bindings, "pass_contracts", return_value=[contract]), \
             patch.object(bindings, "program_contracts", return_value=[contract]), \
             patch.object(bindings, "graph_stream_evidence", return_value=(set(), {})), \
             patch.object(bindings, "_bind", side_effect=ValueError("Unbound engine input")):
            result = bindings.prepare_translation([b"graph"], b"material", {}, world_position=True)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["game_equivalence"])
        self.assertNotIn("nodes", result)
        self.assertEqual(result["independent_sampler_candidates"], [dict(shader_id="fixture",
            code_sha256="code", source_shader_record=source,
            samples=[dict(api=17, sampler_api=3, code_offset=2328)])])

    def test_prepare_translation_opts_in_only_for_world_position_preview(self):
        program = dict(status="compiled", shader_id="fixture", code_sha256="code",
                       code_offset=100, header_offset=200)
        graph = dict(programs=[program], graph_sha256="graph", translator_version=14,
                     limitations=[])
        contract = dict(index=1, resources=[])
        bound = dict(nodes=[dict(op="const", args=[], value=0.)],
                     outputs=dict(albedo=[0, 0, 0], F0=[0, 0, 0], gloss=0, emission=0),
                     normal=None, texture_bindings=[], pass_contract_index=1, limitations=[])
        calls = []

        def join(_data, _program, contracts, allow_independent_samplers=False):
            calls.append(allow_independent_samplers)
            return contracts

        with patch.object(bindings, "compiled", return_value=graph) as compile_call, \
             patch.object(bindings, "pass_contracts", return_value=[contract]), \
             patch.object(bindings, "program_contracts", side_effect=join), \
             patch.object(bindings, "graph_stream_evidence", return_value=(set(), {})), \
             patch.object(bindings, "_bind", return_value=bound), \
             patch.object(bindings, "surface_signature", return_value="same-surface"):
            strict = bindings.prepare_translation([b"graph"], b"material", {})
            preview = bindings.prepare_translation([b"graph"], b"material", {}, world_position=True)

        self.assertEqual(strict["status"], "ready")
        self.assertEqual(preview["status"], "experimental")
        self.assertEqual(calls, [False, True])
        self.assertEqual([call.kwargs.get("allow_independent_samplers", False)
                          for call in compile_call.call_args_list], [False, True])


if __name__ == "__main__":
    unittest.main()
