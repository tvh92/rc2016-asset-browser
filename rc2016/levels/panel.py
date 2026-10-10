"""Level viewer panel: level list, zone toggles, view options and the instanced level viewport.

Shared by the standalone window (level_viewer.py) and the asset browser's "Level" asset type, where
the level list sits in the browser's left pane (``list_parent``). Archive
reads and decoding run on a background thread; Tk and GL are touched only from the Tk thread.
"""
from __future__ import annotations

from collections import Counter
import queue
import sys
import threading
import time

from rc2016.levels import scene as level_scene
from rc2016.levels.viewport import LevelViewport

DEFAULT_LEVEL = "levels/novalis/novalis.level"
HELP = ("Left drag look · right drag pan · WASD move · Q/E down/up · Shift faster · wheel dolly · "
        "Ctrl+wheel or [ ] speed · F overview · click a model to identify it, double click to open it")


def zone_names(zones):
    """{zone path: short label}; the file stem, with its folder when two zones share a stem."""
    stems = [level_scene.zone_label(z) for z in zones]
    names = {}
    for zone, stem in zip(zones, stems):
        if stems.count(stem) > 1:
            parent = zone.replace("\\", "/").rsplit("/", 2)[-2]
            stem = f"{stem} ({parent})"
        names[zone] = stem
    return names


def override_summary(overrides):
    """Status text for instance material overrides: instances changed, and override entries that
    were not applied (stale slot names, null or unlisted replacements, ambiguous or conflicting slots)."""
    if not overrides or not overrides.get("outcomes"):
        return ""
    outcomes = overrides["outcomes"]
    skipped = sum(n for k, n in outcomes.items() if k not in (level_scene.APPLIED, level_scene.SAME))
    return (f" · material overrides on {overrides['instances']:,} instances"
            + (f" ({skipped:,} entries not applied)" if skipped else ""))


def load_sky(archive, zones):
    """level_sky for the level's full zone list, with its dome and noise textures loaded, or None."""
    from rc2016.levels.sky import level_sky
    from rc2016.viewer.textures import load_texture
    sky = level_sky(archive, zones)
    if sky:
        sky["textures"] = {}
        for path in {p for dome in sky["domes"] for p in (dome["dome_texture"], dome["noise_texture"])}:
            try:
                sky["textures"][path] = load_texture(archive, path)
            except (ValueError, KeyError, OSError) as exc:
                sky["textures"][path] = None
                sky["notes"].append(f"{path}: {exc}")
    return sky


def sky_summary(sky):
    """Status-line text for a level_sky result."""
    if sky is None:
        return "no sky"
    parts = [f"{len(sky['domes'])} cloud dome{'s' * (len(sky['domes']) != 1)}"]
    if sky["unsupported"]:
        parts.append(f"{len(sky['unsupported'])} sky mesh{'es' * (len(sky['unsupported']) != 1)} not ported")
    parts.append("captured clear sky" if sky["clear_sky"] else "flat background")
    return "sky: " + ", ".join(parts)


