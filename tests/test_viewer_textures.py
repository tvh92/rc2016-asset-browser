import os
from pathlib import Path
import struct
import unittest

from rc2016.viewer import textures as tp
from rc2016.viewer.loader import mesh_payload

# Game source assets from the research workspace (RC2016_RESEARCH); they are not in git.
MATERIALS = Path(os.environ.get('RC2016_RESEARCH', 'missing')) / 'Animation/pipeline/Research/Materials'


class FakeArchive:
    """Uncompressed '1TAD' blobs keyed by path; enough for reference scanning."""

    def __init__(self, files):
        self.entries, self.by_path, self.blobs = {}, {}, {}
        for number, (path, (kind, data)) in enumerate(files.items()):
            key = f'id{number}'
            self.entries[key] = dict(type=kind, path=path, built_path=path + '.built')
            self.by_path[path] = [key]
            self.blobs[key] = b'1TAD' + data

    def resolve(self, path):
        return self.by_path.get(path, [])

    def bytes(self, key):
        return self.blobs[key], 'hash'


def dat1(strings, sections):
    """DAT1 body after FakeArchive's '1TAD' magic: header, section table, NUL-separated strings, sections.

    ``sections(offsets)`` gets each string's offset in the full blob and returns [(tag, bytes)].
    """
    count = len(sections([0] * len(strings)))
    at, cursor, text = [], 16 + 12 * count + 1, b'\0'
    for s in strings:
        at.append(cursor)
        text += s.encode() + b'\0'
        cursor += len(s) + 1
    rows, body, offset = b'', b'', 16 + 12 * count + len(text)
    for tag, data in sections(at):
        rows += struct.pack('<III', tag, offset, len(data))
        body += data
        offset += len(data)
    return struct.pack('<IIHH', 0, offset, count, 0) + rows + text + body


def slot_rows(rows):
    return b''.join(struct.pack('<IIQ', name, 1, slot) for name, slot in rows)


def flags(word):
    return bytes(16) + struct.pack('<I', word) + bytes(16)


