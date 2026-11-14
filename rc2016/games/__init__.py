"""Supported games and the folder -> game detection (the game seam; see ``base.Game``)."""
from __future__ import annotations

from pathlib import Path

from rc2016.games.base import APP_TITLE, CAPABILITIES, Game, GameUnavailable
from rc2016.games.rc2016 import GAME as RC2016
from rc2016.games.rcra_game import GAME as RCRA

__all__ = ['APP_TITLE', 'CAPABILITIES', 'GAMES', 'Game', 'GameUnavailable', 'RC2016', 'RCRA', 'SOURCE_PICKER_TITLE',
           'by_id', 'detect', 'open_source']

GAMES = (RC2016, RCRA)
# Detection order: the checks that read a few files first; R&C 2016's walk for its archive root last.
_DETECT_ORDER = (RCRA, RC2016)
SOURCE_PICKER_TITLE = 'Choose a game folder (R&C 2016 extracted archive or Rift Apart install)'


def by_id(game_id: str) -> Game:
    for game in GAMES:
        if game.id == game_id:
            return game
    raise KeyError(game_id)


def detect(folder) -> Game:
    """The game a picked folder holds; ValueError naming the supported games when none matches."""
    for game in _DETECT_ORDER:
        if game.detect(folder):
            return game
    names = ' or '.join(game.title for game in GAMES)
    raise ValueError(f'{folder} is not a supported game folder. Pick the folder of {names}: the extracted '
                     'R&C 2016 PS4 archive (the CUSA folder), or the Rift Apart install (the folder with toc, dag and d).')


def open_source(folder, output, log):
    """(game, source) for a game folder. A relative ``output`` is a folder under the game's cache_dir()."""
    game = detect(folder)
    output = Path(output)
    if not output.is_absolute():
        output = game.cache_dir() / output
    return game, game.open_source(folder, output, log)
