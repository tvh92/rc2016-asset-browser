# Sun shadow atlas

The key light's cast shadow is a set of six orthographic cascades along the sun ray, rendered into one D16 atlas and turned into a screen-space shadow mask before the deferred lighting pass. Everything below is read from one Novalis frame (shadPS4 RenderDoc capture); the viewer's implementation is `rc2016/levels/shadow_cascades.py` and the `sceneShadow` helper in `rc2016/levels/scene_effects.py`.

## Passes

| Pass | Program | Inputs | Output |
| --- | --- | --- | --- |
| Atlas depth draws | the casters' depth-only programs | per tile: viewport and scissor | D16 atlas 8192 x 4096 |
| Raw screen shadow | compute, 8 x 8 groups | view depth (R32), G-buffer normal, atlas, its own 528-byte constant block | R8 1920 x 1080 |
| Filter | compute, 16 x 16 shared tile | raw R8, view depth | R8 1920 x 1080 |
| Deferred lighting | fragment `0xa9f0a431` | the filtered R8 at binding 9 when `CB0[135]` has bit `0x40` (it has), the atlas otherwise | lit HDR |

**Atlas layout.** The six sun cascades own 1792-pixel square viewports at x 0, 1792, 3584 and y 0, 1792: cascade `j` sits in column `j // 2`, row `j % 2`. The fourth column holds the local-light shadows (1088 and 1024-pixel viewports at x 5376 and 6464). Each sun tile is cleared by a four-index quad with depth test Always, then the casters draw with depth test LessEqual, depth clamp on, depth clip off, depth bias enabled with constant factor 1 and slope factor 0, and back, front or no culling per material. The scissor of each tile is a sub-rectangle that the lighting pass also uses as its clamp rectangle. In the frame the six tiles take 44, 31, 200, 325, 101 and 5 caster draws.

**Lighting-pass fallback.** With bit `0x40` clear the lighting fragment program samples the atlas itself with the same lookup but no blend between cascades (its fraction term is `fract(floor(f))`, always 0). The captured frame takes the screen-mask path.

## Constants

Frame block `CB0` of the lighting pass (dword numbers; the compute pass holds the same values in view space):

| Dwords | Content | Frame value |
| --- | --- | --- |
| 128 to 130 | direction to the sun `L` | (-0.07768, 0.66913, 0.73907), the atmosphere ray |
| 140, 141 | band scale and offset | `1 / log2(2.5)` = 0.756471, 6 |
| 144 to 159 | four cascade lanes `q = a X + b Y + c Z + d` (four floats per coefficient) | two distinct lanes, the others repeat |
| 160 to 170 | light basis: columns are the x, y, z axes | z = -L; x = normalize(-z.z, 0, z.x); y = z x x |
| 172 + 4j | per-cascade scale (u, v, depth) | u: 0.011924 ... 0.000122; v = 2u |
| 196 + 4j | per-cascade offset (u, v, depth) and normal offset w | w = 2.2 texels of the cascade |
| 220 + 4j | per-cascade clamp rectangle (u0, v0, u1, v1) | inside the cascade's tile |

The u offsets are whole atlas texels (`8192 u` is an integer), so the cascade centre is snapped to its texel grid.

## Lookup

For a pixel at world position `p` (camera + view depth x ray) with G-buffer normal `N`:

1. `M = max(q0, q1, q2, q3)`; `fade = sat(10 M - 9)`.
2. `f = max(0, CB0[140] log2(M) + CB0[141])`; cascade `j = int(f)` (at most 5), `t = fract(f)`.
3. `blend = max(0, 16 t - fade - 15)`: the last sixteenth of each band blends into cascade `j + 1`.
4. Normal offset: `w = mix(w(j), w(j+1), sat(2 t - 1))`, `p' = p + (L + sat(7 N.L + 2) N) w`.
5. Light space `l = basis . p'`; `uv = clamp(l.xy * scale.xy + offset.xy, rect)`, reference depth `l.z * scale.z + offset.z`.
6. Nine weighted linear Less comparisons: with `st = (8192, 4096) uv + 0.5`, `f = fract(st)`, `base = (floor(st) - 0.5) / size`, weights per axis `(4 - 3f, 7, 1 + 3f)` at offsets `((3 - 2f) / (4 - 3f) - 2, (3 + f) / 7, f / (1 + 3f) + 2)` texels, sum divided by 144. The same kernel serves the second cascade when `blend > 0`; the two are mixed by `blend`, then `visibility = mix(v, 1, fade)`.
7. Filter: `out = (0.4038 c + sum w_n s_n) / (0.4038 + sum w_n)` over the four direct neighbours, `w_n = max(0, 0.14905 - 4 |depth_n / depth_c - 1|)`.

