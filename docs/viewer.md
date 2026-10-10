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
| `rc2016/app/catalog.py`, `rc2016/app/workers.py` | Read-only result catalog; coalesced background workers and owned-process cleanup |
| `rc2016/viewer/loader.py` | `SourcePreviewLoader`: archive model to viewport payload |
| `rc2016/viewer/viewport.py` | Windows OpenGL viewport (ctypes, no extra packages) |
| `rc2016/viewer/shading.py` | GLSL 1.20 programs for Shaded mode and the glass/forward path |
| `rc2016/viewer/textures.py` | Texture decoding/upload, material slot tables, `GRAPH_RULES`, texture export |
| `rc2016/viewer/animation.py` | Clip library and skinning matrices |
| `rc2016/levels/*` | Level panel and viewport ([levels](levels.md)) |
| `rc2016/flash/tab.py`, `gfx.py`, `planet_maps.py` | Flash UI tab, SWF/GFX renderer, planet-map export |
| `rc2016/export/models.py`, `blender_convert.py` | Portable export |

## Launching

Requirements: Windows with an OpenGL-capable GPU and a regular Python 3.11+ with Tkinter. The 3D view uses only the standard library (OpenGL through ctypes). Optional packages: Pillow (Textures tab, PNG export; imported lazily), NumPy (faster model preparation and texture un-tiling), `lz4` (faster archive decompression). If OpenGL initialisation fails, the view shows a placeholder explaining why.

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
| Model (default) | Search/filter row and model tree | Notebook with **3D model**, **Textures**, **Details** |
| Level | The archive's levels; a typed `.level` or `.zone` path plus **Load** below the list | Zone toggles, view options, level viewport ([levels](levels.md)) |
| Flash UI | The archive's 53 `.gfx`/`.swf` files | Symbol list, canvas, frame and export controls |

The level list and the Flash UI list are read the first time their type is selected.

## Model tree, search, used-by

- The tree groups models by archive folder. A folder that holds one model and no subfolders is skipped (`folder_layout`). Selecting a folder selects every model inside it.
- Models are named by the named actors whose files reference them (`actor_users()`, one level of parent actors included), read from `reports/asset_references.sqlite`. The numbered moby/tie/shrub assets of the first game's data carry no name anywhere in the game data (their own materials are numbered too), and almost none is used by a named actor.
- The search box matches path, ID, actor names and animation-set names. Several words must all match.
- The State filter (All, READY, EXPORTED, VERIFIED, STALE, NO ANIMSET, PREVIOUS) reflects saved builds in the output root; EXPORTED marks folders that hold an `export.json`. Browsing does not approve backend cache reuse.

## 3D view

Select one source row to load its geometry. Archive metadata is loaded once; only the selected model's bytes and geometry are decoded, in a background worker that coalesces pending selections.

**Controls:** left drag orbits, right drag pans, the wheel zooms whenever the pointer is over the view, **Reset view** or double click restores framing. Redraws are scheduled once per idle pass (`_schedule_draw`) and redrawn after Tk's `<Expose>`/`<Map>`, because the view has no Tk background and Tk would otherwise paint over the GL output.

**View modes** (`ModelViewport.MODES`): Shaded (default), Textured, Solid, Wireframe, Solid + wireframe.

**Look and LOD:** the source look and Highest / Lowest / All LOD selections use the model's native memberships.

**Pose:** the model is drawn in its bind/rest pose until an animation is played. Attachments and runtime animation blending are not reproduced.

**Unsupported layouts** show the decoder's reason and clear the previous geometry. Loose-file transform mode has no native archive identity and says so.

**Textured mode** draws, per material slot, the `_c` texture that the material references directly (otherwise via its material graph), with the decoder's UV0 (V flipped back). The block-compressed data (BC1-BC7) is read in memory with the pipeline's header parser and un-tiler and uploaded unchanged via `glCompressedTexImage2D`, so the GPU decodes it. The streamed full-size mip is used when the built-in mip is under 512 px (`MIN_PREVIEW_SIZE`; about 0.25 s per 1024x1024 texture to un-tile). sRGB formats are uploaded as their UNORM twins, because the fixed-function path lights in display space. This mode is base colour only (no gloss, normal, self-lit mask, tint or shader); slots without a `_c` are grey. The numbered first-game mobys point at a 2x2 `default_pink_shader_c` and show pink.

