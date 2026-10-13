"""Authored light selection is computed once per frame and equals the per-call original."""
import ctypes
import random
import unittest
from unittest.mock import patch

import numpy as np

from rc2016.levels.viewport import LevelViewport


def original(matrix_floats, all_lights):
    """The selection _apply_lighting computed for every shader switch before caching."""
    mv = (ctypes.c_float * 16)(*matrix_floats); matrix = np.array(mv).reshape(4, 4).T
    lights = sorted(all_lights, key=lambda l: float(np.linalg.norm((matrix @ np.r_[l['position'], 1.])[:3])))[:32]
    positions, colours = [], []
    for light in lights:
        inverse = np.float32(light['inverse_radius'])
        positions.extend([*(matrix @ np.r_[light['position'], 1.])[:3], float(inverse)])
        colours.extend((np.array(light['colour'], dtype=np.float32) * np.float32(inverse * inverse)).tolist())
    return len(lights), list((ctypes.c_float * len(positions))(*positions)), list((ctypes.c_float * len(colours))(*colours))


class AuthoredLightCacheTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(3)
        self.lights = [dict(position=[rng.uniform(-500, 500) for _ in range(3)], inverse_radius=rng.uniform(0.01, 1),
                            colour=[rng.uniform(0, 5) for _ in range(3)]) for _ in range(940)]
        # Exact ties (same position) must keep their original order, as the stable sort does.
        for j in range(0, 40, 4):
            self.lights.insert(j, dict(self.lights[500 + j], inverse_radius=0.5 + j))
        self.matrix = [1., 0., 0., 0., 0., 0.8, -0.6, 0., 0., 0.6, 0.8, 0., 12., -3., -40., 1.]
        self.view = object.__new__(LevelViewport)

    def selection(self, matrix):
        count, positions, colours = self.view._authored_light_uniforms(bytes((ctypes.c_float * 16)(*matrix)), self.lights)
        return count, list(positions), list(colours)

    def test_matches_the_per_call_computation(self):
        self.assertEqual(self.selection(self.matrix), original(self.matrix, self.lights))

    def test_matches_from_many_cameras(self):
        rng = random.Random(9)
        for _ in range(40):
            matrix = list(self.matrix); matrix[12:15] = [rng.uniform(-600, 600) for _ in range(3)]
            self.assertEqual(self.selection(matrix), original(matrix, self.lights))

    def test_sorts_once_per_modelview_and_light_list(self):
        with patch('rc2016.levels.viewport.sorted', create=True, side_effect=sorted) as sort:
            for _ in range(50):   # fifty shader switches in one frame
                self.selection(self.matrix)
            self.assertEqual(sort.call_count, 1)
            moved = list(self.matrix); moved[12] += 1.0
            self.assertEqual(self.selection(moved), original(moved, self.lights))
            self.assertEqual(sort.call_count, 2)
            self.lights = list(self.lights)   # a new light list (set_scene_lighting) recomputes
            self.selection(moved)
            self.assertEqual(sort.call_count, 3)


if __name__ == '__main__':
    unittest.main()
