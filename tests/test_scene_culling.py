"""Shadow casters stay complete when camera culling compacts instance buffers."""
import unittest

from rc2016.viewer.shading import LIGHTING_UNIFORMS
from rc2016.levels.viewport import LevelViewport, BASIS
from tests.test_perf_wp5 import bare_view, translation_rows, FakeGL


def model(rows):
    return dict(path='m', hidden=False, groups=[(7, len(rows) // 16, False, ())],
                instance_rows={7: rows}, sphere=((0., 0., 0.), 1.))


class SceneCullingTests(unittest.TestCase):
    def test_offscreen_caster_rows_restore_then_camera_reculls(self):
        rows = translation_rows(10., -10., 20., -30.)
        record = model(rows)
        view = bare_view({'m': record})
        view._update_culling(800, 600)
        self.assertEqual(view._cull_counts[7], 2)
        self.assertEqual(view._ext.uploads[-1][2], translation_rows(10., 20.).tobytes())
        view._restore_unculled_instances()
        self.assertEqual(view._ext.uploads[-1][2], rows.tobytes())
        self.assertEqual(view._cull_counts, {})
        self.assertEqual(record['instance_rows'][7].tobytes(), rows.tobytes())
        view._update_culling(800, 600)
        self.assertEqual(view._cull_counts[7], 2)
        self.assertEqual(view._ext.uploads[-1][2], translation_rows(10., 20.).tobytes())

    def test_shadow_render_sees_all_rows_and_discards_light_frame_cache(self):
        view = bare_view({'m': model(translation_rows(10., -10.))}, _frame_lighting={'main': True})
        view._update_culling(800, 600)
        observed = []
        def draw(geometry, shadow):
            observed.append((geometry, shadow, view._cull_counts.get(7, 2), view._frame_lighting))
            view._frame_lighting = {'shadow': True}
        view._draw_level = draw
        view._draw_geometry(shadow=True)
        self.assertEqual(observed, [(True, True, 2, None)])
        self.assertIsNone(view._frame_lighting)
        self.assertFalse(view._scene_geometry)

    def test_bounds_include_offscreen_casters_and_ignore_compacted_buffers(self):
        rows = translation_rows(10., -100.)
        record = model(rows)
        record.update(local_bounds=((-1., -2., -3.), (1., 2., 3.)),
                      zones=[('z', (False, ()), rows)], actor_zones=[], spawn_zones=[])
        view = bare_view({'m': record}, _scene_revision=1)
        expected = ((-101., -2., -3.), (11., 2., 3.))
        self.assertEqual(view.scene_bounds(), expected)
        view._update_culling(800, 600)
        self.assertEqual(view._cull_counts[7], 1)
        self.assertEqual(view.scene_bounds(), expected)
        view._yaw = 180.
        view._update_culling(800, 600)
        self.assertEqual(view.scene_bounds(), expected)

    def test_ao_updates_camera_rows_before_drawing_when_shadow_map_is_reused(self):
        view = bare_view({'m': model(translation_rows(10., -10., 20., -30.))})
        view.winfo_width, view.winfo_height = lambda: 800, lambda: 600
        view._cull_frame(800, 600)
        self.assertEqual(view._ext.uploads[-1][2], translation_rows(10., 20.).tobytes())
        view._yaw = 180.
        drawn_rows = []
        view._draw_level = lambda **_kwargs: drawn_rows.append(view._ext.uploads[-1][2])
        view._draw_geometry(shadow=False)
        self.assertEqual(drawn_rows, [translation_rows(-10., -30.).tobytes()])
        self.assertEqual(view._cull_counts[7], 2)
        uploads = len(view._ext.uploads)
        view._cull_frame(800, 600)
        self.assertEqual(len(view._ext.uploads), uploads)

    def test_each_geometry_matrix_gets_its_own_cached_lighting_frame(self):
        class GL(FakeGL):
            def __init__(self):
                super().__init__()
                self.matrix = tuple(float(i % 5 == 0) for i in range(16))
                self.readbacks = 0
                owner = self
                class Readback:
                    def __call__(self, _name, out):
                        owner.readbacks += 1
                        for i, value in enumerate(owner.matrix):
                            out[i] = value
                self.glGetFloatv = Readback()
        view = bare_view({}, _gl=GL(), _shading=FakeGL(), _scene_effects=None,
                         lighting=LevelViewport.LIGHTINGS[0], tonemap=LevelViewport.TONEMAPS[0],
                         _environment=None, _frame_lighting=None)
        uniforms = {name: i for i, name in enumerate(LIGHTING_UNIFORMS)}
        view._apply_lighting(uniforms)
        main_rig = bytes(view._frame_lighting['rig'])
        view._apply_lighting(uniforms)
        self.assertEqual(view._gl.readbacks, 1)
        view._gl.matrix = BASIS
        def draw(**_kwargs):
            view._apply_lighting(uniforms)
            self.assertNotEqual(bytes(view._frame_lighting['rig']), main_rig)
            view._apply_lighting(uniforms)
        view._draw_level = draw
        view._draw_geometry(shadow=True)
        self.assertEqual(view._gl.readbacks, 2)
        view._gl.matrix = tuple(float(i % 5 == 0) for i in range(16))
        view._apply_lighting(uniforms)
        self.assertEqual(view._gl.readbacks, 3)
        self.assertEqual(bytes(view._frame_lighting['rig']), main_rig)


if __name__ == '__main__':
    unittest.main()
