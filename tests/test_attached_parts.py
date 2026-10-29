"""A model's bangle_set/*_attach parts export skinned to its own rig, as extra nodes of one glTF skin."""
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

from rc2016.export.models import ExportOptions, ModelExporter

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

    def test_the_body_alone_without_the_option(self):
        _result, document = self.export(attachments=False)
        self.assertEqual(len([n for n in document['nodes'] if 'mesh' in n and 'skin' in n]), 1)


if __name__ == '__main__':
    unittest.main()
