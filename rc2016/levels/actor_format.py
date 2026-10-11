"""Source-traced actor descriptors and zone placements; no gameplay execution.

The placement control is still open (docs/levels.md). This module deliberately
provides data only: it does not decide that a dependency is a drawable attachment,
apply spawn scripts, or silently select a model from dependency strings.
"""
from __future__ import annotations

import math
import struct

from rc2016.formats.container import game_crc32
from rc2016.levels.zone_format import (
    DATA_HEAP, _material_overrides, override_materials, path_crc64, sections,
)

ACTORS = 0x70682CB8
ACTOR_NAMES = 0xDC625B3D
ACTOR_TYPES = 0x78684035
ZONE_COMPONENTS = 0x50EDC53D
ZONE_COMPONENT_DATA = 0x81999057
PRIMARY_MODEL = 0x32FAC8E0
ACTOR_RECORD = 0x364A6C7C
ACTOR_REFERENCES = 0x3AB204B9
ACTOR_COMPONENTS = 0x135832C8
ACTOR_COMPONENT_DATA = 0x6D4301EF
NO_INDEX = 0xFFFFFFFF
IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)


def _table(data):
    if len(data) < 16 or data[:4] != b'1TAD':
        raise ValueError('Truncated or non-DAT1 actor container')
    count = struct.unpack_from('<I', data, 12)[0]
    end = 16 + 12 * count
    if end > len(data):
        raise ValueError('Truncated DAT1 section table')
    table = sections(data)
    if len(table) != count:
        raise ValueError('Duplicate DAT1 section tag')
    for tag, (off, size) in table.items():
        if off < end or off + size > len(data):
            raise ValueError(f'{tag:#010x}: section outside container')
    return table


def _string(data, table, offset):
    start = 16 + 12 * len(table)
    end = min((off for off, _ in table.values()), default=len(data))
    if not start <= offset < end or (offset > start and data[offset - 1] != 0):
        raise ValueError(f'{offset:#x}: reference is not a string-pool start')
    nul = data.find(b'\0', offset, end)
    if nul < 0:
        raise ValueError('Unterminated actor string')
    value = data[offset:nul].decode('utf-8')
    if value and not value.isprintable():
        raise ValueError('Non-printable actor string')
    return value.replace('\\', '/')


def _chunk(data, table, tag, stride):
    if tag not in table:
        return b''
    off, size = table[tag]
    if size % stride:
        raise ValueError(f'{tag:#010x}: size is not a multiple of {stride}')
    return data[off:off + size]


def _strings(data, table, tag):
    return [_string(data, table, off) for (off,) in
            struct.iter_unpack('<I', _chunk(data, table, tag, 4))]


def _matrix(data, offset):
    values = struct.unpack_from('<16f', data, offset)
    if not all(math.isfinite(x) for x in values):
        raise ValueError('Nonfinite actor matrix')
    if tuple(values[i] for i in (3, 7, 11, 15)) != (0., 0., 0., 1.):
        raise ValueError('Actor matrix w column is not (0,0,0,1)')
    return [list(values[i:i + 4]) for i in range(0, 16, 4)]


def _components(data, table, definitions_tag, data_tag):
    result = []
    for i, (nameoff, name_hash, offset, size) in enumerate(struct.iter_unpack(
            '<4I', _chunk(data, table, definitions_tag, 16))):
        name = _string(data, table, nameoff)
        if game_crc32(name) != name_hash:
            raise ValueError(f'Component {i}: name CRC does not match')
        if bool(offset) != bool(size):
            raise ValueError(f'Component {i}: incomplete payload range')
        if size:
            if data_tag not in table:
                raise ValueError('Component payload section absent')
            lo, length = table[data_tag]
            if not lo <= offset < offset + size <= lo + length:
                raise ValueError(f'Component {i}: payload outside section')
        result.append(dict(name=name, name_hash=f'{name_hash:08X}', section=f'{definitions_tag:08X}',
                           definition_offset=table[definitions_tag][0] + 16 * i,
                           payload_section=f'{data_tag:08X}', offset=offset, size=size))
    return result


