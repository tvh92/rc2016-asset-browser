"""Factory source identity and isolation of the opt-in approximate layer."""
import copy
import os
from pathlib import Path
import struct
import unittest

from rc2016.levels import spawn_format as sf
from rc2016.levels.actor_format import IDENTITY
from rc2016.formats.container import game_crc32
from tests.test_level_actors import container, change
from tests.test_level_actor_layer import actor, catalog


def factory_data(actor_id=1, packed_count=1):
    return container([
        (sf.NAMES, lambda o: struct.pack('<I', o[sf.FACTORY])),
        (sf.NODES, struct.pack('<QHHHHHHIII', 123, 0, 0xFFFF, 0, 0, 3, 0,
                              game_crc32(sf.FACTORY), 0, 0)),
        (sf.LINKS, b''.join(struct.pack('<III', pin, i, 0) for i, pin in
                           enumerate((sf.TEMPLATES, sf.LOCATIONS, sf.INITIAL_COUNT)))),
        (sf.PARAMETERS, struct.pack('<HHHHQQ', 5, 0xFFFF, 0, 0xFFFF, actor_id, 0) +
                        struct.pack('<HHHHQQ', 5, 0xFFFF, 0xFFFF, 0xFFFF, 456, 0) +
                        struct.pack('<HHHHQQ', 2, 0xFFFF, 0xFFFF, 0xFFFF, 0x40A00000, 0)),
        (sf.GROUPS, struct.pack('<IIIIQ', 0, 0, packed_count, 0xFFFFFFFF, 456)),
        (sf.MEMBERS, struct.pack('<H', 1)),
    ], [sf.FACTORY])


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.actors = [actor(), dict(actor(), id='0000000000000002')]

    def test_typed_actor_and_flagged_group_bindings_and_literal_count(self):
        decoded = sf.decode_factories(factory_data(packed_count=0x10001), self.actors)[0]
        self.assertEqual(decoded['templates'], [0])
        self.assertEqual(decoded['locations'], [1])
        self.assertEqual(decoded['initial_count'], 5)
        self.assertEqual(decoded['reasons'], [])
        self.assertEqual(decoded['section'], '5E54ACCF')

    def test_mismatched_actor_id_bad_crc_and_ranges_fail_loudly(self):
        for data in (factory_data(actor_id=99),
                     change(factory_data(), sf.NODES, 20, '<I', 42),
                     change(factory_data(), sf.NODES, 16, '<H', 100),
                     change(factory_data(), sf.MEMBERS, 0, '<H', 99)):
            with self.assertRaises(ValueError):
                sf.decode_factories(data, self.actors)

    def test_empty_binding_sentinel_and_dynamic_counts(self):
        data = change(factory_data(), sf.NODES, 14, '<HH', 0xFFFF, 0)
        f = sf.decode_factories(data, self.actors)[0]
        self.assertIsNone(f['initial_count'])
        self.assertIn('initial count not a single literal', f['reasons'])
        data = change(factory_data(), sf.PARAMETERS, 48, '<H', 1)
        self.assertIsNone(sf.decode_factories(data, self.actors)[0]['initial_count'])

    def test_external_location_is_reported_without_fallback(self):
        data = change(factory_data(), sf.PARAMETERS, 32, '<Q', 999)
        f = sf.decode_factories(data, self.actors)[0]
        self.assertEqual(f['locations'], [])
        self.assertIn('external or unresolved actor/group reference', f['reasons'])

    def test_preview_preserves_template_linear_matrix_without_volume_scale(self):
        template = actor('enemy.model')
        template.update(actor_path='character/enemy/exact.actor', binding={'section':'32FAC8E0'}, record_offset=12)
        template['matrix_rows'][0][:3] = [-2., .3, .5]
        location = dict(actor(), id='0000000000000002', record_offset=24)
        location['matrix_rows'][0][0] = 100.
        location['matrix_rows'][3][:3] = [10.,20.,30.]
        source = [template, location]
        before = copy.deepcopy(source)
        f = dict(sf.decode_factories(factory_data(), source)[0], zone='game.zone')
        descriptor = lambda path: dict(drawing_components=[])
        placements, reports = sf.preview_placements([f], {'game.zone':source}, descriptor)
        self.assertEqual(len(placements), 5)
        self.assertEqual(source, before)
        self.assertTrue(all(a['matrix_rows'][:3] == template['matrix_rows'][:3] for a in placements))
        self.assertTrue(all(a['matrix_rows'][3][1] == 20 for a in placements))
        self.assertEqual(len({tuple(a['matrix_rows'][3]) for a in placements}), 5)
        self.assertTrue(all(a['spawn_preview']['template_id'] == template['id'] for a in placements))
        self.assertTrue(all(a['binding'] == template['binding'] for a in placements))
        self.assertEqual(sf.preview_summary(placements, reports)['generated'], 5)
        template['model'] = None
        self.assertEqual(sf.preview_placements([f], {'game.zone':source}, descriptor)[0], [])



@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'set RC2016_RESEARCH for real factory inputs')
class ArchiveFactoryTests(unittest.TestCase):
    def test_novalis_exact_factory_links_preview_84_peckbots_and_20_birdbots(self):
        from rc2016.levels.actors import ActorCatalog
        from rc2016.levels.scene import open_archive
        root = Path(os.environ['RC2016_RESEARCH']).parents[1]/'Ratchet & Clank (EUR)'/'Image0'
        archive = open_archive(root)
        try:
            c = ActorCatalog(archive)
            c.add_zone('levels/novalis/openarea/openworld/default_region/gp_base.zone', {'models':[]})
            placements, factories = sf.preview_placements(c.factories, c.zone_actors, c.descriptor)
            self.assertEqual(len(factories), 39)
            self.assertEqual(len(placements), 123)
            self.assertEqual(sum('/enm_peckbot/' in a['actor_path'].lower() for a in placements), 84)
            self.assertEqual(sum('/enm_birdbot/' in a['actor_path'].lower() for a in placements), 20)
            self.assertTrue(all(a['binding']['section']=='32FAC8E0' for a in placements))
            self.assertTrue(all(a['zone_sha256'] and a['actor_sha256'] for a in placements))
        finally:
            archive.close()
