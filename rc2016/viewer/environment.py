"""Shared review lighting for the viewers: the HDRI in assets/lighting/shared_lighting.json as
pre-blurred lighting maps, so the preview predicts the research Blender render.

The pipeline's scenes light models with an equirectangular world HDRI at the file's world strength,
view transform Khronos PBR Neutral and its exposure (rc2016/pipeline/scene_defaults.py). The viewer
cannot path-trace, so it splits the same world into a sun and the rest of the sky:

- sun: texels brighter than SUN_THRESHOLD (luminance) give up their excess radiance to one
  directional light (energy-weighted direction, summed irradiance), which the shaders light with
  like a rig light, so the terminator stays sharp; energy is conserved;
- diffuse from the rest: irradiance from 9 spherical-harmonic coefficients (Ramamoorthi and
  Hanrahan), so a surface's diffuse radiance is albedo x E(n) / pi, as Cycles' diffuse BSDF;
- specular from the rest: radiance prefiltered with the GGX lobe at a few roughness levels (one
  equirectangular band each, stacked in one texture), times an analytic environment BRDF (Karis'
  fit) for F0 and F90.

Shadowing, interreflection and the BSDF's exact multiple-scattering behaviour are not reproduced:
this predicts the Blender review render; it is not the render and not game lighting.

Directions are Blender world space (Z up). Blender's environment lookup maps a direction d to
u = 0.5 - atan2(d.y, d.x) / (2 pi), v = atan2(d.z, hypot(d.x, d.y)) / pi + 0.5 (v = 0 is the image's
bottom row; +X is the image centre). Checked against Cycles renders of the shared HDRI: six
directions each returned exactly the texel this mapping selects. The pipeline turns the lookup by
``hdri_rotation_z_degrees`` (a Mapping node: image direction = Rz(angle) . world direction), so the maps
are built from the image turned the same way (``rotate_z``).
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parents[2]
LIGHTING_FILE = ROOT / 'assets' / 'lighting' / 'shared_lighting.json'
CACHE_DIR = ROOT / '.cache' / 'environment'
# Perceptual roughness (sqrt of GGX alpha) of each prefiltered band, sharpest first.
ROUGHNESS_LEVELS = (0.15, 0.3, 0.45, 0.6, 0.8, 1.0)
BAND_SIZE = (128, 64)
SUN_THRESHOLD = 20.0   # luminance above which radiance moves into the directional sun
ENVIRONMENT_VERSION = 3   # bump when the maps' computation changes; it names the cache files

# Tonemaps the viewers offer (the GLSL lives in rc2016/viewer/shading.py).
TONEMAPS = ('ACES fit', 'Khronos PBR Neutral')
LIGHTINGS = ('Viewer rig', 'Shared HDRI')


def shared_lighting(path=LIGHTING_FILE):
    """The shared review lighting settings with ``hdri_path`` resolved next to the JSON file."""
    path = Path(path)
    settings = json.loads(path.read_text(encoding='utf-8'))
    settings['hdri_path'] = str((path.parent / settings['hdri']).resolve())
    return settings


# OpenEXR (scanline, NONE / ZIPS / ZIP, HALF or FLOAT channels): enough for the shared HDRI. -----

_COMPRESSION_LINES = {0: 1, 2: 1, 3: 16}


def _attributes(data):
    attributes, offset = {}, 8
    while data[offset] != 0:
        end = data.index(b'\0', offset)
        name = data[offset:end].decode('latin-1')
        type_end = data.index(b'\0', end + 1)
        kind = data[end + 1:type_end].decode('latin-1')
        size = struct.unpack_from('<i', data, type_end + 1)[0]
        start = type_end + 5
        attributes[name] = (kind, data[start:start + size])
        offset = start + size
    return attributes, offset + 1


def read_exr(path):
    """(height, width, 3) float32 linear RGB array of a scanline OpenEXR file, top row first.

    Supports the uncompressed, ZIPS and ZIP codecs with HALF or FLOAT R, G and B channels; other
    files raise ValueError. Needs NumPy."""
    import numpy as np
    data = Path(path).read_bytes()
    if data[:4] != b'\x76\x2f\x31\x01':
        raise ValueError(f'{path}: not an OpenEXR file')
    if struct.unpack_from('<I', data, 4)[0] & 0x200:
        raise ValueError(f'{path}: tiled OpenEXR is not supported')
    attributes, offset = _attributes(data)
    compression = attributes['compression'][1][0]
    if compression not in _COMPRESSION_LINES:
        raise ValueError(f'{path}: OpenEXR compression {compression} is not supported')
    xmin, ymin, xmax, ymax = struct.unpack('<4i', attributes['dataWindow'][1])
    width, height = xmax - xmin + 1, ymax - ymin + 1
    channels, raw, position = [], attributes['channels'][1], 0
    while raw[position] != 0:
        end = raw.index(b'\0', position)
        name = raw[position:end].decode('latin-1')
        pixel_type, _linear, xs, ys = struct.unpack_from('<iB3xii', raw, end + 1)
        if pixel_type not in (1, 2) or xs != 1 or ys != 1:
            raise ValueError(f'{path}: channel {name} has an unsupported type or sampling')
        channels.append((name, pixel_type))
        position = end + 17
    if not {'R', 'G', 'B'} <= {name for name, _type in channels}:
        raise ValueError(f'{path}: needs R, G and B channels')
    lines = _COMPRESSION_LINES[compression]
    blocks = (height + lines - 1) // lines
    table = struct.unpack_from(f'<{blocks}Q', data, offset)
    out = np.zeros((height, width, 3), np.float32)
    sizes = [(name, 2 if pixel_type == 1 else 4, np.float16 if pixel_type == 1 else np.float32)
             for name, pixel_type in channels]
    row_bytes = sum(size for _name, size, _dtype in sizes) * width
    for block_offset in table:
        y, length = struct.unpack_from('<ii', data, block_offset)
        payload = data[block_offset + 8:block_offset + 8 + length]
        count = min(lines, ymin + height - y)
        if compression and length < row_bytes * count:
            # ZIP: zlib, then a byte predictor and two interleaved halves (the OpenEXR spec).
            values = np.frombuffer(zlib.decompress(payload), np.uint8).astype(np.int16)
            values = (np.cumsum(np.concatenate(([values[0]], values[1:] - 128))) & 0xFF).astype(np.uint8)
            half = (len(values) + 1) // 2
            payload = np.empty(len(values), np.uint8)
            payload[0::2], payload[1::2] = values[:half], values[half:]
            payload = payload.tobytes()
        position = 0
        for line in range(count):
            row = y - ymin + line
            for name, size, dtype in sizes:
                values = np.frombuffer(payload, dtype, width, position)
                position += size * width
                if name in 'RGB' and len(name) == 1:
                    out[row, :, 'RGB'.index(name)] = values
    return out


# Lighting maps -----------------------------------------------------------------------------

def equirect_directions(width, height):
    """(height, width, 3) unit Blender world directions at the texel centres, top row first."""
    import numpy as np
    u = (np.arange(width) + 0.5) / width
    v = 1.0 - (np.arange(height) + 0.5) / height          # top row is v = 1 (straight up)
    phi = (0.5 - u) * 2.0 * math.pi                        # u = 0.5 - atan2(y, x) / 2pi
    theta = (v - 0.5) * math.pi                            # elevation
    cos_t = np.cos(theta)[:, None]
    x = np.cos(phi)[None, :] * cos_t
    y = np.sin(phi)[None, :] * cos_t
    z = np.broadcast_to(np.sin(theta)[:, None], x.shape)
    return np.stack([x, y, z], axis=-1).astype(np.float32)


def direction_uv(direction):
    """Blender's equirectangular (u, v) for a world direction; v = 0 is the bottom row."""
    x, y, z = direction
    return (0.5 - math.atan2(y, x) / (2.0 * math.pi),
            math.atan2(z, math.hypot(x, y)) / math.pi + 0.5)


