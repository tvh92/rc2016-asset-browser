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