def load_level(get_archive, level_path, out, cancel, zone_filter=None):
    """Background loader. Posts, in order: ('zones', paths), ('zone', scene summary) per zone,
    ('bounds', native bounds), ('sky', level_sky result or None) for a .level, ('model', path, geometry, hidden, [(zone, groups)]) per model with
    groups keyed by (mirrored, effective material changes), ('overrides', running totals) after each
    model, ('materials', {path: entry}) batches, then ('done', timings). Errors: ('error', what, message)
    or ('fatal', message)."""
    started = time.perf_counter()
    try:
        archive = get_archive()
        zones = level_scene.level_zone_paths(archive, level_path) if level_path.endswith(".level") else [level_path]
    except Exception as exc:
        out.put(("fatal", f"{type(exc).__name__}: {exc}"))
        return
    all_zones = zones
    if zone_filter:
        zones = [z for z in zones if level_scene.zone_label(z) in zone_filter or z in zone_filter]
    out.put(("zones", zones))
    models, points = {}, []
    for zone in zones:
        if cancel.is_set():
            return
        try:
            scene = level_scene.zone_scene(archive, zone)
        except Exception as exc:
            out.put(("error", zone, f"{type(exc).__name__}: {exc}"))
            continue
        out.put(("zone", dict(zone=zone, instances=scene["instances"], models=len(scene["models"]))))
        points.extend(scene["points"])
        for model in scene["models"]:
            entry = models.setdefault(model["path"], dict(hidden=model["hidden"], count=0, zones=[]))
            entry["count"] += model["count"]
            entry["zones"].append((zone, model["groups"]))
    out.put(("bounds", level_scene.core_bounds(points)))
    if level_path.endswith(".level"):
        try:
            out.put(("sky", load_sky(archive, all_zones)))
        except Exception as exc:
            out.put(("error", "sky", f"{type(exc).__name__}: {exc}"))
    geometries, overrides = [], dict(outcomes=Counter(), instances=0, models=set())
    for path, entry in sorted(models.items(), key=lambda item: -item[1]["count"]):
        if cancel.is_set():
            return
        try:
            geometry = level_scene.model_geometry(archive, path)
        except Exception as exc:
            out.put(("error", path, f"{type(exc).__name__}: {exc}"))
            continue
        zones, replacements = [], {}
        for zone, groups in entry["zones"]:
            groups, outcomes, changed = level_scene.effective_groups(geometry["materials"], groups)
            zones.append((zone, groups))
            overrides["outcomes"].update(outcomes)
            overrides["instances"] += changed
            for _flip, applied in groups:
                replacements.update((material, None) for _slot, material in applied)
        if replacements:
            overrides["models"].add(path)
        # Replacement materials load with the model's own (material_entries skips loaded paths).
        geometry["override_materials"] = [dict(path=material) for material in replacements]
        geometries.append(geometry)
        out.put(("model", path, geometry, entry["hidden"], zones))
        out.put(("overrides", dict(outcomes=dict(overrides["outcomes"]), instances=overrides["instances"],
                                   models=len(overrides["models"]))))
    geometry_seconds = time.perf_counter() - started
    done, cache = set(), {}
    for geometry in geometries:
        if cancel.is_set():
            return
        try:
            entries = level_scene.material_entries(archive, geometry["materials"] + geometry["override_materials"],
                                                   done, cache)
        except Exception as exc:
            out.put(("error", "materials", f"{type(exc).__name__}: {exc}"))
            continue
        done.update(entries)
        if entries:
            out.put(("materials", entries))
        # The GL side owns uploaded textures (deduplicated by path), so drop large decoded blocks.
        # Small ones stay: tint palettes are sampled again on the CPU by later materials.
        # Keep the other fields: the puddle atlas is cached under a tuple key, and later
        # materials need its own path (GL dedup) and 'scales'.
        for path, texture in cache.items():
            if texture and len(texture.get("pixels") or b"") > 65536:
                cache[path] = {k: v for k, v in texture.items() if k != "pixels"}
    out.put(("done", dict(geometry_seconds=geometry_seconds, total_seconds=time.perf_counter() - started,
                          models=len(geometries), materials=len(done),
                          material_overrides=dict(outcomes=dict(overrides["outcomes"]), instances=overrides["instances"],
                                                  models=len(overrides["models"])))))


