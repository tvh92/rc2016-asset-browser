import unittest

from rc2016.pipeline.projectile_mounts import (WARMONGER, WARMONGER_ROCKET, WARMONGER_V1, WARMONGER_V5, idle_offsets,
                                               rack_matrix, rack_placements)
from rc2016.pipeline.run import GROUP_STATUSES, assemble_group_blend

WEAPON = 'equipment/weapon/wpn_bouncer/wpn_bouncer.model'
BALL = 'equipment/projectile/proj_bouncer/proj_bouncer.model'

# The eight proj_warmonger copies of the 2026-11-01 Peacemaker capture in the weapon's bind pose (instance record world
# matrices relative to the weapon's record, through the captured barrel joint pose): roll (degrees) -> translation.
# The records hold 5-decimal values, so the linear part reads 0.70700 and 0.99986 for cos 45 and 1.
CAPTURED_V5 = {135.0: (-0.14527, 0.06490, 0.46210), 45.0: (-0.14527, 0.35544, 0.46210), 0.0: (0.00001, 0.41560, 0.46210),
               -45.0: (0.14527, 0.35543, 0.46211), -90.0: (0.20544, 0.21016, 0.46212), -135.0: (0.14527, 0.06489, 0.46211),
               180.0: (-0.00001, 0.00472, 0.46212), 90.0: (-0.20543, 0.21016, 0.46211)}


class ProjectileMountTests(unittest.TestCase):
    def test_projectile_moves_only_with_its_weapon(self):
        self.assertEqual(idle_offsets([WEAPON, BALL]), {BALL: (0.0, 0.174, 0.92)})
        self.assertEqual(idle_offsets([BALL]), {})
        self.assertEqual(idle_offsets([WEAPON]), {})

    def test_each_barrel_variant_and_both_balls_mount(self):
        v1 = 'equipment/weapon/wpn_bouncer/bangle_set/bangle_v1/wpn_bouncer_bangle_v1.model'
        v5 = v1.replace('v1', 'v5')
        big = 'equipment/projectile/proj_bouncer_big/proj_bouncer_big.model'
        for barrel in (v1, v5):
            self.assertEqual(idle_offsets([barrel, big]), {big: (0.0, 0.174, 0.93)})
        self.assertEqual(set(idle_offsets([v1, v5, BALL, big])), {BALL, big})

    def test_paths_match_case_and_separator_insensitively(self):
        ball = BALL.replace('/', chr(92)).upper()
        self.assertEqual(list(idle_offsets([WEAPON, ball])), [ball])


class RackTests(unittest.TestCase):
    def test_v5_rack_reproduces_the_captured_copies(self):
        rack = rack_placements([WARMONGER, WARMONGER_V5, WARMONGER_ROCKET])[WARMONGER_ROCKET]
        self.assertEqual((rack['joint'], rack['owner'], len(rack['matrices'])), ('barrel', WARMONGER, 8))
        self.assertTrue(rack['evidence'].startswith('captured'))
        found = {}
        for rows in rack['matrices']:
            for roll, t in CAPTURED_V5.items():
                if max(abs(a - b) for a, b in zip(rows[3][:3], t)) < 3e-5:
                    found[roll] = rows
        self.assertEqual(set(found), set(CAPTURED_V5))
        for roll, rows in found.items():   # local +Y points away from the barrel axis, unit scale, no tilt
            self.assertEqual(rows, rack_matrix((0.0, 0.21016, 0.46211), 0.20544, roll))
            self.assertAlmostEqual(rows[0][0] ** 2 + rows[0][1] ** 2, 1.0, places=12)
            self.assertEqual(rows[2], [0.0, 0.0, 1.0, 0.0])

    def test_v1_rack_is_four_inferred_copies_in_its_channels(self):
        rack = rack_placements([WARMONGER_V1, WARMONGER_ROCKET])[WARMONGER_ROCKET]
        self.assertEqual((rack['owner'], len(rack['matrices'])), (WARMONGER_V1, 4))
        self.assertTrue(rack['evidence'].startswith('inferred'))
        for rows in rack['matrices']:
            x, y, z = rows[3][:3]
            self.assertAlmostEqual(abs(x), 0.166 * 0.5 ** 0.5, places=6)   # channels at 45, 135, 225, 315 degrees
            self.assertAlmostEqual(abs(y - 0.21016), 0.166 * 0.5 ** 0.5, places=6)
            self.assertEqual(z, 0.46211)

    def test_rack_needs_its_barrel_and_the_captured_rack_wins(self):
        self.assertEqual(rack_placements([WARMONGER, WARMONGER_ROCKET]), {})
        self.assertEqual(rack_placements([WARMONGER_V5]), {})
        both = rack_placements([WARMONGER_V1, WARMONGER_V5, WARMONGER_ROCKET])[WARMONGER_ROCKET]
        self.assertEqual((both['owner'], len(both['matrices'])), (WARMONGER_V5, 8))

    def test_group_members_carry_one_mounted_copy_per_slot(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            finals = {}
            for path in (WARMONGER, WARMONGER_V5, WARMONGER_ROCKET):
                finals[path] = Path(tmp) / (Path(path).stem + '.blend')
                finals[path].write_bytes(path.encode())
            manifest = dict(models={k: dict(status=GROUP_STATUSES[0], model=k, final=str(v)) for k, v in finals.items()}, errors=[])
            captured = {}
            with patch('rc2016.pipeline.run.run_group_blend', lambda name, members, *a: captured.update(members=members)):
                assemble_group_blend(list(finals), manifest, SimpleNamespace(group_blend='peacemaker', group_actors=''),
                                     lambda *a, **k: None)
        members = captured['members']
        copies = [m for m in members if 'mount' in m]
        self.assertEqual(len(members), 2 + 8)
        self.assertEqual([m['name'] for m in copies], ['proj_warmonger.model #%d' % k for k in range(1, 9)])
        self.assertTrue(all(m['mount'] == dict(joint='barrel', owner='wpn_warmonger.model') for m in copies))
        self.assertTrue(all(m['blend'] == str(finals[WARMONGER_ROCKET]) and 'native_matrix' in m for m in copies))
        barrel = next(m for m in members if 'v5' in m['name'])
        self.assertEqual(barrel.get('rig_of'), 'wpn_warmonger.model')


if __name__ == '__main__':
    unittest.main()
