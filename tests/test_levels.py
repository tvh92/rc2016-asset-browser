"""Pure math tests for the level viewer: camera, presentation basis, mirrored instances, PNG capture."""
import math
import unittest
import zlib

from rc2016.levels.panel import drop_uploaded_pixels, restore_native_pixels, zone_names
from rc2016.levels.scene import core_bounds, hidden_by_default, mirrored, zone_label
from rc2016.levels.viewport import BASIS, look_vectors, overview_camera, png_bytes, view_matrix


def apply(m, p):   # column-major 4x4 times point
    return tuple(m[i] * p[0] + m[4 + i] * p[1] + m[8 + i] * p[2] + m[12 + i] for i in range(3))


class LevelCameraTests(unittest.TestCase):
    def test_basis_maps_native_y_up_to_z_up(self):
        self.assertEqual(apply(BASIS, (1, 2, 3)), (1, -3, 2))

    def test_look_vectors_are_orthonormal_and_z_up(self):
        f, r, u = look_vectors(30.0, -20.0)
        for v in (f, r, u):
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in v)), 1.0)
        self.assertAlmostEqual(sum(a * b for a, b in zip(f, r)), 0.0)
        self.assertAlmostEqual(sum(a * b for a, b in zip(f, u)), 0.0)
        self.assertGreater(u[2], 0.0)
        self.assertAlmostEqual(look_vectors(90.0, 0.0)[0][1], 1.0)

    def test_view_matrix_puts_target_on_negative_z(self):
        eye = (10.0, -5.0, 3.0)
        f, r, u = look_vectors(90.0, -30.0)
        target = tuple(e + 7.0 * x for e, x in zip(eye, f))
        view = apply(view_matrix(eye, 90.0, -30.0), target)
        self.assertAlmostEqual(view[0], 0.0, places=5); self.assertAlmostEqual(view[1], 0.0, places=5)
        self.assertAlmostEqual(view[2], -7.0, places=5)
        right = apply(view_matrix(eye, 90.0, -30.0), tuple(e + x for e, x in zip(eye, r)))
        self.assertAlmostEqual(right[0], 1.0, places=5)

    def test_overview_looks_at_bounds_centre(self):
        eye, yaw, pitch, speed = overview_camera(((0, 0, 0), (100, 20, 200)))
        f, _r, _u = look_vectors(yaw, pitch)
        centre = (50.0, -100.0, 10.0)   # presentation coordinates
        to_centre = [c - e for c, e in zip(centre, eye)]
        length = math.sqrt(sum(x * x for x in to_centre))
        self.assertAlmostEqual(sum(a * b / length for a, b in zip(to_centre, f)), 1.0, places=5)
        self.assertGreater(speed, 0)


