"""Hash-bound shader-family preflight for extracted materialgraphs.

This is interpretation metadata, not a materialgraph compiler. A graph is
recognized only when both its normalized source path and decoded SHA-256 match
an entry below. Unknown variants remain explicitly unsupported.
"""
import hashlib
import math
import struct

from .common import asset_path

PARAM_TABLES_SECTION = 0x45C4F4C0
PARAM_DEFAULTS_SECTION = 0xA59F667B
EMISSIVE_K_KEY = 0x66EFA55B

# Instance-overridable texture slots of the basic_skin graph family (section 0x1CAFE804,
# byte-identical in skin_emissive and basic_skin). Their order is the forward shader's
# API5/6/7/11 order observed in BED06840 (spec/gloss, normal, second normal, colour); in
# F0CD85FF the second normal is sampled at UV0 * table-0 key 0xB7ABB64A.
SKIN_TEXTURE_SLOTS = {"0x93CA35AB": "colored_specular_gloss", "0x5E5B04FB": "normal",
                      "0x7DF1B1FC": "secondary_normal", "0x0056CBD0": "base_color"}

# Instance texture slots shared by the basic_normal_gloss / basic_normal_coloredgloss_emissive /
# basic_incandescence graphs (section 0x1CAFE804 in each). Roles come from how each graph's
# cached deferred writer uses the sampled image, not from the slot-table order, which differs
# per graph. "colored_specular_gloss" is the pipeline role of the _g map; its channel meaning
# is per graph (see DEFERRED_FAMILY_SEMANTICS).
STANDARD_TEXTURE_SLOTS = {"0xABDAD780": "base_color", "0x1576D848": "colored_specular_gloss",
                          "0x7AE34E7A": "normal"}
SPECULAR_SCALE_KEY = 0x62835AF9
GLOSS_SCALE_KEY = 0x24F4CA4C
COLOREDGLOSS_SELF_LIT_K_KEY = 0x06F19114
INCANDESCENCE_INTENSITY_KEY = 0x5FD56522
INCANDESCENCE_PULSE_RATE_KEY = 0x41E7FE54
CSECIA_PULSE_RATE_KEY = 0xAF5BEBBC
CSECIA_SELF_LIT_K_KEY = 0xF8C61683
CSECIA_NORMAL_BLEND_KEY = 0x173F37E2
_CSECIA_DECODE = "Other models/enm_telepathipus_v2/evidence/CSECIA_DECODE.md"
_BLARG_SPIRV = "Other models/enm_blarg_space_commando/evidence/spirv/"
_BLARG_DECODE = "Other models/enm_blarg_space_commando/evidence/DEFERRED_FAMILIES_DECODE.md"
_STANDARD_LIMITATIONS = [
    "Only the cached deferred G-buffer writer is decoded; forward/other variants and runtime variant selection are unresolved.",
    "The writer's geometric-AA gloss term is taken as 1 (no derivative-based gloss reduction).",
]


def _standard_semantics(rows):
    return {0: dict(rows), 1: dict(rows)}


# character_glass forward pixel shader 34D5B21C, native straight-line slices
# (Other models/enm_blarg_space_commando/evidence/glass). Keys are cross-checked against the
# captured character_glass_02 SPIR-V (Micron 4DF1D438), which uses the same keys in the same roles.
CHARACTER_GLASS_KEYS = {"body_colour": 0x7C456482, "gloss": 0x24F4CA4C, "reflection_tint": 0x4B4DAB19,
                        "reflection_intensity": 0xEF9CE5DD, "alpha_exponent": 0x2B68D24C,
                        "env_scale": 0x80C092EE, "alpha_bias": 0x4E5B65FC, "rim_bias": 0x42170E5E,
                        "env_exponent": 0xACE4D867, "rim_exponent": 0xECE213A3}
_GLASS_VEC3 = {"body_colour", "reflection_tint"}
_GLASS_DECODE = "Other models/enm_blarg_space_commando/evidence/glass/CHARACTER_GLASS_DECODE.md"


