"""Small Tk-hosted Windows WGL mesh preview; no external graphics packages."""
from __future__ import annotations

import ctypes
import math
import os
try:
    import tkinter as tk
except ImportError:  # Blender's bundled Python may omit Tk; keep pure math importable.
    tk = None
from ctypes import wintypes

_TkFrame = tk.Frame if tk is not None else object


class MeshPayloadError(ValueError):
    pass


# Viewer rig space -> Blender world (Z up) when the rig is fixed to the world: rig x (right) stays
# +X, rig y (up) is +Z and rig z (towards the viewer) is -Y, as seen from a camera looking along +Y.
RIG_TO_WORLD = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))

# texture_preview entry roles uploaded as 2D GL textures, deduplicated by texture path. Other textures
# in an entry (tint palette, screen timeline, puddle and RockBlend sources) are only sampled on the CPU.
GL_TEXTURE_ROLES = ("_c", "_g", "_n", "_pick", "_h", "_sequence", "_tint_detail", "_bng_detail", "_vehicle_p2",
                    "_glow_parallax", "_puddle_atlas", "_parallax_height")


def _mat3_mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def light_frames(modelview, object_to_world=None, rig_fixed_to_world=False):
    """(view -> Blender world, rig -> view) as row-major 3x3 tuples.

    ``modelview`` is GL's column-major modelview matrix (object -> view); ``object_to_world`` maps
    object space to Blender world space (None: the object space is Blender's Z-up world, as the
    model viewer's presentation-basis vertices). Uniform scale in the modelview is removed."""
    rotation = [[modelview[j * 4 + i] for j in range(3)] for i in range(3)]   # object -> view, rows
    scale = math.sqrt(sum(x * x for x in rotation[0])) or 1.0
    view_to_object = tuple(tuple(rotation[j][i] / scale for j in range(3)) for i in range(3))   # transpose / s
    view_to_world = _mat3_mul(object_to_world, view_to_object) if object_to_world else view_to_object
    if not rig_fixed_to_world:
        return view_to_world, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    world_to_view = tuple(tuple(view_to_world[j][i] for j in range(3)) for i in range(3))
    return view_to_world, _mat3_mul(world_to_view, RIG_TO_WORLD)


def column_major3(matrix):
    """Row-major 3x3 -> the 9 floats glUniformMatrix3fv reads without transposing."""
    return [matrix[i][j] for j in range(3) for i in range(3)]


def parallax_preview_parameters_valid(params):
    """Explicit preview policy for parameters whose native execution is undefined/uncontrolled."""
    values = [float(params.get(k, d)) for k, d in
              (('height_scale', 0.), ('steps_front', 16.), ('steps_grazing', 64.), ('fade', 1.))]
    return all(math.isfinite(v) for v in values) and all(0. < v < 2147483647. for v in values[1:3])


def _triples(values, label, *, allow_flat=False):
    """Normalize a sequence/array into finite 3-tuples without a NumPy dependency."""
    try:
        rows = values.tolist() if hasattr(values, "tolist") else values
        if allow_flat and rows and not isinstance(rows[0], (list, tuple)):
            if len(rows) % 3:
                raise MeshPayloadError(f"{label} must contain triples")
            rows = [rows[i:i + 3] for i in range(0, len(rows), 3)]
        result = tuple(tuple(float(x) for x in row) for row in rows)
    except MeshPayloadError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise MeshPayloadError(f"{label} must be an array of 3D triples") from exc
    if any(len(row) != 3 for row in result):
        raise MeshPayloadError(f"{label} must contain 3D triples")
    if any(not math.isfinite(x) for row in result for x in row):
        raise MeshPayloadError(f"{label} contains non-finite coordinates")
    return result


def validate_mesh(payload):
    """Return immutable vertices, triangle indices, and normalized vertex normals."""
    if not isinstance(payload, dict):
        raise MeshPayloadError("Preview unavailable: expected a mesh payload")
    try:
        vertices = _triples(payload["vertices"], "vertices", allow_flat=True)
        raw_triangles = payload["triangles"]
    except KeyError as exc:
        raise MeshPayloadError(f"Preview unavailable: missing {exc.args[0]}") from exc
    except TypeError as exc:
        raise MeshPayloadError("Preview unavailable: expected a mesh payload") from exc
    if not vertices:
        raise MeshPayloadError("Preview unavailable: mesh has no vertices")
    try:
        triangles = raw_triangles.tolist() if hasattr(raw_triangles, "tolist") else raw_triangles
        converted = []
        for tri in triangles:
            row = []
            for index in tri:
                value = int(index)
                if value != index:
                    raise ValueError("non-integral index")
                row.append(value)
            converted.append(tuple(row))
        triangles = tuple(converted)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MeshPayloadError(f"triangles must contain integer index triples: {exc}") from exc
    if not triangles or any(len(tri) != 3 for tri in triangles):
        raise MeshPayloadError("Preview unavailable: mesh has no triangle faces")
    if any(i < 0 or i >= len(vertices) for tri in triangles for i in tri):
        raise MeshPayloadError("triangle index is outside the vertex array")
    supplied = payload.get("normals")
    if supplied is not None:
        normals = _triples(supplied, "normals", allow_flat=True)
        if len(normals) != len(vertices):
            raise MeshPayloadError("normals must match the vertex count")
        normalized = []
        for normal in normals:
            length = math.sqrt(sum(v * v for v in normal))
            normalized.append(tuple(v / length for v in normal) if length > 1e-20 else (0.0, 0.0, 1.0))
        normals = tuple(normalized)
    else:
        accum = [[0.0, 0.0, 0.0] for _ in vertices]
        for a, b, c in triangles:
            va, vb, vc = vertices[a], vertices[b], vertices[c]
            ab = tuple(vb[k] - va[k] for k in range(3))
            ac = tuple(vc[k] - va[k] for k in range(3))
            n = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
            for index in (a, b, c):
                for axis in range(3):
                    accum[index][axis] += n[axis]
        normals = tuple(
            tuple(value / math.sqrt(sum(x * x for x in normal)) for value in normal)
            if sum(x * x for x in normal) > 1e-40 else (0.0, 0.0, 1.0)
            for normal in accum
        )
    return vertices, triangles, normals


def mesh_bounds(vertices):
    """Return ((center xyz), bounding radius), including a safe radius for points."""
    if not vertices:
        raise MeshPayloadError("Preview unavailable: mesh has no vertices")
    low = tuple(min(v[i] for v in vertices) for i in range(3))
    high = tuple(max(v[i] for v in vertices) for i in range(3))
    center = tuple((low[i] + high[i]) * 0.5 for i in range(3))
    radius = max(math.sqrt(sum((v[i] - center[i]) ** 2 for i in range(3))) for v in vertices)
    return center, max(radius, 1e-5)


def camera_fit(vertices, aspect=1.0, zoom=1.0, fov_degrees=45.0, bounds=None):
    """Camera values that contain the full bounds and valid perspective clip planes."""
    if not math.isfinite(aspect) or aspect <= 0:
        raise ValueError("aspect must be positive and finite")
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("zoom must be positive and finite")
    if not 1.0 < fov_degrees < 179.0:
        raise ValueError("field of view must be between 1 and 179 degrees")
    center, radius = bounds or mesh_bounds(vertices)
    half_angle = math.radians(fov_degrees) * 0.5
    limiting_angle = min(half_angle, math.atan(math.tan(half_angle) * aspect))
    distance = radius * 1.1 / math.sin(limiting_angle) / zoom
    near = max(radius * 0.01, distance - radius * 1.5)
    far = max(near * 2.0, distance + radius * 1.5)
    half_height = near * math.tan(half_angle)
    half_width = half_height * aspect
    return {"center": center, "radius": radius, "distance": distance,
            "near": near, "far": far, "left": -half_width, "right": half_width,
            "bottom": -half_height, "top": half_height}


def perspective_matrix(camera):
    """Return a row-major OpenGL perspective matrix for camera_fit output."""
    left, right = camera["left"], camera["right"]
    bottom, top = camera["bottom"], camera["top"]
    near, far = camera["near"], camera["far"]
    if right <= left or top <= bottom or near <= 0 or far <= near:
        raise ValueError("invalid perspective frustum")
    return (
        (2 * near / (right - left), 0.0, (right + left) / (right - left), 0.0),
        (0.0, 2 * near / (top - bottom), (top + bottom) / (top - bottom), 0.0),
        (0.0, 0.0, -(far + near) / (far - near), -(2 * far * near) / (far - near)),
        (0.0, 0.0, -1.0, 0.0),
    )



def split_forward(batches, textures):
    """(opaque, forward) batch lists; forward batches have a texture_preview 'forward' entry (glass)."""
    opaque, forward = [], []
    for batch in batches:
        (forward if ((textures or {}).get(batch[0]) or {}).get("forward") else opaque).append(batch)
    return opaque, forward