class LevelSceneTests(unittest.TestCase):
    def test_mirrored_uses_determinant_sign(self):
        identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [5, 6, 7, 1]]
        flipped = [[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        sheared = [[1, 0.5, 0, 0], [0, 1, 0, 0], [0, 0, 2, 0], [0, 0, 0, 1]]
        self.assertFalse(mirrored(identity)); self.assertTrue(mirrored(flipped)); self.assertFalse(mirrored(sheared))

    def test_collision_and_template_models_start_hidden(self):
        self.assertTrue(hidden_by_default('gameplay\\gbl_gp_hero_collision_01.model'))
        self.assertTrue(hidden_by_default('environment/template/template_Square.model'))
        self.assertFalse(hidden_by_default('environment/novalis/architecture/tb_nov_bldg1_floor_4x4.model'))

    def test_core_bounds_trim_distant_backdrops(self):
        points = [(float(i), 0.0, -float(i)) for i in range(100)] + [(5000.0, 0.0, -5000.0)]
        low, high = core_bounds(points)
        self.assertEqual(low[0], 2.0); self.assertEqual(high[0], 98.0)
        self.assertEqual(high[2], -2.0)
        self.assertIsNone(core_bounds([]))

    def test_zone_names_disambiguate_shared_stems(self):
        zones = ['levels/Novalis/openArea/OpenWorld/default_region/art_buildings.zone',
                 'levels/Novalis/openArea/NOV_mis_1/lighting.zone', 'levels/Novalis/openArea/OpenWorld/default_region/lighting.zone']
        names = zone_names(zones)
        self.assertEqual(names[zones[0]], 'art_buildings')
        self.assertEqual(names[zones[1]], 'lighting (NOV_mis_1)')
        self.assertEqual(zone_label('levels\\x\\sound.zone'), 'sound')

    def test_png_rows_are_flipped_to_top_first(self):
        data = png_bytes(1, 2, bytes([1, 2, 3, 4, 5, 6]))   # bottom row (1,2,3), top row (4,5,6)
        self.assertTrue(data.startswith(b"\x89PNG"))
        idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
        self.assertEqual(zlib.decompress(idat), b"\x00\x04\x05\x06\x00\x01\x02\x03")


class LevelTextureCacheTests(unittest.TestCase):
    def test_native_upload_keeps_pixels_for_a_later_raw_gl_object(self):
        pixels=bytes(65537)
        texture=dict(path='shared.texture',pixels=pixels,slices=[pixels],srgb=True)
        cache={'shared.texture':texture};uploaded=set()
        drop_uploaded_pixels(cache,{'native.material':dict(native_textures={5:texture})},uploaded)
        self.assertEqual(uploaded,set())
        self.assertIs(cache['shared.texture']['pixels'],pixels)
        # The later raw entry must still carry pixels for its distinct upload.
        raw=cache['shared.texture']
        drop_uploaded_pixels(cache,{'hand.material':dict(_c=raw)},uploaded)
        self.assertIs(raw['pixels'],pixels)
        self.assertNotIn('pixels',cache['shared.texture'])
        self.assertEqual(uploaded,{'shared.texture'})

    def test_released_native_textures_reload_once_per_gl_variant(self):
        from unittest.mock import patch
        released=dict(path='shared.texture',srgb=True,array_size=1)
        def entry():
            return dict(native_translation=dict(normal=dict(api=2)),native_textures={0:released,2:released})
        loaded=[]
        def load(archive,path):
            loaded.append(path);return dict(path=path,srgb=True,array_size=1,pixels=b'x')
        posted=set()
        first,second={'a.material':entry()},{'b.material':entry(),'c.material':entry()}
        with patch('rc2016.viewer.textures.load_texture',side_effect=load):
            restore_native_pixels(None,first,posted)
            restore_native_pixels(None,second,posted)
        # sRGB colour (api 0) and linear normal (api 2) are two GL objects from one decode.
        self.assertEqual(loaded,['shared.texture'])
        self.assertEqual(first['a.material']['native_textures'][0]['pixels'],b'x')
        self.assertEqual(first['a.material']['native_textures'][2]['pixels'],b'x')
        # Later materials reuse the uploaded GL objects and need no pixels.
        self.assertIs(second['b.material']['native_textures'][0],released)
        self.assertEqual(posted,{('shared.texture',True),('shared.texture',False)})

    def test_only_uploaded_large_textures_lose_their_pixels(self):
        def texture(path, size):
            pixels = bytes(size)
            return dict(path=path, width=1, height=1, pixels=pixels, slices=[pixels], scales=[(1.0, 1.0)])
        atlas_key = ("GroundPuddlesAtlas", "noise", "ripple")
        cache = {"c": texture("c", 65537), "small": texture("small", 64), "mask": texture("mask", 65537),
                 atlas_key: texture("GroundPuddlesAtlas:x", 65537), "missing": None}
        uploaded = set()
        drop_uploaded_pixels(cache, {"m/a.material": dict(_c=cache["c"], _g=cache["small"], _n=None,
                                                          _rock_mask=cache["mask"], _puddle_atlas=cache[atlas_key])},
                             uploaded)
        self.assertEqual(uploaded, {"c", "small", "GroundPuddlesAtlas:x"})
        self.assertEqual(cache["c"], dict(path="c", width=1, height=1, scales=[(1.0, 1.0)]))
        self.assertEqual(cache[atlas_key]["path"], "GroundPuddlesAtlas:x")
        self.assertNotIn("pixels", cache[atlas_key])
        self.assertIn("pixels", cache["small"])
        # Only sampled on the CPU so far: a later material may still upload it under a GL role.
        self.assertIn("pixels", cache["mask"])
        drop_uploaded_pixels(cache, {"m/b.material": dict(_c=cache["mask"])}, uploaded)
        self.assertNotIn("pixels", cache["mask"])
        self.assertNotIn("slices", cache["mask"])
        self.assertIsNone(cache["missing"])


if __name__ == "__main__":
    unittest.main()
