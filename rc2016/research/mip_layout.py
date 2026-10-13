"""Read-only check: do built/stream texture payloads hold lower mips, and in which layout?

The viewer decodes only the top mip. Before uploading stored mip chains instead of generating
them, this compares every texture's payload sizes with candidate layouts:

- ``top``: the tile-padded top mip only (8x8-block tiles, as the top-mip decoder assumes);
- ``tiled_chain``: the header's mip count, each level padded to whole 8x8-block tiles;
- ``linear_chain``: the same levels unpadded (whole 4x4 blocks only).

Array textures multiply by their slice count. Nothing is decoded or written; the output is a JSON
summary of matches per payload, format and mip count, plus a few unmatched examples.

    python -m rc2016.research.mip_layout --source-root <Image0 folder> [--limit N]
"""
import argparse
from collections import Counter
import json
import sys

from rc2016.formats.archive import asset_data
from rc2016.formats.texture import parse_header, tiled_top_mip_size, top_mip_size


def chain(width, height, fmt, levels, size):
    total = 0
    for level in range(max(1, levels)):
        total += size(max(1, width >> level), max(1, height >> level), fmt)
    return total


def classify(payload_size, width, height, fmt, levels, slices=1):
    """The candidate layouts that account for exactly ``payload_size`` bytes."""
    if not (payload_size and width and height):
        return ['empty']
    candidates = dict(top=tiled_top_mip_size(width, height, fmt),
                      tiled_chain=chain(width, height, fmt, levels, tiled_top_mip_size),
                      linear_chain=chain(width, height, fmt, levels, top_mip_size))
    matched = [name for name, size in candidates.items() if size * slices == payload_size]
    return matched or ['unmatched']


def survey(archive, limit=None):
    built = Counter(); stream = Counter(); examples = []; failures = Counter(); seen = 0
    for key, entry in archive.entries.items():
        if not entry.get('built_path', '').lower().endswith('.built') or entry.get('type') != 'texture':
            continue
        if limit is not None and seen >= limit:
            break
        seen += 1
        try:
            info = parse_header(asset_data(archive, key))
        except (ValueError, KeyError, OSError) as exc:
            failures[type(exc).__name__] += 1
            continue
        fmt = info['format']
        sd = classify(info['built_size'], info['sd_width'], info['sd_height'], fmt, info['sd_mip_count'],
                      max(1, info['array_size']))
        hd = classify(info['stream_size'], info['hd_width'], info['hd_height'], fmt, info['hd_mip_count'])
        for name in sd:
            built[(name, fmt, info['sd_mip_count'])] += 1
        for name in hd:
            stream[(name, fmt, info['hd_mip_count'])] += 1
        if ('unmatched' in sd or 'unmatched' in hd) and len(examples) < 20:
            examples.append(dict(path=entry['built_path'], format=fmt, sd=(info['sd_width'], info['sd_height'], info['sd_mip_count']),
                                 built_size=info['built_size'], hd=(info['hd_width'], info['hd_height'], info['hd_mip_count']),
                                 stream_size=info['stream_size'], array_size=info['array_size'], sd_match=sd, hd_match=hd))

    def table(counts):
        totals = Counter()
        for (name, _fmt, _levels), n in counts.items():
            totals[name] += n
        rows = [dict(layout=name, format=fmt, mip_count=levels, textures=n)
                for (name, fmt, levels), n in sorted(counts.items(), key=lambda row: -row[1])]
        return dict(totals=dict(totals), by_format_and_mips=rows[:40])
    return dict(textures=seen, header_failures=dict(failures), built=table(built), stream=table(stream),
                unmatched_examples=examples,
                note='"top" and a chain can both match when the header records one mip; '
                     'a payload matching only "top" for multi-mip headers stores no lower mips.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--source-root', required=True, help='the Image0 folder holding the archive')
    parser.add_argument('--limit', type=int, help='stop after this many textures')
    args = parser.parse_args(argv)
    from rc2016.levels.scene import open_archive
    json.dump(survey(open_archive(args.source_root), args.limit), sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
