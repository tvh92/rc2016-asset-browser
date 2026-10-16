"""Source-bounded controls for the Gaspar lava-flow front end.

The archive-backed test is skipped on machines without the private research
archive. The remaining tests cover the hash-pinned contract and its numerical
equations without treating suffixes as shader semantics.
"""
import math
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from rc2016.shaders.liquids import (
    GASPAR_LAVA_FLOW_GRAPH, GASPAR_LAVA_FLOW_GRAPH_SHA256,
    GASPAR_LAVA_FLOW_WRITERS, GASPAR_LAVA_FLOW_PARAMETERS,
    gaspar_lava_flow_contract,
)


class LavaFlowSourceTests(unittest.TestCase):
    def test_contract_requires_exact_graph_and_decoded_writer(self):
        writer = next(iter(GASPAR_LAVA_FLOW_WRITERS))
        contract = gaspar_lava_flow_contract(GASPAR_LAVA_FLOW_GRAPH,
                                             GASPAR_LAVA_FLOW_GRAPH_SHA256,
                                             writer)
        self.assertEqual(contract['status'], 'source_decoded_partial')
        self.assertEqual(contract['equations']['F0'], 'CB2[7] * sample(API8, uv_b).g')
        self.assertIsNone(gaspar_lava_flow_contract(
            GASPAR_LAVA_FLOW_GRAPH + '.bak', GASPAR_LAVA_FLOW_GRAPH_SHA256, writer))
        self.assertIsNone(gaspar_lava_flow_contract(
            GASPAR_LAVA_FLOW_GRAPH, '0' * 64, writer))
        self.assertIsNone(gaspar_lava_flow_contract(
            GASPAR_LAVA_FLOW_GRAPH, GASPAR_LAVA_FLOW_GRAPH_SHA256, 'f' * 64))

    def test_optional_archive_material_has_agreed_pass_bindings(self):
        research = os.environ.get('RC2016_GAME_ROOT') or os.environ.get('RC2016_RESEARCH')
        if not research:
            self.skipTest('RC2016_RESEARCH is not configured')
        if not Path(research).exists():
            self.skipTest('RC2016_RESEARCH path is absent')
        try:
            from rc2016.formats.archive import Archive, locate
            from rc2016.formats.archive import asset_data
            root = locate(Path(research))
            archive = Archive(root, Path(__file__).resolve().parents[1] / '.cache/liquid-flow-test',
                              lambda *_a, **_k: None)
            self.addCleanup(archive.close)
            graph_ids = archive.resolve(GASPAR_LAVA_FLOW_GRAPH)
            material_path = 'material/environment/gaspar/fx/gas_lava_flow_01/gas_lava_flow_01.material'
            material_ids = archive.resolve(material_path)
            if not graph_ids or not material_ids:
                self.skipTest('Gaspar lava-flow graph/material are not indexed in this archive')
            from rc2016.shaders.liquids import liquid_flow_rule
            graph_blob = asset_data(archive, graph_ids[0])
            result = liquid_flow_rule(graph_blob, asset_data(archive, material_ids[0]))
            self.assertEqual(result['status'], 'source_decoded_partial', result)
            self.assertEqual(result['graph_sha256'], GASPAR_LAVA_FLOW_GRAPH_SHA256)
            self.assertEqual(result['time'], 0.0)
            self.assertEqual(result['texture_slots']['albedo']['role'], 'albedo')
            self.assertEqual(result['texture_slots']['specular_gloss_emission']['role'],
                             'specular_gloss_emission')
            self.assertEqual(result['normal_fallback'], 'geometry_normal')
            self.assertTrue(any('Attribute0.xy' in item for item in result['limitations']))
            self.assertEqual(len(result['writers']), 2)
            self.assertEqual(result['writers'][0]['effective_uv_offsets_at_frozen_time']['uv_b'],
                             tuple(result['uv_offset']))
            from rc2016.viewer.textures import material_texture_paths
            from rc2016.formats.materials import viewer_graph_rule
            payload = material_texture_paths(archive, material_path)
            self.assertEqual(payload['liquid_flow']['graph_sha256'], GASPAR_LAVA_FLOW_GRAPH_SHA256)
            self.assertEqual(payload['_c'], payload['liquid_flow']['texture_slots']['albedo']['texture_path'])
            self.assertEqual(payload['_g'], payload['liquid_flow']['texture_slots']['specular_gloss_emission']['texture_path'])
            self.assertNotIn('_n', payload)
            blender_rule = viewer_graph_rule(archive, material_path, [payload['_c'], payload['_g']])
            self.assertIn('liquid_flow', blender_rule['ported'])
            self.assertEqual(blender_rule['terms']['liquid_flow']['uv_offset'], payload['liquid_flow']['uv_offset'])

            # Compile and evaluate the actual GCN writer DAG. These assertions
            # compare source outputs to the decoded channel/phase equations;
            # the chosen texels and CB0 time are explicit controls.
            from rc2016.shaders.translator import compile_graph, lower_deferred
            from rc2016.shaders.ir import DAG
            compiled = compile_graph(graph_blob, allow_independent_samplers=True)['programs']
            by_writer = {row['shader_id']: row for row in compiled
                         if row['shader_id'] in ('0xea6006f6', '0xbacb8758')}
            self.assertEqual({key: by_writer[key]['code_sha256'] for key in by_writer},
                             {value['shader_id']: digest for digest, value in GASPAR_LAVA_FLOW_WRITERS.items()})
            program = by_writer['0xea6006f6']
            self.assertEqual(program['code_sha256'],
                             '4dc3c446f0a31c8ef2c890d1b5bce58abc4bd16fb50ff21b7870abdf5ed50be0')
            low = lower_deferred(program['ir'])
            dag = DAG(low['nodes'])
            params = result['parameters_by_cb2_dword']
            time_value, rate_value = 4.2, 0.13
            inputs = {f'CB2[{dword}]': value for dword, value in params.items()}
            inputs.update({'CB2[11]': rate_value, 'CB0[99]': time_value,
                           'attribute0.0': 0.375, 'attribute0.1': 0.625})
            texels = {(2, 0): .36, (2, 1): .44, (2, 2): .72,
                      (3, 0): .1, (3, 1): .3, (3, 2): .4,
                      (0, 0): .4, (0, 1): .6, (1, 0): .2, (1, 1): .7}
            decoded = {name: ([dag.evaluate(node, inputs, texels) for node in root]
                              if isinstance(root, list) else dag.evaluate(root, inputs, texels))
                       for name, root in low['outputs'].items()}
            phase = 2.0 * math.pi * ((abs(0.15915493667125702 * rate_value * time_value)) % 1.0)
            expected_emission = params[12] * texels[(2, 2)] * (0.5 + 0.5 * math.cos(phase))
            self.assertEqual(decoded['albedo'], [.1, .3, .4])
            self.assertAlmostEqual(decoded['F0'][0], params[7] * .44, places=6)
            self.assertAlmostEqual(decoded['gloss'], math.sqrt(min(1.0, params[6] * .36)), places=6)
            self.assertAlmostEqual(decoded['emission'], expected_emission, delta=3e-7)

            # Exercise instance substitution and pass agreement against the
            # real graph contracts, while keeping the archive material parser
            # out of the mutation test. The production decoder still supplies
            # the graph and source pass contracts.
            from rc2016.formats import index as index_format, materials as material_format
            from rc2016.shaders import bindings
            from rc2016.shaders.liquids import liquid_flow_rule
            defaults = {dword: spec['default'] for dword, spec in GASPAR_LAVA_FLOW_PARAMETERS.items()}
            slot_path = {
                0x49A7866BABDAD780: 'override/albedo.texture',
                0x49A7866B1576D848: 'override/spec-gloss.texture',
            }
            graph_slots = [
                (0x49A7866BABDAD780, 'graph/default-c.texture'),
                (0x49A7866B0056CBD0, 'graph/water-a.texture'),
                (0x49A7866B39DBF715, 'graph/water-b.texture'),
                (0x49A7866B1576D848, 'graph/default-g.texture'),
                (0x49A7866B67D6383E, 'graph/noise.texture'),
                (0x49A7866B7AE34E7A, 'graph/normal.texture'),
            ]
            contracts = [dict(resources=[dict(slot_key=key, path=path) for key, path in graph_slots])
                         for _ in range(2)]
            def evaluate(params, slots=None):
                records = [{'slot_key': key, 'path': value} for key, value in (slots or slot_path).items()]
                with (patch.object(index_format, 'references', return_value=[{'path': GASPAR_LAVA_FLOW_GRAPH}]),
                      patch.object(material_format, 'texture_slot_records', return_value=records),
                      patch.object(material_format, 'extract_material_parameters', return_value={'fixture': True}),
                      patch.object(bindings, 'pass_contracts', return_value=contracts),
                      patch.object(bindings, '_parameter_values', return_value=(dict(params), {'fixture': True}))):
                    return liquid_flow_rule(graph_blob, b'fixture-material')

            zero = evaluate(defaults)
            self.assertEqual(zero['status'], 'source_decoded_partial')
            self.assertEqual(zero['uv_offset'], [0.0, 0.0])
            self.assertEqual(zero['time'], 0.0)
            changed = dict(defaults, **{})
            changed.update({9: 0.25, 8: -0.5, 15: 7.0, 14: -3.0, 6: 0.4, 7: 0.2, 11: 2.0, 12: 5.0})
            varied = evaluate(changed, {
                0x49A7866BABDAD780: 'instance/changed-c.texture',
                0x49A7866B1576D848: 'instance/changed-g.texture',
            })
            self.assertEqual(varied['uv_offset'], [0.25, -0.5])
            self.assertEqual(varied['uv_scroll'], {'x': 7.0, 'y': -3.0})
            self.assertEqual(varied['spec'], 0.2)
            self.assertEqual(varied['gloss'], 0.4)
            self.assertEqual(varied['k'], 5.0)
            self.assertEqual(varied['texture_slots']['albedo']['texture_path'], 'instance/changed-c.texture')
            self.assertEqual(varied['texture_slots']['specular_gloss_emission']['texture_path'],
                             'instance/changed-g.texture')
            self.assertEqual(varied['writers'][0]['effective_uv_offsets_at_frozen_time']['uv_b'],
                             varied['writers'][1]['effective_uv_offsets_at_frozen_time']['uv_b'])
            bad = dict(defaults, **{})
            bad[11] = float('nan')
            self.assertEqual(evaluate(bad)['status'], 'unsupported')
            bad[11] = 'not-a-number'
            self.assertEqual(evaluate(bad)['status'], 'unsupported')
        except FileNotFoundError as exc:
            # An existing tree with missing archive contents is a fixture
            # failure, not a reason to hide the regression as a skip.
            if not Path(research).exists():
                self.skipTest(f'private archive is not available: {exc}')
            raise


if __name__ == '__main__':
    unittest.main()
