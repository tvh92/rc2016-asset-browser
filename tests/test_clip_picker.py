"""The animation picker: when it opens, what it selects, and its search, filter and tick rules."""
import json
import time
import types
import unittest
from dataclasses import dataclass
from pathlib import Path

from rc2016.app.batch import PipelineBatchRunner
from rc2016.app.clip_picker import CLIP_PICKER_THRESHOLD, ClipPickerDialog, ClipSelection, needs_picker
from rc2016.app.clip_viewer import ClipViewer


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
        self.assertIn(str(dialog.boxes["off"]), dialog.tree.item(clip, "image"))
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


class BlendClipMap(unittest.TestCase):
    """The Blender scene path: picked clips reach the pipeline as one --clip-map file, in the same run."""

    def runner(self, selectors, choices, clip_only=None):
        return PipelineBatchRunner(selectors, Path("game"), Path("out"), Path("python.exe"), Path("blender.exe"),
                                   clip_choices=choices, clip_only=clip_only)

    def test_picked_models_get_a_clip_map_and_unpicked_models_keep_every_clip(self):
        runner = self.runner(["a/hero.model", "b/npc.model"], {"a/hero.model": ("Run00", "Wave: Idle")})
        clip_map = runner._write_clip_map()
        try:
            self.assertEqual(json.loads(clip_map.read_text(encoding="utf-8")),
                             {"a/hero.model": ["name:Run00", "name:Idle"]})
            command = runner.command(runner.selectors, (), clip_map)
            self.assertEqual(command[command.index("--clip-map") + 1], str(clip_map))
            self.assertNotIn("--clip", command)
        finally:
            clip_map.unlink()

    def test_an_empty_choice_is_written_as_an_empty_list(self):
        runner = self.runner(["a/hero.model"], {"a/hero.model": ()})
        clip_map = runner._write_clip_map()
        try:
            self.assertEqual(json.loads(clip_map.read_text(encoding="utf-8")), {"a/hero.model": []})
        finally:
            clip_map.unlink()

    def test_no_choices_writes_no_file_and_the_viewer_single_clip_keeps_its_own_run(self):
        self.assertIsNone(self.runner(["a/hero.model"], {})._write_clip_map())
        runner = self.runner(["a/hero.model"], {"a/hero.model": ("Run00",)}, clip_only=("a/hero.model", "Run01"))
        self.assertIsNone(runner._write_clip_map())

    def test_clip_map_is_not_added_to_a_run_that_selects_clips(self):
        runner = self.runner(["a/hero.model"], {})
        command = runner.command(["a/hero.model"], ("name:Run01",), Path("map.json"))
        self.assertNotIn("--clip-map", command)
        self.assertIn("--clip", command)


if __name__ == "__main__":
    unittest.main()


class FakeViewport:
    """Records what the viewer sends; the real viewport needs an OpenGL context."""
    can_animate = True

    def __init__(self):
        self.meshes, self.poses = [], []

    def set_mesh(self, payload):
        self.meshes.append(payload)

    def set_pose(self, frame):
        self.poses.append(frame)

    def clear(self, _message=None):
        pass

    def clear_pose(self):
        pass

    def set_effects_running(self, _running, reset=False):
        pass


class FakeLibrary:
    rows = [1]
    clips = []
    animset_data = {}

    def __init__(self, labels):
        self._labels = labels

    def labels(self):
        return list(self._labels)


class FakeLoader:
    """Stands in for SourcePreviewLoader: counts the model loads and answers poses for a fixed clip list."""

    def __init__(self, labels):
        self.labels, self.loads, self.animation, self.archive = labels, 0, None, None

    def load(self, _root, model_id, _path, _lod, _look, _output, _defer):
        self.loads += 1
        return f"mesh:{model_id}"

    def load_animation(self, _root, _model_id, _path, _output, _borrowed):
        self.animation = FakeLibrary(self.labels)

    def pose(self, index):
        return dict(name=self.labels[index], kind="skinned", fps=10.0, frames=[index, index, index], joint_count=1)


