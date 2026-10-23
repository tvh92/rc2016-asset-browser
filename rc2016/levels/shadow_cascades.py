"""Sun shadow cascades fitted the way the game fits them (docs/material-decoding/shadow-atlas.md).

Decoded from one Novalis frame (the lighting pass, its screen-shadow compute and the atlas depth draws): six
orthographic cascades along the sun ray, nested from the camera with split distances ``D * 2.5**(j - 5)``,
``D`` = 1000; each owns a 1792-square tile of a D16 atlas, three columns by two rows; the cascade of a point comes
from the light-space hull of the view frustum (``lanes``), with a blend into the next cascade over the last
sixteenth of each logarithmic band and a fade to lit between 0.9 and 1 of ``D``; receivers are pushed
``2.2`` texels along ``L + sat(7 N.L + 2) N``; casters draw with a constant depth bias of one unit, no slope bias,
depth clamp on. The tile size and centre rule, the depth range and the tile clamp rectangle are fits to that one
frame or viewer choices, as marked below. Coordinates are native (Y-up) world units."""
import math

import numpy as np

CASCADES = 6
SPLIT_RATIO = 2.5            # CB0[140] = 1 / log2(2.5), CB0[141] = 6: band j holds M in [2.5**(j-6), 2.5**(j-5))
SHADOW_DISTANCE = 1000.0     # the lanes reach 1 at view depth 1000 at the hull corners (one frame; per level unknown)
TILE = 1792                  # atlas viewport of each cascade (depth draws at x 0/1792/3584, y 0/1792)
GAME_ATLAS = (8192, 4096)    # the game's D16 atlas; column four holds the local-light shadows
BLEND_BAND = 1.0 / 16.0      # compute 13650: blend = max(0, 16 fract(f) - fade - 15)
FADE = (10.0, 9.0)           # fade = sat(10 M - 9): lit beyond the shadow distance
NORMAL_OFFSET_TEXELS = 2.2   # CB0 cascade w = 2.2 texels of that cascade, all six
NORMAL_OFFSET_NDL = (7.0, 2.0)
DEPTH_BIAS_UNITS = 1.0       # atlas depth draws: depthBias constant 1, slope 0, clamp 0
DEPTH_SLOPE_BIAS = 0.0
# Fit to the captured frame, rule inferred: the depth range of cascades 0 to 3 is 6.06 to 6.09 times the split
# distance (cascades 4 and 5 are shorter, 5.83 and 4.09, by a rule not decoded, probably scene bounds).
DEPTH_RANGE = 6.065
TILE_MARGIN_TEXELS = 3.0     # viewer choice: lookups are clamped this far inside the tile (the game clamps to a fitted rect)


def light_basis(toward_sun):
    """Rows x, y, z of the light space: z is the sun ray (pointing away from the sun), x the horizontal axis
    normal to it (``normalize(-z.z, 0, z.x)``, CB0[160..170]) and y = z x x."""
    z = -np.asarray(toward_sun, dtype=float)
    z = z / np.linalg.norm(z)
    x = np.array([-z[2], 0.0, z[0]])
    if np.linalg.norm(x) < 1e-6:   # sun overhead: any horizontal axis (not seen in a capture)
        x = np.array([1.0, 0.0, 0.0])
    x = x / np.linalg.norm(x)
    return np.stack((x, np.cross(z, x), z))


def split_distances(distance=SHADOW_DISTANCE):
    return distance * SPLIT_RATIO ** (np.arange(CASCADES, dtype=float) - (CASCADES - 1))


def frustum_corners(forward, right, up, tan_x, tan_y):
    """The four corner rays of the view frustum per unit view depth (top-left, top-right, bottom-left, bottom-right)."""
    f, r, u = (np.asarray(v, dtype=float) for v in (forward, right, up))
    return np.array([f + sx * tan_x * r + sy * tan_y * u for sy in (1, -1) for sx in (-1, 1)])


def _hull(points):
    pts = sorted(map(tuple, points))
    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 1e-12:
                out.pop()
            out.append(p)
        return out
    lower, upper = half(pts), half(reversed(pts))
    return lower[:-1] + upper[:-1]


