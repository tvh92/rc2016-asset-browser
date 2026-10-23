"""Authored direct lights and bounded lightgrid references for level previews.

`parse_light_record` decodes every understood field of a light record (shape byte +0x50, flag byte +0x51,
normalization byte +0x52, aim axis, colour, radius, gobo path). Only shape 0 (point) with normalization byte 0 is
emitted as a light. Flag byte 1 has a captured Veldin join to the native category-0 point-light path
(`validated`); the other flag bytes use the same path as an inferred extension that the level viewer applies only
under the Level atmosphere lighting (docs/levels.md, Authored lights). Spots, projectors, boxes and the other
normalization modes are reported, not guessed. Lightgrid coefficients and native probe-slot allocation remain
unresolved, so this module exposes source references without fabricating runtime probe bindings.
"""
from __future__ import annotations

import math
import struct

from rc2016.common import read
from rc2016.levels.scene import asset_bytes
from rc2016.levels.sky import atmosphere_zone_assets
from rc2016.levels.zone_format import sections
from rc2016.pipeline.lighting import (
    ENVPROBE_PREFIX,
    NOVALIS_LIGHTING_ZONE,
    _decode_ps4_atmosphere_header,
    _decode_lighting_zone_grid_map,
    _lightgrid_probe_assignment,
)

LIGHT_INDEX = 0x97FF6EB5
DATA_HEAP = 0x06ABCAB2
SUPPORTED_POINT_SOURCE_TYPE = 1       # the flag byte (+0x51) value with a captured Veldin join
SUPPORTED_POINT_RAW_BYTE_0X50 = 0     # shape byte (+0x50): point
SUPPORTED_NORMALIZATION = 0
MIN_LIGHT_RECORD_SIZE = 0x98
SHAPE_NAMES = {0: 'point', 1: 'spot', 2: 'projector', 3: 'box', 4: 'box', 5: 'box'}


def parse_light_record(record):
    """Decode every understood field of one 0x97FF6EB5 light record (at least 0x98 bytes), applying no semantics.

    `shape` is byte +0x50 (0 point, 1 spot, 2 gobo projector, 3 to 5 box); `flags` is byte +0x51 (a bit set whose
    bits 1, 2, 8 and 16 are unresolved); `normalization` is byte +0x52. `axis` is the normalised third matrix row
    (the aim direction of aimed shapes) and `scale` the three row lengths (the extents of box shapes).
    `cone_degrees` (+0x64) and `range` (+0x80, +0x84) are decoded values with unverified meaning. `gobo` is the
    texture path of the optional extension that the u16 at +0x56 announces, else None.
    """
    if len(record) < MIN_LIGHT_RECORD_SIZE:
        raise ValueError(f'light record has {len(record)} bytes, fewer than {MIN_LIGHT_RECORD_SIZE}')
    matrix = struct.unpack_from('<16f', record, 0x00)
    rows = [matrix[i:i + 3] for i in (0, 4, 8)]
    scale = [math.sqrt(sum(v * v for v in row)) for row in rows]
    axis = [v / scale[2] for v in rows[2]] if scale[2] > 0.0 and math.isfinite(scale[2]) else [0.0, 0.0, 0.0]
    gobo = None
    extension = struct.unpack_from('<H', record, 0x56)[0]
    if MIN_LIGHT_RECORD_SIZE <= extension < len(record):
        gobo = record[extension:].split(b'\0', 1)[0].decode('ascii', 'replace')
    return dict(
        transform=[list(matrix[i:i + 4]) for i in range(0, 16, 4)],
        position=list(struct.unpack_from('<3f', record, 0x30)), axis=axis, scale=scale,
        source_id=struct.unpack_from('<Q', record, 0x48)[0], shape=record[0x50],
        shape_name=SHAPE_NAMES.get(record[0x50], 'unknown'), flags=record[0x51], normalization=record[0x52],
        raw_dword_0x50=struct.unpack_from('<I', record, 0x50)[0], byte_0x54=record[0x54],
        colour=list(struct.unpack_from('<3f', record, 0x58)), cone_degrees=struct.unpack_from('<f', record, 0x64)[0],
        radius=struct.unpack_from('<f', record, 0x68)[0], intensity_scale=struct.unpack_from('<f', record, 0x74)[0],
        range=list(struct.unpack_from('<2f', record, 0x80)), link_id=struct.unpack_from('<Q', record, 0x90)[0],
        gobo=gobo, size=len(record))


