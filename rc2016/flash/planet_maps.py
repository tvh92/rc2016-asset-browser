"""Export the pause-menu planet maps in their in-game colours, without the menu background.

Sources (all from the game archive):
* Shapes: `ui/export/pause/map.gfx`, sprite `Map_fla.map_image_asset_holder_6`,
  one labelled frame per map (`kVeldin`, ...). Each frame places a `land`
  sprite whose children are the map areas, instance-named `area<zone>_<n>`.
* Colours: the map's ActionScript constants. Its fields `_visitedColor`,
  `_secretColor`, `_inaccessibleColor`, `_unvisitedColor` hold 0xAFDAFF,
  0xAFFFE4, 0xA87373 and 0xFFFFFF; the field-to-value pairing was matched
  against an in-game screenshot (blue, green, dusty red, faint white).
* Secret areas: `configs/system/system_<planet>_map.config`, whose segments
  give SectionRangeFirst/Last (e.g. kVEL_1_16b) with SectionState kSecret.

Areas are drawn opaque. The game draws them partly transparent over the menu
background and switches visited/unvisited/inaccessible from save progress;
this export shows every area as visited (blue) and secrets in green.
"""
from __future__ import annotations

from pathlib import Path
import re

from rc2016.flash import gfx as G
MAP_FILE = 'ui/export/pause/map.gfx'
HOLDER = 'Map_fla.map_image_asset_holder_6'
VISITED = (0xAF, 0xDA, 0xFF, 255)
SECRET = (0xAF, 0xFF, 0xE4, 255)
INACCESSIBLE = (0xA8, 0x73, 0x73, 255)


def _natural(name):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', name)]


def secret_ranges(config_bytes: bytes) -> list[tuple[str, str]]:
    """(first, last) area instance names of every kSecret segment.

    The config's strings appear per segment as the two range ends, the
    condition, then the state; the two area tokens just before each
    `kSecret` are its range.
    """
    tokens = [t.decode() for t in re.findall(rb'k[A-Za-z0-9_]{3,}', config_bytes)]
    area = re.compile(r'^k[A-Z]{3}_(\d+_\d+[a-z]?(?:_\w+)?)$')
    ranges = []
    for index, token in enumerate(tokens):
        if token != 'kSecret':
            continue
        ends = [m.group(1) for m in (area.match(t) for t in tokens[max(0, index - 4):index]) if m][-2:]
        if ends:
            first, last = ('area' + ends[0], 'area' + ends[-1])
            ranges.append((first, last))
    return ranges


def _expand(ranges, names):
    chosen = set()
    for first, last in ranges:
        lo, hi = sorted((_natural(first), _natural(last)))
        chosen |= {n for n in names if lo <= _natural(n) <= hi}
    return chosen


def planet_maps(library: G.Library, config_loader) -> list[dict]:
    """Per map: label, draw items coloured visited/secret, and the area bookkeeping."""
    movie = library.movie
    holder_id = next(i for i, n in movie.names.items() if n == HOLDER)
    holder = movie.characters[holder_id]
    maps = []
    for label, frame in sorted(holder.labels.items(), key=lambda kv: kv[1]):
        land = next((o for o in G.display_list(holder, frame) if o.get('name') == 'land'), None)
        if land is None:
            continue
        sprite = movie.characters[land['char']]
        areas = [o for o in G.display_list(sprite, 0) if o.get('name')]
        names = [o['name'] for o in areas]
        planet = label[1:].lower()
        config = config_loader(f'configs/system/system_{planet}_map.config')
        ranges = secret_ranges(config) if config else []
        secret = _expand(ranges, names)
        unresolved = [r for r in ranges if not _expand([r], names)]
        items, groups = [], []
        for obj in areas:
            color = SECRET if obj['name'] in secret else VISITED
            tint = ((0.0, 0.0, 0.0, 1.0), (float(color[0]), float(color[1]), float(color[2]), 0.0))
            matrix = G.concat(land['matrix'], obj.get('matrix', G.IDENTITY))
            area_items = []
            G._collect(library, movie.characters.get(obj['char']), matrix, tint, 0, area_items)
            items += area_items
            groups.append(area_items)
        maps.append(dict(label=label, planet=label[1:], items=items, groups=groups, areas=len(names),
                         secret=sorted(secret, key=_natural), secret_ranges=ranges, unresolved=unresolved,
                         config_found=config is not None))
    return maps


