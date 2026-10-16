"""Exact source guards and archive-backed checks of the weapon and gadget writers previewed with CB0[99] frozen at 0."""
import hashlib
import os
from pathlib import Path
import unittest

from rc2016.shaders.bindings import (
    MAG_BOOT_FAN_SOURCE_PREVIEW,
    PIXELIZER_BARREL_SOURCE_PREVIEW,
    PLASMA_STRIKER_SCOPE_SOURCE_PREVIEW,
    SOURCE_PREVIEW_POLICIES,
    TRESPASSER_SCREEN_SOURCE_PREVIEW,
    _source_preview_policy,
    validate_source_preview_inputs,
)

# rule, pass contract of the bound writer, texture APIs, surface evaluated with (R, G, B, A) = (.25, .5, .75, .8) at every sample
CASES = (
    (TRESPASSER_SCREEN_SOURCE_PREVIEW, 0, (5, 6, 7, 8, 9, 10), dict(F0=(.5, .5, .5), gloss=.5)),
    (PIXELIZER_BARREL_SOURCE_PREVIEW, 0, (5, 6, 7, 8, 9, 10),
     dict(albedo=(0.040925, 0.458146, 0.0), F0=(0.0, 0.0, 0.0), gloss=0.0, emission=2.9986464)),
    (PLASMA_STRIKER_SCOPE_SOURCE_PREVIEW, 0, (5, 6),
     dict(albedo=(0.0, 0.079536, 0.113564), F0=(0.0, 0.0, 0.0), gloss=0.0, emission=10.0000016)),
    (MAG_BOOT_FAN_SOURCE_PREVIEW, 1, (5, 6),
     dict(albedo=(.25, .5, .75), F0=(.5, .5, .5), gloss=.5, emission=0.0)),
)


class FrozenTimeSourceGuardTests(unittest.TestCase):
    def test_exact_graph_material_writer_and_code_are_required(self):
        for rule, _, _, _ in CASES:
            with self.subTest(rule=rule['label']):
                program = {'shader_id': rule['shader_id'], 'code_sha256': rule['code_sha256']}
                self.assertIs(SOURCE_PREVIEW_POLICIES[rule['graph_path']], rule)
                self.assertIsNotNone(_source_preview_policy([rule['graph_path'].upper()], rule['graph_sha256'],
                                                            rule['material_path'].upper(), rule['material_sha256'], program))
                for paths, graph_sha, material_path, material_sha, shader in (
                        (['materialgraph/other/other.materialgraph'], rule['graph_sha256'], rule['material_path'], rule['material_sha256'], program),
                        ([rule['graph_path']], '0' * 64, rule['material_path'], rule['material_sha256'], program),
                        ([rule['graph_path']], rule['graph_sha256'], 'material/other/other.material', rule['material_sha256'], program),
                        ([rule['graph_path']], rule['graph_sha256'], rule['material_path'], '0' * 64, program),
                        ([rule['graph_path']], rule['graph_sha256'], rule['material_path'], rule['material_sha256'], dict(program, shader_id='0x315a8578')),
                        ([rule['graph_path']], rule['graph_sha256'], rule['material_path'], rule['material_sha256'], dict(program, code_sha256='0' * 64)),
                        ([rule['graph_path'], 'materialgraph/other/other.materialgraph'], rule['graph_sha256'], rule['material_path'], rule['material_sha256'], program)):
                    self.assertIsNone(_source_preview_policy(paths, graph_sha, material_path, material_sha, shader))

    def test_persisted_preview_receipt_is_exact(self):
        for rule, _, _, _ in CASES:
            with self.subTest(rule=rule['label']):
                receipt = dict(name='CB0[99]', kind='cb', api=0, dword=99, value=0.0,
                               graph_path=rule['graph_path'], graph_sha256=rule['graph_sha256'],
                               material_path=rule['material_path'], material_sha256=rule['material_sha256'],
                               shader_id=rule['shader_id'], code_sha256=rule['code_sha256'],
                               meaning='time/phase-like source input; runtime upload/value unverified')
                translation = dict(source_preview_inputs=[receipt], shader_id=rule['shader_id'],
                                   code_sha256=rule['code_sha256'], graph_sha256=rule['graph_sha256'])
                self.assertTrue(validate_source_preview_inputs(translation))
                for bad in (dict(receipt, value=1.0), dict(receipt, material_sha256='0' * 64)):
                    with self.assertRaisesRegex(ValueError, 'exact graph, material and writer'):
                        validate_source_preview_inputs(dict(translation, source_preview_inputs=[bad]))
                with self.assertRaisesRegex(ValueError, 'exact graph, material and writer'):
                    validate_source_preview_inputs(dict(translation, shader_id='0x315a8578'))


class FrozenTimeArchiveTests(unittest.TestCase):
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

    def test_exact_source_writers_are_ready_with_frozen_input(self):
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
                self.assertFalse(any(n['op'] == 'input' and n.get('kind') == 'cb' for n in translation['nodes']))
                receipt = translation['source_preview_inputs'][0]
                self.assertEqual((receipt['name'], receipt['kind'], receipt['api'], receipt['dword'], receipt['value']),
                                 ('CB0[99]', 'cb', 0, 99, 0.0))
                self.assertTrue(validate_source_preview_inputs(translation))
                self.assertTrue(any('CB0[99] is frozen at 0' in s and 'runtime value are unverified' in s
                                    for s in translation['limitations']))
                result = evaluate_surface(translation, (.25, .75), lambda api, uv: (.25, .5, .75, .8))
                for key, value in expected.items():
                    got = result[key]
                    if isinstance(value, tuple):
                        for g, v in zip(got, value):
                            self.assertAlmostEqual(g, v, places=5, msg=key)
                    else:
                        self.assertAlmostEqual(got, value, places=5, msg=key)
                self.assertEqual(len(result['albedo']), 3)
                context_free = prepare_translation([graph_data], material_data, params)
                self.assertEqual(context_free['status'], 'unsupported')
                self.assertNotIn('source_preview_inputs', context_free)


if __name__ == '__main__':
    unittest.main()
