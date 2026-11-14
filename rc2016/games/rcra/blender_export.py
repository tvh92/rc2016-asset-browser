"""Rift Apart materials for Blender: each material's surface recipe and its unit textures, written beside an export.

``blender_materials`` is the ``Game.blender_materials`` hook. For every material it translates the graph's G-buffer
writer (rc2016.games.rcra.surface), writes the textures the recipe samples and returns a JSON-ready entry that
rc2016/export/rcra_blender.py turns into a node tree. A material whose writer is not supported gets an entry with its
reason and keeps the glTF importer's material.

A blended material (rc2016.games.rcra.material.material_blend: additive, blended or modulate) also gets its forward program
translated (rc2016.games.rcra.surface.forward) as ``forward``: the colour and alpha the game blends, with every light source
zero. Its G-buffer surface, when it translates, stays as ``recipe`` and is lit by the renderer. A blended material whose
forward program does not translate keeps the opaque surface and says why.

Unit textures keep their stored values. 8-bit formats are written as RGBA PNG with the D3D defaults for channels a format
lacks (0 for green and blue, 1 for alpha); float formats are written as raw float32 RGBA (``.f32``, little endian, rows
top first) that Blender loads into a float image. Colourspace follows the DXGI format: sRGB formats are 'sRGB', everything
else 'Non-Color'. Texture arrays write the slice the recipe samples; an array sampled at a slice chosen at run time
writes every slice (up to MAX_SLICES), and the Blender material picks one by round(slice), clamped, as D3D does.
A 3D texture (flipbook volumes) is written as one atlas: its slices in a grid of ``columns``, each with a one-texel border
copied from the opposite edge, so a bilinear lookup inside a tile wraps as the texture does; the Blender material samples
the two slices around the depth coordinate and mixes them (trilinear, wrapping in depth).
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
ENTRY_SCHEMA = 'rcra-blender-materials-v2'
# How each blend mode is composed over what is behind it. The engine names the modes ("Additive", "Lerp", "Modulate"
# blending into the alpha layer); the blend factors themselves are pipeline state outside the files, so these are the
# standard factors for those names, not decoded values.
BLEND_LIMITATIONS = {
    'blended': 'Blended (the engine\'s "Lerp" mode): colour x alpha over the background x (1 - alpha), the standard '
               'factors for that mode; the pipeline\'s blend state is not in the files.',
    'additive': 'Additive: colour x alpha added to the background, the standard factors for that mode; the pipeline\'s blend '
                'state is not in the files.',
    'modulate': 'Modulate: the background multiplied by the colour (lerped to white by alpha), the standard factors for that '
                'mode; the pipeline\'s blend state is not in the files.'}
MAX_SLICES = 32
ALL_SLICES = 'all'
VOLUME = 'volume'


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
    row = _save(data, texture, Path(path).stem + ('_slice%d' % index if index else ''), folder, path)
    if cache is not None:
        cache[key] = row
    return row


def volume_atlas(slices, columns):
    """(rows, cols, 4) atlas of equal (h, w, 4) slices, each tile (h + 2, w + 2) with a wrapped one-texel border."""
    h, w = slices[0].shape[:2]
    rows = -(-len(slices) // columns)
    atlas = np.zeros((rows * (h + 2), columns * (w + 2), 4), np.float32)
    for k, data in enumerate(slices):
        r, c = divmod(k, columns)
        atlas[r * (h + 2):(r + 1) * (h + 2), c * (w + 2):(c + 1) * (w + 2)] = np.pad(data, ((1, 1), (1, 1), (0, 0)), 'wrap')
    return atlas


def write_volume_texture(source, path, folder, cache=None):
    """Write a 3D texture as a padded slice atlas; the row carries volume=dict(depth, columns, width, height)."""
    from rc2016.games.rcra.texture import load_texture_full
    key = (path, VOLUME)
    if cache is not None and key in cache:
        return cache[key]
    texture = load_texture_full(source, path)
    count = len(texture.get('slices', [texture['pixels']]))
    columns = max(1, int(np.ceil(np.sqrt(count))))
    atlas = volume_atlas([texel_array(texture, k) for k in range(count)], columns)
    row = _save(atlas, texture, Path(path).stem + '_volume', folder, path)
    row['volume'] = dict(depth=count, columns=columns, width=int(texture['width']), height=int(texture['height']))
    if cache is not None:
        cache[key] = row
    return row


def _save(data, texture, stem, folder, path):
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
    return dict(path=str(target), colourspace=colourspace, kind=kind, width=int(data.shape[1]), height=int(data.shape[0]),
                source=path, format=int(texture['format']))


def _slices(recipe):
    """{binding: slice} of the recipe's samples, ALL_SLICES where the slice is chosen at run time or differs, VOLUME for a
    3D texture."""
    slices = {}
    for n in recipe['nodes']:
        if n['op'] == 'sample':
            z = recipe['nodes'][n['args'][2]] if len(n['args']) > 2 else None
            if n.get('dimension') == rsurface.DIMENSION_TEXTURE3D:
                index = VOLUME
            else:
                index = 0 if z is None else (int(round(z['value'])) if z['op'] == 'const' else ALL_SLICES)
            if slices.setdefault(n['binding'], index) != index:
                slices[n['binding']] = ALL_SLICES
    return slices


def _unit_images(source, path, folder, index, cache):
    if index == VOLUME:
        return write_volume_texture(source, path, folder, cache)
    if index != ALL_SLICES:
        return write_unit_texture(source, path, folder, index, cache)
    from rc2016.games.rcra.texture import texture_header
    count = max(1, int(texture_header(source, path).get('array_size') or 1))
    if count > MAX_SLICES:
        raise rsurface.Unsupported('Texture array %s has %d slices' % (path, count))
    return dict(slices=[write_unit_texture(source, path, folder, k, cache) for k in range(count)])


def _images(source, results, folder, texture_cache):
    """{binding: image row} of every texture the recipes sample (one material: a binding is the same texture in both)."""
    slices = {}
    for recipe, result in results:
        for binding, index in _slices(recipe).items():
            if slices.setdefault(binding, index) != index:
                if VOLUME in (index, slices[binding]):
                    raise rsurface.Unsupported('Texture %s is sampled as a volume and as an array' % binding)
                slices[binding] = ALL_SLICES
    textures = {}
    for _recipe, result in results:
        textures.update(result['textures'])
    return {binding: _unit_images(source, path, folder, slices.get(binding, 0), texture_cache)
            for binding, path in textures.items()}


def _reads_background(recipe):
    """True when a forward recipe's background tint is not the constant 0."""
    nodes = recipe['nodes']
    return any(nodes[recipe['terms'][k]]['op'] != 'const' or nodes[recipe['terms'][k]]['value']
               for k in ('background.r', 'background.g', 'background.b') if k in recipe['terms'])


