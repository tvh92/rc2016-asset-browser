"""Fail-closed tests for explicit static-vector preview input selection."""
import unittest

from rc2016.shaders.static_vectors import StaticCompiler
from rc2016.shaders.translator import Compiler
from rc2016.viewer.viewport import ModelViewport
from rc2016.levels.viewport import LevelViewport
from tests.test_vertex_context import run, s_buffer_load


CB1_SLOTS = [dict(kind=2, api=1, reg=0, flags=0)]
STATIC_PREVIEW = dict(status="experimental", preview_contract="static-packed-vector-preview-v1",
                      translation_sha256="static")
NATIVE = dict(status="ready", translation_sha256="native")


def _static_compiler(code):
    return StaticCompiler(code, [dict(row) for row in CB1_SLOTS], flags=0x5A)


def _extended_compiler(code, *, target_slots=None):
    slots = [dict(kind=0x1B, api=0, reg=12, flags=0),
             dict(kind=2, api=1, reg=16, flags=0)]
    if target_slots is not None:
        slots.extend(target_slots)
    return StaticCompiler(code, slots, flags=0x5A)


class StaticCompilerDescriptorTests(unittest.TestCase):
    def test_complete_cb1_descriptor_resolves_flag_word_and_keeps_other_words_symbolic(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 60) + \
               s_buffer_load("S_BUFFER_LOAD_DWORD", 9, 0, 0)
        compiler = run(_static_compiler(code))
        self.assertEqual(compiler.scalar_bits[8], 0x5A)
        self.assertEqual(compiler.ir.value(compiler.s[8]), 0x5A)
        self.assertEqual(compiler.ir.nodes[compiler.s[9]]["op"], "input")
        self.assertEqual((compiler.ir.nodes[compiler.s[9]]["kind"],
                          compiler.ir.nodes[compiler.s[9]]["api"],
                          compiler.ir.nodes[compiler.s[9]]["dword"]), ("cb", 1, 0))

    def test_partial_mixed_and_tainted_cb1_descriptors_reject_the_read(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORDX4", 20, 0, 0)
        cases = []
        compiler = _static_compiler(code)
        del compiler.descriptors[2]
        cases.append(("missing word", compiler))
        compiler = _static_compiler(code)
        compiler.descriptors[2] = dict(compiler.descriptors[2], api=2)
        cases.append(("mixed API identity", compiler))
        compiler = _static_compiler(code)
        compiler.buffer_descriptor_taint.add(1)
        cases.append(("tainted word", compiler))
        for label, compiler in cases:
            with self.subTest(label=label):
                run(compiler)
                for reg in range(20, 24):
                    self.assertEqual(compiler.ir.nodes[compiler.s[reg]]["op"], "unsupported")
                    self.assertNotIn(reg, compiler.scalar_bits)

    def test_unknown_or_out_of_record_span_reads_reject_every_destination_word(self):
        cases = (
            ("dynamic offset", s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 4,
                                               immediate=False), 8, 1),
            ("x2 crossing 272-byte boundary", s_buffer_load("S_BUFFER_LOAD_DWORDX2", 20, 0, 67), 20, 2),
        )
        for label, code, first, count in cases:
            with self.subTest(label=label):
                compiler = run(_static_compiler(code))
                for reg in range(first, first + count):
                    self.assertEqual(compiler.ir.nodes[compiler.s[reg]]["op"], "unsupported")
                    self.assertNotIn(reg, compiler.scalar_bits)

    def test_non_cb1_and_ordinary_compiler_cb1_reads_remain_symbolic(self):
        code = s_buffer_load("S_BUFFER_LOAD_DWORD", 8, 0, 0)
        other_api = StaticCompiler(code, [dict(kind=2, api=2, reg=0, flags=0)], flags=0x5A)
        run(other_api)
        node = other_api.ir.nodes[other_api.s[8]]
        self.assertEqual((node["op"], node["kind"], node["api"], node["dword"]),
                         ("input", "cb", 2, 0))

        ordinary = run(Compiler(code, [dict(kind=2, api=1, reg=0, flags=0)]))
        self.assertEqual(ordinary.ir.nodes[ordinary.s[8]]["op"], "input")
        self.assertEqual(ordinary.ir.nodes[ordinary.s[8]]["name"], "CB1[0]")

    def test_complete_unconditional_extended_cb1_fetch_refreshes_full_descriptor_and_clears_stale_taint(self):
        code = s_buffer_load("S_LOAD_DWORDX4", 20, 12, 0)
        compiler = _extended_compiler(code)
        compiler.buffer_descriptor_taint.update(range(20, 24))
        run(compiler)
        self.assertFalse(set(range(20, 24)) & compiler.buffer_descriptor_taint)
        self.assertIsNotNone(compiler.texture_descriptor(20, 2, 4))
        self.assertEqual([compiler.descriptors[20 + word]["word"] for word in range(4)],
                         list(range(4)))

    def test_incomplete_dynamic_conditional_mixed_ambiguous_or_tainted_extended_refresh_keeps_stale_taint(self):
        code = s_buffer_load("S_LOAD_DWORDX4", 20, 12, 0)
        cases = []

        compiler = _extended_compiler(code)
        del compiler.descriptors[13]
        cases.append(("partial pointer", compiler))

        compiler = _extended_compiler(code)
        compiler.descriptors[13] = dict(compiler.descriptors[13], api=1)
        cases.append(("mixed pointer identity", compiler))

        compiler = _extended_compiler(code)
        compiler.buffer_descriptor_taint.add(13)
        cases.append(("tainted pointer", compiler))

        compiler = _extended_compiler(code)
        compiler.bp = compiler.ir.node("input", name="branch", kind="runtime_flag")
        cases.append(("conditional block", compiler))

        compiler = _extended_compiler(code)
        compiler.exec = compiler.ir.node("input", name="exec", kind="runtime_flag")
        cases.append(("conditional execution mask", compiler))

        compiler = _extended_compiler(s_buffer_load("S_LOAD_DWORDX4", 20, 12, 0, immediate=False))
        cases.append(("dynamic offset", compiler))

        compiler = _extended_compiler(code, target_slots=[dict(kind=2, api=1, reg=16, flags=1)])
        cases.append(("ambiguous destination slot", compiler))

        for label, compiler in cases:
            with self.subTest(label=label):
                compiler.buffer_descriptor_taint.update(range(20, 24))
                run(compiler)
                self.assertTrue(set(range(20, 24)) & compiler.buffer_descriptor_taint)

    def test_base_compiler_does_not_clear_historical_stale_descriptor_taint(self):
        code = s_buffer_load("S_LOAD_DWORDX4", 20, 12, 0)
        compiler = Compiler(code, [dict(kind=0x1B, api=0, reg=12, flags=0),
                                   dict(kind=2, api=1, reg=16, flags=0)])
        compiler.buffer_descriptor_taint.update(range(20, 24))
        run(compiler)
        self.assertEqual(set(range(20, 24)) & compiler.buffer_descriptor_taint, set(range(20, 24)))


