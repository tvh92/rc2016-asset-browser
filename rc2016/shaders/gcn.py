"""GCN (PS4 GPU) instruction decoding for compiled materialgraph shaders.

Instruction classes, opcodes, operand fields and literal words follow the opengnm
decoder (third_party/opengnm); the opcode enums are read from its gcn_types.h, so
no names are invented. This decodes encodings only; it does not emulate shaders.
rc2016.shaders.gcn_fields adds the VOP3/EXP/branch fields and the stream decoder.
"""
from __future__ import annotations

import re
from pathlib import Path

GCN_TYPES = Path(__file__).resolve().parents[2] / "third_party" / "opengnm" / "gcn_types.h"
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
    text = _strip_c_comments(GCN_TYPES.read_text(encoding="utf-8"))
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


