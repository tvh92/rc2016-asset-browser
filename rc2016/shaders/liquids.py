"""Source-bounded liquid material contracts recovered from native PS4 GCN.

These records describe inputs and equations that the translator recovered from
one exact materialgraph. They are not a general liquid-family classifier and do
not claim runtime draw selection or game equivalence.
"""

GASPAR_LAVA_FLOW_GRAPH = (
    "materialgraph/environment/gaspar/ambient/gas_amb_lava_flow_01/"
    "gas_amb_lava_flow_01.materialgraph"
)
GASPAR_LAVA_FLOW_GRAPH_SHA256 = (
    "6ad0e42d51a44d79d92dac80788c18e2384256762d096ead273b4fc3c7c4bbb2"
)
LAVA_FLOW_GRAPH = GASPAR_LAVA_FLOW_GRAPH
LAVA_FLOW_SHA256 = GASPAR_LAVA_FLOW_GRAPH_SHA256

# The graph has two source pass contracts. The first and second compiled
# deferred writers both use the same decoded color/specular/emission equations;
# the second omits the portable tangent-normal recovery slice.
GASPAR_LAVA_FLOW_WRITERS = {
    "4dc3c446f0a31c8ef2c890d1b5bce58abc4bd16fb50ff21b7870abdf5ed50be0": {
        "shader_id": "0xea6006f6",
        "pass_index": 0,
        "normal_status": "partial_native_slice",
    },
    "d398484200d0659b4033b13bf6284ae51cc8695bf1c2bf9364d8f87ce61c4f58": {
        "shader_id": "0xbacb8758",
        "pass_index": 1,
        "normal_status": "not_recovered",
    },
}

# For these graph pass contracts the native sample API is the graph resource
# slot ordinal plus five. This identity is established by the decoded pass
# resource rows and the GCN sample descriptors, not by texture filenames.
GASPAR_LAVA_FLOW_TEXTURES = {
    5: "textures/environment/gaspar/fx/gas_lava_flow_01/gas_lava_flow_01_c.texture",
    6: "textures/visualeffect/water/water_tile_01.texture",
    7: "textures/visualeffect/water/water_tile_01.texture",
    8: "textures/environment/gaspar/fx/gas_lava_flow_01/gas_lava_flow_01_g.texture",
    9: "textures/environment/gaspar/fx/gas_lava_noise_01_nmap.texture",
    10: "textures/environment/gaspar/fx/gas_lava_flow_01/gas_lava_flow_01_n.texture",
}

# The GCN compiler names these CB2 dwords. Graph parameter layouts bind the
# dwords to the listed keys and defaults; only the gloss/specular names have
# existing cross-family key evidence. The rate and UV labels describe their
# observed source use and remain inferred semantics.
GASPAR_LAVA_FLOW_PARAMETERS = {
    4: {"key": "0x2BA2E2C6", "default": 0.485},
    5: {"key": "0xB2ABB37C", "default": 0.0},
    6: {"key": "0x24F4CA4C", "name": "gloss_scale", "default": 1.0},
    7: {"key": "0x62835AF9", "name": "specular_scale", "default": 1.0},
    8: {"key": "0x1278F4E8", "default": 0.0},
    9: {"key": "0x8B71A552", "default": 0.0},
    11: {"key": "0x41E7FE54", "observed_use": "emission_oscillation_rate", "default": 0.0},
    12: {"key": "0x5FD56522", "observed_use": "emission_scale", "default": 10.0},
    14: {"key": "0x3D0F21CA", "observed_use": "uv_y_scroll_rate", "default": -0.05},
    15: {"key": "0xA4067070", "observed_use": "uv_x_scroll_rate", "default": 0.0},
}

# CB0[99] is used as the time-like input in scrolling UVs and emission phase.
# Its runtime upload name and units are not established by the graph decoder.
GASPAR_LAVA_FLOW_TIME_INPUT = "CB0[99]"


