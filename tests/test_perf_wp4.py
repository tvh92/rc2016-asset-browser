import json
from pathlib import Path
import tempfile
import types
import unittest

from rc2016.app import catalog, gallery, main as appmain, thumbnails as th
from rc2016.app.catalog import ResultRef

WINDOW = next(c for c in vars(appmain).values() if isinstance(c, type) and hasattr(c, "update_record_row"))


def old_row(ref, doc):
    """Reference copy of the original linear _row scan."""
    if not isinstance(doc, dict):
        return None
    if ref.report_kind == "manifest":
        row = (doc.get("models") or {}).get(ref.model_id)
        if isinstance(row, dict) and (row.get("source_model_path") or row.get("source_path") or row.get("model")) == ref.source_path:
            return row
        return None
    for case in doc.get("cases", []):
        if isinstance(case, dict):
            actual = case.get("actual") or {}
            if actual.get("asset_id") == ref.model_id and actual.get("source_model_path") == ref.source_path:
                return case
    return None


class ReportCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        catalog._REPORTS.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def ref(self, path, ident, source, kind):
        return ResultRef(path, ident, source, "x", self.root, kind)

    def test_index_matches_linear_scan(self):
        cases = [{"actual": {"asset_id": "a", "source_model_path": "m/a.model"}, "n": 1},
                 {"actual": {"asset_id": "a", "source_model_path": "m/a.model"}, "n": 2},   # duplicate: first wins
                 {"actual": {"asset_id": "b", "source_model_path": "m/b.model"}},
                 {"actual": None}, "junk", {"actual": {"asset_id": ["x"], "source_model_path": "q"}}]
        models = {"a": {"source_model_path": "m/a.model"}, "b": {"source_path": "m/b.model"},
                  "c": {"model": "m/c.model"}, "d": "junk", "e": {"model": "other"}}
        p = self.root / "results.json"
        p.write_text(json.dumps({"cases": cases, "models": models}))
        doc = json.loads(p.read_text())
        for kind in ("aggregate", "manifest"):
            for ident in ("a", "b", "c", "d", "e", "zz"):
                for src in ("m/a.model", "m/b.model", "m/c.model", "q", "nope"):
                    ref = self.ref(p, ident, src, kind)
                    self.assertEqual(catalog.report_row(ref), old_row(ref, doc), (kind, ident, src))

    def test_missing_and_non_dict(self):
        self.assertIsNone(catalog.report_row(self.ref(self.root / "none.json", "a", "s", "aggregate")))
        p = self.root / "list.json"
        p.write_text("[]")
        self.assertIsNone(catalog.report_row(self.ref(p, "a", "s", "manifest")))

    def test_parsed_once_and_invalidated(self):
        p = self.root / "results.json"
        p.write_text(json.dumps({"cases": []}))
        calls = []
        orig = catalog._json
        catalog._json = lambda path: (calls.append(path), orig(path))[1]
        try:
            for _ in range(5):
                catalog._report(p)
            self.assertEqual(len(calls), 1)
            p.write_text(json.dumps({"cases": [{"actual": {"asset_id": "a", "source_model_path": "s"}}]}))
            self.assertIsNotNone(catalog.report_row(self.ref(p, "a", "s", "aggregate")))
            self.assertEqual(len(calls), 2)
        finally:
            catalog._json = orig

    def test_cache_is_bounded(self):
        for i in range(catalog._REPORTS_MAX + 5):
            p = self.root / f"{i}.json"
            p.write_text("{}")
            catalog._report(p)
        self.assertLessEqual(len(catalog._REPORTS), catalog._REPORTS_MAX)


