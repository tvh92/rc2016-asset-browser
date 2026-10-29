"""Assemble several delivered models into one .blend framed by one camera.

Run by ``run.py --group-blend NAME`` inside Blender (``blender --background --python group_blend.py -- --job JOB``;
``--validate`` reopens the candidate).  Each member is a finished per-model .blend: its meshes and armature are
appended at the position they have in that .blend, so a part built as part of a larger model (a glove of a suit)
overlaps the model it belongs to exactly.  Each member keeps the animation it was built with (its NLA strips and their
Actions), so the timeline plays every member's clips; ``bind_pose`` on a member clears it instead.  The scene then
uses the shared review lighting, one camera dollied to fit the whole group and one Render Region around it, exactly
as a single-model build does.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import bpy  # type: ignore
import numpy as np  # type: ignore
from mathutils import Euler, Matrix, Vector  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rc2016.common import file_hash  # noqa: E402
from rc2016.pipeline.scene_defaults import (  # noqa: E402
    CAMERA_EULER_DEGREES, OUTPUT_HEIGHT, OUTPUT_WIDTH, apply_new_scene_defaults, check_shared_lighting,
    fit_render_region, fit_render_size, frame_camera_zoom, visible_mesh_points)

MEMBER_KEY = "group_member"


def _embed(name, payload):
    text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    text.clear()
    text.write(json.dumps(payload, indent=2, sort_keys=True))


def _world_corners(objects):
    return [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]


def _extent(points, axis):
    values = [float(point.dot(axis)) for point in points]
    return min(values), max(values)


def _base_name(name):
    """Blender appends ``.001`` to a datablock whose name is taken; the source name is the part before it."""
    return re.sub(r"\.\d{3}$", "", name)


def _load_materials(path, names):
    """Append the named materials from another member's .blend; returns {source name: material}."""
    before = set(bpy.data.materials)
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        target.materials = [name for name in names if name in source.materials]
    loaded = {}
    for material in bpy.data.materials:
        if material not in before and _base_name(material.name) in names:
            loaded[_base_name(material.name)] = material
    return loaded


def _mesh_key(obj):
    """The part name of a mesh object: the last ``|`` field of ``<model> | subset NNN | <part>``, without ``.001``."""
    return _base_name(obj.name).split("|")[-1].strip()


def _load_member(member, label):
    """Append one member's meshes and armatures into a collection named after it; returns all of them.

    The member's armatures keep the action and NLA strips they were built with unless ``member["bind_pose"]`` is set,
    which clears them to the rest pose. ``member["materials"]`` (``blend``,
    ``names``) replaces the named material slots with the same-named materials of another member's .blend, which
    is how a texture set (captain, chrome, flame) is put on a suit without another mesh. ``member["exclude"]`` lists
    part names (see ``_mesh_key``) that are left out of the group. ``member["hide_armature"]`` hides the member's
    armatures in the view and in renders while keeping their pose, so the animation still drives the meshes."""
    path = Path(member["blend"])
    if file_hash(path) != member["sha256"]:
        raise RuntimeError("Member changed since it was delivered: %s" % path)
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        target.objects = list(source.objects)
    collection = bpy.data.collections.new(label)
    bpy.context.scene.collection.children.link(collection)
    excluded = set(member.get("exclude", []))
    kept = []
    for obj in target.objects:
        if obj is None:
            continue
        if obj.type not in ("MESH", "ARMATURE"):  # cameras, lights and diagnostic empties are not part of the group
            bpy.data.objects.remove(obj, do_unlink=True)
            continue
        if obj.type == "MESH" and _mesh_key(obj) in excluded:
            bpy.data.objects.remove(obj, do_unlink=True)
            continue
        if obj.type == "ARMATURE" and member.get("bind_pose"):
            # Bind pose: no action, no NLA strips, and every pose bone back at its rest transform.
            obj.animation_data_clear()
            for bone in obj.pose.bones:
                bone.location = (0.0, 0.0, 0.0)
                bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                bone.rotation_euler = (0.0, 0.0, 0.0)
                bone.scale = (1.0, 1.0, 1.0)
        obj[MEMBER_KEY] = label
        collection.objects.link(obj)
        if obj.type == "ARMATURE" and member.get("hide_armature"):
            obj.hide_set(True)
            obj.hide_render = True
        kept.append(obj)
    swaps = member.get("materials") or []
    if isinstance(swaps, dict):  # one source file; a list takes several (body parts and helmet from different files)
        swaps = [swaps]
    for swap in swaps:
        replacements = _load_materials(swap["blend"], swap["names"])
        missing = sorted(set(swap["names"]) - set(replacements))
        if missing:
            raise RuntimeError("Texture set %s lacks materials: %s" % (swap["blend"], ", ".join(missing)))
        for obj in kept:
            if obj.type != "MESH":
                continue
            for slot in obj.material_slots:
                if slot.material and _base_name(slot.material.name) in replacements:
                    slot.material = replacements[_base_name(slot.material.name)]
    return kept