**GPU buffers:** vertex, normal, UV, skin, rock-alpha and index arrays are uploaded once to GL buffers on first draw (`ModelViewport._vptr`). A new mesh frees the old buffers and bindings are reset after each frame. Without the GL 1.5 entry points every pointer falls back to client memory; both paths give identical framebuffers.

**GPU skinning** takes up to 8 influences. Bone matrices are read from a 1-row RGBA32F texture, because a uniform array failed to link above about 200 joints on NVIDIA (C6020). NVIDIA aliases built-in vertex attributes, so skin attributes use slots 1, 6, 7 and 9.

**Loading speed:**

- The archive opens from the pipeline's cached parsed `layout.csv` (`.cache/shared_index`; see [pipeline](pipeline.md), "Archive loading speed"). With the optional `lz4` package, LZ4 blocks decode in C.
- `prepare_viewport` builds the GL arrays with NumPy and falls back to the pure-Python `validate_mesh`, which also supplies the exact error message for an invalid payload. Both paths produce identical arrays.
- The remaining time is mostly the shared native decoder (`rc2016.formats.native_model.decode_model`); a 550k-vertex prop loads in a few seconds and models under about 20k vertices draw in under 1 ms per frame.
- **Recent models** (`SourcePreviewLoader._recent`): the last 6 decoded models (at most 1.5 million vertices in total) are kept with their animation libraries, so switching back or changing look or LOD is nearly instant. The key includes the output root, because animation sets are found through it. Setting `loader.identity = None` forces a fresh decode.
- **Animations after the mesh:** the browser calls `load(..., defer_animation=True)`. A skinned model returns at once with `animation_pending=True` ("Loading animations..."), then `load_animation()` builds its clip library on a separate worker outside the loader's main lock. The clip menu and Details fill in only if the same model is still shown. Other callers keep the eager default.

## Shaded mode

Shaded (`rc2016/viewer/shading.py`) is one GLSL 1.20 program on the legacy WGL context. It follows the game's deferred BRDF (see [shading](shading.md)):

- diffuse = `_c`;
- specular = GGX with Schlick Fresnel, F0 from `_g`, F90 = sat(60 x F0), GGX alpha = 2^(-5.5 x gloss) (roughness = 2^(-2.75 x gloss)); diffuse and specular are added;
- self-lit = `_c.rgb x _c.a x K`;
- `_n` is read as an RG tangent normal without a green flip, with the tangent frame taken from screen-space UV derivatives.

The lighting is a fixed viewer rig (key, fill, sky/ground ambient, an ACES fit), not the game's lighting. The skin Fresnel lerp is not applied.

### `_g` meaning per material graph

The `_g` channel layout depends on the graph's decoded writer. `GRAPH_RULES` in `rc2016/viewer/textures.py` registers decoded rules by exact full graph stems ending in a dot before the `.materialgraph` extension. These rules precede the retained skin, incandescence and `basic_normal_gloss` family fallbacks; the first matching entry wins. Graphs without a rule use the generic reading: coloured F0 = spec x `_g.rgb`, gloss = sqrt(sat(gloss x a^2)), where `a` is `_g.a`.

The specular and gloss scales (keys `0x62835AF9` and `0x24F4CA4C`) come from the material, else from the graph default. K (self-lit strength) comes from the self-lit key in the graph's own parameter table, because materials can store keys their graph never reads (for example writer `C4828176` uses only `0x06F19114`). Self-lit keys by family: `0x66EFA55B` (default deferred), `0x06F19114` (coloured-gloss-emissive), `0xF8C61683` (emissive in `_c` alpha), `0x5FD56522` (incandescence).

"Grey" below means F0 = grey spec x `_g.G` and gloss = sqrt(sat(gloss x `_g.R`)). Writer IDs are the hashes of the decoded shader writers; the decode notes are research material (not included here).

