"""Probe-volume reflection of the window glass (rc2016.levels.probe_volume; synthetic cells, no GL).

The archive tests read the game archive (RC2016_RESEARCH) and skip without it; the capture test also needs the saved
capture in Research/ and skips when it is absent."""
import math
import os
import struct
import unittest
from pathlib import Path

import numpy as np

from rc2016.levels import lightgrid as lg
from rc2016.levels import probe_volume as pv
from rc2016.levels.probe_lighting import face_direction
from rc2016.viewer import shading

ROOT = Path(__file__).resolve().parents[1]
NOVALIS_ZONE = 'levels/novalis/openarea/openworld/default_region/lighting.zone'


def dat1(sections, tail=b''):
    """A DAT1 container with the given {label: bytes} sections followed by ``tail`` (rc2016.levels.zone_format layout)."""
    table = 16 + 12 * len(sections)
    body, entries = b'', []
    for label, payload in sections.items():
        entries.append(struct.pack('<III', label, table + len(body), len(payload)))
        body += payload + b'\0' * (-len(payload) % 4)
    return b'1TAD' + b'\0' * 8 + struct.pack('<I', len(sections)) + b''.join(entries) + body + tail


def tiles(n, probes):
    cells = np.zeros((n, lg.CELLS_PER_TILE, lg.CELL_BYTES), np.uint8)
    cells[..., 0:3] = (128, 128, 255)
    cells[..., 16:20] = (128, 255, 128, 255)       # open cells
    cells[..., 20:24] = (255, 255, 255, 255)       # global cube
    return lg.pack_tiles(cells, [(8 + 16 * i, 8, 8) for i in range(n)], probes)


def synthetic_cube(index, directions):
    d = np.asarray(directions, np.float64)
    return (index + 1) * (0.1 + 0.25 * (d + 1.0))


def decode_formula(grid, selectors, point, direction, sky):
    """The decode's E for one point, corner by corner (WINDOW_GLASS_DECODE, probe volume and cube-255 blend)."""
    p = np.asarray(point, float) - 0.5
    cell = np.floor(p)
    f = p - cell
    ws, recs = [], []
    for dz in (0, 1):
        for dy in (0, 1):
            for dx in (0, 1):
                c = cell + (dx, dy, dz)
                tile = np.floor_divide(c, 16).astype(int)
                entry = grid.table[tile[2] & 63, tile[1] & 63, tile[0] & 63]
                if entry[3] < 0.5:
                    continue
                slot = int(entry[2] * 64 + entry[1] * 8 + entry[0])
                local = (c - tile * 16).astype(int)
                k = local[0] + 16 * local[1] + 256 * local[2]
                cells = grid.cells[slot, k]
                plane = cells[16:20] / 255.0
                delta = f - (dx, dy, dz)
                tri = (f[0] if dx else 1 - f[0]) * (f[1] if dy else 1 - f[1]) * (f[2] if dz else 1 - f[2])
                vis = min(1.0, max(0.0, 4.0 * float(((2 * plane[:3] - 1) * delta).sum()) + 16.0 * plane[3] - 7.0))
                ws.append(max(2.0 ** -18, tri * vis))
                recs.append(selectors[slot, k] / 255.0)
    if not ws:
        return np.asarray(sky, float)
    wn = np.array(ws) / sum(ws)
    cube = lambda i: np.asarray(sky, float) if i == 255 else synthetic_cube(i, direction)
    E = sum(w * (r[1] * cube(int(round(255 * r[0]))) + (1 - r[1]) * cube(int(round(255 * r[2])))) for w, r in zip(wn, recs))
    m = min(1.0, sum(w * r[3] for w, r in zip(wn, recs)))
    return (1 - m) * E + m * np.asarray(sky, float)


