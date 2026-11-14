"""Rift Apart translated materials for the viewer: a receipt (rc2016.viewer.rcra_glsl) and its textures, per material.

The receipt holds the material's G-buffer surface recipe (rc2016.games.rcra.surface.surface), its forward recipe when the
game draws it blended (surface.forward, by material.material_blend), the blend mode and each texture binding's layout. The
choices follow the Blender export (rc2016.games.rcra.blender_export.material_entry): a blended material whose forward
program does not translate keeps its opaque surface; additive and modulate materials take no lit surface; a modulate
material that passes the scene behind it through is not linear in it and keeps its surface.

Textures are viewer texture dicts (rc2016.viewer.textures shape). A binding sampled at one array slice uploads that slice as
stored; a runtime-chosen slice or a 3D texture uploads a padded atlas of every slice (blender_export.volume_atlas), without
mipmaps so tiles never mix. Float and 16-bit data formats, which the viewer's block uploads do not take, upload as float
RGBA (``gl_float``) with the stored values, missing channels following D3D (blender_export.texel_array).
"""
from __future__ import annotations

import numpy as np

from rc2016.games.rcra import blender_export as bexp, surface as rsurface
from rc2016.viewer import rcra_glsl

MAX_BINDINGS = 14   # rc2016.viewer.native_glsl.TEXTURE_UNITS
RGBA8, RGBA8_SRGB, GL_RGBA = 28, 29, 0x1908


class NotTranslated(ValueError):
    pass


def _graph(source, material_key):
    from rc2016.games.rcra.material import _graphs
    graphs = list(_graphs(source, material_key))
    if not graphs:
        raise NotTranslated('material references no graph')
    graph_key = next((k for k in source.resolve(graphs[0]) if source.entries[k]['type'] == 'materialgraph'), None)
    if graph_key is None:
        raise NotTranslated('graph not found: ' + graphs[0])
    return source.asset_data(graph_key)


def translate(source, material_key):
    """(surface recipe or None, forward recipe or None, blend, limitations, textures {binding: path})."""
    from rc2016.games.rcra.material import material_blend
    graph, data = _graph(source, material_key), source.asset_data(material_key)
    blend = material_blend(data)
    results, errors = {}, {}
    for name, fn in (('forward', rsurface.forward), ('surface', rsurface.surface)) if blend != 'opaque' else \
            (('surface', rsurface.surface),):
        try:
            results[name] = fn(graph, data)
        except (rsurface.Unsupported, ValueError, KeyError) as error:
            errors[name] = str(error)
    recipes = {name: rsurface.export_recipe(r) for name, r in results.items()}
    if blend == 'modulate' and 'forward' in recipes and bexp._reads_background(recipes['forward']):
        errors['forward'] = 'a modulate material that passes the scene behind through is not linear in it'
        del recipes['forward'], results['forward']
    limitations = [rcra_glsl.LIMITATION]
    if blend != 'opaque' and 'forward' not in recipes:
        limitations.append('The game draws this material %s; its forward program does not translate (%s), so the viewer '
                           'draws its opaque surface.' % (blend, errors.get('forward')))
        blend = 'opaque'
    if blend in ('additive', 'modulate'):
        recipes.pop('surface', None)
        results.pop('surface', None)
    if not recipes or (blend == 'opaque' and 'surface' not in recipes):
        raise NotTranslated(errors.get('surface') or errors.get('forward') or 'nothing translates')
    if blend != 'opaque':
        limitations.append(bexp.BLEND_LIMITATIONS[blend])
    textures = {}
    for result in results.values():
        textures.update(result['textures'])
        limitations += result['limitations']
    return recipes.get('surface'), recipes.get('forward'), blend, limitations, textures


def _layouts(recipes):
    """{binding: slice index | ALL_SLICES | VOLUME} over the recipes (blender_export._slices)."""
    slices = {}
    for recipe in recipes:
        for binding, index in bexp._slices(recipe).items():
            if slices.setdefault(binding, index) != index:
                if bexp.VOLUME in (index, slices[binding]):
                    raise NotTranslated('texture %s is sampled as a volume and as an array' % binding)
                slices[binding] = bexp.ALL_SLICES
    return slices


