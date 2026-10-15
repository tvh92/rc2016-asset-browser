"""Level viewer speed-ups checked against reference copies of the previous algorithms on synthetic data:
zone decoding and grouping, core bounds, the geometry cache, draw-list building, culling and the loader."""
from array import array
import math
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from rc2016.levels import scene
from rc2016.levels.zone_format import decode_zone, decode_zone_arrays, name_crc32, path_crc64
from tests.test_level_material_overrides import RED, record, zone


# Reference copies of the previous code --------------------------------------------------------
def reference_mirrored(rows):
    (a, b, c) = (r[:3] for r in rows[:3])
    det = (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
           + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return det < 0


def reference_zone_scene(data, zone_path='z.zone'):
    try:
        decoded = decode_zone(data)
    except ValueError as exc:
        if 'lacks instance sections' not in str(exc):
            raise
        return dict(zone=zone_path, models=[], instances=0, points=[])
    models = [dict(path=p.replace('\\', '/'), hidden=scene.hidden_by_default(p), count=0, groups={})
              for p in decoded['models']]
    points = []
    for instance in decoded['instances']:
        rows = instance['matrix_rows']
        model = models[instance['model_index']]
        overrides = tuple((o['slot_hash'], o['material'], o['material_hash']) for o in instance['material_overrides'])
        model['groups'].setdefault((reference_mirrored(rows), overrides), array('f')).extend(x for row in rows for x in row)
        model['count'] += 1
        if not model['hidden']:
            points.append(tuple(rows[3][:3]))
    return dict(zone=zone_path, models=[m for m in models if m['count']], instances=len(decoded['instances']),
                points=points)


def reference_core_bounds(points, trim=0.02):
    if not points:
        return None
    low, high = [], []
    for axis in range(3):
        values = sorted(p[axis] for p in points)
        cut = int(len(values) * trim)
        low.append(values[cut]); high.append(values[len(values) - 1 - cut])
    return tuple(low), tuple(high)


def matrix_record(model_index, instance_id, matrix, overrides=()):
    body = bytearray(record(model_index, instance_id, overrides))
    struct.pack_into('<16f', body, 0, *matrix)
    return bytes(body)


def random_matrix(rng):
    m = [rng.uniform(-2, 2) for _ in range(16)]
    m[3] = m[7] = m[11] = 0.0
    m[15] = 1.0
    return m


MODELS = ('environment/a.model', 'environment\\B.model', 'gameplay/hero_collision.model', 'environment/unused.model')


def synthetic_zone(rng, count):
    slot = name_crc32('Window_Glass')
    choices = [(), [(slot, path_crc64(RED))], [(slot, 0)], [(0x1234, path_crc64(RED)), (slot, path_crc64(RED))]]
    records = [matrix_record(rng.randrange(3), i + 1, random_matrix(rng), rng.choice(choices)) for i in range(count)]
    return zone(records, models=MODELS)


class ZoneArraysTests(unittest.TestCase):
    def test_scene_matches_reference_on_random_zones(self):
        rng = random.Random(5)
        for count in (0, 1, 2, 7, 64, 300):
            data = synthetic_zone(rng, count)
            with self.subTest(count=count), patch.object(scene, 'asset_bytes', return_value=data):
                got, want = scene.zone_scene(None, 'z.zone'), reference_zone_scene(data)
                self.assertEqual(got['instances'], want['instances'])
                self.assertEqual(got['points'], want['points'])
                self.assertEqual([(m['path'], m['hidden'], m['count']) for m in got['models']],
                                 [(m['path'], m['hidden'], m['count']) for m in want['models']])
                for a, b in zip(got['models'], want['models']):
                    self.assertEqual(list(a['groups']), list(b['groups']))   # same keys, same order
                    for key in a['groups']:
                        self.assertEqual(a['groups'][key].tobytes(), b['groups'][key].tobytes())

    def test_arrays_match_full_decoder(self):
        data = synthetic_zone(random.Random(9), 40)
        fast, full = decode_zone_arrays(data), decode_zone(data)
        self.assertEqual(fast['models'], full['models'])
        self.assertEqual(fast['override_materials'], full['override_materials'])
        self.assertEqual(fast['model_index'].tolist(), [i['model_index'] for i in full['instances']])
        self.assertEqual(fast['instance_flags'].tolist(), [int(i['flags'], 16) for i in full['instances']])
        self.assertEqual(fast['matrices'].tolist(), [[x for r in i['matrix_rows'] for x in r] for i in full['instances']])
        self.assertEqual(fast['overrides'], {n: i['material_overrides'] for n, i in enumerate(full['instances'])
                                             if i['material_overrides']})

    def test_full_decoder_fallback_preserves_raw_instance_flags(self):
        data = zone([record(0, 1)])
        from rc2016.levels.zone_format import _arrays_from_full
        arrays = _arrays_from_full(data)
        self.assertEqual(arrays['instance_flags'].dtype, np.dtype(np.uint32))
        self.assertEqual(arrays['instance_flags'].tolist(), [0x440000])

    def test_invariant_failures_raise_the_full_decoders_error(self):
        good = [record(0, 1), record(0, 2, x=3.0)]
        cases = []
        bad = bytearray(good[1]); struct.pack_into('<f', bad, 12, 0.5); cases.append([good[0], bytes(bad)])   # w column
        bad = bytearray(good[1]); struct.pack_into('<I', bad, 0x58, 7); cases.append([good[0], bytes(bad)])    # model index
        cases.append([record(0, 1), record(0, 1)])                                                              # duplicate id
        bad = bytearray(good[0]); struct.pack_into('<I', bad, 0x44, 0xB8); cases.append([bytes(bad), good[1]])  # stride
        bad = bytearray(good[0]); struct.pack_into('<I', bad, 0x44, 0x40); cases.append([bytes(bad), good[1]])  # size
        bad = bytearray(record(0, 1, [(1, 2)])); struct.pack_into('<I', bad, 0xA4, 0xB8); cases.append([bytes(bad)])
        bad = bytearray(record(0, 1)); bad[0x99] = 1; cases.append([bytes(bad)])
        for records in cases:
            data = zone(records)
            with self.subTest(records=len(records)):
                with self.assertRaises(ValueError) as full:
                    decode_zone(data)
                with self.assertRaises(ValueError) as fast:
                    decode_zone_arrays(data)
                self.assertEqual(str(fast.exception), str(full.exception))

    def test_zone_without_instance_sections(self):
        data = b'1TAD' + struct.pack('<III', 0, 16, 0)
        with patch.object(scene, 'asset_bytes', return_value=data):
            self.assertEqual(scene.zone_scene(None, 'z.zone'), reference_zone_scene(data))

    def test_mirrored_array_matches_scalar(self):
        rng = random.Random(1)
        matrices = [random_matrix(rng) for _ in range(500)] + [[1.0 if i % 5 == 0 else 0.0 for i in range(16)]]
        rows = np.array(matrices, np.float32)
        want = [reference_mirrored([list(map(float, r[i:i + 4])) for i in range(0, 16, 4)]) for r in rows]
        self.assertEqual(scene.mirrored_array(rows).tolist(), want)


class CoreBoundsTests(unittest.TestCase):
    def test_matches_sorted_reference(self):
        rng = random.Random(3)
        for count in (1, 2, 49, 50, 51, 100, 1001):
            points = [tuple(rng.uniform(-1e4, 1e4) for _ in range(3)) for _ in range(count)]
            points[0] = (1.0, 1.0, 1.0)
            with self.subTest(count=count):
                self.assertEqual(scene.core_bounds(points), reference_core_bounds(points))
                self.assertEqual(scene.core_bounds(points, trim=0.3), reference_core_bounds(points, trim=0.3))
        self.assertIsNone(scene.core_bounds([]))
        rows = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]   # actor translations arrive as lists
        self.assertEqual(scene.core_bounds(rows), reference_core_bounds(rows))


