"""The light cone pixel program as Python (rc2016/levels/cone_shader.py) against the translated game program 0xE8CDB312."""
import math
import os
import random
import unittest
from pathlib import Path

from rc2016.levels import cone_shader

class FormulaTests(unittest.TestCase):
    def test_the_fade_is_zero_at_the_apex_and_the_base_and_one_minus_u_between(self):
        self.assertEqual(cone_shader.fade(0.0), 0.0)
        self.assertEqual(cone_shader.fade(1.0), 0.0)
        self.assertAlmostEqual(cone_shader.fade(0.5), 0.5, places=6)    # 1 - 0.5^100 is 1
        self.assertAlmostEqual(cone_shader.fade(0.02), (1 - 0.98 ** 100) * 0.98, places=9)
        self.assertLess(cone_shader.fade(0.02), 0.9 * 0.98)                          # the steep start at the apex

    def test_facing_is_one_looking_along_the_normal_and_the_face_flips_the_sign(self):
        self.assertAlmostEqual(cone_shader.facing((0, 0, 1), (0, 0, 2), True), 1.0 - 0.001 ** 2)
        self.assertEqual(cone_shader.facing((0, 0, 1), (0, 0, 2), False), 0.0)            # a back face seen from the normal's side
        self.assertAlmostEqual(cone_shader.facing((0, 0, -1), (0, 0, 2), False), 1.0 - 0.001 ** 2)
        self.assertEqual(cone_shader.facing((1, 0, 0), (0, 1, 0), True), 0.0)             # edge on

    def test_the_output_is_additive_light_that_fog_replaces_by_its_transmittance(self):
        rgb, alpha, keep = cone_shader.cone_response(0.3, 0.2, (0, 0, 1), (0, 0, 1), True, 0.5, 20.0, 10.0, 1.0, 0.0, 1.0, 0.01,
                                                      (1.0, 0.5, 0.25), (9.0, 9.0, 9.0))
        self.assertTrue(keep)
        self.assertGreater(alpha, 0.0)
        for got, want in zip(rgb, (1.0, 0.5, 0.25)):
            self.assertGreater(got, 0.01 * want)                  # the hot core adds to amplitude x colour
        rgb, *_ = cone_shader.cone_response(0.3, 0.2, (0, 0, 1), (0, 0, 1), True, 0.5, 20.0, 10.0, 1.0, 0.0, 0.0, 0.01,
                                            (1.0, 0.5, 0.25), (9.0, 8.0, 7.0))
        self.assertEqual(rgb, [9.0, 8.0, 7.0])                    # transmittance 0: the fog colour

    def test_fragments_in_front_of_the_scene_fade_with_the_depth_gap(self):
        def alpha(gap):
            return cone_shader.cone_response(0.5, 0.0, (0, 0, 1), (0, 0, 1), True, 1.0, 10.0 + gap, 10.0, 1.0, 0.0, 1.0, 0.01,
                                             (1, 1, 1), (0, 0, 0))[1]
        self.assertEqual(alpha(-1.0), 0.0)
        self.assertLess(alpha(0.05), alpha(0.3))
        self.assertEqual(alpha(5.0), alpha(50.0))

    def test_the_threshold_discards_faint_fragments(self):
        args = (0.5, 0.0, (0, 0, 1), (0, 0, 1), True, 1.0, 20.0, 10.0, 1.0)
        self.assertTrue(cone_shader.cone_response(*args, 0.0, 1.0, 0.01, (1, 1, 1), (0, 0, 0))[2])
        self.assertFalse(cone_shader.cone_response(*args, 0.9, 1.0, 0.01, (1, 1, 1), (0, 0, 0))[2])


RESEARCH = os.environ.get('RC2016_RESEARCH')


