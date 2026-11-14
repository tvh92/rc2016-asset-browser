"""Rift Apart architecture glass (gbl_arch_glass): parameter and graph readers, the viewer entry, and real-install checks (RCRA_GAME)."""
import os
import struct
import tempfile
import unittest
from pathlib import Path

from rc2016.app.browser import rule_text
from rc2016.games.rcra import material as rmat
from rc2016.viewer import shading
from tests.test_rcra_texture import FakeSource, dat1, texture_asset

GAME = os.environ.get('RCRA_GAME')
KEYS = dict(soft=0xFD3F0F1D, adapt=0x0C0A6B30, gloss=0x24F4CA4C, rim=0x70137DA7, colour=0x74979B4A, mode=0x8E7EC8A1,
            emission=0x06F19114, floor=0xCAF31501)
OFFSETS = dict(soft=0, adapt=4, gloss=8, rim=12, colour=16, mode=20, emission=24, floor=28)   # the graph's byte offsets in cb2
DEFAULTS = (1.0, 0.0, 1.0, 0.25, 5.0, 0.0, 0.05, 0.4)
DOME = dict(soft=1.0, adapt=1.0, gloss=0.9, rim=0.2, colour=0.5, mode=1.0, emission=15.0, floor=0.4)   # trooper dome material values
SLOT_F0_GLOSS = 0x6376D191   # the graph's t8 slot: F0 rgb and gloss alpha
DEFAULT_TEXTURE = 'textures/default_pink_shader/default_pink_shader_c.texture'


def glass_graph():
    """A graph with the real section layout: slot table (unit order g=3, n=2, mask=0, c=1), parameter records, float defaults."""
    names = ['textures\\default_pink_shader\\default_pink_shader_g.texture', 'textures\\default_pink_shader\\default_pink_shader_n.texture',
             'textures\\default_pink_shader\\default_pink_shader_c.texture', 'textures\\default_pink_shader\\default_pink_shader_c.texture']
    slots = [(3, SLOT_F0_GLOSS), (2, rmat.SLOT_NORMAL), (0, 0xA6C3B1D5), (1, rmat.SLOT_BASE_COLOUR)]
    records = b''.join(struct.pack('<HHI', OFFSETS[name], 4, KEYS[name]) for name in KEYS)
    defaults = struct.pack('<8f', *DEFAULTS)
    sections = {rmat.GRAPH_SLOT_TABLE: bytes(64), rmat.GRAPH_PARAM_RECORDS: records, rmat.GRAPH_DEFAULTS: defaults}
    head = len(dat1(0x1592401A, sections))
    strings, rows = b'', b''
    for name, (unit, key) in zip(names, slots):
        rows += struct.pack('<IHHII', head + len(strings), 1, unit, key, 0x49A7866B)
        strings += name.encode() + b'\0'
    sections[rmat.GRAPH_SLOT_TABLE] = rows
    body = dat1(0x1592401A, sections, tail=strings)
    return struct.pack('<III', 0x1592401A, len(body), 0) + bytes(24) + body


