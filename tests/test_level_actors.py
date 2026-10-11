"""Actor layout invariants and source pointers; synthetic plus gated corpus evidence."""
import json
import os
from pathlib import Path
import struct
import unittest

from rc2016.levels import actor_format as af
from rc2016.formats.container import game_crc32


def container(bodies, strings):
    base = 16 + 12 * len(bodies)
    pool, offsets = bytearray(), {}
    for text in strings:
        offsets[text] = base + len(pool)
        pool.extend(text.encode('utf-8') + b'\0')
    pool.extend(b'\0' * (-(base + len(pool)) % 16))
    cursor = base + len(pool)
    table, payload = bytearray(), bytearray()
    for tag, make_body in bodies:
        body = make_body(offsets) if callable(make_body) else make_body
        table.extend(struct.pack('<III', tag, cursor, len(body)))
        payload.extend(body)
        pad = -len(body) % 16
        payload.extend(b'\0' * pad)
        cursor += len(body) + pad
    return b'1TAD' + struct.pack('<III', 0, cursor, len(bodies)) + table + pool + payload


def render_record(aid=1, matrix=af.IDENTITY, size=0xB0):
    body = bytearray(size)
    struct.pack_into('<16f', body, 0, *matrix)
    struct.pack_into('<I', body, 0x44, size)
    struct.pack_into('<Q', body, 0x48, aid)
    struct.pack_into('<I', body, 0x58, 0xFFFF)
    if size >= 0xB0:
        struct.pack_into('<I', body, 0xA4, 0xFFFFFFFF)
    return bytes(body)


def actor(primary='env/crate.model'):
    # A secondary model string is an explicitly indexed dependency, never the primary.
    return container([(af.PRIMARY_MODEL, lambda o: struct.pack('<I', o[primary])),
                      (af.ACTOR_RECORD, render_record()),
                      (af.ACTOR_REFERENCES, lambda o: struct.pack('<I', o['env/broken.model']))],
                     [primary, 'env/broken.model'])


def zone(records=None, name_index=0, type_index=0, first=0, count=0, flags=8, defs=b''):
    records = records or [render_record()]
    heap = b''.join(records)
    return container([(af.ACTORS, b''.join(struct.pack('<6IQ', name_index, type_index,
                      sum(len(r) for r in records[:i]), first, count, flags, i+1)
                      for i in range(len(records)))),
                      (af.ACTOR_NAMES, lambda o: struct.pack('<I', o['Crate'])),
                      (af.ACTOR_TYPES, lambda o: struct.pack('<I', o['env/crate.actor'])),
                      (af.DATA_HEAP, heap), (af.ZONE_COMPONENTS, defs)],
                     ['Crate', 'env/crate.actor'])


def change(data, tag, offset, fmt, *values):
    data = bytearray(data)
    table = af._table(data)
    struct.pack_into(fmt, data, table[tag][0] + offset, *values)
    return bytes(data)


class ActorDescriptorTests(unittest.TestCase):
    def test_primary_pointer_is_distinct_from_breakable_dependencies(self):
        decoded = af.decode_actor(actor())
        self.assertEqual(decoded['primary_model'], 'env/crate.model')
        self.assertEqual(decoded['dependencies'][0]['path'], 'env/broken.model')
        self.assertEqual(decoded['primary_reference']['section'], '32FAC8E0')
        self.assertEqual(decoded['matrix_rows'][3], [0., 0., 0., 1.])

    def test_empty_primary_does_not_fall_back_to_a_dependency(self):
        self.assertIsNone(af.decode_actor(actor(''))['primary_model'])

    def test_invalid_primary_and_string_suffix_raise(self):
        with self.assertRaisesRegex(ValueError, 'not a model'):
            af.decode_actor(actor('env/child.actor'))
        data = actor()
        pointer = struct.unpack_from('<I', data, af._table(data)[af.PRIMARY_MODEL][0])[0]
        with self.assertRaisesRegex(ValueError, 'string-pool start'):
            af.decode_actor(change(data, af.PRIMARY_MODEL, 0, '<I', pointer+1))

    def test_bad_dat1_bounds_duplicate_section_and_record_size_raise(self):
        with self.assertRaises(ValueError):
            af.decode_actor(actor()[:14])
        data = bytearray(actor())
        struct.pack_into('<I', data, 16+12, af.PRIMARY_MODEL)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            af.decode_actor(data)
        with self.assertRaisesRegex(ValueError, 'size does not match'):
            af.decode_actor(change(actor(), af.ACTOR_RECORD, 0x44, '<I', 0xC0))


