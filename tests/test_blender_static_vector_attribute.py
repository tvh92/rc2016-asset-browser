"""Focused contract checks for the opt-in packed-vector Blender input."""
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from rc2016.shaders.nodes import (
    STATIC_VECTOR_ATTRIBUTE,
    _validate_static_vector_mesh_attribute,
)


def _mesh_user(material, *, attribute=None, vertex_count=3):
    attrs = {} if attribute is None else {STATIC_VECTOR_ATTRIBUTE: attribute}
    mesh = SimpleNamespace(
        attributes=SimpleNamespace(get=lambda name: attrs.get(name)),
        vertices=[object() for _ in range(vertex_count)],
    )
    return SimpleNamespace(
        type="MESH", data=mesh, name="fixture",
        material_slots=[SimpleNamespace(material=material)],
    )


class PackedVectorAttributeTests(unittest.TestCase):
    def validate(self, objects, material):
        fake_bpy = SimpleNamespace(data=SimpleNamespace(objects=objects))
        with patch.dict(sys.modules, {"bpy": fake_bpy}):
            return _validate_static_vector_mesh_attribute(material)

    def test_accepts_exact_point_float_vector_attribute_and_reports_limits(self):
        material = object()
        attr = SimpleNamespace(domain="POINT", data_type="FLOAT_VECTOR", data=[1, 2, 3])
        receipt = self.validate([_mesh_user(material, attribute=attr)], material)
        self.assertEqual(receipt["name"], STATIC_VECTOR_ATTRIBUTE)
        self.assertEqual(receipt["mesh_user_count"], 1)
        self.assertEqual(receipt["vertex_counts"], [3])
        self.assertEqual(receipt["source_binding_proven"], False)
        self.assertEqual(receipt["game_equivalence"], False)

    def test_rejects_missing_attribute(self):
        material = object()
        with self.assertRaisesRegex(AssertionError, "lacks required"):
            self.validate([_mesh_user(material)], material)

    def test_rejects_wrong_domain_or_storage_type(self):
        material = object()
        for domain, data_type in (("CORNER", "FLOAT_VECTOR"), ("POINT", "FLOAT_COLOR")):
            with self.subTest(domain=domain, data_type=data_type):
                attr = SimpleNamespace(domain=domain, data_type=data_type, data=[1, 2, 3])
                with self.assertRaisesRegex(AssertionError, "must be POINT/FLOAT_VECTOR"):
                    self.validate([_mesh_user(material, attribute=attr)], material)

    def test_rejects_attribute_length_not_matching_mesh_vertices(self):
        material = object()
        attr = SimpleNamespace(domain="POINT", data_type="FLOAT_VECTOR", data=[1, 2])
        with self.assertRaisesRegex(AssertionError, "length differs"):
            self.validate([_mesh_user(material, attribute=attr)], material)

    def test_requires_a_mesh_user_of_this_material(self):
        material = object()
        attr = SimpleNamespace(domain="POINT", data_type="FLOAT_VECTOR", data=[1, 2, 3])
        other_material = object()
        with self.assertRaisesRegex(AssertionError, "No mesh object uses"):
            self.validate([_mesh_user(other_material, attribute=attr)], material)


if __name__ == "__main__":
    unittest.main()
