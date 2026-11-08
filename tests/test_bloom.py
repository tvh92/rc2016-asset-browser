"""The decoded bloom (rc2016/viewer/bloom.py): pass plan, kernel invariants and the CPU reference."""
import unittest

import numpy as np

from rc2016.viewer import bloom


class BloomPlanTests(unittest.TestCase):
    def test_captured_sizes(self):
        self.assertEqual(bloom.padded_half(1920, 1080), (960, 544))
        self.assertEqual(bloom.bloom_size(1920, 1080), (480, 272))
        self.assertEqual(bloom.level_sizes(1920, 1080), [(960, 544), (480, 544), (480, 272), (240, 272), (240, 136), (120, 136),
                                                         (120, 68), (60, 68), (60, 34), (30, 34), (30, 17)])

    def test_pass_list_matches_the_captured_draws(self):
        plan = bloom.passes(1920, 1080, 3.7123)
        kinds = [p['kind'] for p in plan]
        self.assertEqual(kinds, ['prefilter'] + ['down'] * 10 + ['up'] * 8 + ['prefilter'] + ['down'] * 4 + ['up'] * 2)
        lerps = [(p['dst'], p['alpha']) for p in plan if p['blend'] == 'lerp']
        self.assertEqual(lerps, [('D8', 0.75), ('D6', 0.8), ('D4', 0.9)])
        self.assertEqual(plan[-1]['blend'], 'add')
        self.assertEqual(plan[-1]['dst'], 'bloom')
        first_down = plan[1]
        self.assertAlmostEqual(first_down['offset'][0], 0.0017563, places=6)   # 1.686 / 960 in the capture
        self.assertAlmostEqual(plan[2]['offset'][1], 0.0030993, places=6)      # 1.686 / 544
        self.assertAlmostEqual(plan[11]['offset'][1], 1 / 17, places=6)        # first upsample: 1 / 17 vertical
        self.assertAlmostEqual(plan[0]['gain'], 3.7123 / 8)

    def test_other_sizes_stay_divisible(self):
        for width, height in ((768, 768), (1000, 700), (1280, 720)):
            for w, h in bloom.level_sizes(width, height)[:1]:
                self.assertEqual((w % 32, h % 32), (0, 0))
            self.assertGreaterEqual(min(min(s) for s in bloom.level_sizes(width, height)), 1)

    def test_gate_takes_the_display_exposure(self):
        # The game display exposes the scene by its captured multiplier; ACES and Neutral by the lighting exposure alone.
        self.assertEqual(bloom.gate_exposure(2.0, True, 4.55), 9.1)
        self.assertEqual(bloom.gate_exposure(2.0, False, 4.55), 2.0)
        # A lit pane of luma 0.9 opens the gate at the game exposure but not under the other tonemaps at exposure 1.
        pane = np.full((64, 64, 3), 0.9, np.float32)
        empty = np.zeros_like(pane)
        self.assertGreater(float(bloom.reference(empty, pane, bloom.gate_exposure(1.0, True, 4.55)).max()), 0.0)
        self.assertEqual(float(bloom.reference(empty, pane, bloom.gate_exposure(1.0, False, 4.55)).max()), 0.0)

    def test_kernels_are_normalised(self):
        self.assertAlmostEqual(2 * bloom.DOWN_SIDE + bloom.DOWN_CENTRE, 1.0, places=4)
        self.assertAlmostEqual(2 * (bloom.UP_W0 + bloom.UP_W1 + bloom.UP_W2), 1.0, places=4)


class BloomReferenceTests(unittest.TestCase):
    def test_dark_scene_has_no_bloom(self):
        dark = np.full((64, 64, 3), 0.05, np.float32)
        self.assertEqual(float(bloom.reference(dark, dark, 4.55).max()), 0.0)

    def test_flat_image_passes_through_the_pyramid(self):
        # Every kernel sums to one and each lerp mixes two equal images, so a flat bright image keeps its bright-pass value.
        size = bloom.padded_half(128, 128)
        flat = np.full((size[1], size[0], 3), 6.0, np.float32)
        out = bloom.reference_chain(flat, flat, 4.0)
        self.assertEqual(out.shape, (size[1] // 2, size[0] // 2, 3))
        expected = bloom.LIT_WEIGHT        # c / luma is 1 per channel and the gate saturates (4 / 8 x 6 - 0.5 > 1)
        np.testing.assert_allclose(out, expected, atol=1e-3)

    def test_added_light_blooms_in_the_sprite_chain(self):
        size = bloom.padded_half(128, 128)
        before = np.zeros((size[1], size[0], 3), np.float32)
        after = before.copy()
        after[20:30, 20:30] = (1.0, 8.0, 12.0)
        out = bloom.reference_chain(before, after, 4.55)
        self.assertGreater(float(out.max()), 0.1)
        lit = bloom.reference_chain(after, after, 4.55)   # the same light present before the pass: lit chain only
        self.assertGreater(float(out[..., 2].max()), 0.0)
        self.assertGreater(float(lit.max()), 0.0)


if __name__ == '__main__':
    unittest.main()
