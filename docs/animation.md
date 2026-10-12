# Animation

How packed skeletal animation is stored in Ratchet & Clank (PS4, 2016), how this repository decodes it, and how decoded clips become Blender Actions. Supported scope is the inspected PS4 version-15 layout only: not other codecs or versions, and not shader or VFX animation.

Related documents: [pipeline](pipeline.md) (how the build uses the baker, clip selection, NLA and controls), [viewer](viewer.md) (animation preview in the browser).

## Modules

| Module | Role |
| --- | --- |
| `rc2016/formats/container.py` | DAT1 (`1TAD`) container and section inspection; clip inventory. CLI: `python rc2016/formats/container.py file.animset [--json report.json] [--bones names.txt]`. |
| `rc2016/formats/animation_samples.py` | Lower-level integer sampler: lossless frame-major unsigned deltas plus the packed base records and runtime component offsets. No rotation or translation conversion. |
| `rc2016/formats/animation_transforms.py` | Model-independent S/Q/T extractor: reads an animset and its original model, resolves joint hashes against the model skeleton, and exports raw clips plus source-scale, source-time JSON. No bone names or authored curves are assumed. Its CLI is the loose-file backend of the GUI. |
| `rc2016/formats/clips.py` | All embedded clips of an animset in directory order: exact samples, classification, inheritance rule, unsupported rows. |
| `rc2016/pipeline/animation_bake.py` | Affine bone baking and numerical verification (`make_bake_rig`, `bake_clip`, `world_samples`, `matrix_error`); no character names. The pipeline's buffered production baker is `rc2016/pipeline/blender_support.py` (`bake`), which uses the same SVD scheme. |
| `rc2016/pipeline/controls.py` | Embedded 3D View **Animations** panel; no external dependencies. (`legacy_controls.py` is an older panel kept so previously delivered files still validate.) |

`rc2016/formats/animation_samples.py` and `rc2016/formats/animation_transforms.py` need only the Python standard library; `sample_transforms` uses a batched numpy path when numpy is importable (identical values) and otherwise, or for data that fails a validity check, the exact per-frame loop that raises the reference errors. `clips.worlds_batch` is the all-frames form of `skeleton.worlds` (used by `extract_set` and the portable export, which also batches the local-TRS decomposition and memoises per-clip tracks). Exports refuse existing output directories; choose a new one.

```text
python rc2016/formats/animation_transforms.py <actor.animset> --model <actor.model> [--prefix <clip prefix>] --out <new directory>
python rc2016/formats/animation_samples.py <file.animset> [--bones names.txt] --out <new directory>
python rc2016/formats/animation_samples.py --validate-tree <folder> [--report validation.json]
```

`animation_transforms.py` writes, per clip, `NNN_<name>.json` (times, joint names and hashes, frames as scale / rotation xyzw or null / translation per joint) and the verbatim `NNN_<name>.animclip`, plus `manifest.json` with source hashes (schema `ps4-v15-source-sqt-v1`). `--prefix` is repeatable; omit it for the whole set. `animation_samples.py` writes `integer_deltas.json` and raw clips. Its bundle export needs a root lookup pointing directly at embedded clips (verified on Sheepinator); complex character animset lookups are deliberately rejected. `--validate-tree` checks embedded clips independently and does not imply support for every lookup format.

## Container

- DAT1 header: u16 section count followed by u16 unknown flags (not a u32 count). Container boundaries are enforced and truncation is rejected.
- Clip-related section types (`container.SECTION_TYPES`): `anim_clip_base_state` `0x2BB5BC8F`, `anim_clip_path` `0x3976E44C`, `anim_clip_sample_data` `0x3A7B4855`, `anim_clip_data` `0x9FD19C20`, `anim_clip_built` `0x9DF23F77`, `anim_clip_joint_hashes` `0xA3B26640`, `anim_clip_lookup` `0xB79CF1D7`, `anim_clip_sample_elements` `0xD070D358`. Root names follow the public ig-tools schema; the nested animation section names are corroborated by independent public format research.
- An animset root has `anim_clip_lookup` (16-byte records: u32 name hash, u32 offset into `anim_clip_data`) and `anim_clip_data` holding the embedded clip containers. Lookup entries must stay inside the data section, must not overlap, and must match the clip's own name hash. Nested wrapper layouts without a directory keep physical order. Embedded clips outside the directory are an error.

