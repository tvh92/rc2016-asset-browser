# Taxi glass: forward program `0x4E9B2CB3`

The canopy glass of the taxi (`environment/global/gameplay/gbl_gp_taxi_01/gbl_gp_taxi.model`) uses `material/environment/global/gameplay/gbl_gp_taxi_glass_01/gbl_gp_taxi_glass_01.material` (flags word: opaque) on the graph `materialgraph/environment/global/architecture/mo_gbl_arch_glass_01/mo_gbl_arch_glass_01.materialgraph` (SHA-256 `8c2b13e3394d8aaeacede412ab62736e7b7ea672d70689eee59f802f19e0653a`). The graph holds two programs: the forward program `0x4E9B2CB3` described here and the deferred writer `0x11408DE9`, which it shares with the hydro harvester tank graph. The rule (`glass_taxi`, `GLASS_FRAGMENT` `uKind` 17) is bound to the exact graph hash and to this material only.

## Captured draws

A shadPS4 capture draws the glass twice, both 1164 indices with vertex program `0x4FE7233C`: one covers the canopy (about 38,700 pixels), the other only 35 pixels at the screen edge. Both use `SRC_ALPHA` / `ONE_MINUS_SRC_ALPHA` (colour and alpha, ADD), depth test GREATER_EQUAL without writes and no culling, so the glass is alpha-over although the flags word says opaque. The instance flags are `0x2061` (bit `0x80` clear). The material block (CB1, 20 dwords) equals the material file value for value, and the frame's `CB0[95]` is 1. Alpha solved from pixel history (`(pre - post) / (pre - shader)`) spans about 0.1 to 0.8 across the canopy.

The post-vertex-shader outputs of the draw give the inputs the program reads from the mesh: `attribute0` = (UV0, 4/255, 1), `attribute2` = normal, `attribute3` = tangent with the handedness in `w`, `attribute4` = 0 at every vertex, `attribute5` = camera-relative position. The game's tangent equals `-dP/du` and its bitangent `+dP/dv` on every captured triangle.

## Decoded alpha

The program computes `alpha = sat(attribute0.w sat(lerp(W (A + B), s e, m)))`, with `m = attribute4.w`, `W = CB1[7]` and `s e` a texture and view-angle term that only matters for `m > 0`. For the taxi mesh `m = 0`, so

```
alpha = sat(attribute0.w sat(W (A + B))),  W = 0.5
A = H(UV0)
B = H(UV0 + depth f (1 - fade) (-V.Pu, -V.Pv))        depth = CB1[14] = 0.05, CB0[95] = 1
fade = sat(view depth R63 + R62)                       R62 = -2.33333, R63 = 0.071561 (instance record words)
discard when sat(W (A + B)) < attribute0.z = 4/255
```
`H` is `gbl_gp_Taxi_glass_alpha_H` (BC4, red channel; the material binds it at both graph slots `49A7866BA1D220DE` and `49A7866BE0B8D722`), `f` is +1 on front faces and -1 on back faces, `V` the unit vector to the camera, and `Pu`, `Pv` the unit `dP/du` and `dP/dv`. The rotation angle of the first read is `CB1[11] t + CB1[9]`, both 0 for this material, so `A` is read at UV0 unrotated. With `V.T = -V.Pu` and `V.Bt = V.Pv` the program's offset is `depth f (V.T, -V.Bt)`.

Check against the capture: the closed form (mip 0, bilinear) against alpha solved from pixel history at 40 bright-background pixels has a mean absolute error of 0.020 (0.035 with `A` alone); the remaining error is the game's mip and anisotropic sampling. The fade is 0 at the captured depths (7 to 12), so the offset is `0.05 f` there.

## Decoded colour

The RGB export is `fog + t (X - fog)` (fog colour `CB0[112..114]`, `t` the fog transmittance, raised by a screen-space reflection where one hits), with

