"""Rift Apart (PC) ``.model`` geometry, in the dict schema of ``formats.native_model.decode_model``.

The asset bytes start with the 36-byte toc header (u32 magic 0x9D2C0FA9, u32 offset and u32 size
of the streamed sections, 24 zero bytes); the DAT1 (type 0x9D2C0FA9) follows at +36. String
pointers are offsets from the DAT1 start. Layouts follow ALERT ``dat1lib`` and RCRA-Forge, checked
on the installed files (docs/rift-apart.md):

  0x283D0383 built  f32 position offset at +0x1C, f32 position scale at +0x2C (1/4096 seen),
                    u32 at +0x30 whose low 4 bits give the UV scale (1 << n) / 16384
  0xA98BE69B        16-byte vertex: int16 x, y, z, w | u32 packed normal | int16 u, v
                    The normal is ALERT's two-angle form (10-bit x, 10-bit y, sign at bit 31);
                    w is not decoded (not a +-1 handedness: it spans the int16 range)
  0x0859863D        u16 indices; mesh flag 0x10 makes them relative to the mesh's first vertex
  0x78D9CBDE        64-byte mesh record: +4 u64 id, +20 u32 first vertex, first index, index
                    count, vertex count, +36 u16 flags, material slot, first skin batch, batch count,
                    +56 u32 first weight index
  0x06EB7EFC        looks: 8 (u16 first mesh, u16 count) LOD slots per look
  0x3250BB80        materials: the first half holds (u64 path, u64 name) string pointers
  0x6B855EED        int16 UV1 per vertex (same scale); this and the colours may be zero-padded to 16 bytes
  0x5CBA9DE9        u8 RGBA per vertex
  0x15DF9D3B        joints: i16 parent, u16 index, u16, u16, u32 name hash, u32 name pointer

Skin weights are not decoded yet. 0xCCBAFF15 holds 8 bytes (4 u8 joints, 4 u8 weights) per vertex
of the meshes with flag 0x100, reached through the mesh's first weight index (Ratchet: 202,471
records = the vertex count of those meshes); 0xC61B1FF5/0xDCA379A2 batches differ from the 2016
layout. Nothing can check the joint mapping before animation is read. A
skinned model is shown in its bind pose, which is how its vertices are stored.
"""
import math
import struct

import numpy as np

from rc2016.common import read, sha
from rc2016.games.rcra.toc import sections as dat1_sections

MODEL_MAGIC = 0x9D2C0FA9
HEADER = 36
BUILT, VERTICES, INDEXES, MESHES = 0x283D0383, 0xA98BE69B, 0x0859863D, 0x78D9CBDE
LOOK, MATERIALS, UV1, COLORS, JOINTS = 0x06EB7EFC, 0x3250BB80, 0x6B855EED, 0x5CBA9DE9, 0x15DF9D3B
JOINT_TRANSFORMS = 0xDCC88A19
SKIN_SECTIONS = (0xC61B1FF5, 0xDCA379A2, 0xCCBAFF15)
LODS_PER_LOOK = 8
MESH_STRIDE = 64
VERTEX = np.dtype([('xyzw', '<i2', 4), ('normal', '<u4'), ('uv', '<i2', 2)])
DECODED = {BUILT, VERTICES, INDEXES, MESHES, LOOK, MATERIALS, UV1, COLORS, JOINTS}
SCHEMA = 'rcra-pc-model-v1'


class UnsupportedModelError(ValueError):
    pass


def _cstring(dat1, offset, bounds):
    start, end = bounds
    if not start <= offset < end:
        raise ValueError('String pointer outside the model string pool')
    stop = dat1.find(b'\0', offset, end)
    if stop < 0:
        raise ValueError('Unterminated model string')
    return dat1[offset:stop].decode('ascii')


def unpack_normals(words):
    """ALERT's normal decode: x, y from two 10-bit fields, z from the remainder, its sign in bit 31."""
    words = np.asarray(words, np.uint32).astype(np.int64)
    nx = (words & 0x3FF) * 0.00276483595 - math.sqrt(2)
    ny = ((words >> 10) & 0x3FF) * 0.00276483595 - math.sqrt(2)
    q = nx * nx + ny * ny
    w = np.sqrt(np.clip(1.0 - 0.25 * q, 0.0, None))
    nz = 1.0 - 0.5 * q
    nz = np.where((words >> 31) == 0, -nz, nz)
    vectors = np.stack((nx * w, ny * w, nz), axis=1)
    length = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.where(length > 1e-12, vectors / np.maximum(length, 1e-12), np.array([0.0, 0.0, 1.0]))


