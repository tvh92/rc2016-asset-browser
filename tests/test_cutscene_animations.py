"""Cutscene animation library: shot labels, the cutscene source in AnimationLibrary, glTF clips, the manifest.

All inputs are synthetic byte strings built by test_animation_sources; no game data is embedded.
"""
import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rc2016.export import cutscene_animations as C
from rc2016.export.models import ModelExporter, clip_tracks, write_glb
from rc2016.formats import animation_sources as S
from rc2016.viewer.animation import AnimationLibrary
from tests.test_animation_sources import FakeArchive, animset, clip, model, rig

JOINTS = [('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0))]


def cutscene_blob():
    """Two named shots for the rig, one clip whose name is in no string, one version-22 (undecodable) shot."""
    return animset([('nov_test_rt_shot1_npc_Test', clip('nov_test_rt_shot1_npc_Test', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)])),
                    ('nov_test_rt_shot2_npc_Test', clip('nov_test_rt_shot2_npc_Test', ['root', 'spine'], [(1, 0, 0), (0, 2, 0)])),
                    ('nov_test_rt_shot3_npc_Test', clip('nov_test_rt_shot3_npc_Test', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)],
                                                        version=22)),
                    ('hidden', clip('hidden', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)]))],
                   strings=['nov_test_rt_shot1_npc_Test', 'nov_test_rt_shot2_npc_Test', 'nov_test_rt_shot3_npc_Test'])


def archive():
    return FakeArchive([('c1', 'cinematic2', 'cinematic/nov_test/nov_test_rt.cinematic2', cutscene_blob()),
                        ('m1', 'model', 'character/npc/npc_test/npc_test.model', model(JOINTS))])


class ShotLabelTests(unittest.TestCase):
    def test_pattern_gives_cutscene_and_shot(self):
        self.assertEqual(S.shot_label('nov_mayor_rt_shot3_npc_Mayor_master', 'a/b/c.cinematic2'), ('nov_mayor_rt', 3))
        self.assertEqual(S.shot_label('vel_qwark_rt_shot20_Shiv', 'a/b/c.cinematic2'), ('vel_qwark_rt', 20))

    def test_other_names_fall_back_to_the_asset_stem(self):
        self.assertEqual(S.shot_label('pickup_predator', 'cinematic/x/predator_pickup_cine.cinematic2'),
                         ('predator_pickup_cine', None))
        self.assertEqual(S.shot_label(None, 'x/y.cinematic2'), ('y', None))


class DirectoryRowTests(unittest.TestCase):
    def test_rows_carry_version_and_duration(self):
        rows = S.clip_directory(cutscene_blob())
        self.assertEqual({r['format_version'] for r in rows}, {21, 22})
        self.assertTrue(all('duration_seconds' in r for r in rows))

    def test_unnamed_clips_are_listed_only_on_request(self):
        rows = rig(JOINTS)
        named = S.cinematic_sources(archive(), rows)[0][0]['clips']
        every = S.cinematic_sources(archive(), rows, include_unnamed=True)[0][0]['clips']
        self.assertEqual(len(named), 3)
        self.assertEqual(sorted(c['name'] or '' for c in every)[0], '')
        self.assertEqual(len(every), 4)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.archive = archive()
        base = AnimationLibrary(self.archive, model(JOINTS), [])
        self.base = base
        self.library = base.with_cinematic(self.archive, replace=True)

    def test_copy_leaves_the_original_untouched_and_names_clips_plainly(self):
        self.assertEqual(self.base.clips, [])
        self.assertEqual(self.library.labels(), ['nov_test_rt_shot1_npc_Test', 'nov_test_rt_shot2_npc_Test',
                                                 'nov_test_rt_shot3_npc_Test'])
        self.assertTrue(all(c['source'] == S.CINEMATIC_SOURCE for c in self.library.clips))

    def test_same_shot_name_in_two_cutscenes_gets_the_asset_prefix(self):
        two = FakeArchive([('c1', 'cinematic2', 'a/one.cinematic2', cutscene_blob()),
                           ('c2', 'cinematic2', 'b/two.cinematic2', cutscene_blob())])
        labels = self.base.with_cinematic(two, replace=True).labels()
        self.assertEqual(labels[0], 'one: nov_test_rt_shot1_npc_Test')
        self.assertEqual(len(set(labels)), 6)

    def test_clips_decode_through_the_exporter_track_path(self):
        kind, fps, tracks = clip_tracks(self.library, 1)
        self.assertEqual((kind, fps, len(tracks)), ('absolute skeletal', 30.0, 1))
        self.assertEqual(np.round(tracks.translation[0, 0], 6).tolist(), [1.0, 0.0, 0.0])
        with self.assertRaisesRegex(ValueError, 'only PS4 clip versions 15 and 21'):
            clip_tracks(self.library, 2)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.archive = archive()
        self.sources, self.errors = S.cinematic_sources(self.archive, rig(JOINTS), include_unnamed=True)
        self.asset = self.sources[0]['path']
        offsets = {c['name']: c['offset'] for c in self.sources[0]['clips']}
        self.offsets = offsets
        self.result = dict(
            clips=[dict(name='nov_test_rt_shot1_npc_Test', kind='absolute skeletal', fps=30.0, frames=1, source='cinematic2',
                        asset=self.asset, offset=offsets['nov_test_rt_shot1_npc_Test']),
                   dict(name='nov_test_rt_shot2_npc_Test', kind='absolute skeletal', fps=30.0, frames=1, source='cinematic2',
                        asset=self.asset, offset=offsets['nov_test_rt_shot2_npc_Test'])],
            skipped_clips=[dict(name='nov_test_rt_shot3_npc_Test', reason='only PS4 clip versions 15 and 21 are validated',
                                not_playable=False, source='cinematic2', asset=self.asset,
                                offset=offsets['nov_test_rt_shot3_npc_Test'])])

    def test_every_clip_gets_a_row_with_status_and_reason(self):
        manifest = C.build_manifest('character/npc/npc_test/npc_test.model', 'm1', 2, self.sources, self.errors, self.result)
        rows = {r['name']: r for r in manifest['clips']}
        self.assertEqual(len(manifest['clips']), 4)
        self.assertEqual(rows['nov_test_rt_shot1_npc_Test']['decode_status'], 'decoded')
        self.assertEqual((rows['nov_test_rt_shot1_npc_Test']['cutscene'], rows['nov_test_rt_shot1_npc_Test']['shot']),
                         ('nov_test_rt', 1))
        self.assertEqual(rows['nov_test_rt_shot1_npc_Test']['gltf_animation'], 'nov_test_rt_shot1_npc_Test')
        failed = rows['nov_test_rt_shot3_npc_Test']
        self.assertEqual((failed['decode_status'], failed['format_version']), ('not_decoded', 22))
        self.assertIn('versions 15 and 21', failed['reason'])
        self.assertEqual(rows[None]['decode_status'], 'unnamed_not_exported')
        self.assertEqual(manifest['counts'], dict(named_clips=3, decoded=2, not_decoded=1, not_playable=0,
                                                  unnamed_not_exported=1, decoded_frames=2, cutscenes=1))
        self.assertIn('not idle loops', manifest['note'])
        json.dumps(manifest)

    def test_not_playable_is_its_own_status(self):
        self.result['skipped_clips'][0].update(not_playable=True, reason='unrelated; it does not drive this model')
        manifest = C.build_manifest('p', 'm1', 2, self.sources, [], self.result)
        self.assertEqual(manifest['counts']['not_playable'], 1)

    def test_resolve_model_by_path_or_unique_stem(self):
        self.assertEqual(C.resolve_model(self.archive, 'npc_test'), ('m1', 'character/npc/npc_test/npc_test.model'))
        self.assertEqual(C.resolve_model(self.archive, 'character/npc/npc_test/npc_test.model')[0], 'm1')
        with self.assertRaisesRegex(ValueError, 'matches 0 models'):
            C.resolve_model(self.archive, 'missing')


