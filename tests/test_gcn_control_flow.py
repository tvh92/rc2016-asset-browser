"""Forward control flow and exec masks are linearised by predication."""
import struct
import unittest

from rc2016.shaders import gcn
from rc2016.shaders.ir import DAG
from rc2016.shaders.translator import Compiler, decoder

V = 256  # VGPR operand base


def op(table, name):
    return gcn.enum_value(table, 'GCN_' + name)


def sop1(name, dst, src):
    return struct.pack('<I', (0x17d << 23) | (dst << 16) | (op('GcnOpcodeSOP1', name) << 8) | src)


def sop2(name, dst, src0, src1):
    return struct.pack('<I', (2 << 30) | (op('GcnOpcodeSOP2', name) << 23) | (dst << 16) | (src1 << 8) | src0)


def sopc(name, src0, src1):
    return struct.pack('<I', (0x17e << 23) | (op('GcnOpcodeSOPC', name) << 16) | (src1 << 8) | src0)


def sopp(name, simm=0):
    return struct.pack('<I', (0x17f << 23) | (op('GcnOpcodeSOPP', name) << 16) | (simm & 0xffff))


def vopc(name, src0, vsrc1):
    return struct.pack('<I', (0x3e << 25) | (op('GcnOpcodeVOPC', name) << 17) | (vsrc1 << 9) | src0)


def vmov(dst, src0):
    return struct.pack('<I', (0x3f << 25) | (dst << 17) | (op('GcnOpcodeVOP1', 'V_MOV_B32') << 9) | src0)


def vop2(name, dst, src0, vsrc1):
    return struct.pack('<I', (op('GcnOpcodeVOP2', name) << 25) | (dst << 17) | (vsrc1 << 9) | src0)


