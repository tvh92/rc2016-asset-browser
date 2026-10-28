"""Animation picker for a batch export: shown only when a model has more clips than CLIP_PICKER_THRESHOLD."""
from __future__ import annotations

from rc2016.app.browser import group_clips
from rc2016.app.clip_viewer import ClipViewer
from rc2016.app.theme import CHECK_GAP, CHECK_SIZE, _draw_check_box, apply_titlebar, current_palette

# Models with this many clips or fewer export every clip without asking: reading them is quick.
CLIP_PICKER_THRESHOLD = 10


def needs_picker(listed) -> bool:
    """True when a listed (record, clip labels) pair has more clips than the threshold."""
    return any(len(labels) > CLIP_PICKER_THRESHOLD for _record, labels in listed)


class ClipSelection:
    """Which clips of each model are ticked; every clip starts ticked. Holds no Tk state, so the rules are testable."""

    def __init__(self, listed):
        self.models = [(record, list(labels)) for record, labels in listed]
        self.ticked = [set(range(len(labels))) for _record, labels in self.models]

    def visible(self, model: int, query: str = "", mode: str = "all") -> list[int]:
        """Clip indices of one model that match every word of ``query`` and the ``mode`` filter
        ("all", "ticked" or "unticked"), in label order."""
        terms = query.casefold().split()
        shown = []
        for index, label in enumerate(self.models[model][1]):
            if terms and not all(term in label.casefold() for term in terms):
                continue
            on = index in self.ticked[model]
            if (mode == "ticked" and not on) or (mode == "unticked" and on):
                continue
            shown.append(index)
        return shown

    def set_ticked(self, model: int, indices, value: bool) -> None:
        if value:
            self.ticked[model].update(indices)
        else:
            self.ticked[model].difference_update(indices)

    def totals(self) -> tuple[int, int]:
        """(ticked clips, all clips) across every model."""
        ticked = sum(len(on) for on in self.ticked)
        return ticked, sum(len(labels) for _record, labels in self.models)

    def result(self) -> dict[str, tuple[str, ...]]:
        """The clips to export for each model that is not fully ticked; a model absent from the result exports all clips."""
        return {
            record.model_id: tuple(label for index, label in enumerate(labels) if index in ticked)
            for (record, labels), ticked in zip(self.models, self.ticked) if len(ticked) < len(labels)
        }


def _leaves(nodes):
    """Clip positions of a ``group_clips`` tree, in menu order."""
    for _text, node in nodes:
        if isinstance(node, int):
            yield node
        else:
            yield from _leaves(node)