class TexturePreviewTests(unittest.TestCase):
    def test_stale_self_lit_screen_exact_slots_and_t0_forward(self):
        from unittest.mock import patch
        from rc2016.viewer.shading import glass_uniforms
        from rc2016.viewer.viewport import ModelViewport
        graph = 'materialgraph/environment/nebula/ambient/neb_amb_screen_01/mo_neb_amb_screen_01.materialgraph'
        self.assertEqual(tp._graph_rule([graph]), dict(self_lit_key=None, screen=True))
        self.assertEqual(tp._graph_rule([graph.replace('.materialgraph', '_extra.materialgraph')]), {})
        slots = (0x628AD5A840ED6C83, 0x49A7866B0056CBD0, 0x49A7866B7A530738,
                 0x49A7866B4AC18E9F, 0x49A7866B5DBA9ADC)
        paths = ['t/timeline.texture', 't/source_colour.texture', 't/screen_mask.texture',
                 't/noise_a.texture', 't/noise_b.texture']
        blob = dat1(paths, lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows(list(zip(at, slots))))])
        mat = dat1([graph, 't/stale_c.texture', 't/mask_override.texture'], lambda at: [
            (tp.MATERIAL_SLOT_SECTION, slot_rows([(at[1], 0x49A7866BABDAD780), (at[2], slots[2])])),
            (tp.MATERIAL_FLAGS_SECTION, flags(6))])
        archive = FakeArchive({'m/s.material': ('material', mat), graph: ('materialgraph', blob)})
        defaults = {0x4E14D6BE: [1., .8, .4], 0x54CC2EDE: [.2, 1., .3], 0x5AEFB7E9: .15,
                    0x33648910: 1., 0x901F1F8A: 1024., 0xD5CE3EFE: 1., 0x1F4CDDD9: .5,
                    0xCD131891: 1., 0xD7B41D33: .1}
        params = dict(parameters=[], unsupported_parameters=[
            dict(key_hash=0x66EFA55B, size=4, raw_hex=struct.pack('<f', 99.).hex()),
            dict(key_hash=0xCD131891, size=4, raw_hex=struct.pack('<f', 0.).hex())])
        def tex(_archive, path):
            return dict(path=path, format=28, width=2, height=1, srgb=False,
                        pixels=bytes((64, 64, 64, 255, 128, 128, 128, 255)))
        with (patch('rc2016.shaders.families.graph_parameter_tables',
                    return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params),
              patch.object(tp, 'load_texture', side_effect=tex)):
            e = tp.material_textures(archive, [dict(index=0, path='m/s.material')], {})[0]
        self.assertEqual(e['k'], 0.)
        self.assertEqual(e['forward']['time'], 0.)
        self.assertEqual(e['forward']['time_input'], 'CB0 dword 99')
        self.assertEqual(e['forward']['params']['intensity_a'], 0.)
        self.assertEqual(e['forward']['blend'], 'alpha_over')
        self.assertTrue(e['forward']['two_sided'])
        self.assertAlmostEqual(e['forward']['params']['grid'], 1024 * 96 / 255)
        self.assertEqual([e[k]['path'] for k in ('_c', '_n', '_pick', '_g')],
                         [paths[1], 't/mask_override.texture', paths[3], paths[4]])
        kind, a, b, c, d = glass_uniforms(e['forward'])
        self.assertEqual(kind, 3); self.assertEqual(a, (1., .8, .4, 0.))
        self.assertAlmostEqual(c[2], 1024 * 96 / 255)
        class Upload:
            def _upload_texture(self, texture): return 17
        uploaded = ModelViewport._gl_entry(Upload(), e, {})
        self.assertEqual(uploaded['forward'], e['forward'])
        self.assertEqual(uploaded['_c'], (17, False))

    def test_stale_self_lit_colored_writer_keeps_own_key_and_hero_unruled(self):
        from unittest.mock import patch
        graphs = ('required/materials/basic_normal_coloredgloss_emissive.materialgraph',
                  'materialgraph/character/hero/hero_bright_center_light/hero_bright_center_light_variable.materialgraph')
        for graph in graphs:
            self.assertEqual(tp._graph_rule([graph]), {})  # no speculative new rule
            mat = dat1([graph], lambda at: [])
            archive = FakeArchive({'m/a.material': ('material', mat), graph: ('materialgraph', dat1([], lambda at: []))})
            own = 0x06F19114 if 'coloredgloss' in graph else 0xB7305C21
            params = dict(parameters=[], unsupported_parameters=[
                dict(key_hash=k, size=4, raw_hex=struct.pack('<f', v).hex())
                for k, v in ((0x66EFA55B, 99.), (0xF8C61683, 77.), (0x06F19114, 3.))])
            with (patch('rc2016.shaders.families.graph_parameter_tables',
                        return_value=([[dict(key_hash=own)]], {own: 1.})),
                  patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
                got = tp.material_texture_paths(archive, 'm/a.material')
            self.assertEqual(got['k'], 3. if own == 0x06F19114 else 0.)

    def test_foliage_exact_grey_slots_defaults_and_stale_keys(self):
        from unittest.mock import patch
        graph = 'materialgraph/global/basic_foliage/basic_foliage.materialgraph'
        rule = tp._graph_rule([graph])
        self.assertEqual(rule['g_mode'], 'grey'); self.assertIsNone(rule['self_lit_key'])
        self.assertTrue(rule['foliage']); self.assertNotIn('g_switch', rule)
        for name in ('basic_emissive_foliage', 'basic_foliage_extra', 'jn_basic_foliage'):
            self.assertEqual(tp._graph_rule([graph.replace('basic_foliage.materialgraph', name + '.materialgraph')]), {})
        c, g, n = 0x49A7866BABDAD780, 0x49A7866B1576D848, 0x49A7866B7AE34E7A
        blob = dat1(['t/default_c.texture', 't/default_g.texture', 't/default_n.texture'],
                    lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows(list(zip(at, (c, g, n)))))])
        material = dat1([graph, 't/spec_named_c.texture', 't/albedo_no_suffix.texture'],
                       lambda at: [(tp.MATERIAL_SLOT_SECTION, slot_rows([(at[1], g), (at[2], c)]))])
        archive = FakeArchive({'m/leaf.material': ('material', material), graph: ('materialgraph', blob)})
        params = dict(parameters=[], unsupported_parameters=[
            dict(key_hash=0x62835AF9, size=4, raw_hex=struct.pack('<f', 0.0).hex()),
            dict(key_hash=0x66EFA55B, size=4, raw_hex=struct.pack('<f', 20.0).hex()),
            dict(key_hash=0x7107A2D8, size=4, raw_hex=struct.pack('<f', 1.0).hex())])
        defaults = {0x62835AF9: 1.0, 0x24F4CA4C: 1.0}
        with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params),
              patch.object(tp, 'load_texture', side_effect=lambda _, p: dict(path=p, pixels=b'', format=61))):
            paths = tp.material_texture_paths(archive, 'm/leaf.material')
            entry = tp.material_textures(archive, [dict(index=0, path='m/leaf.material')], {})[0]
        self.assertEqual(paths['_g'], 't/spec_named_c.texture')
        self.assertEqual(paths['_c'], 't/albedo_no_suffix.texture')
        self.assertEqual(paths['_n'], 't/default_n.texture')
        self.assertEqual(paths['g_mode'], 'grey'); self.assertEqual(paths['k'], 0.0)
        self.assertEqual(paths['spec'], 0.0); self.assertEqual(paths['gloss'], 1.0)
        self.assertTrue(entry['foliage']); self.assertEqual(entry['_g']['path'], paths['_g'])
        self.assertEqual(paths['alpha_cut'], tp.FOLIAGE_ALPHA_CUTOFF)
        self.assertEqual(entry['alpha_cut'], tp.FOLIAGE_ALPHA_CUTOFF)

    def test_leaf_cutout_is_exact_per_graph_and_off_for_other_graphs(self):
        base = 'materialgraph/environment/global/plant/gbl_plant_tree_foliage_transparent_01/'
        graphs = (base + 'rp_nov_plant_tree_foliage_transparent.materialgraph',
                  base + 'gbl_plant_tree_foliage_transparent_01.materialgraph',
                  'materialgraph/global/basic_foliage/basic_foliage.materialgraph')
        for graph in graphs:
            with self.subTest(graph=graph):
                self.assertTrue(tp._graph_rule([graph])['alpha_cut'])
                self.assertEqual(tp._graph_rule([graph.replace('.materialgraph', '_extra.materialgraph')]), {})
        # The tree graphs only cut out; their g/n/cma/cmb terms keep the generic reading.
        self.assertEqual(tp._graph_rule([graphs[0]]), dict(alpha_cut=True))
        self.assertEqual(tp._graph_rule([graphs[1]]), dict(alpha_cut=True))
        # The split-face sibling and other foliage graphs have no decoded writer here.
        self.assertEqual(tp._graph_rule(['materialgraph/global/basic_foliage_split_face_texture/basic_foliage_split_face_texture.materialgraph']), {})
        self.assertEqual(tp.FOLIAGE_ALPHA_CUTOFF, 0.5)   # inferred default, see the constant's comment

    def test_tree_material_reads_alpha_cut_without_changing_generic_roles(self):
        from unittest.mock import patch
        graph = 'materialgraph/environment/global/plant/gbl_plant_tree_foliage_transparent_01/rp_nov_plant_tree_foliage_transparent.materialgraph'
        blob = dat1(['t/leaf_c.texture', 't/leaf_n.texture'], lambda at: [])
        material = dat1([graph, 't/leaf_c.texture'], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(0x23C))])
        archive = FakeArchive({'m/leaf.material': ('material', material), graph: ('materialgraph', blob)})
        with patch.object(tp, 'load_texture', side_effect=lambda _, p: dict(path=p, pixels=b'', format=61)):
            paths = tp.material_texture_paths(archive, 'm/leaf.material')
            entry = tp.material_textures(archive, [dict(index=0, path='m/leaf.material')], {})[0]
        self.assertEqual(paths['alpha_cut'], 0.5); self.assertEqual(entry['alpha_cut'], 0.5)
        self.assertEqual(paths['g_mode'], 'colored'); self.assertNotIn('foliage', paths)
        self.assertEqual(tp.blend_state(0x23C), dict(blend='opaque', two_sided=True))   # opaque, two-sided: alpha test, no blend

    def test_glow_mask_graphs_are_distinct_and_bind_serialized_slots(self):
        from unittest.mock import patch
        slots = (0x49A7866BABDAD780, 0x49A7866B1576D848, 0x49A7866B7AE34E7A,
                 0x49A7866B0005F1E9, 0x49A7866BE0B8D722)
        defaults = {0x06F19114: 1.0, 0x62835AF9: 1.0, 0x24F4CA4C: 1.0,
                    0x1AAD6A99: [0.2, 0.4, 0.6], 0x5DB5EFC7: 0.04,
                    0x33BD9BA6: 0.1, 0x8A43287F: 1.0, 0x9E0BA553: 0.0}
        for suffix, kind in (('', 1), ('_02', 2)):
            graph = f'required/materials/jn_basic_normal_gloss_glow_mask{suffix}.materialgraph'
            with self.subTest(graph=graph):
                self.assertEqual(tp._graph_rule([graph])['glow'], kind)
                self.assertEqual(tp._graph_rule([graph.replace(suffix + '.materialgraph', suffix + '_extra.materialgraph')]), {})
                inputs = ['t/default_c.texture', 't/default_g.texture', 't/default_n.texture',
                          't/default_mask.texture', 't/default_parallax.texture']
                graph_blob = dat1(inputs, lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows(list(zip(at, slots))))])
                material = dat1([graph, 't/mask_wrongly_named_c.texture', 't/colour_with_no_suffix.texture'],
                    lambda at: [(tp.MATERIAL_SLOT_SECTION, slot_rows([(at[1], slots[3]), (at[2], slots[0])]))])
                archive = FakeArchive({'m/a.material': ('material', material), graph: ('materialgraph', graph_blob)})
                params = dict(parameters=[], unsupported_parameters=[
                    dict(key_hash=0x66EFA55B, size=4, raw_hex=struct.pack('<f', 99.0).hex()),
                    dict(key_hash=0x06F19114, size=4, raw_hex=struct.pack('<f', 3.0).hex()),
                    dict(key_hash=0x5DB5EFC7, size=4, raw_hex=struct.pack('<f', 0.0).hex()),
                    dict(key_hash=0x9E0BA553, size=4, raw_hex=struct.pack('<f', 0.75).hex())])
                with (patch('rc2016.shaders.families.graph_parameter_tables',
                            return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
                      patch('rc2016.formats.materials.extract_material_parameters', return_value=params),
                      patch.object(tp, 'load_texture', side_effect=lambda _, p: dict(path=p, pixels=b'', format=61))):
                    paths = tp.material_texture_paths(archive, 'm/a.material')
                    entry = tp.material_textures(archive, [dict(index=3, path='m/a.material')], {})[3]
                self.assertEqual(paths['k'], 3.0)  # own K; stored generic key must not win
                self.assertEqual(paths['g_mode'], 'grey')
                self.assertEqual(paths['_c'], 't/colour_with_no_suffix.texture')
                self.assertEqual(paths['_h'], 't/mask_wrongly_named_c.texture')
                self.assertEqual(paths['glow']['kind'], kind)
                self.assertEqual(paths['glow']['depth'], 0.0)  # zero is an intentional override
                self.assertEqual(paths['glow']['vertex_fade'], 0.75)
                self.assertEqual(entry['glow'], paths['glow'])
                self.assertEqual(entry['_h']['path'], paths['_h'])
                if kind == 1:
                    self.assertEqual(entry['_glow_parallax']['path'], inputs[4])
                    self.assertEqual(paths['glow']['colour'], [0.2, 0.4, 0.6])
                else:
                    self.assertNotIn('_glow_parallax', paths)
                    self.assertNotIn('_glow_parallax', entry)
                    self.assertEqual(paths['self_lit'], 'glow_mask02')
        from rc2016.viewer.shading import GLOW_PARALLAX_TEXTURE_UNIT
        self.assertEqual(GLOW_PARALLAX_TEXTURE_UNIT, 13)

    def test_parallax_occlusion_exact_rule(self):
        from unittest.mock import patch
        graph = 'materialgraph/global/basic_parallax_occlusion/basic_parallax_occlusion.materialgraph'
        prefix = graph.removesuffix('materialgraph')
        rule = tp._graph_rule([graph])
        self.assertEqual(rule['g_mode'], 'grey')
        self.assertIsNone(rule['self_lit_key'])
        self.assertNotIn('g_switch', rule)
        for other in ('basic_parallax_occlusion_extra', 'jn_basic_parallax_occlusion_alpha', 'gbl_prop_turbine_01'):
            self.assertEqual(tp._graph_rule([graph.replace('basic_parallax_occlusion.materialgraph', other + '.materialgraph')]), {})
        # api5 height is misleadingly named _c and appears first. Actual C/G/N slots must win.
        c, g, n = 0x49A7866BABDAD780, 0x49A7866B1576D848, 0x49A7866B7AE34E7A
        mat = dat1([graph, 't/height_c.texture', 't/albedo_wrong_suffix.texture', 't/spec_wrong_suffix.texture', 't/normal_wrong_suffix.texture'],
            lambda at: [(tp.MATERIAL_SLOT_SECTION, slot_rows([(at[1], 0x49A7866BB521DFCB), (at[2], c), (at[3], g), (at[4], n)]))])
        blob = dat1([], lambda at: [])
        archive = FakeArchive({'m/p.material': ('material', mat), graph: ('materialgraph', blob)})
        params = dict(parameters=[], unsupported_parameters=[
            dict(key_hash=0x62835AF9, size=4, raw_hex=struct.pack('<f', .25).hex()),
            dict(key_hash=0x24F4CA4C, size=4, raw_hex=struct.pack('<f', .5).hex()),
            dict(key_hash=0x7107A2D8, size=4, raw_hex=struct.pack('<f', 1).hex()),
            dict(key_hash=0x66EFA55B, size=4, raw_hex=struct.pack('<f', 99).hex())])
        with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([], {})),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
            got = tp.material_texture_paths(archive, 'm/p.material')
        self.assertEqual(got['g_mode'], 'grey')
        self.assertEqual(got['k'], 0)
        self.assertEqual((got['spec'], got['gloss']), (.25, .5))
        self.assertEqual((got['_c'], got['_g'], got['_n']),
                         ('t/albedo_wrong_suffix.texture', 't/spec_wrong_suffix.texture', 't/normal_wrong_suffix.texture'))

    def _bng_fixture(self, graph, material_params=None, material_slots=None):
        from unittest.mock import patch
        c, g, n, detail = (0x49A7866BABDAD780, 0x49A7866B1576D848,
                           0x49A7866B7AE34E7A, 0x49A7866B45A52060)
        strings = ['t/default_c.texture', 't/default_g.texture',
                   't/default_n.texture', 't/default_detail.texture']
        graph_blob = dat1(strings, lambda at: [(tp.GRAPH_SLOT_SECTION,
            slot_rows([(at[0], c), (at[1], g), (at[2], n), (at[3], detail)]))])
        mat_strings = [graph, 't/slot_n_named_detail.texture', 't/slot_detail_named_normal.texture']
        mat_blob = dat1(mat_strings, lambda at: [(tp.MATERIAL_SLOT_SECTION,
            slot_rows(material_slots or [(at[1], n), (at[2], detail)]))])
        archive = FakeArchive({'m/a.material': ('material', mat_blob), graph: ('materialgraph', graph_blob)})
        defaults = {0x66EFA55B: 2.0, 0x62835AF9: 1.0, 0x24F4CA4C: 1.0,
                    0x34493800: 1.0, 0xAD4069BA: 1.0, 0x49AFC3A0: 0.75,
                    0x5FD56522: 10.0, 0xAAC47D17: 1.0, 0xB7BAC19F: 1.0,
                    0xD94E1303: 10.0, 0x9ACE7120: 3.0, 0x07CC3BD9: 1.0}
        params = material_params or dict(parameters=[], unsupported_parameters=[])
        return archive, defaults, params

    def test_basic_normal_gloss_six_exact_rules_and_fallback_boundary(self):
        from unittest.mock import patch
        cases = (
            ('emissive', 1), ('emissive_detail', 2), ('fresnel_detail', 3),
            ('emissive_colored_spec', 4), ('aniso_incan', 5), ('specfresnel_emissive', 6),
        )
        fallback_index = next(i for i, (path, _) in enumerate(tp.GRAPH_RULES)
                              if path == 'required/materials/basic_normal_gloss')
        self.assertEqual([path for path, _ in tp.GRAPH_RULES[fallback_index-6:fallback_index]],
                         [f'required/materials/basic_normal_gloss_{suffix}.' for suffix, _ in cases])
        for suffix, kind in cases:
            with self.subTest(suffix=suffix):
                graph = f'required/materials/basic_normal_gloss_{suffix}.materialgraph'
                archive, defaults, params = self._bng_fixture(graph)
                with (patch('rc2016.shaders.families.graph_parameter_tables',
                            return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
                      patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
                    got = tp.material_texture_paths(archive, 'm/a.material')
                self.assertEqual(got['bng']['kind'], kind)
                self.assertEqual(got['g_mode'], 'grey')

        # The exact dotted rule must not capture another graph sharing the prefix.
        graph = 'required/materials/basic_normal_gloss_emissive_variant.materialgraph'
        archive, defaults, params = self._bng_fixture(graph)
        with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
            fallback = tp.material_texture_paths(archive, 'm/a.material')
        self.assertEqual(fallback['g_mode'], 'grey')  # family fallback remains active
        self.assertNotIn('bng', fallback)
        self.assertNotIn('_bng_detail', fallback)

    def test_basic_normal_gloss_detail_uses_serialized_slots_not_suffixes(self):
        from unittest.mock import patch
        graph = 'required/materials/basic_normal_gloss_fresnel_detail.materialgraph'
        archive, defaults, params = self._bng_fixture(graph)
        with (patch('rc2016.shaders.families.graph_parameter_tables',
                    return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
            got = tp.material_texture_paths(archive, 'm/a.material')
        # Material texture suffixes are intentionally swapped; the serialized slot identity wins.
        self.assertEqual(got['_n'], 't/slot_n_named_detail.texture')
        self.assertEqual(got['_bng_detail'], 't/slot_detail_named_normal.texture')

    def test_basic_normal_gloss_stale_key_is_ignored_and_emissive_rgb_is_instance_data(self):
        from unittest.mock import patch
        graph = 'required/materials/basic_normal_gloss_emissive_detail.materialgraph'
        color = (0.25, 0.5, 0.75)
        rows = [
            dict(key_hash=0x06F19114, size=4, raw_hex=struct.pack('<f', 99.0).hex()),
            dict(key_hash=0x66EFA55B, size=4, raw_hex=struct.pack('<f', 3.0).hex()),
            dict(key_hash=0xD21B698F, size=12, raw_hex=struct.pack('<3f', *color).hex()),
            dict(key_hash=0x5FD56522, size=4, raw_hex=struct.pack('<f', 4.0).hex()),
            dict(key_hash=0x41E7FE54, size=4, raw_hex=struct.pack('<f', 30.0).hex()),
        ]
        archive, defaults, _ = self._bng_fixture(graph, dict(parameters=[], unsupported_parameters=rows))
        with (patch('rc2016.shaders.families.graph_parameter_tables',
                    return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters',
                    return_value=dict(parameters=[], unsupported_parameters=rows))):
            got = tp.material_texture_paths(archive, 'm/a.material')
        self.assertEqual(got['bng']['colour'], list(color))
        self.assertEqual(got['k'], 3.0 * 4.0 * color[0])
        self.assertEqual(got['self_lit'], 'g_blue')
        self.assertNotEqual(got['k'], 99.0)  # stale stored key 0x06F19114 is not this graph's K
        self.assertEqual(got['bng']['params'], (0.0, 0.0, 0.0, 0.0))

    def test_basic_normal_gloss_colored_spec_rgb_and_aniso_custom_gloss(self):
        from unittest.mock import patch
        # 0x43C53ECA is the per-instance RGB spec tint for the colored-spec graph.
        graph = 'required/materials/basic_normal_gloss_emissive_colored_spec.materialgraph'
        tint = (0.25, 0.5, 0.75)
        rows = [dict(key_hash=0x43C53ECA, size=12, raw_hex=struct.pack('<3f', *tint).hex())]
        archive, defaults, _ = self._bng_fixture(graph, dict(parameters=[], unsupported_parameters=rows))
        with (patch('rc2016.shaders.families.graph_parameter_tables',
                    return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters',
                    return_value=dict(parameters=[], unsupported_parameters=rows))):
            got = tp.material_texture_paths(archive, 'm/a.material')
        self.assertEqual(got['spec_tint'], list(tint))
        self.assertEqual(got['bng']['kind'], 4)

        # Aniso uses its own custom gloss key even when the generic gloss scale is set.
        graph = 'required/materials/basic_normal_gloss_aniso_incan.materialgraph'
        gloss = 0.875
        rows = [dict(key_hash=0x24F4CA4C, size=4, raw_hex=struct.pack('<f', 7.0).hex()),
                dict(key_hash=0x9856BA09, size=4, raw_hex=struct.pack('<f', gloss).hex())]
        archive, defaults, _ = self._bng_fixture(graph, dict(parameters=[], unsupported_parameters=rows))
        with (patch('rc2016.shaders.families.graph_parameter_tables',
                    return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters',
                    return_value=dict(parameters=[], unsupported_parameters=rows))):
            got = tp.material_texture_paths(archive, 'm/a.material')
        self.assertEqual(got['gloss'], gloss)
        self.assertEqual(got['bng']['kind'], 5)

    def test_basic_normal_gloss_metadata_transfers_through_material_textures(self):
        from unittest.mock import patch
        graph = 'required/materials/basic_normal_gloss_fresnel_detail.materialgraph'
        archive, defaults, params = self._bng_fixture(graph)
        sentinel = lambda archive, path: dict(path=path, pixels=b'', format=61)
        with (patch('rc2016.shaders.families.graph_parameter_tables',
                    return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params),
              patch.object(tp, 'load_texture', side_effect=sentinel)):
            entry = tp.material_textures(archive, [dict(index=17, path='m/a.material')], {})[17]
        self.assertEqual(entry['bng']['kind'], 3)
        self.assertEqual(entry['_n']['path'], 't/slot_n_named_detail.texture')
        self.assertEqual(entry['_bng_detail']['path'], 't/slot_detail_named_normal.texture')
        self.assertEqual(entry['self_lit'], 'c_alpha')
        self.assertEqual(entry['k'], 0.0)

    def test_basic_normal_gloss_specfresnel_parameter_roles(self):
        from unittest.mock import patch
        graph = 'required/materials/basic_normal_gloss_specfresnel_emissive.materialgraph'
        archive, defaults, params = self._bng_fixture(graph)
        with (patch('rc2016.shaders.families.graph_parameter_tables',
                    return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
            got = tp.material_texture_paths(archive, 'm/a.material')
        self.assertEqual(got['bng']['params'], (3.0, 1.0, 10.0, 0.0))
        self.assertEqual(got['bng']['detail'], (1.0, 1.0, 0.75))

    def test_base_color_comes_from_material_then_graph(self):
        archive = FakeArchive({
            'm/direct.material': ('material', b'\0t/body_n.texture\0t/body_c.texture\0'),
            'm/graph.material': ('material', b'\0g/shader.materialgraph\0'),
            'g/shader.materialgraph': ('materialgraph', b'\0t/graph_g.texture\0t/graph_c.texture\0'),
            'm/none.material': ('material', b'\0t/only_n.texture\0'),
        })
        self.assertEqual(tp.material_base_color_path(archive, 'm/direct.material'), 't/body_c.texture')
        self.assertEqual(tp.material_base_color_path(archive, 'm/graph.material'), 't/graph_c.texture')
        self.assertIsNone(tp.material_base_color_path(archive, 'm/none.material'))
        self.assertIsNone(tp.material_base_color_path(archive, 'm/missing.material'))

    def test_unloadable_texture_leaves_slot_untextured(self):
        archive = FakeArchive({'m/a.material': ('material', b'\0t/broken_c.texture\0')})
        cache = {}
        result = tp.material_textures(archive, [dict(index=0, path='m/a.material'), dict(index=1, path='')], cache)
        empty = {'_c': None, '_g': None, '_n': None, '_pick': None, '_h': None, 'k': 0.0, 'g_mode': 'colored',
                 'self_lit': 'c_alpha', 'spec': 1.0, 'gloss': 1.0}
        self.assertEqual(result, {0: empty, 1: empty})
        self.assertIn('t/broken_c.texture', cache)

    def test_vehicle_random_color_exact_slots_and_constant_specular(self):
        from unittest.mock import patch
        graph = 'materialgraph/vehicle/mo_veh_random_color_01/mo_veh_random_color_01.materialgraph'
        variant = graph.replace('_01.', '_01_extra.')
        slots = (0x49A7866BABDAD780, 0x628AD5A8603FF271, 0x628AD5A8AB25DA7A, 0x49A7866B0005F1E9)
        inputs = ['t/base_c.texture', 't/palette1.texture', 't/palette2.texture', 't/paintmask_h.texture']
        archive = FakeArchive({
            graph: ('materialgraph', dat1(inputs, lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows(list(zip(at, slots))))])),
            'm/car.material': ('material', dat1([graph, 't/white.texture', 't/ignored_g.texture', 't/ignored_n.texture'],
                lambda at: [(tp.MATERIAL_SLOT_SECTION, slot_rows([(at[1], slots[1])]))])),
            variant: ('materialgraph', b'\0'),
            'm/variant.material': ('material', b'\0' + variant.encode() + b'\0t/keep_g.texture\0'),
        })
        defaults = {0x4F1684D2: [1.0, 1.0, 1.0], 0xB5D46F28: [1.0, 1.0, 1.0], 0xF0A64F9B: 1.0, 0x148C2635: 1.0}
        params = dict(parameters=[], unsupported_parameters=[
            dict(key_hash=0x4F1684D2, size=12, raw_hex=struct.pack('<3f', 0.5, 0.25, 1.0).hex()),
            dict(key_hash=0x148C2635, size=4, raw_hex=struct.pack('<f', 0.0).hex()),
            dict(key_hash=0x66EFA55B, size=4, raw_hex=struct.pack('<f', 99.0).hex())])
        with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in defaults]], defaults)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
            paths = tp.material_texture_paths(archive, 'm/car.material')
            self.assertEqual((paths['_pick'], paths['_vehicle_p2'], paths['_h']), ('t/white.texture', inputs[2], inputs[3]))
            self.assertEqual((paths['g_mode'], paths['k']), ('vehicle', 0.0))
            self.assertNotIn('_g', paths); self.assertNotIn('_n', paths); self.assertNotIn('pick', paths)
            self.assertEqual(paths['vehicle']['a'], [0.5, 0.25, 1.0])
            self.assertEqual((paths['vehicle']['rim'], paths['vehicle']['palette_coordinate']), (0.0, 'UV0.x'))
            with patch.object(tp, 'load_texture', side_effect=lambda _, path: dict(path=path, pixels=b'', format=28)):
                entry = tp.material_textures(archive, [dict(index=0, path='m/car.material')], {})[0]
            self.assertEqual(entry['vehicle'], paths['vehicle'])
            self.assertEqual(entry['_vehicle_p2']['path'], inputs[2])
            other = tp.material_texture_paths(archive, 'm/variant.material')
            self.assertNotIn('vehicle', other); self.assertEqual(other['_g'], 't/keep_g.texture')
        from rc2016.viewer.shading import VEHICLE_PALETTE_TEXTURE_UNIT
        self.assertEqual(VEHICLE_PALETTE_TEXTURE_UNIT, 10)

    def test_roles_prefer_material_over_graph_defaults(self):
        archive = FakeArchive({
            'm/a.material': ('material', b'\0t/body_c.texture\0g/s.materialgraph\0'),
            'g/s.materialgraph': ('materialgraph', b'\0t/default_c.texture\0t/default_g.texture\0t/default_n.texture\0'),
        })
        paths = tp.material_texture_paths(archive, 'm/a.material')
        self.assertEqual((paths['_c'], paths['_g'], paths['_n']),
                         ('t/body_c.texture', 't/default_g.texture', 't/default_n.texture'))

    def test_graph_rules_and_colour_pick_palette(self):
        saucer = 'materialgraph/global/basic_colored_spec_emmisive_in_c_alpha/blarg_saucer_colorpick.materialgraph'
        archive = FakeArchive({
            'm/saucer.material': ('material', b'\0t/blarg_saucer_g.texture\0t/blarg_saucer_c.texture\0' + saucer.encode() + b'\0'),
            saucer: ('materialgraph', b'\0t/blarg_saucer_c.texture\0t/blarg_saucer_colorpick_c.texture\0'),
            'm/gloss.material': ('material', b'\0required/materials/basic_normal_gloss.materialgraph\0'),
            'required/materials/basic_normal_gloss.materialgraph': ('materialgraph', b'\0t/x_g.texture\0'),
            'm/plain.material': ('material', b'\0t/y_c.texture\0'),
        })
        paths = tp.material_texture_paths(archive, 'm/saucer.material')
        self.assertEqual((paths['_c'], paths['_pick']), ('t/blarg_saucer_c.texture', 't/blarg_saucer_colorpick_c.texture'))
        self.assertEqual((paths['g_mode'], paths['self_lit']), ('grey', 'g_blue'))
        self.assertEqual(set(paths['pick']), {'scale', 'blend', 'u'})
        gloss = tp.material_texture_paths(archive, 'm/gloss.material')
        self.assertEqual((gloss['g_mode'], gloss['k']), ('grey', 0.0))
        plain = tp.material_texture_paths(archive, 'm/plain.material')
        self.assertEqual((plain['g_mode'], plain['self_lit'], plain['spec'], plain['gloss']), ('colored', 'c_alpha', 1.0, 1.0))
        self.assertNotIn('_pick', plain)

    def test_color_tint_graph_reads_grey_g_and_tint_mask(self):
        tint = 'materialgraph/environment/global/gbl_color_tint_01/mo_gbl_color_tint_01.materialgraph'
        variant = 'materialgraph/environment/global/gbl_color_tint_01/jn_color_tint_detail_parallax.materialgraph'
        archive = FakeArchive({
            'm/trim.material': ('material', b'\0' + tint.encode() + b'\0t/trim_c.texture\0t/trim_g.texture\0'),
            tint: ('materialgraph', b'\0t/trim_c.texture\0t/mo_black_h.texture\0'),
            'm/variant.material': ('material', b'\0' + variant.encode() + b'\0t/v_c.texture\0t/v_h.texture\0'),
            variant: ('materialgraph', b'\0t/v_g.texture\0'),
        })
        paths = tp.material_texture_paths(archive, 'm/trim.material')
        self.assertEqual((paths['g_mode'], paths['k'], paths['_h']), ('grey', 0.0, 't/mo_black_h.texture'))
        self.assertEqual(set(paths['tint']), {'a', 'b', 'power_a', 'power_b', 'weight_a', 'weight_b', 'mask_b'})
        self.assertEqual(paths['spec_tint'], [1.0, 1.0, 1.0])
        other = tp.material_texture_paths(archive, 'm/variant.material')
        self.assertEqual(other['g_mode'], 'grey')
        self.assertEqual(other['tint']['variant'], 'detail')
        self.assertEqual(other['parallax']['writer'], '4E4D7894')

    def test_color_tint_variants_bind_exact_slots_and_preserve_zero_overrides(self):
        from unittest.mock import patch
        from rc2016.viewer.shading import TINT_DETAIL_TEXTURE_UNIT
        self.assertEqual(TINT_DETAIL_TEXTURE_UNIT, 9)
        folder = 'materialgraph/environment/global/gbl_color_tint_01/'
        variants = [('jn_mo_gbl_color_tint_01_random_color', 'random'), ('jn_color_tint_detail', 'detail'),
                    ('jn_gbl_color_tint_01_world_detail', 'world'), ('jn_gbl_color_tint_01_world_detail_normal', 'world_normal')]
        slots = [(0x49A7866BABDAD780, 't/main_c.texture'), (0x49A7866B1576D848, 't/main_g.texture'),
                 (0x49A7866B7AE34E7A, 't/main_n.texture'), (0x49A7866B93BB7340, 't/tint_h.texture'),
                 (0x49A7866B0056CBD0, 't/world_detail_c.texture'), (0x49A7866B4A362E9B, 't/detail_n.texture'),
                 (0x49A7866B67D6383E, 't/world_detail_n.texture'), (0x628AD5A840ED6C83, 't/palette_1d.texture')]
        defaults = {tp.TINT_KEYS['colour_a']: [.2,.3,.4], tp.TINT_KEYS['colour_b']: [.6,.7,.8],
                    tp.TINT_KEYS['weight_a']: .75, tp.TINT_KEYS['weight_b']: .5, tp.TINT_KEYS['mask_b']: .9,
                    0xCD0DCE36: .25, 0x26DCC4CE: 0., 0x5016F9F7: 1.,
                    0xAC34BF6E: .07, 0x93F9F959: .25, 0xB23D4CB3: .6, 0x3BBC1444: 1.}
        # Stale base switch/spec-tint/K in a material must not affect these distinct writers.
        params = dict(parameters=[], unsupported_parameters=[dict(key_hash=k, size=4,
            raw_hex=struct.pack('<f', value).hex()) for k, value in
            ((tp.TINT_G_SWITCH, 1.), (0x66EFA55B, 20.), (0x62835AF9, 2.), (0x24F4CA4C, .4),
             (0x26DCC4CE, 1.), (0xCD0DCE36, 0.), (0x5016F9F7, 0.))])
        for stem, kind in variants:
            graph = folder + stem + '.materialgraph'
            files = {'m/v.material': ('material', dat1([graph, 't/replacement_g.texture'], lambda at:
                [(tp.MATERIAL_SLOT_SECTION, slot_rows([(at[1], 0x49A7866B1576D848)]))])),
                graph: ('materialgraph', dat1([path for slot,path in slots], lambda at:
                    [(tp.GRAPH_SLOT_SECTION, slot_rows([(at[i], slot) for i,(slot,path) in enumerate(slots)]))]))}
            archive = FakeArchive(files)
            with patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in defaults]], defaults)), \
                 patch('rc2016.formats.materials.extract_material_parameters', return_value=params):
                paths = tp.material_texture_paths(archive, 'm/v.material')
                self.assertEqual((paths['_c'], paths['_g'], paths['_n'], paths['_h']),
                                 ('t/main_c.texture', 't/replacement_g.texture', 't/main_n.texture', 't/tint_h.texture'))
                self.assertEqual((paths['g_mode'], paths['k'], paths['spec_tint']), ('grey', 0., [1.,1.,1.]))
                self.assertEqual((paths['spec'], round(paths['gloss'], 5)), (2., .4))
                self.assertEqual(paths['tint']['variant'], kind)
                self.assertEqual((paths['tint']['palette_u'], paths['tint']['palette_blend']), (0., 0.))
                self.assertEqual(paths['tint']['world_origin'], [0.,0.,0.])
                if kind == 'detail':
                    self.assertEqual((paths['_tint_detail'], paths['tint']['mask_b']), ('t/detail_n.texture', 1.))
                if kind in ('world', 'world_normal'):
                    self.assertEqual(paths['_tint_detail'], 't/world_detail_c.texture')
                if kind == 'world_normal': self.assertEqual(paths['_pick'], 't/world_detail_n.texture')
            other = tp._graph_rule([folder + stem + '_extra.materialgraph'])
            self.assertNotIn('tint_variant', other)
        self.assertEqual(tp._graph_rule([folder + 'jn_color_tint_detail_parallax.materialgraph'])['parallax'], 'tint')

    def test_fixed_tint_palette_filters_in_linear_space_and_repeats(self):
        texture = dict(format=29, width=2, height=1, pixels=bytes([255,0,0,255, 0,0,255,255]), srgb=True)
        self.assertEqual(tp.tint_palette_sample(texture, .25), [1.,0.,0.])
        self.assertEqual(tp.tint_palette_sample(texture, .75), [0.,0.,1.])
        self.assertEqual(tp.tint_palette_sample(texture, .5), [.5,0.,.5])
        self.assertEqual(tp.tint_palette_sample(texture, 1.5), [.5,0.,.5])
        grey = dict(format=29, width=1, height=1, pixels=bytes([128,128,128,255]), srgb=True)
        expected = ((128/255 + .055)/1.055)**2.4
        self.assertAlmostEqual(tp.tint_palette_sample(grey, .4)[0], expected)

    def test_za_basic_normal_gloss_exact_rule_and_fresnel_values(self):
        from unittest.mock import patch
        graph = 'required/materials/za_basic_normal_gloss.materialgraph'
        variants = ('required/materials/za_basic_normal_gloss_detail.materialgraph',
                    'required/materials/za_basic_normal_gloss_extra.materialgraph')
        files = {'m/za.material': ('material', b'\0' + graph.encode() + b'\0'),
                 graph: ('materialgraph', b'\0t/za_c.texture\0t/za_g.texture\0t/za_n.texture\0')}
        for i, variant in enumerate(variants):
            files[f'm/v{i}.material'] = ('material', b'\0' + variant.encode() + b'\0')
            files[variant] = ('materialgraph', b'\0')
        archive = FakeArchive(files)
        defaults = {0x62835AF9: 1.0, 0x24F4CA4C: 1.0, 0x4D385B45: 8.0}
        tables = [[dict(key_hash=k) for k in defaults]]
        with patch('rc2016.shaders.families.graph_parameter_tables', return_value=(tables, defaults)):
            paths = tp.material_texture_paths(archive, 'm/za.material')
            self.assertEqual((paths['g_mode'], paths['k'], paths['za_fresnel_power']), ('grey', 0.0, 8.0))
            self.assertEqual((paths['spec'], paths['gloss']), (1.0, 1.0))
            for i in range(len(variants)):
                other = tp.material_texture_paths(archive, f'm/v{i}.material')
                self.assertEqual(other['g_mode'], 'colored')
                self.assertNotIn('za_fresnel_power', other)
            # Instance power (including zero) must survive paths -> texture entries; stale self-lit keys stay ignored.
            for power in (3.0, 0.0):
                params = dict(parameters=[], unsupported_parameters=[
                    dict(key_hash=k, size=4, raw_hex=struct.pack('<f', v).hex())
                    for k, v in ((0x4D385B45, power), (0x62835AF9, 2.0), (0x24F4CA4C, 0.5), (0x66EFA55B, 22.0))])
                with patch('rc2016.formats.materials.extract_material_parameters', return_value=params):
                    paths = tp.material_texture_paths(archive, 'm/za.material')
                    self.assertEqual((paths['spec'], paths['gloss'], paths['k']), (2.0, 0.5, 0.0))
                    entry = tp.material_textures(archive, [dict(index=0, path='m/za.material')], {})[0]
                    self.assertEqual((entry['g_mode'], entry['za_fresnel_power'], entry['k']), ('grey', power, 0.0))

    def test_arch_emissive_sequence_exact_rule_slots_defaults_and_stale_k(self):
        from unittest.mock import patch
        graph = 'materialgraph/environment/global/architecture/gbl_arch_emissive_sequence_01/mo_gbl_arch_emissive_sequence_01.materialgraph'
        other = graph.replace('mo_gbl_arch_emissive_sequence_01.', 'mo_gbl_arch_parallax_window_01.')
        base, seq, mask = 0x49A7866BC7FB024E, 0x628AD5A80A2038A8, 0x49A7866BE0B8D722
        archive = FakeArchive({
            graph: ('materialgraph', dat1(['t/default_h.texture', 't/sequence_h.texture', 't/mask_h.texture', 't/black_h.texture'],
                lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows([(at[0], base), (at[1], seq), (at[2], mask), (at[3], 0x49A7866B1576D848)]))])),
            'm/a.material': ('material', dat1([graph, 't/override_h.texture', 't/unused_c.texture'],
                lambda at: [(tp.MATERIAL_SLOT_SECTION, slot_rows([(at[1], base)]))])),
            other: ('materialgraph', b'\0'),
            'm/b.material': ('material', b'\0' + other.encode() + b'\0'),
        })
        defaults = {0x7F058428: [0.2, 0.4, 0.8], 0x77AF2D2B: 4.0, 0x8A43287F: 0.25,
                    0x60DE8181: 0.125, 0x33BD9BA6: 0.1, 0x23FEED4B: 2.0, 0xD7B41D33: 3.0}
        params = dict(parameters=[], unsupported_parameters=[dict(key_hash=k, size=4, raw_hex=struct.pack('<f', v).hex())
            for k, v in ((0x66EFA55B, 99.0), (0x77AF2D2B, 0.0), (0x7F87B733, 1.0), (0x1418C201, 1.0))])
        with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in defaults]], defaults)), patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
            paths = tp.material_texture_paths(archive, 'm/a.material')
            self.assertEqual((paths['g_mode'], paths['k'], paths['spec'], paths['gloss']), ('grey', 0.0, 1.0, 5.0))
            self.assertEqual((paths['_h'], paths['_sequence'], paths['_pick']), ('t/override_h.texture', 't/sequence_h.texture', 't/mask_h.texture'))
            self.assertEqual(paths['_g'], 't/black_h.texture')  # Native G slot can hold an _h texture.
            self.assertNotIn('pick', paths)
            decoded = paths['arch_sequence']
            self.assertEqual((decoded['colour'], decoded['intensity'], decoded['phase']), ([0.2, 0.4, 0.8], 0.0, 0.125))
            self.assertEqual((decoded['time'], decoded['attr1'], decoded['attr3']), (0.0, 0.0, 0.0))
            self.assertTrue(decoded['attr1_enabled']); self.assertTrue(decoded['attr3_enabled'])
            fake_texture = dict(path='dummy', pixels=b'', format=61)
            with patch.object(tp, 'load_texture', return_value=fake_texture):
                entry = tp.material_textures(archive, [dict(index=0, path='m/a.material')], {})[0]
            self.assertEqual(entry['arch_sequence'], decoded)
            self.assertEqual(entry['_sequence'], fake_texture)
            self.assertNotIn('arch_sequence', tp.material_texture_paths(archive, 'm/b.material'))
        from rc2016.viewer.shading import ARCH_SEQUENCE_TEXTURE_UNIT
        self.assertEqual(ARCH_SEQUENCE_TEXTURE_UNIT, 8)

    def test_glass_rules_bind_by_slot_and_read_flags(self):
        glass = 'materialgraph/global/basic_glass_patterned/blarg_saucer_glass.materialgraph'
        visor = 'materialgraph/global/basic_glass/character_glass.materialgraph'
        pick_slot, n_slot, c_slot = 0x628AD5A840ED6C83, 0x49A7866B7AE34E7A, 0x49A7866BABDAD780
        archive = FakeArchive({
            # The dome material binds _c in a slot its graph does not have; flags 0x232 = alpha-over, culled.
            'm/dome.material': ('material', dat1(
                [glass, 't/blarg_saucer_glass_c.texture'],
                lambda at: [(tp.MATERIAL_SLOT_SECTION, slot_rows([(at[1], c_slot)])), (tp.MATERIAL_FLAGS_SECTION, flags(0x232))])),
            glass: ('materialgraph', dat1(
                ['t/blarg_saucer_colorpick_c.texture', 't/patterns_cma.texture'],
                lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows([(at[0], pick_slot), (at[1], n_slot)]))])),
            'm/visor.material': ('material', dat1([visor], lambda at: [(tp.MATERIAL_FLAGS_SECTION, flags(0x3E))])),
            visor: ('materialgraph', b'\0'),
        })
        dome = tp.material_texture_paths(archive, 'm/dome.material')
        self.assertEqual(dome['forward']['kind'], 'glass_patterned')
        self.assertEqual((dome['forward']['blend'], dome['forward']['two_sided']), ('alpha_over', False))
        # Patterns_cma has no role suffix: it is the graph's _n-slot texture.
        self.assertEqual((dome['_n'], dome['_pick']), ('t/patterns_cma.texture', 't/blarg_saucer_colorpick_c.texture'))
        self.assertEqual(set(dome['forward']['params']),
                         {'tiling', 'normal_strength', 'f0', 'gloss', 'pick_u', 'rim_bias', 'rim_exp'})
        helmet = tp.material_texture_paths(archive, 'm/visor.material')
        self.assertEqual((helmet['forward']['kind'], helmet['forward']['blend'], helmet['forward']['two_sided']),
                         ('character_glass', 'alpha_over', True))
        self.assertEqual(len(helmet['forward']['params']['tint']), 3)
        entries = tp.material_textures(archive, [dict(index=0, path='m/dome.material')], {})
        self.assertEqual(entries[0]['forward']['kind'], 'glass_patterned')
        # Opaque graphs get no forward entry.
        plain = FakeArchive({'m/p.material': ('material', b'\0t/y_c.texture\0')})
        self.assertNotIn('forward', tp.material_texture_paths(plain, 'm/p.material'))

    def test_blend_state_from_flags_word(self):
        self.assertEqual(tp.blend_state(0x0020003A), dict(blend='alpha_over', two_sided=False))   # Blarg visor
        self.assertEqual(tp.blend_state(0x00200839), dict(blend='additive', two_sided=False))     # Micron canopy
        self.assertEqual(tp.blend_state(0x4), dict(blend='opaque', two_sided=True))
        self.assertEqual(tp.blend_state(0x3)['blend'], None)
        self.assertEqual(tp.blend_state(None), dict(blend=None, two_sided=False))

    def test_glass_uniform_packing(self):
        from rc2016.viewer.shading import glass_uniforms
        patterned = dict(kind='glass_patterned', params=dict(tiling=1.5, normal_strength=0.03, f0=0.1, gloss=0.75,
                                                              pick_u=0.125, rim_bias=0.9, rim_exp=8.0))
        self.assertEqual(glass_uniforms(patterned)[:3], (2, (1.5, 0.03, 0.1, 0.75), (0.125, 0.9, 8.0, 0.0)))
        visor = dict(kind='character_glass', params=dict(body=[0.1, 0.2, 0.3], gloss=1.0, tint=[0.3, 1.0, 0.1],
                                                          tint_scale=1.0, alpha_exp=0.5, env_scale=1.0, alpha_bias=0.15,
                                                          rim_bias=1.0, env_exp=2.0, rim_exp=0.0))
        self.assertEqual(glass_uniforms(visor), (1, (0.1, 0.2, 0.3, 1.0), (0.3, 1.0, 0.1, 1.0),
                                                 (0.5, 1.0, 0.15, 1.0), (2.0, 0.0, 0.0, 0.0)))

    def test_texture_list_merges_materials_and_marks_graph_defaults(self):
        archive = FakeArchive({
            'm/a.material': ('material', b'\0t/body_c.texture\0g/s.materialgraph\0'),
            'm/b.material': ('material', b'\0t/body_c.texture\0'),
            'g/s.materialgraph': ('materialgraph', b'\0t/default_n.texture\0t/body_c.texture\0'),
        })
        rows = tp.model_texture_list(archive, [dict(index=0, path='m/a.material', name='A'),
                                                dict(index=1, path='m/b.material', name='B'), dict(index=2, path='')])
        by_path = {r['path']: r for r in rows}
        self.assertEqual(list(by_path), ['t/body_c.texture', 't/default_n.texture'])
        self.assertEqual(by_path['t/body_c.texture']['materials'], ['A', 'B'])
        self.assertFalse(by_path['t/body_c.texture']['via_graph'])
        self.assertTrue(by_path['t/default_n.texture']['via_graph'])
        self.assertEqual(by_path['t/default_n.texture']['suffix'], '_n')

    def test_to_image_keeps_stored_values_and_row_order(self):
        rg = tp.to_image(dict(format=49, width=2, height=2, pixels=bytes([1, 2, 3, 4, 5, 6, 7, 8])))
        self.assertEqual((rg.mode, rg.getpixel((0, 0)), rg.getpixel((1, 1))), ('RGB', (1, 2, 0), (7, 8, 0)))
        rgba = tp.to_image(dict(format=29, width=1, height=2, pixels=bytes([9, 8, 7, 6, 1, 2, 3, 4])))
        self.assertEqual(rgba.getpixel((0, 0)), (9, 8, 7, 6))   # top row first, unflipped
        # One BC4 block: endpoints 255/0, all indices 0 -> every texel is 255.
        bc4 = tp.to_image(dict(format=80, width=4, height=4, pixels=bytes([255, 0, 0, 0, 0, 0, 0, 0])))
        self.assertEqual((bc4.size, bc4.getpixel((3, 3))), ((4, 4), 255))

    def test_untile_numpy_matches_python_including_npot(self):
        import os, sys
        from rc2016.formats import texture
        for width, height, fmt in [(1920, 1080, 98), (250, 250, 28), (64, 24, 72), (256, 256, 80)]:
            data = os.urandom(texture.tiled_top_mip_size(width, height, fmt))
            fast = texture.ps4_deswizzle_top(data, width, height, fmt)
            saved = sys.modules.get('numpy')
            sys.modules['numpy'] = None   # forces the pure-Python loop
            try:
                slow = texture.ps4_deswizzle_top(data, width, height, fmt)
            finally:
                sys.modules['numpy'] = saved
            self.assertEqual(fast, slow, (width, height, fmt))
            self.assertEqual(len(fast), texture.top_mip_size(width, height, fmt))

    def test_array_slices_and_array_dds(self):
        from rc2016.formats import texture
        step = texture.tiled_top_mip_size(32, 32, 80)
        data = bytes([1]) * step + bytes([2]) * step
        slices = texture.ps4_deswizzle_slices(data, 32, 32, 80, 2)
        self.assertEqual([s[0] for s in slices], [1, 2])
        dds = texture.dds_bytes(32, 32, 80, b''.join(slices), 2)
        self.assertEqual(int.from_bytes(dds[140:144], 'little'), 2)   # DX10 arraySize
        self.assertEqual(len(dds), 148 + 2 * texture.top_mip_size(32, 32, 80))

    def test_bc1_srgb_decodes_like_bc1(self):
        block = bytes([0xFF, 0xFF, 0, 0, 0, 0, 0, 0])   # colour0 white, all indices 0
        image = tp.to_image(dict(format=72, width=4, height=4, pixels=block))
        self.assertEqual(image.convert('RGB').getpixel((0, 0)), (255, 255, 255))

    def test_bc7_srgb_uploads_as_unorm(self):
        self.assertEqual(tp.GL_COMPRESSED[99], tp.GL_COMPRESSED[98])

    def test_mesh_payload_batches_by_material_and_restores_v(self):
        mesh = lambda index, slot, first: dict(
            index=index, positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], normals=[(0, 1, 0)] * 3,
            uv0=[(0.25, 0.1), (0.5, 0.2), (0.75, 0.9)], triangles=[[0, 1, 2]],
            material=dict(index=slot))
        looks = [dict(index=0, lods=[dict(index=0, start=0, count=2, meshes=[0, 1])])]
        decoded = dict(looks=looks, mesh_record_count=2, source_sha256='x', meshes=[mesh(0, 3, 0), mesh(1, 1, 3)])
        payload = mesh_payload(decoded, 'All')
        self.assertEqual(payload['batches'], [(3, 0, 1), (1, 1, 1)])
        self.assertEqual(payload['uvs'][0], (0.25, 0.9))
        self.assertEqual(payload['triangles'][1], (3, 4, 5))


