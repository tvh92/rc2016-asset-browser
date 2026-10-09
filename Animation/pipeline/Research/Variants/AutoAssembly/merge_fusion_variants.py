"""Merge two independently source-built Fusion Grenade native model scenes.

The merge keeps each pipeline-built rig/material/mesh at its existing world
matrix. It does not derive a cross-version attachment transform.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
import bpy
from bpy_extras.object_utils import world_to_camera_view
from Animation.pipeline.builder import validate_reopened_native_descriptor
from Animation.pipeline.common import dump, load, sha, file_hash
from Animation.pipeline.Research.Variants.AutoAssembly.fusion_variant_contract import assert_visible_source_sections
from blender_defaults import audit_scene, _configure_optix_gpu


def move_to_collection(obj, collection):
    if collection not in obj.users_collection:
        collection.objects.link(obj)
    for old in list(obj.users_collection):
        if old != collection:
            old.objects.unlink(obj)


def hide_variant(collection, visible):
    collection.hide_render = False
    for obj in collection.objects:
        # Source rigs remain enabled for deformation, with their ordinary eye
        # hidden just as in the independently validated model scenes.
        hidden = obj.type == 'ARMATURE' or not visible
        obj.hide_set(hidden)
        obj.hide_render = hidden


def set_variant_visibility(collections, variant):
    if variant not in {"V1", "V5"}:
        raise ValueError(f"Unknown Fusion visibility state: {variant}")
    for name in ("FUSION_SHARED_WEAPON", "FUSION_SHARED_HAND"):
        collection = collections[name]
        collection.hide_render = False
        for obj in collection.objects:
            hidden = obj.type == 'ARMATURE'
            obj.hide_set(hidden)
            obj.hide_render = hidden
    hide_variant(collections["FUSION_GRENADE_V5"], variant == "V5")


def visible_mesh_objects(collections):
    candidates = set()
    for name in ("FUSION_SHARED_WEAPON", "FUSION_SHARED_HAND", "FUSION_GRENADE_V5"):
        candidates.update(o for o in collections[name].objects if o.type == "MESH")
    return [o for o in candidates if not o.hide_get() and not o.hide_render]


def visible_source_sections(collections, job):
    actual = {"v1": set(), "v5": set()}
    source_sha = {"v1": job["v1_descriptor"]["sha256"],
                  "v5": job["v5_descriptor"]["sha256"]}
    for obj in visible_mesh_objects(collections):
        digest = obj.get("source_model_sha256")
        matches = [key for key, expected in source_sha.items() if digest == expected]
        if len(matches) != 1:
            raise AssertionError(f"Visible mesh has unknown/ambiguous source model hash: {obj.name}")
        actual[matches[0]].add(int(obj.get("source_subset_index", -1)))
    return actual


def visible_geometry_manifest(collections, job):
    descriptors = {"v1": job["v1_descriptor"], "v5": job["v5_descriptor"]}
    result = {}
    for key, descriptor in descriptors.items():
        meshes = [obj for obj in visible_mesh_objects(collections)
                  if obj.get("source_model_sha256") == descriptor["sha256"]]
        sections = [{"section": int(obj.get("source_subset_index", -1)),
                     "vertices": len(obj.data.vertices),
                     "triangles": len(obj.data.polygons),
                     "source_material_index": int(obj.get("source_material_index", -1))}
                    for obj in sorted(meshes, key=lambda item: int(item.get("source_subset_index", -1)))]
        result[key] = {"asset_id": descriptor["asset_id"], "source_sha256": descriptor["sha256"],
                       "sections": sections,
                       "visible_vertex_total": sum(row["vertices"] for row in sections),
                       "visible_triangle_total": sum(row["triangles"] for row in sections)}
    return result


def camera_snapshot(camera):
    return {"name": camera.name, "matrix_world": [[float(camera.matrix_world[i][j]) for j in range(4)] for i in range(4)],
            "type": camera.data.type, "lens": float(camera.data.lens), "shift": [camera.data.shift_x, camera.data.shift_y]}


def build(job):
    v1_blend, v5_blend = Path(job["v1_blend"]), Path(job["v5_blend"])
    v1_desc, v5_desc = job["v1_descriptor"], job["v5_descriptor"]
    if file_hash(v1_blend) != job["v1_blend_sha256"] or file_hash(v5_blend) != job["v5_blend_sha256"]:
        raise ValueError("Pipeline-built source scene hash changed before merge")
    bpy.ops.wm.open_mainfile(filepath=str(v1_blend))
    scene = bpy.context.scene
    if not scene.camera or not scene.world:
        raise ValueError("V1 source-built scene is missing camera or world")
    v1_objects = [o for o in scene.objects if o.type == "MESH" and o.get("source_model_sha256") == v1_desc["sha256"]]
    if not v1_objects:
        raise ValueError("V1 source model geometry missing from pipeline-built scene")
    v1_by_section = {int(o.get("source_subset_index", -1)): o for o in v1_objects}
    if set(v1_by_section) != {0, 1} or len(v1_by_section) != len(v1_objects):
        raise ValueError(f"Expected exactly V1 source sections 0/1; found {sorted(v1_by_section)}")
    shared_weapon = bpy.data.collections.new("FUSION_SHARED_WEAPON")
    shared_hand = bpy.data.collections.new("FUSION_SHARED_HAND")
    scene.collection.children.link(shared_weapon)
    scene.collection.children.link(shared_hand)
    v1_rigs = {m.object for o in v1_objects for m in o.modifiers if m.type == "ARMATURE" and m.object}
    for obj in v1_rigs:
        move_to_collection(obj, shared_weapon)
    move_to_collection(v1_by_section[0], shared_weapon)
    move_to_collection(v1_by_section[1], shared_hand)
    for obj in v1_objects:
        obj["source_config_id"] = job["config"]["asset_id"]
        obj["source_config_sha256"] = job["config"]["sha256"]
        obj["source_variant_field"] = "GloveModelOverride"

    with bpy.data.libraries.load(str(v5_blend), link=False) as (data_from, data_to):
        data_to.objects = list(data_from.objects)
    imported = [o for o in data_to.objects if o]
    v5_objects = [o for o in imported if o.type == "MESH" and o.get("source_model_sha256") == v5_desc["sha256"]]
    if not v5_objects:
        raise ValueError("V5 source model geometry missing from pipeline-built scene library")
    v5_rigs = {m.object for o in v5_objects for m in o.modifiers if m.type == "ARMATURE" and m.object}
    if any(rig not in imported for rig in v5_rigs):
        raise ValueError("V5 mesh armature dependency was not loaded with source scene objects")
    v5_collection = bpy.data.collections.new("FUSION_GRENADE_V5")
    scene.collection.children.link(v5_collection)
    for obj in [*v5_objects, *v5_rigs]:
        move_to_collection(obj, v5_collection)
    for obj in v5_objects:
        obj["source_config_id"] = job["config"]["asset_id"]
        obj["source_config_sha256"] = job["config"]["sha256"]
        obj["source_variant_field"] = "GloveUpgradeModel"
    set_variant_visibility({"FUSION_SHARED_WEAPON": shared_weapon,
                            "FUSION_SHARED_HAND": shared_hand,
                            "FUSION_GRENADE_V5": v5_collection}, "V1")
    scene["fusion_variant_default"] = "V1"
    scene["fusion_config_id"] = job["config"]["asset_id"]
    scene["fusion_config_sha256"] = job["config"]["sha256"]
    scene["fusion_v1_model_sha256"] = v1_desc["sha256"]
    scene["fusion_v5_model_sha256"] = v5_desc["sha256"]
    scene["fusion_variant_control"] = "Keep FUSION_SHARED_WEAPON and FUSION_SHARED_HAND visible in both states; toggle only FUSION_GRENADE_V5 for the V5 upgrade. Collections remain enabled."
    scene["fusion_assembly_boundary"] = "Source config proves model version links only. Actor selection and cross-version attachment transform are unproven. Each model stays at its independently pipeline-presented source bind-space matrix."
    candidate = Path(job["candidate"])
    candidate.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(candidate), compress=True)
    print(json.dumps({"status": "built", "candidate": str(candidate), "shared_weapon_objects": [o.name for o in shared_weapon.objects],
                      "shared_hand_objects": [o.name for o in shared_hand.objects],
                      "v5_objects": [o.name for o in v5_objects]}, indent=2), flush=True)


def validate(job):
    _configure_optix_gpu(bpy)
    candidate = Path(job["candidate"])
    bpy.ops.wm.open_mainfile(filepath=str(candidate))
    _configure_optix_gpu(bpy)
    scene = bpy.context.scene
    assert scene.camera and scene.world
    assert scene["fusion_config_id"] == job["config"]["asset_id"]
    assert scene["fusion_config_sha256"] == job["config"]["sha256"]
    camera_before = camera_snapshot(scene.camera)
    assert scene.camera.data.type == "PERSP"
    collections = {name: bpy.data.collections.get(name) for name in ("FUSION_SHARED_WEAPON", "FUSION_SHARED_HAND", "FUSION_GRENADE_V5")}
    assert all(collections.values())
    assert all(not collection.hide_render and not collection.hide_viewport for collection in collections.values())
    def assert_enabled(layer):
        if layer.collection.name in collections:
            assert not layer.exclude and not layer.hide_viewport, 'Variant collection is disabled'
        for child in layer.children:
            assert_enabled(child)
    assert_enabled(bpy.context.view_layer.layer_collection)
    shared_objects = set(collections["FUSION_SHARED_WEAPON"].objects) | set(collections["FUSION_SHARED_HAND"].objects)
    assert shared_objects
    assert all(not obj.hide_get() and not obj.hide_render for obj in shared_objects if obj.type == 'MESH'), "Shared V1 sections are hidden in saved default"
    assert all(obj.hide_get() and obj.hide_render and not obj.hide_viewport
               for obj in shared_objects if obj.type == 'ARMATURE'), "Shared source rigs must remain enabled and eye-hidden"
    assert all(obj.hide_get() and obj.hide_render for obj in collections["FUSION_GRENADE_V5"].objects), "Saved default does not hide V5"
    source_rows = []
    shared_meshes = [o for o in shared_objects if o.type == "MESH" and o.get("source_model_sha256") == job["v1_descriptor"]["sha256"]]
    v5_meshes = [o for o in collections["FUSION_GRENADE_V5"].objects if o.type == "MESH" and o.get("source_model_sha256") == job["v5_descriptor"]["sha256"]]
    assert {int(o.get("source_subset_index", -1)) for o in shared_meshes} == {0, 1}
    assert {int(o.get("source_subset_index", -1)) for o in v5_meshes} == {0, 1, 2}
    for label, descriptor, meshes in (("V1_SHARED", job["v1_descriptor"], shared_meshes),
                                      ("V5_UPGRADE", job["v5_descriptor"], v5_meshes)):
        assert meshes, f"{label} lacks its source meshes"
        proof = validate_reopened_native_descriptor(descriptor, meshes, descriptor.get("joint_names"))
        material_bindings = {}
        for obj in meshes:
            used_local = sorted({int(poly.material_index) for poly in obj.data.polygons})
            bindings = []
            for local_slot in used_local:
                material = obj.data.materials[local_slot]
                bindings.append({"local_slot": local_slot,
                    "source_material_index": int(obj.get("source_material_index", -1)),
                    "source_material_path": material.get("source_material_path") if material else None,
                    "source_material_sha256": material.get("source_material_sha256") if material else None})
            material_bindings[obj.name] = bindings
        source_rows.append({"role": label, "model_id": descriptor["asset_id"], "model_path": descriptor["archive_path"],
            "model_sha256": descriptor["sha256"], "subset_proofs": proof,
            "mesh_objects": [o.name for o in meshes],
            "source_sections_visible": sorted(int(o.get("source_subset_index", -1)) for o in meshes),
            "used_polygon_slots": {o.name: sorted({int(p.material_index) for p in o.data.polygons}) for o in meshes},
            "used_material_bindings": material_bindings,
            "rigs": sorted({m.object.name for o in meshes for m in o.modifiers if m.type == "ARMATURE" and m.object})})
        for obj in meshes:
            expected_variant = descriptor["field"]
            assert obj.get("source_config_id") == job["config"]["asset_id"]
            assert obj.get("source_variant_field") == expected_variant
            for actual, expected in zip(obj.matrix_world, descriptor["presentation_matrix"]):
                assert max(abs(float(a)-float(b)) for a,b in zip(actual, expected)) < 1e-7, "Source presentation matrix changed"
    source_images = [image for image in bpy.data.images if image.get("source_texture_path")]
    assert source_images and all(image.packed_file and image.size[0] > 0 and image.size[1] > 0 for image in source_images)
    materials = [material for material in bpy.data.materials if material.get("source_material_path")]
    assert materials
    for material in materials:
        output = material.node_tree.nodes.get("Material Output") if material.node_tree else None
        assert output and output.inputs["Surface"].is_linked
    # Confirm both alternative geometries fit the preserved V1 camera view.
    bpy.context.view_layer.update()
    framing = {}
    for label in ("V1", "V5"):
        set_variant_visibility(collections, label)
        visible = visible_source_sections(collections, job)
        assert_visible_source_sections(label, visible)
        visible_objects = visible_mesh_objects(collections)
        coords = [world_to_camera_view(scene, scene.camera, obj.matrix_world @ vertex.co)
                  for obj in visible_objects for vertex in obj.data.vertices]
        assert coords
        bounds = {axis: [min(getattr(c, axis) for c in coords), max(getattr(c, axis) for c in coords)] for axis in ("x", "y")}
        assert min(c.z for c in coords) > 0, f"{label} geometry is behind the preserved camera"
        assert bounds["x"][0] >= -0.02 and bounds["x"][1] <= 1.02 and bounds["y"][0] >= -0.02 and bounds["y"][1] <= 1.02, (label, bounds)
        framing[label] = bounds
    assert not job["v1_job"].get("clips") and not job["v5_job"].get("clips"), "Unexpected clips; animation provenance needs review"
    # Cheap state-specific proofs; the saved final state remains V1 with normal settings.
    renders = []
    if job.get("render_proofs", True):
        old_percentage, old_samples, old_path = scene.render.resolution_percentage, scene.cycles.samples, scene.render.filepath
        scene.render.resolution_percentage, scene.cycles.samples = 25, 32
        review = Path(job["review"]); review.mkdir(parents=True, exist_ok=True)
        for label in ("V1", "V5"):
            set_variant_visibility(collections, label)
            scene.render.filepath = str(review / f"fusion_grenade_{label.lower()}_proof.png")
            bpy.ops.render.render(write_still=True)
            renders.append({"variant": label, "path": scene.render.filepath, "sha256": file_hash(scene.render.filepath)})
        scene.render.resolution_percentage, scene.cycles.samples, scene.render.filepath = old_percentage, old_samples, old_path
    elif Path(job["validation"]).is_file():
        # Receipt migration retains previous visual evidence while repeating
        # source/reopen validation without rendering or saving the blend.
        try:
            renders = load(job["validation"]).get("proof_renders", [])
        except Exception:
            renders = []
    visible_sections = {}
    visible_geometry = {}
    for label in ("V1", "V5"):
        set_variant_visibility(collections, label)
        actual = visible_source_sections(collections, job)
        assert_visible_source_sections(label, actual)
        visible_sections[label] = actual
        visible_geometry[label] = visible_geometry_manifest(collections, job)
    set_variant_visibility(collections, "V1")
    assert all(not obj.hide_get() and not obj.hide_render for obj in shared_objects if obj.type == 'MESH')
    assert all(obj.hide_get() and obj.hide_render and not obj.hide_viewport
               for obj in shared_objects if obj.type == 'ARMATURE')
    assert not audit_scene(scene)["mismatches"]
    report = {"status": "passed", "blend_sha256": file_hash(candidate), "config": job["config"],
        "models": source_rows, "camera": camera_before, "camera_preserved_from": job["v1_blend"],
        "world": scene.world.name, "source_images_packed": len(source_images), "source_materials": len(materials),
        "saved_default": "V1", "visibility_states_validated": ["V1", "V5"],
        "visible_source_sections_by_state": {k: {name: sorted(v) for name, v in value.items()} for k, value in visible_sections.items()},
        "visible_geometry_by_state": visible_geometry,
        "shared_collections": {name: sorted(o.name for o in collections[name].objects) for name in ("FUSION_SHARED_WEAPON", "FUSION_SHARED_HAND")},
        "collections_enabled": True, "frame_bounds": framing, "proof_renders": renders,
        "source_animation_status": "No source animset is referenced by either selected wearable model; no applicable clips were included.",
        "limitations": ["Weapon config model links are source-exact; actor selection is unproven.",
            "No V1-to-V5 attachment transform is decoded; each model retains its pipeline-produced source bind-space matrix.",
            "Blender material graph appearance is not evidence of game shader equivalence."]}
    dump(job["validation"], report)
    print(json.dumps({"status": "validated", "report": job["validation"]}, indent=2), flush=True)


parser = argparse.ArgumentParser()
parser.add_argument("--job", required=True)
parser.add_argument("--validate", action="store_true")
args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else [])
job = load(args.job)
validate(job) if args.validate else build(job)
