"""Inspect Insomniac 1TAD animation containers without modifying source assets.

Usage:
    python Animation/animation_audit.py path/to/file.animset [--json report.json]

The tool intentionally stops at the section-table level.  It records every
embedded 1TAD container, its hash-addressed sections, and printable strings so
format work can be validated before interpreting compressed keyframes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any


MAGIC = b"1TAD"
ENTRY_SIZE = 12
HEADER_SIZE = 16
PRINTABLE = re.compile(rb"[ -~]{4,}")

# Root names are established by the public ig-tools 1TAD schema.  The nested
# animation-section names are corroborated by independent public Insomniac
# format research; their contents are still treated conservatively below.
SECTION_TYPES = {
    0x2BB5BC8F: "anim_clip_base_state",
    0x3976E44C: "anim_clip_path",
    0x3A7B4855: "anim_clip_sample_data",
    0x9FD19C20: "anim_clip_data",
    0x9DF23F77: "anim_clip_built",
    0xA3B26640: "anim_clip_joint_hashes",
    0xB79CF1D7: "anim_clip_lookup",
    0xD070D358: "anim_clip_sample_elements",
}


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def game_crc32(value: str) -> int:
    """Insomniac's reflected CRC-32: init 0xEDB88320, no final xor."""
    crc = 0xEDB88320
    for byte in value.encode("ascii"):
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc & 0xFFFFFFFF


def section_table(data: bytes, start: int = 0) -> list[dict[str, Any]]:
    """The container's sections ({label_hash, offset, size}) with read_container's bounds
    checks, without hashing sections or scanning strings (callers that only need offsets)."""
    if data[start : start + 4] != MAGIC:
        raise ValueError(f"0x{start:X} does not begin with 1TAD")
    if start + HEADER_SIZE > len(data):
        raise ValueError(f"truncated header at 0x{start:X}")
    declared_size = u32(data, start + 8)
    section_count = struct.unpack_from("<H", data, start + 12)[0]
    container_end = start + declared_size
    if declared_size < HEADER_SIZE or container_end > len(data):
        raise ValueError(f"invalid container size at 0x{start:X}")
    table_end = start + HEADER_SIZE + section_count * ENTRY_SIZE
    if table_end > container_end:
        raise ValueError(f"truncated section table at 0x{start:X}")
    sections = []
    for index in range(section_count):
        label_hash, relative_offset, size = struct.unpack_from("<III", data, start + HEADER_SIZE + index * ENTRY_SIZE)
        offset = start + relative_offset
        if offset < table_end or offset + size > container_end:
            raise ValueError(f"section {index} outside container payload at 0x{start:X}")
        sections.append({"index": index, "label_hash": f"0x{label_hash:08X}", "offset": offset, "size": size})
    return sections


