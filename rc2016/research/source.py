"""Streaming sources using the existing archive reader and DAT1 section parser."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import struct
from unittest.mock import patch

from rc2016.common import sha
from rc2016.formats import archive as archive_module
from rc2016.levels.zone_format import sections, DATA_HEAP, INSTANCE_OFFSETS

DEFAULT_EXTENSIONS = ('.level', '.zone', '.zone_static')
STRIDES = (4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 64, 80, 88, 96, 112, 128, 176, 256, 512)
DECODED = {
    '.zone': {0xC6A5905E: 'model paths', 0x6987F172: 'instance offsets',
              0x06ABCAB2: 'partially decoded: instance heap', 0xBCF43558: 'decal offsets'},
    '.level': {0x2BA33702: 'zone paths', 0x4130D903: 'region paths', 0x2236C47A: 'spawn names'},
    '.zone_static': {0x657512BB: 'decal mesh records', 0xCF30405E: 'decal buffers'},
}
PRIORITY = {DATA_HEAP, 0x032D450A, 0x3395AEC1}
PRINTABLE = re.compile(rb'[\x20-\x7e]{1,}\x00')


def namespace(path):
    return path.replace('\\', '/').split('/')[0]


def level_name(path):
    parts = path.replace('\\', '/').split('/')
    if 'levels' in parts:
        index = parts.index('levels')
        if index + 1 < len(parts) - 1:
            return '/'.join(parts[:index] + [parts[index + 1]])
    return '/'.join(parts[:-1])


def tag_name(tag):
    return f'0x{tag:08X}'


@dataclass
class Asset:
    path: str
    data: bytes

    def __post_init__(self):
        self.extension = Path(self.path).suffix.lower()
        # Reuse the decoder, then add bounds checks for research inputs.
        if len(self.data) < 16:
            raise ValueError('Truncated DAT1 header')
        count = struct.unpack_from('<I', self.data, 12)[0]
        if 16 + 12 * count > len(self.data):
            raise ValueError('Truncated DAT1 section table')
        self.table = sections(self.data)
        if len(self.table) != count:
            raise ValueError('Duplicate DAT1 section tag')
        for tag, (off, size) in self.table.items():
            if off < 16 + 12 * count or off + size > len(self.data):
                raise ValueError(f'{tag_name(tag)} outside DAT1 bounds')
        self.digest = sha(self.data)
        # Require string starts, not accidental printable suffixes of binary words.
        self.strings = {m.start(): m.group()[:-1].decode('ascii')
                        for m in PRINTABLE.finditer(self.data)
                        if m.start() == 0 or self.data[m.start() - 1] == 0}

    def chunk(self, tag):
        off, size = self.table[tag]
        return self.data[off:off + size]

    def words(self, tag):
        raw = self.chunk(tag)
        return [x[0] for x in struct.iter_unpack('<I', raw[:len(raw) // 4 * 4])]

    def counts(self):
        result = {(tag, 4): size // 4 for tag, (_, size) in self.table.items() if size % 4 == 0}
        if 0x657512BB in self.table and self.table[0x657512BB][1] % 88 == 0:
            result[(0x657512BB, 88)] = self.table[0x657512BB][1] // 88
        return result

    def instance_records(self):
        if hasattr(self, '_instance_records'):
            return self._instance_records
        if not {DATA_HEAP, INSTANCE_OFFSETS} <= self.table.keys():
            return []
        base, heap_size = self.table[DATA_HEAP]
        rows = []
        for rel in self.words(INSTANCE_OFFSETS):
            if rel + 0xB0 > heap_size:
                raise ValueError(f'Instance header outside heap at {rel:#x}')
            size = struct.unpack_from('<I', self.data, base + rel + 0x44)[0]
            if size < 0xB0 or rel + size > heap_size + 7:
                raise ValueError(f'Instance size outside heap at {rel:#x}')
            rows.append((base + rel, min(size, heap_size - rel)))
        self._instance_records = rows
        return rows


class Corpus:
    """Re-iterable stream. Cache and all optional archive writes stay under --out."""
    def __init__(self, root, out, extensions=DEFAULT_EXTENSIONS, level=None, limit=None, loose=False):
        self.root, self.out = Path(root).resolve(), Path(out).resolve()
        self.extensions = tuple(sorted(set((e if e.startswith('.') else '.' + e).lower() for e in extensions)))
        self.level, self.limit, self.loose = level, limit, loose
        if limit is not None and limit < 1:
            raise ValueError('--limit must be positive')
        self.errors = {}
        self.archive = None
        if loose:
            self.entries = [(p.relative_to(self.root).as_posix(), p) for p in sorted(self.root.rglob('*'))
                            if p.is_file() and p.suffix.lower() in self.extensions]
            self.paths = [p for p, _ in self.entries]
        else:
            (self.out / '.cache').mkdir(parents=True, exist_ok=True)
            with patch.object(archive_module, 'LAYOUT_CACHE_DIR', self.out / '.cache' / 'index'):
                self.archive = archive_module.Archive(archive_module.locate(self.root),
                                                     self.out / '.cache' / 'archive', lambda *a, **k: None)
            # Archive.bytes verifies repeated occurrences; decompression remains in memory.
            entries = sorted(self.archive.entries.values(), key=lambda e: (e['path'], e['id']))
            self.paths = sorted(set(e['path'] for e in entries))
            self.entries = [(e['path'], e['id']) for e in entries
                            if Path(e['path']).suffix in self.extensions]
        if level:
            selector = level.replace('\\', '/').lower().strip('/')
            if selector.startswith('levels/'):
                parts = selector.split('/')
                selector = parts[1] if len(parts) > 1 else selector
            self.entries = [(p, key) for p, key in self.entries if level_name(p).lower() == selector]
        if limit is not None:
            self.entries = self.entries[:limit]

    def __iter__(self):
        for name, key in self.entries:
            try:
                if self.loose:
                    data = key.read_bytes()
                else:
                    raw, _ = self.archive.bytes(key)
                    data, _ = archive_module.decompress(raw)
                yield Asset(name, data)
            except Exception as exc:
                self.errors[name] = f'{type(exc).__name__}: {exc}'

    def close(self):
        if self.archive:
            self.archive.close()

    @contextmanager
    def opened(self):
        try:
            yield self
        finally:
            self.close()
