"""The build fingerprint follows every rc2016 module a pipeline build imports, not only formats/shaders/pipeline."""
from pathlib import Path
import tempfile
import unittest

from rc2016.pipeline.run import ROOT, imported_sources


class ImportedSourcesTests(unittest.TestCase):
    def test_pipeline_build_reaches_the_viewer_effect_decoders(self):
        found = {p.relative_to(ROOT).as_posix() for p in imported_sources(ROOT, ['rc2016/pipeline/holocon_effect.py'])}
        # ``from rc2016.viewer import holocon_effect as fx`` inside a function, and its own imports in turn
        self.assertIn('rc2016/viewer/holocon_effect.py', found)
        self.assertIn('rc2016/viewer/idle_effect.py', found)

    def test_follows_imports_transitively_and_ignores_other_packages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, text in {'rc2016/__init__.py': '', 'rc2016/a/__init__.py': '', 'rc2016/b/__init__.py': '',
                               'rc2016/a/start.py': 'import json\ndef f():\n    from rc2016.b import middle\n',
                               'rc2016/b/middle.py': 'from rc2016.b.leaf import X\nimport numpy\n',
                               'rc2016/b/leaf.py': 'X = 1\n', 'rc2016/b/unused.py': 'Y = 2\n'}.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            found = {p.relative_to(root.resolve()).as_posix() for p in imported_sources(root, ['rc2016/a/start.py'])}
        self.assertEqual(found, {'rc2016/b/__init__.py', 'rc2016/b/middle.py', 'rc2016/b/leaf.py'})


if __name__ == '__main__':
    unittest.main()