## Sample layout

- Clip Built `+0x20`: u16 joint count, u16 stored sample count. `+0x24`: u16 sample-element count, u16 bytes per sample. `+0x28` of the metadata holds the base-tail block count.
- Sample Elements: `N` little-endian u16 destination offsets, then `N` bytes holding bit width minus one. Values are unsigned, packed in directory order, LSB first, per frame.
- Stride = `4 * ceil(sum(bit_widths) / 32)`; leftover bits are zero. Destination offsets are 4-byte aligned into 48-byte joint records: `joint_index = offset // 48`, `component_byte_offset = offset % 48`. Joint order comes from the clip's joint hashes, not from any export order.
- Sample Data = stored sample count x stride. Zero-element, zero-stride clips may omit sections.
- Preserve the stored sample count: the corpus has both `duration * fps` and `duration * fps + 1`. `anim_clip_built +0x08` bit `0x20` tracks the 13 duration-formula exclusive clips in a 192-clip audit, but five one-sample Ratchet hydropack poses carry `0x41` without it; assign no universal timing meaning. Timestamps are `sample_index / fps`.
- Do not assign component semantics from destination offsets alone. The format is frame-major packed samples, not a Havok or spline layout.

## Decoder rules

- Runtime S/Q/T component slots are at byte offsets 0/4/8 (scale), 16/20/24/28 (quaternion) and 32/36/40 (translation). The sampler rejects any other offset.
- Add the unsigned samples to the signed packed base quaternion, then normalize the four-vector (homogeneous coordinates, not already unit length). Rotor curves recover 18, 36 and 72 degree steps within quantization.
- Packed translation integers and deltas use `2 ** -base_control_byte_15`, the second control byte of the base record. The first control byte is the scale exponent, and unlisted scales are one. Exponents above 30 are unsupported.
- Base tail: pairs of scale records, six u16 in `sx0, sx1, sy0, sy1, sz0, sz1` order followed by two u16 destinations. Four records make a 32-byte block; exact duplicate final records pad. The block count is metadata `+0x28`. Conflicting duplicate records and destinations that are not a multiple of 48 or past the joint count are errors. High-bit (signed) scale values are unsupported.
- Quaternion `(-32768, -32768, -32768, -32768)` is surfaced as an unresolved collapsed-rotation sentinel (`rotation_xyzw: null`). It is valid only on a joint with at least one zero scale, and is never normalized as a pose.
- Joint record `+6` bit 0 selects segment-scale compensation: world = parent world `*` `T * inverse(parent local S) * R * S`, with the local translation column unchanged. This is the adopted interpretation, not verified against the running game; other flag bits stay raw. The pipeline treats it as one of two candidate rules (`full`, `segment_scale_compensation_bit0`) and records which fits the model's inverse binds; see [pipeline](pipeline.md#inheritance-rule).
- Inverse-bind matrices begin at the next 64-byte boundary after the SQT array (not at a fixed 32-byte gap). Ratchet's bind scales are unit; other rigs have non-unit scales and need their own validation.

## Coverage

1. Integer sampler: 11 animsets, 680 embedded clips (658 sampled, 22 constant), 22,027 frames. All payloads re-encode exactly with zero padding.
2. Source S/Q/T parser: all 520 Ratchet Protosuit clips pass; this is numeric and structural coverage, not appearance.
3. Blender comparison on a rig with non-unit bind scales: 12 clips / 243 frames reopened and compared with decoded affine matrices, maximum error about `2.84e-5`.
4. Blender comparison on a 193-bone humanoid rig: a 20-frame run clip at 30 FPS; 182 clip hashes match the model, 16 are unmatched in the source export, 11 bones are unsampled and use model rest; matrices agree within `1.53e-5`.
5. Fidelity audit across nine source pairs: 192 clips and 6,924 samples decode. Ratchet's 520 clips classify as 415 partial absolute and 105 relative/additive-style (`anim_clip_built` bit `0x40` set on exactly those 105). Path sections are not assigned (the root matches both endpoints in 10 of 192 clips).

