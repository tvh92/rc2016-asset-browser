"""Assemble several delivered models into one .blend framed by one camera.

Run by ``run.py --group-blend NAME`` inside Blender (``blender --background --python group_blend.py -- --job JOB``;
``--validate`` reopens the candidate).  Each member is a finished per-model .blend: its meshes and armature are
appended and centred on one spot along the reference camera's right axis, so the members overlap, with the animation cleared so every
member stands in its bind pose.  The scene then uses the shared review lighting, one camera dollied to fit the
whole group and one Render Region around it, exactly as a single-model build does.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy  # type: ignore
import numpy as np  # type: ignore
from mathutils import Euler, Vector  # type: ignore

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


def _load_member(member, label):
    """Append one member's meshes and armatures; returns all of them."""
    path = Path(member["blend"])
    if file_hash(path) != member["sha256"]:
        raise RuntimeError("Member changed since it was delivered: %s" % path)
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        target.objects = list(source.objects)
    kept = []
    for obj in target.objects:
        if obj is None:
            continue
        if obj.type not in ("MESH", "ARMATURE"):  # cameras, lights and diagnostic empties are not part of the group
            bpy.data.objects.remove(obj, do_unlink=True)
            continue
        if obj.type == "ARMATURE":
            # Bind pose: no action, no NLA strips, and every pose bone back at its rest transform.
            obj.animation_data_clear()
            for bone in obj.pose.bones:
                bone.location = (0.0, 0.0, 0.0)
                bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                bone.rotation_euler = (0.0, 0.0, 0.0)
                bone.scale = (1.0, 1.0, 1.0)
        obj[MEMBER_KEY] = label
        bpy.context.scene.collection.objects.link(obj)
        kept.append(obj)
    return kept


def _layout(members, right):
    """Centre every member on the origin along ``right``, so the members overlap in the same place.

    The extent is measured on the member's visible meshes; only root objects move, and their children follow."""
    bpy.context.view_layer.update()
    layout = []
    for objects in members:
        meshes = [obj for obj in objects if obj.type == "MESH" and not obj.hide_render]
        low, high = _extent(_world_corners(meshes), right)
        shift = -(low + high) / 2
        for obj in objects:
            if obj.parent is None:
                obj.location = obj.location + right * shift
        layout.append(dict(width=float(high - low), shift=float(shift)))
    return layout


def assemble(job):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    apply_new_scene_defaults(scene, job.get("hdri"), configure_world=True, lighting=job.get("shared_lighting"))
    right = Euler(tuple(math.radians(v) for v in CAMERA_EULER_DEGREES)).to_matrix() @ Vector((1.0, 0.0, 0.0))
    members = []
    for index, member in enumerate(job["members"]):
        members.append(_load_member(member, member["name"]))
        print("GROUP member", index + 1, len(job["members"]), member["name"], flush=True)
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
                                      layout=layout, placement="overlapping", camera_right_axis=list(right)))
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
