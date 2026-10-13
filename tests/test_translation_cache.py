"""Native translations persist on disk across sessions and load back exactly."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2016.viewer.native_glsl import TranslationDiskCache, prepare_material_translation


def translation():
    return dict(status='ready', nodes=[dict(op='const', args=[], value=1.0), dict(op='input', args=[], channel=0)],
                outputs=dict(albedo=[0, 0, 0], gloss=1), normal=None, cb2={5: 0.5, 7: -0.0},
                sample=(1, 2), nested=[dict(span=(0, 4))], tag={'__tuple__': 'literal'},
                translation_sha256='x', game_equivalence=False)


class Archive:
    """Two .material/.materialgraph entries; a fresh instance has an empty in-memory cache."""
    entries = {1: dict(type='material'), 2: dict(type='materialgraph')}

    def resolve(self, path):
        return [1] if path.endswith('.material') else [2]


class TranslationDiskCacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.disk = TranslationDiskCache(Path(self.folder.name))

    def test_round_trip_keeps_tuples_and_non_string_keys(self):
        key = self.disk.key(b'material', [b'graph'])
        self.disk.put(key, translation())
        loaded = self.disk.get(key)
        self.assertEqual(loaded, translation())
        self.assertIsInstance(loaded['sample'], tuple)
        self.assertIsInstance(loaded['nested'][0]['span'], tuple)
        self.assertEqual(set(loaded['cb2']), {5, 7})
        self.assertEqual(loaded['tag'], {'__tuple__': 'literal'})

    def test_key_follows_material_and_graph_bytes(self):
        key = self.disk.key(b'material', [b'graph'])
        self.assertEqual(key, self.disk.key(b'material', [b'graph']))
        self.assertNotEqual(key, self.disk.key(b'material2', [b'graph']))
        self.assertNotEqual(key, self.disk.key(b'material', [b'graph2']))
        self.assertNotEqual(key, self.disk.key(b'material', [b'graph', b'graph']))

    def test_unstorable_and_damaged_entries_are_ignored(self):
        key = self.disk.key(b'material', [b'graph'])
        self.disk.put(key, dict(status='ready', raw=b'bytes'))
        self.assertIsNone(self.disk.get(key))
        self.disk.put(key, dict(status='ready', value=float('nan')))
        self.assertIsNone(self.disk.get(key))
        self.disk.put(key, translation())
        self.disk._file(key).write_bytes(b'{not json')
        self.assertIsNone(self.disk.get(key))

    def test_a_new_session_reuses_the_stored_translation(self):
        calls = []

        def prepare(graphs, material, instance):
            calls.append((graphs, material))
            return translation()
        with patch('rc2016.formats.archive.asset_data', side_effect=lambda archive, i: b'material' if i == 1 else b'graph'), \
             patch('rc2016.formats.archive.asset_references', return_value=[dict(path='g.materialgraph')]), \
             patch('rc2016.formats.materials.extract_material_parameters', return_value={}), \
             patch('rc2016.shaders.bindings.prepare_translation', side_effect=prepare):
            for _session in range(2):
                archive = Archive()
                archive.translation_disk_cache = self.disk
                self.assertEqual(prepare_material_translation(archive, 'm.material'), translation())
        self.assertEqual(calls, [([b'graph'], b'material')])


if __name__ == '__main__':
    unittest.main()