These figures were established in the research notes (not included). Regression tests cover known curves, joint order, bit-string re-encoding, extracted-clip decoding, overwrite refusal, header flags, bad destinations, strides and padding, and truncated or escaping sections.

## Not decoded

Root-motion paths, runtime subframe interpolation, further joint-flag semantics, negative or high-bit scales, other codecs, additive animation graphs, and shader or VFX animation are separate problems and are reported as unsupported rather than approximated. Numeric parser coverage does not prove clips look right in game.

## From clips to Blender Actions

### Classification (`clips.py`)

`classify` compares each clip's first-frame joint translations with the model's rest translations (median ratio of translation length to rest length over joints with a nonzero rest length):

| Result | Condition |
| --- | --- |
| `unrelated` | no joint hash of the clip matches the model |
| `relative/additive-style layer` | median ratio `<= 0.05` |
| `absolute skeletal` / `partial absolute skeletal` | median ratio `>= 0.9`; partial when the clip does not cover every model joint |
| `unsupported` | intermediate or insufficient evidence; a hash-bound `classification_overrides` entry in a profile is required |

This is a heuristic, not a recovered runtime layer flag. A decode that fails with an "unsupported" or "not validated" error marks the clip `unsupported`; any other failure marks it `malformed`. Clip entries keep raw and sample SHA-256, frame count, fps, unsampled model hashes, unmatched clip hashes and collapsed-rotation joints.

Relative clips are baked as standalone previews: rest translation plus delta translation, rest rotation times delta rotation (a collapsed delta uses identity), rest scale times delta scale. The blend partner and weight are unknown. Absolute and partial clips use source samples and source rest for unsampled joints.

### Bake procedure

