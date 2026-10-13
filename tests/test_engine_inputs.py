"""World-position reconstruction shapes are recognised up to arrangement and scale."""
import unittest

from rc2016.shaders import bindings
from rc2016.shaders.engine_inputs import analyse, reconstruct_world_position
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

    def test_experimental_receipt_reaches_glsl_only_as_experimental(self):
        from rc2016.viewer.native_glsl import emit_surface
        ir = DAG()
        p = ir.node('input', name='preview_position.0', kind='preview_position', channel=0)
        f = ir.op('fract', p)
        receipt = dict(status='experimental', preview_contract=bindings.WORLD_POSITION_CONTRACT, automatic_ready=False,
                       game_equivalence=False, code_sha256='c', graph_sha256='g', material_sha256='m',
                       nodes=ir.nodes, texture_bindings=[], translation_sha256='t',
                       outputs=dict(albedo=[f] * 3, F0=[f] * 3, gloss=f, emission=f))
        self.assertIn('vTintWorldPos.x', emit_surface(receipt, experimental=True)['body'])
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