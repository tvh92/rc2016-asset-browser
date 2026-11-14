"""Ratchet & Clank: Rift Apart (Steam PC build): detection and registration.

The reader itself lives in ``rc2016.games.rcra``; ``open_source`` imports it only when a Rift Apart
folder is opened, so a build without it still detects the game and says what is missing.
"""
from __future__ import annotations

from pathlib import Path
import struct

from rc2016.common import extended
from rc2016.games.base import Game, GameUnavailable

TOC_MAGIC = 0x34E89035


def detect(folder) -> bool:
    """An install folder: ``toc`` starting with u32 0x34E89035, a ``dag`` file and a ``d/`` folder."""
    folder = Path(folder).expanduser()
    try:
        with open(extended(folder / 'toc'), 'rb') as f:
            head = f.read(4)
    except OSError:
        return False
    return (len(head) == 4 and struct.unpack('<I', head)[0] == TOC_MAGIC
            and Path(extended(folder / 'dag')).is_file() and Path(extended(folder / 'd')).is_dir())


def open_source(folder, output, log):
    try:
        from rc2016.games.rcra.source import RcraSource
    except ModuleNotFoundError as error:
        if error.name not in ('rc2016.games.rcra', 'rc2016.games.rcra.source'):
            raise
        raise GameUnavailable('Rift Apart reader not installed') from error
    return RcraSource(Path(folder).expanduser().resolve(), output, log)


def decode_model(source, model_id, source_path, as_arrays):
    from rc2016.games.rcra.model import decode_for_viewer
    return decode_for_viewer(source, model_id, source_path, as_arrays)


def material_textures(source, materials, cache, translations=None, hd=False):
    from rc2016.games.rcra.material import material_textures as read
    return read(source, materials, cache, translations, hd=hd)


def model_texture_list(source, materials):
    from rc2016.games.rcra.material import model_texture_list as read
    return read(source, materials)


def material_rules(source, materials, translations=None):
    from rc2016.games.rcra.material import material_rules as rules
    return rules(source, materials, translations)


def effect_preview(source, data, source_path=''):
    from rc2016.games.rcra.material import effect_preview as preview
    return preview(source, data, source_path)


def texture_header(source, path):
    from rc2016.games.rcra.texture import texture_header as read
    return read(source, path)


def texture_paths(source):
    from rc2016.games.rcra.texture import archive_textures
    return sorted(row['path'] for row in archive_textures(source))


def load_texture(source, path, hd=False):
    from rc2016.games.rcra.texture import load_texture as load
    return load(source, path, hd=hd)


def export_textures(source, paths, folder, formats, flip=False, progress=None):
    from rc2016.games.rcra.texture import export_textures as export
    return export(source, paths, folder, formats, flip, progress)


def list_models(folder, output, progress=None):
    """Every model in the install as app.batch.PipelineModelCandidate records, sorted by path (read in-process
    from the toc and dag index; ``output`` is not written)."""
    from rc2016.app.batch import PipelineModelCandidate
    source = open_source(folder, GAME.cache_dir() / 'source_preview', lambda *_a, **_k: None)
    try:
        rows = sorted((source.entries[key]['path'], key) for key in source.ids_of_type('model'))
    finally:
        close = getattr(source, 'close', None)
        if close is not None:
            close()
    if progress is not None:
        progress(1.0)
    return [PipelineModelCandidate(model_id=key, archive_path=path) for path, key in rows]


def animation_library(source, model_bytes, model_path, clips=True):
    from rc2016.games.rcra.clip_library import animation_library as build
    return build(source, model_bytes, model_path, clips)


def material_maps(source, material, cache, map_cache=None):
    from rc2016.games.rcra_export import material_maps as maps
    return maps(source, material, cache, map_cache)


# Browse, models, textures, glTF export, and skeletal animation (viewer playback and export).
GAME = Game(id='rcra', title='Rift Apart', detect=detect, open_source=open_source,
            capabilities=frozenset({'models', 'textures', 'export', 'animation'}),
            hooks=dict(decode_model=decode_model, material_textures=material_textures, model_texture_list=model_texture_list,
                       material_rules=material_rules, effect_preview=effect_preview, texture_header=texture_header,
                       texture_paths=texture_paths, load_texture=load_texture, export_textures=export_textures,
                       material_maps=material_maps, list_models=list_models, animation_library=animation_library))
