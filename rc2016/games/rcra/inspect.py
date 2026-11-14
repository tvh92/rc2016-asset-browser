"""Rift Apart archive evidence: summary, listings, raw extraction and a whole-archive read check.

    python -m rc2016.games.rcra.inspect --game <install> --output-root <folder> --summary
    python -m rc2016.games.rcra.inspect --game <install> --output-root <folder> --list model
    python -m rc2016.games.rcra.inspect --game <install> --output-root <folder> --extract <asset path> ...
    python -m rc2016.games.rcra.inspect --game <install> --output-root <folder> --verify

--verify decompresses every block of every DSAR archive once and checks that every toc row lies
inside its archive's decompressed stream. Results are JSON under the output root; nothing is
written to the game folder.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rc2016.common import disk_asset, dump, write
from rc2016.games.rcra.dsar import MODE_GDEFLATE, MODE_LZ4, BlockReader
from rc2016.games.rcra.source import RcraSource


def summary(source):
    return dict(game='rcra', root=str(source.root), metadata=source.metadata, assets=len(source.entries),
                rows=int(len(source.toc.ids)), archives=len(source.toc.archives), types=source.type_counts(),
                warnings=source.warnings, streamed_textures=int(len(source.toc.texture_ids)))


def verify(source, workers=None):
    """Decompress every block of every installed DSAR archive; check every row's range."""
    reader = BlockReader(source.reader.gdeflate, cache_bytes=0)
    names = sorted({source.toc.archives[i] for i, ok in source.available.items() if ok})
    report = dict(archives={}, failures=[], rows_checked=0, rows_outside=[])
    started = time.perf_counter()

    def check(name):
        archive = reader.archive(source.root / name)
        counts = {MODE_LZ4: 0, MODE_GDEFLATE: 0}
        failures = []
        for index in range(len(archive.blocks)):
            try:
                reader._block(archive, index)
                counts[int(archive.blocks['mode'][index])] += 1
            except Exception as error:   # noqa: BLE001 - every failure is reported, none hides another
                failures.append(dict(archive=name, block=index, error=str(error)))
        return name, dict(compressed=archive.compressed, blocks=len(archive.blocks), size=archive.size,
                          lz4_blocks=counts[MODE_LZ4], gdeflate_blocks=counts[MODE_GDEFLATE]), failures

    with ThreadPoolExecutor(workers or min(8, os.cpu_count() or 1)) as pool:
        for name, row, failures in pool.map(check, names):
            report['archives'][name] = row
            report['failures'].extend(failures)
            print(f"{name}: {row['blocks']} blocks, {len(failures)} failures", flush=True)
    meta = source.toc.meta
    for row in range(len(meta)):
        index = int(meta['archive'][row])
        name = source.toc.archives[index]
        if not source.available.get(index):
            continue
        report['rows_checked'] += 1
        end = int(meta['offset'][row]) + int(meta['size'][row])
        if end > report['archives'][name]['size']:
            report['rows_outside'].append(dict(row=row, archive=name, end=end))
    report['seconds'] = round(time.perf_counter() - started, 1)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game', type=Path, required=True, help='Rift Apart install folder')
    parser.add_argument('--output-root', type=Path, required=True, help='folder for the JSON reports and extractions')
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--list', metavar='TYPE', help="list one asset type ('model', 'texture', ...)")
    parser.add_argument('--extract', nargs='+', metavar='PATH', help='write the raw asset bytes (and a texture HD stream)')
    parser.add_argument('--verify', action='store_true', help='decompress every archive block and check every toc row')
    args = parser.parse_args(argv)
    game, output = args.game.resolve(), args.output_root.resolve()
    if output == game or game in output.parents:
        parser.error('The output root must be outside the game folder')
    source = RcraSource(game, output)
    try:
        if args.summary:
            dump(output / 'rcra_summary.json', summary(source))
            print(json.dumps(summary(source)['types'], indent=1))
        if args.list:
            rows = [dict(id=k, path=source.paths[source._index(k)]) for k in source.ids_of_type(args.list)]
            dump(output / f'rcra_list_{args.list}.json', rows)
            print(f'{len(rows)} {args.list} assets')
        for path in args.extract or ():
            keys = source.resolve(path)
            if not keys:
                print('not found: ' + path, file=sys.stderr)
                continue
            data, digest = source.bytes(keys[0])
            target = output / 'extracted' / disk_asset(source.entries[keys[0]]['path'])
            write(target, data)
            stream = source.texture_stream(keys[0])
            if stream is not None:
                write(target.with_name(target.name + '.stream'), stream)
            print(f'{target} ({len(data)} bytes, sha256 {digest[:12]})')
        if args.verify:
            report = verify(source)
            dump(output / 'rcra_verify.json', report)
            print(f"{len(report['failures'])} block failures, {len(report['rows_outside'])} rows outside, "
                  f"{report['rows_checked']} rows checked in {report['seconds']} s")
            return 1 if report['failures'] or report['rows_outside'] else 0
    finally:
        source.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
