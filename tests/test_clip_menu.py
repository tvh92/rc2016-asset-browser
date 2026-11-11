"""The viewport's Animation list: it stays open while clips are tried and reopens as it was left."""
import unittest

from rc2016.app.browser import REST_POSE, AssetBrowser

MARK = "▶  "


class ClipMenuHost:
    """The parts of AssetBrowser the Animation list uses, without the rest of the window."""

    _open_clip_picker = AssetBrowser._open_clip_picker
    _close_clip_picker = AssetBrowser._close_clip_picker
    _fill_clip_menu = AssetBrowser._fill_clip_menu
    _shown_clips = AssetBrowser._shown_clips

    def __init__(self, tk, ttk, root, labels):
        self.tk, self.ttk = tk, ttk
        self.clip_button = ttk.Menubutton(root, text=REST_POSE)
        self.clip_button.pack()
        self.hide_clips_var = tk.BooleanVar(master=root, value=False)
        self.clip_popup = None
        self.clip_index = -1
        self.chosen = []
        self._fill_clip_menu(labels)

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
        host = ClipMenuHost(self.tk, self.ttk, self.root, labels)
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
        tree.focus_set()
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


if __name__ == "__main__":
    unittest.main()
