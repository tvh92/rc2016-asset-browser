"""Hash-pinned read-only Blender review adapter. Invoke via Blender --disable-autoexec."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import sys

SCHEMA = "rcps4-asset-review-request-v1"
REPORT_SCHEMA = "rcps4-asset-review-report-v1"
ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = ROOT / ".cache" / "reviews"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Set by run() before helpers are called; tests may inject a fake module here.
bpy = None


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside_review(path, field):
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(REVIEW_DIR)
    except ValueError:
        raise ValueError(field + " must stay inside the review folder")
    return candidate


def validate_request(request):
    if not isinstance(request, dict) or request.get("schema") != SCHEMA:
        raise ValueError("request schema must be " + SCHEMA)
    if request.get("mode") not in ("inspect", "proof"):
        raise ValueError("mode must be inspect or proof")
    for key in ("master_blend", "master_sha256", "report_path"):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise ValueError(key + " is required")
    digest = request["master_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
        raise ValueError("master_sha256 must be a 64-character hex digest")
    _inside_review(request["report_path"], "report_path")
    if request["mode"] == "proof":
        if not request.get("output_dir"):
            raise ValueError("output_dir is required for proof mode")
        _inside_review(request["output_dir"], "output_dir")
        frame = request.get("frame")
        if frame is not None and (isinstance(frame, bool) or not isinstance(frame, int)):
            raise ValueError("frame must be an integer")
        for key in ("clip", "variant", "lod"):
            if request.get(key) is not None and not isinstance(request[key], str):
                raise ValueError(key + " selector must be a string")
        if request.get("variant") is not None and request.get("lod") is not None:
            raise ValueError("combined variant and LOD selection has no validated source contract")
    return request


def require_available(rows, selector, reason, kind):
    row = next((item for item in rows if item.get("selector") == selector), None)
    if row is None:
        raise ValueError(reason or "Unsupported " + kind + " selector: " + str(selector))
    return row


def _catalog():
    text = bpy.data.texts.get("Animation Catalog.json")
    if text is None:
        return None
    try:
        result = json.loads(text.as_string())
    except (TypeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def _clips(catalog):
    result = []
    for item in (catalog or {}).get("clips", []):
        name, frames, fps = item.get("action"), item.get("frames"), item.get("fps")
        action = bpy.data.actions.get(name) if isinstance(name, str) else None
        if (action is None or isinstance(frames, bool) or not isinstance(frames, int) or frames < 1 or
                isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0):
            continue
        result.append({"selector": name, "name": item.get("name", name),
                       "frame_start": 1, "frame_end": frames, "fps": float(fps),
                       "kind": item.get("kind"), "source_raw_sha256": item.get("raw_sha256"),
                       "source_sample_sha256": item.get("sample_sha256")})
    return result


def _fusion_variants(scene):
    required = ("fusion_config_id", "fusion_config_sha256", "fusion_v1_model_sha256", "fusion_v5_model_sha256")
    if any(not scene.get(key) for key in required):
        return [], "No recognized source-backed variant contract is recorded on this scene."
    names = ("FUSION_SHARED_WEAPON", "FUSION_SHARED_HAND", "FUSION_GRENADE_V5")
    cols = [bpy.data.collections.get(name) for name in names]
    if any(col is None for col in cols):
        return [], "Fusion scene identity is incomplete: required collections are missing."
    shared, hand, v5 = cols
    v1_meshes = [obj for col in (shared, hand) for obj in col.objects if obj.type == "MESH"]
    v5_meshes = [obj for obj in v5.objects if obj.type == "MESH"]
    if (not v1_meshes or not v5_meshes or
            any(obj.get("source_variant_field") != "GloveModelOverride" or
                obj.get("source_model_sha256") != scene["fusion_v1_model_sha256"] for obj in v1_meshes) or
            any(obj.get("source_variant_field") != "GloveUpgradeModel" or
                obj.get("source_model_sha256") != scene["fusion_v5_model_sha256"] for obj in v5_meshes)):
        return [], "Fusion source hashes and config variant fields fail the validated contract."
    if any(obj.get("source_config_id") != scene["fusion_config_id"] or
           obj.get("source_config_sha256") != scene["fusion_config_sha256"]
           for obj in v1_meshes + v5_meshes):
        return [], "Fusion source config identities do not match the scene contract."
    if ({int(obj.get("source_subset_index", -1)) for obj in v1_meshes} != {0, 1} or
            {int(obj.get("source_subset_index", -1)) for obj in v5_meshes} != {0, 1, 2}):
        return [], "Fusion source sections fail the validated V1/V5 visibility contract."
    if any(col.hide_viewport or col.hide_render for col in cols):
        return [], "Fusion switch collections are disabled."
    for view_layer in bpy.context.scene.view_layers:
        for layer, excluded, hidden in _tree_state(view_layer.layer_collection):
            if layer.collection in cols and (excluded or hidden):
                return [], "Fusion switch collection is excluded or hidden in a view layer."
    return ([{"selector": value, "label": value, "contract": "Fusion Grenade source-linked visibility"}
             for value in ("V1", "V5")], None)


def _lods(scene):
    found = {}
    source_meshes = [obj for obj in scene.objects if obj.type == "MESH" and obj.get("source_model_sha256")]
    if any(not obj.get("source_look_lod_membership") for obj in source_meshes):
        return [], "Not every source mesh carries source look/LOD membership."
    for obj in source_meshes:
        raw = obj.get("source_look_lod_membership")
        if not raw:
            continue
        try:
            membership = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(membership, list):
            continue
        for row in membership:
            if isinstance(row, dict) and isinstance(row.get("look"), int) and isinstance(row.get("lod"), int):
                found.setdefault((row["look"], row["lod"]), set()).add(obj.name)
    rows = [{"selector": f"{look}:{lod}", "label": f"Look {look} / LOD {lod}",
             "look": look, "lod": lod, "object_count": len(objects)}
            for (look, lod), objects in sorted(found.items())]
    return rows, None if rows else "No imported mesh records source look/LOD membership."


def inspect_scene(master_hash):
    scene = bpy.context.scene
    variants, variant_reason = _fusion_variants(scene)
    lods, lod_reason = _lods(scene)
    return {"master_sha256_before": master_hash, "scene": scene.name,
            "clips": _clips(_catalog()), "clip_selector": "exact embedded action name",
            "variants": variants, "variants_unavailable_reason": variant_reason,
            "lods": lods, "lods_unavailable_reason": lod_reason}


def _choose_variant(selector):
    rows, reason = _fusion_variants(bpy.context.scene)
    require_available(rows, selector, reason, "variant")
    for name in ("FUSION_SHARED_WEAPON", "FUSION_SHARED_HAND"):
        for obj in bpy.data.collections[name].objects:
            if obj.type == "MESH":
                obj.hide_set(False)
                obj.hide_render = False
    # The validated scene keeps shared armatures eye-hidden in both states.
    for name in ("FUSION_SHARED_WEAPON", "FUSION_SHARED_HAND"):
        for obj in bpy.data.collections[name].objects:
            if obj.type == "ARMATURE":
                obj.hide_set(True)
                obj.hide_render = True
    visible = selector == "V5"
    for obj in bpy.data.collections["FUSION_GRENADE_V5"].objects:
        if obj.type == "ARMATURE":
            obj.hide_set(True)
            obj.hide_render = True
        else:
            obj.hide_set(not visible)
            obj.hide_render = not visible
    bpy.context.view_layer.update()


def _choose_lod(selector):
    rows, reason = _lods(bpy.context.scene)
    chosen_row = require_available(rows, selector, reason, "look/LOD")
    chosen = (chosen_row["look"], chosen_row["lod"])
    source_meshes = [obj for obj in bpy.context.scene.objects
                     if obj.type == "MESH" and obj.get("source_model_sha256")]
    all_source_meshes = [obj for obj in source_meshes if obj.get("source_look_lod_membership")]
    if not all_source_meshes:
        raise ValueError("Source look/LOD evidence became unavailable")
    for obj in all_source_meshes:
        membership = json.loads(obj["source_look_lod_membership"])
        hidden = not any((row.get("look"), row.get("lod")) == chosen for row in membership)
        obj.hide_set(hidden)
        obj.hide_render = hidden
    bpy.context.view_layer.update()


def _tree_state(layer):
    rows = [(layer, bool(layer.exclude), bool(layer.hide_viewport))]
    for child in layer.children:
        rows.extend(_tree_state(child))
    return rows


def _camera_state(scene):
    camera = scene.camera
    if camera is None:
        camera_row = None
    else:
        data = camera.data
        camera_row = {"name": camera.name,
                      "matrix_world": tuple(tuple(float(v) for v in row) for row in camera.matrix_world),
                      "location": tuple(float(v) for v in camera.location),
                      "rotation_mode": camera.rotation_mode,
                      "rotation_euler": tuple(float(v) for v in camera.rotation_euler),
                      "type": data.type, "lens": float(data.lens),
                      "shift": (float(data.shift_x), float(data.shift_y)),
                      "camera_data": {name: getattr(data, name) for name in
                                      ("sensor_fit", "sensor_width", "sensor_height", "clip_start", "clip_end",
                                       "ortho_scale", "display_size", "show_passepartout", "passepartout_alpha",
                                       "show_limits", "show_mist", "show_name", "show_sensor", "show_safe_areas")
                                      if hasattr(data, name)},
                      "dof": {name: getattr(data.dof, name) for name in
                              ("use_dof", "focus_distance", "aperture_fstop", "aperture_blades",
                               "aperture_rotation", "aperture_ratio") if hasattr(data, "dof") and
                              hasattr(data.dof, name)},
                      "constraints": tuple((c.name, c.type, bool(c.mute), float(c.influence),
                                            getattr(getattr(c, "target", None), "name", None),
                                            getattr(c, "subtarget", None)) for c in camera.constraints)}
    camera_to_view = []
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                camera_to_view.append((screen.name, area.as_pointer(),
                                       bool(getattr(area.spaces.active, "lock_camera", False))))
    return camera_row, tuple(camera_to_view)


def _world_signature(world):
    if world is None or not world.use_nodes or world.node_tree is None:
        return None
    nodes = []
    for node in world.node_tree.nodes:
        inputs = []
        for socket in node.inputs:
            if hasattr(socket, "default_value"):
                value = socket.default_value
                try:
                    value = tuple(value)
                except TypeError:
                    value = float(value) if isinstance(value, (int, float)) else str(value)
                inputs.append((socket.name, value))
        image = getattr(node, "image", None)
        nodes.append((node.name, node.bl_idname, tuple(inputs),
                      image.name if image else None,
                      image.filepath if image else None))
    links = sorted((link.from_node.name, link.from_socket.name,
                    link.to_node.name, link.to_socket.name) for link in world.node_tree.links)
    return tuple(sorted(nodes)), tuple(links)


def _animation_evidence(scene):
    rows = []
    for obj in scene.objects:
        if obj.type != "ARMATURE":
            continue
        data = obj.animation_data
        if data is None:
            rows.append((obj.name, None, None, ()))
            continue
        slot = getattr(data, "action_slot", None)
        rows.append((obj.name, data.action.name if data.action else None,
                     getattr(slot, "identifier", None),
                     tuple((track.name, bool(track.mute)) for track in data.nla_tracks)))
    return tuple(rows)


_POSE_FIELDS=('location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale')
def _pose_evidence(scene):
    return tuple((obj.name,bone.name,bone.rotation_mode,
                  tuple((name,tuple(getattr(bone,name))) for name in _POSE_FIELDS))
                 for obj in scene.objects if obj.type=='ARMATURE' for bone in obj.pose.bones)


def _render_evidence(scene):
    render = scene.render
    return {"engine": render.engine, "resolution": (render.resolution_x, render.resolution_y,
            render.resolution_percentage), "filepath": render.filepath,
            "format": render.image_settings.file_format, "color_mode": render.image_settings.color_mode,
            "film_transparent": render.film_transparent, "use_compositing": render.use_compositing,
            "fps": (render.fps, render.fps_base), "samples": scene.cycles.samples,
            "cycles_device": scene.cycles.device}


def _gpu_evidence():
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        return {"compute_device_type": prefs.compute_device_type,
                "devices": tuple(sorted((device.name, device.type, bool(device.use))
                                        for device in prefs.devices))}
    except (KeyError, AttributeError):
        return None


def _image_evidence(images):
    return tuple(sorted((image.name, image.filepath, "shared_external_hdri_path" in image,
                         image.get("shared_external_hdri_path")) for image in images))


def _proof(request, master_hash):
    scene = bpy.context.scene
    all_objects = list(scene.objects)
    object_states = [(obj, bool(obj.hide_get()), bool(obj.hide_render)) for obj in all_objects]
    collection_states = [(col, bool(col.hide_viewport), bool(col.hide_render)) for col in bpy.data.collections]
    layer_states = []
    for view_layer in bpy.context.scene.view_layers:
        layer_states.extend(_tree_state(view_layer.layer_collection))
    rigs = []
    bone_states = [(bone,bone.rotation_mode,{name:tuple(getattr(bone,name)) for name in _POSE_FIELDS})
                   for obj in all_objects if obj.type=="ARMATURE" for bone in obj.pose.bones]
    for obj in all_objects:
        if obj.type == "ARMATURE":
            data = obj.animation_data
            rigs.append((obj, data is not None,
                         data.action if data else None,
                         getattr(data, "action_slot", None) if data else None,
                         [(track, bool(track.mute)) for track in data.nla_tracks] if data else []))
    scene_keys = set(scene.keys())
    shared_lighting_was_set = "shared_lighting" in scene_keys
    shared_lighting_value = scene.get("shared_lighting")
    original_world = scene.world
    original_worlds = set(bpy.data.worlds)
    original_images = set(bpy.data.images)
    image_states = [(image, image.filepath, "shared_external_hdri_path" in image,
                     image.get("shared_external_hdri_path")) for image in original_images]
    try:
        cycles_preferences = bpy.context.preferences.addons["cycles"].preferences
        gpu_state = (cycles_preferences.compute_device_type,
                     [(device.name, device.type, bool(device.use)) for device in cycles_preferences.devices])
    except (KeyError, AttributeError):
        gpu_state = None
    render = scene.render
    state = {"frame": scene.frame_current, "frame_start": scene.frame_start, "frame_end": scene.frame_end,
             "fps": render.fps, "fps_base": render.fps_base, "view_transform": scene.view_settings.view_transform,
             "look": scene.view_settings.look, "exposure": scene.view_settings.exposure, "gamma": scene.view_settings.gamma,
             "compositing": render.use_compositing, "engine": render.engine,
             "resolution_x": render.resolution_x, "resolution_y": render.resolution_y,
             "resolution_percentage": render.resolution_percentage, "filepath": render.filepath,
             "file_format": render.image_settings.file_format, "color_mode": render.image_settings.color_mode,
             "film_transparent": render.film_transparent, "samples": scene.cycles.samples,
             "device": scene.cycles.device, "active_source_clip": scene.get("active_source_clip")}
    camera_before = _camera_state(scene)
    state_before = {"camera": camera_before, "frame": state["frame"], "frame_start": state["frame_start"],
                    "frame_end": state["frame_end"], "fps": state["fps"], "fps_base": state["fps_base"],
                    "world": original_world.name if original_world else None,
                    "world_graph": _world_signature(original_world),
                    "shared_lighting": shared_lighting_value,
                    "animation": _animation_evidence(scene), "pose_channels": _pose_evidence(scene),
                    "render": _render_evidence(scene),
                    "gpu": _gpu_evidence(),
                    "images": _image_evidence(original_images),
                    "objects": [(obj.name, hidden, hidden_render) for obj, hidden, hidden_render in object_states],
                    "collections": [(col.name, eye, render_hidden) for col, eye, render_hidden in collection_states],
                    "layer_flags": [(layer.name, excluded, hidden) for layer, excluded, hidden in layer_states]}
    result = None
    try:
        result = _proof_render(request, master_hash)
    finally:
        for obj, had_animation_data, action, slot, tracks in rigs:
            if not had_animation_data:
                if obj.animation_data:
                    obj.animation_data_clear()
                continue
            obj.animation_data.action = action
            if action is not None and hasattr(obj.animation_data, "action_slot"):
                obj.animation_data.action_slot = slot
            for track, muted in tracks:
                track.mute = muted
        for obj, hidden, hidden_render in object_states:
            obj.hide_set(hidden)
            obj.hide_render = hidden_render
        for col, hidden, hidden_render in collection_states:
            col.hide_viewport = hidden
            col.hide_render = hidden_render
        for layer, excluded, hidden in layer_states:
            layer.exclude = excluded
            layer.hide_viewport = hidden
        scene.world = original_world
        if shared_lighting_was_set:
            scene["shared_lighting"] = shared_lighting_value
        elif "shared_lighting" in scene:
            del scene["shared_lighting"]
        scene.frame_start = state["frame_start"]
        scene.frame_end = state["frame_end"]
        render.fps = state["fps"]
        render.fps_base = state["fps_base"]
        scene.view_settings.view_transform = state["view_transform"]
        scene.view_settings.look = state["look"]
        scene.view_settings.exposure = state["exposure"]
        scene.view_settings.gamma = state["gamma"]
        render.use_compositing = state["compositing"]
        render.engine = state["engine"]
        render.resolution_x = state["resolution_x"]
        render.resolution_y = state["resolution_y"]
        render.resolution_percentage = state["resolution_percentage"]
        render.filepath = state["filepath"]
        render.image_settings.file_format = state["file_format"]
        render.image_settings.color_mode = state["color_mode"]
        render.film_transparent = state["film_transparent"]
        scene.cycles.samples = state["samples"]
        scene.cycles.device = state["device"]
        if state["active_source_clip"] is None:
            if "active_source_clip" in scene:
                del scene["active_source_clip"]
        else:
            scene["active_source_clip"] = state["active_source_clip"]
        for bone,rotation_mode,values in bone_states:
            bone.rotation_mode=rotation_mode
            for name,value in values.items():setattr(bone,name,value)
        scene.frame_set(state["frame"])
        bpy.context.view_layer.update()
        for world in list(bpy.data.worlds):
            if world not in original_worlds and world.users == 0:
                bpy.data.worlds.remove(world)
        for image, filepath, had_path, path_value in image_states:
            image.filepath = filepath
            if had_path:
                image["shared_external_hdri_path"] = path_value
            elif "shared_external_hdri_path" in image:
                del image["shared_external_hdri_path"]
        for image in list(bpy.data.images):
            if image not in original_images and image.users == 0 and not image.packed_file:
                bpy.data.images.remove(image)
        if gpu_state is not None:
            preferences = bpy.context.preferences.addons["cycles"].preferences
            device_type, devices = gpu_state
            preferences.compute_device_type = device_type
            preferences.get_devices()
            original_device_flags = {(name, device_kind): enabled for name, device_kind, enabled in devices}
            for device in preferences.devices:
                key = (device.name, device.type)
                if key in original_device_flags:
                    device.use = original_device_flags[key]
    state_after = {"camera": _camera_state(scene), "frame": scene.frame_current,
                   "frame_start": scene.frame_start, "frame_end": scene.frame_end,
                   "fps": render.fps, "fps_base": render.fps_base,
                   "world": scene.world.name if scene.world else None,
                   "world_graph": _world_signature(scene.world),
                   "shared_lighting": scene.get("shared_lighting"),
                   "animation": _animation_evidence(scene), "pose_channels": _pose_evidence(scene),
                   "render": _render_evidence(scene),
                   "gpu": _gpu_evidence(),
                   "images": _image_evidence(original_images),
                   "objects": [(obj.name, bool(obj.hide_get()), bool(obj.hide_render)) for obj in scene.objects],
                   "collections": [(col.name, bool(col.hide_viewport), bool(col.hide_render))
                                   for col in bpy.data.collections],
                   "layer_flags": [(layer.name, bool(layer.exclude), bool(layer.hide_viewport))
                                   for view_layer in scene.view_layers for layer, _, _ in _tree_state(view_layer.layer_collection)]}
    if gpu_state is not None and state_after["gpu"] is not None:
        old_keys={(name,kind) for name,kind,_ in gpu_state[1]}
        state_after["gpu"]["devices"]=tuple(row for row in state_after["gpu"]["devices"] if (row[0],row[1]) in old_keys)
    if result is not None:
        result["proof"]["scene_state"] = {"before": state_before, "after": state_after,
                                          "restored": state_before == state_after}
        if not result["proof"]["scene_state"]["restored"]:
            changed=[key for key in state_before if state_before[key]!=state_after[key]]
            error=RuntimeError("Proof changed saved scene state in memory: "+", ".join(changed))
            error.scene_state=result["proof"]["scene_state"]
            raise error
        return result


def _selected_mesh_evidence(scene):
    """Record evaluated geometry and render visibility at the requested source frame."""
    import struct
    render_members=set()
    def visit(collection, enabled=True):
        enabled=enabled and not collection.hide_render
        if enabled:render_members.update(collection.objects)
        for child in collection.children:visit(child,enabled)
    visit(scene.collection)
    depsgraph=bpy.context.evaluated_depsgraph_get()
    rows=[]
    for obj in scene.objects:
        if obj.type!='MESH' or obj.hide_render or obj not in render_members or not obj.visible_get():continue
        evaluated=obj.evaluated_get(depsgraph)
        mesh=evaluated.to_mesh()
        try:
            digest=hashlib.sha256()
            for vertex in mesh.vertices:
                digest.update(struct.pack('<3f',*vertex.co))
            rows.append({'object':obj.name,'vertex_count':len(mesh.vertices),
                         'evaluated_local_positions_sha256':digest.hexdigest(),
                         'matrix_world':[list(row) for row in evaluated.matrix_world]})
        finally:evaluated.to_mesh_clear()
    return {'frame':scene.frame_current,'visible_meshes':rows,'animation':_animation_evidence(scene)}


def _release_verified_packed_hdri(scene):
    """Review-only, in memory: hand-delivered masters pack the shared HDRI, which the shared preview check rejects.

    The pack is dropped only when its bytes equal the shared file at that same path. Nothing is saved, so the
    master on disk is unchanged and the proof is lit by identical HDRI content.
    """
    from rc2016.pipeline.scene_defaults import load_shared_lighting
    world = scene.world
    if world is None or not world.use_nodes:
        return None
    packed = [n for n in world.node_tree.nodes if n.type == "TEX_ENVIRONMENT" and n.image is not None and n.image.packed_file]
    if not packed:
        return None
    if len(packed) != 1:
        raise RuntimeError("Expected one packed environment image in the saved world")
    image = packed[0].image
    hdri = Path(load_shared_lighting()["hdri_path"]).resolve()
    if Path(bpy.path.abspath(image.filepath)).resolve() != hdri:
        raise RuntimeError("Packed world image does not reference the shared HDRI path")
    if hashlib.sha256(bytes(image.packed_file.data)).hexdigest() != sha256_file(hdri):
        raise RuntimeError("Packed world image differs from the shared HDRI")
    image.unpack(method="REMOVE")
    return {"released_packed_world_image": image.name, "hdri_sha256": sha256_file(hdri),
            "saved_to_master": False}


def _proof_render(request, master_hash):
    scene = bpy.context.scene
    catalog = _catalog()
    selector, frame = request.get("clip"), request.get("frame")
    chosen = None
    if selector is not None:
        clips = _clips(catalog)
        chosen = require_available(clips, selector, None, "clip")
        index = next(i for i, row in enumerate(catalog["clips"]) if row.get("action") == selector)
        control_text = bpy.data.texts.get("Animation Controls.py")
        if control_text is None:
            raise ValueError("Embedded source animation controls are missing")
        canonical_controls = ROOT / "Animation" / "pipeline" / "controls.py"
        legacy_controls = Path(__file__).with_name("legacy_controls.py")
        embedded = control_text.as_string().splitlines()
        if canonical_controls.is_file() and embedded == canonical_controls.read_text(encoding="utf-8").splitlines():
            from rc2016.pipeline import controls
        elif (legacy_controls.is_file() and sha256_file(legacy_controls) == "48ff215035460b9797bb2664b80a5bd0031bce09a4e7a6e21e6ad0d13bc481cd" and
              embedded == legacy_controls.read_text(encoding="utf-8").splitlines()):
            # Exact sealed pre-compaction API for retained full-channel libraries.
            from rc2016.pipeline import legacy_controls as controls
        else:
            raise ValueError("Embedded source controls do not match a reviewed pipeline API")
        controls.select_clip(index)
        frame = chosen["frame_start"] if frame is None else frame
        if not chosen["frame_start"] <= frame <= chosen["frame_end"]:
            raise ValueError("frame is outside selected source clip bounds")
    elif frame is None:
        frame = scene.frame_current
    elif not scene.frame_start <= frame <= scene.frame_end:
        raise ValueError("frame is outside saved scene timeline bounds")
    if request.get("variant") is not None:
        _choose_variant(request["variant"])
    if request.get("lod") is not None:
        _choose_lod(request["lod"])
    scene.frame_set(frame)

    if scene.camera is None:
        raise ValueError("Proof rendering requires the saved scene camera")
    output_dir = _inside_review(request["output_dir"], "output_dir")
    output_dir.mkdir(parents=True, exist_ok=True)
    parts = [master_hash[:12]]
    for key, value in (("clip", selector), ("variant", request.get("variant")),
                       ("lod", request.get("lod")), ("frame", str(frame))):
        if value is not None:
            parts.append(key + "-" + re.sub(r"[^A-Za-z0-9_-]+", "_", str(value))[:48])
    image_path = output_dir / ("proof_" + "_".join(parts) + ".png")
    if image_path.exists():
        raise FileExistsError("Refusing to overwrite existing proof output")
    from rc2016.pipeline.scene_defaults import _configure_optix_gpu, apply_shared_lighting
    from rc2016.pipeline.builder import review_preview_lighting
    _configure_optix_gpu(bpy)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.render.resolution_x = 3840
    scene.render.resolution_y = 2560
    scene.render.resolution_percentage = 25
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.cycles.samples = 32
    scene.render.filepath = str(image_path)
    settings = {"engine": scene.render.engine, "device": scene.cycles.device,
                "resolution": [3840, 2560, 25], "samples": 32,
                "file_format": "PNG", "color_mode": "RGBA", "film_transparent": True, "gpu": _gpu_evidence()}
    released_pack = _release_verified_packed_hdri(scene)
    with review_preview_lighting(scene) as provenance:
        # Work on a disposable world so applying current central settings cannot mutate the master world.
        saved_preview_world = scene.world
        temp_world = bpy.data.worlds.new("Asset Browser Review | Shared Lighting")
        try:
            scene.world = temp_world
            shared_lighting = apply_shared_lighting(scene)
            lights = [obj for obj in scene.objects if obj.type == "LIGHT"]
            for light in lights:
                light.hide_render = True
            provenance["disabled_separate_lights"] = len(lights)
            scene.render.resolution_percentage = 25
            scene.cycles.samples = 32
            selection_evidence=_selected_mesh_evidence(scene)
            bpy.ops.render.render(write_still=True)
        finally:
            scene.world = saved_preview_world
            if temp_world.users == 0:
                bpy.data.worlds.remove(temp_world)
    if not image_path.is_file() or image_path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("Proof render did not produce a valid PNG")
    return {"proof": {"path": str(image_path), "sha256": sha256_file(image_path),
                      "size_px": [960, 640], "frame": frame, "clip": chosen,
                      "variant": request.get("variant"), "lod": request.get("lod"),
                      "settings": settings, "selection_evidence": selection_evidence,
                      "lighting": {**provenance, "source": "current assets/lighting/shared_lighting.json",
                                   "in_memory_packed_hdri_release": released_pack,
                                   "settings": {key: shared_lighting.get(key) for key in
                                                ("hdri_path", "world_strength", "view_transform", "look",
                                                 "exposure", "gamma", "use_compositing", "config_sha256")},
                                   "hdri_sha256": sha256_file(shared_lighting["hdri_path"]),
                                   "presentation_only": True, "game_lighting_equivalence_claim": False}}}


def run(request):
    global bpy
    validate_request(request)
    master = Path(request["master_blend"]).resolve()
    report_path = _inside_review(request["report_path"], "report_path")
    before = sha256_file(master) if master.is_file() else None
    report = {"schema": REPORT_SCHEMA, "status": "failed", "request_schema": SCHEMA,
              "master_blend": str(master), "master_sha256_before": before, "master_sha256_after": before}
    try:
        if report_path.exists():
            raise FileExistsError("Refusing to overwrite existing report")
        if before is None:
            raise FileNotFoundError("master_blend does not exist")
        if before.lower() != request["master_sha256"].lower():
            raise ValueError("master SHA-256 does not match pinned request")
        import bpy as blender
        bpy = blender
        bpy.ops.wm.open_mainfile(filepath=str(master))
        report.update(inspect_scene(before))
        if request["mode"] == "proof":
            report.update(_proof(request, before))
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = str(exc)
        report["error_type"] = type(exc).__name__
        if getattr(exc,"scene_state",None) is not None:report["failed_scene_state"]=exc.scene_state
    after = sha256_file(master) if master.is_file() else None
    report["master_sha256_after"] = after
    report["master_unchanged"] = before == after
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # This check is repeated to close a race between initial validation and writing.
    try:
        with report_path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
    except FileExistsError:
        return 1
    return 0 if report["status"] == "passed" and report["master_unchanged"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, help="path to adapter request JSON")
    args = parser.parse_args(argv)
    return run(json.loads(Path(args.request).read_text(encoding="utf-8")))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None))
