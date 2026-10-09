"""Decode model-instance placements from an RC PS4 `.zone` asset.

Layout established on Novalis `art_buildings.zone` (docs/levels.md):

    0xC6A5905E  u32[n_models]   absolute file offsets of NUL-terminated model paths
    0x6987F172  u32[n_inst]     offsets of instance records inside section 0x06ABCAB2
    0x06ABCAB2  data heap       instance records (variable size)

Instance record (offsets relative to the record):
    +0x00  float32[16]  4x4 matrix, row-vector convention (rows 0-2 = local axes incl.
                        scale, row 3 = translation, w = 1); native Y-up game space
    +0x44  u32          record size in bytes (0xB0 + optional tail); records are 8-byte aligned
    +0x48  u64          per-instance id (unique within the zone)
    +0x58  u32          index into the 0xC6A5905E model list
    +0x5C  u32          flags (meaning unknown; kept raw)

Decals (projected, pre-clipped meshes) are split between the `.zone` and its `.zone_static`
("Zone Decal Built File"):

    .zone 0xBCF43558        u32[n] heap offsets of decal objects: +0x00 float32 4x4 projector matrix
                            (not needed for geometry), +0x48 inline NUL-terminated material path,
                            +0x288 u32 index of the decal's mesh record in the .zone_static
    .zone_static 0x657512BB 0x58-byte mesh records: u16 vertex count, u16 index count, u32 flags
                            (0xF12/0xE12/0xD12, raw), u32 absolute data offset, +0x40 float32[4]
                            bounding sphere (centre xyz, radius R)
    .zone_static 0xCF30405E mesh data: n_v 16-byte vertices (u16[3] position, u16 1, u32 normal,
                            u32 tangent, both 10:10:10:2 UNORM as in models), then n_v half2 UVs,
                            then n_i u16 triangle-list indices
    position = centre + (u / 65535 * 2 - 1) * R / sqrt(3)  (world-axis cube inscribed in the sphere)

The position frame was chosen over projector-space variants because decoded face normals match
the stored vertex normals (median |dot| 1.000 on 76 Novalis building decals, 0.990 on 27 terrain
decals, against ~0.4 for every projector frame); 19 small building decals still disagree.

Usage: python zone_instances.py <decoded .zone file> <out.json> [<decoded .zone_static file>]
"""
import json
import math
import struct
import sys
from pathlib import Path

DATA_HEAP = 0x06ABCAB2
INSTANCE_OFFSETS = 0x6987F172
MODEL_PATHS = 0xC6A5905E
DECAL_OFFSETS = 0xBCF43558
DECAL_MESHES = 0x657512BB
DECAL_RECORD_SIZE = 0x58
RECORD_HEADER_SIZE = 0xB0


def sections(data):
    if data[:4] != b'1TAD':
        raise ValueError('Not a DAT1 container')
    count, = struct.unpack_from('<I', data, 12)
    return {label: (offset, size) for label, offset, size in
            (struct.unpack_from('<III', data, 16 + 12 * i) for i in range(count))}


def _cstring(data, offset):
    return data[offset:data.index(b'\0', offset)].decode('ascii')


