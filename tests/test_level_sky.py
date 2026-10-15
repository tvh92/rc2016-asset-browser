"""Level sky data: atmosphere zone strings, dome constants and the clear-sky profile (no archive, no GL)."""
import struct
import unittest
from unittest import mock

from rc2016.levels import sky


class AtmosphereZoneTests(unittest.TestCase):
    def test_zone_strings_name_atmosphere_and_capture(self):
        data = (b'1TAD' + b'\0' * 60 + b'atmosphere\\atm_Novalis\\atm_Novalis.atmosphere\0'
                + b'?\\textures\\atmosphere\\volumereflections\\0x1\\reflection.texture\0')
        atmospheres, captures = sky.atmosphere_zone_assets(data)
        self.assertEqual(atmospheres, ['atmosphere/atm_Novalis/atm_Novalis.atmosphere'])
        self.assertEqual(captures, ['textures/atmosphere/volumereflections/0x1/reflection.texture'])


class DomeConstantTests(unittest.TestCase):
    def test_instance_values_override_graph_defaults_by_dword(self):
        # As in Rk_Sky_Dome: vec3 rows at dwords 0 and 4, scalars elsewhere.
        rows = [dict(dword=0, key_hash=1), dict(dword=3, key_hash=2),
                *(dict(dword=d, key_hash=10 + d) for d in range(4, 23) if d not in (5, 6))]
        defaults = {1: [0.5, 0.25, 0.125], 2: 7.0, 14: [4.0, 5.0, 6.0], **{10 + d: float(d) for d in range(7, 23)}}
        instance = dict(parameters=[], unsupported_parameters=[
            dict(key_hash=2, raw_hex=struct.pack('<f', 9.0).hex()),
            dict(key_hash=14, raw_hex=struct.pack('<3f', 1.0, 2.0, 3.0).hex())])   # dword 4 row, 3 values
        with mock.patch('rc2016.shaders.families.graph_parameter_tables', return_value=([rows], defaults)), \
                mock.patch('rc2016.formats.materials.extract_material_parameters', return_value=instance):
            values = sky.dome_constants(b'', b'')
        self.assertEqual(values[:4], [0.5, 0.25, 0.125, 9.0])
        self.assertEqual(values[4:7], [1.0, 2.0, 3.0])
        self.assertEqual(values[22], 22.0)

    def test_missing_dword_raises(self):
        rows = [dict(dword=d, key_hash=d) for d in range(22)]
        with mock.patch('rc2016.shaders.families.graph_parameter_tables', return_value=([rows], {d: 1.0 for d in range(22)})), \
                mock.patch('rc2016.formats.materials.extract_material_parameters', return_value={}):
            with self.assertRaises(ValueError):
                sky.dome_constants(b'', b'')


class ClearSkyProfileTests(unittest.TestCase):
    def test_profile_keeps_clear_sky_and_fills_terrain_bins(self):
        import numpy as np
        size = 64
        blue, ground, cloud = np.array([0.1, 0.2, 0.4]), np.array([0.3, 0.3, 0.1]), np.array([1.0, 1.0, 1.0])
        side = np.empty((size, size, 3))
        side[: size // 2] = blue           # rows above the horizon (y = -t > 0)
        side[size // 2:] = ground
        side[size // 2 - 3: size // 2] = ground   # terrain just above the horizon
        up = np.tile(blue, (size, size, 1)); up[:8] = cloud   # clipped clouds are not sky
        down = np.tile(ground, (size, size, 1))
        profile = sky._profile_from_faces([side, side, up, down, side, side])
        self.assertEqual(len(profile), 15)
        for colour in profile:
            np.testing.assert_allclose(colour, blue, atol=1e-6)

    def test_no_clear_sky_gives_none(self):
        import numpy as np
        grey = np.full((16, 16, 3), 0.5)
        self.assertIsNone(sky._profile_from_faces([grey] * 6))


if __name__ == '__main__':
    unittest.main()


class ClearSkyHdrTests(unittest.TestCase):
    def test_unclamped_values_above_one_do_not_count_as_clear_sky(self):
        import numpy as np
        size = 64
        blue, glow = np.array([0.1, 0.2, 0.4]), np.array([0.9, 1.6, 2.4])   # HDR haze-tinted cloud, blue dominant
        up = np.tile(blue, (size, size, 1)); up[:32] = glow
        side = np.tile(blue, (size, size, 1))
        profile = sky._profile_from_faces([side, side, up, side, side, side])
        for colour in profile:
            np.testing.assert_allclose(colour, blue, atol=1e-6)

    def test_clear_sky_profile_decodes_the_probe_in_hdr(self):
        import numpy as np
        faces = [np.full((8, 8, 3), 0.4, np.float32) * np.array([0.25, 0.5, 1.0], np.float32) for _ in range(6)]
        with mock.patch('rc2016.levels.probe_lighting.probe_faces', return_value=(faces, {})) as probe:
            profile = sky.clear_sky_profile(object(), 'x.texture')
        probe.assert_called_once()
        np.testing.assert_allclose(profile[0], [0.1, 0.2, 0.4], atol=1e-6)


class DomeFogTests(unittest.TestCase):
    PROFILE = dict(heights=(-100.0, -52.0, 386.0, 926.0, 400.0), densities=(1.0, 0.1, 0.002, 0.0, 0.0))   # Novalis

    def ramps(self, cam_y):
        from rc2016.levels.fog import build_ramps
        return build_ramps(self.PROFILE, cam_y)

    def test_transmittance_rises_with_elevation_and_is_a_fraction(self):
        ramps = self.ramps(100.0)
        values = [sky.dome_fog_transmittance(sky_sin, ramps) for sky_sin in (0.05, 0.1, 0.2, 0.4, 0.8, 1.0)]
        self.assertTrue(all(0.0 < v <= 1.0 for v in values))
        self.assertEqual(values, sorted(values))
        self.assertLess(values[0], 0.8)       # the horizon is hazed
        self.assertGreater(values[-1], 0.95)  # the zenith is nearly clear

    def test_distance_beyond_a_few_thousand_units_no_longer_matters(self):
        from rc2016.levels.fog import fog_T
        ramps = self.ramps(150.0)
        near = fog_T((3000 * 0.866, 3000 * 0.5, 0.0), (0.0, 0.0, 0.0), ramps)
        self.assertAlmostEqual(near, sky.dome_fog_transmittance(0.5, ramps), places=3)

    def test_glsl_constants_match_the_python_reference(self):
        import re
        from rc2016.viewer import shading
        source = shading.SKY_FRAGMENT
        for name, value in (('SKY_FOG_DISTANCE', sky.DOME_FOG_DISTANCE), ('SKY_FOG_BRIGHTNESS', sky.DOME_FOG_BRIGHTNESS),
                            ('SKY_EXPOSURE', sky.SKY_EXPOSURE)):
            found = re.search(rf'const float {name} = ([0-9.]+);', source)
            self.assertIsNotNone(found, name)
            self.assertEqual(float(found.group(1)), value, name)
