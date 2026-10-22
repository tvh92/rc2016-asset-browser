"""The animation picker: when it opens, what it selects, and its search, filter and tick rules."""
import unittest
from dataclasses import dataclass

from rc2016.app.clip_picker import CLIP_PICKER_THRESHOLD, ClipPickerDialog, ClipSelection, needs_picker


@dataclass
class Record:
    model_id: str
    display_name: str


def listed_model(model_id, count, prefix="Idle"):
    labels = [f"{prefix}{index:02d}" for index in range(count)]
    return Record(model_id, model_id.title()), labels


class PickerTrigger(unittest.TestCase):
    def test_threshold_is_ten_and_only_more_than_ten_opens_the_picker(self):
        self.assertEqual(CLIP_PICKER_THRESHOLD, 10)
        self.assertFalse(needs_picker([listed_model("a", 10)]))
        self.assertTrue(needs_picker([listed_model("a", 11)]))

    def test_one_large_model_opens_it_even_beside_small_ones(self):
        listed = [listed_model("small", 3), listed_model("big", 12)]
        self.assertTrue(needs_picker(listed))

    def test_no_models_or_only_small_ones_do_not_open_it(self):
        self.assertFalse(needs_picker([]))
        self.assertFalse(needs_picker([listed_model("a", 2), listed_model("b", 9)]))


class SelectionRules(unittest.TestCase):
    def setUp(self):
        self.selection = ClipSelection([
            listed_model("hero", 4, prefix="Run"),
            listed_model("npc", 3, prefix="Wave"),
        ])

    def test_everything_starts_ticked_and_untouched_models_are_absent_from_the_result(self):
        self.assertEqual(self.selection.totals(), (7, 7))
        self.assertEqual(self.selection.result(), {})

    def test_result_lists_only_the_ticked_clips_of_partly_ticked_models(self):
        self.selection.set_ticked(0, [1, 3], False)
        self.selection.set_ticked(1, [0, 1, 2], False)
        self.assertEqual(self.selection.totals(), (2, 7))
        self.assertEqual(self.selection.result(), {"hero": ("Run00", "Run02"), "npc": ()})

    def test_search_matches_every_word_case_insensitively(self):
        self.assertEqual(self.selection.visible(0, "RUN 02"), [2])
        self.assertEqual(self.selection.visible(0, "wave"), [])
        self.assertEqual(self.selection.visible(1, "  "), [0, 1, 2])

    def test_ticked_and_unticked_filters_follow_the_current_state(self):
        self.selection.set_ticked(0, [0, 2], False)
        self.assertEqual(self.selection.visible(0, mode="ticked"), [1, 3])
        self.assertEqual(self.selection.visible(0, mode="unticked"), [0, 2])

    def test_search_and_filter_combine(self):
        self.selection.set_ticked(0, [1], False)
        self.assertEqual(self.selection.visible(0, "run", "unticked"), [1])


class DialogRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        from tkinter import ttk
        try:
            cls.root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display for Tk")
        cls.root.withdraw()
        cls.tk, cls.ttk = tk, ttk

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def dialog(self):
        listed = [listed_model("hero", 12, prefix="Run"), listed_model("npc", 11, prefix="Wave")]
        dialog = ClipPickerDialog(self.tk, self.ttk, self.root, listed)
        self.addCleanup(lambda: dialog.top.winfo_exists() and dialog.top.destroy())
        dialog.rebuild()
        return dialog

    def test_space_toggles_the_focused_clip_and_refreshes_the_status(self):
        dialog = self.dialog()
        clip = next(item for item, entry in dialog.targets.items() if entry[3] and entry[0] == 0)
        dialog.tree.focus(clip)
        dialog._toggle_focus()
        self.assertEqual(dialog.selection.totals(), (22, 23))
        self.assertTrue(dialog.tree.item(clip, "text").startswith("[ ]"))
        self.assertIn("22 of 23", dialog.status.get())

    def test_tick_shown_follows_the_search(self):
        dialog = self.dialog()
        dialog.search.set("wave")
        dialog._tick_shown(False)
        self.assertEqual(dialog.selection.totals(), (12, 23))
        self.assertEqual(dialog.selection.result(), {"npc": ()})

    def test_escape_clears_the_search_before_cancelling(self):
        dialog = self.dialog()
        dialog.search.set("run")
        dialog._escape()
        self.assertEqual(dialog.search.get(), "")
        self.assertTrue(dialog.top.winfo_exists())


if __name__ == "__main__":
    unittest.main()
