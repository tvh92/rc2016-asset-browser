# Novalis window glass: forward program `0x8df14766`

`jn_glass_parallax_mask` (graph SHA-256 `bc5bb839d7b389638d55b0bb8784798aade5215aa31e5fd692bdf970ffd89c2c`; materials `tb_nov_window_parallax_01` to `_04` and `_01_no_shad`, flags `0x230`: opaque, back faces culled) has a viewer and Blender rule (`window_glass`, `GLASS_FRAGMENT` `uKind` 14, `game_shader._viewer_rule_glass`, validated by `validation.GLASS_CONSTANTS`). It is bound to the exact graph hash and to the forward program `0x8df14766` (code SHA-256 `84ecf283…70a3d`, pass contract 0). It is a labelled preview, not a game-equivalent render (`game_equivalence = False`). Archive-backed checks of the bindings below are in `tests/test_window_glass_rule.py`.

## Method

The program walks the engine light list, which the translator refuses. The terms below were read from a GCN interpreter run of the real program on a captured Novalis frame, rewritten as independent formulas and compared with the executed values (six captured pixels plus synthetic inputs). The interpreter reproduces the captured shader output to about 0.1 % at most pixels; at some pixels the red channel is off by up to about 30 %, which follows the interior texture's sampler (2x anisotropic wrap filtering, not fully reproduced). Nothing in the repository runs the program; the tests pin identities, slots and key layout.

## Inputs

Texture slot `i` of pass contract 0 is API `5 + i`:

| API | Slot key | Content | Coordinates |
| --- | --- | --- | --- |
| 5 | `0x0056CBD0` | mask (R) | UV1 |
| 6 | `0x641CFAC2` | window normal map (RG) | world projection |
| 7 | `0x1576D848` | glass `_g` (R gloss, G reflection weight) | world projection |
| 8 | `0x7AE34E7A` | detail normal map (RG) | UV1 |
| 9 | `0xABDAD780` | painted glass `_c` | world projection |
| 10, 11 | `0xB521DFCB`, `0x40A7D737` | interior `_c`: alpha is the height, RGB the colour | UV0, parallax-shifted |

CB2 dwords (key): 0..2 painted-glass colour `C` (`7F058428`), 3 gloss (`24F4CA4C`), 4..6 mask tint `Z` (`93C5FD2C`), 7 sky weight (`EBF9478B`), 8..10 reflection tint `T` (`4B4DAB19`), 11 parallax depth (`5DB5EFC7`), 12 glass weight (`AF4BB549`), 13 rim exponent (`ECE213A3`), 14 reflection weight (`62835AF9`), 15 interior weight (`EE0F9FF8`), 16 interior light (`1B128294`).

## Decoded terms

`n` is the geometric normal, `V` the unit vector to the camera, `f` = +1 front face, -1 back face, `W` the native world position.

- **World projection** (APIs 6, 7, 9): `0.075 (u, v)` with the dominant axis of `n` and `s` its sign: x: `(-s W.z, -W.y)`; y: `(W.x, s W.z)`; z: `(s W.x, -W.y)`.
- **Interior parallax**: `D = CB2[11] f (V.T, -V.B) / (n.V)`; `N = int(64 - 48 sat(n.V))` steps `uv_k = uv0 - k D / N` against heights `1 - k / N` read from the interior alpha with the UV0 gradients; the first sample above the ray and the previous one give the secant height `h*`, and `uv11 = uv0 - (1 - h*) D`.
- **Colour** per channel: `S = P Q + spec`, `P = CB2[16] T11.b + A`, `Q = CB2[4+c] mask + CB2[15] T11 + CB2[12] C (0.5 + mask)^5 T9 (1 + CB2[7] sky)`, `spec = k (Wocc E F + sun and light specular)`, `F = CB2[14] T7.g + rim^10`, `rim = sat(4 (1 - sat(f n.V))^CB2[13])`, `k = sat(T / luma(T))`. Gloss `g = sat(0.5 + CB2[3] T7.r - 2 mask)`, GGX alpha `2^(-5.5 sqrt g)`. `A` is the probe ambient and sun diffuse, `E` the probe and cube reflection, `Wocc` the specular occlusion from the screen-space ambient occlusion. A screen-space reflection is blended in after this sum.
- **Shaded normal**: the window map (world projection) and the detail map (UV1) are combined in the tangent frame; the detail map fades with distance.

- **UV1** is the second binary16 pair of the 8-byte UV0 vertex record (`native_model.secondary_uv`, not V-flipped). On the captured window draw it equals the program's `attribute1.xy` at all nine vertices of an instance (models `tb_nov_bldg2_mid_window` and `_distant`). The window meshes have no separate UV1 chunk. The pair is not established as UV1 for other writers.

## What the preview draws

The viewer binds all seven slots: the interior in the `_n` unit (mipmapped; sRGB from its native format), the glass `_g` in the `_pick` unit, and the mask, window normal, detail normal and painted-glass `_c` on texture units 10 to 13 (`shading.WINDOW_GLASS_TEXTURES`, roles `_window_mask`, `_window_n1`, `_window_n2`, `_window_c`; mipmapped; the mask and `_c` take their sRGB flag from the native format, the normal maps are Non-Color). The level geometry (`uv1s`) and the model viewer payload carry UV1 as vertex attribute 3 (`aUV1`).

