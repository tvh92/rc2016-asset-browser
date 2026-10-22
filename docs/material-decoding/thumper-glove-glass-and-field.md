# Thumper glove glass and shockwave field: no source preview

Neither material gets a `SOURCE_PREVIEW_POLICIES` rule. The glove glass has a separate forward viewer rule, described in [thumper-glove-glass.md](thumper-glove-glass.md); the shockwave field has none. Each blocker is an input or a pass that no exact-hash freeze can supply without inventing a value. Archive-backed guards are in `tests/test_thumper_not_bindable.py`.

## Glove glass (`wpn_thumper_glove_glass`)

Graph SHA-256 `ffec1bf5…ad98`, material SHA-256 `4e6f27a3…4f70`, flags word `0x00200234` (opaque, backface shown; bits `0x200000` and `0x30` have no decode).

| Program | Shader id | Shape | Status |
| --- | --- | --- | --- |
| 0, 3 | `0x5226f404`, `0x1d429528` | Target 0 only, loop over the engine light list | Holds the visible glass colour. The translator refuses the loop; the material-only terms are read with the loop-opaque research mode (see the glass page). |
| 1, 4 | `0x11408de9` | Three targets, all constant zero | Compiles; the shared placeholder writer (933 graphs), not glass content. |
| 2, 5 | `0xdd9a126f`, `0x34256787` | Three targets, exec-masked | Untranslated. |

- The decoded outputs of the exec-masked writers are `target0 = 0.5 * normalize(attribute1.xyz) + 0.5`, albedo 0, F0 0, gloss 0. A preview built from them would be black and must not be read as the glass look.
- Their keep predicate reads `CB0[92..94, 96..102, 104..106]`, engine texture API 33, `POS_X`, `POS_Y`, `POS_W`, `FRONT_FACE`, `attribute0.0..2`, `attribute1.0..2` and three unresolved descriptor values. The `CB0[92..106]` block has no known meaning, and a review value for it would shape the discard mask directly with no evidence for any number.
- None of the four code hashes equals `basic_glass`, `character_glass_02` or `jn_glass`, so the decoded rules cannot be reused by hash. `5226f404` does share the light-loop body of `basic_glass` `4FC5703F`; that and the material-only terms are established on the glass page.

## Shockwave field (`proj_thumper_field`)

Graph SHA-256 `61e30442…7dc2`, material SHA-256 `9057cb3f…7123`, flags `0x3A` (alpha-over, backface culled), no parameter or texture section, so the graph defaults apply (`CB2[0]` = 1.0, `fiber_noise_01` for API 5).

| Program | Shader id | Shape |
| --- | --- | --- |
| 0, 3 | `0x9ca12194` | Forward, target 0 only (no targets 1 and 2); colour reads `CB0[112..115]` and four unresolved terms; alpha reads `attribute0.3` |
| 1, 4 | `0x11408de9` | Zero placeholder |
| 2, 5 | `0x27dda5f7` | Three targets, exec-masked on `attribute0.2`; albedo, F0 and gloss are constant zero |

- The keep predicate of both writers is `X >= attribute0.2`, with `X` built from `CB2[0]`, UV0 and the API 5 sample. It reads no engine word, but `attribute0.2` has no provable value: the vertex programs that export it branch on per-draw runtime flags (see Known gaps in `docs/shading.md`).
- The visible colour is in `0x9ca12194`, which is not a deferred writer, so there is no pass contract to bind. Its RGB is `a2.3 * (unresolved * CB0[115] * ...) + CB0[11x] * (1 - a2.3)`. That is the fog blend of the other forward effects: `CB0[112..114]` is the frame fog colour and `CB0[115]` the frame amplitude ([frame constants](frame-constants.md)), so `attribute2.3` takes the role of the fog transmittance. This program's vertex source of `attribute2.3`, and `attribute0.3`, are not traced.
- Alpha is `sat(a0.3 * sat(max(0, sat(0.75 X + Y Z))))` with the nodes behind `X`, `Y` and `Z` not yet decoded. This is the only part that could later become a labelled preview, once those nodes and a source for `attribute0.3` are traced.

## Not decoded

The loop-program colour of the glove, the `CB0[92..106]` and `CB0[112..115]` contents, the `attribute0.2`, `attribute0.3` and `attribute2.3` sources, and the API-number to graph-slot mapping for the glove's unsupported writers. These stay visible limitations; nothing here is claimed as game data.
