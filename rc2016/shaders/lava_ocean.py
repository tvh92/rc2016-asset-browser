"""Source-bound, explicitly partial Gaspar lava-ocean surface preview.

The portable DAG retains the decoded deferred writer. Only the reflection
cube sample, time input and review amplitude are substituted; runtime draw
selection and the complete normal/forward closure are not claimed.
"""

GASPAR_LAVA_OCEAN_GRAPH = (
    "materialgraph/visualeffect/environment/nebula/unique/neb_uniq_lava_pool/"
    "gas_lava_ocean.materialgraph"
)
GASPAR_LAVA_OCEAN_GRAPH_SHA256 = (
    "05b50f542d64a6c4da3dc6d4f1c30a334906f847efb8b0f95aa730a47c349148"
)
GASPAR_LAVA_OCEAN_WRITER_SHA256 = (
    "092d38bf9a2fca119994ade27272e082568fe3f424d51379e077d34b908fe6a5"
)
GASPAR_LAVA_OCEAN_SHADER_ID = "0xce49c867"
GASPAR_LAVA_OCEAN_TEXTURES = {
    5: (0xBA53B50A, "textures/visualeffect/water/water_tile_01.texture"),
    6: (0x784C459F, "textures/visualeffect/water/water_tile_02.texture"),
    7: (0x3BEB2C88, "textures/visualeffect/noise/fx_noise_blobby_01.texture"),
    8: (0x0D86F9E5, "textures/visualeffect/noise/fx_noise_blobby_01_nmap.texture"),
    9: (0x41829E92, "textures/visualeffect/test/cube_map/fx_cubemap_generic_tile01.texture"),
}
GRAPH = GASPAR_LAVA_OCEAN_GRAPH
GRAPH_SHA256 = GASPAR_LAVA_OCEAN_GRAPH_SHA256


