"""Per-instance paint pairing, shared buffers, white fallback and culling controls."""
from array import array
import ctypes
import struct
import unittest
from unittest.mock import Mock

import numpy as np

from rc2016.levels import scene, viewport
from rc2016.levels.zone_format import name_crc32, path_crc64
from tests.test_level_material_overrides import record, zone
from rc2016.viewer.shading import INSTANCE_ATTRIBUTES


def _key(x, y, z):
    bias = 1 << 20
    return (x + bias) | ((y + bias) << 21) | ((z + bias) << 42) | (1 << 63)


def _painted_record(instance_id, paint_rgba, *, x=0.0, mirrored=False, override=None):
    """Build one standard record with optional override immediately before its paint block."""
    body = bytearray(record(0, instance_id, x=x))[:0xB0]
    if mirrored:
        struct.pack_into('<f', body, 0, -1.0)
    if override is None:
        struct.pack_into('<I', body, 0xA4, 0xFFFFFFFF)
        paint_at = 0xB0
    else:
        slot_name, replacement = override
        struct.pack_into('<I', body, 0xA4, 0xB0)
        body[0x99] = 1
        body += struct.pack('<IQ', name_crc32(slot_name), path_crc64(replacement))
        paint_at = len(body)
    struct.pack_into('<II', body, 0xA8, paint_at, paint_at + 8)
    struct.pack_into('<I', body, 0x9C, 1)
    body += struct.pack('<Q', _key(0, 0, 0))
    body += bytes(paint_rgba)
    struct.pack_into('<I', body, 0x44, len(body))
    return bytes(body) + b'\0' * (-len(body) % 8)


