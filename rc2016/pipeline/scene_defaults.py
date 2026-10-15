"""Shared Blender scene defaults for scenes built from the game's assets.

Run inside Blender's Python console or with ``blender --background --python``.
``apply_new_scene_defaults`` is intended for a newly created scene.  It does
not invent a camera transform: an existing camera keeps its location,
rotation, lens, shift, constraints, and framing.  Use ``audit_scene`` before
touching a delivered/master scene.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


# The shared review lighting (HDRI file, world strength, view transform, look, exposure) lives
# in assets/lighting/shared_lighting.json, so it can be replaced in one place.  Every scene
# records the values it was given (scene["shared_lighting"]); checks compare against that.
SHARED_LIGHTING_FILE = Path(__file__).resolve().parents[2] / "assets" / "lighting" / "shared_lighting.json"
_LIGHTING_KEYS = ("hdri", "hdri_rotation_z_degrees", "world_strength", "view_transform", "look", "exposure", "gamma",
                  "use_compositing")
# Render Region padding (fractions of the model's own screen size): the longer screen axis gets the
# first value, the shorter axis the second.  The horizontal axis gets at least the side value, so the
# crop leaves breathing space beside the model (the hand-widened npc_ryno_dealer.blend crop).
REGION_PADDING_LONG = 0.10
REGION_PADDING_SHORT = 0.07
REGION_PADDING_SIDE = 0.25
# Build camera: the exact orientation of the hand-framed reference npc_grimroth.blend (Euler XYZ, degrees).
# Every pipeline build keeps this angle and only dollies the camera along its view axis.
CAMERA_EULER_DEGREES = (77.90851178661542, -1.7045135013369456e-05, 21.765396705411746)
# The model's longer screen axis fills this fraction of the frame; the Render Region crop then adds
# the padding, so the padded box stays inside the frame.
CAMERA_FILL_LONG_AXIS = 1.0 / (1.0 + REGION_PADDING_LONG)
# Horizontally the crop may add the larger side padding, so a model whose width decides the
# distance fills less of the frame width.
CAMERA_FILL_HORIZONTAL = 1.0 / (1.0 + max(REGION_PADDING_LONG, REGION_PADDING_SIDE))


def load_shared_lighting(path: Optional[Any] = None) -> Dict[str, Any]:
    """Read the shared lighting file; relative "hdri" paths resolve beside that file."""
    path = Path(path or SHARED_LIGHTING_FILE).expanduser().resolve()
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    missing = [k for k in _LIGHTING_KEYS if k not in data]
    if missing:
        raise ValueError("Shared lighting file %s lacks %s" % (path, missing))
    hdri = Path(data["hdri"]).expanduser()
    hdri = (hdri if hdri.is_absolute() else path.parent / hdri).resolve()
    if not hdri.is_file():
        raise FileNotFoundError("Shared HDRI not found: %s" % hdri)
    settings = {k: data[k] for k in _LIGHTING_KEYS}
    settings.update(hdri_path=str(hdri), config_path=str(path), config_sha256=hashlib.sha256(raw).hexdigest())
    return settings


def _recorded_lighting(scene: Any) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(scene["shared_lighting"])
    except (KeyError, TypeError, ValueError):
        return None


def check_shared_lighting(scene: Any, expected: Optional[Dict[str, Any]] = None) -> List[str]:
    """Mismatches between a scene and its lighting (recorded on the scene, else the shared file)."""
    import bpy  # type: ignore

    expected = expected or _recorded_lighting(scene) or load_shared_lighting()
    problems = []
    try:
        output = next(n for n in scene.world.node_tree.nodes if n.type == "OUTPUT_WORLD" and n.is_active_output)
        bg = output.inputs["Surface"].links[0].from_node
        env = bg.inputs["Color"].links[0].from_node
        image = env.image if env.type == "TEX_ENVIRONMENT" else None
        path = Path(bpy.path.abspath(image.filepath)).expanduser().resolve() if image else None
        if bg.type != "BACKGROUND" or abs(bg.inputs["Strength"].default_value - float(expected["world_strength"])) > 1e-5:
            problems.append("lighting.world_strength")
        # Scenes built before the rotation existed have no Mapping node, which is a zero rotation.
        mapping = scene.world.node_tree.nodes.get("PS4 HDRI Rotation")
        turned = mapping.inputs["Rotation"].default_value[2] if mapping is not None else 0.0
        if abs(turned - math.radians(float(expected.get("hdri_rotation_z_degrees", 0.0)))) > 1e-5:
            problems.append("lighting.hdri_rotation")
        if image is None or path is None or str(path) != expected["hdri_path"] or tuple(image.size) == (0, 0):
            problems.append("lighting.hdri_file")
        elif not image.packed_file and image.get("shared_external_hdri_path") != str(path):
            problems.append("lighting.hdri_file")
    except (AttributeError, IndexError, KeyError, StopIteration):
        problems.append("lighting.hdri")
    view = scene.view_settings
    if (view.view_transform != expected["view_transform"] or view.look != expected["look"] or
            abs(view.exposure - float(expected["exposure"])) > 1e-5 or abs(view.gamma - float(expected["gamma"])) > 1e-5 or
            bool(scene.render.use_compositing) != bool(expected["use_compositing"])):
        problems.append("colour_management.shared_view")
    if [o for o in scene.objects if o.type == "LIGHT" and not o.hide_render]:
        problems.append("lighting.separate_lights")
    return problems
OUTPUT_WIDTH = 3840
OUTPUT_HEIGHT = 2560


def _set_if_present(obj: Any, name: str, value: Any) -> None:
    """Set a Blender property when it exists in the installed version."""
    if hasattr(obj, name):
        setattr(obj, name, value)


def _set_cycles_defaults(scene: Any) -> None:
    cycles = getattr(scene, "cycles", None)
    if cycles is None:
        return
    for name, value in {
        "samples": 96,
        "adaptive_threshold": 0.04,
        "max_bounces": 5,
        "diffuse_bounces": 2,
        "glossy_bounces": 2,
        "transmission_bounces": 4,
        "transparent_max_bounces": 6,
        "volume_bounces": 0,
        "use_light_tree": True,
    }.items():
        _set_if_present(cycles, name, value)


def _set_world_hdri(scene: Any, hdri_path: Optional[Path], strength: Optional[float] = None,
                    rotation_z_degrees: Optional[float] = None) -> None:
    if hdri_path is None:
        hdri_path = Path(load_shared_lighting()["hdri_path"])
    if strength is None:
        strength = float(load_shared_lighting()["world_strength"])
    if rotation_z_degrees is None:
        rotation_z_degrees = float(load_shared_lighting()["hdri_rotation_z_degrees"])
    hdri_path = Path(hdri_path).expanduser().resolve()
    if not hdri_path.is_file():
        raise FileNotFoundError("Shared HDRI not found: %s" % hdri_path)

    import bpy  # type: ignore

    world = scene.world or bpy.data.worlds.new("PS4 Gadget World")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    background = nodes.get("Background") or nodes.new("ShaderNodeBackground")
    output = nodes.get("World Output") or nodes.new("ShaderNodeOutputWorld")
    environment = nodes.get("PS4 Golden Gate HDRI")
    if environment is None:
        environment = nodes.new("ShaderNodeTexEnvironment")
        environment.name = "PS4 Golden Gate HDRI"
    # Texture Coordinate -> Mapping -> Environment. The Mapping rotates the lookup direction, so a
    # positive Z value makes the light appear turned by the opposite angle in world space: the same
    # relative light as turning the model by +N degrees, without rotating the model.
    coords = nodes.get("PS4 HDRI Coordinates") or nodes.new("ShaderNodeTexCoord")
    coords.name = "PS4 HDRI Coordinates"
    mapping = nodes.get("PS4 HDRI Rotation") or nodes.new("ShaderNodeMapping")
    mapping.name = "PS4 HDRI Rotation"
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(rotation_z_degrees))
    try:
        image = bpy.data.images.load(str(hdri_path), check_existing=True)
        environment.image = image
        image.filepath = str(hdri_path)
        image["shared_external_hdri_path"] = str(hdri_path)
    except (RuntimeError, OSError) as exc:
        raise RuntimeError("Could not load shared HDRI: %s" % hdri_path) from exc
    background.inputs["Strength"].default_value = strength
    # Remove only duplicate links into these sockets; leave unrelated nodes intact.
    for link in list(links):
        if link.to_node in (background, output, environment) and link.to_socket in (
            background.inputs.get("Color"), output.inputs.get("Surface"), environment.inputs.get("Vector")
        ):
            links.remove(link)
    links.new(coords.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
    links.new(environment.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    output.is_active_output = True


def pack_scene_images_except_shared_hdri(scene: Any) -> Dict[str, Any]:
    """Pack scene assets while preventing Blender's pack-all from embedding the EXR."""
    import bpy  # type: ignore

    try:
        output = next(n for n in scene.world.node_tree.nodes
                      if n.type == "OUTPUT_WORLD" and n.is_active_output)
        background = output.inputs["Surface"].links[0].from_node
        environment = background.inputs["Color"].links[0].from_node
        image = environment.image
    except (AttributeError, IndexError, KeyError, StopIteration) as exc:
        raise RuntimeError("HDRI world is missing its active Environment Texture") from exc
    if environment.type != "TEX_ENVIRONMENT" or image is None:
        raise RuntimeError("HDRI world is missing its active Environment Texture image")
    path = Path(bpy.path.abspath(image.filepath)).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("Shared HDRI not found: %s" % path)
    image.filepath = ""
    try:
        bpy.ops.file.pack_all()
    finally:
        image.filepath = str(path)
    if image.packed_file:
        image.unpack(method="REMOVE")
        raise RuntimeError("Blender packed the shared HDRI despite its path being cleared")
    image.filepath = str(path)
    image.reload()
    image["shared_external_hdri_path"] = str(path)
    return {"path": str(path), "packed": False}


