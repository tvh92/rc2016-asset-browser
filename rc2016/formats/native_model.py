"""Ratchet & Clank (EUR) PS4 DAT1 model geometry decoder.

This is deliberately a PS4-direct-DAT1 adapter.  The packed layout is the
one used by the archived game-specific 3ds Max importer, not the different
MSMR/RCRA PC stream layout in ALERT/RCRA-Forge.  Every decoded result retains
the source hash and the raw mesh record; fields without an evidence-backed
interpretation are retained as raw values instead of being guessed.
"""
import math
import struct

import numpy as np

from rc2016.formats.container import read_container
from rc2016.common import read, sha

INDEXES = 0x0859863D
LOOK = 0x06EB7EFC
UV0 = 0x16F3BA18
MATERIALS = 0x3250BB80
COLORS = 0x5CBA9DE9
UV1 = 0x6B855EED
MESHES = 0x78D9CBDE
VERTICES = 0xA98BE69B
SKIN_BATCH = 0xC61B1FF5
SKIN_DATA = 0xDCA379A2
SKIN_JOINT_REMAP = 0x5240C82B
JOINTS = 0x15DF9D3B
MODEL_HEADER = 0x283D0383
DEFAULT_POSITION_SHIFT = 12
RCRA_WEIGHTS = 0xCCBAFF15

DAT1_MAGIC = b'1TAD'
PS4_MODEL_TYPE_HASH = 0x882A03DC
VERTEX_STRIDE = 16
UV0_STRIDE = 4
UV0_SECONDARY_STRIDE = 8
INDEX_STRIDE = 2
MESH_STRIDE = 64
SKIN_BATCH_STRIDE = 16
# Bits 0x40 and 0x80 occur in the archive's direct PS4 meshes.  Their
# rendering meaning is unresolved, but the direct geometry reader does not
# consume them; retain them as opaque record bits while rejecting every bit
# not observed in the validated direct family.
# 0x0020 (2026-10-09) is set only on the 15 atmosphere sky/cloud/star/planet meshes. It does not
# change the vertex/index layout: the flagged Novalis dome is the same mesh (2,756 triangles, same
# bounds and UV range) as the unflagged Metropolis dome, flagged and unflagged meshes share one
# vertex buffer in the Kalebo/Pokitaru domes, and stored normals match face normals (median |dot|
# 1.000). Its render meaning (likely sky/draw-at-infinity) is not decoded; the raw flags are kept.
# Evidence: Research/Levels/README.md (Skybox), Research/CorpusPreflight/Flag0020/README.md.
SUPPORTED_MESH_FLAG_MASK = 0x00F1


class UnsupportedModelError(ValueError):
    """The asset is valid enough to identify, but lacks this PS4 geometry layout."""


def _unorm_1010102_vector(value, label):
    """Decode a direct-PS4 10:10:10:2 UNORM word to a signed unit vector.

    The game's compiled vertex shader (shadPS4 cache module 0x59bb3ae4, see
    Research/NativeTranslators/VertexInput20261006/README.md) fetches the +8
    and +12 words of the 16-byte vertex record as three 10-bit UNORM fields
    (u/1023) plus a 2-bit field, then forms 2*x-1 per component. The 2-bit
    field is unused for these vectors and is retained only in the raw word.
    The encoded vectors are unit length to within 0.0015 across 92,353 checked
    vertices; the result is renormalized to remove quantization.
    """
    value &= 0xffffffff
    x, y, z = (((value >> shift) & 0x3ff) / 1023.0 * 2.0 - 1.0 for shift in (0, 10, 20))
    length = math.sqrt(x*x+y*y+z*z)
    if not length or not math.isfinite(length):
        raise ValueError(f'Invalid packed {label}')
    return (x/length, y/length, z/length)


def _normal(value):
    """Decode the vertex-record +8 word (normal).

    This replaces the archived MaxScript's ``unpackNormal [10,10,11] 511``
    reading. That form gave the same direction to within 0.1 degrees on every
    checked vertex, but read bit 30 into Z, which would corrupt the normal if
    the 2-bit field were ever nonzero. ALERT's spherical 10/10/sign decoder
    belongs to other DAT1 model versions and is not applied here.
    """
    return _unorm_1010102_vector(value, 'normal')


def _tangent(value):
    """Decode the vertex-record +12 word (tangent); handedness is not stored.

    The shader transforms it with the same basis as the normal. On seven
    checked models it is orthogonal to the normal (median |dot| ~0.0006) and
    within a median 0.3-0.8 degrees of the UV-derived tangent. Its 2-bit field
    was zero everywhere while UV frames had both orientations, so the
    bitangent sign does not come from this word.
    """
    return _unorm_1010102_vector(value, 'tangent')


