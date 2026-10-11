"""Source singularities and boundaries of the finite portable power guard."""
import math
import unittest
from rc2016.shaders.ir import DAG
from rc2016.shaders.bindings import guard_source_zero_powers
from rc2016.viewer.native_glsl import evaluate_surface,emit_surface


class SourceZeroPowerTests(unittest.TestCase):
    def surface(self,ir,root):
        z=ir.const(0.)
        return dict(status='ready',translation_sha256='probe',nodes=ir.nodes,
            outputs=dict(albedo=[root]*3,F0=[z]*3,gloss=z,emission=z),normal=None,texture_bindings=[])

    def test_zero_and_positive_retain_source_chain(self):
        for exponent in (.01,.5,1.,2.,10.):
            ir=DAG();x=ir.node('input',kind='attribute',attribute=0,channel=0)
            base=ir.op('abs',x);root=ir.op('exp2',ir.op('mul',ir.op('log2',base),ir.const(exponent)))
            roots,guards=guard_source_zero_powers(ir,[root]);self.assertEqual(len(guards),1)
            t=self.surface(ir,roots[0]);self.assertIn('log2(',emit_surface(t)['body'])
            for value in (0.,-0.,1e-20,.125,1.,2.,-2.):
                expected=2**(math.log2(abs(value))*exponent) if value else 0.
                self.assertEqual(evaluate_surface(t,(value,0),None)['albedo'][0],expected)

    def test_bound_zero_constant_eliminates_singular_chain(self):
        ir=DAG();root=ir.op('exp2',ir.op('mul',ir.op('log2',ir.const(0.)),ir.const(.25)))
        roots,guards=guard_source_zero_powers(ir,[root]);self.assertEqual(ir.value(roots[0]),0.)
        self.assertEqual(len(guards),1)

    def test_unknown_domains_and_exponents_are_not_widened(self):
        for exponent in (0.,-1.,None):
            ir=DAG();x=ir.node('input',kind='attribute',attribute=0,channel=0)
            p=ir.const(exponent) if exponent is not None else x
            root=ir.node('exp2',[ir.node('mul',[ir.node('log2',[ir.op('abs',x)]),p])])
            roots,guards=guard_source_zero_powers(ir,[root]);self.assertFalse(guards)
        ir=DAG();x=ir.node('input',kind='attribute',attribute=0,channel=0)
        root=ir.op('exp2',ir.op('mul',ir.op('log2',x),ir.const(2.)))
        self.assertFalse(guard_source_zero_powers(ir,[root])[1])
        self.assertFalse(guard_source_zero_powers(ir,[ir.op('log2',ir.op('abs',x))])[1])


if __name__=='__main__':unittest.main()
