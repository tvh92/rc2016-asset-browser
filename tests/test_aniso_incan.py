"""basic_normal_gloss_aniso_incan: the anisotropic lobe of forward program 33C21955 (bng kind 5).

Synthetic data only: no archive, no GPU, no Blender.
"""
import math
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rc2016.formats.materials import viewer_graph_rule
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT
from tests.test_viewer_textures import FakeArchive, dat1, slot_rows

GRAPH = 'required/materials/basic_normal_gloss_aniso_incan.materialgraph'
GLOSS_T, GLOSS_B, STALE_GLOSS = 0x015FEBB3, 0x9856BA09, 0x24F4CA4C
DEFAULTS = {GLOSS_B: 1.0, 0x62835AF9: 1.0, GLOSS_T: 1.0, 0x49AFC3A0: 0.75, 0x75D37A23: 1.0,
            0xAD4069BA: 1.0, 0x06F19114: 1.0, 0x34493800: 1.0}   # the graph's own table


def row(key, value):
    return dict(key_hash=key, size=4, raw_hex=struct.pack('<f', value).hex())


def extract(material_rows=()):
    c, g, n = 0x49A7866BABDAD780, 0x49A7866B1576D848, 0x49A7866B7AE34E7A
    graph = dat1(['t/c.texture', 't/g.texture', 't/n.texture'],
                 lambda at: [(tp.GRAPH_SLOT_SECTION, slot_rows([(at[0], c), (at[1], g), (at[2], n)]))])
    archive = FakeArchive({'m/wing.material': ('material', dat1([GRAPH], lambda at: [])), GRAPH: ('materialgraph', graph)})
    params = dict(parameters=[], unsupported_parameters=list(material_rows))
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/wing.material'), params


class AnisoRuleTests(unittest.TestCase):
    def test_wingpack_and_jetpack_axis_scales(self):
        # hero_clank_wingpack: T 0.7, B 0.0 and a stale 0x24F4CA4C = 1.0; hero_clank_jetpack: T 0.2, B 0.5.
        for rows, want in (([row(STALE_GLOSS, 1.0), row(GLOSS_T, 0.7), row(GLOSS_B, 0.0)], (0.7, 0.0)),
                           ([row(GLOSS_T, 0.2), row(GLOSS_B, 0.5)], (0.2, 0.5)), ([], (1.0, 1.0))):
            with self.subTest(want=want):
                _, got, _ = extract(rows)
                self.assertEqual(got['bng']['kind'], 5)
                self.assertAlmostEqual(got['bng']['params'][0], want[0], places=6)
                self.assertAlmostEqual(got['bng']['params'][1], want[1], places=6)
                self.assertEqual(got['bng']['params'][2:], (0.0, 0.0))
                # 'gloss' stays the deferred writer F9719EF2's isotropic reading (0x9856BA09), never the stale key.
                self.assertAlmostEqual(got['gloss'], want[1], places=6)
                self.assertEqual(got['g_mode'], 'grey')

    def test_glsl_lobe(self):
        for text in ('vec3 ggxAniso(', '0.75 * min(aT, aB) + 0.25 * max(aT, aB)', '0.3535533 * (anisoVisAlpha(aT, aB) + 1.0)',
                     'd = th * th + bh * bh + NdH * NdH', '1.0 / (PI * aT * aB * d * d',
                     'sqrt(clamp(uBngP.x * gl.r, 0.0, 1.0)), gB = sqrt(clamp(uBngP.y * gl.r, 0.0, 1.0))',
                     'exp2(-5.5 * gT), exp2(-5.5 * gB)', '(uBngKind == 5 || uBngKind == 8 || uBngKind == 12) && dot(vTangent'):
            self.assertIn(text, FRAGMENT)

    def test_lobe_normalisation(self):
        # The decoded D integrates to 1 over projected microfacet area (int D (N.H) dw = 1) for the wing axes.
        def integral(aT, aB, steps=600):
            total = 0.0
            for i in range(steps):
                theta = (i + 0.5) / steps * math.pi / 2
                for j in range(steps):
                    phi = (j + 0.5) / steps * 2 * math.pi
                    h = (math.sin(theta) * math.cos(phi), math.sin(theta) * math.sin(phi), math.cos(theta))
                    d = (h[0] / aT) ** 2 + (h[1] / aB) ** 2 + h[2] ** 2
                    total += h[2] / (math.pi * aT * aB * d * d) * math.sin(theta)
            return total * (math.pi / 2 / steps) * (2 * math.pi / steps)
        a_t = 2.0 ** (-5.5 * math.sqrt(0.7 * 0.69))   # wing T axis at the mean _g.R; B is 1
        self.assertAlmostEqual(integral(a_t, 1.0), 1.0, places=2)
        self.assertAlmostEqual(integral(0.3, 0.3), 1.0, places=2)

    def test_viewer_rule_ports_the_lobe(self):
        archive, _, params = extract([row(GLOSS_T, 0.7), row(GLOSS_B, 0.0)])
        with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
              patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
            rule = viewer_graph_rule(archive, 'm/wing.material', None)
        self.assertTrue(rule.get('applies'), rule)
        self.assertIn('bng', rule['ported'])
        self.assertAlmostEqual(rule['terms']['bng']['params'][0], 0.7, places=6)
        self.assertEqual(rule['normal_z'], '1-x*x-y*y')
        self.assertEqual(validation._viewer_required(rule, True)[0], [])   # the axis scales are group inputs, not constants


