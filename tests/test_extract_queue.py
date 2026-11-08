"""Extraction queue and deselect-after-extract rules of the Extract selected button.

The methods are exercised on a stub that carries only the state they read, so no Tk window is needed.
"""
import unittest
from types import SimpleNamespace

from rc2016.app.window import AssetBrowserApp


class _Table:
    def __init__(self, selected=()):
        self.selected = set(selected)

    def selection_remove(self, items):
        self.selected -= set(items)


def _record(model):
    return SimpleNamespace(model=model, model_id=None)


class _Stub(SimpleNamespace):
    """Carries the attributes the methods under test read or write; the rest is no-op."""

    def update_selection_label(self):
        self.labels = getattr(self, "labels", 0) + 1

    _record_key = staticmethod(AssetBrowserApp._record_key)
    _deselect_records = AssetBrowserApp._deselect_records
    _sync_status_text = AssetBrowserApp._sync_status_text


class DeselectAfterExtractTests(unittest.TestCase):
    def _stub(self):
        a, b, c = _record("a"), _record("b"), _record("c")
        return _Stub(
            item_by_record={"item-a": a, "item-b": b, "item-c": c},
            table=_Table({"item-a", "item-b", "item-c"}),
            selected_map={"a": a, "b": b, "c": c},
        ), (a, b, c)

    def test_only_the_extracted_models_are_deselected(self):
        stub, (a, b, c) = self._stub()
        stub._deselect_records([a, b])
        self.assertEqual(stub.table.selected, {"item-c"})
        self.assertEqual(set(stub.selected_map), {"c"})

    def test_hidden_selected_models_keep_their_selection(self):
        stub, (a, b, c) = self._stub()
        hidden = _record("hidden")
        stub.selected_map["hidden"] = hidden   # selected, but not shown by the list
        stub._deselect_records([a])
        self.assertIn("hidden", stub.selected_map)
        self.assertEqual(set(stub.selected_map), {"b", "c", "hidden"})

    def test_empty_batch_changes_nothing(self):
        stub, _ = self._stub()
        stub._deselect_records([])
        self.assertEqual(len(stub.selected_map), 3)
        self.assertEqual(len(stub.table.selected), 3)


class QueuedBatchTests(unittest.TestCase):
    def test_deselecting_a_queued_model_does_not_drop_it(self):
        a, b = _record("a"), _record("b")
        started = []
        stub = _Stub(
            pending_batches=[[a, b]],
            records=[a, b],
            selected_map={"a": a},   # b was deselected after it was queued
            runner=None,
            worker=None,
            browser=SimpleNamespace(review=False),
            root=SimpleNamespace(after=lambda *args: None),
            start_batch=lambda records=None: started.append(records),
            _append_log=lambda *args, **kwargs: None,
            _extracting=lambda: False,
        )
        stub._start_next_queued = AssetBrowserApp._start_next_queued.__get__(stub)
        stub._start_next_queued()
        self.assertEqual(started, [[a, b]])
        self.assertEqual(stub.pending_batches, [])

    def test_a_queued_model_no_longer_listed_is_skipped(self):
        a, gone = _record("a"), _record("gone")
        started = []
        stub = _Stub(
            pending_batches=[[gone]],
            records=[a],
            selected_map={},
            runner=None,
            worker=None,
            browser=SimpleNamespace(review=False),
            root=SimpleNamespace(after=lambda *args: None),
            start_batch=lambda records=None: started.append(records),
            _append_log=lambda *args, **kwargs: None,
            _extracting=lambda: False,
        )
        stub._start_next_queued = AssetBrowserApp._start_next_queued.__get__(stub)
        stub._start_next_queued()
        self.assertEqual(started, [])


class BatchFinishTests(unittest.TestCase):
    def test_only_models_that_extracted_are_deselected_even_when_the_batch_failed(self):
        from rc2016.app.batch import PipelineModelCandidate
        a = PipelineModelCandidate(model_id="1", archive_path="models/a.model")
        b = PipelineModelCandidate(model_id="2", archive_path="models/b.model")
        c = PipelineModelCandidate(model_id="3", archive_path="models/c.model")
        deselected = []
        stub = _Stub(
            batch_records=[a, b, c],
            batch_extracted={AssetBrowserApp._queue_key("models/a.model"), AssetBrowserApp._queue_key("models/b.model")},
            batch_clips={},
            retained_clips={},
            _record_path=staticmethod(AssetBrowserApp._record_path),
            _queue_key=staticmethod(AssetBrowserApp._queue_key),
            _deselect_records=lambda records: deselected.append(list(records)),
        )
        AssetBrowserApp._batch_finished(stub, False)
        self.assertEqual(deselected, [[a, b]])
        self.assertEqual(stub.batch_records, [])
        self.assertEqual(stub.batch_extracted, set())


class StatusTextTests(unittest.TestCase):
    def test_counts_line_has_no_space_before_the_comma(self):
        class Var:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        stub = _Stub(progress_var=Var("12 models found; 5 visible"), selection_var=Var("2 selected"), status_text=Var(""))
        stub._sync_status_text()
        self.assertEqual(stub.status_text.get(), "12 models found; 5 visible, 2 selected")


if __name__ == "__main__":
    unittest.main()
