"""Level viewport: all visible instances of a model drawn with one instanced call per material.

Builds on ModelViewport's WGL context, texture upload and per-material uniforms. Geometry and
instance matrices stay in native Y-up space (level_scene.py); the camera matrix applies the
presentation basis (x, y, z) -> (x, -z, y), so the fly camera works in Blender-like Z-up.
"""
from __future__ import annotations

from collections import deque
import ctypes
import itertools
import math
import struct
import sys
import time
import zlib

from rc2016.viewer.viewport import ModelViewport

# Column-major native -> presentation basis: (x, y, z) -> (x, -z, y).
BASIS = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
MOVE_KEYS = {"w": (1, 0, 0), "s": (-1, 0, 0), "d": (0, 1, 0), "a": (0, -1, 0), "e": (0, 0, 1), "q": (0, 0, -1)}
FOV_DEGREES = 60.0


def to_presentation(point):
    x, y, z = point
    return (x, -z, y)


def look_vectors(yaw, pitch):
    """(forward, right, up) for a Z-up camera; yaw 0 looks along +X, positive pitch looks up."""
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    forward = (cp * cy, cp * sy, sp)
    right = (sy, -cy, 0.0)   # normalize(forward x Z)
    up = (right[1] * forward[2] - right[2] * forward[1], right[2] * forward[0] - right[0] * forward[2],
          right[0] * forward[1] - right[1] * forward[0])
    return forward, right, up


def view_matrix(eye, yaw, pitch):
    """Column-major world (presentation) -> view matrix, as gluLookAt."""
    f, s, u = look_vectors(yaw, pitch)
    dot = lambda a, b: a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    return (s[0], u[0], -f[0], 0.0, s[1], u[1], -f[1], 0.0, s[2], u[2], -f[2], 0.0,
            -dot(s, eye), -dot(u, eye), dot(f, eye), 1.0)


def overview_camera(bounds):
    """(eye, yaw, pitch, speed) framing native-space translation bounds from the south, looking down."""
    low, high = (to_presentation(p) for p in bounds)
    low, high = tuple(map(min, low, high)), tuple(map(max, low, high))
    centre = tuple((a + b) * 0.5 for a, b in zip(low, high))
    radius = max(10.0, 0.5 * math.dist(low, high))
    pitch = -35.0
    f, _r, _u = look_vectors(90.0, pitch)
    eye = tuple(c - fi * radius * 1.6 for c, fi in zip(centre, f))
    return eye, 90.0, pitch, radius / 8.0


def png_bytes(width, height, rgb_rows_bottom_up):
    """Minimal RGB PNG from glReadPixels rows (bottom row first)."""
    stride = width * 3
    raw = b"".join(b"\x00" + rgb_rows_bottom_up[(height - 1 - y) * stride:(height - y) * stride] for y in range(height))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def merge_ranges(batches):
    """Merge (first, count) triangle ranges that follow each other; order is kept."""
    merged = []
    for first, count in batches:
        if merged and merged[-1][0] + merged[-1][1] == first:
            merged[-1] = (merged[-1][0], merged[-1][1] + count)
        else:
            merged.append((first, count))
    return merged


def draw_items(models, level, visible_groups, use_overrides=True):
    """[(material key, model, (first, count), groups)] sorted by material key, then model path.

    A model's instance groups are (buffer, count, mirrored, changes), where ``changes`` holds the
    group's effective material overrides ((slot index, material path), ...). Each batch draws its
    slot's material per group; groups that agree on it share one item, so geometry stays shared and
    an override changes only the instances that carry it. Adjacent batches of one model with the
    same material and groups merge into one draw. Batches without UV0 draw untextured (key level 0)."""
    ranges = {}
    for model in models:
        groups = visible_groups(model)
        if not groups:
            continue
        changes = [dict(group[3]) if use_overrides and len(group) > 3 else {} for group in groups]
        for batch in model["batches"]:
            by_key = {}
            for group, changed in zip(groups, changes):
                material = (changed.get(batch[0]) or model["slots"].get(batch[0])) if batch[3] else None
                by_key.setdefault((material or "", level if batch[3] else 0), []).append(group)
            for key, selected in by_key.items():
                ranges.setdefault((key, model["path"], tuple(g[0] for g in selected)),
                                  (model, selected, []))[2].append((batch[1], batch[2]))
    items = [(key, model, span, groups) for (key, _path, _ids), (model, groups, spans) in ranges.items()
             for span in merge_ranges(sorted(spans))]
    items.sort(key=lambda item: (item[0], item[1]["path"]))
    return items


def _mat4_mul(a, b):
    """Product of two column-major 4x4 matrices (as glMultMatrixf: a * b)."""
    return tuple(sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4)) for c in range(4) for r in range(4))


def projection_matrix(width, height, near, far):
    """Column-major glFrustum matrix of LevelViewport._setup_camera."""
    top = near * math.tan(math.radians(FOV_DEGREES) * 0.5)
    right = top * width / height
    return (near / right, 0.0, 0.0, 0.0, 0.0, near / top, 0.0, 0.0,
            0.0, 0.0, -(far + near) / (far - near), -1.0, 0.0, 0.0, -2.0 * far * near / (far - near), 0.0)


def frustum_planes(projection, modelview):
    """The six clip planes (left, right, bottom, top, near, far) of column-major GL matrices as an
    (6, 4) array in the modelview's object space, normalised so that a.x + b.y + c.z + d is the
    signed distance (positive inside)."""
    import numpy as np
    clip = np.asarray(projection, np.float64).reshape(4, 4).T @ np.asarray(modelview, np.float64).reshape(4, 4).T
    planes = np.array([clip[3] + clip[0], clip[3] - clip[0], clip[3] + clip[1], clip[3] - clip[1],
                       clip[3] + clip[2], clip[3] - clip[2]])
    return planes / np.linalg.norm(planes[:, :3], axis=1)[:, None]


def model_sphere(geometry):
    """(centre, radius) bounding all vertices in model space: the smaller of the origin sphere
    (``radius``) and the sphere around the ``bounds`` box; None when neither is known."""
    radius, bounds = geometry.get("radius"), geometry.get("bounds")
    best = ((0.0, 0.0, 0.0), float(radius)) if radius is not None else None
    if bounds:
        low, high = bounds
        box = (tuple((a + b) * 0.5 for a, b in zip(low, high)), 0.5 * math.dist(low, high))
        if best is None or box[1] < best[1]:
            best = box
    return best


def instance_spheres(rows, sphere):
    """World-space bounding spheres of instances: ``rows`` is (n, 16) row-vector matrices (p' = p * M).
    The radius scales by the spectral norm of each 3x3 part, so sheared and mirrored matrices stay
    conservative. Returns (centres (n, 3), radii (n,)) in float64."""
    import numpy as np
    rows = np.asarray(rows, np.float64).reshape(-1, 16)
    if not len(rows):
        return np.zeros((0, 3)), np.zeros(0)
    if sphere is None:
        return rows[:, 12:15].copy(), np.full(len(rows), np.inf)
    linear = rows.reshape(-1, 4, 4)[:, :3, :3]
    centres = np.asarray(sphere[0], np.float64) @ linear + rows[:, 12:15]
    return centres, np.linalg.norm(linear, ord=2, axis=(1, 2)) * float(sphere[1])


def sphere_visibility(centres, radii, planes, margin=1e-3):
    """True where a sphere is not entirely outside one of ``planes``. ``margin`` (relative to the
    radius, plus a small absolute term) keeps float rounding from dropping spheres touching a plane."""
    import numpy as np
    if not len(centres):
        return np.zeros(0, bool)
    distances = centres @ planes[:, :3].T + planes[:, 3]
    reach = radii * (1.0 + margin) + 1e-4
    return np.all(distances >= -reach[:, None], axis=1)


def screen_size_visibility(centres, radii, near_plane, near, height, min_pixels):
    """True where a sphere's projected diameter is at least ``min_pixels`` on a ``height``-pixel
    viewport. Depth is measured along the view axis from ``near_plane`` (the normalised near clip
    plane), which never exceeds the true distance, so the estimate errs toward keeping a sphere."""
    import numpy as np
    if not len(centres) or not min_pixels:
        return np.ones(len(centres), bool)
    depth = np.maximum(centres @ near_plane[:3] + near_plane[3] + near, near)
    focal = height * 0.5 / math.tan(math.radians(FOV_DEGREES) * 0.5)
    return 2.0 * radii * focal / depth >= min_pixels


_VERSIONS = itertools.count(1)


def cached_draw_items(models, level, visible_groups, use_overrides=True):
    """draw_items over ``models``, reusing each model's items while its key holds: (groups version,
    level, overrides, visible). Equal to draw_items(models, ...): items of one model keep their
    relative order and the final sort is stable on (material key, model path)."""
    items = []
    for model in models:
        groups = visible_groups(model)
        key = (model.get("groups_version"), level, use_overrides, bool(groups))
        cached = model.get("draw_items_for")
        if cached is None or cached[0] != key or key[0] is None:
            cached = model["draw_items_for"] = (key, draw_items([model], level, visible_groups, use_overrides))
        items.extend(cached[1])
    items.sort(key=lambda item: (item[0], item[1]["path"]))
    return items


def pick_colour(index):
    """RGB in 0..1 encoding draw id ``index`` (0 is the background) in 24 bits."""
    value = index + 1
    return ((value & 0xFF) / 255.0, ((value >> 8) & 0xFF) / 255.0, ((value >> 16) & 0xFF) / 255.0)


def pick_index(rgb):
    """Inverse of pick_colour for a read-back pixel (bytes); None for the background."""
    value = rgb[0] | rgb[1] << 8 | rgb[2] << 16
    return value - 1 if value else None


class _Recorder:
    """Stands in for the GL and shading objects while ModelViewport._apply_material runs once per
    material: uniform, texture-unit and texture-binding calls are recorded, not issued; everything
    else is read from the real objects."""
    UNIFORMS = ("glUniform1i", "glUniform1f", "glUniform3f", "glUniform4f")

    def __init__(self, gl, shading):
        self.ops, self._unit = [], 0
        recorder = self

        class Proxy:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                if name in recorder.UNIFORMS:
                    return lambda location, *args: location != -1 and recorder.ops.append(("u", location, name, args))
                if name == "glActiveTexture":
                    return lambda unit: setattr(recorder, "_unit", unit)
                if name == "glBindTexture":
                    return lambda target, texture: recorder.ops.append(("t", recorder._unit, target, texture))
                return getattr(self._real, name)
        self.gl, self.shading = Proxy(gl), Proxy(shading)


