# Telepathipus brain case, brain and inside shell

`character/enemy/enm_telepathipus/enm_telepathipus.model` (and `enm_telepathipus_v2`, which adds armour plates) carries its brain in three nested parts. The viewer drew all three wrongly: the outer case as an opaque purple surface, the inside shell as an opaque red surface around the brain, and the brain itself with a flat grey placeholder as its albedo.

| Mesh | Material | Graph (SHA-256) | Program | Flags word |
| --- | --- | --- | --- | --- |
| 1 (outer case, about 8 x 6 x 12 units) | `enm_Telepathipus_Brain_Case` | `enm_telepathipus_brain_case_exterior` (`353289b4...47ad`) | forward `AE2B0B81` (pass 0), `775A45A0` (pass 1) | `0x3A`: alpha-over, back faces culled, opacity byte 255 |
| 3 (inside shell, the same size, normals and winding facing inward) | `enm_Telepathipus_Brain_Case_Inside` | `basic_flat_color` (`6d9bcad5...4f95`) | forward `D169A814` | `0x230`: opaque, back faces culled |
| 0 (the brain, about 2.6 x 2.2 x 3.0 units, inside the case) | `enm_Telepathipus_Brain` (slot name `new_brain`) | `enm_telepathipus_brain` (`46c6c154...8654`) | forward `FBA51F6C` (pass 0), `26538E77` (pass 1) | `0x230`: opaque, back faces culled |

The deferred writers of the case and brain graphs are the shared zero writer `11408DE9`; all three are drawn by their forward programs.

## Evidence and what it does not prove

- The capture (`Research/captures/2026-11-07_telepathopus/`, a Telepathipus in close view) holds 47 draws, all screen-space UI over a finished scene image, so there are no captured constants or draw state. The final image shows the look: a pink-white, brain-shaped case with red edges and veins, the yellow core of the brain showing through it.
- The shadPS4 dumps of that session hold all five forward programs, compiled during the scene. The formulas below were read from their IR. The case's colour and alpha were checked in `tests/test_telepathipus_brain_case.py` against the project's own GCN decode (`rc2016.research.forward_loops`, with the light-loop outputs as random inputs): relative error below 2e-6.
- Texture binding follows API = 5 + resource index of pass 0 (case: 5 flow normal `met_amb_water_pool_flow_01_n`, 6 and 7 the case `_g`, 8 `_c`, 9 `_n`; brain: 5 `_g`, 6 `_n`, 7 `telepathipus_brain_electricity`, 8 `default_pink_shader_c`, 9 `telepathipus_leg_brain_c`), confirmed by the channels each sample reads.
- Pass 1 of the case (`775A45A0`) has the same literals and terms without the water-flow distortion (three samples fewer); which pass the game picks at what distance is not established.

## Outer case (AE2B0B81)

With `q` = `CB0[95]` (1 - instance fade), `t` = `CB0[99]`, `(u, v)` = UV0, `n`, `T`, `B` the normalised normal, tangent and `sign(attribute3.w) normalize(n x T)`, `V` = normalize(`attribute4.xyz`) (camera minus fragment):

| Term | Value |
| --- | --- |
| Flow normal | `f = q (2 S5(0.3 u - 0.1 t, 0.3 v + 0.025 t).rg - 1)`, `zf = 1 - f.f` |
| Distorted UV | `uv' = (u, v) + o (-T.V, B.V)`, `o = -0.4 S6.b(u, v) q f.y` (S6 is the `_g` blue channel) |
| Case normal | `m = q (2 S9(uv').rg - 1)`, `zm = 1 - m.m` (linear z) |
| Rim normal | `Nr = face normalize(T m.x + B m.y + n zm)`, `e = abs(1.001 - sat(Nr.V))` |
| Shaded normal | `Ns = normalize(T (m.x zf + w f.x) + B (m.y zf + w f.y) + n zm zf)`, `w = 3 S7.b zm` |
| Gloss | GGX alpha^2 = `2^(-11 sqrt(sat(S7.r lerp(CB2[3], CB2[7], S8.a))))` (4 and 10) |
| F0, F90 | `F0 = sat(S7.g lerp(CB2[11], CB2[12], S8.a))` (5 and 1, grey), `F90 = sat(60 F0)` |
| Specular tint | `A = sat(CB2[14] lerp(CB2[8..10], CB2[0..2], e^CB2[13]))`: (1, 0.098, 0.044) facing, (0.037, 0, 0.034) grazing, exponent 0.2, scale 5 |
| Rim weight | `s = sat((10 e^4)^5)`: 1 below N.V of about 0.44 |
| Thickness | `k = sat((abs(Z - W) - 1) / 3)`, `Z` the scene's linear depth behind the fragment (an engine image at the screen position), `W` the fragment's view depth |
| Albedo | `lerp(lerp(lerp(Bg, P, k), R S8.rgb, s), S8.rgb, S8.a)`, `Bg` the scene colour behind the fragment (an engine image at the screen position), `P` = `CB2[4..6]` = (1, 0.381, 0.591), `R` = (0.83577013, 0.16456541, 0.41727373) hard-coded |
| Colour | `fog + t_f (albedo D + A S - fog)`: `D` the diffuse light and `S` the specular light of the engine loop, `t_f` = `attribute4.w` (the 0x2000 path mixes in SSR) |
| Alpha | `sat(attribute0.w)` (opacity byte 22 / 255 = 1), blended alpha-over |

