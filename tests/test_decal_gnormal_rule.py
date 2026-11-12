"""mo_decal_gnormal mesh decals: forward 83652CC4 (the Deplanetizer dome grime; docs/material-decoding/deplanetizer-exterior.md).

Synthetic checks (rule binding, uniform packing, GLSL text, validator constants) always run; the archive checks read the real
material and skip without RC2016_RESEARCH.
"""
import os
import unittest
from pathlib import Path

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.grey_g_graphs import GREY_G_GRAPHS
from rc2016.viewer.shading import GLASS_FRAGMENT, GLASS_KINDS, glass_uniforms

ROOT = Path(__file__).resolve().parents[1]
GRAPH = 'materialgraph/environment/global/decal/mo_decal_gnormal.materialgraph'
SHA = '7849348e0e69b686b28d581f271ec3441a4e914fb486243b7cdbd564462c31a8'
MATERIAL = 'material/environment/nebula/architecture/neb_arch_dirt_decal_01/rv_dep_ext_arch_grime_01.material'
TEXTURE = 'textures/environment/nebula_g34/decals/mo_neb_dirt_decal_01/mo_neb_dirt_decal_01'
PARAMS = dict(f0=1.0, gloss=1.0, albedo_blend=1.0, albedo_power=1.0, record_colour=(1.0, 1.0, 1.0), alpha_scale=38 / 255,
              alpha_cut=7 / 255)


def sat(x):
    return max(0.0, min(1.0, x))


def decal_terms(c, g, p=PARAMS):
    """83652CC4 material terms from a _c (rgba) and _g (rg) sample: (alpha or None when cut, albedo, F0, gloss)."""
    cover = sat(c[3])
    if cover < p['alpha_cut']:
        return None, None, None, None
    albedo = [ch + p['albedo_blend'] * (sat(abs(ch * s) ** p['albedo_power']) - ch) for ch, s in zip(c[:3], p['record_colour'])]
    return sat(cover * p['alpha_scale']), albedo, p['f0'] * g[1], sat(p['gloss'] * g[0])


class RuleTests(unittest.TestCase):
    def test_rule_binding(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: SHA})
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})   # bound to the exact graph bytes
        self.assertEqual((rule['forward'], rule['writer']), ('decal_gnormal', '83652CC4'))
        self.assertEqual(rule['slots'], {'_window_c': 0x49A7866BABDAD780, '_window_g': 0x49A7866B1576D848})
        self.assertEqual([key for _, key, _ in rule['params']], [0x62835AF9, 0x24F4CA4C, 0xF0A64F9B, 0x965B2DCD])
        self.assertNotIn(GRAPH, GREY_G_GRAPHS)   # the exact rule replaces the grey _g family reading

    def test_uniforms_and_glsl(self):
        self.assertEqual(GLASS_KINDS['decal_gnormal'], 24)
        kind, a, b, c, d, e = glass_uniforms(dict(kind='decal_gnormal', params=PARAMS))
        self.assertEqual((kind, a, b[3], c[0]), (24, (1.0, 1.0, 1.0, 1.0), 38 / 255, 7 / 255))
        branch = GLASS_FRAGMENT.split('uKind == 24')[1].split('} else {')[0]
        for text in ('float cover = sat(c.a)', 'if (cover < uC.x) discard', 'alpha = sat(cover * uB.w)',
                     'mix(c.rgb, clamp(pow(abs(c.rgb * uB.rgb), vec3(uA.w)), 0.0, 1.0), uA.z)',
                     'pow(2.0, -5.5 * sqrt(sat(uA.y * g.r)))', 'A * Ld + glassSpec(N, V, L1, L2, C1, C2, vec3(uA.x * g.g), a, ambient)'):
            self.assertIn(text, branch)

    def test_closed_form(self):
        # Defaults (K = p = 1, white record colour): the albedo is _c.rgb; the opacity byte caps the dirt at 38/255.
        alpha, albedo, f0, gloss = decal_terms((0.04, 0.04, 0.04, 1.0), (0.0382, 0.0382))
        self.assertAlmostEqual(alpha, 38 / 255)
        self.assertEqual([round(x, 6) for x in albedo], [0.04, 0.04, 0.04])
        self.assertEqual((round(f0, 4), round(gloss, 4)), (0.0382, 0.0382))
        self.assertEqual(decal_terms((0.5, 0.5, 0.5, 0.02), (0.0, 0.0))[0], None)   # below attribute0.z: discarded
        # the power and record colour act only through the blend K
        _, albedo, _, _ = decal_terms((0.5, 0.25, 1.0, 1.0), (0.0, 0.0), dict(PARAMS, albedo_power=2.0, record_colour=(2.0, 1.0, 1.0)))
        self.assertEqual([round(x, 6) for x in albedo], [1.0, 0.0625, 1.0])

    def test_validator_and_blender(self):
        self.assertEqual(validation.GLASS_CONSTANTS['decal_gnormal'],
                         ('f0', 'gloss', 'albedo blend', 'albedo power', 'record colour', 'alpha scale', 'alpha cut'))
        rule = dict(applies=True, ported=['decal_gnormal'],
                    terms={'decal_gnormal': dict(kind='decal_gnormal', params=dict(PARAMS), blend='alpha_over')},
                    textures={'_c': None, '_g': None, '_window_c': TEXTURE + '_c.texture', '_window_g': TEXTURE + '_g.texture'})
        names, roles = validation._viewer_required(rule, False)
        self.assertTrue(set(names) <= set(validation._viewer_expected_constants(rule)))
        self.assertEqual(roles, ['_window_c', '_window_g'])
        text = (ROOT / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        self.assertIn("'decal_gnormal'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])
        body = text.split("elif key == 'decal_gnormal':")[1].split("elif key == 'window_glass':")[0]
        for name in ['glass ' + n for n in validation.GLASS_CONSTANTS['decal_gnormal']] + ['glass decal colour map', 'glass decal gloss map']:
            self.assertIn("'%s'" % name, body)


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ.get('RC2016_GAME_ROOT', os.environ['RC2016_RESEARCH']))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(root)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_dome_grime(self):
        paths = tp.material_texture_paths(self.archive, MATERIAL)
        forward, p = paths['forward'], paths['forward']['params']
        self.assertEqual((forward['kind'], forward['writer'], forward['blend'], forward['two_sided'], forward['graph_sha256']),
                         ('decal_gnormal', '83652CC4', 'alpha_over', False, SHA))   # flags 0x23a
        self.assertEqual((paths['_window_c'], paths['_window_g']), (TEXTURE + '_c.texture', TEXTURE + '_g.texture'))
        for role in ('_c', '_g'):
            self.assertNotIn(role, paths)   # the forward program reads them on the window roles (_c alpha is coverage)
        self.assertEqual((p['f0'], p['gloss'], p['albedo_blend'], p['albedo_power']), (1.0, 1.0, 1.0, 1.0))
        self.assertAlmostEqual(p['alpha_scale'], 38 / 255)
        self.assertAlmostEqual(p['alpha_cut'], 7 / 255, places=6)

    def test_blender_rule(self):
        from rc2016.formats.materials import viewer_graph_rule
        rule = viewer_graph_rule(self.archive, MATERIAL)
        self.assertTrue(rule['applies'])
        self.assertEqual((rule['ported'], rule['unported']), (['decal_gnormal'], []))


if __name__ == '__main__':
    unittest.main()
