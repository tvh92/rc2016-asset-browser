"""Blarg saucer dome glass FEEC00CE (blarg_saucer_glass): the pipeline viewer rule, the Blender front-end and its
validator agree, and the real material reads the orange colour-pick entry. The archive check is skipped without
RC2016_RESEARCH.
"""
import os
from pathlib import Path
import unittest

from rc2016.formats import materials
from rc2016.shaders import validation

PATTERNS = 'textures/character/shared/patterns_cma.texture'
MATERIAL = 'material/character/enemy/enm_blarg_saucer_glass/enm_blarg_saucer_glass.material'


class SaucerGlassRuleTest(unittest.TestCase):
    def test_pipeline_lists_the_kind(self):
        self.assertIn('glass_patterned', materials.VIEWER_RULE_BUILT)
        self.assertEqual(materials.VIEWER_TERM_ROLES['glass_patterned'], ('_n',))
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        self.assertIn("'glass_patterned'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])
        body = text.split("elif key == 'glass_patterned':")[1].split('elif key ==')[0]
        for name in ('glass rim exp', 'glass tiling', 'glass normal strength', 'glass pick rgb', 'glass f0', 'glass gloss',
                     'glass rim bias', 'glass pattern map', '0.400295'):
            self.assertIn(name, body)

    def test_blender_front_end_and_validator_agree(self):
        self.assertEqual(validation.GLASS_CONSTANTS['glass_patterned'],
                         ('tiling', 'normal strength', 'f0', 'gloss', 'pick rgb', 'rim bias', 'rim exp'))
        params = dict(tiling=1.5, normal_strength=0.03, f0=0.1, gloss=0.75, pick_u=0.125, pick_rgb=[0.7, 0.3, 0.0],
                      rim_bias=0.9, rim_exp=8.0)
        for textured in (True, False):
            rule = dict(applies=True, ported=['glass_patterned'],
                        terms={'glass_patterned': dict(kind='glass_patterned', params=params, blend='alpha_over')},
                        textures={'_c': None, '_g': None, '_n': PATTERNS if textured else None})
            names, roles = validation._viewer_required(rule, False)
            expected = validation._viewer_expected_constants(rule)
            self.assertTrue(set(names) <= set(expected))
            self.assertIn('VR glass pick rgb', names)
            self.assertEqual('VR glass tiling' in names, textured)
            self.assertEqual(roles, ['_n'] if textured else [])
            self.assertEqual(expected['VR glass pick rgb'], [0.7, 0.3, 0.0])


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class SaucerGlassArchiveTest(unittest.TestCase):
    def test_dome_reads_the_orange_pick_entry(self):
        from rc2016.levels.scene import open_archive
        archive = open_archive(Path(os.environ['RC2016_RESEARCH']))
        rule = materials.viewer_graph_rule(archive, MATERIAL)
        self.assertTrue(rule['applies'], rule.get('reason'))
        self.assertEqual(rule['ported'], ['glass_patterned'])
        term = rule['terms']['glass_patterned']
        self.assertEqual((term['blend'], term['params']['pick_u']), ('alpha_over', 0.125))
        r, g, b = term['params']['pick_rgb']   # sRGB (218, 151, 8) decoded: orange, no blue
        self.assertAlmostEqual(r, 0.701, places=3); self.assertAlmostEqual(g, 0.309, places=3); self.assertLess(b, 0.01)
        self.assertEqual(rule['textures']['_n'], PATTERNS)


if __name__ == '__main__':
    unittest.main()
