"""Novalis window glass forward program 8DF14766 (docs/material-decoding/novalis-window-glass.md).

Synthetic checks (rule binding, uniform packing, GLSL text, labels, validator constants) always run. The archive-backed
checks pin the graph and program identity, the slot and CB2 key layout of pass contract 0 and the rule read from the two
Novalis window materials; they skip without RC2016_RESEARCH / RC2016_GAME_ROOT. The closed forms were checked against a
research interpreter outside the repository; nothing here runs the program.
"""
import hashlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import (GLASS_FRAGMENT, GLASS_KINDS, GLASS_UNIFORMS, INSTANCE_ATTRIBUTES, INSTANCED_VERTEX, UV1_ATTRIBUTE,
                                    VERTEX, WINDOW_GLASS_TEXTURES, glass_uniforms, window_glass_uniform)

GRAPH = 'materialgraph/global/basic_glass/jn_glass_parallax_mask.materialgraph'
GRAPH_SHA = 'bc5bb839d7b389638d55b0bb8784798aade5215aa31e5fd692bdf970ffd89c2c'
CODE_SHA = {'0x8df14766': '84ecf283308e609d0c9b652d76345876b056e3c9ed3cc4c52b6a2967a5f70a3d',
            '0x140bfec8': 'c3f17fc8cc0e99ff3faff88e2022eca9f3042e4d5071d0039fa92bef2f765071'}
FOLDER = 'material/environment/kalebo/architecture/kal_arch_glass_parallax_01/'
WINDOW_01 = FOLDER + 'tb_nov_window_parallax_01.material'
WINDOW_02 = FOLDER + 'tb_nov_window_parallax_02.material'
INTERIOR_01 = 'textures/environment/rilgar/architecture/parallax_interior_office_01/parallax_interior_office_01_c.texture'
GLASS_G_01 = 'textures/environment/rilgar/architecture/parallax_interior_office_01/window_glass_painted_01_g.texture'
# Pass-0 CB2 dword of each rule parameter (WINDOW_GLASS_DECODE: C 0..2, gloss 3, Z 4..6, sky 7, T 8..10, ...).
DWORDS = dict(colour=0, gloss=3, mask_tint=4, sky_weight=7, tint=8, parallax_depth=11, glass_weight=12, rim_exp=13,
              f0_weight=14, interior_weight=15, interior_light=16)
PARAMS = dict(tint=(0.1, 0.2, 0.7), gloss=0.95, parallax_depth=0.672, rim_exp=0.006, f0_weight=0.784, interior_weight=1.0,
              interior_light=0.472, interior_srgb=True, colour=(0.0027, 0.5457, 0.8879), glass_weight=0.406,
              mask_tint=(0.6038, 0.3916, 0.1683), sky_weight=0.639, cb0_95=1.0, record_fade=(-2.3333333, 0.0071561))


