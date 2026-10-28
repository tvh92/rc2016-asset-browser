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


class ScrollFrame(ttk.Frame):
    """A frame whose content (``inner``) scrolls when a divider leaves less room than the content needs. Each
    scrollbar appears only while its direction overflows, so nothing is cut off; with room to spare the content is
    stretched to fill the frame, as a plain frame would be. The wheel scrolls it while the pointer is over it."""

    def __init__(self, parent, **options):
        super().__init__(parent, **options)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, width=1, height=1)
        track(self.canvas, background="bg")
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", lambda _event: self._fit(), add="+")
        self.canvas.bind("<Configure>", lambda _event: self._fit(), add="+")
        # Added beside the app's other wheel handlers (3D view zoom, gallery, textures); each acts only under its pointer.
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _fit(self) -> None:
        need_width, need_height = self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        self.canvas.itemconfigure(self._window, width=max(need_width, width), height=max(need_height, height))
        self.canvas.configure(scrollregion=(0, 0, max(need_width, width), max(need_height, height)))
        for bar, overflow, place in ((self.vbar, need_height > height, dict(row=0, column=1, sticky="ns")),
                                     (self.hbar, need_width > width, dict(row=1, column=0, sticky="ew"))):
            shown = bar.winfo_manager() == "grid"
            if overflow and not shown:
                bar.grid(**place)
            elif not overflow and shown:
                bar.grid_remove()
        if need_height <= height:
            self.canvas.yview_moveto(0)
        if need_width <= width:
            self.canvas.xview_moveto(0)

    def _wheel(self, event) -> None:
        """Scroll when the pointer is over this frame's content and it overflows vertically."""
        try:
            under = self.winfo_containing(event.x_root, event.y_root)
            if self.vbar.winfo_manager() != "grid":
                return
        except (tk.TclError, KeyError):   # destroyed, or over a widget tkinter does not wrap (a combobox list)
            return
        while under is not None and under is not self:
            under = under.master
        if under is self:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


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
