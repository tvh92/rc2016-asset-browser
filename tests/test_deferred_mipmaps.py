"""Level loads queue mip generation and build it after the loader is done."""
import unittest
from collections import deque

from rc2016.levels.viewport import LevelViewport
from rc2016.viewer.viewport import ModelViewport


class FakeGL:
    def __init__(self):
        self.calls = []

    def glGetError(self):
        return 0

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
        return call


def level_view():
    view = object.__new__(LevelViewport)
    view._gl = FakeGL()
    view._make_current = lambda: True
    view.built, view.scheduled, view.draws = [], [], 0
    view._build_mipmaps = lambda compressed: view.built.append((view._gl.calls[-1][1][1], compressed))
    view.after = lambda ms, function: view.scheduled.append(function) or f'after#{len(view.scheduled)}'
    view.after_cancel = lambda ident: view.scheduled.append(('cancel', ident))

    def draw():
        view.draws += 1
    view._schedule_draw = draw
    view._mipmap_queue, view._loading_done = deque([(5, True), (6, False), (7, True)]), False
    return view


class DeferredMipmapTests(unittest.TestCase):
    def test_upload_queues_instead_of_generating_when_deferred(self):
        self.assertFalse(ModelViewport.DEFER_MIPMAPS)
        self.assertTrue(LevelViewport.DEFER_MIPMAPS)

    def test_built_after_the_load_with_each_texture_bound(self):
        view = level_view()
        view._material_queue, view._compile_queue = {}, []
        view.finish_load()
        self.assertEqual(view.built, [(5, True), (6, False), (7, True)])
        self.assertEqual(view._gl.calls[-1], ('glBindTexture', (view.GL_TEXTURE_2D, 0)))
        self.assertEqual(view.draws, 1)

    def test_waits_for_queued_materials_then_the_pump_builds(self):
        view = level_view()
        view._material_queue, view._compile_queue = {'m': {}}, []
        view.finish_load()
        self.assertEqual(view.built, [])
        view._materials = {'m': {}}   # the pump drains its last queued material
        view._pump_materials()        # and, with the load done, starts the mip build
        self.assertEqual(view._material_queue, {})
        self.assertEqual(len(view.built), 3)

    def test_budget_continues_on_a_later_tick(self):
        view = level_view()
        view.build_deferred_mipmaps(budget=0.0)   # at least one per tick
        self.assertEqual(view.built, [(5, True)])
        self.assertEqual(view.scheduled, [view.build_deferred_mipmaps])
        view.build_deferred_mipmaps(budget=float('inf'))
        self.assertEqual(len(view.built), 3)

    def test_flush_for_captures_builds_everything(self):
        view = level_view()
        view._material_queue, view._compile_queue = {}, []
        view._mipmap_pump = 'after#9'
        view.flush_materials()
        self.assertIn(('cancel', 'after#9'), view.scheduled)
        self.assertEqual(len(view.built), 3)


    def test_waits_while_the_camera_moves(self):
        import time
        view = level_view()
        view._camera_changed_at = time.perf_counter()
        view.build_deferred_mipmaps()
        self.assertEqual(view.built, [])
        self.assertEqual(view.scheduled, [view.build_deferred_mipmaps])
        view._camera_changed_at = time.perf_counter() - 10
        view.build_deferred_mipmaps(budget=float('inf'))
        self.assertEqual(len(view.built), 3)

if __name__ == '__main__':
    unittest.main()