def material_entry(source, material_path, folder, texture_cache=None, graph_cache=None):
    """The Blender entry of one material: dict(blend, recipe, forward, images, limitations), or dict(error)."""
    from rc2016.games.rcra.material import _graphs, _material_key, material_blend
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
    data = source.asset_data(key)
    try:
        blend = material_blend(data)
    except ValueError as error:
        return dict(error='ValueError: %s' % error, graph=graph_path)
    errors, results, limitations = {}, {}, []
    passes = (('forward', rsurface.forward), ('recipe', rsurface.surface)) if blend != 'opaque' else \
        (('recipe', rsurface.surface),)
    for name, translate in passes:
        try:
            results[name] = translate(graph, data)
        except (rsurface.Unsupported, ValueError, KeyError) as error:
            errors[name] = '%s: %s' % (type(error).__name__, error)
    if 'recipe' not in results and 'forward' not in results:
        return dict(error=errors.get('recipe') or errors.get('forward'), graph=graph_path, blend=blend)
    recipes = {name: rsurface.export_recipe(result) for name, result in results.items()}
    if blend == 'modulate' and 'forward' in recipes and _reads_background(recipes['forward']):
        errors['forward'] = 'Unsupported: a modulate material that passes the scene behind through is not linear in it'
        del results['forward'], recipes['forward']
        if 'recipe' not in results:
            return dict(error=errors.get('recipe') or errors['forward'], graph=graph_path, blend=blend)
    try:
        images = _images(source, [(recipes[n], results[n]) for n in recipes], folder, texture_cache)
    except (rsurface.Unsupported, ValueError, KeyError, OSError) as error:
        return dict(error='%s: %s' % (type(error).__name__, error), graph=graph_path, blend=blend)
    if blend != 'opaque':
        if 'forward' in results:
            limitations.append(BLEND_LIMITATIONS[blend])
            limitations += results['forward']['limitations']
            if 'recipe' not in results:
                limitations.append('The G-buffer surface does not translate (%s): only the forward colour is drawn, '
                                   'unlit by the renderer.' % errors['recipe'])
        else:
            limitations.append('The game draws this material %s in its forward pass, which does not translate (%s): '
                               'Blender gets its opaque G-buffer surface.' % (blend, errors['forward']))
    if 'recipe' in results:
        limitations += results['recipe']['limitations']
    limitations += [
        'Tangent frame from Blender\'s UV0 tangent space (the stored vertex tangent is not decoded); bitangent along the '
        "game's -v, as in the viewer.",
        'Texture filtering is Blender\'s linear repeat; the game\'s sampler states are not in the program.']
    entry = dict(graph=graph_path, blend=blend if 'forward' in results else 'opaque', game_blend=blend, images=images,
                 limitations=limitations)
    entry.update(recipes)
    return entry


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