class LevelPanel:
    """Tk panel; ``source_root`` is a callable returning the current extracted source root."""

    def __init__(self, parent, tk, ttk, source_root, level=None, mode="Shaded", zone_filter=None, on_done=None,
                 list_parent=None, on_open_model=None, material_overrides=True, sky=True):
        """``list_parent``: a separate frame for the level list (the asset browser's left pane);
        by default the list sits at the top of the panel's own side column (standalone window).
        ``on_open_model(model path)``: open a picked model elsewhere (the browser's Model tab)."""
        self.tk, self.ttk, self.source_root = tk, ttk, source_root
        self.zone_filter, self.on_done, self.on_open_model = zone_filter, on_done, on_open_model
        self.archive, self.archive_root, self.archive_lock = None, None, threading.Lock()
        self.events, self.cancel, self.generation = queue.Queue(), threading.Event(), 0
        self.zone_vars, self.zone_rows, self.progress = {}, {}, {}
        self.closed, self.loaded_level, self.levels = False, None, []
        self.frame = ttk.Panedwindow(parent, orient="horizontal")
        side = ttk.Frame(self.frame, padding=(4, 4, 8, 4))
        main = ttk.Frame(self.frame)
        self.frame.add(side, weight=0); self.frame.add(main, weight=1)
        side.columnconfigure(0, weight=1)
        self.level_var = tk.StringVar(value=level or DEFAULT_LEVEL)
        if list_parent is None:
            holder = ttk.Frame(side); holder.grid(row=0, column=0, rowspan=3, sticky="nsew", pady=(0, 8))
            self._build_level_list(holder, height=8)
        else:
            self._build_level_list(list_parent, height=20)
        options = ttk.Frame(side); options.grid(row=3, column=0, sticky="ew")
        ttk.Label(options, text="View").pack(side="left")
        self.mode_var = tk.StringVar(value=mode)
        box = ttk.Combobox(options, textvariable=self.mode_var, values=LevelViewport.MODES, state="readonly", width=17)
        box.pack(side="left", padx=4)
        box.bind("<<ComboboxSelected>>", lambda _e: self.view.set_mode(self.mode_var.get()))
        light = ttk.Frame(side); light.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.lighting_var = tk.StringVar(value=LevelViewport.LIGHTINGS[0])
        self.tonemap_var = tk.StringVar(value=LevelViewport.TONEMAPS[0])
        ttk.Label(light, text="Lighting").grid(row=0, column=0, sticky="w")
        lighting_box = ttk.Combobox(light, textvariable=self.lighting_var, values=LevelViewport.LIGHTINGS, state="readonly", width=12)
        lighting_box.grid(row=0, column=1, sticky="w", padx=4)
        lighting_box.bind("<<ComboboxSelected>>", lambda _e: self._lighting_changed())
        ttk.Label(light, text="Tonemap").grid(row=1, column=0, sticky="w", pady=(4, 0))
        tonemap_box = ttk.Combobox(light, textvariable=self.tonemap_var, values=LevelViewport.TONEMAPS, state="readonly", width=19)
        tonemap_box.grid(row=1, column=1, sticky="w", padx=4, pady=(4, 0))
        tonemap_box.bind("<<ComboboxSelected>>", lambda _e: self.view.set_lighting(tonemap=self.tonemap_var.get()))
        self.lighting_note = ttk.Label(light, text="", foreground="gray", wraplength=240)
        self.lighting_note.grid(row=2, column=0, columnspan=2, sticky="w")
        self.hidden_var = tk.BooleanVar(value=False)
        toggles = ttk.Frame(side); toggles.grid(row=5, column=0, sticky="w", pady=4)
        ttk.Checkbutton(toggles, text="Show collision/template models", variable=self.hidden_var,
                        command=lambda: self.view.set_show_hidden(self.hidden_var.get())).pack(anchor="w")
        self.overrides_var = tk.BooleanVar(value=material_overrides)
        ttk.Checkbutton(toggles, text="Instance material overrides", variable=self.overrides_var,
                        command=lambda: self.view.set_material_overrides(self.overrides_var.get())).pack(anchor="w")
        self.sky_var = tk.BooleanVar(value=sky)
        ttk.Checkbutton(toggles, text="Sky (atmosphere cloud dome)", variable=self.sky_var,
                        command=lambda: self.view.set_show_sky(self.sky_var.get())).pack(anchor="w")
        head = ttk.Frame(side); head.grid(row=6, column=0, sticky="ew", pady=(8, 2))
        ttk.Label(head, text="Zones").pack(side="left")
        ttk.Button(head, text="None", width=5, command=lambda: self._set_all(False)).pack(side="right")
        ttk.Button(head, text="All", width=5, command=lambda: self._set_all(True)).pack(side="right", padx=4)
        side.rowconfigure(7, weight=1)
        holder = ttk.Frame(side); holder.grid(row=7, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1); holder.rowconfigure(0, weight=1)
        self.zone_canvas = tk.Canvas(holder, highlightthickness=0, width=250)
        self.zone_canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.zone_canvas.yview); scroll.grid(row=0, column=1, sticky="ns")
        self.zone_canvas.configure(yscrollcommand=scroll.set)
        self.zone_list = ttk.Frame(self.zone_canvas)
        self.zone_canvas.create_window((0, 0), window=self.zone_list, anchor="nw")
        self.zone_list.bind("<Configure>", lambda _e: self.zone_canvas.configure(scrollregion=self.zone_canvas.bbox("all")))
        ttk.Label(side, text=HELP, wraplength=250, foreground="gray").grid(row=8, column=0, sticky="ew", pady=(8, 0))
        main.columnconfigure(0, weight=1); main.rowconfigure(0, weight=1)
        self.view = LevelViewport(main, width=420, height=300)
        self.view.grid(row=0, column=0, sticky="nsew")
        self.view.material_overrides = bool(material_overrides)
        self.view.show_sky = bool(sky)
        self.view.set_mode(mode)
        self.view.clear("Choose a level and click Load.")
        self.view.on_stats = self._show_stats
        self.view.on_pick = self._picked
        self.view.on_open = self._open_model
        self.view.on_lighting_changed = lambda: self.lighting_note.configure(text=self.view.lighting_note or "")
        self.status_var = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.status_var, anchor="w", padding=(4, 3)).grid(row=1, column=0, sticky="ew")
        picked = ttk.Frame(main); picked.grid(row=2, column=0, sticky="ew")
        picked.columnconfigure(0, weight=1)
        self.picked_var = tk.StringVar(value="Click a model in the view to identify it.")
        ttk.Label(picked, textvariable=self.picked_var, anchor="w", padding=(4, 0, 4, 3)).grid(row=0, column=0, sticky="ew")
        self.open_button = ttk.Button(picked, text="Open in Model tab", state="disabled",
                                      command=lambda: self._open_model(self.view.selected))
        if on_open_model:
            self.open_button.grid(row=0, column=1, padx=4, pady=(0, 3))
        self.frame.after(50, self._poll)

    def _build_level_list(self, parent, height):
        """Level list (selecting a row loads it) plus an entry for a typed .level/.zone path."""
        tk, ttk = self.tk, self.ttk
        parent.columnconfigure(0, weight=1); parent.rowconfigure(1, weight=1)
        head = ttk.Frame(parent); head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="Levels").pack(side="left")
        ttk.Button(head, text="List levels", command=self.list_levels).pack(side="right")
        box = ttk.Frame(parent); box.grid(row=1, column=0, sticky="nsew", pady=(4, 4))
        box.columnconfigure(0, weight=1); box.rowconfigure(0, weight=1)
        self.level_list = ttk.Treeview(box, show="tree", selectmode="browse", height=height)
        self.level_list.column("#0", width=230, stretch=True)
        self.level_list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.level_list.yview); scroll.grid(row=0, column=1, sticky="ns")
        self.level_list.configure(yscrollcommand=scroll.set)
        self.level_list.bind("<<TreeviewSelect>>", lambda _e: self._level_chosen())
        pick = ttk.Frame(parent); pick.grid(row=2, column=0, sticky="ew"); pick.columnconfigure(0, weight=1)
        self.level_entry = ttk.Entry(pick, textvariable=self.level_var, width=30)
        self.level_entry.grid(row=0, column=0, sticky="ew")
        self.level_entry.bind("<Return>", lambda _e: self.load())
        ttk.Button(pick, text="Load", width=6, command=self.load).grid(row=0, column=1, padx=(4, 0))

    def _fill_level_list(self, levels):
        self.levels = list(levels)
        tree = self.level_list
        tree.delete(*tree.get_children())
        for path in self.levels:
            tree.insert("", "end", iid=path, text=path)
        self._mark_level(self.loaded_level)

    def _mark_level(self, level):
        """Highlight the loaded level without reloading it (the select handler skips it)."""
        if level and self.level_list.exists(level):
            if tuple(self.level_list.selection()) != (level,):
                self.level_list.selection_set(level)
            self.level_list.see(level)
        elif self.level_list.selection():
            self.level_list.selection_remove(*self.level_list.selection())   # a typed path not in the list

    def _level_chosen(self):
        selection = self.level_list.selection()
        if selection and selection[0] != self.loaded_level:
            self.level_var.set(selection[0])
            self.load()

    # Archive ------------------------------------------------------------------------------
    def _archive(self, root):
        """The panel's own archive instance (not shared with other tabs' workers). Worker threads
        call this with a root read on the Tk thread: Tk variables are not thread-safe."""
        with self.archive_lock:
            if self.archive is None or self.archive_root != root:
                if not root:
                    raise ValueError("Set the extracted source root first.")
                self.archive, self.archive_root = level_scene.open_archive(root), root
            return self.archive

    def list_levels(self):
        root = str(self.source_root() or "")

        def work():   # untagged (None): a level load started meanwhile must not drop the list
            try:
                self.events.put((None, ("levels", level_scene.level_list(self._archive(root)))))
            except Exception as exc:
                self.events.put((None, ("list_error", f"{type(exc).__name__}: {exc}")))
        self.status_var.set("Reading the archive's level list…")
        threading.Thread(target=work, daemon=True).start()

    # Loading ------------------------------------------------------------------------------
    def load(self):
        level = self.level_var.get().strip()
        if not level:
            return
        self.cancel.set()
        self.cancel, self.generation = threading.Event(), self.generation + 1
        self.view.clear(f"Loading {level}…")
        for child in self.zone_list.winfo_children():
            child.destroy()
        self.zone_vars, self.zone_rows = {}, {}
        self.loaded_level = level
        self._mark_level(level)
        self.progress = dict(level=level, state="loading", models=0, errors=[], zones=0, zones_with_models=0,
                             instances=0, started=time.perf_counter())
        generation, cancel = self.generation, self.cancel
        relay = _Relay(self.events, generation)
        root = str(self.source_root() or "")
        threading.Thread(target=load_level, args=(lambda: self._archive(root), level, relay, cancel, self.zone_filter),
                         daemon=True).start()

    def _lighting_changed(self):
        if self.lighting_var.get() == "Shared HDRI":
            self.tonemap_var.set("Khronos PBR Neutral")
        self.view.set_lighting(self.lighting_var.get(), self.tonemap_var.get())

    def _picked(self, path):
        if not path:
            self.picked_var.set("Nothing under the pointer.")
            self.open_button.configure(state="disabled")
            return
        self.picked_var.set(f"{path} · {self.view.instance_count(path):,} visible instances (outlined)")
        self.open_button.configure(state="normal")

    def _open_model(self, path):
        if path and self.on_open_model:
            self.on_open_model(path)

    def _set_all(self, visible):
        for zone, var in self.zone_vars.items():
            if str(self.zone_rows[zone].cget("state")) != "disabled":
                var.set(visible)
                self.view.set_zone_visible(zone, visible)

    def _add_zone_rows(self, zones):
        names = zone_names(zones)
        for zone in zones:
            var = self.tk.BooleanVar(value=True)
            row = self.ttk.Checkbutton(self.zone_list, text=names[zone] + " …", variable=var,
                                       command=lambda z=zone, v=var: self.view.set_zone_visible(z, v.get()))
            row.pack(anchor="w")
            self.zone_vars[zone], self.zone_rows[zone] = var, row
            row.name = names[zone]

    def _poll(self):
        if self.closed:
            return
        deadline = time.perf_counter() + 0.03
        while time.perf_counter() < deadline:
            try:
                generation, item = self.events.get_nowait()
            except queue.Empty:
                break
            if generation is not None and generation != self.generation:
                continue   # a previous load or list request
            self._handle(item)
        self.frame.after(30, self._poll)

    def _handle(self, item):
        kind, p = item[0], self.progress
        if kind == "levels":
            self._fill_level_list(item[1])
            self.status_var.set(f"{len(item[1])} levels in the archive.")
            return
        elif kind == "list_error":
            self.status_var.set(item[1])
            return
        elif kind == "zones":
            self._add_zone_rows(item[1])
            p["zones"] = len(item[1])
        elif kind == "zone":
            summary = item[1]
            row = self.zone_rows.get(summary["zone"])
            if row is not None:
                if summary["models"]:
                    row.configure(text=f"{row.name} — {summary['instances']:,} placed")
                    p["zones_with_models"] += 1
                    p["instances"] += summary["instances"]
                else:
                    row.configure(text=f"{row.name} — no models", state="disabled")
        elif kind == "bounds":
            if item[1]:
                self.view.set_scene_bounds(item[1])
        elif kind == "model":
            _kind, path, geometry, hidden, zones = item
            self.view.add_model(path, geometry, hidden)
            for zone, groups in zones:
                self.view.add_instances(zone, path, groups)
            p["models"] += 1
        elif kind == "sky":
            self.view.set_sky(item[1])
            p["sky"] = sky_summary(item[1])
            for unsupported in (item[1] or {}).get("unsupported", ()):
                print("level viewer: sky:", unsupported["model"], "-", unsupported["reason"], file=sys.stderr)
        elif kind == "overrides":
            p["overrides"] = item[1]
        elif kind == "materials":
            self.view.add_materials(item[1])
        elif kind == "error":
            p.setdefault("errors", []).append(item[1:])
            print("level viewer:", *item[1:], file=sys.stderr)
            row = self.zone_rows.get(item[1])
            if row is not None:
                row.configure(text=f"{row.name} — unreadable", state="disabled")
        elif kind == "fatal":
            p["state"] = "failed"
            self.view.clear(item[1])
            self.status_var.set(item[1])
            return
        elif kind == "done":
            p["state"] = f"loaded in {item[1]['total_seconds']:.1f} s"
            p["done"] = item[1]
            if self.on_done:
                self.frame.after(300, lambda: self.on_done(self))
        self._show_stats(self.view.stats())

    def _show_stats(self, stats):
        p = self.progress
        if not p:
            return
        errors = f" · {len(p['errors'])} failed" if p.get("errors") else ""
        overrides = override_summary(p.get("overrides"))
        sky = f" · {p['sky']}" if p.get("sky") else ""
        self.status_var.set(
            f"{p['state']} · {p['zones_with_models']}/{p['zones']} zones with models · {p['models']} models{errors} · "
            f"{stats['materials']} materials · {stats['textures']} textures{overrides}{sky} · showing {stats['instances']:,} instances, "
            f"{stats['triangles']:,} triangles in {stats['draws']} draws · {stats['draw_ms']:.1f} ms/frame · "
            f"speed {stats['speed']:.1f}/s")

    def close(self):
        self.closed = True
        self.cancel.set()


class _Relay:
    """Tags a loader's events with its generation so a superseded load is ignored."""
    def __init__(self, events, generation):
        self.events, self.generation = events, generation

    def put(self, item):
        self.events.put((self.generation, item))
