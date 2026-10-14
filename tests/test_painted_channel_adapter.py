"""Experimental painted-channel adaptation with exact program evidence.

No game assets or Blender runtime are required. They assert that only exact
semantic-5 R/G/W evidence creates experimental inputs, and evaluate the
linear generic white fallback represented by the resulting bound IR.
"""
import unittest
from unittest import mock

from rc2016.shaders import bindings
from rc2016.shaders.ir import DAG


def _fixture(stream_channels, attribute_channels=(0, 1, 3)):
    ir = DAG()
    attributes = [ir.node('input', name=f'attribute4.{channel}', kind='attribute',
                          attribute=4, channel=channel) for channel in attribute_channels]
    attributes = (attributes + [ir.const(0.)] * 3)[:3]
    zero = ir.const(0.)
    lowered = dict(nodes=ir.nodes,
        outputs=dict(albedo=attributes, F0=[zero, zero, zero], gloss=zero, emission=zero),
        normal=None, limitations=[])
    program = {'_lowered': lowered}
    contract = {'resources': [], 'index': 0}
    with mock.patch('rc2016.formats.materials.texture_slot_records', return_value=[]), \
         mock.patch.object(bindings, '_parameter_values', return_value=({}, [])), \
         mock.patch('rc2016.shaders.engine_inputs.reconstruct_world_position',
                    side_effect=lambda _ir, roots: (roots, 1)):
        result = bindings._bind(program, contract, b'', {}, True, (), stream_channels)
    return result


def _evaluate(nodes, root, values):
    memo = {}
    def visit(index):
        if index in memo:
            return memo[index]
        node = nodes[index]
        op, args = node['op'], [visit(i) for i in node['args']]
        if op == 'const': value = node['value']
        elif op == 'input': value = values[node['name']]
        elif op == 'add': value = args[0] + args[1]
        elif op == 'sub': value = args[0] - args[1]
        else: raise AssertionError(f'Unexpected operation in adapter proof: {op}')
        memo[index] = value
        return value
    return visit(root)


class PaintedAdapterTests(unittest.TestCase):
    def test_red_green_use_exact_proven_lanes_and_white_unmatched_fallback(self):
        evidence = {
            (4, 0): (5, 0),  # red
            (4, 1): (5, 1),  # green
            (4, 2): (5, 2),  # unsupported blue lane is deliberately ignored
            (4, 3): (5, 3),  # alpha
        }
        result = _fixture(evidence)
        self.assertEqual(result['painted_channel_evidence'], [
            dict(attribute=4, channel=0, semantic=5, component=0),
            dict(attribute=4, channel=1, semantic=5, component=1),
            dict(attribute=4, channel=3, semantic=5, component=3),
        ])
        nodes, roots = result['nodes'], result['outputs']['albedo']
        # Matched: supplied lanes are already squared at the vertex and interpolate.
        matched = {'preview_ground_blend.0': .25, 'preview_ground_blend.1': .36,
                   'preview_ground_blend.2': 1., 'preview_vertex_alpha_squared': .49}
        self.assertAlmostEqual(_evaluate(nodes, roots[0], matched), .25)
        self.assertAlmostEqual(_evaluate(nodes, roots[1], matched), .36)
        self.assertAlmostEqual(_evaluate(nodes, roots[2], matched), .49)
        # Unlisted vertices carry zero masked channels and zero presence: R/G become white.
        unmatched = {'preview_ground_blend.0': 0., 'preview_ground_blend.1': 0.,
                     'preview_ground_blend.2': 0., 'preview_vertex_alpha_squared': 1.}
        self.assertAlmostEqual(_evaluate(nodes, roots[0], unmatched), 1.)
        self.assertAlmostEqual(_evaluate(nodes, roots[1], unmatched), 1.)
        self.assertAlmostEqual(_evaluate(nodes, roots[2], unmatched), 1.)

    def test_wrong_semantic_or_component_does_not_bind(self):
        for evidence, channels in (
            ({(4, 0): (6, 0)}, (0,)),  # wrong vertex semantic
            ({(4, 2): (5, 2)}, (2,)),  # blue component has no adapter
        ):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, 'Unbound engine/interpolant input'):
                _fixture(evidence, channels)

    def test_graph_adapter_uses_exact_pixel_header_mapping(self):
        common={(4, 0): (5, 0), (4, 1): (5, 1), (4, 3): (5, 3)}
        by_header={0x100: common, 0x200: {}}
        with mock.patch.object(bindings, '_once', return_value=(set(), common, by_header)):
            selected=bindings.graph_stream_evidence(b'graph', program={'header_offset':0x100})
            missing=bindings.graph_stream_evidence(b'graph', program={'header_offset':0x300})
        self.assertEqual(selected[1], common)
        self.assertEqual(missing[1], {})
        _fixture(selected[1])
        with self.assertRaisesRegex(ValueError, 'Unbound engine/interpolant input'):
            _fixture(missing[1])
    def test_empty_exact_program_evidence_does_not_create_paint_inputs(self):
        # graph_stream_evidence performs the header selection; this is the expected
        # downstream behavior when it returns an empty map for a missing header.
        with self.assertRaisesRegex(ValueError, 'Unbound engine/interpolant input'):
            _fixture({})

    def test_limitations_keep_the_inference_and_standalone_gap_visible(self):
        result = _fixture({(4, 0): (5, 0), (4, 1): (5, 1), (4, 3): (5, 3)})
        receipt = ' '.join(result['limitations'])
        self.assertIn('runtime flag path', receipt)
        self.assertIn('identity are inferred', receipt)
        self.assertIn('standalone red/green stream transport remains unresolved', receipt)
        self.assertNotIn('game_equivalence', result)


if __name__ == '__main__':
    unittest.main()
