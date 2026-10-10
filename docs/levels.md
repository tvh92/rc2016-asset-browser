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
- Batches of one model with the same material and instance buffers that follow each other in the index buffer merge into one draw. Material uniforms and texture bindings are recorded once per material (`_Recorder` around `ModelViewport._apply_material`), and only the ones that differ from the current state are issued. `glFrontFace` is set only when handedness changes. Draws stay sorted by material.
- Normals use the cofactor matrix times sign(det), so sheared and mirrored instances shade correctly. Mirrored groups draw with `glFrontFace(GL_CW)` so `gl_FrontFacing` keeps meaning "outside".
- Forward-shaded (glass) materials draw after all opaque ones with material blend and cull state and no depth write. Instances are not depth-sorted.
- Lighting and tonemap use the model viewer's selectors ([Lighting and tonemap](viewer.md#lighting-and-tonemap)). The Viewer rig is fixed to the world (rig right = +X, up = +Z, towards the viewer = -Y in Blender's Z-up world), so lighting does not shift while orbiting or flying. Shared HDRI lights the level from the shared review HDRI, with level directions taken through the presentation basis. This is not game lighting.
- The sky ([sky](#sky-in-the-level-viewer-skypy)) is drawn before the level; without one the background is a flat colour. No decals, lights or fog.

### Modes

`Shaded` (model-viewer material rules), `Textured`, `Solid`, `Wireframe`, `Solid + wireframe`. Batches without UV0 draw as Solid.

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
| Left click (no drag) | Identify the model under the pointer |
| Double click | Open the identified model in the Model tab (asset browser) and load it; it does not reset the camera |

Zone list: per-zone checkbox with placed-instance count, **All** / **None** buttons. The side column also has the **Lighting** (`Viewer rig` or `Shared HDRI`) and **Tonemap** (`ACES fit` or `Khronos PBR Neutral`) selectors, which work as in the model viewer ([Lighting and tonemap](viewer.md#lighting-and-tonemap)), and the **Show collision/template models**, **Instance material overrides** and **Sky (atmosphere cloud dome)** checkboxes. The status line shows load state, zone and model counts, failures, material and texture counts, the number of instances drawn with material overrides and of override entries not applied, the sky (cloud domes drawn, sky meshes not ported, captured clear sky or flat background), visible instances and triangles, draw calls, frame time (ms) and speed.

### Click to identify

A left click without dragging identifies the model under the pointer. Each visible model is drawn into the back buffer in a 24-bit id colour and one pixel is read, so the result identifies the model, not the individual instance. The picked model's visible instances are outlined in orange, and the line under the status shows its path and visible instance count. Double click, or **Open in Model tab** (in the asset browser; the standalone viewer has no Model tab), selects the model in the Model tree and loads it.

### Command line

```
python -m rc2016.levels.viewer [--source-root <extracted game folder>]
                               [--level <.level or .zone asset path>]
                               [--zones a,b,...]
                               [--mode Shaded|Textured|Solid|Wireframe|"Solid + wireframe"]
                               [--capture out.png] [--camera x,y,z,yaw,pitch]
                               [--no-material-overrides] [--no-sky]
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

For ranked structural candidates, dat1lib cross-checks and serialized schemas across the archive, see [corpus research](research.md). Inferred layouts remain hypotheses until independently decoded and validated.

## Known limitations

- Materials: foliage shows as solid blobs (leaf cut-out not decoded); large planes whose materials have no `_c` texture draw flat white (some Novalis surfaces, Veldin's large ground / backdrop plane). Terrain layer textures and other layered or foliage graphs are not fully translated. The research notes counted 88 of 222 Novalis environment materials as unsupported graphs (colour-only fallbacks in the Blender path). In the viewer's rule coverage, the 233 materials of models placed in Novalis zones are 168 exact rule, 4 family fallback and 61 generic reading (see [rule coverage](viewer.md#rule-coverage)).
- Water: the `pok_uniq_water` replacement materials on the ocean and pool planes draw with the [water preview](viewer.md#glass-and-forward-materials), an approximation, not the game's water shader. Planes that two overrides stack at one position draw both layers.
- Replacement materials go through the same rules as other materials; they are not included in the rule-coverage counts above (with overrides, 292 materials load for Novalis).
- Material-override entries whose slot name the placed model lacks (22% of entries) are not applied. Whether the game applies them some other way (for example by slot position) is not known; they are counted on the status line.
- Not handled: gameplay actors (`gp_*` zones and actors in art zones, see below), `_imposter` zones, quest-overlay regions, VFX, lights, collision volumes, `.level` spawn records (`0x3395AEC1`), decals, fog, sky meshes on graphs other than `Rk_Sky_Dome`, the stars term, cloud scrolling (time is frozen) and the atmosphere scattering tables.
- Undecoded instance fields: flags at `+0x5C`, the tail blocks other than material overrides, the heap's other objects.

## Actor material swaps (not drawn)

Zones also hold actors and their scripts. Their material swaps do not apply to the model-instance table, and the viewer does not draw actors. These layouts were checked on all 113 main-game zones that have script nodes (42,587 nodes, 40,267 actors) without a failure, but no repository code decodes them:

| Section | Content |
| --- | --- |
| `0x70682CB8` | 32-byte actors: u32 name index (`0xDC625B3D`), u32 actor-type index (`0x78684035`, `.actor` paths), u32 heap offset of an instance-like record (matrix, size, the actor id at `+0x48`, model index `0xFFFF`; not listed in `0x6987F172`), 3 x u32 not decoded, u64 actor id |
| `0x5E54ACCF` | 32-byte script nodes: u64 id, u16 input-pin count, u16 + u16 first and count of outgoing links, u16 + u16 first and count of parameter bindings (all in `0xBEAB52E7`), u16 action index (`0xEF8637D5` names), u32 CRC32 of the action name, u32 offset and size of the node's object in the serialized `0x2300D240` |
| `0xBEAB52E7` | 12-byte links: CRC32 pin name, node or parameter index, CRC32 pin name (`Out`, `In`, `Apply`, `Reset`, `Target`, ...) |
| `0xD86A7934` | 24-byte parameters: u16 kind, u16 special name index; kind 5 references an actor (u16 index into `0x70682CB8` and the u64 actor id) or an actor group |
| `0x0CF58A6E` | 24-byte actor groups: u32 index, u32 start, u32 count of u16 actor indices in `0x041071EF`, `0xFFFFFFFF`, u64 group id |

`MaterialOverrideData {SourceMaterialName, Replacement}` is the payload of `SetMaterialAction` nodes, which have `Apply` and `Reset` inputs and a `Target` parameter. All 42 in the main game target actors: 55 bindings to single actors and 3 to actor groups (82 actors). On each target's model, `SourceMaterialName` names exactly one material slot. They fire on gameplay events (entering volumes, power state, checkpoints, signal relays, save flags), so they are runtime states, not placement data. Four Nebula airlock-door actors share their position with static instances of the same model in `neb_art.zone`, which are drawn with their own materials. The `MaterialConstant` fields in `0x81999057` belong to gameplay actor components and are not decoded.
