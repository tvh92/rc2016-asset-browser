# Victor von Ion tubes

The exact graph `materialgraph/character/enemy/enm_victor_von_ion_tubes/enm_victor_von_ion_tubes_w_rust.materialgraph` (SHA-256 `8cdc2c05c2e934ecc56e6765992ee8734ada8af530dc9d42c1265578ff21b901`) and the exact material `material/character/enemy/enm_victor_von_ion_tubes/enm_victor_von_ion_tubes.material` (SHA-256 `d87dabcb497abb21f04af2a8d13689a0874371d319cf693bd3e1fb5312aa9251`) draw the lightning (the `electricity_line_01` bolt texture) inside Victor's chest tubes (the `pasted__energy_blades` mesh of `enm_victor`). The mesh is the same 1072-triangle tube geometry as the `enm_Victor_Dirty_Glass_Tubes` glass shell, so the glass material sits over it.

## Programs

The graph holds six pixel programs (two copies of three shaders). Their roles are read from the exports:

| Shader | Exports | Role |
| --- | --- | --- |
| `0x11408de9` | three deferred targets; albedo, F0, gloss and emission are literal 0; target 0 is the packed `attribute0` normal | zero-colour normal pass |
| `0x9f551dad` | the same, with the packed `attribute1` normal, under an exec mask `attribute0.2 <= 1` | zero-colour normal pass with a discard |
| `0x86dde345` (code SHA-256 `85dd7c3c...b20d15`) | one forward target (target 0), about 195 instructions, no loop and no light list | the visible colour |

So the deferred path draws a black, zero-specular, zero-emission cut-out and the colour comes from the forward program, as for the prisoner number displays. The Victor tubes are therefore not blocked by their discard predicate: the predicate only decides which pixels survive and is not translated.

## Forward program

The pass contract binds the CB2 words and two texture APIs (5 and 6). Both resolve to `textures/visualeffect/electricity/electricity_line_01/electricity_line_01.texture` (64x512, non-sRGB), in the graph slots `0x4AC18E9F` and `0x39DBF715`; the material overrides neither. The material stores `CB2[0..2]` (key `0x427C868D`) = (0.0953, 0.5906, 1.0) and `CB2[3]` (key `0x8A063007`) = 6.0. `CB2[4]` (key `0xEC98DCA7`) is not stored and defaults to 0. Flags `0x38`: opaque, one-sided.

With `CB0[107]` (a fade/dissolve threshold) = 0, `attribute2.w` = 1 and `CB2[4]` = 0, the compiled program equals, to double precision at 200 random inputs (random `CB0[99]` included):

```text
rgb = 2 A (1 + m) (base + rim F^5 + m C g)
m   = sat(API6.R(2u + 0.3, v/2 - t) + API5.G(1 - 2v + t, u/4 - t)),   t = CB0[99]
F   = max(N.V - 0.00100005, 0)
```

Here `C` is `CB2[0..2]`, `g` is `CB2[3]`, `A` is the engine-owned `CB0[115]`, `base` = (0.0074990, 0.0122865, 0.0193824) and `rim` = (0.0307053, 0.1289768, 0.1964781) are literals of the program, and `(u, v)` is UV0. `N` is the interpolant `attribute1.xyz`, which the sibling deferred writer packs as its normal. `V` is `attribute2.xyz`, which the vertex programs export as a camera-relative vector (the eye position minus the vertex position; the form is read from the vertex code, not verified against a capture). The sign of `N.V` follows `FRONT_FACE`, as in the viewer.

The `CB2[4]` terms are `|CB2[4]|^1.5` powers (sat of it, and exp2 of 1.5 log2 of it) that vanish at 0. The rule applies only while `CB2[4]` stays 0 and both texture slots bind one image; any other material gets no rule.

## What is not decoded

- `CB0[115]` is engine-owned. The preview uses the labelled presentation value 0.5, the established convention for the other forward rules. Its game value is unknown, so the brightness is not a decoded value.
- `CB0[99]` (`t`) enters only through the sample coordinates above: both bolt samples scroll with it at unit rate per texture repeat, so the bolts crawl along the tubes. It is time-like (the other forward rules use it the same way) but its engine upload, unit and rate are unverified. The model view therefore runs `t` as a wall clock in seconds while an animation clip plays, a labelled presentation value (held while paused); the rest pose, static renders, thumbnails and the Blender front-end keep `t` = 0 (the Blender `VR ion tubes time` node can be keyed or driven to scroll the bolts). `CB0[107]` is frozen at 0; its runtime upload is unverified.
- `attribute2.w` is a vertex fog transmittance: exactly 1 in some vertex variants and `sat(exp2(-tau))` in others. It is frozen at 1 (no fog), so the fog colour `CB0[112..114]` drops out.
- The vertex alpha `attribute0.3` is unused for an opaque material. The discard predicate on `attribute0.2` is not translated, and which pass the engine selects at draw time is not established.
- Viewer lighting, tonemapping and the dirty glass shell are separate from this term. `game_equivalence` is false.

The viewer implements this as `uKind` 12 (`rc2016/viewer/shading.py`) and the Blender front-end as an unlit emission (`_viewer_rule_ion_tubes`, constants `VR ion tubes ...`, validated by `rc2016/shaders/validation.py`). Archive-backed checks are in `tests/test_ion_tubes.py`.