def _light_radius(source_rgb, normalization, explicit_radius):
    """Return the Veldin-validated category-0 radius, or None if unsupported."""
    if normalization != SUPPORTED_NORMALIZATION:
        return None, 'unsupported_normalization'
    if math.isfinite(explicit_radius) and explicit_radius > 0.0:
        return explicit_radius, 'positive_source_radius_0x68'
    # Match the native float32 source order used by the capture control:
    # three weighted multiplies, two rounded additions, sqrt, then *2.
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    luminance = f32(f32(source_rgb[0] * f32(0.2126)) + f32(source_rgb[1] * f32(0.7152)))
    luminance = f32(luminance + f32(source_rgb[2] * f32(0.0722)))
    radius = f32(f32(math.sqrt(max(f32(0.0), luminance))) * f32(2.0))
    if not math.isfinite(radius) or radius <= 0.0:
        return None, 'nonpositive_or_nonfinite_derived_radius'
    return radius, 'unnormalized_luminance_radius'


def _decode_zone_lights(data, source_path):
    """Decode indexed 0x97FF6EB5 records; only point shapes become lights, and `validated` marks the
    flag byte with a Veldin capture join (the rest is an inferred extension for the Level atmosphere lighting)."""
    table = sections(data)
    if LIGHT_INDEX not in table or DATA_HEAP not in table:
        return [], []
    index_offset, index_size = table[LIGHT_INDEX]
    heap_offset, heap_size = table[DATA_HEAP]
    if index_size % 4:
        raise ValueError(f'{source_path}: light index size is not a multiple of four')
    offsets = struct.unpack_from(f'<{index_size // 4}I', data, index_offset)
    heap_end = heap_offset + heap_size
    sorted_offsets = sorted(offsets)
    if len(set(sorted_offsets)) != len(sorted_offsets):
        raise ValueError(f'{source_path}: light index contains duplicate heap offsets')
    next_offset = {offset: sorted_offsets[i + 1] if i + 1 < len(sorted_offsets) else heap_size
                   for i, offset in enumerate(sorted_offsets)}
    lights, skipped = [], []
    for ordinal, relative in enumerate(offsets):
        start = heap_offset + relative
        if relative + 0x48 > heap_size or start + 0x48 > heap_end:
            raise ValueError(f'{source_path}: light record {ordinal} header is outside 0x06ABCAB2')
        record_size = struct.unpack_from('<I', data, start + 0x44)[0]
        if (record_size < MIN_LIGHT_RECORD_SIZE or relative + record_size > heap_size
                or relative + record_size > next_offset[relative]):
            raise ValueError(f'{source_path}: light record {ordinal} has invalid size {record_size}')
        record = data[start:start + record_size]
        if len(record) != record_size:
            raise ValueError(f'{source_path}: light record {ordinal} is truncated')

        raw_flags = struct.unpack_from('<I', record, 0x50)[0]
        raw_byte_0x50 = record[0x50]
        source_type = record[0x51]
        normalization = record[0x52]
        source_id = struct.unpack_from('<Q', record, 0x48)[0]
        if raw_byte_0x50 != SUPPORTED_POINT_RAW_BYTE_0X50:
            skipped.append(dict(source_path=source_path, ordinal=ordinal, source_id=f'0x{source_id:016x}',
                                reason='unsupported_light_shape', shape=SHAPE_NAMES.get(raw_byte_0x50, 'unknown'),
                                source_type=source_type, raw_byte_0x50=raw_byte_0x50, raw_flags=raw_flags,
                                normalization=normalization))
            continue

        position = struct.unpack_from('<3f', record, 0x30)
        source_rgb = struct.unpack_from('<3f', record, 0x58)
        explicit_radius = struct.unpack_from('<f', record, 0x68)[0]
        transform = struct.unpack_from('<16f', record, 0x00)
        if not all(math.isfinite(v) for v in (*position, *source_rgb, *transform)) or any(v < 0 for v in source_rgb):
            skipped.append(dict(source_path=source_path, ordinal=ordinal, source_id=f'0x{source_id:016x}',
                                reason='nonfinite_or_negative_source_values', source_type=source_type,
                                raw_flags=raw_flags, normalization=normalization))
            continue
        radius, radius_rule = _light_radius(source_rgb, normalization, explicit_radius)
        if radius is None:
            skipped.append(dict(source_path=source_path, ordinal=ordinal, source_id=f'0x{source_id:016x}',
                                reason=radius_rule, source_type=source_type, raw_flags=raw_flags,
                                normalization=normalization))
            continue
        try:
            inverse_radius = struct.unpack('<f', struct.pack('<f', 1.0 / radius))[0]
        except OverflowError:
            inverse_radius = math.inf
        if not math.isfinite(inverse_radius) or inverse_radius <= 0.0:
            skipped.append(dict(source_path=source_path, ordinal=ordinal, source_id=f'0x{source_id:016x}',
                                reason='nonfinite_or_nonpositive_inverse_radius', source_type=source_type,
                                raw_byte_0x50=raw_byte_0x50, raw_flags=raw_flags, normalization=normalization))
            continue
        lights.append(dict(kind='point', position=list(position), radius=radius, colour=list(source_rgb),
                           inverse_radius=inverse_radius,
                           transform=[list(transform[i:i + 4]) for i in range(0, 16, 4)],
                           source_path=source_path, source_ordinal=ordinal, source_id=f'0x{source_id:016x}',
                           source_type=source_type, raw_byte_0x50=raw_byte_0x50,
                           normalization=normalization, raw_flags=raw_flags,
                           validated=source_type == SUPPORTED_POINT_SOURCE_TYPE,
                           source_radius=explicit_radius, source_record_size=record_size,
                           source_extension_size=record_size - MIN_LIGHT_RECORD_SIZE,
                           radius_rule=radius_rule))
    return lights, skipped


