# Frame constants in CB0

Many graph programs read a few engine words from `CB0`, which is the frame constant block, not a per-material or per-draw buffer:

| Dword | Role in the programs | Captured behaviour |
| --- | --- | --- |
| `CB0[99]` | Time: UV scrolls, blink phases, wobble | Game time in seconds; it advances by the frame time, about 1/30 s per frame |
| `CB0[107]` | Dissolve or cut threshold (Victor ion tubes, thrusters, scorch marks, lasers, shields), vignette width (prisoner numbers) | A new value in [0, 1) every frame |
| `CB0[112..114]` | Fog colour, blended as `T colour + (1 - T) fog` with a vertex fog transmittance `T` | (1.665, 3.720, 5.201) in the captured frame |
| `CB0[115]` | Glow, emission or albedo amplitude `A` (facing glow, bright glow, prisoner numbers, Victor reticle and ion tubes, Map-o-matic, thumper glove glass, lava) | 0.01 in every captured frame |

None of these words is stored in the material, graph, atmosphere, level, zone, config, visual-effect or actor data.

## Evidence

The only capture is one Veldin frame (shadPS4 RenderDoc). The buffer the shaders read `CB0` from is shadPS4's 64 MB constant ring. It holds the current frame and 20 earlier ones, and each frame's block is uploaded about 1150 times (once per draw and stage). A shader reads dword `N` at the draw's descriptor offset, plus the low byte of the push constant `buf_offsets0.x`, plus `4N` bytes. The dump starts at byte 0, which holds the oldest frame in the ring, so dword numbers read from the start of the dump belong to a different frame. The lighting event binds the current frame's block at byte 54186240.

- **Frame-level.** A replay that reads each draw's own bound `CB0` finds the same dwords 99, 107 and 115 at every draw that binds the frame block. That covers the lighting pass, the G-buffer draws and the forward effect draws that read dwords 107 and 115. So no per-effect, per-actor or per-material value reaches a program through these words. A rule that needs a different value per object cannot get it from `CB0`.
- **The captured frame:** `CB0[99]` = 259.7947, `CB0[107]` = 0.3936, `CB0[115]` = 0.01.
- **`CB0[99]`.** Across the 21 frames in the ring it steps by 0.0333 to 0.0354 s, the frame time.
- **`CB0[107]`.** The 21 values run from 0.018 to 0.965 (mean 0.56), and neighbouring frames are uncorrelated. The spread is consistent with a uniform draw in [0, 1): Kolmogorov-Smirnov D = 0.22 against a 5% critical value of 0.30. The engine's generator and exact distribution are inferred from these samples, not decoded. Any effect that reads it changes every frame: a cut threshold flickers, and the prisoner-number vignette width `0.5 + 0.1 CB0[107]` jitters between 0.5 and 0.6.
- **`CB0[115]`.** It is 0.01 in every frame (bits `0x3C23D70A`), at every draw. The Veldin forward effects apply it before the fog blend, for example `T A colour (1 + 20 x) + (1 - T) fog`. Whether it is a level setting or an engine constant is not established. Two `atm_veldin` header floats equal 0.01 (offsets 37 and 72), but 0.01 is common there, and one capture cannot single out a field.

## How the rules use them

- **`CB0[99]`.** Static renders freeze it at 0. The model view runs it as a wall clock in seconds only for the time-driven forward kinds (`EFFECT_KINDS` in `rc2016/viewer/viewport.py`; the Victor ion tubes).
- **`CB0[107]`.** Frozen at 0 for static renders: full streaks for cut thresholds, `w` = 0.5 for the prisoner-number vignette. The ion tubes replace it while a clip plays with a hashed value per 30 Hz frame (`ion_tubes_fade`), a presentation stand-in for the flicker.
- **`CB0[115]`.** The rules use 0.5 (`2A` = 1), a labelled presentation value, and record the Veldin 0.01 as `captured_amplitude` or `amplitude_captured`. The Gaspar lava rule uses 1. At 0.01, facing and bright glows and the Map-o-matic albedo are nearly off. That would be faithful to the captured Veldin frame. But no capture identifies a draw of these programs, and whether `A` differs between levels or moments is unknown. A capture of a frame with one of them on screen, read at the draw's own `CB0` offset, would settle it for that frame.
