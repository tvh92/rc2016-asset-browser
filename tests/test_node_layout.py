import unittest

from rc2016.pipeline.node_layout import COLUMN_GAP, ROW_GAP, layout_positions


class NodeLayoutTests(unittest.TestCase):
    def test_inputs_sit_left_of_what_they_feed(self):
        positions = layout_positions(['tex', 'mix', 'out'], [('tex', 'mix'), ('mix', 'out')],
                                     {'tex': (200, 100), 'mix': (200, 100), 'out': (200, 100)})
        self.assertLess(positions['tex'][0], positions['mix'][0])
        self.assertLess(positions['mix'][0], positions['out'][0])
        self.assertEqual(positions['out'][0], -200)   # the rightmost column ends at x = 0

    def test_longest_chain_decides_the_column(self):
        # 'value' feeds the output directly and through 'mix': it must sit in the column left of 'mix'.
        positions = layout_positions(['value', 'mix', 'out'], [('value', 'mix'), ('mix', 'out'), ('value', 'out')],
                                     {'value': (100, 100), 'mix': (100, 100), 'out': (100, 100)})
        self.assertLess(positions['value'][0], positions['mix'][0])

    def test_nodes_in_one_column_do_not_overlap(self):
        ids = ['a', 'b', 'c', 'out']
        positions = layout_positions(ids, [('a', 'out'), ('b', 'out'), ('c', 'out')],
                                     {i: (240, 150) for i in ids})
        ys = sorted(positions[i][1] for i in 'abc')
        for earlier, later in zip(ys, ys[1:]):
            self.assertGreaterEqual(later - earlier, 150 + ROW_GAP)
        self.assertLess(positions['a'][0], positions['out'][0] - COLUMN_GAP)

    def test_cycles_and_unknown_links_do_not_hang(self):
        positions = layout_positions(['a', 'b'], [('a', 'b'), ('b', 'a'), ('a', 'missing')], {})
        self.assertEqual(set(positions), {'a', 'b'})


if __name__ == '__main__':
    unittest.main()
