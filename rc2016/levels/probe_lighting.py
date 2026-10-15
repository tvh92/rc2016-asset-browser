"""Level lighting from the atmosphere zone's reflection probe and atmosphere sun (data only, no GL).

An atmosphere zone names a reflection capture (a 512x512 BC6H cube, PS4-tiled, under
``textures/atmosphere/volumereflections``) and an ``.atmosphere`` asset with the sun ray and an
RGB96 direct-light value. This module turns them into the lighting maps of the viewer's
``Level atmosphere`` source (``rc2016.viewer.environment`` layout): the cube is resampled to an
equirectangular world in Blender's Z-up space, which gives 9 irradiance SH coefficients and
GGX-prefiltered specular bands, and the atmosphere supplies the key light.

Evidence and limits:

- Decoded: the probe's radiance (``rc2016.formats.bc6h``; two-subset blocks, about 11 % of the Novalis
  probe, keep Pillow's value clamped to 1), the atmosphere sun ray, and the RGB96 value.
- Checked: the probe's bright glare points at the atmosphere's sun (Novalis: 7 degrees apart, dot
  0.993), which fixes the cube's handedness and face orientation (identity, equal to the pipeline's
  validated state once Blender's bottom-up image rows are accounted for) for this probe.
- The glare itself is cut at ``GLARE_THRESHOLD`` and left to the atmosphere sun, so the sun is not
  counted twice. The threshold is a viewer choice (about the 99th percentile of the Novalis probe).
- Not decoded: how RGB96 scales against the probe (the Novalis sun, 0.7, is small beside the sky
  irradiance, about 1.5 to 2.2; field names and any multiplier are unresolved), the exposure that
  maps these units to the display (``REVIEW_EXPOSURE_STOPS`` is 0 so the lit level agrees with the
  sky dome drawn behind it, which is in the probe's units), light-grid irradiance, per-region
  probe selection and occlusion of the sky term. This is a review preview, not game lighting.
- The research pipeline's ``novalis`` source uses a different probe (``NOVALIS_PROBE``, about 25 times
  dimmer); the two are not interchangeable.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path

from rc2016.formats.archive import decompress
from rc2016.formats.bc6h import decode_bc6h

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / '.cache' / 'environment'
ENVIRONMENT_VERSION = 2       # bump when the maps' computation changes; it names the cache files
LATLONG_SIZE = (1024, 512)
SEAM_RATIO_LIMIT = 2.0        # the pipeline rejects a cube whose seams are worse than this
FORMAT_BC6H = 95
REVIEW_EXPOSURE_STOPS = 0.0   # see the module docstring
GLARE_THRESHOLD = 2.0         # luminance above which probe radiance is left to the atmosphere sun


# Cube geometry -------------------------------------------------------------------------------
# Direct3D face order +X -X +Y -Y +Z -Z in native Y-up game space; rows top first. The same
# formulas as rc2016/pipeline/lighting_blender.py (_face_direction and _latlong).

def face_direction(face, u, v):
    """Unnormalised native direction of face texture coordinates u, v in [-1, 1] (v grows downward)."""
    return ((1, -v, -u), (-1, -v, u), (u, 1, v), (u, -1, -v), (u, -v, 1), (-u, -v, -1))[face]


def sample_cube(faces, directions):
    """(N, 3) radiance of the cube in native-space ``directions`` (N, 3), nearest texel."""
    import numpy as np
    directions = np.asarray(directions, np.float64)
    size = faces[0].shape[0]
    magnitude = np.abs(directions)
    axis = magnitude.argmax(axis=1)
    out = np.zeros((len(directions), 3), np.float32)
    for face in range(6):
        select = (axis == face // 2) & ((directions[np.arange(len(directions)), axis] > 0) == (face % 2 == 0))
        if not select.any():
            continue
        x, y, z = (directions[select, i] / magnitude[select, face // 2] for i in range(3))
        u, v = ((-z, -y), (z, -y), (x, z), (x, -z), (x, -y), (-x, -y))[face]
        col = np.clip(np.floor((u + 1.0) * 0.5 * size).astype(int), 0, size - 1)
        row = np.clip(np.floor((v + 1.0) * 0.5 * size).astype(int), 0, size - 1)
        out[select] = faces[face][row, col]
    return out


def latlong(faces, width=LATLONG_SIZE[0], height=LATLONG_SIZE[1]):
    """(height, width, 3) equirectangular world in Blender's Z-up space (the layout of
    ``rc2016.viewer.environment.equirect_directions``), top row first. Native X, Y, Z is Blender
    X, -Z, Y, so a Blender direction (x, y, z) reads the cube at native (x, z, -y)."""
    import numpy as np
    from rc2016.viewer.environment import equirect_directions
    blender = equirect_directions(width, height).reshape(-1, 3)
    native = np.stack([blender[:, 0], blender[:, 2], -blender[:, 1]], axis=1)
    return sample_cube(faces, native).reshape(height, width, 3)


def _direction_face_uv(direction, exclude=None):
    """(face, u, v) of a direction; ``exclude`` skips that axis, which picks the neighbouring face at an edge."""
    values = [abs(c) for c in direction]
    axis = next(i for i in sorted(range(3), key=lambda i: values[i], reverse=True) if exclude is None or i != exclude)
    face = 2 * axis + (0 if direction[axis] > 0 else 1)
    x, y, z = (c / values[axis] for c in direction)
    return (face,) + ((-z, -y), (z, -y), (x, z), (x, -z), (x, -y), (-x, -y))[face]


def seam_ratio(faces):
    """Mean log-radiance mismatch across the twelve cube edges over the mean mismatch of neighbouring
    texels at the face borders: about 1 for a continuous cube (the Novalis probe measures about 1.3).
    The same measure as ``_orient_faces`` in rc2016/pipeline/lighting_blender.py."""
    import numpy as np
    size = faces[0].shape[0]
    logged = [np.log1p(np.maximum(face, 0.0)) for face in faces]
    t = np.linspace(-1 + 1 / size, 1 - 1 / size, size)
    seam = []
    for face in range(6):
        for edge in range(4):
            u = np.full(size, -1.0 if edge == 0 else 1.0) if edge < 2 else t
            v = t if edge < 2 else np.full(size, -1.0 if edge == 2 else 1.0)
            mapped = [_direction_face_uv(face_direction(face, a, b), exclude=face // 2) for a, b in zip(u, v)]
            other = mapped[0][0]
            if face > other:
                continue
            if not all(m[0] == other for m in mapped):
                raise AssertionError('cube edge changed target face')
            row = np.rint((v + 1) * 0.5 * (size - 1)).astype(int)
            col = np.rint((u + 1) * 0.5 * (size - 1)).astype(int)
            ocol = np.rint((np.array([m[1] for m in mapped]) + 1) * 0.5 * (size - 1)).astype(int)
            orow = np.rint((np.array([m[2] for m in mapped]) + 1) * 0.5 * (size - 1)).astype(int)
            seam.append(np.abs(logged[face][row, col] - logged[other][orow, ocol]))
    if len(seam) != 12:
        raise AssertionError('a cube has twelve unique edges')
    inner = []
    for face in logged:
        inner.extend([np.abs(face[:, 0] - face[:, 1]), np.abs(face[:, -1] - face[:, -2]),
                      np.abs(face[0] - face[1]), np.abs(face[-1] - face[-2])])
    return float(np.mean(seam) / max(float(np.mean(inner)), 1e-12))


# Probe loading -------------------------------------------------------------------------------

def fill_unsupported(face, unsupported, fallback):
    """Copy ``fallback`` pixels into ``face`` (in place) wherever a 4x4 block is flagged in ``unsupported``."""
    import numpy as np
    height, width = face.shape[:2]
    mask = np.repeat(np.repeat(unsupported, 4, axis=0), 4, axis=1)[:height, :width]
    face[mask] = fallback[mask]
    return face


def probe_faces(archive, texture_path):
    """(faces, info): six float32 (512, 512, 3) linear faces of a BC6H reflection cube and a dict with
    the source hash, size, share of blocks held at Pillow's clamped value, and the brightest texel."""
    import numpy as np
    from PIL import Image
    from rc2016.formats.texture import dds_bytes, parse_header, ps4_deswizzle_top, top_mip_size
    ids = [i for i in archive.resolve(texture_path) if archive.entries[i]['type'] == 'texture']
    if len(ids) != 1:
        raise ValueError(f'{texture_path}: expected one texture, found {len(ids)}')
    decoded = decompress(archive.bytes(ids[0])[0])[0]
    info = parse_header(decoded)
    width, height, fmt = info['sd_width'], info['sd_height'], info['format']
    if fmt != FORMAT_BC6H or width != height:
        raise ValueError(f'{texture_path}: not a square BC6H cube (format {fmt}, {width}x{height})')
    surface = top_mip_size(width, height, fmt)
    payload = info['built_payload']
    if len(payload) < 6 * surface:
        raise ValueError(f'{texture_path}: fewer than six faces')
    faces, clamped, blocks = [], 0, 0
    for index in range(6):
        linear = ps4_deswizzle_top(payload[index * surface:(index + 1) * surface], width, height, fmt)
        face, unsupported = decode_bc6h(linear, width, height)
        if unsupported.any():
            fallback = np.asarray(Image.open(io.BytesIO(dds_bytes(width, height, fmt, linear))).convert('RGB'),
                                  np.float32) / 255.0
            fill_unsupported(face, unsupported, fallback)
        clamped += int(unsupported.sum())
        blocks += unsupported.size
        faces.append(face)
    return faces, dict(probe=texture_path, probe_sha256=hashlib.sha256(decoded).hexdigest(), size=width,
                       clamped_block_fraction=clamped / blocks,
                       peak=float(max(face.max() for face in faces)),
                       mean=[float(x) for x in np.mean([face.reshape(-1, 3).mean(axis=0) for face in faces], axis=0)])


