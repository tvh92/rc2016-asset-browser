"""Rift Apart animation: synthetic clips and skin batches, plus real-install checks (RCRA_GAME, skipped without it)."""
import os
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rc2016.formats.animation_transforms import sample_transforms
from rc2016.formats.container import game_crc32
from rc2016.games.rcra import animation as ra
from rc2016.games.rcra import model as rm

GAME = os.environ.get('RCRA_GAME')
ROOT, CHILD = game_crc32('root'), game_crc32('child')


def _pack(values, widths):
    """Frame-major LSB-first bit packing of one stored sample, padded to 4 bytes."""
    word, bit = 0, 0
    for value, width in zip(values, widths):
        word |= value << bit
        bit += width
    return word.to_bytes((bit + 31) // 32 * 4, 'little')


def build_clip(samples, table=None, frames=None, name='Walk_test', fps=30.0, extra=None, flags=0):
    """A two-joint clip: root translation x (4 bits, 1/1024 units) and child quaternion z (8 bits) are sampled.

    ``samples``: stored (root tx, child qz) integer pairs. ``table``: the frame table (stored-sample index per entry,
    round(seconds * fps) + 1 entries) or None for one stored sample per frame."""
    frames = frames or (len(table) if table else len(samples))
    seconds = (frames - 1) / fps
    widths = (4, 8)
    elements = struct.pack('<2I', 32, 48 + 24) + bytes(w - 1 for w in widths)
    elements += bytes(-len(elements) % 4)
    stride = 4
    payload = b''.join(_pack(s, widths) for s in samples)
    if table is not None:
        flags |= ra.FLAG_FRAME_TABLE
        head = struct.pack(f'<{len(table)}H', *table)
        payload = head + bytes(-len(head) % 16) + payload
    base = (struct.pack('<7h', 0, 0, 0, 1024, 0, 0, 0) + bytes((10, 10))
            + struct.pack('<7h', 0, 0, 0, 1024, 1024, 0, 0) + bytes((10, 10)))
    hashes = struct.pack('<3I', ROOT, CHILD, 0xFFFFFFFF)
    built = bytearray(96)
    struct.pack_into('<IIIff', built, 0, 0, game_crc32(name), flags, fps, seconds)
    struct.pack_into('<4H', built, 32, 2, frames, 2, 0)
    struct.pack_into('<IHHH', built, 40, stride, 0, 0, len(samples))
    sections = {ra.BASE: base, ra.ELEMENTS: elements, ra.SAMPLES: payload, ra.JOINT_HASHES: hashes, ra.BUILT: bytes(built)}
    sections.update(extra or {})
    strings = b'animclip built\0' + name.encode() + b'\0'
    offset = 16 + 12 * len(sections) + len(strings)
    table_bytes, body = b'', b''
    for tag, data in sections.items():
        table_bytes += struct.pack('<III', tag, offset + len(body), len(data))
        body += data
    dat1 = struct.pack('<4sIIHH', b'1TAD', ra.CLIP_MAGIC, offset + len(body), len(sections), 0) + table_bytes + strings + body
    return struct.pack('<I', ra.CLIP_MAGIC) + bytes(32) + dat1


class SyntheticClipTests(unittest.TestCase):
    def test_header_and_samples(self):
        data = build_clip([(0, 0), (3, 64), (15, 255)])
        facts = ra.header(data)
        self.assertEqual((facts['name'], facts['bone_count'], facts['original_frame_count'], facts['stored_sample_count']),
                         ('Walk_test', 2, 3, 3))
        clip = ra.decode_clip(data, {ROOT: 'root', CHILD: 'child'})
        self.assertEqual([c['unsigned_deltas'] for c in clip['channels']], [[0, 3, 15], [0, 64, 255]])
        frames = sample_transforms(clip)
        self.assertEqual(len(frames), 3)
        self.assertEqual([f[0]['translation'][0] for f in frames], [0.0, 3 / 1024, 15 / 1024])
        self.assertEqual(frames[0][1]['translation'], [1.0, 0.0, 0.0])
        q = np.array(frames[2][1]['rotation_xyzw'])
        np.testing.assert_allclose(q, np.array([0, 0, 255, 1024]) / np.hypot(255, 1024))
        self.assertEqual(frames[1][0]['scale'], [1.0, 1.0, 1.0])

    def test_frame_table_repeats_stored_samples(self):
        # Five frames, two stored samples: the table plays 0 1 0 1 0 (and a closing 0 in its sixth entry).
        data = build_clip([(1, 10), (2, 20)], table=[0, 1, 0, 1, 0, 0], frames=5)
        clip = ra.decode_clip(data)
        self.assertEqual(clip['metadata']['original_frame_count'], 5)
        self.assertEqual(clip['channels'][0]['unsigned_deltas'], [1, 2, 1, 2, 1])
        self.assertEqual(len(ra.decode_clip(data, first_frames=2)['channels'][1]['unsigned_deltas']), 2)

    def test_frame_table_without_a_closing_entry_when_the_end_pose_is_counted(self):
        # frames == seconds * fps + 1: the table has exactly one entry per frame.
        data = build_clip([(1, 10), (2, 20)], table=[0, 1, 1], frames=3)
        self.assertEqual(ra.decode_clip(data)['channels'][0]['unsigned_deltas'], [1, 2, 2])

    def test_rejections(self):
        with self.assertRaises(ra.UnsupportedClipError):
            ra.decode_clip(b'\0' * 64)
        short = bytearray(build_clip([(1, 1), (2, 2)]))
        _dat1, table = ra.clip_dat1(bytes(short))
        struct.pack_into('<H', short, 36 + table[ra.BUILT][0] + 48, 1)   # one stored sample declared, two stored
        with self.assertRaisesRegex(ValueError, 'stored sample count|payload size'):
            ra.decode_clip(bytes(short))
        with self.assertRaisesRegex(ValueError, 'frame table points past'):
            ra.decode_clip(build_clip([(1, 1)], table=[0, 3, 0], frames=3))
        padded = build_clip([(0, 0)])
        # Set a padding bit above the 12 used bits of the only stored sample.
        _dat1, table = ra.clip_dat1(padded)
        offset, _size = table[ra.SAMPLES]
        corrupt = bytearray(padded)
        corrupt[36 + offset + 2] |= 0x80
        with self.assertRaisesRegex(ValueError, 'padding'):
            ra.decode_clip(bytes(corrupt))

    def test_custom_track_clip_is_not_skeletal(self):
        data = build_clip([(0, 0)])
        dat1, table = ra.clip_dat1(data)
        # Rename the base-state section: a clip with only custom float tracks has none.
        offset = 16 + 12 * list(table).index(ra.BASE)
        corrupt = bytearray(data)
        struct.pack_into('<I', corrupt, 36 + offset, ra.CUSTOM_TRACKS)
        with self.assertRaisesRegex(ra.UnsupportedClipError, 'custom float tracks'):
            ra.decode_clip(bytes(corrupt))


class SkinBatchTests(unittest.TestCase):
    def test_groups_of_one_and_several_influences(self):
        # 18 vertices: a full one-influence group (joint 2), then a two-vertex group of (joint, weight) pairs.
        skin = bytes([0]) + bytes([2] * 16) + bytes([1]) + bytes([0, 200, 1, 56, 1, 128, 0, 128])
        batches = [(0, 0, 0, 0, 18, 0)]
        lists, joints, weights = rm._mesh_weights(skin, b'', batches, 0, 1, 18, 3, 0, True)
        self.assertEqual(lists[0], [(2, 1.0)])
        self.assertEqual(lists[16], [(0, 200 / 256), (1, 56 / 256)])
        self.assertEqual(joints[17, :2].tolist(), [1, 0])
        np.testing.assert_allclose(weights.sum(axis=1), 1.0)

    def test_palette_and_joint_base(self):
        remap = struct.pack('<2H', 300, 7)
        skin = bytes([0, 1, 0]) + bytes([0, 0, 1])
        batches = [(0, 0, 2, 0, 2, 0), (3, 5, 0, 0, 2, 2)]   # palette of 2 at byte 0; then base 5
        lists, _j, _w = rm._mesh_weights(skin, remap, batches, 0, 2, 4, 400, 0, False)
        self.assertEqual([row[0][0] for row in lists], [7, 300, 5, 6])

    def test_rejections(self):
        with self.assertRaisesRegex(ValueError, 'without weights'):
            rm._mesh_weights(bytes([0, 0, 0]), b'', [(0, 0, 0, 0, 2, 0)], 0, 1, 3, 1, 0, False)
        with self.assertRaisesRegex(ValueError, 'outside the skeleton'):
            rm._mesh_weights(bytes([0, 5]), b'', [(0, 0, 0, 0, 1, 0)], 0, 1, 1, 2, 0, False)
        with self.assertRaisesRegex(ValueError, 'runs past'):
            rm._mesh_weights(bytes([1, 0, 255]), b'', [(0, 0, 0, 0, 1, 0)], 0, 1, 1, 2, 0, False)


@unittest.skipUnless(GAME, 'RCRA_GAME is not set')
class RealAnimationTests(unittest.TestCase):
    FISH = 'characters/ambient/amb_fish_01/amb_fish_01.model'
    RATCHET = 'characters/hero/hero_ratchet/hero_ratchet.model'

    @classmethod
    def setUpClass(cls):
        from rc2016.games.rcra.source import RcraSource
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = RcraSource(GAME, Path(cls.temp.name) / 'out')

    @classmethod
    def tearDownClass(cls):
        cls.source.close()
        cls.temp.cleanup()

    def data(self, path):
        return self.source.bytes(self.source.resolve(path)[0])[0]

    def library(self, path):
        from rc2016.games.rcra.clip_library import animation_library
        return animation_library(self.source, self.data(path), path)

    def test_fish_clips_rest_pose_and_first_frame(self):
        from rc2016.formats.skeleton import worlds
        library = self.library(self.FISH)
        self.assertEqual(list(library.animset_data), ['characters/ambient/amb_fish_01/amb_fish_01.animset'])
        self.assertEqual(len(library.clips), 5)
        rest = np.stack(worlds(library.rows, (), library.inheritance)) @ np.stack(library.inverse)
        self.assertLess(float(np.abs(rest - np.eye(4)).max()), 1e-5)
        index = library.labels().index('Swimming_Idle_amb_fish_01')
        _clip, kind, frames = library.clip_frames(index)
        self.assertEqual((kind, len(frames)), ('absolute skeletal', 76))
        rows = {r['hash']: r for r in library.rows}
        moved = [np.linalg.norm(np.subtract(j['translation'], rows[j['hash']]['translation']))
                 for j in frames[0] if j['hash'] in rows]
        self.assertLess(float(np.median(moved)), 1e-3)     # frame 0 sits on the rest pose within quantization
        shots = library.with_cinematic()
        self.assertEqual(len(shots.clips), 35)
        self.assertTrue(all(c['animset'].endswith('.cinematic2') for c in shots.clips[5:]))
        self.assertEqual(shots.clip_notes(), [None] * 35)

    def test_fish_batches_match_the_fixed_weight_records(self):
        data = self.data(self.FISH)
        decoded = rm.decode_model(self.FISH, data=data, as_arrays=True)
        dat1 = data[36:]
        offset, size = rm.dat1_sections(dat1)[rm.WEIGHTS]
        records = np.frombuffer(dat1[offset:offset + size], np.uint8).reshape(-1, 8)
        for mesh in decoded['meshes']:
            ref = records[mesh['first_weight_index']:mesh['first_weight_index'] + mesh['vertex_count']]
            expected = ref[:, 4:] / ref[:, 4:].sum(axis=1, keepdims=True)
            got = np.zeros((mesh['vertex_count'], 115))
            want = np.zeros_like(got)
            rows = np.arange(mesh['vertex_count'])
            np.add.at(got, (np.repeat(rows, 8), mesh['skin_joint_array'].ravel()), mesh['skin_weight_array'].ravel())
            np.add.at(want, (np.repeat(rows, 4), ref[:, :4].ravel().astype(int)), expected.ravel())
            np.testing.assert_allclose(got, want, atol=1e-12)

    def test_ratchet_animates_without_tearing(self):
        from rc2016.viewer.animation import BASIS
        data = self.data(self.RATCHET)
        decoded = rm.decode_model(self.RATCHET, data=data, as_arrays=True, lod='Highest')
        self.assertTrue(all(m['skinned'] for m in decoded['meshes']))
        library = self.library(self.RATCHET)
        self.assertIn('characters/hero/hero_ratchet/hero_ratchet.animset', library.animset_data)
        index = library.labels().index('hero_ratchet: relaxed_neutral_idle_hero_ratchet')
        pose = library.pose(index)['frames'][0].astype(float)
        native = np.einsum('ij,fjk,kl->fil', BASIS.T, pose, BASIS)
        lengths, rest = [], []
        for mesh in decoded['meshes']:
            p = np.c_[mesh['positions'], np.ones(len(mesh['positions']))]
            moved = sum(mesh['skin_weight_array'][:, s, None] * np.einsum('nij,nj->ni', native[mesh['skin_joint_array'][:, s]], p)
                        for s in range(8))[:, :3]
            edges = np.r_[mesh['triangles'][:, :2], mesh['triangles'][:, 1:]]
            lengths.append(np.linalg.norm(moved[edges[:, 0]] - moved[edges[:, 1]], axis=1))
            rest.append(np.linalg.norm(mesh['positions'][edges[:, 0]] - mesh['positions'][edges[:, 1]], axis=1))
        lengths, rest = np.concatenate(lengths), np.concatenate(rest)
        ratio = lengths[rest > 1e-5] / rest[rest > 1e-5]
        self.assertAlmostEqual(float(np.median(ratio)), 1.0, delta=1e-3)
        # The far tail is the armpit pinch of linear blend skinning (sub-millimetre shirt edges on the upper-arm
        # twist joints as the T-pose arms come down), so the bound is on the 99th percentile.
        self.assertLess(float(np.percentile(ratio, 99)), 1.5)   # measured 1.37


if __name__ == '__main__':
    unittest.main()
