"""The health crate effect for Blender (rc2016/pipeline/health_effect.py): the data side, which needs no Blender."""
import json
import unittest

import numpy as np

from rc2016.pipeline import health_effect
from rc2016.viewer import idle_effect as fx


class EffectDescriptionTests(unittest.TestCase):
    def test_description_is_json_and_anchored_at_the_locator(self):
        d = health_effect.effect_description()
        json.dumps(d)
        self.assertEqual(tuple(d['origin']), fx.HEALTH_CRATE_LOCATOR)
        self.assertEqual(d['locator'], 'igLoc_health_FX')

    def test_quads_follow_the_viewer_values(self):
        d = health_effect.effect_description()
        kinds = [q['kind'] for q in d['quads']]
        self.assertEqual(kinds[:2], ['glow_spin', 'glow_soft'])   # components 9 and 6 of the captured draw order
        spin, soft = d['quads'][:2]
        self.assertAlmostEqual(spin['size'][0], 0.50166)
        self.assertAlmostEqual(soft['size'][0], 0.85519)
        # soft glow: rgb = 2 A P2 (...); spin glow: P2 (L + 1) = 2 P2, no A
        self.assertAlmostEqual(soft['emit'][2], 2 * fx.AMPLITUDE * 10.0 * fx.SPRITES[1]['colour'][2] / 10.0)
        self.assertAlmostEqual(spin['emit'][2], 2 * fx.SPRITES[0]['colour'][2])
        self.assertTrue({'shockwave', 'glow_soft'} & set(kinds[2:]))
        for q in d['quads']:
            self.assertEqual(len(q['emit']), 3)
            self.assertGreaterEqual(q['alpha'], 0.0)

    def test_ring_colour_uses_the_red_term(self):
        d = health_effect.effect_description()
        ring = next(q for q in d['quads'] if q['kind'] == 'shockwave')
        k = fx.AMPLITUDE * fx.EMITTER_COLOUR[0]
        self.assertAlmostEqual(ring['emit'][0], 2 * k * (2 * k + 1))

    def test_limitations_name_what_is_not_built(self):
        text = ' '.join(health_effect.effect_description()['limitations'])
        self.assertIn('depth-test-off', text)
        self.assertIn('bloom', text.lower())


class FlareAndLightTests(unittest.TestCase):
    def test_flares_follow_the_viewer_sprites(self):
        d = health_effect.effect_description()
        flares = [q for q in d['quads'] if q.get('flare')]
        rows = [r for r in fx.SPRITES if r['flare']]
        self.assertEqual([q['kind'] for q in flares], ['flare_round'] * 4 + ['flare_square'])
        for q, r in zip(flares, rows):
            self.assertAlmostEqual(q['size'][0], r['size'][0])
            self.assertAlmostEqual(q['emit'][2], 2 * fx.AMPLITUDE * r['colour'][2])
            self.assertAlmostEqual(q['alpha'], r['alpha'])
            self.assertGreaterEqual(q['offset'][2], fx.FLARE_PULL)   # pulled toward the camera, layers a gap apart
        self.assertEqual([q['offset'][2] for q in flares], sorted(q['offset'][2] for q in flares))
        self.assertEqual(d['mask_weights']['flare_square'], fx.MASK_WEIGHT[fx.FLARE_SQUARE])
        self.assertEqual(d['flare_pull'], fx.FLARE_PULL)
        self.assertEqual(flares[-1]['kind'], d['quads'][-1]['kind'])   # drawn after the particles, as in the viewer

    def test_flares_and_light_can_be_left_out(self):
        d = health_effect.effect_description(flares=False, light=False)
        self.assertFalse([q for q in d['quads'] if q.get('flare')])
        self.assertIsNone(d['light'])

    def test_light_and_fade_carry_the_viewer_values(self):
        d = health_effect.effect_description()
        self.assertEqual(tuple(d['light']['colour']), fx.LIGHT_COLOUR)
        self.assertEqual(d['light']['radius'], fx.LIGHT_RADIUS)
        self.assertEqual((d['fade']['distance'], d['fade']['range']), (fx.FADE_DISTANCE, fx.FADE_RANGE))

    def test_coplanar_glows_are_layered_in_draw_order(self):
        d = health_effect.effect_description()
        spin, soft = d['quads'][:2]
        self.assertTrue(spin['layer'] and soft['layer'])
        self.assertGreater(soft['offset'][2], spin['offset'][2])

    def test_fade_expression(self):
        self.assertEqual(health_effect._fade_expression(0.5, dict(distance=100, range=25)), '0.5 * max(0.0, min(1.0, (100.0 - d) / 25.0))')


class Bc4Tests(unittest.TestCase):
    def test_block_decode(self):
        # endpoints 255 and 0, all 16 indices 0 (the first endpoint) and then 1 (the second)
        block0 = bytes([255, 0]) + bytes(6)
        block1 = bytes([255, 0]) + bytes([0x49, 0x92, 0x24] * 2)   # every 3-bit index 1
        out = health_effect._bc4(block0 + block1, 8, 4)
        self.assertTrue(np.allclose(out[:, :4], 1.0))
        self.assertTrue(np.allclose(out[:, 4:], 0.0))


if __name__ == '__main__':
    unittest.main()