The rule computes the parallax march, the world projection, rim, `F`, gloss with the mask term, `k`, `Q` with the mask tint and the painted-glass layer, and the shaded normal `N'` in its coded form: the world-projected window map moved to the projection axis, combined in the tangent frame with the detail map `m2 = S2 (T8.rg - 0.5)`, `S2 = 2 CB0[95] (1 - sat(w rec[3] + rec[2]))`, `w` the view depth. `CB0[95] = 1` and `rec[2], rec[3] = -2.3333, 0.0071561` are the captured window values (presentation inputs). `N'` drives the lights, the environment and the sky lookup; the rim and the parallax use the geometric normal, as decoded. The tangent `T` comes from screen derivatives of the world position and UV0 (the level geometry carries no tangent stream); the bitangent is built as the program builds it from the vertex tangent, `B = h normalize(n x T)`, with `h` the side of the derivative bitangent, for both `N'` and the parallax. With the orthonormal frame the result does not depend on how `T` turns in the surface plane except through the small detail-map term: given the game's map samples, the viewer frame reproduces the program's `N'` within 0.04 degrees at the six captured pixels, where the derivative bitangent was off by up to 6.5 degrees at one of them. The cube-255 sky sample is the viewer environment along the reflection `R` of `V` about `N'`, and the sun and light specular use the viewer lights through the glass GGX lobe with `F0 = F`, `F90 = sat(60 F)`.

**Ambient and sun.** In the level viewer with the lightgrid bound, `A` is the program's probe-volume ambient `A'` at `N'` ([levels: probe ambient](../levels.md#probe-volume-reflection-probe_volumepy)), with confidence 0 (no cube-255 irradiance) and `CB0[116] = CB0[117] = 1` as captured; it equals the program's value at the six captured pixels to 0.3 % in the viewer read-back. The key light's diffuse and specular are multiplied by the viewer's sun cascades evaluated at the fragment with the geometric normal (`sceneShadow`, the level glass program only). The program reads `T43.R` there; in the capture `T32` and `T43` are the full-resolution R8 screen targets bound as `fs_img0` and `fs_img8`, and the half-resolution depth at the window pixels equals the window's own depth, so both are computed on the window surface itself. The viewer's screen AO is computed without forward materials and is therefore not a stand-in for `T32`: ambient occlusion stays 1. Elsewhere `A` is the viewer diffuse light (rig or HDRI ambient and lights) and the sun is unshadowed.

**Reflection `E`.** In the level viewer, with the Level atmosphere lighting and the scene-effect programs, `E` is the decoded probe-volume reflection ([levels: probe-volume reflection](../levels.md#probe-volume-reflection-probe_volumepy)): the lightgrid cell's selector picks the level's archive probe cubes, `E = (1 - m) sum w_i [mix_i cube[A_i](R) + (1 - mix_i) cube[B_i](R)] + m cube[255](R)`, with the viewer environment along `R` for cube 255. Elsewhere (model viewer, other lighting sources, scene effects off) `E` is that environment. Given the game's position and reflection vector, the viewer's `E` matches the program's within 1 % at the pixels whose cube LOD is 0 or below.

**Against the capture.** At the captured frame's camera, over the six captured window pixels (mean `|log2(viewer / capture)|` over 18 channel values): the environment reflection alone gave 0.97 stops, the probe-volume reflection 0.34, and with the probe ambient, the sun cascades and the program's bitangent 0.24 (per pixel 0.37, 0.20, 0.25, 0.17, 0.30, 0.08). With every engine term at the game's value the same decomposition reaches 0.05, the interpreter's own residual. What remains: the reflection vector (the shaded normal is now within 0.3 to 0.7 degrees; the difference is in the normal-map samples, which the viewer filters with its own mipmapped anisotropic sampler, against the game's 2x anisotropic one, and the mip-0 cubes are sharp enough that this moves `E` by up to 30 %), the undecoded confidence (`m` 0.13 and 0.19 at two pixels in the game, 0 in the viewer), the missing cube mips, the screen-space reflection at one pixel and the interior's sampling. Given exact ambient occlusion alone the error rises (0.34 stops), because it is only consistent with the game's `E'`; it is not drawn in any case (no source).

## Limitations (kept visible in the rule and the Blender material)

- The sky cube of the painted-glass layer and the sun colour and direction are the viewer lighting; no screen-space reflection (`r = 0`) and no engine light list.
- The probe reflection and probe ambient are read only in the level viewer (Level atmosphere, lightgrid and scene effects on): cubes at mip 0 for every gloss (the runtime mips are not decoded), the runtime confidence byte taken as 0 (local probes alone, no cube-255 irradiance in the ambient), the viewer environment for cube 255, and `CB0[116]`, `CB0[117]` as the captured 1. Elsewhere both are the viewer lighting.
- The sun visibility `T43` is the viewer's own sun cascades at the fragment (forward materials cast no shadow there), 1 outside the level viewer.
- An unresolved slot or an interior height and colour bound to different images fails the rule.
- Ambient occlusion `T32` is taken as 1 (the viewer has no screen AO of the window surface), so `Wocc = 1` and the light-list factor `0.5 ao + 0.5` is 1.
- The tangent (parallax and `N'`) comes from screen derivatives, not the vertex tangent; the bitangent is `n x T` as in the program. Outside a level the world projection uses the model's own space. A mesh without the second UV pair draws no mask and no detail normal.
- The interior and the normal maps use the viewer's mipmapped anisotropic filtering; the game samples them with 2x anisotropic wrap filtering.
- The program variant of pass contract 1 (`0x140bfec8`, without the detail normal, rim and mask tint) is not selected.
- Blender samples the interior at the unshifted UV0 (no parallax march), has no second UV pair (mask 0, no detail normal), does not build the window normal (geometric normal), adds the painted-glass layer with mask 0 and sky 0 when the `_c` image is in the material, uses object space for the world projection, multiplies the tint into `F` and lets the Principled BSDF supply the environment reflection.
