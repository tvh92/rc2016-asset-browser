"""Source and synthetic controls for the exact 5881 stage."""
import math
import struct
import unittest

from rc2016.levels import scene_ao


class SceneAOControls(unittest.TestCase):
    def test_captured_stage_controls_are_float32_payload(self):
        payload = scene_ao._control_words(240, 136)
        self.assertEqual(len(payload), 28 * 4)
        values = struct.unpack("<28f", payload)
        self.assertAlmostEqual(values[0], 1 / 240, places=9)
        self.assertAlmostEqual(values[1], 1 / 136, places=9)
        for got, expected in zip(values[4:16], scene_ao.CAPTURED_DEPTH_REFERENCES):
            self.assertAlmostEqual(got, expected, places=5)
        for got, expected in zip(values[16:28], scene_ao.CAPTURED_WEIGHTS):
            self.assertAlmostEqual(got, expected, places=8)

    def test_phase_mapping_is_a_bijection_for_each_four_by_four_block(self):
        phases = {scene_ao._phase_index(x, y) for y in range(4) for x in range(4)}
        self.assertEqual(phases, set(range(16)))
        self.assertEqual(scene_ao._phase_index(2, 1), 6)  # source layer: (y << 2) | x
        self.assertEqual(scene_ao._phase_index(4, 5), scene_ao._phase_index(0, 1))
        self.assertIn("ivec3(q,4*py+px)", scene_ao._DEINTERLEAVE)
        self.assertIn("min(min(d0,d1),min(d2,d3))", scene_ao._DEINTERLEAVE)
        self.assertIn("if(texelFetch(uDepth,p,0).r<=0.0)", scene_ao._UPSAMPLE)

    def test_adapted_core_preserves_native_tile_and_exact_reduction(self):
        shader = scene_ao._compute_source()
        self.assertIn("textureGather", shader)
        self.assertIn("shared_mem_u32[256]", shader)
        self.assertIn("precise float _1199 = 2.0 * _1197;", shader)
        self.assertIn("vec4(clamp(_1199,0.0,1.0))", shader)
        self.assertNotIn("cos(angle)", shader)
        self.assertNotIn("weightSum", shader)
        self.assertEqual(shader.count("imageStore(cs_img1"), 1)

    def test_projection_contract_and_parity_boundary(self):
        m = (2.0, 0, 0, 0, 0, 3.0, 0, 0, 0, 0, -1.0, -1.0, 0, 0, -0.2, 0)
        self.assertEqual(scene_ao._projection_diagonal(m), (2.0, 3.0))
        near, far = 0.1, 5000.0
        p = [2.0,0,0,0, 0,3.0,0,0, 0,0,-(far+near)/(far-near),-1.0, 0,0,-2*far*near/(far-near),0]
        self.assertAlmostEqual(scene_ao._projection_far(p), far, places=2)
        self.assertFalse(scene_ao.GAME_EQUIVALENCE)
        with self.assertRaises(ValueError):
            scene_ao._projection_diagonal((1.0, 0.0))
        with self.assertRaises(ValueError):
            scene_ao._control_words(0, 136)


if __name__ == "__main__":
    unittest.main()
