# Warbot V3 eye

The exact graph `materialgraph/character/enemy/enm_warbot_v3_eye/enm_warbot_v3_eye.materialgraph` (SHA-256 `28c3f0d182bbdb1d2c62a193339abcb1abbb7c4992c18d7c6fa19cb3b4aa91c9`) contains two equivalent contracts for the compiled deferred writer `0xbbcf827b` (code SHA-256 `439a2fb594be9f55d18fd79884552e72fa7864392b24314392d5fd8a66351383`). Both bind API5 to `textures/character/enemy/enm_warbot_v3/warbot_v3_scroll_eye_cma.texture`.

For the exact material `material/character/enemy/enm_warbot_v3_eye/enm_warbot_v3_eye.material` (SHA-256 `0102d39765c61b550f3a8b3f1ab1f6beafad8defbb15f8506c7dcd816fb4ffda`), the graph pass table maps the serialized material records to float CB2 words:

| Key | CB2 words | Serialized material value |
|---|---:|---|
| `0x7F058428` | 0–2 (12 bytes) | `(1.0, 0.5209955573, 0.0)` |
| `0x06F19114` | 3 (4 bytes) | `20.0` |
| `0x62835AF9` | 4 (4 bytes) | `0.0` |
| `0x4069EA80` | 5 (4 bytes) | `0.0` |

The mapping is source-bound by the graph table's key, dword, byte width and defaults, and by the exact material's matching key records and raw values. The values' named engine meanings and runtime upload/pass selection are unverified. CB0[99] remains a time-like input and is frozen at 0 for the bounded preview; this must not be read as the game's runtime time value.

At that preview input, the retained writer reads API5 sample 0 blue at `(u + 1/3, v)` and sample 1 red at `(u + 0.4117647111, v)`. Let `b` and `r` be those samples. Its emitted surface is:

```text
d = (1 - 0.6·b)·(1 - r)
albedo = (1 - d, 0.5209955573·(1 - d), 0)
F0 = (0, 0, 0)
gloss = 0
emission = exp2(5·0.200000003·log2(1 + 20·albedo.r)) - 1
```

The source expression for emission evaluates to approximately `20·albedo.r` for positive sampled albedo. The eye therefore has real decoded base-colour and emissive terms; this decode does not depend on guessing a colour from the texture name.

The same graph also contains `0x2fbf8531`, which has a loop the current decoder cannot prove bounded, and `0xda01d2c2`, whose exports are exec-masked. The preview uses only the exact `0xbbcf827b` source writer and does not establish which pass the game selects. Its MRT0 alpha and the masked program's discard behavior are outside this slice. The viewer and Blender use geometry normals because the selected writer has no portable tangent-normal slice. Game equivalence is false.

The exact graph/material/writer/time/parameter receipt is required for this preview; missing context, a key/value change or another shader path leaves the material unsupported by this rule. Archive-backed regression and numerical checks are in `tests/test_warbot_v3_eye_preview.py`.
