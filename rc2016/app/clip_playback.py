"""Clip playback on a ModelViewport: Play/Pause, a frame slider and a frame readout. The browser's Animation row and the
clip picker's viewport both play through it, so the frame clock and the effect clock behave the same in both."""
from __future__ import annotations


class ClipPlayback:
    """``animation`` is a pose from ``SourcePreviewLoader.pose``: {'frames': [skin matrices], 'fps': float, ...}."""

    def __init__(self, viewport, clock, play_button, slider, position, readout):
        self.viewport = viewport
        self.clock = clock   # the Tk widget whose after() drives the frames
        self.play_button, self.slider, self.position, self.readout = play_button, slider, position, readout
        self.animation = None
        self.frame = 0
        self.playing = False
        self.job = None
        self.closed = False

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
        self.play_button.configure(text='Pause' if self.playing else 'Play')
        self._sync()

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
        self.readout.set(f'{self.frame + 1}/{count} @ {self.animation["fps"]:g} fps')

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
        delay = max(10, int(round(1000 / max(1.0, self.animation['fps']))))
        self.job = self.clock.after(delay, self._tick)

    def _tick(self) -> None:
        self.job = None
        if self.playing and self.animation and not self.closed:
            self.show(self.frame + 1)
            self._schedule()

    def _cancel(self) -> None:
        if self.job is not None:
            self.clock.after_cancel(self.job)
            self.job = None
