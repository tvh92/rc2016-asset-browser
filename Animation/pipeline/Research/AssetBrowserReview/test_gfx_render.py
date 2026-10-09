import struct
import unittest

from Animation.pipeline.Research.AssetBrowserReview import gfx_render as G


class BitWriter:
    def __init__(self):
        self.bits = []

    def ub(self, n, value):
        self.bits += [(value >> (n - 1 - i)) & 1 for i in range(n)]

    def sb(self, n, value):
        self.ub(n, value & ((1 << n) - 1))

    def align(self):
        while len(self.bits) % 8:
            self.bits.append(0)

    def bytes(self):
        self.align()
        return bytes(int(''.join(map(str, self.bits[i:i + 8])), 2) for i in range(0, len(self.bits), 8))


def rect(x0, x1, y0, y1, n=16):
    w = BitWriter(); w.ub(5, n)
    for v in (x0, x1, y0, y1):
        w.sb(n, v)
    return w.bytes()


def tag(code, body):
    if len(body) < 63:
        return struct.pack('<H', code << 6 | len(body)) + body
    return struct.pack('<HI', code << 6 | 63, len(body)) + body


def square_shape(char_id, size, rgba):
    """DefineShape3: one solid square from (0,0) to (size,size) twips."""
    body = struct.pack('<H', char_id) + rect(0, size, 0, size)
    body += bytes([1, 0x00]) + bytes(rgba) + bytes([0])      # 1 fill style, 0 line styles
    w = BitWriter(); w.ub(4, 1); w.ub(4, 0)                    # fill bits, line bits
    w.ub(1, 0); w.ub(5, 0b00101)                               # style change: fill1 + moveTo
    w.ub(5, 14); w.sb(14, 0); w.sb(14, 0)
    w.ub(1, 1)                                                 # fill1 = style 1
    for dx, dy in ((size, 0), (0, size), (-size, 0), (0, -size)):
        w.ub(1, 1); w.ub(1, 1); w.ub(4, 12)                    # straight edge, 14-bit deltas
        if dx:
            w.ub(1, 0); w.ub(1, 0); w.sb(14, dx)
        else:
            w.ub(1, 0); w.ub(1, 1); w.sb(14, dy)
    w.ub(1, 0); w.ub(5, 0)                                     # end of shape
    return tag(32, body + w.bytes())


def place(depth, char_id, tx, ty, clip_depth=None):
    flags = 0x02 | 0x04 | (0x40 if clip_depth else 0)
    w = BitWriter(); w.ub(1, 0); w.ub(1, 0); w.ub(5, 15); w.sb(15, tx); w.sb(15, ty)
    body = bytes([flags]) + struct.pack('<HH', depth, char_id) + w.bytes()
    if clip_depth:
        body += struct.pack('<H', clip_depth)
    return tag(26, body)


def movie():
    sprite = struct.pack('<HH', 10, 2)
    sprite += place(1, 1, 0, 0) + tag(43, b'first\0') + tag(1, b'')
    sprite += place(2, 1, 4000, 0) + tag(43, b'second\0') + tag(1, b'') + tag(0, b'')
    body = rect(0, 8000, 0, 2000) + struct.pack('<HH', 30 << 8, 1)
    body += square_shape(1, 2000, (255, 0, 0, 255)) + tag(39, sprite)
    body += tag(76, struct.pack('<H', 1) + struct.pack('<H', 10) + b'TwoSquares\0') + tag(0, b'')
    return b'GFX' + bytes([12]) + struct.pack('<I', 8 + len(body)) + body


class GfxRenderTests(unittest.TestCase):
    def setUp(self):
        self.movie = G.parse(movie())
        self.library = G.Library(self.movie)

    def test_parses_shape_sprite_labels_and_names(self):
        self.assertIsInstance(self.movie.characters[1], G.Shape)
        sprite = self.movie.characters[10]
        self.assertEqual((sprite.frame_count, sprite.labels), (2, {'first': 0, 'second': 1}))
        self.assertEqual(self.movie.names[10], 'TwoSquares')
        self.assertEqual(len(G.display_list(sprite, 0)), 1)
        self.assertEqual(len(G.display_list(sprite, 1)), 2)
        self.assertFalse(self.movie.tag_counts.get('errors'))

    def test_renders_frames_with_placement_matrices(self):
        image = G.render(G.collect(self.library, 10, 1), size=300)
        self.assertEqual(image.size, (300, 100))
        self.assertEqual(image.getpixel((40, 50)), (255, 0, 0, 255))    # first square
        self.assertEqual(image.getpixel((150, 50))[3], 0)               # gap between them
        self.assertEqual(image.getpixel((260, 50)), (255, 0, 0, 255))   # second square, frame 2 only
        self.assertEqual(G.render(G.collect(self.library, 10, 0), size=100).getpixel((50, 50)), (255, 0, 0, 255))

    def test_clip_depth_masks_later_depths(self):
        clipped = G.Sprite(20, 1, [[('place', dict(depth=1, char=1, matrix=(0.5, 0, 0, 0.5, 0, 0), clip_depth=2)),
                                    ('place', dict(depth=2, char=1, matrix=G.IDENTITY))]])
        self.movie.characters[20] = clipped
        items = G.collect(self.library, 20, 0)
        image = G.render(items, size=200, bounds=(0, 0, 2000, 2000))
        self.assertEqual(image.getpixel((50, 50))[3], 255)    # inside the half-size mask
        self.assertEqual(image.getpixel((150, 150))[3], 0)    # outside the mask

    def test_symbols_lists_timeline_sprites_and_shapes(self):
        rows = G.symbols(self.movie)
        self.assertEqual([(r['id'], r['kind'], r['name']) for r in rows],
                         [(0, 'timeline', '(main timeline)'), (1, 'shape', ''), (10, 'sprite', 'TwoSquares')])


class MapExportTests(unittest.TestCase):
    def test_secret_ranges_from_config_strings(self):
        from Animation.pipeline.Research.AssetBrowserReview import map_export as M
        # Shape of system_<planet>_map.config strings: range ends, condition, state.
        config = (b'\0kVEL_1_16b\0kVEL_1_16b\0kAlways\0kSecret\0kNone\0kVEL_3_02\0kVEL_3_02\0kAlways\0kSecret\0'
                  b'kQUA_1_escape_from_victor\0kVEL_2_01a\0kVEL_2_07\0kIncomplete\0kHidden\0'
                  b'kMET_4_05c_secret\0kMET_4_05d_secret\0kAlways\0kSecret\0')
        ranges = M.secret_ranges(config)
        self.assertEqual(ranges, [('area1_16b', 'area1_16b'), ('area3_02', 'area3_02'),
                                  ('area4_05c_secret', 'area4_05d_secret')])
        names = ['area1_16a', 'area1_16b', 'area3_02', 'area2_07', 'area4_05c_secret', 'area4_05d_secret', 'area4_05e']
        self.assertEqual(M._expand(ranges, names),
                         {'area1_16b', 'area3_02', 'area4_05c_secret', 'area4_05d_secret'})


if __name__ == '__main__':
    unittest.main()
