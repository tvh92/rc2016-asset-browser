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

    def _receipt(self, vertex_frame):
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
                                                vertex_frame=vertex_frame)
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


if __name__ == '__main__':
    unittest.main()
