# Victor reticle material

The material graph `materialgraph/visualeffect/character/enemy/enm_victor_reticle/enm_victor_reticle.materialgraph` has SHA-256 `e4bcaf552be55cbc14b5081b6695a600084ae162889455b87eadc6c0103d79ea`. The bounded rule is guarded by that graph identity and ports writer `EB9C8254` (`0xeb9c8254`). Both compiled copies of that writer lower without unresolved output nodes. The graph's unsupported loop and exec-masked alternatives are outside this rule.

The writer samples API8 through slot `0x628AD5A840ED6C83`, whose default is `textures/visualeffect/trigger/trigger_linear_01_c.texture` (512×1 RGBA8 UNORM, non-sRGB). Material records include a similarly named `_c` texture, but the writer's API8 slot determines this sample. `CB2[1]` has material instance key `0xFD5FF119` and value 4.0. `CB2[0]` is the material UV phase (instance key `0x76C88A95`, current/default value 0), so the source coordinates are `(1, 8)` for this material. Separately, engine time `CB0[99]` is frozen at 0 in the preview. The ring tint is `CB2[2]` (instance key `0xCC2FE3CC`, default 0).

With UV0 `(u,v)`, `d = length((u,v) - (0.5,0.5))`, `b = 1-d`, and `e = abs(b)`, the writer's MRT1 albedo is:

```text
R = 2 A e
G = 2 A (e + r (e^10 - e))
B = 2 A (b^2 + r (e^20 - b^2))
```

Here `A` is engine-owned `CB0[115]`; it is absent from the material archive. The preview exposes it as the named `VICTOR_RETICLE_PRESENTATION_AMPLITUDE` input and uses the established 0.5 presentation convention. That is not a decoded game value. The albedo target is UNORM and the preview clamps it to `[0,1]` before deferred lighting. The writer's MRT2 terms are `F0 = (0, 0.0999755859375, 0.0999755859375)` and gloss `0`.

The MRT1 alpha stores self-lit data, not surface opacity. The source scalar is `exp2(5 * (0.20000000298023224 * log2(1 + API8.R * CB2[1]))) - 1`. The Viewer and Blender port add `clamped_albedo * source_scalar` through their shared deferred BRDF representation. Material flags `0x9` select additive, one-sided material state; the preview records this composition policy explicitly.

The image is a square source mesh, not a reconstructed in-game reticle. Time is frozen at zero; geometry normal substitutes for the unavailable portable tangent-normal slice; the Viewer uses its own lighting and tone mapping. Runtime pass selection, the engine-owned amplitude, animation/fade inputs, and exact additive pass composition remain unverified, so this rule is a bounded preview and `game_equivalence` is false. With the default 0.5 presentation amplitude, the actual Viewer capture saturates to pale white/yellow and hides some radial albedo variation; a lower diagnostic amplitude may be used only as an explicitly labelled display adjustment.
