"""colored_basic_normal_colored_gloss_detail (deferred writer 16541306): constant tint albedo, constant coloured F0.

The rule tests use synthetic data. The archive test reads the Blarg armored transport glass (RC2016_RESEARCH) and skips without it.
"""
import os
import unittest
from pathlib import Path

from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT
from tests.test_oneoff_materials import extract, row

GRAPH = 'required/materials/colored_basic_normal_colored_gloss_detail.materialgraph'
DEFAULTS = {0x7F058428: [1.0, 0.0, 1.0], 0x17DE448C: [1.0, 0.0, 1.0], 0x49AFC3A0: 0.75, 0x24F4CA4C: 1.0, 0xEF187899: 1.0, 0xE3BD962E: 1.0}
GLASS_MATERIAL = 'material/vehicle/veh_blarg_armored_transport_glass/veh_blarg_armored_transport_glass.material'
RESEARCH = os.environ.get('RC2016_RESEARCH')


class RuleTests(unittest.TestCase):
    def test_graph_prefix_selects_the_flat_kind_with_linear_z(self):
        rule = tp._graph_rule([GRAPH])
        self.assertEqual(rule['flat_surface'], 'colored_gloss_detail')
        self.assertTrue(rule['linear_z'])
        self.assertIsNone(rule['self_lit_key'])

    def test_values_come_from_the_material_rows(self):
        _, got = extract(GRAPH, DEFAULTS, [row(0x7F058428, (0.0152, 0.0091, 0.0)), row(0x17DE448C, (0.4072, 0.3231, 0.0222)),
                                           row(0xEF187899, 2.0), row(0x24F4CA4C, 0.5)])
        flat = got['flat_surface']
        self.assertEqual(flat['model'], 'colored_gloss_detail')
        self.assertEqual(flat['writer'], '16541306')
        self.assertAlmostEqual(flat['colour'][0], 0.0304, places=4)   # tint x k8
        self.assertAlmostEqual(flat['f0_colour'][1], 0.3231, places=4)
        self.assertEqual(flat['gloss'], 0.5)
        self.assertEqual(flat['detail_strength'], 0.75)
        self.assertEqual(got['k'], 0.0)
        self.assertTrue(got['linear_z'])

    def test_no_bound_c_means_no_texture_sum(self):
        _, got = extract(GRAPH, DEFAULTS, [])
        self.assertIsNone(got['flat_surface']['texture_sum'])

    def test_shader_reads_the_channel_sum_and_coloured_f0(self):
        self.assertIn('uFlatSpec.z > 0.5 && uHasC) ? uFlatColour * (c.r + c.g + c.b)', FRAGMENT)
        self.assertIn('F0 = uFlatSpec.w > 0.5 ? uFlatF0 : vec3(uFlatSpec.x)', FRAGMENT)


@unittest.skipUnless(RESEARCH, 'RC2016_RESEARCH is not set')
class ArchiveTests(unittest.TestCase):
    def test_blarg_transport_glass(self):
        from rc2016.formats.archive import Archive
        import tempfile
        with tempfile.TemporaryDirectory() as out:
            archive = Archive(Path(RESEARCH) / 'asset_archive', out, lambda *a, **k: None)
            got = tp.material_texture_paths(archive, GLASS_MATERIAL)
        flat = got['flat_surface']
        self.assertTrue(got['_c'].endswith('mo_gray_h.texture'))
        self.assertTrue(got['_n'].endswith('tb_pok_metal_trim_03_n.texture'))
        self.assertEqual(flat['texture_sum'], 'r')   # BC4: one channel
        for have, want in zip(flat['colour'], (0.015208514, 0.009134059, 0.0)):
            self.assertAlmostEqual(have, want, places=6)
        for have, want in zip(flat['f0_colour'], (0.40724021, 0.32314321, 0.02217389)):
            self.assertAlmostEqual(have, want, places=6)
        self.assertEqual(flat['gloss'], 1.0)


if __name__ == '__main__':
    unittest.main()
