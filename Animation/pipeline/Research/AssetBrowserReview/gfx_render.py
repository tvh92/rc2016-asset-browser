"""Basic Scaleform GFX / Flash SWF vector viewer for the game's UI files.

The game's menus (`ui/export/**/*.gfx`, e.g. the pause-menu map in
`ui/export/pause/map.gfx`) are Scaleform GFX: SWF version 12 with a `GFX`
signature and a few extra tags. This module parses the parts needed to draw
their vector art and rasterizes it with Pillow (+ numpy for gradients):

* DefineShape 1-4: solid, linear and radial gradient fills; line styles.
  Bitmap fills draw as neutral grey (these files carry no bitmaps).
* DefineSprite timelines with PlaceObject2/3 and RemoveObject, evaluated at
  any frame; FrameLabel names; matrices, colour transforms and clip masks.
* SymbolClass / ExportAssets names, and ImportAssets2 resolved through a
  caller-supplied loader (other `.gfx` files in the same archive).

Not drawn: text fields and fonts, filters, blend modes, morph shapes,
ActionScript. It is a viewing aid; it makes no claim to match the game.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct
import zlib

TWIPS = 20.0


class Bits:
    """Big-endian bit reader over a byte buffer (SWF's UB/SB/FB fields)."""

    def __init__(self, data, pos=0):
        self.data, self.pos, self.bit = data, pos, 0

    def align(self):
        if self.bit:
            self.pos += 1
            self.bit = 0

    def ub(self, n):
        value = 0
        for _ in range(n):
            if self.pos >= len(self.data):
                raise ValueError('Truncated bit field')
            value = (value << 1) | ((self.data[self.pos] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit = 0
                self.pos += 1
        return value

    def sb(self, n):
        value = self.ub(n)
        return value - (1 << n) if n and value & (1 << (n - 1)) else value

    def fb(self, n):
        return self.sb(n) / 65536.0

    def u8(self):
        self.align()
        self.pos += 1
        return self.data[self.pos - 1]

    def u16(self):
        self.align()
        self.pos += 2
        return struct.unpack_from('<H', self.data, self.pos - 2)[0]

    def s16(self):
        self.align()
        self.pos += 2
        return struct.unpack_from('<h', self.data, self.pos - 2)[0]

    def u32(self):
        self.align()
        self.pos += 4
        return struct.unpack_from('<I', self.data, self.pos - 4)[0]

    def string(self):
        self.align()
        end = self.data.index(b'\0', self.pos)
        text = self.data[self.pos:end].decode('utf-8', 'replace')
        self.pos = end + 1
        return text

    def rect(self):
        self.align()
        n = self.ub(5)
        values = [self.sb(n) for _ in range(4)]
        self.align()
        return values   # xmin, xmax, ymin, ymax in twips

    def matrix(self):
        self.align()
        a = d = 1.0
        b = c = 0.0
        if self.ub(1):
            n = self.ub(5)
            a, d = self.fb(n), self.fb(n)
        if self.ub(1):
            n = self.ub(5)
            b, c = self.fb(n), self.fb(n)
        n = self.ub(5)
        tx, ty = self.sb(n), self.sb(n)
        self.align()
        # x' = a*x + c*y + tx ; y' = b*x + d*y + ty
        return (a, b, c, d, float(tx), float(ty))

    def cxform(self, alpha):
        self.align()
        has_add, has_mult = self.ub(1), self.ub(1)
        n = self.ub(4)
        count = 4 if alpha else 3
        mult = [self.sb(n) / 256.0 for _ in range(count)] if has_mult else [1.0] * count
        add = [float(self.sb(n)) for _ in range(count)] if has_add else [0.0] * count
        self.align()
        if not alpha:
            mult.append(1.0)
            add.append(0.0)
        return (tuple(mult), tuple(add))


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
NO_TINT = ((1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0))


def concat(outer, inner):
    """Matrix applying `inner` first, then `outer`."""
    a1, b1, c1, d1, x1, y1 = outer
    a2, b2, c2, d2, x2, y2 = inner
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * x2 + c1 * y2 + x1, b1 * x2 + d1 * y2 + y1)


def apply(m, x, y):
    a, b, c, d, tx, ty = m
    return a * x + c * y + tx, b * x + d * y + ty


def tint_concat(outer, inner):
    (om, oa), (im, ia) = outer, inner
    return (tuple(o * i for o, i in zip(om, im)), tuple(o * a + b for o, a, b in zip(om, ia, oa)))


def tint_color(tint, rgba):
    mult, add = tint
    return tuple(max(0, min(255, int(round(v * m + a)))) for v, m, a in zip(rgba, mult, add))


# Characters ---------------------------------------------------------------

@dataclass
class Shape:
    id: int
    bounds: list
    fills: list            # [(fill style dict, [closed loop of points])]
    lines: list            # [(line style dict, [polyline points])]
    kind: str = 'shape'


@dataclass
class Sprite:
    id: int
    frame_count: int
    frames: list           # per frame: list of control tags (code, dict)
    labels: dict = field(default_factory=dict)   # label -> frame index (0-based)
    kind: str = 'sprite'


@dataclass
class Movie:
    version: int
    signature: str
    bounds: list
    frame_rate: float
    characters: dict       # id -> Shape | Sprite | ('text', bounds) | ('unsupported', tag)
    root: Sprite
    names: dict            # id -> exported / class name
    imports: dict          # local id -> (url, exported name)
    tag_counts: dict


def _fill_style(r, shape_tag):
    kind = r.u8()
    style = dict(type=kind)
    if kind == 0x00:
        style['color'] = _color(r, shape_tag >= 3)
    elif kind in (0x10, 0x12, 0x13):
        style['matrix'] = r.matrix()
        r.align()
        r.ub(2)   # spread mode
        r.ub(2)   # interpolation mode
        count = r.ub(4)
        style['stops'] = [(r.u8(), _color(r, shape_tag >= 3)) for _ in range(count)]
        if kind == 0x13:
            style['focal'] = r.s16() / 256.0
    elif 0x40 <= kind <= 0x43:
        style['bitmap'] = r.u16()
        style['matrix'] = r.matrix()
    else:
        raise ValueError(f'Unknown fill style type 0x{kind:02X}')
    return style


def _color(r, alpha):
    r.align()
    rgb = (r.u8(), r.u8(), r.u8())
    return rgb + ((r.u8(),) if alpha else (255,))


def _styles(r, shape_tag):
    count = r.u8()
    if count == 0xFF and shape_tag >= 2:
        count = r.u16()
    fills = [_fill_style(r, shape_tag) for _ in range(count)]
    count = r.u8()
    if count == 0xFF and shape_tag >= 2:
        count = r.u16()
    lines = []
    for _ in range(count):
        width = r.u16()
        if shape_tag == 4:
            r.align()
            r.ub(2)
            join = r.ub(2)
            has_fill = r.ub(1)
            r.ub(3)
            r.ub(5)
            r.ub(1)
            r.ub(2)
            if join == 2:
                r.u16()
            if has_fill:
                fill = _fill_style(r, shape_tag)
                color = fill.get('color') or (fill.get('stops') or [(0, (128, 128, 128, 255))])[0][1]
            else:
                color = _color(r, True)
        else:
            color = _color(r, shape_tag >= 3)
        lines.append(dict(width=width, color=color))
    return fills, lines


def _curve_points(p0, c, p1, twips_tolerance=10.0):
    """Quadratic Bezier flattened to points (excluding p0)."""
    length = math.dist(p0, c) + math.dist(c, p1)
    steps = max(2, min(64, int(length / (twips_tolerance * 4)) + 2))
    points = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        points.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0],
                       u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]))
    return points


