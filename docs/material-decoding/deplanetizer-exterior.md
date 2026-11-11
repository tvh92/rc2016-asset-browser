# Deplanetizer exterior: dome glass, dwarf star and hangar glow

The Deplanetizer exterior model (`environment/global/prop/gbl_prop_uiobject_deplanetizer_01/gbl_prop_uiobject_deplanetizer_01.model`) has three forward materials that the generic reading drew wrongly: the dome glass (opaque dark grey), the dwarf star inside it and the hangar glow strips. Each is now an exact-graph rule (`GRAPH_RULES` in `rc2016/viewer/textures.py`, bound to the graph path and SHA-256) drawn by `GLASS_FRAGMENT`:

| Material | Graph (SHA-256) | Forward program | Rule | `uKind` | Draw state (flags word) |
|---|---|---|---|---|---|
| `rv_dep_arch_star_chamber_glass_green` | `dep_arch_glass_panels_01` (`5cf25d6c…0b62c`) | `39FDC0B9` (pass 0) | `glass_tinted_spec` | 21 | `0x3A`: alpha-over, back faces culled |
| `dep_gp_dwarf_star_surface_calm_01` | `dep_gp_dwarf_star_surface_calm_01` (`5e0fcc30…9091f`) | `05C96D48` (passes 0 and 3) | `dwarf_star` | 22 | `0x20C`: opaque, two-sided |
| `dep_arch_hangar_glow` | `basic_flat_color_incandescent` (`960498e0…f74a4`) | `FDF7FDFB` (pass 3), `E16F8FC0` (pass 0) | `flat_incandescent` | 23 | `0x239`: additive (`SRC_ALPHA`, `ONE`), back faces culled |

No draw of these programs is in a capture. The equations below were read from the shadPS4 IR dumps of the programs (compiled in game, so the programs did run), the native code through `rc2016.shaders.translator` (sample order, channels) and the graph pass contracts. The dome glass terms were checked numerically against its IR (maximum error 4e-9 over 300 random inputs). Every rule is a labelled preview (`game_equivalence` False).

## Dome glass (`39FDC0B9`, `uKind` 21)

The graph has two forward programs. Pass 0 (`39FDC0B9`) reads the `_n` map and multiplies the specular by the material tint; pass 1 (`0740DDCC`) has the geometric normal and no tint, so it can only draw grey. The game shows a saturated green dome, so the in-view draw is taken to be pass 0 (inferred; what selects a pass is not known).

CB2 by key (the material stores every key; other materials of the graph fall back to the graph defaults):

| Symbol | Key | `rv_dep_arch_star_chamber_glass_green` |
|---|---|---|
| tint `T` | `0x4B4DAB19` | (0.1169707, 0.3564001, 0.0168074) |
| rim bias `P3` | `0x42170E5E` | 0.352 |
| gloss | `0x24F4CA4C` | 0.806 |
| F0 base `P8` | `0x62835AF9` | 1.0 |
| UV scale `s` | `0x9C3D1E95` | 0.5 |

`_n` (slot `0x49A7866B7AE34E7A`, BC5, linear) and `_g` (slot `0x49A7866B1576D848`, BC7 sRGB) are read at `UV0 s`. The material also binds a `_c` under a slot the graph does not declare, so `_c` is never sampled.

```
N'    = f normalize(x T + y B + z n),  (x, y) = 2 CB0[95] (1 - fade) (_n.rg - 0.5),  z = 1 - x^2 - y^2
rim   = (1.001 - sat(N'.V))^0.65
a0    = sat(P3 + rim);  discard when a0 < attribute0.z;  alpha = sat(a0 attribute0.w)
E     = rim^1.678
gloss = sat(lerp(CB2 gloss, _g.r, E))          GGX alpha 2^(-5.5 sqrt(gloss))
F0    = lerp(P8, _g.g, E)                       grey, F90 = sat(60 F0)
rgb   = T * S                                    S = specular light (light list, sun, probe weighted by F0)
```

