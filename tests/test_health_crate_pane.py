"""Health (nanotech) crate window panes: alpha_over material drawn with alpha = sat(_c.a)."""
import os
from pathlib import Path
import unittest

from rc2016.viewer.native_glsl import fragment_source
from rc2016.viewer.textures import HEALTH_CRATE_GRAPH_SHA256, HEALTH_PANE_DISCARD

BASE = 'material/environment/global/gameplay/gbl_gp_Crate_Nanotech_01/'
BODY, PANE = BASE + 'gbl_gp_Crate_Nanotech_01.material', BASE + 'gbl_gp_Crate_Nanotech_Alpah_01.material'


class HealthCratePaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_RESEARCH')
        if not research:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        root = Path(os.environ.get('RC2016_GAME_ROOT', str(Path(research).parents[1] / 'Ratchet & Clank (EUR)' / 'Image0')))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(root)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_only_the_alpha_over_material_is_a_pane(self):
        from rc2016.viewer.textures import material_texture_paths
        pane = material_texture_paths(self.archive, PANE).get('alpha_pane')
        self.assertEqual(pane['graph_sha256'], HEALTH_CRATE_GRAPH_SHA256)
        self.assertEqual((pane['alpha_api'], pane['discard'], pane['opacity']), (5, HEALTH_PANE_DISCARD, 1.0))
        self.assertEqual((pane['writers'], pane['surface_writer']), (['BCE8624F'], '5C03EF34'))
        captured = pane['captured']   # the crate's own pane records (pass 1), not another material's
        self.assertEqual((captured['program'], captured['instance_flags'], captured['rec66'], captured['records']),
                         ('EC04A1EB', 0x61, 1.0, 7))
        self.assertEqual(captured['rec67'], HEALTH_PANE_DISCARD)
        self.assertNotIn('alpha_pane', material_texture_paths(self.archive, BODY))

    def test_pane_program_writes_c_alpha_and_body_stays_opaque(self):
        from rc2016.viewer.native_glsl import prepare_material_translation
        translation = prepare_material_translation(self.archive, PANE)
        self.assertEqual(translation['status'], 'ready')
        opaque = fragment_source(translation)['source']
        pane = fragment_source(translation, alpha_api=5, alpha_discard=HEALTH_PANE_DISCARD)['source']
        self.assertIn(', 1.0));', opaque.split('void main()')[1].rsplit('sceneOutput', 1)[1])
        self.assertNotIn('paneAlpha', opaque)
        self.assertIn('paneAlpha = clamp(texture2D(uNativeTex5, vUV).a, 0.0, 1.0)', pane)
        self.assertIn('paneAlpha));', pane)
        with self.assertRaises(ValueError):
            fragment_source(translation, alpha_api=9)

    def test_rule_does_not_change_the_native_status(self):
        from rc2016.formats.materials import final_shader_status, viewer_graph_rule
        from rc2016.viewer.native_glsl import prepare_material_translation
        translation = prepare_material_translation(self.archive, PANE)
        bound = {b['path'] for b in translation['texture_bindings']}
        rule = viewer_graph_rule(self.archive, PANE, bound)
        self.assertIn('alpha_pane', rule['ported'])
        self.assertEqual(final_shader_status('color_only_preview', rule, bound, translation, None, [],
                                             ['required/materials/health_crate.materialgraph']),
                         'native_translated_deferred_frontend')


if __name__ == '__main__':
    unittest.main()
