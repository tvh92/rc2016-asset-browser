"""Explicit neutral-mask review inputs never make native materials ready."""
import unittest
from unittest.mock import patch
from rc2016.shaders import bindings
from rc2016.shaders.ir import DAG
from rc2016.viewer.native_glsl import evaluate_surface

def fixture(*,experimental=True,channel=0,key=bindings.BLEND_MASK_SLOT[0],type_hash=bindings.BLEND_MASK_SLOT[1],override=True,path=None):
    ir=DAG();u=ir.node('input',name='attribute0.0',kind='attribute',attribute=0,channel=0);v=ir.node('input',name='attribute0.1',kind='attribute',attribute=0,channel=1)
    sample=ir.node('texture',[u,v],api=5,sample=0,channel=channel)
    paint=ir.node('input',name='attribute3.3',kind='attribute',attribute=3,channel=3)
    root=ir.op('mul',sample,paint);zero=ir.const(0.)
    program={'_lowered':dict(nodes=ir.nodes,outputs=dict(albedo=[root]*3,F0=[zero]*3,gloss=zero,emission=zero),normal=None,limitations=[])}
    graphrow=dict(slot_key=key,type_hash=type_hash,flag=1,path=path)
    overrides=[dict(graphrow)] if override else []
    with patch('rc2016.formats.materials.texture_slot_records',return_value=overrides),patch.object(bindings,'_parameter_values',return_value=({},[])):
        return bindings._bind(program,dict(resources=[graphrow],index=0),b'material',{},experimental,(),{(3,3):(5,3)})

class NeutralMaskTests(unittest.TestCase):
    def test_explicit_unset_mask_r_is_one_only_in_experimental_paint_binding(self):
        receipt=fixture()
        self.assertEqual(receipt['neutral_blend_mask_inputs'],[dict(api=5,sample=0,channel=0,slot_key=bindings.BLEND_MASK_SLOT[0],type_hash=bindings.BLEND_MASK_SLOT[1],review_value=1.)])
        self.assertEqual(receipt['texture_bindings'],[])
        self.assertIn('game empty-slot behavior is unverified',' '.join(receipt['limitations']))
        receipt['status']='experimental'
        for alpha in (0.,.25,1.):
            actual=evaluate_surface(receipt,(.4,.6),lambda *_:self.fail('Unset mask must not load a texture'),preview_inputs={'preview_vertex_alpha_squared':alpha*alpha})
            for value in actual['albedo']:self.assertAlmostEqual(value,alpha*alpha)
    def test_native_binding_retains_unset_resource_failure(self):
        with self.assertRaisesRegex(ValueError,'unset or unbound'):fixture(experimental=False)
    def test_other_mask_channels_slots_types_and_graph_default_are_not_substituted(self):
        for kwargs in (dict(channel=1),dict(channel=3),dict(key=99),dict(type_hash=99),dict(override=False)):
            with self.subTest(kwargs=kwargs),self.assertRaisesRegex(ValueError,'unset or unbound'):fixture(**kwargs)
    def test_bound_mask_keeps_exact_source_texture(self):
        receipt=fixture(path='source-mask.texture')
        self.assertNotIn('neutral_blend_mask_inputs',receipt)
        self.assertEqual(receipt['texture_bindings'][0]['path'],'source-mask.texture')
