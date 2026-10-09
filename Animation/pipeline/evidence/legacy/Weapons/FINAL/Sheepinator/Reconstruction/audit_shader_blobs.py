"""Audit compiled Sheepinator materialgraph shader sections.

This records section offsets, real GNM Shdr headers, OrbShdr metadata, engine
reflection symbols, exact IEEE-754 literal hits, and a conservative GCN
instruction-shape scan. The scan decodes only public/local GCN field layouts
(instruction class, image samples, exports, and literal words); it does not
claim to recover the material graph's data flow. The trailing "Shdr" in each
"OrbShdr" signature is not counted as a second shader header.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[7]
ALERT = PROJECT / "Animation" / "pipeline" / "references" / "ALERT"
sys.path.insert(0, str(ALERT))
import dat1lib  # noqa: E402
from dat1lib.types.dat1 import DAT1  # noqa: E402


GRAPHS = {
    "body_coloredgloss": PROJECT / "Animation" / "pipeline" / "NativeExample" / "decoded_sources" / "6046b3bca1afc2c8b065" / "required" / "materials" / "basic_normal_coloredgloss_emissive.materialgraph",
    "glass_character": PROJECT / "Animation" / "pipeline" / "NativeExample" / "decoded_sources" / "25b91930b5c6420e65c3" / "materialgraph" / "global" / "basic_glass" / "character_glass.materialgraph",
    "internal_energy": PROJECT / "Animation" / "pipeline" / "evidence" / "legacy" / "Weapons" / "FINAL" / "Sheepinator" / "Source Archives" / "Materials" / "wpn_sheepinator_internal_energy.materialgraph",
}
VALUES = {
    "minimum_energy_opacity": 0.015,
    "energy_opacity_scale": 0.065,
    "glass_tuned_roughness": 0.12,
    "reconstruction_mix": 0.18,
    "principled_specular": 0.32,
    "red_saturation": 1.8,
    "red_value": 2.8,
    "source_glossiness": 0.8,
    "source_glass_roughness_candidate": 0.2,
    "body_roughness_floor": 0.3,
}
SYMBOLS = (
    "diff_emis",
    "spec_gloss",
    "fs_in.m_Normal",
    "m_Color",
    "m_Custom",
    "m_CombinedAlpha",
    "g_Frame_ShaderTimer",
    "g_Frame_DeltaTime",
    "g_VP_TimeOfDay",
)

# The audit intentionally follows the checked-in local opengnm implementation,
# rather than silently assuming a generic GCN dialect.  Keep these paths and
# their hashes in the report so a later decoder update is visible.
OPENGNM = PROJECT / "Animation" / "pipeline" / "references" / "opengnm"
OPENGNM_SOURCES = (
    OPENGNM / "src" / "gcn" / "microcode.h",
    OPENGNM / "src" / "gcn" / "decoder.c",
    OPENGNM / "src" / "gcn" / "gcn_types.h",
    OPENGNM / "include" / "gnm_shaderbinary.h",
    OPENGNM / "include" / "gnm_shader.h",
)
USAGE_TYPES = {
    0x00: "IMM_RESOURCE",
    0x01: "IMM_SAMPLER",
    0x02: "IMM_CONSTBUFFER",
    0x03: "IMM_VERTEXBUFFER",
    0x04: "IMM_RWRESOURCE",
    0x05: "IMM_ALUFLOATCONST",
    0x06: "IMM_ALUBOOL32CONST",
    0x0A: "IMM_SRT",
    0x12: "SUBPTR_FETCHSHADER",
    0x13: "PTR_RESOURCETABLE",
    0x14: "PTR_INTERNALRESOURCETABLE",
    0x15: "PTR_SAMPLERTABLE",
    0x16: "PTR_CONSTBUFFERTABLE",
    0x17: "PTR_VERTEXBUFFERTABLE",
    0x18: "PTR_SOBUFFERTABLE",
    0x19: "PTR_RWRESOURCETABLE",
    0x1A: "PTR_INTERNALGLOBALTABLE",
    0x1B: "PTR_EXTENDEDUSERDATA",
    0x1C: "PTR_INDIRECTRESOURCETABLE",
    0x1D: "PTR_INDIRECTINTERNALRESOURCETABLE",
    0x1E: "PTR_INDIRECTRWRESOURCETABLE",
}
SHADER_TYPES = {
    0x01: "VERTEX",
    0x02: "PIXEL",
    0x03: "GEOMETRY",
    0x04: "COMPUTE",
    0x05: "EXPORT",
    0x06: "LOCAL",
    0x07: "HULL",
}
SHADER_BINARY_TYPES = {
    0: "PS",
    1: "VS_VS",
    2: "VS_ES",
    3: "VS_LS",
    4: "CS",
    5: "GS",
    6: "GS_VS",
    7: "HS",
    8: "DS_VS",
    9: "DS_ES",
}

MICROCODE_NAMES = {
    0: "SOP1",
    1: "SOP2",
    2: "SOPC",
    3: "SOPK",
    4: "SOPP",
    5: "VOP1",
    6: "VOP2",
    7: "VOP3",
    8: "VOPC",
    9: "VINTRP",
    10: "SMRD",
    11: "DS",
    12: "MUBUF",
    13: "MTBUF",
    14: "MIMG",
    15: "EXP",
}
MIMG_OP_NAMES = {
    0: "IMAGE_LOAD",
    1: "IMAGE_LOAD_MIP",
    2: "IMAGE_LOAD_PCK",
    3: "IMAGE_LOAD_PCK_SGN",
    4: "IMAGE_LOAD_MIP_PCK",
    5: "IMAGE_LOAD_MIP_PCK_SGN",
    8: "IMAGE_STORE",
    9: "IMAGE_STORE_MIP",
    10: "IMAGE_STORE_PCK",
    11: "IMAGE_STORE_MIP_PCK",
    14: "IMAGE_GET_RESINFO",
    32: "IMAGE_SAMPLE",
    33: "IMAGE_SAMPLE_CL",
    34: "IMAGE_SAMPLE_D",
    35: "IMAGE_SAMPLE_D_CL",
    36: "IMAGE_SAMPLE_L",
    37: "IMAGE_SAMPLE_B",
    38: "IMAGE_SAMPLE_B_CL",
    39: "IMAGE_SAMPLE_LZ",
    40: "IMAGE_SAMPLE_C",
    41: "IMAGE_SAMPLE_C_CL",
    42: "IMAGE_SAMPLE_C_D",
    43: "IMAGE_SAMPLE_C_D_CL",
    44: "IMAGE_SAMPLE_C_L",
    45: "IMAGE_SAMPLE_C_B",
    46: "IMAGE_SAMPLE_C_B_CL",
    47: "IMAGE_SAMPLE_C_LZ",
    48: "IMAGE_SAMPLE_O",
    49: "IMAGE_SAMPLE_CL_O",
    50: "IMAGE_SAMPLE_D_O",
    51: "IMAGE_SAMPLE_D_CL_O",
    52: "IMAGE_SAMPLE_L_O",
    53: "IMAGE_SAMPLE_B_O",
    54: "IMAGE_SAMPLE_B_CL_O",
    55: "IMAGE_SAMPLE_LZ_O",
    56: "IMAGE_SAMPLE_C_O",
    57: "IMAGE_SAMPLE_C_CL_O",
    58: "IMAGE_SAMPLE_C_D_O",
    59: "IMAGE_SAMPLE_C_D_CL_O",
    60: "IMAGE_SAMPLE_C_L_O",
    61: "IMAGE_SAMPLE_C_B_O",
    62: "IMAGE_SAMPLE_C_B_CL_O",
    63: "IMAGE_SAMPLE_C_LZ_O",
    104: "IMAGE_SAMPLE_CD",
    105: "IMAGE_SAMPLE_CD_CL",
    106: "IMAGE_SAMPLE_C_CD",
    107: "IMAGE_SAMPLE_C_CD_CL",
    108: "IMAGE_SAMPLE_CD_O",
    109: "IMAGE_SAMPLE_CD_CL_O",
    110: "IMAGE_SAMPLE_C_CD_O",
    111: "IMAGE_SAMPLE_C_CD_CL_O",
}


def parse_microcode(instr: int) -> int | None:
    """Mirror opengnm/src/gcn/microcode.h's instruction-class tests."""
    magic_tests = (
        ((instr & (0x1FF << 23)) == 0xBE800000, 0),
        ((instr & (0x1FF << 23)) == 0xBF000000, 2),
        ((instr & (0x1FF << 23)) == 0xBF800000, 4),
        ((instr & (0x7F << 25)) == 0x7E000000, 5),
        ((instr & (0x7F << 25)) == 0x7C000000, 8),
        ((instr & (0x3F << 26)) == 0xD0000000, 7),
        ((instr & (0x3F << 26)) == 0xC8000000, 9),
        ((instr & (0x3F << 26)) == 0xD8000000, 11),
        ((instr & (0x3F << 26)) == 0xE0000000, 12),
        ((instr & (0x3F << 26)) == 0xE8000000, 13),
        ((instr & (0x3F << 26)) == 0xF0000000, 14),
        ((instr & (0x3F << 26)) == 0xF8000000, 15),
        ((instr & (0x1F << 27)) == 0xC0000000, 10),
        ((instr & (0xF << 28)) == 0xB0000000, 3),
        ((instr & (0x3 << 30)) == 0x80000000, 1),
        ((instr & (1 << 31)) == 0, 6),
    )
    for matched, microcode in magic_tests:
        if matched:
            return microcode
    return None


