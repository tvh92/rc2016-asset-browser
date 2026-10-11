# Levels, zones and the level viewer

Reference for the `.level` and `.zone` formats as decoded in `rc2016/levels/`, and for the level viewer built on them. The viewer reads levels, zones, models, materials and textures straight from the game archive; it does not use the export pipeline or Blender. See also [viewer](viewer.md) (model viewer, whose viewport, shading and material rules the level viewer reuses) and [pipeline](pipeline.md).

Blender level assembly (placing pipeline-built models as collection instances, a Blender sky dome port) was done in the research notes and is not included in this repository. Everything below was validated against the EUR `Image0` archive; corpus counts come from those research notes (not included).

## Archive layout

- `levels/` holds 18 level folders and about 28,000 assets: 814 `.zone`, 232 `.zone_physics`, 68 `.zone_static`, 17 `.level`, 21,158 `.lightgrid` and 5,750 `.texture`.
- A level is split into named zones per region (Novalis `default_region/`: `art_buildings`, `art_terrain`, `art_sewers`, `gp_base`, `gp_crates`, `gp_ledges`, `vfx`, `lighting`, `nav`, `sound`, ...). Each has an `_imposter` twin (distant stand-in, not examined).
- Terrain is ordinary model placement: Novalis `art_terrain.zone` places `rp_novalis_level1_tfrag` (terrain), `rp_nov_uniq_Distant_View_Landscape_01`, cave interiors and `rb_bat_mountains_*`.
- `.zone_static` is a "Zone Decal Built File": pre-clipped projected decal meshes (dirt, scorch marks, screws), not terrain.
- Zones carry no transform of their own; instance matrices are in world space.

All of these are DAT1 containers (`1TAD` magic, section count at `+0x0C`, section table of `(tag, offset, size)` u32 triples from `+0x10`). Strings are referenced by absolute file offset.

## `.level` format (`level_format.py`)

`decode_level(data)` returns `zones`, `regions` and `spawn_names`. It raises `ValueError` if the zone list is absent.

| Section | Content |
| --- | --- |
| `0x2BA33702` | `u32[n]` file offsets of zone asset paths (Novalis: 18; `_imposter` twins are not listed, except atmosphere) |
| `0x4130D903` | `u32[n]` region paths (Global, OpenWorld, default_region, quest overlays `NOV_mis_1..3`) |
| `0x2236C47A` | `u32[n]` checkpoint / spawn names |
| `0x3395AEC1` | spawn records with world positions (not decoded) |

In Novalis, 8 of the 18 zones have a model-instance table (10,158 instances, 286 unique models). The rest are atmosphere, music, sound, nav, vfx, ambient and gameplay zones; `gp_crates`, `gp_ledges` and `gp_map` place actors, not models.

Command line (also writes JSON per zone):

```
python -m rc2016.levels.level_format <level asset path> <out dir> [--archive-root <asset_archive dir>]
```

It writes `<out>/<zone stem>_instances.json` for each zone with a model-instance table (including decals when a sibling `.zone_static` exists), `<out>/models.json` (union of model paths) and `<out>/level.json`. The built-in default archive root is a developer-specific path, so always pass `--archive-root`.

## `.zone` model-instance table (`zone_format.py`)

`decode_zone(data)` returns `models` (paths with `/` separators), `instances`, `override_materials` and `models_used`. Zones without the first three sections below raise `ValueError('Zone lacks instance sections ...')`.

| Section | Content |
| --- | --- |
| `0xC6A5905E` | `u32[n_models]` file offsets of NUL-terminated model paths (backslash-separated in the file) |
| `0x6987F172` | `u32[n_inst]` offsets of instance records, relative to the start of `0x06ABCAB2` |
| `0x06ABCAB2` | data heap holding the instance records (and other, undecoded objects) |
| `0x032D450A` | `(u32 string offset, 0xFFFFFFFF)` pairs: the zone's replacement material paths for [material overrides](#per-instance-material-overrides) |

Instance record (offsets relative to the record):

| Offset | Type | Meaning |
| --- | --- | --- |
| `+0x00` | float32[16] | 4x4 matrix, row-vector convention: rows 0-2 are the local axes including scale, row 3 is the translation, w column `(0,0,0,1)`. Native Y-up game space |
| `+0x44` | u32 | record size: `0xB0` plus an optional tail; records are 8-byte aligned |
| `+0x48` | u64 | per-instance id, unique within the zone |
| `+0x58` | u32 | index into the `0xC6A5905E` model list |
| `+0x5C` | u32 | flags, kept raw (values seen: `0x440000`, `0x440020`, `0x740000`, ...; meaning unknown) |
| `+0x99` | u8 | number of material overrides |
| `+0xA4` | u32 | record-relative offset of the material-override block, `0xFFFFFFFF` when there is none |
| `+0xB0` | bytes | optional tail, kept raw as hex. Holds the material-override block and other blocks: `+0xA0` and `+0xA8` (with `+0x9A`, `+0x9C`, `+0xAC`) locate blocks that are not decoded |

The decoder enforces these invariants and raises if any fails: size field is at least `0xB0` and inside the heap; size field matches the stride to the next record (rounded up to 8); the last record's tail may overrun an unpadded heap by fewer than 8 bytes (13 of the 229 zones); w column is `(0,0,0,1)`; model index is in range; ids are unique; a nonzero override count has a block, and the block lies inside the record.

Validation: every `.zone` in the archive that has the three sections decodes with all invariants holding, 229 zones and 228,727 instances. 585 zones have no model-instance table (lighting, sound, nav, gameplay-only). In Novalis `art_buildings.zone`, 3,167 instances use all 136 listed models. 3,999 matrices in the corpus are sheared (worst axis cosine 0.685) and 17,511 are mirrored, so placement must keep the full matrix (no TRS decomposition).

### Per-instance material overrides

An instance can replace materials of its model. The override block holds `n` 12-byte entries: u32 CRC32 of the material slot name, then u64 CRC64 of the replacement material path. Both hashes normalise the string first (lower case, `/` separators, repeated separators collapsed): `name_crc32` is the game's CRC32 (`game_crc32`), `path_crc64` is the reflected CRC-64 with polynomial and seed `0xC96C5795D7870F42`, shifted right by 2 with the top bit set (both match ALERT's `dat1lib`). `decode_zone` gives each instance `material_overrides`: `[{slot_hash, material_hash, material}]`, where `material` is the path from the zone's `0x032D450A` list with that hash, or None.

