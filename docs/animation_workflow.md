# Adding source animations to a Blender scene

Project entry point for animation work: build the model scene, discover its original
animation set, validate the rig binding, bake applicable clips as Actions, embed the
selector, verify after reopening. Format notes: `docs/animation_format.md`;
generic rules: root `AGENTS.md` ("Source animations").

Root keeps importable tooling and tests; generated coverage/delivery reports go in `Audit/`;
model-specific proofs, scripts and reports stay beside their model. Users must be able to
choose a clip and frame and render without re-extracting or editing Python paths. Clank and
Ratchet prove the PS4 version-15 skeletal sampler; it is not a universal codec.

## Reuse these files

| File | Responsibility |
| --- | --- |
| `rc2016/formats/container.py` | Container/section inspection and clip inventory |
| `rc2016/formats/animation_samples.py` | Exact packed integer samples and raw embedded clips |
| `rc2016/formats/animation_transforms.py` | Model hash mapping and source S/Q/T samples; no Blender assumptions |
| `rc2016/pipeline/animation_bake.py` | Affine bone baking and numerical verification; no character names |
| `Animation/animation_delivery.py` | Embed a persistent catalog and portable selection controls |
| `Animation/animation_controls.py` | Embedded 3D View Animations panel; no external dependencies |
| `rc2016/app/main.py` | Tkinter archive browser and resumable multi-model batch front end |

Model-specific examples: Protosuit `build_animation_proof.py`, `probe_animation_bind.py`; Clank
Packs `build_animation_preview.py`, `finish_backpack_animations.py`. Read that model's README
before reusing its binding or variant rules. Clank's older naive preview and rotor experiment
are research artifacts: do not copy their incorrect inheritance or substitute procedural rotors.

## Starting from an extracted PS4 archive

Root `scripts/run-pipeline.ps1` automates archive discovery, reference indexing, selective
decompression, sample extraction, scene building and reopened validation (see
[`pipeline/README.md`](pipeline/README.md)). It starts after PKG extraction, never modifies
existing masters, and generates review images for the user (no AI visual review).

## Large libraries and deliberate subsets

The pipeline includes every applicable supported clip by default. Optional
PowerShell `-Clip <string[]>` or repeated CLI `--clip` selectors accept exact
`name:`, `hash:` (source name hash) and `action:` identities. An explicit subset
keeps the full catalog and records user omissions separately from decode failures;
it is labelled incomplete as a full library. Full rebuild: use the same sources
and settings, omit selectors and pass `-Force` / `--force` for an existing final.
Selected and full deliveries have separate verified cache identities.

`pipeline/channel_library.py` stores exact float32 canonical defaults and the
library's moving-channel mask. Every channel that moves has a curve in every
Action, including source rest and sparse earlier clips; late activation backfills
those Actions before checkpointing. Globally invariant channels use persistent
defaults restored by the embedded selector. Do not omit a curve merely because a
single clip is constant: that can retain a preceding clip's pose. Reopened
validation checks the shared reset domain as well as every source Action/NLA sample.
The rigid right SVD factor's unit scale preserves source S/Q/T bytes and timing;
native scale/shear stay in the left factor and all derived matrices retain the
unchanged numerical tolerance. Results and limits:
[`pipeline/Research/AnimationMemory/README.md`](pipeline/Research/AnimationMemory/README.md).

## Scene build procedure

1. **Discover and inventory.** Read actor/model references and original animsets. Use
   `model_skeleton()` and `embedded_clips()` to list clips and resolve hashes against the exact
   model. Shared sets may hold unrelated attachments or variants; names do not prove
   applicability. Record every candidate as included, unrelated or unsupported with a reason;
   record the search result if no set exists. Include all applicable supported clips unless the
   task narrows it. (The Ratchet run file is a deliberate single-clip proof.)
