import math
import os
import unittest
from pathlib import Path

from rc2016.shaders.lava_ocean import (
    GRAPH, GRAPH_SHA256, GASPAR_LAVA_OCEAN_WRITER_SHA256,
    surface_rule, valid_contract,
)


class LavaOceanContractTests(unittest.TestCase):
    def test_hash_gate_and_receipt_gate_are_exact(self):
        with self.assertRaisesRegex(ValueError, "graph SHA-256"):
            surface_rule(b"not the source graph", b"")
        self.assertFalse(valid_contract(None))
        self.assertFalse(valid_contract({"status": "partial_source_rule"}))
        self.assertFalse(valid_contract({
            "status": "partial_source_rule", "preview_contract": "gaspar-lava-source-v1",
            "graph_path": GRAPH, "graph_sha256": GRAPH_SHA256,
            "writer_sha256": "0" * 64, "shader_id": "0xce49c867",
            "normal": None, "outputs": {"albedo": [], "F0": [], "gloss": 0, "emission": 0},
            "texture_apis": [5, 6, 7],
        }))

    def test_real_archive_source_contract_and_numeric_dag(self):
        research = os.environ.get("RC2016_GAME_ROOT") or os.environ.get("RC2016_RESEARCH") or os.environ.get("RC2016_ROOT")
        if not research or not Path(research).exists():
            self.skipTest("RC2016_RESEARCH is not configured")
        from rc2016.formats.archive import Archive, asset_data, locate
        from rc2016.formats.index import references
        from rc2016.shaders.bindings import _parameter_values, pass_contracts
        from rc2016.formats.materials import extract_material_parameters

        archive = Archive(locate(Path(research)), Path(__file__).resolve().parents[1] / ".cache/liquid-ocean-test",
                          lambda *_args, **_kwargs: None)
        self.addCleanup(archive.close)
        graph_ids = archive.resolve(GRAPH)
        material_path = "material/environment/global/gameplay/gbl_gp_lava_plane_01/gas_lava_02.material"
        material_ids = archive.resolve(material_path)
        if not graph_ids or not material_ids:
            self.skipTest("Gaspar lava-ocean source graph/material are absent from this archive")
        graph_data, material_data = asset_data(archive, graph_ids[0]), asset_data(archive, material_ids[0])
        self.assertIn(GRAPH, {row.get("path") for row in references(material_data)})
        receipt = surface_rule(graph_data, material_data)
        self.assertTrue(valid_contract(receipt))
        self.assertEqual(receipt["writer_sha256"], GASPAR_LAVA_OCEAN_WRITER_SHA256)
        self.assertEqual(receipt["pass_index"], 0)
        self.assertEqual(receipt["texture_apis"], [5, 6, 7])
        self.assertEqual(receipt["world_position_terms_replaced"], 18)
        self.assertEqual(receipt["normal"], None)
        self.assertEqual(
            {node["name"] for node in receipt["nodes"] if node["op"] == "input"},
            {"preview_position.0", "preview_position.2"},
        )
        self.assertTrue(any("API9 cube reflection is black" in item for item in receipt["limitations"]))

        # Both serialized passes resolve to identical CB2 values for the live
        # material instance; this is a source-table check, not semantic naming.
        instance = extract_material_parameters(material_data)
        contracts = pass_contracts(graph_data)
        self.assertEqual(len(contracts), 2)
        first, _ = _parameter_values(contracts[0], instance)
        second, _ = _parameter_values(contracts[1], instance)
        self.assertEqual(first, second)

        # Evaluate the actual compact decoded writer with explicit review
        # positions and deterministic texels, checking that it is executable
        # and that changing source samples changes at least one material term.
        from rc2016.viewer.native_glsl import evaluate_surface, emit_surface
        from rc2016.shaders.lava_surface import prepare_rule, valid_contract as sealed_contract
        receipt = prepare_rule(GRAPH, graph_data, material_data)
        self.assertTrue(sealed_contract(receipt))
        emit_surface(receipt, rule_preview=True)
        with self.assertRaises(ValueError):
            emit_surface(receipt)
        inputs = {"preview_position.0": 0.25, "preview_position.2": 0.75}
        def evaluate(texel):
            return evaluate_surface(receipt, (0,0), lambda api, uv: (texel,)*4, preview_inputs=inputs)
        output_a, output_b = evaluate(0.2), evaluate(0.8)
        for output in (output_a, output_b):
            values = [x for name,value in output.items() if value is not None
                      for x in (value if isinstance(value, (list,tuple)) else [value])]
            self.assertTrue(all(math.isfinite(value) for value in values))
        self.assertNotEqual(output_a, output_b)
        receipt['source_substitutions']['CB0[115]']['value'] = 2.0
        self.assertFalse(sealed_contract(receipt))
