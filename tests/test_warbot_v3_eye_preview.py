"""Exact source guard and archive-backed Warbot V3 eye writer preview."""
import os
from pathlib import Path
import unittest

from rc2016.shaders.bindings import (
    WARBOT_V3_EYE_SOURCE_PREVIEW as RULE,
    _source_preview_policy,
    validate_source_preview_inputs,
)


class WarbotEyeSourceGuardTests(unittest.TestCase):
    def test_exact_graph_material_writer_and_raw_parameter_receipt_are_required(self):
        program={'shader_id':RULE['shader_id'],'code_sha256':RULE['code_sha256']}
        self.assertIsNotNone(_source_preview_policy([RULE['graph_path'].upper()],RULE['graph_sha256'],
            RULE['material_path'].upper(),RULE['material_sha256'],program))
        for paths,graph_sha,material_path,material_sha,shader in [
            (['materialgraph/character/enemy/other/other.materialgraph'],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],program),
            ([RULE['graph_path']],'0'*64,RULE['material_path'],RULE['material_sha256'],program),
            ([RULE['graph_path']],RULE['graph_sha256'],'material/character/enemy/other.material',RULE['material_sha256'],program),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],'0'*64,program),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],dict(program,shader_id='0x00000000')),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],dict(program,code_sha256='0'*64)),
        ]:
            with self.subTest(graph=paths,material=material_path):
                self.assertIsNone(_source_preview_policy(paths,graph_sha,material_path,material_sha,shader))

    def test_persisted_preview_receipt_rejects_changed_parameter_values(self):
        receipt=dict(name='CB0[99]',kind='cb',api=0,dword=99,value=0.0,
            graph_path=RULE['graph_path'],graph_sha256=RULE['graph_sha256'],
            material_path=RULE['material_path'],material_sha256=RULE['material_sha256'],
            shader_id=RULE['shader_id'],code_sha256=RULE['code_sha256'],
            meaning='time/phase-like source input; runtime upload/value unverified',
            parameter_receipts=[dict(key_hash='0x%08X'%p['key_hash'],dword=p['dword'],
                values=p['values'],source=p['source']) for p in RULE['parameters']])
        translation=dict(source_preview_inputs=[receipt],shader_id=RULE['shader_id'],
            code_sha256=RULE['code_sha256'],graph_sha256=RULE['graph_sha256'])
        self.assertTrue(validate_source_preview_inputs(translation))
        bad=dict(translation);bad['source_preview_inputs']=[dict(receipt,parameter_receipts=[
            dict(receipt['parameter_receipts'][0],values=[1.0,0.03310476616024971,0.0]),
            *receipt['parameter_receipts'][1:]])]
        with self.assertRaisesRegex(ValueError,'exact graph, material and writer'):
            validate_source_preview_inputs(bad)


class WarbotEyeArchiveTests(unittest.TestCase):
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

    def test_exact_source_writer_parameter_layout_and_bounded_preview(self):
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import pass_contracts,prepare_translation
        from rc2016.viewer.native_glsl import evaluate_surface,selected_translation
        graph_path=RULE['graph_path'];material_path=RULE['material_path']
        graph_data=asset_bytes(self.archive,graph_path);material_data=asset_bytes(self.archive,material_path)
        graph_paths=[r['path'] for r in references(material_data) if r['path'].endswith('.materialgraph')]
        self.assertEqual(graph_paths,[graph_path])
        contracts=pass_contracts(graph_data)
        self.assertEqual(len(contracts),2)
        self.assertEqual([[(e['key_hash'],e['dword'],e['size_bytes']) for e in c['table']] for c in contracts],
            [[(p['key_hash'],p['dword'],4*len(p['values'])) for p in RULE['parameters']]]*2)
        translation=prepare_translation([graph_data],material_data,extract_material_parameters(material_data),
            source_graph_paths=graph_paths,source_material_path=material_path)
        self.assertEqual(translation['status'],'ready')
        self.assertEqual(translation['graph_sha256'],RULE['graph_sha256'])
        self.assertEqual(translation['shader_id'],RULE['shader_id'])
        self.assertEqual(translation['code_sha256'],RULE['code_sha256'])
        self.assertEqual(translation['equivalent_candidates'],2)
        self.assertFalse(any(n['op'] in ('u_and','u_or') for n in translation['nodes']))
        self.assertFalse(any(n['op']=='input' and n.get('kind')=='cb' for n in translation['nodes']))
        self.assertEqual({c['pass_contract_index'] for c in translation['candidate_sources']},{0,1})
        self.assertEqual([(b['api'],b['path']) for b in translation['texture_bindings']],
            [(5,'textures/character/enemy/enm_warbot_v3/warbot_v3_scroll_eye_cma.texture')])
        self.assertEqual(translation['source_preview_inputs'][0]['value'],0.0)
        self.assertEqual(translation['source_preview_inputs'][0]['parameter_receipts'],[
            dict(key_hash='0x%08X'%p['key_hash'],dword=p['dword'],values=p['values'],source=p['source'])
            for p in RULE['parameters']])
        self.assertFalse(translation['game_equivalence'])
        self.assertIsNone(translation['normal'])
        self.assertTrue(any('runtime value are unverified' in s for s in translation['limitations']))
        self.assertTrue(any('runtime pass selection remain unverified' in s for s in translation['limitations']))
        # Both visible albedo and emission depend on the writer's API5 samples.
        def texture(api,uv):
            self.assertEqual(api,5)
            return (.25,.5,.75,.8)
        result=evaluate_surface(translation,(.25,.75),texture)
        self.assertEqual(result['F0'],[0.0,0.0,0.0])
        self.assertEqual(result['gloss'],0.0)
        self.assertGreater(result['albedo'][0],0.0)
        self.assertGreater(result['emission'],result['albedo'][0])
        self.assertAlmostEqual(result['emission'],20.0*result['albedo'][0],places=5)
        viewer=selected_translation(self.archive,material_path)
        self.assertEqual(viewer['source_preview_inputs'],translation['source_preview_inputs'])

        context_free=prepare_translation([graph_data],material_data,extract_material_parameters(material_data))
        self.assertEqual(context_free['status'],'unsupported')
        self.assertNotIn('source_preview_inputs',context_free)
        wrong_path=prepare_translation([graph_data],material_data,extract_material_parameters(material_data),
            source_graph_paths=['materialgraph/character/enemy/other/other.materialgraph'],
            source_material_path=material_path)
        self.assertEqual(wrong_path['status'],'unsupported')
        bad_instance=extract_material_parameters(material_data)
        row=next(r for r in bad_instance['unsupported_parameters'] if r['key_hash']==0x06F19114)
        row['raw_hex']='00000000'
        bad_override=prepare_translation([graph_data],material_data,bad_instance,
            source_graph_paths=graph_paths,source_material_path=material_path)
        self.assertEqual(bad_override['status'],'unsupported')

        programs=translation['diagnostics']
        self.assertTrue(any('Loop at 892' in row.get('reason','') for row in programs if isinstance(row,dict)))
        self.assertTrue(any('Exec-masked exports' in row.get('reason','') for row in programs if isinstance(row,dict)))


if __name__=='__main__':unittest.main()