def _strip_c_comments(text: str) -> str:
    return re.sub(r"//.*?$|/\*.*?\*/", "", text, flags=re.M | re.S)


def opengnm_enum_tables() -> dict[str, dict[str, int]]:
    """Read the local opengnm opcode enum values without inventing names."""
    text = _strip_c_comments((OPENGNM / "src" / "gcn" / "gcn_types.h").read_text(encoding="utf-8"))
    tables: dict[str, dict[str, int]] = {}
    pattern = re.compile(r"typedef\s+enum\s*\{(.*?)\}\s*(GcnOpcode[A-Za-z0-9_]+)\s*;", re.S)
    for match in pattern.finditer(text):
        value = -1
        table: dict[str, int] = {}
        for item in match.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            name_match = re.match(r"(GCN_[A-Z0-9_]+)(?:\s*=\s*([^\s]+))?$", item)
            if not name_match:
                continue
            name, expression = name_match.groups()
            if expression:
                try:
                    value = int(expression, 0)
                except ValueError:
                    continue
            else:
                value += 1
            table[name] = value
        tables[match.group(2)] = table
    return tables


ENUM_TABLES = opengnm_enum_tables()
ENUM_BY_MICROCODE = {
    0: "GcnOpcodeSOP1",
    1: "GcnOpcodeSOP2",
    2: "GcnOpcodeSOPC",
    3: "GcnOpcodeSOPK",
    4: "GcnOpcodeSOPP",
    5: "GcnOpcodeVOP1",
    6: "GcnOpcodeVOP2",
    7: "GcnOpcodeVOP3",
    8: "GcnOpcodeVOPC",
    9: "GcnOpcodeVINTRP",
    10: "GcnOpcodeSMRD",
    11: "GcnOpcodeDS",
    12: "GcnOpcodeMUBUF",
    13: "GcnOpcodeMTBUF",
    14: "GcnOpcodeMIMG",
}


def enum_value(table: str, name: str) -> int:
    return ENUM_TABLES[table][name]


