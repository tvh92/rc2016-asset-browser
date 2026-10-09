"""The captured "RC PS4 Game" view as compositor nodes: no OCIO setup needed.

Reproduces rc_game_view/ocio/config.ocio's view chain inside the .blend:
    exposure 2^E -> lg2 allocation [-8, +5] -> rc_tonemap.spi1d (linear)
    -> clamp 0..1 -> sRGB encode -> rc_grade.cube
The 1D and 3D tables are packed float images sampled with Map UV (bilinear at texel
centres = linear / trilinear interpolation).  The game samples its grade as a 3D
texture, i.e. trilinearly; the OCIO config uses tetrahedral.  The scene view is
set to "Raw" so Blender adds nothing after the compositor; the result shows in
renders and in the viewport (Rendered / Material Preview, compositor "Always").
"""
import json
import os

import bpy
import numpy as np

VIEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rc_game_view")
GROUP_NAME = "RC PS4 Game View (compositor)"
TREE_NAME = "RC PS4 Game compositor"
LUT1D_NAME = "RC PS4 tonemap 1D LUT"
LUT3D_NAME = "RC PS4 grade 3D LUT (tiled)"
VERSION = 1


def read_spi1d(path):
    rows, length = [], None
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if not parts or parts[0] in ("Version", "From", "Components", "{", "}"):
                continue
            if parts[0] == "Length":
                length = int(parts[1]); continue
            rows.append([float(v) for v in parts])
    lut = np.array(rows, dtype=np.float32)
    assert length == len(lut) and lut.shape[1] == 3, "unexpected spi1d layout"
    return lut


def read_cube(path):
    size, rows = None, []
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if not parts or parts[0].startswith("#") or parts[0] == "TITLE":
                continue
            if parts[0] == "LUT_3D_SIZE":
                size = int(parts[1]); continue
            if parts[0] in ("DOMAIN_MIN", "DOMAIN_MAX"):
                assert [float(v) for v in parts[1:]] == ([0, 0, 0] if parts[0] == "DOMAIN_MIN" else [1, 1, 1])
                continue
            rows.append([float(v) for v in parts])
    lut = np.array(rows, dtype=np.float32)
    assert size and len(lut) == size ** 3, "unexpected cube layout"
    return size, lut            # .cube order: red fastest, then green, then blue


