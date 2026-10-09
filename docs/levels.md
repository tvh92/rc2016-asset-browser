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

`decode_zone(data)` returns `models` (paths with `/` separators), `instances` and `models_used`. Zones without the three sections below raise `ValueError('Zone lacks instance sections ...')`.

| Section | Content |
| --- | --- |
| `0xC6A5905E` | `u32[n_models]` file offsets of NUL-terminated model paths (backslash-separated in the file) |
| `0x6987F172` | `u32[n_inst]` offsets of instance records, relative to the start of `0x06ABCAB2` |
| `0x06ABCAB2` | data heap holding the instance records (and other, undecoded objects) |
| `0x032D450A` | `(u32 string offset, 0xFFFFFFFF)` pairs pointing to material paths (not needed for placement, not decoded in code) |

Instance record (offsets relative to the record):

| Offset | Type | Meaning |
| --- | --- | --- |
| `+0x00` | float32[16] | 4x4 matrix, row-vector convention: rows 0-2 are the local axes including scale, row 3 is the translation, w column `(0,0,0,1)`. Native Y-up game space |
| `+0x44` | u32 | record size: `0xB0` plus an optional tail; records are 8-byte aligned |
| `+0x48` | u64 | per-instance id, unique within the zone |
| `+0x58` | u32 | index into the `0xC6A5905E` model list |
| `+0x5C` | u32 | flags, kept raw (values seen: `0x440000`, `0x440020`, `0x740000`, ...; meaning unknown) |
| `+0xB0` | bytes | optional tail, kept raw as hex; not decoded |

The decoder enforces these invariants and raises if any fails: size field is at least `0xB0` and inside the heap; size field matches the stride to the next record (rounded up to 8); the last record's tail may overrun an unpadded heap by fewer than 8 bytes (13 of the 229 zones); w column is `(0,0,0,1)`; model index is in range; ids are unique.

Validation: every `.zone` in the archive that has the three sections decodes with all invariants holding, 229 zones and 228,727 instances. 585 zones have no model-instance table (lighting, sound, nav, gameplay-only). In Novalis `art_buildings.zone`, 3,167 instances use all 136 listed models. 3,999 matrices in the corpus are sheared (worst axis cosine 0.685) and 17,511 are mirrored, so placement must keep the full matrix (no TRS decomposition).

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

The level viewer does not load atmosphere assets and draws no sky; its background is a flat clear colour.

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
| `level_format.py`, `zone_format.py` | The decoders described above |

### Loading

`load_level` runs on a worker thread and posts events that the Tk thread applies; Tk and GL are touched only from the Tk thread. Order:

1. The zone list (`.level` -> zone paths; a single `.zone` path is treated as a one-zone level). `--zones` filters by short name or path.
2. Each zone's instance table (`zone_scene`). Instances are grouped per model and per handedness (mirrored or not) as flat arrays of 16 floats per matrix. Zones without a model table are listed but disabled ("no models"); a zone that fails to decode is disabled as "unreadable" and the error is logged to stderr.
3. Scene bounds from the translations of visible instances.
4. Each unique model's geometry, most-placed first (`model_geometry`: Highest LOD, look 0, decoded with numpy; V is flipped back because the decoder flips it for Blender). The view fills in as models arrive.
5. Materials and textures by material path, using the model viewer's Shaded rules (`rc2016.viewer.textures.material_textures`). Each material graph is parsed once and cached.
6. A `done` event with timings.

A new load cancels the previous one (generation counter; stale events are dropped). The panel opens its own `Archive` (not shared with other tabs) with `verify_repeats = False`, which reads only the first copy of repeated assets; the pipeline keeps that check on. The source root may be any folder that contains exactly one archive root (`locate`); it needs the level assets (the `Image0` tree). Both `CUSA01073` and `Image0` list the same 17 levels.

### Rendering

