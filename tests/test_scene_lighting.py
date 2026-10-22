"""Bounded authored-light decoding; unsupported source light semantics fail closed."""
import math
import struct
import unittest
from unittest.mock import patch

from rc2016.levels.scene_lighting import _decode_zone_lights, _decode_zone_spots, load_scene_lighting, parse_light_record


def dat1(sections):
    count = len(sections)
    offset = 16 + count * 12
    rows, payload = bytearray(), bytearray()
    for label, blob in sections:
        rows.extend(struct.pack('<III', label, offset, len(blob)))
        payload.extend(blob)
        offset += len(blob)
    return b'1TAD' + struct.pack('<IIHH', 0, offset, count, 0) + rows + payload


def light_record(source_type=1, normalization=0, radius=12.0, flags=0x100, size=0x98, shape=None, gobo=None):
    record = bytearray(size)
    struct.pack_into('<16f', record, 0, 1, 0, 0, 0, 0, 1, 0, 0,
                     0, 0, 1, 0, 10, 20, 30, 1)
    struct.pack_into('<I', record, 0x44, len(record))
    struct.pack_into('<Q', record, 0x48, 0x123456789ABCDEF0)
    struct.pack_into('<I', record, 0x50, flags)
    if shape is not None:
        record[0x50] = shape
    record[0x51] = source_type
    record[0x52] = normalization
    if gobo is not None:
        struct.pack_into('<H', record, 0x56, 0x98)
        record.extend(gobo.encode('ascii') + b'\0')
        struct.pack_into('<I', record, 0x44, len(record))
    struct.pack_into('<3f', record, 0x58, 2.0, 3.0, 4.0)
    struct.pack_into('<f', record, 0x68, radius)
    return bytes(record)


def light_zone(records):
    heap = b''.join(records)
    index = bytearray()
    offset = 0
    for record in records:
        index.extend(struct.pack('<I', offset))
        offset += len(record)
    return dat1([(0x97FF6EB5, bytes(index)), (0x06ABCAB2, heap)])


class FakeArchive:
    def __init__(self, data):
        self.entries = {'zone-id': {'id': 'zone-id', 'path': 'levels/example/point.zone', 'type': 'zone'}}
        self._data = data

    def resolve(self, path):
        return ['zone-id'] if path == 'levels/example/point.zone' else []

    def bytes(self, key):
        return self._data, 'fixture-sha'


