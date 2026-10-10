"""Per-weapon source-linked V1/V5 assemblies and presentation fixes."""

import os
from pathlib import Path


def research_file(relative):
    """Resolve a hash-pinned research-workspace file under RC2016_RESEARCH."""
    base = os.environ.get('RC2016_RESEARCH')
    if not base:
        raise RuntimeError(f'RC2016_RESEARCH must point to the research workspace to load {relative}')
    path = Path(base) / relative
    if not path.is_file():
        raise FileNotFoundError(f'Pinned research file is missing under RC2016_RESEARCH: {path}')
    return path