class RuleBindingTests(unittest.TestCase):
    def test_exact_graph_hash_binds_the_rule(self):
        rule = tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})
        self.assertEqual((rule['forward'], rule['writer'], rule['graph_sha256']), ('window_glass', '8DF14766', GRAPH_SHA))
        self.assertEqual(rule['slots'], {'_n': 0x49A7866B40A7D737, '_pick': 0x49A7866B1576D848})
        self.assertEqual([slot & 0xFFFFFFFF for _, slot in tp.WINDOW_GLASS_SLOTS],
                         [0x0056CBD0, 0x641CFAC2, 0x1576D848, 0x7AE34E7A, 0xABDAD780, 0xB521DFCB, 0x40A7D737])   # API 5..11
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})

    def test_preview_is_labelled(self):
        c = tp.WINDOW_GLASS_CONSTANTS
        self.assertIs(c['game_equivalence'], False)
        self.assertTrue(c['presentation'])
        text = ' '.join(c['limitations'])
        for phrase in ('replaced by the viewer lighting', 'no screen-space reflection', 'no engine light list',
                       '30 %', '2x anisotropic', 'not game-equivalent'):
            self.assertIn(phrase, text)

    def test_kind_and_uniform_packing(self):
        self.assertEqual(GLASS_KINDS['window_glass'], 14)
        self.assertEqual(len(GLASS_KINDS), len(set(GLASS_KINDS.values())))
        kind, a, b, c, d, e = glass_uniforms(dict(kind='window_glass', params=PARAMS))
        self.assertEqual((kind, a, b, c, d, e), (14, (0.1, 0.2, 0.7, 0.95), (0.672, 0.006, 0.784, 1.0),
                                                  (0.472, 1.0, 0.0, 0.0), (0.0027, 0.5457, 0.8879, 0.406),
                                                  (0.6038, 0.3916, 0.1683, 0.639)))
        self.assertEqual(window_glass_uniform(dict(kind='window_glass', params=PARAMS)), (1.0, -2.3333333, 0.0071561, 0.0))
        self.assertEqual(window_glass_uniform(dict(kind='glass_basic', params={})), (0.0,) * 4)
        self.assertEqual(glass_uniforms(dict(kind='window_glass', params=dict(PARAMS, interior_srgb=False)))[3][1], 0.0)

    def test_glsl_branch_carries_the_decoded_equations(self):
        self.assertIn('#extension GL_ARB_shader_texture_lod : enable', GLASS_FRAGMENT.split('uniform')[0])
        branch = GLASS_FRAGMENT.split('uKind == 14')[1].split('uKind == 9')[0]
        for text in ('float rim = sat(4.0 * pow(om, uB.y))', 'vec2 D = uB.x * face * vec2(dot(V, T), -dot(V, B)) / nv',
                     'int count = int(64.0 - 48.0 * sat(nv))', 'texture2DGradARB(uTexN, p, du1, du2).a',
                     '(dPrev * lower - dHit * upper) / (dPrev - dHit)', 'uv11 = vUV - (1.0 - hs) * D',
                     'aw.y >= aw.x && aw.y >= aw.z ? 1 : (aw.x >= aw.z ? 0 : 2)',
                     'vec2(-s * W.z, -W.y)', 'vec2(W.x, s * W.z)', 'vec2(s * W.x, -W.y)',
                     'texture2D(uTexP, wuv)', 'texture2D(uTexWindowC, wuv)', 'texture2D(uTexWindowMask, vUV1).r',
                     'pow(2.0, -5.5 * sqrt(sat(0.5 + uA.w * g7.r - 2.0 * mask)))',
                     'float c = 1.0 - dot(e, e), sg = face * s',
                     '(sqrt(r2 + 0.25 * nn * nn) - 0.5 * nn) / max(r2, 1.0 / 256.0) * vec2(tt, bb)',
                     '2.0 * uWindowP.x * (1.0 - sat(-vPos.z * uWindowP.z + uWindowP.y))',
                     's2 * (texture2D(uTexWindowN2, vUV1).rg - 0.5)',
                     'Tw * (0.5 * z2 * u1.x + z1 * m2.x) + Bw * (0.5 * z2 * u1.y + z1 * m2.y) + wn * (z1 * z2)',
                     'Ns = normalize(vNativeToView * Nn)',
                     'float F = uB.z * g7.g + pow(rim, 10.0)', 'F90 = vec3(sat(60.0 * F))',
                     'vec3 Rs = reflect(-V, Ns)', 'vec3 sky = windowEnvironment(Rs, a)',
                     'uProbeOn ? windowProbeReflection(vTintWorldPos, transpose(vNativeToView) * Rs, sky) : sky', '+ E * F)',
                     'vec3 Q = uE.rgb * mask + uB.w * interior + uD.w * uD.rgb * pow(0.5 + mask, 5.0) * c9 * (1.0 + uE.w * sky)',
                     'color = (uC.x * interior.b + Ld) * Q + spec', 'alpha = 1.0'):
            self.assertIn(text, branch)

    def test_blender_front_end_and_validator_agree(self):
        self.assertEqual(validation.GLASS_CONSTANTS['window_glass'],
                         ('tint', 'gloss', 'rim exp', 'f0 weight', 'interior weight', 'interior light'))
        rule = dict(applies=True, ported=['window_glass'],
                    terms={'window_glass': dict(kind='window_glass', params=PARAMS, blend='opaque', two_sided=False)},
                    textures={'_c': None, '_g': None, '_n': INTERIOR_01, '_pick': GLASS_G_01})
        names, roles = validation._viewer_required(rule, False)
        expected = validation._viewer_expected_constants(rule)
        self.assertTrue(set(names) <= set(expected))
        self.assertEqual(roles, ['_n', '_pick'])
        self.assertEqual(expected['VR glass f0 weight'], 0.784)
        self.assertEqual((expected['VR glass glass weight'], expected['VR glass colour']), (0.406, (0.0027, 0.5457, 0.8879)))
        text = (Path(__file__).resolve().parents[1] / 'rc2016/pipeline/game_shader.py').read_text(encoding='utf-8')
        body = text.split('def _viewer_rule_glass')[1].split('def apply_viewer_graph_rule')[0]
        for name in ('glass tint', 'glass gloss', 'glass rim exp', 'glass f0 weight', 'glass interior weight',
                     'glass interior light', 'glass interior map', 'glass world map', 'VR glass interior',
                     'glass colour', 'glass glass weight', 'glass painted map'):
            self.assertIn(name, body)
        self.assertIn("'window_glass'", text.split('GLASS_VIEWER_KINDS =')[1].split('\n')[0])


