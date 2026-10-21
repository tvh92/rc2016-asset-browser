"""Level sky: the atmosphere's cloud dome and a clear-sky colour profile (data only, no GL).

A level's atmosphere zone names one `.atmosphere` asset; the asset lists its sky models first
(docs/levels.md, "Skybox"). Dome materials on the `Rk_Sky_Dome` graph are drawn with a port of its
cloud fragment shader 0x8d3c81d0 (`SKY_FRAGMENT` in rc2016/viewer/shading.py); other sky graphs (`_flat` nebula
domes, `rk_sky_matte` planets) are reported, not drawn. The clear sky behind the clouds comes from
the atmosphere scattering tables, which are not decoded; `clear_sky_profile` instead samples the
clear-sky pixels of the reflection capture the atmosphere zone references.
"""
from __future__ import annotations

import hashlib
import math
import re
import struct

from rc2016.formats.archive import decompress
from rc2016.formats.index import references
from rc2016.levels.scene import asset_bytes, model_geometry
from rc2016.viewer.textures import GRAPH_SLOT_SECTION, MATERIAL_SLOT_SECTION, texture_slots

DOME_GRAPH = 'materialgraph/global/rk_sky_dome/rk_sky_dome.materialgraph'
# The graph whose cloud fragment shader 0x8d3c81d0 was read (shadPS4 cache SPIR-V); hash-bound.
DOME_GRAPH_SHA256 = '4637d9a78bc577aae47f4db9b4576909d0fce172c6cf173b3910b7fdcd45278c'
DOME_SHADER_ID = '0x8d3c81d0'
DOME_DWORDS = 23            # constant-buffer dwords 0-22, one-to-one with the graph's table 0
# Graph texture slots (0x1CAFE804 hashes): distortion noise, the two cloud-layer slots, the two star
# slots. Which cloud slot feeds which layer is not decoded; every dome material binds both to one
# texture, and a material that does not is reported instead of drawn.
NOISE_SLOT = 0x49A7866B0D1BFEF3
DOME_SLOTS = (0x49A7866BA1EFA69D, 0x49A7866BA80406E7)
# Engine and vertex-shader inputs the cloud shader reads that are not decoded (presentation values).
PRESENTATION = dict(time=0.0, alpha_multiplier=1.0, alpha_test_threshold=0.0, fog_factor=1.0)
PROFILE_STEP = 6.0          # degrees per clear-sky profile bin, 0-90
PROFILE_MIN_SKY = 0.1       # fraction of clear-sky pixels a bin needs to count
# A blue-dominant probe pixel brighter than this (largest channel, probe units) is not taken as clear sky:
# bright blue pixels near the horizon are haze-tinted cloud and horizon glow, which would lift the median of
# the low bins. Chosen when the probe was still decoded clamped to 1; kept as a deliberate cap now that it is
# decoded in HDR (above 24 degrees it removes under 1% of the blue pixels, so those bins are unchanged).
CLEAR_SKY_MAX = 0.98
# Dome fog and the sky display of the other tonemaps, mirrored in SKY_FRAGMENT (rc2016/viewer/shading.py; tests keep the values in step).
# Inferred or calibrated, not decoded; docs/levels.md ("Sky in the level viewer").
DOME_FOG_DISTANCE = 10000.0   # the dome is treated as a point this far from the camera along the view ray
# The dome fog colour is the geometry's (``rc2016.levels.fog.fog_colour``: one shared inferred factor).
SKY_EXPOSURE = 0.9            # sky exposure before the Khronos Neutral curve of the non-game tonemaps; fitted to one in-game frame
ATMOSPHERE_PATH = re.compile(rb'([\x20-\x7e]+\.atmosphere)\x00')
REFLECTION_PATH = re.compile(rb'([\x20-\x7e]*volumereflections[\x20-\x7e]+\.texture)\x00')


def _string_paths(data, pattern):
    return sorted({m.group(1).decode().replace('\\', '/').lstrip('?/') for m in pattern.finditer(data)})


