"""DXBC decoding and SM5 -> DAG translation, on programs compiled at test time by Windows' d3dcompiler_47 (skipped elsewhere)."""
import ctypes
import os
import unittest

from rc2016.shaders import dxbc, sm5

try:
    D3D = ctypes.WinDLL('d3dcompiler_47.dll') if os.name == 'nt' else None
except OSError:
    D3D = None


def _blob_bytes(blob):
    vt = ctypes.cast(ctypes.cast(blob, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    pointer = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(blob)
    size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(blob)
    data = ctypes.string_at(pointer, size)
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(blob)
    return data


def compile_hlsl(source, target='ps_5_0', entry='main'):
    code, errors = ctypes.c_void_p(), ctypes.c_void_p()
    text = source.encode()
    hr = D3D.D3DCompile(text, len(text), None, None, None, entry.encode(), target.encode(), 0, 0,
                        ctypes.byref(code), ctypes.byref(errors))
    if hr:
        raise AssertionError(_blob_bytes(errors).decode('latin-1') if errors.value else hex(hr & 0xFFFFFFFF))
    return _blob_bytes(code)


def disassemble(blob):
    out = ctypes.c_void_p()
    if D3D.D3DDisassemble(blob, len(blob), 0x80, None, ctypes.byref(out)):
        raise AssertionError('D3DDisassemble failed')
    return _blob_bytes(out).decode('latin-1').rstrip(chr(0))


def mnemonics(text):
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('//', 'dcl_', '{', '}', 'ps_', 'vs_', 'cs_')) or line[0] in '-0123456789':
            continue
        word, depth = '', 0
        for ch in line:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif depth == 0:
                if ch == ' ':
                    break
                word += ch
        names.append(word.replace('_indexable', '').replace('_sat', ''))
    return names


def run(source, inputs=None, constants=None, resource=None, bindings=None, target='ps_5_0', outputs=('o', 0)):
    shader = dxbc.shader(compile_hlsl(source, target))
    translation = sm5.translate(shader, bindings)
    keys = [('o', outputs[1], c) for c in range(4) if ('o', outputs[1], c) in translation.outputs]
    values = sm5.evaluate(translation.graph, [translation.outputs[k] for k in keys], inputs or {}, resource, constants)
    return translation, [values[translation.outputs[k]] for k in keys]


@unittest.skipUnless(D3D, 'd3dcompiler_47 is not available')
class DecoderTests(unittest.TestCase):
    SOURCES = [
        'float4 main(float4 p : SV_POSITION, float2 uv : TEXCOORD0) : SV_TARGET { return float4(uv * 2 - 1, p.z, 1); }',
        """static const float2 taps[4] = {float2(1, 2), float2(3, 4), float2(5, 6), float2(7, 8)};
           cbuffer C : register(b0) { int n; float k; };
           Texture2D t : register(t0); SamplerState s : register(s0);
           float4 main(float2 uv : TEXCOORD0) : SV_TARGET {
               float4 sum = 0; [loop] for (int i = 0; i < n; i++) sum += t.Sample(s, uv + taps[i & 3]) * k;
               if (sum.x < 0) discard; return sum; }""",
        """RWTexture2D<float4> o : register(u0); Texture2D<uint4> g : register(t1);
           [numthreads(8, 8, 1)] void main(uint3 id : SV_DispatchThreadID) {
               uint4 v = g.Load(int3(id.xy, 0)); o[id.xy] = float4(f16tof32(v.x), (v.y >> 8) / 255.0, countbits(v.z), 1); }""",
    ]

    def test_mnemonics_match_the_disassembler(self):
        for i, source in enumerate(self.SOURCES):
            target = 'cs_5_0' if 'numthreads' in source else 'ps_5_0'
            blob = compile_hlsl(source, target)
            shader = dxbc.shader(blob)
            ours = [str(ins).split()[0].replace('_sat', '') for ins in shader.program.instructions]
            theirs = [m.replace('resinfo_uint', 'resinfo') for m in mnemonics(disassemble(blob))]
            self.assertEqual(ours, theirs, 'program %d' % i)

    def test_reflection_and_signatures(self):
        shader = dxbc.shader(compile_hlsl(self.SOURCES[1]))
        self.assertEqual(shader.stage, 'ps')
        self.assertEqual([c.name for c in shader.reflection.constant_buffers], ['C'])
        self.assertEqual([(v.name, v.offset) for v in shader.reflection.constant_buffers[0].variables], [('n', 0), ('k', 4)])
        self.assertEqual(shader.reflection.binding('t', 0).name, 't')
        self.assertEqual([e.semantic for e in shader.inputs], ['TEXCOORD'])
        self.assertEqual([e.semantic for e in shader.outputs], ['SV_TARGET'])
        self.assertEqual(len(shader.program.immediate_constants), 16)

    def test_dxil_is_identified(self):
        with self.assertRaises(dxbc.DxbcError):
            dxbc.chunks(b'nope' + bytes(40))


@unittest.skipUnless(D3D, 'd3dcompiler_47 is not available')
class TranslatorTests(unittest.TestCase):
    def test_arithmetic_and_swizzles(self):
        _tr, out = run('float4 main(float4 a : TEXCOORD0) : SV_TARGET { return float4(a.yx * 3 + a.w, rsqrt(a.z), saturate(a.x - 5)); }',
                       {'TEXCOORD.x': 2.0, 'TEXCOORD.y': 1.0, 'TEXCOORD.z': 4.0, 'TEXCOORD.w': 0.5})
        self.assertEqual(out, [3.5, 6.5, 0.5, 0.0])

    def test_branches_become_selects(self):
        source = """float4 main(float4 a : TEXCOORD0) : SV_TARGET {
            float4 r = a; [branch] if (a.x > 0) { r.y = a.z * 2; } else { r.y = -a.z; r.w = 7; } return r; }"""
        tr, plus = run(source, {'TEXCOORD.x': 1.0, 'TEXCOORD.y': 0.0, 'TEXCOORD.z': 3.0, 'TEXCOORD.w': 1.0})
        _tr, minus = run(source, {'TEXCOORD.x': -1.0, 'TEXCOORD.y': 0.0, 'TEXCOORD.z': 3.0, 'TEXCOORD.w': 1.0})
        self.assertEqual(tr.status(), 'ready')
        self.assertEqual(plus, [1.0, 6.0, 3.0, 1.0])
        self.assertEqual(minus, [-1.0, -3.0, 3.0, 7.0])

    def test_fixed_loop_unrolls_inside_a_branch(self):
        source = """float4 main(float4 a : TEXCOORD0) : SV_TARGET {
            float s = 0; [branch] if (a.x > 0) { [loop] for (int i = 0; i < 6; i++) { s += a.y * i; } } return s; }"""
        tr, out = run(source, {'TEXCOORD.x': 1.0, 'TEXCOORD.y': 2.0})
        self.assertEqual(tr.status(), 'ready')
        self.assertEqual(out[0], 30.0)
        self.assertTrue(any(n == 7 for _p, n in tr.loops))

    def test_data_dependent_loop_is_unknown_until_its_count_is_bound(self):
        source = """cbuffer C : register(b0) { uint count; float pad; float base; };
            float4 main(float4 a : TEXCOORD0) : SV_TARGET {
                float s = base; [loop] for (uint i = 0; i < count; i++) { s += a.y * a.x + i; } return float4(s, a.z, 0, 1); }"""
        shader = dxbc.shader(compile_hlsl(source))
        loose = sm5.translate(shader)
        self.assertEqual(loose.status(), 'partial')
        self.assertTrue(loose.graph.unknowns([loose.output(0, 0)])[0].startswith('loop at'))
        self.assertEqual(loose.graph.unknowns([loose.output(0, 1)]), [])   # outputs that do not depend on it still translate

        class Empty(sm5.Bindings):
            def constant(self, name):
                return graph.const('i', 0) if name == 'C.count' else None
        bind = Empty()
        translator = sm5.Translator(shader, bind)
        graph = translator.g
        bound = translator.run()
        self.assertEqual(bound.status(), 'ready')
        value = sm5.evaluate(bound.graph, [bound.output(0, 0)], {'C.base': 4.0})[bound.output(0, 0)]
        self.assertEqual(value, 4.0)

    def test_switch_and_integer_packing(self):
        source = """cbuffer C : register(b0) { uint mode; uint packed; };
            float4 main() : SV_TARGET {
                float r; [forcecase] switch (mode) { case 0: r = 1; break; case 1: case 2: r = f16tof32(packed); break;
                default: r = (packed >> 16) & 0xff; break; }
                return float4(r, countbits(packed), firstbithigh(packed), reversebits(1u) == 0x80000000u); }"""
        packed = 0x00AB3C00   # f16 1.0 in the low half, 0xAB in byte 2
        for mode, first in ((0, 1.0), (1, 1.0), (2, 1.0), (7, 171.0)):
            tr, out = run(source, {'C.mode': sm5.bits_float(mode), 'C.packed': sm5.bits_float(packed)})
            self.assertEqual(out[0], first, mode)
            self.assertEqual(out[1:3], [9.0, 23.0])
            # The compiler writes the bool as and(mask, 1.0f): an integer node holding the bits of 1.0.
            self.assertEqual(sm5.bits_float(out[3]) if tr.graph.type(tr.output(0, 3)) == 'i' else out[3], 1.0)

    def test_discard_predicate(self):
        shader = dxbc.shader(compile_hlsl(
            'float4 main(float4 a : TEXCOORD0) : SV_TARGET { clip(a.x - 0.5); return a; }'))
        tr = sm5.translate(shader)
        g = tr.graph
        for x, expected in ((0.2, True), (0.8, False)):
            self.assertEqual(sm5.evaluate(g, [tr.discard], {'TEXCOORD.x': x})[tr.discard], expected)

    def test_samples_and_named_constants(self):
        source = """struct S { float3 tint; float gloss; float4x4 m; };
            cbuffer Mat : register(b2) { S mat; float4 extra[2]; };
            Texture2DArray t : register(t5); SamplerState s : register(s5);
            float4 main(float2 uv : TEXCOORD0) : SV_TARGET {
                float4 c = t.Sample(s, float3(uv, 0)); return float4(c.rgb * mat.tint, c.a * mat.gloss + extra[1].y); }"""
        tr, out = run(source, {'TEXCOORD.x': 0.25, 'TEXCOORD.y': 0.5, 'Mat.mat.tint': 2.0, 'Mat.mat.tint+4': 3.0,
                               'Mat.mat.tint+8': 4.0, 'Mat.mat.gloss': 0.5, 'Mat.extra+20': 0.125},
                      resource=lambda node, args: [0.5, 0.25, 0.125, 1.0])
        self.assertEqual(out, [1.0, 0.75, 0.5, 0.625])
        g = tr.graph
        samples = [n for n in g.nodes if n['op'] == 'sample']
        self.assertEqual([(n['binding'], n['sampler']) for n in samples], [('t', 's')])
        self.assertIn('Mat.mat.gloss', g.leaves(list(tr.outputs.values()), 'input'))

    def test_compute_stores_are_captured(self):
        source = """RWTexture2D<float4> o : register(u0); Texture2D<uint4> g : register(t1);
            [numthreads(8, 8, 1)] void main(uint3 id : SV_DispatchThreadID) {
                uint4 v = g.Load(int3(id.xy, 0)); [branch] if (v.w != 0) o[id.xy] = float4((v.y >> 8) / 255.0, 0, 0, 1); }"""
        shader = dxbc.shader(compile_hlsl(source, 'cs_5_0'))
        tr = sm5.translate(shader)
        self.assertEqual(len(tr.stores), 1)
        store = tr.stores[0]
        self.assertEqual(store['binding'], 'o')
        texel = {'w': 1}

        def load(node, args):
            return [0, 0x3300, 0, texel['w']]
        g = tr.graph
        got = sm5.evaluate(g, [store['values'][0], store['active']], {'vThreadID.x': 3, 'vThreadID.y': 4}, load)
        self.assertAlmostEqual(got[store['values'][0]], 0x33 / 255.0, places=6)
        self.assertTrue(got[store['active']])
        texel['w'] = 0
        self.assertFalse(sm5.evaluate(g, [store['active']], {'vThreadID.x': 3, 'vThreadID.y': 4}, load)[store['active']])


class FoldTests(unittest.TestCase):
    """Graph folds that need no compiler."""

    def test_comparison_of_a_never_negative_value_with_zero(self):
        g = sm5.Graph()
        x = g.input('TEXCOORD.x')
        never = g.op('min', g.op('sat', x), g.op('add', g.op('mul', x, g.const('f', 0.0)), g.const('f', 1.0)))
        self.assertIs(g.value(g.op('lt', never, g.const('f', 0.0))), False)
        self.assertIsNone(g.value(g.op('lt', g.op('add', x, g.const('f', 1.0)), g.const('f', 0.0))))
        self.assertIsNone(g.value(g.op('lt', g.op('sat', x), g.const('f', 0.5))))

    def test_shifted_zero(self):
        g = sm5.Graph()
        n = g.cast(g.input('TEXCOORD.x'), 'i')
        for op in ('ushr', 'ishr', 'ishl'):
            self.assertEqual(g.value(g.op(op, g.const('i', 0), n)), 0)


if __name__ == '__main__':
    unittest.main()
