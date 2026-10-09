# Ratchet & Clank PS4 model and animation pipeline

Research index: [Research/README.md](Research/README.md). Game-accurate shading and lighting now live in `Claude_shading_fix/` (see root `AGENTS.md`); the Novalis light-grid/probe/atmosphere research is archived under `_Archive/2026-10-06_obsolete_research/`.

The backend now automatically translates supported native GCN deferred writers
into Blender materials using source pass tables, texture slots and material
parameters, without registering a recipe for each graph. Unsupported control
flow, engine inputs or conflicting bindings retain the existing preview
fallback. Profiles and fuller skin/forward recipes remain available. See the
[translator implementation and limits](Research/Shaders/ShaderTranslatorProof/README.md)
and [370-graph coverage audit](Research/Shaders/ShaderTranslatorCorpus/README.md).
This does not establish complete game shader equivalence or change promotion rules.

`Run-PS4AnimationPipeline.ps1` turns compatible assets from the already-extracted European PS4 release into one portable Blender file per selected model. It discovers relationships from archive metadata and decoded source references, decodes native geometry and textures, builds materials and the model's own skeleton, binds source skin weights, creates persistent Actions and an NLA overview, reopens the file in another Blender process, and writes machine-readable reports plus PNGs for manual review (no AI image review). Specific to **Ratchet & Clank (EUR) for PS4**: it does not extract PKGs and does not support other games or the Rift Apart PC format.

## Run

Requires Windows PowerShell 5.1 or 7, Blender 5.0 with bundled NumPy, and an OptiX GPU. From the project root:

```powershell
.\Run-PS4AnimationPipeline.ps1 `
  -ExtractedRoot "C:\Users\Tim\Documents\Bestanden\Downloads\Ratchet & Clank (EUR)" `
  -Model "hero_ratchet" `
  -OutputRoot "C:\Users\Tim\Documents\Bestanden\Downloads\RC_Output" `
  -ImportLOD Highest
```

| Parameter | Behavior |
| --- | --- |
| `-Model <name-or-path>` | One exact actor/model stem or archive path. Ambiguous selectors fail with candidates. |
| `-AllModels` | Process every indexed model independently; failures are retained and the batch continues. |
| `-OutputRoot <path>` | Caches, reports, candidates, finals, textures and review images go below this separate directory. |
| `-Force` | Rebuild and atomically replace an existing final file. |
| `-ReuseCache` | Reuse only a final whose input fingerprint, file hash, validation report and preview hashes still match. |
| `-ExportTextures` | Debug only, not for deliveries: keeps loose converted DDS beside the model, duplicating images already packed in the blend. |
| `-KeepIntermediates` | Keep `blender/` (candidate/checkpoint), `compressed_assets/`, `decoded_sources/`, `work/tmp` and `cache/t` (converted DDS) after a fully successful run. By default they are deleted then: they are regenerable and were ~85% of a run (Blarg: 0.8 of 1.07 GB). Failed runs keep them for diagnosis. Evidence that needs a decoded source file must copy it into its own evidence folder. |
| Asset index | The archive-wide `assets` table (~194k rows, ~55 MB) is stored once in `Animation/pipeline/shared_index/assets_<rows-hash>.sqlite`. Each run keeps only its `references_found` in `reports/asset_references.sqlite`, and `reports/asset_index.json` names both. Read through `Animation.pipeline.index.open_asset_index(output_root)`, which returns both tables as before and also reads legacy runs. `migrate_legacy_index(output_root)` converts an older run. |
| `-ImportLOD All\|Highest\|Lowest` | Select model subsets using the source look/LOD table. |
| `-LightingSource HDRI\|Veldin\|Novalis` | **HDRI (default)**: the shared review lighting in root `shared_lighting.json` (currently `golden_gate_hills_1k.exr` ×2, no lights, Khronos PBR Neutral, exposure −1.0, compositor off), applied by `blender_defaults.apply_shared_lighting` and recorded on the scene; `external_hdri` validation compares the scene with the job's lighting settings. `-LightingConfig <file>` / `--lighting-config` uses another lighting file; `-HdriPath` overrides only its HDRI. Veldin (opt-in): the captured Veldin game look (key-light colour/strength, fitted ambient world, captured exposure, `RC PS4 Game` tonemap as compositor nodes, view `Raw`) via `Claude_shading_fix/game_look.py`; numerically faithful to the capture but washed out on standalone models, so no longer the default (2026-10-06). Novalis: decoded reflection probe. CLI: `run.py --lighting-source hdri\|veldin\|novalis`. |
| `--novalis-atmosphere-sun` (runner) | Adds the opt-in Novalis RGB96 Sun candidate (exact atmosphere ray; runtime field names pending). |
| `-FailOnMissingAssets` | Fail instead of delivering an explicitly limited material/attachment result. |
| `--skip-previews` (runner) | Keep build and separate-process validation, skip manual-review rendering. |
| `-BlenderPath`, `-HdriPath`, `-Profile` | Override Blender, the HDRI fallback path, or a hash-bound model profile. |
| `-ExperimentalAmbiguousInheritance` | See Animation below. |
| `-Clip <string[]>` / repeated `--clip` | Optional exact source selectors: `name:`, `hash:` (32-bit source name hash, e.g. `0x0B358C81`), `action:`, or an unambiguous exact name/action. Missing, ambiguous or unsupported matches fail before Blender. Omit for all supported clips. |

