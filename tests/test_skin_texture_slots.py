"""Skin previews use the same verified slot roles as the pipeline."""
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.test_viewer_textures import FakeArchive, dat1, slot_rows
from rc2016.viewer import textures as tp
from rc2016.shaders.families import FAMILY_REGISTRY, SKIN_TEXTURE_SLOTS


ARCHIVE = Path(__file__).resolve().parents[1] / 'Research' / '2026-10-22_moved-from-PS4-models-Animation-pipeline-Research'
class SkinSlotTests(unittest.TestCase):
    GRAPH = 'materialgraph/global/basic_skin/basic_skin.materialgraph'

    def fixture(self, instance=True):
        graph = dat1(['g/default_c.texture', 'g/default_g.texture', 'g/default_n.texture',
                      'g/detail_n.texture'], lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows([
                          (at[0], 0x49A7866B0056CBD0), (at[1], 0x49A7866B93CA35AB),
                          (at[2], 0x49A7866B5E5B04FB), (at[3], 0x49A7866B7DF1B1FC)]))])
        paths = [self.GRAPH, 'old/swarmer_c.texture', 'old/swarmer_g.texture',
                 'old/swarmer_n.texture', 'real/albedo.texture', 'real/spec.texture',
                 'real/normal.texture', 'real/detail_n.texture']
        rows = [(1, 0x49A7866BABDAD780), (2, 0x49A7866B1576D848), (3, 0x49A7866B7AE34E7A)]
        if instance:
            rows += [(4, 0x49A7866B0056CBD0), (5, 0x49A7866B93CA35AB),
                     (6, 0x49A7866B5E5B04FB), (7, 0x49A7866B7DF1B1FC)]
        material = dat1(paths, lambda at: [(tp.MATERIAL_SLOT_SECTION,
                                            slot_rows([(at[i], slot) for i, slot in rows]))])
        archive = FakeArchive({self.GRAPH: ('materialgraph', graph), 'm.material': ('material', material)})
        digest = hashlib.sha256(b'1TAD' + graph).hexdigest()
        return archive, {(self.GRAPH, digest): dict(family='basic_skin', texture_slots=SKIN_TEXTURE_SLOTS)}

    def test_verified_slots_ignore_stale_suffixes_and_secondary_normal(self):
        archive, registry = self.fixture()
        with patch.dict(FAMILY_REGISTRY, registry):
            got = tp.material_texture_paths(archive, 'm.material')
        self.assertEqual([got[r] for r in ('_c', '_g', '_n')],
                         ['real/albedo.texture', 'real/spec.texture', 'real/normal.texture'])

    def test_graph_defaults_win_when_only_stale_instance_slots_exist(self):
        archive, registry = self.fixture(instance=False)
        with patch.dict(FAMILY_REGISTRY, registry):
            got = tp.material_texture_paths(archive, 'm.material')
        self.assertEqual([got[r] for r in ('_c', '_g', '_n')],
                         ['g/default_c.texture', 'g/default_g.texture', 'g/default_n.texture'])

    def test_unregistered_graph_identity_does_not_inherit_verified_skin_roles(self):
        archive, registry = self.fixture()
        # An unknown hash keeps the existing labelled family fallback.
        got = tp.material_texture_paths(archive, 'm.material')
        self.assertEqual(got['_c'], 'old/swarmer_c.texture')
        self.assertEqual(got['_n'], 'old/swarmer_n.texture')

    @unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'requires research workspace')
    def test_live_pufferfish_model_and_level_material_loaders_agree(self):
        from rc2016.research.source import Corpus
        from rc2016.levels.scene import material_entries
        evidence = ARCHIVE / 'levels/Actors/out/corpus_validation.json'
        if not evidence.is_file():
            self.skipTest('actor source-root receipt unavailable')
        root = json.loads(evidence.read_text())['root']
        material = 'material/character/enemy/enm_sandshark/enm_puffer_fish.material'
        with Corpus(root, ARCHIVE / 'levels/Actors/out', extensions=('.zone',)).opened() as c:
            got = tp.material_texture_paths(c.archive, material)
            expected = {r: 'textures/character/enemy/enm_pufferfish/enm_pufferfish' + r + '.texture'
                        for r in ('_c', '_g', '_n')}
            self.assertEqual({r: got[r] for r in expected}, expected)
            def texture(_archive, path, hd=False):
                return dict(path=path, format=28, width=1, height=1, srgb=False, pixels=bytes(4))
            with patch.object(tp, 'load_texture', side_effect=texture):
                model = tp.material_textures(c.archive, [dict(index=0, path=material)], {})[0]
                level = material_entries(c.archive, [dict(path=material)], set(), {})[material]
            for role, path in expected.items():
                self.assertEqual(model[role]['path'], path)
                self.assertEqual(level[role]['path'], path)


if __name__ == '__main__':
    unittest.main()
