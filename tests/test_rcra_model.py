"""Rift Apart model decoder: a synthetic model, plus real-install checks (RCRA_GAME, skipped without it)."""
import math
import os
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rc2016.games.rcra import model as rm
from rc2016.viewer.loader import mesh_payload

GAME = os.environ.get('RCRA_GAME')


def pack_normal(n):
    """Inverse of model.unpack_normals for a unit vector (bit 31 set for +z)."""
    x, y, z = n
    k = math.sqrt(max(0.0, 2.0 / (1.0 + abs(z)))) if abs(z) < 1 else 1.0
    ax, ay = x * k, y * k                       # nx' = ax / w with w = sqrt(1 - q/4), q = ax^2 + ay^2
    qx = round((ax + math.sqrt(2)) / 0.00276483595)
    qy = round((ay + math.sqrt(2)) / 0.00276483595)
    return qx | (qy << 10) | ((1 << 31) if z >= 0 else 0)


def build_model(pad_uv1=True, flags=0x11):
    """Two meshes in two LODs: a quad (LOD 0) and a triangle (LOD 1), with UV1, colours and a joint."""
    positions = [(0, 0, 0), (4096, 0, 0), (4096, 4096, 0), (0, 4096, 0), (0, 0, 0), (2048, 0, 0), (0, 2048, 0)]
    normal = pack_normal((0.0, 0.0, 1.0))
    uvs = [(0, 0), (1024, 0), (1024, 1024), (0, 1024), (0, 0), (512, 0), (0, 512)]
    vertices = b''.join(struct.pack('<4hI2h', x, y, z, 7, normal, u, v) for (x, y, z), (u, v) in zip(positions, uvs))
    indices = struct.pack('<9H', 0, 1, 2, 0, 2, 3, 0, 1, 2)
    strings = b'\0materials/test.material\0testMat\0root\0'
    pool = 16 + 12 * 10                          # 10 sections
    path_ptr, name_ptr, joint_ptr = pool + 1, pool + 1 + len('materials/test.material') + 1, pool + strings.index(b'root')

    def mesh(first_vertex, first_index, index_count, vertex_count):
        return (struct.pack('<IQ', 0, 0x1122334455667788) + bytes(8) + struct.pack('<4I', first_vertex, first_index, index_count, vertex_count)
                + struct.pack('<4H', flags, 0, 0, 0) + bytes(12) + struct.pack('<II', first_vertex, 0))
    built = bytearray(120)
    struct.pack_into('<3f', built, 0x1C, 1.0, 0.0, 0.0)          # position offset
    struct.pack_into('<f', built, 0x2C, 1 / 4096)
    struct.pack_into('<I', built, 0x30, 4)                       # uv scale 1/1024
    look = struct.pack('<16H', 0, 1, 1, 1, *([0] * 12))
    materials = struct.pack('<QQ', path_ptr, name_ptr) + bytes(16)
    joints = struct.pack('<hHHHII', -1, 0, 0, 0, 0xABCD, joint_ptr)
    uv1 = b''.join(struct.pack('<2h', u // 2, v // 2) for u, v in uvs) + (bytes(4) if pad_uv1 else b'')
    colors = bytes([255, 0, 0, 128]) * 7 + (bytes(4) if pad_uv1 else b'')
    sections = {rm.BUILT: bytes(built), rm.VERTICES: vertices, rm.INDEXES: indices,
                rm.MESHES: mesh(0, 0, 6, 4) + mesh(4, 6, 3, 3), rm.LOOK: look, rm.MATERIALS: materials,
                rm.UV1: uv1, rm.COLORS: colors, rm.JOINTS: joints, 0x4CCEA4AD: b'unknown'}
    offset = pool + len(strings)
    table, body = b'', b''
    for tag, data in sections.items():
        table += struct.pack('<III', tag, offset + len(body), len(data))
        body += data
    dat1 = struct.pack('<4sIIHH', b'1TAD', rm.MODEL_MAGIC, offset + len(body), len(sections), 0) + table + strings + body
    header = struct.pack('<III', rm.MODEL_MAGIC, 0, 0) + bytes(24)
    return header + dat1


class SyntheticModelTests(unittest.TestCase):
    def test_geometry_scale_offset_and_uvs(self):
        d = rm.decode_model('test.model', data=build_model(), as_arrays=True)
        self.assertEqual(d['schema'], rm.SCHEMA)
        self.assertEqual(d['mesh_record_count'], 2)
        quad = d['meshes'][0]
        np.testing.assert_allclose(quad['positions'][2], (2.0, 1.0, 0.0))        # 4096/4096 + offset x 1
        np.testing.assert_allclose(quad['normals'], [[0, 0, 1]] * 4, atol=2e-3)
        np.testing.assert_allclose(quad['uv0'][2], (1.0, 0.0))                  # V flipped
        self.assertEqual(quad['uv1'][2], (0.5, 0.5))
        self.assertEqual(quad['triangles'].tolist(), [[0, 1, 2], [0, 2, 3]])
        self.assertAlmostEqual(quad['colors'][0][3], 128 / 255)
        self.assertEqual(quad['material']['path'], 'materials/test.material')
        self.assertEqual(quad['material']['name'], 'testMat')
        self.assertEqual(d['joints'], [dict(index=0, parent=-1, name='root', name_hash='0x0000ABCD')])
        self.assertEqual(d['unsupported_sections'], ['0x4CCEA4AD'])

    def test_lists_mode_matches_arrays(self):
        a = rm.decode_model('t', data=build_model(), as_arrays=True)
        b = rm.decode_model('t', data=build_model(), as_arrays=False)
        np.testing.assert_allclose(np.array(b['meshes'][1]['positions']), a['meshes'][1]['positions'])
        self.assertEqual(b['meshes'][1]['triangles'], [[0, 1, 2]])

    def test_lods_and_viewer_payload(self):
        d = rm.decode_model('t', data=build_model(), as_arrays=True)
        self.assertEqual([lod['count'] for lod in d['looks'][0]['lods']][:3], [1, 1, 0])
        self.assertEqual(mesh_payload(d, 'Highest', 0)['selected_mesh_indices'], [0])
        self.assertEqual(mesh_payload(d, 'Lowest', 0)['selected_mesh_indices'], [1])
        payload = mesh_payload(d, 'Highest', 0)
        self.assertEqual(len(payload['vertices']), 4)
        self.assertIsNone(payload['raw_vectors'])

    def test_global_index_base(self):
        data = bytearray(build_model(flags=0x01))
        with self.assertRaisesRegex(ValueError, 'indices fall outside'):
            rm.decode_model('t', data=bytes(data))   # the second mesh's local indices 0..2 are below its first vertex 4

    def test_padding_and_rejections(self):
        rm.decode_model('t', data=build_model(pad_uv1=False))
        with self.assertRaises(rm.UnsupportedModelError):
            rm.decode_model('t', data=b'\0' * 64)
        data = bytearray(build_model())
        struct.pack_into('<I', data, 36 + 8, 1)       # declared DAT1 size
        with self.assertRaisesRegex(ValueError, 'declares'):
            rm.decode_model('t', data=bytes(data))


@unittest.skipUnless(GAME, 'RCRA_GAME is not set')
class RealModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.games.rcra.source import RcraSource
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = RcraSource(GAME, Path(cls.temp.name) / 'out')

    @classmethod
    def tearDownClass(cls):
        cls.source.close()
        cls.temp.cleanup()

    def decode(self, path):
        key = self.source.resolve(path)[0]
        return rm.decode_for_viewer(self.source, key, path, True)[0]

    def test_ratchet(self):
        d = self.decode('characters/hero/hero_ratchet/hero_ratchet.model')
        self.assertEqual((d['vertex_count'], d['mesh_record_count'], len(d['looks']), len(d['joints'])), (331368, 186, 7, 205))
        self.assertEqual([lod['count'] for lod in d['looks'][0]['lods']], [31] * 6 + [0, 0])
        self.assertIn('hero_Ratchet_Boots', [m['name'] for m in d['materials']])
        self.assertEqual(d['joint_count'], 0)        # until skinning and animation are decoded
        payload = mesh_payload(d, 'Highest', 0)
        height = payload['vertices'][:, 2].max() - payload['vertices'][:, 2].min()
        self.assertAlmostEqual(float(height), 1.39, delta=0.02)
        dots = []
        for m in d['meshes'][:31]:
            p, n, t = m['positions'], m['normals'], m['triangles']
            f = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
            ok = np.linalg.norm(f, axis=1) > 1e-12
            f = f[ok] / np.linalg.norm(f[ok], axis=1)[:, None]
            v = n[t[ok, 0]] + n[t[ok, 1]] + n[t[ok, 2]]
            dots.append((f * v / np.linalg.norm(v, axis=1)[:, None]).sum(1))
        self.assertGreater(float(np.median(np.concatenate(dots))), 0.99)

    def test_geometry_less_model_is_reported(self):
        path = 'environment/megalopolis/architecture/meg_arch_rooftop_garden_01/meg_arch_rooftop_garden_planter_bush_x2/garden_planter_bush_x2_stump.model'
        if not self.source.resolve(path):
            self.skipTest('model not in this install')
        with self.assertRaisesRegex(rm.UnsupportedModelError, 'no geometry'):
            self.decode(path)


if __name__ == '__main__':
    unittest.main()