def _solid_angles(height, width):
    import numpy as np
    theta = (0.5 - (np.arange(height) + 0.5) / height) * math.pi
    row = np.cos(theta) * (math.pi / height) * (2.0 * math.pi / width)
    return np.broadcast_to(row[:, None], (height, width)).astype(np.float32)


def _downsample(image, width, height):
    """Box-filter an equirectangular image to an integer fraction of its size."""
    h, w, _c = image.shape
    if w % width or h % height:
        raise ValueError(f'{w}x{h} does not divide into {width}x{height}')
    return image.reshape(height, h // height, width, w // width, 3).mean(axis=(1, 3))


def rotate_z(image, degrees):
    """The equirectangular ``image`` as Blender's world shows it under a Mapping rotation of ``degrees`` about Z.

    The Mapping node looks up image direction Rz(angle) . d for world direction d, so world azimuth phi shows image
    azimuth phi + angle: with u = 0.5 - phi / (2 pi), the image moves angle / 360 of its width towards higher u,
    wrapping around (linear between columns for a fraction of a texel)."""
    import numpy as np
    shift = (degrees / 360.0) * image.shape[1]
    whole = math.floor(shift)
    fraction = shift - whole
    turned = np.roll(image, whole, axis=1)
    if fraction:
        turned = turned * (1.0 - fraction) + np.roll(image, whole + 1, axis=1) * fraction
    return turned.astype(np.float32)


def sh_irradiance(image):
    """9 x RGB coefficients c such that irradiance E(n) = sum c_i Y_i(n), including the cosine lobe
    (Ramamoorthi and Hanrahan's A_l: pi, 2pi/3, pi/4)."""
    import numpy as np
    h, w, _c = image.shape
    d = equirect_directions(w, h).reshape(-1, 3)
    weight = _solid_angles(h, w).reshape(-1, 1)
    x, y, z = d[:, 0], d[:, 1], d[:, 2]
    basis = np.stack([np.full_like(x, 0.282095), 0.488603 * y, 0.488603 * z, 0.488603 * x,
                      1.092548 * x * y, 1.092548 * y * z, 0.315392 * (3 * z * z - 1),
                      1.092548 * x * z, 0.546274 * (x * x - y * y)], axis=1)
    radiance = image.reshape(-1, 3) * weight
    coefficients = basis.T @ radiance                      # (9, 3)
    lobe = np.array([math.pi, *(2 * math.pi / 3,) * 3, *(math.pi / 4,) * 5], np.float32)[:, None]
    return (coefficients * lobe).astype(np.float32)


def extract_sun(image, threshold=SUN_THRESHOLD):
    """(image without the sun, unit direction, RGB irradiance at normal incidence).

    Texels above ``threshold`` luminance keep ``threshold`` and give the rest of their radiance to
    the sun, so the sum of both equals the original world. No texel above it: zero irradiance."""
    import numpy as np
    h, w, _c = image.shape
    weights_rgb = np.array([0.2126, 0.7152, 0.0722], np.float32)
    luminance = image @ weights_rgb
    bright = luminance > threshold
    if not bright.any():
        return image, np.array([0.0, 0.0, 1.0], np.float32), np.zeros(3, np.float32)
    keep = np.ones_like(luminance)
    keep[bright] = threshold / luminance[bright]
    residual = (image * keep[..., None]).astype(np.float32)
    excess = (image - residual)[bright] * _solid_angles(h, w)[bright][:, None]
    direction = (equirect_directions(w, h)[bright] * (excess @ weights_rgb)[:, None]).sum(axis=0)
    return residual, (direction / np.linalg.norm(direction)).astype(np.float32), excess.sum(axis=0).astype(np.float32)


def _prefilter(source, alpha, width, height):
    """Radiance convolved with the GGX lobe for N = V = R (the split-sum assumption)."""
    import numpy as np
    sh, sw, _c = source.shape
    inputs = equirect_directions(sw, sh).reshape(-1, 3)
    solid = _solid_angles(sh, sw).reshape(-1)
    radiance = source.reshape(-1, 3) * solid[:, None]
    outputs = equirect_directions(width, height).reshape(-1, 3)
    a2 = alpha * alpha
    result = np.empty((outputs.shape[0], 3), np.float32)
    for start in range(0, outputs.shape[0], 1024):
        cosine = np.clip(outputs[start:start + 1024] @ inputs.T, 0.0, 1.0)
        # GGX D(h) with h = n for N = V = R, weighted by n.l as in Karis' prefilter.
        denominator = cosine * cosine * (a2 - 1.0) + 1.0
        weights = cosine * a2 / (math.pi * denominator * denominator)
        result[start:start + 1024] = (weights @ radiance) / np.maximum(weights @ solid, 1e-12)[:, None]
    return result.reshape(height, width, 3)


def prefiltered_bands(image):
    """(len(ROUGHNESS_LEVELS) * BAND_SIZE[1], BAND_SIZE[0], 3) float32: one prefiltered band per
    roughness level, sharpest band at the top, each band's rows top (up) first."""
    import numpy as np
    width, height = BAND_SIZE
    sharp = _downsample(image, width, height)
    coarse = _downsample(image, width // 2, height // 2)
    bands = []
    for roughness in ROUGHNESS_LEVELS:
        alpha = roughness * roughness
        if roughness == ROUGHNESS_LEVELS[0]:
            bands.append(sharp)    # a lobe of a few degrees: about one texel at this size
        else:
            bands.append(_prefilter(sharp if alpha < 0.15 else coarse, alpha, width, height))
    return np.concatenate(bands, axis=0).astype(np.float32)


def environment_maps(settings=None):
    """{'sh': (9, 3), 'bands': (levels * h, w, 3), 'sun_direction', 'sun': RGB irradiance, 'strength',
    'exposure', 'hdri', 'sha256'} for the shared lighting (world strength not applied, the HDRI turned by
    ``hdri_rotation_z_degrees``), cached under .cache/environment by the HDRI's hash and the turn."""
    import numpy as np
    settings = settings or shared_lighting()
    hdri = Path(settings['hdri_path'])
    digest = hashlib.sha256(hdri.read_bytes()).hexdigest()
    turn = float(settings.get('hdri_rotation_z_degrees', 0.0))
    cache = CACHE_DIR / f'{digest[:16]}_z{turn:g}_v{ENVIRONMENT_VERSION}.npz'
    maps = None
    if cache.is_file():
        try:
            with np.load(cache) as stored:
                maps = {key: stored[key] for key in ('sh', 'bands', 'sun_direction', 'sun')}
        except (OSError, ValueError, KeyError):
            maps = None
    if maps is None:
        image, direction, sun = extract_sun(rotate_z(read_exr(hdri), turn))
        maps = dict(sh=sh_irradiance(image), bands=prefiltered_bands(image), sun_direction=direction, sun=sun)
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix('.tmp.npz')
            np.savez(temporary, **maps)
            temporary.replace(cache)
        except OSError:
            pass
    maps.update(strength=float(settings['world_strength']), exposure=float(settings['exposure']),
                view_transform=settings['view_transform'], hdri=hdri.name, sha256=digest)
    return maps


__all__ = ['LIGHTINGS', 'ROUGHNESS_LEVELS', 'TONEMAPS', 'direction_uv', 'environment_maps', 'equirect_directions', 'extract_sun',
           'prefiltered_bands', 'read_exr', 'rotate_z', 'sh_irradiance', 'shared_lighting']
