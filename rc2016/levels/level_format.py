"""Read an RC PS4 `.level` asset and decode the model instances of every zone it lists (spike).

`.level` layout ("Level Built File", DAT1; strings by absolute file offset), from Novalis:

    0x2BA33702  u32[n]  zone asset paths (the level's zone list; `_imposter` twins are not listed)
    0x4130D903  u32[n]  region asset paths (Global, OpenWorld, default_region, quest overlays)
    0x2236C47A  u32[n]  checkpoint / spawn names
    0x3395AEC1          spawn records with world positions (not decoded)

Zones carry no transform of their own: instance matrices are already in world space.

Usage (from the project root, with any Python 3):
    python rc2016/levels/level_format.py <level asset path> <out dir>
        [--archive-root <asset_archive dir>]

Writes <out>/<zone stem>_instances.json per zone with a model-instance table,
<out>/models.json (union of model paths, first spelling kept) and <out>/level.json.
"""
import json
import struct
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rc2016.formats.archive import Archive  # noqa: E402
from rc2016.common import read  # noqa: E402
from rc2016.levels.zone_format import INSTANCE_OFFSETS, MODEL_PATHS, decode_decals, decode_zone, sections  # noqa: E402

ZONE_LIST = 0x2BA33702
REGION_LIST = 0x4130D903
SPAWN_NAMES = 0x2236C47A


def string_list(data, table, tag):
    if tag not in table:
        return []
    offset, size = table[tag]
    return [data[o:data.index(b'\0', o)].decode('ascii')
            for o in struct.unpack_from(f'<{size // 4}I', data, offset)]


def decode_level(data):
    table = sections(data)
    if ZONE_LIST not in table:
        raise ValueError('Level lacks the zone list section 0x2BA33702')
    return dict(zones=string_list(data, table, ZONE_LIST), regions=string_list(data, table, REGION_LIST),
                spawn_names=string_list(data, table, SPAWN_NAMES))


def main():
    argv = sys.argv[1:]
    if '--archive-root' not in argv:
        raise SystemExit('usage: python -m rc2016.levels.level_format --archive-root <asset_archive folder> <level path> <output folder>')
    i = argv.index('--archive-root'); archive_root = argv[i + 1]; del argv[i:i + 2]
    level_path, out = argv[0], Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        archive = Archive(archive_root, Path(scratch), lambda *a, **k: None)

        def load(path):
            ids = archive.resolve(path)
            if len(ids) != 1:
                raise ValueError(f'{path}: expected one archive entry, found {len(ids)}')
            return read(archive.decode(ids[0]))

        level = decode_level(load(level_path))
        models, seen, rows = [], set(), []
        for zone in level['zones']:
            data = load(zone)
            stem = Path(zone).stem
            if not {MODEL_PATHS, INSTANCE_OFFSETS} <= set(sections(data)):
                rows.append(dict(zone=zone, instances=0, models=0)); continue
            decoded = decode_zone(data)
            decoded.update(source=Path(zone).name, source_path=zone)
            if archive.resolve(zone + '_static'):   # decal meshes live in the sibling .zone_static
                decoded['decals'] = decode_decals(data, load(zone + '_static'))
            (out / f'{stem}_instances.json').write_text(json.dumps(decoded, indent=1), encoding='utf-8')
            for model in decoded['models']:
                if model.lower() not in seen:
                    seen.add(model.lower()); models.append(model)
            rows.append(dict(zone=zone, instances=len(decoded['instances']), models=len(decoded['models']),
                             decals=len(decoded.get('decals', [])), instances_json=f'{stem}_instances.json'))
    level.update(source=level_path, zone_summary=rows, unique_models=len(models),
                 total_instances=sum(r['instances'] for r in rows))
    (out / 'models.json').write_text(json.dumps(models, indent=1), encoding='utf-8')
    (out / 'level.json').write_text(json.dumps(level, indent=1), encoding='utf-8')
    for r in rows:
        print(f"{r['instances']:6d} instances {r['models']:4d} models {r.get('decals', 0):4d} decals  {r['zone']}")
    print(f"{level_path}: {len(level['zones'])} zones, {level['total_instances']} instances, {len(models)} unique models")


if __name__ == '__main__':
    main()
