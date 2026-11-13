# Test dummy energy ball

`character/enemy/enm_test_dummy/enm_test_dummy.model` has three meshes: the body (`enm_Test_Dummy`, `basic_normal_gloss_emissive`), the glass of the eye band, ear lamps and antenna bulb (`jn_kal_glass_01`, the `glass_jn` rule) and the sphere the dummy rides on (mesh 2, 287 vertices, 480 triangles, material `enm_Test_Dummy_Energy_Ball`). The sphere's graph is its own forward-only effect, so the generic reading drew it as an opaque white ball. In the game it is a translucent cyan energy ball with scrolling bolts.

| Material | Graph (SHA-256) | Forward programs | Flags word |
| --- | --- | --- | --- |
| `enm_Test_Dummy_Energy_Ball` | `enm_test_dummy_energy_ball` (`8a7ed8e3...96cc3`) | `DB658C65` (pass 0), `7DB1911C` (pass 1); zero writer `11408DE9` | `0xA`: alpha-over, back faces culled |

The graph has no CB2 (both pass contracts are 0 bytes) and three texture resources, in this order: `electricity_line_01` (BC7, 64 x 512, three different bolts in R, G and B), and `test_dummy_energy_ball` (BC4, 256 x 256, a chevron strip) twice. The rule is `energy_ball` (`uKind` 25), matched by exact graph path and hash; `game_equivalence` is False.

## Evidence

- A shadPS4 capture of a corridor with six test dummies draws the ball once per dummy (1440 indices each). The draws bind the graph's three images in resource order (image units 0, 1, 2), use SRC_ALPHA / ONE_MINUS_SRC_ALPHA, depth test GREATER_EQUAL without depth write and back-face culling, as the flags word says.
- The capture runs program `817D25E0`. Its IR has the same terms as the archive's `DB658C65` (read from the shadPS4 IR dump) except the view vector: `DB658C65` interpolates it, `817D25E0` rebuilds it from the screen position and the CB0 camera rows 92 to 107.
- Per-vertex outputs of the captured draw: attribute0 = (u, v, 4/255, 1.0) (UV0, alpha cut-off, opacity), attribute1.xyz = the normal, attribute2.w = 1.0 (fog transmittance). The material's flags block gives the same cut-off and opacity (byte 22 = 255).
- Frame constants of that draw: `CB0[115]` = 0.18306638 (`A`), `CB0[99]` = 117.06222 (`t`, seconds), `CB0[107]` = 0.362 (`r`, a new value each game frame), fog colour `CB0[112..114]` = (1.639, 2.985, 6.011).
- Per-pixel check: the captured post-VS mesh was rasterised and the formula below evaluated with the captured textures (trilinear, wrap, LOD from the coordinate footprint) and blended over the target before the draw. Over the 1196 pixels the draw changes, the predicted change correlates 0.99992 with the captured one and the mean colour is within 0.7 %. The 114 predicted pixels the draw does not change are behind the dummy's foot (depth test).

## Program

Inputs: `u, v` = attribute0.xy (the viewer's UV0), `N.V` = sat(N . V) on the front face, `r` = CB0[107], `t` = CB0[99], `A` = CB0[115], `F` = attribute2.w.

```text
E  = electricity_line_01(1 - 2v - r, u + r), channel floor(3r): 0 -> R, 1 -> G, otherwise B
n1 = test_dummy_energy_ball(0.75 u + 0.125, v - 0.25 t).r
n2 = test_dummy_energy_ball(u, v - 0.5 t + 0.474).r
S  = E + n1 + 0.219 n2 + min(1, |1.001 - N.V|)
discard sat(S) < attribute0.z
alpha = sat(attribute0.w * sat(S))
rgb   = lerp(fog CB0[112..114], A (1 + S) (0.8894421, 5, 4.523306), F)
```

The program reads no light, probe or shadow input. The electricity coordinate is a 90-degree rotation of `(u + r, 2v + r)` about 0.5 (the `-4.371139e-08` terms are cos(pi/2) in float32).

## In the viewer and Blender

- Viewer (`rc2016/viewer/shading.py` `GLASS_FRAGMENT`, `uKind` 25): the three images sit on the window texture units (roles `_window_mask`, `_window_n1`, `_window_n2`); the frame blends in the float scene target and is tonemapped once, as the game blends it in its HDR target. The static values are the captured frame's `A`, `t` and `r`; while a clip plays, `t` is the effect clock and `r` the `frame_fade` stand-in (the engine's generator of `CB0[107]` is unknown, so the bolt changes channel and offset each 30 Hz frame but not in the game's sequence). Fog is the viewer fog (`F` = 1).
- Blender (`rc2016/pipeline/game_shader.py`): the same terms as nodes into an emission shader, alpha-over and back-face culled; the nodes "VR glass time" and "VR glass frame random" can be driven to animate.

## Not a material difference

In the capture the dummy's face shows an open eye slit with a cyan glow behind the eye glass. The viewer's bind pose shows the face plate closed: the skeleton has `eyelid_upper` and `eyelid_lower` joints, so the open slit is a pose, not a shading term.
