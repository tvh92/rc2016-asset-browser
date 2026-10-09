# Ratchet & Clank (2016) asset browser and extractor: format evidence and status

Scope: this tool handles Ratchet & Clank (PS4, 2016) assets only. Its GUI browses and
views assets and animations; the Blender pipeline exports models, materials and actions.

For scene construction start with `Animation/ANIMATION_WORKFLOW.md`. This file holds
format evidence, coverage and unresolved semantics. The packed decoder is reusable on
the validated PS4 version-15 layout; new work should focus on model binding and delivery.
`animation_delivery.py` embeds a clip catalog and `animation_controls.py` provides the
selection/playback panel (both animated deliveries verified; all 13 clip selections
compared before/after reopening in `animation_delivery_controls_verification.json`).

## Modules

- `animation_transforms.py`: standalone, model-independent S/Q/T extractor (original
  model + animset, resolves joint hashes, exports raw clips and source-scale/source-time
  JSON). No Clank bone names or authored curves.
- `animation_extract.py`: lower-level integer sampler. `animation_blender.py`: shared
  Blender adapter (SVD helper/deform method); Blender conversion is separate.
- `animation_audit.py`: container/section inspection. It corrects the DAT1 header to
  u16 section count + u16 unknown flags, enforces container boundaries, rejects truncation.

## Decoder rules

- Runtime S/Q/T component slots are 0/4/8, 16/20/24/28, and 32/36/40.
- Add unsigned samples to the signed packed base quaternion, then normalize the
  four-vector (homogeneous, not already unit). Clank rotor curves recover 18/36/72
  degree steps within quantization.
- Packed translation integers/deltas use `2 ** -base_control_byte_15`. The first
  control byte is the scale exponent; unlisted scales are one.
- Base tail: pairs of scale records, six u16 in `sx0,sx1,sy0,sy1,sz0,sz1` order then two
  u16 destinations; four records = 32 bytes, duplicate final records pad. Block count is
  metadata +0x28. High-bit scale values are unsupported.
- Quaternion `(-32768,-32768,-32768,-32768)` is surfaced as an unresolved
  collapsed-rotation sentinel (needs at least one zero scale).
- Clank model joint record +6 bit zero = segment-scale compensation
  `T * inverse(parent local S) * R * S` (user-approved pose; not verified against the
  running game; check on further rigs; other flag bits stay raw).
- Inverse-bind matrices begin at the next 64-byte boundary after the SQT array (not a
  fixed 32-byte gap). Ratchet bind scales are unit.

## Sample layout

- Clip Built `+0x20`: u16 joint count, u16 stored sample count. `+0x24`: u16 sample-element
  count, u16 bytes per sample.
- Sample Elements: `N` little-endian u16 destination offsets, then `N` bytes = bit width
  minus one. Values unsigned, packed in directory order LSB first, per frame.
- Stride = `4 * ceil(sum(bit_widths) / 32)`; leftover bits zero. Destination offsets are
  4-byte aligned into 48-byte joint records: `joint_index = offset // 48`,
  `component_byte_offset = offset % 48`. Joint order comes from clip hashes, not FBX order.
- Sample Data = stored sample count x stride; zero-element, zero-stride clips may omit sections.
- Preserve the stored sample count (corpus has both `duration*fps` and `duration*fps+1`).
  `anim_clip_built +0x08` bit `0x20` tracks the 13 duration-formula exclusive clips in the
  192-clip audit, but five one-sample Ratchet hydropack poses carry `0x41` without it: assign
  no universal timing meaning. Timestamps are `sample_index / fps`.
- Do not assign component semantics from destination offsets alone. The earlier Havok/spline
  lead is superseded by frame-major packed samples.

## Coverage

1. Integer sampler: 11 animsets, 680 embedded clips (658 sampled, 22 constant), 22,027
   frames; all payloads re-encode exactly, padding zero (`animation_validation.json`).
2. Source SQT parser: all 520 Ratchet Protosuit clips pass (`animation_humanoid_coverage.json`);
   numeric/structural only, not appearance.
