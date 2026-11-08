"""Holocon device holograms (rc2016/viewer/holocon_effect.py, rc2016/pipeline/holocon_effect.py): placement, still-frame rays,
the viewer quads, the shader kinds, the decoded effects (game files) and the icon equation against the vendor capture (Research)."""
import json
import math
import os
import unittest
from pathlib import Path

from rc2016.viewer import holocon_effect as fx
from rc2016.viewer import shading
from rc2016.viewer.idle_effect import GLOW_SOFT

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / 'Research/captures/2026-11-08_holocon-hologram'
IDENTITY = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]


def _preview(origin=(0.0, 1.179, 0.0), rows=IDENTITY, axis_rows=((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))):
    """A vendor-shaped preview without game files."""
    return dict(family='holocon', origin=origin, rows=[tuple(r) for r in rows],
                icon=dict(component=1, offset=(0.0, 1.0, 0.0), size=(2.0, 2.0), alpha=1.0),
                glow=dict(component=3, offset=(0.0, 0.0, 0.0), size=(1.5084, 1.5084), colour=(3.23, 13.85, 20.0), alpha=1.0,
                          discard=1.0 / 255.0),
                ray_emitter=dict(component=2, axis_rows=[tuple(r) for r in axis_rows]), rays=fx.rays(),
                light=dict(offset=(0.0, 1.0, 0.0), hue=(0.1779, 0.624, 1.0), colour=(0.02, 0.07, 0.112), radius=5.0, unit_scale=1.0))


IDENTITY_VIEW = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -10.0, 1.0]   # 10 u in front


class DeviceTableTests(unittest.TestCase):
    def test_devices_map_to_effects_and_locators(self):
        self.assertEqual(fx.device_effect('environment/global/gameplay/gbl_gp_weapon_vendor_01/weapon_vendor_01.model'),
                         ('vendor', fx.EFFECTS['vendor'], 'igLoc_holo_emit'))
        self.assertEqual(fx.device_effect('x/gbl_gp_teleport_pad_01.model')[2], 'igLoc_AttachPosition')
        self.assertIsNone(fx.device_effect('x/gbl_gp_crate_health_01.model'))
        for key, _locator in fx.DEVICES.values():
            self.assertIn(key, fx.EFFECTS)

    def test_locator_frame_goes_through_the_joint(self):
        locator = dict(position=(0.0, 0.0, 1.0), rotation=[[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]])
        joint = [[1.0, 0.0, 0.0, 2.0], [0.0, 1.0, 0.0, 3.0], [0.0, 0.0, 1.0, 4.0], [0.0, 0.0, 0.0, 1.0]]
        position, rows = fx.locator_frame(locator, joint)
        self.assertEqual(position, (2.0, 3.0, 5.0))
        self.assertEqual(rows[2], (0.0, 0.0, -1.0))
        preview = dict(origin=position, rows=rows)
        self.assertEqual(fx.effect_point(preview, (0.0, 0.0, 0.75)), (2.0, 3.0, 4.25))   # the Trespasser's icon offset


class RayTests(unittest.TestCase):
    def test_still_frame_rays_follow_rate_life_and_alpha(self):
        rays = fx.rays()
        self.assertEqual(rays, fx.rays())                          # seeded
        self.assertTrue(8 <= len(rays) <= 11)                     # 10 per second, about 1 s each (the capture had 9 live)
        self.assertEqual([r['t'] for r in rays], sorted((r['t'] for r in rays), reverse=True))   # oldest first
        for r in rays:
            self.assertTrue(0.0 <= r['alpha'] <= fx.RAY_ALPHA_MAX)
            self.assertTrue(math.radians(9.0) <= r['tilt'] <= math.radians(19.0))
            self.assertIn(r['laser'], (1, 2, 3))
            self.assertAlmostEqual(r['colour'][2], 10.0)
            self.assertLess(abs(r['colour'][1] - r['colour'][0] - 4.932), 0.02)   # one random for R and G (captured)

    def test_rays_leave_along_the_emitter_axis(self):
        preview = _preview()
        self.assertEqual(tuple(round(v, 6) for v in fx.emitter_axes(preview)[2]), (0.0, 1.0, 0.0))   # the vendor's axis is up
        for ray in preview['rays']:
            apex, direction = fx.ray_geometry(preview, ray)
            self.assertLess(math.dist(apex, preview['origin']), fx.RAY_APEX_RADIUS + 1e-9)
            self.assertAlmostEqual(math.degrees(math.acos(direction[1])), math.degrees(ray['tilt']), places=6)