class StaticVectorSurfaceEligibilityTests(unittest.TestCase):
    def _view(self, payload):
        view = object.__new__(ModelViewport)
        view._payload = payload
        view.experimental_materials = True
        view.native_translation = True
        return view

    def test_model_preview_requires_payload_flag_and_complete_raw_vector_count(self):
        valid = dict(static_vector_eligible=True, vertices=[0, 1, 2],
                     raw_vectors=[0, 1, 2])
        for label, payload in (
            ("missing", dict(static_vector_eligible=True, vertices=[0, 1, 2])),
            ("count mismatch", dict(valid, raw_vectors=[0, 1])),
            ("payload ineligible", dict(valid, static_vector_eligible=False)),
        ):
            with self.subTest(label=label):
                view = self._view(payload)
                self.assertFalse(view._static_vector_input_eligible())
                entry = dict(experimental_preview=STATIC_PREVIEW, experimental_textures={},
                             native_translation=NATIVE, native_textures={})
                selected, _, experimental = view._selected_surface(entry, 2)
                self.assertIs(selected, NATIVE)
                self.assertFalse(experimental)

        view = self._view(valid)
        self.assertTrue(view._static_vector_input_eligible())
        entry = dict(experimental_preview=STATIC_PREVIEW, experimental_textures={},
                     native_translation=NATIVE, native_textures={})
        selected, _, experimental = view._selected_surface(entry, 2)
        self.assertIs(selected, STATIC_PREVIEW)
        self.assertTrue(experimental)

    def test_level_model_gate_controls_the_static_draw_eligibility(self):
        view = object.__new__(LevelViewport)
        view.native_translation = True
        view.experimental_materials = True
        view._shading = None
        view.show_actors = False
        view.show_spawn_preview = False
        model = dict(static_vector_eligible=True, svbo=7,
                     zones=[("zone", (), [])], static_vector_zone_eligibility={"zone": True},
                     actor_zones=[], actors=[], spawn_zones=[], spawns=[])
        eligible = view._static_vector_eligible_for_model(model)
        self.assertTrue(eligible)
        view._static_vector_draw_eligible = eligible
        entry = dict(experimental_preview=STATIC_PREVIEW, experimental_textures={},
                     native_translation=NATIVE, native_textures={})
        self.assertIs(view._selected_surface(entry, 2)[0], STATIC_PREVIEW)
        view._static_vector_draw_eligible = view._static_vector_eligible_for_model(
            dict(model, static_vector_zone_eligibility={"zone": False}))
        self.assertFalse(view._static_vector_draw_eligible)
        self.assertIs(view._selected_surface(entry, 2)[0], NATIVE)

    def test_program_memo_separates_eligible_static_and_native_surfaces(self):
        class Shading:
            def __init__(self):
                self.calls = []

            def native_program(self, translation, _srgb, _instanced, **kwargs):
                result = object()
                self.calls.append((translation, kwargs, result))
                return result

        view = object.__new__(ModelViewport)
        view._shading = Shading()
        view._static_vector_draw_eligible = True
        view.experimental_materials = True
        view.native_translation = True
        view._bone_count = 0
        entry = dict(experimental_preview=STATIC_PREVIEW, experimental_textures={},
                     native_translation=NATIVE, native_textures={})
        static_program = view._surface_program(entry, 2)
        view._static_vector_draw_eligible = False
        native_program = view._surface_program(entry, 2)
        self.assertIsNot(static_program, native_program)
        self.assertEqual(len(view._shading.calls), 2)
        self.assertIs(view._shading.calls[0][0], STATIC_PREVIEW)
        self.assertTrue(view._shading.calls[0][1]["experimental"])
        self.assertIs(view._shading.calls[1][0], NATIVE)
        self.assertEqual(view._shading.calls[1][1], {})
        self.assertIs(view._surface_program(entry, 2), native_program)
        self.assertEqual(len(view._shading.calls), 2)


if __name__ == "__main__":
    unittest.main()
