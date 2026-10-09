"""Add and separately validate native source clips on a copied Pyrocitor V1/V5 scene."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

from rc2016.formats.container import inspect as inspect_animset
from rc2016.formats.animation_samples import decode_clip
from rc2016.formats.animation_transforms import sample_transforms
from rc2016.formats.archive import Archive, locate
from rc2016.pipeline.blender_support import bake as bake_source_action, targets as source_targets
from rc2016.formats.skeleton import inspect_model, worlds
from rc2016.formats.native_model import decode_model
from rc2016.pipeline.timeline import timeline
from rc2016.pipeline.scene_defaults import audit_scene

# Set from the job JSON that run.py writes (_read_job).
ARCHIVE = None
SOURCE_BLEND = None
SOURCE_BLEND_SHA = "9308d1264245ece8e8b6fa4edf158e8b937a5ed399d5ca388b8f38acefcd5d77"
MODEL_PATHS = {
    "main": "equipment/weapon/wpn_pyrociter/wpn_pyrociter.model",
    "v1": "equipment/weapon/wpn_pyrociter/bangle_set/wpn_pyrociter_bangle_v1_barrel/wpn_pyrociter_bangle_v1_barrel.model",
    "v5": "equipment/weapon/wpn_pyrociter/bangle_set/wpn_pyrociter_bangle_v5_barrel/wpn_pyrociter_bangle_v5_barrel.model",
}
MODEL_IDS = {"main": "7ad48452acac82c9edee", "v1": "7699113e93128f485d10", "v5": "86f736dced71288e7503"}
MODEL_SHA = {
    "main": "b652ff3c6e8ce6a5a1dce7677866cd562df56a42a547b199eaa9b9503523f4f8",
    "v1": "20c14e7d499263522446a6245e7acacb73b7f410f0a050fe51469020b8976d09",
    "v5": "4474f86943c3249e2fb9bec46b9184d62b5fa6f6c3f6c7f65f825ef66bccae0b",
}
PACKED_MODEL_SHA = {
    "main": "ca051b54d065939d83e2606a95aa599ce04d67a14be5800029ff7842c6d0a756",
    "v5": "ef0af73728cd8709186ad4b2e0dfaa1cf4ee08b3f45b29539403ec86b1334b8a",
}
ANIMSET_PATH = "equipment/weapon/wpn_pyrociter/wpn_pyrociter.animset"
ANIMSET_ID = "473a0e630c85158c0f8d"
ANIMSET_PACKED_SHA = "0cee8fa86b94243ce6199f6e67d2ac407b87f1ce291bf6c8de100c3ea2629a5f"
ANIMSET_DECODED_SHA = "fc94dc61b4ac1a7070c8cde35fa391aeeaeca63cba2e1063dffc7f73f6befb0e"
UNMATCHED = "0x1A61FAF6"
FPS = 30
MATRIX_TOL = 0.001
SKIN_TOL = 0.005
RIG_NAMES = {
    "main": "Source animation rig",
    "v1": "Source animation rig.001",
    "v5": "Source animation rig.002",
}
COLLECTION_NAMES = {"main": "PYROCITOR_SHARED_MAIN", "v1": "PYROCITOR_V1_BARREL", "v5": "PYROCITOR_V5_BARREL"}
OUT_ROOT = None
BLEND = None
BUILD_LOG = None
VALIDATE_LOG = None
# The pinned proof that run.py checks before integrating; never written by a job.
REPORT_JSON = HERE / "animation_proof.json"
JOB_CONFIG = None
JOB_REPORT_JSON = None
JOB_MODE = False


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _job_path(value, label):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path: {path}")
    return path.resolve()


def _read_job(job_path: Path):
    global ARCHIVE, SOURCE_BLEND, SOURCE_BLEND_SHA, OUT_ROOT, BLEND
    global BUILD_LOG, VALIDATE_LOG, JOB_CONFIG, JOB_REPORT_JSON, JOB_MODE
    job_path = _job_path(job_path, "job JSON")
    if not job_path.is_file():
        raise FileNotFoundError(job_path)
    job = json.loads(job_path.read_text(encoding="utf-8"))
    required = {"source_blend", "source_sha256", "output_blend", "report_json", "archive_root"}
    missing = sorted(required - set(job))
    if missing:
        raise ValueError(f"Job JSON is missing required fields: {missing}")
    SOURCE_BLEND = _job_path(job["source_blend"], "source_blend")
    BLEND = _job_path(job["output_blend"], "output_blend")
    JOB_REPORT_JSON = _job_path(job["report_json"], "report_json")
    source_sha = str(job["source_sha256"]).lower()
    if len(source_sha) != 64 or any(c not in "0123456789abcdef" for c in source_sha):
        raise ValueError("source_sha256 must be a 64-character SHA-256 hex string")
    SOURCE_BLEND_SHA = source_sha
    if SOURCE_BLEND == BLEND:
        raise ValueError("source_blend and output_blend resolve to the same file")
    if JOB_REPORT_JSON in {SOURCE_BLEND, BLEND, job_path, REPORT_JSON.resolve()}:
        raise ValueError("report_json must be a separate job report path, not a blend or fixed research proof report")
    archive_root = Path(job["archive_root"]).expanduser()
    if not archive_root.is_absolute():
        archive_root = ROOT / archive_root
    ARCHIVE = archive_root.resolve()
    if not ARCHIVE.is_dir():
        raise NotADirectoryError(f"archive_root is not a readable directory: {ARCHIVE}")
    OUT_ROOT = BLEND.parent
    BUILD_LOG = OUT_ROOT / "reports" / "logs" / "build.log"
    VALIDATE_LOG = OUT_ROOT / "reports" / "logs" / "validate.log"
    JOB_CONFIG = {**job, "source_blend": str(SOURCE_BLEND), "source_sha256": SOURCE_BLEND_SHA,
                  "output_blend": str(BLEND), "report_json": str(JOB_REPORT_JSON),
                  "archive_root": str(ARCHIVE), "job_json": str(job_path)}
    JOB_MODE = True
    return JOB_CONFIG


def _verify_source_hash():
    if not SOURCE_BLEND.is_file():
        raise FileNotFoundError(SOURCE_BLEND)
    actual = sha(SOURCE_BLEND.read_bytes())
    if actual != SOURCE_BLEND_SHA:
        raise AssertionError(f"Source blend SHA-256 mismatch: expected {SOURCE_BLEND_SHA}, got {actual}")
    return actual


def jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "to_list"):
        return jsonable(value.to_list())
    if hasattr(value, "items"):
        return {str(k): jsonable(v) for k, v in value.items()}
    try:
        return [jsonable(v) for v in value]
    except TypeError:
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)


def matrix_list(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def max_delta(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c])) for r in range(4) for c in range(4))


def transform_rows(rows, frame):
    return source_targets(rows, frame, {"kind": "absolute skeletal", "inheritance": "full"})


def rig_right_corrections(rig):
    return {name: Matrix(matrix) for name, matrix in json.loads(rig["source_right_corrections"]).items()}


def get_overview_track(rig):
    return next((track for track in rig.animation_data.nla_tracks
                 if track.name == "Pyrociter source clips | coordinated across main, V1 and V5"), None)


def source_assets(scratch: Path):
    archive = Archive(locate(ARCHIVE), scratch, lambda *_a, **_k: None)
    by_path = {entry["path"]: entry for entry in archive.entries.values()}
    models = {}
    decoded = {}
    native = {}
    packed = {}
    for label, path in MODEL_PATHS.items():
        entry = by_path[path]
        assert entry["id"] == MODEL_IDS[label], (label, entry["id"])
        payload, packed_sha = archive.bytes(entry["id"])
        model_path = archive.decode(entry["id"])
        data = model_path.read_bytes()
        decoded_sha = sha(data)
        assert decoded_sha == MODEL_SHA[label], (label, decoded_sha)
        if label in PACKED_MODEL_SHA:
            assert packed_sha == PACKED_MODEL_SHA[label], (label, packed_sha)
        models[label] = inspect_model(model_path)
        decoded[label] = dict(entry=entry, packed=payload, packed_sha=packed_sha, path=model_path,
                              bytes=data, decoded_sha=decoded_sha)
        native[label] = decode_model(model_path, lod="All")
    anim_entry = by_path[ANIMSET_PATH]
    assert anim_entry["id"] == ANIMSET_ID
    anim_packed, anim_packed_sha = archive.bytes(anim_entry["id"])
    assert anim_packed_sha == ANIMSET_PACKED_SHA
    anim_path = archive.decode(anim_entry["id"])
    anim_bytes = anim_path.read_bytes()
    anim_decoded_sha = sha(anim_bytes)
    assert anim_decoded_sha == ANIMSET_DECODED_SHA
    all_hash_names = {int(row["hash"], 16): row["name"] for label in models for row in models[label]["skeleton"]}
    clips = []
    for container in inspect_animset(anim_path)["containers"]:
        if container.get("metadata"):
            clip = decode_clip(anim_bytes, container["offset"], all_hash_names)
            raw_start = int(clip["source_offset"])
            raw_end = raw_start + int(clip["source_size"])
            clip["raw_sha256"] = sha(anim_bytes[raw_start:raw_end])
            clips.append(clip)
    clips.sort(key=lambda clip: clip["name"])
    assert [clip["name"] for clip in clips] == [
        "Fire_wpn_Pyrociter", "Idle_wpn_Pyrociter", "Open_wpn_Pyrociter"]
    assert [len(sample_transforms(clip)) for clip in clips] == [15, 21, 13]
    assert all(clip["metadata"]["sample_rate_fps"] == FPS for clip in clips)
    for clip in clips:
        hashes = {joint["hash"] for joint in clip["joints"]}
        assert len(hashes) == 10 and UNMATCHED in hashes
    return archive, models, decoded, native, anim_entry, anim_packed, anim_packed_sha, anim_decoded_sha, clips


def hierarchy_signature(rows):
    return {row["hash"]: {"name": row["name"],
                          "parent_hash": rows[row["parent"]]["hash"] if row["parent"] >= 0 else None,
                          "flags": int(row["flags"])} for row in rows}


def validate_native_bindings(models, native):
    main_rows = models["main"]["skeleton"]
    v1_rows = models["v1"]["skeleton"]
    v5_rows = models["v5"]["skeleton"]
    signatures = {label: hierarchy_signature(models[label]["skeleton"]) for label in MODEL_PATHS}
    assert len(v1_rows) == 3, len(v1_rows)
    assert set(signatures["v1"]) <= set(signatures["main"])
    assert set(signatures["main"]) <= set(signatures["v5"])
    for label in ("v1", "v5"):
        for h in set(signatures[label]) & set(signatures["main"]):
            assert signatures[label][h] == signatures["main"][h], (label, h, signatures[label][h], signatures["main"][h])
    matrices = {label: {row["hash"]: Matrix(m.tolist()) for row, m in
                        zip(models[label]["skeleton"], worlds(models[label]["skeleton"], [], "full"))}
                for label in MODEL_PATHS}
    invbinds = {label: {models[label]["skeleton"][i]["hash"]: Matrix(m) for i, m in
                        enumerate(models[label]["inverse_bind_matrices"])} for label in MODEL_PATHS}
    shared_checks = []
    for label in ("v1", "v5"):
        for h in sorted(set(signatures[label]) & set(signatures["main"])):
            rest_delta = max_delta(matrices[label][h], matrices["main"][h])
            inverse_delta = max_delta(invbinds[label][h], invbinds["main"][h])
            assert rest_delta <= 1e-6 and inverse_delta <= 1e-6, (label, h, rest_delta, inverse_delta)
            shared_checks.append({"model": label, "joint_hash": h, "joint_name": signatures[label][h]["name"],
                                  "main_parent_hash": signatures["main"][h]["parent_hash"],
                                  "variant_parent_hash": signatures[label][h]["parent_hash"],
                                  "max_rest_world_delta": rest_delta, "max_inverse_bind_delta": inverse_delta})
    # Native weight rows carry joint-table indices. Establish exact use coverage before binding any mesh.
    influence_rows = {}
    for label, model in native.items():
        row_hashes = [r["hash"] for r in models[label]["skeleton"]]
        used = set()
        per_section = []
        for section in model["meshes"]:
            used_ids = set()
            for weights in section["weights"]:
                if weights:
                    for joint, weight in weights:
                        if float(weight) > 0:
                            used_ids.add(int(joint))
                            used.add(row_hashes[int(joint)])
            per_section.append({"section": section["index"], "vertices": len(section["positions"]),
                                "triangles": len(section["triangles"]),
                                "weighted_vertices": sum(bool(w) for w in section["weights"]),
                                "joint_hashes_used": [row_hashes[i] for i in sorted(used_ids)]})
        influence_rows[label] = {"joint_hashes_used": sorted(used), "sections": per_section}
    assert set(influence_rows["v1"]["joint_hashes_used"]) <= set(signatures["v1"])
    assert set(influence_rows["v1"]["joint_hashes_used"]) <= set(signatures["main"])
    return shared_checks, signatures, matrices, invbinds, influence_rows


def object_for_section(label, section_index):
    matches = [obj for obj in bpy.data.objects if obj.type == "MESH"
               and obj.get("source_model_sha256") == MODEL_SHA[label]
               and int(obj.get("source_subset_index", -1)) == int(section_index)]
    if len(matches) != 1:
        raise AssertionError(f"Expected one scene mesh for {label} section {section_index}, got {[o.name for o in matches]}")
    return matches[0]


def verify_scene_meshes(models, native):
    checks = {}
    for label, native_model in native.items():
        sections = []
        rows = models[label]["skeleton"]
        for section in native_model["meshes"]:
            obj = object_for_section(label, section["index"])
            expected_vertex_count = len(section["positions"])
            assert len(obj.data.vertices) == expected_vertex_count
            assert len(obj.data.polygons) == len(section["triangles"])
            position_error = max(max(abs(float(obj.data.vertices[i].co[a]) - float(section["positions"][i][a]))
                                      for a in range(3)) for i in range(expected_vertex_count))
            expected_faces = {tuple(sorted(map(int, tri))) for tri in section["triangles"]}
            actual_faces = {tuple(sorted(int(i) for i in poly.vertices)) for poly in obj.data.polygons}
            if position_error > 1e-6 or expected_faces != actual_faces:
                raise AssertionError(f"Scene/source geometry mismatch {label} section {section['index']}: "
                                     f"position_error={position_error}, expected_faces={len(expected_faces)}, "
                                     f"actual_faces={len(actual_faces)}, face_symmetric_difference={len(expected_faces ^ actual_faces)}")
            group_by_index = {group.index: group.name for group in obj.vertex_groups}
            max_weight_error, mismatches, duplicates = 0.0, 0, 0
            for vertex_index, weights in enumerate(section["weights"]):
                expected = {}
                if weights:
                    ids = [int(j) for j, w in weights if float(w) > 0]
                    duplicates += len(ids) != len(set(ids))
                    for joint_index, weight in weights:
                        name = rows[int(joint_index)]["name"]
                        if float(weight) > 0:
                            expected[name] = expected.get(name, 0.0) + float(weight)
                actual = {group_by_index[item.group]: float(item.weight)
                          for item in obj.data.vertices[vertex_index].groups if item.weight > 0}
                if set(expected) != set(actual):
                    mismatches += 1
                for name in set(expected) | set(actual):
                    max_weight_error = max(max_weight_error, abs(expected.get(name, 0.0) - actual.get(name, 0.0)))
            modifiers = [m for m in obj.modifiers if m.type == "ARMATURE"]
            if len(modifiers) != 1 or modifiers[0].object != bpy.data.objects[RIG_NAMES[label]]:
                raise AssertionError(f"Wrong animation rig on {obj.name}")
            rig_matrix_error = max_delta(obj.matrix_world, modifiers[0].object.matrix_world)
            if mismatches or max_weight_error > 1e-5 or rig_matrix_error > 1e-6:
                raise AssertionError(f"Weight/rig bind mismatch {label} section {section['index']}: "
                                     f"mismatches={mismatches}, weight={max_weight_error}, xform={rig_matrix_error}")
            sections.append({"section": section["index"], "object": obj.name, "vertices": expected_vertex_count,
                             "triangles": len(section["triangles"]), "max_position_delta": position_error,
                             "max_weight_delta": max_weight_error, "weight_group_mismatches": mismatches,
                             "duplicate_native_joint_ids_coalesced": duplicates, "mesh_rig_world_matrix_delta": rig_matrix_error})
        checks[label] = sections
    return checks


def _socket_value(socket):
    if not hasattr(socket, "default_value"):
        return None
    try:
        return jsonable(socket.default_value)
    except Exception:
        return str(socket.default_value)


def _idprops(id_block):
    result = {}
    for key in id_block.keys():
        try:
            val = id_block[key]
        except Exception:
            continue
        if isinstance(val, (str, int, float, bool)) or val is None or hasattr(val, "to_list"):
            result[str(key)] = jsonable(val)
    return result


def preservation_snapshot():
    scene = bpy.context.scene
    camera = scene.camera
    cam = None
    if camera:
        d = camera.data
        cam = {"name": camera.name, "matrix_world": matrix_list(camera.matrix_world),
               "type": d.type, "lens": float(d.lens), "shift_x": float(d.shift_x), "shift_y": float(d.shift_y),
               "clip_start": float(d.clip_start), "clip_end": float(d.clip_end),
               "sensor_fit": d.sensor_fit, "sensor_width": float(d.sensor_width), "sensor_height": float(d.sensor_height),
               "ortho_scale": float(d.ortho_scale), "props": _idprops(d),
               "constraints": [{"name": c.name, "type": c.type, "mute": c.mute,
                                "influence": float(c.influence)} for c in camera.constraints]}
    lock_rows = []
    for screen in bpy.data.screens:
        lock_rows.append({"screen": screen.name, "locks": [bool(area.spaces.active.lock_camera)
                        for area in screen.areas if area.type == "VIEW_3D"]})
    world = scene.world
    world_snapshot = None
    if world:
        world_snapshot = {"name": world.name, "color": jsonable(world.color), "props": _idprops(world),
                          "use_nodes": bool(world.use_nodes), "nodes": [], "links": []}
        if world.use_nodes:
            for n in world.node_tree.nodes:
                world_snapshot["nodes"].append({"name": n.name, "type": n.bl_idname, "label": n.label,
                    "image": getattr(getattr(n, "image", None), "name", None),
                    "inputs": [(s.identifier, _socket_value(s)) for s in n.inputs if hasattr(s, "default_value")]})
            world_snapshot["nodes"].sort(key=lambda x: x["name"])
            world_snapshot["links"] = sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                                              for l in world.node_tree.links)
    materials = []
    for material in sorted(bpy.data.materials, key=lambda x: x.name):
        row = {"name": material.name, "props": _idprops(material), "use_nodes": bool(material.use_nodes),
               "blend_method": getattr(material, "blend_method", None),
               "surface_render_method": getattr(material, "surface_render_method", None), "nodes": [], "links": []}
        if material.use_nodes:
            for n in material.node_tree.nodes:
                row["nodes"].append({"name": n.name, "type": n.bl_idname, "label": n.label,
                    "image": getattr(getattr(n, "image", None), "name", None),
                    "inputs": [(s.identifier, _socket_value(s)) for s in n.inputs if hasattr(s, "default_value")]})
            row["nodes"].sort(key=lambda x: x["name"])
            row["links"] = sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                                   for l in material.node_tree.links)
        materials.append(row)
    images = []
    for image in sorted(bpy.data.images, key=lambda x: x.name):
        packed = image.packed_file
        packed_sha = None
        if packed:
            try:
                packed_sha = sha(bytes(packed.data))
            except Exception:
                packed_sha = None
        images.append({"name": image.name, "source": image.source, "size": list(image.size),
                       "packed": bool(packed), "packed_size": packed.size if packed else None,
                       "packed_sha256": packed_sha, "colorspace": image.colorspace_settings.name,
                       "alpha_mode": image.alpha_mode, "props": _idprops(image)})
    return {"camera": cam, "camera_to_view_locks": lock_rows, "world": world_snapshot,
            "materials": materials, "images": images}


def preservation_hash(snapshot):
    return sha(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def preservation_diff(left, right, prefix=""):
    if type(left) is not type(right):
        return [(prefix, left, right)]
    if isinstance(left, dict):
        result = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in left or key not in right:
                result.append((path, left.get(key, "<missing>"), right.get(key, "<missing>")))
            else:
                result.extend(preservation_diff(left[key], right[key], path))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return [(prefix + ".length", len(left), len(right))]
        result = []
        for i, (a, b) in enumerate(zip(left, right)):
            result.extend(preservation_diff(a, b, f"{prefix}[{i}]"))
        return result
    return [] if left == right else [(prefix, left, right)]


def collect_existing_actions(label, rig, clips):
    if label != "main":
        return None
    results = {}
    for clip in clips:
        matches = [a for a in bpy.data.actions if a.get("source_clip") == clip["name"]
                   and a.get("source_sample_sha256") == clip["sample_sha256"]]
        if len(matches) != 1:
            raise AssertionError(f"Expected one existing source Action for main rig {clip['name']}, found {[a.name for a in matches]}")
        action = matches[0]
        if len(action.slots) != 1 or not action.use_fake_user:
            raise AssertionError(f"Main action slot/fake-user missing: {action.name}")
        results[clip["name"]] = action
    return results


def make_actions_and_overview(models, clips):
    scene = bpy.context.scene
    intervals, overview_fps = timeline([dict(action=clip["name"], name=clip["name"], kind="absolute skeletal",
                                            frames=len(sample_transforms(clip)), fps=FPS) for clip in clips])
    rig_actions = {}
    for label in ("main", "v1", "v5"):
        rig = bpy.data.objects.get(RIG_NAMES[label])
        assert rig and rig.type == "ARMATURE", RIG_NAMES[label]
        rows = models[label]["skeleton"]
        hashes = {int(row["hash"], 16) for row in rows}
        # Verify the native rows exactly cover every non-unmatched action channel expected on this rig.
        for clip in clips:
            available = {joint["hash"] for joint in clip["joints"]}
            native_hashes = {row["hash"] for row in rows}
            assert native_hashes <= available, (label, clip["name"], native_hashes - available)
            assert hashes
        if label == "main":
            actions = collect_existing_actions(label, rig, clips)
        else:
            actions = {}
            rig.animation_data_create()
            rig.animation_data.action = None
            with tempfile.TemporaryDirectory(prefix=f"{label}_", dir=OUT_ROOT) as scratch_text:
                archive = Archive(locate(ARCHIVE), Path(scratch_text), lambda *_a, **_k: None)
                by_path = {e["path"]: e for e in archive.entries.values()}
                anim_entry = archive.decode(ANIMSET_ID)
                anim_bytes = anim_entry.read_bytes()
                names = {int(row["hash"], 16): row["name"] for row in rows}
                clip_map = {clip["name"]: clip for clip in clips}
                # Re-decode each clip with only this native model's hash map so unmatched and foreign channels stay unnamed.
                for container in inspect_animset(anim_entry)["containers"]:
                    if not container.get("metadata"):
                        continue
                    decoded_clip = decode_clip(anim_bytes, container["offset"], names)
                    clip = clip_map[decoded_clip["name"]]
                    entry = {"action": f"{label.upper()} | {clip['name']}", "name": clip["name"],
                             "kind": "absolute skeletal", "inheritance": "full", "fps": FPS,
                             "sample_sha256": clip["sample_sha256"], "raw_sha256": clip["raw_sha256"],
                             "animset_sha256": ANIMSET_DECODED_SHA, "name_hash": clip["metadata"]["name_hash"],
                             "source_duration_seconds": clip["metadata"]["duration_seconds"]}
                    action = bake_source_action(rig, rows, entry, sample_transforms(decoded_clip))
                    action["source_rig_role"] = label
                    action["source_sample_sha256"] = clip["sample_sha256"]
                    action["source_raw_sha256"] = clip["raw_sha256"]
                    action["source_animset_asset_id"] = ANIMSET_ID
                    action["source_animset_decoded_sha256"] = ANIMSET_DECODED_SHA
                    action["source_joint_hashes"] = json.dumps(sorted(row["hash"] for row in rows))
                    action["unmatched_channel_hashes"] = json.dumps([UNMATCHED])
                    assert len(action.slots) == 1 and action.use_fake_user
                    actions[clip["name"]] = action
        # Rebuild a coordinated timeline for each rig, keeping one Action/slot per state and clip.
        rig.animation_data_create()
        rig.animation_data.action = None
        for old_track in list(rig.animation_data.nla_tracks):
            rig.animation_data.nla_tracks.remove(old_track)
        track = rig.animation_data.nla_tracks.new()
        track.name = "Pyrociter source clips | coordinated across main, V1 and V5"
        for clip, interval in zip(clips, intervals):
            action = actions[clip["name"]]
            strip = track.strips.new(clip["name"], interval["start"], action)
            strip.action_slot = action.slots[0]
            strip.use_sync_length = False
            strip.action_frame_start = interval["action_start"]
            strip.action_frame_end = interval["nla_action_end"]
            strip.frame_start = interval["start"]
            strip.frame_end = interval["nla_end"]
            strip.scale = interval["sample_step"]
            strip.repeat = 1
            strip.extrapolation = "NOTHING"
            strip.blend_type = "REPLACE"
            strip.blend_in = 0
            strip.blend_out = 0
        rig_actions[label] = {"rig": rig, "actions": actions, "track": track, "rows": models[label]["skeleton"]}
    for marker in list(scene.timeline_markers):
        scene.timeline_markers.remove(marker)
    for clip, interval in zip(clips, intervals):
        scene.timeline_markers.new(clip["name"], frame=interval["start"])
    scene.render.fps = int(overview_fps)
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = intervals[-1]["end_inclusive"]
    scene.frame_set(1)
    return rig_actions, intervals, overview_fps


def set_variant_visibility(variant):
    assert variant in ("v1", "v5")
    for obj in bpy.context.scene.objects:
        source_hash = obj.get("source_model_sha256")
        if source_hash == MODEL_SHA["v1"]:
            obj.hide_set(variant != "v1")
            obj.hide_render = variant != "v1"
        elif source_hash == MODEL_SHA["v5"]:
            obj.hide_set(variant != "v5")
            obj.hide_render = variant != "v5"


def scene_visibility_snapshot():
    return {label: {"collection_hide_viewport": bpy.data.collections[COLLECTION_NAMES[label]].hide_viewport,
                    "collection_hide_render": bpy.data.collections[COLLECTION_NAMES[label]].hide_render,
                    "objects": [{"name": obj.name, "hide_set": obj.hide_get(), "hide_render": obj.hide_render}
                                for obj in bpy.context.scene.objects if obj.get("source_model_sha256") == MODEL_SHA[label]]}
            for label in ("main", "v1", "v5")}


def write_embedded_controls(rig_actions, clips, intervals, overview_fps, preserve_hash):
    rest_actions = {"main": "Original source rest pose",
                    "v1": "Original source rest pose.001",
                    "v5": "Original source rest pose.002"}
    for name in rest_actions.values():
        action = bpy.data.actions.get(name)
        if action:
            action.use_fake_user = True
    catalog = {"rigs": {label: {"object": info["rig"].name,
                                "rest_action": rest_actions[label] if bpy.data.actions.get(rest_actions[label]) else None,
                                "actions": {name: action.name for name, action in info["actions"].items()}}
                       for label, info in rig_actions.items()},
               "clips": [{"name": clip["name"], "frames": len(sample_transforms(clip)), "fps": FPS,
                          "start": interval["start"], "end_inclusive": interval["end_inclusive"]}
                         for clip, interval in zip(clips, intervals)],
               "overview_fps": overview_fps, "unmatched_channels": [UNMATCHED],
               "source_scene_sha256": SOURCE_BLEND_SHA, "preservation_snapshot_sha256": preserve_hash}
    catalog_text = bpy.data.texts.get("Pyrociter Animation Catalog.json") or bpy.data.texts.new("Pyrociter Animation Catalog.json")
    catalog_text.clear()
    catalog_text.write(json.dumps(catalog, indent=2) + "\n")
    code = '''"""Portable controls for synchronized Pyrociter source Actions; run once per Blender session."""
import json
import bpy

def catalog():
    return json.loads(bpy.data.texts["Pyrociter Animation Catalog.json"].as_string())

def rigs():
    return [bpy.data.objects[row["object"]] for row in catalog()["rigs"].values()]

def select_clip(index):
    data = catalog(); clip = data["clips"][index]; scene = bpy.context.scene
    for label, row in data["rigs"].items():
        obj = bpy.data.objects[row["object"]]
        for track in obj.animation_data.nla_tracks: track.mute = True
        action = bpy.data.actions[row["actions"][clip["name"]]]
        obj.animation_data.action = action; obj.animation_data.action_slot = action.slots[0]
    scene.render.fps = clip["fps"]; scene.render.fps_base = 1.0
    scene.frame_start = 1; scene.frame_end = clip["frames"]; scene.frame_set(1)
    scene["active_source_clip"] = clip["name"]; bpy.context.view_layer.update()

def play_all():
    data = catalog(); scene = bpy.context.scene
    for obj in rigs():
        obj.animation_data.action = None
        for track in obj.animation_data.nla_tracks: track.mute = False
    scene.render.fps = int(data["overview_fps"]); scene.render.fps_base = 1.0
    scene.frame_start = 1; scene.frame_end = data["clips"][-1]["end_inclusive"]
    scene.frame_set(1); scene["active_source_clip"] = "Sequential overview"; bpy.context.view_layer.update()

def restore_rest():
    data = catalog(); scene = bpy.context.scene
    for label, row in data["rigs"].items():
        obj = bpy.data.objects[row["object"]]
        for track in obj.animation_data.nla_tracks: track.mute = True
        if row["rest_action"]:
            action = bpy.data.actions[row["rest_action"]]
            obj.animation_data.action = action; obj.animation_data.action_slot = action.slots[0]
        else:
            obj.animation_data.action = None
    scene.frame_start = 1; scene.frame_end = 1; scene.frame_set(1)
    scene["active_source_clip"] = "Original source rest pose"; bpy.context.view_layer.update()

class PS4PYROCITOR_OT_select(bpy.types.Operator):
    bl_idname = "ps4pyrocitor.select"; bl_label = "Select synchronized source clip"
    index: bpy.props.IntProperty()
    def execute(self, context): select_clip(self.index); return {"FINISHED"}

class PS4PYROCITOR_OT_all(bpy.types.Operator):
    bl_idname = "ps4pyrocitor.all"; bl_label = "Play all source clips"
    def execute(self, context): play_all(); return {"FINISHED"}

class PS4PYROCITOR_OT_rest(bpy.types.Operator):
    bl_idname = "ps4pyrocitor.rest"; bl_label = "Restore source rest pose"
    def execute(self, context): restore_rest(); return {"FINISHED"}

class PS4PYROCITOR_PT_controls(bpy.types.Panel):
    bl_label = "Pyrocitor source animation"; bl_idname = "PS4PYROCITOR_PT_controls"
    bl_space_type = "VIEW_3D"; bl_region_type = "UI"; bl_category = "Animations"
    def draw(self, context):
        layout = self.layout
        for i, clip in enumerate(catalog()["clips"]):
            layout.operator("ps4pyrocitor.select", text=clip["name"]).index = i
        layout.operator("ps4pyrocitor.all", text="Play synchronized overview")
        layout.operator("ps4pyrocitor.rest", text="Restore source rest pose")
        layout.prop(context.scene, "frame_current", text="Frame")
        layout.operator("screen.animation_play", text="Play / pause", icon="PLAY")
        layout.operator("render.render", text="Render current frame")

def register():
    for cls in (PS4PYROCITOR_OT_select, PS4PYROCITOR_OT_all, PS4PYROCITOR_OT_rest, PS4PYROCITOR_PT_controls):
        old = getattr(bpy.types, cls.__name__, None)
        if old: bpy.utils.unregister_class(old)
        bpy.utils.register_class(cls)

if __name__ == "__main__": register()
'''
    control_text = bpy.data.texts.get("Animation Controls.py") or bpy.data.texts.new("Animation Controls.py")
    control_text.clear()
    control_text.write(code)
    start = bpy.data.texts.get("START HERE") or bpy.data.texts.new("START HERE")
    start.clear()
    start.write("The saved source clip overview plays immediately with Space/Play. For synchronized per-clip Actions, open Animation Controls.py in the Text Editor, Run Script once, then use N > Animations. The selector drives the main, V1, and V5 source rigs together. The unmatched 0x1A61FAF6 channel is documented as unsupported and excluded.\n")


def build():
    if JOB_MODE:
        if BLEND.exists():
            raise FileExistsError(f"Refusing to overwrite existing job output: {BLEND}")
        OUT_ROOT.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.open_mainfile(filepath=str(SOURCE_BLEND))
        _verify_source_hash()
    else:
        OUT_ROOT.mkdir(parents=True, exist_ok=False)
    (OUT_ROOT / "reports/logs").mkdir(parents=True, exist_ok=JOB_MODE)
    _verify_source_hash()
    source_preservation_snapshot = preservation_snapshot()
    source_snapshot_hash = preservation_hash(source_preservation_snapshot)
    baseline_defaults = audit_scene(bpy.context.scene)
    if baseline_defaults.get("mismatches"):
        raise AssertionError(f"Input scene defaults fail audit: {baseline_defaults['mismatches']}")
    with tempfile.TemporaryDirectory(prefix="src_", dir=OUT_ROOT) as scratch_text:
        (archive, models, decoded, native, anim_entry, anim_packed, anim_packed_sha,
         anim_decoded_sha, clips) = source_assets(Path(scratch_text))
        shared_checks, signatures, rest_matrices, inverse_binds, influence_rows = validate_native_bindings(models, native)
        # The native model inventory identifies each mesh by its exact source model hash and subset index.
        mesh_checks = verify_scene_meshes(models, native)
        rig_actions, intervals, overview_fps = make_actions_and_overview(models, clips)
        write_embedded_controls(rig_actions, clips, intervals, overview_fps, source_snapshot_hash)
        baseline_text = bpy.data.texts.get("Pyrocitor scene preservation baseline.json") or bpy.data.texts.new("Pyrocitor scene preservation baseline.json")
        baseline_text.clear()
        baseline_text.write(json.dumps({"sha256": source_snapshot_hash, "snapshot": source_preservation_snapshot},
                                       indent=2) + "\n")
        for rig in (rig_actions[l]["rig"] for l in ("main", "v1", "v5")):
            rig["source_animation_animset_asset_id"] = ANIMSET_ID
            rig["source_animation_animset_decoded_sha256"] = ANIMSET_DECODED_SHA
            rig["unsupported_source_animation_channels"] = json.dumps([UNMATCHED])
        bpy.context.scene["pyrocitor_animation_proof_source_scene_sha256"] = SOURCE_BLEND_SHA
        if JOB_MODE:
            bpy.context.scene["pyrocitor_animation_job_metadata"] = json.dumps({
                "source_blend": str(SOURCE_BLEND), "source_sha256": SOURCE_BLEND_SHA,
                "output_blend": str(BLEND), "archive_root": str(ARCHIVE),
                "build_pid": os.getpid(), "build_unix_time": time.time(),
            }, sort_keys=True)
        bpy.context.scene["pyrocitor_animation_integration_scope"] = "native main, source-proven V1 three-joint subset, and native V5 nine-joint rig; unmatched 0x1A61FAF6 excluded"
        # Preserve the saved V1 default using ordinary object eye/render hiding only.
        set_variant_visibility("v1")
        if preservation_hash(preservation_snapshot()) != source_snapshot_hash:
            raise AssertionError("Camera/world/material/image metadata changed during integration build")
        if audit_scene(bpy.context.scene).get("mismatches"):
            raise AssertionError(f"Shared render defaults changed: {audit_scene(bpy.context.scene)['mismatches']}")
        bpy.ops.wm.save_as_mainfile(filepath=str(BLEND), compress=True)
        report = {"schema": "pyrocitor-switchable-animation-integration-v1", "status": "built_waiting_for_separate_process_reopen_validation",
                  "source_blend": {"path": str(SOURCE_BLEND), "sha256": SOURCE_BLEND_SHA},
                  "source_models": {label: {"asset_id": decoded[label]["entry"]["id"], "path": MODEL_PATHS[label],
                                                "packed_sha256": decoded[label]["packed_sha"],
                                                "decoded_sha256": decoded[label]["decoded_sha"],
                                                "joint_count": len(models[label]["skeleton"]),
                                                "joint_hashes": [r["hash"] for r in models[label]["skeleton"]],
                                                "native_influence_inventory": influence_rows[label]}
                                   for label in MODEL_PATHS},
                  "native_shared_bind_checks": shared_checks,
                  "v1_mapping_claim": "Each V1 joint is proven by archive-native hash, parent hash, rest-world matrix, inverse bind, source geometry, and source weights; no name-only binding is used.",
                  "animset": {"path": ANIMSET_PATH, "asset_id": anim_entry["id"], "packed_sha256": anim_packed_sha,
                              "decoded_sha256": anim_decoded_sha},
                  "clips": [{"name": c["name"], "frames": len(sample_transforms(c)), "fps": FPS,
                             "raw_sha256": c["raw_sha256"], "sample_sha256": c["sample_sha256"],
                             "source_joint_hash_count": len({j["hash"] for j in c["joints"]}),
                             "unsupported_hash": UNMATCHED,
                             "timeline": {"start": i["start"], "end_inclusive": i["end_inclusive"], "nla_end": i["nla_end"]}}
                            for c, i in zip(clips, intervals)],
                  "per_rig_actions": {label: {"rig": data["rig"].name,
                                                "action_names": {name: action.name for name, action in data["actions"].items()},
                                                "slots": {name: action.slots[0].identifier for name, action in data["actions"].items()},
                                                "nla_track": data["track"].name}
                                      for label, data in rig_actions.items()},
                  "native_geometry_weights": mesh_checks,
                  "preservation": {"snapshot_sha256": source_snapshot_hash, "camera_world_material_source_metadata": "captured for exact reopened comparison", "saved_v1_visibility": scene_visibility_snapshot()},
                  "shared_render_defaults": audit_scene(bpy.context.scene),
                  "proof_blend": {"path": str(BLEND), "size_bytes": BLEND.stat().st_size, "sha256": sha(BLEND.read_bytes())},
                  "separate_process_reopen": False, "unmatched_channels": [{"hash": UNMATCHED, "status": "unsupported; excluded from all Actions"}],
                  "full_ten_channel_equivalence": "not claimed"}
        return report


def validate_native_mesh_skin(label, model, native_model, rig, clip, source_frame, require_deformation):
    rows = model["skeleton"]
    sample = sample_transforms(clip)[source_frame - 1]
    targets = transform_rows(rows, sample)
    depgraph = bpy.context.evaluated_depsgraph_get()
    depgraph.update()
    total, maximum, max_displacement = 0, 0.0, 0.0
    section_rows = []
    for section in native_model["meshes"]:
        obj = object_for_section(label, section["index"])
        evaluated_obj = obj.evaluated_get(depgraph)
        eval_mesh = evaluated_obj.to_mesh()
        try:
            assert len(eval_mesh.vertices) == len(section["positions"])
            section_error, section_disp = 0.0, 0.0
            for vi, (position, weights) in enumerate(zip(section["positions"], section["weights"])):
                point = Vector((float(position[0]), float(position[1]), float(position[2]), 1.0))
                expected = Vector((0.0, 0.0, 0.0, 0.0))
                for joint_index, weight in weights or []:
                    weight = float(weight)
                    if weight <= 0:
                        continue
                    joint_index = int(joint_index)
                    row = rows[joint_index]
                    name = row["name"]
                    invbind = Matrix(model["inverse_bind_matrices"][joint_index])
                    expected += (targets[name] @ invbind @ point) * weight
                actual = eval_mesh.vertices[vi].co
                err = max(abs(float(actual[i]) - float(expected[i])) for i in range(3))
                disp = max(abs(float(expected[i]) - float(point[i])) for i in range(3))
                section_error = max(section_error, err)
                section_disp = max(section_disp, disp)
                if not any(weights or []):
                    # Unweighted native vertices should remain in place.
                    section_error = max(section_error, max(abs(float(actual[i]) - float(point[i])) for i in range(3)))
            total += len(section["positions"])
            maximum = max(maximum, section_error)
            max_displacement = max(max_displacement, section_disp)
            section_rows.append({"section": section["index"], "vertices": len(section["positions"]),
                                 "max_evaluated_vertex_error": section_error,
                                 "max_source_pose_displacement": section_disp})
        finally:
            evaluated_obj.to_mesh_clear()
    if maximum > SKIN_TOL:
        raise AssertionError(f"{label} evaluated native skin error {maximum} > {SKIN_TOL}")
    if require_deformation and max_displacement <= 1e-7:
        raise AssertionError(f"{label} {clip['name']} frame {source_frame} did not deform any vertices")
    return {"model": label, "clip": clip["name"], "source_frame": source_frame,
            "overview_frame": next(i["start"] for c, i in zip(ALL_CLIPS, ALL_INTERVALS) if c["name"] == clip["name"]) + source_frame - 1,
            "vertices_checked": total, "max_evaluated_vertex_error": maximum,
            "max_deformation_from_bind": max_displacement, "deformed": max_displacement > 1e-7,
            "nonzero_deformation_required": require_deformation, "sections": section_rows}


ALL_CLIPS = []
ALL_INTERVALS = []


def validate():
    if not BLEND.is_file():
        raise FileNotFoundError(BLEND)
    _verify_source_hash()
    with tempfile.TemporaryDirectory(prefix="verify_", dir=OUT_ROOT) as scratch_text:
        archive, models, decoded, native, anim_entry, anim_packed, anim_packed_sha, anim_decoded_sha, clips = source_assets(Path(scratch_text))
        shared_checks, signatures, _, _, influence_rows = validate_native_bindings(models, native)
        mesh_checks = verify_scene_meshes(models, native)
        preserve_text = bpy.data.texts.get("Pyrocitor scene preservation baseline.json")
        if not preserve_text:
            raise AssertionError("Embedded preservation baseline is missing")
        source_preserve = json.loads(preserve_text.as_string())
        actual_preserve = preservation_snapshot()
        preserve_hash = preservation_hash(actual_preserve)
        if preserve_hash != source_preserve["sha256"]:
            print("PRESERVATION_DIFF", json.dumps(preservation_diff(source_preserve["snapshot"], actual_preserve)[:60], ensure_ascii=False), flush=True)
            raise AssertionError(f"Camera/world/material/image source metadata changed: {preserve_hash} != {source_preserve['sha256']}")
        scene = bpy.context.scene
        if scene.get("pyrocitor_animation_proof_source_scene_sha256") != SOURCE_BLEND_SHA:
            raise AssertionError("Reopened scene does not record the canonical source blend identity")
        if JOB_MODE:
            embedded = json.loads(scene.get("pyrocitor_animation_job_metadata", "{}"))
            for key in ("source_blend", "source_sha256", "output_blend", "archive_root"):
                if embedded.get(key) != JOB_CONFIG[key]:
                    raise AssertionError(f"Embedded job metadata mismatch for {key}: {embedded.get(key)!r} != {JOB_CONFIG[key]!r}")
            if embedded.get("build_pid") == os.getpid():
                raise AssertionError("Validation must run in a separate Blender process from build")
        if audit_scene(scene).get("mismatches"):
            raise AssertionError(f"Render defaults changed: {audit_scene(scene)['mismatches']}")
        intervals, overview_fps = timeline([dict(action=c["name"], name=c["name"], kind="absolute skeletal",
                                                frames=len(sample_transforms(c)), fps=FPS) for c in clips])
        global ALL_CLIPS, ALL_INTERVALS
        ALL_CLIPS, ALL_INTERVALS = clips, intervals
        per_rig_checks = {}
        action_refs = {}
        total_bone_comparisons = 0
        for label in ("main", "v1", "v5"):
            rig = bpy.data.objects.get(RIG_NAMES[label])
            if not rig or rig.type != "ARMATURE":
                raise AssertionError(f"Missing rig {RIG_NAMES[label]}")
            track = get_overview_track(rig)
            if not track or len(track.strips) != len(clips):
                raise AssertionError(f"NLA track/strip mismatch on {label}")
            rows = models[label]["skeleton"]
            actions = {}
            for clip in clips:
                matches = [a for a in bpy.data.actions if a.get("source_clip") == clip["name"]
                           and a.get("source_sample_sha256") == clip["sample_sha256"]
                           and ((label == "main" and not a.get("source_rig_role"))
                                or a.get("source_rig_role") == label)]
                if len(matches) != 1:
                    raise AssertionError(f"Action mismatch for {label}/{clip['name']}: {[a.name for a in matches]}")
                action = matches[0]
                if len(action.slots) != 1 or not action.use_fake_user:
                    raise AssertionError(f"Action slot/fake user missing for {label}/{clip['name']}")
                if action.get("source_frame_count") != len(sample_transforms(clip)) or float(action.get("source_fps")) != FPS:
                    raise AssertionError(f"Source timing mismatch for {label}/{clip['name']}")
                hashes = [row["hash"] for row in rows]
                if label in ("v1", "v5"):
                    assert json.loads(action["source_joint_hashes"]) == sorted(hashes)
                    assert json.loads(action["unmatched_channel_hashes"]) == [UNMATCHED]
                actions[clip["name"]] = action
            action_refs[label] = actions
            direct_rows, nla_rows, boundary_rows = [], [], []
            right = rig_right_corrections(rig)
            for ci, (clip, interval) in enumerate(zip(clips, intervals)):
                action = actions[clip["name"]]
                strip = track.strips[ci]
                expected_bounds = (interval["start"], interval["nla_end"], interval["action_start"], interval["nla_action_end"])
                actual_bounds = (float(strip.frame_start), float(strip.frame_end),
                                 float(strip.action_frame_start), float(strip.action_frame_end))
                if actual_bounds != tuple(float(x) for x in expected_bounds) or strip.action_slot != action.slots[0]:
                    raise AssertionError(f"NLA bounds/slot mismatch {label}/{clip['name']}: {actual_bounds} != {expected_bounds}")
                boundary_rows.append({"clip": clip["name"], "bounds": actual_bounds,
                                      "action_slot_identifier": strip.action_slot.identifier})
                samples = sample_transforms(clip)
                rig.animation_data.action = action
                rig.animation_data.action_slot = action.slots[0]
                track.mute = True
                direct_errors = []
                for local, sample in enumerate(samples, 1):
                    scene.frame_set(local)
                    bpy.context.view_layer.update()
                    targets = transform_rows(rows, sample)
                    error = max(max_delta(rig.pose.bones[row["name"]].matrix @ right[row["name"]].inverted(),
                                          targets[row["name"]]) for row in rows)
                    direct_errors.append(error)
                    total_bone_comparisons += len(rows)
                direct_rows.append({"clip": clip["name"], "frames": len(samples),
                                    "max_matrix_error": max(direct_errors), "action_slot_identifier": action.slots[0].identifier})
                rig.animation_data.action = None
                track.mute = False
                nla_errors = []
                for offset, sample in enumerate(samples):
                    scene.frame_set(interval["start"] + offset)
                    bpy.context.view_layer.update()
                    targets = transform_rows(rows, sample)
                    error = max(max_delta(rig.pose.bones[row["name"]].matrix @ right[row["name"]].inverted(),
                                          targets[row["name"]]) for row in rows)
                    nla_errors.append(error)
                    total_bone_comparisons += len(rows)
                # Also test the held final sample just before the next clip boundary.
                scene.frame_set(interval["end_inclusive"])
                bpy.context.view_layer.update()
                held_targets = transform_rows(rows, samples[-1])
                held_error = max(max_delta(rig.pose.bones[row["name"]].matrix @ right[row["name"]].inverted(),
                                           held_targets[row["name"]]) for row in rows)
                nla_rows.append({"clip": clip["name"], "frames": len(samples), "max_matrix_error": max(nla_errors),
                                 "held_end_matrix_error": held_error, "action_slot_identifier": action.slots[0].identifier})
            if max([x["max_matrix_error"] for x in direct_rows] + [x["max_matrix_error"] for x in nla_rows]
                   + [x["held_end_matrix_error"] for x in nla_rows]) >= MATRIX_TOL:
                raise AssertionError(f"{label} source matrix error exceeds {MATRIX_TOL}")
            per_rig_checks[label] = {"rig": rig.name, "bone_count": len(rows), "direct_action": direct_rows,
                                     "nla_overview": nla_rows, "nla_boundaries_and_slots": boundary_rows,
                                     "matrix_comparisons": sum(len(sample_transforms(c))*len(rows)*2 for c in clips)}
        if total_bone_comparisons != 49 * (6 + 3 + 9) * 2:
            raise AssertionError(f"Unexpected total source matrix comparisons: {total_bone_comparisons}")
        markers = {clip["name"]: scene.timeline_markers.get(clip["name"]).frame for clip in clips}
        if markers != {clips[0]["name"]: 1, clips[1]["name"]: 16, clips[2]["name"]: 37}:
            raise AssertionError(f"Coordinated timeline markers mismatch: {markers}")
        main_rows = models["main"]["skeleton"]
        body_row = next(row for row in main_rows if row["hash"] == "0x0B11BAB9")
        body_rest = source_targets(main_rows, [], {"kind": "absolute skeletal", "inheritance": "full"})[body_row["name"]]
        body_motion = []
        for clip in clips:
            clip_motion = max(max_delta(source_targets(main_rows, sample,
                {"kind": "absolute skeletal", "inheritance": "full"})[body_row["name"]], body_rest)
                for sample in sample_transforms(clip))
            body_motion.append({"clip": clip["name"], "max_a_body_world_matrix_delta_from_rest": clip_motion})
        main_body_binding = {"joint_hashes_used": influence_rows["main"]["joint_hashes_used"],
                             "only_weighted_joint_is_a_body": influence_rows["main"]["joint_hashes_used"] == ["0x0B11BAB9"],
                             "source_a_body_motion": body_motion}
        # Test native skinned deformation for every source section in all three rig/state bindings.
        skin_rows = []
        for variant in ("v1", "v5"):
            set_variant_visibility(variant)
            vis = scene_visibility_snapshot()
            if any(row["collection_hide_viewport"] or row["collection_hide_render"] for row in vis.values()):
                raise AssertionError(f"A variant collection was disabled during eye/render switching: {vis}")
            for label in ("main", variant):
                for clip in clips:
                    frame = (len(sample_transforms(clip)) + 1) // 2
                    interval = intervals[next(i for i, c in enumerate(clips) if c["name"] == clip["name"])]
                    rig = bpy.data.objects[RIG_NAMES[label]]
                    track = get_overview_track(rig)
                    rig.animation_data.action = None; track.mute = False
                    scene.frame_set(interval["start"] + frame - 1)
                    bpy.context.view_layer.update()
                    skin_rows.append(validate_native_mesh_skin(label, models[label], native[label], rig, clip, frame,
                                                               require_deformation=(label != "main")))
        set_variant_visibility("v1")
        final_vis = scene_visibility_snapshot()
        if not all(not row["hide_set"] and not row["hide_render"] for row in final_vis["v1"]["objects"]):
            raise AssertionError("Saved V1 default visibility was not restored")
        if not all(row["hide_set"] and row["hide_render"] for row in final_vis["v5"]["objects"]):
            raise AssertionError("Saved V5 hidden state was not restored")
        control_text = bpy.data.texts.get("Animation Controls.py")
        catalog_text = bpy.data.texts.get("Pyrociter Animation Catalog.json")
        if not control_text or "def select_clip" not in control_text.as_string() or not catalog_text:
            raise AssertionError("Embedded synchronized animation controls/catalog are missing")
        # Check embedded controls against actual actions and playback mode without registering a UI panel.
        control_namespace = {"__name__": "pyrocitor_animation_controls_validation"}
        exec(control_text.as_string(), control_namespace)
        control_namespace["select_clip"](0)
        if not all(bpy.data.objects[RIG_NAMES[l]].animation_data.action == action_refs[l][clips[0]["name"]]
                   for l in ("main", "v1", "v5")):
            raise AssertionError("Embedded clip selector did not load all three rigs")
        control_namespace["restore_rest"]()
        expected_rests = {"main": "Original source rest pose", "v1": "Original source rest pose.001",
                          "v5": "Original source rest pose.002"}
        if not all(bpy.data.objects[RIG_NAMES[l]].animation_data.action == bpy.data.actions[expected_rests[l]]
                   for l in ("main", "v1", "v5")):
            raise AssertionError("Embedded rest-pose control did not restore all source rigs")
        control_namespace["play_all"]()
        if not all(bpy.data.objects[RIG_NAMES[l]].animation_data.action is None and
                   not get_overview_track(bpy.data.objects[RIG_NAMES[l]]).mute
                   for l in ("main", "v1", "v5")):
            raise AssertionError("Embedded overview control did not restore coordinated NLA")
        result = {"schema": "pyrocitor-switchable-animation-integration-v1", "status": "reopened_switchable_animation_integration_passed",
                  "separate_process_reopen": True, "source_blend_sha256": SOURCE_BLEND_SHA,
                  "proof_scope": "native main, hash-mapped V1 three-joint subset and native V5 nine-joint rig; Fire/Idle/Open; 0x1A61FAF6 excluded",
                  "source_models": {label: {"asset_id": decoded[label]["entry"]["id"], "path": MODEL_PATHS[label],
                                                "packed_sha256": decoded[label]["packed_sha"],
                                                "decoded_sha256": decoded[label]["decoded_sha"],
                                                "joint_count": len(models[label]["skeleton"]),
                                                "joint_hashes": [r["hash"] for r in models[label]["skeleton"]],
                                                "native_influence_inventory": influence_rows[label]}
                                   for label in MODEL_PATHS},
                  "native_shared_bind_checks": shared_checks,
                  "v1_mapping": {"status": "proven_from_native_hash_parent_rest_inverse_bind_geometry_and_weights",
                                 "v1_joint_count": len(models["v1"]["skeleton"]),
                                 "v1_hashes": [row["hash"] for row in models["v1"]["skeleton"]],
                                 "unmatched_channels": [UNMATCHED]},
                  "animset": {"path": ANIMSET_PATH, "asset_id": anim_entry["id"], "packed_sha256": anim_packed_sha,
                              "decoded_sha256": anim_decoded_sha},
                  "clips": [{"name": c["name"], "frames": len(sample_transforms(c)), "fps": FPS,
                             "raw_sha256": c["raw_sha256"], "sample_sha256": c["sample_sha256"],
                             "timeline": {"start": i["start"], "end_inclusive": i["end_inclusive"], "nla_end": i["nla_end"]}}
                            for c, i in zip(clips, intervals)],
                  "per_rig_animation_checks": per_rig_checks,
                  "total_bone_matrix_comparisons": total_bone_comparisons,
                  "maximum_direct_matrix_error": max(x["max_matrix_error"] for v in per_rig_checks.values() for x in v["direct_action"]),
                  "maximum_nla_matrix_error": max(x["max_matrix_error"] for v in per_rig_checks.values() for x in v["nla_overview"]),
                  "native_geometry_weights": mesh_checks,
                  "main_body_binding_and_motion": main_body_binding,
                  "representative_skinned_vertex_checks": skin_rows,
                  "maximum_skinned_vertex_error": max(r["max_evaluated_vertex_error"] for r in skin_rows),
                  "variant_visibility_checks": {"default_saved_state": final_vis,
                                                 "states_evaluated": ["v1", "v5"],
                                                 "switching": "ordinary hide_set eye state plus object hide_render; collections remain enabled"},
                  "embedded_controls": {"text": "Animation Controls.py", "catalog": "Pyrociter Animation Catalog.json",
                                        "synchronized_rigs": [RIG_NAMES[x] for x in ("main", "v1", "v5")],
                                        "selection_and_overview_passed": True},
                  "preservation": {"snapshot_sha256": preserve_hash, "reopened_camera_world_material_image_source_metadata_exact": True,
                                   "camera_world_material_image_counts": {"materials": len(actual_preserve["materials"]),
                                                                         "images": len(actual_preserve["images"]),
                                                                         "world_nodes": len(actual_preserve["world"]["nodes"]) if actual_preserve["world"] else 0},
                                   "shared_render_defaults": audit_scene(scene)},
                  "unmatched_channels": [{"hash": UNMATCHED, "status": "unsupported; excluded from all rig Actions"}],
                  "full_ten_channel_runtime_equivalence": "not claimed",
                  "proof_blend": {"path": str(BLEND), "size_bytes": BLEND.stat().st_size, "sha256": sha(BLEND.read_bytes())},
                  "blender_version": bpy.app.version_string}
        if JOB_MODE:
            result["job"] = {**JOB_CONFIG, "build_pid": embedded["build_pid"], "validate_pid": os.getpid(),
                             "separate_process_reopen": True}
        return result


def main():
    parser = argparse.ArgumentParser(description="Run by run.py: add the Pyrocitor source clips to a static V1/V5 assembly.")
    parser.add_argument("--job", type=Path, required=True, help="job JSON written by run.py")
    parser.add_argument("--mode", choices=("build", "validate"), required=True)
    cli_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(cli_args)
    _read_job(args.job)
    if args.mode == "build":
        result = build()
    else:
        _verify_source_hash()
        bpy.ops.wm.open_mainfile(filepath=str(BLEND))
        result = validate()
        JOB_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        temp = JOB_REPORT_JSON.with_suffix(JOB_REPORT_JSON.suffix + ".tmp")
        temp.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, JOB_REPORT_JSON)
    print("PYROCITOR_INTEGRATION_STATUS", result["status"], "BLEND", BLEND, flush=True)


if __name__ == "__main__":
    main()