class ZoneActorTests(unittest.TestCase):
    def test_shear_mirror_and_translation_are_retained_without_trs(self):
        matrix = (-1., .4, 0., 0., 0., 2., 0., 0., 0., .2, 1., 0., 4., 5., 6., 1.)
        a = af.decode_zone_actors(zone([render_record(matrix=matrix)]))['actors'][0]
        self.assertAlmostEqual(a['matrix_rows'][0][1], .4)
        self.assertEqual(a['matrix_rows'][0][0], -1.)
        self.assertEqual(a['matrix_rows'][3], [4., 5., 6., 1.])
        self.assertEqual(a['flags_raw'], 8)
        self.assertEqual(a['id'], '0000000000000001')

    def test_missing_name_sentinel_is_allowed(self):
        self.assertIsNone(af.decode_zone_actors(zone(name_index=af.NO_INDEX))['actors'][0]['name'])

    def test_indices_id_size_alignment_and_matrix_invariants_raise(self):
        for data in (zone(name_index=1), zone(type_index=1), zone(first=1, count=1),
                     change(zone(), af.DATA_HEAP, 0x48, '<Q', 3),
                     change(zone(), af.DATA_HEAP, 0x44, '<I', 0xC0),
                     change(zone(), af.ACTORS, 8, '<I', 1),
                     change(zone(), af.DATA_HEAP, 12, '<f', 1.),
                     change(zone(), af.DATA_HEAP, 0, '<f', float('nan')),
                     change(zone(), af.DATA_HEAP, 0x58, '<I', 0)):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    af.decode_zone_actors(data)

    def test_nonrender_headers_are_not_interpreted_as_render_ids(self):
        body = bytearray(render_record(size=0x70))
        struct.pack_into('<I', body, 0x40, 2)
        struct.pack_into('<Q', body, 0x48, 0x100000001)
        a = af.decode_zone_actors(zone([bytes(body)]))['actors'][0]
        self.assertEqual(a['record_kind'], 2)
        self.assertEqual(a['material_overrides'], [])

    def test_component_crc_and_ownership_fail_closed(self):
        # Name pointer references the known pool string, but the hash is deliberately wrong.
        defs = struct.pack('<4I', 16+12*5, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, 'CRC'):
            af.decode_zone_actors(zone(count=1, defs=defs))
        defs = struct.pack('<4I', 16+12*5, game_crc32('Crate'), 0, 0)
        with self.assertRaisesRegex(ValueError, 'owned exactly once'):
            af.decode_zone_actors(zone(defs=defs))
        a = af.decode_zone_actors(zone(count=1, defs=defs))['actors'][0]
        self.assertEqual(a['components'][0]['name'], 'Crate')

    def test_duplicate_ids_raise(self):
        data = zone([render_record(1), render_record(2)])
        with self.assertRaisesRegex(ValueError, 'duplicate id'):
            af.decode_zone_actors(change(data, af.ACTORS, 32+24, '<Q', 1))


class ComponentReaderTests(unittest.TestCase):
    def component(self, raw):
        data = container([(af.ACTOR_COMPONENT_DATA, raw)], ['Field'])
        off, size = af._table(data)[af.ACTOR_COMPONENT_DATA]
        return data, dict(offset=off, size=size, payload_section='6D4301EF')

    def test_unparsed_tail_is_preserved_with_absolute_source_offset(self):
        data, component = self.component(struct.pack('<4I', 0, 0x03150044, 0, 4) + b'abcd')
        parsed = af.read_component(data, component)
        self.assertEqual(parsed['value'], {})
        self.assertEqual(parsed['skipped_bytes'], 4)
        self.assertEqual(parsed['unparsed_ranges'], [dict(offset=component['offset']+16, size=4, hex='61626364')])

    def test_extra_objects_are_reported(self):
        empty = struct.pack('<4I', 0, 0x03150044, 0, 0)
        data, component = self.component(empty + empty)
        self.assertEqual(af.read_component(data, component)['extras'], [{}])

    def test_unknown_node_type_raises_in_existing_guarded_reader(self):
        raw = struct.pack('<4I', 0, 0x03150044, 1, 12) + struct.pack('<IHBBI', 0, 16, 0, 255, 28)
        data, component = self.component(raw)
        with self.assertRaisesRegex(ValueError, 'Unsupported serialized node type'):
            af.read_component(data, component)

    def test_component_payload_range_is_checked(self):
        data, component = self.component(struct.pack('<4I', 0, 0x03150044, 0, 0))
        component['size'] += 1
        with self.assertRaisesRegex(ValueError, 'outside payload section'):
            af.read_component(data, component)


class ActorCorpusTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'requires research workspace')
    def test_recorded_corpus_and_live_airlock_control(self):
        from rc2016.research.source import Corpus
        from rc2016.levels.scene import asset_bytes
        root = Path(os.environ['RC2016_RESEARCH'])
        report = root / 'Animation/pipeline/Research/Levels/Actors/out/corpus_validation.json'
        if not report.is_file():
            self.skipTest('actor corpus evidence not available')
        r = json.loads(report.read_text(encoding='utf-8'))
        self.assertEqual(r['invariant_failures'], [])
        self.assertEqual(r['archive_errors'], {})
        with Corpus(r['root'], root / 'Animation/pipeline/Research/Levels/Actors/out', extensions=('.zone',)).opened() as corpus:
            zone_path = 'levels/nebula/openarea/openworld/default_region/gp_base.zone'
            data = asset_bytes(corpus.archive, zone_path)
            actors = af.decode_zone_actors(data)['actors']
            control = next(a for a in actors if a['name'] == 'AIRLOCK_TRESPASSER_1')
            recorded = next(z for z in r['zones'] if z['path'] == zone_path)
            expected = next(a for a in recorded['actors'] if a['id'] == control['id'])
            self.assertEqual(control['matrix_rows'], expected['matrix_rows'])
            desc = af.decode_actor(asset_bytes(corpus.archive, control['actor_path']))
            self.assertEqual(desc['primary_model'], expected['primary_model'])
            bad = [d for d in r['placement_controls'] if d['actor'] == 'AIRLOCK_TRESPASSER_1']
            self.assertEqual(len(bad), 1)
            self.assertGreater(bad[0]['max_matrix_delta'], 1.)


if __name__ == '__main__':
    unittest.main()
