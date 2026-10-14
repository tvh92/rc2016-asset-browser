"""Pure helpers of the character audit (idle suggestion, material decode categories)."""
import unittest

from rc2016.research.npc_audit import category, suggested_idle


class AuditHelperTests(unittest.TestCase):
    def test_suggested_idle_prefers_the_plain_loop(self):
        names = ['Stand_Idle_Fidget_npc_x', 'aim_idle_x', 'Stand_Idle_npc_x', 'Walk_x', None]
        self.assertEqual(suggested_idle(names), 'Stand_Idle_npc_x')
        self.assertIsNone(suggested_idle(['Walk_x']))

    def test_categories(self):
        self.assertEqual(category('viewer_graph_rule_frontend', None), 'generic_default_reading')
        self.assertEqual(category('viewer_graph_rule_frontend', 'materialgraph/global/basic_skin/'), 'prefix_viewer_rule')
        self.assertEqual(category('viewer_graph_rule_frontend', 'required/materials/basic_normal_gloss.'), 'exact_viewer_rule')
        self.assertEqual(category('color_only_preview_missing_base_color', None), 'missing_base_colour')
        self.assertEqual(category('partial_skin_frontend', None), 'partial_family')


if __name__ == '__main__':
    unittest.main()
