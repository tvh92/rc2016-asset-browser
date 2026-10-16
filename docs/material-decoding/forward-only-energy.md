# Forward-only energy materials: Warmonger reticle and Sheepinator internal energy

`wpn_warmonger_reticle` (graph SHA-256 `78182cb38b81b861b1117bb9c4f36bbb0dc8f57e1d4593bae6a25e633d935ced`) and `wpn_sheepinator_internal_energy` (graph SHA-256 `697a8cc3ab623d51cf4f8c170c013f57616cfe97743c3805942d3dcdfe2fd3a7`) get no preview. Their visible look exists only in forward programs, and no deferred program, frozen input or discard model can stand in for it. `tests/test_forward_only_energy_materials.py` pins the facts below against the archive.

## What is decoded

Each graph has the usual six pixel programs. Their roles are read from the exports, not assumed:

| Programs | Shader | What it exports |
| --- | --- | --- |
| 1, 4 | `0x11408de9` (both graphs) | Three deferred targets. Albedo, emission, F0 and gloss are literal 0. Target 0 carries only the packed interpolant normal. No resources, no constant-buffer reads. |
| 2, 5 | Warmonger `0x630cb83c`, `0x6c8f5a50`; Sheepinator `0x4fbb52c8` (both) | Three deferred targets under a discard (exec-masked) predicate. Targets 1 and 2 are still literal 0; target 0 is again a packed normal (or nothing). The predicate only decides which pixels survive. |
| 0, 3 | Warmonger `0x4c184dcc`, `0x1e6c0141`; Sheepinator `0x46976f6e` | Single-target forward programs (target 0, packed half-float RGBA) around an engine light-list loop. |

So in the deferred path both materials are a black, zero-specular, zero-emission cut-out. The earlier reading that only the zero writer lacked a pass-contract join was incomplete: even the discard variants, which do sample textures (Sheepinator APIs 5 to 8) for their keep predicate, write no colour. A bounded frozen-input preview of either variant would show nothing, so none is registered and no `SOURCE_PREVIEW_POLICIES` entry exists.

The Warmonger graph's forward programs are about 2500 instructions: texture lookups, a constant-buffer-driven light walk with comparison shadow-map samples, and a final half-float export. Their texture and sampler descriptors do not resolve through the translator's pass-contract join (every sample reports no API), and the colour is combined with engine light, shadow and exposure inputs that no material supplies. Separating a "material-only" emissive term from that program would be an inference, not a decode, so it is not offered.

## Material facts (unchanged, for reference)

- Warmonger reticle: flags `0x200236` (alpha-over, two-sided), parameters (raw record bytes, not typed) `0x4B4DAB19` (0.366253, 0.138432, 0.034340), `0xECE213A3` 0.1, `0x62835AF9` 0.272, `0x28E7096A` 0.1. None is read by the deferred writers; their role in the forward programs is not decoded.
- Sheepinator internal energy: no parameters. Its discard variant samples `fx_noisetile02`, `fx_graphic_noise_01` and `cloud_noise_01` (APIs 5 to 8); how the forward program uses them is not decoded.

## Remaining limitation

Rendering either material needs a forward-pass translator: a bounded model of the light-list loop with decoded engine uploads (light records, shadow maps, exposure) and resolved forward texture descriptors. Until then the viewer and the Blender pipeline keep their generic handling and report these materials as unsupported.
