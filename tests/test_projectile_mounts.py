import unittest

from rc2016.pipeline.projectile_mounts import idle_offsets

WEAPON = 'equipment/weapon/wpn_bouncer/wpn_bouncer.model'
BALL = 'equipment/projectile/proj_bouncer/proj_bouncer.model'


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


if __name__ == '__main__':
    unittest.main()