def _looks(raw, mesh_count):
    if not raw:
        return [dict(index=0, source='implicit_all_meshes',
                     lods=[dict(index=0, start=0, count=mesh_count, meshes=list(range(mesh_count)), implicit=True)])]
    if len(raw) % (4 * LODS_PER_LOOK):
        raise ValueError('Look table is not a multiple of eight LOD records')
    looks = []
    for look_index, base in enumerate(range(0, len(raw), 4 * LODS_PER_LOOK)):
        lods = []
        for lod in range(LODS_PER_LOOK):
            start, count = struct.unpack_from('<HH', raw, base + 4 * lod)
            if start + count > mesh_count:
                raise ValueError('Look/LOD references mesh records outside the model')
            lods.append(dict(index=lod, start=start, count=count, meshes=list(range(start, start + count))))
        looks.append(dict(index=look_index, lods=lods))
    return looks


def _materials(dat1, raw, bounds):
    if len(raw) % 32:
        raise ValueError('Material table is not two halves of 16-byte records')
    result = []
    for offset in range(0, len(raw) // 2, 16):
        path_ptr, name_ptr = struct.unpack_from('<QQ', raw, offset)
        path = _cstring(dat1, path_ptr, bounds)
        result.append(dict(index=len(result), path=path.replace('\\', '/'), name=_cstring(dat1, name_ptr, bounds),
                           raw_record_hex=raw[offset:offset + 16].hex()))
    return result


def _joints(dat1, raw, bounds):
    if len(raw) % 16:
        raise ValueError('Joint table is not a multiple of 16 bytes')
    joints = []
    for offset in range(0, len(raw), 16):
        parent, index, _a, _b, name_hash, name_ptr = struct.unpack_from('<hHHHII', raw, offset)
        if parent >= len(raw) // 16 or parent < -1:
            raise ValueError('Joint parent outside the joint table')
        joints.append(dict(index=index, parent=parent, name=_cstring(dat1, name_ptr, bounds), name_hash=f'0x{name_hash:08X}'))
    return joints


def _per_vertex(raw, vertex_count, label):
    """A 4-byte-per-vertex section; the stored section may be padded to a 16-byte multiple."""
    need = vertex_count * 4
    if not need <= len(raw) < need + 16 or any(raw[need:]):
        raise ValueError(f'{label} section size {len(raw)} does not match {vertex_count} vertices')
    return raw[:need]


def _tuples(values):
    return list(map(tuple, values.tolist()))


def decode_model(path, lod='All', look=0, data=None, as_arrays=False):
    """Decode a Rift Apart model asset (toc header + DAT1). ``path`` is a label when ``data`` is given."""
    from rc2016.formats.native_model import _selected_meshes
    data = read(path) if data is None else bytes(data)
    source_sha256 = sha(data)
    if len(data) < HEADER + 16 or struct.unpack_from('<I', data, 0)[0] != MODEL_MAGIC:
        raise UnsupportedModelError('Not a Rift Apart model (expected magic 0x%08X)' % MODEL_MAGIC)
    dat1 = data[HEADER:]
    if dat1[:4] != b'1TAD' or struct.unpack_from('<I', dat1, 4)[0] != MODEL_MAGIC:
        raise UnsupportedModelError('Rift Apart model without its DAT1 at +36')
    declared = struct.unpack_from('<I', dat1, 8)[0]
    if declared != len(dat1):
        raise ValueError('Model DAT1 declares %d bytes, asset holds %d' % (declared, len(dat1)))
    table = dat1_sections(dat1)
    first = min((o for o, _s in table.values()), default=len(dat1))
    bounds = (16 + 12 * len(table), first)

    def chunk(tag, required=False):
        if tag not in table:
            if required:
                raise UnsupportedModelError('Missing model section 0x%08X' % tag)
            return b''
        offset, size = table[tag]
        return dat1[offset:offset + size]

    if VERTICES not in table and MESHES not in table:
        raise UnsupportedModelError('This model holds no geometry (no vertex or mesh section); '
                                    'it carries only looks, locators or joints')
    built = chunk(BUILT, True)
    if len(built) < 0x34:
        raise ValueError('Model built section is too short')
    offset = np.array(struct.unpack_from('<3f', built, 0x1C), np.float64)
    scale = struct.unpack_from('<f', built, 0x2C)[0]
    uv_bits = struct.unpack_from('<I', built, 0x30)[0]
    uv_scale = (1 << (uv_bits & 0xF)) / 16384.0
    if not (math.isfinite(scale) and scale > 0 and np.all(np.isfinite(offset))):
        raise ValueError('Invalid model position scale/offset')
    vertices_raw, indexes, meshes_raw = chunk(VERTICES, True), chunk(INDEXES, True), chunk(MESHES, True)
    if len(vertices_raw) % VERTEX.itemsize or len(indexes) % 2 or len(meshes_raw) % MESH_STRIDE:
        raise ValueError('Misaligned core geometry section')
    vertex_count = len(vertices_raw) // VERTEX.itemsize
    if not vertex_count:
        raise ValueError('Model has no vertices')
    records = np.frombuffer(vertices_raw, VERTEX)
    positions = records['xyzw'][:, :3].astype(np.float64) * scale + offset
    normals = unpack_normals(records['normal'])
    uv = records['uv'].astype(np.float64) * uv_scale
    uvs = np.stack((uv[:, 0], 1.0 - uv[:, 1]), axis=1)      # V flipped like the 2016 decoder (Blender convention)
    uv1 = None
    uv1_raw = chunk(UV1)
    if uv1_raw:
        second = np.frombuffer(_per_vertex(uv1_raw, vertex_count, 'UV1'), '<i2').reshape(-1, 2).astype(np.float64) * uv_scale
        uv1 = np.stack((second[:, 0], 1.0 - second[:, 1]), axis=1)
    colors = None
    colors_raw = chunk(COLORS)
    if colors_raw:
        colors = np.frombuffer(_per_vertex(colors_raw, vertex_count, 'Vertex colour'), np.uint8).reshape(-1, 4) / 255.0
    if not (np.all(np.isfinite(positions)) and np.all(np.isfinite(uvs))):
        raise ValueError('Non-finite vertex data')
    materials = _materials(dat1, chunk(MATERIALS), bounds)
    joints = _joints(dat1, chunk(JOINTS), bounds)
    mesh_count = len(meshes_raw) // MESH_STRIDE
    looks = _looks(chunk(LOOK), mesh_count)
    membership = {i: [] for i in range(mesh_count)}
    for look_row in looks:
        for level in look_row['lods']:
            for index in level['meshes']:
                membership[index].append(dict(look=look_row['index'], lod=level['index']))
    selected = _selected_meshes(looks, mesh_count, lod, look)
    index_words = np.frombuffer(indexes, '<u2')
    # As in the 2016 schema, colours and UV1 are lists of tuples in both modes (consumers test them for truth).
    uv1_out = _tuples(uv1) if uv1 is not None else None
    colors_out = _tuples(colors) if colors is not None else None
    if not as_arrays:
        positions_out, normals_out, uvs_out = _tuples(positions), _tuples(normals), _tuples(uvs)
    else:
        positions_out, normals_out, uvs_out = positions, normals, uvs
    result = []
    for mesh_index in selected:
        at = mesh_index * MESH_STRIDE
        unknown_u32_00, mesh_id = struct.unpack_from('<IQ', meshes_raw, at)
        first_vertex, first_index, index_count, local_count = struct.unpack_from('<4I', meshes_raw, at + 20)
        flags, material_index, first_skin_batch, skin_batch_count = struct.unpack_from('<4H', meshes_raw, at + 36)
        first_weight_index, unknown_u32_60 = struct.unpack_from('<II', meshes_raw, at + 56)
        if not local_count or index_count % 3:
            raise ValueError(f'Mesh {mesh_index} has invalid vertex/index counts')
        if first_vertex + local_count > vertex_count or first_index + index_count > len(index_words):
            raise ValueError(f'Mesh {mesh_index} ranges exceed the geometry buffers')
        if materials and material_index >= len(materials):
            raise ValueError(f'Mesh {mesh_index} uses material slot {material_index} outside the material table')
        base = 0 if flags & 0x10 else first_vertex
        local = index_words[first_index:first_index + index_count].astype(np.int64) - base
        if index_count and (local.min() < 0 or local.max() >= local_count):
            raise ValueError(f'Mesh {mesh_index} indices fall outside its vertices')
        triangles = local.reshape(-1, 3)
        window = slice(first_vertex, first_vertex + local_count)
        result.append(dict(
            index=mesh_index, mesh_id=f'0x{mesh_id:016X}', first_vertex=first_vertex, first_index=first_index,
            index_count=index_count, vertex_count=local_count, flags=flags, material_index=material_index,
            first_skin_batch=first_skin_batch, skin_batch_count=skin_batch_count,
            first_weight_index=first_weight_index, unknown_u32_00=unknown_u32_00, unknown_u32_60=unknown_u32_60,
            raw_record_hex=meshes_raw[at:at + MESH_STRIDE].hex(),
            triangles=triangles if as_arrays else triangles.tolist(),
            positions=positions_out[window], normals=normals_out[window], uv0=uvs_out[window],
            uv1=uv1_out[window] if uv1_out is not None else None,
            colors=colors_out[window] if colors_out is not None else None,
            tangents=None, tangent_signs=None, packed_normals=None, raw_vectors=None, uv0_secondary_packed=None,
            static_vector_eligible=False, weights=None, unweighted_vertices=local_count,
            skinned=bool(skin_batch_count), material=materials[material_index] if material_index < len(materials) else None,
            index_base='local' if flags & 0x10 else 'global', memberships=membership[mesh_index]))
    section_sizes = {f'0x{k:08X}': size for k, (_o, size) in table.items()}
    unsupported = sorted(f'0x{k:08X}' for k in table if k not in DECODED)
    stream_offset, stream_size = struct.unpack_from('<II', data, 4)
    return dict(
        schema=SCHEMA, game='rcra', source=str(path), source_sha256=source_sha256,
        dat1=dict(type_hash=f'0x{MODEL_MAGIC:08X}', endianness='little', header_bytes=HEADER,
                  streamed_sections_offset=stream_offset, streamed_sections_size=stream_size,
                  section_table_count=len(table)),
        source_units=dict(position=f'int16 * {scale!r} + {offset.tolist()}', position_scale=scale,
                          position_offset=offset.tolist(), uv_scale=uv_scale,
                          uv0_decode='int16 * uv scale; V flipped for the Blender convention like the 2016 decoder'),
        packed_normal='ALERT two-angle form: 10-bit x and y, z from the remainder, sign at bit 31 (face-normal checked)',
        tangent='not decoded: the vertex w word is not yet interpreted',
        skin='not decoded: bind-pose geometry only; skin batch layout differs from 2016',
        vertex_count=vertex_count, index_count=len(index_words), mesh_record_count=mesh_count,
        selected_mesh_count=len(result), selected_mesh_indices=selected, import_lod=lod, import_look=look,
        looks=looks, materials=materials, joints=joints, skeleton_joint_count=len(joints), meshes=result,
        section_sizes=section_sizes, unsupported_sections=unsupported,
        skin_sections_present=[f'0x{t:08X}' for t in SKIN_SECTIONS if t in table])


def decode_for_viewer(source, model_id, source_path, as_arrays):
    """The ``Game.decode_model`` hook: (decoded, data) with packed_sha256, codec and joint_count.

    joint_count stays 0 until Rift Apart skinning and animation are decoded, so the loader does not
    look for 2016 animation sets; the skeleton itself is in ``joints``."""
    data, digest = source.bytes(model_id)
    decoded = decode_model(source_path, lod='All', data=data, as_arrays=as_arrays)
    decoded.update(packed_sha256=digest, codec='rcra-dsar', joint_count=0,
                   animation_note='Rift Apart animation and skinning are not decoded yet; the model shows its bind pose')
    return decoded, data
