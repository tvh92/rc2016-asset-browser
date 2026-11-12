# Battery cannon coil

The barrel coil of the Batalia battery cannon (`environment/batalia/gameplay/rb_bat_battery_cannon/rb_bat_battery_cannon.model`, mesh 0; also `rb_bat_battery_cannon_guns_01`) uses `wpn_Battery_Cannon_Coil` on graph `wpn_battery_cannon_coil`. Neither has a family or a viewer rule, and its writer did not bind on its own, so the coil was drawn through the generic reading as a white ring stack. In the game it is a self-lit cyan coil.

| Material | Graph | Writer (pass contract) | Textures by API | Frozen words |
|---|---|---|---|---|
| `wpn_Battery_Cannon_Coil` | `wpn_battery_cannon_coil` | `0x16c18bea` (0 and 1, identical) | 5 `cloud_noise_01` (graph default) | `CB0[99]` = 0, `CB0[139]` = 0 |

The writer binds through the native translator with the policy `BATTERY_CANNON_COIL_SOURCE_PREVIEW` in `SOURCE_PREVIEW_POLICIES` (`rc2016/shaders/bindings.py`). It follows the rule of [gauge-needle-map-o-matic](gauge-needle-map-o-matic.md): it requires the exact graph path and SHA-256, material path and SHA-256, shader id and code SHA-256, every frozen word is a named receipt row, and the preview carries `game_equivalence = False`. The graph's other programs do not bind: `0xf6673be1` is a forward light-loop program and `0x8f221e6e` an exec-masked (discard) variant. Archive-backed checks are in `tests/test_battery_cannon_coil_preview.py`.

## What is decoded

With `t = CB0[99]`, `s` = the red channel of `cloud_noise_01` at `(u + 0.05 t, 0.1 (v + t))` and `K = CB2[0]` (key `0x89F6E29E`, graph default 0, which the material does not override):

```text
h = 0.3 s - 0.02          a = 0.5 + 0.5 s
q = sat(K^2 - 0.125)      k = sat(8 K)
albedo   = h + a (k (0.39199 q + 0.55201),  k (0.42031 q - 0.42187) + 0.55201,  k (0.12361 q - 1) + 1)
F0       = (0.19995, 0.19995, 0.2)
emission = 3 (h + min(1, K^2)) + 2
gloss    = flat-surface value -log2(1 - 0.99928) / 11 = 0.94868 (= sqrt(0.9))
```

At `K = 0` the albedo is `h + a (0, 0.552, 1)`: a cyan whose brightness follows the noise, and the emission `3h + 2` (1.94 to 2.84) lights it by its own albedo. As for every native translation, the albedo is clamped to [0, 1], the range of the UNORM target the game stores it in: this lifts the slightly negative red where `s` is below 0.067. `cloud_noise_01` peaks at 0.686, so the blue never reaches 1. The emission output is the engine's `exp2(5 · 0.2 log2(x)) - 1` encoding, which folds to `x - 1`. `K` above 0 turns the coil towards a warmer colour; nothing in the material or the model sets it, so a runtime charge state may drive it, which is not decoded.

The gloss output has the same form as the gauge needle writer, `-log2(1 - C (2 - R)) / 11` with `R = 1 / max(0.5, 1 - S^2 / 4)`. Here `S = CB0[139] · POS_W · attribute1.2 / max(0.1, -N·D)`, with `N` the normalised interpolant `attribute2.xyz` and `D` the normalised camera ray built from `CB0[92..106]`, `POS_X`, `POS_Y` and `POS_W`. `CB0[139]` is a factor of `S`, so freezing it at 0 makes `S = 0`, `R = 1` and gloss the flat-surface value. The real specular-AA widening is not modelled.

## Check against a capture

The Batalia capture of the cannon (local research, 2026-11-12) holds only the passes after deferred lighting: its HDR scene target already contains the lit coil, and no G-buffer is in the capture, so the writer's outputs cannot be compared per pixel. The lit colour still checks the decode. Over 53 414 coil pixels the blue-to-green balance `(b - r) / (g - r)` is 2.16 (interquartile 2.12 to 2.21), with median scene-linear colour (0.09, 0.89, 1.84). The decoded emission alone gives 1.81 at every `s` below 0.76. Adding the scene's blue sky light (the sky reads 4.95 on the same measure) on the cyan albedo raises it to about 2.3, which brackets the capture. The red channel is near 0 where the noise is dark, as `h` predicts. That fits `K = 0` (the cyan branch) and does not fit a warm `K`.

## Not reproduced

- `CB0[99]` (time) is frozen at 0, so the noise does not scroll.
- The specular-AA widening (`CB0[139]`, `attribute1.2`) is neutral.
- `K` stays at its graph default 0.
- The forward light-loop and discard programs are not drawn.
