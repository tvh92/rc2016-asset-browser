"""Mrs. Zurkon's own graph (enm_mrs_zurkon.materialgraph): forward program 2F0F9452 read as a viewer rule.

The rule tests use synthetic data. The archive test reads the game archive (RC2016_RESEARCH) and checks the graph,
program and material identities and the stored values the rule depends on.
"""
import hashlib
import os
import struct
import unittest
from unittest.mock import patch

from rc2016.formats.materials import viewer_graph_rule
from rc2016.shaders import validation
from rc2016.viewer import textures as tp
from rc2016.viewer.shading import FRAGMENT, OPAQUE_UNIFORMS
from tests.test_viewer_textures import FakeArchive, dat1

GRAPH = 'materialgraph/character/enemy/enm_mrs_zurkon/enm_mrs_zurkon.materialgraph'
MATERIAL = 'material/character/enemy/enm_mrs_zurkon_remix/enm_mrs_zurkon_remix.material'
GRAPH_SHA = '33c7fd2cf4991229c5f79eb498c0e513707a06963e464b00c1d99f943697e38b'
PROGRAM_SHA = 'e0f22a4b4c4e30ef67afa978f0f055ac1398370809431e4d780b2e8252351fad'
SIBLING_SHA = 'eb371f900ae42e2900eb25fc7ac836ce6a84261fd96d8441257e1ad2ab666ca3'
# Graph parameter table defaults (both passes carry the same table).
DEFAULTS = {0x3FC4F8A5: [1.0, 1.0, 1.0], 0x223DC13C: 1.0, 0x4B804EF2: 1.0, 0x6424A3B7: 0.0, 0xFD2DF20D: 0.0,
            0x9BFF641D: 0.5, 0x9ACE7120: 2.0, 0x07CC3BD9: 1.0, 0x3DD27B1C: 1.0}
# Remix material values.
REMIX = {0x4B804EF2: 2.0, 0x223DC13C: 1.5, 0x9BFF641D: 1.0, 0x9ACE7120: 4.0, 0x07CC3BD9: 1.5, 0x6424A3B7: 0.0, 0xFD2DF20D: 0.0}
SLOTS = {0x49A7866B7AE34E7A: 't/z_n.texture', 0x49A7866B1576D848: 't/z_g.texture', 0x49A7866BBD3C1D2D: 't/z_g.texture',
         0x49A7866B4AC18E9F: 't/z_cma.texture', 0x49A7866B93CA35AB: 't/z_g.texture',
         0x49A7866B0056CBD0: 't/z_specularcolor_c.texture', 0x49A7866BABDAD780: 't/z_c.texture',
         0x49A7866B39DBF715: 't/z_c.texture', 0x49A7866B2EA0E356: 't/z_c.texture'}


def row(key, values):
    values = tuple(values) if isinstance(values, (tuple, list)) else (values,)
    return dict(key_hash=key, size=4 * len(values), raw_hex=struct.pack('<%df' % len(values), *values).hex())


def extract(values=REMIX, sha=None):
    graph_blob = dat1([], lambda at: [])
    mat = dat1([GRAPH], lambda at: [])
    archive = FakeArchive({'m/z.material': ('material', mat), GRAPH: ('materialgraph', graph_blob)})
    digest = sha or hashlib.sha256(b'1TAD' + graph_blob).hexdigest()
    rules = tuple((p, dict(r, _graph_sha256=digest) if r.get('zurkon') else r) for p, r in tp.GRAPH_RULES)
    params = dict(parameters=[], unsupported_parameters=[row(k, v) for k, v in values.items()])
    with (patch.object(tp, 'GRAPH_RULES', rules),
          patch.object(tp, 'texture_slots', side_effect=lambda data, tag: dict(SLOTS)),
          patch('rc2016.shaders.families.graph_parameter_tables', return_value=([[dict(key_hash=k) for k in DEFAULTS]], DEFAULTS)),
          patch('rc2016.formats.materials.extract_material_parameters', return_value=params)):
        return archive, tp.material_texture_paths(archive, 'm/z.material')


