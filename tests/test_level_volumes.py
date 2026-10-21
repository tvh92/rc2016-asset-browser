"""Reflection-capture volume records of lighting zones (rc2016.levels.volumes; synthetic zones, no game data)."""
import struct
import unittest

from rc2016.levels import volumes


def dat1(sections):
    """A DAT1 container with the given {label: bytes} sections (the layout of rc2016.levels.zone_format.sections)."""
    table = 16 + 12 * len(sections)
    body, entries = b'', []
    for label, payload in sections.items():
        entries.append(struct.pack('<III', label, table + len(body), len(payload)))
        body += payload + b'\0' * (-len(payload) % 4)
    return b'1TAD' + b'\0' * 8 + struct.pack('<I', len(sections)) + b''.join(entries) + body


def record(centre, half, texture_id, flags=0x10020, path=None, matrix=None):
    """One volume record: axis-aligned box of half extents ``half`` at ``centre`` (row-vector matrix)."""
    m = matrix or (half[0], 0, 0, 0, 0, half[1], 0, 0, 0, 0, half[2], 0, *centre, 1)
    text = path if path is not None else f'\\textures\\lgt\\volumereflections\\0x{texture_id:x}\\reflection.texture'
    size = 0x70 + len(text) + 1
    size += -size % 8
    body = bytearray(size)
    struct.pack_into('<16f', body, 0, *m)
    struct.pack_into('<II', body, 0x44, size, flags)      # +0x44 record size, +0x48 flag word
    struct.pack_into('<Q', body, 0x50, texture_id)
    body[0x70:0x70 + len(text)] = text.encode()
    return bytes(body)


def zone(records, tiles=()):
    heap, offsets = b'', []
    for rec in records:
        offsets.append(len(heap))
        heap += rec
    sections = {volumes.HEAP: heap, volumes.VOLUME_INDEX: struct.pack(f'<{len(offsets)}I', *offsets)}
    if tiles:
        sections[volumes.GRID_COORDINATES] = b''.join(struct.pack('<3fI', *t, i) for i, t in enumerate(tiles))
    return dat1(sections)


class RecordTests(unittest.TestCase):
    def test_reflection_records_are_read_and_other_flags_skipped(self):
        data = zone([record((10, 20, 30), (2, 3, 4), 0xAB), record((0, 0, 0), (1, 1, 1), 0xCD, flags=0x8),
                     record((50, 0, 0), (5, 5, 5), 0xEF, flags=0x20)])
        found = volumes.reflection_volumes(data)
        self.assertEqual([v['index'] for v in found], [0, 2])
        first = found[0]
        self.assertEqual(first['id'], 0xAB)
        self.assertEqual(first['path'], 'textures/lgt/volumereflections/0xab/reflection.texture')
        self.assertEqual(first['centre'], (10.0, 20.0, 30.0))
        self.assertEqual(first['half_extents'], (2.0, 3.0, 4.0))
        self.assertEqual(first['volume'], 8.0 * 2 * 3 * 4)

    def test_zone_without_volumes_or_with_bad_records(self):
        self.assertEqual(volumes.reflection_volumes(dat1({})), [])
        self.assertEqual(volumes.reflection_volumes(dat1({volumes.HEAP: b'\0' * 8})), [])
        singular = record((0, 0, 0), (1, 1, 1), 1, matrix=(0,) * 12 + (0, 0, 0, 1))
        not_texture = record((0, 0, 0), (1, 1, 1), 2, path='\\textures\\other\\thing.texture')
        nan = record((0, 0, 0), (1, 1, 1), 3, matrix=(float('nan'),) + (0,) * 11 + (0, 0, 0, 1))
        self.assertEqual(volumes.reflection_volumes(zone([singular, not_texture, nan])), [])

    def test_truncated_record_is_skipped(self):
        short = dat1({volumes.HEAP: record((0, 0, 0), (1, 1, 1), 1)[:0x60], volumes.VOLUME_INDEX: struct.pack('<I', 0)})
        self.assertEqual(volumes.reflection_volumes(short), [])
        # A record whose size field runs past the heap is skipped too.
        cut = record((0, 0, 0), (1, 1, 1), 1)
        self.assertEqual(volumes.reflection_volumes(dat1({volumes.HEAP: cut[:-16], volumes.VOLUME_INDEX: struct.pack('<I', 0)})), [])

    def test_grid_centres(self):
        data = zone([], tiles=[(8, 8, 8), (24, -8, 40)])
        self.assertEqual(volumes.grid_centres(data), [(8.0, 8.0, 8.0), (24.0, -8.0, 40.0)])
        self.assertEqual(volumes.grid_centres(zone([record((0, 0, 0), (1, 1, 1), 1)])), [])