def _unorm_1010102_vectors(words, label):
    """Vectorized ``_unorm_1010102_vector`` over a uint32 array: (n, 3) float64.

    Same float64 operations in the same order (no fused multiply-add), so every
    component is bit-identical to the scalar decoder.
    """
    words = words.astype(np.int64)
    x, y, z = (((words >> shift) & 0x3ff) / 1023.0 * 2.0 - 1.0 for shift in (0, 10, 20))
    length = np.sqrt(x*x+y*y+z*z)
    if not np.all(length) or not np.all(np.isfinite(length)):
        raise ValueError(f'Invalid packed {label}')
    return np.stack((x/length, y/length, z/length), axis=1)


def _tuples(values):
    return list(map(tuple, values.tolist()))


def _cstring(data, offset, string_start=0, string_end=None):
    string_end = len(data) if string_end is None else string_end
    if not string_start <= offset < string_end:
        raise ValueError('String pointer outside model')
    end = data.find(b'\0', offset)
    if end < 0 or end >= string_end:
        raise ValueError('Unterminated model string')
    return data[offset:end].decode('ascii')


def _sections(data):
    if len(data) < 16 or data[:4] != DAT1_MAGIC:
        raise UnsupportedModelError('Native adapter requires a direct PS4 1TAD model, not a compressed payload')
    root = read_container(data, 0, {})
    if root['declared_size'] != len(data):
        raise ValueError('Model has bytes outside DAT1 container')
    type_hash = struct.unpack_from('<I', data, 4)[0]
    if type_hash != PS4_MODEL_TYPE_HASH:
        raise UnsupportedModelError(
            f'Unsupported DAT1 model type 0x{type_hash:08X}; expected direct PS4 model 0x{PS4_MODEL_TYPE_HASH:08X}')
    result = {int(s['label_hash'], 16): s for s in root['sections']}
    if len(result) != len(root['sections']):
        raise ValueError('Duplicate DAT1 model section tag')
    spans = sorted((s['offset'], s['offset'] + s['size'], s['label_hash'])
                   for s in root['sections'] if s['size'])
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError('Overlapping model sections')
    table_end = 16 + root['section_count'] * 12
    first_section = min((s['relative_offset'] for s in root['sections']), default=len(data))
    if first_section < table_end:
        raise ValueError('Model string/section pool overlaps the section table')
    # Material and joint pointers are offsets into the local string pool.  A
    # pointer into an arbitrary geometry section is not a valid fallback.
    result['_string_bounds'] = (table_end, first_section)
    return result


def _chunk(data, sections, tag, required=False):
    item = sections.get(tag)
    if item is None:
        if required:
            raise UnsupportedModelError(f'Missing required PS4 model section 0x{tag:08X}')
        return b''
    start, end = int(item['offset']), int(item['offset'] + item['size'])
    if start < 0 or end < start or end > len(data):
        raise ValueError(f'Section 0x{tag:08X} is outside the model')
    return data[start:end]


def _materials(data, section, string_bounds):
    if len(section) % 32:
        raise ValueError('Material table is not a multiple of 32 bytes')
    string_start, string_end = string_bounds
    result=[]
    for offset in range(0, len(section), 32):
        path_ptr, name_ptr = struct.unpack_from('<II', section, offset)
        path, name = (_cstring(data, path_ptr, string_start, string_end),
                      _cstring(data, name_ptr, string_start, string_end))
        result.append(dict(index=len(result), path=path.replace('\\','/'), name=name,
                           raw_record_hex=section[offset:offset+32].hex()))
    return result


def _looks(section, mesh_count):
    if not section:
        return [dict(index=0, source='implicit_all_meshes',
                     lods=[dict(index=0, start=0, count=mesh_count,
                                meshes=list(range(mesh_count)), implicit=True)])]
    # This PS4 title uses four <HH> slots per look. RCRA PC expanded this to eight.
    if len(section) % 16:
        raise ValueError('PS4 look table is not a multiple of four LOD records')
    result=[]
    for look_index, base in enumerate(range(0, len(section), 16)):
        lods=[]
        for lod_index in range(4):
            start,count=struct.unpack_from('<HH',section,base+lod_index*4)
            if start+count > mesh_count:
                raise ValueError('Look/LOD references mesh records outside the model')
            lods.append(dict(index=lod_index,start=start,count=count,meshes=list(range(start,start+count))))
        result.append(dict(index=look_index,lods=lods))
    return result


