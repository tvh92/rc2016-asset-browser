"""Rift Apart ``toc`` and ``dag`` readers (numpy views over the raw DAT1 bodies).

toc: u32 magic 0x34E89035, u32 DAT1 size (the file may carry padding after it), then a DAT1 (type 0x4D7CF320) whose sections are
  0x506D7B8A  u64 asset id per row (CRC-64 of the asset path, levels.zone_format.path_crc64)
  0x65BCF461  per row: u32 size, u32 archive index, u32 offset in the archive's decompressed
              stream, i32 byte offset of a 36-byte asset header in 0x654BDED9 (-1: none)
  0x654BDED9  36-byte asset headers, prepended to the archive bytes when present
  0xEDE8ADA9  256 spans of (u32 first row, u32 row count); ids are sorted within a span
  0x398ABFF0  66-byte archive records, NUL-terminated name in the first 40 bytes ("d\\model")
  0x36A6C8CC  u64 ids of streamed textures; 0xC9FB9DDA one 72-byte record per such id
A texture with HD mips has two rows with its id: the headered ``.texture`` and a headerless
stream holding the top mips.

dag: u32 magic 0xB8EF3955, u32 body size, u32 zero, then a DAT1 (type 0x2A077A51) with one entry
per named asset, sorted by path CRC-64:
  0xD101A6CC  u32 offset of the path string (DAT1 string blob, backslash separators)
  0x7A0266BC  u8 asset type
  0xF958372E  i32 start of the asset's dependency list in 0xBC91D1CC (-1: none)
  0xBC91D1CC  i32 dag entry indices, each list terminated by -1
"""
import re
import struct
from dataclasses import dataclass

import numpy as np

TOC_MAGIC = 0x34E89035
DAG_MAGIC = 0xB8EF3955
TAG_IDS, TAG_META, TAG_HEADERS = 0x506D7B8A, 0x65BCF461, 0x654BDED9
TAG_SPANS, TAG_ARCHIVES = 0xEDE8ADA9, 0x398ABFF0
TAG_TEXTURE_IDS, TAG_TEXTURE_META = 0x36A6C8CC, 0xC9FB9DDA
TAG_NAMES, TAG_TYPES, TAG_DEP_STARTS, TAG_DEPS = 0xD101A6CC, 0x7A0266BC, 0xF958372E, 0xBC91D1CC
HEADER_SIZE = 36
ARCHIVE_RECORD = 66
META_DTYPE = np.dtype([('size', '<u4'), ('archive', '<u4'), ('offset', '<u4'), ('header', '<i4')])


def sections(data, base=0):
    """{tag: (offset from base, size)} of the DAT1 starting at ``base``."""
    if data[base:base + 4] != b'1TAD':
        raise ValueError('Not a DAT1 container')
    count = struct.unpack_from('<H', data, base + 12)[0]
    table = {}
    for i in range(count):
        tag, offset, size = struct.unpack_from('<III', data, base + 16 + 12 * i)
        if offset + size > len(data) - base:
            raise ValueError('DAT1 section %08X outside the container' % tag)
        table[tag] = (offset, size)
    return table


