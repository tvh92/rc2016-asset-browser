"""Cutscene clip naming, version-21 clips, filtered extraction and matching-rig borrowing.

All inputs are synthetic byte strings built here; no game bytes are embedded.
"""
import struct
import tempfile
import unittest
from pathlib import Path

from rc2016.formats import animation_sources as S
from rc2016.formats.animation_samples import decode_clip
from rc2016.formats.animation_transforms import model_skeleton, sample_transforms
from rc2016.formats.clips import extract_set
from rc2016.formats.container import game_crc32
from rc2016.viewer.animation import AnimationLibrary

BUILT, JOINTS, BASE = 0x9DF23F77, 0xA3B26640, 0x2BB5BC8F
LOOKUP, DATA = 0xB79CF1D7, 0x9FD19C20
STRINGS = 0x12345678
MODEL_JOINTS, MODEL_TRANSFORMS = 0x15DF9D3B, 0xDCC88A19


def container(sections):
    """A 1TAD container from (label, payload) pairs."""
    table_end = 16 + 12 * len(sections)
    payload, entries = bytearray(), []
    for label, body in sections:
        entries.append(struct.pack('<III', label, table_end + len(payload), len(body)))
        payload += body
    total = table_end + len(payload)
    return b'1TAD' + struct.pack('<IIHH', 0, total, len(sections), 0) + b''.join(entries) + bytes(payload)


def clip(name, joint_names, translations, version=21, frames=1, duration=0.0):
    """A zero-delta clip of ``frames`` frames (no sample stream, so every frame repeats the base): identity
    rotations, unit scales, the given translations. The default is one frame."""
    built = struct.pack('<IIIff', version, game_crc32(name), 0x80001, 30.0, duration) + bytes(12)
    built += struct.pack('<IHHII', len(joint_names) | (frames << 16), 0, 0, 0, 1)
    hashes = b''.join(struct.pack('<I', game_crc32(n)) for n in joint_names) + b'\xff' * 4
    base = b''.join(struct.pack('<7h', 0, 0, 0, 16384, *t) + bytes(2) for t in translations)
    return container([(BUILT, built), (JOINTS, hashes), (BASE, base)])


def animset(clips, strings=()):
    """A root with a lookup/data directory over ``clips`` (name, bytes), plus a string section."""
    data, lookup = bytearray(), bytearray()
    for name, body in clips:
        lookup += struct.pack('<IIII', game_crc32(name), len(data), 0, 0)
        data += body
    sections = [(LOOKUP, bytes(lookup)), (DATA, bytes(data))]
    if strings:
        sections.append((STRINGS, b''.join(s.encode() + b'\0' for s in strings)))
    return container(sections)


def rig(joints):
    """Joint rows as model_skeleton returns them: (name, parent, translation[, flags])."""
    return [dict(name=j[0], hash=f'0x{game_crc32(j[0]):08X}', parent=j[1], flags=j[3] if len(j) > 3 else 0,
                 scale=[1., 1., 1.], rotation_xyzw=[0., 0., 0., 1.], translation=list(j[2])) for j in joints]


def model(joints):
    """A model blob with the joint and rest-transform sections model_skeleton reads."""
    names = b''
    pointers = []
    head = 16 + 12 * 3
    records = bytearray()
    transforms = bytearray()
    for name, parent, translation in joints:
        pointers.append(len(names)); names += name.encode() + b'\0'
    joint_size, transform_size = 16 * len(joints), 48 * len(joints)
    names_offset = head + joint_size + transform_size
    for (name, parent, translation), pointer in zip(joints, pointers):
        records += struct.pack('<hhHHII', parent, 0, 0, 0, 0, names_offset + pointer)
        transforms += struct.pack('<12f', 1, 1, 1, 0, 0, 0, 0, 1, *translation, 0)
    return container([(MODEL_JOINTS, bytes(records)), (MODEL_TRANSFORMS, bytes(transforms)), (STRINGS, names)])


class FakeArchive:
    def __init__(self, assets):
        self.entries, self.data = {}, {}
        for order, (key, kind, path, data) in enumerate(assets):
            self.entries[key] = dict(id=key, type=kind, path=path, order=order, occurrences=[dict(available=True)])
            self.data[key] = data

    def asset_data(self, key):
        return self.data[key]

    def resolve(self, path):
        return [k for k, e in self.entries.items() if e['path'] == path]


