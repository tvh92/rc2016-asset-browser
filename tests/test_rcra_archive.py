"""Rift Apart archive reader: synthetic toc/dag/DSAR fixtures, plus real-install checks.

The real-install tests read RCRA_GAME (the Rift Apart install folder) and skip without it.
No game bytes are embedded here.
"""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rc2016.games.rcra import toc as toc_module
from rc2016.games.rcra.dsar import BLOCK_DTYPE, MODE_GDEFLATE, MODE_LZ4, ArchiveFile, BlockReader
from rc2016.games.rcra.source import RcraSource, is_game_root, locate
from rc2016.levels.zone_format import path_crc64

try:
    import lz4.block as lz4block
except ImportError:
    lz4block = None

GAME = os.environ.get('RCRA_GAME')


def dat1(kind, sections, strings=b''):
    """A DAT1 with the given {tag: bytes} sections placed after an optional string blob."""
    header = 16 + 12 * len(sections)
    offset = header + len(strings)
    table, body = b'', b''
    for tag, data in sections.items():
        table += struct.pack('<III', tag, offset + len(body), len(data))
        body += data
    total = header + len(strings) + len(body)
    return struct.pack('<4sIIHH', b'1TAD', kind, total, len(sections), 0) + table + strings + body


def dsar(payload, block=64, modes=None, compress=True):
    """A DSAR archive of ``payload`` in ``block``-byte blocks (LZ4, or stored bytes tagged with a mode)."""
    pieces = [payload[i:i + block] for i in range(0, len(payload), block)]
    packed = [lz4block.compress(p, store_size=False) if compress else p for p in pieces]
    start = 32 + 32 * len(pieces)
    records, data, cursor, dofs = b'', b'', start, 0
    for i, (piece, comp) in enumerate(zip(pieces, packed)):
        mode = (modes or {}).get(i, MODE_LZ4)
        records += struct.pack('<QQIIB7x', dofs, cursor, len(piece), len(comp), mode)
        data += comp
        cursor += len(comp)
        dofs += len(piece)
    return struct.pack('<4sIIIQQ', b'DSAR', 0x10003, len(pieces), start, len(payload), 0) + records + data


def archive_record(name):
    return name.encode('ascii').ljust(40, b'\0') + bytes(26)