def _source_equations(writer):
    return {
        "status": "source_decoded_partial",
        "graph_path": GASPAR_LAVA_FLOW_GRAPH,
        "graph_sha256": GASPAR_LAVA_FLOW_GRAPH_SHA256,
        **writer,
        "time_input": GASPAR_LAVA_FLOW_TIME_INPUT,
        "equations": {
            "albedo": "sample(API5, uv_b).rgb",
            "F0": "CB2[7] * sample(API8, uv_b).g",
            "gloss": "sqrt(sat(CB2[6] * sample(API8, uv_b).r))",
            "emission": (
                "sample(API8, uv_b).b * CB2[12] * "
                "(0.5 + 0.5*cos(2*pi*fract(abs(CB2[11]*CB0[99])/(2*pi))))"
            ),
            "uv_a": "(attr0.x + CB2[4], attr0.y + CB2[5] - 0.02*CB0[99])",
            "uv_b": (
                "(attr0.x + CB2[9] + CB2[15]*CB0[99], "
                "attr0.y + CB2[8] + CB2[14]*CB0[99])"
            ),
            "partial_normal": (
                "The first writer has a recovered API8 sample0 RG-to-XY slice at uv_a; "
                "the scale also depends on unresolved position/pull-model inputs. "
                "The second writer has no portable normal slice."
            ),
        },
        "limitations": [
            "CB0[99] time upload name and units are unresolved.",
            "Attribute0.xy to model UV semantic binding is not proven by this pixel program.",
            "The full tangent-normal composition and source TBN transport are unresolved.",
            "The contract does not prove runtime pass or draw selection.",
        ],
    }


def gaspar_lava_flow_contract(graph_path, graph_sha256, writer_sha256):
    """Return the bounded contract for an exact graph/writer pair, else None."""
    if (str(graph_path).replace("\\", "/").lower() != GASPAR_LAVA_FLOW_GRAPH
            or str(graph_sha256).lower() != GASPAR_LAVA_FLOW_GRAPH_SHA256):
        return None
    writer = GASPAR_LAVA_FLOW_WRITERS.get(str(writer_sha256).lower())
    if writer is None:
        return None
    result = _source_equations(writer)
    result.update(writer_sha256=str(writer_sha256).lower(),
                  textures_by_api=dict(GASPAR_LAVA_FLOW_TEXTURES),
                  parameters_by_cb2_dword=dict(GASPAR_LAVA_FLOW_PARAMETERS))
    return result