def read_component(data, component, *, asset=None):
    """Decode one payload with the existing guarded generic dat1lib reader.

    202300 names the tested wire-layout hypothesis, not this game's version.
    Bounds and unknown types fail loudly. Extra objects and unparsed byte ranges
    are reported explicitly; parsed values are never a claim of complete consumption.
    MaterialConstant values remain raw structured data; no shader constants are inferred.
    """
    if not component['size']:
        return None
    from rc2016.research.source import Asset
    from rc2016.research import cross_reference as xr
    xr.load_library()
    from dat1lib.types.sections import SerializedSection
    base = xr.guarded_serialized(SerializedSection)

    class Traced(base):
        def __init__(self, *args):
            self.object_frames, self.unparsed_ranges = [], []
            super().__init__(*args)

        def _deserialize_object(self, stream):
            pos = stream.tell()
            frame = dict(end=pos + 16)
            self.object_frames.append(frame)
            try:
                value = super()._deserialize_object(stream)
                if frame['end'] < stream.tell():
                    self.unparsed_ranges.append(dict(offset=frame['end'],
                        size=stream.tell() - frame['end'],
                        hex=bytes(stream.getbuffer()[frame['end']:stream.tell()]).hex()))
                return value
            finally:
                self.object_frames.pop()

        def _align(self, stream, alignment):
            super()._align(stream, alignment)
            if self.object_frames:
                self.object_frames[-1]['end'] = stream.tell()

    reader = Traced
    if asset is None:
        asset = Asset('<actor component>', data)
    elif asset.data is not data:
        raise ValueError('Component context belongs to different source bytes')
    offset, size = component['offset'], component['size']
    tag = int(component['payload_section'], 16)
    lo, length = asset.table[tag]
    if not lo <= offset < offset + size <= lo + length:
        raise ValueError('Component read outside payload section')
    parsed = reader(data[offset:offset + size], xr.StringContext(asset, 202300))
    return dict(value=parsed.root, extras=parsed.extras, skipped_bytes=parsed.skipped_bytes,
                unparsed_ranges=[dict(r, offset=offset + r['offset']) for r in parsed.unparsed_ranges])


def decode_actor(data):
    """Actor primary model, local record and structural dependencies/components.

    primary_model is exclusively the u32 string pointer in 0x32FAC8E0.
    Dependencies are not alternative primary models: crates also reference broken
    chunks and components can spawn other actors. An empty primary stays empty.
    """
    table = _table(data)
    if PRIMARY_MODEL not in table or table[PRIMARY_MODEL][1] != 4:
        raise ValueError('Actor lacks a four-byte primary-model section')
    pointer = struct.unpack_from('<I', data, table[PRIMARY_MODEL][0])[0]
    primary = _string(data, table, pointer)
    if primary and not primary.lower().endswith('.model'):
        raise ValueError('Actor primary reference is not a model')
    if ACTOR_RECORD not in table:
        raise ValueError('Actor lacks local record')
    record, size = table[ACTOR_RECORD]
    if size < 0x70:
        raise ValueError('Truncated actor local record')
    rows = _matrix(data, record)
    declared = struct.unpack_from('<I', data, record + 0x44)[0]
    if declared != size and not (not primary and size == 0x98 and declared == 0xA0):
        raise ValueError('Actor local record size does not match section size')
    if primary and (size < 0xB0 or struct.unpack_from('<I', data, record + 0x58)[0] != 0xFFFF):
        raise ValueError('Primary-model actor lacks render-record header')
    dependencies = []
    for i, (off,) in enumerate(struct.iter_unpack('<I', _chunk(data, table, ACTOR_REFERENCES, 4))):
        dependencies.append(dict(path=_string(data, table, off), string_offset=off,
                                 section=f'{ACTOR_REFERENCES:08X}',
                                 reference_offset=table[ACTOR_REFERENCES][0] + i * 4))
    return dict(primary_model=primary or None,
                primary_reference=dict(section=f'{PRIMARY_MODEL:08X}', offset=table[PRIMARY_MODEL][0],
                                       type='u32 absolute string offset', string_offset=pointer),
                matrix_rows=rows, record_offset=record, record_size=size, declared_record_size=declared,
                record_kind=struct.unpack_from('<I', data, record + 0x40)[0],
                dependencies=dependencies,
                components=_components(data, table, ACTOR_COMPONENTS, ACTOR_COMPONENT_DATA))


