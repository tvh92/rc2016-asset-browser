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


if __name__ == '__main__':
    unittest.main()
