"""The read-only shared-lighting audit honours an explicitly selected config without hiding drift."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rc2016.pipeline import scene_defaults


class FakeSun(dict):
    """A review HDRI sun lamp as check_shared_lighting reads it."""
    type = 'LIGHT'
    hide_render = False

    def __init__(self, cap, visible_glossy, energy=8.6):
        super().__init__(lighting_role=scene_defaults.REVIEW_SUN_ROLE, hdri_sun=json.dumps(dict(cap=cap, energy=energy)))
        self.visible_glossy = visible_glossy
        self.data = SimpleNamespace(type='SUN', energy=energy)


class SharedLightingConfigAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'light.exr').write_bytes(b'fixture')
        self.config = root / 'lighting.json'
        source = json.loads(scene_defaults.SHARED_LIGHTING_FILE.read_text(encoding='utf-8'))
        source['hdri'] = 'light.exr'
        self.config.write_text(json.dumps(source), encoding='utf-8')
        self.selected = scene_defaults.load_shared_lighting(self.config)

    def test_selected_config_survives_checkout_path_change_and_explicit_hdri_override(self):
        record = dict(self.selected)
        self.assertFalse(scene_defaults._lighting_file_differs(record, self.selected))
        self.assertTrue(scene_defaults._lighting_file_differs(record))  # no selection still checks this checkout
        override = Path(self.temp.name) / 'override.exr'
        override.write_bytes(b'override')
        selected = dict(self.selected, hdri_path=str(override))
        self.assertFalse(scene_defaults._lighting_file_differs(dict(selected), selected))
        record['world_strength'] = float(record['world_strength']) + 1
        self.assertTrue(scene_defaults._lighting_file_differs(record, self.selected))
        record = dict(selected, config_sha256='wrong')
        self.assertTrue(scene_defaults._lighting_file_differs(record, selected))

    @staticmethod
    def fake_scene(selected, view=None, compositing=None, compositor=None):
        """(scene, world strength socket) with the selected lighting's world; ``view`` overrides the view settings."""
        class Image:
            filepath = selected['hdri_path']
            size = (4, 2)
            packed_file = None
            def get(self, key):
                return self.filepath if key == 'shared_external_hdri_path' else None
        image = Image()
        env = SimpleNamespace(type='TEX_ENVIRONMENT', image=image, name='PS4 Golden Gate HDRI')
        strength = SimpleNamespace(default_value=float(selected['world_strength']))
        objects, colour_source = [], env
        sun = selected.get('hdri_sun')
        if sun:   # the HDRI sun split: Environment -> sun cap -> Background, plus the review sun lamp
            cap = float(sun['cap'])
            colour_source = SimpleNamespace(name=scene_defaults.SUN_CAP_NODE, operation='MINIMUM',
                inputs=[SimpleNamespace(links=[SimpleNamespace(from_node=env)]), SimpleNamespace(default_value=(cap, cap, cap))])
            objects.append(FakeSun(cap, bool(sun.get('visible_glossy', False))))
        bg = SimpleNamespace(type='BACKGROUND', inputs={'Strength': strength,
            'Color': SimpleNamespace(links=[SimpleNamespace(from_node=colour_source)])})
        output = SimpleNamespace(type='OUTPUT_WORLD', is_active_output=True,
            inputs={'Surface': SimpleNamespace(links=[SimpleNamespace(from_node=bg)])})
        rotation = SimpleNamespace(default_value=(math.radians(selected['hdri_rotation_x_degrees']), 0,
                                                   math.radians(selected['hdri_rotation_z_degrees'])))
        class Nodes(list):
            def get(self, key):
                return SimpleNamespace(inputs={'Rotation': rotation}) if key == 'PS4 HDRI Rotation' else None
        scene = SimpleNamespace(world=SimpleNamespace(node_tree=SimpleNamespace(nodes=Nodes([output]))),
            view_settings=SimpleNamespace(**(view or dict(view_transform=selected['view_transform'], look=selected['look'],
                exposure=float(selected['exposure']), gamma=float(selected['gamma'])))),
            render=SimpleNamespace(use_compositing=selected['use_compositing'] if compositing is None else compositing),
            objects=objects, compositing_node_group=compositor)
        return scene, strength

    def test_selected_config_checks_actual_scene_values_not_only_record(self):
        selected = self.selected
        scene, strength = self.fake_scene(selected)
        fake_bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda path: path))
        with patch.dict(sys.modules, {'bpy': fake_bpy}):
            self.assertEqual(scene_defaults.check_shared_lighting(scene, selected), [])
            strength.default_value += 0.5
            self.assertIn('lighting.world_strength', scene_defaults.check_shared_lighting(scene, selected))

    def test_hdri_sun_split_is_audited(self):
        self.assertEqual(self.selected['hdri_sun'], {'cap': 4.0, 'visible_glossy': False})
        fake_bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda path: path))
        with patch.dict(sys.modules, {'bpy': fake_bpy}):
            scene, _ = self.fake_scene(self.selected)
            self.assertEqual(scene_defaults.check_shared_lighting(scene, self.selected), [])
            scene.objects[0].visible_glossy = True                     # sun spots back
            self.assertIn('lighting.hdri_sun', scene_defaults.check_shared_lighting(scene, self.selected))
            scene, _ = self.fake_scene(self.selected)
            scene.objects.clear()                                      # cap without its lamp: the sun's light is lost
            self.assertIn('lighting.hdri_sun', scene_defaults.check_shared_lighting(scene, self.selected))
            plain = {k: v for k, v in self.selected.items() if k != 'hdri_sun'}
            scene, _ = self.fake_scene(self.selected)                  # split scene against a config without it
            self.assertIn('lighting.hdri_sun', scene_defaults.check_shared_lighting(scene, plain))
            scene, _ = self.fake_scene(plain)
            self.assertEqual(scene_defaults.check_shared_lighting(scene, plain), [])
        self.assertTrue(scene_defaults._lighting_file_differs(plain, self.selected))

    def test_hdri_sun_parameters_integrate_the_pixels_above_the_cap(self):
        width, height, cap = 64, 32, 4.0
        pixels = [0.5] * (width * height * 4)
        x, y = 10, 20                                                   # one hot pixel, bottom row first
        for c, value in enumerate((104.0, 54.0, 4.0)):
            pixels[(y * width + x) * 4 + c] = value
        got = scene_defaults.hdri_sun_parameters(pixels, width, height, cap, 1.5, 0.0, 0.0)
        theta = ((y + 0.5) / height - 0.5) * math.pi
        phi = ((x + 0.5) / width - 0.5) * 2 * math.pi
        d_omega = (2 * math.pi / width) * (math.pi / height) * math.cos(theta)
        self.assertAlmostEqual(got['energy'], 100.0 * d_omega * 1.5, places=9)
        self.assertEqual([round(c, 9) for c in got['color']], [1.0, 0.5, 0.0])
        want = (math.cos(theta) * math.cos(phi), -math.cos(theta) * math.sin(phi), math.sin(theta))
        # Blender's equirectangular lookup of that direction lands back on the pixel
        u = -math.atan2(want[1], want[0]) / (2 * math.pi) + 0.5
        v = math.atan2(want[2], math.hypot(want[0], want[1])) / math.pi + 0.5
        self.assertEqual((int(u * width), int(v * height)), (x, y))
        for a, b in zip(got['direction'], want):
            self.assertAlmostEqual(a, b, places=9)
        self.assertEqual(got['pixels'], 1)
        self.assertAlmostEqual(got['angle'], math.radians(0.53))
        # the PS4 HDRI Rotation (image = Rz(z) Rx(x) world) is undone
        turned = scene_defaults.hdri_sun_parameters(pixels, width, height, cap, 1.5, 0.0, math.radians(90))
        self.assertAlmostEqual(turned['direction'][0], want[1], places=9)
        self.assertAlmostEqual(turned['direction'][1], -want[0], places=9)
        with self.assertRaises(ValueError):
            scene_defaults.hdri_sun_parameters([0.5] * (width * height * 4), width, height, cap, 1.5, 0.0, 0.0)

    def test_game_tonemap_checks_the_compositor_in_place_of_the_view_transform(self):
        selected = dict(self.selected, game_tonemap=True)
        stops = scene_defaults.game_tonemap_stops(selected)
        self.assertAlmostEqual(stops, math.log2(4.553783893585205) + float(self.selected['exposure']), places=6)

        def compositor(grade='identity', exposure=stops):
            node = SimpleNamespace(node_tree={'rc_grade': grade},
                                   inputs={'Exposure (stops)': SimpleNamespace(default_value=exposure)})
            return SimpleNamespace(nodes={scene_defaults.GAME_TONEMAP_GROUP: node})
        raw = dict(view_transform='Raw', look='None', exposure=0.0, gamma=1.0)
        fake_bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda path: path))
        with patch.dict(sys.modules, {'bpy': fake_bpy}):
            scene, _ = self.fake_scene(selected, raw, True, compositor())
            self.assertEqual(scene_defaults.check_shared_lighting(scene, selected), [])
            for scene in (self.fake_scene(selected)[0],                                   # Khronos PBR Neutral
                          self.fake_scene(selected, raw, True, compositor('captured'))[0],  # Veldin's grade
                          self.fake_scene(selected, raw, True, compositor(exposure=stops + 1))[0],
                          self.fake_scene(selected, raw, False, compositor())[0]):
                self.assertIn('colour_management.game_tonemap', scene_defaults.check_shared_lighting(scene, selected))
            # a scene with the game tonemap fails the plain shared view
            scene, _ = self.fake_scene(self.selected, raw, True, compositor())
            self.assertIn('colour_management.shared_view', scene_defaults.check_shared_lighting(scene, self.selected))

    def test_game_tonemap_choice_is_part_of_the_selection_not_of_the_file(self):
        record = dict(self.selected, game_tonemap=True)
        self.assertTrue(scene_defaults._lighting_file_differs(record, self.selected))
        self.assertFalse(scene_defaults._lighting_file_differs(record, dict(record)))
        with patch.object(scene_defaults, 'SHARED_LIGHTING_FILE', self.config):
            self.assertFalse(scene_defaults._lighting_file_differs(dict(scene_defaults.load_shared_lighting(), game_tonemap=True)))

    def test_selected_config_edit_and_default_file_edit_both_report_drift(self):
        record = dict(self.selected)
        data = json.loads(self.config.read_text(encoding='utf-8'))
        data['world_strength'] = float(data['world_strength']) + 1
        self.config.write_text(json.dumps(data), encoding='utf-8')
        self.assertTrue(scene_defaults._lighting_file_differs(record, self.selected))
        with patch.object(scene_defaults, 'SHARED_LIGHTING_FILE', self.config):
            old_default = scene_defaults.load_shared_lighting()
            data['world_strength'] += 1
            self.config.write_text(json.dumps(data), encoding='utf-8')
            self.assertTrue(scene_defaults._lighting_file_differs(old_default))


if __name__ == '__main__':
    unittest.main()
