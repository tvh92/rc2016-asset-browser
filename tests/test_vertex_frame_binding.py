"""Vertex frame binding (rc2016/shaders/bindings.py, vertex_frame=True).

attribute2.xyz and attribute3.xyz become the decoded vertex normal and tangent, and the integer sign chain of
attribute3.w (the bitangent handedness) becomes an explicit input. The handedness is inferred from the UV
winding, so it is never reported as game-proven. Synthetic checks always run; the archive check skips without
RC2016_RESEARCH.
"""
import os
import unittest

from rc2016.shaders import bindings
from rc2016.shaders.ir import DAG


def _input(ir, name, kind, attribute, channel):
    return ir.node('input', name=name, kind=kind, attribute=attribute, channel=channel)


class FrameAndEquivalenceTests(unittest.TestCase):
    def _bound(self, build):
        ir = DAG([])
        t = ir.node('texture', [ir.node('input', name='attribute0.0', kind='attribute', attribute=0, channel=0),
                                ir.node('input', name='attribute0.1', kind='attribute', attribute=0, channel=1)],
                    api=5, sample=0, channel=0)
        h = ir.node('input', name='vertex_handedness', kind='vertex_handedness')
        value = build(ir, t, h)
        return dict(nodes=ir.nodes, normal=None, texture_bindings=[dict(api=5, path='a_c.texture', type_hash=1)],
                    outputs=dict(albedo=[value] * 3, F0=[value] * 3, gloss=value, emission=value))

    def test_reassociated_variants_are_numerically_equivalent(self):
        first = self._bound(lambda ir, t, h: ir.op('mul', ir.op('mul', t, h), ir.const(.3)))
        second = self._bound(lambda ir, t, h: ir.op('mul', t, ir.op('mul', ir.const(.3), h)))
        self.assertNotEqual(bindings.surface_signature(first), bindings.surface_signature(second))
        self.assertIsNotNone(bindings.numeric_equivalence([first, second]))

    def test_different_variants_are_rejected(self):
        first = self._bound(lambda ir, t, h: ir.op('mul', t, h))
        second = self._bound(lambda ir, t, h: ir.op('mul', t, ir.const(.999)))
        self.assertIsNone(bindings.numeric_equivalence([first, second]))
        other = dict(first, texture_bindings=[dict(api=5, path='b_c.texture', type_hash=1)])
        self.assertIsNone(bindings.numeric_equivalence([first, other]))

    def test_frame_inputs_emit_only_in_experimental_glsl(self):
        from rc2016.viewer.native_glsl import emit_surface, fragment_source
        ir = DAG([])
        h = ir.node('input', name='vertex_handedness', kind='vertex_handedness')
        t = ir.node('input', name='vertex_tangent.0', kind='vertex_tangent', channel=0)
        n = ir.node('input', name='vertex_normal.2', kind='vertex_normal', channel=2)
        v = ir.op('mul', ir.op('mul', h, t), n)
        receipt = dict(status='experimental', preview_contract=bindings.VERTEX_FRAME_CONTRACT, automatic_ready=False,
                       game_equivalence=False, code_sha256='c', graph_sha256='g', material_sha256='m', nodes=ir.nodes,
                       texture_bindings=[], translation_sha256='t', outputs=dict(albedo=[v] * 3, F0=[v] * 3, gloss=v, emission=v))
        body = emit_surface(receipt, experimental=True)['body']
        for name in ('nativeFrameTangent.x', 'nativeHandedness', 'vTintWorldNrm.z'):
            self.assertIn(name, body)
        self.assertIn('float nativeFrameHandedness', fragment_source(receipt, experimental=True)['source'])
        with self.assertRaises(ValueError):
            emit_surface(receipt)


