"""The main window (Tk presentation layer) of the asset browser."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import queue
import re
import threading
import traceback

from rc2016.app.clip_picker import CLIP_PICKER_THRESHOLD, ClipPickerDialog, needs_picker
from rc2016.app.batch import (
    BatchRunner, ExportBatchRunner, ModelCandidate, PipelineBatchRunner, PipelineIndexScanner,
    PipelineModelCandidate, discover_models, ensure_disjoint, folder_layout, parse_prefixes, status_for)
from rc2016.app.settings import _default_blender, _default_interpreter, _load_settings, _save_settings
from rc2016.common import remember_var, reset_remembered
from rc2016.app.theme import (
    UI_SCALES, _Tooltip, _configure_tk_runtime, apply_theme, apply_titlebar, apply_ui_scale, color, listen,
    themed_scrolled_text_class, track, windows_prefers_dark)


ALWAYS_COLLAPSED_FOLDERS = {"chunk", "bangle_set"}


HELP = {
    "source": (
        "The game folder you unpacked from the PS4 .pkg file.\n\n"
        "It is the folder named after the game ID, for example:\n"
        "...\\Downloads\\Ratchet & Clank (EUR)\\CUSA01073\n\n"
        "Click Browse… and pick that folder. The model list fills in automatically."),
    "output": (
        "Where the finished Blender files and reports are saved.\n\n"
        "Filled in for you next to the game folder. It must be a different "
        "folder from the game folder; you can leave it as is."),
    "python": (
        "A normal Python 3 installation on this PC, used behind the scenes.\n\n"
        "Found automatically; you normally don't need to touch this."),
    "backend": (
        "Full archive pipeline (recommended): builds complete Blender scenes "
        "with model, materials and animations.\n\n"
        "Transform only: the older animation-only extractor."),
    "blender": (
        "Blender 5.0 (blender.exe), used to build the .blend files.\n\n"
        "Found automatically when installed in the default place. Browsing "
        "the model list works without it."),
    "scale": "Make all text and controls bigger or smaller. Remembered for next time.",
    "dark": "Dark or light colours for the whole window. Applies at once.",
    "reset": ("Put every option, toggle and window setting back to its first-start value: Vertical layout, 100% text size, "
              "the window maximised and the Browser split in its default place. The game folder, output folder, Python and "
              "Blender paths are kept. No files are deleted."),
    "prefixes": "Transform-only mode: only extract clips whose names start with these words.",
    "blend_scene": ("Writes one validated Blender scene (.blend) per model: the RC PS4 game shader, the shared review "
                    "lighting, the HDRI, the camera and render region framing for quick rendering, and every animation "
                    "as an action. Slow, and needs Blender.\n\nWhile it is on, the other export formats are not written. "
                    "Skeleton and animations can still be left out."),
    "group": ("Blender Scene only. After the selected models are built, they are also placed side by side "
              "in one .blend under groups/, with one camera and one Render Region framing them all. Each model keeps "
              "its own .blend as well."),
    "picker": ("Before an export with animations, the clips of the selected models are read. When a model has more clips "
               "than this number, a list opens to choose which ones to export; 0 opens it for any model with clips.\n\n"
               "Unticked, the list never opens and every clip is exported."),
    "skeleton": ("Keeps the joints and skin weights, so the model can be posed and animated. Off exports the meshes only, "
                 "as static geometry, and no animations are written.\n\nUseful for props or for a model that is only "
                 "needed for its shape."),
    "scan": "Re-read the game folder and list every model it contains.",
    "extract": (
        "Exports the selected models in the formats chosen under Options… (GLB by default), "
        "with their textures and animations.\n\n"
        "Select rows in the list first (Ctrl-click or Shift-click for several)."),
    "options": "Export formats, what to include, Python/Blender locations and display size.",
    "formats": (
        "GLB/glTF: model, skeleton, materials and animations in one standard file; opens in Blender, "
        "Unity, Unreal, Godot, three.js and most viewers.\n"
        "OBJ: the model and its textures only, in the rest pose (OBJ has no skeleton or animation).\n"
        "FBX and USD are converted from the glTF by Blender, so they need Blender."),
    "cancel": "Stop the running extraction. Finished models are kept.",
    "filter": (
        "Search by file name, folder, asset ID, or the actor/animset that uses the model "
        "(for example 'hydro harvester' or 'enm_'). Several words must all match.\n\n"
        "Numbered rc1 mobys (moby-1964 etc.) have no name in the game data; where a named "
        "actor uses one, it shows in the 'Used by' column and is searchable."),
    "folder_tree": (
        "The folders of the game's models. Select one to list the models inside it, sub-folders included; "
        "All models lists every model the search and filters show.\n\n"
        "Click a folder's arrow to open or close it."),
    "tree": (
        "The models in the selected folder, with their output state. Ctrl-click or Shift-click selects several for "
        "extraction. The selection stays when you choose another folder."),
    "expand": (
        "Opens every folder on the left, or closes them all again.\n\n"
        "Without this, folders open by themselves for short lists and searches. "
        "chunk and bangle folders stay closed in that automatic mode."),
    "state": (
        "READY: nothing recorded for this model in the output folder yet\n"
        "EXPORTED: exported to the output folder (it holds an export.json)\n"
        "PREVIOUS: an earlier research Blender run recorded this model"),
    "state_transform": (
        "READY: not extracted yet\n"
        "VERIFIED: already extracted and checked\n"
        "STALE: extracted before, but out of date\n"
        "NO ANIMSET: no animation found for this model"),
    "asset_type": (
        "Model: the model list with the 3D view, textures and details.\n"
        "Level: the game levels, in a 3D level viewer.\n"
        "Flash UI: the menu and HUD movies.\n"
        "Texture: every texture in the archive, with or without a model.\n\n"
        "The List/Grid switch and Search belong to the model and texture lists; State, Shading and Expand all are for models only."),
    "layout": (
        "Vertical: each viewer has its viewport on the left and its controls on the right.\n"
        "Horizontal: the viewport on top and the controls below it, in columns.\n"
        "Applies to the 3D model, textures, level and Flash UI viewers. Drag the divider to resize; "
        "each layout remembers its own sizes."),
    "select_visible": "Select every model the search, filters and chosen folder show (all of them when nothing is filtered).",
    "clear_selection": "Deselect every model.",
    "list_view": (
        "List: the models of the selected folder as rows, with the output state of each.\n"
        "Grid: the same models as thumbnails, beside the same folder list. "
        "Clicking a thumbnail loads the model in the 3D view."),
    "shading": (
        "Filter by the viewer shading rule of the model's materials (Details lists it per slot):\n"
        "Native translation: at least one material uses a bounded native deferred surface DAG.\n"
        "Generic reading: at least one material graph has no decoded rule, so _g is read as coloured "
        "F0 + gloss alpha; a to-do list for decoding.\n"
        "Family fallback: at least one graph only matches a family rule (basic_skin, "
        "basic_normal_gloss*, basic_incandescence).\n"
        "No material graph: a material names no graph.\n"
        "Exact rules only: every material has a decoded writer rule.\n\n"
        "The first use indexes the archive's asset links (about half a minute, once)."),
}
# Output states each backend can produce; the State filter offers only those.
STATE_FILTERS = {"pipeline": ("All", "READY", "EXPORTED", "PREVIOUS"),
                 "transform": ("All", "READY", "VERIFIED", "STALE", "NO ANIMSET")}
SHADING_FILTERS = {"Native translation": "native translation", "Generic reading": "generic reading", "Family fallback": "family fallback",
                   "No material graph": "no material graph", "Exact rules only": None}


class AssetBrowserApp:
    """Tkinter presentation layer; all file/process work stays off the UI thread."""

    def __init__(self) -> None:
        _configure_tk_runtime()
        try:
            import tkinter as tk
            from tkinter import filedialog, messagebox, ttk
            from tkinter.scrolledtext import ScrolledText
        except ImportError as error:
            raise RuntimeError(
                "Tkinter is required for the GUI. Run this with a regular Windows Python "
                "installation, not Blender's bundled Python, or select that interpreter "
                "in the GUI once it is running."
            ) from error
        self.tk = tk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.ttk = ttk
        self.ScrolledText = themed_scrolled_text_class(ScrolledText, tk, ttk)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            raise RuntimeError(
                "Tkinter is installed, but its Tcl/Tk runtime could not start. "
                "Repair the regular Windows Python installation or run the headless "
                "--scan mode. Blender's bundled Python does not include Tkinter. "
                f"Details: {error}"
            ) from error
        self.root.title("R&C 2016 Asset Browser and Extractor")
        appearance = _load_settings().get("appearance", "")
        self.dark = appearance == "dark" if appearance in ("dark", "light") else windows_prefers_dark()
        apply_theme(self.root, ttk, self.dark)
        scale = _load_settings().get("ui_scale", "100%")
        self.ui_scale_var = tk.StringVar(value=scale if scale in UI_SCALES else "100%")
        apply_ui_scale(self.root, ttk, int(self.ui_scale_var.get().rstrip("%")))
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.minsize(min(1480, self.root.winfo_screenwidth()), 900)
        self._restore_window()
        self.events: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()
        self.records: list[ModelCandidate | PipelineModelCandidate] = []
        self.filtered_records: list[ModelCandidate | PipelineModelCandidate] = []
        self.runner: BatchRunner | PipelineBatchRunner | None = None
        self.worker: threading.Thread | None = None
        self.item_by_record: dict[str, ModelCandidate | PipelineModelCandidate] = {}
        self.item_by_path: dict[str, str] = {}
        self._refresh_after = None
        self.progress_done = 0
        self.progress_total = 0
        self._build_variables()
        self._build_ui()
        self.root.after(100, self._drain_events)
        if self.source_var.get() and self.output_var.get():
            # Remembered from last time: list the models straight away.
            self.root.after(300, self.scan)

    def _build_variables(self) -> None:
        tk = self.tk
        saved = _load_settings()
        remembered = lambda key: saved.get(key, "") if Path(saved.get(key, "") or "\0").exists() else ""
        self.source_var = tk.StringVar(value=remembered("source"))
        self.output_var = tk.StringVar(value=saved.get("output", "") if remembered("source") else "")
        self.interpreter_var = tk.StringVar(value=remembered("python") or str(_default_interpreter()))
        self.blender_var = tk.StringVar(value=remembered("blender") or _default_blender())
        self.backend_var = remember_var(tk.StringVar(value="Full archive pipeline"), "export.backend",
                                        ("Transform only", "Full archive pipeline"))
        self.filter_var = tk.StringVar()
        self.model_filter_stale = False   # the shared Search changed while the Texture type showed; the model list refreshes on return
        self.folder_mode = "auto"  # "auto", "expanded" or "collapsed"; see toggle_expand_all
        self.folder_scope = ""     # "" lists every model; otherwise the folder, and the folders in it, the list shows
        self.selected_map: dict = {}  # record key -> record, for every selected model, including those the list hides
        self._queue_rows: dict[str, str] = {}   # queue key (model path) -> item of the extraction queue; see _fill_queue
        self._rebuilding = False   # True while the folder tree and the list are refilled; their selection events are ignored
        self.state_var = tk.StringVar(value="All")
        self.shading_var = tk.StringVar(value="All")
        self.prefix_var = remember_var(tk.StringVar(), "export.prefixes")
        # Export options (Options… window): every option is restored from, and saved to, the settings file on change.
        from rc2016.export.models import FORMATS
        self.export_format_vars = {key: remember_var(tk.BooleanVar(value=key == "glb"), f"export.format.{key}") for key in FORMATS}
        self.export_textures_var = remember_var(tk.BooleanVar(value=True), "export.textures")
        self.export_dds_var = remember_var(tk.BooleanVar(value=False), "export.dds")
        self.export_animations_var = remember_var(tk.BooleanVar(value=False), "export.animations")
        self.export_skeleton_var = remember_var(tk.BooleanVar(value=True), "export.skeleton")
        self.export_lod_var = remember_var(tk.StringVar(value="Highest"), "export.lod", ("Highest", "Lowest", "All"))
        self.blend_scene_var = remember_var(tk.BooleanVar(value=False), "export.blend_scene")
        self.group_blend_var = remember_var(tk.BooleanVar(value=False), "export.group_blend")
        self.group_name_var = remember_var(tk.StringVar(value="Selected models"), "export.group_name")
        # Animation picker: opens when a model has more clips than the threshold; switched off, every clip is exported.
        self.picker_var = remember_var(tk.BooleanVar(value=True), "export.picker")
        self.picker_threshold_var = remember_var(tk.StringVar(value=str(CLIP_PICKER_THRESHOLD)), "export.picker_threshold")
        self.batch_clips: dict[str, tuple[str, ...]] = {}     # the picker's ticks for the running export
        self.retained_clips: dict[str, tuple[str, ...]] = {}  # those of an export that did not finish, for the next picker
        self.export_summary_var = tk.StringVar()
        self.exported_names: set[str] = set()
        self.selection_var = tk.StringVar(value="0 models selected")
        self.progress_var = tk.StringVar(value="Step 1: pick the game folder (Browse…).")

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        # Segmented buttons (asset type, List/Grid, layout) are roomier than ordinary buttons.
        ttk.Style(self.root).configure("Segment.Toolbutton", padding=(14, 6))
        from rc2016.app.layout import SplitLayout
        self.split_layout = SplitLayout(_load_settings().get("layout", ""))

        sources = ttk.LabelFrame(self.root, text="Sources and output")
        sources.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        sources.columnconfigure(1, weight=1)
        self._path_row(sources, 0, "Game folder (source)", self.source_var, self._browse_source, HELP["source"])
        self._path_row(sources, 1, "Output folder", self.output_var, self._browse_output, HELP["output"])
        option_row = ttk.Frame(sources)
        option_row.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 6))
        options_button = ttk.Button(option_row, text="Options…", command=self.show_options)
        options_button.pack(side="left")
        ttk.Label(option_row, textvariable=self.export_summary_var, style="Muted.TLabel").pack(side="left", padx=10)
        self._tip(HELP["options"], options_button)
        self._build_options_window()

        actions = ttk.Frame(self.root)
        actions.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        actions.columnconfigure(2, weight=1)
        # The status text, the bar and the three buttons stretch to the row's height (Extract selected is the tallest).
        scan_button = ttk.Button(actions, text="Scan archive", command=self.scan)
        scan_button.grid(row=0, column=0, padx=(0, 8), sticky="ns")
        # Sized to its text, so the bar gets the room the text does not use; the bar moves as the text changes.
        ttk.Label(actions, textvariable=self.progress_var, anchor="w").grid(row=0, column=1, padx=(0, 8), sticky="ns")
        # A scan reports fractions of PROGRESS_STEPS; a batch counts models.
        self.progressbar = ttk.Progressbar(actions, mode="determinate", maximum=1, value=0)
        # The 4 px right pad matches Extract's left pad, so the bar is centred between the two buttons.
        self.progressbar.grid(row=0, column=2, sticky="nsew", padx=(0, 4))
        self.start_button = tk.Button(actions, text="Extract selected", command=self.start_batch, state="disabled",
                                      font="AccentButtonFont", relief="flat", borderwidth=0, padx=18, pady=6,
                                      cursor="hand2")
        track(self.start_button, foreground="select_fg", disabledforeground="accent_disabled", background="accent",
              activebackground="accent_active", activeforeground="select_fg")
        self.start_button.grid(row=0, column=3, padx=4, sticky="ns")
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel_batch, state="disabled")
        self.cancel_button.grid(row=0, column=4, padx=(4, 0), sticky="ns")
        self._tip(HELP["scan"], scan_button)
        self._tip(HELP["extract"], self.start_button)
        self._tip(HELP["cancel"], self.cancel_button)

        main = ttk.Panedwindow(self.root, orient="vertical")
        main.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        top = ttk.Frame(main)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        main.add(top, weight=6)

        # One toolbar row: asset type, then the model list's own tools (hidden for Level and Flash UI), then the layout.
        toolbar = ttk.Frame(top)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        types = ttk.Frame(toolbar)
        types.grid(row=0, column=0, sticky="w", padx=(0, 18))
        # Each asset type has its own list (left) and viewer (right); see AssetBrowser.show_asset_type.
        self.asset_type_var = tk.StringVar(value="Model")
        self.asset_type_buttons = {}
        for kind in ("Model", "Level", "Texture", "Flash UI"):
            button = ttk.Radiobutton(types, text=kind, value=kind, variable=self.asset_type_var, style="Segment.Toolbutton", width=9,
                                     command=lambda: self.browser.show_asset_type(self.asset_type_var.get()))
            button.pack(side="left", padx=(0, 2))
            self.asset_type_buttons[kind] = button
        self._tip(HELP["asset_type"], *self.asset_type_buttons.values())
        self.model_tools = filters = ttk.Frame(toolbar)
        filters.grid(row=0, column=1, sticky="ew")
        filters.columnconfigure(2, weight=1)
        view_box = ttk.Frame(filters)
        view_box.grid(row=0, column=0, padx=(0, 18))
        self.list_view_var = tk.StringVar(value="List")
        self.model_list_view = "List"   # the model list's own mode, kept while the Texture type uses the shared toggle
        for kind in ("List", "Grid"):
            button = ttk.Radiobutton(view_box, text=kind, value=kind, variable=self.list_view_var, style="Segment.Toolbutton", width=6,
                                     command=lambda: self.show_list_view(self.list_view_var.get()))
            button.pack(side="left", padx=(0, 2))
            self._tip(HELP["list_view"], button)
        filter_label = ttk.Label(filters, text="Search  ⓘ")
        filter_label.grid(row=0, column=1, padx=(0, 8))
        filter_entry = ttk.Entry(filters, textvariable=self.filter_var, width=8)
        filter_entry.grid(row=0, column=2, sticky="ew")
        self.filter_var.trace_add("write", lambda *_args: self.on_filter_changed())
        self.expand_button = ttk.Button(filters, text="Expand all", width=11, command=self.toggle_expand_all)
        self._tip(HELP["expand"], self.expand_button)
        self.expand_button.grid(row=0, column=4, padx=(10, 0), sticky="ns")
        state_label = ttk.Label(filters, text="State")
        state_label.grid(row=0, column=5, padx=(18, 8))
        self.state_box = state = ttk.Combobox(filters, textvariable=self.state_var, state="readonly", width=14,
                                              values=STATE_FILTERS["pipeline"])
        state.grid(row=0, column=6)
        self._tip(HELP["filter"], filter_label, filter_entry)
        self.state_tips = [_Tooltip(self.tk, widget, HELP["state"]) for widget in (state_label, state)]
        state.bind("<<ComboboxSelected>>", lambda _event: self.refresh_table())
        shading_label = ttk.Label(filters, text="Shading")
        shading_label.grid(row=0, column=7, padx=(18, 8))
        shading = ttk.Combobox(filters, textvariable=self.shading_var, state="readonly", values=("All", *SHADING_FILTERS), width=17)
        shading.grid(row=0, column=8)
        self._tip(HELP["shading"], shading_label, shading)
        shading.bind("<<ComboboxSelected>>", lambda _event: self.refresh_table())
        # Model-only controls: hidden for the Texture type, which shares the Search and List|Grid switch.
        self.model_only_tools = (self.expand_button, state_label, state, shading_label, shading)
        # Layout of the viewers (all asset types): Vertical = viewport left, controls right; Horizontal = stacked.
        layout_box = ttk.Frame(toolbar)
        layout_box.grid(row=0, column=2, sticky="e", padx=(18, 0))
        layout_label = ttk.Label(layout_box, text="Layout")
        layout_label.pack(side="left", padx=(0, 8))
        self.layout_var = tk.StringVar(value=self.split_layout.mode)
        self.layout_buttons = []
        from rc2016.app.layout import MODES
        for mode in MODES:
            button = ttk.Radiobutton(layout_box, text=mode, value=mode, variable=self.layout_var, style="Segment.Toolbutton", width=10,
                                     command=lambda: self.set_layout(self.layout_var.get()))
            button.pack(side="left", padx=(0, 2))
            self.layout_buttons.append(button)
        self._tip(HELP["layout"], layout_label, *self.layout_buttons)

        # Model view: the model list | review notebook.
        model_view = ttk.Frame(top)
        model_view.grid(row=1, column=0, sticky="nsew")
        model_view.columnconfigure(0, weight=1)
        model_view.rowconfigure(0, weight=1)

        browser_split = self.browser_split = ttk.Panedwindow(model_view, orient="horizontal")
        browser_split.grid(row=0, column=0, sticky="nsew")
        # The folder tree stays on the left; the model list and the thumbnail grid share the right cell (show_list_view swaps them).
        self.list_host = list_host = ttk.Frame(browser_split)
        browser_split.add(list_host, weight=3)
        list_host.columnconfigure(1, weight=1)
        list_host.rowconfigure(0, weight=1)
        self._build_folder_tree(list_host)
        self.table_frame = table_frame = ttk.Frame(list_host)
        table_frame.grid(row=0, column=1, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("model", "animset", "state", "details"), show="tree headings", selectmode="extended")
        self.table.heading("#0", text="Model")
        self.table.heading("model", text="Used by (actor)")
        self.table.heading("animset", text="Asset ID / animset")
        self.table.heading("state", text="Output state")
        self.table.heading("details", text="Details")
        self.table.column("#0", width=330, minwidth=160, anchor="w")
        self.table.column("model", width=170, anchor="w")
        self.table.column("animset", width=150, anchor="w")
        self.table.column("state", width=90, anchor="center")
        self.table.column("details", width=220, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self._tip(HELP["tree"], self.table)
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.update_selection_label())
        self.table.bind("<Button-1>", self._table_click)
        self.table.bind("<Double-1>", self._table_double_click)

        from rc2016.app.browser import AssetBrowser
        self.browser = AssetBrowser(self, browser_split, type_host=top, model_view=model_view)
        self.root.after(200, lambda: browser_split.sashpos(0, int(self.root.winfo_width() * self._saved_split())))
        self.prefix_entry.configure(state="disabled")

        bottom = ttk.Frame(main)
        bottom.columnconfigure(1, weight=1)
        bottom.rowconfigure(0, weight=1)
        main.add(bottom, weight=1)
        # The extraction queue: one row per model of the batch, marked queued, running, done, failed or cancelled.
        self.queue_tree = ttk.Treeview(bottom, columns=("status",), show="tree headings", height=4, selectmode="none")
        self.queue_tree.heading("#0", text="Extraction queue")
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.column("#0", width=280, minwidth=160, anchor="w")
        self.queue_tree.column("status", width=90, anchor="center", stretch=False)
        self.queue_tree.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.log = self.ScrolledText(bottom, height=4, state="disabled", wrap="none", font="TkFixedFont")
        self.log.grid(row=0, column=1, sticky="nsew")
        self._status_colors()
        listen(self._status_colors)

        # Selection: the actions on the model list, then how many models are selected.
        selection_row = ttk.Frame(self.root)
        selection_row.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))
        select_button = ttk.Button(selection_row, text="Select visible", command=self.select_visible)
        select_button.pack(side="left")
        clear_button = ttk.Button(selection_row, text="Deselect all", command=self.clear_selection)
        clear_button.pack(side="left", padx=(6, 12))
        ttk.Label(selection_row, textvariable=self.selection_var, anchor="w").pack(side="left")
        self._tip(HELP["select_visible"], select_button)
        self._tip(HELP["clear_selection"], clear_button)

    def _status_colors(self) -> None:
        """Queue-row and log-line colours; tags are not widget options, so a theme switch calls this again."""
        for state, token in (("queued", "muted"), ("running", "running"), ("done", "ok"), ("failed", "error"),
                             ("cancelled", "muted")):
            self.queue_tree.tag_configure(state, foreground=color(token))
        for tag, token in (("error", "error"), ("success", "ok"), ("muted", "muted")):
            self.log.tag_configure(tag, foreground=color(token))

    def _build_options_window(self) -> None:
        """All optional yes/no settings, in a window that is built once and shown by Options…"""
        tk, ttk = self.tk, self.ttk
        from rc2016.export.models import FORMATS
        window = self.options_window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("Options")
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_options)
        window.columnconfigure(0, weight=1)

        formats = ttk.LabelFrame(window, text="Export formats  ⓘ", padding=8)
        formats.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        self.blend_check = ttk.Checkbutton(formats, text="Blender Scene (.blend) (includes local setup for quick rendering)",
                                           variable=self.blend_scene_var)
        self.blend_check.grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        self._tip(HELP["blend_scene"], self.blend_check)
        self.group_check = ttk.Checkbutton(formats, text="Also put the selected models into one .blend, framed by one camera",
                                           variable=self.group_blend_var)
        self.group_check.grid(row=1, column=0, columnspan=3, sticky="w", padx=(22, 0), pady=(4, 2))
        self._tip(HELP["group"], self.group_check)
        self.group_name_label = ttk.Label(formats, text="Group file name")
        self.group_name_label.grid(row=2, column=0, sticky="w", padx=(22, 0), pady=4)
        self.group_name_entry = ttk.Entry(formats, textvariable=self.group_name_var, width=28)
        self.group_name_entry.grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(formats, text="saved under groups/", style="Muted.TLabel").grid(row=2, column=2, sticky="w")
        self._tip(HELP["group"], self.group_name_label, self.group_name_entry)
        self.format_checks = []
        for number, (key, (label, _suffix, needs_blender)) in enumerate(FORMATS.items()):
            box = ttk.Checkbutton(formats, text=label + ("  (needs Blender)" if needs_blender else ""),
                                  variable=self.export_format_vars[key])
            box.grid(row=3 + number % 3, column=number // 3, sticky="w", padx=(0, 18), pady=2)
            self._tip(HELP["formats"], box)
            self.format_checks.append(box)

        include = ttk.LabelFrame(window, text="Include", padding=8)
        include.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        self.texture_check = ttk.Checkbutton(include, text="Textures (embedded in .glb; PNG files in a textures folder otherwise)",
                                             variable=self.export_textures_var)
        self.texture_check.grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        self.dds_check = ttk.Checkbutton(include, text="Also the original game textures (.dds, unchanged)",
                                         variable=self.export_dds_var)
        self.dds_check.grid(row=1, column=0, columnspan=3, sticky="w", pady=2)
        self.skeleton_check = ttk.Checkbutton(include, text="Skeleton (joints and skin weights; off exports static meshes)",
                                              variable=self.export_skeleton_var)
        self.skeleton_check.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 2))
        self._tip(HELP["skeleton"], self.skeleton_check)
        self.animation_check = ttk.Checkbutton(include, text="Include animations", variable=self.export_animations_var)
        self.animation_check.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 2))
        picker = ttk.Frame(include)
        picker.grid(row=4, column=0, columnspan=3, sticky="w", padx=(22, 0))
        self.picker_check = ttk.Checkbutton(picker, text="Animation picker: ask which clips to export when a model has more than",
                                            variable=self.picker_var)
        self.picker_check.pack(side="left")
        self.picker_spin = ttk.Spinbox(picker, from_=0, to=9999, width=5, textvariable=self.picker_threshold_var)
        self.picker_spin.pack(side="left", padx=4)
        self.picker_unit = ttk.Label(picker, text="clips (0: always)")
        self.picker_unit.pack(side="left")
        self._tip(HELP["picker"], self.picker_check, self.picker_spin, self.picker_unit)
        self.lod_label = ttk.Label(include, text="Detail level (LOD)")
        self.lod_label.grid(row=5, column=0, sticky="w", pady=(6, 2))
        self.lod_box = ttk.Combobox(include, textvariable=self.export_lod_var, values=("Highest", "Lowest", "All"),
                                    state="readonly", width=10)
        self.lod_box.grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 2))

        programs = ttk.LabelFrame(window, text="Programs", padding=8)
        programs.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        programs.columnconfigure(1, weight=1)
        self._path_row(programs, 0, "Python", self.interpreter_var, self._browse_interpreter, HELP["python"])
        self._path_row(programs, 1, "Blender (optional)", self.blender_var, self._browse_blender, HELP["blender"])

        advanced = ttk.LabelFrame(window, text="Advanced", padding=8)
        advanced.grid(row=3, column=0, sticky="ew", padx=12, pady=6)
        advanced.columnconfigure(1, weight=1)
        backend_label = ttk.Label(advanced, text="Backend  ⓘ")
        backend_label.grid(row=0, column=0, sticky="w", pady=4)
        backend = ttk.Combobox(advanced, textvariable=self.backend_var, state="readonly",
                                values=("Transform only", "Full archive pipeline"), width=28)
        backend.grid(row=0, column=1, sticky="w", padx=6, pady=4)
        backend.bind("<<ComboboxSelected>>", lambda _event: self._backend_changed())
        self._tip(HELP["backend"], backend_label, backend)
        self.prefix_label = ttk.Label(advanced, text="Clip prefixes")
        self.prefix_label.grid(row=1, column=0, sticky="w", pady=4)
        self.prefix_entry = ttk.Entry(advanced, textvariable=self.prefix_var)
        self.prefix_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(advanced, text="transform only; comma-separated", style="Muted.TLabel").grid(row=1, column=2, sticky="w")
        self._tip(HELP["prefixes"], self.prefix_label, self.prefix_entry)

        display = ttk.Frame(window, padding=(12, 6))
        display.grid(row=4, column=0, sticky="ew")
        self.dark_mode_var = tk.BooleanVar(value=self.dark)
        dark_box = ttk.Checkbutton(display, text="Dark mode  ⓘ", variable=self.dark_mode_var, command=self._dark_mode_changed)
        dark_box.pack(side="left", padx=(0, 18))
        self._tip(HELP["dark"], dark_box)
        scale_label = ttk.Label(display, text="UI scale  ⓘ")
        scale_label.pack(side="left", padx=(0, 6))
        scale_box = ttk.Combobox(display, textvariable=self.ui_scale_var, values=UI_SCALES, state="readonly", width=6)
        scale_box.pack(side="left")
        scale_box.bind("<<ComboboxSelected>>", lambda _event: self._ui_scale_changed())
        self._tip(HELP["scale"], scale_label, scale_box)
        ttk.Button(display, text="Close", command=self._close_options).pack(side="right")
        reset = ttk.Button(display, text="Reset to defaults", command=self._reset_to_defaults)
        reset.pack(side="right", padx=(0, 8))
        self._tip(HELP["reset"], reset)

        for variable in (*self.export_format_vars.values(), self.export_textures_var, self.export_dds_var,
                         self.export_animations_var, self.export_skeleton_var, self.export_lod_var, self.blend_scene_var,
                         self.backend_var, self.group_blend_var, self.picker_var):
            variable.trace_add("write", lambda *_args: self._options_changed())
        self._options_changed()

    def show_options(self) -> None:
        window = self.options_window
        window.deiconify()
        window.update()   # mapped first: the title bar is only repainted for a window that exists on screen
        apply_titlebar(window)
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - window.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + 60
        window.geometry(f"+{x}+{y}")
        window.lift()
        window.focus_set()

    def _reset_to_defaults(self) -> None:
        """Every option, toggle and window setting back to its first-start value. The folder and program paths stay."""
        from rc2016.app.layout import VERTICAL
        reset_remembered()
        window_keys = ("window.geometry", "window.zoomed", "window.split", "ui_scale", "appearance")
        _save_settings({key: value for key, value in _load_settings().items() if key not in window_keys})
        self.set_layout(VERTICAL)
        self.ui_scale_var.set("100%")
        self._ui_scale_changed()
        # The saved choice is gone, so the Windows app mode applies again (and is not written back).
        self.dark = windows_prefers_dark()
        self.dark_mode_var.set(self.dark)
        apply_theme(self.root, self.ttk, self.dark)
        self.root.state("normal")
        self.root.geometry("1420x980")
        self.root.state("zoomed")
        self.root.after(200, lambda: self.browser_split.sashpos(0, int(self.root.winfo_width() * self._saved_split())))

    def _close_options(self) -> None:
        self.options_window.withdraw()
        self._remember_settings()

    def _export_formats(self) -> tuple[str, ...]:
        return tuple(key for key, variable in self.export_format_vars.items() if variable.get())

    def _options_changed(self) -> None:
        # The Blender scene is the research pipeline; Transform only ignores every export option.
        pipeline = self.backend_var.get() == "Full archive pipeline"
        blend = pipeline and self.blend_scene_var.get()
        # The Blender scene replaces the portable formats, so their boxes are greyed out while it is on.
        for box in self.format_checks:
            box.configure(state="disabled" if blend else "normal")
        self.blend_check.configure(state="normal" if pipeline else "disabled")
        group_state = "normal" if blend else "disabled"
        self.group_check.configure(state=group_state)
        self.group_name_entry.configure(state=group_state)
        self.group_name_label.configure(style="Disabled.TLabel" if blend else "TLabel")
        # Without a skeleton there is nothing to animate, so the animation choice is switched off.
        self.skeleton_check.configure(state="normal" if pipeline else "disabled")
        self.animation_check.configure(state="normal" if pipeline and self.export_skeleton_var.get() else "disabled")
        animated = pipeline and self.export_skeleton_var.get() and self.export_animations_var.get()
        self.picker_check.configure(state="normal" if animated else "disabled")
        self.picker_spin.configure(state="normal" if animated and self.picker_var.get() else "disabled")
        self.picker_unit.configure(style="TLabel" if animated and self.picker_var.get() else "Disabled.TLabel")
        # Textures, the original .dds files and the LOD level are only written by the portable export.
        self.texture_check.configure(state="disabled" if blend else "normal")
        self.dds_check.configure(state="disabled" if blend else "normal")
        self.lod_label.configure(style="Disabled.TLabel" if blend else "TLabel")
        self.lod_box.configure(state="disabled" if blend else "readonly")
        if not pipeline:
            text = "Transform only: animation data from loose files"
        else:
            parts = ["Blender Scene (.blend)"] if blend else []
            if not blend:
                from rc2016.export.models import FORMATS
                formats = [FORMATS[key][1] for key in self._export_formats()] or ["no format chosen"]
                parts.append(", ".join(formats))
                parts.append("textures" if self.export_textures_var.get() else "no textures")
            if self.export_skeleton_var.get():
                parts.append("all animations" if self.export_animations_var.get() else "no animations")
            else:
                parts.append("no skeleton or animations")
            if not blend and self.export_lod_var.get() != "Highest":
                parts.append(f"{self.export_lod_var.get()} LOD")
            text = "Export: " + " · ".join(parts)
        self.export_summary_var.set(text)

    def export_options(self):
        from rc2016.export.models import ExportOptions
        return ExportOptions(formats=self._export_formats(), textures=self.export_textures_var.get(),
                             source_textures=self.export_dds_var.get(), animations="all" if self.export_animations_var.get() else "none",
                             skeleton=self.export_skeleton_var.get(),
                             lod=self.export_lod_var.get(), blender=self.blender_var.get().strip(),
                             skip_existing=True, cinematic=self.export_animations_var.get(),
                             borrowed=self.export_animations_var.get() and self.browser.borrowed_var.get())

    def _path_row(self, parent, row: int, label: str, variable, command, tooltip: str) -> None:
        ttk = self.ttk
        label_widget = ttk.Label(parent, text=label + "  ⓘ")
        label_widget.grid(row=row, column=0, sticky="w", padx=(6, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        button = ttk.Button(parent, text="Browse…", command=command)
        button.grid(row=row, column=2, padx=(8, 6), pady=4)
        self._tip(tooltip, label_widget, entry, button)

    def _tip(self, text: str, *widgets) -> None:
        for widget in widgets:
            _Tooltip(self.tk, widget, text)

    def set_layout(self, mode: str) -> None:
        """Vertical or Horizontal viewers; remembered for the next start."""
        self.split_layout.set_mode(mode)
        self.layout_var.set(self.split_layout.mode)
        settings = _load_settings()
        settings["layout"] = self.split_layout.mode
        _save_settings(settings)

    def _remember_settings(self) -> None:
        _save_settings({**_load_settings(), "source": self.source_var.get().strip(), "output": self.output_var.get().strip(),
                        "python": self.interpreter_var.get().strip(), "blender": self.blender_var.get().strip(),
                        "ui_scale": self.ui_scale_var.get()})

    def _dark_mode_changed(self) -> None:
        self.dark = self.dark_mode_var.get()
        settings = _load_settings()
        settings["appearance"] = "dark" if self.dark else "light"
        _save_settings(settings)
        apply_theme(self.root, self.ttk, self.dark)

    def _ui_scale_changed(self) -> None:
        apply_ui_scale(self.root, self.ttk, int(self.ui_scale_var.get().rstrip("%")))
        settings = _load_settings()
        settings["ui_scale"] = self.ui_scale_var.get()
        _save_settings(settings)

    def _browse_source(self) -> None:
        chosen = self.filedialog.askdirectory(title="Choose extracted PS4 source root")
        if chosen:
            self.source_var.set(chosen)
            if not self.output_var.get():
                source = Path(chosen)
                self.output_var.set(str(source.parent / f"{source.name} Animation Output"))
            self.scan()

    def _browse_output(self) -> None:
        chosen = self.filedialog.askdirectory(title="Choose a separate output root")
        if chosen:
            self.output_var.set(chosen)
            self._scan_exported()
            if self.records:
                self.refresh_table()
            self.browser.reload()

    def _browse_interpreter(self) -> None:
        chosen = self.filedialog.askopenfilename(title="Choose Python interpreter", filetypes=(("Python", "python*.exe"), ("All files", "*.*")))
        if chosen:
            self.interpreter_var.set(chosen)

    def _browse_blender(self) -> None:
        chosen = self.filedialog.askopenfilename(title="Choose Blender executable", filetypes=(("Blender", "blender.exe"), ("All files", "*.*")))
        if chosen:
            self.blender_var.set(chosen)

    def _apply_backend_states(self) -> None:
        """The State filter offers only the states the chosen backend produces."""
        kind = "pipeline" if self.backend_var.get() == "Full archive pipeline" else "transform"
        self.state_box.configure(values=STATE_FILTERS[kind])
        for tip in self.state_tips:
            tip.text = HELP["state" if kind == "pipeline" else "state_transform"]
        if self.state_var.get() not in STATE_FILTERS[kind]:
            self.state_var.set("All")

    def _backend_changed(self) -> None:
        self.state_var.set("All")
        self._apply_backend_states()
        self.records = []
        self.selected_map = {}   # the other backend's records cannot be exported by this one
        widget_state = "disabled" if self.backend_var.get() == "Full archive pipeline" else "normal"
        self.prefix_entry.configure(state=widget_state)
        self.prefix_label.configure(style="Disabled.TLabel" if widget_state == "disabled" else "TLabel")
        self.refresh_table()
        self.progress_var.set("Scan the source root for this backend")

    def scan(self) -> None:
        if self.runner or (self.worker and self.worker.is_alive()) or self.browser.review:
            return
        source_text = self.source_var.get().strip()
        if not source_text:
            self.messagebox.showinfo("Source root", "Choose a source root first.")
            return
        source = Path(source_text)
        output = Path(self.output_var.get()) if self.output_var.get().strip() else None
        if self.backend_var.get() == "Full archive pipeline":
            if output is None:
                self.messagebox.showerror("Output root", "Choose a separate output root before indexing the archive.")
                return
            try:
                ensure_disjoint(source, output)
            except ValueError as error:
                self.messagebox.showerror("Output root", str(error))
                return
            python = Path(self.interpreter_var.get().strip())
            blender = Path(self.blender_var.get().strip())
            if not python.is_file():
                self.messagebox.showerror("Python not found", "No Python 3 was found on this PC. Install it from python.org, then restart the extractor.")
                return
            self._set_busy(scanning=True, measured=True)
            self.progress_var.set("Reading the archive files…")
            self._append_log("Indexing archive models through the pipeline", "muted")
            self.worker = threading.Thread(target=self._pipeline_scan_worker,
                                           args=(source, output, python, blender), daemon=True)
            self.worker.start()
            return
        self._set_busy(scanning=True)
        self.progress_var.set("Scanning model files…")
        self._append_log("Scanning " + str(source), "muted")
        self.worker = threading.Thread(target=self._scan_worker, args=(source, output), daemon=True)
        self.worker.start()

    def _scan_worker(self, source: Path, output: Path | None) -> None:
        try:
            records = discover_models(source, output)
            self.events.put(("scan_done", {"records": records}))
        except Exception as error:
            self.events.put(("scan_error", {"message": str(error)}))

    def _pipeline_scan_worker(self, source: Path, output: Path, python: Path, blender: Path) -> None:
        try:
            scanner = PipelineIndexScanner(source, output, python, blender)
            records = scanner.scan(progress=lambda fraction: self.events.put(("scan_progress", {"fraction": fraction})))
            self.events.put(("scan_done", {"records": records, "reused": getattr(scanner, "reused", False)}))
        except Exception as error:
            self.events.put(("scan_error", {"message": str(error)}))


    def refresh_table(self) -> None:
        output = Path(self.output_var.get()).expanduser() if self.output_var.get().strip() else None
        needle = self.filter_var.get().casefold().strip()
        state_filter = self.state_var.get()
        shading_filter = self.shading_var.get()
        coverage = None
        if shading_filter != "All":
            links = self.browser.links
            coverage = links.coverage if links.links is not None and links.root == self.source_var.get().strip() else None
            if coverage is None:
                # Built on a worker once; the table refreshes when it is ready (or says why not).
                links.request(lambda ready: self.refresh_table() if ready is not None else self.progress_var.set(links.message),
                              key="shading filter")
        self._sync_selection()   # selected models stay selected, even the ones this list is about to hide
        open_folders = {item for item in self._folder_items() if self.folder_tree.item(item, "open")} if hasattr(self, "folder_tree") else set()
        terms = needle.split()
        self._cancel_pending_refresh()
        matched, statuses = [], []
        for record in self.records:
            is_pipeline = isinstance(record, PipelineModelCandidate)
            relative_model = record.archive_path if is_pipeline else record.relative_model
            haystack = (relative_model + (" " + record.model_id + " " + record.search_text if is_pipeline
                        else " " + (record.relative_animset or ""))).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            state, reason = self._record_status(record, output)   # once per record; reused for the row
            if state_filter != "All" and state != state_filter:
                continue
            if shading_filter != "All":
                kinds = (coverage or {}).get(getattr(record, "archive_path", None) or "")
                wanted = SHADING_FILTERS[shading_filter]
                if not kinds or (kinds != {"exact rule"} if wanted is None else wanted not in kinds):
                    continue
            matched.append(record)
            statuses.append((state, reason))
        # The folder tree lists the folders of every match; the list shows the models of the chosen folder.
        parents = folder_layout([self._record_path(record) for record in matched])
        counts: dict[str, int] = {}
        for parent in parents:
            folder = parent
            while folder:
                counts[folder] = counts.get(folder, 0) + 1
                folder = folder.rpartition("/")[0]
        if self.folder_scope and self.folder_scope not in counts:
            self.folder_scope = ""   # that folder holds no match any more
        scope = self.folder_scope
        self.filtered_records, rows = [], []
        for record, parent, (state, reason) in zip(matched, parents, statuses):
            if scope and parent != scope and not parent.startswith(scope + "/"):
                continue
            self.filtered_records.append(record)
            rows.append((record, state, reason))
        self._rebuilding = True
        try:
            self._fill_folder_tree(counts, len(matched), open_folders, bool(terms) or len(matched) <= 150)
            self.table.delete(*self.table.get_children())
            self.item_by_record.clear()
            self.item_by_path.clear()
            for number, (record, state, reason) in enumerate(rows):
                item = f"model-{number}"
                self.item_by_record[item] = record
                self.item_by_path[self._record_path(record)] = item
                self.table.insert("", "end", iid=item, text=Path(self._record_path(record)).stem, values=(
                    record.used_by if isinstance(record, PipelineModelCandidate) else "",
                    record.model_id if isinstance(record, PipelineModelCandidate) else record.relative_animset or "—",
                    state,
                    reason,
                ))
                if self._record_key(record) in self.selected_map:
                    self.table.selection_add(item)
        finally:
            self._rebuilding = False
        self.update_selection_label()
        self.start_button.configure(state="normal" if self.records and self.runner is None and not getattr(self, "scanning", False) and not self.browser.review else "disabled")
        where = f" in {scope}" if scope else ""
        self.progress_var.set(f"{len(self.records)} models found; {len(self.filtered_records)} visible{where}"
                              + (" (reading material links…)" if shading_filter != "All" and coverage is None else ""))
        self._gallery_changed()


    def show_list_view(self, kind: str) -> None:
        """List: the folder's models as rows. Grid: the same models as thumbnails. The folder tree stays on the left in both.
        The Texture type uses this same switch for its own list."""
        if self.browser.asset_type == "Texture":
            self.browser.archive_textures.show_list_view(kind)
        else:
            gallery = self.browser.gallery.frame
            if kind == "Grid":
                self.table_frame.grid_remove()
                gallery.grid(row=0, column=1, sticky="nsew")
            else:
                gallery.grid_remove()
                self.table_frame.grid(row=0, column=1, sticky="nsew")
            self.model_list_view = kind
        self.list_view_var.set(kind)

    def _build_folder_tree(self, parent) -> None:
        """The model folders, beside both List and Grid. Selecting one lists the models in it and below it."""
        ttk = self.ttk
        frame = self.folder_frame = ttk.Frame(parent)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = self.folder_tree = ttk.Treeview(frame, show="tree", selectmode="browse", height=10)
        tree.column("#0", width=210, stretch=True)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        tree.bind("<<TreeviewSelect>>", lambda _event: self._folder_selected())
        self._tip(HELP["folder_tree"], tree)

    def _folder_selected(self) -> None:
        if self._rebuilding:
            return
        selection = self.folder_tree.selection()
        iid = selection[0] if selection else "all"
        folder = "" if iid == "all" else iid[len("dir:"):]
        if folder == self.folder_scope:
            return
        self.folder_scope = folder
        self.refresh_table()


    def _gallery_changed(self) -> None:
        """The selection or the visible model list changed: the Gallery tab refreshes when shown."""
        gallery = getattr(getattr(self, "browser", None), "gallery", None)
        if gallery is not None:
            gallery.invalidate()

    def on_filter_changed(self) -> None:
        if self.browser.asset_type == "Texture":   # the shared Search filters the texture list
            self.model_filter_stale = True
            self.browser.archive_textures.shared_filter_changed()
            return
        # Typing a search returns the tree to automatic folding so matches are visible.
        if self.folder_mode != "auto":
            self.folder_mode = "auto"
            self.expand_button.configure(text="Expand all")
        self._debounce_refresh()

    def _cancel_pending_refresh(self) -> None:
        if self._refresh_after is not None:
            self.root.after_cancel(self._refresh_after)
            self._refresh_after = None

    def _debounce_refresh(self, delay_ms: int = 200) -> None:
        """Typing: restart the timer so only the last keystroke of a burst rebuilds the tree."""
        self._cancel_pending_refresh()
        self._refresh_after = self.root.after(delay_ms, self.refresh_table)

    def _coalesce_refresh(self, delay_ms: int = 200) -> None:
        """Batch progress: one rebuild per window however many models finish in it."""
        if self._refresh_after is None:
            self._refresh_after = self.root.after(delay_ms, self.refresh_table)

    def update_record_row(self, path: str, record: ModelCandidate | PipelineModelCandidate) -> None:
        """Refresh one model's row after its status changed; a coalesced full refresh if that cannot be done in place."""
        if self._refresh_after is not None:
            return  # a full refresh is already queued and will pick this up
        item = self.item_by_path.get(path)
        if (item is None or not self.table.exists(item) or self.state_var.get() != "All"
                or self.shading_var.get() != "All"):
            self._coalesce_refresh()
            return
        output = Path(self.output_var.get()).expanduser() if self.output_var.get().strip() else None
        state, reason = self._record_status(record, output)
        self.item_by_record[item] = record
        is_pipeline = isinstance(record, PipelineModelCandidate)
        self.table.item(item, values=(record.used_by if is_pipeline else "",
                                      record.model_id if is_pipeline else record.relative_animset or "—", state, reason))

    def toggle_expand_all(self) -> None:
        # Cycles expanded -> collapsed -> expanded; the first press from auto opens everything.
        self.folder_mode = "collapsed" if self.folder_mode == "expanded" else "expanded"
        self.expand_button.configure(text="Expand all" if self.folder_mode == "collapsed" else "Collapse all")
        self.refresh_table()


    def _fill_folder_tree(self, counts: dict[str, int], total: int, open_folders: set[str], expand: bool) -> None:
        """Folders of the matches, each count including its sub-folders; the chosen folder stays selected."""
        tree = self.folder_tree
        tree.delete(*tree.get_children())
        tree.insert("", "end", iid="all", text=f"All models  ({total})", open=True)
        for folder in sorted(counts, key=str.casefold):   # a parent path sorts before its children, so it exists when they are inserted
            parent = folder.rpartition("/")[0]
            name = folder.rpartition("/")[2]
            item = "dir:" + folder
            if self.folder_mode == "expanded":
                is_open = True
            elif self.folder_mode == "collapsed":
                is_open = False
            else:
                # chunk/bangle folders always hold a complete model, so they stay collapsed in auto mode.
                is_open = (expand or item in open_folders) and name.casefold() not in ALWAYS_COLLAPSED_FOLDERS
            tree.insert(f"dir:{parent}" if parent else "all", "end", iid=item, text=f"{name}  ({counts[folder]})", open=is_open)
        tree.selection_set("dir:" + self.folder_scope if self.folder_scope else "all")


    def _folder_items(self, parent: str = "all") -> list[str]:
        found = []
        if not self.folder_tree.exists(parent):   # the tree is empty until the first refresh
            return found
        for child in self.folder_tree.get_children(parent):
            if child.startswith("dir:"):
                found.append(child)
                found.extend(self._folder_items(child))
        return found

    @staticmethod
    def _record_path(record: ModelCandidate | PipelineModelCandidate) -> str:
        return record.archive_path if isinstance(record, PipelineModelCandidate) else record.relative_model.replace("\\", "/")


    def _selected_records(self) -> list[ModelCandidate | PipelineModelCandidate]:
        """The selected models. A selection survives moving to another folder or search, so it may hide models the list shows."""
        self._sync_selection()
        return list(self.selected_map.values())

    def _sync_selection(self) -> None:
        """Fold the table's selection into selected_map; models the list does not show keep their place in it."""
        if self._rebuilding or not hasattr(self, "table"):
            return
        visible = {self._record_key(record) for record in self.item_by_record.values()}
        chosen = {}
        for item in self.table.selection():
            record = self.item_by_record.get(item)
            if record is not None:
                chosen[self._record_key(record)] = record
        kept = {key: record for key, record in self.selected_map.items() if key not in visible}
        self.selected_map = {**kept, **chosen}

    def _table_click(self, event) -> str | None:
        """A plain click on a selected model deselects only that model; Ctrl and Shift clicks keep Tk's own rules."""
        if event.state & 0x0005:   # Shift (0x1) or Control (0x4)
            return None
        row = self.table.identify_row(event.y)
        if row in self.item_by_record and row in self.table.selection():
            self.table.selection_remove(row)
            return "break"   # stops Tk's default, which would select only this row and drop the others
        return None

    def _table_double_click(self, event) -> str | None:
        # Double-clicking a folder just opens/closes it (Tk's default).
        row = self.table.identify_row(event.y)
        if row in self.item_by_record:
            # The first click of a double-click may have deselected the model; it is selected again, with the others kept.
            self.table.selection_add(row)
            self._show_selected_details()
            return "break"
        return None


    def select_visible(self) -> None:
        self.table.selection_set(list(self.item_by_record))
        self._sync_selection()
        self.update_selection_label()


    def clear_selection(self) -> None:
        self.selected_map = {}
        self.table.selection_remove(self.table.selection())
        self.update_selection_label()

    def update_selection_label(self) -> None:
        selected = self._selected_records() if hasattr(self, "table") else []
        self.selection_var.set(f"{len(selected)} models selected")
        items = self.table.selection() if hasattr(self, "table") else ()
        if len(items) == 1 and items[0] in self.item_by_record and hasattr(self, "browser"):
            self.browser.select_asset(self.item_by_record[items[0]])
        self._gallery_changed()

    @staticmethod
    def _record_key(record: ModelCandidate | PipelineModelCandidate):
        return record.model_id if isinstance(record, PipelineModelCandidate) else record.model

    def _record_status(self, record: ModelCandidate | PipelineModelCandidate, output: Path | None):
        return self.record_status(record, output, self.exported_names)

    def _scan_exported(self) -> None:
        """Folder names in the output root that hold a portable export (export.json)."""
        names = set()
        text = self.output_var.get().strip()
        try:
            for entry in os.scandir(text) if text else ():
                if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "export.json")):
                    names.add(entry.name)
        except OSError:
            pass
        self.exported_names = names

    @staticmethod
    def record_status(record: ModelCandidate | PipelineModelCandidate, output: Path | None, exported: set[str] | None = None):
        if isinstance(record, PipelineModelCandidate):
            if exported is not None and record.export_name in exported:
                return "EXPORTED", "Exported to the output folder"
            if record.previous_status:
                return "PREVIOUS", "Prior run recorded; full cache hashes will be rechecked on run"
            return "READY", "Indexed model; no prior result"
        return status_for(record, output)

    def _show_selected_details(self) -> None:
        selected = self._selected_records()
        if not selected:
            return
        record = selected[0]
        output = Path(self.output_var.get()) if self.output_var.get().strip() else None
        state, reason = self._record_status(record, output)
        self.messagebox.showinfo(
            record.display_name,
            (f"Archive model:\n{record.archive_path}\n\nAsset ID: {record.model_id}" if isinstance(record, PipelineModelCandidate)
             else f"Model:\n{record.model}\n\nAnimset:\n{record.animset or 'not found'}") + f"\n\nState: {state}\n{reason}",
        )

    def start_batch(self) -> None:
        if self.runner is not None or (self.worker and self.worker.is_alive()) or self.browser.review:
            return
        selected = self._selected_records()
        if not selected:
            self.messagebox.showinfo("Selection", "Select one or more models first.")
            return
        output_text = self.output_var.get().strip()
        interpreter_text = self.interpreter_var.get().strip()
        if not output_text or not interpreter_text:
            self.messagebox.showerror("Configuration", "Choose an output root and Python interpreter first.")
            return
        output = Path(output_text)
        interpreter = Path(interpreter_text)
        try:
            ensure_disjoint(Path(self.source_var.get()), output)
        except ValueError as error:
            self.messagebox.showerror("Output root", str(error))
            return
        if not interpreter.is_file():
            self.messagebox.showerror("Python interpreter", f"File not found:\n{interpreter}")
            return
        pipeline_mode = self.backend_var.get() == "Full archive pipeline"
        blend_mode = pipeline_mode and self.blend_scene_var.get()
        export_mode = pipeline_mode and not blend_mode
        if export_mode:
            options = self.export_options()
            if not options.formats:
                self.messagebox.showinfo("Export formats", "Choose at least one export format under Options….")
                self.show_options()
                return
            needs_blender = [key for key in options.formats if key in ("fbx", "usd")]
            if needs_blender and not Path(options.blender or "\0").is_file():
                self.messagebox.showerror("Blender needed", "FBX and USD are converted by Blender. Set its "
                                          "location under Options…, or untick those formats.")
                self.show_options()
                return
        if blend_mode:   # checked before the clips are read, so a fix under Options… does not cost the picker's ticks
            blender = Path(self.blender_var.get().strip())
            if not blender.is_file():
                self.messagebox.showerror("Blender executable", f"File not found:\n{blender}")
                return
            group_name = self.group_name_var.get().strip() if self.group_blend_var.get() else ""
            if self.group_blend_var.get() and not group_name:
                self.messagebox.showerror("Group file name", "Type a name for the one-.blend file under Options….")
                self.show_options()
                return
        if not pipeline_mode and any(not isinstance(record, ModelCandidate) or record.animset is None for record in selected):
            self.messagebox.showerror("Selection", "One or more selected models have no exact same-directory animset. Deselect them or choose paired models.")
            return
        # Include animations: each model's clips are read first, and the user picks which to export.
        clip_choices: dict[str, tuple[str, ...]] = {}
        self.batch_clips = {}
        models = [r for r in selected if isinstance(r, PipelineModelCandidate)]
        reader_options = None
        if export_mode and options.animations == "all":
            reader_options = options
        elif blend_mode and self.export_animations_var.get() and self.export_skeleton_var.get():
            # The Blender scene reads the same clips as a glTF export, so its picker lists them the same way.
            # The Blender build reads its own clips, so the cutscene shots and borrowed animsets are not offered here.
            reader_options = replace(self.export_options(), formats=("glb",), animations="all", skeleton=True,
                                     cinematic=False, borrowed=False)
        if reader_options is not None:
            picked = self._choose_clips(models, output, reader_options)
            if picked is None:
                self._append_log("Export cancelled", "muted")
                return
            clip_choices = picked
        self._append_log(f"Batch started: {len(selected)} model(s)", "success")
        self._set_busy(running=True)
        self.progress_done = 0
        self.progress_total = len(selected)
        self.progressbar.configure(maximum=self.progress_total, value=0)
        self.progress_var.set(f"0/{len(selected)} models processed")
        if export_mode:
            self.runner = ExportBatchRunner(
                models, Path(self.source_var.get()), output, options,
                event=lambda kind, **values: self.events.put((kind, values)), clip_choices=clip_choices)
        elif pipeline_mode:
            self.runner = PipelineBatchRunner(
                [record.archive_path for record in models], Path(self.source_var.get()), output, interpreter, blender,
                event=lambda kind, **values: self.events.put((kind, values)),
                group_blend=group_name, blend_only=True, skeleton=self.export_skeleton_var.get(),
                animations=self.export_animations_var.get(),
                clip_choices={r.archive_path: clip_choices[r.model_id] for r in models if r.model_id in clip_choices})
        else:
            self.runner = BatchRunner(
                [record for record in selected if isinstance(record, ModelCandidate)], output, interpreter,
                parse_prefixes(self.prefix_var.get()), True,
                event=lambda kind, **values: self.events.put((kind, values)))
        self._fill_queue(selected)
        self.worker = threading.Thread(target=self._batch_worker, args=(self.runner,), daemon=True)
        self.worker.start()

    def _picker_threshold(self) -> int | None:
        """Clip count above which the animation picker opens; None when it is switched off under Options…"""
        if not self.picker_var.get():
            return None
        try:
            return max(0, int(self.picker_threshold_var.get().strip()))
        except ValueError:
            return CLIP_PICKER_THRESHOLD

    def _choose_clips(self, records, output: Path, options) -> dict[str, tuple[str, ...]] | None:
        """Read each model's animation clips, then let the user untick the ones to leave out. Returns the clips
        for each model whose choice is not every clip (a model left fully ticked, or with no clips, is absent),
        or None when cancelled. When no model has clips, the user is told why and asked whether to export without them.
        The picker opens only above the threshold of Options…; switched off, the clips are not read at all. It shows
        the ticks of an export that did not finish (``retained_clips``) for the models it lists again."""
        from rc2016.export.models import ModelExporter
        tk, ttk = self.tk, self.ttk
        reader = ModelExporter(Path(self.source_var.get()), output)
        no_index = ("This output folder has no usable archive index, which links models to their animation sets. "
                    "Press Scan archive with this output folder first, then export again.")
        threshold = self._picker_threshold()
        if threshold is None:   # every clip is exported, so nothing is read first
            if records and reader.animation_index() is None and not self.messagebox.askokcancel(
                    "No animations found", f"{no_index}\n\nExport without animations?", icon="warning", parent=self.root):
                return None
            return {}
        cancel = threading.Event()
        found = []   # (record, clip labels, clip notes) in the order the models were read
        errors = []  # (record, error) of the models that could not be read

        def read() -> None:
            for record in records:
                if cancel.is_set():
                    return
                try:
                    labels, notes = reader.clip_labels(record.model_id, record.archive_path, options, notes=True)
                except Exception as error:   # a model that cannot be read has no clips to choose from
                    labels, notes = [], []
                    errors.append((record, error))
                found.append((record, labels, notes))

        worker = threading.Thread(target=read, daemon=True)
        progress = tk.Toplevel(self.root)
        progress.withdraw()
        progress.title("Reading animations")
        progress.transient(self.root)
        progress.protocol("WM_DELETE_WINDOW", cancel.set)
        progress.columnconfigure(0, weight=1)
        status = tk.StringVar(value="")
        ttk.Label(progress, textvariable=status, padding=12).grid(row=0, column=0, sticky="w")
        ttk.Button(progress, text="Cancel", command=cancel.set).grid(row=1, column=0, sticky="e", padx=12, pady=(0, 12))

        def poll() -> None:
            status.set(f"Reading animations: {len(found)} of {len(records)} model(s)")
            if worker.is_alive():
                progress.after(100, poll)
            else:
                progress.destroy()

        worker.start()
        progress.deiconify()
        progress.update()   # mapped before the title bar is set, as in show_options
        apply_titlebar(progress)
        progress.grab_set()
        progress.after(0, poll)
        self.root.wait_window(progress)
        if cancel.is_set():
            return None
        for record, error in errors:   # the export still runs for them, with every clip it can read
            self._append_log(f"{record.display_name}: its animations could not be read for the picker: {error}", "error")
        listed = [entry for entry in found if entry[1]]
        if not listed:
            # Say why nothing is offered instead of exporting without animations unannounced.
            if records and reader.animation_index() is None:
                reason = no_index
            elif errors:
                reason = f"{errors[0][0].display_name} could not be read: {errors[0][1]}"
            else:
                reason = "None of the selected models has animation clips (static models have none)."
            if not self.messagebox.askokcancel("No animations found", f"{reason}\n\nExport without animations?",
                                               icon="warning", parent=self.root):
                return None
            return {}

        if not needs_picker(listed, threshold):
            return {}   # every model has few enough clips to export them all without asking
        previous, self.retained_clips = self.retained_clips, {}   # kept only until the next picker opens
        dialog = ClipPickerDialog(tk, ttk, self.root, listed, source_root=self.source_var.get(),
                                  index_root=reader.animation_index(), borrowed=options.borrowed,
                                  cinematic=options.cinematic, previous=previous)
        picked = dialog.show()
        if picked is not None:
            self.batch_clips = dialog.selection.ticked_labels()
        return picked

    def _batch_finished(self, succeeded: bool) -> None:
        """An export that finished without a failure deselects every model. Otherwise the selection stays, and the
        picker's ticks for its models are kept for the next picker, which shows them again."""
        if succeeded:
            self.retained_clips = {}
            self.clear_selection()
        elif self.batch_clips:
            self.retained_clips = dict(self.batch_clips)
        self.batch_clips = {}

    def _batch_worker(self, runner: BatchRunner) -> None:
        try:
            runner.run()
        except Exception as error:
            self.events.put(("batch_error", {"message": str(error)}))

    def cancel_batch(self) -> None:
        if self.runner:
            self._append_log("Cancellation requested…", "muted")
            self.runner.cancel()

    def _restore_window(self) -> None:
        """Last size and place, and whether it was maximised; the defaults on a first start or when the saved
        place is off the screen. Maximised is the default: the 3D view needs the room."""
        saved = _load_settings()
        match = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", saved.get("window.geometry", ""))
        on_screen = match and int(match[3]) < self.root.winfo_screenwidth() and int(match[4]) < self.root.winfo_screenheight()
        self.root.geometry(match.group(0) if on_screen else "1420x980")
        if saved.get("window.zoomed", "1") == "1":
            try:
                self.root.state("zoomed")
            except Exception:
                pass

    def _saved_split(self) -> float:
        """Fraction of the window width that the Browser's list pane had last time."""
        try:
            return min(0.9, max(0.1, float(_load_settings().get("window.split", "0.53"))))
        except ValueError:
            return 0.53

    def _remember_window(self) -> None:
        """Size, place, maximised state and the Browser split, restored at the next start."""
        zoomed = self.root.state() == "zoomed"
        values = {"window.zoomed": "1" if zoomed else "0"}
        if not zoomed:   # keep the last normal size while maximised
            values["window.geometry"] = self.root.geometry()
        width = self.root.winfo_width()
        if width > 1 and self.browser_split is not None:
            values["window.split"] = f"{self.browser_split.sashpos(0) / width:.4f}"
        _save_settings({**_load_settings(), **values})

    def _close(self) -> None:
        self._remember_settings()
        self._remember_window()
        self.browser.cancel_review()
        self.browser._close_clip_picker()   # the Animation list is a grabbed popup; release it first
        if self.browser.review or self.runner:
            self._append_log("Closing after the active extractor stops…", "muted")
            if self.runner:
                self.runner.cancel()
            self.root.after(100, self._close_when_idle)
        else:
            self.browser.close()
            self.root.destroy()

    def _close_when_idle(self) -> None:
        if self.runner or self.browser.review:
            self.root.after(100, self._close_when_idle)
        else:
            self.browser.close()
            self.root.destroy()

    PROGRESS_STEPS = 1000

    def _set_busy(self, scanning: bool = False, running: bool = False, measured: bool = False) -> None:
        self.scanning = scanning
        # The pipeline scan reports its progress; the loose-file scan cannot, so it gets a moving bar.
        self.progressbar.stop()
        if scanning and not measured:
            self.progressbar.configure(mode="indeterminate")
            self.progressbar.start(25)
        else:
            self.progressbar.configure(mode="determinate", maximum=self.PROGRESS_STEPS, value=0)
        if scanning or running or (hasattr(self, "browser") and self.browser.review):
            self.start_button.configure(state="disabled")
            self.cancel_button.configure(state="normal" if running else "disabled")
        else:
            self.cancel_button.configure(state="disabled")
            self.start_button.configure(state="normal" if self.records else "disabled")

    @staticmethod
    def _queue_label(status: str) -> str:
        """The queue row state for a model_done status."""
        if status == "cancelled":
            return "cancelled"
        if status in {"failed", "model_failed", "model_unsupported"}:
            return "failed"
        return "done"

    def _fill_queue(self, records) -> None:
        """One queue row per model the batch runs, all queued, in the order they run."""
        tree = self.queue_tree
        tree.delete(*tree.get_children())
        self._queue_rows = {}
        for number, record in enumerate(records):
            item = f"queue-{number}"
            self._queue_rows.setdefault(self._queue_key(self._record_path(record)), item)
            tree.insert("", "end", iid=item, text=record.display_name, values=("queued",), tags=("queued",))
        tree.heading("#0", text=f"Extracting {len(records)} model{'' if len(records) == 1 else 's'}")

    @staticmethod
    def _queue_key(path: str) -> str:
        return path.replace("\\", "/").casefold()

    def _queue_set(self, values: dict[str, object], state: str) -> None:
        """Mark the queue row of the model an event names (its candidate, or its archive path)."""
        candidate = values.get("candidate")
        path = self._record_path(candidate) if candidate is not None else str(values.get("model", ""))
        item = self._queue_rows.get(self._queue_key(path))
        if item is None and values.get("index"):   # an event without a known path: its place in the queue
            item = f"queue-{int(values['index']) - 1}"
        if item is not None and self.queue_tree.exists(item):
            self.queue_tree.item(item, values=(state,), tags=(state,))

    def _queue_finish(self, running: str) -> None:
        """The batch ended: a model still queued was not run; one still running has no result (shown as ``running``)."""
        self.queue_tree.heading("#0", text="Extraction queue")
        for item in self.queue_tree.get_children():
            state = self.queue_tree.set(item, "status")
            if state in ("queued", "running"):
                label = "cancelled" if state == "queued" else running
                self.queue_tree.item(item, values=(label,), tags=(label,))

    def _append_log(self, message: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", tag or "")
        # Keep long archive/proof sessions from accumulating unbounded Tk text.
        if int(self.log.index("end-1c").split(".")[0]) > 3000:
            self.log.delete("1.0", "500.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, values = self.events.get_nowait()
                try:
                    self._handle_event(kind, values)
                except Exception as error:   # one failed event must not stop the pump the later events need
                    traceback.print_exc()
                    self._append_log(f"Internal error while handling '{kind}': {type(error).__name__}: {error}", "error")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_event(self, kind: str, values: dict[str, object]) -> None:
        if kind == "scan_progress":
            if getattr(self, "scanning", False):
                fraction = float(values["fraction"])
                self.progressbar.configure(value=fraction * self.PROGRESS_STEPS)
                phase = ("Reading the archive files" if fraction < PipelineIndexScanner.CLI_START
                         else "Indexing the archive's models" if fraction < PipelineIndexScanner.CLI_END else "Reading the model list")
                self.progress_var.set(f"{phase}… {fraction:.0%}")
        elif kind == "scan_done":
            self.records = values["records"]  # type: ignore[assignment]
            # A selection kept from before the scan holds only models the new list still has, as its new records.
            current = {self._record_key(record): record for record in self.records}
            self.selected_map = {key: current[key] for key in self.selected_map if key in current}
            self._scan_exported()
            self._set_busy()
            self._remember_settings()
            self.refresh_table()
            if self.backend_var.get() == "Full archive pipeline":
                note = " (archive and index unchanged since the last scan; reused it)" if values.get("reused") else ""
                self._append_log(f"Pipeline index ready: {len(self.records)} model(s){note}", "success")
            else:
                paired = sum(isinstance(record, ModelCandidate) and record.animset is not None for record in self.records)
                self._append_log(f"Scan complete: {len(self.records)} model(s), {paired} exact animset pair(s)", "success")
        elif kind == "scan_error":
            self._set_busy()
            self.progress_var.set("Scan failed")
            self._append_log(str(values["message"]), "error")
            self.messagebox.showerror("Scan failed", str(values["message"]))
        elif kind == "batch_started":
            self.progress_done = 0
            self.progress_total = int(values["total"])
            self.progressbar.configure(maximum=max(1, self.progress_total), value=0)
            self._append_log(f"Running {values['total']} model(s)…", "muted")
        elif kind == "model_started":
            self._queue_set(values, "running")
            if "candidate" in values:
                candidate = values["candidate"]
                name = candidate.display_name
                path = candidate.relative_model
            else:
                path = str(values.get("model", ""))
                name = Path(path).stem
            self.progress_var.set(f"{values['index']}/{values['total']}: {name}")
            self._append_log(f"[{values['index']}/{values['total']}] {path}", "muted")
        elif kind == "log":
            self._append_log(str(values["message"]), "error" if values.get("level") == "error" else None)
        elif kind == "model_done":
            status = str(values["status"])
            self._queue_set(values, self._queue_label(status))
            if "candidate" in values:
                candidate = values["candidate"]
                name = candidate.display_name
            else:
                name = Path(str(values.get("model", ""))).stem
            tag = ("success" if status in {"completed", "verified", "delivered", "reused_verified_delivery", "exported", "skipped"}
                   else "error" if status in {"failed", "model_failed"} else "muted")
            self._append_log(f"{name}: {status} — {values['message']}", tag)
            if "candidate" not in values:
                model_path = str(values.get("model", ""))
                changed = None
                records = []
                for r in self.records:
                    if isinstance(r, PipelineModelCandidate) and r.archive_path == model_path:
                        r = changed = replace(r, previous_status=status)
                    records.append(r)
                self.records = records
                if status in {"exported", "skipped"}:
                    self._scan_exported()
                if changed is not None:
                    self.update_record_row(model_path, changed)
                else:
                    self._coalesce_refresh()
            else:
                self.update_record_row(self._record_path(candidate), candidate)
            self.progress_done += 1
            self.progressbar.configure(value=self.progress_done)
        elif kind == "batch_done":
            if "summary" in values:
                summary = values["summary"]
                failed = bool(summary["failed"])
                message = ", ".join(f"{key} {value}" for key, value in summary.items() if key != "cancelled" and value) or "nothing to do"
                cancelled = bool(summary["cancelled"])
            else:
                failed = bool(values.get("returncode")) and not bool(values.get("cancelled"))
                message = f"exit code {values.get('returncode')}"
                cancelled = bool(values.get("cancelled"))
            self._append_log(f"Batch finished: {message}", "error" if failed else "success")
            if failed and isinstance(self.runner, PipelineBatchRunner):
                self._append_log(f"The pipeline log and the Blender logs of the failed models are kept in "
                                 f"{self.runner.output_root / 'reports' / 'logs'}", "error")
            self._queue_finish("failed")
            self._batch_finished(not failed and not cancelled)
            self.runner = None
            self._set_busy()
            self.progress_var.set("Batch cancelled" if cancelled else "Batch finished with errors" if failed else "Batch complete")
            self.browser.reload()
        elif kind == "batch_error":
            self._append_log(str(values["message"]), "error")
            self._queue_finish("failed")
            self._batch_finished(False)
            self.runner = None
            self._set_busy()
            self.progress_var.set("Batch failed before completion")
            self.messagebox.showerror("Batch failed", str(values["message"]))

    def run(self) -> None:
        self.root.mainloop()

