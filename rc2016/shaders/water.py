"""Source-bounded water terms recovered from native PS4 GCN.

The sampling contract covers both water graphs. The ordinary graph
(pok_uniq_water, colour writer F4D47ECE) also has a decoded surface closure:
normal composition, crest, shore foam, environment reflection, alpha and fog,
checked against a captured Novalis frame (docs/material-decoding/novalis-water.md).
Runtime inputs the closure reads (the reflection probe, scene depth, the
screen-space reflection and the per-instance opacity) stay labelled inputs.
"""

WATER_GRAPH = (
    "materialgraph/visualeffect/environment/pokitaru/unique/pok_uniq_water/"
    "pok_uniq_water.materialgraph"
)
WATER_GRAPH_SHA256 = "0cdcf946a6d628509191df6768257f9da0bdb785b9e9d7f0511c60432509ba11"
GLOW_GRAPH = (
    "materialgraph/visualeffect/environment/pokitaru/unique/pok_uniq_water/"
    "rp_pok_uniq_water_biolumenscent.materialgraph"
)
GLOW_GRAPH_SHA256 = "7b04d6928cd5b2e002aba09d95f762c47d1679c331faa559456220f405df2498"

# Entries identify the native color writer by code hash. Sample offsets are
# MIMG byte offsets in that writer, not graph node IDs.
WATER_WRITERS = {
    "89b494be6b573eb0f5e7ed2af9385498eb8eff08c1fc017edb07007930d72d20": {
        "shader_id": "0xf4d47ece", "pass_index": 0,
        "height_sample_offsets": (808, 816),
        "normal_sample_offsets": (2136, 2144),
        "cb2_displacement_dword": 36,
    },
}
GLOW_WRITERS = {
    "fa8376674acd23e96fee6f432a8d9c6b31543eea0d68e22bb02f42939bb2c044": {
        "shader_id": "0x59540726", "pass_index": 0,
        "height_sample_offsets": (620, 628),
        "normal_sample_offsets": (1752, 1760),
        "cb2_displacement_dword": 18,
    },
}

# Pass contract API slots 5..10 map to graph texture resource slots 0..5.
WATER_TEXTURES_BY_API = {
    5: {"slot_key": "0x48F15502", "path": "textures/visualeffect/water/water_tile_01.texture", "format": "BC4", "role": "height"},
    6: {"slot_key": "0x3BEB2C88", "path": "textures/visualeffect/water/water_tile_02.texture", "format": "BC4", "role": "height"},
    7: {"slot_key": "0x69E7942C", "path": "textures/visualeffect/water/water_tile_01n.texture", "format": "BC7", "role": "normal_sample_RG"},
    8: {"slot_key": "0x0D86F9E5", "path": "textures/visualeffect/water/water_tile_02n.texture", "format": "BC7", "role": "normal_sample_RG"},
    9: {"slot_key": "0x048C8807", "path": "textures/visualeffect/water/water_beachfoam_tile_01.texture", "format": "BC7", "role": "foam_unresolved"},
    10: {"slot_key": "0x3D01B4C2", "path": "textures/visualeffect/water/water_beachfoam_tile_02.texture", "format": "BC7", "role": "foam_unresolved"},
}

# CB2[36] in the ordinary graph and CB2[18] in glow share parameter key
# 0x230C6389 (graph default 0.075); instance overrides must be supplied by
# the caller. Glow has distinct foam resources and must not inherit ordinary
# graph material defaults.
WATER_DISPLACEMENT_KEY = "0x230C6389"
WATER_DISPLACEMENT_DEFAULT = 0.075


