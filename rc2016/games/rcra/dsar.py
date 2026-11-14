"""DSAR block archives (the files in Rift Apart's ``d/`` folder).

Header (32 bytes): b'DSAR', u32 version 0x10003, u32 block count, u32 first data byte,
u64 total decompressed size, u64 unused. Then one 32-byte record per block: u64 decompressed
offset, u64 compressed offset, u32 decompressed size, u32 compressed size, u8 mode, 7 unused bytes.
Mode 3 is an LZ4 block and mode 2 is DirectStorage GDeflate (both checked on the installed files).
toc offsets address the concatenated decompressed stream. Files without the magic (``movie*``,
``soundbank``, ``wem*``) are read as they are.
"""
import os
import threading
from collections import OrderedDict

import numpy as np

from rc2016.common import extended

MAGIC = b'DSAR'
BLOCK_DTYPE = np.dtype([('dofs', '<u8'), ('cofs', '<u8'), ('dsize', '<u4'), ('csize', '<u4'), ('mode', 'u1'),
                        ('unused', 'V7')])
MODE_GDEFLATE, MODE_LZ4 = 2, 3
BLOCK_CACHE_BYTES = 256 * 1024 * 1024

try:
    from lz4.block import decompress as _lz4
except ImportError:          # the 2016 reader's pure-Python LZ4 is the fallback
    _lz4 = None


def lz4_block(data, size):
    if _lz4 is not None:
        return _lz4(data, uncompressed_size=size)
    from rc2016.formats.archive import lz4_block as fallback
    return fallback(data, size)


class ArchiveFile:
    """Block table of one archive file, read once."""

    def __init__(self, path):
        self.path = path
        with open(extended(path), 'rb') as f:
            head = f.read(32)
            self.length = os.fstat(f.fileno()).st_size
            self.compressed = head[:4] == MAGIC
            if not self.compressed:
                self.blocks, self.size = np.zeros(0, BLOCK_DTYPE), self.length
                return
            if len(head) < 32:
                raise ValueError('Truncated DSAR header: %s' % path)
            count, _data_start = np.frombuffer(head, '<u4', 2, 8)
            self.size = int(np.frombuffer(head, '<u8', 1, 16)[0])
            table = f.read(32 * int(count))
        if len(table) != 32 * int(count):
            raise ValueError('Truncated DSAR block table: %s' % path)
        self.blocks = np.frombuffer(table, BLOCK_DTYPE).copy()
        b = self.blocks
        ends = b['dofs'] + b['dsize'].astype(np.uint64)
        if len(b) and (int(b['dofs'][0]) != 0 or bool(np.any(b['dofs'][1:] != ends[:-1]))):
            raise ValueError('DSAR blocks are not contiguous: %s' % path)
        if len(b) and int(ends[-1]) != self.size:
            raise ValueError('DSAR blocks cover %d bytes, header says %d: %s' % (int(ends[-1]), self.size, path))
        if bool(np.any(b['cofs'] + b['csize'].astype(np.uint64) > self.length)):
            raise ValueError('DSAR block outside the file: %s' % path)
        unknown = set(np.unique(b['mode']).tolist()) - {MODE_GDEFLATE, MODE_LZ4}
        if unknown:
            raise ValueError('Unknown DSAR block mode(s) %s: %s' % (sorted(unknown), path))

    def block_range(self, offset, size):
        """Indices [first, last) of the blocks covering [offset, offset+size)."""
        first = int(np.searchsorted(self.blocks['dofs'], offset, 'right')) - 1
        last = int(np.searchsorted(self.blocks['dofs'], offset + size, 'left'))
        return max(first, 0), max(last, first + 1)


class BlockReader:
    """Range reads over a set of archive files, with thread-local handles and a shared,
    byte-bounded LRU of decompressed blocks (neighbouring assets share blocks)."""

    def __init__(self, gdeflate=None, cache_bytes=BLOCK_CACHE_BYTES):
        self.gdeflate = gdeflate          # a GDeflate, or an exception explaining why there is none
        self.cache_bytes = cache_bytes
        self._files, self._files_lock = {}, threading.Lock()
        self._local = threading.local()
        self._cache, self._cached, self._cache_lock = OrderedDict(), 0, threading.Lock()

    def archive(self, path):
        with self._files_lock:
            found = self._files.get(path)
        if found is None:
            found = ArchiveFile(path)
            with self._files_lock:
                found = self._files.setdefault(path, found)
        return found

    def _handle(self, path):
        handles = getattr(self._local, 'files', None)
        if handles is None:
            handles = self._local.files = {}
        f = handles.get(path)
        if f is None:
            f = handles[path] = open(extended(path), 'rb')
        return f

    def _raw(self, path, offset, size):
        f = self._handle(path)
        f.seek(offset)
        data = f.read(size)
        if len(data) != size:
            raise ValueError('Short read at %d+%d in %s' % (offset, size, path))
        return data

    def _block(self, archive, index):
        key = (archive.path, index)
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        record = archive.blocks[index]
        dsize, mode = int(record['dsize']), int(record['mode'])
        packed = self._raw(archive.path, int(record['cofs']), int(record['csize']))
        if mode == MODE_LZ4:
            data = lz4_block(packed, dsize)
        elif isinstance(self.gdeflate, Exception) or self.gdeflate is None:
            raise ValueError('This asset is GDeflate-compressed: %s' % (self.gdeflate or 'no GDeflate codec'))
        else:
            data = self.gdeflate.decompress(packed, dsize)
        if len(data) != dsize:
            raise ValueError('Block %d of %s gave %d bytes, expected %d' % (index, archive.path, len(data), dsize))
        if dsize <= self.cache_bytes // 4:
            with self._cache_lock:
                if key not in self._cache:
                    self._cache[key] = data
                    self._cached += dsize
                    while self._cached > self.cache_bytes and self._cache:
                        _k, dropped = self._cache.popitem(last=False)
                        self._cached -= len(dropped)
        return data

    def read(self, path, offset, size):
        archive = self.archive(path)
        if offset < 0 or size < 0 or offset + size > archive.size:
            raise ValueError('Range %d+%d outside %s (%d bytes)' % (offset, size, path, archive.size))
        if not archive.compressed:
            return self._raw(path, offset, size)
        first, last = archive.block_range(offset, size)
        parts = []
        for index in range(first, last):
            start = int(archive.blocks['dofs'][index])
            block = self._block(archive, index)
            lo = max(offset - start, 0)
            hi = min(offset + size - start, len(block))
            parts.append(block[lo:hi])
        data = b''.join(parts)
        if len(data) != size:
            raise ValueError('Read %d bytes, expected %d at %d in %s' % (len(data), size, offset, path))
        return data

    def close(self):
        for f in (getattr(self._local, 'files', None) or {}).values():
            f.close()
        self._local.files = {}
        with self._cache_lock:
            self._cache.clear()
            self._cached = 0
