# Warmonger sight screen: forward program `0x4C184DCC`

The Warmonger's (and the Peacemaker's, its V5) targeting sight is mesh 1 of `equipment/weapon/wpn_warmonger/wpn_warmonger.model` (80 vertices, 336 indices), material `material/equipment/weapon/wpn_Warmonger_Reticle/wpn_Warmonger_Reticle.material` (flags `0x200236`: alpha-over, two-sided, alpha cut-off 4/255), graph `materialgraph/equipment/weapon/wpn_warmonger_reticle/wpn_warmonger_reticle.materialgraph` (SHA-256 `78182cb3…5ced`, used by this material only). The graph binds no `_c`, `_g` or `_n`; it samples `wpn_warmonger_reticle_cma` and the eight-frame `wpn_warmonger_reticle_cmb` strip, so the generic reading drew it as a plain untextured surface. It has a viewer and Blender rule (`warmonger_reticle`, `GLASS_FRAGMENT` `uKind` 16, `game_shader._viewer_rule_glass`, validated by `validation.GLASS_CONSTANTS`) bound to the exact graph hash. It is a preview, not a game-equivalent render (`game_equivalence = False`). Checks are in `tests/test_warmonger_reticle_rule.py`.

## Captured draws

A shadPS4 capture of the Peacemaker in Ratchet's hand (731 draws, 1920x1080) draws the sight mesh with the graph's forward program twice and once with a separate program:

| Pass | Pixel program | Target | Blend | Depth | Cull |
| --- | --- | --- | --- | --- | --- |
| visible sight | `0x4C184DCC` | scene HDR (R11G11B10F) | `SRC_ALPHA` / `ONE_MINUS_SRC_ALPHA`, ADD | test GE, no write | none |
| auxiliary | `0x4C184DCC` | RGBA8 | `ONE` / `ONE`, MAX | test GE, no write | none |
| auxiliary | `0x38EAA665` (reads the RGBA8 target) | R32F + RG8 | alpha-over | test GE, no write | none |

So the flags word predicts the visible pass (alpha-over, no cull). `CB0[135]` is 0. The instance record holds flags `0x261`, fade words -2.3333 and 0.071561 (no fade at arm's length) and the cut-off 4/255 (`attribute0.z`); `attribute0.w` is 1. The material block `CB2[0..6]` = (0.36625, 0.13843, 0.03434, 0.1, 0.272, 1.0, 0.1): colour `0x4B4DAB19`, fresnel exponent `0xECE213A3`, F0 base `0x62835AF9`, gloss `0x24F4CA4C` (graph default 1.0) and flipbook rate `0x28E7096A`, all equal to the material and graph values. The bound textures match the archive's by top-mip hash: API 5 and 6 read `_cma` (512x512 BC7), API 7 to 10 read `_cmb` (2048x256 BC7). The 128x128 BC1 at API 11 is read only with instance flag `0x80` (clear here). Blend alpha solved from pixel history (`(post - pre) / (shader - pre)`) is about 0.19 to 0.22 on the captured screen; the shader's red output (0.8 to 1.1) is mostly the scene's diffuse light on the amber albedo.

The capture, the draw state and the pixel history behind these numbers are kept in the local research folder; they are not part of the repository.

## Decoded terms

`u, v` = `attribute0.xy`, `t` = `CB0[99]`. Samples are numbered by API.

| Term | Value |
| --- | --- |
| `S5` | `_cma` at `(u, v)` (R, B, A) |
| `S6` | `_cma` at `(u, v)` turned about `(0.5, 0.5)` by `th = -t/4 + 0.411765 cos 3t` (G): the rotating ring |
| `S7` | `_cmb` at `(0.625 (u - f) - 0.465, 5 (v - f))`, `f = floor(-8 fract(CB2[6] t))` (R): the eight-frame flipbook |
| `S8`, `S9` | `_cmb` at `(u/8, v + 0.285 t)` and `(u/8, v + 0.508 t)` (B): two scrolling scanlines |
| `S10` | `_cmb` at `(u/8, v)` (G): the screen mask |
| Fresnel | `fres = sat(4 \|1 - sat(f N.V)\|^CB2[3])`, geometry normal `attribute2`, view vector `attribute3`, `f` = +1 on front faces |
| Self-lit `E` | `sat(0.874 + fres) (1 - 0.6436 (1 - S7.R S5.A)(1 - S5.R)(1 - S5.B S6.G)) (1 - (1 - S10.G)(1 - S8.B)(1 - S9.B))` (the first factor is the union with the instance fade, 0 here) |
| Alpha | `sat(attribute0.w sat(E))`; the fragment is discarded when `sat(E) < attribute0.z` (checked against the program's DAG, error below 5e-6) |
| Colour | `R = E + Ld.R + spec.R`, `G = 0.473531 (E + Ld.G) + spec.G`, `B = spec.B`: albedo `(1, 0.473531, 0)` compiled into the program, on both the diffuse light and `E` |
| Specular | `F = CB2[4] + fres^10`, F90 = `sat(60 F)`, GGX alpha `2^(-5.5 sqrt(sat(CB2[5])))`, times `k = sat(C / luma(C))` with `C = CB2[0..2]` |

The colour is fogged by `attribute3.w` (1 at the captured distance).

## Viewer rule

`rgb = (1, 0.473531, 0) (Ld + E) + k specular(F)`, alpha as decoded, blended alpha-over and two-sided. `t` is 0 at rest; while a clip plays the model view's effect clock drives it, so the ring turns, the flipbook steps and the scanlines scroll. In Blender the screen is `Mix(alpha; background, diffuse + emission + specular)` and `VR glass time` holds `t` (drive it to animate).

## What is frozen, inferred or not drawn

- **Frozen:** the instance fade (0), instance flags `0x261`, fog 1, geometric anti-aliasing 0.
- **Viewer rig:** the engine light list, probes and shadows are replaced by the viewer's lighting (Cycles in Blender).
- **Not drawn:** the RGBA8 `MAX` pass of the same program and the `0x38EAA665` pass that reads it (an auxiliary screen-space buffer; its use is not decoded).
- **Blender:** the alpha cut-off is a step on alpha, the colour ratio `k` multiplies F0 and the Principled F90 is collapsed to its maximum channel.
