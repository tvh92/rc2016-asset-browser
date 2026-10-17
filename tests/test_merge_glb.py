import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rc2016.export.merge_glb import merge_glb, read_glb
from rc2016.export.models import GLTF, write_glb


def tiny_model(path, name):
    """A one-triangle model with a texture, a material, a node and a skin-free animation."""
    gltf = GLTF('test')
    positions = gltf.accessor(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32), 'VEC3', target=34962, minmax=True)
    indices = gltf.accessor(np.array([0, 1, 2], np.uint16), 'SCALAR', target=34963)
    times = gltf.accessor(np.array([0.0, 1.0], np.float32), 'SCALAR')
    values = gltf.accessor(np.array([[0, 0, 0], [0, 0, 1]], np.float32), 'VEC3')
    texture = gltf.image(name, b'\x89PNG\r\n\x1a\n' + b'x' * 8)
    gltf.doc['materials'].append(dict(name=name, pbrMetallicRoughness=dict(baseColorTexture=dict(index=texture))))
    gltf.doc['meshes'].append(dict(name=name, primitives=[dict(attributes=dict(POSITION=positions), indices=indices, material=0)]))
    mesh_node = gltf.node(name=name, mesh=0)
    gltf.doc['animations'] = [dict(samplers=[dict(input=times, output=values, interpolation='LINEAR')],
                                   channels=[dict(sampler=0, target=dict(node=mesh_node, path='translation'))])]
    gltf.doc['scenes'][0]['nodes'].append(mesh_node)
    doc, blob = gltf.finish(embed=True)
    write_glb(path, doc, blob)


class MergeGlbTests(unittest.TestCase):
    def test_merged_indices_point_at_each_models_own_data(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second, out = (Path(folder) / name for name in ('a.glb', 'b.glb', 'combined.glb'))
            tiny_model(first, 'alpha')
            tiny_model(second, 'beta')
            self.assertEqual(merge_glb([first, second], out), 2)
            doc, blob = read_glb(out)
        self.assertEqual([n['name'] for n in doc['nodes']], ['alpha', 'beta'])
        self.assertEqual(doc['scenes'][0]['nodes'], [0, 1])
        self.assertEqual(doc['meshes'][1]['primitives'][0]['material'], 1)
        self.assertEqual(doc['materials'][1]['pbrMetallicRoughness']['baseColorTexture']['index'], 1)
        self.assertEqual(doc['textures'][1]['source'], 1)
        self.assertEqual(doc['animations'][1]['channels'][0]['target']['node'], 1)
        second_positions = doc['accessors'][doc['meshes'][1]['primitives'][0]['attributes']['POSITION']]
        view = doc['bufferViews'][second_positions['bufferView']]
        self.assertEqual(view['byteLength'], 36)
        self.assertEqual(np.frombuffer(blob[view['byteOffset']:view['byteOffset'] + 36], np.float32).reshape(3, 3)[1].tolist(),
                         [1.0, 0.0, 0.0])
        self.assertEqual(doc['buffers'][0]['byteLength'], len(blob))
        for view in doc['bufferViews']:
            self.assertLessEqual(view['byteOffset'] + view['byteLength'], len(blob))


if __name__ == '__main__':
    unittest.main()
