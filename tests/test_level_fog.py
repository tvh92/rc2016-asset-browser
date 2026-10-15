import math
import struct
import unittest

from rc2016.levels import fog

# Reference outputs of the decoded formula, computed independently of rc2016.levels.fog (the
# haze_ref.py `fog_T` / `build_cb0` of the research notes) for the Novalis header profile at camera height 75.
NOVALIS = dict(heights=(-100.0, -52.0, 386.0, 926.0, 400.0), densities=(1.0, 0.1, 0.002, 0.0, 0.0))
VELDIN = dict(heights=(-150.0, -20.0, 40.0, 250.0, 1000.0), densities=(1.0, 0.4, 0.07, 0.01, 0.0))


def reference_T(cam, pos, ramps):
    """Straight transcription of the decoded vertex-shader code, one ramp at a time."""
    d = [pos[i] - cam[i] for i in range(3)]
    if abs(d[1]) <= 1e-4:
        d[1] = 1e-4
    dist = math.sqrt(sum(x * x for x in d))
    hs = ramps['start'] * d[1] / dist
    total = 0.0
    for k in range(4):
        clamp = lambda x: min(max(x, 0.0), 1.0)
        a_s, a_e = clamp(ramps['a'][k] * hs + ramps['b'][k]), clamp(ramps['a'][k] * d[1] + ramps['b'][k])
        total += ramps['alpha'][k] * (a_e ** 2 - a_s ** 2) + ramps['beta'][k] * (a_e - a_s)
    tau = dist / d[1] * total
    if tau > 0:
        tau **= ramps['exponent']
    return min(max(2.0 ** -tau, 0.0), 1.0)


