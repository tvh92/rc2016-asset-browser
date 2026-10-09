# RC PS4 game shading toolkit

Scripts that reproduce the Ratchet & Clank (PS4) deferred shading, lighting and tonemap in Blender, using values read from the shadPS4 RenderDoc capture instead of hand-tuned presets. The equations and the reasoning behind them are in [SHADING_LIGHTING_AUDIT.md](SHADING_LIGHTING_AUDIT.md).

## Files

| File | Run with | Purpose |
|---|---|---|
| `qrenderdoc_run.py` | qrenderdoc `--python` | Wrapper that runs a RenderDoc script with arguments and a `<script>.log` file. |
| `renderdoc_dump_frame.py` | via `qrenderdoc_run.py` | Dumps textures, buffers and push constants at the lighting and composite events. |
| `renderdoc_dump_material_cbs.py` | via `qrenderdoc_run.py` | Dumps each G-buffer draw's fragment constant buffers and texture hashes (matches `.material` keys to CB offsets). |
| `dump_material_values.py` | Python 3 | Prints a `.material` instance value section as floats, for comparison with the CB dump. |
| `build_rc_game_view.py` | Blender's Python (numpy) | Builds the 1D/3D LUTs and `rc_game_view.json` (exposure, key light, ambient fit) and validates against the captured frame. |
| `fit_capture_ambient.py` | Blender's Python (numpy) | Fits the captured irradiance as `a + B.n`; called by the view build, also usable alone. |
| `install_rc_view.py` | `blender -b` | Adds the OCIO view "RC PS4 Game" in a private copy of Blender's colour config. |
| `rc_game_shader.py` | Blender | Node group "RC PS4 Deferred BRDF" plus conversion helpers. |
| `render_game_look.py` | `blender -b` | Applies shader, Veldin lighting and tonemap to a delivered model blend and renders it. |
| `render_state/` | see its README | Render-state translator: captured blend/cull/depth/sampler state, the per-material flags word (`0xE1275683`), and the Blender applier `render_state.py`. |
| `brdf_check.py` | Python 3 (numpy) | Numerical proof of the lighting BRDF. |
| `materials_srgb_from_dxgi_format.patch` | archived | Pipeline patch (applied 2026-10-06). |

## Rebuild / render a model

Run from the project root `C:\Users\Tim\Documents\Bestanden\Downloads\Models\PS4 models` unless stated.

1. Dump the capture (only needed when the capture changes):

```powershell
cd Claude_shading_fix
$env:RC_HOME = "$PWD"; $env:RC_SCRIPT = "renderdoc_dump_frame.py"; $env:RC_ARGS = "--lighting-shader 540 --composite-shader 6172"
Start-Process -Wait "..\.codex\scratch\renderdoc-noadb-1.45\qrenderdoc.exe" -ArgumentList "--python", "`"$PWD\qrenderdoc_run.py`"" -WorkingDirectory "$PWD"
Get-Content renderdoc_dump_frame.log
```

`qrenderdoc --python` shows no stdout, passes no arguments and defines no `__file__`, hence the wrapper; the output log is `<script>.log`. The capture `%APPDATA%\shadPS4\captures\CUSA01073_capture.rdc` was re-recorded on 2026-09-25, so the shader IDs changed from those in the audit: lighting 532 to 540, tonemap 6386 to 6172, G-buffer writer 6553 to 6335 (identical disassembly). If the capture is replaced again, re-inventory and match shaders by disassembly content, not by ID.

2. Build the view (also writes the capture-ambient fit, `capture_ambient` in `rc_game_view.json`):

```powershell
$py = "C:\Program Files\Blender Foundation\Blender 5.0\5.0\python\bin\python.exe"
& $py build_rc_game_view.py rc_frame_dump --out rc_game_view
```

3. Install the view:

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" -b --factory-startup --python install_rc_view.py -- "$PWD\rc_game_view"
```

4. Extract the model (from the project root). The root MUST be the `CUSA01073` subfolder; the parent contains two archive copies and the pipeline refuses it.

```powershell
.\scripts/run-pipeline.ps1 -ExtractedRoot "C:\Users\Tim\Documents\Bestanden\Downloads\Ratchet & Clank (EUR)\CUSA01073" -Model wpn_sheepinator -OutputRoot "$PWD\Claude_shading_fix\run" -ReuseCache
```

5. Render:

```powershell
$env:OCIO = "$PWD\assets\game_view\ocio\config.ocio"
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" -b "<model dir>\<model>.blend" --python Claude_shading_fix\render_game_look.py -- "$PWD\assets\game_view" "$PWD\Claude_shading_fix\render" 50 96
```