def glass_material(values, slots):
    """A material with the dome's parameter records (key, offset in the value block) and texture slots."""
    names = list(values)
    records = b''.join(struct.pack('<HHI', 4 * i, 4, KEYS[name]) for i, name in enumerate(names))
    block = struct.pack('<%df' % len(names), *(values[name] for name in names))
    base = 40 + len(records)
    start = base + len(block)
    textures = b''.join(struct.pack('<II', 0, key) for key, _ in slots)
    end = start + len(textures)
    strings, patched = b'', b''
    for key, path in slots:
        patched += struct.pack('<II', len(strings), key)
        strings += path.encode() + b'\0'
    head = struct.pack('<10I', end + len(strings), len(names), base, (len(names) + 3) // 4, start, len(slots), start, end, 0, 0)
    body = dat1(rmat.MATERIAL_MAGIC, {rmat.FLAGS: struct.pack('<I', 0) + bytes(36),
                                      rmat.SERIALIZED: head + records + block + patched + strings})
    return struct.pack('<III', rmat.MATERIAL_MAGIC, len(body), 0) + bytes(24) + body


class GlassReaderTests(unittest.TestCase):
    def setUp(self):
        self.graph = glass_graph()
        self.material = glass_material(DOME, [(rmat.SLOT_BASE_COLOUR, 'm/dome_c.texture'), (rmat.SLOT_NORMAL, 'm/dome_n.texture'),
                                              (SLOT_F0_GLOSS, 'm/dome_g.texture')])

    def test_material_parameters_read_by_key(self):
        values = rmat.material_parameters(self.material)
        self.assertEqual(len(values), 8)
        self.assertAlmostEqual(values[KEYS['emission']][0], 15.0)
        self.assertAlmostEqual(values[KEYS['gloss']][0], 0.9, places=6)

    def test_graph_layout(self):
        rows, offsets, defaults = rmat.graph_layout(self.graph)
        self.assertEqual({unit: key for unit, key, _ in rows}, {0: 0xA6C3B1D5, 1: rmat.SLOT_BASE_COLOUR, 2: rmat.SLOT_NORMAL, 3: SLOT_F0_GLOSS})
        self.assertEqual(rows[0][2], 'textures/default_pink_shader/default_pink_shader_g.texture')
        self.assertEqual(offsets[KEYS['emission']], 24)
        for got, want in zip(defaults, DEFAULTS):
            self.assertAlmostEqual(got, want, places=6)

    def test_params_are_defaults_overwritten_by_material_values(self):
        params = rmat.arch_glass_params(self.graph, self.material)
        self.assertEqual(tuple(params), rmat.ARCH_GLASS_PARAMS)
        for name, key in zip(rmat.ARCH_GLASS_PARAMS, ('soft', 'adapt', 'gloss', 'rim', 'colour', 'mode', 'emission', 'floor')):
            self.assertAlmostEqual(params[name], DOME[key], places=6)
        partial = glass_material(dict(emission=3.0), [])    # unset parameters keep the graph default
        params = rmat.arch_glass_params(self.graph, partial)
        self.assertAlmostEqual(params['emission'], 3.0)
        self.assertEqual((params['colour_k'], params['rim_width']), (5.0, 0.25))
        self.assertAlmostEqual(params['alpha_floor'], 0.4, places=6)

    def test_bad_graph_is_rejected(self):
        with self.assertRaises(ValueError):
            rmat.graph_layout(glass_material(DOME, []))


class GlassEntryTests(unittest.TestCase):
    def setUp(self):
        tex, _ = texture_asset(hd_mips=0)
        self.material = glass_material(DOME, [(rmat.SLOT_BASE_COLOUR, 'm/dome_c.texture'), (rmat.SLOT_NORMAL, 'm/dome_n.texture')])
        plain = glass_material({}, [(rmat.SLOT_BASE_COLOUR, 'm/dome_c.texture')])
        assets = {'m/dome.material': self.material, 'm/plain.material': plain, 'm/dome_c.texture': tex, 'm/dome_n.texture': tex,
                  rmat.ARCH_GLASS_GRAPH: glass_graph(), 'g/other.materialgraph': b''}
        for name in ('_g', '_n', '_c'):
            assets['textures/default_pink_shader/default_pink_shader%s.texture' % name] = tex
        self.source = FakeSource(assets, refs={'m/dome.material': [rmat.ARCH_GLASS_GRAPH], 'm/plain.material': ['g/other.materialgraph']})
        self.materials = [dict(index=0, path='m/dome.material', name='dome'), dict(index=1, path='m/plain.material', name='plain')]

    def test_glass_material_gets_forward_rule_and_graph_units(self):
        cache = {}
        entries = rmat.material_textures(self.source, self.materials, cache)
        glass = entries[0]
        self.assertEqual(glass['forward']['kind'], 'rcra_arch_glass')
        self.assertEqual(glass['forward']['blend'], 'alpha_over')
        self.assertAlmostEqual(glass['forward']['params']['emission'], 15.0)
        self.assertEqual(glass['_window_c']['path'], 'm/dome_c.texture')
        self.assertEqual(glass['_n']['path'], 'm/dome_n.texture')
        self.assertEqual(glass['_c'], glass['_window_c'])
        # the slots the material leaves unbound take the graph's default textures
        self.assertEqual(glass['_window_g']['path'], 'textures/default_pink_shader/default_pink_shader_g.texture')
        self.assertEqual(glass['_pick']['path'], DEFAULT_TEXTURE)

    def test_other_graphs_keep_the_preview(self):
        plain = rmat.material_textures(self.source, self.materials, {})[1]
        self.assertNotIn('forward', plain)
        self.assertEqual(plain['_c']['path'], 'm/dome_c.texture')

    def test_rules_and_label(self):
        rules = rmat.material_rules(self.source, self.materials)
        self.assertEqual(rules['m/dome.material']['rule_kind'], rmat.GLASS_RULE_KIND)
        self.assertEqual(rules['m/plain.material']['rule_kind'], rmat.RULE_KIND)
        self.assertIn('Rift Apart glass', rule_text(rules['m/dome.material']))
        self.assertIn('gbl_arch_glass', rule_text(rules['m/dome.material']))

    def test_unreadable_graph_falls_back_to_the_preview(self):
        self.source.assets[rmat.ARCH_GLASS_GRAPH] = b'\0' * 80
        entry = rmat.material_textures(self.source, self.materials, {})[0]
        self.assertNotIn('forward', entry)
        self.assertEqual(entry['_c']['path'], 'm/dome_c.texture')

    def test_viewer_uniforms(self):
        forward = rmat.material_textures(self.source, self.materials, {})[0]['forward']
        kind, a, b, c, *_ = shading.glass_uniforms(forward)
        self.assertEqual(kind, shading.GLASS_KINDS['rcra_arch_glass'])
        self.assertEqual(tuple(round(x, 4) for x in a), (1.0, 1.0, 0.9, 0.2))
        self.assertEqual(tuple(round(x, 4) for x in b), (0.5, 1.0, 15.0, 0.4))
        self.assertEqual(c[0], shading.RCRA_ADAPTED_LUMINANCE)
        self.assertIn('else if (uKind == %d)' % kind, shading.GLASS_FRAGMENT)


@unittest.skipUnless(GAME, 'RCRA_GAME is not set')
class RealGlassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.games.rcra.source import RcraSource
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = RcraSource(GAME, Path(cls.temp.name) / 'out')

    @classmethod
    def tearDownClass(cls):
        cls.source.close()
        cls.temp.cleanup()

    def graph(self):
        return self.source.asset_data(next(k for k in self.source.resolve(rmat.ARCH_GLASS_GRAPH)
                                           if self.source.entries[k]['type'] == 'materialgraph'))

    def test_graph_layout_and_the_trooper_dome(self):
        rows, offsets, defaults = rmat.graph_layout(self.graph())
        self.assertEqual({unit: key for unit, key, _ in rows}, {0: 0xA6C3B1D5, 1: rmat.SLOT_BASE_COLOUR, 2: rmat.SLOT_NORMAL, 3: SLOT_F0_GLOSS})
        self.assertEqual(sorted(offsets.values()), [0, 4, 8, 12, 16, 20, 24, 28])
        self.assertAlmostEqual(defaults[6], 0.05, places=6)
        dome = 'characters/enemy/enm_neo_nefarious_trooper/materials/enm_neo_nefarious_trooper_dome.material'
        params = rmat.arch_glass_params(self.graph(), self.source.asset_data(self.source.resolve(dome)[0]))
        for name, key in zip(rmat.ARCH_GLASS_PARAMS, ('soft', 'adapt', 'gloss', 'rim', 'colour', 'mode', 'emission', 'floor')):
            self.assertAlmostEqual(params[name], DOME[key], places=5)

    def test_swarmer_dome_is_glass_and_its_other_materials_are_not(self):
        from rc2016.games.rcra.model import decode_model
        path = 'characters/enemy/enm_neo_nefarious_swarmer/enm_neo_nefarious_swarmer.model'
        decoded = decode_model(path, data=self.source.asset_data(self.source.resolve(path)[0]), as_arrays=True)
        entries = rmat.material_textures(self.source, decoded['materials'], {})
        forward = [e for e in entries.values() if e.get('forward')]
        # Blended materials draw their translated programs; the dome keeps the decoded glass rule as the fallback.
        self.assertTrue(forward and all(e['forward']['kind'] == rmat.TRANSLATED_KIND for e in forward))
        glass = [e for e in forward if e['forward'].get('fallback')]
        self.assertEqual(len(glass), 1)
        self.assertEqual(glass[0]['forward']['fallback']['kind'], 'rcra_arch_glass')
        self.assertTrue(all(glass[0][role] for role in ('_pick', '_window_c', '_n', '_window_g')))


if __name__ == '__main__':
    unittest.main()
