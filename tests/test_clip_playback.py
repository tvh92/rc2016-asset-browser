"""Clip playback shared by the browser's viewport and the picker's: speed, loop or play once, frame steps and keys."""
import unittest

from rc2016.app.clip_playback import DEFAULT_SPEED, SPEEDS, ClipPlayback, playback_options


class Recorder:
    """Stands in for the viewport, the buttons, the slider and the Tk variables."""

    def __init__(self):
        self.poses, self.value, self.text, self.config = [], 0, '', {}

    def set_pose(self, frame):
        self.poses.append(frame)

    def clear_pose(self):
        pass

    def set_effects_running(self, _running, reset=False):
        pass

    def configure(self, **options):
        self.config.update(options)

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Clock:
    """after() that runs nothing by itself: the test fires the pending tick."""

    def __init__(self):
        self.delays, self.pending = [], None

    def after(self, delay, callback):
        self.delays.append(delay)
        self.pending = callback
        return f'job{len(self.delays)}'

    def after_cancel(self, _job):
        self.pending = None

    def fire(self):
        callback, self.pending = self.pending, None
        callback()


def clip(frames=4, fps=30.0):
    return dict(frames=list(range(frames)), fps=fps)


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.readout = Clock(), Recorder()
        self.playback = ClipPlayback(Recorder(), self.clock, Recorder(), Recorder(), Recorder(), self.readout)

    def test_speed_scales_the_frame_clock_and_is_named_in_the_readout(self):
        self.playback.start(clip())
        self.playback.set_speed(0.5)
        self.assertEqual(self.clock.delays, [33, 67])   # the running tick is rescheduled at the new rate
        self.clock.fire()
        self.assertEqual(self.playback.frame, 1)
        self.assertEqual(self.readout.value, '2/4 @ 30 fps  ·  0.5×')
        self.playback.set_speed(2.0)
        self.assertEqual(self.clock.delays[-1], 17)

    def test_looping_wraps_and_play_once_stops_on_the_last_frame(self):
        self.playback.start(clip(3))
        for _ in range(3):
            self.clock.fire()
        self.assertEqual((self.playback.frame, self.playback.playing), (0, True))
        self.playback.set_loop(False)
        self.clock.fire()
        self.clock.fire()
        self.assertEqual(self.playback.frame, 2)
        self.clock.fire()   # the tick on the last frame stops the clock
        self.assertEqual((self.playback.frame, self.playback.playing, self.clock.pending), (2, False, None))
        self.playback.toggle()   # Play after the end starts the clip again
        self.assertEqual((self.playback.frame, self.playback.playing), (0, True))

    def test_a_step_pauses_and_wraps_round_the_clip(self):
        self.playback.start(clip(3))
        self.playback.step(-1)
        self.assertEqual((self.playback.frame, self.playback.playing), (2, False))
        self.playback.step(1)
        self.assertEqual(self.playback.frame, 0)

    def test_without_a_clip_the_keys_do_nothing(self):
        self.playback.step(1)
        self.playback.toggle()
        self.assertFalse(self.playback.playing)


class PlaybackWidgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        from tkinter import ttk
        try:
            cls.root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display for Tk")
        cls.tk, cls.ttk = tk, ttk

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.playback = ClipPlayback(Recorder(), Clock(), Recorder(), Recorder(), Recorder(), Recorder())
        self.playback.start(clip())

    def test_space_and_the_arrow_keys_play_and_step_over_the_bound_widget(self):
        widget = self.tk.Frame(self.root, width=20, height=20, takefocus=1)
        widget.pack()
        self.addCleanup(widget.destroy)
        self.playback.bind_keys(widget)
        widget.focus_force()
        self.root.update()
        widget.event_generate('<space>')
        self.assertFalse(self.playback.playing)
        widget.event_generate('<Right>')
        widget.event_generate('<Right>')
        widget.event_generate('<Left>')
        self.assertEqual(self.playback.frame, 1)

    def test_the_options_apply_their_variables_at_once_and_on_change(self):
        speed = self.tk.StringVar(master=self.root, value='0.25×')
        loop = self.tk.BooleanVar(master=self.root, value=False)
        frame = playback_options(self.tk, self.ttk, self.root, self.playback, speed, loop)
        self.addCleanup(frame.destroy)
        self.assertEqual((self.playback.speed, self.playback.loop), (0.25, False))
        speed.set(DEFAULT_SPEED)
        loop.set(True)
        self.assertEqual((self.playback.speed, self.playback.loop), (1.0, True))
        self.assertEqual(tuple(frame.speed_box.cget('values')), tuple(SPEEDS))


if __name__ == '__main__':
    unittest.main()
