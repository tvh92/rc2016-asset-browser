"""RcraSource: a Rift Apart install read through the same surface the app uses on formats.archive.Archive.

Entries are keyed by the asset id as 16 lower-case hex digits. Every toc row with that id is an
``occurrence``: language copies (rows in a locale span or a ``.xx`` archive) and, for streamed
textures, the headerless HD stream next to the headered ``.texture``. Asset paths come from the
game's own dag; ids the dag does not name (Wwise audio, movies, some UI files) get the path
``_unnamed/<archive>/<id>``. Nothing is ever written to the game folder.
"""
import pickle
import re
import threading
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from rc2016.common import asset_path, disk_asset, extended, read, sha, write
from rc2016.games.rcra.dsar import BlockReader
from rc2016.games.rcra.gdeflate import GDeflate, GDeflateUnavailable
from rc2016.games.rcra.toc import DAG_MAGIC, TOC_MAGIC, read_dag, read_toc

GAME_ID = 'rcra'
# Parsed dag hashes and id names, keyed by the toc/dag hashes and this reader's own source.
INDEX_CACHE_DIR = Path(__file__).resolve().parents[3] / '.cache' / 'rcra'
INDEX_CACHE_VERSION = 1
try:
    _PARSER_SOURCE_SHA256 = sha(b''.join(Path(__file__).with_name(n).read_bytes() for n in ('toc.py', 'source.py')))
except OSError:              # frozen executable without .py sources
    _PARSER_SOURCE_SHA256 = 'frozen'
LOCALE = re.compile(r'\.([a-z]{2})$')
BASE_LANGUAGES = ('', 'us')       # preferred copy of a language-variant asset


def is_game_root(folder):
    """True for a Rift Apart install folder (toc with the RCRA magic, dag and d/)."""
    folder = Path(folder)
    try:
        with open(extended(folder / 'toc'), 'rb') as f:
            toc_magic = int.from_bytes(f.read(4), 'little')
        with open(extended(folder / 'dag'), 'rb') as f:
            dag_magic = int.from_bytes(f.read(4), 'little')
    except OSError:
        return False
    return toc_magic == TOC_MAGIC and dag_magic == DAG_MAGIC and (folder / 'd').is_dir()


def locate(path):
    """The install folder for ``path``: the folder itself, its ``d`` subfolder's parent, or a file's folder."""
    path = Path(path).expanduser().resolve()
    for candidate in (path, path.parent, path.parent.parent):
        if is_game_root(candidate):
            return candidate
    raise ValueError('Not a Rift Apart install (no toc, dag and d/ found): %s' % path)


class _Entries(Mapping):
    """Read-only {key: entry dict}; an entry dict is built only when asked for."""

    def __init__(self, source):
        self._s = source

    def __getitem__(self, key):
        index = self._s._index(key)
        if index is None:
            raise KeyError(key)
        return self._s._entry(index)

    def get(self, key, default=None):
        index = self._s._index(key)
        return default if index is None else self._s._entry(index)

    def __contains__(self, key):
        return self._s._index(key) is not None

    def __iter__(self):
        return iter(self._s.keys)

    def __len__(self):
        return len(self._s.keys)


