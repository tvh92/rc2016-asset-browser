"""Probe-volume reflection for the window glass preview (data and CPU reference, no GL; docs/levels.md, "Probe-volume
reflection").

The Novalis window program (``0x8df14766``, docs/material-decoding/novalis-window-glass.md) reads its reflection E from
the lightgrid cell's probe selector: per corner cell ``i`` of the 8 around the point (the lightgrid weights ``w_i``,
renormalised), ``E_probe = sum w_i [mix_i cube[A_i](R) + (1 - mix_i) cube[B_i](R)]``,
``m = min(1, sum w_i conf_i)`` and ``E = (1 - m) E_probe + m cube[255](R)``.

Decoded from the archive and checked against the shadPS4 capture of one Novalis frame:

- **Selector.** Cell bytes 20 and 22 of a ``.lightgrid`` tile index the tile's probe list
  (``lightgrid.tile_probe_paths``, section ``0x96D77BBD``); 255 means the global cube. Byte 21 is the mix and reaches
  the GPU unchanged. At run time the game rewrites the indices as slots of its cube array: in the captured frame each
  of the 153 Novalis probe paths maps to exactly one slot over all 2,977,792 cells (141 slots in use, 12 probes not
  resident and mapped to 255).
- **Cubes.** A probe texture (``levels/novalis/.../envprobes/<id>.texture``) is a 512 x 256 BC6H image holding the six
  128 x 128 faces in a 4 x 2 grid, faces +X, -X, +Y, -Y, +Z, -Z in row-major order (the last two tiles are not read).
  Each face equals mip 0 of the captured cube slice, orientation unchanged (mean absolute log difference 0.007 over the
  six faces of nine cubes, the R11G11B10 quantisation of the capture). Face coordinates follow the GCN cube
  instructions: ``uv = sc / |ma| + 0.5`` (``cube_face_uv``).

Not decoded, kept visible:

- **Confidence** (byte 23). It is 255 in every archive cell; the runtime writes its own value (0 to 255, median 0.22 in
  valid cells that name a probe; it grows with height above some surfaces and correlates with the sky term C, 0.65), and
  its source was not found. The viewer stores ``VIEWER_CONFIDENCE`` (0): cells that name probes reflect them alone, and
  the global cube enters only where a cell names index 255.
- **Global cube** (index 255). The captured cube 255 is not the atmosphere's reflection probe (per-face mean ratios
  0.33 to 1.1, texel log error 0.87); the viewer uses its Level atmosphere environment in its place.
- **Mips.** The archive holds mip 0 only; the runtime's mips 1 to 5 are blurred by a filter that is not a box filter and
  was not identified. The viewer samples mip 0 at every gloss (the window pixels of the capture read LOD 0 to 0.3).
"""
from __future__ import annotations

from rc2016.levels.lightgrid import (ATLAS_XY, CELLS_PER_TILE, TABLE, TILE, VALIDITY_FLOOR, cell_index,
                                     corner_validity, decode_cells, tile_of)

GLOBAL_CUBE = 255           # selector index of the global cube
MAX_PROBES = 255            # probe indices 0 to 254 fit the selector byte
VIEWER_CONFIDENCE = 0       # byte 23 the viewer stores; the runtime's value is not decoded (module docstring)
ENVPROBE_FORMAT = 95        # BC6H unsigned float
ENVPROBE_SIZE = (512, 256)
FACE = 128                  # face edge in texels; face f sits at tile (f % 4, f // 4) of the 4 x 2 grid
BLOCK_BYTES = 16


def cube_face_uv(directions):
    """(face, u, v) of (N, 3) native directions, the GCN V_CUBEID / SC / TC / MA convention: face 0 to 5 is +X, -X, +Y,
    -Y, +Z, -Z, ``uv = sc / |ma| + 0.5`` with ``ma`` twice the major component (u right, v down in the face image)."""
    import numpy as np
    d = np.asarray(directions, np.float64).reshape(-1, 3)
    x, y, z = d[:, 0], d[:, 1], d[:, 2]
    ax, ay, az = np.abs(x), np.abs(y), np.abs(z)
    on_x = (ax >= ay) & (ax >= az)
    on_y = ~on_x & (ay >= az)
    face = np.where(on_x, np.where(x > 0, 0, 1), np.where(on_y, np.where(y > 0, 2, 3), np.where(z > 0, 4, 5)))
    sc = np.select([face == 0, face == 1, face == 2, face == 3, face == 4], [-z, z, x, x, x], -x)
    tc = np.select([face == 0, face == 1, face == 2, face == 3, face == 4], [-y, -y, z, -z, -y], -y)
    major = np.where(on_x, ax, np.where(on_y, ay, az))
    with np.errstate(invalid='ignore', divide='ignore'):
        return face, sc / (2.0 * major) + 0.5, tc / (2.0 * major) + 0.5