def smrd_buffer_load(dst, base, dword):
    return struct.pack('<I', (0x18 << 27) | (op('GcnOpcodeSMRD', 'S_BUFFER_LOAD_DWORD') << 22) |
                       (dst << 15) | ((base // 2) << 9) | (1 << 8) | dword)


def export(target):
    return struct.pack('<II', (0x3e << 26) | (target << 4) | 0xf, 0)


def program(*parts):
    """Assemble parts; ('branch', name, label) and ('label', name) resolve forward displacements."""
    code, labels, fixups = b'', {}, []
    for part in parts:
        if isinstance(part, bytes):
            code += part
        elif part[0] == 'label':
            labels[part[1]] = len(code)
        else:
            fixups.append((len(code), part[1], part[2])); code += b'\0' * 4
    for at, name, label in fixups:
        code = code[:at] + sopp(name, (labels[label] - at - 4) // 4) + code[at + 4:]
    return code


def run(code, inputs, slots=(), register=3):
    compiler = Compiler(code, list(slots))
    for reg in inputs:
        if isinstance(reg, int):
            compiler.v[reg] = compiler.ir.node('input', name=f'v{reg}', kind='test')
    compiler.run(decoder().decode_stream(code, 0))
    names = {f'v{r}' if isinstance(r, int) else r: v for r, v in inputs.items()}
    return DAG(compiler.ir.nodes).evaluate(compiler.v[register], names), compiler


class ControlFlowTests(unittest.TestCase):
    def test_ps_input_registers_follow_input_address(self):
        from rc2016.shaders.translator import ps_input_registers
        # PERSP_CENTER (bit 1), POS_X..POS_W (bits 8-11), FRONT_FACE (bit 12); POS_W not loaded.
        addr = (1 << 1) | (0xf << 8) | (1 << 12)
        ena = addr & ~(1 << 11)
        compiler = Compiler(b'', [], addr, ena, 8)
        name = lambda reg: compiler.ir.nodes[compiler.v[reg]].get('name') or compiler.ir.nodes[compiler.v[reg]]['reason']
        self.assertEqual([name(r) for r in (0, 1, 2, 3, 4, 6)],
                         ['ps.PERSP_CENTER.0', 'ps.PERSP_CENTER.1', 'ps.POS_X', 'ps.POS_Y', 'ps.POS_Z', 'ps.FRONT_FACE'])
        self.assertIn('not loaded', name(5))
        # A layout larger than the program's VGPR allocation is not trusted.
        self.assertEqual(ps_input_registers(addr, ena, 4), {})

    def test_front_face_zero_test_binds_to_facing(self):
        from rc2016.shaders.bindings import bind_front_face
        from rc2016.viewer.native_glsl import emit_surface, evaluate_surface
        addr = (1 << 1) | (1 << 12)  # PERSP_CENTER, FRONT_FACE in VGPR 2
        # v3 = front ? v1 : v2 through an integer not-equal test (VOP3 names it LG).
        cmp = struct.pack('<Q', (0x34 << 26) | (op('GcnOpcodeVOP3', 'V_CMP_LG_U32_E64') << 17) | 106 |
                          (128 << 32) | ((V + 2) << 41))
        cnd = struct.pack('<I', (op('GcnOpcodeVOP2', 'V_CNDMASK_B32') << 25) | (3 << 17) | (11 << 9) | (V + 10))
        compiler = Compiler(cmp + cnd, [], addr, addr, 16)
        compiler.v[10] = compiler.ir.node('input', name='attribute0.1', kind='attribute', attribute=0, channel=1)
        compiler.v[11] = compiler.ir.node('input', name='attribute0.0', kind='attribute', attribute=0, channel=0)
        compiler.run(decoder().decode_stream(cmp + cnd, 0))
        ir = DAG(compiler.ir.nodes)
        roots, bound = bind_front_face(ir, [compiler.v[3]])
        self.assertTrue(bound)
        translation = dict(nodes=ir.nodes, texture_bindings=[], status='ready', translation_sha256='t',
                           outputs=dict(albedo=roots * 3, F0=roots * 3, gloss=roots[0], emission=roots[0]))
        self.assertIn('gl_FrontFacing', emit_surface(translation)['body'])
        front = evaluate_surface(translation, (.25, .75), None)
        back = evaluate_surface(translation, (.25, .75), None, preview_inputs={'front_facing': 0.})
        self.assertEqual((front['gloss'], back['gloss']), (.25, .75))

    def test_front_face_other_uses_stay_unbound(self):
        from rc2016.shaders.bindings import bind_front_face
        ir = DAG()
        face = ir.node('input', name='ps.FRONT_FACE', kind='ps_input', input='FRONT_FACE')
        roots = [ir.op('mul', face, ir.const(2.))]
        self.assertEqual(bind_front_face(ir, roots), (roots, False))

    def test_divergent_if(self):
        # if (v1 > v2) v3 = v1;  with v3 initially v2 -> max(v1, v2)
        code = program(vopc('V_CMP_GT_F32', V + 1, 2), sop1('S_AND_SAVEEXEC_B64', 0, 106),
                       ('branch', 'S_CBRANCH_EXECZ', 'end'), vmov(3, V + 1),
                       ('label', 'end'), sop2('S_OR_B64', 126, 126, 0))
        for a, b in [(1., 2.), (3., 2.)]:
            with self.subTest(a=a, b=b):
                compiler = Compiler(code, [])
                for reg in (1, 2):
                    compiler.v[reg] = compiler.ir.node('input', name=f'v{reg}', kind='test')
                compiler.v[3] = compiler.v[2]
                compiler.run(decoder().decode_stream(code, 0))
                self.assertEqual(DAG(compiler.ir.nodes).evaluate(compiler.v[3], {'v1': a, 'v2': b}), max(a, b))
                self.assertEqual(compiler.ir.value(compiler.exec), 1.)

    def test_divergent_if_else(self):
        # if (v1 < v2) v3 = v1; else v3 = v2;  -> min(v1, v2)
        code = program(vopc('V_CMP_LT_F32', V + 1, 2), sop1('S_AND_SAVEEXEC_B64', 0, 106),
                       sop2('S_XOR_B64', 0, 126, 0), vmov(3, V + 1),
                       sop1('S_OR_SAVEEXEC_B64', 0, 0), sop2('S_XOR_B64', 126, 126, 0),
                       ('branch', 'S_CBRANCH_EXECZ', 'end'), vmov(3, V + 2),
                       ('label', 'end'), sop2('S_OR_B64', 126, 126, 0))
        for a, b in [(1., 2.), (3., 2.)]:
            with self.subTest(a=a, b=b):
                value, compiler = run(code, {1: a, 2: b})
                self.assertEqual(value, min(a, b))
                self.assertEqual(compiler.ir.value(compiler.exec), 1.)

    def test_uniform_branch_on_material_constant(self):
        # if (CB2[0] == 0) v3 = v2; else v3 = v1;
        slots = [dict(kind=2, api=2, reg=0, flags=0)]
        code = program(smrd_buffer_load(4, 0, 0), sopc('S_CMP_EQ_U32', 4, 128),
                       ('branch', 'S_CBRANCH_SCC1', 'zero'), vmov(3, V + 1),
                       ('branch', 'S_BRANCH', 'end'), ('label', 'zero'), vmov(3, V + 2), ('label', 'end'))
        one_bits = struct.unpack('<f', struct.pack('<I', 1))[0]
        self.assertEqual(run(code, {1: 10., 2: 20., 'CB2[0]': 0.}, slots)[0], 20.)
        self.assertEqual(run(code, {1: 10., 2: 20., 'CB2[0]': one_bits}, slots)[0], 10.)

    def test_uniform_flag_bit_test(self):
        # if (CB2[0] & 0x10) v3 = v1; else v3 = v2;   (S_AND_B32 with a literal, then SCC0)
        slots = [dict(kind=2, api=2, reg=0, flags=0)]
        code = program(smrd_buffer_load(4, 0, 0), sop2('S_AND_B32', 5, 4, 255), struct.pack('<I', 0x10),
                       ('branch', 'S_CBRANCH_SCC0', 'clear'), vmov(3, V + 1),
                       ('branch', 'S_BRANCH', 'end'), ('label', 'clear'), vmov(3, V + 2), ('label', 'end'))
        word = lambda b: struct.unpack('<f', struct.pack('<I', b))[0]
        self.assertEqual(run(code, {1: 10., 2: 20., 'CB2[0]': word(0x13)}, slots)[0], 10.)
        self.assertEqual(run(code, {1: 10., 2: 20., 'CB2[0]': word(0x03)}, slots)[0], 20.)

    def test_uniform_ordered_compare(self):
        # if (CB2[0] < 3u) v3 = v1; else v3 = v2;
        slots = [dict(kind=2, api=2, reg=0, flags=0)]
        code = program(smrd_buffer_load(4, 0, 0), sopc('S_CMP_LT_U32', 4, 128 + 3),
                       ('branch', 'S_CBRANCH_SCC0', 'big'), vmov(3, V + 1),
                       ('branch', 'S_BRANCH', 'end'), ('label', 'big'), vmov(3, V + 2), ('label', 'end'))
        word = lambda b: struct.unpack('<f', struct.pack('<I', b))[0]
        self.assertEqual(run(code, {1: 10., 2: 20., 'CB2[0]': word(2)}, slots)[0], 10.)
        self.assertEqual(run(code, {1: 10., 2: 20., 'CB2[0]': word(3)}, slots)[0], 20.)
        self.assertEqual(run(code, {1: 10., 2: 20., 'CB2[0]': 1.0}, slots)[0], 20.)  # 0x3F800000 as an integer

    def test_uniform_vcc_branch(self):
        # v1 = 0; vcc = (CB2[0] == v1); if (vccz) v3 = v2;  -- VCC is the same in every lane.
        slots = [dict(kind=2, api=2, reg=0, flags=0)]
        code = program(smrd_buffer_load(4, 0, 0), vmov(1, 128), vopc('V_CMP_EQ_U32', 4, 1),
                       ('branch', 'S_CBRANCH_VCCZ', 'skip'), vmov(3, V + 2), ('label', 'skip'))
        compiler = Compiler(code, slots)
        compiler.v[2] = compiler.ir.node('input', name='v2', kind='test')
        compiler.v[3] = compiler.ir.node('input', name='v3', kind='test')
        compiler.run(decoder().decode_stream(code, 0))
        dag = DAG(compiler.ir.nodes)
        word = lambda b: struct.unpack('<f', struct.pack('<I', b))[0]
        self.assertEqual(dag.evaluate(compiler.v[3], {'v2': 2., 'v3': 3., 'CB2[0]': 0.}), 2.)
        self.assertEqual(dag.evaluate(compiler.v[3], {'v2': 2., 'v3': 3., 'CB2[0]': word(1)}), 3.)

    def test_vccz_skip_over_a_vcc_masked_region(self):
        # if (v1 > v2) v3 = v1; compiled as a VCCZ skip over an exec-masked region.
        code = program(vopc('V_CMP_GT_F32', V + 1, 2), ('branch', 'S_CBRANCH_VCCZ', 'skip'),
                       sop1('S_AND_SAVEEXEC_B64', 10, 106), vmov(3, V + 1), sop2('S_OR_B64', 126, 126, 10),
                       ('label', 'skip'))
        for a, b in [(1., 2.), (3., 2.)]:
            with self.subTest(a=a, b=b):
                compiler = Compiler(code, [])
                for reg in (1, 2):
                    compiler.v[reg] = compiler.ir.node('input', name=f'v{reg}', kind='test')
                compiler.v[3] = compiler.v[2]
                compiler.run(decoder().decode_stream(code, 0))
                self.assertEqual(DAG(compiler.ir.nodes).evaluate(compiler.v[3], {'v1': a, 'v2': b}), max(a, b))
                self.assertEqual(compiler.ir.value(compiler.exec), 1.)

    def test_first_lane_uniformity_check_takes_the_scalar_path(self):
        # s14 = readfirstlane(v3); vcc = (s14 != v3); if all equal goto fast; slow: v5 = v1; fast: v5 = v2
        first = struct.pack('<I', (0x3f << 25) | (14 << 17) | (op('GcnOpcodeVOP1', 'V_READFIRSTLANE_B32') << 9) | (V + 3))
        code = program(first, vopc('V_CMP_NE_U32', 14, 3), ('branch', 'S_CBRANCH_VCCZ', 'fast'),
                       vmov(5, V + 1), ('branch', 'S_BRANCH', 'end'), ('label', 'fast'), vmov(5, V + 2), ('label', 'end'))
        value, compiler = run(code, {1: 1., 2: 2., 3: 3.}, register=5)
        self.assertEqual(value, 2.)
        self.assertEqual(compiler.s[14], compiler.v[3])

    def test_lane_varying_vcc_branch_stays_unsupported(self):
        # Vector work before the mask: lanes without VCC would be affected.
        code = program(vopc('V_CMP_GT_F32', V + 1, 2), ('branch', 'S_CBRANCH_VCCZ', 'skip'), vmov(3, V + 1),
                       sop1('S_AND_SAVEEXEC_B64', 10, 106), ('label', 'skip'))
        with self.assertRaisesRegex(ValueError, 'lane-varying VCC'):
            run(code, {1: 1., 2: 2.})
        code = program(vopc('V_CMP_GT_F32', V + 1, 2), ('branch', 'S_CBRANCH_VCCZ', 'skip'), ('label', 'skip'))
        with self.assertRaisesRegex(ValueError, 'lane-varying VCC'):
            run(code, {1: 1., 2: 2.})

    def test_kill_mask_branch(self):
        # live = exec & ~vcc; if (!any(live)) goto dead; exec &= live; v3 = v1
        code = program(vopc('V_CMP_LT_F32', V + 1, 2), sop2('S_ANDN2_B64', 10, 126, 106),
                       ('branch', 'S_CBRANCH_SCC0', 'dead'), sop2('S_AND_B64', 126, 126, 10), vmov(3, V + 1),
                       ('label', 'dead'))
        value, compiler = run(code, {1: 5., 2: 1.})
        self.assertEqual(value, 5.)
        self.assertNotEqual(compiler.ir.value(compiler.exec), 1.)

    def test_unresolved_scc_names_its_source(self):
        code = program(sop2('S_ADD_U32', 4, 5, 6), ('branch', 'S_CBRANCH_SCC1', 'end'), ('label', 'end'))
        with self.assertRaisesRegex(ValueError, 'unresolved SCC from S_ADD_U32'):
            run(code, {})

    def test_runtime_conditions_trace_both_paths_for_diagnostics(self):
        # s4 is an unbound per-draw word: S_CMP_LG_U32 s4, 0 skips v3 = v2 when the flag is clear.
        code = program(vmov(3, V + 1), sopc('S_CMP_LG_U32', 4, 128), ('branch', 'S_CBRANCH_SCC0', 'end'),
                       vmov(3, V + 2), ('label', 'end'))
        with self.assertRaisesRegex(ValueError, 'S_CMP_LG_U32 of s4'):
            run(code, {1: 1., 2: 2.})
        compiler = Compiler(code, []); compiler.runtime_conditions = True
        for reg in (1, 2):
            compiler.v[reg] = compiler.ir.node('input', name=f'v{reg}', kind='test')
        compiler.run(decoder().decode_stream(code, 0))
        dag = DAG(compiler.ir.nodes)
        flag = next(n['name'] for n in compiler.ir.nodes if n.get('kind') == 'runtime_flag')
        self.assertEqual(dag.evaluate(compiler.v[3], {'v1': 1., 'v2': 2., flag: 1.}), 2.)
        self.assertEqual(dag.evaluate(compiler.v[3], {'v1': 1., 'v2': 2., flag: 0.}), 1.)

    def test_vcc_from_an_unresolved_scalar_is_wave_uniform(self):
        # V_CMP_NEQ_F32 vcc = s4 != 0 on an unbound scalar word: one outcome per wave.
        code = program(vmov(3, V + 1), vopc('V_CMP_NEQ_F32', 4, 5),
                       ('branch', 'S_CBRANCH_VCCZ', 'end'), vmov(3, V + 2), ('label', 'end'))
        compiler = Compiler(code, [])
        for reg in (1, 2):
            compiler.v[reg] = compiler.ir.node('input', name=f'v{reg}', kind='test')
        compiler.v[5] = compiler.ir.const(0.)
        compiler.s[4] = compiler.unknown('S_BUFFER_LOAD_DWORD from an unbound descriptor')
        compiler.run(decoder().decode_stream(code, 0))
        node = compiler.ir.nodes[compiler.v[3]]
        self.assertEqual(node['op'], 'select')

    def test_unresolved_multi_dword_load_keeps_words_distinct(self):
        # A load from a descriptor that was not bound: its two words are both unknown,
        # but they are different unknowns and must not cancel.
        load = struct.pack('<I', (0x18 << 27) | (op('GcnOpcodeSMRD', 'S_BUFFER_LOAD_DWORDX2') << 22) |
                           (20 << 15) | ((8 // 2) << 9) | (1 << 8) | 4)
        code = program(load, vop2('V_SUBREV_F32', 3, 20, V + 2), vop2('V_SUB_F32', 3, 21, V + 3))
        compiler = Compiler(code, [])
        compiler.v[2] = compiler.ir.node('input', name='v2', kind='test')
        compiler.run(decoder().decode_stream(code, 0))
        self.assertNotEqual(compiler.s[20], compiler.s[21])
        self.assertNotEqual(compiler.ir.nodes[compiler.v[3]]['op'], 'const')

    def test_xor_restores_exact_predicates(self):
        compiler = Compiler(b'', [])
        a = compiler.ir.node('input', name='a', kind='test'); b = compiler.ir.node('input', name='b', kind='test')
        self.assertEqual(compiler.p_xor(compiler.p_xor(a, b), b), a)
        self.assertEqual(compiler.ir.value(compiler.p_or(compiler.p_xor(a, b), compiler.p_not(compiler.p_xor(a, b)))), 1.)

    def test_discard_marks_exports_and_keeps_live_values(self):
        code = program(vopc('V_CMPX_GT_F32', V + 1, 2), ('branch', 'S_CBRANCH_EXECZ', 'dead'),
                       vmov(3, V + 1), ('label', 'dead'))
        value, compiler = run(code, {1: 5., 2: 1.})
        self.assertEqual(value, 5.)
        self.assertNotEqual(compiler.ir.value(compiler.exec), 1.)
        # A discarding writer is not offered as a translation yet.
        compiler.exports = {t: [compiler.v[3]] * 4 for t in (0, 1, 2)}
        compiler.exec_masked_export = True
        with self.assertRaisesRegex(ValueError, 'alpha test'):
            compiler.compile()

    def test_unresolved_scc_and_backward_branch_stay_unsupported(self):
        unresolved = program(sop2('S_ADD_U32', 4, 5, 6), ('branch', 'S_CBRANCH_SCC1', 'end'), ('label', 'end'))
        with self.assertRaisesRegex(ValueError, 'unresolved SCC'):
            run(unresolved, {})
        endless = program(('label', 'top'), vmov(3, V + 1), sopp('S_CBRANCH_EXECNZ', -2))
        with self.assertRaisesRegex(ValueError, 'not shown to end'):
            run(endless, {1: 1.})

    def test_unbounded_loop_names_a_forward_pass_program_by_its_exports(self):
        endless = lambda *exports: program(*(export(t) for t in exports), ('label', 'top'), vmov(3, V + 1),
                                           sopp('S_CBRANCH_EXECNZ', -2))
        # Back-edge unconditional under an exec-mask test, as in a forward light-list loop.
        with self.assertRaisesRegex(ValueError, r'not shown to end within 32 iterations \(forward-pass program: exports target 0, not'):
            run(endless(0), {1: 1.})
        with self.assertRaisesRegex(ValueError, r'forward-pass program: exports no target'):
            run(endless(), {1: 1.})
        # A three-target program keeps the plain loop diagnostic.
        with self.assertRaises(ValueError) as caught:
            run(endless(0, 1, 2), {1: 1.})
        self.assertTrue(str(caught.exception).endswith('not shown to end within 32 iterations'))

    def test_scalar_counter_loop_unrolls(self):
        # s4 = 0; do { v3 += v1; s4 += 1; } while (s4 < 4u);
        code = program(sop1('S_MOV_B32', 4, 128), ('label', 'top'), vop2('V_ADD_F32', 3, V + 3, 1),
                       sop2('S_ADD_U32', 4, 4, 129), sopc('S_CMP_LT_U32', 4, 132), ('branch', 'S_CBRANCH_SCC1', 'top'))
        value, compiler = run(code, {1: 2., 3: 1.})
        self.assertEqual(value, 9.)
        self.assertEqual(compiler.ir.value(compiler.bp), 1.)

    def test_lane_counter_loop_ends_through_exec(self):
        # saved = exec; v5 = 0; do { v3 += v1; v5 += 1; exec &= (4 > v5); } while (exec); exec = saved;
        code = program(sop1('S_MOV_B64', 10, 126), vmov(5, 128), ('label', 'top'),
                       vop2('V_ADD_F32', 3, V + 3, 1), vop2('V_ADD_I32', 5, 129, 5), vopc('V_CMP_GT_I32', 132, 5),
                       sop2('S_AND_B64', 126, 126, 106), ('branch', 'S_CBRANCH_EXECNZ', 'top'), sop1('S_MOV_B64', 126, 10))
        value, compiler = run(code, {1: 2., 3: 1.})
        self.assertEqual(value, 9.)
        self.assertEqual(compiler.ir.value(compiler.exec), 1.)

    def test_integer_b64_constant_keeps_scalar_bits(self):
        code = sop1('S_MOV_B64', 4, 128)
        compiler = Compiler(code, [])
        compiler.step(decoder().decode_stream(code, 0)[0])
        self.assertEqual(compiler.scalar_bits.get(4), 0)


if __name__ == '__main__':
    unittest.main()