class BillboardTests(unittest.TestCase):
    def test_quads_in_captured_order_with_flipped_icon_v(self):
        preview = _preview()
        quads, light = fx.billboards(preview, IDENTITY_VIEW)
        kinds = [q['kind'] for q in quads]
        self.assertEqual(kinds[0], fx.HOLO_ICON)
        self.assertEqual(kinds[-1], GLOW_SOFT)
        self.assertEqual(set(kinds[1:-1]), {fx.HOLO_RAYS})
        icon = quads[0]
        top = max(icon['corners'], key=lambda c: c[0][1])
        self.assertEqual(top[1][1], 0.0)                           # v = 0 at the top, as captured
        width = max(c[0][0] for c in icon['corners']) - min(c[0][0] for c in icon['corners'])
        self.assertAlmostEqual(width, 2.0)
        self.assertTrue(icon['cull'])
        self.assertAlmostEqual(icon['discard'], 4.0 / 255.0)
        self.assertIsNotNone(light)

    def test_fixed_icon_holds_the_effect_frame(self):
        preview = _preview(rows=[(-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)])   # the Trespasser's locator turn
        preview['icon'] = dict(component=2, offset=(0.0, 0.0, 0.75), size=(1.0, 1.0), alpha=1.0, fixed=True)
        quads, _light = fx.billboards(preview, IDENTITY_VIEW)
        corners = [c[0] for c in quads[0]['corners']]
        ys = {round(c[1], 6) for c in corners}                     # native y is display z = view z here
        self.assertEqual(len({round(c[2], 6) for c in corners}), 2)   # not in a plane facing the view
        self.assertEqual(len(ys), 1)
        # first corner (u 0, v 1) is at local (-x, -y): native x +0.5 after the turn
        self.assertAlmostEqual(corners[0][0], 0.5)

    def test_rays_are_axial_billboards(self):
        quads, _light = fx.billboards(_preview(), IDENTITY_VIEW)
        for q in (q for q in quads if q['kind'] == fx.HOLO_RAYS):
            (a, _), (b, _), (c, _), _d = q['corners']
            side = [y - x for x, y in zip(a, b)]
            along = [y - x for x, y in zip(b, c)]
            mid = [(x + y) / 2 for x, y in zip(a, b)]
            self.assertAlmostEqual(sum(s * v for s, v in zip(side, along)), 0.0, places=6)
            self.assertAlmostEqual(sum(s * m for s, m in zip(side, mid)) / math.dist(mid, (0, 0, 0)), 0.0, places=6)
            self.assertTrue(q['laser'].startswith('laser'))

    def test_fade_beyond_the_effect_distance(self):
        far = list(IDENTITY_VIEW)
        far[14] = -200.0
        self.assertEqual(fx.billboards(_preview(), far), ([], None))
        self.assertEqual(fx.distance_fade(140.0), 0.4)


class ShaderTests(unittest.TestCase):
    def test_effect_program_has_the_holocon_kinds(self):
        self.assertIn('uEffectKind == 5', shading.EFFECT_FRAGMENT)
        self.assertIn('uEffectKind == 6', shading.EFFECT_FRAGMENT)
        for name in ('uHoloIcon', 'uHoloBackground', 'uHoloScan', 'uHoloTrigger', 'uHoloLaser', 'uHoloOffsetsRB',
                     'uHoloOffsetG', 'uHoloHue', 'uHoloFrame'):
            self.assertIn(name, shading.EFFECT_UNIFORMS)
            self.assertIn(name, shading.EFFECT_FRAGMENT)


