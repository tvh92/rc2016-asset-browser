"""Lightgrid indirect light for the Level atmosphere lighting source (data and CPU reference, no GL).

A `.lightgrid` asset is one tile of 16 x 16 x 16 cells of 24 bytes. The layout below was decoded from the
game's lit shader (GCN code translated to SPIR-V) and byte-checked against a shadPS4 RenderDoc capture of one
Veldin frame (the runtime tile buffer equals the archive tiles in bytes 0 to 19):

- A tile covers [c - 8, c + 8) per axis in the game's native Y-up world, one cell per world unit; the
  path coordinate ``grid_<x>_<y>_<z>`` is the tile centre c. The runtime finds a tile through a 64 x 64 x 64
  table of u16 slots indexed ``tx + 64 ty + 4096 tz`` with ``t = (floor(p - 0.5) >> 4) & 63`` (the table wraps).
- Cell index inside a tile: ``x + 16 y + 256 z``. A cell centre is the integer coordinate plus 0.5, so a point
  p reads the 8 cells around ``floor(p - 0.5)`` with trilinear weights of ``p - 0.5``.
- Bytes 0 to 2: unorm8 direction ``d = 2 q - 1`` (unit length). Bytes 4 to 15: three R11G11B10_FLOAT colours,
  A (the colour facing +d), B (facing -d, mostly black) and C (a sky-facing term). Bytes 16 to 19: unorm8
  ``(qx, qy, qz, w)``, a per-cell validity plane. Bytes 20 to 23 select reflection probes at run time; the
  viewer does not use them.
- Per cell, for a unit normal n: ``E(n) = lerp(B, A, 0.5 + 0.5 dot(n, d)) + S (0.5 + 0.5 n.y) C``.
- Per corner k with o = p - centre(k): ``v = clamp(8 dot(q - 0.5, o) + 16 w - 7, 0, 1)`` and the corner
  weight is ``max(2^-18, trilinear_k v)``; the weights are renormalised to sum to 1. (The shader spells the
  ``-0.5`` as ``-4 (ox + oy + oz)``, the signed component sum.)

``S`` is the atmosphere's authored sky-term scale: the header float at offset 128 reads 2.33 in Veldin's atmosphere,
the value the Veldin capture holds, and 1.75, 1.0 and so on elsewhere (``rc2016.levels.sky.sky_term_scale``);
``SKY_TERM_SCALE`` is only the fallback when a header cannot be read.

Inferred, not decoded: ``AMBIENT_SCALE``, the factor from grid units to the viewer's ambient units (docs/levels.md). The grid holds baked indirect light only: the
authored lights of ``lighting.zone`` are drawn separately, and whether the game's baking already contains
some of them is unknown, so the preview may count them twice in places.
"""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / '.cache' / 'lightgrid'
LIGHTGRID_VERSION = 1           # bump when the packed arrays change; it names the cache files
TILE = 16
CELLS_PER_TILE = TILE ** 3
CELL_BYTES = 24
TABLE = 64                      # tiles per axis of the lookup table (the index wraps)
ATLAS_XY = 8                    # atlas tiles per axis in x and y; z grows to fit
VALIDITY_FLOOR = 2.0 ** -18     # smallest corner weight, as in the shader
COVER_GAIN = 4.0                # viewer choice: coverage is the valid trilinear weight times this, clamped to 1
CELL_SECTION = 0x9989BB49       # DAT1 section that holds the 4096 cells
PROBE_SELECTOR_SECTION = 0x96D77BBD
# SKY_TERM_SCALE is the sky-term scale S used when an atmosphere's own (``sky.sky_term_scale``, header offset 128) is
# unreadable; 2.33 is the Veldin capture's. Visual calibration, not decoded data (docs/levels.md, Level atmosphere
# lighting): AMBIENT_SCALE converts grid units to the viewer's ambient units (diffuse is albedo * ambient / pi). It was
# fitted, with S assumed 2.33, so that the mean display luminance of the pixels the grid changes matches the
# reflection-probe ambient in two Novalis views (open ground and city: 1.03 and 0.97 at 11). Novalis' own S is 1.75,
# which lowers the level-average grid light by 1.255, so the scale keeps that calibration at 11 x 1.255 = 13.8. The
# grid is directional and occluded, so this is about 2.5 times what an up-facing normal needs to match the probe at
# open-sky cells and 4.4 times pi, the factor of a grid that stores E / pi.
SKY_TERM_SCALE = 2.33
AMBIENT_SCALE = 13.8
LUMA = (0.2126, 0.7152, 0.0722)