There is no separate shadow floor; the visibility multiplies the key light.

**Cascade lanes.** The two distinct lanes are the edges of the convex hull of the camera and the four view-frustum corners at depth 1000, projected along the sun, that do not touch the camera (here the far-top and far-right edges), each scaled to 1 on its edge. So `M` is the light-space position measured against the frustum's projected hull: band `j` holds `M` in `[2.5^(j-6), 2.5^(j-5))`, the nested split distances 10.24, 25.6, 64, 160, 400 and 1000, and `M >= 1` is lit. Four lanes allow for a sun behind or in front of the camera, where three or four far edges bound the hull.

## Fitting rule

Measured against the frame's constants (`tests/test_shadow_cascades.py`):

| Cascade | Split | Tile width | Texel | Depth range | Normal offset |
| --- | --- | --- | --- | --- | --- |
| 0 | 10.24 | 18.35 | 0.0102 | 62.3 | 0.0225 |
| 1 | 25.6 | 45.85 | 0.0256 | 155.3 | 0.0563 |
| 2 | 64 | 114.6 | 0.0640 | 388.2 | 0.1407 |
| 3 | 160 | 286.5 | 0.160 | 970.4 | 0.352 |
| 4 | 400 | 716.3 | 0.400 | 2331.7 | 0.879 |
| 5 | 1000 | 1789.6 | 0.999 | 4090.4 | 2.197 |

- **Width.** `2 d tan` with `tan` the half-diagonal tangent of the view frustum (`sqrt(tan_x^2 + tan_y^2)`), the bounding-sphere diameter of the slice, to 0.0014 % for cascade 5 and 0.07 to 0.11 % for the others; the small excess is not decoded.
- **Centre.** The middle of the light-space box of the slice `[d(j-1) 2.5^(-1/16), d(j)]` (the near plane, 0.1, for cascade 0), snapped to whole texels: within 0.6 texel for all six. The slice starts where the previous band begins to blend.
- **Depth.** The near depth is the slice's nearest light-space depth (casters nearer the sun are clamped onto it by depth clamp): within 0.02 units for cascades 0 to 4. The range is 6.065 split distances for cascades 1 to 3 (6.086 for cascade 0). Cascades 4 and 5 are shorter, and cascade 5's near depth lies 1286 units nearer the sun than the camera: that rule is not decoded (scene bounds are a candidate).
- **Normal offset.** 2.2 texels of each cascade, exactly.

## Verification

Re-projecting every pixel of the captured frame through these constants and the captured atlas reproduces the game's screen shadow (the R8 the lighting pass reads):

| Model | Pixels whose lit/shadowed class agrees | Mean absolute difference |
| --- | --- | --- |
| Lighting-pass fallback branch (no blend, no filter) | 98.05 % (99.49 % beyond 3 pixels of a shadow edge) | 0.021 |
| Compute raw mask (blend and fade) | 98.89 % | 0.013 |
| Compute raw mask and the filter | 99.29 % | 0.007 |

The remaining differences sit on shadow edges; the filter's half-precision gathers are not reproduced bit for bit. In this frame the cascades' depth axis is the atmosphere ray to 1e-7, so the sun's shadow direction is the atmosphere sun.

## Viewer

`shadow_cascades.fit` applies the rule to the viewer camera (position, axes, field of view and near plane read from the GL matrices) and the active key direction; `SceneEffects` renders the six tiles into a 5376 x 3584 D16 atlas (the sun tiles only), culls casters per tile to the tile's sides and far depth (no near plane, because of depth clamp), draws them with polygon offset factor 0 and units 1, and `sceneShadow` does steps 1 to 6 per fragment with the shading normal. Viewer choices, not game data:

- The shadow distance 1000 and the ratio 2.5 come from one Novalis frame and are used for every level.
- Depth range: 6.065 split distances for every cascade (cascades 4 and 5 differ in the game); the near depth is snapped down to a texel so a camera moving within a texel keeps the tile.
- The lookup is clamped 3 texels inside the tile instead of the game's fitted rectangle.
- The game refits every frame; the viewer refits when the camera has rested 0.15 s (or every 0.5 s while it moves) and redraws only the tiles whose snapped fit changed. A new key direction or new scene content redraws all six (content changes at most every 2 s).
- The filter pass (step 7) is screen space and is not applied: the viewer shades forward.
- Without depth clamp the near depths move up to the top of the scene bounds in light space; where the texture limit is below 5376 the tiles shrink to a third of it.
- Local-light shadow tiles are not rendered.