class SceneGroupingTests(unittest.TestCase):
    def test_zone_scene_pairs_mirror_override_and_paint_identity(self):
        replacement = 'material/env/Variants/paint_test.material'
        source = zone([
            _painted_record(1, (10, 20, 30, 40), x=10),
            _painted_record(2, (50, 60, 70, 80), x=-10,
                            override=('Window_Glass', replacement)),
            record(0, 3, x=20),
            _painted_record(4, (10, 20, 30, 40), x=30, mirrored=True),
        ], materials=(replacement,))
        original = scene.asset_bytes
        scene.asset_bytes = lambda _archive, _path: source
        try:
            model = scene.zone_scene(None, 'paint_test.zone')['models'][0]
        finally:
            scene.asset_bytes = original

        groups = model['groups']
        by_x = {round(float(rows[i * 16 + 12])): (key, rows)
                for key, rows in groups.items() for i in range(len(rows) // 16)}
        self.assertEqual(set(by_x), {-10, 10, 20, 30})
        self.assertNotEqual(by_x[10][0][2], by_x[-10][0][2])
        self.assertEqual(by_x[10][0][2], by_x[30][0][2])
        self.assertEqual(by_x[20][0][2], '')
        self.assertTrue(by_x[30][0][0])
        self.assertEqual(len(model['vertex_paint_blocks']), 2)
        self.assertEqual({key[2] for key in groups}, set(model['vertex_paint_blocks']) | {''})
        self.assertEqual(len(by_x[-10][0][1]), 1)  # override remains paired with its painted row

    def test_stale_override_collapse_retains_paint_key_tail(self):
        rows = lambda x: array('f', [0.0] * 12 + [x, 0, 0, 1])
        stale = ((f'{name_crc32("renamed_slot"):08X}', 'material/other.material', '0000000000000001'),)
        groups = {(False, stale, 'paint-a'): rows(1), (False, (), 'paint-b'): rows(2)}
        result, _outcomes, changed = scene.effective_groups([], groups)
        self.assertEqual(changed, 0)
        self.assertEqual(set(result), {(False, (), 'paint-a'), (False, (), 'paint-b')})
        self.assertEqual(sum(len(v) // 16 for v in result.values()), 2)


class _FakeExt:
    def __init__(self):
        self.bound = None
        self.next_vao = 1000
        self.deletions = []
        self.uploads = []
        self.pointers = {}
        self.divisors = {}

    def glBindBuffer(self, _target, name):
        self.bound = int(name)

    def glGenVertexArrays(self, _count, ptr):
        ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint))[0] = self.next_vao
        self.next_vao += 1

    def glBindVertexArray(self, _vao):
        pass

    def glVertexAttribDivisor(self, index, divisor):
        self.divisors[int(index)] = int(divisor)

    def glDeleteBuffers(self, count, values):
        self.deletions.extend(int(values[i]) for i in range(count))

    def glBufferSubData(self, target, offset, size, _ptr):
        self.uploads.append((self.bound, offset, size))


class PaintViewportTests(unittest.TestCase):
    def _geometry(self):
        return dict(positions=array('f', [0, 0, 0, 1, 0, 0, 0, 1, 0]),
                    normals=array('f', [0, 0, 1] * 3), uvs=array('f', [0, 0] * 3),
                    indices=array('I', [0, 1, 2]), batches=[], triangles=1, materials=[],
                    vertex_paints={
                        'paint-a': dict(alpha=array('f', [0, .5, 1]), ground=array('f', [1, 0, 1] * 3)),
                        'paint-b': dict(alpha=array('f', [1, .5, 0]), ground=array('f', [0, 1, 1] * 3)),
                    })

    def test_upload_shares_geometry_and_clear_owns_each_paint_pair(self):
        view = object.__new__(viewport.LevelViewport)
        ext = _FakeExt()
        view._models, view._ext = {}, {'glBindBuffer': ext.glBindBuffer, 'glDeleteBuffers': ext.glDeleteBuffers}
        uploaded = []

        def allocate(_target, data):
            uploaded.append(data)
            return len(uploaded)

        view._buffer = allocate
        view._upload_model('painted.model', self._geometry(), False)
        model = view._models['painted.model']
        self.assertEqual(len(set(model[k] for k in ('vbo', 'nbo', 'tbo', 'ibo'))), 4)
        self.assertNotEqual(model['paint_buffers']['paint-a'], model['paint_buffers']['paint-b'])
        self.assertEqual(len(model['buffers']), 8)  # four shared geometry + two pairs
        self.assertTrue(set(model['paint_buffers']['paint-a'] + model['paint_buffers']['paint-b']) <= set(model['buffers']))

        model['groups'] = [(99, 1, False, (), 'paint-a')]
        view._make_current = lambda: True
        view._cancel_mipmaps = lambda: None
        view._delete_vaos = lambda _models: None
        view._release_sky = lambda: None
        view._show_error = lambda _message: None
        view._texture_names = {}
        view._sky = view._scene_effects = None
        view.clear()
        self.assertEqual(len(ext.deletions), len(set(ext.deletions)))
        self.assertEqual(set(ext.deletions), set(model['buffers']) | {99})

    def test_vao_binds_matching_paint_stream_and_keeps_legacy_shape(self):
        view = object.__new__(viewport.LevelViewport)
        ext = _FakeExt()
        view._ext = {'glGenVertexArrays': ext.glGenVertexArrays,
                     'glBindVertexArray': ext.glBindVertexArray,
                     'glBindBuffer': ext.glBindBuffer,
                     'glVertexAttribDivisor': ext.glVertexAttribDivisor}
        view._gl, view._shading = Mock(), Mock()
        view._shading.glVertexAttribPointer.side_effect = lambda index, *_args: ext.pointers.__setitem__(int(index), ext.bound)
        model = dict(vbo=1, nbo=2, tbo=3, ibo=4, abo=None, gbo=None,
                     paint_buffers={'paint-a': (51, 52), 'paint-b': (53, 54)}, vaos={})
        view._vao(model, (101, 1, False, (), 'paint-a'))
        alpha = INSTANCE_ATTRIBUTES['aRockAlpha']
        ground = INSTANCE_ATTRIBUTES['aGroundBlend']
        # A VAO is built per instance buffer; each paint group binds its own alpha and ground buffers.
        view._vao(model, (101, 1, False, (), 'paint-a'))
        self.assertEqual(ext.pointers[alpha], 51)
        self.assertEqual(ext.pointers[ground], 52)
        view._vao(model, (102, 1, False, (), 'paint-b'))
        self.assertEqual(ext.pointers[alpha], 53)
        self.assertEqual(ext.pointers[ground], 54)
        calls = view._shading.glVertexAttribPointer.call_args_list
        self.assertIn((alpha, 1, view.GL_FLOAT, 0, 4, None), [c.args for c in calls])
        self.assertIn((ground, 3, view.GL_FLOAT, 0, 12, None), [c.args for c in calls])
        self.assertEqual(ext.divisors[INSTANCE_ATTRIBUTES['aInst0']], 1)
        self.assertEqual(ext.divisors[alpha], 0)
        # No paint identity selects disabled arrays, allowing _begin_attributes' white defaults.
        view._vao(model, (103, 1, False, (), ''))
        view._shading.glDisableVertexAttribArray.assert_any_call(alpha)
        view._shading.glDisableVertexAttribArray.assert_any_call(ground)
        view._begin_attributes()
        view._shading.glVertexAttrib4f.assert_any_call(alpha, 1.0, 0.0, 0.0, 1.0)
        view._shading.glVertexAttrib4f.assert_any_call(ground, 0.0, 0.0, 0.0, 0.0)
        # Existing four-field groups continue to use the legacy per-model streams.
        legacy = dict(vbo=1, nbo=2, tbo=3, ibo=4, abo=61, gbo=62, vaos={})
        view._vao(legacy, (104, 1, False, ()))
        self.assertEqual(len((104, 1, False, ())), 4)
        self.assertEqual(ext.pointers[alpha], 61)
        self.assertEqual(ext.pointers[ground], 62)
        self.assertIn((alpha, 1, view.GL_FLOAT, 0, 4, None), [c.args for c in view._shading.glVertexAttribPointer.call_args_list])

    def test_culling_restore_reuses_core_group_buffer_ids_and_paint_membership(self):
        view = object.__new__(viewport.LevelViewport)
        ext = _FakeExt()
        view._ext = {'glBindBuffer': ext.glBindBuffer, 'glBufferSubData': ext.glBufferSubData,
                     'glDeleteBuffers': ext.glDeleteBuffers}
        next_id = iter(range(200, 210))
        view._buffer = lambda _target, _rows: next(next_id)
        view._delete_vaos = lambda _models: None
        view._models = {'m': dict(hidden=False, zones=[('zone', (False, (), 'paint-a'), array('f', [1] + [0] * 15)),
                                                        ('zone', (False, (), 'paint-b'), array('f', [2] + [0] * 15))],
                                  actor_zones=[], spawn_zones=[], groups=[], groups_for=None,
                                  paint_buffers={'paint-a': (1, 2), 'paint-b': (3, 4)},
                                  instance_rows={}, triangles=1)}
        view._hidden_zones, view.show_actors, view.show_spawn_preview = set(), False, False
        view._cull_masks, view._cull_counts, view._cull_camera, view._stats_base = {}, {}, None, None
        view._merge_groups()
        model = view._models['m']
        original_ids = tuple(g[0] for g in model['groups'])
        self.assertEqual({g[4] for g in model['groups']}, {'paint-a', 'paint-b'})
        self.assertTrue(all(len(g) == 5 for g in model['groups']))
        view._cull_masks = {name: np.array([False]) for name in original_ids}
        view._cull_counts = {name: 0 for name in original_ids}
        view._restore_unculled_instances()
        self.assertEqual(tuple(g[0] for g in model['groups']), original_ids)
        self.assertEqual({g[4] for g in model['groups']}, {'paint-a', 'paint-b'})
        self.assertEqual([upload[0] for upload in ext.uploads], list(original_ids))
        self.assertFalse(view._cull_masks)
        self.assertFalse(view._cull_counts)


if __name__ == '__main__':
    unittest.main()