class GeometryCacheTests(unittest.TestCase):
    def geometry(self):
        return dict(positions=array('f', [0, 1, 2, 3, 4, 5.5]), normals=array('f', [0, 0, 1, 0, 0, 1]),
                    raw_vectors=array('f'), static_vector_eligible=False,
                    uvs=array('f', [0, 1, 0.25, 0.5]), indices=array('I', [0, 1, 1]),
                    batches=[(0, 0, 1, True), (None, 1, 0, False)],
                    materials=[dict(index=0, name='a', path='m/a.material'), dict(index=1, name=None, path=None)],
                    radius=math.sqrt(2.0) / 3.0, triangles=1, source_sha256='ab' * 32, skinned=False,
                    bind_usable=True, bounds=([0.0, 1.0, 2.0], [3.0, 4.0, 5.5]))

    def test_round_trip_is_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'g.npz'
            scene.save_cached_geometry(path, self.geometry())
            self.assertEqual(scene.load_cached_geometry(path), self.geometry())
            self.assertEqual([p.name for p in Path(folder).iterdir()], ['g.npz'])   # no temporary left

    def test_unreadable_or_unrepresentable_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'g.npz'
            path.write_bytes(b'not a zip')
            self.assertIsNone(scene.load_cached_geometry(path))
            odd = self.geometry(); odd['materials'][0]['extra'] = (1, 2)   # a tuple would read back as a list
            scene.save_cached_geometry(Path(folder) / 'h.npz', odd)
            self.assertFalse((Path(folder) / 'h.npz').exists())

    def test_key_changes_with_bytes_selection_and_decoder(self):
        key = scene.geometry_cache_key
        base = key('Env/A.model', 'Highest', 0, 'x', 'f')
        self.assertEqual(base, key('env\\a.model', 'Highest', 0, 'x', 'f'))
        self.assertEqual(len({base, key('env/a.model', 'Lowest', 0, 'x', 'f'), key('env/a.model', 'Highest', 1, 'x', 'f'),
                              key('env/a.model', 'Highest', 0, 'y', 'f'), key('env/a.model', 'Highest', 0, 'x', 'g')}), 5)
        self.assertTrue(scene.decoder_fingerprint())

    def test_model_geometry_decodes_once_then_reads_the_cache(self):
        class Archive:
            def resolve(self, path):
                return ['id']

            def bytes(self, key):
                return b'raw', 'sha'
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(scene, 'GEOMETRY_CACHE_DIR', Path(folder)), \
                patch.object(scene, 'decompress', return_value=(b'data',)), \
                patch.object(scene, 'decode_geometry', return_value=self.geometry()) as decode:
            first = scene.model_geometry(Archive(), 'env/a.model')
            second = scene.model_geometry(Archive(), 'env/a.model')
            third = scene.model_geometry(Archive(), 'env/a.model', cache=False)
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(first, second)
        self.assertEqual(second, third)