def socket(value=None, links=()):
    return SimpleNamespace(default_value=value, links=list(links), is_linked=bool(links))


def node(name, label='', kind='MATH', inputs=None, **extra):
    inputs = inputs or {}
    return SimpleNamespace(name=name, label=label, type=kind, inputs=Inputs(inputs), **extra)


class Inputs(dict):
    def __iter__(self):
        return iter(self.values())


def fake_brdf(gloss_b=0.0, uv_map='UV0', labels=True):
    link = lambda n: SimpleNamespace(from_node=n)
    r_b = node('r_B', 'r_B = 2^(-2.75 g_B)' if labels else 'other')
    r_t = node('r_T', 'roughness = 2^(-2.75 g)')
    rough = node('r', 'r = sqrt(r_T r_B): alpha_x alpha_y = alpha_T alpha_B', inputs={'a': socket(links=[link(r_t), link(r_b)])})
    tangent = node('t', kind='TANGENT', direction_type='UV_MAP', uv_map=uv_map)
    spec = node('Game GGX specular (_g)', kind='BSDF_PRINCIPLED', distribution='GGX', inputs={
        'Roughness': socket(links=[link(rough)]), 'Anisotropic': socket(links=[link(rough)]),
        'Anisotropic Rotation': socket(links=[link(r_b)]), 'Tangent': socket(links=[link(tangent)])})
    tree = SimpleNamespace(nodes={spec.name: spec})
    return SimpleNamespace(inputs={'Gloss scale B (0x9856BA09)': socket(gloss_b)}, node_tree=tree)


class AnisoValidationTests(unittest.TestCase):
    MATERIAL = {'rc_shader_frontend_limitation': 'aniso 33C21955: PREVIEW Principled mapping'}

    def test_group_check_passes(self):
        validation._check_aniso_group(fake_brdf(0.0), (0.7, 0.0, 0.0, 0.0), self.MATERIAL)

    def test_group_check_rejects(self):
        for brdf, params, material in ((fake_brdf(0.5), (0.7, 0.0), self.MATERIAL),
                                       (fake_brdf(uv_map='UV1'), (0.7, 0.0), self.MATERIAL),
                                       (fake_brdf(labels=False), (0.7, 0.0), self.MATERIAL),
                                       (fake_brdf(), (0.7, 0.0), {})):
            with self.assertRaises(AssertionError):
                validation._check_aniso_group(brdf, params, material)


if __name__ == '__main__':
    unittest.main()
