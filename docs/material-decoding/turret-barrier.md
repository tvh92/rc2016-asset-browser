# Blarg turret barrier and targeting display

`environment/global/gameplay/gbl_gp_enemy_turret_01/gbl_gp_enemy_turret_01.model` has six meshes. Four of their materials already had rules (the three metal trims and the emissive blue lights). The other two are forward-only visual effects that the viewer used to draw as opaque grey surfaces through the generic reading:

| Mesh | Material | Graph (SHA-256) | Forward program | Flags word |
| --- | --- | --- | --- | --- |
| 4 (the curved barrier, 44 vertices, about 2.9 x 1.6 units) | `enm_Blarg_Turret_Shield_Lg` | `enm_blarg_turret_shield_lg` (`ac43cf5f...5d0`) | `19D0F1FA` | `0x20000E`: alpha-over, two-sided |
| 5 (a 0.56 x 0.66 quad above the gun) | `gdgt_Blarg_Turret_HUD` | `gdgt_blarg_turret_hud` (`c2957214...afc4`) | `678C5C0E` | `0x20023E`: alpha-over, two-sided |

The deferred writers of both graphs are not the look: the shield's is the shared zero writer `11408DE9`, and the HUD's `1A8B88D4` is an opaque-pass variant. Both materials are drawn by their forward program (the `_0` variant, pass 0). Both are exact-graph preview rules (`turret_shield`, `uKind` 18; `turret_hud`, `uKind` 19) with `game_equivalence` False.

## Evidence and what it does not prove

- The Gaspar turret capture (`CUSA01073_capture_2.rdc`, "Enter Turret" prompt, barrier up) holds 27 draws, all screen-space UI over a scene target that already contains the barrier. Neither forward program is drawn inside the capture, so there are no captured constants, draw state or per-pixel checks for either. The final image shows the look: an amber hex-grid pane with bright bracket frames and, over the gun, a fine yellow dashed V.
- The shadPS4 dumps hold both programs (`fs_0x19d0f1fa`, `fs_0x678c5c0e`, compiled during the same scene). The formulas below were read from their IR and checked numerically two ways: with an IR evaluator against the closed forms (4000 random samples, error 1e-7 for the shield, 7e-7 for the HUD colour) and, in `tests/test_turret_barrier_rule.py`, against the project's own GCN decode (`rc2016.research.forward_loops`), which does not use the shadPS4 IR. The texture-coordinate transforms are checked by the first only; the second treats each sample as an independent value.
- Texture binding follows the pass-contract convention API = 5 + resource index. The GCN decode reads API 5 to 9 for the shield (the graph's five resources; API 8 and 9 are one image sampled at one coordinate) and API 5, 6, 7 plus the engine image 33 for the HUD.
- The decoded images reproduce the capture's look when evaluated in UV space (hex grid, bracket frames, the bright centre stripe where the two panes meet; the HUD's dashed V and outline). The brightness could not be compared: the engine constants below are not in the capture.

## Shield (19D0F1FA)

Inputs: `u, v` = attribute0.x, .y (UV0; the mesh holds u in [0.007, 0.857], mirrored about the centre line, v in [0.004, 0.995], counted from the top); `t` = CB0[99]; `A` = CB0[115]; `K0` = CB2[0] (material key `0xEF782F74`, 1.0); `K1` = CB2[1] (key `0x2F7DD4F2`, graph default 0, the material does not set it); attribute0.z = the alpha cut-off, attribute0.w = the opacity, attribute1.w = the fog transmittance `F`. Images, in graph order: `fx_noisetile03` (API 5), `fx_noisetile02` (6), `hex_grid_02` (7), `enm_blarg_turret_shield_lg_v2` (8 and 9).

```text
n3  = noisetile03(u - 0.035 t, 1.5 v + 0.05 t).r
n2  = noisetile02(0.5 + 0.5 u - 0.03 t, 0.5 + 0.5 v - 0.05 t).r
H   = hex_grid_02(10 u, 10 v).r
S   = shield_lg_v2(u, v)
d   = |(u, v - 0.475)|          R = sat(d^1.25)
Q1  = min(1, fract(3 (R - 0.2 t))^2) - 0.5
Q2  = min(1, fract(3 (sat(R + sat(0.3 |1 - n3 - n2|)) - 0.2 t))^2) - 0.5
ring = sat(0.5 sat(1 - 0.5 sqrt(Q1^2 + 3 Q2^2)))
line = sat(sat(1 - 20 |H|) / 3)
s   = (ring + line) sqrt(|1 - u|) + S.r + S.b + sat(4 S.g - 3)
Ks  = K0 s           cover = sat(max(0, Ks))
discard when cover < attribute0.z
alpha = sat(attribute0.w cover)
e   = A F Ks (20 K1 + 5)
rgb = fog (1 - F) + e (36, 6.2873, 0.30845)       fog = CB0[112..114]
```

