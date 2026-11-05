"""Explosive crate: exact light-writer source preview and the not-drawn additive glass."""
import hashlib
import os
from pathlib import Path
import unittest

from rc2016.shaders.bindings import (
    EXPLOSIVE_CRATE_LIGHT_SOURCE_PREVIEW as RULE,
    SOURCE_PREVIEW_POLICIES,
    _source_preview_policy,
)

# Code hash of the sibling writer 0x03319265 in the same graph. It must never receive the policy.
SIBLING_WRITER_CODE_SHA256_PREFIX = 'cd2ceaf3'


class ExplosiveCrateLightGuardTests(unittest.TestCase):
    def test_exact_graph_material_writer_and_code_are_required(self):
        program = {'shader_id': RULE['shader_id'], 'code_sha256': RULE['code_sha256']}
        self.assertIsNotNone(_source_preview_policy([RULE['graph_path'].upper()], RULE['graph_sha256'],
            RULE['material_path'].upper(), RULE['material_sha256'], program))
        self.assertIs(SOURCE_PREVIEW_POLICIES[RULE['graph_path']], RULE)
        for paths, graph_sha, material_path, material_sha, shader in [
            (['materialgraph/equipment/pickup/other/other.materialgraph'], RULE['graph_sha256'], RULE['material_path'], RULE['material_sha256'], program),
            ([RULE['graph_path']], '0' * 64, RULE['material_path'], RULE['material_sha256'], program),
            ([RULE['graph_path']], RULE['graph_sha256'], 'material/equipment/pickup/other.material', RULE['material_sha256'], program),
            ([RULE['graph_path']], RULE['graph_sha256'], RULE['material_path'], '0' * 64, program),
            ([RULE['graph_path']], RULE['graph_sha256'], RULE['material_path'], RULE['material_sha256'], dict(program, shader_id='0x03319265')),
            ([RULE['graph_path']], RULE['graph_sha256'], RULE['material_path'], RULE['material_sha256'], dict(program, code_sha256='0' * 64)),
        ]:
            with self.subTest(graph=paths, material=material_path, shader=shader['shader_id']):
                self.assertIsNone(_source_preview_policy(paths, graph_sha, material_path, material_sha, shader))

    def test_policy_prefers_the_writer_and_drops_only_the_tangent_normal(self):
        self.assertTrue(RULE['preferred'])
        self.assertEqual(RULE['normal'], 'geometry')
        self.assertEqual(RULE['input']['dword'], 99)
        self.assertEqual(RULE['input']['value'], 0.0)
        self.assertFalse(RULE['code_sha256'].startswith(SIBLING_WRITER_CODE_SHA256_PREFIX))


class ExplosiveCrateArchiveTests(unittest.TestCase):
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

    def test_light_writer_is_ready_and_reads_the_emblem(self):
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import prepare_translation
        graph_data = asset_bytes(self.archive, RULE['graph_path'])
        material_data = asset_bytes(self.archive, RULE['material_path'])
        self.assertEqual(hashlib.sha256(bytes(graph_data)).hexdigest(), RULE['graph_sha256'])
        self.assertEqual(hashlib.sha256(bytes(material_data)).hexdigest(), RULE['material_sha256'])
        graph_paths = [r['path'] for r in references(material_data) if r['path'].endswith('.materialgraph')]
        translation = prepare_translation([graph_data], material_data, extract_material_parameters(material_data),
            source_graph_paths=graph_paths, source_material_path=RULE['material_path'])
        self.assertEqual(translation['status'], 'ready')
        self.assertEqual(translation['shader_id'], RULE['shader_id'])
        self.assertEqual(translation['code_sha256'], RULE['code_sha256'])
        self.assertFalse(translation['game_equivalence'])
        self.assertTrue(translation['source_preview_inputs'])
        self.assertTrue(any(b['path'].endswith('_g.texture') for b in translation['texture_bindings']))
        context_free = prepare_translation([graph_data], material_data, extract_material_parameters(material_data))
        self.assertNotIn('source_preview_inputs', context_free)

    def test_additive_glass_is_not_drawn(self):
        from rc2016.viewer.textures import material_texture_paths
        glass = material_texture_paths(self.archive, 'material/equipment/pickup/pup_rcps4_explosive_crate_glass/pup_rcps4_explosive_crate_glass.material')
        self.assertIn('not_drawn', glass)
        self.assertEqual(glass['not_drawn']['writer'], '98B54ECA')
        light = material_texture_paths(self.archive, RULE['material_path'])
        self.assertNotIn('not_drawn', light)

    def test_split_forward_skips_not_drawn_batches(self):
        from rc2016.viewer.viewport import split_forward
        batches = [('a',), ('b',), ('c',)]
        textures = {'a': {'not_drawn': {'writer': 'x'}}, 'b': {}, 'c': {'forward': {'kind': 'screen'}}}
        opaque, forward = split_forward(batches, textures)
        self.assertEqual(opaque, [('b',)])
        self.assertEqual(forward, [('c',)])


if __name__ == '__main__':
    unittest.main()
