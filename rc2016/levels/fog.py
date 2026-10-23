"""Height fog for the Level atmosphere lighting (data only, no GL).

The game's fog is analytic height fog read from its material vertex shaders (shadPS4 cache, e.g.
0x7cba9e6d): a piecewise-linear density-versus-height profile integrated along the ray from a start
distance out to the surface, then ``T = 2 ** -(tau ** exponent)`` and ``colour = mix(fog, lit, T)``.
``fog_T`` is that formula. The shader reads the profile as four height ramps (``a``, ``b``, ``alpha``,
``beta`` per ramp, per-view constants CB0[72..87]); ``build_ramps`` derives them from the five knot
heights and densities the atmosphere asset stores at the end of its header (``read_profile``).

Decoded from shader code: the formula, the ramp layout, the start-distance and exponent slots. Inferred
(one indoor Veldin capture, no distant geometry to compare with): the header-density to optical-density
mapping ``rho = FOG_DENSITY_SCALE * -ln(1 - d)`` and the values of ``FOG_DENSITY_SCALE``,
``FOG_EXPONENT`` and ``FOG_START``. The fog colour is not in the header; the viewer uses the sky's
horizon colour. See docs/levels.md ("Height fog").
"""
from __future__ import annotations

import math
import struct

from rc2016.levels.capture_calibration import frame

# Atmosphere header (section 0x02F06D4E, 131552 bytes) tail: five knot heights, then five densities.
HEADER_SIZE = 131552
KNOT_OFFSET = 131424
DENSITY_OFFSET = 131444
KNOTS = 5
# Inferred, not decoded. Fitted to one Veldin capture (rho_0 = 0.228865 for header density 1, which
# clamps 1 - d at 1e-10): rho = FOG_DENSITY_SCALE * -ln(max(1 - d, MIN_TRANSPARENCY)) per level unit.
FOG_DENSITY_SCALE = 0.0099397
MIN_TRANSPARENCY = 1e-10
# Runtime values read from that capture (CB0[90], CB0[91]); whether they are global is unknown.
FOG_EXPONENT = 1.45
FOG_START = 13.2006
MIN_DY = 1e-4               # the shader's clamp of the vertical offset (decoded)
# Fog colour = FOG_COLOUR_SCALE x the sky's horizon colour, for the geometry fog and the dome fog alike (inferred, see
# ``fog_colour``). The geometry fog used the plain horizon sample (1.0) while the dome fit wanted 2.5.
FOG_COLOUR_SCALE = 2.5


def read_profile(header):
    """{'heights': 5 floats, 'densities': 5 floats} from an atmosphere header section, or None when it is
    not the 131552-byte PS4 layout or holds non-finite values or densities outside [0, 1]."""
    if len(header) != HEADER_SIZE:
        return None
    heights = struct.unpack_from('<5f', header, KNOT_OFFSET)
    densities = struct.unpack_from('<5f', header, DENSITY_OFFSET)
    if not all(math.isfinite(x) for x in heights + densities) or not all(0.0 <= d <= 1.0 for d in densities):
        return None
    return dict(heights=heights, densities=densities)


def optical_density(header_density, scale=FOG_DENSITY_SCALE):
    """Optical density per level unit for an authored header density in [0, 1] (inferred mapping)."""
    return scale * -math.log(max(1.0 - float(header_density), MIN_TRANSPARENCY))


def build_ramps(profile, cam_y, scale=FOG_DENSITY_SCALE, exponent=FOG_EXPONENT, start=FOG_START, c88_ratio=1.0):
    """Runtime ramps (the shader's CB0[72..91] contents) for a camera at native height ``cam_y``.

    Ramp k spans knot k to k + 1 with t = clamp(a * (h - cam_y) + b) and density (2 * alpha * t + beta) * a
    along it, so ``a`` = 1 / width, ``b`` = a * (cam_y - h_k), and alpha, beta follow from the densities at
    the two knots. A knot pair that is not ascending (Novalis lists 926 then 400) is an empty ramp.
    ``c88`` / ``c89`` are the horizontal-ray form ``c88 * dist + c89`` (zero at the start distance): c88 is
    the density at the camera height (the continuous limit) times ``c88_ratio``: the Veldin capture holds 1.45 times
    that and the Novalis capture 1.73 times, unexplained."""
    heights, rho = profile['heights'], [optical_density(d, scale) for d in profile['densities']]
    a, b, alpha, beta = [], [], [], []
    for k in range(KNOTS - 1):
        width = heights[k + 1] - heights[k]
        if not width > 0.0:
            a.append(0.0); b.append(0.0); alpha.append(0.0); beta.append(0.0)
            continue
        ak = 1.0 / width
        beta_k = rho[k] / ak
        a.append(ak); b.append(ak * (cam_y - heights[k]))
        beta.append(beta_k); alpha.append((rho[k + 1] / ak - beta_k) * 0.5)
    c88 = 0.0
    for k in range(KNOTS - 1):
        if a[k] and 0.0 < b[k] < 1.0:
            c88 += (2.0 * alpha[k] * b[k] + beta[k]) * a[k]
    c88 *= c88_ratio
    return dict(a=a, b=b, alpha=alpha, beta=beta, c88=c88, c89=-c88 * start, exponent=float(exponent),
                start=float(start))