def read_container(data: bytes, start: int, known_bones: dict[int, str]) -> dict[str, Any]:
    if data[start : start + 4] != MAGIC:
        raise ValueError(f"0x{start:X} does not begin with 1TAD")
    if start + HEADER_SIZE > len(data):
        raise ValueError(f"truncated header at 0x{start:X}")

    declared_size = u32(data, start + 8)
    # ig-tools igdat1.ksy: two separate u16 fields, not a u32 count.
    section_count, header_flags = struct.unpack_from("<HH", data, start + 12)
    container_end = start + declared_size
    if declared_size < HEADER_SIZE or container_end > len(data):
        raise ValueError(f"invalid container size at 0x{start:X}")
    table_end = start + HEADER_SIZE + section_count * ENTRY_SIZE
    if table_end > container_end:
        raise ValueError(f"truncated section table at 0x{start:X}")

    sections = []
    for index in range(section_count):
        entry = start + HEADER_SIZE + index * ENTRY_SIZE
        label_hash, relative_offset, size = struct.unpack_from("<III", data, entry)
        # 1TAD section offsets are relative to their enclosing container.
        offset = start + relative_offset
        end = offset + size
        if offset < table_end or end > container_end:
            raise ValueError(f"section {index} outside container payload at 0x{start:X}")
        sections.append(
            {
                "index": index,
                "label_hash": f"0x{label_hash:08X}",
                "type_name": SECTION_TYPES.get(label_hash),
                "relative_offset": relative_offset,
                "offset": offset,
                "size": size,
                "in_bounds": end <= len(data),
                "overlaps_header": offset < table_end,
                "sha256_16": hashlib.sha256(data[offset:end]).hexdigest()[:16]
                if end <= len(data)
                else None,
            }
        )

    container_end = start + declared_size
    local_strings = [
        match.group().decode("ascii")
        for match in PRINTABLE.finditer(data[start:container_end])
    ]
    name = None
    for index, value in enumerate(local_strings[:-1]):
        if value == "animclip built":
            name = local_strings[index + 1]
            break

    metadata = None
    # This section is identical in role across the verified Sheepinator and
    # Proton Drum clips.  Its fixed fields provide useful decoder guardrails.
    metadata_section = next(
        (section for section in sections if section["label_hash"] == "0x9DF23F77"),
        None,
    )
    if metadata_section and metadata_section["size"] < 48:
        raise ValueError(f"truncated clip metadata at 0x{start:X}")
    if metadata_section:
        offset = metadata_section["offset"]
        packed_tracks_and_frames = u32(data, offset + 32)
        metadata = {
            "format_version": u32(data, offset),
            "name_hash": f"0x{u32(data, offset + 4):08X}",
            "unknown_u32_08": u32(data, offset + 8),
            "sample_rate_fps": struct.unpack_from("<f", data, offset + 12)[0],
            "duration_seconds": struct.unpack_from("<f", data, offset + 16)[0],
            "unknown_u32_14": u32(data, offset + 20),
            "unknown_u32_18": u32(data, offset + 24),
            "unknown_u32_1c": u32(data, offset + 28),
            "bone_count": packed_tracks_and_frames & 0xFFFF,
            "original_frame_count": packed_tracks_and_frames >> 16,
            "sample_element_count": struct.unpack_from("<H", data, offset + 36)[0],
            "sample_stride_bytes": struct.unpack_from("<H", data, offset + 38)[0],
            "unknown_u32_28": u32(data, offset + 40),
            "unknown_u32_2c": u32(data, offset + 44),
        }
        if name:
            metadata["computed_name_hash"] = f"0x{game_crc32(name):08X}"
            metadata["name_hash_matches_name"] = game_crc32(name) == u32(data, offset + 4)

    bone_hashes = []
    bone_section = next(
        (section for section in sections if section["label_hash"] == "0xA3B26640"),
        None,
    )
    if bone_section and bone_section["size"] % 4 == 0:
        for offset in range(bone_section["offset"], bone_section["offset"] + bone_section["size"], 4):
            value = u32(data, offset)
            if value == 0xFFFFFFFF:
                break
            bone_hashes.append(
                {
                    "hash": f"0x{value:08X}",
                    "name": known_bones.get(value),
                }
            )

    stream_directory = None
    # The directory is a 3-byte-per-entry structure in every currently
    # inspected embedded clip (Sheepinator and Proton Drum).  It consists of
    # N little-endian words followed by N one-byte codes.  The words are
    # retained under their original raw keys for report compatibility.
    # Animation/animation_extract.py validates them as 48-byte joint-record destination
    # offsets and bit-width-minus-one codes. Component semantics remain unknown.
    directory_section = next(
        (section for section in sections if section["label_hash"] == "0xD070D358"),
        None,
    )
    if directory_section and directory_section["size"] % 3 == 0:
        count = directory_section["size"] // 3
        offset = directory_section["offset"]
        stream_directory = {
            "entry_count": count,
            "entries": [
                {
                    "raw_offset_word": struct.unpack_from("<H", data, offset + index * 2)[0],
                    "raw_code": data[offset + count * 2 + index],
                    "bit_width": data[offset + count * 2 + index] + 1,
                }
                for index in range(count)
            ],
        }

    return {
        "offset": start,
        "declared_size": declared_size,
        "section_count": section_count,
        "header_flags": header_flags,
        "table_end": table_end,
        "sections": sections,
        "name": name if metadata else None,
        "metadata": metadata,
        "bone_hashes": bone_hashes,
        "stream_directory": stream_directory,
    }


def inspect(path: Path, bone_names: list[str] | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    known_bones = {game_crc32(name): name for name in bone_names or []}
    starts = [match.start() for match in re.finditer(re.escape(MAGIC), data)]
    containers = []
    errors = []
    for start in starts:
        try:
            containers.append(read_container(data, start, known_bones))
        except ValueError as error:
            errors.append(str(error))

    strings = [m.group().decode("ascii") for m in PRINTABLE.finditer(data)]
    clip_names = []
    for index, value in enumerate(strings[:-1]):
        if value == "animclip built":
            clip_names.append(strings[index + 1])

    # The lookup has 16-byte records: clip-name hash followed by the offset of
    # that clip inside the root anim_clip_data section.  This is verified on
    # Sheepinator and Proton Drum and links metadata to a specific container.
    clip_lookup = []
    root = next((item for item in containers if item["offset"] == 0), None)
    if root:
        data_section = next(
            (item for item in root["sections"] if item["type_name"] == "anim_clip_data"),
            None,
        )
        lookup_section = next(
            (item for item in root["sections"] if item["type_name"] == "anim_clip_lookup"),
            None,
        )
        if data_section and lookup_section and lookup_section["size"] % 16 == 0:
            for offset in range(lookup_section["offset"], lookup_section["offset"] + lookup_section["size"], 16):
                name_hash = u32(data, offset)
                relative_offset = u32(data, offset + 4)
                clip_lookup.append(
                    {
                        "name_hash": f"0x{name_hash:08X}",
                        "relative_offset": relative_offset,
                        "container_offset": data_section["offset"] + relative_offset,
                    }
                )

    return {
        "path": str(path),
        "file_size": len(data),
        "container_offsets": starts,
        "containers": containers,
        "clip_names": clip_names,
        "clip_lookup": clip_lookup,
        "strings": strings,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("animset", type=Path)
    parser.add_argument("--json", type=Path, help="write the report here")
    parser.add_argument("--bones", type=Path, help="optional newline-delimited FBX bone names")
    args = parser.parse_args()
    bones = args.bones.read_text(encoding="utf-8").splitlines() if args.bones else None
    report = inspect(args.animset, bones)

    print(f"{report['path']}: {report['file_size']} bytes")
    print("containers:", ", ".join(f"0x{x:X}" for x in report["container_offsets"]))
    print("clips:", ", ".join(report["clip_names"]) or "(none embedded)")
    for item in report["containers"]:
        print(
            f"  0x{item['offset']:X}: {item['section_count']} sections, "
            f"declared size 0x{item['declared_size']:X}"
        )
    if report["errors"]:
        print("warnings:", *report["errors"], sep="\n  ")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