```
X_c   = A_c (lerp(C_c, D_c, H) T5_c + w E_c) + S_c
H     = alpha_H.R at UV0 (the alpha's A read)        T5 = window _c (API 5) at UV0, sRGB-decoded
C     = CB1[0..2] (0.694, 0.658, 0.485)              D = CB1[4..6] (0.074, 0.014, 0)          w = CB1[15] (0.25)
N'    = f normalize(T x + B y + n z),  (x, y) = 2 CB0[95] (1 - fade) (_n.rg - 0.5),  z = 1 - x^2 - y^2
        n, T the normalised vertex normal and tangent, B = sign(T.w) normalize(n x T), _n = window _n (API 9, BC5) at UV0
gloss = sat(CB1[8] _g.r) (GGX alpha 2^(-5.5 sqrt(gloss)))    F0 = CB1[10] _g.g, F90 = sat(60 F0), grey    _g = window _g (API 8, sRGB)
```

`fade` is the alpha's instance fade (record words 63 and 62). `A_c` is the diffuse light at `N'` (probe-volume ambient, sun with the cascade shadow, light-list diffuse, the type-2 volume multiplier), `E_c` the reflection along `R = 2 (N'.V) N' - V` (a loop over local box-projected cubes, records from `CB0[119]`, blended with the probe-volume reflection), `S_c` the specular light (probe reflection, sun and light-list lobes with the `F0` and gloss above; no tint). These engine terms are the code the [Novalis window glass](novalis-window-glass.md) program shares. The textures are bound by graph slot: `_c` `49A7866BABDAD780`, `_g` `49A7866B1576D848`, `_n` `49A7866B7AE34E7A` (all `mo_gbl_window_glass_01`, matched to the captured bindings by top-mip hash).

So the glass is a lit, tinted dirt layer, not a specular-only pane: where `H` is moderate the body colour is the warm grey `C` lit by the scene's diffuse light, which in a daylight scene gives the bright hazy look. In the captured (night) frame `A` is small, so the shader output is dark (0.002 to 0.45).

**Check against the capture.** The research GCN interpreter runs the real program on the captured quads of draw 13644 with the dumped constants, light list, probe bricks, cube slices, textures and shadow atlas. Against the shader output from pixel history at 95 single-fragment pixels: mean relative error 0.26 %, maximum 2.3 % (mean absolute 5.6e-5). The closed form above, with `A`, `E` and `S` read back from interpreter runs with edited inputs (`T5` = 0 / 1, `w` = 0 / 1), reproduces the interpreter to 2.5e-4 relative on all 95 pixels, and also predicts runs with changed `C`, `D` and `w` (7e-4 relative maximum); against the capture its error is the interpreter's (mean 0.26 %, maximum 2.4 %). At the captured pixels the body term `A lerp(C, D, H) T5` is 90 % of the output on average (12 % to 98 % per pixel), `S` 7 % and `w A E` 3 %. `N'`, the gloss, `F0` and `H` were checked against the translator's opaque-loop DAG of the program with random inputs (exact).

## What the preview draws

The viewer samples `H` from the `_pick` unit, the window `_n` on the `_n` unit, and the window `_g` and `_c` on two window-glass units (`shading.TAXI_GLASS_TEXTURES`, roles `_window_g`, `_window_c`; sRGB from the native format). It builds `N'` in a screen-derivative frame (`T = -dP/du`, `B` on the `+dP/dv` side), and draws `rgb = Ld(N') (lerp(C, D, H) T5 + w E(R)) + specular(N', F0, gloss)` alpha-over with the captured blend. `Ld` is the viewer's diffuse light (rig or HDRI ambient and lights), `E` the viewer environment along `R`, the lobe the shared glass GGX lobe with the viewer lights and environment.

## Limitations (kept visible in the rule and the Blender material)

- `game_equivalence` is False: `A`, `E` and `S` are the viewer lighting, not the engine's probes, local cubes, sun shadow and light list; no screen-space reflection; fog `t = 1`. `attribute0.w` (material byte 22, 1 as captured), `CB0[95]` (1) and the instance fade words are frozen to the capture.
- `attribute4.w = 0` and the zero rotation hold for this mesh and material; the rule is not bound to other materials of the graph.
- The viewer's tangent frame comes from screen derivatives, not the vertex tangent; the face sign `f` assumes the viewer's winding matches the game's. The game samples `_c` and `_g` with 2x anisotropic filtering.
- Blender samples `B` at the unshifted UV0 (`B = A`), builds `N'` with its own UV tangent and no fade, and leaves out the `w A E` term (about 3 % of the captured output).
- The second program of the graph (`0x11408DE9`, deferred) is not selected.
