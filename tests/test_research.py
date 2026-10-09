"""Synthetic research fixtures; no game bytes are embedded in tests."""
import csv
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from rc2016.research.source import Asset, Corpus, level_name
from rc2016.research.cross_reference import CrossReference, HashIndex, StringContext
from rc2016.research.census import Census
from rc2016.research.fields import Layout, select_sources
from rc2016.research.correlate import Correlations, zone_features
from rc2016.research.hypothesis import Claim, RecordSource, run
from rc2016.research.report import build, write


def dat1(items, strings=()):
    """Sections may be callables receiving actual absolute string/section offsets."""
    string_offsets = {}
    pool = bytearray(b'\0')
    start = 16 + 12 * len(items)
    for string in strings:
        string_offsets[string] = start + len(pool)
        pool.extend(string.encode('ascii') + b'\0')
    pool.extend(b'\0' * (-(start + len(pool)) % 16))
    cursor = start + len(pool)
    sizes = []
    for tag, payload in items:
        raw = payload(string_offsets, {}) if callable(payload) else payload
        sizes.append((tag, cursor, len(raw)))
        cursor += len(raw)
    offsets = {tag: off for tag, off, _ in sizes}
    raw = bytearray(struct.pack('<IIII', 0x44415431, 0, cursor, len(items)))
    raw.extend(b''.join(struct.pack('<III', *row) for row in sizes))
    raw.extend(pool)
    for tag, payload in items:
        raw.extend(payload(string_offsets, offsets) if callable(payload) else payload)
    return bytes(raw)


