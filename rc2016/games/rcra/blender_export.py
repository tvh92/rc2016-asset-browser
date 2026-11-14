"""Rift Apart materials for Blender: each material's surface recipe and its unit textures, written beside an export.

``blender_materials`` is the ``Game.blender_materials`` hook. For every material it translates the graph's G-buffer
writer (rc2016.games.rcra.surface), writes the textures the recipe samples and returns a JSON-ready entry that
rc2016/export/rcra_blender.py turns into a node tree. A material whose writer is not supported gets an entry with its
reason and keeps the glTF importer's material.

Unit textures keep their stored values. 8-bit formats are written as RGBA PNG with the D3D defaults for channels a format
lacks (0 for green and blue, 1 for alpha); float formats are written as raw float32 RGBA (``.f32``, little endian, rows
top first) that Blender loads into a float image. Colourspace follows the DXGI format: sRGB formats are 'sRGB', everything
else 'Non-Color'. Texture arrays write the slice the recipe samples; an array sampled at a slice chosen at run time
writes every slice (up to MAX_SLICES), and the Blender material picks one by round(slice), clamped, as D3D does.
"""
import json
from pathlib import Path

import numpy as np

from rc2016.games.rcra import surface as rsurface

SRGB_FORMATS = frozenset({29, 72, 75, 78, 91, 93, 99})
# DXGI float/integer formats as (numpy dtype, channels).
FLOAT_FORMATS = {2: ('<f4', 4), 10: ('<f2', 4), 16: ('<f4', 2), 34: ('<f2', 2), 41: ('<f4', 1), 54: ('<f2', 1),
                 11: ('<u2', 4), 35: ('<u2', 2), 56: ('<u2', 1)}
UNORM16 = frozenset({11, 35, 56})
ENTRY_SCHEMA = 'rcra-blender-materials-v1'
# Third u32 of the material's flags section 0xE1275683: bit 0x100 is set on every glass and effect material sampled
# (gbl_arch_glass, basic_glass, exp_quad, exp_beam) and clear on the opaque ones; inferred, not decoded, so it only labels.
FORWARD_HINT_BIT = 0x100
MAX_SLICES = 32
ALL_SLICES = 'all'


def texel_array(texture, index=0):
    """Float32 (height, width, 4) of one slice as stored, missing channels 0 and alpha 1."""
    fmt, width, height = texture['format'], texture['width'], texture['height']
    pixels = texture.get('slices', [texture['pixels']])[index]
    if fmt in FLOAT_FORMATS:
        dtype, channels = FLOAT_FORMATS[fmt]
        data = np.frombuffer(pixels, dtype=dtype, count=width * height * channels).astype(np.float32)
        if fmt in UNORM16:
            data /= 65535.0
        data = data.reshape(height, width, channels)
    else:
        from rc2016.viewer.textures import to_image
        image = to_image(texture, index)
        channels = {'L': 1, 'LA': 2, 'RGB': 3, 'RGBA': 4}.get(image.mode)
        if channels is None:
            image = image.convert('RGBA')
            channels = 4
        data = np.asarray(image, dtype=np.float32).reshape(height, width, channels) / 255.0
        if image.mode == 'LA':   # luminance + alpha is not a D3D layout; keep it as red + alpha
            data = np.stack([data[..., 0], np.zeros_like(data[..., 0]), np.zeros_like(data[..., 0]), data[..., 1]], -1)
            channels = 4
        if fmt == 49 or (channels == 3 and fmt in (83, 84)):   # R8G8 / BC5: blue is not stored
            data[..., 2] = 0.0
    out = np.zeros((height, width, 4), np.float32)
    out[..., 3] = 1.0
    out[..., :min(channels, 4)] = data[..., :4]
    return out


def write_unit_texture(source, path, folder, index=0, cache=None):
    """Write one texture slice for Blender; returns {path, colourspace, kind, width, height}."""
    from rc2016.games.rcra.texture import load_texture_full
    key = (path, index)
    if cache is not None and key in cache:
        return cache[key]
    texture = load_texture_full(source, path)
    data = texel_array(texture, index)
    stem = Path(path).stem + ('_slice%d' % index if index else '')
    folder.mkdir(parents=True, exist_ok=True)
    colourspace = 'sRGB' if texture['format'] in SRGB_FORMATS else 'Non-Color'
    if texture['format'] in FLOAT_FORMATS:
        target = folder / (stem + '.f32')
        target.write_bytes(np.ascontiguousarray(data, '<f4').tobytes())
        kind = 'float'
    else:
        from PIL import Image
        target = folder / (stem + '.png')
        Image.fromarray(np.round(data * 255.0).astype(np.uint8), 'RGBA').save(target)
        kind = 'png'
    row = dict(path=str(target), colourspace=colourspace, kind=kind, width=int(data.shape[1]), height=int(data.shape[0]),
               source=path, format=int(texture['format']))
    if cache is not None:
        cache[key] = row
    return row