def batch_centres(vertices, triangles, batches):
    """{(first, count): centroid} of each batch's triangle vertices (rest pose)."""
    centres = {}
    if hasattr(vertices, "shape") and hasattr(triangles, "shape"):   # NumPy arrays from prepare_viewport
        for _slot, first, count in batches:
            points = vertices[triangles[first:first + count].reshape(-1)]
            if len(points):
                centres[(first, count)] = tuple(float(x) for x in points.sum(axis=0) / len(points))
        return centres
    for _slot, first, count in batches:
        points = [vertices[i] for tri in triangles[first:first + count] for i in tri]
        if points:
            centres[(first, count)] = tuple(sum(p[k] for p in points) / len(points) for k in range(3))
    return centres


def back_to_front(batches, centres, modelview):
    """Batches sorted farthest first by view-space depth; ``modelview`` is GL's column-major 4x4."""
    m = modelview
    def depth(batch):
        x, y, z = centres.get((batch[1], batch[2]), (0.0, 0.0, 0.0))
        return m[2] * x + m[6] * y + m[10] * z + m[14]   # view z: more negative is farther
    return sorted(batches, key=depth)


def _prepare_viewport_numpy(payload, np):
    """prepare_viewport with NumPy arrays: the same checks and GL arrays as validate_mesh,
    ~20x faster on large meshes. None means "use the reference path" (which also raises
    validate_mesh's exact error for an invalid payload)."""
    try:
        vertices = np.asarray(payload["vertices"], dtype=np.float64)
        triangles = np.asarray(payload["triangles"])
    except (KeyError, TypeError, ValueError):
        return None
    if vertices.ndim == 1 and vertices.size and vertices.size % 3 == 0:
        vertices = vertices.reshape(-1, 3)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices) or not np.isfinite(vertices).all()
            or triangles.ndim != 2 or triangles.shape[1] != 3 or not len(triangles)):
        return None
    if triangles.dtype.kind == "f":
        if not np.isfinite(triangles).all() or (triangles != np.round(triangles)).any():
            return None
    elif triangles.dtype.kind not in "iu":
        return None
    triangles = triangles.astype(np.int64)
    if triangles.min() < 0 or triangles.max() >= len(vertices):
        return None
    supplied = payload.get("normals")
    if supplied is not None:
        try:
            normals = np.asarray(supplied, dtype=np.float64)
        except (TypeError, ValueError):
            return None
        if normals.ndim == 1 and normals.size % 3 == 0:
            normals = normals.reshape(-1, 3)
        if normals.shape != vertices.shape or not np.isfinite(normals).all():
            return None
        length = np.sqrt(normals[:, 0] ** 2 + normals[:, 1] ** 2 + normals[:, 2] ** 2)
        keep = length > 1e-20
        normals = np.where(keep[:, None], normals / np.where(keep, length, 1.0)[:, None], (0.0, 0.0, 1.0))
    else:
        a, b, c = (vertices[triangles[:, k]] for k in range(3))
        face = np.cross(b - a, c - a)
        accum = np.zeros_like(vertices)
        for k in range(3):
            np.add.at(accum, triangles[:, k], face)
        square = accum[:, 0] ** 2 + accum[:, 1] ** 2 + accum[:, 2] ** 2
        keep = square > 1e-40
        normals = np.where(keep[:, None], accum / np.sqrt(np.where(keep, square, 1.0))[:, None], (0.0, 0.0, 1.0))
    floats = lambda values: (ctypes.c_float * values.size).from_buffer_copy(np.ascontiguousarray(values, dtype=np.float32))
    uvs = payload.get("uvs")
    uv_array = floats(np.asarray(uvs, dtype=np.float64)) if uvs and len(uvs) == len(vertices) else None
    joints, weights = payload.get("skin_joints") or [], payload.get("skin_weights") or []
    skinned = len(joints) == len(weights) == 8 * len(vertices) and any(weights)
    arrays = (floats(vertices), floats(normals),
              (ctypes.c_uint * triangles.size).from_buffer_copy(np.ascontiguousarray(triangles, dtype=np.uint32)),
              uv_array,
              floats(np.asarray(joints, dtype=np.float64)) if skinned else None,
              floats(np.asarray(weights, dtype=np.float64)) if skinned else None)
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    center = (low + high) * 0.5
    offset = vertices - center
    radius = float(np.sqrt(offset[:, 0] ** 2 + offset[:, 1] ** 2 + offset[:, 2] ** 2).max())
    bounds = (tuple(float(x) for x in center), max(radius, 1e-5))
    return (vertices, triangles, normals), arrays, bounds


def prepare_viewport(payload):
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None and isinstance(payload, dict):
        prepared = _prepare_viewport_numpy(payload, np)
        if prepared is not None:
            return prepared
    mesh=validate_mesh(payload)
    vertices,triangles,normals=mesh
    flat_vertices=[x for row in vertices for x in row]
    flat_normals=[x for row in normals for x in row]
    flat_indices=[i for tri in triangles for i in tri]
    uvs=payload.get("uvs")
    flat_uvs=[x for row in uvs for x in row] if uvs and len(uvs)==len(vertices) else []
    joints,weights=payload.get("skin_joints") or [],payload.get("skin_weights") or []
    skinned=len(joints)==len(weights)==8*len(vertices) and any(weights)
    arrays=((ctypes.c_float*len(flat_vertices))(*flat_vertices),
            (ctypes.c_float*len(flat_normals))(*flat_normals),
            (ctypes.c_uint*len(flat_indices))(*flat_indices),
            (ctypes.c_float*len(flat_uvs))(*flat_uvs) if flat_uvs else None,
            (ctypes.c_float*len(joints))(*joints) if skinned else None,
            (ctypes.c_float*len(weights))(*weights) if skinned else None)
    return mesh,arrays,mesh_bounds(vertices)

