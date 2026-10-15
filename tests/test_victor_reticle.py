"""Exact-identity, source-equation and routing contracts for the bounded Victor reticle rule."""
import hashlib
import math
import struct
import unittest
from unittest.mock import patch

from rc2016.formats.links import EXACT, rule_coverage
from rc2016.formats.materials import final_shader_status, viewer_graph_rule
from rc2016.viewer import textures as tp
from tests.test_viewer_textures import FakeArchive, dat1, flags, slot_rows

GRAPH = ('materialgraph/visualeffect/character/enemy/enm_victor_reticle/'
         'enm_victor_reticle.materialgraph')
MATERIAL = 'm/victor_reticle.material'
GRAPH_SHA = 'e4bcaf552be55cbc14b5081b6695a600084ae162889455b87eadc6c0103d79ea'
TRIGGER_SLOT = 0x628AD5A840ED6C83
TRIGGER = 'textures/visualeffect/trigger/trigger_linear_01_c.texture'
GAIN_KEY = 0xFD5FF119
AMP = tp.VICTOR_RETICLE_PRESENTATION_AMPLITUDE
REAL_SHA256 = hashlib.sha256


def f32(key, value, size=4, raw=None):
    return dict(key_hash=key, size=size, raw_hex=raw if raw is not None else struct.pack('<f', value).hex())


def archive_for(graph=GRAPH, graph_blob=None, rows=()):
    graph_blob = graph_blob if graph_blob is not None else dat1([TRIGGER], lambda at: [
        (tp.GRAPH_SLOT_SECTION, slot_rows([(at[0], TRIGGER_SLOT)]))])
    material_blob = dat1([graph], lambda at: [
        (tp.MATERIAL_FLAGS_SECTION, flags(0x9))])
    return FakeArchive({MATERIAL: ('material', material_blob), graph: ('materialgraph', graph_blob)})


def exact_hash_for_blob(blob):
    # FakeArchive.asset_data includes its synthetic 1TAD marker before the DAT1 body.
    return lambda data=b'': _Digest(GRAPH_SHA) if data == b'1TAD' + blob else REAL_SHA256(data)


class _Digest:
    def __init__(self, value): self.value = value
    def hexdigest(self): return self.value


def extract(archive, rows=()):
    with (patch('rc2016.shaders.families.graph_parameter_tables', return_value=([], {})),
          patch('rc2016.formats.materials.extract_material_parameters', return_value={
              'parameters': [], 'unsupported_parameters': list(rows)})):
        return tp.material_texture_paths(archive, MATERIAL)


class VictorReticleRuleTests(unittest.TestCase):
    def test_exact_graph_prefix_and_hash_are_both_required(self):
        self.assertEqual(tp._graph_rule([GRAPH])['forward'], 'victor_reticle')
        self.assertEqual(rule_coverage([GRAPH])[0], EXACT)
        self.assertEqual(tp._graph_rule([GRAPH.replace('.materialgraph', '_extra.materialgraph')]), {})
        archive = archive_for()
        graph_id = archive.resolve(GRAPH)[0]
        graph_blob = archive.blobs[graph_id][4:]
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)):
            accepted = extract(archive)
        self.assertEqual(accepted['forward']['kind'], 'victor_reticle')
        rejected_archive = archive_for(graph_blob=dat1([TRIGGER], lambda at: [
            (tp.GRAPH_SLOT_SECTION, slot_rows([(at[0], TRIGGER_SLOT)]))]))
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=REAL_SHA256):
            rejected = extract(rejected_archive)
        self.assertNotIn('forward', rejected)

    def test_api8_slot_gain_flags_and_named_presentation_input(self):
        archive = archive_for()
        graph_blob = archive.blobs[archive.resolve(GRAPH)[0]][4:]
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)):
            got = extract(archive, [f32(GAIN_KEY, 4.0)])
        self.assertEqual(got['_reticle'], TRIGGER)
        self.assertEqual(got['forward']['params']['texture_gain'], 4.0)
        self.assertEqual(got['forward']['params']['amplitude'], AMP)
        self.assertEqual(got['forward']['params']['amplitude_input'], 'CB0[115]')
        self.assertTrue(got['forward']['params']['presentation'])
        self.assertEqual((got['forward']['blend'], got['forward']['two_sided']), ('additive', False))

    def test_missing_bound_api8_does_not_claim_the_rule_is_ready(self):
        archive = archive_for()
        graph_blob = archive.blobs[archive.resolve(GRAPH)[0]][4:]
        with patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)):
            ready = viewer_graph_rule(archive, MATERIAL, {TRIGGER})
            missing = viewer_graph_rule(archive, MATERIAL, {'textures/other.texture'})
        self.assertIn('victor_reticle', ready['ported'])
        self.assertEqual(final_shader_status('unsupported', ready, {TRIGGER}, {'status': 'unsupported'}, None, [], [GRAPH]),
                         'viewer_graph_rule_frontend')
        self.assertIn('victor_reticle', missing['unported'])
        self.assertEqual(final_shader_status('unsupported', missing, set(), {'status': 'unsupported'}, None, [], [GRAPH]),
                         'unsupported')

    def test_malformed_nonfinite_and_unsupported_explicit_values_fail_closed(self):
        cases = ([f32(GAIN_KEY, math.nan)], [f32(GAIN_KEY, math.inf)],
                 [f32(GAIN_KEY, 4.0, size=8)], [f32(GAIN_KEY, 4.0, raw='not-hex')])
        for rows in cases:
            with self.subTest(rows=rows):
                archive = archive_for()
                graph_blob = archive.blobs[archive.resolve(GRAPH)[0]][4:]
                with (patch('rc2016.viewer.textures.hashlib.sha256', side_effect=exact_hash_for_blob(graph_blob)),
                      self.assertRaises(ValueError)):
                    extract(archive, rows)

    def test_source_equation_vectors_and_deferred_contract(self):
        # Independently evaluated writer output; A is the explicitly labelled preview input.
        def albedo(u, v, amp=0.5, ring=0.0):
            d = math.hypot(u - 0.5, v - 0.5); b = 1.0 - d; e = abs(b)
            return tuple(min(1.0, max(0.0, x)) for x in (
                2 * amp * e,
                2 * amp * (e + ring * (e**10 - e)),
                2 * amp * (b**2 + ring * (e**20 - b**2))))
        self.assertEqual(albedo(.5, .5), (1.0, 1.0, 1.0))
        self.assertEqual(albedo(1.5, .5), (0.0, 0.0, 0.0))
        self.assertEqual(albedo(1.0, .5), (0.5, 0.5, 0.25))
        ring_vector = albedo(.75, .5, ring=.25)
        self.assertAlmostEqual(ring_vector[0], .75, places=7)
        self.assertAlmostEqual(ring_vector[1], .5765783786773682, places=7)
        self.assertAlmostEqual(ring_vector[2], .4226678029847335, places=7)
        encoded = math.exp2(5 * (0.20000000298023224 * math.log2(1 + 0.5 * 4.0))) - 1
        self.assertAlmostEqual(encoded, 2.0, places=6)
        terms = tp._graph_rule([GRAPH])['constants']
        self.assertEqual(terms['f0'], [0.0, 0.0999755859375, 0.0999755859375])
        self.assertEqual(terms['gloss'], 0.0)


if __name__ == '__main__':
    unittest.main()