class ContainmentTests(unittest.TestCase):
    def test_box_contains_in_its_rotated_frame(self):
        # A box rotated 45 degrees about Y, half extents 10 along its own axes.
        c = s = 0.5 ** 0.5
        matrix = (10 * c, 0, -10 * s, 0, 0, 10, 0, 0, 10 * s, 0, 10 * c, 0, 100, 0, 0, 1)
        volume = volumes.reflection_volumes(zone([record((0, 0, 0), (0, 0, 0), 1, matrix=matrix)]))[0]
        self.assertTrue(volumes.contains(volume, (100, 0, 0)))
        self.assertTrue(volumes.contains(volume, (100 + 9 * c, 9, 9 * s)))
        self.assertFalse(volumes.contains(volume, (100 + 11 * c, 0, 11 * s)))
        self.assertFalse(volumes.contains(volume, (100, 10.5, 0)))
        self.assertTrue(volumes.contains(volume, (113, 0, 0)))    # the rotated box reaches 14.1 along x ...
        self.assertFalse(volumes.contains(volume, (115, 0, 0)))   # ... and no further


class ChooseTests(unittest.TestCase):
    def setUp(self):
        self.near = volumes.reflection_volumes(zone([record((0, 0, 0), (10, 10, 10), 1)]))[0]
        self.far = volumes.reflection_volumes(zone([record((100, 0, 0), (10, 10, 10), 2)]))[0]
        self.big = volumes.reflection_volumes(zone([record((0, 0, 0), (30, 30, 30), 3)]))[0]

    def test_nothing_and_a_single_volume(self):
        self.assertIsNone(volumes.choose_reflection([]))
        self.assertIs(volumes.choose_reflection([self.far]), self.far)
        self.assertIs(volumes.choose_reflection([self.far], position=(0, 0, 0)), self.far)   # outside: the default applies

    def test_position_picks_the_containing_volume_and_the_smallest_of_several(self):
        pool = [self.near, self.far, self.big]
        self.assertIs(volumes.choose_reflection(pool, [(100, 0, 0)] * 5, position=(1, 1, 1)), self.near)
        self.assertIs(volumes.choose_reflection(pool, position=(100, 0, 0)), self.far)
        self.assertIs(volumes.choose_reflection([self.big, self.near], position=(0, 0, 0)), self.near)

    def test_default_is_the_volume_covering_most_grid_tiles(self):
        centres = [(100, 0, 0), (104, 0, 0), (0, 0, 0)]
        self.assertIs(volumes.choose_reflection([self.near, self.far], centres), self.far)
        self.assertIs(volumes.choose_reflection([self.far, self.near], centres), self.far)
        # A position outside every volume falls back to that default.
        self.assertIs(volumes.choose_reflection([self.near, self.far], centres, position=(500, 0, 0)), self.far)

    def test_tie_takes_the_earlier_and_no_tiles_means_no_default(self):
        self.assertIs(volumes.choose_reflection([self.near, self.far], [(0, 0, 0), (100, 0, 0)]), self.near)
        self.assertIs(volumes.choose_reflection([self.far, self.near], [(0, 0, 0), (100, 0, 0)]), self.far)
        self.assertIsNone(volumes.choose_reflection([self.near, self.far], [(500, 0, 0)]))
        self.assertIsNone(volumes.choose_reflection([self.near, self.far]))


if __name__ == '__main__':
    unittest.main()
