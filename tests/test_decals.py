"""Scorch-mark decals: the merged mesh of the zone decal table (rc2016/levels/decals.py), its shader declarations and the Novalis archive."""
import os
import unittest
from pathlib import Path

import numpy as np

from rc2016.levels import decals
from rc2016.viewer import shading

SCORCH = 'material/visualeffect/equipment/weapon/wpn_Explosion_Scorchmark/wpn_Explosion_Scorchmark.material'
OTHER = 'material/environment/metropolis/architecture/met_arch_decal_03/met_arch_decal_03.material'


def decal(material, offset=0.0):
    """Two triangles over a unit quad at x = ``offset`` (positions, normals, UVs and triangles as zone_format.decode_decals)."""
    return dict(material=material,
                positions=[(offset, 0, 0), (offset + 1, 0, 0), (offset + 1, 1, 0), (offset, 1, 0)],
                normals=[(0, 0, 1)] * 4, uvs=[(0, 0), (1, 0), (1, 0.25), (0, 0.25)], triangles=[(0, 1, 2), (0, 2, 3)])


class ScorchMeshTests(unittest.TestCase):
    def test_material_match_ignores_case_and_separators(self):
        self.assertTrue(decals.is_scorch(SCORCH))
        self.assertTrue(decals.is_scorch(SCORCH.replace('/', '\\')))
        self.assertFalse(decals.is_scorch(OTHER))

    def test_only_scorch_decals_are_merged_with_offset_indices(self):
        positions, normals, uvs, indices = decals.scorch_mesh([decal(SCORCH), decal(OTHER, 5), decal(SCORCH, 9)])
        self.assertEqual((positions.shape, normals.shape, uvs.shape), ((8, 3), (8, 3), (8, 2)))
        self.assertEqual(indices.tolist(), [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7])
        self.assertEqual((positions.dtype, indices.dtype), (np.float32, np.uint32))
        self.assertEqual(positions[4, 0], 9.0)

    def test_uvs_are_as_stored(self):
        _positions, _normals, uvs, _indices = decals.scorch_mesh([decal(SCORCH)])
        np.testing.assert_allclose(uvs[:, 1], [0.0, 0.0, 0.25, 0.25])
        np.testing.assert_allclose(uvs[:, 0], [0, 1, 1, 0])

    def test_no_scorch_decal_gives_an_empty_mesh(self):
        positions, _normals, _uvs, indices = decals.scorch_mesh([decal(OTHER)])
        self.assertEqual((len(positions), len(indices)), (0, 0))


class DecalStateTests(unittest.TestCase):
    def test_every_decal_blends_alpha_over_except_additive(self):
        for blend in ('opaque', 'alpha_over', None):
            self.assertEqual(decals.decal_state(OTHER, blend, 'x.materialgraph'), dict(blend='alpha_over', two_sided=False))
        self.assertEqual(decals.decal_state(OTHER, 'additive_alpha', '')['blend'], 'additive')


class ScorchShaderTests(unittest.TestCase):
    def test_program_declares_what_the_fragment_reads(self):
        self.assertIn('uniform sampler2D uDecalMask; uniform sampler2D uDecalEmissive; uniform vec3 uEmber;', shading.SCORCH_FRAGMENT)
        self.assertEqual(shading.SCORCH_UNIFORMS, ('uDecalMask', 'uDecalEmissive', 'uEmber'))
        self.assertTrue(any('uniform int uAlphaOut;' in v for v in vars(shading).values() if isinstance(v, str)))


class ScorchArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_RESEARCH')
        if not research:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        from rc2016.levels import scene
        try:
            cls.archive = scene.open_archive(Path(research))
            cls.zone = 'levels/Novalis/openArea/OpenWorld/default_region/art_terrain.zone'
            cls.scene = scene.zone_scene(cls.archive, cls.zone)
        except (OSError, ValueError):
            raise unittest.SkipTest('Source archive not available')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_pit_decal_lies_flat_on_the_crater_patch_and_faces_up(self):
        from rc2016.levels.scene import asset_bytes
        from rc2016.levels.zone_format import decode_decals
        decal = decode_decals(asset_bytes(self.archive, self.zone), asset_bytes(self.archive, self.zone + '_static'))[1]
        positions, normals = np.asarray(decal['positions']), np.asarray(decal['normals'])
        self.assertTrue(decals.is_scorch(decal['material']))
        np.testing.assert_allclose(positions.min(0), [162.28, 40.69, 121.54], atol=0.05)     # signed positions: 0.65 deep, not a 3-unit cube
        np.testing.assert_allclose(positions.max(0), [166.98, 41.34, 126.24], atol=0.05)
        self.assertGreater(normals[:, 1].mean(), 0.8)                                       # box-frame normals turned to world: up
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-6)
        self.assertEqual(np.asarray(decal['projector']).shape, (4, 4))

    def test_novalis_terrain_loads_the_scorch_decals_over_the_craters(self):
        loaded = decals.load_scorch_decals(self.archive, [self.zone])
        self.assertEqual(loaded['count'], 6)
        self.assertEqual((loaded['mask']['width'], loaded['emissive']['format']), (1024, 80))
        positions, _normals, uvs, indices = loaded['mesh']
        self.assertEqual(len(positions), len(uvs))
        self.assertEqual(int(indices.max()) + 1, len(positions))
        self.assertTrue(np.isfinite(positions).all())
        crater = next(m for m in self.scene['models'] if 'nov_gnd_crater_01' in m['path'].lower())
        centres = np.concatenate([np.frombuffer(g, 'f4').reshape(-1, 16)[:, 12:15] for g in crater['groups'].values()])
        # The pits whose decal is a wpn_explosion_scorchmark (three of the six; the others carry the crate, Pyrocitor or building
        # scorch materials, which are not drawn) have mesh vertices within the decal's radius (at most 3.5) of their centre.
        near = sum(1 for centre in centres if np.linalg.norm(positions - centre, axis=1).min() < 4.0)
        self.assertGreaterEqual(near, 3)

    def test_other_decal_materials_become_one_synthetic_model_per_zone_and_material(self):
        models, undrawn = decals.decal_models(self.archive, [self.zone])
        self.assertGreater(len(models), 5)
        self.assertTrue(all(m['path'].startswith(decals.MODEL_PREFIX) and m['zone'] == self.zone for m in models))
        self.assertEqual(len({m['path'] for m in models}), len(models))
        for model in models:
            geometry = model['geometry']
            self.assertEqual(len(geometry['positions']) % 3, 0)
            self.assertEqual(geometry['decal']['blend'], 'alpha_over')
            self.assertEqual(geometry['batches'], [(0, 0, geometry['triangles'], True)])
            self.assertEqual(list(model['groups'].values())[0].tolist()[::5], [1, 1, 1, 1])   # identity row
            self.assertNotIn(model['path'].rsplit('|', 1)[-1].lower(), undrawn)
        paths = {m['path'].rsplit('|', 1)[-1].lower() for m in models}
        self.assertFalse(any(decals.is_scorch(p) for p in paths))
        self.assertTrue(any('moss' in p for p in paths))


if __name__ == '__main__':
    unittest.main()
