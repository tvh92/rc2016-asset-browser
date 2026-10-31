# Thumper glove glass: forward program `0x5226f404`

`wpn_thumper_glove_glass` (graph SHA-256 `ffec1bf59b2567ad404a016412708a6df957f004250cf276d9c13e725743ad98`, flags `0x00200234`: blend mode 0, two-sided) has a viewer and Blender rule (`glass_thumper`, `GLASS_FRAGMENT` `uKind` 11, `game_shader._viewer_rule_glass`, validated by `validation.GLASS_CONSTANTS`). It is bound to the exact graph hash and to the forward program `0x5226f404` (code SHA-256 `97ce23dd…6b57`, pass contract 0). The rule blends alpha-over and uses the captured `CB0[115]`, both from the captured draws below, not from the flags word. It is a frozen preview, not a game-equivalent render (`game_equivalence = False`). Archive-backed checks of everything below are in `tests/test_thumper_glove_glass_rule.py`.

## Method

The translator refuses the program (`Loop at 1528 not shown to end`). `rc2016/research/forward_loops.py` runs the same translator with each taken backward branch kept opaque: the first pass is used for control flow only and every register it changed becomes a named unknown (`loop-varying VGPR n`). Everything outside the loop is then the translator's own exact DAG. The loop body of `0x5226f404` is instruction-for-instruction the body of `basic_glass` `4FC5703F` (507 against 506 instructions): B lacks one accumulator multiply-add (the blue diffuse channel, whose glow-colour component is 0) and swaps VCC low/high as the sources of one multiply-add pair. So the light-loop lobe documented for `4FC5703F` (`docs/shading.md`, Forward glass writers) applies unchanged. No SPIR-V of this program exists in the shader cache; the decode is from the GCN in the archive.

## Verified closed forms

Evaluated numerically against the program DAG (60 random inputs, error below 5e-7, the float32 literals) with the engine inputs frozen as listed under limitations. `uv` is `attribute0.xy`, `n` the normalised `attribute2`, `T` the normalised `attribute3.xyz`, `B` the normalised `sign(attribute3.w) n x T`, `p` the normalised `attribute4`, `f` = +1 front face, -1 back face. Keys are CB2 dwords of pass contract 0.

| Term | Value |
| --- | --- |
| Pattern sample | `patterns_cma` (API 7 alpha `S`, API 8 rg, one texture) at `uv * CB2[7]` (tiling, `0x375AF9AC`) |
| Normal | `x, y = sat(CB2[10]) (2 rg - 1)`, `w = 1 - x^2 - y^2` (linear, not sqrt); `N' = f normalize(w n + x T + y B)`; `CB2[10]` is `0xCC7457CD` (graph default 0.25 when the material stores none) |
| Rim | `sat(4 |1 - sat(f n.p)|^P9)`, P9 = `CB2[11]` (`0xECE213A3`); geometry normal |
| Alpha | `sat(sat(X + sat(P3 + rim)) attribute0.w)`, P3 = `CB2[3]` (`0x42170E5E`); unused by the opaque blend |
| Noise `X` | `sat(0.454545 (n1 + n2) - 0.363636)`, `fx_noisetile02` (API 5 and 6, one texture, `.r`) at `(u/2 + 0.1 t, v/2 + 1/2)` and `(u/2 - 0.1 t, v/2)` with `t = CB0[99]` |
| F0 | `P8 C S + rim^10` per channel, P8 = `CB2[8]` (`0x62835AF9`), C = `CB2[0..2]` (`0x79120089`) |
| F90 | `sat(60 F0)` |
| Roughness | alpha^2 = `2^(-11 sqrt(g))`, `g = sat(CB2[9] C.r S)` (gloss `0x24F4CA4C`; the red channel of C is what the code multiplies) |
| Tint factor | `k = sat(T / luma(T))`, T = `CB2[4..6]` (`0x4B4DAB19`), luma weights (0.2126, 0.7152, 0.0722) |

## Structure read from the DAG, not verified numerically

Final colour before fog is `k (light-loop and sun and probe specular with F0, F90, N') + K CB0[115] (D + X)`, with `K = (0.39613863825798035, 2.0, 0.0)` (constants of the program; the blue term and its `X` are absent) and `D` the diffuse light sum (light list, sun, probe ambient). Fog, a screen-space reflection blend and the transmittance mix follow. The viewer uses its own rig for the light terms: `k glassSpec(F0, N') + K amp (Ld + X)`.

## Captured draws

A shadPS4 RenderDoc capture of a Novalis frame with the Thumper in view (the glass covers 5510 pixels of the 1920x1080 scene target) draws the glass mesh three times. Program identity is the SPIR-V matched to the shadPS4 shader dumps; buffers are read at each draw's own descriptor offset ([frame constants](frame-constants.md)). Measured:

| Pass | Pixel program | Target | Blend | Depth | `CB0[135]` | Instance flags | Pixels written |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G-buffer | `0xdd9a126f` | three G-buffer targets | off | greater-equal, write on | `0x40` | `0x201` | 109, all on the lower rim |
| Forward, depth equal | `0x5226f404` | scene HDR (R11G11B10F) | off | equal, write off | `0x40` | `0x261` | 108, the same rim pixels |
| Forward, blended | `0x5226f404` | scene HDR (R11G11B10F) | `SRC_ALPHA`, `ONE_MINUS_SRC_ALPHA`, add (colour and alpha) | greater (nearer, reverse Z), write off | `0x2` | `0x261` | 5487 |