class CrcAndNamingTests(unittest.TestCase):
    def test_zlib_crc_equals_game_crc(self):
        for text in ('', 'a', 'Idle_npc_Sheep', 'nov_mayor_rt_shot5_npc_Mayor_master'):
            self.assertEqual(S.crc32(text), game_crc32(text))

    def test_cutscene_clip_is_named_only_by_a_crc_string_in_the_file(self):
        blob = animset([('shot1_npc_test', clip('shot1_npc_test', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)])),
                        ('shot2_unnamed', clip('shot2_unnamed', ['root'], [(0, 0, 0)]))],
                       strings=['shot1_npc_test'])
        rows = S.clip_directory(blob)
        self.assertEqual([(r['name'], r['name_source']) for r in rows],
                         [('shot1_npc_test', 'crc32_string_in_file'), (None, None)])

    def test_joint_filter_requires_every_model_joint(self):
        blob = animset([('a', clip('a', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)]))], strings=['a'])
        self.assertEqual(len(S.clip_directory(blob, {f'0x{game_crc32("root"):08X}'})), 1)
        self.assertEqual(S.clip_directory(blob, {f'0x{game_crc32("root"):08X}', f'0x{game_crc32("arm"):08X}'}), [])
        self.assertEqual(S.clip_directory(blob, set()), [])

    def test_cinematic_sources_skip_static_models_and_uncovered_rigs(self):
        blob = animset([('shot1_npc_test', clip('shot1_npc_test', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)]))],
                       strings=['shot1_npc_test'])
        archive = FakeArchive([('c1', 'cinematic2', 'cinematic/test/test.cinematic2', blob)])
        path = 'character/npc/npc_test/npc_test.model'
        found, errors = S.cinematic_sources(archive, rig([('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0))]), path)
        self.assertEqual([c['name'] for c in found[0]['clips']], ['shot1_npc_test'])
        self.assertEqual(errors, [])
        self.assertEqual(S.cinematic_sources(archive, [], path)[0], [])
        self.assertEqual(S.cinematic_sources(archive, rig([('root', -1, (0, 0, 0)), ('arm', 0, (1, 0, 0))]), path)[0], [])


class PerformerTests(unittest.TestCase):
    """A cutscene clip goes to the model it is the performance of, not to every model its joints cover."""
    harvester = 'character/enemy/enm_hydro_harvester/enm_hydro_harvester.model'
    tank = 'character/enemy/enm_hydro_harvester_tank/enm_hydro_harvester_tank.model'
    felton = 'character/npc/npc_felton_razz/npc_felton_razz.model'
    crate = 'environment/test/prop/test_prop_crate_01/test_prop_crate_01.model'
    body = [('a_body', -1, (0, 0, 0))]
    biped = [('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0)), ('head', 1, (0, 2, 0))]
    shots = [('ocean_rt_shot01_enm_Hydro_Harvester_Tank_master6', body), ('ocean_rt_shot01_enm_Hydro_Harvester_master1', body),
             ('ocean_rt_shot01_Mystery', body), ('ocean_rt_shot01_Grimroth', biped)]

    def archive(self):
        actors = {'harvester': (r'character\enemy\enm_Hydro_Harvester\enm_Hydro_Harvester.actor', self.harvester),
                  'tank': (r'character\enemy\enm_Hydro_Harvester_Tank\enm_Hydro_Harvester_Tank.actor', self.tank),
                  'felton': (r'character\npc\npc_Felton_Razz\npc_Felton_Razz_cine.actor', self.felton)}
        blob = animset([(name, clip(name, [j[0] for j in joints], [j[2] for j in joints])) for name, joints in self.shots],
                       strings=[name for name, _joints in self.shots] + [actor for actor, _model in actors.values()])
        assets = [('c1', 'cinematic2', 'cinematic/ocean_rt/ocean_rt.cinematic2', blob)]
        for key, (actor, model_path) in actors.items():
            assets.append((key, 'actor', actor.lower().replace('\\', '/'), model_path.replace('/', '\\').encode() + b'\0'))
        for path, joints in ((self.harvester, self.body), (self.tank, self.body), (self.felton, self.biped),
                             (self.crate, self.body)):
            assets.append(('m:' + path, 'model', path, model(joints)))
        return FakeArchive(assets)

    def names(self, path, joints):
        found = S.cinematic_sources(self.archive(), rig(joints), path)[0]
        return [c['name'] for source in found for c in source['clips']]

    def test_the_performer_is_read_from_the_end_of_the_clip_name(self):
        words = S.asset_words(self.harvester)
        self.assertTrue(S.names_asset('pok_ocean_rt_shot01_enm_Hydro_Harvester_master1', words))
        self.assertTrue(S.names_asset('pok_ocean_rt_shot01_enm_Hydro_Harvester', words))
        self.assertFalse(S.names_asset('pok_ocean_rt_shot01_enm_Hydro_Harvester_Tank_master6', words))
        self.assertTrue(S.names_asset('vel_qwark_rt_shot2_npc_Solana_Trooper2', {'npc_solana_trooper'}))
        self.assertFalse(S.names_asset(None, words))
        self.assertEqual(S.asset_words('character/hero/hero_ratchet/bangle_set/hero_ratchet_cap/hero_ratchet_cap.model'),
                         {'hero_ratchet', 'bangle_set', 'hero_ratchet_cap'})

    def test_one_joint_models_get_only_their_own_clips(self):
        self.assertEqual(self.names(self.harvester, self.body), ['ocean_rt_shot01_enm_Hydro_Harvester_master1'])
        self.assertEqual(self.names(self.tank, self.body), ['ocean_rt_shot01_enm_Hydro_Harvester_Tank_master6'])
        self.assertEqual(self.names(self.crate, self.body), [])   # covered by three clips, the performer of none

    def test_a_performer_alias_goes_to_the_cutscene_actor_with_the_matching_rig(self):
        self.assertEqual(self.names(self.felton, self.biped), ['ocean_rt_shot01_Grimroth'])

    def test_a_one_joint_model_plays_its_cutscene_clip(self):
        archive = self.archive()
        library = AnimationLibrary(archive, model(self.body), []).with_cinematic(archive, self.harvester, replace=True)
        self.assertEqual(library.clip_notes(), [None])
        self.assertEqual(library.clip_frames(0)[1], 'absolute skeletal')

    def test_only_a_cutscene_clip_without_translation_evidence_is_absolute(self):
        from rc2016.formats.clips import CUTSCENE_BASIS, classify
        frames = sample_transforms(decode_clip(clip('x', ['a_body'], [(3, 0, 0)]), 0, {}))
        self.assertEqual(classify(rig(self.body), frames)[0], 'unsupported')
        kind, evidence = classify(rig(self.body), frames, cutscene=True)
        self.assertEqual((kind, evidence['classification_basis']), ('absolute skeletal', CUTSCENE_BASIS))


class ClipVersionTests(unittest.TestCase):
    def test_version_21_uses_the_version_15_layout(self):
        for version in (15, 21):
            data = clip('x', ['root', 'spine'], [(0, 0, 0), (1, 2, 3)], version=version)
            frames = sample_transforms(decode_clip(data, 0, {}))
            self.assertEqual(frames[0][1]['translation'], [1.0, 2.0, 3.0])

    def test_version_143_uses_the_version_15_layout(self):
        data = clip('x', ['root', 'spine'], [(0, 0, 0), (1, 2, 3)], version=143)
        self.assertEqual(sample_transforms(decode_clip(data, 0, {}))[0][1]['translation'], [1.0, 2.0, 3.0])

    def test_other_versions_stay_unsupported(self):
        with self.assertRaisesRegex(ValueError, 'only PS4 clip versions 15, 21 and 143'):
            decode_clip(clip('x', ['root'], [(0, 0, 0)], version=22), 0, {})


class FilteredExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rows = rig([('root', -1, (0, 0, 0)), ('spine', 0, (1, 2, 3))])
        self.profile = dict(inheritance='full', inheritance_candidates=['full'], classification_overrides={})

    def tearDown(self):
        self.temp.cleanup()

    def extract(self, blob, names):
        path = self.root / 'test.cinematic2'
        path.write_bytes(blob)
        return extract_set(path, self.rows, self.root / 'cache', self.profile, lambda *a, **k: None, clip_names=names)[0]

    def test_named_subset_is_extracted_and_classified(self):
        blob = animset([('other', clip('other', ['root', 'spine'], [(0, 0, 0), (1, 2, 3)])),
                        ('shot1_npc_test', clip('shot1_npc_test', ['root', 'spine'], [(0, 0, 0), (1, 2, 3)]))],
                       strings=['shot1_npc_test'])
        offset = next(r['offset'] for r in S.clip_directory(blob) if r['name'] == 'shot1_npc_test')
        entries = self.extract(blob, {offset: 'shot1_npc_test'})
        self.assertEqual([(e['order'], e['name'], e['status'], e['kind']) for e in entries],
                         [(1, 'shot1_npc_test', 'included', 'absolute skeletal')])

    def test_name_that_does_not_hash_to_the_clip_is_rejected(self):
        blob = animset([('shot1_npc_test', clip('shot1_npc_test', ['root', 'spine'], [(0, 0, 0), (1, 2, 3)]))])
        offset = S.clip_directory(blob)[0]['offset']
        entry = self.extract(blob, {offset: 'wrong_name'})[0]
        self.assertEqual((entry['status'], entry['reason']), ('malformed', 'Clip name/hash mismatch'))


class AnimsetClipNaming(unittest.TestCase):
    """A clip whose own name does not hash to its name hash takes the animset string that does."""
    joints = [('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0))]

    def blob(self):
        return animset([('Engaged_Idle', clip('Engaged_Idle', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)], version=143))],
                       strings=['igAnimWrinkleBG', 'Engaged_Idle'])

    def test_extract_set_names_the_clip_from_the_animset_strings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'test.animset'
            path.write_bytes(self.blob())
            profile = dict(inheritance='full', inheritance_candidates=['full'], classification_overrides={})
            entries = extract_set(path, rig(self.joints), Path(temp) / 'cache', profile, lambda *a, **k: None)[0]
        self.assertEqual([(e['name'], e['status']) for e in entries], [('Engaged_Idle', 'included')])

    def test_library_names_the_clip_from_the_animset_strings(self):
        from unittest import mock
        rows = rig(self.joints)
        inspected = dict(skeleton=rows, inverse_bind_matrices=[[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]] * 2,
                         profile=dict(inheritance='full', inheritance_candidates=['full']))
        with mock.patch('rc2016.viewer.animation.inspect_model', return_value=inspected),                 mock.patch('rc2016.viewer.animation._blob', return_value=self.blob()):
            library = AnimationLibrary(None, b'', ['a.animset'])
        self.assertEqual([c['name'] for c in library.clips], ['Engaged_Idle'])


class RigComparisonTests(unittest.TestCase):
    anchor = rig([('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0)), ('head', 1, (0, .5, 0))])

    def test_float_noise_is_the_same_rig_and_extra_joints_are_listed(self):
        rows = rig([('root', -1, (0, 0, 0)), ('spine', 0, (0, 1 + 1e-6, 0)), ('head', 1, (0, .5, 0)),
                    ('backpack', 1, (0, 0, -.2))])
        result = S.rig_comparison(rows, self.anchor)
        self.assertTrue(result['same_rig'])
        self.assertEqual(result['absent_from_anchor'], ['backpack'])

    def test_parent_flags_and_proportions_must_match(self):
        cases = {'different parent joint': rig([('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0)), ('head', 0, (0, .5, 0))]),
                 'different joint flags': rig([('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0), 1), ('head', 1, (0, .5, 0))]),
                 'rest translation differs': rig([('root', -1, (0, 0, 0)), ('spine', 0, (0, 1.1, 0)), ('head', 1, (0, .5, 0))])}
        for reason, rows in cases.items():
            result = S.rig_comparison(rows, self.anchor)
            self.assertFalse(result['same_rig'])
            self.assertEqual([f['reason'] for f in result['failed']], [reason])

    def test_no_shared_joint_is_not_the_same_rig(self):
        self.assertFalse(S.rig_comparison(rig([('tail', -1, (0, 0, 0))]), self.anchor)['same_rig'])


class RetargetTests(unittest.TestCase):
    """An animset built for a model of other proportions (Evil Zurkon's set on Mr. Zurkon) plays on the model's rig."""
    source = [('root', -1, (0, 0, 0)), ('spine', 0, (0, 3, 0)), ('head', 1, (0, 6, 0))]
    small = [('root', -1, (0, 0, 0)), ('spine', 0, (0, 2, 0)), ('head', 1, (0, 4, 0))]

    @staticmethod
    def frame(translations):
        return [dict(hash=f'0x{game_crc32(name):08X}', translation=list(t), rotation_xyzw=[0., 0., 0., 1.], scale=[1., 1., 1.])
                for name, t in zip(('root', 'spine', 'head'), translations)]

    def test_the_same_rig_is_not_retargeted(self):
        self.assertIsNone(S.translation_retarget(rig(self.source), rig(self.source)))

    def test_offsets_from_the_source_rest_scale_onto_the_model(self):
        mapping = S.translation_retarget(rig(self.small), rig(self.source))
        self.assertEqual(sorted(round(k, 6) for _rest, _source, k in mapping.values()), [round(2 / 3, 6)] * 3)
        moved = S.retarget_frames([self.frame([(3, 0, 0), (0, 3, 0), (0, 9, 0)])], mapping)[0]
        # The root has no rest length to compare, so it takes the rig's median ratio.
        self.assertEqual([[round(v, 6) for v in row['translation']] for row in moved], [[2, 0, 0], [0, 2, 0], [0, 6, 0]])

    def test_only_a_clip_on_the_source_rig_is_retargeted(self):
        mapping = S.translation_retarget(rig(self.small), rig(self.source))
        self.assertTrue(S.clip_fits_source(self.frame([t for _n, _p, t in self.source]), mapping))
        self.assertFalse(S.clip_fits_source(self.frame([t for _n, _p, t in self.small]), mapping))

    def test_library_plays_the_source_rest_as_the_model_rest(self):
        from unittest import mock
        names = [n for n, _p, _t in self.source]
        blob = animset([('Idle', clip('Idle', names, [t for _n, _p, t in self.source])),
                        ('Own', clip('Own', names, [t for _n, _p, t in self.small]))],
                       strings=['Anim Set Built File', 'character/test/source.model'])
        archive = FakeArchive([('m1', 'model', 'character/test/source.model', model(self.source))])
        inspected = dict(skeleton=rig(self.small), inverse_bind_matrices=[[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]] * 3,
                         profile=dict(inheritance='full', inheritance_candidates=['full']))
        with mock.patch('rc2016.viewer.animation.inspect_model', return_value=inspected),                 mock.patch('rc2016.viewer.animation._blob', return_value=blob):
            library = AnimationLibrary(archive, b'small', ['a.animset'])
        self.assertEqual(library.retarget['a.animset']['model'], 'character/test/source.model')
        for index in range(2):
            _clip, kind, frames = library.clip_frames(index)
            self.assertEqual(kind, 'absolute skeletal')
            self.assertEqual([[round(v, 6) for v in row['translation']] for row in frames[0]],
                             [list(map(float, t)) for _n, _p, t in self.small])

    def test_pipeline_retargets_only_from_another_model(self):
        from rc2016.pipeline.run import animset_translation_retarget
        archive = FakeArchive([('m1', 'model', 'character/test/source.model', model(self.source)),
                               ('m2', 'model', 'character/test/small.model', model(self.small))])
        blob = animset([('Idle', clip('Idle', ['root'], [(0, 0, 0)]))], strings=['Anim Set Built File', 'character/test/source.model'])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'a.animset'
            path.write_bytes(blob)
            found = animset_translation_retarget(archive, path, rig(self.small), 'm2')
            self.assertEqual(found['model'], 'character/test/source.model')
            self.assertEqual(len(found['joints']), 3)
            self.assertIsNone(animset_translation_retarget(archive, path, rig(self.source), 'm1'))


class BorrowTests(unittest.TestCase):
    joints = [('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0))]

    def archive(self, target_joints):
        return FakeArchive([('anim', 'animset', 'character/a/a.animset', b''),
                            ('ma', 'model', 'character/a/a.model', model(self.joints)),
                            ('mb', 'model', 'character/b/b.model', model(target_joints)),
                            ('act', 'actor', 'character/a/a.actor', b'')])

    graph = {'act': [dict(resolved_ids=['ma']), dict(resolved_ids=['anim'])]}

    def test_matching_rig_borrows_with_provenance(self):
        archive = self.archive(self.joints)
        rows = model_skeleton(archive.asset_data('mb'))
        source = S.borrowed_animset(archive, self.graph, 'character/a/a.animset', rows, 'mb')
        self.assertEqual((source['asset_id'], source['source_type']), ('anim', S.BORROWED_SOURCE))
        self.assertEqual(source['provenance']['rig_anchor_model'], 'character/a/a.model')

    def test_different_rig_fails_closed(self):
        archive = self.archive([('root', -1, (0, 0, 0)), ('spine', 0, (0, 2, 0))])
        rows = model_skeleton(archive.asset_data('mb'))
        with self.assertRaisesRegex(ValueError, 'has this rest rig'):
            S.borrowed_animset(archive, self.graph, 'character/a/a.animset', rows, 'mb')

    def test_already_paired_model_is_rejected(self):
        archive = self.archive(self.joints)
        rows = model_skeleton(archive.asset_data('ma'))
        with self.assertRaisesRegex(ValueError, 'already pairs'):
            S.borrowed_animset(archive, self.graph, 'character/a/a.animset', rows, 'ma')


class CinematicDirectoryMemo(unittest.TestCase):
    def test_a_second_call_on_the_same_archive_does_not_reread_the_cutscenes(self):
        blob = animset([('shot1_npc_test', clip('shot1_npc_test', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)]))],
                       strings=['shot1_npc_test'])

        class CountingArchive(FakeArchive):
            def __init__(self, assets):
                super().__init__(assets)
                self.reads = []

            def asset_data(self, key):
                self.reads.append(key)
                return super().asset_data(key)

        archive = CountingArchive([('c1', 'cinematic2', 'cinematic/a/a.cinematic2', blob),
                                   ('c2', 'cinematic2', 'cinematic/b/b.cinematic2', blob),
                                   ('m', 'model', 'character/m.model', b'')])
        rows = rig([('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0))])
        first = S.cinematic_sources(archive, rows, 'character/npc_test.model')
        self.assertEqual(sorted(archive.reads), ['c1', 'c2'])
        second = S.cinematic_sources(archive, rows, 'character/npc_test.model')
        self.assertEqual(sorted(archive.reads), ['c1', 'c2'])
        self.assertEqual(first, second)
        self.assertEqual(len(first[0]), 2)


class FirstFramesDecode(unittest.TestCase):
    def test_first_frames_decodes_only_the_leading_frame_and_reports_that_count(self):
        data = clip('x', ['root', 'spine'], [(0, 0, 0), (1, 2, 3)], frames=3, duration=0.1)
        full = decode_clip(data, 0, {})
        head = decode_clip(data, 0, {}, first_frames=1)
        self.assertEqual(full['metadata']['original_frame_count'], 3)
        self.assertEqual(head['metadata']['original_frame_count'], 1)
        self.assertEqual(len(head['sample_times_seconds']), 1)
        full_frames = sample_transforms(full)
        self.assertEqual(len(full_frames), 3)
        self.assertEqual(sample_transforms(head), full_frames[:1])


class ClipNotes(unittest.TestCase):
    joints = [('root', -1, (0, 0, 0)), ('spine', 0, (0, 1, 0))]

    def library(self, clips, animsets):
        """An AnimationLibrary without its archive: only the state clip_notes reads."""
        library = AnimationLibrary.__new__(AnimationLibrary)
        library.rows = rig(self.joints)
        library.names = {int(row['hash'], 16): row['name'] for row in library.rows}
        library.animset_data = animsets
        library.clips = clips
        library._kinds = {}
        return library

    @staticmethod
    def clips_of(path, blob):
        return [dict(animset=path, offset=r['offset'], size=r['declared_size'], name=r['name'] or '')
                for r in S.clip_directory(blob)]

    def test_a_repeated_clip_is_a_duplicate_and_layers_and_playable_clips_are_told_apart(self):
        idle = clip('Idle', ['root', 'spine'], [(0, 0, 0), (0, 1, 0)])       # equal to the rest translations
        aim = clip('Aim', ['root', 'spine'], [(0, 0, 0), (0, 0, 0)])        # zero translations: an additive layer
        first = animset([('Idle', idle), ('Aim', aim)])
        second = animset([('Idle', idle)])                                  # repeats the first animset's Idle
        library = self.library(self.clips_of('a.animset', first) + self.clips_of('b.animset', second),
                               {'a.animset': first, 'b.animset': second})
        self.assertEqual(library.clip_notes(), [None, 'layer', 'duplicate'])


if __name__ == '__main__':
    unittest.main()
