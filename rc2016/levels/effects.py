"""Visual-effect placements of a `.zone`, the effects' component records, and the light cones among them (docs/levels.md,
"Light beams").

A zone lists the visual effects it places in three sections, decoded here on every zone of the game (5114 placements,
all with the same record shape):

    0x3D5E2FEF  u32[n]   absolute file offsets of NUL-terminated `.visualeffect` paths
    0x927C4EC3  u32[n]   offsets of placement records inside the data heap 0x06ABCAB2
    placement record (0x60 bytes): +0x00 float32[16] 4x4 matrix, row-vector convention (rows 0 to 2 the local axes,
                                    row 3 the position), +0x40 u32 kind (5 in all), +0x44 u32 record size (0x60),
                                    +0x48 float32[4] (1.0 in all 27 Aridia light cones), +0x5C u32 whose low byte is the
                                    index into the path list (the high byte is 0xFF)

A `.visualeffect` is a particle system. Its components are the 272-byte records of section 0xB86CF442:

    +0x3C, +0x40  float32      size pair: the sprite size of a sprite component, 1.0 and 1.0 on all 426 mesh components
    +0x44         u64          asset id of the component's model: the CRC-64 of its path (zone_format.path_crc64),
                               0 for components that draw none; 274 of the 277 effects that name a model have a
                               component with its id
    +0xF4         u32          flag word (0x0C0CC82x for the mesh and the unlisted components, 0x0E0CE82x for sprites)
    +0x104        u8[7]        indices into the parameter table

Parameters are the 16-byte entries of 0xB1F4C248 (four u16 values at +0, +2, +4, +6, 0x7FFF being 1.0 before the scale)
paired entry for entry with 0xD78E007C (u32 hash, u32 index, u32 1, float32 scale at +12 that the u16 values multiply:
1/32767 for a plain value, 3/32767 for a value up to 3). The hash is not a name that can be reproduced.

The light cones are the `gbl_amb_Light_Cone_*` effects. Each draws one cone model through a component with the model's
id, with the additive two-sided material `gbl_amb_Light_Cone_Circular_Med` (flags 0x20D, a captured draw state,
docs/shading.md). The placement matrix aims the cone directly: its local +Z (circular cone, apex at the origin, 10 long)
or -Y (wide hanging cone, 2 long) is the aim axis of the spot light that stands at the same place (all 59 of Quartu's
wide cones lie within 0.6 units of a spot record, with the axis opposite the matrix's +Y row).

Not decoded: the other components (sprites for the flare at the lamp, and records without a model), the particle
update that colours the cone (the game's pixel shader reads the colour from a per-particle buffer), and the effect's
timing. The viewer draws the cone model at scale 1 (the mesh component's size pair), coloured by the spot light that
stands there (rc2016.levels.beams).
"""
from __future__ import annotations

import math
import re
import struct

from rc2016.levels.scene import asset_bytes
from rc2016.levels.zone_format import path_crc64, sections

EFFECT_PATHS = 0x3D5E2FEF
EFFECT_OFFSETS = 0x927C4EC3
DATA_HEAP = 0x06ABCAB2
RECORD_SIZE = 0x60
RECORD_KIND = 5
COMPONENTS = 0xB86CF442
PARAMETER_VALUES = 0xB1F4C248
PARAMETER_SCALES = 0xD78E007C
COMPONENT_SIZE = 0x110
LIGHT_CONE = re.compile(r'light_cone', re.IGNORECASE)
JOIN_DISTANCE = 1.0    # a placement takes the colour of a spot light this close (observed 0.05 to 0.59)


def _cstring(data, offset):
    return data[offset:data.index(b'\0', offset)].decode('ascii', 'replace').replace('\\', '/')