def apply_new_scene_defaults(scene: Any = None, hdri_path: Optional[str] = None, configure_world: bool = True,
                             lighting: Optional[Any] = None, configure_gpu: bool = True) -> Any:
    """Apply repository defaults to a new/current scene and return the scene.

    Camera transforms are deliberately never changed.  If an active camera is
    present, only its projection type is normalized to perspective.
    ``configure_gpu=False`` leaves the OptiX denoisers, Cycles device and GPU
    preferences untouched (for files that are saved, not rendered).
    """
    import bpy  # type: ignore

    scene = scene or bpy.context.scene
    render = scene.render
    # Cycles is required for this project's delivery renderer.
    try:
        render.engine = "CYCLES"
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Cycles render engine is unavailable in this Blender build") from exc
    render.resolution_x = OUTPUT_WIDTH
    render.resolution_y = OUTPUT_HEIGHT
    render.resolution_percentage = 100
    render.image_settings.file_format = "PNG"
    render.image_settings.color_mode = "RGBA"
    render.image_settings.compression = 100
    render.film_transparent = True
    # Render Region with Crop to Render Region: the region is the full frame unless a scene sets one.
    render.use_border = True
    render.use_crop_to_border = True
    _set_cycles_defaults(scene)
    cycles = scene.cycles
    _set_if_present(cycles, "use_adaptive_sampling", True)
    _set_if_present(cycles, "use_denoising", True)
    _set_if_present(cycles, "preview_samples", 32)
    _set_if_present(cycles, "use_preview_denoising", True)
    if configure_gpu:
        cycles.denoiser = "OPTIX"
        cycles.preview_denoiser = "OPTIX"
    _set_if_present(render, "use_persistent_data", True)
    if configure_gpu:
        _set_if_present(cycles, "device", "GPU")
    camera = getattr(scene, "camera", None)
    if camera is not None and getattr(camera, "type", None) == "CAMERA":
        camera.data.type = "PERSP"
    for obj in list(scene.objects):
        if getattr(obj, "type", None) == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)
    # Camera-to-View is an editor setting; apply it to open 3D viewports when
    # running interactively. It is absent in background mode.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                _set_if_present(area.spaces.active, "lock_camera", True)
    if configure_gpu:
        _configure_optix_gpu(bpy)
    if configure_world:
        apply_shared_lighting(scene, lighting, hdri_path)
    return scene


