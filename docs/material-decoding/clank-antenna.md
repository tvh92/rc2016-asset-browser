# Clank's antenna light (`hero_bright_center_light`, writer `7BEC2D2F`)

Material `material/character/hero/hero_clank_antenna/hero_bright_center_light_clank.material` (SHA-256 `5f3f1641...87a8`, the only material on the graph) uses graph `materialgraph/character/hero/hero_bright_center_light/hero_bright_center_light.materialgraph` (SHA-256 `d3e43dc0...4d28d`). The graph has two compiled deferred writers: `7BEC2D2F` (code SHA-256 `cd0b14ef...e4e1`, pass contract 0, the antenna surface) and `11408DE9` (the zero-output placeholder). The other four programs are forward light-loop or exec-masked (discard) programs and are not modelled. Archive-backed checks are in `tests/test_clank_antenna.py`.

The writer is the sibling of the Qwark light writer `D14A48D7` (graph `hero_bright_center_light_variable`, see [viewer](../viewer.md)). It is a different graph with different arithmetic, so it has its own rule, bound to the graph SHA-256 in `GRAPH_RULES` (`rc2016/viewer/textures.py`).

## Decoded surface

All expressions were read from the lowered source DAG and evaluated numerically against it (N.V from 0 to 1, time 0 and 0.25).

```
t      = |1.001 - sat(N.V)| ^ CB2[3]
w      = B + (1 - B) (0.5 sin(2 pi fract(4.2 CB0[99] / 2 pi)) + 0.5)
albedo = _c.rgb * (E t + 10 C w (1 - t))            (per channel; E = CB2[0..2], C = CB2[4..6])
F0     = CB2[9]                                     (grey)
gloss  = sqrt(0.35)  at specular-AA width 0         (baked; no gloss key)
self-lit = K albedo.R                               (K = CB2[7]; emission = exp2(5 * 0.2 log2(1 + K albedo.R)) - 1)
```

`N.V` is negated on back faces (`FRONT_FACE`). The sine phase is `4.2 CB0[99]` with no per-vertex term, so freezing `CB0[99]` at 0 gives the exact `sin = 0` weight `w = B + 0.5 (1 - B)` = 0.575.

