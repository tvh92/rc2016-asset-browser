"""Fresh-process structural checks for preflight-selected shader previews."""
import json
import math
import struct

from rc2016.shaders.families import FAMILY_REGISTRY


DEFERRED_GROUP = "RC PS4 Deferred BRDF"
SKIN_GROUP = "RC PS4 skin_emissive G-buffer"
# Per family: front-end group name and deferred-writer table-1 dword -> parameter.
SKIN_FAMILY_CHECKS = {
    "skin_emissive": (SKIN_GROUP, {1: "Specular cap", 2: "Gloss Fresnel multiplier",
                                   3: "Specular Fresnel multiplier", 4: "Fresnel exponent",
                                   5: "Albedo scale", 6: "Gloss scale", 7: "Self-lit K",
                                   8: "Specular scale"}),
    "basic_skin": ("RC PS4 basic_skin G-buffer", {1: "Specular cap", 2: "Gloss Fresnel multiplier",
                                                  3: "Specular Fresnel multiplier", 4: "Fresnel exponent",
                                                  5: "Albedo scale", 6: "Gloss scale", 7: "Specular scale"}),
}
SECONDARY_NORMAL_BLEND_KEY = 0x59011FBD
SECONDARY_NORMAL_UV_SCALE_KEY = 0xB7ABB64A


def _from_node(socket):
    return socket.links[0].from_node if socket and socket.is_linked else None


def _linked(source_node, source_socket, destination_node, destination_socket):
    dst = destination_node.inputs.get(destination_socket)
    return bool(dst and any(link.from_node == source_node and
                            link.from_socket.name == source_socket for link in dst.links))


def _source_images(material):
    result = {}
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            path = node.get("source_texture_path")
            if path:
                result[path] = node
    return result


def _binding_for_node(source, node, role):
    path = node.get("source_texture_path")
    matches = [row for row in source.get("textures", [])
               if row.get("path") == path and row.get("role") == role]
    if len(matches) != 1:
        raise AssertionError(f"Expected one exact {role} binding for linked image {path}; found {len(matches)}")
    return matches[0]


def _has_real_role_binding(source, role):
    return any(row.get("role") == role and
               (not row.get("source_references") or any(
                   not ref.get("graph_fallback") for ref in row.get("source_references", [])))
               for row in source.get("textures", []))


def _check_binding_image(source, node, role):
    assert node is not None and node.type == "TEX_IMAGE", f"Reopened material lost linked {role} image"
    binding = _binding_for_node(source, node, role)
    assert node.get("source_texture_path") == binding["path"]
    assert node.get("source_texture_sha256") == binding.get("dds_sha256"), \
        f"Reopened {role} image source hash changed"
    assert node.image.colorspace_settings.name == binding["colorspace"], \
        f"Reopened {role} image colourspace differs from native DXGI preflight"
    return node


def _table_value(preflight, table_index, dword):
    rows = [row for row in preflight.get("parameter_tables", [])
            if row.get("table_index") == table_index and row.get("dword") == dword]
    assert len(rows) == 1, f"Missing or duplicate shader CB table {table_index} dword {dword}"
    row = rows[0]
    value = row.get("interpreted_instance_value", row.get("graph_default"))
    if value is None and row.get("instance_raw_hex") and len(row["instance_raw_hex"]) == 8:
        value = struct.unpack("<f", bytes.fromhex(row["instance_raw_hex"]))[0]
    assert isinstance(value, (int, float)), f"Unresolved shader CB table {table_index} dword {dword}"
    return float(value)


def _key_value(preflight, key, table_index=0):
    rows = [row for row in preflight.get("parameter_tables", [])
            if row.get("table_index") == table_index and row.get("key_hash") == key]
    assert len(rows) == 1, f"Missing or duplicate shader CB key 0x{key:08X}"
    return _table_value(preflight, table_index, rows[0]["dword"])


def _validate_skin_material(source, material, preflight):
    identity = (preflight.get("source_path"), preflight.get("source_sha256"))
    family = preflight.get("family")
    assert identity in FAMILY_REGISTRY and family in SKIN_FAMILY_CHECKS, \
        "Skin preflight identity is not registered"
    skin_group, socket_names = SKIN_FAMILY_CHECKS[family]
    nodes = material.node_tree.nodes
    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    assert output and output.inputs["Surface"].is_linked, "Skin material has no active surface output"
    brdf = [node for node in nodes if node.type == "GROUP" and node.node_tree and
            node.node_tree.name == DEFERRED_GROUP]
    skin = [node for node in nodes if node.type == "GROUP" and node.node_tree and
            node.node_tree.name == skin_group]
    assert len(brdf) == len(skin) == 1, "Expected one skin front-end and one deferred BRDF group"
    brdf, skin = brdf[0], skin[0]
    assert _from_node(output.inputs["Surface"]) == brdf, "Deferred BRDF is not linked to active Material Output"
    assert _linked(skin, "Albedo", brdf, brdf.inputs[0].name), "Skin albedo does not feed deferred diffuse"
    clamp = skin.node_tree.nodes.get("Skin albedo UNORM clamp")
    assert clamp is not None and clamp.clamp_result, "Skin albedo is not clamped to the UNORM MRT1 range"
    assert _linked(skin, "Specular F0", brdf, brdf.inputs[2].name), "Skin grey F0 does not feed deferred specular"
    assert _linked(skin, "Gloss", brdf, brdf.inputs[3].name), "Skin gloss does not feed deferred roughness"

    c = _check_binding_image(source, _from_node(skin.inputs["_c RGB"]), "base_color")
    g = _check_binding_image(source, _from_node(skin.inputs["_g RGB"]), "colored_specular_gloss")
    assert _linked(c, "Color", skin, "_c RGB"), "Native _c image is disconnected from skin front-end"
    assert _linked(g, "Color", skin, "_g RGB"), "Native _g image is disconnected from skin front-end"

    # Confirm the graph-table values survived application to the node group.
    params = json.loads(material.get("rc_skin_params_json", "{}"))
    for dword, name in socket_names.items():
        expected = _table_value(preflight, 1, dword)
        assert abs(float(params[name]) - expected) < 1e-6, f"Applied skin parameter changed: {name}"
        if name != "Self-lit K":
            assert abs(float(skin.inputs[name].default_value) - expected) < 1e-6, \
                f"Skin node group input changed: {name}"
    expected_k = (_table_value(preflight, 1, 7) if "Self-lit K" in socket_names.values() else 0.0)
    assert abs(float(brdf.inputs[7].default_value) - expected_k) < 1e-6, \
        "Deferred BRDF self-lit K differs from source-bound parameter"

    # BED06840's native XY reconstruction applies only when the instance
    # disables secondary normals and the native normal map is available.
    secondary = _key_value(preflight, SECONDARY_NORMAL_BLEND_KEY)
    normal_map = [node for node in nodes if node.type == "NORMAL_MAP"]
    has_native_normal = bool(material.get("rc_native_skin_frontend"))
    detail_rg = nodes.get("Native skin secondary normal RG")
    if has_native_normal:
        if detail_rg is None:
            assert secondary == 0.0, "Native single-normal frontend applied with nonzero secondary-normal blend"
        else:
            assert family == "basic_skin" and secondary != 0.0, "Detail-normal blend applied outside its decoded family"
            d = _check_binding_image(source, _from_node(detail_rg.inputs[0]), "secondary_normal")
            assert d.image.colorspace_settings.name == "Non-Color", "Secondary normal image must remain Non-Color"
            uv = nodes.get("Secondary normal UV0 scale")
            assert uv and _linked(uv, "Vector", d, "Vector") and \
                abs(float(uv.inputs["Scale"].default_value) - _key_value(preflight, SECONDARY_NORMAL_UV_SCALE_KEY)) < 1e-6, \
                "Secondary normal UV scale differs from source parameter"
            w = nodes.get("Skin detail weight")
            gsep = nodes.get("Skin detail weight _g.B")
            assert w and abs(float(w.inputs[1].default_value) - secondary) < 1e-6, "Detail weight differs from source blend"
            assert gsep and _linked(gsep, "Blue", w, w.inputs[0].name) and \
                _from_node(gsep.inputs[0]) == _from_node(skin.inputs["_g RGB"]), "Detail weight is not blend * _g.B"
        assert len(normal_map) == 1 and abs(float(normal_map[0].inputs["Strength"].default_value) - 1.0) < 1e-6
        normal_map = normal_map[0]
        encoded = nodes.get("Native skin normal encoded")
        normal_rg = nodes.get("Native skin normal RG")
        assert encoded and encoded.type == "VECT_MATH" and encoded.operation == "MULTIPLY_ADD"
        assert normal_rg and normal_rg.type == "SEPARATE_COLOR"
        n = _check_binding_image(source, _from_node(normal_rg.inputs[0]), "normal")
        assert n.image.colorspace_settings.name == "Non-Color", "Native normal image must remain Non-Color"
        assert _linked(n, "Color", normal_rg, normal_rg.inputs[0].name), \
            "Native _n image does not feed the decoded XY normal path"
        assert _linked(encoded, encoded.outputs[0].name, normal_map, "Color"), \
            "Decoded native normal is not encoded back through the Normal Map node"
        assert _linked(normal_map, "Normal", skin, "Material normal"), \
            "Normal Map result does not feed skin material-normal input"
        normal_mode = ("F0CD85FF_detail_normal_blend" if detail_rg is not None else
                       "BED06840_single_normal_secondary_weight_zero")
    else:
        assert material.get("rc_shader_frontend_limitation"), \
            "Cached writer fallback with unresolved native normal has no limitation label"
        geo_normal = nodes.get("skin cached writer vertex normal")
        assert geo_normal and geo_normal.type == "NEW_GEOMETRY" and \
            _linked(geo_normal, "Normal", skin, "Material normal"), \
            "Cached-writer fallback does not use its labelled geometry normal"
        normal_mode = "cached_writer_geometry_normal_approximation"
    return dict(status="passed_partial_skin_frontend", family=family, source_path=preflight["source_path"],
                source_sha256=preflight["source_sha256"], active_deferred_brdf=True,
                c_g_native_colorspaces=True, normal_frontend=normal_mode,
                limitation="Forward lighting, skin_ramp and runtime shader selection remain unresolved.")


# Standard deferred families: g_mode and (self-lit source, K key), mirroring
# rc_game_shader.DEFERRED_FAMILIES; parameters are checked by key.
DEFERRED_FAMILY_CHECKS = {
    "basic_normal_gloss": ("grey_rg", None),
    "basic_normal_coloredgloss_emissive": ("colored_alpha", ("c_alpha", 0x06F19114)),
    "basic_incandescence": ("grey_rg", ("g_blue", 0x5FD56522)),
    "basic_colored_spec_emmisive_in_c_alpha": ("colored_alpha", ("c_alpha", 0xF8C61683)),
}
SPECULAR_SCALE_KEY = 0x62835AF9
GLOSS_SCALE_KEY = 0x24F4CA4C


