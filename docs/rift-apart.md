# Rift Apart (PC)

The Rift Apart reader lives in `rc2016/games/rcra/`. Rift Apart is a PS5 game; the reader takes its Steam PC release, not a PS5 dump, and reads the install directly: nothing is extracted, and nothing is written to the game folder. This page covers the archive layer, model geometry, textures, the material texture slots behind the viewer's labelled preview, and the translation of the games' own shader programs.

The layouts below were taken from two references and then checked against the installed files:

- ALERT `dat1lib` (GPL-3.0, vendored in `third_party/ALERT`)
- RCRA-Forge (GPL-3.0), for mesh, joint and texture header layouts

## How the browser picks the game

The folder chosen as the game folder picks the game, and the browser shows only that game's assets. `rc2016/games/` holds one `Game` record per game. `games.detect(folder)` tries them in this order:

| Game | Recognised by |
| --- | --- |
| Rift Apart | A `toc` whose first u32 is `0x34E89035`, a `dag` file and a `d/` folder |
| R&C 2016 | Exactly one extracted archive root under the folder (`layout.csv`, `toc`, `dag`, `chunkmap.txt` and segment files) |

A folder that matches neither gets an error naming both games. With the Transform only backend, a folder that matches neither is read as R&C 2016 loose files.

Once the scan recognises the game:

- The window title becomes "R&C Asset Browser — <game>".
- Settings keep a folder and an output root per game (`game.<id>.source`, `game.<id>.output`) beside the last-used `source` and `output`.
- An installed game, under Program Files or in a Steam library, never receives output. Its default output root is `Documents\R&C Asset Browser\<game> Output`.

Each `Game` record declares its capabilities and supplies hooks: model decode, material textures and rules, texture header, texture listing, loading and export, export material maps and shadow-only flags. Dispatch happens once per source open and once per model decode or export, so no hot loop sees the seam. R&C 2016's record names the functions it used before the seam, and its caches stay directly in `.cache/`. Other games cache under `.cache/<game id>/`.

### What Rift Apart supports

Rift Apart's capabilities are `models`, `textures`, `export` and `animation`:

- **Supported:**
  - Browsing.
  - The 3D model view with its labelled preview shading, and skeletal [animation](#animation) playback.
  - The Textures tab.
  - Model export to GLB, glTF and OBJ (FBX and USD through Blender), with skin weights and animation clips.
- **Disabled:** the Level and Flash UI types, the backend choice, the Blender scene and the shading filter. Each is greyed out with the tip "Not yet available for Rift Apart".
- **Clips:** found through the dag by `Game.animation_library`, without a scan's reference index.
- **Model list:** read in-process through `Game.list_models`, without the pipeline subprocess.

The export writes only what is decoded, through `rc2016/games/rcra_export.py`:

- Each material's base colour and its normal map, at full resolution.
- Every material is opaque, single-sided, non-metallic and fully rough, and its notes say so.
- A texture stored in a float or 16-bit data format is left out with a note, because its 8-bit conversion is not decoded. One example is the 4×4 `R32G32_FLOAT` `default_pink_shader_n` placeholder.

## Install layout

| File | Contents |
| --- | --- |
| `toc` | Asset table: the id, archive, offset and size of every asset, plus optional 36-byte asset headers |
| `dag` | The path, type and dependency list of every named asset |
| `d/*` | DSAR block-compressed archives; the `movie*`, `soundbank`, `soundbank.us` and `wem*` files are stored without DSAR |
| `dstoragecore.dll` | Microsoft DirectStorage 1.2. Its CPU GDeflate codec is the one we use |

## toc

- **Header:** u32 magic `0x34E89035`, then u32 DAT1 size, then the DAT1 itself (type `0x4D7CF320`). The installed file has a few padding bytes after the DAT1.
- **Rows:** each row is one copy of an asset. An asset id is the CRC-64 of its normalized path (`levels.zone_format.path_crc64`).

| Section | Contents |
| --- | --- |
| `0x506D7B8A` | u64 asset id per row |
| `0x65BCF461` | Per row: u32 size, u32 archive index, u32 offset into the archive's decompressed stream, i32 offset of a 36-byte header in `0x654BDED9` (-1: none) |
| `0x654BDED9` | 36-byte asset headers. When a row has one, it is prepended to the archive bytes; a model then has its DAT1 at +36 |
| `0xEDE8ADA9` | 256 spans of (u32 first row, u32 count). Ids are sorted within a span. Locale spans hold the language copies |
| `0x398ABFF0` | 66-byte archive records, with a NUL-terminated name such as `d\model` in the first 40 bytes |
| `0x36A6C8CC` | u64 ids of streamed textures |
| `0xC9FB9DDA` | One 72-byte record per streamed texture id |

**One asset, several rows.** An asset id can appear in more than one row:

- **Streamed textures** have two rows: the headered `.texture` (SD mips) and a headerless stream that holds the top mips.
- **Localized assets** have one row per language. A row's language is its archive's `.xx` suffix, or else the locale of the span it sits in.

`RcraSource.bytes()` returns the headered row in the base language. `texture_stream()` returns the HD stream.

## dag

- **Header:** u32 magic `0xB8EF3955`, u32 DAT1 size, then u32 0 and the DAT1 itself (type `0x2A077A51`).
- **Order:** one entry per named asset, sorted by path CRC-64.
- **The `0x933C0D32` field is not the asset id.** Ids are computed from the path strings instead.

| Section | Contents |
| --- | --- |
| `0xD101A6CC` | u32 offset of the path string in the DAT1 string blob (backslash separators) |
| `0x7A0266BC` | u8 asset type |
| `0xF958372E` | i32 start of the asset's dependency list in `0xBC91D1CC` (-1: none) |
| `0xBC91D1CC` | i32 dag indices; each list ends with -1 |

**What gets a name.** The dag names every model, texture, material and other content asset. The assets it does not name are Wwise audio, movies and some UI files. The browser lists those as `_unnamed/<archive>/<id>`.

**Dependency lists** are what `asset_references()` returns. For example, a model's dependencies are its materials and textures.

## DSAR archives

- **Header:** b`DSAR`, u32 version `0x10003`, u32 block count, u32 offset of the first data byte, u64 total decompressed size, u64 unused.
- **Block records** follow the header, 32 bytes each: u64 decompressed offset, u64 compressed offset, u32 decompressed size, u32 compressed size, u8 mode, then 7 unused bytes.
- **Block size:** usually 256 KiB, up to 4 MiB in texture archives.
- **Compression modes:**
  - **Mode 3, LZ4 block.** Decoded with the `lz4` package, or the pure-Python fallback in `formats.archive`.
  - **Mode 2, GDeflate.** Decoded by DirectStorage's `DStorageCreateCompressionCodecCore` in the install's `dstoragecore.dll`. The front-end `dstorage.dll` export returns `E_NOTIMPL` for the same request.
- **When the DLL is missing,** GDeflate assets fail with a message that names it, and LZ4 assets still read.

`BlockReader` covers a range read in three steps:

1. It bisects the block table to find the covering blocks.
2. It decompresses each block once. Decompressed blocks are kept in a shared 256 MB LRU, because neighbouring assets share blocks.
3. It joins the pieces.

## Source and caches

`RcraSource` offers the same surface the app uses on `formats.archive.Archive`: `entries`, `by_path`, `resolve`, `bytes`, `asset_data`, `asset_references`, `decode`, `close` and `checkpoint`.

- **Entry keys** are the asset id as 16 lower-case hex digits.
- **`entries` is built on request.** It is a read-only mapping that builds an entry dict only when one is asked for. `ids_of_type()` lists one type without building any entries.

**Index cache.** Hashing every dag path is the slow part of opening a source.

- The result is cached in `.cache/rcra/index_<key>.pickle`.
- The cache key covers the toc and dag hashes (through the shared `stat_file_hash` sidecar) and the reader's own source.
- A warm open takes about half a second.

## Models

`rc2016/games/rcra/model.py` decodes a `.model` asset into the same dict schema as `formats.native_model.decode_model` (`schema='rcra-pc-model-v1'`). Because the schema matches, `viewer.loader.mesh_payload`, `_selected_meshes` and the glTF exporter use the result unchanged. `decode_for_viewer` is the `Game.decode_model` hook.

### Asset and DAT1 layout

- **Asset header:** the 36-byte toc header comes first: u32 magic `0x9D2C0FA9`, u32 streamed-section offset, u32 streamed-section size, then 24 zero bytes.
- **DAT1:** starts at +36, with type `0x9D2C0FA9`.
- **String pointers** are offsets from the DAT1 start.

| Section | Contents |
| --- | --- |
| `0x283D0383` built | f32 position offset at +0x1C; f32 position scale at +0x2C (1/4096 in every checked model); u32 at +0x30 whose low 4 bits give the UV scale `(1 << n) / 16384` |
| `0xA98BE69B` | 16-byte vertex: int16 x, y, z, w; u32 packed normal; int16 u, v |
| `0x0859863D` | u16 indices. Mesh flag `0x10` makes them relative to the mesh's first vertex; without it they are global |
| `0x78D9CBDE` | 64-byte mesh record (fields below) |
| `0x06EB7EFC` | Looks: 8 (u16 first mesh, u16 count) LOD slots per look. The 2016 game uses 4 |
| `0x3250BB80` | Materials: the first half of the section holds (u64 path, u64 name) string pointers |
| `0x6B855EED` | int16 UV1 per vertex, at the same UV scale |
| `0x5CBA9DE9` | u8 RGBA per vertex |
| `0x15DF9D3B` | Joints: i16 parent, u16 index, u16, u16 flags (bit 0: segment scale compensation, as in 2016), u32 name hash (game CRC-32 of the name), u32 name pointer |
| `0xDCC88A19` | Joint rest S/Q/T, 48 bytes each, then the inverse binds from the next 64-byte boundary (the 2016 layout) |
| `0xC61B1FF5` | Skin batches, 16 bytes: u32 offset into `0xDCA379A2`, u32 remap, u16 palette size, u16 (not read), u16 vertex count, u16 first vertex in the mesh |
| `0xDCA379A2` | Skin data: groups of 16 vertices per batch (see [Skin weights](#skin-weights)) |
| `0x5240C82B` | Skin joint palettes: u16 skeleton joints, one palette per batch that names a size |

`0x6B855EED` and `0x5CBA9DE9` may be zero-padded to a 16-byte multiple.

**Mesh record fields (`0x78D9CBDE`):**
- +4: u64 mesh id
- +20: four u32: first vertex, first index, index count, vertex count
- +36: four u16: flags, material slot, first skin batch, skin batch count word (the mesh owns the low byte's count of batches; the high byte is not read)
- +56: u32 first weight index

### Vertex decoding

- **Positions** are `int16 * scale + offset`.
- **Normals** use ALERT's two-angle form: x and y come from two 10-bit fields, z from the remainder, and z's sign is bit 31. Across the corpus, decoded normals agree with face normals at a median dot product of 0.9995.
- **UVs** are `int16 * uv scale`. V is flipped for the Blender convention, as the 2016 decoder does.
- **The vertex w word is not decoded.** It spans the whole int16 range, so it is not a ±1 handedness. Tangents are therefore not available yet.

### Skin weights

Each skin batch covers `vertex count` vertices from `first vertex` of its mesh. Its data is a run of groups of 16
vertices; the last group may be shorter. Each group is:

1. A byte n.
2. For n = 0: one u8 joint id per vertex, with weight 1.
3. Otherwise: n + 1 (u8 joint id, u8 weight) pairs per vertex, at most 8 influences. Weights sum to 255 or 256 and are
   normalized.

**Joint ids.** A batch with a palette size reads the u8 id as an index into that many u16 skeleton joints, at byte
offset `remap` of `0x5240C82B`. A batch without one adds `remap` to the id. These are the R&C 2016 rules, used by the
rigs of more than 256 joints. Remapping brings the median joint-to-vertex distance on those rigs from about 2.0 to
0.28 units.

**The weight records in `0xCCBAFF15` are not read.** They hold the same weights as fixed 8-byte records (4 u8
joints, 4 u8 weights) for the meshes with flag `0x100`, from the mesh's first weight index. The batches cover every
skinned mesh:

- Across the install's 959 skinned models (41,818 meshes), every batch parses and covers its mesh.
- On the 77,722 batches that both forms hold, the raw batch ids and weights equal the records exactly.
- So the records keep palette-local ids, and only the remapped id is a skeleton joint. On a remapped rig, the record
  ids put the Thug Brawler's pistol on `sword_RT_unequip_chunk`; the remap puts it on `gun_unequip_chunk`.

`joint_count` is the skeleton size when the model has `0xDCC88A19`. The skeleton is still listed in `joints`.

### Not decoded yet

These sections are listed in `unsupported_sections` and are not interpreted.

**Models without geometry:** 24 models have no vertex or mesh section, only looks, locators or joints. Decoding one raises `UnsupportedModelError` with a message saying so.

## Animation

Rift Apart clips play in the 3D view and export with the model, like R&C 2016 clips. The decoder is
`rc2016/games/rcra/animation.py`; `rc2016/games/rcra/clip_library.py` finds a model's clips and is the
`Game.animation_library` hook.

### Clip assets

Each clip is its own `.animclip` asset: the 36-byte toc header (type `0x0F64FFE8`) and a DAT1 of the same type. Its
sections have the R&C 2016 clip ids ([animation](animation.md#sample-layout)), and the base records, base scale
tail, 48-byte destination layout and frame-major bit packing are the 2016 version-15 layout. So
`animation_transforms.sample_transforms` turns a decoded clip into scale, rotation and translation per joint
unchanged. Joint hashes are the game CRC-32 of the model's joint names.

Four things differ from 2016:

| Section | Rift Apart |
| --- | --- |
| `0x9DF23F77` built | +0 u32 is the name's string offset, not a format version. +4 name hash; +8 flags (`0x40` additive, `0x10000` frame table); +12 f32 fps; +16 f32 seconds; +32 u16 joints, frames, elements, 0; +40 u32 bytes per stored sample; +44 u16 scale-tail blocks, u16 count of 4-byte records after the tail; +48 u16 stored samples |
| `0xD070D358` elements | u32 destination per element (not u16), then u8 bit width minus one each, zero-padded to 4 bytes |
| `0x3A7B4855` samples | With flag `0x10000`: `round(seconds * fps) + 1` u16 stored-sample indices, padded to 16 bytes, then the stored samples. A clip that repeats a pose stores it once. Without the flag, frame i is stored sample i |
| `0x2BB5BC8F` base | Joint records and scale tail as in 2016; one additive clip adds the 4-byte (u16 joint, u16 value) records the built header counts, which are not read |

A clip with only custom float tracks (`0x14014CB6`/`0x116EB684`: camera and property curves) is not skeletal and is
refused. Root motion (`0x3976E44C`, `0xE08AA35F`), triggers, morph and face sections are not read.

**Coverage.** Of the 23,317 installed clips, 21,377 are skeletal and 21,003 decode. The rest:

- 10 have a negative packed scale (a mirrored joint), which the 2016 sampler refuses as well.
- 1 has a payload 12 bytes longer than its header implies.
- 1,940 are not skeletal: custom-track clips and one camera-shake clip.

No decoded clip uses the collapsed-rotation sentinel.

**Check.** On `amb_fish_01`, frame 0 of `Swimming_Idle` matches the model's rest S/Q/T with a median translation
difference of 1.1e-4.

### Skinning

Each skinned mesh's weights are in skin batches (`0xC61B1FF5`, `0xDCA379A2`). The layout is in [Models](#models).
Rest S/Q/T and inverse binds (`0xDCC88A19`) have the 2016 layout, read by `formats.skeleton.inspect_model`.
Both 2016 inheritance rules reproduce the stored inverse binds:

| Model | Max inverse-bind error |
| --- | --- |
| Fish | 4.5e-7 |
| Ratchet | 1.8e-5 |

### Finding a model's clips

Clips are found through the dag, never by joint coverage alone:

1. **Referenced animsets.** An actor or config that depends on the model, and the configs it depends on, name the
   animsets. Each animset depends on its clips.
2. **Same-folder animsets.** These are the animsets in the model's own folder whose first clip names at least half of
   the model's joints. The runtime assembles some models, such as the Ratchet outfits, and no actor names them.
3. **Cutscene shots** (3D view, or export with Include cutscenes). These are clips under `cinematics/` whose name
   ends in `_shot<N>_<performer>`. The performer must be the model's file stem or one of its folder names holding an
   underscore, as written or without a `_master<N>` or copy-number suffix. The clip's `.cinematic2` is its source.

Each clip is still classified on its first frame, as in 2016. The clip list hides one that does not drive the
skeleton. For example, `hero_ratchet.model` gets:

- 1,380 animset clips from five animsets, among them `hero_rivet.animset`, which an actor pairs with the model.
- 532 cutscene shots.
- Of those 1,912 clips, 212 are additive layers, 38 are unplayable and 20 are copies.

The clip notes take about 8 s to compute, off the Tk thread.

### Not decoded

- Negative packed scales.
- Root-motion paths.
- Clip blending and additive partners. Additive clips are previewed on the rest pose, as in 2016.
- Face performance clips (`.performanceclip`).
- Vertex-animation textures.

## Textures

`rc2016/games/rcra/texture.py` reads `.texture` assets. It returns the same dicts as the 2016 viewer's texture functions:

- `load_texture`, `load_texture_full` and `read_top_mip` return the loaded top mip.
- `texture_header` returns the header facts.
- `export_textures` also writes the asset and its HD stream.
- `archive_textures` lists every texture in the install.

### Layout

- **Asset header:** the 36-byte toc header comes first: u32 type `0x8F53A199`, u32 DAT1 size, u32 SD byte count.
- **DAT1:** a small DAT1 follows. Its one section, `0x4EDE3593`, is the 44-byte texture header.
- **SD mip chain:** follows the DAT1.
- **HD stream:** a streamed texture's headerless toc row holds the larger top mips the SD chain lacks.

**Header fields.** The 2016 game uses the same layout.

| Offset | Field |
| --- | --- |
| +0 | u32 SD bytes |
| +4 | u32 HD bytes |
| +8 | u16 HD width, u16 HD height |
| +12 | u16 SD width, u16 SD height |
| +16 | u16 array size |
| +18 | u8 Insomniac format, u8 planes |
| +20 | u16 DXGI format |
| +30 | u8 SD mip count |
| +32 | u8 HD mip count |

### Storage on PC

**Mips** are stored linearly in DXGI order. There is no tiling.

**Arrays and cube maps** store one whole chain per slice, back to back.
- A cube map has an SD payload exactly six chains long (108 sky and probe textures). Its six faces are read as six slices.

**Format coverage.** All 12,502 headers parse, and their SD and HD sizes match the computed chains exactly.
- **Previewable:** BC1–BC7, BC6H, RGBA8, R8 and R8G8.
- **Sized but not previewed:** the float data formats (RG32F, RGBA16F, RGBA32F, RG16F, RG16, R16F) used by vertex-animation and lookup textures.

## Materials and the viewer preview

`rc2016/games/rcra/material.py` reads the texture slots of a `.material`. It also provides the `Game` hooks `material_textures`, `model_texture_list`, `material_rules` and `effect_preview`.

### Layout

- **Asset header:** a material asset is the 36-byte toc header (type `0x88730155`) and a DAT1 of the same type.
- **Section `0xF5260180`** begins with ten u32:
  1. section size
  2. parameter count
  3. unknown
  4. unknown
  5. parameter batch end
  6. texture count
  7. texture batch start
  8. texture batch end
  9. unknown
  10. unknown
- **Texture records:** each is (u32 string offset from the batch end, u32 slot key). The strings follow the batch.
- **Section `0xE1275683`** is the 40-byte flags block, as in the 2016 game.
- **Fur materials** have no `0xF5260180` section, so they bind no textures.

### Slot keys

Slot keys are the low 32 bits of the 2016 game's `0x49A7866B........` slot hashes. Across 5,721 materials they bind the same texture suffixes:

| Slot key | Texture | Count |
| --- | --- | --- |
| `ABDAD780` | Base colour (`_c`) | 2,600 of 2,862 |
| `7AE34E7A` | Normal (`_n`) | 2,591 of 2,647 |
| `1576D848` | `_g` | — |
| `E68930AC` | `_m` mask | — |

### The labelled preview

The viewer draws a labelled **Rift Apart preview** (`rule_kind 'rift apart preview'`).

**What the preview uses:**
- **Base colour:** from the material's `ABDAD780` slot.
- **Normal:** from the material's `7AE34E7A` slot.
- **Everything else:** neutral parameters.

**What it leaves out:**
- **`_g` and `_m` maps:** their channel meanings are not decoded.
- **Material graphs:** no graph shader is translated, except architecture glass (next section). 1,474 graphs exist; 1,386 are bound by materials and 29 materials bind none.
- **Gloss, metal, emission and blending:** these are review values, not game data.
- **Fur shells:** they have no texture slots, so they draw in the neutral colour.

### Architecture glass (`gbl_arch_glass`)

The one decoded graph. 44 materials use it, among them the Nefarious trooper and swarmer domes. Every graph embeds its shaders (about 15 DXBC containers each, SM5 and DXIL, with named reflection), so the rule is traced to the graph's forward pixel shader (SM5 `ps_5_0`, disassembled with `D3DDisassemble`), not to a capture. The viewer draws these materials alpha-over through the forward glass program (`uKind 26`; GLSL comment in `rc2016/viewer/shading.py`), with `rule_kind 'rift apart glass'`.

**Graph layout** (`graph_layout`). Three sections of the graph, then the material's own values (`material_parameters`):

| Section | Content |
| --- | --- |
| `0x1CAFE804` (graph) | Slot table, rows of (u32 default-texture string offset, u16, u16 unit, u64 slot hash). Unit `u` is texture register `t(5 + u)`. |
| `0x45C4F4C0` (graph) | Parameter records (u16 byte offset, u16 byte size, u32 key); the offset is the dword position in `cb2`. |
| `0xA59F667B` (graph) | The float defaults of `cb2`. |
| `0xF5260180` (material) | The same record layout from byte 40, its values in a block that starts at header word 2. |

`cb2` is the graph's defaults overwritten by the material's values at the graph's offsets. An unbound texture unit takes the graph's default texture.

**Texture units**

| Unit | Slot | Role | Use |
| --- | --- | --- | --- |
| `t5` | `A6C3B1D5` | `_pick` | One scalar, added to the alpha. The default is a mid-grey. |
| `t6` | `ABDAD780` | `_window_c` | Colour. |
| `t7` | `7AE34E7A` | `_n` | Normal x, y. |
| `t8` | `6376D191` | `_window_g` | F0 in rgb, gloss in alpha. |

**Parameters** (dword in `cb2`; the trooper dome value in brackets, then the graph default)

| Dword | Key | Name | Use |
| --- | --- | --- | --- |
| 0 | `FD3F0F1D` | `soft_flag` | Scene-depth fade on the colour and alpha (1, default 1). |
| 1 | `0C0A6B30` | `emissive_adapt` | Non-zero: the emission is scaled by 4 `m_AdaptedLuminance` (1, default 0). |
| 2 | `24F4CA4C` | `gloss_scale` | Scale of the `t8` gloss (0.9, default 1). |
| 3 | `70137DA7` | `rim_width` | Width of the grazing rim (0.2, default 0.25). |
| 4 | `74979B4A` | `colour_k` | Colour factor at full alpha (0.5, default 5). |
| 5 | `8E7EC8A1` | `g_mode` | At or above 0.5 `t8` is F0 rgb and gloss alpha; below, F0 is `t8.g` and gloss is `sqrt(t8.r)` (1, default 0). |
| 6 | `06F19114` | `emission` | Self-lit amount (15, default 0.05). |
| 7 | `CAF31501` | `alpha_floor` | Lowest alpha before the rim and `t5` (0.4, default 0.4). |

**Equations.** `N' = normalize((1 - x² - y²) N + x T + y B)`, `(x, y) = 2 n.rg - 1`.
- **Rim:** `rim = sat(((1 - N'·V)⁴ - w/2) / (w/2))`.
- **Alpha:** `pre = max(rim, floor) + t5.r`, `alpha = sat(pre)`, times the vertex alpha (taken as 1).
- **Colour:** `c' = c (1 + sat(pre + soft_flag · depth_fade) (k - 1))` with `depth_fade` 0 here, emission `E = emission (1 - rim/2)`, times `4 L` with the adapt flag.
- **Specular:** GGX alpha `(0.9725 - 0.7514 gloss)⁴`, Schlick Fresnel on `F0`.
- **Result:** `rgb = c' (E + Ld) + specular`, then the fog.

**Not decoded.** These are viewer stand-ins, and the preview says so (`GLASS_LIMITATION`):
- **Lighting:** the light list, shadows, probe and screen-space reflections and fog are the viewer rig. The specular lobe is the shared glass lobe, not the program's own visibility term (its Fresnel has F90 = 1).
- **Frame:** the soft depth fade is 0 (no scene depth); the vertex alpha, the alpha override and the dissolve discard are 1, 0 and off; the normal frame comes from screen derivatives, not the vertex tangent.
- **Exposure:** `m_AdaptedLuminance` is not known. The preview uses 1.0 (`RCRA_ADAPTED_LUMINANCE`), so a material with a large `emission` (the trooper and swarmer domes: 15) draws far brighter than a lit surface.
- **Blender:** there is no Blender rule; the Blender scene is not available for Rift Apart.

**Translated program.** The automatic path (see [Material graph shaders](#material-graph-shaders)) reproduces these equations: on the trooper dome, with the real material values and textures, its alpha and emissive colour match them to float precision (`tests/test_rcra_shaders.py`). The translation also shows the terms the preview leaves out:
- the material and vertex alpha (`TEXCOORD.w`) and the per-draw alpha override (`MatDrawCB.m_AlphaOverrides`)
- the custom clip plane and the dissolve test on `TEXCOORD.z`, which set the alpha to 0
- a fog factor on the alpha, gated by render flag `0x4000000`
- the unlit path: with render flag `0x400` clear, the lighting term is exactly 1
- the normal frame: the vertex tangent and its bitangent sign, not screen derivatives

## Material graph shaders

Every `.materialgraph` carries its compiled programs, and the engine's own passes are in `RiftApart.exe`. Both are read in place; nothing is copied out of the game folder.

| Module | Role |
| --- | --- |
| `rc2016/shaders/dxbc.py` | DXBC container: chunks, RDEF reflection (constant buffers with struct members, bindings), signatures, and the SM4/SM5 token stream. DXIL containers are identified only. |
| `rc2016/shaders/sm5.py` | SM5 program to a typed expression DAG, with a numeric evaluator. |
| `rc2016/games/rcra/shaders.py` | Pass table, pass programs, the generic material binding and the neutral engine profile. |

### Programs in a graph

- **Shader section `0xBBFC8900`:** entries on 16-byte boundaries, with zero bytes between some of them. An entry is a 12-byte header (u32 magic `IGSH`, u32 SM5 size, u32 DXIL size), the SM5 container, then the DXIL container. Raytracing libraries use magic `IGRS` and are DXIL only (395 graphs).
- **Pass table `0x8C049CCA`:** 18 records (1,281 graphs) or 36 (193 graphs) of (u32 flags, u32 hash, u32 entry offset from the section start, u32 entry size). The record index is the pass slot; an entry's size is 12 + SM5 + DXIL.
- **Raytracing record `0xF9C35F30`:** one record of the same shape, pointing at the `IGRS` entry.

The slot meanings below come from each program's stage, outputs and bindings across all 1,474 graphs:

| Slot | Graphs | Program |
| --- | --- | --- |
| 1 | 1,474 | Forward pixel program, one target. It does its own lighting: light grid, probes, sun shadows, SSR, GI, fog. |
| 4 | 1,474 | G-buffer pixel program, 4 targets (5 in 54 graphs): packed normal and specular, albedo and occlusion or emission, view depth, velocity. |
| 5 | 1,474 | The same writer without velocity (3 targets). |
| 8 | 1,474 | Depth-only pixel program (alpha test). |
| 11 | 1,474 | Two-target pixel program. |
| 15, 16 | 1,474 | GI voxelization pixel program (UAV stores) and its geometry program. |
| 3, 7, 10, 13, 17 | 208 | Vertex programs of vertex-deforming graphs. |
| 0, 14, 18 to 31 | fewer | Other pixel and vertex programs, mostly in the 36-record tables. |

The engine vertex program that feeds slots 1, 4 and 5 is in the exe (vertex pulling from `g_MainVBPosView`, `g_MainVBNrmTanView`, `g_MainVBUV0View` and others). Its translation gives the interpolants:

| Interpolant | Content |
| --- | --- |
| `TEXCOORD.xy` | UV0 (int16 × the subset's UV scale / 16384) |
| `TEXCOORD.zw` | Material and object values (dissolve, alpha) |
| `TEXCOORD1.xy` | UV1 |
| `TEXCOORD2.xyz`, `.w` | World normal; instance index + 0.25 |
| `TEXCOORD3.xyz`, `.w` | World tangent; bitangent sign |
| `TEXCOORD4` | Vertex colour or paint |
| `TEXCOORD5` | Previous clip position (velocity) |

### Translation

`dxbc.program` decodes the token stream: opcodes, operands with swizzles, masks, modifiers and relative indices, declarations and the immediate constant buffer. Over all 7,899 distinct SM5 programs in the graphs, its instruction list matches `D3DDisassemble`, mnemonic by mnemonic and operand count by operand count. The one naming difference is the disassembler's `resinfo_uint`.

`sm5.translate` turns a program into a hash-consed DAG:

- **Types:** every node is a float, an integer bit pattern or a comparison result. Bitcasts fold for constants; folding uses float32 arithmetic.
- **Branches:** each `if`, `else` and `switch` case runs as its own region and joins with one `select` per register.
- **Loops:** a loop is unrolled while its exit condition folds. A data-dependent loop (a light list, for example) poisons only the registers it writes, with an `unknown` node, so outputs that do not depend on it still translate.
- **Discard and return:** both become predicates.
- **Names:** constant-buffer reads become inputs named from the reflection, such as `GlobalWorldCBuffer.m_KeyLightVec+4` or `MultiCustomMaterialCB.g_CustomMaterial_Buffer+12`. Interpolants become inputs named by semantic, such as `TEXCOORD2.x`. Texture reads become `sample` and `load` nodes carrying the binding name.
- **Compute programs:** UAV stores are kept with their predicate (`Translation.stores`).

A `Bindings` object can replace any input during translation.

**Material binding.** The binding is the same for every graph:
- `cb2` (`MultiCustomMaterialCB`) holds the graph's float defaults, overwritten by the material's values at the graph's offsets (`material_constants`).
- Texture unit `t(5 + u)` (`textureUnit<5+u>`) is the graph's slot-table row `u`: the material's texture for that slot, else the row's default (`material_units`).

**Neutral engine profile** (`NEUTRAL_INTEGERS`, `NEUTRAL_FLOATS`): no local lights or probes (lookup word counts 0), no debug view (`m_MiscFlags` 0), no optional features (`m_FeatureFlags`, `m_RTFlags` 0) and 8 sun-shadow taps. These are viewer choices, not game data. Every other engine input stays a named input.

### Coverage

With the neutral profile, over all programs (`ready` means no output depends on an unknown value):

| Slots | Ready | Material uses covered |
| --- | --- | --- |
| G-buffer 4 and 5 | 1,449 of 1,474 programs | 5,611 of 5,692 |
| Forward 1 | 356 fully, 1,073 in part, 45 not at all | 552 of 5,692 fully |
| Vertex 3, 7, 10, 13, 17 | 194 of 208 | 526 of 583 |

The forward programs still in part keep further engine loops, so their outputs need more engine inputs bound. Not translated:
- groupshared memory
- tessellation control-point inputs (3 graphs)
- UAV atomics
- the DXIL-only raytracing libraries

### G-buffer and deferred lighting

The G-buffer writer of `basic_normal_gloss` (666 materials) translates fully. The engine's deferred apply programs are compute programs in the exe that read `g_GBufNormalSpec`, `g_GBufDiffuseEmis` and `g_GBufExtra`. Their translations decode the targets:

| Target | Content |
| --- | --- |
| NormalSpec x, y | Normal: 12 bits each, `n = v × 0.00069070 − 1.41421` (octahedral-style, z sign in bit 15 of y); class in bits 13 to 15 of x (bit 12 set means class 0) |
| NormalSpec z, w | Bytes: F0 red, F0 green (z high, low), F0 blue (w high), gloss (w low). F0 is stored as `sqrt(F0) × 255` and read as `(byte / 255)²`. |
| DiffuseEmis rgb | Albedo (`basic_normal_gloss`: `t5.rgb × tint`, `cb2` dwords 0 to 2) |
| DiffuseEmis a | Occlusion when NormalSpec y bit 13 is set; otherwise emission `2^(5a) − 1` |

The class-0 apply program (exe offset 59122268) gives the deferred BRDF. It is not the R&C 2016 one ([shading](shading.md#opaque-deferred-brdf)):

| Term | Rift Apart |
| --- | --- |
| GGX | `α = (0.9725 − 0.7514 gloss)²` |
| Visibility | Smith joint approximation, `0.5 / (NL (NV (1 − α) + α) + NV (NL (1 − α) + α))` |
| Fresnel | Schlick, F90 = 1 |
| Diffuse | Lambert `sat(N·L)`, no 1/π, times the light colour |

### Not done yet

- **Viewer:** the viewer does not draw translated programs yet. The glass preview is still the hand-written `uKind 26` program.
- **Blender:** no Blender node emitter or Rift Apart BRDF group exists yet.
- **Engine inputs:** the key light, ambient and probe values the programs read are not chosen yet, and neither are the engine textures (`g_BrdfLookup`, `g_EnvProbeDefault`).

## Evidence tool

```bash
python -m rc2016.games.rcra.inspect --game <install> --output-root <folder> --summary --list model --verify
```

`--verify` decompresses every block of every installed archive once, and checks that every toc row lies inside its archive. `--extract <asset path>` writes an asset's raw bytes, plus a `.stream` file for a streamed texture.

## Tests

`tests/test_rcra_archive.py`, `tests/test_rcra_model.py`, `tests/test_rcra_texture.py`, `tests/test_rcra_glass.py` and `tests/test_rcra_animation.py` each have two parts:

- **Synthetic tests** build a small toc, dag and set of DSAR archives, a two-mesh model, texture and material assets, clips (plain and with a frame table) or skin batches (with a palette and a joint base).
- **Real-install tests** read `RCRA_GAME` (the install folder) and skip when it is not set. They also compare bytes against ALERT's own TOC2 reader.

`tests/test_sm5.py` compiles small HLSL programs with Windows' `d3dcompiler_47` at test time, checks the decoder against `D3DDisassemble` and the translator against known results (branches, loops, switch, bit packing, discard, reflection names, UAV stores). It skips without the DLL. `tests/test_rcra_shaders.py` (`RCRA_GAME`) checks the pass table, the glass forward program against the hand decode on the trooper dome, and the `basic_normal_gloss` G-buffer writer.
