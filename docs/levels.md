# Level / zone placement spike (2026-10-08)

Goal: test whether levels can be rebuilt in Blender from the archive's own placement data,
reusing the pipeline's validated model, material and texture output.

## Archive facts (EUR, `Image0` asset archive)

- `levels/` holds 18 levels and ~28k assets: 814 `.zone`, 232 `.zone_physics`, 68 `.zone_static`,
  17 `.level`, 21,158 `.lightgrid`, 5,750 `.texture`.
- A level is split into named zones per region (Novalis `default_region/`: `art_buildings`,
  `art_terrain`, `art_sewers`, `gp_base`, `gp_crates`, `gp_ledges`, `vfx`, `lighting`, `nav`,
  `sound`, …), each with an `_imposter` twin (distant stand-in, not yet examined).
- `.zone_static` is a **"Zone Decal Built File"**: pre-clipped projected decal meshes (dirt,
  scorch marks, screws), not terrain. Decoded (see Decals below).
- Terrain is ordinary model placement: Novalis `art_terrain.zone` places `rp_novalis_level1_tfrag`
  (terrain), `rp_nov_uniq_Distant_View_Landscape_01`, cave interiors and `rb_bat_mountains_*`.
- `.level` ("Level Built File") lists the level's zones; decoded (see below).

## Decoded: `.level` ([`level_zones.py`](level_zones.py))

| Section | Content |
| --- | --- |
| `0x2BA33702` | `u32[n]` file offsets of zone paths (Novalis: 18; `_imposter` twins other than atmosphere not listed) |
| `0x4130D903` | `u32[n]` region paths (Global, OpenWorld, default_region, quest overlays `NOV_mis_1..3`) |
| `0x2236C47A` | `u32[n]` checkpoint / spawn names |
| `0x3395AEC1` | spawn records with world positions (not decoded) |

Zones carry no transform of their own; instance matrices are world space. Novalis: 8 of 18 zones
have a model-instance table (10,158 instances, 286 unique models); the rest are atmosphere,
music, sound, nav, vfx, ambient and gameplay zones (`gp_crates`, `gp_ledges`, `gp_map` place
actors, not models).

## Decoded: zone model-instance table ([`zone_instances.py`](zone_instances.py))

DAT1 container; strings are referenced by **absolute file offset**.

| Section | Content |
| --- | --- |
| `0xC6A5905E` | `u32[n_models]` file offsets of model path strings (backslash-separated) |
| `0x6987F172` | `u32[n_inst]` offsets of instance records inside `0x06ABCAB2` |
| `0x06ABCAB2` | data heap holding the instance records (and other, undecoded objects) |
| `0x032D450A` | `(u32 string offset, 0xFFFFFFFF)` pairs → material paths (not needed for placement) |

Instance record: `+0x00` float32 4×4 matrix, row-vector convention (rows 0–2 = local axes with
scale, row 3 = translation, w column `(0,0,0,1)`), native Y-up game space; `+0x44` u32 record
size (`0xB0` + optional tail, records 8-byte aligned, last tail may overrun an unpadded heap by
<8 bytes); `+0x48` u64 per-instance id; `+0x58` u32 model index; `+0x5C` u32 flags (raw; values
`0x440000`, `0x440020`, `0x740000`, … meaning unknown). Tail bytes kept raw.

Evidence:
- Novalis `art_buildings.zone`: 3,167 instances, all 136 listed models used, ids unique.
- Every `.zone` in the archive that has the three sections decodes with all invariants holding
  (size/stride, w column, index range, unique ids): **229 zones, 228,727 instances**; 585 zones
  have no model-instance table (lighting, sound, nav, gameplay-only…). 3,999 matrices are
  sheared (worst axis cosine 0.685) and 17,511 are mirrored, so placement must keep the full matrix.
- `_distant` models (e.g. `tb_nov_bldg2_mid_window_distant`) are separate backdrop buildings
  (median ~90 units from the nearest near-variant), not LODs stacked on near models; all are placed.
- Visual: the proof render shows floor tiles tiling into continuous roofs and vegetation on the
  ground, confirming matrix convention, presentation basis and model index.

