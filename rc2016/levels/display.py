"""The game's final display transform for the Level atmosphere lighting (data only, no GL).

Decoded from the shadPS4 RenderDoc capture of one Veldin frame (``assets/game_view/rc_game_view.json``,
validated there against the captured frame, median error 0.0099 in display units) and from the atmosphere asset:

    scene-linear HDR colour (the units of the reflection probes and the light colours)
      -> times EXPOSURE_MULTIPLIER (4.5538; a runtime value of that frame)
      -> 1D tone curve over log2 in [-8, +5] (``assets/game_view/rc_tonemap.spi1d``, 4096 samples, R = G = B)
      -> sRGB transfer function
      -> 32x32x32 colour-grade LUT, sampled trilinearly with the half-texel mapping c * 31/32 + 1/64
         (the atmosphere header's own LUT: bytes 312 to 131384, R8G8B8A8, red fastest, then green, then blue;
         for Veldin it equals the captured grade exactly, Novalis' is a mild saturation boost)

Not modelled, as in the research view: bloom, lens dirt, film grain, the screen fade.

Inferred, not decoded: that the tone curve (a table, not an atmosphere field) and the exposure of that frame apply to
other levels. Evidence for it on Novalis: the exposure takes the captured clear sky (probe units) to within about 5 %
of the in-game frame's sky colour with no other fit (docs/levels.md, "Display transform").

The viewer packs the curve and the grade into one float texture (``display_table``) that the Level atmosphere
programs sample (``gameDisplay`` in rc2016/viewer/shading.py); ``display`` is the CPU reference of the same chain.
"""
from __future__ import annotations

import json
from pathlib import Path

GAME_VIEW_DIR = Path(__file__).resolve().parents[2] / 'assets' / 'game_view'
EXPOSURE_MULTIPLIER = 4.553783893585205   # rc_game_view.json "exposure_multiplier" (a runtime value of the capture frame)
CURVE_STOPS = (-8.0, 5.0)                 # the 1D curve's domain, log2 of the exposed colour
CURVE_SAMPLES = 1024                      # samples kept of the 4096 (the table is smooth: decimation error about 2e-4)
GRADE_SIZE = 32
GRADE_OFFSET, GRADE_BYTES = 312, GRADE_SIZE ** 3 * 4   # in the atmosphere header section
TABLE_WIDTH, TABLE_HEIGHT = GRADE_SIZE * GRADE_SIZE, GRADE_SIZE + 1   # grade rows 0-31 (slab b at x = 32 b + r), curve row 32


def read_curve(path=None):
    """The tone curve as a float array (n,), from a Sony-style ``.spi1d`` (R = G = B is checked)."""
    import numpy as np
    path = Path(path) if path else GAME_VIEW_DIR / 'rc_tonemap.spi1d'
    rows, length = [], None
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if not parts or parts[0] in ('Version', 'From', 'Components', '{', '}'):
                continue
            if parts[0] == 'Length':
                length = int(parts[1])
                continue
            rows.append([float(v) for v in parts])
    lut = np.array(rows, np.float64)
    if length != len(lut) or lut.shape[1] != 3 or not (lut[:, 0] == lut[:, 1]).all() or not (lut[:, 1] == lut[:, 2]).all():
        raise ValueError(f'{path}: unexpected tone curve layout')
    return lut[:, 0]


def exposure_from_view_json(path=None):
    """``exposure_multiplier`` of ``rc_game_view.json`` (the constant above is checked against it)."""
    path = Path(path) if path else GAME_VIEW_DIR / 'rc_game_view.json'
    return float(json.loads(path.read_text(encoding='utf-8'))['exposure_multiplier'])


def decimate_curve(curve, samples=CURVE_SAMPLES):
    import numpy as np
    return np.interp(np.linspace(0.0, 1.0, samples), np.linspace(0.0, 1.0, len(curve)), curve)


def grade_from_bytes(raw):
    """A colour-grade LUT of raw R8G8B8A8 bytes (``sky.colour_grade``) as a float array (b, g, r, 3) in [0, 1]."""
    import numpy as np
    texels = np.frombuffer(raw, np.uint8, GRADE_BYTES).reshape(GRADE_SIZE, GRADE_SIZE, GRADE_SIZE, 4)
    return texels[..., :3].astype(np.float64) / 255.0


def grade_from_header(header):
    """The atmosphere header section's colour-grade LUT as a float array (b, g, r, 3) in [0, 1]."""
    return grade_from_bytes(header[GRADE_OFFSET:GRADE_OFFSET + GRADE_BYTES])


def identity_grade():
    import numpy as np
    axis = np.linspace(0.0, 1.0, GRADE_SIZE)
    b, g, r = np.meshgrid(axis, axis, axis, indexing='ij')
    return np.stack([r, g, b], -1)


def display_table(grade, curve=None):
    """The float32 texture (TABLE_HEIGHT, TABLE_WIDTH, 3) the shader samples: rows 0 to 31 hold the grade, blue slab b at
    columns 32 b to 32 b + 31 (red along x, green along y), row 32 holds the decimated tone curve in every channel."""
    import numpy as np
    curve = decimate_curve(read_curve() if curve is None else curve)
    table = np.zeros((TABLE_HEIGHT, TABLE_WIDTH, 3), np.float32)
    table[:GRADE_SIZE] = np.asarray(grade, np.float32).transpose(1, 0, 2, 3).reshape(GRADE_SIZE, TABLE_WIDTH, 3)
    table[GRADE_SIZE, :len(curve)] = curve[:, None]
    return table


def tone(x, curve):
    """Tone curve of an exposed scene-linear value (numpy array), linear interpolation in log2 as the game's 1D LUT."""
    import numpy as np
    lo, hi = CURVE_STOPS
    u = np.clip((np.log2(np.maximum(x, 1e-10)) - lo) / (hi - lo), 0.0, 1.0) * (len(curve) - 1)
    return np.interp(u, np.arange(len(curve)), curve)


def srgb_encode(c):
    import numpy as np
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(np.maximum(c, 1e-12), 1.0 / 2.4) - 0.055)


def apply_grade(rgb, grade):
    """Trilinear lookup of encoded colours (..., 3) in a (b, g, r, 3) grade."""
    import numpy as np
    p = np.clip(np.asarray(rgb, np.float64), 0.0, 1.0) * (GRADE_SIZE - 1)
    i = np.minimum(np.floor(p).astype(int), GRADE_SIZE - 2)
    f = p - i
    out = 0.0
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                w = (f[..., 2] if db else 1 - f[..., 2]) * (f[..., 1] if dg else 1 - f[..., 1]) * (f[..., 0] if dr else 1 - f[..., 0])
                out = out + w[..., None] * grade[i[..., 2] + db, i[..., 1] + dg, i[..., 0] + dr]
    return out


def display(rgb, grade, curve=None, exposure=EXPOSURE_MULTIPLIER):
    """CPU reference of ``gameDisplay``: scene-linear (..., 3) -> display values in [0, 1] (sRGB encoded, graded)."""
    import numpy as np
    curve = decimate_curve(read_curve() if curve is None else curve)
    tone_mapped = tone(np.asarray(rgb, np.float64) * exposure, curve)
    return apply_grade(srgb_encode(tone_mapped), grade)
