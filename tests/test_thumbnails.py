"""Thumbnail cache index, PNG writer, downsampling and command line (no Tk, OpenGL or game data)."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

from rc2016.app import thumbnails as th


def decode_png(data):
    """(width, height, (depth, color type), top-down RGBA bytes) of an 8-bit RGBA, filter-0 PNG."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    position, header, idat = 8, None, b''
    while position < len(data):
        length, tag = struct.unpack('>I4s', data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        assert struct.unpack('>I', data[position + 8 + length:position + 12 + length])[0] == zlib.crc32(tag + body) & 0xFFFFFFFF
        if tag == b'IHDR':
            header = struct.unpack('>IIBBBBB', body)
        elif tag == b'IDAT':
            idat += body
        position += 12 + length
    width, height, depth, color = header[:4]
    raw, stride = zlib.decompress(idat), width * 4
    rows = []
    for y in range(height):
        row = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        assert row[0] == 0
        rows.append(row[1:])
    return width, height, (depth, color), b''.join(rows)


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = th.cache_dir(self.temp.name)

    def ok_entry(self, index, model_id='abc', source_hash='h1', size=192):
        index.write_png(model_id, b'png')
        return index.record(model_id, 'ok', source_hash, size)

    def test_cache_lives_under_the_output_root_cache_folder(self):
        self.assertEqual(self.folder, Path(self.temp.name) / '.cache' / 'thumbnails')

    def test_file_names_are_safe(self):
        self.assertEqual(th.safe_name('f696247894031f0d7010'), 'f696247894031f0d7010')
        self.assertEqual(th.safe_name('../x\\y:z'), 'x_y_z')
        self.assertEqual(th.safe_name('///'), 'model')

    def test_round_trip_and_current(self):
        index = th.ThumbnailIndex.load(self.folder)
        self.ok_entry(index)
        index.save()
        again = th.ThumbnailIndex.load(self.folder)
        self.assertTrue(again.is_current('abc', 192, 'h1'))
        self.assertEqual(again.get('abc')['renderer_version'], th.THUMBNAIL_VERSION)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['abc.png', 'index.json'])

    def test_stale_on_hash_version_and_size(self):
        index = th.ThumbnailIndex.load(self.folder)
        self.ok_entry(index)
        self.assertTrue(index.is_current('abc', 192))                 # no hash known: not compared
        self.assertTrue(index.is_current('abc', 192, 'h1'))
        self.assertFalse(index.is_current('abc', 192, 'other'))       # source changed
        self.assertFalse(index.is_current('abc', 128, 'h1'))          # other size
        with mock.patch.object(th, 'THUMBNAIL_VERSION', th.THUMBNAIL_VERSION + 1):
            self.assertFalse(index.is_current('abc', 192, 'h1'))      # renderer changed
        self.assertFalse(index.is_current('missing', 192))

    def test_success_needs_its_png(self):
        index = th.ThumbnailIndex.load(self.folder)
        self.ok_entry(index)
        index.png_path('abc').unlink()
        self.assertFalse(index.is_current('abc', 192))

    def test_failures_are_recorded_and_not_retried(self):
        index = th.ThumbnailIndex.load(self.folder)
        index.record('bad', 'unsupported', 'h2', 192, 'layout not decoded')
        index.record('worse', 'error', None, 192, 'x' * 2000)
        index.save()
        again = th.ThumbnailIndex.load(self.folder)
        self.assertTrue(again.is_current('bad', 192, 'h2'))
        self.assertFalse(again.is_current('bad', 192, 'h3'))          # retried once the source changes
        self.assertEqual(again.get('bad')['reason'], 'layout not decoded')
        self.assertEqual(len(again.get('worse')['reason']), 500)
        self.assertEqual(th.missing_ids(again, ['bad', 'new', 'worse'], 192), ['new'])
        with self.assertRaises(ValueError):
            index.record('x', 'fine')

    def test_writes_are_atomic_and_leave_no_temp_files(self):
        index = th.ThumbnailIndex.load(self.folder)
        self.ok_entry(index)
        index.save()
        with mock.patch('os.replace', side_effect=OSError('locked')):
            with self.assertRaises(OSError):
                index.save(force=True)
        self.assertEqual(json.loads(index.path.read_text(encoding='utf8'))['models']['abc']['status'], 'ok')
        self.assertFalse(list(self.folder.glob('*.tmp')))

    def test_damaged_index_is_ignored(self):
        self.folder.mkdir(parents=True)
        (self.folder / th.INDEX_NAME).write_text('{not json', encoding='utf8')
        self.assertEqual(th.ThumbnailIndex.load(self.folder).models, {})
        (self.folder / th.INDEX_NAME).write_text('[1, 2]', encoding='utf8')
        self.assertEqual(th.ThumbnailIndex.load(self.folder).models, {})

    def test_save_only_when_dirty_or_forced(self):
        index = th.ThumbnailIndex.load(self.folder)
        index.save()
        self.assertFalse(index.path.exists())
        index.save(force=True)
        self.assertTrue(index.path.exists())


