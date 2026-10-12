"""Standalone level viewer window (docs/levels.md, "Level viewer").

Pick a level, load it, and toggle its zones. Everything is read straight from the archive on a
background thread; the view fills in as models, then materials and textures, arrive.

    python -m rc2016.levels.viewer
        [--source-root <extracted source root>] [--level <.level or .zone asset path>] [--zones a,b]
        [--mode Shaded|Textured|Solid|...] [--capture out.png [--camera x,y,z,yaw,pitch]]

--capture loads the level, saves one frame and exits (for proofs). The source root defaults to the
asset browser's saved one; the last level and source root are remembered in
%APPDATA%/RC2016 Asset Browser/level_viewer.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rc2016.common import settings_dir  # noqa: E402
from rc2016.levels.panel import DEFAULT_LEVEL, LevelPanel  # noqa: E402
from rc2016.levels.viewport import LevelViewport  # noqa: E402

SETTINGS_DIR = settings_dir()


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def default_settings():
    """Own settings first, then the asset browser's source root."""
    own = _read_json(SETTINGS_DIR / "level_viewer.json")
    browser = _read_json(SETTINGS_DIR / "settings.json")
    return dict(source=own.get("source") or browser.get("source") or "",
                level=own.get("level") or DEFAULT_LEVEL)


def save_settings(values):
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        (SETTINGS_DIR / "level_viewer.json").write_text(json.dumps(values, indent=2), encoding="utf-8")
    except OSError:
        pass


def main(argv=None):
    settings = default_settings()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source-root", default=settings["source"])
    parser.add_argument("--level", default=None, help="a .level or a single .zone asset path")
    parser.add_argument("--zones", type=lambda s: [z.strip() for z in s.split(",") if z.strip()],
                        help="only these zones (short names or paths)")
    parser.add_argument("--mode", default="Shaded", choices=LevelViewport.MODES)
    parser.add_argument("--capture")
    parser.add_argument("--camera", type=lambda s: tuple(float(x) for x in s.split(",")))
    parser.add_argument("--no-material-overrides", action="store_true",
                        help="draw instances without their per-instance material overrides")
    parser.add_argument("--experimental-spawns", action="store_true",
                        help="approximate enemy factory populations; all encounter states combined")
    parser.add_argument("--experimental-materials", action="store_true",
                        help="enable labelled simplified material previews with unverified runtime inputs")
    parser.add_argument("--no-lighting", action="store_true", help="unlit surface inspection; disable rig/HDRI, AO, shadows and authored lights")
    parser.add_argument("--no-actors", action="store_true", help="hide the source-placement actor layer")
    parser.add_argument("--no-sky", action="store_true", help="draw the flat background instead of the level sky")
    parser.add_argument("--no-native-translation", action="store_true",
                        help="use only hand rules and generic/family material readings")
    args = parser.parse_args(argv)

    import tkinter as tk
    from tkinter import filedialog, ttk
    root = tk.Tk()
    root.title("R&C 2016 Level Viewer")
    root.geometry("1500x920")
    top = ttk.Frame(root, padding=(8, 6)); top.pack(side="top", fill="x")
    ttk.Label(top, text="Source root").pack(side="left")
    source = tk.StringVar(value=args.source_root)
    ttk.Entry(top, textvariable=source).pack(side="left", fill="x", expand=True, padx=4)

    def browse():
        chosen = filedialog.askdirectory(title="Extracted source root (holds the asset archive)", initialdir=source.get())
        if chosen:
            source.set(chosen)
    ttk.Button(top, text="Browse…", command=browse).pack(side="left")

    def finished(panel):
        if args.camera:
            x, y, z, yaw, pitch = args.camera
            panel.view.set_camera((x, y, z), yaw, pitch)
        if args.capture:
            ok = panel.view.capture_png(args.capture)
            print("capture", args.capture, ok, panel.progress.get("done"), panel.view.stats(), flush=True)
            root.destroy()

    panel = LevelPanel(root, tk, ttk, source.get, level=args.level or settings["level"], mode=args.mode,
                       zone_filter=args.zones, on_done=finished if (args.capture or args.camera) else None,
                       material_overrides=not args.no_material_overrides, sky=not args.no_sky,
                       native_translation=not args.no_native_translation, actors=not args.no_actors, spawn_preview=args.experimental_spawns,
                       experimental_materials=args.experimental_materials)
    if args.no_lighting:
        panel.lighting_enabled_var.set(False)
        panel._lighting_enabled_changed()
    panel.frame.pack(side="top", fill="both", expand=True)

    def close():
        if not args.capture:
            save_settings(dict(source=source.get(), level=panel.loaded_level or panel.level_var.get()))
        panel.close()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, panel.list_levels)
    root.after(150, panel.load)
    root.mainloop()


if __name__ == "__main__":
    main()
