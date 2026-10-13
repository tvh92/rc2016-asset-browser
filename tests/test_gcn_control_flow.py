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


def smrd_buffer_load(dst, base, dword):
    return struct.pack('<I', (0x18 << 27) | (op('GcnOpcodeSMRD', 'S_BUFFER_LOAD_DWORD') << 22) |
                       (dst << 15) | ((base // 2) << 9) | (1 << 8) | dword)


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
    instructions = decoder().decode_stream(code, 0)
    for i in instructions:
        if i['relative'] in compiler.pending:
            compiler.bp = compiler.p_or(compiler.bp, compiler.pending.pop(i['relative']))
        if compiler.ir.value(compiler.bp) == 0:
            continue
        compiler.step(i)
    names = {f'v{r}' if isinstance(r, int) else r: v for r, v in inputs.items()}
    return DAG(compiler.ir.nodes).evaluate(compiler.v[register], names), compiler


class ControlFlowTests(unittest.TestCase):
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
                for i in decoder().decode_stream(code, 0):
                    if i['relative'] in compiler.pending:
                        compiler.bp = compiler.p_or(compiler.bp, compiler.pending.pop(i['relative']))
                    compiler.step(i)
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

    def test_unresolved_scc_names_its_source(self):
        code = program(sop2('S_ADD_U32', 4, 5, 6), ('branch', 'S_CBRANCH_SCC1', 'end'), ('label', 'end'))
        with self.assertRaisesRegex(ValueError, 'unresolved SCC from S_ADD_U32'):
            run(code, {})

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
        backward = program(('label', 'top'), vmov(3, V + 1), sopp('S_CBRANCH_EXECNZ', -2))
        with self.assertRaisesRegex(ValueError, 'Backward branch'):
            run(backward, {1: 1.})

    def test_integer_b64_constant_keeps_scalar_bits(self):
        code = sop1('S_MOV_B64', 4, 128)
        compiler = Compiler(code, [])
        compiler.step(decoder().decode_stream(code, 0)[0])
        self.assertEqual(compiler.scalar_bits.get(4), 0)


if __name__ == '__main__':
    unittest.main()
