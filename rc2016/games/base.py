"""The game seam: one ``Game`` record per supported game.

A game folder picks its game (``rc2016.games.detect``). The app asks the record what the game can
do (``capabilities``), where its caches and default output go, and how to open its archive and
decode its models. Dispatch happens once per source open and once per model decode; the decoders'
inner loops never see the seam.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Callable, Mapping

# What a game may offer. The UI disables a tab or control whose capability the current game lacks.
CAPABILITIES = frozenset({'models', 'textures', 'export', 'animation', 'levels', 'flash', 'pipeline', 'decoded_shading'})
CACHE_ROOT = Path(__file__).resolve().parents[2] / '.cache'
APP_TITLE = 'R&C Asset Browser'
# Hooks a game may supply; each one missing raises NotImplementedError when called.
HOOKS = frozenset({'list_models', 'decode_model', 'material_textures', 'model_texture_list', 'texture_header',
                   'material_rules', 'effect_preview', 'texture_paths', 'load_texture', 'export_textures',
                   'material_maps', 'shadow_only', 'animation_library', 'blender_materials'})


class GameUnavailable(RuntimeError):
    """The folder holds a known game, but this build cannot open it (its reader is missing)."""


def documents_folder() -> Path:
    """The user's Documents folder (the shell's, which may be redirected, e.g. to OneDrive)."""
    if os.name == 'nt':
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:   # CSIDL_PERSONAL
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    return Path.home() / 'Documents'


def _install_roots() -> list[str]:
    roots = [os.environ.get(name, '') for name in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432')]
    roots += [r'C:\Program Files', r'C:\Program Files (x86)']
    return sorted({os.path.normcase(os.path.abspath(root)) for root in roots if root})


def is_installed_game(folder) -> bool:
    """True for a folder inside Program Files or a Steam library: an install never receives output."""
    path = os.path.normcase(os.path.abspath(Path(folder).expanduser()))
    if any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in _install_roots()):
        return True
    parts = [part.casefold() for part in Path(path).parts]
    return any(a == 'steamapps' and b == 'common' for a, b in zip(parts, parts[1:]))


@dataclass(frozen=True)
class Game:
    """One supported game.

    ``detect(folder) -> bool`` says whether a folder the user picked holds this game.
    ``open_source(folder, output, log)`` returns the game's archive object: R&C 2016's
    ``formats.archive.Archive`` or an object with the same duck-typed surface (``entries``,
    ``by_path``, ``resolve``, ``bytes``, ``asset_data``, ``asset_references``, ``root``, ...).
    ``hooks`` holds the optional functions named in ``HOOKS``; see the methods of the same name.
    """
    id: str
    title: str
    detect: Callable[[Path], bool]
    open_source: Callable[..., object]
    capabilities: frozenset = frozenset()
    hooks: Mapping[str, Callable] = field(default_factory=dict)
    # Folder under .cache for this game's caches. '' keeps them directly in .cache (R&C 2016, whose
    # caches predate the seam and keep their place, so no rescan happens); None means the game id.
    cache_name: str | None = None
    output_suffix: str = 'Output'   # default output folder beside a source that is not an install

    def __post_init__(self):
        unknown = set(self.capabilities) - CAPABILITIES
        if unknown:
            raise ValueError(f'{self.id}: unknown capabilities {sorted(unknown)}')
        unknown = set(self.hooks) - HOOKS
        if unknown:
            raise ValueError(f'{self.id}: unknown hooks {sorted(unknown)}')
        object.__setattr__(self, 'capabilities', frozenset(self.capabilities))
        if self.cache_name is None:
            object.__setattr__(self, 'cache_name', self.id)

    def has(self, capability: str) -> bool:
        if capability not in CAPABILITIES:
            raise ValueError(f'Unknown capability: {capability}')
        return capability in self.capabilities

    @property
    def unavailable_tip(self) -> str:
        """Tooltip of a control this game lacks."""
        return f'Not yet available for {self.title}'

    @property
    def window_title(self) -> str:
        return f'{APP_TITLE} \u2014 {self.title}'

    def cache_dir(self) -> Path:
        return CACHE_ROOT / self.cache_name if self.cache_name else CACHE_ROOT

    def default_output(self, source) -> Path:
        """Output root for a newly picked source: beside it, or under Documents for an installed game."""
        source = Path(source)
        if is_installed_game(source):
            return documents_folder() / APP_TITLE / f'{self.title} Output'
        return source.parent / f'{source.name} {self.output_suffix}'

    def _hook(self, name):
        hook = self.hooks.get(name)
        if hook is None:
            raise NotImplementedError(f'{name} is not yet available for {self.title}')
        return hook

    # Hooks -------------------------------------------------------------------------------------
    def list_models(self, folder, output, progress=None):
        """The game's models as ``app.batch.PipelineModelCandidate`` records, read in-process."""
        return self._hook('list_models')(folder, output, progress)

    def decode_model(self, source, model_id, source_path, as_arrays):
        """(decoded, data): the ``formats.native_model.decode_model`` dict schema plus ``packed_sha256``,
        ``codec`` and ``joint_count``, and the model's decompressed bytes."""
        return self._hook('decode_model')(source, model_id, source_path, as_arrays)

    def material_textures(self, source, materials, cache, translations, hd=True):
        return self._hook('material_textures')(source, materials, cache, translations, hd=hd)

    def model_texture_list(self, source, materials):
        return self._hook('model_texture_list')(source, materials)

    def texture_header(self, source, path):
        return self._hook('texture_header')(source, path)

    def material_rules(self, source, materials, translations=None):
        return self._hook('material_rules')(source, materials, translations)

    def effect_preview(self, source, data, source_path=''):
        return self._hook('effect_preview')(source, data, source_path)

    # Textures tab --------------------------------------------------------------------------------
    def texture_paths(self, source):
        """Every listable texture path of the game (placeholders left out), sorted."""
        return self._hook('texture_paths')(source)

    def load_texture(self, source, path, hd=False):
        """``viewer.textures.load_texture``'s dict: the top mip with its GL upload formats."""
        return self._hook('load_texture')(source, path, hd=hd)

    def export_textures(self, source, paths, folder, formats, flip=False, progress=None):
        return self._hook('export_textures')(source, paths, folder, formats, flip, progress)

    # Model export ------------------------------------------------------------------------------
    def material_maps(self, source, material, cache, map_cache=None):
        """``export.models.material_maps``'s dict (name, base, mr, normal, emissive, factors, notes, sources, png)."""
        return self._hook('material_maps')(source, material, cache, map_cache)

    def blender_materials(self, source, materials, folder):
        """Translated Blender materials for an export: ({glTF material name: material dict}, export folder) ->
        (path of a JSON file for rc2016/export/blender_convert.py, {name: reason a material keeps the glTF material}),
        or None when the game has none."""
        hook = self.hooks.get('blender_materials')
        return hook(source, materials, folder) if hook is not None else None

    # Animation -------------------------------------------------------------------------------
    def has_animation_library(self) -> bool:
        """True when the game finds and decodes its own clips (``animation_library``); R&C 2016 instead reaches its
        animsets through the scan's reference index (``viewer.animation.model_animsets``)."""
        return 'animation_library' in self.hooks

    def animation_library(self, source, model_bytes, model_path, clips=True):
        """A ``viewer.animation.AnimationLibrary``-compatible clip library of one model (``clips=False``: the
        skeleton only, for a rig exported without clips)."""
        return self._hook('animation_library')(source, model_bytes, model_path, clips)

    def shadow_only(self, material_path):
        """True for a material the game only draws into shadow maps; False when the game cannot tell."""
        hook = self.hooks.get('shadow_only')
        return bool(hook(material_path)) if hook is not None else False
