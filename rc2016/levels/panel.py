"""Level viewer panel: level list, zone toggles, view options and the instanced level viewport.

Shared by the standalone window (level_viewer.py) and the asset browser's "Level" asset type, where
the level list sits in the browser's left pane (``list_parent``). Archive
reads and decoding run on a background thread; Tk and GL are touched only from the Tk thread.
"""
from __future__ import annotations

from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
import os
import queue
import sys
import threading
import time

from rc2016.app.layout import Splitter, regrid
from rc2016.app.theme import track
from rc2016.common import remember_var
from rc2016.levels import scene as level_scene
from rc2016.levels.viewport import BOOST_RANGE, LevelViewport
from rc2016.viewer.viewport import GL_TEXTURE_ROLES

DEFAULT_LEVEL = "levels/novalis/novalis.level"
HELP = ("Left drag look · right drag pan · WASD move · Q/E down/up · Shift faster (wheel sets how much) · "
        "Ctrl slower (10%) · [ ] speed · Shift+Tab hide sidebar · F11 full screen · F overview · click a model to identify it, right click to clear it, double click to open it")


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


# Zone name prefix -> (display order, group title); anything else falls under "Other".
ZONE_GROUPS = {"art": (0, "Art"), "gp": (1, "Gameplay"), "lgt": (2, "Lighting & atmosphere"), "lighting": (2, "Lighting & atmosphere"),
               "atm": (2, "Lighting & atmosphere"), "atmosphere": (2, "Lighting & atmosphere"), "vfx": (3, "Effects"), "fx": (3, "Effects"),
               "neb": (3, "Effects"), "sound": (4, "Audio"), "music": (4, "Audio"), "nav": (5, "Navigation")}
OTHER_GROUP = (6, "Other")


def zone_group(label):
    """(order, title) of the group a zone label belongs to, from its prefix (`art_terrain` -> Art)."""
    base = label[:-len("_imposter")] if label.endswith("_imposter") else label
    return ZONE_GROUPS.get(base.split("_", 1)[0].lower(), OTHER_GROUP)


def zone_order(zones):
    """[(group title, [zone path])] with groups in a fixed logical order; inside a group the zones are
    alphabetical, each followed by its `_imposter` twin."""
    names = zone_names(zones)

    def key(zone):
        label = names[zone].lower()
        base = label.replace("_imposter", "")
        return (base, label != base, label)
    groups = {}
    for zone in sorted(zones, key=key):
        groups.setdefault(zone_group(level_scene.zone_label(zone)), []).append(zone)
    return [(title, members) for (_order, title), members in sorted(groups.items())]


def override_summary(overrides):
    """Status text for instance material overrides: instances changed, and override entries that
    were not applied (stale slot names, null or unlisted replacements, ambiguous or conflicting slots)."""
    if not overrides or not overrides.get("outcomes"):
        return ""
    outcomes = overrides["outcomes"]
    skipped = sum(n for k, n in outcomes.items() if k not in (level_scene.APPLIED, level_scene.SAME))
    return (f" · material overrides on {overrides['instances']:,} instances"
            + (f" ({skipped:,} entries not applied)" if skipped else ""))


