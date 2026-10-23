"""Visual-effect placements and the light cones among them (rc2016/levels/effects.py)."""
import math
import os
import struct
import unittest
from pathlib import Path

from rc2016.levels import effects
from rc2016.levels.zone_format import path_crc64

PATHS = [r'visualeffect\environment\global\ambient\gbl_amb_Light_Cone_Circular_Med\gbl_amb_Light_Cone_Circular_Med.visualeffect',
         r'visualeffect\environment\global\ambient\gbl_amb_Light_Cone_Circular_Med\gbl_amb_Light_Cone_Circular_Small.visualeffect',
         r'visualeffect\global\sun_flare\sun_flare.visualeffect']


def dat1(sections):
    count = len(sections)
    offset = 16 + count * 12
    rows, payload = bytearray(), bytearray()
    for label, blob in sections:
        rows.extend(struct.pack('<III', label, offset, len(blob)))
        payload.extend(blob)
        offset += len(blob)
    return b'1TAD' + struct.pack('<IIHH', 0, offset, count, 0) + rows + payload


def placement(position=(10.0, 20.0, 30.0), index=0, rows=((1, 0, 0), (0, 1, 0), (0, 0, 1)), kind=5, size=0x60):
    record = bytearray(0x60)
    struct.pack_into('<16f', record, 0, *rows[0], 0, *rows[1], 0, *rows[2], 0, *position, 1)
    struct.pack_into('<II', record, 0x40, kind, size)
    struct.pack_into('<4f', record, 0x48, 1, 1, 1, 1)
    struct.pack_into('<I', record, 0x5C, 0xFF000000 | index)
    return bytes(record)


def effect_zone(records, paths=PATHS):
    """A zone with the three effect sections: the string pool is its own section so the absolute offsets can be computed."""
    strings = b''.join(p.encode() + b'\0' for p in paths)
    heap = b''.join(records)
    index = b''.join(struct.pack('<I', i * 0x60) for i in range(len(records)))

    def build(table):
        return dat1([(effects.EFFECT_PATHS, table), (effects.EFFECT_OFFSETS, index), (effects.DATA_HEAP, heap), (0x1234, strings)])
    start = struct.unpack_from('<I', build(bytes(4 * len(paths))), 16 + 12 * 3 + 4)[0]
    table, cursor = b'', start
    for path in paths:
        table += struct.pack('<I', cursor)
        cursor += len(path) + 1
    return build(table)


class DecodeTests(unittest.TestCase):
    def test_placements_carry_the_effect_path_position_and_aim_axis(self):
        zone = effect_zone([placement(), placement((1.0, 2.0, 3.0), index=1, rows=((0, 0, 1), (1, 0, 0), (0, -1, 0)))])
        first, second = effects.decode_zone_effects(zone, 'levels/example/lighting.zone')
        self.assertEqual(first['effect'], PATHS[0].replace('\\', '/'))
        self.assertEqual(first['position'], [10.0, 20.0, 30.0])
        self.assertEqual(first['axis'], [0.0, 0.0, 1.0])
        self.assertEqual((second['effect'], second['position'], second['axis']), (PATHS[1].replace('\\', '/'), [1.0, 2.0, 3.0], [0.0, -1.0, 0.0]))
        self.assertEqual((first['source_ordinal'], second['source_ordinal'], first['source_path']), (0, 1, 'levels/example/lighting.zone'))

    def test_a_zone_without_the_sections_places_no_effects(self):
        self.assertEqual(effects.decode_zone_effects(dat1([(0x1234, b'\0' * 8)])), [])

    def test_records_without_the_decoded_shape_are_left_out(self):
        zone = effect_zone([placement(kind=4), placement(size=0xB0), placement(index=9), placement()])
        self.assertEqual([p['source_ordinal'] for p in effects.decode_zone_effects(zone)], [3])

    def test_a_matrix_with_a_non_affine_column_is_left_out(self):
        record = bytearray(placement())
        struct.pack_into('<f', record, 12, 0.5)
        self.assertEqual(effects.decode_zone_effects(effect_zone([bytes(record)])), [])

    def test_the_aim_axis_is_normalised_when_the_matrix_is_scaled(self):
        zone = effect_zone([placement(rows=((2, 0, 0), (0, 2, 0), (0, 0, 2)))])
        self.assertEqual(effects.decode_zone_effects(zone)[0]['axis'], [0.0, 0.0, 1.0])