@unittest.skipUnless((MATERIALS / 'RockBlend/source_assets').is_dir(), 'source assets are not present')
class RockBlendTests(unittest.TestCase):
    def test_exact_rule_slots_overrides_and_siblings(self):
        from unittest.mock import patch
        graph='materialgraph/environment/global/rock/mo_blend_01/mo_blend_01.materialgraph'
        here=MATERIALS/'RockBlend/source_assets'
        graph_bytes=(here/'mo_blend_01.materialgraph').read_bytes()
        material=(here/'rv_neb_rock_asteroid_tile_01.material').read_bytes()
        # FakeArchive adds the native magic; use source body so DAT1 offsets remain exact.
        archive=FakeArchive({graph:('materialgraph',graph_bytes[4:]),
                             'm/rock.material':('material',material[4:])})
        paths=tp.material_texture_paths(archive,'m/rock.material')
        expected=tp.texture_slots(material,tp.MATERIAL_SLOT_SECTION)
        defaults=tp.texture_slots(graph_bytes,tp.GRAPH_SLOT_SECTION)
        for role,key in (('_c',0x49A7866B969AFD6A),('_rock_c2',0x49A7866BAF17C1AF),
                         ('_g',0x49A7866B7BF812C7),('_rock_g2',0x49A7866B42752E02),
                         ('_n',0x49A7866B146D84F5),('_rock_n2',0x49A7866B2DE0B830),('_rock_mask',0x49A7866B3404C763)):
            self.assertEqual(paths[role],expected.get(key) or defaults[key])
        self.assertEqual((paths['g_mode'],paths['k'],paths['rock_blend']['writer']),('grey',0.0,'886AAC94'))
        self.assertEqual((paths['spec'],paths['gloss']), (1.0,1.0))
        self.assertEqual((paths['rock_blend']['mask_strength'],paths['rock_blend']['sharpness']), (1.0,1.0))
        for stem in ('rp_mo_blend_01','rp_mo_blend_01_caustic','rp_mo_blend_01_glow_vert_paint','mo_neb_worldspace_detail_01','mo_blend_01_extra'):
            self.assertNotIn('rock_blend',tp._graph_rule([graph.replace('mo_blend_01.materialgraph',stem+'.materialgraph')]))
        def loaded(_archive,path):
            return dict(path=path,width=1,height=1,format=28,pixels=bytes([10,20,30,255]),srgb=False)
        with patch.object(tp,'load_texture',side_effect=loaded):
            entry=tp.material_textures(archive,[dict(index=0,path='m/rock.material')],{})[0]
        self.assertIn('_rock_volume',entry)
        self.assertEqual(entry['_rock_volume']['source_paths'],[paths[k] for k in ('_rock_mask','_rock_c2','_rock_g2','_rock_n2')])
        def missing(_archive,path):
            if path==paths['_rock_c2']: raise ValueError('Unresolved source')
            return loaded(_archive,path)
        with patch.object(tp,'load_texture',side_effect=missing):
            entry=tp.material_textures(archive,[dict(index=0,path='m/rock.material')],{})[0]
        self.assertNotIn('_rock_volume',entry)
        self.assertTrue(entry['rock_blend']['unresolved_second_layer'])
        self.assertIsNotNone(entry['_c'])

    def test_source_alpha_transport_and_absent_default_are_distinct(self):
        mesh=dict(index=0,positions=[(0,0,0),(1,0,0),(0,1,0)],normals=[(0,1,0)]*3,
            uv0=[(0,0)]*3,triangles=[(0,1,2)],colors=[(1,1,1,a) for a in (0.0,0.5,1.0)])
        decoded=dict(meshes=[mesh],looks=[],mesh_record_count=1,source_sha256='x')
        from unittest.mock import patch
        with patch('rc2016.formats.native_model._selected_meshes',return_value=[0]):
            payload=mesh_payload(decoded)
            self.assertEqual(payload['rock_alpha'],[0.0,0.5,1.0])
            self.assertEqual(payload['rock_alpha_sources'][0]['source'],'native_colors_alpha')
            mesh['colors']=None;payload=mesh_payload(decoded)
            self.assertEqual(payload['rock_alpha'],[1.0]*3)
            self.assertIn('unresolved',payload['rock_alpha_sources'][0]['source'])

    def test_volume_tiles_preserve_source_values_and_spacing(self):
        textures=[dict(path=str(i),width=1 if i else 2,height=1,format=28,
            pixels=bytes([i,1,2,255])*(1 if i else 2)) for i in range(4)]
        result=tp.rock_texture_volume(textures)
        self.assertEqual((result['width'],result['height'],len(result['pixels'])),(2,1,32))
        self.assertEqual(result['scales'],[(1.,1.),(.5,1.),(.5,1.),(.5,1.)])
        self.assertEqual(result['pixels'][8:16],bytes([1,1,2,255])*2)