Game-look renders use `Claude_shading_fix/render_game_look.py`, not these pipeline lighting options (the Novalis probe/Sun options are legacy presentation lighting).

HDRI-lit builds and `blender_defaults.py` scenes link the shared `golden_gate_hills_1k.exr` (6 MB float RGB, 4×4 box-filtered from the 96 MB `golden_gate_hills_4k.exr`; see `Claude_shading_fix/hdri_1k_check/`) by absolute path (not embedded); keep it at the recorded path. A missing EXR fails clearly. Model DDS stay packed. For manual-review PNGs, fresh-process validation temporarily uses that HDRI at strength 2 with separate lights hidden, even for Novalis-probe scenes; the saved world is unchanged and `preview_lighting` plus each preview row record source, path, SHA-256 and presentation-only status (proof: `test_blender_review_lighting.py`).

### Output

```text
OutputRoot/
  ModelName_stableId/
    ModelName.blend  meshes/manifest.json  textures/  materials/  decoded_sources/manifest.json
    animation_cache/  reports/  validation/validation.json  review_images/
    part_assets/                 # multipart-character material/texture evidence
  compressed_assets/             # shared hash-addressed extraction cache
  decoded_sources/               # shared hash-addressed decoded cache
  blender/                       # working candidates/checkpoints
  reports/{manifest,global_summary,validation_report,unresolved_references,unsupported_clips,source_mapping}.json
  reports/asset_index.sqlite
  work/  cache/
```

- The source archive is read-only; asset IDs, archive occurrences, compressed/decoded hashes, source paths, code hashes and final hashes are recorded. Final promotion happens only after the candidate reopens and passes validation in a separate Blender process.
- Each model report adds `geometry_game_equivalence` to `reports/limitations.json` (short status in `manifest.json`, aggregate in `global_summary.json`). `structural` covers source/count/topology/round-trip; `semantic_equivalence` counts per-subset UV0/normal/tangent claims as proven/unproven/unknown. `game_accurate_uv_claim: false`, missing evidence or any unproven interpretation keeps `equivalent` false. It does not change `COMPLETE_*` labels.
- Converted material DDS use a flat 64-hex SHA-256 filename (from the canonical native texture asset path) under `textures/` with `-ExportTextures`, otherwise `OutputRoot/cache/t/<model-id>/`. Material manifests keep source `path`, asset IDs/hashes and `dds_path`/DDS hash. The pipeline preflights the projected texture path and asks for a shorter `-OutputRoot` if it exceeds 240 UTF-16 units.

### Texture transport gate

Fresh-process validation checks every material texture: `texture_transport_validation.bindings[]` has `source_exact_top_mip=true` only when built/stream identity and bounds are consistent, a raw or LZ4 stream matches the built header size, and a supported top mip deswizzles byte-for-byte to the pipeline DDS. `null` carries an unsupported/missing-stream reason; `false` is contradictory evidence and fails validation. Reopened packed bytes, dimensions, colour space and `CHANNEL_PACKED` alpha are checked; the R8G8_UNORM-to-PNG adapter records its conversion marker, dimensions and PNG hash. Transport and packed-image integrity only; no shader, binding or lower-mip claims. Tests: `TextureTransportGateTests` in `test_pipeline.py`, `test_blender_texture_transport.py`, `Research/Proofs/Textures/RatchetTransportFailures/`.

Textures with no `layout.csv` entry at all (2026-10-09: `textures/rkamins/env/rk_no_spec_c.texture`, the default of slot `0x1576D848` in the Novalis foliage graph `rp_nov_plant_tree_foliage_transparent` and bound directly to `0x1576D848`/`0x93CA35AB` by `rp_nov_grass_hill_blend`) no longer fail the whole material. `classify_absent_texture` records a graph default that the instance overrides for every such slot key under `unbound_texture_references` (status `overridden_graph_default_missing_from_archive`, not sampled). It records any other reference in the material's `texture_errors` (`missing_from_archive`), which surfaces as `limitations.json` `missing_source_textures` and the model status `COMPLETE_MESH_ANIMATIONS_TEXTURE_LIMITATION`. No substitute image is applied. The library `textures` list and the transport expectation contain only textures that a built material binds. Decode-cache leftovers from a material that failed later go to `orphaned_decoded_textures`; before this change they made the reopen gate report "expected one reopened source image, found 0". Tests: `AbsentTextureTests` in `test_pipeline.py`.

