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


class ActiveAuthoredLightTests(unittest.TestCase):
    """The inferred point-light extension applies only to the built Level atmosphere lighting."""

    def setUp(self):
        self.lights = [dict(position=[0, 0, 0], inverse_radius=1., colour=[1, 1, 1], validated=True),
                       dict(position=[1, 0, 0], inverse_radius=1., colour=[1, 1, 1], validated=False),
                       dict(position=[2, 0, 0], inverse_radius=1., colour=[1, 1, 1], validated=False)]
        self.view = object.__new__(LevelViewport)
        self.view._scene_lighting = {'lights': self.lights, 'notes': []}
        self.view.lighting = 'Viewer rig'

    def atmosphere(self, built=True):
        self.view.lighting = 'Level atmosphere'
        self.view._environment_lighting = 'Level atmosphere' if built else None
        self.view._environment = {'sun_direction': (0, 0, 1)} if built else None

    def test_other_lighting_sources_use_only_the_validated_lights(self):
        self.assertEqual(self.view._active_authored_lights(), self.lights[:1])
        self.view.lighting = 'Shared HDRI'
        self.assertEqual(self.view._active_authored_lights(), self.lights[:1])

    def test_level_atmosphere_adds_the_extension_once_its_maps_are_built(self):
        self.atmosphere(built=False)
        self.assertEqual(self.view._active_authored_lights(), self.lights[:1])
        self.atmosphere(built=True)
        self.assertIs(self.view._active_authored_lights(), self.lights)

    def test_selection_is_cached_and_follows_the_lighting_and_the_light_list(self):
        first = self.view._active_authored_lights()
        self.assertIs(self.view._active_authored_lights(), first)
        self.atmosphere()
        self.assertEqual(len(self.view._active_authored_lights()), 3)
        self.view._scene_lighting = {'lights': self.lights[:2], 'notes': []}
        self.assertEqual(len(self.view._active_authored_lights()), 2)

    def test_lights_without_the_marker_count_as_validated(self):
        self.view._scene_lighting = {'lights': [dict(position=[0, 0, 0])], 'notes': []}
        self.assertEqual(len(self.view._active_authored_lights()), 1)


if __name__ == '__main__':
    unittest.main()