def _all_tables_key_value(preflight, key):
    values = {round(_table_value(preflight, row["table_index"], row["dword"]), 9)
              for row in preflight.get("parameter_tables", []) if row.get("key_hash") == key}
    assert len(values) == 1, f"Shader CB key 0x{key:08X} is missing or differs between tables"
    return values.pop()


def _validate_deferred_material(source, material, preflight):
    identity = (preflight.get("source_path"), preflight.get("source_sha256"))
    family = preflight.get("family")
    assert identity in FAMILY_REGISTRY and family in DEFERRED_FAMILY_CHECKS, \
        "Deferred preflight identity is not registered"
    g_mode, self_lit = DEFERRED_FAMILY_CHECKS[family]
    assert material.get("rc_deferred_family") == family, "Material does not record its deferred family"
    nodes = material.node_tree.nodes
    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    assert output and output.inputs["Surface"].is_linked, "Deferred material has no active surface output"
    brdf = [node for node in nodes if node.type == "GROUP" and node.node_tree and
            node.node_tree.name == DEFERRED_GROUP]
    assert len(brdf) == 1 and _from_node(output.inputs["Surface"]) == brdf[0], \
        "Deferred BRDF is not the active Material Output surface"
    brdf = brdf[0]
    c = _check_binding_image(source, _from_node(brdf.inputs[0]), "base_color")
    assert _linked(c, "Color", brdf, brdf.inputs[0].name), "_c colour does not feed deferred albedo"
    expected = {"spec": _all_tables_key_value(preflight, SPECULAR_SCALE_KEY),
                "gloss": _all_tables_key_value(preflight, GLOSS_SCALE_KEY),
                "k": _all_tables_key_value(preflight, self_lit[1]) if self_lit else 0.0}
    for index, name in ((5, "spec"), (6, "gloss"), (7, "k")):
        assert abs(float(brdf.inputs[index].default_value) - expected[name]) < 1e-5, \
            f"Deferred BRDF {name} differs from source-bound parameter"
    if g_mode == "grey_rg":
        grey, root = _from_node(brdf.inputs[2]), _from_node(brdf.inputs[3])
        sep = nodes.get("Deferred _g channels")
        assert grey and grey.name == "Deferred grey F0 = _g.G" and root and root.name == "Deferred gloss sqrt(_g.R)"
        assert sep and all(_linked(sep, "Green", grey, grey.inputs[i].name) for i in range(3)), "Grey F0 is not _g.G"
        assert _linked(sep, "Red", root, root.inputs[0].name) and root.operation == "SQRT", "Gloss is not sqrt(_g.R)"
        g = _check_binding_image(source, _from_node(sep.inputs[0]), "colored_specular_gloss")
    else:
        g = _check_binding_image(source, _from_node(brdf.inputs[2]), "colored_specular_gloss")
        assert _linked(g, "Color", brdf, brdf.inputs[2].name) and _linked(g, "Alpha", brdf, brdf.inputs[3].name), \
            "Coloured F0/gloss is not _g.rgb/_g.a"
    if self_lit is None:
        assert not brdf.inputs[1].is_linked, "Family without self-lit has a linked self-lit mask"
    elif self_lit[0] == "c_alpha":
        assert _linked(c, "Alpha", brdf, brdf.inputs[1].name), "Self-lit mask is not _c.a"
    else:
        sep = nodes.get("Deferred _g channels")
        assert sep and _linked(sep, "Blue", brdf, brdf.inputs[1].name) and _from_node(sep.inputs[0]) == g, \
            "Incandescence mask is not _g.B"
    normal_rg = nodes.get("Native skin normal RG")
    if normal_rg is not None:
        n = _check_binding_image(source, _from_node(normal_rg.inputs[0]), "normal")
        assert n.image.colorspace_settings.name == "Non-Color", "Native normal image must remain Non-Color"
        normal_map = _from_node(brdf.inputs[4])
        encoded = nodes.get("Native skin normal encoded")
        assert normal_map and normal_map.type == "NORMAL_MAP" and \
            abs(float(normal_map.inputs["Strength"].default_value) - 1.0) < 1e-6
        assert encoded and _linked(encoded, encoded.outputs[0].name, normal_map, "Color"), \
            "Decoded native normal is not encoded through the Normal Map node"
        normal_mode = "native_xy_z_1_minus_r2"
    else:
        geo = _from_node(brdf.inputs[4])
        assert geo and geo.type == "NEW_GEOMETRY", "Normal input is neither native normal nor geometry normal"
        normal_mode = "geometry_normal_no_normal_binding"
    return dict(status="passed_partial_deferred_writer_frontend", family=family,
                source_path=preflight["source_path"], source_sha256=preflight["source_sha256"],
                active_deferred_brdf=True, c_g_native_colorspaces=True, normal_frontend=normal_mode,
                parameters=expected, frontend_limitation=material.get("rc_shader_frontend_limitation"),
                limitation="Deferred writer front-end only; forward variants and runtime selection unresolved.")


GLASS_GROUP = "RC PS4 character_glass forward"
GLASS_SCALAR_SOCKETS = {"Alpha exponent P8": "alpha_exponent", "Env scale P9": "env_scale",
                        "Alpha bias P10": "alpha_bias", "Rim bias P11": "rim_bias",
                        "Env exponent P12": "env_exponent", "Rim exponent P13": "rim_exponent"}


