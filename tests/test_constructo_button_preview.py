"""Exact source guard and archive-backed Constructor Bot button preview."""
import os
from pathlib import Path
import unittest

from rc2016.shaders.bindings import (
    CONSTRUCTO_BUTTON_SOURCE_PREVIEW as RULE,
    _source_preview_policy,
    validate_source_preview_inputs,
)


class SourcePreviewGuardTests(unittest.TestCase):
    def setUp(self):
        self.program={'shader_id':RULE['shader_id'],'code_sha256':RULE['code_sha256']}

    def test_exact_paths_case_alias_and_hashes_are_required(self):
        policy=_source_preview_policy([RULE['graph_path'].upper()],RULE['graph_sha256'],
            RULE['material_path'].upper(),RULE['material_sha256'],self.program)
        self.assertIsNotNone(policy)
        controls=[
            (['materialgraph/other/constructo.materialgraph'],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],self.program),
            ([RULE['graph_path']], '0'*64,RULE['material_path'],RULE['material_sha256'],self.program),
            ([RULE['graph_path']],RULE['graph_sha256'],'material/other/button.material',RULE['material_sha256'],self.program),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],'0'*64,self.program),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],dict(self.program,shader_id='0x00000000')),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],dict(self.program,code_sha256='0'*64)),
            ([RULE['graph_path'],RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],self.program),
            (['../untrusted.materialgraph'],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],self.program),
        ]
        for args in controls:
            with self.subTest(args=args):self.assertIsNone(_source_preview_policy(*args))

    def test_persisted_preview_receipt_is_exact_and_fails_closed_when_edited(self):
        receipt=dict(name='CB0[99]',kind='cb',api=0,dword=99,value=0.0,
            graph_path=RULE['graph_path'],graph_sha256=RULE['graph_sha256'],
            material_path=RULE['material_path'],material_sha256=RULE['material_sha256'],
            shader_id=RULE['shader_id'],code_sha256=RULE['code_sha256'],
            meaning='time/phase-like source input; runtime upload/value unverified',
            selector_key_hash='0x2A40709D',selector_value=1.0)
        translation=dict(source_preview_inputs=[receipt],shader_id=RULE['shader_id'],
            code_sha256=RULE['code_sha256'],graph_sha256=RULE['graph_sha256'])
        self.assertTrue(validate_source_preview_inputs(translation))
        for field,value in [('value',1.0),('material_path','material/other.material'),
                            ('selector_value',0.0),('shader_id','0x00000000')]:
            bad=dict(translation);bad['source_preview_inputs']=[dict(receipt,**{field:value})]
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'exact graph, material and writer'):
                validate_source_preview_inputs(bad)


class ConstructorButtonArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research=os.environ.get('RC2016_RESEARCH')
        if not research:raise unittest.SkipTest('RC2016_RESEARCH is required')
        root=Path(os.environ.get('RC2016_GAME_ROOT',str(Path(research).parents[1]/'Ratchet & Clank (EUR)'/'Image0')))
        if not root.exists():raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive=open_archive(root)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls,'archive'):cls.archive.close()

    def test_source_material_and_both_writer_contracts_translate_with_explicit_limits(self):
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import pass_contracts,prepare_translation
        from rc2016.viewer.native_glsl import evaluate_surface
        graph_path=RULE['graph_path'];material_path=RULE['material_path']
        graph_data=asset_bytes(self.archive,graph_path);material_data=asset_bytes(self.archive,material_path)
        graph_paths=[r['path'] for r in references(material_data) if r['path'].endswith('.materialgraph')]
        self.assertEqual(graph_paths,[graph_path])
        contracts=pass_contracts(graph_data)
        self.assertEqual([c['defaults'][RULE['selector']['key_hash']] for c in contracts],[1.0,1.0])
        translation=prepare_translation([graph_data],material_data,extract_material_parameters(material_data),
            source_graph_paths=graph_paths,source_material_path=material_path)
        self.assertEqual(translation['status'],'ready')
        self.assertEqual(translation['graph_sha256'],RULE['graph_sha256'])
        self.assertEqual(translation['code_sha256'],RULE['code_sha256'])
        self.assertEqual(translation['shader_id'],RULE['shader_id'])
        self.assertEqual(translation['equivalent_candidates'],2)
        self.assertEqual({row['pass_contract_index'] for row in translation['candidate_sources']},{0,1})
        self.assertEqual(translation['source_preview_inputs'][0]['value'],0.0)
        self.assertEqual([(row['api'],row['path']) for row in translation['texture_bindings']],
            [(5,'textures/character/enemy/enm_constructor_bot/enm_constructor_bot_cma.texture')])
        self.assertTrue(any('0x2A40709D' in item for item in translation['limitations']))
        self.assertIsNone(translation['normal'])
        self.assertFalse(translation['game_equivalence'])
        self.assertTrue(any('CB0[99]' in item and 'runtime' in item for item in translation['limitations']))
        self.assertTrue(any('MRT0 alpha' in item and 'not interpreted as opacity' in item for item in translation['limitations']))

        def constant_source_texture(api,uv):
            self.assertEqual(api,5)
            return (.25,.5,.75,.8)
        result=evaluate_surface(translation,(.25,.75),constant_source_texture)
        self.assertTrue(any(abs(value)>1e-6 for value in result['albedo']))
        self.assertNotEqual(result['emission'],0.0)
        self.assertEqual(result['F0'],[0.0,0.0,0.0])
        self.assertEqual(result['gloss'],0.0)
        from rc2016.viewer.native_glsl import selected_translation
        viewer=selected_translation(self.archive,material_path)
        self.assertEqual(viewer['status'],'ready')
        self.assertEqual(viewer['source_preview_inputs'],translation['source_preview_inputs'])

        blocked=prepare_translation([graph_data],material_data,extract_material_parameters(material_data),
            source_graph_paths=['materialgraph/other/button.materialgraph'],source_material_path=material_path)
        self.assertEqual(blocked['status'],'unsupported')
        context_free=prepare_translation([graph_data],material_data,extract_material_parameters(material_data))
        self.assertEqual(context_free['status'],'unsupported')
        self.assertNotIn('source_preview_inputs',context_free)


if __name__=='__main__':unittest.main()
