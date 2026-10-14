"""Source-bound ground preview stays explicit and preserves production gates."""
import json
import math
import os
from pathlib import Path
import unittest

from rc2016.shaders import terrain_preview as ground
from rc2016.viewer.native_glsl import emit_surface, evaluate_surface
from rc2016.viewer.viewport import ModelViewport


class GroundControlTests(unittest.TestCase):
    def test_invalid_review_controls_do_not_change_view_state(self):
        view = object.__new__(ModelViewport)
        view.ground_preview_controls = ground.DEFAULT_CONTROLS
        for values in ((-1,0,1),(0,2,1),(0,0,0),(math.nan,0,1),(0,0,math.inf)):
            with self.assertRaises(ValueError): view.set_ground_preview_controls(*values)
            self.assertEqual(view.ground_preview_controls,ground.DEFAULT_CONTROLS)


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'),'source preview needs RC2016_RESEARCH')
class GroundSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.levels.scene import open_archive
        root=Path(os.environ['RC2016_RESEARCH'])
        cls.archive=open_archive(str(root.parents[1]/'Ratchet & Clank (EUR)'/'CUSA01073'))
        cls.receipt=ground.prepare_preview(cls.archive,ground.MATERIAL)

    @classmethod
    def tearDownClass(cls): cls.archive.close()

    def test_source_byte_offsets_and_texture_bindings(self):
        r=self.receipt
        self.assertEqual(r['status'],'experimental')
        self.assertFalse(r['automatic_ready']);self.assertFalse(r['game_equivalence'])
        self.assertEqual(r['preview_source']['cb2']['4'],0.5)
        self.assertEqual(r['preview_source']['cb2']['2'],1.5)
        paths={b['api']:b['path'] for b in r['texture_bindings']}
        self.assertEqual(set(paths),{6,7,8,9,12,13,14})
        self.assertTrue(paths[12].endswith('/nov_ground_dirt_01_c.texture'))
        self.assertTrue(paths[13].endswith('/nov_ground_dirt_02_c.texture'))
        self.assertTrue(paths[14].endswith('/nov_ground_grass_01_c.texture'))

    def test_explicit_emitter_gate_and_hash_mismatch(self):
        with self.assertRaisesRegex(ValueError,'ready'):emit_surface(self.receipt)
        self.assertEqual(len(emit_surface(self.receipt,experimental=True)['samplers']),7)
        changed=dict(self.receipt,code_sha256='different')
        with self.assertRaisesRegex(ValueError,'contract'):emit_surface(changed,experimental=True)

    def test_source_surface_endpoints_and_interiors(self):
        colors={12:(.2,.3,.4,1),13:(.6,.5,.4,1),14:(.1,.4,.2,1)}
        for red in (0.,.5,.75,1.):
            for green in (0.,.5,1.):
                for mask in (0.,.3,.7,1.):
                    r=ground.rebind_preview(self.receipt,(red,green,2.))
                    def sample(api,uv):
                        self.assertEqual(uv,(.4,.6))
                        return colors.get(api, {6:(.2,.04,0,1),7:(.6,.08,0,1),8:(.4,.1,0,1),9:(mask,0,0,1)}.get(api))
                    result=evaluate_surface(r,(.2,.3),sample)
                    t=green*green;w=max(0,min(1,(mask-red*red+.5)/.5))
                    expected=[(a+(b-a)*t)*(1-w)+c*w for a,b,c in zip(colors[12][:3],colors[13][:3],colors[14][:3])]
                    for actual,value in zip(result['albedo'],expected):self.assertAlmostEqual(actual,value)
                    f0=(.04+(.08-.04)*t)*1.5*(1-w)+.1*w
                    for actual in result['F0']:self.assertAlmostEqual(actual,f0)
                    self.assertAlmostEqual(result['gloss'],math.sqrt((.2+(.6-.2)*t)*(1-w)+.4*w))
                    self.assertEqual(result['emission'],0.)
                    self.assertIsNone(result['normal'])

    def test_candidate_paint_uses_squared_vertex_channels_and_exact_mixed_triangle_fallback(self):
        colors={12:(.2,.3,.4,1),13:(.6,.5,.4,1),14:(.1,.4,.2,1)}
        sample=lambda api,uv:colors.get(api,{6:(.2,.04,0,1),7:(.6,.08,0,1),8:(.4,.1,0,1),9:(.3,0,0,1)}.get(api))
        # Interpolation weights .25/.75: painted (R=1,G=0) then an unlisted vertex.
        for coverage in (0.,.25,1.):
            inputs={'preview_ground_blend.0':coverage,'preview_ground_blend.1':0.,'preview_ground_blend.2':coverage}
            r2=coverage+(1-coverage)*ground.DEFAULT_CONTROLS[0]**2
            g2=(1-coverage)*ground.DEFAULT_CONTROLS[1]**2
            w=max(0.,min(1.,(.3-r2+.5)/.5))
            expected=[(a+(b-a)*g2)*(1-w)+c*w for a,b,c in zip(colors[12][:3],colors[13][:3],colors[14][:3])]
            result=evaluate_surface(self.receipt,(.2,.3),sample,preview_inputs=inputs)
            for a,b in zip(result['albedo'],expected):self.assertAlmostEqual(a,b)
        body=emit_surface(self.receipt,experimental=True)['body']
        for ch in 'xyz':self.assertIn('vGroundBlend.'+ch,body)
        # Uniform review changes cannot recolour a fully painted vertex.
        fully={'preview_ground_blend.0':1.,'preview_ground_blend.1':0.,'preview_ground_blend.2':1.}
        a=evaluate_surface(self.receipt,(.2,.3),sample,preview_inputs=fully)
        b=evaluate_surface(ground.rebind_preview(self.receipt,(0.,1.,1.)),(.2,.3),sample,preview_inputs=fully)
        self.assertEqual(a,b)

    def test_saved_receipt_and_rebinding_are_stable(self):
        saved=json.loads(json.dumps(self.receipt))
        self.assertEqual(saved,self.receipt)
        self.assertEqual(ground.rebind_preview(saved,ground.DEFAULT_CONTROLS),self.receipt)

    def test_loader_adds_preview_without_changing_native_support(self):
        from rc2016.viewer.textures import material_textures
        from rc2016.viewer.native_glsl import prepare_material_translation
        entry=material_textures(self.archive,[dict(index=0,path=ground.MATERIAL)],{})[0]
        self.assertEqual(entry['experimental_preview']['preview_contract'],ground.CONTRACT)
        self.assertEqual(set(entry['experimental_textures']),{6,7,8,9,12,13,14})
        self.assertEqual(prepare_material_translation(self.archive,ground.MATERIAL)['status'],'unsupported')
        self.assertNotIn('native_translation',entry)

    def test_control_updates_preserve_native_and_mountain_receipts(self):
        view=object.__new__(ModelViewport);view._schedule_draw=lambda:None
        view._material_ops={1:'cached'};view._draw_list=['old']
        native={'status':'ready'};mountain={'preview_contract':'unrelated'}
        view._materials={'ground':dict(experimental_preview=self.receipt,native_translation=native),
                         'mountain':dict(experimental_preview=mountain)}
        view.set_ground_preview_controls(.5,.75,2.)
        self.assertIs(view._materials['ground']['native_translation'],native)
        self.assertIs(view._materials['mountain']['experimental_preview'],mountain)
        self.assertEqual(view._materials['ground']['experimental_preview']['preview_controls'],dict(red=.5,green=.75,uv_scale=2.))
        self.assertIsNone(view._draw_list);self.assertEqual(view._material_ops,{})


if __name__=='__main__':unittest.main()
