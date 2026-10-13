import unittest

from rc2016.levels.scene_effects import SHADOW_REFRESH_SECONDS, SceneEffects


class View:
    def __init__(self):
        self.after_calls = []
        self.cancelled = []
        self.draws = 0

    def after(self, ms, callback):
        self.after_calls.append((ms, callback))
        return f'after#{len(self.after_calls)}'

    def after_cancel(self, ident):
        self.cancelled.append(ident)

    def _schedule_draw(self):
        self.draws += 1


def effects():
    e = SceneEffects.__new__(SceneEffects)   # no GL context: only the shadow scheduling state
    e.shadow_key = None
    e.shadow_time = 0.0
    e.shadow_retry = None
    return e


class ShadowRefreshTests(unittest.TestCase):
    def test_first_map_and_new_direction_render_at_once(self):
        e, view = effects(), View()
        self.assertTrue(e._shadow_due(view, (1, (0.0, 1.0, 0.0)), 100.0))
        e.shadow_key, e.shadow_time = (1, (0.0, 1.0, 0.0)), 100.0
        self.assertTrue(e._shadow_due(view, (1, (1.0, 0.0, 0.0)), 100.1))
        self.assertEqual(view.after_calls, [])

    def test_content_changes_are_rate_limited_with_one_retry(self):
        e, view = effects(), View()
        e.shadow_key, e.shadow_time = (1, (0.0, 1.0, 0.0)), 100.0
        for revision in range(2, 12):   # a burst of model arrivals
            self.assertFalse(e._shadow_due(view, (revision, (0.0, 1.0, 0.0)), 100.5))
        self.assertEqual(len(view.after_calls), 1)
        self.assertGreater(view.after_calls[0][0], 0)
        view.after_calls[0][1]()
        self.assertEqual(view.draws, 1)
        self.assertIsNone(e.shadow_retry)
        self.assertTrue(e._shadow_due(view, (12, (0.0, 1.0, 0.0)), 100.0 + SHADOW_REFRESH_SECONDS))

    def test_close_cancels_a_pending_retry(self):
        e, view = effects(), View()
        e.shadow_key, e.shadow_time = (1, (0.0, 1.0, 0.0)), 100.0
        e._shadow_due(view, (2, (0.0, 1.0, 0.0)), 100.1)
        e.ao, e.textures, e.fbos = None, [], []
        e.close()
        self.assertEqual(view.cancelled, ['after#1'])
        self.assertIsNone(e.shadow_retry)


if __name__ == '__main__':
    unittest.main()
