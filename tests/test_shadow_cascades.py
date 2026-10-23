"""Sun shadow cascades: the fitting rule against one captured Novalis frame, and the viewer's GL mapping.

The constants are the frame block CB0 of the lighting pass (docs/material-decoding/shadow-atlas.md): camera,
view axes and field of view, the sun ray, the light basis (dwords 160 to 170), the cascade lanes (144 to 159) and
the per-cascade scale and offset rows (172 to 219) of the game's 8192 x 4096 atlas."""
import math
import unittest

import numpy as np

from rc2016.levels import shadow_cascades as sc
from rc2016.levels.scene_effects import AO_SETTLE_SECONDS, SHADOW_FOLLOW_SECONDS, SceneEffects
from rc2016.levels.viewport import BASIS, projection_matrix, view_matrix

CAMERA = (140.071732, 63.6009293, 151.753494)
FORWARD = (0.771155715, -0.365430444, -0.521324754)
RIGHT = (0.56005919, 2.98023224e-08, 0.828452587)
DOWN = (-0.302741796, -0.930838585, 0.20466271)
TAN = (0.7798812389, 0.4386831820)
NEAR = 0.1
TOWARD_SUN = (-0.0776798204, 0.669130623, 0.739073813)
BASIS_COLUMNS = np.array([0.994521856, -0.069943212, 0.0776798204, 0, 1.11758709e-08, -0.74314481, -0.669130623, 0,
                          0.104528502, 0.665465057, -0.739073813, 0]).reshape(3, 4)[:, :3]   # CB0[160..170]: x, y, z coefficients
LANES = np.array([[0.000745385769, -0.000187822225, 0.000248346914, -0.130149364],     # CB0[144..159], lane 0 and 1
                  [0.000738491246, 0.000637112884, -0.000499242742, -0.0682008266]])  # (lanes 2 and 3 repeat lane 1)
SCALE = np.array([0.011923803, 0.00477147568, 0.00190859055, 0.000763436314, 0.000305374531, 0.000122234735])
OFFSET = np.array([[-1.82251, -0.7971192, 2.30804157, 0.0225225724], [-0.714721739, 0.268798828, 0.923331022, 0.0562833622],
                   [-0.0517578125, 0.0827636719, 0.367490619, 0.140708402], [0.125976562, 0.620849609, 0.145151436, 0.351770937],
                   [0.415771484, 0.223632798, 0.0584801994, 0.879427314], [0.444213867, 0.677246094, 0.34960711, 2.19704103]])
DEPTH_SCALE = np.array([0.0160471629, 0.00644076476, 0.00257630693, 0.00103052298, 0.000428863976, 0.000244473194])


def captured_fit(**kw):
    return sc.fit(CAMERA, FORWARD, RIGHT, -np.array(DOWN), TAN[0], TAN[1], NEAR, TOWARD_SUN, **kw)


def captured_tiles():
    """Width, centre (light xy) and near depth of each captured cascade from its scale and offset rows."""
    width = (sc.TILE / sc.GAME_ATLAS[0]) / SCALE
    centres = []
    for j in range(sc.CASCADES):
        x0, y0 = sc.tile_origin(j)
        u = (x0 + sc.TILE / 2) / sc.GAME_ATLAS[0]
        v = (y0 + sc.TILE / 2) / sc.GAME_ATLAS[1]
        centres.append(((u - OFFSET[j, 0]) / SCALE[j], (v - OFFSET[j, 1]) / (2 * SCALE[j])))
    return width, np.array(centres), -OFFSET[:, 2] / DEPTH_SCALE


class CapturedFitTests(unittest.TestCase):
    def test_basis_is_the_captured_light_space(self):
        self.assertLess(np.abs(captured_fit()['basis'] - BASIS_COLUMNS.T).max(), 1e-6)

    def test_lanes_reproduce_the_captured_cascade_measure(self):
        fit = captured_fit()
        R = fit['basis']
        world = np.array([np.append(gx * R[0] + gy * R[1], c) for gx, gy, c in fit['lanes']])
        scale = np.abs(LANES).max(axis=0)
        error = np.abs((world[:, None] - LANES[None]) / scale).max(axis=2)   # fitted lane x captured lane
        self.assertLess(error.min(axis=0).max(), 2e-3)   # every captured lane is fitted
        self.assertLess(error.min(axis=1).max(), 2e-3)   # and no other lane is
        self.assertLess(np.abs(world[:, :3] @ np.array(CAMERA) + world[:, 3]).max(), 1e-6)   # zero at the camera

    def test_split_distances_and_bands(self):
        np.testing.assert_allclose(sc.split_distances(), [10.24, 25.6, 64, 160, 400, 1000])
        j, frac, blend, fade = sc.cascade_band(np.array([0.005, 0.0103, 0.02, 0.99 * 0.064, 0.5, 0.95, 1.2]))
        self.assertEqual(j.tolist(), [0, 1, 1, 2, 5, 5, 5])
        self.assertEqual(blend[0], 0.0)
        self.assertGreater(blend[3], 0.0)              # last sixteenth of band 2 blends into cascade 3
        self.assertAlmostEqual(float(fade[5]), 0.5)
        self.assertEqual(float(fade[6]), 1.0)
        self.assertAlmostEqual(1.0 / math.log2(sc.SPLIT_RATIO), 0.7564708590507507, places=6)   # CB0[140]

    def test_tile_width_centre_depth_and_normal_offset_follow_the_rule(self):
        fit = captured_fit()
        width, centres, z_near = captured_tiles()
        np.testing.assert_allclose(fit['widths'], width, rtol=1.5e-3)
        texel = width / sc.TILE
        self.assertLess(np.abs((fit['centres'] - centres) / texel[:, None]).max(), 1.0)
        np.testing.assert_allclose(fit['z_near'][:5], z_near[:5], atol=texel[:5].max() + 0.02)
        np.testing.assert_allclose(fit['normal_offset'], OFFSET[:, 3], rtol=1.5e-3)
        np.testing.assert_allclose(fit['z_range'][1:4] * DEPTH_SCALE[1:4], 1.0, rtol=1e-3)

    def test_measure_selects_the_band_of_a_frustum_corner(self):
        fit = captured_fit()
        corner = np.array(CAMERA) + 400.0 * (np.array(FORWARD) + TAN[0] * np.array(RIGHT) - TAN[1] * np.array(DOWN))
        self.assertAlmostEqual(float(sc.cascade_measure(fit, corner)), 0.4, places=4)