def load_sky(archive, zones, probe_position=None):
    """level_sky for the level's full zone list, with its dome and noise textures loaded, or None.
    ``probe_position`` (native Y-up) selects the reflection volume that contains it (``sky.level_sky``)."""
    from rc2016.levels.sky import level_sky
    from rc2016.viewer.textures import load_texture
    sky = level_sky(archive, zones, probe_position=probe_position)
    if sky:
        sky["textures"] = {}
        for path in {p for dome in sky["domes"] for p in (dome["dome_texture"], dome["noise_texture"])}:
            try:
                sky["textures"][path] = load_texture(archive, path, hd=True)
            except (ValueError, KeyError, OSError) as exc:
                sky["textures"][path] = None
                sky["notes"].append(f"{path}: {exc}")
        if sky.get("reflection"):
            def environment_loader():
                from rc2016.levels.probe_lighting import level_environment
                maps = level_environment(archive, sky)
                try:
                    from rc2016.levels.lightgrid import level_lightgrid
                    maps["lightgrid"] = level_lightgrid(archive, zones, night=sky.get("night", False))   # None without a lighting zone
                except Exception as exc:   # keep the single-probe ambient and say why
                    maps["lightgrid"] = None
                    print(f"lightgrid unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
                return maps
            sky["environment_loader"] = environment_loader   # Level atmosphere lighting, built on demand
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


def drop_uploaded_pixels(cache, entries, uploaded):
    """Drop the decoded blocks of large cached textures that the GL side now holds.

    ``uploaded`` accumulates the texture paths posted in GL_TEXTURE_ROLES; the GL side uploads each
    once per path, so later materials reusing one need only its other fields (the puddle atlas is
    cached under a tuple key and later materials need its path and 'scales'). Textures so far only
    sampled on the CPU (RockBlend layers, puddle sources) keep their pixels, because a later
    material may still upload one under a GL role; material_textures reloads any dropped source it
    samples. 'slices' goes too: its first slice is the same block as 'pixels'.
    """
    uploaded.update(texture["path"] for entry in entries.values() for role in GL_TEXTURE_ROLES
                    if (texture := entry.get(role)))
    # Native uploads have separate sRGB/linear GL cache keys. Posting one
    # does not prove the raw path-keyed object exists for a later hand material.
    # Keep that raw source until a GL_TEXTURE_ROLES entry has also been posted.
    for key, texture in list(cache.items()):   # loader threads may add entries meanwhile
        if texture and texture.get("path") in uploaded and len(texture.get("pixels") or b"") > 65536:
            cache[key] = {k: v for k, v in texture.items() if k not in ("pixels", "slices")}


def restore_native_pixels(archive, entries, posted):
    """Give native and experimental textures their pixels where the GL side has not uploaded them yet.

    The GL side uploads each (path, sRGB variant) once and then never reads that texture's pixels
    again. Material workers hand over cached textures whose pixels drop_uploaded_pixels may have
    dropped, so only a variant first posted here is reloaded: once per variant, not once per
    material binding. ``posted`` accumulates the variants, in posting order."""
    from rc2016.viewer.textures import load_texture
    reloaded = {}
    for entry in entries.values():
        for receipt_key, texture_key in (("native_translation", "native_textures"),
                                         ("experimental_preview", "experimental_textures"),
                                         ("graph_rule_surface", "graph_rule_textures")):
            receipt, textures = entry.get(receipt_key), entry.get(texture_key)
            if not receipt or not textures:
                continue
            normal_api = (receipt.get("normal") or {}).get("api")
            for api, texture in list(textures.items()):
                variant = (texture["path"], bool(texture.get("srgb")) and api != normal_api)
                if variant in posted:
                    continue
                posted.add(variant)
                if "pixels" not in texture:
                    path = texture["path"]
                    if path not in reloaded:
                        reloaded[path] = load_texture(archive, path, hd=True)
                    textures[api] = reloaded[path]


# Worker threads for model geometry and materials. Archive reads keep per-thread handles; NumPy, zlib
# and file reads release the GIL, so a few workers overlap decoding with I/O.
LOAD_WORKERS = max(2, min(6, (os.cpu_count() or 2) - 1))


def ordered_results(pool, items, function, window, cancel=None):
    """Yield (item, future) in ``items`` order, keeping at most ``window`` calls submitted ahead, so
    work queued behind them (material jobs) starts early instead of after every item."""
    items, pending = iter(items), deque()
    for item in items:
        pending.append((item, pool.submit(function, item)))
        if len(pending) >= window:
            break
    while pending:
        if cancel is not None and cancel.is_set():
            return
        item, future = pending.popleft()
        following = next(items, _END)
        if following is not _END:
            pending.append((following, pool.submit(function, following)))
        yield item, future


_END = object()


class MaterialJobs:
    """Material and texture loading on the pool, posted in submission order. Each material path is
    loaded once (``claimed``); ``done`` holds the paths whose entries were posted."""

    def __init__(self, archive, pool, out):
        self.archive, self.pool, self.out = archive, pool, out
        self.claimed, self.done, self.cache, self.uploaded, self.jobs = set(), set(), {}, set(), deque()
        self.native_posted = set()

    def submit(self, geometry):
        todo = [m for m in geometry["materials"] + geometry.get("override_materials", [])
                if m.get("path") and m["path"] not in self.claimed]
        if todo:
            self.claimed.update(m["path"] for m in todo)
            self.jobs.append(self.pool.submit(level_scene.material_entries, self.archive, todo, frozenset(), self.cache))

    def post(self, wait=False, cancel=None):
        """Post finished jobs from the front of the queue; with ``wait``, all of them."""
        while self.jobs and (wait or self.jobs[0].done()):
            if cancel is not None and cancel.is_set():
                return
            job = self.jobs.popleft()
            try:
                entries = job.result()
            except Exception as exc:
                self.out.put(("error", "materials", f"{type(exc).__name__}: {exc}"))
                continue
            self.done.update(entries)
            try:
                restore_native_pixels(self.archive, entries, self.native_posted)
            except Exception as exc:
                self.out.put(("error", "materials", f"{type(exc).__name__}: {exc}"))
            if entries:
                self.out.put(("materials", entries))
            drop_uploaded_pixels(self.cache, entries, self.uploaded)


def load_level(get_archive, level_path, out, cancel, zone_filter=None, workers=None, probe_position=None):
    """Background loader. Posts, in order: ('zones', paths), ('zone', scene summary) per zone,
    ('bounds', native bounds), ('sky', level_sky result or None) for a .level, ('model', path, geometry, hidden, [(zone, groups)]) per model with
    groups keyed by (mirrored, effective material changes), ('overrides', running totals) after each
    model, ('materials', {path: entry}) batches, then ('done', timings). Errors: ('error', what, message)
    or ('fatal', message).

    Geometry decodes on a small thread pool, most-placed model first; each model's materials are
    queued right after its geometry, so the view becomes textured progressively."""
    started = time.perf_counter()
    try:
        archive = get_archive()
        disk = getattr(archive, "translation_disk_cache", None)
        translations_before = disk.stats() if disk is not None and hasattr(disk, "stats") else None
        zones = level_scene.level_zone_paths(archive, level_path) if level_path.endswith(".level") else [level_path]
    except Exception as exc:
        out.put(("fatal", f"{type(exc).__name__}: {exc}"))
        return
    all_zones = zones
    if zone_filter:
        zones = [z for z in zones if level_scene.zone_label(z) in zone_filter or z in zone_filter]
    out.put(("zones", zones))
    # Authored lighting reads every zone again; decode it beside the geometry rather than before it.
    threading.Thread(target=_post_scene_lighting, args=(archive, all_zones, out, cancel), daemon=True,
                     name="level-lighting").start()
    from rc2016.levels.actors import ActorCatalog, actor_groups
    catalog = ActorCatalog(archive)
    actor_decode_seconds = 0.0
    models, points = {}, []
    for zone in zones:
        if cancel.is_set():
            return
        try:
            scene = level_scene.zone_scene(archive, zone)
        except Exception as exc:
            out.put(("error", zone, f"{type(exc).__name__}: {exc}"))
            continue
        actor_time = time.perf_counter()
        try:
            actor_count = catalog.add_zone(zone, scene)
        except Exception as exc:
            # Layout invariants fail the load instead of silently losing actors.
            out.put(("fatal", f"Actors in {zone}: {type(exc).__name__}: {exc}"))
            return
        actor_decode_seconds += time.perf_counter() - actor_time
        out.put(("zone", dict(zone=zone, instances=scene["instances"], models=len(scene["models"]), actors=actor_count)))
        points.extend(scene["points"])
        for model in scene["models"]:
            entry = models.setdefault(model["path"], dict(hidden=model["hidden"], count=0, zones=[], vertex_colours=[]))
            entry["count"] += model["count"]
            entry["vertex_colours"].extend(model.get("vertex_colours", []))
            entry.setdefault("vertex_paint_blocks",{}).update(model.get("vertex_paint_blocks",{}))
            entry["zones"].append((zone, model["groups"], bool(model.get("static_vector_instances_eligible"))))
    out.put(("bounds", level_scene.core_bounds(points)))
    if level_path.endswith(".level"):
        try:
            out.put(("sky", load_sky(archive, all_zones, probe_position)))
        except Exception as exc:
            out.put(("error", "sky", f"{type(exc).__name__}: {exc}"))
    workers = workers or LOAD_WORKERS
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="level-load")
    try:
        _load_models(archive, pool, workers, models, catalog, actor_groups, points, out, cancel, started,
                     actor_decode_seconds, translations_before)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _post_scene_lighting(archive, zones, out, cancel):
    try:
        from rc2016.levels.scene_lighting import load_scene_lighting
        lighting = load_scene_lighting(archive, zones)
    except Exception as exc:
        lighting = {'lights': [], 'probes': [], 'notes': [f'Authored lighting unavailable: {exc}']}
    if not cancel.is_set():
        out.put(('scene_lighting', lighting))


