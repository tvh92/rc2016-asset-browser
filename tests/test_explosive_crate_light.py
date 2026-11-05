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

    def test_additive_glass_is_the_decoded_forward_program(self):
        from rc2016.viewer.textures import material_texture_paths
        glass = material_texture_paths(self.archive, 'material/equipment/pickup/pup_rcps4_explosive_crate_glass/pup_rcps4_explosive_crate_glass.material')
        forward = glass['forward']
        self.assertEqual((forward['kind'], forward['writer'], forward['blend']), ('glass_patterned_add', '90542658', 'additive'))
        self.assertFalse(forward['two_sided'])
        p = forward['params']
        self.assertEqual(tuple(round(x, 6) for x in p['tint']), (1.0, 0.099899, 0.0))
        self.assertEqual(tuple(round(x, 6) for x in p['body']), (1.0, 0.184475, 0.0))
        for name, expected in (('rim_bias', 1.0), ('tiling', 0.5), ('normal_strength', 0.2), ('gloss', 0.8),
                               ('rim_exp', 5.0), ('f0_base', 0.5)):
            self.assertAlmostEqual(p[name], expected, places=6, msg=name)
        self.assertEqual((p['amplitude'], tuple(p['glow_colour'])), (0.0, (0.0, 0.0, 0.0)))
        self.assertTrue(glass.get('_n', '').endswith('patterns_cma.texture'))
        self.assertNotIn('_pick', glass)
        self.assertNotIn('forward', material_texture_paths(self.archive, RULE['material_path']))

    def test_glass_uniforms_are_the_thumper_packing_without_noise_or_glow(self):
        from rc2016.viewer.shading import GLASS_KINDS, glass_uniforms
        from rc2016.viewer.textures import material_texture_paths
        forward = material_texture_paths(self.archive, 'material/equipment/pickup/pup_rcps4_explosive_crate_glass/pup_rcps4_explosive_crate_glass.material')['forward']
        kind, a, b, c, d, e = glass_uniforms(forward, 0.0)
        self.assertEqual(kind, GLASS_KINDS['glass_thumper'])
        self.assertEqual(len(a), 4); self.assertEqual(d, (0.0,) * 4)
        self.assertAlmostEqual(c[0], 0.5); self.assertAlmostEqual(c[3], 5.0)

    def test_opaque_material_of_the_same_graph_keeps_its_translation(self):
        from rc2016.formats.links import GENERIC, NATIVE, rule_coverage
        from rc2016.viewer.native_glsl import selected_translation
        path = 'material/character/enemy/enm_extermitank_glass/enm_extermitank_glass.material'
        from rc2016.viewer.textures import material_texture_paths
        self.assertNotEqual((material_texture_paths(self.archive, path).get('forward') or {}).get('kind'), 'glass_patterned_add')
        translation = selected_translation(self.archive, path)
        self.assertIsNotNone(translation)   # the gated rule must not make the graph an exact match that beats it
        self.assertEqual(rule_coverage(['materialgraph/global/basic_glass_patterned/basic_glass_patterned.materialgraph'],
                                       translation)[0], NATIVE)
        self.assertEqual(rule_coverage(['materialgraph/global/basic_glass_patterned/basic_glass_patterned.materialgraph'])[0],
                         GENERIC)


if __name__ == '__main__':
    unittest.main()
