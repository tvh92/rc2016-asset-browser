# Novalis river water: forward program `0xF4D47ECE`

The Novalis river and sea are `environment/pokitaru/natural/rp_pok_ocean_water_plane` placements (a 600 x 600 unit quad, 200 triangles) whose slot 0 the zone replaces with `pok_uniq_water.material`. The material names `pok_uniq_water.materialgraph` (SHA-256 `0cdcf946a6d628509191df6768257f9da0bdb785b9e9d7f0511c60432509ba11`); its flags word `0x0000000A` is alpha-over and one-sided. Pass 0's colour writer is `F4D47ECE` (code SHA-256 `89b494be6b573eb0f5e7ed2af9385498eb8eff08c1fc017edb07007930d72d20`); `5476807B` is the same closure without the near-camera refraction term, under the pass-1 constant layout.

This page decodes the output colour and alpha of `F4D47ECE`, including the two loops the static translator keeps opaque, and checks every term numerically against one captured Novalis frame. The viewer and Blender rule built from it is described under "Viewer rule" (see also [liquids](../liquids.md) and [viewer](../viewer.md)). `rc2016/shaders/water.py` implements the closed forms; `tests/test_water_surface.py` checks them.

Labels: **decoded** means read from the program, the material or the capture, with the evidence given; **inferred** means a reading the evidence does not fix.

## Which draws are the water

In the captured frame (shadPS4 RenderDoc, camera at native 140.07, 63.60, 151.75, `CB0[99]` = 48.35) four draws bind the graph's writers. Each is tessellated (the domain program displaces the vertices with the two height tiles) and each follows a full-screen copy of the lit target at half resolution, which the writer reads as its refraction and screen-space reflection source.

| Draw | Writer | Pixels changed | Placement (decoded) |
| --- | --- | --- | --- |
| 15931 | `F4D47ECE` | 84,413 | `art_terrain` instance 3594 at (186.892, 38.273, 94.439): the river |
| 15903 | `F4D47ECE` | 0 | instance 3382, the same position with the Y axis mirrored: the underside, culled from above |
| 15130 | `5476807B` | 19,118 | instance 160 at (507.597, 41.616, 84.235): the far sea |
| 15390 | `F4D47ECE` | 0 | a 2,376-index mesh at y = 38, not joined |

The join is exact: the per-draw instance record (pixel binding 0) holds the world matrix, and it equals the zone placement's matrix.

## Method

The loops are opaque to the translator: the first (at 3196) walks the clustered light list, the second (at 12144) is a 40-step screen-space reflection march. Two checks replace a static loop decode:

- **Record flags.** The instance record's flag word (dword 60) is `0x2001` for all four draws: the screen-space bit `0x2000` is set, the lit-diffuse and lit-specular bits (`0x20`, `0x40`) are clear. The light-list walk is gated by `flags & 0x60` and is skipped, so its contribution is zero for these draws (decoded). The SSR march runs.
- **Whole-program execution.** The draw's SPIR-V (the shadPS4 translation of the same GCN code), cross-compiled to GLSL and run in Python with every buffer, texture and sampler of the replay at the draw, reproduces the captured fragment output (pixel history) at 240 river pixels: median relative error 0.3 %, mean colour within 0.2 %. The same run of `5476807B` at the far sea matches 160 pixels with a median error of 0.08 %.

The closed form below, evaluated from the program's inputs, equals the whole-program values term by term at all 240 pixels (double-precision agreement). The alpha also matches the loop-opaque translation with random inputs.

## Closed form

Notation: `X`, `Z` are native world coordinates (`CB0[28] - attribute3.x`, `CB0[30] - attribute3.z`; attribute3.xyz is the camera minus the world position, decoded from the domain outputs and the camera block), `t` = `CB0[99]`, `d` = |attribute3| the camera distance, `V` the unit vector to the camera, `N` the interpolated geometry normal, `T` the interpolated tangent, `B = sign(attribute2.w) N x T`, `W` the fragment's view depth. API 5 and 6 are the height tiles (`water_tile_01`, `_02`, BC4), API 7 and 8 the normal tiles (`_01n`, `_02n`, RG), API 9 and 10 the foam tiles (`water_beachfoam_tile_01`, `_02`, R). Parameters are the pass-0 `CB2` words listed under "Material parameters".

