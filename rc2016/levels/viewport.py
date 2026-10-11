"""Level viewport: all visible instances of a model drawn with one instanced call per material.

Builds on ModelViewport's WGL context, texture upload and per-material uniforms. Geometry and
instance matrices stay in native Y-up space (level_scene.py); the camera matrix applies the
presentation basis (x, y, z) -> (x, -z, y), so the fly camera works in Blender-like Z-up.
"""
from __future__ import annotations

import ctypes
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
    the previous material's."""
    GL_ARRAY_BUFFER, GL_ELEMENT_ARRAY_BUFFER, GL_STATIC_DRAW = 0x8892, 0x8893, 0x88E4
    GL_CW, GL_CCW = 0x0900, 0x0901
    MODES = ("Shaded", "Textured", "Solid", "Wireframe", "Solid + wireframe")
    # Native Y-up objects; the rig stays fixed to the world, so lighting does not turn with the camera.
    OBJECT_TO_WORLD = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
    RIG_FIXED_TO_WORLD = True
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

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
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

    def add_instances(self, zone, path, groups):
        """One zone's instances of an uploaded model: {mirrored: array('f') of 16-float rows}."""
        self._gl_call(self._store_instances, zone, path, groups)

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
        self._draw()

    def set_zone_visible(self, zone, visible):
        (self._hidden_zones.discard if visible else self._hidden_zones.add)(zone)
        self._draw_list = None
        self._draw()

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
        if not self._make_current():
            self._pending_materials.update(entries)
            return
        for path, entry in entries.items():
            if path not in self._materials:
                self._materials[path] = self._gl_entry(entry, self._texture_names)
                # Early frames can record an empty entry before its textures arrive.
                # Invalidate every mode/program variant of that material.
                for key in [k for k in self._material_ops if k[0] == path]:
                    self._material_ops.pop(key)
        self._gl.glBindTexture(self.GL_TEXTURE_2D, 0)
        self._draw_list = None
        self._schedule_draw()

    def set_mode(self, mode):
        self._draw_list = None   # material keys carry the shading level
        super().set_mode(mode)

    def set_show_hidden(self, show):
        self.show_hidden = bool(show)
        self._draw_list = None
        self._draw()

    def set_material_overrides(self, enabled):
        """Draw instances with (True) or without their per-instance material overrides."""
        self.material_overrides = bool(enabled)
        self._draw_list = None
        self._draw()

    def set_sky(self, sky):
        """The level's sky (sky.level_sky with a 'textures' {path: load_texture} map), or None."""
        self._release_sky()
        self._sky = sky
        self._schedule_draw()

    def set_show_sky(self, show):
        self.show_sky = bool(show)
        self._draw()

    def _visible_groups(self, model):
        if model["hidden"] and not self.show_hidden:
            return ()
        return model["groups"]

    def stats(self):
        counts = [(m["triangles"], sum(g[1] for g in self._visible_groups(m))) for m in self._models.values()]
        actors = sum(len(m['actors']) - sum(a['zone'] in self._hidden_zones for a in m['actors'])
                     for m in self._models.values() if not m['hidden'] or self.show_hidden) if self.show_actors else 0
        spawns = sum(sum(a['zone'] not in self._hidden_zones for a in m.get('spawns', []))
                     for m in self._models.values() if not m['hidden'] or self.show_hidden) if self.show_spawn_preview else 0
        return dict(actors=actors, spawns=spawns, models=len(self._models), instances=sum(n for _t, n in counts),
                    triangles=sum(t * n for t, n in counts),
                    materials=len(self._materials), textures=sum(1 for v in self._texture_names.values() if v),
                    draws=len(self._draw_list or ()), draw_ms=self.last_draw_ms, speed=self._speed,
                    eye=self._eye, yaw=self._yaw, pitch=self._pitch,
                    native_translation=self.native_translation, program_switches=self.native_program_switches)

    def clear(self, message="Loading level…"):
        if self._make_current():
            buffers = [name for m in self._models.values() for name in m["buffers"] + [g[0] for g in m["groups"]]]
            if buffers:
                self._ext["glDeleteBuffers"](len(buffers), (ctypes.c_uint * len(buffers))(*buffers))
            self._delete_vaos(list(self._models.values()))
            textures = [v[0] for v in self._texture_names.values() if v]
            if textures:
                self._gl.glDeleteTextures(len(textures), (ctypes.c_uint * len(textures))(*textures))
        self._release_sky()
        self._sky = None
        self._models, self._materials, self._texture_names, self._draw_list = {}, {}, {}, None
        self._material_ops = {}
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
        record = dict(path=path, hidden=hidden, batches=geometry["batches"], triangles=geometry["triangles"],
                      slots={m["index"]: m["path"] for m in geometry["materials"]},
                      vbo=self._buffer(self.GL_ARRAY_BUFFER, geometry["positions"]),
                      nbo=self._buffer(self.GL_ARRAY_BUFFER, geometry["normals"]),
                      tbo=self._buffer(self.GL_ARRAY_BUFFER, geometry["uvs"]),
                      ibo=self._buffer(self.GL_ELEMENT_ARRAY_BUFFER, geometry["indices"]),
                      zones=[], actor_zones=[], actors=[], spawn_zones=[], spawns=[], groups=[], groups_for=None)
        record["buffers"] = [record[k] for k in ("vbo", "nbo", "tbo", "ibo")]
        self._ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0); self._ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, 0)
        self._models[path] = record

    def _store_instances(self, zone, path, groups):
        """Keep one zone's instance rows, {(mirrored, material changes): rows}; the GPU buffers merge
        every visible zone (_merge_groups)."""
        record = self._models[path]
        for key, rows in sorted(groups.items()):
            record["zones"].append((zone, key, rows))
        record["groups_for"] = None

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
        """One instance buffer per model, handedness and set of material changes over the visible zones:
        [(buffer, count, mirrored, changes)]."""
        from array import array
        hidden = frozenset(self._hidden_zones)
        preview = getattr(self, 'show_spawn_preview', False)
        state = (hidden, self.show_actors, preview)
        for model in self._models.values():
            if model["groups_for"] == state:
                continue
            old = [g[0] for g in model["groups"]]
            if old:
                self._ext["glDeleteBuffers"](len(old), (ctypes.c_uint * len(old))(*old))
            self._delete_vaos([model])
            merged = {}
            layers = model["zones"] + (model["actor_zones"] if self.show_actors else [])
            if preview:
                layers += model.get("spawn_zones", [])
            for zone, key, rows in layers:
                if zone not in hidden:
                    merged.setdefault(key, array("f")).extend(rows)
            model["groups"] = [(self._buffer(self.GL_ARRAY_BUFFER, rows), len(rows) // 16, flip, changes)
                               for (flip, changes), rows in sorted(merged.items())]
            model["groups_for"] = state
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
        """Draw items sorted by material so uniforms change once per material (draw_items)."""
        self._merge_groups()
        self._draw_list = draw_items(self._models.values(), self._level(), self._visible_groups,
                                     self.material_overrides)
        # Keep all draws of one native program together, then retain material/model ordering.
        def program_key(item):
            key = item[0]; entry = self._materials.get(key[0]) or {}
            t = entry.get('native_translation') if self.native_translation and key[1] == 2 else None
            return (t['translation_sha256'] if t else '', key, item[1]['path'])
        self._draw_list.sort(key=program_key)

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
        if self._models and self._shading is not None and self._ext:
            self._setup_camera(width, height)
            if self._draw_list is None:
                self._build_draw_list()
            if self._sky and self.show_sky and self._mode in ("Shaded", "Textured"):
                try:
                    self._draw_sky()
                except Exception as exc:   # the level still draws on the flat background
                    print(f"level viewer: sky failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    self._release_sky()
                    self._sky = None
            try:
                self._draw_level()
            except Exception as exc:   # report instead of failing every Tk repaint
                self.shading_error = f"Level view failed: {exc}"
                self._error = self.shading_error
                self._show_error(self._error)
                self._models = {}
        self.last_draw_ms = (time.perf_counter() - started) * 1000.0
        self._gdi32.SwapBuffers(self._hdc)
        if self.on_stats:
            self.on_stats(self.stats())

    def _begin_attributes(self):
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

    def _draw_level(self):
        from rc2016.viewer.shading import (ARCH_SEQUENCE_TEXTURE_UNIT, BNG_DETAIL_TEXTURE_UNIT, PICK_TEXTURE_UNIT,
                                 TINT_DETAIL_TEXTURE_UNIT, TINT_MASK_TEXTURE_UNIT, VEHICLE_PALETTE_TEXTURE_UNIT,
                                 GLOW_PARALLAX_TEXTURE_UNIT)
        gl, sh = self._gl, self._shading
        program, u = sh.instanced_program()
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
            gl.glEnable(self.GL_POLYGON_OFFSET_FILL); gl.glPolygonOffset(1.0, 1.0)
            sh.glUniform3f(u["uColor"], 0.72, 0.74, 0.78)
            current = None
            for item in self._draw_list:
                key, model, span, groups = item
                entry = self._materials.get(key[0]) or {}
                if key[1] == 2 and entry.get("forward"):
                    glass.append(item)
                    continue
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
            gl.glDisable(self.GL_POLYGON_OFFSET_FILL)
            program, u = base_program, base_u
            sh.glUseProgram(program)
            if glass:
                self._draw_level_glass(glass)
                sh.glUseProgram(program)
                self._texture_state, self._active_unit = {}, None
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

    _bound_vao = _front = None

    def _draw_span(self, model, span, groups):
        gl, ext = self._gl, self._ext
        first, count = span
        offset = ctypes.c_void_p(first * 12)
        for group in groups:
            vao = self._vao(model, group)
            if self._bound_vao != vao:
                ext["glBindVertexArray"](vao)
                self._bound_vao = vao
            # Mirrored instances reverse the winding; keep gl_FrontFacing meaning "outside".
            flip = group[2]
            if self._front != flip:
                gl.glFrontFace(self.GL_CW if flip else self.GL_CCW)
                self._front = flip
            ext["glDrawElementsInstanced"](self.GL_TRIANGLES, count * 3, self.GL_UNSIGNED_INT, offset, group[1])

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
            if forward.get('kind') == 'screen':
                self._apply_screen_forward(u, entry)
            kind, *vectors = glass_uniforms(forward)
            sh.glUniform1i(u["uKind"], kind)
            for name, vector in zip(("uA", "uB", "uC", "uD"), vectors):
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
            record["textures"][path] = self._upload_texture(texture) if texture else 0
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
        """Write the current frame (redrawn now) to a PNG; True on success."""
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
        self._draw()

    def _begin_look(self, event):
        self.focus_set()
        self._drag = (event.x, event.y, self._yaw, self._pitch)
        self._press = (event.x, event.y)   # a release without movement picks (_end_look)

    def _look(self, event):
        if self._drag and len(self._drag) == 4:
            x, y, yaw, pitch = self._drag
            self._yaw = yaw - (event.x - x) * 0.25
            self._pitch = max(-89.0, min(89.0, pitch - (event.y - y) * 0.25))
            self._draw()

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
            self._draw()

    def _wheel(self, event):
        if event.state & 0x4:   # Ctrl: change fly speed
            self._speed = max(0.05, min(1e5, self._speed * (1.25 if event.delta > 0 else 0.8)))
        else:
            f, _r, _u = look_vectors(self._yaw, self._pitch)
            step = self._speed * (0.5 if event.delta > 0 else -0.5)
            self._eye = tuple(e + fi * step for e, fi in zip(self._eye, f))
        self._draw()

    def _zoom_by(self, factor):   # Button-4/5 (X11 wheel) from ModelViewport
        f, _r, _u = look_vectors(self._yaw, self._pitch)
        step = self._speed * (0.5 if factor > 1 else -0.5)
        self._eye = tuple(e + fi * step for e, fi in zip(self._eye, f))
        self._draw()

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
        self._draw()
        self.after(8, self._tick)


__all__ = ["LevelViewport", "draw_items", "look_vectors", "merge_ranges", "overview_camera", "pick_colour", "pick_index", "png_bytes", "view_matrix"]
