"""Rift Apart glTF/OBJ export maps: the ``Game.material_maps`` hook.

Only what is decoded goes in: the base colour (slot ABDAD780) and the DirectX tangent normal (slot
7AE34E7A) of each material, at full resolution, converted by the same pixel code as the R&C 2016
export (``export.models._convert_maps``). The _g and _m slots, the material graph, the flags block
and blending are not decoded, so every material is exported opaque, single-sided, non-metallic and
fully rough, and each one says so in its notes.
"""
from __future__ import annotations

from pathlib import Path

NOTE = ('Rift Apart preview: base colour and normal only. The _g/_m slots and the material graph are not decoded, '
        'so the export is opaque, non-metallic and fully rough; no gloss, metal, emission or blending is claimed.')


def exportable(fmt) -> bool:
    """Formats ``viewer.textures.to_image`` reads as stored: RGBA8, R8G8, R8 and the BC formats."""
    from rc2016.viewer.textures import GL_COMPRESSED, GL_UNCOMPRESSED
    return fmt in GL_COMPRESSED or fmt in GL_UNCOMPRESSED


def material_maps(source, material, cache, map_cache=None):
    from rc2016.export.models import _convert_maps
    from rc2016.games.rcra.material import PREVIEW_ROLES, _material_key, material_slots
    from rc2016.games.rcra.texture import load_texture_full
    name = material.get('name') or (Path(material['path']).stem if material.get('path') else f"material_{material.get('index', 0)}")
    out = dict(name=name, base=None, mr=None, normal=None, emissive=None, emissive_strength=0.0,
               alpha_mode='OPAQUE', alpha=1.0, double_sided=False, notes=[], sources={}, png={})
    if not material.get('path'):
        out['notes'].append('no material file; neutral grey')
        out['base_factor'] = [0.5, 0.5, 0.5]
        return out
    paths = {}
    try:
        key = _material_key(source, material['path'])
        if key is None:
            raise ValueError('no single material asset for this path')
        slots = dict(material_slots(source.asset_data(key)))
        paths = {role: slots[slot] for slot, role in PREVIEW_ROLES.items() if slots.get(slot)}
    except (ValueError, KeyError, OSError) as error:
        out['notes'].append(f'material unreadable: {error}')

    def texture(role):
        path = paths.get(role)
        if not path:
            return None
        if path not in cache:
            try:
                loaded = load_texture_full(source, path)
                if not exportable(loaded['format']):
                    # Float/16-bit data formats (e.g. the 4x4 R32G32_FLOAT default_pink_shader_n placeholder): how
                    # their values map to 8-bit is not decoded, so they are left out instead of guessed.
                    raise ValueError(f"DXGI format {loaded['format']} has no decoded 8-bit conversion")
                cache[path] = loaded
            except (ValueError, KeyError, OSError) as error:
                cache[path] = None
                out['notes'].append(f'{role} texture {path} left out: {error}')
        if cache[path] is not None:
            out['sources'][role] = path
        return cache[path]

    c_tex, n_tex = texture('_c'), texture('_n')
    if c_tex is None:
        out['base_factor'] = [0.8, 0.8, 0.8]
        out['notes'].append('no base colour texture: white-grey placeholder')
    textured = c_tex is not None or n_tex is not None
    cache_key = ('rcra', paths.get('_c'), paths.get('_n'))
    entry = map_cache.get(cache_key) if map_cache is not None and textured else None
    if entry is None:
        entry = _convert_maps(c_tex, None, n_tex, dict(g_mode='colored', k=0.0))
        entry['png'] = {}
        if map_cache is not None and textured:
            map_cache.put(cache_key, entry)
    for field in ('base', 'mr', 'normal', 'emissive', 'emissive_strength', 'png'):
        out[field] = entry[field]
    out['notes'].append(NOTE)   # instead of _convert_maps' 2016 wording about a missing _g texture
    return out
