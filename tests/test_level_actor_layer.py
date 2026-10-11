"""Actor selection and layer isolation; source data tests are opt-in."""
from array import array
from collections import defaultdict
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from rc2016.levels.actor_format import IDENTITY
from rc2016.levels.actors import ActorCatalog, actor_groups, _references, KNOWN_UNSUPPORTED, AMBIGUOUS_PLACEMENT
from rc2016.levels.viewport import LevelViewport


def actor(model='object.model'):
    return dict(model=model, actor_path='object.actor', zone='game.zone', id='0000000000000001', name='crate',
                matrix_rows=[list(IDENTITY[i:i+4]) for i in range(0,16,4)],
                material_overrides=[], drawing_components=[])


def catalog(a):
    c=ActorCatalog(None)
    c.actors=[a]
    c.descriptors['object.actor']=dict(primary_model=a['model'], matrix_rows=a['matrix_rows'],
                                      drawing_components=[], dependencies=[dict(path='fallback.model')])
    return c


class SelectionTests(unittest.TestCase):
    def test_empty_primary_never_uses_dependency_model(self):
        a=actor(None); c=catalog(a)
        self.assertEqual(c.candidates(), [])
        self.assertEqual(a['reason'], 'no primary render model')

    def test_ambiguous_airlock_and_known_failures_are_not_drawn(self):
        for path in KNOWN_UNSUPPORTED | AMBIGUOUS_PLACEMENT:
            a=actor(path); c=catalog(a)
            self.assertEqual(c.candidates(), [])
            self.assertTrue(a['reason'])

    def test_vfx_actor_does_not_enter_layer_through_an_environment_model(self):
        a=actor();a['actor_path']='visualeffect/bridge.actor'
        c=catalog(a);c.descriptors[a['actor_path']]=c.descriptors.pop('object.actor')
        self.assertEqual(c.candidates(),[])
        self.assertEqual(a['reason'],'VFX outside actor layer')

    def test_unproven_drawing_components_fail_closed(self):
        a=actor(); c=catalog(a)
        a['drawing_components']=[dict(name='ScalePrototyping', decoded=None)]
        self.assertEqual(c.candidates(), [])
        self.assertIn('ScalePrototyping',a['reason'])

    def test_attachment_child_and_spawn_templates_are_skipped(self):
        a=actor(); c=catalog(a)
        ids=_references({'JointAttachList': {'AttachedActor': 1}, 'SpawneeTemplate': 2, 'Health':3},
                        {'AttachedActor','SpawneeTemplate'})
        self.assertEqual(ids, {'0000000000000001','0000000000000002'})
        c.references[a['id']].add('attachment placement unproven')
        self.assertEqual(c.candidates(), [])

    def test_exact_static_duplicate_is_reported_and_suppressed(self):
        a=actor(); c=catalog(a)
        c.static[('object.model',(0,0,0))].append(('art.zone', IDENTITY, ()))
        self.assertEqual(c.candidates(), [])
        self.assertEqual(len(c.duplicates),1)
        self.assertTrue(c.duplicates[0]['same_matrix'])

    def test_coincident_different_matrix_is_reported_without_transform_tuning(self):
        a=actor(); c=catalog(a)
        other=list(IDENTITY);other[0]=other[10]=-1
        c.static[('object.model',(0,0,0))].append(('art.zone',other,()))
        self.assertEqual(c.candidates(),[])
        self.assertEqual(a['matrix_rows'][0][0],1)
        self.assertFalse(c.duplicates[0]['same_matrix'])
        # Call twice without multiplying duplicate receipts.
        c.candidates();self.assertEqual(len(c.duplicates),1)

    def test_grouping_keeps_shear_mirroring_and_full_source_matrix(self):
        a=actor();a['matrix_rows'][0][0]=-1;a['matrix_rows'][1][0]=.25
        a['matrix_rows'][3][:3]=[3,4,5]
        groups,replacements=actor_groups([a],[])
        self.assertEqual(list(groups),[(True,())])
        self.assertEqual(list(groups[(True,())]),[v for r in a['matrix_rows'] for v in r])
        self.assertFalse(replacements)