`scene.resolve_overrides(model materials, entries)` decides what an entry does. It applies only when the slot hash matches exactly one slot name of the placed model's own material table and the replacement is a listed path. Every other entry is reported, never guessed:

| Outcome | Meaning |
| --- | --- |
| `applied` | the slot draws the replacement material |
| `same as default` | the replacement is the slot's own material (no change) |
| `slot not in model` | no slot of this model has that name |
| `slot name ambiguous` | two slots share the name |
| `slot overridden twice` | two entries set one slot to different materials |
| `null replacement` | the material hash is 0 |
| `replacement not in zone list` | a nonzero hash that is not in `0x032D450A` |

Corpus (main `levels/` zones, checked with the repository decoder): 71,145 of 228,727 instances carry a block, 78,097 entries; every block lies inside its record and a zero count always comes with `0xFFFFFFFF`. All 77,371 nonzero replacement hashes resolve to a path in the same zone's list; the other 726 entries are null. 1,105 of the 1,194 listed paths are used by an override in their zone. Against the placed models: 58,820 entries apply (55,378 instances change), 1,259 are the default material, 17,273 name a slot the model does not have, 19 sit on a model whose material table cannot be read; none is ambiguous or conflicting. Of the missed slot names, about 70% are slot names of other models (renamed or replaced slots), so they are treated as stale. 454 models are placed with more than one effective material set. The 22 `rc1/levels/` zones (51,919 instances) have no overrides.

`_distant` models (for example `tb_nov_bldg2_mid_window_distant`) are separate backdrop buildings placed by the zone (median about 90 units from the nearest near-variant), not LODs stacked on near models. All are placed.

### Conventions

- Geometry and instance matrices are native Y-up. Matrices are row-vector: `p' = p * M`, so the translation is `rows[3][:3]`. A column-vector consumer must transpose.
- The presentation basis (Z-up, as in Blender) is `(x, y, z) -> (x, -z, y)`, applied once outside the matrices (the viewer puts it in its camera matrix).
- A matrix with negative determinant of its upper 3x3 is mirrored; it reverses triangle winding and needs the normal matrix handled accordingly.

## Decals (`.zone` + `.zone_static`, `decode_decals`)

`decode_decals(zone_data, static_data)` returns decal meshes in native world space joined with their material paths. It returns `[]` when the zone has no decal section, and raises if a mesh index is out of range or reused, an index buffer is malformed, or any `.zone_static` mesh has no decal object.

| Where | Content |
| --- | --- |
| `.zone` `0xBCF43558` | `u32[n]` heap offsets of 0x28C-byte decal objects: `+0x00` float32 4x4 projector matrix (not used for geometry), `+0x48` inline NUL-terminated material path, `+0x288` u32 mesh-record index |
| `.zone_static` `0x657512BB` | 0x58-byte mesh records: u16 vertex count, u16 index count, u32 flags (`0xF12` / `0xE12` / `0xD12`, raw), u32 absolute data offset, `+0x40` float32[4] bounding sphere (centre xyz, radius R) |
| `.zone_static` `0xCF30405E` | per mesh: n_v x 16-byte vertices (u16[3] position, u16 `1`, u32 normal, u32 tangent; 10:10:10:2 UNORM as in models), then n_v half2 UVs, then n_i u16 triangle-list indices |

Position = `centre + (u / 65535 * 2 - 1) * R / sqrt(3)`: a world-axis cube inscribed in the bounding sphere. This frame was chosen because decoded face normals match the stored vertex normals (median |dot| 1.000 on 76 Novalis building decals, 0.990 on 27 terrain decals) against about 0.4 for every projector-frame variant tried (rows, columns, rotation only, sphere centre).

Corpus: all 68 `.zone_static` files decode, every mesh record is claimed by exactly one decal object, 6,500 decals; median normal agreement 0.993, 78% above 0.95. Unresolved: the other 22%. Many are tiny 4-6 vertex quads where quantisation dominates; some flat screw decals have stored normals perpendicular to their surface.

Decal materials are not part of any model and are not built; the level viewer does not draw decals.

## Skybox and mesh flag `0x0020`

Atmosphere assets name their sky model first (`atm_novalis.atmosphere` -> `atmosphere/atm_Novalis/rk_sky_novalis_dome.model`). The rest of that 2.3 MB asset is mostly packed half-float tables (probably precomputed scattering; undecoded).

- Mesh flag `0x0020` is set only on the 15 atmosphere sky / cloud / star / planet meshes. It does not change the vertex or index layout (the flagged Novalis dome has the same geometry as the unflagged Metropolis dome; stored normals match face normals, median |dot| 1.000). `SUPPORTED_MESH_FLAG_MASK` in `rc2016/formats/native_model.py` is `0x00F1`, so these models decode; the raw flags are kept and the render meaning (likely draw-at-infinity) is undecoded.
- The Novalis dome is a hemisphere of radius 0.09: 1,486 vertices, 2,756 triangles, elevation 0.6-90 degrees. V tracks elevation, U wraps azimuth, no vertex colours. The game draws it around the camera.
- Its material `atm_rk_sky_novalis_dome` uses `materialgraph/global/Rk_Sky_Dome`, whose fragment shader (`0x8d3c81d0`, from a shadPS4 cache) is a cloud-layer shader: two cloud layers (coverage and shading each) sampled from the dome texture, a scrolling distortion noise and two further masks, three RGB light colours plus a sun-direction term, alpha test of coverage against an interpolated threshold, then fog. The dome draws clouds only; the clear sky behind it comes from the atmosphere system (undecoded).
- All 23 material constants the shader reads (constant-buffer dwords 0-22) map one-to-one onto the graph's parameter table. Not known: the five texture-slot bindings (inferred from sampling patterns), the per-frame engine constants (time, horizon / fog colour, sun direction) and the three vertex-shader outputs (alpha-test threshold, alpha multiplier, fog factor); the dome's vertex shader is not in the cache.

### Sky in the level viewer (`sky.py`)

`level_sky(archive, zone paths)` finds the level's sky. It returns None when no atmosphere zone names an atmosphere (Veldin), and raises if the zones name more than one.