def water_sampling_contract(graph_path, graph_sha256, writer_sha256):
    """Return proven front-end sampling terms for an exact graph/writer pair."""
    path = str(graph_path).replace("\\", "/").lower()
    graph_sha = str(graph_sha256).lower()
    writer_sha = str(writer_sha256).lower()
    if path == WATER_GRAPH and graph_sha == WATER_GRAPH_SHA256:
        row = WATER_WRITERS.get(writer_sha)
        textures = WATER_TEXTURES_BY_API
    elif path == GLOW_GRAPH and graph_sha == GLOW_GRAPH_SHA256:
        row = GLOW_WRITERS.get(writer_sha)
        textures = dict(WATER_TEXTURES_BY_API)
        textures[6] = {**textures[6], "slot_key": "0x717C69C7"}
        textures[9] = {**textures[9], "path": "textures/environment/novalis/rp_nov_emissive_foam_tile_01.texture"}
        textures[10] = {**textures[10], "path": "textures/environment/novalis/rp_nov_emissive_foam_tile_02.texture"}
    else:
        return None
    if row is None:
        return None
    return {
        "status": "source_decoded_sampling_partial",
        "graph_path": path,
        "graph_sha256": graph_sha,
        "writer_sha256": writer_sha,
        **row,
        "textures_by_api": {api: dict(value) for api, value in textures.items()},
        "displacement_parameter": {
            "key": WATER_DISPLACEMENT_KEY,
            "cb2_dword": row["cb2_displacement_dword"],
            "graph_default": WATER_DISPLACEMENT_DEFAULT,
        },
        "decoded_terms": ["height_uvs", "height_cross_displacement", "first_RG_normal_sample_uvs"],
        "sampling": {
            "height_uvs_by_api": {
                5: ("0.105*dx + 0.252*time", "0.095*dz - 0.246*time"),
                6: ("0.248 + 0.095*dx - 0.247*time", "0.613 + 0.105*dz + 0.255*time"),
            },
            "height_cross_displacement": "CB2[dword] * (sample(API5).R + sample(API6).R - 1)",
            "primary_normal_uvs_by_api": {
                7: ("0.133 + 0.315*dx + displacement + 0.2042*time",
                    "0.346 + 0.285*dz + displacement + 0.196*time"),
                8: ("0.285*dx + displacement - 0.197*time",
                    "0.315*dz + displacement - 0.2013*time"),
            },
            "delta_inputs": {
                "dx": "CB0[28] - attribute3.x",
                "dz": "CB0[30] - attribute3.z",
            },
        },
        "unresolved": [
            "CB0[99] runtime upload name/units; zero is a preview substitution",
            "CB0[28]/CB0[30] and attribute3 world/local transform binding",
            "complete shoreline-selected normal sample sequence and normal composition/strength",
            "foam, alpha/coverage, Fresnel, and post-light-loop material closure",
            "runtime pass/draw selection and game equivalence",
        ],
    }


