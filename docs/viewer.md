# Asset browser and viewer

Reference for the Tk asset browser (`python -m rc2016`) and its Windows OpenGL viewer. The browser reads the game archive directly: selecting an asset decodes only that asset's bytes in a background worker. Blender is optional and only needed for FBX/USD/`.blend` export, the research Blender build, and isolated scene proofs.

Related documents:

- [levels](levels.md): level and zone decoding, the level viewer.
- [pipeline](pipeline.md): the extraction pipeline, archive loading, the research Blender build.
- [shading](shading.md): the game's deferred shading, lighting and tonemap, and the `RC PS4 Deferred BRDF` node group.
- [animation](animation.md): animation container and sample formats, and how clips become Blender actions.

Module map:

| Module | Role |
|---|---|
| `rc2016/app/main.py` | Entry point (`python -m rc2016`, `--scan`, packaged-exe backend); re-exports the app's public names |
| `rc2016/app/window.py` | `AssetBrowserApp`: main window, Options window, scan and export wiring |
| `rc2016/app/batch.py` | Model discovery, output verification, `BatchRunner`, `PipelineBatchRunner`, `ExportBatchRunner`, `PipelineIndexScanner` |
| `rc2016/app/theme.py`, `rc2016/app/settings.py` | Dark theme, tooltips, UI scale, Tk runtime bootstrap; settings file, project paths, Python/Blender discovery |
| `rc2016/app/browser.py` | Model/Level/Flash UI views, 3D/Textures/Details notebook, model details |
| `rc2016/app/gallery.py` | Gallery tab: virtualised thumbnail grid, selection and render-queue rules |
| `rc2016/app/thumbnails.py` | Thumbnail cache index, offscreen renderer and its CLI (`python -m rc2016.app.thumbnails`) |
| `rc2016/app/catalog.py`, `rc2016/app/workers.py` | Read-only result catalog; coalesced background workers and owned-process cleanup |
| `rc2016/viewer/loader.py` | `SourcePreviewLoader`: archive model to viewport payload |
| `rc2016/viewer/viewport.py` | Windows OpenGL viewport (ctypes, no extra packages) |
| `rc2016/viewer/shading.py` | GLSL 1.20 programs, shared deferred lighting and glass/forward paths |
| `rc2016/viewer/native_glsl.py` | Material-bound native DAG emission, selection and CPU surface evaluation |
| `rc2016/viewer/textures.py` | Texture decoding/upload, material slot tables, `GRAPH_RULES`, texture export |
| `rc2016/viewer/animation.py` | Clip library and skinning matrices |
| `rc2016/viewer/environment.py` | Shared review HDRI as lighting maps: built-in EXR reader, sun extraction, diffuse SH and prefiltered specular |
| `rc2016/formats/links.py` | Archive-wide asset links table (`build_links`, `AssetLinks`) and material rule coverage (`rule_coverage`) |
| `rc2016/app/links.py` | Browser access to the links table: `LinkService` worker and callbacks, `placement_lines` |
| `rc2016/levels/*` | Level panel and viewport ([levels](levels.md)) |
| `rc2016/flash/tab.py`, `gfx.py`, `planet_maps.py` | Flash UI tab, SWF/GFX renderer, planet-map export |
| `rc2016/export/models.py`, `blender_convert.py` | Portable export |

## Launching

Requirements: Windows with an OpenGL-capable GPU and a regular Python 3.11+ with Tkinter. The 3D view uses only the standard library (OpenGL through ctypes). Optional packages: Pillow (Textures tab, PNG export; imported lazily), NumPy (faster model preparation and texture un-tiling; required for the Shared HDRI lighting), `lz4` (faster archive decompression). If OpenGL initialisation fails, the view shows a placeholder explaining why.

```powershell
python -m rc2016                  # asset browser
python -m rc2016.levels.viewer    # standalone level viewer
python -m rc2016.flash.planet_maps <source> <out>   # planet maps without the GUI
```

1. Set **Source** to the extracted game folder (the one that holds the archive: `layout.csv`, `toc`, `dag`, `chunkmap.txt`, segments; for the EUR dump this is the `CUSA01073` folder, not its parent, which holds two archive copies). Set **Output** to a separate folder.
2. Click **Scan**. It indexes the archive and writes the model index and `reports/asset_references.sqlite` into the output root. Leave the Blender path empty to just browse.
3. Choose an asset type and select an item.

The window opens maximised, follows Windows' app mode (light or dark, including the title bar), and has a UI scale of 75-200% that resizes fonts live. Dark mode and scale are stored in the settings file.

## Asset types

An **Asset type** selector (Model / Level / Flash UI) above the search row decides what the main area shows. Each type has a list on the left and a viewer on the right. Switching hides the other views without destroying them, so a loaded model, level or UI file stays loaded.

| Type | Left | Right |
|---|---|---|
| Model (default) | Search/filter row and model tree | Notebook with **3D model**, **Textures**, **Details**, **Gallery** |
| Level | The archive's levels; a typed `.level` or `.zone` path plus **Load** below the list | Zone toggles, view options, level viewport ([levels](levels.md)) |
| Flash UI | The archive's 53 `.gfx`/`.swf` files | Symbol list, canvas, frame and export controls |

The level list and the Flash UI list are read the first time their type is selected.

