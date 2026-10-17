import time
import unittest

from rc2016.app.texture_grid import ThumbnailFeed


class ThumbnailFeedTests(unittest.TestCase):
    def wait_for(self, feed, count, seconds=5.0):
        results, end = [], time.time() + seconds
        while len(results) < count and time.time() < end:
            try:
                results.append(feed.results.get(timeout=0.05))
            except Exception:
                pass
        return results

    def test_decodes_wanted_paths_and_reports_failures(self):
        def decode(path):
            if path.startswith('bad'):
                raise ValueError('cannot decode')
            return path.upper()

        feed = ThumbnailFeed(decode)
        try:
            feed.want(['a', 'bad1', 'b'])
            results = self.wait_for(feed, 3)
        finally:
            feed.close()
        self.assertEqual(sorted(results), [('a', 'A'), ('b', 'B'), ('bad1', None)])

    def test_newest_request_replaces_the_queue(self):
        seen = []
        feed = ThumbnailFeed(lambda path: seen.append(path) or path)
        try:
            feed.clear()
            feed.want(['visible_1', 'visible_2'])
            self.wait_for(feed, 2)
            self.assertEqual(seen, ['visible_1', 'visible_2'])
        finally:
            feed.close()


if __name__ == '__main__':
    unittest.main()
