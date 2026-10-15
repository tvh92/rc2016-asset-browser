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

    def test_variant_key_is_separate_and_default_is_unchanged(self):
        import hashlib
        from rc2016.viewer import native_glsl
        key = self.disk.key(b'material', [b'graph'])
        self.assertEqual(key, self.disk.key(b'material', [b'graph'], variant=''))
        legacy = hashlib.sha256(repr((native_glsl.TRANSLATION_DISK_CACHE_VERSION, native_glsl._TRANSLATION_CODE_SHA256,
                                      hashlib.sha256(b'material').hexdigest(),
                                      [hashlib.sha256(b'graph').hexdigest()])).encode()).hexdigest()
        self.assertEqual(key, legacy)
        self.assertNotEqual(key, self.disk.key(b'material', [b'graph'], variant='world_position'))

    def test_unstorable_and_damaged_entries_are_ignored(self):
        key = self.disk.key(b'material', [b'graph'])
        self.disk.put(key, dict(status='ready', raw=b'bytes'))
        self.assertIsNone(self.disk.get(key))
        self.disk.put(key, dict(status='ready', value=float('nan')))
        self.assertIsNone(self.disk.get(key))
        self.disk.put(key, translation())
        self.disk._file(key).write_bytes(b'{not json')
        self.assertIsNone(self.disk.get(key))

    def test_stats_count_hits_and_misses(self):
        key = self.disk.key(b'material', [b'graph'])
        self.assertIsNone(self.disk.get(key))
        self.disk.put(key, translation())
        self.disk.get(key); self.disk.get(key)
        self.assertEqual(self.disk.stats(), dict(hits=2, misses=1))

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

    def test_world_position_translation_is_reused_across_sessions(self):
        from rc2016.viewer.experimental_materials import world_position_translation
        calls = []

        def prepare(graphs, material, instance, world_position=False):
            calls.append((graphs, material, world_position))
            return translation()
        with patch('rc2016.formats.archive.asset_data', side_effect=lambda archive, i: b'material' if i == 1 else b'graph'), \
             patch('rc2016.formats.archive.asset_references', return_value=[dict(path='g.materialgraph')]), \
             patch('rc2016.formats.materials.extract_material_parameters', return_value={}), \
             patch('rc2016.shaders.bindings.prepare_translation', side_effect=prepare):
            for _session in range(2):
                archive = Archive()
                archive.translation_disk_cache = self.disk
                self.assertEqual(world_position_translation(archive, 'm.material'), translation())
        self.assertEqual(calls, [([b'graph'], b'material', True)])


class TranslationCodeKeyTests(unittest.TestCase):
    def test_excluded_modules_are_not_imported_by_the_translator(self):
        """A module outside the code hash must not be reachable from prepare_translation."""
        import ast
        from rc2016.viewer.native_glsl import TRANSLATION_CODE_EXCLUDED
        root = Path(__file__).resolve().parents[1]
        shaders = root / 'rc2016' / 'shaders'
        seen, stack = set(), ['bindings']
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            for node in ast.walk(ast.parse((shaders / f'{name}.py').read_text(encoding='utf-8'))):
                if isinstance(node, ast.ImportFrom) and node.module == 'rc2016.shaders':
                    stack.extend(a.name for a in node.names if (shaders / f'{a.name}.py').exists())
                elif isinstance(node, ast.ImportFrom) and (node.module or '').startswith('rc2016.shaders.'):
                    stack.append(node.module.split('.')[2])
                elif isinstance(node, ast.Import):
                    stack.extend(a.name.split('.')[2] for a in node.names if a.name.startswith('rc2016.shaders.'))
        self.assertIn('translator', seen)
        self.assertEqual({f'{n}.py' for n in seen} & TRANSLATION_CODE_EXCLUDED, set())
        for name in TRANSLATION_CODE_EXCLUDED:
            self.assertTrue((shaders / name).exists(), name)

    def test_hash_ignores_the_excluded_modules_and_native_glsl(self):
        from rc2016.viewer import native_glsl
        self.assertIsNotNone(native_glsl._TRANSLATION_CODE_SHA256)
        sources = Path(native_glsl.__file__).resolve().parents[1]
        with patch.object(Path, 'read_bytes', autospec=True,
                          side_effect=lambda self: (_ for _ in ()).throw(AssertionError(self.name))
                          if self.name in native_glsl.TRANSLATION_CODE_EXCLUDED | {'native_glsl.py'} else b'x'):
            self.assertEqual(len(native_glsl._translation_code_sha256()), 64)
        self.assertTrue((sources / 'shaders' / 'translator.py').exists())


if __name__ == '__main__':
    unittest.main()
