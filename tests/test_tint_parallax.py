"""Exact tint-POM binding and parity regressions (no proprietary data in repo)."""
import json,os,struct,unittest
from pathlib import Path
from unittest.mock import patch
from rc2016.viewer import textures as tp
from tests.test_viewer_textures import FakeArchive,dat1,slot_rows
GRAPH='materialgraph/environment/global/gbl_color_tint_01/jn_color_tint_detail_parallax.materialgraph'
ROOT=Path(os.environ.get('RC2016_RESEARCH','missing'))/'Animation/pipeline/Research/Materials/ColorTintVariants'
class TintParallaxTests(unittest.TestCase):
 def test_exact_rule_and_misleading_texture_names(self):
  rule=tp._graph_rule([GRAPH]);self.assertEqual(rule.get('parallax'),'tint');self.assertEqual(rule.get('tint_variant'),'detail')
  self.assertEqual(tp._graph_rule([GRAPH.replace('.materialgraph','_extra.materialgraph')]),{})
  slots=[0x49A7866BB521DFCB,0x49A7866BABDAD780,0x49A7866B1576D848,0x49A7866B7AE34E7A,0x49A7866B4A362E9B,0x49A7866B93BB7340]
  paths=['t/source_height_c.texture','t/source_albedo_c.texture','t/misleading_spec_c.texture','t/base_n.texture','t/detail_n.texture','t/tintmask.texture']
  gd=dat1(paths,lambda at:[(tp.GRAPH_SLOT_SECTION,slot_rows(list(zip(at,slots))))])
  md=dat1([GRAPH,'t/real_spec_c.texture'],lambda at:[(tp.MATERIAL_SLOT_SECTION,slot_rows([(at[1],slots[2])]))])
  arc=FakeArchive({GRAPH:('materialgraph',gd),'m/material.material':('material',md)})
  defaults={0xEE0F9FF8:1.,0x93F9F959:.25,0x62835AF9:1.,0x24F4CA4C:1.,0x3BBC1444:.75}
  params=dict(parameters=[],unsupported_parameters=[dict(key_hash=k,size=4,raw_hex=struct.pack('<f',v).hex()) for k,v in [(0xEE0F9FF8,0.),(0x93F9F959,0.),(0x3BBC1444,0.),(0x47F581F6,99.),(0x66EFA55B,20.)]])
  with patch('rc2016.shaders.families.graph_parameter_tables',return_value=([[dict(key_hash=k) for k in defaults]],defaults)),patch('rc2016.formats.materials.extract_material_parameters',return_value=params):
   e=tp.material_texture_paths(arc,'m/material.material')
  self.assertEqual(e['_g'],'t/real_spec_c.texture');self.assertEqual(e['_parallax_height'],paths[0]);self.assertEqual(e['_tint_detail'],paths[4]);self.assertEqual(e['_h'],paths[5])
  self.assertEqual((e['k'],e['g_mode']),(0.,'grey'));self.assertEqual(e['parallax']['kind'],2);self.assertEqual(e['parallax']['height_scale'],0.);self.assertEqual((e['parallax']['steps_front'],e['parallax']['steps_grazing']),(16.,64.));self.assertEqual(e['tint']['detail_scale'],0.);self.assertEqual(e['tint']['detail_strength'],1.);self.assertEqual(e['tint']['mask_b'],1.);self.assertEqual(e['tint']['palette_blend'],0.)
 @unittest.skipUnless((ROOT/'parallax_followup/source_receipt.json').exists(),'Requires private tint-POM source receipt')
 def test_all_three_source_material_slots_and_values(self):
  receipt=json.loads((ROOT/'parallax_followup/source_receipt.json').read_text());gd=(ROOT/'source_assets/jn_color_tint_detail_parallax.materialgraph').read_bytes()
  for row in receipt['materials']:
   md=(ROOT/'parallax_followup'/Path(row['material']).name).read_bytes();arc=FakeArchive({GRAPH:('materialgraph',gd[4:]),row['material']:('material',md[4:])})
   e=tp.material_texture_paths(arc,row['material']);expected={b['api']:b['path'] for b in row['bindings']}
   with self.subTest(material=row['material']):
    self.assertEqual([e[k] for k in ('_parallax_height','_c','_g','_n','_tint_detail','_h')],[expected[i] for i in range(5,11)])
    h=next(v['value'][0] for v in row['values'] if v['key']=='0xEE0F9FF8');self.assertAlmostEqual(e['parallax']['height_scale'],h)
    self.assertEqual((e['k'],e['g_mode'],e['parallax']['writer']),(0.,'grey','4E4D7894'))
 def test_shared_sampler_and_tint_uv_order_contract(self):
  from rc2016.viewer import shading
  self.assertEqual(shading.PARALLAX_HEIGHT_TEXTURE_UNIT,12);self.assertEqual(shading.TINT_DETAIL_TEXTURE_UNIT,9)
  source=shading.FRAGMENT_SHADER if hasattr(shading,'FRAGMENT_SHADER') else shading.FRAGMENT
  self.assertIn('texture2D(uTexH, materialUV)',source)
  self.assertIn('materialUV * uTintDetailP.x',source)
  self.assertLess(source.index('albedo = b2 + m * (a2 - b2)'),source.index('albedo *= parallaxShadowWeight'))
if __name__=='__main__':unittest.main()
