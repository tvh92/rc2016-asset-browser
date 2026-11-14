# Rift Apart (PC)

The Rift Apart reader lives in `rc2016/games/rcra/`. It reads the Steam PC install directly: nothing is extracted, and nothing is written to the game folder. This page covers the archive layer. Model, texture and material decoding are added in later steps.

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

## Evidence tool

```bash
python -m rc2016.games.rcra.inspect --game <install> --output-root <folder> --summary --list model --verify
```

`--verify` decompresses every block of every installed archive once, and checks that every toc row lies inside its archive. `--extract <asset path>` writes an asset's raw bytes, plus a `.stream` file for a streamed texture.

## Tests

`tests/test_rcra_archive.py` has two parts:

- **Synthetic tests** build a small toc, dag and set of DSAR archives.
- **Real-install tests** read `RCRA_GAME` (the install folder) and skip when it is not set. They also compare bytes against ALERT's own TOC2 reader.
