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
    +0x99  u8           number of material overrides
    +0xA4  u32          record-relative offset of the material-override block, 0xFFFFFFFF if none;
                        n x 12 bytes: u32 CRC32 of the material slot name, u64 CRC64 of the
                        replacement material path (both normalised: lower case, '/' separators)

    +0x9C  u32          number of position-keyed vertex colours
    +0xA8  u32          record-relative offset of their keys, 0xFFFFFFFF if none: n x u64, three
                        21-bit fields (x, y, z from bit 0) each biased by 2^20, sorted ascending
    +0xAC  u32          record-relative offset of their colours: n x 4 bytes, directly after the keys

    0x032D450A  (u32 path offset, u32 0xFFFFFFFF) pairs: the zone's replacement material paths

Vertex colours (vertex_colour_block) were read on one record, the Novalis terrain
`rp_novalis_level1_tfrag` in `art_terrain.zone` (8,997 entries, the block ending the record).
A key is the model-space position x 10 rounded to an integer: 8,995 of its keys lie within 0.6
units per axis of a vertex of the placed model (residuals -0.5..0.5625, so the keys were
rounded from positions finer than the model's int16 grid), and only part of the terrain is
listed. Byte 3 is 0 along the visible dirt paths and 255 on the grass around them; byte 0 is
roughly its inverse. Which shader input each byte feeds, and what unlisted vertices receive,
are inferred, not decoded.

Material overrides were validated on every main-game zone: 71,145 of 228,727 instances carry
a block, all inside their records; every non-null replacement hash is a path in the same zone's
0x032D450A list. Slot matching against the model is left to the caller (scene.py), because
22% of the slot hashes name slots the placed model no longer has.

Decals (projected, pre-clipped meshes) are split between the `.zone` and its `.zone_static`
("Zone Decal Built File"):

    .zone 0xBCF43558        u32[n] heap offsets of decal objects: +0x00 float32 4x4 projector matrix
                            (row vectors: rows 0-2 are the box axes times their half extents, row 3 the
                            box centre), +0x48 inline NUL-terminated material path, +0x288 u32 index of
                            the decal's mesh record in the .zone_static
    .zone_static 0x657512BB 0x58-byte mesh records: u16 vertex count, u16 index count, u32 flags
                            (0xF12/0xE12/0xD12, raw), u32 absolute data offset, +0x40 float32[4]
                            bounding sphere (centre xyz, radius R)
    .zone_static 0xCF30405E mesh data: n_v 16-byte vertices (s16[3] position, u16 1, u32 normal,
                            u32 tangent, both 10:10:10:2 UNORM as in models), then n_v half2 UVs,
                            then n_i u16 triangle-list indices
    local    = s / 32768                  (signed, so a surface at the box centre wraps around 0)
    position = local @ M[:3, :3] + M[3, :3]    (M the projector matrix)
    normal   = stored normal @ inverse(M[:3, :3]).T, normalised

Read as unsigned values over a world-axis cube (the first reading), the surfaces near the box centre
split into two sheets at the box faces and the decals stood up as walls; with the signed values a
Novalis pit's decal is 0.65 units deep and lies on the crater patch. The stored normals are in the box
frame (most point along local -Z, the projection axis). Face normals of the box-frame mesh match the
stored ones with median |dot| 0.994 (86% of 35,859 Novalis triangles above 0.95, against 78% unsigned).

Usage: python zone_instances.py <decoded .zone file> <out.json> [<decoded .zone_static file>]
"""
import json
import math
import re
import struct
import sys
from pathlib import Path

from rc2016.formats.container import game_crc32

DATA_HEAP = 0x06ABCAB2
INSTANCE_OFFSETS = 0x6987F172
MODEL_PATHS = 0xC6A5905E
OVERRIDE_MATERIALS = 0x032D450A
DECAL_OFFSETS = 0xBCF43558
DECAL_MESHES = 0x657512BB
DECAL_RECORD_SIZE = 0x58
RECORD_HEADER_SIZE = 0xB0
OVERRIDE_ENTRY_SIZE = 12
NO_BLOCK = 0xFFFFFFFF


def sections(data):
    if len(data) < 16 or data[:4] != b'1TAD':
        raise ValueError('Not a DAT1 container')
    count, = struct.unpack_from('<I', data, 12)
    if 16 + 12 * count > len(data):
        raise ValueError('Truncated DAT1 section table')
    return {label: (offset, size) for label, offset, size in
            (struct.unpack_from('<III', data, 16 + 12 * i) for i in range(count))}


def _normalized(text):
    return re.sub('/+', '/', text.lower().replace('\\', '/'))


def name_crc32(text):
    """CRC32 of a normalised name, as stored for material slot names."""
    return game_crc32(_normalized(text))


def _crc64_table():
    table = []
    for i in range(256):
        value = i
        for _ in range(8):
            value = (value >> 1) ^ (0xC96C5795D7870F42 if value & 1 else 0)
        table.append(value)
    return table


_CRC64 = _crc64_table()


def path_crc64(path):
    """Insomniac's 64-bit asset path hash: reflected CRC-64 with polynomial and seed
    0xC96C5795D7870F42 over the normalised path, shifted right by 2 with the top bit set."""
    value = 0xC96C5795D7870F42
    for byte in _normalized(path).encode('ascii'):
        value = (value >> 8) ^ _CRC64[(value ^ byte) & 0xFF]
    return value >> 2 | 0x8000000000000000


def override_materials(data, table=None):
    """The zone's replacement material paths ('/' separators) from 0x032D450A; [] if absent."""
    table = table or sections(data)
    if OVERRIDE_MATERIALS not in table:
        return []
    off, size = table[OVERRIDE_MATERIALS]
    if size % 8:
        raise ValueError(f'material list size {size} is not a multiple of 8')
    return [_cstring(data, struct.unpack_from('<I', data, off + i)[0]).replace('\\', '/')
            for i in range(0, size, 8)]


def _material_overrides(data, record, record_size, heap_end, by_hash):
    """[{slot_hash, material_hash, material}] of one instance record (material None if unlisted)."""
    count = data[record + 0x99]
    block, = struct.unpack_from('<I', data, record + 0xA4)
    if block == NO_BLOCK and count:
        raise ValueError(f'{count} material overrides without a block')
    if not count:   # in the game files a zero count always comes with 0xFFFFFFFF
        return []
    end = block + OVERRIDE_ENTRY_SIZE * count
    if block < RECORD_HEADER_SIZE or block % 4 or end > record_size or record + end > heap_end:
        raise ValueError(f'material override block {block:#x} x {count} outside the record')
    result = []
    for k in range(count):
        slot, material = struct.unpack_from('<IQ', data, record + block + OVERRIDE_ENTRY_SIZE * k)
        result.append(dict(slot_hash=f'{slot:08X}', material_hash=f'{material:016X}',
                           material=by_hash.get(material)))
    return result


VERTEX_COLOUR_KEY_BITS = 21


def vertex_colour_block(data, record, record_size):
    """(keys int64 (n, 3), colours uint8 (n, 4)) of the instance record at absolute offset ``record``,
    or None when it has no block or the block does not fit the layout above (count at +0x9C, keys at
    the offset in +0xA8, colours at the offset in +0xAC directly after them, inside the record).
    Keys are model-space positions x 10 (see the module docstring)."""
    import numpy as np
    count, keys_at = struct.unpack_from('<II', data, record + 0x9C)[0], struct.unpack_from('<I', data, record + 0xA8)[0]
    colours_at, = struct.unpack_from('<I', data, record + 0xAC)
    if keys_at == NO_BLOCK or not count or colours_at != keys_at + 8 * count \
            or keys_at < RECORD_HEADER_SIZE or colours_at + 4 * count > record_size or record + record_size > len(data):
        return None
    raw = np.frombuffer(data, '<u8', count, record + keys_at)
    mask, bias = np.uint64((1 << VERTEX_COLOUR_KEY_BITS) - 1), 1 << (VERTEX_COLOUR_KEY_BITS - 1)
    keys = np.stack([((raw >> np.uint64(VERTEX_COLOUR_KEY_BITS * a)) & mask).astype(np.int64) - bias
                     for a in range(3)], axis=1)
    colours = np.frombuffer(data, np.uint8, 4 * count, record + colours_at).reshape(count, 4).copy()
    return keys, colours


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
    replacements = override_materials(data, table)
    by_hash = {}
    for path in replacements:
        by_hash.setdefault(path_crc64(path), path)
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
            material_overrides=_material_overrides(data, r, record_size, heap + heap_size, by_hash),
            tail=data[r + RECORD_HEADER_SIZE:min(r + record_size, heap + heap_size)].hex()))
    return dict(models=models, instances=instances, override_materials=replacements,
                models_used=len({i['model_index'] for i in instances}))


