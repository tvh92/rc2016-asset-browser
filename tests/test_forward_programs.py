"""Forward pixel programs of the Ratchet character items (archive-backed, read-only).

Pins the facts of docs/material-decoding/ratchet-forward-passes.md: which materials reach which graph
and forward program, what each forward program reads from the resource table, and that the fur-shell
materials have no graph. Skips without RC2016_RESEARCH / RC2016_GAME_ROOT.
"""
import hashlib
import os
import unittest
from pathlib import Path

from rc2016.research.forward_programs import forward_resource_usage, split_material_apis

BASIC_SKIN = 'materialgraph/global/basic_skin/basic_skin.materialgraph'
NEWSSS = 'materialgraph/global/basic_skin/test_basic_skin_05_newsss_03.materialgraph'
CORNEA = 'materialgraph/character/hero/hero_eye_cornea/hero_eye_cornea.materialgraph'
FACE_PARTS = 'material/character/hero/hero_ratchet_face_parts/hero_ratchet_face_parts.material'
BODY = 'material/character/hero/hero_ratchet_body/hero_ratchet_body.material'
HEAD = 'material/character/hero/hero_ratchet_head/hero_ratchet_head.material'
FUR = ('material/character/hero/test/test_hero_ratchet_head_fur/test_hero_ratchet_head_fur.material',
       'material/character/hero/test/test_hero_ratchet_head_fur/test_hero_ratchet_head_fur_ear_1.material',
       'material/character/hero/test/test_hero_ratchet_head_fur/test_hero_ratchet_head_fur_ear_2.material')
# Pass-owned texture APIs read by every forward program: 3 and 4 (immediate offsets) and the literal-offset block.
PASS_TEXTURES = [3, 4, 32, 33, 34, 35, 36, 37, 43]


class ResourceSplitTests(unittest.TestCase):
    def test_material_and_pass_textures_are_split_at_the_contract_boundary(self):
        usage = dict(texture_apis={3: [], 5: [], 13: [], 32: [], 43: []})
        self.assertEqual(split_material_apis(usage), ([5, 13], [3, 32, 43]))


class ForwardProgramArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_RESEARCH')
        if not research:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        root = Path(os.environ.get('RC2016_GAME_ROOT', research))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(root)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def data(self, path):
        from rc2016.levels.scene import asset_bytes
        return asset_bytes(self.archive, path)

    def graphs_of(self, material_path):
        from rc2016.formats.index import references
        return [r['path'] for r in references(self.data(material_path)) if r['path'].endswith('.materialgraph')]

    def shader_ids(self, graph_path):
        from rc2016.shaders.translator import pixel_programs
        return [p['shader_id'] for p in pixel_programs(self.data(graph_path))]

    def test_f0cd85ff_belongs_to_basic_skin_only_not_to_the_ratchet_body_or_head(self):
        self.assertIn('0xf0cd85ff', self.shader_ids(BASIC_SKIN))
        self.assertEqual(hashlib.sha256(self.data(BASIC_SKIN)).hexdigest(),
                         '94922c9be24a9e893841ef0641a071c9ca40cbb46a335a3a49f2bd220579618c')
        for material in (BODY, HEAD):
            with self.subTest(material=material):
                graphs = self.graphs_of(material)
                self.assertEqual(len(graphs), 1)
                self.assertNotIn('0xf0cd85ff', self.shader_ids(graphs[0]))

    def test_face_parts_has_forward_programs_and_only_the_zero_output_deferred_writer(self):
        self.assertEqual(self.graphs_of(FACE_PARTS), [NEWSSS])
        self.assertEqual(hashlib.sha256(self.data(NEWSSS)).hexdigest(),
                         '8621d1220b5c9e736f47985600d57b98fa31fca55b8c4c28e22b6b4bb797ac5d')
        self.assertEqual(self.shader_ids(NEWSSS),
                         ['0x921420cd', '0x11408de9', '0x6c8f5a50', '0x54623cd4', '0x11408de9', '0x6c8f5a50'])

    def test_cornea_graph_pairs_each_deferred_placeholder_with_a_forward_program(self):
        self.assertEqual(self.shader_ids(CORNEA),
                         ['0xa36a97cb', '0x11408de9', '0xe3f59633', '0xae044d65', '0xf900d315', '0x55a74034'])

    def test_fur_shell_materials_name_no_graph_no_slots_no_parameters_and_carry_the_fur_flag(self):
        from rc2016.formats.index import references
        from rc2016.formats.materials import MATERIAL_TEXTURE_SLOTS, extract_material_parameters, texture_slot_records
        from rc2016.viewer.textures import FUR_SHELL_FLAG, material_flags
        for path in FUR:
            with self.subTest(material=path):
                data = self.data(path)
                self.assertEqual(self.graphs_of(path), [])
                self.assertTrue(material_flags(data) & FUR_SHELL_FLAG)
                self.assertEqual(len(data), 404)
                # The file carries only four texture references in this order; no slot-key table and no parameters.
                self.assertEqual([r['path'].rsplit('/', 1)[1] for r in references(data) if r['path'].endswith('.texture')],
                                 ['ratchet_head_c.texture', 'ratchet_head_n.texture', 'ratchet_head_fur_g.texture',
                                  'hero_ratchet_fur_control.texture'])
                try:
                    slots = texture_slot_records(data, MATERIAL_TEXTURE_SLOTS)
                except ValueError:
                    slots = []
                self.assertEqual(slots, [])
                parameters = extract_material_parameters(data)
                self.assertEqual(parameters['parameters'] + parameters.get('unsupported_parameters', []), [])

    def test_forward_resource_tables(self):
        # img index order of the SPIR-V is first use; the contract API comes from the descriptor load offset.
        expected = {
            (BASIC_SKIN, '0xf0cd85ff'): ([5, 6, 7, 10, 11], [3, 4, 33, 34, 35, 36, 37, 43]),
            (NEWSSS, '0x921420cd'): ([5, 6, 7, 8, 9, 10, 11, 12, 13], PASS_TEXTURES),
            (NEWSSS, '0x54623cd4'): ([5, 6, 7, 9, 10, 11, 12, 13], PASS_TEXTURES),   # does not sample API 8
            (CORNEA, '0xa36a97cb'): ([5, 6], [3, 4, 32, 33, 34, 35, 36, 37, 41, 42, 43]),
            (CORNEA, '0xae044d65'): ([], [3, 4, 32, 33, 34, 35, 36, 37, 43]),
        }
        for (graph, shader), (material_apis, pass_apis) in expected.items():
            with self.subTest(graph=graph, shader=shader):
                usage = forward_resource_usage(self.data(graph), shader)
                self.assertEqual(usage['unresolved'], 0)
                self.assertEqual(split_material_apis(usage), (material_apis, pass_apis))

    def test_face_parts_material_slots_versus_the_contract(self):
        # API 7 (sss normal) and API 8 (sss map) are bound by default_pink_shader textures; API 13 is a graph default.
        from rc2016.formats.materials import GRAPH_TEXTURE_SLOTS, MATERIAL_TEXTURE_SLOTS, texture_slot_records
        from rc2016.shaders.bindings import pass_contracts
        contract = pass_contracts(self.data(NEWSSS))[0]
        material = {r['slot_key']: r['path'] for r in texture_slot_records(self.data(FACE_PARTS), MATERIAL_TEXTURE_SLOTS)}
        by_api = {5 + i: (r['slot_key'], r['flag'], material.get(r['slot_key'])) for i, r in enumerate(contract['resources'])}
        self.assertEqual(by_api[5][2], 'textures/character/hero/ratchet/ratchet_eye_g.texture')
        self.assertEqual(by_api[6][2], 'textures/character/hero/ratchet/ratchet_eye_n.texture')
        self.assertEqual(by_api[7][2], 'textures/default_pink_shader/default_pink_shader_n.texture')
        self.assertEqual(by_api[8][2], 'textures/default_pink_shader/default_pink_shader_c.texture')
        self.assertEqual(by_api[12][2], 'textures/character/hero/ratchet/ratchet_eye_c.texture')
        for api in (9, 10, 11, 13):
            self.assertIsNone(by_api[api][2], api)   # not overridden: the graph default texture applies
        self.assertTrue(GRAPH_TEXTURE_SLOTS)


if __name__ == '__main__':
    unittest.main()
