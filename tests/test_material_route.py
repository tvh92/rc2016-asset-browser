"""Blender front-end precedence between viewer graph rules and ready native translations."""
import unittest

from rc2016.formats.materials import final_shader_status

READY = dict(status='ready')
EXACT_GRAPH = ['materialgraph/global/basic_colored_spec/basic_colored_spec.materialgraph']
FAMILY_GRAPH = ['required/materials/basic_normal_gloss_fancy_variant.materialgraph']
GENERIC_GRAPH = ['required/materials/basic_normal_coloredgloss_detail.materialgraph']


def rule(unported=()):
    return dict(applies=True, textures={'_c': 'c.texture'}, ported=[], unported=list(unported))


def route(graphs, translation=READY, unported=(), build='color_only_preview'):
    return final_shader_status(build, rule(unported), {'c.texture'}, translation, None, [], graphs)


class MaterialRouteTests(unittest.TestCase):
    def test_exact_rule_keeps_precedence_over_a_ready_translation(self):
        self.assertEqual(route(EXACT_GRAPH), 'viewer_graph_rule_frontend')
        self.assertEqual(route(EXACT_GRAPH, unported=['parallax']), 'viewer_graph_rule_frontend')

    def test_exact_rule_with_dropped_terms_yields(self):
        self.assertEqual(route(EXACT_GRAPH, unported=['tint']), 'native_translated_deferred_frontend')

    def test_family_and_generic_readings_yield_to_a_ready_translation(self):
        self.assertEqual(route(FAMILY_GRAPH), 'native_translated_deferred_frontend')
        self.assertEqual(route(GENERIC_GRAPH), 'native_translated_deferred_frontend')

    def test_without_a_ready_translation_the_reading_is_used(self):
        for graphs in (EXACT_GRAPH, FAMILY_GRAPH, GENERIC_GRAPH):
            self.assertEqual(route(graphs, translation=dict(status='unsupported')), 'viewer_graph_rule_frontend')

    def test_skin_family_is_never_replaced_by_a_translation(self):
        status = final_shader_status('partial_skin_frontend', rule(), {'c.texture'}, READY, None,
                                     [dict(family='basic_skin')], GENERIC_GRAPH)
        self.assertEqual(status, 'partial_skin_frontend')


if __name__ == '__main__':
    unittest.main()