def _material(archive, path):
    ids = [i for i in archive.resolve(path) if archive.entries[i]['type'] == 'material']
    if len(ids) != 1:
        raise ValueError(f'{path}: expected one material, found {len(ids)}')
    return decompress(archive.bytes(ids[0])[0])[0]


def _graph(archive, path):
    ids = [i for i in archive.resolve(path) if archive.entries[i]['type'] == 'materialgraph']
    if len(ids) != 1:
        raise ValueError(f'{path}: expected one material graph, found {len(ids)}')
    return decompress(archive.bytes(ids[0])[0])[0]


def atmosphere_zone_assets(zone_data):
    """(atmosphere paths, reflection-capture texture paths) named in an atmosphere zone's strings."""
    return _string_paths(zone_data, ATMOSPHERE_PATH), _string_paths(zone_data, REFLECTION_PATH)


def sun_ray(atmosphere_data):
    """Unit sun ray direction (direction of travel) in native Y-up game space."""
    from rc2016.pipeline.lighting import _decode_ps4_atmosphere_header
    return tuple(_decode_ps4_atmosphere_header(atmosphere_data)['sun_ray_direction_game_xyz'])


def sun_colour(atmosphere_data):
    """RGB96 direct-light candidate of an atmosphere (header offset 96): the surface irradiance at normal
    incidence in the units of the reflection captures. Runtime field names are unresolved
    (``atmosphere_sun_candidate`` in ``rc2016.pipeline.lighting``)."""
    from rc2016.pipeline.lighting import _decode_ps4_atmosphere_header
    candidate = _decode_ps4_atmosphere_header(atmosphere_data)['atmosphere_sun_candidate']
    return tuple(candidate['surface_irradiance_rgb_candidate'])


def _header_section(atmosphere_data):
    """Bytes of the atmosphere's 131552-byte parameter header section, or None."""
    from rc2016.formats.container import read_container
    from rc2016.pipeline.lighting import ATMOSPHERE_HEADER
    root = read_container(atmosphere_data, 0, {})
    for section in root['sections']:
        if int(section['label_hash'], 16) == ATMOSPHERE_HEADER:
            return atmosphere_data[section['offset']:section['offset'] + section['size']]
    return None


def fog_profile(atmosphere_data):
    """Authored fog profile of an atmosphere ({'heights', 'densities'}, ``rc2016.levels.fog.read_profile``), or None."""
    from rc2016.levels.fog import read_profile
    header = _header_section(atmosphere_data)
    return read_profile(header) if header is not None else None


def colour_grade(atmosphere_data):
    """The atmosphere's 32x32x32 colour-grade LUT as raw R8G8B8A8 bytes (``rc2016.levels.display.grade_from_header``), or None."""
    from rc2016.levels.display import GRADE_BYTES, GRADE_OFFSET
    header = _header_section(atmosphere_data)
    if header is None or len(header) < GRADE_OFFSET + GRADE_BYTES:
        return None
    return bytes(header[GRADE_OFFSET:GRADE_OFFSET + GRADE_BYTES])


def dome_constants(material_data, graph_data):
    """Constant-buffer dwords 0-22 of a dome material: instance values over graph table-0 defaults."""
    from rc2016.formats.materials import extract_material_parameters
    from rc2016.shaders.families import graph_parameter_tables
    tables, defaults = graph_parameter_tables(graph_data)
    parameters = extract_material_parameters(material_data)
    instance = {row['key_hash']: row for row in parameters.get('parameters', []) + parameters.get('unsupported_parameters', [])}
    values = {}
    for row in tables[0]:
        if row['key_hash'] in instance and instance[row['key_hash']].get('raw_hex'):
            raw = bytes.fromhex(instance[row['key_hash']]['raw_hex'])
            floats = struct.unpack(f'<{len(raw) // 4}f', raw)
        else:
            default = defaults.get(row['key_hash'])
            if default is None:
                continue
            floats = default if isinstance(default, (list, tuple)) else [default]
        for i, value in enumerate(floats):
            values[row['dword'] + i] = float(value)
    missing = [d for d in range(DOME_DWORDS) if d not in values]
    if missing:
        raise ValueError(f'dome material lacks constant dwords {missing}')
    return [values[d] for d in range(DOME_DWORDS)]