# Partial recognition is intentional: the cached deferred writer and bounded
# native forward slices do not establish the entire graph implementation.
FAMILY_REGISTRY = {
    ("materialgraph/global/basic_glass/character_glass.materialgraph",
     "be4eb2847693739c596cad28b11af11a98b261c60aa54ce42f72d60574a62a54"): {
        "family": "character_glass",
        "family_status": "supported_partial",
        "evidence": "Native GCN straight-line slices of forward pixel shader 0x34D5B21C (500-case numerical control); see " + _GLASS_DECODE + ".",
        "evidence_sources": [{"path": "Other models/enm_blarg_space_commando/evidence/glass/native_glass_slices.txt",
                              "code_sha256": "57748bea173fe6c41aadfda61e8abbb8ac09ae9f49cbddbd8f0ef91db1f4dc27",
                              "scope": "Alpha curve, gloss, reflection tint and final composite; branch-accumulated lighting stays an input."},
                             {"path": "Other models/enm_blarg_space_commando/evidence/glass/micron_4df1d438.glsl",
                              "scope": "Captured sibling graph (character_glass_02): same 53-branch skeleton and key roles; blend ADD SRC_ALPHA/ONE."}],
        "texture_slots": {},
        "shader_variants": [
            {"identity": "0x34d5b21c", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Forward lit glass: material prefix and composite traced; light loop, probes and SSR are engine inputs."},
            {"identity": "0xdff763a3", "stage": "PIXEL", "status": "unresolved_variant", "scope": "Second long forward variant; not traced."},
            {"identity": "0x4aec89aa", "stage": "PIXEL", "status": "unresolved_variant", "scope": "Short variant (depth/prepass); not traced."},
        ],
        "parameter_semantics": {t: {key: (name, "float32x3" if name in _GLASS_VEC3 else "float32")
                                    for name, key in CHARACTER_GLASS_KEYS.items()} for t in (0, 1)},
        "limitations": [
            "Blend and cull come from each material's flags word (.material section 0xE1275683; Claude_shading_fix/render_state/README.md): flags & 3 = 1 additive, 2 alpha-over. The Blarg visor (0x0020003A) is alpha-over. No capture of a character_glass draw exists.",
            "Per-instance fade (instance buffer) and interpolant attr0.w are taken as 0 and 1; the flag-gated dissolve/alpha-test block is not modelled.",
            "Light-loop specular and probe/SSR reflection are represented by a Cycles GGX lobe tinted by the decoded reflection tint; their inner engine equations are not traced.",
            "The env-probe colour E in the body term is a constant (captured ambient radiance when available).",
            "Fog is not applied in the material.",
        ],
    },
    ("required/materials/basic_normal_gloss.materialgraph",
     "adf901e345f6eb3d336badd32215bb1f7cfd018450fc3169774d17423aaca1b9"): {
        "family": "basic_normal_gloss",
        "family_status": "supported_partial",
        "evidence": "Cached shadPS4 SPIR-V of deferred writer 0x5ebdeca4, decompiled with spirv-cross; see " + _BLARG_DECODE + ".",
        "evidence_sources": [{"path": _BLARG_SPIRV + "5ebdeca4.glsl",
                              "scope": "Complete deferred writer: albedo = _c.rgb, grey F0 = spec*_g.G, gloss = sqrt(sat(gloss*_g.R)), native XY normal, no self-lit."}],
        "texture_slots": STANDARD_TEXTURE_SLOTS,
        "shader_variants": [
            {"identity": "0x5ebdeca4", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Deferred writer (two cached suffix variants, identical code); also embedded by the _aniso/_detail/_monitor_dep graphs, which are not registered.",
             "observed_bindings": {"img0": "_c", "img1": "_n", "img2": "_g (R gloss, G grey specular)"}},
            {"identity": "0x37af5b69", "stage": "PIXEL", "status": "unresolved_variant", "scope": "Large forward variant; not compared."},
            {"identity": "0xb2866be3", "stage": "PIXEL", "status": "unresolved_variant", "scope": "Not compared."},
        ],
        "parameter_semantics": _standard_semantics({SPECULAR_SCALE_KEY: ("specular_scale", "float32"),
                                                    GLOSS_SCALE_KEY: ("gloss_scale", "float32")}),
        "limitations": list(_STANDARD_LIMITATIONS),
    },
    ("required/materials/basic_normal_coloredgloss_emissive.materialgraph",
     "a4001db8313fa440618b6c302196b4fd3041fc211032d2f348ef240e5be46f1c"): {
        "family": "basic_normal_coloredgloss_emissive",
        "family_status": "supported_partial",
        "evidence": "Cached shadPS4 SPIR-V of deferred writer 0xc4828176, decompiled with spirv-cross; see " + _BLARG_DECODE + ".",
        "evidence_sources": [{"path": _BLARG_SPIRV + "c4828176.glsl",
                              "scope": "Complete deferred writer: albedo = _c.rgb, F0 = spec*_g.rgb, gloss = sqrt(sat(gloss*_g.a^2)), self-lit = K*_c.a, native XY normal."}],
        "texture_slots": STANDARD_TEXTURE_SLOTS,
        "shader_variants": [
            {"identity": "0xc4828176", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Deferred writer; the only cached pixel shader of this graph.",
             "observed_bindings": {"img0": "_n", "img1": "_g (RGB coloured F0, A gloss)", "img2": "_c (A self-lit mask)"}},
        ],
        "parameter_semantics": _standard_semantics({COLOREDGLOSS_SELF_LIT_K_KEY: ("self_lit_K", "float32"),
                                                    SPECULAR_SCALE_KEY: ("specular_scale", "float32"),
                                                    GLOSS_SCALE_KEY: ("gloss_scale", "float32")}),
        "limitations": list(_STANDARD_LIMITATIONS),
    },
    ("materialgraph/global/basic_colored_spec_emmisive_in_c_alpha/basic_colored_spec_emmisive_in_c_alpha.materialgraph",
     "5cb9d81d537d974c132995afac1b5d0d0c6404e50597b3fc69c1fb29cc8cf1e3"): {
        "family": "basic_colored_spec_emmisive_in_c_alpha",
        "family_status": "supported_partial",
        "evidence": "Native GCN of deferred writer 0x89638DFB (no pixel shader of this graph is in the shadPS4 cache), read against the SPIR-V-decoded control 0xC4828176; see " + _CSECIA_DECODE + ".",
        "evidence_sources": [{"path": "Other models/enm_telepathipus_v2/evidence/native/csecia_89638DFB_listing.txt",
                              "scope": "Deferred writer: albedo = _c.rgb, F0 = spec*_g.rgb, gloss = sqrt(sat(gloss*_g.a^2)), self-lit = K*cos(rate*t)*_c.a, view-faded native XY normal; dw4 > 0 enables an unmodelled normal blend."},
                             {"path": "Other models/enm_telepathipus_v2/evidence/native/ncge_C4828176_listing.txt",
                              "scope": "Control: native listing of the SPIR-V-decoded basic_normal_coloredgloss_emissive writer reproduces its known equations."}],
        "texture_slots": STANDARD_TEXTURE_SLOTS,
        "shader_variants": [
            {"identity": "0x89638dfb", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Deferred writer; CB2 = graph table 0 dwords 0-4, frame CB dwords 95 (normal fade) and 99 (time).",
             "observed_bindings": {"api5": "_g (RGB coloured F0, A gloss)", "api6": "_n (RG)", "api7": "_c (A self-lit mask)"}},
            {"identity": "0x4ac85eb4", "stage": "PIXEL", "status": "unresolved_variant",
             "scope": "Writer variant with an extra api33 mask (pairs with control 0x32E740EB); not traced."},
            {"identity": "0x6dabd5b2", "stage": "PIXEL", "status": "unresolved_variant", "scope": "Large forward variant; not traced."},
            {"identity": "0xa2c927b7", "stage": "PIXEL", "status": "unresolved_variant", "scope": "Large forward variant; not traced."},
        ],
        "parameter_semantics": _standard_semantics({CSECIA_PULSE_RATE_KEY: ("self_lit_pulse_rate", "float32"),
                                                    CSECIA_SELF_LIT_K_KEY: ("self_lit_K", "float32"),
                                                    SPECULAR_SCALE_KEY: ("specular_scale", "float32"),
                                                    GLOSS_SCALE_KEY: ("gloss_scale", "float32"),
                                                    CSECIA_NORMAL_BLEND_KEY: ("normal_blend_weight", "float32")}),
        "limitations": list(_STANDARD_LIMITATIONS) + [
            "Decoded from native GCN (no cached SPIR-V); the method is controlled by the SPIR-V-decoded sibling writer 0xC4828176.",
            "The self-lit pulse cos(rate*t) (not remapped to 0..1) uses frame time; the material is shown at t = 0 (cos = 1). How the G-buffer stores negative values is unknown.",
            "The normal XY scale fades with view depth (frame CB dword 95); the close-range value (x = 2R-1) is assumed, as for basic_incandescence.",
            "Key 0x173F37E2 > 0 enables a view-dependent normal blend that is not modelled; it is flagged when nonzero.",
        ],
    },
    ("materialgraph/global/basic_incandescence/basic_incandescence.materialgraph",
     "4e1bad9de62b7c28ca4a5d5638a0a573a45a08f5d4be0153d5f3027d5f843aa2"): {
        "family": "basic_incandescence",
        "family_status": "supported_partial",
        "evidence": "Cached shadPS4 SPIR-V of deferred writers 0x5dbcccb7 (normal-mapped, CB = table 0) and 0x42254271 (no normal, CB = table 1); both give the same per-key semantics. See " + _BLARG_DECODE + ".",
        "evidence_sources": [{"path": _BLARG_SPIRV + "5dbcccb7.glsl",
                              "scope": "Deferred writer: albedo = _c.rgb, grey F0 = spec*_g.G, gloss = sqrt(sat(gloss*_g.R)), self-lit = intensity*_g.B*(0.5cos(rate*t)+0.5), view-faded native XY normal."},
                             {"path": _BLARG_SPIRV + "42254271.glsl",
                              "scope": "Normal-less deferred writer; cross-checks the key semantics through the other CB layout."}],
        "texture_slots": STANDARD_TEXTURE_SLOTS,
        "shader_variants": [
            {"identity": "0x5dbcccb7", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Deferred writer with normal map; reads CB in graph table-0 layout.",
             "observed_bindings": {"img0": "_g (R gloss, G grey specular, B incandescence mask)", "img1": "_c", "img2": "_n"}},
            {"identity": "0x42254271", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Deferred writer without normal map; reads CB in graph table-1 layout.",
             "observed_bindings": {"img0": "_g", "img1": "_c"}},
        ],
        "parameter_semantics": _standard_semantics({INCANDESCENCE_PULSE_RATE_KEY: ("incandescence_pulse_rate", "float32"),
                                                    INCANDESCENCE_INTENSITY_KEY: ("incandescence_intensity", "float32"),
                                                    SPECULAR_SCALE_KEY: ("specular_scale", "float32"),
                                                    GLOSS_SCALE_KEY: ("gloss_scale", "float32")}),
        "limitations": list(_STANDARD_LIMITATIONS) + [
            "The incandescence pulse 0.5*cos(rate*t)+0.5 uses frame time; it is exact only when the rate is 0 (pulse = 1). Nonzero rates are shown at the pulse peak and labelled.",
            "The normal XY scale fades with view depth (frame CB dword 95 and per-instance fade); the close-range value (x = 2R-1) is assumed, as for basic_skin.",
        ],
    },
    ("materialgraph/global/basic_skin/basic_skin.materialgraph",
     "94922c9be24a9e893841ef0641a071c9ca40cbb46a335a3a49f2bd220579618c"): {
        "family": "basic_skin",
        "family_status": "supported_partial",
        "evidence": "Cached shadPS4 SPIR-V of deferred writer 0x9aad640b and forward pixel shader 0xf0cd85ff, decompiled with spirv-cross; see Other models/enm_puffer_fish/evidence/BASIC_SKIN_DECODE.md.",
        "evidence_sources": [{"path": "Other models/enm_puffer_fish/evidence/spirv/9aad640b.glsl",
                              "scope": "Complete deferred writer: albedo/F0/gloss front-end, no self-lit output."},
                             {"path": "Other models/enm_puffer_fish/evidence/spirv/f0cd85ff.glsl",
                              "scope": "Forward material prefix (detail-normal blend, Fresnel, spec/gloss) and colour suffix; per-light skin term read but not reproduced."}],
        "texture_slots": SKIN_TEXTURE_SLOTS,
        "shader_variants": [
            {"identity": "0xf0cd85ff", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Forward shader; material front-end and detail-normal blend decoded from SPIR-V. Per-light skin ramp/scatter term not reproduced in Cycles.",
             "observed_bindings": {"img1": "normal (UV0)", "img2": "secondary_normal (UV0 * key 0xB7ABB64A)",
                                   "img3": "_g (R gloss, G spec, B detail-normal weight, A skin term)",
                                   "img6": "skin_ramp", "img12": "_c"}},
            {"identity": "0xadd87f78", "stage": "PIXEL", "status": "unresolved_variant",
             "scope": "Second forward variant (cached); not compared."},
            {"identity": "0x9aad640b", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Deferred writer; complete front-end decoded (parameter table 1)."},
            {"identity": "0xd1c874f4", "stage": "PIXEL", "status": "unresolved_variant",
             "scope": "Second deferred writer (cached); not compared."},
        ],
        "parameter_semantics": {0: {
            0xCFCCABE4: ("skin_light_term_tint", "float32x3"),
            0xE6185DBC: ("specular_fresnel_multiplier", "float32"),
            0x59011FBD: ("secondary_normal_blend", "float32"),
            0x03168D4E: ("specular_cap", "float32"),
            0xA81E0932: ("fresnel_exponent", "float32"),
            0xF503794A: ("gloss_scale", "float32"),
            0xB7ABB64A: ("secondary_normal_uv_scale", "float32"),
            0x3055F730: ("skin_ramp_v_coordinate", "float32"),
            0xF38FFA75: ("albedo_scale", "float32"),
            0xC2473357: ("specular_scale", "float32"),
            0xAB221207: ("gloss_fresnel_multiplier", "float32"),
        }, 1: {
            0x03168D4E: ("specular_cap", "float32"),
            0xAB221207: ("gloss_fresnel_multiplier", "float32"),
            0xE6185DBC: ("specular_fresnel_multiplier", "float32"),
            0xA81E0932: ("fresnel_exponent", "float32"),
            0xF38FFA75: ("albedo_scale", "float32"),
            0xF503794A: ("gloss_scale", "float32"),
            0xC2473357: ("specular_scale", "float32"),
        }},
        "limitations": [
            "The forward shader's per-light skin term (_g.A^2, tint 0xCFCCABE4, skin_ramp, per-channel wrap 0x991DAC4F, shadows) needs per-light shading and is not reproduced in Cycles.",
            "Variants 0xadd87f78 and 0xd1c874f4 and runtime variant selection are unresolved.",
            "The forward normal scale uses a per-view distance fade (frame CB dword 95); the close-range value 2 (x = 2R-1) is assumed, as for skin_emissive.",
        ],
    },
    ("materialgraph/global/basic_skin/skin_emissive.materialgraph",
     "5a4a9490c33d0a87e4e360ee9be1c1417c6f1b8e2ee307773d4aadce5ae59437"): {
        "family": "skin_emissive",
        "family_status": "supported_partial",
        "texture_slots": SKIN_TEXTURE_SLOTS,
        "evidence": "Native skin slice evidence; code SHA-256 9ee3dbce0a9bf96eb2f65b7eed91ee446bec3975b9d3b217de303c9235f8dd56.",
        "evidence_sources": [{"path": "Other models/enm_toxic_crab/evidence/native_skin_slices.json",
                              "code_sha256": "9ee3dbce0a9bf96eb2f65b7eed91ee446bec3975b9d3b217de303c9235f8dd56",
                              "scope": "BED06840 straight-line material prefix/suffix; branch-accumulated lighting remains an input."},
                             {"path": "Other models/enm_toxic_crab/evidence/native_skin_ramp_slices.json",
                              "code_sha256": "9ee3dbce0a9bf96eb2f65b7eed91ee446bec3975b9d3b217de303c9235f8dd56",
                              "scope": "API10.R UV and additive diffuse contribution; branch-derived factors remain inputs."}],
        "shader_variants": [
            {"identity": "0x7d3f5fa6", "stage": "VERTEX", "status": "unresolved_variant",
             "scope": "Native variant identified; arithmetic and runtime selection unresolved."},
            {"identity": "0xd7749b42", "stage": "VERTEX", "status": "unresolved_variant",
             "scope": "Native variant identified; arithmetic and runtime selection unresolved."},
            {"identity": "0xbed06840", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Partial material normal, Fresnel/spec/gloss, self-lit, and skin-ramp slices recovered; full forward lighting and runtime choice unresolved.",
             "observed_bindings": {"API5": "spec_gloss", "API6": "normal", "API7": "secondary_normal",
                                   "API10": "skin_ramp (observed twice)", "API11": "_c"}},
            {"identity": "0xae7244fe", "stage": "PIXEL", "status": "unresolved_variant",
             "scope": "Large pixel shader with full forward-lighting MRT0 export; full arithmetic unresolved."},
            {"identity": "0x567fe897", "stage": "PIXEL", "status": "supported_partial",
             "scope": "Cached deferred writer only; reconstructed front-end equations and parameter table 1."},
            {"identity": "0x6830e968", "stage": "PIXEL", "status": "unresolved_variant",
             "scope": "Deferred writer identified; its equations and runtime selection are unresolved."},
        ],
        # Table 1 meanings come from cached writer 567FE897. Table 0 meanings
        # are restricted to independently traced BED06840 material/ramp slices.
        "parameter_semantics": {0: {
            0xCFCCABE4: ("skin_ramp_diffuse_tint", "float32x3"),
            0xE6185DBC: ("specular_fresnel_multiplier", "float32"),
            0x59011FBD: ("secondary_normal_blend", "float32"),
            0x03168D4E: ("specular_cap", "float32"),
            0xA81E0932: ("fresnel_exponent", "float32"),
            0xF503794A: ("gloss_scale", "float32"),
            0xF38FFA75: ("albedo_scale", "float32"),
            0xC2473357: ("specular_scale", "float32"),
            EMISSIVE_K_KEY: ("self_lit_K", "float32"),
            0xAB221207: ("gloss_fresnel_multiplier", "float32"),
            0x3055F730: ("skin_ramp_v_coordinate", "float32"),
        }, 1: {
            0x03168D4E: ("specular_cap", "float32"),
            0xAB221207: ("gloss_fresnel_multiplier", "float32"),
            0xE6185DBC: ("specular_fresnel_multiplier", "float32"),
            0xA81E0932: ("fresnel_exponent", "float32"),
            0xF38FFA75: ("albedo_scale", "float32"),
            0xF503794A: ("gloss_scale", "float32"),
            EMISSIVE_K_KEY: ("self_lit_K", "float32"),
            0xC2473357: ("specular_scale", "float32"),
        }},
        "limitations": [
            "The two VERTEX variants, full forward-lighting PIXEL implementations, and deferred writer 0x6830e968 remain unresolved.",
            "BED06840-derived material front-end is partial; forward lighting, skin_ramp, and runtime variant selection are unresolved.",
            "BED06840 ramp evidence establishes API10.R UV sampling and an additive diffuse term only; branch factors are unresolved.",
            "Graph references do not establish use of skin_micro_details_n/_v; full skin_ramp response is not implemented.",
            "The cached deferred writer and BED06840 recovered slices are partial; full forward lighting and runtime variant selection are not represented.",
        ],
    },
}


def graph_parameter_tables(data):
    """Parse bounded graph CB tables/defaults, preserving byte offsets."""
    data = bytes(data)
    if len(data) < 16 or struct.unpack_from("<I", data, 0)[0] != 0x44415431:
        raise ValueError("Invalid materialgraph DAT1 header")
    declared, count = struct.unpack_from("<IH", data, 8)
    if declared != len(data) or 16 + count * 12 > declared:
        raise ValueError("Invalid materialgraph section bounds")
    sections = {}
    for i in range(count):
        tag, off, size = struct.unpack_from("<III", data, 16 + i * 12)
        if off < 16 + count * 12 or off + size > declared or tag in sections:
            raise ValueError("Invalid or duplicate materialgraph section")
        sections[tag] = (off, size)
    if PARAM_TABLES_SECTION not in sections:
        return [], {}
    off, size = sections[PARAM_TABLES_SECTION]
    if size % 8:
        raise ValueError("Materialgraph parameter table is not 8-byte aligned")
    tables, current = [], []
    for pos in range(off, off + size, 8):
        byte_offset, byte_size, key = struct.unpack_from("<HHI", data, pos)
        if byte_offset % 4 or byte_size % 4:
            raise ValueError("Materialgraph parameter layout is not dword aligned")
        if byte_offset == 0 and current:
            tables.append(current)
            current = []
        current.append({"dword": byte_offset // 4, "size_bytes": byte_size,
                        "key_hash": key, "record_file_offset": pos})
    if current:
        tables.append(current)
    defaults = {}
    if PARAM_DEFAULTS_SECTION in sections and tables:
        default_off, default_size = sections[PARAM_DEFAULTS_SECTION]
        for entry in tables[0]:
            n = entry["size_bytes"]
            start = default_off + entry["dword"] * 4
            if n and start + n <= default_off + default_size:
                vals = struct.unpack_from("<%df" % (n // 4), data, start)
                if all(math.isfinite(v) for v in vals):
                    defaults[entry["key_hash"]] = vals[0] if len(vals) == 1 else list(vals)
    return tables, defaults


def preflight_graph(path, data, instance_parameters):
    """Classify by exact source identity and report exposed CB bindings."""
    path = asset_path(path)
    digest = hashlib.sha256(bytes(data)).hexdigest()
    spec = FAMILY_REGISTRY.get((path, digest))
    tables, graph_defaults = graph_parameter_tables(data)
    instance_rows = {}
    for row in ((instance_parameters or {}).get("parameters", []) +
                (instance_parameters or {}).get("unsupported_parameters", [])):
        try:
            raw_key = row.get("key_hash", row.get("key", "0"))
            key = int(raw_key, 0) if isinstance(raw_key, str) else int(raw_key)
        except (TypeError, ValueError):
            continue
        instance_rows[key] = row
    parameter_rows = []
    semantic_tables = spec["parameter_semantics"] if spec else {}
    for table_index, table in enumerate(tables):
        for entry in table:
            key = entry["key_hash"]
            semantic = semantic_tables.get(table_index, {}).get(key)
            row = dict(table_index=table_index, **entry,
                       meaning=semantic[0] if semantic else None,
                       declared_type=semantic[1] if semantic else "unknown")
            inst = instance_rows.get(key)
            if inst is not None:
                raw_hex = inst.get("raw_hex", "")
                row.update(value_source="material_instance", instance_value=inst.get("value"),
                           instance_raw_hex=raw_hex)
                if semantic and semantic[1].startswith("float32"):
                    raw = bytes.fromhex(raw_hex)
                    if len(raw) % 4 == 0 and len(raw) == entry["size_bytes"]:
                        values = struct.unpack("<%df" % (len(raw) // 4), raw)
                        if all(math.isfinite(value) for value in values):
                            row["interpreted_instance_value"] = values[0] if len(values) == 1 else list(values)
            elif key in graph_defaults:
                row.update(value_source="graph_default", graph_default=graph_defaults[key])
            else:
                row["value_source"] = "unresolved"
            parameter_rows.append(row)
    return {
        "schema": "rcps4-shader-family-preflight-v1",
        "source_path": path,
        "source_sha256": digest,
        "family": spec["family"] if spec else None,
        "status": spec["family_status"] if spec else "unsupported_graph_identity",
        "evidence": spec["evidence"] if spec else "No exact path-and-hash family registration.",
        "evidence_sources": list(spec.get("evidence_sources", [])) if spec else [],
        "shader_variants": spec["shader_variants"] if spec else [],
        "texture_slots": dict(spec.get("texture_slots", {})) if spec else {},
        "parameter_tables": parameter_rows,
        "limitations": list(spec["limitations"]) if spec else [
            "Compiled shader family and runtime variant are unsupported; do not infer semantics from graph or texture names."],
    }