def _parse_shape(data, code):
    shape_tag = {2: 1, 22: 2, 32: 3, 83: 4}[code]
    r = Bits(data)
    char_id = r.u16()
    bounds = r.rect()
    if shape_tag == 4:
        r.rect()          # edge bounds
        r.u8()            # reserved / winding / stroke hint flags
    fill_styles, line_styles = _styles(r, shape_tag)
    r.align()
    fill_bits, line_bits = r.ub(4), r.ub(4)
    # Edges grouped by style; each edge keeps its flattened point run.
    fill_edges, line_runs = {}, []
    # Style indices are local to the current style arrays; new arrays get fresh keys.
    group = 0
    x = y = 0.0
    fill0 = fill1 = line = 0
    current_line = None
    styles_by_group = {0: (fill_styles, line_styles)}
    while True:
        if r.ub(1) == 0:
            flags = r.ub(5)
            if flags == 0:
                break
            if flags & 1:            # move to
                n = r.ub(5)
                x, y = float(r.sb(n)), float(r.sb(n))
                current_line = None
            if flags & 2:
                fill0 = r.ub(fill_bits)
            if flags & 4:
                fill1 = r.ub(fill_bits)
            if flags & 8:
                line = r.ub(line_bits)
                current_line = None
            if flags & 16:           # new styles
                fill_styles, line_styles = _styles(r, shape_tag)
                group += 1
                styles_by_group[group] = (fill_styles, line_styles)
                r.align()
                fill_bits, line_bits = r.ub(4), r.ub(4)
                fill0 = fill1 = line = 0
                current_line = None
            continue
        straight = r.ub(1)
        n = r.ub(4) + 2
        start = (x, y)
        if straight:
            if r.ub(1):
                dx, dy = r.sb(n), r.sb(n)
            elif r.ub(1):
                dx, dy = 0, r.sb(n)
            else:
                dx, dy = r.sb(n), 0
            x, y = x + dx, y + dy
            points = [(x, y)]
        else:
            cx, cy = x + r.sb(n), y + r.sb(n)
            x, y = cx + r.sb(n), cy + r.sb(n)
            points = _curve_points(start, (cx, cy), (x, y))
        if fill1:
            fill_edges.setdefault((group, fill1), []).append([start] + points)
        if fill0:   # fill0 lies to the left: walk the edge backwards
            fill_edges.setdefault((group, fill0), []).append(list(reversed([start] + points)))
        if line:
            if current_line is None or current_line[1][-1] != start:
                current_line = ((group, line), [start])
                line_runs.append(current_line)
            current_line[1].extend(points)
    fills = []
    for (g, index), edges in fill_edges.items():
        styles = styles_by_group[g][0]
        if 0 < index <= len(styles):
            fills.append((styles[index - 1], _join_loops(edges)))
    lines = []
    for (g, index), points in line_runs:
        styles = styles_by_group[g][1]
        if 0 < index <= len(styles):
            lines.append((styles[index - 1], points))
    return Shape(char_id, bounds, fills, lines)


