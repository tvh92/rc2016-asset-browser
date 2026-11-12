"""Holocard glow (rc2016/viewer/holocard_effect.py, rc2016/pipeline/holocard_effect.py): the card table, the glow quad's
corners and normals, the viewer quads, the shader kinds, the decoded effect and card locators (game files) and the quad
geometry against the holocard capture (Research)."""
import json
import math
import os
import unittest
from pathlib import Path

from rc2016.viewer import holocard_effect as fx
from rc2016.viewer import shading

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / 'Research/captures/2026-11-12_holocard-glow'
TILT = math.radians(15.0)
# The card locator's rotation rows (all three cards): local Z = native +X (the card face), the card tilted 15 degrees.
CARD_ROWS = [(0.0, -math.sin(TILT), -math.cos(TILT)), (0.0, math.cos(TILT), -math.sin(TILT)), (1.0, 0.0, 0.0)]
IDENTITY_VIEW = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -5.0, 1.0]   # 5 u in front


def _preview(rows=CARD_ROWS):
    """A single-card-shaped preview without game files."""
    colour = (0.0, 2.4846644, 5.0)
    return dict(family='holocard', origin=(0.0, 1.195, 0.0), rows=[tuple(r) for r in rows],
                glow=dict(component=2, offset=(0.0, 0.0, 0.0), size=1.8316, colour=colour, alpha=1.0, fixed=True, pull=0.2),
                clouds=[dict(component=1, size=s, alpha=a, angle=math.radians(g), colour=colour, pull=0.2) for s, a, g in fx.CLOUDS],
                sparkles=[dict(component=3, offset=o, size=s, alpha=1.0, colour=colour) for o, s in fx.SPARKLES],
                amplitude=0.277088, frame_time=0.0, effect=fx.EFFECT_PATH, locator=fx.LOCATOR,
                texture_paths=dict(fx.TEXTURES))


def _angle(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))))


class CardTableTests(unittest.TestCase):
    def test_the_three_cards_are_holocards(self):
        for stem in ('pup_card_single', 'pup_card_ryno', 'pup_card_pack'):
            self.assertTrue(fx.is_holocard('equipment/pickup/x/%s.model' % stem), stem)
        self.assertFalse(fx.is_holocard('equipment/pickup/pup_bolt_gold_01/pup_bolt_gold_01.model'))


class GeometryTests(unittest.TestCase):
    def test_corner_normals_lean_45_degrees_outward(self):
        corners = fx.glow_corners(_preview())
        self.assertEqual([uv for _p, uv, _n in corners], [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)])   # v = 0 at the top
        for (x, y, _z), _uv, normal in corners:
            self.assertAlmostEqual(_angle(normal, (0.0, 0.0, 1.0)), 45.0, places=6)
            self.assertGreater(normal[0] * x + normal[1] * y, 0.0)                   # outward
            self.assertAlmostEqual(abs(x), 1.8316 / 2, places=6)

    def test_billboards_in_captured_order_and_additive(self):
        quads, light = fx.billboards(_preview(), IDENTITY_VIEW)
        self.assertIsNone(light)
        kinds = [q['kind'] for q in quads]
        self.assertEqual(kinds, [fx.HOLOCARD_GLOW] + [fx.ETHEREAL_CLOUD] * 2 + [fx.SPARKLE] * len(fx.SPARKLES))
        for quad in quads:
            self.assertTrue(quad['additive'])
            self.assertFalse(quad['depth_test'])     # the game's card writes no depth (captured)
            self.assertEqual(quad['discard'], 4.0 / 255.0)
        glow = quads[0]
        self.assertEqual(len(glow['normals']), 4)
        # The card face (native +X) is the display +x: the face normal, the quad's plane and the leaning normals follow it.
        self.assertEqual(tuple(round(v, 6) for v in glow['face']), (1.0, 0.0, 0.0))
        self.assertTrue(all(abs(p[0]) < 1e-6 for p, _uv in glow['corners']))
        self.assertTrue(all(abs(_angle(n, glow['face']) - 45.0) < 1e-6 for n in glow['normals']))
        self.assertEqual([round(q['alpha'], 4) for q in quads[1:3]], [round(23 / 255, 4), round(248 / 255, 4)])
        # +0x7C: the glow quad and the cloud centres move 0.2 u toward the camera, the sparkles stay
        centre = [sum(p[k] for p, _uv in glow['corners']) / 4 for k in range(3)]
        self.assertAlmostEqual(math.dist(centre, (0.0, 0.0, 0.0)), 5.0 - 1.195 - 0.2, places=6)   # native up is display z
        cloud = [sum(p[k] for p, _uv in quads[1]['corners']) / 4 for k in range(3)]
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(cloud, centre)))

    def test_fade_beyond_the_effect_distance(self):
        far = list(IDENTITY_VIEW)
        far[14] = -80.0
        self.assertEqual(fx.billboards(_preview(), far), ([], None))
        self.assertEqual(fx.distance_fade(50.0), 0.4)