def hull_lanes(corners_xy, distance=SHADOW_DISTANCE):
    """The cascade measure M = max of four lanes, each linear in the light-space xy offset from the camera: the edges
    of the convex hull of the camera and the four frustum corners at ``distance`` (light xy, per unit depth) that do
    not touch the camera, scaled to 1 on the edge. Unused lanes repeat the first (CB0[144..159] has two distinct lanes,
    the far-top and far-right edges, and two copies). Returns (4, 2) gradients per unit light-space offset."""
    origin = (0.0, 0.0)
    hull = _hull([origin] + [tuple(map(float, c)) for c in corners_xy])
    lanes = []
    for a, b in zip(hull, hull[1:] + hull[:1]):
        if a == origin or b == origin:
            continue
        a, b = np.array(a), np.array(b)
        n = np.array([b[1] - a[1], a[0] - b[0]])
        if n @ a < 0:
            n = -n
        if n @ a > 1e-12:
            lanes.append(n / (n @ a * distance))
    if not lanes:   # degenerate frustum: a radial measure along the farthest corner
        c = max(corners_xy, key=lambda v: float(np.hypot(*v)))
        lanes.append(np.asarray(c, dtype=float) / (float(np.dot(c, c)) * distance))
    lanes = (lanes + [lanes[0]] * 4)[:4]
    return np.array(lanes)


def fit(camera, forward, right, up, tan_x, tan_y, near, toward_sun, distance=SHADOW_DISTANCE, tile=TILE,
        snap=True, caster_min_z=None):
    """Fit the cascades to a camera (native position and unit axes, tangents of the half fields of view, near plane).

    Decoded: basis, splits, lanes, normal offset in texels. Fits to the captured frame (within the tolerances of
    ``tests/test_shadow_cascades.py``), rule inferred: the tile width is the slice's far-face diagonal
    ``2 d tan`` (the bounding-sphere diameter of the slice), the centre is the middle of the light-space box of the
    slice ``[d(j-1) 2.5**(-1/16), d(j)]`` (the near plane for cascade 0) snapped to whole texels, and the depth range
    starts at the slice's nearest light-space depth and spans ``DEPTH_RANGE`` split distances. ``caster_min_z`` (viewer
    fallback without depth clamp) moves every near plane up to that light-space depth so casters above are kept."""
    R = light_basis(toward_sun)
    cam = R @ np.asarray(camera, dtype=float)
    corners = frustum_corners(forward, right, up, tan_x, tan_y) @ R.T
    lanes_xy = hull_lanes(corners[:, :2], distance)
    lanes = np.concatenate([lanes_xy, -(lanes_xy @ cam[:2])[:, None]], axis=1)   # lane = gx l.x + gy l.y + c
    splits = split_distances(distance)
    out = dict(basis=R, lanes=lanes, splits=splits, centres=np.zeros((CASCADES, 2)), widths=np.zeros(CASCADES),
               z_near=np.zeros(CASCADES), z_range=np.zeros(CASCADES), texels=np.zeros(CASCADES),
               normal_offset=np.zeros(CASCADES), tile=int(tile))
    diagonal = 2.0 * math.hypot(tan_x, tan_y)
    for j, d in enumerate(splits):
        n = near if j == 0 else splits[j - 1] * SPLIT_RATIO ** (-BLEND_BAND)
        pts = cam + np.concatenate([corners * n, corners * d])
        width = diagonal * d
        texel = width / tile
        centre = (pts[:, :2].min(axis=0) + pts[:, :2].max(axis=0)) * 0.5
        z_near, z_range = float(pts[:, 2].min()), DEPTH_RANGE * d
        if snap:
            centre = np.round(centre / texel) * texel
            z_near = math.floor(z_near / texel) * texel   # viewer choice: stable depth while the camera moves within a texel
        if caster_min_z is not None and caster_min_z < z_near:
            z_range += z_near - caster_min_z
            z_near = float(caster_min_z)
        out['centres'][j], out['widths'][j], out['texels'][j] = centre, width, texel
        out['z_near'][j], out['z_range'][j] = z_near, z_range
        out['normal_offset'][j] = NORMAL_OFFSET_TEXELS * texel
    return out


def cascade_measure(fit_, points):
    """M for native points (any leading shape): the max of the four lanes."""
    l = np.asarray(points, dtype=float) @ fit_['basis'].T
    q = l[..., :1] * fit_['lanes'][:, 0] + l[..., 1:2] * fit_['lanes'][:, 1] + fit_['lanes'][:, 2]
    return q.max(axis=-1)


