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

The one decoded graph is architecture glass (gbl_arch_glass, 44 materials, e.g. the Nefarious trooper and swarmer
domes). Its forward pixel shader reads named reflection data, so the rule is traced to that program, not to a capture:
see ARCH_GLASS_PARAMS and the uKind 26 comment in rc2016/viewer/shading.py.
"""
import struct

from rc2016.games.rcra.toc import sections

MATERIAL_MAGIC = 0x88730155
SERIALIZED = 0xF5260180
FLAGS = 0xE1275683
SLOT_BASE_COLOUR, SLOT_NORMAL, SLOT_G, SLOT_MASK = 0xABDAD780, 0x7AE34E7A, 0x1576D848, 0xE68930AC
PREVIEW_ROLES = {SLOT_BASE_COLOUR: '_c', SLOT_NORMAL: '_n'}
RULE_KIND = 'rift apart preview'
ARCH_GLASS_GRAPH = 'materialgraph/environment/global/architecture/gbl_arch_glass/gbl_arch_glass.materialgraph'
GLASS_RULE_KIND = 'rift apart glass'
GRAPH_SLOT_TABLE, GRAPH_PARAM_RECORDS, GRAPH_DEFAULTS = 0x1CAFE804, 0x45C4F4C0, 0xA59F667B
# gbl_arch_glass pixel shader (SM5 ps_5_0, forward pass): the four material texture units t5..t8 bind the graph's slot table
# in order (unit u = t(5 + u)); cb2 (g_CustomMaterial_Buffer) holds the graph's float defaults at the graph's own byte
# offsets, overwritten by the material's parameter values (keys in section 0x45C4F4C0 of the graph and 0xF5260180 of the material).
ARCH_GLASS_ROLES = {0: '_pick', 1: '_window_c', 2: '_n', 3: '_window_g'}   # t5 scalar, t6 colour, t7 normal xy, t8 F0 rgb + gloss a
ARCH_GLASS_PARAMS = ('soft_flag', 'emissive_adapt', 'gloss_scale', 'rim_width', 'colour_k', 'g_mode', 'emission', 'alpha_floor')   # cb2 dwords 0..7
GLASS_LIMITATION = ('Rift Apart glass preview (graph gbl_arch_glass, traced to its forward pixel shader): the material parameters, the four '
                    'texture units, the rim alpha with its floor and the emission are decoded; the light list, shadows, probes, '
                    'screen-space reflections, fog, soft depth fade, vertex alpha and tonemapping are viewer stand-ins, and the '
                    'adapted luminance that scales the emission is not known.')
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


def material_parameters(data):
    """{parameter key: tuple of floats} of a material's section 0xF5260180: records (u16 offset, u16 byte size, u32 key) from byte
    40, their values in a block that starts at header word 2."""
    table = sections(data[36:])
    if SERIALIZED not in table:
        return {}
    offset, size = table[SERIALIZED]
    section = data[36:][offset:offset + size]
    if len(section) < 40:
        raise ValueError('Material serialized data is too short')
    count, base = struct.unpack_from('<2I', section, 4)
    if 40 + 8 * count > len(section) or base > len(section):
        raise ValueError('Material parameter records outside their section')
    values = {}
    for i in range(count):
        at, width, key = struct.unpack_from('<HHI', section, 40 + 8 * i)
        if width % 4 or base + at + width > len(section):
            raise ValueError('Material parameter value outside its section')
        values[key] = struct.unpack_from('<%df' % (width // 4), section, base + at)
    return values


def graph_layout(data):
    """(slot rows, parameter offsets, defaults) of a materialgraph: [(unit, slot key, default texture path)], {key: byte offset} and
    the graph's float defaults. Section 0x1CAFE804 rows are (u32 string offset, u16, u16 unit, u64 slot hash)."""
    dat = data[36:]
    table = sections(dat)
    if not {GRAPH_SLOT_TABLE, GRAPH_PARAM_RECORDS, GRAPH_DEFAULTS} <= set(table):
        raise ValueError('Material graph has no slot, parameter or default section')
    offset, size = table[GRAPH_SLOT_TABLE]
    rows = []
    for at in range(offset, offset + size - 15, 16):
        name, _flags, unit, key, _high = struct.unpack_from('<IHHII', dat, at)
        stop = dat.find(b'\0', name)
        if name >= len(dat) or stop < 0:
            raise ValueError('Material graph default texture string outside its data')
        rows.append((unit, key, dat[name:stop].decode('latin-1').replace('\\', '/').lower()))
    offset, size = table[GRAPH_PARAM_RECORDS]
    offsets = {key: at for at, _width, key in (struct.unpack_from('<HHI', dat, i) for i in range(offset, offset + size - 7, 8))}
    offset, size = table[GRAPH_DEFAULTS]
    return rows, offsets, struct.unpack_from('<%df' % (size // 4), dat, offset)


def arch_glass_params(graph_data, material_data):
    """{name: float} of ARCH_GLASS_PARAMS for one material: the graph's defaults with the material's values at the graph's offsets."""
    _rows, offsets, defaults = graph_layout(graph_data)
    cb2 = list(defaults) + [0.0] * (len(ARCH_GLASS_PARAMS) - len(defaults))
    for key, values in material_parameters(material_data).items():
        if key in offsets:
            for i, value in enumerate(values):
                if offsets[key] // 4 + i < len(cb2):
                    cb2[offsets[key] // 4 + i] = value
    return dict(zip(ARCH_GLASS_PARAMS, (float(x) for x in cb2)))


def _material_key(source, path):
    keys = [k for k in source.resolve(path) if source.entries[k]['type'] == 'material']
    return keys[0] if len(keys) == 1 else None


def _graphs(source, key):
    return [r['path'] for r in source.asset_references(key) if r['path'].endswith('.materialgraph')]


def _load(source, path, cache, hd):
    """The viewer texture dict of ``path`` through the shared cache, or None when it cannot be read."""
    from rc2016.games.rcra.texture import load_texture
    from rc2016.viewer.textures import _remember, _CACHE_LOCK
    with _CACHE_LOCK:
        known = path in cache
        texture = cache.get(path)
    if not known:
        try:
            texture = load_texture(source, path, hd=hd)
        except (ValueError, KeyError, OSError):
            texture = None
        texture = _remember(cache, path, texture)
    return texture


def _arch_glass(source, key, slots, cache, hd):
    """(forward rule, {role: texture}) of a gbl_arch_glass material, or None when it does not use that graph or the graph cannot be read.

    An unbound unit takes the graph's default texture, as the game does."""
    if ARCH_GLASS_GRAPH not in _graphs(source, key):
        return None
    graph_key = next((k for k in source.resolve(ARCH_GLASS_GRAPH) if source.entries[k]['type'] == 'materialgraph'), None)
    if graph_key is None:
        return None
    try:
        graph = source.asset_data(graph_key)
        params = arch_glass_params(graph, source.asset_data(key))
        rows, _offsets, _defaults = graph_layout(graph)
    except (ValueError, KeyError, OSError, struct.error):
        return None
    textures = {}
    for unit, slot, default in rows:
        role = ARCH_GLASS_ROLES.get(unit)
        if role:
            textures[role] = _load(source, slots.get(slot) or default, cache, hd)
    forward = dict(kind='rcra_arch_glass', blend='alpha_over', two_sided=False, params=params, writer='gbl_arch_glass',
                   limitation=GLASS_LIMITATION)
    return forward, textures


def material_textures(source, materials, cache, translations=None, hd=False):
    """Material slot -> the viewer entry (viewer.textures.material_textures shape) for the preview:
    '_c' and '_n' loaded from their slots, every other role None, neutral parameters. A gbl_arch_glass material instead gets its
    decoded forward glass rule (``forward``) and the four graph units as the roles _pick, _window_c, _n and _window_g."""
    from rc2016.viewer.textures import ROLES
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
            glass = _arch_glass(source, key, slots, cache, hd)
            if glass:
                entry['forward'] = glass[0]
                entry.update(glass[1])
                entry['_c'] = glass[1].get('_window_c')   # the Textured mode, which draws no forward rule, still shows the colour
            else:
                for slot, role in PREVIEW_ROLES.items():
                    if slots.get(slot):
                        entry[role] = _load(source, slots[slot], cache, hd)
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
    """{material path: {'graphs', 'rule_kind', 'rule', 'native_limitations'}}: every material gets the preview, except the
    gbl_arch_glass ones, which get the decoded glass rule (GLASS_RULE_KIND)."""
    rules = {}
    for material in materials:
        path = material.get('path')
        if not path or path in rules:
            continue
        key = _material_key(source, path)
        graphs = _graphs(source, key) if key is not None else []
        glass = ARCH_GLASS_GRAPH in graphs
        rules[path] = dict(graphs=graphs, rule_kind=GLASS_RULE_KIND if glass else RULE_KIND, rule=None,
                           native_limitations=[GLASS_LIMITATION if glass else LIMITATION])
    return rules


def effect_preview(source, data, source_path=''):
    """No Rift Apart effect previews are decoded."""
    return None
