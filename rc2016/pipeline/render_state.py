"""Blend and cull state of a .material, from its flags word.

Every .material has a 36-byte section 0xE1275683; the u32 at byte 16 is its flags word:
bits 0-1 select the blend (0 opaque, 1 additive alpha, 2 alpha over, 3 not observed) and
bit 2 makes it two-sided. The rule was validated against draws in a RenderDoc capture of the
game (docs/shading.md, "Render state").

material_flags() reads the word from decoded material bytes, or by archive path from
material_flags.json.gz, a census of every .material in the game's archive.
"""
import gzip
import json
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS = os.path.join(HERE, "material_flags.json.gz")

MATERIAL_FLAGS_SECTION = 0xE1275683       # 36 bytes in every .material; flags = u32 at byte 16
FLAG_SURFACE = {0: "opaque", 1: "additive_alpha", 2: "alpha_over", 3: None}
_census = None


def decode_flags(flags):
    """Blend/cull from a material's flags word (bits 0-1 blend, bit 2 two-sided).

    Mode 3 has no captured example and returns surface None. Engine special passes override
    the flag: decals (mo_decal_*) and fur shells (bit 0x80000) were captured with blends their
    flag does not predict.
    """
    mode = flags & 3
    surface = FLAG_SURFACE[mode]
    notes = []
    if surface is None:
        notes.append("blend mode 3 not observed in the capture")
    if flags & 0x80000:
        notes.append("fur-shell material: engine draws extra alpha-over passes")
    return {"surface": surface, "surface_family": (surface or "unknown").split("_")[0],
            "backface": "show" if flags & 4 else "cull", "mode": mode, "notes": notes}


def material_flags(material_path=None, data=None):
    """Flags word of a .material, from decoded bytes or from the census by archive path."""
    global _census
    if data is not None:
        n = struct.unpack_from("<H", data, 12)[0]
        for k in range(n):
            tag, o, s = struct.unpack_from("<III", data, 16 + k * 12)
            if tag == MATERIAL_FLAGS_SECTION:
                return struct.unpack_from("<I", data, o + 16)[0]
        return None
    if _census is None:
        with gzip.open(CENSUS, "rt", encoding="utf-8") as fh:
            _census = {path.lower(): flags for path, flags in json.load(fh)["flags"].items()}
    flags = _census.get(material_path.replace("\\", "/").lower())
    return int(flags, 16) if flags else None
