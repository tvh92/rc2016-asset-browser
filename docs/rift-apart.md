# Rift Apart (PC)

The Rift Apart reader lives in `rc2016/games/rcra/`. It reads the Steam PC install directly: nothing is extracted, and nothing is written to the game folder. This page covers the archive layer, model geometry, textures and the material texture slots behind the viewer's labelled preview.

The layouts below were taken from three references and then checked against the installed files:

- ALERT `dat1lib` (GPL-3.0, vendored in `third_party/ALERT`)
- ripped_apart (MIT)
- riftripper (GPL-3.0)

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
| `0x15DF9D3B` | Joints: i16 parent, u16 index, two u16, u32 name hash, u32 name pointer |

`0x6B855EED` and `0x5CBA9DE9` may be zero-padded to a 16-byte multiple.

**Mesh record fields (`0x78D9CBDE`):**
- +4: u64 mesh id
- +20: four u32: first vertex, first index, index count, vertex count
- +36: four u16: flags, material slot, first skin batch, skin batch count
- +56: u32 first weight index

### Vertex decoding

- **Positions** are `int16 * scale + offset`.
- **Normals** use ALERT's two-angle form: x and y come from two 10-bit fields, z from the remainder, and z's sign is bit 31. Across the corpus, decoded normals agree with face normals at a median dot product of 0.9995.
- **UVs** are `int16 * uv scale`. V is flipped for the Blender convention, as the 2016 decoder does.
- **The vertex w word is not decoded.** It spans the whole int16 range, so it is not a ±1 handedness. Tangents are therefore not available yet.

### Not decoded yet

These sections are listed in `unsupported_sections` and are not interpreted.

**Skinning is not read yet.** Skinned models show their bind pose, which is how their vertices are stored.
- `0xCCBAFF15` holds 8 bytes per vertex (4 u8 joints, then 4 u8 weights) for meshes with flag `0x100`, reached through the mesh's first weight index.
- `0xC61B1FF5` and `0xDCA379A2` batches differ from the 2016 layout.
- Until skinning and animation are read, `joint_count` is 0 and the joints are listed in `joints`.

**Models without geometry:** 24 models have no vertex or mesh section, only looks, locators or joints. Decoding one raises `UnsupportedModelError` with a message saying so.

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
- **Material graphs:** no graph shader is translated.
- **Gloss, metal, emission and blending:** these are review values, not game data.
- **Fur shells:** they have no texture slots, so they draw in the neutral colour.

## Evidence tool

```bash
python -m rc2016.games.rcra.inspect --game <install> --output-root <folder> --summary --list model --verify
```

`--verify` decompresses every block of every installed archive once, and checks that every toc row lies inside its archive. `--extract <asset path>` writes an asset's raw bytes, plus a `.stream` file for a streamed texture.

## Tests

`tests/test_rcra_archive.py`, `tests/test_rcra_model.py` and `tests/test_rcra_texture.py` each have two parts:

- **Synthetic tests** build a small toc, dag and set of DSAR archives, a two-mesh model, or texture and material assets.
- **Real-install tests** read `RCRA_GAME` (the install folder) and skip when it is not set. They also compare bytes against ALERT's own TOC2 reader.
