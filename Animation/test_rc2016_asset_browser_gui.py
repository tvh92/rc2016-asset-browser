"""Regression tests for archive discovery and the GUI's Python batch backend."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from Animation.rc2016_asset_browser_gui import (
    BatchRunner,
    build_command,
    discover_models,
    output_directory,
    scan_json,
    status_for,
    verify_output,
)


ROOT = Path(__file__).resolve().parent
SHEEP_ROOT = ROOT.parent / "_Archive/2026-10-08_legacy_fbx_models/Weapons/COPY/Sheepinator/Source Archives"


class DiscoveryTest(unittest.TestCase):
    def test_pairs_only_exact_same_directory_animsets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "actor").mkdir()
            (root / "actor/hero.model").write_bytes(b"model")
            (root / "actor/hero.animset").write_bytes(b"animset")
            (root / "actor/static.model").write_bytes(b"model")
            (root / "elsewhere/static.animset").parent.mkdir()
            (root / "elsewhere/static.animset").write_bytes(b"wrong pair")
            records = discover_models(root)
            self.assertEqual([record.relative_model for record in records], ["actor/hero.model", "actor/static.model"])
            self.assertEqual(records[0].relative_animset, "actor/hero.animset")
            self.assertIsNone(records[1].animset)
            self.assertEqual(status_for(records[1], root / "out")[0], "NO ANIMSET")

    def test_output_name_is_stable_and_collision_resistant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").mkdir()
            (root / "b").mkdir()
            for folder in ("a", "b"):
                (root / folder / "same.model").write_bytes(b"model")
                (root / folder / "same.animset").write_bytes(b"animset")
            records = discover_models(root)
            self.assertEqual(len({record.output_name for record in records}), 2)
            self.assertEqual(records[0].output_name, discover_models(root)[0].output_name)

    def test_headless_scan_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "thing.model").write_bytes(b"model")
            (root / "thing.animset").write_bytes(b"animset")
            result = subprocess.run(
                [sys.executable, str(ROOT / "rc2016_asset_browser_gui.py"), "--scan", str(root)],
                cwd=str(ROOT.parent), capture_output=True, text=True, check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model_count"], 1)
            self.assertEqual(payload["models"][0]["state"], "READY")


class VerificationTest(unittest.TestCase):
    def test_manifest_and_raw_clip_hash_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "thing.model"
            animset = root / "thing.animset"
            model.write_bytes(b"model bytes")
            animset.write_bytes(b"animset bytes")
            output = root / "out"
            output.mkdir()
            raw = output / "000_clip.animclip"
            raw.write_bytes(b"raw clip")
            (output / "000_clip.json").write_text('{"frames": []}\n', encoding="utf-8")
            manifest = {
                "schema": "ps4-v15-source-sqt-v1",
                "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                "animset_sha256": hashlib.sha256(animset.read_bytes()).hexdigest(),
                "skeleton": [],
                "clips": [{
                    "json": "000_clip.json",
                    "raw_clip": "000_clip.animclip",
                    "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                }],
            }
            (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(verify_output(output, model, animset), (True, "verified 1 clip(s)"))
            raw.write_bytes(b"changed")
            self.assertEqual(verify_output(output, model, animset)[0], False)


class BackendIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(SHEEP_ROOT.joinpath("wpn_sheepinator/wpn_sheepinator.model").is_file(), "source fixture is not present")
    def test_batch_invokes_transform_cli_and_resumes_verified_output(self):
        records = discover_models(SHEEP_ROOT)
        self.assertEqual(len(records), 1)
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            first = BatchRunner(records, output_root, Path(sys.executable)).run()
            self.assertEqual(first["completed"], 1)
            output = output_directory(records[0], output_root)
            self.assertTrue(verify_output(output, records[0].model, records[0].animset)[0])
            second = BatchRunner(records, output_root, Path(sys.executable)).run()
            self.assertEqual(second["verified"], 1)

    def test_command_uses_python_cli_and_repeatable_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "thing.model"
            animset = root / "thing.animset"
            model.write_bytes(b"model")
            animset.write_bytes(b"animset")
            record = discover_models(root)[0]
            command = build_command(Path("python.exe"), record, root / "out", ("Idle", "Fire"))
            self.assertIn(str(ROOT / "animation_transforms.py"), command)
            self.assertEqual(command[-4:], ["--prefix", "Idle", "--prefix", "Fire"])


if __name__ == "__main__":
    unittest.main()