def liquid_flow_rule(graph_data, material_data):
    """Decode an exact Gaspar lava-flow graph/material pair for a time-zero preview.

    The graph and material are DAT1 byte strings obtained from the archive. Bad
    identities or incomplete native pass bindings fail closed with a reason.
    The returned C/G inputs follow pass-contract resource order, including any
    material slot overrides. Time is explicitly frozen at zero for presentation.
    """
    import hashlib
    import math
    from rc2016.formats.index import references
    from rc2016.formats.materials import (
        extract_material_parameters, texture_slot_records,
        MATERIAL_TEXTURE_SLOTS,
    )
    from rc2016.shaders.bindings import pass_contracts, _parameter_values

    graph_data = bytes(graph_data)
    material_data = bytes(material_data)
    graph_sha = hashlib.sha256(graph_data).hexdigest()
    if graph_sha != GASPAR_LAVA_FLOW_GRAPH_SHA256:
        return {"status": "unsupported", "reason": "graph SHA-256 is not the decoded Gaspar lava-flow source"}
    graph_refs = {row.get("path") for row in references(material_data)
                  if not row.get("resolution_error")}
    if GASPAR_LAVA_FLOW_GRAPH not in graph_refs:
        return {"status": "unsupported", "reason": "material does not reference the exact source graph"}

    contracts = pass_contracts(graph_data)
    if len(contracts) != 2 or any(len(row["resources"]) != 6 for row in contracts):
        return {"status": "unsupported", "reason": "source pass resource contracts changed"}
    material_slots = {row["slot_key"]: row["path"] for row in
                      texture_slot_records(material_data, MATERIAL_TEXTURE_SLOTS)}
    instance = extract_material_parameters(material_data)
    writer_rows = []
    for index, contract in enumerate(contracts):
        writer_sha = next((digest for digest, row in GASPAR_LAVA_FLOW_WRITERS.items()
                           if row["pass_index"] == index), None)
        if writer_sha is None:
            return {"status": "unsupported", "reason": "source writer identity is incomplete"}
        values, receipt = _parameter_values(contract, instance)
        resources = contract["resources"]
        by_api = {}
        for ordinal, resource in enumerate(resources):
            api = ordinal + 5
            # Only these two roles have a complete decoded colour/specular
            # front-end. Other sampled resources remain visible as evidence.
            if api in (5, 8):
                by_api[api] = {
                    "texture_path": material_slots.get(resource["slot_key"], resource["path"]),
                    "graph_default_path": resource["path"],
                    "slot_key": f"0x{resource['slot_key']:08X}",
                    "role": "albedo" if api == 5 else "specular_gloss_emission",
                }
        required = {5, 8}
        if set(by_api) != required or any(not row["texture_path"] for row in by_api.values()):
            return {"status": "unsupported", "reason": "albedo/specular texture slots are not fully bound"}
        writer_rows.append({
            **_source_equations(GASPAR_LAVA_FLOW_WRITERS[writer_sha]),
            "writer_sha256": writer_sha,
            "textures_by_api": by_api,
            "parameters_by_cb2_dword": values,
            "parameter_receipt": receipt,
            "frozen_time": 0.0,
            "effective_uv_offsets_at_frozen_time": {
                "uv_a": (values.get(4, 0.0), values.get(5, 0.0)),
                "uv_b": (values.get(9, 0.0), values.get(8, 0.0)),
            },
            "not_implemented_resource_apis": [6, 7, 9, 10],
        })
    first, second = writer_rows
    for dword in (6, 7, 8, 9, 11, 12, 14, 15):
        if first["parameters_by_cb2_dword"].get(dword) != second["parameters_by_cb2_dword"].get(dword):
            return {"status": "unsupported", "reason": "pass material parameter values disagree"}
    if first["textures_by_api"] != second["textures_by_api"]:
        return {"status": "unsupported", "reason": "pass material texture bindings disagree"}
    params = first["parameters_by_cb2_dword"]
    required_parameters = (4, 5, 6, 7, 8, 9, 11, 12, 14, 15)
    if any(dword not in params for dword in required_parameters):
        return {"status": "unsupported", "reason": "required source parameter is missing"}
    for dword in required_parameters:
        value = params[dword]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"status": "unsupported", "reason": f"CB2[{dword}] is not a numeric scalar"}
        try:
            finite = math.isfinite(value)
        except (OverflowError, TypeError):
            finite = False
        if not finite:
            return {"status": "unsupported", "reason": f"CB2[{dword}] is non-finite"}
    return {
        "status": "source_decoded_partial",
        "graph_path": GASPAR_LAVA_FLOW_GRAPH,
        "graph_sha256": graph_sha,
        "material_sha256": hashlib.sha256(material_data).hexdigest(),
        "texture_slots": {
            "albedo": first["textures_by_api"][5],
            "specular_gloss_emission": first["textures_by_api"][8],
        },
        "uv_offset": [params[9], params[8]],
        "uv_a_offset": [params[4], params[5]],
        "spec": params[7],
        "gloss": params[6],
        "k": params[12],
        "emission_rate": params[11],
        "uv_scroll": {"x": params[15], "y": params[14]},
        "time": 0.0,
        "time_input": GASPAR_LAVA_FLOW_TIME_INPUT,
        "parameters_by_cb2_dword": params,
        "parameter_receipt": first["parameter_receipt"],
        "provenance": {
            "source_graph_sha256": graph_sha,
            "writer_code_sha256": [row["writer_sha256"] for row in writer_rows],
            "equations": first["equations"],
            "effective_uv_offsets_at_frozen_time": first["effective_uv_offsets_at_frozen_time"],
        },
        "limitations": [
            "CB0[99] is time-like by source use; its runtime field name and units are unresolved.",
            "The preview freezes CB0[99] at 0; source scrolling is retained as metadata.",
            "Attribute0.xy to model UV semantic binding is not proven by this pixel program.",
            "The full tangent-normal composition and source TBN transport are unresolved; geometry normal is used.",
            "The contract does not prove runtime pass or draw selection.",
        ],
        "normal_fallback": "geometry_normal",
        "normal_note": "The source normal composition is partial; generic _n sampling is disabled.",
        "writers": writer_rows,
    }