class DroppedPixelsTests(unittest.TestCase):
    def test_cpu_sampled_sources_reload_after_a_caller_drops_their_pixels(self):
        # The level loader drops 'pixels' and 'slices' of cached textures it has uploaded; RockBlend
        # layers, tint palettes and puddle sources are sampled here again (Pokitaru KeyError 'pixels').
        from unittest.mock import patch
        base = {'_c': 'c', '_g': 'g', '_n': 'n'}
        paths = {'m/rock.material': dict(base, rock_blend=dict(writer='886AAC94'), _rock_mask='mask',
                                         _rock_c2='c2', _rock_g2='g2', _rock_n2='n2'),
                 'm/tint.material': dict(base, tint=dict(variant=1, palette_u=0.5), _tint_palette='palette'),
                 'm/puddle.material': dict(base, puddles=dict(params=[]), _puddle_noise='noise', _puddle_ripple='ripple')}
        def loaded(_archive, path):
            pixels = bytes([10, 20, 30, 255])
            return dict(path=path, width=1, height=1, format=28, pixels=pixels, slices=[pixels], srgb=False)
        names = ('c', 'g', 'n', 'mask', 'c2', 'g2', 'n2', 'palette', 'noise', 'ripple')
        cache = {p: {k: v for k, v in loaded(None, p).items() if k not in ('pixels', 'slices')} for p in names}
        materials = [dict(index=i, path=p) for i, p in enumerate(paths)]
        with patch.object(tp, 'material_texture_paths', side_effect=lambda _a, p: paths[p]), \
                patch.object(tp, 'load_texture', side_effect=loaded) as load:
            rock, tint, puddle = (tp.material_textures(None, materials, cache)[i] for i in range(3))
        self.assertEqual(rock['_rock_volume']['source_paths'], ['mask', 'c2', 'g2', 'n2'])
        self.assertNotIn('unresolved_second_layer', rock['rock_blend'])
        self.assertEqual(tint['tint']['palette_source'], 'palette')
        self.assertEqual(puddle['_puddle_atlas']['width'], 6)
        # Uploaded GL roles are not reloaded: the GL side already holds them by path.
        self.assertEqual(sorted(c.args[1] for c in load.call_args_list),
                         ['c2', 'g2', 'mask', 'n2', 'noise', 'palette', 'ripple'])
        self.assertNotIn('pixels', rock['_c'])