def dome_fog_transmittance(sin_elevation, ramps):
    """Fog transmittance T applied to the cloud dome in direction ``sin_elevation`` (sine of the elevation):
    the level's height fog (``rc2016.levels.fog.fog_T`` with the ramps of ``build_ramps`` for the camera's
    height) of the point DOME_FOG_DISTANCE along that ray. CPU reference of ``skyFogTransmittance`` in
    SKY_FRAGMENT. The dome's own vertex shader (the source of the game's T) is not decoded."""
    from rc2016.levels.fog import fog_T
    cos_elevation = math.sqrt(max(0.0, 1.0 - sin_elevation * sin_elevation))
    return fog_T((DOME_FOG_DISTANCE * cos_elevation, DOME_FOG_DISTANCE * sin_elevation, 0.0), (0.0, 0.0, 0.0), ramps)


def _profile_from_faces(faces, step=PROFILE_STEP, min_sky=PROFILE_MIN_SKY):
    """Clear-sky colour per elevation bin from six cube faces (float arrays HxWx3, D3D order +X -X +Y -Y +Z -Z,
    rows top-down). A pixel is clear sky when blue dominates (b > 1.25 r, b > g) and no channel exceeds CLEAR_SKY_MAX;
    each bin is the per-channel median of its clear-sky pixels. Bins with too few such pixels (terrain,
    cloud banks) take the nearest valid bin above them. Returns a list of RGB per bin, or None."""
    import numpy as np
    size = faces[0].shape[0]
    c = (np.arange(size) + 0.5) / (size * 0.5) - 1.0
    s, t = np.meshgrid(c, c)
    elevations, colours = [], []
    for index, face in enumerate(faces):
        if index in (2, 3):
            y, lateral = (1.0 if index == 2 else -1.0) * np.ones_like(s), np.hypot(s, t)
        else:
            y, lateral = -t, np.hypot(1.0, s)
        elevations.append(np.degrees(np.arctan2(y, lateral)).ravel())
        colours.append(face.reshape(-1, 3))
    elevation, colour = np.concatenate(elevations), np.concatenate(colours)
    sky = (colour[:, 2] > 1.25 * colour[:, 0]) & (colour[:, 2] > colour[:, 1]) & (colour.max(axis=1) < CLEAR_SKY_MAX)
    bins = int(round(90.0 / step))
    profile = [None] * bins
    for i in range(bins):
        inside = (elevation >= i * step) & (elevation < (i + 1) * step)
        clear = inside & sky
        if inside.any() and clear.sum() >= min_sky * inside.sum():
            profile[i] = [float(x) for x in np.median(colour[clear], axis=0)]
    if all(p is None for p in profile):
        return None
    for i in range(bins - 2, -1, -1):
        if profile[i] is None:
            profile[i] = profile[i + 1]
    for i in range(1, bins):
        if profile[i] is None:
            profile[i] = profile[i - 1]
    return profile


def clear_sky_profile(archive, texture_path):
    """Clear-sky colour by elevation from a reflection-capture cube (BC6H, six 512x512 faces).

    The cube is decoded in HDR (``rc2016.levels.probe_lighting.probe_faces``, ``rc2016.formats.bc6h``), so
    the profile is in the probe's linear units and nothing is clamped; a block in a BC6H mode the decoder
    lacks falls back to Pillow's value clamped to 1. Needs NumPy (and Pillow for that fallback)."""
    from rc2016.levels.probe_lighting import probe_faces
    faces, _info = probe_faces(archive, texture_path)
    return _profile_from_faces(faces)


NIGHT = re.compile(r'night', re.IGNORECASE)
LIGHTING_STEM = re.compile(r'(lighting|lgt)(_night)?')   # file stems of the zones that hold a region's lightgrid and reflection volumes


def zone_stem(zone_path):
    """Lower-case file stem of a zone path (``atm_lgt`` for ``levels/Veldin/atm_lgt.zone``)."""
    return zone_path.replace('\\', '/').rsplit('/', 1)[-1].rsplit('.', 1)[0].lower()


