"""Rift Apart materialgraph programs through the SM5 translator, on the real install (RCRA_GAME).

The glass check is the automatic path against the hand decode (rc2016/viewer/shading.py, uKind 26) on the trooper dome:
real material values and textures, the forward program translated under the neutral engine profile, all lighting zero
and the per-draw "lit" render flag set, so the colour is the program's emissive term.
"""
import io
import math
import os
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import numpy as np

from rc2016.games.rcra import material as rmat
from rc2016.games.rcra import shaders as rshaders
from rc2016.shaders import sm5

GAME = os.environ.get('RCRA_GAME')
DOME = 'characters/enemy/enm_neo_nefarious_trooper/materials/enm_neo_nefarious_trooper_dome.material'
BASIC = 'required/materials/basic_normal_gloss.materialgraph'
SRGB_AS_UNORM = {72: 71, 75: 74, 78: 77, 99: 98, 29: 28, 91: 87}
RENDER_FLAG_LIT = 0x400   # MatDrawCB.m_RenderFlags: clear, the forward program's lighting term is exactly 1


def rgba(texture):
    """The top mip as float RGBA, sRGB formats linearised as the sampler would."""
    from PIL import Image
    from rc2016.formats.texture import dds_bytes
    fmt = SRGB_AS_UNORM.get(texture['format'], texture['format'])
    image = Image.open(io.BytesIO(dds_bytes(texture['width'], texture['height'], fmt, texture['pixels'])))
    data = np.asarray(image.convert('RGBA'), dtype=np.float32) / 255.0
    if texture['srgb']:
        rgb = data[..., :3]
        data[..., :3] = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return data


def texel(image, u, v):
    h, w = image.shape[:2]
    return image[int(math.floor(v * h)) % h, int(math.floor(u * w)) % w]


def hand_glass(cb, c, n, p, N, T, B, V):
    """The hand decode with lighting zero: (rgb, alpha)."""
    _soft, adapt, _gloss, rim_w, k, _mode, emission, floor_ = cb[:8]
    x, y = 2 * n[0] - 1, 2 * n[1] - 1
    Ns = N * (1 - x * x - y * y) + T * x + B * y
    Ns /= np.linalg.norm(Ns)
    s = 1 - min(max(float(Ns @ V), 0.0), 1.0)
    rw = 0.5 * rim_w
    rim = min(max((s ** 4 - rw) / rw, 0.0), 1.0)
    alpha = min(max(max(rim, floor_) + p[0], 0.0), 1.0)
    E = emission * (1 - 0.5 * rim) * (4.0 if adapt else 1.0)   # adapted luminance 1
    return c[:3] * (1 + alpha * (k - 1)) * E, alpha


def camera_inputs(u, v, N, T, sign, V):
    """Interpolants and viewport constants with view space = world space and P_view = d (X, Y, 1)."""
    d = 3.0
    X, Y = V[0] / V[2], V[1] / V[2]
    inputs = defaultdict(float)
    inputs.update({'TEXCOORD.x': u, 'TEXCOORD.y': v, 'TEXCOORD.w': 1.0, 'TEXCOORD2.x': N[0], 'TEXCOORD2.y': N[1],
                   'TEXCOORD2.z': N[2], 'TEXCOORD2.w': 0.25, 'TEXCOORD3.x': T[0], 'TEXCOORD3.y': T[1], 'TEXCOORD3.z': T[2],
                   'TEXCOORD3.w': sign, 'SV_POSITION.x': X, 'SV_POSITION.y': Y, 'SV_POSITION.z': 0.5, 'SV_POSITION.w': d,
                   'SV_IsFrontFace.x': 0xFFFFFFFF, 'GlobalWorldCBuffer.m_AdaptedLuminance': 1.0,
                   'GlobalViewportCBuffer.m_CustomClipPlane+12': 1.0, 'GlobalViewportCBuffer.m_FarClip': 1000.0,
                   'GlobalViewportCBuffer.m_NearClip': 0.1})
    for name in ('m_InvDimensions', 'm_InvDimensions+4', 'm_ScreenToViewA', 'm_ScreenToViewA+4', 'm_ScreenToViewB',
                 'm_ViewToWorldMat', 'm_ViewToWorldMat+20', 'm_ViewToWorldMat+40', 'm_ViewToWorldMat+60'):
        inputs['GlobalViewportCBuffer.' + name] = 1.0
    return inputs


