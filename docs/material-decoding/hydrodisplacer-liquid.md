# Hydrodisplacer liquid: forward program `0xF0F45D78`

The liquid inside the Hydrodisplacer's tank is mesh 1 of `equipment/gadget/gdgt_hydrodisplacer/gdgt_hydrodisplacer.model`, material `test_Liquid_Orientation` (graph SHA-256 `7eb07c69c31448c0efee469b681f1e6084b13f33ef8481d8ad39b05ca6097fb3`, flags `0x0000023C`: opaque, two-sided). The material stores no parameters, so every input is a graph default. The graph has two pass contracts with the same layout (`CB2[0]` gloss `0x24F4CA4C` = 1.0, `CB2[1]` fill `0x66583BF6` = 1.0; three bindings of `hydrodisplacer_water_cma` and one of `met_amb_water_pool_flow_01_n`).

This page decodes the pass-0 forward colour program `F0F45D78` (code SHA-256 `43f06ca9…f6259`, 13,668 bytes) and its alpha-tested pre-pass `09BE55F4` (828 bytes) statically from the archive. The viewer and Blender preview rule built from it is described under "Viewer rule" (see also [liquids](../liquids.md), S28). Archive-backed checks of everything below are in `tests/test_liquid_orientation_program.py`.

## Which program the Hydrodisplacer draws

The gadget's five materials map to programs as follows. The shader dumps of the one captured Gadgets-menu session (`dump_shaders` on, compile time per program) show that the view compiled exactly one program of each graph: `5DBCCCB7` (main body, `basic_incandescence`), `4FC5703F` (glass, `basic_glass` table 0, byte-identical to the decoded program), `F0F45D78` and `09BE55F4` (liquid), `3AD1AD46` and `A4B733E7` (gauge needle). The match is by compile time and graph contents, not by a captured draw: that capture holds only the menu's UI pass (`Research/captures/2026-10-23_hydrodisplacer-glass/README.md`).

## Method

`rc2016/research/forward_loops.py` compiles the program with each taken backward branch kept opaque, as for the [Thumper glove glass](thumper-glove-glass.md): the loops are the light-list walk, and everything the material decides is outside them. Texture coordinates come from the translator's sample list (`Compiler.samples`), because the samples themselves are unresolved indirect descriptors. The frozen inputs are the pass word `CB0[135]` = 0 (see [frame constants](frame-constants.md)) and the instance flag word `0x2261` (lit diffuse `0x20`, lit specular `0x40`, fog `0x2000`; `0x80` clear); the exec-masked export is evaluated with those. Fade slope and offset (the record's two other words) are 0 unless stated.

## Verified closed forms

Evaluated against the program DAG with random inputs (40 trials, error below 5e-7; the UV terms below 5e-6 for times up to 400, float32 literals against the double form). `N`, `T` are the normalised `attribute2` and `attribute3.xyz`, `V` the normalised `attribute4.xyz`, `sgn` the sign of `attribute3.w`, `f` = +1 front, -1 back face, `t` = `CB0[99]`, `(rx, ry, rz)` the per-instance record's position (the model origin; the record slot is read through `attribute2.w`), `W` = `ps.POS_W`, and `Sn(u, v)` a sample of the binding with API `n` (a mapping of API to texture is below).