2. **Validate binding before baking.** Compare names, parents, rest SQT, FBX rest matrices,
   weights and inverse binds (separate from the hand-posed presentation). The adapter uses 50
   Blender armature units per source translation unit; validate per rig. Ratchet has unit bind
   scales; Clank has nonunit scales and a recovered mesh bind: do not copy Ratchet's corrections.
   Preserve the original rig/meshes; record unmatched clip hashes and unsampled bones.
3. **Decode and export.** `sample_transforms()`; retain raw clips, hashes, exact frame counts/rates.
   Unsupported packed channels/scales stay explicit failures. Null collapsed rotations are
   unresolved; use a source-rest fallback only where zero scale and model visibility justify it.
4. **Bake on a separate animation rig.** Give `make_bake_rig()` / `bake_clip()` validated bind
   corrections and a fixed presentation anchor. The helper/deform SVD hierarchy preserves shear
   and collapsed scales (ordinary connected FBX bones cannot). Keep source samples intact, keep
   actions with fake users, constant interpolation at integer source frames. Fit placement to the
   latest saved camera; never rebuild cameras or alter poses to ease framing.
5. **Package selection and overview.** `install_controls()` sets action, slot, frame range and FPS.
   If variants need different geometry/cameras/placement, embed a model-owned text defining
   `play_clip(name)` and pass it as `selector_text` (Clank does); never infer pack visibility from
   bone names; the callback must also set timing and slot. Also create a named NLA track placing
   every included action sequentially, with a scene marker at each clip boundary; save with it
   active and document how single selection mutes it. Do not rewrite shader branches for animation.
6. **Verify and deliver.** Reopen and compare all baked frame matrices with decoded targets; check
   fake-user actions, slots, catalog, clip selection and variant visibility; numerically validate
   every action. Render low-res representative phases per new rig/motion family (check deformation
   and bounds); reuse proofs for controls-only changes. Preserve geometry/weights/UVs/materials/
   cameras, pack dependencies, restore render defaults, save a useful default clip. Record the
   inventory, limitations and playback instructions in the model README.

## Integration example

```powershell
$py = 'C:/Program Files/Blender Foundation/Blender 5.0/5.0/python/bin/python.exe'
& $py rc2016/formats/animation_transforms.py 'actor.animset' --model 'actor.model' --out 'Animation Extracted'  # new dir; omit --prefix for whole set
```

```python
from rc2016.pipeline.animation_bake import make_bake_rig, bake_clip
from Animation.animation_delivery import install_controls

rig = make_bake_rig(source_rig, rows, 'Actor | animation rig')
actions = []
for clip in applicable_clips:
    # matrices come from the model-specific validated bind/placement step
    action, error = bake_clip(rig, rows, clip, corrections, anchors[clip['name']])
    actions.append(action)
install_controls(rig, actions)  # or selector_text='Source animation controls.py'
```

Keep corrections, variant rules and source locations in the model build script, not the decoder.
Rebuilds use the latest master and protect user changes (examples refuse overwrites).
Controls-only packaging needs no rebaking or rerendering.

## User playback

The default action plays with Space and renders with F12. For clip selection run the embedded
**Animation Controls.py** in the Text Editor once per session, then 3D View **N > Animations**:
choose a clip, **Load selected clip**, play or pick a frame, set the output path, **Render
animation**. Panel and catalog travel inside the blend (no add-on, script path or preference
change). Plain Action Editor selection skips a model's variant callback and clip timing.
For large libraries prefer **Load selected clip** or **Load 24 clips from selected in order (fast)**:
Blender's NLA evaluates every overview strip's channel domain per frame, so the full overview
runs about 0.12 s/frame at 520 clips; the range button uses a temporary track and is removed by
the other buttons (`pipeline/Research/AnimationMemory/continuation/README.md`).

## Scope

Stop format research for rigs covered by the working layout; validate the next actor when a scene
task needs it. Root-motion paths, subframe interpolation, further joint-flag semantics,
negative/high-bit scales, other codecs and shader/VFX animation are separate problems; investigate
only if one blocks a requested animation. Numeric parser coverage does not prove clips look
right in game. Keep source archives, delivery blends, decoder tests and proof audits; keep new
research separate from these entry points.