def water_sampling_rule(graph_data, material_data):
    """Resolve the exact water sampling partial against a graph/material pair.

    Raises ``ValueError`` when identity, graph layout, displacement parameter,
    or texture-slot binding does not match the recovered source. The result
    only binds the first height/normal sample pair; it does not decode closure.
    """
    import hashlib
    import math

    from rc2016.formats.index import references
    from rc2016.formats.materials import (
        MATERIAL_TEXTURE_SLOTS,
        extract_material_parameters,
        texture_slot_records,
    )
    from rc2016.shaders.bindings import pass_contracts, _parameter_values

    graph_data, material_data = bytes(graph_data), bytes(material_data)
    graph_sha = hashlib.sha256(graph_data).hexdigest()
    if graph_sha == WATER_GRAPH_SHA256:
        graph_path, writer_sha = WATER_GRAPH, next(iter(WATER_WRITERS))
        displacement_dword = 36
        displacement_dwords = (36, 20)
        expected_resources = {
            5: 0x48F15502, 6: 0x3BEB2C88, 7: 0x69E7942C,
            8: 0x0D86F9E5, 9: 0x048C8807, 10: 0x3D01B4C2,
        }
    elif graph_sha == GLOW_GRAPH_SHA256:
        graph_path, writer_sha = GLOW_GRAPH, next(iter(GLOW_WRITERS))
        displacement_dword = 18
        displacement_dwords = (18, 18)
        expected_resources = {
            5: 0x48F15502, 6: 0x717C69C7, 7: 0x69E7942C,
            8: 0x0D86F9E5, 9: 0x048C8807, 10: 0x3D01B4C2,
        }
    else:
        raise ValueError("unsupported water graph SHA-256")

    material_refs = {
        row.get("path") for row in references(material_data)
        if not row.get("resolution_error")
    }
    if graph_path not in material_refs:
        raise ValueError("material does not reference the exact water graph")

    contracts = pass_contracts(graph_data)
    if len(contracts) != 2:
        raise ValueError("water graph pass-contract count changed")
    for contract in contracts:
        resources = contract["resources"]
        if len(resources) != 6:
            raise ValueError("water graph resource count changed")
        for api, resource in zip(range(5, 11), resources):
            if resource["slot_key"] != expected_resources[api]:
                raise ValueError(f"water API{api} resource slot identity changed")

    instance = extract_material_parameters(material_data)
    resolved = []
    for contract, expected_dword in zip(contracts, displacement_dwords):
        rows = [entry for entry in contract["table"]
                if entry["key_hash"] == int(WATER_DISPLACEMENT_KEY, 16)]
        if len(rows) != 1:
            raise ValueError("water displacement key is missing or duplicated")
        entry = rows[0]
        if (entry["dword"] != expected_dword or entry["size_bytes"] != 4):
            raise ValueError("water displacement CB layout changed")
        values, receipt = _parameter_values(contract, instance)
        if expected_dword not in values:
            raise ValueError("water displacement parameter did not resolve")
        value = float(values[expected_dword])
        if not math.isfinite(value):
            raise ValueError("water displacement parameter is non-finite")
        resolved.append((value, receipt))
    if resolved[0][0] != resolved[1][0]:
        raise ValueError("water pass displacement values disagree")

    material_rows = texture_slot_records(material_data, MATERIAL_TEXTURE_SLOTS)
    material_slots = {}
    for row in material_rows:
        key, path = row["slot_key"], row["path"]
        if key in material_slots and material_slots[key] != path:
            raise ValueError(f"conflicting water texture overrides for 0x{key:08X}")
        material_slots[key] = path

    textures_by_api = {}
    for api, resource in zip(range(5, 11), contracts[0]["resources"]):
        graph_path_default = resource["path"]
        # Missing records inherit graph defaults; an explicit empty required
        # slot is not evidence of a bound source texture and fails closed.
        if resource["slot_key"] in material_slots:
            bound_path = material_slots[resource["slot_key"]]
            if not bound_path and api in (5, 6, 7, 8):
                raise ValueError(f"water API{api} has an explicitly empty required texture slot")
        else:
            bound_path = graph_path_default
        if not bound_path:
            bound_path = graph_path_default
        if not bound_path:
            raise ValueError(f"water API{api} texture slot is unresolved")
        textures_by_api[api] = {
            "texture_path": bound_path,
            "graph_default_path": graph_path_default,
            "slot_key": f"0x{resource['slot_key']:08X}",
            "role": {
                5: "water_height_a", 6: "water_height_b",
                7: "water_normal_a", 8: "water_normal_pick",
                9: "foam_unresolved", 10: "foam_unresolved",
            }[api],
        }

    bound = {
        "_water_height_a": textures_by_api[5]["texture_path"],
        "_water_height_b": textures_by_api[6]["texture_path"],
        "_n": textures_by_api[7]["texture_path"],
        "_pick": textures_by_api[8]["texture_path"],
    }
    contract = water_sampling_contract(graph_path, graph_sha, writer_sha)
    return {
        "status": "source_decoded_sampling_partial",
        "graph_path": graph_path,
        "graph_sha256": graph_sha,
        "material_sha256": hashlib.sha256(material_data).hexdigest(),
        "writer_sha256": writer_sha,
        "shader_id": contract["shader_id"],
        "pass_index": contract["pass_index"],
        "sampling": {
            "displacement": resolved[0][0],
            "displacement_parameter_key": WATER_DISPLACEMENT_KEY,
            "displacement_cb2_dword": displacement_dword,
            "height_uvs_by_api": contract["sampling"]["height_uvs_by_api"],
            "height_cross_displacement": contract["sampling"]["height_cross_displacement"],
            "primary_normal_uvs_by_api": contract["sampling"]["primary_normal_uvs_by_api"],
            "delta_inputs": contract["sampling"]["delta_inputs"],
            "frozen_time": 0.0,
            "time_input": "CB0[99]",
        },
        # Top-level alias is convenient for callers that store this whole
        # partial rule under their presentation `sampling` field.
        "displacement": resolved[0][0],
        "textures": bound,
        "textures_by_api": textures_by_api,
        "parameter_receipt": resolved[0][1],
        "time_substitution": "CB0[99] frozen to zero for this preview",
        "normal_note": "The RG samples and first UV pair are source decoded; normal unpack/composition/strength remain unresolved.",
        "limitations": contract["unresolved"],
    }


# ---------------------------------------------------------------------------------------------
# Decoded surface closure of F4D47ECE (pass 0 of pok_uniq_water.materialgraph)

