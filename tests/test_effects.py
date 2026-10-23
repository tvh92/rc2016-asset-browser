"""Visual-effect placements and the light cones among them (rc2016/levels/effects.py)."""
import math
import os
import struct
import unittest
from pathlib import Path

from rc2016.levels import effects

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


class LightConeTests(unittest.TestCase):
    def test_light_cone_effects_are_told_by_their_file_name(self):
        self.assertTrue(effects.is_light_cone('visualeffect/x/gbl_amb_Light_Cone_Circular_Med/gbl_amb_Light_Cone_Circular_Small.visualeffect'))
        self.assertTrue(effects.is_light_cone('visualeffect/x/gbl_amb_Light_Cone_Hagin_Wide_blue.visualeffect'))
        self.assertFalse(effects.is_light_cone('visualeffect/global/sun_flare/sun_flare.visualeffect'))
        self.assertFalse(effects.is_light_cone('visualeffect/light_cone_folder/sun_flare.visualeffect'))

    def test_a_placement_takes_the_colour_of_the_spot_light_at_its_position(self):
        spots = [dict(position=[10.0, 20.0, 30.2], colour=[3.0, 2.0, 1.0], source_ordinal=7),
                 dict(position=[10.0, 20.0, 31.5], colour=[9.0, 9.0, 9.0], source_ordinal=8)]
        placed = effects.join_spots([dict(position=[10.0, 20.0, 30.0]), dict(position=[50.0, 0.0, 0.0])], spots)
        self.assertEqual((placed[0]['spot'], placed[0]['colour']), (7, [3.0, 2.0, 1.0]))
        self.assertEqual((placed[1]['spot'], placed[1]['colour']), (None, None))

    def test_the_nearest_spot_wins(self):
        spots = [dict(position=[0.0, 0.0, 0.9], colour=[1.0, 1.0, 1.0], source_ordinal=1),
                 dict(position=[0.0, 0.0, 0.1], colour=[2.0, 2.0, 2.0], source_ordinal=2)]
        self.assertEqual(effects.join_spots([dict(position=[0.0, 0.0, 0.0])], spots)[0]['spot'], 2)


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

    def test_aridia_beams_sit_on_spot_lights_and_the_wide_cone_is_not_drawn(self):
        from rc2016.levels.scene_lighting import load_scene_lighting
        lighting = load_scene_lighting(self.archive, [self.zone])
        cones = lighting['light_cones']
        self.assertGreaterEqual(len(cones), 26)
        self.assertTrue(all(cone['colour'] and cone['spot'] is not None for cone in cones))
        geometry = lighting['cone_geometry']['visualeffect/environment/global/ambient/gbl_amb_light_cone_circular_med/gbl_amb_light_cone_circular_med.model']
        self.assertEqual(float(geometry['positions'][:, 2].min()), 0.0)
        self.assertEqual(float(geometry['positions'][:, 2].max()), 10.0)


if __name__ == '__main__':
    unittest.main()