def decode_zone_effects(data, source_path=''):
    """The visual-effect placements of one decoded zone: dicts with ``effect`` (path), ``matrix`` (16 floats), ``position``,
    ``axis`` (the unit local +Z row), ``source_path`` and ``source_ordinal``. A zone without the sections has none; a record
    that does not have the decoded shape is left out."""
    table = sections(data)
    if not all(tag in table for tag in (EFFECT_PATHS, EFFECT_OFFSETS, DATA_HEAP)):
        return []
    path_offset, path_size = table[EFFECT_PATHS]
    index_offset, index_size = table[EFFECT_OFFSETS]
    heap_offset, heap_size = table[DATA_HEAP]
    paths = [_cstring(data, offset) for offset in struct.unpack_from(f'<{path_size // 4}I', data, path_offset)]
    placements = []
    for ordinal, relative in enumerate(struct.unpack_from(f'<{index_size // 4}I', data, index_offset)):
        start = heap_offset + relative
        if relative + RECORD_SIZE > heap_size:
            continue
        matrix = struct.unpack_from('<16f', data, start)
        kind, size = struct.unpack_from('<II', data, start + 0x40)
        index = data[start + 0x5C]
        if (kind != RECORD_KIND or size != RECORD_SIZE or index >= len(paths) or not all(math.isfinite(v) for v in matrix)
                or (matrix[3], matrix[7], matrix[11], matrix[15]) != (0.0, 0.0, 0.0, 1.0)):
            continue
        row = matrix[8:11]
        length = math.sqrt(sum(v * v for v in row))
        placements.append(dict(effect=paths[index], matrix=list(matrix), position=list(matrix[12:15]),
                               axis=[v / length for v in row] if length > 0.0 else [0.0, 0.0, 1.0],
                               source_path=source_path, source_ordinal=ordinal))
    return placements


