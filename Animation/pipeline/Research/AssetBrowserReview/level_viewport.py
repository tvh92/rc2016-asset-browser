"""Level viewport: every instance of a zone drawn with one instanced call per model and material.

Builds on ModelViewport's WGL context, texture upload and per-material uniforms. Geometry and
instance matrices stay in native Y-up space (level_scene.py); the camera matrix applies the
presentation basis (x, y, z) -> (x, -z, y), so the fly camera works in Blender-like Z-up.
"""
from __future__ import annotations

import ctypes
import math
import struct
import time
import zlib

from .viewport import ModelViewport

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


class LevelViewport(ModelViewport):
    """Interactive level view. Add models and materials from the Tk thread as they decode."""
    GL_ARRAY_BUFFER, GL_ELEMENT_ARRAY_BUFFER, GL_STATIC_DRAW = 0x8892, 0x8893, 0x88E4
    GL_CW, GL_CCW = 0x0900, 0x0901
    MODES = ("Shaded", "Textured", "Solid", "Wireframe", "Solid + wireframe")
    EXT = {
        "glGenBuffers": (None, [ctypes.c_int, ctypes.c_void_p]),
        "glDeleteBuffers": (None, [ctypes.c_int, ctypes.c_void_p]),
        "glBindBuffer": (None, [ctypes.c_uint, ctypes.c_uint]),
        "glBufferData": (None, [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint]),
        "glVertexAttribDivisor": (None, [ctypes.c_uint, ctypes.c_uint]),
        "glDrawElementsInstanced": (None, [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]),
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
        self._texture_names = {}   # texture path -> (GL id, sRGB) or None
        self._draw_list = None
        self.show_hidden = False
        self.on_stats = None
        self.last_draw_ms = 0.0
        self._bounds_native = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        self._eye, self._yaw, self._pitch, self._speed = (0.0, -50.0, 20.0), 90.0, -20.0, 20.0
        self._far = 10000.0
        self._keys, self._release_pending = set(), {}
        self._last_tick = None
        self.bind("<ButtonPress-1>", self._begin_look)
        self.bind("<B1-Motion>", self._look)
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
        self._gl_call(self._upload_instances, zone, path, groups)

    def set_zone_visible(self, zone, visible):
        (self._hidden_zones.discard if visible else self._hidden_zones.add)(zone)
        self._draw_list = None
        self._draw()

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

    def _visible_groups(self, model):
        if model["hidden"] and not self.show_hidden:
            return ()
        return tuple(g for g in model["groups"] if g[3] not in self._hidden_zones)

    def stats(self):
        counts = [(m["triangles"], sum(g[1] for g in self._visible_groups(m))) for m in self._models.values()]
        return dict(models=len(self._models), instances=sum(n for _t, n in counts),
                    triangles=sum(t * n for t, n in counts),
                    materials=len(self._materials), textures=sum(1 for v in self._texture_names.values() if v),
                    draws=len(self._draw_list or ()), draw_ms=self.last_draw_ms, speed=self._speed,
                    eye=self._eye, yaw=self._yaw, pitch=self._pitch)

    def clear(self, message="Loading level…"):
        if self._make_current():
            buffers = [name for m in self._models.values() for name in m["buffers"]]
            if buffers:
                self._ext["glDeleteBuffers"](len(buffers), (ctypes.c_uint * len(buffers))(*buffers))
            textures = [v[0] for v in self._texture_names.values() if v]
            if textures:
                self._gl.glDeleteTextures(len(textures), (ctypes.c_uint * len(textures))(*textures))
        self._models, self._materials, self._texture_names, self._draw_list = {}, {}, {}, None
        self._hidden_zones = set()
        self._pending, self._pending_materials = [], {}
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
                      ibo=self._buffer(self.GL_ELEMENT_ARRAY_BUFFER, geometry["indices"]), groups=[])
        record["buffers"] = [record[k] for k in ("vbo", "nbo", "tbo", "ibo")]
        self._ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0); self._ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, 0)
        self._models[path] = record

    def _upload_instances(self, zone, path, groups):
        record = self._models[path]
        for flip, rows in sorted(groups.items()):
            buffer = self._buffer(self.GL_ARRAY_BUFFER, rows)
            record["groups"].append((buffer, len(rows) // 16, flip, zone))   # (buffer, count, mirrored, zone)
            record["buffers"].append(buffer)
        self._ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0)

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
        """[(material key, model, batch, instance groups)] sorted by material so uniforms change once per material."""
        level = self._level()
        items = []
        for model in self._models.values():
            groups = self._visible_groups(model)
            if not groups:
                continue
            for batch in model["batches"]:
                material = model["slots"].get(batch[0]) if batch[3] else None
                items.append(((material or "", level if batch[3] else 0), model, batch, groups))
        items.sort(key=lambda item: (item[0], item[1]["path"]))
        self._draw_list = items

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
            # Near plane follows fly speed: fine detail up close, less depth fighting when flying fast.
            near = max(0.05, min(2.0, self._speed * 0.004))
            top = near * math.tan(math.radians(FOV_DEGREES) * 0.5)
            gl.glMatrixMode(self.GL_PROJECTION); gl.glLoadIdentity()
            gl.glFrustum(-top * width / height, top * width / height, -top, top, near, self._far)
            gl.glMatrixMode(self.GL_MODELVIEW)
            gl.glLoadMatrixf((ctypes.c_float * 16)(*view_matrix(self._eye, self._yaw, self._pitch)))
            gl.glMultMatrixf((ctypes.c_float * 16)(*BASIS))
            if self._draw_list is None:
                self._build_draw_list()
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

    def _draw_level(self):
        from .gl_shading import (ARCH_SEQUENCE_TEXTURE_UNIT, BNG_DETAIL_TEXTURE_UNIT, INSTANCE_ATTRIBUTES, PICK_TEXTURE_UNIT,
                                 TINT_DETAIL_TEXTURE_UNIT, TINT_MASK_TEXTURE_UNIT, VEHICLE_PALETTE_TEXTURE_UNIT,
                                 GLOW_PARALLAX_TEXTURE_UNIT)
        gl, sh = self._gl, self._shading
        program, u = sh.instanced_program()
        sh.glUseProgram(program)
        for unit, name in ((TINT_DETAIL_TEXTURE_UNIT, "uTexTintDetail"), (0, "uTexC"), (1, "uTexG"), (2, "uTexN"),
                           (PICK_TEXTURE_UNIT, "uTexP"), (TINT_MASK_TEXTURE_UNIT, "uTexH"), (ARCH_SEQUENCE_TEXTURE_UNIT, "uTexSequence"),
                           (BNG_DETAIL_TEXTURE_UNIT, "uTexBngDetail"), (VEHICLE_PALETTE_TEXTURE_UNIT, "uTexVehicleP2"),
                           (GLOW_PARALLAX_TEXTURE_UNIT, "uTexGlowParallax")):
            sh.glUniform1i(u[name], unit)
        gl.glEnableClientState(self.GL_VERTEX_ARRAY); gl.glEnableClientState(self.GL_NORMAL_ARRAY)
        gl.glEnableClientState(self.GL_TEXTURE_COORD_ARRAY)
        locations = [INSTANCE_ATTRIBUTES[f"aInst{i}"] for i in range(4)]
        for location in locations:
            sh.glEnableVertexAttribArray(location)
            self._ext["glVertexAttribDivisor"](location, 1)
        mode = self._mode
        glass = []
        if mode != "Wireframe":
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
            gl.glEnable(self.GL_POLYGON_OFFSET_FILL); gl.glPolygonOffset(1.0, 1.0)
            sh.glUniform3f(u["uColor"], 0.72, 0.74, 0.78)
            current = None
            for item in self._draw_list:
                key, model, batch, groups = item
                entry = self._materials.get(key[0]) or {}
                if key[1] == 2 and entry.get("forward"):
                    glass.append(item)
                    continue
                if key != current:
                    sh.glUniform1i(u["uMode"], key[1])
                    self._apply_material(u, entry, key[1])
                    current = key
                self._draw_batch(model, batch, groups, locations)
            gl.glDisable(self.GL_POLYGON_OFFSET_FILL)
            if glass:
                self._draw_level_glass(glass, locations)
                sh.glUseProgram(program)
        if mode in ("Wireframe", "Solid + wireframe"):
            sh.glUniform1i(u["uMode"], 3)
            sh.glUniform3f(u["uColor"], *((0.62, 0.78, 0.95) if mode == "Wireframe" else (0.12, 0.13, 0.15)))
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_LINE)
            for _key, model, batch, groups in self._draw_list:
                self._draw_batch(model, batch, groups, locations)
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
        for location in locations:
            self._ext["glVertexAttribDivisor"](location, 0)
            sh.glDisableVertexAttribArray(location)
        gl.glFrontFace(self.GL_CCW)
        gl.glDisableClientState(self.GL_TEXTURE_COORD_ARRAY)
        gl.glDisableClientState(self.GL_NORMAL_ARRAY); gl.glDisableClientState(self.GL_VERTEX_ARRAY)
        self._ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0); self._ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, 0)
        self._bound_model = None
        self._unbind_material_units()
        sh.glUseProgram(0)

    _bound_model = _bound_group = None

    def _draw_batch(self, model, batch, groups, locations):
        gl, ext, sh = self._gl, self._ext, self._shading
        if self._bound_model is not model:
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, model["vbo"]); gl.glVertexPointer(3, self.GL_FLOAT, 0, None)
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, model["nbo"]); gl.glNormalPointer(self.GL_FLOAT, 0, None)
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, model["tbo"]); gl.glTexCoordPointer(2, self.GL_FLOAT, 0, None)
            ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, model["ibo"])
            self._bound_model = model
            self._bound_group = None
        _slot, first, count, _uv = batch
        offset = ctypes.c_void_p(first * 12)
        for group in groups:
            buffer, instances, flip, _zone = group
            if self._bound_group is not group:
                ext["glBindBuffer"](self.GL_ARRAY_BUFFER, buffer)
                for row, location in enumerate(locations):
                    sh.glVertexAttribPointer(location, 4, self.GL_FLOAT, 0, 64, ctypes.c_void_p(16 * row))
                self._bound_group = group
            # Mirrored instances reverse the winding; keep gl_FrontFacing meaning "outside".
            gl.glFrontFace(self.GL_CW if flip else self.GL_CCW)
            ext["glDrawElementsInstanced"](self.GL_TRIANGLES, count * 3, self.GL_UNSIGNED_INT, offset, instances)

    def _draw_level_glass(self, items, locations):
        """Forward materials after all opaque ones: material blend/cull state, no depth write.
        Instances are not depth-sorted (the model viewer sorts batches of one model only)."""
        from .gl_shading import PICK_TEXTURE_UNIT, glass_uniforms
        gl, sh = self._gl, self._shading
        program, u = sh.instanced_program(glass=True)
        sh.glUseProgram(program)
        sh.glUniform1i(u["uTexN"], 2); sh.glUniform1i(u["uTexP"], PICK_TEXTURE_UNIT)
        gl.glBlendFunc.argtypes = [ctypes.c_uint, ctypes.c_uint]; gl.glDepthMask.argtypes = [ctypes.c_ubyte]
        gl.glCullFace.argtypes = [ctypes.c_uint]
        gl.glCullFace(self.GL_BACK)
        for key, model, batch, groups in items:
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
            self._draw_batch(model, batch, groups, locations)
        gl.glDepthMask(1); gl.glDisable(self.GL_BLEND); gl.glDisable(self.GL_CULL_FACE)

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


__all__ = ["LevelViewport", "look_vectors", "overview_camera", "view_matrix", "png_bytes"]