class HandednessChainTests(unittest.TestCase):
    def test_select_chain_is_recognised_as_the_sign_of_attribute3_w(self):
        ir = DAG([])
        w = _input(ir, 'attribute3.3', 'attribute', 3, 3)
        zero = ir.const(0.0)
        denorm = ir.const(1.401298464324817e-45)
        positive = ir.op('select', ir.op('lt', zero, w), denorm, zero)
        negative = ir.op('select', ir.op('gt', zero, w), denorm, zero)
        sign = ir.op('i2f', ir.op('u_sub', positive, negative))
        self.assertTrue(bindings._handedness_sign(ir, sign))

    def test_other_inputs_are_not_the_handedness(self):
        ir = DAG([])
        normal_z = _input(ir, 'attribute2.2', 'attribute', 2, 2)
        self.assertFalse(bindings._handedness_sign(ir, ir.op('i2f', normal_z)))
        w = _input(ir, 'attribute3.3', 'attribute', 3, 3)
        x = _input(ir, 'attribute3.0', 'attribute', 3, 0)
        self.assertFalse(bindings._handedness_sign(ir, ir.op('i2f', ir.op('add', w, x))))

    def test_vertex_kinds_are_the_only_frame_exemptions(self):
        self.assertEqual(bindings.VERTEX_FRAME_KINDS, ('vertex_normal', 'vertex_tangent', 'vertex_handedness'))


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class BoulderVertexFrameTests(unittest.TestCase):
    MATERIAL = 'material/environment/aridia/rock/ari_rock_boulder_01/tj_ari_rock_boulder_01.material'

    def _receipt(self, vertex_frame, world_position=False):
        from rc2016.formats.archive import asset_data, asset_references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import open_archive
        archive = open_archive(os.environ['RC2016_RESEARCH'])
        try:
            ids = [i for i in archive.resolve(self.MATERIAL) if archive.entries[i]['type'] == 'material']
            material = asset_data(archive, ids[0])
            graphs = list(dict.fromkeys(r['path'] for r in asset_references(archive, ids[0])
                                        if r['path'].endswith('.materialgraph')))
            datas = [bytes(asset_data(archive, [i for i in archive.resolve(g)
                                                if archive.entries[i]['type'] == 'materialgraph'][0])) for g in graphs]
            return bindings.prepare_translation(datas, material, extract_material_parameters(material),
                                                source_graph_paths=graphs, source_material_path=self.MATERIAL,
                                                vertex_frame=vertex_frame, world_position=world_position)
        finally:
            archive.close()

    def _writer_blockers(self, receipt):
        import json
        for d in receipt['diagnostics']:
            if isinstance(d, dict) and d.get('shader_id') == '0x76c79aa4':
                return {x['reason'] for x in json.loads(d['reason'])}
        self.fail('deferred writer 0x76c79aa4 was not reported')

    def test_frame_removes_the_normal_tangent_and_sign_blockers_only(self):
        before = self._writer_blockers(self._receipt(False))
        after = self._writer_blockers(self._receipt(True))
        frame = {'Unbound engine/interpolant input attribute%d.%d' % (a, c) for a in (2, 3) for c in range(3)}
        self.assertTrue(frame <= before)
        self.assertFalse(frame & after)
        self.assertEqual({'i2f', 'u_sub'} & after, set())
        # The engine world-position inputs remain, so the boulder is still not a ready translation.
        self.assertTrue(any('CB0[' in r or 'POS_' in r for r in after))

    def test_experimental_receipt_binds_frame_view_offset_and_equivalent_passes(self):
        receipt = self._receipt(False, world_position=True)
        self.assertEqual(receipt['status'], 'experimental')
        self.assertEqual(receipt['shader_id'], '0x76c79aa4')
        self.assertEqual(receipt['vertex_frame_inputs'], ['vertex_handedness', 'vertex_normal', 'vertex_tangent'])
        kinds = {n.get('kind') for n in receipt['nodes'] if n['op'] == 'input'}
        self.assertIn('preview_view_offset', kinds)
        self.assertEqual({n['attribute'] for n in receipt['nodes'] if n['op'] == 'input' and n.get('kind') == 'attribute'}, {0})
        # Passes 0 (76C79AA4) and 1 (BA876177) differ in API numbering and association only.
        self.assertEqual(receipt['equivalent_candidates'], 2)
        self.assertLess(receipt['numeric_equivalence']['max_difference'], 1e-9)


if __name__ == '__main__':
    unittest.main()
