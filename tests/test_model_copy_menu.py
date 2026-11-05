"""Copy model name / path from the model list's right-click menu (needs a display for Tk)."""
from types import SimpleNamespace
import unittest

from rc2016.app.window import AssetBrowserApp


class CopyModelTextTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as error:  # no Tk runtime or no display
            self.skipTest(f"Tk unavailable: {error}")

    def tearDown(self):
        self.root.destroy()

    def copied(self, stem):
        """Run the menu's copy action for a model whose path is written with backslashes, as the scan stores it."""
        parts = ["environment", "global", "gameplay", "gbl_gp_raritanium_crystal_01", "gbl_gp_raritanium_crystal_01", "gbl_gp_raritanium_crystal_01.model"]
        record = SimpleNamespace(relative_model="\\".join(parts))
        window = SimpleNamespace(table=self.root, _menu_record=record, _record_path=AssetBrowserApp._record_path)
        AssetBrowserApp._copy_model_text(window, stem=stem)
        return self.root.clipboard_get()

    def test_copy_model_name_is_the_file_stem(self):
        self.assertEqual(self.copied(stem=True), "gbl_gp_raritanium_crystal_01")

    def test_copy_model_path_uses_forward_slashes(self):
        self.assertEqual(self.copied(stem=False),
                         "environment/global/gameplay/gbl_gp_raritanium_crystal_01/"
                         "gbl_gp_raritanium_crystal_01/gbl_gp_raritanium_crystal_01.model")


if __name__ == "__main__":
    unittest.main()