So the case shows what is behind it (the inside shell's far wall or the brain), tinted toward the pink `P` with the distance to it, darkened toward `R _c` at grazing angles, and replaced by its own painted membrane where `_c.a` is 1. The scene colour is multiplied by the case's own diffuse light.

## Inside shell (D169A814)

The existing [basic_flat_color](BASIC_FLAT_COLOR_D169A814.md) reading applies: zero albedo, grey F0 0.198, gloss 0.143 and the additive colour (1.0, 0.0009, 0.068). Its mesh is a copy of the case with every normal and winding turned inward, and its flags cull back faces, so from outside the game only draws its far inner wall: the red seen through the case near its edges, where the case is thin. The viewer draws other opaque materials two-sided; `basic_flat_color` now keeps the flags word's cull (six other materials use the graph, all with culling flags except `dt_black`, which is two-sided).

## Brain (FBA51F6C)

`rgb = fog + t_f ((D + 100 w) B + T S)`, with `B` = S9 = `telepathipus_leg_brain_c` (the albedo), grey `F0 = CB2[4] S5.g`, gloss `sqrt(sat(CB2[5] S5.r))` (1 and 2), the S6 normal, and a specular tint `T = S8 / luma(S8)` where S8 is `default_pink_shader_c`, a flat grey 2 x 2 image, so `T` = 1. The emission `w = B.r E` takes one channel of the electricity image by the per-frame random `CB0[107]`: blue at or below 0.1, green to 0.2, red to 0.25, none above (three frames in four). The capture's bright yellow core is consistent with an electricity frame.

## Viewer and Blender

- Case: exact-graph rule `brain_case` (`uKind` 20, `rc2016/viewer/shading.py`). The model viewer draws its frames in the float scene target and copies the frame's colour and depth just before the case draws, so `Bg`, `Z` and the composition above are the program's; the light is the viewer rig. The level viewer has no copies: there `k` = 1 and the blend supplies `Bg` (alpha = `1 - (1 - k)(1 - s)(1 - _c.a)`, `D` taken as 1 for it). Frozen: `t` = 0, `q` = 1, no SSR, fog `t_f` = 1. The tangent frame comes from screen derivatives, as for the viewer's other normal maps.
- Inside: the `basic_flat_color` reading culls back faces when the flags word says so (viewer and Blender, `VR cull back faces`).
- Brain: exact-graph grey reading with `_c`, `_g`, `_n` bound by slot (`0xABDAD780`, `0x1576D848`, `0x7AE34E7A`) instead of by suffix; the electricity is drawn in its off state.
- Blender builds the case through the glass front-end without the scene lookups: `k` = 1 (the body colour where the viewer shows what is behind), no UV distortion, and the specular tint on F0 and the Principled edge tint.

All three are labelled previews (`game_equivalence` False): the engine light list, probes, shadows, SSR and fog are the viewer's or Cycles' lighting. Against the capture image, the vein pattern, the pink-white lobes, the red edges and the brain showing through agree; the game frame is redder, because its sun is orange and brighter and the case is close to a mirror (F0 = sat(5 `_g.g`), near 1, with the orange specular tint) that reflects the desert.