The DDS writer emits a single top mip. Header bytes 30/32 are parsed as SD/HD mip counts (bounded by the implied chain length, zero = one, matching RCRA-Forge); extra stream bytes are not serialized as mips until their PS4 layout is established (see [`sheepinator_lower_mip_serialization_blocker_20260923.md`](Research/Proofs/Textures/sheepinator_lower_mip_serialization_blocker_20260923.md); test `test_texture_header_mips.py`).

## Native PS4 data handled

Reads direct PS4 DAT1 model sections (type hash `0x882A03DC`); compressed archive payloads, ALERT outer containers and MSMR/RCRA PC layouts are rejected, not guessed.

- Fixed-point positions `int16 / 2^shift`, where shift is the u32 at byte 32 of model section `0x283D0383` (default 12 = /4096 when the section is absent). Across all 6,949 archive models it reproduces the mesh-record bounding spheres for every model whose bounds give a clean power-of-two factor (6,711/6,711; shifts 3–12, about a third of the corpus is not 12). Recorded as `source_units.position_shift`; evidence in [Telepathipus README](../../Other%20models/enm_telepathipus_v2/README.md). Shader-observed 10:10:10:2 UNORM normal (+8) and tangent (+12) decode, lossless raw packed tangent words, triangle indices, 4- or 8-byte half-float UV0 records (secondary 8-byte pair kept as opaque words), structurally guarded optional UV1/colours, 64-byte mesh/subset records, material indices, opaque high mesh flags, four-slot looks, LOD membership.
- 1/2/4/8-influence `0xDCA379A2` skin batches, normalized weights, strict bounds. Batches whose u32 at +4 / u16 at +8 are nonzero read their u8 ids through a u16 joint palette in `0x5240C82B` (ALERT: "Model Skin Joint Remap"; +4 = byte offset, +8 = count, palettes packed at 16-byte alignment); used by 5 archive models with >256 joints. The NPC form with a nonzero +4 and zero count is undecoded and fails closed. Static models (no joints) become geometry-only assets.
- Material/materialgraph references and the exact texture built/stream pair; PS4 8x8 block untile, raw or LZ4 streams, DDS DX10 output for RGBA8, RG8, R8, BC1/2/3/5/6H/7; high-resolution stream top mips when present. Images are packed into the blend. Colourspace follows the native DXGI format (sRGB 29/72/75/78/91/93/99, else Non-Color; normals Non-Color) via the patched `materials.py`; alpha Channel Packed.
- Per-model geometry corrections live in `profiles/<source-model-sha256>.json` and apply only to that exact source (source hash, mesh count/order and ordered raw bound signature must match before any vertex change; raw signed XYZ kept as `SourcePositionI16_X/Y/Z` and rechecked after reopening). No profile currently applies one: the Blarg Trooper 2× correction (`520efb32…`, [`ProductionCorrection/README.md`](Research/AnimationCoverage/BlargInheritanceComparison/ScaleRegistration/ProductionCorrection/README.md)) was retired on 2026-10-06 because the decoded position shift (11) supplies the same factor; the profile keeps it under `retired_native_mesh_position_correction`. Sheepinator also has a profile.
- Native Y-up data stays unchanged for validation; rig and mesh objects get the verified `(x, y, z) -> (x, -z, y)` presentation transform (recorded on the scene and objects, asserted after reopening; animation bounds use the same basis before camera fitting).
- Normal and tangent (2026-10-07): the game's compiled vertex shader fetches the +8 and +12 words of the 16-byte vertex record as 10:10:10:2 UNORM. Each vector is three 10-bit fields decoded as `2*(u/1023)-1`; the 2-bit field is unused. Both vectors are transformed by the same skinning/world basis. `native_model._normal` and `_tangent` use this decode and renormalize. Evidence is in [`Research/NativeTranslators/VertexInput20261006/README.md`](Research/NativeTranslators/VertexInput20261006/README.md) ("Packed normal/tangent decode").
  - Across 92,353 vertices on seven models both words are unit length to within 0.0015. +12 is orthogonal to +8 (median |dot| ≈ 0.0006) and lies a median 0.3–0.8° from the UV-derived tangent.
  - The old MaxScript `[10,10,11] / 511` reading gave the same normal directions to within 0.1°, but read bit 30 into Z; earlier builds therefore have correct normals.
  - The format is from the shader plus numeric agreement, not a captured draw, so geometry game-equivalence keeps normals/tangents `unproven`.
  - The tangent is kept twice: exactly as `SourceTangentPacked` (INT) and decoded as `SourceTangent` (FLOAT_VECTOR, POINT, native mesh space). The bitangent sign is not stored in +12; its top bits were zero while UV frames had both orientations. Blender's Normal Map node still uses its own UV-derived tangents.
  - Reopen validation compares `SourceTangent` with a fresh decode. Blends built before this change lack it and are reported as `decoded_tangent_attribute: absent_pre_tangent_decode_build` rather than failing.
  - ALERT's spherical decoder is for a different stream and is not applied. The MaxScript's `setNormal` calls are commented out, so FBX normals cannot validate either reading. For 8-byte UV records both pairs are kept (first = UV0 candidate; second as `SourceUV0SecondaryU/V` ints); no UV1/lightmap meaning is assigned.
