"""Result browsing must not bless mutated deliveries or stale evidence."""
import tempfile
import unittest
from pathlib import Path
from rc2016.app import catalog
from rc2016.app.browser import normalize_details
from tests import test_catalog
class ResultIntegrityTests(unittest.TestCase):
    def test_mutated_master_and_receipt_disable_review_without_rewriting_reports(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            test_catalog.CatalogTests().make_output(root)
            ref=catalog.discover_results([root])[0]
            original_report=ref.report_path.read_bytes()
            details=catalog.inspect_result(ref)
            self.assertTrue(normalize_details(details)['integrity_verified'])
            details.blend.path.write_bytes(b'mutated delivery')
            stale=catalog.inspect_result(ref)
            self.assertFalse(normalize_details(stale)['integrity_verified'])
            self.assertEqual(stale.blend.hash_status,'mismatch')
            self.assertEqual(catalog.verify_preview(stale.previews[0]).status,'stale')
            self.assertEqual(ref.report_path.read_bytes(),original_report)
            # A separate fresh result verifies validation-receipt mutation too.
            fresh=root/'fresh';test_catalog.CatalogTests().make_output(fresh)
            ref=catalog.discover_results([fresh])[0]
            details=catalog.inspect_result(ref)
            details.validation.path.write_bytes(b'{"status":"passed","mutated":true}')
            stale=catalog.inspect_result(ref)
            self.assertEqual(stale.validation.hash_status,'mismatch')
            self.assertFalse(normalize_details(stale)['integrity_verified'])
            self.assertEqual(catalog.verify_preview(stale.previews[0]).status,'stale')
    def test_result_without_previews_verifies(self):
        # Runs no longer render review previews; their manifest rows carry no previews key.
        import json
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            test_catalog.CatalogTests().make_output(root)
            manifest=root/'reports'/'manifest.json'
            data=json.loads(manifest.read_text(encoding='utf-8'))
            for row in data['models'].values():row.pop('previews',None)
            manifest.write_text(json.dumps(data),encoding='utf-8')
            ref=catalog.discover_results([root])[0]
            details=catalog.inspect_result(ref)
            self.assertEqual(details.previews,())
            self.assertTrue(normalize_details(details)['integrity_verified'])

if __name__=='__main__':unittest.main()
