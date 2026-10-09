# R&C (PS4, 2016) asset browser and model reconstruction — project constitution

This folder holds two things: the **R&C 2016 Asset Browser** (a Windows viewer/exporter that reads the game archive directly) and the older **Blender model-reconstruction** work (per-model master scenes, renders, research evidence). Most rules below are about the second; the asset browser's own rules are in the next two sections.

## Asset browser

- Entry point: `Animation/rc2016_asset_browser_gui.py` (desktop shortcut `R&C 2016 Asset Browser.lnk`). Standalone level viewer: `Animation/pipeline/Research/AssetBrowserReview/level_viewer.py` (`R&C 2016 Level Viewer.lnk`). Launch with the installed Python 3.14 (`C:/Users/Tim/AppData/Local/Programs/Python/Python314/python.exe`), not Blender's.
- Viewer code lives in `Animation/pipeline/Research/AssetBrowserReview/` (`browser_ui.py`, `viewport.py`/`gl_shading.py`, `texture_preview.py`, `level_*.py`, `flash_tab.py`/`gfx_render.py`, `model_export.py`). Its `README.md` is the feature log and the reference for viewer behaviour and material rules; read it before changing the viewer. Level decoding is in `Animation/pipeline/Research/Levels/`.
- The archive scan and the optional "Full research Blender scene" export run `Animation/pipeline/run.py` as a subprocess; portable exports (GLB/glTF/OBJ, then FBX/USD/`.blend` through `blender_convert.py`) run in-process. Blender stays optional for browsing and portable export.
- The validated source root is `C:/Users/Tim/Documents/Bestanden/Downloads/Ratchet & Clank (EUR)/CUSA01073`. Settings persist in `%APPDATA%/PS4 Animation Extractor/settings.json`.
- Tests: the command in the root `README.md`, run from the project root. `smoke_gui.py`/`smoke_level.py` drive hidden windows against the real archive and are local-only.

## Git repository

- This project folder is a git repository, pushed to GitHub as `tvh92/rc2016-asset-browser` (private). It tracks **only the asset browser**: its code, the pipeline code it runs, small config/profile JSON, the files `run.py` hashes or reads, docs, and two vendored third-party pieces under `Animation/pipeline/references/` (ALERT `dat1lib`, GPL-3.0; opengnm `gcn_types.h`, MIT).
- `.gitignore` is an **allow-list**: everything is ignored unless it is listed. Never commit game data (`_Extracted PS4 files/`, DDS/textures, `.model`/`.animset`, extracted archives), Blender files, renders, proof images, reports, logs, research outputs or caches. Do not weaken the allow-list with broad patterns like `!*.py` or `!*/`: it keeps `git status` instant and stops gigabytes of research output from being picked up.
- When the browser starts importing, launching, hashing or reading a new file, add it to `.gitignore` (`!/path/file`; a new folder also needs `!/dir/` + `/dir/*`) in the same change, and check `git status` shows it. Before pushing, check that a fresh clone still imports and passes the tests: a missing allow-list entry only shows up there.
- `.gitattributes` sets `* -text`, so files are stored byte for byte. `run.py` hashes its own source files, so do not turn on line-ending conversion.
- The user leaves git management to agents: after a browser change has passed its tests, commit it as a focused commit with a clear message and `git push` (no force-pushes or history rewrites). Check `git status` at the start of a task, so you can tell your changes apart from the user's. The local git identity is set for this repository only and uses the GitHub noreply address.

## Usage governor

Treat agent usage (Codex or Claude) as limited: remove wasted agent work, not necessary reasoning or local Blender computation. Read the relevant model `README.md` first and reuse its validated mappings, settings, evidence, and scripts. Reason deeply but narrowly about the important uncertainty; prefer targeted inspection, deterministic scripts, batching, and reuse. More reasoning up front is worthwhile when it prevents incorrect edits, repeated experiments, or long tool loops. Use direct Blender data/assertions for structural questions and cheap low-resolution proof renders for visual questions. Diagnose failures before retrying; do not repeat equivalent experiments without new evidence. Stay within scope, record low-impact uncertainty, investigate deeply when correctness or future rebuilds depend on it, and stop once the requested result is correct and validated. Do not impose arbitrary token, tool-call, launch, reasoning-time, or render limits.

## Source and texture rules

