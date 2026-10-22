import unittest

from rc2016.levels.panel import zone_order


class ZoneOrderTests(unittest.TestCase):
    def test_groups_in_logical_order_with_imposter_after_base(self):
        zones = [f"levels/x/{n}.zone" for n in ("sound", "gp_base", "art_terrain_imposter", "art_terrain", "atm_lgt", "odd")]
        grouped = [(title, [z.rsplit("/", 1)[1][:-5] for z in members]) for title, members in zone_order(zones)]
        self.assertEqual(grouped, [("Art", ["art_terrain", "art_terrain_imposter"]), ("Gameplay", ["gp_base"]),
                                   ("Lighting & atmosphere", ["atm_lgt"]), ("Audio", ["sound"]), ("Other", ["odd"])])


if __name__ == "__main__":
    unittest.main()
