# R&C (PS4) → Blender: shading & lighting audit

*Claude, 2026-10-06. Sources: the project folder, `Animation/pipeline/*`, the Sheepinator/Pyrocitor/Proton Drum READMEs, and the existing RenderDoc capture disassembly (`renderdoc_inspect/runtime/shaders/fragment_*.txt`).*

## TL;DR

The project never got game-accurate shading because four wrong assumptions are written into `AGENTS.md` as rules, so every Codex session re-applies them. The game's actual equations were already on disk in the RenderDoc capture's SPIR-V (`fragment_532.txt`, `fragment_6553.txt`, `fragment_6386.txt`). Instead of reading those, the agent spent weeks hand-tracing PS4 GCN microcode inside `.materialgraph` blobs.

| # | Current rule (AGENTS.md / pipeline) | What the game does (capture) | Effect |
|---|---|---|---|
| 1 | `_g` is **Non-Color** | `_g` is **BC7_UNORM_SRGB** (DXGI 99), so the GPU linearises it. The Sheepinator `_g` in the pipeline's own manifest is format 99. | Specular is about 2.4× too strong (Sheepinator `_g` mean is 0.405 as read, but 0.170 correctly decoded). |
| 2 | Diffuse = `_c × (1 − _g)` + Glossy(`_g`), mixed 18% into a Principled | Diffuse = `_c`. Specular = GGX with Schlick Fresnel (F0 = `_g`, F90 = sat(60·F0)). The two are simply **added**. | No energy split, no Fresnel on the old Glossy, and the 18% mix has no basis. That is why every model needed hand-tuning. |
| 3 | roughness = `1 − gloss` (floor 0.30) | GGX α² = 2^(−11·gloss), so Blender roughness = **2^(−2.75·gloss)** | Glossy parts render too rough. At the Sheepinator median gloss (0.33) roughness is 0.53, not 0.67. |
| 4 | `_c` alpha > 0.5 → emission strength 1 | Self-lit = `_c.rgb × (_c.a × K)`, continuous, where K is a per-material constant | Hard edges, and the intensity is unrelated to the scene lighting. |
| 5 | Golden Gate HDRI ×2, view transform chosen per model (Standard −0.7, Khronos −0.5, AgX…) | Game lights → **exposure multiplier** → **1D filmic LUT over 13 stops** → sRGB → **3D grade LUT** | There is no consistent "game look". The decoded Novalis probe looked "too dark" only because the exposure and tonemap were never applied. |
| 6 | Sun energy = game colour | The game BRDF omits 1/π, so **Blender strength = π × game light colour** | The sun is about 3× too weak relative to the probe. |

## 1. Project audit (state of the folder)

- **Pipeline** (`Animation/pipeline/`, about 25 modules plus the GUI and PyInstaller exe). The native archive → DAT1 → model/texture/animation decoding is solid and well tested. The texture decoder writes DDS files that keep the native DXGI format; `materials.py` then overrides the colourspace by filename suffix (§2.1).
- **Materials** are built in `geometry.py::_material` as a Principled BSDF plus a "recovered" Diffuse/Glossy branch. Hash-bound `material_profiles/*.json` add per-model tuning: mix 0.18, saturation 1.8/2.0, value 2.8, roughness floors. All of it is labelled "presentation_tuned, game_equivalence false", which is accurate.
- **Lighting**:
  - `lighting.py` and `lighting_blender.py` decode the Novalis reflection probe (BC6H cube, correctly oriented), the light-grid tables and the atmosphere sun direction/colour. That is good work.
  - Delivered masters still use the Golden Gate HDRI.
  - No exposure or tonemap is ever applied.
- **Shader reverse-engineering**:
  - About 40 scripts and multi-MB JSON traces under `Animation/pipeline/evidence/legacy/Weapons/FINAL/Sheepinator/Reconstruction/` decode GCN ISA from `.materialgraph` blobs.
  - These are the *material* (G-buffer write) programs. They can never reveal the lighting equation, which lives in the deferred lighting pass.
  - The traces correctly report that no shader computes `c × (1 − g)`. The conclusion that should follow (the recipe is wrong) was never drawn.
- **Hygiene**:
  - Root-level one-off scripts and `.tmp_list.py` are left over.
  - `golden_gate_hills_4k.exr` is 96 MB.
  - There are duplicate archives (`Weapons/<model>` vs `Weapons/FINAL/<model>`).
  - The evidence/hash gating in AGENTS.md is thorough but very costly in tokens, and it blocks generalisation. For example, the Ratchet `_g` sRGB fact was hard-coded as a one-material exception (`RATCHET_BODY_GLOSS_SRGB`) instead of being recognised as a rule.

## 2. Decoded game equations

### 2.1 Texture decoding (G-buffer writer, `fragment_6553`)

