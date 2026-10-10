"""Fast deterministic tests for GUI batch isolation and verified resume."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from rc2016.app.main import BatchRunner, ModelCandidate, output_directory, verify_output


def candidate(root: Path, name: str) -> ModelCandidate:
    model = root / f"{name}.model"
    animset = root / f"{name}.animset"
    model.write_bytes((name + " model").encode())
    animset.write_bytes((name + " animset").encode())
    return ModelCandidate(model, animset, model.name, animset.name, name)


def child(root: Path, fail: bool = False) -> Path:
    script = root / ("fail.py" if fail else "success.py")
    script.write_text('''import hashlib, json, pathlib, sys\nout=pathlib.Path(sys.argv[1]); model=pathlib.Path(sys.argv[2]); anim=pathlib.Path(sys.argv[3])\nif sys.argv[4] == "fail": raise SystemExit(7)\nout.mkdir(parents=True)\nraw=b"clip"; (out/"clip.animclip").write_bytes(raw); (out/"clip.json").write_text("{}")\n(out/"manifest.json").write_text(json.dumps({"schema":"ps4-v15-source-sqt-v1","model_sha256":hashlib.sha256(model.read_bytes()).hexdigest(),"animset_sha256":hashlib.sha256(anim.read_bytes()).hexdigest(),"skeleton":[],"clips":[{"json":"clip.json","raw_clip":"clip.animclip","raw_sha256":hashlib.sha256(raw).hexdigest()}]}))\n''', encoding="utf-8")
    return script


class GuiBatchIsolationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original_cli = None

    def tearDown(self):
        self.temp.cleanup()

    def run_batch(self, records, out, behavior):
        import rc2016.app.batch as gui
        original = gui.build_command
        gui.build_command = lambda _py, candidate, staging, _prefixes: [
            sys.executable, str(behavior[candidate.display_name]), str(staging),
            str(candidate.model), str(candidate.animset),
            "fail" if candidate.display_name == "middle" else "success"]
        try:
            return BatchRunner(records, out, Path(sys.executable)).run()
        finally:
            gui.build_command = original

    def test_success_failure_success_continues(self):
        records = [candidate(self.root, n) for n in ("first", "middle", "last")]
        scripts = {n: child(self.root, n == "middle") for n in ("first", "middle", "last")}
        # Only the named middle candidate fails; each candidate otherwise gets a real child process.
        import rc2016.app.batch as gui
        original = gui.build_command
        gui.build_command = lambda _py, c, stage, _p: [sys.executable, str(scripts[c.display_name]), str(stage), str(c.model), str(c.animset), "fail" if c.display_name == "middle" else "success"]
        try:
            result = BatchRunner(records, self.root / "out", Path(sys.executable)).run()
        finally:
            gui.build_command = original
        self.assertEqual((result["completed"], result["failed"], result["cancelled"]), (2, 1, False))
        self.assertTrue(verify_output(output_directory(records[0], self.root / "out"), records[0].model, records[0].animset)[0])
        self.assertTrue(verify_output(output_directory(records[2], self.root / "out"), records[2].model, records[2].animset)[0])

    def test_cancel_at_completion_boundary_prevents_next_launch(self):
        records = [candidate(self.root, n) for n in ("first", "middle", "last")]
        script = child(self.root)
        import rc2016.app.batch as gui
        original = gui.build_command
        runner = None
        def command(_py, c, stage, _p):
            if c.display_name == "first":
                # Cancellation from completion event exercises the boundary between models.
                pass
            return [sys.executable, str(script), str(stage), str(c.model), str(c.animset), "success"]
        def event(kind, **data):
            if kind == "model_done" and data["candidate"].display_name == "first":
                runner.cancel()
        runner = BatchRunner(records, self.root / "out", Path(sys.executable), event=event)
        gui.build_command = command
        try:
            result = runner.run()
        finally:
            gui.build_command = original
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["completed"], 1)
        self.assertFalse(output_directory(records[1], self.root / "out").exists())

    def test_resume_rejects_mutated_manifest_hash_and_raw(self):
        record = candidate(self.root, "model")
        output = self.root / "output"
        output.mkdir()
        raw = output / "clip.animclip"
        raw.write_bytes(b"clip")
        (output / "clip.json").write_text("{}", encoding="utf-8")
        manifest = {"schema":"ps4-v15-source-sqt-v1","model_sha256":hashlib.sha256(record.model.read_bytes()).hexdigest(),"animset_sha256":hashlib.sha256(record.animset.read_bytes()).hexdigest(),"skeleton":[],"clips":[{"json":"clip.json","raw_clip":"clip.animclip","raw_sha256":hashlib.sha256(raw.read_bytes()).hexdigest()}]}
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertTrue(verify_output(output, record.model, record.animset)[0])
        manifest["animset_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertFalse(verify_output(output, record.model, record.animset)[0])
        manifest["animset_sha256"] = hashlib.sha256(record.animset.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertTrue(verify_output(output, record.model, record.animset)[0])
        raw.write_bytes(b"changed")
        self.assertFalse(verify_output(output, record.model, record.animset)[0])


if __name__ == "__main__":
    unittest.main()
