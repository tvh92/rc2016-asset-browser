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

With `attribute2.w` = 1 and `CB2[4]` = 0, the compiled program equals, to double precision at 200 random inputs (random `CB0[99]` and `CB0[107]` included):

```text
rgb = 2 A (1 + m) (base + rim F^5 + m C g)
m   = sat((s - c) / max(1e-5, 1 - c)),   s = API6.R(2u + 0.3, v/2 - t) + API5.G(1 - 2v + t, u/4 - t),   t = CB0[99],   c = CB0[107]
F   = max(N.V - 0.00100005, 0)
```

`c` is a dissolve threshold: below it the streak is cut, above it the remainder is stretched to 1, so 0 shows the full bolts and 1 removes them. Twenty-odd other effect programs (thrusters, scorch marks, laser beams, shields, the prisoner number displays) read `CB0[107]` the same way. In game it is not a per-effect fade but a frame value that changes every frame (see below), so the bolts flicker frame by frame. The viewer and Blender front-end carry it as the input `fade` (`VR ion tubes fade`), 0 for static renders.

Here `C` is `CB2[0..2]`, `g` is `CB2[3]`, `A` is the engine-owned `CB0[115]`, `base` = (0.0074990, 0.0122865, 0.0193824) and `rim` = (0.0307053, 0.1289768, 0.1964781) are literals of the program, and `(u, v)` is UV0. `N` is the interpolant `attribute1.xyz`, which the sibling deferred writer packs as its normal. `V` is `attribute2.xyz`, which the vertex programs export as a camera-relative vector (the eye position minus the vertex position; the form is read from the vertex code, not verified against a capture). The sign of `N.V` follows `FRONT_FACE`, as in the viewer.

The `CB2[4]` terms are `|CB2[4]|^1.5` powers (sat of it, and exp2 of 1.5 log2 of it) that vanish at 0. The rule applies only while `CB2[4]` stays 0 and both texture slots bind one image; any other material gets no rule.

## Engine inputs in the capture

`A`, `c` and `t` are dwords of `CB0`, the frame constant block. Neither the material, graph, atmosphere, level, zone, config, visual-effect nor actor data stores them per draw. The only capture is a Veldin frame (shadPS4 RenderDoc), so it has no Victor draw, but it settles what kind of inputs they are:

- **They are frame-level.** A replay that reads each draw's own bound `CB0` (fragment buffer binding 0 at its descriptor offset plus the low byte of `buf_offsets0.x`) finds the same dwords 99, 107 and 115 at every draw that binds the frame block. That covers the lighting pass, the G-buffer draws and the forward effect draws that read dwords 107 and 115. No per-effect, per-actor or per-material value can reach the program through them.
- **The captured frame.** At the lighting event (`CB0` bound at byte 54186240 of shadPS4's 64 MB constant ring), `t` = 259.7947, `c` = 0.3936, `A` = 0.01, fog colour `CB0[112..114]` = (1.665, 3.720, 5.201). The ring also keeps 20 earlier frames, each uploaded about 1150 times. Reading the dump from byte 0 (the oldest frame in the ring) gives a different frame: `c` = 0.5245.
- **`t` is game time in seconds.** Across the 21 frames it advances by the frame time, about 1/30 s per frame (0.0333 to 0.0354 s).
- **`c` is redrawn every frame.** The 21 values run from 0.018 to 0.965 (mean 0.56). Neighbouring frames are uncorrelated, and the spread is consistent with a uniform draw in [0, 1) (Kolmogorov-Smirnov D = 0.22 against a 5% critical value of 0.30). So the engine supplies a new frame-global random value each frame. The generator and its exact distribution are inferred from these samples, not decoded.
- **`A` is 0.01 in every frame** (bits `0x3C23D70A`). The Veldin forward effects use it the same way, as an emission scale before the fog blend (for example `T A colour (1 + 20 x) + (1 - T) fog`). Whether it is a level setting or an engine constant is not established. Two `atm_veldin` header floats equal 0.01 (offsets 37 and 72), but 0.01 is common there, and one capture cannot single out a field.

## What is not decoded

- `A` for Victor's arena. The capture only gives the Veldin value. Through the captured display chain (exposure 4.55, tone curve, sRGB; `rc2016/levels/display.py`), a streak core (`m` = 1, facing the camera) shows as roughly (0.07, 0.34, 0.47) at `A` = 0.01, (0.46, 0.93, 0.99) at 0.1 and (0.88, 1, 1) at 0.5 (identity colour grade). Victor's arena may use a different `A` and a different exposure, so the brightness is open. The preview keeps `A` = 0.5, the labelled presentation value that the other `CB0[115]` rules use (`2A` = 1). The constant `amplitude_captured` records the Veldin 0.01.
- The sequence of `c`. Static renders use `c` = 0 (full streaks, the upper envelope of the flicker; labelled presentation). While an animation clip plays, the model view replaces it once per 30 Hz game frame with a hashed value in [0, 1) (`ion_tubes_fade` in `rc2016/viewer/shading.py`). This presentation stand-in matches the captured behaviour, not the engine's numbers.
- The origin of `t`. The model view runs `t` as a wall clock in seconds while a clip plays and holds it while paused. Seconds at rate 1 is the captured unit. The rest pose, static renders, thumbnails and the Blender front-end keep `t` = 0 and `c` = 0 (the Blender `VR ion tubes time` and `VR ion tubes fade` nodes can be keyed or driven).
- `attribute2.w` is a vertex fog transmittance: exactly 1 in some vertex variants and `sat(exp2(-tau))` in others. It is frozen at 1 (no fog), so the fog colour `CB0[112..114]` drops out.
- The vertex alpha `attribute0.3` is unused for an opaque material. The discard predicate on `attribute0.2` is not translated, and which pass the engine selects at draw time is not established.
- Viewer lighting, tonemapping and the dirty glass shell are separate from this term. `game_equivalence` is false.

## Settling `A` with a capture

A shadPS4 RenderDoc capture of a frame with Victor's tubes on screen would settle `A` for the arena. Record it with the tubes visible, then dump it with the existing RenderDoc tooling (`qrenderdoc_run.py` wrapper, as in the shading toolkit's README). Find the forward draw whose fragment shader matches the disassembly of `86DDE345`: one colour target, two samples of one 64x512 image, the `0.3` and `0.25` UV constants, and reads of `CB0` dwords 99, 107 and 115. Then read that draw's bound `CB0` at its descriptor offset plus `buf_offsets0.x`, not at byte 0 of the ring: dwords 99, 107, 112 to 115 and 135. From the same event, read the vertex output that feeds `attribute2.w`. Two or three frames give the frame step of `t` and confirm the per-frame `c`.

The viewer implements this as `uKind` 12 (`rc2016/viewer/shading.py`) and the Blender front-end as an unlit emission (`_viewer_rule_ion_tubes`, constants `VR ion tubes ...`, validated by `rc2016/shaders/validation.py`). Archive-backed checks are in `tests/test_ion_tubes.py`.