class Fixture:
    """A tiny install: models in a DSAR archive, a texture with an HD stream, a language copy,
    an unnamed raw audio row and a locale archive that is not installed."""

    MODEL = 'characters\\hero\\hero_test\\hero_test.model'
    TEXTURE = 'characters\\hero\\hero_test\\textures\\hero_test_c.texture'
    MATERIAL = 'materials\\hero_test.material'
    LOCAL = 'localization\\strings.localization'

    def __init__(self, folder):
        self.folder = Path(folder)
        (self.folder / 'd').mkdir()
        self.model = b'1TAD' + bytes(range(200)) * 2
        self.texture_sd = b'1TAD' + b'S' * 100
        self.texture_hd = bytes(range(256)) * 3
        self.local_base, self.local_fr = b'base text', b'texte fr'
        self.audio = b'RIFF' + b'w' * 60
        self.header_model, self.header_texture = b'M' * 36, b'T' * 36
        stream = self.model + self.texture_sd + self.texture_hd + self.local_base + self.local_fr
        (self.folder / 'd' / 'main').write_bytes(dsar(stream))
        (self.folder / 'd' / 'wem_0').write_bytes(self.audio)
        ids = {p: path_crc64(p) for p in (self.MODEL, self.TEXTURE, self.MATERIAL, self.LOCAL)}
        self.ids = ids
        self.audio_id = 0x8123456789ABCDEF
        o_model, o_sd = 0, len(self.model)
        o_hd = o_sd + len(self.texture_sd)
        o_lb = o_hd + len(self.texture_hd)
        o_lf = o_lb + len(self.local_base)
        # span 0: base rows (sorted ids); span 1: the French copy; span 2: audio; span 3: missing locale
        span0 = sorted([(ids[self.MODEL], 0, o_model, len(self.model), 0),
                        (ids[self.TEXTURE], 0, o_sd, len(self.texture_sd), 36),
                        (ids[self.TEXTURE], 0, o_hd, len(self.texture_hd), -1),
                        (ids[self.LOCAL], 0, o_lb, len(self.local_base), -1)], key=lambda r: r[0])
        # span 1: the French copy beside a row of the (absent) .fr soundbank archive, which gives
        # the span its language; span 2: audio; span 3: a German copy in an absent .de archive
        rows = span0 + [(ids[self.LOCAL], 0, o_lf, len(self.local_fr), -1),
                        (0x8000000000000001, 2, 0, 4, -1),
                        (self.audio_id, 1, 0, len(self.audio), -1),
                        (ids[self.LOCAL], 3, 0, 4, -1)]
        n = len(span0)
        spans = [(0, n), (n, 2), (n + 2, 1), (n + 3, 1)] + [(len(rows), 0)] * 252
        archives = ['d\\main', 'd\\wem_0', 'd\\soundbank.fr', 'd\\wem.de']
        sections = {
            toc_module.TAG_SPANS: b''.join(struct.pack('<II', a, c) for a, c in spans),
            toc_module.TAG_IDS: b''.join(struct.pack('<Q', r[0]) for r in rows),
            toc_module.TAG_META: b''.join(struct.pack('<IIIi', r[3], r[1], r[2], r[4]) for r in rows),
            toc_module.TAG_ARCHIVES: b''.join(archive_record(a) for a in archives),
            toc_module.TAG_HEADERS: self.header_model + self.header_texture,
            toc_module.TAG_TEXTURE_IDS: struct.pack('<Q', ids[self.TEXTURE]),
        }
        body = dat1(0x4D7CF320, sections)
        (self.folder / 'toc').write_bytes(struct.pack('<II', toc_module.TOC_MAGIC, len(body)) + body + bytes(7))
        names = [self.MODEL, self.TEXTURE, self.MATERIAL, self.LOCAL]
        names.sort(key=lambda p: ids[p])
        blob, offsets = b'', []
        base = 16 + 12 * 4
        for name in names:
            offsets.append(base + len(blob))
            blob += name.encode('ascii') + b'\0'
        model_at = names.index(self.MODEL)
        deps = [names.index(self.MATERIAL), names.index(self.TEXTURE), -1]
        dep_starts = [0 if i == model_at else -1 for i in range(len(names))]
        dag_body = dat1(0x2A077A51, {
            toc_module.TAG_NAMES: b''.join(struct.pack('<I', o) for o in offsets),
            toc_module.TAG_TYPES: bytes(len(names)),
            toc_module.TAG_DEP_STARTS: b''.join(struct.pack('<i', s) for s in dep_starts),
            toc_module.TAG_DEPS: b''.join(struct.pack('<i', d) for d in deps)}, strings=blob)
        (self.folder / 'dag').write_bytes(struct.pack('<III', toc_module.DAG_MAGIC, len(dag_body), 0) + dag_body)
        self.archives = archives


