"""Finite-domain float opcodes: legacy/clamp aliases, min3/max3/med3 and rounding."""
import math
import struct
import unittest

from rc2016.shaders import gcn
from rc2016.shaders.ir import DAG
from rc2016.shaders.translator import Compiler, decoder


def op(table, name):
    return gcn.enum_value(table, 'GCN_' + name)


def vop1(name, dst, src0):
    # VOP1: 0b0111111 in 31:25, VDST 24:17, OP 16:9, SRC0 8:0.
    return struct.pack('<I', (0x3f << 25) | (dst << 17) | (op('GcnOpcodeVOP1', name) << 9) | src0)


def vop2(name, dst, src0, vsrc1):
    # VOP2: 0 in bit 31, OP 30:25, VDST 24:17, VSRC1 16:9, SRC0 8:0.
    return struct.pack('<I', (op('GcnOpcodeVOP2', name) << 25) | (dst << 17) | (vsrc1 << 9) | src0)


def vop3(name, dst, *src):
    # VOP3A: 0b110100 in 31:26, OP 25:17, VDST 7:0; SRC0..2 at 40, 49, 58.
    src = list(src) + [0] * (3 - len(src))
    word = (0x34 << 26) | (op('GcnOpcodeVOP3', name + '_E64') << 17) | dst
    return struct.pack('<Q', word | (src[0] << 32) | (src[1] << 41) | (src[2] << 50))


def run(code, inputs):
    """Compile hand-encoded VALU words with VGPR inputs; return the result VGPR value at register 9."""
    compiler = Compiler(code, [])
    for reg, value in inputs.items():
        compiler.v[reg] = compiler.ir.node('input', name=f'v{reg}', kind='test')
    for instruction in decoder().decode_stream(code, 0):
        compiler.instruction(instruction)
    node = compiler.v[9]
    return DAG(compiler.ir.nodes).evaluate(node, {f'v{r}': v for r, v in inputs.items()})


V = 256  # VGPR operand base


class FloatOpcodeTests(unittest.TestCase):
    def test_decoder_names(self):
        for code, name in [(vop1('V_FRACT_F32', 9, V + 1), 'GCN_V_FRACT_F32'),
                           (vop2('V_MUL_LEGACY_F32', 9, V + 1, 2), 'GCN_V_MUL_LEGACY_F32'),
                           (vop3('V_MED3_F32', 9, V + 1, V + 2, V + 3), 'GCN_V_MED3_F32_E64')]:
            self.assertEqual(decoder().decode_stream(code, 0)[0]['name'], name)

    def test_rounding(self):
        for x in (-2.5, -1.0, -0.25, 0.0, 0.75, 3.0, 7.5):
            with self.subTest(x=x):
                self.assertEqual(run(vop1('V_FLOOR_F32', 9, V + 1), {1: x}), math.floor(x))
                self.assertEqual(run(vop1('V_CEIL_F32', 9, V + 1), {1: x}), math.ceil(x))
                self.assertEqual(run(vop1('V_TRUNC_F32', 9, V + 1), {1: x}), math.trunc(x))
                self.assertAlmostEqual(run(vop1('V_FRACT_F32', 9, V + 1), {1: x}), x - math.floor(x))

    def test_three_operand_min_max_median(self):
        for a, b, c in [(1., 2., 3.), (3., 1., 2.), (2., 3., 1.), (-1., -1., 5.), (0.5, 0.25, 0.5)]:
            with self.subTest(values=(a, b, c)):
                inputs = {1: a, 2: b, 3: c}
                self.assertEqual(run(vop3('V_MIN3_F32', 9, V + 1, V + 2, V + 3), inputs), min(a, b, c))
                self.assertEqual(run(vop3('V_MAX3_F32', 9, V + 1, V + 2, V + 3), inputs), max(a, b, c))
                self.assertEqual(run(vop3('V_MED3_F32', 9, V + 1, V + 2, V + 3), inputs), sorted((a, b, c))[1])

    def test_finite_aliases(self):
        self.assertEqual(run(vop2('V_MUL_LEGACY_F32', 9, V + 1, 2), {1: 3., 2: -0.5}), -1.5)
        self.assertEqual(run(vop2('V_MIN_LEGACY_F32', 9, V + 1, 2), {1: 3., 2: -0.5}), -0.5)
        self.assertEqual(run(vop2('V_MAX_LEGACY_F32', 9, V + 1, 2), {1: 3., 2: -0.5}), 3.)
        # MAC accumulates into its destination.
        self.assertEqual(run(vop2('V_MAC_LEGACY_F32', 9, V + 1, 2), {1: 3., 2: 2., 9: 1.}), 7.)
        self.assertEqual(run(vop3('V_MAD_LEGACY_F32', 9, V + 1, V + 2, V + 3), {1: 3., 2: 2., 3: 1.}), 7.)
        self.assertEqual(run(vop1('V_RCP_CLAMP_F32', 9, V + 1), {1: 4.}), .25)
        self.assertEqual(run(vop1('V_RSQ_CLAMP_F32', 9, V + 1), {1: 4.}), .5)
        self.assertEqual(run(vop1('V_LOG_CLAMP_F32', 9, V + 1), {1: 8.}), 3.)

    def test_normalised_packs(self):
        for name, low, high in [('V_CVT_PKNORM_U16_F32', 1., 0.), ('V_CVT_PKNORM_I16_F32', 1., -1.)]:
            with self.subTest(name=name):
                code = vop2(name, 9, V + 1, 2)
                compiler = Compiler(code, [])
                compiler.v[1] = compiler.ir.const(1.5); compiler.v[2] = compiler.ir.const(-2.)
                compiler.instruction(decoder().decode_stream(code, 0)[0])
                node = compiler.ir.nodes[compiler.v[9]]
                self.assertEqual(node['op'], 'pack')
                self.assertEqual([compiler.ir.value(a) for a in node['args']], [low, high])

    def test_constant_packed_export(self):
        # V_MOV_B32 v4, 0x3C003800 then a compressed export: halves (0.5, 1.0).
        compiler = Compiler(b'', [])
        compiler.v[4] = compiler.ir.const(struct.unpack('<f', struct.pack('<I', 0x3C003800))[0])
        self.assertEqual([compiler.ir.value(x) for x in compiler.unpack(compiler.v[4])], [.5, 1.])

    def test_unmodelled_legacy_stays_unsupported(self):
        # RCP_LEGACY's special cases are not modelled; it must stay a diagnostic.
        compiler = Compiler(vop1('V_RCP_LEGACY_F32', 9, V + 1), [])
        compiler.v[1] = compiler.ir.const(4.)
        compiler.instruction(decoder().decode_stream(vop1('V_RCP_LEGACY_F32', 9, V + 1), 0)[0])
        self.assertEqual(compiler.ir.nodes[compiler.v[9]]['op'], 'unsupported')

    def test_new_ops_reach_glsl(self):
        from rc2016.viewer.native_glsl import emit_surface
        nodes = DAG()
        uv = nodes.node('input', name='attribute0.0', kind='attribute', attribute=0, channel=0)
        floor, fract = nodes.op('floor', uv), nodes.op('fract', uv)
        translation = dict(nodes=nodes.nodes, texture_bindings=[], status='ready', translation_sha256='t',
                           outputs=dict(albedo=[floor, fract, floor], F0=[fract] * 3, gloss=fract, emission=floor))
        body = emit_surface(translation)['body']
        self.assertIn('floor(native0)', body)
        self.assertIn('fract(native0)', body)


if __name__ == '__main__':
    unittest.main()