def _selected_meshes(looks, mesh_count, mode, look_index=0):
    if look_index < 0 or look_index >= len(looks):
        raise ValueError(f'Look index {look_index} is outside the model look table')
    active=[x for x in looks[look_index]['lods'] if x['count']]
    if mode == 'All':
        selected={m for look in looks for level in look['lods'] for m in level['meshes']}
        # Keep an unreferenced mesh visible in the explicit All diagnostic;
        # Highest/Lowest remain faithful to their selected LOD only.
        selected.update(range(mesh_count))
    elif mode == 'Highest':
        selected=set(active[0]['meshes']) if active else set()
    elif mode == 'Lowest':
        selected=set(active[-1]['meshes']) if active else set()
    else:
        raise ValueError('ImportLOD must be All, Highest, or Lowest')
    return sorted(selected)


def _mesh_record(meshes_raw, mesh_index):
    """Decode the PS4 64-byte ``meshInfo`` record from the legacy importer."""
    offset = mesh_index * MESH_STRIDE
    unknown_floats_00 = struct.unpack_from('<4f', meshes_raw, offset)
    first_vertex, first_index, index_count = struct.unpack_from('<3I', meshes_raw, offset + 16)
    vertex_count, unknown_u16_30 = struct.unpack_from('<2H', meshes_raw, offset + 28)
    mesh_flags, material_index = struct.unpack_from('<2H', meshes_raw, offset + 32)
    first_skin_batch = struct.unpack_from('<H', meshes_raw, offset + 36)[0]
    skin_batch_count_word = struct.unpack_from('<H', meshes_raw, offset + 38)[0]
    skin_batch_count_byte = meshes_raw[offset + 38]
    skin_batch_adjacent_byte = meshes_raw[offset + 39]
    unknown_u16_40 = struct.unpack_from('<H', meshes_raw, offset + 40)[0]
    unknown_u8_42, vertex_info_count = struct.unpack_from('<2B', meshes_raw, offset + 42)
    poly_element_index, vertex_info_offset = struct.unpack_from('<2H', meshes_raw, offset + 44)
    tail_floats = struct.unpack_from('<3f', meshes_raw, offset + 48)
    unknown_u32_60 = struct.unpack_from('<I', meshes_raw, offset + 60)[0]
    if not all(math.isfinite(value) for value in (*unknown_floats_00, *tail_floats)):
        raise ValueError(f'Mesh {mesh_index} has non-finite unknown/auxiliary fields')
    if mesh_flags & ~SUPPORTED_MESH_FLAG_MASK:
        raise UnsupportedModelError(
            f'Mesh {mesh_index} uses unsupported PS4 mesh flags 0x{mesh_flags:04X}')
    return dict(index=mesh_index, unknown_floats_00=list(unknown_floats_00), first_vertex=first_vertex,
                first_index=first_index, index_count=index_count,
                vertex_count=vertex_count, unknown_u16_30=unknown_u16_30,
                flags=mesh_flags, material_index=material_index,
                first_skin_batch=first_skin_batch, skin_batch_count=skin_batch_count_word,
                skin_batch_count_word=skin_batch_count_word,
                skin_batch_count_byte=skin_batch_count_byte,
                skin_batch_adjacent_byte=skin_batch_adjacent_byte,
                unknown_u16_40=unknown_u16_40, unknown_u8_42=unknown_u8_42,
                vertex_info_count=vertex_info_count, poly_element_index=poly_element_index,
                vertex_info_offset=vertex_info_offset, auxiliary_floats=list(tail_floats),
                unknown_u32_60=unknown_u32_60,
                raw_record_hex=meshes_raw[offset:offset + MESH_STRIDE].hex())


def _semantic_evidence(uv_record_stride):
    """Separate storage integrity from interpretation confidence."""
    if uv_record_stride == UV0_STRIDE:
        uv = dict(record_stride_bytes=uv_record_stride,
                  storage_integrity='source_cardinality_and_binary16_decode_validated',
                  primary_pair='UV0 pair 0 with V flip',
                  semantic_confidence='validated_against_matching_fbx_controls',
                  secondary_pair='not_present',
                  secondary_semantic_confidence='not_applicable')
    else:
        uv = dict(record_stride_bytes=uv_record_stride,
                  storage_integrity='source_cardinality_and_binary16_decode_validated; secondary_uint16_words_retained',
                  primary_pair='pair 0 candidate only; V flip is a structural preview convention',
                  semantic_confidence='unproven_by_matching_fbx_controls',
                  secondary_pair='opaque_uint16_pair_retained_unassigned',
                  secondary_semantic_confidence='unproven; not assigned as UV1')
    return dict(uv0=uv,
                normals=dict(interpretation='direct_PS4_legacy_candidate',
                             semantic_confidence='unproven; FBX_corner_normals_are_regenerated'),
                tangents=dict(storage_integrity='packed_uint32_retained_exactly',
                              semantic_confidence='unsupported_interpretation'))


