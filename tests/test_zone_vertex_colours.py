import struct
import unittest

import numpy as np

from rc2016.levels.scene import instance_vertex_alpha, keyed_vertex_colours, zone_scene
from rc2016.levels.zone_format import decode_zone_arrays, vertex_colour_block
from tests.test_level_material_overrides import record, zone


def key(x, y, z):
    bias = 1 << 20
    return (x + bias) | ((y + bias) << 21) | ((z + bias) << 42) | (1 << 63)


def coloured_record(model_index, instance_id, entries):
    """record() with a position-keyed colour block ending the record: keys at +0xB0, colours after."""
    body = bytearray(record(model_index, instance_id))[:0xB0]
    count = len(entries)
    struct.pack_into('<II', body, 0xA8, 0xB0, 0xB0 + 8 * count)
    struct.pack_into('<I', body, 0x9C, count)
    body += b''.join(struct.pack('<Q', key(*k)) for k, _c in entries)
    body += b''.join(bytes(c) for _k, c in entries)
    struct.pack_into('<I', body, 0x44, len(body))
    return bytes(body) + b'\0' * (-len(body) % 8)


class FakeArchive:
    def __init__(self, data):
        self.data = data


class VertexColourBlockTests(unittest.TestCase):
    entries = [((0, 0, 0), (1, 2, 3, 0)), ((10, -5, 3), (9, 9, 9, 255))]

    def test_block_decodes_signed_keys_and_colours(self):
        data = coloured_record(0, 7, self.entries)
        keys, colours = vertex_colour_block(data, 0, struct.unpack_from('<I', data, 0x44)[0])
        self.assertEqual(keys.tolist(), [[0, 0, 0], [10, -5, 3]])
        self.assertEqual(colours.tolist(), [[1, 2, 3, 0], [9, 9, 9, 255]])

    def test_records_without_or_with_misplaced_blocks_give_none(self):
        self.assertIsNone(vertex_colour_block(record(0, 1), 0, 0xB0))
        data = bytearray(coloured_record(0, 7, self.entries))
        struct.pack_into('<I', data, 0xAC, 0xB0 + 8 * 2 + 4)   # colours not directly after the keys
        self.assertIsNone(vertex_colour_block(bytes(data), 0, struct.unpack_from('<I', data, 0x44)[0]))

    def test_arrays_and_scene_carry_blocks_per_instance(self):
        data = zone([record(0, 1), coloured_record(0, 2, self.entries)])
        arrays = decode_zone_arrays(data)
        self.assertEqual(sorted(arrays['vertex_colours']), [1])
        import rc2016.levels.scene as scene
        original = scene.asset_bytes
        scene.asset_bytes = lambda _archive, _path: data
        try:
            models = zone_scene(None, 'z.zone')['models']
        finally:
            scene.asset_bytes = original
        self.assertEqual(len(models[0]['vertex_colours']), 1)


class KeyedColourTests(unittest.TestCase):
    block = (np.array([[0, 0, 0], [10, -5, 3]]), np.array([[1, 2, 3, 0], [9, 9, 9, 128]], np.uint8))

    def test_vertices_take_the_nearest_key_within_tolerance(self):
        positions = [[0.0, 0.0, 0.0], [1.04, -0.46, 0.3], [0.07, 0.0, 0.0], [5.0, 5.0, 5.0]]
        colours, matched = keyed_vertex_colours(positions, self.block)
        self.assertEqual(matched.tolist(), [True, True, False, False])
        self.assertEqual(colours[:, 3].tolist(), [0, 128, 255, 255])

    def test_ground_candidate_retains_key_presence_instead_of_inventing_unlisted_colours(self):
        from array import array
        from rc2016.levels.scene import instance_vertex_paint
        geometry=dict(positions=array('f',[0,0,0,5,5,5]))
        alpha,paint,note=instance_vertex_paint(geometry,[self.block],1)
        self.assertEqual(list(alpha),[0.,1.])
        np.testing.assert_allclose(np.asarray(paint).reshape(-1,3),[[1/255,2/255,1],[1,1,0]])
        self.assertIn('1 of 2 vertices',note)

    def test_alpha_needs_the_same_block_on_every_instance(self):
        from array import array
        geometry = dict(positions=array('f', [0, 0, 0, 1, -0.5, 0.3]))
        alpha, note = instance_vertex_alpha(geometry, [self.block], 1)
        self.assertEqual(list(alpha), [0.0, np.float32(128 / 255)])
        self.assertIn('2 of 2 vertices', note)
        alpha, note = instance_vertex_alpha(geometry, [self.block], 2)
        self.assertIsNone(alpha)
        self.assertIn('1 of 2 instances', note)
        self.assertEqual(instance_vertex_alpha(geometry, [], 1), (None, None))


if __name__ == '__main__':
    unittest.main()