def is_atmosphere_zone(zone_path):
    """Whether a zone is read for its atmosphere: its stem contains ``atmosphere`` (Novalis ``atmosphere.zone``) or
    starts with ``atm_`` (Veldin ``atm_lgt.zone``, ``atm_lgt_night.zone``); ``_imposter`` twins are not."""
    stem = zone_stem(zone_path)
    return 'imposter' not in stem and ('atmosphere' in stem or stem.startswith('atm_'))


def is_night(path):
    """Whether a zone or atmosphere path names the night variant: ``night`` in the file name (``lgt_night``,
    ``atm_Veldin_Night_Darker``). Only the file name counts, not the folders."""
    return bool(NIGHT.search(path.replace('\\', '/').rsplit('/', 1)[-1]))


def is_lighting_zone(zone_path):
    """Whether a zone is a region's lighting zone (stem ``lighting`` or ``lgt``, with ``_night`` for the night variant; ``_imposter`` twins are not)."""
    return LIGHTING_STEM.fullmatch(zone_stem(zone_path)) is not None


def choose_atmosphere(found):
    """The level's atmosphere from ``found`` = {atmosphere path: [atmosphere zone paths naming it]}.

    One atmosphere is used as is. Several (Veldin names a day and a night one) resolve to the day one: an
    atmosphere is night when its file name or any zone naming it has ``night`` in the file name. Exactly one day
    atmosphere must remain, otherwise this raises ValueError. Returns (atmosphere, is_night)."""
    if len(found) == 1:
        atmosphere = next(iter(found))
        return atmosphere, is_night(atmosphere) or all(is_night(z) for z in found[atmosphere])
    day = sorted(a for a, zones in found.items() if not is_night(a) and not any(is_night(z) for z in zones))
    if len(day) != 1:
        raise ValueError(f'atmosphere zones name {len(found)} atmospheres, {len(day)} of them not night: {sorted(found)}')
    return day[0], False


def lighting_reflection(archive, zone_paths, night=False, position=None):
    """The reflection capture chosen from the volume records of the level's lighting zones, or None:
    {'path', 'id', 'zone', 'candidates'}. Only zones whose night-ness equals ``night`` count (day zones by
    default); ``rc2016.levels.volumes.choose_reflection`` picks among the volumes whose capture resolves to one
    archive asset: the one containing ``position`` (a native Y-up point), else the one covering most of the
    lightgrid. ``candidates`` lists every volume id considered."""
    from rc2016.levels import volumes
    rows, centres = [], []
    for zone in sorted(z for z in zone_paths if is_lighting_zone(z) and is_night(z) == night):
        data = asset_bytes(archive, zone)
        rows.extend(dict(volume, zone=zone) for volume in volumes.reflection_volumes(data))
        centres.extend(volumes.grid_centres(data))
    rows = [r for r in rows if len(archive.resolve(r['path'])) == 1]
    chosen = volumes.choose_reflection(rows, centres, position)
    if chosen is None:
        return None
    return dict(path=chosen['path'], id=chosen['id'], zone=chosen['zone'], candidates=[r['id'] for r in rows],
                candidate_paths=[r['path'] for r in rows])


