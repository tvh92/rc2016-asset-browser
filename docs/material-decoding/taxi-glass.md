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

## What the preview draws

The viewer samples `H` from the `_pick` unit, derives `Pu` and `Pv` from screen derivatives and draws alpha-over with the captured blend. The colour is not decoded: the program walks the engine light list and probes, and the captured shader output (about 0.02 to 0.07 over backgrounds of 0.3 to 3) rules out a lit diffuse body, so the rule draws only a specular lobe with F0 = `sat(C / luma(C))` x 0.04 (a dielectric constant of the preview, not a decoded value) and gloss 1.

## Limitations (kept visible in the rule and the Blender material)

- `game_equivalence` is False: the colour terms, fog, SSR, `attribute0.w` (material byte 22, 1 as captured) and the instance fade words (frozen to the captured record) are not established for other draws.
- `attribute4.w = 0` and the zero rotation hold for this mesh and material; the rule is not bound to other materials of the graph.
- The viewer's tangent frame comes from screen derivatives, not the vertex tangent; the face sign `f` assumes the viewer's winding matches the game's.
- Blender samples `B` at the unshifted UV0 (the offset needs the vertex tangent frame), so `B = A`.
- The second program of the graph (`0x11408DE9`, deferred) is not selected.