class ShaderTests(unittest.TestCase):
    def test_effect_program_has_the_holocard_kinds(self):
        for kind in (7, 8, 9):
            self.assertIn('uEffectKind == %d' % kind, shading.EFFECT_FRAGMENT)
        for name in ('uCardGlow', 'uCardNoise', 'uCardSmoke', 'uCardSparkle', 'uEffectTime', 'uEffectFace'):
            self.assertIn(name, shading.EFFECT_UNIFORMS)
            self.assertIn(name, shading.EFFECT_FRAGMENT)
        self.assertIn('vN = gl_Normal', shading.EFFECT_VERTEX)


def _archive():
    from rc2016.levels.scene import open_archive
    return open_archive(os.environ['RC2016_RESEARCH'])


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH (game archive folder) not set')
class GameFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = _archive()

    def test_glow_component(self):
        glow = fx.decode_glow(self.archive)
        self.assertEqual(glow['component'], 2)
        self.assertAlmostEqual(glow['size'], 1.8316, places=4)
        self.assertEqual([round(c, 4) for c in glow['colour']], [0.0, 2.4847, 5.0])
        self.assertTrue(glow['fixed'])
        self.assertEqual([round(v, 6) for v in glow['pulls']], [-0.2, 0.2, 0.2, -0.0])   # +0x7C = 0.2, -0.2, -0.2, 0

    def test_actors_and_conduit_name_the_effect(self):
        from rc2016.levels.scene import asset_bytes
        for actor in fx.CARDS.values():
            self.assertIn(b'pup_holocard_vfx', asset_bytes(self.archive, actor).lower())
        conduit = asset_bytes(self.archive, fx.CONDUIT_PATH)
        self.assertIn(b'pup_holocard_glow.visualeffect', conduit.lower())
        self.assertIn(fx.LOCATOR.encode(), conduit)

    def test_every_card_preview_and_blender_description(self):
        from rc2016.formats.native_model import decode_locators
        from rc2016.levels.scene import asset_bytes
        from rc2016.pipeline import holocard_effect
        for stem, folder in (('pup_card_single', 'pup_card_single'), ('pup_card_ryno', 'pup_card_single'), ('pup_card_pack', 'pup_card_pack')):
            path = 'equipment/pickup/%s/%s.model' % (folder, stem)
            data = asset_bytes(self.archive, path)
            preview = fx.holocard_preview(self.archive, path, data, decode_locators(data))
            self.assertIsNotNone(preview, stem)
            self.assertAlmostEqual(preview['origin'][1], 1.195, places=3)   # joint 0: the card centre
            self.assertEqual(tuple(round(v, 6) for v in preview['rows'][2]), (1.0, 0.0, 0.0))
            description = holocard_effect.effect_description(preview)
            json.dumps(description)
            self.assertEqual([q['kind'] for q in description['quads']][:3], ['holocard_glow', 'ethereal', 'ethereal'])
            self.assertGreater(holocard_effect.required_transparent_bounces(description), len(description['quads']))


@unittest.skipUnless((CAPTURE / 'eid_2622/postvs_i0.bin').exists(), 'Research holocard capture not present')
class CaptureTests(unittest.TestCase):
    def test_captured_quad_has_the_decoded_size_and_corner_normals(self):
        import numpy as np
        v = np.frombuffer((CAPTURE / 'eid_2622/postvs_i0.bin').read_bytes(), '<f4').reshape(-1, 20)[[0, 1, 2, 5]]
        cb = np.frombuffer(next((CAPTURE / 'eid_2622').glob('buf_b0_*.bin')).read_bytes(), '<f4')
        w = v[:, 3]
        px, py = (v[:, 0] / w + 1) * 960, (1 - v[:, 1] / w) * 540
        world = np.stack([(cb[92 + k] + cb[100 + k] * py + px * (cb[96 + k] + cb[104 + k] * py)) * w for k in range(3)], -1)
        self.assertAlmostEqual(float(np.linalg.norm(world[2] - world[0])), 1.8316, places=2)
        normals = v[:, 8:11] / np.linalg.norm(v[:, 8:11], axis=1, keepdims=True)
        face = normals.sum(0) / np.linalg.norm(normals.sum(0))
        centre = world.mean(0)
        for corner, normal in zip(world, normals):
            self.assertAlmostEqual(_angle(normal, face), 45.0, places=3)
            lean = normal - face * float(normal @ face)
            out = corner - centre
            self.assertAlmostEqual(_angle(lean / np.linalg.norm(lean), out / np.linalg.norm(out)), 0.0, delta=0.1)


if __name__ == '__main__':
    unittest.main()