There is no diffuse or body term: the program reads no albedo. At facing angles `E` is close to 0 and the glass is a near mirror (`F0` about 1, gloss 0.806) with alpha 0.37 to 0.39; toward grazing angles `_g` takes over and alpha reaches 1 from about 60 degrees.

The viewer builds `N'` in the taxi glass frame (`T = -dP/du`, `B` on the `+dP/dv` side, from screen derivatives), reads `_n` on the `_n` unit and `_g` on the window mask unit (role `_window_g`, as the [taxi glass](taxi-glass.md)), and draws `T * glassSpec(...)` with the viewer lights and environment alpha-over. The graph's opaque material (`tb_nov_window_glass_01_opaque`) is not the decoded blended draw and keeps the grey `_g` reading of the graph's deferred writer.

## Dwarf star (`05C96D48`, `uKind` 22)

The G-buffer passes of this graph write zero albedo and zero specular, so the forward pass paints the whole look. The program reads five noise images bound by graph slot (the material binds none), in image-unit order:

| Unit | Slot | Image | Channels | Viewer role |
|---|---|---|---|---|
| 0 | `0x734CB25A` | `fx_noisetile04_n` (BC5) | x, z (z reads 0) | `_window_mask` |
| 1 | `0x4AC18E9F` | `fx_noise_3channel_01` | x, y | `_window_n1` |
| 2 | `0xABDAD780` | `hydro_noise_flame_05_sm` | x | `_window_n2` |
| 3 | `0x6437A619` | `hydro_noise_flame_03_sm` | x | `_window_c` |
| 4 | `0x2EA0E356` | `sun_base_noise_ao_01` | x | `_pick` |

With `t = CB0[99]`, `(u, v)` = UV0, `k = CB2[0]` (key `0xE994800B`, 0 for this material):

```
s0 = T0(2u + 0.01t, v - 0.03t)                  s1 = T1(5u + 0.025t, 4v + 0.025t)
U  = u + 0.04 (s1.x + 0.2746773 (s0.x - s1.x))   V = v + 0.04 (s1.y + 0.2746773 (s0.z - s1.y))
n2 = T2(7U - 0.14 + 0.03t, 4.5V - 0.09 - 0.002t)
n3 = T3(8U - 0.16 + 0.02t, 5V - 0.10 + 0.005t)
n4 = T4(4U - 0.08 + 0.015t, 3V - 0.06 + 0.005t)
m  = min(1, |min(1, |n3 - n2|) - (0.9 n4 - 0.7)|)
b  = sat(0.95 - 0.25 m);  d = sat(5 (b - (1 - P)) / max(1e-5, P));  discard unless d >= 1 and d >= attribute0.z
g  = sat(|k|^0.4) + 0.05 sin(t (5k + 3))
f  = (1.001 - sat(N.V))^3
Nc = ((1 - m)^(4 - 2.5g), (1 - m)^(20 - 15g), (1 - m)^(100 - 70g))
Rm = (0.4072402 f^0.1, 0.06847817 f^3, 0)
E  = lerp(Nc, lerp(Rm, Nc, 0.16202937), f)
s  = max(0, 1.055 c^(1/2.4) - 0.055),  c = instance record vec4 11
rgb = 3 A (3g + 4) F s E + (1 - F) fog,  A = CB0[115], F = attribute3.w;  alpha = sat(attribute0.w d)
```

`E` is a white-yellow-orange-red ramp of the noise (red falls as `(1 - m)^4`, green as `(1 - m)^20`, blue as `(1 - m)^100`) with an orange-red rim at grazing angles. With `k = 0` the brightness breathes by about 4 % with period `2 pi / 3` s, and the six UV layers scroll at different rates.

The viewer draws it opaque and two-sided in the forward pass, with the five images on the window units and the `_pick` unit. `t` is 0 in still renders and the effect clock while a clip plays (`ModelViewport.EFFECT_KINDS`).

## Hangar glow (`FDF7FDFB`, `uKind` 23)