def decode_zone_arrays(data):
    """decode_zone's placements as arrays, for the level viewer: the same invariants and errors, without
    per-instance dicts, matrix lists or the tail hex.

    {'models', 'override_materials', 'instances' (count), 'matrices' float32 (n, 16) row-vector,
     'instance_flags' uint32 (n,) from record +0x5c,
     'model_index' uint32 (n,), 'overrides' {instance number: decode_zone's material_overrides list},
     'vertex_colours' {instance number: vertex_colour_block}}.
    Only instances with a nonzero override count appear in 'overrides', and only those with a
    vertex-colour block in 'vertex_colours'. Any invariant failure is
    re-raised by decode_zone itself, so the message is the one the full decoder gives."""
    import numpy as np
    table = sections(data)
    missing = [hex(t) for t in (DATA_HEAP, INSTANCE_OFFSETS, MODEL_PATHS) if t not in table]
    if missing:
        raise ValueError(f'Zone lacks instance sections {missing}')
    heap, heap_size = table[DATA_HEAP]
    off, size = table[MODEL_PATHS]
    models = [_cstring(data, o).replace('\\', '/') for o in struct.unpack_from(f'<{size // 4}I', data, off)]
    off, size = table[INSTANCE_OFFSETS]
    rel = np.frombuffer(data, '<u4', size // 4, off).astype(np.int64)
    replacements = override_materials(data, table)
    n = len(rel)
    result = dict(models=models, override_materials=replacements, instances=n, overrides={}, vertex_colours={},
                  matrices=np.zeros((0, 16), np.float32), model_index=np.zeros(0, np.uint32),
                  instance_flags=np.zeros(0, np.uint32))
    if not n:
        return result
    r = heap + rel
    if int(r.min()) < 0 or int(r.max()) + RECORD_HEADER_SIZE > len(data):
        return _arrays_from_full(data)
    raw = np.frombuffer(data, np.uint8)[r[:, None] + np.arange(0x60)]
    matrices = np.ascontiguousarray(raw[:, :64]).view('<f4')
    record_size = np.ascontiguousarray(raw[:, 0x44:0x48]).view('<u4')[:, 0].astype(np.int64)
    ids = np.ascontiguousarray(raw[:, 0x48:0x50]).view('<u8')[:, 0]
    model_index = np.ascontiguousarray(raw[:, 0x58:0x5C]).view('<u4')[:, 0]
    instance_flags = np.ascontiguousarray(raw[:, 0x5C:0x60]).view('<u4')[:, 0]
    last = np.zeros(n, bool); last[-1] = True
    ok = ((record_size >= RECORD_HEADER_SIZE) & (rel + RECORD_HEADER_SIZE <= heap_size)
          & ((rel + record_size <= heap_size) | (last & (rel + record_size - heap_size < 8))))
    ok[:-1] &= rel[1:] - rel[:-1] == (record_size[:-1] + 7) & ~7
    ok &= (matrices[:, 3] == 0.0) & (matrices[:, 7] == 0.0) & (matrices[:, 11] == 0.0) & (matrices[:, 15] == 1.0)
    ok &= model_index < len(models)
    if not ok.all() or len(np.unique(ids)) != n:
        return _arrays_from_full(data)
    by_hash = {}
    for path in replacements:
        by_hash.setdefault(path_crc64(path), path)
    counts = np.frombuffer(data, np.uint8)[r + 0x99]
    for i in np.flatnonzero(counts).tolist():
        result['overrides'][i] = _material_overrides(data, int(r[i]), int(record_size[i]), heap + heap_size, by_hash)
    has_colours = np.frombuffer(data, np.uint8)[r[:, None] + np.arange(0xA8, 0xAC)].view('<u4')[:, 0] != NO_BLOCK
    for i in np.flatnonzero(has_colours).tolist():
        block = vertex_colour_block(data, int(r[i]), int(record_size[i]))
        if block is not None:
            result['vertex_colours'][i] = block
    result.update(matrices=matrices, model_index=model_index, instance_flags=instance_flags)
    return result


def _arrays_from_full(data):
    """decode_zone_arrays through the full decoder (it raises the exact invariant error)."""
    import numpy as np
    zone = decode_zone(data)
    instances = zone['instances']
    return dict(models=zone['models'], override_materials=zone['override_materials'], instances=len(instances),
                matrices=np.array([[x for row in i['matrix_rows'] for x in row] for i in instances], np.float32).reshape(-1, 16),
                model_index=np.array([i['model_index'] for i in instances], np.uint32),
                instance_flags=np.array([int(i['flags'], 16) for i in instances], np.uint32),
                overrides={n: i['material_overrides'] for n, i in enumerate(instances) if i['material_overrides']},
                vertex_colours={})


def _unorm_1010102(word):
    v = [((word >> shift) & 0x3FF) / 1023 * 2 - 1 for shift in (0, 10, 20)]
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / length for c in v]