@unittest.skipUnless(RESEARCH and Path(RESEARCH).is_dir(), 'RC2016_RESEARCH (the game archive) is not set')
class TranslatedProgramTests(unittest.TestCase):
    """The reference against the DAG the translator makes of the game's code, on random inputs."""

    @classmethod
    def setUpClass(cls):
        from rc2016.levels.scene import asset_bytes, open_archive
        from rc2016.shaders import translator
        archive = open_archive(RESEARCH)
        graph = asset_bytes(archive, 'materialgraph/visualeffect/environment/global/ambient/gbl_amb_light_cone_circular_med/'
                                     'gbl_amb_light_cone_circular_med.materialgraph')
        program = next(p for p in translator.pixel_programs(graph) if p['shader_id'] == cone_shader.PROGRAM_ID)
        cls.compiler = translator.Compiler(program['code'], program['usage_slots'], program['ps_input_addr'], program['ps_input_ena'],
                                           program['vgpr_count'])
        cls.compiler.run(translator.decoder().decode_stream(program['code'], 0))
        cls.ir = cls.compiler.ir
        cls.program = program
        # the three half floats read from the instance record are the colour inputs
        cls.colour_nodes = [i for i, n in enumerate(cls.ir.nodes) if n['op'] == 'unsupported' and n['reason'] == 'TBUFFER_LOAD_FORMAT_XYZ']
        for k, i in enumerate(cls.colour_nodes):
            cls.ir.nodes[i] = dict(op='input', args=[], name=f'record.colour.{k}', kind='record')
        cls.roots = list(cls.compiler.exports[0])
        cls.keep = cls.compiler.export_exec[0]

    def test_the_program_is_the_one_the_module_describes(self):
        self.assertEqual(self.program['shader_id'], cone_shader.PROGRAM_ID)
        self.assertEqual(len(self.colour_nodes), 3)
        self.assertTrue(self.compiler.exec_masked_export)

    def test_the_reference_equals_the_translated_program_on_random_inputs(self):
        rng = random.Random(7)
        for _ in range(300):
            u, v = rng.random(), rng.random()
            n = [rng.uniform(-1, 1) for _ in range(3)]
            eye = [rng.uniform(-1, 1) for _ in range(3)]
            front = rng.random() < 0.5
            noise, scene_depth, pos_w = rng.random(), rng.uniform(0.0, 50.0), rng.uniform(0.5, 50.0)
            alpha, threshold, fog_T = rng.random(), rng.choice([0.0, rng.random() * 0.2]), rng.random()
            amplitude, colour, fog = rng.uniform(0.001, 0.05), [rng.uniform(0, 50) for _ in range(3)], [rng.uniform(0, 6) for _ in range(3)]
            inputs = {'attribute0.0': u, 'attribute0.1': v, 'attribute0.2': threshold, 'attribute0.3': alpha,
                      'attribute1.0': n[0], 'attribute1.1': n[1], 'attribute1.2': n[2],
                      'attribute2.0': eye[0], 'attribute2.1': eye[1], 'attribute2.2': eye[2], 'attribute2.3': fog_T,
                      'ps.FRONT_FACE': 1.0 if front else 0.0, 'ps.POS_W': pos_w, 'CB0[99]': 0.0, 'CB0[115]': amplitude,
                      'CB0[112]': fog[0], 'CB0[113]': fog[1], 'CB0[114]': fog[2], 'record.colour.0': colour[0],
                      'record.colour.1': colour[1], 'record.colour.2': colour[2]}
            for node in self.ir.nodes:      # the remaining inputs only feed the texture coordinates, which the texture values replace
                if node['op'] == 'input':
                    inputs.setdefault(node['name'], 0.0)
            textures = {(0, 0): scene_depth, (1, 0): noise}
            want, want_alpha, want_keep = cone_shader.cone_response(u, v, n, eye, front, noise, scene_depth, pos_w, alpha, threshold,
                                                                    fog_T, amplitude, colour, fog)
            got = [self.ir.evaluate(root, inputs, textures) for root in self.roots]
            for channel in range(3):
                self.assertAlmostEqual(got[channel], want[channel], delta=1e-4 * max(1.0, abs(want[channel])))
            self.assertAlmostEqual(got[3], want_alpha, delta=1e-6)
            self.assertEqual(self.ir.evaluate(self.keep, inputs, textures) != 0.0, want_keep)


if __name__ == '__main__':
    unittest.main()