class _PIXELFORMATDESCRIPTOR(ctypes.Structure):
    _fields_ = [("nSize", wintypes.WORD), ("nVersion", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("iPixelType", ctypes.c_ubyte),
                ("cColorBits", ctypes.c_ubyte), ("cRedBits", ctypes.c_ubyte),
                ("cRedShift", ctypes.c_ubyte), ("cGreenBits", ctypes.c_ubyte),
                ("cGreenShift", ctypes.c_ubyte), ("cBlueBits", ctypes.c_ubyte),
                ("cBlueShift", ctypes.c_ubyte), ("cAlphaBits", ctypes.c_ubyte),
                ("cAlphaShift", ctypes.c_ubyte), ("cAccumBits", ctypes.c_ubyte),
                ("cAccumRedBits", ctypes.c_ubyte), ("cAccumGreenBits", ctypes.c_ubyte),
                ("cAccumBlueBits", ctypes.c_ubyte), ("cAccumAlphaBits", ctypes.c_ubyte),
                ("cDepthBits", ctypes.c_ubyte), ("cStencilBits", ctypes.c_ubyte),
                ("cAuxBuffers", ctypes.c_ubyte), ("iLayerType", ctypes.c_ubyte),
                ("bReserved", ctypes.c_ubyte), ("dwLayerMask", wintypes.DWORD),
                ("dwVisibleMask", wintypes.DWORD), ("dwDamageMask", wintypes.DWORD)]


class ModelViewport(_TkFrame):
    """Interactive fixed-pipeline preview for decoded source triangles."""
    LIGHTINGS = ("Viewer rig", "Shared HDRI")   # rc2016.viewer.environment.LIGHTINGS
    TONEMAPS = ("ACES fit", "Khronos PBR Neutral")
    # Object space -> Blender world (None: identity); the level viewer's objects are native Y-up.
    OBJECT_TO_WORLD = None
    RIG_FIXED_TO_WORLD = False   # the model viewer's rig turns with the camera
    GL_COLOR_BUFFER_BIT = 0x00004000
    GL_DEPTH_BUFFER_BIT = 0x00000100
    GL_DEPTH_TEST = 0x0B71
    GL_LIGHTING = 0x0B50
    GL_LIGHT0 = 0x4000
    GL_NORMALIZE = 0x0BA1
    GL_COLOR_MATERIAL = 0x0B57
    GL_FRONT_AND_BACK = 0x0408
    GL_AMBIENT_AND_DIFFUSE = 0x1602
    GL_PROJECTION = 0x1701
    GL_MODELVIEW = 0x1700
    GL_FLOAT = 0x1406
    GL_TRIANGLES = 0x0004
    GL_UNSIGNED_INT = 0x1405
    GL_VERTEX_ARRAY = 0x8074
    GL_NORMAL_ARRAY = 0x8075
    GL_POSITION = 0x1203
    GL_AMBIENT = 0x1200
    GL_DIFFUSE = 0x1201
    GL_LINE = 0x1B01
    GL_FILL = 0x1B02
    GL_POLYGON_OFFSET_FILL = 0x8037
    GL_TEXTURE_2D = 0x0DE1
    GL_TEXTURE_COORD_ARRAY = 0x8078
    GL_TEXTURE_MIN_FILTER = 0x2801
    GL_TEXTURE_MAG_FILTER = 0x2800
    GL_TEXTURE_WRAP_S = 0x2802
    GL_TEXTURE_WRAP_T = 0x2803
    GL_LINEAR = 0x2601
    GL_REPEAT = 0x2901
    GL_UNSIGNED_BYTE = 0x1401
    GL_UNPACK_ALIGNMENT = 0x0CF5
    MODES = ("Shaded", "Textured", "Solid", "Wireframe", "Solid + wireframe")
    GL_TEXTURE0 = 0x84C0

    def __init__(self, parent, **kwargs):
        if tk is None:
            raise RuntimeError("Tkinter is unavailable in this Python runtime")
        # No Tk background: Tk would otherwise erase the OpenGL frame on every repaint.
        kwargs.setdefault("background", "")
        super().__init__(parent, **kwargs)
        self._payload = None
        self._redraw_pending = False
        self._mode = "Shaded"
        self._gl_textures = None   # material slot -> {role: (GL id, sRGB)} + 'k', built lazily
        self._compressed_upload = None
        self._shading = None       # gl_shading.Shading once the context exists
        self.shading_error = None
        self.shading_warning = None
        self._bones = None         # current frame: ctypes float array, joints x 16 (row-major)
        self._bone_count = 0
        self._bones_dirty = False
        self._bone_texture = None  # GL texture holding self._bones
        self._mesh = None
        self._error = "Select a model with decoded mesh geometry to preview it."
        self._context_error = None
        self._hdc = self._hrc = None
        self._gl = None
        self._arrays = None
        self._vbos = {}            # id(ctypes array) -> (GL buffer, array) for the current mesh
        self._vbo_owner = None     # the self._arrays tuple those buffers belong to
        self._buffer_ext = None    # GL buffer entry points, False when unavailable
        self._camera = None
        self._bounds = None
        self._yaw, self._pitch, self._zoom = 0.0, -65.0, 1.0
        self._pan = [0.0, 0.0]
        self._drag = None
        self.lighting, self.tonemap = self.LIGHTINGS[0], self.TONEMAPS[0]
        self.lighting_note = None   # why the shared HDRI is not shown yet, if it is not
        self._environment = None   # environment.environment_maps() once read
        self._environment_texture = None
        self._environment_loading = False
        self._label = tk.Label(self, text=self._error, justify="center", anchor="center", wraplength=440)
        self._label.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0, relheight=1.0)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Expose>", lambda _e: self._schedule_draw())
        self.bind("<Map>", lambda _e: self._schedule_draw())
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.bind("<ButtonPress-1>", self._begin_orbit)
        self.bind("<B1-Motion>", self._orbit)
        self.bind("<ButtonPress-3>", self._begin_pan)
        self.bind("<B3-Motion>", self._pan_drag)
        # Windows sends the wheel to the focused widget, not the one under the
        # pointer, so listen application-wide and zoom when hovering this view.
        self.bind_all("<MouseWheel>", self._wheel_anywhere, add="+")
        self.bind("<Button-4>", lambda _e: self._zoom_by(1.12))
        self.bind("<Button-5>", lambda _e: self._zoom_by(1 / 1.12))
        self.bind("<Double-Button-1>", self._reset_camera)

    def set_mesh(self, payload):
        """Install a decoded mesh mapping: vertices, triangles, and optional normals."""
        self._release_textures()
        self._payload = payload
        alpha = payload.get('rock_alpha') or []
        self._rock_alpha = (ctypes.c_float*len(alpha))(*alpha) if len(alpha)==len(payload.get('vertices',[])) else None
        self._bones, self._bone_count = None, 0
        try:
            prepared=payload.get("_prepared_viewport")
            if prepared:
                self._mesh,self._arrays,self._bounds=prepared
            else:
                self._mesh = validate_mesh(payload)
                self._arrays=None
                self._bounds=mesh_bounds(self._mesh[0])
            self._error = None
            self._reset_camera()
            self._show_error(self._context_error)
            self._draw()
            return True
        except (MeshPayloadError, TypeError, ValueError) as exc:
            self._mesh, self._arrays = None, None
            self._error = str(exc)
            self._show_error(self._error)
            self._draw()
            return False

    @property
    def can_animate(self):
        """Skinned playback needs the GLSL path."""
        return self._shading is not None and not self.shading_error

    def set_pose(self, matrices):
        """Skin matrices for the current frame: (joints, 4, 4) row-major, preview basis."""
        flat = matrices.reshape(-1)
        if self._bones is None or len(self._bones) != len(flat):
            self._bones = (ctypes.c_float * len(flat))()
        ctypes.memmove(self._bones, flat.ctypes.data, flat.nbytes)
        self._bone_count = len(flat) // 16
        self._bones_dirty = True
        self._draw()

    def clear_pose(self):
        """Back to the bind/rest pose."""
        self._bones, self._bone_count = None, 0
        self._draw()

    def set_lighting(self, lighting=None, tonemap=None):
        """Choose the lighting (LIGHTINGS) and tonemap (TONEMAPS). The shared HDRI's lighting maps are
        read on a worker thread the first time; until then the view keeps the viewer rig."""
        if lighting is not None:
            if lighting not in self.LIGHTINGS:
                raise ValueError(f"unknown lighting {lighting!r}")
            self.lighting = lighting
        if tonemap is not None:
            if tonemap not in self.TONEMAPS:
                raise ValueError(f"unknown tonemap {tonemap!r}")
            self.tonemap = tonemap
        if self.lighting == "Shared HDRI" and self._environment is None and not self._environment_loading:
            self._environment_loading = True
            self.lighting_note = "Reading the shared review HDRI…"
            import threading
            result = {}

            def work():
                try:
                    from rc2016.viewer.environment import environment_maps
                    result["maps"] = environment_maps()
                except Exception as exc:   # missing NumPy or an unreadable HDRI: say so, keep the rig
                    result["error"] = f"Shared HDRI unavailable: {type(exc).__name__}: {exc}"

            def poll():
                if "maps" not in result and "error" not in result:
                    self.after(100, poll)
                    return
                self._environment_loading = False
                self._environment = result.get("maps") or False
                self.lighting_note = result.get("error")
                self._schedule_draw()
                if self.on_lighting_changed:
                    self.on_lighting_changed()
            threading.Thread(target=work, daemon=True).start()
            self.after(100, poll)
        elif self.lighting != "Shared HDRI":
            self.lighting_note = None
        self._schedule_draw()
        if self.on_lighting_changed:
            self.on_lighting_changed()

    on_lighting_changed = None   # called after the lighting or its HDRI maps change

    def _upload_environment(self):
        """GL texture of the prefiltered bands (RGB16F, world strength applied), or None."""
        if self._environment_texture is None and self._environment:
            import numpy as np
            gl = self._gl
            bands = np.ascontiguousarray(self._environment["bands"] * self._environment["strength"], np.float32)
            name = ctypes.c_uint(0)
            gl.glGenTextures(1, ctypes.byref(name))
            gl.glBindTexture(self.GL_TEXTURE_2D, name.value)
            gl.glPixelStorei(self.GL_UNPACK_ALIGNMENT, 1)
            gl.glTexImage2D(self.GL_TEXTURE_2D, 0, 0x881B, bands.shape[1], bands.shape[0], 0, 0x1907, self.GL_FLOAT,
                            bands.ctypes.data_as(ctypes.c_void_p))   # GL_RGB16F from GL_RGB float
            for parameter, value in ((self.GL_TEXTURE_MIN_FILTER, self.GL_LINEAR), (self.GL_TEXTURE_MAG_FILTER, self.GL_LINEAR),
                                     (self.GL_TEXTURE_WRAP_S, self.GL_REPEAT), (self.GL_TEXTURE_WRAP_T, 0x812F)):   # CLAMP_TO_EDGE
                gl.glTexParameteri(self.GL_TEXTURE_2D, parameter, value)
            gl.glBindTexture(self.GL_TEXTURE_2D, 0)
            self._environment_texture = name.value
        return self._environment_texture

    def _apply_lighting(self, u):
        """Lighting and tonemap uniforms for the program in use (every opaque and glass program)."""
        from rc2016.viewer.shading import ENVIRONMENT_TEXTURE_UNIT
        gl, sh = self._gl, self._shading
        hdri = self.lighting == "Shared HDRI" and bool(self._environment) and bool(self._upload_environment())
        sh.glUniform1i(u["uLighting"], 1 if hdri else 0)
        sh.glUniform1i(u["uTonemap"], self.TONEMAPS.index(self.tonemap))
        sh.glUniform1f(u["uExposure"], 2.0 ** self._environment["exposure"] if hdri else 1.0)
        modelview = (ctypes.c_float * 16)()
        gl.glGetFloatv.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        gl.glGetFloatv(self.GL_MODELVIEW_MATRIX, modelview)
        view_to_world, rig = light_frames(list(modelview), self.OBJECT_TO_WORLD, self.RIG_FIXED_TO_WORLD)
        sh.glUniformMatrix3fv(u["uViewToWorld"], 1, 0, (ctypes.c_float * 9)(*column_major3(view_to_world)))
        sh.glUniformMatrix3fv(u["uLightFrame"], 1, 0, (ctypes.c_float * 9)(*column_major3(rig)))
        sh.glUniform1i(u["uEnvTex"], ENVIRONMENT_TEXTURE_UNIT)
        sh.glActiveTexture(self.GL_TEXTURE0 + ENVIRONMENT_TEXTURE_UNIT)
        gl.glBindTexture(self.GL_TEXTURE_2D, self._environment_texture if hdri else 0)
        sh.glActiveTexture(self.GL_TEXTURE0)
        if hdri:
            strength = self._environment["strength"]
            coefficients = [float(x) * strength for x in self._environment["sh"].reshape(-1)]
            sh.glUniform3fv(u["uSH"], 9, (ctypes.c_float * 27)(*coefficients))
            sh.glUniform3f(u["uSunDir"], *(float(x) for x in self._environment["sun_direction"]))
            sh.glUniform3f(u["uSunColour"], *(float(x) * strength for x in self._environment["sun"]))

    def set_mode(self, mode):
        """Solid shading, wireframe (mesh only), or both."""
        if mode not in self.MODES:
            raise ValueError(f"unknown view mode {mode!r}")
        self._mode = mode
        self._draw()

    def _release_textures(self):
        if self._gl_textures and self._make_current():
            ids = list({entry[role][0] for entry in self._gl_textures.values()
                        for role in ("_c", "_g", "_n", "_pick", "_h", "_bng_detail", "_vehicle_p2", "_tint_detail") if entry.get(role)})
            ids.extend(entry['_rock_volume'][0] for entry in self._gl_textures.values() if entry.get('_rock_volume'))
            if ids:
                self._gl.glDeleteTextures(len(ids), (ctypes.c_uint * len(ids))(*ids))
        self._gl_textures = None

    def _upload_textures(self):
        """Create GL textures per material slot: {role: (GL id, sRGB)} and its K."""
        gl = self._gl
        self._gl_textures, by_path = {}, {}
        for slot, entry in ((self._payload or {}).get("textures") or {}).items():
            self._gl_textures[slot] = self._gl_entry(entry, by_path)
        gl.glBindTexture(self.GL_TEXTURE_2D, 0)

    def _gl_entry(self, entry, by_path):
        """One material's texture_preview entry with its textures uploaded: {role: (GL id, sRGB)} + parameters.

        ``by_path`` maps texture path -> (GL id, sRGB) or None and is shared across materials."""
        uploaded = {key: entry[key] for key in ("k", "g_mode", "self_lit", "spec", "gloss", "pick", "tint", "spec_tint", "forward", "arch_sequence", "vehicle", "facing_glow") if key in entry}
        if "two_color_metal" in entry:
            uploaded["two_color_metal"] = entry["two_color_metal"]
        if "puddles" in entry:
            uploaded["puddles"] = entry["puddles"]
        if "glow" in entry:
            uploaded["glow"] = entry["glow"]
        if entry.get("foliage"):
            uploaded["foliage"] = True
        if entry.get("alpha_cut"):
            uploaded["alpha_cut"] = float(entry["alpha_cut"])
        if "za_fresnel_power" in entry:
            uploaded["za_fresnel_power"] = entry["za_fresnel_power"]
        if entry.get('rock_blend'):
            uploaded['rock_blend'] = dict(entry['rock_blend'])
            if entry.get('_rock_volume'):
                uploaded['_rock_volume'] = (self._upload_rock_volume(entry['_rock_volume']), False)
                uploaded['rock_blend']['scales'] = entry['_rock_volume']['scales']
        for key in ('parallax', 'parallax_normal'):
            if key in entry: uploaded[key] = entry[key]
        if "bng" in entry:
            uploaded["bng"] = entry["bng"]
        for role in GL_TEXTURE_ROLES:
            texture = entry.get(role)
            if not texture:
                continue
            if texture["path"] not in by_path:
                name = self._upload_texture(texture)
                by_path[texture["path"]] = (name, bool(texture.get("srgb"))) if name else None
            if by_path[texture["path"]]:
                uploaded[role] = by_path[texture["path"]]
        return uploaded

    def _upload_rock_volume(self, texture):
        gl = self._gl; address = gl.wglGetProcAddress(b"glTexImage3D")
        if not address: raise RuntimeError('RockBlend requires glTexImage3D')
        upload = ctypes.WINFUNCTYPE(None, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)(address)
        name = ctypes.c_uint(); gl.glGenTextures(1, ctypes.byref(name)); gl.glBindTexture(0x806F, name.value)
        for p,v in ((0x2801,self.GL_LINEAR),(0x2800,self.GL_LINEAR),(0x2802,self.GL_REPEAT),(0x2803,self.GL_REPEAT),(0x8072,0x812F)):
            gl.glTexParameteri(0x806F,p,v)
        gl.glPixelStorei(self.GL_UNPACK_ALIGNMENT,1)
        pixels=ctypes.create_string_buffer(texture['pixels'])
        upload(0x806F,0,0x8058,texture['width'],texture['height'],4,0,0x1908,self.GL_UNSIGNED_BYTE,pixels)
        gl.glBindTexture(0x806F,0)
        if gl.glGetError():
            gl.glDeleteTextures(1,ctypes.byref(name)); raise RuntimeError('RockBlend volume upload failed')
        return name.value

    def _upload_texture(self, texture):
        """One GL texture from GPU-ready blocks; 0 if the driver rejects the format."""
        gl = self._gl
        if self._compressed_upload is None:
            gl.wglGetProcAddress.argtypes = [ctypes.c_char_p]; gl.wglGetProcAddress.restype = ctypes.c_void_p
            address = gl.wglGetProcAddress(b"glCompressedTexImage2D")
            prototype = ctypes.WINFUNCTYPE(None, ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
            self._compressed_upload = prototype(address) if address else False
        name = ctypes.c_uint(0)
        gl.glGetError()
        gl.glGenTextures(1, ctypes.byref(name))
        gl.glBindTexture(self.GL_TEXTURE_2D, name.value)
        for parameter, value in ((self.GL_TEXTURE_MIN_FILTER, self.GL_LINEAR), (self.GL_TEXTURE_MAG_FILTER, self.GL_LINEAR),
                                 (self.GL_TEXTURE_WRAP_S, self.GL_REPEAT), (self.GL_TEXTURE_WRAP_T, self.GL_REPEAT)):
            gl.glTexParameteri(self.GL_TEXTURE_2D, parameter, value)
        pixels = texture["pixels"]
        if texture.get("gl_format") and self._compressed_upload:
            self._compressed_upload(self.GL_TEXTURE_2D, 0, texture["gl_format"], texture["width"],
                                    texture["height"], 0, len(pixels), pixels)
        elif texture.get("gl_pixel_format"):
            gl.glPixelStorei(self.GL_UNPACK_ALIGNMENT, 1)
            gl.glTexImage2D(self.GL_TEXTURE_2D, 0, texture["gl_pixel_format"], texture["width"], texture["height"],
                            0, texture["gl_pixel_format"], self.GL_UNSIGNED_BYTE, pixels)
        if gl.glGetError():
            # Driver rejected this format: show the material without it.
            gl.glDeleteTextures(1, ctypes.byref(name))
            name.value = 0
        return name.value

    def clear(self, message="Select a model with decoded mesh geometry to preview it."):
        self._release_textures()
        self._payload, self._mesh, self._arrays = None, None, None
        self._bones, self._bone_count = None, 0
        self._error = message
        self._show_error(message)
        self._draw()

    def _show_error(self, message):
        if message:
            self._label.configure(text=message)
            self._label.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0, relheight=1.0)
        else:
            self._label.place_forget()
        # Removing or placing the label repaints this area afterwards.
        self._schedule_draw()

    def _on_configure(self, _event=None):
        if os.name == "nt" and self._hrc is None and not self._context_error:
            try:
                self._create_context()
            except Exception as exc:
                self._context_error = f"3D preview unavailable: {exc}"
                self._show_error(self._context_error)
        self._draw()

    def _create_context(self):
        if os.name != "nt":
            raise RuntimeError("native Windows OpenGL (WGL) is available only on Windows")
        user32, gdi32, gl = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.opengl32
        self._gdi32 = gdi32
        gdi32.SwapBuffers.argtypes=[wintypes.HDC];gdi32.SwapBuffers.restype=wintypes.BOOL
        gl.wglDeleteContext.argtypes=[ctypes.c_void_p];gl.wglDeleteContext.restype=wintypes.BOOL
        user32.ReleaseDC.argtypes=[wintypes.HWND,wintypes.HDC];user32.ReleaseDC.restype=ctypes.c_int
        hwnd = wintypes.HWND(self.winfo_id())
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        self._hdc = user32.GetDC(hwnd)
        if not self._hdc:
            raise OSError("could not acquire the viewport drawing surface")
        pfd = _PIXELFORMATDESCRIPTOR()
        pfd.nSize = ctypes.sizeof(pfd); pfd.nVersion = 1
        pfd.dwFlags = 0x00000004 | 0x00000020 | 0x00000001
        pfd.iPixelType = 0; pfd.cColorBits = 24; pfd.cDepthBits = 24; pfd.iLayerType = 0
        gdi32.ChoosePixelFormat.argtypes = [wintypes.HDC, ctypes.POINTER(_PIXELFORMATDESCRIPTOR)]
        gdi32.ChoosePixelFormat.restype = ctypes.c_int
        gdi32.SetPixelFormat.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.POINTER(_PIXELFORMATDESCRIPTOR)]
        gdi32.SetPixelFormat.restype = wintypes.BOOL
        pixel_format = gdi32.ChoosePixelFormat(self._hdc, ctypes.byref(pfd))
        if not pixel_format or not gdi32.SetPixelFormat(self._hdc, pixel_format, ctypes.byref(pfd)):
            raise OSError("could not configure an OpenGL depth-buffer surface")
        gl.wglCreateContext.argtypes = [wintypes.HDC]; gl.wglCreateContext.restype = ctypes.c_void_p
        gl.wglMakeCurrent.argtypes = [wintypes.HDC, ctypes.c_void_p]; gl.wglMakeCurrent.restype = wintypes.BOOL
        self._hrc = gl.wglCreateContext(self._hdc)
        if not self._hrc or not gl.wglMakeCurrent(self._hdc, self._hrc):
            raise OSError("Windows could not create an OpenGL context")
        self._gl = gl
        self._set_gl_prototypes()
        gl.glEnable(self.GL_DEPTH_TEST); gl.glEnable(self.GL_LIGHTING); gl.glEnable(self.GL_LIGHT0)
        gl.glEnable(self.GL_NORMALIZE); gl.glEnable(self.GL_COLOR_MATERIAL)
        gl.glColorMaterial(self.GL_FRONT_AND_BACK, self.GL_AMBIENT_AND_DIFFUSE)
        gl.glLightfv(self.GL_LIGHT0, self.GL_AMBIENT, (ctypes.c_float * 4)(0.38, 0.38, 0.38, 1.0))
        gl.glLightfv(self.GL_LIGHT0, self.GL_DIFFUSE, (ctypes.c_float * 4)(0.78, 0.78, 0.78, 1.0))
        gl.glLightfv(self.GL_LIGHT0, self.GL_POSITION, (ctypes.c_float * 4)(-0.4, -0.6, 1.0, 0.0))
        try:
            from rc2016.viewer.shading import Shading
            self._shading = Shading(gl)
            self._shading.program(1)   # compile now so a driver problem shows up immediately
        except Exception as exc:
            self._shading, self.shading_error = None, f"Shaded view unavailable: {exc}"
        self._show_error(None if self._mesh else self._error)

    def _set_gl_prototypes(self):
        gl = self._gl
        gl.glViewport.argtypes = [ctypes.c_int] * 4
        gl.glMatrixMode.argtypes = [ctypes.c_uint]; gl.glLoadIdentity.argtypes = []
        gl.glFrustum.argtypes = [ctypes.c_double] * 6
        gl.glClearColor.argtypes = [ctypes.c_float] * 4; gl.glClear.argtypes = [ctypes.c_uint]
        gl.glTranslatef.argtypes = [ctypes.c_float] * 3; gl.glRotatef.argtypes = [ctypes.c_float] * 4
        gl.glColor3f.argtypes = [ctypes.c_float] * 3; gl.glEnableClientState.argtypes = [ctypes.c_uint]
        gl.glVertexPointer.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_int, ctypes.c_void_p]
        gl.glNormalPointer.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p]
        gl.glDrawElements.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
        gl.glPolygonMode.argtypes = [ctypes.c_uint, ctypes.c_uint]
        gl.glPolygonOffset.argtypes = [ctypes.c_float, ctypes.c_float]
        gl.glEnable.argtypes = [ctypes.c_uint]; gl.glDisable.argtypes = [ctypes.c_uint]
        gl.glGenTextures.argtypes = [ctypes.c_int, ctypes.c_void_p]
        gl.glDeleteTextures.argtypes = [ctypes.c_int, ctypes.c_void_p]
        gl.glBindTexture.argtypes = [ctypes.c_uint, ctypes.c_uint]
        gl.glTexParameteri.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
        gl.glPixelStorei.argtypes = [ctypes.c_uint, ctypes.c_int]
        gl.glTexImage2D.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        gl.glTexCoordPointer.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_int, ctypes.c_void_p]
        gl.glDisableClientState.argtypes = [ctypes.c_uint]
        gl.glGetError.restype = ctypes.c_uint

    def _schedule_draw(self):
        # Tk paints after event handlers run, so draw once it has finished.
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self._idle_draw)

    def _idle_draw(self):
        self._redraw_pending = False
        self._draw()

    def _make_current(self):
        return bool(self._gl and self._hdc and self._hrc and self._gl.wglMakeCurrent(self._hdc, self._hrc))

    def _draw(self):
        if not self._make_current():
            return
        gl = self._gl
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        gl.glViewport(0, 0, width, height)
        gl.glClearColor(0.14, 0.15, 0.17, 1.0)
        gl.glClear(self.GL_COLOR_BUFFER_BIT | self.GL_DEPTH_BUFFER_BIT)
        if self._mesh:
            vertices, triangles, normals = self._mesh
            self._camera = camera_fit(vertices, width / height, self._zoom, bounds=self._bounds)
            c = self._camera
            gl.glMatrixMode(self.GL_PROJECTION); gl.glLoadIdentity()
            gl.glFrustum(c["left"], c["right"], c["bottom"], c["top"], c["near"], c["far"])
            gl.glMatrixMode(self.GL_MODELVIEW); gl.glLoadIdentity()
            gl.glTranslatef(self._pan[0], self._pan[1], -c["distance"])
            gl.glRotatef(self._pitch, 1.0, 0.0, 0.0); gl.glRotatef(self._yaw, 0.0, 0.0, 1.0)
            gl.glTranslatef(-c["center"][0], -c["center"][1], -c["center"][2])
            if self._arrays is None:
                flat_vertices = [x for row in vertices for x in row]
                flat_normals = [x for row in normals for x in row]
                flat_indices = [i for tri in triangles for i in tri]
                self._arrays = ((ctypes.c_float * len(flat_vertices))(*flat_vertices),
                                (ctypes.c_float * len(flat_normals))(*flat_normals),
                                (ctypes.c_uint * len(flat_indices))(*flat_indices), None, None, None)
            vertex_array, normal_array, index_array, uv_array, joint_array, weight_array = (tuple(self._arrays) + (None,) * 6)[:6]
            self._sync_buffers()
            gl.glEnableClientState(self.GL_VERTEX_ARRAY); gl.glEnableClientState(self.GL_NORMAL_ARRAY)
            gl.glVertexPointer(3, self.GL_FLOAT, 0, self._vptr(vertex_array))
            gl.glNormalPointer(self.GL_FLOAT, 0, self._vptr(normal_array))
            if uv_array is not None:
                gl.glEnableClientState(self.GL_TEXTURE_COORD_ARRAY)
                gl.glTexCoordPointer(2, self.GL_FLOAT, 0, self._vptr(uv_array))
            batches = (self._payload or {}).get("batches") or [(None, 0, len(index_array) // 3)]
            if self._gl_textures is None and uv_array is not None:
                self._upload_textures()
            drawn = False
            if self._shading is not None and not self.shading_error:
                try:
                    self._draw_shaded(index_array, uv_array, joint_array, weight_array, batches)
                    drawn = True
                except Exception as exc:   # keep the fixed-function view working
                    self.shading_error = f"Shaded view unavailable: {exc}"
                    self._shading.glUseProgram(0)
            if not drawn:
                self._draw_fixed(index_array, uv_array, batches)
            gl.glDisableClientState(self.GL_TEXTURE_COORD_ARRAY)
            gl.glDisableClientState(self.GL_NORMAL_ARRAY); gl.glDisableClientState(self.GL_VERTEX_ARRAY)
            self._unbind_buffers()
        self._gdi32.SwapBuffers(self._hdc)

    def _draw_range(self, index_array, first, count):
        self._gl.glDrawElements(self.GL_TRIANGLES, count * 3, self.GL_UNSIGNED_INT,
                                self._vptr(index_array, first * 3 * ctypes.sizeof(ctypes.c_uint), self.GL_ELEMENT_ARRAY_BUFFER))

    # GPU vertex/index buffers (2026-10-09) ---------------------------------
    # Each mesh array is uploaded once on first use instead of being copied from
    # client memory every frame (13 ms for a 550k-vertex prop).
    # Without the GL 1.5 entry points every pointer falls back to client memory.
    GL_ARRAY_BUFFER, GL_ELEMENT_ARRAY_BUFFER, GL_STATIC_DRAW = 0x8892, 0x8893, 0x88E4

    def _buffers(self):
        if self._buffer_ext is None:
            self._buffer_ext = False
            try:
                gl = self._gl
                gl.wglGetProcAddress.argtypes = [ctypes.c_char_p]; gl.wglGetProcAddress.restype = ctypes.c_void_p
                prototypes = {"glGenBuffers": [ctypes.c_int, ctypes.c_void_p], "glDeleteBuffers": [ctypes.c_int, ctypes.c_void_p],
                              "glBindBuffer": [ctypes.c_uint, ctypes.c_uint],
                              "glBufferData": [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint]}
                functions = {}
                for name, argtypes in prototypes.items():
                    address = gl.wglGetProcAddress(name.encode())
                    if not address:
                        return False
                    functions[name] = ctypes.WINFUNCTYPE(None, *argtypes)(address)
                self._buffer_ext = functions
            except Exception:
                self._buffer_ext = False
        return self._buffer_ext

    def _sync_buffers(self):
        """Free the previous mesh's buffers once a different mesh is drawn."""
        if self._vbo_owner is not self._arrays:
            ext = self._buffers()
            names = [name for name, _array in self._vbos.values()]
            if ext and names:
                ext["glDeleteBuffers"](len(names), (ctypes.c_uint * len(names))(*names))
            self._vbos, self._vbo_owner = {}, self._arrays

    def _vptr(self, array, offset=0, target=None):
        """Pointer argument for ``array``: a byte offset into its GPU buffer (bound here), else its client address."""
        target = target or self.GL_ARRAY_BUFFER
        ext = self._buffers()
        if not ext:
            return ctypes.c_void_p(ctypes.addressof(array) + offset)
        entry = self._vbos.get(id(array))
        if entry is None or entry[1] is not array:
            name = ctypes.c_uint(0)
            ext["glGenBuffers"](1, ctypes.byref(name))
            ext["glBindBuffer"](target, name.value)
            ext["glBufferData"](target, ctypes.sizeof(array), ctypes.c_void_p(ctypes.addressof(array)), self.GL_STATIC_DRAW)
            entry = self._vbos[id(array)] = (name.value, array)
        ext["glBindBuffer"](target, entry[0])
        return ctypes.c_void_p(offset)

    def _unbind_buffers(self):
        ext = self._buffers()
        if ext:
            ext["glBindBuffer"](self.GL_ARRAY_BUFFER, 0); ext["glBindBuffer"](self.GL_ELEMENT_ARRAY_BUFFER, 0)

    def _draw_fixed(self, index_array, uv_array, batches):
        """Fallback without shaders: static geometry, base colour only."""
        gl = self._gl
        mode = "Textured" if self._mode == "Shaded" else self._mode
        if mode == "Textured" and uv_array is not None:
            gl.glEnable(self.GL_LIGHTING)
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
            for slot, first, count in batches:
                texture = ((self._gl_textures or {}).get(slot) or {}).get("_c")
                if texture:
                    gl.glEnable(self.GL_TEXTURE_2D); gl.glBindTexture(self.GL_TEXTURE_2D, texture[0])
                    gl.glColor3f(1.0, 1.0, 1.0)
                else:
                    gl.glDisable(self.GL_TEXTURE_2D); gl.glColor3f(0.72, 0.74, 0.78)
                self._draw_range(index_array, first, count)
            gl.glDisable(self.GL_TEXTURE_2D); gl.glBindTexture(self.GL_TEXTURE_2D, 0)
            return
        draw = lambda: gl.glDrawElements(self.GL_TRIANGLES, len(index_array), self.GL_UNSIGNED_INT,
                                         self._vptr(index_array, 0, self.GL_ELEMENT_ARRAY_BUFFER))
        if mode != "Wireframe":
            gl.glEnable(self.GL_LIGHTING)
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
            # Push faces back slightly so an overlaid wireframe stays visible.
            gl.glEnable(self.GL_POLYGON_OFFSET_FILL); gl.glPolygonOffset(1.0, 1.0)
            gl.glColor3f(0.72, 0.74, 0.78)
            draw()
            gl.glDisable(self.GL_POLYGON_OFFSET_FILL)
        if mode in ("Wireframe", "Solid + wireframe"):
            gl.glDisable(self.GL_LIGHTING)
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_LINE)
            gl.glColor3f(*((0.62, 0.78, 0.95) if mode == "Wireframe" else (0.12, 0.13, 0.15)))
            draw()
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
            gl.glEnable(self.GL_LIGHTING)

    def _draw_shaded(self, index_array, uv_array, joint_array, weight_array, batches):
        """GLSL path for every mode: GPU skinning, base colour, and the game BRDF."""
        from rc2016.viewer.shading import TINT_DETAIL_TEXTURE_UNIT, ATTRIBUTES, BONE_TEXTURE_UNIT, PICK_TEXTURE_UNIT, TINT_MASK_TEXTURE_UNIT, ARCH_SEQUENCE_TEXTURE_UNIT, BNG_DETAIL_TEXTURE_UNIT, VEHICLE_PALETTE_TEXTURE_UNIT, GLOW_PARALLAX_TEXTURE_UNIT, PARALLAX_HEIGHT_TEXTURE_UNIT
        gl, sh = self._gl, self._shading
        self.shading_warning = None
        program, u, size = sh.program(self._bone_count or 1)
        sh.glUseProgram(program)
        self._apply_lighting(u)
        for unit, name in ((TINT_DETAIL_TEXTURE_UNIT, "uTexTintDetail"), (0, "uTexC"), (1, "uTexG"), (2, "uTexN"), (PICK_TEXTURE_UNIT, "uTexP"), (TINT_MASK_TEXTURE_UNIT, "uTexH"), (ARCH_SEQUENCE_TEXTURE_UNIT, "uTexSequence"), (BNG_DETAIL_TEXTURE_UNIT, "uTexBngDetail"), (VEHICLE_PALETTE_TEXTURE_UNIT, "uTexVehicleP2")):
            sh.glUniform1i(u[name], unit)
        skin = joint_array is not None and self._bones is not None
        sh.glUniform1i(u["uSkin"], 1 if skin else 0)
        if skin:
            sh.glActiveTexture(self.GL_TEXTURE0 + BONE_TEXTURE_UNIT)
            if self._bone_texture is None:
                name = ctypes.c_uint(0)
                gl.glGenTextures(1, ctypes.byref(name)); self._bone_texture = name.value
                gl.glBindTexture(self.GL_TEXTURE_2D, self._bone_texture)
                for parameter in (self.GL_TEXTURE_MIN_FILTER, self.GL_TEXTURE_MAG_FILTER):
                    gl.glTexParameteri(self.GL_TEXTURE_2D, parameter, 0x2600)   # NEAREST: exact matrices
            gl.glBindTexture(self.GL_TEXTURE_2D, self._bone_texture)
            if self._bones_dirty:
                # RGBA32F, 4 texels (matrix rows) per joint, one row.
                gl.glTexImage2D(self.GL_TEXTURE_2D, 0, 0x8814, 4 * self._bone_count, 1, 0, 0x1908, self.GL_FLOAT,
                                ctypes.cast(self._bones, ctypes.c_void_p))
                self._bones_dirty = False
            sh.glUniform1i(u["uBoneTex"], BONE_TEXTURE_UNIT)
            sh.glUniform1f(u["uBoneWidth"], float(4 * self._bone_count))
            sh.glActiveTexture(self.GL_TEXTURE0)
            stride = 8 * ctypes.sizeof(ctypes.c_float)
            for location, array, offset in ((ATTRIBUTES['aJoints0'], joint_array, 0), (ATTRIBUTES['aJoints1'], joint_array, 16),
                                           (ATTRIBUTES['aWeights0'], weight_array, 0), (ATTRIBUTES['aWeights1'], weight_array, 16)):
                sh.glEnableVertexAttribArray(location)
                sh.glVertexAttribPointer(location, 4, self.GL_FLOAT, 0, stride, self._vptr(array, offset))
        else:
            for location in ATTRIBUTES.values():
                sh.glDisableVertexAttribArray(location)
                sh.glVertexAttrib4f(location, 0.0, 0.0, 0.0, 0.0)
        alpha = getattr(self,'_rock_alpha',None)
        if alpha is not None:
            sh.glEnableVertexAttribArray(14); sh.glVertexAttribPointer(14,1,self.GL_FLOAT,0,0,self._vptr(alpha))
        else:
            sh.glDisableVertexAttribArray(14); sh.glVertexAttrib4f(14,1.0,0.0,0.0,0.0)
        mode = self._mode
        level = {"Shaded": 2, "Textured": 1}.get(mode, 0)
        if uv_array is None:
            level = 0
        if mode != "Wireframe":
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
            gl.glEnable(self.GL_POLYGON_OFFSET_FILL); gl.glPolygonOffset(1.0, 1.0)
            sh.glUniform1i(u["uMode"], level)
            sh.glUniform3f(u["uColor"], 0.72, 0.74, 0.78)
            # Shaded: forward (glass) batches are drawn after every opaque one, see _draw_glass.
            opaque, glass = split_forward(batches, self._gl_textures) if level == 2 else (batches, [])
            for slot, first, count in opaque:
                self._apply_material(u, (self._gl_textures or {}).get(slot) or {}, level)
                self._draw_range(index_array, first, count)
            if glass:
                self._draw_glass(index_array, glass, skin)
                sh.glUseProgram(program)
            gl.glDisable(self.GL_POLYGON_OFFSET_FILL)
        if mode in ("Wireframe", "Solid + wireframe"):
            sh.glUniform1i(u["uMode"], 3)
            sh.glUniform3f(u["uColor"], *((0.62, 0.78, 0.95) if mode == "Wireframe" else (0.12, 0.13, 0.15)))
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_LINE)
            gl.glDrawElements(self.GL_TRIANGLES, len(index_array), self.GL_UNSIGNED_INT, self._vptr(index_array, 0, self.GL_ELEMENT_ARRAY_BUFFER))
            gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
        self._unbind_material_units()
        sh.glDisableVertexAttribArray(14)
        for location in ATTRIBUTES.values():
            sh.glDisableVertexAttribArray(location)
        sh.glUseProgram(0)

    def _unbind_material_units(self):
        self._shading.glActiveTexture(self.GL_TEXTURE0+14);self._gl.glBindTexture(self.GL_TEXTURE_2D,0)
        self._shading.glActiveTexture(self.GL_TEXTURE0 + 15); self._gl.glBindTexture(0x806F,0)
        from rc2016.viewer.shading import TINT_DETAIL_TEXTURE_UNIT, BONE_TEXTURE_UNIT, PICK_TEXTURE_UNIT, TINT_MASK_TEXTURE_UNIT, ARCH_SEQUENCE_TEXTURE_UNIT, BNG_DETAIL_TEXTURE_UNIT, VEHICLE_PALETTE_TEXTURE_UNIT, GLOW_PARALLAX_TEXTURE_UNIT, PARALLAX_HEIGHT_TEXTURE_UNIT
        for unit in (PARALLAX_HEIGHT_TEXTURE_UNIT, GLOW_PARALLAX_TEXTURE_UNIT, TINT_DETAIL_TEXTURE_UNIT, VEHICLE_PALETTE_TEXTURE_UNIT, BNG_DETAIL_TEXTURE_UNIT, ARCH_SEQUENCE_TEXTURE_UNIT, TINT_MASK_TEXTURE_UNIT, PICK_TEXTURE_UNIT, BONE_TEXTURE_UNIT, 2, 1, 0):
            self._shading.glActiveTexture(self.GL_TEXTURE0 + unit)
            self._gl.glBindTexture(self.GL_TEXTURE_2D, 0)

    def _apply_material(self, u, entry, level):
        """Bind one material's textures and set its uniforms on the current opaque program."""
        from rc2016.viewer.shading import TINT_DETAIL_TEXTURE_UNIT, PICK_TEXTURE_UNIT, TINT_MASK_TEXTURE_UNIT, ARCH_SEQUENCE_TEXTURE_UNIT, BNG_DETAIL_TEXTURE_UNIT, VEHICLE_PALETTE_TEXTURE_UNIT, GLOW_PARALLAX_TEXTURE_UNIT, PARALLAX_HEIGHT_TEXTURE_UNIT
        gl, sh = self._gl, self._shading
        for unit, role, has, srgb in ((PARALLAX_HEIGHT_TEXTURE_UNIT, "_parallax_height", "uHasParallaxHeight", None), (GLOW_PARALLAX_TEXTURE_UNIT, "_glow_parallax", "uHasGlowParallax", "uSrgbGlowParallax"), (TINT_DETAIL_TEXTURE_UNIT, "_tint_detail", "uHasTintDetail", "uSrgbTintDetail"), (0, "_c", "uHasC", "uSrgbC"), (1, "_g", "uHasG", "uSrgbG"), (2, "_n", "uHasN", None),
                                      (PICK_TEXTURE_UNIT, "_pick", "uHasP", "uSrgbP"),
                                      (TINT_MASK_TEXTURE_UNIT, "_h", "uHasH", None), (ARCH_SEQUENCE_TEXTURE_UNIT, "_sequence", "uHasSequence", None), (BNG_DETAIL_TEXTURE_UNIT, "_bng_detail", "uHasBngDetail", None),
                                      (VEHICLE_PALETTE_TEXTURE_UNIT, "_vehicle_p2", "uHasVehicleP2", "uSrgbVehicleP2")):
            texture = entry.get(role) if level else None
            sh.glActiveTexture(self.GL_TEXTURE0 + unit)
            gl.glBindTexture(self.GL_TEXTURE_2D, texture[0] if texture else 0)
            sh.glUniform1i(u[has], 1 if texture else 0)
            if srgb:
                sh.glUniform1i(u[srgb], 1 if texture and texture[1] else 0)
        pom = entry.get('parallax') or {}
        supported = getattr(sh, 'supports_parallax', True)
        valid = parallax_preview_parameters_valid(pom)
        if pom and not supported:
            self.shading_warning = 'Parallax unavailable: explicit texture gradients require GL_ARB_shader_texture_lod'
        elif pom and not valid:
            self.shading_warning = 'Parallax unavailable: nonfinite parameters or invalid signed step endpoints; preview guard, native behavior unproven'
        sh.glUniform1i(u['uParallax'], 1 if pom and level == 2 and supported and valid else 0)
        sh.glUniform1i(u['uParallaxNormal'], 1 if entry.get('parallax_normal') else 0)
        sh.glUniform1i(u['uTexParallaxHeight'], PARALLAX_HEIGHT_TEXTURE_UNIT)
        sh.glUniform4f(u['uParallaxP'], *(float((pom if valid else {}).get(k,d)) for k,d in
            (('height_scale',0.),('steps_front',16.),('steps_grazing',64.),('fade',1.))))
        rock = entry.get('rock_blend') or {}
        volume = entry.get('_rock_volume')
        sh.glActiveTexture(self.GL_TEXTURE0 + 15); gl.glBindTexture(0x806F, volume[0] if volume else 0)
        sh.glUniform1i(u['uRockTex'],15); sh.glUniform1i(u['uRockBlend'],1 if rock and volume and level == 2 else 0)
        sh.glUniform3f(u['uRockSrgb'],*(float(x) for x in rock.get('extra_srgb',(False,)*3)))
        scales = rock.get('scales',[(1.,1.)]*4)
        sh.glUniform4f(u['uRockScale0'],*scales[0],*scales[1]); sh.glUniform4f(u['uRockScale1'],*scales[2],*scales[3])
        sh.glUniform3f(u['uRockTint'],*(float(x) for x in rock.get('tint',(1.,)*3)))
        sh.glUniform4f(u['uRockP'],*(float(rock.get(k,d)) for k,d in (('exponent',1.),('tint_amount',0.),('mask_strength',1.),('sharpness',1.))))
        puddles=entry.get('puddles') or {}; atlas=entry.get('_puddle_atlas')
        sh.glActiveTexture(self.GL_TEXTURE0+14);gl.glBindTexture(self.GL_TEXTURE_2D,atlas[0] if atlas else 0)
        sh.glUniform1i(u['uPuddleTex'],14);sh.glUniform1i(u['uHasPuddleAtlas'],1 if atlas else 0)
        sh.glUniform1i(u['uPuddles'],1 if puddles and atlas and level==2 else 0)
        p=puddles.get('params',[0.]*15)
        sh.glUniform4f(u['uPuddle0'],*p[0:4]);sh.glUniform4f(u['uPuddle1'],*p[4:8])
        sh.glUniform4f(u['uPuddle2'],*p[8:12]);sh.glUniform3f(u['uPuddle3'],*p[12:15])
        scales=puddles.get('scales',[(1.,1.,0.,0.)]*2)
        sh.glUniform4f(u['uPuddleNoiseScale'],*scales[0]);sh.glUniform4f(u['uPuddleRippleScale'],*scales[1])
        glow = entry.get("glow") or {}
        sh.glUniform1i(u["uGlowKind"], int(glow.get("kind", 0)) if level == 2 else 0)
        sh.glUniform3f(u["uGlowColour"], *(float(x) for x in glow.get("colour", (0.0,) * 3)))
        multiplier = 1.0 + float(glow.get("vertex_fade", 0.0)) * (abs(float(glow.get("vertex_magnitude", 1.0))) - 1.0)
        sh.glUniform4f(u["uGlowP"], float(glow.get("depth", 0.0)), float(glow.get("bias", 0.0)),
                       float(glow.get("environment_scale", 0.0)), multiplier)
        sh.glUniform1i(u["uSrgbGlowMask"], 1 if glow and entry.get("_h") and entry["_h"][1] else 0)
        sh.glUniform1i(u["uTexGlowParallax"], GLOW_PARALLAX_TEXTURE_UNIT)
        sh.glUniform1f(u["uK"], float(entry.get("k", 0.0)))
        sh.glUniform1i(u["uGMode"], {"grey_skin": 1, "grey": 2}.get(entry.get("g_mode"), 0))
        sh.glUniform1f(u["uSpecScale"], float(entry.get("spec", 1.0)))
        sh.glUniform1f(u["uGlossScale"], float(entry.get("gloss", 1.0)))
        metal = entry.get("two_color_metal") or {}
        sh.glUniform1i(u["uTwoColorMetal"], 1 if metal and level == 2 else 0)
        for name, key in (("uMetalDiffuseA", "diffuse_a"), ("uMetalDiffuseB", "diffuse_b"),
                          ("uMetalSpecA", "spec_a"), ("uMetalSpecB", "spec_b")):
            sh.glUniform3f(u[name], *(float(x) for x in metal.get(key, (1., 1., 1.))))
        sh.glUniform4f(u["uMetalP"], *(float(metal.get(key, fallback)) for key, fallback in
                       (("power", 1.), ("gloss_power", 4.), ("texture_scale", 6.), ("normal_strength", 1.))))
        fg = entry.get("facing_glow") or {}
        sh.glUniform1i(u["uFacingGlow"], 1 if fg and level else 0)
        sh.glUniform4f(u["uFacingGlowP"], *(float(fg.get(key, 0.0)) for key in ("amplitude", "exponent", "a_scale", "a_offset")))
        sh.glUniform3f(u["uFacingGlowQ"], float(fg.get("bias", 0.0)), float(fg.get("g_scale", 0.0)), 0.0)
        sh.glUniform1i(u["uFoliage"], 1 if entry.get("foliage") else 0)
        # Leaf cut-out needs the bound `_c` alpha: Solid mode (level 0) binds no texture and draws whole.
        sh.glUniform1f(u["uAlphaCut"], float(entry.get("alpha_cut", 0.0)) if level and entry.get("_c") else 0.0)
        sh.glUniform1i(u["uZaFresnel"], 1 if "za_fresnel_power" in entry else 0)
        sh.glUniform1f(u["uZaFresnelPower"], float(entry.get("za_fresnel_power", 8.0)))
        sh.glUniform1i(u["uSelfLitG"], 1 if entry.get("self_lit") == "g_blue" else 0)
        bng = entry.get("bng") or {}
        sh.glUniform1i(u["uBngKind"], int(bng.get("kind", 0)) if level == 2 else 0)
        sh.glUniform3f(u["uBngColour"], *(float(x) for x in bng.get("colour", (1.0, 1.0, 1.0))))
        sh.glUniform4f(u["uBngP"], *(float(x) for x in bng.get("params", (0.0,) * 4)))
        sh.glUniform3f(u["uBngDetail"], *(float(x) for x in bng.get("detail", (1.0, 1.0, 0.0))))
        vehicle = entry.get("vehicle")
        sh.glUniform1i(u["uVehicle"], 1 if vehicle else 0)
        if vehicle:
            sh.glUniform3f(u["uVehicleA"], *(float(x) for x in vehicle["a"]))
            sh.glUniform3f(u["uVehicleB"], *(float(x) for x in vehicle["b"]))
            sh.glUniform3f(u["uVehicleP"], float(vehicle["blend"]), float(vehicle["rim"]), 0.0)
        sequence = entry.get("arch_sequence")
        sh.glUniform1i(u["uArchSequence"], 1 if sequence else 0)
        if sequence:
            sh.glUniform3f(u["uArchColour"], *(float(x) for x in sequence["colour"]))
            sh.glUniform4f(u["uArchP"], *(float(sequence[k]) for k in ("intensity", "linear", "mask_bias", "power")))
            sh.glUniform1f(u["uArchPhase"], float(sequence["phase"]))
        pick = entry.get("pick") or {}
        sh.glUniform3f(u["uPick"], float(pick.get("scale", 0.0)), float(pick.get("blend", 0.0)), float(pick.get("u", 0.0)))
        sh.glUniform3f(u["uSpecTint"], *(float(x) for x in entry.get("spec_tint", (1.0, 1.0, 1.0))))
        tint = entry.get("tint")
        sh.glUniform1i(u["uTint"], 1 if tint else 0)
        sh.glUniform1i(u["uTintVariant"], {"random": 1, "detail": 2, "world": 3, "world_normal": 4}.get((tint or {}).get("variant"), 0))
        sh.glUniform3f(u["uTintPalette"], *(float(x) for x in (tint or {}).get("palette_rgb", (1.0, 1.0, 1.0))))
        sh.glUniform1f(u["uTintPaletteBlend"], float((tint or {}).get("palette_blend", 0.0)))
        sh.glUniform3f(u["uTintDetailP"], float((tint or {}).get("detail_scale", 1.0)), float((tint or {}).get("detail_strength", 1.0)), 0.0)
        sh.glUniform3f(u["uTintWorldOrigin"], *(float(x) for x in (tint or {}).get("world_origin", (0.0, 0.0, 0.0))))
        if tint:
            sh.glUniform3f(u["uTintA"], *(float(x) for x in tint["a"]))
            sh.glUniform3f(u["uTintB"], *(float(x) for x in tint["b"]))
            sh.glUniform4f(u["uTintP"], float(tint["power_a"]), float(tint["power_b"]),
                           float(tint["weight_b"]), float(tint["mask_b"]))
            sh.glUniform1f(u["uTintM"], float(tint["weight_a"]))

    GL_BLEND = 0x0BE2
    GL_CULL_FACE = 0x0B44
    GL_BACK = 0x0405
    GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE = 0x0302, 0x0303, 1
    GL_MODELVIEW_MATRIX = 0x0BA6

    def _apply_screen_forward(self, u, entry):
        gl, sh = self._gl, self._shading
        sh.glUniform1i(u['uScreenNoiseA'], 0); sh.glUniform1i(u['uScreenNoiseB'], 1)
        for unit, role, name in ((0, '_c', 'uScreenSrgbA'), (1, '_g', 'uScreenSrgbB')):
            texture = entry.get(role); sh.glActiveTexture(self.GL_TEXTURE0 + unit)
            gl.glBindTexture(self.GL_TEXTURE_2D, texture[0] if texture else 0)
            sh.glUniform1i(u[name], int(bool(texture and texture[1])))
        texture = entry.get('_n'); sh.glUniform1i(u['uScreenSrgbN'], int(bool(texture and texture[1])))

    def _draw_glass(self, index_array, batches, skin):
        """Forward batches: the material's blend state and culling, no depth write, farthest batch first.

        Blend/cull come from the material flags word (texture_preview.blend_state): additive is
        (SRC_ALPHA, ONE), alpha-over (SRC_ALPHA, ONE_MINUS_SRC_ALPHA); an opaque flag draws without
        blending and with depth write; mode 3 (never captured) falls back to alpha-over.
        """
        from rc2016.viewer.shading import BONE_TEXTURE_UNIT, PICK_TEXTURE_UNIT, glass_uniforms
        gl, sh = self._gl, self._shading
        program, u = sh.glass_program()
        sh.glUseProgram(program)
        self._apply_lighting(u)
        sh.glUniform1i(u["uSkin"], 1 if skin else 0)
        if skin:
            sh.glUniform1i(u["uBoneTex"], BONE_TEXTURE_UNIT)
            sh.glUniform1f(u["uBoneWidth"], float(4 * self._bone_count))
        sh.glUniform1i(u["uTexN"], 2); sh.glUniform1i(u["uTexP"], PICK_TEXTURE_UNIT)
        gl.glBlendFunc.argtypes = [ctypes.c_uint, ctypes.c_uint]; gl.glDepthMask.argtypes = [ctypes.c_ubyte]
        gl.glCullFace.argtypes = [ctypes.c_uint]; gl.glGetFloatv.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        cached = getattr(self, "_glass_centres", None)
        if not cached or cached[0] is not self._mesh:
            cached = self._glass_centres = (self._mesh, batch_centres(self._mesh[0], self._mesh[1], batches))
        modelview = (ctypes.c_float * 16)()
        gl.glGetFloatv(self.GL_MODELVIEW_MATRIX, modelview)
        gl.glCullFace(self.GL_BACK)   # decoded meshes are counter-clockwise front, GL's default
        for slot, first, count in back_to_front(batches, cached[1], list(modelview)):
            entry = self._gl_textures[slot]
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
            self._draw_range(index_array, first, count)
        gl.glDepthMask(1); gl.glDisable(self.GL_BLEND); gl.glDisable(self.GL_CULL_FACE)

    def _begin_orbit(self, event): self._drag = (event.x, event.y, self._yaw, self._pitch)
    def _orbit(self, event):
        if self._drag:
            x, y, yaw, pitch = self._drag
            self._yaw = yaw + (event.x - x) * 0.6
            self._pitch = max(-89.0, min(89.0, pitch + (event.y - y) * 0.6))
            # Coalesced: each draw waits for the display refresh, so drawing per
            # motion event let fast drags queue up and lag behind the mouse.
            self._schedule_draw()
    def _begin_pan(self, event): self._drag = (event.x, event.y, self._pan[0], self._pan[1])
    def _pan_drag(self, event):
        if self._drag:
            x, y, px, py = self._drag
            scale = (self._camera["radius"] / max(1, self.winfo_height())) if self._camera else 0.01
            self._pan = [px + (event.x - x) * scale, py - (event.y - y) * scale]
            self._schedule_draw()
    def _wheel(self, event): self._zoom_by(1.12 if event.delta > 0 else 1 / 1.12)
    def _wheel_anywhere(self, event):
        try:
            hovered = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):   # pointer over a popup/menu Tk does not track
            return
        while hovered is not None and hovered is not self:
            hovered = hovered.master
        if hovered is self:
            self._wheel(event)
    def _zoom_by(self, factor):
        self._zoom = max(0.08, min(20.0, self._zoom * factor)); self._schedule_draw()
    def _reset_camera(self, _event=None):
        self._yaw, self._pitch, self._zoom, self._pan = 0.0, -65.0, 1.0, [0.0, 0.0]
        self._draw()

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        if self._gl and self._hrc:
            try:
                self._gl.wglMakeCurrent(None, None)
                self._gl.wglDeleteContext(self._hrc)
            except Exception:
                pass
        if self._hdc:
            try:
                ctypes.windll.user32.ReleaseDC(wintypes.HWND(self.winfo_id()), self._hdc)
            except Exception:
                pass
        self._hrc = self._hdc = self._gl = None
        self._environment_texture = None


__all__ = ["MeshPayloadError", "ModelViewport", "camera_fit", "light_frames", "mesh_bounds", "perspective_matrix", "validate_mesh"]






