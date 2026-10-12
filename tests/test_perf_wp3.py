"""Archive IO, model decode and model-viewer load path: cached/vectorized code against the former algorithms."""
from pathlib import Path
import os
import struct
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from rc2016.formats import archive as ar
from rc2016.formats import native_model as nm
from rc2016.formats import texture as tx
from rc2016.formats.container import read_container
from rc2016.viewer import loader as ld
from rc2016.viewer import textures as tp


# ------------------------------------------------------------------ reference copies of the former code
def reference_skin_batch(skin, skin_offset, count, stride, influences, palette, id_base, joint_count,
                         first, weights, batch_index, mesh_index):
    packed_count = influences
    for i in range(count):
        skin_record = skin[skin_offset+i*stride:skin_offset+(i+1)*stride]
        ids = list(skin_record[:influences])
        raw = list(skin_record[packed_count:packed_count+influences])
        if palette is not None:
            if max(ids) >= len(palette):
                raise ValueError(f'Skin batch {batch_index} local joint id exceeds its remap palette')
            ids = [palette[x] for x in ids]
        elif id_base:
            ids = [x+id_base for x in ids]
            if max(ids) >= joint_count:
                raise ValueError(f'Skin batch {batch_index} joint id base exceeds the skeleton')
        values = [1.0] if influences == 1 else ([v/sum(raw) for v in raw] if sum(raw) else [])
        if not values:
            raise ValueError(f'Zero-sum skin weights in batch {batch_index}')
        target = first+i
        if weights[target] is not None:
            raise ValueError(f'Overlapping skin batches on mesh {mesh_index} vertex {target}')
        weights[target] = list(zip(ids, values))


