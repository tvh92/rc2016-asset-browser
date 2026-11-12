"""basic_normal_gloss alpha-over materials: the 5EBDECA4 surface drawn by forward program 37AF5B69 with
alpha = sat(sat(_c.a) attribute0.w), attribute0.w = byte 22 / 255, discard below the material cut-off (docs/viewer.md).

Synthetic checks (GLSL text, uniforms, validator constants) always run; archive checks skip without RC2016_RESEARCH.
"""
import os
import unittest
from pathlib import Path

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT, OPAQUE_UNIFORMS

CLAW_PAINT = 'material/environment/deplanetizer/architecture/dep_arch_ext_claw_paint_01/rv_dep_arch_ext_claw_paint_01.material'
EYELASHES = 'material/character/npc/npc_elaris_eyelashes/npc_elaris_eyelashes.material'
METAL_TILE = 'material/environment/deplanetizer/architecture/dep_arch_ext_metal_tile_01/rv_dep_arch_ext_metal_tile_01.material'


class SyntheticTests(unittest.TestCase):
    def test_fragment_writes_the_pane_alpha(self):
        self.assertIn('uAlphaOut == 2 ? clamp(clamp(c.a, 0.0, 1.0) * uPaneOpacity, 0.0, 1.0) : c.a', FRAGMENT)
        self.assertIn('if (uHasC && c.a < uAlphaCut) discard;', FRAGMENT)   # attribute0.z
        self.assertIn('uPaneOpacity', OPAQUE_UNIFORMS)

    def test_native_pane_applies_the_opacity(self):
        from rc2016.viewer import native_glsl
        surface = dict(declarations='', body='', samplers=[dict(api=5, uniform='uNativeTex5')], key=())
        original = native_glsl.emit_surface
        native_glsl.emit_surface = lambda *a, **k: dict(surface)
        try:
            pane = native_glsl.fragment_source({}, alpha_api=5, alpha_discard=4 / 255, alpha_opacity=112 / 255)['source']
            crate = native_glsl.fragment_source({}, alpha_api=5, alpha_discard=4 / 255)['source']
        finally:
            native_glsl.emit_surface = original
        self.assertIn('paneAlpha = clamp(paneAlpha * ', pane)
        self.assertLess(pane.index('discard'), pane.index('paneAlpha * '))   # the cut applies to the cover
        self.assertNotIn('paneAlpha * ', crate)   # opacity 1: the crate panes keep their source

    def test_graph_identity(self):
        self.assertEqual(tp.BASIC_NORMAL_GLOSS_GRAPH, 'required/materials/basic_normal_gloss.materialgraph')
        self.assertEqual(len(tp.BASIC_NORMAL_GLOSS_GRAPH_SHA256), 64)

    def test_validator_constants(self):
        pane = dict(alpha_api=5, discard=4 / 255, opacity=112 / 255, two_sided=False, writers=['37AF5B69'])
        rule = dict(ported=['alpha_pane'], terms=dict(alpha_pane=pane), textures={'_c': 'c.texture'}, g_mode='grey')
        expected = validation._viewer_expected_constants(rule)
        self.assertAlmostEqual(expected['VR pane opacity'], 112 / 255)
        self.assertAlmostEqual(expected['VR pane alpha cut'], 4 / 255)
        names, roles = validation._viewer_required(rule, False)
        self.assertTrue({'VR pane opacity', 'VR pane alpha cut'} <= set(names))
        self.assertIn('_c', roles)


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_RESEARCH')
        if not research:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(Path(research))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_claw_paint_is_a_pane(self):
        paths = tp.material_texture_paths(self.archive, CLAW_PAINT)
        pane = paths['alpha_pane']
        self.assertEqual((pane['graph_sha256'], pane['writers'], pane['surface_writer'], pane['alpha_api']),
                         (tp.BASIC_NORMAL_GLOSS_GRAPH_SHA256, ['37AF5B69'], '5EBDECA4', 5))   # _c is API 5 of the translation
        self.assertEqual(pane['opacity'], 112 / 255)            # flags 0x23a, byte 22 = 112
        self.assertAlmostEqual(pane['discard'], 4 / 255, 6)     # byte 21 = 0, floored to 4 for a blended state
        self.assertFalse(pane['two_sided'])
        self.assertTrue(paths['_c'].endswith('rv_dep_ext_arch_claw_paint_01_c.texture'))
        self.assertEqual(paths['g_mode'], 'grey')               # the 5EBDECA4 surface reading is kept

    def test_two_sided_and_opaque(self):
        self.assertTrue(tp.material_texture_paths(self.archive, EYELASHES)['alpha_pane']['two_sided'])
        self.assertNotIn('alpha_pane', tp.material_texture_paths(self.archive, METAL_TILE))   # opaque flags 0x230

    def test_rule_ports_the_pane(self):
        from rc2016.formats.materials import viewer_graph_rule
        rule = viewer_graph_rule(self.archive, CLAW_PAINT)
        self.assertIn('alpha_pane', rule['ported'])
        self.assertEqual(rule['terms']['alpha_pane']['opacity'], 112 / 255)


if __name__ == '__main__':
    unittest.main()