- Degenerate source triangles (2026-10-09): some environment models carry triangles with a repeated index or zero area (e.g. `tb_sewer_pipe_large_end` subset 000: 5 + 3; `tb_nov_gate_bldg02` subset 008: 6 + 2). In Blender 5.0 they crash `normals_split_custom_set_from_vertices` (access violation in `mesh_normals_corner_custom_set`) or change corner normals on save/reopen. `native_model.blender_safe_triangles` leaves them out of the Blender mesh only; vertices and the source index buffer are untouched. Each dropped triangle (subset, source triangle index, indices, reason) is listed in the build report (`degenerate_triangles_dropped`), stored on the object (`source_degenerate_triangles_dropped`, `source_triangle_count`), and compared with a fresh decode when the file is reopened. Meshes without degenerate triangles are built exactly as before.
- Field map, confidences, unsupported families, hashes and FBX measurements: [`native_geometry_audit.md`](native_geometry_audit.md). Regenerate with `audit_native_geometry.py` (loose corpus) and `audit_native_archive_sample.py` (original archive sample: 21 of 1,903 models have non-identical LOD ranges, all four slots nonempty). Focused native-vs-FBX: ten weapon/Protosuit pairs pass positions, topology, UV0, weights on matched sections (four FBX-omitted cases handled explicitly). 8-byte UV checks: `test_blender_native_uv_variants.py`, `audit_native_uv8_variants.py`, `test_blender_native_uv8_formats.py`, `audit_native_uv8_formats.py` (no alternative reaches tolerance). Geometry-only proof builds: `build_native_geometry_proofs.py`.

## Materials

New unprofiled builds preflight every referenced materialgraph before Blender construction. `shader_families.py` recognizes exact canonical path + decoded SHA-256, preserves parameter tables/defaults/instance overrides and reports shader stages/support scope. Registered families dispatch through `Claude_shading_fix/rc_game_shader.apply_preflight_material()`: `skin_emissive` (Toxic Crab) and `basic_skin` (Puffer Fish; no self-lit, F0CD85FF detail-normal blend `w = 0x59011FBD * _g.B`, secondary at UV0 * `0xB7ABB64A`; [decode](../../Other%20models/enm_puffer_fish/evidence/BASIC_SKIN_DECODE.md)). Their native normal-dependent front-ends are supported, while complete forward skin-ramp lighting is explicitly partial. Fresh-process checks assert polygon-used skin-material wiring, source-format colourspaces/packing, parameter values and normal reconstruction. The toolkit code hash participates in cache invalidation.

Texture bindings are resolved by slot key: a material record in section `0xB967FF7A` whose key no referenced graph declares in section `0x1CAFE804` is never sampled, so it is dropped and listed in `unbound_texture_references` (the Puffer Fish material carries stale `enm_alien_swarmer` records). Registered families map declared keys to roles (`_c`/`_g`/`_n`/`secondary_normal`).

Unknown unprofiled graph identities receive a labelled source-colour-only preview (`game_equivalence=false`); `_g` equations and self-lit behaviour are not guessed. Existing hash-bound presentation profiles and documented thin-glass recipes retain their scoped behaviour. Older delivered blends may still use `_c * (1 - _g)` plus Glossy; they are presentation references until deliberately converted. Missing/new graph variants stay visible in material reports so research can happen before an appearance claim or final render.

- Authoritative materialgraph names identify conservative glass/transparency families. Model-specific recipes live in `material_profiles/` and apply only when canonical material path and source SHA-256 both match (Sheepinator: 18% body reconstruction, roughness floor, Principled-only colour tuning, thin outer glass, two-noise transparent energy layer). Profile file and evidence path are embedded as material properties and asserted after reopening.
- Compiled materialgraph execution, procedural animation, runtime tints and unknown shader parameters remain reported limitations; visual tuning inferred from a similar name is never promoted. Unresolved colour is labelled, not grey.
- `presentation_tuned` hash-bound profiles exist per material when a reproducible review decision exists: Combuster hose (`3d101a87c45b09fa3ed9ac5b78ec13af8085b862c9c75ca326dd200b38c927aa`), V1 body (`4f7293a02d8bef4c5796aab033aa3dea6e35287cb3ced0411412a81e8ee285d4`), V5 body shell (`32f423bb2af28e86707cec74a9038a7e9c4e87219f6b363457e898d781897c82`). C/G/N role/path/DDS hashes/colourspaces are checked first; each sets mix 0, unlinks and zeros C-alpha Principled emission, roughness 0.48, Metallic 0, keeps C base colour and N, and keeps a fake-user factor-1 generic baseline. `game_equivalence: false`.
- Historical `material_evidence.generic_cg_presentation_gate` metadata records older presentation recipes. New `shader_preflight` and `shader_status` fields distinguish recognized partial families from unsupported graph equations; neither establishes game equivalence.
- `COMPLETE_*` labels describe delivery coverage, not game accuracy. Each material report has `material_evidence` (source-binding, reproducible recipe, capture/runtime equivalence, unsupported operations); `validated_hash_bound_recipe` is reproducibility only; `game_equivalence: false`/`unproven` means not established, not disproven. The model result repeats this as `material_game_equivalence`.