def opcode_name(microcode: int, opcode: int) -> str:
    table = ENUM_TABLES.get(ENUM_BY_MICROCODE.get(microcode, ""), {})
    names = [name for name, value in table.items() if value == opcode]
    return names[0] if names else f"{MICROCODE_NAMES.get(microcode, 'UNKNOWN')}_OP_{opcode}"


def valid_opcode(microcode: int, opcode: int) -> bool:
    """Mirror every isvalid/parseopcode predicate in opengnm/microcode.h."""
    def v(table: str, name: str) -> int:
        return enum_value(table, name)

    if microcode == 0:  # parseopcode_sop1
        return v("GcnOpcodeSOP1", "GCN_S_MOV_B32") <= opcode <= v("GcnOpcodeSOP1", "GCN_S_MOV_FED_B32")
    if microcode == 1:  # parseopcode_sop2
        return not (
            v("GcnOpcodeSOP2", "GCN_S_CSELECT_B64") < opcode < v("GcnOpcodeSOP2", "GCN_S_AND_B32")
            or opcode > v("GcnOpcodeSOP2", "GCN_S_ABSDIFF_I32")
        )
    if microcode == 2:  # isvalidsopc
        return opcode <= v("GcnOpcodeSOPC", "GCN_S_SETVSKIP")
    if microcode == 3:  # isvalidsopk
        return opcode <= v("GcnOpcodeSOPK", "GCN_S_SETREG_IMM32_B32")
    if microcode == 4:  # isvalidsopp
        return opcode <= v("GcnOpcodeSOPP", "GCN_S_CBRANCH_CDBGSYS_AND_USER")
    if microcode == 5:  # parseopcode_vop1
        return opcode <= v("GcnOpcodeVOP1", "GCN_V_EXP_LEGACY_F32")
    if microcode == 6:  # isvalidvop2
        return opcode <= v("GcnOpcodeVOP2", "GCN_V_CVT_PK_I16_I32")
    if microcode == 7:  # parseopcode_vop3
        return opcode <= v("GcnOpcodeVOP3", "GCN_V_EXP_LEGACY_F32_E64")
    if microcode == 8:  # parseopcode_vopc
        return opcode <= v("GcnOpcodeVOPC", "GCN_V_CMPX_TRU_U64")
    if microcode == 9:  # parseopcode_vintrp
        return opcode <= v("GcnOpcodeVINTRP", "GCN_V_INTERP_MOV_F32")
    if microcode == 10:  # isvalidsmrd
        return not (
            opcode > v("GcnOpcodeSMRD", "GCN_S_DCACHE_INV")
            or v("GcnOpcodeSMRD", "GCN_S_LOAD_DWORDX16") < opcode < v("GcnOpcodeSMRD", "GCN_S_BUFFER_LOAD_DWORD")
            or v("GcnOpcodeSMRD", "GCN_S_BUFFER_LOAD_DWORDX16") < opcode < v("GcnOpcodeSMRD", "GCN_S_DCACHE_INV_VOL")
        )
    if microcode == 11:  # parseopcode_ds
        return not (
            v("GcnOpcodeDS", "GCN_DS_NOP") < opcode < v("GcnOpcodeDS", "GCN_DS_GWS_SEMA_RELEASE_ALL")
            or v("GcnOpcodeDS", "GCN_DS_MAX_F64") < opcode < v("GcnOpcodeDS", "GCN_DS_ADD_RTN_U64")
            or v("GcnOpcodeDS", "GCN_DS_MAX_RTN_F64") < opcode < v("GcnOpcodeDS", "GCN_DS_READ_B64")
            or v("GcnOpcodeDS", "GCN_DS_READ2ST64_B64") < opcode < v("GcnOpcodeDS", "GCN_DS_CONDXCHG32_RTN_B64")
            or v("GcnOpcodeDS", "GCN_DS_CONDXCHG32_RTN_B64") < opcode < v("GcnOpcodeDS", "GCN_DS_ADD_SRC2_U32")
            or v("GcnOpcodeDS", "GCN_DS_XOR_SRC2_B32") < opcode < v("GcnOpcodeDS", "GCN_DS_WRITE_SRC2_B32")
            or v("GcnOpcodeDS", "GCN_DS_WRITE_SRC2_B32") < opcode < v("GcnOpcodeDS", "GCN_DS_MIN_SRC2_F32")
            or v("GcnOpcodeDS", "GCN_DS_MAX_SRC2_F32") < opcode < v("GcnOpcodeDS", "GCN_DS_ADD_SRC2_U64")
            or v("GcnOpcodeDS", "GCN_DS_XOR_SRC2_B64") < opcode < v("GcnOpcodeDS", "GCN_DS_WRITE_SRC2_B64")
            or v("GcnOpcodeDS", "GCN_DS_WRITE_SRC2_B64") < opcode < v("GcnOpcodeDS", "GCN_DS_MIN_SRC2_F64")
            or v("GcnOpcodeDS", "GCN_DS_MAX_SRC2_F64") < opcode < v("GcnOpcodeDS", "GCN_DS_WRITE_B96")
            or v("GcnOpcodeDS", "GCN_DS_WRITE_B128") < opcode < v("GCN_DS_CONDXCHG32_RTN_B128")
        )
    if microcode == 12:  # isvalidmubuf
        return not (
            opcode > v("GcnOpcodeMUBUF", "GCN_BUFFER_LOAD_DWORDX3") and opcode < v("GcnOpcodeMUBUF", "GCN_BUFFER_STORE_BYTE")
            or opcode > v("GcnOpcodeMUBUF", "GCN_BUFFER_STORE_BYTE") and opcode < v("GcnOpcodeMUBUF", "GCN_BUFFER_STORE_SHORT")
            or opcode > v("GcnOpcodeMUBUF", "GCN_BUFFER_STORE_DWORDX3") and opcode < v("GcnOpcodeMUBUF", "GCN_BUFFER_ATOMIC_SWAP")
            or opcode > v("GcnOpcodeMUBUF", "GCN_BUFFER_ATOMIC_FMAX") and opcode < v("GcnOpcodeMUBUF", "GCN_BUFFER_ATOMIC_SWAP_X2")
            or opcode > v("GcnOpcodeMUBUF", "GCN_BUFFER_ATOMIC_FMAX_X2") and opcode < v("GcnOpcodeMUBUF", "GCN_BUFFER_WBINVL1_SC")
            or opcode > v("GcnOpcodeMUBUF", "GCN_BUFFER_WBINVL1")
        )
    if microcode == 13:  # isvalidmtbuf
        return opcode <= v("GcnOpcodeMTBUF", "GCN_TBUFFER_STORE_FORMAT_XYZW")
    if microcode == 14:  # parseopcode_mimg
        return not (
            v("GcnOpcodeMIMG", "GCN_IMAGE_LOAD_MIP_PCK_SGN") < opcode < v("GcnOpcodeMIMG", "GCN_IMAGE_STORE")
            or v("GcnOpcodeMIMG", "GCN_IMAGE_STORE_MIP_PCK") < opcode < v("GcnOpcodeMIMG", "GCN_IMAGE_GET_RESINFO")
            or v("GcnOpcodeMIMG", "GCN_IMAGE_ATOMIC_SUB") < opcode < v("GcnOpcodeMIMG", "GCN_IMAGE_ATOMIC_SMIN")
            or v("GcnOpcodeMIMG", "GCN_IMAGE_GATHER4_CL") < opcode < v("GcnOpcodeMIMG", "GCN_IMAGE_GATHER4_L")
            or v("GcnOpcodeMIMG", "GCN_IMAGE_GATHER4_C_CL") < opcode < v("GcnOpcodeMIMG", "GCN_IMAGE_GATHER4_C_L")
            or v("GcnOpcodeMIMG", "GCN_IMAGE_GATHER4_CL_O") < opcode < v("GcnOpcodeMIMG", "GCN_IMAGE_GATHER4_L_O")
            or v("GcnOpcodeMIMG", "GCN_IMAGE_GATHER4_C_CL_O") < opcode < v("GcnOpcodeMIMG", "GCN_IMAGE_GATHER4_C_L_O")
            or v("GcnOpcodeMIMG", "GCN_IMAGE_GET_LOD") < opcode < v("GcnOpcodeMIMG", "GCN_IMAGE_SAMPLE_CD")
            or opcode > v("GcnOpcodeMIMG", "GCN_IMAGE_SAMPLE_C_CD_CL_O")
        )
    return True


