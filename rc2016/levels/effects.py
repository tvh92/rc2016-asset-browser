"""Visual-effect placements of a `.zone` and the light cones among them (docs/levels.md, "Visual effects").

A zone lists the visual effects it places in three sections, decoded here on every zone of the game (5114 placements,
all with the same record shape):

    0x3D5E2FEF  u32[n]   absolute file offsets of NUL-terminated `.visualeffect` paths
    0x927C4EC3  u32[n]   offsets of placement records inside the data heap 0x06ABCAB2
    placement record (0x60 bytes): +0x00 float32[16] 4x4 matrix, row-vector convention (rows 0 to 2 the local axes,
                                    row 3 the position), +0x40 u32 kind (5 in all), +0x44 u32 record size (0x60),
                                    +0x48 float32[4] (1.0 in all 27 Aridia light cones), +0x5C u32 whose low byte is the
                                    index into the path list (the high byte is 0xFF)

The light cones are the `gbl_amb_Light_Cone_*` effects. Each is a particle system (sections 0xB86CF442 and others, whose
component records are undecoded) that draws one cone model with the additive two-sided material of
`material/visualeffect/environment/global/ambient/gbl_amb_Light_Cone_Circular_Med` (flags 0x20D, a captured draw state,
docs/shading.md). The placement matrix aims the cone: its local +Z is the aim axis of the spot light that stands at the
same place (26 of Aridia's 27 placements lie within 0.4 units of a spot record, with the axes within a few degrees).

Decoded here: which effects a zone places, where and how they are oriented, and the cone model each light-cone effect
names. Not decoded: the effect's own component records, so its extra scale, offsets, colour and animation, and the
`gbl_Glow_Simp_Soft`-based pixel shader's inputs (the cone colour is read from a particle buffer). The viewer draws the
cone model of the circular effects at the placement's scale, joined to the spot light for its colour (rc2016.levels.beams).
"""
from __future__ import annotations

import math
import re
import struct

from rc2016.levels.scene import asset_bytes
from rc2016.levels.zone_format import sections

EFFECT_PATHS = 0x3D5E2FEF
EFFECT_OFFSETS = 0x927C4EC3
DATA_HEAP = 0x06ABCAB2
RECORD_SIZE = 0x60
RECORD_KIND = 5
LIGHT_CONE = re.compile(r'light_cone', re.IGNORECASE)
# The light-cone models the viewer draws: the circular cone's local +Z is the aim axis, apex at the origin, 10 long and
# 3.75 in radius. The wide hanging cone (local -Y axis, 2 long) is decoded but not drawn: its effect scales it by an
# undecoded component value, so its size is unknown.
DRAWN_CONE_MODELS = ('gbl_amb_light_cone_circular_med.model',)
JOIN_DISTANCE = 1.0    # a placement takes the colour of a spot light this close (observed 0.05 to 0.38 in Aridia)


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


def effect_model_path(archive, effect_path):
    """The `.model` path a `.visualeffect` names, or None."""
    data = asset_bytes(archive, effect_path)
    models = [_cstring(data, m.start()) for m in re.finditer(rb'[A-Za-z0-9_][ -~]*\.model\0', data, re.IGNORECASE)]
    return models[0] if models else None


def is_light_cone(effect_path):
    return LIGHT_CONE.search(effect_path.rsplit('/', 1)[-1]) is not None


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

    ``spots`` are the decoded spot lights (rc2016.levels.scene_lighting). Returns ``cones`` (placements of the drawn cone
    models, each with its ``model`` path and the ``colour`` of the spot light it was joined to), ``geometry`` ({model path:
    {'positions', 'normals' float32 (n, 3), 'indices' uint32} in model space}) and ``undrawn`` ({effect file name: count} of
    the light-cone placements that are decoded but not drawn, with the reason in ``undrawn_reasons``)."""
    import numpy as np
    from rc2016.levels.scene import model_geometry
    cones, geometry, undrawn, reasons, seen = [], {}, {}, {}, set()
    for original in zone_paths:
        path = original.replace('\\', '/').lstrip('?').lower()
        if not path.endswith('.zone') or path in seen:
            continue
        seen.add(path)
        ids = archive.resolve(path)
        if len(ids) != 1:
            continue
        for placement in decode_zone_effects(asset_bytes(archive, path), path):
            if not is_light_cone(placement['effect']):
                continue
            name = placement['effect'].rsplit('/', 1)[-1]
            model = effect_model_path(archive, placement['effect'])
            if model is None or model.rsplit('/', 1)[-1].lower() not in DRAWN_CONE_MODELS:
                undrawn[name] = undrawn.get(name, 0) + 1
                reasons[name] = 'its cone model is scaled by an undecoded effect component'
                continue
            placement['model'] = model
            cones.append(placement)
    join_spots(cones, [s for s in spots if s.get('kind') == 'spot'])
    kept = []
    for cone in cones:
        if cone['colour'] is None:
            name = cone['effect'].rsplit('/', 1)[-1]
            undrawn[name] = undrawn.get(name, 0) + 1
            reasons[name] = 'no spot light within the join distance, so no colour'
            continue
        if cone['model'] not in geometry:
            loaded = model_geometry(archive, cone['model'])
            geometry[cone['model']] = dict(positions=np.asarray(loaded['positions'], np.float32).reshape(-1, 3),
                                           normals=np.asarray(loaded['normals'], np.float32).reshape(-1, 3),
                                           indices=np.asarray(loaded['indices'], np.uint32))
        kept.append(cone)
    return dict(cones=kept, geometry=geometry, undrawn=undrawn, undrawn_reasons=reasons)


__all__ = ['DRAWN_CONE_MODELS', 'JOIN_DISTANCE', 'decode_zone_effects', 'effect_model_path', 'is_light_cone', 'join_spots', 'load_light_cones']
