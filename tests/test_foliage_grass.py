"""Short-grass patches: basic_foliage keeps the vertex normal on back faces, and the grass materials cast no sun shadow.

The shader and the material list are checked synthetically; the archive test (RC2016_RESEARCH) checks that the grass patch's
vertex normals point straight up while its triangles face down, which is why a back-face normal flip turned the cards black.
"""
import os
import re
import unittest

from rc2016.viewer import shading, textures


class FoliageFaceTests(unittest.TestCase):
    def test_back_faces_flip_the_normal_except_for_basic_foliage(self):
        main = shading.FRAGMENT[shading.FRAGMENT.index('void main() {'):]
        flips = re.findall(r'if \(!gl_FrontFacing[^)]*\) N = -N;', main)
        self.assertEqual(flips[0], 'if (!gl_FrontFacing && !uFoliage) N = -N;')
        self.assertNotIn('if (!gl_FrontFacing) N = -N;', main[:main.index('vec3 V = normalize(-vPos);')])

    def test_grass_materials_are_not_sun_casters(self):
        for path in ('material/environment/pokitaru/plant/pok_plant_Grass_Short_01/rp_nov_plant_Grass_Short_01.material',
                     'material/environment/pokitaru/plant/pok_plant_Grass_Short_01/rp_pok_plant_Grass_Short_01.material'):
            self.assertIn(path.lower(), textures.NO_SHADOW_CAST_MATERIALS)
        self.assertNotIn('material/environment/novalis/shrub/nov_shrub_tree02_leaves_01/rp_nov_shrub_bush_leaves_01.material',
                         textures.NO_SHADOW_CAST_MATERIALS)

    def test_level_viewer_skips_them_only_in_the_shadow_pass(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rc2016', 'levels', 'viewport.py')
        source = open(path, encoding='utf-8').read()
        self.assertIn('if shadow and entry.get("no_shadow_cast"):', source)
        self.assertNotIn('if geometry and entry.get("no_shadow', source)


class GrassPatchArchiveTests(unittest.TestCase):
    MODEL = 'environment/novalis/shrub/nov_shrub_grass_short_patch_01/rp_nov_shrub_grass_short_patch_01.model'

    @classmethod
    def setUpClass(cls):
        root = os.environ.get('RC2016_RESEARCH')
        if not root:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        from rc2016.formats import native_model
        from rc2016.levels.scene import asset_bytes, open_archive
        cls.decoded = native_model.decode_model(cls.MODEL, 'All', data=asset_bytes(open_archive(root), cls.MODEL), as_arrays=True)

    def test_vertex_normals_point_up_and_faces_point_down(self):
        import numpy as np
        for mesh in self.decoded['meshes']:
            n = np.asarray(mesh['normals']).reshape(-1, 3)
            self.assertGreater(n[:, 1].min(), 0.999)
            p = np.asarray(mesh['positions']).reshape(-1, 3)
            t = np.asarray(mesh['triangles']).reshape(-1, 3)
            face = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
            self.assertGreater(float((face[:, 1] < 0).mean()), 0.9)
            self.assertIn('grass_short_01', str((mesh.get('material') or {}).get('path')).lower())


if __name__ == '__main__':
    unittest.main()