| Term | Value |
| --- | --- |
| Rim | `rim = sat(4 (1 - sat(f N.V))^2)` (fixed exponent 2, no material key) |
| Fade | `e = sat(W s + o)`, `s`, `o` the record's slope and offset words |
| Alpha before the opacity product | `m = (1 - e) sat(0.5 + rim) + 0.741 e`; `a = sat(S7.A (1 - (1 - m)(1 - S5.R)(1 - S6.R)))` |
| Alpha | `sat(a attribute0.3)`; the opaque blend ignores it |
| Discard (exec mask) | a fragment is dropped when `a < attribute0.2` (the alpha-test shape described in [shading](../shading.md#recovered-data-versus-approximation)); `attribute0.2` is not UV0 |
| Wobble | `w = cos(0.1 t)` |
| `S6` UV | `(0.5 u + w + 0.03 t, 0.5 v + w + 0.05 t)`, `u, v` = `attribute0.xy`, channel R |
| `S5` UV | `(u + w + 0.04 t, v + w + 0.06 t)`, channel R |
| `S7` UV | `(l, l)`, `l = 0.1 (ry - (CB0[29] - attribute4.y) + 1.6 CB2[1] - 1)`, channel A. `CB0[29] - attribute4.y` is the fragment's world height, so `l` follows the height below the model origin and the fill `CB2[1]` shifts it |
| `S8a`, `S8b` UV | with `gx = rx - (CB0[28] - attribute4.x)`, `gz = rz - (CB0[30] - attribute4.z)`: `S8a = (0.4 gx - 0.1 t, 0.466667 gz + 0.025 t)`, `S8b = (gx / 3 + 0.1 t, gz / 3 - 0.05 t)`; channels R, G of the BC5 flow normal map |
| Normal strength | `q = CB0[95] (1 - e)` |
| Normal | `x = 2 q (S8a.R - S8b.R)`, `y = 2 q (S8a.G - S8b.G)`, `w' = 1 - x^2 - y^2`, `B = normalize(sgn N x T)`, `N' = normalize(w' N + \|y\| B + \|x\| T)` (absolute values, no front-face flip) |
| Gloss | `a0 = 2^(-11 sqrt(sat(CB2[0])))` (the roughness `a^2`), widened by the geometric-AA term `h = CB0[139] W attribute1.2 / max(0.1, N.V)`: `a^2 = 1 - (1 - a0)(2 - 1 / max(0.5, 1 - h^2 / 4))` |
| F0, F90 | `F0 = 1 + rim^10` (grey, no material key), `F90 = sat(60 F0)` = 1 |
| Final colour | `rgb_c = t_f (K_c D_c + S_c) + (1 - t_f) CB0[112 + c]` (fog mix), `K` = `(0.0648033, 0.2383976, 0.3185468)` hard-coded in the program: the liquid's base colour, a teal. The specular light term has its red channel scaled by 0.3126391 (green, blue by 1). Read from the DAG, not evaluated: `S_c = lerp(spec_c, F0 g_c, r)`, `g` a screen-space colour lookup (API 3, offset by API 4) and `r` its weight, and `t_f = 1 - (1 - attribute4.w)(1 - r)` on the `0x2000` path (`t_f = attribute4.w` otherwise) |

`D` (the lit diffuse sum) and the specular accumulators are the light-list loop: the registers `loop-varying VGPR 73..75` multiply both. The debug-view selects (`CB0[135]` bits) wrap the colour and are the identity at 0.

## Pre-pass `09BE55F4`

Exports `0.5 N_v + 0.5` (xyz, `N_v` the normalised `attribute1.xyz`) to target 0 with the same alpha `a` as above and the same discard, `a < attribute0.2`. It reads the same three bindings and the record's position, so it cuts the liquid out of the normal target along the same waterline.

## What the texture channels do

`hydrodisplacer_water_cma` (512 x 512 BC7): `A` is a soft step along the diagonal, 1.0 up to about 0.4 and 0 beyond about 0.62, so `S7.A(l, l)` is the fill mask, a waterline at world height set by `CB2[1]`; `R` is nearly zero (mean 0.013) with sparse bright dots, so `S5.R` and `S6.R` add scattered bright speckles to the alpha, not a texture. `met_amb_water_pool_flow_01_n` (BC5, 512 x 512) has red and green near 0.5 (range 0.42 to 0.57): a gentle ripple. With the default fill 1.0, `l = 0.1 (h + 0.6)` where `h` is the fragment's height below the record origin, so `S7.A` is 1 for `h` below about 3.2 units and the alpha test then passes wherever `m >= attribute0.2` (`m` is at least 0.5). Above `h = -0.6` the coordinate goes negative, and the sampler's addressing decides whether that part is kept (clamp, `A` = 1) or cut (wrap, `A` near 0): see the sampler item below.

## What stays undecoded without draw data

- **Per-instance record.** The flag word is assumed `0x2261`: bit `0x80` would replace the alpha with `min(S33.R, a)`, `S33` an engine texture (API 33) read at `(attribute0.x / 3, attribute0.y / 3)`; bits `0x20`, `0x40` and `0x2000` gate the diffuse, specular and fog paths. The fade slope and offset (taken 0), the position `(rx, ry, rz)` and the product in `attribute0.3` are record contents. No record is in the archive or in the one capture that touches this gadget.
- **Vertex outputs.** `attribute0.2` (the alpha reference), `attribute0.3`, `attribute1.2` (geometric-AA width) and `attribute4.w` (fog) come from the vertex program, which was not decoded for this graph; their values on the liquid mesh are not read.
- **Sampler state.** Whether the `S7` lookup wraps or clamps decides what a fill below 1 does (`l` goes negative); the sampler states of the five bindings are runtime data.
- **Fill value.** `CB2[1]` is 1.0 in the graph and the material does not override it; whether game code sets it per draw (as the tank empties or fills) is not in the archive.
- **Lighting.** The light list, sun, shadow cascades, environment probes, screen-space reflection (the API 3 and API 4 samples under flag `0x2000`), fog colour `CB0[112..114]`, `CB0[95]`, `CB0[139]` and time `CB0[99]` are frame or engine inputs. The registers 73 to 75 that scale the diffuse and specular sums are loop outputs and are not evaluated.

## Viewer rule

`test_liquid_orientation` has an exact-graph-hash preview rule (`glass_liquid`, `rc2016/viewer/textures.py` `GRAPH_RULES`, `GLASS_FRAGMENT` `uKind` 13, Blender `game_shader._viewer_rule_glass`, validated by `validation.GLASS_CONSTANTS`). It draws the opaque two-sided liquid as `rgb = K Ld + tint x specular(F0, N')` with the decoded base colour `K`, the red-scaled specular, gloss from the graph default, and the flow normal map read at the fragment's model-space XZ (`gx = -x`, `gz = -z`, the record origin taken as the model origin). Everything under "What stays undecoded" is a frozen or omitted input: `CB0[99]` = 0, `CB0[95]` = 1, no fade, no alpha test, no waterline mask, fog `t` = 1, and the light list, shadows, probes and screen-space lookup are the viewer rig (Cycles lighting in Blender). It is a preview, not a game-equivalent render (`game_equivalence` is False).

Blender differs in two recorded ways: the Principled metallic base colour cannot exceed 1, so `F0 = 1 + rim^10` is clamped to 1 and the grey rim term is dropped; and the tangent frame is the UV tangent, not the screen-derivative frame of the viewer. The Blender front-end is checked by building the rule on a material and running `validation._validate_viewer_rule_material`; no full model build or reopen validation of the gadget has been run.