# Culling ---------------------------------------------------------------------------------------
import ctypes   # noqa: E402

from rc2016.levels import viewport as lv   # noqa: E402


def clip_inside(projection, modelview, points):
    clip = (np.asarray(projection).reshape(4, 4).T @ np.asarray(modelview).reshape(4, 4).T
            @ np.c_[points, np.ones(len(points))].T).T
    w = clip[:, 3:4]
    return np.all(np.abs(clip[:, :3]) <= w, axis=1) & (w[:, 0] > 0)


def sphere_points(centre, radius, rng, count=200):
    v = rng.normal(size=(count, 3))
    v /= np.linalg.norm(v, axis=1)[:, None]
    inner = v * radius * rng.uniform(0, 1, (count, 1)) ** (1 / 3)
    return np.vstack((centre + v * radius, centre + inner, [centre]))


class CullingTests(unittest.TestCase):
    def camera(self, eye=(0.0, 0.0, 0.0), yaw=30.0, pitch=-10.0, near=0.1, far=500.0, width=800, height=600):
        projection = lv.projection_matrix(width, height, near, far)
        modelview = lv._mat4_mul(lv.view_matrix(eye, yaw, pitch), lv.BASIS)
        return projection, modelview

    def test_projection_matches_gl_frustum_formula(self):
        near, far, width, height = 0.5, 1000.0, 1216, 873
        top = near * math.tan(math.radians(lv.FOV_DEGREES) * 0.5)
        right = top * width / height
        left, bottom = -right, -top
        expected = (2 * near / (right - left), 0, 0, 0, 0, 2 * near / (top - bottom), 0, 0,
                    (right + left) / (right - left), (top + bottom) / (top - bottom), -(far + near) / (far - near), -1,
                    0, 0, -2 * far * near / (far - near), 0)
        for a, b in zip(lv.projection_matrix(width, height, near, far), expected):
            self.assertAlmostEqual(a, b, places=12)

    def test_mat4_mul_is_column_major_product(self):
        a = tuple(float(i) for i in range(16)); b = tuple(float(i * i % 7) for i in range(16))
        want = (np.array(a).reshape(4, 4).T @ np.array(b).reshape(4, 4).T).T.reshape(-1)
        self.assertEqual(lv._mat4_mul(a, b), tuple(want.tolist()))

    def test_spheres_are_kept_whenever_any_point_is_visible(self):
        rng = np.random.default_rng(1)
        for eye, yaw, pitch in (((0, 0, 0), 30, -10), ((50, -20, 10), 200, 45), ((1, 2, 3), -90, -89)):
            projection, modelview = self.camera(eye, yaw, pitch)
            planes = lv.frustum_planes(projection, modelview)
            centres = rng.uniform(-300, 300, (400, 3)); radii = rng.uniform(0.0, 60.0, 400)
            mask = lv.sphere_visibility(centres, radii, planes)
            for centre, radius, keep in zip(centres, radii, mask):
                if clip_inside(projection, modelview, sphere_points(centre, radius, rng)).any():
                    self.assertTrue(keep, (eye, yaw, pitch, centre, radius))
            self.assertGreater(int(mask.sum()), 0); self.assertLess(int(mask.sum()), len(mask))

    def test_planes_are_in_native_space(self):
        # Yaw 0 looks along presentation +X, which is native +X: ahead is kept, behind is not.
        projection, modelview = self.camera((0, 0, 0), 0.0, 0.0)
        planes = lv.frustum_planes(projection, modelview)
        mask = lv.sphere_visibility(np.array([[20.0, 0, 0], [-20.0, 0, 0], [20.0, 5.0, 0]]), np.array([1.0, 1.0, 1.0]), planes)
        self.assertEqual(mask.tolist(), [True, False, True])
        # Presentation up (+Z) is native +Y: looking straight up keeps native +Y.
        projection, modelview = self.camera((0, 0, 0), 0.0, 89.0)
        planes = lv.frustum_planes(projection, modelview)
        self.assertEqual(lv.sphere_visibility(np.array([[0, 20.0, 0], [0, -20.0, 0]]), np.ones(2), planes).tolist(), [True, False])
        self.assertEqual(lv.sphere_visibility(np.zeros((0, 3)), np.zeros(0), planes).tolist(), [])
        self.assertEqual(lv.sphere_visibility(np.array([[0, -20.0, 0]]), np.array([np.inf]), planes).tolist(), [True])

    def test_instance_spheres_bound_sheared_and_mirrored_vertices(self):
        rng = np.random.default_rng(2)
        vertices = rng.uniform(-1, 1, (300, 3)) * (4, 1, 0.5) + (2, 0, 0)
        geometry = dict(radius=float(np.linalg.norm(vertices, axis=1).max()),
                        bounds=(vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()))
        sphere = lv.model_sphere(geometry)
        self.assertLess(sphere[1], geometry['radius'])   # the box sphere is tighter here
        rows = []
        for _ in range(50):
            m = np.eye(4)
            m[:3, :3] = rng.uniform(-2, 2, (3, 3))   # shear, scale, mirroring
            m[3, :3] = rng.uniform(-100, 100, 3)
            rows.append(m.reshape(-1))
        rows = np.array(rows, np.float32)
        centres, radii = lv.instance_spheres(rows, sphere)
        for row, centre, radius in zip(rows.astype(np.float64), centres, radii):
            m = row.reshape(4, 4)
            world = vertices @ m[:3, :3] + m[3, :3]
            self.assertLessEqual(np.linalg.norm(world - centre, axis=1).max(), radius * (1 + 1e-9) + 1e-9)
        self.assertIsNone(lv.model_sphere({}))
        self.assertEqual(lv.model_sphere(dict(radius=2.0)), ((0.0, 0.0, 0.0), 2.0))
        empty = lv.instance_spheres(np.zeros((0, 16), np.float32), sphere)
        self.assertEqual((empty[0].shape, empty[1].shape), ((0, 3), (0,)))
        self.assertTrue(np.isinf(lv.instance_spheres(rows[:2], None)[1]).all())


