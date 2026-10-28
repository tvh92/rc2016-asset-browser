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


def noted_model(model_id, labels, notes):
    """A listed model whose clips carry notes (None, 'duplicate', 'layer' or 'unplayable'), as AnimationLibrary gives."""
    return Record(model_id, model_id.title()), list(labels), list(notes)


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

    def test_threshold_zero_opens_for_any_model_with_a_clip(self):
        self.assertTrue(needs_picker([listed_model("a", 1)], threshold=0))
        self.assertFalse(needs_picker([listed_model("a", 0)], threshold=0))
        self.assertFalse(needs_picker([], threshold=0))

    def test_no_threshold_never_opens(self):
        self.assertFalse(needs_picker([listed_model("a", 50)], threshold=None))

    def test_a_custom_threshold_opens_only_above_it(self):
        self.assertFalse(needs_picker([listed_model("a", 3)], threshold=3))
        self.assertTrue(needs_picker([listed_model("a", 4)], threshold=3))


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


class NotedClips(unittest.TestCase):
    def setUp(self):
        self.selection = ClipSelection([
            noted_model("hero", ["Run00", "Run01", "Run02"], [None, "duplicate", None]),
            noted_model("npc", ["Wave00", "Wave01"], [None, "layer"]),
        ])

    def test_noted_clips_start_unticked_and_the_result_lists_their_model(self):
        self.assertEqual(self.selection.noted(), 2)
        self.assertEqual(self.selection.totals(), (3, 5))
        self.assertEqual(self.selection.result(), {"hero": ("Run00", "Run02"), "npc": ("Wave00",)})

    def test_hidden_noted_clips_leave_the_visible_list(self):
        self.assertEqual(self.selection.visible(0), [0, 1, 2])
        self.assertEqual(self.selection.visible(0, noted=False), [0, 2])
        self.assertEqual(self.selection.visible(1, "wave", noted=False), [0])

    def test_previous_ticks_restore_the_named_models_and_ignore_notes(self):
        selection = ClipSelection([
            noted_model("hero", ["Run00", "Run01", "Run02"], [None, "duplicate", None]),
            noted_model("npc", ["Wave00", "Wave01"], [None, "layer"]),
        ], previous={"hero": ("Run01",), "gone": ("Run00",)})
        self.assertEqual(selection.restored, 1)
        self.assertEqual(selection.result(), {"hero": ("Run01",), "npc": ("Wave00",)})

    def test_an_earlier_model_with_no_ticks_stays_unticked(self):
        selection = ClipSelection([noted_model("hero", ["Run00", "Run01"], [None, None])], previous={"hero": ()})
        self.assertEqual(selection.restored, 1)
        self.assertEqual(selection.result(), {"hero": ()})

    def test_ticked_labels_lists_every_model_and_round_trips_through_previous(self):
        self.selection.set_ticked(0, [0], False)
        self.selection.set_ticked(1, [1], True)
        ticked = self.selection.ticked_labels()
        self.assertEqual(ticked, {"hero": ("Run02",), "npc": ("Wave00", "Wave01")})
        restored = ClipSelection([noted_model("hero", ["Run00", "Run01", "Run02"], [None, "duplicate", None]),
                                  noted_model("npc", ["Wave00", "Wave01"], [None, "layer"])], previous=ticked)
        self.assertEqual(restored.restored, 2)
        self.assertEqual(restored.ticked_labels(), ticked)


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

    def test_a_noted_clip_is_hidden_until_show_noted_is_set(self):
        labels = [f"Run{index:02d}" for index in range(12)]
        notes = [None] * 11 + ["duplicate"]
        dialog = ClipPickerDialog(self.tk, self.ttk, self.root, [noted_model("hero", labels, notes)])
        self.addCleanup(lambda: dialog.top.winfo_exists() and dialog.top.destroy())
        dialog.rebuild()

        def rows_for_last_clip():
            return [entry for entry in dialog.targets.values() if entry[3] and entry[1] == [11]]

        self.assertFalse(dialog.show_noted.get())
        self.assertEqual(rows_for_last_clip(), [])
        dialog.show_noted.set(True)
        rows = rows_for_last_clip()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "Run11   (copy of another clip)")


class BlendClipMap(unittest.TestCase):
    """The Blender scene path: picked clips reach the pipeline as one --clip-map file, in the same run."""

    def runner(self, selectors, choices):
        return PipelineBatchRunner(selectors, Path("game"), Path("out"), Path("python.exe"), Path("blender.exe"),
                                   clip_choices=choices)

    def test_picked_models_get_a_clip_map_of_labels_and_unpicked_models_keep_every_clip(self):
        runner = self.runner(["a/hero.model", "b/npc.model"], {"a/hero.model": ("Run00", "Wave: Idle")})
        clip_map = runner._write_clip_map()
        try:
            self.assertEqual(json.loads(clip_map.read_text(encoding="utf-8")),
                             {"a/hero.model": ["label:Run00", "label:Wave: Idle"]})
            command = runner.command(clip_map)
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

    def test_no_choices_writes_no_file(self):
        self.assertIsNone(self.runner(["a/hero.model"], {})._write_clip_map())
        self.assertIsNone(self.runner(["b/npc.model"], {"a/hero.model": ("Run00",)})._write_clip_map())

    def test_a_run_without_a_clip_map_has_no_clip_flags(self):
        command = self.runner(["a/hero.model"], {}).command()
        self.assertNotIn("--clip-map", command)
        self.assertNotIn("--clip", command)
        self.assertNotIn("--cinematic-clips", command)
        self.assertIn("--model", command)
        with_map = self.runner(["a/hero.model"], {}).command(Path("map.json"))
        self.assertEqual(with_map[with_map.index("--clip-map") + 1], "map.json")
        self.assertNotIn("--clip", with_map)


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


if __name__ == "__main__":
    unittest.main()