def _join_loops(edges):
    """Chain directed edges (point runs) into closed loops by matching endpoints."""
    key = lambda p: (round(p[0]), round(p[1]))
    starts = {}
    for i, edge in enumerate(edges):
        starts.setdefault(key(edge[0]), []).append(i)
    used = [False] * len(edges)
    loops = []
    for i, edge in enumerate(edges):
        if used[i]:
            continue
        used[i] = True
        loop = list(edge)
        origin = key(edge[0])
        while key(loop[-1]) != origin:
            candidates = [j for j in starts.get(key(loop[-1]), []) if not used[j]]
            if not candidates:
                break           # open path; closing it straight is the best guess
            j = candidates[0]
            used[j] = True
            loop.extend(edges[j][1:])
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def _parse_place(data, code):
    r = Bits(data)
    flags = r.u8()
    flags2 = r.u8() if code == 70 else 0
    place = dict(depth=r.u16(), move=bool(flags & 1))
    if code == 70 and (flags2 & 0x08 or (flags2 & 0x10 and flags & 0x02)):
        place['class_name'] = r.string()
    if flags & 0x02:
        place['char'] = r.u16()
    if flags & 0x04:
        place['matrix'] = r.matrix()
    if flags & 0x08:
        place['tint'] = r.cxform(True)
    if flags & 0x10:
        place['ratio'] = r.u16()
    if flags & 0x20:
        place['name'] = r.string()
    if flags & 0x40:
        place['clip_depth'] = r.u16()
    if code == 70 and flags2 & 0x20:
        # Visibility lives after filters/blend mode; read them only to reach it.
        pass
    return place