def operand_valid(field: int, encoded_max: int = 511) -> bool:
    """Mirror opengnm parseoperandfield for encoded SGPR/special/literal fields."""
    if field > encoded_max:
        return False
    if field in (104, 105, 125):
        return False
    if 209 <= field <= 239:
        return False
    if 248 <= field <= 250:
        return False
    return True


def export_target_valid(target: int) -> bool:
    return target <= 63 and target not in (10, 11) and not 16 <= target <= 31


def instruction_fields(microcode: int, instr32: int, instr64: int, opcode: int) -> dict[str, list[int] | int]:
    """Return encoded operand fields used by the matching opengnm decoder."""
    if microcode == 0:
        return {"src": [instr32 & 0xFF], "dst": [(instr32 >> 16) & 0x7F]}
    if microcode == 1:
        return {"src": [instr32 & 0xFF, (instr32 >> 8) & 0xFF], "dst": [(instr32 >> 16) & 0x7F]}
    if microcode == 5:
        return {"src": [instr32 & 0x1FF], "dst": [(instr32 >> 17) & 0xFF]}
    if microcode == 6:
        return {"src": [instr32 & 0x1FF, (instr32 >> 9) & 0xFF], "dst": [(instr32 >> 17) & 0xFF]}
    if microcode == 7:
        return {"src": [(instr64 >> 32) & 0x1FF, (instr64 >> 41) & 0x1FF, (instr64 >> 50) & 0x1FF], "dst": [instr64 & 0xFF]}
    if microcode == 8:
        return {"src": [instr32 & 0x1FF, (instr32 >> 9) & 0xFF], "dst": [106]}
    if microcode == 9:
        return {"src": [instr32 & 0xFF], "dst": [(instr32 >> 18) & 0xFF]}
    if microcode == 10:
        return {"src": [(instr32 >> 9) & 0x3F, instr32 & 0xFF], "dst": [(instr32 >> 15) & 0x7F], "imm": [(instr32 >> 8) & 1]}
    if microcode == 11:
        return {"src": [instr64 & 0xFF, (instr64 >> 40) & 0xFF, (instr64 >> 48) & 0xFF], "dst": [(instr64 >> 56) & 0xFF]}
    if microcode == 12:  # MUBUF: vaddr, vdata, srsrc, soffset
        return {
            "src": [(instr64 >> 32) & 0xFF, ((instr64 >> 48) & 0x1F) << 2, (instr64 >> 56) & 0xFF],
            "dst": [(instr64 >> 40) & 0xFF],
        }
    if microcode == 13:  # MTBUF: vaddr, vdata, srsrc, soffset
        return {
            "src": [(instr64 >> 32) & 0xFF, ((instr64 >> 48) & 0x1F) << 2, (instr64 >> 56) & 0xFF],
            "dst": [(instr64 >> 40) & 0xFF],
        }
    if microcode == 14:
        return {"src": [(instr64 >> 32) & 0xFF, ((instr64 >> 48) & 0x1F) << 2, ((instr64 >> 53) & 0x1F) << 2]}
    if microcode == 15:
        return {"src": [(instr64 >> 32) & 0xFF, (instr64 >> 40) & 0xFF, (instr64 >> 48) & 0xFF, (instr64 >> 56) & 0xFF], "target": (instr64 >> 4) & 0x3F}
    return {}