@unittest.skipIf(lz4block is None, 'lz4 package not installed')
class SyntheticInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'game').mkdir()
        self.fx = Fixture(root / 'game')
        self.cache = root / 'cache'
        self.source = RcraSource(root / 'game', root / 'out', cache_dir=self.cache)

    def tearDown(self):
        self.source.close()
        self.temp.cleanup()

    def key(self, path):
        return '%016x' % self.fx.ids[path]

    def test_detection_and_locate(self):
        game = self.fx.folder
        self.assertTrue(is_game_root(game))
        self.assertEqual(locate(game / 'd'), game.resolve())
        self.assertEqual(locate(game / 'toc'), game.resolve())
        self.assertFalse(is_game_root(game / 'd'))

    def test_vectorised_crc_matches_reference(self):
        paths = [self.fx.MODEL, 'A//B\\C.texture', 'x', 'Levels\\I29\\Zone.zone']
        self.assertEqual(toc_module.path_crc64_many(paths).tolist(), [path_crc64(p) for p in paths])

    def test_entries_paths_and_types(self):
        entry = self.source.entries[self.key(self.fx.MODEL)]
        self.assertEqual(entry['path'], 'characters/hero/hero_test/hero_test.model')
        self.assertEqual(entry['type'], 'model')
        self.assertEqual(self.source.resolve('CHARACTERS\\hero\\hero_test\\hero_test.model'), [self.key(self.fx.MODEL)])
        self.assertEqual(self.source.ids_of_type('texture'), [self.key(self.fx.TEXTURE)])
        audio = self.source.entries['%016x' % self.fx.audio_id]
        self.assertEqual(audio['type'], 'unnamed')
        self.assertTrue(audio['path'].startswith('_unnamed/d_wem_0/'))
        self.assertNotIn('0' * 16, self.source.entries)
        self.assertIsNone(self.source.entries.get('not hex'))

    def test_header_is_prepended_and_bytes_match(self):
        data, _digest = self.source.bytes(self.key(self.fx.MODEL))
        self.assertEqual(data, self.fx.header_model + self.fx.model)
        self.assertEqual(self.source.asset_data(self.key(self.fx.TEXTURE)), self.fx.header_texture + self.fx.texture_sd)

    def test_texture_stream_is_the_headerless_row(self):
        key = self.key(self.fx.TEXTURE)
        self.assertTrue(self.source.entries[key]['streamed'])
        self.assertEqual(self.source.texture_stream(key), self.fx.texture_hd)
        self.assertIsNone(self.source.texture_stream(self.key(self.fx.MODEL)))

    def test_language_copies(self):
        key = self.key(self.fx.LOCAL)
        languages = sorted(o['language'] for o in self.source.entries[key]['occurrences'])
        self.assertEqual(languages, ['', 'de', 'fr'])
        self.assertEqual(self.source.asset_data(key), self.fx.local_base)
        self.assertEqual(self.source.bytes(key, language='fr')[0], self.fx.local_fr)

    def test_dag_dependencies_become_references(self):
        refs = [r['path'] for r in self.source.asset_references(self.key(self.fx.MODEL))]
        self.assertEqual(refs, ['materials/hero_test.material', 'characters/hero/hero_test/textures/hero_test_c.texture'])
        self.assertEqual(self.source.asset_references(self.key(self.fx.TEXTURE)), [])

    def test_raw_archive_and_missing_locale(self):
        self.assertEqual(self.source.asset_data('%016x' % self.fx.audio_id), self.fx.audio)
        self.assertFalse(any('wem.de' in w for w in self.source.warnings))   # locale archives are optional

    def test_index_cache_is_reused(self):
        cached = list(self.cache.glob('index_*.pickle'))
        self.assertEqual(len(cached), 1)
        again = RcraSource(self.fx.folder, self.fx.folder.parent / 'out', cache_dir=self.cache)
        try:
            self.assertEqual(again.paths, self.source.paths)
            self.assertEqual(again.dag.ids.tolist(), self.source.dag.ids.tolist())
        finally:
            again.close()

    def test_toc_padding_and_bad_magic(self):
        data = (self.fx.folder / 'toc').read_bytes()
        self.assertEqual(len(toc_module.read_toc(data).ids), 8)
        with self.assertRaises(ValueError):
            toc_module.read_toc(b'\0' * 4 + data[4:])

    def test_decode_writes_to_output_only(self):
        path = self.source.decode(self.key(self.fx.MODEL))
        self.assertTrue(str(path).startswith(str(self.source.output)))
        self.assertEqual(path.read_bytes(), self.fx.header_model + self.fx.model)