def _parse_tags(data, pos, end, movie_chars, names, imports, counts, owner_frames, labels):
    frame = []
    while pos + 2 <= end:
        header = struct.unpack_from('<H', data, pos)[0]
        pos += 2
        code, length = header >> 6, header & 0x3F
        if length == 0x3F:
            length = struct.unpack_from('<I', data, pos)[0]
            pos += 4
        body = data[pos:pos + length]
        pos += length
        counts[code] = counts.get(code, 0) + 1
        if code == 0:
            break
        try:
            if code in (2, 22, 32, 83):
                shape = _parse_shape(body, code)
                movie_chars[shape.id] = shape
            elif code == 39:
                sprite_id, frame_count = struct.unpack_from('<HH', body, 0)
                frames, sprite_labels = [], {}
                _parse_tags(body, 4, len(body), movie_chars, names, imports, counts, frames, sprite_labels)
                movie_chars[sprite_id] = Sprite(sprite_id, frame_count, frames, sprite_labels)
            elif code in (26, 70):
                frame.append(('place', _parse_place(body, code)))
            elif code == 4:
                char_id, depth = struct.unpack_from('<HH', body, 0)
                frame.append(('place', dict(depth=depth, char=char_id, move=False)))
            elif code in (5, 28):
                depth = struct.unpack_from('<H', body, 2 if code == 5 else 0)[0]
                frame.append(('remove', dict(depth=depth)))
            elif code == 1:
                owner_frames.append(frame)
                frame = []
            elif code == 43:
                labels[Bits(body).string()] = len(owner_frames)
            elif code in (56, 76):
                r = Bits(body)
                for _ in range(r.u16()):
                    char_id = r.u16()
                    names.setdefault(char_id, r.string())
            elif code in (57, 71):
                r = Bits(body)
                url = r.string()
                if code == 71:
                    r.u8()
                    r.u8()
                for _ in range(r.u16()):
                    char_id = r.u16()
                    imports[char_id] = (url, r.string())
            elif code in (37, 11, 33):
                char_id = struct.unpack_from('<H', body, 0)[0]
                movie_chars[char_id] = ('text', Bits(body, 2).rect())
            elif code in (46, 84):
                char_id = struct.unpack_from('<H', body, 0)[0]
                movie_chars[char_id] = ('unsupported', code)
        except (ValueError, IndexError, struct.error) as error:
            counts.setdefault('errors', []).append(f'tag {code}: {error}')
    if frame:
        owner_frames.append(frame)
    return pos


def parse(data: bytes) -> Movie:
    signature = data[:3].decode('latin-1')
    if signature in ('CFX', 'CWS'):
        data = data[:8] + zlib.decompress(data[8:])
    elif signature not in ('GFX', 'FWS'):
        raise ValueError(f'Not a GFX/SWF file (signature {signature!r})')
    version = data[3]
    r = Bits(data, 8)
    bounds = r.rect()
    frame_rate = r.u16() / 256.0
    frame_count = r.u16()
    characters, names, imports, counts, frames, labels = {}, {}, {}, {}, [], {}
    _parse_tags(data, r.pos, len(data), characters, names, imports, counts, frames, labels)
    root = Sprite(0, frame_count, frames, labels)
    return Movie(version, signature, bounds, frame_rate, characters, root, names, imports, counts)


# Display list ---------------------------------------------------------------

def display_list(sprite: Sprite, frame: int) -> list:
    """Objects on stage at `frame` (0-based), sorted by depth."""
    stage = {}
    for index in range(min(frame + 1, len(sprite.frames))):
        for kind, tag in sprite.frames[index]:
            if kind == 'remove':
                stage.pop(tag['depth'], None)
                continue
            depth = tag['depth']
            if tag.get('move') and depth in stage:
                entry = dict(stage[depth])
                if 'char' in tag:
                    entry['char'] = tag['char']
                    entry['placed_frame'] = index
            elif 'char' in tag:
                entry = dict(char=tag['char'], matrix=IDENTITY, tint=NO_TINT, placed_frame=index)
            else:
                continue
            for key in ('matrix', 'tint', 'clip_depth', 'name', 'ratio'):
                if key in tag:
                    entry[key] = tag[key]
            entry['depth'] = depth
            stage[depth] = entry
    return [stage[d] for d in sorted(stage)]


