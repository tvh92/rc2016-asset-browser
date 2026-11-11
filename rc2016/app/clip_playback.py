"""Clip playback on a ModelViewport: Play/Pause, a frame slider and a frame readout. The browser's Animation row and the
clip picker's viewport both play through it, so the frame clock and the effect clock behave the same in both."""
from __future__ import annotations

# Playback speed choices (label -> rate). The rate scales the frame clock only; every source frame is still shown.
SPEEDS = {'0.25×': 0.25, '0.5×': 0.5, '1×': 1.0, '2×': 2.0}
DEFAULT_SPEED = '1×'


class ClipPlayback:
    """``animation`` is a pose from ``SourcePreviewLoader.pose``: {'frames': [skin matrices], 'fps': float, ...}.

    ``speed`` scales the frame clock; with ``loop`` off a clip stops on its last frame, and Play starts it again."""

    def __init__(self, viewport, clock, play_button, slider, position, readout):
        self.viewport = viewport
        self.clock = clock   # the Tk widget whose after() drives the frames
        self.play_button, self.slider, self.position, self.readout = play_button, slider, position, readout
        self.animation = None
        self.frame = 0
        self.playing = False
        self.job = None
        self.closed = False
        self.speed = 1.0
        self.loop = True

    def start(self, animation) -> None:
        """Show ``animation`` from its first frame and play it; a one-frame clip is shown still."""
        self._cancel()
        self.animation = animation
        count = len(animation['frames'])
        self.slider.configure(from_=0, to=max(1, count - 1), state='normal')
        self.play_button.configure(state='normal')
        self.show(0)
        self.playing = count > 1
        self.play_button.configure(text='Pause' if self.playing else 'Play')
        self._sync()

    def clear(self) -> None:
        """No clip: the rest pose, with the controls disabled and the readout empty."""
        self.playing = False
        self._cancel()
        self.animation = None
        self.viewport.clear_pose()
        self.play_button.configure(state='disabled', text='Pause')
        self.slider.configure(state='disabled')
        self.readout.set('')
        self._sync()

    def pause(self) -> None:
        """Stop the frame clock; the clip stays on its current frame."""
        self.playing = False
        self.play_button.configure(text='Play')
        self._sync()

    def toggle(self) -> None:
        if not self.animation:
            return
        self.playing = not self.playing
        if self.playing and not self.loop and self.frame == len(self.animation['frames']) - 1:
            self.show(0)   # a clip that played once and stopped starts again
        self.play_button.configure(text='Pause' if self.playing else 'Play')
        self._sync()

    def step(self, offset: int) -> None:
        """Pause and move ``offset`` frames, wrapping round the clip."""
        if not self.animation:
            return
        self.pause()
        self.show(self.frame + offset)

    def set_speed(self, speed: float) -> None:
        """A new clock rate; a playing clip keeps its frame and continues at the new rate."""
        self.speed = speed
        if self.job is not None:
            self._cancel()
            self._sync()

    def set_loop(self, loop: bool) -> None:
        self.loop = loop

    def scrub(self, _value=None) -> None:
        """The slider moved: show that frame and pause."""
        if not self.animation:
            return
        frame = int(round(self.position.get()))
        if frame != self.frame:
            self.pause()
            self.show(frame)

    def show(self, frame: int) -> None:
        count = len(self.animation['frames'])
        self.frame = frame % count
        self.viewport.set_pose(self.animation['frames'][self.frame])
        self.position.set(self.frame)
        rate = '' if self.speed == 1.0 else f'  ·  {self.speed:g}×'
        self.readout.set(f'{self.frame + 1}/{count} @ {self.animation["fps"]:g} fps{rate}')

    def bind_keys(self, widget) -> None:
        """Space plays or pauses, Left and Right step one frame, while ``widget`` has the keyboard focus."""
        def key(action):
            def handler(_event):
                action()
                return 'break'
            return handler
        widget.bind('<space>', key(self.toggle))
        widget.bind('<Left>', key(lambda: self.step(-1)))
        widget.bind('<Right>', key(lambda: self.step(1)))

    def close(self) -> None:
        self.closed = True
        self.pause()

    def _sync(self) -> None:
        """Time-driven materials move only while a clip plays; the frame clock runs only while playing."""
        self.viewport.set_effects_running(bool(self.playing and self.animation), reset=not self.animation)
        if self.playing and self.job is None and not self.closed:
            self._schedule()
        elif not self.playing:
            self._cancel()

    def _schedule(self) -> None:
        delay = max(10, int(round(1000 / max(1.0, self.animation['fps'] * self.speed))))
        self.job = self.clock.after(delay, self._tick)

    def _tick(self) -> None:
        self.job = None
        if self.playing and self.animation and not self.closed:
            if not self.loop and self.frame == len(self.animation['frames']) - 1:
                self.pause()   # played once: stay on the last frame
                return
            self.show(self.frame + 1)
            self._schedule()

    def _cancel(self) -> None:
        if self.job is not None:
            self.clock.after_cancel(self.job)
            self.job = None


def playback_options(tk, ttk, parent, playback, speed_var=None, loop_var=None):
    """A frame with the Speed choice and the Loop box for ``playback``; the caller places it. ``speed_var`` (a speed
    label) and ``loop_var`` may be remembered variables; their values apply at once and on every change."""
    speed_var = speed_var or tk.StringVar(master=parent, value=DEFAULT_SPEED)
    loop_var = loop_var if loop_var is not None else tk.BooleanVar(master=parent, value=True)
    frame = ttk.Frame(parent)
    ttk.Label(frame, text='Speed').pack(side='left')
    box = ttk.Combobox(frame, textvariable=speed_var, values=tuple(SPEEDS), state='readonly', width=6)
    box.pack(side='left', padx=(4, 8))
    check = ttk.Checkbutton(frame, text='Loop', variable=loop_var)
    check.pack(side='left')

    def apply(*_args):
        playback.set_speed(SPEEDS.get(speed_var.get(), 1.0))
        playback.set_loop(bool(loop_var.get()))
        if playback.animation:
            playback.show(playback.frame)   # the readout names the rate

    speed_var.trace_add('write', apply)
    loop_var.trace_add('write', apply)
    apply()
    frame.speed_box, frame.loop_check = box, check
    return frame
