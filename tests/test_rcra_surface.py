"""Rift Apart surface terms for Blender: unpacking checked against the writer's own G-buffer bits, the texture writer,
the export wiring, the blend and lit decode, forward recipes of blended materials, and (with Blender) the emitted node
trees baked against the recipes.

Real-install tests read RCRA_GAME; the Blender test also needs Blender (RC2016_BLENDER, else the default install path).
"""
import math
import os
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import numpy as np

from rc2016.games.rcra import blender_check, blender_export
from rc2016.games.rcra import surface as rsurface
from rc2016.shaders import sm5

GAME = os.environ.get('RCRA_GAME')
BLENDER = os.environ.get('RC2016_BLENDER') or r'C:\Program Files\Blender Foundation\Blender 5.0\blender.exe'
GRAPHS = ('required/materials/basic_normal_gloss.materialgraph',
          'materialgraph/global/gbl_rgb_blend/gbl_rgb_blend.materialgraph',
          'materialgraph/characters/hero/hero_armor_palette/hero_armor_palette.materialgraph',
          'required/materials/basic_normal_gloss_emissive.materialgraph')
CHECKED = 'material/environment/megalopolis/prop/meg_prop_plastic_trims/meg_prop_plastic_ship_002.material'
DOME = 'characters/enemy/enm_neo_nefarious_trooper/materials/enm_neo_nefarious_trooper_dome.material'
# (material, blend, lit) as the flags word decodes them.
BLENDS = ((DOME, 'blended', True), ('material/characters/npc/wine_glass/wine_glass.material', 'additive', True),
          ('material/visualeffect/global/gbl_glow_sphere_mask_01_w_depthsoftness/gbl_glow_sphere_mask_01_modulate.material',
           'modulate', False),
          ('material/visualeffect/global/exp_quad/exp_quad_thk_02_unlit.material', 'blended', False), (CHECKED, 'opaque', True))


class TexelTests(unittest.TestCase):
    def test_missing_channels_follow_d3d(self):
        two = dict(format=49, width=2, height=1, pixels=bytes([10, 20, 30, 40]))
        data = blender_export.texel_array(two)
        self.assertEqual(data.shape, (1, 2, 4))
        np.testing.assert_allclose(data[0, 0], [10 / 255, 20 / 255, 0, 1])
        floats = dict(format=16, width=1, height=1, pixels=np.array([0.25, -0.5], '<f4').tobytes())
        np.testing.assert_allclose(blender_export.texel_array(floats)[0, 0], [0.25, -0.5, 0, 1])

    def test_slices(self):
        nodes = [dict(op='const', type='f', value=1.0), dict(op='input', type='f', name='TEXCOORD.x'),
                 dict(op='sample', binding='textureUnit5', args=[1, 1, 0]), dict(op='sample', binding='textureUnit6', args=[1, 1, 1])]
        self.assertEqual(blender_export._slices(dict(nodes=nodes)), {'textureUnit5': 1, 'textureUnit6': blender_export.ALL_SLICES})

    def test_bilinear_matches_texel_centres(self):
        image = np.arange(16, dtype=np.float32).reshape(2, 2, 4)
        np.testing.assert_allclose(blender_check.bilinear(image, 0.25, 0.25), image[0, 0])
        np.testing.assert_allclose(blender_check.bilinear(image, 0.5, 0.25), (image[0, 0] + image[0, 1]) / 2)

    def test_blend_and_lit_bits(self):
        from unittest import mock
        from rc2016.games.rcra import material as rmat
        for flags, blend, lit in ((0x1810, 'opaque', 0x400), (0x1110, 'blended', 0), (0x887, 'additive', 0x400),
                                  (0x1211, 'modulate', 0), (0x918, 'opaque', 0x400), (None, 'opaque', 0)):
            with mock.patch.object(rmat, 'flags_word', return_value=flags):
                self.assertEqual(rmat.material_blend(b''), blend, hex(flags or 0))
                self.assertEqual(rmat.draw_flags(b''), lit, hex(flags or 0))
        with mock.patch.object(rmat, 'flags_word', return_value=0x180):
            with self.assertRaises(ValueError):
                rmat.material_blend(b'')

    def test_export_wiring(self):
        from rc2016.export.models import FORMATS
        from rc2016.games import rcra_game
        self.assertTrue(FORMATS['blend'][2])
        self.assertIn('blender_materials', rcra_game.GAME.hooks)


def _q(x):
    return int(math.floor(min(max(x, 0.0), 1.0) * 255.0 + 0.5))


