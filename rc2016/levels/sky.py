"""Level sky: the atmosphere's cloud dome and a clear-sky colour profile (data only, no GL).

A level's atmosphere zone names one `.atmosphere` asset; the asset lists its sky models first
(docs/levels.md, "Skybox"). Dome materials on the `Rk_Sky_Dome` graph are drawn with a port of its
cloud fragment shader 0x8d3c81d0 (`SKY_FRAGMENT` in viewport.py); other sky graphs (`_flat` nebula
domes, `rk_sky_matte` planets) are reported, not drawn. The clear sky behind the clouds comes from
the atmosphere scattering tables, which are not decoded; `clear_sky_profile` instead samples the
clear-sky pixels of the reflection capture the atmosphere zone references.
"""
from __future__ import annotations

import hashlib
import io
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


def fog_profile(atmosphere_data):
    """Authored fog profile of an atmosphere ({'heights', 'densities'}, ``rc2016.levels.fog.read_profile``), or None."""
    from rc2016.formats.container import read_container
    from rc2016.levels.fog import read_profile
    from rc2016.pipeline.lighting import ATMOSPHERE_HEADER
    root = read_container(atmosphere_data, 0, {})
    for section in root['sections']:
        if int(section['label_hash'], 16) == ATMOSPHERE_HEADER:
            return read_profile(atmosphere_data[section['offset']:section['offset'] + section['size']])
    return None


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


def _profile_from_faces(faces, step=PROFILE_STEP, min_sky=PROFILE_MIN_SKY):
    """Clear-sky colour per elevation bin from six cube faces (float arrays HxWx3, D3D order +X -X +Y -Y +Z -Z,
    rows top-down). A pixel is clear sky when blue dominates (b > 1.25 r, b > g) and it is not clipped;
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
    sky = (colour[:, 2] > 1.25 * colour[:, 0]) & (colour[:, 2] > colour[:, 1]) & (colour.max(axis=1) < 0.98)
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

    Needs Pillow and NumPy; Pillow decodes BC6H clamped to [0, 1], which keeps clear sky (well below 1)
    and only clips clouds and the sun, which the selection drops anyway."""
    from PIL import Image
    import numpy as np
    from rc2016.formats.texture import dds_bytes, parse_header, ps4_deswizzle_top, top_mip_size
    ids = [i for i in archive.resolve(texture_path) if archive.entries[i]['type'] == 'texture']
    if len(ids) != 1:
        raise ValueError(f'{texture_path}: expected one texture, found {len(ids)}')
    info = parse_header(decompress(archive.bytes(ids[0])[0])[0])
    width, height, fmt = info['sd_width'], info['sd_height'], info['format']
    if fmt != 95 or width != height:
        raise ValueError(f'{texture_path}: not a square BC6H cube (format {fmt}, {width}x{height})')
    surface = top_mip_size(width, height, fmt)
    payload = info['built_payload']
    if len(payload) < 6 * surface:
        raise ValueError(f'{texture_path}: fewer than six faces')
    faces = []
    for index in range(6):
        linear = ps4_deswizzle_top(payload[index * surface:(index + 1) * surface], width, height, fmt)
        image = Image.open(io.BytesIO(dds_bytes(width, height, fmt, linear))).convert('RGB')
        faces.append(np.asarray(image, dtype=np.float32) / 255.0)
    return _profile_from_faces(faces)


def level_sky(archive, zone_paths, profile=True):
    """The sky of a level, from its zone list: {'atmosphere', 'sun_ray', 'domes', 'unsupported',
    'sun_rgb', 'clear_sky', 'fog' (authored height-fog profile or None), 'reflection', 'notes'}, or None when no atmosphere zone names an atmosphere.

    Each dome: {'model', 'geometry' (model_geometry), 'spans': [(first triangle, count)], 'material',
    'constants' (23 floats), 'dome_texture', 'noise_texture', 'stars'}. Textures come from the graph's
    slots with the material's slot overrides (NOISE_SLOT, DOME_SLOTS)."""
    atmospheres, reflections = set(), set()
    for zone in zone_paths:
        if 'atmosphere' not in zone.lower() or 'imposter' in zone.lower():
            continue
        found, captures = atmosphere_zone_assets(asset_bytes(archive, zone))
        atmospheres.update(found); reflections.update(captures)
    if not atmospheres:
        return None
    if len(atmospheres) != 1:
        raise ValueError(f'atmosphere zones name {len(atmospheres)} atmospheres: {sorted(atmospheres)}')
    atmosphere = atmospheres.pop()
    data = asset_bytes(archive, atmosphere)
    result = dict(atmosphere=atmosphere, sun_ray=sun_ray(data), sun_rgb=sun_colour(data), domes=[], unsupported=[],
                  notes=[], clear_sky=None, reflection=None, fog=fog_profile(data))
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
        result['reflection'] = next(iter(reflections))
    if profile and result['reflection']:
        try:
            result['clear_sky'] = clear_sky_profile(archive, result['reflection'])
        except (ImportError, ValueError, OSError) as exc:
            result['notes'].append(f'clear sky profile unavailable: {exc}')
    return result
