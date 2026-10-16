# Plasma Striker body glow: a resource-free deferred writer

`wpn_plasma_striker_fx` (material SHA-256 `4bbf9c43...f8ee`, graph SHA-256 `2dcefd19...5838`) has the deferred writer `0x368cca2e` in both pass contracts (0 and 1). It is exact and portable with no texture and no engine input:

- albedo = (0, 0.423095703125, 1.0), F0 = 0, gloss = 0, emission = 19.9966876. The material has no parameter sections and the writer reads no constant buffer.
- The writer has no texture node and no usage slot at API 5 or above. The native B6 record of the writer (record 4 in pass 0, 17 in pass 1) has resource bitmap 0 while its pass lists one resource (`wpn_plasma_striker_cma`). The program and the native envelope therefore agree that nothing is sampled. The constant is the game's output, not a lost texture read.
- The geometry normal is used because no tangent-normal slice exists.

## Why the join dropped it

`program_contracts` required a sampler at API 5 or above so that the resource count and bitmap could be checked against the pass. A program that samples nothing has nothing to check, so it was dropped. This is a conservative guard, not a texture gap.

## Why the rule is not general

Admitting every texture-free deferred writer whose native resource bitmap is 0 was measured across all archive graphs. It newly binds, for dozens of graphs, the zero-output placeholder writer `0x11408de9` (the visible colour of those materials lives in forward and discard programs), plus `0xd88e357b` and `0xec0d6a2f`. Those would change from the existing preview fallback to a zero-output surface, so the general rule was rejected. `RESOURCE_FREE_WRITERS` in `rc2016/shaders/bindings.py` names the one audited writer by graph SHA-256, shader id and code SHA-256 (`82d8502f...032d`); any other graph or code gets nothing. Archive-backed check: `tests/test_resource_free_writer_join.py`.

## Not modelled

The graph also holds the discard program `0x235ad891` (alpha test reading `CB0[99]`, a `TBUFFER` load, an interpolant and texture API 33) and the forward program `0x43339821`. Which program the game draws is not established, so the constant glow is shown on the whole surface and any dissolve or cut-out is missing. The receipt keeps `game_equivalence = False`.