| Graph | Reading |
|---|---|
| `basic_skin/*` | R = gloss (sqrt), G = grey specular capped at 0.5. Coloured F0 would give red specular on skin |
| `basic_normal_gloss`, `_detail`, `_monitor_dep`, `_aniso` (writer `5EBDECA4`) | Grey, no self-lit |
| `basic_incandescence` | Grey; self-lit = `0x5FD56522` x `_g.B` |
| `basic_normal_gloss_emissive`, `_emissive_detail`, `_fresnel_detail`, `_emissive_colored_spec`, `_aniso_incan`, `_specfresnel_emissive` | Six separately traced writers: grey, with graph-specific tint, glow, rim and detail terms; detail uses texture unit 11; pulses show t=0 |
| other `basic_normal_gloss*` | Undecoded writers: grey by name only, K from the graph's own table |
| `blarg_saucer_colorpick` (writer `66744D01`) | Grey; self-lit = `0x06F19114` x `_g.B`; `_c.a` blends towards the 8x1 `_colorpick_c` palette |
| `mo_gbl_color_tint_01` (writer `BE8C3EE0`) | Switch `0x7107A2D8` < 0.5 (every corpus material) reads `_g` as grey, otherwise coloured; F0 x specular tint `0x6B591974`; no self-lit; albedo tinted towards colours A/B using the `_h` mask (unit 5) |
| `jn_mo_gbl_color_tint_01_random_color`, `jn_color_tint_detail`, `jn_gbl_color_tint_01_world_detail`, `..._world_detail_normal` | Grey, no self-lit; tint, palette and detail terms; instance palette magnitude fixed at 0.5, world origin 0; unit 9. Parallax variants have no rule |
| `za_basic_normal_gloss` (writer `E08357DC`) | Grey; spec and gloss suppressed by a geometry-normal view-angle term (exponent key `0x4D385B45`); scales clamped before the texture multiply; no self-lit |
| `basic_normal_two_color_gloss_metal` (writers `1012E99D`/`EC1E7CD3`) | Grey, no self-lit; forward diffuse and specular tints and rim gloss, with viewer light, no SSR or fog, instance opacity 1 |
| `mo_gbl_arch_emissive_sequence_01` (writer `AAED778C`) | Preview at CB0[99]=0 and zero instance interpolants; grey G as F0, gloss sqrt(sat(5R)), colour/base-mask albedo and sequence/mask glow; stale K and `_c` ignored; unit 8 |
| `mo_veh_random_color_01` (writer `EB1AC2CD`) | UV0.x selects two linear palettes (second on unit 10), paint mask, view-dependent RGB tints; constant packed F0 (0, 0.0999756, 0.0999756), gloss 0.948681; no self-lit; forward flake slice omitted |
| `basic_parallax_occlusion` | Grey, no layout switch or self-lit; C/G/N bound by serialized slots; the source height march (16-64 steps) and height shadows are omitted |
| `jn_basic_normal_gloss_glow_mask` (`2685144F`), `_02` (`CA81B14F`) | Grey, K `0x06F19114`; first: mask/tint/environment and offset-parallax glow; `_02`: `c.R x mask x K` with a vertex factor; unit 13 |
| `mo_blend_01` (writer `886AAC94`) | Blends two source-resolved c/g/n layers through H, squared vertex alpha and sharpness; grey, no self-lit; unit 15; source vertex alpha when available, otherwise 1 |
| `basic_foliage` | Four deferred writers: grey, no self-lit, no subsurface output; normal z = 1 - x^2 - y^2; runtime alpha cutoff, dither and normal fade omitted |
| `mo_neb_amb_screen_01` (forward `8716DC7A`) | Source timeline/grid, mask, colour/intensity and scroll noise at CB0[99]=0 with frozen cutoff, alpha and fog; units 0/1/2/4 |
| `jn_ground_puddles` (forward `DB75867B`, no-normal `4A6CDAAA`) | Source noise thresholds drive grey F0, gloss and diffuse attenuation; t=0 ripple normal and environment-reflection blend; noise/ripple atlas on unit 14. The emissive sibling stays generic |
| `npc_qwark_robot_bulb` (exact graph, forward `0CD2AAFB`) | White base (no albedo input) plus the writer's red facing glow at amplitude CB0 dw115 = 0.5 (captured 0.01; blink timing not decoded). Reads as white with a pink-to-red rim, not saturated red |

