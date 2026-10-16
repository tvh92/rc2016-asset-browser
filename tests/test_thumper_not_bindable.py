"""Thumper glove glass and shockwave field must stay unbound: no frozen-input preview is defensible."""
import hashlib
import os
from pathlib import Path
import unittest

from rc2016.shaders.bindings import SOURCE_PREVIEW_POLICIES

CASES = (
    ('materialgraph/equipment/weapon/wpn_thumper_glove_glass/wpn_thumper_glove_glass.materialgraph',
     'material/equipment/weapon/wpn_thumper_glove_glass/wpn_thumper_glove_glass.material',
     'ffec1bf59b2567ad404a016412708a6df957f004250cf276d9c13e725743ad98'),
    ('materialgraph/equipment/projectile/proj_thumper_field/proj_thumper_field.materialgraph',
     'material/equipment/projectile/proj_thumper_field/proj_thumper_field.material',
     '61e304422d517b569573cf75ee41f3233875897bee8239f7031bebfb87ee7dc2'),
)


class ThumperPolicyTests(unittest.TestCase):
    def test_no_source_preview_policy(self):
        for graph, _, _ in CASES:
            self.assertNotIn(graph, SOURCE_PREVIEW_POLICIES)


class ThumperArchiveTests(unittest.TestCase):
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

    def test_materials_stay_unsupported(self):
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import prepare_translation
        for graph, material, graph_sha in CASES:
            with self.subTest(graph=graph):
                graph_data = asset_bytes(self.archive, graph)
                material_data = asset_bytes(self.archive, material)
                self.assertEqual(hashlib.sha256(bytes(graph_data)).hexdigest(), graph_sha)
                params = extract_material_parameters(material_data)
                translation = prepare_translation([graph_data], material_data, params,
                                                  source_graph_paths=[graph], source_material_path=material)
                self.assertNotEqual(translation['status'], 'ready')
                self.assertNotIn('source_preview_inputs', translation)


if __name__ == '__main__':
    unittest.main()
