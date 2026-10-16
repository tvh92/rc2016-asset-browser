# Ratchet character forward passes

Four items of the Ratchet character stay undecoded in the viewer and in Blender: the skin forward term (`F0CD85FF`), the fur shells, the eye cornea light loop and the `hero_ratchet_face_parts` skin. This page records what the shader programs prove, which engine inputs keep the rest out of reach, and why nothing new is drawn. No rendering rule changed with this decode. Archive-backed checks of every binding below are in `tests/test_forward_programs.py` (skipped without `RC2016_RESEARCH`); the descriptor tables are read by `rc2016/research/forward_programs.py`.

The forward programs were read from the cached SPIR-V of the programs, decompiled with spirv-cross, and cross-checked against the GCN in the archive: the texture of every `IMAGE_*` instruction comes from the `S_LOAD_DWORDX8` at dword offset 8 x API of the resource table, so the `fs_imgN` order of the decompilation (first use) is not the API order. "Verified" below means the GLSL statements were evaluated mechanically and compared with the closed form.

## Which program reaches which item

| Item | Material | Graph | Forward program | Deferred writer |
|---|---|---|---|---|
| Ratchet body | `hero_ratchet_body` | `basic_normla_gloss_fresnel_detail_emissive` | loop programs `A7528F09`, `CB65EF13` | `14B1CC68`, `3A9A3C3C` (decoded) |
| Ratchet head | `hero_ratchet_head` | `basic_normal_gloss` | loop program `37AF5B69` | `5EBDECA4` (decoded) |
| Skin forward term | enemy skins (`enm_puffer_fish` and others) | `basic_skin` | `F0CD85FF` (also `ADD87F78`) | `9AAD640B`, `D1C874F4` (decoded) |
| Face parts | `hero_ratchet_face_parts` | `test_basic_skin_05_newsss_03` | `921420CD` and `54623CD4` | `11408DE9`, the zero-output placeholder |
| Eye cornea | `hero_eye_cornea`, `hero_eye_cornea_ratchet` | `hero_eye_cornea` | `A36A97CB` and `AE044D65` | `11408DE9`, `F900D315` |
| Fur shells | `test_hero_ratchet_head_fur`, `_ear_1`, `_ear_2` | none | none in the archive | none |

`F0CD85FF` is in `basic_skin.materialgraph` only. Ratchet's body and head do not use it; the skin forward term affects enemy and NPC skins. Ratchet's own skin is drawn by the deferred writers already decoded.

## What every forward program reads from the engine

All five forward programs (`F0CD85FF`, `921420CD`, `54623CD4`, `A36A97CB`, `AE044D65`) share one lighting structure and read the same engine inputs:

- **Light list.** A tile table indexed by the pixel's tile (screen position scaled by CB0 dwords 44-47, tile counts in CB0 dwords 245 and 246) gives the head of a linked list. Each node holds a 16-bit near/far depth range, a 24-bit next index and an 8-bit light index; the loop stops at the end marker or after 256 nodes. The list is built per frame on the GPU. Nothing in the archive describes it.
- **Light records.** 64 dwords per light. Roles proven by the arithmetic: 6-8 axis (spot) or direction (type 3), 9-11 position, 44-45 cone scale and bias (`sat(d44 (-axis.L) + d45)^2`), 47 skin ramp enable, 52-54 colour, 55 specular gain, 56 inverse radius (`sat(1 - d56 |r|)^d60`; for `d60 > 2` an inverse-square form), 58 diffuse gain, 60 falloff exponent, 61 type (3 directional; 2 a box-volume light handled in a separate branch that only scales the colour accumulators; otherwise positional), 12-23 and 63 (a 3 x 4 bounds test, enabled by dword 63). Also read, role not named: 0-5, 24-43 (projection rows of the cookie and shadow lookups), 48-51 (cookie scale and bias), 59 (shadow offset added to the PCF sum), 62 (shadow variant). These values exist only in the running game.
- **Frame constants (CB0).** Camera position (28-30), screen scale and bias (44-47), normal-scale fade (95), global light colour (124-126) and direction (128-130), flag word (135, bit 64 enables the occlusion texture), tile counts (245, 246), plus view, projection and ambient constants (16-26, 36-43, 72-91, 111-121, 139-170).
- **Pass textures.** Resource-table indices 3 and 4 and the literal block: 32 and 34 and 43 screen-space textures, 33 the dissolve dither (`min(1, t) < vertex alpha` discards), 35 the light cookie atlas, 36 the shadow map (comparison sampler), 37 and, in the cornea, 41-42 environment lookups. Material textures are APIs 5-13 of the pass contract.
- **Per-instance record** (CB2 from byte 240, 272 bytes per instance): its first dword is read by every program (bit 0x80 dissolve, 0x20 and 0x40 enable the lit paths, 0x2000 a sign used by the screen-space trace).
- **Material table.** Read as CB1 by `F0CD85FF` and `921420CD`, as dwords 0-13 / 0-8 of CB2 by `54623CD4` and the cornea programs.

