"""Game-accurate opaque surface shader for Ratchet & Clank (PS4) assets in Blender.

Derived from the shadPS4 RenderDoc capture shaders (see docs/shading.md):
  * G-buffer writer  fragment_6553  (_g = BC7 *sRGB*, alpha = gloss)
  * deferred lighting fragment_532  (GGX + Smith-Schlick + Schlick, Oren-Nayar diffuse)

Per-pixel game equations, with gloss = _g.alpha and F0 = _g.rgb (sRGB-decoded):
    s           = 2 ^ (-11 * gloss)                 # GGX alpha^2
    GGX alpha   = 2 ^ (-5.5 * gloss)
    Blender roughness (alpha = r^2)  r = 2 ^ (-2.75 * gloss)
    F90         = saturate(60 * F0)
    diffuse     = _c.rgb * light       (NO (1 - _g) factor, no Fresnel weighting)
    specular    = GGX(F0, alpha) * light               (simply added to diffuse)
    self-lit    = _c.rgb * x,   x = _c.alpha * K  (K = material key 0x66EFA55B; see
                  emissive_constants_from_reports)
    lights      = Blender light strength = pi * game light colour  (game omits 1/pi)

Usage (Blender 4.0+; tested logic targets Blender 5.0):
    import sys; sys.path.append(r"<repository folder>")
    from rc2016.pipeline import game_shader as rc
    rc.convert_all()                       # every material that has _c and _g images
    rc.convert_material(bpy.data.materials["wpn_Sheepinator"], emissive_intensity=4.0)
    rc.restore_backups()                   # undo: swap back the fake-user originals

Each converted material keeps an untouched copy named "<name> | pre-RC-game-shader"
(fake user), so nothing is lost.  Glass / energy / VFX materials are skipped.
"""
import json
import math
import re

import bpy

GROUP_NAME = "RC PS4 Deferred BRDF"
GROUP_VERSION = 2
BACKUP_SUFFIX = " | pre-RC-game-shader"


# ----------------------------------------------------------------------------- group
def _sock(group, name, kind, in_out, default=None, lo=None, hi=None):
    s = group.interface.new_socket(name=name, in_out=in_out, socket_type=kind)
    if default is not None and hasattr(s, "default_value"):
        s.default_value = default
    if lo is not None and hasattr(s, "min_value"):
        s.min_value = lo
    if hi is not None and hasattr(s, "max_value"):
        s.max_value = hi
    return s


def _math(nodes, op, a=None, b=None, label=None, clamp=False):
    n = nodes.new("ShaderNodeMath")
    n.operation = op
    n.use_clamp = clamp
    if label:
        n.label = n.name = label
    if a is not None and not hasattr(a, "is_linked"):
        n.inputs[0].default_value = a
    if b is not None and not hasattr(b, "is_linked"):
        n.inputs[1].default_value = b
    return n


def build_group(force=False):
    """Create (or return) the shared node group implementing the game BRDF."""
    g = bpy.data.node_groups.get(GROUP_NAME)
    if g is not None and not force and g.get("rc_version") == GROUP_VERSION:
        return g
    if g is None:
        g = bpy.data.node_groups.new(GROUP_NAME, "ShaderNodeTree")
    g.nodes.clear()
    g.interface.clear()
    g["rc_version"] = GROUP_VERSION
    g["rc_source"] = "RenderDoc CUSA01073 fragment_532 (lighting) + fragment_6553 (G-buffer)"
    _sock(g, "Albedo (_c RGB)", "NodeSocketColor", "INPUT", (0.5, 0.5, 0.5, 1))
    _sock(g, "Self-lit mask (_c alpha)", "NodeSocketFloat", "INPUT", 0.0, 0.0, 1.0)
    _sock(g, "Specular F0 (_g RGB, sRGB-decoded)", "NodeSocketColor", "INPUT", (0.04, 0.04, 0.04, 1))
    _sock(g, "Gloss (_g alpha)", "NodeSocketFloat", "INPUT", 0.5, 0.0, 1.0)
    _sock(g, "Normal", "NodeSocketVector", "INPUT")
    _sock(g, "Specular scale", "NodeSocketFloat", "INPUT", 1.0, 0.0, 10.0)
    _sock(g, "Gloss scale", "NodeSocketFloat", "INPUT", 1.0, 0.0, 10.0)
    _sock(g, "Emissive intensity", "NodeSocketFloat", "INPUT", 0.0, 0.0, 1000.0)
    _sock(g, "Shader", "NodeSocketShader", "OUTPUT")
    _sock(g, "Blender roughness", "NodeSocketFloat", "OUTPUT")

    N, L = g.nodes, g.links
    gi = N.new("NodeGroupInput"); gi.location = (-1200, 0)
    go = N.new("NodeGroupOutput"); go.location = (900, 0)

    # gloss_eff = sqrt(saturate(gloss^2 * gloss_scale))      (fragment_6553)
    sq = _math(N, "POWER", None, 2.0, "gloss^2");             L.new(gi.outputs[3], sq.inputs[0])
    gs = _math(N, "MULTIPLY", label="* gloss scale", clamp=True)
    L.new(sq.outputs[0], gs.inputs[0]);                         L.new(gi.outputs[6], gs.inputs[1])
    ge = _math(N, "SQRT", label="gloss_eff");                   L.new(gs.outputs[0], ge.inputs[0])
    # Blender roughness r = 2^(-2.75*gloss_eff)  (=> GGX alpha = 2^(-5.5 g), alpha^2 = 2^(-11 g))
    ex = _math(N, "MULTIPLY", None, -2.75, "-2.75 * gloss");    L.new(ge.outputs[0], ex.inputs[0])
    rough = _math(N, "POWER", 2.0, None, "roughness = 2^(-2.75 g)")
    L.new(ex.outputs[0], rough.inputs[1])
    L.new(rough.outputs[0], go.inputs[1])

    # F0 = _g * specular scale ; F90 weight = saturate(60 * max(F0))
    f0 = N.new("ShaderNodeVectorMath"); f0.operation = "SCALE"; f0.name = f0.label = "F0"
    L.new(gi.outputs[2], f0.inputs[0]);                         L.new(gi.outputs[5], f0.inputs["Scale"])
    sep = N.new("ShaderNodeSeparateColor");                     L.new(f0.outputs[0], sep.inputs[0])
    m1 = _math(N, "MAXIMUM");                                   L.new(sep.outputs[0], m1.inputs[0]); L.new(sep.outputs[1], m1.inputs[1])
    m2 = _math(N, "MAXIMUM");                                   L.new(m1.outputs[0], m2.inputs[0]); L.new(sep.outputs[2], m2.inputs[1])
    f90 = _math(N, "MULTIPLY", None, 60.0, "F90 = saturate(60*F0)", clamp=True)
    L.new(m2.outputs[0], f90.inputs[0])

    # Diffuse: albedo straight from _c (game: out = (diffuse_light + selfLit) * albedo)
    diff = N.new("ShaderNodeBsdfDiffuse"); diff.name = diff.label = "Game diffuse (_c)"
    diff.inputs["Roughness"].default_value = 0.0   # game uses Oren-Nayar(sigma^2 = 2^-11g); see audit
    L.new(gi.outputs[0], diff.inputs["Color"]);                 L.new(gi.outputs[4], diff.inputs["Normal"])

    # Specular: Schlick-Fresnel GGX with F0 = _g.  A metallic Principled with white
    # specular tint is exactly Schlick(F0 -> 1) on single-scatter GGX.
    spec = N.new("ShaderNodeBsdfPrincipled"); spec.name = spec.label = "Game GGX specular (_g)"
    spec.distribution = "GGX"
    spec.inputs["Metallic"].default_value = 1.0
    if "Specular Tint" in spec.inputs:
        spec.inputs["Specular Tint"].default_value = (1, 1, 1, 1)
    # Game: F = F0 + (F90 - F0)(1-VH)^5 with F90 = sat(60 F0).  This equals
    # F90 * Schlick(F0/F90 -> 1), so feed F0/F90 to the lobe and weight it by F90.
    inv = _math(N, "MAXIMUM", None, 1e-4, "max(F90, eps)"); L.new(f90.outputs[0], inv.inputs[0])
    rec = _math(N, "DIVIDE", 1.0, None, "1 / F90");          L.new(inv.outputs[0], rec.inputs[1])
    f0n = N.new("ShaderNodeVectorMath"); f0n.operation = "SCALE"; f0n.name = f0n.label = "F0 / F90"
    L.new(f0.outputs[0], f0n.inputs[0]);                      L.new(rec.outputs[0], f0n.inputs["Scale"])
    L.new(f0n.outputs[0], spec.inputs["Base Color"])
    L.new(rough.outputs[0], spec.inputs["Roughness"])
    L.new(gi.outputs[4], spec.inputs["Normal"])

    add = N.new("ShaderNodeAddShader"); add.name = add.label = "diffuse + specular (no energy split)"
    L.new(diff.outputs[0], add.inputs[0]);                      L.new(spec.outputs[0], add.inputs[1])
    gate = N.new("ShaderNodeMixShader"); gate.name = gate.label = "spec weight = F90"
    L.new(f90.outputs[0], gate.inputs[0]);                      L.new(diff.outputs[0], gate.inputs[1]); L.new(add.outputs[0], gate.inputs[2])

    # Self-lit: albedo * (_c.alpha * intensity)
    es = _math(N, "MULTIPLY", label="self-lit = _c.a * intensity")
    L.new(gi.outputs[1], es.inputs[0]);                         L.new(gi.outputs[7], es.inputs[1])
    em = N.new("ShaderNodeEmission"); em.name = em.label = "Game self-lit"
    L.new(gi.outputs[0], em.inputs["Color"]);                   L.new(es.outputs[0], em.inputs["Strength"])
    out = N.new("ShaderNodeAddShader");                         L.new(gate.outputs[0], out.inputs[0]); L.new(em.outputs[0], out.inputs[1])
    L.new(out.outputs[0], go.inputs[0])

    x = -900
    for n in (sq, gs, ge, ex, rough, f0, sep, m1, m2, f90, inv, rec, f0n, diff, spec, add, gate, es, em, out):
        n.location = (x, n.location[1]); x += 110
    return g


# ----------------------------------------------------------------------------- material
_ROLE_BY_SUFFIX = (("_c", "c"), ("_g", "g"), ("_n", "n"))
_ROLE_BY_PIPELINE = {"base_color": "c", "colored_specular_gloss": "g", "normal": "n"}


def _role(node):
    if re.search(r"\|\s*secondary_normal\s*$", node.name):
        return None          # skin detail normal: never the primary _n
    m = re.search(r"\|\s*(base_color|colored_specular_gloss|normal)\s*$", node.name)
    if m:
        return _ROLE_BY_PIPELINE[m.group(1)]
    candidates = [node.get("source_texture_path", ""), node.label]
    if node.image is not None:
        candidates += [node.image.get("source_texture_path", ""), node.image.name,
                       bpy.path.basename(node.image.filepath)]
    for text in candidates:
        stem = re.sub(r"(\.\d{3})+$", "", str(text).lower())          # ".001" duplicates
        stem = re.sub(r"\.(texture|dds|png|tga|exr|blender)+$", "", stem)
        stem = re.sub(r"\.(texture|dds|png|tga|exr|blender)+$", "", stem)
        for suffix, role in _ROLE_BY_SUFFIX:
            if stem.endswith(suffix):
                return role
    return None


def _tex_path(node):
    return str(node.get("source_texture_path", "") or
               (node.image.get("source_texture_path", "") if node.image else "") or
               (node.image.name if node.image else "")).lower()


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _find_textures(mat):
    """Pick one _c/_g/_n image node; shared/detail maps lose to the _c's own set.

    Images that reach the active Material Output win over unconnected ones (pipeline
    materials also carry materialgraph defaults such as 2x2 default_pink_shader_c),
    then larger images win.
    """
    reached = set()
    for out in mat.node_tree.nodes:
        if out.type == "OUTPUT_MATERIAL" and out.is_active_output:
            _upstream(out, reached)
    by_role = {}
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            role = _role(node)
            if role:
                by_role.setdefault(role, []).append(node)
    found = {}
    if "c" in by_role:
        found["c"] = max(by_role["c"], key=lambda n: (n in reached, n.image.size[0] * n.image.size[1]))
        ref = _tex_path(found["c"])
        for role in ("g", "n"):
            if role in by_role:
                found[role] = max(by_role[role], key=lambda n: (n in reached, _common_prefix(_tex_path(n), ref)))
    return found


def _has_selflit_mask(image, threshold=0.5):
    """True when _c alpha contains a sparse authored mask (typical emissive graphs)."""
    try:
        import numpy as np
        px = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(px)
        frac = float((px[3::4] > threshold).mean())
        return 1e-4 < frac < 0.6
    except Exception:
        return False


def _upstream(node, keep):
    for s in node.inputs:
        for link in s.links:
            if link.from_node not in keep:
                keep.add(link.from_node)
                _upstream(link.from_node, keep)