Ground puddles use the exact graph path and serialized C/G/N/noise/ripple slots. Generic specular and gloss keys are ignored because neither source parameter table declares them. Noise is stationary; CB0[99] scrolls ripple V. Each sampled normal is normalized, then the puddle blend is left unnormalized. Its forward terminal composition retains the reflection-mix parameter and specular clamp, with the viewer rig supplying light/specular/environment inputs. Origin and instance-axis scales are frozen to zero and one, normal fade is zero, geometric AA and opacity are one, and SSR, fog attenuation and debug outputs are disabled. These are explicit preview inputs; runtime pass selection, descriptor bindings, face polarity and engine lighting are unverified. The atlas preserves native tile sizes with repeat gutters and uses linear top-mip sampling. The Blender mirror ports mask, D/F0/gloss and ripple-normal terms, then uses the deferred BRDF; it records that this GL forward reflection mix, specular clamp and fog composition are not reproduced.

Limitations that apply across the table: viewer lighting, ambient, TBN and instance values are substitutes for the game's runtime inputs; time-driven terms show t=0; runtime draw, constant-buffer and descriptor binding is unproven for several writers (the two-colour metal, vehicle and screen graphs); variable-intensity hero materials are left unruled pending the runtime phase inputs. Base textures use units 0/1/2, skinning uses vertex unit 3, and palettes use unit 4. Graph extras use units 5 (tint mask), 8 (arch sequence), 9 (tint variants), 10 (second vehicle palette), 11 (basic_normal_gloss detail), 13 (glow mask), 14 (puddle atlas) and 15 (rock blend).

### Glass and forward materials

Graphs with a `forward` rule are drawn after every opaque batch with a second program (`GLASS_FRAGMENT`, `Shading.glass_program`, `ModelViewport._draw_glass`).

- **Draw state** comes from the material's render-state flags word: `.material` section `0xE1275683`, u32 at byte 16 (`material_flags`, `blend_state`). Bits 0-1 select blend (0 opaque, 1 additive, 2 alpha-over; 3 has no captured example); bit 2 set means two-sided. Additive is (`SRC_ALPHA`, `ONE`), alpha-over is (`SRC_ALPHA`, `ONE_MINUS_SRC_ALPHA`). With bit 2 clear, back faces are culled (the decoded meshes are counter-clockwise front).
- Depth write is off. Glass batches draw farthest first by their rest-pose centroid in view space (`split_forward`, `back_to_front`); triangles within a batch are not sorted.
- **`character_glass`** (Blarg visor, writer `34D5B21C`): rim alpha, tinted reflection without Fresnel, and body colour x (1 + environment rim). Parameters are read from the material, else the graph default.
- **`blarg_saucer_glass`** (saucer dome, writer `FEEC00CE`): the writer binds textures by slot hash, not suffix: the palette `_colorpick_c` and `Patterns_cma` in the `_n` slot. The material's `glass_c`/`glass_g` are never read (Textured mode still shows `glass_c`). Alpha = sat(0.9 + rim), so the dome is at least 90% opaque: dark amber 0.4 x palette x diffuse, grey F0 0.1 reflections, a faint pattern normal.
- **Approximations:** the engine's light loop, probes, screen-space reflections and fog are replaced by the viewer rig. Colour is tonemapped per fragment before blending (the game blends HDR before tonemapping). Instance fade is 0 and `attr0.w` is 1.
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

## Portable export (Extract selected)

**Extract selected** exports portable files by default and needs no Blender. `ModelExporter` and `ExportOptions` (`rc2016/export/models.py`) run in the GUI process with their own `SourcePreviewLoader`, so they use the same decoders as the 3D view. The GUI runner is `ExportBatchRunner` in `rc2016/app/batch.py`.

One folder per model in the output root:

| Output | How |
|---|---|
| `<model>.glb` (default), `<model>.gltf` + `.bin`, `<model>.obj` + `.mtl` | Written directly |
| `<model>.fbx`, `.usdc`, `.blend` | Converted from the GLB by `rc2016/export/blender_convert.py` in a background Blender |
| `textures/` | Material PNGs: `_basecolor`, `_metal_rough`, `_normal`, `_emissive` (OBJ also `_roughness`/`_metallic`); optionally `textures/game_dds/` with the original DDS. Only material maps of polygon-used slots are written |
| `export.json` | Source ID/path/hash, options, files, clips (and skipped clips with reasons), per-material texture sources and notes |

**Coordinates and skeleton:** Y-up in game units (glTF's convention), game UVs, textures never flipped. GLB/glTF carry the skeleton (local TRS derived from `worlds()`, so either inheritance rule is reproduced), inverse binds, up to 8 influences (two JOINTS/WEIGHTS sets), and every playable clip as a glTF animation (additive clips standalone, as in the viewer). OBJ is the static rest pose.

**Blender conversion** uses factory settings, `disable_bone_shape=True` (otherwise FBX/USD export the importer's bone-display icosphere as a mesh) and a private user folder. The `.blend` gets `apply_new_scene_defaults()` and packed images. These files carry glTF Principled materials, not the RC PS4 Deferred BRDF; use the research build for that.

**Materials** follow the viewer's per-graph `_g` reading (`material_texture_paths`: grey, skin or coloured, spec/gloss scales, self-lit K and mask):

- The game's diffuse and F0/gloss are converted per pixel to metal/roughness with the Khronos specular-glossiness conversion; roughness = 2^(-2.75 x gloss).
- Self-lit = `_c.rgb x mask`, exported with `KHR_materials_emissive_strength` = K.
- Normals are reconstructed from RG without a green flip.
- Graph extras (tints, palettes, detail maps, rims, glass, animated terms) are not baked. Forward blend materials (alpha-over or additive) get a fixed 40% opacity. The bulb graph's facing glow becomes a constant emissive (0.497, 0.021, 0) with a white base, because glTF has no view-dependent emission.
- This is a portable approximation, not game equivalence.

**Options...** (in the main window) holds the formats, textures, game DDS, animations (all/none), LOD, skip-existing (keeps a folder whose `export.json` matches source and options), Python and Blender paths, the advanced research-build switch, backend and clip prefixes, and UI scale. Export choices are saved in the settings file.

**Single clip:** pick a clip under the 3D view (the Animation button opens the searchable list) and tick **Extract only this clip**; it applies to the model shown there.

## Research Blender build and viewer rules

Options -> Advanced selects the full research Blender scene (`PipelineBatchRunner`; backend "Full archive pipeline"), which runs the pipeline in [pipeline](pipeline.md) and needs Blender. With **Extract only this clip** ticked, the shown model gets its own CLI run with `--clip name:<clip>` and the other selected models run without it.

The research build consumes the viewer's material reading:

- `rc2016.formats.materials.viewer_graph_rule` stores the viewer's reading of each material (from `rc2016.viewer.textures.material_texture_paths`): `_g` layout, spec/gloss scales, effective K (for example K x `0x5FD56522` for emissive-detail graphs), mask channel, and the exact `_c/_g/_n` paths.
- For materials without a profile, `rc2016.pipeline.geometry._material` applies `rc2016.pipeline.game_shader.apply_viewer_graph_rule` before native translation when preflight selects `viewer_graph_rule_frontend`, and tries it as a fallback after translation and registered family handling otherwise. The front-end wires its material terms into the shared deferred BRDF, with the closure limits described in [shading](shading.md).
- The front-end also builds the graph terms the viewer draws, with the viewer's frozen values: colour pick, colour tint and its variants, specular tint, arch emissive sequence, vehicle palettes, glow masks, two-colour metal, ground puddles, foliage and `za` normal z, `za` rim suppression, the forward screen, rock blend, and the `basic_normal_gloss` variant terms and detail normals. Constants are `VR <term> <param>` nodes; extra images are bound, hashed source textures. A term whose image is not bound stays in `rule['unported']` with a reason, also recorded on the material as `rc_shader_frontend_limitation`.
- **Precedence:** when no registered family applies and the viewer rule has no unported term, the viewer rule wins over a ready native translation (`materials.py` decides `shader_status`, `geometry._material` follows). A partial viewer rule gives way to the translation.
- `shader_status` becomes `viewer_graph_rule_frontend`. `rc2016.shaders.validation` checks after reopening: the rule; BRDF to active output; `_c/_g` paths, hashes and native colourspaces; scales and K; every `VR` constant against the rule; and each ported term's constants and images reaching the output. These checks validate stored values and node wiring; they do not establish identical closures or rendered output.
- `rc2016/pipeline/run.py` hashes `rc2016/viewer/textures.py` into the implementation fingerprint, so a rule change invalidates reused builds.
- The bulb graph's facing glow is a `VR facing glow emission` through an Add Shader in Blender.

Output-folder hygiene (`run.py`): only `work/` and `reports/logs/` are created up front; other folders only for a real build. A model-list scan prunes the unpacked actor files it indexed, and every run deletes its lock file and empty folders. `prune_intermediates` uses Windows extended-length paths, because unpacked asset names can exceed 260 characters. A scan leaves only `reports/` (the archive index, needed to find animation sets).

### Optional Blender scene review

For saved Blender scenes the output root can drive an isolated review (`rc2016/pipeline/review.py`, `rc2016/app/catalog.py`):

- **Open Blender** uses an explicitly configured executable. **Artifacts / logs** opens the result location.
- A proof uses a hash-pinned request, a new `reviews/<UUID>/` directory under `.cache/`, and a fresh Blender process with `--disable-autoexec`. It never saves the delivered master or overwrites reports, preserves the saved camera, and restores and compares in-memory state. It reads the scene's embedded controls (persistent source actions with source FPS and frame bounds, recorded variant/LOD selectors) or the sealed legacy-controls API for older scenes; unknown control text fails closed. Combined variant plus LOD switching has no validated contract and is rejected. Additive clips are standalone source previews.
- Proofs use the shared review lighting, transparent 960x640 RGBA, 32 samples, Cycles OptiX with CPU disabled. Missing rendering prerequisites fail explicitly; no global preferences are saved.
- Review work uses bounded background queues, one owned process, stale-result tokens, cancellation and double-submit guards. Closing the UI cancels its review process; unrelated Blender processes are never stopped.
- The Blender proof controls are built but not shown in the notebook, and a proof never substitutes for the 3D model.
- Catalog discovery lists a passed `runs/<run>/variant_assemblies/<name>/assembly_receipt.json` (schema `fusion-source-linked-assembly-cache-v1`, exactly one matching input) under its V1 source identity, labelled "(V1/V5 assembly)"; master and validation hashes are verified on selection.

## Settings and caches

- Settings: `%APPDATA%/RC2016 Asset Browser/settings.json` (source and output folders, UI scale, export options, Blender and Python paths). Settings saved under the former name `PS4 Animation Extractor` are copied once.
- `<repo>/.cache/shared_index`: parsed `layout.csv` and the shared index sidecar.
- `<repo>/.cache/source_preview`: the viewer's archive working cache.
- `<repo>/.cache/reviews`: isolated Blender review runs.
- Output root: `reports/asset_references.sqlite`, the model index, saved builds, `export.json` folders.

## Tests

```powershell
python -m unittest discover -s tests -t .
```

Data-dependent tests read the `RC2016_RESEARCH` environment variable and skip without it.

## Limitations

- The 3D view shows the bind pose or a played clip, not attachments, runtime blending or the game's lighting and materials beyond the rules above.
- Several graph rules rest on bounded decodes; runtime draw-time inputs (time, instance interpolants, constant-buffer binding) are frozen or unproven.
- Forward materials other than the character glass, saucer dome, screen, puddle and bulb graphs draw opaque.
- Portable exports approximate materials; they are not game-accurate.
- Native mouse input and the packaged executable's window have been checked by hand only, not by automated tests.