- Binding 4 (BC7 **sRGB**) RGB × material constant goes to MRT2 RGB (`R8G8B8A8_SRGB`) as linear F0.
- Gloss path: `t = sqrt(saturate(gloss² × K_gloss))`, `e = 2^(−11 t)`, a geometric spec-AA widening `w`, then `alpha_out = −log2(1 − w(1 − e)) / 11`. On flat surfaces `w = 1`, so `alpha_out = t`.
- Binding 5 (BC7 sRGB) goes to MRT1 RGB as albedo. MRT1 alpha is the self-lit term, encoded `0.2·log2(1 + x)` (decoded in lighting as `2^(5a) − 1`).
- The normal is BC5 RG with `z = 1 − x² − y²`, then normalised.

Rule: **use the colourspace implied by the DXGI format** (sRGB for 29/72/75/78/91/93/99). `materials_srgb_from_dxgi_format.patch` applies this to the pipeline.

### 2.2 Deferred lighting (`fragment_532`, full-screen pass, event 3187)

With `s = 2^(−11·a)` (`a` = MRT2 alpha):

```
k      = (0.3536·(√s + 1))²            = (α+1)²/8 with α = √s    (Smith-Schlick)
D·G    = s / ((NH²(s−1)+1)² · (NV(1−k)+k) · (NL(1−k)+k))         (GGX, α² = s)
F      = F0 + (sat(60·F0) − F0)(1 − VH)⁵                          (Schlick, F90 trick)
spec   = ¼ · D·G · NL · F · lightColour       (key light; local lights fold ¼ into m_SpecIntensity)
diff   = NL · OrenNayar(σ² = s) · lightColour (A = 1 − 0.5 s/(s+0.33), B = 0.45 s/(s+0.09))
out    = Σspec + (Σdiff + ambientDiff·AO + 2^(5·albedoA) − 1) · albedo + reflections·specOcc
```

`brdf_check.py` (numerical check) confirms this equals textbook GGX/Smith/Schlick with α = 2^(−5.5·gloss) and light irradiance E = π·colour, to 4 significant figures.

Frame constants (buf0 dwords):
- 124–126: key colour.
- 128–130: key direction.
- 132–138: hemisphere fill colours C0 and C1.

Ambient comes from the irradiance and reflection-probe buffers, or from `C0 + C1·n_y` when flag 128 is set.

### 2.3 Final composite / tonemap (`fragment_6386`, event 6372)

```
hdr  = scene × EXPOSURE                          (buf0 dword 1)
u    = clamp(log2(max(hdr, 2⁻⁸))/13 + 8/13)      (13 stops: −8 … +5)
tone = saturate(LUT1D(u) + bloom)                (1D texture, binding 7)
out  = LUT3D(sRGB_OETF(tone)·S + O)              (3D texture, binding 9; S, O = buf2 dwords 5, 6)
```

The exact curve values are still inside the `.rdc`. `renderdoc_dump_frame.py` extracts them.

## 3. What is in `Claude_shading_fix/`

| File | Run with | Purpose |
|---|---|---|
| `rc_game_shader.py` | Blender | Node group **"RC PS4 Deferred BRDF"** implementing §2.1–2.2. The F90 term is exact: the lobe uses F0/F90 and is weighted by F90. `convert_all()` / `convert_material()` rebuild materials and keep a fake-user backup of each original. `restore_backups()` undoes. Glass and energy materials are skipped. Note: the image colourspace change (`_g` → sRGB) applies to the shared image, so a restored backup also sees it. |
| `renderdoc_dump_frame.py` | Python with `import renderdoc` (same as `extract_runtime_inventory.py`) | Dumps every bound texture, buffer and push constant, plus the outputs, at the lighting event and the final tonemap event. |
| `build_rc_game_view.py` | Python 3 + numpy | Turns the dump into `rc_tonemap.spi1d`, `rc_grade.cube` and `rc_game_view.json` (exposure, key light, fills). It then **re-implements the game tonemap on the captured HDR frame and reports the error against the captured final image**. Tested on synthetic data: median error 0.001. |
| `install_rc_view.py` | `blender -b --python … -- rc_game_view` | Copies Blender's OCIO config, adds the view **"RC PS4 Game"** and writes `Launch-Blender-RC-View.bat`. Blender's own install is untouched. |
| `qrenderdoc_run.py` | qrenderdoc `--python` | Wrapper that passes arguments to a RenderDoc script and writes `<script>.log` (qrenderdoc shows no stdout). |
| `fit_capture_ambient.py` | Python 3 + numpy | Fits the captured irradiance as `a + B.n` (see §6); also run by `build_rc_game_view.py`. |
| `renderdoc_dump_material_cbs.py` | via `qrenderdoc_run.py` | Dumps each G-buffer draw's fragment constant buffers and texture hashes. |
| `dump_material_values.py` | Python 3 | Prints a `.material` value section as floats, for matching with the CB dump. |
| `materials_srgb_from_dxgi_format.patch` | applied 2026-10-06 (archived) | Pipeline: colourspace from the DXGI format instead of the filename suffix. |
| `brdf_check.py` | Python 3 + numpy | Numerical proof of §2.2. |

