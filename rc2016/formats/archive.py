"""layout.csv extraction and bounded ig-tools-compatible outer decompression.

Algorithm reference: doesthisusername/ig-tools, commit b4ebe28a926cf298bff70ad01cdf9e14533c5b94,
general/ig_csvext.py, ig_dec.py and ig_unzip.py. No PKG extraction. The optional `lz4`
package (C) decodes raw LZ4 blocks when installed; the pure-Python decoder below is the
fallback and the reference (byte-identical on the archive corpus, 2026-10-09).
"""
import csv
from collections import OrderedDict
import io
import json
import os
from pathlib import Path
import pickle
import re
import struct
import threading
import zlib
from rc2016.common import asset_path, disk_asset, dump, extended, file_hash, load, read, sha, write
try:
    from lz4.block import decompress as _lz4_c, LZ4BlockError as _LZ4CError
except ImportError:          # Blender's bundled Python, or lz4 not installed
    _lz4_c = None

METADATA = ('layout.csv', 'toc', 'dag', 'chunkmap.txt')
# Parsed layout.csv cache, keyed by the metadata hashes and archive-file availability.
LAYOUT_CACHE_DIR = Path(__file__).resolve().parents[2]/'.cache'/'shared_index'
LAYOUT_CACHE_VERSION = 1
LAYOUT_CACHE_MIN_ENTRIES = 5000
KEEP_OPEN_MIN_BYTES = 16 * 1024 * 1024
# Decompressed assets and their reference lists kept per Archive (viewer/level loads read one
# material, graph or texture header several times). Bounded by total bytes; larger assets bypass it.
ASSET_MEMO_BYTES = 256 * 1024 * 1024
ASSET_MEMO_MAX_ITEM = 32 * 1024 * 1024
# (path, size, mtime_ns) -> sha256 of large files hashed on every open (archive metadata, shared index).
FILE_HASH_SIDECAR = LAYOUT_CACHE_DIR/'file_hashes.json'
FILE_HASH_MIN_BYTES = 1024 * 1024   # smaller files (test fixtures) are simply hashed
# Game folder -> archive root found by locate(); revalidated on use.
LOCATE_SIDECAR = LAYOUT_CACHE_DIR/'located_roots.json'
_SIDECAR_LOCK = threading.Lock()
try:
    _PARSER_SOURCE_SHA256 = sha(b''.join(Path(__file__).with_name(n).read_bytes() for n in ('archive.py', 'common.py')))
except OSError:              # frozen executable without .py sources: the version alone keys the cache
    _PARSER_SOURCE_SHA256 = 'frozen'


def _is_archive_root(path):
    try:
        files = os.listdir(extended(path))
    except OSError:
        return False
    return (all(name in files for name in METADATA)
            and any(re.fullmatch(r'[a-z]\d+s\d+(?:\.[a-z]+)?', f) for f in files))