class FakeExt(dict):
    def __init__(self):
        super().__init__()
        self.bound, self.uploads = None, []
        self['glBindBuffer'] = lambda target, name: setattr(self, 'bound', name)
        self['glBufferSubData'] = lambda target, offset, size, address: self.uploads.append(
            (self.bound, offset, ctypes.string_at(address, size)))
        self['glDeleteBuffers'] = lambda count, names: None


def bare_view(models, **extra):
    view = object.__new__(lv.LevelViewport)
    view.__dict__.update(_models=models, _ext=FakeExt(), _cull_masks={}, _cull_counts={}, _cull_camera=None,
                         _stats_base=None, show_hidden=False, _eye=(0.0, 0.0, 0.0), _yaw=0.0, _pitch=0.0,
                         _near=0.1, _far=1000.0, _hidden_zones=set(), show_actors=True, show_spawn_preview=False)
    view.__dict__.update(extra)
    return view


def translation_rows(*xs):
    rows = array('f')
    for x in xs:
        rows.extend([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, 0, 0, 1])
    return rows


class UpdateCullingTests(unittest.TestCase):
    def model(self, buffer, rows, path='m'):
        return dict(path=path, hidden=False, groups=[(buffer, len(rows) // 16, False, ())], instance_rows={buffer: rows},
                    sphere=((0.0, 0.0, 0.0), 1.0))

    def test_visible_rows_are_uploaded_once_per_change(self):
        rows = translation_rows(10.0, -10.0, 20.0, -30.0)   # yaw 0 looks along +X
        view = bare_view({'m': self.model(7, rows)})
        view._update_culling(800, 600)
        self.assertEqual(view._cull_counts, {7: 2})
        (name, offset, data), = view._ext.uploads
        self.assertEqual((name, offset), (7, 0))
        self.assertEqual(data, translation_rows(10.0, 20.0).tobytes())
        view._update_culling(800, 600)                      # same camera: nothing to do
        self.assertEqual(len(view._ext.uploads), 1)
        view._yaw = 180.0                                   # turn around
        view._update_culling(800, 600)
        self.assertEqual(view._cull_counts, {7: 2})
        self.assertEqual(view._ext.uploads[-1][2], translation_rows(-10.0, -30.0).tobytes())
        view._yaw, view._pitch = 90.0, 89.0                 # nothing in view: no upload, count 0
        view._update_culling(800, 600)
        self.assertEqual((view._cull_counts, len(view._ext.uploads)), ({7: 0}, 2))
        view._yaw, view._pitch = 0.0, 0.0
        view._update_culling(800, 600)
        self.assertEqual(view._ext.uploads[-1][2], translation_rows(10.0, 20.0).tobytes())
        view._cull = None                                   # draw list rebuilt: masks carry over
        view._update_culling(800, 600)
        self.assertEqual(len(view._ext.uploads), 3)

    def test_fully_visible_buffer_is_not_touched_and_hidden_models_are_skipped(self):
        hidden = self.model(3, translation_rows(-50.0), 'h'); hidden['hidden'] = True
        view = bare_view({'m': self.model(7, translation_rows(10.0, 12.0)), 'h': hidden})
        view._update_culling(800, 600)
        self.assertEqual((view._ext.uploads, view._cull_counts), ([], {}))

    def test_without_buffer_sub_data_nothing_is_culled(self):
        view = bare_view({'m': self.model(7, translation_rows(-10.0))})
        del view._ext['glBufferSubData']
        view._update_culling(800, 600)
        self.assertEqual(view._cull_counts, {})


# Draw list and zone toggles -------------------------------------------------------------------
def random_models(rng, count=12):
    models = []
    for m in range(count):
        slots = {i: f'mat/{rng.randrange(6)}.material' for i in range(3)}
        batches = [(rng.randrange(3), t * 4, 4, rng.random() > 0.2) for t in range(rng.randrange(1, 6))]
        groups = []
        for g in range(rng.randrange(0, 4)):
            changes = tuple(sorted({(rng.randrange(3), f'mat/r{rng.randrange(3)}.material') for _ in range(rng.randrange(3))}))
            groups.append((100 * m + g, rng.randrange(1, 9), bool(rng.randrange(2)), changes))
        models.append(dict(path=f'model/{rng.randrange(5)}_{m}.model', hidden=rng.random() < 0.2, slots=slots,
                           batches=batches, groups=groups, groups_version=m + 1))
    return models


class DrawListTests(unittest.TestCase):
    def test_cached_items_equal_draw_items(self):
        rng = random.Random(4)
        for trial in range(30):
            models = random_models(rng)
            show_hidden = bool(trial % 2)
            visible = lambda model: () if model['hidden'] and not show_hidden else model['groups']
            for level in (0, 1, 2):
                for overrides in (True, False):
                    want = lv.draw_items(models, level, visible, overrides)
                    self.assertEqual(lv.cached_draw_items(models, level, visible, overrides), want)
                    self.assertEqual(lv.cached_draw_items(models, level, visible, overrides), want)   # cached
            changed = models[trial % len(models)]
            changed['groups'] = changed['groups'][:1]; changed['groups_version'] += 1000
            self.assertEqual(lv.cached_draw_items(models, 2, visible), lv.draw_items(models, 2, visible))
        self.assertEqual(lv.cached_draw_items([], 2, lambda m: ()), [])

    def test_zone_toggle_rebuilds_only_models_in_that_zone(self):
        uploads = []
        counter = iter(range(1, 100))

        def buffer(target, rows):
            uploads.append(rows.tobytes()); return next(counter)
        a = dict(zones=[('za', (False, ()), translation_rows(1.0))], actor_zones=[], groups=[], groups_for=None)
        b = dict(zones=[('zb', (False, ()), translation_rows(2.0))], actor_zones=[], groups=[], groups_for=None)
        view = bare_view({'a': a, 'b': b}, _buffer=buffer, _delete_vaos=lambda models: None, GL_ARRAY_BUFFER=1)
        lv.LevelViewport._merge_groups(view)
        self.assertEqual(len(uploads), 2)
        first = a['groups'][0][0]
        view._cull_masks[first], view._cull_counts[first] = 'stale', 0
        view._hidden_zones = {'za'}
        lv.LevelViewport._merge_groups(view)
        self.assertEqual((len(uploads), a['groups'], b['groups'][0][1]), (2, [], 1))
        self.assertNotIn(first, view._cull_masks); self.assertNotIn(first, view._cull_counts)   # GL reuses names
        view.show_actors = False                           # neither model has actor rows
        lv.LevelViewport._merge_groups(view)
        self.assertEqual(len(uploads), 2)
        view._hidden_zones = {'zc'}                         # a zone neither model uses
        lv.LevelViewport._merge_groups(view)
        self.assertEqual(len(uploads), 3)                   # only 'a' comes back
        self.assertEqual(a['instance_rows'][a['groups'][0][0]].tobytes(), translation_rows(1.0).tobytes())


# Loader -----------------------------------------------------------------------------------------
import queue   # noqa: E402
import threading   # noqa: E402
from concurrent.futures import ThreadPoolExecutor   # noqa: E402

from rc2016.levels import panel   # noqa: E402


class LoaderTests(unittest.TestCase):
    def test_ordered_results_keep_order_and_window(self):
        submitted = []
        with ThreadPoolExecutor(3) as pool:
            def work(item):
                return item * 2
            original = pool.submit

            def submit(function, item):
                submitted.append(item); return original(function, item)
            pool.submit = submit
            seen = []
            for item, future in panel.ordered_results(pool, range(10), work, 3):
                self.assertLessEqual(len(submitted) - len(seen), 3 + 1)   # the window plus the item in hand
                seen.append((item, future.result()))
        self.assertEqual(seen, [(i, 2 * i) for i in range(10)])
        with ThreadPoolExecutor(1) as pool:
            self.assertEqual(list(panel.ordered_results(pool, [], lambda x: x, 2)), [])
            cancel = threading.Event(); cancel.set()
            self.assertEqual(list(panel.ordered_results(pool, [1, 2], lambda x: x, 2, cancel)), [])

    def test_level_loads_each_material_once_and_posts_models_by_placement_count(self):
        def static_model(path, count, material):
            return dict(path=path, hidden=False, count=count, groups={(False, ()): translation_rows(*range(count))})
        scenes = {'z1.zone': dict(zone='z1.zone', instances=3, points=[(0.0, 0.0, 0.0)],
                                  models=[static_model('a.model', 1, 'm/a'), static_model('b.model', 2, 'm/b')]),
                  'z2.zone': dict(zone='z2.zone', instances=2, points=[(1.0, 2.0, 3.0)],
                                  models=[static_model('c.model', 4, 'm/c')])}

        class Catalog:
            factories, zone_actors = [], {}

            def __init__(self, archive): pass
            def add_zone(self, zone, scene): return 0
            def candidates(self): return []
            def descriptor(self, path): return {}
            def summary(self): return dict(reasons={}, duplicates=[], resolved=0, drawn=0)
        materials = {'a.model': ['m/shared', 'm/a'], 'b.model': ['m/shared', 'm/b'], 'c.model': ['m/c', 'm/shared']}
        loaded = []
        lock = threading.Lock()

        def geometry(archive, path):
            return dict(materials=[dict(index=i, name=str(i), path=p) for i, p in enumerate(materials[path])],
                        skinned=False, bind_usable=True, source_sha256=path)

        def entries(archive, todo, done, cache):
            with lock:
                loaded.extend(m['path'] for m in todo)
            return {m['path']: dict(path=m['path']) for m in todo}
        out = queue.Queue()
        from rc2016.levels import actors as actor_module, spawn_format
        with patch.object(actor_module, 'ActorCatalog', Catalog), \
                patch.object(spawn_format, 'preview_placements', return_value=([], [])), \
                patch.object(spawn_format, 'preview_summary', return_value=dict(reasons={})), \
                patch.object(scene, 'level_zone_paths', return_value=['z1.zone', 'z2.zone']), \
                patch.object(scene, 'zone_scene', side_effect=lambda archive, zone: scenes[zone]), \
                patch.object(scene, 'model_geometry', side_effect=geometry), \
                patch.object(scene, 'material_entries', side_effect=entries), \
                patch.object(panel, 'load_sky', return_value=None):
            panel.load_level(lambda: object(), 'x.level', out, threading.Event(), workers=2)
        events = list(out.queue)
        kinds = [e[0] for e in events]
        self.assertEqual([e[1] for e in events if e[0] == 'model'], ['c.model', 'b.model', 'a.model'])
        self.assertEqual(sorted(loaded), ['m/a', 'm/b', 'm/c', 'm/shared'])
        posted = [p for e in events if e[0] == 'materials' for p in e[1]]
        self.assertEqual(sorted(posted), sorted(loaded))
        self.assertEqual(kinds[-1], 'done')
        self.assertEqual(events[-1][1]['materials'], 4)
        self.assertLess(kinds.index('model'), kinds.index('materials'))
        self.assertEqual(next(e[1] for e in events if e[0] == 'bounds'), ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0)))

    def test_drop_uploaded_pixels_tolerates_concurrent_inserts(self):
        class Growing(dict):
            def items(self):
                result = super().items()
                self[f'new{len(self)}'] = None   # an insert while the caller iterates
                return result
        cache = Growing(a=dict(path='t/a', pixels=b'x' * 70000))
        panel.drop_uploaded_pixels(cache, {'m': dict(_c=dict(path='t/a'))}, set())
        self.assertNotIn('pixels', cache['a'])


