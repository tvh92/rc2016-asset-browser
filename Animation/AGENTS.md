# PS4 model and animation extraction

These instructions apply to everything under `Animation/` and supplement the
repository-level `AGENTS.md`. Read `pipeline/README.md` and the selected model's
README before changing extraction, reconstruction, materials or animation.

## Objective

Maintain a generic Windows pipeline that turns the game's original `.model`,
`.animset`, material, texture and lighting assets into portable Blender files.
Each output should contain the decoded native geometry when supported, source
materials and textures, selectable source Actions, an upright Z-up presentation
and source-derived lighting. Preserve exact source bytes and hashes beside every
interpretation. Never present an inferred shader or lighting field as decoded.

The GUI (`rc2016_asset_browser_gui.py`, the R&C 2016 Asset Browser) browses
and filters the archive's models, levels, textures and Flash UI files, shows
them in an interactive OpenGL view, and exports one or several models as one
batch with live progress, cancel and resume. Blender is optional. Keep the CLI as
the backend and test both entry points; the GUI must not depend on PowerShell.
Viewer details are in `pipeline/Research/AssetBrowserReview/README.md`; git
rules for this code (allow-list `.gitignore`) are in the root `AGENTS.md`.

## Working rules

- Reuse validated hashes, mappings, material profiles, coordinate transforms and
  animation samplers. Keep raw assets read-only and output into a separate root.
- `pipeline/NativeExample`, `HeroExample`, `ExampleOutput`, `PropExample` and
  `OtherCharacterExample` remain as evidence; do not use them as scratch space.
  Use a new output root for destructive rebuilds. `TestOutput` is empty;
  `pipeline/test_fixtures/` holds the test inputs (local only, not in git).
- Keep native Y-up mesh, skeleton and animation values for numerical checks.
  Apply the verified `(x, y, z) -> (x, -z, y)` presentation basis to displayed
  objects and assert it after reopening.
- Build candidate `.blend` files, reopen them in a separate Blender process,
  validate source hashes, structure, materials, packed images, actions and
  lighting, then promote atomically. Renders need manual visual review; image
  statistics are integrity evidence only.
- Do not use reference-image brightness or Blender exposure guesses as recovered
  game data. Record diagnostics separately from canonical outputs.
- The Novalis RGB96 Sun option uses the exact atmosphere ray, RGB96 magnitude and
  angular-radius candidate without float84/float156/exposure scaling. Direction
  is decoded; the RGB96 runtime field name is pending.
- Diagnose a failed run from its log before retrying. Keep useful rejected
  hypotheses in reports so later work does not repeat them.

## Shading correction — 2026-10-06

- The shadPS4 RenderDoc capture's SPIR-V supersedes the GCN-microcode inference
  work for the lighting equation. `c*(1-g)` and the 18% mix are not the game
  equation: diffuse `_c` plus GGX/Schlick specular (F0 = `_g`, roughness =
  2^(-2.75*gloss)), simply added; see node group "RC PS4 Deferred BRDF" in
  `Claude_shading_fix/rc_game_shader.py`.
- Texture colourspace comes from the native DXGI format (sRGB for
  29/72/75/78/91/93/99, else Non-Color; normals always Non-Color);
  `pipeline/materials.py` is patched. `_g` is BC7_UNORM_SRGB (99), so sRGB.
- Self-lit = `_c.rgb * _c.a * K`, K from material key 0x66EFA55B (Sheepinator 5.0).
- The captured exposure and tonemap (x4.554, "RC PS4 Game" OCIO view) are
  recovered data, so the exposure-guess rule above does not forbid them.
- Read `Claude_shading_fix/README.md` and `Claude_shading_fix/SHADING_LIGHTING_AUDIT.md`
  before any material or lighting work. Older Class-4 `c*(1-g)` notes are history.

## Requested feature checklist

Sheepinator trace evidence (use its README's opening correction and
`Reconstruction/shader_binding_trace.json`, not the old `shader_trace.json`):
body API slots 5/6/7 = instance `_g`/`_n`/`_c`; API 33 in the 1052-byte variant =
`detail_dents`. `native_cloud_verification.json` shows the native RGBA8 cloud is
non-white and reproduces the pipeline DDS; the white legacy export is not game
behaviour (its export failure and orientation are unresolved). Unchanged control
blend: `_Archive/2026-10-06_obsolete_research/Animation/pipeline/AuditTemp/SheepinatorMaterialInvestigation/Sheepinator_canonical_binding_control.blend`.
Energy: `energy_uniform_trace.json`, `energy_full_uniform_trace.json` and
`energy_joined_trace.json` show the API-0 byte-396 scalar reaches the energy
sample coordinates but has no direct path to export; its time/opacity meaning and
alpha equation are unresolved, and the internal-energy material instance has no
scalar override (a runtime constant-buffer capture would be needed).

- [x] Decode native PS4 geometry, UVs, normals/tangents, skin weights, material slots, skeletons.
- [x] Decode observed texture formats, pack images, and automate the hash-bound Sheepinator glass/energy/emission/gloss/transparency recipe (`Reconstruction/material_investigation.json`).
- [x] Audit Sheepinator material records, graph texture tables, shader markers, polygon-used slots and DDS alpha bounds; the GCN scan confirms structure, not parameter data flow.
- [x] Reconcile the GCN scan with `references/opengnm` (`decoder.c`, `microcode.h`, `gnm_shaderbinary.h`); do not reintroduce the false positives from reading raw VGPR indices 104/105 as SGPR/special fields. Trace files: `shader_binding_trace.json`, `body_equation_trace.json`, `body_cfg_trace.json`, `glass_glossiness_trace.json`, `energy_*_trace.json`.
- [x] Compare original, extracted and pipeline Sheepinator energy DDS (container, channels, orientation); native cloud contradicts the white legacy export.
- [x] Evidence-only shader-trace test blend reopened in a second process; never overwrite the canonical animated master for audits.
- [ ] Generalize source-driven material extraction per model: resolve every polygon-used slot to its source material and textures, derive alpha/transparency mode and sorting, preserve gloss and normals, identify glass/energy families, attach compiled-shader inputs where exposed; no manual post-conversion repair for supported models.
- [x] Decode supported animation clips into persistent Actions (source timing, NLA controls, Z-up, reopen checks).
- [x] Windows GUI: archive scan, filtering, multi-select, one batch, live logs, cancel, resumable verified outputs.
- [x] Decode the Novalis atmosphere ray and opt-in RGB96 Sun candidate; validate direction, colour, energy, angle and source hash.
- [ ] Expand validated material profiles across the corpus. Unknown shader parameters, time-driven energy/noise, VFX and runtime tints stay conspicuous limitations, not guesses. Sheepinator: the held-weapon conduit is ambient (`wpn_Sheepinator_Amb`); `proj_sheepinator_beam.material` and the fractal-field material belong to the separate projectile/beam path.
- [ ] Corpus-test GUI/CLI batches (weapons, characters, props, multipart actors, models without animations, unsupported geometry, failed dependencies); one failed model must not discard other results.
- [ ] Optional per-model review controls for clip/frame selection and proof renders, preserving final resolution and visibility.
- Archived (Novalis light-grid irradiance, local probes, atmosphere/runtime field names, promoting the Sun/probe default): see `_Archive/2026-10-06_obsolete_research/`.

## Lighting research

Archived under `_Archive/2026-10-06_obsolete_research/`. Game-look renders use `Claude_shading_fix/render_game_look.py`.
