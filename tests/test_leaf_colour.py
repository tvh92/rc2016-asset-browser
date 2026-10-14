import os
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2016.viewer import textures as tp
from rc2016.formats.materials import viewer_graph_rule


class LeafPaletteTests(unittest.TestCase):
    def test_model_and_level_uniform_transport_reset_and_supported_api(self):
        from types import SimpleNamespace
        from rc2016.levels.viewport import _Recorder
        from rc2016.viewer.viewport import ModelViewport
        from rc2016.viewer.shading import OPAQUE_UNIFORMS, Shading
        recorder = _Recorder(object(),object())
        view = SimpleNamespace(_gl=recorder.gl,_shading=recorder.shading,native_translation=False,
                               GL_TEXTURE0=0x84C0,GL_TEXTURE_2D=0x0DE1)
        view._static_vector_input_eligible = lambda: ModelViewport._static_vector_input_eligible(view)
        view._selected_surface = lambda entry,level: ModelViewport._selected_surface(view,entry,level)
        uniforms = {name:i for i,name in enumerate(OPAQUE_UNIFORMS)}
        entry = dict(_cma=(77,True),leaf_colour=dict(exponent=4.,scale=.5,palette_rgb=[.1,.2,.3]))
        ModelViewport._apply_material(view,uniforms,entry,2)
        calls = {op[1]:(op[2],op[3]) for op in recorder.ops if op[0]=='u'}
        self.assertEqual(calls[uniforms['uLeafP']],('glUniform4f',(4.,.5,0.,0.)))
        self.assertEqual(calls[uniforms['uLeafColour']][1],(1,))
        self.assertIn(('t',0x84C0+7,0x0DE1,77),recorder.ops)
        self.assertTrue(all(op[2] in Shading.NAMES for op in recorder.ops if op[0]=='u'))
        for other,mode in (({},2),(entry,0)):
            recorder.ops.clear(); ModelViewport._apply_material(view,uniforms,other,mode)
            calls = {op[1]:op[3] for op in recorder.ops if op[0]=='u'}
            self.assertEqual(calls[uniforms['uLeafColour']],(0,))
            self.assertEqual(calls[uniforms['uHasCma']],(0,))

    def test_bilinear_repeat_and_srgb_before_filter(self):
        from PIL import Image
        image = Image.new('RGB', (2, 2))
        image.putdata([(0,0,0),(255,128,64),(255,255,255),(0,0,0)])
        with patch.object(tp, 'to_image', return_value=image):
            got = tp.leaf_palette_sample(dict(srgb=True), .5, .5)
            linear = lambda b: (b/255)/12.92 if b/255 <= .04045 else ((b/255+.055)/1.055)**2.4
            expected = [.5, (1+linear(128))/4, (1+linear(64))/4]
            for a,b in zip(got,expected): self.assertAlmostEqual(a,b)
            self.assertEqual(got,tp.leaf_palette_sample(dict(srgb=True), 1.5, -.5))
            self.assertEqual(tp.leaf_palette_sample(dict(srgb=True), .25, .25), [0.,0.,0.])


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'requires local game archive')
class LeafSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(str(Path(os.environ['RC2016_RESEARCH']).parents[1] / 'Ratchet & Clank (EUR)/CUSA01073'))

    def test_material_constants_and_blender_binding_gate(self):
        # Resolve the canonical source path from the archive, without inventing texture names.
        matches = [e['path'] for e in self.archive.entries.values() if e['type']=='material' and
                   e['path'].lower().endswith('/rp_nov_plant_tree_leaf_branches_01.material')]
        self.assertEqual(len(set(matches)), 1)
        path = matches[0]
        paths = tp.material_texture_paths(self.archive,path)
        leaf = paths['leaf_colour']
        self.assertEqual((leaf['exponent'],leaf['scale'],leaf['palette_uv'][0]), (4.,.5,.5))
        self.assertAlmostEqual(leaf['palette_uv'][1], .529, places=6)
        bound = [paths[r] for r in ('_c','_g','_n','_cma','_cmb') if paths.get(r)]
        rule = viewer_graph_rule(self.archive,path,bound)
        self.assertTrue(rule['applies']); self.assertIn('leaf_colour',rule['ported'])
        self.assertEqual(rule['terms']['leaf_colour']['palette_rgb'],
                         tp.leaf_palette_sample(tp.load_texture(self.archive,paths['_cmb']),*leaf['palette_uv']))
        missing = viewer_graph_rule(self.archive,path,[p for p in bound if p!=paths['_cmb']])
        self.assertIn('leaf_colour',missing['unported'])
        sibling = tp._graph_rule(['materialgraph/environment/global/plant/gbl_plant_tree_foliage_transparent_01/gbl_plant_tree_foliage_transparent_01.materialgraph'])
        self.assertEqual(sibling['leaf_colour'],'global')

    def test_global_bush_uses_literal_scale_row_mapped_normal_and_zero_specular(self):
        matches = [e['path'] for e in self.archive.entries.values() if e['type']=='material' and
                   e['path'].lower().endswith('/nov_shrub_shrub_leaves_01.material')]
        self.assertEqual(len(set(matches)),1)
        paths = tp.material_texture_paths(self.archive,matches[0]); leaf = paths['leaf_colour']
        self.assertEqual((leaf['writer'],leaf['exponent'],leaf['scale'],leaf['palette_uv']), ('CDD7CD94',12.,.5,(.5,.75)))
        self.assertTrue(leaf['mapped_fresnel']);self.assertNotIn('_g',paths);self.assertEqual(paths['k'],0.)
        self.assertEqual(leaf['keys'],dict(exponent=tp.LEAF_EXPONENT_KEY))
        rule = viewer_graph_rule(self.archive,matches[0],[paths[k] for k in ('_c','_n','_cma','_cmb')])
        self.assertIn('leaf_colour',rule['ported']);self.assertEqual(rule['normal_z'],'1-x*x-y*y')
        self.assertIsNone(rule['textures']['_g'])
        missing = viewer_graph_rule(self.archive,matches[0],[paths[k] for k in ('_c','_n','_cmb')])
        self.assertIn('leaf_colour',missing['unported'])


if __name__ == '__main__': unittest.main()