# Shader program binaries ------------------------------------------------------------------------
from rc2016.viewer import shading   # noqa: E402


class FakeShading:
    """Stands in for Shading's GL entry points: programs link, binaries round-trip by content."""
    def __init__(self, accept_binary=True):
        self.compiled, self.loaded, self.deleted, self.accept, self.next = [], [], [], accept_binary, 10
        self.linked = {}

    def glCreateProgram(self):
        self.next += 1; return self.next

    def glGetProgramiv(self, program, name, out):
        if name == 0x8B82:
            out._obj.value = 1 if program in self.linked else 0
        elif name == 0x8741:
            out._obj.value = len(self.linked.get(program, b''))

    def glGetUniformLocation(self, program, name):
        return len(name)

    def glDeleteProgram(self, program):
        self.deleted.append(program)

    def link_source(self, vertex, fragment, attributes, retrievable=False):
        program = self.glCreateProgram()
        self.linked[program] = (vertex + '|' + fragment).encode()
        self.compiled.append((program, retrievable))
        return program

    def binary_functions(self):
        def get_binary(program, size, written, binary_format, blob):
            data = self.linked[program]
            ctypes.memmove(blob, data, len(data))
            written._obj.value, binary_format._obj.value = len(data), 0x1234

        def program_binary(program, binary_format, data, length):
            self.loaded.append((binary_format, bytes(data[:length])))
            if self.accept and binary_format == 0x1234:
                self.linked[program] = bytes(data[:length])
        return dict(glGetProgramBinary=get_binary, glProgramBinary=program_binary, glProgramParameteri=lambda *a: None,
                    driver=(b'vendor', b'renderer', b'4.6'))