class RowUpdateTests(unittest.TestCase):
    def stub(self, state_filter="All", present=True, pending=None):
        t = types.SimpleNamespace(calls=[], coalesced=0)
        t._refresh_after = pending
        t.item_by_path = {"a.model": "model-0"} if present else {}
        t.item_by_record = {}
        t.state_var = types.SimpleNamespace(get=lambda: state_filter)
        t.shading_var = types.SimpleNamespace(get=lambda: "All")
        t.output_var = types.SimpleNamespace(get=lambda: "")
        t.table = types.SimpleNamespace(exists=lambda i: True, item=lambda i, **kw: t.calls.append((i, kw)))
        t._record_status = lambda r, o: ("PREVIOUS", "why")
        t._coalesce_refresh = lambda: setattr(t, "coalesced", t.coalesced + 1)
        return t

    def test_in_place(self):
        t = self.stub()
        r = appmain.PipelineModelCandidate.__new__(appmain.PipelineModelCandidate)
        object.__setattr__(r, "used_by", "u")
        object.__setattr__(r, "model_id", "id")
        WINDOW.update_record_row(t, "a.model", r)
        self.assertEqual(t.coalesced, 0)
        self.assertIs(t.item_by_record["model-0"], r)
        self.assertEqual(t.calls, [("model-0", {"values": ("u", "id", "PREVIOUS", "why")})])

    def test_fallbacks(self):
        for kw in ({"state_filter": "READY"}, {"present": False}):
            t = self.stub(**kw)
            WINDOW.update_record_row(t, "a.model", object())
            self.assertEqual((t.coalesced, t.calls), (1, []))
        t = self.stub(pending="x")
        WINDOW.update_record_row(t, "a.model", object())
        self.assertEqual((t.coalesced, t.calls), (0, []))


class ThumbnailIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "a.bin").write_bytes(b"x" * 100)
        self.archive = types.SimpleNamespace(root=self.root, layout_identity="L")
        self.entry = {"order": 3, "occurrences": [{"archive": "gone.bin", "available": False, "offset": 0, "size": 1},
                                                   {"archive": "a.bin", "available": True, "offset": 8, "size": 16}]}

    def test_identity_tracks_layout_range_and_file_size(self):
        base = th.entry_identity(self.archive, self.entry)
        self.assertEqual(base, th.entry_identity(self.archive, self.entry))
        self.assertNotEqual(base, th.entry_identity(types.SimpleNamespace(root=self.root, layout_identity="M"), self.entry))
        (self.root / "a.bin").write_bytes(b"x" * 101)
        self.assertNotEqual(base, th.entry_identity(self.archive, self.entry))

    def test_identity_unknown(self):
        self.assertIsNone(th.entry_identity(self.archive, None))
        self.assertIsNone(th.entry_identity(self.archive, {"loose_path": "p", "occurrences": []}))
        self.assertIsNone(th.entry_identity(self.archive, {"occurrences": [{"archive": "a.bin", "available": False}]}))
        self.assertIsNone(th.entry_identity(self.archive, {"occurrences": [{"archive": "nope.bin", "available": True, "offset": 0, "size": 1}]}))

    def test_is_unchanged(self):
        index = th.ThumbnailIndex(self.root)
        index.write_png("m", b"png")
        index.record("m", "ok", "hash", th.DEFAULT_SIZE, identity="I")
        self.assertTrue(index.is_unchanged("m", th.DEFAULT_SIZE, "I"))
        self.assertFalse(index.is_unchanged("m", th.DEFAULT_SIZE, "J"))
        self.assertFalse(index.is_unchanged("m", th.DEFAULT_SIZE, None))
        self.assertFalse(index.is_unchanged("m", 128, "I"))
        self.assertFalse(index.is_unchanged("other", th.DEFAULT_SIZE, "I"))
        index.record("legacy", "unsupported", "hash", th.DEFAULT_SIZE)          # older entry: no identity
        self.assertFalse(index.is_unchanged("legacy", th.DEFAULT_SIZE, "I"))
        index.save()
        again = th.ThumbnailIndex.load(self.root)
        self.assertTrue(again.is_unchanged("m", th.DEFAULT_SIZE, "I"))
        (self.root / "m.png").unlink()
        self.assertFalse(again.is_unchanged("m", th.DEFAULT_SIZE, "I"))        # success needs its PNG


class GalleryDecodeTests(unittest.TestCase):
    def test_decode_thumbnail_rgba(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow not installed")
        rgba = bytes([10, 20, 30, 0, 200, 100, 50, 255]) * 2
        data = th.encode_png_rgba(2, 2, rgba)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "t.png"
            path.write_bytes(data)
            image = gallery.decode_thumbnail(path)
        self.assertEqual((image.mode, image.size), ("RGBA", (2, 2)))
        self.assertEqual(image.tobytes(), rgba)


if __name__ == "__main__":
    unittest.main()
