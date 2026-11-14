"""Rift Apart textures and material slots: synthetic assets, plus real-install checks (RCRA_GAME)."""
import os
import struct
import tempfile
import unittest
from pathlib import Path

from rc2016.app.browser import rule_text
from rc2016.games.rcra import material as rmat
from rc2016.games.rcra import texture as rt

GAME = os.environ.get('RCRA_GAME')


def dat1(kind, sections, tail=b''):
    header = 16 + 12 * len(sections)
    table, body = b'', b''
    for tag, data in sections.items():
        table += struct.pack('<III', tag, header + len(body), len(data))
        body += data
    return struct.pack('<4sIIHH', b'1TAD', kind, header + len(body), len(sections), 0) + table + body + tail


def texture_asset(fmt=71, sd=(8, 8), sd_mips=2, hd=(16, 16), hd_mips=1, array=1, faces=1):
    sd_len = sum(rt.chain_sizes(fmt, *sd, sd_mips)) * array * faces
    hd_len = sum(rt.chain_sizes(fmt, *hd, hd_mips)) * array * faces if hd_mips else 0
    header = bytearray(44)
    struct.pack_into('<IIHHHHHBBH', header, 0, sd_len, hd_len, *(hd if hd_mips else sd), *sd, array, 16, 0, fmt)
    header[30], header[32] = sd_mips, hd_mips
    payload = bytes(i % 251 for i in range(sd_len))
    body = dat1(rt.TEXTURE_MAGIC, {rt.HEADER_SECTION: bytes(header)})
    return struct.pack('<III', rt.TEXTURE_MAGIC, len(body), sd_len) + bytes(24) + body + payload, hd_len


def material_asset(slots):
    strings, records = b'', b''
    for key, path in slots:
        records += struct.pack('<II', len(strings), key)
        strings += path.encode() + b'\0'
    start = 40
    end = start + len(records)
    head = struct.pack('<10I', end + len(strings), 0, 0, 0, start, len(slots), start, end, 0, 0)
    body = dat1(rmat.MATERIAL_MAGIC, {rmat.FLAGS: struct.pack('<I', 5) + bytes(36), rmat.SERIALIZED: head + records + strings})
    return struct.pack('<III', rmat.MATERIAL_MAGIC, len(body), 0) + bytes(24) + body


class FakeSource:
    """The slice of RcraSource the texture/material functions use."""

    def __init__(self, assets, streams=None, refs=None):
        self.assets, self.streams, self.refs = assets, streams or {}, refs or {}
        self.entries = {p: dict(type=p.rsplit('.', 1)[-1]) for p in assets}

    def resolve(self, path):
        return [path] if path in self.assets else []

    def asset_data(self, key):
        return self.assets[key]

    def texture_stream(self, key):
        return self.streams.get(key)

    def asset_references(self, key):
        return [dict(path=p) for p in self.refs.get(key, ())]