def _load_models(archive, pool, workers, models, catalog, actor_groups, points, out, cancel, started, actor_decode_seconds,
                 translations_before=None):
    materials = MaterialJobs(archive, pool, out)
    geometry_of = lambda path: level_scene.model_geometry(archive, path)
    geometries, geometry_cache, geometry_paths, overrides = [], {}, {}, dict(outcomes=Counter(), instances=0, models=set())
    ordered = sorted(models.items(), key=lambda item: -item[1]["count"])
    for (path, entry), future in ordered_results(pool, ordered, lambda item: geometry_of(item[0]), workers, cancel):
        materials.post(cancel=cancel)
        try:
            geometry = future.result()
        except Exception as exc:
            out.put(("error", path, f"{type(exc).__name__}: {exc}"))
            continue
        zones, replacements = [], {}
        for zone_entry in entry["zones"]:
            zone, groups = zone_entry[:2]
            static_vector_eligible = bool(zone_entry[2]) if len(zone_entry) > 2 else False
            groups, outcomes, changed = level_scene.effective_groups(geometry["materials"], groups)
            zones.append((zone, groups, static_vector_eligible))
            overrides["outcomes"].update(outcomes)
            overrides["instances"] += changed
            for group_key in groups:
                applied=group_key[1]
                replacements.update((material, None) for _slot, material in applied)
        if replacements:
            overrides["models"].add(path)
        # Replacement materials load with the model's own (each material path loads once).
        geometry["override_materials"] = [dict(path=material) for material in replacements]
        alpha, ground, note = level_scene.instance_vertex_paint(geometry, entry.get("vertex_colours"), entry["count"])
        if alpha is not None:
            geometry = dict(geometry, vertex_alpha=alpha, vertex_ground_blend=ground)
        blocks=entry.get("vertex_paint_blocks",{})
        paints=({identity:dict(alpha=alpha,ground=ground,note=note) for identity in blocks}
                if alpha is not None else level_scene.vertex_paint_streams(geometry,blocks))
        if paints:
            geometry=dict(geometry,vertex_paints=paints)
            note=f'{len(entry.get("vertex_colours",[]))} of {entry["count"]} instances carry {len(paints)} distinct keyed paint blocks'
        if note:
            geometry = dict(geometry, vertex_colour_note=note)
            out.put(("vertex_colours", path, note, alpha is not None or bool(paints)))
        geometries.append(geometry)
        geometry_cache[path.lower()] = geometry
        geometry_paths[path.lower()] = path
        out.put(("model", path, geometry, entry["hidden"], zones))
        out.put(("overrides", dict(outcomes=dict(overrides["outcomes"]), instances=overrides["instances"],
                                   models=len(overrides["models"]))))
        materials.submit(geometry)
    if cancel.is_set():
        return
    actor_time = time.perf_counter()
    candidates = catalog.candidates()
    from rc2016.levels.spawn_format import preview_placements, preview_summary
    spawn_candidates, factories = preview_placements(catalog.factories, catalog.zone_actors, catalog.descriptor)
    actor_models = {}
    for actor in candidates + spawn_candidates:
        actor_models.setdefault(actor['model'].lower(), []).append(actor)

    def actor_geometry(item):
        key, placements = item
        return geometry_cache.get(key) or geometry_of(placements[0]['model'])
    for (key, placements), future in ordered_results(pool, list(actor_models.items()), actor_geometry, workers, cancel):
        path = placements[0]['model']
        materials.post(cancel=cancel)
        upload = key not in geometry_cache
        try:
            geometry = future.result()
            for actor in placements:
                actor['skinned'] = geometry['skinned']
                actor['model_sha256'] = geometry['source_sha256']
            if not geometry['bind_usable']:
                for actor in placements:
                    actor['reason'] = 'unusable bind/rest geometry'
                    actor['skip_skin'] = geometry['skinned']
                continue
        except Exception as exc:
            for actor in placements:
                actor['reason'] = f'model decode failed: {path}: {type(exc).__name__}: {exc}'
            continue
        if upload:
            geometry['override_materials'] = []
            geometry_cache[key] = geometry
            geometry_paths[key] = path
            geometries.append(geometry)
            out.put(("model", path, geometry, level_scene.hidden_by_default(path), []))
        # Canonical GPU path for a model shared with the static layer.
        gpu_path = geometry_paths[key]
        by_zone = {}
        for actor in placements:
            by_zone.setdefault((actor['zone'], bool(actor.get('spawn_preview'))), []).append(actor)
            actor['drawn'] = True
        for (zone, preview), actors in by_zone.items():
            groups, replacements = actor_groups(actors, geometry['materials'])
            geometry['override_materials'].extend(dict(path=p) for p in replacements)
            out.put(("spawns" if preview else "actors", zone, gpu_path, groups, actors))
        if upload:
            materials.submit(geometry)
    if cancel.is_set():
        return
    summary = catalog.summary()
    spawns = preview_summary(spawn_candidates, factories)
    out.put(("spawn_summary", spawns))
    actor_seconds = actor_decode_seconds + time.perf_counter() - actor_time
    out.put(("actor_summary", summary))
    if not points:
        out.put(("bounds", level_scene.core_bounds([a['matrix_rows'][3][:3] for a in candidates if a.get('drawn')])))
    geometry_seconds = time.perf_counter() - started
    for geometry in geometries:   # actor replacement materials on models placed earlier
        materials.submit(geometry)
    materials.post(wait=True, cancel=cancel)
    if cancel.is_set():
        return
    translations = None
    if translations_before is not None:   # native translations read from disk vs translated this load
        now = archive.translation_disk_cache.stats()
        translations = {k: now[k] - translations_before[k] for k in now}
    out.put(("done", dict(geometry_seconds=geometry_seconds, total_seconds=time.perf_counter() - started,
                          translation_cache=translations,
                          models=len(geometries), materials=len(materials.done), actors=summary, spawns=spawns, actor_seconds=actor_seconds,
                          material_overrides=dict(outcomes=dict(overrides["outcomes"]), instances=overrides["instances"],
                                                  models=len(overrides["models"])))))


def kept(remember, variable, key, allowed=None):
    """variable, restored from and saved to the settings file when remember is set."""
    return remember_var(variable, key, allowed) if remember else variable


