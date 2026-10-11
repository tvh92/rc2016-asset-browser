"""Equivalence of the batched animation maths / texture conversion with copies of the previous algorithms."""
import io
import math
import unittest

import numpy as np

from rc2016.export import models
from rc2016.formats import animation_transforms as at
from rc2016.formats.clips import worlds_batch
from rc2016.formats.skeleton import worlds


# ---- reference copies of the previous export code
def ref_quat(m):
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.asarray(q)
    return q / np.linalg.norm(q)


def ref_decompose(matrix):
    t = matrix[:3, 3].copy()
    basis = matrix[:3, :3].copy()
    scale = np.linalg.norm(basis, axis=0)
    if np.linalg.det(basis) < 0:
        scale[0] = -scale[0]
    rotation = basis / np.where(np.abs(scale) > 1e-12, scale, 1.0)
    u, _s, vt = np.linalg.svd(rotation)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:, -1] = -u[:, -1]; rotation = u @ vt
    return t, ref_quat(rotation), scale


def ref_local_trs(rows, world):
    return [ref_decompose(np.linalg.inv(world[r['parent']]) @ world[j] if r['parent'] >= 0 else world[j])
            for j, r in enumerate(rows)]


def ref_flip(values):
    values = values.copy()
    for f in range(1, len(values)):
        if np.dot(values[f], values[f - 1]) < 0:
            values[f] = -values[f]
    return values