class ProbeListTests(unittest.TestCase):
    def test_tile_probe_paths_follow_the_offsets(self):
        names = [b'levels\\a\\envprobes\\0x1.texture', b'levels\\a\\envprobes\\0x2.texture']
        head = 16 + 12 * 2
        cells = bytes(lg.CELLS_PER_TILE * lg.CELL_BYTES)
        start = head + len(cells) + 8          # the strings follow the two sections
        offsets = [start, start + len(names[0]) + 1]
        data = dat1({lg.CELL_SECTION: cells, lg.PROBE_SELECTOR_SECTION: struct.pack('<2I', *offsets)},
                    b'\0'.join(names) + b'\0')
        self.assertEqual(lg.tile_probe_paths(data), [n.decode() for n in names])
        self.assertEqual(lg.tile_probe_paths(dat1({lg.CELL_SECTION: cells})), [])
        bad = dat1({lg.PROBE_SELECTOR_SECTION: struct.pack('<I', 1 << 30)})
        with self.assertRaises(ValueError):
            lg.tile_probe_paths(bad)

    def test_pack_tiles_keeps_one_list_per_tile(self):
        grid = tiles(2, [['b'], ['a', 'b']])
        self.assertEqual(grid.probes, [['b'], ['a', 'b']])
        self.assertEqual(tiles(1, None).probes, [[]])
        with self.assertRaises(ValueError):
            tiles(2, [['a']])

    def test_selectors_use_level_indices(self):
        grid = tiles(2, [['p2', 'p0'], ['p1', 'p2']])
        grid.cells[0, 5, 20:24] = (1, 200, 0, 255)       # p0, mix 200, p2
        grid.cells[1, 7, 20:24] = (0, 17, 1, 255)        # p1, p2
        grid.cells[1, 9, 20:24] = (4, 255, 4, 255)       # outside the tile's list
        volume = pv.build_selectors(grid)
        self.assertEqual(volume.paths, ['p0', 'p1', 'p2'])
        np.testing.assert_array_equal(volume.selectors[0, 5], (0, 200, 2, pv.VIEWER_CONFIDENCE))
        np.testing.assert_array_equal(volume.selectors[1, 7], (1, 17, 2, 0))
        np.testing.assert_array_equal(volume.selectors[1, 9], (255, 255, 255, 0))
        np.testing.assert_array_equal(volume.selectors[0, 0], (255, 255, 255, 0))
        self.assertEqual(volume.unlisted, 2)

    def test_too_many_probes_are_refused(self):
        with self.assertRaises(ValueError):
            pv.build_selectors(tiles(1, [[f'p{i}' for i in range(256)]]))

    def test_selector_atlas_matches_the_grid_atlas(self):
        grid = tiles(3, [[], [], []])
        grid.cells[2, 4095, 20:24] = (9, 8, 7, 6)
        layout = grid.atlas_layout(grid.cells)
        _colour, dirq = grid.atlas()
        np.testing.assert_array_equal(dirq[:, :, 1::2], layout[..., 16:20])
        x, y, z = grid.slots[2] * 16 + 15
        np.testing.assert_array_equal(layout[z, y, x, 20:24], (9, 8, 7, 6))


class CubeTests(unittest.TestCase):
    def test_face_uv_inverts_the_face_directions(self):
        rng = np.random.default_rng(1)
        for face in range(6):
            u, v = rng.uniform(-0.95, 0.95, (2, 50))
            d = np.array([face_direction(face, a, b) for a, b in zip(u, v)], float)
            got, gu, gv = pv.cube_face_uv(d)
            np.testing.assert_array_equal(got, face)
            np.testing.assert_allclose(gu, 0.5 * u + 0.5, atol=1e-12)
            np.testing.assert_allclose(gv, 0.5 * v + 0.5, atol=1e-12)

    def test_sample_faces_reads_texel_centres(self):
        n = 8
        faces = [np.full((n, n, 3), f, np.float32) + np.arange(n)[None, :, None] * 10 + np.arange(n)[:, None, None] * 100
                 for f in range(6)]
        for face in range(6):
            col, row = 3, 5
            u, v = (col + 0.5) / n * 2 - 1, (row + 0.5) / n * 2 - 1
            got = pv.sample_faces(faces, [face_direction(face, u, v)])[0]
            np.testing.assert_allclose(got, faces[face][row, col], rtol=1e-5)

    def test_atlas_places_probe_blocks(self):
        blocks = [np.full((64, 128, 16), i, np.uint8) for i in range(5)]
        volume = pv.ProbeVolume(['a'] * 5, None, blocks=blocks)
        atlas, (columns, rows) = volume.atlas_blocks(2)
        self.assertEqual((columns, rows, atlas.shape), (2, 3, (192, 256, 16)))
        self.assertEqual(atlas[64 * 2, 0, 0], 4)
        self.assertEqual(atlas[64, 128, 0], 3)
        self.assertEqual(atlas[64 * 2, 128, 0], 0)


class ReflectionTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.grid = tiles(2, [['p0', 'p1', 'p2'], ['p2', 'p3']])
        cells = self.grid.cells
        cells[..., 16:19] = rng.integers(0, 256, cells.shape[:2] + (3,))
        cells[..., 19] = rng.choice([0, 255, 200], cells.shape[:2])
        self.volume = pv.build_selectors(self.grid)
        sel = self.volume.selectors
        sel[..., 0] = rng.choice([0, 1, 2, 3, 255], sel.shape[:2])
        sel[..., 2] = rng.choice([0, 1, 2, 3, 255], sel.shape[:2])
        sel[..., 1] = rng.integers(0, 256, sel.shape[:2])
        sel[..., 3] = rng.choice([0, 0, 30, 255], sel.shape[:2])     # exercise the cube-255 blend
        self.rng = rng

    def test_matches_the_decode_formula(self):
        points = np.column_stack([self.rng.uniform(1, 31, 60), self.rng.uniform(1, 15, 60), self.rng.uniform(1, 15, 60)])
        points[:4] = [(15.9, 7.5, 7.5), (16.1, 7.5, 7.5), (16.0, 8.0, 8.0), (0.2, 0.2, 0.2)]   # tile seam, grid edge
        dirs = self.rng.normal(size=(60, 3))
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
        sky = self.rng.uniform(0.5, 2.0, (60, 3))
        got, m = pv.reflection(self.grid, self.volume, points, dirs, synthetic_cube, sky)
        for i in range(60):
            want = decode_formula(self.grid, self.volume.selectors, points[i], dirs[i], sky[i])
            np.testing.assert_allclose(got[i], want, rtol=2e-5, atol=1e-6)
        self.assertTrue(((m >= 0) & (m <= 1)).all())

    def test_viewer_confidence_reflects_local_probes_only(self):
        volume = pv.build_selectors(self.grid)
        volume.selectors[..., 0:3] = (2, 255, 2)
        got, m = pv.reflection(self.grid, volume, [(8.3, 8.6, 8.1)], [(0, 1, 0)], synthetic_cube, [(9.0, 9.0, 9.0)])
        np.testing.assert_allclose(got[0], synthetic_cube(2, (0, 1, 0)), rtol=1e-6)
        self.assertEqual(float(m[0]), 0.0)

    def test_outside_the_grid_is_the_sky(self):
        got, m = pv.reflection(self.grid, self.volume, [(500.0, 500.0, 500.0)], [(1, 0, 0)], synthetic_cube, [(3.0, 2.0, 1.0)])
        np.testing.assert_allclose(got[0], (3, 2, 1))
        self.assertEqual(float(m[0]), 1.0)


class AmbientTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(11)
        self.grid = tiles(2, [[], []])
        cells = self.grid.cells
        cells[..., 0:3] = rng.integers(0, 256, cells.shape[:2] + (3,))
        cells[..., 4:16] = rng.integers(0, 256, cells.shape[:2] + (12,))
        cells[..., 16:19] = rng.integers(0, 256, cells.shape[:2] + (3,))
        cells[..., 19] = rng.choice([0, 255, 200], cells.shape[:2])
        self.rng = rng

    def decode_formula(self, point, normal, sky_scale):
        """The decode's A_probe for one point: per corner B + hl (A - B) + gU C, hl = 0.5 + 0.5 d.N (not clamped),
        gU = 0.5 CB0[118] (1 + N.y), weight max(2^-18, tri vis), renormalised."""
        p = np.asarray(point, float) - 0.5
        cell = np.floor(p)
        f = p - cell
        acc, total = np.zeros(3), 0.0
        for dz in (0, 1):
            for dy in (0, 1):
                for dx in (0, 1):
                    c = cell + (dx, dy, dz)
                    tile = lg.tile_of(c[None])[0]
                    entry = self.grid.table[tuple((tile & (lg.TABLE - 1))[::-1])]
                    if entry[3] < 0.5:
                        continue
                    slot = int(entry[2] * lg.ATLAS_XY * lg.ATLAS_XY + entry[1] * lg.ATLAS_XY + entry[0])
                    local = (c - tile * lg.TILE).astype(int)
                    raw = self.grid.cells[slot, lg.cell_index(*local)]
                    fields = lg.decode_cells(raw[None])
                    tri = np.prod([f[k] if b else 1.0 - f[k] for k, b in enumerate((dx, dy, dz))])
                    vis = float(lg.corner_validity(fields['q'], fields['w'], (p - c)[None])[0])
                    w = max(2.0 ** -18, tri * vis)
                    A, B, C, d = (fields[k][0].astype(float) for k in ('A', 'B', 'C', 'd'))
                    hl = 0.5 + 0.5 * float(d @ normal)
                    acc += w * (B + hl * (A - B) + 0.5 * sky_scale * (1.0 + normal[1]) * C)
                    total += w
        return acc / total

    def test_matches_the_decode_formula(self):
        points = np.column_stack([self.rng.uniform(1, 31, 40), self.rng.uniform(1, 15, 40), self.rng.uniform(1, 15, 40)])
        points[:3] = [(15.9, 7.5, 7.5), (16.1, 7.5, 7.5), (0.2, 0.2, 0.2)]
        normals = self.rng.normal(size=(40, 3))
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        got = pv.ambient(self.grid, points, normals, 1.75, np.zeros(3))
        for i in range(40):
            np.testing.assert_allclose(got[i], self.decode_formula(points[i], normals[i], 1.75), rtol=2e-5, atol=1e-6)

    def test_no_coverage_fade_and_fallback_outside(self):
        point, normal = [(8.3, 8.6, 8.1)], [(0.0, 1.0, 0.0)]
        light, cover = self.grid.sample(point, normal, 1.75)
        np.testing.assert_allclose(pv.ambient(self.grid, point, normal, 1.75, (0, 0, 0))[0], light[0], rtol=1e-5)
        np.testing.assert_allclose(pv.ambient(self.grid, [(500.0, 500.0, 500.0)], normal, 1.75, (3, 2, 1))[0], (3, 2, 1))


class ShaderTests(unittest.TestCase):
    def test_window_glass_reads_the_probe_volume(self):
        self.assertIn('windowProbeVolume(vTintWorldPos, viewToNative * Ns, viewToNative * Rs, sky, ambientLight(Ns), ambient)',
                      shading.GLASS_FRAGMENT)
        for name in ('uProbeOn', 'uProbeSel', 'uProbeAtlas', 'uProbeAtlasGrid', 'uGridTable', 'uGridDirQ', 'uGridInvSize',
                     'uGridOn', 'uGridColour', 'uGridParams'):
            self.assertIn(name, shading.GLASS_UNIFORMS)
        # The sky term of Q keeps the cube-255 stand-in, not the probe reflection.
        self.assertIn('(1.0 + uE.w * sky)', shading.GLASS_FRAGMENT)
        self.assertEqual(shading.GLASS_FRAGMENT.count('windowProbeVolume('), 2)
        self.assertIn('ambAcc += w * (B + (0.5 + 0.5 * dot(d, N)) * (A - B) + 0.5 * uGridParams.x * (1.0 + N.y) * C)',
                      shading.GLASS_FRAGMENT)
        self.assertIn('amb = ambAcc / total * uGridParams.y', shading.GLASS_FRAGMENT)

    def test_window_glass_sun_visibility_only_in_the_level_glass_program(self):
        branch = shading.GLASS_FRAGMENT.split('uKind == 14')[1].split('uKind == 9')[0]
        self.assertIn('float sun = sceneShadow(vPos, N)', branch)
        self.assertIn('sun * max(dot(Ns, L1), 0.0) * C1', branch)
        self.assertIn('sun * ggxGlass(Ns, V, L1, F0, F90, a) * C1', branch)
        # The level glass program gets the scene helpers, not the lightgrid ambient path of every other glass kind.
        self.assertIn('#if defined(RC_SCENE_EFFECTS) || defined(RC_SCENE_SHADOW)', shading.GLASS_FRAGMENT)
        self.assertNotIn('defined(RC_SCENE_SHADOW)', shading.GLASS_FRAGMENT.split('vec3 ambientLight(vec3 N)')[1].split('}')[0])