class LevelPanel:
    """Tk panel; ``source_root`` is a callable returning the current extracted source root."""

    CONTROLS_WIDTH = 300   # initial width of the controls column (Vertical layout)
    CONTROLS_HEIGHT = 340  # initial height of the controls (Horizontal layout: three columns)

    def __init__(self, parent, tk, ttk, source_root, level=None, mode="Shaded", zone_filter=None, on_done=None,
                 list_parent=None, on_open_model=None, material_overrides=True, sky=True, native_translation=True, actors=True, spawn_preview=False,
                 experimental_materials=False, layout=None, remember=False, probe_position=None, on_fullscreen=None):
        """``list_parent``: a separate frame for the level list (the asset browser's left pane);
        by default the list sits at the top of the panel's own side column (standalone window).
        ``on_open_model(model path)``: open a picked model elsewhere (the browser's Model tab).
        ``probe_position``: native Y-up point whose reflection volume the Level atmosphere probe should be
        (``sky.level_sky``); None takes the level's default volume."""
        self.tk, self.ttk, self.source_root = tk, ttk, source_root
        self.probe_position = probe_position
        self.zone_filter, self.on_done, self.on_open_model = zone_filter, on_done, on_open_model
        self.archive, self.archive_root, self.archive_lock = None, None, threading.Lock()
        self.events, self.cancel, self.generation = queue.Queue(), threading.Event(), 0
        self.relay, self.material_paths, self.preview_paths = None, {}, set()
        self._stats_due = False
        self.zone_vars, self.zone_rows, self.progress = {}, {}, {}
        self.closed, self.loaded_level, self.levels = False, None, []
        # Viewport and controls, side by side or stacked per ``layout`` (the model viewer's arrangement); drag the sash to resize.
        self.frame = Splitter(parent, side=self.CONTROLS_WIDTH, stacked=self.CONTROLS_HEIGHT, width=720, height=500)
        main = self.frame.first
        side = self.controls = self.frame.second
        side.configure(padding=(8, 4, 4, 4))
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
        box.bind("<<ComboboxSelected>>", lambda _e: (self.view.set_mode(self.mode_var.get()), self._update_lighting_controls()))
        light = ttk.LabelFrame(side, text="Lighting", padding=(4, 2)); light.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.lighting_var = kept(remember, tk.StringVar(value=LevelViewport.LIGHTINGS[0]), "level.lighting", LevelViewport.LIGHTINGS)
        self.tonemap_var = kept(remember, tk.StringVar(value=LevelViewport.TONEMAPS[0]), "level.tonemap", LevelViewport.TONEMAPS)
        self.lighting_enabled_var = kept(remember, tk.BooleanVar(value=True), "level.lighting_enabled")
        lighting_enable_check = ttk.Checkbutton(light, text="On", variable=self.lighting_enabled_var,
                                                command=self._lighting_enabled_changed)
        lighting_enable_check.grid(row=0, column=0, sticky="w")
        lighting_box = ttk.Combobox(light, textvariable=self.lighting_var, values=LevelViewport.LIGHTINGS, state="readonly", width=16)
        lighting_box.grid(row=0, column=1, sticky="w", padx=(2, 4))
        lighting_box.bind("<<ComboboxSelected>>", lambda _e: self._lighting_changed())
        self.scene_ao_var=kept(remember,tk.BooleanVar(value=True),"level.ao");self.scene_shadows_var=kept(remember,tk.BooleanVar(value=True),"level.shadows");self.scene_authored_var=kept(remember,tk.BooleanVar(value=True),"level.authored")
        scene_options=ttk.Frame(light);scene_options.grid(row=1,column=0,columnspan=2,sticky='w',pady=(2,0))
        scene_tips = []
        self.effect_checks, self.atmosphere_checks = [], []   # see _update_lighting_controls
        scene_tip_text = {"AO": "Ambient occlusion: darkens creases and contact areas. Needs Lighting on and the Shaded view.",
                          "Shadows": "Shadows cast in the scene preview. Needs Lighting on and the Shaded view.",
                          "Authored lights": "The point lights authored in the level data (the nearest ones are used). Needs Lighting on and the Shaded view."}
        # Two rows: the lighting effects, then the atmosphere options.
        for column,(text,var,name) in enumerate((("AO",self.scene_ao_var,"ao"),("Shadows",self.scene_shadows_var,"shadows"),("Authored lights",self.scene_authored_var,"authored"))):
            check = ttk.Checkbutton(scene_options,text=text,variable=var,command=lambda v=var,n=name:self.view.set_scene_effects(**{n:v.get()}))
            check.grid(row=0,column=column,sticky="w",padx=(0,8))
            scene_tips.append((check, scene_tip_text[text]))
            self.effect_checks.append(check)
        self.fog_var = kept(remember, tk.BooleanVar(value=True), "level.haze")
        haze_check = ttk.Checkbutton(scene_options, text="Haze", variable=self.fog_var,
                                     command=lambda: self.view.set_fog(self.fog_var.get()))
        haze_check.grid(row=1, column=0, sticky="w", padx=(0, 8))
        scene_tips.append((haze_check, "Distance haze from the level's atmosphere. Only used by the Level atmosphere lighting."))
        self.atmosphere_checks.append(haze_check)
        self.lightgrid_var = kept(remember, tk.BooleanVar(value=True), "level.light_grid")
        grid_check = ttk.Checkbutton(scene_options, text="Light grid", variable=self.lightgrid_var,
                                     command=lambda: self.view.set_lightgrid(self.lightgrid_var.get()))
        grid_check.grid(row=1, column=1, columnspan=2, sticky="w")
        scene_tips.append((grid_check, "The decoded light grid: baked ambient light that varies through the level. Off uses the reflection-probe ambient. Only used by the Level atmosphere lighting."))
        self.atmosphere_checks.append(grid_check)
        self.scene_note=ttk.Label(light,text='Scene preview; game caster/probe allocation is unverified.',style='Muted.TLabel',wraplength=240)
        self.scene_note.grid(row=2,column=0,columnspan=2,sticky="w")
        self.lighting_note = ttk.Label(light, text="", style="Muted.TLabel", wraplength=240)
        self.lighting_note.grid(row=3, column=0, columnspan=2, sticky="w")
        display = ttk.LabelFrame(side, text="Display", padding=(4, 2)); display.grid(row=5, column=0, sticky="ew", pady=(3, 0))
        ttk.Label(display, text="Tonemap").grid(row=0, column=0, sticky="w")
        tonemap_box = ttk.Combobox(display, textvariable=self.tonemap_var, values=LevelViewport.TONEMAPS, state="readonly", width=19)
        tonemap_box.grid(row=0, column=1, sticky="w", padx=4)
        tonemap_box.bind("<<ComboboxSelected>>", lambda _e: self.view.set_lighting(tonemap=self.tonemap_var.get()))
        # Small-object culling: instances whose bounding sphere is smaller than this on screen.
        self.cull_size_var = tk.StringVar(value="1 px")
        hide_label = ttk.Label(display, text="Hide smaller than")
        hide_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        cull_box = ttk.Combobox(display, textvariable=self.cull_size_var, state="readonly", width=7,
                                values=("Off", "1 px", "2 px", "4 px", "8 px", "16 px"))
        cull_box.grid(row=1, column=1, sticky="w", padx=4, pady=(2, 0))
        cull_box.bind("<<ComboboxSelected>>", lambda _e: self.view.set_min_screen_pixels(
            0 if self.cull_size_var.get() == "Off" else float(self.cull_size_var.get().split()[0])))
        self.native_translation_var = kept(remember, tk.BooleanVar(value=native_translation), "level.native_translation")
        native_check = ttk.Checkbutton(display, text="Native translation", variable=self.native_translation_var,
                                       command=lambda: self.view.set_native_translation(self.native_translation_var.get()))
        native_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
        from rc2016.app.theme import _Tooltip
        self.boost_var = tk.StringVar(value="4.0")
        self.fine_var = kept(remember, tk.BooleanVar(value=True), "level.fine_ctrl")
        boost_label = ttk.Label(display, text="Shift speed ×")
        boost_label.grid(row=3, column=0, sticky="w", pady=(2, 0))
        self.boost_box = ttk.Spinbox(display, from_=BOOST_RANGE[0], to=BOOST_RANGE[1], increment=0.5, width=7, textvariable=self.boost_var,
                                     command=self._boost_typed)
        self.boost_box.grid(row=3, column=1, sticky="w", padx=4, pady=(2, 0))
        self.boost_box.bind("<Return>", self._boost_typed)
        self.boost_box.bind("<FocusOut>", self._boost_typed)
        fine_check = ttk.Checkbutton(display, text="Ctrl = fine move (10%)", variable=self.fine_var,
                                     command=lambda: setattr(self.view, "fine_enabled", self.fine_var.get()))
        fine_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        _Tooltip(tk, boost_label, "How much faster Shift moves than the normal fly speed. The mouse wheel over the view changes it; type a value if you have no wheel.")
        _Tooltip(tk, self.boost_box, "How much faster Shift moves than the normal fly speed. The mouse wheel over the view changes it; type a value if you have no wheel.")
        _Tooltip(tk, fine_check, "Holding Ctrl moves at 10% of the normal fly speed, for fine positioning.")
        scene = ttk.LabelFrame(side, text="Scene", padding=(4, 2)); scene.grid(row=6, column=0, sticky="ew", pady=(3, 0))
        self.overrides_var = kept(remember, tk.BooleanVar(value=material_overrides), "level.material_overrides")
        overrides_check = ttk.Checkbutton(scene, text="Material overrides", variable=self.overrides_var,
                                          command=lambda: self.view.set_material_overrides(self.overrides_var.get()))
        overrides_check.grid(row=0, column=0, sticky="w")
        self.hidden_var = kept(remember, tk.BooleanVar(value=False), "level.collision_helpers")
        hidden_check = ttk.Checkbutton(scene, text="Collision/helpers", variable=self.hidden_var,
                                       command=lambda: self.view.set_show_hidden(self.hidden_var.get()))
        hidden_check.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.sky_var = kept(remember, tk.BooleanVar(value=sky), "level.sky")
        sky_check = ttk.Checkbutton(scene, text="Sky", variable=self.sky_var,
                                    command=lambda: self.view.set_show_sky(self.sky_var.get()))
        sky_check.grid(row=1, column=0, sticky="w")
        self.actors_var = kept(remember, tk.BooleanVar(value=actors), "level.actors")
        actors_check = ttk.Checkbutton(scene, text="Actors", variable=self.actors_var,
                                       command=lambda: self.view.set_show_actors(self.actors_var.get()))
        actors_check.grid(row=1, column=1, sticky="w", padx=(8, 0))
        experimental = ttk.LabelFrame(side, text="Experimental", padding=(4, 2)); experimental.grid(row=7, column=0, sticky="ew", pady=(3, 0))
        self.experimental_var = kept(remember, tk.BooleanVar(value=bool(spawn_preview or experimental_materials)), "level.experimental")
        enable_check = ttk.Checkbutton(experimental, text="Enable", variable=self.experimental_var,
                                       command=self._experimental_changed)
        enable_check.grid(row=0, column=0, sticky="w")
        self.material_previews_var = kept(remember, tk.BooleanVar(value=bool(experimental_materials or not spawn_preview)), "level.material_previews")
        # The options below only mean something while Enable is ticked: they are hidden otherwise.
        self.experimental_body = body = ttk.Frame(experimental)
        body.grid(row=1, column=0, columnspan=2, sticky="w")
        previews_check = ttk.Checkbutton(body, text="Material previews", variable=self.material_previews_var,
                                         command=self._experimental_changed)
        previews_check.grid(row=0, column=0, sticky="w")
        self.spawn_var = kept(remember, tk.BooleanVar(value=spawn_preview), "level.enemy_spawns")
        spawn_check = ttk.Checkbutton(body, text="Enemy spawns", variable=self.spawn_var,
                                      command=self._experimental_changed)
        spawn_check.grid(row=0, column=1, sticky="w", padx=(8, 0))
        from rc2016.app.theme import _Tooltip
        _Tooltip(tk, tonemap_box, "Maps the lit HDR result to screen colours.\nACES fit: filmic. Khronos PBR Neutral: keeps base colours accurate until highlights.\nGame (captured): the decoded Veldin display chain, for Level atmosphere lighting.")
        _Tooltip(tk, native_check, "Shade materials from their translated game shader program instead of the generic reading. Replaces the fallback rules only; exact hand-decoded rules stay.")
        _Tooltip(tk, enable_check, "Show the experimental previews. They are not game-equivalent; the options appear when this is on.")
        _Tooltip(tk, box, "Shaded: the game BRDF under the chosen lighting. Textured: base colour only. Solid, Wireframe: geometry only.")
        _Tooltip(tk, lighting_enable_check, "Master switch for lighting. Off shows the unlit surface colour; self-lit emission stays.")
        _Tooltip(tk, lighting_box, "Viewer rig: fixed key, fill and sky light. Shared HDRI: the review HDRI. Level atmosphere: the level's own sun, ambient and fog, where decoded.")
        _Tooltip(tk, hide_label, "Skip instances whose bounding sphere is smaller than this on screen. Faster in large levels; Off draws everything.")
        _Tooltip(tk, cull_box, "Skip instances whose bounding sphere is smaller than this on screen. Faster in large levels; Off draws everything.")
        _Tooltip(tk, overrides_check, "Apply the level's material overrides on top of each model's own materials.")
        _Tooltip(tk, hidden_check, "Also draw collision and helper meshes, which the game never shows.")
        _Tooltip(tk, sky_check, "Draw the level's sky.")
        _Tooltip(tk, actors_check, "Draw the actors (enemies, props) the level places, not only its static scenery.")
        _Tooltip(tk, spawn_check, "Mark where the level data places enemy spawns. A preview.")
        _Tooltip(tk, previews_check, "Best-effort material previews (Novalis mountain, terrain, and a generic adapter). Matte lighting; inferred inputs; not game-equivalent.")
        ttk.Label(body, text="Mountains: matte. Paint and UVs inferred. Empty blend mask: review value 1; game behavior unverified.",
                  style="Muted.TLabel", wraplength=250).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        from rc2016.shaders.terrain_preview import DEFAULT_CONTROLS
        self.ground_controls = [tk.DoubleVar(value=value) for value in DEFAULT_CONTROLS]
        ground = ttk.Frame(body); ground.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ground_widgets = []
        for row, (label, variable, upper, step) in enumerate(zip(('Blend red', 'Blend green', 'Ground UV scale'), self.ground_controls, (1, 1, 100), (0.05, 0.05, 0.1))):
            ground_label = ttk.Label(ground, text=label)
            ground_label.grid(row=row, column=0, sticky="w")
            ground_widgets.append(ground_label)
            control = ttk.Spinbox(ground, from_=0.01 if row == 2 else 0, to=upper, increment=step,
                                  width=7, textvariable=variable, command=self._ground_controls_changed)
            control.grid(row=row, column=1, padx=(6, 0), sticky="w")
            control.bind('<Return>', self._ground_controls_changed)
            control.bind('<FocusOut>', self._ground_controls_changed)
            ground_widgets.append(control)
        for widget in ground_widgets:
            _Tooltip(tk, widget, "Inputs of the terrain preview: the blend-mask channels and the ground UV scale. Inferred values, not read from the game.")
        for widget, text in scene_tips:
            _Tooltip(tk, widget, text)
        head = ttk.Frame(side); head.grid(row=8, column=0, sticky="ew", pady=(6, 2))
        zones_label = ttk.Label(head, text="Zones")
        zones_label.pack(side="left")
        _Tooltip(tk, zones_label, "A level is made of zones. Untick one to hide it; the view updates at once.")
        for text, visible in (("None", False), ("All", True)):
            ttk.Button(head, text=text, width=5, command=lambda v=visible: self._set_all_zones(v)).pack(side="right", padx=(2, 0))
        side.rowconfigure(9, weight=1)
        holder = ttk.Frame(side); holder.grid(row=9, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1); holder.rowconfigure(0, weight=1)
        self.zone_canvas = tk.Canvas(holder, highlightthickness=0, width=250)
        track(self.zone_canvas, background="bg")
        self.zone_canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.zone_canvas.yview); scroll.grid(row=0, column=1, sticky="ns")
        self.zone_canvas.configure(yscrollcommand=scroll.set)
        self.zone_list = ttk.Frame(self.zone_canvas)
        self.zone_canvas.create_window((0, 0), window=self.zone_list, anchor="nw")
        self.zone_list.bind("<Configure>", lambda _e: self.zone_canvas.configure(scrollregion=self.zone_canvas.bbox("all")))
        self.help_label = ttk.Label(side, text=HELP, wraplength=250, style="Muted.TLabel")
        self.help_label.grid(row=10, column=0, sticky="ew", pady=(8, 0))
        self._groups = dict(options=options, light=light, display=display, scene=scene, experimental=experimental,
                            head=head, holder=holder, help=self.help_label)
        main.columnconfigure(0, weight=1); main.rowconfigure(0, weight=1)
        self.view = LevelViewport(main, width=420, height=300)
        self.view.grid(row=0, column=0, sticky="nsew")
        # The starting values (remembered or the defaults) reach the view through the same setters the checkboxes call.
        self.view.fine_enabled = self.fine_var.get()
        self.view.on_boost_change = lambda value: self.boost_var.set(f"{value:.1f}")
        self.view.set_boost(float(self.boost_var.get()))
        self.view.set_lighting(lighting=self.lighting_var.get(), tonemap=self.tonemap_var.get())
        self.view.set_scene_effects(ao=self.scene_ao_var.get(), shadows=self.scene_shadows_var.get(),
                                    authored=self.scene_authored_var.get())
        self.view.set_fog(self.fog_var.get())
        self.view.set_lightgrid(self.lightgrid_var.get())
        self.view.set_material_overrides(self.overrides_var.get())
        self.view.set_show_hidden(self.hidden_var.get())
        self.view.set_show_sky(self.sky_var.get())
        self.view.set_show_actors(self.actors_var.get())
        self.view.show_spawn_preview = self.spawn_var.get()
        self.view.native_translation = self.native_translation_var.get()
        self.view.set_lighting_enabled(self.lighting_enabled_var.get())
        self.view.set_experimental_materials(self.experimental_var.get() and self.material_previews_var.get())
        self.view.set_spawn_preview(self.experimental_var.get() and self.spawn_var.get())
        if not self.experimental_var.get():
            self.experimental_body.grid_remove()
        self._update_lighting_controls()
        self.view.set_mode(mode)
        self.view.clear("Choose a level from the list.")
        self.view.on_stats = self._show_stats
        self.on_fullscreen = on_fullscreen
        self.view.on_toggle_sidebar = self._toggle_sidebar
        self.view.on_toggle_fullscreen = self._toggle_fullscreen
        self.view.on_pick = self._picked
        self.view.on_open = self._open_model
        # Right-button press and release without a drag clears the pick; a right drag still pans (the view's own binding).
        self._right_press = None
        self.view.bind("<ButtonPress-3>", lambda e: setattr(self, "_right_press", (e.x, e.y)), add="+")
        self.view.bind("<ButtonRelease-3>", self._right_released, add="+")
        self.view.on_lighting_changed = lambda: self.lighting_note.configure(text=self.view.lighting_note or "")
        self.status_var = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.status_var, width=1, anchor="w", padding=(4, 3)).grid(row=1, column=0, sticky="ew")
        picked = ttk.Frame(main); picked.grid(row=2, column=0, sticky="ew")
        picked.columnconfigure(0, weight=1)
        self.picked_var = tk.StringVar(value="Click a model in the view to identify it.")
        ttk.Label(picked, textvariable=self.picked_var, width=1, wraplength=1000, anchor="w", padding=(4, 0, 4, 3)).grid(row=0, column=0, sticky="ew")
        self.open_button = ttk.Button(picked, text="Open in Model tab", state="disabled",
                                      command=lambda: self._open_model(self.view.selected))
        if on_open_model:
            self.open_button.grid(row=0, column=1, padx=4, pady=(0, 3))
        self.frame.after(50, self._poll)
        if layout is not None and list_parent is not None:
            layout.register(self.frame)
            layout.listen(self._arrange_controls)

    def _arrange_controls(self, stacked):
        """Vertical layout: one column of control groups. Horizontal (stacked): three columns, so the viewport keeps the height."""
        side, g = self.controls, self._groups
        side.configure(padding=(4, 6, 4, 4) if stacked else (8, 4, 4, 4))
        places = ({"options": (0, 0), "light": (1, 0), "display": (2, 0), "scene": (0, 1), "experimental": (1, 1),
                   "head": (0, 2), "holder": (1, 2), "help": (2, 2)} if stacked else
                  {"options": (3, 0), "light": (4, 0), "display": (5, 0), "scene": (6, 0), "experimental": (7, 0),
                   "head": (8, 0), "holder": (9, 0), "help": (10, 0)})
        for name, (row, column) in places.items():
            regrid(g[name], row=row, column=column, sticky="nsew" if name == "holder" else "ew", padx=(0, 12) if stacked and column < 2 else 0)
        for column in range(3):
            side.columnconfigure(column, weight=1 if stacked or column == 0 else 0, uniform="groups" if stacked else "")
        side.rowconfigure(9, weight=0 if stacked else 1)
        side.rowconfigure(1, weight=1 if stacked else 0)

    def _build_level_list(self, parent, height):
        """Level list (selecting a row loads it) plus an entry for a typed .level/.zone path."""
        tk, ttk = self.tk, self.ttk
        parent.columnconfigure(0, weight=1); parent.rowconfigure(0, weight=1)
        box = ttk.Frame(parent); box.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        box.columnconfigure(0, weight=1); box.rowconfigure(0, weight=1)
        self.level_list = ttk.Treeview(box, show="tree", selectmode="browse", height=height)
        self.level_list.column("#0", width=230, stretch=True)
        self.level_list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.level_list.yview); scroll.grid(row=0, column=1, sticky="ns")
        self.level_list.configure(yscrollcommand=scroll.set)
        self.level_list.bind("<<TreeviewSelect>>", lambda _e: self._level_chosen())
        pick = ttk.Frame(parent); pick.grid(row=1, column=0, sticky="ew"); pick.columnconfigure(0, weight=1)
        self.level_entry = ttk.Entry(pick, textvariable=self.level_var, width=30)
        self.level_entry.grid(row=0, column=0, sticky="ew")
        self.level_entry.bind("<Return>", lambda _e: self.load())
        from rc2016.app.theme import _Tooltip
        _Tooltip(tk, self.level_list, "Click a level to load it. Loading builds every zone's geometry and places its models.")
        _Tooltip(tk, self.level_entry, "An archive path to a .level or .zone file, for files the list does not show. Enter loads it.")

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
        self.relay, self.material_paths, self.preview_paths = relay, {}, set()
        root = str(self.source_root() or "")
        threading.Thread(target=load_level, args=(lambda: self._archive(root), level, relay, cancel, self.zone_filter),
                         kwargs=dict(probe_position=self.probe_position), daemon=True).start()

    def _lighting_enabled_changed(self):
        self.view.set_lighting_enabled(self.lighting_enabled_var.get())
        self._update_lighting_controls()

    def _update_lighting_controls(self):
        """Grey out the lighting options that cannot change the picture: AO, Shadows and Authored lights need
        lighting on in the Shaded view; Haze and Light grid belong to the Level atmosphere lighting."""
        effects = self.lighting_enabled_var.get() and self.mode_var.get() == "Shaded"
        atmosphere = effects and self.lighting_var.get() == "Level atmosphere"
        for checks, usable in ((getattr(self, "effect_checks", ()), effects), (getattr(self, "atmosphere_checks", ()), atmosphere)):
            for check in checks:
                check.state(["!disabled"] if usable else ["disabled"])

    def _experimental_changed(self):
        enabled = self.experimental_var.get()
        body = getattr(self, "experimental_body", None)
        if body is not None:
            body.grid() if enabled else body.grid_remove()
        self.view.set_experimental_materials(enabled and self.material_previews_var.get())
        self.view.set_spawn_preview(enabled and self.spawn_var.get())
        self._prepare_previews(getattr(self, "material_paths", {}))

    def _prepare_previews(self, paths):   # {material path: has a ready native translation}
        """Prepare the experimental previews of loaded materials once they are enabled. Their source
        analysis is slow, so the loader skips it; this runs it beside the view and swaps the
        materials in when done ('preview_materials')."""
        if not (self.experimental_var.get() and self.material_previews_var.get()):
            return
        from rc2016.viewer.experimental_materials import has_preview, prepare_preview, preview_priority
        todo = sorted((p for p, ready in paths.items() if p not in self.preview_paths and has_preview(p, ready)),
                      key=lambda p: (not preview_priority(p), p))
        if not todo or self.relay is None:
            return
        self.preview_paths.update(todo)
        relay, cancel, root = self.relay, self.cancel, str(self.source_root() or "")

        def one(path):
            if cancel.is_set():
                return
            try:
                archive = self._archive(root)
                try:
                    if not prepare_preview(archive, path):
                        return   # no preview: the loaded entry would not change, so skip its reload and re-upload
                except Exception:
                    pass         # material_entries reports the failure on the entry
                entries = level_scene.material_entries(archive, [dict(path=path)], frozenset(), {}, previews=True)
            except Exception as exc:
                relay.put(("error", "material previews", f"{type(exc).__name__}: {exc}"))
                return
            if not cancel.is_set():
                relay.put(("preview_materials", entries))

        def work():
            # Several threads, so the compile processes analyse different graphs at once; each preview
            # is swapped in when ready instead of after the whole batch. Most candidates have none,
            # and are decided by the analysis alone (cached on the archive for the ones that have one).
            with ThreadPoolExecutor(max_workers=LOAD_WORKERS, thread_name_prefix="material-previews") as pool:
                list(pool.map(one, todo))
        threading.Thread(target=work, daemon=True, name="material-previews").start()

    def _ground_controls_changed(self, event=None):
        try:
            values = [v.get() for v in self.ground_controls]
            self.view.set_ground_preview_controls(*values)
        except (ValueError, self.tk.TclError):
            return

    def _lighting_changed(self):
        if self.lighting_var.get() == "Shared HDRI":
            self.tonemap_var.set("Khronos PBR Neutral")
        elif self.lighting_var.get() == "Level atmosphere":
            self.tonemap_var.set("Game (captured)")   # the game's own display chain (rc2016/levels/display.py)
        elif self.tonemap_var.get() == "Game (captured)":
            self.tonemap_var.set(LevelViewport.TONEMAPS[0])   # that chain belongs to the Level atmosphere lighting
        self.view.set_lighting(self.lighting_var.get(), self.tonemap_var.get())
        self._update_lighting_controls()

    def _picked(self, path):
        if not path:
            self.picked_var.set("Nothing under the pointer.")
            self.open_button.configure(state="disabled")
            return
        actor = self.view.picked_actor
        if actor:
            text = f"{actor['name'] or '(unnamed)'} · {actor['actor_path']} · {actor['id']} · {path}"
            if actor.get('spawn_preview'):
                spawn = actor['spawn_preview']
                text += f" · APPROXIMATE: factory {spawn['factory_id']}, template {spawn['template_id']}, location {spawn['location_id']} · {spawn['policy']}"
            self.picked_var.set(text)
        else:
            self.picked_var.set(f"{path} · {self.view.instance_count(path):,} visible instances (outlined)")
        self.open_button.configure(state="normal")

    def _open_model(self, path):
        if path and self.on_open_model:
            self.on_open_model(path)

    def _right_released(self, event):
        """Cancel the left-click selection (the outline and the picked model) when the right button was
        released where it was pressed; a right drag pans and cancels nothing."""
        press, self._right_press = self._right_press, None
        if press and abs(event.x - press[0]) <= 3 and abs(event.y - press[1]) <= 3:
            self.view.select(None)
            self.view.picked_actor = None
            self.picked_var.set("Click a model in the view to identify it.")
            self.open_button.configure(state="disabled")

    def _boost_typed(self, _event=None):
        try:
            self.view.set_boost(float(self.boost_var.get()))
        except ValueError:
            self.boost_var.set(f"{self.view.boost:.1f}")

    def _add_zone_rows(self, zones):
        names = zone_names(zones)
        for title, members in zone_order(zones):
            header = self.ttk.Label(self.zone_list, style="Muted.TLabel", cursor="hand2")
            header.pack(anchor="w", pady=(4, 0))
            group = self.ttk.Frame(self.zone_list)   # collapsed until the header is clicked
            header.bind("<Button-1>", lambda _e, h=header, g=group, t=title, n=len(members): self._toggle_group(h, g, t, n))
            header.configure(text=f"▸ {title} ({len(members)})")
            for zone in members:
                var = self.tk.BooleanVar(value=True)
                row = self.ttk.Checkbutton(group, text=names[zone] + " …", variable=var,
                                           command=lambda z=zone, v=var: self.view.set_zone_visible(z, v.get()))
                row.pack(anchor="w", padx=(8, 0))
                self.zone_vars[zone], self.zone_rows[zone] = var, row
                row.name = names[zone]

    def _toggle_group(self, header, group, title, count):
        if group.winfo_manager():
            group.pack_forget()
            header.configure(text=f"▸ {title} ({count})")
        else:
            group.pack(anchor="w", after=header)
            header.configure(text=f"▾ {title} ({count})")

    def _toggle_sidebar(self):
        self.frame.set_second_visible(not self.frame.second_visible)

    def _toggle_fullscreen(self):
        """Borderless full screen with the sidebar hidden; leaving restores the sidebar."""
        window = self.frame.winfo_toplevel()
        entering = not self.view.is_fullscreen
        self.view.is_fullscreen = entering
        self.frame.set_second_visible(not entering)
        window.attributes("-fullscreen", entering)
        if self.on_fullscreen:
            self.on_fullscreen(entering)
        self.view.focus_set()

    def _set_all_zones(self, visible):
        for zone, var in self.zone_vars.items():
            if str(self.zone_rows[zone].cget("state")) == "disabled":
                continue
            var.set(visible)
            self.view.set_zone_visible(zone, visible)

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
        if self._stats_due:   # once per tick, not per event
            self._stats_due = False
            self._show_stats(self.view.stats())
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
                if summary["models"] or summary.get("actors"):
                    row.configure(text=f"{row.name} — {summary['instances']:,} placed"
                                  + (f", {summary['actors']:,} actors" if summary.get("actors") else ""))
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
            for zone_entry in zones:
                zone, groups = zone_entry[:2]
                static_vector_eligible = bool(zone_entry[2]) if len(zone_entry) > 2 else False
                self.view.add_instances(zone, path, groups, static_vector_eligible=static_vector_eligible)
            p["models"] += 1
        elif kind == "actors":
            _kind, zone, path, groups, actors = item
            self.view.add_actors(zone, path, groups, actors)
        elif kind == "spawns":
            _kind, zone, path, groups, actors = item
            self.view.add_spawns(zone, path, groups, actors)
        elif kind == "spawn_summary":
            p["spawns"] = item[1]
            for reason, count in item[1]["reasons"].items():
                print(f"level viewer: experimental spawns ({count}): {reason}", file=sys.stderr)
        elif kind == "actor_summary":
            p["actors"] = item[1]
            for reason, count in item[1]['reasons'].items():
                print(f"level viewer: {count} actors unresolved: {reason}", file=sys.stderr)
            if item[1]['duplicates']:
                print(f"level viewer: {len(item[1]['duplicates'])} actor/static near pairs (see actor summary)", file=sys.stderr)
        elif kind == 'scene_lighting':
            self.view.set_scene_lighting(item[1]);p['scene_lighting']=dict(points=len(item[1].get('lights',[])),validated_points=sum(1 for l in item[1].get('lights',[]) if l.get('validated',True)),notes=item[1].get('notes',[]))
        elif kind == "sky":
            self.view.set_sky(item[1])
            p["sky"] = sky_summary(item[1])
            for unsupported in (item[1] or {}).get("unsupported", ()):
                print("level viewer: sky:", unsupported["model"], "-", unsupported["reason"], file=sys.stderr)
        elif kind == "overrides":
            p["overrides"] = item[1]
        elif kind == "vertex_colours":
            _kind, path, note, used = item
            p.setdefault("vertex_colours", {})[path] = used
            print("level viewer: zone-keyed vertex colours (inferred):", path, "-", note, file=sys.stderr)
        elif kind == "materials":
            self.view.add_materials(item[1])
            ready = {path: bool(entry.get("native_translation")) for path, entry in item[1].items()}
            self.material_paths.update(ready)
            self._prepare_previews(ready)
        elif kind == "preview_materials":
            self.view.replace_materials(item[1])
            for path, entry in item[1].items():
                if entry.get("experimental_error"):
                    print("level viewer: material preview:", path, "-", entry["experimental_error"], file=sys.stderr)
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
            cached = item[1].get("translation_cache")
            if cached and (cached["hits"] or cached["misses"]):
                p["state"] += f" (native translations: {cached['hits']} from disk cache, {cached['misses']} translated)"
                print(f"level viewer: native translations {cached['hits']} from disk cache, "
                      f"{cached['misses']} translated", file=sys.stderr)
            finish = getattr(self.view, "finish_load", None)
            if finish:
                finish()   # deferred mip chains build from here
            p["done"] = item[1]
            if self.on_done:
                self.frame.after(300, lambda: self.on_done(self))
        self._stats_due = True

    def _show_stats(self, stats):
        scene=stats.get('scene_preview',{})
        source_note=self.progress.get('scene_lighting',{})
        unavailable=next((note for note in source_note.get('notes',[]) if isinstance(note,str) and note.startswith('Authored lighting unavailable:')),None)
        self.scene_note.configure(text=scene.get('error') or unavailable or f"Scene preview: {scene.get('authored_lights',0)} authored points (nearest 32). Game caster/probe allocation is unverified.")
        p = self.progress
        if not p:
            return
        errors = f" · {len(p['errors'])} failed" if p.get("errors") else ""
        overrides = override_summary(p.get("overrides"))
        sky = f" · {p['sky']}" if p.get("sky") else ""
        keyed = p.get("vertex_colours") or {}
        if keyed:
            sky += f" · {sum(keyed.values())}/{len(keyed)} models with zone-keyed vertex alpha (inferred)"
        actor_stats = p.get('actors', {})
        actors = (f" · actors {stats.get('actors', 0):,} drawn, {actor_stats.get('unresolved', 0):,} unresolved, "
                  f"{actor_stats.get('skipped_skinned', 0):,} skipped-skinned")
        if self.view.show_spawn_preview:
            actors += (f" · EXPERIMENTAL spawns {stats.get('spawns', 0):,} drawn (approximate), "
                       f"{p.get('spawns', {}).get('unresolved', 0):,} unresolved")
        phase = stats.get("phase_ms") or {}
        phases = f" (scene {phase['scene']:.0f}, draw {phase['level']:.0f})" if phase else ""
        self.status_var.set(
            f"{p['state']}{actors} · {p['zones_with_models']}/{p['zones']} zones with placements · {p['models']} models{errors} · "
            f"{stats['materials']} materials · {stats['textures']} textures{overrides}{sky} · "
            f"showing {stats['instances']:,} of {stats.get('placed', stats['instances']):,} instances (culled), "
            f"{stats['triangles']:,} triangles in {stats['draws']} draws · {stats['draw_ms']:.1f} ms/frame{phases} · "
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
