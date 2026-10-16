"""Source-bounded controls for the Pokitaru water front-end sampling terms."""
import math
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from rc2016.shaders.water import (
    GLOW_GRAPH,
    GLOW_GRAPH_SHA256,
    GLOW_WRITERS,
    WATER_GRAPH,
    WATER_GRAPH_SHA256,
    WATER_WRITERS,
    water_height_sample_uvs,
    water_primary_normal_sample_uvs,
    water_sampling_contract,
)


class WaterSamplingEquationTests(unittest.TestCase):
    def test_height_and_primary_normal_uvs(self):
        heights = water_height_sample_uvs(2.0, -3.0, 0.5)
        self.assertAlmostEqual(heights[5][0], 0.336)
        self.assertAlmostEqual(heights[5][1], -0.408)
        self.assertAlmostEqual(heights[6][0], 0.3145)
        self.assertAlmostEqual(heights[6][1], 0.4255)

        uv = water_primary_normal_sample_uvs(
            2.0, -3.0, 0.2, 0.7, displacement=0.25, time=0.5)
        # cross displacement = .25 * (.2 + .7 - 1) = -.025
        self.assertAlmostEqual(uv[7][0], 0.133 + 0.630 - 0.025 + 0.1021)
        self.assertAlmostEqual(uv[7][1], 0.346 - 0.855 - 0.025 + 0.098)
        self.assertAlmostEqual(uv[8][0], 0.570 - 0.025 - 0.0985)
        self.assertAlmostEqual(uv[8][1], -0.945 - 0.025 - 0.10065)

    def test_contract_is_exact_graph_and_writer_bound(self):
        writer = next(iter(WATER_WRITERS))
        row = water_sampling_contract(WATER_GRAPH, WATER_GRAPH_SHA256, writer)
        self.assertEqual(row["status"], "source_decoded_sampling_partial")
        self.assertEqual(row["sampling"]["height_cross_displacement"],
                         "CB2[dword] * (sample(API5).R + sample(API6).R - 1)")
        self.assertIsNone(water_sampling_contract(WATER_GRAPH + ".bak", WATER_GRAPH_SHA256, writer))
        self.assertIsNone(water_sampling_contract(WATER_GRAPH, "0" * 64, writer))
        self.assertIsNone(water_sampling_contract(WATER_GRAPH, WATER_GRAPH_SHA256, "f" * 64))

    def test_glow_has_its_own_graph_and_displacement_register(self):
        writer = next(iter(GLOW_WRITERS))
        row = water_sampling_contract(GLOW_GRAPH, GLOW_GRAPH_SHA256, writer)
        self.assertEqual(row["displacement_parameter"]["cb2_dword"], 18)
        self.assertEqual(row["textures_by_api"][9]["path"],
                         "textures/environment/novalis/rp_nov_emissive_foam_tile_01.texture")


class WaterSamplingArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_GAME_ROOT') or os.environ.get("RC2016_RESEARCH")
        if not research:
            raise unittest.SkipTest("RC2016_RESEARCH is not configured")
        from rc2016.formats.archive import Archive, locate, asset_data

        cls.archive = Archive(locate(Path(research)),
                              Path(__file__).resolve().parents[1] / ".cache/water-sampling-test",
                              lambda *_a, **_k: None)
        cls.asset_data = staticmethod(asset_data)
        cls.graph_materials = (
            (WATER_GRAPH,
             "material/visualeffect/environment/pokitaru/unique/pok_uniq_water/pok_uniq_water.material"),
            (WATER_GRAPH,
             "material/visualeffect/environment/pokitaru/unique/pok_uniq_water/rb_ari_hydro_water.material"),
            (WATER_GRAPH,
             "material/visualeffect/environment/pokitaru/unique/pok_uniq_water/pok_uniq_sewer_water.material"),
            (GLOW_GRAPH,
             "material/visualeffect/environment/pokitaru/unique/pok_uniq_water/rp_pok_uniq_water_biolumenscent.material"),
        )
        if any(not cls.archive.resolve(graph) or not cls.archive.resolve(material)
               for graph, material in cls.graph_materials):
            raise unittest.SkipTest("water graph/material pair is not indexed in this archive")

    def source_pair(self, graph_path, material_path):
        from rc2016.shaders.water import water_sampling_rule

        graph = self.asset_data(self.archive, self.archive.resolve(graph_path)[0])
        material = self.asset_data(self.archive, self.archive.resolve(material_path)[0])
        return graph, material, water_sampling_rule(graph, material)

    def test_archive_materials_resolve_instance_displacement_and_graph_slots(self):
        from rc2016.shaders.water import water_sampling_rule

        for graph_path, material_path in self.graph_materials:
            graph = self.asset_data(self.archive, self.archive.resolve(graph_path)[0])
            material = self.asset_data(self.archive, self.archive.resolve(material_path)[0])
            row = water_sampling_rule(graph, material)
            self.assertEqual(row["status"], "source_decoded_sampling_partial")
            self.assertAlmostEqual(row["sampling"]["displacement"], 0.025, places=7)
            self.assertAlmostEqual(row["displacement"], row["sampling"]["displacement"])
            self.assertEqual(row["textures"]["_water_height_a"],
                             "textures/visualeffect/water/water_tile_01.texture")
            self.assertEqual(row["textures"]["_water_height_b"],
                             "textures/visualeffect/water/water_tile_02.texture")
            self.assertEqual(row["textures"]["_n"],
                             "textures/visualeffect/water/water_tile_01n.texture")
            self.assertEqual(row["textures"]["_pick"],
                             "textures/visualeffect/water/water_tile_02n.texture")
            self.assertEqual(row["sampling"]["frozen_time"], 0.0)
            self.assertTrue(any("normal composition" in text for text in row["limitations"]))

    def test_material_override_path_wins_and_conflicts_fail_closed(self):
        from rc2016.formats import materials as material_format
        from rc2016.shaders.water import water_sampling_rule

        graph_path, material_path = self.graph_materials[0]
        graph = self.asset_data(self.archive, self.archive.resolve(graph_path)[0])
        material = self.asset_data(self.archive, self.archive.resolve(material_path)[0])
        original = material_format.texture_slot_records
        source_rows = original(material, material_format.MATERIAL_TEXTURE_SLOTS)
        override = dict(slot_key=0x48F15502, path="override/custom-water-height.texture")

        def slots(data, tag):
            if tag == material_format.MATERIAL_TEXTURE_SLOTS:
                return [row for row in source_rows if row["slot_key"] != override["slot_key"]] + [override]
            return original(data, tag)

        with patch.object(material_format, "texture_slot_records", side_effect=slots):
            row = water_sampling_rule(graph, material)
        self.assertEqual(row["textures"]["_water_height_a"], override["path"])

        def conflicting(data, tag):
            if tag == material_format.MATERIAL_TEXTURE_SLOTS:
                return [*slots(data, tag), dict(override, path="override/conflicting.texture")]
            return original(data, tag)

        with patch.object(material_format, "texture_slot_records", side_effect=conflicting):
            with self.assertRaisesRegex(ValueError, "conflicting water texture overrides"):
                water_sampling_rule(graph, material)

    def test_material_must_reference_the_exact_source_graph(self):
        from rc2016.formats import index as index_format
        from rc2016.shaders.water import water_sampling_rule

        graph_path, material_path = self.graph_materials[0]
        graph = self.asset_data(self.archive, self.archive.resolve(graph_path)[0])
        material = self.asset_data(self.archive, self.archive.resolve(material_path)[0])
        with patch.object(index_format, "references", return_value=[]):
            with self.assertRaisesRegex(ValueError, "material does not reference the exact water graph"):
                water_sampling_rule(graph, material)
        with self.assertRaisesRegex(ValueError, "unsupported water graph SHA-256"):
            water_sampling_rule(graph + b"changed", material)

    def test_explicit_empty_required_texture_slot_fails_closed(self):
        from rc2016.formats import materials as material_format
        from rc2016.shaders.water import water_sampling_rule

        graph_path, material_path = self.graph_materials[0]
        graph = self.asset_data(self.archive, self.archive.resolve(graph_path)[0])
        material = self.asset_data(self.archive, self.archive.resolve(material_path)[0])
        original = material_format.texture_slot_records

        def slots(data, tag):
            if tag == material_format.MATERIAL_TEXTURE_SLOTS:
                return [{"slot_key": 0x48F15502, "path": None}]
            return original(data, tag)

        with patch.object(material_format, "texture_slot_records", side_effect=slots):
            with self.assertRaisesRegex(ValueError, "explicitly empty required texture slot"):
                water_sampling_rule(graph, material)

    def test_decoded_forward_uv_coordinates_match_numeric_contract(self):
        from rc2016.formats.archive import asset_data
        from rc2016.shaders import translator
        from rc2016.shaders.translator import decoder, pixel_programs
        from rc2016.shaders.water import water_primary_normal_sample_uvs

        graph_path = self.graph_materials[0][0]
        graph = asset_data(self.archive, self.archive.resolve(graph_path)[0])
        program = next(row for row in pixel_programs(graph)
                       if row["shader_id"].lower() == "0xf4d47ece")
        original_instruction = translator.Compiler.instruction
        stop_before_loop = RuntimeError("stop before water light loop")

        def projected_instruction(self, instruction):
            if instruction.get("relative") == 3196:
                raise stop_before_loop
            if instruction["kind"] != 14:
                return original_instruction(self, instruction)
            resource_base = ((instruction["wide"] >> 48) & 31) * 4
            resource = self.texture_descriptor(resource_base, 0, 8)
            saved = {}
            if (resource is None or resource.get("table_slot") != (19, 0, 12, 48)
                    or resource.get("guard") != 0):
                return original_instruction(self, instruction)
            for reg in range(resource_base, resource_base + 8):
                descriptor = self.descriptors.get(reg)
                if (descriptor and descriptor.get("api") == resource["api"]
                        and descriptor.get("table_slot") == (19, 0, 12, 48)
                        and descriptor.get("guard") == 0):
                    saved[reg] = (descriptor.pop("table_slot"), descriptor.pop("guard"))
            try:
                return original_instruction(self, instruction)
            finally:
                for reg, (table_slot, guard) in saved.items():
                    self.descriptors[reg]["table_slot"] = table_slot
                    self.descriptors[reg]["guard"] = guard

        compiler = translator.Compiler(
            program["code"], program["usage_slots"], program.get("ps_input_addr"),
            program.get("ps_input_ena"), program.get("vgpr_count"),
            allow_independent_samplers=True)
        with patch.object(translator.Compiler, "instruction", projected_instruction):
            with self.assertRaisesRegex(RuntimeError, "stop before water light loop"):
                compiler.run(decoder().decode_stream(program["code"], 0))

        samples = {row["api"]: row for row in compiler.samples
                   if row["offset"] in (2136, 2144)}
        self.assertEqual(set(samples), {7, 8})
        input_values = {
            "CB0[28]": 10.0, "CB0[30]": 2.0,
            "attribute3.0": 8.75, "attribute3.2": 2.75,
            "CB2[36]": 0.25,
        }
        coords = {}
        dag = compiler.ir
        for api, sample in samples.items():
            roots = sample["coordinates"]
            reachable = dag.reachable(roots)
            time_nodes = [node for node in reachable
                          if dag.nodes[node]["op"] == "unsupported"]
            self.assertTrue(time_nodes)
            substitutions = {}
            for node in time_nodes:
                record = dag.nodes[node]
                self.assertEqual(record["reason"], "Unresolved scalar memory load")
                self.assertEqual((record["offset"], record["base"], record["load_offset"]),
                                 (620, 28, 396))
                substitutions[node] = dag.const(0.0)  # explicit frozen-time preview input
            evaluated_roots = dag.replace(roots, substitutions)
            texture_values = {}
            for node in dag.reachable(evaluated_roots):
                record = dag.nodes[node]
                if record["op"] == "texture":
                    self.assertIn(record["api"], (5, 6))
                    texture_values[(record["sample"], record["channel"])] = (
                        0.2 if record["api"] == 5 else 0.7)
            coords[api] = tuple(dag.evaluate(root, input_values, texture_values)
                                for root in evaluated_roots)

        expected = water_primary_normal_sample_uvs(
            1.25, -0.75, 0.2, 0.7, displacement=0.25, time=0.0)
        for api in (7, 8):
            for actual, target in zip(coords[api], expected[api]):
                self.assertAlmostEqual(actual, target, places=6)

    def test_wrong_width_and_non_finite_material_override_fail_closed(self):
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.shaders.water import water_sampling_rule

        graph_path, material_path = self.graph_materials[0]
        graph = self.asset_data(self.archive, self.archive.resolve(graph_path)[0])
        material = self.asset_data(self.archive, self.archive.resolve(material_path)[0])
        parsed = extract_material_parameters(material)
        matching = [row for row in parsed["parameters"] + parsed["unsupported_parameters"]
                    if row["key_hash"] == 0x230C6389]
        self.assertTrue(matching)

        wrong_width = bytearray(material)
        for row in matching:
            struct.pack_into("<H", wrong_width, row["entry_file_offset"] + 2, 8)
        with self.assertRaises((ValueError, struct.error)):
            water_sampling_rule(graph, wrong_width)

        non_finite = bytearray(material)
        for row in matching:
            struct.pack_into("<f", non_finite, row["value_file_offset"], math.nan)
        with self.assertRaisesRegex(ValueError, "Non-finite float material parameter"):
            water_sampling_rule(graph, non_finite)


if __name__ == "__main__":
    unittest.main()