class Library:
    """A parsed movie plus lazily resolved imports from sibling files."""

    def __init__(self, movie: Movie, loader=None, _seen=None):
        self.movie, self.loader = movie, loader
        self._imports = {}
        self._seen = _seen if _seen is not None else set()

    def resolve(self, char_id):
        """(Library, character) for an id, following ImportAssets."""
        if char_id in self.movie.characters:
            return self, self.movie.characters[char_id]
        if char_id in self.movie.imports and self.loader:
            url, name = self.movie.imports[char_id]
            if url not in self._imports:
                library = None
                if url not in self._seen:
                    self._seen.add(url)
                    try:
                        data = self.loader(url)
                        library = Library(parse(data), self.loader, self._seen) if data else None
                    except (ValueError, OSError):
                        library = None
                self._imports[url] = library
            library = self._imports[url]
            if library:
                target = next((i for i, n in library.movie.names.items() if n == name), None)
                if target is not None:
                    return library.resolve(target)
        return self, None


# Rendering --------------------------------------------------------------------

def _collect(library, char, matrix, tint, frame, out, depth_limit=24):
    """Flatten a character into draw items: ('fill'|'line'|'clip_start'|'clip_end', ...).

    `frame` applies to this character's own timeline; nested sprites are shown
    at their first frame (the game usually picks their frame from ActionScript).
    """
    if depth_limit <= 0 or char is None:
        return
    if isinstance(char, Shape):
        for style, loops in char.fills:
            out.append(('fill', style, [[apply(matrix, *p) for p in loop] for loop in loops], matrix, tint))
        for style, points in char.lines:
            scale = math.sqrt(abs(matrix[0] * matrix[3] - matrix[1] * matrix[2])) or 1.0
            out.append(('line', style, [apply(matrix, *p) for p in points], style['width'] * scale, tint))
        return
    if isinstance(char, Sprite):
        objects = display_list(char, frame)
        open_clips = []
        for obj in objects:
            while open_clips and obj['depth'] > open_clips[-1]:
                out.append(('clip_end',))
                open_clips.pop()
            lib, child = library.resolve(obj['char'])
            m = concat(matrix, obj.get('matrix', IDENTITY))
            t = tint_concat(tint, obj.get('tint', NO_TINT))
            if obj.get('clip_depth'):
                mask = []
                _collect(lib, child, m, t, 0, mask, depth_limit - 1)
                out.append(('clip_start', mask))
                open_clips.append(obj['clip_depth'])
                continue
            _collect(lib, child, m, t, 0, out, depth_limit - 1)
        for _ in open_clips:
            out.append(('clip_end',))


def collect(library: Library, char_id: int, frame: int = 0):
    """Draw items for a character id (0 = root timeline) at `frame`."""
    char = library.movie.root if char_id == 0 else library.resolve(char_id)[1]
    items = []
    _collect(library, char, IDENTITY, NO_TINT, frame, items)
    return items