class RcraSource:
    game_id = GAME_ID

    def __init__(self, root, output, log=None, cache_dir=None):
        self.root, self.output = locate(root), Path(output)
        self.log = log or (lambda *_a, **_k: None)
        self.verify_repeats = False        # rows are distinct (variants), never byte-identical repeats
        from rc2016.formats.archive import stat_file_hash
        self.metadata = {name: stat_file_hash(self.root / name) for name in ('toc', 'dag')}
        self.toc = read_toc(read(self.root / 'toc'))
        self.cache_dir = Path(cache_dir) if cache_dir else INDEX_CACHE_DIR
        cached = self._cached_index()
        self.dag = read_dag(read(self.root / 'dag'), ids=cached['dag_ids'] if cached else None)
        self._group_rows()
        if cached and len(cached['paths']) == len(self.uids):
            self.dag_index, self.paths, self.types = cached['dag_index'], cached['paths'], cached['types']
            self._types = np.array(self.types, dtype=object)
        else:
            self._name_ids()
            self._store_index()
        self.layout_identity = sha(repr((GAME_ID, sorted(self.metadata.items()))).encode())
        self.warnings = self._check_files()
        for warning in self.warnings:
            self.log('warning', message=warning)
        try:
            gdeflate = GDeflate(self.root)
        except GDeflateUnavailable as error:
            gdeflate = error
            self.warnings.append(str(error))
        self.reader = BlockReader(gdeflate)
        self.entries = _Entries(self)
        self._by_path, self._by_path_lock = None, threading.Lock()

    # -- index construction -------------------------------------------------------------------

    def _index_cache_path(self):
        key = sha(repr((INDEX_CACHE_VERSION, sorted(self.metadata.items()), _PARSER_SOURCE_SHA256)).encode())[:24]
        return self.cache_dir / f'index_{key}.pickle'

    def _cached_index(self):
        """dag path hashes and per-id names/types computed earlier from byte-identical toc and dag."""
        try:
            with open(extended(self._index_cache_path()), 'rb') as f:
                cached = pickle.load(f)
            if cached.get('version') != INDEX_CACHE_VERSION or cached.get('metadata') != self.metadata:
                return None
            return cached
        except Exception:      # absent, stale or unreadable: compute again
            return None

    def _store_index(self):
        try:
            write(self._index_cache_path(), pickle.dumps(dict(
                version=INDEX_CACHE_VERSION, metadata=self.metadata, dag_ids=self.dag.file_ids,
                dag_index=self.dag_index, paths=self.paths, types=self.types), protocol=pickle.HIGHEST_PROTOCOL))
        except OSError:
            pass               # a read-only project folder only loses the speed-up

    def _group_rows(self):
        ids = self.toc.ids
        self.row_order = np.argsort(ids, kind='stable')
        self.uids, self.row_first, self.row_count = np.unique(ids[self.row_order], return_index=True,
                                                               return_counts=True)
        self.keys = ['%016x' % v for v in self.uids.tolist()]
        # Language of each row: the archive's ".xx" suffix, else the locale its span holds.
        archive_language = [m.group(1) if (m := LOCALE.search(name)) else '' for name in self.toc.archives]
        row_archive_language = np.array(archive_language, dtype=object)[self.toc.meta['archive']]
        span = self.toc.row_spans()
        span_language = {}
        for index, (first, count) in enumerate(self.toc.spans.tolist()):
            found = {row_archive_language[r] for r in range(first, first + count)} - {''}
            span_language[index] = found.pop() if len(found) == 1 else ''
        self.row_language = [row_archive_language[r] or span_language.get(int(span[r]), '')
                             for r in range(len(self.toc.ids))]

    def _name_ids(self):
        position = np.searchsorted(self.dag.ids, self.uids)
        position = np.clip(position, 0, max(len(self.dag.ids) - 1, 0))
        named = (self.dag.ids[position] == self.uids) if len(self.dag.ids) else np.zeros(len(self.uids), bool)
        self.dag_index = np.where(named, position, -1)
        self.paths, self.types = [], []
        for i, index in enumerate(self.dag_index.tolist()):
            if index >= 0:
                path = asset_path(self.dag.paths[index])
                kind = path.rsplit('.', 1)[-1] if '.' in path.rsplit('/', 1)[-1] else ''
            else:
                row = int(self.row_order[self.row_first[i]])
                archive = self.toc.archives[int(self.toc.meta['archive'][row])].replace('/', '_')
                path, kind = '_unnamed/%s/%s' % (archive, self.keys[i]), 'unnamed'
            self.paths.append(path)
            self.types.append(kind)
        self._types = np.array(self.types, dtype=object)

    def _check_files(self):
        warnings = []
        used = np.unique(self.toc.meta['archive']).tolist()
        self.available = {}
        for index in used:
            name = self.toc.archives[index]
            exists = (self.root / name).is_file()
            self.available[index] = exists
            if not exists and not LOCALE.search(name):
                warnings.append('Missing archive (may be an uninstalled language): ' + name)
        if (self.root / 'toc.BAK').exists() or (self.root / 'toc.bak').exists():
            warnings.append('toc.BAK is present: this install may be modded; assets are read as the current toc lists them')
        return warnings

    # -- lookups --------------------------------------------------------------------------------

    def _index(self, key):
        try:
            value = np.uint64(int(key, 16))
        except (TypeError, ValueError, OverflowError):
            return None
        i = int(np.searchsorted(self.uids, value))
        return i if i < len(self.uids) and self.uids[i] == value else None

    def _rows(self, index):
        first = int(self.row_first[index])
        return [int(r) for r in self.row_order[first:first + int(self.row_count[index])]]

    def _occurrence(self, row):
        meta = self.toc.meta[row]
        archive = int(meta['archive'])
        return dict(row=row, archive=self.toc.archives[archive], offset=int(meta['offset']), size=int(meta['size']),
                    header=int(meta['header']) >= 0, language=self.row_language[row],
                    available=self.available.get(archive, False))

    def _entry(self, index):
        path = self.paths[index]
        occurrences = [self._occurrence(r) for r in self._rows(index)]
        primary = self._primary(occurrences)
        return dict(id=self.keys[index], path=path, original_path=self.dag.paths[self.dag_index[index]]
                    if self.dag_index[index] >= 0 else path, built_path='', type=self.types[index],
                    language=primary['language'] if primary else '', order=index, occurrences=occurrences,
                    asset_id=int(self.uids[index]), streamed=self._stream(occurrences) is not None)

    @staticmethod
    def _primary(occurrences):
        """The copy an asset read returns: a headered row first (a texture's own .texture), then the base language."""
        usable = [o for o in occurrences if o['available']] or occurrences
        if not usable:
            return None
        return min(usable, key=lambda o: (not o['header'], o['language'] not in BASE_LANGUAGES,
                                          BASE_LANGUAGES.index(o['language']) if o['language'] in BASE_LANGUAGES else 0,
                                          o['row']))

    @staticmethod
    def _stream(occurrences):
        """A streamed texture's headerless HD row (same language as its headered row)."""
        if not any(o['header'] for o in occurrences):
            return None
        primary = RcraSource._primary(occurrences)
        streams = [o for o in occurrences if not o['header'] and o['available'] and o['language'] == primary['language']]
        return streams[0] if streams else None

    @property
    def by_path(self):
        with self._by_path_lock:
            if self._by_path is None:
                by_path = {}
                for key, path in zip(self.keys, self.paths):
                    by_path.setdefault(path, []).append(key)
                self._by_path = by_path
            return self._by_path

    def resolve(self, reference):
        try:
            return list(self.by_path.get(asset_path(reference), []))
        except ValueError:
            return []

    def ids_of_type(self, kind):
        """Keys of every asset of one type ('model', 'texture', ...), in path order."""
        picked = np.nonzero(self._types == kind)[0]
        return [self.keys[i] for i in sorted(picked.tolist(), key=self.paths.__getitem__)]

    def type_counts(self):
        kinds, counts = np.unique(self._types.astype(str), return_counts=True)
        return dict(zip(kinds.tolist(), counts.tolist()))

    # -- reading --------------------------------------------------------------------------------

    def _read_occurrence(self, occurrence):
        if not occurrence['available']:
            raise FileNotFoundError('Archive not installed: ' + occurrence['archive'])
        data = self.reader.read(self.root / occurrence['archive'], occurrence['offset'], occurrence['size'])
        header = self.toc.header(occurrence['row']) if occurrence['header'] else None
        return header + data if header else data

    def bytes(self, key, language=None):
        """(asset bytes, sha256): the 36-byte toc header (when the asset has one) plus the archive bytes."""
        index = self._index(key)
        if index is None:
            raise KeyError(key)
        occurrences = [self._occurrence(r) for r in self._rows(index)]
        if language is not None:
            occurrences = [o for o in occurrences if o['language'] == language and (o['header'] or
                           not any(p['header'] for p in occurrences))] or occurrences
        primary = self._primary(occurrences)
        data = self._read_occurrence(primary)
        return data, sha(data)

    def texture_stream(self, key):
        """The HD mip stream of a streamed texture, or None."""
        index = self._index(key)
        if index is None:
            raise KeyError(key)
        stream = self._stream([self._occurrence(r) for r in self._rows(index)])
        return None if stream is None else self._read_occurrence(stream)

    def asset_data(self, key):
        return self.bytes(key)[0]

    def asset_references(self, key):
        """The asset's dag dependencies, in the index.references record shape."""
        index = self._index(key)
        if index is None:
            raise KeyError(key)
        dag_index = int(self.dag_index[index])
        if dag_index < 0:
            return []
        return [dict(path=asset_path(self.dag.paths[d]), original_reference=self.dag.paths[d], evidence='dag dependency')
                for d in self.dag.dependencies(dag_index)]

    def decode(self, key):
        """Write the asset bytes under output/decoded_sources/<key>/<name> and return that path."""
        entry = self.entries[key]
        data, digest = self.bytes(key)
        target = self.output / 'decoded_sources' / key / disk_asset(Path(entry['path']).name)
        try:
            if sha(read(target)) == digest:
                return target
        except OSError:
            pass
        write(target, data)
        self.log('decoded', asset=entry['path'], bytes=len(data), codec='rcra-dsar')
        return target

    def checkpoint(self):
        pass

    def close(self):
        self.reader.close()
        if not isinstance(self.reader.gdeflate, Exception) and self.reader.gdeflate is not None:
            self.reader.gdeflate.close()
