"""Which lighting zone feeds the lightgrid and how its tables decode (rc2016.levels.lightgrid; synthetic zones, no game data)."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from rc2016.levels import lightgrid as lg
from rc2016.pipeline.lighting import NOVALIS_LIGHTING_ZONE, ZONE_GRID_COORDINATES, ZONE_GRID_PATHS
from tests.test_level_volumes import dat1

REGION = 'levels/veldin/openarea/openworld/default_region/day'
LGT = 'levels/Veldin/openArea/OpenWorld/default_region/lgt.zone'
LGT_NIGHT = 'levels/Veldin/openArea/OpenWorld/default_region/lgt_night.zone'


def grid_zone(coordinates, region=REGION, paths=None, ordinals=None, backslashes=True):
    """A lighting zone whose tables list ``grid_<x>_<y>_<z>.lightgrid`` paths for ``coordinates``."""
    texts = paths or [f'{region}/grid_{x}_{y}_{z}.lightgrid' for x, y, z in coordinates]
    strings, offsets = b'', []
    for text in texts:
        offsets.append(len(strings))
        strings += (text.replace('/', '\\') if backslashes else text).encode() + b'\0'
    coords = b''.join(struct.pack('<3fI', *c, i if ordinals is None else ordinals[i]) for i, c in enumerate(coordinates))
    # The path strings follow the container; the table holds their absolute file offsets.
    base = len(dat1({ZONE_GRID_PATHS: b'\0' * 4 * len(texts), ZONE_GRID_COORDINATES: coords}))
    table = struct.pack(f'<{len(offsets)}I', *[base + o for o in offsets])
    return dat1({ZONE_GRID_PATHS: table, ZONE_GRID_COORDINATES: coords}) + strings


class ResolveEverything:
    def __call__(self, path):
        return [hash(path) % 100000]


class ZoneGridMapTests(unittest.TestCase):
    def test_rows_follow_the_tables(self):
        coordinates = [(8, 24, -8), (24, 24, -8), (-8, 8, 40)]
        data = grid_zone(coordinates)
        rows = lg.zone_grid_map(data, ResolveEverything())
        self.assertEqual([r['coordinates'] for r in rows], [list(c) for c in coordinates])
        self.assertEqual(rows[0]['path'], f'{REGION}/grid_8_24_-8.lightgrid')
        self.assertEqual([r['index'] for r in rows], [0, 1, 2])

    def test_disagreements_raise(self):
        resolve = ResolveEverything()
        with self.assertRaises(ValueError):   # stored coordinate differs from the path
            lg.zone_grid_map(grid_zone([(8, 8, 8)], paths=[f'{REGION}/grid_8_8_24.lightgrid']), resolve)
        with self.assertRaises(ValueError):   # broken ordinal
            lg.zone_grid_map(grid_zone([(8, 8, 8), (24, 8, 8)], ordinals=[0, 5]), resolve)
        with self.assertRaises(ValueError):   # not a grid path
            lg.zone_grid_map(grid_zone([(8, 8, 8)], paths=[f'{REGION}/other.lightgrid']), resolve)
        with self.assertRaises(ValueError):   # duplicate coordinate
            lg.zone_grid_map(grid_zone([(8, 8, 8), (8, 8, 8)]), resolve)
        with self.assertRaises(ValueError):   # a path the archive cannot resolve
            lg.zone_grid_map(grid_zone([(8, 8, 8)]), lambda path: [])
        with self.assertRaises(ValueError):   # two regions in one zone
            lg.zone_grid_map(grid_zone([(8, 8, 8), (24, 8, 8)], paths=[f'{REGION}/grid_8_8_8.lightgrid', 'levels/veldin/night/grid_24_8_8.lightgrid']), resolve)
        with self.assertRaises(ValueError):   # no tables
            lg.zone_grid_map(dat1({}), resolve)


class DayLightingZoneTests(unittest.TestCase):
    def test_novalis_zone_wins_when_listed(self):
        zones = ['levels/Novalis/atmosphere.zone', '?\\' + NOVALIS_LIGHTING_ZONE.replace('/', '\\').upper()]
        self.assertEqual(lg.day_lighting_zone(zones), NOVALIS_LIGHTING_ZONE)
        self.assertEqual(lg.day_lighting_zone(zones, night=True), NOVALIS_LIGHTING_ZONE)

    def test_day_and_night_zones_of_other_levels(self):
        zones = [LGT_NIGHT, 'levels/Veldin/atm_lgt.zone', LGT, 'levels/Veldin/openArea/OpenWorld/default_region/gp_base.zone']
        self.assertEqual(lg.day_lighting_zone(zones), LGT)
        self.assertEqual(lg.day_lighting_zone(zones, night=True), LGT_NIGHT)

    def test_no_or_several_candidates_give_none(self):
        self.assertIsNone(lg.day_lighting_zone(['levels/x/atmosphere.zone']))
        self.assertIsNone(lg.day_lighting_zone([LGT_NIGHT]))
        self.assertIsNone(lg.day_lighting_zone([LGT, 'levels/Veldin/other/lighting.zone']))
        self.assertIsNone(lg.day_lighting_zone(['levels/x/lighting_boss.zone']))


class LevelLightgridTests(unittest.TestCase):
    def test_no_lighting_zone_is_none(self):
        self.assertIsNone(lg.level_lightgrid(object(), ['levels/x/atmosphere.zone']))

    def test_a_lighting_zone_without_grid_tables_gives_none(self):
        with mock.patch('rc2016.levels.scene.asset_bytes', return_value=dat1({})):
            self.assertIsNone(lg.level_lightgrid(object(), [LGT]))

    def test_other_levels_read_their_zone_and_cache_under_a_zone_name(self):
        rows = [dict(index=i, path=f'{REGION}/grid_{c[0]}_{c[1]}_{c[2]}.lightgrid', coordinates=list(c), asset_id=i)
                for i, c in enumerate([(8, 8, 8), (24, 8, 8)])]
        archive = mock.Mock()
        archive.entries = {0: dict(loose_path=None, occurrences=[dict(archive='a', offset=0, size=1, available=True)]),
                           1: dict(occurrences=[dict(archive='a', offset=1, size=1, available=True)])}
        archive.entries[0].pop('loose_path')
        cells = np.zeros((lg.CELLS_PER_TILE, lg.CELL_BYTES), np.uint8)
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(lg, 'CACHE_DIR', Path(folder)), \
                mock.patch('rc2016.levels.scene.asset_bytes', return_value=dat1({ZONE_GRID_PATHS: b'', ZONE_GRID_COORDINATES: b''})) as read, \
                mock.patch.object(lg, 'zone_grid_map', return_value=rows) as grid_map, \
                mock.patch.object(lg, '_tile_cells', return_value=(cells, ['probe.texture'])):
            grid = lg.level_lightgrid(archive, [LGT_NIGHT, LGT])
            self.assertEqual(grid.tile_count, 2)
            self.assertEqual(grid.probes, [['probe.texture']] * 2)
            read.assert_called_once_with(archive, LGT)
            grid_map.assert_called_once()
            files = sorted(p.name for p in Path(folder).iterdir())
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].startswith('zone_'))
            again = lg.level_lightgrid(archive, [LGT])      # warm: tiles and probe lists come from the cache
            self.assertEqual(again.tile_count, 2)
            self.assertEqual(again.probes, [['probe.texture']] * 2)
            read.assert_called_with(archive, LGT)

    def test_novalis_keeps_its_decoder_and_cache_name(self):
        rows = [dict(index=0, path='levels/novalis/openarea/openworld/day/grid_8_8_8.lightgrid', coordinates=[8, 8, 8], asset_id=0)]
        archive = mock.Mock()
        archive.entries = {0: dict(occurrences=[dict(archive='a', offset=0, size=1, available=True)])}
        cells = np.zeros((lg.CELLS_PER_TILE, lg.CELL_BYTES), np.uint8)
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(lg, 'CACHE_DIR', Path(folder)), \
                mock.patch('rc2016.levels.scene.asset_bytes', return_value=b'zone') as read, \
                mock.patch('rc2016.pipeline.lighting._decode_lighting_zone_grid_map', return_value=dict(grids=rows)) as novalis, \
                mock.patch.object(lg, 'zone_grid_map') as generic, mock.patch.object(lg, '_tile_cells', return_value=(cells, [])):
            grid = lg.level_lightgrid(archive, ['levels/Novalis/atmosphere.zone', NOVALIS_LIGHTING_ZONE], night=True)
            self.assertEqual(grid.tile_count, 1)
            novalis.assert_called_once()
            generic.assert_not_called()
            read.assert_called_once_with(archive, NOVALIS_LIGHTING_ZONE)
            self.assertTrue(next(Path(folder).iterdir()).name.startswith('novalis_'))


if __name__ == '__main__':
    unittest.main()
