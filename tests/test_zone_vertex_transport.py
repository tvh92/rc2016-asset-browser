"""Guard the complete level loader -> GPU path, including the white fallback."""
from array import array
import ctypes
import queue
import threading
import unittest
from unittest.mock import Mock, patch
import numpy as np

from rc2016.levels import panel, scene, viewport, actors
from rc2016.viewer.shading import INSTANCE_ATTRIBUTES


class TransportTests(unittest.TestCase):
    def test_batched_loader_posts_alpha_without_mutating_cached_geometry(self):
        class Catalog:
            factories, zone_actors = [], {}
            def __init__(self, _archive): pass
            def add_zone(self, *_args): return 0
            def candidates(self): return []
            def descriptor(self, _path): return {}
            def summary(self): return dict(reasons={}, duplicates=[], resolved=0, drawn=0)
        block=(np.array([[0,0,0],[10,0,0]]), np.array([[1,2,3,0],[4,5,6,128]],np.uint8))
        model=dict(path='terrain.model',hidden=False,count=1,groups={},vertex_colours=[block])
        zone=dict(zone='terrain.zone',instances=1,points=[(0,0,0)],models=[model])
        geometry=dict(positions=array('f',[0,0,0,1,0,0]),materials=[])
        for missing_instance in (False,True):
            model['count']=2 if missing_instance else 1
            out=queue.Queue()
            with patch.object(actors,'ActorCatalog',Catalog), \
                 patch.object(scene,'zone_scene',return_value=zone), \
                 patch.object(scene,'model_geometry',return_value=geometry), \
                 patch.object(scene,'material_entries',return_value={}), \
                 patch.object(panel,'_post_scene_lighting'):
                panel.load_level(lambda:object(),'terrain.zone',out,threading.Event(),workers=2)
            events=list(out.queue)
            posted=next(e[2] for e in events if e[0]=='model')
            note=next(e for e in events if e[0]=='vertex_colours')
            self.assertEqual(note[3],not missing_instance)
            if missing_instance:self.assertNotIn('vertex_alpha',posted)
            else:self.assertEqual(list(posted['vertex_alpha']),[0.,np.float32(128/255)])
            self.assertNotIn('vertex_alpha',geometry)

    def test_alpha_buffer_is_owned_and_uploaded_with_one_value_per_vertex(self):
        view=object.__new__(viewport.LevelViewport)
        view._models={};view._ext={'glBindBuffer':Mock()}
        uploaded=[]
        def buffer(target,data):
            uploaded.append(data);return len(uploaded)
        view._buffer=buffer
        g=dict(positions=array('f',[0,0,0,1,0,0,0,1,0]),normals=array('f',[0,0,1]*3),
               uvs=array('f',[0,0]*3),indices=array('I',[0,1,2]),batches=[],triangles=1,materials=[],
               vertex_alpha=array('f',[0.,.5,1.]))
        view._upload_model('painted',g,False)
        self.assertIs(uploaded[-1],g['vertex_alpha'])
        record=view._models['painted']
        self.assertEqual(record['abo'],5)
        self.assertIn(record['abo'],record['buffers'])
        view._upload_model('plain',{k:v for k,v in g.items() if k!='vertex_alpha'},False)
        self.assertIsNone(view._models['plain']['abo'])

    def test_vao_alpha_is_per_vertex_and_disabled_for_plain_models(self):
        view=object.__new__(viewport.LevelViewport)
        view._gl=Mock();view._shading=Mock()
        def generate(_n,ptr):ctypes.cast(ptr,ctypes.POINTER(ctypes.c_uint))[0]=42
        view._ext={'glGenVertexArrays':generate,'glBindVertexArray':Mock(),
                   'glBindBuffer':Mock(),'glVertexAttribDivisor':Mock()}
        alpha=INSTANCE_ATTRIBUTES['aRockAlpha']
        view._begin_attributes()
        view._shading.glVertexAttrib4f.assert_called_with(alpha,1.,0.,0.,1.)
        for paint in (True,False):
            model=dict(vbo=1,nbo=2,tbo=3,ibo=4,abo=5 if paint else None)
            view._vao(model,(6,1,False,()))
            if paint:
                view._shading.glEnableVertexAttribArray.assert_any_call(alpha)
                view._shading.glVertexAttribPointer.assert_any_call(alpha,1,view.GL_FLOAT,0,4,None)
                view._ext['glVertexAttribDivisor'].assert_any_call(alpha,0)
            else:view._shading.glDisableVertexAttribArray.assert_called_with(alpha)
            for row in range(4):
                view._ext['glVertexAttribDivisor'].assert_any_call(INSTANCE_ATTRIBUTES[f'aInst{row}'],1)