def decode_decals(zone_data, static_data):
    """Decal meshes in native Y-up world space, joined with their material paths."""
    import numpy as np
    table, static_table = sections(zone_data), sections(static_data)
    if DECAL_OFFSETS not in table:
        return []
    if DATA_HEAP not in table:
        raise ValueError('Zone lacks its data heap for decal records')
    if DECAL_MESHES not in static_table:
        raise ValueError('.zone_static lacks its decal mesh section')
    heap = table[DATA_HEAP][0]
    off, size = table[DECAL_OFFSETS]
    meshes_off, meshes_size = static_table[DECAL_MESHES]
    if meshes_off + meshes_size > len(static_data):
        raise ValueError('.zone_static decal mesh section outside file')
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
        projector = np.array(struct.unpack_from('<16f', zone_data, r)).reshape(4, 4)
        vertices = [struct.unpack_from('<3hHII', static_data, data + 16 * j) for j in range(n_v)]
        local = np.array([v[:3] for v in vertices], np.float64).reshape(-1, 3) / 32768.0
        positions = (local @ projector[:3, :3] + projector[3, :3]).tolist()
        stored = np.array([_unorm_1010102(v[4]) for v in vertices], np.float64).reshape(-1, 3)
        rotated = stored @ np.linalg.inv(projector[:3, :3]).T
        normals = (rotated / np.maximum(np.linalg.norm(rotated, axis=1, keepdims=True), 1e-12)).tolist()
        uvs = [list(struct.unpack_from('<2e', static_data, data + 16 * n_v + 4 * j)) for j in range(n_v)]
        indices = struct.unpack_from(f'<{n_i}H', static_data, data + 20 * n_v)
        if n_i % 3 or (indices and max(indices) >= n_v):
            raise ValueError(f'decal {n}: bad index buffer')
        decals.append(dict(index=n, mesh_index=index, material=material, flags=f'{flags:#06x}',
                           sphere=[cx, cy, cz, radius], projector=projector.tolist(), positions=positions, normals=normals, uvs=uvs,
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