def _float(data, path, **extra):
    """A float RGBA viewer texture dict of (h, w, 4) stored values."""
    return dict(path=path, width=int(data.shape[1]), height=int(data.shape[0]), format=2, gl_format=None, gl_pixel_format=None,
                gl_float=True, pixels=np.ascontiguousarray(data, '<f4').tobytes(), srgb=False, **extra)


def _atlas(texture, path):
    """A padded atlas texture dict of every slice of ``texture`` (RGBA8, or float for float formats), and its grid."""
    count = len(texture.get('slices', [texture['pixels']]))
    columns = max(1, int(np.ceil(np.sqrt(count))))
    atlas = bexp.volume_atlas([bexp.texel_array(texture, k) for k in range(count)], columns)
    if texture['format'] in bexp.FLOAT_FORMATS:
        out = _float(atlas, path + '#atlas', no_mipmaps=True)
    else:
        srgb = bool(texture.get('srgb'))
        pixels = np.round(np.clip(atlas, 0.0, 1.0) * 255.0).astype(np.uint8).tobytes()
        out = dict(path=path + '#atlas', width=int(atlas.shape[1]), height=int(atlas.shape[0]),
                   format=RGBA8_SRGB if srgb else RGBA8, gl_format=None, gl_pixel_format=GL_RGBA, pixels=pixels, srgb=srgb,
                   no_mipmaps=True)
    grid = dict(depth=count, columns=columns, width=int(texture['width']), height=int(texture['height']))
    return out, grid


def _slice(texture, path, index):
    slices = texture.get('slices', [texture['pixels']])
    if index >= len(slices):
        raise NotTranslated('texture %s has no slice %d' % (path, index))
    if texture['format'] in bexp.FLOAT_FORMATS:
        return _float(bexp.texel_array(texture, index), '%s#float%d' % (path, index))
    if index == 0:
        return texture
    return dict(texture, pixels=slices[index], path='%s#slice%d' % (path, index))


def material_receipt(source, material_key, hd=False):
    """(receipt, {binding: viewer texture dict}); raises NotTranslated with the reason."""
    from rc2016.games.rcra.texture import load_texture, load_texture_full
    surface, forward, blend, limitations, paths = translate(source, material_key)
    kinds = _layouts([r for r in (surface, forward) if r])
    if len(paths) > MAX_BINDINGS:
        raise NotTranslated('%d texture bindings, the viewer has %d units' % (len(paths), MAX_BINDINGS))
    layouts, textures = {}, {}
    for binding, path in sorted(paths.items()):
        try:
            texture = load_texture(source, path, hd=hd)
        except ValueError:
            try:   # a data format the viewer previews nowhere else: read as stored, uploaded as float
                texture = load_texture_full(source, path)
            except (ValueError, KeyError, OSError) as error:
                raise NotTranslated('texture %s: %s' % (path, error)) from error
            if texture['format'] not in bexp.FLOAT_FORMATS:
                raise NotTranslated('texture %s: format %d is not previewable' % (path, texture['format']))
        except (KeyError, OSError) as error:
            raise NotTranslated('texture %s: %s' % (path, error)) from error
        index = kinds.get(binding, 0)
        if index in (bexp.ALL_SLICES, bexp.VOLUME):
            textures[binding], grid = _atlas(texture, path)
            layouts[binding] = dict(grid, kind='volume' if index == bexp.VOLUME else 'array')
        else:
            textures[binding] = _slice(texture, path, index)
            layouts[binding] = dict(kind='slice')
    emission = surface['terms']['emission'] if surface else forward['terms']['colour.r']
    recipe = surface or forward
    receipt = dict(kind='rcra', status='ready', blend=blend, surface=surface, forward=forward, layouts=layouts,
                   limitations=limitations, nodes=recipe['nodes'], outputs=dict(emission=emission),
                   translation_sha256=rcra_glsl.receipt_sha256(surface, forward, blend, layouts))
    return receipt, textures


def cached_receipt(source, material_key, hd=False):
    """material_receipt memoised on the source: (receipt, textures) or (None, reason)."""
    memo = source.__dict__.setdefault('_rcra_viewer_receipts', {})
    key = (material_key, bool(hd))
    if key not in memo:
        try:
            memo[key] = material_receipt(source, material_key, hd)
        except NotTranslated as error:
            memo[key] = (None, str(error))
    return memo[key]
