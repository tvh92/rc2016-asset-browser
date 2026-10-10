"""Archive-wide links table queries (rc2016/formats/links.py) and the details-tab lines (rc2016/app/links.py)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rc2016.app.links import placement_lines
from rc2016.formats import links
from rc2016.formats.links import AssetLinks


class RuleCoverageTests(unittest.TestCase):
    def test_no_graphs_is_no_graph(self):
        self.assertEqual(links.rule_coverage([]), (links.NO_GRAPH, None))

    def test_exact_writer_graph(self):
        self.assertEqual(links.rule_coverage(['required/materials/basic_normal_gloss.materialgraph']),
                         (links.EXACT, 'required/materials/basic_normal_gloss.'))

    def test_unknown_variant_falls_back_to_family(self):
        self.assertEqual(links.rule_coverage(['required/materials/basic_normal_gloss_unknownvariant.materialgraph']),
                         (links.FAMILY, 'required/materials/basic_normal_gloss'))

    def test_basic_skin_family_prefix(self):
        kind, _rule = links.rule_coverage(['materialgraph/global/basic_skin/anything.materialgraph'])
        self.assertEqual(kind, links.FAMILY)

    def test_unmatched_graph_is_generic(self):
        self.assertEqual(links.rule_coverage(['materialgraph/nothing/here.materialgraph']), (links.GENERIC, None))


class AssetLinksTests(unittest.TestCase):
    TEXTURE = 'required/textures/shared_metal.texture'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / 'links.sqlite'
        db = sqlite3.connect(path)
        db.executescript(links.SCHEMA)
        db.executemany('INSERT INTO model_materials VALUES (?, ?, ?)', [
            ('models/a.model', 'materials/a_metal.material', 1),
            ('models/a.model', 'materials/a_plain.material', 0),
            ('models/b.model', 'materials/b_graph.material', 0),
            ('models/c.model', 'materials/c_nograph.material', 0),
        ])
        db.executemany('INSERT INTO material_refs VALUES (?, ?, ?)', [
            ('materials/a_metal.material', self.TEXTURE, 'texture'),
            ('materials/a_metal.material', 'required/materials/basic_normal_gloss.materialgraph', 'graph'),
            ('materials/b_graph.material', 'materialgraph/global/basic_skin/skin.materialgraph', 'graph'),
        ])
        db.executemany('INSERT INTO graph_textures VALUES (?, ?)', [
            ('materialgraph/global/basic_skin/skin.materialgraph', self.TEXTURE),
        ])
        db.executemany('INSERT INTO zone_models VALUES (?, ?, ?)', [
            ('zones/z1.zone', 'models/p.model', 5),
            ('zones/z2.zone', 'models/p.model', 9),
        ])
        db.executemany('INSERT INTO level_zones VALUES (?, ?)', [
            ('levels/l1.level', 'zones/z1.zone'),
            ('levels/l2.level', 'zones/z1.zone'),
        ])
        db.commit()
        db.close()
        self.links = AssetLinks(path)

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def test_texture_users_split_direct_and_graph_default_reach(self):
        users = self.links.texture_users(self.TEXTURE)
        self.assertEqual(users, [
            dict(model='models/a.model', materials=['materials/a_metal.material'], via_graph=False),
            dict(model='models/b.model', materials=['materials/b_graph.material'], via_graph=True),
        ])

    def test_model_placements_list_unlisted_zone_first(self):
        placements = self.links.model_placements('models/p.model')
        self.assertEqual([p['zone'] for p in placements], ['zones/z2.zone', 'zones/z1.zone'])
        self.assertEqual(placements[0]['instances'], 9)
        self.assertEqual(placements[0]['levels'], [])
        self.assertEqual(placements[1]['instances'], 5)
        self.assertEqual(sorted(placements[1]['levels']), ['levels/l1.level', 'levels/l2.level'])

    def test_model_materials_follow_position(self):
        self.assertEqual(self.links.model_materials('models/a.model'),
                         ['materials/a_plain.material', 'materials/a_metal.material'])

    def test_coverage_reports_rule_per_material(self):
        coverage = {c['material']: c for c in self.links.coverage('models/a.model')}
        self.assertEqual(coverage['materials/a_metal.material']['kind'], links.EXACT)
        self.assertEqual(coverage['materials/a_plain.material']['kind'], links.NO_GRAPH)

    def test_models_by_coverage_collects_kinds_per_model(self):
        kinds = self.links.models_by_coverage()
        self.assertEqual(kinds['models/a.model'], {links.EXACT, links.NO_GRAPH})
        self.assertEqual(kinds['models/b.model'], {links.FAMILY})
        self.assertEqual(kinds['models/c.model'], {links.NO_GRAPH})


class PlacementLinesTests(unittest.TestCase):
    def test_empty_placements_say_none(self):
        self.assertIn('none', placement_lines([])[0])

    def test_two_zones_summary_and_unlisted_zone_marker(self):
        placements = [
            dict(zone='zones/z2.zone', instances=9, levels=[]),
            dict(zone='zones/z1.zone', instances=5, levels=['levels/l1.level', 'levels/l2.level']),
        ]
        lines = placement_lines(placements)
        self.assertIn('14 instances in 2 zones of 2 levels', lines[0])
        self.assertTrue(any('(no level lists this zone)' in line for line in lines[1:]))


if __name__ == '__main__':
    unittest.main()