class WindowTextureTests(unittest.TestCase):
    def test_extra_roles_units_and_uniforms(self):
        from rc2016.formats.materials import VIEWER_TERM_ROLES
        from rc2016.viewer.viewport import GL_TEXTURE_ROLES, MIPMAP_ROLES
        roles = [role for role, _ in tp.WINDOW_GLASS_ROLES]
        self.assertEqual(tp.WINDOW_GLASS_ROLES, (('_window_mask', 'mask'), ('_window_n1', 'window_normal'),
                                                 ('_window_n2', 'detail_normal'), ('_window_c', 'glass_c')))
        self.assertEqual([row[1] for row in WINDOW_GLASS_TEXTURES], roles)
        self.assertEqual([row[0] for row in WINDOW_GLASS_TEXTURES], [10, 11, 12, 13])
        self.assertEqual([row[4] for row in WINDOW_GLASS_TEXTURES], ['uSrgbWindowMask', None, None, 'uSrgbWindowC'])
        for role in roles:
            self.assertIn(role, GL_TEXTURE_ROLES); self.assertIn(role, MIPMAP_ROLES)
        self.assertEqual(VIEWER_TERM_ROLES['window_glass'], ('_n', '_pick', *roles))
        for row in WINDOW_GLASS_TEXTURES:
            for name in row[2:]:
                if name:
                    self.assertIn(name, GLASS_UNIFORMS); self.assertIn(name, GLASS_FRAGMENT)
        for name in ('uHasUV1', 'uWindowP'):
            self.assertIn(name, GLASS_UNIFORMS)
        self.assertEqual((UV1_ATTRIBUTE, INSTANCE_ATTRIBUTES['aUV1']), (3, 3))
        for source in (VERTEX, INSTANCED_VERTEX):
            self.assertIn('vUV1 = aUV1', source)
            self.assertNotIn('gl_Color', source)   # slot 3 is the colour alias

    def test_secondary_uv_reads_the_second_half_pair(self):
        import numpy as np
        from rc2016.formats.native_model import secondary_uv
        words = np.array([[0x3C00, 0x0000], [0x3800, 0x3C00]], dtype=np.uint16)   # (1, 0), (0.5, 1)
        self.assertEqual(secondary_uv(dict(uv0_secondary_packed=words)).tolist(), [[1.0, 0.0], [0.5, 1.0]])
        self.assertIsNone(secondary_uv(dict(uv0_secondary_packed=None)))


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'RC2016_RESEARCH is required')
class ArchiveRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ.get('RC2016_GAME_ROOT', os.environ['RC2016_RESEARCH']))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import asset_bytes, open_archive
        cls.archive = open_archive(root)
        cls.graph = asset_bytes(cls.archive, GRAPH)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_identities_and_contract_layout(self):
        from rc2016.shaders import translator
        from rc2016.shaders.bindings import pass_contracts
        self.assertEqual(hashlib.sha256(self.graph).hexdigest(), GRAPH_SHA)
        programs = {p['shader_id']: p['code_sha256'] for p in translator.pixel_programs(self.graph)}
        for shader_id, digest in CODE_SHA.items():
            self.assertEqual(programs[shader_id], digest)
        contract = pass_contracts(self.graph)[0]
        self.assertEqual([r['slot_key'] for r in contract['resources']], [slot & 0xFFFFFFFF for _, slot in tp.WINDOW_GLASS_SLOTS])
        keys = {e['key_hash']: e['dword'] for e in contract['table']}
        rule = tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})
        self.assertEqual({name: keys[key] for name, key, _ in rule['params']}, DWORDS)

    def test_rule_binds_both_novalis_window_materials(self):
        for material, gloss, glass_g in ((WINDOW_01, 0.95, GLASS_G_01),
                                          (WINDOW_02, 0.33, 'textures/environment/novalis/architecture/tb_nov_window_glass_01/tb_nov_window_glass_g.texture')):
            paths = tp.material_texture_paths(self.archive, material)
            f = paths['forward']; p = f['params']
            self.assertEqual((f['kind'], f['writer'], f['blend'], f['two_sided'], f['graph_sha256']),
                             ('window_glass', '8DF14766', 'opaque', False, GRAPH_SHA))
            self.assertAlmostEqual(p['gloss'], gloss, places=6)
            self.assertFalse(p['game_equivalence'])
            self.assertTrue(p['interior_srgb'])
            self.assertEqual(sorted(f['slots']), sorted(name for name, _ in tp.WINDOW_GLASS_SLOTS))
            self.assertEqual((paths['_n'], paths['_pick']), (f['slots']['interior'], glass_g))
            self.assertEqual({role: paths[role] for role, _ in tp.WINDOW_GLASS_ROLES},
                             {role: f['slots'][name] for role, name in tp.WINDOW_GLASS_ROLES})
            self.assertFalse(tp.texture_is_srgb(self.archive, paths['_window_mask']))
            self.assertTrue(tp.texture_is_srgb(self.archive, paths['_window_c']))
        p = tp.material_texture_paths(self.archive, WINDOW_01)['forward']['params']
        for name, want in (('tint', (0.0024, 0.1301, 0.8879)), ('colour', (0.0027, 0.5457, 0.8879)), ('mask_tint', (0.6038, 0.3916, 0.1683))):
            self.assertEqual([round(x, 4) for x in p[name]], list(want))
        self.assertEqual([round(p[k], 3) for k in ('sky_weight', 'parallax_depth', 'glass_weight', 'rim_exp', 'f0_weight',
                                                   'interior_weight', 'interior_light')],
                         [0.639, 0.672, 0.406, 0.006, 0.784, 1.0, 0.472])

    def test_unresolved_slot_fails_loudly(self):
        mask = tp.WINDOW_GLASS_SLOTS[0][1]
        real_slots, real_info = tp.texture_slots, tp._graph_info

        def without_mask(data, tag):   # the material's own bindings
            return {k: v for k, v in real_slots(data, tag).items() if k != mask}

        def graph_without_mask(archive, graph_id):   # the graph defaults (cached per archive)
            textures, slots, keys, defaults, roles = real_info(archive, graph_id)
            return textures, {k: v for k, v in slots.items() if k != mask}, keys, defaults, roles
        with patch.object(tp, 'texture_slots', side_effect=without_mask), \
                patch.object(tp, '_graph_info', side_effect=graph_without_mask):
            with self.assertRaisesRegex(ValueError, 'unresolved texture slot.*mask'):
                tp.material_texture_paths(self.archive, WINDOW_01)

    def test_blender_rule_is_ported(self):
        from rc2016.formats.materials import viewer_graph_rule
        rule = viewer_graph_rule(self.archive, WINDOW_01)
        self.assertTrue(rule['applies'])
        self.assertEqual(rule['ported'], ['window_glass'])
        self.assertEqual((rule['textures']['_n'], rule['textures']['_pick']), (INTERIOR_01, GLASS_G_01))


if __name__ == '__main__':
    unittest.main()