class ClipPickerDialog:
    """Modal list of the clips of every model, with search, a Ticked/Unticked filter, folder and model ticks,
    expand and collapse, and keyboard control. Each row has a tick box: empty, ticked, or a dash when partly ticked.
    With ``source_root``, a viewport beside the list plays the clip whose name is clicked (see ClipViewer); the
    viewer reads the archive with ``index_root``, ``borrowed`` and ``cinematic`` as the export would."""

    MODES = (("all", "All"), ("ticked", "Ticked"), ("unticked", "Unticked"))

    def __init__(self, tk, ttk, parent, listed, source_root=None, index_root=None, borrowed=False, cinematic=False):
        self.tk, self.ttk, self.parent = tk, ttk, parent
        self.selection = ClipSelection(listed)
        self.targets = {}   # tree item -> (model, clip indices, text, is_clip) for every row shown
        self.opened = {f"m{model}" for model in range(len(listed))}   # models start open, folders closed
        self.confirmed = False
        self.search = tk.StringVar()
        self.mode = tk.StringVar(value="all")
        self.status = tk.StringVar()
        self.boxes = self._tick_boxes()

        top = self.top = tk.Toplevel(parent)
        top.withdraw()
        top.title("Choose animations to export")
        top.transient(parent)
        top.minsize(600, 520)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        # The list and the viewport sit either side of a divider the user can drag.
        split = self.split = ttk.PanedWindow(top, orient="horizontal")
        split.grid(row=0, column=0, sticky="nsew")
        left = self.left = ttk.Frame(split)
        split.add(left, weight=1)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        hint = ("Every animation starts ticked. Click a tick box or press Space to tick it; a folder or model ticks "
                "everything inside it.")
        if source_root:
            hint += " Click an animation's name or press Enter to play it in the viewport."
        ttk.Label(left, text=hint, style="Muted.TLabel", wraplength=560, padding=(12, 12, 12, 6)).grid(row=0, column=0,
                                                                                                        sticky="ew")
        self.viewer = None
        if source_root:
            pane = ttk.Frame(split, padding=(0, 12, 12, 12))
            split.add(pane, weight=0)
            self.viewer = ClipViewer(tk, ttk, pane, source_root, index_root, borrowed, cinematic)

        bar = ttk.Frame(left, padding=(12, 0, 12, 6))
        bar.grid(row=1, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.search_entry = ttk.Entry(bar, textvariable=self.search)
        self.search_entry.grid(row=0, column=0, sticky="ew")
        filters = ttk.Frame(bar)
        filters.grid(row=0, column=1, padx=(8, 0))
        for value, label in self.MODES:
            ttk.Radiobutton(filters, text=label, value=value, variable=self.mode).pack(side="left", padx=(0, 4))

        body = ttk.Frame(left, padding=(12, 0))
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(body, show="tree", selectmode="browse")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        footer = ttk.Frame(left, padding=12)
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status).grid(row=0, column=0, columnspan=2, sticky="w")
        tools = ttk.Frame(footer)
        tools.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(tools, text="Expand all", command=lambda: self._set_open(True)).pack(side="left")
        ttk.Button(tools, text="Collapse all", command=lambda: self._set_open(False)).pack(side="left", padx=(6, 0))
        buttons = ttk.Frame(footer)
        buttons.grid(row=1, column=1, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Tick shown", command=lambda: self._tick_shown(True)).pack(side="left")
        ttk.Button(buttons, text="Untick shown", command=lambda: self._tick_shown(False)).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Cancel", command=self._close).pack(side="left", padx=(18, 0))
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="left", padx=(6, 0))
        ttk.Label(footer, text="Ctrl+F search   ·   Space tick   ·   Esc clears the search, else cancels   ·   Ctrl+Enter OK",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.search.trace_add("write", self.rebuild)
        self.mode.trace_add("write", self.rebuild)
        self.tree.bind("<ButtonRelease-1>", self._on_click)
        self.tree.bind("<<TreeviewOpen>>", lambda _event: self._remember_open(True))
        self.tree.bind("<<TreeviewClose>>", lambda _event: self._remember_open(False))
        self.tree.bind("<space>", lambda _event: self._toggle_focus())
        self.tree.bind("<Return>", lambda _event: self._enter_focus())
        self.search_entry.bind("<Down>", self._focus_tree)
        self.search_entry.bind("<Return>", self._focus_tree)
        top.bind("<Control-f>", lambda _event: self.search_entry.focus_set())
        top.bind("<Control-Return>", self._ok)
        top.bind("<Escape>", self._escape)
        top.protocol("WM_DELETE_WINDOW", self._close)

    # Building the rows

    def rebuild(self, *_args) -> None:
        """Refill the tree for the current search and filter, keeping the focused row when it is still shown."""
        tree, query, mode = self.tree, self.search.get(), self.mode.get()
        searching = bool(query.strip())
        focus = tree.focus()
        tree.delete(*tree.get_children())
        self.targets = {}
        for model, (record, labels) in enumerate(self.selection.models):
            shown = self.selection.visible(model, query, mode)
            if not shown:
                continue
            iid = f"m{model}"
            self.targets[iid] = (model, shown, record.display_name, False)
            tree.insert("", "end", iid=iid, open=searching or iid in self.opened)
            self._add_nodes(iid, group_clips([labels[index] for index in shown]), shown, model, searching)
        self._refresh()
        if focus and tree.exists(focus):
            tree.focus(focus)
            tree.selection_set(focus)
            tree.see(focus)

    def _add_nodes(self, parent, nodes, shown, model, searching) -> None:
        for text, node in nodes:
            if isinstance(node, int):
                index = shown[node]
                iid = f"c{model}.{index}"
                self.targets[iid] = (model, [index], text, True)
                self.tree.insert(parent, "end", iid=iid)
                continue
            iid = f"{parent}/{text}"
            while self.tree.exists(iid):
                iid += "'"
            self.targets[iid] = (model, [shown[leaf] for leaf in _leaves(node)], text, False)
            self.tree.insert(parent, "end", iid=iid, open=searching or iid in self.opened)
            self._add_nodes(iid, node, shown, model, searching)

    def _tick_boxes(self) -> dict:
        """Tick boxes for the rows, drawn in the current palette: unticked, ticked, and partly ticked (a dash)."""
        palette = current_palette()
        boxes = {}
        for state, fill, border, mark in (("off", palette["field"], palette["muted"], None),
                                          ("on", palette["accent"], palette["accent"], palette["select_fg"]),
                                          ("part", palette["field"], palette["accent"], None)):
            image = self.tk.PhotoImage(master=self.parent, width=CHECK_SIZE + CHECK_GAP, height=CHECK_SIZE)
            _draw_check_box(image, fill, border, mark)
            if state == "part":
                for x in range(4, CHECK_SIZE - 4):
                    image.put(border, to=(x, 8, x + 1, 10))
            boxes[state] = image
        return boxes

    def _refresh(self) -> None:
        """Update every row's tick box and count, and the status line. Rows are not refilled, so a row
        that a filter no longer matches stays visible until the search or filter changes."""
        ticked_in = self.selection.ticked
        shown_clips = 0
        for iid, (model, indices, text, is_clip) in self.targets.items():
            on = sum(1 for index in indices if index in ticked_in[model])
            box = self.boxes["on" if on == len(indices) else "off" if on == 0 else "part"]
            if is_clip:
                shown_clips += 1
                self.tree.item(iid, text=text, image=box)
            else:
                self.tree.item(iid, text=f"{text}  ({on} of {len(indices)})", image=box)
        ticked, total = self.selection.totals()
        models = len(self.selection.models)
        line = f"{ticked} of {total} animations ticked across {models} model{'s' if models != 1 else ''}"
        if self.search.get().strip() or self.mode.get() != "all":
            line += f"   ·   {shown_clips} shown"
        self.status.set(line)

    # Actions

    def _on_click(self, event) -> None:
        """A tick box ticks its row. A clip's name shows that clip; a folder or model name ticks its clips."""
        row = self.tree.identify_row(event.y)
        if not row:
            return
        element = self.tree.identify_element(event.x, event.y)
        if element == "Treeitem.indicator":
            return
        self.tree.focus(row)
        if "image" in element or not self._is_clip(row):
            self._toggle(row)
        else:
            self._show_clip(row)

    def _is_clip(self, item: str) -> bool:
        entry = self.targets.get(item)
        return bool(entry and entry[3])

    def _show_clip(self, item: str) -> None:
        """Play one clip in the viewport beside the list."""
        entry = self.targets.get(item)
        if entry is None or self.viewer is None:
            return
        model, indices, _text, _is_clip = entry
        record, labels = self.selection.models[model]
        self.viewer.show(record, labels[indices[0]])

    def _enter_focus(self) -> str:
        """Enter shows the focused clip, or ticks the focused folder or model."""
        item = self.tree.focus()
        if self._is_clip(item):
            self._show_clip(item)
        else:
            self._toggle(item)
        return "break"

    def _toggle_focus(self) -> str:
        self._toggle(self.tree.focus())
        return "break"

    def _toggle(self, item: str) -> None:
        """Tick or untick a row: all of its clips become ticked unless every one already is."""
        entry = self.targets.get(item)
        if entry is None:
            return
        model, indices, _text, _is_clip = entry
        value = not all(index in self.selection.ticked[model] for index in indices)
        self.selection.set_ticked(model, indices, value)
        self._refresh()

    def _tick_shown(self, value: bool) -> None:
        """Tick or untick every clip the tree is showing."""
        by_model: dict[int, list[int]] = {}
        for model, indices, _text, is_clip in self.targets.values():
            if is_clip:
                by_model.setdefault(model, []).extend(indices)
        for model, indices in by_model.items():
            self.selection.set_ticked(model, indices, value)
        self._refresh()

    def _set_open(self, value: bool) -> None:
        self.opened = {iid for iid, entry in self.targets.items() if not entry[3]} if value else set()
        for iid, entry in self.targets.items():
            if not entry[3]:
                self.tree.item(iid, open=value)

    def _remember_open(self, value: bool) -> None:
        """Keep folders the user opens or closes open across searches and filters."""
        item = self.tree.focus()
        if item:
            (self.opened.add if value else self.opened.discard)(item)

    def _focus_tree(self, _event=None) -> str:
        self.tree.focus_set()
        if not self.tree.focus() and self.tree.get_children():
            first = self.tree.get_children()[0]
            self.tree.focus(first)
            self.tree.selection_set(first)
        return "break"

    def _close(self) -> None:
        """Stop the preview while its Tk widgets still exist, then close the picker."""
        if self.viewer is not None:
            self.viewer.close()
            self.viewer = None
        if self.top.winfo_exists():
            self.top.destroy()

    def _ok(self, _event=None) -> None:
        self.confirmed = True
        self._close()

    def _escape(self, _event=None) -> None:
        if self.search.get():
            self.search.set("")
        else:
            self._close()

    def show(self) -> dict[str, tuple[str, ...]] | None:
        """Run the dialog until it closes. Returns the clips to export per model (see ClipSelection.result),
        or None when cancelled."""
        top = self.top
        self.rebuild()
        top.update_idletasks()
        width = min(1140 if self.viewer else 760, top.winfo_screenwidth() - 40)
        height = min(760, top.winfo_screenheight() - 120)
        x = max(0, (top.winfo_screenwidth() - width) // 2)   # centred on the screen
        y = max(0, (top.winfo_screenheight() - height) // 2)
        top.geometry(f"{width}x{height}+{x}+{y}")
        top.deiconify()
        top.update()
        if self.viewer is not None:
            self.split.sashpos(0, width - 380)   # the viewport keeps its natural width; the list takes the rest
        apply_titlebar(top)
        top.grab_set()
        self.search_entry.focus_set()
        self.parent.wait_window(top)
        return self.selection.result() if self.confirmed else None