def _read_sidecar(path):
    try:
        with open(extended(path), encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _update_sidecar(path, key, value, limit=256):
    """Merge one key into a small JSON sidecar (atomic replace; failures only lose the speed-up)."""
    with _SIDECAR_LOCK:
        data = _read_sidecar(path)
        data.pop(key, None); data[key] = value
        while len(data) > limit:
            data.pop(next(iter(data)))
        try:
            os.makedirs(extended(Path(path).parent), exist_ok=True)
            temp = extended(f'{path}.{os.getpid()}.{threading.get_ident()}.tmp')
            with open(temp, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(temp, extended(path))
        except OSError:
            pass


def stat_file_hash(path, sidecar=None):
    """file_hash(path), reused while the file's (size, mtime_ns) stamp is unchanged.

    Same value as file_hash; the stamp approach is Archive._cached_file_matches'. Any stamp
    difference re-hashes the file and refreshes the record.
    """
    sidecar = FILE_HASH_SIDECAR if sidecar is None else sidecar
    stamp = _stat(path)
    if stamp is None or stamp[0] < FILE_HASH_MIN_BYTES:
        return file_hash(path)    # raises like file_hash for a missing file
    key = os.path.normcase(os.path.abspath(path))
    known = _read_sidecar(sidecar).get(key)
    if isinstance(known, list) and len(known) == 3 and known[:2] == stamp and re.fullmatch(r'[0-9a-f]{64}', str(known[2])):
        return known[2]
    digest = file_hash(path)
    if _stat(path) == stamp:      # unchanged while hashing
        _update_sidecar(sidecar, key, [*stamp, digest], limit=4096)
    return digest


def locate(root, sidecar=None):
    """The one archive root under ``root``; the result is remembered and revalidated
    (metadata and a segment file still present) instead of walking the game folder again."""
    sidecar = LOCATE_SIDECAR if sidecar is None else sidecar
    key = os.path.normcase(os.path.abspath(root))
    known = _read_sidecar(sidecar).get(key)
    if isinstance(known, str):
        plain = known[8:] if known.startswith('\\\\?\\UNC\\') else known[4:] if known.startswith('\\\\?\\') else known
        plain = os.path.normcase(('\\\\' + plain) if known.startswith('\\\\?\\UNC\\') else plain)
        inside = plain == key or plain.startswith(key.rstrip(os.sep) + os.sep)
        if inside and _is_archive_root(known):
            return Path(known)   # same value the walk returned (extended form on Windows)
    found = _locate_walk(root)
    _update_sidecar(sidecar, key, str(found))
    return found


def _locate_walk(root):
    found = []
    for folder, dirs, files in os.walk(extended(root)):
        dirs.sort()
        if all(name in files for name in METADATA):
            path = Path(folder)
            if any(re.fullmatch(r'[a-z]\d+s\d+(?:\.[a-z]+)?', f) for f in files):
                found.append(path)
    if len(found) != 1:
        raise ValueError('Expected one archive root containing layout.csv, toc, dag, chunkmap.txt and segments; found: '+repr([str(p) for p in found]))
    return found[0]


def _stat(path):
    """[size, mtime_ns] of a file, or None when it is absent."""
    try:
        s = os.stat(extended(path))
    except OSError:
        return None
    return [s.st_size, s.st_mtime_ns]


def lz4_block(data, size, maximum=2**31):
    """Decode the raw LZ4 block used by both built assets and texture streams.

    Inlined byte reads and slice copies (2026-10-09): byte-identical to the previous per-byte
    closure version on 461 archive assets / 162 MB, ~4x faster on texture streams.
    """
    if not 0 < size <= maximum:
        raise ValueError('Invalid/unsupported declared decompressed size: '+str(size))
    if _lz4_c is not None:
        try:
            out = _lz4_c(data, uncompressed_size=size)
        except _LZ4CError:
            out = None       # e.g. a stream ending on a match: let the reference decoder judge it
        if out is not None and len(out) == size:
            return out
    return _lz4_block_python(data, size)


def _lz4_block_python(data, size):
    end, pos, out = len(data), 0, bytearray()
    while pos < end:
        token = data[pos]; pos += 1
        n = token >> 4
        if n == 15:
            while True:
                if pos >= end:
                    raise ValueError('Truncated LZ4 token')
                v = data[pos]; pos += 1; n += v
                if v != 255:
                    break
        if pos+n > end or len(out)+n > size:
            raise ValueError('Literal exceeds source/destination bounds')
        out += data[pos:pos+n]; pos += n
        if pos == end:
            break
        if pos+2 > end:
            raise ValueError('Truncated LZ4 token')
        distance = data[pos] | data[pos+1] << 8; pos += 2
        n = token & 15
        if n == 15:
            while True:
                if pos >= end:
                    raise ValueError('Truncated LZ4 token')
                v = data[pos]; pos += 1; n += v
                if v != 255:
                    break
        n += 4
        start = len(out) - distance
        if distance == 0 or start < 0 or len(out)+n > size:
            raise ValueError('Invalid LZ4 backreference')
        if n <= distance:
            out += out[start:start+n]
        else:   # overlapping match repeats the last `distance` bytes
            out += (out[start:] * (n//distance + 1))[:n]
    if len(out) != size:
        raise ValueError('Decoded length differs from header')
    return bytes(out)


def decompress(data, maximum=2**31):
    if data.startswith((b'1TAD', b'DDS ', b'Kaydara FBX Binary')):
        return data, 'uncompressed'
    if len(data) < 12:
        raise ValueError('Truncated outer asset header')
    built_size, appended_size = struct.unpack_from('<II', data, 4)
    size = built_size + appended_size
    if not 0 < size <= maximum or size < built_size:
        raise ValueError('Invalid/unsupported declared decompressed size: '+str(size))
    # ig_unzip wrapper (zlib at +12); a header mismatch is not silently accepted.
    if data[12:14] in (b'\x78\x01', b'\x78\x9c', b'\x78\xda'):
        z = zlib.decompressobj()
        out = z.decompress(data[12:], size + 1)
        if len(out) != size or not z.eof or z.unconsumed_tail or z.unused_data:
            raise ValueError('Zlib size/end-of-stream mismatch')
        return out, 'ig-tools-zlib-12'
    if len(data) < 37:
        raise ValueError('Unsupported outer compression header')
    out = lz4_block(data[36:], size, maximum)
    if out[:4] == b'1TAD' and out[4:12] != data[:8]:
        raise ValueError('Outer/inner asset identity mismatch')
    codec = 'ig-tools-lz4-36' if not appended_size else 'ig-tools-lz4-36-built-plus-appended'
    return out, codec


def asset_data(archive, key):
    """Decompressed asset bytes; memoized on a real Archive, read directly on stand-ins."""
    method = getattr(archive, 'asset_data', None)
    return method(key) if method is not None else decompress(archive.bytes(key)[0])[0]


def asset_references(archive, key):
    """index.references of one asset; memoized on a real Archive. Do not mutate the result."""
    method = getattr(archive, 'asset_references', None)
    if method is not None:
        return method(key)
    from rc2016.formats.index import references
    return references(asset_data(archive, key))


class Archive:
    def __init__(self, root, output, log):
        self.root, self.output, self.log = Path(root), Path(output), log
        # bytes() reads and hash-compares every repeated occurrence of an asset. Previews may set
        # this False to read only the first one (most assets repeat across several archive files).
        self.verify_repeats = True
        self.entries, self.warnings = {}, []
        self.metadata = {name: stat_file_hash(self.root/name) for name in METADATA}
        cached = self._cached_layout()
        if cached:
            self.entries, missing = cached
        else:
            missing = self._parse_layout()
        # Identifies the layout-derived entries (metadata + archive availability), so the
        # index can reuse the name of an identical shared assets table without re-hashing it.
        self.layout_identity = sha(repr((self._layout_cache_path().name, sorted(self._available.items()))).encode())
        self.by_path = {}
        for e in self.entries.values():
            self.by_path.setdefault(e['path'], []).append(e['id'])
        self.warnings = ['Missing archive (may be an uninstalled language): '+p for p in sorted(missing)]
        for warning in self.warnings:
            log('warning', message=warning)
        self.map_path = self.output/'reports/source_mapping.json'
        self.mapping = load(self.map_path) if self.map_path.exists() else {}
        self._mapping_dirty = not self.map_path.exists()
        self._handles = threading.local()   # per-thread open handles of large archive files
        self._memo, self._memo_bytes, self._memo_lock = OrderedDict(), 0, threading.Lock()

    def _layout_cache_path(self):
        # The parser's own source (this file and common.asset_path) is part of the key,
        # so editing how layout.csv is read invalidates the cache instead of serving stale rows.
        key = sha(repr((LAYOUT_CACHE_VERSION, sorted(self.metadata.items()), _PARSER_SOURCE_SHA256)).encode())[:24]
        return LAYOUT_CACHE_DIR/f'layout_{key}.pickle'

    def _cached_layout(self):
        """Entries parsed earlier from byte-identical metadata, if archive availability is unchanged."""
        try:
            with open(extended(self._layout_cache_path()), 'rb') as f:
                cached = pickle.load(f)
            if cached.get('version') != LAYOUT_CACHE_VERSION or cached.get('metadata') != self.metadata:
                return None
            for archive, exists in cached['available'].items():
                if os.path.isfile(extended(self.root/archive)) != exists:
                    return None
            self._available = dict(cached['available'])
            return cached['entries'], set(cached['missing'])
        except Exception:      # absent, stale or unreadable cache: parse layout.csv
            return None

    def _parse_layout(self):
        missing = set(); available = {}
        for order, row in enumerate(csv.DictReader(io.StringIO(read(self.root/'layout.csv').decode('utf-8-sig')))):
            path = asset_path(row['Asset Path'])
            archive = row['Archive File']
            if '/' in archive or '\\' in archive or ':' in archive or archive in ('.', '..'):
                raise ValueError('Unsafe archive filename')
            offset, size = int(row['Segment Offset']), int(row['File Size'])
            if min(offset, size) < 0:
                raise ValueError('Negative archive range')
            identity = (path, row['Built Path'], row.get('Language', ''))
            key = sha(repr(identity).encode())[:20]
            entry = self.entries.setdefault(key, dict(id=key, path=path, original_path=row['Asset Path'],
                built_path=row['Built Path'], type=row['Asset Type'].lower(), language=row.get('Language',''),
                order=order, occurrences=[]))
            if archive not in available: available[archive] = os.path.isfile(extended(self.root/archive))
            exists = available[archive]
            if not exists:
                missing.add(archive)
            entry['occurrences'].append(dict(archive=archive, offset=offset, size=size, available=exists,
                install_bucket=row.get('Install Bucket',''),ref_hash=row.get('RefHash',''),ref_count=row.get('RefCount',''),
                always_loaded=row.get('Always Loaded',''),key_asset=row.get('Key Asset','')))
        self._available = available
        if len(self.entries) < LAYOUT_CACHE_MIN_ENTRIES:
            return missing     # test fixtures parse instantly; keep them out of the shared cache
        try:
            write(self._layout_cache_path(), pickle.dumps(dict(version=LAYOUT_CACHE_VERSION, metadata=self.metadata,
                available=available, missing=sorted(missing), entries=self.entries), protocol=pickle.HIGHEST_PROTOCOL))
        except OSError:
            pass               # a read-only project folder only loses the speed-up
        return missing

    def resolve(self, reference):
        return self.by_path.get(asset_path(reference), [])

    def index_loose(self, extracted_root):
        next_order=max((e['order'] for e in self.entries.values()),default=-1)+1
        for folder,dirs,files in os.walk(extended(extracted_root)):
            dirs.sort()
            for filename in sorted(files):
                if Path(filename).suffix.lower() not in ('.fbx','.dds'):continue
                path=Path(folder)/filename
                relative=os.path.relpath(str(path),extended(extracted_root)).replace('\\','/')
                canonical=asset_path(relative);key=sha(('loose:'+canonical).encode())[:20]
                self.entries[key]=dict(id=key,path=canonical,original_path=relative,type=path.suffix[1:].lower(),
                    built_path='',language='',order=next_order,occurrences=[dict(available=True,loose_path=str(path))],loose_path=str(path))
                next_order+=1
                self.by_path.setdefault(canonical,[]).append(key)
                self.layout_identity=sha((self.layout_identity+'|'+key+'|'+relative+'|'+str(self.entries[key]['order'])).encode())

    def bytes(self, key):
        e = self.entries[key]
        if 'loose_path' in e:
            data=read(e['loose_path']);return data,sha(data)
        occurrences = [r for r in e['occurrences'] if r['available']]
        if not occurrences:
            raise FileNotFoundError('No available archive occurrence: '+e['path'])
        if not self.verify_repeats:
            occurrences = occurrences[:1]
        hashes, first = [], None
        for r in occurrences:
            data = self._read_range(r['archive'], r['offset'], r['size'], e['path'])
            h = sha(data); hashes.append(h)
            if first is None:
                first = data
        if len(set(hashes)) != 1:
            raise ValueError('Conflicting repeated asset occurrences: '+e['path'])
        return first, hashes[0]

    def _memo_get(self, name, key, build, size):
        """Thread-safe byte-bounded LRU of derived per-entry values; ``build`` runs unlocked."""
        with self._memo_lock:
            hit = self._memo.get((name, key))
            if hit is not None:
                self._memo.move_to_end((name, key))
                return hit[0]
        value = build()
        cost = size(value)
        if cost > ASSET_MEMO_MAX_ITEM:
            return value
        with self._memo_lock:
            hit = self._memo.get((name, key))
            if hit is not None:   # another thread built it first: keep one shared object
                return hit[0]
            self._memo[(name, key)] = (value, cost)
            self._memo_bytes += cost
            while self._memo_bytes > ASSET_MEMO_BYTES and self._memo:
                _k, (_v, dropped) = self._memo.popitem(last=False)
                self._memo_bytes -= dropped
        return value

    def asset_data(self, key):
        """decompress(bytes(key))[0], memoized (callers must not mutate it; bytes are immutable)."""
        return self._memo_get('data', key, lambda: decompress(self.bytes(key)[0])[0], len)

    def asset_references(self, key):
        """index.references(asset_data(key)), memoized. Shared result: do not mutate."""
        from rc2016.formats.index import references
        return self._memo_get('refs', key, lambda: references(self.asset_data(key)),
                              lambda refs: 64 + 160 * len(refs))

    def _read_range(self, archive, offset, size, label):
        """Bytes [offset, offset+size) of one archive file.

        Large archive files stay open per thread (opening one per asset cost ~0.3 s
        over the actor index); small files, such as test fixtures, are opened and
        closed each time so their folders can still be deleted.
        """
        handles = getattr(self._handles, 'files', None)
        if handles is None:
            handles = self._handles.files = {}
        held = handles.get(archive)
        if held is None:
            f = open(extended(self.root/archive), 'rb')
            length = os.fstat(f.fileno()).st_size
            if length >= KEEP_OPEN_MIN_BYTES:
                held = handles[archive] = (f, length)
            else:
                with f:
                    if offset+size > length:
                        raise ValueError('Archive entry outside file: '+label)
                    f.seek(offset); return f.read(size)
        f, length = held
        if offset+size > length:
            raise ValueError('Archive entry outside file: '+label)
        f.seek(offset); return f.read(size)

    def close(self):
        """Close this thread's held archive handles (others close when their thread ends)."""
        for f, _length in (getattr(self._handles, 'files', None) or {}).values():
            f.close()
        self._handles.files = {}

    def decode(self, key):
        e = self.entries[key]; data, source_hash = self.bytes(key)
        # Asset IDs already bind the full canonical path/build/language tuple.
        # Keep cache files flat under that unique ID so deep game paths do not
        # exceed Windows' legacy path handling in model/animation readers.
        stem = Path(key)/disk_asset(Path(e['path']).name)
        compressed = self.output/'compressed_assets'/stem
        decoded = self.output/'decoded_sources'/stem
        previous = self.mapping.get(key, {})
        if previous.get('source_sha256') == source_hash and self._cached_file_matches(previous, 'decoded', decoded, previous.get('decoded_sha256')):
            if not self._cached_file_matches(previous, 'compressed', compressed, source_hash):
                write(compressed, data); previous['compressed_stat'] = _stat(compressed); self._mapping_dirty = True
            return decoded
        write(compressed, data)
        out, codec = (data,'source-fbx') if e['type']=='fbx' else decompress(data)
        write(decoded, out)
        self.mapping[key] = dict(path=e['path'], original_path=e['original_path'], source_sha256=source_hash,
            decoded_sha256=sha(out), compressed=str(compressed.relative_to(self.output)), decoded=str(decoded.relative_to(self.output)),
            codec=codec, occurrences=e['occurrences'], decoded_stat=_stat(decoded), compressed_stat=_stat(compressed))
        self._mapping_dirty = True
        self.log('decoded', asset=e['path'], bytes=len(out), codec=codec)
        return decoded

    def _cached_file_matches(self, record, role, path, expected_sha256):
        """True when a cache file still holds `expected_sha256`.

        A size + mtime_ns stamp recorded when the hash was last verified skips re-reading
        unchanged files (re-hashing every cached actor dominated the archive scan); any
        stamp difference falls back to the full hash, which then refreshes the stamp.
        """
        stamp = _stat(path)
        if stamp is None:
            return False
        if record.get(role+'_stat') == stamp:
            return True
        if file_hash(path) != expected_sha256:
            return False
        record[role+'_stat'] = stamp
        self._mapping_dirty = True
        return True

    def checkpoint(self):
        # Rewriting the multi-MB mapping when nothing changed cost ~0.3 s per scan.
        if self._mapping_dirty or not self.map_path.exists():
            dump(self.map_path, self.mapping)
            self._mapping_dirty = False
