"""Nefarious mech rocket flame (rc2016/viewer/mech_flame_effect.py, rc2016/pipeline/mech_flame_effect.py): the model table,
the decoded program, the instance transforms, the viewer triangles, the shader kind, the Blender description, the decoded
effect (game files) and the transforms against the mech capture (Research)."""
import math
import os
import unittest
from pathlib import Path

from rc2016.viewer import mech_flame_effect as fx
from rc2016.viewer import shading

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / 'Research/captures/2026-11-12_nefarious-mech'
# igLoc_thruster through joint 2's rest matrix: at the model origin, local Z = native -Y (down).
THRUSTER_ROWS = [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)]
IDENTITY_ROWS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
IDENTITY_VIEW = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -20.0, 1.0]
# The effect file's mesh components: slot b4 value and spread, offset, b2 x b5 (decoded from the .visualeffect).
COMPONENTS = ((2, (0.8, 0.8, 1.5), (-0.1, -0.1, -0.25), (0.0, 0.0, -0.15), 2.5),
              (3, (1.1, 1.1, 2.0), (-0.2, -0.2, -0.25), (0.0, 0.0, -0.35), 1.0))
# The capture's instance transforms in the mech's native space (exact fit of the post-VS vertices): scale, spin about local Z
# (degrees) and origin; the posed locator was 0.2169 u above its rest position.
CAPTURED = {2: ((0.70361, 0.70361, 1.25879), -0.335, (0.0, 0.3669, 0.0)),
            3: ((0.90723, 0.90723, 1.75879), -71.731, (0.0, 0.5669, 0.0))}
POSE_LIFT = 0.2169


def _components():
    return [dict(component=n, rows=IDENTITY_ROWS, offset=offset, intensity=intensity, alpha=1.0,
                 scale=tuple(v + fx.CAPTURED_SPREAD * s for v, s in zip(value, spread)),
                 spin=math.radians(fx.CAPTURED_SPIN[n]))
            for n, value, spread, offset, intensity in COMPONENTS]


def _mesh():
    """A tiny stand-in flame: a triangle along local Z."""
    return dict(positions=[(0.0, 0.0, -0.12), (0.5, 0.0, 0.7), (0.0, 0.0, 1.59)], uv=[(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)],
                triangles=[(0, 1, 2)])


def _preview():
    return dict(family='mech_flame', origin=(0.0, 0.0, 0.0), rows=THRUSTER_ROWS, joint=2, components=_components(),
                mesh=_mesh(), amplitude=0.277088, frame_time=0.0, effect=fx.EFFECT_PATH, locator=fx.LOCATOR,
                texture_paths=dict(fx.TEXTURES))


def _cols(origin, rows):
    """Columns (images of local x, y, z) of a row-vector frame."""
    return [tuple(rows[i][k] for k in range(3)) for i in range(3)]


class TableTests(unittest.TestCase):
    def test_the_mech_models(self):
        for stem in ('enm_nefarious_mech', 'enm_nefarious_mech_damaged', 'enm_nefarious_mech_damaged_dome'):
            self.assertTrue(fx.is_mech('character/enemy/enm_nefarious_mech/%s.model' % stem), stem)
        self.assertFalse(fx.is_mech('character/enemy/enm_nefarious/enm_nefarious.model'))


class ProgramTests(unittest.TestCase):
    def test_flame_is_blue_and_fades_down_the_flame(self):
        rgb, alpha = fx.shade(0.3, 0.0, 0.5, 2.5)
        q = 0.85 ** 4                                   # n = 0.5 makes the noise term 0
        k = 93.0 * fx.AMPLITUDE * 2.5
        for got, want in zip(rgb, (k * q * q, k * q ** 0.75, k * q ** 0.01)):
            self.assertAlmostEqual(got, want, places=9)
        self.assertGreater(rgb[2], rgb[1]); self.assertGreater(rgb[1], rgb[0])
        self.assertAlmostEqual(alpha, q, places=9)
        self.assertIsNone(fx.shade(0.3, 1.0, 0.0, 2.5))   # q = 0.15^4 - 0 at the tip: below the 4/255 discard

    def test_shader_has_the_flame_kind(self):
        self.assertIn('uEffectKind == 10', shading.EFFECT_FRAGMENT)
        self.assertIn('uFlameNoise', shading.EFFECT_UNIFORMS)
        self.assertIn('0.5 * vUV.y - 2.0 * uEffectTime', shading.EFFECT_FRAGMENT)


class TransformTests(unittest.TestCase):
    def test_instances_match_the_capture_at_the_rest_pose(self):
        preview = _preview()
        for component in preview['components']:
            origin, rows = fx.instance_rows(preview, component)
            scale, spin, captured_origin = CAPTURED[component['component']]
            self.assertAlmostEqual(origin[1] + POSE_LIFT, captured_origin[1], places=3)
            cols = _cols(origin, rows)
            for axis, (col, s) in enumerate(zip(cols, scale)):
                self.assertAlmostEqual(math.sqrt(sum(c * c for c in col)), s, places=3)
            self.assertAlmostEqual(cols[2][1] / math.sqrt(sum(c * c for c in cols[2])), -1.0, places=9)   # flame axis down
            angle = math.degrees(math.atan2(cols[0][2], cols[0][0]))                        # local Y of the frame is native +Z
            self.assertAlmostEqual(angle, spin, places=2)

    def test_viewer_triangles(self):
        quads, light = fx.billboards(_preview(), IDENTITY_VIEW)
        self.assertIsNone(light)
        self.assertEqual([q['component'] for q in quads], [2, 3])                          # captured draw order
        for quad, intensity in zip(quads, (2.5, 1.0)):
            self.assertTrue(quad['triangles'])
            self.assertEqual(len(quad['corners']), 3)
            self.assertTrue(quad['depth_test'])
            self.assertFalse(quad['additive'])
            self.assertFalse(quad['cull'])
            self.assertEqual(quad['colour'], (intensity,) * 3)
            self.assertEqual(quad['discard'], 4.0 / 255.0)