def validate_instruction_fields(microcode: int, instr32: int, instr64: int, opcode: int) -> list[str]:
    fields = instruction_fields(microcode, instr32, instr64, opcode)
    errors: list[str] = []
    src = list(fields.get("src", []))
    dst = list(fields.get("dst", []))
    def vgpr_field(raw: int) -> int:
        # Match decoder.c's parse_vgpr_field helper: low encodings are VGPR
        # aliases, while 104+ are passed through parseoperandfield.
        return raw + 256 if raw <= 103 else raw
    if microcode == 10:
        src[0] *= 2
        if fields.get("imm", [0])[0]:
            src = src[:1]
    if microcode == 6:
        if opcode == enum_value("GcnOpcodeVOP2", "GCN_V_WRITELANE_B32"):
            src = [src[1] + 256] if len(src) > 1 else []
        else:
            src = src[:1]
        if opcode == enum_value("GcnOpcodeVOP2", "GCN_V_READLANE_B32"):
            dst = []
        else:
            dst = []  # VOP2 destinations are direct VGPR encodings.
    if microcode == 7:
        if opcode not in (enum_value("GcnOpcodeVOP3", "GCN_V_MOVRELS_B32_E64"), enum_value("GcnOpcodeVOP3", "GCN_V_MOVRELSD_B32_E64")):
            pass
        else:
            src = src[1:]
        if opcode in (enum_value("GcnOpcodeVOP3", "GCN_V_ADDC_U32_E64"), enum_value("GcnOpcodeVOP3", "GCN_V_CNDMASK_B32_E64"), enum_value("GcnOpcodeVOP3", "GCN_V_SUBB_U32_E64"), enum_value("GcnOpcodeVOP3", "GCN_V_SUBBREV_U32_E64")):
            src = src[:2]
        if opcode <= enum_value("GcnOpcodeVOP3", "GCN_V_CMPX_TRU_U64_E64"):
            pass
        else:
            dst = []  # non-compare VOP3 destinations are direct VGPRs.
    if microcode == 5:
        dst = []  # VOP1 destinations are direct VGPR encodings.
    if microcode == 8:
        src = src[:1]  # VOPC source 1 is a direct VGPR encoding.
    if microcode == 9:
        src = []  # VINTRP uses direct VGPR encodings and performs no parse.
        dst = []
    if microcode == 11:
        src = [vgpr_field(x) for x in src]
        dst = [vgpr_field(x) for x in dst]
    if microcode in (12, 13, 14):
        # MUBUF/MTBUF/MIMG assign their encoded VGPR/SGPR fields directly in
        # decoder.c; there is no parseoperandfield call for these operands.
        src = []
        dst = []
    if microcode == 15:
        target = int(fields.get("target", -1))
        if not export_target_valid(target):
            errors.append(f"invalid export target {target}")
        src = [vgpr_field(x) for x in (src[:2] if ((instr64 >> 10) & 1) else src[:4])]
    src_max = 511 if microcode in (5, 6, 7, 8) else 255
    for index, field in enumerate(src):
        if not operand_valid(field, src_max):
            errors.append(f"invalid operand field {field}")
    for field in dst:
        if not operand_valid(field, 255):
            errors.append(f"invalid operand field {field}")
    return errors


def encoded_opcode(microcode: int, instr32: int, instr64: int) -> int | None:
    if microcode == 0:
        return (instr32 >> 8) & 0xFF
    if microcode == 1:
        return (instr32 >> 23) & 0x7F
    if microcode == 2:
        return (instr32 >> 16) & 0x7F
    if microcode == 3:
        return (instr32 >> 23) & 0x1F
    if microcode == 4:
        return (instr32 >> 16) & 0x7F
    if microcode == 5:
        return (instr32 >> 9) & 0xFF
    if microcode == 6:
        return (instr32 >> 25) & 0x3F
    if microcode == 7:
        return (instr64 >> 17) & 0x1FF
    if microcode == 8:
        return (instr32 >> 17) & 0xFF
    if microcode == 9:
        return (instr32 >> 16) & 0x3
    if microcode == 10:
        return (instr32 >> 22) & 0x1F
    if microcode == 11:
        return (instr64 >> 18) & 0xFF
    if microcode == 12:
        return (instr64 >> 18) & 0x7F
    if microcode == 13:
        return (instr64 >> 16) & 0x7
    if microcode == 14:
        return (instr64 >> 18) & 0x7F
    return None


def literal_count(microcode: int, instr32: int, instr64: int, opcode: int | None = None) -> int:
    """Return literal words consumed by the exact local opengnm decoder.

    In particular, VOP2 src1 is always a VGPR selector, VOP2 MADAK/MADMK
    synthesize one literal source, and the checked-in VOP3 decoder does not
    consume literal words even when an encoded source field is 255.
    """
    opcode = opcode if opcode is not None else 0
    if microcode == 0:  # SOP1
        return int((instr32 & 0xFF) == 255)
    if microcode == 1:  # SOP2
        return int((instr32 & 0xFF) == 255) + int(((instr32 >> 8) & 0xFF) == 255)
    if microcode == 5:  # VOP1
        return int((instr32 & 0x1FF) == 255)
    if microcode == 6:  # VOP2
        count = 0 if opcode == enum_value("GcnOpcodeVOP2", "GCN_V_WRITELANE_B32") else int((instr32 & 0x1FF) == 255)
        count += int(opcode == enum_value("GcnOpcodeVOP2", "GCN_V_MADAK_F32"))
        count += int(opcode == enum_value("GcnOpcodeVOP2", "GCN_V_MADMK_F32"))
        return count
    # opengnm's decodesopc and decodevop3 never advance for literal fields.
    return 0