# name: (material key, pass-0 CB2 dword, float count). Every value is read through the pass-0
# table; the layout is checked against the graph, so a changed table fails closed.
WATER_SURFACE_KEYS = (
    ("shallow_colour", 0x56CB709A, 0, 3),
    ("body", 0x1DAF77B8, 4, 3),
    ("depth_fade", 0xD50FF59A, 8, 1),
    ("grazing_power", 0x66DF5298, 9, 1),
    ("near_fade_start", 0x575A5C8E, 10, 1),
    ("near_fade_end", 0xDC287E64, 11, 1),
    ("displacement_b", 0xE706F97C, 12, 1),
    ("strength_distance", 0xAAED7A8B, 13, 1),
    ("crest_scale", 0x266F5E89, 14, 1),
    ("strength_a", 0x8EC1A779, 15, 1),
    ("strength_b", 0x60402AB7, 17, 1),
    ("foam_height_a", 0xA98713DA, 19, 1),
    ("foam_height_b", 0xB9E7ED97, 20, 1),
    ("reflection_distance", 0x4ACC9AF0, 21, 1),
    ("foam_scale_u", 0xE61BBA8C, 25, 1),
    ("foam_scale_v", 0x608FC822, 26, 1),
    ("foam_displacement_a", 0x144F65DB, 27, 1),
    ("foam_displacement_b", 0x53EF1F0B, 28, 1),
    ("foam_scroll_au", 0xF9F2E56C, 29, 1),
    ("foam_scroll_av", 0x60FBB4D6, 30, 1),
    ("foam_scroll_bu", 0xC07FD9A9, 31, 1),
    ("foam_scroll_bv", 0x59768813, 32, 1),
    ("foam_power", 0x4E58797E, 33, 1),
    ("thickness_power", 0xD3AFCBB7, 34, 1),
    ("thickness_scale", 0x097B0E99, 35, 1),
    ("displacement_a", 0x230C6389, 36, 1),
    ("crest_colour", 0x897E48FA, 39, 1),
    ("environment_scale", 0xE93F97D5, 40, 1),
    ("environment_luminance", 0x9C360E85, 43, 1),
    ("specular_weight", 0x8AC26FB9, 44, 1),
    ("foam_scale", 0x339D4B95, 45, 1),
    ("far_reflection", 0xA46F1D8D, 46, 1),
    ("far_strength", 0xBBBA496D, 47, 1),
)

# Labelled presentation inputs of the decoded closure (not material data):
# - opacity: attribute0.w, the per-draw opacity of the instance record (record dword 66). The
#   captured Novalis river instance has 0.4 (102/255); the far sea instance and the culled
#   mirrored river instance have 0.949 (242/255). Its source is not located in the zone data.
# - time: CB0[99], frozen at 0 for static renders (the captured frame has 48.35).
WATER_SURFACE_PRESENTATION = dict(opacity=0.4, time=0.0)

WATER_SURFACE_DECODED = (
    "height, normal, crest and foam sample UVs (native world X/Z: attribute3 is camera minus world position)",
    "crest c = sat(CB2[14] (1 - H1)(1 - H2)) from the two large-scale height samples",
    "normal strengths q = sat(c lerp(s, s CB2[47], sat(distance / CB2[13]))) and the four-sample composition",
    "alpha = sat(opacity sat(near Z (1 - F))), Z = sat(CB2[8] thickness), F = sat((1.001 - N.V)^CB2[9])",
    "shore foam gated by the water thickness, two foam tiles and the summed heights",
    "reflection = sat(CB2[39] (1 - c) lerp(A, B, c) + CB2[40] probe(R)) (1 + sat(d / CB2[21]) (CB2[46] - 1))",
    "colour = fog + T (foam + lerp(reflection, SSR, w) - fog) with T the vertex fog transmittance",
)

WATER_SURFACE_LIMITATIONS = (
    "the reflection probe is the viewer's Level atmosphere environment (the capture's global probe matches the "
    "atmosphere zone's reflection capture), sampled at its sharpest prefiltered band",
    "screen-space reflection (record flag 0x2000) is not drawn: weight 0, so the environment reflection is not "
    "darkened where the game reflects the river banks",
    "per-instance opacity is a labelled presentation value (0.4, the captured river instance)",
    "the light-list specular (CB2[44] times the light loop) is off for the captured record flags (0x2001); other "
    "record flags are not covered",
    "time CB0[99] is frozen at 0; the near-camera refraction body term (view depth below about 0.93) is not drawn",
    "scene depth (thickness, shore fade and foam) is available in the level viewer only; elsewhere Z = 1 and no foam",
    "the tangent frame is native world X; the game uses the mesh tangent (the instance's model X axis)",
)


