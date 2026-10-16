import unittest

from rc2016.app.layout import HORIZONTAL, VERTICAL, SplitLayout, Splitter, regrid


class SplitLayoutTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")
        self.root.geometry("800x600")

    def tearDown(self):
        self.root.destroy()

    def settle(self):
        self.root.update_idletasks()
        self.root.update()

    def test_sash_sizes_are_kept_per_layout(self):
        from tkinter import ttk
        layout = SplitLayout()
        split = Splitter(self.root, side=200, stacked=150)
        split.pack(fill="both", expand=True)
        layout.register(split)
        ttk.Label(split.first, text="a").grid()
        self.settle()
        self.assertEqual((split.second.winfo_width(), split.second.winfo_height()), (200, 600))
        layout.set_mode(HORIZONTAL)
        self.settle()
        self.assertEqual((split.second.winfo_width(), split.second.winfo_height()), (800, 150))
        split.sizes[True] = 250   # a drag in the stacked layout
        split._place()
        layout.set_mode(VERTICAL)
        self.settle()
        self.assertEqual(split.second.winfo_width(), 200)
        layout.set_mode(HORIZONTAL)
        self.settle()
        self.assertEqual(split.second.winfo_height(), 250)

    def test_fixed_first_pane_and_minimum(self):
        split = Splitter(self.root, side=300, stacked=100, fixed_first=True)
        split.pack(fill="both", expand=True)
        self.settle()
        self.assertEqual(split.first.winfo_width(), 300)
        split.set_stacked(True)
        self.settle()
        self.assertEqual(split.first.winfo_height(), Splitter.MINIMUM)   # 100 is below the minimum

    def test_unknown_mode_is_ignored_and_listeners_hear_changes(self):
        layout = SplitLayout("nonsense")
        self.assertEqual(layout.mode, VERTICAL)
        heard = []
        layout.listen(heard.append)
        layout.set_mode("Diagonal")
        layout.set_mode(HORIZONTAL)
        self.assertEqual(heard, [False, True])

    def test_regrid_keeps_a_hidden_widget_hidden(self):
        from tkinter import ttk
        frame = ttk.Frame(self.root)
        frame.pack()
        shown, hidden = ttk.Label(frame, text="s"), ttk.Label(frame, text="h")
        shown.grid(row=0, column=0)
        hidden.grid(row=1, column=0)
        hidden.grid_remove()
        regrid(shown, row=2, column=1)
        regrid(hidden, row=3, column=1)
        self.assertEqual(shown.winfo_manager(), "grid")
        self.assertEqual(hidden.winfo_manager(), "")
        hidden.grid()
        self.assertEqual((hidden.grid_info()["row"], hidden.grid_info()["column"]), (3, 1))


if __name__ == "__main__":
    unittest.main()
