"""Group members placed by zone actors: matching, anchoring, labels and the run.py member list.

The zone and actor decoders are replaced by fixed rows; no game data is used.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from rc2016.pipeline import actor_placements as P
from rc2016.pipeline.run import placed_group_members

ZONE = 'levels/test/gp_flight.zone'
HEAD = 'character/enemy/enm_test/enm_test.model'
TANK = 'character/enemy/enm_test_tank/enm_test_tank.model'


def matrix(x, z, turned=False):
    """Row-vector placement at (x, 0, z); ``turned`` swaps the X and Z axes as the Pokitaru convoys do."""
    rows = np.eye(4)
    if turned:
        rows[0, :3], rows[2, :3] = (0, 0, 1), (-1, 0, 0)
    rows[3, :3] = (x, 0, z)
    return rows.tolist()


class FakeArchive:
    def __init__(self):
        self.entries = {'z': dict(type='zone', path=ZONE), 'a1': dict(type='actor', path='mobys/head.actor'),
                        'a2': dict(type='actor', path='mobys/tank.actor'), 'a3': dict(type='actor', path='mobys/cam.actor'),
                        'mh': dict(type='model', path=HEAD), 'mt': dict(type='model', path=TANK)}

    def resolve(self, path):
        return [k for k, e in self.entries.items() if e['path'] == path]

    def asset_data(self, key):
        return key


ACTORS = [dict(name='Convoy #1 tank segment', id='2', actor_path='mobys\\tank.actor', matrix_rows=matrix(90, 5, True)),
          dict(name='Convoy #1 locomotive', id='1', actor_path='mobys\\head.actor', matrix_rows=matrix(100, 5, True)),
          dict(name='Convoy #1 tank segment', id='3', actor_path='mobys\\tank.actor', matrix_rows=matrix(80, 6, True)),
          dict(name='Convoy #1 path', id='5', actor_path='mobys\\cam.actor', matrix_rows=matrix(0, 0)),
          dict(name='Convoy #2 locomotive', id='4', actor_path='mobys\\head.actor', matrix_rows=matrix(0, 0))]
PRIMARY = {'a1': dict(primary_model=HEAD), 'a2': dict(primary_model=TANK), 'a3': dict(primary_model=None)}


def patched():
    return (mock.patch.object(P, 'decode_zone_actors', return_value=dict(actors=ACTORS)),
            mock.patch.object(P, 'decode_actor', side_effect=lambda key: PRIMARY[key]))


class ZonePlacementTests(unittest.TestCase):
    def rows(self, prefix='Convoy #1', anchor=HEAD):
        zone, actor = patched()
        with zone, actor:
            rows, self.skipped = P.zone_placements(FakeArchive(), ZONE, prefix, anchor)
            return rows

    def test_matching_actors_are_placed_relative_to_the_anchor(self):
        rows = self.rows()
        self.assertEqual([r['id'] for r in rows], ['2', '1', '3'])
        self.assertEqual([r['model'] for r in rows], [TANK, HEAD, TANK])
        anchor = np.array(rows[1]['native_matrix'])
        self.assertTrue(np.allclose(anchor, np.eye(4)))
        # The tank 10 units behind along world -X sits at local +Z of the turned anchor.
        self.assertTrue(np.allclose(np.array(rows[0]['native_matrix'])[3, :3], (0, 0, 10)))
        self.assertTrue(np.allclose(np.array(rows[2]['native_matrix'])[3, :3], (1, 0, 20)))
        self.assertEqual(self.skipped, [dict(name='Convoy #1 path', actor='mobys\\cam.actor')])   # no primary model

    def test_prefix_is_case_insensitive_and_needs_a_match(self):
        self.assertEqual(len(self.rows('convoy #1')), 3)
        with self.assertRaisesRegex(ValueError, 'No actor'):
            self.rows('Missing')

    def test_the_anchor_model_must_be_placed(self):
        with self.assertRaisesRegex(ValueError, 'places'):
            self.rows(anchor='character/other.model')

    def test_lead_moves_the_anchor_one_spacing_beyond_the_far_end(self):
        tank = lambda z: dict(name='t', native_matrix=matrix(0, z))
        rows = [tank(10), dict(name='h', native_matrix=np.eye(4).tolist()), tank(30), tank(18)]
        moved = P.lead_with_anchor(rows)
        self.assertTrue(np.allclose(moved[1]['native_matrix'], np.eye(4)))
        self.assertAlmostEqual(moved[1]['lead']['spacing'], 10.0)   # median of the gaps 8 and 12
        # The head lands at z = 40, so the tanks sit 30, 10 and 22 behind it.
        self.assertEqual([np.array(moved[i]['native_matrix'])[3, 2] for i in (0, 2, 3)], [-30.0, -10.0, -22.0])
        with self.assertRaisesRegex(ValueError, 'two other'):
            P.lead_with_anchor(rows[:2])

    def test_repeated_names_are_numbered(self):
        self.assertEqual(P.placement_labels([dict(name='t'), dict(name='h'), dict(name='t')]), ['t 1', 'h', 't 2'])


class RunMemberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.blends = {}
        for name, path in (('head', HEAD), ('tank', TANK)):
            blend = Path(self.temp.name) / f'{name}.blend'
            blend.write_bytes(name.encode())
            self.blends[path] = str(blend)

    def tearDown(self):
        self.temp.cleanup()

    def members(self, delivered, selection=('mh', 'mt')):
        args = SimpleNamespace(group_actors=f'{ZONE}:Convoy #1')
        zone, actor = patched()
        with zone, actor:
            return placed_group_members(FakeArchive(), list(selection), delivered, args)

    def test_one_bind_pose_member_per_placed_actor(self):
        members = self.members([(HEAD, self.blends[HEAD]), (TANK, self.blends[TANK])])
        self.assertEqual([m['name'] for m in members], ['Convoy #1 tank segment 1', 'Convoy #1 locomotive',
                                                        'Convoy #1 tank segment 2'])
        self.assertEqual([m['blend'] for m in members], [self.blends[TANK], self.blends[HEAD], self.blends[TANK]])
        self.assertTrue(all(m['bind_pose'] and len(m['native_matrix']) == 4 for m in members))
        self.assertEqual(members[1]['placement'], dict(zone=ZONE, actor='mobys\\head.actor', actor_id='1'))

    def test_every_placed_model_must_be_delivered(self):
        with self.assertRaisesRegex(ValueError, 'enm_test_tank'):
            self.members([(HEAD, self.blends[HEAD])])


if __name__ == '__main__':
    unittest.main()