def cascade_band(M):
    """Cascade index, fraction within the band, blend weight into the next cascade and the fade to lit (compute 13650)."""
    M = np.asarray(M, dtype=float)
    fade = np.clip(FADE[0] * M - FADE[1], 0.0, 1.0)
    with np.errstate(divide='ignore', invalid='ignore'):
        f = np.log2(np.where(M > 0, M, 1e-30)) / math.log2(SPLIT_RATIO) + CASCADES
    f = np.maximum(0.0, f)
    j = np.minimum(f.astype(int), CASCADES - 1)
    frac = f - np.floor(f)
    blend = np.maximum(0.0, frac / BLEND_BAND - fade - (1.0 / BLEND_BAND - 1.0))
    return j, frac, blend, fade


def tile_origin(j, tile=TILE):
    """Atlas pixel origin of cascade j: column j // 2, row j % 2."""
    return (j // 2) * tile, (j % 2) * tile


def atlas_size(tile=TILE):
    """The viewer atlas holds only the six sun tiles: 3 x 2 tiles (the game's is 8192 x 4096 with local-light tiles)."""
    return 3 * tile, 2 * tile


def gl_matrices(fit_, j):
    """Modelview (native -> eye) and orthographic projection that render cascade j into its tile viewport.
    Eye space is (x, -y, -z) of the light basis, so GL's usual winding and depth direction hold."""
    R = fit_['basis']
    view = np.eye(4)
    view[:3, :3] = np.diag([1.0, -1.0, -1.0]) @ R
    W, (cx, cy) = fit_['widths'][j], fit_['centres'][j]
    zn, zr = fit_['z_near'][j], fit_['z_range'][j]
    proj = np.eye(4)
    proj[0, 0], proj[0, 3] = 2.0 / W, -2.0 * cx / W
    proj[1, 1], proj[1, 3] = 2.0 / W, 2.0 * cy / W
    proj[2, 2], proj[2, 3] = -2.0 / zr, -2.0 * zn / zr - 1.0
    return view, proj


def lookup_constants(fit_, atlas=None):
    """Per-cascade scale (xyz), offset (xyz, w = normal offset in world units) and clamp bounds (u0, v0, u1, v1) for
    ``uv = l.xy * scale.xy + offset.xy``, ``depth = l.z * scale.z + offset.z`` in a GL atlas laid out by ``tile_origin``."""
    tile = fit_['tile']
    aw, ah = atlas or atlas_size(tile)
    scale, offset, bounds = np.zeros((CASCADES, 4)), np.zeros((CASCADES, 4)), np.zeros((CASCADES, 4))
    for j in range(CASCADES):
        x0, y0 = tile_origin(j, tile)
        W, (cx, cy) = fit_['widths'][j], fit_['centres'][j]
        sx, sy = tile / (W * aw), -tile / (W * ah)
        scale[j, :3] = sx, sy, 1.0 / fit_['z_range'][j]
        offset[j] = (x0 + tile * 0.5) / aw - cx * sx, (y0 + tile * 0.5) / ah - cy * sy, -fit_['z_near'][j] / fit_['z_range'][j], fit_['normal_offset'][j]
        m = TILE_MARGIN_TEXELS
        bounds[j] = (x0 + m) / aw, (y0 + m) / ah, (x0 + tile - m) / aw, (y0 + tile - m) / ah
    return scale, offset, bounds


def cascade_planes(fit_, j):
    """Native-space culling planes (n.p + d >= 0 inside) of cascade j's caster volume: the tile's four sides and its
    far depth; no near plane, because casters between the cascade and the sun are clamped onto it (depth clamp)."""
    R = fit_['basis']
    W, (cx, cy) = fit_['widths'][j], fit_['centres'][j]
    far = fit_['z_near'][j] + fit_['z_range'][j]
    h = W * 0.5
    return np.array([np.append(R[0], -(cx - h)), np.append(-R[0], cx + h),
                     np.append(R[1], -(cy - h)), np.append(-R[1], cy + h),
                     np.append(-R[2], far)])


def camera_from_gl(projection, modelview):
    """Camera position, forward, right, up (native), half-field tangents and near plane from column-major GL matrices
    whose modelview maps native to eye space."""
    P = np.asarray(projection, dtype=float).reshape(4, 4).T
    V = np.asarray(modelview, dtype=float).reshape(4, 4).T
    inv = np.linalg.inv(V)
    right, up, back = (inv[:3, i] / np.linalg.norm(inv[:3, i]) for i in range(3))
    near = P[2, 3] / (P[2, 2] - 1.0)
    return inv[:3, 3], -back, right, up, 1.0 / P[0, 0], 1.0 / P[1, 1], near
