import unittest

from rc2016.app.browser import lod_choices_differ, material_toggles


def looks(*lod_meshes):
    """One look whose four LOD slots hold the given mesh lists."""
    return [dict(index=0, lods=[dict(index=i, start=m[0] if m else 0, count=len(m), meshes=list(m)) for i, m in enumerate(lod_meshes)])]


class BrowserControlsTest(unittest.TestCase):
    def test_identical_lods_hide_the_choice(self):
        self.assertFalse(lod_choices_differ(looks([0, 1, 2], [0, 1, 2], [0, 1, 2], [0, 1, 2]), 3))

    def test_differing_lods_show_the_choice(self):
        self.assertTrue(lod_choices_differ(looks([0, 1], [2], [], []), 3))

    def test_bad_look_index_is_not_a_choice(self):
        self.assertFalse(lod_choices_differ(looks([0]), 1, look=5))

    def test_material_toggles(self):
        self.assertEqual(material_toggles(None), (False, False))
        self.assertEqual(material_toggles({0: {}, 1: {}}), (False, False))
        self.assertEqual(material_toggles({0: {}, 1: {'native_translation': {'x': 1}}}), (True, False))
        self.assertEqual(material_toggles({0: {'experimental_preview': {'x': 1}}}), (False, True))



class StateFilterTests(unittest.TestCase):
    def test_every_state_a_backend_produces_is_offered(self):
        from pathlib import Path
        from rc2016.app.batch import PipelineModelCandidate
        from rc2016.app.window import STATE_FILTERS, AssetBrowserApp
        pipeline = {AssetBrowserApp.record_status(PipelineModelCandidate("id", "a/b.model", previous), Path("out"), exported)[0]
                    for previous in ("", "COMPLETE") for exported in (set(), {"b"})}
        self.assertLessEqual(pipeline, set(STATE_FILTERS["pipeline"]))
        self.assertEqual(set(STATE_FILTERS["pipeline"]) - {"All"}, pipeline)


if __name__ == '__main__':
    unittest.main()