def decode_effect(data):
    """A decoded `.visualeffect`: ``components`` (dicts with ``asset`` (the u64 model id, 0 for none), ``size`` (the pair at
    +0x3C), ``flags`` (+0xF4) and ``slots`` (the seven parameter indices)) and ``parameters`` (dicts with ``hash``, ``value``
    (four floats: the u16 values times the scale) and ``scale``). An effect without the sections has neither."""
    table = sections(data)
    components, parameters = [], []
    if COMPONENTS in table:
        offset, size = table[COMPONENTS]
        for n in range(size // COMPONENT_SIZE):
            start = offset + n * COMPONENT_SIZE
            components.append(dict(asset=struct.unpack_from('<Q', data, start + 0x44)[0],
                                   size=struct.unpack_from('<2f', data, start + 0x3C),
                                   flags=struct.unpack_from('<I', data, start + 0xF4)[0],
                                   slots=list(struct.unpack_from('<7B', data, start + 0x104))))
    if PARAMETER_VALUES in table and PARAMETER_SCALES in table:
        values, scales = table[PARAMETER_VALUES], table[PARAMETER_SCALES]
        for n in range(min(values[1], scales[1]) // 16):
            raw = struct.unpack_from('<4H', data, values[0] + n * 16)
            parameter_hash, scale = struct.unpack_from('<I', data, scales[0] + n * 16)[0], struct.unpack_from('<f', data, scales[0] + n * 16 + 12)[0]
            parameters.append(dict(hash=parameter_hash, value=[v * scale for v in raw], scale=scale))
    return dict(components=components, parameters=parameters)


def effect_model_path(archive, effect_path):
    """The `.model` path a `.visualeffect` names, or None."""
    data = asset_bytes(archive, effect_path)
    models = [_cstring(data, m.start()) for m in re.finditer(rb'[A-Za-z0-9_][ -~]*\.model\0', data, re.IGNORECASE)]
    return models[0] if models else None


def mesh_scale(effect, model_path):
    """The scale of the effect's mesh component: the size pair of the one component whose asset id is the CRC-64 of
    ``model_path``, when its two values agree; None when there is not exactly one such component."""
    mesh = [c for c in effect['components'] if c['asset'] == path_crc64(model_path)]
    if len(mesh) != 1 or mesh[0]['size'][0] != mesh[0]['size'][1]:
        return None
    return float(mesh[0]['size'][0])


def is_light_cone(effect_path):
    return LIGHT_CONE.search(effect_path.rsplit('/', 1)[-1]) is not None


def cone_frame(positions):
    """(axis, length) of a cone model's vertices: the aim axis (a unit vector pointing from the apex to the base) and the
    extent along it. The axis is the coordinate axis along which the bounding box is least centred on the origin (the
    apex stands at the origin, so the cone lies to one side of it), directed away from the origin."""
    import numpy as np
    low, high = positions.min(axis=0), positions.max(axis=0)
    axis = int(np.argmax(np.abs(low + high)))
    direction = np.zeros(3)
    direction[axis] = 1.0 if high[axis] + low[axis] > 0.0 else -1.0
    return direction, float(high[axis] - low[axis])


def join_spots(placements, spots, distance=JOIN_DISTANCE):
    """Each placement with ``colour`` and ``spot`` (the ordinal of the nearest spot light within ``distance``, else None and
    no colour). The join is by position only: no field links an effect to a light."""
    for placement in placements:
        best, best_distance = None, distance
        for spot in spots:
            gap = math.dist(placement['position'], spot['position'])
            if gap <= best_distance:
                best, best_distance = spot, gap
        placement['spot'] = best['source_ordinal'] if best else None
        placement['colour'] = list(best['colour']) if best else None
    return placements


def load_light_cones(archive, zone_paths, spots):
    """The light cones to draw for the zones of a level.

    ``spots`` are the decoded spot lights (rc2016.levels.scene_lighting). Returns ``cones`` (placements of the cone models,
    each with its ``model`` path, ``scale`` (the mesh component's) and the ``colour`` of the spot light it was joined to; the
    matrix is scaled), ``geometry`` ({model path: {'positions', 'normals' float32 (n, 3), 'indices' uint32, 'axis', 'length'}}
    in model space) and ``undrawn`` ({effect file name: count} of the light-cone placements that are decoded but not drawn,
    with the reason in ``undrawn_reasons``)."""
    import numpy as np
    from rc2016.levels.scene import model_geometry
    cones, geometry, undrawn, reasons, seen, decoded = [], {}, {}, {}, set(), {}

    def skip(placement, reason):
        name = placement['effect'].rsplit('/', 1)[-1]
        undrawn[name] = undrawn.get(name, 0) + 1
        reasons[name] = reason

    for original in zone_paths:
        path = original.replace('\\', '/').lstrip('?').lower()
        if not path.endswith('.zone') or path in seen:
            continue
        seen.add(path)
        if len(archive.resolve(path)) != 1:
            continue
        for placement in decode_zone_effects(asset_bytes(archive, path), path):
            if not is_light_cone(placement['effect']):
                continue
            if placement['effect'] not in decoded:
                model = effect_model_path(archive, placement['effect'])
                scale = mesh_scale(decode_effect(asset_bytes(archive, placement['effect'])), model) if model else None
                decoded[placement['effect']] = (model, scale)
            model, scale = decoded[placement['effect']]
            if model is None or scale is None:
                skip(placement, 'no mesh component with a model of the effect')
                continue
            placement['model'], placement['scale'] = model, scale
            cones.append(placement)
    join_spots(cones, [s for s in spots if s.get('kind') == 'spot'])
    kept = []
    for cone in cones:
        if cone['colour'] is None:
            skip(cone, 'no spot light within the join distance, so no colour')
            continue
        if cone['model'] not in geometry:
            loaded = model_geometry(archive, cone['model'])
            positions = np.asarray(loaded['positions'], np.float32).reshape(-1, 3)
            axis, length = cone_frame(positions)
            geometry[cone['model']] = dict(positions=positions, normals=np.asarray(loaded['normals'], np.float32).reshape(-1, 3),
                                           indices=np.asarray(loaded['indices'], np.uint32), axis=axis, length=length)
        matrix = np.asarray(cone['matrix'], np.float64).reshape(4, 4)
        matrix[:3, :3] *= cone['scale']
        cone['matrix'] = matrix.reshape(-1).tolist()
        kept.append(cone)
    return dict(cones=kept, geometry=geometry, undrawn=undrawn, undrawn_reasons=reasons)


__all__ = ['JOIN_DISTANCE', 'cone_frame', 'decode_effect', 'decode_zone_effects', 'effect_model_path', 'is_light_cone',
           'join_spots', 'load_light_cones', 'mesh_scale']
