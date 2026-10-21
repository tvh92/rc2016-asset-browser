# Thumper glove glass: forward program `0x5226f404`

`wpn_thumper_glove_glass` (graph SHA-256 `ffec1bf59b2567ad404a016412708a6df957f004250cf276d9c13e725743ad98`, flags `0x00200234`: opaque, two-sided) has a viewer and Blender rule (`glass_thumper`, `GLASS_FRAGMENT` `uKind` 11, `game_shader._viewer_rule_glass`, validated by `validation.GLASS_CONSTANTS`). It is bound to the exact graph hash and to the forward program `0x5226f404` (code SHA-256 `97ce23dd…6b57`, pass contract 0). It is a frozen preview, not a game-equivalent render (`game_equivalence = False`). Archive-backed checks of everything below are in `tests/test_thumper_glove_glass_rule.py`.

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

## Limitations (kept visible in the rule and the Blender material)

- `CB0[115]` is engine-owned. The rule uses the labelled presentation value 0.5 (`BRIGHT_GLOW_PRESENTATION`; captured 0.01 for other effects, not for this pass). At 0.01 the glow and diffuse term is nearly off.
- Frozen: `CB0[99] = 0`, normal-map fade `CB0[95] = 1`, instance fade 0, `attribute0.w = 1`, geometric AA width 0, instance flags `0x2261` (lit diffuse and specular paths), no dissolve.
- Light list, shadows, probes, screen-space reflection, ambient occlusion and fog are the viewer rig or Cycles lighting.
- Pass contract 1 (`0x1d429528`) is the variant without the pattern normal (no `0xCC7457CD`, three texture slots: API 5, 6, 7, C at `CB2[4..6]`). Which variant the game draws is not in the archive; the rule uses `0x5226f404`.
- The viewer and Blender derive the tangent frame from screen derivatives or UVs, not the vertex tangent. Blender collapses the per-channel F90 to its maximum channel and multiplies the tint into F0.
- A material binding the two noise slots (or the two pattern slots) to different textures is not the decoded input and gets no rule.
- The discard-capable deferred writers `0xdd9a126f` and `0x34256787` stay unbound; see [thumper-glove-glass-and-field.md](thumper-glove-glass-and-field.md).
