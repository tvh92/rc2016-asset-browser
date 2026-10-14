"""Bounded pass rows/defaults and exact source-bound candidate equivalence."""
import copy
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from rc2016.shaders.bindings import pass_contracts, surface_signature, prepare_translation
from rc2016.shaders.ir import DAG


def graph(passes, rows=(), defaults=b''):
    contracts=bytearray(24*len(passes))
    for i,(params,padded) in enumerate(passes):
        struct.pack_into('<H',contracts,24*i+18,params)
        struct.pack_into('<I',contracts,24*i+20,padded)
    payloads=[(0xBC93FB5E,bytes(contracts)),
              (0x45C4F4C0,b''.join(struct.pack('<HHI',*r) for r in rows)),
              (0xA59F667B,defaults)]
    offset=16+12*len(payloads);entries=[];data=b''
    for tag,payload in payloads:
        entries.append(struct.pack('<III',tag,offset,len(payload)))
        offset+=len(payload);data+=payload
    return struct.pack('<IIIHH',0x44415431,0,offset,len(payloads),0)+b''.join(entries)+data


class PassPartitionTests(unittest.TestCase):
    def test_empty_passes_and_nonzero_first_parameter_offset(self):
        data=graph([(0,0),(1,16),(0,0)],[(4,4,42)],struct.pack('<4f',0,2.5,0,0))
        contracts=pass_contracts(data)
        self.assertEqual([len(c['table']) for c in contracts],[0,1,0])
        self.assertEqual([c['defaults'] for c in contracts],[{},{42:2.5},{}])

    def test_counts_split_layout_without_an_offset_reset(self):
        data=graph([(1,16),(1,16)],[(0,4,42),(4,4,42)],
                   struct.pack('<8f',1,0,0,0,0,7,0,0))
        contracts=pass_contracts(data)
        self.assertEqual(contracts[0]['defaults'],{42:1})
        self.assertEqual(contracts[1]['defaults'],{42:7})
        self.assertEqual(contracts[1]['table'][0]['dword'],1)

    def test_defaults_are_pass_local_and_vectors_preserve_width(self):
        data=graph([(1,16),(1,16)],[(0,8,42),(0,8,42)],
                   struct.pack('<8f',1,2,0,0,3,4,0,0))
        self.assertEqual([c['defaults'][42] for c in pass_contracts(data)],[[1,2],[3,4]])

    def test_malformed_counts_and_default_blocks_fail_closed(self):
        controls=[
            (graph([(2,16)],[(0,4,1)],bytes(16)),'count exceeds'),
            (graph([(0,0)],[(0,4,1)]),'Unassigned graph parameter'),
            (graph([(1,16)],[(0,4,1)],bytes(12)),'defaults exceed'),
            (graph([(1,16)],[(0,4,1)],bytes(20)),'Unassigned graph default'),
            (graph([(2,16)],[(0,8,1),(4,4,2)],bytes(16)),'Overlapping'),
            (graph([(2,16)],[(0,4,1),(4,4,1)],bytes(16)),'Duplicate'),
            (graph([(1,16)],[(0,4,1)],struct.pack('<4f',float('nan'),0,0,0)),'Non-finite'),
        ]
        for data,reason in controls:
            with self.subTest(reason=reason),self.assertRaisesRegex(ValueError,reason):
                pass_contracts(data)


def bound(api=5,sample=0,unused=False):
    ir=DAG()
    if unused:ir.const(123)
    u=ir.node('input',name='attribute0.0',kind='attribute',attribute=0,channel=0)
    v=ir.node('input',name='attribute0.1',kind='attribute',attribute=0,channel=1)
    c=[ir.node('texture',[u,v],api=api,sample=sample,channel=i) for i in range(4)]
    z=ir.const(0)
    return dict(nodes=ir.nodes,outputs=dict(albedo=c[:3],F0=[z]*3,gloss=c[3],emission=z),
                normal=None,texture_bindings=[dict(api=api,path='t/source.texture',type_hash=123)],
                pass_contract_index=0,limitations=[])