def scan_gcn_code(code: bytes, file_offset: int) -> dict:
    """Collect instruction-shape evidence without pretending to decompile it."""
    pos = 0
    counts: dict[str, int] = {}
    image_ops: dict[str, int] = {}
    image_instructions: list[dict] = []
    exports: list[dict] = []
    literal_words: list[dict] = []
    invalid_offsets: list[int] = []
    invalid_opcode_offsets: list[int] = []
    invalid_operand_offsets: list[int] = []
    invalid_export_target_offsets: list[int] = []
    invalid_details: list[dict] = []
    opcode_counts: dict[str, int] = {}
    length_counts: dict[str, int] = {}
    instruction_count = 0
    while pos + 4 <= len(code):
        instr32 = u32(code, pos)
        microcode = parse_microcode(instr32)
        if microcode is None:
            invalid_offsets.append(file_offset + pos)
            pos += 4
            continue
        instr64 = struct.unpack_from("<Q", code, pos)[0] if pos + 8 <= len(code) else instr32
        long_form = microcode in (7, 11, 12, 13, 14, 15)
        base_length = 8 if long_form else 4
        if pos + base_length > len(code):
            break
        class_name = MICROCODE_NAMES[microcode]
        counts[class_name] = counts.get(class_name, 0) + 1
        instruction_count += 1
        opcode = encoded_opcode(microcode, instr32, instr64)
        op_name = opcode_name(microcode, opcode) if opcode is not None else class_name
        opcode_counts[op_name] = opcode_counts.get(op_name, 0) + 1
        if opcode is not None:
            if not valid_opcode(microcode, opcode):
                invalid_opcode_offsets.append(file_offset + pos)
            field_errors = validate_instruction_fields(microcode, instr32, instr64, opcode)
            for error in field_errors:
                invalid_details.append({
                    "file_offset": file_offset + pos,
                    "microcode": class_name,
                    "opcode": op_name,
                    "error": error,
                })
                if error.startswith("invalid export target"):
                    invalid_export_target_offsets.append(file_offset + pos)
                else:
                    invalid_operand_offsets.append(file_offset + pos)
        if microcode == 14:
            op = opcode
            name = MIMG_OP_NAMES.get(op, op_name)
            image_ops[name] = image_ops.get(name, 0) + 1
            image_instructions.append({
                "file_offset": file_offset + pos,
                "opcode": name,
                "opcode_value": op,
                "dmask": (instr64 >> 8) & 0xF,
                "unnormalized": (instr64 >> 12) & 1,
                "global_coherency": (instr64 >> 13) & 1,
                "declare_array": (instr64 >> 14) & 1,
                "resource_128": (instr64 >> 15) & 1,
                "texture_fail": (instr64 >> 16) & 1,
                "lod_warning": (instr64 >> 17) & 1,
                "syslevel_coherent": (instr64 >> 25) & 1,
                "vaddr": (instr64 >> 32) & 0xFF,
                "vdata": (instr64 >> 40) & 0xFF,
                "resource_sgpr": ((instr64 >> 48) & 0x1F) << 2,
                "sampler_sgpr": ((instr64 >> 53) & 0x1F) << 2,
            })
        elif microcode == 15:
            target = (instr64 >> 4) & 0x3F
            target_name = (
                f"MRT_{target}" if target <= 7 else
                "Z" if target == 8 else
                "NULL" if target == 9 else
                f"POS_{target - 12}" if 12 <= target <= 15 else
                f"PARAM_{target - 32}" if 32 <= target <= 63 else
                f"TARGET_{target}"
            )
            exports.append({
                "file_offset": file_offset + pos,
                "target": target_name,
                "enable_mask": instr64 & 0xF,
                "compressed": (instr64 >> 10) & 1,
                "done": (instr64 >> 11) & 1,
                "valid_mask": (instr64 >> 12) & 1,
                "sources": [
                    (instr64 >> 32) & 0xFF,
                    (instr64 >> 40) & 0xFF,
                    (instr64 >> 48) & 0xFF,
                    (instr64 >> 56) & 0xFF,
                ],
            })
        extra_literal_words = literal_count(microcode, instr32, instr64, opcode)
        length = base_length + extra_literal_words * 4
        length_counts[str(length)] = length_counts.get(str(length), 0) + 1
        if extra_literal_words:
            for index in range(extra_literal_words):
                literal_pos = pos + base_length + index * 4
                if literal_pos + 4 <= len(code):
                    literal_words.append({
                        "file_offset": file_offset + literal_pos,
                        "bits": f"{u32(code, literal_pos):08X}",
                        "float": struct.unpack_from("<f", code, literal_pos)[0],
                    })
        pos += base_length + extra_literal_words * 4
    return {
        "code_file_offset": file_offset,
        "code_size_bytes": len(code),
        "instruction_count": instruction_count,
            "microcode_counts": counts,
        "opcode_counts": opcode_counts,
        "instruction_length_counts": length_counts,
        "image_ops": image_ops,
        "image_instructions": image_instructions,
        "exports": exports,
        "literal_words": literal_words,
        "invalid_instruction_offsets": invalid_offsets,
        "invalid_opcode_offsets": invalid_opcode_offsets,
        "invalid_operand_offsets": invalid_operand_offsets,
        "invalid_export_target_offsets": invalid_export_target_offsets,
        "invalid_details": invalid_details[:256],
        "scan_complete": not invalid_offsets and pos == len(code),
        "opengnm_shape_validation_passed": not (
            invalid_offsets or invalid_opcode_offsets or invalid_operand_offsets or invalid_export_target_offsets
        ) and pos == len(code),
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def positions(blob: bytes, needle: bytes) -> list[int]:
    found = []
    start = 0
    while True:
        index = blob.find(needle, start)
        if index < 0:
            return found
        found.append(index)
        start = index + 1


def float_hits(blob: bytes, value: float) -> list[int]:
    needle = struct.pack("<f", value)
    return positions(blob, needle)


def printable_strings(blob: bytes) -> list[str]:
    return sorted(set(x.decode("ascii", "replace") for x in re.findall(rb"[ -~]{4,}", blob)))


def u16(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<H", blob, offset)[0]


def u32(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def aligned4(value: int) -> int:
    return (value + 3) & ~3


def usage_slot(blob: bytes, offset: int) -> dict:
    raw = list(blob[offset:offset + 4])
    flags = raw[3]
    return {
        "offset": offset,
        "raw": raw,
        "usage_type": USAGE_TYPES.get(raw[0], f"0x{raw[0]:02X}"),
        "api_slot": raw[1],
        "start_register": raw[2],
        "register_count": flags & 1,
        "resource_type": (flags >> 1) & 1,
        "chunk_mask": (flags >> 4) & 0xF,
        "srt_dwords_minus_one": flags,
    }


def orb_info(blob: bytes, offset: int) -> dict:
    if offset < 0 or blob[offset:offset + 7] != b"OrbShdr":
        return {}
    flags_length = u32(blob, offset + 8)
    usage_flags = blob[offset + 14]
    return {
        "offset": offset,
        "version": blob[offset + 7],
        "is_pssl_cg": flags_length & 1,
        "is_source_cached": (flags_length >> 1) & 1,
        "binary_type": SHADER_BINARY_TYPES.get((flags_length >> 2) & 0xF, f"0x{(flags_length >> 2) & 0xF:X}"),
        "source_type": (flags_length >> 6) & 0x3,
        "length": (flags_length >> 8) & 0xFFFFFF,
        "chunk_usage_base_offset_dwords": blob[offset + 12],
        "num_input_usage_slots": blob[offset + 13],
        "has_srt": usage_flags & 1,
        "has_srt_used_valid_info": (usage_flags >> 1) & 1,
        "has_extended_usage_info": (usage_flags >> 2) & 1,
        "shader_hash0": f"{u32(blob, offset + 16):08X}",
        "shader_hash1": f"{u32(blob, offset + 20):08X}",
        "crc32": f"{u32(blob, offset + 24):08X}",
    }


def gnm_headers(blob: bytes, section_file_offset: int) -> dict:
    all_shdr = positions(blob, b"Shdr")
    # OrbShdr ends in the bytes "Shdr". Only the other occurrences begin a
    # GnmShaderFileHeader.
    shdr = [o for o in all_shdr if o < 3 or blob[o - 3:o] != b"Orb"]
    orb = positions(blob, b"OrbShdr")
    records = []
    for index, offset in enumerate(shdr):
        version_major = u16(blob, offset + 4)
        version_minor = u16(blob, offset + 6)
        shader_type = blob[offset + 8]
        header_words = blob[offset + 9]
        common = u32(blob, offset + 16)
        shader_size = common & 0x7FFFFF
        stage_base = offset + 16
        if shader_type == 0x01:
            input_count = blob[stage_base + 0x24]
            output_count = blob[stage_base + 0x25]
            stage_fixed_size = 0x28
            usage_base = stage_base + stage_fixed_size
            input_base = usage_base + 4 * (common >> 24)
            output_base = input_base + 4 * input_count
            semantics = {
                "inputs": [
                    {
                        "semantic": blob[output_base - 4 * input_count + 4 * i],
                        "vgpr": blob[output_base - 4 * input_count + 4 * i + 1],
                        "size_in_elements": blob[output_base - 4 * input_count + 4 * i + 2],
                    }
                    for i in range(input_count)
                ],
                "outputs_raw": [
                    list(blob[output_base + 2 * i:output_base + 2 * i + 2])
                    for i in range(output_count)
                ],
            }
            stage_size = aligned4(stage_fixed_size + 4 * (common >> 24) + 4 * input_count + 2 * output_count)
        elif shader_type == 0x02:
            input_count = blob[stage_base + 0x38]
            output_count = 0
            stage_fixed_size = 0x3C
            usage_base = stage_base + stage_fixed_size
            input_base = usage_base + 4 * (common >> 24)
            semantics = {
                "inputs_raw": [
                    list(blob[input_base + 2 * i:input_base + 2 * i + 2])
                    for i in range(input_count)
                ],
            }
            stage_size = aligned4(stage_fixed_size + 4 * (common >> 24) + 2 * input_count)
        else:
            input_count = 0
            output_count = 0
            stage_fixed_size = 0
            usage_base = stage_base
            semantics = {}
            stage_size = 0
        usage_count = common >> 24
        code_pointer_offset = u32(blob, stage_base + 8) if shader_type in (0x01, 0x02) else None
        # opengnm's sceGnm{Vs,Ps}ShaderCodePtr adds the register value to the
        # stage-struct pointer itself (Shdr + 0x10), not to the Shdr header.
        # Therefore the encoded pointer is the stage struct's calculated size.
        expected_code_pointer_offset = stage_size if shader_type in (0x01, 0x02) else None
        next_orb = next((x for x in orb if x > offset and (index + 1 == len(shdr) or x < shdr[index + 1])), -1)
        # The machine code follows the stage header.  The local opengnm helper
        # treats OrbShdr.length as the shader-code length, so use that field
        # as the instruction boundary.  The materialgraph record also carries
        # auxiliary bytes before the OrbShdr block; those are deliberately not
        # decoded as instructions.
        code_start = offset + 16 + stage_size
        declared_shader_end = code_start + shader_size
        orb_footer_offset = declared_shader_end - 0x1C
        has_expected_orb_footer = (
            orb_footer_offset >= 0 and
            blob[orb_footer_offset:orb_footer_offset + 7] == b"OrbShdr"
        )
        next_orb_info = orb_info(blob, next_orb)
        orb_code_size = next_orb_info.get("length")
        if isinstance(orb_code_size, int) and code_start + orb_code_size <= len(blob):
            code_end = code_start + orb_code_size
        else:
            code_end = orb_footer_offset if has_expected_orb_footer else declared_shader_end
        auxiliary_bytes_before_orb = max(0, next_orb - code_end) if next_orb >= 0 else 0
        orb_boundary_consistent = bool(
            next_orb >= 0 and isinstance(orb_code_size, int) and
            next_orb == code_end + auxiliary_bytes_before_orb and
            has_expected_orb_footer and
            next_orb + 0x1C == declared_shader_end
        )
        code_scan = scan_gcn_code(
            blob[code_start:min(code_end, len(blob))],
            section_file_offset + code_start,
        )
        records.append({
            "file_offset": section_file_offset + offset,
            "relative_offset": offset,
            "version": f"{version_major}.{version_minor}",
            "shader_type": SHADER_TYPES.get(shader_type, f"0x{shader_type:02X}"),
            "header_size_words": header_words,
            "header_size_bytes": header_words * 4,
            "common": {
                "shader_size": shader_size,
                "is_using_srt": (common >> 23) & 1,
                "num_input_usage_slots": usage_count,
                "embedded_constant_buffer_dqwords": u16(blob, offset + 20),
                "scratch_size_per_thread_dwords": u16(blob, offset + 22),
            },
            "stage_size_bytes": stage_size,
            "metadata_header_size_matches": header_words * 4 == stage_size,
            "code_offset_from_header_bytes": 16 + stage_size,
            "code_file_offset": section_file_offset + code_start,
            "code_size_bytes": max(0, code_end - code_start),
            "declared_shader_size_bytes": shader_size,
            "common_code_size_with_embedded_constants": shader_size + u16(blob, offset + 20) * 16,
            "orb_declared_code_size_bytes": orb_code_size,
            "auxiliary_bytes_before_orb": auxiliary_bytes_before_orb,
            "declared_shader_end_file_offset": section_file_offset + declared_shader_end,
            "orb_footer_file_offset": section_file_offset + next_orb if next_orb >= 0 else None,
            "code_end_file_offset": section_file_offset + code_end,
            "stage_code_pointer_offset_bytes": code_pointer_offset,
            "stage_code_pointer_matches_layout": code_pointer_offset == expected_code_pointer_offset,
            "orb_boundary_consistent": orb_boundary_consistent,
            "orb_footer_at_declared_end_minus_0x1c": has_expected_orb_footer,
            "instruction_scan": code_scan,
            "usage_slots": [usage_slot(blob, usage_base + 4 * i) for i in range(usage_count)],
            "semantics": semantics,
            "orbshdr": orb_info(blob, next_orb),
        })
    return {
        "real_shdr_relative_offsets": shdr,
        "real_shdr_file_offsets": [section_file_offset + x for x in shdr],
        "orbshdr_relative_offsets": orb,
        "orbshdr_file_offsets": [section_file_offset + x for x in orb],
        "overlapping_shdr_suffix_offsets": [x + 3 for x in orb],
        "records": records,
    }


def audit_graph(path: Path) -> dict:
    raw = path.read_bytes()
    dat = DAT1(io.BytesIO(raw), version=dat1lib.VERSION_SO, ignore_sections_exceptions=True)
    section_header = next(x for x in dat.header.sections if x.tag == 0x873D1E6C)
    section = dat.get_section(0x873D1E6C)
    blob = bytes(section._raw)
    gnm = gnm_headers(blob, section_header.offset)
    return {
        "path": str(path),
        "sha256": sha(path),
        "compiled_section": {
            "tag": "0x873D1E6C",
            "file_offset": section_header.offset,
            "size": section_header.size,
            "end_file_offset": section_header.offset + section_header.size,
            "real_shdr_relative_offsets": gnm["real_shdr_relative_offsets"],
            "real_shdr_file_offsets": gnm["real_shdr_file_offsets"],
            "orbshdr_relative_offsets": gnm["orbshdr_relative_offsets"],
            "orbshdr_file_offsets": gnm["orbshdr_file_offsets"],
            "overlapping_shdr_suffix_offsets": gnm["overlapping_shdr_suffix_offsets"],
            "gnm_shader_records": gnm["records"],
        },
        "engine_symbol_offsets": {
            symbol: [section_header.offset + x for x in positions(blob, symbol.encode("ascii"))]
            for symbol in SYMBOLS
            if positions(blob, symbol.encode("ascii"))
        },
        "exact_float_hits_in_compiled_section": {
            name: [section_header.offset + x for x in float_hits(blob, value)]
            for name, value in VALUES.items()
            if float_hits(blob, value)
        },
        "readable_strings": printable_strings(blob),
        "instruction_semantics": (
            "Conservative GCN instruction-shape scan only: instruction classes, "
            "local-opengnm opcode/operand validity, MIMG image operation fields, "
            "EXP export fields, and instruction-form-specific literal words. "
            "No graph data-flow or material-parameter semantics are claimed."
        ),
    }


def main() -> None:
    report = {
        "scope": "Sheepinator compiled materialgraph sections plus conservative GCN instruction-shape scan",
        "values_tested": VALUES,
        "opengnm_reference": {
            "root": str(OPENGNM),
            "sources": [{"path": str(path), "sha256": sha(path)} for path in OPENGNM_SOURCES],
            "contracts": [
                "microcode.h parsemicrocode/getmicrocodelen and opcode/operand validity predicates",
                "decoder.c instruction-specific literal consumption and EXP/MIMG field interpretation",
                "gnm_shaderbinary.h stage sizes, code pointers, common shader size and OrbShdr length",
            ],
        },
        "graphs": [audit_graph(path) for path in GRAPHS.values() if path.exists()],
    }
    out = HERE / "shader_blob_audit.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("WROTE", out)
    for graph in report["graphs"]:
        print(graph["path"])
        print("  section", graph["compiled_section"])
        print("  symbols", graph["engine_symbol_offsets"])
        print("  exact literals", graph["exact_float_hits_in_compiled_section"])


if __name__ == "__main__":
    main()
