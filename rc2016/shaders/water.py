"""Source-bounded water sampling terms recovered from native PS4 GCN.

This is deliberately a sampling contract only. It does not reconstruct the
water normal, material closure, alpha, Fresnel, or runtime engine inputs.
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