class GroundPuddlesTests(unittest.TestCase):
    @unittest.skipUnless((MATERIALS / 'GroundPuddles/source_assets').is_dir(), 'source assets are not present')
    def test_exact_graph_and_all_source_material_slots_params(self):
        from pathlib import Path
        import json
        from unittest.mock import patch
        from rc2016.viewer.viewport import ModelViewport
        folder=MATERIALS/'GroundPuddles'
        graph='materialgraph/environment/kalebo/jn_ground_puddles.materialgraph'
        rule=tp._graph_rule([graph]);self.assertTrue(rule['puddles'])
        for suffix in ('_emissive','_extra','_02'):
            self.assertEqual(tp._graph_rule([graph.replace('.materialgraph',suffix+'.materialgraph')]),{})
        files={graph:('materialgraph',(folder/'source_assets/jn_ground_puddles.materialgraph').read_bytes()[4:])}
        slots=json.loads((folder/'slot_bindings.json').read_text());effective=json.loads((folder/'corpus_check.json').read_text())['effective_parameters']
        materials=[]
        for file in sorted((folder/'source_assets').glob('*.material')):
            path='m/'+file.name;files[path]=('material',file.read_bytes()[4:]);materials.append(dict(index=len(materials),path=path))
        archive=FakeArchive(files)
        def texture(_archive,path):
            return dict(path=path,format=28,width=2,height=2,srgb=False,pixels=bytes((32,96,192,255))*4)
        with patch.object(tp,'load_texture',side_effect=texture): entries=tp.material_textures(archive,materials,{})
        class Upload:
            def _upload_texture(self,texture):return 17
        for material in materials:
            e=entries[material['index']];name=Path(material['path']).name
            self.assertEqual(e['g_mode'],'grey');self.assertEqual(e['self_lit'],'none')
            self.assertEqual((e['k'],e['spec'],e['gloss']),(0.,1.,1.))
            self.assertEqual(e['_g']['path'],slots['materials'][name].get('0x49a7866b1576d848',slots['graph']['0x49a7866b1576d848']))
            self.assertEqual(e['puddles']['params'],list(effective[name].values()))
            self.assertEqual(e['puddles']['time'],0.);self.assertFalse(e['puddles']['runtime_pass_verified'])
            self.assertEqual(e['_puddle_atlas']['width'],8);self.assertEqual(e['_puddle_atlas']['height'],4)
            self.assertIsNone(e['_h']);self.assertIsNone(e['_pick'])
            uploaded=ModelViewport._gl_entry(Upload(),e,{})
            self.assertEqual(uploaded['puddles'],e['puddles']);self.assertEqual(uploaded['_puddle_atlas'],(17,False))
        self.assertEqual(len(entries),17)

    def test_atlas_keeps_native_sizes_and_bilinear_repeat_seams(self):
        import math,random
        from PIL import Image
        def tex(path,w,h):
            data=bytes((x*37+y*17+c*13)%256 for y in range(h) for x in range(w) for c in range(4))
            return dict(path=path,width=w,height=h,format=28,srgb=False,pixels=data)
        textures=[tex('noise',2,3),tex('ripple',3,2)];atlas=tp.puddle_texture_atlas(*textures)
        self.assertEqual((atlas['width'],atlas['height']),(9,5));self.assertFalse(atlas['srgb'])
        def sample(im,u,v,repeat):
            x=u*im.width-.5;y=v*im.height-.5;i=math.floor(x);j=math.floor(y);fx=x-i;fy=y-j
            def pixel(i,j):return im.getpixel((i%im.width,j%im.height) if repeat else (i,j))
            return tuple(sum(pixel(i+dx,j+dy)[c]*(fx if dx else 1-fx)*(fy if dy else 1-fy) for dy in (0,1) for dx in (0,1)) for c in range(4))
        packed=tp.to_image(atlas);rng=random.Random(90914)
        for texture,scale in zip(textures,atlas['scales']):
            image=tp.to_image(texture)
            for u,v in [(0,0),(1,1),(-1,-1),(.5,0),(0,.5)]+[(rng.uniform(-3,3),rng.uniform(-3,3)) for _ in range(500)]:
                a=sample(image,u,v,True);b=sample(packed,(u%1)*scale[0]+scale[2],(v%1)*scale[1]+scale[3],False)
                for x,y in zip(a,b):self.assertAlmostEqual(x,y,places=10)
        with self.assertRaises(ValueError):tp.puddle_texture_atlas(dict(textures[0],srgb=True),textures[1])

if __name__ == '__main__':
    unittest.main()
