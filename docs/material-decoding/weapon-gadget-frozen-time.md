# Weapon and gadget writers previewed with `CB0[99]` frozen at 0

Four deferred writers bind through the native translator once their one unbound engine input, `CB0[99]`, is replaced by 0. Each policy is in `SOURCE_PREVIEW_POLICIES` (`rc2016/shaders/bindings.py`) and requires the exact graph path and SHA-256, material path and SHA-256, shader id and code SHA-256. A source-identical writer in another graph or material gets nothing, and a translation without the source context stays unsupported. Archive-backed checks are in `tests/test_weapon_gadget_frozen_time_previews.py`.

`CB0[99]` is treated as a time or phase input because it enters these writers only through texture coordinate offsets and rotations and, in the Pixelizer, a cosine term. Its engine upload and runtime value are unverified, and 0 is a review choice. All four previews therefore show the surface at "time 0". They use the geometry normal (or the native slice named in the receipt), the viewer's lighting and tone mapping, and carry `game_equivalence = False`. Which program the game selects at draw time is not established, and the forward light-loop and exec-masked (discard) programs of each graph are not modelled.

| Material | Graph | Writer (pass contract) | Textures by API |
|---|---|---|---|
| Trespasser gun screen | `required/materials/gdgt_tresspasser_screen` | `0x97715c0e` (0) | 5 `_g`, 6 `_n`, 7 `hydro_noise_cell_sm`, 8 `_c`, 9 `fx_noise_holes_01_inv_nmap`, 10 `fiber_noise_01` |
| Pixelizer barrel screen | `wpn_pixelizer_barrel` | `0x566afd49` (0) | 5, 6, 9 `wpn_pixelizer_screen_ui`; 7, 8, 10 `wpn_pixelizer_barrel_cma` |
| Plasma Striker scope | `wpn_plasma_striker_fx_scope` | `0x00e1b2fe` (0) | 5, 6 `wpn_plasma_striker_cmb` |
| Magnetic boot fan | `gbl_prop_basic_rotate_01` | `0x00707a09` (1) | 5 `ratchet_magboot_fan_c`, 6 `ratchet_magboot_fan_g` |

## Equations

All expressions were read from the lowered source DAG and checked numerically against it.

- **Pixelizer barrel.** With `b` = API8 sample 0 B, `c` = API7 sample 1 B, `a` = API10 sample 2 A and `S = clamp(0.2 (R + G + B9), 0, 1)` from the API9 sample: `W = b + c - bc`, `X = 1 - 0.3049873 W`, `Q = (1 - S)(1 - a/2) X`, `F(T) = 1 - 0.3961726 (1 - 0.0953075 cos(16 T))` with `T = CB0[99]`. Albedo = `F(T) (0.0908417 - 0.0902062 Q, 1 - 0.9533349 Q, 0)`, F0 = 0, gloss = 0 and emission = 2.9986464 (constant). `CB0[99]` also offsets and rotates five of the six sample coordinates. The material has no parameters.
- **Plasma Striker scope.** With `d = (1 - B6)(1 - G5)` and `S = 0.8587367 d` (samples from API6 and API5): albedo = `(0, 0.3564001 R5 (1 - S), 0.5088813 R5 (1 - S))`, F0 = 0, gloss = 0 and emission = 10.0000016. The API6 coordinates are rotated about (0.5, 0.5) by `2π fract(|0.229 CB0[99] / 2π|)`, which is the identity at 0. The 0.229 rate and the "time" meaning of the input are inferred.
- **Magnetic boot fan.** The `_c` (API5) and `_g` (API6) UVs are rotated about the centre by `2π fract(5 CB0[99] / 2π)`, the identity at 0. Albedo = `_c.rgb`, F0 = grey `_g.G`, gloss = `sqrt(sat(_g.R))` and emission = 0. The rate 5.0 is the material value of key `0xBF44EC0D` (CB2 dword 0); its role as a rotation rate is inferred. The sibling writer `0x5fdcac1d` of the same graph produces the same colour outputs, but its normal needs `CB0[95]`, `POS_W` and two `TBUFFER` reads, so the exact code hash excludes it.
- **Trespasser gun screen.** `CB0[99]` feeds the albedo only, through 45 DAG nodes. With a constant sample the evaluated F0 is the grey `_g.G` and gloss follows `_g.R`. The albedo and emission chains are kept as the translator's DAG and are not restated by hand. The material has no parameters.

## Not decoded

- The Plasma Striker body glow (`wpn_plasma_striker_fx`, writer `0x368cca2e`) has constant outputs, but the pass-contract join drops any program with no sampler at API 5 or above, so it does not bind.
- The Warmonger reticle and Sheepinator internal energy deferred writers are the zero-output placeholder `0x11408de9`, and their discard variants also export zero colour; the visible look is forward-only (see [forward-only energy](forward-only-energy.md)).
- The Thumper glove glass and shockwave field writers are exec-masked or forward-only; see [thumper-glove-glass-and-field.md](thumper-glove-glass-and-field.md).
- The gauge needle (Hydrodisplacer) and Map-o-matic gloss terms need `CB0[139]` and `attribute1.2`, which have no decoded value.
