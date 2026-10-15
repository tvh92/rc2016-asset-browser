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

    def test_selected_config_checks_actual_scene_values_not_only_record(self):
        selected = self.selected
        class Image:
            filepath = selected['hdri_path']
            size = (4, 2)
            packed_file = None
            def get(self, key):
                return self.filepath if key == 'shared_external_hdri_path' else None
        image = Image()
        env = SimpleNamespace(type='TEX_ENVIRONMENT', image=image)
        strength = SimpleNamespace(default_value=float(selected['world_strength']))
        bg = SimpleNamespace(type='BACKGROUND', inputs={'Strength': strength,
            'Color': SimpleNamespace(links=[SimpleNamespace(from_node=env)])})
        output = SimpleNamespace(type='OUTPUT_WORLD', is_active_output=True,
            inputs={'Surface': SimpleNamespace(links=[SimpleNamespace(from_node=bg)])})
        rotation = SimpleNamespace(default_value=(0, 0, math.radians(selected['hdri_rotation_z_degrees'])))
        class Nodes(list):
            def get(self, key):
                return SimpleNamespace(inputs={'Rotation': rotation}) if key == 'PS4 HDRI Rotation' else None
        scene = SimpleNamespace(world=SimpleNamespace(node_tree=SimpleNamespace(nodes=Nodes([output]))),
            view_settings=SimpleNamespace(view_transform=selected['view_transform'], look=selected['look'],
                exposure=float(selected['exposure']), gamma=float(selected['gamma'])),
            render=SimpleNamespace(use_compositing=selected['use_compositing']), objects=[])
        fake_bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda path: path))
        with patch.dict(sys.modules, {'bpy': fake_bpy}):
            self.assertEqual(scene_defaults.check_shared_lighting(scene, selected), [])
            strength.default_value += 0.5
            self.assertIn('lighting.world_strength', scene_defaults.check_shared_lighting(scene, selected))

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