| Role | Key (CB2 dword) | Value used |
|---|---|---|
| E (edge colour) | vec3 `0x16B8AB81` (0..2) | (0, 0, 0), graph default |
| C (centre colour) | vec3 `0x5BC2D1F0` (4..6) | (1, 0.5271, 0.4621), graph default, times the literal 10 |
| exponent | `0x9E351D7A` (3) | 0.01, graph default (a direct exponent, not Qwark's 0.2 (1 - S)) |
| K | `0xB7305C21` (7) | 15, graph default |
| B (blink floor) | `0x9872D04A` (8) | 0.15, graph default |
| F0 | `0x08FE9F29` (9) | 0.2, graph default |

The material stores none of these keys. Its stored rows (`0x24F4CA4C`, `0x62835AF9`, `0x66EFA55B`, `0x7F058428`, `0x5FD56522`, `0x41E7FE54`, `0x600B83DC`, `0xD21B698F`, `0x43C53ECA`) belong to another graph and are not read, so the graph defaults apply. In particular `0x66EFA55B` (6.0) is not the self-lit K here.

Textures: the albedo is `clank_antenna_c.texture` (64x64), the material's override of the graph slot `2883245952` whose graph default is `white_c`. No `_g` is read. API 6 is `default_pink_shader_n`, a flat BC5 tangent normal (R 125/255, G 128/255).

With the exponent 0.01 the factor `t` is 0.93 at N.V = 1 and approaches 1 elsewhere, so the antenna is dark (E = 0) except for a small bright core of `10 C w (1 - t)` times the `_c` colour; at N.V = 1 albedo.R is about 0.38 for a white `_c`, and the emission is `K albedo.R`.

## Rule and preview status

`hero_bright_center_light` (exact graph SHA-256) reuses the viewer's `bright_center` term with the values above (`bright_center = 'clank'` in `GRAPH_RULES`). Blender builds the same `VR bright centre ...` constants and passes reopen validation. The term carries `writer = 7BEC2D2F`, the graph path and SHA-256, `frozen_inputs = {CB0_99: 0}` and `game_equivalence = False`; Blender records the frozen inputs as a limitation. The Qwark sibling rule is unchanged.

Native translation of the writer is not possible. Freezing `CB0[99]` through `SOURCE_PREVIEW_POLICIES` (the convention of [weapon-gadget-frozen-time](weapon-gadget-frozen-time.md)) does not make it ready, because five more engine-owned inputs remain. No policy is registered, since it would change nothing.

## Engine-bound inputs

| Input | Role in the writer | Treatment |
|---|---|---|
| `CB0[99]` | time of the blink phase `4.2 CB0[99]` | frozen at 0 in the rule (labelled preview value; the engine upload is unverified) |
| `attribute2.xyz` | interpolated vertex normal, normalised, for N.V | the viewer's geometry normal stands in |
| `ps.POS_X/Y/W`, `CB0[92..106]` | screen ray, normalised, giving V | the viewer camera stands in |
| `ps.FRONT_FACE` | flips N.V on back faces | the viewer's face orientation |
| `attribute3.xyz`, `attribute3.w` | tangent and handedness; rotate the normal-map term only | not used |
| `CB0[95]`, two `TBUFFER_LOAD_FORMAT_X` fetches (descriptor API 1, register 32, index `attribute2.w`) | normal-map strength `2 CB0[95] (1 - sat(b + POS_W a))`, a per-instance close-range fade | not reproduced |
| `CB0[139]`, `attribute1.2`, `POS_W` | specular anti-aliasing width of the gloss encoding | width 0, which gives `sqrt(0.35)` |

The normal-map term is bounded: the API 6 texture is flat to 1% (R - 0.5 = -0.0098, G - 0.5 = +0.0020), so the tangent-space tilt is at most `0.0196 CB0[95]` and the geometry normal is within about 1 degree of the writer's normal for `CB0[95]` near 1. The value of `CB0[95]` itself is an engine upload that is not decoded.

## Audit of the sibling writer `D14A48D7`

The [viewer](../viewer.md) row for `hero_bright_center_light_variable` is correct for the albedo form, F0, gloss, the self-lit rule and the exponent: evaluating the lowered DAG with the documented keys reproduces `t`, `E t + C w (1 - t)`, F0 = `0x08FE9F29`, gloss = `sqrt(0.35)` (G key `0x0B065E94`) and emission = K albedo.R. It is incomplete in the blink term.

- The phase of `D14A48D7` is `rate CB0[99] + 1000 |attribute3.w|`, with rate = key `0xB162C569` (CB2 dword 9, graph default 4.0). The per-vertex term was not documented. `attribute3.w` is the tangent handedness: the same writer builds the bitangent from `sign(attribute3.w)`, which must be non-zero (a zero value makes the normalised bitangent undefined), and the vertex program writes it as a sign-valued integer in its main branch. With `|attribute3.w| = 1` the sine argument at time 0 is 1000 rad, `sin(1000) = 0.8269`, and the weight is `sat(1 + (1 - B)(0.5 sin - 0.5))` = 0.9264, not the 0.575 of `sin = 0`. The viewer's weight 0.575 is therefore a labelled review freeze, not the writer's value at time 0, and the centre albedo it draws is about 35% lower than the writer's at time 0 (albedo.R per unit `_c.R` at N.V = 1 is 0.0613 against 0.0401 drawn, for E.R = 0.00605, C x intensity = 0.469 and S = 0.9). The magnitude `|attribute3.w| = 1` is inferred from the sign-valued vertex word, not read from mesh data.
- The graph's tangent-mapped normal has the same `CB0[95]` and two-`TBUFFER` fade as Clank's writer; the viewer's normal-map reading is the unfaded one.
- The glow shell `93695188` has the same `1000 a3.z` phase term (see [viewer](../viewer.md#antenna-glow-shell)); its `a3.z` is a different attribute component.

The viewer still draws the Qwark light with the `sin = 0` weight; Blender now states in its limitation that the writer's own phase is not zero at time 0.

## Not decoded

- The runtime values of `CB0[99]`, `CB0[95]` and the per-instance fade buffer.
- The forward light-loop programs `D96A497B` and `457B6AC7` and the exec-masked programs `655CBE88` and `48EAC24B` of the graph, and which program the game selects per draw.
- Vertex tangent handedness magnitude (see the audit above) and the mesh stream that feeds `attribute2`.