def blender_safe_triangles(positions, triangles):
    """Split source triangles into those Blender can hold and degenerate ones.

    Triangles with a repeated index, or whose (unscaled source) positions span zero
    area, crash Blender 5.0's custom-normal setter (mesh_normals_corner_custom_set)
    or are rewritten on save/reopen. They cover no surface, so the importer leaves
    them out; the source index buffer stays untouched and every dropped triangle is
    returned with its source triangle index for the reports. Valid meshes come back
    unchanged.
    """
    kept, dropped = [], []
    for index, tri in enumerate(triangles):
        tri = tuple(int(x) for x in tri)
        if len(set(tri)) < 3:
            dropped.append(dict(source_triangle_index=index, indices=list(tri), reason='repeated_index'))
            continue
        a, b, c = (positions[x] for x in tri)
        u = [float(b[k]) - float(a[k]) for k in range(3)]
        v = [float(c[k]) - float(a[k]) for k in range(3)]
        cross = (u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0])
        if not math.sqrt(sum(x*x for x in cross)) > 1e-12:
            dropped.append(dict(source_triangle_index=index, indices=list(tri), reason='zero_area'))
            continue
        kept.append(tri)
    return kept, dropped


def decode_model(path, lod='Highest', look=0, expected_sha256=None, preserve_source_positions=False,
                 data=None, as_arrays=False):
    """Decode a direct PS4 model file (or its ``data`` bytes; ``path`` is then only a label).

    ``as_arrays`` gives each mesh's positions, normals, tangents, packed_tangents, uv0 and
    triangles as numpy arrays instead of lists, for previews that upload them as-is.
    """
    data=read(path) if data is None else bytes(data)
    source_sha256 = sha(data)
    if expected_sha256 and source_sha256.lower() != expected_sha256.lower():
        raise ValueError('Native model source hash does not match expected descriptor')
    sections=_sections(data)
    vertices=_chunk(data,sections,VERTICES,True)
    uv0=_chunk(data,sections,UV0,True)
    indexes=_chunk(data,sections,INDEXES,True)
    meshes_raw=_chunk(data,sections,MESHES,True)
    material_section = _chunk(data, sections, MATERIALS)
    materials=_materials(data, material_section, sections['_string_bounds'])
    if len(vertices)%VERTEX_STRIDE or len(indexes)%INDEX_STRIDE or len(meshes_raw)%MESH_STRIDE:
        raise ValueError('Misaligned core geometry section')
    for tag, alignment in ((VERTICES, VERTEX_STRIDE), (UV0, UV0_STRIDE),
                           (INDEXES, INDEX_STRIDE), (MESHES, MESH_STRIDE)):
        if sections[tag]['relative_offset'] % alignment:
            raise ValueError(f'Section 0x{tag:08X} has an unsupported alignment')
    vertex_count=len(vertices)//VERTEX_STRIDE
    if not vertex_count:
        raise ValueError('Position section contains no vertices')
    if len(uv0) not in (vertex_count*UV0_STRIDE, vertex_count*UV0_SECONDARY_STRIDE):
        raise ValueError('UV0 and position vertex counts differ')
    uv0_bytes_per_vertex=len(uv0)//vertex_count
    position_shift,position_shift_basis=_position_shift(_chunk(data,sections,MODEL_HEADER))
    divisor=float(1<<position_shift)
    # Vertex record: int16 x,y,z,w | +8 packed normal | +12 packed tangent (see _normal/_tangent).
    # Vectorized; values are bit-identical to the former per-vertex struct/math loop.
    records=np.frombuffer(vertices,dtype=np.dtype([('xyzw','<i2',4),('normal','<u4'),('tangent','<u4')]))
    source_xyz=records['xyzw'][:,:3]
    positions=source_xyz.astype(np.float64)/divisor
    normals=_unorm_1010102_vectors(records['normal'],'normal')
    # Keep the exact uint32 (including its unused 2-bit field) beside the
    # decoded tangent so the source bits stay auditable.
    tangents=records['tangent'].astype(np.uint32)
    tangent_vectors=_unorm_1010102_vectors(tangents,'tangent')
    uv_words=np.frombuffer(uv0,dtype='<u2').reshape(vertex_count,uv0_bytes_per_vertex//2)
    uv_pairs=uv_words[:,:2].copy().view('<f2').astype(np.float64)
    if not np.all(np.isfinite(uv_pairs)):
        raise ValueError('Non-finite half-float UV0')
    uvs=np.stack((uv_pairs[:,0],1.0-uv_pairs[:,1]),axis=1)
    uv0_secondary_packed=uv_words[:,2:4] if uv0_bytes_per_vertex==UV0_SECONDARY_STRIDE else None
    colors_raw=_chunk(data,sections,COLORS)
    colors=None
    if colors_raw:
        if len(colors_raw)!=vertex_count*4:
            raise ValueError('Vertex color count does not match positions')
        colors=_tuples(np.frombuffer(colors_raw,dtype=np.uint8).reshape(vertex_count,4)/255.0)
    uv1_raw=_chunk(data,sections,UV1)
    uv1=None
    if uv1_raw:
        if len(uv1_raw)!=vertex_count*4:
            raise ValueError('UV1 count does not match positions')
        uv1_words=np.frombuffer(uv1_raw,dtype='<i2').reshape(vertex_count,2)
        uv1=_tuples(np.stack((uv1_words[:,0]/32768.0,1.0-uv1_words[:,1]/32768.0),axis=1))
    if not as_arrays:
        source_xyz=_tuples(source_xyz) if preserve_source_positions else None
        positions,normals,tangent_vectors,uvs=(_tuples(a) for a in (positions,normals,tangent_vectors,uvs))
        tangents=tangents.tolist()
        uv0_secondary_packed=_tuples(uv0_secondary_packed) if uv0_secondary_packed is not None else None

    mesh_count=len(meshes_raw)//MESH_STRIDE
    looks=_looks(_chunk(data,sections,LOOK),mesh_count)
    membership={i:[] for i in range(mesh_count)}
    for look_row in looks:
        for level in look_row['lods']:
            for index in level['meshes']:
                membership[index].append(dict(look=look_row['index'],lod=level['index']))
    selected=_selected_meshes(looks,mesh_count,lod,look)
    skin_batches=_chunk(data,sections,SKIN_BATCH)
    skin=_chunk(data,sections,SKIN_DATA)
    if RCRA_WEIGHTS in sections:
        raise UnsupportedModelError(
            'RCRA 0xCCBAFF15 weights are a different DAT1 family; PS4 direct models require 0xDCA379A2 batches')
    if skin and not skin_batches:
        raise UnsupportedModelError('PS4 skin data exists without its 0xC61B1FF5 batch table')
    if skin_batches and len(skin_batches)%SKIN_BATCH_STRIDE:
        raise ValueError('Skin batch section is not a multiple of 16 bytes')
    skin_batch_counts=_skin_batch_counts(meshes_raw,mesh_count,len(skin_batches)//SKIN_BATCH_STRIDE,selected)
    joint_palettes=_skin_joint_palettes(skin_batches,_chunk(data,sections,SKIN_JOINT_REMAP))
    joint_count=len(_chunk(data,sections,JOINTS) or b'')//16
    result=[]
    for mesh_index in selected:
        mesh_record = _mesh_record(meshes_raw, mesh_index)
        resolved_skin_count, skin_count_source = skin_batch_counts[mesh_index]
        mesh_record['skin_batch_count'] = resolved_skin_count
        mesh_record['skin_batch_count_source'] = skin_count_source
        first_vertex=mesh_record['first_vertex']; first_index=mesh_record['first_index']
        index_count=mesh_record['index_count']; local_count=mesh_record['vertex_count']
        material_index=mesh_record['material_index']; mesh_flags=mesh_record['flags']
        batch_first=mesh_record['first_skin_batch']; batch_count=mesh_record['skin_batch_count']
        if not local_count or index_count%3:
            raise ValueError(f'Mesh {mesh_index} has invalid vertex/index counts')
        if first_vertex+local_count>vertex_count or (first_index+index_count)*INDEX_STRIDE>len(indexes):
            raise ValueError(f'Mesh {mesh_index} ranges exceed geometry buffers')
        if materials and material_index >= len(materials):
            raise ValueError(f'Mesh {mesh_index} references material slot {material_index} outside the material table')
        if not materials and material_index:
            raise UnsupportedModelError(
                f'Mesh {mesh_index} uses material slot {material_index} but the material table is missing')
        raw_indices=np.frombuffer(indexes,dtype='<u2',count=index_count,offset=first_index*2)
        base=0 if mesh_flags&0x10 else first_vertex
        local_indices=raw_indices.astype(np.int64)-base
        if index_count and (local_indices.min()<0 or local_indices.max()>=local_count):
            raise ValueError(f'Mesh {mesh_index} index base/flags are unsupported')
        triangles=local_indices.reshape(-1,3)
        weights=[None]*local_count
        if batch_count:
            if not skin_batches or not skin:
                raise ValueError(f'Mesh {mesh_index} references missing skin data')
            if (batch_first+batch_count)*SKIN_BATCH_STRIDE>len(skin_batches):
                raise ValueError(f'Mesh {mesh_index} skin batch range exceeds section')
            for batch_index in range(batch_first,batch_first+batch_count):
                skin_offset,zero0,zero1,influence_code,count,first=struct.unpack_from('<II4H',skin_batches,batch_index*SKIN_BATCH_STRIDE)
                palette=joint_palettes.get(batch_index)
                if palette is None and zero1:
                    raise ValueError(f'Unsupported nonzero skin batch fields at {batch_index}')
                # Without a palette, a nonzero u32 at +4 is a joint-id base added to each u8 id (NPC form).
                id_base=zero0 if palette is None else 0
                influences,stride=_influence_layout(influence_code)
                packed_count=influences
                if first+count>local_count or skin_offset+count*stride>len(skin):
                    raise ValueError(f'Skin batch {batch_index} exceeds mesh/skin range')
                for i in range(count):
                    skin_record=skin[skin_offset+i*stride:skin_offset+(i+1)*stride]
                    ids=list(skin_record[:influences])
                    raw=list(skin_record[packed_count:packed_count+influences])
                    if palette is not None:
                        if max(ids)>=len(palette):
                            raise ValueError(f'Skin batch {batch_index} local joint id exceeds its remap palette')
                        ids=[palette[x] for x in ids]
                    elif id_base:
                        ids=[x+id_base for x in ids]
                        if max(ids)>=joint_count:
                            raise ValueError(f'Skin batch {batch_index} joint id base exceeds the skeleton')
                    values=[1.0] if influences==1 else ([v/sum(raw) for v in raw] if sum(raw) else [])
                    if not values:
                        raise ValueError(f'Zero-sum skin weights in batch {batch_index}')
                    target=first+i
                    if weights[target] is not None:
                        raise ValueError(f'Overlapping skin batches on mesh {mesh_index} vertex {target}')
                    weights[target]=list(zip(ids,values))
        unweighted=sum(x is None for x in weights)
        if batch_count and unweighted:
            raise ValueError(f'Mesh {mesh_index} skin batches leave {unweighted} vertices without weights')
        mesh_result=dict(mesh_record)
        mesh_result.update(
            semantic_evidence=_semantic_evidence(uv0_bytes_per_vertex),
            triangles=triangles if as_arrays else triangles.tolist(),
            positions=positions[first_vertex:first_vertex+local_count],
            normals=normals[first_vertex:first_vertex+local_count],
            packed_tangents=tangents[first_vertex:first_vertex+local_count],
            tangents=tangent_vectors[first_vertex:first_vertex+local_count],
            uv0=uvs[first_vertex:first_vertex+local_count],
            uv0_secondary_packed=(uv0_secondary_packed[first_vertex:first_vertex+local_count]
                                  if uv0_secondary_packed is not None else None),
            uv1=uv1[first_vertex:first_vertex+local_count] if uv1 else None,
            colors=colors[first_vertex:first_vertex+local_count] if colors else None,
            weights=weights,unweighted_vertices=unweighted,
            material=materials[material_index] if material_index<len(materials) else None,
            index_base='local' if mesh_flags&0x10 else 'global',
            memberships=membership[mesh_index])
        if preserve_source_positions:
            mesh_result['source_positions_i16']=source_xyz[first_vertex:first_vertex+local_count]
        result.append(mesh_result)
    known={INDEXES,LOOK,UV0,MATERIALS,COLORS,UV1,MESHES,VERTICES,SKIN_BATCH,SKIN_DATA,SKIN_JOINT_REMAP,
           0x15DF9D3B,0xDCC88A19,0x283D0383}
    sections_out={f'0x{k:08X}':v['size'] for k,v in sections.items() if isinstance(k,int)}
    return dict(schema='rcps4-native-model-v2',source=str(path),source_sha256=source_sha256,
        dat1=dict(type_hash=f'0x{PS4_MODEL_TYPE_HASH:08X}',endianness='little',
                  direct_container=True,section_table_count=len(sections_out)),
        source_units=dict(position_fixed_point=f'int16 / 2^{position_shift}',position_shift=position_shift,
                          position_shift_basis=position_shift_basis,source_to_blender=1.0,
                          uv0_decode='first IEEE-754 binary16 pair; V flipped for structural Blender representation',
                          uv0_record_stride=uv0_bytes_per_vertex,
                          uv0_secondary='opaque uint16 pair retained when record stride is 8'),
        uv_semantic_evidence=_semantic_evidence(uv0_bytes_per_vertex),
        packed_normal='Vertex record +8: 10:10:10:2 UNORM, 2*(u/1023)-1 per 10-bit field, normalized; 2-bit field unused. Format from the game vertex shader fetch (shadPS4 cache 0x59bb3ae4); not yet confirmed against a captured draw. ALERT spherical 10/10/sign is a different model-stream candidate',
        tangent='Vertex record +12: 10:10:10:2 UNORM tangent decoded like the normal and kept losslessly as the raw uint32; bitangent sign is not stored in this word. Format from the game vertex shader fetch; not yet confirmed against a captured draw',vertex_count=vertex_count,
        index_count=len(indexes)//2,mesh_record_count=mesh_count,selected_mesh_count=len(result),
        selected_mesh_indices=selected,import_lod=lod,import_look=look,looks=looks,materials=materials,meshes=result,
        section_sizes=sections_out,
        unsupported_sections=[f'0x{k:08X}' for k in sections if isinstance(k,int) and k not in known])


def inspect_native_model(path, lod='Highest', look=0):
    decoded=decode_model(path,lod,look)
    summary={k:v for k,v in decoded.items() if k!='meshes'}
    summary['meshes']=[]
    excluded={'positions','normals','packed_tangents','tangents','uv0','uv0_secondary_packed',
              'uv1','colors','weights','triangles','source_positions_i16'}
    for mesh in decoded['meshes']:
        row={k:v for k,v in mesh.items() if k not in excluded}
        row.update(triangle_count=len(mesh['triangles']),weighted_vertices=mesh['vertex_count']-mesh['unweighted_vertices'])
        summary['meshes'].append(row)
    return summary


def validate_mesh_position_correction(correction, source_sha256, meshes):
    """Validate a deliberately narrow, source-hash-bound native mesh scale profile."""
    if correction is None:
        return None
    if not isinstance(correction,dict) or correction.get('schema')!='ps4-native-mesh-position-correction-v1':
        raise ValueError('Unsupported native mesh position correction profile')
    if correction.get('source_sha256')!=source_sha256:
        raise ValueError('Native mesh position correction source hash mismatch')
    if correction.get('scale')!=2.0:
        raise ValueError('Unsupported native mesh position correction factor')
    expected_count=correction.get('expected_mesh_count')
    if not isinstance(expected_count,int) or expected_count!=len(meshes):
        raise ValueError('Native mesh position correction selected mesh count mismatch')
    expected_indices=correction.get('expected_mesh_indices')
    actual_indices=[int(mesh['index']) for mesh in meshes]
    if expected_indices!=actual_indices:
        raise ValueError('Native mesh position correction subset signature mismatch')
    try:
        bound_bytes=b''.join(bytes.fromhex(mesh['raw_record_hex'])[:16] for mesh in meshes)
    except (KeyError,ValueError) as exc:
        raise ValueError('Native mesh bounds signature cannot be computed') from exc
    actual_signature=sha(bound_bytes)
    if actual_signature!=correction.get('expected_bound_signature_sha256'):
        raise ValueError('Native mesh position correction bound signature mismatch')
    center_tolerance=float(correction.get('center_tolerance',0.0003))
    radius_tolerance=float(correction.get('radius_tolerance',0.003))
    center_errors=[];radius_ratios=[]
    for mesh in meshes:
        points=mesh['positions'];bounds=tuple((min(p[i] for p in points),max(p[i] for p in points)) for i in range(3))
        center=[(lo+hi)*.5 for lo,hi in bounds]
        stored=mesh['unknown_floats_00'];stored_center=stored[:3];stored_radius=float(stored[3])
        error=max(abs(float(stored_center[i])-2.0*center[i]) for i in range(3))
        if error>center_tolerance:
            raise ValueError(f'Native mesh position correction expected 2x bound center at subset {mesh["index"]}')
        radius=max(math.sqrt(sum((2.0*p[i]-stored_center[i])**2 for i in range(3))) for p in points)
        ratio=radius/stored_radius if stored_radius>0 else math.inf
        if not math.isfinite(ratio) or abs(ratio-1.0)>radius_tolerance:
            raise ValueError(f'Native mesh position correction expected 2x bound radius at subset {mesh["index"]}')
        center_errors.append(error);radius_ratios.append(ratio)
    return dict(status='validated',schema=correction['schema'],source_sha256=source_sha256,
        scale=float(correction['scale']),mesh_count=len(meshes),mesh_indices=actual_indices,
        bound_signature_sha256=actual_signature,max_center_error=max(center_errors),
        radius_ratio_min=min(radius_ratios),radius_ratio_max=max(radius_ratios),
        gate='source hash, selected mesh count/order, bound-record signature, 2x AABB-center match, and 2x bound-radius match')


def _influence_layout(code):
    if code not in (0,1,2,3):
        raise ValueError(f'Unsupported skin influence code {code}')
    count=1 << code
    return count,(1 if count==1 else count*2)


def _position_shift(header):
    """Return the per-model position quantization shift (positions = int16 / 2^shift).

    The u32 at byte 32 of model section 0x283D0383 selects the shift. Across all
    6,949 archive models it reproduces the mesh-record bounding spheres (bytes
    0-15 of each 64-byte mesh record) for every model whose bounds give a clean
    power-of-two factor (6,711 of 6,711); observed shifts are 3-12, 12 being
    the old fixed /4096. Blarg Trooper (11) and Telepathipus (10) were the
    visible cases. Evidence: Other models/enm_telepathipus_v2/README.md.
    """
    if not header:
        return DEFAULT_POSITION_SHIFT, 'default_no_model_header'
    if len(header) < 36:
        raise ValueError('Model header 0x283D0383 is too short for the position shift')
    shift = struct.unpack_from('<I', header, 32)[0]
    if not 1 <= shift <= 15:
        raise ValueError(f'Unsupported model position shift {shift}')
    return shift, 'model_header_0x283D0383_u32_at_32'


def _skin_joint_palettes(skin_batches, remap):
    """Decode per-batch u16 joint palettes from section 0x5240C82B.

    ALERT names the section "Model Skin Joint Remap". A batch whose u32 at +4
    is a byte offset into it and whose u16 at +8 is a nonzero entry count
    reads its u8 skin ids as palette indices. Palettes are packed in batch
    order, each starting on the next 16-byte boundary with zero padding, and
    the last one ends at the section end; all five archive models with the
    section (two Telepathipus, three NPCs) satisfy this. Batches with a
    nonzero +4 but a zero count are the palette-less NPC form: decode_model
    adds the +4 value to each u8 id instead, and no palette is built.
    """
    if not remap:
        return {}
    palettes, position = {}, 0
    for batch_index in range(len(skin_batches)//SKIN_BATCH_STRIDE):
        offset, count = struct.unpack_from('<IH', skin_batches, batch_index*SKIN_BATCH_STRIDE+4)
        if not count:
            continue
        end = offset + 2*count
        if offset != position or end > len(remap) or any(remap[end:(end+15)//16*16]):
            raise ValueError(f'Skin joint remap palette for batch {batch_index} is not contiguous')
        palettes[batch_index] = struct.unpack_from(f'<{count}H', remap, offset)
        position = (end+15)//16*16
    if position != len(remap):
        raise ValueError('Skin joint remap section has unclaimed bytes')
    return palettes


def _skin_batch_counts(meshes_raw, mesh_count, batch_record_count, selected_meshes=None):
    """Resolve the ordinary u16 mesh count, or one bounded split-byte form.

    Some source records fail the ordinary u16 range check even though the low
    byte gives the exact distance to the next mesh's first batch. Accept that
    alternate only when the next mesh boundary (or the section end for the
    last mesh) independently proves the count. The adjacent byte remains
    opaque. Batch contents and vertex coverage are still validated later.
    """
    starts = [struct.unpack_from('<H', meshes_raw, i*MESH_STRIDE+36)[0]
              for i in range(mesh_count)]
    resolved = {}
    selected = None if selected_meshes is None else set(selected_meshes)
    for i, first in enumerate(starts):
        count_word = struct.unpack_from('<H', meshes_raw, i*MESH_STRIDE+38)[0]
        if selected is not None and i not in selected:
            resolved[i] = (count_word, 'unselected_mesh_not_checked')
            continue
        if first + count_word <= batch_record_count:
            resolved[i] = (count_word, 'mesh_record_u16')
            continue
        count_byte = meshes_raw[i*MESH_STRIDE+38]
        boundary = starts[i+1] if i+1 < mesh_count else batch_record_count
        if (count_byte == 0 or first + count_byte > batch_record_count or
                boundary < first or boundary - first != count_byte):
            raise ValueError(f'Mesh {i} skin batch range exceeds section and bounded byte-count control failed')
        resolved[i] = (count_byte, 'low_byte_verified_by_next_mesh_boundary')
    return resolved