def water_surface_parameters(graph_data, material_data):
    """Decoded closure parameters of the ordinary water graph, by name, read through pass 0.

    Raises ``ValueError`` for another graph, a changed pass-0 layout or a non-finite value."""
    import hashlib
    import math

    from rc2016.formats.materials import extract_material_parameters
    from rc2016.shaders.bindings import pass_contracts, _parameter_values

    graph_data = bytes(graph_data)
    if hashlib.sha256(graph_data).hexdigest() != WATER_GRAPH_SHA256:
        raise ValueError("the decoded water surface is bound to pok_uniq_water only")
    contract = pass_contracts(graph_data)[0]
    table = {entry["key_hash"]: entry for entry in contract["table"]}
    values, _receipt = _parameter_values(contract, extract_material_parameters(bytes(material_data)))
    surface = {}
    for name, key, dword, count in WATER_SURFACE_KEYS:
        entry = table.get(key)
        if entry is None or entry["dword"] != dword or entry["size_bytes"] != 4 * count:
            raise ValueError("water surface parameter %s (0x%08X) changed in the pass-0 layout" % (name, key))
        row = [values.get(dword + i) for i in range(count)]
        if any(v is None or not math.isfinite(v) for v in row):
            raise ValueError("water surface parameter %s did not resolve" % name)
        surface[name] = [float(v) for v in row] if count == 3 else float(row[0])
    if surface["near_fade_end"] == surface["near_fade_start"]:
        raise ValueError("water near fade has zero width")
    return surface


def water_surface_rule(graph_data, material_data):
    """``water_sampling_rule`` of the ordinary graph plus the decoded closure and its foam textures."""
    rule = water_sampling_rule(graph_data, material_data)
    surface = water_surface_parameters(graph_data, material_data)
    foam = {"_water_foam_a": rule["textures_by_api"][9]["texture_path"],
            "_water_foam_b": rule["textures_by_api"][10]["texture_path"]}
    rule["textures"] = dict(rule["textures"], **foam)
    rule["surface"] = dict(surface, **WATER_SURFACE_PRESENTATION, status="source_decoded_surface_partial",
                           decoded_terms=list(WATER_SURFACE_DECODED),
                           presentation=dict(opacity="per-instance record opacity of the captured river (0.4)",
                                             time="CB0[99] frozen at 0"))
    rule["limitations"] = list(WATER_SURFACE_LIMITATIONS)
    rule["normal_note"] = "Four RG samples composed as decoded from F4D47ECE; see the surface terms."
    return rule


def water_secondary_uvs(world_x, world_z, height_sum_a, displacement_b, time=0.0):
    """Crest (API5/API6 at 0.0025 per unit) and second normal-pair (API7/API8) UVs of F4D47ECE.

    ``height_sum_a`` is the first height pair's h5 + h6 (the cross term is ``displacement_b (h5 + h6 - 1)``)."""
    x, z, t = float(world_x), float(world_z), float(time)
    c = float(displacement_b) * (float(height_sum_a) - 1.0)
    return {
        "crest_5": (0.116 + 0.002625 * x + 0.0099 * t, 0.002375 * z + 0.011 * t),
        "crest_6": (0.346 + 0.002375 * x - 0.012 * t, 0.133 + 0.002625 * z - 0.008 * t),
        "pair_b_8": (0.0475 * x + c + 0.052 * t, 0.0525 * z + c + 0.048 * t),
        "pair_b_7": (0.133 + 0.0525 * x + c - 0.049 * t, 0.346 + 0.0475 * z + c - 0.051 * t),
    }


def water_foam_uvs(world_x, world_z, height_sum_a, p, time=0.0):
    """Foam tile UVs (API9, API10) for parameters ``p`` (``water_surface_parameters``)."""
    x, z, t = 0.05 * float(world_x), 0.05 * float(world_z), float(time)
    hh = float(height_sum_a) - 1.0
    return {
        9: (0.116 + p["foam_scale_u"] * x + p["foam_displacement_a"] * hh + p["foam_scroll_au"] * t,
            p["foam_scale_v"] * z + p["foam_displacement_a"] * hh + p["foam_scroll_av"] * t),
        10: (0.346 + p["foam_scale_v"] * x + p["foam_displacement_b"] * hh + p["foam_scroll_bu"] * t,
             0.133 + p["foam_scale_u"] * z + p["foam_displacement_b"] * hh + p["foam_scroll_bv"] * t),
    }