class GlbAnimationTests(unittest.TestCase):
    def test_exporter_writes_one_glb_animation_per_clip_and_the_summary_reads_it(self):
        library = AnimationLibrary(archive(), model(JOINTS), []).with_cinematic(archive(), replace=True)
        library.inverse = [np.eye(4)] * len(library.rows)   # the synthetic model has no inverse binds
        clips = []
        for index, label in enumerate(library.labels()[:2]):
            kind, fps, tracks = clip_tracks(library, index)
            clips.append(dict(name=label, kind=kind, fps=fps, tracks=tracks))
        parts = []
        exporter = ModelExporter.__new__(ModelExporter)
        gltf = exporter._build_gltf('npc_test', parts, {}, library, clips)
        doc, blob = gltf.finish(embed=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'x.glb'
            write_glb(path, doc, blob)
            summary = C.glb_summary(path)
        self.assertEqual(summary['animations'], 2)
        self.assertEqual(summary['animation_names'], ['nov_test_rt_shot1_npc_Test', 'nov_test_rt_shot2_npc_Test'])
        self.assertEqual((summary['skins'], summary['joints']), (1, 2))

    def test_glb_summary_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'x.glb'
            path.write_bytes(struct.pack('<III', 0x12345678, 2, 12))
            with self.assertRaises(ValueError):
                C.glb_summary(path)


class OptionsTests(unittest.TestCase):
    def test_new_options_do_not_change_the_key_of_existing_exports(self):
        from rc2016.export.models import ExportOptions
        self.assertNotIn('cinematic', ExportOptions().key())
        self.assertEqual(ExportOptions(cinematic_only=True, file_suffix='_cutscenes').key()['file_suffix'], '_cutscenes')

    def test_borrowed_animsets_stay_out_of_the_key_unless_asked_for(self):
        from rc2016.export.models import ExportOptions
        self.assertNotIn('borrowed', ExportOptions().key())
        self.assertTrue(ExportOptions(borrowed=True).key()['borrowed'])


class BrowserLoaderTests(unittest.TestCase):
    """The browser's clip library offers cutscene shots; the export load does not unless asked to."""

    def build(self, purpose):
        from rc2016.viewer.loader import SourcePreviewLoader
        loader = SourcePreviewLoader.__new__(SourcePreviewLoader)
        loader.archive = archive()
        decoded = dict(joint_count=2, animation_note='', animsets=[], _archive_path='character/npc/npc_test/npc_test.model')
        cached = dict(decoded=decoded, data=model(JOINTS), purpose=purpose, animation=None, animation_built=None)
        loader._build_animation_locked(cached, None)
        return cached, decoded

    def test_view_library_lists_the_cutscene_shots_without_an_animset(self):
        cached, decoded = self.build('view')
        self.assertEqual(cached['animation'].labels(), ['nov_test_rt_shot1_npc_Test', 'nov_test_rt_shot2_npc_Test',
                                                       'nov_test_rt_shot3_npc_Test'])
        self.assertEqual(decoded['animsets'], ['cinematic/nov_test/nov_test_rt.cinematic2'])
        self.assertEqual(decoded['animation_note'], '')

    def test_export_library_stays_animset_only(self):
        cached, decoded = self.build('export')
        self.assertEqual(cached['animation'].clips, [])
        self.assertIn('output root', decoded['animation_note'])


if __name__ == '__main__':
    unittest.main()
