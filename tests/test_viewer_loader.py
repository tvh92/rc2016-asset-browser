from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from rc2016.viewer.loader import SourcePreviewLoader, mesh_payload, presentation_basis
from rc2016.app.browser import count_clips, group_clips, source_details

class SourcePreviewTests(unittest.TestCase):
    def fixture(self):
        meshes=[]
        for i in range(2):
            meshes.append({'index':i,'positions':[(0,0,0),(1+i,0,0),(0,1,0)],'normals':[(0,0,1)]*3,'triangles':[(0,1,2)]})
        return {'looks':[{'index':0,'lods':[{'index':0,'meshes':[0],'start':0,'count':1},{'index':1,'meshes':[1],'start':1,'count':1}]}], 'mesh_record_count':2,'meshes':meshes,'source_sha256':'a'*64}

    def test_source_lods_and_basis(self):
        decoded=self.fixture()
        high=mesh_payload(decoded,'Highest');low=mesh_payload(decoded,'Lowest');all_mesh=mesh_payload(decoded,'All')
        self.assertEqual(high['selected_mesh_indices'],[0]);self.assertEqual(low['selected_mesh_indices'],[1])
        self.assertEqual(all_mesh['triangles'],[(0,1,2),(3,4,5)])
        self.assertEqual(high['vertices'][2],(0,0,1))
        self.assertEqual(high['normals'][0],(0,-1,0))
        self.assertFalse(high['game_equivalence'])
        self.assertEqual(presentation_basis((3,4,5)),(3,-5,4))

    def test_source_identity_rejected_before_bytes_decode(self):
        loader=SourcePreviewLoader();loader.source_root=Path.cwd()
        loader.archive=SimpleNamespace(entries={'id':{'path':'exact/model.model','type':'model'}})
        with self.assertRaisesRegex(ValueError,'identity|match'):
            loader.load(Path.cwd(),'id','other/model.model')

    def test_details_come_from_the_source_payload(self):
        payload=dict(source_path='a/b.model',model_id='id1',joint_count=49,looks=[{}],mesh_record_count=3,look=0,lod='Highest',
                     selected_mesh_indices=[0,1],vertices=[0]*10,triangles=[0]*4,
                     materials=[dict(index=0,name='body',path='m/body.material'),dict(index=1,name=None,path=None)],
                     texture_list=[{'via_graph':False},{'via_graph':True}],clips=['walk','idle'],animsets=['x.animset'],
                     source_packed_sha256='ab',codec='raw')
        text=source_details(payload,'enm_toxic_crab')
        for part in ('Used by: enm_toxic_crab','skinned, 49 joints','3 in the file','10 vertices','0: body — m/body.material',
                     '1: (unnamed) — no material file','Textures: 2 (1 graph defaults)','2 clips from 1 animation set:','  x.animset'):
            self.assertIn(part,text)
        static=source_details(dict(payload,joint_count=0,clips=[],animsets=[],animation_note=''))
        self.assertIn('static (no skeleton)',static);self.assertIn('Animations: none; static model',static)

    def test_clip_menu_groups_and_sorts_but_keeps_source_indices(self):
        labels=['b: Hover_R','b: Hover_Idle','b: Engaged_Run','b: Engaged_Turn','b: Death','a: Idle_A']
        tree=group_clips(labels)
        self.assertEqual([text for text,_ in tree],['a','b'])
        self.assertEqual(tree[1][1],[('Engaged',[('Engaged_Run',2),('Engaged_Turn',3)]),
                                     ('Hover',[('Hover_Idle',1),('Hover_R',0)]),('Other',[('Death',4)])])
        self.assertEqual(count_clips(tree),len(labels))
        self.assertEqual(group_clips(['Walk','Idle']),[('Idle',1),('Walk',0)])
        self.assertEqual(group_clips([]),[])

if __name__=='__main__':unittest.main()
