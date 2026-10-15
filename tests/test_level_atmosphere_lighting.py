"""Level atmosphere lighting selection in the level viewport (no GL, no Tk window)."""
import time
import unittest

import numpy as np

from rc2016.levels.viewport import LevelViewport
from rc2016.viewer.viewport import ModelViewport
from tests.test_perf_wp5 import FakeGL, bare_view


def maps(tag):
    return dict(sh=np.zeros((9, 3), np.float32), bands=np.zeros((384, 128, 3), np.float32), sun_direction=np.array([0, 0, 1.0]),
                sun=np.ones(3), strength=1.0, exposure=0.0, tag=tag, probe='textures/x/0x1/reflection.texture',
                sun_rgb=[0.7, 0.6, 0.5])


def level_view(sky=None, **extra):
    view = bare_view({}, lighting='Viewer rig', tonemap=LevelViewport.TONEMAPS[0], lighting_note=None, _environment=None,
                     _environment_lighting=None, _environment_texture=None, _environment_texture_stale=False,
                     _environment_loading=False, _frame_lighting=None, _sky=sky, _sky_gl=None, **extra)
    view.pending = []
    view.after = lambda _ms, callback: view.pending.append(callback)
    view._schedule_draw = lambda: None
    return view


def settle(view):
    deadline = time.time() + 5
    while view._environment_loading and time.time() < deadline:
        while view.pending:
            view.pending.pop(0)()
        time.sleep(0.01)
    while view.pending:
        view.pending.pop(0)()


class SelectionTests(unittest.TestCase):
    def test_level_viewport_offers_the_source_and_model_viewport_does_not(self):
        self.assertIn('Level atmosphere', LevelViewport.LIGHTINGS)
        self.assertIn('Level atmosphere', LevelViewport.ENVIRONMENT_LIGHTINGS)
        self.assertEqual(LevelViewport.LIGHTINGS[0], 'Viewer rig')       # opt-in: the default is unchanged
        self.assertNotIn('Level atmosphere', ModelViewport.LIGHTINGS)

    def test_loads_the_sky_environment_and_reports_it(self):
        view = level_view(dict(environment_loader=lambda: maps('level')))
        view.set_lighting('Level atmosphere')
        settle(view)
        self.assertEqual(view._environment['tag'], 'level')
        self.assertEqual(view._environment_lighting, 'Level atmosphere')
        self.assertIn('0x1', view.lighting_note)
        self.assertIn('0.70, 0.60, 0.50', view.lighting_note)

    def test_missing_probe_keeps_the_rig_and_says_why(self):
        view = level_view(None)
        view.set_lighting('Level atmosphere')
        settle(view)
        self.assertFalse(view._environment)
        self.assertIn('Level atmosphere unavailable', view.lighting_note)
        self.assertIn('reflection probe', view.lighting_note)

    def test_sky_arriving_after_selection_loads_it(self):
        view = level_view(None)
        view.lighting = 'Level atmosphere'
        view.set_lighting()
        settle(view)
        self.assertFalse(view._environment)
        view.set_sky(dict(environment_loader=lambda: maps('late')))
        settle(view)
        self.assertEqual(view._environment['tag'], 'late')

    def test_switching_sources_drops_the_previous_maps(self):
        view = level_view(dict(environment_loader=lambda: maps('level')))
        view._environment, view._environment_lighting, view._environment_texture = maps('hdri'), 'Shared HDRI', 5
        view.lighting = 'Shared HDRI'
        view.set_lighting('Level atmosphere')
        settle(view)
        self.assertEqual(view._environment['tag'], 'level')
        self.assertTrue(view._environment_texture_stale)

    def test_new_level_forgets_its_environment(self):
        view = level_view(dict(environment_loader=lambda: maps('level')))
        view.set_lighting('Level atmosphere')
        settle(view)
        view._reset_level_environment()
        self.assertIsNone(view._environment)
        self.assertIsNone(view._environment_lighting)

    def test_lighting_state_only_uses_maps_that_belong_to_the_selection(self):
        class GL(FakeGL):
            def __init__(self):
                super().__init__()
                self.glGetFloatv = lambda _name, out: [out.__setitem__(i, float(i % 5 == 0)) for i in range(16)]
        view = level_view(None, _gl=GL(), _shading=FakeGL(), _scene_effects=None)
        view._upload_environment = lambda: 3
        view.lighting, view._environment, view._environment_lighting = 'Level atmosphere', maps('x'), 'Shared HDRI'
        self.assertFalse(view._lighting_state()['hdri'])                # maps of another source
        view._frame_lighting = None
        view._environment_lighting = 'Level atmosphere'
        state = view._lighting_state()
        self.assertTrue(state['hdri'])
        self.assertEqual(state['sun_colour'], (1.0, 1.0, 1.0))


if __name__ == '__main__':
    unittest.main()