def convert_material(mat, emissive_intensity=None, specular_scale=1.0, gloss_scale=1.0,
                     g_is_srgb=True, flip_normal_green=False, keep_backup=True, keep_nodes=()):
    """Rebuild one material with the game BRDF.  Returns True when converted.

    emissive_intensity: None -> 4.0 when _c alpha holds a sparse authored mask
        (emissive-in-_c-alpha graphs), else 0.  Adjust per material.
    g_is_srgb: the capture shows _g is BC7_UNORM_SRGB; leave True unless a texture's
        native DXGI format is a non-sRGB one (the pipeline manifest has it).
    """
    if not mat or getattr(mat, "node_tree", None) is None:
        return False
    tex = _find_textures(mat)
    if "c" not in tex or "g" not in tex:
        return False
    has_backup = any(m.get("rc_backup_of") == mat.name for m in bpy.data.materials)
    if keep_backup and not has_backup:
        backup = mat.copy()
        backup.name = mat.name + BACKUP_SUFFIX
        backup.use_fake_user = True
        backup["rc_backup_of"] = mat.name

    c, gnode, n = tex["c"], tex["g"], tex.get("n")
    c.image.colorspace_settings.name = "sRGB"
    gnode.image.colorspace_settings.name = "sRGB" if g_is_srgb else "Non-Color"
    for t in (c, gnode):
        t.image.alpha_mode = "CHANNEL_PACKED"
    if n is not None:
        n.image.colorspace_settings.name = "Non-Color"

    nt = mat.node_tree
    keep = set(tex.values()) | set(keep_nodes)
    for t in list(keep):
        _upstream(t, keep)                 # UV maps / mapping nodes feeding the images
    for node in list(nt.nodes):
        if node not in keep:
            nt.nodes.remove(node)

    grp = nt.nodes.new("ShaderNodeGroup")
    grp.node_tree = build_group()
    grp.name = grp.label = GROUP_NAME
    grp.location = (300, 0)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    out.is_active_output = True
    L = nt.links
    L.new(c.outputs["Color"], grp.inputs[0])
    L.new(c.outputs["Alpha"], grp.inputs[1])
    L.new(gnode.outputs["Color"], grp.inputs[2])
    L.new(gnode.outputs["Alpha"], grp.inputs[3])
    if n is not None:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.name = nm.label = "Tangent normal (_n)"
        nm.location = (0, -350)
        nm.inputs["Strength"].default_value = 1.0
        src = n.outputs["Color"]
        if flip_normal_green:
            sep = nt.nodes.new("ShaderNodeSeparateColor"); sep.location = (-250, -450)
            inv = nt.nodes.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.inputs[0].default_value = 1.0
            inv.location = (-120, -450)
            com = nt.nodes.new("ShaderNodeCombineColor"); com.location = (-50, -450)
            L.new(src, sep.inputs[0]); L.new(sep.outputs[0], com.inputs[0])
            L.new(sep.outputs[1], inv.inputs[1]); L.new(inv.outputs[0], com.inputs[1])
            L.new(sep.outputs[2], com.inputs[2]); src = com.outputs[0]
        L.new(src, nm.inputs["Color"])
        L.new(nm.outputs["Normal"], grp.inputs[4])
    else:   # never leave the group's Normal input at its (0,0,0) default
        geo = nt.nodes.new("ShaderNodeNewGeometry"); geo.location = (0, -350)
        L.new(geo.outputs["Normal"], grp.inputs[4])
    grp.inputs[5].default_value = specular_scale
    grp.inputs[6].default_value = gloss_scale
    if emissive_intensity is None:
        # Fallback when the material's K (key EMISSIVE_K_KEY) was not supplied;
        # 4.0 is a starting point (strength is in game light units).
        emissive_intensity = 4.0 if _has_selflit_mask(c.image) else 0.0
    grp.inputs[7].default_value = float(emissive_intensity)
    L.new(grp.outputs[0], out.inputs["Surface"])
    mat["rc_shader"] = "%s v%d" % (GROUP_NAME, GROUP_VERSION)
    mat["rc_shader_evidence"] = ("Equations from RenderDoc fragment_532/6553; emissive intensity and "
                                 "per-material spec/gloss scales are material constants not yet read "
                                 "from a capture of this asset (defaults 1.0 / user value).")
    return True


def convert_all(emissive_by_material=None, **kwargs):
    """Convert every eligible material.  emissive_by_material: {name: K} overrides."""
    done = []
    emissive_by_material = emissive_by_material or {}
    reconvert = kwargs.pop("reconvert", False)
    for mat in list(bpy.data.materials):
        if mat.name.endswith(BACKUP_SUFFIX) or mat.get("rc_backup_of"):
            continue
        if mat.get("rc_shader") and not reconvert:
            done.append(mat.name)          # already converted (keeps model front-ends)
            continue
        extra = dict(kwargs)
        if mat.name in emissive_by_material:
            extra["emissive_intensity"] = emissive_by_material[mat.name]
        if convert_material(mat, **extra):
            done.append(mat.name)
    print("RC game shader applied to %d materials: %s" % (len(done), ", ".join(done)))
    return done


# Material-instance property hash of the self-lit constant K (self-lit = _c.rgb *
# _c.a * K).  Identified from the Veldin capture: the emissive G-buffer shader reads
# K from its material constant buffer (8.0 for hero_Ratchet_Body), and 8.0 is that
# material's value only under this key.  Sheepinator's value is 5.0.
EMISSIVE_K_KEY = 0x66EFA55B


def emissive_constants_from_reports(materials_dir):
    """{material name: K} from pipeline material reports (<model>/materials/*.json)."""
    import glob
    import os
    import struct
    found = {}
    for path in glob.glob(os.path.join(materials_dir, "*.json")):
        try:
            with open(path) as fh:
                report = json.load(fh)
        except Exception:
            continue
        ip = report.get("instance_parameters") or {}
        for p in (ip.get("parameters") or []) + (ip.get("unsupported_parameters") or []):
            if int(str(p.get("key_hash", p.get("key", "0"))), 0) == EMISSIVE_K_KEY and p.get("raw_hex"):
                found[report.get("name")] = struct.unpack("<f", bytes.fromhex(p["raw_hex"])[:4])[0]
                break
    return found


def restore_backups():
    """Swap every converted material back to its saved original."""
    for backup in [m for m in bpy.data.materials if m.get("rc_backup_of")]:
        target = bpy.data.materials.get(backup["rc_backup_of"])
        if target is None:
            continue
        target.user_remap(backup)
        name = target.name
        bpy.data.materials.remove(target)
        backup.name = name
        del backup["rc_backup_of"]


# ----------------------------------------------------------------------------- material graphs
# .materialgraph section 0x45C4F4C0 holds parameter tables of (u16 byte offset, u16 size,
# u32 key) records; a new table starts where the offset returns to 0.  Table 0 is the main
# G-buffer shader's constant-buffer layout (Ratchet control: key 0x66EFA55B at dword 5, as
# read from the capture) and section 0xA59F667B holds the graph defaults in that layout.
# Further tables are layouts of other shaders in the graph.
PARAM_TABLES_SECTION = 0x45C4F4C0
PARAM_DEFAULTS_SECTION = 0xA59F667B