def component(asset=0, size=(1.0, 1.0), flags=0x0C0CC823, slots=(0, 1, 0, 0, 0, 1, 2)):
    record = bytearray(effects.COMPONENT_SIZE)
    struct.pack_into('<2f', record, 0x3C, *size)
    struct.pack_into('<Q', record, 0x44, asset)
    struct.pack_into('<I', record, 0xF4, flags)
    struct.pack_into('<7B', record, 0x108, *slots)
    return bytes(record)


def effect_asset(components, parameters=()):
    """A visual effect with the component records and ``parameters`` ((u16 x 4, scale) per entry)."""
    values = b''.join(struct.pack('<8h', *raw, 0, 0, 0, 1) if len(raw) == 4 else struct.pack('<8h', *raw) for raw, _scale in parameters)
    scales = b''.join(struct.pack('<IIIf', 0xAB000000 + n, n, 1, scale) for n, (_raw, scale) in enumerate(parameters))
    return dat1([(effects.COMPONENTS, b''.join(components)), (effects.PARAMETER_VALUES, values), (effects.PARAMETER_SCALES, scales)])


MODEL = 'visualeffect/x/cone/cone.model'


class EffectTests(unittest.TestCase):
    def test_components_carry_the_model_id_size_flags_and_parameter_slots(self):
        data = effect_asset([component(), component(path_crc64(MODEL), (1.0, 1.0), 0x0C0CC823, (0, 1, 0, 0, 1, 7, 8))])
        decoded = effects.decode_effect(data)
        self.assertEqual([c['asset'] for c in decoded['components']], [0, path_crc64(MODEL)])
        self.assertEqual(decoded['components'][1]['slots'], [0, 1, 0, 0, 1, 7, 8])
        self.assertEqual((decoded['components'][1]['size'], decoded['components'][1]['flags']), ((1.0, 1.0), 0x0C0CC823))

    def test_parameters_are_the_u16_values_times_the_scale(self):
        data = effect_asset([component()], [((0x7FFF, 0x7FFF, 0x7FFF, 0), 1 / 32767), ((0x7FFF, 0x1912, 0x02C5, 0), 3 / 32767)])
        first, second = effects.decode_effect(data)['parameters']
        for got, want in zip(first['value'], [1.0, 1.0, 1.0, 0.0]):
            self.assertAlmostEqual(got, want, places=5)
        for got, want in zip(second['value'][:3], [3.0, 3 * 0x1912 / 32767, 3 * 0x02C5 / 32767]):
            self.assertAlmostEqual(got, want, places=5)
        self.assertEqual(first['hash'], 0xAB000000)

    def test_an_asset_without_the_sections_has_no_components(self):
        self.assertEqual(effects.decode_effect(dat1([(0x1234, bytes(8))])), dict(components=[], parameters=[]))

    def test_the_mesh_scale_is_the_size_of_the_component_with_the_models_id(self):
        effect = effects.decode_effect(effect_asset([component(), component(path_crc64(MODEL), (2.0, 2.0)), component(0x85CB1669081163E5, (4.0, 4.0))]))
        self.assertEqual(effects.mesh_scale(effect, MODEL), 2.0)

    def test_the_mesh_scale_is_unknown_without_exactly_one_matching_component_or_with_unequal_sizes(self):
        self.assertIsNone(effects.mesh_scale(effects.decode_effect(effect_asset([component()])), MODEL))
        self.assertIsNone(effects.mesh_scale(effects.decode_effect(effect_asset([component(path_crc64(MODEL)), component(path_crc64(MODEL))])), MODEL))
        self.assertIsNone(effects.mesh_scale(effects.decode_effect(effect_asset([component(path_crc64(MODEL), (1.0, 2.0))])), MODEL))


