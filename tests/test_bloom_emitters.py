"""Which materials make a frame use the float target, so the decoded bloom glows around their emission."""
import unittest

from rc2016.viewer.viewport import ModelViewport, self_lit_entry


def translation(emission):
    nodes = [dict(op='const', value=0.0), emission]
    return dict(nodes=nodes, outputs=dict(albedo=[0, 0, 0], F0=[0, 0, 0], gloss=0, emission=1))


class SelfLitEntry(unittest.TestCase):
    def test_self_lit_gain(self):
        self.assertTrue(self_lit_entry({'k': 20.0, 'self_lit': 'g_blue'}))   # the Clank bots' eyes (basic_incandescence)
        self.assertFalse(self_lit_entry({'k': 0.0, 'self_lit': 'c_alpha'}))
        self.assertFalse(self_lit_entry({}))

    def test_bulb_and_glow_shell(self):
        self.assertTrue(self_lit_entry({'k': 0.0, 'bright_center': {'k': 50.0}}))
        self.assertTrue(self_lit_entry({'forward': {'kind': 'bright_glow'}}))
        self.assertFalse(self_lit_entry({'forward': {'kind': 'character_glass'}}))

    def test_native_translation_emission(self):
        lit = {'k': 0.0, 'native_translation': translation(dict(op='sub', args=[0, 0]))}
        dark = {'k': 0.0, 'native_translation': translation(dict(op='const', value=0.0))}
        self.assertTrue(self_lit_entry(lit))
        self.assertFalse(self_lit_entry(dark))
        self.assertFalse(self_lit_entry(lit, native_translation=False))   # the translation is not drawn

    def test_switch(self):
        self.assertTrue(ModelViewport.BLOOM_EMITTERS)


if __name__ == '__main__':
    unittest.main()