- **Atmosphere.** The atmosphere zone (`atmosphere.zone`; `_imposter` twins are skipped) holds the `.atmosphere` path as a string. Its sky models are the asset's `.model` references. The sun ray is the unit vector at header offset 72 (`rc2016/pipeline/lighting.py`, `_decode_ps4_atmosphere_header`), in native space.
- **Cloud domes.** A sky mesh is drawn only when its material's graph is `Rk_Sky_Dome` and the graph bytes match the decoded one (SHA-256 `DOME_GRAPH_SHA256`).
  - Constants: dwords 0-22 come from table 0, instance values over graph defaults (`dome_constants`).
  - Textures come from the graph's slots, overridden by the material's: the distortion noise (`NOISE_SLOT`) and the two cloud-layer slots (`DOME_SLOTS`). Which cloud slot feeds which layer is not decoded. Every dome material binds both to one texture; a material that does not is reported, not drawn. Material slots the graph does not have (Batalia and Gaspar carry two) are ignored.
- **Unsupported sky graphs.** These are reported on stderr and counted on the status line: `rk_sky_dome_flat` (nebula and space domes), `rk_sky_matte` (planets), `rk_sky_cloudlayer`, and the Veldin planet graph.
- **Coverage.** Domes are drawn for Batalia, Gaspar, Kalebo, Metropolis, Museum, Novalis, Pokitaru, Quartu, Prequartu and Rilgar. The Aridia, Deplanetizer (three levels), Nebula and Phoenix skies use only unported graphs.
- **Shader port.** `SKY_FRAGMENT` in `rc2016/viewer/shading.py` follows the shader formula, with UV0 in DirectX convention. Engine inputs take presentation values: time 0 (no scrolling), alpha multiplier 1, alpha-test threshold 0, no fog. The stars term (dword 22; non-zero on Gaspar, Metropolis and Rilgar) is not ported. The output goes through the level's tonemap and blends over the clear sky with the material's alpha-over state.
- **Clear sky.** The clear sky behind the clouds comes from the atmosphere's undecoded scattering tables. When the atmosphere zone references exactly one `volumereflections` capture (Batalia, Kalebo, Novalis), `clear_sky_profile` decodes its six 512x512 BC6H faces (in-game reflection captures, with level geometry and clouds baked in) and keeps the clear-sky pixels. A clear-sky pixel has blue above 1.25x red and above green, and is not clipped. The result is the per-channel median in 6-degree elevation bins. A bin needs 10% clear-sky pixels; bins below that (terrain near the horizon, cloud banks) take the nearest valid bin. This is sampled game data, not the scattering model; it needs Pillow and NumPy. Levels without a capture keep the flat background behind the clouds.
- **Drawing.** The sky is drawn first, around the camera (camera rotation only, at a radius between the near and far planes), with no depth test or depth write. It shows in Shaded and Textured modes.

For Novalis, the profile's zenith is (0.145, 0.255, 0.404), and it brightens towards the horizon. The capture's upward face shows the same sun-side warm tint on the clouds that the port produces.

## Level viewer

Two front ends share one implementation:

- Standalone window: `python -m rc2016.levels.viewer` (`rc2016/levels/viewer.py`).
- The **Level** asset type in the asset browser (`rc2016/app/browser.py`), where the level list sits in the browser's left pane and the source root is the browser's.

### Modules

| Module | Role |
| --- | --- |
| `panel.py` | `LevelPanel` (Tk): level list, typed path entry, view mode, zone toggles, status line; `load_level` background loader |
| `viewport.py` | `LevelViewport` (a `ModelViewport` subclass): instanced renderer, fly camera, PNG capture |
| `scene.py` | Data only, no GL: archive access, `zone_scene`, `model_geometry`, `core_bounds`, material lookup |
| `actor_format.py`, `actors.py` | Actor/component decoding, structural primary binding, source-placement policy and exclusions |
| `sky.py` | Data only, no GL: the level's atmosphere, cloud domes and clear-sky profile ([sky](#sky-in-the-level-viewer-skypy)) |
| `level_format.py`, `zone_format.py` | The decoders described above |

### Loading

`load_level` runs on a worker thread and posts events that the Tk thread applies; Tk and GL are touched only from the Tk thread. Order:

1. The zone list (`.level` -> zone paths; a single `.zone` path is treated as a one-zone level). `--zones` filters by short name or path.
2. Each zone's instance table (`zone_scene`). Instances are grouped per model, per handedness (mirrored or not) and per raw material-override entries, as flat arrays of 16 floats per matrix. Zones without a model table are listed but disabled ("no models"); a zone that fails to decode is disabled as "unreadable" and the error is logged to stderr.
3. Scene bounds from the translations of visible instances, then, for a `.level`, its sky (`load_sky`: `level_sky` over the unfiltered zone list, with the dome and noise textures loaded; a failure is logged and the level loads without a sky).
4. Each unique model's geometry, most-placed first (`model_geometry`: Highest LOD, look 0, decoded with numpy; V is flipped back because the decoder flips it for Blender). Its groups are then resolved against the model's material slots (`effective_groups`): instances whose overrides give the same material changes share a group, and instances with no effective change join the plain group. The view fills in as models arrive.
5. Materials and textures by material path, including the replacement materials of applied overrides, using the model viewer's Shaded rules (`rc2016.viewer.textures.material_textures`). Each material graph is parsed once and cached.
6. A `done` event with timings and the override outcome counts (`material_overrides`).

A new load cancels the previous one (generation counter; stale events are dropped). The panel opens its own `Archive` (not shared with other tabs) with `verify_repeats = False`, which reads only the first copy of repeated assets; the pipeline keeps that check on. The source root may be any folder that contains exactly one archive root (`locate`); it needs the level assets (the `Image0` tree). Both `CUSA01073` and `Image0` list the same 17 levels.

### Rendering