def ref_srgb(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def rand_quat(rng, n):
    q = rng.normal(size=(n, 4))
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def make_rig(rng, count, mirrored=()):
    rows = []
    for j in range(count):
        parent = -1 if j == 0 or rng.random() < .15 else int(rng.integers(0, j))
        scale = rng.uniform(.6, 1.6, 3)
        if j in mirrored:
            scale[0] = -scale[0]
        rows.append(dict(name=f'j{j}', hash=f'0x{j:08X}', parent=parent, flags=int(rng.integers(0, 4)),
                         scale=scale.tolist(), rotation_xyzw=rand_quat(rng, 1)[0].tolist(),
                         translation=rng.normal(size=3).tolist()))
    return rows


def make_frames(rng, rows, nframes, drop=(), sentinel=()):
    frames = []
    for _ in range(nframes):
        frames.append([dict(name=r['name'], hash=r['hash'], scale=rng.uniform(.5, 1.5, 3).tolist(),
                            rotation_xyzw=None if j in sentinel else rand_quat(rng, 1)[0].tolist(),
                            rotation_sentinel=j in sentinel, translation=rng.normal(size=3).tolist())
                       for j, r in enumerate(rows) if j not in drop])
    return frames


class WorldsBatch(unittest.TestCase):
    def test_matches_worlds_for_both_rules(self):
        rng = np.random.default_rng(1)
        for rule in ('full', 'segment_scale_compensation_bit0'):
            rows = make_rig(rng, 14)
            frames = make_frames(rng, rows, 5, drop={3, 7}, sentinel={2, 9})
            batch = worlds_batch(rows, frames, rule)
            self.assertEqual(batch.shape, (5, 14, 4, 4))
            for f, frame in enumerate(frames):
                np.testing.assert_allclose(batch[f], np.array(worlds(rows, frame, rule)), atol=1e-12)
            rest = worlds_batch(rows, [()], rule)
            np.testing.assert_allclose(rest[0], np.array(worlds(rows, (), rule)), atol=1e-12)

    def test_zero_parent_scale_and_empty(self):
        rng = np.random.default_rng(2)
        rows = make_rig(rng, 4)
        rows[1].update(parent=0, flags=1)
        frames = make_frames(rng, rows, 2)
        frames[0][0]['scale'] = [0.0, 1.0, 1.0]
        np.testing.assert_allclose(worlds_batch(rows, frames, 'segment_scale_compensation_bit0')[0],
                                   np.array(worlds(rows, frames[0], 'segment_scale_compensation_bit0')), atol=1e-12)
        self.assertEqual(worlds_batch(rows, [], 'full').shape, (0, 4, 4, 4))


class LocalTrs(unittest.TestCase):
    def check(self, rows, frames, rule):
        t, r, s = models.local_trs_batch(rows, worlds_batch(rows, frames, rule))
        for f in range(len(frames)):
            ref = ref_local_trs(rows, worlds(rows, frames[f], rule))
            for j, (rt, rr, rs) in enumerate(ref):
                np.testing.assert_allclose(t[f, j], rt, atol=1e-6)
                np.testing.assert_allclose(r[f, j], rr, atol=1e-6)
                np.testing.assert_allclose(s[f, j], rs, atol=1e-6)

    def test_random_rigs_all_rules(self):
        rng = np.random.default_rng(3)
        for rule in ('full', 'segment_scale_compensation_bit0'):
            for count in (1, 2, 17):
                rows = make_rig(rng, count, mirrored={0, 3})
                self.check(rows, make_frames(rng, rows, 4), rule)

    def test_wrapper(self):
        rng = np.random.default_rng(4)
        rows = make_rig(rng, 6, mirrored={1})
        w = np.array(worlds(rows, (), 'full'))
        for got, ref in zip(models.local_trs(rows, w), ref_local_trs(rows, list(w))):
            for a, b in zip(got, ref):
                np.testing.assert_allclose(a, b, atol=1e-9)

    def test_singular_parent_raises(self):
        rows = make_rig(np.random.default_rng(5), 3)
        rows[1]['parent'] = 0
        w = worlds_batch(rows, [()], 'full')
        w[0, 0, :3, :3] = 0
        with self.assertRaises(np.linalg.LinAlgError):
            models.local_trs_batch(rows, w)


class HemisphereWriter(unittest.TestCase):
    def test_sign_continuity_matches_loop(self):
        q = rand_quat(np.random.default_rng(6), 200 * 5).reshape(200, 5, 4)
        d = np.einsum('fjk,fjk->fj', q[1:], q[:-1])
        sign = np.concatenate([np.ones((1, 5)), np.cumprod(np.where(d < 0, -1.0, 1.0), axis=0)])
        fast = q * sign[..., None]
        for j in range(5):
            np.testing.assert_array_equal(fast[:, j], ref_flip(q[:, j]))


def clip_with(rng, joints, frames, channels=True):
    rows = [dict(name=f'j{i}', hash=f'0x{i:08X}', packed_base_control_bytes=[int(rng.integers(0, 12)), int(rng.integers(0, 12))],
                 packed_base_i16=rng.integers(-3000, 3000, 7).tolist()) for i in range(joints)]
    chans = []
    if channels:
        for j in range(joints):
            for off in (0, 16, 20, 24, 28, 32, 36, 40):
                if rng.random() < .5:
                    chans.append(dict(joint_index=j, component_byte_offset=off,
                                      unsigned_deltas=rng.integers(0, 500, frames).tolist()))
    return dict(joints=rows, channels=chans, base_tail_hex='',
                metadata=dict(original_frame_count=frames, unknown_u32_28=0))


class SampleTransforms(unittest.TestCase):
    def test_equals_loop(self):
        rng = np.random.default_rng(7)
        for joints, frames in ((1, 1), (5, 7), (30, 12)):
            clip = clip_with(rng, joints, frames)
            self.assertEqual(at.sample_transforms(clip), at._sample_transforms_loop(clip))

    def test_empty_and_errors_fall_back(self):
        rng = np.random.default_rng(8)
        self.assertEqual(at.sample_transforms(clip_with(rng, 3, 0)), [])
        clip = clip_with(rng, 3, 2)
        clip['joints'][1]['packed_base_control_bytes'] = [31, 0]
        with self.assertRaisesRegex(ValueError, 'unsupported transform exponent'):
            at.sample_transforms(clip)
        clip = clip_with(rng, 3, 2, channels=False)
        clip['joints'][2]['packed_base_i16'] = [0, 0, 0, 0, 1, 1, 1]
        with self.assertRaisesRegex(ValueError, 'zero quaternion'):
            at.sample_transforms(clip)

    def test_sentinel_on_noncollapsed_joint_raises(self):
        clip = clip_with(np.random.default_rng(9), 2, 3, channels=False)
        clip['joints'][0]['packed_base_i16'] = [-32768] * 4 + [1, 2, 3]
        with self.assertRaisesRegex(ValueError, 'sentinel'):
            at.sample_transforms(clip)


class Textures(unittest.TestCase):
    def test_lut_and_float_path_identical(self):
        u8 = np.random.default_rng(10).integers(0, 256, (64, 64, 3), dtype=np.uint8)
        f = u8.astype(np.float32) / 255.0
        np.testing.assert_array_equal(models._SRGB_LUT[u8], ref_srgb(f))
        np.testing.assert_array_equal(models._srgb_to_linear(f), ref_srgb(f))
        np.testing.assert_array_equal(models._unit(u8), f)

    def test_png_u8_equals_float_quantisation(self):
        from PIL import Image
        a = np.random.default_rng(11).random((8, 8, 3)).astype(np.float32)
        p1, p2 = models._png(a), models._png(models._u8(a))
        self.assertEqual(p1, p2)
        np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(p1))), models._u8(a))

    def test_map_cache_lru(self):
        cache = models._MapCache(limit_bytes=100)
        entry = lambda: dict(base=np.zeros(60, np.uint8), mr=None, normal=None, emissive=None)
        cache.put('a', entry()); cache.put('b', entry())
        self.assertIsNone(cache.get('a'))
        self.assertIsNotNone(cache.get('b'))


if __name__ == '__main__':
    unittest.main()
