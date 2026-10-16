"""Gallery tab: a virtualised thumbnail grid for the Model asset type.

The tab shows the models selected in the tree (selecting a folder selects every model in it) or, with
nothing or one model selected, the current search results. Thumbnails come from the cache written by
``rc2016.app.thumbnails``; missing ones are rendered by one owned renderer process, visible tiles first.
The layout, queue and selection rules are plain functions so they run without Tk.
"""
from __future__ import annotations

from collections import OrderedDict
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue

from rc2016.app.thumbnails import (DEFAULT_SIZE, RendererProcess, ThumbnailIndex, cache_dir)
from rc2016.app.workers import LatestWork

IMAGE_CACHE_SIZE = 300      # PhotoImages kept; the tiles on screen are a small subset
FILTER_CAP = 2000           # largest search-result list shown when nothing is selected
QUEUE_HEAD = 80             # visible tiles must be within this many pending models, else the renderer restarts
QUEUE_LIMIT = 1500          # models handed to the renderer in one run
RESTART_DELAY_MS = 450      # debounce for scroll and selection changes
RESTART_MIN_SECONDS = 1.5   # never respawn faster than this


# Layout maths ----------------------------------------------------------------
def column_count(width, cell_width):
    return max(1, int(width) // max(1, int(cell_width)))


def row_count(total, columns):
    return math.ceil(total / columns) if total > 0 else 0


def visible_rows(offset, view_height, row_height, rows, overscan=1):
    """Row range [first, last) that intersects the viewport (plus ``overscan`` rows each side)."""
    if rows <= 0 or row_height <= 0:
        return 0, 0
    first = max(0, int(offset // row_height) - overscan)
    last = min(rows, math.ceil((offset + max(0, view_height)) / row_height) + overscan)
    return first, max(first, last)


def visible_tiles(offset, view_height, row_height, columns, total, overscan=1):
    """Tile index range [first, last) for a scroll offset."""
    first_row, last_row = visible_rows(offset, view_height, row_height, row_count(total, columns), overscan)
    return first_row * columns, min(total, last_row * columns)


def tile_at(x, y, offset, cell_width, cell_height, columns, total):
    """Index of the tile under canvas-window point (x, y), or None for gaps and the empty tail."""
    if x < 0 or y < 0:
        return None
    column, row = int(x // cell_width), int((y + offset) // cell_height)
    if column >= columns:
        return None
    index = row * columns + column
    return index if index < total else None


def scroll_to_show(index, offset, view_height, cell_height, columns):
    """New scroll offset that brings tile ``index`` fully into view (unchanged when it already is)."""
    top = (index // columns) * cell_height
    if top < offset:
        return top
    if top + cell_height > offset + view_height:
        return top + cell_height - view_height
    return offset


class ImageCache:
    """Bounded LRU of decoded images, so thousands of tiles never hold hundreds of megabytes."""

    def __init__(self, capacity=IMAGE_CACHE_SIZE):
        self.capacity = max(1, capacity)
        self.items = OrderedDict()

    def get(self, key):
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key]
        return None

    def put(self, key, value):
        self.items[key] = value
        self.items.move_to_end(key)
        while len(self.items) > self.capacity:
            self.items.popitem(last=False)

    def discard(self, key):
        self.items.pop(key, None)

    def clear(self):
        self.items.clear()

    def __len__(self):
        return len(self.items)

    def __contains__(self, key):
        return key in self.items


def elide(text, measure, width):
    """Shorten ``text`` with an ellipsis until ``measure(text)`` fits ``width``."""
    if measure(text) <= width:
        return text
    low, high = 0, len(text)
    while low < high:   # longest prefix that fits with the ellipsis
        middle = (low + high + 1) // 2
        if measure(text[:middle] + '…') <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + '…' if low else '…'


def display_name(record):
    """First named actor that uses the model, else the file stem."""
    actors = [name.strip() for name in (getattr(record, 'used_by', '') or '').split(',') if name.strip()]
    return actors[0] if actors else Path(record.archive_path).stem


def choose_records(selected, filtered, shown_ids, cap=FILTER_CAP):
    """Models the gallery shows: (records, kind, total).

    Two or more selected models (a folder, or several rows): exactly those. One selected model that
    the gallery already shows: keep the list, so clicking a tile does not replace it. Otherwise the
    current search results, capped at ``cap``. Only models with an archive id are shown."""
    selected = [r for r in selected if getattr(r, 'model_id', None)]
    if len(selected) >= 2:
        return selected, 'selection', len(selected)
    if len(selected) == 1 and selected[0].model_id in shown_ids:
        return None, 'keep', 0
    filtered = [r for r in filtered if getattr(r, 'model_id', None)]
    return filtered[:cap], 'filter', len(filtered)


def render_queue(records, is_ready, first_visible, last_visible, limit=QUEUE_LIMIT):
    """Ids to render: missing visible tiles, then the missing ones after them, then those before."""
    order = (list(range(first_visible, last_visible)) + list(range(last_visible, len(records)))
             + list(range(0, first_visible)))
    queued = []
    for position in order:
        model_id = records[position].model_id
        if not is_ready(model_id):
            queued.append(model_id)
            if len(queued) >= limit:
                break
    return queued


def needs_restart(visible_missing, pending, head=QUEUE_HEAD):
    """True when a missing visible tile is not within the first ``head`` pending ids of the running batch."""
    if not visible_missing:
        return False
    return not set(visible_missing) <= set(pending[:head])


def decode_thumbnail(path):
    """Decode one thumbnail PNG to an RGBA Pillow image (worker thread; no Tk objects). None without Pillow."""
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(path) as image:
        return image.convert('RGBA')


def photo_from_image(image, master):
    """A Tk PhotoImage from a decoded image (UI thread). None when Pillow's Tk bridge is unavailable."""
    try:
        from PIL import ImageTk
        return ImageTk.PhotoImage(image, master=master)
    except Exception:
        return None


# Tk view ---------------------------------------------------------------------
class Gallery:
    """The thumbnail grid: the Grid view of the model list (AssetBrowserApp.show_list_view). All Tk work happens on the UI thread; the renderer is a child process."""

    THUMB = DEFAULT_SIZE

    def __init__(self, browser, parent):
        """``parent``: the frame the app places ``self.frame`` in (the cell shared with the model tree)."""
        import tkinter.font as tkfont
        self.browser, self.app, self.tk, self.ttk = browser, browser.app, browser.tk, browser.ttk
        tk, ttk = self.tk, self.ttk
        self.font = tkfont.nametofont('TkDefaultFont', root=self.app.root)
        self.records, self.kind, self.total = [], 'filter', 0
        self.ids = set()
        self.index = ThumbnailIndex(Path('.'))
        self.cache_root = None
        self.images = ImageCache()
        self.index_work = LatestWork()     # index.json is parsed off the UI thread
        self.index_loading = False
        self.decoder = ThreadPoolExecutor(max_workers=2, thread_name_prefix='thumbnail-decode')
        self.decoded = queue.Queue()       # (model_id, token, image or None) from decoder threads
        self.inflight = {}                 # model_id -> token of the decode whose result is still wanted
        self.unreadable = set()
        self.decode_job = None
        self.token = 0
        self.live = {}                 # id -> (status, reason) learned from renderer events this session
        self.item_by_id = {}
        self.current_id = None
        self.proc = None
        self.proc_pending = []         # ids still queued in the running renderer, in order
        self.mode = 'view'             # 'view' (visible first) or 'all' (Render all thumbnails)
        self.blocked = ''              # why automatic rendering stopped (renderer crashed)
        self.last_start = 0.0
        self.stale = True
        self.refresh_job = self.queue_job = self.redraw_job = self.poll_job = self.tip_job = self.tip_window = None
        self.hover = None
        self.redraw_due = False
        self.closed = False
        self.columns, self.cell_w, self.cell_h, self.pad, self.margin = 1, 1, 1, 6, 0
        self._visible = (0, 0)
        self.total_done_logged = 0

        self.frame = ttk.Frame(parent)   # placed (and hidden) by the app
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        bar = ttk.Frame(self.frame)
        bar.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 4))
        bar.columnconfigure(0, weight=1)
        self.info_var = tk.StringVar(value='')
        ttk.Label(bar, textvariable=self.info_var, wraplength=520).grid(row=0, column=0, sticky='ew')
        self.render_all_button = ttk.Button(bar, text='Render all thumbnails', command=self.render_all)
        self.render_all_button.grid(row=0, column=1, padx=(6, 2))
        self.cancel_button = ttk.Button(bar, text='Cancel', command=self.cancel_render, state='disabled')
        self.cancel_button.grid(row=0, column=2)
        background = ttk.Style(self.app.root).lookup('TFrame', 'background') or '#1e1f22'
        self.background = background
        self.canvas = tk.Canvas(self.frame, background=background, highlightthickness=0, takefocus=True)
        self.canvas.grid(row=1, column=0, sticky='nsew')
        self.scrollbar = ttk.Scrollbar(self.frame, orient='vertical', command=self._scroll_command)
        self.scrollbar.grid(row=1, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.message = ttk.Label(self.frame, text='Thumbnails need an Output folder: they are cached there. Set Output above.',
                                 wraplength=380, justify='center')
        self.canvas.bind('<Configure>', lambda _e: self._relayout())
        self.canvas.bind('<ButtonPress-1>', self._click)
        self.canvas.bind('<Double-Button-1>', self._double_click)
        self.canvas.bind('<Motion>', self._motion)
        self.canvas.bind('<Leave>', lambda _e: self._hide_tip())
        for key, step in (('<Left>', -1), ('<Right>', 1)):
            self.canvas.bind(key, lambda _e, s=step: self._move(s))
        self.canvas.bind('<Up>', lambda _e: self._move(-self.columns))
        self.canvas.bind('<Down>', lambda _e: self._move(self.columns))
        self.canvas.bind('<Prior>', lambda _e: self._move(-self.columns * self._page_rows()))
        self.canvas.bind('<Next>', lambda _e: self._move(self.columns * self._page_rows()))
        self.canvas.bind('<Home>', lambda _e: self._move_to(0))
        self.canvas.bind('<End>', lambda _e: self._move_to(len(self.records) - 1))
        self.canvas.bind('<Return>', lambda _e: self._open_current())
        self.canvas.bind('<Enter>', lambda _e: self.canvas.focus_set())
        # Windows delivers the wheel to the focused widget, so listen application-wide.
        self.canvas.bind_all('<MouseWheel>', self._wheel, add='+')
        self.frame.bind('<Map>', lambda _e: self.invalidate())
        self._poll()

    # Contents --------------------------------------------------------------
    def visible(self):
        try:
            return bool(self.frame.winfo_ismapped())
        except Exception:
            return False

    def invalidate(self):
        """The tree selection or the search results changed; refresh once the tab is shown."""
        self.stale = True
        if self.closed or not self.visible() or self.refresh_job is not None:
            return
        self.refresh_job = self.app.root.after_idle(self.refresh)

    def refresh(self):
        self.refresh_job = None
        if self.closed or not self.stale or not self.visible():
            return
        self.stale = False
        app = self.app
        output = app.output_var.get().strip()
        if not output:
            self._show_message(True)
            self.records, self.ids = [], set()
            self.info_var.set('')
            self.stop_renderer()
            return
        self._show_message(False)
        root = Path(output)
        if root != self.cache_root:
            self.cache_root = root
            self._clear_images()
            self.live.clear()
            self.index = ThumbnailIndex(cache_dir(root))
            self.stop_renderer()
            self.blocked = ''
        self.item_by_id = {getattr(r, 'model_id', None): item for item, r in app.item_by_record.items()}
        selected = app._selected_records()
        records, kind, total = choose_records(selected, app.filtered_records, self.ids)
        if len(selected) == 1:
            self.current_id = getattr(selected[0], 'model_id', None)
        if kind != 'keep':
            self.records, self.kind, self.total, self.ids = records, kind, total, {r.model_id for r in records}
            self.canvas.yview_moveto(0)
        if kind != 'keep' or not self.index.models:
            self._load_index(cache_dir(root))
        self._describe()
        self._relayout()
        self.schedule_render()

    def _load_index(self, folder):
        """Parse index.json on a worker; the previous index keeps serving until the new one arrives."""
        self.index_loading = True
        self.index_work.submit(ThumbnailIndex.load, folder)
        self._poll_index()

    def _poll_index(self):
        if self.closed:
            return
        try:
            while True:
                generation, index, error = self.index_work.events.get_nowait()
                if generation != self.index_work.generation:
                    continue
                self.index_loading = False
                if error:
                    continue   # unreadable index: keep what is shown; missing models are simply rendered again
                self.index = index
                self.live.clear()
                self._clear_images()
                self.redraw()
                self.schedule_render()
        except queue.Empty:
            pass
        if self.index_loading:
            self.app.root.after(30, self._poll_index)

    def _clear_images(self):
        self.images.clear()
        self.unreadable.clear()
        self.inflight.clear()

    def _discard_image(self, model_id):
        self.images.discard(model_id)
        self.unreadable.discard(model_id)
        self.inflight.pop(model_id, None)

    def _describe(self):
        if not self.records:
            self.info_var.set('No models to show. Scan the archive, then select a folder or search.')
            return
        note = ''
        if self.kind == 'filter':
            note = (f'Search results (showing the first {len(self.records):,} of {self.total:,}; narrow the search or select a folder)'
                    if self.total > len(self.records) else 'Search results')
        else:
            note = 'Selected models'
        if self.blocked:
            note += f' · {self.blocked}'
        self.info_var.set(f'{note}: {len(self.records):,} models')

    def _show_message(self, show):
        if show:
            self.canvas.grid_remove()
            self.scrollbar.grid_remove()
            self.message.grid(row=1, column=0, columnspan=2, sticky='nsew')
            self.render_all_button.configure(state='disabled')
        else:
            self.message.grid_remove()
            self.canvas.grid()
            self.scrollbar.grid()
            self.render_all_button.configure(state='normal')

    # Layout and drawing ----------------------------------------------------
    def _relayout(self):
        linespace = self.font.metrics('linespace')
        self.pad = max(6, linespace // 2)
        self.cell_w = self.THUMB + 2 * self.pad
        self.cell_h = self.THUMB + linespace + 3 * self.pad
        width = max(self.canvas.winfo_width(), 1)
        self.columns = column_count(width, self.cell_w)
        rows = row_count(len(self.records), self.columns)
        # Centre the grid: the leftover width is split into the left margin.
        self.margin = max(0, (width - self.columns * self.cell_w) // 2)
        self.canvas.configure(scrollregion=(0, 0, width, max(rows * self.cell_h, 1)), yscrollincrement=max(1, self.cell_h // 3))
        self.redraw()

    def _page_rows(self):
        return max(1, self.canvas.winfo_height() // max(1, self.cell_h) - 0)

    def _offset(self):
        return int(self.canvas.canvasy(0))

    def _scroll_command(self, *args):
        self.canvas.yview(*args)
        self.redraw()
        self.schedule_render()

    def _wheel(self, event):
        if not self.visible():
            return
        try:
            under = self.canvas.winfo_containing(event.x_root, event.y_root)
        except Exception:
            under = None
        if under is not self.canvas:
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        self.redraw()
        self.schedule_render()
        return 'break'

    def status_of(self, model_id):
        """('ok'|'unsupported'|'error'|'queued', reason) for one model, from this session's events then the index."""
        live = self.live.get(model_id)
        if live:
            return live
        if self.index_loading and not self.index.models:
            return 'loading', ''
        if self.index.is_current(model_id, self.THUMB):
            entry = self.index.get(model_id)
            return entry['status'], entry.get('reason', '')
        return 'queued', ''

    def _photo(self, model_id):
        photo = self.images.get(model_id)
        if photo is None and model_id not in self.unreadable and model_id not in self.inflight:
            path = str(self.index.png_path(model_id))
            self.token += 1
            self.inflight[model_id] = token = self.token
            self.decoder.submit(self._decode, model_id, token, path)
            self._poll_decoded()
        return photo

    def _decode(self, model_id, token, path):
        try:
            image = decode_thumbnail(path)
        except Exception:
            image = None
        self.decoded.put((model_id, token, image, path))

    def _poll_decoded(self):
        """Wrap decoded images into PhotoImages here, on the UI thread (Tk objects are not thread safe)."""
        if self.decode_job is not None:
            self.app.root.after_cancel(self.decode_job)
            self.decode_job = None
        if self.closed:
            return
        changed = False
        try:
            while True:
                model_id, token, image, path = self.decoded.get_nowait()
                if self.inflight.get(model_id) != token:
                    continue   # superseded: the thumbnail was re-rendered or the cache changed
                del self.inflight[model_id]
                photo = photo_from_image(image, self.canvas) if image is not None else None
                if photo is None:   # no Pillow / ImageTk: let Tk decode the PNG itself
                    try:
                        photo = self.tk.PhotoImage(file=path, master=self.canvas)
                    except Exception:
                        photo = None
                if photo is None:
                    self.unreadable.add(model_id)
                else:
                    self.images.put(model_id, photo)
                changed = True
        except queue.Empty:
            pass
        if changed:
            self._schedule_redraw()
        if self.inflight:
            self.decode_job = self.app.root.after(30, self._poll_decoded)

    def redraw(self):
        self.redraw_job = None
        canvas = self.canvas
        canvas.delete('all')
        total = len(self.records)
        if not total:
            return
        height = max(canvas.winfo_height(), 1)
        offset = self._offset()
        first, last = visible_tiles(offset, height, self.cell_h, self.columns, total)
        self._visible = (first, last)
        dark = self.app.dark
        text_colour = '#e6e6e6' if dark else '#1f2937'
        muted = '#9aa0a6' if dark else '#6b7280'
        placeholder = '#2b2d31' if dark else '#e5e7eb'
        accent = '#3d6fb6'
        width = self.cell_w - 2 * self.pad
        for number in range(first, last):
            record = self.records[number]
            x = self.margin + (number % self.columns) * self.cell_w
            y = (number // self.columns) * self.cell_h
            selected = record.model_id == self.current_id
            if selected:
                canvas.create_rectangle(x + 2, y + 2, x + self.cell_w - 2, y + self.cell_h - 2, fill=accent, outline='')
            status, _reason = self.status_of(record.model_id)
            photo = self._photo(record.model_id) if status == 'ok' else None
            left, top = x + self.pad, y + self.pad
            if photo is not None:
                canvas.create_image(left, top, anchor='nw', image=photo)
            else:
                canvas.create_rectangle(left, top, left + self.THUMB, top + self.THUMB, fill=placeholder, outline='')
                label = {'queued': 'queued…', 'loading': 'loading…', 'unsupported': 'unsupported', 'error': 'error'}.get(status, status)
                if status == 'ok':
                    label = 'unreadable' if record.model_id in self.unreadable else 'loading…'
                canvas.create_text(left + self.THUMB // 2, top + self.THUMB // 2, text=label, fill=muted, font=self.font)
            name = elide(display_name(record), self.font.measure, width)
            canvas.create_text(left + self.THUMB // 2, top + self.THUMB + self.pad, anchor='n', text=name,
                               fill='#ffffff' if selected else text_colour, font=self.font)

    def _schedule_redraw(self):
        if self.redraw_job is None and not self.closed:
            self.redraw_job = self.app.root.after(120, self.redraw)

    # Interaction -----------------------------------------------------------
    def _event_index(self, event):
        if not self.records:
            return None
        return tile_at(event.x - self.margin, event.y, self._offset(), self.cell_w, self.cell_h, self.columns, len(self.records))

    def _click(self, event):
        self.canvas.focus_set()
        self._hide_tip()
        index = self._event_index(event)
        if index is not None:
            self.select(index)

    def _double_click(self, event):
        index = self._event_index(event)
        if index is not None:
            self.select(index)
            self._open_current()

    def select(self, index, show=False):
        """Select tile ``index`` in the model tree (which loads it in the 3D view)."""
        if not 0 <= index < len(self.records):
            return
        record = self.records[index]
        self.current_id = record.model_id
        item = self.item_by_id.get(record.model_id)
        table = self.app.table
        if item is not None and table.exists(item):
            table.selection_set(item)
            table.focus(item)
            table.see(item)
        if show:
            offset = scroll_to_show(index, self._offset(), self.canvas.winfo_height(), self.cell_h, self.columns)
            rows = max(1, row_count(len(self.records), self.columns) * self.cell_h)
            self.canvas.yview_moveto(offset / rows)
        self.redraw()

    def _current_index(self):
        for number, record in enumerate(self.records):
            if record.model_id == self.current_id:
                return number
        return None

    def _move(self, step):
        if not self.records:
            return 'break'
        current = self._current_index()
        target = 0 if current is None else max(0, min(len(self.records) - 1, current + step))
        self.select(target, show=True)
        self.schedule_render()
        return 'break'

    def _move_to(self, target):
        if self.records:
            self.select(max(0, min(len(self.records) - 1, target)), show=True)
            self.schedule_render()
        return 'break'

    def _open_current(self):
        index = self._current_index()
        if index is None:
            return 'break'
        self.select(index)
        notebook = self.browser.notebook
        notebook.select(notebook.tabs()[0])   # the "3D model" tab
        return 'break'

    # Hover tooltip ---------------------------------------------------------
    def _motion(self, event):
        index = self._event_index(event)
        if index == self.hover:
            return
        self._hide_tip()
        self.hover = index
        if index is not None:
            self.tip_job = self.canvas.after(500, lambda: self._show_tip(index, event.x_root, event.y_root))

    def _show_tip(self, index, x, y):
        self.tip_job = None
        if index >= len(self.records):
            return
        record = self.records[index]
        status, reason = self.status_of(record.model_id)
        lines = [display_name(record), record.archive_path, f'ID {record.model_id}']
        if status in ('unsupported', 'error'):
            lines.append(f'Thumbnail {status}: {reason}')
        elif status == 'queued':
            lines.append('Thumbnail not rendered yet')
        window = self.tip_window = self.tk.Toplevel(self.canvas)
        window.wm_overrideredirect(True)
        self.tk.Label(window, text='\n'.join(lines), justify='left', wraplength=520, background='#fffbe6',
                      foreground='#1f2937', relief='solid', borderwidth=1, padx=8, pady=6).pack()
        window.wm_geometry(f'+{x + 14}+{y + 18}')

    def _hide_tip(self):
        self.hover = None
        if self.tip_job is not None:
            self.canvas.after_cancel(self.tip_job)
            self.tip_job = None
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None

    # Rendering -------------------------------------------------------------
    def _source(self):
        return self.app.source_var.get().strip()

    def _is_ready(self, model_id):
        return self.status_of(model_id)[0] != 'queued'

    def schedule_render(self):
        if self.queue_job is None and not self.closed:
            self.queue_job = self.app.root.after(RESTART_DELAY_MS, self.ensure_rendering)

    def ensure_rendering(self):
        """Start the renderer for missing thumbnails, or re-prioritise it when the view moved."""
        import time
        self.queue_job = None
        if self.closed or not self.records or not self.visible() or self.cache_root is None or not self._source():
            return
        if self.index_loading:
            return   # the loaded index decides what is missing; it schedules rendering itself
        if self.blocked and self.mode != 'all':
            return
        first, last = self._visible
        wanted = render_queue(self.records, self._is_ready, first, last)
        if not wanted:
            return
        running = self.proc is not None and self.proc.running()
        if running:
            if self.mode == 'all':
                return
            visible_missing = [r.model_id for r in self.records[first:last] if not self._is_ready(r.model_id)]
            if not needs_restart(visible_missing, self.proc_pending):
                return
            if time.monotonic() - self.last_start < RESTART_MIN_SECONDS:
                self.schedule_render()
                return
            self.stop_renderer()
        self._start(wanted, all_models=False)

    def _start(self, ids, all_models):
        import time
        from rc2016.app.settings import PROJECT_ROOT, _default_interpreter
        try:
            self.proc = RendererProcess(self._source(), self.cache_root, ids=ids, all_models=all_models, size=self.THUMB,
                                        cwd=PROJECT_ROOT, python=_default_interpreter()).start()
        except OSError as error:
            self.proc = None
            self.blocked = f'thumbnail renderer could not start ({error})'
            self._describe()
            return
        self.proc_pending = list(ids)
        self.last_start = time.monotonic()
        self.mode = 'all' if all_models else 'view'
        self.cancel_button.configure(state='normal' if all_models else 'disabled')
        self.total_done_logged = 0
        self._poll()

    def render_all(self):
        if not self.cache_root or not self._source():
            return
        self.stop_renderer()
        self.blocked = ''
        first, last = self._visible
        ordered = render_queue(self.records, self._is_ready, first, last)
        self.app._append_log('Rendering thumbnails for every archive model…', 'muted')
        self._start(ordered, all_models=True)

    def cancel_render(self):
        if self.proc is not None:
            self.app._append_log('Thumbnail rendering cancelled.', 'muted')
        self.stop_renderer()
        self.info_var.set('Thumbnail rendering cancelled.')

    def stop_renderer(self):
        proc, self.proc, self.proc_pending = self.proc, None, []
        if proc is not None:
            proc.stop()
        self.mode = 'view'
        self.cancel_button.configure(state='disabled')

    def _poll(self):
        """Drain renderer events (JSON lines through a queue), refreshing only the affected tiles."""
        if self.poll_job is not None:
            self.app.root.after_cancel(self.poll_job)
            self.poll_job = None
        if self.closed:
            return
        proc = self.proc
        if proc is not None:
            changed = False
            try:
                while True:
                    event = proc.events.get_nowait()
                    changed |= self._handle(event, proc)
            except queue.Empty:
                pass
            if changed:
                self._schedule_redraw()
        if self.proc is not None:
            self.poll_job = self.app.root.after(120, self._poll)

    def _handle(self, event, proc):
        kind = event.get('event')
        if kind == 'model':
            model_id = event.get('id')
            if model_id in self.proc_pending:
                self.proc_pending.remove(model_id)
            if event.get('skipped'):
                self.live.pop(model_id, None)
            else:
                self.live[model_id] = (event.get('status', 'error'), event.get('reason', ''))
            self._discard_image(model_id)
            if self.mode == 'all':
                done = event.get('n', 0)
                total = event.get('total', 0)
                self.info_var.set(f'Rendering thumbnails: {done:,} of {total:,}')
                if done - self.total_done_logged >= 250:
                    self.total_done_logged = done
                    self.app._append_log(f'Thumbnails: {done:,} of {total:,}', 'muted')
            return model_id in self.ids
        if kind == 'start':
            if not event.get('transparent') and event.get('fbo') is False:
                self.app._append_log('Thumbnails: offscreen framebuffer unavailable, using the window back buffer '
                                     f"({event.get('fbo_error') or 'forced'}); backgrounds are opaque.", 'muted')
        elif kind == 'fatal':
            self.blocked = f"renderer stopped: {event.get('reason')}"
            self.app._append_log(f"Thumbnail renderer: {event.get('reason')}", 'error')
            self._describe()
        elif kind == 'done':
            if self.mode == 'all':
                self.app._append_log(f"Thumbnails finished: {event.get('rendered', 0):,} models.", 'success')
        elif kind == 'exit':
            code = event.get('code')
            finished_all = self.mode == 'all'
            self.proc, self.proc_pending, self.mode = None, [], 'view'
            self.cancel_button.configure(state='disabled')
            if self.cache_root:
                self._load_index(cache_dir(self.cache_root))   # live results keep serving until it arrives
            else:
                self.live.clear()
                self._clear_images()
            if code not in (0, None) and not self.blocked:
                self.blocked = f'renderer exited with code {code}; see .cache/thumbnails/renderer.log'
                self.app._append_log(f'Thumbnail renderer exited with code {code}; see .cache/thumbnails/renderer.log in the output folder.', 'error')
            self._describe()
            if finished_all:
                self.info_var.set('Thumbnails finished.')
            self.schedule_render()
            return True
        return False

    def close(self):
        self.closed = True
        for job in (self.refresh_job, self.queue_job, self.redraw_job, self.poll_job):
            if job is not None:
                try:
                    self.app.root.after_cancel(job)
                except Exception:
                    pass
        self._hide_tip()
        self.stop_renderer()
        self.index_work.close()
        self.decoder.shutdown(wait=False, cancel_futures=True)