def apply_shared_lighting(scene: Any, lighting: Optional[Any] = None, hdri_path: Optional[str] = None) -> Dict[str, Any]:
    """Apply the shared review lighting (a settings dict or a lighting-file path; default
    assets/lighting/shared_lighting.json) and record it on the scene.  hdri_path overrides only the HDRI file."""
    settings = dict(lighting) if isinstance(lighting, dict) else load_shared_lighting(lighting)
    if hdri_path:
        settings["hdri_path"] = str(Path(hdri_path).expanduser().resolve())
    _set_world_hdri(scene, Path(settings["hdri_path"]), float(settings["world_strength"]),
                    float(settings["hdri_rotation_z_degrees"]))
    view = scene.view_settings
    view.view_transform = settings["view_transform"]
    view.look = settings["look"]
    view.exposure = float(settings["exposure"])
    view.gamma = float(settings["gamma"])
    scene.render.use_compositing = bool(settings["use_compositing"])
    scene["shared_lighting"] = json.dumps(settings, sort_keys=True)
    return settings


def padded_region(bounds: Any, pixel_size: Any, long_padding: float = REGION_PADDING_LONG,
                  short_padding: float = REGION_PADDING_SHORT, side_padding: float = REGION_PADDING_SIDE) -> tuple:
    """Render Region fractions (min_x, min_y, max_x, max_y) around a model box.

    ``bounds`` is (x0, y0, x1, y1) in normalised camera-frame coordinates (origin bottom-left).
    Each padding is a fraction of the model's own size on that screen axis: the longer axis
    (in pixels) takes ``long_padding`` and the shorter axis takes ``short_padding``; the horizontal
    padding is raised to at least ``side_padding``.  The result is clamped to the frame."""
    x0, y0, x1, y1 = (float(v) for v in bounds)
    width_px = (x1 - x0) * float(pixel_size[0])
    height_px = (y1 - y0) * float(pixel_size[1])
    if height_px >= width_px:  # taller than wide: vertical gets the long padding
        pad_x, pad_y = short_padding, long_padding
    else:
        pad_x, pad_y = long_padding, short_padding
    pad_x = max(pad_x, side_padding)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half_w, half_h = (x1 - x0) * (1 + pad_x) / 2, (y1 - y0) * (1 + pad_y) / 2
    return (max(0.0, cx - half_w), max(0.0, cy - half_h), min(1.0, cx + half_w), min(1.0, cy + half_h))


