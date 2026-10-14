"""Archive-backed controls for the effective Novalis replacement materials."""
import hashlib
import os
from pathlib import Path
import unittest
from rc2016.levels.scene import open_archive, asset_bytes
from rc2016.shaders import bindings, interpolants
from rc2016.viewer.experimental_materials import prepare_preview
from rc2016.viewer.native_glsl import prepare_material_translation, evaluate_surface, emit_surface

GRAPH='materialgraph/environment/global/rock/mo_blend_01/rp_mo_blend_01_glow_vert_paint.materialgraph'
MATERIALS=(
('material/environment/novalis/ground/nov_gnd_cave_floor_blend_01/rp_nov_gnd_cave_floor_blend_01.material','f574dfd20a8f52777204a732695a15b89b5379c670dc47463fc4b46a9390f211'),
('material/environment/novalis/ground/nov_gnd_cave_floor_blend_01/rp_nov_gnd_cave_floor_sand_blend_01.material','5569ed9a2969f96628bc8fd014165e277e5d960ebdf688f5c8109d428f7270b2'))

class ReplacementPaintSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.environ.get('RC2016_RESEARCH')
        if not root:raise unittest.SkipTest('RC2016_RESEARCH not set')
        game=Path(root).parents[1]/'Ratchet & Clank (EUR)'/'CUSA01073'
        if not game.is_dir():raise unittest.SkipTest('validated source archive unavailable')
        cls.archive=open_archive(str(game))
        cls.addClassCleanup(cls.archive.close)
        cls.graph=asset_bytes(cls.archive,GRAPH)
        cls.previews=[prepare_preview(cls.archive,p) for p,_sha in MATERIALS]

    def test_source_partition_and_all_same_pass_exporters_agree(self):
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(),'3222a3c77a202350f42685257acc3032f786eb14a377631da91f24f7b00ae6e0')
        partitions=interpolants.shader_pass_partitions(self.graph)
        self.assertEqual([partitions[h] for h in (108804,88452,120836)],[1,1,1])
        _common,by_header=interpolants.squared_stream_evidence(self.graph)
        self.assertEqual(by_header[108804][(3,3)],(5,3))
        for header in (88452,120836):
            row=next(h for h in interpolants.shader_headers(self.graph) if h['header_offset']==header)
            (params,ir),reason=interpolants.vertex_exports(row)
            self.assertIsNone(reason)
            self.assertEqual(interpolants._squared_stream(ir,params[3][3]),(5,3))

    def test_effective_replacements_are_experimental_not_native_ready(self):
        for (path,sha),receipt in zip(MATERIALS,self.previews):
            with self.subTest(material=path):
                self.assertEqual(hashlib.sha256(asset_bytes(self.archive,path)).hexdigest(),sha)
                self.assertEqual(prepare_material_translation(self.archive,path)['status'],'unsupported')
                self.assertEqual(receipt['status'],'experimental')
                self.assertEqual(receipt['preview_contract'],bindings.PAINT_STREAM_CONTRACT)
                self.assertFalse(receipt['automatic_ready']);self.assertFalse(receipt['game_equivalence'])
                self.assertEqual(receipt['source_shader_record']['index'],17)
                self.assertEqual(receipt['painted_channel_evidence'],[dict(attribute=3,channel=3,semantic=5,component=3)])
                self.assertNotIn('world_position_terms',receipt)
                self.assertFalse(any(n.get('kind') in ('preview_position','preview_normal','cb') for n in receipt['nodes']))
                self.assertIn('vRockAlpha',emit_surface(receipt,experimental=True)['body'])
                with self.assertRaises(ValueError):emit_surface(receipt)

    def test_source_colour_blend_preserves_endpoints_and_interior_weights(self):
        a=(.2,.3,.4,1.);b=(.8,.7,.6,1.)
        for receipt in self.previews:
            for alpha in (0.,.125,.5,1.):
                def sample(api,uv):return a if api==8 else b if api==9 else (.25,.04,.04,1.)
                actual=evaluate_surface(receipt,(.4,.6),sample,preview_inputs={'preview_vertex_alpha_squared':alpha*alpha})
                for ch in range(3):self.assertAlmostEqual(actual['albedo'][ch],a[ch]+alpha*alpha*(b[ch]-a[ch]))
