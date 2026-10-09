"""Captured Veldin game look for a scene: key light, ambient world, exposure and tonemap.

Shared by render_game_look.py and the extractor pipeline (``--lighting-source veldin``).
The captured tonemap is installed as compositor nodes (game_view_compositor.py), so
the .blend shows the game view in any Blender 5 without OCIO setup.
"""
import json
import math
import os

import bpy
from mathutils import Vector

from rc2016.pipeline import game_shader as rc

VIEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "game_view")
KEY_LIGHT_NAME = "Veldin key light (captured)"
LIGHTING_SOURCE = "veldin_capture"


def _rgba(socks, name):
    # ShaderNodeMix has float/vector/colour sockets that share the names A/B/Result.
    return next(s for s in socks if s.name == name and s.type == "RGBA")


def apply_veldin_look(scene=None, view_dir=VIEW_DIR, elevation_deg=None, azimuth_offset_deg=None):
    """Replace scene lighting with the Veldin capture; returns an evidence dict."""
    scene = scene or bpy.context.scene
    info = json.load(open(os.path.join(view_dir, "rc_game_view.json")))
    cam = scene.camera
    if cam is None:
        raise RuntimeError("Veldin look needs the scene camera: the key light is placed on its side")

    # Key light: captured colour/strength, presentation direction.  The captured
    # direction is relative to the game camera of that frame, so it has no meaning
    # for a standalone model.  Light from the camera's side, from above.
    for o in [o for o in scene.objects if o.type == "LIGHT"]:
        o.hide_render = True
    key = info.get("key_light", {})
    colour, strength = rc.game_light_to_blender_strength(key.get("colour_game", [1, 1, 1]))
    if elevation_deg is None:
        elevation_deg = float(os.environ.get("RC_SUN_ELEVATION_DEG", "45"))
    if azimuth_offset_deg is None:
        azimuth_offset_deg = float(os.environ.get("RC_SUN_AZIMUTH_OFFSET_DEG", "30"))
    elevation, azimuth_offset = math.radians(elevation_deg), math.radians(azimuth_offset_deg)
    back = cam.matrix_world.to_3x3() @ Vector((0, 0, 1))          # camera -> viewer direction
    heading = math.atan2(back.y, back.x) + azimuth_offset
    to_light_world = Vector((math.cos(heading) * math.cos(elevation),
                             math.sin(heading) * math.cos(elevation), math.sin(elevation)))
    sun_data = bpy.data.lights.new(KEY_LIGHT_NAME, "SUN")
    sun_data.color = colour
    sun_data.energy = strength
    sun_data.angle = math.radians(0.5)
    sun = bpy.data.objects.new(sun_data.name, sun_data)
    sun["lighting_role"] = "veldin_captured_key_light"
    scene.collection.objects.link(sun)
    sun.rotation_euler = (-to_light_world).to_track_quat("-Z", "Y").to_euler()

    # Ambient.  The lighting pass uses the hemisphere fill C0 + C1*n_y only when
    # frame flag 128 is set; otherwise (Veldin capture) it uses the per-pixel
    # irradiance buffer, fitted by fit_capture_ambient.py as radiance c0 + c1_up*w_z.
    amb = info.get("capture_ambient")
    fill = None
    if isinstance(amb, dict) and not amb.get("hemisphere_fill_used_by_game"):
        w = amb["blender_world"]
        fill = dict(name="Veldin captured ambient (irradiance-buffer fit)",
                    c0=w["radiance_c0"], c1=w["radiance_c1_up"])
    elif info.get("hemisphere_fill"):
        h = info["hemisphere_fill"]
        fill = dict(name="Veldin captured hemisphere fill",
                    c0=h["c0"], c1=[1.5 * v for v in h["c1_times_normal"]])
    if fill and os.environ.get("RC_KEEP_WORLD") != "1":
        world = bpy.data.worlds.new(fill["name"])
        world.use_nodes = True
        N, L = world.node_tree.nodes, world.node_tree.links
        N.clear()
        tc = N.new("ShaderNodeTexCoord")
        sep = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Generated"], sep.inputs[0])
        mix = N.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "ADD"; mix.clamp_result = False
        _rgba(mix.inputs, "A").default_value = (*fill["c0"], 1)
        scale = N.new("ShaderNodeMix"); scale.data_type = "RGBA"; scale.blend_type = "MULTIPLY"
        _rgba(scale.inputs, "A").default_value = (*fill["c1"], 1)
        up = N.new("ShaderNodeMath"); up.operation = "MAXIMUM"; up.inputs[1].default_value = -1.0
        L.new(sep.outputs["Z"], up.inputs[0])
        comb = N.new("ShaderNodeCombineXYZ")
        for ch in ("X", "Y", "Z"):
            L.new(up.outputs[0], comb.inputs[ch])
        scale.inputs["Factor"].default_value = 1.0
        L.new(comb.outputs[0], _rgba(scale.inputs, "B"))
        mix.inputs["Factor"].default_value = 1.0
        L.new(_rgba(scale.outputs, "Result"), _rgba(mix.inputs, "B"))
        clampn = N.new("ShaderNodeVectorMath"); clampn.operation = "MAXIMUM"
        L.new(_rgba(mix.outputs, "Result"), clampn.inputs[0])
        bg = N.new("ShaderNodeBackground"); bg.inputs["Strength"].default_value = 1.0
        L.new(clampn.outputs[0], bg.inputs["Color"])
        out = N.new("ShaderNodeOutputWorld"); L.new(bg.outputs[0], out.inputs["Surface"])
        scene.world = world

    # Captured tonemap + exposure.
    from rc2016.pipeline import game_view_compositor
    view = game_view_compositor.apply(scene, view_dir)
    evidence = dict(lighting_source=LIGHTING_SOURCE, key_light=KEY_LIGHT_NAME, sun_strength=strength,
                    sun_colour=list(colour), sun_elevation_deg=elevation_deg,
                    sun_azimuth_offset_deg=azimuth_offset_deg,
                    world=scene.world.name if scene.world else None,
                    exposure=view["exposure"], view=view["view"], view_transform=view["view_transform"],
                    compositor_group=view["compositor_group"],
                    scope="Captured Veldin key-light colour/strength, fitted ambient, exposure and tonemap; "
                          "presentation light direction; not the model's own level lighting.")
    scene["lighting_source"] = LIGHTING_SOURCE
    scene["lighting_evidence_json"] = json.dumps(evidence, sort_keys=True)
    return evidence