Without these inputs a forward result cannot be game-equivalent. The draw-time choice between the two programs of a graph (`921420CD` and `54623CD4`, `A36A97CB` and `AE044D65`) is also not in the archive.

## Skin forward term (`F0CD85FF`)

Table-0 dwords of the program (pass contract 0 of `basic_skin`), with the keys the reading proves:

| Dword | Key | Role in the term |
|---|---|---|
| 0-2 | `0xCFCCABE4` | `tint`, per-channel (graph default `(0.6376, 0.1170, 0.1221)`) |
| 6 | `0x58DF74DE` | ramp weight `p6` (default 0.4) |
| 8 | `0xB79779EC` | rim gate roll-off `p8` (default 3) |
| 11 | `0x3055F730` | V coordinate of the skin ramp |
| 12 | `0x991DAC4F` | wrap strength `p12` (default 0.7) |
| 15 | `0xEAF30A93` | rim gain `p15` (default 0.4) |

The material textures are API 5 `_g`, 6 `_n`, 7 secondary normal, 10 `skin_ramp` (256 x 128, grey, sRGB format 29, `required/textures/skin_ramp.texture`) and 11 `_c`. Per light, with `N` the shaded normal, `Ng` the interpolated geometric normal, `V` the unit vector to the camera, `L` the unit vector to the light (the negated direction for type 3), `C` the light colour already multiplied by its distance/cone falloff and cookie (type 3: the record colour), `att` the cookie-red times falloff (type 3: 1), `S` the product of the light-volume test and the shadow factor, `d` the camera distance and `A2 = (_g.A)^2`:

```text
u      = (record[47] > 0 ? S * att : 0) * sat(0.05 + Ng.L)
W      = 0.66 * p6 * A2 * (1 - sat(0.2 d - 2))
ramp   = skin_ramp(u, V_key).x
s      = (front face ? -1 : 1) * dot(CB0[128..130], Ng)
gate   = A2 * sat(p15 * (1 + s - p8 * sat(s)))
wrap_c = 1 - (0.767, 0.545, 0.351)_c * p12
diffuse_c += record[58] * ( C_c * sat(Ng.L)
                          + wrap_c * C_c * (sat(N.L) - sat(Ng.L))
                          + tint_c * C_c * ramp * W
                          + sat(1 - N.L)^2 * C_c * tint_c * gate )
```

The statements `_2007`-`_2163` of the decompilation (the ramp coordinate through the three diffuse channels before the record[58] gain) agree with this form to a relative 8e-10 over 2000 random inputs, including the front/back sign, the `record[47]` gate and the distance fade. Notes the evaluation makes explicit:

- The shadow factor `S` reaches the diffuse sum only through the ramp coordinate (and the specular lobes). The wrap and rim terms are not shadow-gated in this program.
- The base diffuse uses the geometric normal; the shaded normal only enters through the per-channel wrap factor `(0.767, 0.545, 0.351)` and the rim term.
- The specular lobe of the same loop is a two-lobe GGX mix (alpha `a = 2^(-11 sqrt(gloss))`, weights 0.85 and 0.15 for `a` and `a^2`) with a Schlick-Smith visibility `k = 0.3536 sqrt(a) + 0.3536`, a Schlick Fresnel on `H.V` and gain `record[55]`. It was read, not executed.
- The global light (CB0 124-130, with probe-cube ambient from the pass cube arrays) feeds the same ramp equations with `L` from CB0 and `S` from the cascaded shadow map and screen-space occlusion.

Engine inputs that keep it out of the viewer and Cycles: the light records, the tile list, the shadow and occlusion maps, the probe cubes and CB0. Cycles has no per-light shading hook, so a faithful mirror is impossible and the viewer rule would have no Blender counterpart; the term therefore stays a documented limitation of the skin rule (see [shading](../shading.md)). It does not touch Ratchet.

## `hero_ratchet_face_parts` (`test_basic_skin_05_newsss_03`)

