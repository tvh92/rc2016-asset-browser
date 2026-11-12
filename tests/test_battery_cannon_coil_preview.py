"""Exact source guard and archive-backed check of the battery cannon coil preview (docs/material-decoding/battery-cannon-coil.md)."""
import hashlib
import math
import os
from pathlib import Path
import unittest

from rc2016.shaders.bindings import (
    BATTERY_CANNON_COIL_SOURCE_PREVIEW as RULE,
    SOURCE_PREVIEW_POLICIES,
    _extra_input_receipts,
    _source_preview_policy,
    validate_source_preview_inputs,
)

TIME_MEANING = 'time/phase-like source input; runtime upload/value unverified'


def expected_surface(s):
    """The decoded writer at K = CB2[0] = 0 (graph default)."""
    h, a = 0.3 * s - 0.02, 0.5 + 0.5 * s
    return dict(albedo=(h, h + 0.5520114302635193 * a, h + a), F0=(0.199951171875, 0.199951171875, 0.20000000298023224),
                gloss=math.sqrt(.9), emission=3 * h + 2)


class SourceGuardTests(unittest.TestCase):
    def test_exact_graph_material_writer_and_code_are_required(self):
        program = {'shader_id': RULE['shader_id'], 'code_sha256': RULE['code_sha256']}
        self.assertIs(SOURCE_PREVIEW_POLICIES[RULE['graph_path']], RULE)
        args = ([RULE['graph_path']], RULE['graph_sha256'], RULE['material_path'], RULE['material_sha256'], program)
        self.assertIsNotNone(_source_preview_policy(*args))
        for index, value in ((0, ['materialgraph/other/other.materialgraph']), (1, '0' * 64),
                             (2, 'material/other/other.material'), (3, '0' * 64),
                             (4, dict(program, shader_id='0x8f221e6e')), (4, dict(program, code_sha256='0' * 64)),
                             (0, [RULE['graph_path'], 'materialgraph/other/other.materialgraph'])):
            changed = list(args)
            changed[index] = value
            self.assertIsNone(_source_preview_policy(*changed))

    def test_frozen_values_are_the_audited_ones(self):
        frozen = {e['name']: e['value'] for e in (RULE['input'], *RULE['extra_inputs'])}
        self.assertEqual(frozen, {'CB0[99]': 0.0, 'CB0[139]': 0.0})

    def test_persisted_receipt_is_exact(self):
        primary = dict(name='CB0[99]', kind='cb', api=0, dword=99, value=0.0,
                       graph_path=RULE['graph_path'], graph_sha256=RULE['graph_sha256'],
                       material_path=RULE['material_path'], material_sha256=RULE['material_sha256'],
                       shader_id=RULE['shader_id'], code_sha256=RULE['code_sha256'], meaning=TIME_MEANING)
        rows = [primary, *_extra_input_receipts(RULE)]
        translation = dict(source_preview_inputs=rows, shader_id=RULE['shader_id'],
                           code_sha256=RULE['code_sha256'], graph_sha256=RULE['graph_sha256'])
        self.assertTrue(validate_source_preview_inputs(translation))
        for bad in (rows[:1], [dict(primary, value=1.0), *rows[1:]], [primary, dict(rows[1], value=1.0)],
                    [primary, dict(rows[1], meaning='decoded game value')]):
            with self.assertRaisesRegex(ValueError, 'exact graph, material and writer'):
                validate_source_preview_inputs(dict(translation, source_preview_inputs=bad))


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

    def test_coil_writer_is_ready_and_cyan(self):
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import prepare_translation
        from rc2016.viewer.native_glsl import evaluate_surface
        graph_data = asset_bytes(self.archive, RULE['graph_path'])
        material_data = asset_bytes(self.archive, RULE['material_path'])
        self.assertEqual(hashlib.sha256(bytes(graph_data)).hexdigest(), RULE['graph_sha256'])
        self.assertEqual(hashlib.sha256(bytes(material_data)).hexdigest(), RULE['material_sha256'])
        graph_paths = [r['path'] for r in references(material_data) if r['path'].endswith('.materialgraph')]
        self.assertEqual(graph_paths, [RULE['graph_path']])
        params = extract_material_parameters(material_data)
        translation = prepare_translation([graph_data], material_data, params,
                                          source_graph_paths=graph_paths, source_material_path=RULE['material_path'])
        self.assertEqual(translation['status'], 'ready')
        self.assertEqual((translation['shader_id'], translation['code_sha256']), (RULE['shader_id'], RULE['code_sha256']))
        self.assertFalse(translation['game_equivalence'])
        self.assertEqual([(b['api'], b['path']) for b in translation['texture_bindings']],
                         [(5, 'textures/visualeffect/noise/cloud_noise/cloud_noise_01.texture')])
        names = {n['name'] for n in translation['nodes'] if n['op'] == 'input' and n.get('kind') != 'cb'}
        self.assertLessEqual(names, {'attribute0.0', 'attribute0.1'})
        self.assertFalse(any(n['op'] == 'input' and n.get('kind') == 'cb' and n.get('api') == 0 for n in translation['nodes']))
        limits = ' '.join(translation['limitations'])
        self.assertIn('CB0[99] is frozen at 0', limits)
        self.assertIn('CB0[139] is frozen at 0', limits)
        for s in (0.0, 0.3, 0.7):
            result = evaluate_surface(translation, (.25, .75), lambda api, uv: (s, s, s, s))
            for key, value in expected_surface(s).items():
                got = result[key]
                for g, v in zip(got, value) if isinstance(value, tuple) else ((got, value),):
                    self.assertAlmostEqual(g, v, places=5, msg=f'{key} at s={s}')
            red, green, blue = result['albedo']
            self.assertGreater(blue, green)
            self.assertGreater(green, 3 * max(red, 0.0))
        context_free = prepare_translation([graph_data], material_data, params)
        self.assertEqual(context_free['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