def ambient_scale(atmosphere):
    """Grid units to the viewer's ambient units for an atmosphere path: pi where a captured frame shows the grid is in
    game units (``capture_calibration``), else the fitted ``AMBIENT_SCALE``."""
    from rc2016.levels.capture_calibration import frame
    captured = frame(atmosphere)
    return captured['grid_ambient_scale'] if captured else AMBIENT_SCALE


def float_from_bits(bits, mantissa_bits):
    """Unsigned small float of ``mantissa_bits`` (6 for the 11-bit channels, 5 for the 10-bit one) and 5
    exponent bits; the infinity and NaN exponent decodes as 0, which the viewer treats as no light."""
    import numpy as np
    bits = np.asarray(bits, np.uint32)
    exponent = (bits >> mantissa_bits).astype(np.int32)
    mantissa = (bits & ((1 << mantissa_bits) - 1)).astype(np.float64)
    scale = float(1 << mantissa_bits)
    value = np.where(exponent == 0, mantissa / scale * 2.0 ** -14, (1.0 + mantissa / scale) * 2.0 ** (exponent - 15.0))
    return np.where(exponent == 31, 0.0, value).astype(np.float32)


def decode_r11g11b10(words):
    """(..., 3) float32 colour of R11G11B10_FLOAT words (red in the low 11 bits, blue in the top 10)."""
    import numpy as np
    words = np.asarray(words, np.uint32)
    return np.stack([float_from_bits(words & 0x7FF, 6), float_from_bits((words >> 11) & 0x7FF, 6),
                     float_from_bits((words >> 22) & 0x3FF, 5)], axis=-1)


def decode_cells(cells):
    """Fields of (..., 24) uint8 cells: direction ``d`` (3), colours ``A``, ``B``, ``C`` (3 each), plane ``q`` (3)
    and ``w``."""
    import numpy as np
    cells = np.ascontiguousarray(cells, np.uint8)
    words = cells.view('<u4').reshape(cells.shape[:-1] + (6,))
    return dict(d=cells[..., 0:3].astype(np.float32) / 255.0 * 2.0 - 1.0, A=decode_r11g11b10(words[..., 1]),
                B=decode_r11g11b10(words[..., 2]), C=decode_r11g11b10(words[..., 3]),
                q=cells[..., 16:19].astype(np.float32) / 255.0, w=cells[..., 19].astype(np.float32) / 255.0)


def cell_index(x, y, z):
    """Index of a cell inside its tile (x fastest, then y, then z)."""
    return x + TILE * y + TILE * TILE * z


def tile_of(cell):
    """Tile index (floor division by 16) of integer cell coordinates."""
    import numpy as np
    return np.floor_divide(np.asarray(cell, np.int64), TILE)


def table_slot(tile):
    """Index ``tx + 64 ty + 4096 tz`` of tile indices (..., 3) in the 64-cubed tile table (indices wrap)."""
    import numpy as np
    tile = np.asarray(tile, np.int64) & (TABLE - 1)
    return tile[..., 0] + TABLE * tile[..., 1] + TABLE * TABLE * tile[..., 2]


def corner_validity(q, w, offset):
    """Validity of a corner cell for a sample at ``offset`` = p - cell centre (..., 3): the shader's
    ``clamp(8 dot(q - 0.5, o) + 16 w - 7, 0, 1)``."""
    import numpy as np
    return np.clip(8.0 * np.sum((np.asarray(q) - 0.5) * offset, axis=-1) + 16.0 * np.asarray(w) - 7.0, 0.0, 1.0)


def cell_light(fields, normal, sky_scale):
    """E(n) of decoded cells for unit normals (broadcastable): ``lerp(B, A, 0.5 + 0.5 d.n) + S (0.5 + 0.5 n.y) C``."""
    import numpy as np
    normal = np.asarray(normal, np.float32)
    blend = (0.5 + 0.5 * np.sum(fields['d'] * normal, axis=-1))[..., None]
    sky = (sky_scale * (0.5 + 0.5 * normal[..., 1]))[..., None]
    return fields['B'] + (fields['A'] - fields['B']) * blend + sky * fields['C']