@unittest.skipUnless(GAME, 'RCRA_GAME is not set')
class RealShaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.games.rcra.source import RcraSource
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = RcraSource(GAME, Path(cls.temp.name) / 'out')

    @classmethod
    def tearDownClass(cls):
        cls.source.close()
        cls.temp.cleanup()

    def asset(self, path, kind):
        return self.source.asset_data(next(k for k in self.source.resolve(path) if self.source.entries[k]['type'] == kind))

    def test_pass_table(self):
        graph = self.asset(rmat.ARCH_GLASS_GRAPH, 'materialgraph')
        table = rshaders.pass_table(graph)
        self.assertEqual(sorted(table), [1, 4, 5, 8, 11, 15, 16])
        sm5_blob, dxil_blob = rshaders.pass_blobs(graph, 1)
        self.assertEqual((len(sm5_blob), len(dxil_blob)), (83032, 56311))
        self.assertEqual([rshaders.pass_shader(graph, s).stage for s in (1, 4, 16)], ['ps', 'ps', 'gs'])

    def test_glass_forward_program_matches_the_hand_decode(self):
        graph = self.asset(rmat.ARCH_GLASS_GRAPH, 'materialgraph')
        material = self.asset(DOME, 'material')
        cb2 = rshaders.material_constants(graph, material)
        self.assertEqual([round(x, 4) for x in cb2[:8]], [1.0, 1.0, 0.9, 0.2, 0.5, 1.0, 15.0, 0.4])
        from rc2016.games.rcra.texture import read_top_mip
        units = {name: rgba(read_top_mip(self.source, path, full=False))
                 for name, path in rshaders.material_units(graph, material).items()}
        self.assertEqual(sorted(units), ['textureUnit5', 'textureUnit6', 'textureUnit7', 'textureUnit8'])
        tr = rshaders.translate(rshaders.pass_shader(graph, rshaders.SLOT_FORWARD))
        self.assertEqual(tr.status(), 'ready')
        roots = [tr.output(0, c) for c in range(4)]
        constants = defaultdict(float, {('MatDrawCB', 0): sm5.bits_float(RENDER_FLAG_LIT)})
        rng = np.random.default_rng(7)
        checked = 0
        while checked < 40:
            u, v = rng.random(2)
            V = rng.normal(size=3)
            V /= np.linalg.norm(V)
            if V[2] > -0.2:
                continue
            N = rng.normal(size=3)
            N /= np.linalg.norm(N)
            if N @ V < 0:
                N = -N
            if N @ V < 0.02:
                continue
            T = np.cross(N, rng.normal(size=3))
            T /= np.linalg.norm(T)
            sign = rng.choice([-1.0, 1.0])
            inputs = camera_inputs(u, v, N, T, sign, V)
            inputs.update({rshaders.constant_name(i): value for i, value in enumerate(cb2)})
            texels = {name: texel(image, u, v) for name, image in units.items()}
            got = sm5.evaluate(tr.graph, roots, inputs,
                               lambda node, args: [float(x) for x in texels.get(node['binding'], (0, 0, 0, 0))], constants)
            rgb, alpha = hand_glass(cb2, texels['textureUnit6'], texels['textureUnit7'], texels['textureUnit5'],
                                    N, T, sign * np.cross(N, T), V)
            self.assertAlmostEqual(got[roots[3]], alpha, places=5)
            for c in range(3):
                self.assertAlmostEqual(got[roots[c]], rgb[c], delta=1e-5 * max(1.0, abs(rgb[c])))
            checked += 1

    def test_basic_normal_gloss_gbuffer_program(self):
        tr = rshaders.translate(rshaders.pass_shader(self.asset(BASIC, 'materialgraph'), rshaders.SLOT_GBUFFER))
        self.assertEqual(tr.status(), 'ready')
        self.assertEqual(len([k for k in tr.outputs if k[0] == 'o']), 11)
        g = tr.graph
        # Target 1 rgb = base colour (unit 5) times the material tint (cb2 dwords 0..2); alpha = occlusion from unit 8 blue.
        inputs = defaultdict(float, {rshaders.constant_name(i): x for i, x in enumerate((0.5, 2.0, 4.0, 1.0, 1.0, 0.0, 0.25))})
        values = sm5.evaluate(g, [tr.output(1, c) for c in range(4)], inputs,
                              lambda node, args: [0.5, 0.25, 0.75, 1.0] if node['binding'] in ('textureUnit5', 'textureUnit8')
                              else [0.0, 0.0, 0.0, 0.0])
        self.assertEqual([values[tr.output(1, c)] for c in range(4)], [0.25, 0.5, 3.0, 0.9375])


if __name__ == '__main__':
    unittest.main()