SPOT_SHAPE = 1
# Decoded from the game's spot constructor (eboot function 0x1852aa0, Research/2026-10-23_spot-light-constructor): the
# pair at +0x80 and +0x84 is the inner and outer HALF angle in degrees. The engine clamps the inner angle to 0..88,
# lifts the outer to at least inner + 1 and caps it at 89 (stored in radians), then derives the cone terms
# 1/(cos inner - cos outer) and -cos outer/(cos inner - cos outer) of the cone factor saturate(dot(axis, to pixel)*f0 + f4)
# (function 0x1854040). The float at +0x88, clamped to 0.1..10 (2.5 in 99.5 % of the spots), is stored beside them as the
# cone exponent. The pixel shader that consumes these terms is not in the archive, so how the exponent enters the lit
# cone (and the exact curve) is not decoded.
SPOT_CONE_OFFSET = 0x80
SPOT_EXPONENT_OFFSET = 0x88
SPOT_MAX_INNER_DEGREES = 88.0
SPOT_MAX_OUTER_DEGREES = 89.0
SPOT_MIN_GAP_DEGREES = 1.0
SPOT_EXPONENT_RANGE = (0.1, 10.0)


def spot_half_angles(inner, outer):
    """The game's inner and outer half angles in degrees from the raw pair at +0x80 and +0x84 (eboot function 0x1852aa0)."""
    inner = min(max(inner, 0.0), SPOT_MAX_INNER_DEGREES)
    outer = max(inner + SPOT_MIN_GAP_DEGREES, outer)
    return inner, min(outer, SPOT_MAX_OUTER_DEGREES)


