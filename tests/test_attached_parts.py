"""A model's bangle_set/*_attach parts export skinned to its own rig, as extra nodes of one glTF skin."""
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rc2016.export.models import ExportOptions, ModelExporter, bind_worlds, root_shift

ROOT = os.environ.get('RC2016_RESEARCH')
ZURKON = ('6d8653db864e5c7763b2', 'character/enemy/enm_mrs_zurkon/enm_mrs_zurkon.model')


def glb_document(path):
    data = Path(path).read_bytes()
    length = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20 + length])


class AttachmentOption(unittest.TestCase):
    def test_the_option_is_on_by_default_and_part_of_the_key(self):
        self.assertTrue(ExportOptions().attachments)
        self.assertTrue(ExportOptions().key()['attachments'])
        self.assertFalse(ExportOptions(attachments=False).key()['attachments'])


def row(parent, translation):
    return dict(parent=parent, translation=translation, rotation_xyzw=[0, 0, 0, 1], scale=[1, 1, 1])


class RootShift(unittest.TestCase):
    BODY = [row(-1, [0, 1, 0]), row(0, [0, 2, 0]), row(1, [0, 3, 0])]

    def test_a_part_with_its_root_at_the_origin_shifts_by_the_body_root(self):
        part = [row(-1, [0, 0, 0]), row(0, [0, 2, 0])]
        shift = root_shift(part, [0, 1], bind_worlds(self.BODY))
        self.assertEqual(shift.tolist(), [0, 1, 0])

    def test_a_part_with_the_body_translations_has_no_shift_to_find(self):
        part = [row(-1, [0, 1, 0]), row(0, [0, 2, 0])]
        self.assertEqual(root_shift(part, [0, 1], bind_worlds(self.BODY)).tolist(), [0, 0, 0])

    def test_joints_that_disagree_give_no_shift(self):
        part = [row(-1, [0, 0, 0]), row(0, [0, 5, 0])]
        self.assertIsNone(root_shift(part, [0, 1], bind_worlds(self.BODY)))

    def test_a_rotated_joint_gives_no_shift(self):
        part = [dict(row(-1, [0, 0, 0]), rotation_xyzw=[0, 0, 1, 0]), row(0, [0, 2, 0])]
        self.assertIsNone(root_shift(part, [0, 1], bind_worlds(self.BODY)))


@unittest.skipUnless(ROOT, 'needs the game archive (RC2016_RESEARCH)')
class ZurkonAttachments(unittest.TestCase):
    def export(self, **options):
        with tempfile.TemporaryDirectory() as temp:
            exporter = ModelExporter(Path(ROOT), Path(temp))
            result = exporter.export(*ZURKON, ExportOptions(formats=('glb',), animations='none', **options))
            info = json.loads((Path(result['folder']) / 'export.json').read_text(encoding='utf-8'))
            return info, glb_document(Path(result['folder']) / 'enm_mrs_zurkon.glb')

    def test_parts_share_the_body_skin_and_stay_in_range(self):
        info, document = self.export()
        self.assertEqual(len(document['skins']), 1)
        nodes = [n for n in document['nodes'] if 'mesh' in n and 'skin' in n]
        self.assertEqual(len(nodes), 10)   # the body and nine parts
        self.assertEqual({n['skin'] for n in nodes}, {0})
        self.assertEqual(sum(1 for n in nodes if 'attach' in n['name']), 9)
        self.assertEqual(len(info['attached_parts']), 9)

    def test_a_part_sits_at_its_root_offset_on_the_body(self):
        _info, document = self.export()
        hatch = next(n for n in document['nodes'] if n.get('name', '').startswith('enm_mrs_zurkon_bangle_00_hatch') and 'mesh' in n)
        primitive = document['meshes'][hatch['mesh']]['primitives'][0]
        low = document['accessors'][primitive['attributes']['POSITION']]['min']
        high = document['accessors'][primitive['attributes']['POSITION']]['max']
        # authored around the door hinge at the part's origin; the body's door joint is at (0, 0.60, 2.15)
        self.assertGreater(low[2], 1.7)
        self.assertLess(high[2], 3.6)
        self.assertGreater(low[1], 0.5)

    def test_the_body_alone_without_the_option(self):
        _result, document = self.export(attachments=False)
        self.assertEqual(len([n for n in document['nodes'] if 'mesh' in n and 'skin' in n]), 1)


if __name__ == '__main__':
    unittest.main()
