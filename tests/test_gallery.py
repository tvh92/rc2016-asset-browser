"""Gallery layout maths, image cache, selection and queue rules (no Tk)."""
from dataclasses import dataclass
import unittest

from rc2016.app import gallery as g


@dataclass(frozen=True)
class Rec:
    model_id: str
    archive_path: str = 'a/b/thing.model'
    used_by: str = ''


class LayoutTests(unittest.TestCase):
    def test_columns_and_rows(self):
        self.assertEqual(g.column_count(700, 216), 3)
        self.assertEqual(g.column_count(100, 216), 1)       # never zero columns
        self.assertEqual((g.row_count(0, 3), g.row_count(7, 3), g.row_count(9, 3)), (0, 3, 3))

    def test_visible_rows_with_overscan(self):
        # 100 rows of 50 px, 200 px viewport at offset 500 -> rows 10..13 visible
        self.assertEqual(g.visible_rows(500, 200, 50, 100, overscan=0), (10, 14))
        self.assertEqual(g.visible_rows(500, 200, 50, 100, overscan=1), (9, 15))
        self.assertEqual(g.visible_rows(0, 200, 50, 100, overscan=1), (0, 5))
        self.assertEqual(g.visible_rows(4900, 200, 50, 100, overscan=1), (97, 100))
        self.assertEqual(g.visible_rows(0, 200, 50, 0), (0, 0))
        self.assertEqual(g.visible_rows(520, 200, 50, 100, overscan=0), (10, 15))   # partial rows count

    def test_visible_tiles_clamp_to_total(self):
        self.assertEqual(g.visible_tiles(0, 100, 50, 3, 7, overscan=0), (0, 6))
        self.assertEqual(g.visible_tiles(100, 400, 50, 3, 7, overscan=0), (6, 7))
        self.assertEqual(g.visible_tiles(0, 100, 50, 3, 0), (0, 0))

    def test_tile_at(self):
        args = dict(cell_width=100, cell_height=50, columns=3, total=7)
        self.assertEqual(g.tile_at(10, 10, 0, **args), 0)
        self.assertEqual(g.tile_at(250, 10, 0, **args), 2)
        self.assertEqual(g.tile_at(10, 10, 100, **args), 6)       # scrolled two rows
        self.assertIsNone(g.tile_at(150, 10, 100, **args))        # beyond the last tile
        self.assertIsNone(g.tile_at(310, 10, 0, **args))          # right of the grid
        self.assertIsNone(g.tile_at(-5, 10, 0, **args))

    def test_scroll_to_show(self):
        self.assertEqual(g.scroll_to_show(0, 200, 300, 100, 3), 0)        # above: scroll up to it
        self.assertEqual(g.scroll_to_show(9, 0, 300, 100, 3), 100)        # below: align its bottom edge
        self.assertEqual(g.scroll_to_show(4, 100, 300, 100, 3), 100)      # already visible


class CacheTests(unittest.TestCase):
    def test_lru_is_bounded_and_evicts_least_recently_used(self):
        cache = g.ImageCache(3)
        for key in 'abcd':
            cache.put(key, key.upper())
        self.assertEqual(len(cache), 3)
        self.assertNotIn('a', cache)
        self.assertEqual(cache.get('b'), 'B')     # touch b: c is now the oldest
        cache.put('e', 'E')
        self.assertEqual(sorted(cache.items), ['b', 'd', 'e'])

    def test_thousands_of_tiles_stay_bounded(self):
        cache = g.ImageCache()
        for number in range(5000):
            cache.put(number, object())
        self.assertEqual(len(cache), g.IMAGE_CACHE_SIZE)

    def test_discard_and_clear(self):
        cache = g.ImageCache(5)
        cache.put('a', 1)
        cache.discard('a')
        cache.discard('missing')
        self.assertEqual(len(cache), 0)


class TextTests(unittest.TestCase):
    def test_elide(self):
        self.assertEqual(g.elide('short', len, 10), 'short')
        out = g.elide('a_rather_long_model_name', len, 10)
        self.assertLessEqual(len(out), 10)
        self.assertTrue(out.endswith('…'))
        self.assertEqual(g.elide('abc', lambda s: 99, 5), '…')

    def test_display_name(self):
        self.assertEqual(g.display_name(Rec('1', 'x/y/npc_qwark_robot.model', 'Qwark, Other')), 'Qwark')
        self.assertEqual(g.display_name(Rec('1', 'rc1/mobys/moby0/moby-0_lod0.model')), 'moby-0_lod0')


class SelectionTests(unittest.TestCase):
    def test_many_selected_models_are_shown(self):
        sel = [Rec('1'), Rec('2')]
        records, kind, total = g.choose_records(sel, [Rec('9')], set())
        self.assertEqual((records, kind, total), (sel, 'selection', 2))

    def test_single_selection_inside_the_shown_list_keeps_it(self):
        self.assertEqual(g.choose_records([Rec('2')], [Rec('9')], {'1', '2'})[1], 'keep')

    def test_single_or_no_selection_outside_the_list_shows_the_search_results(self):
        filtered = [Rec(str(n)) for n in range(5)]
        records, kind, total = g.choose_records([Rec('77')], filtered, {'1'})
        self.assertEqual((len(records), kind, total), (5, 'filter', 5))
        self.assertEqual(g.choose_records([], filtered, set())[1], 'filter')

    def test_search_results_are_capped_and_loose_records_ignored(self):
        filtered = [Rec(str(n)) for n in range(10)] + [object()]
        records, kind, total = g.choose_records([], filtered, set(), cap=4)
        self.assertEqual((len(records), total), (4, 10))


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.records = [Rec(str(n)) for n in range(10)]

    def test_visible_missing_first_then_after_then_before(self):
        ready = {'4', '6'}
        queue = g.render_queue(self.records, lambda m: m in ready, 3, 6)
        self.assertEqual(queue, ['3', '5', '7', '8', '9', '0', '1', '2'])

    def test_limit(self):
        self.assertEqual(g.render_queue(self.records, lambda m: False, 5, 7, limit=3), ['5', '6', '7'])

    def test_nothing_missing(self):
        self.assertEqual(g.render_queue(self.records, lambda m: True, 0, 4), [])

    def test_restart_only_when_visible_tiles_are_not_near_the_front_of_the_queue(self):
        pending = [str(n) for n in range(500)]
        self.assertFalse(g.needs_restart([], pending))
        self.assertFalse(g.needs_restart(['0', '5'], pending))
        self.assertTrue(g.needs_restart(['0', '300'], pending))
        self.assertTrue(g.needs_restart(['0'], []))


if __name__ == '__main__':
    unittest.main()