def render_with_seams(groups, items, size, seam_px=None, darken=0.7):
    """Render the map and darken the boundaries shared by neighbouring areas.

    In game every area is its own shape, so a thin line shows where two
    areas meet. Here each area's mask is grown by half the line width; pixels
    covered by two or more grown masks (and inside the map) are those shared
    boundaries. Outer outlines stay clean. `seam_px` defaults to about 1/800
    of the long side, as measured on the in-game screenshot.
    """
    import numpy as np
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    image = G.render(items, size=size)
    bounds = G.item_bounds(items)
    if not bounds or not seam_px and seam_px is not None:
        return image
    seam_px = seam_px or max(1, round(size / 800))
    x0, y0, x1, y1 = bounds
    scale = size / max(x1 - x0, y1 - y0, 1.0)
    W, H = image.size
    radius = max(1, (seam_px + 1) // 2)
    count = np.zeros((H, W), dtype=np.uint8)
    for group in groups:
        loops = [[((x - x0) * scale, (y - y0) * scale) for x, y in loop]
                 for item in group if item[0] == 'fill' for loop in item[2] if len(loop) >= 3]
        if not loops:
            continue
        xs = [p[0] for loop in loops for p in loop]; ys = [p[1] for loop in loops for p in loop]
        left, top = max(0, int(min(xs)) - radius - 1), max(0, int(min(ys)) - radius - 1)
        right, bottom = min(W, int(max(xs)) + radius + 2), min(H, int(max(ys)) + radius + 2)
        if right <= left or bottom <= top:
            continue
        mask = Image.new('1', (right - left, bottom - top), 0)
        for loop in loops:   # even-odd, like the renderer
            layer = Image.new('1', mask.size, 0)
            ImageDraw.Draw(layer).polygon([(x - left, y - top) for x, y in loop], fill=1)
            mask = ImageChops.logical_xor(mask, layer)
        grown = mask.convert('L').filter(ImageFilter.MaxFilter(2 * radius + 1))
        count[top:bottom, left:right] += (np.asarray(grown) > 0)
    rgba = np.asarray(image).copy()
    seam = (count >= 2) & (rgba[..., 3] > 0)
    rgba[seam, :3] = (rgba[seam, :3].astype(np.float32) * darken).astype(np.uint8)
    return Image.fromarray(rgba, 'RGBA')


def export(loader, folder, size=4096, progress=None, seams=True) -> list[dict]:
    """Write `<Planet>.png` per map (transparent background) into folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    from rc2016.formats.archive import decompress
    library = loader.gfx_library(MAP_FILE)
    results = []

    def config(path):   # configs are compressed built assets, unlike the raw GFX files
        data = loader._gfx_bytes(path)
        return decompress(data)[0] if data else None

    maps = planet_maps(library, config)
    for number, entry in enumerate(maps, 1):
        image = (render_with_seams(entry['groups'], entry['items'], size) if seams
                 else G.render(entry['items'], size=size))
        target = folder / f"{entry['planet']}.png"
        image.save(target)
        entry = {k: v for k, v in entry.items() if k not in ('items', 'groups')}
        entry.update(file=str(target), width=image.width, height=image.height)
        results.append(entry)
        if progress:
            progress(number, len(maps), target.name)
    return results


if __name__ == '__main__':
    import argparse, json, sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from rc2016.viewer.loader import SourcePreviewLoader
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('source_root', help='extracted game folder (e.g. ...\\CUSA01073)')
    parser.add_argument('output', help='folder for the PNGs')
    parser.add_argument('--size', type=int, default=4096, help='long side in pixels (default 4096)')
    parser.add_argument('--no-seams', action='store_true', help='omit the section lines between neighbouring areas')
    args = parser.parse_args()
    loader = SourcePreviewLoader()
    loader.ui_files(args.source_root)
    rows = export(loader, args.output, args.size, lambda n, t, name: print(f'{n}/{t} {name}', flush=True),
                  seams=not args.no_seams)
    (Path(args.output) / 'planet_maps.json').write_text(json.dumps(dict(
        source=MAP_FILE, symbol=HOLDER, colours=dict(visited='#AFDAFF', secret='#AFFFE4'),
        note='Areas opaque; every area shown as visited, secrets per system_<planet>_map.config.',
        maps=rows), indent=2), encoding='utf8')