## Decoded: decals (`.zone` + `.zone_static`, [`zone_instances.py`](zone_instances.py) `decode_decals`)

| Where | Content |
| --- | --- |
| `.zone` `0xBCF43558` | `u32[n]` heap offsets of 0x28C-byte decal objects: `+0x00` float32 4×4 projector matrix, `+0x48` inline material path, `+0x288` u32 mesh-record index |
| `.zone_static` `0x657512BB` | 0x58-byte mesh records: u16 vertex count, u16 index count, u32 flags (`0xF12`/`0xE12`/`0xD12`, raw), u32 absolute data offset, `+0x40` bounding sphere (centre, R) |
| `.zone_static` `0xCF30405E` | per mesh: n_v × 16-byte vertices (u16[3] position, u16 `1`, u32 normal, u32 tangent; 10:10:10:2 UNORM as in models), n_v × half2 UV, n_i × u16 triangle-list indices |

Position = `centre + (u/65535·2 − 1)·R/√3`, a world-axis cube inscribed in the bounding sphere.
Chosen because decoded face normals match the stored vertex normals: median |dot| 1.000 on 76
Novalis building decals and 0.990 on 27 terrain decals, against ≈0.4 for every projector-frame
variant (rows, columns, rotation only, sphere centre). Corpus: all 68 `.zone_static` files decode,
every mesh record is claimed by exactly one decal object, 6,500 decals; median agreement 0.993,
78% above 0.95. **Unresolved:** the other 22% (many are tiny 4–6-vertex quads where quantisation
dominates; some flat screw decals have stored normals perpendicular to their surface). Decal
materials are not part of any model and are not built by the pipeline; the assembler imports decal
geometry with UVs, normals and the source material path, hidden (eye + render), with no material.
Degenerate and zero-area triangles are dropped: Blender 5.0's
`normals_split_custom_set_from_vertices` crashes (access violation in
`mesh_normals_corner_custom_set`) on them.

## Assembly ([`assemble_zone.py`](assemble_zone.py))

Models are built with the ordinary pipeline (`run.py --model <path> … --skip-previews`); the
assembler loads each model's mesh objects into an unlinked fake-user prototype collection
(native Y-up, identity transform) and places one collection-instance empty per zone instance,
parented to a root carrying the presentation basis, with the source matrix in
`matrix_parent_inverse` (keeps shear/mirroring exactly; world matrices are asserted after
evaluation). Materials are merged by `source_material_sha256`, images by DDS-hash name. Models
whose pipeline validation failed are loaded from their unvalidated candidate blend and listed
under `unvalidated` in the report.

Instances of models whose path contains `collision` (e.g. `gbl_gp_hero_collision_01`) or
`environment/template/` (`template_Square`, 152 instances in `lighting.zone`, assumed to be an editor
helper) are kept but hidden (eye + `hide_render`). A `level.json` input assembles all its zones
into one file (one collection per zone, shared prototypes). [`validate_assembly.py`](validate_assembly.py)
reopen-checks any assembled file and can render a zone close-up without saving.
[`build_models_parallel.py`](build_models_parallel.py) builds a model list in N parallel shards,
skipping models that already have a final in the given roots.

### Build speed (2026-10-09)

Profile of one prop build (136 s): 86 s in the pure-Python `archive.lz4_block`, of which ~59 s
came from the once-per-run Novalis light-grid inventory; Blender build + validation ~5 s. Changes:
`lz4_block` rewritten with inlined reads and slice copies (byte-identical on 1,396 random assets
and on 300 corrupted inputs, including identical error messages; ~3.5–4× faster), and the light-grid
inventory now runs only for `--lighting-source novalis`. The runner is single-threaded, so
zone models are built in parallel shards, each with its own `--output-root`
(`models_s0` … `models_s4`); pass all roots to the assembler separated by `;`.

## Result: Novalis `art_buildings.zone` (2026-10-09)

