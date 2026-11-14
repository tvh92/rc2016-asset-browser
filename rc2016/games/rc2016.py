"""R&C 2016 (PS4): a thin adapter over the existing archive, model and texture code.

Nothing here decodes: it names the functions the app already used before the game seam, so
behaviour, caches (``.cache/shared_index``, ``.cache/source_preview``, ``.cache/texture_preview``, ...)
and their keys are unchanged. The model list comes from the pipeline scan
(``app.batch.PipelineIndexScanner``), so this game has no ``list_models`` hook.
"""
from __future__ import annotations

from pathlib import Path

from rc2016.games.base import CAPABILITIES, Game


def detect(folder) -> bool:
    """A folder with exactly one extracted archive root (layout.csv, toc, dag, chunkmap.txt, segments)."""
    from rc2016.formats.archive import locate
    try:
        locate(Path(folder).expanduser().resolve())
    except (ValueError, OSError):
        return False
    return True


def open_source(folder, output, log):
    from rc2016.formats.archive import Archive, locate
    return Archive(locate(Path(folder).expanduser().resolve()), output, log)


def decode_model(archive, model_id, source_path, as_arrays):
    from rc2016.formats.archive import decompress
    from rc2016.formats.container import section_table
    from rc2016.formats.native_model import decode_model as decode
    packed, packed_hash = archive.bytes(model_id)
    data, codec = decompress(packed)
    decoded = decode(source_path, lod='All', data=data, as_arrays=as_arrays)
    sections = section_table(data, 0)
    joint_count = next((s['size'] // 16 for s in sections if s['label_hash'] == '0x15DF9D3B'), 0)
    decoded.update(packed_sha256=packed_hash, codec=codec, joint_count=joint_count)
    return decoded, data


def material_textures(archive, materials, cache, translations, hd=True):
    from rc2016.viewer.textures import material_textures as read
    return read(archive, materials, cache, translations, hd=hd)


def model_texture_list(archive, materials):
    from rc2016.viewer.textures import model_texture_list as read
    return read(archive, materials)


def texture_header(archive, path):
    from rc2016.viewer.textures import texture_header as read
    return read(archive, path)


def material_rules(archive, materials, translations=None):
    from rc2016.viewer.loader import material_rules as rules
    return rules(archive, materials, translations)


def effect_preview(archive, data, source_path=''):
    from rc2016.viewer.loader import _effect_preview
    return _effect_preview(archive, data, source_path)


def texture_paths(archive):
    from rc2016.viewer.textures import is_placeholder_texture
    return sorted({e['path'] for e in archive.entries.values()
                   if e['type'] == 'texture' and e['built_path'].lower().endswith('.built') and not is_placeholder_texture(e['path'])})


def load_texture(archive, path, hd=False):
    from rc2016.viewer.textures import load_texture as load
    return load(archive, path, hd=True) if hd else load(archive, path)   # the call the Textures tab made before


def export_textures(archive, paths, folder, formats, flip=False, progress=None):
    from rc2016.viewer.textures import export_textures as export
    return export(archive, paths, folder, formats, flip, progress)


def material_maps(archive, material, cache, map_cache=None):
    from rc2016.export.models import material_maps as maps
    return maps(archive, material, cache, map_cache)


def shadow_only(material_path):
    from rc2016.pipeline.render_state import shadow_only as census
    return census(material_path)


GAME = Game(
    id='rc2016', title='R&C 2016', detect=detect, open_source=open_source, capabilities=CAPABILITIES,
    hooks=dict(decode_model=decode_model, material_textures=material_textures, model_texture_list=model_texture_list,
               texture_header=texture_header, material_rules=material_rules, effect_preview=effect_preview,
               texture_paths=texture_paths, load_texture=load_texture, export_textures=export_textures,
               material_maps=material_maps, shadow_only=shadow_only),
    cache_name='', output_suffix='Animation Output')
