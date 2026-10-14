# Shading, render state and lighting

Reference for how the pipeline shades Ratchet & Clank (PS4, 2016) materials in Blender, which parts come from recovered game data, and which are approximations. Pipeline stages are described in [pipeline](pipeline.md); the in-app viewer's own GL shading is described in [viewer](viewer.md).

The equations below were read from the SPIR-V of a RenderDoc capture of the game (recorded with shadPS4) instead of being traced from GCN microcode. The capture analysis, capture IDs and dump scripts live in the research notes and are not part of this repository. Facts that depend on the capture are marked as such.

## Modules and data

| Path | Purpose |
|---|---|
| `rc2016/pipeline/game_shader.py` | Node group "RC PS4 Deferred BRDF", material conversion, per-family front-ends, character glass, viewer graph rules. |
| `rc2016/pipeline/render_state.py` | Blend and cull state from a `.material` flags word. |
| `rc2016/pipeline/material_flags.json.gz` | Census of the flags word for every `.material` in the archive (path to flags). |
| `rc2016/pipeline/scene_defaults.py` | New-scene defaults, `load_shared_lighting`, `apply_shared_lighting`, `check_shared_lighting`, `audit_scene`. |
| `assets/lighting/shared_lighting.json`, `golden_gate_hills_1k.exr` | Shared review lighting. |
| `rc2016/pipeline/game_look.py` | Opt-in captured Veldin look (`apply_veldin_look`). |
| `rc2016/pipeline/game_view_compositor.py` | The captured tonemap as compositor nodes. |
| `assets/game_view/` | Captured view data: `rc_game_view.json`, `rc_tonemap.spi1d`, `rc_grade.cube`. |
| `rc2016/formats/materials.py` | Chooses image colourspaces; produces the material reports used here. |
| `rc2016/shaders/families.py` | Shader family registry keyed by source path and decoded SHA-256. |

## Textures