def _glass_key_value(preflight, key):
    values = set()
    for row in preflight.get("parameter_tables", []):
        if row.get("key_hash") != key:
            continue
        value = row.get("interpreted_instance_value", row.get("graph_default"))
        if value is None and len(row.get("instance_raw_hex", "")) % 8 == 0 and row.get("instance_raw_hex"):
            raw = bytes.fromhex(row["instance_raw_hex"])
            value = list(struct.unpack("<%df" % (len(raw) // 4), raw))
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        values.add(json.dumps(value))
    assert len(values) == 1, f"character_glass key 0x{key:08X} missing or inconsistent"
    return json.loads(values.pop())


def _validate_character_glass(source, material, preflight):
    from rc2016.shaders.families import CHARACTER_GLASS_KEYS
    identity = (preflight.get("source_path"), preflight.get("source_sha256"))
    assert identity in FAMILY_REGISTRY and preflight.get("family") == "character_glass", \
        "character_glass preflight identity is not registered"
    nodes = material.node_tree.nodes
    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    groups = [node for node in nodes if node.type == "GROUP" and node.node_tree and node.node_tree.name == GLASS_GROUP]
    assert output and len(groups) == 1 and _from_node(output.inputs["Surface"]) == groups[0], \
        "character_glass group is not the active surface"
    group = groups[0]
    values = {name: _glass_key_value(preflight, key) for name, key in CHARACTER_GLASS_KEYS.items()}
    for socket, name in GLASS_SCALAR_SOCKETS.items():
        assert abs(float(group.inputs[socket].default_value) - float(values[name])) < 1e-5, \
            f"Glass parameter {name} differs from source-bound value"
    body = list(group.inputs["Body colour B"].default_value)[:3]
    assert all(abs(a - float(b)) < 1e-5 for a, b in zip(body, values["body_colour"])), "Glass body colour changed"
    tint = [float(x) for x in values["reflection_tint"]]
    y = 0.2126 * tint[0] + 0.7152 * tint[1] + 0.0722 * tint[2]
    sat = lambda x: min(1.0, max(0.0, x))
    expected = [sat(float(values["reflection_intensity"]) * sat(c / y)) for c in tint]
    got = list(group.inputs["Reflection tint"].default_value)[:3]
    assert all(abs(a - b) < 1e-5 for a, b in zip(got, expected)), "Glass reflection tint differs from decoded normalisation"
    rough = 2.0 ** (-2.75 * (sat(max(0.0, float(values["gloss"]))) ** 0.5))
    assert abs(float(group.inputs["Roughness"].default_value) - rough) < 1e-5, "Glass roughness differs from gloss mapping"
    inner = group.node_tree.nodes
    assert inner.get("Glass alpha") and inner.get("Blend over background") and inner.get("SRC_ALPHA weight"), \
        "character_glass group lost its alpha/blend structure"
    from rc2016.pipeline.game_shader import character_glass_blend
    flags, (blend, attenuate) = character_glass_blend(material["source_material_path"])
    assert abs(float(group.inputs["Attenuate background"].default_value) - attenuate) < 1e-6 and \
        material.get("rc_glass_blend") == blend, "Glass blend differs from the material flags word"
    two_sided = bool(flags & 4)
    assert float(group.inputs["Two sided"].default_value) == (1.0 if two_sided else 0.0), \
        "Glass two-sided input differs from the material flags word (bit 2)"
    assert material.use_backface_culling == (not two_sided) and inner.get("Cull back faces"), \
        "Glass back-face culling differs from the material flags word (bit 2)"
    return dict(status="passed_partial_character_glass_native", family="character_glass",
                source_path=preflight["source_path"], source_sha256=preflight["source_sha256"],
                parameters=dict(values, reflection_tint_normalised=expected, blender_roughness=rough,
                                blend=blend, material_flags="0x%08X" % flags),
                limitation=material.get("rc_shader_frontend_limitation"))


def _validate_unknown_color_preview(source, material):
    bindings = source.get("textures", [])
    base_bindings = [row for row in bindings if row.get("role") == "base_color" and
                     (not row.get("source_references") or
                      any(not ref.get("graph_fallback") for ref in row.get("source_references", [])))]
    has_base = bool(base_bindings)
    expected_label = ("source_colour_only; unsupported graph equations" if has_base else
                      "no_base_color; unsupported graph equations")
    assert material.get("shader_preview_status") == expected_label, \
        "Unsupported shader graph is missing its labelled colour-only fallback status"
    nodes = material.node_tree.nodes
    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    assert output and output.inputs["Surface"].is_linked, "Colour-only preview has no active output"
    reached = set()
    pending = [output]
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        for socket in node.inputs:
            pending.extend(link.from_node for link in socket.links)
    assert not any(node.type == "GROUP" and node.node_tree and node.node_tree.name == DEFERRED_GROUP
                   for node in reached), "Unsupported graph entered the deferred BRDF shader"
    principled = next((node for node in reached if node.type == "BSDF_PRINCIPLED"), None)
    assert principled is not None, "Colour-only preview lacks its Principled surface"
    base = _from_node(principled.inputs["Base Color"])
    if has_base:
        base = _check_binding_image(source, base, "base_color")
        assert base in reached, "Colour-only preview lost the source base-colour image"
    else:
        assert not principled.inputs["Base Color"].is_linked, \
            "No-base fallback invented a source colour binding"
    for role in ("colored_specular_gloss", "normal"):
        rows = [row for row in source.get("textures", []) if row.get("role") == role]
        for binding in rows:
            node = _source_images(material).get(binding["path"])
            assert node not in reached, f"Unsupported graph interpreted {role} texture semantics"
    return dict(status="passed_unknown_graph_colour_only" if has_base else
                "passed_unknown_graph_no_base_colour", source_path=source.get("path"),
                limitation=("Only source base colour reaches output; shader equations are unsupported." if has_base else
                            "No source base-colour binding exists; all source images are disconnected from output."))


def _viewer_upstream(*sockets):
    """Every node upstream of the given input sockets."""
    seen, stack = {}, [link.from_node for socket in sockets for link in socket.links]
    while stack:
        node = stack.pop()
        if node.name in seen:
            continue
        seen[node.name] = node
        stack.extend(link.from_node for socket in node.inputs for link in socket.links)
    return list(seen.values())


# Source-rule parameters each forward glass writer front-end (game_shader._viewer_rule_glass) builds as 'VR glass <name>'.
GLASS_CONSTANTS = {
    "glass_basic": ("tint", "rim bias", "body", "gloss", "f0 base", "rim exp", "alpha scale"),
    "glass_jn": ("tint", "rim bias", "colour", "weight", "gloss", "f0 base", "rim exp", "alpha scale"),
    "glass_cg02": ("rim colour", "gloss", "body", "tint scale", "tint", "env scale", "rim exp", "rim mult", "rim cap",
                   "alpha floor", "env exp", "alpha exp"),
    "glass_thumper": ("tint", "rim bias", "body", "tiling", "f0 base", "gloss", "normal strength", "rim exp", "glow colour",
                      "amplitude"),
}


def _viewer_expected_constants(rule):
    """'VR <name>' constant -> value, derived from the source rule alone (rc_game_shader._ViewerNodes)."""
    terms, ported = rule.get("terms") or {}, set(rule.get("ported") or ())
    e = {}
    if "liquid_flow" in ported:
        flow = terms["liquid_flow"]
        e.update({"liquid flow U offset": flow["uv_offset"][0], "liquid flow V offset": flow["uv_offset"][1]})
    if "water" in ported:
        water = terms["water"]; p = water["params"]
        e.update({"water frozen time substitution": 0.0,
                  "water CB2 displacement parameter": water["sampling"]["displacement"],
                  "water API5 U scale": .105, "water API5 V scale": .095,
                  "water API6 U scale": .095, "water API6 V scale": .105,
                  "water API6 U offset": .248, "water API6 V offset": .613,
                  "water API7 U scale": .315, "water API7 V scale": .285,
                  "water API7 U offset": .133, "water API7 V offset": .346,
                  "water API8 U scale": .285, "water API8 V scale": .315,
                  "water inferred normal strength": p["normal_strength"],
                  "water inferred body colour": p["body"],
                  "water presentation F0": p["f0"], "water presentation gloss": p["gloss"],
                  "water presentation opacity": p["opacity"]})
    if "leaf_colour" in ported:
        l = terms["leaf_colour"]; e.update({"leaf exponent":l["exponent"], "leaf scale":l["scale"], "leaf palette":l["palette_rgb"]})
    if "fresnel_tint" in ported:
        f = terms["fresnel_tint"]; e.update({"mushroom fresnel exponent": f["exponent"], "mushroom fresnel colour": f["colour"]})
    if "hair" in ported:
        h = terms["hair"]; e.update({"hair albedo": h["albedo"], "hair spec colour": h["spec"], "hair rim exponent": h["rim_exponent"],
                                     "hair gloss scale": h["gloss_scale"], "hair normal strength": h["normal_strength"]})
    if "skin" in ported:
        s = terms["skin"]
        e.update({"skin fresnel exponent": s["exponent"], "skin albedo scale": s["albedo"], "skin fresnel tint": s["tint"],
                  "skin specular cap": s["cap"], "skin spec scale": s["spec"], "skin gloss scale": s["gloss"],
                  "skin spec fresnel": s["spec_fresnel"], "skin gloss fresnel": s["gloss_fresnel"]})
        if int(s["kind"]) == 2:
            e.update({"skin spec2": s["spec2"], "skin gloss2": s["gloss2"]})
    if "skin_detail" in ported:
        d = terms["skin_detail"]
        e.update({"skin detail uv scale": d["uv_scale"], "skin detail uv scale v": d["uv_scale"], "skin detail blend": d["blend"]})
    if "pick" in ported:
        p = terms["pick"]; e.update({"pick scale": p["scale"], "pick blend": p["blend"], "pick u": p["u"]})
    if "tint" in ported:
        t = terms["tint"]
        e.update({"tint A": t["a"], "tint B": t["b"], "tint power A": t["power_a"], "tint power B": t["power_b"],
                  "tint weight A": t["weight_a"], "tint weight B": t["weight_b"], "tint mask B": t["mask_b"]})
        if t.get("variant"):
            e.update({"tint palette": t["palette_rgb"], "tint palette blend": t["palette_blend"],
                      "tint detail scale": t["detail_scale"], "tint detail strength": t["detail_strength"],
                      "tint world origin": t["world_origin"]})
    if "spec_tint" in ported:
        e["spec tint"] = terms["spec_tint"]
    if "arch_sequence" in ported:
        a = terms["arch_sequence"]
        e.update({"arch colour": a["colour"], "arch linear": a["linear"], "arch power": a["power"],
                  "arch mask bias": a["mask_bias"], "arch phase": a["phase"]})
    if "vehicle" in ported:
        v = terms["vehicle"]; e.update({"vehicle A": v["a"], "vehicle B": v["b"], "vehicle blend": v["blend"], "vehicle rim": v["rim"]})
    if "glow" in ported:
        g = terms["glow"]
        e.update({"glow colour": g["colour"], "glow environment scale": g["environment_scale"], "glow depth": g["depth"],
                  "glow bias": g["bias"], "glow vertex multiplier":
                  1.0 + float(g.get("vertex_fade", 0.0)) * (abs(float(g.get("vertex_magnitude", 1.0))) - 1.0)})
    if "two_color_metal" in ported:
        m = terms["two_color_metal"]
        e.update({"metal diffuse A": m["diffuse_a"], "metal diffuse B": m["diffuse_b"], "metal spec A": m["spec_a"],
                  "metal spec B": m["spec_b"], "metal power": m["power"], "metal gloss power": m["gloss_power"],
                  "metal texture scale": m["texture_scale"], "metal normal strength": m["normal_strength"]})
    if "puddles" in ported:
        e.update({"puddle p%02d" % i: x for i, x in enumerate(terms["puddles"]["params"])})
        if terms["puddles"].get("variant") == "sludge":
            e.update({"sludge uv scale": terms["puddles"]["uv_scale"], "sludge dry gloss scale": terms["puddles"]["dry_gloss"],
                      "sludge dry F0 scale": terms["puddles"]["dry_f0"]})
    if "za_fresnel_power" in ported:
        e["za fresnel power"] = terms["za_fresnel_power"]
    if "rock_blend" in ported:
        r = terms["rock_blend"]
        e.update({"rock mask strength": r["mask_strength"], "rock sharpness": r["sharpness"]})
        if r.get("variant") == "color_breakup":
            e.update({"rock world tile": r["world_tile"], "rock layer uv scale": r["layer_uv_scale"]})
        else:
            e.update({"rock tint": r["tint"], "rock exponent": r["exponent"], "rock tint amount": r["tint_amount"]})
    if "grass_blend" in ported:
        r = terms["grass_blend"]
        e.update({"grass blend threshold": r["threshold"], "grass blend tile uv scale": r["uv_scale"],
                  "grass blend rock uv scale": r["layer_uv_scale"], "grass blend world tile": r["world_tile"]})
    if "mud_blend" in ported:
        r = terms["mud_blend"]
        e.update({"mud blend sharpness": r["sharpness"], "mud blend mask strength": r["mask_strength"],
                  "mud blend mud uv scale": r["mud_uv_scale"], "mud blend sand uv scale": r["sand_uv_scale"],
                  "mud blend world tile": r["world_tile"], "mud blend mud colour scale": 1.0 - r["mud_darken"],
                  "mud blend gloss scale": r["gloss_scale"], "mud blend grain uv scale": r["grain_uv_scale"],
                  "mud blend sand normal uv scale": r["sand_normal_uv_scale"], "mud blend sand normal strength": r["sand_normal_strength"],
                  "mud blend mud F0 factor": r["f0_scale"] * min(1.0, max(0.0, .3 + .3 * r["f0_bias"]))})
    if "coral_blend" in ported:
        r = terms["coral_blend"]
        e.update({"coral blend height width": r["height_width"], "coral blend water top": r["water_top"],
                  "coral blend water fade": r["water_fade"], "coral blend coral F0": r["coral_f0"],
                  "coral blend coral gloss": r["coral_gloss"], "coral blend base F0": r["base_f0"],
                  "coral blend base gloss": r["base_gloss"], "coral blend blur scale": r["noise_scale"][0],
                  "coral blend noise scale": r["noise_scale"][1], "coral blend smoke scale": r["noise_scale"][2],
                  "coral blend paint fallback red": r["paint_fallback"][0] ** 2,
                  "coral blend paint fallback green": r["paint_fallback"][1] ** 2})
    if "sediment_blend" in ported:
        r = terms["sediment_blend"]
        if r["kind"] == "gas":
            e.update({"sediment blend gradient start": r["gradient_start"], "sediment blend gradient end": r["gradient_end"],
                      "sediment blend weight start": r["weight_start"], "sediment blend weight end": r["weight_end"],
                      "sediment blend gloss scale": r["gloss_scale"], "sediment blend F0 scale": r["f0_scale"]})
            c, s = math.cos(1.0), math.sin(1.0)   # slag tangent normal (sx, sy, sz) -> native axes, per cube face (0440BD68 writer)
            columns = {0: dict(xp=(0, s, -c), xn=(0, s, c), yp=(c, 0, -s), yn=(c, 0, s), zp=(c, s, 0), zn=(c, -s, 0)),
                       1: dict(xp=(0, c, s), xn=(0, c, -s), yp=(-s, 0, -c), yn=(-s, 0, c), zp=(-s, c, 0), zn=(s, c, 0)),
                       2: dict(xp=(1, 0, 0), xn=(-1, 0, 0), yp=(0, 1, 0), yn=(0, -1, 0), zp=(0, 0, 1), zn=(0, 0, -1))}
            e.update({"sediment face %s column %d" % (face, k): vec for k, row in columns.items() for face, vec in row.items()})
        else:
            e.update({"sediment blend world tile": r["world_tile"], "sediment blend noise a": r["noise_a"],
                      "sediment blend noise b": r["noise_b"], "sediment blend height tile": r["height_tile"],
                      "sediment blend wall gloss scale": r["wall_gloss_scale"], "sediment blend cliff gloss scale": r["cliff_gloss_scale"],
                      "sediment blend F0 scale": r["f0_scale"], "sediment blend paint fallback red": r["paint_fallback"] ** 2})
    if "bng" in ported or "bng_detail" in ported:
        b = terms["bng"]
        e.update({"bng colour": b["colour"], **{"bng P%d" % i: x for i, x in enumerate(b["params"])},
                  "bng detail scale u": b["detail"][0], "bng detail scale v": b["detail"][1], "bng detail strength": b["detail"][2]})
    if "bright_center" in ported:
        b = terms["bright_center"]
        e.update({"bright centre edge": b["edge"], "bright centre colour": b["centre"], "bright centre exponent": b["exponent"],
                  "bright centre weight": b["weight"], "bright centre F0": b["f0"], "bright centre gloss": b["gloss_sq"]})
        if b.get("albedo_white"):
            e["bright centre albedo"] = [1.0, 1.0, 1.0]
    if "facing_glow" in ported:
        f = terms["facing_glow"]
        e.update({"facing glow albedo": f["albedo"], "facing glow bias": f["bias"], "facing glow exponent": f["exponent"],
                  "facing glow a scale": f["a_scale"], "facing glow a offset": f["a_offset"],
                  "facing glow amplitude": f["amplitude"], "facing glow g scale": f["g_scale"]})
    if "alpha_cut" in ported:
        e["alpha cut threshold"] = terms["alpha_cut"]
    if "bright_glow" in ported:
        p = terms["bright_glow"]["params"]
        e.update({"bright glow colour": p["colour"], "bright glow colour lo": p["colour_lo"], "bright glow dist_k": p["dist_k"],
                  "bright glow dist0": p["dist0"], "bright glow gain": p["gain"], "bright glow weight": p["weight"],
                  "bright glow exponent": p["exponent"], "bright glow amplitude": p["amplitude"]})
    if "eye_cornea" in ported:
        p = terms["eye_cornea"]["params"]
        e.update({"cornea fresnel normal scale": p["fresnel_normal_scale"], "cornea env normal scale": p["env_normal"],
                  "cornea fresnel bias": p["fresnel_bias"], "cornea fresnel power": p["fresnel_power"],
                  "cornea reflect base": p["reflect_base"], "cornea alpha bias": p["alpha_bias"],
                  "cornea reflect weight": p["reflect_weight"]})
    for key in ("glass_basic", "glass_cg02", "glass_jn", "glass_thumper"):
        if key in ported:
            p = terms[key]["params"]
            e.update({"glass " + name: p[name.replace(" ", "_")] for name in GLASS_CONSTANTS[key]})
            if key == "glass_cg02" and (rule.get("textures") or {}).get("_n"):
                e.update({"glass normal u": p["normal_u"], "glass normal v": p["normal_v"]})
    if "screen" in ported:
        p = terms["screen"]["params"]
        e.update({"screen grid": p["grid"], "screen alpha base": p["alpha_base"], "screen mask strength": p["mask_strength"],
                  "screen colour A": p["colour_a"], "screen colour B": p["colour_b"],
                  "screen intensity A": p["intensity_a"], "screen intensity B": p["intensity_b"]})
    if "flat_surface" in ported:
        f = terms["flat_surface"]
        e.update({"flat surface F0": f["f0"], "flat surface gloss": f["gloss"]})
        if f.get("model") == "basic_flat_color":
            e.update({"flat surface albedo zero": [0.0, 0.0, 0.0], "flat surface emission colour": f["colour"],
                      "flat surface emission scale": f["emission_scale"]})
        else:
            e.update({"flat surface colour": f["colour"], "flat surface self-lit mask": 1.0})
    if "number_display" in ported:
        p = terms["number_display"]["params"]
        e.update({"number distort": p["distort"], "number b scale": p["b_scale"], "number vignette w": p["vignette_w"],
                  "number kc": p["kc"], "number ko": p["ko"], "number amplitude": p["amplitude"]})
    if "ion_tubes" in ported:
        p = terms["ion_tubes"]["params"]
        e.update({"ion tubes tint": p["colour"], "ion tubes gain": p["gain"], "ion tubes amplitude": p["amplitude"],
                  "ion tubes base": p["base"], "ion tubes rim": p["rim"], "ion tubes time": p["time"], "ion tubes fade": p["fade"]})
    if "victor_reticle" in ported:
        p = terms["victor_reticle"]["params"]
        e.update({"Victor reticle CB2[0] material UV phase": p["uv_phase"],
                  "Victor reticle CB2[1] API8 gain": p["texture_gain"],
                  "Victor reticle CB2[2] ring tint": p["ring_tint"],
                  "Victor reticle CB0[115] presentation amplitude": p["amplitude"],
                  "Victor reticle EB9C8254 F0": p["f0"],
                  "Victor reticle EB9C8254 gloss": p["gloss"]})
    return {"VR " + name: value for name, value in e.items()}


def _viewer_required(rule, has_n):
    """(constants, image roles) each ported term must have reaching the output."""
    terms, ported = rule.get("terms") or {}, set(rule.get("ported") or ())
    tex = rule.get("textures") or {}
    names, roles = [], []
    req = lambda *rs: roles.extend(r for r in rs if tex.get(r))
    if "liquid_flow" in ported:
        names += ["liquid flow U offset", "liquid flow V offset"]; req("_c", "_g")
    if "water" in ported:
        names += ["water frozen time substitution", "water CB2 displacement parameter",
                  "water API5 U scale", "water API5 V scale", "water API6 U scale", "water API6 V scale",
                  "water API6 U offset", "water API6 V offset", "water API7 U scale", "water API7 V scale",
                  "water API7 U offset", "water API7 V offset", "water API8 U scale", "water API8 V scale",
                  "water inferred normal strength",
                  "water inferred body colour", "water presentation F0", "water presentation gloss", "water presentation opacity"]
        req("_water_height_a", "_water_height_b", "_n", "_pick")
    if "leaf_colour" in ported:
        names += ["leaf exponent", "leaf scale", "leaf palette"]; req("_cma")
    if "fresnel_tint" in ported:
        names += ["mushroom fresnel exponent", "mushroom fresnel colour"]; req("_c")
    if "hair" in ported:
        names += ["hair albedo", "hair spec colour", "hair rim exponent", "hair gloss scale", "hair normal strength"]; req("_c", "_g")
    if "skin" in ported:
        names += ["skin fresnel exponent", "skin albedo scale", "skin fresnel tint"]
        if tex.get("_g"):
            names += ["skin specular cap", "skin spec scale", "skin gloss scale", "skin spec fresnel", "skin gloss fresnel"]
            if int(terms["skin"]["kind"]) == 2:
                names += ["skin spec2", "skin gloss2"]
    if "skin_detail" in ported and has_n:
        names += ["skin detail uv scale", "skin detail uv scale v", "skin detail blend"]; req("_bng_detail")
    if "pick" in ported and "vehicle" not in ported and "tint" not in ported:
        names += ["pick u", "pick scale", "pick blend"]; req("_pick")
    if "tint" in ported:
        t = terms["tint"]; names += ["tint A", "tint B", "tint weight A"]; req("_h")
        if t.get("variant"):
            names.append("tint palette blend")
        if t.get("variant") in ("world", "world_normal"):
            names += ["tint world origin", "tint detail scale"]; req("_tint_detail")
        if t.get("variant") == "detail" and has_n:
            names.append("tint detail strength"); req("_tint_detail")
        if t.get("variant") == "world_normal" and has_n and tex.get("_pick"):
            names.append("tint detail strength"); req("_pick")
    lit = float(rule.get("k", 0.0)) > 0.0
    if "arch_sequence" in ported:
        names += ["arch colour", "arch power"]; req("_h")
        if float(terms["arch_sequence"]["intensity"]) > 0.0:   # self-lit K = 5 * intensity
            names += ["arch mask bias"] + (["arch phase"] if tex.get("_sequence") else []); req("_sequence", "_pick")
    if "vehicle" in ported:
        names += ["vehicle A", "vehicle B", "vehicle blend", "vehicle rim"]; req("_pick", "_vehicle_p2", "_h")
    if "glow" in ported:
        kind = int(terms["glow"]["kind"])
        names += ["glow colour", "glow environment scale"] if kind == 1 else []
        names += (["glow bias"] if kind == 1 else ["glow vertex multiplier"]) if lit else []
        if kind == 1 and has_n and lit:
            names.append("glow depth")
        req(*(("_h",) if kind == 1 or lit else ()), *(("_glow_parallax",) if kind == 1 and lit else ()))
    if "two_color_metal" in ported:
        names += ["metal diffuse A", "metal diffuse B", "metal spec A", "metal spec B", "metal power", "metal gloss power",
                  "metal texture scale"] + (["metal normal strength"] if has_n else [])
    if "puddles" in ported:
        sludge = terms["puddles"].get("variant") == "sludge"   # CC985D80: the noise coordinate has no rotation (p14)
        names += ["puddle p%02d" % i for i in (0, 2, 3, 4, 6, 8, 9, 10, 11) + (() if sludge else (14,))]
        names += ["puddle p01", "puddle p07", "puddle p13"] if has_n else []
        if sludge:
            names += ["sludge uv scale", "sludge dry gloss scale", "sludge dry F0 scale"]
        req("_puddle_noise", *(("_puddle_ripple",) if has_n else ()))
    if "za_fresnel_power" in ported:
        names.append("za fresnel power")
    if "rock_blend" in ported:
        if terms["rock_blend"].get("variant") == "color_breakup":
            names += ["rock mask strength", "rock sharpness", "rock layer uv scale"] + (["rock world tile"] if tex.get("_c") else [])
            req("_rock_mask", "_rock_g2", *(("_rock_n2",) if has_n else ()))
        else:
            names += ["rock tint", "rock exponent", "rock tint amount", "rock mask strength", "rock sharpness"]
            req("_rock_mask", "_rock_c2", "_rock_g2", *(("_rock_n2",) if has_n else ()))
    if "grass_blend" in ported:
        names += ["grass blend threshold", "grass blend tile uv scale", "grass blend rock uv scale", "grass blend world tile"]
        req("_grass_r", "_grass_g", "_grass_g9")
    if "mud_blend" in ported:
        names += ["mud blend sharpness", "mud blend mask strength", "mud blend mud uv scale", "mud blend sand uv scale",
                  "mud blend world tile", "mud blend mud colour scale", "mud blend gloss scale", "mud blend mud F0 factor"]
        if has_n:
            names += ["mud blend grain uv scale", "mud blend sand normal uv scale", "mud blend sand normal strength"]
        req("_mud_h", "_mud_c", "_mud_g2", *(("_mud_n7", "_mud_n8") if has_n else ()))
    if "coral_blend" in ported:
        names += ["coral blend height width", "coral blend water top", "coral blend water fade", "coral blend coral F0",
                  "coral blend coral gloss", "coral blend base F0", "coral blend base gloss", "coral blend blur scale",
                  "coral blend noise scale", "coral blend smoke scale", "coral blend paint fallback red", "coral blend paint fallback green"]
        req("_coral_n_wavy", "_coral_n_coral", "_coral_c_wavy", "_coral_c_rock", "_coral_g_rock", "_coral_g_sand", "_coral_cma",
            "_caustic_blur", "_caustic_noise", "_caustic_smoke")
    if "sediment_blend" in ported:
        if terms["sediment_blend"]["kind"] == "gas":
            names += ["sediment blend gradient start", "sediment blend gradient end", "sediment blend weight start", "sediment blend weight end",
                      "sediment blend gloss scale", "sediment blend F0 scale"]
            req("_sed_slag_n", "_sed_noise", "_sed_gm", "_sed_slag_cma", "_sed_slag_gma")
        else:
            names += ["sediment blend world tile", "sediment blend noise a", "sediment blend noise b", "sediment blend height tile",
                      "sediment blend wall gloss scale", "sediment blend cliff gloss scale", "sediment blend F0 scale",
                      "sediment blend paint fallback red"]
            req("_sed_wall_g", "_sed_wall_n", "_sed_noise", "_sed_wall_c", "_sed_sediment")
    if "bng" in ported:
        kind = int(terms["bng"]["kind"])
        names += ["bng colour"] if kind == 1 or (kind == 4 and lit) else [] if kind in (4, 10) else ["bng P0", "bng P1"] if kind == 7 else (["bng P0", "bng P1"] + (["bng P2"] if kind == 6 else []) if has_n else [])
        if kind in (9, 10):
            req("_h")
    if "bng_detail" in ported and has_n:
        names += ["bng detail scale u", "bng detail scale v", "bng detail strength"]; req("_bng_detail")
    if "bright_center" in ported:
        names += ["bright centre edge", "bright centre colour", "bright centre exponent", "bright centre weight",
                  "bright centre F0", "bright centre gloss"]
        if terms["bright_center"].get("albedo_white"):
            names.append("bright centre albedo")
    if "facing_glow" in ported:
        names += ["facing glow albedo", "facing glow bias", "facing glow exponent", "facing glow a scale",
                  "facing glow a offset", "facing glow amplitude", "facing glow g scale"]
    if "alpha_cut" in ported:
        names.append("alpha cut threshold"); req("_c")
    if "bright_glow" in ported:
        names += ["bright glow colour", "bright glow colour lo", "bright glow dist_k", "bright glow dist0", "bright glow gain",
                  "bright glow weight", "bright glow exponent", "bright glow amplitude"]
    if "eye_cornea" in ported:
        names += ["cornea fresnel normal scale", "cornea env normal scale", "cornea fresnel bias", "cornea fresnel power",
                  "cornea reflect base", "cornea alpha bias", "cornea reflect weight"]
        req("_n")
    for key in ("glass_basic", "glass_cg02", "glass_jn", "glass_thumper"):
        if key in ported:
            glass = terms[key]
            names += ["glass " + name for name in GLASS_CONSTANTS[key]
                      if not (glass.get("blend") == "opaque" and
                              name in (("alpha floor",) if key == "glass_cg02" else ("rim bias", "alpha scale")))]
            if key == "glass_cg02" and tex.get("_n"):
                names += ["glass normal u", "glass normal v"]; req("_n")
            if key == "glass_thumper":
                if not tex.get("_n"):
                    names.remove("glass normal strength")
                req("_n", "_pick")
    if "screen" in ported:
        names += ["screen alpha base", "screen mask strength", "screen colour A", "screen colour B", "screen intensity A",
                  "screen intensity B"] + (["screen grid"] if tex.get("_n") else [])
        req("_c", "_n", "_pick", "_g")
    if "flat_surface" in ported:
        if terms["flat_surface"].get("model") == "basic_flat_color":
            names += ["flat surface albedo zero", "flat surface emission colour", "flat surface emission scale",
                      "flat surface F0", "flat surface gloss"]
        else:
            names += ["flat surface colour", "flat surface F0", "flat surface gloss"] + (["flat surface self-lit mask"] if lit else [])
    if "number_display" in ported:
        names += ["number distort", "number b scale", "number vignette w", "number kc", "number ko", "number amplitude"]
        req("_c", "_n", "_pick", "_g")
    if "ion_tubes" in ported:
        names += ["ion tubes tint", "ion tubes gain", "ion tubes amplitude", "ion tubes base", "ion tubes rim", "ion tubes time", "ion tubes fade"]
        req("_pick")
    if "victor_reticle" in ported:
        names += ["Victor reticle CB2[0] material UV phase", "Victor reticle CB2[1] API8 gain",
                  "Victor reticle CB2[2] ring tint", "Victor reticle CB0[115] presentation amplitude"]
        req("_reticle")
    return ["VR " + name for name in names], roles


def _viewer_check_constants_and_images(source, material, rule, upstream, has_n):
    expected = _viewer_expected_constants(rule)
    nodes = material.node_tree.nodes
    for node in nodes:
        if node.name.startswith("VR ") and node.type in ("VALUE", "COMBXYZ"):
            assert node.name in expected, f"Viewer-rule constant {node.name} has no source rule value"
            want = expected[node.name]
            got = ([node.outputs[0].default_value] if node.type == "VALUE" else [s.default_value for s in node.inputs[:3]])
            want = [want] if not isinstance(want, (list, tuple)) else list(want)
            assert all(abs(float(a) - float(b)) <= 1e-5 * max(1.0, abs(float(b))) for a, b in zip(got, want)), \
                f"Viewer-rule constant {node.name} differs from the source rule"
    names, roles = _viewer_required(rule, has_n)
    reached = {node.name for node in upstream}
    for name in names:
        assert name in reached, f"Viewer-rule term constant {name} does not reach the active output"
    rows = {row.get("path", "").lower(): row for row in source.get("textures", [])}
    for node in upstream:
        if node.type == "TEX_IMAGE":
            path = str(node.get("source_texture_path", "")).lower()
            assert path in rows, f"Viewer-rule image {path} is not a source binding"
            assert node.get("source_texture_sha256") == rows[path].get("dds_sha256"), f"{path} source hash changed"
            assert node.image.colorspace_settings.name == rows[path]["colorspace"], f"{path} colourspace differs from native DXGI"
    for role in roles:
        path = (rule["textures"].get(role) or "").lower()
        assert any(node.type == "TEX_IMAGE" and str(node.get("source_texture_path", "")).lower() == path for node in upstream), \
            f"Viewer-rule term image {role} ({path}) does not reach the active output"
    terms = rule.get("terms") or {}
    for source_path in ((terms.get("tint") or {}).get("palette_source"), (terms.get("screen") or {}).get("timeline_source"),
                        (terms.get("leaf_colour") or {}).get("palette_source")):
        if source_path:   # CPU-sampled constants (texture_preview.tint_palette_sample), as in the viewer
            assert source_path.lower() in rows and rows[source_path.lower()].get("dds_sha256"), \
                f"Viewer constant sample source {source_path} is not a hashed source binding"
    return sorted(reached & set(expected))


def _validate_viewer_rule_water(source, material, rule, output, has_n):
    """Reopened structural checks for the bounded water sampling preview."""
    from rc2016.shaders.water import WATER_GRAPH, WATER_GRAPH_SHA256, GLOW_GRAPH, GLOW_GRAPH_SHA256, WATER_WRITERS, GLOW_WRITERS
    term = rule["terms"]["water"]; sampling = term["sampling"]
    graph_path, graph_sha = term.get("graph_path"), term.get("graph_sha256")
    assert (graph_path, graph_sha) in ((WATER_GRAPH, WATER_GRAPH_SHA256), (GLOW_GRAPH, GLOW_GRAPH_SHA256)), \
        "Water preview graph identity is not one of the source-decoded graph hashes"
    writer_sha = sampling.get("writer_sha256")
    accepted = WATER_WRITERS if graph_path == WATER_GRAPH else GLOW_WRITERS
    assert writer_sha in accepted and term.get("writer", "").lower() == accepted[writer_sha]["shader_id"], \
        "Water preview writer identity is not source-bound to its graph"
    assert sampling.get("graph_path") == graph_path and sampling.get("graph_sha256") == graph_sha, \
        "Water sampling contract graph identity differs from the forward term"
    expression = sampling.get("sampling", {})
    assert expression.get("displacement_parameter_key") == "0x230C6389", \
        "Water height cross displacement is not bound to material key 0x230C6389"
    assert sampling.get("displacement") is not None and "height_uvs_by_api" in expression and \
        "height_cross_displacement" in expression and "primary_normal_uvs_by_api" in expression, \
        "Water source-decoded sampling contract is incomplete"

    upstream = _viewer_upstream(output.inputs["Surface"])
    names = {node.name for node in upstream}
    labels = {node.label for node in upstream}
    for name in ("VR water API5 height sample", "VR water API6 height sample",
                 "VR water API7 primary normal sample", "VR water API8 primary normal sample",
                 "VR water presentation deferred BRDF", "VR water inferred alpha-over",
                 "VR water transparent background", "water source height cross displacement",
                 "water inferred Schlick coverage Fresnel", "water inferred Fresnel opacity"):
        assert name in names or name in labels, "Water sampling or closure node is missing from the output: " + name
    from rc2016.shaders.water import water_height_sample_uvs, water_primary_normal_sample_uvs
    sample_names = {5: "VR water API5 height sample", 6: "VR water API6 height sample",
                    7: "VR water API7 primary normal sample", 8: "VR water API8 primary normal sample"}
    controlled_heights = {sample_names[5]: (0.2, 0.2, 0.2), sample_names[6]: (0.7, 0.7, 0.7)}
    for position in ((2.0, 0.0, -3.0), (-1.1, 4.0, 0.37)):
        expected_uvs = water_height_sample_uvs(position[0], position[2])
        expected_uvs.update(water_primary_normal_sample_uvs(position[0], position[2], 0.2, 0.7,
                                                          displacement=sampling["displacement"]))
        for api, name in sample_names.items():
            image = material.node_tree.nodes[name]
            got = _viewer_uv_value(image.inputs["Vector"], (0.23, 0.61, 0.0), controlled_heights, position)
            u, v = expected_uvs[api]
            assert max(abs(a-b) for a,b in zip(got, (u, 1.0-v, 0.0))) < 1e-6, \
                "Water source sample UV differs from the decoded first branch (including V orientation)"
    brdf = next(node for node in upstream if node.name == "VR water presentation deferred BRDF")
    assert brdf.type == "GROUP" and brdf.node_tree and brdf.node_tree.name == DEFERRED_GROUP, \
        "Water preview does not use the shared deferred BRDF"
    assert all(brdf.inputs[index].is_linked for index in (0, 2, 3, 4)), \
        "Water inferred albedo/F0/gloss/normal are not connected to the deferred BRDF"
    assert abs(float(brdf.inputs[1].default_value)) < 1e-7 and \
        abs(float(brdf.inputs[5].default_value) - 1.0) < 1e-7 and \
        abs(float(brdf.inputs[6].default_value) - 1.0) < 1e-7 and \
        abs(float(brdf.inputs[7].default_value)) < 1e-7, \
        "Water deferred BRDF self-lit/specular/gloss scales differ from the bounded preview contract"
    assert brdf.inputs[3].links[0].from_node.operation == "SQRT", \
        "Water presentation gloss is not square-rooted before the shared deferred BRDF"
    assert term.get("blend") == "alpha_over" and not term.get("two_sided"), \
        "Water source render state must be one-sided alpha-over"
    assert "VR cull back faces" in names and material.use_backface_culling, \
        "Water one-sided material culling is missing"
    if hasattr(material, "surface_render_method"):
        assert material.surface_render_method == "BLENDED", "Water alpha-over material is not configured as blended"
    constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
    return dict(status="passed_viewer_graph_rule_frontend", terms=["water"], blend="alpha_over",
                writer=term.get("writer"), graph_path=graph_path, graph_sha256=graph_sha,
                sampling_status=sampling.get("status"), constants=constants,
                limitation=material.get("rc_shader_frontend_limitation"))


def _viewer_uv_value(socket, uv, texture_values=None, position=None):
    """Evaluate the source-rule UV arithmetic at a native UV probe after reopening."""
    if not socket.is_linked:
        value = socket.default_value
        return tuple(value) if hasattr(value, '__len__') else float(value)
    source = socket.links[0].from_socket
    node = source.node
    value = lambda s: _viewer_uv_value(s, uv, texture_values, position)
    if node.type == 'TEX_COORD':
        assert source.name == 'Object' and position is not None, "Unexpected source-rule coordinate input"
        return position
    if node.type == 'TEX_IMAGE':
        assert texture_values is not None and node.name in texture_values, "Unexpected source-rule sample in UV transform"
        return texture_values[node.name]
    if node.type == 'UVMAP':
        assert node.uv_map == 'UV0', "Source rule uses another UV layer"
        return uv
    if node.type == 'VALUE':
        return float(source.default_value)
    if node.type == 'SEPXYZ':
        return value(node.inputs[0])[list(node.outputs).index(source)]
    if node.type == 'COMBXYZ':
        return tuple(value(s) for s in node.inputs[:3])
    if node.type == 'MATH':
        a, b = (value(s) for s in node.inputs[:2])
        assert node.operation in ('ADD', 'SUBTRACT', 'MULTIPLY', 'MULTIPLY_ADD'), "Unexpected source-rule UV arithmetic"
        return {'ADD': lambda: a+b, 'SUBTRACT': lambda: a-b, 'MULTIPLY': lambda: a*b,
                'MULTIPLY_ADD': lambda: a*b + value(node.inputs[2])}[node.operation]()
    raise AssertionError('Unexpected node in source-rule UV transform: ' + node.type)


def _validate_viewer_rule_material(source, material):
    """rc_game_shader.apply_viewer_graph_rule: the asset viewer's per-graph reading on the deferred BRDF."""
    rule = source.get("viewer_graph_rule") or {}
    assert rule.get("applies"), "Viewer-rule material without an applicable source rule"
    assert json.loads(material["rc_viewer_graph_rule"]) == rule, "Reopened viewer rule differs from the material report"
    ported = list(rule.get("ported") or ())
    if "parallax" in rule.get("terms", {}):
        assert "parallax" not in ported and "parallax" in rule.get("unported", ()), "Blender POM must remain explicitly unported"
        assert "parallax" in str(material.get("rc_shader_frontend_limitation", "")), "Missing visible Blender POM limitation"
        assert rule.get("normal_z") == "1-x*x-y*y", "POM source normal z semantics lost"

    nodes = material.node_tree.nodes
    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    assert output and output.inputs["Surface"].is_linked, "Viewer-rule material has no active surface output"
    sources = {row.get("path", "").lower() for row in source.get("textures", [])}
    has_n = bool(rule["textures"].get("_n")) and rule["textures"]["_n"].lower() in sources
    if "lava_surface" in ported:
        from rc2016.shaders.lava_surface import valid_contract
        from rc2016.shaders.nodes import tree_signature
        receipt = rule["terms"]["lava_surface"]
        assert valid_contract(receipt), "Invalid partial lava source contract"
        assert tree_signature(material.node_tree) == material.get("rc_lava_surface_tree_sha256"), "Lava source nodes or images changed"
        front = nodes.get("VR lava source surface")
        assert front and front.node_tree.get("translation_sha256") == receipt["translation_sha256"], "Lava source DAG identity changed"
        upstream = _viewer_upstream(output.inputs["Surface"])
        assert front in upstream, "Lava source surface disconnected"
        assert material.use_backface_culling == (not receipt['two_sided']), "Lava source cull flag changed"
        assert receipt['two_sided'] or any(n.name == 'VR cull back faces' for n in upstream), "Lava backface closure missing"
        expected = {row["path"].lower(): row for row in source.get("textures", [])}
        sampled = [n for n in front.node_tree.nodes if n.type == "TEX_IMAGE"]
        for binding in receipt["texture_bindings"]:
            images = [n.image for n in sampled if n.get("source_texture_path") == binding["path"]]
            assert images, "Lava source texture absent"
            original = expected[binding["path"].lower()]
            for image in images:
                assert image.packed_file, "Lava source image not packed"
                assert image.get("source_texture_sha256") == original["dds_sha256"], "Lava source image hash changed"
                assert image.alpha_mode == "CHANNEL_PACKED", "Lava alpha is not channel packed"
                assert image.colorspace_settings.name == original["colorspace"], "Lava source colorspace changed"
        assert material.get("game_equivalence") is False, "Partial lava rule claims game equivalence"
        for limitation in receipt["limitations"]:
            assert limitation in material.get("rc_shader_frontend_limitation", ""), "Lava limitation missing"
        return dict(status="passed_viewer_graph_rule_frontend", terms=["lava_surface"],
                    limitation=material.get("rc_shader_frontend_limitation"))
    if "water" in ported:
        return _validate_viewer_rule_water(source, material, rule, output, has_n)
    if "screen" in ported:
        upstream = _viewer_upstream(output.inputs["Surface"])
        blend = rule["terms"]["screen"].get("blend")
        names = {node.name for node in upstream}
        assert "VR screen colour (unlit)" in names and "VR screen blend %s" % blend in names, "Screen emission/blend is not the output"
        assert rule["terms"]["screen"].get("two_sided") or "VR cull back faces" in names, "Screen back faces are not culled"
        constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
        return dict(status="passed_viewer_graph_rule_frontend", terms=ported, blend=blend, constants=constants,
                    limitation=material.get("rc_shader_frontend_limitation"))
    if "eye_cornea" in ported:   # Surface = Cull(Mix(alpha, Transparent, Glossy(weight)))
        upstream = _viewer_upstream(output.inputs["Surface"])
        names = {node.name for node in upstream}
        forward = rule["terms"]["eye_cornea"]
        assert {"VR cornea glossy", "VR cornea blend %s" % forward.get("blend")} <= names, "Cornea glossy/blend is not the output"
        assert forward.get("blend") == "alpha_over", "Cornea source blend is not alpha-over"
        assert forward.get("two_sided") or "VR cull back faces" in names, "Cornea back faces are not culled"
        assert material.use_backface_culling == (not forward.get("two_sided")), "Cornea material back-face culling differs from the flags word"
        if hasattr(material, "surface_render_method"):
            assert material.surface_render_method == "BLENDED", "Cornea material is not blended"
        constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
        return dict(status="passed_viewer_graph_rule_frontend", terms=ported, blend="alpha_over", constants=constants,
                    limitation=material.get("rc_shader_frontend_limitation"))
    if "number_display" in ported:   # Surface = Cull(Emission(3.5 amp T (2 Kc G + Ko))), opaque
        upstream = _viewer_upstream(output.inputs["Surface"])
        names = {node.name for node in upstream}
        two_sided = rule["terms"]["number_display"].get("two_sided")
        assert "VR number colour (unlit)" in names, "Number display emission is not the output"
        assert rule["terms"]["number_display"].get("blend") == "opaque", "Number display source blend is not opaque"
        assert two_sided or "VR cull back faces" in names, "Number display back faces are not culled"
        constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
        return dict(status="passed_viewer_graph_rule_frontend", terms=ported, blend="opaque", constants=constants,
                    limitation=material.get("rc_shader_frontend_limitation"))
    if "ion_tubes" in ported:   # Surface = Cull(Emission(2 amp (1 + m) (base + rim F^5 + m C g))), opaque
        upstream = _viewer_upstream(output.inputs["Surface"])
        names = {node.name for node in upstream}
        two_sided = rule["terms"]["ion_tubes"].get("two_sided")
        assert "VR ion tubes colour (unlit)" in names, "Ion tubes emission is not the output"
        assert rule["terms"]["ion_tubes"].get("blend") == "opaque", "Ion tubes source blend is not opaque"
        assert two_sided or "VR cull back faces" in names, "Ion tubes back faces are not culled"
        constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
        return dict(status="passed_viewer_graph_rule_frontend", terms=ported, blend="opaque", constants=constants,
                    limitation=material.get("rc_shader_frontend_limitation"))
    glass = next((key for key in ("glass_basic", "glass_cg02", "glass_jn", "glass_thumper") if key in ported), None)
    if glass:   # Surface = Cull(blend(alpha; lit = diffuse/emission + F90 * Schlick-GGX))
        upstream = _viewer_upstream(output.inputs["Surface"])
        names = {node.name for node in upstream}
        term = rule["terms"][glass]
        blend, two_sided = term.get("blend"), term.get("two_sided")
        assert {"VR glass lit", "VR glass specular", "VR glass weight"} <= names, "Glass lit/specular terms are not the output"
        assert (blend == "opaque" or ("VR glass blend additive" if blend == "additive" else "VR glass alpha weight") in names), \
            "Glass blend node differs from the flags word"
        assert blend in ("opaque", "additive", "alpha_over"), "Glass blend is not decoded"
        assert two_sided or "VR cull back faces" in names, "Glass back faces are not culled"
        assert material.use_backface_culling == (not two_sided), "Glass material back-face culling differs from the flags word"
        if hasattr(material, "surface_render_method"):
            assert material.surface_render_method == ("DITHERED" if blend == "opaque" else "BLENDED"), "Glass render method differs from the blend"
        constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
        return dict(status="passed_viewer_graph_rule_frontend", terms=ported, blend=blend, constants=constants,
                    limitation=material.get("rc_shader_frontend_limitation"))
    if "bright_glow" in ported:   # Surface = Cull(Add(Transparent, Emission(Clo + (C - Clo) x^2, 7 amp sat(x))))
        upstream = _viewer_upstream(output.inputs["Surface"])
        names = {node.name for node in upstream}
        two_sided = rule["terms"]["bright_glow"].get("two_sided")
        assert {"VR bright glow emission", "VR bright glow blend additive", "VR camera distance"} <= names, \
            "Bright glow emission/additive blend/camera distance is not the output"
        assert rule["terms"]["bright_glow"].get("blend") == "additive", "Bright glow source blend is not additive"
        assert two_sided or "VR cull back faces" in names, "Bright glow back faces are not culled"
        assert material.use_backface_culling == (not two_sided), "Bright glow material back-face culling differs from the flags word"
        if hasattr(material, "surface_render_method"):
            assert material.surface_render_method == "BLENDED", "Bright glow material is not blended"
        constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
        return dict(status="passed_viewer_graph_rule_frontend", terms=ported, blend="additive", constants=constants,
                    limitation=material.get("rc_shader_frontend_limitation"))
    flat = (rule.get("terms") or {}).get("flat_surface") or {}
    if "flat_surface" in ported and flat.get("model") == "basic_flat_color":
        surface = _from_node(output.inputs["Surface"])
        assert surface and surface.type == "ADD_SHADER" and surface.name == "VR basic_flat_color additive source term", \
            "basic_flat_color source emission is not added beside the BRDF"
        brdf = _from_node(surface.inputs[0]); emission = _from_node(surface.inputs[1])
        assert brdf and brdf.type == "GROUP" and brdf.node_tree and brdf.node_tree.name == DEFERRED_GROUP, \
            "basic_flat_color deferred BRDF is not the first additive input"
        assert emission and emission.type == "EMISSION" and emission.name == "VR basic_flat_color source emission", \
            "basic_flat_color source term is not represented by an emission shader"
        assert {node.name for node in _viewer_upstream(brdf.inputs[0])} == {"VR flat surface albedo zero"}, \
            "basic_flat_color albedo is not the zero source constant"
        assert abs(float(brdf.inputs[7].default_value)) < 1e-7 and not brdf.inputs[1].is_linked, \
            "basic_flat_color additive term was routed through the BRDF self-lit mask"
        constants = _viewer_check_constants_and_images(source, material, rule,
            _viewer_upstream(*brdf.inputs, *emission.inputs), has_n)
        return dict(status="passed_viewer_graph_rule_frontend", terms=ported, constants=constants,
                    limitation=material.get("rc_shader_frontend_limitation"))
    if "victor_reticle" in ported:
        term = rule["terms"]["victor_reticle"]; params = term["params"]
        assert term.get("writer") == "EB9C8254", "Victor reticle writer identity differs from the bounded source rule"
        assert term.get("graph_path") == "materialgraph/visualeffect/character/enemy/enm_victor_reticle/enm_victor_reticle.materialgraph" and \
            term.get("graph_sha256") == "e4bcaf552be55cbc14b5081b6695a600084ae162889455b87eadc6c0103d79ea", \
            "Victor reticle graph identity differs from the bounded source rule"
        composite = _from_node(output.inputs["Surface"])
        assert composite and composite.type == "ADD_SHADER" and composite.name == "VR Victor reticle additive composite", \
            "Victor reticle material flags are not represented by the additive composite"
        clear = _from_node(composite.inputs[0]); brdf = _from_node(composite.inputs[1])
        assert clear and clear.type == "BSDF_TRANSPARENT" and clear.name == "VR Victor reticle additive base", \
            "Victor reticle additive composite has no transparent base"
        assert brdf and brdf.type == "GROUP" and brdf.node_tree and brdf.node_tree.name == DEFERRED_GROUP, \
            "Victor reticle does not use the shared deferred BRDF"
        assert brdf.inputs[0].is_linked and brdf.inputs[1].is_linked and brdf.inputs[2].is_linked and brdf.inputs[3].is_linked, \
            "Victor reticle albedo/F0/gloss/self-lit terms are not connected to the shared BRDF"
        albedo_upstream = _viewer_upstream(brdf.inputs[0])
        albedo_labels = {node.label: node for node in albedo_upstream}
        for label in ("Victor reticle radial distance length", "Victor reticle b = 1 - distance",
                      "Victor reticle e = abs(b)", "Victor reticle MRT1 R = 2*A*e",
                      "Victor reticle MRT1 G ring equation", "Victor reticle MRT1 B ring equation",
                      "Victor reticle UNORM lower clamp", "Victor reticle UNORM upper clamp"):
            assert label in albedo_labels, "Victor reticle source albedo formula is missing from deferred albedo: " + label
        assert albedo_labels["Victor reticle radial distance length"].operation == "LENGTH" and \
            albedo_labels["Victor reticle b = 1 - distance"].operation == "SUBTRACT" and \
            albedo_labels["Victor reticle e = abs(b)"].operation == "ABSOLUTE", \
            "Victor reticle radial albedo source operations changed"
        assert albedo_labels["Victor reticle MRT1 R = 2*A*e"].operation == "MULTIPLY" and \
            albedo_labels["Victor reticle MRT1 G ring equation"].operation == "MULTIPLY" and \
            albedo_labels["Victor reticle MRT1 B ring equation"].operation == "MULTIPLY", \
            "Victor reticle MRT1 color equation outputs changed"
        assert albedo_labels["Victor reticle UNORM lower clamp"].operation == "MAXIMUM" and \
            albedo_labels["Victor reticle UNORM upper clamp"].operation == "MINIMUM", \
            "Victor reticle deferred albedo no longer clamps to the UNORM range"
        emission_upstream = _viewer_upstream(brdf.inputs[1])
        emission_labels = {node.label: node for node in emission_upstream}
        for label, operation in (("Victor reticle emission argument = 1 + API8.R*gain", "ADD"),
                                 ("Victor reticle API8 phase offset = 8*CB2[0]", "MULTIPLY"),
                                 ("Victor reticle API8 v = 8 - phase offset", "SUBTRACT"),
                                 ("Victor reticle source log2", "LOGARITHM"),
                                 ("Victor reticle source 0.20000000298*log2", "MULTIPLY"),
                                 ("Victor reticle source exponent 5*payload", "MULTIPLY"),
                                 ("Victor reticle source exp2", "POWER"),
                                 ("Victor reticle EB9C8254 source self-lit scalar", "SUBTRACT")):
            assert label in emission_labels and emission_labels[label].operation == operation, \
                "Victor reticle source emission math is missing or changed: " + label
        assert abs(float(brdf.inputs[7].default_value) - 1.0) < 1e-7, "Victor reticle emission must use its source scalar directly"
        upstream = _viewer_upstream(*brdf.inputs)
        names = {node.name for node in upstream}
        assert "VR Victor reticle API8 source sample; t=0" in names, "Victor reticle deferred emission does not sample API8"
        assert "VR Victor reticle EB9C8254 F0" in names and "VR Victor reticle EB9C8254 gloss" in names, \
            "Victor reticle MRT2 F0/gloss values are not in the BRDF"
        amplitude = next((node for node in upstream if node.name == "VR Victor reticle CB0[115] presentation amplitude"), None)
        assert amplitude and abs(float(amplitude.outputs[0].default_value) - float(params["amplitude"])) < 1e-6, \
            "Victor reticle amplitude is missing or differs from its labelled preview input"
        f0_node = next((node for node in upstream if node.name == "VR Victor reticle EB9C8254 F0"), None)
        assert f0_node and tuple(float(f0_node.inputs[i].default_value) for i in range(3)) == tuple(params["f0"]), \
            "Victor reticle F0 differs from the lowered source output"
        tex_path = (rule.get("textures", {}).get("_reticle") or "").lower()
        assert tex_path and tex_path in sources and any(node.type == "TEX_IMAGE" and str(node.get("source_texture_path", "")).lower() == tex_path for node in upstream), \
            "Victor reticle API8 source texture path is not bound to the material"
        assert term.get("blend") == "additive" and not term.get("two_sided"), "Victor reticle material flags differ from source"
        assert material.use_backface_culling and material.get("blend_state", "").startswith("additive"), \
            "Victor reticle one-sided/additive material state is missing"
        if hasattr(material, "surface_render_method"):
            assert material.surface_render_method == "BLENDED", "Victor reticle material is not configured for the additive layer"
        assert params.get("time") == 0.0 and params.get("time_input") == "CB0[99]", \
            "Victor reticle preview must document its frozen engine time input"
        constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
        return dict(status="passed_viewer_graph_rule_frontend", terms=ported, blend="additive", source_texture=tex_path,
                    amplitude=float(params["amplitude"]), f0=list(params["f0"]), gloss=float(params["gloss"]),
                    constants=constants, limitation=material.get("rc_shader_frontend_limitation"))
    brdf = _from_node(output.inputs["Surface"])
    facing = "facing_glow" in ported
    extra = ()
    if "alpha_cut" in ported:   # Surface = Mix(Transparent, shader, _c.a >= threshold)
        mix = brdf
        assert mix and mix.type == "MIX_SHADER" and mix.name == "VR alpha cut mix", "Leaf cut-out is not the surface mix"
        clear = _from_node(mix.inputs[1])
        assert clear and clear.type == "BSDF_TRANSPARENT" and clear.name == "VR alpha cut transparent",             "Leaf cut-out hole is not a transparent shader"
        assert mix.inputs[0].is_linked and any(node.type == "TEX_IMAGE" and str(node.get("source_texture_path", "")).lower() ==
            (rule["textures"].get("_c") or "").lower() for node in _viewer_upstream(mix.inputs[0])), "Leaf cut-out factor does not read the _c alpha"
        brdf, extra = _from_node(mix.inputs[2]), (mix.inputs[0],)
    if facing:   # Surface = Add(BRDF, facing-glow emission)
        add = brdf
        assert add and add.type == "ADD_SHADER" and add.name == "VR facing glow add", "Facing glow is not added at the output"
        emission = _from_node(add.inputs[1])
        assert emission and emission.type == "EMISSION" and emission.name == "VR facing glow emission" and \
            abs(emission.inputs["Strength"].default_value - 1.0) < 1e-6, "Facing glow emission is not the second Add input"
        brdf, extra = _from_node(add.inputs[0]), (*extra, *emission.inputs)
    assert brdf and brdf.type == "GROUP" and brdf.node_tree and brdf.node_tree.name == DEFERRED_GROUP, \
        "Deferred BRDF is not the active Material Output surface"
    upstream = _viewer_upstream(*brdf.inputs, *extra)

    def reaches(socket, role):
        path = (rule["textures"].get(role) or "").lower()
        return bool(path) and any(node.type == "TEX_IMAGE" and str(node.get("source_texture_path", "")).lower() == path
                                  for node in _viewer_upstream(socket))
    terms = rule.get("terms") or {}
    if "leaf_colour" in ported:
        assert reaches(brdf.inputs[0],"_cma"), "Leaf cma does not feed albedo"
        if terms['leaf_colour'].get('mapped_fresnel'):
            assert reaches(brdf.inputs[0],"_n") == has_n, "Global leaf Fresnel lost the mapped normal"
            assert not rule['textures'].get('_g') and not brdf.inputs[2].is_linked and not brdf.inputs[3].is_linked, "Global leaf zero MRT2 lost"
            assert all(abs(x)<1e-7 for x in brdf.inputs[2].default_value[:3]) and abs(brdf.inputs[3].default_value)<1e-7, "Global leaf F0/gloss is not zero"
        else:
            assert not reaches(brdf.inputs[0],"_n"), "Novalis Fresnel must use geometry normal"
    arch = "arch_sequence" in ported
    glow1 = "glow" in ported and int(terms["glow"]["kind"]) == 1
    if facing:   # 0CD2AAFB has no albedo input
        assert {node.name for node in _viewer_upstream(brdf.inputs[0])} == {"VR facing glow albedo"}, \
            "Facing-glow albedo is not the white source constant"
    elif "bright_center" in ported and terms["bright_center"].get("albedo_white") and not rule["textures"].get("_c"):
        assert any(node.name == "VR bright centre albedo" for node in _viewer_upstream(brdf.inputs[0])) and not any(
            node.type == "TEX_IMAGE" for node in _viewer_upstream(brdf.inputs[0])),             "Preview bright-centre albedo is not the white constant"
    elif "flat_surface" in ported and flat.get("model") == "basic_flat_color":
        assert {node.name for node in _viewer_upstream(brdf.inputs[0])} == {"VR flat surface albedo zero"}, \
            "basic_flat_color albedo is not the zero source constant"
    elif "flat_surface" in ported:   # MRT1 rgb is the material constant
        assert {node.name for node in _viewer_upstream(brdf.inputs[0])} == {"VR flat surface colour"}, \
            "Flat-surface albedo is not the source colour constant"
        assert not brdf.inputs[4].is_linked or not any(node.type == "TEX_IMAGE" for node in _viewer_upstream(brdf.inputs[4])), \
            "Flat-surface normal samples a texture although the writer binds none"
    elif arch or glow1 or "hair" in ported:   # these writers never sample _c.rgb
        assert not reaches(brdf.inputs[0], "_c"), "Viewer-rule albedo samples _c although the writer does not"
        if "hair" in ported:
            assert {node.name for node in _viewer_upstream(brdf.inputs[0])} == {"VR hair albedo"},                 "Hair albedo is not the material constant A"
    else:
        assert reaches(brdf.inputs[0], "_c"), "Viewer-rule _c image does not feed the BRDF albedo"
    has_g = bool(rule["textures"].get("_g")) and brdf.inputs[2].is_linked
    if has_g and rule["g_mode"] != "vehicle":
        assert reaches(brdf.inputs[2], "_g") or reaches(brdf.inputs[3], "_g"), "Viewer-rule _g image does not feed F0/gloss"
    unit = rule["g_mode"] in ("grey_skin", "vehicle") or "za_fresnel_power" in ported or "puddles" in ported
    spec, gloss = (1.0, 1.0) if unit else (rule["spec"], rule["gloss"])
    assert abs(brdf.inputs[5].default_value - spec) < 1e-5 and abs(brdf.inputs[6].default_value - gloss) < 1e-5, \
        "Viewer-rule specular/gloss scale differs from the source rule"
    k = float(brdf.inputs[7].default_value)
    want_k = 5.0 * float(terms["arch_sequence"]["intensity"]) if arch else float(rule["k"])
    assert (abs(k - want_k) < 1e-4 * max(1.0, want_k) and brdf.inputs[1].is_linked) or \
        (k == 0.0 and not brdf.inputs[1].is_linked), "Viewer-rule self-lit K or mask differs from the source rule"
    if arch and want_k > 0.0:
        assert k > 0.0 and reaches(brdf.inputs[1], "_sequence") if rule["textures"].get("_sequence") else True, \
            "Arch sequence self-lit does not sample the sequence"
    if "liquid_flow" in ported:
        from rc2016.shaders.liquids import LAVA_FLOW_GRAPH, LAVA_FLOW_SHA256
        flow = terms["liquid_flow"]
        assert rule.get("graph_identity") == dict(path=LAVA_FLOW_GRAPH, sha256=LAVA_FLOW_SHA256), "Lava flow graph identity changed"
        assert flow["time"] == 0.0 and flow["normal_fallback"] == "geometry_normal", "Lava flow frozen-input contract changed"
        assert rule["g_mode"] == "grey" and rule["self_lit"] == "g_blue", "Lava flow C/G channel contract changed"
        assert (rule["spec"], rule["gloss"], rule["k"]) == (flow["spec"], flow["gloss"], flow["k"]), "Lava flow surface scales changed"
        assert not rule["textures"].get("_n") and not reaches(brdf.inputs[4], "_n"), "Lava flow acquired an unsupported normal map"
        for role in ("_c", "_g"):
            path = rule["textures"][role].lower()
            images = [node for node in upstream if node.type == "TEX_IMAGE" and str(node.get("source_texture_path", "")).lower() == path]
            assert images, "Lava flow source image missing"
            for image in images:
                assert image.inputs["Vector"].is_linked, "Lava flow source image lost its UV transform"
                for uv in ((0.23, 0.61, 0.0), (-0.2, 1.4, 0.0)):
                    got = _viewer_uv_value(image.inputs["Vector"], uv)
                    expected_uv = (uv[0] + flow["uv_offset"][0], uv[1] - flow["uv_offset"][1], 0.0)
                    assert max(abs(a-b) for a,b in zip(got, expected_uv)) < 1e-6, "Lava flow UV transform differs from source (including V orientation)"
        assert "freezes CB0[99] at 0" in material.get("rc_shader_frontend_limitation", ""), "Lava flow frozen-time limitation lost"
    normal_nodes = _viewer_upstream(brdf.inputs[4])
    labels = {node.label for node in normal_nodes}
    if has_n and not arch and "vehicle" not in ported and "puddles" not in ported:
        if rule.get("normal_z") == "1-x*x-y*y":
            assert "z = sqrt(1-x*x-y*y)" not in labels and "Viewer rule normal z = 1-x*x-y*y" in labels, \
                "Viewer-rule normal z is not 1-x*x-y*y"
        else:
            assert "z = sqrt(1-x*x-y*y)" in labels, "Viewer-rule normal z is not sqrt(1-x*x-y*y)"
    constants = _viewer_check_constants_and_images(source, material, rule, upstream, has_n)
    return dict(status="passed_viewer_graph_rule_frontend", g_mode=rule["g_mode"], self_lit=rule["self_lit"] if k else None,
                k=k, specular_scale=spec, gloss_scale=gloss, has_g=has_g, terms=ported, unported=rule.get("unported"),
                constants=constants, limitation=material.get("rc_shader_frontend_limitation"))


def validate_material_preflight(source, material):
    """Validate the reopened Blender graph for one actual polygon-used material."""
    profile = source.get("validated_profile")
    if profile:
        return dict(status="profile_exception", reason="Hash-bound profile has its own reopen validator.")
    if material.get('rc_shader_translation'):
        from rc2016.shaders.nodes import validate_translated_material
        return validate_translated_material(source,material)
    if material.get("rc_viewer_graph_rule"):
        return _validate_viewer_rule_material(source, material)
    glass = [row for row in source.get("shader_preflight", []) if row.get("family") == "character_glass"]
    if len(glass) == 1 and len(source.get("shader_preflight", [])) == 1:
        return _validate_character_glass(source, material, glass[0])
    if source.get("graph_semantics") == "thin_glass":
        return dict(status="thin_glass_exception", reason="Retained documented thin-glass approximation.")
    preflights = source.get("shader_preflight", [])
    supported = [row for row in preflights if row.get("status") == "supported_partial"]
    if len(preflights) == 1 and len(supported) == 1:
        has_base = _has_real_role_binding(source, "base_color")
        has_specgloss = _has_real_role_binding(source, "colored_specular_gloss")
        if has_base and has_specgloss:
            if supported[0].get("family") in DEFERRED_FAMILY_CHECKS:
                return _validate_deferred_material(source, material, supported[0])
            return _validate_skin_material(source, material, supported[0])
        fallback = _validate_unknown_color_preview(source, material)
        fallback.update(status="passed_partial_family_texture_limited_color_only" if has_base else
                        "passed_partial_family_texture_limited_no_base_color",
                        family=supported[0].get("family"), missing_bindings=[role for role, present in
                            (("base_color", has_base), ("colored_specular_gloss", has_specgloss)) if not present])
        return fallback
    if len(preflights) > 1:
        fallback = _validate_unknown_color_preview(source, material)
        fallback["status"] = ("passed_ambiguous_family_color_only" if _has_real_role_binding(source, "base_color")
                              else "passed_ambiguous_family_color_only_no_base_colour")
        fallback["family_count"] = len(preflights)
        return fallback
    return _validate_unknown_color_preview(source, material)


def validate_polygon_used_material_preflights(descriptors, objects):
    """Validate preflight-selected graphs after reopen, restricted to used slots."""
    results = []
    for descriptor in descriptors:
        rows = descriptor.get("materials", {}).get("materials", [])
        by_index = {int(row["index"]): row for row in rows}
        used = {}
        for obj in objects:
            if obj.get("source_model_sha256") != descriptor.get("sha256"):
                continue
            source_index = int(obj.get("source_material_index", -1))
            if source_index not in by_index:
                continue
            for polygon in obj.data.polygons:
                if polygon.material_index >= len(obj.data.materials):
                    raise AssertionError("Polygon references a missing local material slot")
                mat = obj.data.materials[polygon.material_index]
                expected = by_index[source_index]
                if expected.get("status") == "unset":
                    assert mat and mat.get("source_material_unset") and mat.get("source_material_path") == "" and                         mat.get("shader_status") == "unset_source_material",                         "Unset source material slot %d is not a labelled placeholder" % source_index
                    continue
                if expected.get("status") == "failed":
                    raise AssertionError("Polygon-used source material %d (%s) failed to prepare: %s" % (
                        source_index, expected.get("path"), expected.get("reason")))
                assert mat and mat.get("source_material_path") == expected.get("path") and \
                    mat.get("source_material_sha256") == expected.get("source_sha256"), \
                    "Polygon-used material does not match preflight source identity"
                used.setdefault(source_index, set()).add(mat)
        for index, materials in used.items():
            source = by_index[index]
            for material in materials:
                results.append(dict(source_model_path=descriptor.get("path"),
                    source_model_sha256=descriptor.get("sha256"), material_index=index,
                    source_material_path=source.get("path"),
                    source_material_sha256=source.get("source_sha256"),
                    validation=validate_material_preflight(source, material)))
    return dict(schema="reopened-material-shader-preflight-validation-v1", status="passed",
                materials=results,
                scope="Actual polygon-used slots only; structural wiring and source identity are checked, not rendered pixel equivalence.")
