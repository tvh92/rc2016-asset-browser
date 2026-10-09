# PS4 asset browser and result review

The Windows extractor now loads the selected source model into an interactive 3D viewport inside its existing Tk interface. Model selection requires neither Blender nor saved proof renders. Blender is an optional tool for requested `.blend` deliveries, opening saved scenes, and isolated Cycles proofs.

The latest user requirements are in `USER_STEERING.md`; they override the thumbnail proposal in the original `AGENT_PROMPT_20261007.md`.

## Launch

Run from `C:/Users/Tim/Documents/Bestanden/Downloads/Models/PS4 models` with a regular Windows Python installation that includes Tkinter:

```powershell
& 'C:/Users/Tim/AppData/Local/Programs/Python/Python314/python.exe' -B rc2016/app/main.py
```

Other users can use their own regular Windows Python. The interactive renderer uses Windows OpenGL through standard-library ctypes, without a web stack, Pillow, NumPy, Blender, or a thumbnail collection. The existing pipeline uses the repository's shared lighting configuration and bundled HDRI during CLI setup; keep those files with the project. The shipped executable/installer has not been rebuilt.

Choose **Full archive pipeline**, set the extracted source root and a separate output root, and click Scan. Leave **Blender (optional)** empty to browse. The validated source root here is `C:/Users/Tim/Documents/Bestanden/Downloads/Ratchet & Clank (EUR)/CUSA01073`.

Select one source row to load its actual geometry. Left drag orbits, right drag pans, the wheel zooms, and Reset view/double click restores framing. Source look and Highest/Lowest/All LOD selections use the native memberships. Archive metadata loads once; only selected model bytes and geometry decode in a background worker. The loader retains one decoded model and coalesces pending selections.

The viewport displays neutral shading in the source bind/rest pose. It does not reproduce game materials, playback, attachments, or runtime animation blending. Unsupported mesh layouts display the decoder reason and clear prior geometry. Windows/OpenGL initialization failure produces a clear placeholder. Loose-file transform mode has no native archive identity and explains that preview limitation.