def surface_rule(graph_data, material_data):
    """Return the exact writer DAG with narrow preview substitutions applied.

    Input byte strings must come from the source archive. The caller may feed
    the returned ``nodes``/``outputs``/``texture_bindings`` to the dedicated
    partial-source renderer. Invalid identities or changed contracts raise
    ``ValueError`` so the caller can retain its ordinary fallback.
    """
    import hashlib

    from rc2016.formats.index import references
    from rc2016.formats.materials import MATERIAL_TEXTURE_SLOTS, extract_material_parameters, texture_slot_records
    from rc2016.shaders.bindings import _parameter_values, pass_contracts, program_contracts
    from rc2016.shaders.engine_inputs import reconstruct_world_position
    from rc2016.shaders.ir import DAG
    from rc2016.shaders.translator import compile_graph, lower_deferred

    graph_data, material_data = bytes(graph_data), bytes(material_data)
    graph_sha = hashlib.sha256(graph_data).hexdigest()
    if graph_sha != GASPAR_LAVA_OCEAN_GRAPH_SHA256:
        raise ValueError("unsupported Gaspar lava-ocean graph SHA-256")
    if GASPAR_LAVA_OCEAN_GRAPH not in {
        row.get("path") for row in references(material_data)
        if not row.get("resolution_error")
    }:
        raise ValueError("material does not reference the exact Gaspar lava-ocean graph")

    from rc2016.pipeline.render_state import material_flags, decode_flags
    flags = material_flags(data=material_data)
    state = decode_flags(flags)
    if state['surface'] != 'opaque':
        raise ValueError('partial lava surface requires source opaque blend')
    contracts = pass_contracts(graph_data)
    if len(contracts) != 2:
        raise ValueError("Gaspar lava-ocean pass count changed")
    for contract in contracts:
        rows = contract["resources"]
        if len(rows) != len(GASPAR_LAVA_OCEAN_TEXTURES):
            raise ValueError("Gaspar lava-ocean resource contract changed")
        for api, row in zip(range(5, 10), rows):
            key, path = GASPAR_LAVA_OCEAN_TEXTURES[api]
            if row["slot_key"] != key or row["path"] != path:
                raise ValueError(f"Gaspar lava-ocean API{api} source slot changed")

    instance = extract_material_parameters(material_data)
    values_by_pass, receipts = [], []
    for contract in contracts:
        values, receipt = _parameter_values(contract, instance)
        values_by_pass.append(values)
        receipts.append(receipt)
    if values_by_pass[0] != values_by_pass[1]:
        raise ValueError("Gaspar lava-ocean CB2 values differ between passes")

    textures = {}
    material_slots = {}
    for row in texture_slot_records(material_data, MATERIAL_TEXTURE_SLOTS):
        key = (row["slot_key"], row["type_hash"])
        if key in material_slots and material_slots[key]["path"] != row["path"]:
            raise ValueError(f"conflicting material texture overrides for 0x{key[0]:08X}")
        material_slots[key] = row
    for api, row in zip(range(5, 10), contracts[0]["resources"]):
        if row["flag"] not in (0, 1):
            raise ValueError(f"unsupported source texture override flag for API{api}")
        override = material_slots.get((row["slot_key"], row["type_hash"])) if row["flag"] == 1 else None
        path = override["path"] if override is not None else row["path"]
        if not path:
            raise ValueError(f"Gaspar lava-ocean API{api} is explicitly unset")
        textures[api] = dict(
            api=api, path=path, graph_default_path=row["path"],
            slot_key=f"0x{row['slot_key']:08X}",
            source="material_instance" if override is not None else "graph_default",
        )

    compiled = compile_graph(graph_data, allow_independent_samplers=True)
    candidates = [p for p in compiled["programs"]
                  if p.get("shader_id", "").lower() == GASPAR_LAVA_OCEAN_SHADER_ID
                  and p.get("code_sha256", "").lower() == GASPAR_LAVA_OCEAN_WRITER_SHA256
                  and p.get("status") == "compiled"]
    if not candidates:
        raise ValueError("exact Gaspar lava-ocean deferred writer is unavailable")
    program = candidates[0]
    joined = program_contracts(graph_data, program, contracts, allow_independent_samplers=True)
    if len(joined) != 1:
        raise ValueError("Gaspar lava-ocean writer/pass binding is ambiguous")
    contract = joined[0]
    pass_index = contract["index"]
    if pass_index not in (0, 1):
        raise ValueError("Gaspar lava-ocean writer matched an unexpected pass")

    lowered = lower_deferred(program["ir"])
    ir = DAG(lowered["nodes"])
    output_roots = lowered["outputs"]
    roots = [root for value in output_roots.values()
             for root in (value if isinstance(value, list) else [value])]
    substitutions = {}
    for index, node in enumerate(ir.nodes):
        if node["op"] == "input" and node.get("kind") == "cb" and node.get("api") == 2:
            dword = node.get("dword")
            if dword in values_by_pass[pass_index]:
                substitutions[index] = ir.const(values_by_pass[pass_index][dword])
        elif node["op"] == "input" and node.get("kind") == "cb" and node.get("api") == 0:
            if node.get("dword") == 99:
                substitutions[index] = ir.const(0.0)
            elif node.get("dword") == 115:
                substitutions[index] = ir.const(1.0)
        elif node["op"] == "unsupported" and node.get("reason") in {
            "V_CUBEMA_F32", "V_CUBETC_F32", "V_CUBESC_F32",
        }:
            substitutions[index] = ir.const(0.0)
        elif node["op"] == "texture" and node.get("api") == 9:
            substitutions[index] = ir.const(0.0)
    replaced = ir.replace(roots, substitutions)
    replaced, adapted = reconstruct_world_position(ir, replaced)
    if not adapted:
        raise ValueError("Gaspar lava-ocean world-position expression shape changed")
    from rc2016.shaders.bindings import guard_source_zero_powers
    replaced, power_guards = guard_source_zero_powers(ir, replaced)
    used = ir.reachable(replaced)
    unsupported = [ir.nodes[i] for i in used if ir.nodes[i]["op"] == "unsupported"]
    if unsupported:
        raise ValueError("unsupported operations remain after explicit reflection substitution")
    bad_inputs = [ir.nodes[i].get("name") for i in used if ir.nodes[i]["op"] == "input"
                  and ir.nodes[i].get("kind") != "preview_position"]
    if bad_inputs:
        raise ValueError("unbound inputs remain after source substitutions: " + ", ".join(sorted(set(bad_inputs))))

    compact = DAG()
    remap = {}

    def copy(index):
        if index not in remap:
            node = ir.nodes[index]
            remap[index] = compact.node(
                node["op"], [copy(arg) for arg in node["args"]],
                **{key: value for key, value in node.items() if key not in ("op", "args")},
            )
        return remap[index]

    roots_compact = [copy(index) for index in replaced]
    outputs = dict(albedo=roots_compact[:3], F0=roots_compact[3:6],
                   gloss=roots_compact[6], emission=roots_compact[7])
    active_apis = sorted({node["api"] for node in compact.nodes if node["op"] == "texture"})
    if any(api not in textures for api in active_apis):
        raise ValueError("live source texture API lacks an exact graph resource binding")
    active_textures = [textures[api] for api in active_apis]
    return {
        "status": "partial_source_rule",
        "preview_contract": "gaspar-lava-source-v1",
        "graph_path": GASPAR_LAVA_OCEAN_GRAPH,
        "graph_sha256": graph_sha,
        "material_sha256": hashlib.sha256(material_data).hexdigest(),
        "material_flags": flags,
        "two_sided": state["backface"] == "show",
        "writer_sha256": GASPAR_LAVA_OCEAN_WRITER_SHA256,
        "shader_id": GASPAR_LAVA_OCEAN_SHADER_ID,
        "pass_index": pass_index,
        "nodes": compact.nodes,
        "outputs": outputs,
        "normal": None,
        "texture_bindings": active_textures,
        "parameters": receipts[pass_index],
        "source_substitutions": {
            "CB0[99]": {"value": 0.0, "meaning": "frozen preview time; runtime name/units unknown"},
            "CB0[115]": {"value": 1.0, "meaning": "review amplitude; source multiplier role, runtime value unknown"},
            "API9": {"value": 0.0, "meaning": "black cube reflection review substitution"},
        },
        "world_position_terms_replaced": adapted,
        "source_zero_power_guards": power_guards,
        "texture_apis": active_apis,
        "limitations": [
            "CB0[99] is frozen at zero for the preview; runtime upload name and units are unresolved.",
            "CB0[115] is set to one as an explicit review amplitude; its runtime value is unresolved.",
            "API9 cube reflection is black in this review preview.",
            "Matched camera/ray*POS_W source expressions are replaced by preview world X/Z; this engine meaning is inferred from expression shape.",
            "The deferred writer does not recover a portable tangent normal; geometry normal is used.",
            "Runtime pass/draw selection and game equivalence are not established.",
        ],
    }


def gaspar_lava_ocean_rule(graph_data, material_data):
    """Backward-compatible explicit name for :func:`surface_rule`."""
    return surface_rule(graph_data, material_data)


def valid_contract(receipt):
    """Whether a receipt has the exact bounded Gaspar lava-ocean identity."""
    if not isinstance(receipt, dict):
        return False
    return (
        receipt.get("status") == "partial_source_rule"
        and receipt.get("preview_contract") == "gaspar-lava-source-v1"
        and receipt.get("graph_path") == GRAPH
        and receipt.get("graph_sha256") == GRAPH_SHA256
        and receipt.get("writer_sha256") == GASPAR_LAVA_OCEAN_WRITER_SHA256
        and receipt.get("shader_id") == GASPAR_LAVA_OCEAN_SHADER_ID
        and receipt.get("normal") is None
        and set(receipt.get("outputs", {})) == {"albedo", "F0", "gloss", "emission"}
        and set(receipt.get("texture_apis", ())) <= {5, 6, 7}
    )