def _deforming_rig(objects):
    """(armature, mesh) of the first mesh in ``objects`` that an Armature modifier deforms, else (None, None)."""
    for obj in objects:
        if obj.type != "MESH":
            continue
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is not None:
                return modifier.object, obj
    return None, None


def _bind_to_owner_rigs(defs, loaded):
    """Skin every ``rig_of`` member (an attach plate) to the rig of the member it names; returns {index: owner index}.

    A plate's own .blend carries a small skeleton with its root moved to the origin, so it cannot follow its body's
    pose. Its vertices are in the body's bind space, so pointing its Armature modifiers at the body's rig (the vertex
    groups are the body's joint names) puts it on the body and moves it with it. The plate's own armatures are
    removed from the group. A plate whose joints, rig transform or mesh transform do not match the body's is kept
    on its own rig and named in the log."""
    index_of = {member["name"]: index for index, member in enumerate(defs)}
    bound = {}
    for index, member in enumerate(defs):
        owner = index_of.get(member.get("rig_of"))
        if owner is None or owner == index:
            continue
        rig, body_mesh = _deforming_rig(loaded[owner])
        meshes = [obj for obj in loaded[index] if obj.type == "MESH"]
        own_rigs = [obj for obj in loaded[index] if obj.type == "ARMATURE"]
        if rig is None or not meshes:
            continue
        joints = {bone.name for bone in rig.data.bones}
        unknown = sorted({group.name for obj in meshes for group in obj.vertex_groups} - joints)
        moved = any(obj.matrix_basis != rig.matrix_basis for obj in own_rigs) or any(
            obj.matrix_basis != body_mesh.matrix_basis for obj in meshes)
        if unknown or moved:
            print("GROUP plate kept on its own rig", member["name"], "unknown joints:", unknown[:5], "moved:", moved,
                  flush=True)
            continue
        for obj in meshes:
            for modifier in obj.modifiers:
                if modifier.type == "ARMATURE":
                    modifier.object = rig
        for obj in own_rigs:
            loaded[index].remove(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
        bound[index] = owner
        print("GROUP plate bound to", defs[owner]["name"], member["name"], flush=True)
    return bound


def _layout(members, right):
    """Record each member's visible extent along ``right`` without moving it.

    Members keep the position they have in their own .blend: every per-model build shares one source frame, so
    a glove exported as part of its suit already sits on the suit. Moving members would break that overlap."""
    bpy.context.view_layer.update()
    layout = []
    for objects in members:
        meshes = [obj for obj in objects if obj.type == "MESH" and not obj.hide_render]
        low, high = _extent(_world_corners(meshes), right)
        layout.append(dict(low=float(low), high=float(high)))
    return layout


def _timeline(scene, members):
    """Span the scene's frame range over every kept NLA strip, so the timeline plays the members' clips; a group
    with no animation keeps the default range. Frame 1 of that range is the frame the camera is fitted to."""
    starts, ends = [], []
    for objects in members:
        for obj in objects:
            if obj.type != "ARMATURE" or obj.animation_data is None:
                continue
            for track in obj.animation_data.nla_tracks:
                for strip in track.strips:
                    starts.append(strip.frame_start)
                    ends.append(strip.frame_end)
    if starts:
        scene.frame_start = int(math.floor(min(starts)))
        scene.frame_end = int(math.ceil(max(ends)))
        scene.frame_set(scene.frame_start)


def assemble(job):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    apply_new_scene_defaults(scene, job.get("hdri"), configure_world=True, lighting=job.get("shared_lighting"))
    right = Euler(tuple(math.radians(v) for v in CAMERA_EULER_DEGREES)).to_matrix() @ Vector((1.0, 0.0, 0.0))
    loaded = [_load_member(member, member["name"]) for member in job["members"]]
    owners = _bind_to_owner_rigs(job["members"], loaded)
    members = []
    for index, objects in enumerate(loaded):
        member = job["members"][owners.get(index, index)]  # a part bound to its body's rig is placed with the body
        # An offset moves a whole member sideways, along the camera's right axis, so repeated suits sit side by side.
        offset = float(member.get("offset", 0.0))
        if offset:
            for obj in objects:
                if obj.parent is None:
                    obj.location = obj.location + right * offset
        # ``native_offset`` (game Y-up units, see projectile_mounts.py) moves a member to where it rests on the weapon
        # it is grouped with; the presentation basis maps native (x, y, z) to Blender (x, -z, y).
        native = member.get("native_offset")
        if native:
            shift = Vector((native[0], -native[2], native[1]))
            for obj in objects:
                if obj.parent is None:
                    obj.location = obj.location + shift
        # ``x`` places a member on the world X axis (its own origin, so equal spacing gives equal gaps), and
        # ``rotate_z_degrees`` turns it about the world Z axis through that origin, before the placement.
        turn = float(member.get("rotate_z_degrees", 0.0))
        if turn or "x" in member:
            place = Matrix.Translation((float(member.get("x", 0.0)), 0.0, 0.0)) @ Matrix.Rotation(
                math.radians(turn), 4, "Z")
            for obj in objects:
                if obj.parent is None:
                    # matrix_basis, not matrix_world: appended objects have no evaluated world matrix yet.
                    obj.matrix_basis = place @ obj.matrix_basis
        members.append(objects)
        print("GROUP member", index + 1, len(job["members"]), job["members"][index]["name"], flush=True)
    _timeline(scene, members)
    layout = _layout(members, right)
    camera_data = bpy.data.cameras.new("Group review camera")
    camera_data.type = "PERSP"
    camera_data.lens = 50
    camera = bpy.data.objects.new("Group review camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    points = visible_mesh_points(scene)
    if not len(points):
        raise RuntimeError("The group has no visible mesh to frame")
    low, high = points.min(axis=0), points.max(axis=0)
    radius = max(float(np.linalg.norm(high - low)) / 2, 0.05)
    camera_data.clip_start = max(0.0001, radius / 10000)
    camera_data.clip_end = radius * 100
    camera_receipt = frame_camera_zoom(scene, points)
    region_receipt = fit_render_region(scene)
    region_receipt["render_size"] = fit_render_size(scene)
    _embed("Camera zoom.json", camera_receipt)
    _embed("Render region.json", region_receipt)
    _embed("Group members.json", dict(members=[dict(name=m["name"], sha256=m["sha256"]) for m in job["members"]],
                                      layout=layout, placement="original", camera_right_axis=list(right)))
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
    bpy.ops.wm.save_as_mainfile(filepath=job["candidate"], compress=True)
    print("GROUP built", job["candidate"], flush=True)


def validate(job):
    bpy.ops.wm.open_mainfile(filepath=job["candidate"])
    scene = bpy.context.scene
    problems = []
    if scene.camera is None or scene.camera.type != "CAMERA":
        problems.append("camera")
    render = scene.render
    if (render.resolution_x, render.resolution_y, render.resolution_percentage) != (OUTPUT_WIDTH, OUTPUT_HEIGHT, 100):
        problems.append("render size")
    for member in job["members"]:
        meshes = [obj for obj in scene.objects if obj.type == "MESH" and obj.get(MEMBER_KEY) == member["name"]]
        if not meshes:
            problems.append("member missing: " + member["name"])
    problems.extend("lighting." + item for item in check_shared_lighting(scene))
    if problems:
        raise RuntimeError("Group validation failed: " + ", ".join(problems))
    print("GROUP validation passed:", len(job["members"]), "members", flush=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args(argv)
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    if args.validate:
        validate(job)
    else:
        assemble(job)


if __name__ == "__main__":
    main()
