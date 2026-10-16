"""Deferred writers that sample nothing join their pass through the B6 pointer when the native resource bitmap is empty."""
import hashlib
import os
from pathlib import Path
import unittest

GRAPH = 'materialgraph/equipment/weapon/wpn_plasma_striker_fx/wpn_plasma_striker_fx.materialgraph'
MATERIAL = 'material/equipment/weapon/wpn_plasma_striker_fx/wpn_plasma_striker_fx.material'
GRAPH_SHA256 = '2dcefd19645c75b10d5773454bc8224df7ea792e8b16b369e02e4e0edc9f5838'
MATERIAL_SHA256 = '4bbf9c4395892f4ea024110e292daf470b7a59a6d54aa7c5ea63d62ee889f8ee'


class ResourceFreeWriterJoinTests(unittest.TestCase):
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

    def test_plasma_striker_body_glow_binds_without_textures(self):
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import prepare_translation
        from rc2016.viewer.native_glsl import evaluate_surface
        graph = asset_bytes(self.archive, GRAPH)
        material = asset_bytes(self.archive, MATERIAL)
        self.assertEqual(hashlib.sha256(bytes(graph)).hexdigest(), GRAPH_SHA256)
        self.assertEqual(hashlib.sha256(bytes(material)).hexdigest(), MATERIAL_SHA256)
        paths = [r['path'] for r in references(material) if r['path'].endswith('.materialgraph')]
        translation = prepare_translation([graph], material, extract_material_parameters(material))
        self.assertEqual(translation['status'], 'ready')
        self.assertEqual(translation['shader_id'], '0x368cca2e')
        self.assertEqual(translation['texture_bindings'], [])
        self.assertFalse(translation['game_equivalence'])
        record = translation['source_shader_record']
        self.assertEqual((record['pass_index'], record['resource_mask']), (0, 0))
        self.assertFalse(any(n['op'] in ('texture', 'input') for n in translation['nodes']))
        result = evaluate_surface(translation, (.25, .75), lambda api, uv: (.25, .5, .75, .8))
        for got, want in zip(result['albedo'], (0.0, 0.423095703125, 1.0)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertEqual(tuple(result['F0']), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(result['emission'], 19.996687600260444, places=6)
        self.assertEqual(paths, [GRAPH])

    def test_other_resource_free_writers_stay_unbound(self):
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import RESOURCE_FREE_WRITERS, prepare_translation
        self.assertEqual({row[1] for row in RESOURCE_FREE_WRITERS}, {'0x368cca2e'})
        # Resource-free placeholder writers 0x11408de9 and 0xd88e357b in another graph stay unbound.
        graph = asset_bytes(self.archive, 'materialgraph/environment/deplanetizer/gameplay/dep_gp_laser_beam_01/dep_gp_laser_beam_01.materialgraph')
        material_data = asset_bytes(self.archive, MATERIAL)
        translation = prepare_translation([graph], material_data, extract_material_parameters(material_data))
        self.assertNotEqual(translation['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