def archive_fixture(root, assets):
    root.mkdir(parents=True)
    rows, data = [], bytearray()
    for name, raw in assets:
        rows.append({'Asset Path': name, 'Built Path': name, 'Asset Type': Path(name).suffix[1:],
                     'Archive File': 'g00s000', 'Segment Offset': len(data), 'File Size': len(raw), 'Language': ''})
        data.extend(raw)
    (root / 'g00s000').write_bytes(data)
    for name in ('toc', 'dag', 'chunkmap.txt'):
        (root / name).write_bytes(b'')
    with (root / 'layout.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class ResearchCensusTests(unittest.TestCase):
    def test_gcd_stride_parallel_count_and_pointer_evidence(self):
        census = Census(CrossReference())
        for count in (2, 3):
            data = dat1([(0x7777, b'\0' * (8 * count)), (0x8888, b'\0' * (4 * count)),
                         (0x9999, lambda s, o: struct.pack('<I', s['example.model']))], ['example.model'])
            census.add(Asset(f'levels/test/a{count}.zone', data))
        result = census.finish()
        row = next(r for r in result['sections'] if r['tag'] == '0x00007777')
        self.assertEqual(row['size_gcd'], 8)
        self.assertIn(8, [c['stride'] for c in row['candidate_strides']])
        parallel = next(p for p in row['parallel_counts'] if p['source_stride'] == 8 and p['target'] == '0x00008888')
        self.assertEqual((parallel['matches'], parallel['tests']), (2, 2))
        ptr = next(r for r in result['sections'] if r['tag'] == '0x00009999')['string_offsets']
        self.assertEqual((ptr['matches'], ptr['tests']), (2, 2))

    def test_bad_container_bounds_and_duplicate_tags_raise(self):
        good = bytearray(dat1([(1, b'1234')]))
        struct.pack_into('<I', good, 20, len(good) + 1)
        with self.assertRaisesRegex(ValueError, 'outside DAT1'):
            Asset('x.zone', bytes(good))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            Asset('x.zone', dat1([(1, b'1234'), (1, b'5678')]))


class FieldTests(unittest.TestCase):
    def test_parallel_spawn_count_beats_sibling_scalar_stride(self):
        row = dict(tag='0x3395AEC1', decoded=False,
                   candidate_strides=[{'stride': s} for s in (4, 8, 16, 24, 48)],
                   dat1lib=[dict(entry_size=4, serialized=False)],
                   parallel_counts=[dict(source_stride=48, target='0x2236C47A', target_stride=4,
                                         matches=17, tests=17)])
        self.assertEqual(select_sources([row])[0x3395AEC1][0], 48)

    def test_known_float_index_offset_string_hash_and_flags(self):
        hashes = HashIndex(['environment/test.model'])
        from dat1lib.crc32 import hash as crc32
        from dat1lib.crc64 import hash as crc64
        def records(strings, offsets):
            return b''.join(struct.pack('<fIIIIIQ', i + .5, i % 5, strings['environment/test.model'],
                                         offsets.get(0x2222, 4096) + i % 20,
                                         crc32('environment/test.model'), 0x400000 | (0x20 if i % 2 else 0),
                                         crc64('environment/test.model')) for i in range(20))
        asset = Asset('levels/test/a.zone', dat1([(0x1111, records), (0x2222, b'\0' * 20)], ['environment/test.model']))
        start, _ = asset.table[0x1111]
        layout = Layout('synthetic', 32)
        layout.add(asset, [(start + 32 * i, 32) for i in range(20)], hashes)
        fields = {(f['offset'], f['width']): f for f in layout.finish()['fields']}
        def names(offset, width=4):
            return [c['candidate'] for c in fields[(offset, width)]['candidates']]
        self.assertIn('finite plausible float', names(0))
        self.assertIn('index into 0x00002222 (stride 4)', names(4))
        self.assertIn('string offset', names(8))
        self.assertIn('absolute offset into 0x00002222', names(12))
        self.assertIn('known string hash', names(16))
        self.assertIn('enum or bit flags', names(20))
        self.assertEqual(fields[(20, 4)]['bit_set_counts'][5], 10)
        self.assertIn('known string hash', names(24, 8))
        self.assertEqual(fields[(0, 4)]['float_range'], [.5, 19.5])

    def test_missing_tail_slots_do_not_become_zero_padding(self):
        asset = Asset('x.zone', dat1([(1, b'\0' * 12)]))
        start = asset.table[1][0]
        layout = Layout('different lengths')
        layout.add(asset, [(start, 4), (start + 4, 8)], HashIndex())
        field = next(f for f in layout.finish()['fields'] if f['offset'] == 4 and f['width'] == 4)
        self.assertEqual((field['records'], field['absent_records']), (1, 1))

    def test_affine_matrix_and_unit_vectors(self):
        matrix = struct.pack('<16f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 4, 5, 6, 1)
        asset = Asset('x.zone', dat1([(1, matrix)]))
        layout = Layout('matrix')
        layout.add(asset, [(asset.table[1][0], 64)], HashIndex())
        structures = layout.finish()['structures']
        self.assertTrue(any(s['candidate'].startswith('affine') and s['matches'] == 1 for s in structures))


class CorrelationTests(unittest.TestCase):
    def test_planted_bit_and_missing_optional_population(self):
        corr = Correlations()
        corr.add([0, 0, 32, 32], {'planted': [0, 0, 1, 1]})
        corr.add([0, 32], {'optional': [0, 1]}, 'known_models')
        result = corr.finish()
        row = next(r for r in result['associations'] if r['bit'] == 5 and r['feature'] == 'planted')
        self.assertEqual((row['n00'], row['n01'], row['n10'], row['n11']), (2, 0, 0, 2))
        self.assertEqual(row['phi'], 1)
        optional = next(r for r in result['associations'] if r['feature'] == 'optional')
        self.assertEqual(optional['observations'], 2)

    def test_later_feature_uses_whole_population(self):
        corr = Correlations()
        corr.add([0, 0], {'early': [1, 1]})
        corr.add([32, 32], {'late': [1, 1]})
        late = next(r for r in corr.finish()['associations'] if r['feature'] == 'late')
        self.assertEqual((late['n00'], late['n11']), (2, 2))


class HypothesisTests(unittest.TestCase):
    def test_float3_bounds_and_namespace_populations(self):
        def zone(name, flags):
            record = bytearray(176)
            struct.pack_into('<16f', record, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 4, 5, 6, 1)
            struct.pack_into('<IQ', record, 0x44, 176, 1)
            struct.pack_into('<II', record, 0x58, 0, flags)
            data = dat1([(0xC6A5905E, lambda st, off: struct.pack('<I', st['test.model'])),
                         (0x6987F172, struct.pack('<I', 0)), (0x06ABCAB2, bytes(record))], ['test.model'])
            return Asset(name, data)
        main = zone('levels/test/a.zone', 32)
        legacy = zone('rc1/levels/test/b.zone', 0)
        corr = Correlations()
        zone_features(main, corr)
        zone_features(legacy, corr)
        self.assertEqual(corr.pools['levels']['total'], 1)
        self.assertEqual(corr.pools['levels']['bits'][5], 1)
        self.assertEqual(corr.pools['rc1']['bits'][5], 0)
        self.assertEqual(level_name(legacy.path), 'rc1/test')
        result = run([main], RecordSource(), [Claim('translation', 0x30, 'float3_bounds'),
                                             Claim('outside bounds', 0x70, 'float3_bounds')])
        self.assertEqual(result['claims'][0]['passes'], 1)
        self.assertEqual(result['claims'][1]['fails'], 1)

    def test_pass_fail_offsets_and_count_claim(self):
        asset = Asset('levels/test/a.zone', dat1([(1, struct.pack('<4I', 0, 2, 9, 2)), (2, b'\0' * 8)]))
        source = RecordSource(section=1, stride=8, offsets_section=None, size_field=None)
        result = run([asset], source, [Claim('index', 0, 'index', 2), Claim('count', 4, 'equals_count', 2)])
        self.assertEqual((result['claims'][0]['passes'], result['claims'][0]['fails']), (1, 1))
        self.assertEqual(result['claims'][1]['passes'], 2)
        example = result['claims'][0]['counterexamples'][0]
        self.assertEqual(example['record_offset'], asset.table[1][0] + 8)
        self.assertEqual(example['observed'], 9)

    def test_offset_source_and_bad_source_are_visible(self):
        asset = Asset('x.zone', dat1([(1, struct.pack('<4I', 1, 0, 2, 0)), (2, struct.pack('<2I', 0, 8))]))
        source = RecordSource(section=1, offsets_section=2, size_field=None)
        result = run([asset], source, [Claim('range', 0, 'range', maximum=1)])
        self.assertEqual((result['records'], result['claims'][0]['fails']), (2, 1))
        invalid = run([asset], RecordSource(section=1, stride=7, offsets_section=None), [])
        self.assertEqual(len(invalid['source_errors']), 1)


class Dat1libTests(unittest.TestCase):
    def test_utf8_string_values_and_single_character_names(self):
        def serialized(strings, offsets):
            text = 'café'.encode('utf-8')
            children = struct.pack('<IHBBIHBB', 0, 0x10, 0, 2, 0, 0x10, 0, 10)
            body = children + struct.pack('<2I', strings['X'], strings['Title']) + struct.pack('<I', 42)
            body += struct.pack('<IIQ', len(text), 0, 0) + text + b'\0'
            body += b'\0' * (-len(body) % 4)
            return struct.pack('<4I', 0, 0x03150044, 2, len(body)) + body
        asset = Asset('x.zone', dat1([(0x81999057, serialized)], ['X', 'Title']))
        result = next(r for r in CrossReference().probe(asset, 0x81999057) if r['version_hypothesis'] == 202300)
        self.assertEqual(result['status'], 'parses_cleanly')
        self.assertEqual(result['schema']['$.root.X'], ['integer'])
        self.assertEqual(result['schema']['$.root.Title'], ['string'])
        self.assertNotIn('caf', json.dumps(result['schema']))

    def test_throwing_class_is_reported(self):
        class Raises:
            ENTRY_SIZE = 4
            def __init__(self, data, container):
                raise RuntimeError('planted failure')
        result = CrossReference().probe_class(Raises, b'abcd', None)
        self.assertEqual(result['status'], 'raises')
        self.assertIn('planted failure', result['error'])

    def test_real_fixed_reader_leftovers(self):
        xref = CrossReference()
        asset = Asset('x.zone', dat1([(0xE4158AC3, b'\0' * 17)]))
        results = xref.probe(asset, 0xE4158AC3)
        self.assertTrue(any(r['status'] == 'leftover_bytes' and r['leftover_bytes'] == 1 for r in results))

    def test_generic_reader_really_reads_schema_instead_of_i16_stub(self):
        def serialized(strings, offsets):
            return struct.pack('<4IIHBBII', 0, 0x03150044, 1, 16,
                               0x1234, 0x10, 0, 2, strings['SecretKey'], 123456789)
        xref = CrossReference()
        asset = Asset('x.zone', dat1([(0x81999057, serialized)], ['SecretKey']))
        results = xref.probe(asset, 0x81999057)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r['status'], 'parses_cleanly')
            self.assertEqual(r['nodes'], 1)
            self.assertIn('$.root.SecretKey', r['schema'])
            self.assertEqual(r['consumed_bytes'], 32)
            self.assertNotIn('123456789', json.dumps(r['schema']))

    def test_unknown_serialized_type_and_skipped_bytes_are_reported(self):
        raw = struct.pack('<4IIHBBII', 0, 0x03150044, 1, 16, 0, 0x10, 0, 255, 0, 1)
        bad = Asset('bad.zone', dat1([(0x81999057, raw)]))
        self.assertTrue(all(r['status'] == 'raises' for r in CrossReference().probe(bad, 0x81999057)))
        skipped = Asset('skip.zone', dat1([(0x2300D240, struct.pack('<4I', 0, 0x03150044, 0, 4) + b'abcd')]))
        results = CrossReference().probe(skipped, 0x2300D240)
        self.assertTrue(all(r['status'] == 'leftover_bytes' and r['leftover_bytes'] == 4 for r in results))


class ReportTests(unittest.TestCase):
    def test_archive_cli_and_deterministic_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            archive_fixture(root, [('levels/test/a.zone', dat1([(0x7777, b'\0' * 16)])),
                                   ('levels/test/b.level', dat1([(0x8888, b'\0' * 8)]))])
            out = Path(directory) / 'out'
            corpus = Corpus(root, out)
            with corpus.opened():
                first = build(corpus)
                second = build(corpus)
                self.assertEqual(first, second)
                write(first, out)
            result = subprocess.run([sys.executable, '-m', 'rc2016.research', str(root), '--out', str(out)],
                                    capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((out / 'report.json').read_text()), first)
            self.assertIn('dat1lib cross-check', (out / 'report.md').read_text())
            self.assertFalse((root / '.cache').exists())
            self.assertTrue((out / '.cache').is_dir())
            claims_out = Path(directory) / 'claims'
            result = subprocess.run([sys.executable, '-m', 'rc2016.research', 'hypothesis', str(root),
                                     '--out', str(claims_out), '--section', '0x7777', '--stride', '8',
                                     '--claim', json.dumps(dict(name='zero', offset=0, kind='range', maximum=0))],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((claims_out / 'hypotheses.json').read_text())['claims'][0]['passes'], 2)


    def test_headless_import(self):
        code = "import rc2016.research.report,sys; assert not any(x.startswith(('PySide','PyQt','OpenGL','bpy')) for x in sys.modules)"
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is not set')
    def test_real_archive_opt_in(self):
        from rc2016.formats.archive import locate
        try:
            root = locate(os.environ['RC2016_RESEARCH'])
        except ValueError:
            self.skipTest('RC2016_RESEARCH must contain one game archive for this optional test')
        with tempfile.TemporaryDirectory() as directory:
            corpus = Corpus(root, directory, limit=1)
            with corpus.opened():
                report = build(corpus)
            self.assertEqual(report['corpus']['scanned_files'], 1)
            self.assertFalse(report['corpus']['scan_errors'])


if __name__ == '__main__':
    unittest.main()