def decode_zone_actors(data):
    """All zone actors, including non-render objects, with complete placement matrices.

    c/e at +0x0C/+0x10 are first/count in 0x50EDC53D. +0x14 remains raw.
    Render records (kind 0, +0x40) carry the actor id at +0x48 and a sentinel
    model index at +0x58. Other kinds have different headers; do not pretend
    their +0x48 is an actor id. +0x44 is a byte count, not a scale or bound.
    """
    table = _table(data)
    if ACTORS not in table:
        return dict(actors=[], components=[])
    names, types = _strings(data, table, ACTOR_NAMES), _strings(data, table, ACTOR_TYPES)
    if DATA_HEAP not in table:
        raise ValueError('Actor zone lacks heap')
    heap, heap_size = table[DATA_HEAP]
    components = _components(data, table, ZONE_COMPONENTS, ZONE_COMPONENT_DATA)
    coverage = [0] * len(components)
    replacements = {path_crc64(p): p for p in override_materials(data, table)}
    actors, ids = [], set()
    records = list(struct.iter_unpack('<6IQ', _chunk(data, table, ACTORS, 32)))
    for i, (n, t, rel, first, count, flags, aid) in enumerate(records):
        if n != NO_INDEX and n >= len(names) or t >= len(types):
            raise ValueError(f'Actor {i}: string index out of range')
        if not types[t].lower().endswith('.actor'):
            raise ValueError(f'Actor {i}: type path is not an actor')
        if aid in ids:
            raise ValueError(f'Actor {i}: duplicate id')
        ids.add(aid)
        if rel % 8 or rel + 0x70 > heap_size:
            raise ValueError(f'Actor {i}: record header outside heap or unaligned')
        record = heap + rel
        size = struct.unpack_from('<I', data, record + 0x44)[0]
        overrun = rel + size - heap_size
        if size < 0x70 or (overrun > 0 and not (i == len(records) - 1 and overrun < 8)):
            raise ValueError(f'Actor {i}: record size outside heap')
        rows = _matrix(data, record)
        kind = struct.unpack_from('<I', data, record + 0x40)[0]
        if kind not in (0, 1, 2, 4):
            raise ValueError(f'Actor {i}: unsupported record kind {kind}')
        overrides = []
        if kind == 0:
            if struct.unpack_from('<Q', data, record + 0x48)[0] != aid:
                raise ValueError(f'Actor {i}: render-record id mismatch')
            if struct.unpack_from('<I', data, record + 0x58)[0] != 0xFFFF:
                raise ValueError(f'Actor {i}: unexpected render-record model index')
            if size >= 0xB0:
                overrides = _material_overrides(data, record, size, heap + heap_size, replacements)
        if first + count > len(components):
            raise ValueError(f'Actor {i}: component range outside table')
        for j in range(first, first + count):
            coverage[j] += 1
        actors.append(dict(index=i, name=None if n == NO_INDEX else names[n], actor_path=types[t],
                           id=f'{aid:016X}', matrix_rows=rows, flags_raw=flags,
                           record_offset=record, heap_offset=rel, record_size=size, record_kind=kind,
                           components=components[first:first + count], material_overrides=overrides))
    if any(n != 1 for n in coverage):
        raise ValueError('Actor component definitions are not owned exactly once')
    return dict(actors=actors, components=components)