@unittest.skipUnless(GAME, 'RCRA_GAME is not set')
class RealSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.games.rcra.source import RcraSource
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = RcraSource(GAME, Path(cls.temp.name) / 'out')

    @classmethod
    def tearDownClass(cls):
        cls.source.close()
        cls.temp.cleanup()

    def material_of(self, graph_path):
        for key in self.source.ids_of_type('material'):
            if any(r['path'] == graph_path for r in self.source.asset_references(key)):
                return key
        self.fail('no material uses ' + graph_path)

    def test_terms_repack_to_the_writers_bits(self):
        """Each term, packed again with the engine's formulas, gives the writer's own G-buffer words."""
        rng = np.random.default_rng(3)
        for graph_path in GRAPHS:
            gkey = next(k for k in self.source.resolve(graph_path) if self.source.entries[k]['type'] == 'materialgraph')
            result = rsurface.surface(self.source.asset_data(gkey), self.source.asset_data(self.material_of(graph_path)))
            g, terms, outputs = result['graph'], result['terms'], result['outputs']
            roots = list(terms.values()) + [outputs[('o', r, c)] for r in (0, 1) for c in range(4)]
            for _ in range(25):
                n = rng.normal(size=3)
                n /= np.linalg.norm(n)
                t = np.cross(n, rng.normal(size=3))
                t /= np.linalg.norm(t)
                inputs = defaultdict(float, {'TEXCOORD.x': rng.random(), 'TEXCOORD.y': rng.random(), 'SV_IsFrontFace': True,
                                             'TEXCOORD3.w': 1.0, **{'TEXCOORD2.' + a: n[i] for i, a in enumerate('xyz')},
                                             **{'TEXCOORD3.' + a: t[i] for i, a in enumerate('xyz')},
                                             **{'TEXCOORD4.' + a: 1.0 for a in 'xyzw'}})
                texels = defaultdict(lambda: [float(x) for x in rng.random(4)])
                values = sm5.evaluate(g, roots, inputs, lambda node, args: texels[node['binding']])
                v = {k: values[i] for k, i in terms.items()}
                word = {(r, c): values[outputs[('o', r, c)]] for r in (0, 1) for c in range(4)}
                self.assertEqual(word[(0, 2)], _q(math.sqrt(v['f0.r'])) << 8 | _q(math.sqrt(v['f0.g'])), graph_path)
                self.assertEqual(word[(0, 3)] & 0xFF, _q(v['gloss']), graph_path)
                self.assertEqual(word[(0, 3)] >> 8, _q(math.sqrt(v['f0.b'])), graph_path)
                weight = 1.0 / math.sqrt(8.0 * abs(v['normal.z']) + 8.0)
                for c, axis in ((0, 'normal.x'), (1, 'normal.y')):
                    packed = int(v[axis] * weight * rsurface.NORMAL_SCALE + 2048.0)
                    self.assertLessEqual(abs((word[(0, c)] & 0xFFF) - packed), 1, graph_path)
                self.assertEqual(bool(word[(0, 1)] & 0x8000), v['normal.z'] < 0, graph_path)
                for c, axis in enumerate(('albedo.r', 'albedo.g', 'albedo.b')):
                    self.assertAlmostEqual(min(max(word[(1, c)], 0.0), 1.0), v[axis], places=6)

    def test_blend_modes_and_forward_recipes(self):
        from rc2016.games.rcra import material as rmat
        for path, blend, lit in BLENDS:
            data = self.source.asset_data(self.source.resolve(path)[0])
            self.assertEqual(rmat.material_blend(data), blend, path)
            self.assertEqual(bool(rmat.draw_flags(data)), lit, path)
        with tempfile.TemporaryDirectory() as tmp:
            entry = blender_export.material_entry(self.source, DOME, Path(tmp))
        self.assertEqual(entry['blend'], 'blended')
        self.assertIn('recipe', entry)
        self.assertTrue({'VIEW.x', 'VIEW.y', 'VIEW.z'} <= set(entry['forward']['inputs']))   # the rim reads the view

        def alpha(view):
            inputs = {'TEXCOORD.x': 0.3, 'TEXCOORD.y': 0.3, 'TEXCOORD2.x': 0.0, 'TEXCOORD2.y': 1.0, 'TEXCOORD2.z': 0.0,
                      'TEXCOORD3.x': 1.0, 'TEXCOORD3.y': 0.0, 'TEXCOORD3.z': 0.0, 'TEXCOORD3.w': 1.0, 'SV_IsFrontFace': True,
                      'VIEW.x': view[0], 'VIEW.y': view[1], 'VIEW.z': view[2], 'CAMERA.x': 0.0, 'CAMERA.y': 0.0, 'CAMERA.z': 0.0}
            terms = rsurface.evaluate_recipe(entry['forward'], inputs, lambda node, args: [0.5, 0.5, 0.5, 1.0])
            return terms['alpha']
        self.assertLess(alpha((0.0, -3.0, 0.0)), alpha((2.9, -0.3, 0.0)))   # glass is clearer face-on than at its rim

    @unittest.skipUnless(os.path.isfile(BLENDER), 'Blender is not installed')
    def test_blender_forward_nodes_bake_to_the_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = blender_check.check(self.source, DOME, BLENDER, tmp, size=13)
        self.assertLess(report['forward_colour']['max'], 1e-4)
        self.assertLess(report['forward_alpha']['max'], 1e-5)

    @unittest.skipUnless(os.path.isfile(BLENDER), 'Blender is not installed')
    def test_blender_nodes_bake_to_the_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = blender_check.check(self.source, CHECKED, BLENDER, tmp, size=19)
        for term in ('albedo', 'f0', 'normal'):
            self.assertLess(report[term]['max'], 1e-3, term)
        self.assertLessEqual(report['scalars']['max'], 2.5 / 255)   # 8-bit rounding ties
        self.assertEqual(report['alpha']['max'], 0.0)


if __name__ == '__main__':
    unittest.main()
