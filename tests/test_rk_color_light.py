"""rk_color_light deferred writer (7AFF8C69 / E17902BF): constant albedo, self-lit, grey F0 and gloss by key.

The hoverboard's enm_Space_Commando_Glass slot (material rk_env_gray) is drawn with this writer: captured G-buffer draw, CB table
0 = (0.2086 x 3, 0, 0, 0). The rule-table checks always run; the archive checks read the real materials and skip without
RC2016_RESEARCH.
"""
import os
import unittest
from pathlib import Path

from rc2016.formats.links import EXACT, GENERIC, rule_coverage
from rc2016.viewer import textures as tp

GRAPH = 'materialgraph/global/rk_color_light/rk_color_light.materialgraph'
FOLDER = 'material/global/rk_env_black/'
OPAQUE = ('rk_env_gray', 'rk_env_black', 'rk_light_color_cool', 'rk_light_color_cool_bright', 'rk_light_color_red')
ALPHA_OVER = ('rk_light_color_yellow', 'rk_light_color_warm')
HOVERBOARD = 'equipment/gadget/gdgt_hoverboard/gdgt_hoverboard.model'


class RuleTableTests(unittest.TestCase):
    def test_rule_is_exact_graph_and_gated(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: tp.RK_COLOR_LIGHT_GRAPH_SHA256})
        self.assertEqual(rule['flat_surface'], 'color_light')
        self.assertEqual(rule['self_lit_key'], 0xFF87F342)
        self.assertEqual(rule['graph_sha256'], tp.RK_COLOR_LIGHT_GRAPH_SHA256)
        self.assertNotIn('flat_surface', tp._graph_rule([GRAPH], {GRAPH: '0' * 64}))   # another graph build: no rule
        # Gated: exact only for the materials whose flags select the deferred writer.
        self.assertEqual(rule_coverage([GRAPH])[0], GENERIC)
        self.assertEqual(rule_coverage([GRAPH], forward=True)[0], EXACT)

    def test_cb_table_keys(self):
        # Graph CB table 0: dw0..2 colour, dw3 self-lit, dw4 grey F0, dw5 gloss.
        self.assertEqual(tp.RK_COLOR_LIGHT_KEYS, dict(colour=0xBEAB84FA, self_lit=0xFF87F342, f0=0xFEE38525, gloss=0x6BA9C99C))


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ.get('RC2016_GAME_ROOT', os.environ['RC2016_RESEARCH']))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(root)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_hoverboard_glass_slot_matches_the_capture(self):
        paths = tp.material_texture_paths(self.archive, FOLDER + 'rk_env_gray.material')
        flat = paths['flat_surface']
        self.assertEqual((flat['model'], flat['writer']), ('color_light', '7AFF8C69'))
        self.assertEqual([round(x, 4) for x in flat['colour']], [0.2086] * 3)   # captured CB dw0..2 0x3E55A4E7
        self.assertEqual((flat['f0'], flat['gloss'], paths['k']), (0.0, 0.0, 0.0))   # captured dw4, dw5, dw3
        for role in ('_c', '_g', '_n'):
            self.assertNotIn(role, paths)

    def test_only_opaque_materials_take_the_writer(self):
        from rc2016.viewer.loader import material_rules
        for name in OPAQUE:
            path = FOLDER + name + '.material'
            self.assertEqual(tp.material_texture_paths(self.archive, path)['flat_surface']['model'], 'color_light', name)
            self.assertEqual(material_rules(self.archive, [{'path': path}])[path]['rule_kind'], 'exact rule', name)
        for name in ALPHA_OVER:
            path = FOLDER + name + '.material'
            self.assertNotIn('flat_surface', tp.material_texture_paths(self.archive, path), name)
            self.assertEqual(material_rules(self.archive, [{'path': path}])[path]['rule_kind'], 'generic reading', name)

    def test_keys_follow_the_graph_cb_table(self):
        import hashlib
        from rc2016.shaders.families import graph_parameter_tables
        key = next(k for k in self.archive.resolve(GRAPH) if self.archive.entries[k]['type'] == 'materialgraph')
        data = tp.asset_data(self.archive, key)
        self.assertEqual(hashlib.sha256(data).hexdigest(), tp.RK_COLOR_LIGHT_GRAPH_SHA256)
        table = [(row['dword'], row['key_hash']) for row in graph_parameter_tables(data)[0][0]]
        k = tp.RK_COLOR_LIGHT_KEYS
        self.assertEqual(table, [(0, k['colour']), (3, k['self_lit']), (4, k['f0']), (5, k['gloss'])])

    def test_blender_front_end_gets_the_rule(self):
        from rc2016.formats.materials import viewer_graph_rule
        rule = viewer_graph_rule(self.archive, FOLDER + 'rk_env_gray.material')
        self.assertTrue(rule['applies'])
        self.assertIn('flat_surface', rule['ported'])
        self.assertEqual(rule['terms']['flat_surface']['model'], 'color_light')


if __name__ == '__main__':
    unittest.main()
