"""Exact basic POM bindings, safe Blender route and source-backed instruction controls."""
import hashlib,importlib.util,json,math,os,unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_two_color_metal import SourceAssetFakeArchive,source_value
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT,PARALLAX_HEIGHT_TEXTURE_UNIT
from rc2016.formats.materials import viewer_graph_rule,_viewer_rule_wins_translation
from rc2016.shaders.families import graph_parameter_tables
GRAPH='materialgraph/global/basic_parallax_occlusion/basic_parallax_occlusion.materialgraph'
SLOTS={'_c':0x49A7866BABDAD780,'_g':0x49A7866B1576D848,'_n':0x49A7866B7AE34E7A,'_parallax_height':0x49A7866BB521DFCB}
ARCHIVE = Path(__file__).resolve().parents[1] / 'Research' / '2026-10-22_from-PS4-animation-pipeline'

class RouteTests(unittest.TestCase):
 def test_explicit_exceptional_parameter_policy(self):
  from rc2016.viewer.viewport import parallax_preview_parameters_valid
  self.assertTrue(parallax_preview_parameters_valid({'height_scale':-.2,'steps_front':8,'steps_grazing':16}))
  self.assertTrue(parallax_preview_parameters_valid({'height_scale':0.}))
  for key,val in [('height_scale',float('nan')),('fade',float('inf')),('steps_front',0.),('steps_grazing',-1.),('steps_front',2147483648.)]:
   self.assertFalse(parallax_preview_parameters_valid({key:val}),(key,val))

 def test_pom_only_does_not_discard_proven_channel_rule(self):
  self.assertTrue(_viewer_rule_wins_translation(True,{'unported':['parallax']}))
  self.assertTrue(_viewer_rule_wins_translation(True,{'unported':[]}))
  self.assertTrue(_viewer_rule_wins_translation(False,{'unported':['parallax','tint']}))
  self.assertFalse(_viewer_rule_wins_translation(True,{'unported':['parallax','tint']}))
  self.assertFalse(_viewer_rule_wins_translation(True,{'unported':['tint']}))
 def test_exact_rule_and_capability_contract(self):
  self.assertTrue(tp._graph_rule([GRAPH])['parallax'])
  self.assertEqual(tp._graph_rule([GRAPH.replace('.materialgraph','_other.materialgraph')]),{})
  self.assertEqual(PARALLAX_HEIGHT_TEXTURE_UNIT,12)
  self.assertIn('#ifdef GL_ARB_shader_texture_lod',FRAGMENT)
  self.assertNotIn('#extension GL_ARB_shader_texture_lod : require',FRAGMENT)
  self.assertIn('texture2DGradARB(uTexParallaxHeight',FRAGMENT)
  self.assertNotIn('max(ndv',FRAGMENT)
class SourceTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.here=ARCHIVE/'decodes/Materials/ParallaxOcclusion'
  if not cls.here.is_dir():raise unittest.SkipTest('local research archive not present')
  manifest=json.loads((cls.here/'source_assets/manifest.json').read_text())['assets']
  records=[];cls.sources=[]
  for row in manifest:
   data=(cls.here/'source_assets'/row['file']).read_bytes()
   assert hashlib.sha256(data).hexdigest()==row['sha256']
   kind='materialgraph' if row['file'].endswith('.materialgraph') else 'material'
   records.append((row['path'],kind,data))
   if kind=='material':cls.sources.append((row,data))
   else:cls.graph=data
  cls.archive=SourceAssetFakeArchive(records);cls.defaults=graph_parameter_tables(cls.graph)[1]
 def test_all_19_exact_slots_and_step_height_values(self):
  self.assertEqual(len(self.sources),19)
  for row,data in self.sources:
   p=tp.material_texture_paths(self.archive,row['path']);slots=tp.texture_slots(data,tp.MATERIAL_SLOT_SECTION)
   for role,slot in SLOTS.items():self.assertEqual(p[role],slots.get(slot) or tp.texture_slots(self.graph,tp.GRAPH_SLOT_SECTION)[slot])
   self.assertEqual(p['g_mode'],'grey');self.assertEqual(p['k'],0);self.assertTrue(p['parallax_normal'])
   for field,key,default in [('height_scale',0x47F581F6,.1),('steps_front',0xD709BC41,16),('steps_grazing',0xD60526F5,64)]:
    want,_=source_value(data,key,default,defaults=self.defaults)
    self.assertAlmostEqual(p['parallax'][field],want,places=7)
 def test_blender_unported_reason_and_normal_equation(self):
  for row,data in self.sources:
   paths=tp.material_texture_paths(self.archive,row['path'])
   rule=viewer_graph_rule(self.archive,row['path'],[paths[k] for k in SLOTS])
   self.assertEqual(rule['unported'],['parallax']);self.assertNotIn('parallax',rule['ported'])
   self.assertIn('unshifted C/G/N',rule['unported_reasons']['parallax'])
   self.assertEqual(rule['textures']['_parallax_height'],paths['_parallax_height'])
   self.assertEqual(rule['normal_z'],'1-x*x-y*y')
 def test_height_alpha_transport_and_baseline(self):
  row,data=self.sources[0]
  with patch.object(tp,'load_texture',side_effect=lambda a,p:dict(path=p)):
   got=tp.material_textures(self.archive,[{'path':row['path'],'index':0}],{})[0]
  self.assertEqual(got['_parallax_height']['path'],tp.material_texture_paths(self.archive,row['path'])['_parallax_height'])
  original=tp.GRAPH_RULES
  with patch.object(tp,'GRAPH_RULES',tuple((p,dict(r,parallax=False) if p==GRAPH[:-13] else r) for p,r in original)):
   before=tp.material_texture_paths(self.archive,row['path'])
  self.assertNotIn('parallax',before);self.assertTrue(before['parallax_normal'])
  for k in ('_c','_g','_n','g_mode','k'):self.assertEqual(before[k],got[k]['path'] if k.startswith('_') else got[k])
 def test_source_instruction_controls(self):
  path=self.here/'pom_control.py';spec=importlib.util.spec_from_file_location('basic_pom_control',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  result=module.run(1200)
  self.assertLess(result['max_uv_error_moderate'],1e-12);self.assertLess(result['max_uv_error_normalized'],1e-11);self.assertLess(result['max_shadow_error'],1e-12)
  self.assertTrue(all(result['coverage'].values()))
if __name__=='__main__':unittest.main()
