"""Controls for the GUI's full-pipeline Python backend seam."""
from pathlib import Path
import os
import subprocess
import sys
import threading
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from rc2016.app.main import PipelineBatchRunner, PipelineIndexScanner, build_command


class PipelineGuiBackendTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "process-tree cancellation is Windows-specific")
    def test_pipeline_cancel_cleans_up_cli_and_blender_child(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            py, blender = Path(sys.executable), root / "blender.exe"
            blender.touch()
            child_pid_path = root / "child.pid"
            fake_cli = root / "fake_pipeline.py"
            child_code = (f"import os,time; open({str(child_pid_path)!r}, 'w').write(str(os.getpid())); "
                          "time.sleep(30)")
            fake_cli.write_text(
                "import subprocess, sys, time\n"
                f"child_code = {child_code!r}\n"
                "subprocess.Popen([sys.executable, '-c', child_code])\n"
                "print('fake cli waiting', flush=True)\n"
                "time.sleep(30)\n", encoding="utf-8")
            runner = PipelineBatchRunner(["fixture/model"], root / "source", root / "out",
                                         py, blender)
            errors = []

            def run_batch():
                try:
                    runner.run()
                except BaseException as exc:  # surfaced in the test thread below
                    errors.append(exc)

            with patch("rc2016.app.batch.PIPELINE_CLI", fake_cli):
                thread = threading.Thread(target=run_batch, daemon=True)
                thread.start()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not child_pid_path.exists():
                    time.sleep(0.05)
                self.assertTrue(child_pid_path.exists(), "fake CLI did not start its child")
                thread.join(timeout=0.2)
                self.assertTrue(thread.is_alive(), "control requires cancellation while work is active")
                runner_pid = runner.process.pid if runner.process else None
                runner.cancel()
                thread.join(timeout=5)

            # Ensure a failing implementation cannot leave an orphan behind.
            if thread.is_alive() and runner_pid:
                subprocess.run(["taskkill", "/PID", str(runner_pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                thread.join(timeout=5)
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            task_list = subprocess.run(["tasklist", "/FI", f"PID eq {child_pid}"],
                                       capture_output=True, text=True, check=False).stdout
            self.assertNotIn(str(child_pid), task_list, "Blender-like child survived GUI cancellation")
            process_state = (runner.process.poll() if runner.process else None)
            self.assertFalse(thread.is_alive(),
                f"GUI runner remained blocked; parent pid={runner_pid}, poll={process_state}")
            self.assertFalse(errors, f"GUI runner raised unexpectedly: {errors!r}")

    def test_pipeline_runner_builds_one_python_batch_with_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            py, blender = root / "python.exe", root / "blender.exe"
            py.touch(); blender.touch()
            runner = PipelineBatchRunner(["actors/hero.model", "villain"], root / "source", root / "out",
                                         py, blender, skip_verified=True)
            command = runner.command()
            self.assertEqual(command[:2], [str(py), "-u"])
            self.assertEqual([command[i + 1] for i, value in enumerate(command[:-1]) if value == "--model"],
                             ["actors/hero.model", "villain"])
            self.assertIn("--reuse-cache", command)
            self.assertNotIn("powershell", " ".join(command).casefold())
            force_runner = PipelineBatchRunner(["hero"], root / "source", root / "out", py, blender,
                                               skip_verified=False)
            self.assertIn("--force", force_runner.command())

    def test_transform_backend_command_remains_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model, animset = root / "one.model", root / "one.animset"
            model.touch(); animset.touch()
            from rc2016.app.main import discover_models
            record = discover_models(root)[0]
            command = build_command(Path(sys.executable), record, root / "staging", ("Idle",))
            self.assertIn("animation_transforms.py", " ".join(command))
            self.assertEqual(command[-2:], ["--prefix", "Idle"])

    def test_pipeline_index_scan_uses_run_cli_and_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output, py, blender = (root / name for name in ("source", "out", "python.exe", "blender.exe"))
            source.mkdir(); py.touch(); blender.touch()
            reports = output / "reports"
            reports.mkdir(parents=True)
            (reports / "manifest.json").write_text('{"models":{"id1":{"status":"COMPLETE"}}}', encoding="utf-8")

            def run(command, **_kwargs):
                index_path = Path(command[command.index("--list-models-json") + 1])
                index_path.parent.mkdir(parents=True, exist_ok=True)
                index_path.write_text('{"models":[{"id":"id1","path":"equipment/hero.model","available":true},{"id":"id2","path":"weapons/thing.model","available":true}]}', encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("rc2016.app.batch.subprocess.run", side_effect=run) as called:
                records = PipelineIndexScanner(source, output, py, blender).scan()
            self.assertEqual([r.archive_path for r in records], ["equipment/hero.model", "weapons/thing.model"])
            self.assertEqual(records[0].previous_status, "COMPLETE")
            self.assertEqual(records[1].previous_status, "")
            self.assertIn("--list-models-json", called.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
