"""Rift Apart (PC) ``.material`` texture slots and the labelled viewer preview built from them.

A material asset is the 36-byte toc header (u32 type 0x88730155) and a DAT1 of the same type.
Section 0xF5260180 (ALERT "Material Serialized Data") starts with ten u32: section size, parameter
count, 2 unknown, parameter batch end, texture count, texture batch start, texture batch end,
2 unknown. Each texture record is (u32 string offset from the batch end, u32 slot key); the
strings follow the batch. Section 0xE1275683 is the 40-byte flags block (as in the 2016 game).

Slot keys are the low 32 bits of the 2016 game's 0x49A7866B........ slot hashes, and the corpus
binds them to the same texture suffixes (5,721 materials): ABDAD780 holds the base colour (_c in
2,600 of 2,862 materials), 7AE34E7A the normal (_n, 2,591 of 2,647), 1576D848 the _g map and
E68930AC the _m mask. The preview draws only the base colour and the normal: what the game's
_g and _m channels mean is not decoded, and no materialgraph shader is translated.
"""
import struct

from rc2016.games.rcra.toc import sections

MATERIAL_MAGIC = 0x88730155
SERIALIZED = 0xF5260180
FLAGS = 0xE1275683
SLOT_BASE_COLOUR, SLOT_NORMAL, SLOT_G, SLOT_MASK = 0xABDAD780, 0x7AE34E7A, 0x1576D848, 0xE68930AC
PREVIEW_ROLES = {SLOT_BASE_COLOUR: '_c', SLOT_NORMAL: '_n'}
RULE_KIND = 'rift apart preview'
LIMITATION = ('Rift Apart preview: base colour (slot ABDAD780) and normal (slot 7AE34E7A) only; the _g/_m slots and '
              'the material graph are not decoded, so gloss, metal, emission and blending are review values.')


def material_slots(data):
    """[(slot key, texture path)] of a material asset; empty when it binds no textures."""
    if len(data) < 52 or struct.unpack_from('<I', data, 0)[0] != MATERIAL_MAGIC:
        raise ValueError('Not a Rift Apart material (expected type 0x%08X)' % MATERIAL_MAGIC)
    dat1 = data[36:]
    table = sections(dat1)
    if SERIALIZED not in table:
        return []
    offset, size = table[SERIALIZED]
    section = dat1[offset:offset + size]
    if len(section) < 40:
        raise ValueError('Material serialized data is too short')
    count, start, end = struct.unpack_from('<3I', section, 20)
    if start + 8 * count > end or end > len(section):
        raise ValueError('Material texture batch outside its section')
    slots = []
    for i in range(count):
        name, key = struct.unpack_from('<II', section, start + 8 * i)
        at = end + name
        stop = section.find(b'\0', at)
        if at >= len(section) or stop < 0:
            raise ValueError('Material texture string outside its section')
        slots.append((key, section[at:stop].decode('latin-1').replace('\\', '/').lower()))
    return slots


def material_flags(data):
    """The u32 flags word at the start of section 0xE1275683, or None."""
    table = sections(data[36:])
    if FLAGS not in table or table[FLAGS][1] < 4:
        return None
    return struct.unpack_from('<I', data, 36 + table[FLAGS][0])[0]


def _material_key(source, path):
    keys = [k for k in source.resolve(path) if source.entries[k]['type'] == 'material']
    return keys[0] if len(keys) == 1 else None


def _graphs(source, key):
    return [r['path'] for r in source.asset_references(key) if r['path'].endswith('.materialgraph')]


def material_textures(source, materials, cache, translations=None, hd=False):
    """Material slot -> the viewer entry (viewer.textures.material_textures shape) for the preview:
    '_c' and '_n' loaded from their slots, every other role None, neutral parameters."""
    from rc2016.games.rcra.texture import load_texture
    from rc2016.viewer.textures import ROLES, _remember, _CACHE_LOCK
    result = {}
    for material in materials:
        entry = {role: None for role in ROLES}
        entry.update(k=0.0, g_mode='colored', self_lit='c_alpha', spec=1.0, gloss=1.0, rcra_preview=True)
        key = _material_key(source, material['path']) if material.get('path') else None
        if key is not None:
            try:
                slots = dict(material_slots(source.asset_data(key)))
            except (ValueError, KeyError, OSError):
                slots = {}
            for slot, role in PREVIEW_ROLES.items():
                path = slots.get(slot)
                if not path:
                    continue
                with _CACHE_LOCK:
                    known = path in cache
                    texture = cache.get(path)
                if not known:
                    try:
                        texture = load_texture(source, path, hd=hd)
                    except (ValueError, KeyError, OSError):
                        texture = None
                    texture = _remember(cache, path, texture)
                entry[role] = texture
        if translations is not None and material.get('path'):
            translations[material['path']] = None
        result[material['index']] = entry
    return result


def model_texture_list(source, materials):
    """Every texture the model's materials bind, then the textures their graphs reference (via_graph)."""
    from rc2016.viewer.textures import _suffix, is_placeholder_texture
    rows = {}
    for material in materials:
        key = _material_key(source, material['path']) if material.get('path') else None
        if key is None:
            continue
        try:
            direct = [path for _slot, path in material_slots(source.asset_data(key))]
            via_graph = []
            for graph in _graphs(source, key):
                graph_key = next((k for k in source.resolve(graph) if source.entries[k]['type'] == 'materialgraph'), None)
                if graph_key is not None:
                    via_graph += [r['path'] for r in source.asset_references(graph_key) if r['path'].endswith('.texture')]
        except (ValueError, KeyError, OSError):
            continue
        name = material.get('name') or material['path'].rsplit('/', 1)[-1].rsplit('.', 1)[0]
        for path, graph_only in [(p, False) for p in direct] + [(p, True) for p in via_graph]:
            if is_placeholder_texture(path):
                continue
            row = rows.setdefault(path, dict(path=path, suffix=_suffix(path), materials=[], via_graph=graph_only))
            row['via_graph'] = row['via_graph'] and graph_only
            if name not in row['materials']:
                row['materials'].append(name)
    return list(rows.values())


def material_rules(source, materials, translations=None):
    """{material path: {'graphs', 'rule_kind', 'rule', 'native_limitations'}}: every material gets the preview."""
    rules = {}
    for material in materials:
        path = material.get('path')
        if not path or path in rules:
            continue
        key = _material_key(source, path)
        graphs = _graphs(source, key) if key is not None else []
        rules[path] = dict(graphs=graphs, rule_kind=RULE_KIND, rule=None, native_limitations=[LIMITATION])
    return rules


def effect_preview(source, data, source_path=''):
    """No Rift Apart effect previews are decoded."""
    return None