### Steps

See `README.md` for the tested commands (dump, build view, install view, extract, render).

## 4. Still unknown, and the cheapest evidence for each

1. **Per-material constants** (spec scale, gloss scale, emissive K). Partly resolved: self-lit K = key 0x66EFA55B, see §6:
   - Take one shadPS4 capture with the weapon equipped in first person.
   - Dump the weapon's G-buffer draw with the same script (`--events <eid>`).
   - The constants are in its buffers.
   - These are the floats already visible in the `.material` value sections (e.g. Predator: 1.0, 0.708, 10, 4, 15, 13, 13, 0.6, 4, 2, 0.8, 1.0). One capture names them all.
2. **Exposure for other levels**:
   - The Veldin capture's exposure is for Veldin daytime.
   - The 1D/3D LUTs are very likely global; exposure may be per-level or adaptive.
   - A Novalis capture gives Novalis exposure.
3. **Glass and transparent materials** are forward-shaded (e.g. pipeline 58202, `SRC_ALPHA/ONE_MINUS_SRC_ALPHA`). Their full shaders are in the same capture, and the same method (read the SPIR-V) applies.
4. **Oren-Nayar diffuse**:
   - The shader currently uses Lambert (diffuse roughness 0).
   - Blender's Oren-Nayar is a different parameterisation.
   - The difference only matters for gloss < 0.3.
5. **Ambient**: hemisphere fill only with flag 128; Veldin uses the irradiance buffer (see §6).
6. **Normal-map green**: the current "no inversion" convention is kept, with `flip_normal_green=` as an option. The tangent sign comes from a vertex flag (`fragment_6553` `_482`). Test once on a model with obvious bevels.

## 5. Recommended AGENTS.md changes

Replace the four texture/shader bullets in "Source and texture rules" and "Assignment and shader preflight" with:

- Texture colourspace follows the **native DXGI format** (sRGB formats 29/72/75/78/91/93/99 → sRGB, else Non-Color). Alpha is always linear and channel-packed. `_g` is normally BC7 sRGB.
- Opaque surfaces use the **RC PS4 Deferred BRDF** group (`rc2016/pipeline/game_shader.py`):
  - diffuse = `_c`
  - spec = GGX Schlick with F0 = `_g`
  - roughness = 2^(−2.75·gloss)
  - added, not mixed
  - self-lit = `_c × _c.a × K`

  Do not reintroduce `c·(1−g)`, the 18% mix, `1 − gloss` roughness floors or Hue/Sat "colour tuning".
- Game lighting needs:
  - the captured exposure, plus the **RC PS4 Game** view transform
  - light strengths = π × game colour
  - Golden Gate HDRI only for neutral review renders, never as "game accurate"
- Read the capture's SPIR-V (RenderDoc) before attempting GCN microcode tracing.

## 6. Results after running (2026-10-06)

Veldin capture, Sheepinator pipeline run; details and commands in README.md.

- Exposure x4.5538 (+2.187 stops). The OCIO view matches the numpy game tonemap (median 0.0000, max 0.005). Composite validation median error 0.0099 (bloom modelled; lens dirt and film grain not); the captured exposure is the error minimum.
- Key light colour (5.30, 4.48, 2.95) gives sun strength pi x 5.30 = 16.65. The captured direction is game-camera relative and is not used.
- Ambient: buf0 dword 135 = 0x40 (bit 128 clear), so the hemisphere fill is unused in this frame. Diffuse ambient = irradiance buffer x AO; the fit around Ratchet is c0 about (0.091, 0.074, 0.036), roughly 10x below the unused fill constants. Reflections are not modelled, so reflective dark parts are darker than in game.
- Self-lit: x = K x _c alpha. K comes from material key 0x66EFA55B (8.0 in Ratchet's body material, 5.0 in the Sheepinator material, so K = 5). Inferred from one match; not yet verified on a second material.
- The colourspace-from-DXGI patch is applied to rc2016/formats/materials.py (Sheepinator _g, format 99, is now sRGB).
- Only 13 G-buffer draws exist in this capture (Ratchet + held weapon); the other pre-lighting draws are depth/shadow-only.
- The capture was re-recorded on 2026-09-25: shader IDs are now lighting 540, tonemap 6172, G-buffer writer 6335 (same disassembly as 532, 6386, 6553).