class LevelFogTests(unittest.TestCase):
    def ray(self, ramps, distance, elevation_degrees, height=75.0):
        """T along a ray of the given length and elevation from a camera at ``height`` (native Y up)."""
        e = math.radians(elevation_degrees)
        return fog.fog_T((distance * math.cos(e), height + distance * math.sin(e), 0.0), (0.0, height, 0.0), ramps)

    def test_optical_density_mapping(self):
        self.assertEqual(fog.optical_density(0.0), 0.0)
        self.assertAlmostEqual(fog.optical_density(1.0), fog.FOG_DENSITY_SCALE * math.log(1e10))   # d = 1 clamps
        self.assertAlmostEqual(fog.optical_density(1.0), 0.228865, places=4)   # Veldin capture value at the first knot
        self.assertGreater(fog.optical_density(0.4), fog.optical_density(0.07))

    def test_ramps_reproduce_the_veldin_capture_where_the_header_agrees(self):
        # Knots 0, 2, 3, 4 match the capture; the capture's second knot (-24.125) differs from the header (-20).
        r = fog.build_ramps(VELDIN, 32.872456)
        self.assertAlmostEqual(r['a'][2], 0.004762, places=6)
        self.assertAlmostEqual(r['a'][3], 0.001333, places=6)
        self.assertAlmostEqual(r['beta'][2], 0.151476, places=5)
        self.assertAlmostEqual(r['beta'][3], 0.074921, places=5)
        self.assertAlmostEqual(r['alpha'][3], -0.037461, places=5)
        self.assertAlmostEqual(r['b'][3], -0.289503, places=5)

    def test_matches_the_decoded_formula(self):
        cam = (10.0, 75.0, -20.0)
        ramps = fog.build_ramps(NOVALIS, cam[1])
        for pos in ((500.0, 75.0, 0.0), (3000.0, 400.0, 100.0), (-900.0, -50.0, 2500.0), (40.0, 30.0, 20.0),
                    (200.0, 75.00005, 0.0), (0.0, 800.0, 3000.0)):
            self.assertAlmostEqual(fog.fog_T(pos, cam, ramps), reference_T(cam, pos, ramps), places=12)

    def test_novalis_known_values(self):
        ramps = fog.build_ramps(NOVALIS, 75.0)
        # Values of the research reference implementation for the same inferred constants.
        for distance, expected in ((500, 0.85), (1000, 0.64), (1500, 0.45), (2000, 0.29), (3000, 0.11)):
            self.assertAlmostEqual(self.ray(ramps, distance, 0.0), expected, delta=0.01)

    def test_no_fog_inside_the_start_distance(self):
        ramps = fog.build_ramps(NOVALIS, 75.0)
        for elevation in (-60.0, -5.0, 0.0, 5.0, 60.0):
            for distance in (0.5, 5.0, fog.FOG_START * 0.999):
                self.assertEqual(self.ray(ramps, distance, elevation), 1.0)

    def test_monotone_in_distance(self):
        ramps = fog.build_ramps(NOVALIS, 75.0)
        for elevation in (-10.0, 0.0, 3.0, 20.0):
            values = [self.ray(ramps, d, elevation) for d in (20, 100, 300, 800, 1500, 3000, 8000)]
            self.assertTrue(all(b <= a + 1e-12 for a, b in zip(values, values[1:])), (elevation, values))
            self.assertLess(values[-1], 1.0)

    def test_clear_above_the_density(self):
        ramps = fog.build_ramps(NOVALIS, 75.0)
        # Steeply upward rays leave the dense layer: much clearer than horizontal ones at the same distance.
        self.assertGreater(self.ray(ramps, 3000.0, 20.0), self.ray(ramps, 3000.0, 0.0) + 0.3)
        # A camera above the last knot with a ray going up sees no fog at all.
        high = fog.build_ramps(NOVALIS, 1500.0)
        self.assertEqual(fog.fog_T((0.0, 3000.0, 4000.0), (0.0, 1500.0, 0.0), high), 1.0)

    def test_near_terrain_is_nearly_unfogged(self):
        ramps = fog.build_ramps(NOVALIS, 75.0)
        for elevation in (-12.0, -6.0, 0.0):   # terrain below a camera at 75 stays above the dense layer (-52)
            self.assertGreater(self.ray(ramps, 200.0, elevation), 0.93)

    def test_continuous_through_the_horizontal_ray(self):
        ramps = fog.build_ramps(NOVALIS, 75.0)
        below, level, above = (fog.fog_T((2000.0, 75.0 + dy, 0.0), (0.0, 75.0, 0.0), ramps) for dy in (-0.01, 0.0, 0.01))
        self.assertAlmostEqual(below, level, places=3)
        self.assertAlmostEqual(above, level, places=3)
        # The shader's constant-density form for a near-horizontal ray (c88 * dist + c89, then ^p) agrees.
        tau = ramps['c88'] * 2000.0 + ramps['c89']
        self.assertAlmostEqual(2.0 ** -(tau ** ramps['exponent']), level, places=3)
        self.assertAlmostEqual(ramps['c89'], -ramps['c88'] * fog.FOG_START)

    def test_full_density_is_clamped(self):
        # header density 1 means 1 - d is clamped, not infinite optical depth.
        ramps = fog.build_ramps(dict(heights=(-100.0, 0.0, 100.0, 200.0, 300.0), densities=(1.0, 1.0, 1.0, 1.0, 1.0)), 50.0)
        value = self.ray(ramps, 1000.0, 0.0, 50.0)
        self.assertTrue(math.isfinite(value) and 0.0 <= value < 1e-6)

    def test_descending_knots_make_an_empty_ramp(self):
        ramps = fog.build_ramps(NOVALIS, 75.0)   # the last knot (400) is below the one before it (926)
        self.assertEqual((ramps['a'][3], ramps['alpha'][3], ramps['beta'][3]), (0.0, 0.0, 0.0))
        zero = fog.build_ramps(dict(heights=(-100.0, 100.0, 200.0, 300.0, 400.0), densities=(0.0,) * 5), 0.0)
        self.assertEqual(self.ray(zero, 5000.0, 0.0, 0.0), 1.0)

    def test_read_profile(self):
        header = bytearray(fog.HEADER_SIZE)
        struct.pack_into('<5f', header, fog.KNOT_OFFSET, -100.0, -52.0, 386.0, 926.0, 400.0)
        struct.pack_into('<5f', header, fog.DENSITY_OFFSET, 1.0, 0.1, 0.002, 0.0, 0.0)
        profile = fog.read_profile(bytes(header))
        self.assertEqual(profile['heights'], (-100.0, -52.0, 386.0, 926.0, 400.0))
        self.assertAlmostEqual(profile['densities'][1], 0.1, places=6)
        self.assertIsNone(fog.read_profile(bytes(header[:-4])))
        struct.pack_into('<f', header, fog.DENSITY_OFFSET, 3.0)
        self.assertIsNone(fog.read_profile(bytes(header)))
        struct.pack_into('<f', header, fog.DENSITY_OFFSET, 0.5)
        struct.pack_into('<f', header, fog.KNOT_OFFSET, float('nan'))
        self.assertIsNone(fog.read_profile(bytes(header)))

    def test_apply_fog_blends_towards_the_fog_colour(self):
        colour, haze = (0.2, 0.4, 0.1), (0.4, 0.7, 0.9)
        self.assertEqual(fog.apply_fog(colour, 1.0, haze), colour)
        self.assertEqual(fog.apply_fog(colour, 0.0, haze), haze)
        for m, c, h in zip(fog.apply_fog(colour, 0.25, haze), colour, haze):
            self.assertAlmostEqual(m, h + (c - h) * 0.25)

    def test_horizon_colour_is_the_lowest_bin(self):
        self.assertEqual(fog.horizon_colour([(0.4, 0.7, 0.9), (0.1, 0.2, 0.3)]), (0.4, 0.7, 0.9))
        self.assertIsNone(fog.horizon_colour(None))

    def test_fog_state_only_for_level_atmosphere(self):
        sky = dict(clear_sky=[(0.4, 0.7, 0.9), (0.1, 0.2, 0.3)], fog=NOVALIS)
        state = fog.fog_state(sky, 'Level atmosphere', 75.0)
        self.assertEqual(state['colour'], (0.4, 0.7, 0.9))
        self.assertEqual(state['ramps'], fog.build_ramps(NOVALIS, 75.0))
        self.assertIsNone(fog.fog_state(sky, 'Level atmosphere', 75.0, enabled=False))
        self.assertIsNone(fog.fog_state(sky, 'Shared HDRI', 75.0))
        self.assertIsNone(fog.fog_state(sky, 'Viewer rig', 75.0))
        self.assertIsNone(fog.fog_state(dict(clear_sky=None, fog=NOVALIS), 'Level atmosphere', 75.0))
        self.assertIsNone(fog.fog_state(dict(clear_sky=sky['clear_sky'], fog=None), 'Level atmosphere', 75.0))
        self.assertIsNone(fog.fog_state(None, 'Level atmosphere', 75.0))

    def test_uniform_values(self):
        off = fog.uniform_values(None)
        self.assertEqual(off[0], 0)
        on = fog.uniform_values(fog.fog_state(dict(clear_sky=[(0.4, 0.7, 0.9)], fog=NOVALIS), 'Level atmosphere', 75.0))
        self.assertEqual(on[0], 1)
        self.assertEqual(len(on), len(off))
        self.assertEqual(on[-1][2:], (fog.FOG_EXPONENT, fog.FOG_START))
        for vector in on[2:6]:
            self.assertEqual(len(vector), 4)


if __name__ == '__main__':
    unittest.main()