class MeshColourTests(unittest.TestCase):
    PARAMETERS = [((0, 0, 0, 0), 1 / 32767), ((0x7FFF, 0x7FFF, 0x7FFF, 0), 1 / 32767), ((0x7FFF, 0x1912, 0x02C5, 0), 1 / 32767),
                  ((0x1000, 0, 0, 0), 1 / 32767)]

    def effect(self, colour_slot=2, opacity_slot=3, count=1):
        mesh = component(path_crc64(MODEL), slots=(0, 1, 0, 0, 0, colour_slot, opacity_slot))
        return effects.decode_effect(effect_asset([component(slots=(0, 1, 0, 0, 0, 1, 1))] + [mesh] * count, self.PARAMETERS))

    def test_the_colour_is_the_parameter_in_slot_5_and_the_opacity_the_first_value_of_slot_6(self):
        colour, opacity = effects.mesh_colour(self.effect(), MODEL)
        for got, want in zip(colour, [1.0, 0x1912 / 32767, 0x02C5 / 32767]):
            self.assertAlmostEqual(got, want, places=5)
        self.assertAlmostEqual(opacity, 0x1000 / 32767, places=5)

    def test_other_components_do_not_count(self):
        # slot 5 of the component without a model lists the white parameter 1: the mesh's own slot wins
        self.assertNotEqual(effects.mesh_colour(self.effect(), MODEL)[0], [1.0, 1.0, 1.0])

    def test_a_missing_or_duplicated_mesh_or_a_slot_past_the_table_has_no_colour(self):
        self.assertIsNone(effects.mesh_colour(self.effect(count=2), MODEL))
        self.assertIsNone(effects.mesh_colour(self.effect(), 'visualeffect/x/other.model'))
        self.assertIsNone(effects.mesh_colour(self.effect(colour_slot=9), MODEL))

    def test_the_spread_is_read_as_signed_values_beside_the_base(self):
        data = effect_asset([component()], [((0x7FFF, 0, 0, 0, -0x2AAA, -0x2AAA, 0, 1), 1.5 / 32767)])
        parameter = effects.decode_effect(data)['parameters'][0]
        self.assertAlmostEqual(parameter['value'][0], 1.5, places=4)
        self.assertAlmostEqual(parameter['spread'][0], -0.5, places=3)


class ConeFrameTests(unittest.TestCase):
    def test_the_axis_points_from_the_apex_at_the_origin_to_the_base(self):
        import numpy as np
        circular = np.array([[0, 0, 0], [3.75, 0, 10], [-3.75, 0, 10], [0, 3.75, 10]], np.float32)
        wide = np.array([[0, 0.078, 0], [3.75, -1.92, 0], [-3.75, -1.92, 0], [0, -1.92, 3.75], [0, -1.92, -3.75], [0, -1.92, 0]], np.float32)
        axis, length = effects.cone_frame(circular)
        self.assertEqual((axis.tolist(), length), ([0.0, 0.0, 1.0], 10.0))
        axis, length = effects.cone_frame(wide)
        self.assertEqual(axis.tolist(), [0.0, -1.0, 0.0])
        self.assertAlmostEqual(length, 1.998, places=3)


class LightConeTests(unittest.TestCase):
    def test_light_cone_effects_are_told_by_their_file_name(self):
        self.assertTrue(effects.is_light_cone('visualeffect/x/gbl_amb_Light_Cone_Circular_Med/gbl_amb_Light_Cone_Circular_Small.visualeffect'))
        self.assertTrue(effects.is_light_cone('visualeffect/x/gbl_amb_Light_Cone_Hagin_Wide_blue.visualeffect'))
        self.assertFalse(effects.is_light_cone('visualeffect/global/sun_flare/sun_flare.visualeffect'))
        self.assertFalse(effects.is_light_cone('visualeffect/light_cone_folder/sun_flare.visualeffect'))


RESEARCH = os.environ.get('RC2016_RESEARCH')


