import unittest

from rc2016.export.model_collections import assign_models


def record(name, tag=None, parent=None, children=(), armatures=()):
    return dict(name=name, tag=tag, parent=parent, children=list(children), armatures=list(armatures))


class AssignModelsTests(unittest.TestCase):
    def test_tagged_objects_keep_their_model(self):
        owners = assign_models([record('mesh_a', 'alpha'), record('mesh_b', 'beta')])
        self.assertEqual(owners, {'mesh_a': 'alpha', 'mesh_b': 'beta'})

    def test_an_untagged_armature_joins_the_model_of_the_mesh_it_deforms(self):
        owners = assign_models([record('alpha_skeleton'),
                                record('body', 'alpha', armatures=['alpha_skeleton'])])
        self.assertEqual(owners['alpha_skeleton'], 'alpha')

    def test_children_and_parents_inherit_a_model(self):
        owners = assign_models([record('root', 'beta', children=['leaf']), record('leaf', parent='root')])
        self.assertEqual(owners['leaf'], 'beta')

    def test_objects_with_no_model_stay_unassigned(self):
        owners = assign_models([record('Camera'), record('mesh', 'alpha')])
        self.assertIsNone(owners['Camera'])
        self.assertEqual(owners['mesh'], 'alpha')

    def test_a_parent_cycle_does_not_hang(self):
        owners = assign_models([record('a', parent='b'), record('b', parent='a')])
        self.assertEqual(set(owners), {'a', 'b'})


if __name__ == '__main__':
    unittest.main()