## Game-native lighting (legacy presentation; research archived)

- Novalis probe (default): the validated RefHash-linked reflection asset `0xad76696216314d22` has six 512x512 DXGI 95 BC6H faces. The pipeline PS4-untiles them, picks the face orientation that passes every cube-seam test (state `(6,6,6,6,6,6)`, seam ratio ~1.268), samples through the game-to-Blender basis, writes a 1024x512 float EXR, packs it, and uses it as the sole world light at strength 1.0. Source path/hash, face states, seam measurements and EXR hash are embedded and rechecked after reopening. No sky dome, no extra lights, no guessed exposure.
- The EUR archive has 21 `volumereflections` probes, 21,158 light-grid assets and 153 local 512x256 BC6H env probes; no `lightmap`/`irradiance` paths. Decoded: day lightgrid probe-selector association (`reports/novalis_lightgrid_probe_assignments.json`), `lighting.zone` tile map (727 entries, `reports/novalis_lighting_zone_grid_map.json`), the reflection-volume side table (global probe authored at game-space centre `(94.7155,58.0382,92.1824)`, nearest grid `grid_88_56_88`), and the atmosphere asset `atm_novalis.atmosphere` (SHA-256 `4799f2e5e2b82d60f8f4b215fdf6f161d6edc70a0a240c3fe30bdc3c29b40dfd`; unit ray at offset 72 = Blender `(0.07767982, 0.73907381, -0.66913062)`; RGB96 at offset 96 `(0.70080566, 0.58398438, 0.57971191)` used directly as the opt-in Sun, no exposure scaling).
- Unresolved (do not claim as decoded): packed light-grid irradiance words (R10G10B10 candidates, fifth word = packed unit direction), runtime field names for RGB96/lightgrid, authored light-record (`0x97FF6EB5`/`0x06ABCAB2`) semantics, a world position for a standalone weapon. Local-probe, blended-probe and probe-volume-centre test blends under `NativeExample/wpn_sheepinator_3c094b58/` are diagnostics only (the probe-volume-centre render is intrinsically dark; do not brighten it by an undocumented multiplier). The `1000` field next to the Novalis ray is not Sun strength.
- Forensic JSONs remain in `NativeExample/reports/` (`lighting_*`, `novalis_*`, `lightgrid_*`); their generating scripts (`audit_lighting_*.py` etc.) and `lighting_authored_schema_notes.md` are under `_Archive/2026-10-06_obsolete_research/Animation/pipeline/`. No runtime executable (`.self/.prx/.elf/.exe`) exists in the archive; further decoding needs a runtime capture.

## Multipart actors and weapon variants

Actor/config references identify multipart parts. Source-authored `base` parts with joint hashes in the actor rig are assembled visibly and skinned to it; masks, suits, weapon variants and alternates are listed as identified variants and kept out of the default assembly (no invented runtime selection; undecoded mount/socket rules stay limitations).

Fusion Grenade, Pyrocitor, Proton Drum and Combuster have hash-pinned V1/V5 assembly hooks with fresh-process checks. Proton Drum's switchable delivery includes Activate and Deactivate; Fire and Open_Idle are inheritance-ambiguous and are included under the default SSC0 rule (`default_SSC0`), or as both FULL and SSC0 candidates with `--experimental-ambiguous-inheritance`. Combuster includes Turn_360, and Fire/Open/Idle the same way. Existing delivered weapon masters predate this default. Presentation assemblies are source-local; actor weapon selection and attachment transforms are unproven ([cross-case audit](Research/Variants/Generalization27/FinalAudit/README.md)).

## Animation and playback

All supported source clips remain persistent Actions by default. The baker omits
only channels that are exactly invariant across source rest and the complete
library; a channel that first moves in a later clip backfills reset curves into
all preceding Actions. Canonical values and the moving-channel mask persist on
the rig for checkpoint resume and are checked after reopening. The right SVD
factor uses its algebraic unit scale after a rigid-rest guard; native scale,
reflection, collapse and shear remain in the left factor. Exact source samples
and timing are unchanged; derived matrices keep the existing `1e-4` tolerance.
See [the measured large-library acceptance case](Research/AnimationMemory/README.md).
This reduces allocation but does not make a 520-clip library inexpensive.