`RC_Output/LevelSpike_Novalis/novalis_art_buildings.blend` (154 MB, images packed, shared HDRI
linked) with `novalis_art_buildings_proof.png` and `.report.json`. Reopened in a separate process:
3,167 instances with unique ids, matrix error 0, 136 fake-user prototypes, 57 collision instances
hidden and no other instance hidden, no unpacked images, no shared-lighting or `audit_scene()`
mismatches. 133 models are validated pipeline finals; 3 are unvalidated candidates:
`rp_nov_plant_bush_01`, `rp_nov_plant_flora_patch_01` (leaf textures missing after reopen) and
`tb_nov_gate_bldg02` (one subset, `pasted__tb_nov_dome_metal_blue`, has a different normals hash
after save/reopen). Materials in use: 59 translated deferred, 35 colour-only previews
(10 with no base colour), 4 thin-glass approximations, 3 partial skin, 1 failed. 84 models built
in 253 s with 5 parallel shards after the speed changes (≈1 min/model single-threaded before).

## Result: whole Novalis level (2026-10-09)

`RC_Output/LevelSpike_Novalis/novalis_level.blend` (346 MB) with `novalis_level_proof.png`,
`novalis_level_city_closeup.png` and `.report.json`; inputs in `level_data/`. 153 extra models
built in 325 s (6 shards). Reopened: 10,151 of 10,158 instances placed (7 belong to the one missing
model) with unique ids and matrix error 0, 285 prototypes, 110 decals, only collision/template
instances and decals hidden, images packed, no lighting or audit mismatches. The close-up shows the
city standing on the `tfrag` terrain with rocks, grass and blue mushroom trees, inside the
distant-mountain ring.

Model status: 276 validated finals, 9 unvalidated candidates, 1 missing.
- Missing: `tb_sewer_pipe_large_end` — Blender 5.0 crashed while the pipeline set its custom normals
  (same `mesh_normals_corner_custom_set` access violation as above), so no candidate exists.
- Texture transport failures ("expected one reopened source image, found 0"): the terrain
  `rp_novalis_level1_tfrag` (4 layer textures: `rp_pok_grass_tile_c`, `rp_nov_rock_single_c/_n/_g`),
  `rp_nov_uniq_Distant_View_Landscape_01`, foliage `rp_nov_plant_Bush_01/02`,
  `rp_nov_plant_Flora_Patch_01`, `rp_nov_plant_Tree_Leaves_Canopy_Small_01`,
  `rp_nov_shrub_tree02_trunk_01`. Likely layered/foliage graphs whose extra textures the translator
  leaves unconnected, so they are dropped on save.
- `mo_gbl_amb_Volumetric_Sphere_01`: "No source materials survived reopen" (volumetric/VFX material).
- `tb_nov_gate_bldg02`: normals hash changes on save/reopen.
Materials in use: 125 translated deferred, 88 colour-only previews (24 without base colour),
5 thin-glass approximations, 3 partial skin, 1 failed.

## Skybox: cloud dome (2026-10-09)