def sun_direction_blender(ray_native):
    """Unit Blender-space direction towards the sun for the atmosphere's native ray (direction of travel)."""
    x, y, z = ray_native
    length = math.sqrt(x * x + y * y + z * z)
    return (-x / length, z / length, -y / length)


def environment_from_faces(faces, ray_native, sun_rgb, exposure_stops):
    """Lighting maps in the layout of ``environment.environment_maps`` for an oriented cube and sun."""
    import numpy as np
    from rc2016.viewer.environment import extract_sun, prefiltered_bands, sh_irradiance
    image, _glare_direction, _glare = extract_sun(latlong(faces), GLARE_THRESHOLD)
    return dict(sh=sh_irradiance(image), bands=prefiltered_bands(image),
                sun_direction=np.array(sun_direction_blender(ray_native), np.float32),
                sun=np.array(sun_rgb, np.float32), strength=1.0, exposure=float(exposure_stops))


def level_environment(archive, sky, exposure_stops=None):
    """Lighting maps for a ``level_sky`` result: reflection probe ambient plus the atmosphere sun.

    Raises ValueError when the sky names no single reflection probe or the cube fails the seam check.
    Cached under .cache/environment by the probe's hash, the sun and the version."""
    import numpy as np
    if not sky or not sky.get('reflection'):
        raise ValueError('the level has no atmosphere with exactly one reflection probe')
    stops = REVIEW_EXPOSURE_STOPS if exposure_stops is None else float(exposure_stops)
    ray, rgb = tuple(sky['sun_ray']), tuple(sky['sun_rgb'])
    faces, info = probe_faces(archive, sky['reflection'])
    key = hashlib.sha256(json.dumps([info['probe_sha256'], ray, rgb, ENVIRONMENT_VERSION]).encode()).hexdigest()[:16]
    cache = CACHE_DIR / f'level_{key}.npz'
    maps = None
    if cache.is_file():
        try:
            with np.load(cache) as stored:
                maps = {name: stored[name] for name in ('sh', 'bands', 'sun_direction', 'sun')}
                info['seam_ratio'] = float(stored['seam_ratio'])
        except (OSError, ValueError, KeyError):
            maps = None
    if maps is None:
        info['seam_ratio'] = seam_ratio(faces)
        if info['seam_ratio'] > SEAM_RATIO_LIMIT:
            raise ValueError(f"{sky['reflection']}: cube edge continuity did not validate ({info['seam_ratio']:.2f})")
        maps = environment_from_faces(faces, ray, rgb, stops)
        maps = {name: maps[name] for name in ('sh', 'bands', 'sun_direction', 'sun')}
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix('.tmp.npz')
            np.savez(temporary, seam_ratio=info['seam_ratio'], **maps)
            temporary.replace(cache)
        except OSError:
            pass
    maps.update(strength=1.0, exposure=stops, atmosphere=sky['atmosphere'], sun_rgb=list(rgb), **info)
    return maps


__all__ = ['environment_from_faces', 'face_direction', 'fill_unsupported', 'latlong', 'level_environment', 'probe_faces',
           'sample_cube', 'seam_ratio', 'sun_direction_blender']