The graph has no usable deferred writer (`11408DE9` exports zeros), so the game draws the material through the forward programs. `921420CD` follows pass contract 0 (table in CB1) and samples the APIs below. `54623CD4` follows pass contract 1 (14 dwords in CB2, a different order: dword 3 `0x9F13A7F7`, 4 `0xC791F107`, 5 `0xDBFB7604`, 6 `0x77137A48`, 7 `0x0A2BC3CF`, 8 `0x79669512`, 9 `0xEE9D811B`, 10 `0x34A4D7B5`, 11 `0x00904B77`, 12 `0x1F5AC3C8`, 13 `0xCB6AA161`) and samples APIs 5, 6, 7, 9-13 with API 7 as the colour slot (`0xABDAD780`) and API 8 not sampled.

| API | Slot key | Role read from `921420CD` | Bound for `hero_ratchet_face_parts` |
|---|---|---|---|
| 5 | `0x93CA35AB` | `_g`: R gloss, G specular, B detail weight | `ratchet_eye_g` |
| 6 | `0xDF2B319A` | `_n` (light normal, Fresnel normal) | `ratchet_eye_n` |
| 7 | `0x09431F90` | blurred normal, mip-sampled, scale 1.5 | `default_pink_shader_n` (flat normal) |
| 8 | `0xED37E1B2` | thickness/curvature map `.x` | `default_pink_shader_c` (grey 0.5) |
| 9 | `0x06F649B0` | `skin_integrated_curvature` look-up (RGB at three coordinates) | graph default |
| 10 | `0xBA8D573E` | detail normal at UV x 24 | graph default `test_skin_detail_03_n` |
| 11 | `0xCEA7EB79` | detail variance at UV x 24 | graph default `test_skin_detail_03_v` |
| 12 | `0xABDAD780` | `_c` | `ratchet_eye_c` |
| 13 | `0x67D6383E` | normal for the albedo Fresnel and rim | graph default `test_scans_bobby_03_n` |

The shader is a pre-integrated subsurface skin: per light it builds per-channel diffuse normals from mip-offset samples of API 7 (offsets from table dword 7) and reads the curvature look-up per colour channel. The specular F0 and gloss read as the same Fresnel form as the deferred `basic_skin` writers, from contract-0 dwords 3 (cap), 4 (gloss Fresnel), 5 (specular Fresnel), 6 (exponent), 11 (gloss scale) and 13 (specular scale), and the albedo as `_c` times a Fresnel lerp between dwords 0-2 and 12 plus a `(0.78, 0.96, 1.02)` grazing tint (all read, not executed). The material sets the gloss scale (`0x1F5AC3C8`) to 200, the specular scale (`0xCB6AA161`) to 0.1 and the albedo Fresnel scale (`0x9F13A7F7`) to 0.

Why no rule: (1) the pre-integrated diffuse is evaluated per light inside the loop, so it needs the light list; (2) the viewer's deferred BRDF has no per-channel diffuse normals; (3) the draw-time choice between the two programs is unknown and they bind the material table in different layouts; (4) three of the texture slots resolve to test assets (`test_scans_bobby_03_n` shapes the albedo Fresnel), so a game-equivalent rule would have to freeze a test texture. The viewer keeps the labelled prefix reading of the basic_skin folder for this material (gloss `sqrt(_g.R)`, grey specular `min(0.5, _g.G)`); its gloss and specular scale are inferred, not decoded. The decoded constants above show that the shipped material sets very different scales (specular x 0.1, gloss x 200).

## Eye cornea light loop (`A36A97CB`, `AE044D65`)

The material-dependent parts are already decoded and drawn (Fresnel alpha, dome-normal reflection weight, see [viewer](../viewer.md#eye-cornea)). The remaining part of `A36A97CB` is the shared light loop above: its per-light accumulators (same record fields and branches) are combined at the end with the sampled lens red `shared_eye_lens_c` (API 6), the environment lookups and the global light. The per-light arithmetic was not mechanically verified. `AE044D65` samples no material texture. Both need the light list, the shadow map and the probe cubes, so no light-dependent term can be reproduced exactly. The viewer and Cycles show the highlight through their own lights, which is a review approximation and is not labelled game output.

## Fur shells (`test_hero_ratchet_head_fur`)

The three materials are 404 bytes: flags `0x80030` (bit `0x80000` marks fur shells) and four texture references in this order (`ratchet_head_c`, `ratchet_head_n`, `ratchet_head_fur_g`, `hero_ratchet_fur_control`). They have no graph reference, no texture-slot table and no parameter section. The shell count, spacing, length, density, the role of each texture and the program are therefore not stored in the asset. No shader id can be tied to them: the cached programs are keyed by their own id, and the only graph-embedded ones are those of materials that name a graph. The shell passes cannot be decoded from the archive, and the viewer keeps drawing the layer as one opaque surface with the grey `_g` reading for Big Al's sibling material.