[`sky_dome.py`](sky_dome.py) adds the level's cloud dome to an assembled level blend:
`RC_Output/LevelSpike_Novalis/novalis_level_sky.blend` (362 MB; `novalis_level.blend` is left
unchanged), proofs `novalis_sky_proof_toward_sun.png` / `_away.png`. The dome material is a port
of fragment shader `0x8d3c81d0` (equations in the script docstring) with all 23 CB dwords from the
material report and the decoded Novalis sun ray; the pipeline's preview material is kept as a fake
user. The dome follows the scene camera (Copy Location) at radius 5,000 and is visible to camera
rays only, so level lighting is unchanged. Reopened: material output = Mix(Transparent, Emission),
UV layer `UV0` (the pipeline's name; an earlier build that assumed `UVMap` rendered no clouds),
packed Non-Color images, 23 dwords, constraint and visibility intact; `validate_assembly.py` still
passes. Visual: clouds take the material's orange tint `(1.0, 0.342, 0.089)` toward the sun and are
white/grey away from it.

Presentation values (not decoded): time 0, alpha multiplier 1, alpha-test threshold 0, fog factor 1
(no fog), radiance scale 1. Not ported: the stars term (zero for Novalis, P22 = 0) and therefore the
two textures it alone reads. The clear sky between clouds is still transparent: it comes from the
atmosphere's undecoded tables. With mesh flag `0x0020` accepted, the other 14 atmosphere models
(other levels' domes, clouds, stars, planets) should now decode too; only Novalis was built. The
asset-browser smoke scripts in `Research/AssetBrowserReview/` that expect `rk_sky_nebula_stars` to be
rejected (`smoke_source.py`, `smoke_gui.py`) are now stale evidence.

### Findings behind it

- `atm_novalis.atmosphere` names its sky model first: `atmosphere/atm_Novalis/rk_sky_novalis_dome.model`.
  The rest of the 2.3 MB asset is mostly packed half-float tables (probably precomputed scattering;
  undecoded).
- Mesh flag `0x0020` is set only on the 15 atmosphere sky/cloud/star/planet meshes and does not
  change the vertex layout (see the comment at `SUPPORTED_MESH_FLAG_MASK` in `native_model.py`);
  the decoder now accepts it and the dome builds and validates. Dome: a hemisphere of radius 0.09
  (1,486 vertices, 2,756 triangles, elevation 0.6–90°), V tracks elevation exactly, U wraps azimuth;
  no vertex colours. The game draws it around the camera.
- Material `atm_rk_sky_novalis_dome` uses `materialgraph/global/Rk_Sky_Dome`; its cached fragment
  shader `0x8d3c81d0` (shadPS4 cache, verified SPIR-V) is a **cloud-layer** shader: it samples the
  dome texture twice (`.xy` and `.zw` = two cloud layers: coverage + shading each, see channel sheet),
  a scrolling distortion noise and two further masks, lights the clouds with three RGB colours and a
  sun-direction term, alpha-tests coverage against an interpolated threshold and fogs the result. So
  the dome draws clouds only; the clear sky behind it comes from the atmosphere system.
- All 23 material constants the shader reads (CB dwords 0–22) map one-to-one onto the graph's
  parameter table (keys and values from the material instance or graph defaults), e.g. dword 4–6
  `0x4DBE5B8B` = (1.0, 0.342, 0.089).
- Unknown: the five texture-slot bindings (inferred from sampling patterns only), the per-frame
  engine constants (time `ssbo_1[99]`, a horizon/fog colour `[112..114]`, sun direction
  `[128..130]`) and three vertex-shader outputs (alpha-test threshold, alpha multiplier, fog factor);
  the dome's vertex shader is not in the shadPS4 cache.

## Level viewer (2026-10-09)

An interactive viewer outside Blender, built on the asset browser's WGL viewport and shading. It reads levels,
zones, models, materials and textures straight from the archive: no pipeline builds or `.blend` files.

- Launch: `R&C 2016 Level Viewer.lnk` in the project root (pythonw, from source), the asset browser's **Level**
  tab, or [`level_viewer.py`](../AssetBrowserReview/level_viewer.py):

  ```powershell
  & 'C:/Users/Tim/AppData/Local/Programs/Python/Python314/python.exe' -B rc2016/levels/viewer.py [--level levels/veldin/veldin.level] [--zones art_buildings,art_terrain] [--source-root <root>] [--capture out.png]
  ```

  The standalone window defaults to the asset browser's saved source root and remembers its own root and
  last level in `%APPDATA%/PS4 Animation Extractor/level_viewer.json`. `CUSA01073` and `Image0` both list
  the same 17 levels. `--level` also takes a single `.zone`.
- Files: [`level_panel.py`](../AssetBrowserReview/level_panel.py) (level picker, zone toggles, background loader;
  shared by the window and the browser tab), [`level_viewport.py`](../AssetBrowserReview/level_viewport.py)
  (renderer, fly camera), [`level_scene.py`](../AssetBrowserReview/level_scene.py) (decode),
  [`smoke_level.py`](../AssetBrowserReview/smoke_level.py) (browser-tab acceptance, writes `level_smoke.json`),
  `test_level_view.py`.
- Loading: zone tables, then every unique model's geometry (most-placed first), then materials and textures by
  material path; the view fills in as they arrive. Zones without a model table are listed but disabled.
- Rendering: geometry (Highest LOD, look 0) and the zones' row-vector matrices stay native Y-up; the camera
  matrix carries the presentation basis. Each model has static VBOs; each (zone, handedness) gets one instance
  buffer, drawn with `glDrawElementsInstanced` (matrix rows as attributes 10–13, `gl_shading.INSTANCED_VERTEX`).
  Normals use the cofactor matrix × sign(det), so sheared and mirrored instances shade correctly; mirrored
  groups draw with `glFrontFace(GL_CW)` so `gl_FrontFacing` keeps its meaning. Draws are sorted by material and
  each material's uniforms go through the model viewer's own `_apply_material`. Zone toggles only filter
  instance groups.
- Materials are the model viewer's Shaded rules (`texture_preview.material_textures`). Forward (glass)
  materials draw after opaque ones without per-instance sorting. Lighting is the viewer rig in view space
  (it turns with the camera): not game lighting.
- Collision and `environment/template/` models start hidden, as in the assembler.
- Overview framing uses the 2–98% quantiles of placements (`core_bounds`); full bounds framed mostly the
  distant mountain ring. Far plane ≥ 30,000; near plane follows fly speed.
- Controls: left drag look, right drag pan, WASD, Q/E down/up, Shift ×4, wheel dolly, Ctrl+wheel or `[` `]`
  speed, F/Home/double click overview.

Results (RTX 3060, CPU frame time):

| Level | Models | Instances shown | Triangles | Draws | Load | Frame |
| --- | --- | --- | --- | --- | --- | --- |
| Novalis `art_buildings.zone` | 136 | 3,110 | 1.40 M | 428 | 11–12 s | ~10 ms |
| Novalis (18 zones, 8 with models) | 286 | 9,891 | 13.4 M | 844 | 12–23 s → **2.5 s** | ~19–21 ms |
| Veldin | 166 | 11,591 | 13.3 M | 430 | 6.5 s | ~11 ms |

Load speed-up (2026-10-09): Novalis 10.0 s → 2.5 s in `--capture` (the 12–23 s above included Tk/GL
contention). Before: ~70% of decoding was `native_model.decode_model`'s per-vertex Python loop (plus a
temp-file write and a second SHA-256 per model), ~30% material lookup with ~9,800 archive opens. Changes:
`decode_model` unpacks vertices with numpy (bit-identical: all 286 Novalis models × All/Highest/source-position
modes match the old output in Python 3.14 and Blender 5.0's Python) and takes `data=` and `as_arrays=`;
`model_geometry` uses both and selects the LOD in the decoder (byte-identical buffers); the viewer's archive
sets `verify_repeats = False` (reads only the first copy of repeated assets; the pipeline keeps the check);
`texture_preview` caches each parsed material graph on the archive (235 materials share 55 graphs; results
identical). Capture unchanged pixel for pixel. Rendering cost is unchanged: ~21 ms CPU per frame, mostly
per-call ctypes overhead over 844 draws; that, not loading, is the case for a native renderer later.

All 286 Novalis models decode here, including `tb_sewer_pipe_large_end`, which crashed Blender in the
assembler. Proofs checked by eye: `level_viewer_art_buildings_*.png`, `level_viewer_novalis_level.png`,
`level_viewer_veldin_level.png`. `smoke_level.py` passed in the browser tab: 17 levels listed, Novalis loaded with
no errors, hiding `art_terrain` drops 9,891 → 5,510 instances and showing it restores them.

Visible gaps: foliage shows as solid blobs (leaf cut-out not decoded), and large planes whose materials have no
`_c` show flat white (Novalis, and Veldin's big ground/backdrop plane). Both are environment-shader work.
Next: click-to-identify (step 3); later environment shaders, sky, decals.

## Known gaps

- Environment materials: 88 of 222 fall back to colour-only previews (unsupported graphs); the
  terrain's layer textures are missing (see above).
- Decal materials are not built; decals are geometry only and hidden.
- Not handled: gameplay actors (`gp_*` zones), `_imposter` zones, quest-overlay regions, VFX,
  lights, collision volumes, `.level` spawn records.
- Archive root: `Ratchet & Clank (EUR)` now contains both `CUSA01073/asset_archive` and
  `Image0/asset_archive`, so the runner needs `--extracted-root ".../Ratchet & Clank (EUR)/Image0"`.
