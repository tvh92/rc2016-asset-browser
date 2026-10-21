"""Which atmosphere and reflection capture a level uses (rc2016.levels.sky; synthetic zones, no game data, no GL)."""
import unittest
from unittest import mock

from rc2016.levels import sky
from tests.test_level_volumes import record, zone

DAY_ZONE = 'levels/Veldin/atm_lgt.zone'
NIGHT_ZONE = 'levels/Veldin/atm_lgt_night.zone'
DAY_ATM = 'atmosphere/atm_Veldin/atm_Veldin.atmosphere'
NIGHT_ATM = 'atmosphere/atm_Veldin_Night/atm_Veldin_Night_Darker.atmosphere'
LGT = 'levels/Veldin/openArea/OpenWorld/default_region/lgt.zone'
LGT_NIGHT = 'levels/Veldin/openArea/OpenWorld/default_region/lgt_night.zone'
ZONES = [NIGHT_ZONE, DAY_ZONE, 'levels/Veldin/atm_lgt_imposter.zone', LGT_NIGHT, LGT,
         'levels/Veldin/openArea/OpenWorld/default_region/gp_base.zone']


def atmosphere_zone(atmosphere, capture=None):
    data = b'1TAD' + b'\0' * 60 + atmosphere.replace('/', '\\').encode() + b'\0'
    if capture:
        data += b'?' + capture.replace('/', '\\').encode() + b'\0'
    return data


def capture_path(kind, texture_id):
    return f'textures/{kind}/volumereflections/0x{texture_id:x}/reflection.texture'


class FakeArchive:
    def __init__(self, paths):
        self.paths = set(paths)

    def resolve(self, path):
        return [1] if path in self.paths else []


class Level:
    """Zone bytes of a Veldin-like level and the calls made on them."""

    def __init__(self, archive_paths=None, zones=None):
        self.zones = zones or {
            DAY_ZONE: atmosphere_zone(DAY_ATM), NIGHT_ZONE: atmosphere_zone(NIGHT_ATM),
            LGT: zone([record((0, 0, 0), (10, 10, 10), 0xA), record((100, 0, 0), (10, 10, 10), 0xB)],
                      tiles=[(100, 0, 0), (104, 0, 0), (0, 0, 0)]),
            LGT_NIGHT: zone([record((0, 0, 0), (50, 50, 50), 0xC, path='\\textures\\lgt_night\\volumereflections\\0xc\\reflection.texture')],
                            tiles=[(0, 0, 0)]),
            'levels/Veldin/openArea/OpenWorld/default_region/gp_base.zone': b'1TAD' + b'\0' * 12}
        self.archive = FakeArchive(archive_paths if archive_paths is not None else
                                   [capture_path('lgt', 0xA), capture_path('lgt', 0xB), capture_path('lgt_night', 0xC)])
        self.read = []
        self.profiles = []

    def run(self, zones=ZONES, position=None, profile_result=None):
        def asset_bytes(_archive, path):
            self.read.append(path)
            return self.zones.get(path, b'')

        def clear_sky_profile(_archive, path):
            self.profiles.append(path)
            return profile_result(path) if profile_result else [[0.1, 0.2, 0.4]]
        with mock.patch.object(sky, 'asset_bytes', asset_bytes), mock.patch.object(sky, 'sun_ray', lambda d: (0, -1, 0)), \
                mock.patch.object(sky, 'sun_colour', lambda d: (1, 1, 1)), mock.patch.object(sky, 'fog_profile', lambda d: None), \
                mock.patch.object(sky, 'colour_grade', lambda d: None), mock.patch.object(sky, 'references', lambda d: []), \
                mock.patch.object(sky, 'clear_sky_profile', clear_sky_profile):
            return sky.level_sky(self.archive, zones, probe_position=position)


class NameRuleTests(unittest.TestCase):
    def test_atmosphere_zones_by_name(self):
        for path in ('levels/Novalis/atmosphere.zone', 'levels/Museum/openArea/Atmosphere.zone', 'levels/x/atmosphere_ext.zone',
                     DAY_ZONE, NIGHT_ZONE):
            self.assertTrue(sky.is_atmosphere_zone(path), path)
        for path in ('levels/Veldin/atm_lgt_imposter.zone', 'levels/Novalis/atmosphere_imposter.zone', LGT,
                     'levels/Metropolis/openArea/World/default_region/gp_flight.zone', 'levels/x/batman_atm.zone'):
            self.assertFalse(sky.is_atmosphere_zone(path), path)

    def test_night_is_read_from_the_file_name_only(self):
        self.assertTrue(sky.is_night(NIGHT_ZONE))
        self.assertTrue(sky.is_night(NIGHT_ATM))
        self.assertTrue(sky.is_night('levels/x/LGT_NIGHT.zone'))
        self.assertFalse(sky.is_night(DAY_ZONE))
        self.assertFalse(sky.is_night(DAY_ATM))
        self.assertFalse(sky.is_night('levels/night_shift/lighting.zone'))

    def test_lighting_zones(self):
        for path in (LGT, LGT_NIGHT, 'levels/Novalis/openArea/OpenWorld/default_region/lighting.zone'):
            self.assertTrue(sky.is_lighting_zone(path), path)
        for path in ('levels/Deplanetizer/openArea/OpenWorld/default_region/lighting_boss.zone', DAY_ZONE,
                     'levels/Veldin/lgt_imposter.zone'):
            self.assertFalse(sky.is_lighting_zone(path), path)