def materialgraph_parameter_tables(graph_path):
    """([[(dword, size_bytes, key), ...] per table], {key: default floats})."""
    import struct
    with open(graph_path, "rb") as fh:
        data = fh.read()
    count = struct.unpack_from("<I", data, 12)[0]
    sections = {struct.unpack_from("<I", data, 16 + 12 * i)[0]: struct.unpack_from("<II", data, 20 + 12 * i)
                for i in range(count)}
    off, size = sections[PARAM_TABLES_SECTION]
    tables, cur = [], []
    for i in range(off, off + size, 8):
        byte_off, nbytes, key = struct.unpack_from("<HHI", data, i)
        if byte_off == 0 and cur:
            tables.append(cur)
            cur = []
        cur.append((byte_off // 4, nbytes, key))
    tables.append(cur)
    defaults = {}
    if PARAM_DEFAULTS_SECTION in sections:
        doff, dsize = sections[PARAM_DEFAULTS_SECTION]
        for dword, nbytes, key in tables[0]:
            if 4 * dword + nbytes <= dsize:
                defaults[key] = struct.unpack_from("<%df" % (nbytes // 4), data, doff + 4 * dword)
    return tables, defaults


def material_parameter_values(report, graph_path):
    """{key: float or tuple}: graph defaults overridden by the material instance values."""
    import struct
    _, defaults = materialgraph_parameter_tables(graph_path)
    values = {k: (v[0] if len(v) == 1 else v) for k, v in defaults.items()}
    ip = report.get("instance_parameters") or {}
    for p in (ip.get("parameters") or []) + (ip.get("unsupported_parameters") or []):
        raw = bytes.fromhex(p.get("raw_hex", ""))
        if len(raw) >= 4:
            vals = struct.unpack("<%df" % (len(raw) // 4), raw[:len(raw) // 4 * 4])
            values[int(str(p.get("key_hash", p.get("key", "0"))), 0)] = vals[0] if len(vals) == 1 else vals
    return values


# ----------------------------------------------------------------------------- skin_emissive
# materialgraph/global/basic_skin/skin_emissive.materialgraph.  Equations from its cached
# shadPS4 G-buffer writer 0x567fe897 (CB = graph parameter table 1, dwords 1..8):
#   F      = saturate((1.001 - saturate(N.V)) ^ P4)            (interpolated vertex normal)
#   albedo = saturate(_c.rgb * lerp(P5, (0.745404, 0.644480, 0.603827), F))
#            (MRT1 is R8G8B8A8_SRGB UNORM, so the stored albedo, which also scales
#             the self-lit term in lighting, cannot exceed 1; P5 is 1.4 on some skins)
#   F0     = grey  min(P1, lerp(P8*_g.G, saturate(P3*P8*_g.G), F))
#   gloss  = sqrt(saturate(lerp(P6*_g.R, saturate(P2*P6*_g.R), F)))   (flat-surface AA term = 1)
#   selfLit K = P7 (key 0x66EFA55B), mask = _c.a.   _g.B and _g.A are not read.
SKIN_GROUP_NAME = "RC PS4 skin_emissive G-buffer"
SKIN_GROUP_VERSION = 3
SKIN_SHADER_ID = "0x567fe897"
SKIN_ALBEDO_FRESNEL_TINT = (0.745404183864593505859375, 0.644479691982269287109375, 0.60382735729217529296875)
SKIN_CB_PARAMS = {1: "Specular cap", 2: "Gloss Fresnel multiplier", 3: "Specular Fresnel multiplier",
                  4: "Fresnel exponent", 5: "Albedo scale", 6: "Gloss scale", 7: "Self-lit K",
                  8: "Specular scale"}
_SKIN_KEYS = {"Specular cap": 0x03168D4E, "Gloss Fresnel multiplier": 0xAB221207,
              "Specular Fresnel multiplier": 0xE6185DBC, "Fresnel exponent": 0xA81E0932,
              "Albedo scale": 0xF38FFA75, "Gloss scale": 0xF503794A, "Self-lit K": EMISSIVE_K_KEY,
              "Specular scale": 0xC2473357}

# basic_skin.materialgraph: cached deferred writer 0x9aad640b computes the same front-end
# as 0x567fe897 with table 1 = cap 1, gloss Fresnel 2, spec Fresnel 3, exponent 4, albedo 5,
# gloss scale 6, spec scale 7, and writes no self-lit term (MRT1.a = 0).  Its forward shader
# 0xf0cd85ff blends a detail normal (see apply_native_skin_normal).
SKIN_FAMILIES = {
    "skin_emissive": dict(group=SKIN_GROUP_NAME, shader=SKIN_SHADER_ID, table1=SKIN_CB_PARAMS,
                          detail_normal=False),
    "basic_skin": dict(group="RC PS4 basic_skin G-buffer", shader="0x9aad640b",
                       table1={1: "Specular cap", 2: "Gloss Fresnel multiplier",
                               3: "Specular Fresnel multiplier", 4: "Fresnel exponent",
                               5: "Albedo scale", 6: "Gloss scale", 7: "Specular scale"},
                       detail_normal=True),
}
SECONDARY_NORMAL_BLEND_KEY = 0x59011FBD
SECONDARY_NORMAL_UV_SCALE_KEY = 0xB7ABB64A


def skin_emissive_params(report, graph_path):
    """{socket name: value} for the skin front-end, mapped through graph table 1."""
    tables, _ = materialgraph_parameter_tables(graph_path)
    layout = {dword: key for dword, _, key in tables[1]}
    values = material_parameter_values(report, graph_path)
    assert layout.get(7) == EMISSIVE_K_KEY, "skin_emissive table 1 does not put K at dword 7"
    return {name: float(values[layout[dword]]) for dword, name in SKIN_CB_PARAMS.items()}, \
        {name: "0x%08X" % layout[dword] for dword, name in SKIN_CB_PARAMS.items()}


def build_skin_emissive_group(force=False, family="skin_emissive"):
    spec = SKIN_FAMILIES[family]
    g = bpy.data.node_groups.get(spec["group"])
    if g is not None and not force and g.get("rc_version") == SKIN_GROUP_VERSION:
        return g
    if g is None:
        g = bpy.data.node_groups.new(spec["group"], "ShaderNodeTree")
    g.nodes.clear()
    g.interface.clear()
    g["rc_version"] = SKIN_GROUP_VERSION
    g["rc_source"] = "%s.materialgraph, cached shadPS4 G-buffer writer %s" % (family, spec["shader"])
    _sock(g, "_c RGB", "NodeSocketColor", "INPUT", (0.5, 0.5, 0.5, 1))
    _sock(g, "_g RGB", "NodeSocketColor", "INPUT", (0, 0, 0, 1))
    _sock(g, "Material normal", "NodeSocketVector", "INPUT")
    for name in SKIN_CB_PARAMS.values():
        if name != "Self-lit K":
            _sock(g, name, "NodeSocketFloat", "INPUT", 1.0, 0.0, 100.0)
    _sock(g, "Albedo", "NodeSocketColor", "OUTPUT")
    _sock(g, "Specular F0", "NodeSocketColor", "OUTPUT")
    _sock(g, "Gloss", "NodeSocketFloat", "OUTPUT")
    _sock(g, "Fresnel weight", "NodeSocketFloat", "OUTPUT")
    N, L = g.nodes, g.links
    gi = N.new("NodeGroupInput"); gi.location = (-1100, 0)
    go = N.new("NodeGroupOutput"); go.location = (700, 0)
    p = {s.name: gi.outputs[s.name] for s in g.interface.items_tree if s.in_out == "INPUT"}

    geo = N.new("ShaderNodeNewGeometry")
    ndv = N.new("ShaderNodeVectorMath"); ndv.operation = "DOT_PRODUCT"; ndv.label = "N.V (vertex normal)"
    L.new(p["Material normal"], ndv.inputs[0]); L.new(geo.outputs["Incoming"], ndv.inputs[1])
    satn = _math(N, "MULTIPLY", None, 1.0, "saturate(N.V)", clamp=True); L.new(ndv.outputs["Value"], satn.inputs[0])
    base = _math(N, "SUBTRACT", 1.001, None, "1.001 - N.V"); L.new(satn.outputs[0], base.inputs[1])
    fres = _math(N, "POWER", label="F = (1.001 - N.V)^P4", clamp=True)
    L.new(base.outputs[0], fres.inputs[0]); L.new(p["Fresnel exponent"], fres.inputs[1])
    L.new(fres.outputs[0], go.inputs["Fresnel weight"])

    def lerp_boost(x, scale, mult, label):
        a = _math(N, "MULTIPLY", label=label + " = scale * x"); L.new(x, a.inputs[0]); L.new(scale, a.inputs[1])
        b = _math(N, "MULTIPLY", label="saturate(mult * " + label + ")", clamp=True)
        L.new(a.outputs[0], b.inputs[0]); L.new(mult, b.inputs[1])
        d = _math(N, "SUBTRACT"); L.new(b.outputs[0], d.inputs[0]); L.new(a.outputs[0], d.inputs[1])
        m = _math(N, "MULTIPLY_ADD", label="lerp by F")
        L.new(d.outputs[0], m.inputs[0]); L.new(fres.outputs[0], m.inputs[1]); L.new(a.outputs[0], m.inputs[2])
        return m

    sep = N.new("ShaderNodeSeparateColor"); L.new(p["_g RGB"], sep.inputs[0])
    gl = lerp_boost(sep.outputs[0], p["Gloss scale"], p["Gloss Fresnel multiplier"], "gloss")
    glc = _math(N, "MULTIPLY", None, 1.0, "saturate", clamp=True); L.new(gl.outputs[0], glc.inputs[0])
    gls = _math(N, "SQRT", label="gloss = sqrt(.)"); L.new(glc.outputs[0], gls.inputs[0])
    L.new(gls.outputs[0], go.inputs["Gloss"])
    sp = lerp_boost(sep.outputs[1], p["Specular scale"], p["Specular Fresnel multiplier"], "spec")
    cap = _math(N, "MINIMUM", label="min(cap, spec)"); L.new(sp.outputs[0], cap.inputs[0]); L.new(p["Specular cap"], cap.inputs[1])
    grey = N.new("ShaderNodeCombineColor"); grey.label = "grey F0"
    for i in range(3):
        L.new(cap.outputs[0], grey.inputs[i])
    L.new(grey.outputs[0], go.inputs["Specular F0"])

    tint = N.new("ShaderNodeMix"); tint.data_type = "RGBA"; tint.label = "lerp(P5, Fresnel tint, F)"
    rgba = lambda socks, name: next(s for s in socks if s.name == name and s.type == "RGBA")
    scal = N.new("ShaderNodeCombineColor")
    for i in range(3):
        L.new(p["Albedo scale"], scal.inputs[i])
    L.new(fres.outputs[0], tint.inputs["Factor"])
    L.new(scal.outputs[0], rgba(tint.inputs, "A"))
    rgba(tint.inputs, "B").default_value = (*SKIN_ALBEDO_FRESNEL_TINT, 1.0)
    alb = N.new("ShaderNodeMix"); alb.data_type = "RGBA"; alb.blend_type = "MULTIPLY"; alb.label = "saturate(_c * tint): UNORM MRT1"
    alb.name = "Skin albedo UNORM clamp"
    alb.inputs["Factor"].default_value = 1.0; alb.clamp_result = True
    L.new(p["_c RGB"], rgba(alb.inputs, "A")); L.new(rgba(tint.outputs, "Result"), rgba(alb.inputs, "B"))
    L.new(rgba(alb.outputs, "Result"), go.inputs["Albedo"])
    for i, n in enumerate(N):
        if n not in (gi, go):
            n.location = (-900 + 120 * (i % 12), -200 * (i // 12))
    return g


def apply_skin_emissive(mat, params, family="skin_emissive"):
    """Insert a skin-family G-buffer front-end into a convert_material()-ed material."""
    spec = SKIN_FAMILIES[family]
    grp = next(n for n in mat.node_tree.nodes if n.type == "GROUP" and n.node_tree.name.startswith(GROUP_NAME))
    nt, L = mat.node_tree, mat.node_tree.links
    c_col = grp.inputs[0].links[0].from_socket
    g_col = grp.inputs[2].links[0].from_socket
    for i in (2, 3):
        for link in list(grp.inputs[i].links):
            L.remove(link)
    skin = nt.nodes.new("ShaderNodeGroup")
    skin.node_tree = build_skin_emissive_group(family=family)
    skin.name = skin.label = spec["group"]
    skin.location = (grp.location.x - 300, grp.location.y + 250)
    L.new(c_col, skin.inputs["_c RGB"]); L.new(g_col, skin.inputs["_g RGB"])
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.name = "skin cached writer vertex normal"
    L.new(geo.outputs["Normal"], skin.inputs["Material normal"])
    for name, value in params.items():
        if name != "Self-lit K":
            skin.inputs[name].default_value = value
    for link in list(grp.inputs[0].links):
        L.remove(link)
    L.new(skin.outputs["Albedo"], grp.inputs[0])
    L.new(skin.outputs["Specular F0"], grp.inputs[2])
    L.new(skin.outputs["Gloss"], grp.inputs[3])
    grp.inputs[5].default_value = 1.0
    grp.inputs[6].default_value = 1.0
    grp.inputs[7].default_value = params.get("Self-lit K", 0.0)
    mat["rc_shader_frontend"] = "%s v%d (shader %s)" % (spec["group"], SKIN_GROUP_VERSION, spec["shader"])
    return skin


def _native_normal_xyz(nt, L, source, label):
    """x = 2R-1, y = 2G-1, z = 1-x*x-y*y as in the forward skin shaders."""
    sep = nt.nodes.new('ShaderNodeSeparateColor'); sep.name = label + ' RG'
    L.new(source, sep.inputs[0])
    xy=[]
    for ch in range(2):
        n=_math(nt.nodes,'MULTIPLY_ADD',None,2.0,label+' '+('x' if ch==0 else 'y'))
        n.inputs[2].default_value=-1.0; L.new(sep.outputs[ch],n.inputs[0]); xy.append(n.outputs[0])
    xx=_math(nt.nodes,'MULTIPLY'); L.new(xy[0],xx.inputs[0]); L.new(xy[0],xx.inputs[1])
    yy=_math(nt.nodes,'MULTIPLY'); L.new(xy[1],yy.inputs[0]); L.new(xy[1],yy.inputs[1])
    total=_math(nt.nodes,'ADD'); L.new(xx.outputs[0],total.inputs[0]); L.new(yy.outputs[0],total.inputs[1])
    z=_math(nt.nodes,'SUBTRACT',1.0,None,label+' z = 1-x*x-y*y'); L.new(total.outputs[0],z.inputs[1])
    return xy[0], xy[1], z.outputs[0]


def apply_native_skin_normal(mat, skin, secondary=None, blend=0.0, uv_scale=1.0):
    """Forward skin front-end normal (BED06840 / F0CD85FF).

    Single normal: normalize(x, y, 1-x*x-y*y) with x = 2R-1, y = 2G-1.
    With a secondary (detail) normal, F0CD85FF blends, before its final normalize:
        w = blend * _g.B
        N = (x1*z2 + w*z1*x2,  y1*z2 + w*z1*y2,  z1*z2)
    The secondary image is sampled at UV0 * uv_scale.  The shader's intermediate
    paraboloid re-projection is a uniform scale and cancels in the normalize.
    """
    nt, L = mat.node_tree, mat.node_tree.links
    brdf = next(n for n in nt.nodes if n.type == 'GROUP' and n.node_tree.name == GROUP_NAME)
    normal_map = brdf.inputs['Normal'].links[0].from_node
    if normal_map.type != 'NORMAL_MAP':
        raise ValueError('Native skin front-end requires the source normal image')
    source = normal_map.inputs['Color'].links[0].from_socket
    x1, y1, z1 = _native_normal_xyz(nt, L, source, 'Native skin normal')
    vec=nt.nodes.new('ShaderNodeCombineXYZ')
    if secondary is None:
        for j,s in enumerate((x1,y1,z1)): L.new(s,vec.inputs[j])
        mat['rc_shader_frontend_normal']='native RG z=1-x*x-y*y; secondary-normal weight zero'
    else:
        tc = nt.nodes.new('ShaderNodeTexCoord')
        uv = nt.nodes.new('ShaderNodeVectorMath'); uv.operation = 'SCALE'; uv.name = 'Secondary normal UV0 scale'
        uv.inputs['Scale'].default_value = float(uv_scale)
        L.new(tc.outputs['UV'], uv.inputs[0]); L.new(uv.outputs['Vector'], secondary.inputs['Vector'])
        x2, y2, z2 = _native_normal_xyz(nt, L, secondary.outputs['Color'], 'Native skin secondary normal')
        g = skin.inputs['_g RGB'].links[0].from_node
        gsep = nt.nodes.new('ShaderNodeSeparateColor'); gsep.name = 'Skin detail weight _g.B'
        L.new(g.outputs['Color'], gsep.inputs[0])
        w = _math(nt.nodes, 'MULTIPLY', None, float(blend), 'w = blend * _g.B'); w.name = 'Skin detail weight'
        L.new(gsep.outputs[2], w.inputs[0])
        wz1 = _math(nt.nodes, 'MULTIPLY', label='w*z1'); L.new(w.outputs[0], wz1.inputs[0]); L.new(z1, wz1.inputs[1])
        for j, (a, b) in enumerate(((x1, x2), (y1, y2))):
            m = _math(nt.nodes, 'MULTIPLY'); L.new(a, m.inputs[0]); L.new(z2, m.inputs[1])
            s = _math(nt.nodes, 'MULTIPLY_ADD', label='detail ' + 'xy'[j])
            L.new(wz1.outputs[0], s.inputs[0]); L.new(b, s.inputs[1]); L.new(m.outputs[0], s.inputs[2])
            L.new(s.outputs[0], vec.inputs[j])
        zz = _math(nt.nodes, 'MULTIPLY', label='z1*z2'); L.new(z1, zz.inputs[0]); L.new(z2, zz.inputs[1])
        L.new(zz.outputs[0], vec.inputs[2])
        mat['rc_shader_frontend_normal']=('F0CD85FF detail blend: w=%.6g*_g.B, secondary at UV0*%.6g' % (blend, uv_scale))
    norm=nt.nodes.new('ShaderNodeVectorMath'); norm.operation='NORMALIZE'; L.new(vec.outputs[0],norm.inputs[0])
    enc=nt.nodes.new('ShaderNodeVectorMath'); enc.operation='MULTIPLY_ADD'; enc.name='Native skin normal encoded'
    enc.inputs[1].default_value=(.5,.5,.5); enc.inputs[2].default_value=(.5,.5,.5); L.new(norm.outputs[0],enc.inputs[0])
    L.new(enc.outputs[0],normal_map.inputs['Color'])
    if skin is None:          # standard deferred families: no skin front-end to feed
        mat['rc_native_normal_frontend']='native RG z=1-x*x-y*y (close-range scale)'
        return
    L.new(normal_map.outputs['Normal'],skin.inputs['Material normal'])
    mat['rc_native_skin_frontend']='Forward skin normal and albedo/spec/gloss/self-lit front-end; forward lighting remains partial'


def _preflight_value(pre, key, table_index=None):
    """Instance value, else graph default, for a parameter key from preflight rows."""
    import struct
    for row in pre.get('parameter_tables', []):
        if row.get('key_hash') != key or (table_index is not None and row.get('table_index') != table_index):
            continue
        if 'interpreted_instance_value' in row:
            return row['interpreted_instance_value']
        if row.get('value_source') == 'material_instance' and len(row.get('instance_raw_hex', '')) == 8:
            return struct.unpack('<f', bytes.fromhex(row['instance_raw_hex']))[0]
        return row.get('graph_default')
    return None


# Standard deferred families decoded from their cached G-buffer writers (2026-10-06,
# Other models/enm_blarg_space_commando/evidence/DEFERRED_FAMILIES_DECODE.md).  Parameters
# are looked up by key: the incandescence writers read the same keys through two different
# CB layouts.  g_mode "grey_rg": gloss = sqrt(sat(gloss*_g.R)), F0 = grey spec*_g.G.
# g_mode "colored_alpha": the generic group as is (F0 = spec*_g.rgb, gloss = sqrt(sat(gloss*_g.a^2))).
DEFERRED_FAMILIES = {
    "basic_normal_gloss": dict(shader="0x5ebdeca4", g_mode="grey_rg", self_lit=None),
    "basic_normal_coloredgloss_emissive": dict(shader="0xc4828176", g_mode="colored_alpha",
                                               self_lit=("c_alpha", 0x06F19114)),
    "basic_incandescence": dict(shader="0x5dbcccb7/0x42254271", g_mode="grey_rg",
                                self_lit=("g_blue", 0x5FD56522), pulse_key=0x41E7FE54),
    # Native GCN decode (no cached SPIR-V): Other models/enm_telepathipus_v2/evidence/CSECIA_DECODE.md
    "basic_colored_spec_emmisive_in_c_alpha": dict(shader="0x89638dfb", g_mode="colored_alpha",
                                                   self_lit=("c_alpha", 0xF8C61683), pulse_key=0xAF5BEBBC,
                                                   unmodelled_nonzero=(0x173F37E2,), source="native GCN"),
}
DEFERRED_SPECULAR_SCALE_KEY = 0x62835AF9
DEFERRED_GLOSS_SCALE_KEY = 0x24F4CA4C


def _preflight_key_value(pre, key):
    """One value for a key; every table holding the key must agree."""
    values = {round(float(v), 9) for v in
              (_preflight_value(pre, key, row['table_index']) for row in pre.get('parameter_tables', [])
               if row.get('key_hash') == key) if v is not None}
    if len(values) != 1:
        raise ValueError('Parameter 0x%08X is missing or differs between CB tables: %s' % (key, sorted(values)))
    return values.pop()


def apply_deferred_family(mat, pre, family):
    """Standard deferred front-end for a registered non-skin family."""
    spec = DEFERRED_FAMILIES[family]
    params = {"Specular scale": _preflight_key_value(pre, DEFERRED_SPECULAR_SCALE_KEY),
              "Gloss scale": _preflight_key_value(pre, DEFERRED_GLOSS_SCALE_KEY), "Self-lit K": 0.0}
    limitation = None
    if spec["self_lit"]:
        params["Self-lit K"] = _preflight_key_value(pre, spec["self_lit"][1])
    if spec.get("pulse_key") is not None:
        params["Pulse rate"] = _preflight_key_value(pre, spec["pulse_key"])
        if params["Pulse rate"] != 0.0:
            limitation = "Self-lit pulse rate %.6g is time-driven; shown at its peak (pulse = 1)" % params["Pulse rate"]
    for key in spec.get("unmodelled_nonzero", ()):
        value = _preflight_key_value(pre, key)
        params["0x%08X" % key] = value
        if value != 0.0:
            limitation = ((limitation + "; ") if limitation else "") +                 "Key 0x%08X = %.6g enables an unmodelled writer branch" % (key, value)
    tex = _find_textures(mat)
    if 'c' not in tex or 'g' not in tex:
        return False
    convert_material(mat, emissive_intensity=params["Self-lit K"], specular_scale=params["Specular scale"],
                     gloss_scale=params["Gloss scale"],
                     g_is_srgb=tex['g'].image.colorspace_settings.name == 'sRGB')
    nt, L = mat.node_tree, mat.node_tree.links
    grp = next(n for n in nt.nodes if n.type == 'GROUP' and n.node_tree.name == GROUP_NAME)
    g = tex['g']
    if spec["g_mode"] == "grey_rg" or (spec["self_lit"] and spec["self_lit"][0] == "g_blue"):
        sep = nt.nodes.new('ShaderNodeSeparateColor'); sep.name = sep.label = 'Deferred _g channels'
        L.new(g.outputs['Color'], sep.inputs[0])
    if spec["g_mode"] == "grey_rg":
        for i in (2, 3):
            for link in list(grp.inputs[i].links):
                L.remove(link)
        grey = nt.nodes.new('ShaderNodeCombineColor'); grey.name = grey.label = 'Deferred grey F0 = _g.G'
        for i in range(3):
            L.new(sep.outputs[1], grey.inputs[i])
        root = _math(nt.nodes, 'SQRT', label='sqrt(_g.R): group squares it back'); root.name = 'Deferred gloss sqrt(_g.R)'
        L.new(sep.outputs[0], root.inputs[0])
        L.new(grey.outputs[0], grp.inputs[2]); L.new(root.outputs[0], grp.inputs[3])
    if spec["self_lit"] and spec["self_lit"][0] == "g_blue":
        for link in list(grp.inputs[1].links):
            L.remove(link)
        L.new(sep.outputs[2], grp.inputs[1])
    elif not spec["self_lit"]:
        for link in list(grp.inputs[1].links):
            L.remove(link)
    if 'n' in tex:
        apply_native_skin_normal(mat, None)
    mat['rc_deferred_family'] = family
    mat['rc_deferred_params_json'] = json.dumps(params, sort_keys=True)
    mat['rc_shader_preflight'] = json.dumps(pre, sort_keys=True)
    mat['rc_shader_frontend'] = 'cached deferred writer %s (%s)' % (spec["shader"], spec["g_mode"])
    mat['rc_shader_evidence'] = 'Hash-bound deferred writer front-end decoded from %s; shared deferred BRDF' %         spec.get("source", "cached SPIR-V")
    if limitation:
        mat['rc_shader_frontend_limitation'] = limitation
    mat['game_equivalence'] = False
    return True


GLASS_GROUP_NAME = "RC PS4 character_glass forward"
GLASS_GROUP_VERSION = 3


def _glass_value(pre, key):
    """Instance value, else graph default; scalar or vec3. All tables holding the key must agree."""
    import struct
    found = []
    for row in pre.get('parameter_tables', []):
        if row.get('key_hash') != key:
            continue
        v = row.get('interpreted_instance_value')
        if v is None and row.get('value_source') == 'material_instance':
            raw = bytes.fromhex(row.get('instance_raw_hex', ''))
            v = list(struct.unpack('<%df' % (len(raw) // 4), raw)) if raw else None
        if v is None:
            v = row.get('graph_default')
        if isinstance(v, list) and len(v) == 1:
            v = v[0]
        found.append(json.dumps(v))
    if not found or len(set(found)) != 1 or json.loads(found[0]) is None:
        raise ValueError('character_glass parameter 0x%08X missing or inconsistent: %s' % (key, found))
    return json.loads(found[0])


def _captured_ambient_radiance():
    """E constant: captured Veldin ambient radiance c0 when the view data exists, else black."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'assets', 'game_view', 'rc_game_view.json')
    try:
        with open(path, encoding='utf-8') as f:
            return [float(x) for x in json.load(f)['capture_ambient']['blender_world']['radiance_c0']]
    except Exception:
        return [0.0, 0.0, 0.0]


def build_character_glass_group(force=False):
    """Native 34D5B21C front-end: Fresnel-curve alpha, tinted reflection, faint lit body colour.

    out = Transparent(1 - alpha*A) + alpha * (Diffuse(B*(1 + k*F*T*E)) + GGX(tint, rough))
    alpha = (1.001-N.V)^P8 * (P10 + sat(P11 + sat(4*(1-N.V)^P13)))   (instance fade 0, attr0.w 1)
    F = (1.001-N.V)^P12,  T = sat(4*sqrt(1-N.V)).  A = "Attenuate background": 0 = additive
    (SRC_ALPHA, ONE), 1 = alpha-over (SRC_ALPHA, ONE_MINUS_SRC_ALPHA); set from the material's flags
    word (rc2016.pipeline.render_state), never from the graph. Back faces are culled (flags bit 2 clear):
    without the cull, back faces have N.V < 0, so alpha -> 1 and they block the view under alpha-over.
    """
    g = bpy.data.node_groups.get(GLASS_GROUP_NAME)
    if g is not None and not force and g.get("rc_version") == GLASS_GROUP_VERSION:
        return g
    if g is None:
        g = bpy.data.node_groups.new(GLASS_GROUP_NAME, "ShaderNodeTree")
    g.nodes.clear(); g.interface.clear()
    g["rc_version"] = GLASS_GROUP_VERSION
    g["rc_source"] = "character_glass.materialgraph native forward pixel shader 0x34D5B21C"
    for name, kind, default in (("Body colour B", "NodeSocketColor", (0, 0, 0, 1)),
                                ("Reflection tint", "NodeSocketColor", (1, 1, 1, 1)),
                                ("Roughness", "NodeSocketFloat", 0.15),
                                ("Env probe radiance E", "NodeSocketColor", (0, 0, 0, 1)),
                                ("Alpha exponent P8", "NodeSocketFloat", 2.0), ("Env scale P9", "NodeSocketFloat", 0.3),
                                ("Alpha bias P10", "NodeSocketFloat", 0.15), ("Rim bias P11", "NodeSocketFloat", 0.25),
                                ("Env exponent P12", "NodeSocketFloat", 2.0), ("Rim exponent P13", "NodeSocketFloat", 3.245),
                                ("Attenuate background", "NodeSocketFloat", 1.0)):
        _sock(g, name, kind, "INPUT", default)
    _sock(g, "Shader", "NodeSocketShader", "OUTPUT")
    _sock(g, "Alpha", "NodeSocketFloat", "OUTPUT")
    N, L = g.nodes, g.links
    gi = N.new("NodeGroupInput"); go = N.new("NodeGroupOutput")
    p = {s.name: gi.outputs[s.name] for s in g.interface.items_tree if s.in_out == "INPUT"}
    geo = N.new("ShaderNodeNewGeometry")
    dot = N.new("ShaderNodeVectorMath"); dot.operation = "DOT_PRODUCT"; dot.label = "N.V"
    L.new(geo.outputs["Normal"], dot.inputs[0]); L.new(geo.outputs["Incoming"], dot.inputs[1])
    ndv = _math(N, "MAXIMUM", None, 0.0, "sat(max(0, N.V))", clamp=True); L.new(dot.outputs["Value"], ndv.inputs[0])
    om = _math(N, "SUBTRACT", 1.0, None, "1 - N.V"); L.new(ndv.outputs[0], om.inputs[1])
    om1 = _math(N, "SUBTRACT", 1.001, None, "1.001 - N.V"); L.new(ndv.outputs[0], om1.inputs[1])
    def pw(base, exp_sock, label):
        n = _math(N, "POWER", label=label); L.new(base.outputs[0], n.inputs[0]); L.new(exp_sock, n.inputs[1]); return n
    rim = pw(om, p["Rim exponent P13"], "(1-N.V)^P13")
    rim4 = _math(N, "MULTIPLY", None, 4.0, "sat(4*.)", clamp=True); L.new(rim.outputs[0], rim4.inputs[0])
    rb = _math(N, "ADD", label="sat(P11 + .)", clamp=True); L.new(p["Rim bias P11"], rb.inputs[0]); L.new(rim4.outputs[0], rb.inputs[1])
    ab = _math(N, "ADD", label="P10 + ."); L.new(p["Alpha bias P10"], ab.inputs[0]); L.new(rb.outputs[0], ab.inputs[1])
    ae = pw(om1, p["Alpha exponent P8"], "(1.001-N.V)^P8")
    alpha = _math(N, "MULTIPLY", label="Glass alpha", clamp=True); alpha.name = "Glass alpha"
    L.new(ae.outputs[0], alpha.inputs[0]); L.new(ab.outputs[0], alpha.inputs[1])
    F = pw(om1, p["Env exponent P12"], "F = (1.001-N.V)^P12")
    sq = _math(N, "SQRT"); L.new(om.outputs[0], sq.inputs[0])
    T = _math(N, "MULTIPLY", None, 4.0, "T = sat(4*sqrt(1-N.V))", clamp=True); L.new(sq.outputs[0], T.inputs[0])
    kf = _math(N, "MULTIPLY"); L.new(p["Env scale P9"], kf.inputs[0]); L.new(F.outputs[0], kf.inputs[1])
    kft = _math(N, "MULTIPLY", label="k*F*T"); L.new(kf.outputs[0], kft.inputs[0]); L.new(T.outputs[0], kft.inputs[1])
    ke = N.new("ShaderNodeVectorMath"); ke.operation = "MULTIPLY_ADD"; ke.label = "1 + k*F*T*E"
    L.new(p["Env probe radiance E"], ke.inputs[0]); L.new(kft.outputs[0], ke.inputs[1]); ke.inputs[2].default_value = (1, 1, 1)
    body = N.new("ShaderNodeVectorMath"); body.operation = "MULTIPLY"; body.label = "X = B*(1 + k*F*T*E)"
    L.new(p["Body colour B"], body.inputs[0]); L.new(ke.outputs[0], body.inputs[1])
    diff = N.new("ShaderNodeBsdfDiffuse"); diff.name = diff.label = "Glass body X*L"
    L.new(body.outputs[0], diff.inputs["Color"])
    gloss = N.new("ShaderNodeBsdfGlossy"); gloss.name = gloss.label = "Glass tinted reflection R"
    gloss.distribution = "GGX"
    L.new(p["Reflection tint"], gloss.inputs["Color"]); L.new(p["Roughness"], gloss.inputs["Roughness"])
    lit = N.new("ShaderNodeAddShader"); L.new(diff.outputs[0], lit.inputs[0]); L.new(gloss.outputs[0], lit.inputs[1])
    weighted = N.new("ShaderNodeMixShader"); weighted.name = weighted.label = "SRC_ALPHA weight"
    L.new(alpha.outputs[0], weighted.inputs[0]); L.new(lit.outputs[0], weighted.inputs[2])
    att = _math(N, "MULTIPLY", label="alpha * A"); L.new(alpha.outputs[0], att.inputs[0])
    L.new(p["Attenuate background"], att.inputs[1])
    keep = _math(N, "SUBTRACT", 1.0, None, "1 - alpha*A"); L.new(att.outputs[0], keep.inputs[1])
    clear = N.new("ShaderNodeBsdfTransparent"); clear.name = clear.label = "Background (1 - alpha*A)"
    L.new(keep.outputs[0], clear.inputs["Color"])
    out = N.new("ShaderNodeAddShader"); out.name = out.label = "Blend over background"
    L.new(clear.outputs[0], out.inputs[0]); L.new(weighted.outputs[0], out.inputs[1])
    culled = N.new("ShaderNodeBsdfTransparent"); culled.name = culled.label = "Culled back face"
    cull = N.new("ShaderNodeMixShader"); cull.name = cull.label = "Cull back faces"
    L.new(geo.outputs["Backfacing"], cull.inputs[0]); L.new(out.outputs[0], cull.inputs[1])
    L.new(culled.outputs[0], cull.inputs[2])
    L.new(cull.outputs[0], go.inputs["Shader"]); L.new(alpha.outputs[0], go.inputs["Alpha"])
    for i, n in enumerate(N):
        n.location = (-1000 + 160 * (i % 10), -180 * (i // 10))
    return g


GLASS_BLEND_BY_FLAGS = {1: ("additive_alpha", 0.0), 2: ("alpha_over", 1.0)}   # flags & 3 -> (blend, A)


def character_glass_blend(material_path):
    """Blend of a character_glass material from its .material flags word; fails closed."""
    from rc2016.pipeline import render_state
    flags = render_state.material_flags(material_path)
    if flags is None or (flags & 3) not in GLASS_BLEND_BY_FLAGS or flags & 4:
        raise ValueError('character_glass blend unresolved for %s (flags %s)' % (material_path, flags))
    return flags, GLASS_BLEND_BY_FLAGS[flags & 3]


def apply_character_glass(mat, pre, material_path):
    """Replace a material's surface with the decoded character_glass front-end."""
    from rc2016.shaders.families import CHARACTER_GLASS_KEYS as K
    v = {name: _glass_value(pre, key) for name, key in K.items()}
    B, tint = [float(x) for x in v["body_colour"]], [float(x) for x in v["reflection_tint"]]
    Y = 0.2126 * tint[0] + 0.7152 * tint[1] + 0.0722 * tint[2]
    sat = lambda x: min(1.0, max(0.0, x))
    refl = [sat(float(v["reflection_intensity"]) * sat(c / Y)) if Y > 0 else 0.0 for c in tint]
    gloss = math.sqrt(sat(max(0.0, float(v["gloss"]))))
    rough = 2.0 ** (-2.75 * gloss)
    E = _captured_ambient_radiance()
    flags, (blend, attenuate) = character_glass_blend(material_path)
    has_backup = any(m.get("rc_backup_of") == mat.name for m in bpy.data.materials)
    if not has_backup:
        backup = mat.copy(); backup.name = mat.name + BACKUP_SUFFIX; backup.use_fake_user = True
        backup["rc_backup_of"] = mat.name
    nt = mat.node_tree
    for node in list(nt.nodes):
        if node.type != "TEX_IMAGE":
            nt.nodes.remove(node)
    grp = nt.nodes.new("ShaderNodeGroup"); grp.node_tree = build_character_glass_group()
    grp.name = grp.label = GLASS_GROUP_NAME
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.is_active_output = True; out.location = (400, 0)
    nt.links.new(grp.outputs["Shader"], out.inputs["Surface"])
    grp.inputs["Body colour B"].default_value = (*B, 1.0)
    grp.inputs["Reflection tint"].default_value = (*refl, 1.0)
    grp.inputs["Roughness"].default_value = rough
    grp.inputs["Env probe radiance E"].default_value = (*E, 1.0)
    for sock, name in (("Alpha exponent P8", "alpha_exponent"), ("Env scale P9", "env_scale"),
                       ("Alpha bias P10", "alpha_bias"), ("Rim bias P11", "rim_bias"),
                       ("Env exponent P12", "env_exponent"), ("Rim exponent P13", "rim_exponent")):
        grp.inputs[sock].default_value = float(v[name])
    grp.inputs["Attenuate background"].default_value = attenuate
    mat.use_backface_culling = True                    # flags bit 2 clear: back faces culled
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    params = dict(v, reflection_tint_normalised=refl, gloss_effective=gloss, blender_roughness=rough, env_probe_radiance_E=E)
    mat["rc_glass_family"] = "character_glass"
    mat["rc_glass_blend"] = blend
    mat["rc_material_flags"] = "0x%08X" % flags
    mat["rc_glass_params_json"] = json.dumps(params, sort_keys=True)
    mat["rc_shader_preflight"] = json.dumps(pre, sort_keys=True)
    mat["rc_shader"] = "%s v%d" % (GLASS_GROUP_NAME, GLASS_GROUP_VERSION)
    mat["rc_shader_frontend"] = "native forward pixel shader 0x34D5B21C (straight-line slices)"
    mat["rc_shader_frontend_limitation"] = ("Blend %s from material flags 0x%08X; back faces culled; instance fade 0, " % (blend, flags) +
                                            "attr0.w 1; engine reflections as a tinted GGX lobe; E constant; no fog")
    mat["game_equivalence"] = False
    return True


class _ViewerNodes:
    """Node builder for the viewer-rule front-end. Constants are named 'VR <term> <param>' so the
    reopen validator can compare them with the source rule."""

    def __init__(self, nt):
        self.nt, self.N, self.L = nt, nt.nodes, nt.links
        self._geo = self._uv = self._tc = self._onrm = self._split = None
        self._consts = {}

    def _set(self, sock, value):
        if value is None:
            return
        if isinstance(value, bpy.types.NodeSocket):
            self.L.new(value, sock)
        elif isinstance(value, (tuple, list)):
            sock.default_value = tuple(float(x) for x in value)[:len(sock.default_value)]
        else:
            sock.default_value = float(value)

    def m(self, op, a, b=None, c=None, clamp=False, label=None):
        n = self.N.new('ShaderNodeMath'); n.operation = op; n.use_clamp = clamp
        if label:
            n.label = label
        for i, x in enumerate((a, b, c)):
            self._set(n.inputs[i], x)
        return n.outputs[0]

    def v(self, op, a, b=None, c=None, label=None):
        n = self.N.new('ShaderNodeVectorMath'); n.operation = op
        if label:
            n.label = label
        vec = lambda x: (float(x),) * 3 if isinstance(x, (int, float)) else x
        if op == 'SCALE':
            self._set(n.inputs[0], vec(a)); self._set(n.inputs['Scale'], b)
        else:
            for i, x in enumerate((a, b, c)):
                self._set(n.inputs[i], vec(x))
        return n.outputs['Value'] if op in ('DOT_PRODUCT', 'LENGTH', 'DISTANCE') else n.outputs['Vector']

    def const(self, name, value):
        if name not in self._consts:
            n = self.N.new('ShaderNodeValue'); n.name = n.label = 'VR ' + name
            n.outputs[0].default_value = float(value); self._consts[name] = n.outputs[0]
        return self._consts[name]

    def const3(self, name, value):
        if name not in self._consts:
            n = self.N.new('ShaderNodeCombineXYZ'); n.name = n.label = 'VR ' + name
            for i in range(3):
                n.inputs[i].default_value = float(value[i])
            self._consts[name] = n.outputs[0]
        return self._consts[name]

    def sep(self, vec):
        n = self.N.new('ShaderNodeSeparateXYZ'); self._set(n.inputs[0], vec)
        return n.outputs[0], n.outputs[1], n.outputs[2]

    def comb(self, x, y, z=0.0):
        n = self.N.new('ShaderNodeCombineXYZ')
        for i, s in enumerate((x, y, z)):
            self._set(n.inputs[i], s)
        return n.outputs[0]

    def sat(self, x):
        return self.m('ADD', x, 0.0, clamp=True)

    def vsat(self, vec):
        return self.v('MINIMUM', self.v('MAXIMUM', vec, 0.0), 1.0)

    def lerp(self, a, b, t):
        return self.m('MULTIPLY_ADD', self.m('SUBTRACT', b, a), t, a)

    def vlerp(self, a, b, t):
        return self.v('MULTIPLY_ADD', self.v('SUBTRACT', b, a), t, a)

    def ge(self, a, b):
        """a >= b as 0/1."""
        return self.m('SUBTRACT', 1.0, self.m('LESS_THAN', a, b))

    def geo(self):
        if self._geo is None:
            self._geo = self.N.new('ShaderNodeNewGeometry'); self._geo.name = 'VR geometry'
        return self._geo

    def uv(self):
        if self._uv is None:
            self._uv = self.N.new('ShaderNodeUVMap'); self._uv.uv_map = 'UV0'; self._uv.name = 'VR UV0'
        return self._uv.outputs['UV']

    def uv_split(self):
        if self._split is None:
            self._split = self.sep(self.uv())
        return self._split

    def native_position(self):
        """Object space = native Y-up mesh space (the presentation basis is the object transform)."""
        if self._tc is None:
            self._tc = self.N.new('ShaderNodeTexCoord'); self._tc.name = 'VR native position (object space)'
        return self._tc.outputs['Object']

    def native_normal(self):
        if self._onrm is None:
            t = self.N.new('ShaderNodeVectorTransform'); t.name = 'VR native geometry normal'
            t.vector_type = 'NORMAL'; t.convert_from = 'WORLD'; t.convert_to = 'OBJECT'
            self._set(t.inputs[0], self.geo().outputs['Normal'])
            self._onrm = self.v('NORMALIZE', t.outputs[0])
        return self._onrm

    def game_uv(self, su=1.0, sv=1.0, du=0.0, dv=0.0):
        """Blender UV for game coordinates (su*u + du, sv*v + dv); Blender stores v_b = 1 - v."""
        u, vb, _ = self.uv_split()
        uu = self.m('MULTIPLY_ADD', u, su, du) if (su != 1.0 or du != 0.0) else u
        # 1 - (sv*(1 - vb) + dv) = sv*vb + (1 - sv - dv)
        vv = vb if (sv == 1.0 and dv == 0.0) else self.m('MULTIPLY_ADD', vb, sv, self.m('SUBTRACT', self.m('SUBTRACT', 1.0, sv), dv))
        return self.comb(uu, vv)

    def gl_coord(self, s, t):
        """Blender image vector for GL texture coordinates (s, t), t counted from the top row."""
        return self.comb(s, self.m('SUBTRACT', 1.0, t))

    def tex(self, template, vector, label):
        n = self.N.new('ShaderNodeTexImage'); n.image = template.image
        n.interpolation = template.interpolation; n.extension = 'REPEAT'
        for key in template.keys():
            n[key] = template[key]
        n.name = n.label = 'VR ' + label
        self._set(n.inputs['Vector'], vector)
        return n

    def tangent_normal(self, x, y, z, label):
        """World normal from tangent-space x, y, z (the existing no-green-flip convention)."""
        norm = self.v('NORMALIZE', self.comb(x, y, z))
        enc = self.v('MULTIPLY_ADD', norm, (.5, .5, .5), (.5, .5, .5))
        nm = self.N.new('ShaderNodeNormalMap'); nm.name = nm.label = label
        nm.inputs['Strength'].default_value = 1.0
        self._set(nm.inputs['Color'], enc)
        return nm.outputs['Normal']

    def rg(self, color, scale=None):
        """x, y = 2*RG - 1 (optionally times a scalar)."""
        r, g, _ = self.sep(color)
        x = self.m('MULTIPLY_ADD', r, 2.0, -1.0); y = self.m('MULTIPLY_ADD', g, 2.0, -1.0)
        if scale is not None:
            x, y = self.m('MULTIPLY', x, scale), self.m('MULTIPLY', y, scale)
        return x, y

    def dot2(self, x, y):
        return self.m('MULTIPLY_ADD', x, x, self.m('MULTIPLY', y, y))


VIEWER_RULE_FRONTEND_VERSION = 4


def _viewer_rule_images(nt):
    images = {}
    for node in nt.nodes:
        if node.type == 'TEX_IMAGE' and node.image is not None:
            path = str(node.get('source_texture_path', '')).lower()
            if path and (path not in images or (images[path].get('source_graph_fallback') and not node.get('source_graph_fallback'))):
                images[path] = node
    return images


def _viewer_rule_screen(mat, rule, img):
    """mo_neb_amb_screen_01 forward 8716DC7A at t=0 (gl_shading.GLASS_FRAGMENT uKind 3)."""
    nt = mat.node_tree; P = _ViewerNodes(nt)
    f = rule['terms']['screen']; p = f['params']
    c, n, pick, g = img('_c'), img('_n'), img('_pick'), img('_g')
    grid = P.const('screen grid', p['grid'])
    u, vb, _ = P.uv_split()
    cell = lambda x: P.m('DIVIDE', P.m('FLOOR', P.m('MULTIPLY', x, grid)), grid)
    h = P.sep(P.tex(n, P.gl_coord(cell(u), cell(P.m('SUBTRACT', 1.0, vb))), 'screen grid _n').outputs['Color'])[0] if n is not None else 0.0
    base = c.outputs['Alpha']
    alpha = P.sat(P.m('MULTIPLY_ADD', P.m('MULTIPLY', P.const('screen alpha base', p['alpha_base']), base),
                      P.m('SUBTRACT', 1.0, h), P.m('MULTIPLY', P.const('screen mask strength', p['mask_strength']), h)))
    tint = P.vlerp(P.const3('screen colour B', p['colour_b']), P.const3('screen colour A', p['colour_a']), h)
    na = P.sep(pick.outputs['Color'])[2] if pick is not None else 0.0
    nb = P.sep(g.outputs['Color'])[2] if g is not None else 0.0
    keep = P.v('MULTIPLY', P.v('SUBTRACT', 1.0, tint), P.m('MULTIPLY', P.m('SUBTRACT', 1.0, na), P.m('SUBTRACT', 1.0, nb)))
    gain = P.m('ADD', 1.0, P.lerp(P.const('screen intensity B', p['intensity_b']), P.const('screen intensity A', p['intensity_a']), h))
    colour = P.v('SCALE', P.v('SUBTRACT', 1.0, keep), gain)
    em = P.N.new('ShaderNodeEmission'); em.name = em.label = 'VR screen colour (unlit)'
    clear = P.N.new('ShaderNodeBsdfTransparent'); clear.name = 'VR screen background'
    blend = f.get('blend')
    if blend == 'additive':
        P.L.new(colour, em.inputs['Color']); P.L.new(alpha, em.inputs['Strength'])
        shader = P.N.new('ShaderNodeAddShader'); P.L.new(clear.outputs[0], shader.inputs[0]); P.L.new(em.outputs[0], shader.inputs[1])
    elif blend == 'opaque':
        P.L.new(colour, em.inputs['Color']); shader = em
    else:   # alpha_over (mode 3 never captured: the viewer falls back to alpha-over too)
        P.L.new(colour, em.inputs['Color'])
        shader = P.N.new('ShaderNodeMixShader'); P.L.new(alpha, shader.inputs[0])
        P.L.new(clear.outputs[0], shader.inputs[1]); P.L.new(em.outputs[0], shader.inputs[2])
    shader.name = shader.label = 'VR screen blend %s' % blend
    if not f.get('two_sided'):
        culled = P.N.new('ShaderNodeBsdfTransparent'); culled.name = 'VR culled back face'
        cull = P.N.new('ShaderNodeMixShader'); cull.name = cull.label = 'VR cull back faces'
        P.L.new(P.geo().outputs['Backfacing'], cull.inputs[0]); P.L.new(shader.outputs[0], cull.inputs[1])
        P.L.new(culled.outputs[0], cull.inputs[2]); shader = cull
    out = P.N.new('ShaderNodeOutputMaterial'); out.is_active_output = True
    P.L.new(shader.outputs[0], out.inputs['Surface'])
    mat.use_backface_culling = not f.get('two_sided')
    if hasattr(mat, 'surface_render_method') and blend != 'opaque':
        mat.surface_render_method = 'BLENDED'
    return ['Forward unlit screen at t=0: the viewer tonemaps each fragment before blending, Cycles blends scene-linear '
            'emission before the view transform; cutoff 0, vertex alpha and fog fade 1']


def apply_viewer_graph_rule(mat, source):
    """The asset viewer's per-graph reading on the shared deferred BRDF, for graphs without a
    registered family (pipeline materials.viewer_graph_rule; viewer gl_shading.py Shaded mode).

    g_mode 'grey': F0 = grey spec*_g.G, gloss = sqrt(sat(gloss*_g.R)); 'grey_skin': F0 = grey
    min(0.5, _g.G), gloss = sqrt(sat(_g.R)); 'vehicle': packed constants; otherwise coloured
    F0 = spec*_g.rgb, gloss = sqrt(sat(gloss*_g.a^2)) (the group's own reading). No _g: no
    specular, as in the viewer. Self-lit = _c.rgb * (_g.B or _c.a) * K with the viewer's
    effective K. Normal: x,y = 2RG-1 with the viewer's z.

    Every term in rule['ported'] is built with the same math, order and frozen values as
    gl_shading.FRAGMENT (colour pick, colour tint and its random/detail/world/world-normal
    variants, specular tint, arch emissive sequence, vehicle palettes, glow masks, two-colour
    metal, ground puddles, foliage/za normal z, za rim suppression, rock blend, basic_normal_gloss
    variant tint/rim/self-lit terms and detail normals, leaf cut-out) or GLASS_FRAGMENT (screen). Constants are
    named 'VR <term> <param>' nodes. Terms in rule['unported'] are recorded on the material.
    Returns False when the rule does not apply or its anchor image is not in the material.
    """
    rule = source.get('viewer_graph_rule') or {}
    if not rule.get('applies'):
        return False
    nt, L = mat.node_tree, mat.node_tree.links
    images = _viewer_rule_images(nt)
    paths = rule.get('textures') or {}

    def img(role):
        path = str(paths.get(role) or '').lower()
        return images.get(path) if path else None

    ported = list(rule.get('ported') or ())
    terms = rule.get('terms') or {}
    c, g, n = img('_c'), img('_g'), img('_n')
    arch = terms.get('arch_sequence') if 'arch_sequence' in ported else None
    screen = 'screen' in ported
    facing = terms.get('facing_glow') if 'facing_glow' in ported else None   # 0CD2AAFB samples no texture
    if screen and c is None or not screen and terms.get('screen'):
        return False
    if c is None and not (arch and g is not None) and not facing:
        return False
    for node in list(nt.nodes):   # keep every source image node (provenance and coverage reports)
        if node.type != 'TEX_IMAGE':
            nt.nodes.remove(node)
    for node in images.values():
        node.image.alpha_mode = 'CHANNEL_PACKED'   # alpha is data; colourspaces stay native
    limits = []
    if screen:
        limits += _viewer_rule_screen(mat, rule, img)
        _finish_viewer_rule(mat, rule, 'forward screen 8716DC7A (t=0, unlit, %s)' % terms['screen'].get('blend'), limits)
        return True
    P = _ViewerNodes(nt)
    grp = nt.nodes.new('ShaderNodeGroup'); grp.node_tree = build_group()
    grp.name = grp.label = GROUP_NAME; grp.location = (500, 0)
    out = nt.nodes.new('ShaderNodeOutputMaterial'); out.location = (800, 0); out.is_active_output = True
    L.new(grp.outputs[0], out.inputs['Surface'])
    on = lambda key: key in ported and key in terms
    mode = rule.get('g_mode', 'colored')
    spec, gloss_scale = float(rule.get('spec', 1.0)), float(rule.get('gloss', 1.0))
    k = float(rule.get('k', 0.0))
    geo = P.geo(); V = geo.outputs['Incoming']; Ng = geo.outputs['Normal']
    view_terms = []
    albedo = c.outputs['Color'] if c is not None else (0.0, 0.0, 0.0)
    if facing:   # no albedo input: v76-v78 stay 1.0
        albedo = P.const3('facing glow albedo', facing['albedo'])
    g_rgb = g.outputs['Color'] if g is not None else None
    gR, gG, gB = P.sep(g_rgb) if g is not None else (0.0, 0.0, 0.0)
    f0 = gloss_in = None          # BRDF F0 colour / gloss input (the group squares gloss and scales it)
    self_mask = None              # BRDF self-lit mask when a term replaces the generic one
    normal = None
    bng = terms.get('bng') or {}
    kind = int(bng.get('kind', 0)) if ('bng' in ported or 'bng_detail' in ported) else 0
    kind_terms = 'bng' in ported      # tint/rim/self-lit terms of kinds 1/3/4/6
    tint = terms.get('tint') if on('tint') else None
    variant = (tint or {}).get('variant')
    za_w = None
    if on('za_fresnel_power'):    # E08357DC: geometry-normal rim suppression of spec and gloss
        ndv = P.sat(P.v('DOT_PRODUCT', Ng, V))
        za_w = P.m('SUBTRACT', 1.0, P.m('POWER', P.m('SUBTRACT', 1.00100004673, ndv), P.const('za fresnel power', terms['za_fresnel_power']), clamp=True))
        view_terms.append('za')

    if on('puddles'):             # DB75867B material terms (forward lighting composition not reproduced)
        pp = terms['puddles']['params']
        q = [P.const('puddle p%02d' % i, x) for i, x in enumerate(pp)]
        wx, wy, wz = P.sep(P.v('SCALE', P.native_position(), -1.0))
        qx, qz = P.m('SUBTRACT', wx, .5), P.m('SUBTRACT', wz, .5)
        sn, cn = P.m('SINE', q[14]), P.m('COSINE', q[14])
        nu = P.m('MULTIPLY', q[10], P.m('ADD', .5, P.m('SUBTRACT', P.m('MULTIPLY', cn, qx), P.m('MULTIPLY', sn, qz))))
        nv = P.m('MULTIPLY', q[10], P.m('ADD', .5, P.m('ADD', P.m('MULTIPLY', sn, qx), P.m('MULTIPLY', cn, qz))))
        noise = P.sep(P.tex(img('_puddle_noise'), P.gl_coord(nu, nv), 'puddle noise').outputs['Color'])[0]
        pm = P.sat(P.m('DIVIDE', P.m('SUBTRACT', noise, q[0]), P.m('SUBTRACT', q[6], q[0])))
        pr = P.sat(P.m('DIVIDE', P.m('SUBTRACT', noise, q[2]), P.m('SUBTRACT', q[3], q[2])))
        pb = P.m('MULTIPLY_ADD', q[9], P.m('MULTIPLY_ADD', pr, -2.0, 1.0), pr)
        gloss_in = P.m('SQRT', P.sat(P.m('MULTIPLY_ADD', P.m('MULTIPLY', q[8], gR), P.m('SUBTRACT', 1.0, pb), pb)))
        f0v = P.m('MULTIPLY', gG, P.sat(P.m('MULTIPLY_ADD', q[4], pm, .30000001192092896)))
        f0 = P.comb(f0v, f0v, f0v)
        albedo = P.v('SCALE', albedo, P.m('SUBTRACT', 1.0, P.m('MULTIPLY', P.m('MULTIPLY', noise, q[11]), pm)))
        spec = gloss_scale = 1.0
        if n is not None:
            x, y = P.rg(n.outputs['Color'])
            base_n = P.tangent_normal(x, y, P.m('SUBTRACT', 1.0, P.dot2(x, y)), 'VR puddle base normal')
            wn = P.native_normal(); nx, ny, nz = P.sep(wn); ax, ay, az = P.sep(P.v('ABSOLUTE', wn))
            xd = P.m('MULTIPLY', P.ge(ax, ay), P.ge(ax, az)); yd = P.m('MULTIPLY', P.ge(ay, ax), P.ge(ay, az))
            px = P.lerp(wx, wz, xd); py = P.lerp(P.m('MULTIPLY', wy, -1.0), P.m('MULTIPLY', wz, -1.0), yd)
            nyd = P.m('MULTIPLY', P.m('SUBTRACT', 1.0, xd), yd); zd = P.m('MULTIPLY', P.m('SUBTRACT', 1.0, xd), P.m('SUBTRACT', 1.0, yd))
            px = P.m('MULTIPLY', px, P.m('ADD', P.m('SUBTRACT', 1.0, P.m('ADD', xd, zd)),
                     P.m('ADD', P.m('MULTIPLY', xd, P.m('MULTIPLY', P.m('SIGN', nx), -1.0)), P.m('MULTIPLY', zd, P.m('SIGN', nz)))))
            py = P.lerp(py, P.m('MULTIPLY', P.m('SIGN', ny), wz), nyd)
            px, py = P.m('SUBTRACT', px, .5), P.m('SUBTRACT', py, .5)
            ang = P.m('RADIANS', q[7]); ca, sa = P.m('COSINE', ang), P.m('SINE', ang)
            ru = P.m('MULTIPLY', q[1], P.m('ADD', .5, P.m('SUBTRACT', P.m('MULTIPLY', ca, px), P.m('MULTIPLY', sa, py))))
            rv = P.m('MULTIPLY', q[1], P.m('ADD', .5, P.m('ADD', P.m('MULTIPLY', ca, py), P.m('MULTIPLY', sa, px))))
            rx, ry = P.rg(P.tex(img('_puddle_ripple'), P.gl_coord(ru, rv), 'puddle ripple').outputs['Color'], P.sat(q[13]))
            ripple = P.tangent_normal(rx, ry, P.m('SUBTRACT', 1.0, P.dot2(rx, ry)), 'VR puddle ripple normal')
            rw = P.sat(P.m('DIVIDE', P.m('MULTIPLY_ADD', q[6], -.5, pm), P.m('SUBTRACT', 1.0, q[6])))
            normal = P.vlerp(base_n, ripple, rw)
        final_n = normal if normal is not None else Ng
        limits.append('puddles: forward light/probe/reflection composition (probe mix p12, specular clamp, fog) is replaced by '
                      'the deferred BRDF; ripple scroll at t=0; world origin 0')
    else:
        if on('pick') and c is not None and not on('vehicle') and not tint:   # 66744D01 colour pick
            pk = terms['pick']
            pal = P.tex(img('_pick'), P.comb(P.const('pick u', pk['u']), .5), 'pick palette').outputs['Color']
            a = c.outputs['Alpha']
            ta = P.m('MULTIPLY', P.const('pick scale', pk['scale']), a)
            r = P.v('SCALE', P.v('SUBTRACT', P.v('SCALE', pal, ta), 1.0), P.m('MULTIPLY', P.const('pick blend', pk['blend']), a))
            albedo = P.v('MAXIMUM', P.v('MULTIPLY_ADD', albedo, r, albedo), 0.0)
        rock_w = rock_g = None
        if on('rock_blend'):          # 886AAC94 two-layer blend through H, squared vertex alpha and sharpness
            rb = terms['rock_blend']
            attr = P.N.new('ShaderNodeAttribute'); attr.attribute_name = 'Color'; attr.name = 'VR vertex colour (missing = alpha 1)'
            va = P.m('MULTIPLY', attr.outputs['Alpha'], attr.outputs['Alpha'])
            h = P.sep(img('_rock_mask').outputs['Color'])[0]
            rm = P.m('SUBTRACT', 1.0, P.sat(P.m('MULTIPLY', P.m('SUBTRACT', 1.0, va), P.m('MULTIPLY_ADD', h, -5.0, 6.0))))
            sharp = P.const('rock sharpness', rb['sharpness'])
            target = P.m('POWER', P.m('ABSOLUTE', rm), sharp)
            if float(rb['sharpness']) != 0.0:   # m == 0 -> 0 (sharpness 0: pow(m, 0) = 1 everywhere)
                target = P.m('MULTIPLY', target, P.m('GREATER_THAN', rm, 0.0))
            rock_w = P.lerp(va, target, P.const('rock mask strength', rb['mask_strength']))
            albedo = P.vlerp(albedo, img('_rock_c2').outputs['Color'], rock_w)
            tinted = P.vsat(P.v('POWER', P.v('ABSOLUTE', P.v('MULTIPLY', P.const3('rock tint', rb['tint']), albedo)), P.const('rock exponent', rb['exponent'])))
            albedo = P.vlerp(albedo, tinted, P.const('rock tint amount', rb['tint_amount']))
            rock_g = P.vlerp(g_rgb if g is not None else (0.0, 0.0, 0.0), img('_rock_g2').outputs['Color'], rock_w)
            limits.append('rock_blend: vertex alpha is squared after interpolation (the vertex shader squares it before); '
                          'models without a colour stream use alpha 1 as the viewer does')
        world_uv = detail_mask = None
        if tint and variant in ('world', 'world_normal'):   # 69424FA0: dominant-axis native world projection
            wn = P.native_normal(); nx, ny, nz = P.sep(wn); ax, ay, az = P.sep(P.v('ABSOLUTE', wn))
            wp = P.v('ADD', P.native_position(), P.const3('tint world origin', tint['world_origin']))
            wx, wy, wz = P.sep(wp)
            xd = P.m('MULTIPLY', P.ge(ax, ay), P.ge(ax, az))
            yd = P.m('MULTIPLY', P.m('SUBTRACT', 1.0, xd), P.m('MULTIPLY', P.ge(ay, ax), P.ge(ay, az)))
            zd = P.m('SUBTRACT', 1.0, P.m('ADD', xd, yd))
            s = P.m('ADD', P.m('ADD', P.m('MULTIPLY', xd, P.m('MULTIPLY', P.m('SIGN', nx), P.m('MULTIPLY', wz, -1.0))),
                               P.m('MULTIPLY', yd, wx)), P.m('MULTIPLY', zd, P.m('MULTIPLY', P.m('SIGN', nz), wx)))
            t = P.m('ADD', P.m('MULTIPLY', P.m('ADD', xd, zd), P.m('MULTIPLY', wy, -1.0)), P.m('MULTIPLY', yd, P.m('MULTIPLY', P.m('SIGN', ny), wz)))
            scale = P.const('tint detail scale', tint['detail_scale'])
            world_uv = P.gl_coord(P.m('MULTIPLY', s, scale), P.m('MULTIPLY', t, scale))
            d = P.sep(P.tex(img('_tint_detail'), world_uv, 'tint world detail').outputs['Color'])[0] if img('_tint_detail') else .5
            detail_mask = P.sat(P.m('MULTIPLY', P.sat(P.m('MULTIPLY_ADD', d, 5.0, -.5)), 1.25))
        if tint:                      # BE8C3EE0: lerp(B', A', m)
            h = P.sep(img('_h').outputs['Color'])[0] if img('_h') else 0.0
            tm = P.m('MULTIPLY', P.const('tint weight A', tint['weight_a']), h)
            tw = P.m('MULTIPLY', P.const('tint weight B', tint['weight_b']), P.m('SUBTRACT', 1.0, P.m('MULTIPLY', P.const('tint mask B', tint['mask_b']), h)))
            powered = lambda colour, power: P.vsat(P.v('POWER', P.v('MAXIMUM', P.v('ABSOLUTE', P.v('MULTIPLY', colour, albedo)), 1e-8), power))
            ta = powered(P.const3('tint A', tint['a']), P.const('tint power A', tint['power_a']))
            tb = powered(P.const3('tint B', tint['b']), P.const('tint power B', tint['power_b']))
            a2 = P.vlerp(albedo, ta, tm); b2 = P.vlerp(albedo, tb, tw)
            albedo = P.vlerp(b2, a2, tm)
            if detail_mask is not None:   # overlay with the world detail mask
                low = P.v('SCALE', albedo, P.m('MULTIPLY', detail_mask, 2.0))
                high = P.v('SUBTRACT', 1.0, P.v('SCALE', P.v('SUBTRACT', 1.0, albedo), P.m('MULTIPLY', P.m('SUBTRACT', 1.0, detail_mask), 2.0)))
                below = P.v('MAXIMUM', P.v('SCALE', P.v('SIGN', P.v('SUBTRACT', albedo, .5)), -1.0), 0.0)
                albedo = P.v('MULTIPLY_ADD', below, P.v('SUBTRACT', low, high), high)
            if variant:
                albedo = P.v('MULTIPLY', albedo, P.v('ADD', 1.0, P.v('SCALE', P.v('SUBTRACT', P.const3('tint palette', tint['palette_rgb']), 1.0),
                                                                        P.const('tint palette blend', tint['palette_blend']))))
        if on('vehicle'):             # EB1AC2CD rel 628-828: both 1D palettes on UV0.x
            vh = terms['vehicle']
            u = P.uv_split()[0]
            p1 = P.tex(img('_pick'), P.comb(u, .5), 'vehicle palette 1').outputs['Color'] if img('_pick') else (1.0, 1.0, 1.0)
            p2 = P.tex(img('_vehicle_p2'), P.comb(u, .5), 'vehicle palette 2').outputs['Color'] if img('_vehicle_p2') else (1.0, 1.0, 1.0)
            d = P.m('ABSOLUTE', P.m('SUBTRACT', 1.00100004673, P.sat(P.v('DOT_PRODUCT', Ng, V))))
            fr = P.m('POWER', d, 1.20000004768)
            va = P.v('MULTIPLY', P.const3('vehicle A', vh['a']), p1); vb = P.v('MULTIPLY', P.const3('vehicle B', vh['b']), p2)
            mixv = P.v('SUBTRACT', P.v('MULTIPLY_ADD', P.v('SUBTRACT', vb, va), P.m('MULTIPLY', fr, 2.0), va), 1.0)
            paint = P.v('MULTIPLY', albedo, P.v('ADD', 1.0, P.v('SCALE', mixv, P.const('vehicle blend', vh['blend']))))
            paint = P.v('SCALE', paint, P.m('SUBTRACT', 1.0, P.m('MULTIPLY', P.m('MULTIPLY', P.const('vehicle rim', vh['rim']), 1.20000004768), d)))
            mask = P.sep(img('_h').outputs['Color'])[0] if img('_h') else 0.0
            albedo = P.vsat(P.vlerp(albedo, paint, mask))
            view_terms.append('vehicle')
        arch_self = None
        if arch:                      # AAED778C rel 572-744 at t=0 (no _c, no normal sample)
            h = P.sep(img('_h').outputs['Color'])[0] if img('_h') else 0.0
            amask = P.sep(img('_pick').outputs['Color'])[0] if img('_pick') else 0.0
            power = P.const('arch power', arch['power'])
            hp = P.m('POWER', P.m('ABSOLUTE', h), power)
            if float(arch['power']) != 0.0:
                hp = P.m('MULTIPLY', hp, P.m('GREATER_THAN', P.m('ABSOLUTE', h), 0.0))
            seq = img('_sequence')
            qv = P.sep(P.tex(seq, P.comb(P.const('arch phase', arch['phase']), .5), 'arch sequence at phase').outputs['Color'])[0] if seq else 0.0
            albedo = P.vsat(P.v('SCALE', P.const3('arch colour', arch['colour']), P.m('MULTIPLY_ADD', P.m('MULTIPLY', P.const('arch linear', arch['linear']), .5), h, hp)))
            arch_self = P.m('MULTIPLY', P.m('MULTIPLY', qv, hp), P.m('ADD', P.const('arch mask bias', arch['mask_bias']), amask))
        bng_self = None
        if kind_terms and kind == 1:
            albedo = P.v('MULTIPLY', albedo, P.v('ADD', 1.0, P.v('SCALE', P.v('SUBTRACT', P.const3('bng colour', bng['colour']), 1.0), gB)))
        if kind_terms and kind == 4:
            colour = P.const3('bng colour', bng['colour'])
            bng_self = P.m('MULTIPLY', P.sep(albedo)[0], P.m('ADD', 1.0, P.m('MULTIPLY', gB, P.m('SUBTRACT', P.sep(colour)[0], 1.0))))
        # Tangent normal (gl_shading.FRAGMENT normal block); the arch and vehicle writers have none.
        rim_a = rim_b = bng_p = None
        glow = terms.get('glow') if on('glow') else None
        metal = terms.get('two_color_metal') if on('two_color_metal') else None
        if n is not None and not arch and not on('vehicle'):
            linear_z = rule.get('normal_z') == '1-x*x-y*y'
            strength = P.sat(P.const('metal normal strength', metal['normal_strength'])) if metal else None
            x, y = P.rg(n.outputs['Color'], strength)
            dsq = P.dot2(x, y)
            z = P.m('SUBTRACT', 1.0, dsq, label='Viewer rule normal z = 1-x*x-y*y') if linear_z else \
                P.m('SQRT', P.m('MAXIMUM', P.m('SUBTRACT', 1.0, dsq), 0.0), label='z = sqrt(1-x*x-y*y)')
            if tint and ((variant == 'detail' and img('_tint_detail')) or (variant == 'world_normal' and img('_pick'))):
                dscale = P.const('tint detail scale', tint['detail_scale']) if variant == 'detail' else None
                dtex = P.tex(img('_tint_detail'), P.game_uv(dscale, dscale), 'tint detail normal') if variant == 'detail' else \
                    P.tex(img('_pick'), world_uv, 'tint world detail normal')
                dx, dy = P.rg(dtex.outputs['Color'], P.sat(P.const('tint detail strength', tint['detail_strength'])))
                dz = P.m('SUBTRACT', 1.0, P.dot2(dx, dy)); hz = P.m('MULTIPLY', z, .5)
                x, y = P.m('MULTIPLY_ADD', x, dz, P.m('MULTIPLY', hz, dx)), P.m('MULTIPLY_ADD', y, dz, P.m('MULTIPLY', hz, dy))
                z = P.m('MULTIPLY', z, dz)
            normal = base_normal = P.tangent_normal(x, y, z, 'Tangent normal (_n, no green flip)')
            if kind_terms and kind in (3, 6):
                rim = P.m('SUBTRACT', 1.00100004673, P.sat(P.v('DOT_PRODUCT', base_normal, V)))
                bng_p = lambda i: P.const('bng P%d' % i, bng['params'][i])
                rim_a = P.m('POWER', P.m('ABSOLUTE', rim), bng_p(0)); rim_b = P.m('POWER', P.m('ABSOLUTE', rim), bng_p(1))
                view_terms.append('bng rims')
            if 'bng_detail' in ported and kind in (2, 3, 6):   # F3DCD431/F087BE8D/F8161C61 detail xy
                det = bng['detail']
                dmask = gB if kind == 3 else (P.m('MULTIPLY', g.outputs['Alpha'], g.outputs['Alpha']) if g is not None else 0.0)
                vec = P.game_uv(P.const('bng detail scale u', det[0]), P.const('bng detail scale v', det[1]))
                dx, dy = P.rg(P.tex(img('_bng_detail'), vec, 'bng detail normal').outputs['Color'], dmask)
                dz = P.m('SUBTRACT', 1.0, P.dot2(dx, dy)); zw = P.m('MULTIPLY', z, P.const('bng detail strength', det[2]))
                x, y = P.m('MULTIPLY_ADD', x, dz, P.m('MULTIPLY', dx, zw)), P.m('MULTIPLY_ADD', y, dz, P.m('MULTIPLY', dy, zw))
                z = P.m('MULTIPLY', z, dz)
                normal = P.tangent_normal(x, y, z, 'VR detailed normal')
            if rock_w is not None:
                r2x, r2y = P.rg(img('_rock_n2').outputs['Color'])
                n2 = P.tangent_normal(r2x, r2y, P.m('SUBTRACT', 1.0, P.dot2(r2x, r2y)), 'VR rock layer B normal')
                normal = P.v('NORMALIZE', P.vlerp(base_normal, n2, rock_w))
        final_n = normal if normal is not None else Ng
        metal_tint = None
        if metal:                     # 1012E99D forward two-colour terms with the final normal
            b = P.m('SUBTRACT', 1.00100004673, P.sat(P.v('DOT_PRODUCT', final_n, V)))
            t = P.m('POWER', b, P.const('metal power', metal['power']))
            metal_rim = P.sat(P.m('POWER', b, P.const('metal gloss power', metal['gloss_power'])))
            metal_tint = P.vsat(P.v('MULTIPLY', P.v('SCALE', albedo, P.const('metal texture scale', metal['texture_scale'])),
                                    P.vlerp(P.const3('metal spec B', metal['spec_b']), P.const3('metal spec A', metal['spec_a']), t)))
            albedo = P.v('MULTIPLY', albedo, P.vlerp(P.const3('metal diffuse B', metal['diffuse_b']), P.const3('metal diffuse A', metal['diffuse_a']), t))
            view_terms.append('two-colour metal')
            limits.append('two_color_metal: Blender cannot multiply a closure by a colour, so the specular tint T multiplies F0 '
                          '(the Fresnel edge stays F90 instead of T*F90)')
        if kind_terms and kind == 3 and rim_b is not None:
            albedo = P.v('SCALE', albedo, P.m('ADD', 1.0, P.m('MULTIPLY', P.m('MULTIPLY', bng_p(0), gB), rim_b)))
        if kind_terms and kind == 6 and rim_b is not None:
            albedo = P.v('SCALE', albedo, P.m('SUBTRACT', 1.0, rim_b))
        glow_mask = P.sep(img('_h').outputs['Color'])[0] if glow and img('_h') else (0.0 if glow else None)
        if glow and int(glow['kind']) == 1:   # 2685144F: _c.rgb never sampled; environment = viewer ambient
            up = P.sep(final_n)[2]
            ambient = P.vlerp((0.10, 0.09, 0.08), (0.30, 0.33, 0.38), P.m('MULTIPLY_ADD', up, .5, .5))
            albedo = P.v('SCALE', P.v('ADD', P.const3('glow colour', glow['colour']), P.v('SCALE', ambient, P.const('glow environment scale', glow['environment_scale']))), glow_mask)
            limits.append('glow kind 1: engine environment irradiance is the viewer sky/ground ambient on the world-up normal; '
                          'parallax uses the Blender UV tangent (bitangent = N x T)')
        # Specular F0 / gloss (gl_shading.FRAGMENT specular block).
        if g is not None and mode in ('grey', 'grey_skin'):
            f0v = gG
            if mode == 'grey_skin':
                f0v = P.m('MINIMUM', gG, 0.5, label='skin F0 = min(0.5, _g.G)'); spec, gloss_scale = 1.0, 1.0
            gloss_in = P.m('SQRT', gR, label='sqrt(_g.R): the group squares it back')
            if za_w is not None:
                f0v = P.m('MULTIPLY', gG, P.sat(P.m('MULTIPLY', za_w, spec)))
                gloss_in = P.m('SQRT', P.m('MULTIPLY', gR, P.sat(P.m('MULTIPLY', za_w, gloss_scale))))
                spec = gloss_scale = 1.0
            if kind_terms and kind == 6 and rim_a is not None:
                f0v = P.m('MULTIPLY', gG, P.m('MULTIPLY_ADD', bng_p(2), rim_a, 1.0))
                gloss_in = P.m('SQRT', P.m('MAXIMUM', P.m('ADD', gR, rim_a), 0.0))
            if detail_mask is not None:
                gloss_in = P.m('SQRT', P.m('MULTIPLY', P.m('MULTIPLY', gR, 2.0), detail_mask))
            if rock_g is not None:
                rgr, rgg, _ = P.sep(rock_g); f0v = rgg; gloss_in = P.m('SQRT', P.m('MAXIMUM', rgr, 0.0))
            if metal:
                gloss_in = P.m('SQRT', P.m('MULTIPLY', gR, P.m('SUBTRACT', 1.0, metal_rim)))
            f0 = P.comb(f0v, f0v, f0v)
        elif mode == 'vehicle':
            f0 = (0.0, 0.0999755859375, 0.0999755859375); gloss_in = 0.948681201424; spec, gloss_scale = 1.0, 1.0
        elif g is not None:
            f0, gloss_in = g_rgb, g.outputs['Alpha']
        spec_tint = terms.get('spec_tint') if on('spec_tint') else None
        if spec_tint is not None and f0 is not None and mode != 'vehicle':
            f0 = P.v('MULTIPLY', f0, P.const3('spec tint', spec_tint))
        if metal_tint is not None and f0 is not None:
            f0 = P.v('MULTIPLY', f0, metal_tint)
        # Self-lit (glow, basic_normal_gloss kind 4, arch sequence; else the generic _g.B / _c.a mask).
        if glow and int(glow['kind']) == 1:
            u_off = v_off = 0.0
            if n is not None:   # parallax offset along the geometric UV frame (front face)
                tan = P.N.new('ShaderNodeTangent'); tan.direction_type = 'UV_MAP'; tan.uv_map = 'UV0'; tan.name = 'VR UV0 tangent'
                T = P.v('NORMALIZE', tan.outputs[0]); Bb = P.v('NORMALIZE', P.v('CROSS_PRODUCT', Ng, T))
                depth = P.const('glow depth', glow['depth'])
                u_off = P.m('MULTIPLY', depth, P.v('DOT_PRODUCT', V, T))
                v_off = P.m('MULTIPLY', depth, P.m('MULTIPLY', P.v('DOT_PRODUCT', V, Bb), -1.0))   # game v runs along -B_blender
                view_terms.append('glow parallax')
            par = img('_glow_parallax')
            pv = P.sep(P.tex(par, P.game_uv(1.0, 1.0, u_off, v_off), 'glow parallax').outputs['Color'])[0] if par else 0.0
            self_mask = P.m('MULTIPLY', glow_mask, P.m('ADD', P.const('glow bias', glow['bias']), pv))
        elif glow and int(glow['kind']) == 2:
            mult = 1.0 + float(glow.get('vertex_fade', 0.0)) * (abs(float(glow.get('vertex_magnitude', 1.0))) - 1.0)
            self_mask = P.m('MULTIPLY', P.m('MULTIPLY', P.sep(albedo)[0], glow_mask), P.const('glow vertex multiplier', mult))
        elif bng_self is not None:
            self_mask = bng_self
        elif arch_self is not None:
            self_mask = arch_self; k = 5.0 * float(arch['intensity'])   # K = 5 * intensity
    # Wire the BRDF.
    L.new(albedo, grp.inputs[0]) if isinstance(albedo, bpy.types.NodeSocket) else setattr(grp.inputs[0], 'default_value', (*albedo, 1.0))
    if f0 is None:
        grp.inputs[2].default_value = (0.0, 0.0, 0.0, 1.0)   # no _g: the viewer draws no specular
        grp.inputs[3].default_value = 0.0
    else:
        L.new(f0, grp.inputs[2]) if isinstance(f0, bpy.types.NodeSocket) else setattr(grp.inputs[2], 'default_value', (*f0, 1.0))
        L.new(gloss_in, grp.inputs[3]) if isinstance(gloss_in, bpy.types.NodeSocket) else setattr(grp.inputs[3], 'default_value', float(gloss_in))
    grp.inputs[5].default_value = spec
    grp.inputs[6].default_value = gloss_scale
    if self_mask is not None and k > 0.0:
        L.new(self_mask, grp.inputs[1]) if isinstance(self_mask, bpy.types.NodeSocket) else setattr(grp.inputs[1], 'default_value', float(self_mask))
        how = 'term'
    elif k > 0.0 and rule.get('self_lit') == 'g_blue' and g is not None:
        L.new(gB, grp.inputs[1]); how = 'g_blue'
    elif k > 0.0 and rule.get('self_lit', 'c_alpha') == 'c_alpha' and c is not None and self_mask is None:
        L.new(c.outputs['Alpha'], grp.inputs[1]); how = 'c_alpha'
    else:
        k = 0.0; how = 'off'
    grp.inputs[7].default_value = k
    L.new(final_n, grp.inputs[4])
    if facing:   # 0CD2AAFB rel 12552-12672, added after lighting: an emission beside the BRDF
        ndv = P.sat(P.v('DOT_PRODUCT', final_n, V))
        f = P.m('POWER', P.m('MAXIMUM', P.m('SUBTRACT', ndv, P.const('facing glow bias', facing['bias'])), 0.0),
                P.const('facing glow exponent', facing['exponent']))
        a = P.m('MULTIPLY_ADD', f, P.const('facing glow a scale', facing['a_scale']), P.const('facing glow a offset', facing['a_offset']))
        amp, one_a = P.const('facing glow amplitude', facing['amplitude']), P.m('ADD', a, 1.0)
        red = P.m('MULTIPLY', amp, P.m('MULTIPLY', P.m('MULTIPLY', a, 2.0), one_a))
        green = P.m('MULTIPLY', amp, P.m('MULTIPLY', P.m('MULTIPLY', f, P.const('facing glow g scale', facing['g_scale'])), one_a))
        emit = nt.nodes.new('ShaderNodeEmission'); emit.name = emit.label = 'VR facing glow emission'
        emit.inputs['Strength'].default_value = 1.0
        L.new(P.comb(red, green, 0.0), emit.inputs['Color'])
        add = nt.nodes.new('ShaderNodeAddShader'); add.name = add.label = 'VR facing glow add'; add.location = (650, -150)
        L.new(grp.outputs[0], add.inputs[0]); L.new(emit.outputs[0], add.inputs[1]); L.new(add.outputs[0], out.inputs['Surface'])
        view_terms.append('facing glow')
        limits.append('facing glow: presentation amplitude %g for %s (captured %g; blink timing not decoded); fog, tint '
                      'volumes and instance flag bit 5 lighting terms omitted (v8 = v9 = 1)'
                      % (facing['amplitude'], facing['amplitude_input'], facing['captured_amplitude']))
    if on('alpha_cut') and c is not None:
        # Foliage writers kill a fragment whose _c.a is below the interpolated vertex output attr0.z, which
        # the vertex shader takes from the engine's per-instance record: the threshold is inferred (0.5).
        keep = P.ge(c.outputs['Alpha'], P.const('alpha cut threshold', terms['alpha_cut']))
        clear = nt.nodes.new('ShaderNodeBsdfTransparent'); clear.name = clear.label = 'VR alpha cut transparent'
        mix = nt.nodes.new('ShaderNodeMixShader'); mix.name = mix.label = 'VR alpha cut mix'; mix.location = (950, 0)
        L.new(keep, mix.inputs[0]); L.new(clear.outputs[0], mix.inputs[1])
        L.new(out.inputs['Surface'].links[0].from_socket, mix.inputs[2]); L.new(mix.outputs[0], out.inputs['Surface'])
        out.location = (1150, 0)
        limits.append('leaf cut-out: _c.a < %g is transparent; the game reads this threshold from a per-instance '
                      'engine record, so the value is inferred, not decoded' % float(terms['alpha_cut']))
    if view_terms:
        limits.append('view-dependent terms (%s) use the Cycles Incoming vector, which is the camera direction only for camera rays'
                      % ', '.join(view_terms))
    _finish_viewer_rule(mat, rule, 'asset viewer per-graph reading (%s _g, self-lit %s, K %.6g)' % (mode, how, k), limits)
    return True


def _finish_viewer_rule(mat, rule, frontend, limits):
    mat['rc_shader'] = '%s v%d' % (GROUP_NAME, GROUP_VERSION)
    mat['rc_viewer_graph_rule'] = json.dumps(rule, sort_keys=True)
    mat['rc_viewer_rule_frontend_version'] = VIEWER_RULE_FRONTEND_VERSION
    mat['rc_shader_frontend'] = frontend
    mat['rc_shader_evidence'] = ('Same per-graph rule as the asset viewer Shaded mode (texture_preview.GRAPH_RULES '
                                 'and its decode documents); terms built: %s' % (', '.join(rule.get('ported') or ()) or 'none'))
    notes = list(limits)
    if rule.get('unported'):
        reasons = rule.get('unported_reasons') or {}
        notes.append('Not built: ' + ', '.join('%s (%s)' % (key, reasons.get(key, 'unported')) for key in rule['unported']))
    notes.append('Exact sRGB decode in Blender where the viewer uses pow 2.2; Blender UV tangent frame instead of screen derivatives')
    mat['rc_shader_frontend_limitation'] = '; '.join(notes)
    mat['shader_preview_status'] = 'viewer_graph_rule_frontend'
    mat['game_equivalence'] = False


def apply_preflight_material(mat, source):
    """Apply only an exact, registered shader family from pipeline preflight.

    Returns False for an unknown identity; never infer a graph from its name.
    Images retain the native-format colourspaces assigned by the pipeline.
    """
    from pathlib import Path
    import sys
    root=Path(__file__).resolve().parents[2]
    if str(root) not in sys.path: sys.path.insert(0,str(root))
    from rc2016.shaders.families import FAMILY_REGISTRY
    reports=source.get('shader_preflight',[])
    if len(reports)!=1:return False
    pre=reports[0]
    identity=(pre.get('source_path'),pre.get('source_sha256'))
    family=pre.get('family')
    if identity in FAMILY_REGISTRY and family == 'character_glass':
        return apply_character_glass(mat, pre, source['path'])      # no texture inputs
    if identity not in FAMILY_REGISTRY or (family not in SKIN_FAMILIES and family not in DEFERRED_FAMILIES):
        return False
    # Materialgraph pink defaults do not establish model-specific C/G sources.
    # A missing instance/shared binding must stay a texture-limited preview.
    def real_bindings(role):
        return [r for r in source.get('textures',[]) if r.get('role')==role and
                (not r.get('source_references') or any(not ref.get('graph_fallback')
                    for ref in r.get('source_references',[])))]
    if not real_bindings('base_color') or not real_bindings('colored_specular_gloss'):
        return False
    if family in DEFERRED_FAMILIES:
        return apply_deferred_family(mat,pre,family)
    spec=SKIN_FAMILIES[family]
    rows={r['dword']:r for r in pre['parameter_tables'] if r['table_index']==1}
    params={}
    for dword,name in spec['table1'].items():
        row=rows.get(dword)
        if row is None or row['key_hash']!=_SKIN_KEYS[name]:
            raise ValueError('%s writer table 1 dword %d is not %s' % (family, dword, name))
        value=row.get('interpreted_instance_value',row.get('graph_default'))
        if value is None:raise ValueError('Unresolved skin material parameter '+name)
        params[name]=float(value)
    blend=_preflight_value(pre,SECONDARY_NORMAL_BLEND_KEY,0)
    uv_scale=_preflight_value(pre,SECONDARY_NORMAL_UV_SCALE_KEY,0)
    tex=_find_textures(mat)
    if 'c' not in tex or 'g' not in tex:return False
    secondary=[n for n in mat.node_tree.nodes if n.type=='TEX_IMAGE' and n.image is not None and
               re.search(r"\|\s*secondary_normal\s*$", n.name)]
    detail_ok=(spec['detail_normal'] and blend not in (None,0.0) and uv_scale is not None and
               'n' in tex and len(secondary)==1)
    convert_material(mat,emissive_intensity=params.get('Self-lit K',0.0),
                     g_is_srgb=tex['g'].image.colorspace_settings.name=='sRGB',
                     keep_nodes=secondary if detail_ok else ())
    skin=apply_skin_emissive(mat,params,family)
    if blend==0.0 and 'n' in tex:
        apply_native_skin_normal(mat,skin)
    elif detail_ok:
        apply_native_skin_normal(mat,skin,secondary[0],float(blend),float(uv_scale))
    else:
        mat['rc_shader_frontend_limitation']='Native secondary-normal blend unresolved; cached writer approximation retained'
    mat['rc_skin_family']=family
    mat['rc_skin_params_json']=json.dumps(params,sort_keys=True)
    mat['rc_shader_preflight']=json.dumps(pre,sort_keys=True)
    mat['rc_shader_evidence']='Hash-bound skin front-end; shared deferred BRDF approximation; skin-ramp forward lighting/runtime variant not reconstructed'
    mat['game_equivalence']=False
    return True


# ----------------------------------------------------------------------------- lighting
def game_light_to_blender_strength(game_rgb):
    """Game light colours are irradiance/pi (the BRDF omits 1/pi): Blender = pi * game."""
    peak = max(game_rgb) or 1.0
    return [v / peak for v in game_rgb], math.pi * peak
