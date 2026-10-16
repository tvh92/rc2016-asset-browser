"""Exact source guards and archive-backed checks of the gauge needle and Map-o-matic screen previews."""
import hashlib
import math
import os
from pathlib import Path
import unittest

from rc2016.shaders.bindings import (
    GAUGE_NEEDLE_SOURCE_PREVIEW,
    MAP_O_MATIC_SCREEN_SOURCE_PREVIEW,
    SOURCE_PREVIEW_POLICIES,
    _extra_input_receipts,
    _source_preview_policy,
    validate_source_preview_inputs,
)

TIME_MEANING = 'time/phase-like source input; runtime upload/value unverified'
RULES = (GAUGE_NEEDLE_SOURCE_PREVIEW, MAP_O_MATIC_SCREEN_SOURCE_PREVIEW)

# Texture sample (R, G, B, A) = (.25, .5, .75, .8) at every sample, UV0 = (.25, .75).
# Map-o-matic X = 0.025 + .25 B6 + .963 G5 + .694 clamp(|UV0 - .5|) + .519 B5 + .485 R5 + A5 (decoded weights).
MAP_X = 0.025 + .25 * .75 + .963 * .5 + .694 * math.hypot(-.25, .25) + .519 * .75 + .485 * .25 + .8
# rule, pass contract, texture APIs, expected surface
CASES = (
    (GAUGE_NEEDLE_SOURCE_PREVIEW, 0, (5,),
     dict(albedo=(.25, .5, .75), F0=(0.0999755859375, 0.0999755859375, 0.10000000149011612),
          gloss=math.sqrt(.9), emission=0.0)),
    (MAP_O_MATIC_SCREEN_SOURCE_PREVIEW, 0, (5, 6),
     dict(albedo=(0.0, 0.4969329833984375 * MAP_X, 0.6583748459815979 * MAP_X),
          F0=(0.239990234375, 0.239990234375, 0.23999999463558197), gloss=1.0, emission=2.0 * MAP_X)),
)


class SourceGuardTests(unittest.TestCase):
    def test_exact_graph_material_writer_and_code_are_required(self):
        for rule in RULES:
            with self.subTest(rule=rule['label']):
                program = {'shader_id': rule['shader_id'], 'code_sha256': rule['code_sha256']}
                self.assertIs(SOURCE_PREVIEW_POLICIES[rule['graph_path']], rule)
                self.assertIsNotNone(_source_preview_policy([rule['graph_path']], rule['graph_sha256'],
                                                            rule['material_path'], rule['material_sha256'], program))
                for paths, graph_sha, material_path, material_sha, shader in (
                        (['materialgraph/other/other.materialgraph'], rule['graph_sha256'], rule['material_path'], rule['material_sha256'], program),
                        ([rule['graph_path']], '0' * 64, rule['material_path'], rule['material_sha256'], program),
                        ([rule['graph_path']], rule['graph_sha256'], 'material/other/other.material', rule['material_sha256'], program),
                        ([rule['graph_path']], rule['graph_sha256'], rule['material_path'], '0' * 64, program),
                        ([rule['graph_path']], rule['graph_sha256'], rule['material_path'], rule['material_sha256'], dict(program, shader_id='0xa4b733e7')),
                        ([rule['graph_path']], rule['graph_sha256'], rule['material_path'], rule['material_sha256'], dict(program, code_sha256='0' * 64)),
                        ([rule['graph_path'], 'materialgraph/other/other.materialgraph'], rule['graph_sha256'], rule['material_path'], rule['material_sha256'], program)):
                    self.assertIsNone(_source_preview_policy(paths, graph_sha, material_path, material_sha, shader))

    def test_frozen_values_are_the_audited_ones(self):
        frozen = {r['label']: {e['name']: e['value'] for e in (r['input'], *r['extra_inputs'])} for r in RULES}
        self.assertEqual(frozen['Gauge needle'], {'CB0[99]': 0.0, 'CB0[139]': 0.0})
        self.assertEqual(frozen['Map-o-matic screen'], {'CB0[99]': 0.0, 'CB0[115]': 0.5, 'CB0[139]': 0.0})

    def test_persisted_receipt_is_exact_including_every_frozen_input(self):
        for rule in RULES:
            with self.subTest(rule=rule['label']):
                primary = dict(name='CB0[99]', kind='cb', api=0, dword=99, value=0.0,
                               graph_path=rule['graph_path'], graph_sha256=rule['graph_sha256'],
                               material_path=rule['material_path'], material_sha256=rule['material_sha256'],
                               shader_id=rule['shader_id'], code_sha256=rule['code_sha256'], meaning=TIME_MEANING)
                rows = [primary, *_extra_input_receipts(rule)]
                translation = dict(source_preview_inputs=rows, shader_id=rule['shader_id'],
                                   code_sha256=rule['code_sha256'], graph_sha256=rule['graph_sha256'])
                self.assertTrue(validate_source_preview_inputs(translation))
                bad_sets = [rows[:-1], rows[:1], [dict(primary, value=1.0), *rows[1:]]]
                for j in range(1, len(rows)):
                    edited = list(rows)
                    edited[j] = dict(rows[j], value=rows[j]['value'] + 1.0)
                    bad_sets.append(edited)
                    edited = list(rows)
                    edited[j] = dict(rows[j], meaning='decoded game value')
                    bad_sets.append(edited)
                for bad in bad_sets:
                    with self.assertRaisesRegex(ValueError, 'exact graph, material and writer'):
                        validate_source_preview_inputs(dict(translation, source_preview_inputs=bad))

    def test_flat_gloss_constant_is_the_translator_endpoint(self):
        # The folded writer constants 1 - W C with C = 0.99927789 and 0.99951172 give gloss at width W = 1.
        self.assertAlmostEqual(-math.log2(1 - 0.9992778897285461) / 11, math.sqrt(.9), places=5)
        self.assertAlmostEqual(-math.log2(1 - 0.99951171875) / 11, 1.0, places=5)


