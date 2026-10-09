import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from rc2016.app import catalog


def put_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class CatalogTests(unittest.TestCase):
    def make_output(self, root: Path) -> tuple[Path, bytes]:
        model = root / "asset_abc123"
        model.mkdir(parents=True)
        blend = model / "asset.blend"
        blend.write_bytes(b"blend bytes")
        validation = model / "validation" / "validation.json"
        put_json(validation, {
            "status": "passed",
            "source_geometry_available": True,
            "source_action_count": 1,
            "clips": [{"name": "walk", "selection_passed": True}],
            "visual_review_performed": False,
        })
        put_json(model / "reports" / "clips.json", [{
            "action": "00000 | walk", "name": "walk", "status": "included",
            "raw_clip": "walk.animclip", "animset_path": "actors/a.animset",
            "source_duration_seconds": 1.25, "fps": 30, "frames": 38,
            "raw_sha256": "a" * 64,
        }, {
            "name": "future clip", "status": "unclassified",
        }])
        put_json(model / "reports" / "limitations.json", {
            "unsupported_clips": [{"name": "bad clip", "reason": "decoder rejected payload"}],
            "features_not_decoded": ["runtime blend weights"],
        })
        put_json(model / "meshes" / "manifest.json", {
            "import_look": 0, "import_lod": "Highest",
            "meshes": [{"memberships": [{"lod": 0}, {"lod": 1}]}],
        })
        png = b"\x89PNG\r\n\x1a\n" + b"small-test-png-data"
        preview = model / "review_images" / "preview.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(png)
        row = {
            "model": "character/a/asset.model", "status": "COMPLETE_MESH_ANIMATIONS_MATERIAL_LIMITATION",
            "joint_count": 4, "final": str(blend), "final_sha256": hashlib.sha256(blend.read_bytes()).hexdigest(),
            "validation": str(validation), "validation_sha256": hashlib.sha256(validation.read_bytes()).hexdigest(),
            "limitations": str(model / "reports" / "limitations.json"),
            "previews": [{"path": str(preview), "sha256": hashlib.sha256(png).hexdigest(), "purpose": "review"}],
        }
        put_json(root / "reports" / "manifest.json", {"models": {"abc123": row}})
        return preview, png


    def test_manifest_discovery_details_and_source_timing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_output(root)
            # Arbitrary nested JSON is intentionally not discovered.
            put_json(root / "archive" / "fake" / "reports" / "manifest.json", {"models": {"wrong": {}}})
            refs = catalog.discover_results([root])
            self.assertEqual(1, len(refs))
            ref = refs[0]
            self.assertEqual(("abc123", "character/a/asset.model"), (ref.model_id, ref.source_path))
            details = catalog.inspect_result(ref, verify_blend=False)
            self.assertEqual("skinned", details.kind)
            self.assertEqual("walk", details.supported_clips[0].name)
            self.assertEqual(1.25, details.supported_clips[0].source_duration_seconds)
            self.assertEqual(30, details.supported_clips[0].fps)
            self.assertEqual("00000 | walk", details.supported_clips[0].action)
            self.assertEqual("unknown", details.unknown_clips[0].classification)
            self.assertEqual("unsupported", details.unsupported_clips[0].classification)
            self.assertEqual(("runtime blend weights",), details.limitations)
            self.assertEqual((), details.source_variants)
            self.assertEqual("Highest", details.import_lod)
            self.assertEqual("unavailable", details.blend.hash_status)
            verified = catalog.inspect_result(ref)
            self.assertEqual("match", verified.blend.hash_status)
            self.assertEqual("hash_verified", catalog.verify_preview(verified.previews[0]).status)


    def test_preview_hash_and_png_states(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            preview, png = self.make_output(root)
            digest = hashlib.sha256(png).hexdigest()
            ref = catalog.PreviewRef(preview, digest)
            self.assertEqual("hash_verified", catalog.verify_preview(ref).status)
            self.assertEqual("stale", catalog.verify_preview(
                catalog.PreviewRef(preview, digest, receipt_hashes_match=False)).status)
            preview.write_bytes(b"not png")
            self.assertEqual("invalid", catalog.verify_preview(ref).status)
            preview.write_bytes(b"\x89PNG\r\n\x1a\n" + b"changed")
            self.assertEqual("stale", catalog.verify_preview(ref).status)
            preview.unlink()

            self.assertEqual("missing", catalog.verify_preview(ref).status)



    def test_identity_mismatch_and_unsafe_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_output(root)
            ref = catalog.discover_results([root])[0]
            wrong = catalog.ResultRef(ref.report_path, ref.model_id, "different/path.model",
                                      ref.label, ref.root_path, ref.report_kind)
            self.assertEqual("unknown", catalog.inspect_result(wrong).kind)
            report = json.loads(ref.report_path.read_text(encoding="utf-8"))
            report["models"][ref.model_id]["final"] = str(root.parent / "outside.blend")
            put_json(ref.report_path, report)
            details = catalog.inspect_result(ref)
            self.assertIsNone(details.blend.path)
            self.assertEqual("missing", details.blend.hash_status)


    def make_assembly(self, root: Path, status: str = "passed", override: str | None = None) -> Path:
        """Mimic runs/<run>/variant_assemblies/<name>/assembly_receipt.json with a sealed combined master."""
        run = root / "runs" / "r1"
        blend = run / "final" / "Fusion_Grenade_V1_V5_switchable.blend"
        blend.parent.mkdir(parents=True, exist_ok=True)
        blend.write_bytes(b"combined blend bytes")
        validation = run / "variant_assemblies" / "fusion_grenade" / "validation.json"
        put_json(validation, {"status": "passed", "source_animation_status": "No source animset is referenced by either selected wearable model; no applicable clips were included.",
                              "limitations": ["Actor selection is unproven."]})
        put_json(run / "assembly_audit.json", {"manual_visual_review_performed": False, "limitations": ["Source-local assembly."]})
        v1 = "character/hero/hero_ratchet/bangle_set/hero_ratchet_bangle_glove_rt_fusion_grenade/hero_ratchet_bangle_glove_rt_fusion_grenade.model"
        receipt = run / "variant_assemblies" / "fusion_grenade" / "assembly_receipt.json"
        put_json(receipt, {
            "schema": catalog.ASSEMBLY_SCHEMA, "status": status,
            "blend_path": str(blend), "blend_sha256": hashlib.sha256(blend.read_bytes()).hexdigest(),
            "validation_path": str(validation), "validation_sha256": hashlib.sha256(validation.read_bytes()).hexdigest(),
            "inputs": {
                "config": {"field_values": {"GloveModelOverride": {"value": override or v1.replace("hero_ratchet", "hero_Ratchet")}}},
                "models": [{"asset_id": "09e11ce58f6ade64fd6e", "archive_path": v1},
                           {"asset_id": "e39cd2d0cd8d3632c0fa", "archive_path": v1.replace("rt_fusion_grenade/", "rt_fusion_grenade_v5/").replace("grenade.model", "grenade_v5.model")}],
            },
        })
        return receipt

    def test_assembly_receipt_lists_v1_identity_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            receipt = self.make_assembly(root)
            refs = catalog.discover_results([root])
            self.assertEqual(1, len(refs))
            ref = refs[0]
            self.assertEqual("assembly", ref.report_kind)
            self.assertEqual("09e11ce58f6ade64fd6e", ref.model_id)
            self.assertTrue(ref.label.endswith(" (V1/V5 assembly)"))
            details = catalog.inspect_result(ref)
            self.assertEqual("match", details.blend.hash_status)
            self.assertEqual("match", details.validation.hash_status)
            self.assertEqual((), details.source_variants)  # selectors come from the Blender inspection only
            self.assertEqual("none", details.animation_status)
            self.assertIn("Actor selection is unproven.", details.limitations)
            # The master is confined to the run and its hash is enforced.
            (Path(details.blend.path)).write_bytes(b"changed")
            self.assertEqual("mismatch", catalog.inspect_result(ref).blend.hash_status)
            # A failed receipt, or a V1 override that matches no input model, is not listed.
            receipt.write_text(json.dumps({**json.loads(receipt.read_text(encoding="utf-8")), "status": "failed"}), encoding="utf-8")
            self.assertEqual([], catalog.discover_results([root]))
            self.make_assembly(root, override="character/hero/nope/nope.model")
            self.assertEqual([], [r for r in catalog.discover_results([root]) if r.report_kind == "assembly"])


if __name__ == "__main__":
    unittest.main()