def _sat(x):
    return min(1.0, max(0.0, x))


def water_crest(height_1, height_2, p):
    """c = sat(CB2[14] (1 - H1)(1 - H2)) from the crest samples of API5 and API6."""
    return _sat(p["crest_scale"] * (1.0 - height_1) * (1.0 - height_2))


def water_normal_strengths(crest, distance, p):
    """(q1, q2): first and second normal-pair strengths at the camera distance."""
    f = _sat(distance / p["strength_distance"])
    lerp = lambda s: s + (s * p["far_strength"] - s) * f
    return _sat(crest * lerp(p["strength_a"])), _sat(crest * lerp(p["strength_b"]))


def water_surface_alpha(distance, thickness, n_dot_v, p, opacity):
    """Coverage and alpha; ``n_dot_v`` uses the interpolated geometry normal."""
    z = _sat(p["depth_fade"] * thickness)
    f = _sat((1.001 - _sat(n_dot_v)) ** p["grazing_power"])
    near = _sat((distance - p["near_fade_start"]) / (p["near_fade_end"] - p["near_fade_start"]))
    cover = _sat(near * (z - f * z))
    return cover, _sat(opacity * cover)


def water_foam(foam_a, foam_b, height_sum_a, height_sum_b, thickness, p):
    """Shore foam from the two foam samples (R), the height sums at the first and second normal UVs
    and the water thickness along the view ray (scene view depth minus fragment view depth)."""
    g = p["foam_height_a"] * height_sum_a + p["foam_height_b"] * height_sum_b
    mask = _sat(_sat(foam_a * foam_b) * _sat(_sat(g) ** p["foam_power"]))
    shallow = 1.0 - _sat(p["thickness_scale"] * max(thickness, 0.0) ** p["thickness_power"])
    return _sat(p["foam_scale"] * mask * shallow)


def water_reflection(crest, probe, distance, p, specular=(0.0, 0.0, 0.0)):
    """Reflection colour: crest-coloured scatter plus the scaled probe sample, distance-weighted.

    ``specular`` is the light-list term (zero on the captured record path)."""
    a, b = p["shallow_colour"], p["body"]
    k = p["environment_luminance"]
    env = [p["environment_scale"] * v for v in probe]
    lum = 0.3 * env[0] + 0.59 * env[1] + 0.11 * env[2]
    far = 1.0 + _sat(distance / p["reflection_distance"]) * (p["far_reflection"] - 1.0)
    out = []
    for i in range(3):
        colour = a[i] + (b[i] - a[i]) * crest
        pre = (1.0 - crest) * p["crest_colour"] * colour + env[i] * _sat(1.0 - k) + _sat(k) * lum
        out.append((p["specular_weight"] * specular[i] + _sat(pre)) * far)
    return out


def water_height_sample_uvs(delta_x, delta_z, time=0.0):
    """Native API5/API6 base height UVs; ``time`` is a preview input."""
    dx, dz, t = float(delta_x), float(delta_z), float(time)
    return {
        5: (0.105 * dx + 0.252 * t, 0.095 * dz - 0.246 * t),
        6: (0.248 + 0.095 * dx - 0.247 * t,
            0.613 + 0.105 * dz + 0.255 * t),
    }


def water_primary_normal_sample_uvs(delta_x, delta_z, height5, height6,
                                    displacement=WATER_DISPLACEMENT_DEFAULT,
                                    time=0.0):
    """Native first API7/API8 RG normal-sample UVs before shoreline branches.

    ``delta_x`` and ``delta_z`` stand for the exact shader differences
    ``CB0[28]-attribute3.x`` and ``CB0[30]-attribute3.z``. ``time=0`` is a
    caller-selected frozen-time presentation value, not a decoded runtime
    upload. This function does not perform normal unpacking or composition.
    """
    dx, dz = float(delta_x), float(delta_z)
    h5, h6 = float(height5), float(height6)
    d, t = float(displacement), float(time)
    cross_height = d * (h5 + h6 - 1.0)
    return {
        7: (0.133 + 0.315 * dx + cross_height + 0.2042 * t,
            0.346 + 0.285 * dz + cross_height + 0.196 * t),
        8: (0.285 * dx + cross_height - 0.197 * t,
            0.315 * dz + cross_height - 0.2013 * t),
    }