@unittest.skipUnless(os.environ.get('RC2016_RESEARCH'), 'needs the game archive (RC2016_RESEARCH)')
class NovalisArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rc2016.levels.scene import open_archive
        try:
            cls.archive = open_archive(Path(os.environ['RC2016_RESEARCH']))
            cls.grid = lg.level_lightgrid(cls.archive, [NOVALIS_ZONE])
        except (OSError, ValueError):
            raise unittest.SkipTest('Source archive not available')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def test_selectors_and_probe_lists(self):
        volume = pv.build_selectors(self.grid)
        self.assertEqual(len(volume.paths), 153)
        self.assertEqual(volume.unlisted, 0)
        self.assertTrue(all('/envprobes/' in p.replace('\\', '/') for p in volume.paths))
        self.assertTrue((self.grid.cells[..., 23] == 255).all())            # no confidence in the archive
        np.testing.assert_array_equal(volume.selectors[..., 1], self.grid.cells[..., 21])
        self.assertEqual(max(len(p) for p in self.grid.probes), 23)

    def test_probe_images(self):
        volume = pv.build_selectors(self.grid)
        blocks = pv.envprobe_blocks(self.archive, volume.paths[0])
        self.assertEqual(blocks.shape, (64, 128, 16))
        faces = pv.envprobe_faces(blocks)
        self.assertTrue(all(f.shape == (128, 128, 3) and np.isfinite(f).all() for f in faces))

    def test_ambient_equals_the_captured_probe_ambient(self):
        # The program's A_probe at the six captured window pixels (interpreter run of event 14519) against the CPU mirror
        # at the game's own position and shaded normal, S = CB0[118] = 1.75 (Novalis' sky-term scale).
        terms = ROOT / 'Research' / '2026-10-29_novalis-window-glass' / 'interp' / 's6_game_terms.json'
        if not terms.is_file():
            self.skipTest('saved interpreter terms not available')
        import json
        game = json.loads(terms.read_text(encoding='utf-8'))
        got = pv.ambient(self.grid, [g['W'] for g in game], [g['Np'] for g in game], 1.75, np.zeros(3))
        np.testing.assert_allclose(got, [g['A_probe'] for g in game], rtol=1e-4)

    def test_probe_faces_equal_the_captured_cube(self):
        capture = ROOT / 'Research' / 'captures' / '2026-10-23_clank-antenna' / 'eid_14519' / 'tex'
        if not capture.is_dir():
            self.skipTest('saved capture not available')
        # Capture slot 106 holds this probe (the runtime selectors map every cell that names it to slot 106); RenderDoc
        # layer = 6 * slot + face.
        path = 'levels\\Novalis\\openArea\\OpenWorld\\default_region\\envprobes\\0xb5a298311eba82ab.texture'
        faces = pv.envprobe_faces(pv.envprobe_blocks(self.archive, path))
        for face in range(6):
            raw = np.frombuffer((capture / f'cube_s{6 * 106 + face}_m0.bin').read_bytes(), '<u4')
            captured = lg.decode_r11g11b10(raw).reshape(128, 128, 3)
            error = np.abs(np.log(np.maximum(captured, 1e-4)) - np.log(np.maximum(faces[face], 1e-4))).mean()
            self.assertLess(error, 0.02, face)
        self.assertTrue(math.isfinite(error))


if __name__ == '__main__':
    unittest.main()
