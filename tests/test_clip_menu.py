"""The viewport's Animation list: it stays open while clips are tried, reopens as it was left, shows clip lengths,
plays the row the arrow keys stop on, and steps to the previous and next clip."""
import unittest

from rc2016.app.browser import PREVIEW_DELAY_MS, REST_POSE, AssetBrowser, clip_length, step_clip

MARK = "▶  "


class ClipMenuHost:
    """The parts of AssetBrowser the Animation list uses, without the rest of the window."""

    _open_clip_picker = AssetBrowser._open_clip_picker
    _close_clip_picker = AssetBrowser._close_clip_picker
    _fill_clip_menu = AssetBrowser._fill_clip_menu
    _shown_clips = AssetBrowser._shown_clips
    _clip_order = AssetBrowser._clip_order
    _step_clip = AssetBrowser._step_clip
    _enable_clip_controls = AssetBrowser._enable_clip_controls

    def __init__(self, tk, ttk, root, labels, seconds=()):
        self.tk, self.ttk = tk, ttk
        self.clip_button = ttk.Menubutton(root, text=REST_POSE)
        self.clip_button.pack()
        self.hide_clips_var = tk.BooleanVar(master=root, value=False)
        self.clip_popup = None
        self.clip_index = -1
        self.chosen = []
        self.clip_steps = []
        self._fill_clip_menu(labels, (), (), seconds)

    def _choose_clip(self, index, label):
        self.clip_index = index
        self.chosen.append(label)


class ClipMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        from tkinter import ttk
        try:
            cls.root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display for Tk")
        cls.tk, cls.ttk = tk, ttk

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def host(self):
        labels = ["Run_Fwd", "Run_Back", "Run_Left", "Jump_Start", "Jump_Land", "Idle"]
        host = ClipMenuHost(self.tk, self.ttk, self.root, labels, [1.25, 0.5, 0.5, 2.0, 75.0, None])
        self.addCleanup(host._close_clip_picker)
        self.addCleanup(host.clip_button.destroy)
        return host

    def tree(self, host):
        return next(w for w in host.clip_popup.winfo_children()[0].winfo_children() if w.winfo_class() == "Treeview")

    def item(self, tree, text):
        def walk(parent=""):
            for item in tree.get_children(parent):
                yield item
                yield from walk(item)
        return next(item for item in walk() if tree.item(item, "text").removeprefix(MARK).startswith(text))

    def test_choosing_a_clip_plays_it_keeps_the_list_open_and_moves_the_mark(self):
        host = self.host()
        host._open_clip_picker()
        tree = self.tree(host)
        self.assertEqual(tree.item("clip:-1", "text"), MARK + REST_POSE)
        run = self.item(tree, "Run")
        tree.item(run, open=True)
        fwd = self.item(tree, "Run_Fwd")
        tree.focus(fwd)
        tree.focus_force()   # a test run in the background is not handed the focus otherwise
        self.root.update()
        tree.event_generate("<Return>")
        self.assertIsNotNone(host.clip_popup)
        self.assertEqual(host.chosen, ["Run_Fwd"])
        self.assertEqual(tree.item(fwd, "text"), MARK + "Run_Fwd")
        self.assertEqual(tree.item("clip:-1", "text"), REST_POSE)
        self.assertTrue(tree.item(run, "open"))

    def test_the_list_reopens_with_its_folders_and_search(self):
        host = self.host()
        host._open_clip_picker()
        tree = self.tree(host)
        jump = self.item(tree, "Jump")
        tree.item(jump, open=True)
        tree.event_generate("<<TreeviewOpen>>")
        self.root.update()
        host._close_clip_picker()
        host._open_clip_picker()
        self.assertTrue(self.tree(host).item(jump, "open"))
        host.clip_search = "land"
        host._close_clip_picker()
        host._open_clip_picker()
        tree = self.tree(host)
        self.assertEqual([tree.item(i, "text") for i in tree.get_children()], [MARK + REST_POSE, "Jump_Land"])

    def test_a_new_clip_list_starts_closed_and_unsearched(self):
        host = self.host()
        host.clip_open_folders, host.clip_search = {"group/Jump"}, "land"
        host._fill_clip_menu(["Walk"], ())
        self.assertEqual((host.clip_open_folders, host.clip_search), (set(), ""))

    def test_each_clip_row_shows_its_length(self):
        host = self.host()
        host._open_clip_picker()
        tree = self.tree(host)
        self.assertEqual(tree.set("clip:0", "length"), "1.2 s")
        self.assertEqual(tree.set("clip:-1", "length"), "")

    def test_the_arrow_keys_play_the_row_they_stop_on(self):
        host = self.host()
        host._open_clip_picker()
        tree = self.tree(host)
        tree.focus_force()   # a test run in the background is not handed the focus otherwise
        self.root.update()
        for item in ("clip:5", "clip:4"):   # two presses quickly: only the last row plays
            tree.focus(item)
            tree.event_generate("<KeyRelease-Down>")
        self.root.after(PREVIEW_DELAY_MS + 100, lambda: None)
        self.root.update()
        self.root.after(PREVIEW_DELAY_MS + 100, self.root.quit)
        self.root.mainloop()
        self.assertEqual(host.chosen, ["Jump_Land"])
        self.assertTrue(tree.item("clip:4", "text").startswith(MARK))

    def test_previous_and_next_follow_the_list_order_and_wrap(self):
        host = self.host()
        self.assertEqual(host._clip_order(), [4, 3, 1, 0, 2, 5])   # Jump folder, Run folder, Other
        host._step_clip(1)
        host._step_clip(1)
        host._step_clip(-1)
        host._step_clip(-1)
        self.assertEqual(host.chosen, ["Jump_Land", "Jump_Start", "Jump_Land", "Idle"])
        host._open_clip_picker()
        host._step_clip(1)   # the open list has the keyboard; the buttons cannot be reached
        self.assertEqual(len(host.chosen), 4)


class ClipHelpers(unittest.TestCase):
    def test_lengths_read_in_seconds_then_minutes(self):
        self.assertEqual([clip_length(v) for v in (None, float("nan"), 0, 1.25, 59.94, 65, 600)],
                         ["", "", "0.0 s", "1.2 s", "59.9 s", "1:05", "10:00"])

    def test_step_wraps_and_starts_from_either_end(self):
        self.assertEqual(step_clip([], None, 1), None)
        self.assertEqual([step_clip("abc", "z", 1), step_clip("abc", "z", -1)], ["a", "c"])
        self.assertEqual([step_clip("abc", "c", 1), step_clip("abc", "a", -1)], ["a", "c"])


if __name__ == "__main__":
    unittest.main()