@unittest.skipUnless(RESEARCH and Path(RESEARCH).is_dir(), 'RC2016_RESEARCH (the game archive) is not set')
class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(RESEARCH)
        cls.zone = 'levels/aridia/openarea/openworld/default_region/lighting.zone'

    def test_aridia_places_27_light_cones_and_every_placement_has_the_decoded_shape(self):
        from rc2016.levels.scene import asset_bytes
        placements = effects.decode_zone_effects(asset_bytes(self.archive, self.zone), self.zone)
        cones = [p for p in placements if effects.is_light_cone(p['effect'])]
        names = sorted({p['effect'].rsplit('/', 1)[-1] for p in cones})
        self.assertEqual(names, ['gbl_amb_Light_Cone_Circular_Med.visualeffect', 'gbl_amb_Light_Cone_Circular_Small.visualeffect'])
        self.assertEqual(sum('Small' in p['effect'] for p in cones), 3)
        self.assertEqual(len(cones), 27)
        for placement in cones:
            self.assertAlmostEqual(math.sqrt(sum(v * v for v in placement['axis'])), 1.0, places=5)

    def test_the_cone_effects_name_the_circular_cone_model(self):
        model = effects.effect_model_path(
            self.archive, 'visualeffect/environment/global/ambient/gbl_amb_light_cone_circular_med/gbl_amb_light_cone_circular_small.visualeffect')
        self.assertTrue(model.endswith('gbl_amb_light_cone_circular_med.model'), model)

    def test_aridia_has_a_beam_per_placement_at_scale_1_in_its_effects_colour(self):
        from rc2016.levels.scene_lighting import load_scene_lighting
        lighting = load_scene_lighting(self.archive, [self.zone])
        cones = lighting['light_cones']
        self.assertEqual(len(cones), 27)
        self.assertTrue(all(cone['scale'] == 1.0 for cone in cones))
        med = [cone['colour'] for cone in cones if cone['effect'].endswith('Circular_Med.visualeffect')]
        small = [cone['colour'] for cone in cones if cone['effect'].endswith('Circular_Small.visualeffect')]
        self.assertEqual((len(med), len(small)), (24, 3))
        # the colour parameter times the opacity parameter of each effect's mesh component
        for got, want in zip(med[0], [0.1475 * 0.2, 0.0756 * 0.2, 0.0216 * 0.2]):
            self.assertAlmostEqual(got, want, delta=0.0005)
        for got, want in zip(small[0], [0x7FFF / 32767 * 0x15FA / 32767, 0x1912 / 32767 * 0x15FA / 32767, 0x02C5 / 32767 * 0x15FA / 32767]):
            self.assertAlmostEqual(got, want, delta=0.001)
        geometry = lighting['cone_geometry']['visualeffect/environment/global/ambient/gbl_amb_light_cone_circular_med/gbl_amb_light_cone_circular_med.model']
        self.assertEqual(float(geometry['positions'][:, 2].min()), 0.0)
        self.assertEqual(float(geometry['positions'][:, 2].max()), 10.0)
        self.assertEqual((geometry['axis'].tolist(), geometry['length']), ([0.0, 0.0, 1.0], 10.0))

    def test_the_mesh_component_of_every_light_cone_effect_is_found_by_the_models_crc64(self):
        base = 'visualeffect/environment/global/ambient/gbl_amb_light_cone_circular_med/'
        for name in ('gbl_amb_light_cone_circular_med', 'gbl_amb_light_cone_circular_small', 'gbl_amb_light_cone_hangin_wide_warm',
                     'gbl_amb_light_cone_hagin_wide_blue'):
            path = f'{base}{name}.visualeffect'
            model = effects.effect_model_path(self.archive, path)
            effect = effects.decode_effect(effects.asset_bytes(self.archive, path))
            self.assertEqual(effects.mesh_scale(effect, model), 1.0, name)
            self.assertEqual(len(effect['components']), 3, name)

    def test_the_warm_and_blue_wide_cones_differ_in_colour_as_their_names_say(self):
        base = 'visualeffect/environment/global/ambient/gbl_amb_light_cone_circular_med/'
        colours = {}
        for name in ('gbl_amb_light_cone_hangin_wide_warm', 'gbl_amb_light_cone_hagin_wide_blue'):
            path = f'{base}{name}.visualeffect'
            effect = effects.decode_effect(effects.asset_bytes(self.archive, path))
            colours[name] = effects.mesh_colour(effect, effects.effect_model_path(self.archive, path))
        warm, blue = colours['gbl_amb_light_cone_hangin_wide_warm'][0], colours['gbl_amb_light_cone_hagin_wide_blue'][0]
        self.assertGreater(warm[0], warm[2] * 3)
        self.assertGreater(blue[2], blue[0] * 5)
        self.assertAlmostEqual(colours['gbl_amb_light_cone_hangin_wide_warm'][1], 0.03, delta=0.005)

    def test_quartus_wide_cones_are_drawn_on_the_wide_model_axis(self):
        from rc2016.levels.scene import level_zone_paths
        from rc2016.levels.scene_lighting import load_scene_lighting
        lighting = load_scene_lighting(self.archive, level_zone_paths(self.archive, 'levels/quartu/quartu.level'))
        cones = lighting['light_cones']
        self.assertEqual(len(cones), 59)
        self.assertTrue(all(cone['scale'] == 1.0 and cone['colour'][2] > cone['colour'][0] for cone in cones))   # blue
        geometry = lighting['cone_geometry'][cones[0]['model']]
        self.assertEqual(geometry['axis'].tolist(), [0.0, -1.0, 0.0])
        self.assertAlmostEqual(geometry['length'], 2.0, places=1)


if __name__ == '__main__':
    unittest.main()