3. Clank Blender: 12 clips / 243 frames reopened vs decoded affine matrices, max error ~`2.84e-5`;
   13 regression/malformed-input tests; 132 RGBA frames with unclipped bounds. Delivery:
   `_Archive/2026-10-08_legacy_fbx_models/Gadgets and items/Clank Packs/Clank_Backpack_Animations.blend` (model README documents
   playback, affine baking, scale compensation, root placement, limits; 4 loop GIFs, 21 transition proofs).
4. Ratchet Blender proof: `Run_Forward_hero_Ratchet`, 20 frames at 30 FPS on the 193-bone
   Protosuit rig; 182 clip hashes match, 16 unmatched remain in the source export, 11 unsampled
   bones use model rest; matrices agree within `1.53e-5`; 20 RGBA frames pass alpha/bounds.
   Artifacts: `_Archive/2026-10-08_legacy_fbx_models/Gadgets and items/Protosuit/Ratchet_Animation_Proof.blend`, `Animation Proof/Ratchet_Run.gif`.
   Armor-only model (no face/fur). Root-motion paths and runtime subframe interpolation are out of scope.

## Running it

Windows `python` alias may not work; use Blender's interpreter (no dependencies). Exports
refuse existing output directories; choose a new one.

```powershell
$py = 'C:/Program Files/Blender Foundation/Blender 5.0/5.0/python/bin/python.exe'
& $py Animation/animation_transforms.py 'path/to/actor.animset' --model 'path/to/actor.model' --prefix 'Idle' --out 'new-output-directory'
& $py Animation/animation_extract.py '_Archive/2026-10-08_legacy_fbx_models/Weapons/Sheepinator/Source Archives/wpn_sheepinator/wpn_sheepinator.animset' --bones '_Archive/2026-10-08_legacy_fbx_models/Weapons/Sheepinator/animation_bones.txt' --out 'new-output-directory'
& $py Animation/animation_extract.py --validate-tree . --report Animation/animation_validation.json
& $py -m unittest -v Animation/test_animation_extract
```

- `animation_extract.py` bundle export needs a root lookup pointing directly at embedded clips
  (verified on Sheepinator); complex character animset lookups are deliberately rejected.
  `--validate-tree` checks embedded clips independently; it does not imply support for every lookup format.
- Supports the inspected PS4 version-15 layout only (not other codecs/versions, shader/VFX
  animation). Validate skeleton mapping, inheritance, collapsed parts and placement per actor.
- Tests cover known curves, joint order, bit-string re-encoding, extracted-clip decoding,
  overwrite refusal, header flags, bad destinations/stride/padding, truncated/escaping sections.

## Windows GUI

`rc2016_asset_browser_gui.py` (stdlib Tkinter) front-ends the `animation_transforms.py` CLI:
recursive scan, pairs a `.model` only with an exact same-directory `.animset`, filtering,
multi-select, sequential batch; no PowerShell needed. Each model writes to a stable folder under
the output root; the CLI runs in `.staging/`, then the GUI checks manifest, source hashes, every
JSON clip and raw clip hash before publishing. Verified folders are skipped; incomplete/changed
ones are rebuilt with the old folder kept as `.stale-YYYYMMDD-HHMMSS`. A failed model does not stop
the batch; cancel stops the child and leaves staging for diagnosis.

```powershell
& 'C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe' Animation/rc2016_asset_browser_gui.py
& 'C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe' Animation/rc2016_asset_browser_gui.py --scan 'path/to/sources'
```

Build the no-install `PS4 Animation Extractor.exe` from `Animation/rc2016_asset_browser.spec` with
PyInstaller (child processes use its hidden backend mode).

## External sources (format evidence only; nothing copied or executed)

- [ig-tools](https://github.com/doesthisusername/ig-tools) at `b4ebe28a926cf298bff70ad01cdf9e14533c5b94`:
  DAT1 schema, asset decompression/section splitting; no skeletal sample decoder.
- [ALERT animation notes](https://github.com/Tkachov/ALERT/wiki/.animclip): section identities and
  base-record observations; its later-game sample directory differs from the PS4 layout (do not
  import unchanged); [issue 12](https://github.com/Tkachov/ALERT/issues/12) reports incorrect poses.

## Next work

Validate inheritance and a representative humanoid clip against that actor's own rig and geometry;
generalize the Blender adapter; preserve original rig/skin/cameras and separate source transforms
from presentation placement.
