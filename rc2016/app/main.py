"""Entry point of the R&C 2016 asset browser (window: rc2016.app.window, runners: rc2016.app.batch).

The window hosts the asset browser (rc2016.app.browser). Archive scans and research
Blender builds run the pipeline CLI (rc2016/pipeline/run.py) as a child process; the
loose-file animation backend runs rc2016.formats.animation_transforms in isolated
staging folders. No PowerShell or third-party dependency.

GUI::
    python -m rc2016

Headless loose-file scan (smoke tests and automation)::
    python -m rc2016 --scan "path/to/sources"
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Sequence

if not getattr(sys, "frozen", False):
    # Running this file as a script: make the repository importable before the package imports below.
    _root = str(Path(__file__).resolve().parents[2])
    if _root not in sys.path:
        sys.path.insert(0, _root)

from rc2016.app.batch import (  # noqa: E402,F401  (re-exported: tests and scripts import these names from here)
    BatchRunner, ExportBatchRunner, ModelCandidate, PipelineBatchRunner, PipelineIndexScanner,
    PipelineModelCandidate, actor_users, build_command, discover_models, ensure_disjoint,
    folder_layout, load_json, output_directory, output_name_for, parse_prefixes, safe_name,
    sha256_file, status_for, verify_output)
from rc2016.app.settings import (  # noqa: E402,F401
    PIPELINE_CLI, PROJECT_ROOT, SETTINGS_PATH, TRANSFORM_CLI, _default_blender, _default_interpreter,
    _load_settings, _save_settings)
from rc2016.app.theme import (  # noqa: E402,F401
    _Tooltip, _configure_tk_runtime, apply_dark_theme, apply_ui_scale, themed_scrolled_text_class,
    windows_prefers_dark)
from rc2016.app.window import AssetBrowserApp  # noqa: E402

# Compatibility alias: older scripts construct the window under its previous name.
AnimationExtractorApp = AssetBrowserApp


def scan_json(source_root: Path, output_root: Path | None = None) -> dict[str, object]:
    records = discover_models(source_root, output_root)
    rows = []
    for record in records:
        state, reason = status_for(record, output_root)
        rows.append({
            "model": record.relative_model,
            "animset": record.relative_animset,
            "output": record.output_name,
            "state": state,
            "details": reason,
        })
    return {"source_root": str(source_root.resolve()), "model_count": len(rows), "models": rows}


def _run_embedded_backend(arguments: Sequence[str]) -> int:
    """Run the existing transform CLI inside a frozen child process."""

    # A windowed PyInstaller process can expose ``None`` for stdio when it is
    # launched directly, while BatchRunner normally supplies inherited pipes.
    # Give the backend's final progress print a safe stream in both cases.
    for stream_name, file_descriptor in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, stream_name, None) is not None:
            continue
        try:
            stream = os.fdopen(os.dup(file_descriptor), "w", encoding="utf-8", errors="replace")
        except OSError:
            stream = open(os.devnull, "w", encoding="utf-8")
        setattr(sys, stream_name, stream)

    from rc2016.formats.animation_transforms import main as transform_main

    sys.argv = [str(TRANSFORM_CLI), *arguments]
    result = transform_main()
    return int(result or 0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--backend" in arguments:
        arguments.remove("--backend")
        return _run_embedded_backend(arguments)
    parser.add_argument("--scan", type=Path, help="headless archive scan; prints JSON and does not open Tk")
    parser.add_argument("--output-root", type=Path, help="output root used to calculate resumable states")
    parser.add_argument("--self-check", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(arguments)
    if args.self_check:
        # The windowed exe has no console, so diagnostics go to a file.
        python = _default_interpreter()
        try:   # the 3D preview's decoders (numpy among them) must be inside the exe
            from rc2016.viewer import animation, shading, textures  # noqa: F401
            from PIL import DdsImagePlugin, Image, PngImagePlugin  # noqa: F401  (Textures tab)
            from rc2016.flash import gfx, tab  # noqa: F401  (Flash UI)
            preview = "ok"
        except Exception as error:
            preview = f"{type(error).__name__}: {error}"
        args.self_check.write_text(json.dumps({
            "preview_modules": preview,
            "frozen": bool(getattr(sys, "frozen", False)), "project_root": str(PROJECT_ROOT),
            "pipeline_cli": str(PIPELINE_CLI), "pipeline_found": PIPELINE_CLI.is_file(),
            "python": str(python), "python_is_self": python == Path(sys.executable).resolve(),
            "blender": _default_blender()}, indent=2), encoding="utf-8")
        return 0
    if args.scan:
        print(json.dumps(scan_json(args.scan, args.output_root), indent=2))
        return 0
    try:
        from rc2016.shaders.bindings import use_compile_processes
        use_compile_processes()
        AssetBrowserApp().run()
    except RuntimeError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()   # shader compile workers in the frozen executable
    raise SystemExit(main())