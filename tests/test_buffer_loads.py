"""Focused tests for bounded MUBUF/MTBUF destination writes.

These intentionally assert unknown values: this fixture tests register span and
provenance only, never memory evaluation, sampler state, or vertex semantics.
"""
import unittest

from rc2016.shaders.translator import Compiler


# The MUBUF enum values are from third_party/opengnm/gcn_types.h; MTBUF has
# the same four typed-load widths. The scalar loads each occupy one VGPR,
# DWORDxN loads occupy N VGPRs. All optional TFE words follow loaded data.
LOAD_CASES = [
    (12, "BUFFER_LOAD_FORMAT_X", 1),
    (12, "BUFFER_LOAD_FORMAT_XY", 2),
    (12, "BUFFER_LOAD_FORMAT_XYZ", 3),
    (12, "BUFFER_LOAD_FORMAT_XYZW", 4),
    (12, "BUFFER_LOAD_UBYTE", 1),
    (12, "BUFFER_LOAD_SBYTE", 1),
    (12, "BUFFER_LOAD_USHORT", 1),
    (12, "BUFFER_LOAD_SSHORT", 1),
    (12, "BUFFER_LOAD_DWORD", 1),
    (12, "BUFFER_LOAD_DWORDX2", 2),
    (12, "BUFFER_LOAD_DWORDX3", 3),
    (12, "BUFFER_LOAD_DWORDX4", 4),
    (13, "TBUFFER_LOAD_FORMAT_X", 1),
    (13, "TBUFFER_LOAD_FORMAT_XY", 2),
    (13, "TBUFFER_LOAD_FORMAT_XYZ", 3),
    (13, "TBUFFER_LOAD_FORMAT_XYZW", 4),
]


def instruction(kind, name, dst, *, tfe=False, relative=44,
                vaddr=4, srsrc=8, soffset=12):
    """A decoded-instruction fixture that enters the translator after decode."""
    q = (int(tfe) << 55)
    return {
        "relative": relative,
        "length": 0,
        "kind": kind,
        "name": name,
        "word": 0,
        "wide": q,
        "fields": {"src": [vaddr, srsrc, soffset], "dst": [dst]},
    }


def buffer_nodes(compiler, regs):
    return [compiler.ir.nodes[compiler.v[r]] for r in regs]


class BufferLoadWriteContractTests(unittest.TestCase):
    def compiler(self):
        return Compiler(b"", [])

    def test_named_load_spans_preserve_neighbours_and_distinguish_components(self):
        for kind, name, width in LOAD_CASES:
            for tfe in (False, True):
                with self.subTest(kind=kind, opcode=name, tfe=tfe):
                    c = self.compiler()
                    dst = 20
                    old = {r: c.ir.const(1000 + r) for r in range(dst - 1, dst + width + int(tfe) + 1)}
                    c.v.update(old)
                    c.instruction(instruction(kind, name, dst, tfe=tfe))

                    data_regs = list(range(dst, dst + width))
                    data = buffer_nodes(c, data_regs)
                    self.assertTrue(all(node["op"] == "unsupported" for node in data))
                    self.assertEqual(len({c.v[r] for r in data_regs}), width,
                                     "different loaded words must not share one unknown")
                    self.assertEqual([node["buffer_access"]["component"] for node in data],
                                     list(range(width)))
                    for node in data:
                        proof = node["buffer_access"]
                        self.assertEqual(proof["kind"], "MUBUF" if kind == 12 else "MTBUF")
                        self.assertEqual(proof["opcode"], name)
                        self.assertEqual(proof["role"], "data")
                        self.assertEqual(node["reason"], name)

                    if tfe:
                        status = c.ir.nodes[c.v[dst + width]]
                        self.assertEqual(status["op"], "unsupported")
                        self.assertNotEqual(c.v[dst + width], c.v[dst + width - 1])
                        self.assertEqual(status["buffer_access"]["role"], "tfe_status")
                        self.assertIs(status["buffer_access"]["tfe"], True)
                    else:
                        self.assertEqual(c.v[dst + width], old[dst + width])
                    self.assertEqual(c.v[dst - 1], old[dst - 1])
                    self.assertEqual(c.v[dst + width + int(tfe)], old[dst + width + int(tfe)])

    def test_different_unknown_components_do_not_cancel_as_equal(self):
        c = self.compiler()
        c.instruction(instruction(12, "BUFFER_LOAD_FORMAT_XY", 6))
        x, y = c.v[6], c.v[7]
        difference = c.ir.op("sub", x, y)
        self.assertNotEqual(c.ir.value(difference), 0.,
                            "same-reason buffer components must not fold to zero")
        self.assertNotEqual(x, y)

    def test_tfe_status_is_not_an_extra_data_component(self):
        c = self.compiler()
        c.instruction(instruction(12, "BUFFER_LOAD_FORMAT_XYZW", 24, tfe=True))
        values = buffer_nodes(c, range(24, 29))
        self.assertEqual([n["buffer_access"]["role"] for n in values],
                         ["data", "data", "data", "data", "tfe_status"])
        self.assertEqual([n["buffer_access"]["component"] for n in values[:4]], [0, 1, 2, 3])
        self.assertIsNone(values[4]["buffer_access"]["component"])
        for root in (c.v[r] for r in range(24, 29)):
            with self.assertRaisesRegex(ValueError, "BUFFER_LOAD_FORMAT_XYZW"):
                c.ir.evaluate(root)

    def test_predicated_load_keeps_each_unknown_component_and_status_independent(self):
        c = self.compiler()
        flag = c.ir.node("input", name="test_flag", kind="runtime_flag")
        c.bp = flag
        c.v.update({r: c.ir.const(200 + r) for r in range(30, 36)})
        c.step(instruction(12, "BUFFER_LOAD_FORMAT_XYZ", 31, tfe=True))
        for reg, role, component in [(31, "data", 0), (32, "data", 1), (33, "data", 2),
                                     (34, "tfe_status", None)]:
            with self.subTest(reg=reg):
                root = c.v[reg]
                node = c.ir.nodes[root]
                self.assertEqual(node["op"], "select")
                load_nodes = [c.ir.nodes[index] for index in c.ir.reachable([root])
                              if c.ir.nodes[index]["op"] == "unsupported"
                              and c.ir.nodes[index].get("reason") == "BUFFER_LOAD_FORMAT_XYZ"]
                self.assertEqual(len(load_nodes), 1)
                proof = load_nodes[0]["buffer_access"]
                self.assertEqual(proof["role"], role)
                self.assertEqual(proof["component"], component)
        self.assertEqual(c.ir.value(c.v[30]), 230)
        self.assertEqual(c.ir.value(c.v[35]), 235)

    def test_recorded_access_is_diagnostic_only_and_keeps_raw_operands(self):
        c = self.compiler()
        c.instruction(instruction(12, "BUFFER_LOAD_DWORDX2", 10, tfe=True,
                                  relative=92, vaddr=17, srsrc=28, soffset=6))
        node = c.ir.nodes[c.v[10]]
        access = node["buffer_access"]
        self.assertEqual(access["instruction_offset"], 92)
        self.assertEqual(access["operands"], {"vaddr": 17, "srsrc": 28, "soffset": 6, "vdata": 10})
        self.assertFalse(access["memory_evaluated"])
        self.assertEqual(access["bounds"], "unproven")
        self.assertEqual(c.ir.nodes[c.v[10]]["op"], "unsupported")


if __name__ == "__main__":
    unittest.main()