def level_sky(archive, zone_paths, profile=True, probe_position=None):
    """The sky of a level, from its zone list: {'atmosphere', 'night', 'sun_ray', 'domes', 'unsupported',
    'sun_rgb', 'clear_sky', 'fog' (authored height-fog profile or None), 'grade' (colour-grade LUT bytes or None),
    'reflection', 'reflection_source', 'notes'}, or None when no atmosphere zone names an atmosphere.

    Atmosphere zones are found by name (``is_atmosphere_zone``); several atmospheres resolve to the day one
    (``choose_atmosphere``). The reflection capture is the single one the chosen atmosphere's zones name
    ('reflection_source' is 'atmosphere zone'), else the one chosen from the lighting zones' volume records
    ('lighting volume', ``lighting_reflection``, with ``reflection_volume`` its description; ``probe_position``
    is a native Y-up point that selects the volume containing it), else None.

    Each dome: {'model', 'geometry' (model_geometry), 'spans': [(first triangle, count)], 'material',
    'constants' (23 floats), 'dome_texture', 'noise_texture', 'stars'}. Textures come from the graph's
    slots with the material's slot overrides (NOISE_SLOT, DOME_SLOTS)."""
    found, captures, unnamed = {}, {}, set()
    for zone in zone_paths:
        if not is_atmosphere_zone(zone):
            continue
        names, reflections = atmosphere_zone_assets(asset_bytes(archive, zone))
        if not names:
            unnamed.update(reflections)   # a capture in a zone that names no atmosphere belongs to the level
        for name in names:
            found.setdefault(name, []).append(zone)
            captures.setdefault(name, set()).update(reflections)
    if not found:
        return None
    atmosphere, night = choose_atmosphere(found)
    reflections = captures[atmosphere] | unnamed
    data = asset_bytes(archive, atmosphere)
    result = dict(atmosphere=atmosphere, night=night, sun_ray=sun_ray(data), sun_rgb=sun_colour(data), domes=[], unsupported=[],
                  notes=[], clear_sky=None, reflection=None, reflection_source=None, fog=fog_profile(data), grade=colour_grade(data))
    for model in [r['path'] for r in references(data) if r['path'].endswith('.model')]:
        geometry = model_geometry(archive, model)
        slots = {}
        for slot, first, count, _uv in geometry['batches']:
            slots.setdefault(slot, []).append((first, count))
        for material in geometry['materials']:
            spans = slots.get(material['index'])
            if not spans or not material.get('path'):
                continue
            mdata = _material(archive, material['path'])
            refs = [r['path'] for r in references(mdata) if not r.get('resolution_error')]
            graphs = [p for p in refs if p.endswith('.materialgraph')]
            if graphs != [DOME_GRAPH]:
                result['unsupported'].append(dict(model=model, material=material['path'],
                                                  reason=f'sky graph {", ".join(graphs) or "none"} is not ported'))
                continue
            gdata = _graph(archive, DOME_GRAPH)
            if hashlib.sha256(gdata).hexdigest() != DOME_GRAPH_SHA256:
                result['unsupported'].append(dict(model=model, material=material['path'],
                                                  reason='Rk_Sky_Dome graph bytes differ from the decoded one'))
                continue
            slots = {**texture_slots(gdata, GRAPH_SLOT_SECTION), **texture_slots(mdata, MATERIAL_SLOT_SECTION)}
            dome = {slots.get(s) for s in DOME_SLOTS}
            noise = slots.get(NOISE_SLOT)
            if len(dome) != 1 or None in dome or not noise:
                result['unsupported'].append(dict(model=model, material=material['path'],
                                                  reason=f'cloud-layer slots bind {sorted(map(str, dome))}, noise {noise}'))
                continue
            constants = dome_constants(mdata, gdata)
            result['domes'].append(dict(model=model, geometry=geometry, material=material['path'], spans=spans,
                                        constants=constants, dome_texture=dome.pop(), noise_texture=noise,
                                        stars=constants[22] != 0.0))
    if any(d['stars'] for d in result['domes']):
        result['notes'].append('stars term (constant dword 22) is not ported')
    if len(reflections) == 1:
        result['reflection'], result['reflection_source'] = next(iter(reflections)), 'atmosphere zone'
    else:
        volume = lighting_reflection(archive, zone_paths, night, probe_position)
        if volume:
            result['reflection'], result['reflection_source'], result['reflection_volume'] = volume['path'], 'lighting volume', volume
    if profile and result['reflection']:
        # A volume capture is a local probe that may see no sky (Veldin's capture camera sits in one that does not);
        # the clear-sky profile then comes from the first other volume of the level that does.
        order = [result['reflection']] + [p for p in (result.get('reflection_volume') or {}).get('candidate_paths', [])
                                           if p != result['reflection']]
        for path in order:
            try:
                result['clear_sky'] = clear_sky_profile(archive, path)
            except (ImportError, ValueError, OSError) as exc:
                result['notes'].append(f'clear sky profile unavailable: {exc}')
                break
            if result['clear_sky']:
                if path != result['reflection']:
                    result['notes'].append(f'clear sky profile sampled from {path}')
                break
    return result
