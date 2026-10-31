# Hydro harvester tank glass: forward program `0x7EB73491`

The glass of `enm_hydro_harvester_tank` is mesh 1 (4416 indices), material `material/global/water_tank/hydro_water_tank.material` (flags `0x38`: opaque, one-sided), graph `materialgraph/global/water_tank/water_tank.materialgraph` (SHA-256 `544803a3…4ce9`). It has a viewer and Blender rule (`glass_tank`, `GLASS_FRAGMENT` `uKind` 15, `game_shader._viewer_rule_glass`, validated by `validation.GLASS_CONSTANTS`). The rule is bound to the exact graph hash and to the `hydro_water_tank` material: the graph is shared by four materials (`water_tank`, `ms_kal_water_tank`, `gas_gp_brain_egg_sac_water_01`, `hydro_water_tank`), but only this one is evidenced by a captured draw. It is a frozen preview, not a game-equivalent render (`game_equivalence = False`). Archive-backed checks are in `tests/test_hydro_water_tank_rule.py`.

## Captured draws

A shadPS4 capture of a Pokitaru cutscene frame (the harvester convoy, 326 draws) draws the glass 13 times, all with 4416 indices:

| Pass | Pixel program | Blend | Depth | Cull | `CB0[135]` |
| --- | --- | --- | --- | --- | --- |
| depth-equal forward (dither-kept rim; most fragments discarded) | `0x7EB73491` or `0xC9E3608C` | off | test EQUAL, no write | back | `0x40` |
| blended forward | `0x7EB73491` or `0xC9E3608C` | `SRC_ALPHA` / `ONE_MINUS_SRC_ALPHA` (colour and alpha), ADD | test GREATER, no write | back | `0x2` |

So the glass is alpha-over although the flags word says opaque, as for the [Thumper glove glass](thumper-glove-glass.md). The rule uses the captured blend, not the flags word. Instance flags are `0x61` or `0x261`. The material block `CB2[0..9]` is identical at all 13 draws: `CB2[0..2]` = (0.03689, 0.22323, 0.34191) (key `0x7C456482`), `CB2[3]` = 0.25 (the graph default of key `0x42170E5E`, which the material does not set), `CB2[4..6]` = (1, 1, 1) (`0x4B4DAB19`), `CB2[7]` = 0.05 (`0x62835AF9`), `CB2[8]` = 0.5 (`Glossiness`), `CB2[9]` = 5.0 (`0xECE213A3`). The bound textures match the material's by mip-0 hash: both `S5` and `S6` read `gbl_prop_water_tank_cma` (BC7), the flow normal is `met_amb_water_pool_flow_01_n` (BC5, bound only with `0x7EB73491`).

The capture, the two programs' draw state and the pixel history behind these numbers are kept in the local research folder; they are not part of the repository. Alpha measured from pixel history (`(post - pre) / (shader - pre)`, 13 samples): 0.357 to 0.977, mostly about 0.36, above the `CB2[3]` floor of 0.25.

## Decoded terms (checked numerically against the program)

`rim = sat(4 |1 - sat(f N.V)|^CB2[9])` from the geometry normal `N = attribute2.xyz` and `V = attribute4.xyz`, `f` = +1 on front faces, -1 on back faces; `a = sat(CB2[3] + rim)`.

| Term | Value |
| --- | --- |
| Alpha | `sat(attribute0.w sat(1 - (1 - a)(1 - S5.R)(1 - S6.R)))`; `S5` at `(u + 0.04 t, v + 0.06 t)`, `S6` at `(1.5 u + 0.03 t, 1.5 v + 0.05 t)`, `u, v` = `attribute0.xy`, both the `_cma` texture, channel R (checked against the DAG, error below 5e-6) |
| Flow reads | `P` at `(0.1 gx, 0.116667 gz + 0.025 t)`, `Q` at `(gx / 12, gz / 12 - 0.05 t)`, `gx, gz` = the fragment's world X, Z relative to the instance record's position |
| Normal | `x = 2 (P.R - Q.R)`, `y = 2 (P.G - Q.G)`, `N' = normalize((1 - x^2 - y^2) N + \|y\| B + \|x\| T)`, `B = sign(attribute3.w) normalize(N x T)` (verified against the DAG in an earlier decode; no strength input) |
| Gloss | GGX alpha `2^(-5.5 sqrt(sat(CB2[8])))` (matches the loop-head register) |

## Viewer rule

`rgb = body Ld + specular(F0, N')` with `body` = `CB2[0..2]`, `F0 = sat(T / luma(T)) (CB2[7] + rim^10)` (`T` = `CB2[4..6]`), F90 = `sat(60 F0)`, the light loop of the other glass writers. The flow map is read at the model-space XZ (`gx = -x`, `gz = -z`, the record origin taken as the model origin). Shown in Blender as `Mix(alpha; background, lit)` with the captured alpha-over.

## What is frozen, inferred or not drawn

- **Inferred, not read from this program:** the tint-to-F0 step and the diffuse term `body Ld` follow the `basic_glass` writer, whose material interface (`CB2[0..9]`: tint, rim bias, body, gloss, f0 base, rim exponent) this graph shares. The program's colour export reads `CB2[0..2]`, `CB2[4..9]`, the fog colour and the light loop; the tint arithmetic inside the loop was not evaluated.
- **Frozen:** time `CB0[99]` = 0 (the flow maps do not scroll), `attribute0.w` = 1, instance flags `0x261`, fog 1, no screen-space reflection. The light list, probes, shadows and fog are the viewer rig (Cycles lighting in Blender).
- **Not drawn:** the dither-kept opaque rim and the depth-equal pass. The second program `0xC9E3608C` (same material block, no flow normal) is not selected per draw; what selects it is not decoded. The deferred writers `0x11408DE9` and `0xFB20D308` have no draw in the captured frame.
- **Blender:** the tangent frame is Blender's UV tangent, and the Principled F90 is collapsed to its maximum channel.
- The frame constants of the capture (fog, `CB0[115]`) belong to that cutscene and are not used.
