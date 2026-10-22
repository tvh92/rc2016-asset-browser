"""Viewer layout: panes side by side ("Vertical": a tall viewport with its controls beside it) or
stacked ("Horizontal": a wide viewport with its controls below).

``ttk.Panedwindow`` cannot change its orientation once built, so the viewer splits use
``Splitter``: two panes and a draggable sash that work in both orientations and remember the
sash size of each. One ``SplitLayout`` owns all of them; panels with several control groups also
listen, to re-arrange those groups (``regrid``).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from rc2016.app.theme import color, track

VERTICAL, HORIZONTAL = "Vertical", "Horizontal"
MODES = (VERTICAL, HORIZONTAL)


def regrid(widget, **options) -> None:
    """Move a gridded widget. One hidden with ``grid_remove`` stays hidden at its new place."""
    hidden = widget.winfo_manager() != "grid"
    widget.grid(**options)
    if hidden:
        widget.grid_remove()


class Splitter(ttk.Frame):
    """Two panes (``first``, ``second``) with a sash. One pane has a fixed size that the sash changes,
    the other takes the rest, so a resize only grows or shrinks the stretching pane. The panes are
    plain frames: give the rows and columns that should stretch a weight."""

    SASH = 6
    MINIMUM = 120   # smallest pane

    def __init__(self, parent, side: int, stacked: int, fixed_first: bool = False, width: int = 800, height: int = 560):
        """``side``/``stacked``: the fixed pane's size in pixels when the panes sit side by side (a width)
        or stacked (a height). The fixed pane is the second one unless ``fixed_first``."""
        super().__init__(parent, width=width, height=height)
        self.sizes = {False: side, True: stacked}
        self.fixed_first = fixed_first
        self.is_stacked = False
        self.second_visible = True
        self.first, self.second = ttk.Frame(self), ttk.Frame(self)
        self.sash = tk.Frame(self, borderwidth=0, highlightthickness=0)
        track(self.sash, background="sash")
        self.sash.bind("<B1-Motion>", self._drag)
        self.sash.bind("<Enter>", lambda _event: self.sash.configure(background=color("accent")))
        self.sash.bind("<Leave>", lambda _event: self.sash.configure(background=color("sash")))
        self.bind("<Configure>", lambda _event: self._place())
        self.set_stacked(False)

    def set_stacked(self, stacked: bool) -> None:
        self.is_stacked = bool(stacked)
        self.sash.configure(cursor="sb_v_double_arrow" if self.is_stacked else "sb_h_double_arrow")
        self._place()

    def set_second_visible(self, visible: bool) -> None:
        """Hide the second pane and its sash (the first pane takes the whole area), or bring them back."""
        self.second_visible = bool(visible)
        if not self.second_visible:
            self.sash.place_forget()
            self.second.place_forget()
        self._place()

    def _length(self) -> int:
        return self.winfo_height() if self.is_stacked else self.winfo_width()

    def _clamp(self, size: int, total: int) -> int:
        return max(self.MINIMUM, min(size, total - self.SASH - self.MINIMUM))

    def _place(self) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        total = self._length()
        if total <= 1:
            return
        if not self.second_visible:
            self.first.place(x=0, y=0, width=width, height=height)
            return
        size = self._clamp(self.sizes[self.is_stacked], total)
        first = size if self.fixed_first else total - self.SASH - size
        rest = total - self.SASH - first
        if self.is_stacked:
            self.first.place(x=0, y=0, width=width, height=first)
            self.sash.place(x=0, y=first, width=width, height=self.SASH)
            self.second.place(x=0, y=first + self.SASH, width=width, height=rest)
        else:
            self.first.place(x=0, y=0, width=first, height=height)
            self.sash.place(x=first, y=0, width=self.SASH, height=height)
            self.second.place(x=first + self.SASH, y=0, width=rest, height=height)

    def _drag(self, event) -> None:
        total = self._length()
        if self.is_stacked:
            start = event.y_root - self.winfo_rooty() - self.SASH // 2
        else:
            start = event.x_root - self.winfo_rootx() - self.SASH // 2
        size = start if self.fixed_first else total - self.SASH - start
        self.sizes[self.is_stacked] = self._clamp(size, total)
        self._place()


class SplitLayout:
    def __init__(self, mode: str = VERTICAL):
        self.mode = mode if mode in MODES else VERTICAL
        self._splits: list[Splitter] = []
        self._listeners = []

    @property
    def stacked(self) -> bool:
        return self.mode == HORIZONTAL

    def register(self, splitter: Splitter) -> None:
        self._splits.append(splitter)
        splitter.set_stacked(self.stacked)

    def listen(self, callback) -> None:
        """``callback(stacked)`` now and after every mode change."""
        self._listeners.append(callback)
        callback(self.stacked)

    def set_mode(self, mode: str) -> None:
        if mode not in MODES or mode == self.mode:
            return
        self.mode = mode
        for splitter in self._splits:
            splitter.set_stacked(self.stacked)
        for callback in self._listeners:
            callback(self.stacked)