class AuthoredLightTests(unittest.TestCase):
    def test_category_zero_point_decodes_source_position_colour_radius_and_raw_fields(self):
        lights, skipped = _decode_zone_lights(light_zone([light_record()]), 'levels/example/point.zone')
        self.assertEqual(skipped, [])
        self.assertEqual(len(lights), 1)
        light = lights[0]
        self.assertEqual(light['kind'], 'point')
        self.assertEqual(light['position'], [10.0, 20.0, 30.0])
        self.assertEqual(light['radius'], 12.0)
        self.assertEqual(light['colour'], [2.0, 3.0, 4.0])
        self.assertEqual(light['raw_flags'], 0x100)
        self.assertEqual(light['source_type'], 1)
        self.assertEqual(light['inverse_radius'], struct.unpack('<f', struct.pack('<f', 1.0 / 12.0))[0])
        self.assertEqual(light['transform'][3], [10.0, 20.0, 30.0, 1.0])

    def test_only_point_shape_and_vetted_normalization_are_emitted(self):
        lights, skipped = _decode_zone_lights(light_zone([
            light_record(shape=1), light_record(shape=2), light_record(shape=3), light_record(normalization=2),
        ]), 'levels/example/point.zone')
        self.assertEqual(lights, [])
        self.assertEqual([item['reason'] for item in skipped],
                         ['unsupported_light_shape'] * 3 + ['unsupported_normalization'])
        self.assertEqual([item.get('shape') for item in skipped[:3]], ['spot', 'projector', 'box'])

    def test_other_flag_bytes_are_marked_as_the_inferred_extension(self):
        lights, skipped = _decode_zone_lights(light_zone([
            light_record(source_type=1), light_record(source_type=9), light_record(source_type=17),
            light_record(source_type=19, normalization=1),
        ]), 'levels/example/point.zone')
        self.assertEqual([light['source_type'] for light in lights], [1, 9, 17])
        self.assertEqual([light['validated'] for light in lights], [True, False, False])
        self.assertEqual([item['reason'] for item in skipped], ['unsupported_normalization'])

    def test_load_reports_undrawn_shape_counts(self):
        archive = FakeArchive(light_zone([light_record(), light_record(shape=1), light_record(shape=1),
                                          light_record(shape=4)]))
        result = load_scene_lighting(archive, ['levels/example/point.zone'])
        self.assertEqual(len(result['lights']), 1)
        note = next(note for note in result['notes'] if note['status'] == 'undrawn_light_shapes')
        self.assertEqual(note['counts'], {'spot': 2, 'box': 1})

    def spot_record(self, inner=26.0, outer=46.0, **kwargs):
        record = bytearray(light_record(shape=1, **kwargs))
        struct.pack_into('<16f', record, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 10, 20, 30, 1)   # aims down
        struct.pack_into('<2f', record, 0x80, inner, outer)
        return bytes(record)

    def test_spots_decode_aim_range_and_the_inferred_cone(self):
        spots = _decode_zone_spots(light_zone([self.spot_record(), light_record()]), 'levels/example/point.zone')
        self.assertEqual(len(spots), 1)   # the point record is not a spot
        spot = spots[0]
        self.assertEqual((spot['kind'], spot['validated'], spot['position'], spot['radius']), ('spot', False, [10.0, 20.0, 30.0], 12.0))
        self.assertEqual(spot['axis'], [0.0, -1.0, 0.0])
        self.assertEqual(spot['cone_degrees'], [26.0, 46.0])
        self.assertEqual(spot['colour'], [2.0, 3.0, 4.0])

    def test_unusable_spots_are_left_out(self):
        spots = _decode_zone_spots(light_zone([self.spot_record(outer=0.0), self.spot_record(normalization=2),
                                               self.spot_record(outer=float('nan')), self.spot_record(outer=200.0)]),
                                   'levels/example/point.zone')
        self.assertEqual(spots, [])

    def test_inner_angle_is_clamped_to_the_outer_angle(self):
        spots = _decode_zone_spots(light_zone([self.spot_record(inner=80.0, outer=40.0)]), 'levels/example/point.zone')
        self.assertEqual(spots[0]['cone_degrees'], [40.0, 40.0])

    def test_load_draws_valid_spots_and_reports_only_the_rest_as_undrawn(self):
        archive = FakeArchive(light_zone([light_record(), self.spot_record(), light_record(shape=1), light_record(shape=4)]))
        result = load_scene_lighting(archive, ['levels/example/point.zone'])
        self.assertEqual([light['kind'] for light in result['lights']], ['point', 'spot'])
        note = next(note for note in result['notes'] if note['status'] == 'undrawn_light_shapes')
        self.assertEqual(note['counts'], {'spot': 1, 'box': 1})   # the spot without a cone is still undrawn

    def test_parse_light_record_decodes_shape_axis_scale_and_gobo(self):
        record = bytearray(light_record(source_type=11, shape=2, normalization=0, radius=9.0,
                                        gobo='textures/lighting/global/gobos/rk_gobo_soft_rect.texture'))
        struct.pack_into('<16f', record, 0, 2, 0, 0, 0, 0, 3, 0, 0, 0, 0, -4, 0, 5, 6, 7, 1)
        struct.pack_into('<f', record, 0x64, 44.0)
        struct.pack_into('<ff', record, 0x80, 11.0, 20.0)
        struct.pack_into('<Q', record, 0x90, 0xAABBCCDD11223344)
        parsed = parse_light_record(bytes(record))
        self.assertEqual((parsed['shape'], parsed['shape_name'], parsed['flags'], parsed['normalization']),
                         (2, 'projector', 11, 0))
        self.assertEqual(parsed['position'], [5.0, 6.0, 7.0])
        self.assertEqual(parsed['axis'], [0.0, 0.0, -1.0])
        self.assertEqual(parsed['scale'], [2.0, 3.0, 4.0])
        self.assertEqual((parsed['radius'], parsed['cone_degrees'], parsed['range']), (9.0, 44.0, [11.0, 20.0]))
        self.assertEqual(parsed['colour'], [2.0, 3.0, 4.0])
        self.assertEqual(parsed['link_id'], 0xAABBCCDD11223344)
        self.assertEqual(parsed['gobo'], 'textures/lighting/global/gobos/rk_gobo_soft_rect.texture')
        self.assertEqual(parsed['size'], len(record))

    def test_parse_light_record_without_extension_has_no_gobo_and_rejects_short_records(self):
        self.assertIsNone(parse_light_record(light_record())['gobo'])
        with self.assertRaisesRegex(ValueError, 'fewer than'):
            parse_light_record(light_record()[:0x90])

    def test_type_one_with_nonzero_raw_byte_50_does_not_enter_category_zero(self):
        lights, skipped = _decode_zone_lights(light_zone([light_record(flags=0x101)]),
                                               'levels/example/point.zone')
        self.assertEqual(lights, [])
        self.assertEqual(skipped[0]['raw_byte_0x50'], 1)

    def test_declared_record_extension_is_respected(self):
        lights, skipped = _decode_zone_lights(light_zone([light_record(size=0xD4)]),
                                               'levels/example/point.zone')
        self.assertEqual(skipped, [])
        self.assertEqual(lights[0]['source_record_size'], 0xD4)
        self.assertEqual(lights[0]['source_extension_size'], 0xD4 - 0x98)

    def test_zero_explicit_radius_uses_validated_luminance_rule(self):
        lights, skipped = _decode_zone_lights(light_zone([light_record(radius=0.0)]),
                                               'levels/example/point.zone')
        self.assertEqual(skipped, [])
        f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
        luminance = f32(f32(2.0 * f32(0.2126)) + f32(3.0 * f32(0.7152)))
        luminance = f32(luminance + f32(4.0 * f32(0.0722)))
        expected = f32(f32(math.sqrt(luminance)) * f32(2.0))
        self.assertEqual(lights[0]['radius'], expected)
        self.assertEqual(lights[0]['radius_rule'], 'unnormalized_luminance_radius')

    def test_public_loader_accepts_archive_zone_paths_and_keeps_positions_game_space(self):
        archive = FakeArchive(light_zone([light_record()]))
        result = load_scene_lighting(archive, ['levels/example/point.zone'])
        self.assertEqual(result['lights'][0]['position'], [10.0, 20.0, 30.0])
        self.assertEqual(result['source_zones'], ['levels/example/point.zone'])
        self.assertEqual(result['probes']['status'], 'unsupported_zone')
        self.assertIn('runtime_binding_unobserved', {note['status'] for note in result['notes']})

    def test_authored_sun_status_does_not_discard_supported_points(self):
        archive = FakeArchive(light_zone([light_record()]))
        sun = dict(status='authored_sun_direction_decoded_runtime_global_unobserved',
                   path='atmosphere/example.atmosphere',
                   ray_direction_game_xyz=[0.0, -1.0, 0.0],
                   runtime_global_light='unobserved')
        with patch('rc2016.levels.scene_lighting._authored_sun_note', return_value=sun):
            result = load_scene_lighting(archive, ['levels/example/point.zone'])
        self.assertEqual(len(result['lights']), 1)
        note = next(note for note in result['notes'] if note['status'] == 'authored_sun_candidate')
        self.assertEqual(note['path'], sun['path'])
        self.assertEqual(note['runtime_global_light'], 'unobserved')
        self.assertEqual(sun['status'], 'authored_sun_direction_decoded_runtime_global_unobserved')

    def test_declared_record_size_cannot_overlap_the_next_indexed_record(self):
        first = bytearray(light_record())
        struct.pack_into('<I', first, 0x44, 0xA0)
        with self.assertRaisesRegex(ValueError, 'invalid size'):
            _decode_zone_lights(light_zone([first, light_record()]), 'fixture.zone')

    def test_nonfinite_colour_never_reaches_a_shader_uniform(self):
        record = bytearray(light_record())
        struct.pack_into('<f', record, 0x58, float('nan'))
        lights, skipped = _decode_zone_lights(light_zone([record]), 'fixture.zone')
        self.assertEqual(lights, [])
        self.assertEqual(skipped[0]['reason'], 'nonfinite_or_negative_source_values')

    def test_radius_reciprocal_overflow_does_not_discard_other_source_lights(self):
        lights, skipped = _decode_zone_lights(light_zone([
            light_record(radius=1e-45), light_record(radius=12.0),
        ]), 'fixture.zone')
        self.assertEqual(len(lights), 1)
        self.assertEqual(lights[0]['source_ordinal'], 1)
        self.assertEqual(skipped[0]['reason'], 'nonfinite_or_nonpositive_inverse_radius')


if __name__ == '__main__':
    unittest.main()