- Geometry and instance matrices stay native Y-up. The presentation basis is applied once, in the camera matrix (`BASIS` in `viewport.py`), so the fly camera works in Z-up.
- Each model has static vertex buffers (positions, normals, UVs, indices). Each (zone, handedness) group gets one instance buffer drawn with `glDrawElementsInstanced`; the four matrix rows are vertex attributes 10-13 (`INSTANCED_VERTEX` in `rc2016/viewer/shading.py`). Needs OpenGL 3.3 instancing; otherwise the view reports which entry point is missing.
- Normals use the cofactor matrix times sign(det), so sheared and mirrored instances shade correctly. Mirrored groups draw with `glFrontFace(GL_CW)` so `gl_FrontFacing` keeps meaning "outside".
- Draws are sorted by material, and each material's uniforms go through the model viewer's `_apply_material`. Zone toggles only filter instance groups; no buffers are rebuilt.
- Forward-shaded (glass) materials draw after all opaque ones with material blend and cull state and no depth write. Instances are not depth-sorted.
- Lighting is the model viewer's rig in view space (it turns with the camera). It is not game lighting.
- Background is a flat colour; no sky, decals, lights or fog.

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

Zone list: per-zone checkbox with placed-instance count, **All** / **None** buttons. The status line shows load state, zone and model counts, failures, material and texture counts, visible instances and triangles, draw calls, CPU frame time and speed.

### Command line

```
python -m rc2016.levels.viewer [--source-root <extracted game folder>]
                               [--level <.level or .zone asset path>]
                               [--zones a,b,...]
                               [--mode Shaded|Textured|Solid|Wireframe|"Solid + wireframe"]
                               [--capture out.png] [--camera x,y,z,yaw,pitch]
```

| Option | Meaning |
| --- | --- |
| `--source-root` | Folder holding the extracted archive. Default: the viewer's own saved root, else the asset browser's saved source root |
| `--level` | A `.level` or single `.zone` asset path, for example `levels/novalis/novalis.level`. Default: the last level used, else `levels/novalis/novalis.level` |
| `--zones` | Comma-separated zone short names or paths; only these zones load |
| `--mode` | Initial view mode (default `Shaded`) |
| `--capture` | Load the level, write one frame as an RGB PNG and exit |
| `--camera` | Camera `x,y,z` (presentation space, Z-up), yaw and pitch in degrees, applied after loading; alone it leaves the window open |

`--capture` and `--camera` take effect when loading finishes. With `--capture` the settings file is not updated and the process prints the capture path, success flag, load timings and view stats.

Settings are stored in `%APPDATA%/RC2016 Asset Browser/level_viewer.json` (source root and last level, saved on close). The browser's root is read from `settings.json` in the same folder.

### Reference sizes

Measured on an RTX 3060 (CPU frame time) in the research notes, for scale only:

| Level | Models | Instances shown | Triangles | Draws | Frame |
| --- | --- | --- | --- | --- | --- |
| Novalis `art_buildings.zone` | 136 | 3,110 | 1.40 M | 428 | about 10 ms |
| Novalis (18 zones, 8 with models) | 286 | 9,891 | 13.4 M | 844 | about 20 ms |
| Veldin | 166 | 11,591 | 13.3 M | 430 | about 11 ms |

Frame cost is dominated by per-call ctypes overhead over the draw calls, not GPU work. All 286 Novalis models decode, including `tb_sewer_pipe_large_end`.

## Known limitations

- Materials: foliage shows as solid blobs (leaf cut-out not decoded); large planes whose materials have no `_c` texture draw flat white (some Novalis surfaces, Veldin's large ground / backdrop plane). Terrain layer textures and other layered or foliage graphs are not fully translated. The research notes counted 88 of 222 Novalis environment materials as unsupported graphs (colour-only fallbacks in the Blender path).
- Not handled: gameplay actors (`gp_*` zones), `_imposter` zones, quest-overlay regions, VFX, lights, collision volumes, `.level` spawn records (`0x3395AEC1`), decals, sky and atmosphere.
- Undecoded instance fields: flags at `+0x5C`, the record tail, the heap's other objects, `0x032D450A` material overrides.
- No click-to-identify of instances.