class ArchiveTests(unittest.TestCase):
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

    def test_exact_writers_are_ready_with_labelled_frozen_inputs(self):
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import prepare_translation
        from rc2016.viewer.native_glsl import evaluate_surface
        for rule, contract, apis, expected in CASES:
            with self.subTest(rule=rule['label']):
                graph_data = asset_bytes(self.archive, rule['graph_path'])
                material_data = asset_bytes(self.archive, rule['material_path'])
                self.assertEqual(hashlib.sha256(bytes(graph_data)).hexdigest(), rule['graph_sha256'])
                self.assertEqual(hashlib.sha256(bytes(material_data)).hexdigest(), rule['material_sha256'])
                graph_paths = [r['path'] for r in references(material_data) if r['path'].endswith('.materialgraph')]
                self.assertEqual(graph_paths, [rule['graph_path']])
                params = extract_material_parameters(material_data)
                translation = prepare_translation([graph_data], material_data, params,
                                                  source_graph_paths=graph_paths, source_material_path=rule['material_path'])
                self.assertEqual(translation['status'], 'ready')
                self.assertEqual(translation['shader_id'], rule['shader_id'])
                self.assertEqual(translation['code_sha256'], rule['code_sha256'])
                self.assertEqual(translation['pass_contract_index'], contract)
                self.assertFalse(translation['game_equivalence'])
                self.assertEqual([b['api'] for b in translation['texture_bindings']], list(apis))
                # Only the UV0 attributes survive: every other engine word and interpolant is gone.
                names = {n['name'] for n in translation['nodes'] if n['op'] == 'input' and n.get('kind') != 'cb'}
                self.assertLessEqual(names, {'attribute0.0', 'attribute0.1'})
                self.assertFalse(any(n['op'] == 'input' and n.get('kind') == 'cb' and n.get('api') == 0
                                     for n in translation['nodes']))
                self.assertTrue(validate_source_preview_inputs(translation))
                receipts = {r['name']: r['value'] for r in translation['source_preview_inputs']}
                self.assertEqual(receipts, {e['name']: e['value'] for e in (rule['input'], *rule['extra_inputs'])})
                limits = ' '.join(translation['limitations'])
                self.assertIn('CB0[99] is frozen at 0', limits)
                self.assertIn('CB0[139] is frozen at 0', limits)
                if rule is MAP_O_MATIC_SCREEN_SOURCE_PREVIEW:
                    self.assertIn('not a decoded game value', limits)
                result = evaluate_surface(translation, (.25, .75), lambda api, uv: (.25, .5, .75, .8))
                for key, value in expected.items():
                    got = result[key]
                    if isinstance(value, tuple):
                        for g, v in zip(got, value):
                            self.assertAlmostEqual(g, v, places=5, msg=key)
                    else:
                        self.assertAlmostEqual(got, value, places=5, msg=key)
                context_free = prepare_translation([graph_data], material_data, params)
                self.assertEqual(context_free['status'], 'unsupported')
                self.assertNotIn('source_preview_inputs', context_free)


if __name__ == '__main__':
    unittest.main()
