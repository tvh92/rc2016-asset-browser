"""Assemble several delivered models into one .blend framed by one camera.

Run by ``run.py --group-blend NAME`` inside Blender (``blender --background --python group_blend.py -- --job JOB``;
``--validate`` reopens the candidate).  Each member is a finished per-model .blend: its meshes and armature are
appended at the position they have in that .blend, so a part built as part of a larger model (a glove of a suit)
overlaps the model it belongs to exactly.  Each member keeps the animation it was built with (its NLA strips and their
Actions), so the timeline plays every member's clips; ``bind_pose`` on a member clears it instead.  A member with a
``native_matrix`` (``run.py --group-actors``) is placed by that zone matrix, and the same .blend may then appear in
several members.  The scene then uses the shared review lighting, one camera dollied to fit the whole group and one
Render Region around it, exactly as a single-model build does.
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
# Native Y-up game frame to the presentation basis: (x, y, z) -> (x, -z, y).
PRESENTATION = Matrix(((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, -1.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)))


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


def _native_placement(rows):
    """Presentation-space matrix for a native row-vector placement ``rows`` (``p_world = p_local @ M``)."""
    column = Matrix([list(row) for row in rows]).transposed()
    return PRESENTATION @ column @ PRESENTATION.inverted()


def _bake_track(obj, base, matrices):
    """Key ``obj`` at every native row-vector matrix of ``matrices`` (frame 1 onward) on top of its ``base`` matrix,
    with linear interpolation, in a new action of its own."""
    obj.rotation_mode = "QUATERNION"
    values = np.empty((len(matrices), 10), dtype=np.float32)
    previous = None
    for frame, rows in enumerate(matrices):
        location, rotation, scale = (_native_placement(rows) @ base).decompose()
        if previous is not None and rotation.dot(previous) < 0:
            rotation.negate()   # keep quaternion signs continuous so the interpolation takes the short way
        previous = rotation
        values[frame] = [*location, *rotation, *scale]
    action = bpy.data.actions.new(obj.name + " flight path")
    slot = action.slots.new(id_type="OBJECT", name=obj.name)
    bag = action.layers.new("Flight path").strips.new(type="KEYFRAME").channelbags.new(slot)
    frames = np.arange(1, len(matrices) + 1, dtype=np.float32)
    for path, offset, count in (("location", 0, 3), ("rotation_quaternion", 3, 4), ("scale", 7, 3)):
        for index in range(count):
            curve = bag.fcurves.new(data_path=path, index=index)
            curve.keyframe_points.add(len(matrices))
            curve.keyframe_points.foreach_set("co", np.column_stack((frames, values[:, offset + index])).ravel())
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"
            curve.update()
    obj.animation_data_create()
    obj.animation_data.action = action
    obj.animation_data.action_slot = slot


def _path_curve(scene, path):
    """The flight path as a poly curve (``path["preview"]``, native points), shown in the viewport, not rendered."""
    data = bpy.data.curves.new("Flight path", type="CURVE")
    data.dimensions = "3D"
    spline = data.splines.new("POLY")
    points = path["preview"]
    spline.points.add(len(points) - 1)
    for point, (x, y, z) in zip(spline.points, points):
        point.co = (x, -z, y, 1.0)
    spline.use_cyclic_u = bool(path.get("closed"))
    curve = bpy.data.objects.new("Flight path", data)
    curve.hide_render = True
    curve["flight_path"] = json.dumps({k: v for k, v in path.items() if k != "preview"})
    scene.collection.objects.link(curve)


def _copy_member(objects, label):
    """Copies of an already loaded member's objects in a new collection, sharing their mesh, armature and material
    data, with parents and Armature modifiers pointed at the copies."""
    collection = bpy.data.collections.new(label)
    bpy.context.scene.collection.children.link(collection)
    copies = {obj: obj.copy() for obj in objects}
    for copy in copies.values():
        if copy.parent in copies:
            copy.parent = copies[copy.parent]
        for modifier in copy.modifiers:
            if modifier.type == "ARMATURE" and modifier.object in copies:
                modifier.object = copies[modifier.object]
        copy[MEMBER_KEY] = label
        collection.objects.link(copy)
    return list(copies.values())


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


def _root_shift(plate_rig, body_rig, tolerance=1e-3):
    """Where the plate's root joint sits on the body: the one translation from each of the plate's joints to the
    body's joint of the same name, or None when the joints disagree (the plate is then not the body's skeleton cut
    down to a root at the origin)."""
    offsets = [body_rig.data.bones[bone.name].head_local - bone.head_local for bone in plate_rig.data.bones
               if bone.name in body_rig.data.bones and not bone.name.startswith("__")]
    if not offsets or any((offset - offsets[0]).length > tolerance for offset in offsets):
        return None
    return offsets[0].copy()


def _bind_to_owner_rigs(defs, loaded):
    """Skin every ``rig_of`` member (an attach plate) to the rig of the member it names; returns {index: owner index}.

    A plate's own .blend carries a small skeleton with its root moved to the origin, so it cannot follow its body's
    pose, and its vertices are relative to that root. Moving them by the offset from the plate's joints to the body's
    puts them in the body's bind space; pointing its Armature modifiers at the body's rig (the vertex groups are the
    body's joint names) then moves it with the body. The plate's own armatures are removed from the group. A plate
    whose joints, rig transform or mesh transform do not match the body's is kept on its own rig and named in the
    log."""
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
        own_rig, _mesh = _deforming_rig(loaded[index])
        moved = own_rig is None or any(obj.matrix_basis != rig.matrix_basis for obj in own_rigs) or any(
            obj.matrix_basis != body_mesh.matrix_basis or obj.matrix_basis != own_rig.matrix_basis for obj in meshes)
        shift = None if unknown or moved else _root_shift(own_rig, rig)
        if shift is None:
            print("GROUP plate kept on its own rig", member["name"], "unknown joints:", unknown[:5], "moved:", moved,
                  flush=True)
            continue
        for obj in meshes:  # the plate's vertices are relative to its own root joint
            obj.data.transform(Matrix.Translation(shift))
            obj.data.update()
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
    loaded, first = [], {}
    for member in job["members"]:
        # A placed member repeats a model (five tanks): later copies share the first one's data instead of
        # appending the file again, which would duplicate every packed texture.
        options = json.dumps({k: v for k, v in member.items() if k not in (
            "name", "native_matrix", "placement", "native_track", "flight_path")}, sort_keys=True)
        if "native_matrix" in member and options in first:
            loaded.append(_copy_member(first[options], member["name"]))
        else:
            loaded.append(_load_member(member, member["name"]))
            first.setdefault(options, loaded[-1])
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
        rows = member.get("native_matrix")
        if rows:
            place = _native_placement(rows)
            track = member.get("native_track")
            for obj in objects:
                if obj.parent is None:
                    if track:
                        _bake_track(obj, obj.matrix_basis.copy(), track["matrices"])
                    obj.matrix_basis = place @ obj.matrix_basis
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
    tracked = [m for m in job["members"] if m.get("native_track")]
    if tracked:
        # The flight-path tracks set the range: one baked frame per scene frame, from frame 1.
        scene.render.fps = int(tracked[0]["native_track"]["fps"])
        scene.frame_start, scene.frame_end = 1, len(tracked[0]["native_track"]["matrices"])
        scene.frame_set(1)
    for member in job["members"]:
        if member.get("flight_path"):
            _path_curve(scene, member["flight_path"])
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
    placed = any("native_matrix" in m for m in job["members"])
    _embed("Group members.json", dict(members=[dict(name=m["name"], sha256=m["sha256"],
                                                    **{k: m[k] for k in ("native_matrix", "placement") if k in m})
                                               for m in job["members"]],
                                      layout=layout, placement="zone actors" if placed else "original",
                                      camera_right_axis=list(right)))
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
    problems.extend(_placement_problems(scene, job["members"]))
    for member in job["members"]:
        track = member.get("native_track")
        if not track:
            continue
        frames = len(track["matrices"])
        for obj in scene.objects:
            if obj.get(MEMBER_KEY) != member["name"] or obj.parent is not None:
                continue
            action = obj.animation_data.action if obj.animation_data else None
            curves = [c for layer in action.layers for strip in layer.strips for bag in strip.channelbags
                      for c in bag.fcurves] if action else []
            if len(curves) != 10 or any(len(c.keyframe_points) != frames for c in curves):
                problems.append("flight path keys: " + obj.name)
    if any(m.get("native_track") for m in job["members"]) and scene.frame_end - scene.frame_start + 1 != len(
            next(m for m in job["members"] if m.get("native_track"))["native_track"]["matrices"]):
        problems.append("flight path frame range")
    if problems:
        raise RuntimeError("Group validation failed: " + ", ".join(problems))
    print("GROUP validation passed:", len(job["members"]), "members", flush=True)


def _placement_problems(scene, members, tolerance=1e-4):
    """Placed members built from one .blend must sit relative to each other as their zone matrices say: the root
    armature (or root mesh) world matrices relate exactly as the converted native placements do."""
    roots = {}
    for obj in scene.objects:
        label = obj.get(MEMBER_KEY)
        if label and obj.parent is None and (label not in roots or obj.type == "ARMATURE"):
            roots[label] = obj
    problems = []
    by_blend = {}
    for member in members:
        if "native_matrix" in member:
            by_blend.setdefault(member["blend"], []).append(member)
    for group in by_blend.values():
        base = group[0]
        for member in group[1:]:
            if base["name"] not in roots or member["name"] not in roots:
                problems.append("placement root missing: " + member["name"])
                continue
            expected = _native_placement(member["native_matrix"]) @ _native_placement(base["native_matrix"]).inverted()
            actual = roots[member["name"]].matrix_world @ roots[base["name"]].matrix_world.inverted()
            if max(abs(expected[i][j] - actual[i][j]) for i in range(4) for j in range(4)) > tolerance:
                problems.append("placement: " + member["name"])
    return problems


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