class LayerTests(unittest.TestCase):
    def test_toggle_and_zone_filter_restore_static_bytes(self):
        static=array('f', IDENTITY); placed=array('f',IDENTITY);placed[12]=8
        model=dict(zones=[('art',(False,()),static)],actor_zones=[('game',(False,()),placed)],
                   groups=[],groups_for=None)
        uploaded={}; counter=iter(range(1,20))
        def buffer(target,rows):
            ident=next(counter);uploaded[ident]=rows.tobytes();return ident
        v=SimpleNamespace(_hidden_zones=set(),show_actors=False,_models={'model':model},
            _ext={'glDeleteBuffers':lambda *a:None,'glBindBuffer':lambda *a:None},
            _delete_vaos=lambda *a:None,_buffer=buffer,GL_ARRAY_BUFFER=1)
        LevelViewport._merge_groups(v)
        original=uploaded[model['groups'][0][0]]
        self.assertEqual(original,static.tobytes())
        v.show_actors=True;LevelViewport._merge_groups(v)
        self.assertEqual(model['groups'][0][1],2)
        v._hidden_zones={'game'};LevelViewport._merge_groups(v)
        self.assertEqual(uploaded[model['groups'][0][0]],original)
        v._hidden_zones=set();v.show_actors=False;LevelViewport._merge_groups(v)
        self.assertEqual(uploaded[model['groups'][0][0]],original)

    def test_preview_toggle_invalidates_cached_draws_and_batches_independently(self):
        from array import array
        from types import SimpleNamespace
        from rc2016.levels.viewport import LevelViewport
        static, placed, spawn = (array('f', IDENTITY) for _ in range(3))
        placed[12], spawn[12] = 8., 16.
        model = dict(zones=[('art',(False,()),static)], actor_zones=[('game',(False,()),placed)],
                     spawn_zones=[('game',(False,()),spawn)], groups=[],groups_for=None)
        uploads = {}; identifiers = iter(range(100))
        def buffer(target, rows):
            ident = next(identifiers); uploads[ident] = rows.tobytes(); return ident
        v = SimpleNamespace(_hidden_zones=set(),show_actors=False,show_spawn_preview=False,
            _models={'model':model},_draw_list='cached',picked_actor=actor(), _draw=lambda:None,
            _ext={'glDeleteBuffers':lambda *a:None,'glBindBuffer':lambda *a:None},
            _delete_vaos=lambda *a:None,_buffer=buffer,GL_ARRAY_BUFFER=1)
        LevelViewport.set_spawn_preview(v, True)
        self.assertIsNone(v._draw_list)
        self.assertIsNone(v.picked_actor)
        LevelViewport._merge_groups(v)
        self.assertEqual(uploads[model['groups'][0][0]],static.tobytes()+spawn.tobytes())
        v.show_actors=True
        LevelViewport._merge_groups(v)
        self.assertEqual(uploads[model['groups'][0][0]],static.tobytes()+placed.tobytes()+spawn.tobytes())
        v.show_spawn_preview=False
        LevelViewport._merge_groups(v)
        self.assertEqual(uploads[model['groups'][0][0]],static.tobytes()+placed.tobytes())
        v._hidden_zones={'game'}
        LevelViewport._merge_groups(v)
        self.assertEqual(uploads[model['groups'][0][0]],static.tobytes())


class LoaderTests(unittest.TestCase):
    def test_case_variants_share_the_uploaded_static_geometry(self):
        import queue
        import threading
        from unittest.mock import patch
        from rc2016.levels import panel, scene, actors as module
        a=actor('object.model');b=actor('Object.model');b['id']='0000000000000002'
        c=catalog(a);c.actors.append(b)
        c.add_zone=lambda *args:2
        static=dict(zone='game.zone',models=[dict(path='OBJECT.model',hidden=False,count=1,
                    groups={(False,()):array('f',IDENTITY)})],instances=1,points=[(0,0,0)])
        geometry=dict(materials=[],skinned=False,bind_usable=True,source_sha256='verified')
        out=queue.Queue()
        with patch.object(module,'ActorCatalog',return_value=c), patch.object(scene,'zone_scene',return_value=static), \
             patch.object(scene,'model_geometry',return_value=geometry) as decode, \
             patch.object(scene,'material_entries',return_value={}):
            panel.load_level(lambda:object(),'game.zone',out,threading.Event())
        events=list(out.queue)
        self.assertEqual([e[1] for e in events if e[0]=='model'],['OBJECT.model'])
        self.assertEqual([e[2] for e in events if e[0]=='actors'],['OBJECT.model'])
        decode.assert_called_once()
        summary=next(e[1] for e in events if e[0]=='actor_summary')
        self.assertEqual(summary['resolved'],2)
        self.assertEqual(summary['drawn'],2)


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'),'set RC2016_RESEARCH for archive checks')
class ArchiveTests(unittest.TestCase):
    def test_crate_zone_has_structural_primary_bindings(self):
        from rc2016.levels.scene import open_archive, zone_scene
        root=Path(os.environ['RC2016_RESEARCH']).parents[1]/'Ratchet & Clank (EUR)'/'Image0'
        archive=open_archive(root)
        try:
            path='levels/novalis/openarea/openworld/default_region/gp_crates.zone'
            c=ActorCatalog(archive);c.add_zone(path,zone_scene(archive,path))
            eligible=c.candidates()
            self.assertGreater(len(eligible),10)
            self.assertTrue(all(a['binding']['section']=='32FAC8E0' for a in eligible))
            self.assertTrue(all(a['actor_sha256'] and a['zone_sha256'] for a in eligible))
        finally:
            archive.close()


if __name__=='__main__':
    unittest.main()
