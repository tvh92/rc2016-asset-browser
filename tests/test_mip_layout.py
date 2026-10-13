import unittest

from rc2016.formats.texture import tiled_top_mip_size, top_mip_size
from rc2016.research.mip_layout import classify


class MipLayoutTests(unittest.TestCase):
    def test_candidate_layouts(self):
        size = lambda measure, levels: sum(measure(max(1, 256 >> l), max(1, 256 >> l), 71) for l in range(levels))
        self.assertEqual(classify(size(tiled_top_mip_size, 1), 256, 256, 71, 9), ['top'])
        self.assertEqual(classify(size(tiled_top_mip_size, 9), 256, 256, 71, 9), ['tiled_chain'])
        self.assertEqual(classify(size(top_mip_size, 9), 256, 256, 71, 9), ['linear_chain'])
        self.assertEqual(classify(3 * size(tiled_top_mip_size, 1), 256, 256, 71, 1, slices=3),
                         ['top', 'tiled_chain', 'linear_chain'])
        self.assertEqual(classify(123, 256, 256, 71, 9), ['unmatched'])
        self.assertEqual(classify(0, 0, 0, 71, 1), ['empty'])


if __name__ == '__main__':
    unittest.main()
