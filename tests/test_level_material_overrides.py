"""Per-instance material overrides in zone instance records: decoding, slot resolution, grouping and drawing."""
from array import array
from pathlib import Path
import struct
import sys
import unittest

from rc2016.levels import scene
from rc2016.levels.viewport import draw_items
from rc2016.levels.zone_format import (OVERRIDE_MATERIALS, DATA_HEAP, INSTANCE_OFFSETS, MODEL_PATHS,
                                       decode_zone, name_crc32, path_crc64)

GLASS = 'material/env/glass_default.material'
RED = 'material\\env\\Variants\\glass_red.material'
MATERIALS = [dict(index=0, name='frame_metal', path='material/env/frame.material'),
             dict(index=1, name='Window_Glass', path=GLASS)]


def record(model_index, instance_id, overrides=(), x=0.0, size=None):
    """One instance record: identity matrix at x, optional override block right after the header."""
    body = bytearray(0xB0)
    struct.pack_into('<16f', body, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, 0, 0, 1)
    struct.pack_into('<QII', body, 0x48, instance_id, 0, 0)
    struct.pack_into('<II', body, 0x58, model_index, 0x440000)
    struct.pack_into('<I', body, 0xA0, 0xFFFFFFFF)
    struct.pack_into('<I', body, 0xA4, 0xB0 if overrides else 0xFFFFFFFF)
    struct.pack_into('<I', body, 0xA8, 0xFFFFFFFF)
    body[0x99] = len(overrides)
    for slot, material in overrides:
        body += struct.pack('<IQ', slot, material)
    struct.pack_into('<I', body, 0x44, size or len(body))
    return bytes(body) + b'\0' * (-len(body) % 8)


def zone(records, models=('environment/env/window.model',), materials=(RED,)):
    """A minimal DAT1 zone with model paths, instance records and the replacement material list."""
    strings, offsets = bytearray(), {}
    tags = [MODEL_PATHS, INSTANCE_OFFSETS, DATA_HEAP, OVERRIDE_MATERIALS]
    base = 16 + 12 * len(tags)
    for text in (*models, *materials):
        offsets[text] = base + len(strings)
        strings += text.encode('ascii') + b'\0'
    strings += b'\0' * (-len(strings) % 16)
    heap, rel = bytearray(), []
    for r in records:
        rel.append(len(heap)); heap += r
    bodies = [struct.pack(f'<{len(models)}I', *(offsets[m] for m in models)),
              struct.pack(f'<{len(rel)}I', *rel), bytes(heap),
              b''.join(struct.pack('<II', offsets[m], 0xFFFFFFFF) for m in materials)]
    table, data, cursor = bytearray(), bytearray(), base + len(strings)
    for tag, body in zip(tags, bodies):
        table += struct.pack('<III', tag, cursor, len(body))
        data += body + b'\0' * (-len(body) % 16)
        cursor += len(body) + (-len(body) % 16)
    return b'1TAD' + struct.pack('<III', 0, cursor, len(tags)) + bytes(table) + bytes(strings) + bytes(data)


class HashTests(unittest.TestCase):
    def test_hashes_match_dat1lib(self):
        root = str(Path(__file__).resolve().parents[1] / 'third_party' / 'ALERT')
        sys.path.insert(0, root)
        try:
            from dat1lib.crc32 import hash as crc32
            from dat1lib.crc64 import hash as crc64
        finally:
            sys.path.remove(root)
        for text in ('Window_Glass', 'kal_arch_glow_inactive', RED, 'material//env\\x.material'):
            self.assertEqual(name_crc32(text), crc32(text))
            self.assertEqual(path_crc64(text), crc64(text))
        self.assertEqual(path_crc64(RED), path_crc64(RED.lower().replace('\\', '/')))


class DecodeTests(unittest.TestCase):
    def test_override_block_resolves_against_zone_list(self):
        data = zone([record(0, 1), record(0, 2, [(name_crc32('Window_Glass'), path_crc64(RED))], x=5),
                     record(0, 3, [(name_crc32('old_slot'), 0), (0x1234, 0x8000000000000001)], x=9)])
        decoded = decode_zone(data)
        self.assertEqual(decoded['override_materials'], ['material/env/Variants/glass_red.material'])
        plain, red, stale = decoded['instances']
        self.assertEqual(plain['material_overrides'], [])
        self.assertEqual(red['material_overrides'], [dict(slot_hash=f"{name_crc32('Window_Glass'):08X}",
                         material_hash=f'{path_crc64(RED):016X}', material='material/env/Variants/glass_red.material')])
        self.assertEqual([o['material'] for o in stale['material_overrides']], [None, None])

    def test_block_outside_record_or_count_without_block_raise(self):
        bad = bytearray(record(0, 1, [(1, 2)]))
        struct.pack_into('<I', bad, 0xA4, 0xB8)   # 12-byte entry would pass the record end
        with self.assertRaisesRegex(ValueError, 'outside the record'):
            decode_zone(zone([bytes(bad)]))
        bad = bytearray(record(0, 1)); bad[0x99] = 1
        with self.assertRaisesRegex(ValueError, 'without a block'):
            decode_zone(zone([bytes(bad)]))
        empty = bytearray(record(0, 1)); struct.pack_into('<I', empty, 0xA4, 0)   # zero count: nothing to read
        self.assertEqual(decode_zone(zone([bytes(empty)]))['instances'][0]['material_overrides'], [])

    def test_zone_without_list_still_decodes(self):
        decoded = decode_zone(zone([record(0, 1)], materials=()))
        self.assertEqual(decoded['override_materials'], [])


