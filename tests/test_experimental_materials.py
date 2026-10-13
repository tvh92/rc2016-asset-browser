"""Experimental receipts and master gates must not become ordinary readiness."""
import os
from pathlib import Path
import unittest

from rc2016.shaders.ir import DAG
from rc2016.viewer.experimental_materials import CONTRACT, CODE_SHA, GRAPH_SHA, MATERIAL_SHA, build_preview
from rc2016.viewer.native_glsl import emit_surface, evaluate_surface
from rc2016.viewer.viewport import ModelViewport
from rc2016.levels.viewport import LevelViewport
from rc2016.levels.panel import LevelPanel


def tiny_preview():
    ir = DAG();x = ir.node('input',kind='preview_position',channel=0,name='preview_position.0');zero = ir.const(0)
    return dict(status='experimental',preview_contract=CONTRACT,code_sha256=CODE_SHA,graph_sha256=GRAPH_SHA,
                material_sha256=MATERIAL_SHA,translation_sha256='fixture',nodes=ir.nodes,
                outputs=dict(albedo=[x]*3,F0=[zero]*3,gloss=zero,emission=zero),normal=None,texture_bindings=[])


class PreviewGateTests(unittest.TestCase):
    def test_experimental_input_requires_explicit_frontend_opt_in(self):
        receipt = tiny_preview()
        with self.assertRaisesRegex(ValueError,'ready'): emit_surface(receipt)
        self.assertIn('vTintWorldPos.x',emit_surface(receipt,experimental=True)['body'])
        receipt['status'] = 'ready'
        with self.assertRaisesRegex(ValueError,'UV0'): emit_surface(receipt)

    def test_unknown_preview_contract_stays_rejected(self):
        receipt = tiny_preview();receipt['graph_sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError,'contract'): emit_surface(receipt,experimental=True)

    def test_surface_selection_is_reversible_and_independent_of_native_toggle(self):
        view = object.__new__(ModelViewport);view.native_translation=True;view.experimental_materials=False
        entry = dict(native_translation={'status':'ready'},native_textures={5:'normal'},
                     experimental_preview=tiny_preview(),experimental_textures={6:'preview'})
        self.assertEqual(view._selected_surface(entry,2)[0],entry['native_translation'])
        view.experimental_materials=True;view.native_translation=False
        self.assertIs(view._selected_surface(entry,2)[0],entry['experimental_preview'])
        self.assertIsNone(view._selected_surface(entry,1)[0])
        view.experimental_materials=False
        self.assertIsNone(view._selected_surface(entry,2)[0])

    def test_lighting_master_gates_all_scene_effects_preserves_children(self):
        view = object.__new__(LevelViewport)
        view._scene_effects=object();view._mode='Shaded';view.scene_ao=True;view.scene_shadows=False;view.scene_authored=True
        view._schedule_draw=lambda:None;view._material_ops={1:'old'}
        view.set_lighting_enabled(False)
        self.assertFalse(view.scene_effects_active())
        self.assertEqual((view.scene_ao,view.scene_shadows,view.scene_authored),(True,False,True))
        self.assertEqual(view._material_ops,{})
        view.set_lighting_enabled(True)
        self.assertTrue(view.scene_effects_active())

    def test_experimental_master_keeps_spawn_and_material_choices(self):
        class Var:
            def __init__(self,value):self.value=value
            def get(self):return self.value
        calls=[]
        panel=object.__new__(LevelPanel)
        panel.experimental_var=Var(False);panel.material_previews_var=Var(True);panel.spawn_var=Var(True)
        panel.view=type('View',(),{'set_experimental_materials':lambda _,v:calls.append(('material',v)),
                                   'set_spawn_preview':lambda _,v:calls.append(('spawns',v))})()
        panel._experimental_changed();self.assertEqual(calls,[('material',False),('spawns',False)])
        panel.experimental_var.value=True;panel._experimental_changed()
        self.assertEqual(calls[-2:],[('material',True),('spawns',True)])
        self.assertTrue(panel.material_previews_var.get());self.assertTrue(panel.spawn_var.get())



class OnDemandPreviewTests(unittest.TestCase):
    """Preview source analysis is slow: loads skip it until the previews are enabled."""

    def test_material_textures_skip_preview_analysis_unless_asked(self):
        from unittest.mock import patch
        from rc2016.viewer.textures import material_textures
        from rc2016.viewer.experimental_materials import MATERIAL
        with patch('rc2016.viewer.textures.material_texture_paths',return_value={}), \
             patch('rc2016.viewer.native_glsl.selected_translation',return_value=None), \
             patch('rc2016.viewer.experimental_materials.prepare_preview') as prepare:
            entry=material_textures(None,[dict(index=0,path=MATERIAL)],{},previews=False)[0]
            prepare.assert_not_called()
            self.assertNotIn('experimental_preview',entry)
            material_textures(None,[dict(index=0,path=MATERIAL)],{})
            prepare.assert_called_once_with(None,MATERIAL)

    def test_panel_prepares_enabled_previews_once_beside_the_load(self):
        import threading
        from unittest.mock import patch
        from rc2016.levels import scene
        from rc2016.shaders import terrain_preview
        class Var:
            def __init__(self,value):self.value=value
            def get(self):return self.value
        class Thread:
            def __init__(self,target,**_kw):self.target=target
            def start(self):self.target()
        posted=[];calls=[]
        panel=object.__new__(LevelPanel)
        panel.experimental_var=Var(False);panel.material_previews_var=Var(True)
        panel.relay=type('Relay',(),{'put':lambda _,item:posted.append(item)})()
        panel.cancel=threading.Event();panel.source_root=lambda:'';panel._archive=lambda root:'archive'
        panel.material_paths,panel.preview_paths=set(),set()
        def entries(archive,materials,done,cache,previews=False):
            calls.append(([m['path'] for m in materials],previews));return {m['path']:dict(path=m['path']) for m in materials}
        paths={'other.material',terrain_preview.MATERIAL}
        with patch.object(scene,'material_entries',side_effect=entries), patch('threading.Thread',Thread):
            panel._prepare_previews(paths)
            self.assertEqual(calls,[])   # disabled: no analysis
            panel.experimental_var.value=True
            panel._prepare_previews(paths);panel._prepare_previews(paths)
        self.assertEqual(calls,[([terrain_preview.MATERIAL],True)])
        self.assertEqual(posted,[('preview_materials',{terrain_preview.MATERIAL:dict(path=terrain_preview.MATERIAL)})])

    def test_replace_materials_requeues_posted_and_queued_entries(self):
        view=object.__new__(LevelViewport)
        view._materials={'a':'old a','b':'old b'};view._pending_materials={}
        view._material_queue={'c':'old c'}
        added=[]
        view.add_materials=lambda entries:added.append(dict(entries))
        view.replace_materials({'a':'new a','c':'new c'})
        self.assertEqual(view._materials,{'b':'old b'})
        self.assertEqual(view._material_queue,{})
        self.assertEqual(added,[{'a':'new a','c':'new c'}])

@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'),'real source controls need RC2016_RESEARCH')
class MountainSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.levels.scene import open_archive,asset_bytes
        from rc2016.viewer.experimental_materials import MATERIAL,GRAPH
        root=Path(os.environ['RC2016_RESEARCH'])
        game=root.parents[1]/'Ratchet & Clank (EUR)'/'CUSA01073'
        if not game.is_dir():raise unittest.SkipTest('validated source archive unavailable')
        cls.game = game
        archive=open_archive(str(game))
        try:cls.material=asset_bytes(archive,MATERIAL);cls.graph=asset_bytes(archive,GRAPH)
        finally:archive.close()

    def test_actual_material_preview_and_rejection_controls(self):
        receipt=build_preview(self.material,self.graph)
        self.assertEqual(receipt['status'],'experimental');self.assertFalse(receipt['automatic_ready'])
        self.assertFalse(receipt['game_equivalence'])
        self.assertEqual({b['api'] for b in receipt['texture_bindings']},{8,9})
        self.assertTrue(emit_surface(receipt,experimental=True)['samplers'])
        with self.assertRaisesRegex(ValueError,'changed'):build_preview(self.material+b'\0',self.graph)
        with self.assertRaisesRegex(ValueError,'changed'):build_preview(self.material,self.graph+b'\0')
        # Independent compact source blend; both face orientations and boundaries.
        for y in (-1.,0.,.125,.5,.875,1.):
            for face in (0,1):
                inputs={'preview_position.0':45.,'preview_position.2':-30.,
                        'preview_normal.0':(1-y*y)**.5,'preview_normal.1':y,'preview_normal.2':0.,
                        'preview_front_face':face}
                coords=[]
                def sample(api,uv):
                    coords.append((api,uv));return (0.,0.,0.,1.) if api in (6,7) else ((.2,.3,.4,1.) if api==8 else (.8,.7,.6,1.))
                result=evaluate_surface(receipt,(.4,.6),sample,preview_inputs=inputs)
                w=max(0.,min(1.,((1 if face else -1)*y-.125)/.75))
                for a,b in zip(result['albedo'],[.2+.6*w,.3+.4*w,.4+.2*w]):self.assertAlmostEqual(a,b)
                self.assertIn((9,(1.5,-1.)),coords)
                self.assertIn((8,(.4,.6)),coords)

    def test_actual_texture_loader_prepares_preview_without_advertising_native_readiness(self):
        from rc2016.levels.scene import open_archive
        from rc2016.viewer.experimental_materials import MATERIAL
        from rc2016.viewer.textures import material_textures
        archive = open_archive(str(self.game))
        try:
            entry = material_textures(archive,[dict(index=0,path=MATERIAL)],{})[0]
            self.assertNotIn('native_translation',entry)
            self.assertTrue(entry.get('experimental_preview'),entry.get('experimental_error'))
            self.assertEqual(set(entry['experimental_textures']),{8,9})
            self.assertTrue(all(t.get('pixels') for t in entry['experimental_textures'].values()))
        finally:archive.close()