The program reads no light list, so a barrier is an emissive amber glow. The three fixed colour weights are 36, 18 x 0.3492948 and 18 x 0.01713626 in the program. The shield image supplies the bracket frames (`S.r`, `S.b`) and the bright lines (`S.g` above 0.75); the hex grid supplies the cell lines (`H` near 0); the rings are the slow radial pulses.

## Targeting display (678C5C0E, unlit branch)

Images: `hex_grid_02` (API 5), `gdgt_blarg_turret_hud` (6), `hydro_noise_cell_sm` (7); API 33 and the screen-space image at the program's start are engine images. Inputs as above, `Fg` = attribute3.w, `k` = floor(CB2[0], key `0x47EDAA94`, graph default 0).

```text
H1 = hex(15.2 u - 0.1, 15.2 v - 0.1).r     H2 = hex(15 u, 15 v).r
T  = hud(u, v).rgb                         N  = noise(0.01 u - 0.1 t, v + 0.1 t).r
d  = |(u, v) - 0.5|                        ring = sat(fract(t + d)^0.1)
hexA = sat(1 - 2.2222 |H1|)                hexB = sat(1 - 20 |H2|)
hx = (2.857143 hexB + 0.666667 hexA) (0.05 + d)
g  = 1.5 T.g + 5 T.b (1 - ring)
cover = sat(max(0.05 hx, (g + 1.5 T.r) (0.7 + 0.3 N)))
discard when cover < attribute0.z;   alpha = sat(attribute0.w cover)
G  = 0.25 + 0.75 sat(g + 0.05 hx)
c  = (G^0.1, k (G^50 - G) + G, k (G^200 - G^100) + G^100)
p  = 0.9 + 0.2 sin(pi t)
rgb = fog (1 - Fg) + Fg 3 A (5 p + 1) c
```

The output components come out in this order from the program (the first carries `G^0.1`, the second `G`, the third `G^100`), so the lines run from orange through yellow to white where `G` reaches 1: the yellow dashes of the capture. `sin` is the hardware turns sine fed `t/2` revolutions, that is `sin(pi t)`. The instance record word (record dword 60) selects other paths: bit 0x80 min-combines the coverage with the engine image at (u/3, v/3); bits 0x20 or 0x40 run the light list and add its colour to the same expression. The rule draws the unlit branch.

## Preview values and limits

- `attribute0.z` and `attribute0.w` come from the material flags block as for the Warmonger sight: the alpha cut-off is 4/255 (both materials) and the opacity is byte 22 / 255, 19/255 for the shield and 191/255 for the HUD. The opacity is the weakest assumption: the Warmonger sight is the one captured example, and there it was 1. A pixel check on the capture (a hex line rises by about 0.035 in display red where an opacity of 19/255 predicts a few hundredths and an opacity of 1 predicts several tenths) agrees with 19/255 in magnitude, which is as far as it goes.
- `CB0[115]` is a per-frame engine value that this capture does not hold. The preview uses the Thumper capture's 0.04464268 (`TURRET_BARRIER_PRESENTATION_AMPLITUDE`); it scales the brightness only. 0.01 (Veldin) and 0.277 (a Novalis frame) were also measured in other frames.
- `Fg` = 1 (no fog) and `t` = CB0[99] frozen at 0 unless the effect clock runs (an animating clip, `EFFECT_KINDS`). The decoded scrolls have periods of 1 / 0.035, 1 / 0.05 and similar, not 1 s, so the viewer's 1024 s wrap shows a small jump.
- Not decoded or not drawn: the light-list branch of the HUD, its engine images (API 33 and the screen image), the instance flag word (taken as 0), the fade of attribute0.z / .w with distance or hit state (the shield's dissolve), the second pass variant (`_1`), and whether the HUD ever runs the lit branch.
- The Blender front-end builds the same math as emission through a Transparent mix by the alpha (with the discard as a step), and `rc2016/shaders/validation.py` checks its constants and images.

Research evidence: `Research/captures/2026-11-07_turret-barriers/` (README there: capture hash, draw list, scripts `turret_decode.py`, `ir_eval.py`, `check_decode.py`, `gcn_check.py`, UV-space renders).