def reference_deswizzle(data, width, height, fmt):
    blocks_w, blocks_h, padded_w, padded_h, bytes_per_block = tx._tile_layout(width, height, fmt)
    needed = padded_w*padded_h*bytes_per_block
    padded = bytearray(needed); source = 0
    for tile_y in range(padded_h//8):
        for tile_x in range(padded_w//8):
            for morton in range(64):
                x = (morton & 1) | ((morton >> 1) & 2) | ((morton >> 2) & 4)
                y = ((morton >> 1) & 1) | ((morton >> 2) & 2) | ((morton >> 3) & 4)
                target = ((tile_y*8+y)*padded_w+tile_x*8+x)*bytes_per_block
                padded[target:target+bytes_per_block] = data[source:source+bytes_per_block]
                source += bytes_per_block
    row_bytes = blocks_w*bytes_per_block
    return b''.join(bytes(padded[r*padded_w*bytes_per_block:r*padded_w*bytes_per_block+row_bytes]) for r in range(blocks_h))


def reference_gfx_import(entries, url):
    name = url.replace('\\', '/').rsplit('/', 1)[-1].lower()
    stem = name.rsplit('.', 1)[0]
    for entry in entries.values():
        if entry['type'] in ('gfx', 'swf'):
            base = entry['path'].rsplit('/', 1)[-1].lower()
            if base == name or base.rsplit('.', 1)[0] == stem:
                return entry['path']
    return None


# ------------------------------------------------------------------ synthetic direct PS4 model
def _normal_word(x, y, z):
    q = lambda v: int(round((v+1.0)/2.0*1023))
    return q(x) | q(y) << 10 | q(z) << 20


def build_model(rng, meshes, batches, skin, joint_count=0, remap=b''):
    """meshes: [(vertex_count, first_skin_batch, skin_batch_count, flags)]; batches: [(skin_offset, u32, u16, code, count, first)]."""
    total = sum(m[0] for m in meshes)
    vertices = b''.join(struct.pack('<4hII', *(int(v) for v in rng.integers(-3000, 3000, 3)), 0,
                                    _normal_word(*(rng.uniform(-1, 1, 3)/2+np.array([.5, 0, 0]))),
                                    _normal_word(0.0, 1.0, 0.0)) for _ in range(total))
    uv0 = b''.join(struct.pack('<2e', *rng.uniform(0, 1, 2)) for _ in range(total))
    indexes, records, first_vertex, first_index = b'', b'', 0, 0
    for count, batch_first, batch_count, flags in meshes:
        tris = [(i, (i+1) % count, (i+2) % count) for i in range(count)]
        indexes += b''.join(struct.pack('<3H', *t) for t in tris)
        record = bytearray(64)
        struct.pack_into('<3I', record, 16, first_vertex, first_index, len(tris)*3)
        struct.pack_into('<2H', record, 28, count, 0)
        struct.pack_into('<2H', record, 32, flags | 0x10, 0)
        struct.pack_into('<2H', record, 36, batch_first, batch_count)
        records += bytes(record); first_vertex += count; first_index += len(tris)*3
    batch_table = b''.join(struct.pack('<II4H', *b) for b in batches)
    sections = [(nm.VERTICES, vertices), (nm.UV0, uv0), (nm.INDEXES, indexes), (nm.MESHES, records)]
    if batches: sections += [(nm.SKIN_BATCH, batch_table), (nm.SKIN_DATA, skin)]
    if joint_count: sections.append((nm.JOINTS, bytes(16*joint_count)))
    if remap: sections.append((nm.SKIN_JOINT_REMAP, remap))
    table_end = 16+12*len(sections)
    offset = (table_end+63)//64*64
    body, rows = b'', []
    for tag, payload in sections:
        rows.append((tag, offset+len(body), len(payload)))
        body += payload + bytes((-len(payload)) % 64)
    head = struct.pack('<4sIIHH', b'1TAD', nm.PS4_MODEL_TYPE_HASH, offset+len(body), len(sections), 0)
    head += b''.join(struct.pack('<III', *r) for r in rows)
    return head + bytes(offset-len(head)) + body


def skinned_fixture(seed=1):
    rng = np.random.default_rng(seed)
    skin = bytearray()
    batches = []
    # mesh 0: 6 vertices over a 2-influence batch (palette-less, no base) and a 4-influence batch
    def add(code, count, first, u32=0, u16=0, ids_max=5):
        influences, stride = nm._influence_layout(code)
        start = len(skin)
        for _ in range(count):
            ids = rng.integers(0, ids_max, influences)
            raw = rng.integers(1, 255, influences) if influences > 1 else []
            skin.extend(bytes(int(x) for x in ids) + bytes(int(x) for x in raw))
        batches.append((start, u32, u16, code, count, first))
    add(1, 3, 0); add(2, 3, 3)
    # mesh 1: 5 vertices, single influence with an NPC id base, then 8 influences
    add(0, 2, 0, u32=4); add(3, 3, 2)
    model = build_model(rng, [(6, 0, 2, 0), (5, 2, 2, 0)], batches, bytes(skin), joint_count=12)
    return model


class SkinWeightTests(unittest.TestCase):
    def run_both(self, skin, batches, local_count, palettes=None, joint_count=16):
        palettes = palettes or {}
        old, new = [None]*local_count, [None]*local_count
        assigned = np.zeros(local_count, dtype=bool)
        arrays = (np.zeros((local_count, 8), np.int64), np.zeros((local_count, 8)))
        old_error = new_error = None
        try:
            for b, (offset, base, code, count, first) in enumerate(batches):
                influences, stride = nm._influence_layout(code)
                reference_skin_batch(skin, offset, count, stride, influences, palettes.get(b), base, joint_count, first, old, b, 0)
        except ValueError as e:
            old_error = str(e)
        try:
            for b, (offset, base, code, count, first) in enumerate(batches):
                influences, stride = nm._influence_layout(code)
                nm._skin_batch_weights(skin, offset, count, stride, influences, palettes.get(b), base, joint_count,
                                       first, new, assigned, b, 0, arrays)
        except ValueError as e:
            new_error = str(e)
        self.assertEqual(old_error, new_error)
        if old_error is None:
            self.assertEqual(old, new)
            for row, pairs in enumerate(new):
                for slot, (j, w) in enumerate(pairs or []):
                    self.assertEqual((arrays[0][row, slot], arrays[1][row, slot]), (j, w))
                    self.assertIs(type(j), int); self.assertIs(type(w), float)
        return new_error

    def test_random_batches_match_the_former_loop(self):
        rng = np.random.default_rng(7)
        for trial in range(200):
            code = int(rng.integers(0, 4)); influences, stride = nm._influence_layout(code)
            count = int(rng.integers(0, 6)); first = int(rng.integers(0, 3))
            skin = bytes(rng.integers(0, 4 if trial % 3 == 0 else 256, 64 + count*stride, dtype=np.uint8))
            base = int(rng.integers(0, 12)) if trial % 2 else 0
            palette = tuple(int(x) for x in rng.integers(0, 90, int(rng.integers(1, 300)))) if trial % 5 == 0 else None
            self.run_both(skin, [(int(rng.integers(0, 32)), base, code, count, first)], first+count+1,
                          {0: palette} if palette else None, joint_count=int(rng.integers(1, 260)))

    def test_each_error_and_the_first_failing_vertex_wins(self):
        two = lambda pairs: bytes(j for j, _ in pairs) + bytes(w for _, w in pairs)
        # vertex 0 zero-sum, vertex 1 bad id: zero-sum is reported (first vertex)
        skin = two([(1, 0), (1, 0)]) + two([(200, 3), (1, 1)])
        self.assertIn('Zero-sum', self.run_both(skin, [(0, 0, 1, 2, 0)], 4, {0: (5,)*10}))
        # vertex 0 fine, vertex 1 has both a bad palette id and zero sum: palette check comes first
        skin = two([(1, 2), (1, 2)]) + two([(30, 0), (1, 0)])
        self.assertIn('remap palette', self.run_both(skin, [(0, 0, 1, 2, 0)], 4, {0: (5,)*10}))
        self.assertIn('joint id base', self.run_both(two([(9, 1), (1, 1)]), [(0, 4, 1, 1, 0)], 2, joint_count=10))
        overlap = two([(1, 1), (1, 1)]) * 3
        self.assertIn('Overlapping skin batches on mesh 0 vertex 2',
                      self.run_both(overlap, [(0, 0, 1, 2, 1), (4, 0, 1, 2, 2)], 5))
        self.assertIsNone(self.run_both(b'\x03', [(0, 0, 0, 1, 0)], 1))       # single influence, single vertex
        self.assertIsNone(self.run_both(b'', [(0, 0, 1, 0, 0)], 1))           # empty batch


class ModelDecodeTests(unittest.TestCase):
    def test_arrays_and_lists_decode_the_same_model(self):
        data = skinned_fixture()
        lists = nm.decode_model('m', 'All', data=data)
        arrays = nm.decode_model('m', 'All', data=data, as_arrays=True)
        for a, b in zip(lists['meshes'], arrays['meshes']):
            self.assertEqual(a['weights'], b['weights'])
            self.assertEqual(a['positions'], nm._tuples(b['positions']))
            self.assertEqual(a['triangles'], b['triangles'].tolist())
            self.assertEqual(b['skin_joint_array'].shape, (len(a['positions']), 8))
            self.assertNotIn('skin_joint_array', a)
        self.assertTrue(all(w for m in lists['meshes'] for w in m['weights']))

    def test_light_container_matches_the_full_section_table(self):
        data = skinned_fixture()
        full, light = read_container(data, 0, {}), read_container(data, 0, {}, light=True)
        for key in ('offset', 'declared_size', 'section_count', 'header_flags', 'table_end'):
            self.assertEqual(full[key], light[key])
        self.assertEqual([{k: v for k, v in s.items() if k != 'sha256_16'} for s in full['sections']], light['sections'])
        for bad in (data[:12], b'XXXX' + data[4:], data[:8] + struct.pack('<I', len(data)+1) + data[12:]):
            with self.assertRaises(ValueError) as a: read_container(bad, 0, {})
            with self.assertRaises(ValueError) as b: read_container(bad, 0, {}, light=True)
            self.assertEqual(str(a.exception), str(b.exception))

    def test_array_mesh_payload_equals_the_list_payload(self):
        data = skinned_fixture(3)
        for lod in ('All', 'Highest'):
            old = ld.mesh_payload(nm.decode_model('m', 'All', data=data), lod)
            new = ld.mesh_payload(nm.decode_model('m', 'All', data=data, as_arrays=True), lod)
            self.assertIsInstance(new['vertices'], np.ndarray)
            self.assertEqual(old['vertices'], [tuple(v) for v in new['vertices'].tolist()])
            self.assertEqual(old['normals'], [tuple(v) for v in new['normals'].tolist()])
            self.assertEqual(old['triangles'], [tuple(t) for t in new['triangles'].tolist()])
            self.assertEqual(old['uvs'], [tuple(v) for v in new['uvs']])
            for key in ('batches', 'skin_joints', 'skin_weights', 'rock_alpha', 'rock_alpha_sources', 'selected_mesh_indices', 'source_sha256'):
                self.assertEqual(old[key], new[key], key)
            from rc2016.viewer.viewport import prepare_viewport
            a, b = prepare_viewport(old), prepare_viewport(new)
            for x, y in zip(a[1], b[1]):
                self.assertEqual(bytes(x) if x is not None else None, bytes(y) if y is not None else None)
            self.assertEqual(a[2], b[2])

    def test_array_payload_without_uv_colour_or_weights(self):
        mesh = dict(index=0, positions=np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0]]), normals=np.array([[0., 0, 1]]*3),
                    triangles=np.array([[0, 1, 2]]), uv0=None, colors=[(1, 1, 1, .5)]*3, weights=[None]*3)
        decoded = dict(looks=[dict(index=0, lods=[dict(index=0, start=0, count=1, meshes=[0])])], mesh_record_count=1,
                       meshes=[mesh], source_sha256='x')
        new = ld.mesh_payload(decoded, 'All')
        listed = dict(mesh, positions=mesh['positions'].tolist(), normals=mesh['normals'].tolist(), triangles=[(0, 1, 2)])
        old = ld.mesh_payload(dict(decoded, meshes=[listed]), 'All')
        self.assertIsNone(new['uvs']); self.assertIsNone(old['uvs'])
        for key in ('skin_joints', 'skin_weights', 'rock_alpha'):
            self.assertEqual(old[key], new[key])