All three are two-sided (cull none). So the visible glass is alpha-over on 98% of its pixels although blend mode 0 says opaque: the G-buffer writer keeps a dither-selected rim, the equal-depth pass shades exactly that rim opaquely, and the blended pass covers everything nearer than the stored depth, which excludes the rim. The flags word does not predict this, but one material is not enough to read a new meaning into bit `0x200000` (65 materials carry it, with every blend mode), so `render_state.py` is unchanged and the rule overrides the blend for this exact graph only.

- **Blend alpha.** Solved from pixel history at 16 pixels (front layer): 0.265 to 0.61, mostly 0.27 to 0.39. The floor matches `P3` = 0.272 with `X` = 0 and rim 0, which is consistent with `attribute0.w` = 1. The far side's back faces fail the depth test at most sampled pixels.
- **Frame block.** `CB0[115]` = 0.04464268 (bits `0x3D36DB3F`) at all three draws, `CB0[99]` = 190.82, `CB0[111]` = 1, fog colour `CB0[112..114]` = (1.3038, 3.3024, 4.7033) and exponent `CB0[90]` = 2.08 (the values of the earlier Novalis capture), `CB0[44..45]` = 1/1920, 1/1080.
- **Pass word.** `CB0[135]` is not a debug view: it is `0x40` in the G-buffer and equal-depth passes and `0x2` in the blended pass. The program tests bits `0x2`, `0x40`, `0x80` and `0x1C`; with `0x2` it skips a screen-space sample (API 32, bound to a 4x4 placeholder in the blended draw). The alpha export reads no `CB0` word for any pass word.
- **Instance record.** The program reads dwords 60, 62 and 63 of a 272-byte record indexed by `attribute2.w`: flags `0x261` (`0x1`, `0x20`, `0x40`, `0x200`; `0x80` and `0x2000` clear) and the fade words -2.3333 and 0.071561. Bit `0x2000` gates the block that reads the projection rows, the frame size and `CB0[111]` (the screen-space reflection tail), so that tail does not run for this draw.
- **Material block.** `CB2` holds C = (0.37124, 0.70838, 0.10702), P3 0.272, T = (0.50289, 0.76815, 0.13843), tiling 0.4, P8 3, gloss 0.8, normal strength 0.25 and P9 7: the decoded material values and the graph default.
- **Colour.** The blended pass outputs, before exposure, R 0.003 to 0.010, G 0.009 to 0.035 and B at most 0.001 (G/R 2.7 to 3.6) over a background of 0.001 to 0.01. With `K A` = (0.0177, 0.0893, 0) the glow term `K A (D + X)` explains the green, and a blue channel near zero leaves little room for the tinted specular (tint factor (0.755, 1, 0.208)), which supplies the extra red (inferred split; the light-list sums were not read).
- **Gap of the earlier rule.** It used `CB0[115]` = 0.5, 11.2 times the captured value, and drew the glass opaque instead of at alpha 0.27 to 0.6, which hid the hinge hardware behind the pane. Fog, the reflection tail (off by the flag) and probes do not account for it.

## Limitations (kept visible in the rule and the Blender material)

- `CB0[115]` is a frame constant shared by every draw ([frame constants](frame-constants.md)) that changes between frames (0.01 Veldin, 0.277 and 0.0446 in two Novalis frames). The rule uses 0.04464268, the value of the frame that draws this glass; it is not a per-object value.
- The dither-kept opaque rim is not drawn; the whole mesh blends with the decoded alpha.
- Frozen: `CB0[99] = 0`, normal-map fade `CB0[95] = 1`, instance fade 0 (the captured record has fade words -2.3333 and 0.0716, whose use is not decoded), `attribute0.w = 1`, geometric AA width 0, instance flags `0x261` as captured, no dissolve. The closed forms above were evaluated with flags `0x2261` and pass word 0; the alpha export is the same node for the captured flags and pass words (tested).
- Light list, shadows, probes, screen-space reflection, ambient occlusion and fog are the viewer rig or Cycles lighting.
- Pass contract 1 (`0x1d429528`) is the variant without the pattern normal (no `0xCC7457CD`, three texture slots: API 5, 6, 7, C at `CB2[4..6]`). The captured frame draws `0x5226f404` and never compiles `0x1d429528`; the rule uses `0x5226f404`.
- The viewer and Blender derive the tangent frame from screen derivatives or UVs, not the vertex tangent. Blender collapses the per-channel F90 to its maximum channel and multiplies the tint into F0.
- A material binding the two noise slots (or the two pattern slots) to different textures is not the decoded input and gets no rule.
- The discard-capable deferred writers `0xdd9a126f` (captured as the G-buffer pass above) and `0x34256787` (not drawn in the captured frame) stay unbound; see [thumper-glove-glass-and-field.md](thumper-glove-glass-and-field.md).
