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
from pathlib import Path
from typing import Any, Dict, List, Optional


# The shared review lighting (HDRI file, world strength, view transform, look, exposure) lives
# in assets/lighting/shared_lighting.json, so it can be replaced in one place.  Every scene
# records the values it was given (scene["shared_lighting"]); checks compare against that.
SHARED_LIGHTING_FILE = Path(__file__).resolve().parents[2] / "assets" / "lighting" / "shared_lighting.json"
_LIGHTING_KEYS = ("hdri", "world_strength", "view_transform", "look", "exposure", "gamma", "use_compositing")


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


def _set_world_hdri(scene: Any, hdri_path: Optional[Path], strength: Optional[float] = None) -> None:
    if hdri_path is None:
        hdri_path = Path(load_shared_lighting()["hdri_path"])
    if strength is None:
        strength = float(load_shared_lighting()["world_strength"])
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
        if link.to_node in (background, output) and link.to_socket in (
            background.inputs.get("Color"), output.inputs.get("Surface")
        ):
            links.remove(link)
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
    render.film_transparent = True
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
    _set_world_hdri(scene, Path(settings["hdri_path"]), float(settings["world_strength"]))
    view = scene.view_settings
    view.view_transform = settings["view_transform"]
    view.look = settings["look"]
    view.exposure = float(settings["exposure"])
    view.gamma = float(settings["gamma"])
    scene.render.use_compositing = bool(settings["use_compositing"])
    scene["shared_lighting"] = json.dumps(settings, sort_keys=True)
    return settings


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
        "film_transparent": render.film_transparent,
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
    expected = {
        "engine": "CYCLES",
        "resolution": (OUTPUT_WIDTH, OUTPUT_HEIGHT, 100),
        "file_format": "PNG",
        "color_mode": "RGBA",
        "film_transparent": True,
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
