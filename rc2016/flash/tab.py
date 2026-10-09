"""'Flash UI' tab: browse the game's Scaleform .gfx menus and render their vector art.

Rendering is gfx_render's basic rasterizer (shapes, sprites, frames, masks,
gradients; no text, filters or ActionScript). Work runs in workers; this
class only touches Tk on the main thread via drain().
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
import queue
import threading

from rc2016.app.workers import LatestWork

# Where the pause-menu planet maps live; offered first when it exists.
MAP_FILE = 'ui/export/pause/map.gfx'
MAP_SYMBOL = 'Map_fla.map_image_asset_holder_6'


class FlashTab:
    def __init__(self, app, tab, loader, list_parent=None):
        """``list_parent``: a separate frame for the UI file list (the asset browser's left pane);
        by default the list sits at the top of ``tab``."""
        self.app, self.tk, self.ttk, self.loader = app, app.tk, app.ttk, loader
        tk, ttk = self.tk, self.ttk
        self.list_work, self.symbol_work, self.render_work = LatestWork(), LatestWork(), LatestWork()
        self.events = queue.Queue()
        self.files, self.symbols, self.rows = [], [], []
        self.current, self.image, self.photo, self.exporting = None, None, None, False
        self.files_root = None
        self.file_var = tk.StringVar()
        tab.columnconfigure(0, weight=1); tab.rowconfigure(2, weight=1)
        if list_parent is None:
            list_parent = ttk.Frame(tab); list_parent.grid(row=0, column=0, sticky='ew')
            self._build_file_list(list_parent, height=5)
        else:
            self._build_file_list(list_parent, height=20)
        filt = ttk.Frame(tab); filt.grid(row=1, column=0, sticky='ew', pady=(4, 0))
        filt.columnconfigure(1, weight=1)
        ttk.Label(filt, text='Filter').grid(row=0, column=0)
        self.filter_var = tk.StringVar()
        ttk.Entry(filt, textvariable=self.filter_var).grid(row=0, column=1, sticky='ew', padx=4)
        self.filter_var.trace_add('write', lambda *_a: self._fill_symbols())
        self.named_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(filt, text='Named only', variable=self.named_only, command=self._fill_symbols).grid(row=0, column=2)
        body = ttk.Panedwindow(tab, orient='vertical'); body.grid(row=2, column=0, sticky='nsew', pady=4)
        listing = ttk.Frame(body); listing.columnconfigure(0, weight=1); listing.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(listing, columns=('kind', 'frames'), height=6, selectmode='browse')
        for column, text, width in [('#0', 'Symbol', 260), ('kind', 'Kind', 70), ('frames', 'Frames', 60)]:
            self.tree.heading(column, text=text); self.tree.column(column, width=width, stretch=column == '#0')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview); scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self._symbol_selected())
        self.canvas = tk.Canvas(body, width=420, height=260, background='#2b2b2b', highlightthickness=0)
        self.canvas.bind('<Configure>', lambda _e: self._draw())
        body.add(listing, weight=1); body.add(self.canvas, weight=3)
        frame_row = ttk.Frame(tab); frame_row.grid(row=3, column=0, sticky='ew')
        ttk.Label(frame_row, text='Label').pack(side='left')
        self.label_var = tk.StringVar()
        self.label_box = ttk.Combobox(frame_row, textvariable=self.label_var, state='disabled', width=24)
        self.label_box.pack(side='left', padx=4)
        self.label_box.bind('<<ComboboxSelected>>', lambda _e: self._label_chosen())
        ttk.Label(frame_row, text='Frame').pack(side='left', padx=(8, 0))
        self.frame_var = tk.StringVar(value='1')
        self.frame_box = ttk.Spinbox(frame_row, textvariable=self.frame_var, from_=1, to=1, width=5,
                                     state='disabled', command=self._render_current)
        self.frame_box.pack(side='left', padx=4)
        self.frame_box.bind('<Return>', lambda _e: self._render_current())
        ttk.Label(frame_row, text='Background').pack(side='left', padx=(8, 0))
        self.background_var = tk.StringVar(value='Checker')
        background = ttk.Combobox(frame_row, textvariable=self.background_var, values=('Checker', 'Dark', 'Light'),
                                  state='readonly', width=8)
        background.pack(side='left', padx=4)
        background.bind('<<ComboboxSelected>>', lambda _e: self._draw())
        export = ttk.Frame(tab); export.grid(row=4, column=0, sticky='ew', pady=(4, 0))
        ttk.Label(export, text='PNG size').pack(side='left')
        self.size_var = tk.StringVar(value='2048')
        ttk.Spinbox(export, textvariable=self.size_var, values=(512, 1024, 2048, 4096, 8192), width=6).pack(side='left', padx=4)
        self.source_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(export, text='+ original .gfx', variable=self.source_var).pack(side='left', padx=4)
        self.export_frame_button = ttk.Button(export, text='Export frame…', state='disabled', command=lambda: self._export(False))
        self.export_frame_button.pack(side='left', padx=(6, 2))
        self.export_all_button = ttk.Button(export, text='Export all labels…', state='disabled', command=lambda: self._export(True))
        self.export_all_button.pack(side='left', padx=2)
        maps = ttk.Frame(tab); maps.grid(row=7, column=0, sticky='ew', pady=(2, 0))
        self.maps_button = ttk.Button(maps, text='Export planet maps (game colours)…', command=self._export_maps)
        self.maps_button.pack(side='left')
        ttk.Label(maps, text='All 15 maps: areas #AFDAFF, secrets #AFFFE4, transparent.',
                  foreground='#6b7280').pack(side='left', padx=6)
        self.info = tk.StringVar(value='Load the list of the game\'s Flash UI files. The pause-menu planet maps are in '
                                      f'{MAP_FILE}, symbol {MAP_SYMBOL} (one labelled frame per planet).')
        ttk.Label(tab, textvariable=self.info, wraplength=410).grid(row=5, column=0, sticky='ew', pady=(4, 0))
        ttk.Label(tab, text='Basic vector renderer: shapes, sprites, frames, masks and gradients. Text, filters and '
                            'ActionScript-driven colours are not drawn, so menus appear in their authored base colours.',
                  foreground='#6b7280', wraplength=410).grid(row=6, column=0, sticky='w', pady=4)

    def _build_file_list(self, parent, height):
        ttk = self.ttk
        parent.columnconfigure(0, weight=1); parent.rowconfigure(1, weight=1)
        head = ttk.Frame(parent); head.grid(row=0, column=0, sticky='ew')
        ttk.Label(head, text='UI files (.gfx / .swf)').pack(side='left')
        ttk.Button(head, text='Load list', command=self.load_files).pack(side='right')
        box = ttk.Frame(parent); box.grid(row=1, column=0, sticky='nsew', pady=(4, 0))
        box.columnconfigure(0, weight=1); box.rowconfigure(0, weight=1)
        self.file_list = ttk.Treeview(box, show='tree', selectmode='browse', height=height)
        self.file_list.column('#0', width=260, stretch=True)
        self.file_list.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.file_list.yview); scroll.grid(row=0, column=1, sticky='ns')
        self.file_list.configure(yscrollcommand=scroll.set)
        self.file_list.bind('<<TreeviewSelect>>', lambda _e: self._file_chosen())

    def _file_chosen(self):
        selection = self.file_list.selection()
        if selection and selection[0] != self.file_var.get():
            self.file_var.set(selection[0]); self._open_file()

    def _fill_file_list(self):
        tree = self.file_list
        tree.delete(*tree.get_children())
        for path in self.files:
            tree.insert('', 'end', iid=path, text=path)

    def _mark_file(self, path):
        """Highlight the open file; the select handler skips it, so it is not parsed twice."""
        if path and self.file_list.exists(path):
            if tuple(self.file_list.selection()) != (path,):
                self.file_list.selection_set(path)
            self.file_list.see(path)

    # Files and symbols -------------------------------------------------------
    def load_files(self):
        root = self.app.source_var.get().strip()
        if not root:
            self.info.set('Choose the extracted source archive first.'); return
        self.info.set('Reading the archive index…')
        self.list_work.submit(lambda r=root: (r, self.loader.ui_files(r)))

    def _open_file(self):
        path = self.file_var.get()
        if not path:
            return
        self.info.set('Parsing ' + path + ' …')
        self.symbols, self.rows, self.current, self.image = [], [], None, None
        self.tree.delete(*self.tree.get_children()); self._draw()
        self.symbol_work.submit(self.loader.gfx_symbols, path)

    def _fill_symbols(self):
        words = self.filter_var.get().lower().split()
        named = self.named_only.get()
        self.rows = [s for s in self.symbols
                     if (not named or s['name'] or s['id'] == 0)
                     and all(w in f"{s['name']} {s['id']}".lower() for w in words)]
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.rows):
            title = f"{s['name'] or '(unnamed)'}  #{s['id']}" if s['id'] else s['name']
            self.tree.insert('', 'end', iid=str(i), text=title, values=(s['kind'], s['frames']))

    def _symbol_selected(self):
        selection = self.tree.selection()
        if not selection:
            return
        symbol = self.rows[int(selection[0])]
        self.current = symbol
        labels = sorted(symbol['labels'].items(), key=lambda kv: kv[1])
        self.label_box.configure(values=[name for name, _f in labels], state='readonly' if labels else 'disabled')
        self.label_var.set(labels[0][0] if labels else '')
        self.frame_box.configure(to=max(1, symbol['frames']), state='normal' if symbol['frames'] > 1 else 'disabled')
        self.frame_var.set(str(labels[0][1] + 1) if labels else '1')
        busy = self.exporting
        self.export_frame_button.configure(state='disabled' if busy else 'normal')
        self.export_all_button.configure(state='normal' if labels and not busy else 'disabled')
        self._render_current()

    def _label_chosen(self):
        if self.current and self.label_var.get() in self.current['labels']:
            self.frame_var.set(str(self.current['labels'][self.label_var.get()] + 1))
            self._render_current()

    def _frame(self):
        try:
            value = int(self.frame_var.get()) - 1
        except ValueError:
            value = 0
        return min(max(0, value), max(0, (self.current or {}).get('frames', 1) - 1))

    def _render_current(self):
        if not self.current:
            return
        frame = self._frame()
        label = next((n for n, f in self.current['labels'].items() if f == frame), '')
        if label != self.label_var.get() and label:
            self.label_var.set(label)
        size = max(256, max(self.canvas.winfo_width(), self.canvas.winfo_height()))
        self.info.set(f"Rendering {self.current['name'] or '#' + str(self.current['id'])} frame {frame + 1} …")
        self.render_work.submit(lambda p=self.file_var.get(), c=self.current, f=frame, s=size:
                                (c, f, self.loader.gfx_image(p, c['id'], f, s)))

    # Drawing ------------------------------------------------------------------
    def _draw(self):
        self.canvas.delete('all')
        if self.image is None:
            self.photo = None; return
        from PIL import Image
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        image = self.image
        scale = min(width / image.width, height / image.height)
        size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS).convert('RGBA')
        # Preview only; exports keep their transparency.
        choice = self.background_var.get()
        if choice == 'Checker':
            cell = 8
            board = Image.new('RGBA', (2, 2), (102, 102, 102, 255))
            board.putpixel((0, 0), (153, 153, 153, 255)); board.putpixel((1, 1), (153, 153, 153, 255))
            backdrop = board.resize((2 * cell, 2 * cell), Image.Resampling.NEAREST)
            tiled = Image.new('RGBA', image.size)
            for y in range(0, image.height, 2 * cell):
                for x in range(0, image.width, 2 * cell):
                    tiled.paste(backdrop, (x, y))
            backdrop = tiled
        else:
            backdrop = Image.new('RGBA', image.size, (43, 43, 43, 255) if choice == 'Dark' else (235, 235, 235, 255))
        backdrop.alpha_composite(image); image = backdrop
        buffer = io.BytesIO(); image.save(buffer, format='PNG')
        self.photo = self.tk.PhotoImage(data=base64.b64encode(buffer.getvalue()), format='png')
        self.canvas.create_image(width // 2, height // 2, image=self.photo, anchor='center')

    # Export -------------------------------------------------------------------
    def _export(self, all_labels):
        if not self.current:
            return
        try:
            size = max(64, min(16384, int(self.size_var.get())))
        except ValueError:
            self.info.set('PNG size must be a number.'); return
        if all_labels:
            frames = [(f, n) for n, f in sorted(self.current['labels'].items(), key=lambda kv: kv[1])]
        else:
            frame = self._frame()
            frames = [(frame, next((n for n, f in self.current['labels'].items() if f == frame), ''))]
        folder = self.app.filedialog.askdirectory(title='Choose a folder for the exported UI art')
        if not folder:
            return
        name = self.current['name'] or f"symbol_{self.current['id']}"
        if len(frames) == 1 and frames[0][1]:
            name += '_' + frames[0][1]
            frames = [(frames[0][0], '')]
        self.exporting = True
        self.export_frame_button.configure(state='disabled'); self.export_all_button.configure(state='disabled')
        path, char_id, include = self.file_var.get(), self.current['id'], self.source_var.get()

        def progress(done, total, file_name):
            self.events.put(('progress', f'Exported {done}/{total}: {file_name}'))

        def work():
            try:
                files = self.loader.gfx_export(path, folder, char_id, frames, size, name, include, progress)
                self.events.put(('done', f'Wrote {len(files)} files to {folder}.'))
            except Exception as error:
                self.events.put(('error', f'Export failed: {error}'))
        threading.Thread(target=work, daemon=True).start()
        self.info.set(f'Exporting {len(frames)} image(s) to {folder} …')

    def _export_maps(self):
        if not self.app.source_var.get().strip():
            self.info.set('Choose the extracted source archive first.'); return
        try:
            size = max(256, min(16384, int(self.size_var.get())))
        except ValueError:
            self.info.set('PNG size must be a number.'); return
        folder = self.app.filedialog.askdirectory(title='Choose a folder for the planet maps')
        if not folder:
            return
        self.maps_button.configure(state='disabled')
        root = self.app.source_var.get().strip()

        def work():
            try:
                from rc2016.flash import planet_maps
                self.loader.ui_files(root)   # opens the archive if the list was never loaded
                rows = planet_maps.export(self.loader, folder, size,
                                         lambda n, t, name: self.events.put(('progress', f'Planet maps {n}/{t}: {name}')))
                secrets = sum(len(r['secret']) for r in rows)
                self.events.put(('maps', f'Wrote {len(rows)} planet maps ({secrets} secret areas in green) to {folder}.'))
            except Exception as error:
                self.events.put(('maps_error', f'Planet map export failed: {error}'))
        threading.Thread(target=work, daemon=True).start()
        self.info.set('Exporting planet maps …')

    # Main-thread event pump ---------------------------------------------------------
    def drain(self):
        for worker, kind in ((self.list_work, 'files'), (self.symbol_work, 'symbols'), (self.render_work, 'image')):
            try:
                while True:
                    generation, payload, error = worker.events.get_nowait()
                    if generation != worker.generation:
                        continue
                    if error:
                        self.info.set(error); continue
                    if payload is None:
                        continue
                    if kind == 'files':
                        self.files_root, self.files = payload
                        self._fill_file_list()
                        if MAP_FILE in self.files and not self.file_var.get():
                            self.file_var.set(MAP_FILE); self._open_file()
                            self._mark_file(MAP_FILE)
                        else:
                            self._mark_file(self.file_var.get())
                            self.info.set(f'{len(self.files)} Flash UI files. Choose one.')
                    elif kind == 'symbols':
                        if payload['path'] != self.file_var.get():
                            continue
                        self.symbols = payload['symbols']; self._fill_symbols()
                        extra = f" Imports: {', '.join(payload['imports'])}." if payload['imports'] else ''
                        errors = f" {len(payload['errors'])} tags could not be parsed." if payload['errors'] else ''
                        self.info.set(f"{payload['path']}: {payload['signature']} v{payload['version']}, "
                                      f"{len(self.symbols)} symbols ({sum(1 for s in self.symbols if s['name'])} named).{extra}{errors}")
                        target = next((i for i, s in enumerate(self.rows) if s['name'] == MAP_SYMBOL), None)
                        if target is not None:
                            self.tree.selection_set(str(target)); self.tree.see(str(target))
                    elif kind == 'image':
                        symbol, frame, image = payload
                        self.image = image; self._draw()
                        label = next((n for n, f in symbol['labels'].items() if f == frame), '')
                        self.info.set(f"{symbol['name'] or '#' + str(symbol['id'])} · frame {frame + 1} of {symbol['frames']}"
                                      + (f' · {label}' if label else ''))
            except queue.Empty:
                pass
        try:
            while True:
                kind, message = self.events.get_nowait()
                self.info.set(message)
                if kind in ('maps', 'maps_error'):
                    self.maps_button.configure(state='normal')
                    if kind == 'maps_error':
                        self.app._append_log(message, 'error')
                    continue
                if kind != 'progress':
                    self.exporting = False
                    if self.current:
                        self.export_frame_button.configure(state='normal')
                        self.export_all_button.configure(state='normal' if self.current['labels'] else 'disabled')
                    if kind == 'error':
                        self.app._append_log(message, 'error')
        except queue.Empty:
            pass

    def close(self):
        self.list_work.close(); self.symbol_work.close(); self.render_work.close()