def sample_faces(faces, directions):
    """(N, 3) bilinear samples of six (n, n, 3) faces along native ``directions``, clamped half a texel inside the face
    (the viewer shader's filter; the game filters across face edges)."""
    import numpy as np
    face, u, v = cube_face_uv(directions)
    n = faces[0].shape[0]
    x = np.clip(u * n, 0.5, n - 0.5) - 0.5
    y = np.clip(v * n, 0.5, n - 0.5) - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    x1, y1 = np.minimum(x0 + 1, n - 1), np.minimum(y0 + 1, n - 1)
    stack = np.stack(faces)
    return ((stack[face, y0, x0] * (1 - fx) + stack[face, y0, x1] * fx) * (1 - fy)
            + (stack[face, y1, x0] * (1 - fx) + stack[face, y1, x1] * fx) * fy).astype(np.float32)


class ProbeVolume:
    """The level's probes and per-cell selectors with level-wide probe indices.

    ``paths`` lists the probe textures (index order); ``selectors`` is (n_slots, 4096, 4) uint8 in the lightgrid's slot
    order: probe index A, mix, probe index B, confidence (``VIEWER_CONFIDENCE``); index 255 is the global cube, also for
    a local index outside the tile's list (counted in ``unlisted``). ``blocks`` holds, per probe, the linear BC6H block
    stream of its 512 x 256 image as (64, 128, 16) uint8 (None until ``load_probe_volume`` reads them)."""

    def __init__(self, paths, selectors, unlisted=0, blocks=None):
        self.paths, self.selectors, self.unlisted, self.blocks = list(paths), selectors, int(unlisted), blocks

    def atlas_blocks(self, columns):
        """((rows * 64, columns * 128, 16) uint8, (columns, rows)): the probes' block streams placed in one image,
        probe i at column ``i % columns``, row ``i // columns`` (512 x 256 texels each)."""
        import numpy as np
        rows = max(1, -(-len(self.blocks) // columns))
        bh, bw = ENVPROBE_SIZE[1] // 4, ENVPROBE_SIZE[0] // 4
        atlas = np.zeros((rows * bh, columns * bw, BLOCK_BYTES), np.uint8)
        for index, blocks in enumerate(self.blocks):
            row, col = divmod(index, columns)
            atlas[row * bh:(row + 1) * bh, col * bw:(col + 1) * bw] = blocks
        return atlas, (columns, rows)


def build_selectors(grid, confidence=VIEWER_CONFIDENCE):
    """ProbeVolume (without blocks) of a ``lightgrid.LightGrid``: its tiles' probe lists merged into one sorted list and
    each cell's bytes 20 and 22 translated to that list. Raises ValueError when the level names more than
    ``MAX_PROBES`` probes."""
    import numpy as np
    paths = sorted({path for tile in grid.probes for path in tile})
    if len(paths) > MAX_PROBES:
        raise ValueError(f'{len(paths)} reflection probes; the selector byte holds {MAX_PROBES}')
    index = {path: i for i, path in enumerate(paths)}
    selectors = np.empty((grid.tile_count, CELLS_PER_TILE, 4), np.uint8)
    unlisted = 0
    for slot, (cells, tile) in enumerate(zip(grid.cells, grid.probes)):
        lut = np.full(256, GLOBAL_CUBE, np.uint8)
        lut[:len(tile)] = [index[path] for path in tile]
        local = cells[:, (20, 22)]
        unlisted += int(((local != GLOBAL_CUBE) & (local >= len(tile))).sum())
        selectors[slot, :, 0] = lut[local[:, 0]]
        selectors[slot, :, 1] = cells[:, 21]
        selectors[slot, :, 2] = lut[local[:, 1]]
        selectors[slot, :, 3] = confidence
    return ProbeVolume(paths, selectors, unlisted)


def reflection(grid, volume, position, direction, cube, sky):
    """CPU mirror of the viewer shader's ``windowProbeReflection``: (E (N, 3), m (N,)) at native world ``position`` (N, 3)
    along native ``direction`` (N, 3). ``cube(index, directions)`` returns (N, 3) radiance of probe ``index``; ``sky`` is
    (N, 3), the global cube along ``direction``. Corner weights are the lightgrid's (``LightGrid.sample``: trilinear times
    validity, floored at 2^-18, renormalised over existing tiles); a point with no existing corner returns the sky."""
    import numpy as np
    position = np.asarray(position, np.float64).reshape(-1, 3)
    direction = np.asarray(direction, np.float64).reshape(-1, 3)
    sky = np.asarray(sky, np.float64).reshape(-1, 3)
    shifted = position - 0.5
    base = np.floor(shifted)
    frac = shifted - base
    total = np.zeros(len(position))
    confidence = np.zeros(len(position))
    light = np.zeros((len(position), 3))
    memo = {}

    def radiance(ids):
        out = np.empty((len(ids), 3))
        for value in np.unique(ids):
            pick = ids == value
            if value == GLOBAL_CUBE:
                out[pick] = sky[pick]
            else:
                key = int(value)
                if key not in memo:
                    memo[key] = np.asarray(cube(key, direction), np.float64)
                out[pick] = memo[key][pick]
        return out

    for corner in range(8):
        bits = np.array([corner & 1, (corner >> 1) & 1, (corner >> 2) & 1])
        cell = base + bits
        trilinear = np.prod(np.where(bits == 1, frac, 1.0 - frac), axis=1)
        tile = tile_of(cell)
        entry = grid.table[tuple((tile & (TABLE - 1)).T[::-1])]
        exists = entry[:, 3] > 0.5
        if not exists.any():
            continue
        local = (cell - tile * TILE).astype(np.int64)
        slot = (entry[:, 2] * ATLAS_XY * ATLAS_XY + entry[:, 1] * ATLAS_XY + entry[:, 0]).astype(np.int64)
        slot = np.where(exists, slot, 0)
        at = cell_index(local[:, 0], local[:, 1], local[:, 2])
        fields = decode_cells(grid.cells[slot, at])
        weight = np.where(exists, np.maximum(VALIDITY_FLOOR, trilinear * corner_validity(fields['q'], fields['w'], shifted - cell)), 0.0)
        record = volume.selectors[slot, at].astype(np.float64)
        mix = record[:, 1:2] / 255.0
        a, b = radiance(record[:, 0].astype(int)), radiance(record[:, 2].astype(int))
        light += weight[:, None] * (mix * a + (1.0 - mix) * b)
        confidence += weight * record[:, 3] / 255.0
        total += weight
    covered = total > 0.0
    safe = np.where(covered, total, 1.0)
    m = np.where(covered, np.minimum(1.0, confidence / safe), 1.0)
    probe = light / safe[:, None]
    return ((1.0 - m)[:, None] * probe + m[:, None] * sky).astype(np.float32), m.astype(np.float32)


# Archive -------------------------------------------------------------------------------------------

def envprobe_blocks(archive, path):
    """(64, 128, 16) uint8: the linear (untiled) BC6H block stream of a 512 x 256 probe texture. Raises ValueError when
    the path does not resolve to one texture or the texture is not a 512 x 256 BC6H image."""
    import numpy as np
    from rc2016.formats.archive import decompress
    from rc2016.formats.texture import parse_header, ps4_deswizzle_top
    ids = [i for i in archive.resolve(path) if archive.entries[i]['type'] == 'texture']
    if len(ids) != 1:
        raise ValueError(f'{path}: expected one texture, found {len(ids)}')
    info = parse_header(decompress(archive.bytes(ids[0])[0])[0])
    width, height = ENVPROBE_SIZE
    if (info['format'], info['sd_width'], info['sd_height']) != (ENVPROBE_FORMAT, width, height):
        raise ValueError(f"{path}: not a {width}x{height} BC6H probe (format {info['format']}, "
                         f"{info['sd_width']}x{info['sd_height']})")
    linear = ps4_deswizzle_top(info['built_payload'], width, height, ENVPROBE_FORMAT)
    return np.frombuffer(bytes(linear), np.uint8, width * height).reshape(height // 4, width // 4, BLOCK_BYTES)


def envprobe_faces(blocks):
    """Six float32 (128, 128, 3) faces (+X, -X, +Y, -Y, +Z, -Z) of a probe's block stream (``envprobe_blocks``)."""
    from rc2016.formats.bc6h import decode_bc6h
    width, height = ENVPROBE_SIZE
    image, _unsupported = decode_bc6h(bytes(blocks.tobytes()), width, height)
    return [image[(f // 4) * FACE:(f // 4 + 1) * FACE, (f % 4) * FACE:(f % 4 + 1) * FACE] for f in range(6)]


def load_probe_volume(archive, grid):
    """The ProbeVolume of a LightGrid with every probe's blocks read, or None when no tile lists a probe. Reading the
    153 Novalis probes takes about 0.1 s (no decode: the GPU samples BC6H directly)."""
    volume = build_selectors(grid)
    if not volume.paths:
        return None
    volume.blocks = [envprobe_blocks(archive, path) for path in volume.paths]
    return volume


__all__ = ['GLOBAL_CUBE', 'MAX_PROBES', 'ProbeVolume', 'VIEWER_CONFIDENCE', 'build_selectors', 'cube_face_uv',
           'envprobe_blocks', 'envprobe_faces', 'load_probe_volume', 'reflection', 'sample_faces']
