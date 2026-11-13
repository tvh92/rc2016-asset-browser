"""Flags bit 0x80 marks a shadow-cast-only material, which no colour pass draws."""
import gzip
import json
import unittest
from pathlib import Path

from rc2016.pipeline import render_state

ROOT = Path(__file__).resolve().parents[1]
SHADOW_CAST_ONLY = 'material/global/shadow_cast_only/shadow_cast_only.material'


class ShadowOnlyFlagTests(unittest.TestCase):
    def test_only_the_shadow_cast_only_material_sets_the_bit(self):
        with gzip.open(render_state.CENSUS, 'rt', encoding='utf-8') as fh:
            flags = json.load(fh)['flags']
        marked = sorted(path.lower() for path, word in flags.items() if int(word, 16) & render_state.SHADOW_ONLY_FLAG)
        self.assertEqual(marked, [SHADOW_CAST_ONLY])

    def test_decode_and_lookup(self):
        self.assertTrue(render_state.decode_flags(0xB0)['shadow_only'])
        self.assertFalse(render_state.decode_flags(0x230)['shadow_only'])
        self.assertTrue(render_state.shadow_only(SHADOW_CAST_ONLY))
        self.assertFalse(render_state.shadow_only('material/character/hero/hero_ratchet_body/hero_ratchet_body.material'))
        self.assertFalse(render_state.shadow_only(None))

    def test_every_front_end_skips_the_colour_pass(self):
        model = (ROOT / 'rc2016/viewer/viewport.py').read_text(encoding='utf-8')
        self.assertIn('"shadow_only", "zurkon"', model)
        self.assertIn('.get("shadow_only")]', model)
        level = (ROOT / 'rc2016/levels/viewport.py').read_text(encoding='utf-8')
        self.assertIn('if not shadow and entry.get("shadow_only"):', level)
        geometry = (ROOT / 'rc2016/pipeline/geometry.py').read_text(encoding='utf-8')
        self.assertIn('render_state.shadow_only(material_path)', geometry)
        self.assertIn('obj.visible_shadow=True', geometry)
        export = (ROOT / 'rc2016/export/models.py').read_text(encoding='utf-8')
        self.assertIn('render_state.shadow_only(', export)


if __name__ == '__main__':
    unittest.main()