class TextureTests(unittest.TestCase):
    def test_header_and_sd_top_mip(self):
        data, _hd = texture_asset(hd_mips=0)
        info = rt.parse_header(data)
        self.assertEqual((info['format'], info['sd_width'], info['sd_mips'], info['faces'], info['stream_size']), (71, 8, 2, 1, 0))
        source = FakeSource({'t.texture': data})
        texture = rt.read_top_mip(source, 't.texture', full=True)
        self.assertEqual((texture['width'], texture['height'], len(texture['pixels'])), (8, 8, 32))
        self.assertEqual(texture['pixels'], data[info['payload_offset']:info['payload_offset'] + 32])

    def test_hd_stream_is_used_for_full_and_small_sd(self):
        data, hd_len = texture_asset()
        stream = bytes(range(hd_len % 256)) * 0 + bytes((i * 3) % 256 for i in range(hd_len))
        source = FakeSource({'t.texture': data}, {'t.texture': stream})
        texture = rt.read_top_mip(source, 't.texture', full=False)    # SD 8 px < MIN_PREVIEW_SIZE: stream
        self.assertEqual((texture['width'], texture['pixels']), (16, stream[:128]))
        self.assertEqual(texture['full_size'], (16, 16))
        self.assertEqual(rt.texture_header(source, 't.texture'),
                         dict(format=71, hd_width=16, hd_height=16, sd_width=8, sd_height=8, array_size=1))

    def test_arrays_and_cube_faces_are_slices(self):
        data, _ = texture_asset(hd_mips=0, array=2)
        self.assertEqual(len(rt.read_top_mip(FakeSource({'a.texture': data}), 'a.texture', True)['slices']), 2)
        data, _ = texture_asset(hd_mips=0, faces=6)
        info = rt.parse_header(data)
        self.assertEqual(info['faces'], 6)
        self.assertEqual(len(rt.read_top_mip(FakeSource({'c.texture': data}), 'c.texture', True)['slices']), 6)

    def test_rejects_wrong_sizes(self):
        data, _ = texture_asset(hd_mips=0)
        with self.assertRaisesRegex(ValueError, 'bytes after its header'):
            rt.parse_header(data + b'x')
        with self.assertRaises(ValueError):
            rt.parse_header(b'\0' * 80)

    def test_data_formats_size_but_do_not_preview(self):
        data, _ = texture_asset(fmt=16, hd_mips=0)
        self.assertEqual(rt.parse_header(data)['sd_size'], (64 + 16) * 8)
        with self.assertRaisesRegex(ValueError, 'not previewable'):
            rt.load_texture(FakeSource({'v.texture': data}), 'v.texture')

    def test_export_writes_asset_stream_and_dds(self):
        data, hd_len = texture_asset()
        stream = bytes(hd_len)
        source = FakeSource({'x/t_c.texture': data}, {'x/t_c.texture': stream})
        with tempfile.TemporaryDirectory() as folder:
            rows = rt.export_textures(source, ['x/t_c.texture'], folder, {'texture', 'dds'})
            self.assertIsNone(rows[0]['error'])
            names = sorted(Path(f).name for f in rows[0]['files'])
            self.assertEqual(names, ['t_c.dds', 't_c.texture', 't_c.texture.stream'])
            self.assertEqual((Path(folder) / 't_c.texture.stream').read_bytes(), stream)


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.tex, _ = texture_asset(hd_mips=0)
        self.material = material_asset([(rmat.SLOT_G, 'm/a_g.texture'), (rmat.SLOT_NORMAL, 'm/a_n.texture'),
                                        (rmat.SLOT_BASE_COLOUR, 'm/a_c.texture')])
        self.source = FakeSource({'m/a.material': self.material, 'm/a_c.texture': self.tex, 'm/a_n.texture': self.tex},
                                 refs={'m/a.material': ['g/basic.materialgraph', 'm/a_c.texture'],
                                       'g/basic.materialgraph': ['g/default_c.texture']})
        self.source.entries['g/basic.materialgraph'] = dict(type='materialgraph')
        self.source.assets['g/basic.materialgraph'] = b''

    def test_slots_and_flags(self):
        self.assertEqual(rmat.material_slots(self.material)[2], (rmat.SLOT_BASE_COLOUR, 'm/a_c.texture'))
        self.assertEqual(rmat.material_flags(self.material), 5)

    def test_preview_binds_only_colour_and_normal(self):
        cache = {}
        entries = rmat.material_textures(self.source, [dict(index=0, path='m/a.material', name='a')], cache)
        entry = entries[0]
        self.assertEqual(entry['_c']['path'], 'm/a_c.texture')
        self.assertEqual(entry['_n']['path'], 'm/a_n.texture')
        self.assertIsNone(entry['_g'])
        self.assertTrue(entry['rcra_preview'])
        self.assertIn('m/a_c.texture', cache)

    def test_texture_list_and_rules(self):
        rows = {r['path']: r for r in rmat.model_texture_list(self.source, [dict(index=0, path='m/a.material', name='a')])}
        self.assertEqual(set(rows), {'m/a_g.texture', 'm/a_n.texture', 'm/a_c.texture', 'g/default_c.texture'})
        self.assertTrue(rows['g/default_c.texture']['via_graph'])
        rules = rmat.material_rules(self.source, [dict(index=0, path='m/a.material')])
        self.assertEqual(rules['m/a.material']['rule_kind'], rmat.RULE_KIND)
        self.assertIn('Rift Apart preview', rule_text(rules['m/a.material']))
        self.assertIn('basic', rule_text(rules['m/a.material']))


@unittest.skipUnless(GAME, 'RCRA_GAME is not set')
class RealTextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.games.rcra.source import RcraSource
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = RcraSource(GAME, Path(cls.temp.name) / 'out')

    @classmethod
    def tearDownClass(cls):
        cls.source.close()
        cls.temp.cleanup()

    def test_ratchet_boots(self):
        material = 'material/characters/hero/hero_ratchet_boots/hero_ratchet_boots.material'
        slots = dict(rmat.material_slots(self.source.asset_data(self.source.resolve(material)[0])))
        self.assertTrue(slots[rmat.SLOT_BASE_COLOUR].endswith('hero_ratchet_boots_c.texture'))
        texture = rt.load_texture(self.source, slots[rmat.SLOT_BASE_COLOUR], hd=True)
        self.assertEqual((texture['width'], texture['height'], texture['format'], texture['srgb']), (2048, 2048, 99, True))
        normal = rt.load_texture(self.source, slots[rmat.SLOT_NORMAL], hd=True)
        self.assertFalse(normal['srgb'])

    def test_cube_map(self):
        info = rt.parse_header(self.source.asset_data(self.source.resolve('required/atmospheres/default_day/default_day.texture')[0]))
        self.assertEqual(info['faces'], 6)


if __name__ == '__main__':
    unittest.main()