The Level tab and standalone level viewer include **Actors (source placements)**, enabled by default. The layer previews structurally bound primary models, with skinned NPCs/enemies in source bind pose. Actor-only zones have working zone toggles. Click an actor to report its name, actor path, primary model and ID; double click opens that model. Status reports actors drawn, unresolved and skipped-skinned. The standalone `--no-actors` option hides the layer while keeping its assets preloaded. Runtime state selection, animation and unproven attachment/material components remain excluded; see [level actors](levels.md#gameplay-actor-decoder-actor_formatpy).

**Enemy spawns (experimental)** is off by default. Enable it to inspect approximate factory populations, including Novalis's 84 Peckbots and 20 Birdbots. The standalone option is `--experimental-spawns`. Every eligible encounter state is combined; template/location cycling and repeated-anchor spacing are inspection choices, not decoded game placement. Enemies use source bind pose and the shared material path. Picking identifies the factory, template and location; status labels approximate draws and unresolved inputs. The ordinary actor toggle and zone toggles remain independent. See [enemy factory preview](levels.md#enemy-factory-coverage-and-experimental-preview) for source layouts, placement policy and limitations.

## Model tree, search, used-by

- The tree groups models by archive folder. A folder that holds one model and no subfolders is skipped (`folder_layout`). Selecting a folder selects every model inside it.
- Models are named by the named actors whose files reference them (`actor_users()`, one level of parent actors included), read from `reports/asset_references.sqlite`. The numbered moby/tie/shrub assets of the first game's data carry no name anywhere in the game data (their own materials are numbered too), and almost none is used by a named actor.
- The search box matches path, ID, actor names and animation-set names. Several words must all match.
- The State filter (All, READY, EXPORTED, VERIFIED, STALE, NO ANIMSET, PREVIOUS) reflects saved builds in the output root; EXPORTED marks folders that hold an `export.json`. Browsing does not approve backend cache reuse.
- The **Shading** filter (All, Native translation, Generic reading, Family fallback, No material graph, Exact rules only) sits next to State and uses the links table ([Rule coverage](#rule-coverage)).

## 3D view

Select one source row to load its geometry. Archive metadata is loaded once; only the selected model's bytes and geometry are decoded, in a background worker that coalesces pending selections.

**Controls:** left drag orbits, right drag pans, the wheel zooms whenever the pointer is over the view, **Reset view** or double click restores framing. Redraws are scheduled once per idle pass (`_schedule_draw`) and redrawn after Tk's `<Expose>`/`<Map>`, because the view has no Tk background and Tk would otherwise paint over the GL output.

**View modes** (`ModelViewport.MODES`): Shaded (default), Textured, Solid, Wireframe, Solid + wireframe.

**Look and LOD:** the source look and Highest / Lowest / All LOD selections use the model's native memberships.

**Pose:** the model is drawn in its bind/rest pose until an animation is played. Attachments and runtime animation blending are not reproduced.

**Unsupported layouts** show the decoder's reason and clear the previous geometry. Loose-file transform mode has no native archive identity and says so.

**Textured mode** draws, per material slot, the `_c` texture that the material references directly (otherwise via its material graph), with the decoder's UV0 (V flipped back). The block-compressed data (BC1-BC7) is read in memory with the pipeline's header parser and un-tiler and uploaded unchanged via `glCompressedTexImage2D`, so the GPU decodes it. The streamed full-size mip is used when the built-in mip is under 512 px (`MIN_PREVIEW_SIZE`; about 0.25 s per 1024x1024 texture to un-tile). sRGB formats are uploaded as their UNORM twins, because the fixed-function path lights in display space. This mode is base colour only (no gloss, normal, self-lit mask, tint or shader); slots without a `_c` are grey. The numbered first-game mobys point at a 2x2 `default_pink_shader_c` and show pink.

**Texture filtering:** after upload the driver builds the mip chain from the top mip (`glGenerateMipmap`), and surface textures sample with `GL_LINEAR_MIPMAP_LINEAR` plus anisotropic filtering (up to 8x) when `EXT_texture_filter_anisotropic` is present (`ModelViewport._upload_texture`, `MIPMAP_ROLES`). The chain is generated in the texture's own format, so sRGB formats are filtered in linear space. Lookup tables (`_pick`, `_vehicle_p2`, `_sequence`), the puddle atlas (sampled through `fract()`) and the level sky keep top-mip `GL_LINEAR`. If the driver refuses to generate mips for block-compressed formats, compressed textures keep top-mip `GL_LINEAR` for the rest of the session; a texture never samples an incomplete chain. The game's own mip chains are not read (only the top mip is decoded), and `MIPMAPS = False` restores top-mip sampling everywhere.

**GPU buffers:** vertex, normal, UV, skin, rock-alpha and index arrays are uploaded once to GL buffers on first draw (`ModelViewport._vptr`). A new mesh frees the old buffers and bindings are reset after each frame. Without the GL 1.5 entry points every pointer falls back to client memory; both paths give identical framebuffers.

**GPU skinning** takes up to 8 influences. Bone matrices are read from a 1-row RGBA32F texture, because a uniform array failed to link above about 200 joints on NVIDIA (C6020). NVIDIA aliases built-in vertex attributes, so skin attributes use slots 1, 6, 7 and 9.

**Loading speed:**

- The archive opens from the pipeline's cached parsed `layout.csv` (`.cache/shared_index`; see [pipeline](pipeline.md), "Archive loading speed"). With the optional `lz4` package, LZ4 blocks decode in C.
- Large files hashed on every open (the archive metadata, the shared assets table in `open_asset_index`) are re-hashed only when their size or modification time changes (`stat_file_hash`, sidecar `.cache/shared_index/file_hashes.json`). The archive root found under the game folder is remembered (`located_roots.json`) and used while it still holds the metadata files and a segment; otherwise the folder is walked again. Hash values are unchanged, so existing caches stay valid.
- The viewer reads only the first copy of an asset that the archive repeats (`verify_repeats = False`, as the level viewer does); the scan and pipeline still compare every copy.
- Each `Archive` memoizes decompressed assets and their reference lists (`asset_data`, `asset_references`; LRU, 256 MB, single assets over 32 MB are not kept), so a material, its graphs and texture headers are read once per model open although textures, the texture list, rule coverage and the native translation all need them. `texture_header` is memoized per built asset and filled by `load_texture`'s own parse. These caches and the texture caches of `material_textures` are thread-safe; the level viewer resolves materials from a thread pool.
- Decoded top mips are kept on disk in `.cache/texture_preview` (`TextureDiskCache`, at most 2 GB, least recently used removed first). The key covers the archive metadata hashes, the texture's archive location and the source of the texture decoders, so a changed archive or decoder misses. Files are written atomically and carry a payload hash; a damaged file is decoded again.
- Models are decoded from memory with `decode_model(..., as_arrays=True)`: positions, normals and triangles stay NumPy arrays through `mesh_payload` and `prepare_viewport`, and skin weights are decoded per batch with NumPy. UVs, skin and rock-alpha values reach the payload as flat lists, as before.
- The loader's lock covers its own state only; the decode, texture reads and viewport arrays run outside it, so clip, pose and texture-list requests are not queued behind a model decode.
- `prepare_viewport` builds the GL arrays with NumPy and falls back to the pure-Python `validate_mesh`, which also supplies the exact error message for an invalid payload. Both paths produce identical arrays.
- The remaining time is mostly the shared native decoder (`rc2016.formats.native_model.decode_model`); a 550k-vertex prop loads in a few seconds and models under about 20k vertices draw in under 1 ms per frame.
- **Recent models** (`SourcePreviewLoader._recent`): the last 6 decoded models (at most 1.5 million vertices in total) are kept with their animation libraries, so switching back or changing look or LOD is nearly instant. The key includes the output root, because animation sets are found through it. Setting `loader.identity = None` forces a fresh decode.
- **Animations after the mesh:** the browser calls `load(..., defer_animation=True)`. A skinned model returns at once with `animation_pending=True` ("Loading animations..."), then `load_animation()` builds its clip library on a separate worker outside the loader's main lock. The clip menu and Details fill in only if the same model is still shown. Other callers keep the eager default.

## Shaded mode

Shaded (`rc2016/viewer/shading.py`) uses GLSL 1.20 programs on the legacy WGL context. Hand rules and native translations supply surface values to the same deferred BRDF (see [shading](shading.md)). The hand-rule path reads:

- diffuse = `_c`;
- specular = GGX with Schlick Fresnel, F0 from `_g`, F90 = sat(60 x F0), GGX alpha = 2^(-5.5 x gloss) (roughness = 2^(-2.75 x gloss)); diffuse and specular are added;
- self-lit = `_c.rgb x _c.a x K`;
- `_n` is read as an RG tangent normal without a green flip, with the tangent frame taken from screen-space UV derivatives.

The lighting is selected under the 3D view ([Lighting and tonemap](#lighting-and-tonemap)), not the game's lighting. The skin Fresnel lerp is not applied.

### Native translation

**Native translation** is under **Materials**, on by default, in both the model and level viewers. It affects Shaded mode only and preserves the camera when switched. The level CLI accepts `--no-native-translation` for comparable captures. Validated automatic-decoder extensions use this ordinary path; the separate Experimental switch is not required.

`rc2016/viewer/native_glsl.py` emits the ready, material-bound scalar DAG from `shaders.bindings.prepare_translation`: albedo, F0, gloss, emission multiplier and the optional tangent-space normal. Fully ported exact viewer rules retain precedence through `materials._viewer_rule_wins_translation`, preserving their tint, pick, foliage and other terms. An eligible translation replaces a generic reading or family fallback; unsupported translations keep those readings. Forward rules retain their separate programs. No graph-name, shader-ID or code-hash recipe dispatch is added to the translator.

The GLSL frontend mirrors `shaders.nodes`: UV0.xy is the only input, every sample retains its own coordinates and channels, and textures use linear filtering with repeat addressing. The viewer already reverses the decoder's Blender V flip and uploads top-down image rows, so native UV arithmetic and sample coordinates use game-space V directly. Native textures have separate GL cache entries: DXGI sRGB formats decode RGB before filtering, while alpha and normal maps stay linear. Missing native normals use the geometry normal. Supplied normals use the translated xyz slice with a derivative tangent basis; the game's packed vertex TBN is not reconstructed.

Pass contracts use the serialized parameter/resource counts, including empty parameter passes, and partition padded default blocks. Portable candidates are compared by exact source-bound expressions: texture identity and interpretation, UV arithmetic, channels, literals, output and normal expressions. API registers, sample labels and node indices do not select a variant. The receipt retains all equivalent candidate sources and a `surface_sha256`; genuinely different portable expressions remain unsupported. The comparison covers the translated slice only. For example, all 31 `basic_normal_gloss_detail` materials have equivalent recovered surfaces, but detail-normal composition is omitted and reported; their exact viewer rule keeps precedence.

Typed-buffer loads retain their encoded destination span: X, XY, XYZ and XYZW write one through four VGPRs, with an additional status VGPR when TFE is set. Their loaded values remain unsupported; neighbouring live values survive. This can restore a portable packed export, expose an engine-dependent normal, or reveal differing sibling expressions. Such inputs and variant disagreements still fail closed.

Programs are cached by `translation_sha256`, emitter version, texture interpretation, vertex program and shared fragment source, and each material entry remembers its resolved program until **Native translation** is toggled. Constants are literals. Where the driver supports program binaries (GL 4.1 or `ARB_get_program_binary`), every linked program is also stored under `.cache/gl_programs`, keyed by both shader sources, the attribute bindings and the driver's vendor, renderer and version strings; a later session loads the binary instead of compiling, and a missing, damaged or rejected binary is compiled from source again. The level viewer links a material's native program while its textures upload, a few per Tk tick, instead of at its first draw. The level viewer groups draws by native program then material and records uniform state per material and program. Lighting, tonemap and the Viewer rig / Shared HDRI selectors are shared with the hand-rule path.

A ready translation describes a bounded deferred surface, with `game_equivalence: false`. It does not establish runtime draw/variant selection, engine constants, sampler state or complete game shading. After material parameter binding, a source `exp2(log2(base) * exponent)` chain preserves zero when `base` is explicitly absolute or a nonnegative literal and the exponent is a known positive literal; direct `exp2(log2(base))` has exponent one. Positive-input arithmetic stays unchanged. The shared IR guard reaches both viewer and Blender emitters, while standalone logarithms, unknown domains and nonpositive or unbound exponents retain the existing portable math guards. CPU source controls for `basic_normal_gloss` and untinted `mo_gbl_color_tint_01`, using identical linear texel samples, agree on albedo, F0, gloss and emission. At interpolated UVs the native frontend decodes sRGB before filtering, whereas the retained hand shader uses its power-2.2 approximation after filtering, so the sampled values can differ. Their normal slices differ: the translated basic writer uses normalized `(x, y, 1-x*x-y*y)`, while the hand rule uses a square-root z; the selected tint translation has no normal slice. These findings preserve exact-rule precedence and are covered by `tests/test_native_glsl.py` and `tests/test_source_zero_powers.py`.

### Lighting and tonemap

Controls below the model view and in the level sidebar are grouped by purpose:

| Group | Controls and master behaviour |
|---|---|
| Lighting | Master **On** switches rig/HDRI diffuse, specular and ambient lighting together. In levels it also gates AO, shadows and authored points. Off provides unlit opaque surface inspection; self-lit/forward emission remains. Child choices are remembered. |
| Display | Tonemap stays available independently of the Lighting master. |
| Materials | Ordinary Native translation, enabled by default. |
| Scene (levels) | Instance overrides, sky, source actors and collision/helpers. |
| Experimental | Master **Enable**, off by default, gates Material previews and the level enemy-spawn preview. Their individual selections are retained across master toggles. |

The Lighting source and Display tonemap selectors offer:

| Selector | Choices | Default |
|---|---|---|
| Lighting | `Viewer rig`, `Shared HDRI` | `Viewer rig` |
| Tonemap | `ACES fit`, `Khronos PBR Neutral` | `ACES fit` |

Choosing Shared HDRI also selects Khronos PBR Neutral; the tonemap can be changed back.

### Experimental material previews

**Experimental → Enable → Material previews** enables a hash-bound Novalis mountain **albedo-only matte preview**. Its effective material is `rp_nov_gnd_dirt_rock_blend_01.material`, referencing `rp_nov_grass_blend_detail_01.materialgraph`. It uses the simpler source writer's parameter-bound rock/grass colour expression and original texture bindings. The viewer supplies world position and geometry normal as explicit preview adapters, and chooses the simpler pass deliberately. Detailed normal/fade terms, frame reconstruction and runtime variant selection remain unverified. The source's `rk_no_spec_c` resources have no built archive asset; F0 and gloss are zero matte presentation settings, not decoded substitutes. The UI labels these limitations. Switching Experimental off returns to ordinary material selection without moving the camera.

Preview receipts have `status: experimental`, `automatic_ready: false` and `game_equivalence: false`, and are separate from native translations, corpus readiness and exact rules. A source/hash/resource mismatch leaves the original fallback. The source texture colourspace is applied before filtering and both UV0 and the per-sample projected coordinates retain their own expressions. No source assets are altered.

The same switch also enables a source-bound Novalis **ground layer preview** for `nov_ground_grass_01.material`, the effective override on four terrain slots. A conservative descriptor trace joins the source pass resources, and the straight-line output suffix supplies the two-dirt-to-grass albedo blend, grey F0 and flat-surface gloss. Serialized parameter byte offsets retain the source threshold 0.5 and F0 scale 1.5. This is an explicit source-pass choice; runtime selection is unverified.

**Blend red**, **Blend green** and **Ground UV scale** are review controls. Red and green are squared before entering the source blend expressions, matching the candidate vertex kernel. Their defaults (0.75, 0.5) are uniform presentation inputs, not recovered vertex colours. UV0 times the review scale (default 1) substitutes for the unresolved planar/parallax coordinates. Geometry normals substitute for the unresolved composed normals; derivative AA, native sampling and painted path boundaries are not reconstructed. The UI labels these limits. Controls affect only this ground preview; mountains, native readiness and ordinary exports are unchanged. Turning Experimental off restores ordinary material selection with the same camera.

The shared Blender frontend supports both previews only through an explicit `apply_translated_material(material, source, experimental=True)` call using `source['shader_experimental_preview']`. Ordinary pipeline/export builds do not opt in. `validate_translated_material(..., experimental=True)` checks the stored receipt, node tree, active frontend and image bindings after reopening.

- **Viewer rig** is the fixed key, fill and sky/ground ambient rig at 0 EV. In the model viewer the rig turns with the camera. In the level viewer it is fixed to the world (rig right = +X, up = +Z, towards the viewer = -Y in Blender's Z-up world, as seen by a camera looking along +Y), so lighting does not shift while orbiting or flying. Viewer rig with ACES fit is the rendering the gallery thumbnails use.
- **Shared HDRI** takes the HDRI, world strength and exposure from `assets/lighting/shared_lighting.json`: the same file as the Blender review renders (`golden_gate_hills_1k.exr`, strength 2, exposure -1 EV). The viewer cannot path-trace, so `rc2016/viewer/environment.py` splits the map:
  - texels brighter than luminance 20 (`SUN_THRESHOLD`) give their excess radiance to one directional sun light (energy-weighted direction, summed irradiance, so energy is conserved). It lights like a rig light and keeps the terminator sharp;
  - diffuse from the rest is 9 spherical-harmonic irradiance coefficients (albedo x E / pi);
  - specular from the rest is the radiance prefiltered with the GGX lobe at six roughness levels (perceptual roughness 0.15, 0.3, 0.45, 0.6, 0.8, 1.0; 128x64 equirectangular bands stacked in one RGB16F texture on texture unit 6), times Karis' analytic environment BRDF for F0 and F90.
  
  The HDRI is read by a small built-in OpenEXR reader (scanline; uncompressed, ZIPS or ZIP; HALF or FLOAT). NumPy is required in this mode. The maps are computed once on a worker thread (about a second) and cached in `<repo>/.cache/environment/<hdri hash>_v<version>.npz`. Until they are ready the view keeps the viewer rig and shows a note.
- **Direction convention** (checked against Cycles renders of the HDRI: six directions each returned exactly the texel selected): Blender maps a world direction `d` to `u = 0.5 - atan2(d.y, d.x) / (2 pi)` and `v = atan2(d.z, hypot(d.x, d.y)) / pi + 0.5` (v = 0 is the bottom row; +X is the image centre). Model-viewer vertices are already in Blender's Z-up basis; level geometry is native Y-up and goes through the presentation basis.
- **Khronos PBR Neutral** is GLSL (start compression 0.76, desaturation 0.15) followed by the sRGB transfer function. Exposure multiplies before the tonemap. ACES fit is the viewer's historical curve with gamma 2.2.

Validation (agreement with the Blender review render, not game accuracy): a unit sphere rendered by the viewer and by Cycles, with the same camera, shared lighting, Khronos PBR Neutral and -1 EV:

| Surface | Mean absolute difference | 95th percentile |
|---|---|---|
| Matte, albedo 0.47 (sRGB 0.72-0.78 grey) | 0.5/255 | 1/255 |
| Glossy (diffuse 0.2 + GGX F0 0.039, gloss 0.6, added as the game BRDF) | 1.4/255 | 5/255 |

The standalone model viewer has no shadowing, ambient occlusion or interreflection. The level viewer adds independently switchable [scene AO, key shadows and supported authored points](levels.md); these are bounded previews with unverified native runtime allocation. Cycles has them, so concave or occluded parts of real models differ. Forward and glass materials use the same lighting, with the tonemap applied per fragment before blending. The shared HDRI mode predicts the Blender review render; it is not game lighting.

### `_g` meaning per material graph

The `_g` channel layout depends on the graph's decoded writer. `GRAPH_RULES` in `rc2016/viewer/textures.py` registers decoded rules by exact full graph stems ending in a dot before the `.materialgraph` extension. These rules precede the retained skin, incandescence and `basic_normal_gloss` family fallbacks; the first matching entry wins. Graphs without a rule or eligible native translation use the generic reading: coloured F0 = spec x `_g.rgb`, gloss = sqrt(sat(gloss x a^2)), where `a` is `_g.a`.

A material that names no graph gets the generic reading too. One exception: `FUR_SHELL_GREY_READING` in `rc2016/viewer/textures.py` lists one exact fur-shell material (Big Al's `skin_and_fur`, render-state bit `0x80000`, no graph) and reads it as grey, like its sibling skin material that uses the same `_c`/`_g`/`_n` textures. This is an inference from the shared textures, not a decoded rule (`g_mode_source` marks it). The fur-shell passes themselves are not drawn; the layer is shaded as a normal opaque surface.

The specular and gloss scales (keys `0x62835AF9` and `0x24F4CA4C`) come from the material, else from the graph default. K (self-lit strength) comes from the self-lit key in the graph's own parameter table, because materials can store keys their graph never reads (for example writer `C4828176` uses only `0x06F19114`). Self-lit keys by family: `0x66EFA55B` (default deferred), `0x06F19114` (coloured-gloss-emissive), `0xF8C61683` (emissive in `_c` alpha), `0x5FD56522` (incandescence).

"Grey" below means F0 = grey spec x `_g.G` and gloss = sqrt(sat(gloss x `_g.R`)). Writer IDs are the hashes of the decoded shader writers; the decode notes are research material (not included here).

| Graph | Reading |
|---|---|
| `basic_skin/*` | R = gloss (sqrt), G = grey specular capped at 0.5. Verified graph identities select C/G/N by the pipeline slot roles; stale instance slots and the secondary normal cannot win by suffix. Coloured F0 would give red specular on skin |
| `basic_normal_gloss`, `_detail`, `_monitor_dep`, `_aniso` (writer `5EBDECA4`) | Grey, no self-lit |
| `basic_incandescence` | Grey; self-lit = `0x5FD56522` x `_g.B` |
| `basic_normal_gloss_emissive`, `_emissive_detail`, `_fresnel_detail`, `_emissive_colored_spec`, `_aniso_incan`, `_specfresnel_emissive` | Six separately traced writers: grey, with graph-specific tint, glow, rim and detail terms; detail uses texture unit 11; pulses show t=0 |
| other `basic_normal_gloss*` | Undecoded writers: grey by name only, K from the graph's own table |
| `blarg_saucer_colorpick` (writer `66744D01`) | Grey; self-lit = `0x06F19114` x `_g.B`; `_c.a` blends towards the 8x1 `_colorpick_c` palette |
| `mo_gbl_color_tint_01` (writer `BE8C3EE0`) | Switch `0x7107A2D8` < 0.5 (every corpus material) reads `_g` as grey, otherwise coloured; F0 x specular tint `0x6B591974`; no self-lit; albedo tinted towards colours A/B using the `_h` mask (unit 5) |
| `jn_mo_gbl_color_tint_01_random_color`, `jn_color_tint_detail`, `jn_gbl_color_tint_01_world_detail`, `..._world_detail_normal` | Grey, no self-lit; tint, palette and detail terms; instance palette magnitude fixed at 0.5, world origin 0; unit 9. The exact detail-parallax writer has a separate rule below |
| `za_basic_normal_gloss` (writer `E08357DC`) | Grey; spec and gloss suppressed by a geometry-normal view-angle term (exponent key `0x4D385B45`); scales clamped before the texture multiply; no self-lit |
| `basic_normal_two_color_gloss_metal` (writers `1012E99D`/`EC1E7CD3`) | Grey, no self-lit; forward diffuse and specular tints and rim gloss, with viewer light, no SSR or fog, instance opacity 1 |
| `mo_gbl_arch_emissive_sequence_01` (writer `AAED778C`) | Preview at CB0[99]=0 and zero instance interpolants; grey G as F0, gloss sqrt(sat(5R)), colour/base-mask albedo and sequence/mask glow; stale K and `_c` ignored; unit 8 |
| `mo_veh_random_color_01` (writer `EB1AC2CD`) | UV0.x selects two linear palettes (second on unit 10), paint mask, view-dependent RGB tints; constant packed F0 (0, 0.0999756, 0.0999756), gloss 0.948681; no self-lit; forward flake slice omitted |
| `basic_parallax_occlusion` (writer `5FADAD33`) | Grey, no layout switch or self-lit; serialized C/G/N/height-alpha slots; material-dependent UV march, intersection interpolation and seven albedo height samples; height unit 12 |
| `jn_color_tint_detail_parallax` (writer `4E4D7894`) | Grey, no self-lit; source tint and detail normal at displaced UVs; fixed 16-64 steps, height key `0xEE0F9FF8`, visibility applied after tint to albedo only; height unit 12 |
| `jn_basic_normal_gloss_glow_mask` (`2685144F`), `_02` (`CA81B14F`) | Grey, K `0x06F19114`; first: mask/tint/environment and offset-parallax glow; `_02`: `c.R x mask x K` with a vertex factor; unit 13 |
| `mo_blend_01` (writer `886AAC94`) | Blends two source-resolved c/g/n layers through H, squared vertex alpha and sharpness; grey, no self-lit; unit 15; source vertex alpha when available, otherwise 1 |
| `rp_mo_blend_color_breakup_01` (writer `A3DAA047`) | The `mo_blend_01` H/alpha/sharpness blend of g/n layers sampled at U x UV0 (key `0xE8D31733`), H at UV0; albedo is one breakup `_c` sample at native world (x, z) / T (key `0xFD70EF49`), no layer colour or tint; unit 15. Native position is world space in the level viewer and model space in the model viewer and Blender |
| `basic_foliage` | Four deferred writers: grey, no self-lit, no subsurface output; normal z = 1 - x^2 - y^2; leaf cut-out on `_c` alpha with a decoded per-material baseline ([Leaf cut-out](#leaf-cut-out)); dither and normal fade omitted |
| `rp_nov_plant_tree_foliage_transparent`, `gbl_plant_tree_foliage_transparent_01` (tree leaves) | Leaf cut-out ([Leaf cut-out](#leaf-cut-out)). Both draw the decoded pass-0 `cma` Fresnel mix and `cmb` ramp overlay with **preview palette U = 0.5**, in the model/level viewers and Blender. `rp_nov` (`0476D929`) uses material E/S/Y and geometry-normal Fresnel; global bushes (`CDD7CD94`) use material E, literal scale 0.5, palette V = 0.65 + 0.2 U, mapped-normal Fresnel and zero F0/gloss/self-lit. Runtime pass and instance inputs are unverified |
| `mo_neb_amb_screen_01` (forward `8716DC7A`) | Source timeline/grid, mask, colour/intensity and scroll noise at CB0[99]=0 with frozen cutoff, alpha and fog; units 0/1/2/4 |
| `jn_ground_puddles` (forward `DB75867B`, no-normal `4A6CDAAA`) | Source noise thresholds drive grey F0, gloss and diffuse attenuation; t=0 ripple normal and environment-reflection blend; noise/ripple atlas on unit 14. The emissive sibling stays generic |
| `npc_qwark_robot_bulb` (exact graph, forward `0CD2AAFB`) | White base (no albedo input) plus the writer's red facing glow at amplitude CB0 dw115 = 0.5 (captured 0.01; blink timing not decoded). Reads as white with a pink-to-red rim, not saturated red |

### Leaf cut-out

The deferred foliage writers kill a fragment when its coverage is below an interpolated threshold; the viewer discards the same fragments (`discard` after the `_c` sample, `uAlphaCut`).

- **Coverage** is `_c` alpha, saturated. It is the only use of that alpha in these writers: self-lit is zero and nothing reaches the blend, so `_c` alpha is cut-out coverage here, not the self-lit mask. `basic_foliage` (`FE32FE79`, `B01E7CB2`; shader cache SPIR-V) and the tree-leaf graphs (`0476D929` in pass 0 and `6F0C1A0C` in pass 1 of `rp_nov_plant_tree_foliage_transparent`; native GCN, not cached) share the structure: sample `_c` (api5), multiply by a near-camera distance fade (tree graphs only, key `0xF74DBA4D`; off at its default), optionally take the minimum with a second texture at UV/3 when instance flag 128 is set, then reject when the result is below `attr0.z`. The sibling `gbl_plant_tree_foliage_transparent_01` has the same writers without the mask.
- **Threshold.** The decoded ordinary static-mesh baseline is `min((byte20 + byte21) * float32(1/255), 1)`, from the material's render-state section `0xE1275683`; multiplication is rounded to float32. The CPU material loader (`0x1841c80`) reads these bytes, the packet builder (`0x182a9b0`) calculates the threshold, and the uploader (`0x1841990`) puts it at byte 268 of the 272-byte instance record. The cached instanced vertex shaders pass that field to `attr0.z` when record flags & 3 is nonzero. Novalis grass uses 89/255, flowers 107/255 and tree branches 104/255; zero is a valid material baseline. Runtime draw cut/fade can raise byte21, and draw flag 8 overrides the threshold to 4/255; these runtime inputs are omitted. Other vertex-shader paths can read specialized buffer fields or calculate a threshold; their fallback is not universally 1.0. Only missing or truncated material state uses `FOLIAGE_ALPHA_CUTOFF_FALLBACK = 0.5`, an inferred preview fallback, not an engine default (`rc2016/viewer/textures.py`).
- **State.** The decoded tree-leaf and `basic_foliage` materials are opaque (blend bits 0), so the cut-out is an alpha test, not blending. Most are two-sided (bit 2); the viewer draws both faces of every opaque material, so leaves do not vanish from behind.
- Shaded and Textured modes cut out; Solid does not (no texture is bound). The level viewer's id-colour pick pass samples the same `_c` alpha (`uPickCut`), so a click passes through a hole.
- Novalis tree leaf colour (`rp_nov`, pass-0 writer `0476D929`; `cma` = material slot `0x49A7866B96F96A72`, `cmb` = `0x49A7866B4984DA89`): `F = (1.00100004673 - sat(N·V))^CB2[0]` (geometry normal), `L = CB2[2] · mix(c.rgb, cma.r, F)`, `albedo = cmb < 0.5 ? 2·cmb·L : 1 - 2(1 - cmb)(1 - L)`. `CB2[0]` (exponent E, key `0x9BCD75AC`), `CB2[2]` (scale S, `0xAAC47D17`) and `CB2[1]` (palette Y, `0x0F5B49E2`) are read per material like the other terms, with graph defaults and then 1.0 / 0.5 fallbacks. Palette U is a preview placeholder: the writer multiplies GPU instance-record float112 by float120; the viewer freezes U to 0.5 and samples the ramp on the CPU with top-mip repeat/bilinear and sRGB decoding before filtering. The saved CPU trace proves these bytes are copied from a transform block, but its producer-to-zone relationship remains unproven. The captured translation triplet is from Veldin; its absence from Novalis zones does not establish that Novalis U cannot be reconstructed. Pass selection, native sampler state and the remaining F0/gloss/normal terms are unverified. Blender mirrors the colour equation and frozen sample, with post-reopen constants, source-image and output-link validation; its native sRGB image handling differs from the viewer's approximate colour decoding. This preview is not game equivalence.
- Global pale bushes use the same per-channel overlay in pass-0 writer `CDD7CD94`, with `F` evaluated against the mapped normal. Normal z is `1-x*x-y*y`; normal scale is frozen to 2 (frame scale/fade unproven). The exact global normal slot is `0x49A7866B67D6383E`. Palette U is natively `0.5*sin(record.float112*record.float120)+0.5`; the preview freezes it to 0.5, giving V = 0.75 from the writer's float32 literals. MRT2 and self-lit are literal zero, so unrelated `_g` references do not contribute. This rule applies to the exact global tree-foliage graph, including its source-resolved Novalis shrub/flora materials; adjacent foliage graphs stay unchanged.
- Omitted: the near-camera distance fade, the flag-128 second mask, the dither and the foliage normal fade. The Blender front-end uses the same per-material reading and mixes a transparent shader with the BRDF by `_c.a >= threshold` (`VR alpha cut threshold`); runtime threshold changes remain a limitation.

Ground puddles use the exact graph path and serialized C/G/N/noise/ripple slots. Generic specular and gloss keys are ignored because neither source parameter table declares them. Noise is stationary; CB0[99] scrolls ripple V. Each sampled normal is normalized, then the puddle blend is left unnormalized. Its forward terminal composition retains the reflection-mix parameter and specular clamp, with the viewer rig supplying light/specular/environment inputs. Origin and instance-axis scales are frozen to zero and one, normal fade is zero, geometric AA and opacity are one, and SSR, fog attenuation and debug outputs are disabled. These are explicit preview inputs; runtime pass selection, descriptor bindings, face polarity and engine lighting are unverified. The atlas preserves native tile sizes with repeat gutters and uses linear top-mip sampling. The Blender mirror ports mask, D/F0/gloss and ripple-normal terms, then uses the deferred BRDF; it records that this GL forward reflection mix, specular clamp and fog composition are not reproduced.

Limitations that apply across the table: viewer lighting, ambient, TBN and instance values are substitutes for the game's runtime inputs; time-driven terms show t=0; runtime draw, constant-buffer and descriptor binding is unproven for several writers (the two-colour metal, vehicle and screen graphs); variable-intensity hero materials are left unruled pending the runtime phase inputs. Base textures use units 0/1/2, skinning uses vertex unit 3, and palettes use unit 4. Graph extras use units 5 (tint mask), 8 (arch sequence), 9 (tint variants), 10 (second vehicle palette), 11 (basic_normal_gloss detail), 12 (parallax height), 13 (glow mask), 14 (puddle atlas) and 15 (rock blend).

Parallax samples height alpha with the original UV gradients. Runtime fade, tangent interpolants and shadow-light direction are replaced by fade 1, a derivative tangent frame and the viewer key light. The source visibility is unclamped and can brighten albedo or become negative; no grazing-denominator clamp is added. Nonpositive or unrepresentable step counts, nonfinite parameters, singular rays and overflowing ray offsets disable POM as explicit preview guards. OpenGL contexts without `GL_ARB_shader_texture_lod` retain the channel-correct preview and display a capability warning. Blender builds the proven material terms at unshifted UVs and records the UV march and height visibility as unported; this does not establish node, runtime or game equivalence.

### Rule coverage

The Details tab lists, under each material slot, which rule matched the material:

- **exact rule**: a `GRAPH_RULES` prefix ending in a dot, naming one decoded writer;
- **native translation**: an eligible material-bound deferred DAG after exact-rule precedence; its limitations appear in Details and game equivalence stays false;
- **family fallback**: a prefix naming a family (`basic_skin/`, `basic_normal_gloss*`, `basic_incandescence/`);
- **generic reading**: no rule (coloured F0 with gloss alpha);
- **no material graph**.

The first matching hand rule is tested for precedence, then an eligible native translation replaces its generic/family reading. `rc2016.formats.links.rule_coverage` accepts the selected translation receipt; `material_rules` in `rc2016/viewer/loader.py` uses the same selector. `rule_text` in `rc2016/app/browser.py` provides the wording, and switching Native translation off restores the fallback wording in Details.

The model list's **Shading** filter uses the same classification (a model matches when any of its materials has that kind; Exact rules only requires every material to be exact). It reads the links table, built on first use.

With Native translation enabled, the EUR archive has 11,180 materials: 9,802 exact rule, 165 native translation (51 graphs), 149 family fallback, 1,060 generic reading and 4 without a graph. Of 6,864 models with materials, 5,797 have at least one exact-rule material, 475 a native translation, 1,559 a generic reading, 513 a family fallback, and 14 a material without a graph; these model categories overlap. The 233 base materials of models placed in Novalis zones are 188 exact rule, 6 native translation (5 graphs), 4 family fallback and 35 generic reading. Coverage describes the selected surface frontend, not game accuracy, and excludes per-instance replacement materials from the Novalis base count. Native eligibility is stored per material in the links table; source changes invalidate its cache.

### Glass and forward materials

Graphs with a `forward` rule are drawn after every opaque batch with a second program (`GLASS_FRAGMENT`, `Shading.glass_program`, `ModelViewport._draw_glass`).

- **Draw state** comes from the material's render-state flags word: `.material` section `0xE1275683`, u32 at byte 16 (`material_flags`, `blend_state`). Bits 0-1 select blend (0 opaque, 1 additive, 2 alpha-over; 3 has no captured example); bit 2 set means two-sided. Additive is (`SRC_ALPHA`, `ONE`), alpha-over is (`SRC_ALPHA`, `ONE_MINUS_SRC_ALPHA`). With bit 2 clear, back faces are culled (the decoded meshes are counter-clockwise front).
- Depth write is off. Glass batches draw farthest first by their rest-pose centroid in view space (`split_forward`, `back_to_front`); triangles within a batch are not sorted.
- **`character_glass`** (Blarg visor, writer `34D5B21C`): rim alpha, tinted reflection without Fresnel, and body colour x (1 + environment rim). Parameters are read from the material, else the graph default.
- **`blarg_saucer_glass`** (saucer dome, writer `FEEC00CE`): the writer binds textures by slot hash, not suffix: the palette `_colorpick_c` and `Patterns_cma` in the `_n` slot. The material's `glass_c`/`glass_g` are never read (Textured mode still shows `glass_c`). Alpha = sat(0.9 + rim), so the dome is at least 90% opaque: dark amber 0.4 x palette x diffuse, grey F0 0.1 reflections, a faint pattern normal.
- **Approximations:** the engine's light loop, probes, screen-space reflections and fog are replaced by the selected viewer lighting (rig or shared HDRI). Colour is tonemapped per fragment before blending (the game blends HDR before tonemapping). Instance fade is 0 and `attr0.w` is 1.
- **Water preview** (`pok_uniq_water` folder: `pok_uniq_water`, `rp_pok_uniq_water_biolumenscent`; `uKind` 4) is an approximation, not a decode. The rule is registered as a family prefix, so rule coverage counts it as a fallback.
  - The colour writers (`f4d47ece`, `5476807b`) are not translated: they have per-light loops, about 13 KB of code and 63 texture samples, and they are not in the shadPS4 cache.
  - From game data: the alpha-over state; the normal tiles by slot hash (`water_tile_01n` in the `_n` slot, `water_tile_02n` in the `_pick` slot); and, from the normal pre-pass `b9a5ef0f`, the world-planar mapping (native x, z) with its scale and offset literals at time 0. The pre-pass applies them to the single-channel height tiles; reusing them for the normal tiles is an assumption.
  - The body colour is key `0x1DAF77B8`; its role is inferred.
  - Presentation values (`WATER_PRESENTATION` in `textures.py`): opacity 0.8, F0 0.02, gloss 0.9, normal strength 0.5. The shading is body colour x viewer diffuse x (1 - Fresnel), plus GGX and environment reflection; alpha is raised to 1 by Fresnel.
  - Not drawn: scrolling, foam, depth fade, shoreline coverage and refraction.
- Other forward graphs (VFX, energy, cornea, other glass families) have no rule and draw opaque.

## Animation playback

`rc2016/viewer/animation.py` builds the clip library:

- Animation sets come from the actors that reference the model in `asset_references.sqlite`, following their configs. Attached actors are not followed.
- Clips are listed from the ordered containers, decoded on selection (`decode_clip`, `sample_transforms`) and classified (`classify`).
- Skin matrices = `worlds(rows, frame, rule) @ inverse_bind`, with the pipeline's inheritance rule (a single candidate, else SSC0), conjugated into the preview basis. Rest skinning is exact.
- Additive-style clips play standalone, not on top of a base pose.
- The clip picker is a searchable folder list opened by the Animation button. The frame label shows `frame/total @ fps`.

See [animation](animation.md) for the clip and sample formats.

## Textures tab

**Show** switches between two listings:

- **This model:** every `.texture` referenced by the selected model's materials, directly or as material-graph defaults (marked "graph default"), with format, full size and material.
- **Whole archive:** all 18,994 textures, including ones no model uses (UI art, loading screens, skies, VFX, environment probes). Format and size fill in when a texture is viewed. **Filter** matches every word against the path. At most 3,000 rows are shown (`TEXTURE_ROW_LIMIT`); the filter narrows the rest.

Selecting a texture decodes it in a worker and previews it with a channel selector (RGB, RGBA over a checkerboard, R, G, B, Alpha). RGB is the default, because alpha is usually mask data such as the `_c` self-lit mask. The preview reuses the 3D view's decoded mip (at least 512 px). Array textures (38 in the corpus, mostly VFX `_3d` flipbooks) enable the **Slice** selector.

**Export selected... / Export listed...** write any combination of these into a chosen folder. Export listed covers every filter match, not only the 3,000 rows shown, and asks for confirmation above 200 textures.

- **Original .texture:** the decompressed built asset, plus `<name>.texture.stream` exactly as archived when the texture has a stream.
- **DDS:** the original block-compressed largest top mip with a DX10 header.
- **PNG:** the stored values with no colourspace conversion. R8G8 gets B = 0 and BC6H is clamped to 8-bit by Pillow. Array textures export as one DX10 array DDS plus one PNG per slice (`<name>_sliceNN.png`).

Name collisions get a `__2`, `__3` suffix. A failed texture is logged and does not stop the others.

**Flip vertically** is off by default and affects only the preview and PNG output. Unflipped means as stored (top row first). The pipeline's Blender builds never flip images; the decoder flips UV V instead (`v_blender = 1 - v_game`). Original and DDS output is never flipped.

Pillow has no BC1-BC3 sRGB decoder, so those blocks decode as their identical UNORM twins; no colourspace conversion is applied either way.

`rc2016/formats/texture.py` reads every texture in the archive: BC1/2/3 sRGB (DXGI 72/75/78) are accepted; non-power-of-two sizes (369 in the corpus) are stored padded to whole 8x8-block tiles, the layout the un-tiler assumes; array slices stored back to back are read by `ps4_deswizzle_slices`; un-tiling uses NumPy when available with output identical to the pure-Python fallback. A full decode of all archive textures through the export path completes without failures (research run, about 18 minutes).

Texture colourspace conventions (for exports and Blender): sRGB DXGI formats are 29, 72, 75, 78, 91, 93 and 99; everything else is data.

## Flash UI tab

The game's menus are Scaleform GFX files: SWF with a `GFX` signature, version 12-24. The archive holds 53 of them, as types `gfx` and `swf` under `ui/export/`. They are vector art, so the Textures tab cannot show them.

- The file list loads on first view (or with **Load list**) and opens `ui/export/pause/map.gfx`.
- The symbol list shows the main timeline, sprites and shapes, filtered by default to exported or class names.
- **Label** and **Frame** choose a timeline frame. **Background** (Checker, Dark, Light) changes only the preview.
- **Export frame...** and **Export all labels...** write transparent PNGs at the chosen size. **+ original .gfx** also copies the file's exact bytes.

### Renderer (`rc2016/flash/gfx.py`)

A purpose-written renderer with no external dependencies. It parses:

- DefineShape 1-4: solid, linear and radial fills, line styles.
- DefineSprite timelines with PlaceObject 1/2/3 and RemoveObject: matrices, colour transforms, clip-depth masks, frame labels.
- SymbolClass/ExportAssets names, and ImportAssets resolved by file name against other archive UI files.

Fills are built from SWF's left/right fill edges, chained into loops and drawn even-odd at 2x supersampling. All 53 files parse and all 737 named symbols render.

Not drawn: text fields and fonts; filters and blend modes; morph shapes; ActionScript and its runtime colours; nested sprites' own animation (children show their first frame); bitmap fills (drawn grey: 12 `DefineExternalImage2` references across 8 files point to original export `.tga` names that the game substitutes at runtime).

### Planet maps

The in-game planet maps are in `ui/export/pause/map.gfx`, symbol `Map_fla.map_image_asset_holder_6` (id 1253), which has one labelled frame per map: `kPokitaru`, `kMetropolis`, `kNebula`, `kAridia`, `kBatalia`, `kDeplanetizer`, `kGaspar`, `kKalebo`, `kNovalis`, `kPhoenix`, `kQuartu`, `kRilgar`, `kVeldin`, `kDeplanetizerExterior`, `kDeplanetizerNefarious`. They render in their authored grey; the game recolours areas at runtime from ActionScript, which is not run. Each planet's `gp_map.zone` holds the map-section volumes and the map camera.

**Export planet maps (game colours)...** in the tab, or:

```powershell
python -m rc2016.flash.planet_maps "<extracted game folder>" "<output folder>" [--size 4096] [--no-seams]
```

writes one transparent PNG per map (`rc2016/flash/planet_maps.py`).

- Every named area of the `land` sprite is filled with the visited colour `#AFDAFF`. Areas marked `kSecret` in `configs/system/system_<planet>_map.config` get the secret colour `#AFFFE4`.
- **Colours:** the map script declares `_visitedColor`, `_secretColor`, `_inaccessibleColor`, `_unvisitedColor`, and its constants include `0xAFDAFF`, `0xAFFFE4`, `0xA87373`, `0xFFFFFF`. The script was not decompiled; the pairing of fields to values comes from sampling an in-game screenshot (research notes, not included).
- **Secret areas:** config range ends map to instance names (`kVEL_1_16b` becomes `area1_16b`, `kMET_4_05c_secret` becomes `area4_05c_secret`). Ranges are expanded by natural name order; the game's own section order was not decoded. Gaspar's `kGAS_3_04c` has no matching shape in the Flash file.
- **Section lines** are on by default. They are not drawn in the Flash file (only 4 of 593 area shapes have strokes); in game they are anti-aliasing seams between separate shapes. `render_with_seams` grows each area mask by half the line width and darkens pixels covered by two grown masks by x0.7, at a line width of about 1/800 of the long side (measured on a screenshot). Outer edges stay clean. A full export with seams takes about 5 minutes.
- 12 unnamed pieces across the maps plus the template sprite #93 are omitted (stray dots under 3 px and two hairline slivers; not the section lines).
- Areas are exported opaque. The game blends them over the background at roughly 80% opacity and switches visited/unvisited/inaccessible colours from save progress.

## Details tab

`source_details` describes the selected model from the game files: archive path, ID, actors that use it, static or skinned (with joint count), mesh records and looks, vertices and triangles of the shown look/LOD, every material slot with its material file and texture count, its animation sets and clip count (found through the output root's reference index), and the packed source hash. A model that fails to decode shows the decoder's reason.

When the output root holds a build of that model, its build and reopen status, integrity, limitations and clip support follow under "Build in the output root". COMPLETE there means delivery coverage, not game accuracy. Saved results are read only from the output root, reloaded when it changes and after each batch; large master and validation-report hashes are checked in a worker.

## Asset links

`reports/asset_references.sqlite` records only actor -> model references. The asset links table adds the rest of the archive's reference graph. `rc2016/formats/links.py` (`build_links`, `AssetLinks`) builds it, and `rc2016/app/links.py` (`LinkService`) gives the browser access to it.

| Link | Source |
| --- | --- |
| model -> material | material slots, in file order |
| material -> texture | direct texture references |
| material -> material graph | graph references |
| graph -> texture | graph defaults |
| zone -> model | the zone's model-instance table, with the instance count |
| level -> zone | the `.level` zone list |

The table is built once per archive on a worker thread (about 20 seconds, about 20,000 assets read, no failures on the EUR archive) and cached as `<repo>/.cache/shared_index/links_<identity>.sqlite`. The identity covers the archive layout and the source of `build_links` and its schema, so a different archive or a change to what is stored rebuilds it. Unreadable assets are recorded in the cache's `errors` table.

- **Textures tab:** a **Used by** row under the export row lists the models whose materials reference the selected texture. It is marked "(graph default)" when only a material graph's default reaches the texture. **Open model** selects that model in the tree and shows the 3D model tab.
- **Details tab:** "Placed in levels: N instances in Z zones of L levels", with one line per zone (`level · zone: count`, at most 12 lines, most instances first). A zone that no `.level` lists is marked as such.
- **Level viewer:** a click identifies a model and **Open in Model tab** selects it in the tree (see [levels](levels.md)).
- `AssetBrowser.open_model(path)` clears the search, State and Shading filters if they hide the model.

## Portable export (Extract selected)

**Extract selected** exports portable files by default and needs no Blender. `ModelExporter` and `ExportOptions` (`rc2016/export/models.py`) run in the GUI process with their own `SourcePreviewLoader`, so they use the same decoders as the 3D view. `load(..., purpose='export')` decodes geometry (list-valued meshes), skeleton data and material paths only; it skips the viewer's texture decode, texture list, headers, rule coverage, native translation and viewport arrays. The GUI runner is `ExportBatchRunner` in `rc2016/app/batch.py`.

One folder per model in the output root:

| Output | How |
|---|---|
| `<model>.glb` (default), `<model>.gltf` + `.bin`, `<model>.obj` + `.mtl` | Written directly |
| `<model>.fbx`, `.usdc`, `.blend` | Converted from the GLB by `rc2016/export/blender_convert.py` in a background Blender |
| `textures/` | Material PNGs: `_basecolor`, `_metal_rough`, `_normal`, `_emissive` (OBJ also `_roughness`/`_metallic`); optionally `textures/game_dds/` with the original DDS. Only material maps of polygon-used slots are written |
| `export.json` | Source ID/path/hash, options, files, clips (and skipped clips with reasons), per-material texture sources and notes |

**Coordinates and skeleton:** Y-up in game units (glTF's convention), game UVs, textures never flipped. GLB/glTF carry the skeleton (local TRS derived from `worlds()`, so either inheritance rule is reproduced), inverse binds, up to 8 influences (two JOINTS/WEIGHTS sets), and every playable clip as a glTF animation (additive clips standalone, as in the viewer). OBJ is the static rest pose.

**Blender conversion** uses factory settings, `disable_bone_shape=True` (otherwise FBX/USD export the importer's bone-display icosphere as a mesh) and a private user folder. The `.blend` gets `apply_new_scene_defaults(configure_world=False, configure_gpu=False)` (render defaults only: no HDRI world, no OptiX device setup) and packed images. These files carry glTF Principled materials, not the RC PS4 Deferred BRDF; use the research build for that.

**Materials** follow the viewer's per-graph `_g` reading (`material_texture_paths`: grey, skin or coloured, spec/gloss scales, self-lit K and mask):

- The game's diffuse and F0/gloss are converted per pixel to metal/roughness with the Khronos specular-glossiness conversion; roughness = 2^(-2.75 x gloss).
- Self-lit = `_c.rgb x mask`, exported with `KHR_materials_emissive_strength` = K.
- Normals are reconstructed from RG without a green flip.
- Graph extras (tints, palettes, detail maps, rims, glass, animated terms) are not baked. Forward blend materials (alpha-over or additive) get a fixed 40% opacity. The bulb graph's facing glow becomes a constant emissive (0.497, 0.021, 0) with a white base, because glTF has no view-dependent emission.
- This is a portable approximation, not game equivalence.

**Options...** (in the main window) holds the formats, textures, game DDS, animations (all/none), LOD, skip-existing (keeps a folder whose `export.json` matches source and options), Python and Blender paths, the advanced research-build switch, backend and clip prefixes, and UI scale. Export choices are saved in the settings file.

**Single clip:** pick a clip under the 3D view (the Animation button opens the searchable list) and tick **Extract only this clip**; it applies to the model shown there.

## Research Blender build and viewer rules

Options -> Advanced selects the full research Blender scene (`PipelineBatchRunner`; backend "Full archive pipeline"), which runs the pipeline in [pipeline](pipeline.md) and needs Blender. With **Keep only the .blend files** ticked, a fully successful run deletes everything else in the output folder (reports, meshes, textures, caches); every run then rebuilds, since nothing is left to reuse. With **Extract only this clip** ticked, the shown model gets its own CLI run with `--clip name:<clip>` and the other selected models run without it.

The research build consumes the viewer's material reading:

- `rc2016.formats.materials.viewer_graph_rule` stores the viewer's reading of each material (from `rc2016.viewer.textures.material_texture_paths`): `_g` layout, spec/gloss scales, effective K (for example K x `0x5FD56522` for emissive-detail graphs), mask channel, and the exact `_c/_g/_n` paths.
- For materials without a profile, `rc2016.pipeline.geometry._material` applies `rc2016.pipeline.game_shader.apply_viewer_graph_rule` before native translation when preflight selects `viewer_graph_rule_frontend`, and tries it as a fallback after translation and registered family handling otherwise. The front-end wires its material terms into the shared deferred BRDF, with the closure limits described in [shading](shading.md).
- The front-end also builds the graph terms the viewer draws, with the viewer's frozen values: colour pick, colour tint and its variants, specular tint, arch emissive sequence, vehicle palettes, glow masks, two-colour metal, ground puddles, foliage and `za` normal z, the leaf cut-out (a transparent mix on `_c.a >= threshold`, threshold from the material render state), `za` rim suppression, the forward screen, rock blend, and the `basic_normal_gloss` variant terms and detail normals. Constants are `VR <term> <param>` nodes; extra images are bound, hashed source textures. A term whose image is not bound stays in `rule['unported']` with a reason, also recorded on the material as `rc_shader_frontend_limitation`.
- **Precedence:** when no registered family applies, the viewer rule wins over a ready native translation if it has no unported term or only parallax is unported (`materials.py` decides `shader_status`, `geometry._material` follows). Any other unported term gives way to the translation.
- `shader_status` becomes `viewer_graph_rule_frontend`. `rc2016.shaders.validation` checks after reopening: the rule; BRDF to active output; `_c/_g` paths, hashes and native colourspaces; scales and K; every `VR` constant against the rule; and each ported term's constants and images reaching the output. These checks validate stored values and node wiring; they do not establish identical closures or rendered output.
- `rc2016/pipeline/run.py` hashes `rc2016/viewer/textures.py` into the implementation fingerprint, so a rule change invalidates reused builds.
- The bulb graph's facing glow is a `VR facing glow emission` through an Add Shader in Blender.

Output-folder hygiene (`run.py`): only `work/` and `reports/logs/` are created up front; other folders only for a real build. A model-list scan prunes the unpacked actor files it indexed, and every run deletes its lock file and empty folders. `--blend-only` (keep only the delivered `.blend` files) runs after the log and lock are closed, and only after a fully successful run. `prune_intermediates` uses Windows extended-length paths, because unpacked asset names can exceed 260 characters. A scan leaves only `reports/` (the archive index, needed to find animation sets).

### Optional Blender scene review

For saved Blender scenes the output root can drive an isolated review (`rc2016/pipeline/review.py`, `rc2016/app/catalog.py`):

- **Open Blender** uses an explicitly configured executable. **Artifacts / logs** opens the result location.
- A proof uses a hash-pinned request, a new `reviews/<UUID>/` directory under `.cache/`, and a fresh Blender process with `--disable-autoexec`. It never saves the delivered master or overwrites reports, preserves the saved camera, and restores and compares in-memory state. It reads the scene's embedded controls (persistent source actions with source FPS and frame bounds, recorded variant/LOD selectors) or the sealed legacy-controls API for older scenes; unknown control text fails closed. Combined variant plus LOD switching has no validated contract and is rejected. Additive clips are standalone source previews.
- Proofs use the shared review lighting, transparent 960x640 RGBA, 32 samples, Cycles OptiX with CPU disabled. Missing rendering prerequisites fail explicitly; no global preferences are saved.
- Review work uses bounded background queues, one owned process, stale-result tokens, cancellation and double-submit guards. Closing the UI cancels its review process; unrelated Blender processes are never stopped.
- The Blender proof controls are built but not shown in the notebook, and a proof never substitutes for the 3D model.
- Catalog discovery lists a passed `runs/<run>/variant_assemblies/<name>/assembly_receipt.json` (schema `fusion-source-linked-assembly-cache-v1`, exactly one matching input) under its V1 source identity, labelled "(V1/V5 assembly)"; master and validation hashes are verified on selection.

## Gallery and thumbnails

The **Gallery** tab shows rendered thumbnails of the models selected in the tree (selecting a folder selects every model inside it) or, with nothing or one unlisted model selected, of the current search results (capped at 2000, with a note). A single click selects the model in the tree and loads it in the 3D view; double click or Enter also switches to the **3D model** tab. Arrow keys, Page Up/Down, Home/End and the wheel move through the grid. Hovering a tile shows its path, ID and, for a failed thumbnail, the reason. Without an Output folder the tab only asks for one.

- **Grid:** one Tk canvas that draws only the visible rows; decoded images sit in a 300-entry LRU. Tiles are the native 192 px image; spacing and labels follow the UI scale. The label is the first named actor that uses the model, else the file name.
- **Renderer:** `rc2016/app/thumbnails.py` runs as one child process (never more than one), so the Tk thread and 3D view are not blocked and it can be cancelled. It draws the model in Shaded mode, Highest LOD, look 0, bind pose, with the same framing as **Reset view**, into an offscreen RGBA framebuffer (GL 3 or EXT framebuffer objects) at twice the size, then box-filters down by alpha weight. The background is transparent, so tiles suit light and dark themes. If framebuffer objects are unavailable it reads the window back buffer instead and the background is opaque grey. It skips the streamed HD texture mips (this process only; the interactive viewer is unchanged).
- **Queue:** missing thumbnails for the visible tiles are rendered first, then the rest of the list. The gallery restarts the renderer only when a visible tile is not near the front of its queue. **Render all thumbnails** renders every archive model (progress in the log, **Cancel** stops it).
- **Cache:** `<output root>/.cache/thumbnails/<model id>.png` plus `index.json`, keyed by model id with `source_hash` (SHA-256 of the packed model bytes, as in Details), `renderer_version`, `size`, `status` (`ok`, `unsupported`, `error`) and `reason`. A result is reused until the source hash, `THUMBNAIL_VERSION` (bump it when rendering changes) or the size differs; failures are recorded and not retried before that. PNGs and the index are written atomically; `renderer.log` holds the child's stderr.
- **CLI:** `python -m rc2016.app.thumbnails --source <game root> --output <output root> [--ids-file F] [--all] [--size 192] [--limit N] [--force] [--no-fbo]`. The ids file lists model ids in priority order; `--all` appends every other archive model. One JSON line per model (`event`, `id`, `status`, `reason`, `seconds`) goes to stdout.

## Settings and caches

- Settings: `%APPDATA%/RC2016 Asset Browser/settings.json` (source and output folders, UI scale, export options, Blender and Python paths). Settings saved under the former name `PS4 Animation Extractor` are copied once.
- `<repo>/.cache/shared_index`: parsed `layout.csv` and the shared index sidecar.
- `<repo>/.cache/source_preview`: the viewer's archive working cache.
- `<repo>/.cache/texture_preview`: decoded texture top mips for the model and level viewers (at most 2 GB).
- `<repo>/.cache/shared_index/file_hashes.json`, `located_roots.json`: file hashes by size and modification time, and the located archive root per game folder.
- `<repo>/.cache/reviews`: isolated Blender review runs.
- `<repo>/.cache/environment`: shared HDRI lighting maps (`<hdri hash>_v<version>.npz`).
- `<repo>/.cache/gl_programs`: linked GLSL program binaries, per driver ([native translation](#native-translation)).
- `<repo>/.cache/level_geometry`: decoded level-viewer model geometry ([levels](levels.md#loading)).
- `<repo>/.cache/shared_index/links_*.sqlite`: archive-wide asset links, one per archive identity.
- `<output root>/.cache/thumbnails`: gallery thumbnails and their index.
- Output root: `reports/asset_references.sqlite`, the model index, saved builds, `export.json` folders.

## Tests

```powershell
python -m unittest discover -s tests -t .
```

Data-dependent tests read the `RC2016_RESEARCH` environment variable and skip without it.

## Limitations

- The 3D view shows the bind pose or a played clip, not attachments or runtime blending. Lighting is the viewer rig or the shared review HDRI, with no shadows, ambient occlusion or interreflection in the standalone model viewer; the level viewer adds the scene previews described above. Neither selector reproduces the game's lighting. Materials follow the rules above.
- The rule coverage and Shading filter describe the viewer's rules, not game accuracy.
- Several graph rules rest on bounded decodes; runtime draw-time inputs (time, instance interpolants, constant-buffer binding) are frozen or unproven.
- Forward materials other than the character glass, saucer dome, screen, puddle and bulb graphs, and the water preview, draw opaque.
- Portable exports approximate materials; they are not game-accurate.
- Native mouse input and the packaged executable's window have been checked by hand only, not by automated tests.
