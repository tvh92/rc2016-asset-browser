"""The main window (Tk presentation layer) of the asset browser."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import queue
import threading

from rc2016.app.batch import (
    BatchRunner, ExportBatchRunner, ModelCandidate, PipelineBatchRunner, PipelineIndexScanner,
    PipelineModelCandidate, discover_models, ensure_disjoint, folder_layout, parse_prefixes, status_for)
from rc2016.app.settings import _default_blender, _default_interpreter, _load_settings, _save_settings
from rc2016.app.theme import (
    UI_SCALES, _Tooltip, _configure_tk_runtime, apply_dark_theme, apply_ui_scale,
    themed_scrolled_text_class, windows_prefers_dark)


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
    "prefixes": "Transform-only mode: only extract clips whose names start with these words.",
    "skip": "Don't redo models that were already exported with the same options. Leave this on.",
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
        "FBX, USD and .blend are converted from the glTF by Blender, so they need Blender."),
    "full_pipeline": (
        "Instead of the portable export, run the research pipeline: a validated Blender scene with "
        "the RC PS4 game shader, shared lighting, every animation as an action, and many reports. "
        "Slow and needs Blender; most users want the normal export."),
    "blend_only": (
        "Keep only the finished .blend files and the index the viewer uses to find animations. After a fully "
        "successful run, the other reports, meshes, textures, caches and validation files are deleted. A later run "
        "rebuilds everything, because nothing is left to reuse."),
    "cancel": "Stop the running extraction. Finished models are kept.",
    "filter": (
        "Search by file name, folder, asset ID, or the actor/animset that uses the model "
        "(for example 'hydro harvester' or 'enm_'). Several words must all match.\n\n"
        "Numbered rc1 mobys (moby-1964 etc.) have no name in the game data; where a named "
        "actor uses one, it shows in the 'Used by' column and is searchable."),
    "tree": (
        "Models are grouped by their folder in the game. Click a folder's arrow to open it.\n\n"
        "Selecting a folder selects every model inside it for extraction."),
    "expand": (
        "Opens every folder in the list, or closes them all again.\n\n"
        "Without this, folders open by themselves for short lists and searches. "
        "chunk and bangle folders stay closed in that automatic mode."),
    "state": (
        "READY: not extracted yet\n"
        "EXPORTED: exported to the output folder\n"
        "VERIFIED: already extracted and checked\n"
        "STALE: extracted before, but out of date\n"
        "PREVIOUS: listed in an earlier run's report\n"
        "NO ANIMSET: no animation found for this model"),
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
        self.dark = windows_prefers_dark()
        if self.dark:
            apply_dark_theme(self.root, ttk)
        scale = _load_settings().get("ui_scale", "100%")
        self.ui_scale_var = tk.StringVar(value=scale if scale in UI_SCALES else "100%")
        apply_ui_scale(self.root, ttk, int(self.ui_scale_var.get().rstrip("%")))
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.geometry("1420x980")
        self.root.minsize(1180, 900)
        try:
            self.root.state("zoomed")   # open maximised: the 3D view needs the room
        except Exception:
            pass
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
        self.backend_var = tk.StringVar(value="Full archive pipeline")
        self.filter_var = tk.StringVar()
        self.folder_mode = "auto"  # "auto", "expanded" or "collapsed"; see toggle_expand_all
        self.state_var = tk.StringVar(value="All")
        self.shading_var = tk.StringVar(value="All")
        self.prefix_var = tk.StringVar()
        self.skip_verified_var = tk.BooleanVar(value=True)
        # Export options (Options… window), remembered in the settings file.
        from rc2016.export.models import FORMATS
        chosen = set((saved.get("export_formats") or "glb").split(","))
        self.export_format_vars = {key: tk.BooleanVar(value=key in chosen) for key in FORMATS}
        self.export_textures_var = tk.BooleanVar(value=saved.get("export_textures", "1") == "1")
        self.export_dds_var = tk.BooleanVar(value=saved.get("export_dds", "0") == "1")
        self.export_animations_var = tk.StringVar(value=saved.get("export_animations", "all") if saved.get("export_animations") in ("all", "none") else "all")
        self.export_lod_var = tk.StringVar(value=saved.get("export_lod", "Highest") if saved.get("export_lod") in ("Highest", "Lowest", "All") else "Highest")
        self.full_pipeline_var = tk.BooleanVar(value=False)
        self.blend_only_var = tk.BooleanVar(value=False)
        self.export_summary_var = tk.StringVar()
        self.exported_names: set[str] = set()
        self.selection_var = tk.StringVar(value="0 models selected")
        self.progress_var = tk.StringVar(value="Step 1: click Browse… next to 'Game folder' and pick your unpacked game folder (e.g. CUSA01073). Hover any field for help.")

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        sources = ttk.LabelFrame(self.root, text="Sources and output")
        sources.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        sources.columnconfigure(1, weight=1)
        self._path_row(sources, 0, "Game folder (source)", self.source_var, self._browse_source, HELP["source"])
        self._path_row(sources, 1, "Output folder", self.output_var, self._browse_output, HELP["output"])
        option_row = ttk.Frame(sources)
        option_row.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 6))
        options_button = ttk.Button(option_row, text="Options…", command=self.show_options)
        options_button.pack(side="left")
        ttk.Label(option_row, textvariable=self.export_summary_var, foreground="#9aa0a6" if self.dark else "#6b7280"
                  ).pack(side="left", padx=10)
        self._tip(HELP["options"], options_button)
        self._build_options_window()

        actions = ttk.Frame(self.root)
        actions.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        actions.columnconfigure(1, weight=1)
        scan_button = ttk.Button(actions, text="Scan archive", command=self.scan)
        scan_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Label(actions, textvariable=self.progress_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        # Sits in the gap between Scan archive and Extract selected; the 4 px right pad
        # matches Extract's left pad so the bar is centred between the two buttons.
        self.progressbar = ttk.Progressbar(actions, mode="determinate", maximum=1, value=0)
        self.progressbar.grid(row=0, column=1, sticky="new", padx=(0, 4))
        accent_bg, accent_active, accent_disabled = (("#3d6fb6", "#2f5a97", "#8ea6c9") if self.dark
                                                     else ("#2563eb", "#1d4ed8", "#c7d2fe"))
        self.start_button = tk.Button(actions, text="Extract selected", command=self.start_batch, state="disabled",
                                      font="AccentButtonFont", relief="flat", borderwidth=0, padx=18, pady=6,
                                      cursor="hand2", foreground="#ffffff", disabledforeground=accent_disabled,
                                      background=accent_bg, activebackground=accent_active,
                                      activeforeground="#ffffff")
        self.start_button.grid(row=0, column=2, padx=4)
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel_batch, state="disabled")
        self.cancel_button.grid(row=0, column=3, padx=(4, 0))
        self._tip(HELP["scan"], scan_button)
        self._tip(HELP["extract"], self.start_button)
        self._tip(HELP["cancel"], self.cancel_button)

        main = ttk.Panedwindow(self.root, orient="vertical")
        main.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        top = ttk.Frame(main)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        main.add(top, weight=6)

        # Asset type: each type has its own list (left) and viewer (right); see AssetBrowser.show_asset_type.
        type_row = ttk.Frame(top)
        type_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(type_row, text="Asset type").pack(side="left", padx=(0, 8))
        self.asset_type_var = tk.StringVar(value="Model")
        self.asset_type_buttons = {}
        for kind in ("Model", "Level", "Flash UI"):
            button = ttk.Radiobutton(type_row, text=kind, value=kind, variable=self.asset_type_var, style="Toolbutton",
                                     command=lambda: self.browser.show_asset_type(self.asset_type_var.get()))
            button.pack(side="left", padx=(0, 2))
            self.asset_type_buttons[kind] = button

        # Model view: search/filters above the model list | review notebook.
        model_view = ttk.Frame(top)
        model_view.grid(row=1, column=0, sticky="nsew")
        model_view.columnconfigure(0, weight=1)
        model_view.rowconfigure(1, weight=1)

        filters = ttk.Frame(model_view)
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        filters.columnconfigure(1, weight=1)
        filter_label = ttk.Label(filters, text="Search  ⓘ")
        filter_label.grid(row=0, column=0, padx=(0, 8))
        filter_entry = ttk.Entry(filters, textvariable=self.filter_var)
        filter_entry.grid(row=0, column=1, sticky="ew")
        self.filter_var.trace_add("write", lambda *_args: self.on_filter_changed())
        self.expand_button = ttk.Button(filters, text="Expand all", command=self.toggle_expand_all)
        self._tip(HELP["expand"], self.expand_button)
        self.expand_button.grid(row=0, column=2, padx=(8, 0))
        state_label = ttk.Label(filters, text="State")
        state_label.grid(row=0, column=3, padx=(14, 8))
        state = ttk.Combobox(filters, textvariable=self.state_var, state="readonly", values=("All", "READY", "EXPORTED", "VERIFIED", "STALE", "NO ANIMSET", "PREVIOUS"), width=14)
        state.grid(row=0, column=4)
        self._tip(HELP["filter"], filter_label, filter_entry)
        self._tip(HELP["state"], state_label, state)
        state.bind("<<ComboboxSelected>>", lambda _event: self.refresh_table())
        shading_label = ttk.Label(filters, text="Shading")
        shading_label.grid(row=0, column=5, padx=(14, 8))
        shading = ttk.Combobox(filters, textvariable=self.shading_var, state="readonly", values=("All", *SHADING_FILTERS), width=17)
        shading.grid(row=0, column=6)
        self._tip(HELP["shading"], shading_label, shading)
        shading.bind("<<ComboboxSelected>>", lambda _event: self.refresh_table())
        select_button = ttk.Button(filters, text="Select visible", command=self.select_visible)
        select_button.grid(row=0, column=7, padx=(14, 4))
        self._tip(HELP["tree"], select_button)
        ttk.Button(filters, text="Clear selection", command=self.clear_selection).grid(row=0, column=8)

        browser_split = ttk.Panedwindow(model_view, orient="horizontal")
        browser_split.grid(row=1, column=0, sticky="nsew")
        table_frame = ttk.Frame(browser_split)
        browser_split.add(table_frame, weight=3)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("model", "animset", "state", "details"), show="tree headings", selectmode="extended")
        self.table.heading("#0", text="Folder / model")
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
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.update_selection_label())
        self.table.bind("<Double-1>", self._table_double_click)

        from rc2016.app.browser import AssetBrowser
        self.browser = AssetBrowser(self, browser_split, type_host=top, model_view=model_view)
        self.root.after(200, lambda: browser_split.sashpos(0, int(self.root.winfo_width() * 0.53)))
        self.prefix_entry.configure(state="disabled")

        bottom = ttk.Frame(main)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)
        main.add(bottom, weight=1)
        self.log = self.ScrolledText(bottom, height=4, state="disabled", wrap="none", font="TkFixedFont")
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.tag_configure("error", foreground="#f87171" if self.dark else "#b91c1c")
        self.log.tag_configure("success", foreground="#34d399" if self.dark else "#047857")
        self.log.tag_configure("muted", foreground="#9aa0a6" if self.dark else "#6b7280")

        ttk.Label(self.root, textvariable=self.selection_var, anchor="w").grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))

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
        muted = "#9aa0a6" if self.dark else "#6b7280"

        formats = ttk.LabelFrame(window, text="Export formats  ⓘ", padding=8)
        formats.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        for number, (key, (label, _suffix, needs_blender)) in enumerate(FORMATS.items()):
            box = ttk.Checkbutton(formats, text=label + ("  (needs Blender)" if needs_blender else ""),
                                  variable=self.export_format_vars[key])
            box.grid(row=number % 3, column=number // 3, sticky="w", padx=(0, 18), pady=2)
            self._tip(HELP["formats"], box)

        include = ttk.LabelFrame(window, text="Include", padding=8)
        include.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        ttk.Checkbutton(include, text="Textures (embedded in .glb; PNG files in a textures folder otherwise)",
                        variable=self.export_textures_var).grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(include, text="Also the original game textures (.dds, unchanged)",
                        variable=self.export_dds_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(include, text="Animations").grid(row=2, column=0, sticky="w", pady=(6, 2))
        ttk.Radiobutton(include, text="All", value="all", variable=self.export_animations_var).grid(row=2, column=1, sticky="w", padx=8, pady=(6, 2))
        ttk.Radiobutton(include, text="None", value="none", variable=self.export_animations_var).grid(row=2, column=2, sticky="w", pady=(6, 2))
        ttk.Label(include, text="To export one clip, pick it under the 3D view and tick 'Extract only this clip'.",
                  foreground=muted).grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Label(include, text="Detail level (LOD)").grid(row=4, column=0, sticky="w", pady=(6, 2))
        ttk.Combobox(include, textvariable=self.export_lod_var, values=("Highest", "Lowest", "All"), state="readonly",
                     width=10).grid(row=4, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 2))
        skip = ttk.Checkbutton(include, text="Skip models already exported with these options", variable=self.skip_verified_var)
        skip.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._tip(HELP["skip"], skip)

        programs = ttk.LabelFrame(window, text="Programs", padding=8)
        programs.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        programs.columnconfigure(1, weight=1)
        self._path_row(programs, 0, "Python", self.interpreter_var, self._browse_interpreter, HELP["python"])
        self._path_row(programs, 1, "Blender (optional)", self.blender_var, self._browse_blender, HELP["blender"])

        advanced = ttk.LabelFrame(window, text="Advanced", padding=8)
        advanced.grid(row=3, column=0, sticky="ew", padx=12, pady=6)
        advanced.columnconfigure(1, weight=1)
        full = ttk.Checkbutton(advanced, text="Build the full research Blender scene instead (slow; needs Blender)",
                               variable=self.full_pipeline_var)
        full.grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        self._tip(HELP["full_pipeline"], full)
        self.blend_only_check = ttk.Checkbutton(advanced, text="Keep only the .blend files (delete the other output)",
                                                variable=self.blend_only_var)
        self.blend_only_check.grid(row=1, column=0, columnspan=3, sticky="w", padx=(22, 0), pady=2)
        self._tip(HELP["blend_only"], self.blend_only_check)
        backend_label = ttk.Label(advanced, text="Backend  ⓘ")
        backend_label.grid(row=2, column=0, sticky="w", pady=4)
        backend = ttk.Combobox(advanced, textvariable=self.backend_var, state="readonly",
                                values=("Transform only", "Full archive pipeline"), width=28)
        backend.grid(row=2, column=1, sticky="w", padx=6, pady=4)
        backend.bind("<<ComboboxSelected>>", lambda _event: self._backend_changed())
        self._tip(HELP["backend"], backend_label, backend)
        self.prefix_label = ttk.Label(advanced, text="Clip prefixes")
        self.prefix_label.grid(row=3, column=0, sticky="w", pady=4)
        self.prefix_entry = ttk.Entry(advanced, textvariable=self.prefix_var)
        self.prefix_entry.grid(row=3, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(advanced, text="transform only; comma-separated", foreground=muted).grid(row=3, column=2, sticky="w")
        self._tip(HELP["prefixes"], self.prefix_label, self.prefix_entry)

        display = ttk.Frame(window, padding=(12, 6))
        display.grid(row=4, column=0, sticky="ew")
        scale_label = ttk.Label(display, text="UI scale  ⓘ")
        scale_label.pack(side="left", padx=(0, 6))
        scale_box = ttk.Combobox(display, textvariable=self.ui_scale_var, values=UI_SCALES, state="readonly", width=6)
        scale_box.pack(side="left")
        scale_box.bind("<<ComboboxSelected>>", lambda _event: self._ui_scale_changed())
        self._tip(HELP["scale"], scale_label, scale_box)
        ttk.Button(display, text="Close", command=self._close_options).pack(side="right")

        for variable in (*self.export_format_vars.values(), self.export_textures_var, self.export_dds_var,
                         self.export_animations_var, self.export_lod_var, self.full_pipeline_var, self.blend_only_var,
                         self.backend_var):
            variable.trace_add("write", lambda *_args: self._options_changed())
        self._options_changed()

    def show_options(self) -> None:
        window = self.options_window
        window.deiconify()
        window.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - window.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + 60
        window.geometry(f"+{x}+{y}")
        window.lift()
        window.focus_set()

    def _close_options(self) -> None:
        self.options_window.withdraw()
        self._remember_settings()

    def _export_formats(self) -> tuple[str, ...]:
        return tuple(key for key, variable in self.export_format_vars.items() if variable.get())

    def _options_changed(self) -> None:
        full_research = self.backend_var.get() == "Full archive pipeline" and self.full_pipeline_var.get()
        self.blend_only_check.configure(state="normal" if full_research else "disabled")
        if self.backend_var.get() != "Full archive pipeline":
            text = "Transform only: animation data from loose files"
        elif self.full_pipeline_var.get():
            text = "Full research Blender scene (needs Blender)" + (", .blend files only" if self.blend_only_var.get() else "")
        else:
            from rc2016.export.models import FORMATS
            formats = [FORMATS[key][1] for key in self._export_formats()] or ["no format chosen"]
            parts = [", ".join(formats)]
            parts.append("textures" if self.export_textures_var.get() else "no textures")
            parts.append("all animations" if self.export_animations_var.get() == "all" else "no animations")
            if self.export_lod_var.get() != "Highest":
                parts.append(f"{self.export_lod_var.get()} LOD")
            text = "Export: " + " · ".join(parts)
        self.export_summary_var.set(text)

    def export_options(self):
        from rc2016.export.models import ExportOptions
        return ExportOptions(formats=self._export_formats(), textures=self.export_textures_var.get(),
                             source_textures=self.export_dds_var.get(), animations=self.export_animations_var.get(),
                             lod=self.export_lod_var.get(), blender=self.blender_var.get().strip(),
                             skip_existing=self.skip_verified_var.get())

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

    def _remember_settings(self) -> None:
        _save_settings({"source": self.source_var.get().strip(), "output": self.output_var.get().strip(),
                        "python": self.interpreter_var.get().strip(), "blender": self.blender_var.get().strip(),
                        "ui_scale": self.ui_scale_var.get(), "export_formats": ",".join(self._export_formats()),
                        "export_textures": "1" if self.export_textures_var.get() else "0",
                        "export_dds": "1" if self.export_dds_var.get() else "0",
                        "export_animations": self.export_animations_var.get(), "export_lod": self.export_lod_var.get()})

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

    def _backend_changed(self) -> None:
        self.state_var.set("All")
        self.records = []
        widget_state = "disabled" if self.backend_var.get() == "Full archive pipeline" else "normal"
        self.prefix_entry.configure(state=widget_state)
        self.prefix_label.configure(foreground="#9ca3af" if widget_state == "disabled" else "")
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
            self._set_busy(scanning=True)
            self.progress_var.set("Building pipeline archive index…")
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
            records = scanner.scan()
            self.events.put(("scan_done", {"records": records, "reused": getattr(scanner, "reused", False)}))
        except Exception as error:
            self.events.put(("scan_error", {"message": str(error)}))

    def refresh_table(self) -> None:
        source = Path(self.source_var.get()).expanduser() if self.source_var.get().strip() else None
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
        selected_models = {self._record_key(record) for record in self._selected_records()} if hasattr(self, "table") else set()
        open_folders = {item for item in self._folder_items() if self.table.item(item, "open")} if hasattr(self, "table") else set()
        terms = needle.split()
        self.filtered_records = []
        self._cancel_pending_refresh()
        statuses: list[tuple[str, str]] = []
        for record in self.records:
            is_pipeline = isinstance(record, PipelineModelCandidate)
            relative_model = record.archive_path if is_pipeline else record.relative_model
            haystack = (relative_model + (" " + record.model_id + " " + record.search_text if is_pipeline
                        else " " + (record.relative_animset or ""))).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            state, _reason = self._record_status(record, output)   # once per record; reused for the row
            if state_filter != "All" and state != state_filter:
                continue
            if shading_filter != "All":
                kinds = (coverage or {}).get(getattr(record, "archive_path", None) or "")
                wanted = SHADING_FILTERS[shading_filter]
                if not kinds or (kinds != {"exact rule"} if wanted is None else wanted not in kinds):
                    continue
            self.filtered_records.append(record)
            statuses.append((state, _reason))
        for child in self.table.get_children():
            self.table.delete(child)
        self.item_by_record.clear()
        self.item_by_path.clear()
        paths = [self._record_path(record) for record in self.filtered_records]
        parents = folder_layout(paths)
        # Open everything for a search or a short list; otherwise keep what was open.
        expand = bool(terms) or len(self.filtered_records) <= 150
        if self.folder_mode != "auto":
            open_folders = set()
        counts: dict[str, int] = {}
        for parent in parents:
            folder = parent
            while folder:
                counts[folder] = counts.get(folder, 0) + 1
                folder = folder.rpartition("/")[0]
        for number, (record, parent, (state, reason)) in enumerate(zip(self.filtered_records, parents, statuses)):
            item = f"model-{number}"
            self.item_by_record[item] = record
            self.item_by_path[self._record_path(record)] = item
            self.table.insert(self._ensure_folder(parent, counts, open_folders, expand), "end", iid=item,
                              text=Path(self._record_path(record)).stem, values=(
                record.used_by if isinstance(record, PipelineModelCandidate) else "",
                record.model_id if isinstance(record, PipelineModelCandidate) else record.relative_animset or "—",
                state,
                reason,
            ))
            if self._record_key(record) in selected_models:
                self.table.selection_add(item)
        self.update_selection_label()
        self.start_button.configure(state="normal" if self.records and self.runner is None and not getattr(self, "scanning", False) and not self.browser.review else "disabled")
        self.progress_var.set(f"{len(self.records)} models found; {len(self.filtered_records)} visible"
                              + (" (reading material links…)" if shading_filter != "All" and coverage is None else ""))
        self._gallery_changed()

    def _gallery_changed(self) -> None:
        """The selection or the visible model list changed: the Gallery tab refreshes when shown."""
        gallery = getattr(getattr(self, "browser", None), "gallery", None)
        if gallery is not None:
            gallery.invalidate()

    def on_filter_changed(self) -> None:
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

    def _ensure_folder(self, folder: str, counts: dict[str, int], open_folders: set[str], expand: bool) -> str:
        if not folder:
            return ""
        item = "dir:" + folder
        if not self.table.exists(item):
            parent = self._ensure_folder(folder.rpartition("/")[0], counts, open_folders, expand)
            name = folder.rpartition('/')[2]
            if self.folder_mode == "expanded":
                is_open = True
            elif self.folder_mode == "collapsed":
                is_open = False
            else:
                # chunk/bangle folders always hold a complete model, so they stay collapsed in auto mode.
                is_open = (expand or item in open_folders) and name.casefold() not in ALWAYS_COLLAPSED_FOLDERS
            self.table.insert(parent, "end", iid=item, text=f"{name}  ({counts.get(folder, 0)})",
                              open=is_open, tags=("folder",))
        return item

    def _folder_items(self, parent: str = "") -> list[str]:
        found = []
        for child in self.table.get_children(parent):
            if child.startswith("dir:"):
                found.append(child)
                found.extend(self._folder_items(child))
        return found

    @staticmethod
    def _record_path(record: ModelCandidate | PipelineModelCandidate) -> str:
        return record.archive_path if isinstance(record, PipelineModelCandidate) else record.relative_model.replace("\\", "/")

    def _selected_records(self) -> list[ModelCandidate | PipelineModelCandidate]:
        """Selected models; a selected folder stands for every model inside it."""

        records, seen = [], set()

        def add(item: str) -> None:
            if item in self.item_by_record:
                if item not in seen:
                    seen.add(item)
                    records.append(self.item_by_record[item])
            else:
                for child in self.table.get_children(item):
                    add(child)

        for item in self.table.selection():
            add(item)
        return records

    def _table_double_click(self, event) -> None:
        # Double-clicking a folder just opens/closes it (Tk's default).
        if self.table.identify_row(event.y) in self.item_by_record:
            self._show_selected_details()

    def select_visible(self) -> None:
        self.table.selection_set(list(self.item_by_record))
        self.update_selection_label()

    def clear_selection(self) -> None:
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
        export_mode = pipeline_mode and not self.full_pipeline_var.get()
        if export_mode:
            options = self.export_options()
            if not options.formats:
                self.messagebox.showinfo("Export formats", "Choose at least one export format under Options….")
                self.show_options()
                return
            needs_blender = [key for key in options.formats if key in ("fbx", "usd", "blend")]
            if needs_blender and not Path(options.blender or "\0").is_file():
                self.messagebox.showerror("Blender needed", "FBX, USD and .blend are converted by Blender. Set its "
                                          "location under Options…, or untick those formats.")
                self.show_options()
                return
        clip_only = self.browser.export_clip() if pipeline_mode else None
        if clip_only and not any(getattr(r, "model_id", None) == clip_only[0] for r in selected):
            clip_only = None
        if clip_only:
            self._append_log(f"Only clip '{clip_only[1]}' for the model shown in the 3D view", "muted")
        if not pipeline_mode and any(not isinstance(record, ModelCandidate) or record.animset is None for record in selected):
            self.messagebox.showerror("Selection", "One or more selected models have no exact same-directory animset. Deselect them or choose paired models.")
            return
        self._append_log(f"Batch started: {len(selected)} model(s)", "success")
        self._set_busy(running=True)
        self.progress_done = 0
        self.progress_total = len(selected)
        self.progressbar.configure(maximum=self.progress_total, value=0)
        self.progress_var.set(f"0/{len(selected)} models processed")
        if export_mode:
            self.runner = ExportBatchRunner(
                [record for record in selected if isinstance(record, PipelineModelCandidate)],
                Path(self.source_var.get()), output, options, clip_only,
                event=lambda kind, **values: self.events.put((kind, values)))
        elif pipeline_mode:
            blender = Path(self.blender_var.get().strip())
            if not blender.is_file():
                self.messagebox.showerror("Blender executable", f"File not found:\n{blender}")
                self._set_busy()
                self.runner = None
                return
            selectors = [record.archive_path for record in selected if isinstance(record, PipelineModelCandidate)]
            research_clip = None
            if clip_only:
                path = next(r.archive_path for r in selected if getattr(r, "model_id", None) == clip_only[0])
                research_clip = (path, clip_only[1])
            self.runner = PipelineBatchRunner(
                selectors, Path(self.source_var.get()), output, interpreter, blender,
                skip_verified=self.skip_verified_var.get(), blend_only=self.blend_only_var.get(),
                event=lambda kind, **values: self.events.put((kind, values)), clip_only=research_clip)
        else:
            self.runner = BatchRunner(
                [record for record in selected if isinstance(record, ModelCandidate)], output, interpreter,
                parse_prefixes(self.prefix_var.get()), self.skip_verified_var.get(),
                event=lambda kind, **values: self.events.put((kind, values)))
        self.worker = threading.Thread(target=self._batch_worker, args=(self.runner,), daemon=True)
        self.worker.start()

    def _batch_worker(self, runner: BatchRunner) -> None:
        try:
            runner.run()
        except Exception as error:
            self.events.put(("batch_error", {"message": str(error)}))

    def cancel_batch(self) -> None:
        if self.runner:
            self._append_log("Cancellation requested…", "muted")
            self.runner.cancel()

    def _close(self) -> None:
        self.browser.cancel_review()
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

    def _set_busy(self, scanning: bool = False, running: bool = False) -> None:
        self.scanning = scanning
        # A scan has no known length, so show a moving bar; batches count models.
        if scanning:
            self.progressbar.configure(mode="indeterminate")
            self.progressbar.start(12)
        else:
            self.progressbar.stop()
            self.progressbar.configure(mode="determinate")
        if scanning or running or (hasattr(self, "browser") and self.browser.review):
            self.start_button.configure(state="disabled")
            self.cancel_button.configure(state="normal" if running else "disabled")
        else:
            self.cancel_button.configure(state="disabled")
            self.start_button.configure(state="normal" if self.records else "disabled")

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
                self._handle_event(kind, values)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_event(self, kind: str, values: dict[str, object]) -> None:
        if kind == "scan_done":
            self.records = values["records"]  # type: ignore[assignment]
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
            self.runner = None
            self._set_busy()
            self.progress_var.set("Batch cancelled" if cancelled else "Batch complete")
            self.browser.reload()
        elif kind == "batch_error":
            self._append_log(str(values["message"]), "error")
            self.runner = None
            self._set_busy()
            self.progress_var.set("Batch failed before completion")
            self.messagebox.showerror("Batch failed", str(values["message"]))

    def run(self) -> None:
        self.root.mainloop()

