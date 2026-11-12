"""Which built materials the Blender emitter bloom tags, and that it follows the viewer's self-lit rule."""
import json
import unittest

from rc2016.pipeline.emitter_bloom import material_kind
from rc2016.viewer.viewport import self_lit_entry


def translation(emission):
    return dict(nodes=[dict(op='const', value=0.0), emission], outputs=dict(albedo=[0, 0, 0], F0=[0, 0, 0], gloss=0, emission=1))


def rule(**values):
    return {'rc_viewer_graph_rule': json.dumps(values)}


NATIVE = 'native_translated_deferred_frontend'


class MaterialKind(unittest.TestCase):
    def test_viewer_rules(self):
        self.assertEqual(material_kind(rule(k=50.0, ported=['bright_center'])), 'lit')      # the bots' antenna bulbs
        self.assertEqual(material_kind(rule(k=0.0, ported=['bright_glow'])), 'sprite')      # the additive glow shell
        self.assertIsNone(material_kind(rule(k=0.0, ported=[])))
        self.assertIsNone(material_kind(rule(applies=False, k=20.0)))

    def test_native_translation(self):
        lit = {'shader_status': NATIVE, 'rc_shader_translation': json.dumps(translation(dict(op='sub', args=[0, 0])))}
        dark = {'shader_status': NATIVE, 'rc_shader_translation': json.dumps(translation(dict(op='const', value=0.0)))}
        self.assertEqual(material_kind(lit), 'lit')                                          # the eyes, Zed's visor
        self.assertIsNone(material_kind(dark))
        self.assertIsNone(material_kind(dict(lit, shader_status='viewer_graph_rule_frontend')))   # translation not drawn

    def test_game_shader_self_lit_k(self):
        self.assertEqual(material_kind({}, 5.0), 'lit')
        self.assertIsNone(material_kind({}, 0.0))

    def test_same_rule_as_the_viewer(self):
        for entry, properties in (({'k': 20.0}, rule(k=20.0)), ({'bright_center': {'k': 50.0}}, rule(k=50.0, ported=['bright_center'])),
                                  ({'forward': {'kind': 'bright_glow'}}, rule(ported=['bright_glow'])), ({}, {})):
            self.assertEqual(self_lit_entry(entry), material_kind(properties) is not None)


if __name__ == '__main__':
    unittest.main()