class ChooseAtmosphereTests(unittest.TestCase):
    def test_one_atmosphere_is_taken_as_it_is(self):
        self.assertEqual(sky.choose_atmosphere({DAY_ATM: [DAY_ZONE]}), (DAY_ATM, False))
        self.assertEqual(sky.choose_atmosphere({NIGHT_ATM: [NIGHT_ZONE]}), (NIGHT_ATM, True))

    def test_day_wins_over_night_whatever_the_order(self):
        for found in ({DAY_ATM: [DAY_ZONE], NIGHT_ATM: [NIGHT_ZONE]}, {NIGHT_ATM: [NIGHT_ZONE], DAY_ATM: [DAY_ZONE]}):
            self.assertEqual(sky.choose_atmosphere(found), (DAY_ATM, False))

    def test_a_night_zone_naming_a_plain_atmosphere_makes_it_night(self):
        found = {'atmosphere/a/a.atmosphere': ['levels/x/atm_a.zone'], 'atmosphere/b/b.atmosphere': ['levels/x/atm_b_night.zone']}
        self.assertEqual(sky.choose_atmosphere(found), ('atmosphere/a/a.atmosphere', False))

    def test_ambiguous_day_atmospheres_raise(self):
        with self.assertRaises(ValueError):
            sky.choose_atmosphere({'atmosphere/a/a.atmosphere': ['levels/x/atm_a.zone'], 'atmosphere/b/b.atmosphere': ['levels/x/atm_b.zone']})
        with self.assertRaises(ValueError):
            sky.choose_atmosphere({'atmosphere/a/a_night.atmosphere': ['levels/x/atm_a.zone'], 'atmosphere/b/b.atmosphere': ['levels/x/atm_b_night.zone']})


class LevelSkyTests(unittest.TestCase):
    def test_veldin_like_level_uses_the_day_atmosphere_and_lighting_volumes(self):
        level = Level()
        result = level.run()
        self.assertEqual(result['atmosphere'], DAY_ATM)
        self.assertFalse(result['night'])
        self.assertEqual(result['reflection_source'], 'lighting volume')
        # No position: the volume that covers most lightgrid tiles (0xB holds two of the three tiles).
        self.assertEqual(result['reflection'], capture_path('lgt', 0xB))
        self.assertEqual(result['reflection_volume']['candidates'], [0xA, 0xB])
        self.assertNotIn(LGT_NIGHT, level.read)                    # night lighting is not read for the day atmosphere
        self.assertEqual(level.profiles, [capture_path('lgt', 0xB)])

    def test_position_selects_the_containing_volume(self):
        result = Level().run(position=(2.0, 1.0, -3.0))
        self.assertEqual(result['reflection'], capture_path('lgt', 0xA))

    def test_clear_sky_falls_back_to_another_volume_when_the_chosen_one_sees_no_sky(self):
        level = Level()
        result = level.run(position=(0, 0, 0), profile_result=lambda p: None if p.endswith('0xa/reflection.texture') else [[0.2, 0.3, 0.5]])
        self.assertEqual(result['reflection'], capture_path('lgt', 0xA))
        self.assertEqual(level.profiles, [capture_path('lgt', 0xA), capture_path('lgt', 0xB)])
        self.assertEqual(result['clear_sky'], [[0.2, 0.3, 0.5]])
        self.assertTrue(any('sampled from' in n for n in result['notes']))

    def test_night_only_level_reads_the_night_lighting_zone(self):
        level = Level()
        result = level.run(zones=[NIGHT_ZONE, LGT_NIGHT, LGT])
        self.assertEqual(result['atmosphere'], NIGHT_ATM)
        self.assertTrue(result['night'])
        self.assertEqual(result['reflection'], capture_path('lgt_night', 0xC))

    def test_atmosphere_zone_capture_takes_precedence_and_skips_the_lighting_zones(self):
        capture = capture_path('atmosphere', 0x1)
        level = Level(archive_paths=[capture], zones={'levels/Novalis/atmosphere.zone': atmosphere_zone('atmosphere/atm_Novalis/atm_Novalis.atmosphere', capture)})
        result = level.run(zones=['levels/Novalis/atmosphere.zone', 'levels/Novalis/openArea/OpenWorld/default_region/lighting.zone'])
        self.assertEqual(result['reflection'], capture)
        self.assertEqual(result['reflection_source'], 'atmosphere zone')
        self.assertNotIn('reflection_volume', result)
        self.assertNotIn('levels/Novalis/openArea/OpenWorld/default_region/lighting.zone', level.read)

    def test_unresolved_levels_keep_no_reflection(self):
        # A volume whose capture is not in the archive is not a candidate.
        result = Level(archive_paths=[]).run()
        self.assertIsNone(result['reflection'])
        self.assertIsNone(result['reflection_source'])
        self.assertIsNone(result['clear_sky'])
        # Several volumes, no tiles, no position: ambiguous, so none.
        level = Level()
        level.zones[LGT] = zone([record((0, 0, 0), (10, 10, 10), 0xA), record((100, 0, 0), (10, 10, 10), 0xB)])
        self.assertIsNone(level.run()['reflection'])
        # No lighting zone at all.
        self.assertIsNone(Level().run(zones=[DAY_ZONE])['reflection'])

    def test_no_atmosphere_zone_gives_no_sky(self):
        self.assertIsNone(Level().run(zones=[LGT, 'levels/Veldin/openArea/OpenWorld/default_region/gp_base.zone']))

    def test_two_day_atmospheres_still_raise(self):
        level = Level()
        level.zones['levels/Veldin/atm_other.zone'] = atmosphere_zone('atmosphere/atm_Other/atm_Other.atmosphere')
        with self.assertRaises(ValueError):
            level.run(zones=ZONES + ['levels/Veldin/atm_other.zone'])


if __name__ == '__main__':
    unittest.main()