1. **Discover.** Read actor and model references and the original animsets. Resolve clip joint hashes against the exact model skeleton (`model_skeleton()`, `embedded_clips()`). Shared animsets may hold unrelated attachments or variants; names do not prove applicability. Record every candidate as included, unrelated or unsupported with a reason.
2. **Validate the binding before baking.** Compare names, parents, rest S/Q/T, weights and inverse binds. The adapter uses 50 Blender armature units per source translation unit (`local_matrix`); validate this per rig. Preserve the original rig and meshes, and record unmatched clip hashes and unsampled bones.
3. **Decode.** `sample_transforms(clip)` yields per-frame scale, rotation and translation per joint. Retain raw clips, hashes, exact frame counts and rates. Unsupported channels or scales stay explicit failures. A null collapsed rotation is unresolved; a source-rest fallback is justified only where zero scale and model visibility support it.
4. **Bake on a separate animation rig.** `make_bake_rig()` copies the armature and gives each bone an unparented, unconnected helper parent (`__sample__<bone>`). `bake_clip()` computes target world matrices with `world_samples()` (applying the chosen inheritance rule, bind corrections and a fixed presentation anchor), takes a 3x3 SVD of each target, puts the left factor (rotation `U`, singular values as scale) on the helper, and the right rotation factor `Vt` (relative to the bone rest) on the bone. This helper/deform hierarchy preserves shear and collapsed scales, which ordinary connected bones cannot. Keys sit on exact source sample frames with constant interpolation, Actions have fake users, quaternions are sign-continuous, and `bake_clip` asserts a maximum matrix error below `1e-3` (the pipeline's validator uses `1e-4`). Do not alter poses to ease framing; fit placement to the camera instead.
5. **Pipeline baker.** `blender_support.bake` is the production form: buffered keys, and with a channel library the right factor carries only an algebraic unit scale (after a rigid-rest guard on the bone basis) and an identity rotation where the singular values agree within `2^-19`. Native scale, reflection, collapse and shear stay in the left factor.
6. **Channel library (`channel_library.py`).** Canonical float32 defaults and the library's moving-channel mask are stored on the rig. A channel is omitted only if it never deviates from rest across the whole library (exact float32 equality, no tolerance). Every channel that moves has a curve in every Action, including rest and sparse earlier clips; late activation backfills constant curves into earlier Actions. Never omit a curve merely because one clip is constant: that would retain the previous clip's pose. Globally invariant channels use persistent defaults restored by the embedded controls. Validation checks the shared reset domain as well as every Action and NLA sample.
7. **Overview and controls.** Every included Action is placed sequentially on one NLA track on a common clock (`timeline.timeline`: half-open intervals; the common rate is the LCM of the source rates and must lie in 1 to 32767, otherwise the build fails instead of resampling; the total must fit Blender's frame range). `nla_sample_bounds` maps an N-sample Action to end `max(N, 1.5)` so the first frame of the next strip does not evaluate the wrong endpoint; the half-frame guard keeps a one-sample Action evaluable without inventing a second sample. A scene marker is created at each clip boundary. `Animation Catalog.json` (the clip list, timeline, overview FPS, rest Action name) and `Animation Controls.py` are embedded as text blocks, together with the source catalog, model inventory, unsupported-clip list and a START HERE note. The saved file is left with the overview active.
8. **Verify after reopening.** Compare every baked frame matrix with the decoded target, check fake-user Actions, slots, catalog, clip selection and variant visibility, and numerically validate every Action. Per new rig or motion family, render low-resolution representative phases to check deformation and bounds. Preserve geometry, weights, UVs, materials and cameras, pack dependencies and restore render defaults.

Keep corrections, variant rules and source locations in the model's build step, not in the decoder. A variant-specific selector can replace the generic controls by embedding a text defining `play_clip(name)`; it must also set timing and slot, and pack visibility must never be inferred from bone names.

### User playback

The default Action plays with Space and renders with F12. For clip selection, run the embedded **Animation Controls.py** in the Text Editor once per session, then open **N > Animations** in the 3D View. The panel offers:

- **Load selected clip**: mutes the overview, assigns the Action and its slot, and sets the frame range and FPS from the catalog.
- **Load all clips in order**: unmutes the overview track and sets the overview FPS and full range.
- **Load 24 clips from selected in order (fast)**: builds a temporary NLA track (`Clip range | temporary overview`) holding 24 overview strips; every other control removes it. Blender evaluates the channel domain of every unmuted strip each frame, so the full overview of a 520-clip library costs about 0.12 s per frame.
- **Jump to selected boundary**, **Restore original rest pose**, play/pause, frame, and render current frame.

The panel and catalog travel inside the blend: no add-on, script path or preference change is needed. Plain Action Editor selection skips a model's variant callback and clip timing, so use the panel.

### Python usage

```python
from rc2016.pipeline.animation_bake import make_bake_rig, bake_clip

rig = make_bake_rig(source_rig, rows, 'Actor | animation rig')
actions = []
for clip in applicable_clips:
    # matrices come from the model-specific validated bind and placement step
    action, error = bake_clip(rig, rows, clip, corrections, anchors[clip['name']])
    actions.append(action)
```

For the full pipeline path (clip selection, inheritance profiles, caching, NLA and controls) see [pipeline](pipeline.md#animation-in-the-pipeline).

## External sources (format evidence only; nothing copied or executed)

- [ig-tools](https://github.com/doesthisusername/ig-tools) at commit `b4ebe28a926cf298bff70ad01cdf9e14533c5b94`: DAT1 schema, asset decompression and section splitting; no skeletal sample decoder.
- [ALERT animation notes](https://github.com/Tkachov/ALERT/wiki/.animclip): section identities and base-record observations. Its later-game sample directory differs from the PS4 layout and must not be imported unchanged; [issue 12](https://github.com/Tkachov/ALERT/issues/12) reports incorrect poses.
