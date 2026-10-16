"""Robot antenna tip preview override (graph npc_qwark_robot_bulb drawn with Qwark's bright-centre term).

Synthetic data only: no archive, no GPU, no Blender.
"""
import struct
import unittest
from unittest.mock import patch

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT, OPAQUE_UNIFORMS
from tests.test_viewer_textures import FakeArchive, dat1

ROBOT = 'materialgraph/character/npc/npc_qwark_robot_bulb/npc_qwark_robot_bulb.materialgraph'
BRIGHT = 'materialgraph/character/hero/hero_bright_center_light/hero_bright_center_light_variable.materialgraph'
# enm_qwark_light_center.material keys: edge, centre, intensity, power S, steady B, F0, gloss G, K.
QWARK_LIGHT_CENTER = {0x16B8AB81: (0.006048833020031452, 0.0, 0.0), 0x5BC2D1F0: (0.4691011607646942, 0.002428215928375721, 0.0),
                      0x398AD3E2: 1.0, 0x60DC3D2F: 0.5, 0x01B78BF6: 1.0, 0x08FE9F29: 0.2, 0x0B065E94: 0.35, 0xB7305C21: 50.0}


def extract(graph, rows):
    mat = dat1([graph], lambda at: [])
    archive = FakeArchive({'m/a.material': ('material', mat), graph: ('materialgraph', dat1([], lambda at: []))})
    params = dict(parameters=[], unsupported_parameters=[
        dict(key_hash=k, size=4 * len(v) if isinstance(v, tuple) else 4,
             raw_hex=struct.pack('<%df' % (len(v) if isinstance(v, tuple) else 1), *(v if isinstance(v, tuple) else (v,))).hex())
        for k, v in rows.items()])
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in rows]], {})),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return tp.material_texture_paths(archive, 'm/a.material')


class RobotBulbPreviewTests(unittest.TestCase):
    def test_override_is_on_and_labelled(self):
        self.assertTrue(tp.ROBOT_BULB_QWARK_TIP_PREVIEW)
        got = extract(ROBOT, {})
        bc = got['bright_center']
        self.assertTrue(bc['albedo_white'])
        self.assertIn('enm_qwark_light_center', bc['preview_override'])
        self.assertEqual((got['k'], got['self_lit'], got['spec'], got['gloss']), (50.0, 'bright_center', 1.0, 1.0))
        self.assertNotIn('facing_glow', got)

    def test_constants_equal_what_the_qwark_light_center_material_yields(self):
        qwark = extract(BRIGHT, QWARK_LIGHT_CENTER)['bright_center']
        got = extract(ROBOT, {})['bright_center']
        for key in ('edge', 'centre'):
            for a, b in zip(got[key], qwark[key]):
                self.assertAlmostEqual(a, b, places=6, msg=key)
        self.assertEqual((got['f0'], got['gloss_sq'], got['writer']), (qwark['f0'], qwark['gloss_sq'], qwark['writer']))
        self.assertEqual(got['steady'], qwark['steady'])
        self.assertAlmostEqual(got['exponent'], qwark['exponent'], places=6)
        self.assertAlmostEqual(got['weight'], qwark['weight'], places=6)

    def test_switch_restores_the_decoded_facing_glow(self):
        with patch.object(tp, 'ROBOT_BULB_QWARK_TIP_PREVIEW', False):
            got = extract(ROBOT, {})
        self.assertNotIn('bright_center', got)
        self.assertEqual(got['facing_glow']['writer'], '0CD2AAFB')
        self.assertEqual(got['k'], 0.0)

    def test_other_graphs_are_unaffected(self):
        self.assertNotIn('bright_center_preview', tp._graph_rule([BRIGHT]))
        self.assertNotIn('albedo_white', extract(BRIGHT, QWARK_LIGHT_CENTER)['bright_center'])

    def test_viewer_has_the_white_albedo_uniform(self):
        self.assertIn('uBrightWhite', OPAQUE_UNIFORMS)
        self.assertIn('uBrightCentre && uBrightWhite', FRAGMENT)

    def test_validation_expects_the_white_constant(self):
        import inspect
        source = inspect.getsource(validation)
        self.assertIn('bright centre albedo', source)


if __name__ == '__main__':
    unittest.main()
