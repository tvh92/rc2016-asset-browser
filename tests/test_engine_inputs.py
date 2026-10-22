"""World-position reconstruction shapes are recognised up to arrangement and scale."""
import unittest

from rc2016.shaders import bindings
from rc2016.shaders.engine_inputs import analyse, reconstruct_view_offset, reconstruct_world_position
from rc2016.shaders.ir import DAG


def inputs(ir, *names):
    return [ir.node('input', name=n, kind='test') for n in names]


class ReconstructionTests(unittest.TestCase):
    def build(self, c, camera=True, scale=1.):
        ir = DAG()
        x, y, w = inputs(ir, 'ps.POS_X', 'ps.POS_Y', 'ps.POS_W')
        a, cc, b, d, cam = inputs(ir, *(f'CB0[{base + c}]' for base in (92, 96, 100, 104, 28)))
        # (A + B*y) + x*(C + D*y), arranged as the shader does, times depth.
        ray = ir.op('add', ir.op('add', a, ir.op('mul', b, y)), ir.op('mul', ir.op('add', cc, ir.op('mul', d, y)), x))
        value = ir.op('mul', ir.op('mul', ray, w), ir.const(scale))
        if camera:
            value = ir.op('add', value, ir.op('mul', cam, ir.const(scale)))
        return ir, ir.op('mul', value, ir.const(.5))

    def test_world_position_matches_and_clears_engine_inputs(self):
        ir, root = self.build(1)
        result = analyse(ir.nodes, [root])
        self.assertEqual(result['residual'], [])
        self.assertIn('camera+ray*w.y x0.5', result['matches'])

    def test_ray_without_camera_leaves_nothing_but_matches_ray_shape(self):
        ir, root = self.build(0, camera=False)
        result = analyse(ir.nodes, [root])
        self.assertEqual(result['residual'], [])
        self.assertIn('ray*w.x x0.5', result['matches'])

    def test_other_uses_remain(self):
        ir = DAG()
        x, w = inputs(ir, 'ps.POS_X', 'CB0[95]')
        result = analyse(ir.nodes, [ir.op('mul', x, w)])
        self.assertEqual(result['residual'], ['CB0[95]', 'ps.POS_X'])


    def test_adapter_replaces_world_position_with_viewer_position(self):
        ir, root = self.build(2, scale=.025)
        roots, count = reconstruct_world_position(ir, [ir.op('fract', root)])
        self.assertEqual(count, 1)
        used = [ir.nodes[i] for i in ir.reachable(roots) if ir.nodes[i]['op'] == 'input']
        self.assertEqual([(n['kind'], n['channel']) for n in used], [('preview_position', 2)])
        # 0.5 * 0.025 * world.z at world.z = 8
        self.assertAlmostEqual(ir.evaluate(roots[0], {'preview_position.2': 8.}), .1)

    def test_adapter_leaves_camera_relative_rays(self):
        ir, root = self.build(0, camera=False)
        self.assertEqual(reconstruct_world_position(ir, [root]), ([root], 0))

    def test_view_offset_replaces_the_camera_relative_ray(self):
        # The 76C79AA4 rim reads V = -normalize(ray * POS_W): world position minus camera, normalised.
        ir = DAG()
        parts = [self.build(c, camera=False) for c in range(3)]
        ir = parts[0][0]
        for c in (1, 2):
            other, root = parts[c]
            remap = {}
            for i, n in enumerate(other.nodes):
                remap[i] = ir.node(n['op'], [remap[j] for j in n['args']], **{k: v for k, v in n.items() if k not in ('op', 'args')})
            parts[c] = (ir, remap[root])
        v = [root for _, root in parts]
        length = ir.op('rsqrt', ir.op('add', ir.op('add', ir.op('mul', v[0], v[0]), ir.op('mul', v[1], v[1])), ir.op('mul', v[2], v[2])))
        roots, count = reconstruct_view_offset(ir, [ir.op('mul', ir.op('neg', length), v[c]) for c in range(3)])
        self.assertEqual(count, 3)
        used = sorted((ir.nodes[i]['kind'], ir.nodes[i]['channel']) for i in ir.reachable(roots) if ir.nodes[i]['op'] == 'input')
        self.assertEqual(used, [('preview_view_offset', c) for c in range(3)])
        offset = {'preview_view_offset.0': 3., 'preview_view_offset.1': 0., 'preview_view_offset.2': -4.}
        self.assertAlmostEqual(ir.evaluate(roots[0], offset), -.6)
        self.assertAlmostEqual(ir.evaluate(roots[2], offset), .8)

    def test_view_offset_does_not_split_world_position(self):
        ir, root = self.build(1)
        roots, _ = reconstruct_world_position(ir, [root])
        self.assertEqual(reconstruct_view_offset(ir, roots), (roots, 0))

    def test_experimental_receipt_reaches_glsl_only_as_experimental(self):
        from rc2016.viewer.native_glsl import emit_surface
        ir = DAG()
        p = ir.node('input', name='preview_position.0', kind='preview_position', channel=0)
        a = ir.node('input', name='preview_vertex_alpha_squared', kind='preview_vertex_alpha_squared')
        f = ir.op('mul', ir.op('fract', p), a)
        receipt = dict(status='experimental', preview_contract=bindings.WORLD_POSITION_CONTRACT, automatic_ready=False,
                       game_equivalence=False, code_sha256='c', graph_sha256='g', material_sha256='m',
                       nodes=ir.nodes, texture_bindings=[], translation_sha256='t',
                       outputs=dict(albedo=[f] * 3, F0=[f] * 3, gloss=f, emission=f))
        body = emit_surface(receipt, experimental=True)['body']
        self.assertIn('vTintWorldPos.x', body)
        self.assertIn('vRockAlpha', body)
        o = ir.node('input', name='preview_view_offset.1', kind='preview_view_offset', channel=1)
        offset = dict(receipt, nodes=ir.nodes, outputs=dict(receipt['outputs'], gloss=o))
        self.assertIn('vTintViewOffset.y', emit_surface(offset, experimental=True)['body'])
        with self.assertRaises(ValueError):
            emit_surface(receipt)
        with self.assertRaises(ValueError):
            emit_surface(dict(receipt, automatic_ready=True), experimental=True)

    def test_surface_signature_accepts_front_facing(self):
        ir = DAG()
        f = ir.node('input', name='front_facing', kind='front_facing')
        bound = dict(nodes=ir.nodes, texture_bindings=[], normal=None,
                     outputs=dict(albedo=[f] * 3, F0=[f] * 3, gloss=f, emission=f))
        self.assertEqual(len(bindings.surface_signature(bound)), 64)


if __name__ == '__main__':
    unittest.main()