def _decode_zone_spots(data, source_path):
    """The spot records (shape byte 1, normalization 0) of one zone as lights of kind 'spot': a point light's fields
    plus ``axis`` (the unit aim direction, matrix row 2, native Y-up), ``half_angle_degrees`` (the inner and outer half
    angles at +0x80 and +0x84 after the engine's clamps, decoded) and ``exponent`` (+0x88, clamped). Records that are not usable spots are not returned; the point decoder has
    already reported them as undrawn shapes."""
    table = sections(data)
    if LIGHT_INDEX not in table or DATA_HEAP not in table:
        return []
    index_offset, index_size = table[LIGHT_INDEX]
    heap_offset, heap_size = table[DATA_HEAP]
    spots = []
    for ordinal, relative in enumerate(struct.unpack_from(f'<{index_size // 4}I', data, index_offset)):
        start = heap_offset + relative
        if relative + 0x48 > heap_size:
            continue
        size = struct.unpack_from('<I', data, start + 0x44)[0]
        if size < MIN_LIGHT_RECORD_SIZE or relative + size > heap_size:
            continue
        light = parse_light_record(data[start:start + size])
        raw_inner, raw_outer = struct.unpack_from('<2f', data, start + SPOT_CONE_OFFSET)
        raw_exponent = struct.unpack_from('<f', data, start + SPOT_EXPONENT_OFFSET)[0]
        if light['shape'] != SPOT_SHAPE or light['normalization'] != SUPPORTED_NORMALIZATION:
            continue
        values = (*light['position'], *light['colour'], *light['axis'], raw_inner, raw_outer, raw_exponent)
        if not all(math.isfinite(v) for v in values) or any(v < 0 for v in light['colour']):
            continue
        inner, outer = spot_half_angles(raw_inner, raw_outer)
        exponent = min(max(raw_exponent, SPOT_EXPONENT_RANGE[0]), SPOT_EXPONENT_RANGE[1])
        radius, rule = _light_radius(light['colour'], light['normalization'], light['radius'])
        if radius is None or not light['axis'][0] ** 2 + light['axis'][1] ** 2 + light['axis'][2] ** 2 > 0.5:
            continue
        spots.append(dict(kind='spot', position=light['position'], axis=light['axis'], radius=radius, colour=light['colour'],
                          inverse_radius=struct.unpack('<f', struct.pack('<f', 1.0 / radius))[0],
                          half_angle_degrees=[inner, outer], exponent=exponent, transform=light['transform'],
                          source_path=source_path, source_ordinal=ordinal, source_id=f"0x{light['source_id']:016x}",
                          source_type=light['flags'], raw_byte_0x50=SPOT_SHAPE, normalization=light['normalization'],
                          raw_flags=struct.unpack_from('<I', data, start + 0x50)[0], validated=False,
                          source_radius=light['radius'], source_record_size=size,
                          source_extension_size=size - MIN_LIGHT_RECORD_SIZE, radius_rule=rule))
    return spots


def _novalis_grid_probes(archive, lighting_zone_path):
    """Expose source grid/probe references for the selected Novalis lighting zone only."""
    zone_data = asset_bytes(archive, lighting_zone_path)
    grid_map = _decode_lighting_zone_grid_map(zone_data, archive.resolve)
    grids = []
    unsupported = 0
    for row in grid_map['grids']:
        entry = archive.entries[row['asset_id']]
        try:
            assignment = _lightgrid_probe_assignment(archive, entry)
        except ValueError:
            decoded = read(archive.decode(row['asset_id']))
            grid_sections = sections(decoded)
            if (0x96D77BBD in grid_sections or 0x9989BB49 not in grid_sections
                    or grid_sections[0x9989BB49][1] != 4096 * 24):
                raise
            unsupported += 1
            grids.append(dict(index=row['index'], path=row['path'], coordinates=row['coordinates'],
                              asset_id=row['asset_id'], status='tableless_default_selector',
                              source_default_global_selector=255,
                              runtime_probe_contents='unobserved', runtime_probe_slots='unobserved'))
            continue
        paths = assignment['probe_paths']
        if any(not path.startswith(ENVPROBE_PREFIX) for path in paths):
            raise ValueError(f"{row['path']}: decoded probe path escaped the supported Novalis namespace")
        grids.append(dict(index=row['index'], path=row['path'], coordinates=row['coordinates'],
                          asset_id=row['asset_id'], status='source_paths_and_selector_bytes_decoded',
                          probe_paths=paths, selector=assignment['probe_selector'],
                          runtime_probe_slots='unobserved', blend_direction='unobserved',
                          coefficients='unresolved'))
    return dict(status='source_probe_references_only', grid_count=len(grids),
                grids=grids, tableless_grid_count=unsupported,
                limitations=['Native probe-slot allocation and blend direction are unobserved.',
                             'Lightgrid coefficients are unresolved; no probe texture or default slot is fabricated.'])


def _authored_sun_note(archive, zone_paths):
    """Return a source-only sun candidate from the existing sky atmosphere decoder."""
    atmosphere_paths = set()
    for zone_path in zone_paths:
        if 'atmosphere' not in zone_path.lower() or 'imposter' in zone_path.lower():
            continue
        atmospheres, _captures = atmosphere_zone_assets(asset_bytes(archive, zone_path))
        atmosphere_paths.update(atmospheres)
    if not atmosphere_paths:
        return None
    if len(atmosphere_paths) != 1:
        return dict(status='multiple_authored_atmospheres', paths=sorted(atmosphere_paths),
                    runtime_global_light='unobserved')
    path = atmosphere_paths.pop()
    ids = archive.resolve(path)
    if len(ids) != 1:
        return dict(status='atmosphere_path_not_unique', path=path, match_count=len(ids),
                    runtime_global_light='unobserved')
    data = asset_bytes(archive, path)
    header = _decode_ps4_atmosphere_header(data)
    return dict(status='authored_sun_direction_decoded_runtime_global_unobserved', path=path,
                ray_direction_game_xyz=header['sun_ray_direction_game_xyz'],
                surface_irradiance_rgb_candidate=header['atmosphere_sun_candidate']['surface_irradiance_rgb_candidate'],
                candidate_evidence=header['atmosphere_sun_candidate']['evidence'],
                runtime_global_light='unobserved')