def _slices(recipe):
    """{binding: slice} of the recipe's samples, ALL_SLICES where the slice is chosen at run time or differs."""
    slices = {}
    for n in recipe['nodes']:
        if n['op'] == 'sample':
            z = recipe['nodes'][n['args'][2]] if len(n['args']) > 2 else None
            index = 0 if z is None else (int(round(z['value'])) if z['op'] == 'const' else ALL_SLICES)
            if slices.setdefault(n['binding'], index) != index:
                slices[n['binding']] = ALL_SLICES
    return slices


def _unit_images(source, path, folder, index, cache):
    if index != ALL_SLICES:
        return write_unit_texture(source, path, folder, index, cache)
    from rc2016.games.rcra.texture import texture_header
    count = max(1, int(texture_header(source, path).get('array_size') or 1))
    if count > MAX_SLICES:
        raise rsurface.Unsupported('Texture array %s has %d slices' % (path, count))
    return dict(slices=[write_unit_texture(source, path, folder, k, cache) for k in range(count)])


def material_entry(source, material_path, folder, texture_cache=None, graph_cache=None):
    """The Blender entry of one material: dict(recipe, images, limitations), or dict(error) when unsupported."""
    from rc2016.games.rcra.material import _graphs, _material_key
    key = _material_key(source, material_path)
    if key is None:
        return dict(error='material asset not found')
    graphs = list(_graphs(source, key))
    if not graphs:
        return dict(error='material references no graph')
    graph_path = graphs[0]
    graph_cache = graph_cache if graph_cache is not None else {}
    if graph_path not in graph_cache:
        gkey = next((k for k in source.resolve(graph_path) if source.entries[k]['type'] == 'materialgraph'), None)
        graph_cache[graph_path] = source.asset_data(gkey) if gkey is not None else None
    graph = graph_cache[graph_path]
    if graph is None:
        return dict(error='graph not found: ' + graph_path)
    try:
        result = rsurface.surface(graph, source.asset_data(key))
        recipe = rsurface.export_recipe(result)
        slices = _slices(recipe)
        images = {binding: _unit_images(source, result['textures'][binding], folder, slices.get(binding, 0),
                                        texture_cache) for binding in result['textures']}
    except (rsurface.Unsupported, ValueError, KeyError, OSError) as error:
        return dict(error='%s: %s' % (type(error).__name__, error), graph=graph_path)
    forward = _forward_hint(source.asset_data(key))
    limitations = list(result['limitations']) + ([
        'The game probably draws this material blended in its forward pass (flags bit 0x100, inferred): Blender gets its '
        'opaque G-buffer surface, without the forward alpha or colour.'] if forward else []) + [
        'Tangent frame from Blender\'s UV0 tangent space (the stored vertex tangent is not decoded); bitangent along the '
        "game's -v, as in the viewer.",
        'Texture filtering is Blender\'s linear repeat; the game\'s sampler states are not in the program.']
    return dict(graph=graph_path, recipe=recipe, images=images, limitations=limitations, forward_hint=forward)


def _forward_hint(material_data):
    import struct
    from rc2016.games.rcra.material import FLAGS
    from rc2016.games.rcra.toc import sections
    table = sections(material_data[36:])
    if FLAGS not in table or table[FLAGS][1] < 12:
        return False
    return bool(struct.unpack_from('<I', material_data, 36 + table[FLAGS][0] + 8)[0] & FORWARD_HINT_BIT)


def blender_materials(source, materials, folder):
    """Write ``rcra_materials.json`` for {glTF material name: material dict}; returns (path, {name: error})."""
    folder = Path(folder)
    texture_cache, graph_cache, entries, errors = {}, {}, {}, {}
    for name, material in materials.items():
        path = material.get('path')
        entry = material_entry(source, path, folder / 'textures' / 'units', texture_cache, graph_cache) if path else \
            dict(error='no material asset')
        entries[name] = dict(entry, material=path)
        if 'error' in entry:
            errors[name] = entry['error']
    target = folder / 'rcra_materials.json'
    target.write_text(json.dumps(dict(schema=ENTRY_SCHEMA, materials=entries)), encoding='utf-8')
    return target, errors