def fit_render_region(scene: Any, long_padding: float = REGION_PADDING_LONG,
                      short_padding: float = REGION_PADDING_SHORT) -> Dict[str, Any]:
    """Set the Render Region (with Crop to Render Region on) around the visible meshes as seen
    through the scene camera, plus padding.  Only ``render.border_*`` changes; the camera is untouched.

    The mesh vertices are evaluated at the scene's current frame, so animated clips that move
    outside this region will be cropped; frame the region on the clip's own frame if needed.
    Mesh-less diagnostic rigs keep the full frame and receive an explicit receipt."""
    import bpy  # type: ignore
    import numpy as np  # type: ignore

    camera = getattr(scene, "camera", None)
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError("Render Region fitting needs a scene camera")
    render = scene.render
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    projection = camera.calc_matrix_camera(depsgraph, x=render.resolution_x, y=render.resolution_y)
    to_clip = np.array(projection @ camera.matrix_world.inverted())
    clip_points = []
    for obj in scene.objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", co)
        finally:
            evaluated.to_mesh_clear()
        if co.size == 0:
            continue
        co = np.c_[co.reshape(-1, 3), np.ones(co.size // 3)]
        clip_points.append(co @ (to_clip @ np.array(evaluated.matrix_world)).T)
    if not clip_points:
        # Mesh-less diagnostic rigs keep the joint-fitted camera from the builder.
        render.use_border = True
        render.use_crop_to_border = True
        render.border_min_x = render.border_min_y = 0.0
        render.border_max_x = render.border_max_y = 1.0
        return {"status": "full_frame_no_visible_mesh", "model_bounds": None,
                "region": [0.0, 0.0, 1.0, 1.0], "padding_long": long_padding,
                "padding_short": short_padding,
                "reason": "No visible mesh; preserving the diagnostic rig camera and full frame"}
    clip = np.vstack(clip_points)
    if np.any(clip[:, 3] <= 0):
        raise RuntimeError("Model vertices lie behind the scene camera; set the Render Region by hand")
    ndc = clip[:, :2] / clip[:, 3:4]
    frame = (ndc + 1) / 2  # normalised, origin bottom-left, as Blender's border
    lo, hi = frame.min(axis=0), frame.max(axis=0)
    bounds = (lo[0], lo[1], hi[0], hi[1])
    region = padded_region(bounds, (render.resolution_x, render.resolution_y), long_padding, short_padding)
    render.use_border = True
    render.use_crop_to_border = True
    render.border_min_x, render.border_min_y, render.border_max_x, render.border_max_y = region
    return {"model_bounds": [float(v) for v in bounds], "region": [float(v) for v in region],
            "padding_long": long_padding, "padding_short": short_padding}


# --- Render size rule -------------------------------------------------------------------------
# Characters whose textures are smaller than the frame resolution look blurry when rendered at
# full size, so the render size follows the texture detail.  The metric is the screen-area weighted
# median linear texel density (base-colour texels per render pixel) of the visible mesh at 100%
# size; the resolution percentage is the smallest one that brings that median to about one texel
# per pixel.  Resolution X/Y never change, so the Render Region fractions stay valid.
RENDER_SIZE_MIN_PERCENT = 25
RENDER_SIZE_MAX_PERCENT = 100
# Base-colour images up to this edge length are placeholders (the 2x2 default colour) with no detail.
PLACEHOLDER_TEXTURE_EDGE = 4
# Ray tests for occlusion run on at most this many triangles (an even stride through the mesh).
MAX_VISIBILITY_TRIANGLES = 40000


def weighted_median(values: Any, weights: Any) -> Optional[float]:
    """Weighted median of ``values`` (the smallest value whose cumulative weight reaches half the
    total); None for no positive weight.  Pure numpy."""
    import numpy as np  # type: ignore

    values = np.asarray(values, dtype=np.float64).ravel()
    weights = np.asarray(weights, dtype=np.float64).ravel()
    keep = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not keep.any():
        return None
    values, weights = values[keep], weights[keep]
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order])
    index = int(np.searchsorted(cumulative, cumulative[-1] / 2.0))
    return float(values[order][min(index, len(order) - 1)])


def triangle_texel_density(uv: Any, screen: Any, texture_size: Any) -> tuple:
    """Linear texel density of triangles as (density, pixel_area, front_facing).

    ``uv`` is N x 3 x 2 (UV0 corners), ``screen`` is N x 3 x 2 corner positions in render pixels
    (y up, so counter-clockwise triangles face the camera) and ``texture_size`` is N x 2 (width,
    height in texels).  Density is sqrt(UV area x width x height / pixel area): texels along one
    screen pixel.  Degenerate screen triangles get density 0 and pixel area 0.  Pure numpy."""
    import numpy as np  # type: ignore

    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 3, 2)
    screen = np.asarray(screen, dtype=np.float64).reshape(-1, 3, 2)
    size = np.asarray(texture_size, dtype=np.float64).reshape(-1, 2)

    def area2(points: Any) -> Any:  # signed double area
        a, b, c = points[:, 0], points[:, 1], points[:, 2]
        return (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])

    signed = area2(screen)
    pixel_area = np.abs(signed) / 2.0
    texel_area = np.abs(area2(uv)) / 2.0 * size[:, 0] * size[:, 1]
    drawn = pixel_area > 1e-12
    with np.errstate(divide="ignore", invalid="ignore"):
        density = np.where(drawn, np.sqrt(texel_area / np.where(drawn, pixel_area, 1.0)), 0.0)
    return density, np.where(drawn, pixel_area, 0.0), signed > 0


def render_percentage_for_density(metric: Optional[float], minimum: int = RENDER_SIZE_MIN_PERCENT,
                                  maximum: int = RENDER_SIZE_MAX_PERCENT) -> int:
    """Resolution percentage for a texel density metric: ``ceil(100 x metric)`` clamped to
    ``minimum``..``maximum``.  A metric of 1 or more (or none) keeps ``maximum`` (never upscales)."""
    if metric is None or not math.isfinite(metric) or metric >= 1.0:
        return maximum
    return int(min(maximum, max(minimum, math.ceil(round(100.0 * metric, 9)))))