def load_scene_lighting(archive, zone_paths):
    """Load the selected level's supported authored points and bounded Novalis probe map.

    Returned point positions are native game-world XYZ (Y-up), matching the level
    instance convention; the viewer applies its presentation/view transform.
    The API does not turn sky candidates into global lights or source probe paths
    into native texture-slot bindings.
    """
    lights, skipped = [], []
    loaded_zones = []
    seen = set()
    drawn_spots = 0
    for original_path in zone_paths:
        path = original_path.replace('\\', '/').lstrip('?').lower()
        if not path.endswith('.zone') or path in seen:
            continue
        seen.add(path)
        ids = archive.resolve(path)
        if len(ids) != 1:
            skipped.append(dict(source_path=path, reason='zone_path_not_unique', match_count=len(ids)))
            continue
        zone_data = asset_bytes(archive, path)
        decoded, rejected = _decode_zone_lights(zone_data, path)
        spots = _decode_zone_spots(zone_data, path)
        decoded = decoded + spots
        lights.extend(decoded)
        drawn_spots += len(spots)
        skipped.extend(rejected)
        if decoded or rejected:
            loaded_zones.append(path)

    probes = dict(status='unsupported_zone', grid_count=0, grids=[], tableless_grid_count=0,
                  limitations=['No native global probe assignment is inferred.'])
    notes = [dict(status='bounded_preview_data', message='Point shapes (byte +0x50 equal to 0) and spots (1, with decoded half angles and an inferred cone curve) with normalization byte 0 are emitted. Flag byte 1 points have the Veldin-joined category-0 path (validated); other flag bytes and all spots are an inferred extension that the level viewer applies only under the Level atmosphere lighting. Projectors, boxes and other normalization modes are reported, not drawn. The raw byte and dword are retained without assigning flag names.'),
             dict(status='runtime_binding_unobserved', message='Per-level runtime light dispatch, shader view-space conversion, global-light selection, and native probe-slot allocation are unobserved.')]
    sun = _authored_sun_note(archive, [p.replace('\\', '/').lstrip('?') for p in zone_paths])
    if sun is not None:
        notes.append(dict(sun, status='authored_sun_candidate'))
    if NOVALIS_LIGHTING_ZONE in seen:
        probes = _novalis_grid_probes(archive, NOVALIS_LIGHTING_ZONE)
    undrawn = {}
    for item in skipped:
        if item.get('reason') == 'unsupported_light_shape':
            undrawn[item['shape']] = undrawn.get(item['shape'], 0) + 1
    if drawn_spots:
        undrawn['spot'] = undrawn.get('spot', 0) - drawn_spots
        undrawn = {shape: count for shape, count in undrawn.items() if count > 0}
    if undrawn:
        notes.append(dict(status='undrawn_light_shapes', counts=undrawn,
                          message='Projector and box lights (and spots with non-finite fields) are decoded (aim axis, extents, gobo) but not drawn: cone profile and falloff are unverified.'))
    if skipped:
        notes.append(dict(status='source_records_skipped', records=skipped))
    cones = dict(cones=[], geometry={}, noise=None, undrawn={}, undrawn_reasons={})
    try:
        from rc2016.levels.effects import load_light_cones
        cones = load_light_cones(archive, zone_paths)
    except Exception as exc:   # the lights stay usable without their beams
        notes.append(dict(status='light_cones_unavailable', message=f'{type(exc).__name__}: {exc}'))
    if cones['undrawn']:
        notes.append(dict(status='undrawn_light_cones', counts=cones['undrawn'], reasons=cones['undrawn_reasons'],
                          message='Light-cone effect placements that are decoded (rc2016.levels.effects) but not drawn.'))
    return dict(lights=lights, probes=probes, notes=notes, source_zones=loaded_zones,
                light_cones=cones['cones'], cone_geometry=cones['geometry'], cone_noise=cones['noise'])