def fog_T(pos, cam, ramps):
    """Transmittance of the fog between ``cam`` and ``pos`` (1 = no fog), both native Y-up world points.

    ``ramps`` is ``build_ramps`` for the camera's height. Exactly the decoded shader formula in double
    precision (the game evaluates it per vertex in single precision)."""
    dx, dy, dz = pos[0] - cam[0], pos[1] - cam[1], pos[2] - cam[2]
    if abs(dy) <= MIN_DY:
        dy = MIN_DY
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    hs = ramps['start'] * dy / dist
    total = 0.0
    for a, b, alpha, beta in zip(ramps['a'], ramps['b'], ramps['alpha'], ramps['beta']):
        a_start = min(max(a * hs + b, 0.0), 1.0)
        a_end = min(max(a * dy + b, 0.0), 1.0)
        total += alpha * (a_end * a_end - a_start * a_start) + beta * (a_end - a_start)
    tau = dist / dy * total
    if tau > 0.0:
        tau = tau ** ramps['exponent']
    return min(max(2.0 ** -tau, 0.0), 1.0)


def horizon_colour(profile):
    """RGB of the lowest-elevation bin of a clear-sky profile (``sky.clear_sky_profile``), or None."""
    if not profile:
        return None
    return tuple(float(x) for x in profile[0])


def fog_colour(profile, scale=None):
    """The fog colour (HDR, the units of the lit colour before exposure) for a clear-sky profile, or None: ``scale``
    (default ``FOG_COLOUR_SCALE``) times the lowest bin. One colour serves the geometry fog and the sky dome.

    Inferred, not decoded (the game computes its fog colour on the CPU from the atmosphere; the Veldin capture's
    was (1.665, 3.720, 5.201), not found in the header). Evidence for a factor above 1: the captured Veldin colour
    is of the same size as 2.5 x Novalis' horizon sample (about 1.0, 1.7, 2.2 x 2.5 against 1.7, 3.7, 5.2), a fog
    equal to the horizon sample greys the clouds and the far terrain, and the dome fit of the Novalis probe gave 2
    to 3. The factor is not decoded and the hue is the sampled horizon's, not the game's."""
    horizon = horizon_colour(profile)
    if horizon is None:
        return None
    scale = FOG_COLOUR_SCALE if scale is None else float(scale)
    return tuple(scale * c for c in horizon)


def apply_fog(colour, transmittance, fog_colour):
    """CPU reference of the shader's blend: ``mix(fog, colour, T)`` per channel."""
    return tuple(f * (1.0 - transmittance) + c * transmittance for c, f in zip(colour, fog_colour))


def fog_state(sky, lighting, cam_y, enabled=True):
    """Fog values for a frame, or None for no fog: only for the Level atmosphere lighting with an authored
    profile (``sky['fog']``), a captured clear-sky profile for the colour (or a captured frame of the atmosphere,
    ``capture_calibration``, which gives the colour and the density scale, exponent and start itself), and ``enabled``.
    Returns {'colour': RGB, 'ramps': build_ramps(...)}."""
    sky = sky or {}
    captured = frame(sky.get('atmosphere'))
    captured = captured['fog'] if captured else None
    colour = tuple(captured['colour']) if captured else fog_colour(sky.get('clear_sky'))
    profile = sky.get('fog')
    if not enabled or lighting != 'Level atmosphere' or colour is None or not profile:
        return None
    if captured:
        ramps = build_ramps(profile, float(cam_y), captured['scale'], captured['exponent'], captured['start'], captured['c88_ratio'])
    else:
        ramps = build_ramps(profile, float(cam_y))
    return dict(colour=colour, ramps=ramps)


def uniform_values(state):
    """GLSL uniform values of a ``fog_state``: (on, colour, a, b, alpha, beta, (c88, c89, exponent, start))."""
    if not state:
        return 0, (0.0, 0.0, 0.0), (0.0,) * 4, (0.0,) * 4, (0.0,) * 4, (0.0,) * 4, (0.0, 0.0, 1.0, 0.0)
    r = state['ramps']
    return (1, tuple(state['colour']), tuple(r['a']), tuple(r['b']), tuple(r['alpha']), tuple(r['beta']),
            (r['c88'], r['c89'], r['exponent'], r['start']))
