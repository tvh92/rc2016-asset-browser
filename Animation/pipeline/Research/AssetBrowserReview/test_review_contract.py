"""Pure-Python contract tests; these do not launch Blender."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import sys
from types import SimpleNamespace
from unittest import mock

MODULE = Path(__file__).with_name("review_blender.py")
SPEC = importlib.util.spec_from_file_location("review_blender", MODULE)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


class TestScene(SimpleNamespace):
    def __contains__(self, key):
        return hasattr(self, key)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __delitem__(self, key):
        delattr(self, key)


class RequestContractTests(unittest.TestCase):
    def valid(self, **changes):
        request = {"schema": review.SCHEMA, "mode": "inspect",
                   "master_blend": "master.blend", "master_sha256": "a" * 64,
                   "report_path": str(MODULE.parent / "report.json")}
        request.update(changes)
        return request

    def test_inspect_request_requires_pinned_master_and_report(self):
        self.assertEqual(review.validate_request(self.valid())["mode"], "inspect")
        for key in ("master_blend", "master_sha256", "report_path"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                request = self.valid(); request.pop(key)
                review.validate_request(request)

    def test_rejects_bad_hash_and_mode(self):
        for request in (self.valid(master_sha256="xyz"), self.valid(mode="guess"),
                        self.valid(schema="other")):
            with self.subTest(request=request), self.assertRaises(ValueError):
                review.validate_request(request)

    def test_selectors_fail_closed_without_exact_source_row(self):
        available = [{"selector": "source-action"}]
        self.assertEqual(review.require_available(available, "source-action", None, "clip"), available[0])
        with self.assertRaisesRegex(ValueError, "source contract unavailable"):
            review.require_available([], "V5", "source contract unavailable", "variant")
        with self.assertRaisesRegex(ValueError, "Unsupported clip"):
            review.require_available(available, "source-action-copy", None, "clip")

    def test_proof_requires_destination_and_integer_frame(self):
        with self.assertRaisesRegex(ValueError, "output_dir"):
            review.validate_request(self.valid(mode="proof"))
        proof = self.valid(mode="proof", output_dir=str(MODULE.parent / "proofs"), frame=12)
        self.assertEqual(review.validate_request(proof)["frame"], 12)
        with self.assertRaisesRegex(ValueError, "combined variant and LOD"):
            review.validate_request(self.valid(mode="proof", output_dir=str(MODULE.parent / "proofs"),
                                               variant="V5", lod="0:0"))
        for bad in (True, 1.5, "12"):
            with self.subTest(frame=bad), self.assertRaisesRegex(ValueError, "integer"):
                review.validate_request(self.valid(mode="proof", output_dir=str(MODULE.parent / "proofs"), frame=bad))
        with self.assertRaisesRegex(ValueError, "stay inside AssetBrowserReview"):
            review.validate_request(self.valid(report_path=str(MODULE.parent.parent / "escape.json")))


    def test_proof_returns_report_and_restores_camera_view_state(self):
        scene = TestScene(
            objects=[], view_layers=[], camera=None, frame_current=4, frame_start=1, frame_end=9,
            render=SimpleNamespace(fps=30, fps_base=1.0, use_compositing=False, engine="CYCLES",
                                   resolution_x=3840, resolution_y=2560, resolution_percentage=100,
                                   filepath="master.png", image_settings=SimpleNamespace(
                                       file_format="PNG", color_mode="RGBA"), film_transparent=True),
            cycles=SimpleNamespace(samples=96, device="GPU"),
            view_settings=SimpleNamespace(view_transform="Khronos PBR Neutral", look="None",
                                          exposure=-1.0, gamma=1.0),
            world=SimpleNamespace(name="Fixture World", use_nodes=False, node_tree=None), get=lambda key: None, keys=lambda: (),
            frame_set=lambda frame: setattr(scene, "frame_current", frame))
        bpy_fake = SimpleNamespace(context=SimpleNamespace(scene=scene, view_layer=SimpleNamespace(update=lambda: None)),
                                  data=SimpleNamespace(collections=[], worlds=[], images=[], screens=[]))
        review.bpy = bpy_fake
        with mock.patch.object(review, "_proof_render", return_value={"proof": {}}):
            result = review._proof({"mode": "proof"}, "a" * 64)
        self.assertIn("scene_state", result["proof"])
        self.assertTrue(result["proof"]["scene_state"]["restored"])

    def test_run_installs_bpy_global_before_inspection_and_stringifies_errors(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parent) as temp:
            root = Path(temp)
            master = root / "fixture.blend"
            master.write_bytes(b"pinned fixture")
            report = root / "report.json"
            fake_bpy = SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(open_mainfile=lambda **kwargs: None)))
            request = {"schema": review.SCHEMA, "mode": "inspect", "master_blend": str(master),
                       "master_sha256": review.sha256_file(master), "report_path": str(report)}
            review.bpy = None
            def inspect(_digest):
                self.assertIs(review.bpy, fake_bpy)
                raise RuntimeError("mock inspection failure")
            with mock.patch.dict(sys.modules, {"bpy": fake_bpy}), mock.patch.object(review, "inspect_scene", inspect):
                status = review.run(request)
            data = __import__("json").loads(report.read_text(encoding="utf-8"))
            self.assertEqual(status, 1)
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["error"], "mock inspection failure")
            self.assertIsInstance(data["error"], str)


if __name__ == "__main__":
    unittest.main()