class PngTests(unittest.TestCase):
    def test_rgba_round_trip(self):
        pixels = bytes([255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0, 10, 20, 30, 40])
        width, height, kind, rows = decode_png(th.encode_png_rgba(2, 2, pixels))
        self.assertEqual((width, height, kind), (2, 2, (8, 6)))
        self.assertEqual(rows, pixels)
        self.assertEqual(rows[3::4], bytes([255, 128, 0, 40]))      # alpha survives

    def test_rejects_wrong_buffer_size(self):
        with self.assertRaises(ValueError):
            th.encode_png_rgba(2, 2, b'\x00' * 15)

    def bottom_up_2x2(self):
        # rows bottom first: bottom = transparent, opaque red; top = opaque red, opaque blue
        return bytes([0, 0, 0, 0, 255, 0, 0, 255, 255, 0, 0, 255, 0, 0, 255, 255])

    def expected_average(self):
        # red x2 + blue (opaque) averaged by alpha weight, alpha 3/4 of 255
        return bytes([round(2 * 255 / 3), 0, round(255 / 3), round(255 * 3 / 4)])

    def test_downsample_flips_and_weights_by_alpha(self):
        width, height, rgba = th.downsample_rgba(self.bottom_up_2x2(), 2, 2, 2)
        self.assertEqual((width, height), (1, 1))
        self.assertEqual(rgba, self.expected_average())

    def test_downsample_without_numpy_matches(self):
        with mock.patch.dict(sys.modules, {'numpy': None}):
            _w, _h, rgba = th.downsample_rgba(self.bottom_up_2x2(), 2, 2, 2)
        self.assertEqual(rgba, self.expected_average())

    def test_downsample_keeps_row_order(self):
        # a 1x2 image: bottom pixel red, top pixel blue; factor 1 only flips
        data = bytes([255, 0, 0, 255, 0, 0, 255, 255])
        _w, _h, rgba = th.downsample_rgba(data, 1, 2, 1)
        self.assertEqual(rgba, bytes([0, 0, 255, 255, 255, 0, 0, 255]))

    def test_factor_must_divide_size(self):
        with self.assertRaises(ValueError):
            th.downsample_rgba(b'\x00' * 36, 3, 3, 2)


class CommandLineTests(unittest.TestCase):
    def test_ids_file_keeps_priority_order_and_drops_duplicates_and_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'ids.txt'
            path.write_text('# first the visible ones\nb\n\na\n  c  \nb\n', encoding='utf8')
            self.assertEqual(th.read_ids_file(path), ['b', 'a', 'c'])

    def test_arguments(self):
        args = th.parse_arguments(['--source', 'S', '--output', 'O', '--all'])
        self.assertEqual((args.size, args.all, args.force, args.no_fbo, args.ids_file), (192, True, False, False, None))
        args = th.parse_arguments(['--source', 'S', '--output', 'O', '--ids-file', 'f.txt', '--size', '96', '--limit', '5'])
        self.assertEqual((args.size, args.limit, args.ids_file), (96, 5, Path('f.txt')))

    def test_argument_errors(self):
        for argv in (['--source', 'S', '--output', 'O'],                       # nothing to render
                     ['--source', 'S', '--output', 'O', '--all', '--size', '8'],
                     ['--source', 'S', '--all']):
            with self.subTest(argv=argv), mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                th.parse_arguments(argv)

    def test_select_ids_orders_file_then_rest_of_archive_by_path(self):
        models = [('z/last.model', 'id3'), ('a/first.model', 'id1'), ('m/mid.model', 'id2')]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'ids.txt'
            path.write_text('id3\n', encoding='utf8')
            args = th.parse_arguments(['--source', 'S', '--output', 'O', '--ids-file', str(path), '--all'])
            self.assertEqual(th.select_ids(args, models), ['id3', 'id1', 'id2'])
            args = th.parse_arguments(['--source', 'S', '--output', 'O', '--ids-file', str(path)])
            self.assertEqual(th.select_ids(args, models), ['id3'])
            args = th.parse_arguments(['--source', 'S', '--output', 'O', '--all', '--limit', '2'])
            self.assertEqual(th.select_ids(args, models), ['id1', 'id2'])


if __name__ == '__main__':
    unittest.main()