class LoaderTests(unittest.TestCase):
    def fake_loader(self, data):
        archive = SimpleNamespace(root=Path('.'), entries={'id': dict(type='model', path='a/b.model')},
                                  bytes=lambda key: (data, 'packed'), resolve=lambda path: [])
        loader = ld.SourcePreviewLoader(); loader.source_root = Path.cwd().resolve(); loader.archive = archive
        return loader

    def test_view_and_export_loads(self):
        data = skinned_fixture(5)
        loader = self.fake_loader(data)
        view = loader.load(Path.cwd(), 'id', 'a/b.model', 'All', 0, None, True)
        self.assertTrue(view['animation_pending'])
        self.assertIsInstance(loader.current['meshes'][0]['positions'], np.ndarray)
        self.assertIn('_prepared_viewport', view)
        export = loader.load(Path.cwd(), 'id', 'a/b.model', 'All', 0, None, False, purpose='export')
        self.assertNotIn('vertices', export); self.assertEqual(export['purpose'], 'export')
        self.assertIsInstance(loader.current['meshes'][0]['positions'], list)       # export.models.mesh_arrays reads lists
        self.assertIs(loader._recent[loader.identity]['decoded'], loader.current)
        self.assertEqual(loader.current['texture_list'], [])
        listed = nm.decode_model('m', 'All', data=data)
        self.assertEqual([m['weights'] for m in loader.current['meshes']], [m['weights'] for m in listed['meshes']])
        with self.assertRaises(ValueError): loader.load(Path.cwd(), 'id', 'a/b.model', purpose='other')

    def test_lock_is_free_while_decoding(self):
        data = skinned_fixture(6)
        loader = self.fake_loader(data)
        seen = []
        real = nm.decode_model
        def decode(*a, **k):
            seen.append(loader.lock.acquire(blocking=False))
            if seen[-1]: loader.lock.release()
            return real(*a, **k)
        with patch.object(ld, 'decode_model', side_effect=decode):
            loader.load(Path.cwd(), 'id', 'a/b.model', 'All', 0, None, True)
        self.assertEqual(seen, [True])

    def test_gfx_import_index_matches_the_linear_scan(self):
        entries = {str(i): dict(type=t, path=p) for i, (t, p) in enumerate([
            ('texture', 'ui/menu.gfx'), ('gfx', 'ui/a/Menu.gfx'), ('swf', 'ui/b/menu.swf'), ('gfx', 'ui/x.y.gfx'),
            ('swf', 'ui/hud'), ('gfx', 'ui/c/menu.gfx')])}
        loader = ld.SourcePreviewLoader(); loader.archive = SimpleNamespace(entries=entries)
        loader._gfx_bytes = lambda path: path
        for url in ('menu.gfx', 'MENU.swf', 'C:\\x\\menu', 'x.y.gfx', 'x.y.z', 'hud', 'hud.gfx', 'missing.gfx', 'x', ''):
            self.assertEqual(loader._gfx_import(url), reference_gfx_import(entries, url), url)

    def test_material_rules_reuse_recorded_translations(self):
        archive = SimpleNamespace(entries={'m': dict(type='material')}, resolve=lambda p: ['m'])
        refs = [dict(path='materialgraph/x.materialgraph')]
        with patch.object(ld, 'asset_references', return_value=refs), \
                patch('rc2016.viewer.native_glsl.selected_translation', side_effect=AssertionError('selected twice')):
            rules = ld.material_rules(archive, [dict(path='a.material')], {'a.material': None})
        self.assertEqual(rules['a.material']['graphs'], ['materialgraph/x.materialgraph'])


class ArchiveCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @patch.object(ar, 'FILE_HASH_MIN_BYTES', 0)
    def test_stat_file_hash_reuses_the_recorded_hash_until_the_file_changes(self):
        f = self.root/'big.bin'; f.write_bytes(b'x'*1000); sidecar = self.root/'hashes.json'
        self.assertEqual(ar.stat_file_hash(f, sidecar), ar.file_hash(f))
        with patch.object(ar, 'file_hash', side_effect=AssertionError('re-hashed')):
            self.assertEqual(ar.stat_file_hash(f, sidecar), ar.sha(b'x'*1000))
        f.write_bytes(b'y'*1001)
        self.assertEqual(ar.stat_file_hash(f, sidecar), ar.sha(b'y'*1001))
        sidecar.write_text('not json')
        self.assertEqual(ar.stat_file_hash(f, sidecar), ar.sha(b'y'*1001))
        with self.assertRaises(OSError): ar.stat_file_hash(self.root/'missing', sidecar)

    def test_locate_remembers_and_revalidates_the_root(self):
        game = self.root/'game'; found = game/'image0'/'archive'; found.mkdir(parents=True)
        for name in ar.METADATA + ('g00s000',): (found/name).write_bytes(b'')
        sidecar = self.root/'roots.json'
        first = ar.locate(game, sidecar)
        self.assertEqual(first, ar._locate_walk(game))
        with patch.object(ar, '_locate_walk', side_effect=AssertionError('walked')):
            self.assertEqual(ar.locate(game, sidecar), first)
        (found/'toc').unlink()
        with self.assertRaises(ValueError): ar.locate(game, sidecar)

    def make_archive(self):
        a = object.__new__(ar.Archive)
        a._memo, a._memo_bytes, a._memo_lock = ar.OrderedDict(), 0, threading.Lock()
        a.calls = []
        blobs = {k: b'1TAD' + bytes([n])*(100*(n+1)) + b'material/x.material\0' for n, k in enumerate('abcd')}
        a.bytes = lambda key: (a.calls.append(key) or blobs[key], 'h')
        return a, blobs

    def test_memo_is_bounded_and_returns_decompressed_bytes(self):
        a, blobs = self.make_archive()
        with patch.object(ar, 'ASSET_MEMO_BYTES', 450):
            self.assertEqual(a.asset_data('a'), blobs['a']); a.asset_data('a')
            self.assertEqual(a.calls, ['a'])
            a.asset_data('b'); a.asset_data('c')     # 124 + 224 + 324 > 450: 'a' and 'b' go
            self.assertLessEqual(a._memo_bytes, 450)
            a.asset_data('a'); self.assertEqual(a.calls.count('a'), 2)
        self.assertEqual([r['path'] for r in a.asset_references('d')], ['material/x.material'])
        self.assertIs(a.asset_references('d'), a.asset_references('d'))
        a._memo.clear(); a._memo_bytes = 0
        with patch.object(ar, 'ASSET_MEMO_MAX_ITEM', 10):
            a.asset_data('d'); self.assertNotIn(('data', 'd'), a._memo)
        stand_in = SimpleNamespace(bytes=lambda key: (blobs[key], 'h'))
        self.assertEqual(ar.asset_data(stand_in, 'b'), blobs['b'])

    def test_memo_threads_share_one_value(self):
        a, _blobs = self.make_archive()
        results = []
        threads = [threading.Thread(target=lambda: results.append(a.asset_data('c'))) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len({id(r) for r in results}), 1)


