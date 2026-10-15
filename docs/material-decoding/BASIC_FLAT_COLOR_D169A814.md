# `basic_flat_color` writer 0xD169A814

This record is bound to decoded graph SHA-256 `6d9bcad5a9dab38dd5988195cc9e87514c08a90c6b8fa899c2d21f3e42c14f95` at `materialgraph/global/basic_flat_color/basic_flat_color.materialgraph`. The viewer rule requires both the normalized path and hash. Other graphs or changed content remain on the prior generic reading.

## Evidence and material terms

The retained shadPS4 module is `%APPDATA%/shadPS4/cache/CUSA01073/0x00000000d169a814_0.spv`; it carries program id `0xd169a814` and raw module SHA-256 `2d3feebb7d4547ab635dd34f7e5512f1e6365046b9956777a6c01f78061a200e`. The source catalog joins this id to native program code SHA-256 `29685aad5fa90c6106091ff134a3a866015da9bec1093f500951e12cf4b0d90c`. This id join is source-catalog evidence; it is not an independent byte correspondence between SPIR-V and native code. Existing `Indoor_2026-09-25` RenderDoc event 5584 also replays this module, but it is not a Telepathipus material draw and does not establish the runtime pass selection for these materials.

The source material layout has the same keys in pass 0 and pass 1:

| Key | Source read | Bounded meaning |
|---|---|---|
| `0x7F058428` | CB2 dwords 0–2 | RGB for an independent additive output term |
| `0x77591534` | CB2 dword 3 | Multiplier of that RGB term |
| `0x62835AF9` | CB2 dword 4 | Grey specular/F0 scale for the runtime lighting expression |
| `0x24F4CA4C` | CB2 dword 5 | Gloss scale; shader uses `sqrt(saturate(value))` |

In the decompiled fragment source, the RGB key is only used in the final additive source term and diagnostic output branch; it does not feed the accumulated diffuse/specular light. The geometry normal feeds the lighting expression, and the graph's default normal texture is not sampled by this writer. The viewer therefore uses zero base albedo, grey F0 and saturated-square-root gloss, then adds the exact material term `colour * scale` beside its deferred BRDF preview.

Material overrides are read with the exact scalar/vec3 byte widths and rejected from this rule if malformed or nonfinite; they do not silently inherit graph defaults. Signed finite values are preserved in the viewer shader and Blender source-math nodes without clamping. Final handling of negative radiance or specular parameters by the framebuffer and Cycles BRDF is not validated, so the Blender render is marked unsupported for that domain. If a signed emission reaches glTF export, the exporter leaves that term out and records that glTF's nonnegative emissive factor cannot represent it.

## Scope limits

The dynamic forward light/probe/reflection loop, fog, debug branches and runtime pass selection remain unresolved. The viewer substitutes its own deferred lighting model for those inputs. The result is a source-bounded material preview, not a reconstruction of the selected in-game draw or a game-equivalence claim. The graph's other programs remain unresolved: native writer `0x11408de9` leaves albedo/specular targets zero while retaining the normal target, `0x6c8f5a50` is execution-masked, and other forward variants have a dynamic light loop.