def visible_texel_density(scene: Any, max_rays: int = MAX_VISIBILITY_TRIANGLES) -> Dict[str, Any]:
    """Texel density samples of the visible meshes through the scene camera at 100% size.

    Every front-facing triangle of the evaluated meshes at the current frame that the camera sees
    (its centroid is the first surface along the ray from the camera) contributes: UV0 area x the
    width x height of the material's base-colour image, over its projected area in render pixels
    (resolution X x Y).  Triangles of materials without a detailed base-colour image are skipped.
    Returns ``density``, ``weight`` (pixel area) and ``material`` (index into ``materials``) arrays."""
    import bpy  # type: ignore
    import numpy as np  # type: ignore
    from mathutils import Vector  # type: ignore
    from mathutils.bvhtree import BVHTree  # type: ignore
    from rc2016.pipeline.game_shader import _find_textures

    camera = scene.camera
    render = scene.render
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    to_clip = np.array(camera.calc_matrix_camera(depsgraph, x=render.resolution_x, y=render.resolution_y)
                       @ camera.matrix_world.inverted())
    half = np.array([render.resolution_x, render.resolution_y], dtype=np.float64) / 2.0
    sizes: Dict[str, Any] = {}

    def base_colour_size(material: Any) -> tuple:
        if material is None:
            return (0, 0)
        if material.name not in sizes:
            image = None
            if getattr(material, "node_tree", None) is not None:
                node = _find_textures(material).get("c")
                image = node.image if node is not None else None
            width, height = (int(image.size[0]), int(image.size[1])) if image is not None else (0, 0)
            if max(width, height) <= PLACEHOLDER_TEXTURE_EDGE:
                width = height = 0
            sizes[material.name] = (width, height)
        return sizes[material.name]

    materials: List[str] = []
    world_chunks, tri_chunks, uv_chunks, size_chunks, mat_chunks = [], [], [], [], []
    offset = 0
    for obj in scene.objects:
        if obj.type != "MESH" or obj.hide_render or not len(obj.material_slots):
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            corner_tris = getattr(mesh, "corner_tris", None)
            if corner_tris is None:
                corner_tris = mesh.loop_triangles
            count, vertex_count = len(corner_tris), len(mesh.vertices)
            if not count or not vertex_count or not len(mesh.uv_layers):
                continue
            co = np.empty(vertex_count * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", co)
            tri_vertices = np.empty(count * 3, dtype=np.int64)
            corner_tris.foreach_get("vertices", tri_vertices)
            tri_loops = np.empty(count * 3, dtype=np.int64)
            corner_tris.foreach_get("loops", tri_loops)
            tri_polygon = np.empty(count, dtype=np.int64)
            corner_tris.foreach_get("polygon_index", tri_polygon)
            poly_material = np.zeros(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("material_index", poly_material)
            uv_layer = mesh.uv_layers.active or mesh.uv_layers[0]
            loop_uv = np.empty(len(mesh.loops) * 2, dtype=np.float64)
            if hasattr(uv_layer, "uv"):  # Blender 5.0
                uv_layer.uv.foreach_get("vector", loop_uv)
            else:
                uv_layer.data.foreach_get("uv", loop_uv)
        finally:
            evaluated.to_mesh_clear()
        slot_sizes, slot_ids = [], []
        for slot in obj.material_slots:
            slot_sizes.append(base_colour_size(slot.material))
            name = slot.material.name if slot.material else ""
            if name not in materials:
                materials.append(name)
            slot_ids.append(materials.index(name))
        world = np.c_[co.reshape(-1, 3), np.ones(vertex_count)] @ np.array(evaluated.matrix_world).T
        tri_slot = np.minimum(poly_material[tri_polygon], len(slot_sizes) - 1)
        world_chunks.append(world[:, :3])
        tri_chunks.append(tri_vertices.reshape(-1, 3) + offset)
        uv_chunks.append(loop_uv.reshape(-1, 2)[tri_loops].reshape(-1, 3, 2))
        size_chunks.append(np.array(slot_sizes, dtype=np.float64)[tri_slot])
        mat_chunks.append(np.array(slot_ids, dtype=np.int64)[tri_slot])
        offset += vertex_count
    result = {"density": np.empty(0), "weight": np.empty(0), "material": np.empty(0, dtype=np.int64),
              "materials": materials, "triangles": 0}
    if not world_chunks:
        return result
    world = np.vstack(world_chunks)
    triangles = np.vstack(tri_chunks)
    uv = np.vstack(uv_chunks)
    size = np.vstack(size_chunks)
    material = np.concatenate(mat_chunks)
    result["triangles"] = int(len(triangles))
    clip = np.c_[world, np.ones(len(world))] @ to_clip.T
    in_front = (clip[triangles, 3] > 1e-9).all(axis=1)
    screen = (clip[:, :2] / np.where(clip[:, 3:4] > 1e-9, clip[:, 3:4], 1.0)) * half  # pixels, y up
    density, pixel_area, facing = triangle_texel_density(uv, screen[triangles], size)
    candidates = np.flatnonzero(in_front & facing & (pixel_area > 0) & (size[:, 0] > 0))
    if not len(candidates):
        return result
    if len(candidates) > max_rays:
        candidates = candidates[np.linspace(0, len(candidates) - 1, max_rays).astype(np.int64)]
    bvh = BVHTree.FromPolygons(world.tolist(), triangles.tolist(), all_triangles=True)
    origin = camera.matrix_world.translation.copy()
    centroid = world[triangles[candidates]].mean(axis=1)
    visible = np.zeros(len(candidates), dtype=bool)
    for slot, (index, point) in enumerate(zip(candidates, centroid)):
        direction = Vector(point.tolist()) - origin
        distance = direction.length
        if distance <= 0.0:
            continue
        direction /= distance
        hit = bvh.ray_cast(origin, direction, distance * 1.001)
        visible[slot] = hit[0] is None or hit[2] == index or hit[3] >= distance * (1 - 1e-4)
    candidates = candidates[visible]
    result.update(density=density[candidates], weight=pixel_area[candidates], material=material[candidates])
    return result


def fit_render_size(scene: Any) -> Dict[str, Any]:
    """Set ``render.resolution_percentage`` from the visible mesh's texel density and return the receipt.

    Call it after ``frame_camera_zoom`` and ``fit_render_region``: the density is measured through
    the scene camera at the current frame.  The percentage is ``ceil(100 x metric)`` clamped to
    25..100 (see ``render_percentage_for_density``); it stays 100 when the median is already one
    texel per pixel or more, or when no visible mesh carries a detailed base-colour image.
    Resolution X/Y and the Render Region fractions are untouched."""
    render = scene.render
    camera = getattr(scene, "camera", None)
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError("Render size fitting needs a scene camera")
    samples = visible_texel_density(scene)
    density, weight, material = samples["density"], samples["weight"], samples["material"]
    metric = weighted_median(density, weight)
    percentage = render_percentage_for_density(metric)
    render.resolution_percentage = percentage
    total = float(weight.sum())
    breakdown = []
    for index in sorted(set(material.tolist()), key=lambda i: -float(weight[material == i].sum())):
        chosen = material == index
        breakdown.append({"material": samples["materials"][index],
                          "screen_share": round(float(weight[chosen].sum()) / total, 4),
                          "median_texel_per_pixel": round(float(weighted_median(density[chosen], weight[chosen])), 4)})
    if metric is None:
        status = "full_size_no_textured_mesh"
    elif metric >= 1.0:
        status = "full_size_texel_density_at_least_1"
    elif 100.0 * metric < RENDER_SIZE_MIN_PERCENT:
        status = "scaled_to_minimum"
    else:
        status = "scaled"
    return {"status": status, "resolution_percentage": percentage, "metric_texel_per_pixel_at_100": metric,
            "metric": "screen-area weighted median of sqrt(UV0 area x base-colour width x height / projected pixel area) "
                      "over visible front-facing triangles at 100% size",
            "texel_per_pixel_after": None if metric is None else metric * 100.0 / percentage,
            "min_percentage": RENDER_SIZE_MIN_PERCENT, "max_percentage": RENDER_SIZE_MAX_PERCENT,
            "triangles_measured": int(len(density)), "triangles_total": samples["triangles"],
            "screen_pixels_at_100": total, "materials": breakdown[:12]}


def camera_distance_for_fill(points: Any, centre: Any, rotation: Any, projection: Any, axis: int, fill: float) -> float:
    """Distance from ``centre`` along the camera's back axis (its +Z) at which ``points`` span ``fill``
    of the frame on ``axis`` (0 = horizontal, 1 = vertical).

    Pure numpy.  ``rotation`` is the camera's 3x3 world rotation (columns: right, up, back) and
    ``projection`` its 4x4 projection matrix.  The span shrinks as the camera moves away, so a
    bisection finds the nearest distance that keeps every point inside the frame."""
    import numpy as np  # type: ignore

    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    centre = np.asarray(centre, dtype=np.float64)
    rotation = np.asarray(rotation, dtype=np.float64)
    proj = np.asarray(projection, dtype=np.float64)
    back = rotation[:, 2]
    rel = points - centre
    scale = max(float(np.ptp(rel, axis=0).max()), 1e-6)
    ones = np.ones((len(rel), 1))

    def span(distance: float) -> float:
        cam = (rel - back * distance) @ rotation  # points in camera space, camera at centre + back * distance
        clip = np.c_[cam, ones] @ proj.T
        if np.any(clip[:, 3] <= 1e-9):  # a point is at or behind the camera
            return float("inf")
        ndc = (clip[:, :2] / clip[:, 3:4] + 1) / 2
        return float(ndc[:, axis].max() - ndc[:, axis].min())

    low = float((rel @ back).max()) + 1e-6 * scale  # nearest distance with every point in front
    high = low + scale
    for _ in range(64):
        if span(high) <= fill:
            break
        high = low + 2 * (high - low)
    else:
        raise RuntimeError("Could not frame the model on axis %d at fill %.3f" % (axis, fill))
    for _ in range(200):
        middle = (low + high) / 2
        if span(middle) > fill:
            low = middle
        else:
            high = middle
    return high


def visible_mesh_points(scene: Any) -> Any:
    """World-space vertices of the visible meshes at the scene's current frame, as an N x 3 array."""
    import bpy  # type: ignore
    import numpy as np  # type: ignore

    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    chunks = []
    for obj in scene.objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", co)
        finally:
            evaluated.to_mesh_clear()
        if co.size:
            world = np.c_[co.reshape(-1, 3), np.ones(co.size // 3)] @ np.array(evaluated.matrix_world).T
            chunks.append(world[:, :3])
    return np.vstack(chunks) if chunks else np.empty((0, 3))


def frame_camera_zoom(scene: Any, points: Any = None) -> Dict[str, Any]:
    """Set the scene camera to the reference angle (CAMERA_EULER_DEGREES) and dolly it along its
    view axis, so the model fills CAMERA_FILL_LONG_AXIS of the frame on its longer screen axis
    (CAMERA_FILL_HORIZONTAL when its width decides the distance).

    ``points`` defaults to the visible mesh vertices.  The camera's lens and sensor are unchanged;
    only its rotation and location are set.  Call ``fit_render_region`` afterwards to crop."""
    import bpy  # type: ignore
    import numpy as np  # type: ignore

    camera = getattr(scene, "camera", None)
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError("Camera zoom needs a scene camera")
    if points is None:
        points = visible_mesh_points(scene)
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(points):
        raise RuntimeError("No model points to frame the camera on")
    camera.rotation_euler = tuple(math.radians(v) for v in CAMERA_EULER_DEGREES)
    bpy.context.view_layer.update()
    rotation = np.array(camera.matrix_world.to_3x3())
    render = scene.render
    depsgraph = bpy.context.evaluated_depsgraph_get()
    projection = np.array(camera.calc_matrix_camera(depsgraph, x=render.resolution_x, y=render.resolution_y))
    centre = (points.min(axis=0) + points.max(axis=0)) / 2
    horizontal = camera_distance_for_fill(points, centre, rotation, projection, 0, CAMERA_FILL_HORIZONTAL)
    vertical = camera_distance_for_fill(points, centre, rotation, projection, 1, CAMERA_FILL_LONG_AXIS)
    # The axis that needs the larger distance is the one the model fills most, i.e. its longer screen axis.
    distance = max(horizontal, vertical)
    dominant = "horizontal" if horizontal >= vertical else "vertical"
    location = centre + rotation[:, 2] * distance

    def screen_centre(at: Any) -> Any:
        # Centre of the model's projected box in normalised -1..1 coordinates.
        cam = (points - at) @ rotation
        clip = np.c_[cam, np.ones(len(cam))] @ projection.T
        ndc = clip[:, :2] / clip[:, 3:4]
        return (ndc.min(axis=0) + ndc.max(axis=0)) / 2

    # Slide the camera (rotation untouched) until the model is centred on screen, so the padded
    # Render Region is not clipped by the frame edge.  A camera-space shift of d * ndc / projection.
    for _ in range(4):
        offset = screen_centre(location)
        if np.allclose(offset, 0.0, atol=1e-6):
            break
        location = location + rotation[:, 0] * (offset[0] * distance / projection[0, 0]) \
            + rotation[:, 1] * (offset[1] * distance / projection[1, 1])
    camera.location = tuple(float(v) for v in location)
    bpy.context.view_layer.update()
    return {"dominant_axis": dominant, "distance": float(distance),
            "fill": CAMERA_FILL_HORIZONTAL if dominant == "horizontal" else CAMERA_FILL_LONG_AXIS,
            "location": [float(v) for v in camera.location],
            "rotation_degrees": [float(v) for v in CAMERA_EULER_DEGREES],
            "screen_centre_offset": [float(v) for v in screen_centre(location)]}


def _configure_optix_gpu(bpy: Any) -> None:
    """Select the OptiX GPU for Cycles without saving user preferences."""
    try:
        import addon_utils  # type: ignore
        addon_utils.enable("cycles", default_set=False, persistent=False)
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "OPTIX"
        preferences.get_devices()
        devices = getattr(preferences, "devices", ())
        optix_devices = [device for device in devices if getattr(device, "type", "") == "OPTIX"]
        if not optix_devices:
            raise RuntimeError("No OptiX GPU device is available")
        for device in devices:
            device.use = getattr(device, "type", "") == "OPTIX"
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError("Could not configure the Cycles OptiX GPU") from exc


def expected_render_percentage(region_receipt_text: Optional[str]) -> Optional[int]:
    """Resolution percentage a scene must have, from its embedded ``Render region.json`` text.

    100 when the scene has no receipt or the receipt has no ``render_size`` record (delivered scenes
    built before the render size rule, weapon scenes); the recorded percentage when it is an integer
    in RENDER_SIZE_MIN_PERCENT..RENDER_SIZE_MAX_PERCENT; None for a malformed record."""
    if not region_receipt_text:
        return RENDER_SIZE_MAX_PERCENT
    try:
        receipt = json.loads(region_receipt_text)
    except ValueError:
        return None
    record = receipt.get("render_size") if isinstance(receipt, dict) else None
    if record is None:
        return RENDER_SIZE_MAX_PERCENT
    value = record.get("resolution_percentage") if isinstance(record, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) \
            or not RENDER_SIZE_MIN_PERCENT <= value <= RENDER_SIZE_MAX_PERCENT:
        return None
    return value


def audit_scene(scene: Any = None, check_gpu: bool = True) -> Dict[str, Any]:
    """Return a read-only snapshot of relevant defaults and camera state.

    ``check_gpu=False`` skips the process-level OptiX device preferences (not saved in
    the .blend) for a process that never renders; the saved Cycles settings are still checked.
    """
    import bpy  # type: ignore

    scene = scene or bpy.context.scene
    render = scene.render
    camera = getattr(scene, "camera", None)
    cycles = getattr(scene, "cycles", None)
    gpu = {"compute_device_type": None, "devices": []}
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        gpu["compute_device_type"] = getattr(preferences, "compute_device_type", None)
        gpu["devices"] = [
            {"name": getattr(device, "name", ""), "type": getattr(device, "type", ""),
             "use": getattr(device, "use", None)}
            for device in getattr(preferences, "devices", ())
        ]
    except (KeyError, AttributeError):
        pass
    result = {
        "engine": render.engine,
        "resolution": (render.resolution_x, render.resolution_y, render.resolution_percentage),
        "file_format": render.image_settings.file_format,
        "color_mode": render.image_settings.color_mode,
        "png_compression": render.image_settings.compression,
        "film_transparent": render.film_transparent,
        "use_border": render.use_border,
        "use_crop_to_border": render.use_crop_to_border,
        "render_use_persistent_data": getattr(render, "use_persistent_data", None),
        "cycles": None if cycles is None else {
            "device": getattr(cycles, "device", None),
            "samples": getattr(cycles, "samples", None),
            "adaptive_threshold": getattr(cycles, "adaptive_threshold", None),
            "use_adaptive_sampling": getattr(cycles, "use_adaptive_sampling", None),
            "use_denoising": getattr(cycles, "use_denoising", None),
            "preview_samples": getattr(cycles, "preview_samples", None),
            "use_preview_denoising": getattr(cycles, "use_preview_denoising", None),
            "denoiser": cycles.denoiser,
            "preview_denoiser": cycles.preview_denoiser,
            "max_bounces": getattr(cycles, "max_bounces", None),
            "diffuse_bounces": getattr(cycles, "diffuse_bounces", None),
            "glossy_bounces": getattr(cycles, "glossy_bounces", None),
            "transmission_bounces": getattr(cycles, "transmission_bounces", None),
            "transparent_max_bounces": getattr(cycles, "transparent_max_bounces", None),
            "volume_bounces": getattr(cycles, "volume_bounces", None),
            "use_light_tree": getattr(cycles, "use_light_tree", None),
        },
        "camera": None if camera is None else {
            "name": camera.name,
            "matrix_world": [tuple(row) for row in camera.matrix_world],
            "constraints": [(c.name, c.type) for c in camera.constraints],
            "type": camera.data.type,
            "location": tuple(camera.location),
            "rotation_euler": tuple(camera.rotation_euler),
            "lens": camera.data.lens,
            "shift_x": camera.data.shift_x,
            "shift_y": camera.data.shift_y,
        },
        "world": None if scene.world is None else scene.world.name,
        "gpu": gpu,
    }
    region_text = bpy.data.texts.get("Render region.json")
    percentage = expected_render_percentage(region_text.as_string() if region_text else None)
    expected = {
        "engine": "CYCLES",
        # Resolution X/Y are fixed; the percentage is 100 unless the Render region receipt records the
        # render size rule's choice (a malformed record never matches).
        "resolution": (OUTPUT_WIDTH, OUTPUT_HEIGHT, -1 if percentage is None else percentage),
        "file_format": "PNG",
        "color_mode": "RGBA",
        "png_compression": 100,
        "film_transparent": True,
        "use_border": True,
        "use_crop_to_border": True,
        "render_use_persistent_data": True,
    }
    result["mismatches"] = [key for key, value in expected.items() if result.get(key) != value]
    if cycles is None:
        result["mismatches"].append("cycles")
    else:
        for key, value in {
            "device": "GPU", "samples": 96, "adaptive_threshold": 0.04,
            "use_adaptive_sampling": True, "use_denoising": True,
            "preview_samples": 32, "use_preview_denoising": True,
            "denoiser": "OPTIX", "preview_denoiser": "OPTIX",
            "max_bounces": 5, "diffuse_bounces": 2, "glossy_bounces": 2,
            "transmission_bounces": 4, "transparent_max_bounces": 6,
            "volume_bounces": 0, "use_light_tree": True,
        }.items():
            actual = result["cycles"].get(key)
            if key == "adaptive_threshold":
                matches = actual is not None and abs(actual - value) < 1e-5
            else:
                matches = actual == value
            if not matches:
                result["mismatches"].append("cycles." + key)
    if check_gpu:
        if result["gpu"].get("compute_device_type") != "OPTIX":
            result["mismatches"].append("gpu.compute_device_type")
        if not any(device.get("type") == "OPTIX" and device.get("use") for device in result["gpu"]["devices"]):
            result["mismatches"].append("gpu.optix_device")
        if any(device.get("type") != "OPTIX" and device.get("use") for device in result["gpu"]["devices"]):
            result["mismatches"].append("gpu.non_optix_device_enabled")
    if camera is None or camera.data.type != "PERSP":
        result["mismatches"].append("camera.perspective")
    lights = [o for o in scene.objects if o.type == "LIGHT"]
    authorised_novalis_sun = (scene.get("lighting_source") == "novalis_game_reflection_probe_plus_atmosphere_sun" and
        len(lights) == 1 and lights[0].data.type == "SUN" and
        lights[0].get("lighting_role") == "novalis_atmosphere_sun")
    veldin = scene.get("lighting_source") == "veldin_capture"
    authorised_veldin_key = (veldin and len(lights) == 1 and lights[0].data.type == "SUN" and
        lights[0].get("lighting_role") == "veldin_captured_key_light")
    if lights and not (authorised_novalis_sun or authorised_veldin_key):
        result["mismatches"].append("lighting.separate_lights")
    try:
        output = next(n for n in scene.world.node_tree.nodes if n.type == "OUTPUT_WORLD" and n.is_active_output)
        bg = output.inputs["Surface"].links[0].from_node
        env = bg.inputs["Color"].links[0].from_node
        if veldin:
            # Captured Veldin look (rc2016/pipeline/game_look.py): fitted ambient world,
            # captured key light and the RC PS4 Game view transform.
            valid = (bg.type == "BACKGROUND" and abs(bg.inputs["Strength"].default_value - 1.0) < 1e-5 and
                     scene.world.name.startswith("Veldin captured") and
                     scene.view_settings.view_transform == "Raw" and scene.render.use_compositing and
                     scene.compositing_node_group is not None and
                     scene.compositing_node_group.nodes.get("RC PS4 Game View (compositor)") is not None)
            if not valid:result["mismatches"].append("lighting.veldin_capture")
        elif scene.get("lighting_source") in {"novalis_game_reflection_probe", "novalis_game_reflection_probe_plus_atmosphere_sun"}:
            strength = scene.get("lighting_world_strength")
            valid = (bg.type == "BACKGROUND" and strength == 1.0 and
                abs(bg.inputs["Strength"].default_value - 1.0) < 1e-5 and
                env.type == "TEX_ENVIRONMENT" and env.image and env.image.packed_file and
                env.image.get("source_probe_sha256") == scene.get("lighting_probe_sha256") and
                env.image.get("lighting_region") == scene.get("lighting_region"))
            if not valid:result["mismatches"].append("lighting.game_probe")
        else:
            # Shared review lighting: compare with what the scene recorded (else the shared file).
            result["mismatches"].extend(m for m in check_shared_lighting(scene)
                                        if m not in result["mismatches"])
            recorded = _recorded_lighting(scene)
            if recorded is not None:
                current = load_shared_lighting()
                if any(recorded.get(k) != current.get(k) for k in _LIGHTING_KEYS + ("hdri_path",)):
                    result["mismatches"].append("lighting.differs_from_current_shared_file")
    except (AttributeError, IndexError, KeyError, StopIteration):
        result["mismatches"].append("lighting.hdri")
    return result
