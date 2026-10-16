# Gauge needle and Map-o-matic screen previews

Two deferred writers bind through the native translator once their unbound engine words are replaced by labelled constants. Each policy is in `SOURCE_PREVIEW_POLICIES` (`rc2016/shaders/bindings.py`) and requires the exact graph path and SHA-256, material path and SHA-256, shader id and code SHA-256. A source-identical writer in another graph or material gets nothing, and a translation without the source context stays unsupported. Every frozen word is a named entry of the policy and its receipt (`source_preview_inputs`, one row per word, each with its own meaning text). All previews carry `game_equivalence = False`. Archive-backed checks are in `tests/test_gauge_needle_map_o_matic_previews.py`.

| Material | Graph | Writer (pass contract) | Textures by API | Frozen words |
|---|---|---|---|---|
| Gauge needle (Hydrodisplacer) | `gbl_prop_guage_needle_01` | `0x7d4cea47` (0) | 5 `gbl_prop_guage_needle_c` | `CB0[99]` = 0, `CB0[139]` = 0 |
| Map-o-matic screen (`gbl_map_o_matic`) | `vel_prop_monitor_screen_03` | `0x4dd02bef` (0) | 5 `vel_prop_monitor_screen_03`, 6 `icon_holograms_background` | `CB0[99]` = 0, `CB0[115]` = 0.5, `CB0[139]` = 0 |

The gadget material `gdgt_mapomatic` (graph `basic_normal_gloss`) is a different, already bound writer and is not part of this rule.

## What is decoded

Both writers' gloss output is `-log2(1 - C (2 - R)) / 11` with `R = 1 / max(0.5, 1 - S / 4)`. This is the engine's gloss encoding `-log2(1 - W (1 - 2^(-11 g))) / 11` with the exponential folded into the constant `C = 1 - 2^(-11 g)` and the specular-AA width `W = 2 - R`. `S` is a screen-space term built from `CB0[139]`, `POS_W`, the vertex input `attribute1.2`, the normal interpolant `attribute2.xyz` and the camera ray words `CB0[92..106]`. `S` is a product with `CB0[139]` as a factor, so `CB0[139]` = 0 collapses it to 0 (checked on the lowered DAG).

- **Gauge needle.** Albedo = `_c.rgb` at UV rotated about (0.5, 0.5) by the angle `sat(0.2 cos(2π fract(8 t / 2π)) + 0.2)` radians, `t = CB0[99]`. At `t = 0` the angle is 0.4 rad, not the identity, so the frozen needle sits at the extreme of its swing (the angle ranges over [0, 0.4]). F0 = (0.0999756, 0.0999756, 0.1) grey, emission = 0. Flat-surface gloss = `-log2(1 - 0.9992779)/11 = 0.94868 = sqrt(0.9)`, i.e. `sqrt` of 0.9 under the gloss squaring the other writers use. The material has no parameters.
- **Map-o-matic screen.** Material parameters (instance values): tint `CB2[0..2]` = (0, 0.4969330, 0.6583748) and `CB2[3]` = 2. With `X = 0.025 + 0.25 B6 + 0.963 G5(s1) + 0.694 clamp(|UV0 - (0.5, 0.5)|) + 0.519 B5(s1) + 0.485 R5(s0) + A5(s3)` (API6 sample 2, API5 samples 0, 1, 3): albedo = `2 A CB2[i] X` with `A = CB0[115]`, emission = `exp2(5 · 0.2 · log2(1 + CB2[3] X)) - 1` = `CB2[3] X` (independent of `A`), F0 = 0.24 grey, flat-surface gloss = 1.0. The API5 sample 1 UV is rotated about the centre by `-0.5 t` rad and sample 3 by `+0.5 t` rad (`t = CB0[99]`), the identity at 0; samples 0 and 2 use UV0.

## What is frozen, and why each value is or is not justified

- **`CB0[99]` = 0 (time).** Same labelled convention as the other policies: it enters only through the UV rotation angles above. The engine upload is unverified.
- **`CB0[139]` = 0 (specular-AA width scale).** Setting it to 0 makes `S = 0`, `R = 1`, `W = 1` and gloss equal to the flat-surface value. That is the identical result the translator already applies to every writer whose `exp2` term survives constant folding (`_gloss_without_aa`: "AA width is 1"); here the fold hid the pattern, so the policy reaches the same endpoint through the one input that multiplies `S`. The flat gloss values (`sqrt(0.9)`, 1.0) are the encoded constants, not a guess. The real `CB0[139]` and `attribute1.2` are not decoded and are not read; the omitted widening is not modelled.
- **`CB0[115]` = 0.5 (Map-o-matic albedo amplitude), not decoded.** The writer's albedo factor is `2 A`, the same `2 A` form as the Victor reticle (`CB0[115]` is shared, frame-global). The repository uses 0.5 there as a labelled presentation amplitude; game captures of two other effects (facing and bright glow) read 0.01 (near off), so 0.5 is not evidence of the in-game value. It is used here only because it is the value at which `2 A = 1`, i.e. the preview shows the authored tint times `X` unscaled. The receipt meaning text and the limitation say this is a presentation value; the in-game brightness of the screen is unknown. The emission (`CB2[3] X`) does not depend on it.

## Not reproduced

Pass selection at draw time, the geometry normal (the writers bind no portable tangent-normal slice), the forward light-loop and exec-masked programs of both graphs, the time animation (frozen at 0), the specular-AA widening, and the real `CB0[115]` amplitude.