def _float_image(name, width, height, rgb):
    img = bpy.data.images.get(name)
    if img is not None:
        bpy.data.images.remove(img)
    img = bpy.data.images.new(name, width, height, alpha=True, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    px = np.ones((height, width, 4), dtype=np.float32)
    px[..., :3] = rgb
    img.pixels.foreach_set(px.ravel())
    img.file_format = "OPEN_EXR"
    img.pack()
    img.use_fake_user = True
    return img


def build_lut_images(view_dir=VIEW_DIR):
    lut1 = read_spi1d(os.path.join(view_dir, "rc_tonemap.spi1d"))
    n, lut3 = read_cube(os.path.join(view_dir, "rc_grade.cube"))
    img1 = _float_image(LUT1D_NAME, len(lut1), 1, lut1[None, :, :])
    # Tile blue slices left to right: pixel (x = b*n + r, y = g).
    cube = lut3.reshape(n, n, n, 3)                     # [b][g][r]
    tiled = cube.transpose(1, 0, 2, 3).reshape(n, n * n, 3)   # [g][b*n + r]
    img3 = _float_image(LUT3D_NAME, n * n, n, tiled)
    return img1, img3, len(lut1), n


def _math(nodes, links, op, a=None, b=None, clamp=False):
    m = nodes.new("ShaderNodeMath"); m.operation = op; m.use_clamp = clamp
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            m.inputs[i].default_value = v
        else:
            links.new(v, m.inputs[i])
    return m.outputs[0]


def _map_uv(nodes, links, image, u, v):
    src = nodes.new("CompositorNodeImage"); src.image = image
    uv = nodes.new("ShaderNodeCombineXYZ")
    links.new(u, uv.inputs[0]); links.new(v, uv.inputs[1])
    uv.inputs[2].default_value = 1.0         # Map UV reads Z as the UV-pass coverage mask
    m = nodes.new("CompositorNodeMapUV")
    m.inputs["Interpolation"].default_value = "Bilinear"
    m.inputs["Extension X"].default_value = "Extend"
    m.inputs["Extension Y"].default_value = "Extend"
    links.new(src.outputs["Image"], m.inputs["Image"]); links.new(uv.outputs[0], m.inputs["UV"])
    sep = nodes.new("CompositorNodeSeparateColor"); links.new(m.outputs[0], sep.inputs[0])
    return sep.outputs


def build_group(view_dir=VIEW_DIR):
    img1, img3, n1, n3 = build_lut_images(view_dir)
    g = bpy.data.node_groups.get(GROUP_NAME)
    if g is not None:
        bpy.data.node_groups.remove(g)
    g = bpy.data.node_groups.new(GROUP_NAME, "CompositorNodeTree")
    g["rc_version"] = VERSION
    g.interface.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
    e = g.interface.new_socket("Exposure (stops)", in_out="INPUT", socket_type="NodeSocketFloat")
    g.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    N, L = g.nodes, g.links
    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    sep = N.new("CompositorNodeSeparateColor"); L.new(gi.outputs["Image"], sep.inputs[0])
    gain = _math(N, L, "POWER", 2.0, gi.outputs["Exposure (stops)"])
    encoded = []
    for ch in range(3):
        x = _math(N, L, "MULTIPLY", sep.outputs[ch], gain)
        x = _math(N, L, "MAXIMUM", x, 1e-10)
        u = _math(N, L, "LOGARITHM", x, 2.0)
        u = _math(N, L, "MULTIPLY_ADD", u, 1.0 / 13.0)          # (log2 x + 8) / 13
        u.node.inputs[2].default_value = 8.0 / 13.0
        u.node.use_clamp = True
        # spi1d linear: position u*(n-1); texel centres at (i + 0.5)/n.
        tu = _math(N, L, "MULTIPLY_ADD", u, (n1 - 1) / n1); tu.node.inputs[2].default_value = 0.5 / n1
        half = N.new("ShaderNodeValue"); half.outputs[0].default_value = 0.5
        t = _map_uv(N, L, img1, tu, half.outputs[0])[ch]
        t = _math(N, L, "MINIMUM", _math(N, L, "MAXIMUM", t, 0.0), 1.0)
        lin = _math(N, L, "MULTIPLY", t, 12.92)
        pw = _math(N, L, "MULTIPLY_ADD", _math(N, L, "POWER", t, 1 / 2.4), 1.055)
        pw.node.inputs[2].default_value = -0.055
        low = _math(N, L, "LESS_THAN", t, 0.0031308)
        diff = _math(N, L, "SUBTRACT", lin, pw)
        encoded.append(_math(N, L, "MULTIPLY_ADD", low, diff))
        encoded[-1].node.inputs[2].default_value = 0.0
        L.new(pw, encoded[-1].node.inputs[2])
    r, gch, b = encoded
    # 3D LUT: bilinear in (r, g) inside a blue slice, linear between two slices.
    scale = n3 - 1
    pr = _math(N, L, "MULTIPLY", r, float(scale)); pg = _math(N, L, "MULTIPLY", gch, float(scale))
    pb = _math(N, L, "MULTIPLY", b, float(scale))
    b0 = _math(N, L, "FLOOR", pb); b0 = _math(N, L, "MINIMUM", b0, float(scale - 1))
    f = _math(N, L, "SUBTRACT", pb, b0)
    v = _math(N, L, "MULTIPLY_ADD", pg, 1.0 / n3); v.node.inputs[2].default_value = 0.5 / n3
    samples = []
    for k in (0, 1):
        bs = _math(N, L, "ADD", b0, float(k))
        xu = _math(N, L, "MULTIPLY_ADD", bs, float(n3))           # b*n + r*(n-1) + 0.5
        L.new(pr, xu.node.inputs[2])
        xu = _math(N, L, "ADD", xu, 0.5)
        xu = _math(N, L, "DIVIDE", xu, float(n3 * n3))
        samples.append(_map_uv(N, L, img3, xu, v))
    comb = N.new("CompositorNodeCombineColor")
    for ch in range(3):
        a0, a1 = samples[0][ch], samples[1][ch]
        d = _math(N, L, "SUBTRACT", a1, a0)
        out = _math(N, L, "MULTIPLY_ADD", d, f); L.new(a0, out.node.inputs[2])
        L.new(out, comb.inputs[ch])
    L.new(sep.outputs["Alpha"], comb.inputs["Alpha"])
    L.new(comb.outputs[0], go.inputs["Image"])
    return g


def apply(scene=None, view_dir=VIEW_DIR, exposure_stops=None):
    """Install the compositor game view on a scene; returns an evidence dict."""
    scene = scene or bpy.context.scene
    if exposure_stops is None:
        exposure_stops = json.load(open(os.path.join(view_dir, "rc_game_view.json")))["blender_exposure_stops"]
    group = build_group(view_dir)
    tree = bpy.data.node_groups.get(TREE_NAME) or bpy.data.node_groups.new(TREE_NAME, "CompositorNodeTree")
    tree.nodes.clear(); tree.interface.clear()
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    rl = tree.nodes.new("CompositorNodeRLayers")
    node = tree.nodes.new("CompositorNodeGroup"); node.node_tree = group; node.name = GROUP_NAME
    node.inputs["Exposure (stops)"].default_value = float(exposure_stops)
    out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(rl.outputs["Image"], node.inputs["Image"])
    tree.links.new(node.outputs["Image"], out.inputs["Image"])
    scene.compositing_node_group = tree
    scene.render.use_compositing = True
    vs = scene.view_settings
    vs.view_transform = "Raw"; vs.look = "None"; vs.exposure = 0.0; vs.gamma = 1.0
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.shading.use_compositor = "ALWAYS"
    scene["rc_view"] = "RC PS4 Game view as compositor nodes; exposure %.4f stops" % exposure_stops
    return dict(view="RC PS4 Game (compositor)", view_transform="Raw", exposure=float(exposure_stops),
                compositor_group=GROUP_NAME, version=VERSION)
