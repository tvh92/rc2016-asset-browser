"""World-position reconstruction shapes are recognised up to arrangement and scale."""
import unittest

from rc2016.shaders.engine_inputs import analyse
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


if __name__ == '__main__':
    unittest.main()