class MrsZurkonRuleTests(unittest.TestCase):
    def test_rule_is_bound_to_the_exact_graph_hash(self):
        self.assertEqual(tp.MRS_ZURKON_GRAPH_SHA256, GRAPH_SHA)
        self.assertTrue(tp._graph_rule([GRAPH], {GRAPH: GRAPH_SHA})['zurkon'])
        self.assertEqual(tp._graph_rule([GRAPH], {GRAPH: '0' * 64}), {})
        self.assertEqual(tp._graph_rule([GRAPH], {}), {})
        self.assertIsNone(extract(sha='0' * 64)[1].get('zurkon'))

    def test_remix_values_slots_and_cyan_glow(self):
        _, got = extract()
        z = got['zurkon']
        self.assertEqual((got['g_mode'], got['k'], got['spec'], got['gloss'], got['linear_z']), ('grey', 1.0, 1.0, 1.0, True))
        self.assertEqual((got['_c'], got['_g'], got['_n'], got['_h']),
                         ('t/z_c.texture', 't/z_g.texture', 't/z_n.texture', 't/z_specularcolor_c.texture'))
        self.assertEqual((z['f0_scale'], z['gloss_scale'], z['rim_weight'], z['rim_exponent'], z['facing_exponent']),
                         (1.5, 2.0, 1.0, 4.0, 1.5))
        self.assertEqual(z['glow_colour'], [0.0, 0.28314873576164246, 1.0])   # key 0xFD2DF20D = 0: cyan
        self.assertEqual((z['glow_gain'], z['texture_tint'], z['writer'], z['game_equivalence']), (20.0, True, '2F0F9452', False))
        self.assertNotIn('dissolve term', z['limitation'])

    def test_blend_key_turns_the_glow_red_and_dissolve_is_labelled(self):
        z = extract({**REMIX, 0xFD2DF20D: 1.0, 0x6424A3B7: 0.5, 0x3DD27B1C: 0.0})[1]['zurkon']
        self.assertEqual(z['glow_colour'][0], 1.0); self.assertEqual(z['glow_colour'][2], 0.0)
        self.assertAlmostEqual(z['glow_colour'][1], 0.28314873576164246 - 0.1502804160118103)
        self.assertFalse(z['texture_tint'])
        self.assertIn('dissolve term (key 0x6424A3B7 = 0.5) is not drawn', z['limitation'])

    def test_glsl_terms(self):
        self.assertTrue({'uZurkon', 'uZurkonP', 'uZurkonQ', 'uZurkonGlow', 'uZurkonTint', 'uSrgbZurkonTint'} <= set(OPAQUE_UNIFORMS))
        block = FRAGMENT.split('if (uMode == 2 && uZurkon)')[1].split('vec3 L1 = rigKey()')[0]
        for text in ('abs(1.00100004673 - clamp(dot(N, V), 0.0, 1.0))', 'pow(r, uZurkonP.w)', '1.0 - pow(r, uZurkonQ.x)',
                     'uZurkonQ.y * c.a * zg.b * uZurkonGlow', 'vec3(0.2126, 0.7152, 0.0722)'):
            self.assertIn(text, block)
        spec = FRAGMENT.split('if (uZurkon) { // rel 980-1052')[1].split('}')[0]
        self.assertIn('uZurkonP.x * (gl.g + uZurkonP.z * zurkonRim) * zurkonTint', spec)
        self.assertIn('sqrt(clamp(uZurkonP.y * (gl.r + uZurkonP.z * zurkonRim), 0.0, 1.0))', spec)
        self.assertIn('else if (uZurkon) color += albedo * c.a;', FRAGMENT)

    def test_viewer_entry_and_blender_rule(self):
        archive, paths = extract()
        with (patch.object(tp, 'material_texture_paths', return_value=paths),
              patch('rc2016.viewer.native_glsl.selected_translation', return_value=None)):
            entry = tp.material_textures(archive, [dict(index=0, path='m/z.material')], {}, previews=False)[0]
            self.assertEqual(entry['zurkon'], paths['zurkon'])
            rule = viewer_graph_rule(archive, 'm/z.material', set(SLOTS.values()))
            unbound = viewer_graph_rule(archive, 'm/z.material', {'t/z_c.texture', 't/z_g.texture', 't/z_n.texture'})
        self.assertIn('zurkon', rule['ported'])
        self.assertEqual(rule['textures']['_h'], 't/z_specularcolor_c.texture')
        self.assertEqual(rule['normal_z'], '1-x*x-y*y')
        self.assertIn('zurkon', unbound['unported'])

    def test_validation_names(self):
        _, got = extract()
        rule = dict(applies=True, ported=['zurkon'], terms={'zurkon': got['zurkon']},
                    textures={'_c': 'c', '_g': 'g', '_n': 'n', '_h': 'h'})
        expected = validation._viewer_expected_constants(rule)
        names, roles = validation._viewer_required(rule, True)
        self.assertEqual(sorted(names), sorted('VR zurkon ' + n for n in (
            'rim exponent', 'facing exponent', 'glow gain', 'glow colour', 'rim weight', 'F0 scale', 'gloss scale')))
        self.assertEqual(set(roles), {'_c', '_g', '_h'})
        self.assertEqual(expected['VR zurkon glow colour'], [0.0, 0.28314873576164246, 1.0])
        self.assertNotIn('VR zurkon specular tint', expected)


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'needs the game archive (RC2016_RESEARCH)')
class MrsZurkonArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(os.environ['RC2016_RESEARCH'])

    def test_graph_programs_and_remix_reading(self):
        from rc2016.formats.archive import asset_data
        from rc2016.shaders.translator import pixel_programs
        ids = [i for i in self.archive.resolve(GRAPH) if self.archive.entries[i]['type'] == 'materialgraph']
        data = bytes(asset_data(self.archive, ids[0]))
        self.assertEqual(hashlib.sha256(data).hexdigest(), GRAPH_SHA)
        programs = {p['shader_id']: p['code_sha256'] for p in pixel_programs(data)}
        self.assertEqual((programs['0x2f0f9452'], programs['0x0c76522b']), (PROGRAM_SHA, SIBLING_SHA))
        literals = struct.unpack_from('<2I', [p for p in pixel_programs(data) if p['shader_id'] == '0x2f0f9452'][0]['code'], 13240)
        self.assertEqual(struct.unpack('<f', struct.pack('<I', 0x3E90F8DF))[0], tp.MRS_ZURKON_GLOW_GREEN[0])
        self.assertEqual(literals[1], 0x3E90F8DF)   # V_MADAK_F32 literal at rel 13244
        z = tp.material_texture_paths(self.archive, MATERIAL)['zurkon']
        self.assertEqual(z['glow_colour'], [0.0, 0.28314873576164246, 1.0])
        self.assertEqual((z['f0_scale'], z['gloss_scale'], z['rim_exponent'], z['facing_exponent']), (1.5, 2.0, 4.0, 1.5))


if __name__ == '__main__':
    unittest.main()