@unittest.skipIf(lz4block is None, 'lz4 package not installed')
class DsarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_across_block_edges(self):
        payload = bytes((i * 7) % 251 for i in range(1000))
        path = self.root / 'a'
        path.write_bytes(dsar(payload, block=100))
        reader = BlockReader()
        try:
            for offset, size in ((0, 1000), (95, 10), (100, 100), (250, 333), (999, 1), (500, 0)):
                self.assertEqual(reader.read(path, offset, size), payload[offset:offset + size])
            with self.assertRaises(ValueError):
                reader.read(path, 990, 20)
        finally:
            reader.close()

    def test_gdeflate_block_without_codec_names_the_reason(self):
        path = self.root / 'g'
        path.write_bytes(dsar(b'x' * 50, block=50, modes={0: MODE_GDEFLATE}, compress=False))
        reader = BlockReader(RuntimeError('no dstoragecore.dll'))
        try:
            with self.assertRaisesRegex(ValueError, 'GDeflate.*dstoragecore'):
                reader.read(path, 0, 10)
        finally:
            reader.close()

    def test_rejects_gaps_and_unknown_modes(self):
        good = dsar(b'y' * 100, block=50)
        gap = bytearray(good)
        struct.pack_into('<Q', gap, 64, 60)          # second block's decompressed offset
        (self.root / 'gap').write_bytes(bytes(gap))
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            ArchiveFile(self.root / 'gap')
        odd = bytearray(good)
        odd[32 + 24] = 9
        (self.root / 'odd').write_bytes(bytes(odd))
        with self.assertRaisesRegex(ValueError, 'mode'):
            ArchiveFile(self.root / 'odd')
        self.assertEqual(BLOCK_DTYPE.itemsize, 32)


@unittest.skipUnless(GAME, 'RCRA_GAME is not set')
class RealInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = RcraSource(GAME, Path(cls.temp.name) / 'out')

    @classmethod
    def tearDownClass(cls):
        cls.source.close()
        cls.temp.cleanup()

    def test_named_types(self):
        counts = self.source.type_counts()
        self.assertGreater(counts.get('model', 0), 10000)
        self.assertEqual(counts.get('texture', 0), len(self.source.toc.texture_ids))
        self.assertEqual(self.source.warnings, [])

    def test_ratchet_model_and_dependencies(self):
        key = self.source.resolve('characters/hero/hero_ratchet/hero_ratchet.model')[0]
        data = self.source.asset_data(key)
        self.assertEqual(struct.unpack_from('<I', data, 0)[0], 0x9D2C0FA9)
        self.assertEqual(data[36:40], b'1TAD')
        refs = [r['path'] for r in self.source.asset_references(key)]
        self.assertIn('hero_ratchet_head.material', [p.rsplit('/', 1)[-1] for p in refs])

    def test_streamed_texture_uses_gdeflate(self):
        if isinstance(self.source.reader.gdeflate, Exception):
            self.skipTest(str(self.source.reader.gdeflate))
        archive = self.source.reader.archive(Path(self.source.root) / 'd' / 'tex_char_ratchet')
        index = int(np.nonzero(archive.blocks['mode'] == MODE_GDEFLATE)[0][0])
        start, size = int(archive.blocks['dofs'][index]), int(archive.blocks['dsize'][index])
        self.assertEqual(len(self.source.reader.read(archive.path, start, size)), size)

    def test_matches_alert_dat1lib(self):
        """ALERT's TOC2 reader (pure Python LZ4) returns the same bytes for LZ4-only assets."""
        root = str(Path(__file__).resolve().parents[1] / 'third_party' / 'ALERT')
        sys.path.insert(0, root)
        try:
            import dat1lib.types.toc2 as toc2
            with open(Path(GAME) / 'toc', 'rb') as f:
                alert = toc2.TOC2(f)
            alert.set_archives_dir(GAME)
            checked = 0
            for path in ('configs/hero/hero_ship_teslablast_shot.config',
                         'materials/gl_default_grid/gl_default_grid_black.material'):
                keys = self.source.resolve(path)
                if not keys:
                    continue
                entries = alert.get_asset_entries_by_assetid(int(keys[0], 16), stop_on_first=True)
                self.assertEqual(bytes(alert.extract_asset(entries[0])), self.source.asset_data(keys[0]))
                checked += 1
            alert.set_archives_dir(None)
            self.assertGreater(checked, 0)
        finally:
            sys.path.remove(root)
            for name in [m for m in sys.modules if m == 'dat1lib' or m.startswith('dat1lib.')]:
                del sys.modules[name]


if __name__ == '__main__':
    unittest.main()