The **Details** tab describes the selected model from the game files (`source_details`): archive path, ID, actors that use it, static or skinned with joint count, mesh records and looks, the vertices/triangles of the shown look/LOD, every material slot with its material file, texture count, and its animation sets and clip count (found through the output root's reference index), plus the packed source hash. A model that fails to decode shows the decoder's reason. When the output root holds a build of that model, its build/reopen status, integrity, limitations and clip support follow under "Build in the output root". COMPLETE is delivery coverage, not game accuracy. The Geometry / Source clips / Support list filters were removed on 2026-10-08: they read only saved build reports, so every unbuilt model was Unknown. Browsing does not approve backend cache reuse.

## 2026-10-09 portable export (Extract selected)

**Extract selected** now exports portable files by default; Blender is optional. `model_export.py` (`ModelExporter`, `ExportOptions`) runs in the GUI process through its own `SourcePreviewLoader`, using the same decoders as the 3D view. The GUI runner is `ExportBatchRunner` in `rc2016_asset_browser_gui.py`. One folder per model in the output root:
- `<model>.glb` (default), `<model>.gltf` + `.bin`, `<model>.obj` + `.mtl`: written directly. Native Y-up coordinates in game units (glTF's convention), game UVs, textures never flipped. GLB/glTF carry the skeleton (local TRS derived from `worlds()`, so either inheritance rule is reproduced), inverse binds, up to 8 influences (two JOINTS/WEIGHTS sets), and every playable clip as a glTF animation (additive clips standalone, as in the viewer). OBJ is the static rest pose.
- `<model>.fbx`, `.usdc`, `.blend`: converted from the GLB by `blender_convert.py`. It runs in a background Blender with factory settings, `disable_bone_shape=True` (otherwise FBX/USD export the importer's bone-display Icosphere as a mesh) and a private user folder. The `.blend` gets `apply_new_scene_defaults()` and packed images. These files have glTF Principled materials, not the RC PS4 Deferred BRDF; the validated research scene is still available (below).
- `textures/`: material PNGs (`_basecolor`, `_metal_rough`, `_normal`, `_emissive`, and for OBJ also `_roughness`/`_metallic`), optionally `textures/game_dds/` with the original DDS. Only material maps of the polygon-used slots are written.
- `export.json`: source ID/path/hash, options, files, clips (and skipped clips with reasons), per-material texture sources and notes.

Materials follow the viewer's per-graph `_g` reading (`material_texture_paths`: grey, skin or coloured, spec/gloss scales, self-lit K and mask). The game's diffuse + F0/gloss is converted per pixel to metal/roughness with the Khronos specular-glossiness conversion; roughness = 2^(-2.75·gloss). Self-lit = `_c.rgb × mask` with `KHR_materials_emissive_strength` K. Normals are reconstructed from RG without a green flip, as in the validated Blender builds. Graph extras (tints, palettes, detail maps, rims, glass, animated terms) are not baked, and forward blend materials get a fixed 40% opacity. It is a portable approximation, not game equivalence.

Checked on `npc_qwark_robot` (2026-10-09): all six formats written (6.5 s with Blender); the reopened `.blend` and re-imported FBX have 25 bones and 7 actions and deform; a Cycles proof shows the spring as grey metal (the research build shows it black, see below). The GUI flow (scan, select, clip picker, *Extract only this clip*, GLB + OBJ) was run end-to-end in a hidden test window, with screenshots checked by Claude only.

Options… (`AnimationExtractorApp._build_options_window`) holds the formats, textures/DDS, animations (all/none), LOD, skip-existing, Python/Blender paths, the advanced research-build switch, backend and clip prefixes, and UI scale; the export choices are saved in the settings file. To export one clip, pick it under the 3D view and tick **Extract only this clip** (`AssetBrowser.export_clip`); it applies to the model shown there. The Animation button opens a searchable folder list (`_open_clip_picker`) instead of cascaded Tk menus. The State filter gains **EXPORTED** (folders with an `export.json`).

**Full research Blender scene** (Options → Advanced) is the former behaviour (`PipelineBatchRunner`). With **Extract only this clip** ticked, the shown model gets its own CLI run with `--clip name:<clip>`, and the other selected models run without it.

**Viewer material rules in the research build** (2026-10-09): Blender may not show a material worse than the viewer.
- `materials.viewer_graph_rule` stores the viewer's own reading of each material (`texture_preview.material_texture_paths`): `_g` layout, spec/gloss scales, effective K (for example K × `0x5FD56522` for emissive_detail), mask channel, and the exact `_c/_g/_n` paths.
- `geometry._material` applies `rc_game_shader.apply_viewer_graph_rule` whenever no exact native translation or registered family applies. Before this, such materials got the colour-only preview. The front-end wires the RC PS4 Deferred BRDF the same way as the viewer's Shaded mode.
- Since 2026-10-09 (rule schema v2) the front-end also builds every graph term the viewer draws, with the viewer's frozen values: colour pick, colour tint and its random/detail/world/world-normal variants, specular tint, arch emissive sequence, vehicle palettes, glow masks 1/2, two-colour metal, ground puddles, foliage/za normal z, za rim suppression, the forward screen, rock blend, and the basic_normal_gloss variant tint/rim/self-lit terms and detail normals. Constants are `VR <term> <param>` nodes; extra images are bound, hashed source textures. A term whose image is not bound stays in `rule['unported']` with a reason, also on the material as `rc_shader_frontend_limitation` with the approximations. Per-term status, test models and proofs: [`../Materials/ViewerTermsBlender/README.md`](../Materials/ViewerTermsBlender/README.md).
- Precedence (2026-10-09): when no registered family applies and the viewer rule has no unported term, the viewer rule wins over a ready native translation (`materials.py` decides the `shader_status`, `geometry._material` follows it). Before this, 332 of 343 `mo_gbl_color_tint_01` materials used the translation, which rendered the battery cannon's black wall and tan coil white/grey. A partial viewer rule still gives way to the translation.
- `shader_status` becomes `viewer_graph_rule_frontend`. `shader_validation._validate_viewer_rule_material` checks after reopening:
  - the rule;
  - BRDF → active output;
  - `_c/_g` paths, hashes and native colourspaces;
  - scales and K;
  - every `VR` constant against the rule, and each ported term's constants and images reaching the output.
- `run.py` hashes `texture_preview.py` into the implementation fingerprint, so a rule change invalidates reused builds.
- Checked 2026-10-09 with research builds into a temporary root:
  - **Qwark robot** (one clip): the spring is grey metal and the chest symbol glows (K 30). The eyes use K 50.
  - **Toxic Crab:** its registered skin front-end is unchanged, with 36 clips.
  - **Laser gate:** unchanged.
  - **npc_brain_scientist:** registered and translated front-ends still win; the viewer rule covers the cornea and the coat.
- All of these delivered and passed reopen validation. The renders were looked at by Claude only.

The 3D view's frame label is wide enough for `165/165 @ 30 fps`.

**Output-folder hygiene** (`run.py`): only `work/` and `reports/logs/` are created up front. The other working folders are created only for a real build, a `--list-models-json` scan prunes the unpacked actor files it indexed, and every run deletes its lock file and empty folders (`remove_empty_dirs`). `prune_intermediates` uses Windows extended-length paths, because unpacked asset names exceeded 260 characters in deep output roots. A scan now leaves only `reports/` (the archive index, needed to find animation sets).

**Qwark bot antenna bulb** (2026-10-09, `../Materials/QwarkBulb/README.md`): exact-graph rule `facing_glow` in `texture_preview.GRAPH_RULES`. White base (no albedo input) plus the writer's own red facing glow at the presentation amplitude CB0 dw115 = 0.5 (captured 0.01; blink timing not decoded). Viewer: `gl_shading` `uFacingGlow*`. Blender: the deferred BRDF plus a `VR facing glow emission` through an Add Shader, checked after reopening by `shader_validation`. glTF/OBJ: white base and the head-on emissive constant (0.497, 0.021, 0), since glTF has no view-dependent emission. Because the glow is added to a lit white surface, the bulb reads white with a pink-to-red rim in both the viewer and Cycles, not saturated red.

## 2026-10-09 asset types

An **Asset type** selector (Model / Level / Flash UI, segmented buttons above the search row; `app.asset_type_var`, `AssetBrowser.show_asset_type`) chooses what the main area shows. Each type has its own list on the left and viewer on the right:
- **Model** (default): the search/filter row and model tree on the left, unchanged; on the right the review notebook now has only **3D model**, **Textures** and **Details**.
- **Level**: the archive's levels as a list (`LevelPanel.level_list`, `LevelPanel.levels`); selecting one loads it. A typed `.level`/`.zone` path plus **Load** sits under the list. The right side is the zone toggles, view options and level viewport.
- **Flash UI**: the archive's 53 `.gfx`/`.swf` files (`FlashTab.file_list`); selecting one opens it. The right side is the symbol list, canvas, frame and export controls.

Switching hides the other views without destroying them, so a loaded model, level or UI file stays loaded. The level list is read on the first switch to Level (as the old tab did on first visit); the Flash UI list is now also read on the first switch, which opens `ui/export/pause/map.gfx` as **Load list** did. The standalone `level_viewer.py` keeps the level list in its own side column. `smoke_level.py` switches through the selector.

## 2026-10-09 loading speed

- First model selection opens the archive in ~0.5 s instead of ~2 s, using the pipeline's cached `layout.csv` (see `docs/pipeline.md`, "Archive loading speed"). LZ4 decoding uses the C `lz4` package when it is installed.
- `prepare_viewport` builds the GL arrays with NumPy (`_prepare_viewport_numpy`) and falls back to the pure-Python `validate_mesh`, which also supplies the exact error message for an invalid payload. On 8 models (121–550,425 vertices, Highest/All LOD) the vertex/normal/index/UV/skin arrays, triangles, bounds and glass centres are byte-identical to the old path. `mesh_payload` output is unchanged. The model's section table comes from `animation_audit.section_table`, and `model_skeleton` no longer copies the rest of the file for each bone name; section tables match on 400 real models and skeletons on 69.
- Orbit, pan and zoom redraw through `_schedule_draw`, so a frame is drawn once per idle pass rather than per mouse event. Each draw waits for the display refresh (~7 ms/frame even at 550k vertices), so fast drags used to queue up.
- Measured loads: Toxic Crab 0.81 → 0.22 s, Sheepinator 0.94 → 0.12 s, `npc_brain_scientist` 1.3 → 0.5 s, 550k-vertex `gbl_prop_uiobject_deplanetizer_01` ~15 → 4.0 s. The remainder is mostly the shared native decoder (`native_model.decode_model`), which is unchanged.
- **GPU buffers** (`ModelViewport._vptr`): every vertex, normal, UV, skin, rock-alpha and index array is uploaded once to a GL buffer on first draw. A new mesh frees the old buffers, and bindings are reset after each frame. Without the GL 1.5 entry points every pointer falls back to client memory, the former path. Buffer and client rendering give identical framebuffers in 35 cases: 6 models × 5 modes, plus posed skinned clips and the saucer glass. Frame cost without vsync on the 550k-vertex prop: Shaded 6.9 → 4.5 ms, Solid 5.4 → 2.5 ms, Wireframe 11.8 → 0.9 ms, Solid + wireframe 17.0 → 3.1 ms; models under ~20k vertices were already < 1 ms.
- **Recent models** (`SourcePreviewLoader._recent`): the last 6 decoded models (≤ 1.5 M vertices in total) are kept with their animation libraries, so switching back or changing look/LOD takes ~0.02 s. The key includes the output root, because animation sets are found through it. Setting `loader.identity = None` from outside still forces a fresh decode, as proof scripts expect.
- **Animations after the mesh**: the browser calls `load(..., defer_animation=True)`. A skinned model returns at once with `animation_pending=True` and "Loading animations…", then `load_animation()` builds its clip library on a separate `clip_list_work` worker, outside the loader's main lock. The clip menu and Details fill in only if the same model is still shown. Other callers keep the eager default. Deferred clips, animation sets and notes equal the eager path (Toxic Crab: 36 clips). In the real Tk app, the mesh shows first and the clips follow ~0.08 s later.
- The `mesh_payload` triangle copy unpacks index triples directly (identical lists); the 550k-vertex prop now loads in 2.4 s with the vectorized decoder. Further gains need an array-based payload, which changes `mesh_payload`'s list contract (tests, proof scripts), so they need to be coordinated.
- `smoke_gui.py` still expects `rk_sky_nebula_stars` to be unsupported, but flag `0x0020` sky meshes decode since 2026-10-09 (`native_model.py`). Its earlier steps pass, but the script now times out at that check.

## 2026-10-08 usability pass

- **Blank 3D view fixed.** The WGL frame used to show nothing on screen even though `smoke_gui.py` passed: it reads pixels back from GL, so it never saw Tk repainting the frame background over the GL output. `ModelViewport` now has no Tk background and redraws after Tk's `<Expose>`/`<Map>` repaints (`_schedule_draw`). Checked visually on screen (source run).
- **View modes:** Textured (default) / Solid / Wireframe / Solid + wireframe selector beside Reset view (`ModelViewport.set_mode`). Mouse wheel zooms whenever the pointer is over the view (Windows sends wheel events to the focused widget, so the viewport listens with `bind_all`).
- **Textured mode** (`texture_preview.py`): per material slot, the `_c` texture the material references directly (else through its material graph), read in memory with the pipeline's own `parse_header`/`ps4_deswizzle_top` and uploaded unchanged as BC1–BC7 via `glCompressedTexImage2D`, so the GPU decodes it. The full streamed mip is used when the built-in one is under 512 px (~0.25 s per 1024² texture). sRGB formats upload as their UNORM twin because the fixed-function viewer lights in display space. Base colour only: no gloss, normal, self-lit mask, tint or shader; slots without a `_c` show grey. UVs are the decoder's UV0 with V flipped back. Checked on screen against `Other models/enm_toxic_crab/proofs/neutral_hdri_crop.png` (colours and placement match) and on the Sheepinator. The numbered rc1 mobys' materials point at the game's 2×2 `default_pink_shader_c`, so they show pink.
- **Folder tree:** the model list is grouped by archive folder; a folder holding one model and no subfolders is skipped (`folder_layout`). Selecting a folder selects every model inside it.
- **Used by / search:** `actor_users()` names a model by the named actors whose files reference it (one level of parent actors included), from `reports/asset_references.sqlite`; 1,955 of 6,949 models in CUSA01073. Numbered `rc1` mobys/ties/shrubs have no name anywhere in the game data (their own materials are numbered too); only 3 of 425 rc1 mobys are used by a named actor. The search box matches path, ID, actor and animset names; several words must all match.
- **Dark mode** follows Windows' app mode (`AppsUseLightTheme`), including a dark title bar.
- The packaged exe (`dist/`) runs the pipeline from this project folder with the installed Python (found automatically) and remembers folders in `%APPDATA%/PS4 Animation Extractor/settings.json`.

## 2026-10-08 shaded view and animation playback

- **Shaded** (default view mode, `gl_shading.py`): one GLSL 1.20 program on the legacy WGL context. Diffuse `_c` + GGX/Schlick specular (F0, F90 = sat(60·F0), alpha = 2^(-5.5·gloss)), added; self-lit `_c.rgb·_c.a·K`; `_n` as an RG tangent map without a green flip, tangent frame from screen-space UV derivatives. `_g` channel meaning follows the graph's decoded writer (`texture_preview.GRAPH_RULES`, first matching prefix):
  - `basic_skin/*`: R = gloss (sqrt), G = grey specular capped at 0.5. Treating skin `_g` as coloured F0 gave the known red pincers on Toxic Crab (see its README).
  - `basic_normal_gloss` (and `_detail`, `_monitor_dep`, `_aniso`, which embed the same writer `5EBDECA4`) and `basic_incandescence`: grey F0 = spec·G, gloss = sqrt(sat(gloss·R)), no self-lit for the former; incandescence self-lit = `0x5FD56522`·`_g.B`. Other `basic_normal_gloss*` graphs have undecoded writers and keep the grey reading by name only, with K from their own graph table (`Animation/pipeline/Research/Materials/CorpusMismatchScan/summary.md`).
  - `blarg_saucer_colorpick` (2026-10-08): the same grey layout, self-lit = `0x06F19114`·`_g.B`, and `_c.a` blends towards the 8×1 `_colorpick_c` palette (`Other models/enm_blarg_saucer/evidence/COLORPICK_DECODE.md`). It had been read as coloured F0 with K 22 on `_c.a`, which gave a gold-plated saucer with glowing stripes.
  - `mo_gbl_color_tint_01` (2026-10-08, 305 materials on 1,616 models; `Animation/pipeline/Research/Materials/ColorTint01/COLOR_TINT_DECODE.md`): switch `0x7107A2D8` < 0.5 (every corpus material) reads `_g` as grey, otherwise as coloured; F0 × specular tint `0x6B591974`; no self-lit; albedo tinted towards colours A/B with the `_h` mask (unit 5). It had been read as coloured F0 without the tint, which made the trim pale and shiny. The separately decoded tint variants are listed below; unruled variants keep the generic reading.
  - `jn_mo_gbl_color_tint_01_random_color`, `jn_color_tint_detail`, and `jn_gbl_color_tint_01_world_detail(_normal)` (2026-10-08; [decode](../Materials/ColorTintVariants/COLOR_TINT_VARIANTS_DECODE.md)): four exact graph rules read grey G/R with no self-lit, add source tint/palette/detail terms, fix instance palette magnitude at 0.5 and world origin at 0, and use sole new texture unit 9; parallax remains unruled. Bounded numerical/corpus controls, viewer tests and the Aridia pipe GPU proof pass.
  - `za_basic_normal_gloss` (2026-10-08, 6 materials on 215 models; `Animation/pipeline/Research/Materials/ZaBasicNormalGloss/ZA_BASIC_NORMAL_GLOSS_DECODE.md`): cached writer `E08357DC` reads grey G/R, suppresses spec/gloss by the geometry-normal view-angle term (exponent `0x4D385B45`), clamps scales before the texture multiply, and has no self-lit.
  - `basic_normal_two_color_gloss_metal` (2026-10-09): exact dotted rule uses source-traced forward diffuse/specular tint, grey G/R and rim gloss with declared viewer-light/SSR/fog/normal/AA assumptions. Both writers pass 2,048 output cases; all8 source materials and a visually inspected hidden battery-cannon proof pass. Runtime draw/CB/descriptor binding remains unproven (`Animation/pipeline/Research/Materials/TwoColorGlossMetal/TWO_COLOR_GLOSS_METAL_DECODE.md`).
  - `mo_gbl_arch_emissive_sequence_01` (2026-10-08, 59 materials on 413 models; `Animation/pipeline/Research/Materials/ArchEmissiveSequence/ARCH_EMISSIVE_SEQUENCE_DECODE.md`): bounded `AAED778C` preview at CB0[99]=0 and zero instance interpolants, grey G F0, sqrt(sat(5R)) gloss, colour/base-mask albedo and sequence/mask glow; stale K and `_c` ignored in Shaded, Q on unit 8. Other native writers and runtime selection remain unvalidated.
  - Six `basic_normal_gloss` variants now have exact rules (2026-10-08, 129 materials; [decode](../Materials/BasicNormalGlossVariants/BASIC_NORMAL_GLOSS_VARIANTS_DECODE.md)): `_emissive`, `_emissive_detail`, `_fresnel_detail`, `_emissive_colored_spec`, `_aniso_incan`, `_specfresnel_emissive` keep grey G/R but decode graph-specific tint, glow, rim and detail terms; detail uses unit 11, pulses show t=0, the remaining name fallback stays in place; two offscreen comparisons passed under viewer lighting.
  - `mo_veh_random_color_01` (2026-10-08): [bounded decode](../Materials/VehicleRandomColor/VEHICLE_RANDOM_COLOR_DECODE.md); EB1AC2CD uses UV0.x for two linear palettes, a paint mask and view-dependent RGB tints, constant packed F0 (0,.0999756,.0999756), Q=1 gloss .948681 and no self-lit/flake samples; second palette uses unit 10. Forward flakes affect normal/gloss in a separate unimplemented slice; runtime pass selection remains unproven.
  - `basic_parallax_occlusion` (2026-10-08, 19 materials on 102 models; [decode](../Materials/ParallaxOcclusion/PARALLAX_OCCLUSION_DECODE.md)): exact bounded rule reads grey G/R without a layout switch or self-lit and binds C/G/N by serialized slots; the source height-alpha march uses 16–64 default steps plus height shadows, so both are documented and omitted from the viewer. No new texture unit; 1,000-case controls, corpus audit, 40 tests and visually inspected corridor GPU proof pass.
  - `jn_ground_puddles` (2026-10-08): [bounded forward decode](../Materials/GroundPuddles/GROUND_PUDDLES_DECODE.md); noise thresholds independently affect grey F0, gloss and diffuse attenuation, while CB0[99] scrolls only the ripple normal; all six exports audited, no nonzero deferred material writer found, so no viewer rule/unit 14 or model improvement proof was added.
  - `jn_basic_normal_gloss_glow_mask` / `_02` (2026-10-08; [decode](../Materials/GlowMask/GLOW_MASK_DECODE.md)): separate cached-forward `2685144F` and native-deferred `CA81B14F` slices read grey G/R with K `0x06F19114`; first uses mask/tint/environment and offset parallax glow, `_02` uses `c.R*mask*K` with a vertex factor. All 19 materials checked; independent 1,200-case controls and toolbox/coral GPU proofs pass. Sole new texture unit 13; viewer ambient/TBN/instance substitutes remain explicit.
  - `mo_blend_01` (2026-10-09; [decode](../Materials/RockBlend/ROCK_BLEND_DECODE.md)): resource-table writer `886AAC94` blends two source-resolved c/g/n layers through H, squared vertex alpha and sharpness; both G images read grey G/R, no self-lit. Sole new unit15; all11 material/control checks and GPU proof pass. The proof model has no source colour stream, so alpha is explicitly frozen to1; runtime input/optional vertex displacement stay unproven.
  - `basic_foliage` ([decode](../Materials/Foliage/FOLIAGE_DECODE.md)): all four deferred writers use grey G/R, zero self-lit and no dedicated subsurface output; all 34 materials resolve by C/G/N slot identity, no layout switch or new texture unit. Viewer normal z uses 1-x²-y²; runtime alpha cutoff/dither and normal fade remain omitted.
  - `mo_neb_amb_screen_01` (2026-10-09; [decode](../Materials/StaleSelfLitKeys/STALE_SELF_LIT_KEYS_DECODE.md)): cached forward `8716DC7A` previews source timeline/grid, mask, color/intensity and scroll noise at CB0[99]=0 with frozen cutoff/alpha/fog inputs, on existing units 0/1/2/4; all 10 materials checked and TV GPU proof reviewed. `C4828176` uses only `0x06F19114` (colored-gloss unchanged); hero D14A48D7 table-0 keys are source-proven, but variable intensity remains unruled pending runtime phase inputs.
  - Other graphs: coloured F0 = spec·`_g.rgb`, gloss = sqrt(sat(gloss·a²)).

  Specular/gloss scales (`0x62835AF9`/`0x24F4CA4C`) come from the material, else the graph default. K uses the self-lit key in the graph's own parameter table, because materials can store keys their graph never reads. Lighting is a fixed viewer rig (key, fill, sky/ground ambient, ACES fit): not game lighting. The skin Fresnel lerp is not applied.
- **Glass / forward path** (2026-10-08). Graphs with a `forward` rule in `GRAPH_RULES` are drawn after every opaque batch, using a second program (`gl_shading.GLASS_FRAGMENT`, `Shading.glass_program`, `ModelViewport._draw_glass`):
  - **Draw state:**
    - Blend and cull come from the material's flags word (`.material` section `0xE1275683`, u32 at byte 16; `texture_preview.material_flags`/`blend_state`, the rule in `Claude_shading_fix/render_state/`). Additive is (`SRC_ALPHA`, `ONE`) and alpha-over is (`SRC_ALPHA`, `ONE_MINUS_SRC_ALPHA`). Bit 2 clear culls back faces; the decoded meshes are counter-clockwise front.
    - Depth write is off, and the glass batches draw farthest first by their rest-pose centroid in view space (`split_forward`, `back_to_front`). Triangles within a batch are not sorted.
  - **`character_glass`** (Blarg visor) follows the native `34D5B21C` decode (`Other models/enm_blarg_space_commando/evidence/glass/CHARACTER_GLASS_DECODE.md`): rim alpha, tinted reflection without Fresnel, and body colour × (1 + env rim).
  - **`blarg_saucer_glass`** (saucer dome) follows the native `FEEC00CE` decode (`Other models/enm_blarg_saucer/evidence/GLASS_DECODE.md`).
    - The writer binds textures by slot hash, not suffix: the palette `_colorpick_c` and `Patterns_cma` in the `_n` slot. The material's `glass_c`/`glass_g` are never read (Textured mode still shows `glass_c`).
    - Alpha = sat(0.9 + rim), so the dome is at least 90% opaque: dark amber 0.4·palette × diffuse, grey F0 0.1 reflections, and a faint pattern normal. Before this, the dome drew opaque with its unbound `_c`.
  - **Approximations:** the engine's light loop, probes, screen-space reflections and fog are replaced by the viewer rig, and colour is tonemapped per fragment before blending (the game blends HDR before its tonemap). Instance fade 0, `attr0.w` 1.
  - **Other forward graphs** (VFX, energy, cornea, other glass families) have no rule yet and still draw opaque.
  - **Proof:** `glass_proof.py` renders both models hidden and measures transmission (`glass_proof.json`). Dome pixels over the background pass 7.1% median (p90 9.8%; the decode allows at most 10%). Visor pixels pass 23.5% median (p90 44%) at the default top-down framing. Images: `Other models/enm_blarg_saucer/proofs/asset_browser_glass_20261008.png` and `Other models/enm_blarg_space_commando/proofs/asset_browser_visor_glass_20261008.png`. These were looked at by Claude only, not by a human.
- **Animation playback** (`animation_preview.py`): animsets come from the actors that reference the model in the output's `asset_references.sqlite`, following their configs (attached actors not followed, like `index.related`). Clips are listed from `ordered_containers`, decoded on selection with `decode_clip`/`sample_transforms`, classified with `classify`; skin = `worlds(rows, frame, rule) @ inverse_bind` with the pipeline's inheritance rule (single candidate, else SSC0), conjugated into the preview basis. Additive-style clips play standalone. GPU skinning (8 influences) reads bones from a 1-row RGBA32F texture: a uniform array failed to link above ~200 joints (NVIDIA C6020 on Clank, 150 joints bucketed to 256). NVIDIA aliases built-in attributes, so skin attributes use slots 1, 6, 7, 9. Checked in hidden windows: Toxic Crab walk and Clank strafe walk deform plausibly; rest skinning is exact.
- Blender proof tab hidden (controls still built). UI scale (75–200%) resizes the named fonts live and is saved in the settings file. The window opens maximised.

## Textures tab

The **Textures** tab sits next to **3D model**. **Show** switches between two listings:
- **This model:** every `.texture` that the selected model's materials reference, directly or as material-graph defaults (marked "graph default"), with the format, full size and material.
- **Whole archive:** all 18,994 textures in the archive, including those no model uses, such as UI art, loading screens, skies, VFX and environment probes. Format and size fill in when a texture is viewed. **Filter** matches every word against the path. At most 3,000 rows are shown, so the filter narrows the rest.

Selecting a texture decodes it in a worker and previews it with a channel selector (RGB, RGBA over a checkerboard, R, G, B or Alpha). RGB is the default because alpha is usually mask data, such as the `_c` self-lit mask. The preview reuses the 3D view's decoded mip (at least 512 px). Array textures (38 in the corpus, mostly VFX `_3d` flipbooks) enable the **Slice** selector.

**Export selected… / Export listed…** write any combination of the following into a chosen folder. Export listed covers every filter match, not only the 3,000 rows shown, and asks for confirmation above 200 textures.
- **Original .texture:** the decompressed built asset, plus `<name>.texture.stream` exactly as archived when the texture has a stream.
- **DDS:** the original block-compressed largest top mip with a DX10 header, the same as the pipeline's `decode_texture`.
- **PNG:** the stored values without a colourspace conversion. R8G8 gets B = 0, and BC6H is clamped to 8-bit by Pillow.

When two exported textures share a basename, the later one gets a `__2` suffix, and so on. A failed texture is logged and does not stop the others.

**Flip vertically** is off by default and changes only the preview and PNG output. Unflipped means as stored in the game, top row first, which matches what the pipeline's Blender builds show. The builds never flip images; the decoder flips UV V instead (`v_blender = 1 − v_game`), so textures and Blender UVs line up without flipping. Original and DDS output is never flipped.

An array texture exports as one DX10 array DDS and one PNG per slice (`<name>_sliceNN.png`).

Pillow decodes BC1–BC7 for the preview and PNG export. It is imported lazily, so the 3D view works without it, and `rc2016_asset_browser.spec` lists it as hidden imports for the exe. Pillow has no BC1–BC3 sRGB decoder, so those blocks decode as their identical UNORM twins. No colourspace conversion is applied either way.

As of 2026-10-08, `rc2016/formats/texture.py` reads every texture in the archive:
- It accepts BC1/2/3 sRGB (DXGI 72/75/78).
- It no longer rejects non-power-of-two sizes. All 369 in the corpus are stored padded to whole 8×8-block tiles, the same layout the un-tiler already assumed, and their built sizes match exactly.
- `ps4_deswizzle_slices` reads array slices stored back to back.
- Un-tiling uses numpy when it is available, with output byte-identical to the pure-Python loop, which is kept as the fallback.

On 2026-10-08, a full decode of all 18,994 archive textures (19,242 images including array slices) through the export path had no failures and took about 18 minutes.

## Flash UI tab (menus and in-game maps)

The game's menus are Scaleform GFX files: SWF with a `GFX` signature, version 12–24. The archive holds 53 of them, as types `gfx` and `swf` under `ui/export/`. They are vector art, so the Textures tab cannot show them.

The **Flash UI** asset type (`flash_tab.py`; a tab until 2026-10-09) works as follows:
- The file list loads on first view (or with **Load list**) and opens `ui/export/pause/map.gfx` automatically.
- The symbol list shows the main timeline, sprites and shapes, filtered to exported or class names by default.
- **Label** and **Frame** choose a timeline frame. **Background** (Checker, Dark or Light) changes the preview only.
- **Export frame…** and **Export all labels…** write transparent PNGs at the chosen size. **+ original .gfx** also copies the file's exact bytes.

**In-game planet maps:** these are in `ui/export/pause/map.gfx`, symbol `Map_fla.map_image_asset_holder_6` (id 1253). It has one labelled frame per map: `kPokitaru`, `kMetropolis`, `kNebula`, `kAridia`, `kBatalia`, `kDeplanetizer`, `kGaspar`, `kKalebo`, `kNovalis`, `kPhoenix`, `kQuartu`, `kRilgar`, `kVeldin`, `kDeplanetizerExterior` and `kDeplanetizerNefarious`. They render in their authored grey. The game recolours and highlights map areas at runtime from ActionScript, which is not run. Each planet's `gp_map.zone` holds the map-section volumes and the map camera.

**Game-colour planet maps:** use **Export planet maps (game colours)…** in the tab, or run:

```powershell
python -m rc2016.flash.planet_maps "<...\CUSA01073>" "<output folder>" [--size 4096]
```

`map_export.py` writes one transparent PNG per map. Every named area of the `land` sprite is filled with the map script's visited colour, `#AFDAFF`. Areas marked `kSecret` in `configs/system/system_<planet>_map.config` are filled with its secret colour, `#AFFFE4`.

How the sources fit together:
- **Colours:** the script declares the fields `_visitedColor`, `_secretColor`, `_inaccessibleColor` and `_unvisitedColor`, and its constants include `0xAFDAFF`, `0xAFFFE4`, `0xA87373` and `0xFFFFFF`. The script was not decompiled. The pairing of fields to values comes from sampling an in-game Veldin screenshot, which matches those colours at partial opacity over the menu background.
- **Secret areas:** config range ends map to instance names. For example, `kVEL_1_16b` becomes `area1_16b`, and `kMET_4_05c_secret` becomes `area4_05c_secret`.
- **Ranges** are expanded by natural name order. The game's own section order was not decoded.
- **Unresolved entry:** Gaspar's `kGAS_3_04c` has no matching shape in the Flash file.

**Section lines:** the lines between sections are on by default; `--no-seams` turns them off. They are not drawn in the Flash file, where only 4 of 593 area shapes have strokes. In-game they are anti-aliasing seams, because each section is a separate shape. `render_with_seams` reproduces them by growing each area mask by half the line width. Pixels covered by two grown masks are darkened ×0.7, at a line width of about 1/800 of the long side, as measured on the screenshot. Outer edges stay clean. A full export with seams takes about 5 minutes.

The 12 unnamed pieces across the maps, plus the template sprite #93, are omitted. They are stray dots under 3 px, plus two hairline slivers (7×0.1 px on Aridia, 0.5×66 px on Kalebo). They are not the section lines. On 2026-10-08, the Veldin export matched the in-game screenshot: green at the top tip and at the small south-west piece. Areas are exported opaque. The game blends them over the background at roughly 80% opacity, and switches visited, unvisited and inaccessible colours from save progress.

**Renderer:** `gfx_render.py` was written for this project, because the online options did not fit. pyswf/py3swf are Python 2-era with extra dependencies, and JPEXS FFDec is a separate Java application. It parses the following:
- DefineShape 1–4: solid, linear and radial fills, and line styles.
- DefineSprite timelines with PlaceObject 1/2/3 and RemoveObject: matrices, colour transforms, clip-depth masks, frame labels.
- SymbolClass/ExportAssets names, and ImportAssets resolved by file name against other archive UI files.

Fills are built from SWF's left/right fill edges, chained into loops and drawn even-odd at 2× supersampling. On 2026-10-08, all 53 files parsed without tag errors and all 737 named symbols rendered.

**Not drawn:**
- Text fields and fonts.
- Filters and blend modes.
- Morph shapes.
- ActionScript and its runtime colours.
- Nested sprites' own animation: children show their first frame.
- Bitmap fills, drawn as grey: 12 `DefineExternalImage2` (tag 1009) references across 8 files point to the original export's `.tga` names (such as `Loading_I1D.tga`), which the game substitutes at runtime.

## Saved results and optional Blender

Saved results are read only from the GUI's output root, reloaded when it changes and after each batch. There is no result dropdown (removed 2026-10-08, along with Refresh results / Browse output… and the always-loaded CorpusRegression folder): selecting a source model adds its build to the Details tab when there is one. To inspect the regression runs, set the output root to e.g. `CorpusRegression/runs/r1`, as `smoke_gui.py` does. Large master and validation-report hashes are checked in a worker for the selected result. Mutated masters/receipts disable scene review; stale proof links fail provenance checks. No receipt is rewritten.

**Open Blender** uses an explicitly configured executable and separate path arguments. **Artifacts / logs** opens the result location. **Proof artifact** explicitly opens a verified existing proof; it never substitutes for the 3D model.

The Optional Blender proofs tab reads actual saved scene controls only on request. It offers persistent source Actions with source FPS/frame bounds and exact recorded variant/LOD selectors. Unsupported selectors remain unavailable. Combined variant and LOD switching has no validated contract and is rejected. Additive clips are standalone source previews.

Proofs use `review_blender.py`, a hash-pinned request, a new isolated `reviews/<UUID>/` directory, and a fresh Blender process with `--disable-autoexec`. The adapter never saves the delivered master or overwrites reports. It uses exact current embedded controls or the hash-verified sealed `legacy_controls.py` API for older retained scenes; unknown control text fails closed. It preserves the saved camera and records evaluated geometry/visibility at the selected frame. Current shared HDRI review lighting, transparent 960x640 RGBA, 32 samples, Cycles RTX 3060 OptiX with CPU disabled follow this workspace's proof requirements. Missing rendering prerequisites fail explicitly. No global preferences are saved.

Master hashes are checked before and after. In-memory camera, visibility, animation, timeline, lighting, image metadata, GPU flags and render state are restored and compared. Proof provenance is separate from sealed delivery evidence. Static and two animated frame proofs passed after AnimationMemory completed. Variant acceptance targets the separately delivered combined `Fusion_Grenade_V1_V5_switchable.blend`, pinned by the sealed assembly receipt and hash-verified through catalog discovery. It passed (automated checks only); see `results.json`. `proof_acceptance.py` prepares the plan without launches by default; run it with `--run` only when the machine is free (check `tasklist | findstr blender.exe` first). The passing run was:

```powershell
& 'C:/Users/Tim/AppData/Local/Programs/Python/Python314/python.exe' -B Animation/pipeline/Research/AssetBrowserReview/proof_acceptance.py --run --resume Animation/pipeline/Research/AssetBrowserReview/reviews/acceptance_d8ed99765e24450f98ac57afb16c47e3/acceptance.json
```

The runner checks process availability before each sequential launch and records PNG alpha, restoration, animated deformation and source variant visibility.

Scan, multiselect, batch, logs, cancel, transform mode and verified resume retain the existing CLI/backend lifecycle. New review work uses bounded background queues, one owned process, stale-result tokens, cancellation/reaping, and double-submit guards. Closing the UI cancels its review process. No unrelated Blender process is stopped.

## Validation

```powershell
$py = 'C:/Users/Tim/AppData/Local/Programs/Python/Python314/python.exe'
& $py -B Animation/pipeline/Research/AssetBrowserReview/validate.py
& $py -B Animation/pipeline/Research/AssetBrowserReview/smoke_source.py
& $py -B Animation/pipeline/Research/AssetBrowserReview/smoke_gui.py
& $py -B Animation/pipeline/Research/AssetBrowserReview/glass_proof.py   # glass path: saucer dome + Blarg visor
```

`validate.py` records `tests.log` and `tests.json`: 46 tests, 45 passes, one historical fixture skip (`_Archive/2026-10-08_legacy_fbx_models/Weapons/COPY/Sheepinator/Source Archives` is absent). This does not erase the unchanged sealed CorpusRegression run's 72-test result. All 17 sealed phase-report hashes and its results hash remain unchanged.

`source_smoke.json` verifies exact source hashes/counts for laser gate, Toxic Crab, bridge LOD subsets, and an expected unsupported sky. `scan_smoke.json` records a fresh canonical CLI scan of 6,949 models with a nonexistent Blender executable. `gui_smoke.json` records actual Tk widget instantiation and WGL framebuffer drawing, orbit/pan/zoom/reset, selection, and clearing unsupported geometry with the Blender field empty and no proof images used.

`viewport_framebuffer.png` is a renderer test artifact, not a model thumbnail and never loaded by the interface. Programmatic widget/handler checks do not establish native mouse clicks, a full-window screenshot, or human visual review. Those remain untested; the desktop tool available here has browser-only access.

User smoke sequence: launch, scan with Blender blank, select Toxic Crab, orbit/pan/zoom, change source LOD, select unsupported sky, and verify the previous mesh disappears. Browse a saved result and check limitations. Configure Blender only if desired, then request scene controls and a proof once the shared machine is free. Check clip/frame bounds, source variant visibility, cancel, and normal batch/resume controls.

## Files and ownership

`browser_ui.py` integrates filters, results and optional review controls. `source_preview.py` adapts the existing native decoder. `viewport.py` implements the Windows OpenGL viewport. `catalog.py` reads result schemas without writes. `workers.py` provides coalesced background work and owned-process cleanup. Its tested ThumbnailCache utility is unused by the browser. `review_blender.py` is the isolated optional scene-review adapter.

The only existing entry point changed is `rc2016/app/main.py`. `gui_before.py.snapshot` and `baseline.json` preserve the starting GUI and sealed phase hashes. No CLI, source-animation internals, shader translators, raw sources, masters, or sibling research outputs were changed by this work. Shared animation integration belongs to AnimationMemory; recheck its final status/contracts before depending on new clip-selection APIs.

Assembly-result discovery is implemented. `catalog.py` lists each passed `runs/<run>/variant_assemblies/<name>/assembly_receipt.json` under its single V1 source identity, labelled "(V1/V5 assembly)". Discovery requires the receipt schema `fusion-source-linked-assembly-cache-v1`, a passed status and exactly one input matching `GloveModelOverride`; the master and validation hashes are verified on selection. Its V1/V5 selectors come only from the optional Blender inspection, never from the catalog. `test_catalog.py` covers listing, hash mutation and fail-closed receipts, and the real sealed Fusion receipt is discovered with both hashes matching.

Variant acceptance passed on the combined master. Blender was free, so the resume command ran and V1 and V5 proofs were both rendered with OPTIX, transparent RGBA, restored scene state and unchanged master hashes. The first attempt stopped at the shared builder HDRI check because the combined master packs its HDRI; the adapter now drops that pack in memory only after verifying the bytes match the shared file, and it never saves the master. The variant proof PNGs are automated-check evidence only (render device, RGBA alpha, restoration, hashes, source variant visibility); they have had no human visual review and do not establish game equivalence.

The 2026-10-07 stop checkpoint in `handoff.md` and `results.json` (`historical_stop_checkpoint`) is case history only; its variant blocker is resolved. Exact reproduction commands are in `results.json` under `reproduce`.

## Limitations

- Neutral source rest geometry only: no browser playback or game material recreation.
- Native mouse clicks, full-window screenshots and human visual review of the GUI remain untested.
- Proof PNGs (static, animated, V1/V5) are automated-check evidence only; no human visual review.
- One historical backend test fixture is absent (`_Archive/2026-10-08_legacy_fbx_models/Weapons/COPY/Sheepinator/Source Archives`).
- Combined variant plus LOD switching has no validated contract and is rejected.
- The exe was rebuilt 2026-10-08; its 3D view and tree were checked on screen only from the source run, not inside the exe window.

## 2026-10-09 Level tab

The **Level** asset type (`level_panel.py`; a tab until 2026-10-09) lists the archive's 17 levels on first visit, loads the chosen `.level` (or one `.zone`) with per-zone toggles, and draws it with the instanced `LevelViewport`. It opens its own archive instance and worker, reading the source root on the Tk thread. The same panel runs standalone from `R&C 2016 Level Viewer.lnk` / `level_viewer.py`. Details, results and gaps: `../Levels/README.md`, "Level viewer". Acceptance: `smoke_level.py` → `level_smoke.json`.
