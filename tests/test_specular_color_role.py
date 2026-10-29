import unittest

from rc2016.formats.materials import texture_role
from rc2016.viewer.textures import _role


class SpecularColourRoleTests(unittest.TestCase):
    def test_specular_tint_map_is_not_base_colour(self):
        path = 'textures/character/enemy/enm_mrs_zurkon/enm_mrs_zurkon_specularcolor_c.texture'
        self.assertIsNone(_role(path))
        self.assertEqual(texture_role(path), 'additional')

    def test_plain_base_colour_unchanged(self):
        path = 'textures/character/enemy/enm_mrs_zurkon/enm_mrs_zurkon_c.texture'
        self.assertEqual(_role(path), '_c')
        self.assertEqual(texture_role(path), 'base_color')


if __name__ == '__main__':
    unittest.main()