Explicit `-Clip` / `--clip` selections retain the complete source catalog, original
decoder statuses and provenance, with separate `included` or
`omitted_by_user_selection` selection statuses. They are labelled
`delivery_complete_animation_library=false`, including an explicit enumeration
of every clip. Selection is part of the cache fingerprint; a selected delivery
cannot be reused as a complete library. Rebuild the full library from the same
sources/settings without selectors and use `-Force` / `--force` to replace the
selected final. Fixed-coverage weapon assembly hooks are skipped in explicit
selection mode, with a logged reason. Selecting one experimental FULL/SSC0 Action
retains both interpretations of its originating source clip.

The validated version-15 sampler runs on each related animset. Absolute, partial and relative/additive-style samples become persistent Actions on the exact armature; unsampled joints use source rest; relative clips are labelled standalone previews when the blend partner/weight is unknown. Unsupported codecs, ambiguous classifications, unrelated clips, path-style root motion and VFX are reported, never substituted.

- The NLA catalog uses half-open logical intervals with Blender-safe physical endpoints and one exact timeline clock (mixed frame rates). Boundaries, held ticks, one-frame clips, markers, selection, action and NLA playback are checked numerically after reopen. Embedded `Animation Controls.py` provides the 3D View sidebar controls (run once per session); the overview plays without auto-execution. A temporary 24-clip range overview gives fast playback in large libraries; the baker stores an identity right SVD factor where singular values are equal to float noise (see `Research/AnimationMemory/continuation/README.md`).
- Bundled hash-bound profiles in `profiles/` can fix the rule per source model (Sheepinator, Toxic Crab: SSC0 with evidence in the profile).
- Without a profile, clips whose FULL and SSC0 results diverge are included under **SSC0** (`inheritance_basis=pipeline_default_ssc0`, divergence recorded). Evidence: the eboot reference-pose builder compensates parent scale only for joint-flag bit 0; crab stretch chains; amoeboid FULL shear up to 1.0 versus orthogonal SSC0. See [SSC0Generalization](Research/AnimationCoverage/Inheritance/SSC0Generalization/README.md). The per-frame runtime evaluator is not traced.
- The review camera is fitted to the rest-shape mesh bounds using the camera's limiting field of view (joint motion bounds only for mesh-less rigs).
- Source armatures are saved with ordinary viewport-eye hiding and `hide_render=True`; Actions and modifiers remain active. The Toxic Crab's 36 clips have distinct raw/sample hashes, with one 10-second dance and 35 clips lasting about 0.4–3.5 seconds. Its SSC0 profile does not choose inheritance for future model hashes.
- `-ExperimentalAmbiguousInheritance` admits decoded, source-related clips blocked only because inverse-bind-compatible full and SSC0 interpretations diverge on animated scales; it bakes both labelled experimental Actions per clip (SSC0 preferred preview; neither proven). Catalog, Action metadata and `limitations.json` keep source name, hashes, rate/range, both rules, divergence, class and uncertainty. Each ambiguous clip adds two timeline entries; other failures stay inventoried.

```powershell
.\Run-PS4AnimationPipeline.ps1 -ExtractedRoot $assets -OutputRoot $output `
  -Model 'exact model selector' -ExperimentalAmbiguousInheritance
