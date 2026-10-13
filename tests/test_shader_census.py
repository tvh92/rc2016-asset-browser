"""Shader census grouping and ranking, without game files."""
import json
import unittest

from rc2016.shaders import census


class CensusTests(unittest.TestCase):
    def test_normalise_groups_offsets_and_registers(self):
        self.assertEqual(census.normalise('Control flow unsupported at 1234: S_CBRANCH_EXECZ'),
                         'Control flow unsupported at N: S_CBRANCH_EXECZ')
        self.assertEqual(census.normalise('Uninitialized VGPR 17'), 'Uninitialized VGPR 17')
        self.assertEqual(census.normalise('Unresolved packed export from Uninitialized VGPR 3'),
                         'Unresolved packed export from Uninitialized VGPR N')

    def test_binding_errors_split_into_reasons(self):
        reason = json.dumps([dict(node=4, reason='Unbound engine/interpolant input CB1[3]'),
                             dict(node=9, reason='Unbound engine/interpolant input CB1[3]'), dict(node=2, reason='u_eq')])
        self.assertEqual(census.diagnostic_reasons(dict(shader_id='0x1', contract=0, reason=reason)),
                         {'bind: Unbound engine/interpolant input CB1[3]', 'bind: u_eq'})
        self.assertEqual(census.diagnostic_reasons('Pass contract does not partition resources'),
                         {'Pass contract does not partition resources'})

    def test_malformed_graph_is_classified_not_raised(self):
        row = census.classify_graph(b'not a graph')
        self.assertEqual(row['status'], 'malformed')

    def test_ranking_weights_by_material_usage(self):
        class Archive:
            entries = {1: dict(id=1, path='a.materialgraph', type='materialgraph'),
                       2: dict(id=2, path='b.materialgraph', type='materialgraph'),
                       3: dict(id=3, path='m1.material', type='material'),
                       4: dict(id=4, path='m2.material', type='material')}

            def asset_data(self, key):
                return b'bad'

            def asset_references(self, key):
                return [dict(path='b.materialgraph')]
        report = census.census(Archive())
        self.assertEqual(report['graphs'], 2)
        self.assertEqual(report['graph_status'], {'malformed': 2})
        self.assertEqual(report['per_graph']['b.materialgraph']['materials'], 2)
        self.assertEqual(report['blockers'][0]['materials'], 2)
        self.assertEqual(report['blockers'][0]['examples'][0], 'b.materialgraph')


if __name__ == '__main__':
    unittest.main()