def entry(slot_name, path, material_hash=None):
    return (f'{name_crc32(slot_name):08X}', path,
            f'{(material_hash if material_hash is not None else path_crc64(path or "x")):016X}')


class ResolveTests(unittest.TestCase):
    def test_unique_slot_applies(self):
        applied, outcomes = scene.resolve_overrides(MATERIALS, (entry('window_glass', RED),))
        self.assertEqual(applied, ((1, RED),)); self.assertEqual(outcomes, [scene.APPLIED])

    def test_missing_null_and_unlisted_are_not_applied(self):
        overrides = (entry('renamed_slot', RED), entry('Window_Glass', None, 0), entry('Window_Glass', None, 99))
        applied, outcomes = scene.resolve_overrides(MATERIALS, overrides)
        self.assertEqual(applied, ())
        self.assertEqual(outcomes, [scene.MISSING_SLOT, scene.NULL_MATERIAL, scene.UNLISTED_MATERIAL])

    def test_ambiguous_conflicting_and_default_are_not_applied(self):
        twins = MATERIALS + [dict(index=2, name='window_glass', path=GLASS)]
        self.assertEqual(scene.resolve_overrides(twins, (entry('Window_Glass', RED),)), ((), [scene.AMBIGUOUS_SLOT]))
        other = 'material/env/glass_blue.material'
        self.assertEqual(scene.resolve_overrides(MATERIALS, (entry('Window_Glass', RED), entry('Window_Glass', other))),
                         ((), [scene.CONFLICT, scene.CONFLICT]))
        self.assertEqual(scene.resolve_overrides(MATERIALS, (entry('Window_Glass', GLASS.upper()),)), ((), [scene.SAME]))


class GroupingTests(unittest.TestCase):
    def rows(self, n):
        return array('f', [0.0] * (16 * n))

    def test_instances_group_by_effective_materials(self):
        groups = {(False, ()): self.rows(2),
                  (False, (entry('Window_Glass', RED),)): self.rows(3),
                  (False, (entry('renamed_slot', RED),)): self.rows(1),      # stale: joins the plain group
                  (True, (entry('Window_Glass', RED),)): self.rows(1)}
        result, outcomes, changed = scene.effective_groups(MATERIALS, groups)
        self.assertEqual({k: len(v) // 16 for k, v in result.items()},
                         {(False, ()): 3, (False, ((1, RED),)): 3, (True, ((1, RED),)): 1})
        self.assertEqual(changed, 4)
        self.assertEqual(outcomes, {scene.APPLIED: 4, scene.MISSING_SLOT: 1})


class DrawItemTests(unittest.TestCase):
    def model(self):
        return dict(path='window.model', slots={0: 'frame', 1: 'glass'},
                    batches=[(0, 0, 10, True), (1, 10, 4, True), (1, 14, 2, True)],
                    groups=[(11, 5, False, ()), (12, 2, False, ((1, 'red'),)), (13, 1, True, ())])

    def test_shared_geometry_with_different_effective_materials(self):
        model = self.model()
        items = draw_items([model], 2, lambda m: m['groups'])
        by_key = {}
        for key, m, span, groups in items:
            self.assertIs(m, model)   # one geometry record for every variant
            by_key.setdefault(key[0], []).append((span, [g[0] for g in groups]))
        self.assertEqual(by_key['frame'], [((0, 10), [11, 12, 13])])       # unchanged slot: one draw for all
        self.assertEqual(by_key['glass'], [((10, 6), [11, 13])])          # default instances only
        self.assertEqual(by_key['red'], [((10, 6), [12])])                # the overridden instances only

    def test_overrides_off_draws_defaults(self):
        items = draw_items([self.model()], 2, lambda m: m['groups'], use_overrides=False)
        self.assertEqual(sorted((k[0], s, len(g)) for k, _m, s, g in items), [('frame', (0, 10), 3), ('glass', (10, 6), 3)])

    def test_batches_without_uv_stay_untextured(self):
        model = self.model(); model['batches'] = [(1, 0, 4, False)]
        items = draw_items([model], 2, lambda m: m['groups'])
        self.assertEqual([(k, len(g)) for k, _m, _s, g in items], [(('', 0), 3)])


if __name__ == '__main__':
    unittest.main()