- Set DDS images to `Alpha Mode: Channel Packed`. Alpha is always linear data.
- Colourspace follows the native DXGI format, not the filename: sRGB for formats 29, 72, 75, 78, 91, 93 and 99, Non-Color for every other format. `rc2016/formats/materials.py` applies this; for example a `_g` stored as BC7 sRGB (format 99) is sRGB.
- Normals are always Non-Color.
- Roles by suffix:
  - `_c`: base colour, normally BC7 sRGB. Its alpha is a self-lit mask, never ordinary transparency. The one exception is the foliage writers (`basic_foliage`, the tree-leaf graphs), where it is read only as cut-out coverage compared with an alpha-test threshold and nothing else (see [viewer](viewer.md#leaf-cut-out)).
  - `_g`: normally BC7 sRGB, coloured specular F0 in RGB and gloss in alpha. This is a property of the common graph; some graphs use `_g` differently (see Graph families).
  - `_n`: DirectX tangent normal (BC5 RG, `z = 1 - x² - y²`, normalised), strength 1.0. Green is not flipped by default (`flip_normal_green=` exists on `convert_material`); the game takes the tangent sign from a vertex flag, so test a flip on a model with obvious bevels before using it.
- Keep UV orientation. Do not add metallic masks or strong AO.

## Opaque deferred BRDF

### Recovered equations

G-buffer writer:
- `_g` RGB (sRGB-decoded, times a material constant) goes to MRT2 RGB as linear F0.
- Gloss: `t = sqrt(saturate(gloss² × K_gloss))`, `e = 2^(-11 t)`, then a geometric specular anti-aliasing widening `w`, `alpha_out = -log2(1 - w(1 - e)) / 11`. On flat surfaces `w = 1` and `alpha_out = t`.
- `_c` goes to MRT1 RGB as albedo. MRT1 is `R8G8B8A8_SRGB`, so albedo is stored clamped to [0, 1]. MRT1 alpha stores the self-lit term as `0.2 × log2(1 + x)`; lighting decodes `x = 2^(5a) - 1`.

Deferred lighting, with `s = 2^(-11 × gloss)` (GGX α² = s):

```
k      = (α + 1)² / 8, α = sqrt(s)                      Smith-Schlick
D·G    = s / ((NH²(s-1) + 1)² · (NV(1-k) + k) · (NL(1-k) + k))
F      = F0 + (sat(60·F0) - F0)(1 - VH)^5              Schlick with F90 = sat(60·F0)
spec   = 1/4 · D·G · NL · F · lightColour
diff   = NL · OrenNayar(σ² = s) · lightColour
out    = Σspec + (Σdiff + ambientDiffuse·AO + selfLit) · albedo + reflections · specOcclusion
```

A numerical check against textbook GGX/Smith/Schlick with α = 2^(-5.5 × gloss) and light irradiance E = π × colour agreed to four significant figures. The game BRDF omits 1/π, so Blender light strength = π × game light colour (`game_light_to_blender_strength`).

Summary used by the Blender group:

| Term | Value |
|---|---|
| Diffuse | `_c`, no `(1 - _g)` factor |
| Specular | GGX with Schlick Fresnel, F0 = `_g.rgb` |
| F90 | `sat(60 · F0)` |
| Blender roughness | `2^(-2.75 · gloss)` |
| Combination | diffuse and specular are added, not mixed |
| Self-lit | `_c.rgb × _c.a × K` |
| Metallic | 0 |

The `_c × (1 − _g)` diffuse, the 18% Principled mix and `1 − gloss` roughness floors are not the game equation and must not be reintroduced.

### Self-lit constant K

K is a per-material constant under the material key `0x66EFA55B` (`EMISSIVE_K_KEY`). It was identified from the capture: the emissive G-buffer shader for the hero character reads K from its material constant buffer (8.0), and 8.0 occurs in that `.material` only under this key. A second material was checked offline against a native skin graph's cached writer (K = 2.0 for the Toxic Crab). The key identity is inferred from these matches, not from a full survey. K is per graph (see Graph families).

`emissive_constants_from_reports(materials_dir)` reads K for each material from the pipeline reports (`<model>/materials/*.json`, `instance_parameters`) and returns `{material name: K}`.

### Blender node group

`game_shader.py` builds the node group "RC PS4 Deferred BRDF" (`GROUP_VERSION` 2).

Inputs: albedo (`_c` RGB), self-lit mask (`_c` alpha), specular F0 (`_g` RGB), gloss (`_g` alpha), normal, specular scale, gloss scale, emissive intensity. Outputs: shader and Blender roughness.

Construction:
- Gloss is `sqrt(saturate(gloss² × gloss scale))`; roughness is `2^(-2.75 × gloss)`.
- Diffuse is a Diffuse BSDF with roughness 0 (Lambert) coloured by `_c`.
- Specular is a metallic Principled BSDF with GGX and white specular tint, which gives Schlick on single-scatter GGX. The F90 term is exact: the lobe is fed `F0 / F90` and weighted by `F90`.
- Diffuse and specular are added, then self-lit (Emission of `_c` with strength `_c.a × intensity`) is added.

Functions:
- `convert_material(mat, ...)` rebuilds one material that has `_c` and `_g` images. It sets image colourspaces and alpha mode, removes other nodes, keeps UV and mapping nodes feeding the images, wires the group, adds a Normal Map node for `_n` (or a Geometry normal when there is none) and writes `rc_shader` and `rc_shader_evidence` properties on the material. Without an `emissive_intensity` it uses 4.0 when `_c` alpha holds a sparse authored mask, else 0.
- `convert_all(emissive_by_material=None, reconvert=False, **kwargs)` converts every eligible material and skips ones already converted unless `reconvert=True`. Pass per-material K from `emissive_constants_from_reports`.
- Backups: before converting, each material is copied to `"<name> | pre-RC-game-shader"` with a fake user and an `rc_backup_of` property. `restore_backups()` swaps every original back. Image colourspace changes apply to the shared image, so a restored backup also sees them.
- Glass, energy and VFX materials are skipped by `convert_all`.

Validate a converted material by asserting the group's inputs, image colourspaces and links through the active Material Output after reopening the file. Node names and a plausible render are not evidence.

### Known gaps

- Reflections (the reflection probe term) are not modelled, so reflective dark parts render darker than in game.
- Diffuse uses Lambert; the game uses Oren-Nayar with σ² = s. Blender's Oren-Nayar parameterisation differs, and the difference matters only for gloss below about 0.3.
- Per-material specular and gloss scales default to 1.0 unless read from a graph's parameter table.
- Bloom, lens dirt and film grain of the final composite are not part of the material shading.

## Graph families

`_g` is not coloured F0 in every graph. The pipeline classifies each referenced graph by canonical source path and decoded SHA-256 in `rc2016/shaders/families.py`, and `apply_preflight_material` applies a front-end only to an exact registered identity. It never infers a family from a name. Unregistered graphs get an explicitly labelled source-colour-only preview, or the viewer graph rule below. Source-identical graphs reuse the interpretation; instance values stay material-specific.

Parameter rows come from `.materialgraph` section `0x45C4F4C0` (u16 byte offset, u16 size, u32 key). The registered-family reader splits layouts where the offset returns to zero and reads the first layout's defaults. Native translation instead uses each 24-byte `0xBC93FB5E` record's parameter/resource counts to partition rows, including empty passes and layouts that start at a nonzero offset. Its padded CB size partitions the corresponding block in `0xA59F667B`; extents, alignment, overlap and complete consumption are checked. Instance overrides are bound by key hash within that pass's layout.

| Family | G-buffer writer | `_g` reading | Self-lit |
|---|---|---|---|
| Common graph (`convert_material`) | cached | RGB = F0, alpha = gloss | `_c.a × K`, key `0x66EFA55B` |
| `basic_normal_gloss` | `5ebdeca4` | R = gloss, G = grey specular | none |
| `basic_normal_coloredgloss_emissive` | `c4828176` | RGB F0, alpha gloss | `_c.a × K`, key `0x06F19114` |
| `basic_incandescence` | `5dbcccb7` / `42254271` | R = gloss, G = grey specular | `_g.B × K`, key `0x5FD56522` |
| `basic_colored_spec_emmisive_in_c_alpha` | `89638dfb` (native GCN decode) | RGB F0, alpha gloss | `_c.a × K`, key `0xF8C61683` |
| `skin_emissive` | `0x567fe897` | R = gloss, G = grey specular, Fresnel-boosted | `_c.a × K` from table 1 |

Deferred families use specular scale key `0x62835AF9` and gloss scale key `0x24F4CA4C` (`apply_deferred_family`).

Skin (`apply_skin_emissive`, group `SKIN_GROUP_NAME`, `SKIN_GROUP_VERSION` 3):
- Fresnel-boosted gloss and specular, and a Fresnel albedo tint of `(0.745404, 0.644480, 0.603827)`.
- Albedo `_c × lerp(P5, tint, F)` is clamped to [0, 1] (node "Skin albedo UNORM clamp") because MRT1 is UNORM; some graphs use P5 above 1. The lighting pass multiplies self-lit by the same stored albedo. `rc2016/shaders/validation.py` asserts the clamp.
- The native pixel shader also samples a skin ramp and adds a diffuse term tinted `(0.637597, 0.116971, 0.122139)`. That term and the complete forward light and shadow response are not reproduced, so recognition stays partial and materials carry `game_equivalence = False`.
- `apply_native_skin_normal` connects the decoded normal `normalize(2R-1, 2G-1, 1-x²-y²)` to both the BRDF and the skin group's material normal input. The secondary-normal blend (keys `0x59011FBD`, `0xB7ABB64A`) is applied only when it resolves; otherwise `rc_shader_frontend_limitation` is set.
- Stage assignments from native GCN: `7D3F5FA6` and `D7749B42` are vertex shaders; `BED06840` and `AE7244FE` are forward pixel shaders.

Per-family decodes (decoded writers, native skin and `character_glass` GCN) come from the research notes, which are not included.

### Buffer-read tracing

Known MUBUF and MTBUF loads preserve their decoded component write span and the optional TFE status register. Each unresolved data component and status value has a distinct IR identity, so unrelated fetched words cannot cancel as equal. Adjacent registers retain their values. Loads to LDS and address/destination spans beyond the VGPR range stay unsupported.

Unresolved reads retain a diagnostic `buffer_access` record: raw instruction words, register operands, offset/index controls, instruction format when available, descriptor-word proof, and bounded address-expression snapshots. This record does not evaluate memory or establish buffer bounds, runtime contents, a vertex attribute meaning, or draw selection. Conditional direct descriptor joins with differing or missing identities suppress the diagnostic proof. Descriptor identity alone does not bind a fetch. Typed buffer reads remain unsupported by the rendering frontends.

`V_SAD_U32` uses unsigned absolute difference plus its third operand with 32-bit wrap, following the detailed operation in AMD's [GCN3 ISA reference](https://gpuopen.com/download/AMD_GCN3_Instruction_Set_Architecture_rev1.1.pdf). Integer results retain bit-pattern semantics. Constants and exact identities can fold during tracing; live integer expressions remain outside the portable material frontend. Non-finite bit-pattern literals and unverified instruction modifiers retain the existing unsupported limits.

### Scalar predicate tracing

Diagnostic vertex tracing keeps unresolved scalar comparisons as opaque runtime flags. A `predicate` record retains the exact instruction words, opcode, signed or unsigned immediate bits, and scalar operand snapshots. Operand origins use structural hashes independent of DAG numbering; the same instruction address with different origins does not collapse into one atom. Unresolved scalar operations from the supported 32-bit SOP2 table retain diagnostic operand snapshots so preceding masks and offsets can be inspected without evaluating them. Neither record evaluates a comparison, reads buffer contents, selects a draw-time flag or enables a material frontend. Unresolved scalar reads additionally retain a `scalar_memory_access` record with descriptor and address origins. Register offsets require a known unsigned scalar word; the numeric value of an F32 carrier is not used as a byte address.

SOPC scalar comparisons consume one shared literal dword when either source selector is 255, following the [AMD GCN3 ISA](https://www.amd.com/content/dam/amd/en/documents/radeon-tech-docs/instruction-set-architectures/gcn3-instruction-set-architecture.pdf). A literal with instruction-like bits remains operand data; the following instruction begins after the literal.

### Explicit instance-record tracing

`vertex_exports(..., context=InstanceRecordContext(...))` accepts one immutable
272-byte source-CB1 record, its original instance index, an explicitly identified
wave-uniform instance VGPR, an exact native program SHA-256 and a caller origin.
The default trace supplies none of these inputs. The context evaluates only
known, aligned scalar buffer reads inside that record after checking all four
descriptor words and their path/taint proof. Invalid reads fail atomically;
other buffers, typed fetches and dynamic vertex values remain unresolved.
Finite constant first-lane reads may fold inside this explicit context.

The DAG's `vertex_context_receipt` records the supplied origin, code/record hashes,
instance index, byte window and read spans. Origins are caller assertions; this
API does not establish runtime binding, shader permutation or draw selection.
It is a diagnostic input contract, not material admission: viewer and Blender
frontends do not create records or replace their existing stream proof. No
captured flags or matrices are applied globally, and `game_equivalence` remains
false. CPU-decoded normal normalization is not a substitute for raw vertex
fetch values.

### Instance vector-matrix arithmetic

`instance_vector_matrix(matrix, copy_linear=...)` ports the source CPU helper
that writes the three padded vectors at instance-record bytes 128/144/160.
The supplied native-basis matrix contains four 16-byte axis/translation groups.
The general branch computes cofactors with the decoded float32 operation order
and divides by the determinant when its absolute value is at least 2^-20.
Below that threshold it retains unscaled cofactors. The explicit fast branch
copies the linear axes unchanged. Output padding is zero; vector magnitudes
are retained and translation does not affect this vector matrix. With axes
assembled as columns, the general result is an inverse transpose.

The helper accepts finite float32 arithmetic and rejects overflow/nonfinite
results. Runtime floating-point controls are not established. Its selector
comes from CPU instance state, not directly from the GPU record's flags.
Callers must supply an effective transform and selector explicitly; the helper
does not establish their zone/runtime origin or assemble other CB1 fields.
Viewer and Blender material admission remain unchanged.

The opt-in static packed-vector preview uses this helper with source packed
10:10:10 UNORM data at vertex byte `+8`. Each component is recentered as
`2u - 1`; the vector is not normalized and is kept separate from the decoded
normal. Let `a`, `b`, and `c` be the three linear axes and `l_a = dot(a,a)`
(likewise for `l_b` and `l_c`). The source CPU selector copies the linear axes
when `abs(l_a - l_b) < 0.001` and `abs(l_a - l_c) < 0.001`; otherwise it uses
the inverse transpose, retaining unscaled cofactors when `abs(det) < 2^-20`.
The source vertex expression agrees for the examined producers in every pass
under flags 1 and 61. That agreement proves the expression, not runtime pass
selection or a game draw. Standalone and level viewer inputs remain opt-in;
Blender requires a separate `POINT`/`FLOAT_VECTOR` attribute baked in native
Y-up coordinates and validates that attribute after reopening. It does not
implicitly alias this vector to the normal attribute.

### Viewer graph rules

For graphs without a registered family, `apply_viewer_graph_rule` builds the asset viewer's per-graph reading on the shared deferred BRDF (rules from `viewer_graph_rule` in `rc2016/formats/materials.py`; the GL implementation is in `rc2016/viewer/shading.py`, see [viewer](viewer.md)).
- `g_mode` `grey`: F0 = grey specular × `_g.G`, gloss = `sqrt(sat(gloss × _g.R))`. `grey_skin`: F0 = grey `min(0.5, _g.G)`, gloss = `sqrt(sat(_g.R))`. `vehicle`: packed constants. Otherwise coloured F0 = specular × `_g.rgb`, gloss = `sqrt(sat(gloss × _g.a²))`.
- No `_g` means no specular, as in the viewer.
- Self-lit = `_c.rgb × (_g.B or _c.a) × K` with the viewer's effective K. Normal x, y = 2RG − 1 with the viewer's z.
- Each term in `rule['ported']` builds the viewer's material terms and frozen inputs as nodes named `VR <term> <param>`. Closure limitations are recorded separately on the material; `ported` does not imply identical rendered output. Terms in `rule['unported']` are recorded on the material. The forward unlit screen rule (`mo_neb_amb_screen_01`) is built at t = 0.
- These materials carry `shader_preview_status = viewer_graph_rule_frontend` and `game_equivalence = False`.
- Parallax UV marching and height visibility are explicitly unported in Blender. When POM is the only missing term, the proven C/G/N and tint/detail front-end remains selected instead of a native translation. Additional unported terms retain the existing translation-selection policy. Reopen validation checks that the limitation and source normal equation survive.

### Two-colour metal verification

The exact `basic_normal_two_color_gloss_metal` rule preserves the source diffuse tint, grey specular strength, rim gloss and specular tint. In the viewport, the specular tint multiplies the accumulated lighting **after** GGX/F90. The Blender front-end instead multiplies F0 by the tint, because it cannot colour-multiply a closure; it records this approximation in `rc_shader_frontend_limitation`. Source material metadata parity does not establish closure or game equivalence.

`tests/test_two_color_metal.py` checks the eight saved source instances when `RC2016_RESEARCH` points to the private workspace. It verifies slot identity, raw overrides and defaults, zero values, stale self-lit suppression, exact graph matching, viewport payload transport and Blender front-end metadata. No source assets are stored in the repository.

The hidden Windows GPU proof reads the selected model directly from the archive and removes only the exact rule in memory for its baseline. It checks geometry and camera preservation, unaffected material payloads, shader compilation and source hashes, and writes PNGs and a JSON report to an explicit external folder:

```powershell
python scripts/prove_two_color_metal.py --source '<game-root>' --research '<research-root>' --output '<external-proof-folder>'
```

The proof uses viewer lighting, no SSR or fog attenuation, and fixed normal fade, geometric AA and instance opacity. It is a rendering check, not a captured runtime draw comparison.

### Character glass

`character_glass` (character visors, and glass graphs that share the shader) is decoded from its native forward pixel shader `34D5B21C`, which has no cached SPIR-V. `apply_character_glass` builds group "RC PS4 character_glass forward" (`GLASS_GROUP_VERSION` 3):

```
out   = Transparent(1 - alpha·A) + alpha · (Diffuse(B·(1 + k·F·T·E)) + GGX(tint, rough))
alpha = (1.001 - N·V)^P8 · (P10 + sat(P11 + sat(4·(1 - N·V)^P13)))
F     = (1.001 - N·V)^P12,   T = sat(4·sqrt(1 - N·V))
```

- A = 0 for additive blend (SRC_ALPHA, ONE) and 1 for alpha-over (SRC_ALPHA, ONE_MINUS_SRC_ALPHA); it is taken from the material flags word (`character_glass_blend`), and the call fails if the flags are not a one-sided additive or alpha-over word.
- E is the captured Veldin ambient radiance `c0` from `assets/game_view/rc_game_view.json`, or black if the file is unavailable.
- The additive blend was confirmed on a captured `character_glass_02` draw. Hash-bound profiles for other glass recipes take precedence where they exist.

## Render state

Blend and cull come from the `.material` flags word, not from names or guesses (`rc2016/pipeline/render_state.py`).

- Every `.material` has a 36-byte section `0xE1275683`; the flags word is the u32 at byte 16.
- Bits 0-1 select blend: 0 opaque, 1 additive alpha (SRC_ALPHA, ONE), 2 alpha over (SRC_ALPHA, ONE_MINUS_SRC_ALPHA), 3 not observed (`decode_flags` returns surface `None`). Bit 2 makes the material two-sided; otherwise back faces are culled.
- Foliage with an opaque flags word is alpha-tested, not blended: the pixel shader rejects fragments whose `_c` alpha is below the interpolated `attr0.z`, which the engine supplies per instance (the viewer and Blender use an inferred 0.5).
- Engine special passes override the flag. Decals (`mo_decal_*`) and fur shells (flag bit `0x80000`, which draws extra alpha-over passes) were captured with blends their flag does not predict.
- `material_flags(material_path=None, data=None)` reads the word from decoded bytes, or by lower-cased archive path from `material_flags.json.gz`, a census of every `.material` in the game archive.
- The rule was validated against draws in the capture. The captured depth, sampler and full blend state tables are in the research notes.

## Lighting

### Shared review lighting (default)

`assets/lighting/shared_lighting.json` defines the review lighting for every scene. Editing it changes all new scenes.

| Setting | Value |
|---|---|
| HDRI | `golden_gate_hills_1k.exr`, world strength 2 |
| Separate lights | none |
| View transform | Khronos PBR Neutral, look None |
| Exposure | −1.0 |
| Gamma | 1.0 |
| Compositor | off |

- The 1k file is a 4×4 box-filtered float copy of the 4k original, 6 MB. It is float RGB because the sun reaches about 9e4, beyond half float. Lighting energy is within 0.01% of the original and 25% test renders matched within noise.
- `scene_defaults.load_shared_lighting` fails loudly if a key or the HDRI is missing. `apply_new_scene_defaults` and `apply_shared_lighting` apply it and record it on the scene as `scene["shared_lighting"]`. `check_shared_lighting` and the validators compare against that record, and `audit_scene` reports `lighting.differs_from_current_shared_file` for older scenes.
- The pipeline default is `--lighting-source hdri`; `--lighting-config` selects another lighting file. The pipeline links the HDRI by absolute path; pack it in hand-delivered scenes.
- This is neutral review lighting. It is not game lighting and must not be called game accurate.
- Render defaults in `scene_defaults.py`: 3840 × 2560, Cycles with OptiX on GPU only, 96 samples, adaptive threshold 0.04, 5 total bounces (2 diffuse, 2 glossy, 4 transmission, 6 transparent, 0 volume), Light Tree, transparent film.

### Captured game look (opt-in)

`--lighting-source veldin` in the pipeline, or `game_look.apply_veldin_look(scene)`, replaces the lighting with values recovered from the Veldin capture. It requires a scene camera. Use it only when asked.

Final composite (tonemap):

```
hdr  = scene × EXPOSURE
u    = clamp(log2(max(hdr, 2^-8)) / 13 + 8/13)       13 stops, -8 to +5
tone = saturate(LUT1D(u) + bloom)
out  = LUT3D(sRGB_OETF(tone) · S + O)                 S = 0.96875, O = 0.015625
```

Captured values (`assets/game_view/rc_game_view.json`):
- Exposure ×4.5538, +2.187 stops. The tonemap is a 256-entry R16 1D table (`rc_tonemap.spi1d`) and a 32³ grade table (`rc_grade.cube`).
- Validation of the modelled chain against the captured final image: median absolute error 0.0099 (bloom modelled; lens dirt and a 3% time-rotated film-grain lerp are not). The captured exposure is the error minimum: 0.021 at ×0.95, 0.0099 at ×1.00, 0.013 at ×1.05.
- Key light colour (5.30, 4.48, 2.95), so Blender sun strength is π × 5.30 = 16.65. `apply_veldin_look` adds a sun with the captured colour and strength. The captured direction is relative to the game camera and is not used; the sun is placed on the camera's side at elevation 45° and azimuth offset 30° (overridable with `RC_SUN_ELEVATION_DEG` and `RC_SUN_AZIMUTH_OFFSET_DEG`). Other scene lights are hidden from render.
- Ambient: frame flag dword 135 is `0x40`, bit 128 clear, so the game does not use the hemisphere fill constants (C0 about 1.0) in this frame. Diffuse ambient is the irradiance buffer times AO. A fit as `a + B·n` around the hero gives radiance c0 about (0.091, 0.074, 0.036), roughly 10 times below the unused fill. `apply_veldin_look` builds a world from that fit unless `RC_KEEP_WORLD=1`, and falls back to the hemisphere fill (`c0 + 1.5·c1·up`) only if the fit says the game uses it.
- Only 13 G-buffer draws exist in the capture (the hero and held weapon); the remaining pre-lighting draws are depth and shadow only.

Fidelity check: a fit of the captured lit buffer as `albedo × (key × N·L + irradiance × AO)` over 254k G-buffer pixels gave key scale 0.985 / 1.063 / 1.098 (R/G/B), so π × colour and the fitted ambient are correct and exposure is applied once.

Why it is not the default: on a standalone model the camera-side unshadowed sun puts most surfaces at high N·L, where the tonemap shoulder flattens colour (18% grey at N·L = 1 gives sRGB 0.84). A purple and teal enemy rendered pink and cream.

Compositor implementation (`game_view_compositor.py`): exposure, then `lg2` over [−8, +5], then `rc_tonemap.spi1d`, clamp, sRGB encode and `rc_grade.cube`, built as compositor nodes from packed float LUT images sampled with Map UV. The scene view transform is set to Raw, so no OCIO environment is needed and the result shows in renders and in the viewport (compositor "Always"). The Map UV node reads the UV's Z as a coverage mask, so it is set to 1. The game samples its grade as a 3D texture (trilinear); the compositor matches a trilinear cube to 1.6e-6 and the tetrahedral OCIO view to a maximum of 0.0035 (mean 1e-4).

## Recovered data versus approximation

Recovered from the capture or native shader decode:
- The deferred BRDF equations, gloss and roughness mapping, F0/F90 rule and the self-lit encoding.
- Colourspaces from DXGI formats.
- The render-state flags word and its blend and cull rule.
- Veldin exposure, tonemap, grade, key light colour and ambient fit.
- The decoded front-ends of the registered families and `character_glass`.

Approximations:
- Lambert instead of Oren-Nayar diffuse; no reflections; Principled GGX standing in for the game GGX/Smith lobe.
- Skin and viewer-graph materials (`game_equivalence = False`).
- The shared review lighting, which is neutral and not game lighting.
- Presentation-tuned profiles in `rc2016/pipeline/material_profiles/` and per-weapon profiles are separate from this shading and are not game equations.

Known gaps:
- Glass, energy and VFX materials are forward-shaded and not covered by the deferred group. Only `character_glass` and the documented per-model recipes exist.
- Fur shells (flag `0x80000`) are drawn as ordinary opaque surfaces; their alpha passes and `fur_control` textures are not modelled. One no-graph fur-shell skin layer is read with the grey `_g` layout by inference from its sibling (see [viewer](viewer.md)).
- K is verified on two materials only; a capture with another weapon equipped would confirm further values.
- Exposure is recovered for Veldin daytime only. Whether other levels use a different or adaptive exposure is unknown; the 1D and 3D tables are probably global.
- The native skin ramp term and the full forward light and shadow response are not reproduced.
- Mode 3 of the blend bits has no captured example.

Do not infer that a whole material family is solved from one writer or a plausible render. Decode and validate each distinct graph implementation before registering it.
