"""Settings file, project paths and Python/Blender discovery for the app."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def _find_project_root() -> Path:
    """Locate the project folder whose pipeline source the packaged exe drives.

    The pipeline reads files across the repository (assets, profiles, shaders),
    so the exe runs it from a checkout rather than bundling a copy.  The exe
    normally lives in ``<repository>/dist``; RC2016_BROWSER_ROOT overrides.
    """

    marker = Path("rc2016") / "pipeline" / "run.py"
    starts = [Path(os.environ["RC2016_BROWSER_ROOT"])] if os.environ.get("RC2016_BROWSER_ROOT") else []
    starts.append(Path(sys.executable).resolve().parent)
    for start in starts:
        for folder in (start, *start.parents):
            if (folder / marker).is_file():
                return folder.resolve()
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)).resolve()


if getattr(sys, "frozen", False):
    PROJECT_ROOT = _find_project_root()
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_CLI = PROJECT_ROOT / "rc2016" / "pipeline" / "run.py"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TRANSFORM_CLI = PROJECT_ROOT / "rc2016" / "formats" / "animation_transforms.py"
from rc2016.common import settings_dir  # noqa: E402  (needs PROJECT_ROOT on sys.path)
SETTINGS_PATH = settings_dir() / "settings.json"


def _load_settings() -> dict[str, str]:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return {key: str(value) for key, value in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(values: dict[str, str]) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")
    except OSError:
        pass

def _find_installed_python() -> Path | None:
    """Find a regular Windows Python 3, newest first; skips the Store alias."""

    installs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    found = []
    for candidate in installs.glob("Python3*/python.exe"):
        match = re.fullmatch(r"Python3(\d+)", candidate.parent.name)
        if match:
            found.append((int(match.group(1)), candidate))
    if found:
        return max(found)[1].resolve()
    launcher = shutil.which("py")
    if launcher:
        try:
            result = subprocess.run([launcher, "-3", "-c", "import sys; print(sys.executable)"],
                                    capture_output=True, text=True, timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            path = Path(result.stdout.strip())
            if result.returncode == 0 and path.is_file():
                return path.resolve()
        except (OSError, subprocess.TimeoutExpired):
            pass
    python = shutil.which("python")
    if python and "WindowsApps" not in python:
        return Path(python).resolve()
    return None


def _default_interpreter() -> Path:
    """Prefer the current interpreter; the GUI can override it in the form."""

    if getattr(sys, "frozen", False):
        return _find_installed_python() or Path(sys.executable).resolve()
    return Path(sys.executable).resolve()


def _default_blender() -> str:
    candidate = Path("C:/Program Files/Blender Foundation/Blender 5.0/blender.exe")
    return str(candidate) if candidate.is_file() else ""