class SyntheticFitTests(unittest.TestCase):
    def test_light_along_the_view_gives_four_lanes(self):
        fit = sc.fit((0, 0, 0), (0, -1, 0), (1, 0, 0), (0, 0, -1), 0.5, 0.4, 0.1, (0, 1, 0))
        self.assertEqual(len({tuple(np.round(lane, 9)) for lane in fit['lanes']}), 4)
        far = np.array([[sx * 0.5 * 1000, -1000, sz * 0.4 * 1000] for sx in (-1, 1) for sz in (-1, 1)])
        np.testing.assert_allclose(sc.cascade_measure(fit, far), 1.0, rtol=1e-9)

    def test_gl_matrices_land_where_the_lookup_reads(self):
        fit = captured_fit(tile=1024)
        aw, ah = sc.atlas_size(1024)
        scale, offset, bounds = sc.lookup_constants(fit)
        rng = np.random.default_rng(1)
        for j in range(sc.CASCADES):
            view, proj = sc.gl_matrices(fit, j)
            R, W = fit['basis'], fit['widths'][j]
            l = np.concatenate([fit['centres'][j] + (rng.random((50, 2)) - 0.5) * W * 0.9,
                                fit['z_near'][j] + rng.random((50, 1)) * fit['z_range'][j]], axis=1)
            native = l @ R
            clip = (proj @ view @ np.concatenate([native, np.ones((50, 1))], axis=1).T).T
            ndc = clip[:, :3] / clip[:, 3:]
            x0, y0 = sc.tile_origin(j, 1024)
            window = np.stack([(x0 + (ndc[:, 0] + 1) * 512) / aw, (y0 + (ndc[:, 1] + 1) * 512) / ah, (ndc[:, 2] + 1) / 2], axis=1)
            lookup = l * scale[j, :3] + offset[j, :3]
            np.testing.assert_allclose(window, lookup, atol=1e-9)
            planes = sc.cascade_planes(fit, j)
            self.assertTrue(np.all(native @ planes[:, :3].T + planes[:, 3] >= -1e-6))
            self.assertTrue(np.all((lookup[:, :2] >= bounds[j, :2] - 0.05 / 1024) & (lookup[:, :2] <= bounds[j, 2:] + 0.05 / 1024)))

    def test_camera_from_gl_reads_the_level_camera(self):
        eye, yaw, pitch = (140.07, -151.75, 63.6), 34.05, -21.43
        modelview = np.asarray(view_matrix(eye, yaw, pitch), float).reshape(4, 4).T @ np.asarray(BASIS, float).reshape(4, 4).T
        camera, forward, right, up, tx, ty, near = sc.camera_from_gl(projection_matrix(1920, 1080, 0.1, 4000.0), modelview.T.flatten())
        np.testing.assert_allclose(camera, (140.07, 63.6, 151.75), atol=1e-6)
        self.assertAlmostEqual(ty, math.tan(math.radians(30.0)))
        self.assertAlmostEqual(tx, ty * 1920 / 1080)
        self.assertAlmostEqual(near, 0.1, places=6)
        self.assertAlmostEqual(float(np.dot(forward, right)), 0.0)
        self.assertAlmostEqual(float(np.cross(right, up) @ -forward), 1.0)   # right-handed eye axes

    def test_caster_floor_without_depth_clamp_keeps_the_far_plane(self):
        fit = captured_fit()
        floor = captured_fit(caster_min_z=-500.0)
        np.testing.assert_allclose(floor['z_near'], -500.0)
        np.testing.assert_allclose(floor['z_near'] + floor['z_range'], fit['z_near'] + fit['z_range'])


class View:
    def __init__(self, rested):
        self.after_calls = []
        self._camera_changed_at = rested

    def after(self, ms, callback):
        self.after_calls.append((ms, callback))
        return len(self.after_calls)


class CameraRefitTests(unittest.TestCase):
    def effects(self):
        e = SceneEffects.__new__(SceneEffects)
        e.shadow_key, e.shadow_time, e.shadow_retry = (1, (0.0, 1.0, 0.0), ('a',)), 100.0, None
        return e

    def test_moving_camera_waits_until_it_rests_or_the_follow_interval(self):
        e, view = self.effects(), View(rested=100.05)
        self.assertFalse(e._shadow_due(view, (1, (0.0, 1.0, 0.0), ('b',)), 100.1))
        self.assertEqual(len(view.after_calls), 1)
        self.assertTrue(e._shadow_due(View(rested=100.0), (1, (0.0, 1.0, 0.0), ('b',)), 100.0 + AO_SETTLE_SECONDS))
        self.assertTrue(e._shadow_due(View(rested=101.0), (1, (0.0, 1.0, 0.0), ('b',)), 100.0 + SHADOW_FOLLOW_SECONDS))


if __name__ == '__main__':
    unittest.main()
