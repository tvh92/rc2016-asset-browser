"""Contract tests for source association and per-output hashes.

These tests do not select a pass or change translation readiness. They exercise
the diagnostic API alongside the bounded source-pointer join.
"""
import copy
import hashlib
import json
import struct
import unittest
from unittest.mock import patch

from rc2016.shaders import bindings
from rc2016.shaders.ir import DAG


def bound(api=5):
    ir = DAG()
    uv = [ir.node('input', name=f'attribute0.{ch}', kind='attribute',
                  attribute=0, channel=ch) for ch in (0, 1)]
    rgba = [ir.node('texture', uv, api=api, sample=0, channel=ch) for ch in range(4)]
    f0 = [ir.const(.2), ir.const(.3), ir.const(.4)]
    gloss = ir.const(.6)
    emission = ir.const(0.)
    normal_api = api + 1
    normal = [ir.node('texture', uv, api=normal_api, sample=1, channel=ch) for ch in range(3)]
    return dict(nodes=ir.nodes,
                outputs=dict(albedo=rgba[:3], F0=f0, gloss=gloss, emission=emission),
                normal=dict(api=normal_api, sample=1, xyz=normal),
                texture_bindings=[dict(api=api, path='textures/source.texture', type_hash=77),
                                  dict(api=normal_api, path='textures/normal.texture', type_hash=78)],
                pass_contract_index=0, limitations=[])


def serialized_graph(record_index=4):
    """Small DAT1 with one B6 pointer into one synthetic Shdr envelope."""
    shader_base = 0x200
    b6_base = shader_base + 0x200
    relative = 0x20
    blob_size = 0x100
    cb_mask = 0x14
    resource_mask = 1 << 5
    word2 = 0xDEADBEEF
    table = bytearray(13 * 20)
    struct.pack_into('<5I', table, record_index * 20,
                     cb_mask, resource_mask, word2, relative, blob_size)
    shader = bytearray(0x200)
    sections = [(0x873D1E6C, shader_base, len(shader)),
                (0xB6D49474, b6_base, len(table))]
    total = b6_base + len(table)
    header = struct.pack('<IIIHH', 0x44415431, 0, total, len(sections), 0)
    directory = b''.join(struct.pack('<III', *row) for row in sections)
    data = bytearray(total)
    data[:len(header)] = header
    data[len(header):len(header) + len(directory)] = directory
    data[shader_base:shader_base + len(shader)] = shader
    data[b6_base:b6_base + len(table)] = table
    program = dict(header_offset=shader_base + relative + 36,
                   code_offset=shader_base + relative + 80, code_size=16,
                   usage_slots=[dict(kind=0, api=5)],
                   ir=dict(nodes=[dict(op='texture', api=5)]))
    contract = dict(index=0, resources=[dict(slot_key=1)], table=[], cb_bytes=32)
    return bytes(data), program, contract, dict(index=record_index,
        file_offset=b6_base + record_index * 20, pass_index=0, index_in_pass=record_index,
        cb_mask=cb_mask, resource_mask=resource_mask, word2_uninterpreted=word2,
        shader_relative_offset=relative, shader_blob_size=blob_size,
        shader_header_offset=program['header_offset'])


class OutputHashTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(bindings, 'surface_components', None)),
                        'planned surface_components(bound) API is not present yet')
        self.assertTrue(callable(getattr(bindings, 'surface_component_signatures', None)),
                        'planned surface_component_signatures(bound) API is not present yet')

    def test_api_renumbering_preserves_each_component_hash(self):
        a = bound(5)
        b = bound(12)
        self.assertEqual(bindings.surface_components(a), bindings.surface_components(b))
        self.assertEqual(bindings.surface_component_signatures(a),
                         bindings.surface_component_signatures(b))
        self.assertEqual(bindings.surface_signature(a), bindings.surface_signature(b))

    def test_emission_difference_isolated_to_emission_component(self):
        a = bound()
        b = copy.deepcopy(a)
        b['outputs']['emission'] = len(b['nodes'])
        b['nodes'].append(dict(op='const', value=.125, args=[]))
        before = bindings.surface_component_signatures(a)
        after = bindings.surface_component_signatures(b)
        self.assertEqual(set(before), {'albedo', 'F0', 'gloss', 'emission', 'normal'})
        self.assertNotEqual(before['emission'], after['emission'])
        self.assertEqual({k: before[k] for k in before if k != 'emission'},
                         {k: after[k] for k in after if k != 'emission'})
        self.assertNotEqual(bindings.surface_signature(a), bindings.surface_signature(b))

    def test_normal_difference_isolated_to_normal_component(self):
        a = bound()
        b = copy.deepcopy(a)
        b['nodes'][b['normal']['xyz'][0]]['channel'] = 2
        before = bindings.surface_component_signatures(a)
        after = bindings.surface_component_signatures(b)
        self.assertNotEqual(before['normal'], after['normal'])
        self.assertEqual({k: before[k] for k in before if k != 'normal'},
                         {k: after[k] for k in after if k != 'normal'})

    def test_source_metadata_does_not_change_surface_identity(self):
        a = bound()
        b = copy.deepcopy(a)
        b.update(shader_id='0x1234', code_sha256='abc', pass_contract_index=8,
                 source_shader_record=dict(index=12, file_offset=123456,
                                           word2_uninterpreted=0xFEED))
        self.assertEqual(bindings.surface_components(a), bindings.surface_components(b))
        self.assertEqual(bindings.surface_component_signatures(a),
                         bindings.surface_component_signatures(b))
        self.assertEqual(bindings.surface_signature(a), bindings.surface_signature(b))

    def test_existing_surface_sha_is_hash_of_component_root(self):
        b = bound()
        root = bindings.surface_components(b)
        expected = hashlib.sha256(json.dumps(root, sort_keys=True).encode()).hexdigest()
        self.assertEqual(bindings.surface_signature(b), expected)


class SourceRecordTests(unittest.TestCase):
    def test_b6_pointer_receipt_is_exact_and_contract_is_unchanged(self):
        data, program, contract, expected = serialized_graph()
        original = copy.deepcopy(contract)
        got = bindings.program_contracts(data, program, [contract])
        self.assertEqual(len(got), 1)
        self.assertEqual({k: v for k, v in got[0].items() if k != 'source_shader_record'}, original)
        self.assertEqual(got[0]['source_shader_record'], expected)
        self.assertEqual(contract, original)

    def test_pointer_record_metadata_does_not_merge_into_neighbor_contract(self):
        data, program, contract, expected = serialized_graph(record_index=12)
        got = bindings.program_contracts(data, program, [contract])
        self.assertEqual(got[0]['source_shader_record'], expected)
        self.assertEqual(expected['pass_index'], 0)
        self.assertEqual(expected['index_in_pass'], 12)


class ReceiptOutputTests(unittest.TestCase):
    def test_unsupported_receipt_prints_candidate_identity_binding_and_components(self):
        from rc2016.shaders.census import print_receipt
        source_record = dict(index=4, file_offset=900, pass_index=0, index_in_pass=4,
                             cb_mask=0x14, resource_mask=0x20,
                             word2_uninterpreted=0xDEADBEEF,
                             shader_relative_offset=32, shader_blob_size=256,
                             shader_header_offset=568)
        candidate = dict(shader_id='0x12345678', code_sha256='code-sha',
                         pass_contract_index=0, surface_sha256='surface-sha',
                         component_sha256=dict(albedo='a', F0='f', gloss='g',
                                              emission='e', normal='n'),
                         differing_components=['emission'], source_shader_record=source_record,
                         texture_bindings=[dict(api=5, path='textures/source.texture', source='material')])
        unsupported = dict(status='unsupported', differing_components=['emission'], diagnostics=['Different portable outputs'],
                           candidate_surfaces=[candidate], game_equivalence=False)
        with patch('rc2016.viewer.native_glsl.prepare_material_translation', return_value=unsupported), \
             patch('rc2016.viewer.experimental_materials.world_position_translation', return_value=unsupported), \
             patch('rc2016.viewer.experimental_materials.preview_with_fallback', return_value=unsupported), \
             patch('rc2016.viewer.textures.material_texture_paths', return_value=[]), \
             patch('rc2016.viewer.experimental_materials.has_preview', return_value=False):
            from io import StringIO
            import contextlib
            stream = StringIO()
            with contextlib.redirect_stdout(stream):
                print_receipt(object(), 'material/test.material')
        output = stream.getvalue()
        for expected in ('0x12345678', 'code-sha', 'surface-sha', 'textures/source.texture',
                         'source shader record', 'word2_uninterpreted', '3735928559',
                         'structurally differing components', 'emission', 'component SHA256'):
            self.assertIn(expected, output)


if __name__ == '__main__':
    unittest.main()
