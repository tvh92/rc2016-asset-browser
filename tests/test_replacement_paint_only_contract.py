"""Paint-only binding and explicit experimental frontend contracts."""
import unittest
from unittest import mock

from rc2016.shaders import bindings
from rc2016.shaders.ir import DAG


def _lowered(*, albedo_attributes=(0, 1, 3), normal_attributes=()):
    ir = DAG()
    albedo = [ir.node('input', name=f'attribute4.{ch}', kind='attribute',
                      attribute=4, channel=ch) for ch in albedo_attributes]
    albedo = (albedo + [ir.const(0.)] * 3)[:3]
    zero = ir.const(0.)
    normal = None
    if normal_attributes:
        xyz = [ir.node('input', name=f'attribute{normal_attributes[0]}.{ch}', kind='attribute',
                       attribute=normal_attributes[0], channel=ch) for ch in range(3)]
        normal = dict(xyz=xyz, api=5)
    return ir, dict(nodes=ir.nodes,
                    outputs=dict(albedo=albedo, F0=[zero] * 3, gloss=zero, emission=zero),
                    normal=normal, limitations=[])


def _bind(stream_channels, *, adapted, albedo_attributes=(0, 1, 3), normal_attributes=()):
    ir, lowered = _lowered(albedo_attributes=albedo_attributes,
                           normal_attributes=(2,) if normal_attributes else ())
    with mock.patch('rc2016.formats.materials.texture_slot_records', return_value=[]), \
         mock.patch.object(bindings, '_parameter_values', return_value=({}, [])), \
         mock.patch('rc2016.shaders.engine_inputs.reconstruct_world_position',
                    side_effect=lambda _ir, roots: (roots, adapted)):
        contract = {'resources': [dict(flag=0, slot_key=1, type_hash=2, path='normal.texture')], 'index': 0}
        return bindings._bind({'_lowered': lowered}, contract, b'', {},
                              True, normal_attributes, stream_channels)


def _receipt(contract, **extra):
    result = dict(status='experimental', preview_contract=contract,
                  automatic_ready=False, game_equivalence=False,
                  code_sha256='code', graph_sha256='graph', material_sha256='material')
    result.update(extra)
    return result


class PaintOnlyBindingTests(unittest.TestCase):
    def test_exact_squared_semantic5_paint_can_bind_without_position_terms(self):
        evidence = {(4, 0): (5, 0), (4, 1): (5, 1), (4, 3): (5, 3)}
        result = _bind(evidence, adapted=0)
        self.assertEqual(result['painted_channel_evidence'], [
            dict(attribute=4, channel=0, semantic=5, component=0),
            dict(attribute=4, channel=1, semantic=5, component=1),
            dict(attribute=4, channel=3, semantic=5, component=3),
        ])
        self.assertNotIn('world_position_terms', result)
        self.assertFalse(any(n.get('kind') in ('preview_position', 'preview_normal')
                             for n in result['nodes']))
        self.assertTrue(any(n.get('kind') == 'preview_ground_blend' for n in result['nodes']))

    def test_unproven_paint_or_remaining_engine_attribute_stays_unbound(self):
        for evidence, albedo in (
            ({}, (0, 1, 3)),
            ({(4, 0): (6, 0), (4, 1): (5, 1), (4, 3): (5, 3)}, (0, 1, 3)),
            ({(4, 0): (5, 0), (4, 1): (5, 1), (4, 3): (5, 3)}, (0, 1, 2)),
        ):
            with self.subTest(evidence=evidence, albedo=albedo), self.assertRaises(ValueError):
                _bind(evidence, adapted=0, albedo_attributes=albedo)

    def test_normal_adapter_is_enabled_only_when_position_was_reconstructed(self):
        evidence = {(4, 0): (5, 0), (4, 1): (5, 1), (4, 3): (5, 3)}
        with self.assertRaises(ValueError):
            _bind(evidence, adapted=0, normal_attributes={2})
        result = _bind(evidence, adapted=1, normal_attributes={2})
        self.assertIn('world_position_terms', result)
        self.assertTrue(any(n.get('kind') == 'preview_normal' for n in result['nodes']))


class PaintOnlyFrontendContractTests(unittest.TestCase):
    def test_paint_only_contract_is_separate_from_world_position_and_remains_experimental(self):
        paint_contract = getattr(bindings, 'PAINT_STREAM_CONTRACT', None)
        self.assertIsNotNone(paint_contract, 'translator should expose a distinct paint-only preview contract')
        self.assertNotEqual(paint_contract, bindings.WORLD_POSITION_CONTRACT)
        from rc2016.viewer.experimental_materials import valid_contract
        self.assertTrue(valid_contract(_receipt(paint_contract)))
        self.assertTrue(valid_contract(_receipt(bindings.WORLD_POSITION_CONTRACT)))
        for invalid in (_receipt(paint_contract, automatic_ready=True),
                        _receipt(paint_contract, game_equivalence=True),
                        dict(status='ready', preview_contract=paint_contract)):
            self.assertFalse(valid_contract(invalid))

    def test_attribute_only_engine_diagnostic_reaches_generic_paint_translation(self):
        from rc2016.viewer import experimental_materials
        receipt = _receipt(bindings.PAINT_STREAM_CONTRACT)
        with mock.patch('rc2016.viewer.native_glsl.prepare_material_translation',
                        return_value=dict(status='unsupported', diagnostics=[
                            dict(reason='Unbound engine/interpolant input attribute4.0')])) as native, \
             mock.patch.object(experimental_materials, 'world_position_translation',
                               return_value=receipt) as translate:
            actual = experimental_materials.world_position_preview(object(), 'material/replacement.material')
        native.assert_called_once()
        translate.assert_called_once()
        self.assertIs(actual, receipt)

    def test_prepare_translation_marks_paint_only_receipt_experimental(self):
        program = dict(status='compiled', shader_id='vs/ps-pair', code_sha256='code',
                       code_offset=100, header_offset=200)
        graph = dict(programs=[program], graph_sha256='graph', translator_version='test', limitations=[])
        contract = dict(index=1, resources=[])
        bound = dict(nodes=[dict(op='const', args=[], value=0.)],
                     outputs=dict(albedo=[0, 0, 0], F0=[0, 0, 0], gloss=0, emission=0),
                     normal=None, texture_bindings=[], pass_contract_index=1,
                     limitations=['paint evidence'], painted_channel_evidence=[dict(semantic=5, component=3)])
        with mock.patch.object(bindings, 'compiled', return_value=graph), \
             mock.patch.object(bindings, 'pass_contracts', return_value=[contract]), \
             mock.patch.object(bindings, 'program_contracts', return_value=[contract]), \
             mock.patch.object(bindings, 'graph_stream_evidence', return_value=(set(), {(4, 3): (5, 3)})), \
             mock.patch.object(bindings, '_bind', return_value=bound):
            result = bindings.prepare_translation([b'graph'], b'material', {}, world_position=True)
        self.assertEqual(result['status'], 'experimental')
        self.assertEqual(result['preview_contract'], bindings.PAINT_STREAM_CONTRACT)
        self.assertFalse(result['automatic_ready'])
        self.assertFalse(result['game_equivalence'])
        self.assertNotIn('world_position_terms', result)


if __name__ == '__main__':
    unittest.main()
