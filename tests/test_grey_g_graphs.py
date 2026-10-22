"""GREY_G_GRAPHS: graphs whose lowered writers read a grey _g (F0 = G, gloss = R) and have no hand rule.

The table tests use no game data. The archive test recomputes every entry from the graph bytes (RC2016_RESEARCH) and skips without it.
"""
import hashlib
import os
import unittest
from pathlib import Path

from rc2016.formats.links import FAMILY, rule_coverage
from rc2016.shaders import g_layout
from rc2016.viewer import textures as tp
from rc2016.viewer.grey_g_graphs import GREY_G_GRAPHS


class GreyGTableTests(unittest.TestCase):
    def test_entries_are_exact_lower_case_graph_paths_with_a_sha256(self):
        self.assertGreater(len(GREY_G_GRAPHS), 20)
        for path, digest in GREY_G_GRAPHS.items():
            self.assertTrue(path.endswith('.materialgraph') and path == path.lower() and '\\' not in path, path)
            self.assertRegex(digest, '^[0-9a-f]{64}$')

    def test_rule_needs_the_exact_hash_and_reads_grey(self):
        path, digest = next(iter(GREY_G_GRAPHS.items()))
        rule = tp._graph_rule([path], {path: digest})
        self.assertEqual((rule['g_mode'], rule['graph_path'], rule['graph_sha256']), ('grey', path, digest))
        self.assertEqual(tp._graph_rule([path], {path: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([path]), {})

    def test_entries_are_family_fallbacks_so_native_translation_replaces_them(self):
        for path in GREY_G_GRAPHS:
            self.assertEqual(rule_coverage([path])[0], FAMILY, path)

    def test_no_hand_rule_precedes_an_entry(self):
        stems = {path.removesuffix('.materialgraph') for path in GREY_G_GRAPHS}
        for prefix, _rule in tp.GRAPH_RULES:
            if prefix not in stems:
                self.assertFalse(any(path.startswith(prefix) for path in GREY_G_GRAPHS), prefix)


class LayoutClassifierTests(unittest.TestCase):
    def test_graph_is_grey_only_when_no_program_reads_coloured_f0(self):
        grey, constant, coloured = dict(layout='grey'), dict(layout='constant'), dict(layout='coloured')
        self.assertTrue(g_layout.grey_graph([grey]))
        self.assertTrue(g_layout.grey_graph([grey, constant]))
        self.assertFalse(g_layout.grey_graph([grey, coloured]))
        self.assertFalse(g_layout.grey_graph([constant]))
        self.assertFalse(g_layout.grey_graph([]))


class GreyGArchiveTests(unittest.TestCase):
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

    def test_every_entry_matches_its_bytes_and_reads_a_grey_g(self):
        from rc2016.formats.archive import asset_data
        for path, digest in GREY_G_GRAPHS.items():
            ids = [i for i in self.archive.resolve(path) if self.archive.entries[i]['type'] == 'materialgraph']
            self.assertEqual(len(ids), 1, path)
            data = bytes(asset_data(self.archive, ids[0]))
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, path)
            self.assertTrue(g_layout.grey_graph(g_layout.writer_layouts(data)), path)

    def test_known_layouts(self):
        from rc2016.formats.archive import asset_data

        def layouts(path):
            ids = [i for i in self.archive.resolve(path) if self.archive.entries[i]['type'] == 'materialgraph']
            return {row['layout'] for row in g_layout.writer_layouts(bytes(asset_data(self.archive, ids[0])))}
        self.assertEqual(layouts('required/materials/basic_normal_gloss.materialgraph'), {'grey'})
        self.assertIn('coloured', layouts('materialgraph/global/basic_colored_spec/basic_colored_spec.materialgraph'))


if __name__ == '__main__':
    unittest.main()