class DescriptionTests(unittest.TestCase):
    def test_blender_description(self):
        from rc2016.pipeline import mech_flame_effect as bl
        description = bl.effect_description(_preview())
        self.assertEqual(description['joint'], 2)
        self.assertEqual(len(description['flames']), 2)
        flame = description['flames'][0]
        self.assertEqual(flame['uv'][0], [0.0, 1.0])                                       # Blender's v = 1 - the game's
        self.assertAlmostEqual(flame['emit'][0], 93.0 * 0.277088 * 2.5, places=9)
        # local (0, 0, -0.12) of component 2: 0.15 u below the locator, then -0.12 x scale z along local Z (native -Y)
        self.assertAlmostEqual(flame['vertices'][0][1], 0.15 + 0.12 * (1.5 - 0.25 * fx.CAPTURED_SPREAD), places=6)


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'needs the game archive (RC2016_RESEARCH)')
class GameFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from rc2016.formats.archive import Archive, locate
        cls.temp = tempfile.TemporaryDirectory()
        cls.archive = Archive(locate(os.environ['RC2016_RESEARCH']), Path(cls.temp.name), lambda *a, **k: None)
        cls.archive.verify_repeats = False

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_decoded_components(self):
        components = fx.decode_components(self.archive)
        self.assertEqual([c['component'] for c in components], [2, 3])
        for component, (_n, value, spread, offset, intensity) in zip(components, COMPONENTS):
            self.assertAlmostEqual(component['intensity'], intensity, places=6)
            self.assertAlmostEqual(component['alpha'], 1.0, places=6)
            for got, want in zip(component['offset'], offset):
                self.assertAlmostEqual(got, want, places=6)
            for got, v, s in zip(component['scale'], value, spread):
                self.assertAlmostEqual(got, v + fx.CAPTURED_SPREAD * s, places=3)

    def test_preview_on_the_mech(self):
        from rc2016.formats.native_model import decode_locators
        from rc2016.levels.scene import asset_bytes
        path = 'character/enemy/enm_nefarious_mech/enm_nefarious_mech.model'
        data = asset_bytes(self.archive, path)
        preview = fx.mech_flame_preview(self.archive, path, data, decode_locators(data))
        self.assertIsNotNone(preview)
        self.assertEqual(preview['joint'], 2)
        for got, want in zip(preview['rows'], THRUSTER_ROWS):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(len(preview['mesh']['positions']), 439)
        self.assertEqual(len(preview['mesh']['triangles']), 760)
        tip = max(range(439), key=lambda i: preview['mesh']['positions'][i][2])
        self.assertGreater(preview['mesh']['uv'][tip][1], 0.99)                           # game v = 1 at the z = 1.59 tip


@unittest.skipUnless((CAPTURE / 'eid_8928').is_dir() and os.environ.get('RC2016_RESEARCH'), 'needs the mech capture (Research)')
class CaptureTests(unittest.TestCase):
    def test_vertices_match_the_capture(self):
        """The preview's instance vertices, lifted by the captured pose, equal the captured post-VS vertices taken back to
        the mech's native space (mech instance MVP of the dome draw 8988)."""
        import tempfile
        import numpy as np
        from rc2016.formats.archive import Archive, locate
        from rc2016.formats.native_model import decode_locators
        from rc2016.levels.scene import asset_bytes
        with tempfile.TemporaryDirectory() as temp:
            archive = Archive(locate(os.environ['RC2016_RESEARCH']), Path(temp), lambda *a, **k: None)
            archive.verify_repeats = False
            path = 'character/enemy/enm_nefarious_mech/enm_nefarious_mech.model'
            data = asset_bytes(archive, path)
            preview = fx.mech_flame_preview(archive, path, data, decode_locators(data))
        block = next((CAPTURE / 'eid_8988').glob('buf_b2_*.bin'))
        mvp = np.fromfile(block, np.float32).reshape(17, 4).astype(float)[0:4].T
        for index, component in enumerate(preview['components']):
            post = np.fromfile(CAPTURE / 'eid_8928' / ('postvs_i%d.bin' % index), np.float32).reshape(-1, 16)[:439, :4]
            native = (np.linalg.inv(mvp) @ post.astype(float).T).T
            native = native[:, :3] / native[:, 3:]
            origin, rows = fx.instance_rows(preview, component)
            local = np.array(preview['mesh']['positions'])
            ours = np.array(origin) + local @ np.array(rows) + np.array([0.0, POSE_LIFT, 0.0])
            self.assertLess(np.abs(ours - native).max(), 2e-3, component['component'])
            uv = np.fromfile(CAPTURE / 'eid_8928' / ('postvs_i%d.bin' % index), np.float32).reshape(-1, 16)[:439, 4:6]
            self.assertLess(np.abs(np.array(preview['mesh']['uv']) - uv).max(), 1e-6)


if __name__ == '__main__':
    unittest.main()
