"""Portable surface emission and independent native/hand-rule surface controls."""
import math
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from rc2016.shaders.ir import DAG
from rc2016.viewer.native_glsl import emit_surface, evaluate_surface, fragment_source, selected_translation
from rc2016.viewer import shading
from rc2016.formats.links import rule_coverage, NATIVE


def translation(ir, albedo, f0, gloss, emission, normal=None, bindings=()):
    return dict(status='ready', translation_sha256='control', game_equivalence=False,
                nodes=ir.nodes, outputs=dict(albedo=albedo, F0=f0, gloss=gloss, emission=emission),
                normal=normal, texture_bindings=list(bindings))


class PortableSurfaceTests(unittest.TestCase):
    def test_uv_offset_repeat_alpha_and_sample_identity(self):
        ir=DAG();u=ir.node('input',kind='attribute',attribute=0,channel=0)
        v=ir.node('input',kind='attribute',attribute=0,channel=1)
        v=ir.op('add',v,ir.const(.25));zero=ir.const(0)
        rgba=[ir.node('texture',[u,v],api=5,sample=1,channel=i) for i in range(4)]
        t=translation(ir,rgba[:3],[zero]*3,zero,rgba[3],bindings=[dict(api=5,path='a.texture')])
        emitted=emit_surface(t,{5:True})
        self.assertEqual(emitted['body'].count('texture2D('),1)
        self.assertNotIn('.rgb = linear(',emitted['body']) # hardware sRGB conversion precedes filtering
        self.assertTrue(emitted['samplers'][0]['srgb'])
        self.assertNotIn('.a = linear(',emitted['body'])
        coords=[]
        surface=evaluate_surface(t,(1.2,.4),lambda api,q: coords.append(q) or (.1,.2,.3,.7))
        self.assertEqual(coords,[(1.2,.65)])
        self.assertEqual(surface['emission'],.7)
        self.assertIsNone(surface['normal'])
        self.assertNotIn(3,[s['unit'] for s in emitted['samplers']])
        self.assertNotIn(6,[s['unit'] for s in emitted['samplers']])

    def test_normal_vector_and_comparison_arithmetic(self):
        ir=DAG();x,y,z=[ir.const(v) for v in (.6,0.,.64)]
        one=ir.op('ge',x,ir.const(.5));zero=ir.const(0.)
        value=ir.op('select',one,x,y)
        t=translation(ir,[value]*3,[zero]*3,zero,zero,dict(api=7,xyz=[x,y,z]))
        result=evaluate_surface(t,(0,0),lambda *_: None)
        length=math.sqrt(.6**2+.64**2)
        self.assertAlmostEqual(result['normal'][0],.6/length)
        self.assertEqual(result['albedo'],[.6]*3)
        self.assertIn('nativeTangentNormal',emit_surface(t)['body'])

    def test_zero_normal_and_zero_power_match_blender_math(self):
        ir=DAG();zero=ir.const(0);one=ir.node('pow',[zero,zero])
        t=translation(ir,[one]*3,[zero]*3,zero,zero,dict(api=7,xyz=[zero]*3))
        result=evaluate_surface(t,(0,0),lambda *_:None)
        self.assertEqual(result['normal'],[0.,0.,0.])
        self.assertEqual(result['albedo'],[1.,1.,1.])
        self.assertIn('nativeNormalize',emit_surface(t)['body'])

    def test_rejects_unbound_engine_input_and_bad_texture(self):
        ir=DAG();x=ir.node('input',kind='cb',api=0,dword=99);zero=ir.const(0)
        t=translation(ir,[x]*3,[zero]*3,zero,zero)
        with self.assertRaisesRegex(ValueError,'UV0'):emit_surface(t)
        ir.nodes[x]=dict(op='texture',args=[zero,zero],api=33,sample=0,channel=0)
        with self.assertRaisesRegex(ValueError,'Unbound'):emit_surface(t)

    def test_all_portable_operations_and_safe_zero_domains(self):
        ir=DAG();a=ir.node('input',kind='attribute',attribute=0,channel=0)
        b=ir.const(2.)
        unary=('neg','abs','sqrt','rsqrt','rcp','log2','exp2','sat','sin','cos')
        binary=('add','sub','mul','div','min','max','pow','lt','le','gt','ge','eq','ne')
        for op in (*unary,*binary,'select'):
            args=[a] if op in unary else [a,b] if op in binary else [ir.const(1),a,b]
            value=ir.node(op,args)
            t=translation(ir,[value]*3,[b]*3,b,b)
            emit_surface(t)
            self.assertTrue(math.isfinite(evaluate_surface(t,(.4,.2),lambda *_:None)['albedo'][0]))
        for op in ('div','rcp','rsqrt','sqrt','log2'):
            zero=ir.const(0);args=[a,zero] if op=='div' else [zero]
            value=ir.node(op,args);t=translation(ir,[value]*3,[b]*3,b,b)
            self.assertEqual(evaluate_surface(t,(.4,.2),lambda *_:None)['albedo'][0],0.)

    def test_program_cache_separates_uv_interpretation_and_vertex_program(self):
        ir=DAG();zero=ir.const(0);t=translation(ir,[zero]*3,[zero]*3,zero,zero)
        fake=object.__new__(shading.Shading);fake.programs={};calls=[]
        fake._link=lambda vertex,fragment,attributes,names: calls.append((vertex,fragment)) or (len(calls),{})
        a=fake.native_program(t,{},False)
        self.assertEqual(fake.native_program(t,{},False),a)
        self.assertNotEqual(fake.native_program(t,{},True),a)
        fake.native_program(t,{5:True},False)
        self.assertEqual(len(calls),3)
        self.assertIn('deferredDiffuse',calls[0][1])
        self.assertIn(shading.LIGHTING,calls[0][1])

    def test_fully_ported_hand_rule_keeps_tint(self):
        class Archive:
            entries={1:dict(type='material')}
            def resolve(self,path):return [1]
            def bytes(self,i):return [b'raw']
        graphs=[dict(path='materialgraph/environment/global/gbl_color_tint_01/mo_gbl_color_tint_01.materialgraph')]
        with patch('rc2016.formats.archive.decompress',return_value=[b'decoded']), \
             patch('rc2016.formats.index.references',return_value=graphs), \
             patch('rc2016.formats.materials.viewer_graph_rule',return_value=dict(applies=True,unported=[])), \
             patch('rc2016.viewer.native_glsl.prepare_material_translation') as prepare:
            self.assertIsNone(selected_translation(Archive(),'a.material',{'_c':'a.texture','tint':{}}))
            prepare.assert_not_called()

    def test_late_material_invalidates_every_recorded_program(self):
        from rc2016.levels.viewport import LevelViewport
        view=object.__new__(LevelViewport)
        view._materials={};view._texture_names={}
        view._material_ops={('a',2,1):'empty',('a',2,2):'empty',('a',1,1):'empty',('b',2,1):'keep'}
        view._make_current=lambda:True;view._gl_entry=lambda entry,cache:entry
        view._gl=type('GL',(),{'glBindTexture':lambda *args:None})()
        view._schedule_draw=lambda:None
        view.add_materials({'a':{'_c':(5,True)}})
        self.assertEqual(view._material_ops,{('b',2,1):'keep'})
        self.assertEqual(view._materials['a']['_c'],(5,True))
        self.assertIsNone(view._draw_list)

    def test_native_upload_reloads_pixels_released_after_a_hand_upload(self):
        from rc2016.viewer.textures import material_textures
        texture=dict(path='a.texture',pixels=b'pixels',array_size=1)
        cache={'a.texture':dict(path='a.texture',array_size=1)}
        ready=dict(status='ready',texture_bindings=[dict(api=5,path='a.texture')])
        with patch('rc2016.viewer.textures.material_texture_paths',return_value={}), \
             patch('rc2016.viewer.native_glsl.selected_translation',return_value=ready), \
             patch('rc2016.viewer.textures.load_texture',return_value=texture) as load:
            entry=material_textures(None,[dict(index=0,path='a.material')],cache)[0]
        load.assert_called_once_with(None,'a.texture')
        self.assertEqual(entry['native_textures'][5]['pixels'],b'pixels')
        self.assertIs(entry['native_translation'],ready)

    def test_coverage_and_recorder_are_material_and_program_specific(self):
        self.assertEqual(rule_coverage(['unknown.materialgraph'],dict(status='ready',translation_sha256='x')),(NATIVE,'x'))
        from rc2016.levels.viewport import LevelViewport
        view=object.__new__(LevelViewport)
        class Calls:
            def __init__(self):self.calls=[]
            def glUniform1f(self,*a):self.calls.append(a)
        view._gl=Calls();view._shading=Calls();view._material_ops={}
        view._uniform_state={};view._texture_state={};view._active_unit=None
        view._active_surface_program=1
        view._apply_material=lambda u,e,level:view._shading.glUniform1f(u['value'],e['value'])
        view._material_state({'value':4},('a',2),{'value':.1})
        view._active_surface_program=2;view._uniform_state={}
        view._material_state({'value':7},('a',2),{'value':.1})
        self.assertEqual(len(view._material_ops),2)
        self.assertEqual(view._shading.calls,[(4,.1),(7,.1)])


class NativeHandArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research=os.environ.get('RC2016_RESEARCH')
        if not research:raise unittest.SkipTest('RC2016_RESEARCH is required for source controls')
        source=Path(os.environ.get('RC2016_GAME_ROOT',str(Path(research).parents[1]/'Ratchet & Clank (EUR)'/'Image0')))
        if not source.exists():raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive=open_archive(source)

    @classmethod
    def tearDownClass(cls):cls.archive.close()

    def compare(self,path):
        from rc2016.viewer.native_glsl import prepare_material_translation
        from rc2016.viewer.textures import material_texture_paths
        t=prepare_material_translation(self.archive,path);self.assertEqual(t['status'],'ready')
        p=material_texture_paths(self.archive,path)
        sample=dict((b['api'],(.21,.36,.59,.73)) for b in t['texture_bindings'])
        if t.get('normal'):sample[t['normal']['api']]=(.8,.5,1.,1.)
        for uv in ((.13,.27),(.83,.62),(1.12,-.2)):
            native=evaluate_surface(t,uv,lambda api,q:sample[api])
            self.assertEqual(native['albedo'],[.21,.36,.59])
            self.assertAlmostEqual(native['gloss'],math.sqrt(min(1.,p['gloss']*.21)))
            self.assertEqual(native['F0'],[p['spec']*.36]*3)
            self.assertEqual(native['emission'],0.)
        return t,native

    def test_basic_normal_gloss_surface_agrees_normal_equation_differs(self):
        path='material/environment/global/gameplay/gbl_gp_train_car_crane_01/qrt_gp_factory_crane_01.material'
        t,native=self.compare(path)
        self.assertIsNotNone(t['normal'])
        self.assertAlmostEqual(native['normal'][0],.6/math.sqrt(.6**2+.64**2))
        self.assertGreater(abs(native['normal'][0]-.6),.05) # hand rule sqrt z gives x=.6
        self.assertIsNone(selected_translation(self.archive,path)) # exact hand rule retains precedence

    def test_untinted_mo_gbl_surface_agrees_missing_normal_stays_explicit(self):
        path='material/environment/nebula/architecture/neb_arch_metal_trim_01/mo_neb_arch_metal_trim_02.material'
        t,native=self.compare(path)
        self.assertIsNone(t['normal'])
        self.assertIsNone(native['normal'])
        self.assertFalse(t['game_equivalence'])
        self.assertIsNone(selected_translation(self.archive,path))


if __name__=='__main__':unittest.main()