class ClipViewerPlayback(unittest.TestCase):
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

    def setUp(self):
        self.viewer = ClipViewer(self.tk, self.ttk, self.ttk.Frame(self.root), "source", None, False, False)
        self.addCleanup(self.viewer.close)
        self.viewer.viewport = self.viewer.playback.viewport = self.viewport = FakeViewport()
        self.viewer.loader = self.loader = FakeLoader(["Idle", "Run", "Jump"])
        self.hero = types.SimpleNamespace(model_id="hero", archive_path="a/hero.model")

    def settle(self):
        """Wait for the queued reads, then let the Tk loop apply them."""
        deadline = time.time() + 5
        while self.viewer.pending and time.time() < deadline:
            self.viewer.pending[0].exception(timeout=5)
            self.root.update()
        self.root.update()

    def test_first_clip_loads_the_model_and_starts_playing(self):
        self.viewer.show(self.hero, "Run")
        self.settle()
        self.assertEqual(self.loader.loads, 1)
        self.assertEqual(self.viewport.meshes, ["mesh:hero"])
        self.assertEqual(self.viewport.poses, [1])
        self.assertTrue(self.viewer.playback.playing)
        self.assertEqual(self.viewer.title.get(), "Run")
        self.assertIn("1/3", self.viewer.status.get())

    def test_another_clip_of_the_same_model_reuses_the_loaded_model(self):
        self.viewer.show(self.hero, "Run")
        self.settle()
        self.viewer.show(self.hero, "Jump")
        self.settle()
        self.assertEqual(self.loader.loads, 1)
        self.assertEqual(self.viewport.meshes, ["mesh:hero"])
        self.assertEqual(self.viewport.poses[-1], 2)

    def test_a_clip_of_another_model_loads_that_model(self):
        self.viewer.show(self.hero, "Run")
        self.settle()
        other = types.SimpleNamespace(model_id="npc", archive_path="a/npc.model")
        self.viewer.show(other, "Idle")
        self.settle()
        self.assertEqual(self.loader.loads, 2)
        self.assertEqual(self.viewport.meshes, ["mesh:hero", "mesh:npc"])

    def test_pause_stops_the_frame_clock_and_scrubbing_moves_the_frame(self):
        self.viewer.show(self.hero, "Run")
        self.settle()
        self.viewer.playback.toggle()
        self.assertFalse(self.viewer.playback.playing)
        self.assertIsNone(self.viewer.playback.job)
        self.viewer.playback.position.set(2)
        self.viewer.playback.scrub()
        self.assertEqual(self.viewer.playback.frame, 2)

    def test_a_clip_the_model_does_not_offer_is_reported_in_place(self):
        self.viewer.show(self.hero, "Missing")
        self.settle()
        self.assertIsNone(self.viewer.playback.animation)
        self.assertEqual(self.viewport.meshes, ["mesh:hero"])   # the model still shows
        self.assertIn("not offered", self.viewer.status.get())

    def test_the_offered_clips_drop_the_cutscene_shots_unless_the_export_is_cinematic(self):
        from rc2016.viewer.animation import AnimationLibrary
        from rc2016.formats.animation_sources import CINEMATIC_SOURCE
        library = AnimationLibrary.__new__(AnimationLibrary)
        library.rows = [1]
        library.clips = [dict(animset="a.animset", offset=0, name="Hookshot_Pull"),
                         dict(animset="shot.cinematic2", offset=0, name="Shot", source=CINEMATIC_SOURCE)]
        library.animset_data = {"a.animset": b"", "shot.cinematic2": b""}
        self.assertEqual(library.labels(), ["a: Hookshot_Pull", "Shot"])
        self.assertEqual(self.viewer._offered(library).labels(), ["Hookshot_Pull"])
        cinematic = ClipViewer(self.tk, self.ttk, self.ttk.Frame(self.root), "source", None, False, True)
        self.addCleanup(cinematic.close)
        self.assertEqual(cinematic._offered(library).labels(), ["a: Hookshot_Pull", "Shot"])


class DialogViewer(unittest.TestCase):
    setUpClass = classmethod(ClipViewerPlayback.setUpClass.__func__)
    tearDownClass = classmethod(ClipViewerPlayback.tearDownClass.__func__)

    def test_no_source_means_no_viewport_and_clicks_do_nothing(self):
        listed = [listed_model("hero", 12, prefix="Run")]
        dialog = ClipPickerDialog(self.tk, self.ttk, self.root, listed)
        self.addCleanup(lambda: dialog.top.winfo_exists() and dialog.top.destroy())
        dialog.rebuild()
        self.assertIsNone(dialog.viewer)
        dialog._show_clip("c0.0")

    def test_a_clicked_clip_is_handed_to_the_viewer_with_its_label(self):
        listed = [listed_model("hero", 12, prefix="Run")]
        dialog = ClipPickerDialog(self.tk, self.ttk, self.root, listed, source_root="source")
        self.addCleanup(lambda: dialog.top.winfo_exists() and dialog.top.destroy())
        self.addCleanup(dialog.viewer.close)
        shown = []
        dialog.viewer.show = lambda record, label: shown.append((record.model_id, label))
        dialog.rebuild()
        dialog._show_clip("c0.3")
        self.assertEqual(shown, [("hero", "Run03")])