class LevelViewport(ModelViewport):
    """Interactive level view. Add models and materials from the Tk thread as they decode.

    Speed: frame time is dominated by Python-to-GL call overhead, not GPU work. Each model has one
    instance buffer per handedness holding the instances of every visible zone (rebuilt when zones
    are toggled), so each model batch is one instanced draw; batches of one model that share a
    material and follow each other merge; each (model, instance buffer) pair has a vertex array
    object, so a draw binds one object instead of eight buffers and pointers; and material uniforms
    and texture bindings are recorded once per material and only re-issued where they differ from
    the previous material's. Instances whose bounding sphere lies outside the view frustum are left
    out: when the camera moves, each instance buffer whose visible set changed is refilled with its
    visible rows (glBufferSubData) and drawn with that count."""
    GL_ARRAY_BUFFER, GL_ELEMENT_ARRAY_BUFFER, GL_STATIC_DRAW = 0x8892, 0x8893, 0x88E4
    GL_CW, GL_CCW = 0x0900, 0x0901
    MODES = ("Shaded", "Textured", "Solid", "Wireframe", "Solid + wireframe")
    # Native Y-up objects; the rig stays fixed to the world, so lighting does not turn with the camera.
    OBJECT_TO_WORLD = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
    RIG_FIXED_TO_WORLD = True
    # Level atmosphere: the atmosphere zone's reflection probe as ambient and its sun as the key
    # (rc2016.levels.probe_lighting); opt-in, review preview.
    LIGHTINGS = ModelViewport.LIGHTINGS + ("Level atmosphere",)
    ENVIRONMENT_LIGHTINGS = ModelViewport.ENVIRONMENT_LIGHTINGS + ("Level atmosphere",)
    EXT = {
        "glGenBuffers": (None, [ctypes.c_int, ctypes.c_void_p]),
        "glDeleteBuffers": (None, [ctypes.c_int, ctypes.c_void_p]),
        "glBindBuffer": (None, [ctypes.c_uint, ctypes.c_uint]),
        "glBufferData": (None, [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint]),
        "glVertexAttribDivisor": (None, [ctypes.c_uint, ctypes.c_uint]),
        "glDrawElementsInstanced": (None, [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]),
        "glGenVertexArrays": (None, [ctypes.c_int, ctypes.c_void_p]),
        "glDeleteVertexArrays": (None, [ctypes.c_int, ctypes.c_void_p]),
        "glBindVertexArray": (None, [ctypes.c_uint]),
    }
    # Optional: without it every instance is drawn (no frustum culling).
    OPTIONAL_EXT = {"glBufferSubData": (None, [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_void_p])}
    MATERIAL_BUDGET_SECONDS = 0.008   # texture uploads and shader compiles per Tk tick while loading
    DEFER_MIPMAPS = True              # mip chains are built after the load (build_deferred_mipmaps)
    MIPMAP_BUDGET_SECONDS = 0.008     # mip generation per Tk tick once loaded
    MIPMAP_IDLE_SECONDS = 0.5         # and only once the camera has rested this long
    STATS_INTERVAL = 0.25             # status-line refresh while drawing (4 Hz)
    culling = True
    min_screen_pixels = 1.0   # also cull instances smaller than this on screen (0: frustum only)
    _cull = None
    _frame_lighting = None

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.scene_ao=True;self.scene_shadows=True;self.scene_authored=True
        self._scene_effects=None;self._scene_geometry=False;self._scene_revision=0
        self._scene_lighting={'lights':[],'notes':[]};self.scene_error=None
        self._error = "Loading level…"
        self._show_error(self._error)
        self._ext = None
        self._models = {}          # path -> GPU record
        self._pending = []         # (function, args) received before the context existed
        self._hidden_zones = set()
        self._pending_materials = {}
        self._materials = {}       # material path -> uploaded entry
        self._material_ops = {}    # (material path, level) -> recorded uniform/texture calls
        self._texture_names = {}   # texture path -> (GL id, sRGB) or None
        self._draw_list = None
        self._material_queue = {}  # material path -> entry waiting for its textures to upload
        self._material_pump = None # pending after() id of _pump_materials
        self._compile_queue = []   # material paths whose surface program is compiled ahead of use
        self._cull_masks = {}      # instance buffer -> rows it holds now (bool mask), absent when all
        self._cull_counts = {}     # instance buffer -> instances to draw from it
        self._cull_camera = None
        self._stats_base = None
        self._stats_time, self._stats_pending = 0.0, False
        self.show_hidden = False
        self.show_actors = True
        self.show_spawn_preview = False
        self.picked_actor = None
        self.material_overrides = True   # per-instance material overrides from the zone records
        self.show_sky = True
        self._sky = None           # rc2016.levels.sky.level_sky result plus loaded textures
        self._sky_gl = None        # uploaded sky buffers and textures
        self.on_stats = None
        self.on_pick = None        # called with a model path (or None) after a click
        self.on_open = None        # called with a model path after a double click on it
        self.selected = None       # picked model path, outlined
        self.last_draw_ms = 0.0
        self._bounds_native = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        self._eye, self._yaw, self._pitch, self._speed = (0.0, -50.0, 20.0), 90.0, -20.0, 20.0
        self._far = 10000.0
        self._keys, self._release_pending = set(), {}
        self._last_tick = None
        self._press = None
        self.bind("<ButtonPress-1>", self._begin_look)
        self.bind("<B1-Motion>", self._look)
        self.bind("<ButtonRelease-1>", self._end_look)
        self.bind("<Double-Button-1>", self._double_click)
        self.bind("<ButtonPress-3>", self._begin_pan)
        self.bind("<B3-Motion>", self._pan_drag)
        self.bind("<KeyPress>", self._key_down)
        self.bind("<KeyRelease>", self._key_up)
        self.bind("<FocusOut>", lambda _e: self._keys.clear())

    # Scene ---------------------------------------------------------------------------------
    def set_scene_bounds(self, bounds_native):
        """Frame native-space instance translation bounds and size the far plane and speed."""
        self._bounds_native = bounds_native
        low, high = bounds_native
        # The overview frames the core; backdrops (distant mountains) lie far outside it.
        self._far = max(30000.0, 20.0 * math.dist(low, high))
        self._reset_camera()

    def add_model(self, path, geometry, hidden=False):
        """Upload one model's geometry; its instances come per zone through add_instances."""
        self._gl_call(self._upload_model, path, geometry, hidden)

    def add_instances(self, zone, path, groups, *, static_vector_eligible=False):
        """One zone's instances of an uploaded model: {mirrored: array('f') of 16-float rows}."""
        self._gl_call(self._store_instances, zone, path, groups, bool(static_vector_eligible))

    def add_actors(self, zone, path, groups, actors):
        """Batched actor rows plus source identities for click-to-identify."""
        self._gl_call(self._store_actors, zone, path, groups, actors)

    def add_spawns(self, zone, path, groups, actors):
        self._gl_call(self._store_spawns, zone, path, groups, actors)

    def set_spawn_preview(self, show):
        self.show_spawn_preview = bool(show)
        self._draw_list = None
        self.picked_actor = None
        self._draw()

    def set_show_actors(self, show):
        self.show_actors = bool(show)
        self.picked_actor = None
        self._draw_list = None
        self._schedule_draw()

    def set_zone_visible(self, zone, visible):
        (self._hidden_zones.discard if visible else self._hidden_zones.add)(zone)
        self._draw_list = None
        self._schedule_draw()

    def instance_count(self, path):
        """Visible instances of a model, or 0."""
        model = self._models.get(path)
        return sum(g[1] for g in self._visible_groups(model)) if model else 0

    def _gl_call(self, function, *args):
        if not self._make_current() or self._ext is None and not self._load_ext():
            self._pending.append((function, args))   # the context appears on the first <Configure>
            return
        function(*args)
        if self._error and self._models:
            self._error = None
            self._show_error(None)
        self._draw_list = None
        self._schedule_draw()

    def add_materials(self, entries):
        """{material path: texture_preview entry}; uploads textures once per texture path."""
        self._scene_revision=getattr(self,'_scene_revision',0)+1
        if not self._make_current():
            self._pending_materials.update(entries)
            return
        queue = self.__dict__.setdefault("_material_queue", {})
        for path, entry in entries.items():
            if path not in self._materials:
                queue.setdefault(path, entry)
        self._pump_materials()

    def replace_materials(self, entries):
        """Swap in new entries for materials already posted (experimental previews prepared later).
        Uploaded textures are reused by path; the material's programs and draw state are rebuilt."""
        queue = self.__dict__.setdefault("_material_queue", {})
        for path in entries:
            self._materials.pop(path, None)
            queue.pop(path, None)
            self._pending_materials.pop(path, None)
        self.add_materials(entries)

    def _pump_materials(self, budget=None):
        """Upload queued materials for up to ``budget`` seconds (MATERIAL_BUDGET_SECONDS; at least one
        material per call), then compile their native programs in the time left; what remains
        continues on a later Tk tick, so a large batch does not freeze the window."""
        self.__dict__["_material_pump"] = None
        queue = self.__dict__.setdefault("_material_queue", {})
        compile_queue = self.__dict__.setdefault("_compile_queue", [])
        links = self.__dict__.setdefault("_program_links", {})
        if not (queue or compile_queue or self._pending_links()) or not self._make_current():
            return
        deadline = time.perf_counter() + (self.MATERIAL_BUDGET_SECONDS if budget is None else budget)
        uploaded = False
        while queue:
            path = next(iter(queue))
            entry = queue.pop(path)
            if path in self._materials:
                continue
            self._materials[path] = self._gl_entry(entry, self._texture_names)
            # Early frames can record an empty entry before its textures arrive.
            # Invalidate every mode/program variant of that material.
            for key in [k for k in self._material_ops if k[0] == path]:
                self._material_ops.pop(key)
            compile_queue.append(path)
            uploaded = True
            if time.perf_counter() >= deadline:
                break
        if uploaded:
            self._gl.glBindTexture(self.GL_TEXTURE_2D, 0)
            self._draw_list = None
            self._stats_base = None
            self._schedule_draw()
        while compile_queue and time.perf_counter() < deadline:
            self._precompile(compile_queue.pop(0))
        linked = False
        for key in self._pending_links():   # at least one link per tick
            if linked and time.perf_counter() >= deadline:
                break
            try:
                self._shading.native_program(*links[key])
                del links[key]
            except Exception:
                links[key] = "failed"   # drawn as selected, so the draw reports the error as before
            linked = True
        if linked:
            self.__dict__.pop("_program_memo", None)
            if self._draw_list is not None:
                self._sort_draw_list()
            self._schedule_draw()
        if queue or compile_queue or self._pending_links():
            self._material_pump = self.after(15, self._pump_materials)
        elif self.__dict__.get("_loading_done") and not self.__dict__.get("_mipmap_pump"):
            self.build_deferred_mipmaps()

    def _precompile(self, path):
        """Queue the link of a material's native or experimental surface program before its first
        draw (Shaded mode only); a failure shows up again, and is reported, when the material is drawn."""
        entry = self._materials.get(path) or {}
        if (getattr(self, "_shading", None) is None or self._mode != "Shaded"
                or not (entry.get("native_translation") or entry.get("experimental_preview"))):
            return
        self._selected_surface(entry, 2)

    def _pending_links(self):
        return [key for key, args in self.__dict__.get("_program_links", {}).items() if args != "failed"]

    def _selected_surface(self, entry, level):
        """The model viewer's selection, except that a surface whose program is not linked yet is drawn
        as the next one down (experimental, then native, then the plain shader) until the material pump
        has linked it within its time budget. Enabling previews or scene effects swaps in hundreds of
        programs; linking them all inside one frame froze the window."""
        surface = super()._selected_surface(entry, level)
        if not surface[0] or self._surface_linked(surface, queue=True):
            return surface
        if surface[2]:
            saved = self.experimental_materials
            self.experimental_materials = False
            try:
                surface = super()._selected_surface(entry, level)
            finally:
                self.experimental_materials = saved
            if not surface[0] or self._surface_linked(surface, queue=False):
                return surface
        return None, {}, False

    def _surface_linked(self, surface, queue):
        """Whether the level program of a (translation, textures, experimental) surface is linked, or
        failed to link; with ``queue``, an unlinked one is queued for the material pump."""
        sh = getattr(self, "_shading", None)
        if sh is None:
            return True
        translation, textures, experimental = surface
        args = (translation, {api: texture[1] for api, texture in textures.items()}, True,
                bool(self.scene_effects_active()), experimental)
        key = sh.native_program_key(*args)
        links = self.__dict__.setdefault("_program_links", {})
        if key in sh.programs or links.get(key) == "failed":
            return True
        if queue and key not in links:
            links[key] = args
            if not self.__dict__.get("_material_pump"):
                self._material_pump = self.after(15, self._pump_materials)
        return False

    def finish_load(self):
        """The loader is done: build the deferred mip chains once queued materials are uploaded."""
        self._loading_done = True
        if not (self.__dict__.get("_material_queue") or self.__dict__.get("_compile_queue") or self._pending_links()):
            self.build_deferred_mipmaps()

    def build_deferred_mipmaps(self, budget=None):
        """Generate queued mip chains for up to ``budget`` seconds (MIPMAP_BUDGET_SECONDS) per Tk
        tick. Until then a texture samples its top mip (GL_LINEAR); the finished chain matches
        what generation at upload would have produced."""
        self.__dict__["_mipmap_pump"] = None
        queue = self.__dict__.get("_mipmap_queue")
        if not queue or not self._make_current():
            return
        if budget is None and time.perf_counter() - self.__dict__.get("_camera_changed_at", float("-inf")) < self.MIPMAP_IDLE_SECONDS:
            self._mipmap_pump = self.after(100, self.build_deferred_mipmaps)   # the camera is moving: wait
            return
        deadline = time.perf_counter() + (self.MIPMAP_BUDGET_SECONDS if budget is None else budget)
        while queue:
            name, compressed = queue.popleft()
            self._gl.glBindTexture(self.GL_TEXTURE_2D, name)
            self._build_mipmaps(compressed)
            if time.perf_counter() >= deadline:
                break
        self._gl.glBindTexture(self.GL_TEXTURE_2D, 0)
        self._schedule_draw()
        if queue:
            self._mipmap_pump = self.after(15, self.build_deferred_mipmaps)

    def _cancel_mipmaps(self):
        pump = self.__dict__.get("_mipmap_pump")
        if pump:
            self.after_cancel(pump)
            self._mipmap_pump = None

    def flush_materials(self):
        """Upload every queued material now (captures need the complete frame)."""
        pump = self.__dict__.get("_material_pump")
        if pump:
            self.after_cancel(pump)
        if self._make_current():
            self._pump_materials(budget=float("inf"))
        pump = self.__dict__.get("_material_pump")
        if pump:
            self.after_cancel(pump)
            self._material_pump = None
        self._cancel_mipmaps()
        self.build_deferred_mipmaps(budget=float("inf"))

    def set_mode(self, mode):
        self._draw_list = None   # material keys carry the shading level
        super().set_mode(mode)

    def set_show_hidden(self, show):
        self.show_hidden = bool(show)
        self._draw_list = None
        self._schedule_draw()

    def set_material_overrides(self, enabled):
        """Draw instances with (True) or without their per-instance material overrides."""
        self.material_overrides = bool(enabled)
        self._draw_list = None
        self._schedule_draw()

    def set_sky(self, sky):
        """The level's sky (sky.level_sky with a 'textures' {path: load_texture} map), or None."""
        self._release_sky()
        self._sky = sky
        self._reset_level_environment()
        if self.lighting == "Level atmosphere":
            self.set_lighting()
        self._schedule_draw()

    def _fog_values(self):
        """Distance haze: on only for the Level atmosphere lighting (rc2016.levels.fog, a visual approximation)."""
        from rc2016.levels.fog import fog_state
        enabled = getattr(self, "fog_enabled", True) and getattr(self, "lighting_enabled", True)
        hdri = self.__dict__.get("_environment_lighting") == self.lighting and bool(self.__dict__.get("_environment"))
        return fog_state(getattr(self, "_sky", None), self.lighting if hdri else None, enabled)

    def set_fog(self, enabled):
        """Switch the Level atmosphere distance haze on or off (it only applies to that lighting)."""
        self.fog_enabled = bool(enabled)
        self._frame_lighting = None
        self._schedule_draw()

    def _reset_level_environment(self):
        """Forget the Level atmosphere maps, which belong to the sky they were built from."""
        if self.__dict__.get("_environment_lighting") == "Level atmosphere" and not self._environment_loading:
            self._environment, self._environment_lighting = None, None
            self._environment_texture_stale = self._environment_texture is not None

    def _environment_loader(self, lighting):
        if lighting != "Level atmosphere":
            return super()._environment_loader(lighting)
        loader = (self._sky or {}).get("environment_loader")
        if loader is None:
            def missing():
                raise ValueError("the level has no atmosphere zone with exactly one reflection probe")
            return missing
        return loader

    def _environment_note(self, lighting, maps):
        if lighting != "Level atmosphere":
            return super()._environment_note(lighting, maps)
        probe = maps["probe"].rsplit("/", 2)[-2]
        sun = ", ".join(f"{float(x):.2f}" for x in maps["sun_rgb"])
        return (f"Level atmosphere: probe {probe} ambient, sun RGB96 ({sun}) from the atmosphere. Review preview: "
                f"sun-to-sky scale and exposure are undecoded; diffuse light is scaled by {maps.get('diffuse_scale', 1.0):.2f} "
                f"to match one in-game screenshot (visual calibration).")

    def set_show_sky(self, show):
        self.show_sky = bool(show)
        self._schedule_draw()

    def _visible_groups(self, model):
        if model["hidden"] and not self.show_hidden:
            return ()
        return model["groups"]

    def stats(self):
        """Scene counts (cached until the draw list, the culled set or the materials change) plus the
        current frame time and camera. ``instances``/``triangles`` are drawn after frustum culling;
        ``placed`` counts the instances of the visible zones and layers."""
        base = self._stats_base
        if base is None:
            counts = [(m["triangles"], sum(g[1] for g in self._visible_groups(m)),
                       sum(self._cull_counts.get(g[0], g[1]) for g in self._visible_groups(m))) for m in self._models.values()]
            actors = sum(len(m['actors']) - sum(a['zone'] in self._hidden_zones for a in m['actors'])
                         for m in self._models.values() if not m['hidden'] or self.show_hidden) if self.show_actors else 0
            spawns = sum(sum(a['zone'] not in self._hidden_zones for a in m.get('spawns', []))
                         for m in self._models.values() if not m['hidden'] or self.show_hidden) if self.show_spawn_preview else 0
            base = self._stats_base = dict(
                actors=actors, spawns=spawns, models=len(self._models), placed=sum(n for _t, n, _d in counts),
                instances=sum(d for _t, _n, d in counts), triangles=sum(t * d for t, _n, d in counts),
                materials=len(self._materials), textures=sum(1 for v in self._texture_names.values() if v),
                draws=len(self._draw_list or ()))
        return dict(base, draw_ms=self.last_draw_ms, phase_ms=getattr(self, "last_phase_ms", {}), speed=self._speed, eye=self._eye, yaw=self._yaw, pitch=self._pitch,
                    native_translation=self.native_translation, program_switches=self.native_program_switches,
                    scene_preview=dict(active=self.scene_effects_active(),ao=getattr(self,'scene_ao',False),
                        shadows=getattr(self,'scene_shadows',False),
                        authored_lights=len(getattr(self,'_scene_lighting',{}).get('lights',[])) if getattr(self,'scene_authored',False) else 0,
                        light_limit=32,error=getattr(self,'scene_error',None),game_equivalence=False))

    def _emit_stats(self):
        """on_stats at most every STATS_INTERVAL; a skipped update follows when the interval ends."""
        if not self.on_stats:
            return
        now = time.perf_counter()
        if now - self._stats_time >= self.STATS_INTERVAL:
            self._stats_time = now
            self.on_stats(self.stats())
        elif not self._stats_pending:
            self._stats_pending = True
            self.after(int(self.STATS_INTERVAL * 1000), self._trailing_stats)

    def _trailing_stats(self):
        self._stats_pending = False
        self._emit_stats()

    def clear(self, message="Loading level…"):
        self._cancel_mipmaps()
        self._mipmap_queue, self._loading_done = deque(), False
        if self._make_current():
            buffers = [name for m in self._models.values() for name in m["buffers"] + [g[0] for g in m["groups"]]]
            if buffers:
                self._ext["glDeleteBuffers"](len(buffers), (ctypes.c_uint * len(buffers))(*buffers))
            self._delete_vaos(list(self._models.values()))
            textures = [v[0] for v in self._texture_names.values() if v]
            if textures:
                self._gl.glDeleteTextures(len(textures), (ctypes.c_uint * len(textures))(*textures))
        self._release_sky()
        self._reset_level_environment()
        if self._scene_effects and self._make_current():self._scene_effects.close()
        self._scene_effects=None;self.scene_error=None;self._scene_lighting={'lights':[],'notes':[]}
        self._sky = None
        self._models, self._materials, self._texture_names, self._draw_list = {}, {}, {}, None
        self._material_ops = {}
        pump = self.__dict__.get("_material_pump")
        if pump:
            self.after_cancel(pump)
        self._material_pump, self._material_queue, self._compile_queue, self._program_links = None, {}, [], {}
        self._cull, self._cull_masks, self._cull_counts, self._cull_camera = None, {}, {}, None
        self._stats_base = None
        self._hidden_zones = set()
        self._pending, self._pending_materials = [], {}
        self.selected = None
        self.picked_actor = None
        self._error = message
        self._show_error(message)

    # GL setup ------------------------------------------------------------------------------
    def _load_ext(self):
        gl = self._gl
        gl.wglGetProcAddress.argtypes = [ctypes.c_char_p]; gl.wglGetProcAddress.restype = ctypes.c_void_p
        ext = {}
        for name, (result, arguments) in self.EXT.items():
            address = gl.wglGetProcAddress(name.encode())
            if not address:
                self._error = f"Level view needs OpenGL 3.3 instancing; {name} is unavailable."
                self._show_error(self._error)
                return False
            ext[name] = ctypes.WINFUNCTYPE(result, *arguments)(address)
        for name, (result, arguments) in self.OPTIONAL_EXT.items():
            address = gl.wglGetProcAddress(name.encode())
            if address:
                ext[name] = ctypes.WINFUNCTYPE(result, *arguments)(address)
        gl.glFrontFace.argtypes = [ctypes.c_uint]
        gl.glLoadMatrixf.argtypes = [ctypes.c_void_p]; gl.glMultMatrixf.argtypes = [ctypes.c_void_p]
        gl.glReadPixels.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        gl.glFinish.argtypes = []
        gl.glReadBuffer.argtypes = [ctypes.c_uint]
        self._ext = ext
        return True

    def _buffer(self, target, data):
        name = ctypes.c_uint(0)
        self._ext["glGenBuffers"](1, ctypes.byref(name))
        self._ext["glBindBuffer"](target, name.value)
        address, length = data.buffer_info()
        self._ext["glBufferData"](target, length * data.itemsize, ctypes.c_void_p(address), self.GL_STATIC_DRAW)
        return name.value

    def _upload_model(self, path, geometry, hidden):
        for stream in geometry.get('vertex_paints',{}).values():
            if len(stream['alpha'])*3!=len(geometry['positions']) or len(stream['ground'])!=len(geometry['positions']):
                raise ValueError('Paint stream does not match model vertex count')
        record = dict(path=path, hidden=hidden, batches=geometry["batches"], triangles=geometry["triangles"],
                      slots={m["index"]: m["path"] for m in geometry["materials"]},
                      vbo=self._buffer(self.GL_ARRAY_BUFFER, geometry["positions"]),
                      nbo=self._buffer(self.GL_ARRAY_BUFFER, geometry["normals"]),
                      tbo=self._buffer(self.GL_ARRAY_BUFFER, geometry["uvs"]),
                      ibo=self._buffer(self.GL_ELEMENT_ARRAY_BUFFER, geometry["indices"]),
                      zones=[], static_vector_zone_eligibility={}, actor_zones=[], actors=[], spawn_zones=[], spawns=[], groups=[], groups_for=None,
                      sphere=model_sphere(geometry))
        raw_vectors = geometry.get('raw_vectors')
        record['static_vector_eligible'] = bool(
            geometry.get('static_vector_eligible') and raw_vectors is not None
            and len(raw_vectors) == len(geometry['positions'])
            and len(raw_vectors) % 3 == 0)
        record['svbo'] = (self._buffer(self.GL_ARRAY_BUFFER, raw_vectors)
                          if record['static_vector_eligible'] else None)
        import numpy as np
        p=np.asarray(geometry['positions']).reshape(-1,3)
        record['local_bounds']=(tuple(p.min(axis=0)),tuple(p.max(axis=0)))
        self._scene_revision=getattr(self,'_scene_revision',0)+1
        alpha = geometry.get("vertex_alpha")
        record["abo"] = self._buffer(self.GL_ARRAY_BUFFER, alpha) if alpha is not None and len(alpha) * 3 == len(geometry["positions"]) else None
        ground = geometry.get("vertex_ground_blend")
        record["gbo"] = self._buffer(self.GL_ARRAY_BUFFER, ground) if ground is not None and len(ground) == len(geometry["positions"]) else None
        record["buffers"] = [record[k] for k in ("vbo", "nbo", "tbo", "ibo", "abo", "gbo", "svbo") if record[k]]
        paints=geometry.get('vertex_paints',{})
        if paints:
            record['paint_buffers']={}
            record['uniform_paint']=bool(record['abo'] and record['gbo'] and len(paints)==1)
            for identity,stream in sorted(paints.items()):
                # Reuse the uniform stream's existing buffers instead of a second upload.
                if record['uniform_paint'] and len(paints)==1:abo,gbo=record['abo'],record['gbo']
                else:
                    abo=self._buffer(self.GL_ARRAY_BUFFER,stream['alpha']);gbo=self._buffer(self.GL_ARRAY_BUFFER,stream['ground'])
                    record['buffers'].extend((abo,gbo))
                record['paint_buffers'][identity]=(abo,gbo)
        self._ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0); self._ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, 0)
        self._models[path] = record

    def _store_instances(self, zone, path, groups, static_vector_eligible=False):
        """Keep one zone's instance rows, {(mirrored, material changes): rows}; the GPU buffers merge
        every visible zone (_merge_groups)."""
        record = self._models[path]
        self._scene_revision=getattr(self,'_scene_revision',0)+1
        eligibility = record.setdefault('static_vector_zone_eligibility', {})
        eligibility[zone] = bool(eligibility.get(zone, True) and static_vector_eligible)
        for key, rows in sorted(groups.items()):
            record["zones"].append((zone, key, rows))
        record["groups_for"] = None

    def _static_vector_eligible_for_model(self, model):
        """All source geometry and every relevant zone instance record must be eligible."""
        if not (model.get('static_vector_eligible') and model.get('svbo')):
            return False
        zone_names = {zone for zone, _key, _rows in model.get('zones', ())}
        zone_eligibility = model.get('static_vector_zone_eligibility') or {}
        if not zone_names or not all(zone_eligibility.get(zone) is True for zone in zone_names):
            return False
        if getattr(self, 'show_actors', False) and (model.get('actor_zones') or model.get('actors')):
            return False
        if getattr(self, 'show_spawn_preview', False) and (model.get('spawn_zones') or model.get('spawns')):
            return False
        return True

    def _store_actors(self, zone, path, groups, actors):
        record = self._models[path]
        for key, rows in sorted(groups.items()):
            record['actor_zones'].append((zone, key, rows))
        record['actors'].extend(actors)
        record['groups_for'] = None

    def _store_spawns(self, zone, path, groups, actors):
        record = self._models[path]
        for key, rows in sorted(groups.items()):
            record['spawn_zones'].append((zone, key, rows))
        record['spawns'].extend(actors)
        record['groups_for'] = None

    def _merge_groups(self):
        """One instance buffer per model, handedness, material changes and optional paint identity over visible zones:
        [(buffer, count, mirrored, changes)]. A model is rebuilt only when its own data changed or a
        toggle touches it: a zone it has rows in, or a layer (actors, spawn preview) it has rows in."""
        from array import array
        hidden = frozenset(self._hidden_zones)
        preview = getattr(self, 'show_spawn_preview', False)
        masks, counts = getattr(self, '_cull_masks', None), getattr(self, '_cull_counts', None)
        for model in self._models.values():
            spawn_zones = model.get("spawn_zones", [])
            zones = {zone for zone, _key, _rows in model["zones"] + model["actor_zones"] + spawn_zones}
            state = (hidden & zones, self.show_actors if model["actor_zones"] else None,
                     preview if spawn_zones else None)
            if model["groups_for"] == state:
                continue
            old = [g[0] for g in model["groups"]]
            if old:
                self._ext["glDeleteBuffers"](len(old), (ctypes.c_uint * len(old))(*old))
                for name in old:   # GL reuses deleted names
                    if masks is not None:
                        masks.pop(name, None)
                    if counts is not None:
                        counts.pop(name, None)
            self._delete_vaos([model])
            merged = {}
            layers = model["zones"] + (model["actor_zones"] if self.show_actors else [])
            if preview:
                layers += spawn_zones
            for zone, key, rows in layers:
                if zone not in hidden:
                    if model.get('paint_buffers'):key=(*key[:2],key[2] if len(key)>2 else '')
                    merged.setdefault(key, array("f")).extend(rows)
            model["groups"] = [(self._buffer(self.GL_ARRAY_BUFFER, rows), len(rows) // 16, *key)
                               for key, rows in sorted(merged.items())]
            model["instance_rows"] = {group[0]: rows for group, rows in zip(model["groups"], (merged[k] for k in sorted(merged)))}
            model["groups_for"] = state
            model["groups_version"] = next(_VERSIONS)
            model["instance_spheres"] = {}
            self._cull = None
        self._ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0)

    def _delete_vaos(self, models):
        names = [vao for model in models for vao in model.pop("vaos", {}).values()]
        if names and self._ext:
            self._ext["glDeleteVertexArrays"](len(names), (ctypes.c_uint * len(names))(*names))

    def _vao(self, model, group):
        """The vertex array object of one model and one of its instance buffers, built on first use:
        positions, normals and UVs (fixed-function arrays), indices, and the four matrix-row attributes."""
        vaos = model.setdefault("vaos", {})
        vao = vaos.get(group[0])
        if vao is None:
            from rc2016.viewer.shading import INSTANCE_ATTRIBUTES
            gl, ext, sh = self._gl, self._ext, self._shading
            name = ctypes.c_uint(0)
            ext["glGenVertexArrays"](1, ctypes.byref(name))
            vao = vaos[group[0]] = name.value
            ext["glBindVertexArray"](vao)
            gl.glEnableClientState(self.GL_VERTEX_ARRAY); gl.glEnableClientState(self.GL_NORMAL_ARRAY)
            gl.glEnableClientState(self.GL_TEXTURE_COORD_ARRAY)
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, model["vbo"]); gl.glVertexPointer(3, self.GL_FLOAT, 0, None)
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, model["nbo"]); gl.glNormalPointer(self.GL_FLOAT, 0, None)
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, model["tbo"]); gl.glTexCoordPointer(2, self.GL_FLOAT, 0, None)
            ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, model["ibo"])
            # Attribute 14 is per vertex; the instance matrix rows remain per instance.
            alpha = INSTANCE_ATTRIBUTES["aRockAlpha"]
            paint=group[4] if len(group)>4 else ''
            abo,gbo=(model.get('paint_buffers',{}).get(paint,(None,None))
                     if model.get('paint_buffers') else (model.get('abo'),model.get('gbo')))
            if paint and model.get('uniform_paint'):abo,gbo=model.get('abo'),model.get('gbo')
            if abo:
                ext["glBindBuffer"](self.GL_ARRAY_BUFFER, abo)
                sh.glEnableVertexAttribArray(alpha)
                sh.glVertexAttribPointer(alpha, 1, self.GL_FLOAT, 0, 4, None)
                ext["glVertexAttribDivisor"](alpha, 0)
            else:
                sh.glDisableVertexAttribArray(alpha)
            ground = INSTANCE_ATTRIBUTES["aGroundBlend"]
            if gbo:
                ext["glBindBuffer"](self.GL_ARRAY_BUFFER, gbo)
                sh.glEnableVertexAttribArray(ground)
                sh.glVertexAttribPointer(ground, 3, self.GL_FLOAT, 0, 12, None)
                ext["glVertexAttribDivisor"](ground, 0)
            else:
                sh.glDisableVertexAttribArray(ground)
            static_vector = INSTANCE_ATTRIBUTES["aStaticPackedVector"]
            if model.get('svbo'):
                ext["glBindBuffer"](self.GL_ARRAY_BUFFER, model['svbo'])
                sh.glEnableVertexAttribArray(static_vector)
                sh.glVertexAttribPointer(static_vector, 3, self.GL_FLOAT, 0, 12, None)
                ext["glVertexAttribDivisor"](static_vector, 0)
            else:
                sh.glDisableVertexAttribArray(static_vector)
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, group[0])
            for row in range(4):
                location = INSTANCE_ATTRIBUTES[f"aInst{row}"]
                sh.glEnableVertexAttribArray(location)
                sh.glVertexAttribPointer(location, 4, self.GL_FLOAT, 0, 64, ctypes.c_void_p(16 * row))
                ext["glVertexAttribDivisor"](location, 1)
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0)
            self._bound_vao = vao
        return vao

    def _flush_pending(self):
        if self._pending and (self._ext or self._load_ext()):
            pending, self._pending = self._pending, []
            for function, args in pending:
                function(*args)
            if self._models:
                self._error = None
                self._show_error(None)
        if self._pending_materials:
            pending, self._pending_materials = self._pending_materials, {}
            self.add_materials(pending)

    # Drawing -------------------------------------------------------------------------------
    def _level(self):
        return {"Shaded": 2, "Textured": 1}.get(self._mode, 0)

    def _build_draw_list(self):
        """Draw items sorted by material so uniforms change once per material (draw_items). Each model's
        items are kept until its groups, the shading level or the override setting change, so models
        arriving during a load cost only their own items (the result equals draw_items over all)."""
        self._scene_revision=getattr(self,'_scene_revision',0)+1
        self._merge_groups()
        self._draw_list = cached_draw_items(self._models.values(), self._level(), self._visible_groups,
                                            self.material_overrides)
        self._cull = None
        self._stats_base = None
        self._sort_draw_list()

    def _sort_draw_list(self):
        # Keep all draws of one native program together, then retain material/model ordering.
        def program_key(item):
            key = item[0]; entry = self._materials.get(key[0]) or {}
            eligible = self._static_vector_eligible_for_model(item[1])
            self._static_vector_draw_eligible = eligible
            try:
                t, _textures, _experimental = self._selected_surface(entry, key[1])
            finally:
                self._static_vector_draw_eligible = False
            return (not eligible, t['translation_sha256'] if t else '', key, item[1]['path'])
        self._static_vector_draw_eligible = False
        self._draw_list.sort(key=program_key)
        self._static_vector_draw_eligible = False

    def _setup_camera(self, width, height):
        gl = self._gl
        # Near plane follows fly speed: fine detail up close, less depth fighting when flying fast.
        near = max(0.05, min(2.0, self._speed * 0.004))
        top = near * math.tan(math.radians(FOV_DEGREES) * 0.5)
        gl.glMatrixMode(self.GL_PROJECTION); gl.glLoadIdentity()
        gl.glFrustum(-top * width / height, top * width / height, -top, top, near, self._far)
        gl.glMatrixMode(self.GL_MODELVIEW)
        gl.glLoadMatrixf((ctypes.c_float * 16)(*view_matrix(self._eye, self._yaw, self._pitch)))
        gl.glMultMatrixf((ctypes.c_float * 16)(*BASIS))
        self._near = near
        self._frame_lighting = None   # the lighting frames follow this modelview

    # Frustum culling -----------------------------------------------------------------------
    def _prepare_culling(self):
        """Bounding spheres of every instance in the visible models' buffers, as one array, so a camera
        change costs a few NumPy operations, not one per buffer."""
        import numpy as np
        buffers, rows, centres, radii = [], [], [], []
        for model in self._models.values():
            spheres = model.setdefault("instance_spheres", {})
            for group in self._visible_groups(model):
                data = model.get("instance_rows", {}).get(group[0])
                if data is None:
                    continue
                if group[0] not in spheres:
                    spheres[group[0]] = instance_spheres(np.frombuffer(data, np.float32).reshape(-1, 16), model.get("sphere"))
                buffers.append(group[0]); rows.append(data)
                centres.append(spheres[group[0]][0]); radii.append(spheres[group[0]][1])
        sizes = np.array([len(c) for c in centres], np.int64)
        self._cull = dict(buffers=buffers, rows=rows, sizes=sizes, starts=np.concatenate(([0], np.cumsum(sizes)[:-1])) if len(sizes) else sizes,
                          centres=np.concatenate(centres) if centres else np.zeros((0, 3)),
                          radii=np.concatenate(radii) if radii else np.zeros(0),
                          owner=np.repeat(np.arange(len(buffers)), sizes))
        self._cull_camera = None

    def _update_culling(self, width, height):
        """Refill the instance buffers whose visible set changed since the last camera state."""
        import numpy as np
        if not (self.culling and self._ext and self._ext.get("glBufferSubData")):
            return
        camera = (self._eye, self._yaw, self._pitch, self._near, self._far, width, height, self.min_screen_pixels)
        if self._cull is None:
            self._prepare_culling()
        elif camera == self._cull_camera:
            return
        self._cull_camera = camera
        cull = self._cull
        if not len(cull["radii"]):
            return
        modelview = _mat4_mul(view_matrix(self._eye, self._yaw, self._pitch), BASIS)
        planes = frustum_planes(projection_matrix(width, height, self._near, self._far), modelview)
        visible = sphere_visibility(cull["centres"], cull["radii"], planes)
        if self.min_screen_pixels:
            visible &= screen_size_visibility(cull["centres"], cull["radii"], planes[4], self._near, height,
                                              self.min_screen_pixels)
        previous = np.ones(len(visible), bool)
        for index, name in enumerate(cull["buffers"]):
            mask = self._cull_masks.get(name)
            if mask is not None:
                start = cull["starts"][index]
                previous[start:start + len(mask)] = mask
        changed = np.unique(cull["owner"][visible != previous]).tolist()
        if not changed:
            return
        ext, sub = self._ext, self._ext["glBufferSubData"]
        for index in changed:
            name, start, size = cull["buffers"][index], int(cull["starts"][index]), int(cull["sizes"][index])
            mask = visible[start:start + size]
            count = int(mask.sum())
            if count:   # with nothing visible the stale rows are simply not drawn
                rows = np.ascontiguousarray(np.frombuffer(cull["rows"][index], np.float32).reshape(-1, 16)[mask])
                ext["glBindBuffer"](self.GL_ARRAY_BUFFER, name)
                sub(self.GL_ARRAY_BUFFER, 0, rows.nbytes, rows.ctypes.data)
            self._cull_masks[name] = mask.copy()
            self._cull_counts[name] = count
        ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0)
        self._stats_base = None

    def set_min_screen_pixels(self, pixels):
        """Cull instances whose bounding sphere spans fewer than ``pixels`` on screen (0: off)."""
        self.min_screen_pixels = max(0.0, float(pixels))
        self._cull_camera = None
        self._schedule_draw()

    def scene_effects_active(self):
        return getattr(self,'lighting_enabled',True) and bool(getattr(self,'_scene_effects',None)) and self._mode=='Shaded' and (self.scene_ao or self.scene_shadows or self.scene_authored)

    def set_scene_effects(self,ao=None,shadows=None,authored=None):
        for name,value in (('scene_ao',ao),('scene_shadows',shadows),('scene_authored',authored)):
            if value is not None:setattr(self,name,bool(value))
        self.scene_error=None;self._scene_revision=getattr(self,'_scene_revision',0)+1;self._schedule_draw()

    def set_scene_lighting(self,data):
        self._scene_lighting=data;self._scene_revision=getattr(self,'_scene_revision',0)+1;self._schedule_draw()

    def scene_bounds(self):
        import numpy as np
        if getattr(self,'_scene_bounds_revision',None)==self._scene_revision:return self._scene_bounds_cache
        lows=[];highs=[]
        for m in self._models.values():
            if not self._visible_groups(m):continue
            low,high=m['local_bounds'];corners=np.array([[x,y,z,1.] for x in (low[0],high[0]) for y in (low[1],high[1]) for z in (low[2],high[2])])
            layers=m['zones']+(m['actor_zones'] if self.show_actors else [])+(m['spawn_zones'] if self.show_spawn_preview else [])
            for zone,_key,rows in layers:
                if zone in self._hidden_zones:continue
                matrices=np.asarray(rows,dtype=float).reshape(-1,4,4);p=np.einsum('ci,nij->ncj',corners,matrices)[:,:,:3]
                if len(p):lows.append(p.min(axis=(0,1)));highs.append(p.max(axis=(0,1)))
        self._scene_bounds_cache=(tuple(float(x) for x in np.min(lows,axis=0)),tuple(float(x) for x in np.max(highs,axis=0))) if lows else self._bounds_native
        self._scene_bounds_revision=self._scene_revision;return self._scene_bounds_cache

    def _apply_lighting(self,u):
        super()._apply_lighting(u)
        effects=self._scene_effects
        if not effects:return
        effects.apply(self,u)
        if self._scene_geometry or not self.scene_authored:return
        mv=(ctypes.c_float*16)();self._gl.glGetFloatv(self.GL_MODELVIEW_MATRIX,mv)
        count,positions,colours=self._authored_light_uniforms(bytes(mv),self._scene_lighting.get('lights',[]))
        self._shading.glUniform1i(u['uSceneLightCount'],count)
        if count:
            effects.uniform4(u['uSceneLightPos'],count,positions)
            self._shading.glUniform3fv(u['uSceneLightColour'],count,colours)

    def _authored_light_uniforms(self,modelview,all_lights):
        """(count, positions, colours) of the 32 nearest authored lights in view space, as ctypes
        arrays. Every program drawn in a frame shares one modelview and light list, so the sort
        over all lights runs once per (modelview, list), not once per shader switch."""
        cached=self.__dict__.get('_authored_light_cache')
        if cached is not None and cached[0]==modelview and cached[1] is all_lights:
            return cached[2]
        import numpy as np
        matrix=np.array((ctypes.c_float*16).from_buffer_copy(modelview)).reshape(4,4).T
        candidates=all_lights
        if len(all_lights)>32:
            # Exact prefilter: one vectorised distance pass keeps the lights within a relative 1e-9
            # of the 32nd nearest (rounding differs by ~1e-13), in their original order, so the
            # stable sort below selects and orders exactly as it would over every light.
            known=self.__dict__.get('_authored_light_positions')
            if known is None or known[0] is not all_lights:
                known=self._authored_light_positions=(all_lights,np.array([l['position'] for l in all_lights],dtype=float))
            distances=np.linalg.norm(known[1]@matrix[:3,:3].T+matrix[:3,3],axis=1)
            limit=np.partition(distances,31)[31]
            candidates=[all_lights[i] for i in np.flatnonzero(distances<=limit*(1+1e-9)+1e-9)]
        lights=sorted(candidates,key=lambda l:float(np.linalg.norm((matrix@np.r_[l['position'],1.])[:3])))[:32]
        positions=[];colours=[]
        for light in lights:
            inverse=np.float32(light['inverse_radius'])
            positions.extend([*(matrix@np.r_[light['position'],1.])[:3],float(inverse)])
            colours.extend((np.array(light['colour'],dtype=np.float32)*np.float32(inverse*inverse)).tolist())
        result=(len(lights),(ctypes.c_float*len(positions))(*positions),(ctypes.c_float*len(colours))(*colours))
        self._authored_light_cache=(modelview,all_lights,result)
        return result

    def _restore_unculled_instances(self):
        """Restore full visible-zone rows before shadow rendering.

        Camera culling compacts the GPU buffers; the retained CPU rows and scene
        bounds stay complete. Shadow casters outside the camera still draw here.
        The main frame's culling runs again after the scene passes.
        """
        if not self._cull_masks:
            return
        ext = self._ext
        self._cull_camera = None
        self._stats_base = None
        for model in self._models.values():
            for group in self._visible_groups(model):
                name = group[0]
                mask = self._cull_masks.get(name)
                if mask is None or bool(mask.all()):
                    continue
                rows = model['instance_rows'][name]
                address, length = rows.buffer_info()
                ext['glBindBuffer'](self.GL_ARRAY_BUFFER, name)
                ext['glBufferSubData'](self.GL_ARRAY_BUFFER, 0, length * rows.itemsize,
                                       ctypes.c_void_p(address))
                self._cull_masks.pop(name, None)
                self._cull_counts.pop(name, None)
        ext['glBindBuffer'](self.GL_ARRAY_BUFFER, 0)
        self._cull_masks, self._cull_counts, self._cull_camera = {}, {}, None
        self._stats_base = None

    def _draw_geometry(self,shadow=False):
        if shadow:
            self._restore_unculled_instances()
        else:
            # A reused shadow map leaves the instance buffers at the preceding
            # camera's compacted set. AO needs this frame's camera-visible rows.
            self._cull_frame(max(1, self.winfo_width()), max(1, self.winfo_height()))
        # Each pass has a different modelview, including the shadow light view.
        self._frame_lighting = None
        self._scene_geometry=True
        try:
            self._draw_level(geometry=True,shadow=shadow)
        finally:
            self._scene_geometry=False
            self._frame_lighting = None

    def _draw(self):
        if not self._make_current():
            return
        started = time.perf_counter()
        gl = self._gl
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        gl.glViewport(0, 0, width, height)
        gl.glClearColor(0.33, 0.39, 0.47, 1.0)
        gl.glClear(self.GL_COLOR_BUFFER_BIT | self.GL_DEPTH_BUFFER_BIT)
        if self._pending or self._pending_materials:
            self._flush_pending()
        scene_ms = level_ms = 0.0
        camera = (tuple(self._eye), self._yaw, self._pitch, width, height)
        if camera != self.__dict__.get("_last_camera"):
            self._last_camera, self._camera_changed_at = camera, started
        if self._models and self._shading is not None and self._ext:
            self._setup_camera(width, height)
            if self._draw_list is None:
                self._build_draw_list()
            phase = time.perf_counter()
            if getattr(self,'lighting_enabled',True) and self._mode=='Shaded' and (self.scene_ao or self.scene_shadows or self.scene_authored) and not self.scene_error:
                try:
                    if self._scene_effects is None:
                        from rc2016.levels.scene_effects import SceneEffects
                        self._scene_effects=SceneEffects(gl,self._shading)
                    self._scene_effects.prepare(self,self._draw_geometry)
                except Exception as exc:
                    self.scene_error=f'Scene preview unavailable: {exc}'
                    print(self.scene_error,file=sys.stderr)
                    if self._scene_effects:self._scene_effects.close()
                    self._scene_effects=None
            scene_ms = (time.perf_counter() - phase) * 1000.0
            self._cull_frame(width, height)
            if self._sky and self.show_sky and self._mode in ("Shaded", "Textured"):
                try:
                    self._draw_sky()
                except Exception as exc:   # the level still draws on the flat background
                    print(f"level viewer: sky failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    self._release_sky()
                    self._sky = None
            phase = time.perf_counter()
            try:
                self._draw_level()
                level_ms = (time.perf_counter() - phase) * 1000.0
            except Exception as exc:   # report instead of failing every Tk repaint
                self.shading_error = f"Level view failed: {exc}"
                self._error = self.shading_error
                self._show_error(self._error)
                self._models = {}
        self.last_draw_ms = (time.perf_counter() - started) * 1000.0
        self.last_phase_ms = dict(scene=scene_ms, level=level_ms)   # CPU time issuing each phase
        self._gdi32.SwapBuffers(self._hdc)
        self._emit_stats()

    def _cull_frame(self, width, height):
        try:
            self._update_culling(width, height)
        except Exception as exc:   # draw everything instead: refill every buffer with all its rows
            print(f"level viewer: culling disabled: {type(exc).__name__}: {exc}", file=sys.stderr)
            self.culling = False
            self._cull, self._cull_masks, self._cull_counts = None, {}, {}
            for model in self._models.values():
                model["groups_for"] = None
            self._build_draw_list()

    def _begin_attributes(self):
        from rc2016.viewer.shading import INSTANCE_ATTRIBUTES
        # Generic values are context state, not VAO state; restore white on every pass.
        self._shading.glVertexAttrib4f(INSTANCE_ATTRIBUTES["aRockAlpha"], 1.0, 0.0, 0.0, 1.0)
        self._shading.glVertexAttrib4f(INSTANCE_ATTRIBUTES["aGroundBlend"], 0.0, 0.0, 0.0, 0.0)
        self._shading.glVertexAttrib4f(INSTANCE_ATTRIBUTES["aStaticPackedVector"], 0.0, 0.0, 0.0, 0.0)
        self._bound_vao = self._front = None

    def _end_attributes(self):
        self._ext["glBindVertexArray"](0)
        self._gl.glFrontFace(self.GL_CCW)
        self._bound_vao = self._front = None

    def _material_state(self, u, key, entry):
        """Issue only the uniform and texture calls of this material that differ from the current state.

        The material's calls are recorded once ({location: (function, args)}, {(unit, target): texture});
        the dict-view differences against the state run in C, so an unchanged uniform costs nothing."""
        cache_key = (*key, self._active_surface_program)
        ops = self._material_ops.get(cache_key)
        if ops is None:
            recorder = _Recorder(self._gl, self._shading)
            gl, sh = self._gl, self._shading
            self._gl, self._shading = recorder.gl, recorder.shading
            try:
                self._apply_material(u, entry, key[1])
            finally:
                self._gl, self._shading = gl, sh
            uniforms, textures = {}, {}
            for op in recorder.ops:
                if op[0] == "u":
                    uniforms[op[1]] = (op[2], op[3])
                else:
                    textures[(op[1], op[2])] = op[3]
            ops = self._material_ops[cache_key] = (uniforms, textures)
        gl, sh = self._gl, self._shading
        changed = ops[0].items() - self._uniform_state.items()
        for location, (name, args) in changed:
            getattr(sh, name)(location, *args)
        self._uniform_state.update(changed)
        changed = ops[1].items() - self._texture_state.items()
        for (unit, target), texture in changed:
            if self._active_unit != unit:
                sh.glActiveTexture(unit)
                self._active_unit = unit
            gl.glBindTexture(target, texture)
        self._texture_state.update(changed)

    def _draw_level(self,geometry=False,shadow=False):
        from rc2016.viewer.shading import (ARCH_SEQUENCE_TEXTURE_UNIT, BNG_DETAIL_TEXTURE_UNIT, PICK_TEXTURE_UNIT,
                                 TINT_DETAIL_TEXTURE_UNIT, TINT_MASK_TEXTURE_UNIT, VEHICLE_PALETTE_TEXTURE_UNIT,
                                 GLOW_PARALLAX_TEXTURE_UNIT)
        gl, sh = self._gl, self._shading
        self._static_vector_draw_eligible = False
        program, u = sh.instanced_program(scene=self.scene_effects_active())
        sh.glUseProgram(program)
        base_program, base_u = program, u
        self._active_surface_program = program
        self.native_program_switches = 1
        self._apply_lighting(u)
        for unit, name in ((TINT_DETAIL_TEXTURE_UNIT, "uTexTintDetail"), (0, "uTexC"), (1, "uTexG"), (2, "uTexN"),
                           (PICK_TEXTURE_UNIT, "uTexP"), (TINT_MASK_TEXTURE_UNIT, "uTexH"), (ARCH_SEQUENCE_TEXTURE_UNIT, "uTexSequence"),
                           (BNG_DETAIL_TEXTURE_UNIT, "uTexBngDetail"), (VEHICLE_PALETTE_TEXTURE_UNIT, "uTexVehicleP2"),
                           (GLOW_PARALLAX_TEXTURE_UNIT, "uTexGlowParallax")):
            sh.glUniform1i(u[name], unit)
        self._begin_attributes()
        # Uniform values persist in the program, but other code sets them too: start each frame afresh.
        self._uniform_state, self._texture_state, self._active_unit = {}, {}, None
        mode = self._mode
        glass = []
        if mode != "Wireframe":
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
            gl.glEnable(self.GL_POLYGON_OFFSET_FILL); gl.glPolygonOffset(0.0 if shadow else 1.0, 1.0)
            sh.glUniform3f(u["uColor"], 0.72, 0.74, 0.78)
            current = None
            for item in self._draw_list:
                key, model, span, groups = item
                entry = self._materials.get(key[0]) or {}
                if key[1] == 2 and entry.get("forward"):
                    glass.append(item)
                    continue
                self._static_vector_draw_eligible = self._static_vector_eligible_for_model(model)
                next_program, next_u = self._surface_program(entry, key[1], instanced=True)
                if next_program != program:
                    program, u = next_program, next_u
                    sh.glUseProgram(program); self._apply_lighting(u)
                    sh.glUniform3f(u['uColor'], 0.72, 0.74, 0.78)
                    for unit, name in ((0,'uTexC'),(1,'uTexG'),(2,'uTexN'),(PICK_TEXTURE_UNIT,'uTexP'),
                                      (TINT_MASK_TEXTURE_UNIT,'uTexH'),(ARCH_SEQUENCE_TEXTURE_UNIT,'uTexSequence'),
                                      (BNG_DETAIL_TEXTURE_UNIT,'uTexBngDetail'),(VEHICLE_PALETTE_TEXTURE_UNIT,'uTexVehicleP2'),
                                      (TINT_DETAIL_TEXTURE_UNIT,'uTexTintDetail'),(GLOW_PARALLAX_TEXTURE_UNIT,'uTexGlowParallax')):
                        sh.glUniform1i(u[name], unit)
                    self._active_surface_program = program
                    self.native_program_switches += 1
                    self._uniform_state, self._active_unit = {}, None
                    current = None
                if key != current:
                    mode_state = ("glUniform1i", (key[1],))
                    if self._uniform_state.get(u["uMode"]) != mode_state:
                        self._uniform_state[u["uMode"]] = mode_state
                        sh.glUniform1i(u["uMode"], key[1])
                    self._material_state(u, key, entry)
                    current = key
                self._draw_span(model, span, groups)
                self._static_vector_draw_eligible = False
            gl.glDisable(self.GL_POLYGON_OFFSET_FILL)
            program, u = base_program, base_u
            sh.glUseProgram(program)
            if glass and not geometry:
                self._draw_level_glass(glass)
                sh.glUseProgram(program)
                self._texture_state, self._active_unit = {}, None
        if geometry:
            self._static_vector_draw_eligible = False
            self._end_attributes();self._unbind_material_units();sh.glUseProgram(0);return
        wire = mode in ("Wireframe", "Solid + wireframe")
        if wire or self.selected in self._models:
            sh.glUniform1i(u["uMode"], 3)
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_LINE)
            if wire:
                sh.glUniform3f(u["uColor"], *((0.62, 0.78, 0.95) if mode == "Wireframe" else (0.12, 0.13, 0.15)))
                for _key, model, span, groups in self._draw_list:
                    self._draw_span(model, span, groups)
            selected = self._models.get(self.selected)
            if selected is not None and self._visible_groups(selected):
                # Outline the picked model's instances; drawn over everything, like a selection.
                sh.glUniform3f(u["uColor"], 1.0, 0.62, 0.1)   # display-space colour; mode 3 skips the tonemap
                gl.glDisable(self.GL_DEPTH_TEST)
                for first, count in merge_ranges([(b[1], b[2]) for b in selected["batches"]]):
                    self._draw_span(selected, (first, count), self._visible_groups(selected))
                gl.glEnable(self.GL_DEPTH_TEST)
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
        self._end_attributes()
        self._unbind_material_units()
        sh.glUseProgram(0)
        self._static_vector_draw_eligible = False

    _bound_vao = _front = None

    def _draw_span(self, model, span, groups):
        gl, ext = self._gl, self._ext
        first, count = span
        offset = ctypes.c_void_p(first * 12)
        culled = self._cull_counts
        for group in groups:
            instances = culled.get(group[0], group[1])
            if not instances:
                continue
            vao = self._vao(model, group)
            if self._bound_vao != vao:
                ext["glBindVertexArray"](vao)
                self._bound_vao = vao
            # Mirrored instances reverse the winding; keep gl_FrontFacing meaning "outside".
            flip = group[2]
            if self._front != flip:
                gl.glFrontFace(self.GL_CW if flip else self.GL_CCW)
                self._front = flip
            ext["glDrawElementsInstanced"](self.GL_TRIANGLES, count * 3, self.GL_UNSIGNED_INT, offset, instances)

    def _draw_level_glass(self, items):
        """Forward materials after all opaque ones: material blend/cull state, no depth write.
        Instances are not depth-sorted (the model viewer sorts batches of one model only)."""
        from rc2016.viewer.shading import PICK_TEXTURE_UNIT, glass_uniforms
        gl, sh = self._gl, self._shading
        program, u = sh.instanced_program(glass=True)
        sh.glUseProgram(program)
        self._apply_lighting(u)
        sh.glUniform1i(u["uTexN"], 2); sh.glUniform1i(u["uTexP"], PICK_TEXTURE_UNIT)
        gl.glBlendFunc.argtypes = [ctypes.c_uint, ctypes.c_uint]; gl.glDepthMask.argtypes = [ctypes.c_ubyte]
        gl.glCullFace.argtypes = [ctypes.c_uint]
        gl.glCullFace(self.GL_BACK)
        for key, model, span, groups in items:
            entry = self._materials[key[0]]
            forward = entry["forward"]
            blended = forward.get("blend") != "opaque"
            gl.glDepthMask(0 if blended else 1)
            (gl.glEnable if blended else gl.glDisable)(self.GL_BLEND)
            gl.glBlendFunc(self.GL_SRC_ALPHA, self.GL_ONE if forward.get("blend") == "additive" else self.GL_ONE_MINUS_SRC_ALPHA)
            (gl.glDisable if forward.get("two_sided") else gl.glEnable)(self.GL_CULL_FACE)
            for unit, role, has, srgb in ((2, "_n", "uHasN", None), (PICK_TEXTURE_UNIT, "_pick", "uHasP", "uSrgbP")):
                texture = entry.get(role)
                sh.glActiveTexture(self.GL_TEXTURE0 + unit)
                gl.glBindTexture(self.GL_TEXTURE_2D, texture[0] if texture else 0)
                sh.glUniform1i(u[has], 1 if texture else 0)
                if srgb:
                    sh.glUniform1i(u[srgb], 1 if texture and texture[1] else 0)
            if forward.get('kind') in ('screen', 'number_display'):
                self._apply_screen_forward(u, entry)
            kind, *vectors = glass_uniforms(forward)
            sh.glUniform1i(u["uKind"], kind)
            for name, vector in zip(("uA", "uB", "uC", "uD", "uE"), vectors):
                sh.glUniform4f(u[name], *(float(x) for x in vector))
            self._draw_span(model, span, groups)
        gl.glDepthMask(1); gl.glDisable(self.GL_BLEND); gl.glDisable(self.GL_CULL_FACE)

    # Sky ------------------------------------------------------------------------------------
    _near = 0.05
    SKY_CUBE = (-1, -1, -1, 1, -1, -1, 1, 1, -1, -1, 1, -1, -1, -1, 1, 1, -1, 1, 1, 1, 1, -1, 1, 1)
    SKY_CUBE_INDICES = (0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 4, 5, 0, 5, 1,
                        3, 2, 6, 3, 6, 7, 0, 3, 7, 0, 7, 4, 1, 5, 6, 1, 6, 2)

    def _release_sky(self):
        record, self._sky_gl = self._sky_gl, None
        if not record or not self._make_current():
            return
        buffers = [name for dome in record["domes"] for name in dome["buffers"]]
        if buffers:
            self._ext["glDeleteBuffers"](len(buffers), (ctypes.c_uint * len(buffers))(*buffers))
        textures = [name for name in record["textures"].values() if name]
        if textures:
            self._gl.glDeleteTextures(len(textures), (ctypes.c_uint * len(textures))(*textures))

    def _upload_sky(self):
        """Dome vertex/UV/index buffers and the dome and noise textures (once per path)."""
        from array import array
        sky = self._sky
        record = dict(domes=[], textures={},
                      cube=((ctypes.c_float * 24)(*self.SKY_CUBE), (ctypes.c_uint * 36)(*self.SKY_CUBE_INDICES)))
        for path, texture in sky.get("textures", {}).items():
            record["textures"][path] = self._upload_texture(texture, mipmaps=False) if texture else 0
        self._gl.glBindTexture(self.GL_TEXTURE_2D, 0)
        for dome in sky["domes"]:
            geometry = dome["geometry"]
            if not (record["textures"].get(dome["dome_texture"]) and record["textures"].get(dome["noise_texture"])):
                continue   # a texture the driver rejected: draw the clear sky only
            buffers = [self._buffer(self.GL_ARRAY_BUFFER, geometry["positions"]),
                       self._buffer(self.GL_ARRAY_BUFFER, geometry["uvs"]),
                       self._buffer(self.GL_ELEMENT_ARRAY_BUFFER, array("I", geometry["indices"]))]
            self._ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0); self._ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, 0)
            srgb = {path: bool((sky["textures"].get(path) or {}).get("srgb")) for path in (dome["dome_texture"], dome["noise_texture"])}
            record["domes"].append(dict(dome, buffers=buffers, scale=1.0 / max(geometry["radius"], 1e-6), srgb=srgb))
        self._sky_gl = record

    def _draw_sky(self):
        """Clear sky and cloud domes around the camera: camera rotation only, no depth test or write,
        drawn first so the level covers them. Colour goes through the level's tonemap (display())."""
        if self._sky_gl is None:
            self._upload_sky()
        gl, sh, ext, record, sky = self._gl, self._shading, self._ext, self._sky_gl, self._sky
        program, u = sh.sky_program()
        sh.glUseProgram(program)
        self._apply_lighting(u)                       # tonemap and exposure; reads the camera modelview
        gl.glScalef.argtypes = [ctypes.c_float] * 3
        gl.glBlendFunc.argtypes = [ctypes.c_uint, ctypes.c_uint]; gl.glDepthMask.argtypes = [ctypes.c_ubyte]
        radius = math.sqrt(self._near * self._far)    # anywhere between the planes: no depth test
        gl.glPushMatrix()
        gl.glLoadMatrixf((ctypes.c_float * 16)(*view_matrix((0.0, 0.0, 0.0), self._yaw, self._pitch)))
        gl.glMultMatrixf((ctypes.c_float * 16)(*BASIS))
        gl.glDisable(self.GL_DEPTH_TEST); gl.glDepthMask(0); gl.glDisable(self.GL_CULL_FACE)
        ext["glBindVertexArray"](0)
        gl.glEnableClientState(self.GL_VERTEX_ARRAY)
        try:
            if sky.get("clear_sky"):
                from rc2016.levels.sky import PROFILE_STEP
                profile = [float(x) for colour in sky["clear_sky"] for x in colour]
                sh.glUniform1i(u["uSkyMode"], 0)
                sh.glUniform3fv(u["uProfile"], len(profile) // 3, (ctypes.c_float * len(profile))(*profile))
                sh.glUniform1f(u["uProfileStep"], PROFILE_STEP)
                gl.glPushMatrix(); gl.glScalef(radius, radius, radius)
                vertices, indices = record["cube"]
                gl.glVertexPointer(3, self.GL_FLOAT, 0, ctypes.cast(vertices, ctypes.c_void_p))
                gl.glDrawElements(self.GL_TRIANGLES, 36, self.GL_UNSIGNED_INT, ctypes.cast(indices, ctypes.c_void_p))
                gl.glPopMatrix()
            if record["domes"]:
                sh.glUniform1i(u["uSkyMode"], 1)
                sh.glUniform3f(u["uSunRay"], *(float(x) for x in sky["sun_ray"]))
                sh.glUniform1i(u["uDome"], 0); sh.glUniform1i(u["uNoise"], 1)
                gl.glEnable(self.GL_BLEND); gl.glBlendFunc(self.GL_SRC_ALPHA, self.GL_ONE_MINUS_SRC_ALPHA)
                gl.glEnableClientState(self.GL_TEXTURE_COORD_ARRAY)
                for dome in record["domes"]:
                    constants = list(dome["constants"]) + [0.0]
                    for i in range(6):
                        sh.glUniform4f(u[f"uP[{i}]"], *constants[4 * i:4 * i + 4])
                    sh.glUniform1i(u["uDomeSrgb"], dome["srgb"][dome["dome_texture"]])
                    sh.glUniform1i(u["uNoiseSrgb"], dome["srgb"][dome["noise_texture"]])
                    for unit, path in ((0, dome["dome_texture"]), (1, dome["noise_texture"])):
                        sh.glActiveTexture(self.GL_TEXTURE0 + unit)
                        gl.glBindTexture(self.GL_TEXTURE_2D, record["textures"][path])
                    vbo, tbo, ibo = dome["buffers"]
                    ext["glBindBuffer"](self.GL_ARRAY_BUFFER, vbo); gl.glVertexPointer(3, self.GL_FLOAT, 0, None)
                    ext["glBindBuffer"](self.GL_ARRAY_BUFFER, tbo); gl.glTexCoordPointer(2, self.GL_FLOAT, 0, None)
                    ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, ibo)
                    gl.glPushMatrix(); gl.glScalef(*(radius * dome["scale"],) * 3)
                    for first, count in dome["spans"]:
                        gl.glDrawElements(self.GL_TRIANGLES, count * 3, self.GL_UNSIGNED_INT, ctypes.c_void_p(first * 12))
                    gl.glPopMatrix()
        finally:
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0); ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, 0)
            gl.glDisableClientState(self.GL_TEXTURE_COORD_ARRAY); gl.glDisableClientState(self.GL_VERTEX_ARRAY)
            for unit in (1, 0):
                sh.glActiveTexture(self.GL_TEXTURE0 + unit)
                gl.glBindTexture(self.GL_TEXTURE_2D, 0)
            gl.glDisable(self.GL_BLEND); gl.glDepthMask(1); gl.glEnable(self.GL_DEPTH_TEST)
            gl.glPopMatrix()
            sh.glUseProgram(0)

    # Picking -------------------------------------------------------------------------------
    def pick(self, x, y):
        """Model path under window pixel (x, y), or None: each visible model is drawn in an id colour
        into the back buffer, one pixel is read, and the frame is redrawn."""
        self.picked_actor = None
        if not (self._models and self._make_current() and self._shading is not None and self._ext):
            return None
        gl, sh = self._gl, self._shading
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        if not (0 <= x < width and 0 <= y < height):
            return None
        if self._draw_list is None:
            self._build_draw_list()
        gl.glViewport(0, 0, width, height)
        gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        gl.glClear(self.GL_COLOR_BUFFER_BIT | self.GL_DEPTH_BUFFER_BIT)
        self._setup_camera(width, height)
        self._cull_frame(width, height)   # the id pass draws the frame's visible set
        program, u = sh.pick_program()
        sh.glUseProgram(program)
        self._begin_attributes()
        gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
        order = list(self._models)
        index = {path: i for i, path in enumerate(order)}
        current = cut_state = None
        sh.glUniform1i(u["uTexC"], 0)
        sh.glActiveTexture(self.GL_TEXTURE0)
        for key, model, span, groups in self._draw_list:
            if model["path"] != current:
                current = model["path"]
                sh.glUniform3f(u["uPickColour"], *pick_colour(index[current]))
            # Leaf cut-out: same `_c` alpha test as the drawn frame (no texture is bound in Solid mode).
            entry = self._materials.get(key[0]) or {}
            texture = entry.get("_c") if key[1] else None
            state = (float(entry.get("alpha_cut", 0.0)), texture[0]) if texture and entry.get("alpha_cut") else (0.0, 0)
            if state != cut_state:
                cut_state = state
                gl.glBindTexture(self.GL_TEXTURE_2D, state[1])
                sh.glUniform1f(u["uPickCut"], state[0])
            self._draw_span(model, span, groups)
        gl.glBindTexture(self.GL_TEXTURE_2D, 0)
        self._end_attributes()
        sh.glUseProgram(0)
        pixel = (ctypes.c_ubyte * 4)()
        self._gl.glPixelStorei(0x0D05, 1)   # GL_PACK_ALIGNMENT
        gl.glReadBuffer(0x0405)   # GL_BACK
        gl.glReadPixels(x, height - 1 - y, 1, 1, 0x1907, self.GL_UNSIGNED_BYTE, pixel)   # GL_RGB
        found = pick_index(bytes(pixel)[:3])
        path = order[found] if found is not None and found < len(order) else None
        if path and ((self.show_actors and self._models[path]['actors']) or
                     (self.show_spawn_preview and self._models[path]['spawns'])):
            self._pick_actor(self._models[path], x, y, width, height)
        self._draw()
        return path

    def _pick_actor(self, model, x, y, width, height):
        """Second pass for the selected model only; individual IDs cost work only on clicks.

        Static rows use background ID and still write depth. Each actor keeps its full
        matrix and material cut-out. Regular frames continue to use merged instancing.
        """
        from array import array
        gl, sh = self._gl, self._shading
        gl.glClear(self.GL_COLOR_BUFFER_BIT | self.GL_DEPTH_BUFFER_BIT)
        self._setup_camera(width, height)
        program, u = sh.pick_program()
        sh.glUseProgram(program)
        self._begin_attributes()
        sh.glUniform1i(u['uTexC'], 0)
        sh.glActiveTexture(self.GL_TEXTURE0)
        visible = (model['actors'] if self.show_actors else []) + (model['spawns'] if self.show_spawn_preview else [])
        actors = [a for a in visible if a['zone'] not in self._hidden_zones]
        temporary, entries = [], []
        try:
            for zone, key, rows in model['zones']:
                if zone not in self._hidden_zones:
                    buffer = self._buffer(self.GL_ARRAY_BUFFER, rows)
                    temporary.append(buffer)
                    entries.append((None, (buffer, len(rows)//16, *key)))
            for i, actor in enumerate(actors):
                rows = array('f', (v for row in actor['matrix_rows'] for v in row))
                buffer = self._buffer(self.GL_ARRAY_BUFFER, rows)
                temporary.append(buffer)
                entries.append((i, (buffer, 1, *actor['group_key'])))
            # Preserve the regular draw-list's material/batch order and alpha test.
            for key, item_model, span, original_groups in self._draw_list:
                if item_model is not model:
                    continue
                entry = self._materials.get(key[0]) or {}
                texture = entry.get('_c') if key[1] else None
                cut = float(entry.get('alpha_cut', 0.0)) if texture else 0.0
                gl.glBindTexture(self.GL_TEXTURE_2D, texture[0] if cut else 0)
                sh.glUniform1f(u['uPickCut'], cut)
                keys = {(g[2], g[3]) for g in original_groups}
                for index, group in entries:
                    if (group[2], group[3]) not in keys:
                        continue
                    colour = pick_colour(index) if index is not None else (0.,0.,0.)
                    sh.glUniform3f(u['uPickColour'], *colour)
                    self._draw_span(model, span, [group])
            pixel = (ctypes.c_ubyte * 4)()
            gl.glReadBuffer(0x0405)
            gl.glReadPixels(x, height-1-y, 1, 1, 0x1907, self.GL_UNSIGNED_BYTE, pixel)
            index = pick_index(bytes(pixel)[:3])
            if index is not None and index < len(actors):
                self.picked_actor = actors[index]
        finally:
            gl.glBindTexture(self.GL_TEXTURE_2D, 0)
            self._end_attributes()
            sh.glUseProgram(0)
            self._delete_vaos([model])
            if temporary:
                self._ext['glDeleteBuffers'](len(temporary), (ctypes.c_uint * len(temporary))(*temporary))

    def select(self, path):
        """Outline a model's instances (None clears it)."""
        self.selected = path
        self._draw()

    def _end_look(self, event):
        press, self._press = self._press, None
        if press and abs(event.x - press[0]) <= 3 and abs(event.y - press[1]) <= 3:
            path = self.pick(event.x, event.y)
            self.select(path)
            if self.on_pick:
                self.on_pick(path)

    def _double_click(self, event):
        path = self.pick(event.x, event.y)
        if path and self.on_open:
            self.on_open(path)

    def capture_png(self, path):
        """Write the current frame (redrawn now, with every queued material uploaded) to a PNG; True on success."""
        self.flush_materials()
        self._draw()
        if not self._make_current():
            return False
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        pixels = (ctypes.c_ubyte * (width * height * 3))()
        self._gl.glPixelStorei(0x0D05, 1)   # GL_PACK_ALIGNMENT
        self._gl.glReadBuffer.argtypes = [ctypes.c_uint]; self._gl.glReadBuffer(0x0404)   # GL_FRONT
        self._gl.glReadPixels(0, 0, width, height, 0x1907, self.GL_UNSIGNED_BYTE, pixels)   # GL_RGB
        with open(path, "wb") as handle:
            handle.write(png_bytes(width, height, bytes(pixels)))
        return True

    # Camera --------------------------------------------------------------------------------
    def set_camera(self, eye, yaw, pitch, speed=None):
        self._eye, self._yaw, self._pitch = tuple(eye), float(yaw), max(-89.0, min(89.0, float(pitch)))
        if speed:
            self._speed = float(speed)
        self._draw()

    def _reset_camera(self, _event=None):
        if not hasattr(self, "_bounds_native"):   # ModelViewport.__init__ runs before ours
            return
        self._eye, self._yaw, self._pitch, self._speed = overview_camera(self._bounds_native)
        self._schedule_draw()

    def _begin_look(self, event):
        self.focus_set()
        self._drag = (event.x, event.y, self._yaw, self._pitch)
        self._press = (event.x, event.y)   # a release without movement picks (_end_look)

    def _look(self, event):
        if self._drag and len(self._drag) == 4:
            x, y, yaw, pitch = self._drag
            self._yaw = yaw - (event.x - x) * 0.25
            self._pitch = max(-89.0, min(89.0, pitch - (event.y - y) * 0.25))
            self._schedule_draw()

    def _begin_pan(self, event):
        self.focus_set()
        self._drag = (event.x, event.y, self._eye)

    def _pan_drag(self, event):
        if self._drag and len(self._drag) == 3:
            x, y, eye = self._drag
            _f, r, up = look_vectors(self._yaw, self._pitch)
            scale = self._speed * 0.01
            dx, dy = -(event.x - x) * scale, (event.y - y) * scale
            self._eye = tuple(e + r[i] * dx + up[i] * dy for i, e in enumerate(eye))
            self._schedule_draw()

    def _wheel(self, event):
        if event.state & 0x4:   # Ctrl: change fly speed
            self._speed = max(0.05, min(1e5, self._speed * (1.25 if event.delta > 0 else 0.8)))
        else:
            f, _r, _u = look_vectors(self._yaw, self._pitch)
            step = self._speed * (0.5 if event.delta > 0 else -0.5)
            self._eye = tuple(e + fi * step for e, fi in zip(self._eye, f))
        self._schedule_draw()

    def _zoom_by(self, factor):   # Button-4/5 (X11 wheel) from ModelViewport
        f, _r, _u = look_vectors(self._yaw, self._pitch)
        step = self._speed * (0.5 if factor > 1 else -0.5)
        self._eye = tuple(e + fi * step for e, fi in zip(self._eye, f))
        self._schedule_draw()

    def _key_down(self, event):
        key = event.keysym.lower()
        self._release_pending.pop(key, None)
        if key in ("bracketright", "plus", "equal"):
            self._speed *= 1.25
        elif key in ("bracketleft", "minus"):
            self._speed = max(0.05, self._speed * 0.8)
        elif key in ("home", "f"):
            self._reset_camera()
        if key in MOVE_KEYS or key.startswith("shift"):
            self._keys.add(key)
            if self._last_tick is None:
                self._last_tick = time.perf_counter()
                self.after(1, self._tick)

    def _key_up(self, event):
        # Windows auto-repeat can deliver release/press pairs; drop the key only if no press follows.
        key = event.keysym.lower()
        token = object()
        self._release_pending[key] = token
        self.after(40, lambda: self._release_pending.get(key) is token and (self._keys.discard(key), self._release_pending.pop(key, None)))

    def _tick(self):
        moving = [MOVE_KEYS[k] for k in self._keys if k in MOVE_KEYS]
        if not moving:
            self._last_tick = None
            return
        now = time.perf_counter()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        f, r, _u = look_vectors(self._yaw, self._pitch)
        boost = 4.0 if any(k.startswith("shift") for k in self._keys) else 1.0
        step = self._speed * boost * dt
        dx = sum(m[0] for m in moving); dy = sum(m[1] for m in moving); dz = sum(m[2] for m in moving)
        self._eye = tuple(e + (f[i] * dx + r[i] * dy + (1.0 if i == 2 else 0.0) * dz) * step for i, e in enumerate(self._eye))
        self._schedule_draw()
        self.after(8, self._tick)


__all__ = ["LevelViewport", "draw_items", "look_vectors", "merge_ranges", "overview_camera", "pick_colour", "pick_index", "png_bytes", "view_matrix"]
