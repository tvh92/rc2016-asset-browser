"""Thumbnail grid for the Textures tab: every texture in the list as a small picture, drawn virtually.

Only the tiles on screen are drawn. Their thumbnails are decoded one at a time on a worker thread
(``ThumbnailFeed``), visible tiles first, and turned into Tk images on the UI thread. The layout and
selection maths reuse the Model gallery's plain functions, so the grid behaves like that tab.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from rc2016.app.theme import color, listen, track
from rc2016.app.gallery import (ImageCache, column_count, elide, photo_from_image, row_count, scroll_to_show,
                                tile_at, visible_tiles)

THUMB = 96                  # thumbnail box, pixels
TILE_W = 120
TILE_H = THUMB + 26         # picture, then one line of name
CACHE = 400                 # Tk images kept; the tiles on screen are a small subset
POLL_MS = 60                # how often finished thumbnails are turned into images
SHIFT_MASK = 0x0001         # Tk event.state bits
CONTROL_MASK = 0x0004


class ThumbnailFeed:
    """Decodes wanted thumbnails on one worker thread. ``want`` replaces the queue, so the newest
    request (the tiles on screen now) is decoded first and stale ones are dropped."""

    def __init__(self, decode):
        self.decode = decode                    # path -> Pillow image, or None when it cannot be decoded
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.wanted = []
        self.results = queue.Queue()
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='texture-thumbnails', daemon=True)
        self.thread.start()

    def want(self, paths):
        with self.lock:
            self.wanted = list(paths)
        self.wake.set()

    def clear(self):
        with self.lock:
            self.wanted = []

    def close(self):
        self.closed = True
        self.clear()
        self.wake.set()

    def _run(self):
        while not self.closed:
            with self.lock:
                path = self.wanted.pop(0) if self.wanted else None
            if path is None:
                self.wake.wait()
                self.wake.clear()
                continue
            try:
                image = self.decode(path)
            except Exception:
                image = None
            self.results.put((path, image))


class TextureGrid:
    """A scrollable, selectable grid of texture rows. ``rows`` are dicts with a 'path' key."""

    def __init__(self, parent, decode, on_change, root):
        self.on_change = on_change
        self.root = root
        self.rows = []
        self.selected = set()
        self.focus = None
        self.anchor = None
        self.photos = ImageCache(CACHE)
        self.failed = set()
        self.font = tkfont.nametofont('TkDefaultFont')
        self.frame = parent
        self.canvas = tk.Canvas(parent, highlightthickness=0, takefocus=1, yscrollincrement=TILE_H)
        track(self.canvas, background='canvas')
        listen(self.redraw)
        # A ttk scrollbar: the classic one ignores the theme on Windows.
        self.scroll = ttk.Scrollbar(parent, orient='vertical', command=self._scrolled_by)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.scroll.grid(row=0, column=1, sticky='ns')
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.canvas.bind('<Configure>', lambda _e: self.redraw())
        self.canvas.bind('<ButtonPress-1>', self._press)
        self.canvas.bind('<Left>', lambda _e: self._move(-1))
        self.canvas.bind('<Right>', lambda _e: self._move(1))
        self.canvas.bind('<Up>', lambda _e: self._move(-self._columns()))
        self.canvas.bind('<Down>', lambda _e: self._move(self._columns()))
        self.canvas.bind('<Shift-Left>', lambda _e: self._move(-1, extend=True))
        self.canvas.bind('<Shift-Right>', lambda _e: self._move(1, extend=True))
        self.canvas.bind('<Shift-Up>', lambda _e: self._move(-self._columns(), extend=True))
        self.canvas.bind('<Shift-Down>', lambda _e: self._move(self._columns(), extend=True))
        self.canvas.bind('<Control-a>', lambda _e: self.select_all())
        self.canvas.bind('<Escape>', lambda _e: self.clear_selection())
        self.feed = ThumbnailFeed(decode)
        self.polling = None
        self._poll()

    # Rows and selection --------------------------------------------------------
    def set_rows(self, rows):
        """A new list: selection and focus clear, the view returns to the top, thumbnails are kept."""
        self.rows = rows
        self.selected = set()
        self.focus = None
        self.anchor = None
        self.feed.clear()
        self.canvas.yview_moveto(0)
        self.redraw()

    def forget_thumbnails(self):
        """Another archive: its thumbnails and failures no longer apply."""
        self.photos.clear()
        self.failed.clear()
        self.feed.clear()

    def selected_indices(self):
        return sorted(self.selected)

    def clear_selection(self):
        if self.selected or self.focus is not None:
            self.selected = set()
            self.focus = None
            self.anchor = None
            self.redraw()
            self.on_change()

    def select_all(self):
        self.selected = set(range(len(self.rows)))
        self.redraw()
        self.on_change()

    def _select_only(self, index):
        self.selected = {index}
        self.focus = self.anchor = index

    def _select_range(self, index):
        start = self.anchor if self.anchor is not None else index
        low, high = sorted((start, index))
        self.selected = set(range(low, high + 1))
        self.focus = index

    def _press(self, event):
        self.canvas.focus_set()
        index = self._index_at(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        if index is None:
            self.clear_selection()
            return
        if event.state & SHIFT_MASK:
            self._select_range(index)
        elif event.state & CONTROL_MASK:
            if index in self.selected:
                self.selected.discard(index)
            else:
                self.selected.add(index)
            self.focus = self.anchor = index
        else:
            self._select_only(index)
        self.redraw()
        self.on_change()

    def _move(self, step, extend=False):
        if not self.rows:
            return
        if self.focus is None:
            target = 0
        else:
            target = min(max(self.focus + step, 0), len(self.rows) - 1)
        if extend:
            self._select_range(target)
        else:
            self._select_only(target)
        self._show(target)
        self.redraw()
        self.on_change()

    # Geometry ------------------------------------------------------------------
    def _columns(self):
        return column_count(self.canvas.winfo_width(), TILE_W)

    def _index_at(self, x, y):
        return tile_at(x, y, 0, TILE_W, TILE_H, self._columns(), len(self.rows))

    def _show(self, index):
        """Scroll so tile ``index`` is fully visible."""
        view = max(1, self.canvas.winfo_height())
        total = row_count(len(self.rows), self._columns()) * TILE_H
        offset = self.canvas.canvasy(0)
        new = scroll_to_show(index, offset, view, TILE_H, self._columns())
        if new != offset and total > 0:
            self.canvas.yview_moveto(new / total)

    def _scrolled_by(self, *args):
        self.canvas.yview(*args)
        self.redraw()

    def wheel(self, delta):
        self.canvas.yview_scroll(-int(delta / 120) * 3, 'units')
        self.redraw()

    # Drawing -------------------------------------------------------------------
    def redraw(self):
        canvas = self.canvas
        canvas.delete('all')
        columns = self._columns()
        total = len(self.rows)
        rows = row_count(total, columns)
        canvas.configure(scrollregion=(0, 0, columns * TILE_W, rows * TILE_H))
        view = max(1, canvas.winfo_height())
        offset = canvas.canvasy(0)
        first, last = visible_tiles(offset, view, TILE_H, columns, total, overscan=1)
        wanted = []
        name_width = TILE_W - 8
        for index in range(first, last):
            row = self.rows[index]
            left = (index % columns) * TILE_W
            top = (index // columns) * TILE_H
            selected = index in self.selected
            if selected:
                canvas.create_rectangle(left + 2, top + 2, left + TILE_W - 2, top + TILE_H - 2,
                                        fill=color('accent'), outline=color('focus_ring' if index == self.focus else 'accent'))
            path = row['path']
            photo = self.photos.get(path)
            if photo is not None:
                canvas.create_image(left + TILE_W // 2, top + 6 + THUMB // 2, image=photo, anchor='center')
            else:
                canvas.create_rectangle(left + 12, top + 6, left + TILE_W - 12, top + 6 + THUMB,
                                        fill=color('tile'), outline='')
                canvas.create_text(left + TILE_W // 2, top + 6 + THUMB // 2, fill=color('tile_text'),
                                   text='✕' if path in self.failed else '…')
                if path not in self.failed:
                    wanted.append(path)
            name = elide(row_name(path), self.font.measure, name_width)
            canvas.create_text(left + TILE_W // 2, top + THUMB + 14, text=name,
                               fill=color('select_fg') if selected else color('fg'),
                               font=self.font, anchor='center')
        self.feed.want(wanted)

    # Thumbnails ----------------------------------------------------------------
    def _poll(self):
        """Turn finished thumbnails into Tk images here, on the UI thread."""
        arrived = False
        try:
            while True:
                path, image = self.feed.results.get_nowait()
                photo = None
                if image is not None:
                    photo = photo_from_image(image.convert('RGB'), self.canvas)
                if photo is None:
                    self.failed.add(path)
                else:
                    self.photos.put(path, photo)
                arrived = True
        except queue.Empty:
            pass
        if arrived:
            self.redraw()
        self.polling = self.root.after(POLL_MS, self._poll)

    def close(self):
        if self.polling is not None:
            self.root.after_cancel(self.polling)
            self.polling = None
        self.feed.close()


def row_name(path):
    return path.replace('\\', '/').rsplit('/', 1)[-1]