```

- Inheritance cross-check: `Animation/Audit/world_rule_comparison.json` (adapter vs `worlds(..., 'segment_scale_compensation_bit0')`: max `8.5265128e-14` over 6,924 samples; full inheritance up to `365,353.3723`). `Animation/Audit/rest_order_comparison.json`: `T * R * S` fits all ten complete animated pairs' inverse binds within `2.7340215e-6` (compatible convention, not unique engine semantics).
- Animation-fidelity audit (2026-09-16, report `Animation/Audit/animation_fidelity_audit.md`): the decoder passes 192 clips / 6,924 samples across nine source pairs; `anim_clip_built +0x08` bit `0x20` correlates with the 13 exclusive-duration clips (five Protosuit hydropack pose exceptions: not universal); Ratchet's 520 clips classify 415 partial absolute and 105 relative/additive (bit `0x40` on exactly those); path sections are not assigned (root matches both endpoints in 10 of 192). Fixed the Protosuit NLA defect via `nla_sample_bounds()` in `timeline.py`. Runtime scale-compensation flags, additive graphs, path/root, timing flags and subframe interpolation remain unproven.

## Result statuses

`COMPLETE_MESH_MATERIALS_ANIMATIONS`, `COMPLETE_MESH_ANIMATIONS_MATERIAL_LIMITATION`, `COMPLETE_MESH_ANIMATIONS_TEXTURE_LIMITATION`, `ARMATURE_ANIMATIONS_ONLY`, `UNSUPPORTED`, `FAILED`.

A native model with present but malformed/undecodable mesh data is `FAILED`, never downgraded to armature-only; `UNSUPPORTED` is for assets lacking the supported PS4 model sections. Zero supported animations is reported as zero.

## Validation evidence

Real archive-to-Blender integrations (run folders kept as evidence; `TestOutput` was deleted 2026-10-06, test inputs are in `test_fixtures/`):

- `NativeExample/wpn_sheepinator_3c094b58/wpn_sheepinator.blend`: 3 meshes, 8,990 used vertices, 13,660 triangles, 3 materials, 10 textures, 10 joints, 3 clips/73 samples; hash-bound recipes, upright presentation, Novalis probe and all Action/NLA samples pass after reopen (max matrix error < `7.75e-7`); the game probe cut the file from ~99 MiB to ~9.54 MiB.
- `HeroExample/hero_ratchet_3c43f340/hero_ratchet.blend`: actor model plus six config-referenced base parts, 14 meshes, 19,981 vertices, 34,762 triangles, 14 materials, 198 joints, two exact Actions; other clips and 56 alternate parts reported individually. Unsupported R8G8_UNORM detail-normal DDS is decoded directly (exact R/G, reconstructed Z, verified row orientation, packed PNG; original DDS/hash kept).
- `OtherCharacterExample/enm_evil_zurkon_2e3eaa7b/enm_evil_zurkon.blend`: 29 joints, three verified Actions.
- `PropExample/design_laser_gate_ff1d4836/design_laser_gate.blend`: static prop, 1 mesh, 121 vertices, 200 triangles, 1 material, 5 textures, no invented armature.
- Geometry-only native proofs (Sheepinator, Laser Gate, Protosuit body, colour proof `gbl_prop_qwark_clock_01`, high-flag `met_amb_antenna_01`): zero materials/lights/cameras/actions; run output deleted 2026-10-06, rerun `build_native_geometry_proofs.py`.

Native Sheepinator vs prior FBX: positions within `9.57e-8`; topology and skin weights exact; UV0 exact on two subsets, one half-float step on the third; the fresh 1024x1024 colour DDS matches the archived DDS pixel-for-pixel.

Validation also checks mesh/material/image presence, source hashes, packed images, normalized in-range weights, scene defaults, perspective camera, transparent RGBA, Actions, markers, NLA boundaries and every admitted animation sample. Generated PNGs are for manual inspection only.

### Regressions

`test_pipeline.py` uses `TestOutput/tmp` as scratch. Blender tests need an `--out`/`--blend` scratch location of your choice (below `<scratch>`).

```powershell
$py = 'C:\Program Files\Blender Foundation\Blender 5.0\5.0\python\bin\python.exe'
$blender = 'C:\Program Files\Blender Foundation\Blender 5.0\blender.exe'
& $py -B -m unittest Animation.pipeline.test_pipeline -v
& $blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python Animation/pipeline/test_blender_timeline.py -- --out <scratch>/boundaries
& $blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python Animation/pipeline/test_blender_geometry.py -- --out <scratch>/geometry
& $blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python Animation/pipeline/test_blender_affine.py -- --out <scratch>/affine
& $py -B -m Animation.pipeline.audit_native_geometry
& $py -B -m Animation.pipeline.audit_native_archive_sample
& $blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python Animation/pipeline/build_native_geometry_proofs.py -- --mode build --model Animation/pipeline/NativeExample/decoded_sources/3c094b58296d3e44c564/equipment/weapon/wpn_sheepinator/wpn_sheepinator.model --blend <scratch>/wpn_sheepinator_native_geometry_proof.blend --report <scratch>/wpn_sheepinator_build.json --lod All
& $blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python Animation/pipeline/build_native_geometry_proofs.py -- --mode validate --blend <scratch>/wpn_sheepinator_native_geometry_proof.blend --report <scratch>/wpn_sheepinator_reopen_validation.json
```

Two-process proofs (run `--phase build` then `--phase validate` in separate Blender processes, same arguments):
- `test_blender_external_hdri.py -- --phase <p> --blend <scratch>/external_hdri_storage_proof.blend`: the reopened EXR resolves externally with dimensions loaded while a packed texture stays packed; one 64x64 Cycles render.
- `test_blender_review_lighting.py -- --phase <p> --blend <scratch>/novalis_fixture.blend --probe <scratch>/game_probe_fixture.png --preview <scratch>/preview.png --evidence <scratch>/evidence.json`.

Pure regressions cover archive bounds/duplicates, outer LZ4, path safety, cache/overwrite guards, reference resolution, transform inheritance, classification, NLA timing, native section/mesh/UV/skin/look decoding with fail-closed guards, 1/2/4/8 skin layouts, packed normals, DDS headers, graph families, hash-bound profile selection and both light-grid path layouts. Blender tests cover timing boundaries, affine animation, FBX registration, native-vs-FBX comparisons, geometry-only reopen, texture pixels, recipe persistence, upright presentation and game-lighting integrity.

## Source-bound Proton presentation delivery (2026-09-27)

The Proton V1 pad flow produces a second, separate presentation-tuned blend `final/Proton_Drum_V1_V5_presentation_tuned.blend` (receipt and reopen report under `variant_assemblies/proton_drum/presentation/`) after the native assembly passes its receipt and fresh-process gate; `Proton_Drum_V1_V5_switchable.blend` is untouched. The receipt pins native blend and assembly receipt hashes, source-structure profile/reference hashes, apply/reopen scripts, `run.py`, source model IDs/hashes, polygon-used material identity and C/G/N transport identities; a cache hit also checks final/report hashes, fingerprint, `presentation_tuned` / `game_equivalence=false` labels and V1/V5 PNG hashes. Identity mismatch fails closed for the optional delivery only.

Profile: mix 0.18, `_g` RGB saturation 2 only on the Principled specular-tint input, Khronos PBR Neutral / None / exposure -0.5 (a per-model presentation default, not a shader rule; pre-2026-10-06 recipe). A full selected-model run passed (native assembly SHA-256 `fa9a598fba1bdaca39f645d835324f848d92da5d414e36f2efddfdad07d3fc45`, presentation SHA-256 `496299fdc1f38d9d78777f9274240163b5b117cfd53a17d5b2f9116c510589ae`; run output deleted 2026-10-06); `--reuse-cache` reuse emitted `reused_verified_delivery`, `proton_drum_assembly_reused`, `proton_drum_presentation_reused`. The pinned baseline state is `Research/Materials/ProtonWashout27/source_state_reference.json`; the production stage checks output-root manifests, exact source structure and this reference, not any historical application receipt.

## Archive loading speed (2026-10-09)

- Raw LZ4 blocks (outer assets and texture streams) use the optional `lz4` package (C, `pip install lz4`; installed in the GUI's Python 3.14) when it is importable. The pure-Python `_lz4_block_python` stays as the fallback (Blender's bundled Python has no `lz4`) and as the reference: 2,044 sampled assets of all 23 types plus 150 texture loads (479 MB) decode byte-identically, and the 208 streams the Python decoder rejects are rejected by both. Any C failure or length mismatch falls through to the Python decoder.
- `Archive()` caches the parsed `layout.csv` in `shared_index/layout_<key>.pickle` (~60 MB). The key covers the SHA-256 of `layout.csv`, `toc`, `dag` and `chunkmap.txt` and of the parser source (`archive.py`, `common.py`), so editing either rebuilds it once; the cache is ignored if any archive file's presence changed. Layouts under 5,000 entries (test fixtures) are not cached. Delete the pickle to force a re-parse.
- `decode()` cache hits compare a `[size, mtime_ns]` stamp recorded in `source_mapping.json` (`decoded_stat`/`compressed_stat`) when the file's SHA-256 was last verified. Any difference falls back to the full hash, which refreshes the stamp. Source bytes are still read and hashed from the archive every time.
- Large archive files (≥ 16 MB) stay open per thread in `Archive._read_range` instead of being reopened for every asset. Smaller files, such as test fixtures, are still opened and closed each time, so their folders can be deleted. `Archive.close()` releases the calling thread's handles.
- `checkpoint()` rewrites `source_mapping.json` only when the mapping changed: a new decode or a refreshed stamp.
- `write_split_index` reuses the shared assets table's content hash through `shared_index/rows_<identity>.txt`. The identity is `Archive.layout_identity` (layout cache key, archive availability and loose files) plus the `index.py` source hash. It leaves `asset_references.sqlite` and `asset_index.json` untouched when their contents are unchanged. Three consecutive scans leave `asset_index.json`, `asset_references.sqlite` and `source_mapping.json` byte-identical.
- The GUI's `PipelineIndexScanner.scan()` writes `reports/gui_scan_stamp.json`. It records every file under the extracted root (size and mtime), the hashes of `run.py`, `archive.py`, `index.py` and `common.py`, and the size and mtime of the index outputs. If everything matches, the previous `gui_model_index.json` is reused without running the CLI, and the log says so. Any difference, including another run rewriting the references, runs the CLI. `scan(reuse=False)` forces a run.
- Measured on CUSA01073: GUI/CLI model-list scan 15.8 s → 1.5 s with the CLI, 0.1 s when reused (actor-cache re-hashing was ~10 s, `layout.csv` parsing ~4 s); `Archive()` 2.0–4.1 s → 0.5 s.

## Provenance

Archive framing follows [ig-tools](https://github.com/doesthisusername/ig-tools) at `b4ebe28a926cf298bff70ad01cdf9e14533c5b94`. PS4 mesh/material/skin records come from the archived `ratchet_and_clank_ps4.ms`, corroborated by local recovery scripts and matching FBX assets. [RCRA-Forge](https://github.com/ROStudios25/RCRA-Forge) at `406a315f6e316ffc5b8931890f67b794d95d9708` is a structural reference only (PC layouts not assumed compatible). The 8x8 PS4 block ordering is adapted from MIT [pyswizzle](https://github.com/Aclios/pyswizzle), verified against an independently converted game DDS.
