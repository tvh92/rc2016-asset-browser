# Frame constants in CB0

Many graph programs read a few engine words from `CB0`, which is the frame constant block, not a per-material or per-draw buffer:

| Dword | Role in the programs | Captured behaviour |
| --- | --- | --- |
| `CB0[28..30]` | Camera world position (native Y-up), with `CB0[31]` = 1 | (69.204, 32.872, 148.530) |
| `CB0[92..106]` | Camera-ray rows: `ray = A + B y + x (C + D y)` over the pixel position, `A` = `CB0[92..94]`, `B` = `CB0[100..102]`, `C` = `CB0[96..98]`, `D` = `CB0[104..106]`; world position is `CB0[28..30] + view_depth * ray` | At the screen centre (960, 540) the ray is (-0.8216, -0.1479, -0.5504), the unit view forward `CB0[24..26]` = (-0.8219, -0.1475, -0.5502) to 4e-4, so `view_depth` is the depth along the view axis |
| `CB0[95]` | Normal-map strength factor (`2 CB0[95] (1 - sat(a + view_depth b))`, with `a` and `b` per-instance words from a typed buffer) | 1.0 |
| `CB0[120..121]` | Frame size in pixels | 1920, 1080 |
| `CB0[128..130]`, `CB0[140..243]` | Direction to the sun and the sun shadow cascades (lanes, light basis, per-cascade scale, offset and clamp rectangle) | Novalis frame: see [shadow atlas](shadow-atlas.md) |
| `CB0[139]` | Specular anti-aliasing scale (width = `CB0[139] view_depth attribute1.2 / (N.V)`) | 0.0010417 = 1/960 = `CB0[32]`, 2 / frame width |
| `CB0[99]` | Time: UV scrolls, blink phases, wobble | Game time in seconds; it advances by the frame time, about 1/30 s per frame |
| `CB0[107]` | Dissolve or cut threshold (Victor ion tubes, thrusters, scorch marks, lasers, shields), vignette width (prisoner numbers) | A new value in [0, 1) every frame |
| `CB0[112..114]` | Fog colour, blended as `T colour + (1 - T) fog` with a vertex fog transmittance `T` | (1.665, 3.720, 5.201) in the captured frame |
| `CB0[115]` | Glow, emission or albedo amplitude `A` (facing glow, bright glow, prisoner numbers, Victor reticle and ion tubes, Map-o-matic, thumper glove glass, lava) | 0.01 in every Veldin ring frame; 0.277088 and 0.04464268 in two Novalis frames |
| `CB0[135]` | Pass word tested bit by bit (`0x2`, `0x40`, `0x80`, `0x1C`), not a debug view | `0x40` in a G-buffer pass and its equal-depth forward pass, `0x2` in a blended forward pass (Thumper glove glass draws) |

None of these words is stored in the material, graph, atmosphere, level, zone, config, visual-effect or actor data.

## Evidence

The only capture is one Veldin frame (shadPS4 RenderDoc). The buffer the shaders read `CB0` from is shadPS4's 64 MB constant ring. It holds the current frame and 20 earlier ones, and each frame's block is uploaded about 1150 times (once per draw and stage). A shader reads dword `N` at the draw's descriptor offset, plus the low byte of the push constant `buf_offsets0.x`, plus `4N` bytes. The dump starts at byte 0, which holds the oldest frame in the ring, so dword numbers read from the start of the dump belong to a different frame. The lighting event binds the current frame's block at byte 54186240.

- **Frame-level.** A replay that reads each draw's own bound `CB0` finds the same dwords 99, 107 and 115 at every draw that binds the frame block. That covers the lighting pass, the G-buffer draws and the forward effect draws that read dwords 107 and 115. So no per-effect, per-actor or per-material value reaches a program through these words. A rule that needs a different value per object cannot get it from `CB0`.
- **Camera block.** The ray rows reproduce the captured view forward at the screen centre, which is the check that `camera + view_depth * ray` is world position. The same frame block gives `CB0[95]` = 1 and `CB0[139]` = 1/960, so the viewer's "strength taken as 1" matches the capture. The per-instance close-range fade words (`a`, `b` above, and `attribute1.2`) are not in `CB0`; they come from a typed buffer indexed by `attribute2.w`.
- **The captured frame:** `CB0[99]` = 259.7947, `CB0[107]` = 0.3936, `CB0[115]` = 0.01.
- **`CB0[99]`.** Across the 21 frames in the ring it steps by 0.0333 to 0.0354 s, the frame time.
- **`CB0[107]`.** The 21 values run from 0.018 to 0.965 (mean 0.56), and neighbouring frames are uncorrelated. The spread is consistent with a uniform draw in [0, 1): Kolmogorov-Smirnov D = 0.22 against a 5% critical value of 0.30. The engine's generator and exact distribution are inferred from these samples, not decoded. Any effect that reads it changes every frame: a cut threshold flickers, and the prisoner-number vignette width `0.5 + 0.1 CB0[107]` jitters between 0.5 and 0.6.
- **`CB0[115]`.** In the Veldin ring it is 0.01 in every frame (bits `0x3C23D70A`), at every draw. The Veldin forward effects apply it before the fog blend, for example `T A colour (1 + 20 x) + (1 - T) fog`. Two later Novalis captures read 0.277088 and 0.04464268 (bits `0x3D36DB3F`, the same at the G-buffer, equal-depth and blended draws of the Thumper glove glass), with the same fog colour and exponent in both. So it changes between moments of one level; what drives it is not decoded.
- **`CB0[135]`.** The rest of the frame block is identical between passes, but this word differs: `0x40` at a G-buffer draw and at the equal-depth forward draw of the same mesh, `0x2` at its blended forward draw. The research mode freezes it at 0; the Thumper glass alpha does not read it ([thumper-glove-glass](thumper-glove-glass.md)).

## How the rules use them

- **`CB0[99]`.** Static renders freeze it at 0. While a clip plays, the model view runs a wall clock in seconds for the forward kinds in `EFFECT_KINDS` (`rc2016/viewer/viewport.py`). Only the Victor ion tubes scroll with it; the prisoner numbers' scroll stays frozen.
- **`CB0[107]`.** Frozen at 0 for static renders: full streaks for cut thresholds, `w` = 0.5 for the prisoner-number vignette. While a clip plays, the ion tubes and the prisoner numbers replace it with a hashed value per 30 Hz frame (`frame_fade` in `rc2016/viewer/shading.py`), so the streaks flicker and the vignette width jitters between 0.5 and 0.6. The hash is a presentation stand-in for the flicker, not the engine's sequence. The Blender front-end and the level viewer keep the static values.
- **`CB0[115]`.** The rules use 0.5 (`2A` = 1), a labelled presentation value, and record the Veldin 0.01 as `captured_amplitude` or `amplitude_captured`. The Gaspar lava rule uses 1. The Thumper glove glass uses 0.04464268, the value of the captured frame that draws it. At 0.01, facing and bright glows and the Map-o-matic albedo are nearly off. That would be faithful to the captured Veldin frame. But no capture identifies a draw of these programs, and whether `A` differs between levels or moments is unknown. A capture of a frame with one of them on screen, read at the draw's own `CB0` offset, would settle it for that frame.