`basic_flat_color_incandescent` has a different CB2 layout from `basic_flat_color`:

| Dword | Key | Graph default | `dep_arch_hangar_glow` | Role |
|---|---|---|---|---|
| 0-2 | `0x7F058428` | (0.0343, 0.4397, 0.8308) | (0.049707, 0.658375, 0.693872) | colour `C` |
| 3 | `0x600B83DC` | 1.0 | 5.0 | `K3`, multiplies `C.r` in the emission scale |
| 4 | `0x62835AF9` | 1.0 | 0.01 | grey F0 (pass 3 only) |
| 5 | `0x77591534` | 1.0 | 1.0 | `K5`, added to the emission scale |
| 6 | `0x24F4CA4C` | 1.0 | 0.5 | gloss, used as `sqrt(sat(gloss))` |

```
E     = C (K3 C.r + K5)                              = (0.06206, 0.82200, 0.86632) for this material: cyan
rgb   = T' (L' + E) + (1 - T') fog,  L' the specular light loop (zero albedo, no diffuse)
alpha = sat(x attribute0.w),  x = 1 unless the instance's engine dissolve image is on
F     = F0 + (sat(60 F0) - F0) Fc with F0 = CB2[4] in FDF7FDFB;  E16F8FC0 uses 0.9 Fc + 0.1
```

The values are read strictly, as for `basic_flat_color`: a malformed explicit value is rejected, never replaced by the graph default. The viewer draws `glassSpec(N, V, ..., F0, gloss) + E` with the flags word's additive blend, back-face cull and no depth write. The glow has no time term and no `CB0[115]` amplitude.

## Decoded and inferred

Decoded (IR, native code and archive data):
- the dome glass `N'`, rim, alpha and its cut, `E`, F0, gloss and the tint on the whole specular (checked numerically against the IR);
- every arithmetic step of the star program, its UV transforms and scroll rates, the image units, slots and channels, the dissolve and cut discards, the pulse, the record-colour encode and the `3 A (3g + 4)` scale;
- the glow emission `C (K3 C.r + K5)`, the CB2 layouts and values, the F0 of both glow programs and the gloss form;
- the flags words (blend and cull) and the material values.

Inferred or presentation values, kept visible in each rule's constants and the Blender limitation:
- dome glass: that pass 0 draws in view; the cut-off `attribute0.z = 4/255` (borrowed from the taxi glass capture), `attribute0.w` = material byte 22 / 255, `CB0[95] = 1`, instance fade 0, geometric AA 0;
- star: `A = CB0[115] = 1/12` (not captured for this frame; with it `3 A (3g + 4) = 1` at `t = 0`), the record colour taken as white, dissolve `P = 1`, cut 0, `attribute0.w = 1`, fog transmittance 1, wrap samplers, the BC5 third channel read as 0, the API order of the indirect texture table;
- glow: which of the two programs draws in game (the rule takes `FDF7FDFB`'s F0); `attribute0.w` is taken as 1 although the material's byte 22 is 127 (with the `basic_glass` reading of that byte the strips would add half as much); that the G-buffer passes are not drawn for the additive material.

Engine terms the viewer does not reproduce: the light list, sun with its cascade shadow, reflection probes and local cubes, screen-space reflection and fog. The viewer lighting stands in for them, and colour is tonemapped before blending.

## Blender

`game_shader._viewer_rule_glass` builds the three rules (mirrored by `materials.viewer_graph_rule`, checked by `rc2016/shaders/validation.py`):
- dome glass: `N'` from `_n` with the Blender UV tangent, alpha with its cut, `E`, gloss and F0 from `_g` at `UV0 s`, the lobe with `T` on F0 and on the Principled specular tint, no diffuse, alpha-over;
- star: emission only (`VR glass emission`), opaque, two-sided, five image nodes at the frozen `t` (`VR glass time` drives the scrolls), the record colour and amplitude as named constants;
- glow: emission `C (K3 C.r + K5)` plus the lobe, additive, back faces culled.