class LightGrid:
    """Tiles packed for lookup: ``table`` (64, 64, 64, 4) float32 holds the atlas tile (x, y, z) and 1 where a tile
    exists; ``cells`` is (n_slots, 4096, 24) uint8 in slot order (the CPU reference reads it, the GPU gets the atlas
    arrays of ``atlas``)."""

    def __init__(self, table, cells, slots_xyz):
        self.table, self.cells, self.slots = table, cells, slots_xyz
        self.atlas_tiles = (ATLAS_XY, ATLAS_XY, max(1, -(-len(cells) // (ATLAS_XY * ATLAS_XY))))

    @property
    def tile_count(self):
        return len(self.cells)

    def atlas(self):
        """(colour, dirq): uint32 (D, H, 3 W) words A, B, C per cell interleaved along x, and uint8 (D, H, 2 W, 4)
        bytes (d, 0) then (q, w); x, y, z of the atlas are the tile's cells placed at its table slot."""
        import numpy as np
        ax, ay, az = self.atlas_tiles
        padded = np.zeros((ax * ay * az, CELLS_PER_TILE, CELL_BYTES), np.uint8)
        padded[:len(self.cells)] = self.cells
        grid = padded.reshape(az, ay, ax, TILE, TILE, TILE, CELL_BYTES).transpose(0, 3, 1, 4, 2, 5, 6)
        grid = np.ascontiguousarray(grid.reshape(az * TILE, ay * TILE, ax * TILE, CELL_BYTES))
        colour = np.ascontiguousarray(grid[..., 4:16]).view('<u4').reshape(grid.shape[:3] + (3,))
        dirq = np.stack([grid[..., 0:4], grid[..., 16:20]], axis=3)
        return (colour.reshape(grid.shape[0], grid.shape[1], -1),
                np.ascontiguousarray(dirq.reshape(grid.shape[0], grid.shape[1], -1, 4)))

    def sample(self, position, normal, sky_scale=SKY_TERM_SCALE):
        """CPU mirror of the viewer shader's ``gridAmbient`` at native world ``position`` (N, 3) with unit
        ``normal`` (N, 3): (light (N, 3) in grid units, coverage (N,)). Coverage is the share of the
        trilinear weight on existing, valid cells times ``COVER_GAIN``, clamped to 1 (1 inside the grid, 0 where
        no tile or only invalid cells are near); the viewer fades to the probe ambient as it falls. The light of
        a sample with coverage 0 is zero."""
        import numpy as np
        position = np.asarray(position, np.float64).reshape(-1, 3)
        normal = np.asarray(normal, np.float32).reshape(-1, 3)
        shifted = position - 0.5
        base = np.floor(shifted)
        frac = shifted - base
        total = np.zeros(len(position))
        coverage = np.zeros(len(position))
        light = np.zeros((len(position), 3))
        for corner in range(8):
            bits = np.array([corner & 1, (corner >> 1) & 1, (corner >> 2) & 1])
            cell = base + bits
            trilinear = np.prod(np.where(bits == 1, frac, 1.0 - frac), axis=1)
            offset = shifted - cell
            tile = tile_of(cell)
            entry = self.table[tuple((tile & (TABLE - 1)).T[::-1])]
            exists = entry[:, 3] > 0.5
            if not exists.any():
                continue
            local = (cell - tile * TILE).astype(np.int64)
            slot = (entry[:, 2] * ATLAS_XY * ATLAS_XY + entry[:, 1] * ATLAS_XY + entry[:, 0]).astype(np.int64)
            raw = self.cells[np.where(exists, slot, 0), cell_index(local[:, 0], local[:, 1], local[:, 2])]
            fields = decode_cells(raw)
            validity = corner_validity(fields['q'], fields['w'], offset)
            weight = np.where(exists, np.maximum(VALIDITY_FLOOR, trilinear * validity), 0.0)
            total += weight
            coverage += np.where(exists, trilinear * validity, 0.0)
            light += weight[:, None] * cell_light(fields, normal, sky_scale)
        with np.errstate(invalid='ignore', divide='ignore'):
            light = np.where(total[:, None] > 0.0, light / total[:, None], 0.0)
        return light.astype(np.float32), np.clip(coverage * COVER_GAIN, 0.0, 1.0)


def pack_tiles(cells, coordinates):
    """Build a LightGrid from (n, 4096, 24) uint8 tiles and their (n, 3) path coordinates (tile centres).

    Raises ValueError for a centre that is not 8 mod 16, a cell block of the wrong size, or two tiles that
    share a wrapped table slot."""
    import numpy as np
    cells = np.ascontiguousarray(cells, np.uint8)
    coordinates = np.asarray(coordinates, np.int64).reshape(-1, 3)
    if cells.ndim != 3 or cells.shape[1:] != (CELLS_PER_TILE, CELL_BYTES) or len(cells) != len(coordinates):
        raise ValueError('lightgrid tiles must be (n, 4096, 24) bytes with one coordinate each')
    if np.any((coordinates - TILE // 2) % TILE):
        raise ValueError('a lightgrid path coordinate is not a tile centre (8 mod 16)')
    tiles = (coordinates - TILE // 2) // TILE
    slots = table_slot(tiles)
    if len(np.unique(slots)) != len(slots):
        raise ValueError('two lightgrid tiles share a wrapped table slot')
    ax = ay = ATLAS_XY
    index = np.arange(len(cells))
    atlas_xyz = np.stack([index % ax, (index // ax) % ay, index // (ax * ay)], axis=1)
    table = np.zeros((TABLE, TABLE, TABLE, 4), np.float32)
    wrapped = tiles & (TABLE - 1)
    table[wrapped[:, 2], wrapped[:, 1], wrapped[:, 0], :3] = atlas_xyz
    table[wrapped[:, 2], wrapped[:, 1], wrapped[:, 0], 3] = 1.0
    words = cells.view('<u4').reshape(len(cells), CELLS_PER_TILE, 6)[..., 1:4]
    bad = (((words & 0x7FF) >> 6) == 31) | ((((words >> 11) & 0x7FF) >> 6) == 31) | (((words >> 22) >> 5) == 31)
    if bad.any():   # an infinity or NaN channel would reach the GPU; the CPU decode reads it as 0
        cells = cells.copy()
        cleaned = words.copy()
        cleaned[bad.any(axis=-1)] = 0
        cells.view('<u4').reshape(len(cells), CELLS_PER_TILE, 6)[..., 1:4] = cleaned
    return LightGrid(table, cells, atlas_xyz)


# Loading ---------------------------------------------------------------------------------------------

def _tile_cells(archive, asset_id):
    """The (4096, 24) uint8 cell block of one lightgrid asset."""
    import numpy as np
    from rc2016.formats.archive import asset_data
    from rc2016.levels.zone_format import sections
    data = asset_data(archive, asset_id)   # in memory; decode() would also write the asset to disk
    found = sections(data)
    if CELL_SECTION not in found or found[CELL_SECTION][1] != CELLS_PER_TILE * CELL_BYTES:
        raise ValueError('lightgrid asset has no 4096-cell block')
    offset = found[CELL_SECTION][0]
    return np.frombuffer(data, np.uint8, CELLS_PER_TILE * CELL_BYTES, offset).reshape(CELLS_PER_TILE, CELL_BYTES)


def _location(entry):
    """Cheap identity of an archive entry's bytes: where its available copies sit and how long they are."""
    if 'loose_path' in entry:
        stat = Path(entry['loose_path']).stat()
        return f"{entry['loose_path']}|{stat.st_size}|{stat.st_mtime_ns}"
    return '|'.join(f"{r['archive']}:{r['offset']}:{r['size']}" for r in entry['occurrences'] if r['available'])


def zone_grid_map(data, resolve):
    """Grid rows of any lighting zone's tables (sections ``0x3E36401D`` paths and ``0xD6E15BC0`` coordinates), in the
    shape of the pipeline's Novalis decoder: [{'index', 'path', 'coordinates', 'asset_id'}]. Every path must be a
    ``grid_<x>_<y>_<z>.lightgrid`` of one region folder, agree with its stored coordinate, resolve to one archive
    asset and be unique; otherwise ValueError. ``resolve`` maps a path to archive entry ids."""
    import math
    import struct
    from rc2016.common import asset_path
    from rc2016.levels.zone_format import sections
    from rc2016.pipeline.lighting import ZONE_GRID_COORDINATES, ZONE_GRID_PATHS, grid_coordinates, level_region
    found = sections(data)
    if ZONE_GRID_PATHS not in found or ZONE_GRID_COORDINATES not in found:
        raise ValueError('lighting zone lacks the grid path and coordinate tables')
    (path_start, path_size), (coord_start, coord_size) = found[ZONE_GRID_PATHS], found[ZONE_GRID_COORDINATES]
    if path_size % 4 or coord_size % 16 or path_size // 4 != coord_size // 16:
        raise ValueError('lighting zone grid tables have inconsistent sizes')
    rows, regions = [], set()
    for index, offset in enumerate(struct.unpack_from(f'<{path_size // 4}I', data, path_start)):
        end = data.find(b'\0', offset) if 0 <= offset < len(data) else -1
        if end < 0:
            raise ValueError('lighting zone grid string is outside the file or unterminated')
        path = asset_path(data[offset:end].decode('utf-8'))
        x, y, z, ordinal = struct.unpack_from('<3fI', data, coord_start + 16 * index)
        coordinates = grid_coordinates(path)
        if coordinates is None or ordinal != index or not all(math.isfinite(v) and v.is_integer() for v in (x, y, z)) \
                or coordinates != (int(x), int(y), int(z)):
            raise ValueError(f'lighting zone grid record {index} disagrees with its path {path}')
        regions.add(level_region(path))
        ids = resolve(path)
        if len(ids) != 1:
            raise ValueError(f'lighting zone grid path does not resolve uniquely: {path}')
        rows.append(dict(index=index, path=path, coordinates=list(coordinates), asset_id=ids[0]))
    if len(regions) > 1 or len({r['path'] for r in rows}) != len(rows) or len({tuple(r['coordinates']) for r in rows}) != len(rows):
        raise ValueError('lighting zone grid table mixes regions or repeats a path or coordinate')
    return rows


def day_lighting_zone(zone_paths, night=False):
    """The one lighting zone to read among a level's zone paths: the Novalis zone when listed (its grid is the
    validated one), else the single zone whose stem is ``lighting`` or ``lgt`` and whose night-ness equals ``night``
    (``sky.is_night``). None when there is not exactly one."""
    from rc2016.levels import sky
    from rc2016.pipeline.lighting import NOVALIS_LIGHTING_ZONE
    wanted = [p.replace('\\', '/').lstrip('?/') for p in zone_paths]
    if NOVALIS_LIGHTING_ZONE in {p.lower() for p in wanted}:
        return NOVALIS_LIGHTING_ZONE
    candidates = [p for p in wanted if sky.is_lighting_zone(p) and sky.is_night(p) == night]
    return candidates[0] if len(candidates) == 1 else None


def level_lightgrid(archive, zone_paths, workers=None, night=False):
    """The LightGrid of the level, or None when it has no lighting zone to read (``day_lighting_zone``).

    Novalis' lighting zone goes through the pipeline decoder it was validated with; any other level reads the
    single day (or, with ``night``, night) lighting zone through ``zone_grid_map``, whose paths must share one
    region folder. The layout is the same; only Novalis and Veldin were byte-checked against a capture.

    The packed tiles are cached under .cache/lightgrid by the grid assets' archive locations and the version, so a
    warm load reads one file instead of decoding every asset."""
    import numpy as np
    from rc2016.pipeline.lighting import NOVALIS_LIGHTING_ZONE, _decode_lighting_zone_grid_map
    from rc2016.levels.scene import asset_bytes
    zone = day_lighting_zone(zone_paths, night)
    if zone is None:
        return None
    if zone == NOVALIS_LIGHTING_ZONE:
        rows = _decode_lighting_zone_grid_map(asset_bytes(archive, zone), archive.resolve)['grids']
        prefix = 'novalis'
    else:
        data = asset_bytes(archive, zone)
        from rc2016.levels.zone_format import sections
        from rc2016.pipeline.lighting import ZONE_GRID_COORDINATES, ZONE_GRID_PATHS
        if not {ZONE_GRID_PATHS, ZONE_GRID_COORDINATES} <= set(sections(data)):
            return None     # a lighting zone without a lightgrid (Deplanetizer exterior)
        rows = zone_grid_map(data, archive.resolve)
        prefix = 'zone'
    digest = hashlib.sha256()
    digest.update(f'{LIGHTGRID_VERSION}'.encode())
    for row in rows:
        digest.update(f"{row['path']}|{_location(archive.entries[row['asset_id']])}\n".encode())
    cache = CACHE_DIR / f'{prefix}_{digest.hexdigest()[:16]}.npz'
    coordinates = np.array([row['coordinates'] for row in rows], np.int64)
    if cache.is_file():
        try:
            with np.load(cache) as stored:
                if np.array_equal(stored['coordinates'], coordinates):
                    return pack_tiles(stored['cells'], coordinates)
        except (OSError, ValueError, KeyError):
            pass
    workers = workers or max(2, min(6, (os.cpu_count() or 2) - 1))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='lightgrid') as pool:
        cells = np.stack(list(pool.map(lambda row: _tile_cells(archive, row['asset_id']), rows)))
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix('.tmp.npz')
        np.savez(temporary, cells=cells, coordinates=coordinates)
        temporary.replace(cache)
    except OSError:
        pass
    return pack_tiles(cells, coordinates)


__all__ = ['AMBIENT_SCALE', 'ambient_scale', 'LightGrid', 'SKY_TERM_SCALE', 'cell_index', 'cell_light', 'corner_validity', 'decode_cells',
           'decode_r11g11b10', 'level_lightgrid', 'pack_tiles', 'table_slot', 'tile_of']