- Preserve raw FBX/DDS assets and build each scene from that model's own sources; never substitute similarly named textures. Keep ordinary inventory facts in `MODEL_INVENTORY.txt`.
- Read the model README before editing; it overrides this file. Keep validated model exceptions, mappings, case history, and recovery procedures in that README, not here.
- Set DDS images to `Alpha Mode: Channel Packed` before judging colour; alpha is always linear data. Texture colourspace follows the **native DXGI format**: sRGB formats 29/72/75/78/91/93/99 → sRGB, everything else Non-Color; normals are always Non-Color. `_c` is normally BC7 sRGB base colour and `_g` normally BC7 sRGB coloured specular F0 with gloss alpha (the pipeline's `materials.py` applies this). `_c` alpha is self-lit mask data, never ordinary transparency. `_n` is a DirectX tangent normal, strength `1.0`; invert green only after a lighting test.
- Opaque surfaces use the game's deferred BRDF, the **RC PS4 Deferred BRDF** group from [`Claude_shading_fix/rc_game_shader.py`](Claude_shading_fix/rc_game_shader.py): diffuse = `_c`; specular = GGX with Schlick Fresnel, F0 = `_g`, F90 = sat(60·F0); roughness = `2^(-2.75·gloss)`; the two are added, not mixed; self-lit = `_c.rgb × _c.a × K`, with K from material key `0x66EFA55B`. Metallic stays `0` and no invented metallic masks or strong AO are added; preserve UV orientation. Do not reintroduce `_c × (1 − _g)`, the 18% Principled mix, `1 − gloss` roughness floors, emission thresholds or Hue/Sat "colour tuning" for new work. Glass, energy and VFX materials are forward-shaded and not covered yet; keep their documented model recipes.
- Read the capture's SPIR-V (RenderDoc) before attempting GCN microcode tracing for any shading or lighting question. [`Claude_shading_fix/README.md`](Claude_shading_fix/README.md) has the tooling, the capture facts and the validated values.

## Assignment and shader preflight

- FBX names, material slots, polygon indices, and labels are hypotheses. Before rendering, audit object role, actual polygon-used slots, UV layers/ranges, texture dimensions, and candidate atlas details; compare unchanged geometry/UVs with validated base/V1 components first. Search original archives for genuinely referenced shared textures and record confirmed versus provisional mappings in a repeatable script or README.
- Never blanket-apply an atlas shader, infer identity from names alone, or use a render to discover a basic mapping error. Check every polygon-used material, including hidden V1/V5 variants, glass, and effects. Keep baseline and experimental materials separately recoverable with fake users; verify after reopening. Check alpha bounds, not only image dimensions.
- When source geometry appears missing, verify original model section counts before fabricating replacements: FBX exports can omit sections. The Fusion Grenade README and recovery script document a validated example; do not assume its binary layout applies elsewhere.
- Convert with `rc_game_shader.convert_all()` (it keeps a fake-user `| pre-RC-game-shader` backup of each material; `restore_backups()` undoes it), passing per-material K from the pipeline reports (`emissive_constants_from_reports`). Never infer correctness from node names or a plausible render: assert the group's inputs, image colourspaces and links through the active Material Output after reopening.
- Existing delivered masters built with the older `_c × (1 − _g)` + Principled presentation recipe stay valid as presentation renders until they are deliberately converted. Their model README factors remain the reference for those files only.

## Weapon version assembly

- Only weapons have V5 models. Use one master scene for V1/base and V5, sharing camera, lights/world, render settings, materials, grip, bulb, and unchanged components. Keep clearly named switchable V1/V5 collections; merge partial V5 barrel/upgrade packages with required base parts and apply version atlases only to matching geometry.
- Never use monitor Disable, view-layer exclusion, or `Collection.hide_viewport`/`Object.hide_viewport` to switch variants. Use ordinary eye hiding (`hide_set`/LayerCollection eye) and `hide_render`; keep collections enabled and restore intended eye/render states before saving. Nested base geometry is acceptable when enclosed by a V5 shell; audit data users before extracting duplicates.

## Shared Blender and delivery defaults

- For new scenes use [`blender_defaults.py`](blender_defaults.py) and `apply_new_scene_defaults()`; use `audit_scene()` read-only before/after edits. Do not edit global Blender preferences. Missing Cycles or the HDRI must fail loudly.
- Use Cycles on the RTX 3060 OptiX GPU with CPU disabled.
- **Lighting default — shared review lighting:** defined once in [`shared_lighting.json`](shared_lighting.json) beside this file; edit that file to replace the lighting for every model. Currently: sole lighting `golden_gate_hills_1k.exr` (6 MB, 4×4 box-filtered float copy of the original `golden_gate_hills_4k.exr`; same lighting within render noise) at world strength `2`, no separate lights, view **Khronos PBR Neutral**, look None, exposure **−1.0**, compositor off. `apply_new_scene_defaults()` / `apply_shared_lighting()` and the pipeline (`--lighting-source hdri`, the default; `--lighting-config` for another file) apply it and record it on the scene (`scene["shared_lighting"]`); `check_shared_lighting()` and the validators compare against that record, never hard-coded values. `audit_scene()` reports `lighting.differs_from_current_shared_file` for scenes built with older lighting. Never call these "game accurate". Existing masters keep their colour management unless deliberately rebuilt; a model README may document a different exposure only with a stated reason. Keep `golden_gate_hills_4k.exr` for older pinned scenes and profiles.
- **Game-look renders (opt-in only):** the captured Veldin look ([`Claude_shading_fix/render_game_look.py`](Claude_shading_fix/render_game_look.py), pipeline `--lighting-source veldin`) reproduces the capture's key light, ambient, exposure (+2.19 stops) and tonemap correctly, but on standalone models it washes out (camera-side unshadowed sun pushes most surfaces into the tonemap shoulder). Use it only when asked; it is not the delivery default. Light strength = π × game light colour; the captured light direction is game-camera relative and is not used.
- Defaults: `3840 × 2560` at `100%`, PNG RGBA, transparent film, adaptive sampling/denoising, `96` samples, `32` preview samples, adaptive threshold `0.04`, 5 total bounces (2 diffuse, 2 glossy, 4 transmission, 6 transparent, 0 volume), Light Tree, and Persistent Data. Use 25–50% proofs after material, collection, or framing changes; use higher samples only when reflective/glass noise is evidenced. More samples do not fix wrong assignments or masks.
- Use perspective cameras; do not retain orthographic render cameras unless requested. Preserve the latest saved camera's transform, projection, lens, shift, constraints, framing, and Camera-to-View lock. Never rebuild from hard-coded coordinates or an older scene. Verify transparent and intended partial-alpha pixels, restore final visibility/resolution, pack the HDRI in hand-delivered masters (pipeline builds link the shared file by absolute path), and distinguish `.blend1` backups from the delivered master.

## Source animations

For scene-building tasks, check the model's original actor/model references and available animation sets. Include its applicable supported source clips as persistent, selectable Blender actions by default; follow [`Animation/ANIMATION_WORKFLOW.md`](Animation/ANIMATION_WORKFLOW.md) and reuse the root sampler, Blender adapter and embedded controls. Validate binding and representative deformation per new rig, preserve source timing and presentation cameras, and verify actions after reopening. Record included, unrelated and unsupported clips with reasons; never silently omit animations or substitute authored poses for undecoded samples. Keep model-specific bindings/variant switching in its README and build script. Do not expand the task into decoding unrelated codecs or game VFX.

## Model documentation map

Load only the relevant README for detailed assignments, exceptions, tuning, histories, and scripts. Legacy FBX-era READMEs live under `Animation/pipeline/evidence/legacy/` (abbreviated `legacy/` below) at their original relative paths:
- `legacy/Gadgets and items/Trespasser/README.md`
- `legacy/Weapons/FINAL/Combuster/README.md`
- `legacy/Weapons/Fusion Grenade/Fusion Grenade_archive/README.md`
- `legacy/Weapons/FINAL/Proton Drum/README.md`
- `legacy/Weapons/FINAL/Pyrocitor/README.md`
- `legacy/Weapons/FINAL/Sheepinator/README.md`
- `Other models/enm_toxic_crab/README.md`
- `Other models/enm_puffer_fish/README.md`
- `Other models/enm_blarg_space_commando/README.md` (decoded `basic_normal_gloss` / `coloredgloss_emissive` / `incandescence` writers; native `character_glass` decode)
- `Other models/enm_telepathipus_v2/README.md` (per-model position shift `0x283D0383`, skin joint remap `0x5240C82B`, unset material slots)
- `Other models/enm_blarg_saucer/README.md` (colour-pick and saucer-glass decodes used by the viewer)
- `Other models/random_enemy_pipeline_20261006_213930/README.md`, `Other models/translator_amoeboid_20261006/README.md` (pipeline generalisation runs)

Other legacy model READMEs (under `legacy/Weapons/FINAL/<model>/` and `legacy/Gadgets and items/<model>/`) are the local notes where present; they describe FBX-era approximations, so treat their recipes as history and their source relationships as hypotheses to re-verify. Add a README only for meaningful validated exceptions; do not duplicate full model knowledge here.

Folder roles:
- `Animation/` holds the extractor pipeline and the asset browser. Its example runs (`pipeline/*Example`, `ExampleOutput`) are cited evidence; `pipeline/TestOutput` is empty. See `Animation/AGENTS.md`.
- `Animation/pipeline/Research/` holds research folders (decodes, proofs, corpus regressions). Only the files the browser uses are in git; the rest is local evidence.
- `Other models/` holds per-model pipeline rebuilds, decodes and proofs (local only).
- `_Extracted PS4 files/` holds earlier loose extractions and archives (game data; never commit it). `UI icons/` holds gadget icon renders. `build/` is the PyInstaller work folder.
- `Claude_shading_fix/` holds the game-shading and lighting toolkit.
- `Animation/pipeline/evidence/legacy/` holds what survives of the FBX-era `Weapons/` and `Gadgets and items/` folders that the pipeline, its tests or its docs still cite (READMEs, Sheepinator reconstruction/shader traces and source copies, Proton Drum source metadata, Combuster reconstruction, Fusion Grenade section-recovery scripts). `shader_translator.py` and `run.py` read from it; do not move it.
- `Reference captures/` holds the user's in-game screenshots and reference images from those folders, at their original relative paths. They are not reproducible; keep them.
- `_Archive/` holds reversible cleanup moves; each batch has a `moves.txt` listing the original paths. `_Archive/2026-10-08_legacy_fbx_models/` holds the FBX-era masters, renders, FBX/DDS exports and source copies; its `INVENTORY.md` says what is safe to delete and what still lacks a pipeline replacement.
