"""MTBUF component writes must not destroy adjacent exported values."""
import os
import math
from pathlib import Path
import struct
import unittest

from rc2016.shaders.translator import Compiler,decoder


class TypedBufferWriteSpanTests(unittest.TestCase):
    def test_encoded_load_widths_and_texture_failure_status(self):
        for opcode,width in enumerate((1,2,3,4)):
            for tfe in (0,1):
                # Independent MTBUF word: opcode 18:16, vdata 47:40,
                # TFE 55. Neighbour values represent live packed exports.
                word=0xe8000000|(opcode<<16)|(10<<40)|(tfe<<55)
                instruction=decoder().decode_stream(struct.pack('<Q',word),0)[0]
                self.assertEqual(instruction['kind'],13)
                compiler=Compiler(struct.pack('<Q',word),[])
                old={reg:compiler.ir.op('pack',compiler.ir.const(reg),compiler.ir.const(-reg))
                     for reg in range(9,16)}
                compiler.v.update(old);compiler.instruction(instruction)
                for reg,value in old.items():
                    with self.subTest(width=width,tfe=tfe,reg=reg):
                        if 10<=reg<10+width+tfe:
                            node=compiler.ir.nodes[compiler.v[reg]]
                            self.assertEqual(node['op'],'unsupported')
                            self.assertIn('TBUFFER_LOAD_FORMAT_',node['reason'])
                        else:self.assertEqual(compiler.v[reg],value)


class SourceBasicColourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace=os.environ.get('RC2016_RESEARCH')
        if not workspace:raise unittest.SkipTest('RC2016_RESEARCH not set')
        root=Path(workspace).parents[1]/'Ratchet & Clank (EUR)'/'CUSA01073'
        if not root.exists():raise unittest.SkipTest('Source archive unavailable')
        from rc2016.levels.scene import open_archive
        cls.archive=open_archive(root)

    @classmethod
    def tearDownClass(cls):cls.archive.close()

    def test_source_colour_writers_bind_without_inventing_buffer_values(self):
        from rc2016.viewer.native_glsl import selected_translation,evaluate_surface
        cases=[('material/environment/metropolis/ground/met_gnd_grass_distant_01/met_gnd_grass_distant_01.material',
                .12200000137090683,0.,(1.,1.)),
               ('material/environment/pokitaru/ground/pok_gnd_sand_floor_blend_01/rp_nov_gnd_grass_simple.material',
                .009999999776482582,0.,(50.,40.)),
               ('material/environment/metropolis/rock/met_rock_hoh_01/met_rock_hoh_tile_01.material',
                .30000001192092896,.05000000074505806,(30.,30.))]
        for path,gloss,spec,scale in cases:
            with self.subTest(path=path):
                receipt=selected_translation(self.archive,path)
                self.assertIsNotNone(receipt)
                self.assertEqual(receipt['equivalent_candidates'],2)
                self.assertIsNone(receipt['normal'])
                self.assertFalse(receipt['game_equivalence'])
                seen=[]
                def sample(api,uv):
                    seen.append(uv);return (.2,.4,.6,.9)
                result=evaluate_surface(receipt,(.25,.75),sample)
                self.assertEqual(result['albedo'],[.2,.4,.6])
                self.assertEqual(result['F0'],[spec]*3)
                self.assertEqual(result['gloss'],math.sqrt(gloss))
                self.assertEqual(result['emission'],0.)
                self.assertEqual(seen,[(.25*scale[0],.75*scale[1])])


if __name__=='__main__':unittest.main()