Pass the delivered blend explicitly, not `run\blender\*_candidate.blend`. Output is `render\<model>_game_look.png` (RGBA, transparent film) plus a `.blend` copy. Environment options:
- `RC_SUN_ELEVATION_DEG` (default 45).
- `RC_SUN_AZIMUTH_OFFSET_DEG` (default 30; the sun comes from the camera's side, with the captured colour and strength).
- `RC_KEEP_WORLD=1` keeps the pipeline world.

## Validated results (Veldin capture, 2026-10-06)

- Exposure x4.5538 (+2.187 stops); 1D LUT 256 x R16, 3D LUT 32^3; the OCIO view matches the numpy game tonemap (median 0.0000, max 0.005).
- Composite validation median absolute error 0.0099 (bloom modelled; lens dirt b6 x bloom x buf2[4] and a 3% time-rotated film-grain lerp are not modelled). The captured exposure is the error minimum: 0.021 at x0.95, 0.0099 at x1.00, 0.013 at x1.05.
- Key light colour (5.30, 4.48, 2.95) gives Blender sun strength pi x 5.30 = 16.65. The captured direction is game-camera relative and is NOT used.
- Ambient: frame flag buf0 dword 135 = 0x40 (bit 128 clear), so the game does not use the hemisphere fill C0/C1 in this frame; diffuse ambient = irradiance buffer (binding 12) x AO (binding 10). Fit around Ratchet: c0 about (0.091, 0.074, 0.036), roughly 10x below the unused fill constants (about 1.0). Reflections (binding 11) are not modelled, so reflective dark parts are darker than in game.
- Self-lit: the G-buffer writes MRT1.a = 0.2 * log2(1 + x) and lighting decodes 2^(5a) - 1 = x = K * mask. For the Sheepinator, mask = `_c` alpha (existing body GCN trace, API-2 CB byte 0). K was identified via the capture: the emissive G-buffer shader 9350 (Ratchet) reads K from its material CB dword 5 = 8.0; in `hero_Ratchet_Body.material` the only 8.0 is key 0x66EFA55B, so the K key = 0x66EFA55B. `wpn_Sheepinator.material` has 0x66EFA55B = 5.0, so K = 5. `render_game_look.py` reads it from the pipeline's `materials/*.json` automatically. Confidence: the key-hash identity across materials is inferred from one match (no second material verified yet).
- Pipeline patch applied: `rc2016/formats/materials.py` now picks the colourspace from the native DXGI format (sRGB for 29/72/75/78/91/93/99), so the Sheepinator `_g` (format 99) is delivered as sRGB; re-run validated.
- G-buffer draws in this capture: only 13 (Ratchet + held weapon); the remaining roughly 585 pre-lighting draws are depth/shadow-only.

## Open items

- Render state: `character_glass` takes its blend from the material flags word (the Blarg visor is alpha-over, applied 2026-10-07). The Sheepinator and Combuster glass recipes predate the decode; see [render_state/README.md](render_state/README.md).

### Shader-family pipeline preflight (2026-10-06)

New extraction builds classify each referenced graph by canonical source path **and decoded SHA-256** in `rc2016/shaders/families.py`. Material reports preserve shader stages/support scope and parameter-table offsets, raw instance overrides and graph defaults. `geometry.py` calls `rc_game_shader.apply_preflight_material`; the exact registered skin graph uses its decoded front-end automatically. Unknown unprofiled graphs receive an explicitly labelled source-colour-only preview. Existing validated presentation profiles and thin-glass recipes remain separate. The toolkit code hash participates in the pipeline cache fingerprint.

The Toxic Crab native GCN audit corrected earlier stage assumptions: `7D3F5FA6` and `D7749B42` are vertex shaders; `BED06840` and `AE7244FE` are forward pixel shaders. BED06840's front-end uses a mapped normal, grey specular from `_g.G`, gloss from `_g.R`, and continuous `_c.a` self-lit. The normal is `normalize(2R-1, 2G-1, 1-x*x-y*y)`. Its second-normal blend is zero in this instance; other blend settings remain partial. `apply_native_skin_normal` connects the decoded normal to both the BRDF and the skin group's `Material normal` input. Cached-writer comparisons retain geometry-normal Fresnel.

The full forward shader also samples `skin_ramp.R` and adds a diffuse term tinted by `(0.637597, 0.116971, 0.122139)`. This term and the complete forward light/shadow response are **not** reproduced by the current Cycles deferred BRDF. Recognition therefore remains `supported_partial`, with `game_equivalence=false`. Bounded material/ramp slices have independent 500-case numerical controls each; they do not validate full lighting or runtime selection. Sources, hashes, offsets and remaining work: [`NATIVE_SKIN_DECODE.md`](../Other%20models/enm_toxic_crab/evidence/NATIVE_SKIN_DECODE.md).

Standard deferred families (2026-10-06): `basic_normal_gloss` (writer `5ebdeca4`), `basic_normal_coloredgloss_emissive` (`c4828176`) and `basic_incandescence` (`5dbcccb7`/`42254271`) are registered and applied by `apply_deferred_family`. Parameters are read by key, not dword: spec `0x62835AF9`, gloss `0x24F4CA4C`.
- `basic_normal_gloss` and `basic_incandescence` use `_g.R` = gloss and `_g.G` = grey spec. Only `coloredgloss_emissive` matches the generic `_g` RGB-F0/alpha-gloss group.
- Self-lit K is per graph. `coloredgloss_emissive` uses `0x06F19114`, not `0x66EFA55B`; incandescence uses `0x5FD56522` × `_g.B`.

Evidence: [`../Other models/enm_blarg_space_commando/evidence/DEFERRED_FAMILIES_DECODE.md`](../Other%20models/enm_blarg_space_commando/evidence/DEFERRED_FAMILIES_DECODE.md).

`character_glass` (Blarg visor; also the Sheepinator glass graph, whose hash-bound profile still takes precedence) is decoded from its native forward pixel shader `34D5B21C`, because it has no cached SPIR-V. `apply_character_glass` builds `Transparent + alpha × (Diffuse(B·(1+k·F·T·E)) + GGX(tint))`, with a Fresnel-curve alpha. The additive blend is taken from the captured `character_glass_02` draw. Evidence: [`../Other models/enm_blarg_space_commando/evidence/glass/CHARACTER_GLASS_DECODE.md`](../Other%20models/enm_blarg_space_commando/evidence/glass/CHARACTER_GLASS_DECODE.md).

Do not infer that an entire material family is solved from one writer or a plausible render. Extend the registry after decoding and validating each distinct graph implementation; source-identical graphs reuse the interpretation while instance values remain material-specific.

- Verify the K key on a second emissive material. Partly done offline: the Toxic Crab's `skin_emissive` graph's cached G-buffer writer `0x567fe897` reads K from the CB dword whose graph-table key is 0x66EFA55B (crab K = 2.0).
- Graph-specific G-buffer front-ends: `_g` is not coloured F0 in every graph. `skin_emissive` uses `_g.R` = gloss and `_g.G` = grey specular with Fresnel-boosted gloss/spec and a Fresnel albedo tint (`rc_game_shader.apply_skin_emissive`; equations and parameter-table parsing in `Other models/enm_toxic_crab/README.md`). Check a graph's cached SPIR-V (via `.materialgraph` `OrbShdr` IDs, `Animation/pipeline/Research/Shaders/SourceGraphRuntimeJoin`) before converting a new graph family. `convert_all()` now skips materials that are already converted (pass `reconvert=True` to force).
- Model reflections (binding 11).
- A capture with the Sheepinator equipped would confirm K = 5 and the glass/energy forward shaders.


## Lighting default changed to the neutral HDRI (2026-10-06)

The Veldin look is **opt-in**, no longer the pipeline default. It is numerically faithful: a fit of the captured lit buffer (event 5256) as `albedo × (key × N·L + irradiance × AO)` over 254k G-buffer pixels gives key scale 0.985 / 1.063 / 1.098 (R/G/B), so π × colour and the fitted ambient are correct and exposure is applied once. On a standalone model, however, the camera-side unshadowed sun puts most surfaces at high N·L, where the tonemap shoulder flattens colour (18% grey at N·L = 1 → sRGB 0.84). The Alien Spitter came out pink/cream instead of purple/teal (`Other models/random_enemy_pipeline_20261006_213930/corrections/albedo_clamp_comparison/`).

The default is now the shared review lighting in root `assets/lighting/shared_lighting.json` (`assets/lighting/golden_gate_hills_1k.exr` ×2, Khronos PBR Neutral, exposure −1.0), applied by `rc2016.pipeline.scene_defaults.apply_shared_lighting`. The 1k file is a 4×4 box filter of the 96 MB 4k original, float RGB (the sun reaches ~9e4, beyond half float), 6 MB; lighting energy is within 0.01% and 25% renders of the Spitter, Sheepinator and Blarg match the 4k within noise (mean |Δ| ≈ 0.005, mean colour within 0.001): `hdri_1k_check/`. The −1.0 exposure was picked from a −0.5/−1.0/−1.5 ladder on those three models.

## Skin albedo clamp (2026-10-06)

The skin front-end albedo `_c × lerp(P5, tint, F)` is clamped to [0,1] (`SKIN_GROUP_VERSION` 3), because MRT1 is `R8G8B8A8_SRGB` UNORM in the capture; P5 is 1.4 on the Spitter, so about 5% of its `_c` texels exceed 1. The lighting pass multiplies self-lit by the same stored albedo. Visual effect on the Spitter is negligible (A/B and C/D in the comparison folder). `shader_validation.py` asserts the clamp, so blends built before this fail re-validation until rebuilt.

## Compositor game view (2026-10-06)

`game_view_compositor.py` rebuilds the "RC PS4 Game" view (exposure → lg2 [-8,+5] → `rc_tonemap.spi1d` → clamp → sRGB → `rc_grade.cube`) as compositor nodes with packed float LUT images sampled by Map UV. Scenes use view transform `Raw`, so no OCIO environment or `Launch-Blender-RC-View.bat` is needed. `test_game_view_compositor.py` checks it: 1.6e-6 against the same chain with a trilinear cube; max 0.0035 (mean 1e-4) against the OCIO view, whose cube is tetrahedral. The game samples its grade as a 3D texture (trilinear). `game_look.apply_veldin_look()` and the opt-in pipeline mode `--lighting-source veldin` use it. Map UV reads the UV's Z as a coverage mask, so it is set to 1.
