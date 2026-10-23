"""Novalis crater pits: rp_nov_grass_dirt_blend_colorbreakup_01 (pass-0 writers E22E4D93 and 005F65CF), viewer rule and Blender terms."""
import os
import unittest
from pathlib import Path

from rc2016.viewer import textures as tp

GRAPH = 'materialgraph/environment/pokitaru/rock/pok_rock_blend_01/rp_nov_grass_dirt_blend_colorbreakup_01.materialgraph'
MATERIAL = 'material/environment/novalis/ground/nov_gnd_Crater_01/rp_nov_gnd_Crater_01.material'


class CraterRuleTests(unittest.TestCase):
    def test_rule_needs_the_exact_graph_hash(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: tp.CRATER_BLEND_GRAPH_SHA256})
        self.assertIs(rule['crater_blend'], True)
        self.assertEqual((rule['graph_path'], rule['graph_sha256']), (GRAPH, tp.CRATER_BLEND_GRAPH_SHA256))
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: 'f' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH]), {})

    def test_term_is_registered_for_the_blender_front_end(self):
        from rc2016.formats import materials
        self.assertIn('crater_blend', materials.VIEWER_RULE_EXTRAS)
        self.assertIn('crater_blend', materials.VIEWER_RULE_BUILT)
        self.assertEqual(materials.VIEWER_TERM_ROLES['crater_blend'], ('_crater_c',))
        self.assertNotIn('crater_blend', materials.VIEWER_LINEAR_Z_TERMS)   # the viewer keeps the sqrt normal z for the material normal

    def test_shader_declares_the_crater_uniforms(self):
        from rc2016.viewer import shading
        sources = [value for value in vars(shading).values() if isinstance(value, str)]
        self.assertTrue(any('uniform bool uCraterBlend; uniform vec4 uCraterP;' in text and 'uMode == 2 && uCraterBlend' in text for text in sources))
        self.assertIn('"uCraterBlend", "uCraterP"', Path(shading.__file__).read_text(encoding='utf-8'))


class CraterArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_RESEARCH')
        if not research:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        from rc2016.levels.scene import asset_bytes, open_archive
        try:
            cls.archive = open_archive(Path(research))
            cls.graph = bytes(asset_bytes(cls.archive, GRAPH))
        except (OSError, ValueError):
            raise unittest.SkipTest('Source archive not available')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_graph_hash(self):
        import hashlib
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), tp.CRATER_BLEND_GRAPH_SHA256)

    def test_reading_binds_the_decoded_slots_and_constants(self):
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        self.assertTrue(paths['_crater_c'].endswith('rp_nov_crater_c.texture'), paths['_crater_c'])
        self.assertTrue(paths['_g'].endswith('rp_nov_crater_g.texture'), paths['_g'])
        self.assertTrue(paths['_c'].endswith('nov_ground_grass_01_c.texture'), paths['_c'])
        cb = paths['crater_blend']
        self.assertAlmostEqual(cb['world_tile'], 5.0, places=6)
        self.assertAlmostEqual(cb['g_uv_scale'], 8.0, places=6)
        self.assertAlmostEqual(cb['crater_uv_scale'], 1.0, places=6)
        self.assertEqual(paths['graph_identity']['sha256'], tp.CRATER_BLEND_GRAPH_SHA256)


if __name__ == '__main__':
    unittest.main()