def fake_shading(fake):
    program = object.__new__(shading.Shading)
    program.binary = fake.binary_functions()
    for name in ('glCreateProgram', 'glGetProgramiv', 'glGetUniformLocation', 'glDeleteProgram'):
        setattr(program, name, getattr(fake, name))
    program._link_source = fake.link_source
    return program


class ProgramCacheTests(unittest.TestCase):
    def test_second_link_loads_the_stored_binary(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(shading, 'PROGRAM_CACHE_DIR', Path(folder)):
            fake = FakeShading()
            program = fake_shading(fake)
            first, names = program._link('vs', 'fs', {'a': 1}, ('uA',))
            self.assertEqual(fake.compiled, [(first, True)])
            second, again = fake_shading(fake)._link('vs', 'fs', {'a': 1}, ('uA',))
            self.assertEqual(len(fake.compiled), 1)
            self.assertEqual(fake.loaded, [(0x1234, b'vs|fs')])
            self.assertEqual(names, again)
            self.assertNotEqual(first, second)
            fake_shading(fake)._link('vs', 'fs2', {'a': 1}, ('uA',))   # other source: compiled
            self.assertEqual(len(fake.compiled), 2)

    def test_rejected_binary_falls_back_to_source(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(shading, 'PROGRAM_CACHE_DIR', Path(folder)):
            fake = FakeShading()
            fake_shading(fake)._link('vs', 'fs', {}, ())
            fake.accept = False   # a driver update invalidates stored binaries
            program, _names = fake_shading(fake)._link('vs', 'fs', {}, ())
            self.assertEqual(len(fake.compiled), 2)
            self.assertEqual(len(fake.deleted), 1)
            self.assertIn(program, fake.linked)

    def test_without_binary_support_nothing_is_stored(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(shading, 'PROGRAM_CACHE_DIR', Path(folder)):
            fake = FakeShading()
            program = fake_shading(fake); program.binary = None
            program._link('vs', 'fs', {}, ())
            self.assertEqual((fake.compiled[0][1], list(Path(folder).iterdir())), (False, []))

    def test_key_and_file_format(self):
        key = shading.program_cache_key
        base = key('v', 'f', {'a': 1}, (b'x', b'y', b'z'))
        self.assertEqual(base, key('v', 'f', {'a': 1}, (b'x', b'y', b'z')))
        self.assertEqual(len({base, key('v', 'g', {'a': 1}, (b'x', b'y', b'z')), key('v', 'f', {'a': 2}, (b'x', b'y', b'z')),
                              key('v', 'f', {'a': 1}, (b'x', b'y', b'w')), key('vf', '', {'a': 1}, (b'x', b'y', b'z'))}), 5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'p.bin'
            shading.write_program_binary(path, 7, b'blob')
            self.assertEqual(shading.read_program_binary(path), (7, b'blob'))
            path.write_bytes(path.read_bytes()[:-1])
            self.assertIsNone(shading.read_program_binary(path))
            self.assertIsNone(shading.read_program_binary(Path(folder) / 'missing.bin'))


# Model viewport: mipmaps, lighting per frame, program memo ----------------------------------------
from rc2016.viewer.viewport import MIPMAP_ROLES, ModelViewport   # noqa: E402


class FakeGL:
    def __init__(self, errors=()):
        self.calls, self.errors = [], list(errors)

    def glGetError(self):
        return self.errors.pop(0) if self.errors else 0

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
        return call


class MipmapTests(unittest.TestCase):
    def view(self, errors=(), anisotropy=8.0, generate=True):
        view = object.__new__(ModelViewport)
        view._gl = FakeGL(errors)
        generated = []
        view._mipmap_support = dict(generate=generated.append if generate else None, anisotropy=anisotropy, compressed=True)
        return view, generated

    def filters(self, view):
        return [args[2] for name, args in view._gl.calls if name == 'glTexParameteri' and args[1] == view.GL_TEXTURE_MIN_FILTER]

    def test_generated_chain_uses_trilinear_and_anisotropy(self):
        view, generated = self.view()
        view._build_mipmaps(compressed=True)
        self.assertEqual(generated, [view.GL_TEXTURE_2D])
        self.assertEqual(self.filters(view), [view.GL_LINEAR_MIPMAP_LINEAR])
        self.assertIn(('glTexParameterf', (view.GL_TEXTURE_2D, view.GL_TEXTURE_MAX_ANISOTROPY, 8.0)), view._gl.calls)

    def test_refused_compressed_generation_keeps_top_mip_and_is_not_retried(self):
        view, generated = self.view(errors=[0x502])
        view._build_mipmaps(compressed=True)
        self.assertEqual(self.filters(view), [])
        self.assertFalse(view._mipmap_support['compressed'])
        view._build_mipmaps(compressed=True)
        self.assertEqual(len(generated), 1)
        view._build_mipmaps(compressed=False)               # uncompressed textures still get mips
        self.assertEqual((len(generated), self.filters(view)), (2, [view.GL_LINEAR_MIPMAP_LINEAR]))

    def test_no_generate_entry_point_or_no_anisotropy(self):
        view, _generated = self.view(generate=False)
        view._build_mipmaps(compressed=False)
        self.assertEqual(view._gl.calls, [])
        view, _generated = self.view(anisotropy=0.0)
        view._build_mipmaps(compressed=False)
        self.assertNotIn('glTexParameterf', [name for name, _args in view._gl.calls])

    def test_lookup_roles_keep_their_top_mip(self):
        class Upload:
            _mipmap_next = True

            def __init__(self): self.seen = []

            def _upload_texture(self, texture):
                self.seen.append((texture['path'], self._mipmap_next)); return len(self.seen)
        upload = Upload()
        entry = dict(_c=dict(path='t/a'), _pick=dict(path='t/a'), _n=dict(path='t/n'), _puddle_atlas=dict(path='t/p'))
        uploaded = ModelViewport._gl_entry(upload, entry, {})
        self.assertEqual(upload.seen, [('t/a', True), ('t/n', True), ('t/a', False), ('t/p', False)])
        self.assertEqual(uploaded['_c'], (1, False)); self.assertEqual(uploaded['_pick'], (3, False))
        self.assertTrue(upload._mipmap_next)
        self.assertTrue({'_c', '_g', '_n'} <= MIPMAP_ROLES)
        self.assertFalse({'_pick', '_sequence', '_vehicle_p2', '_puddle_atlas'} & MIPMAP_ROLES)


class FrameStateTests(unittest.TestCase):
    def test_lighting_is_read_once_per_frame(self):
        class GL(FakeGL):
            def __init__(self):
                super().__init__(); self.readbacks = 0

            @property
            def glGetFloatv(self):
                gl = self

                class Function:
                    argtypes = None

                    def __call__(self, name, out):
                        gl.readbacks += 1
                        for i in range(16):
                            out[i] = 1.0 if i % 5 == 0 else 0.0
                return Function()
        view = object.__new__(ModelViewport)
        view._gl, view._shading = GL(), FakeGL()
        view.lighting, view.tonemap, view._environment = ModelViewport.LIGHTINGS[0], ModelViewport.TONEMAPS[1], None
        uniforms = {name: i for i, name in enumerate(shading.LIGHTING_UNIFORMS)}
        view._apply_lighting(uniforms); view._apply_lighting(uniforms)
        self.assertEqual(view._gl.readbacks, 1)
        calls = [c for c in view._shading.calls if c[0] == 'glUniform1i']
        self.assertEqual(calls[:3], [('glUniform1i', (uniforms['uLightingEnabled'], 1)),
                                  ('glUniform1i', (uniforms['uLighting'], 0)), ('glUniform1i', (uniforms['uTonemap'], 1))])
        self.assertEqual(calls[:6], calls[6:12])   # six integer uniforms per call (uFogOn, uEnvTex, uGradeTex among them)
        view._frame_lighting = None   # next frame
        view._apply_lighting(uniforms)
        self.assertEqual(view._gl.readbacks, 2)

    def test_surface_program_is_memoised_per_entry_and_translation_state(self):
        class Programs:
            def __init__(self): self.calls = 0

            def native_program(self, translation, srgb, instanced):
                self.calls += 1; return ('native', translation['translation_sha256'], tuple(sorted(srgb.items())))

            def instanced_program(self):
                self.calls += 1; return ('instanced', None)
        view = object.__new__(ModelViewport)
        view._shading, view.native_translation, view._bone_count = Programs(), True, 0
        view._schedule_draw = lambda: None
        entry = dict(native_translation=dict(translation_sha256='t'), native_textures={3: (9, True)})
        first = view._surface_program(entry, 2, instanced=True)
        self.assertEqual(first, ('native', 't', ((3, True),)))
        self.assertIs(view._surface_program(entry, 2, instanced=True), first)
        self.assertEqual(view._shading.calls, 1)
        self.assertEqual(view._surface_program(entry, 1, instanced=True), ('instanced', None))
        view.set_native_translation(False)
        self.assertEqual(view._surface_program(entry, 2, instanced=True), ('instanced', None))
        other = dict(entry)   # a new entry (even with equal contents) is resolved again
        view.native_translation = True
        self.assertEqual(view._surface_program(other, 2, instanced=True), first)
        self.assertEqual(view._shading.calls, 4)
        view._shading = Programs()   # a new context: the memo starts over
        view._surface_program(entry, 2, instanced=True)
        self.assertEqual(view._shading.calls, 1)


class LevelViewportPacingTests(unittest.TestCase):
    def test_stats_are_throttled_with_a_trailing_update(self):
        view = bare_view({})
        shown, scheduled = [], []
        view.on_stats = shown.append
        view.stats = lambda: 'stats'
        view.after = lambda ms, function: scheduled.append(function)
        view._stats_time, view._stats_pending = 0.0, False
        with patch.object(lv.time, 'perf_counter', side_effect=[10.0, 10.1, 10.2, 10.3]):
            view._emit_stats(); view._emit_stats(); view._emit_stats()
            self.assertEqual((shown, len(scheduled)), (['stats'], 1))
            scheduled[0]()
        self.assertEqual(shown, ['stats', 'stats'])

    def test_stats_counts_are_cached_and_report_culled_instances(self):
        model = dict(path='m', hidden=False, triangles=10, groups=[(7, 4, False, ()), (8, 2, True, ())], actors=[], spawns=[])
        view = bare_view({'m': model}, _materials={'a': {}}, _texture_names={'t': (1, False)}, _draw_list=[1, 2],
                         last_draw_ms=1.0, _speed=2.0, native_translation=True, native_program_switches=1)
        view._cull_counts = {7: 1}
        stats = view.stats()
        self.assertEqual((stats['placed'], stats['instances'], stats['triangles'], stats['draws']), (6, 3, 30, 2))
        view._cull_counts[8] = 0
        self.assertEqual(view.stats()['instances'], 3)       # cached until invalidated
        view._stats_base = None
        self.assertEqual(view.stats()['instances'], 1)

    def test_materials_upload_within_a_budget_and_continue_later(self):
        view = bare_view({}, _materials={}, _texture_names={}, _material_ops={('a', 2, 1): 'old'}, _mode='Shaded')
        view._make_current = lambda: True
        view._gl = FakeGL()
        view._gl_entry = lambda entry, names: dict(entry)
        view._schedule_draw = lambda: None
        later = []
        view.after = lambda ms, function: later.append(function) or 'id'
        view.MATERIAL_BUDGET_SECONDS = 0.0   # one material per tick
        view.add_materials({'a': {'v': 1}, 'b': {'v': 2}, 'c': {'v': 3}})
        self.assertEqual(list(view._materials), ['a'])
        self.assertEqual(view._material_ops, {})
        self.assertEqual(len(later), 1)
        later.pop()()
        self.assertEqual(list(view._materials), ['a', 'b'])
        view.after_cancel = lambda ident: None
        view.flush_materials()
        self.assertEqual(list(view._materials), ['a', 'b', 'c'])
        self.assertEqual((view._material_queue, view._compile_queue), ({}, []))


if __name__ == '__main__':
    unittest.main()
