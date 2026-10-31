# Animation

How packed skeletal animation is stored in Ratchet & Clank (PS4, 2016), how this repository decodes it, and how decoded clips become Blender Actions. Supported scope is the inspected PS4 clip layout, built-header versions 15 (animsets), 21 (real-time cutscene clips) and 143 (Mrs. Zurkon's boss clips), all with the same sample layout: not other codecs or versions, and not shader or VFX animation.

Related documents: [pipeline](pipeline.md) (how the build uses the baker, clip selection, NLA and controls), [viewer](viewer.md) (animation preview in the browser).

## Modules

| Module | Role |
| --- | --- |
| `rc2016/formats/container.py` | DAT1 (`1TAD`) container and section inspection; clip inventory. CLI: `python rc2016/formats/container.py file.animset [--json report.json] [--bones names.txt]`. |
| `rc2016/formats/animation_samples.py` | Lower-level integer sampler: lossless frame-major unsigned deltas plus the packed base records and runtime component offsets. No rotation or translation conversion. |
| `rc2016/formats/animation_transforms.py` | Model-independent S/Q/T extractor: reads an animset and its original model, resolves joint hashes against the model skeleton, and exports raw clips plus source-scale, source-time JSON. No bone names or authored curves are assumed. Its CLI is the loose-file backend of the GUI. |
| `rc2016/formats/clips.py` | All embedded clips of an animset in directory order: exact samples, classification, inheritance rule, unsupported rows. `extract_set(..., clip_names=)` extracts only the named containers of a file. |
| `rc2016/formats/animation_sources.py` | Clip sources beyond a model's referenced animsets: cutscene (`cinematic2`) clip directories named by CRC32, `cinematic_sources`, the matching-rig comparison and `borrowed_animset`. |
| `rc2016/export/cutscene_animations.py` | Cutscene animation library: manifest plus GLB per model (see [Cutscene animation library](#cutscene-animation-library)). |
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
- A real-time cutscene (`cinematic2`) root has the same lookup and data sections. All 162 EUR cutscenes parse with the animset directory reader. Their clips carry no `animclip built` name string; a clip's name hash is the game CRC32 of a string stored in the same file (for example `nov_mayor_rt_shot5_npc_Mayor_master`), and only such an exact match names it. Each shot holds one clip per performer (characters, vehicles, props, cameras), and the cutscene's dependency list names one actor per performer (`pok_ocean_rt` names the Hydro Harvester actor three times, once per copy).
- A cutscene clip is offered to a model when its joint hashes cover every model joint and it is that model's performance (`animation_sources.performs`). Coverage alone matches far too much: most vehicles, props and debris have the single joint `a_body`, which every one-joint clip of every cutscene covers (2,671 clips for the Hydro Harvester, 422,525 model-clip pairs over the 409 covered models against 16,823 under the performer rule). The performer is the end of the clip name, optionally followed by `_master<N>` or a copy number (`pok_ocean_rt_shot01_enm_Hydro_Harvester_Tank_master6`, `vel_qwark_rt_shot2_npc_Solana_Trooper2`). A clip is the model's when its performer is the model's file stem or one of its folder names holding an underscore (parts under `hero_ratchet/bangle_set/` play Ratchet's shots), or when the cutscene's dependency list names an actor (or the model itself) that references the model and the clip is that reference's: the one its performer names, otherwise the one whose model rig is most like the clip's joint set (shared over combined joint hashes; a tie gives none). The second rule covers performers named for the character rather than the model (`vel_qwark_rt_shot20_Shiv` for `npc_shiv.model`, `pok_ocean_rt_shot02_npc_Grimroth_master` for Felton Razz). A performer with no actor in its cutscene's dependency list can still leave its one-joint clips to a one-joint model of that cutscene: `rv_neb_warship_ext` gets 8 Ratchet and Clank clips of `neb_warsh_rt`.

## Sample layout

- Clip Built `+0x20`: u16 joint count, u16 stored sample count. `+0x24`: u16 sample-element count, u16 bytes per sample. `+0x28` of the metadata holds the base-tail block count.
- Sample Elements: `N` little-endian u16 destination offsets, then `N` bytes holding bit width minus one. Values are unsigned, packed in directory order, LSB first, per frame.
- Stride = `4 * ceil(sum(bit_widths) / 32)`; leftover bits are zero. Destination offsets are 4-byte aligned into 48-byte joint records: `joint_index = offset // 48`, `component_byte_offset = offset % 48`. Joint order comes from the clip's joint hashes, not from any export order.
- Sample Data = stored sample count x stride. Zero-element, zero-stride clips may omit sections.
- Preserve the stored sample count: the corpus has both `duration * fps` and `duration * fps + 1`. `anim_clip_built +0x08` bit `0x20` tracks the 13 duration-formula exclusive clips in a 192-clip audit, but five one-sample Ratchet hydropack poses carry `0x41` without it; assign no universal timing meaning. Timestamps are `sample_index / fps`.
- Do not assign component semantics from destination offsets alone. The format is frame-major packed samples, not a Havok or spline layout.

## Decoder rules

- The built header's u32 at `+0` is 15 for animset clips and 21 for 2,665 of the cutscene clips. Version 21 uses the version-15 sample layout: `pickup_predator_launcher_hero_ratchet` in `hero_ratchet.animset` (15) and its twin in `predator_pickup_cine.cinematic2` (21) have byte-identical base state, joint hashes, sample elements and sample data and decode to identical frames; their headers differ only in that word, the name hash and `+0x08` bit `0x80000`. Every structural check applies to both. Some version-21 clips with `+0x08` bit `0x20000` store fewer samples than frames in an unknown arrangement; they fail the payload-size check and are reported as malformed. Version 143 (the Engaged_* and React_Hit clips of `enm_mrs_zurkon.animset`) has no version-15 twin to compare bytes with, but every structural check passes with the same layout and the decoded motion is consistent: Engaged_Idle's first and last frame are identical and frame 0 of each clip equals the rest pose for joints it does not move. Other values (cutscene tracks such as 26, 140, 198, and animset clips outside the Zurkon set) stay unsupported.
- A clip whose own name string does not hash to its name hash (the version-143 clips read `igAnimWrinkleBG`) takes the string of its animset whose game CRC32 is the name hash, the same rule that names cutscene clips; the clip is left with its own name only when no such string exists.
- Runtime S/Q/T component slots are at byte offsets 0/4/8 (scale), 16/20/24/28 (quaternion) and 32/36/40 (translation). The sampler rejects any other offset.
- Add the unsigned samples to the signed packed base quaternion, then normalize the four-vector (homogeneous coordinates, not already unit length). Rotor curves recover 18, 36 and 72 degree steps within quantization.
- Packed translation integers and deltas use `2 ** -base_control_byte_15`, the second control byte of the base record. The first control byte is the scale exponent, and unlisted scales are one. Exponents above 30 are unsupported.
- Base tail: pairs of scale records, six u16 in `sx0, sx1, sy0, sy1, sz0, sz1` order followed by two u16 destinations. Four records make a 32-byte block; exact duplicate final records pad. The block count is metadata `+0x28`. Conflicting duplicate records and destinations that are not a multiple of 48 or past the joint count are errors. High-bit (signed) scale values are unsupported.
- Quaternion `(-32768, -32768, -32768, -32768)` is surfaced as an unresolved collapsed-rotation sentinel (`rotation_xyzw: null`). It is valid only on a joint with at least one zero scale, and is never normalized as a pose.
- Joint record `+6` bit 0 selects segment-scale compensation: world = parent world `*` `T * inverse(parent local S) * R * S`, with the local translation column unchanged. This is the adopted interpretation, not verified against the running game; other flag bits stay raw. The pipeline treats it as one of two candidate rules (`full`, `segment_scale_compensation_bit0`) and records which fits the model's inverse binds; see [pipeline](pipeline.md#inheritance-rule).
- The inverse binds of `npc_sheep` and `amb_glow_slug` fit a different operand order of that compensation (local basis `R * diag(S / parent S)`, the parent scale divided out after the rotation) that default SSC0 only approximates, but animated poses under it shear the bit-0 bases where SSC0 keeps them orthogonal, so SSC0 stays the default for clips; the order is the profile-only rule `segment_scale_compensation_bit0_after_rotation`, which `npc_sheep` uses for its rest pose only (profile key `rest_inheritance`; its clips keep SSC0; numbers and scope in [pipeline](pipeline.md#inheritance-rule)). The viewer's `AnimationLibrary` applies the model's bundled profile like the pipeline: clips use `inheritance`, the glTF rest joint nodes use `rest_inheritance`, and skin = clip world `*` stored inverse bind throughout.
- Inverse-bind matrices begin at the next 64-byte boundary after the SQT array (not at a fixed 32-byte gap). Ratchet's bind scales are unit; other rigs have non-unit scales and need their own validation.

## Coverage

1. Integer sampler: 11 animsets, 680 embedded clips (658 sampled, 22 constant), 22,027 frames. All payloads re-encode exactly with zero padding.
2. Source S/Q/T parser: all 520 Ratchet Protosuit clips pass; this is numeric and structural coverage, not appearance.
3. Blender comparison on a rig with non-unit bind scales: 12 clips / 243 frames reopened and compared with decoded affine matrices, maximum error about `2.84e-5`.
4. Blender comparison on a 193-bone humanoid rig: a 20-frame run clip at 30 FPS; 182 clip hashes match the model, 16 are unmatched in the source export, 11 bones are unsampled and use model rest; matrices agree within `1.53e-5`.
5. Fidelity audit across nine source pairs: 192 clips and 6,924 samples decode. Ratchet's 520 clips classify as 415 partial absolute and 105 relative/additive-style (`anim_clip_built` bit `0x40` set on exactly those 105). Path sections are not assigned (the root matches both endpoints in 10 of 192 clips).

These figures were established in the research notes (not included). Regression tests cover known curves, joint order, bit-string re-encoding, extracted-clip decoding, overwrite refusal, header flags, bad destinations, strides and padding, and truncated or escaping sections.

## Not decoded

Root-motion paths, runtime subframe interpolation, further joint-flag semantics, negative or high-bit scales, other codecs, additive animation graphs, how the game plays an animset on a model of other proportions (the viewer, portable export and pipeline use a labelled translation rule, see [viewer](viewer.md#animation-playback)), and shader or VFX animation are separate problems and are reported as unsupported rather than approximated. Numeric parser coverage does not prove clips look right in game.

## From clips to Blender Actions

### Classification (`clips.py`)

`classify` compares each clip's first-frame joint translations with the model's rest translations (median ratio of translation length to rest length over joints with a nonzero rest length):

| Result | Condition |
| --- | --- |
| `unrelated` | no joint hash of the clip matches the model |
| `relative/additive-style layer` | median ratio `<= 0.05` |
| `absolute skeletal` / `partial absolute skeletal` | median ratio `>= 0.9`, or a cutscene clip with no evidence; partial when the clip does not cover every model joint |
| `unsupported` | intermediate or insufficient evidence; a hash-bound `classification_overrides` entry in a profile is required |

A one-joint rig (most vehicles, props and debris) has no child joint to measure, so its clips give no evidence. Cutscene clips are absolute performances: of the cutscene clips offered to their performers that give evidence, 13,074 classify absolute, 30 intermediate and none additive, so a cutscene clip without evidence is absolute (`classify(..., cutscene=True)`, basis recorded in the evidence); animset clips without evidence stay `unsupported`. Many one-joint cutscene clips hold a constant root: all 12 Hydro Harvester shots and the trash and train props play as a still pose, while 12 of the Harvester Tank's 60 turn it by up to 179 degrees. Where such a performer travels through the scene is not in its clip and is not decoded.

This is a heuristic, not a recovered runtime layer flag. A decode that fails with an "unsupported" or "not validated" error marks the clip `unsupported`; any other failure marks it `malformed`. Clip entries keep raw and sample SHA-256, frame count, fps, unsampled model hashes, unmatched clip hashes and collapsed-rotation joints.

Relative clips are baked as standalone previews: rest translation plus delta translation, rest rotation times delta rotation (a collapsed delta uses identity), rest scale times delta scale. The blend partner and weight are unknown. Absolute and partial clips use source samples and source rest for unsampled joints.

### Bake procedure

1. **Discover.** Read actor and model references and the original animsets. Resolve clip joint hashes against the exact model skeleton (`model_skeleton()`, `embedded_clips()`). Shared animsets may hold unrelated attachments or variants; names do not prove applicability. Record every candidate as included, unrelated or unsupported with a reason. Two opt-in sources extend this (see [pipeline](pipeline.md#animation-in-the-pipeline)): cutscene shots of the model (see [Container](#container)), and animsets borrowed from a model with the same rest rig.
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

## Cutscene animation library

Characters without an idle animset only move in real-time cutscene (`cinematic2`) assets. `rc2016/export/cutscene_animations.py` exports every such performance of a model, reusing the portable exporter (`rc2016/export/models.py`) with the cutscene shots as its only animation source (`ExportOptions.cinematic_only`; `cinematic` adds them to the model's animset clips; `AnimationLibrary.with_cinematic` builds the clip list):

```text
python -m rc2016.export.cutscene_animations <game or asset_archive root> --model <archive path or unique stem> [--model ...]
        --out <folder> [--no-textures] [--overwrite]
```

Per model it writes `<out>/<model stem>/`:

| File | Content |
| --- | --- |
| `<stem>_cutscenes.glb` | The skinned model (geometry, skeleton, materials as in the portable export) with one glTF animation per decoded clip, named by the clip. Native Y-up basis, local TRS per joint per frame at the clip's fps; the cutscene scene space is not re-centred. |
| `manifest.json` | Model path, a note that these are cutscene performances and not idle loops, counts, the GLB summary and one row for every matching clip, decoded or not: name, name hash, source cutscene asset (path, id, SHA-256 of its decoded bytes), cutscene and shot parsed from the name (`<cutscene>_rt_shot<N>_<performer>`), clip format version, frames, fps, duration, joint counts, `decode_status` and, when not decoded, the reason. |
| `export.json` | The portable exporter's record (materials, notes, skipped clips). |

`decode_status` is `decoded` (exported), `not_decoded` (the decoder refused it, for example an unsupported clip version or a payload-size mismatch), `not_playable` (decoded but classified as not driving this skeleton) or `unnamed_not_exported` (the clip covers the model's joints but its name hash matches no string of its file, so it is listed and never guessed). Clips that do not cover the whole model or are another performer's are not matched (see [Container](#container)); an unnamed clip is listed only from a cutscene holding a named clip of the model. The command fails if the GLB's animation count differs from the number of decoded rows. For the Novalis mayor, Darla Gratch and Shiv this yields 6 of 7, 8 of 14 and 33 of 35 shots.

The pipeline can bake the same clips into a `.blend`: `--cinematic-clips` offers them, no `--clip` bakes all, and `--active-clip name:<shot>` leaves one as the active Action (see [pipeline](pipeline.md#animation-in-the-pipeline)).

## External sources (format evidence only; nothing copied or executed)

- [ig-tools](https://github.com/doesthisusername/ig-tools) at commit `b4ebe28a926cf298bff70ad01cdf9e14533c5b94`: DAT1 schema, asset decompression and section splitting; no skeletal sample decoder.
- [ALERT animation notes](https://github.com/Tkachov/ALERT/wiki/.animclip): section identities and base-record observations. Its later-game sample directory differs from the PS4 layout and must not be imported unchanged; [issue 12](https://github.com/Tkachov/ALERT/issues/12) reports incorrect poses.