class SurfaceIdentityTests(unittest.TestCase):
    def test_register_sample_and_node_labels_do_not_select_a_variant(self):
        a=bound();b=bound(8,19,True)
        self.assertEqual(surface_signature(a),surface_signature(b))
        # Parameter receipt provenance has no effect on bound arithmetic.
        b['parameters']=[dict(source='graph_default')]
        self.assertEqual(surface_signature(a),surface_signature(b))

    def test_source_texture_uv_channel_literal_and_normal_differences_are_retained(self):
        a=bound();controls=[]
        b=copy.deepcopy(a);b['texture_bindings'][0]['path']='t/other.texture';controls.append(b)
        b=copy.deepcopy(a);b['nodes'][2]['args'].reverse();controls.append(b)
        b=copy.deepcopy(a);b['nodes'][2]['channel']=1;controls.append(b)
        b=copy.deepcopy(a);b['nodes'][-1]['value']=.01;controls.append(b)
        b=copy.deepcopy(a);b['normal']=dict(api=5,sample=0,xyz=[2,3,4]);controls.append(b)
        for b in controls:self.assertNotEqual(surface_signature(a),surface_signature(b))

    def test_normal_sampling_interpretation_is_part_of_identity(self):
        a=bound();a['normal']=dict(api=5,sample=0,xyz=[2,3,4])
        b=copy.deepcopy(a);b['texture_bindings'].append(dict(api=8,path='t/source.texture',type_hash=123))
        b['normal']['api']=8
        self.assertNotEqual(surface_signature(a),surface_signature(b))

    def test_prepare_accepts_proven_equivalence_and_rejects_real_differences(self):
        programs=[dict(status='compiled',shader_id=str(i),code_sha256=str(i),code_offset=i) for i in range(2)]
        compiled=dict(programs=programs,graph_sha256='source',translator_version=2,limitations=[])
        candidates=[bound(),bound(8,19,True)]
        candidates[1]['pass_contract_index']=1
        candidates[1]['limitations']=['Other native normal composition is not covered.']
        with patch('rc2016.shaders.bindings.compiled',return_value=compiled), \
             patch('rc2016.shaders.bindings.pass_contracts',return_value=[{}]), \
             patch('rc2016.shaders.bindings.program_contracts',return_value=[{}]), \
             patch('rc2016.shaders.bindings._bind',side_effect=candidates):
            result=prepare_translation([b'graph'],b'material',{})
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['equivalent_candidates'],2)
        self.assertEqual([c['pass_contract_index'] for c in result['candidate_sources']],[0,1])
        self.assertIn(candidates[1]['limitations'][0],result['limitations'])
        self.assertFalse(result['game_equivalence'])
        candidates[1]['texture_bindings'][0]['path']='t/other.texture'
        with patch('rc2016.shaders.bindings.compiled',return_value=compiled), \
             patch('rc2016.shaders.bindings.pass_contracts',return_value=[{}]), \
             patch('rc2016.shaders.bindings.program_contracts',return_value=[{}]), \
             patch('rc2016.shaders.bindings._bind',side_effect=candidates):
            result=prepare_translation([b'graph'],b'material',{})
        self.assertEqual(result['status'],'unsupported')
        self.assertIn('Multiple portable native variants',result['diagnostics'][-1])
        self.assertEqual({c['pass_contract_index'] for c in result['candidate_surfaces']},{0,1})
        self.assertEqual(len({c['surface_sha256'] for c in result['candidate_surfaces']}),2)
        self.assertEqual({c['texture_bindings'][0]['path'] for c in result['candidate_surfaces']},{'t/source.texture','t/other.texture'})


class ArchiveBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research=os.environ.get('RC2016_RESEARCH')
        if not research:raise unittest.SkipTest('RC2016_RESEARCH is required')
        root=Path(os.environ.get('RC2016_GAME_ROOT',str(Path(research).parents[1]/'Ratchet & Clank (EUR)'/'Image0')))
        if not root.exists():raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive=open_archive(root)

    @classmethod
    def tearDownClass(cls):cls.archive.close()

    def translate(self,path):
        from rc2016.levels.scene import asset_bytes
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        data=asset_bytes(self.archive,path)
        graphs=[asset_bytes(self.archive,r['path']) for r in references(data) if r['path'].endswith('.materialgraph')]
        return prepare_translation(graphs,data,extract_material_parameters(data))

    def test_detail_variants_agree_only_within_the_recovered_surface_slice(self):
        path='material/environment/gaspar/rock/gas_rock_lava_rock_mound_01/nov_rock_basic_01.material'
        result=self.translate(path)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['equivalent_candidates'],2)
        self.assertEqual({c['pass_contract_index'] for c in result['candidate_sources']},{0,1})
        self.assertTrue(any('composition involving other samples' in s for s in result['limitations']))
        self.assertFalse(result['game_equivalence'])
        from rc2016.viewer.native_glsl import selected_translation
        self.assertIsNone(selected_translation(self.archive,path)) # exact hand rule keeps precedence

    def test_empty_parameter_passes_bind_actual_source_textures(self):
        from rc2016.levels.scene import asset_bytes
        from rc2016.viewer.native_glsl import selected_translation
        graph_path='materialgraph/environment/global/prop/gbl_prop_qwark_posters_01/gbl_prop_qwark_posters_01.materialgraph'
        contracts=pass_contracts(asset_bytes(self.archive,graph_path))
        self.assertEqual([c['table'] for c in contracts],[[],[]])
        self.assertEqual([c['defaults'] for c in contracts],[{},{}])
        path='material/environment/global/prop/gbl_prop_qwark_posters_01/gbl_prop_qwark_posters_01.material'
        result=self.translate(path)
        self.assertEqual(result['status'],'ready')
        self.assertTrue(result['texture_bindings'])
        self.assertIsNotNone(selected_translation(self.archive,path))
        self.assertFalse(result['game_equivalence'])


if __name__=='__main__':unittest.main()
