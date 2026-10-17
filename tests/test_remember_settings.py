"""Tk variables remembered in the settings file (rc2016/common.remember_var); needs a display."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2016 import common


class RememberVarTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as error:  # no Tk runtime or no display
            self.skipTest(f"Tk unavailable: {error}")
        self.tk = tk
        self.tmp = tempfile.TemporaryDirectory()
        self.appdata = patch.dict(os.environ, {"APPDATA": self.tmp.name})
        self.appdata.start()

    def tearDown(self):
        self.appdata.stop()
        self.root.destroy()
        self.tmp.cleanup()

    def settings(self):
        return json.loads((Path(self.tmp.name) / "RC2016 Asset Browser" / "settings.json").read_text(encoding="utf-8"))

    def test_flag_is_saved_on_change_and_restored_by_a_new_variable(self):
        first = common.remember_var(self.tk.BooleanVar(value=True), "test.flag")
        first.set(False)
        self.assertEqual(self.settings()["test.flag"], "0")
        again = common.remember_var(self.tk.BooleanVar(value=True), "test.flag")
        self.assertFalse(again.get())

    def test_text_outside_the_allowed_values_keeps_the_default(self):
        common.remember_var(self.tk.StringVar(value="a"), "test.choice", ("a", "b")).set("b")
        restored = common.remember_var(self.tk.StringVar(value="a"), "test.choice", ("a", "b"))
        self.assertEqual(restored.get(), "b")
        (Path(self.tmp.name) / "RC2016 Asset Browser" / "settings.json").write_text('{"test.choice": "zzz"}', encoding="utf-8")
        self.assertEqual(common.remember_var(self.tk.StringVar(value="a"), "test.choice", ("a", "b")).get(), "a")

    def test_other_settings_are_kept(self):
        folder = Path(self.tmp.name) / "RC2016 Asset Browser"
        folder.mkdir(parents=True)
        (folder / "settings.json").write_text('{"source": "D:/game"}', encoding="utf-8")
        common.remember_var(self.tk.BooleanVar(value=False), "test.flag").set(True)
        self.assertEqual(self.settings()["source"], "D:/game")


if __name__ == "__main__":
    unittest.main()