def _array(body, table, tag, dtype, required=True):
    if tag not in table:
        if required:
            raise ValueError('Missing section %08X' % tag)
        return np.zeros(0, dtype)
    offset, size = table[tag]
    dtype = np.dtype(dtype)
    if size % dtype.itemsize:
        raise ValueError('Section %08X size %d is not a multiple of %d' % (tag, size, dtype.itemsize))
    return np.frombuffer(body, dtype, size // dtype.itemsize, offset)


@dataclass
class Toc:
    ids: np.ndarray            # u64 per row
    meta: np.ndarray           # META_DTYPE per row
    headers: bytes             # the 0x654BDED9 blob
    spans: np.ndarray          # (n, 2) u32 first row, count
    archives: list             # archive file names, '/' separated, relative to the game folder
    texture_ids: np.ndarray    # u64
    texture_meta: bytes        # 72 bytes per texture id

    def header(self, row):
        offset = int(self.meta['header'][row])
        if offset < 0:
            return None
        if offset + HEADER_SIZE > len(self.headers):
            raise ValueError('Asset header outside the toc')
        return self.headers[offset:offset + HEADER_SIZE]

    def row_spans(self):
        """Span index of every row."""
        span = np.full(len(self.ids), -1, np.int32)
        for index, (first, count) in enumerate(self.spans.tolist()):
            span[first:first + count] = index
        return span


def read_toc(data):
    if len(data) < 8:
        raise ValueError('Truncated toc')
    magic, size = struct.unpack_from('<II', data, 0)
    if magic != TOC_MAGIC:
        raise ValueError('Not a Rift Apart toc (magic %08X)' % magic)
    body = memoryview(data)[8:].tobytes() if not isinstance(data, bytes) else data[8:]
    if len(body) < size:     # the installed toc carries a few padding bytes after the DAT1
        raise ValueError('toc body is %d bytes, header says %d' % (len(body), size))
    body = body[:size]
    table = sections(body)
    ids = _array(body, table, TAG_IDS, '<u8')
    meta = _array(body, table, TAG_META, META_DTYPE)
    if len(ids) != len(meta):
        raise ValueError('toc id and metadata counts differ')
    spans = _array(body, table, TAG_SPANS, '<u4').reshape(-1, 2)
    if int(spans[:, 1].sum()) != len(ids):
        raise ValueError('toc spans do not cover every row')
    offset, length = table[TAG_ARCHIVES]
    if length % ARCHIVE_RECORD:
        raise ValueError('toc archive table size %d' % length)
    archives = []
    for start in range(offset, offset + length, ARCHIVE_RECORD):
        name = body[start:start + 40].split(b'\0', 1)[0].decode('ascii').replace('\\', '/')
        if not name or name.startswith('/') or ':' in name or any(p in ('', '.', '..') for p in name.split('/')):
            raise ValueError('Unsafe archive name in toc: %r' % name)
        archives.append(name)
    if len(meta) and int(meta['archive'].max()) >= len(archives):
        raise ValueError('toc row names a missing archive index')
    h_off, h_len = table.get(TAG_HEADERS, (0, 0))
    t_off, t_len = table.get(TAG_TEXTURE_META, (0, 0))
    return Toc(ids=ids, meta=meta, headers=body[h_off:h_off + h_len], spans=spans, archives=archives,
               texture_ids=_array(body, table, TAG_TEXTURE_IDS, '<u8', required=False),
               texture_meta=body[t_off:t_off + t_len])


def _crc64_table():
    table = np.zeros(256, np.uint64)
    for i in range(256):
        value = i
        for _ in range(8):
            value = (value >> 1) ^ (0xC96C5795D7870F42 if value & 1 else 0)
        table[i] = value
    return table


_CRC64 = _crc64_table()


def normalized_path(text):
    """The form the game hashes: lower case, '/' separators, no repeated separators."""
    text = text.lower().replace('\\', '/')
    return re.sub('/+', '/', text) if '//' in text else text


def path_crc64_many(paths):
    """levels.zone_format.path_crc64 of every path at once (column-wise over the bytes)."""
    encoded = [normalized_path(p).encode('ascii') for p in paths]
    if not encoded:
        return np.zeros(0, np.uint64)
    width = max(len(e) for e in encoded)
    grid = np.zeros((len(encoded), width), np.uint8)
    lengths = np.fromiter((len(e) for e in encoded), np.int64, len(encoded))
    flat = np.frombuffer(b''.join(encoded), np.uint8)
    rows = np.repeat(np.arange(len(encoded)), lengths)
    cols = np.arange(len(flat)) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    grid[rows, cols] = flat
    value = np.full(len(encoded), 0xC96C5795D7870F42, np.uint64)
    eight, mask = np.uint64(8), np.uint64(0xFF)
    for column in range(width):
        live = lengths > column
        step = (value >> eight) ^ _CRC64[((value ^ grid[:, column].astype(np.uint64)) & mask).astype(np.intp)]
        value = np.where(live, step, value)
    return (value >> np.uint64(2)) | np.uint64(1 << 63)


@dataclass
class Dag:
    paths: list                # asset paths as stored (backslashes), one per entry
    file_ids: np.ndarray       # u64 path CRC-64 in file order (what an index cache stores)
    ids: np.ndarray            # u64 path CRC-64 per entry (computed, ascending)
    types: np.ndarray          # u8
    dep_starts: np.ndarray     # i32
    deps: np.ndarray           # i32, -1 terminated lists

    def dependencies(self, index):
        """Dag entry indices the entry depends on."""
        start = int(self.dep_starts[index])
        if start < 0:
            return []
        end = start
        while end < len(self.deps) and self.deps[end] != -1:
            end += 1
        return [int(i) for i in self.deps[start:end] if 0 <= i < len(self.paths)]


def read_dag(data, ids=None):
    """The dag; ``ids`` (the path CRC-64 of every entry, in file order) skips hashing when cached."""
    if len(data) < 12:
        raise ValueError('Truncated dag')
    magic, size, _zero = struct.unpack_from('<III', data, 0)
    if magic != DAG_MAGIC:
        raise ValueError('Not a Rift Apart dag (magic %08X)' % magic)
    body = data[12:]
    if len(body) != size:
        raise ValueError('dag body is %d bytes, header says %d' % (len(body), size))
    table = sections(body)
    name_offsets = _array(body, table, TAG_NAMES, '<u4')
    types = _array(body, table, TAG_TYPES, 'u1')
    dep_starts = _array(body, table, TAG_DEP_STARTS, '<i4')
    if not len(name_offsets) == len(types) == len(dep_starts):
        raise ValueError('dag section counts differ')
    deps = _array(body, table, TAG_DEPS, '<i4', required=False)
    # Strings sit between the section table and the first section; index them once.
    blob_start = 16 + 12 * len(table)
    blob_end = min(offset for offset, _size in table.values())
    blob = body[blob_start:blob_end]
    starts, offset = {}, 0
    for piece in blob.split(b'\0'):
        starts[blob_start + offset] = piece
        offset += len(piece) + 1
    try:
        paths = [starts[int(o)].decode('ascii') for o in name_offsets]
    except KeyError as error:
        raise ValueError('dag name offset %s is not a string start' % error) from None
    ids = file_ids = path_crc64_many(paths) if ids is None else np.asarray(ids, np.uint64)
    if len(ids) != len(paths):
        raise ValueError('Cached dag ids do not match the dag')
    if len(ids) > 1 and not bool(np.all(ids[1:] > ids[:-1])):
        order = np.argsort(ids, kind='stable')
        ids, types, dep_starts = ids[order], types[order], dep_starts[order]
        remap = np.empty(len(order), np.int64)
        remap[order] = np.arange(len(order))
        paths = [paths[i] for i in order]
        deps = np.where(deps >= 0, remap[np.clip(deps, 0, None)], -1).astype(np.int32)
    return Dag(paths=paths, file_ids=file_ids, ids=ids, types=types, dep_starts=dep_starts, deps=deps)