class TextureTests(unittest.TestCase):
    def test_cached_deswizzle_matches_the_loop(self):
        rng = np.random.default_rng(2)
        for width, height, fmt in ((4, 4, 71), (16, 8, 98), (20, 12, 28), (1, 1, 61), (64, 32, 77), (16, 8, 98)):
            _bw, _bh, pw, ph, bpb = tx._tile_layout(width, height, fmt)
            data = bytes(rng.integers(0, 256, pw*ph*bpb, dtype=np.uint8))
            self.assertEqual(tx.ps4_deswizzle_top(data, width, height, fmt), reference_deswizzle(data, width, height, fmt))
        self.assertFalse(tx._deswizzle_targets(16, 8).flags.writeable)
        self.assertIs(tx._deswizzle_targets(16, 8), tx._deswizzle_targets(16, 8))

    def header_archive(self):
        header = bytearray(36)
        struct.pack_into('<II4HHBBH', header, 0, 0, 0, 0, 0, 8, 8, 1, 0, 0, 71)
        struct.pack_into('<4B', header, 30, 1, 0, 0, 0)
        body = struct.pack('<4sIIHH', b'1TAD', 0, 16+12+36, 1, 0) + struct.pack('<III', tx.HEADER, 28, 36) + bytes(header)
        built = bytearray(body); struct.pack_into('<I', built, 28, 512)
        built = bytes(built) + bytes(range(256))*2
        archive = SimpleNamespace(entries={'t': dict(built_path='x.built')}, resolve=lambda p: ['t'],
                                  bytes=lambda key: (built, 'h'))
        return archive

    def test_texture_header_is_memoized_and_shared_with_top_mip_reads(self):
        archive = self.header_archive()
        with patch.object(tp, 'parse_header', wraps=tp.parse_header) as parse:
            first = tp.texture_header(archive, 'a.texture')
            self.assertEqual(tp.texture_header(archive, 'a.texture'), first)
            self.assertEqual(parse.call_count, 1)
            first['format'] = 0   # a caller's copy
            self.assertEqual(tp.texture_header(archive, 'a.texture')['format'], 71)
        fresh = self.header_archive()
        texture = tp.load_texture(fresh, 'a.texture')
        with patch.object(tp, 'parse_header', side_effect=AssertionError('parsed again')):
            self.assertEqual(tp.texture_header(fresh, 'a.texture')['sd_width'], texture['width'])

    def test_disk_cache_round_trip_rejects_damage_and_evicts(self):
        with tempfile.TemporaryDirectory() as folder:
            disk = tp.TextureDiskCache(folder, limit=10_000)
            archive = self.header_archive()
            archive.metadata = {'toc': 'a'*64}
            archive.entries['t'].update(occurrences=[dict(archive='g00s000', offset=0, size=1, available=True)])
            archive.texture_disk_cache = disk
            fresh = tp.load_texture(archive, 'a.texture')
            key = disk.key(archive, 'a.texture')
            with patch.object(tp, '_read_top_mip', side_effect=AssertionError('decoded again')):
                cached = tp.load_texture(archive, 'a.texture')
            self.assertEqual(cached, fresh)
            path = disk._file(key); blob = path.read_bytes()
            path.write_bytes(blob[:-1] + bytes([blob[-1] ^ 1]))
            self.assertIsNone(disk.get(key))
            archive.metadata = {'toc': 'b'*64}
            self.assertNotEqual(disk.key(archive, 'a.texture'), key)
            for n in range(60):
                disk.put(f'{n:064x}', dict(fresh, path=str(n)))
            self.assertLessEqual(sum(size for _p, size, _t in disk._scan()), 10_000)
            self.assertFalse(list(Path(folder).rglob('*.tmp')))

    def test_texture_cache_loads_each_path_once_across_threads(self):
        cache, calls, gate = {}, [], threading.Barrier(6)
        def slow(_archive, path):
            calls.append(path); return dict(path=path)
        def worker():
            gate.wait(); tp._cached_texture(None, cache, 'p')
        with patch.object(tp, 'load_texture', side_effect=slow):
            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads: t.start()
            for t in threads: t.join()
            values = {id(tp._cached_texture(None, cache, 'p')) for _ in range(3)}
        self.assertEqual(len(values), 1)
        with patch.object(tp, 'load_texture', side_effect=ValueError('bad')):
            self.assertIsNone(tp._cached_texture(None, cache, 'q'))
            with self.assertRaises(ValueError): tp._cached_texture(None, cache, 'r', tolerate=False)
        self.assertNotIn('r', cache)


if __name__ == '__main__':
    unittest.main()