def _archive():
    from rc2016.levels.scene import open_archive
    return open_archive(os.environ['RC2016_RESEARCH'])


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH (game archive folder) not set')
class GameFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = _archive()

    def test_vendor_effect_components(self):
        parts = fx.decode_holocon(self.archive, fx.EFFECTS['vendor'])
        self.assertAlmostEqual(parts['icon']['size'][0], 2.0, places=6)
        self.assertEqual(tuple(round(v, 6) for v in parts['icon']['offset']), (0.0, 1.0, 0.0))
        self.assertEqual([round(c, 2) for c in parts['glow']['colour']], [3.23, 13.85, 20.0])
        self.assertEqual([tuple(round(v, 6) for v in row) for row in parts['rays']['axis_rows']][2], (0.0, 1.0, -0.0))
        self.assertAlmostEqual(parts['light']['b4'], 5.0, places=6)

    def test_hydrodisplacer_icon_is_in_front_and_rays_aim_at_it(self):
        parts = fx.decode_holocon(self.archive, fx.EFFECTS['hydrodisplacer'])
        self.assertEqual(tuple(round(v, 6) for v in parts['icon']['offset']), (0.0, 0.0, 0.75))
        self.assertTrue(parts['icon']['fixed'])
        self.assertTrue(fx.decode_holocon(self.archive, fx.EFFECTS['trespasser'])['icon']['fixed'])
        for key in ('vendor', 'teleporter', 'turret', 'jetpack', 'hologuise', 'bolt_crank'):
            self.assertFalse(fx.decode_holocon(self.archive, fx.EFFECTS[key])['icon']['fixed'], key)
        self.assertEqual([tuple(round(v, 6) for v in row) for row in parts['rays']['axis_rows']][2], (0.0, 0.0, 1.0))
        self.assertIsNone(parts['glow'])

    def test_lockout_icon_is_red_and_the_others_cyan(self):
        values = fx._material_values(self.archive, 'material/visualeffect/global/gbl_icon_hologram_lockout/gbl_icon_hologram_lockout.material')
        self.assertEqual(values['hue'], 1.0)
        vendor = fx._material_values(self.archive, 'material/visualeffect/global/gbl_icon_hologram_vendor/gbl_icon_hologram_vendor.material')
        self.assertEqual(vendor['hue'], 0.0)
        self.assertEqual([round(v, 3) for v in vendor['offset_r'] + vendor['offset_b'] + vendor['offset_g']], [0.01, 0.01, 0.0, 0.03, -0.01, -0.01])

    def test_vendor_preview_and_blender_description(self):
        from rc2016.formats.native_model import decode_locators
        from rc2016.levels.scene import asset_bytes
        from rc2016.pipeline import holocon_effect
        path = 'environment/global/gameplay/gbl_gp_weapon_vendor_01/weapon_vendor_01.model'
        data = asset_bytes(self.archive, path)
        preview = fx.holocon_preview(self.archive, path, data, decode_locators(data))
        self.assertEqual(preview['device'], 'vendor')
        self.assertAlmostEqual(preview['origin'][1], 1.179, places=3)
        self.assertTrue(preview['texture_paths']['icon'].endswith('icon_holograms_vendor.texture'))
        description = holocon_effect.effect_description(self.archive, preview)
        json.dumps(description)
        self.assertEqual(description['icon']['trigger'], 0.0)      # the presentation seed lands on a 0 texel
        self.assertEqual([q['kind'] for q in description['quads']][0], 'holo_icon')
        self.assertIsNone(description['light'])


@unittest.skipUnless((CAPTURE / 'pixels/hdr_after_9761.bin').exists() and (CAPTURE / 'tex_png').exists(),
                     'Research holocon capture not present')
class CaptureTests(unittest.TestCase):
    def test_icon_equation_matches_the_captured_draw(self):
        import sys
        import numpy as np
        cwd = os.getcwd()
        sys.path.insert(0, str(CAPTURE))
        os.chdir(CAPTURE)   # the check script reads its images and constants relative to its folder on import
        try:
            import verify_icon as v
            from hdr import load
            before, after = load('pixels/hdr_after_9755.bin'), load('pixels/hdr_after_9761.bin')
            x0, x1 = (1.6692 / 8.4394 + 1) * 960, (4.2337 / 8.4394 + 1) * 960
            y0, y1 = (1 - 5.2372 / 8.4394) * 540, (1 - 0.6781 / 8.4394) * 540
            ys, xs = np.mgrid[int(y0) + 1:int(y1), int(x0) + 1:int(x1)]
            rgb, a, keep, _f = v.icon((xs + 0.5 - x0) / (x1 - x0), (ys + 0.5 - y0) / (y1 - y0))
        finally:
            os.chdir(cwd)
            sys.path.pop(0)
        pred = np.where(keep[..., None], rgb * a[..., None] + before[ys, xs] * (1 - a[..., None]), before[ys, xs])
        got = after[ys, xs]
        changed = np.abs(got - before[ys, xs]).sum(-1) > 0.02
        for channel in range(3):
            self.assertGreater(np.corrcoef(pred[changed][:, channel], got[changed][:, channel])[0, 1], 0.9999)


if __name__ == '__main__':
    unittest.main()
