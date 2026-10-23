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
| `+0x9C` | u32 | number of [position-keyed vertex colours](#position-keyed-vertex-colours) |
| `+0xA4` | u32 | record-relative offset of the material-override block, `0xFFFFFFFF` when there is none |
| `+0xA8` | u32 | record-relative offset of the vertex-colour keys, `0xFFFFFFFF` when there are none |
| `+0xAC` | u32 | record-relative offset of the vertex colours |
| `+0xB0` | bytes | optional tail, kept raw as hex. Holds the material-override block, the vertex-colour block and a block at `+0xA0` (with `+0x9A`) that is not decoded |

The decoder enforces these invariants and raises if any fails: size field is at least `0xB0` and inside the heap; size field matches the stride to the next record (rounded up to 8); the last record's tail may overrun an unpadded heap by fewer than 8 bytes (13 of the 229 zones); w column is `(0,0,0,1)`; model index is in range; ids are unique; a nonzero override count has a block, and the block lies inside the record.

Validation: every `.zone` in the archive that has the three sections decodes with all invariants holding, 229 zones and 228,727 instances. 585 zones have no model-instance table (lighting, sound, nav, gameplay-only). In Novalis `art_buildings.zone`, 3,167 instances use all 136 listed models. 3,999 matrices in the corpus are sheared (worst axis cosine 0.685) and 17,511 are mirrored, so placement must keep the full matrix (no TRS decomposition).

### Position-keyed vertex colours

An instance can carry vertex colours for its model, keyed by position. `zone_format.vertex_colour_block` reads `n` (`+0x9C`) u64 keys at the offset in `+0xA8`, sorted ascending, then `n` 4-byte colours at the offset in `+0xAC`, directly after the keys and inside the record. A key packs three 21-bit fields (x, y, z from bit 0), each biased by 2^20 (bit 63 is also set). The layout was read on one record, the Novalis terrain `rp_novalis_level1_tfrag` in `art_terrain.zone`: 8,997 entries, the block ending the record. There a key is the model-space position x 10, rounded: 8,995 keys lie within 0.6 units per axis of a vertex of the placed model, with residuals from -0.5 to 0.5625, so the keys were rounded from positions finer than the model's int16 grid. Only part of the terrain is listed (9,631 of 44,982 vertices). Byte 3 is 0 along the dirt paths and plaza seen in the game and 255 on the grass around them; byte 0 is roughly its inverse.

The level viewer uses byte 3 as the model's colour alpha (`scene.keyed_vertex_colours`, `instance_vertex_alpha`): each vertex takes the colour of the nearest key within 0.6 units per axis, unlisted vertices take 255, and the alpha feeds the squared vertex-alpha input of the blend rules (`vRockAlpha`, [viewer](viewer.md)). The terrain blend graph's vertex shader squares semantic 5 alpha, or on one per-draw path a value loaded from a separate typed buffer, which this block is taken to fill. That mapping, the byte order, the meaning of bytes 0-2 and the value for unlisted vertices are inferred, not decoded. The viewer keeps geometry shared and partitions instance draws by handedness, effective material overrides and keyed paint-block identity. Each distinct source block is matched to the model once and owns its alpha and candidate ground buffers; identical blocks share those buffers across zones. Missing blocks keep white alpha and the ground-preview fallback, including actor/spawn instances of a painted static model. Paint identities travel with the original matrix rows through grouping, override resolution, zone visibility and culling; differing paint is no longer discarded. The batched loader carries the blocks across zones before matching, and the GPU uses a per-vertex attribute with divisor 0; models without colours restore the generic alpha 1 for every drawing pass. The status line counts the models drawn with keyed alpha. A corpus layout check covered 858 zone assets, including 251 instance tables and 280,646 records: all 5,353 records with a keys offset other than `0xFFFFFFFF` passed the block checks, across 66 zones. These are structural layout counts; the position scale, byte meanings and runtime transport remain inferred beyond the Novalis control. The explicit ground-layer preview also receives bytes 0/1 and a matched-key flag through attribute 15; its vertex shader squares matched channels before interpolation, and unlisted vertices retain the existing review controls. This candidate buffer join is separate from native material readiness. The Blender research pipeline builds single models, not levels, and does not use these colours.

### Static packed-vector preview eligibility

The experimental static packed-vector preview reads the model's packed 10:10:10 UNORM value at vertex byte `+8`, recentres it componentwise to `2u - 1`, and retains its magnitude. It is a separate input from the ordinary decoded normal. The source vertex expression agrees across the examined pass producers under flags 1 and 61; this does not identify the runtime-selected pass. The viewer transforms the vector with the source CPU matrix helper: equal squared axis lengths within 0.001 select a copy of the linear axes; otherwise it uses the inverse transpose, with unscaled cofactors below the determinant threshold. Translation is excluded.

Zone records retain the raw instance-flags word at `+0x5C`. A model is eligible only when every known placement has finite transforms and none carries the currently rejected bits `0x00001000`, `0x00000400`, or `0x08000000`; these correspond to a billboard case, an untraced constructor special case, and runtime history. Eligibility is whole-model across its stored zones. Missing caller metadata, incomplete zone coverage, or visible actor/spawn layers with unknown instance context disables the preview. Geometry also fails closed when mesh flag `0x40` indicates the skinned path. This gate uses source instance and mesh state, not material identity. The adapter remains opt-in under [Experimental material previews](viewer.md#experimental-material-previews); it is not native readiness or game-equivalence evidence.

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

`level_sky(archive, zone paths, probe_position=None)` finds the level's sky. It returns None when no atmosphere zone names an atmosphere, and raises if the day atmosphere is not unique.

- **Which zones and which atmosphere.** An atmosphere zone is found by file name: the stem contains `atmosphere` (Novalis `atmosphere.zone`) or starts with `atm_` (Veldin `atm_lgt.zone`, `atm_lgt_night.zone`); `_imposter` twins are skipped. Several zones may name several atmospheres (Veldin: `atm_Veldin` and `atm_Veldin_Night_Darker`). An atmosphere is night when `night` (any case) is in its own file name or in the file name of any zone naming it; the folders do not count. `sky.choose_atmosphere` takes the only atmosphere, or of several the single day one, and raises when no or several day atmospheres remain. The result's `night` says which kind was chosen.

- **Atmosphere.** The atmosphere zone (`atmosphere.zone`; `_imposter` twins are skipped) holds the `.atmosphere` path as a string. Its sky models are the asset's `.model` references. The sun ray is the unit vector at header offset 72 (`rc2016/pipeline/lighting.py`, `_decode_ps4_atmosphere_header`), in native space.
- **Cloud domes.** A sky mesh is drawn only when its material's graph is `Rk_Sky_Dome` and the graph bytes match the decoded one (SHA-256 `DOME_GRAPH_SHA256`).
  - Constants: dwords 0-22 come from table 0, instance values over graph defaults (`dome_constants`).
  - Textures come from the graph's slots, overridden by the material's: the distortion noise (`NOISE_SLOT`) and the two cloud-layer slots (`DOME_SLOTS`). Which cloud slot feeds which layer is not decoded. Every dome material binds both to one texture; a material that does not is reported, not drawn. Material slots the graph does not have (Batalia and Gaspar carry two) are ignored.
- **Sun and probe.** `level_sky` also returns `sun_rgb` (the atmosphere's RGB96 at header offset 96, a direct-light candidate whose runtime field names are unresolved) and `reflection`, the reflection capture of the level. It is the single capture the chosen atmosphere's zones name (`textures/atmosphere/volumereflections/<id>/reflection.texture`; `reflection_source` is `atmosphere zone`), else the one chosen from the lighting zones' volume records ([Reflection volumes](#reflection-volumes-volumespy); `lighting volume`), else `None`. Novalis, Batalia, Kalebo, Nebula and Pokitaru name theirs in the atmosphere zone and read no lighting zone for it; Veldin, Museum, Phoenix, Quartu, Prequartu and Deplanetizer exterior take it from the volume records; Aridia, Deplanetizer, Gaspar, Metropolis, Rilgar and Deplanetizer Nefarious have none (Aridia's two art-zone captures belong to its store interiors and its exterior one is not in the archive; a level with a lightgrid still gets Level atmosphere lighting, [below](#level-atmosphere-lighting-probe_lightingpy)). A volume capture is a local probe and may show no sky: when it gives no clear-sky profile, the profile (and so the fog colour) is sampled from the first other volume of the level that does, and a note says so.
- **Unsupported sky graphs.** These are reported on stderr and counted on the status line: `rk_sky_dome_flat` (nebula and space domes), `rk_sky_matte` (planets), `rk_sky_cloudlayer`, and the Veldin planet graph.
- **Coverage.** Cloud domes are drawn for Batalia, Gaspar, Kalebo, Metropolis, Museum, Novalis, Pokitaru, Quartu, Prequartu and Rilgar, and flat nebula domes ([below](#flat-nebula-dome)) for Aridia, Deplanetizer (three levels), Nebula and Phoenix. Planet (`rk_sky_matte`) and cloud-layer graphs are still not ported.
- **Flat nebula dome.** `rk_sky_dome_flat` (graph SHA-256 bound in `sky.FLAT_GRAPH_SHA256`) is drawn with a port of its pixel program `0xdc0d410d`, which the translator turned into one expression (`sky.flat_constants`, `uSkyMode` 2 in `SKY_FRAGMENT`). With `N` the nebula texture at `(CB2[13] + CB2[14] u, CB2[12] + v)`, `s6` the star texture at `(5.5 CB2[8] u, 4 CB2[8] v)`, `s7a` at `(8u, 8v) + 0.12 t` and `s7b` at `(u, 1.1 + v)` (the same texture; `t` is the engine clock, frozen at 0): `glow = CB2[9] pow(sat(-sun . dir), CB2[7])`, `colour = CB2[11] (N.rgb + CB2[0..2] glow + sat(CB2[4..6] s6.rgb CB2[3] s6.r (-0.5 + 2 N.r + s7a.b s7b.g)))` and `alpha = sat(vertex alpha * sat(CB2[10] (N.r - 1) + 1))`, blended as `mix(fog colour, colour, T)` with `T` a vertex output. The constants are the material's CB2 over the graph defaults (Aridia: star gain 2, glow colour (0.14, 0.55, 1), exponent 9). Inferred, not decoded: the sign of the glow (taken toward the atmosphere sun, as the cloud dome), the vertex alpha (1) and `T` (1, so the engine fog colour CB0[112..114] never mixes in; the program is the only decoded place that names it). The program blends over the atmosphere's clear sky, whose scattering tables are undecoded: with no capture-derived clear sky (Aridia) the dome is drawn over black, which loses the navy of the game's sky; with one (Nebula, Phoenix) it blends by `alpha` over it, which was only looked at in Nebula.
- **Shader port.** `SKY_FRAGMENT` in `rc2016/viewer/shading.py` follows the shader formula, with UV0 in DirectX convention. Engine inputs take presentation values: time 0 (no scrolling), alpha multiplier 1, alpha-test threshold 0. The stars term (dword 22; non-zero on Gaspar, Metropolis and Rilgar) is not ported. The output blends over the clear sky with the material's alpha-over state.
- **Dome fog (inferred).** The shader ends in `mix(fog colour, lit, T)` with `T` a vertex output; the dome's vertex shader is not in the cache, so `T` is undecoded. The viewer takes the level's [height fog](#height-fog-fogpy) for a point `DOME_FOG_DISTANCE` (10,000 units, the dome at infinity; the result no longer changes beyond about 3,000) along the view ray from the camera, mixed towards the same fog colour as the geometry (`fog.fog_colour`: `FOG_COLOUR_SCALE`, 2.5, times the horizon colour; [Height fog](#height-fog-fogpy)). It applies only where the scene fog does (Level atmosphere lighting, Haze on). Evidence, from the Novalis reflection capture (the game's own sky, clouds included, in HDR), measured on bright clouds away from the sun: they are tinted blue near the horizon and nearly neutral above about 30 degrees; two independent estimates of the fog weight from the G-R and B-R differences agree within 10%; and the weight (0.22, 0.08, 0.04, 0.03 at 13, 20, 29, 41 degrees) has the magnitude and the 1/sin(elevation) shape of the decoded fog formula for a camera 100 to 150 units up. The factor is a visual fit, now shared with the geometry: a fog colour equal to the dim horizon colour would grey the clouds, and the capture needs a brighter one (a grid fit over the capture and the in-game frame gave 2 to 3, under the former Neutral display; the factor was not refitted for the game display, which gives 234, 244, 247 for the brightest decile of the upper image against the game's 224, 251, 254). The capture's cloud distribution at 24 to 50 degrees (away from the sun) matches the port's with threshold 0 and multiplier 1 (R quantiles 0.46 / 0.67 / 1.0 / 1.57 against 0.49 / 0.70 / 0.99 / 1.56), so coverage and brightness need no extra term.
- **Sky display.** With the **Game (captured)** tonemap (the Level atmosphere default) the sky goes through the same decoded display chain as the level ([Display transform](#display-transform-displaypy)): the captured clear sky (probe units) comes out at 142, 190, 210 (blue-dominant pixels of the top fifth) against the game's 147, 198, 218, with no sky-specific fit. The paragraph below describes the previous, now fallback, calibration used with the other tonemaps. `skyDisplay` is Khronos PBR Neutral at `SKY_EXPOSURE` 0.9 times the lighting exposure. Against one in-game Novalis frame the clear-sky value of the capture (0.395, 0.688, 0.866 at 12 to 18 degrees) is 0.83, 0.83 and 0.81 of the displayed linear value in the three channels, so the game's mid-tone curve is close to linear; the ACES fit brightens and desaturates those mid-tones (192, 219, 227 against the game's 155, 199, 218), and Neutral at 0.9 gives 152, 200, 223. That calibration was a stand-in for the display chain that is now decoded (it also explains why the ACES fit looked too bright: the game's curve is much flatter in the mid-tones).
- **Clear sky.** The clear sky behind the clouds comes from the atmosphere's undecoded scattering tables. When the atmosphere zone references exactly one `volumereflections` capture (Batalia, Kalebo, Novalis), `clear_sky_profile` decodes its six 512x512 BC6H faces (in-game reflection captures, with level geometry and clouds baked in) and keeps the clear-sky pixels. The capture is decoded in HDR (`probe_lighting.probe_faces`; the former 8-bit clamped decode changed no bin by more than 0.007). A clear-sky pixel has blue above 1.25x red and above green, and no channel above `CLEAR_SKY_MAX` (0.98): bright blue-dominant pixels near the horizon are haze-tinted cloud and glow, and above 24 degrees the cap removes under 1% of them. The result is the per-channel median in 6-degree elevation bins. A bin needs 10% clear-sky pixels; bins below that (terrain near the horizon, cloud banks) take the nearest valid bin. This is sampled game data, not the scattering model; it needs Pillow and NumPy. Levels without a capture keep the flat background behind the clouds.
- **Drawing.** The sky is drawn first, around the camera (camera rotation only, at a radius between the near and far planes), with no depth test or depth write. It shows in Shaded and Textured modes.

For Novalis, the profile's zenith is (0.145, 0.255, 0.404), and it brightens towards the horizon. The capture's upward face shows the same sun-side warm tint on the clouds that the port produces. The profile's lowest bins (below 18 degrees) are the least certain: terrain and fogged cloud dominate the capture there, and the real horizon glow is brighter than the median (blue-dominant pixels at 6 to 12 degrees have a median of 0.75, 1.26, 1.59).

Region check against the in-game Novalis frame at 10 to 15 degrees elevation (sRGB medians): cloud tops 224, 240, 248 (game 227, 242, 248), shaded cloud 181, 207, 220 (game 177, 204, 216), blue-dominant sky 177, 214, 232 (game 155, 199, 218; the viewer's pixels include cloud-edge blends). Before these changes the cloud tops were 219, 227, 231 and shaded cloud was grey (199, 199, 199). Cloud tops cover 20% of the sky pixels in that band against 13% in the game; the clouds scroll with time, which the port holds at 0, so the frames are different moments.

## Level viewer

Two front ends share one implementation:

- Standalone window: `python -m rc2016.levels.viewer` (`rc2016/levels/viewer.py`).
- The **Level** asset type in the asset browser (`rc2016/app/browser.py`). The level list sits in the browser's left pane; clicking a level loads it, and a typed `.level` or `.zone` archive path loads on Enter. The source root is the browser's.

### Modules

| Module | Role |
| --- | --- |
| `panel.py` | `LevelPanel` (Tk): level list, typed path entry, view mode, zone toggles, status line; `load_level` background loader |
| `viewport.py` | `LevelViewport` (a `ModelViewport` subclass): instanced renderer, fly camera, PNG capture |
| `scene.py` | Data only, no GL: archive access, `zone_scene`, `model_geometry`, `core_bounds`, material lookup |
| `actor_format.py`, `actors.py` | Actor/component decoding, structural primary binding, source-placement policy and exclusions |
| `sky.py` | Data only, no GL: the level's atmosphere, cloud domes and clear-sky profile ([sky](#sky-in-the-level-viewer-skypy)) |
| `scene_lighting.py` | Data only, no GL: the authored light records and lightgrid references ([Authored lights](#authored-lights-scene_lightingpy)) |
| `probe_lighting.py` | Data only, no GL: the **Level atmosphere** lighting maps from the reflection probe and atmosphere sun ([below](#level-atmosphere-lighting-probe_lightingpy)) |
| `volumes.py` | Data only, no GL: the reflection-capture box volumes of a lighting zone and the choice among them ([below](#reflection-volumes-volumespy)) |
| `lightgrid.py` | Data only, no GL: the baked lightgrid tiles, their decode, GPU atlas packing and a CPU reference sampler ([Lightgrid ambient](#lightgrid-ambient-lightgridpy)) |
| `level_format.py`, `zone_format.py` | The decoders described above |

### Loading

`load_level` runs on a worker thread and posts events that the Tk thread applies; Tk and GL are touched only from the Tk thread. Order:

1. The zone list (`.level` -> zone paths; a single `.zone` path is treated as a one-zone level). `--zones` filters by short name or path.
2. Each zone's instance table (`zone_scene`, through `decode_zone_arrays`: the same invariants and error messages as `decode_zone`, read with NumPy and without per-instance dicts or the tail hex). Instances are grouped per model, per handedness (mirrored or not) and per raw material-override entries, as flat arrays of 16 floats per matrix. Zones without a model table are listed but disabled ("no models"); a zone that fails to decode is disabled as "unreadable" and the error is logged to stderr.
3. Scene bounds from the translations of visible instances, then, for a `.level`, its sky (`load_sky`: `level_sky` over the unfiltered zone list, with the dome and noise textures loaded; a failure is logged and the level loads without a sky).
4. Each unique model's geometry, most-placed first (`model_geometry`: Highest LOD, look 0, decoded with numpy; V is flipped back because the decoder flips it for Blender). Its groups are then resolved against the model's material slots (`effective_groups`): instances whose overrides give the same material changes share a group, and instances with no effective change join the plain group. The view fills in as models arrive.
5. Materials and textures by material path, including the replacement materials of applied overrides, using the model viewer's Shaded rules (`rc2016.viewer.textures.material_textures`). Each material path loads once; each material graph is parsed once and cached.
6. A `done` event with timings and the override outcome counts (`material_overrides`).

Geometry and materials run on a small thread pool (`LOAD_WORKERS`: CPU count minus one, 2 to 6). Models are still posted most-placed first; at most one decode per worker is queued ahead, and each model's materials are queued right after its geometry, so the level becomes textured while later models decode. Actor-only models follow the same pattern. The worker threads share the archive (per-thread file handles) and the texture cache passed to `material_textures`, which must be safe for concurrent use.

Decoded geometry is cached on disk under `<repo>/.cache/level_geometry` as uncompressed `.npz` files, keyed by the model's archive bytes (SHA-256), the LOD/look selection and a fingerprint of the decoder sources (`rc2016/formats/*.py`, `common.py`, `scene.py`), so any decoder change invalidates it. Files are written atomically; an unreadable file is decoded again, a read-only folder only loses the speed-up, and a frozen build without sources does not cache.

A new load cancels the previous one (generation counter; stale events are dropped). The panel opens its own `Archive` (not shared with other tabs) with `verify_repeats = False`, which reads only the first copy of repeated assets; the pipeline keeps that check on. The source root may be any folder that contains exactly one archive root (`locate`); it needs the level assets (the `Image0` tree). Both `CUSA01073` and `Image0` list the same 17 levels.

### Rendering

- Geometry and instance matrices stay native Y-up. The presentation basis is applied once, in the camera matrix (`BASIS` in `viewport.py`), so the fly camera works in Z-up.
- Each model keeps its instances per zone on the CPU and has one instance buffer per handedness and set of effective material changes, holding the instances of every visible zone. Toggling a zone, or the actor or spawn layer, rebuilds only the buffers of models that have rows in it. The draw list keeps each model's batches until that model's buffers change, so models arriving during a load do not re-sort the others' batches.
- Frustum culling: each instance gets a bounding sphere, the smaller of the model's origin sphere (`radius`) and its box sphere (`bounds`), transformed by the full instance matrix with the radius scaled by the spectral norm of its 3x3 part (safe for sheared and mirrored matrices). When the camera changes, all spheres are tested against the six frustum planes in one NumPy pass; each instance buffer whose visible set changed is refilled with its visible rows (`glBufferSubData`) and drawn with that count. Picking, wireframe and the selection outline use the same visible set. Without `glBufferSubData` nothing is culled. Frustum culling removes only instances entirely outside the view. Small-object culling (**Display → Hide below**, default 1 px, `LevelViewport.min_screen_pixels`) also removes instances whose bounding sphere spans fewer pixels on screen than the threshold; depth is measured along the view axis, which errs toward keeping objects. Above 1 px it visibly drops distant detail. A group with no visible instances is not drawn at all. Shadow maps still draw every instance.
- Each (model, instance buffer) has a vertex array object (OpenGL 3.0) holding the position, normal and UV arrays, the index buffer and the four matrix-row attributes (10-13, `INSTANCED_VERTEX` in `rc2016/viewer/shading.py`) with divisor 1, so a draw binds one object. Instancing needs OpenGL 3.3; otherwise the view reports which entry point is missing.
- Material overrides keep the model's one geometry upload. `draw_items` picks each batch's material per instance buffer (override or slot default); buffers that agree on it share a draw, so an override changes only the instances that carry it. Blend, cull and forward (glass) handling follow the effective material. **Material overrides** (on by default) switches them off for comparison.
- Batches of one model with the same material and instance buffers that follow each other in the index buffer merge into one draw. Material uniforms and texture bindings are recorded once per material (`_Recorder` around `ModelViewport._apply_material`), and only the ones that differ from the current state are issued. `glFrontFace` is set only when handedness changes. Draws stay sorted by native program then material; recorded uniform locations and values are specific to the program.
- Normals use the cofactor matrix times sign(det), so sheared and mirrored instances shade correctly. Mirrored groups draw with `glFrontFace(GL_CW)` so `gl_FrontFacing` keeps meaning "outside".
- Forward-shaded (glass) materials draw after all opaque ones with material blend and cull state and no depth write. Instances are not depth-sorted.
- Shaded mode enables **AO**, **Shadows** and **Authored lights** by default under the **Lighting** group. Each can be selected independently; the Lighting master gates all three alongside rig/HDRI direct, ambient and specular lighting, preserving their selections when off. `--no-lighting` provides unlit inspection without scene-effect buffers. AO uses a port of captured compute shader 5881 on the current scene depth, with a viewer half-depth adapter and bilinear upsample; the other native AO stages and camera ABI are unported. AO is screen space and is rendered once the camera has rested for 150 ms, then reused until the camera or scene changes; frames drawn while the camera moves have no AO. After a load, deferred mip chains build in 8 ms batches while the camera rests for 0.5 s. The status line splits frame time into the scene passes and the main draw (CPU time issuing each). Shadows use the selected viewer key, visible opaque geometry with its material cutouts, a viewer-allocated 2048-square D16 map, and fragment 540's nine weighted linear Less comparisons. The map re-renders at once for a new key direction; scene content changes (models, instances, materials, zone visibility) refresh it at most every 2 seconds, so a streaming load does not redraw the whole level into it every frame. Authored lighting decodes on its own thread beside the geometry, so points appear once it finishes. Supported authored points retain source position, RGB, inverse radius and raw identity fields; only point-shape records (byte +0x50 equal to 0) with normalization byte 0 are drawn, and of those only flag byte 1 has the Veldin-joined category-0 preview path ([Authored lights](#authored-lights-scene_lightingpy) for the rest). The nearest 32 points use the captured fourth-power radius window and squared diffuse cosine. Forward glass is excluded from scene buffers. Probe references are decoded but native slots and coefficients are unbound; native caster allocation, per-light visibility/dimming and complete game lighting remain unverified. Missing GL capabilities report a scene-preview error and retain the base viewer lighting.
- Lighting and tonemap use the model viewer's selectors ([Lighting and tonemap](viewer.md#lighting-and-tonemap)); the level viewer adds a third tonemap, **Game (captured)**, that the Level atmosphere lighting selects by itself ([Display transform](#display-transform-displaypy)). With any other lighting that choice falls back to the ACES fit. The Viewer rig is fixed to the world (rig right = +X, up = +Z, towards the viewer = -Y in Blender's Z-up world), so lighting does not shift while orbiting or flying. Shared HDRI lights the level from the shared review HDRI, with level directions taken through the presentation basis. This is not game lighting.
- The sky ([sky](#sky-in-the-level-viewer-skypy)) is drawn before the level; without one the background is a flat colour. No decals; fog exists only as the Level atmosphere height fog, partly inferred ([below](#height-fog-fogpy)); the supported authored-point preview is described above.
- Per frame, lighting uniforms are computed once (one modelview read-back) and reused for every program; each material's surface program is resolved once.
- Arriving materials upload their textures for at most `MATERIAL_BUDGET_SECONDS` (8 ms) per Tk tick, then link their native and experimental surface programs in the time left; the rest continues on later ticks. A draw never links: a material whose program is not linked yet (after enabling material previews or scene effects, for example) is drawn with its native surface, or the plain shader, until the pump has linked it. `capture_png` uploads everything first. Camera drags, keys and toggles schedule one redraw per idle pass instead of drawing per event, and the status line refreshes at most four times a second.

### Authored lights (`scene_lighting.py`)

Light records sit in the `lighting.zone` of each region: index `0x97FF6EB5` (u32 offsets into the `0x06ABCAB2` heap, as for instances). Novalis `default_region/lighting.zone` holds 936. Records are 152 bytes; the 44 with a gobo texture carry an extension (209 or 212 bytes). `parse_light_record` decodes one record without assigning semantics beyond the table below.

| Offset | Type | Field |
| --- | --- | --- |
| +0x00 | f32[16] | Row-vector 4x4 matrix: rows 0 to 2 are the local axes (row 2 is the aim axis), their lengths the extents, row 3 the position |
| +0x30 | f32[3] | Position (the matrix translation, repeated) |
| +0x40 | u32 | 1 in all records |
| +0x44 | u32 | Record size |
| +0x48 | u64 | Light id (unique) |
| +0x50 | u8 | Shape: 0 point, 1 spot, 2 gobo projector, 3 to 5 box |
| +0x51 | u8 | Flag byte, a bit set (values 0, 1, 3, 9, 11, 17, 18, 19); bits 2, 8 and 16 unresolved |
| +0x52 | u8 | Normalization mode, 0, 1 or 2; only 0 is understood |
| +0x54 | u8 | 0 or 1 (32 point lights); meaning unknown |
| +0x56 | u16 | 152 exactly when the extension (a NUL-terminated gobo texture path) is present |
| +0x58 | f32[3] | RGB, linear, unbounded (0 to 358) |
| +0x64 | f32 | Read by the game's light constructor for every shape but the large box (shape 5), stored in the light object, and replaced by a table default when not positive: a distance, not an angle (0 in most spots; 44, 55, 66, 146, 186, 200, 350 in the rest). Not applied |
| +0x7C | f32 | Stored by the constructor for shapes 0 to 3 and tested against 0 (a flag bit is set when positive); 0 in 43 % of the spots, otherwise 16 to 46 in most. Not applied |
| +0x68 | f32 | Radius (0 for box shape 3) |
| +0x74 | f32 | Scale, 1 in 909 records (0.03 to 0.6 in the rest, mostly flag byte 9); meaning unknown, not applied |
| +0x78 | f32 | 40 in 934 records (1 in the two shape-4 and shape-5 boxes); stored by the constructor for shapes 0 to 3, a flag bit is set when positive. Not applied |
| +0x88 | f32 | 2.5 in 2742 of 2758 spots. Spots: the cone exponent, clamped to 0.1 to 10 (decoded, see Spots) |
| +0x80, +0x84 | f32 | Spots: the inner and outer cone half angle in degrees (decoded, see Spots). Other shapes: a pair with the second always larger (points 15/45, 11/35 or 38/63); the point constructor does not read it |
| +0x90 | u64 | Reference to another object in 29 records (10 distinct values); not resolved |

Evidence for the shape byte:

- **Orientation.** Of the 707 shape-0 records, 98% have the aim axis straight down or along one shared default tilt (663 records of the corpus share one of two identical rotations), the signature of an unaimed omni light. Of the 199 shape-1 records, 184 have a rotation shared by fewer than ten records and the axis points in all directions (48% straight down, 10% near horizontal). All 24 shape-2 records carry a gobo path (`rk_gobo_rounded_rect` or `rk_gobo_soft_rect`) and point straight down. Shapes 3 to 5 (6 records) are the only records with a scaled matrix (extents 4.4 x 3.5 x 19.5, 12 x 6 x 6.4 and a 105 x 110 x 4 slab), with radius 0 or 1. Shape 0 is the Veldin capture's category 0.
- **Positions.** The light positions share the instance frame of the level: the median distance from a Novalis light to the nearest placed instance (art_buildings, art_terrain, gp_base, art_sewers) is 2.0 units, against 16.5 for random points in the lights' bounding box; shapes 0, 1 and 2 are at 1.9, 2.2 and 2.2.
- **Colour scale.** For normalization 0, `luminance / radius^2` (the quantity the preview shader multiplies by the fourth-power window) has a median of 0.025 for points, 0.10 for spots and 0.25 for projectors, the same order as the four validated flag-byte-1 lights, so the colours of the three shapes share one unit.
- **Co-located lights.** Many positions hold two or three records (134 pairs within 0.5 units), for example a spot (flag byte 11) with a much dimmer point (flag byte 9) at the same place, the colours in a ratio of 60 in three pairs and the spot's radius larger. Lights appear to be authored in sets.

The flag byte is a bit set, not an enumeration: its values combine bits 1, 2, 8 and 16 (only value 18 lacks bit 1, and only the value 0 has none). Novalis counts: 17 (327 records), 19 (270), 11 (154), 9 (152), 3 (20), 1 (8), 0 (3), 18 (2). Bit 2 occurs on spots and projectors (3, 11) and on some points (19); bit 8 mostly on points (9) and spots (11); bit 16 on points (17, 19) and almost nowhere else. The bits do not change the colour scale of a record (normalization-0 point lights of flag bytes 1, 9, 17 and 19 all have a median luminance of 0.1 to 0.4). Whether any bit marks a light that the game bakes into the lightgrid rather than evaluating at run time was not found: no decoded field separates them.

**What is drawn.** Point lights (shape 0) with normalization 0 (314 of 936 in Novalis: 4 with flag byte 1, 175 with 17, 108 with 9 and 27 with 19; every one has an explicit positive radius) go through the captured category-0 preview path ([Rendering](#rendering)). The 4 with flag byte 1 are Veldin-joined (`validated`) and draw under every lighting source. The other 310 are an inference, that the flag byte does not change the shading path of a point. They draw only under the **Level atmosphere** lighting once its maps are built, so the Viewer rig and Shared HDRI looks keep the 4 validated lights. `LevelViewport._active_authored_lights` chooses the list. The nearest-32 limit is unchanged.

**Spots (cone decoded, curve inferred).** Spots (shape 1, normalization 0) go through the same distance falloff as points, times a cone factor, and only under the **Level atmosphere** lighting (`scene_lighting._decode_zone_spots`, `sceneSpot` in `rc2016/levels/scene_effects.py`). The game's spot constructor (eboot function `0x1852aa0`, Research/2026-10-23_spot-light-constructor) reads the pair at +0x80 and +0x84 as the inner and outer **half** angle in degrees: inner clamped to 0 to 88, outer raised to at least inner + 1 and capped at 89 (`scene_lighting.spot_half_angles`). Function `0x1854040` turns them into the cone terms `f0 = 1 / (cos inner - cos outer)` and `f4 = -cos outer * f0`, so the cone input is `saturate(dot(axis, direction to the pixel) * f0 + f4)`, with the aim axis as matrix row 2. The float at +0x88 (2.5 in nearly every spot, clamped to 0.1 to 10) is stored beside them as the cone exponent. The pixel shader that consumes these terms is not in the archive, so the viewer shapes the input with `smoothstep` and does not apply the exponent (both inferred), and the colour scale is the point lights'. Aridia has 197 spots among its 591 records; the nearest-32 limit applies to points and spots together, by distance only.

**Lamp beams (inferred).** The lighting zone lists `gbl_amb_Light_Cone_Circular_Med` and `_Small` visual effects (a soft cone mesh, 3.75 radius by 10 long, with the `gbl_glow_simp_soft` material, driven by a particle system whose format and placement are undecoded). `rc2016/levels/beams.py` stands in for them: a spot with an instance of a lamp model within 3.5 units (`LAMP_MODEL`: work, pole, shop, flood and claw (`construction_set_light`) lights, which match 153 of Aridia's 197 spots; the claw lamp's spot lies 3.1 units from its instance origin, and the median nearest-instance distance over all spots is 2.1) gets one additive cone along its aim if it aims level or downward (an upward spot is an uplight), as long as its range (at most 14 units) and as wide as half the mean of its inner and outer half angles (17 degrees for the claw lamp's 22 and 46, about that lamp's beam width in one in-game frame; the halving is a fit, not decoded), with a facing term `pow(|N.V|, 1.6)`, a squared fade along its length and a soft apex (`BEAM_FRAGMENT`; the 0.3 gain is a visual choice, added in display units after the tonemap). It draws after the level, depth-tested, under the Level atmosphere lighting with Authored lights on. Whether the game's beams come from the light, a placed visual effect or the lamp model is unknown, and so is which lamps have one.

**What is not drawn.**

- Projectors (24) and boxes (6), and spots with non-finite fields: the aim axis, extents and gobo are decoded, but the gobo projection and the box falloff are unverified and no capture covers them. The status notes count them (`undrawn_light_shapes`).
- Normalization modes 1 and 2 (393 records, mostly dim points with colours of 0.001 to 0.05): `luminance * radius^2` has no tight value in any mode (about 2.5 decades of spread in each), so the mode is not a simple rescale and the colour units are unknown.
- The scale at +0x74, the +0x80 and +0x84 pair and the flag bits are not applied.
- Possible double counting: if some of the 310 extended lights are baked into the lightgrid in the game, the preview adds them on top of the unbound probe lighting.

Cost: the selection of the 32 nearest lights is one vectorised distance pass per frame; with 314 lights instead of 4 it takes 0.7 ms against 0.16 ms of CPU per new modelview, and the shader loop stays bounded by the same 32 uniforms.

### Level atmosphere lighting (`probe_lighting.py`)

The opt-in **Level atmosphere** lighting source replaces the rig or shared HDRI with the level's own data: the atmosphere zone's reflection probe as ambient and specular, and the atmosphere sun as the key light (and the shadow direction). It feeds the same lighting maps as **Shared HDRI**: 9 irradiance SH coefficients, GGX-prefiltered specular bands, a sun direction and an irradiance. The maps are built on a worker thread when the source is first selected and cached under `.cache/environment/level_*.npz`. Levels without an atmosphere zone and a single probe report that and keep the rig.

- **Probe.** A 512x512 BC6H cube (six faces, PS4-tiled). `rc2016/formats/bc6h.py` decodes it to linear float; Pillow clamps BC6H to 1, so it is used only for blocks in a mode the decoder lacks (the other two-subset modes; none in the Novalis probe). Modes 11 to 14 and the two-subset mode with field `01` (about 11 % of the Novalis probe) match Pillow's decode within 8-bit rounding wherever a block stays below 1. The cube is resampled to an equirectangular world in Blender's Z-up space through the Direct3D face formulas of `rc2016/pipeline/lighting_blender.py` (face orientation: identity), and rejected if its seam ratio exceeds 2.0 (Novalis 1.28).
- **Check.** The probe's bright glare lies 7 degrees from the atmosphere's sun direction in Novalis (dot 0.993), which confirms the cube's handedness and face orientation for this probe.
- **Sun.** Direction is the negated atmosphere ray, colour is RGB96 used as irradiance at normal incidence (as in the research pipeline). Probe texels above luminance 2 (about the 99th percentile in Novalis) are cut from the ambient maps so the sun is not counted twice.
- **Visual calibration (not decoded data).** The SH irradiance and the sun colour are multiplied by `DIFFUSE_LIGHT_SCALE` = 5.0 (the specular bands stay in probe units). It was pi (1.65 stops, what a diffuse without the 1/pi factor gives) under the former ACES display and was refitted when the captured display chain replaced it: against one in-game Novalis screenshot, sunlit grass, tree canopy and sunlit rock land at -0.35, +0.07 and +0.12 stops of the game (mean -0.05; before, with pi and ACES: -0.52, -0.06, -0.02). The remaining factor 1.6 over pi is unexplained; a runtime sun and sky multiplier is plausible (the Veldin capture's key light is 5.3, 4.5, 3.0 against its atmosphere's RGB96 of 0.75, 0.86, 0.50, a ratio of 5 to 7). A global exposure cannot replace it because the sky dome, in the probe's units, already matches through the display chain. The scale is applied after the cache, so cached maps are unchanged.
- **Shadow depth (visual calibration, not decoded data).** The key shadow map is a viewer allocation, so two viewer constants shape it. `scene_effects.SHADOW_AMBIENT` (0.05) scales the sky diffuse term where the map blocks a key light that would reach the surface (the factor is 1 when lit and when facing away from the key light, so sunlit pixels and unlit faces are unchanged, and it applies only to the Level atmosphere source). With the atmosphere sun at about 12 % of horizontal-ground light, grass in cast shadow kept 90 % of its lit luminance; in one in-game Novalis screenshot it is about 1/10 (display luminance 0.009 to 0.013 against 0.09 to 0.11 sunlit). At the calibrated camera the viewer now measures grass 0.113 lit, 0.0135 shadowed (ratio 8.4 against about 10; 6.6 under the former ACES display), a floor of specular, self-lit and fog light that the factor does not touch. Stone stays shallow (about 2.0 against roughly 3 to 8 in the game, a rough measure) for the same reason. `viewport.SHADOW_SLOPE_BIAS` (2.0) is the slope factor of the shadow pass's polygon offset; with 0 the 2048-square map over the whole level (texels of about a unit) striped sloped ground with false shadows, which the calibration above would have deepened. Shadow direction: the atmosphere ray is exactly the sun direction the game's constant buffer holds in the Veldin capture (view constants, dwords 128 to 130), and the Novalis probe glare agrees with it, so its sign and handedness stand. At the calibrated camera it puts the sun behind and 28 degrees right of the view axis, so cast shadows fall forward and to the left of trees; in the in-game shot they fall nearly straight left (about 10 degrees forward), so the game's shadow azimuth differs from the atmosphere sun by roughly 50 degrees for this view. The captured shadow-mask pass (pixel shader `ca0246a6`: `mask = 1 + s * n.y * fade * (avg - 1)` with `n` the decoded G-buffer normal, `fade` a depth fade and `s` = 0.66 in that frame, over 32 rotated-disc taps) is a separate small contact/character shadow on a 96-square near-vertical map, not the sun's; its 34 % floor belongs to that pass alone. The sun shadow is a 6-cascade orthographic atlas whose depth axis equals the atmosphere ray (no separate shadow-direction header field exists), filtered by 9 weighted bilinear compare taps with no floor, so the direction is left as decoded and the 50 degree difference stays unexplained (candidates: camera calibration, shadows baked into the lightgrid, a runtime sun override).
- **Levels without a probe (Aridia).** When the sky names no reflection capture but the level has a lightgrid, `grid_environment` replaces the probe maps with the level-average grid light: the per-cell light `lerp(B, A, 0.5 + 0.5 d.n) + S (0.5 + 0.5 n.y) C` is linear in `n`, so its mean over the valid cells (validity byte 128 or more) is exact as `k0 + k.n` (`grid_mean_irradiance`). The SH holds that function in viewer ambient units (times `AMBIENT_SCALE`, as the grid path), the specular bands hold a sky of that irradiance, `E / (pi * DIFFUSE_LIGHT_SCALE)` in probe units, and the sun is the atmosphere RGB96 times `DIFFUSE_LIGHT_SCALE` as before. The grid still overrides the SH wherever it has valid cells, so the SH is the fallback elsewhere. Cached under `.cache/environment/grid_*.npz` by the grid bytes. Aridia's authored values are 10 to 20 times below Novalis' (RGB96 0.034, 0.021, 0.056, equal to the grid's mean sky term; grid A mean 0.03 against 0.25), and the level is only played at night.

  **Aridia calibration (one in-game frame).** Camera `112.5, -189.2, 28.55`, yaw 232.75, pitch 9.25 (presentation space), compared region by region with a viewer capture of the same view (`LEVEL_EXPOSURE_MULTIPLIER`, `LEVEL_SHADOW_AMBIENT`). The flat sky dome is decoded exactly, and its nebula matches the game (display 0.59, 0.39, 0.64 against 0.59, 0.40, 0.62) only at exposure 8.2, 1.8 times the Veldin capture's, which is therefore used for Aridia (`probe_lighting.level_exposure_stops`; the game's value is a runtime one). With it the distant hill and mountain regions land within about 10 to 20 % of the game and the nebula within 5 %. The Novalis shadow-ambient factor (0.05) is 1 for Aridia: the game's ground in the open is evenly lit. Still wrong: the game's ground and rocks carry a warm neutral light the viewer lacks (ground display 0.25, 0.24, 0.19 against 0.11, 0.08, 0.16, rock midtones 0.16, 0.12, 0.17 against 0.21, 0.07, 0.23, so the viewer's light is too violet and too weak in green); the sky has no navy base (clear-sky tables); the display exposure and the factor 1 are fits to one frame. Inferred, not decoded: that the level-average grid light stands in for the missing capture, and the two fitted values. Not covered: the sky (Aridia's dome uses the unported `rk_sky_dome_flat` graph, so the background stays flat) and the height fog (its colour needs a clear-sky profile, which only a capture gives), so Haze does nothing there.
- **Not decoded.** How RGB96 scales against the probe (Novalis: sun 0.7 against about 1.5 to 2.2 of sky irradiance, so shadows are shallow: the sun is about 12 % of the lighting on horizontal ground, whereas in the game screenshot grass and stone in cast shadow are about 1/12 of their sunlit value; part of that is the game's tonemap and ambient occlusion, but a runtime sun multiplier of several times is plausible and is not applied), the exposure of Novalis' own frame (the Veldin capture's 4.55 is applied by the display chain and reproduces the sky, but it is a runtime value; the lighting exposure stays 0 stops), the lightgrid's sky-term scale and probe selection per region (the grid itself is read, [below](#lightgrid-ambient-lightgridpy)), and occlusion of the sky term beyond screen-space AO. The research pipeline's `novalis` lighting source uses a different probe (`0xad76696216314d22`, about 25 times dimmer), so the two are not interchangeable. This is a review preview, not game lighting.

#### Reflection volumes (`volumes.py`)

A lighting zone (`lighting.zone`; Veldin `lgt.zone`, `lgt_night.zone`) holds box-volume records in section `0xBDAB2B0D` (u32 offsets into the `0x06ABCAB2` heap). A record whose flag word at `+0x48` has bit `0x20` set is a reflection capture (Veldin `lgt.zone`: flag `0x10020`; the other volumes there, such as fog volumes, are not read):

| Offset | Type | Field |
| --- | --- | --- |
| +0x00 | f32[16] | Row-vector 4x4 matrix: rows 0 to 2 are the box half-axes (their lengths are the half extents), row 3 the centre; a point is inside when its local coordinates `(p - centre) A^-1` are within `+-1` |
| +0x44 | u32 | Record size (644 bytes for a reflection record) |
| +0x48 | u32 | Flag word |
| +0x50 | u64 | Volume id, equal to the `<id>` in the path |
| +0x70 | string | Inline NUL-terminated texture path `\textures\lgt\volumereflections\<id>\reflection.texture` (`lgt_night` for the night zone) |

Veldin day (`lgt.zone`): volume `0x83c18ca9948b0057` (centre 264, 35.5, 104.8; half extents 37, 37, 41) and `0xe5711cc80e553fac` (centre 63, 32.6, 142.1; half extents 21.5, 29.2, 27.6, an interior hangar probe whose cube shows no blue sky); `lgt_night.zone` holds `0xc7448ba22e0d872c` at the same place as the second. The Veldin RenderDoc capture's camera (native 69.204, 32.872, 148.535) lies inside the second only (largest local coordinate 0.29); the other box is far away. That the box spans local `+-1` (rather than `+-0.5`) is inferred; the camera is inside either way.

`sky.lighting_reflection` collects the volumes of the level's lighting zones that match the chosen atmosphere's night-ness and whose texture resolves to one archive asset, and `volumes.choose_reflection` picks one: the volume containing `probe_position` (a native Y-up point; the smallest box if several), otherwise the only volume, otherwise the one whose box encloses the most lightgrid tile centres of the lighting zone, the earlier one on a tie; with no tile inside any box there is no default and the level has no probe. The default is a viewer choice, not decoded: the game selects probes per lightgrid cell (cell bytes 20 to 23), the viewer has one probe. Veldin's default is `0x83c1...` (84 tile centres inside against 24 for `0xe571...`); `--probe-position` (or `--camera`) selects the hangar probe.

#### Lightgrid ambient (`lightgrid.py`)

With the **Level atmosphere** lighting the diffuse ambient comes from the level's baked lightgrid where it has valid cells, and from the single-probe SH ambient elsewhere. **Light grid** under Lighting (`LevelViewport.set_lightgrid`, `--no-lightgrid`) switches back to the probe ambient for comparison. Other lighting sources are unchanged, and so are glass and the specular terms. One lighting zone is read (`lightgrid.day_lighting_zone`): Novalis' (727 `.lightgrid` assets, listed by `lighting.zone` sections `0xD6E15BC0` coordinates and `0x3E36401D` paths) when the level lists it, through the decoder it was validated with; for any other level the single zone whose stem is `lighting` or `lgt` and that is a day zone (`lgt_night` is read only when the chosen atmosphere is night), through `lightgrid.zone_grid_map`, which requires every path to be a `grid_<x>_<y>_<z>.lightgrid` of one region folder that agrees with its stored coordinate and resolves to one asset. A level without such a zone, or a lighting zone without the two tables, has no lightgrid. Veldin's `lgt.zone` lists 1,127 day tiles (453 night); the layout is the Novalis one (the Veldin capture's runtime tile buffer matched it), but only Novalis and Veldin were checked against a capture: Batalia, Kalebo, Nebula, Pokitaru, Quartu, Phoenix and Museum now also read a grid on the same assumption. The grid is sampled by the scene-effect programs (AO, Shadows or Authored lights on); with all three off the probe ambient applies.

**Decoded** (the lit shader translated from GCN code, checked against the shadPS4 RenderDoc capture of one Veldin frame, whose runtime tile buffer equals the archive tiles in bytes 0 to 19):

| Item | Layout |
| --- | --- |
| Tile | 16 x 16 x 16 cells of 24 bytes (section `0x9989BB49`, 98,304 bytes); the path coordinate `grid_<x>_<y>_<z>` is the tile centre c and the tile covers [c - 8, c + 8) in native Y-up world units, one cell per unit |
| Tile table | 64 x 64 x 64 u16 slots indexed `tx + 64 ty + 4096 tz`, `t = (floor(p - 0.5) >> 4) & 63` (it wraps; the Novalis tiles span at most 22 tiles per axis, so none collide) |
| Cell | index `x + 16 y + 256 z`; bytes 0 to 2 unorm8 direction `d = 2q - 1`; 4 to 7, 8 to 11, 12 to 15 R11G11B10_FLOAT colours A (facing +d), B (facing -d) and C (sky term); 16 to 19 unorm8 `(qx, qy, qz, w)`; 20 to 23 probe selector (unused here) |
| Light | `E(n) = lerp(B, A, 0.5 + 0.5 d.n) + S (0.5 + 0.5 n.y) C` per cell, in the native frame (`n.y` vertical) |
| Sampling | the 8 cells around `floor(p - 0.5)`; cell centres are integers plus 0.5; weight `max(2^-18, trilinear * v)`, `v = clamp(8 dot(q - 0.5, o) + 16 w - 7, 0, 1)`, `o = p - cell centre`, renormalised. The shader spells `q - 0.5` as `-4 (ox + oy + oz)`, a signed sum, not the L1 norm |

Novalis data: 727 tiles, 2,977,792 cells; 51 % have `w` = 255 (open) and 49 % `w` = 0 in the validity byte (almost no cell lies between); B is black in 54 % of cells.

**Viewer path.** `level_lightgrid` decodes the tiles on the worker thread that builds the Level atmosphere maps and caches them under `.cache/lightgrid` (uncompressed `.npz`, keyed by the assets' archive locations and `LIGHTGRID_VERSION`): cold 1.9 s, warm 0.15 s, outside the geometry load. `LightGrid.atlas` packs the tiles into a 128 x 128 x 192 cell atlas (`ATLAS_XY` tiles per axis in x and y): a 3D R11G11B10F texture with A, B, C interleaved along x (the file's words, uploaded as is), a 3D RGBA8 texture with the direction and the plane/weight bytes interleaved, and the 64-cubed table as RGBA16F holding each tile's atlas position. That is 63 MB of GPU memory on units 19 to 21, with NEAREST fetches; the shader (`gridAmbient` in `rc2016/viewer/shading.py`, compiled only under `RC_SCENE_EFFECTS`) does 6 fetches for each of 8 corners. `LightGrid.sample` is the CPU mirror, used by the tests. A fragment with no existing valid corner falls back to the probe: `cover` is the valid trilinear weight times `COVER_GAIN` (4, a viewer choice), and the ambient is `mix(probe, grid * AMBIENT_SCALE, cover)`. AO still multiplies the ambient, and the cast-shadow ambient factor (`SHADOW_AMBIENT`, calibrated for the probe) is unchanged, so the grid's baked occlusion and those two stack.

**Inferred, not decoded.**
- **S is decoded by value.** The atmosphere header float at offset 128 (`sky.sky_term_scale`) reads 2.33 in Veldin's atmosphere, exactly the value the Veldin capture holds for the sky term, 2.5 in Veldin night, 1.75 in Novalis and 1.0 in most others (Aridia, Batalia, Kalebo, Pokitaru, Quartu and Rilgar, up to 10 in some Deplanetizer, Metropolis and research atmospheres). It is authored per atmosphere and feeds the shader and the no-probe maps; `SKY_TERM_SCALE` (2.33) remains only the fallback. One capture confirms the field, so the match by value is the evidence for its role elsewhere. Before, 2.33 was assumed for every level.
- `AMBIENT_SCALE` converts grid units to the viewer's ambient units (the viewer's diffuse is albedo * ambient / pi). It is 13.8: 11.0 was fitted with S assumed 2.33 so that the mean display luminance of the pixels the grid changes equals that of the probe ambient in two calibrated Novalis views (grid on over grid off: 1.07 open ground, 1.00 city). A grid normal facing up needs only 5.4 to match the probe's up-facing ambient at open-sky cells, and pi would follow from a grid that stores E / pi; the directional, occluded grid is darker on average, so overall brightness needs about 2 times the first and 3.5 times pi. Novalis' own S is 1.75, which lowers its level-average grid light by 1.255, so the scale was raised by that factor to 13.8 to keep the calibration. S and the scale trade off; only their product at sky-facing normals is constrained by the Novalis fit.
- The authored lights of `lighting.zone` are drawn on top of the grid. Whether the game's bake already contains some of them is unknown, so the preview may count them twice.
- The viewer's directional sun shadow does not appear in the bake; AO and `SHADOW_AMBIENT` act on the grid as on the probe.

**Effect** (Novalis, display luminance, grid on against off, same camera): open ground view p10, p50, p90 0.031, 0.112, 0.475 against 0.019, 0.131, 0.500; city view 0.034, 0.119, 0.543 against 0.012, 0.138, 0.574. The darkest tenth of the changed pixels is 37 to 65 % darker (shade under trees and overhangs) and the median 17 % brighter, so the lit-to-shade spread widens; shade hue moves slightly towards the sky colour. The frame costs about 7 to 20 ms more at 1600 x 900 (40 to 69 ms without, on a busy GPU), the 48 fetches per fragment running in each scene-effect material pass.

#### Display transform (`display.py`)

The **Game (captured)** tonemap (`uTonemap` 2; only the level viewer offers it, and only the Level atmosphere lighting uses it, so the Viewer rig and Shared HDRI looks are unchanged) replaces the ACES fit with the game's own final display chain, applied by `gameDisplay` in `rc2016/viewer/shading.py` to the lit colour, the fogged colour and the sky alike: scene-linear colour times an exposure, a 1D tone curve over log2 in [-8, +5], the sRGB transfer function, then a 32x32x32 colour-grade LUT sampled trilinearly. `rc2016/levels/display.py` is the CPU reference and builds the one float texture (unit `GRADE_TEXTURE_UNIT`) the shader samples: the grade in rows 0 to 31 (blue slabs side by side) and the decimated curve in row 32.

**Decoded** (shadPS4 RenderDoc capture of one Veldin frame, `assets/game_view/`; the chain reproduces that frame's pixels with a median error of 0.0099): the order of the stages, the curve (`rc_tonemap.spi1d`; its value for an unexposed 0.18, 0.0141, is the captured one, which fixes the domain) and the half-texel mapping of the grade lookup. The **colour-grade LUT is the atmosphere header's own** (bytes 312 to 131384 of the 131552-byte section, R8G8B8A8, red fastest, then green, then blue): Veldin's equals the captured `rc_grade.cube` exactly, so each level has its own grade (`sky.colour_grade`). Novalis' is a mild saturation boost (grey axis exact, up to 0.07 and 0.016 on average away from it), which the viewer applies from the Novalis header.

**Inferred, not decoded.** The exposure, 4.5538 (`EXPOSURE_MULTIPLIER`), is a runtime value of the Veldin frame, and the viewer assumes it for Novalis; the curve is a table whose per-level variation, if any, is unknown. Evidence for both on Novalis: with no other fit the exposure takes the captured clear sky (probe units) to 142, 190, 210 against the game's 147, 198, 218 in the blue-dominant pixels of the top fifth of the in-game frame (the former Neutral fit, which needed an exposure of 0.9, gave 156, 202, 224), and the flatter curve raises the lit-to-shadow ratio of grass from 6.6 to 8.4 (about 10 in the game). Not modelled, as in the research view: bloom, lens dirt, film grain and the screen fade. The game's curve is much flatter in the mid-tones than the ACES fit (a scene value of 0.18 displays at 0.39 through the game chain against 0.55 through ACES), which is why the former display looked too bright; `DIFFUSE_LIGHT_SCALE` was refitted for it ([Level atmosphere lighting](#level-atmosphere-lighting-probe_lightingpy)). The Blender pipeline's captured Veldin look (`pipeline/game_view_compositor.py`) uses the same tables.

#### Height fog (`fog.py`)

With the **Level atmosphere** lighting (and only then) the level viewer's surface and glass programs apply the game's analytic height fog: `displayFog` blends the lit colour before exposure and tonemap, `mix(fogColour, colour, T)`, per fragment, so far mountains and city turn hazy blue-grey while near terrain is almost unchanged (Novalis calibrated view: grass and trees within about 300 units move 2 to 5 percent in luminance, the far mountains about 40 percent). The sky domes are not fogged. The **Haze** checkbox under Lighting (`LevelViewport.set_fog`) turns it off; there is no fog for the rig, the shared HDRI, or a level without an authored profile or a captured clear-sky profile.

**Decoded from shader code** (material vertex shaders in the shadPS4 cache, e.g. 0x7cba9e6d; the dome fragment 0x8d3c81d0 shows the pixel blend). With `dy` the height of the surface above the camera in the game's native Y-up space (clamped to 1e-4 in magnitude), `dist` the distance and `start` a start distance, the fog is the integral of a piecewise-linear density-versus-height profile along the ray from `start` out to the surface: `hs = start * dy / dist`, four height ramps `t = clamp(a * h + b)`, `I = sum(alpha * (Ae^2 - As^2) + beta * (Ae - As))` with `As`, `Ae` the ramps at `hs` and `dy`, `tau = dist / dy * I`, `tau = tau ^ exponent` when positive and `T = clamp(2^-tau)`. `fog_T` is this formula; `build_ramps` derives `a, b, alpha, beta` from five knot heights and densities for a camera height. Inside the start distance T is exactly 1. The viewer shader works from the camera-relative offset (`uViewToWorld * vPos`, presentation Z-up, whose Z is the native Y) and the eye height, and switches to the constant-density form `c88 * dist + c89` for rays within 0.05 units of horizontal, where `1/dy` is unstable in single precision (the game's single-precision vertex code shares that weakness).

**Authored profile.** The atmosphere asset's 131552-byte header holds, after the colour-grade table, five ascending knot heights at offset 131424 and five densities in [0, 1] at 131444 (Novalis: -100, -52, 386, 926 then an out-of-order 400, densities 1, 0.1, 0.002, 0, 0; Veldin: -150, -20, 40, 250, 1000 with 1, 0.4, 0.07, 0.01, 0). `sky.fog_profile` reads them; a non-ascending knot pair (Novalis' last) is an empty ramp.

**Inferred, not decoded** (one indoor Veldin RenderDoc capture, no distant geometry, so distant T was never compared with GPU output):
- The header-to-optical-density mapping `rho = FOG_DENSITY_SCALE * -ln(max(1 - d, 1e-10))` per unit. It reproduces that capture's ramps for the upper knots to 4 digits and its first-knot density, but the capture's second knot sits at -24.1 against -20 in the header, unexplained.
- `FOG_DENSITY_SCALE` = 0.0099397 (fitted), `FOG_EXPONENT` = 1.45 and `FOG_START` = 13.2006 (read from that capture's constants); whether they are global or per atmosphere is unknown. They were not adjusted to the Novalis screenshot.
- `c88` (horizontal rays) uses the density at the camera height; the capture holds 1.45 times that, unexplained.
- The fog colour is not in the header. One colour serves the geometry fog and the sky dome (`fog.fog_colour`): `FOG_COLOUR_SCALE` (2.5, inferred) times the lowest bin of `clear_sky_profile` (sampled game data; Novalis 0.395, 0.688, 0.866, so 0.99, 1.72, 2.17). The capture's in-game colour was an HDR (1.67, 3.72, 5.20), level and frame specific and of the same order, so a plain horizon sample is too dim; it was not found in the header, and neither the tone curve nor the grade explains the factor (both are applied after the fog, to scene-linear values, and the grade is nearly neutral). The geometry used the plain sample (1.0) while the dome fit wanted 2.5; at the calibrated camera 1.0 leaves the far ridges grey-green and the sky band at the horizon cleaner than the game's, 2.5 gives their pale cyan haze and 4 washes the mid-ground out. Region numbers do not separate the two cleanly (the viewer's far terrain is brown where the game's is green), so the factor and the haze hue are approximate.
- Per-fragment evaluation replaces the game's per-vertex evaluation; how deferred opaque pixels receive fog in the game is not visible in the shader cache.

### Modes

The viewport is on the left and the controls column on the right (drag the sash between them to resize), as in the model viewer. With the **Horizontal** [layout](viewer.md#layout) the viewport is on top and the groups sit below it in three columns. The groups **Lighting**, **Display** (tonemap, **Hide smaller than** culling and **Native translation**), **Scene**, and **Experimental** controls. AO, Shadows and Authored lights apply only with Lighting on in Shaded mode, and Haze and Light grid only to the Level atmosphere lighting; they grey out otherwise. Experimental is off by default and shows its options only once enabled; its master gates the independently selected material and enemy-spawn previews. `--experimental-materials` enables the labelled Novalis mountain albedo-only matte and ground-layer previews while leaving spawns off. Ground uses candidate zone-keyed red/green paint on matched vertices and uniform blend-channel review controls on unlisted vertices; the runtime buffer join remains inferred, and UV-scale controls still substitute for native sampling. Instance overrides replace four Novalis terrain slots with this layered ground material. The default colour-breakup graph uses keyed alpha for gloss, F0 and normal blending, while its albedo uses one world-position texture; transporting alpha alone does not recover dirt colour. The hill slot uses a different graph and remains unsupported. The same Material previews switch also supports Novalis's cave-floor and cave-floor/sand replacement materials: their simpler deferred writer blends original source colours with each instance's squared alpha. Exact source-pass joins and agreement across its vertex exporters establish the candidate channel; runtime flag selection and the keyed-stream join remain inferred. These paint-only previews do not substitute world position or normals; with no recovered tangent-normal slice they use geometry normals. Pokitaru's eroded-rock, dirt-floor and dirt/grass island materials also have candidate painted-layer previews. Their empty source Blend Mask R input uses explicitly labelled uniform review value 1, with runtime empty-slot handling unverified; other resources and colour equations are retained. The grass-tile ground (`rp_pok_grass_tile_blend`) has an exact viewer rule ([rule table](viewer.md)) that reads the up-facing weight from the mapped normal; the coral shader still has unresolved inputs, so green regions are not all resolved. The original Novalis cave-wall tile texture is lavender and its native material has no proven paint channel. That alone does not establish in-game wall colour: the complete cave lighting, probe and caustics/effect paths are not reconstructed. Ordinary native readiness and exports remain unchanged. `--experimental-spawns` selects spawns without selecting material previews, and enables the master; both flags may be combined. Their source analysis is slow, so a level load skips it; once Material previews are enabled, during or after a load, the panel analyses the loaded materials in the background, one per loader thread task and pinned previews first, and reloads and swaps in only those that have a preview (16 of 368 candidates in Novalis) as each is ready; the rest keep their loaded entries. See [experimental material previews](viewer.md#experimental-material-previews) for source inputs, preview substitutions and unsupported limits. Ordinary Native translation remains under Materials and is independent of Experimental.

`Shaded` (model-viewer rules and eligible native translations), `Textured`, `Solid`, `Wireframe`, `Solid + wireframe`. Batches without UV0 draw as Solid.

### Hidden by default

Models whose path contains `collision`, `camera_coll`, ends in `_coll.model` or contains `environment/template/` (for example `gbl_gp_hero_collision_01`, the camera collision proxies such as Aridia's `tj_ari_gnd_Sludge_camera_coll`, which would otherwise cover the ground they follow with the default grid material, and `template_Square`, 152 instances in Novalis `lighting.zone`, assumed to be an editor helper) are loaded but not drawn. The **Collision/helpers** checkbox shows them. They are also excluded from the framing bounds.

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
| Shift | Fly speed times the Shift multiplier (default x4) |
| Wheel | Change the Shift multiplier (also editable in the side column, x1 to x10) |
| Ctrl | 10% of the fly speed, when **Ctrl = fine move** is ticked |
| `[` `]`, `-` `+` | Fly speed |
| Shift+Tab | Hide or show the side column |
| F11 | Borderless full screen (also hides the side column and the level list); F11 or Esc leaves it |
| F, Home | Reset to overview |
| Left click (no drag) | Identify a model or actor; actors report name, actor path, primary model and ID |
| Double click | Open the identified model in the Model tab (asset browser) and load it; it does not reset the camera |

Zone list: one checkbox per zone with its placed-instance count, grouped by name prefix, collapsed until clicked (Art, Gameplay, Lighting & atmosphere, Effects, Audio, Navigation, Other) with each `_imposter` twin after its zone; unticking a zone hides it at once, and **All** / **None** set every zone that has models. The side column also has the **Lighting** (`Viewer rig` or `Shared HDRI`) and **Tonemap** (`ACES fit` or `Khronos PBR Neutral`) selectors, which work as in the model viewer ([Lighting and tonemap](viewer.md#lighting-and-tonemap)), and the **Collision/helpers**, **Material overrides**, **Native translation**, **Sky** and **Actors** checkboxes. Native translation is on by default, affects Shaded mode only and switches surface frontends without moving the camera. The status line shows load state, zone and model counts, failures, material and texture counts, the number of instances drawn with material overrides and of override entries not applied, the sky (cloud domes drawn, sky meshes not ported, captured clear sky or flat background), visible instances and triangles, draw calls, frame time (ms) and speed. Instances and triangles are those drawn after culling ("showing N of M instances"); frame time is split into the scene passes and the main draw, where M is the placed total of the visible zones and layers.

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
                               [--lighting "Viewer rig"|"Shared HDRI"|"Level atmosphere"]
```

| Option | Meaning |
| --- | --- |
| `--source-root` | Folder holding the extracted archive. Default: the viewer's own saved root, else the asset browser's saved source root |
| `--level` | A `.level` or single `.zone` asset path, for example `levels/novalis/novalis.level`. Default: the last level used, else `levels/novalis/novalis.level` |
| `--zones` | Comma-separated zone short names or paths; only these zones load |
| `--mode` | Initial view mode (default `Shaded`) |
| `--capture` | Load the level, write one frame as an RGB PNG and exit |
| `--camera` | Camera `x,y,z` (presentation space, Z-up), yaw and pitch in degrees, applied after loading; alone it leaves the window open |
| `--probe-position` | `x,y,z` (presentation space): the Level atmosphere probe is the [reflection volume](#reflection-volumes-volumespy) containing it; defaults to the `--camera` position, else the level's default volume |
| `--no-material-overrides` | Start with **Instance material overrides** off (for before/after captures with the same camera) |
| `--no-native-translation` | Start with **Native translation** off (hand rules and generic/family readings) |
| `--no-sky` | Start with **Sky** off (flat background) |
| `--lighting` | Initial lighting source (default `Viewer rig`); with `--capture` the frame waits until the source's lighting maps are built |

`--capture` and `--camera` take effect when loading finishes. With `--capture` the settings file is not updated and the process prints the capture path, success flag, load timings (including the override outcome counts) and view stats.

Settings are stored in `%APPDATA%/RC2016 Asset Browser/level_viewer.json` (source root and last level, saved on close). The browser's root is read from `settings.json` in the same folder.

### Reference sizes

Measured on an RTX 3060: median frame time including GPU work (`glFinish`), and CPU-only time, for scale only:

| Level | Models | Instances shown | Triangles | Draws | Frame | CPU |
| --- | --- | --- | --- | --- | --- | --- |
| Novalis `art_buildings.zone` | 136 | 3,110 | 1.40 M | 491 | about 7 ms | about 7 ms |
| Novalis (18 zones, 8 with models) | 286 | 9,891 | 13.4 M | 989 | about 11 ms | about 7 ms |
| Veldin | 166 | 11,591 | 13.3 M | 412 | about 13 ms | about 7 ms |

These measurements predate frustum culling and mipmaps; culled frames draw fewer instances and triangles from most cameras. Draw counts include material overrides (without them: 424 and 829 for the two Novalis rows; Veldin has one overridden instance). Frame times were measured before overrides were drawn; the full Novalis level still captures at about 10 ms CPU time with them. The frames are rendered identically to drawing each zone separately (pixel-identical capture checked). Call overhead is about 7 ms per frame; the full Novalis and Veldin levels are bound by GPU work. All 286 Novalis models decode, including `tb_sewer_pipe_large_end`.

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
- Water: the `pok_uniq_water` replacement materials on ocean and pool planes use the [partial water sampling preview](viewer.md#glass-and-forward-materials): source height-displaced normal-tile coordinates, frozen time, and inferred shading/normal composition. Depth, shoreline coverage, foam and refraction remain unresolved. Planes that two overrides stack at one position draw both layers. The [liquid inventory](liquids.md) separates these graphs from lava, waterfalls and other flowing surfaces.
- Replacement materials go through the same rules as other materials; they are not included in the rule-coverage counts above (with overrides, 292 materials load for Novalis).
- Material-override entries whose slot name the placed model lacks (22% of entries) are not applied. Whether the game applies them some other way (for example by slot position) is not known; they are counted on the status line.
- Gameplay actors use the source-placement layer below. Runtime quest/save/spawn visibility, animation, attachments and unproven drawing components remain unsupported; ambiguous classes are skipped and reported.
- Distance LOD: every instance draws the Highest LOD. The decoder selects only Highest, Lowest or All per look (not LOD 1-2), the game's LOD switch distances are not decoded, and instance buffers are not split per LOD.
- Not handled: `_imposter` zones, quest-overlay regions, VFX (the lamp beams below are a stand-in), projector/box lights and normalized point lights (see [Authored lights](#authored-lights-scene_lightingpy)), collision volumes, `.level` spawn records (`0x3395AEC1`), decals, complete fog (only the partly inferred height fog below), sky meshes on graphs other than `Rk_Sky_Dome`, the stars term, cloud scrolling (time is frozen) and the atmosphere scattering tables.
- Undecoded instance fields: flags at `+0x5C`, the tail block at `+0xA0` (with `+0x9A`), the heap's other objects. Position-keyed vertex colours are decoded from one validated record; their shader meaning is inferred (see above).

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

Gaspar lava visibility depends on effective mesh slots, not declared override names. The Lava Rivers override to `gas_lava_01` does not match a model slot; the source flow material remains selected. The `gas_lava_ocean` preview covers 20 placed hazard pools, with seven more references on hidden templates. Cliff-lava and volcano graphs remain separate unresolved surfaces. See the [Gaspar graph selection table](liquids.md#gaspar-level-selection-and-ocean-surface).
