"""Unit tests for bounded worker and review-process lifecycles."""
import base64
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from rc2016.app import workers
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "lXcAAAAASUVORK5CYII="
)


class LatestWorkTests(unittest.TestCase):
    def test_coalesces_pending_and_suppresses_stale_result(self):
        work = workers.LatestWork()
        entered, release = threading.Event(), threading.Event()
        try:
            first = work.submit(lambda value: (entered.set(), release.wait(3), value)[-1], "old")
            self.assertTrue(entered.wait(2))
            middle = work.submit(lambda: "middle")
            latest = work.submit(lambda: "latest")
            release.set()
            self.assertEqual(work.events.get(timeout=3), (latest, "latest", None))
            self.assertGreater(middle, first)
            self.assertTrue(work.events.empty())
        finally:
            release.set()
            work.close()

    def test_close_suppresses_active_result_and_rejects_submissions(self):
        work = workers.LatestWork()
        entered, release = threading.Event(), threading.Event()
        work.submit(lambda: (entered.set(), release.wait(3), "done")[-1])
        self.assertTrue(entered.wait(2))
        work.close()
        self.assertIsNone(work.submit(lambda: "after close"))
        release.set()
        self.assertTrue(work.events.empty())

    def test_worker_exception_is_delivered_as_text(self):
        work = workers.LatestWork()
        try:
            generation = work.submit(lambda: (_ for _ in ()).throw(ValueError("bad metadata")))
            self.assertEqual(work.events.get(timeout=3), (generation, None, "bad metadata"))
        finally:
            work.close()


class ReviewProcessTests(unittest.TestCase):
    def make_run(self, directory, source):
        root = Path(directory)
        report = root / "review report.json"
        return workers.ReviewProcess([sys.executable, "-c", source, str(report)], report, root), report

    def test_success_and_live_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            process, _ = self.make_run(directory, "import json,sys; print('started', flush=True); open(sys.argv[1], 'w').write(json.dumps({'status':'passed','items':2}))")
            logs = []
            process.event = lambda kind, **data: logs.append((kind, data.get('message')))
            self.assertEqual(process.run(), {"status": "passed", "items": 2})
            self.assertTrue(any("started" in (line or "") for _kind, line in logs))
            self.assertTrue((Path(directory) / "process.log").is_file())
            self.assertIsNone(process.process)

    def test_nonzero_exit_without_report_has_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            process, _ = self.make_run(directory, "import sys; print('failed'); sys.exit(7)")
            with self.assertRaisesRegex(RuntimeError, "Review exited 7 without a result"):
                process.run()
            self.assertIsNone(process.process)
            self.assertIn("failed", (Path(directory) / "process.log").read_text())

    def test_failed_report_is_error_even_after_zero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            process, _ = self.make_run(directory, "import json,sys; open(sys.argv[1], 'w').write(json.dumps({'status':'failed','error':'bad scene'}))")
            with self.assertRaisesRegex(RuntimeError, "bad scene"):
                process.run()

    def test_cancel_before_start_and_double_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            process, _ = self.make_run(directory, "raise SystemExit('must not launch')")
            with mock.patch.object(subprocess, "Popen") as popen:
                process.cancel()
                self.assertEqual(process.run(), {"status": "cancelled"})
                popen.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "already submitted"):
                process.run()

    def test_real_active_process_cancel_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            process, _ = self.make_run(directory, "import sys,time; print('ready', flush=True); time.sleep(30); open(sys.argv[1], 'w').write('{}')")
            ready, result, errors = threading.Event(), [], []
            def on_event(kind, **data):
                if kind == "log" and data.get("message") == "ready":
                    ready.set()
            process.event = on_event
            def run():
                try:
                    result.append(process.run())
                except Exception as error:
                    errors.append(error)
            runner = threading.Thread(target=run)
            runner.start()
            self.assertTrue(ready.wait(5), "child did not announce readiness")
            process.cancel()
            runner.join(8)
            self.assertFalse(runner.is_alive(), "cancelled review did not finish")
            self.assertEqual(result, [{"status": "cancelled"}])
            self.assertEqual(errors, [])
            self.assertIsNone(process.process)

    def test_report_collision_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            process, report = self.make_run(directory, "raise SystemExit(0)")
            report.write_text("{}")
            with mock.patch.object(subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "already exists"):
                    process.run()
                popen.assert_not_called()

    def test_windows_paths_remain_separate_arguments(self):
        command = workers.review_command(r"C:\Program Files\Blender\blender.exe", r"C:\Model Folder\review adapter.py", r"D:\Run Folder\request.json")
        self.assertEqual(command, [r"C:\Program Files\Blender\blender.exe", "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1", "--python", r"C:\Model Folder\review adapter.py", "--", "--request", r"D:\Run Folder\request.json"])
        self.assertTrue(all(isinstance(arg, str) for arg in command))


class ThumbnailCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog_name = workers.__package__ + ".catalog"
        fake_catalog = types.SimpleNamespace(sha256_file=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest())
        patcher = mock.patch.dict(sys.modules, {self.catalog_name: fake_catalog})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_png(self, name="tiny.png", data=TINY_PNG):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_verified_png_and_bounded_lru(self):
        cache = workers.ThumbnailCache(capacity=1)
        digest = hashlib.sha256(TINY_PNG).hexdigest()
        first = self.write_png()
        encoded, scale = cache.load(first, digest)
        rendered=base64.b64decode(encoded)
        self.assertEqual(rendered[:8], TINY_PNG[:8])
        self.assertEqual(rendered[16:24], TINY_PNG[16:24])
        self.assertEqual(first.read_bytes(), TINY_PNG)  # PIL may re-encode; source stays exact.
        self.assertEqual(scale, 1)
        second = self.write_png("second.png")
        cache.load(second, digest)
        self.assertEqual(len(cache.cache), 1)
        self.assertEqual(next(iter(cache.cache))[0], str(second.resolve()))

    def test_missing_stale_invalid_and_oversized_rejected(self):
        cache = workers.ThumbnailCache()
        with self.assertRaisesRegex(ValueError, "missing"):
            cache.load(self.root / "missing.png", "anything")
        valid = self.write_png()
        with self.assertRaisesRegex(ValueError, "stale or unverified"):
            cache.load(valid, "0" * 64)
        invalid = self.write_png("invalid.png", b"not png" + b"0" * 40)
        with self.assertRaisesRegex(ValueError, "Invalid PNG"):
            cache.load(invalid, hashlib.sha256(invalid.read_bytes()).hexdigest())
        oversized = self.write_png("oversized.png")
        original_stat = Path.stat
        def fake_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == oversized:
                return types.SimpleNamespace(st_mode=result.st_mode, st_size=32 * 1024 * 1024 + 1)
            return result
        with mock.patch.object(Path, "stat", fake_stat):
            with self.assertRaisesRegex(ValueError, "too large"):
                cache.load(oversized, hashlib.sha256(TINY_PNG).hexdigest())


if __name__ == "__main__":
    unittest.main()