def item_bounds(items):
    xs, ys = [], []
    for item in items:
        if item[0] == 'fill':
            for loop in item[2]:
                xs.extend(p[0] for p in loop)
                ys.extend(p[1] for p in loop)
        elif item[0] == 'line':
            xs.extend(p[0] for p in item[2])
            ys.extend(p[1] for p in item[2])
        elif item[0] == 'clip_start':
            pass   # masks never enlarge the visible area
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def render(items, size=1024, background=None, supersample=2, bounds=None):
    """Rasterize draw items to an RGBA Pillow image whose long side is `size` px."""
    from PIL import Image, ImageChops, ImageDraw
    bounds = bounds or item_bounds(items)
    if not bounds:
        return Image.new('RGBA', (size, size), background or (0, 0, 0, 0))
    x0, y0, x1, y1 = bounds
    width_t, height_t = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    scale = size / max(width_t, height_t)
    out_w, out_h = max(1, int(round(width_t * scale))), max(1, int(round(height_t * scale)))
    ss = max(1, supersample)
    W, H = out_w * ss, out_h * ss
    k = scale * ss
    to_px = lambda p: ((p[0] - x0) * k, (p[1] - y0) * k)

    def coverage(loops):
        mask = Image.new('1', (W, H), 0)
        for loop in loops:
            if len(loop) < 3:
                continue
            layer = Image.new('1', (W, H), 0)
            ImageDraw.Draw(layer).polygon([to_px(p) for p in loop], fill=1)
            mask = ImageChops.logical_xor(mask, layer)   # even-odd rule
        return mask.convert('L')

    def paint(item, canvas):
        kind = item[0]
        if kind == 'fill':
            _, style, loops, matrix, tint = item
            alpha = coverage(loops)
            if not alpha.getbbox():
                return
            layer = _fill_image(style, matrix, tint, W, H, x0, y0, k)
            layer.putalpha(ImageChops.multiply(layer.getchannel('A'), alpha))
            canvas.alpha_composite(layer)
        elif kind == 'line':
            _, style, points, width, tint = item
            if len(points) < 2:
                return
            color = tint_color(tint, style['color'])
            layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(layer).line([to_px(p) for p in points], fill=color,
                                       width=max(1, int(round(width * k))), joint='curve')
            canvas.alpha_composite(layer)

    canvas = Image.new('RGBA', (W, H), background or (0, 0, 0, 0))
    stack = []    # (saved canvas, mask) for clip groups
    for item in items:
        if item[0] == 'clip_start':
            mask_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            for mask_item in item[1]:
                if mask_item[0] == 'fill':
                    mask_layer.putalpha(ImageChops.lighter(mask_layer.getchannel('A'), coverage(mask_item[2])))
            stack.append((canvas, mask_layer.getchannel('A')))
            canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        elif item[0] == 'clip_end':
            if stack:
                outer, mask = stack.pop()
                canvas.putalpha(ImageChops.multiply(canvas.getchannel('A'), mask))
                outer.alpha_composite(canvas)
                canvas = outer
        else:
            paint(item, canvas)
    while stack:
        outer, mask = stack.pop()
        canvas.putalpha(ImageChops.multiply(canvas.getchannel('A'), mask))
        outer.alpha_composite(canvas)
        canvas = outer
    if ss > 1:
        canvas = canvas.resize((out_w, out_h), Image.Resampling.LANCZOS)
    return canvas


def _fill_image(style, matrix, tint, W, H, x0, y0, k):
    from PIL import Image
    kind = style['type']
    if kind == 0x00:
        return Image.new('RGBA', (W, H), tint_color(tint, style['color']))
    if kind in (0x10, 0x12, 0x13):
        import numpy as np
        stops = [(ratio, tint_color(tint, color)) for ratio, color in style['stops']] or [(0, (128, 128, 128, 255))]
        # Gradient space is the square -16384..16384 twips, mapped by the
        # style matrix, then by the object's matrix.
        m = concat(matrix, style['matrix'])
        a, b, c, d, tx, ty = m
        det = a * d - b * c
        if abs(det) < 1e-12:
            return Image.new('RGBA', (W, H), stops[-1][1])
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        px = xs / k + x0 - tx
        py = ys / k + y0 - ty
        gx = (d * px - c * py) / det
        gy = (-b * px + a * py) / det
        if kind == 0x10:
            t = (gx + 16384.0) / 32768.0
        else:
            t = np.sqrt(gx * gx + gy * gy) / 16384.0
        t = np.clip(t * 255.0, 0, 255)
        ratios = np.array([s[0] for s in stops], dtype=np.float32)
        colors = np.array([s[1] for s in stops], dtype=np.float32)
        rgba = np.stack([np.interp(t, ratios, colors[:, ch]) for ch in range(4)], axis=-1)
        return Image.fromarray(rgba.astype(np.uint8), 'RGBA')
    return Image.new('RGBA', (W, H), tint_color(tint, (128, 128, 128, 255)))   # bitmap fill placeholder


def symbols(movie: Movie) -> list[dict]:
    """Rows for a symbol list: root timeline, sprites and shapes with names/frames."""
    rows = [dict(id=0, kind='timeline', name='(main timeline)', frames=movie.root.frame_count,
                 labels=movie.root.labels)]
    for char_id in sorted(movie.characters):
        char = movie.characters[char_id]
        if isinstance(char, Sprite):
            rows.append(dict(id=char_id, kind='sprite', name=movie.names.get(char_id, ''),
                             frames=char.frame_count, labels=char.labels))
        elif isinstance(char, Shape):
            rows.append(dict(id=char_id, kind='shape', name=movie.names.get(char_id, ''), frames=1, labels={}))
    return rows
