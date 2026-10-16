"""Exact source guard and archive-backed Extermitank hoses writer preview."""
import hashlib
import os
from pathlib import Path
import unittest

from rc2016.shaders.bindings import (
    EXTERMITANK_HOSES_SOURCE_PREVIEW as RULE,
    SOURCE_PREVIEW_POLICIES,
    _source_preview_policy,
    validate_source_preview_inputs,
)

# Code hash of the sibling writer 0x315a8578 in the same graph. It must never receive the policy.
SIBLING_WRITER_CODE_SHA256='9d86587dc230c2efa4243aa56d4289163b436ec060fe4229b3f15f09f2b9a9e7'


class ExtermitankHosesSourceGuardTests(unittest.TestCase):
    def test_exact_graph_material_writer_and_code_are_required(self):
        program={'shader_id':RULE['shader_id'],'code_sha256':RULE['code_sha256']}
        self.assertIsNotNone(_source_preview_policy([RULE['graph_path'].upper()],RULE['graph_sha256'],
            RULE['material_path'].upper(),RULE['material_sha256'],program))
        self.assertIs(SOURCE_PREVIEW_POLICIES[RULE['graph_path']],RULE)
        for paths,graph_sha,material_path,material_sha,shader in [
            (['materialgraph/character/enemy/other/other.materialgraph'],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],program),
            ([RULE['graph_path']],'0'*64,RULE['material_path'],RULE['material_sha256'],program),
            ([RULE['graph_path']],RULE['graph_sha256'],'material/character/enemy/other.material',RULE['material_sha256'],program),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],'0'*64,program),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],dict(program,shader_id='0x315a8578')),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],dict(program,code_sha256='0'*64)),
            ([RULE['graph_path']],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],dict(program,code_sha256=SIBLING_WRITER_CODE_SHA256)),
        ]:
            with self.subTest(graph=paths,material=material_path,shader=shader['shader_id']):
                self.assertIsNone(_source_preview_policy(paths,graph_sha,material_path,material_sha,shader))

    def test_same_writer_code_under_another_graph_or_material_gets_no_policy(self):
        program={'shader_id':RULE['shader_id'],'code_sha256':RULE['code_sha256']}
        other_graph='materialgraph/character/enemy/enm_warbot_v3_eye/enm_warbot_v3_eye.materialgraph'
        self.assertIsNone(_source_preview_policy([other_graph],RULE['graph_sha256'],RULE['material_path'],RULE['material_sha256'],program))
        other_material='material/character/enemy/enm_tank_extermitank/enm_tank_extermitank.material'
        self.assertIsNone(_source_preview_policy([RULE['graph_path']],RULE['graph_sha256'],other_material,'0'*64,program))

    def test_persisted_preview_receipt_is_exact(self):
        receipt=dict(name='CB0[99]',kind='cb',api=0,dword=99,value=0.0,
            graph_path=RULE['graph_path'],graph_sha256=RULE['graph_sha256'],
            material_path=RULE['material_path'],material_sha256=RULE['material_sha256'],
            shader_id=RULE['shader_id'],code_sha256=RULE['code_sha256'],
            meaning='time/phase-like source input; runtime upload/value unverified')
        translation=dict(source_preview_inputs=[receipt],shader_id=RULE['shader_id'],
            code_sha256=RULE['code_sha256'],graph_sha256=RULE['graph_sha256'])
        self.assertTrue(validate_source_preview_inputs(translation))
        for bad_row in [dict(receipt,value=1.0),dict(receipt,material_sha256='0'*64)]:
            with self.subTest(row=bad_row):
                with self.assertRaisesRegex(ValueError,'exact graph, material and writer'):
                    validate_source_preview_inputs(dict(translation,source_preview_inputs=[bad_row]))
        with self.assertRaisesRegex(ValueError,'exact graph, material and writer'):
            validate_source_preview_inputs(dict(translation,shader_id='0x315a8578'))


class ExtermitankHosesArchiveTests(unittest.TestCase):
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

    def test_exact_source_writer_is_ready_with_frozen_input(self):
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import prepare_translation
        from rc2016.viewer.native_glsl import evaluate_surface
        graph_path=RULE['graph_path'];material_path=RULE['material_path']
        graph_data=asset_bytes(self.archive,graph_path);material_data=asset_bytes(self.archive,material_path)
        self.assertEqual(hashlib.sha256(bytes(graph_data)).hexdigest(),RULE['graph_sha256'])
        self.assertEqual(hashlib.sha256(bytes(material_data)).hexdigest(),RULE['material_sha256'])
        graph_paths=[r['path'] for r in references(material_data) if r['path'].endswith('.materialgraph')]
        self.assertEqual(graph_paths,[graph_path])
        translation=prepare_translation([graph_data],material_data,extract_material_parameters(material_data),
            source_graph_paths=graph_paths,source_material_path=material_path)
        self.assertEqual(translation['status'],'ready')
        self.assertEqual(translation['graph_sha256'],RULE['graph_sha256'])
        self.assertEqual(translation['shader_id'],RULE['shader_id'])
        self.assertEqual(translation['code_sha256'],RULE['code_sha256'])
        self.assertEqual(translation['pass_contract_index'],1)
        self.assertFalse(translation['game_equivalence'])
        # The frozen input is a zero constant in the source DAG; no CB input survives.
        self.assertFalse(any(n['op']=='input' and n.get('kind')=='cb' for n in translation['nodes']))
        self.assertTrue(any(n['op']=='const' and n.get('value')==0.0 for n in translation['nodes']))
        receipt=translation['source_preview_inputs'][0]
        self.assertEqual((receipt['name'],receipt['kind'],receipt['api'],receipt['dword'],receipt['value']),
            ('CB0[99]','cb',0,99,0.0))
        self.assertEqual(receipt['shader_id'],RULE['shader_id'])
        self.assertTrue(any('Extermitank hoses source preview' in s and 'runtime value are unverified' in s
                            for s in translation['limitations']))
        self.assertEqual([(b['api'],b['path']) for b in translation['texture_bindings']],[
            (5,'textures/character/enemy/enm_tank_extermitank/enm_tank_extermitank_hoses_g.texture'),
            (6,'textures/character/enemy/enm_tank_extermitank/enm_tank_extermitank_hoses_c.texture'),
            (7,'textures/character/enemy/enm_tank_extermitank/enm_tank_extermitank_hoses_c.texture')])
        # The sibling writer 0x315a8578 keeps its CB0[99] input unbound.
        self.assertTrue(any(isinstance(row,dict) and row.get('shader_id')=='0x315a8578' and
                            'CB0[99]' in row.get('reason','') for row in translation['diagnostics']))
        result=evaluate_surface(translation,(.25,.75),lambda api,uv:(.25,.5,.75,.8))
        self.assertEqual(len(result['albedo']),3)
        context_free=prepare_translation([graph_data],material_data,extract_material_parameters(material_data))
        self.assertEqual(context_free['status'],'unsupported')
        self.assertNotIn('source_preview_inputs',context_free)


if __name__=='__main__':unittest.main()