| Term | Value |
| --- | --- |
| First height pair, first normal pair | the sampling contract in [liquids](../liquids.md): `h5`, `h6` at (0.105 X + 0.252 t, 0.095 Z - 0.246 t) and (0.248 + 0.095 X - 0.247 t, 0.613 + 0.105 Z + 0.255 t); `hh = h5 + h6 - 1`; normal pair A at the API 7 and 8 UVs displaced by `CB2[36] hh` |
| Second pair | API 8 normal and API 5 height at (0.0475 X + `CB2[12]` hh + 0.052 t, 0.0525 Z + `CB2[12]` hh + 0.048 t); API 7 normal and API 6 height at (0.133 + 0.0525 X + `CB2[12]` hh - 0.049 t, 0.346 + 0.0475 Z + `CB2[12]` hh - 0.051 t) |
| Crest | `c = sat(CB2[14] (1 - H1)(1 - H2))`, `H1` = API 5 at (0.116 + 0.002625 X + 0.0099 t, 0.002375 Z + 0.011 t), `H2` = API 6 at (0.346 + 0.002375 X - 0.012 t, 0.133 + 0.002625 Z - 0.008 t) |
| Normal strengths | `q1 = sat(c lerp(CB2[15], CB2[15] CB2[47], f))`, `q2` the same with `CB2[17]`, `f = sat(d / CB2[13])` |
| Normal | each sample `s = q (2 rg - 1)`, `n = normalize(N (1 - s.s) + B s.y + T s.x)` (no absolute values); `N' = normalize(normalize(n1 + n2) + normalize(n3 + n4))`, pair A with `q1`, pair B with `q2` |
| Thickness | `D - W`, `D` the scene's linear view depth at the refracted screen UV; the refraction offset `(1 - edge) lerp(CB2[3], CB2[7], sat(d / CB2[18])) (2 sat(h + h' - 0.5) - 1)` is at most 0.0015 here (sub-pixel) |
| Alpha | `cover = sat(near Z (1 - F))`, `near = sat((d - CB2[10]) / (CB2[11] - CB2[10]))`, `Z = sat(CB2[8] thickness)`, `F = sat((1.001 - sat(N.V))^CB2[9])`; `alpha = sat(attribute0.w cover)`; discarded when `cover < attribute0.z` (4/255) |
| Opacity | `attribute0.w` is record dword 66, the zone instance record's byte `0x98` / 255: 102 (0.4) on the river, 242 (0.949) on the sea and the underside (three draws) |
| Foam | `G = CB2[19] (API5 at the API 8 UV + API6 at the API 7 UV) + CB2[20] (h at the second pair)`; `m = sat(sat(F1 F2) sat(sat(G)^CB2[33]))`; `foam = sat(CB2[45] m (1 - sat(CB2[35] thickness^CB2[34])))`, so foam exists only where the water is shallower than about 2.4 units; `F1`, `F2` at (0.116 + 0.05 `CB2[25]` X + `CB2[27]` hh + `CB2[29]` t, 0.05 `CB2[26]` Z + `CB2[27]` hh + `CB2[30]` t) and (0.346 + 0.05 `CB2[26]` X + `CB2[28]` hh + `CB2[31]` t, 0.133 + 0.05 `CB2[25]` Z + `CB2[28]` hh + `CB2[32]` t) |
| Reflection | `R = 2 (N'.V) N' - V`; `P` = global probe (cube-array slot 255, mip 0) at `R`; `refl = (CB2[44] spec + sat(CB2[39] (1 - c) lerp(A, B, c) + CB2[40] P (1 - sat(CB2[43])) + sat(CB2[43]) lum(CB2[40] P))) (1 + sat(d / CB2[21]) (CB2[46] - 1))`, `A` = `CB2[0..2]`, `B` = `CB2[4..6]`, `lum` = (0.3, 0.59, 0.11); `spec` (the light list) is 0 on the captured record path |
| Screen-space reflection | weight `w = CB0[111] sat(2.5 R_view.z + 0.5) min(0.8, -0.1 log2(g) - 0.2)` times screen-edge fades and a hit test (`g` from the gloss `lerp(CB2[22], CB2[22] CB2[23], sat(d / CB2[21]))`); colour `0.1 x` the fogged half-resolution scene colour at the hit, reprojected by the motion vectors |
| Near-camera term | `(1 - e) lerp(S, lerp(A S, B, k), k)`, `S` the half-resolution scene colour at the refracted UV, `k = lerp(CB2[41], CB2[42], sat((D - CB2[37]) / (CB2[38] - CB2[37])))`, `e = sat(3.578 W - 2.333)` (record dwords 63, 62): zero beyond a view depth of about 0.93 |
| Colour | `rgb = fog + T' (foam + near-camera term + lerp(refl, ssr, w) - fog)`, `T' = T + w (1 - T)`, `T` = attribute3.w (vertex fog transmittance), `fog` = `CB0[112..114]` |

The river is therefore a 40 % blend over the lit river bed: the water's own colour is the crest-tinted environment reflection, darkened by the screen-space reflection of the banks, plus shore foam. The body colour `B` (key `0x1DAF77B8`) only tints the crest scatter; it is not a diffuse albedo.

## The captured river

Means over the 240 checked pixels: foam 0.019 (above 0.05 on 11 % of the pixels; 22 % are shallower than 2.4 units), reflection (0.099, 0.161, 0.174), SSR weight 0.39 with an SSR colour of (0.0096, 0.0098, 0.0063), alpha 0.398, `F` below 1e-4, `Z` 0.996, thickness median 5.3. The water's colour is (0.0825, 0.1216, 0.1287); without the screen-space reflection it would be (0.118, 0.180, 0.193). The river bed under it averages (0.032, 0.041, 0.033) and the blended result (0.051, 0.072, 0.070), scene-linear before exposure.

The probe the reflection reads is the global slot 255 of the cube array. Its face means equal those of the atmosphere zone's reflection capture (`textures/atmosphere/volumereflections/0xcfd50205b5044662`) within 9 % on five faces; the downward face is darker in the capture (0.26 against 0.74).

## Material parameters (pass 0)

| Key | Dword | `pok_uniq_water.material` | Role |
| --- | --- | --- | --- |
| `0x56CB709A` | 0..2 | (0, 1, 0.8228) | `A`, the crest scatter's shallow colour |
| `0xE125A9BA`, `0xC5F0775B` | 3, 7 | 0.0015, 0 | refraction offset near, far |
| `0x1DAF77B8` | 4..6 | (0, 0.1779, 0.2159) | `B`, the crest scatter's body colour |
| `0xD50FF59A` | 8 | 4 | depth-fade slope |
| `0x66DF5298` | 9 | 600 | grazing exponent |
| `0x575A5C8E`, `0xDC287E64` | 10, 11 | 0.375, 0.5 | near-camera fade |
| `0xE706F97C` | 12 | 0.025 | second-pair displacement |
| `0xAAED7A8B` | 13 | 144 | normal-strength distance |
| `0x266F5E89` | 14 | 2 | crest scale |
| `0x8EC1A779`, `0x60402AB7` | 15, 17 | 0.25, 0.5 | normal strengths |
| `0x8493E615` | 16 | 100 | refraction edge exponent |
| `0x3781CAB8` | 18 | 48 | refraction distance |
| `0xA98713DA`, `0xB9E7ED97` | 19, 20 | 0.333, 1 | foam height weights |
| `0x4ACC9AF0` | 21 | 144 | reflection and gloss distance |
| `0x24F4CA4C`, `0xE9D9BF9F` | 22, 23 | 1, 0.125 | gloss, far gloss factor (SSR and light list) |
| `0x3B41A2DA` | 24 | 0.5 | not read by `F4D47ECE` |
| `0xE61BBA8C`, `0x608FC822` | 25, 26 | 2.625, 2.375 | foam UV scales |
| `0x144F65DB`, `0x53EF1F0B` | 27, 28 | 0.017, 0.025 | foam UV displacements |
| `0xF9F2E56C` .. `0x59768813` | 29..32 | 0.031, 0.021, -0.021, -0.031 | foam scrolls |
| `0x4E58797E`, `0xD3AFCBB7`, `0x097B0E99` | 33, 34, 35 | 6, 3, 0.075 | foam exponent, thickness exponent, thickness scale |
| `0x230C6389` | 36 | 0.025 | first-pair displacement |
| `0x11522758`, `0xBDEDD1CB` | 37, 38 | 12, 48 | near-camera depth ramp |
| `0x897E48FA` | 39 | 0.25 | crest scatter weight |
| `0xE93F97D5` | 40 | 0.2 | environment scale |
| `0x71B86B09`, `0x4DB55450` | 41, 42 | 0.333, 1 | near-camera mix range |
| `0x9C360E85` | 43 | 0 | environment luminance mix |
| `0x8AC26FB9` | 44 | 0.5 | light-list specular weight |
| `0x339D4B95` | 45 | 1 | foam scale |
| `0xA46F1D8D` | 46 | 0.75 | far reflection factor |
| `0xBBBA496D` | 47 | 0.5 | far normal-strength factor |

The values are the material's instance block where it has one, else the graph default; they equal the `CB2` words the captured draw bound.

## Viewer rule

The exact graph hash binds the decoded closure (`forward` kind `water` with a `surface` entry; `GLASS_FRAGMENT` `uKind` 4 with `uE.z` = 1, `water_surface_uniforms`). It draws every term above except the inputs a viewer frame does not have:

- **Probe:** the viewer's environment at its sharpest band (Level atmosphere: the same reflection capture; elsewhere the review HDRI or the rig sky). At the river's reflection directions it is on average 0.81 times the captured probe sample.
- **Screen-space reflection:** not drawn (weight 0), so the reflection is not darkened where the game reflects the banks.
- **Scene depth:** the level viewer copies the frame's depth buffer before the forward pass, which gives the thickness, the shore fade and the foam; its thickness matches the capture's per pixel (median ratio 1.01). The model viewer and Blender have none (no shore fade, no foam).
- **Opacity:** one labelled value per material, 0.4 (the river instance); the per-instance byte `0x98` is not carried to the viewers, so the far sea (0.949 in the game) draws too transparent.
- **Time** `CB0[99]` is frozen at 0; the near-camera term is not drawn; the tangent frame is native world X (the game uses the mesh tangent, the instance's model X axis); the colour is tonemapped before blending.

Blender builds the same samples, crest, strengths and four-sample normal (tangent space, then the UV-tangent normal map), the crest scatter as an emission and the environment reflection as a mirror glossy lobe weighted by `CB2[40]` and the distance factor (the sum is not clamped), and the alpha without the depth fade. It is checked by `validation._validate_viewer_rule_water`. Neither front end is game-equivalent.

## What stays open

- **Other record flags.** The lit-diffuse and lit-specular paths (`0x20`, `0x40`: the light list) and the flag `0x80` alpha path are not exercised by any captured water draw.
- **Per-instance opacity in the viewers.** The decoded byte needs a per-instance attribute in the level instancing.
- **Screen-space reflection.** Its march and reprojection are read from the program but not ported.
- **Probe selection.** Only the global slot is seen; local probes (the light-list branch's cube-array blend) are not exercised.
- **Translator input.** The loop-opaque translation reads the geometry normal (attribute1) as two components with z dropped, where the shadPS4 program reads three; on the water plane z is 0.004, so the alpha agrees.
