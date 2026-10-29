"""A material texture without a _c/_g/_n suffix binds to the graph slot whose default has that role.

Synthetic data only: no archive, no GPU, no Blender.
"""
import unittest

from rc2016.viewer import textures as tp
from tests.test_viewer_textures import FakeArchive, dat1, flags, slot_rows

GRAPH = 'required/materials/basic_normal_gloss.materialgraph'
DEFAULTS = ['textures/default_pink_shader/default_pink_shader_c.texture',
            'textures/default_pink_shader/default_pink_shader_g.texture',
            'textures/default_pink_shader/default_pink_shader_n.texture']
SLOTS = (0x49A7866B0056CBD0, 0x49A7866B7A530738, 0x49A7866B4AC18E9F)


def paths_for(material_textures):
    graph = dat1(DEFAULTS, lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows(list(zip(at, SLOTS))))])
    mat = dat1([GRAPH] + [path for path, _slot in material_textures], lambda at: [
        (tp.MATERIAL_SLOT_SECTION, slot_rows([(at[i + 1], slot) for i, (_path, slot) in enumerate(material_textures)])),
        (tp.MATERIAL_FLAGS_SECTION, flags(6))])
    archive = FakeArchive({'m/a.material': ('material', mat), GRAPH: ('materialgraph', graph)})
    return tp.material_texture_paths(archive, 'm/a.material')


class UnsuffixedTextureSlotTests(unittest.TestCase):
    def test_texture_named_without_role_suffix_replaces_the_default_in_its_slot(self):
        paths = paths_for([('rc1/textures/tie1530_tex1.texture', SLOTS[0])])
        self.assertEqual(paths['_c'], 'rc1/textures/tie1530_tex1.texture')
        self.assertEqual(paths['_g'], DEFAULTS[1])
        self.assertEqual(paths['_n'], DEFAULTS[2])

    def test_a_placeholder_never_replaces_a_role(self):
        paths = paths_for([(DEFAULTS[0], SLOTS[1])])
        self.assertEqual(paths['_g'], DEFAULTS[1])


if __name__ == '__main__':
    unittest.main()