- Geometry and instance matrices stay native Y-up. The presentation basis is applied once, in the camera matrix (`BASIS` in `viewport.py`), so the fly camera works in Z-up.
- Each model keeps its instances per zone on the CPU and has one instance buffer per handedness and set of effective material changes, holding the instances of every visible zone. Toggling a zone rebuilds only the affected models' buffers.
- Each (model, instance buffer) has a vertex array object (OpenGL 3.0) holding the position, normal and UV arrays, the index buffer and the four matrix-row attributes (10-13, `INSTANCED_VERTEX` in `rc2016/viewer/shading.py`) with divisor 1, so a draw binds one object. Instancing needs OpenGL 3.3; otherwise the view reports which entry point is missing.
- Material overrides keep the model's one geometry upload. `draw_items` picks each batch's material per instance buffer (override or slot default); buffers that agree on it share a draw, so an override changes only the instances that carry it. Blend, cull and forward (glass) handling follow the effective material. **Instance material overrides** (on by default) switches them off for comparison.
- Batches of one model with the same material and instance buffers that follow each other in the index buffer merge into one draw. Material uniforms and texture bindings are recorded once per material (`_Recorder` around `ModelViewport._apply_material`), and only the ones that differ from the current state are issued. `glFrontFace` is set only when handedness changes. Draws stay sorted by native program then material; recorded uniform locations and values are specific to the program.
- Normals use the cofactor matrix times sign(det), so sheared and mirrored instances shade correctly. Mirrored groups draw with `glFrontFace(GL_CW)` so `gl_FrontFacing` keeps meaning "outside".
- Forward-shaded (glass) materials draw after all opaque ones with material blend and cull state and no depth write. Instances are not depth-sorted.
- Lighting and tonemap use the model viewer's selectors ([Lighting and tonemap](viewer.md#lighting-and-tonemap)). The Viewer rig is fixed to the world (rig right = +X, up = +Z, towards the viewer = -Y in Blender's Z-up world), so lighting does not shift while orbiting or flying. Shared HDRI lights the level from the shared review HDRI, with level directions taken through the presentation basis. This is not game lighting.
- The sky ([sky](#sky-in-the-level-viewer-skypy)) is drawn before the level; without one the background is a flat colour. No decals, lights or fog.

### Modes

`Shaded` (model-viewer rules and eligible native translations), `Textured`, `Solid`, `Wireframe`, `Solid + wireframe`. Batches without UV0 draw as Solid.

### Hidden by default

Models whose path contains `collision` or `environment/template/` (for example `gbl_gp_hero_collision_01`, and `template_Square`, 152 instances in Novalis `lighting.zone`, assumed to be an editor helper) are loaded but not drawn. The **Show collision/template models** checkbox shows them. They are also excluded from the framing bounds.

### Camera and controls

- Overview camera (`overview_camera`, reset with F or Home): frames the 2-98% quantile box of instance translations (`core_bounds`), from the south at pitch -35 degrees, yaw 90. Full bounds are not used because levels ring their play space with distant backdrops (Novalis mountains about 10x farther out than the city), which would frame mostly empty sky.
- Far plane is at least 30,000 (20x the bounds diagonal if larger); the near plane follows fly speed (0.05 to 2.0). Vertical FOV 60 degrees.
- Yaw 0 looks along +X in presentation space; positive pitch looks up; pitch is clamped to +-89 degrees.

| Input | Action |
| --- | --- |
| Left drag | Look |
| Right drag | Pan |
| W A S D | Move |
| Q / E | Down / up |
| Shift | Speed x4 |
| Wheel | Dolly |
| Ctrl+wheel, `[` `]`, `-` `+` | Fly speed |
| F, Home | Reset to overview |
| Left click (no drag) | Identify a model or actor; actors report name, actor path, primary model and ID |
| Double click | Open the identified model in the Model tab (asset browser) and load it; it does not reset the camera |

Zone list: per-zone checkbox with placed-instance count, **All** / **None** buttons. The side column also has the **Lighting** (`Viewer rig` or `Shared HDRI`) and **Tonemap** (`ACES fit` or `Khronos PBR Neutral`) selectors, which work as in the model viewer ([Lighting and tonemap](viewer.md#lighting-and-tonemap)), and the **Show collision/template models**, **Instance material overrides**, **Native translation** and **Sky (atmosphere cloud dome)** checkboxes. Native translation is on by default, affects Shaded mode only and switches surface frontends without moving the camera. The status line shows load state, zone and model counts, failures, material and texture counts, the number of instances drawn with material overrides and of override entries not applied, the sky (cloud domes drawn, sky meshes not ported, captured clear sky or flat background), visible instances and triangles, draw calls, frame time (ms) and speed.

### Click to identify

A left click without dragging identifies the model under the pointer. Each visible model is drawn into the back buffer in a 24-bit id colour and one pixel is read, so the result identifies the model, not the individual instance. The picked model's visible instances are outlined in orange, and the line under the status shows its path and visible instance count. Double click, or **Open in Model tab** (in the asset browser; the standalone viewer has no Model tab), selects the model in the Model tree and loads it.

### Command line

```
python -m rc2016.levels.viewer [--source-root <extracted game folder>]
                               [--level <.level or .zone asset path>]
                               [--zones a,b,...]
                               [--mode Shaded|Textured|Solid|Wireframe|"Solid + wireframe"]
                               [--capture out.png] [--camera x,y,z,yaw,pitch]
                               [--no-material-overrides] [--no-native-translation] [--no-sky]
```

| Option | Meaning |
| --- | --- |
| `--source-root` | Folder holding the extracted archive. Default: the viewer's own saved root, else the asset browser's saved source root |
| `--level` | A `.level` or single `.zone` asset path, for example `levels/novalis/novalis.level`. Default: the last level used, else `levels/novalis/novalis.level` |
| `--zones` | Comma-separated zone short names or paths; only these zones load |
| `--mode` | Initial view mode (default `Shaded`) |
| `--capture` | Load the level, write one frame as an RGB PNG and exit |
| `--camera` | Camera `x,y,z` (presentation space, Z-up), yaw and pitch in degrees, applied after loading; alone it leaves the window open |
| `--no-material-overrides` | Start with **Instance material overrides** off (for before/after captures with the same camera) |
| `--no-native-translation` | Start with **Native translation** off (hand rules and generic/family readings) |
| `--no-sky` | Start with **Sky** off (flat background) |

`--capture` and `--camera` take effect when loading finishes. With `--capture` the settings file is not updated and the process prints the capture path, success flag, load timings (including the override outcome counts) and view stats.

Settings are stored in `%APPDATA%/RC2016 Asset Browser/level_viewer.json` (source root and last level, saved on close). The browser's root is read from `settings.json` in the same folder.

### Reference sizes

Measured on an RTX 3060: median frame time including GPU work (`glFinish`), and CPU-only time, for scale only:

| Level | Models | Instances shown | Triangles | Draws | Frame | CPU |
| --- | --- | --- | --- | --- | --- | --- |
| Novalis `art_buildings.zone` | 136 | 3,110 | 1.40 M | 491 | about 7 ms | about 7 ms |
| Novalis (18 zones, 8 with models) | 286 | 9,891 | 13.4 M | 989 | about 11 ms | about 7 ms |
| Veldin | 166 | 11,591 | 13.3 M | 412 | about 13 ms | about 7 ms |

Draw counts include material overrides (without them: 424 and 829 for the two Novalis rows; Veldin has one overridden instance). Frame times were measured before overrides were drawn; the full Novalis level still captures at about 10 ms CPU time with them. The frames are rendered identically to drawing each zone separately (pixel-identical capture checked). Call overhead is about 7 ms per frame; the full Novalis and Veldin levels are bound by GPU work. All 286 Novalis models decode, including `tb_sewer_pipe_large_end`.

Full-Novalis native-toggle comparison on an RTX 3060: 1216x873 viewport, camera `118,-418,345,90,-35`, Viewer rig / ACES fit, sky and material overrides enabled. Four alternating blocks measured 60 frames each after 10 warm-up frames, with `glFinish` after each draw:

| Native translation | Frame median | CPU median | Opaque program selections |
| --- | --- | --- | --- |
| Off | 15.54 ms | 9.77 ms | 1 |
| On | 15.66 ms | 10.13 ms | 10 |

Both draw 989 batches and 13,418,448 triangles. The 0.12 ms frame difference is smaller than the variation between blocks (off medians 15.20-15.96 ms; on 15.44-15.90 ms). These paired measurements include the current sky and overrides; the earlier approximate 11 ms reference uses different conditions. That comparison routes 9 effective materials across 4 graphs through translation; the base-material eligibility count is 9 across 5 graphs because visibility, UV availability and replacements affect actual draws. A control frame using the original fragment source is pixel-identical to the shared-BRDF hand path.

With source-count pass partitioning and structural surface comparison, the same full-Novalis configuration routes 10 effective materials across 5 graphs through translation. The additional material is an instance replacement using the opaque glowing-foliage graph; it accounts for 36,800 placed triangles (0.27%) but changes no pixels at this camera. The 233 base-material eligibility counts remain unchanged. The detail graph's rocks account for 1,810,808 placed triangles (13.49%) with Highest LOD, look 0 and overrides; its 31 corpus materials now have ready bounded receipts, while the exact viewer rule still supplies their surfaces and detail-normal composition remains omitted from translation.

A paired measurement that changes only newly ready surface sources, keeping previous native programs enabled, gives 14.60 ms completed frame / 8.75 ms CPU before and 14.27 ms / 8.80 ms after. Opaque program selections increase from 10 to 11; batches and triangles stay unchanged. Block medians span 14.03-15.12 ms, so these measurements establish no consistent frame-time improvement. The source changes add 13 native-eligible materials overall, reaching 233 materials across 51 graphs ([rule coverage](viewer.md#rule-coverage)); the fixed-camera captures are pixel-identical.

For ranked structural candidates, dat1lib cross-checks and serialized schemas across the archive, see [corpus research](research.md). Inferred layouts remain hypotheses until independently decoded and validated.

## Known limitations

- Materials: foliage is cut out by `_c` alpha ([leaf cut-out](viewer.md#leaf-cut-out)) at each material's decoded static-mesh threshold; runtime draw cut/fade, the near-camera fade, dither and normal fade are omitted. Large planes whose materials have no `_c` texture draw flat white (some Novalis surfaces, Veldin's large ground / backdrop plane). Terrain layer textures and other layered or foliage graphs are not fully translated. The research notes counted 88 of 222 Novalis environment materials as unsupported graphs (colour-only fallbacks in the Blender path). In the viewer's rule coverage, the 233 materials of models placed in Novalis zones are 183 exact rule, 9 native translation across 5 graphs, 4 family fallback and 37 generic reading (see [rule coverage](viewer.md#rule-coverage)).
- Water: the `pok_uniq_water` replacement materials on the ocean and pool planes draw with the [water preview](viewer.md#glass-and-forward-materials), an approximation, not the game's water shader. Planes that two overrides stack at one position draw both layers.
- Replacement materials go through the same rules as other materials; they are not included in the rule-coverage counts above (with overrides, 292 materials load for Novalis).
- Material-override entries whose slot name the placed model lacks (22% of entries) are not applied. Whether the game applies them some other way (for example by slot position) is not known; they are counted on the status line.
- Gameplay actors use the source-placement layer below. Runtime quest/save/spawn visibility, animation, attachments and unproven drawing components remain unsupported; ambiguous classes are skipped and reported.
- Not handled: `_imposter` zones, quest-overlay regions, VFX, lights, collision volumes, `.level` spawn records (`0x3395AEC1`), decals, fog, sky meshes on graphs other than `Rk_Sky_Dome`, the stars term, cloud scrolling (time is frozen) and the atmosphere scattering tables.
- Undecoded instance fields: flags at `+0x5C`, the tail blocks other than material overrides, the heap's other objects.

## Gameplay actor decoder (`actor_format.py`)

`decode_actor(data)` reads an actor's dedicated primary-model pointer, local record, indexed dependencies and component definitions. `decode_zone_actors(data)` reads actor placement matrices and component ownership even in zones without a model-instance table. These functions supply source data to `actors.ActorCatalog`, used by the viewer. No animation or script execution is included.

### Actor-to-model binding

| `.actor` section | Confirmed wire layout |
| --- | --- |
| `0x32FAC8E0` | Exactly one u32 absolute string-pool offset: the primary `.model` path, or an empty string |
| `0x364A6C7C` | Local record: float32[16] row-vector matrix at +0x00, record kind at +0x40, declared byte size at +0x44 |
| `0x3AB204B9` | Array of u32 absolute string-pool offsets to dependencies; this game's entries are not the modern 16-byte reference format |
| `0x135832C8` | 16-byte component definitions: u32 name string offset, u32 CRC32(name), u32 absolute payload offset, u32 payload byte size |
| `0x6D4301EF` | Actor component payloads |

The primary pointer, rather than a string scan or name similarity, is the only candidate model-selection rule. Dependency models include destroyed crate chunks and spawned/attached parts. Dependency actors can form deeper chains: the corpus reaches five edges from the Clankbot spawner pad. Those edges do not establish model inheritance or attachment transforms, so an empty primary never falls back to a dependency's model. Every readable actor descriptor has an identity local matrix. Model-bearing descriptors store 176-byte local records; two empty-primary light descriptors store 152 bytes while declaring 160, a reported non-render exception.

All 822 actor types referenced by zones were checked, followed recursively through the structural dependency table: 1,127 descriptors, 1,126 readable and one missing asset. Of the direct types, 720 have a primary model. The combined descriptor catalog references 887 distinct primary models. The viewer selects that dedicated primary, Highest LOD and look 0. This is an explicit preview rule, not a claim of the runtime initial state. Secondary models, broken pieces and dependencies are not selected for drawing; an empty primary stays unresolved. Directly placed helper actors use their own structural primary pointer.

### Zone placement and components

| Section / actor-table offset | Type | Meaning |
| --- | --- | --- |
| `0x70682CB8` | 32-byte records | Actor placements; offsets below are relative to each table record |
| +0x00 | u32 | Name index in `0xDC625B3D`; `0xFFFFFFFF` means unnamed |
| +0x04 | u32 | Actor-path index in `0x78684035` |
| +0x08 | u32 | Placement record offset relative to heap `0x06ABCAB2` |
| +0x0C | u32 | First component-definition index in `0x50EDC53D` |
| +0x10 | u32 | Component count; every definition is owned exactly once |
| +0x14 | u32 | Raw flags (observed 0, 4, 8, 12); spawn/visibility meaning unresolved |
| +0x18 | u64 | Actor ID, unique in the zone |
| `0x50EDC53D` | 16-byte records | Same component-definition layout as an actor file; payloads are in `0x81999057` |

Placement records retain the complete finite affine float32[16] matrix at +0x00, using the model-instance row-vector convention. They must not be decomposed into TRS. +0x40 is a record-kind discriminator (0, 1, 2, 4); +0x44 is the record's byte count, not a scale or bounding volume. Kind-0 render headers carry the matching actor ID at +0x48 and model sentinel `0xFFFF` at +0x58. Other kinds have different headers: their +0x48 must not be treated as a render-record ID. Existing per-instance material-override blocks are decoded on render records using their own bounds and the zone replacement list, then applied through `scene.resolve_overrides`. Runtime SetMaterialAction swaps are not applied.

The decoder raises on container/section bounds, duplicate tags, malformed string references, invalid indices, duplicate IDs, nonfinite/nonaffine matrices, record sizes/alignment, render ID/sentinel mismatches, component name CRCs, payload ranges and component ownership failures. A final actor record may extend fewer than eight bytes beyond an unpadded heap, as with model instances (ten corpus cases, all four bytes).

`read_component(data, component)` reuses the guarded generic dat1lib SerializedSection reader under the explicitly named 202300 wire-layout hypothesis, not a game-version claim. Unknown types and out-of-range reads raise; extra objects and unread ranges are reported with absolute offsets/raw bytes. The corpus has 45 actor-descriptor and 156 zone-component payloads with unread four- or eight-byte tails; successful parsing is not complete semantic coverage. ActorAttachment, ScalePrototyping, PositionOffset, MaterialOverrideComponent and StaticMaterialOverrideList data are retained, but attachment placement, component scale, spawn visibility and default-material-state behavior are not implemented. MaterialConstant values are not interpreted as shader parameters.

### Corpus counts and unresolved classes

All 233 actor-bearing zones decode with no layout invariant or archive repeated-copy failures: 40,267 actors in the main `levels/` namespace plus eight legacy `rc1/levels/` actors. 18,565 placements have a structural primary-model binding; 18,559 pass the bounded geometry preflight after keeping the three known viewer failure classes unresolved. The other actors have no primary render model (21,709) or a missing actor asset (one Batalia `vol_simpleattach`).

Counts below follow each actual `.level` zone list, including shared zones. Primary-bound means the dedicated source pointer exists. Resolved means eligible geometry submitted, including default-hidden collision/template models. Drawn excludes that existing hidden filter with all zones enabled. Rigid/skinned classify resolved geometry by nonempty skin influences. Actors = rigid + skinned + unresolved; drawn + hidden = resolved.

| Level | Actors | Primary bound | Rigid | Skinned | Hidden | Drawn | Unresolved | Near static pairs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `aridia` | 1,728 | 738 | 264 | 453 | 20 | 697 | 1,011 | 0 |
| `batalia` | 3,310 | 1,651 | 1,045 | 589 | 22 | 1,612 | 1,676 | 0 |
| `deplanetizer` | 2,874 | 1,498 | 330 | 932 | 101 | 1,161 | 1,612 | 0 |
| `deplanetizer_exterior` | 832 | 62 | 7 | 53 | 0 | 60 | 772 | 0 |
| `deplanetizer_nefarious` | 329 | 58 | 14 | 25 | 1 | 38 | 290 | 0 |
| `gaspar` | 4,146 | 2,350 | 459 | 1,838 | 46 | 2,251 | 1,849 | 0 |
| `kalebo` | 3,884 | 767 | 171 | 474 | 22 | 623 | 3,239 | 0 |
| `metropolis` | 4,912 | 2,967 | 671 | 2,214 | 73 | 2,812 | 2,027 | 0 |
| `museum` | 540 | 344 | 331 | 1 | 82 | 250 | 208 | 0 |
| `nebula` | 1,661 | 867 | 330 | 444 | 76 | 698 | 887 | 4 |
| `novalis` | 1,943 | 794 | 307 | 480 | 31 | 756 | 1,156 | 0 |
| `phoenix` | 837 | 436 | 244 | 87 | 25 | 306 | 506 | 0 |
| `pokitaru` | 2,554 | 954 | 327 | 574 | 45 | 856 | 1,653 | 0 |
| `prequartu` | 1,122 | 364 | 241 | 59 | 81 | 219 | 822 | 0 |
| `quartu` | 3,072 | 1,411 | 569 | 645 | 111 | 1,103 | 1,858 | 0 |
| `rilgar` | 3,587 | 1,327 | 680 | 612 | 17 | 1,275 | 2,295 | 0 |
| `veldin` | 1,760 | 1,051 | 175 | 836 | 30 | 981 | 749 | 0 |

All actual level lists and all 233 actor-bearing zones pass the actor loader policy without invariant failures. Selected geometry is finite and has nonzero triangle area. This numerical check does not visually certify every bind pose. Proof views cover Plumber, Grimroth, vendors, enemies, crate clusters and a sewer door; their source bind/rest meshes are noncollapsed. No poses are authored.

Remaining classes and reasons:

- Empty primaries: volumes, curves, cameras, controllers and other non-render actors; no dependency model is substituted. One Batalia `volume/vol_simpleattach/vol_simpleattach.actor` asset is missing.
- Known model failures: `npc_skidd_mcmarx` (two Aridia actors), `npc_skidds_agent` (three Aridia actors), `npc_ryno_dealer` (one Rilgar actor). These remain explicitly excluded even when a geometry-only decode returns arrays.
- Placement conflicts: Nebula airlock primary-model class, described below. Any same-model near static control with a conflicting full matrix excludes that model class for the selected zone list.
- Unproven drawing components: ScalePrototyping, ScaleOverTime, PositionOffset, CurveFollowerAttach, MaterialOverrideComponent, StaticMaterialOverrideList, StaticMaterialOverride, MaterialOverrideSyncComponent, RenderOverrideConstantSyncComponent, TriggerResponderChangeMaterial, AttachmentList and HoverboardAttach. Their affected placements remain unresolved, including components with empty payloads whose defaults are unproven.
- ActorAttachment/spawner components: typed AttachedActor/AttachedList and SpawnTemplate/SpawneeTemplate IDs mark referenced children/templates undrawn. Parent primary meshes can still draw; no joint-relative transform or spawned dependency is inferred.
- VFX actor or primary-model namespaces are outside the layer.

Local render-record bytes +0x58 through +0xAF are identical across all 1,025 model-bearing descriptors, including descendants: raw flags +0x5C = 0x00440000, candidate override count +0x99 = 0, block +0xA4 = 0xFFFFFFFF. Along with identity local matrices, this supplies no class-specific descriptor offset, scale or material variation. Unknown byte semantics are not inferred. Placement records have 1,224 override entries across the whole corpus and use the existing exact slot/material resolver; per-actor outcomes are retained.

The raw table flags 0/4/8/12 and runtime visibility remain unresolved. The layer previews source placements, including multiple quest-state Plumber placements that overlap in Novalis bind pose. Highest LOD / look 0 and default model materials are used only when no unproven drawing component blocks the actor. MaterialConstant is not interpreted. Per-level reason counts, actor paths, model bindings, IDs, offsets and hashes are retained in the private corpus report.

### Actor layer and identification

**Actors (source placements)** is enabled by default and independent of the existing zone toggles. Actor-only zone rows are enabled. `--no-actors` starts the standalone viewer with the layer hidden. Both states preload actor geometry/materials; toggling requires no archive reload. The normal draw path shares model geometry and batches full matrices by handedness and effective materials across visible zones. Static rows retain their original order and framing bounds; actor bounds are used only without static placements.

A click reports actor name, .actor path, resolved primary model and actor ID. Model-level picking is followed by an identity pass for that model only; individual actor IDs add click-time work, not regular frame draws. The source receipt retains the exact matrix, primary section/offset/type and actor/zone/model hashes. Double click continues to open the primary in the Model tab. Status shows visible actors drawn, unresolved placements and skipped-skinned (known skin-model failures or unusable bind geometry); component/placement policy skips count as unresolved.

### Actor performance and regression

RTX 3060, 1216x836 viewport, Viewer lighting / ACES fit, native translation, sky and material overrides enabled. Full Novalis camera is `118,-418,345,90,-35`; full Veldin is `75,-160,85,90,-25`. Two 30-frame blocks per state follow five warm-up frames in off/on/on/off order, with glFinish per frame; the table reports the median over 60 samples. Block medians vary, especially for Veldin, so these are representative preview costs.

| Level | Worker load (either toggle state) | Actor decode/resolve/geometry | Frame off/on | CPU off/on | Draws off/on |
| --- | ---: | ---: | --- | --- | --- |
| novalis | 8.48 s | 1.25 s | 16.52 / 18.19 ms | 9.43 / 10.83 ms | 989 / 1152 |
| veldin | 4.02 s | 1.00 s | 8.81 / 14.90 ms | 3.94 / 8.64 ms | 412 / 631 |

Pre-change worker loads in the corresponding baseline run were 5.65 s for Novalis and 3.14 s for Veldin. The new totals include actor texture loading; actor_seconds excludes textures. Hiding the preloaded layer saves frame cost, while retaining its load/memory cost. The actors-off full-level PNG pixels match the pre-change frames exactly at the same cameras and viewport sizes. GPU picks identify the crate, door, NPC/vendor and enemy proof placements. The available reference folder has no matching Novalis/Veldin actor camera, so no direct reference-image match is claimed.

### Placement controls

All four airlock actors in Nebula `gp_base.zone` are within 0.00265 native units of static instances of their exact primary model in the actual `neb_art.zone`, but face approximately 180 degrees away from those instances about Y. For example, `AIRLOCK_TRESPASSER_1` has first-axis X/Z `(-0.258819, +0.965926)`, while the coinciding static instance has `(+0.258819, -0.965926)`. Their actor descriptors have identity local matrices and no per-instance component payload explaining the difference. Matching boss-door matrices exist only in an unreferenced debug art zone, which is not part of `nebula.level`.

The full-zone probe finds 21 same-model near pairs for 17 actors, including unreferenced/debug zones; eight match full matrices (<1e-6 maximum element difference). Actual level lists contain only the four Nebula airlock conflicts. Same-model translations within 0.05 units are reported. Full matches suppress the actor in favour of the selected static placement, even when materials differ; conflicting matrices block the model class. Classification precedes zone visibility toggles, so suppressed actors remain suppressed when their static zone is hidden. Co-location alone never supplies a corrected transform.

The airlock class stays excluded while other proven primary bindings draw. A RenderDoc capture of the Nebula airlock draws, especially both Trespasser doors, with identified model/vertex buffers, full world and bind/bone matrices, and the visibility/state of coinciding static doors would settle whether runtime applies a transform/pose adjustment or the source intentionally represents opposite-facing geometry. No rotation or scale is tuned to hide the disagreement.

## Enemy factory coverage and experimental preview

Source actor counts are not runtime enemy population counts. In Novalis the four Peckbot and two Birdbot model records are templates used by script factories. **Actors (source placements)** retains the stored-record preview policy. **Enemy spawns (experimental)** is a separate, off-by-default inspection layer; `--experimental-spawns` enables it in the standalone viewer. Source actor visibility and zone toggles remain independent. All eligible factory assets preload in either state.

`spawn_format.py` structurally decodes `SunsetSpawnerFactoryScriptAction` inputs. Models still come exclusively from each referenced template's `.actor` primary pointer in `0x32FAC8E0`, Highest LOD / look 0, and the existing bind/rest geometry and material rules. Template material overrides reuse the normal instance path. Runtime material actions, animation, scripts and encounter activation are not applied. A click labels the synthetic instance approximate and reports its factory, template and location IDs alongside its actor/model paths. Status reports experimental draws and unresolved placements separately from source actors.

| Source | Decoded input |
| --- | --- |
| `0x5E54ACCF` / `0xEF8637D5` | 32-byte node record, action-name index and matching CRC32; factory ID and parameter-binding range |
| `0xBEAB52E7` | 12-byte binding: pin CRC32, parameter index, trailing pin CRC32 |
| `0xD86A7934` | 24-byte parameter `<HHHHQQ>`; kind 5 actor index plus matching u64 ID, or group ID with actor index `0xFFFF`; kind 2 low-u32 float literal for count |
| `0x0CF58A6E` / `0x041071EF` | Group ID selects u16 actor indices; count is the low u16 of the packed count/flags word, with the upper u16 retained as uninterpreted flags |
| `Templates` / `Locations` / `InitialCount` | Matching CRC32 `0xEDD5E282` / `0x951BD5B6` / `0x90D607B0`; typed source targets and literal count |

Novalis has 39 bound factories requesting 123 enemies: 84 Peckbots across 18 factories, 20 Birdbots across 11, eight Blarg troopers, one armored transport, four medium and six small Amoeboids. All 123 preview in bind pose. These sums cross conditional encounter/checkpoint states and are not simultaneous gameplay populations. Veldin previews 75 of 78 requested enemies across 36 factories; three lack bound locations.

**Approximate placement policy:** cycle templates and locations in source binding order for the factory's total literal InitialCount. Retain the template's complete linear matrix, including scale, shear and mirroring. Replace only translation with the referenced location actor's translation. For repeated anchors, spread instances on a centred 2.5-unit grid in native X/Z, leaving native Y unchanged. This grid, template choice and location cycling are presentation choices. A volume's linear matrix never scales an enemy. Paths are represented by their actor anchor, not sampled along their curve. Source template records and the normal actor layer remain unchanged, so stored templates may also appear when both layers are on.

Locations include points, spatial volumes, paths and groups. Node 229 requests five Peckbots from one StaticVolume; node 465 requests five from eight paths. Factory counts and references are source-traced; exact world matrices, orientation selection, distribution, ground contact, visibility and spawn order remain unproven. Floating/intersecting enemies and bind-pose shapes are expected test limitations and are not corrected by tuning transforms. Runtime captures with factory/template IDs and individual enemy world/bone matrices would establish actual placement. The experiment deliberately combines every eligible encounter state and excludes other spawning mechanisms.

The real-archive check covers all 233 zones without an invariant failure: 807 factories with identifiable enemy templates, 3,040 known requested placements, 3,011 usable bind-pose previews. Thirty-three factories remain unresolved (some have no literal count): 16 with ScalePrototyping, eleven without locations, five without a single literal count, and two with external/unresolved references; reasons overlap. Known unsupported models, missing structural primaries, nonidentity actor-local transforms and unproven drawing components stay excluded. Counts are per source-zone inventory; actual-level membership is used separately for per-level receipts.

Full Novalis and Veldin GPU proofs identify generated Peckbots, Birdbots and Horned Toads. With the experiment off, both levels match the previous build pixel for pixel at the same 1216×873 camera, with source actors both on and off. Timing below uses all zones, source actors on, warmed OpenGL rendering plus `glFinish`, 60 samples per state in reversed blocks. Both states share one preloaded scene; enabling adds no archive reads.

| Level | Load with assets preloaded (s) | Experiment off (ms/frame) | On (ms/frame) | Extra enemies | Draw calls off / on |
| --- | --- | --- | --- | --- | --- |
| Novalis | 8.14 | 19.53 | 19.12 | 123 | 1152 / 1152 |
| Veldin | 4.45 | 11.86 | 11.92 | 75 | 631 / 631 |

The small frame-time differences are within run variability, not evidence of a speedup or a reliable fixed cost. The extra rows share existing model/material batches; these two levels add no draw calls. Load times include the experimental assets even when its checkbox is off and are warm-run observations, not cold-load comparisons.

## Actor material swaps (runtime, not applied)

Zones also hold actors and their scripts. Their runtime material swaps do not apply to the model-instance table or the source-placement actor preview. The script-table layouts below were checked on all 113 main-game zones that have script nodes (42,587 nodes) without a failure. Factory inputs are now decoded for the explicit experimental layer; runtime material actions remain research-only. Actor placement and component ownership are now decoded separately by `actor_format.py`:

| Section | Content |
| --- | --- |
| `0x70682CB8` | 32-byte actors: u32 name index (`0xDC625B3D`), u32 actor-type index (`0x78684035`, `.actor` paths), u32 heap offset of a placement record (kind-0 render header: matrix, byte size, actor ID at `+0x48`, model index `0xFFFF`; not listed in `0x6987F172`), u32 first component index, u32 component count, u32 raw flags, u64 actor ID |
| `0x5E54ACCF` | 32-byte script nodes: u64 id, u16 input-pin count, u16 + u16 first and count of outgoing links, u16 + u16 first and count of parameter bindings (all in `0xBEAB52E7`), u16 action index (`0xEF8637D5` names), u32 CRC32 of the action name, u32 offset and size of the node's object in the serialized `0x2300D240` |
| `0xBEAB52E7` | 12-byte links: CRC32 pin name, node or parameter index, CRC32 pin name (`Out`, `In`, `Apply`, `Reset`, `Target`, ...) |
| `0xD86A7934` | 24-byte parameters: u16 kind, u16 special name index; kind 5 references an actor (u16 index into `0x70682CB8` and the u64 actor id) or an actor group |
| `0x0CF58A6E` | 24-byte actor groups: u32 index, u32 start, u16 count and u16 raw flags for actor indices in `0x041071EF`, `0xFFFFFFFF`, u64 group id |

`MaterialOverrideData {SourceMaterialName, Replacement}` is the payload of `SetMaterialAction` nodes, which have `Apply` and `Reset` inputs and a `Target` parameter. All 42 in the main game target actors: 55 bindings to single actors and 3 to actor groups (82 actors). On each target's model, `SourceMaterialName` names exactly one material slot. They fire on gameplay events (entering volumes, power state, checkpoints, signal relays, save flags), so they are runtime states, not placement data. Four Nebula airlock-door actors share their position with static instances of the same model in `neb_art.zone`, which are drawn with their own materials. The `MaterialConstant` fields in `0x81999057` belong to gameplay actor components and are not decoded.