def decode_zone(data):
    table = sections(data)
    missing = [hex(t) for t in (DATA_HEAP, INSTANCE_OFFSETS, MODEL_PATHS) if t not in table]
    if missing:
        raise ValueError(f'Zone lacks instance sections {missing}')
    heap, heap_size = table[DATA_HEAP]
    off, size = table[MODEL_PATHS]
    models = [_cstring(data, o).replace('\\', '/') for o in struct.unpack_from(f'<{size // 4}I', data, off)]
    off, size = table[INSTANCE_OFFSETS]
    records = struct.unpack_from(f'<{size // 4}I', data, off)
    instances, ids = [], set()
    for n, rel in enumerate(records):
        r = heap + rel
        matrix = struct.unpack_from('<16f', data, r)
        record_size, = struct.unpack_from('<I', data, r + 0x44)
        instance_id, = struct.unpack_from('<Q', data, r + 0x48)
        model_index, flags = struct.unpack_from('<II', data, r + 0x58)
        # Invariants that the layout claims; any failure means the layout is wrong for this zone.
        # The final record's tail may overrun an unpadded heap by a few bytes (13 of 229 zones).
        last = n + 1 == len(records)
        if not (record_size >= RECORD_HEADER_SIZE and rel + RECORD_HEADER_SIZE <= heap_size
                and (rel + record_size <= heap_size or (last and rel + record_size - heap_size < 8))):
            raise ValueError(f'record {n}: size {record_size:#x} out of bounds')
        if n + 1 < len(records) and records[n + 1] - rel != (record_size + 7) & ~7:   # 8-byte aligned
            raise ValueError(f'record {n}: size field {record_size:#x} != stride {records[n + 1] - rel:#x}')
        if (matrix[3], matrix[7], matrix[11], matrix[15]) != (0.0, 0.0, 0.0, 1.0):
            raise ValueError(f'record {n}: matrix w column is not (0,0,0,1)')
        if model_index >= len(models):
            raise ValueError(f'record {n}: model index {model_index} >= {len(models)}')
        if instance_id in ids:
            raise ValueError(f'record {n}: duplicate instance id {instance_id:#018x}')
        ids.add(instance_id)
        instances.append(dict(
            index=n, id=f'{instance_id:016X}', model_index=model_index, flags=f'{flags:#010x}',
            matrix_rows=[list(matrix[i:i + 4]) for i in range(0, 16, 4)],
            tail=data[r + RECORD_HEADER_SIZE:min(r + record_size, heap + heap_size)].hex()))
    return dict(models=models, instances=instances,
                models_used=len({i['model_index'] for i in instances}))


def _unorm_1010102(word):
    v = [((word >> shift) & 0x3FF) / 1023 * 2 - 1 for shift in (0, 10, 20)]
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / length for c in v]


def decode_decals(zone_data, static_data):
    """Decal meshes in native Y-up world space, joined with their material paths."""
    table, static_table = sections(zone_data), sections(static_data)
    if DECAL_OFFSETS not in table:
        return []
    heap = table[DATA_HEAP][0]
    off, size = table[DECAL_OFFSETS]
    meshes_off, meshes_size = static_table[DECAL_MESHES]
    count = meshes_size // DECAL_RECORD_SIZE
    decals, used = [], set()
    for n, rel in enumerate(struct.unpack_from(f'<{size // 4}I', zone_data, off)):
        r = heap + rel
        material = zone_data[r + 0x48:zone_data.index(b'\0', r + 0x48)].decode('ascii').replace('\\', '/')
        index, = struct.unpack_from('<I', zone_data, r + 0x288)
        if index >= count or index in used:
            raise ValueError(f'decal {n}: mesh index {index} invalid or reused')
        used.add(index)
        rec = meshes_off + index * DECAL_RECORD_SIZE
        n_v, n_i, flags, data = struct.unpack_from('<HHII', static_data, rec)
        cx, cy, cz, radius = struct.unpack_from('<4f', static_data, rec + 0x40)
        half = radius / math.sqrt(3)
        positions, normals = [], []
        for j in range(n_v):
            x, y, z, w, normal, _tangent = struct.unpack_from('<3HHII', static_data, data + 16 * j)
            positions.append([c + (u / 65535 * 2 - 1) * half for c, u in ((cx, x), (cy, y), (cz, z))])
            normals.append(_unorm_1010102(normal))
        uvs = [list(struct.unpack_from('<2e', static_data, data + 16 * n_v + 4 * j)) for j in range(n_v)]
        indices = struct.unpack_from(f'<{n_i}H', static_data, data + 20 * n_v)
        if n_i % 3 or (indices and max(indices) >= n_v):
            raise ValueError(f'decal {n}: bad index buffer')
        decals.append(dict(index=n, mesh_index=index, material=material, flags=f'{flags:#06x}',
                           sphere=[cx, cy, cz, radius], positions=positions, normals=normals, uvs=uvs,
                           triangles=[list(indices[i:i + 3]) for i in range(0, n_i, 3)]))
    if len(used) != count:
        raise ValueError(f'{count - len(used)} .zone_static meshes have no decal object')
    return decals


def main():
    source, out = Path(sys.argv[1]), Path(sys.argv[2])
    result = decode_zone(source.read_bytes())
    if len(sys.argv) > 3:
        result['decals'] = decode_decals(source.read_bytes(), Path(sys.argv[3]).read_bytes())
    result['source'] = source.name
    out.write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(f"{source.name}: {len(result['instances'])} instances, "
          f"{result['models_used']}/{len(result['models'])} models used, {len(result.get('decals', []))} decals")


if __name__ == '__main__':
    main()
