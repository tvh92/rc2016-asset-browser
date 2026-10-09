"""Windows GUI and batch runner for the validated PS4 animation extractor.

The decoder remains :mod:`Animation.animation_transforms`; this module only
discovers model/animset pairs, presents them in a Tk interface, and runs the
CLI in isolated staging directories.  It deliberately has no PowerShell or
third-party dependency.

GUI::
    python Animation/rc2016_asset_browser_gui.py

Headless scan (useful for smoke tests and automation)::
    python Animation/rc2016_asset_browser_gui.py --scan "path/to/sources"
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tempfile
from typing import Callable, Iterable, Sequence


def _find_project_root() -> Path:
    """Locate the project folder whose pipeline source the packaged exe drives.

    The pipeline reads files across the whole project (shading toolkit,
    profiles, research evidence), so the exe runs it from the real folder
    rather than bundling a copy.  The exe normally lives in ``<project>/dist``.
    """

    marker = Path("Animation") / "pipeline" / "run.py"
    starts = [Path(os.environ["PS4_EXTRACTOR_PROJECT"])] if os.environ.get("PS4_EXTRACTOR_PROJECT") else []
    starts.append(Path(sys.executable).resolve().parent)
    for start in starts:
        for folder in (start, *start.parents):
            if (folder / marker).is_file():
                return folder.resolve()
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)).resolve()


if getattr(sys, "frozen", False):
    PROJECT_ROOT = _find_project_root()
    PIPELINE_CLI = PROJECT_ROOT / "Animation" / "pipeline" / "run.py"
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    PIPELINE_CLI = Path(__file__).with_name("pipeline") / "run.py"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TRANSFORM_CLI = Path(__file__).with_name("animation_transforms.py")
SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "PS4 Animation Extractor" / "settings.json"
OUTPUT_SCHEMA = "ps4-v15-source-sqt-v1"
IGNORED_DIRECTORY_NAMES = {".git", "__pycache__", ".staging"}
ALWAYS_COLLAPSED_FOLDERS = {"chunk", "bangle_set"}
WINDOWS_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
_TCL_BOOTSTRAP_DLL = None
_TK_BOOTSTRAP_DLL = None


@dataclass(frozen=True)
class ModelCandidate:
    """A source model and its exact same-directory animation-set match."""

    model: Path
    animset: Path | None
    relative_model: str
    relative_animset: str | None
    output_name: str

    @property
    def display_name(self) -> str:
        return self.model.stem


@dataclass(frozen=True)
class PipelineModelCandidate:
    model_id: str
    archive_path: str
    previous_status: str = ""
    # Named actors that use this model (and their animsets), for display and search.
    used_by: str = ""
    search_text: str = ""

    @property
    def display_name(self) -> str:
        return Path(self.archive_path).stem

    @property
    def export_name(self) -> str:
        """Folder name of this model's portable export (model_export.safe_name of the stem)."""
        return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(self.archive_path).stem).strip("._")[:70] or "model"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str, maximum: int = 70) -> str:
    """Return a readable Windows-safe name without relying on source names."""

    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    value = (value or "model")[:maximum]
    if value.split(".", 1)[0].upper() in WINDOWS_DEVICE_NAMES:
        value = "_" + value
    return value


def ensure_disjoint(source_root: Path, output_root: Path) -> None:
    """Keep generated output outside the read-only source tree."""

    source = source_root.expanduser().resolve()
    output = output_root.expanduser().resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Output root and source root must be disjoint directories")


def output_name_for(model: Path, source_root: Path) -> str:
    """Create a stable, collision-resistant output folder name."""

    relative = model.resolve().relative_to(source_root.resolve()).as_posix()
    suffix = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:8]
    return f"{safe_name(model.stem)}_{suffix}"


def _walk_files(root: Path, ignored_roots: Iterable[Path] = ()) -> Iterable[Path]:
    """Yield files deterministically while avoiding generated/cache folders."""

    root = root.resolve()
    ignored = [path.resolve() for path in ignored_roots]
    for folder, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        folder_path = Path(folder).resolve()
        directories[:] = sorted(
            name for name in directories
            if name not in IGNORED_DIRECTORY_NAMES
            and not name.startswith(".")
            and not any((folder_path / name).resolve().is_relative_to(item) for item in ignored)
        )
        for filename in sorted(filenames, key=str.casefold):
            yield folder_path / filename


def discover_models(source_root: Path, output_root: Path | None = None) -> list[ModelCandidate]:
    """Find all model files and pair only an exact same-directory animset.

    Pairing is intentionally conservative.  A similarly named animset in a
    different folder can belong to another actor or variant and is not
    silently inferred.
    """

    source_root = source_root.expanduser().resolve()
    if not source_root.is_dir():
        raise ValueError(f"Source root is not a directory: {source_root}")
    if output_root is not None:
        ensure_disjoint(source_root, output_root)
    ignored = [output_root] if output_root else []
    files = list(_walk_files(source_root, ignored))
    by_exact_path = {path.resolve(): path for path in files}
    models = sorted(
        (path for path in files if path.suffix.casefold() == ".model"),
        key=lambda path: path.relative_to(source_root).as_posix().casefold(),
    )
    records: list[ModelCandidate] = []
    for model in models:
        expected = model.with_suffix(".animset").resolve()
        animset = by_exact_path.get(expected)
        relative_model = model.relative_to(source_root).as_posix()
        relative_animset = animset.relative_to(source_root).as_posix() if animset else None
        records.append(ModelCandidate(
            model=model,
            animset=animset,
            relative_model=relative_model,
            relative_animset=relative_animset,
            output_name=output_name_for(model, source_root),
        ))
    return records


def output_directory(candidate: ModelCandidate, output_root: Path) -> Path:
    return output_root.expanduser().resolve() / candidate.output_name


def _safe_manifest_path(root: Path, relative: str) -> Path | None:
    """Resolve a manifest child and reject path traversal."""

    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def verify_output(
    output: Path,
    model: Path,
    animset: Path,
) -> tuple[bool, str]:
    """Verify a resumable transform output against its current source files."""

    manifest_path = output / "manifest.json"
    if not output.is_dir() or not manifest_path.is_file():
        return False, "manifest is missing"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != OUTPUT_SCHEMA:
            return False, "unsupported manifest schema"
        if manifest.get("animset_sha256") != sha256_file(animset):
            return False, "animset source hash changed"
        if manifest.get("model_sha256") != sha256_file(model):
            return False, "model source hash changed"
        skeleton = manifest.get("skeleton")
        clips = manifest.get("clips")
        if not isinstance(skeleton, list) or not isinstance(clips, list):
            return False, "manifest structure is incomplete"
        for clip in clips:
            if not isinstance(clip, dict):
                return False, "manifest contains an invalid clip entry"
            json_name = clip.get("json")
            raw_name = clip.get("raw_clip")
            if not isinstance(json_name, str) or not isinstance(raw_name, str):
                return False, "clip files are not recorded"
            json_path = _safe_manifest_path(output, json_name)
            raw_path = _safe_manifest_path(output, raw_name)
            if not json_path or not raw_path or not json_path.is_file() or not raw_path.is_file():
                return False, "a clip file is missing"
            json.loads(json_path.read_text(encoding="utf-8"))
            expected_hash = clip.get("raw_sha256")
            if not isinstance(expected_hash, str) or sha256_file(raw_path) != expected_hash:
                return False, "a raw clip hash does not match"
        return True, f"verified {len(clips)} clip(s)"
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return False, f"verification error: {error}"


def status_for(candidate: ModelCandidate, output_root: Path | None) -> tuple[str, str]:
    if candidate.animset is None:
        return "NO ANIMSET", "No exact same-directory .animset was found"
    if output_root is None:
        return "READY", "No output root selected"
    output = output_directory(candidate, output_root)
    verified, reason = verify_output(output, candidate.model, candidate.animset)
    if verified:
        return "VERIFIED", reason
    if output.exists():
        return "STALE", reason
    return "READY", "No output yet"


def build_command(
    python_executable: Path,
    candidate: ModelCandidate,
    staging_output: Path,
    prefixes: Sequence[str] = (),
) -> list[str]:
    if getattr(sys, "frozen", False):
        # A frozen app cannot pass a source .py file to itself.  The same
        # executable exposes a hidden backend mode for the child process.
        command = [
            str(python_executable), "--backend", str(candidate.animset),
            "--model", str(candidate.model), "--out", str(staging_output),
        ]
    else:
        command = [
            str(python_executable), "-u", str(TRANSFORM_CLI), str(candidate.animset),
            "--model", str(candidate.model), "--out", str(staging_output),
        ]
    for prefix in prefixes:
        command.extend(("--prefix", prefix))
    return command


def _promote_staging(staging: Path, final: Path) -> Path | None:
    """Publish a verified directory, retaining any old directory as a backup."""

    final.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if final.exists():
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup = final.with_name(f"{final.name}.stale-{timestamp}")
        counter = 1
        while backup.exists():
            backup = final.with_name(f"{final.name}.stale-{timestamp}-{counter}")
            counter += 1
        final.replace(backup)
    try:
        staging.replace(final)
    except Exception:
        if backup and not final.exists():
            backup.replace(final)
        raise
    return backup


class BatchRunner:
    """Run selected candidates sequentially and report events to a callback."""

    def __init__(
        self,
        candidates: Sequence[ModelCandidate],
        output_root: Path,
        python_executable: Path,
        prefixes: Sequence[str] = (),
        skip_verified: bool = True,
        event: Callable[..., None] | None = None,
    ):
        self.candidates = list(candidates)
        self.output_root = output_root.expanduser().resolve()
        self.python_executable = python_executable.expanduser().resolve()
        self.prefixes = tuple(prefixes)
        self.skip_verified = skip_verified
        self.event = event or (lambda *_args, **_kwargs: None)
        self.cancel_requested = threading.Event()
        self.process: subprocess.Popen[str] | None = None
        self.staging: Path | None = None
        self._cancel_boundary = threading.Lock()
        self._launching = False

    def cancel(self) -> None:
        with self._cancel_boundary:
            self.cancel_requested.set()
            process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _emit(self, kind: str, **values: object) -> None:
        self.event(kind, **values)

    def _staging_path(self, candidate: ModelCandidate, index: int) -> Path:
        stamp = f"{os.getpid()}-{threading.get_ident()}-{index}"
        return self.output_root / ".staging" / f"{candidate.output_name}-{stamp}"

    def run(self) -> dict[str, int | bool]:
        if not self.candidates:
            return {"total": 0, "verified": 0, "completed": 0, "failed": 0, "cancelled": False}
        if not self.python_executable.is_file():
            raise FileNotFoundError(f"Python interpreter was not found: {self.python_executable}")
        if not getattr(sys, "frozen", False) and not TRANSFORM_CLI.is_file():
            raise FileNotFoundError(f"Animation CLI was not found: {TRANSFORM_CLI}")
        self.output_root.mkdir(parents=True, exist_ok=True)
        (self.output_root / ".staging").mkdir(parents=True, exist_ok=True)
        summary = {"total": len(self.candidates), "verified": 0, "completed": 0, "failed": 0, "cancelled": False}
        self._emit("batch_started", total=len(self.candidates))
        for index, candidate in enumerate(self.candidates, start=1):
            # Serialize the cancellation check with process launch. A cancel
            # arriving between the old check and Popen must not start another model.
            with self._cancel_boundary:
                if self.cancel_requested.is_set():
                    summary["cancelled"] = True
                    break
                self._launching = True
            self._emit("model_started", index=index, total=len(self.candidates), candidate=candidate)
            if candidate.animset is None:
                summary["failed"] += 1
                self._emit("model_done", candidate=candidate, status="failed", message="No exact animset match")
                continue
            final = output_directory(candidate, self.output_root)
            if self.skip_verified:
                verified, reason = verify_output(final, candidate.model, candidate.animset)
                if verified:
                    summary["verified"] += 1
                    self._emit("model_done", candidate=candidate, status="verified", message=reason)
                    continue
            staging = self._staging_path(candidate, index)
            self.staging = staging
            if staging.exists():
                raise FileExistsError(f"Staging directory already exists: {staging}")
            command = build_command(self.python_executable, candidate, staging, self.prefixes)
            self._emit("log", level="info", message="Starting: " + subprocess.list2cmdline(command))
            env = os.environ.copy()
            existing_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            try:
                self._run_process(command, env, candidate)
                if self.cancel_requested.is_set():
                    summary["cancelled"] = True
                    self._emit("model_done", candidate=candidate, status="cancelled", message="Cancellation requested")
                    break
                valid, reason = verify_output(staging, candidate.model, candidate.animset)
                if not valid:
                    raise RuntimeError("Staging output failed verification: " + reason)
                backup = _promote_staging(staging, final)
                summary["completed"] += 1
                message = reason + (f"; previous output kept at {backup.name}" if backup else "")
                self._emit("model_done", candidate=candidate, status="completed", message=message)
            except Exception as error:
                if self.cancel_requested.is_set():
                    summary["cancelled"] = True
                    self._emit("model_done", candidate=candidate, status="cancelled", message="Cancellation requested")
                    break
                summary["failed"] += 1
                self._emit("model_done", candidate=candidate, status="failed", message=str(error))
            finally:
                with self._cancel_boundary:
                    self._launching = False
                self.process = None
                self.staging = None
        self._emit("batch_done", summary=summary)
        return summary

    def _run_process(self, command: list[str], env: dict[str, str], candidate: ModelCandidate) -> None:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with self._cancel_boundary:
            if self.cancel_requested.is_set():
                self._launching = False
                return
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
            self.process = process
            self._launching = False
        assert process.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            try:
                for line in process.stdout:  # type: ignore[union-attr]
                    lines.put(line.rstrip("\r\n"))
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_output, name="animation-cli-reader", daemon=True)
        reader.start()
        finished_reading = False
        cancel_sent_at: float | None = None
        while True:
            try:
                line = lines.get(timeout=0.1)
                if line is None:
                    finished_reading = True
                elif line:
                    self._emit("log", level="output", candidate=candidate, message=line)
            except queue.Empty:
                pass
            if self.cancel_requested.is_set() and process.poll() is None:
                if cancel_sent_at is None:
                    cancel_sent_at = time.monotonic()
                    try:
                        process.terminate()
                    except OSError:
                        pass
                elif time.monotonic() - cancel_sent_at > 2.0:
                    try:
                        process.kill()
                    except OSError:
                        pass
            if process.poll() is not None and finished_reading:
                break
        reader.join(timeout=1)
        return_code = process.wait()
        process.stdout.close()
        if return_code != 0:
            raise RuntimeError(f"Extractor exited with code {return_code}")


class PipelineBatchRunner:
    """Launch selected archive-index models through one full-pipeline CLI run."""

    def __init__(self, selectors: Sequence[str], extracted_root: Path, output_root: Path,
                 python_executable: Path, blender_executable: Path,
                 skip_verified: bool = True, event: Callable[..., None] | None = None,
                 skip_previews: bool = False, clip_only: tuple[str, str] | None = None):
        self.selectors = tuple(dict.fromkeys(s.strip() for s in selectors if s.strip()))
        # (archive path, clip name): that model is built in its own run with only this clip.
        self.clip_only = clip_only if clip_only and clip_only[0] in self.selectors else None
        self.extracted_root = extracted_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()
        self.python_executable = python_executable.expanduser().resolve()
        self.blender_executable = blender_executable.expanduser().resolve()
        self.skip_verified = skip_verified
        self.skip_previews = skip_previews
        self.event = event or (lambda *_args, **_kwargs: None)
        self.cancel_requested = threading.Event()
        self.process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self.cancel_requested.set()
        process = self.process
        if process and process.poll() is None:
            if os.name == "nt":
                # Blender is launched by run.py and inherits this pipe. Killing
                # only the CLI leaves the pipe open and this reader blocked.
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                        timeout=2, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    def command(self, selectors: Sequence[str] | None = None, clips: Sequence[str] = ()) -> list[str]:
        command = [str(self.python_executable), "-u", str(PIPELINE_CLI),
                   "--extracted-root", str(self.extracted_root),
                   "--output-root", str(self.output_root),
                   "--blender", str(self.blender_executable)]
        for selector in self.selectors if selectors is None else selectors:
            command.extend(("--model", selector))
        for clip in clips:
            command.extend(("--clip", clip))
        if self.skip_verified:
            command.append("--reuse-cache")
        else:
            command.append("--force")
        if self.skip_previews:
            command.append("--skip-previews")
        return command

    def run(self) -> int:
        if not self.selectors:
            raise ValueError("Select at least one indexed model")
        for path, label in ((self.python_executable, "Python"), (self.blender_executable, "Blender"),
                            (PIPELINE_CLI, "pipeline CLI")):
            if not path.is_file():
                raise FileNotFoundError(f"{label} was not found: {path}")
        _require_source_interpreter(self.python_executable)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.event("batch_started", total=len(self.selectors), selectors=self.selectors)
        # --clip applies to a whole CLI run, so a clip-only model gets a run of its own.
        runs = [(self.selectors, ())]
        if self.clip_only:
            path, label = self.clip_only
            name = label.rpartition(": ")[2]   # viewer labels carry "animset: " with several animsets
            rest = tuple(s for s in self.selectors if s != path)
            runs = ([(rest, ())] if rest else []) + [((path,), ("name:" + name,))]
        self._started = 0
        return_code = 0
        for selectors, clips in runs:
            if self.cancel_requested.is_set():
                break
            return_code = self._run_cli(self.command(selectors, clips)) or return_code
        cancelled = self.cancel_requested.is_set()
        self.event("batch_done", returncode=return_code, cancelled=cancelled)
        if return_code and not cancelled:
            raise RuntimeError(f"Full pipeline exited with code {return_code}")
        return return_code

    def _run_cli(self, command: list[str]) -> int:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.process = process
        assert process.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            try:
                for output_line in process.stdout:
                    lines.put(output_line)
            finally:
                lines.put(None)

        # A Blender descendant can inherit stdout. Keep the UI runner able to
        # observe cancellation even if a broken child retains the pipe handle.
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        output_closed = False
        while not output_closed:
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                if self.cancel_requested.is_set() and process.poll() is not None:
                    break
                continue
            if line is None:
                output_closed = True
                continue
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                self.event("log", level="output", message=line)
                continue
            event = record.get("event")
            if event == "model_started":
                self._started += 1
                self.event("model_started", index=self._started, total=len(self.selectors), model=record.get("model"))
            elif event in {"model_failed", "model_unsupported", "delivered", "reused_verified_delivery"}:
                self.event("model_done", model=record.get("model"), status=event,
                           message=record.get("error", record.get("reason", record.get("final", ""))))
            else:
                self.event("log", level="output", message=line)
        return_code = process.wait()
        self.process = None
        return return_code


class ExportBatchRunner:
    """Export selected archive models to portable files in this process (no Blender unless a
    Blender format is chosen); same events as the other runners."""

    def __init__(self, records: Sequence[PipelineModelCandidate], extracted_root: Path, output_root: Path,
                 options, clip_only: tuple[str, str] | None = None, event: Callable[..., None] | None = None):
        self.records = list(records)
        self.extracted_root = extracted_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()
        self.options = options
        self.clip_only = clip_only   # (model_id, clip label): that model gets only this clip
        self.event = event or (lambda *_args, **_kwargs: None)
        self.cancel_requested = threading.Event()
        self.exporter = None

    def cancel(self) -> None:
        self.cancel_requested.set()
        if self.exporter:
            self.exporter.cancel()

    def run(self) -> dict[str, int | bool]:
        from dataclasses import replace as replace_options
        from Animation.pipeline.Research.AssetBrowserReview.model_export import Cancelled, ModelExporter
        summary = {"total": len(self.records), "exported": 0, "skipped": 0, "failed": 0, "cancelled": False}
        self.event("batch_started", total=len(self.records))
        self.exporter = ModelExporter(self.extracted_root, self.output_root,
                                      log=lambda message: self.event("log", level="output", message=message),
                                      cancelled=self.cancel_requested.is_set)
        for index, record in enumerate(self.records, start=1):
            if self.cancel_requested.is_set():
                summary["cancelled"] = True
                break
            self.event("model_started", index=index, total=len(self.records), model=record.archive_path)
            options = self.options
            if self.clip_only and self.clip_only[0] == record.model_id:
                options = replace_options(options, animations="clips", clips=(self.clip_only[1],))
            try:
                result = self.exporter.export(record.model_id, record.archive_path, options)
                summary[result["status"]] += 1
                for note in result.get("notes", []):
                    self.event("log", level="output", message="  " + note)
                self.event("model_done", model=record.archive_path, status=result["status"],
                           message=f"{result['message']} -> {result['folder']}")
            except Cancelled:
                summary["cancelled"] = True
                self.event("model_done", model=record.archive_path, status="cancelled", message="Cancellation requested")
                break
            except Exception as error:   # one failed model must not stop the batch
                summary["failed"] += 1
                self.event("model_done", model=record.archive_path, status="failed", message=str(error))
        self.event("batch_done", summary=summary)
        return summary


class PipelineIndexScanner:
    """Ask the canonical pipeline to enumerate models from its archive index."""

    def __init__(self, extracted_root: Path, output_root: Path, python_executable: Path,
                 blender_executable: Path):
        self.extracted_root = extracted_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()
        self.python_executable = python_executable.expanduser().resolve()
        self.blender_executable = blender_executable.expanduser().resolve()

    # Pipeline sources whose code shapes the model index; a change forces a real scan.
    STAMP_SOURCES = ("Animation/pipeline/run.py", "Animation/pipeline/archive.py",
                     "Animation/pipeline/index.py", "Animation/pipeline/common.py")
    STAMP_OUTPUTS = ("gui_model_index.json", "asset_index.json", "asset_references.sqlite")

    def _input_stamp(self) -> dict[str, object]:
        """Every file under the extracted root (size, mtime) plus the indexing code's hashes."""
        files = []
        for folder, dirs, names in os.walk(self.extracted_root):
            dirs.sort()
            for name in sorted(names):
                path = Path(folder) / name
                stat = path.stat()
                files.append([path.relative_to(self.extracted_root).as_posix(), stat.st_size, stat.st_mtime_ns])
        code = {name: sha256_file(PROJECT_ROOT / name) for name in self.STAMP_SOURCES if (PROJECT_ROOT / name).is_file()}
        return dict(schema="gui-scan-stamp-v1", extracted_root=str(self.extracted_root),
                    output_root=str(self.output_root), files=files, code=code)

    def _output_stamp(self, reports: Path) -> list[object]:
        stamp = []
        for name in self.STAMP_OUTPUTS:
            path = reports / name
            stat = path.stat() if path.is_file() else None
            stamp.append([name, stat.st_size if stat else None, stat.st_mtime_ns if stat else None])
        return stamp

    def scan(self, reuse: bool = True) -> list[PipelineModelCandidate]:
        """List models through the pipeline CLI.

        With ``reuse``, an unchanged archive (same files, sizes and mtimes), unchanged
        indexing code and untouched index outputs reuse the previous scan's model list
        instead of re-running the ~1.5 s CLI pass.
        """
        _require_source_interpreter(self.python_executable)
        self.output_root.mkdir(parents=True, exist_ok=True)
        reports = self.output_root / "reports"
        index_json = reports / "gui_model_index.json"
        stamp_path = reports / "gui_scan_stamp.json"
        inputs = self._input_stamp()
        try:
            previous_stamp = json.loads(stamp_path.read_text(encoding="utf-8")) if reuse else None
        except (OSError, ValueError):
            previous_stamp = None
        reused = bool(previous_stamp) and previous_stamp.get("inputs") == inputs and \
            previous_stamp.get("outputs") == self._output_stamp(reports)
        if not reused:
            command = [str(self.python_executable), "-u", str(PIPELINE_CLI),
                       "--extracted-root", str(self.extracted_root),
                       "--output-root", str(self.output_root),
                       "--blender", str(self.blender_executable),
                       "--list-models-json", str(index_json)]
            # Run hidden: without this flag a windowless GUI gives the child its own
            # visible console, and closing that window aborts the scan.
            result = subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True,
                                    text=True, encoding="utf-8", errors="replace",
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError((result.stdout + result.stderr).strip() or
                                    f"Archive index scan exited with code {result.returncode}")
        payload = json.loads(index_json.read_text(encoding="utf-8"))
        if not reused:
            try:
                stamp_path.write_text(json.dumps(dict(inputs=inputs, outputs=self._output_stamp(reports))),
                                      encoding="utf-8")
            except OSError:
                pass   # only loses the reuse next time
        self.reused = reused
        previous_path = self.output_root / "reports" / "manifest.json"
        previous = load_json(previous_path).get("models", {}) if previous_path.is_file() else {}
        users = actor_users(self.output_root)
        records = []
        for row in payload.get("models", []):
            actors, animsets = users.get(row["path"], ((), ()))
            records.append(PipelineModelCandidate(
                row["id"], row["path"], str(previous.get(row["id"], {}).get("status", "")),
                used_by=", ".join(actors), search_text=" ".join((*actors, *animsets))))
        return records


def folder_layout(paths: Sequence[str]) -> list[str]:
    """Display folder for each model path in the browser tree.

    Most archive models sit alone in a folder of their own name
    (``.../rock_01/rock_01.model``), which would add a pointless level. A folder
    holding just one model and no subfolders is skipped, so the model is shown
    in its parent.
    """

    models: dict[str, int] = {}
    subfolders: dict[str, set[str]] = {}
    for path in paths:
        folder = path.rpartition("/")[0]
        models[folder] = models.get(folder, 0) + 1
        while folder:
            parent = folder.rpartition("/")[0]
            subfolders.setdefault(parent, set()).add(folder)
            folder = parent
    result = []
    for path in paths:
        folder = path.rpartition("/")[0]
        while folder and models.get(folder, 0) == 1 and not subfolders.get(folder):
            folder = folder.rpartition("/")[0]
        result.append(folder)
    return result


def actor_users(output_root: Path) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Map model path -> (named actors using it, their animsets) from the archive index.

    The index records every path string found in the game's actor files. A model
    can sit inside a numbered helper actor (``rc1/mobys/moby1265.actor``) that a
    named actor references, so one level of parent actors is included. Numbered
    names are kept only when nothing better exists. Returns {} if no index.
    """

    try:
        from Animation.pipeline.index import open_asset_index
        db = open_asset_index(output_root)
        try:
            rows = db.execute("SELECT a.path, r.target_path FROM references_found r "
                              "JOIN assets a ON a.id = r.source WHERE a.type = 'actor'").fetchall()
        finally:
            db.close()
    except Exception:
        return {}
    targets: dict[str, set[str]] = {}
    parents: dict[str, set[str]] = {}
    for actor, target in rows:
        targets.setdefault(actor, set()).add(target)
        if target.endswith(".actor"):
            parents.setdefault(target, set()).add(actor)
    users: dict[str, set[str]] = {}
    for actor, refs in targets.items():
        for target in refs:
            if target.endswith(".model"):
                users.setdefault(target, set()).update({actor, *parents.get(actor, ())})
    numbered = re.compile(r"^(moby|tie|shrub)-?\d+$")
    result = {}
    for model, actors in users.items():
        names = sorted({Path(a).stem for a in actors})
        named = [n for n in names if not numbered.match(n)] or names
        animsets = sorted({Path(t).stem for a in actors for t in targets.get(a, ()) if t.endswith(".animset")})
        result[model] = (tuple(named), tuple(animsets))
    return result


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_prefixes(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in re.split(r"[,;\n]+", value) if item.strip())


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


def _require_source_interpreter(python_executable: Path) -> None:
    """The pipeline CLI is a source script, so the packaged exe cannot run it."""

    if getattr(sys, "frozen", False) and python_executable == Path(sys.executable).resolve():
        raise RuntimeError(
            "No Python installation was found on this PC. Install Python 3 from "
            "python.org, then restart the extractor (or point the Python field at python.exe).")
    if not PIPELINE_CLI.is_file():
        raise RuntimeError(
            f"The extractor's pipeline files were not found at {PIPELINE_CLI}. Keep the exe "
            "inside the project folder (for example in its 'dist' subfolder).")


def _default_blender() -> str:
    candidate = Path("C:/Program Files/Blender Foundation/Blender 5.0/blender.exe")
    return str(candidate) if candidate.is_file() else ""


HELP = {
    "source": (
        "The game folder you unpacked from the PS4 .pkg file.\n\n"
        "It is the folder named after the game ID, for example:\n"
        "...\\Downloads\\Ratchet & Clank (EUR)\\CUSA01073\n\n"
        "Click Browse… and pick that folder. The model list fills in automatically."),
    "output": (
        "Where the finished Blender files and reports are saved.\n\n"
        "Filled in for you next to the game folder. It must be a different "
        "folder from the game folder; you can leave it as is."),
    "python": (
        "A normal Python 3 installation on this PC, used behind the scenes.\n\n"
        "Found automatically; you normally don't need to touch this."),
    "backend": (
        "Full archive pipeline (recommended): builds complete Blender scenes "
        "with model, materials and animations.\n\n"
        "Transform only: the older animation-only extractor."),
    "blender": (
        "Blender 5.0 (blender.exe), used to build the .blend files.\n\n"
        "Found automatically when installed in the default place. Browsing "
        "the model list works without it."),
    "scale": "Make all text and controls bigger or smaller. Remembered for next time.",
    "prefixes": "Transform-only mode: only extract clips whose names start with these words.",
    "skip": "Don't redo models that were already exported with the same options. Leave this on.",
    "scan": "Re-read the game folder and list every model it contains.",
    "extract": (
        "Exports the selected models in the formats chosen under Options… (GLB by default), "
        "with their textures and animations.\n\n"
        "Select rows in the list first (Ctrl-click or Shift-click for several)."),
    "options": "Export formats, what to include, Python/Blender locations and display size.",
    "formats": (
        "GLB/glTF: model, skeleton, materials and animations in one standard file; opens in Blender, "
        "Unity, Unreal, Godot, three.js and most viewers.\n"
        "OBJ: the model and its textures only, in the rest pose (OBJ has no skeleton or animation).\n"
        "FBX, USD and .blend are converted from the glTF by Blender, so they need Blender."),
    "full_pipeline": (
        "Instead of the portable export, run the research pipeline: a validated Blender scene with "
        "the RC PS4 game shader, shared lighting, every animation as an action, and many reports. "
        "Slow and needs Blender; most users want the normal export."),
    "cancel": "Stop the running extraction. Finished models are kept.",
    "filter": (
        "Search by file name, folder, asset ID, or the actor/animset that uses the model "
        "(for example 'hydro harvester' or 'enm_'). Several words must all match.\n\n"
        "Numbered rc1 mobys (moby-1964 etc.) have no name in the game data; where a named "
        "actor uses one, it shows in the 'Used by' column and is searchable."),
    "tree": (
        "Models are grouped by their folder in the game. Click a folder's arrow to open it.\n\n"
        "Selecting a folder selects every model inside it for extraction."),
    "expand": (
        "Opens every folder in the list, or closes them all again.\n\n"
        "Without this, folders open by themselves for short lists and searches. "
        "chunk and bangle folders stay closed in that automatic mode."),
    "state": (
        "READY: not extracted yet\n"
        "EXPORTED: exported to the output folder\n"
        "VERIFIED: already extracted and checked\n"
        "STALE: extracted before, but out of date\n"
        "PREVIOUS: listed in an earlier run's report\n"
        "NO ANIMSET: no animation found for this model"),
}


def windows_prefers_dark() -> bool:
    """True when Windows' 'Choose your app mode' setting is Dark."""

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except (ImportError, OSError):
        return False


TOOLTIP_COLORS = {"background": "#fffbe6", "foreground": "#1f2937"}


def apply_dark_theme(root, ttk) -> None:
    """Dark palette for ttk and classic Tk widgets; call before building widgets."""

    bg, panel, field, fg, muted, accent, border = ("#1e1f22", "#2b2d31", "#26282c", "#e6e6e6",
                                                  "#9aa0a6", "#3d6fb6", "#3c3f44")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=bg, foreground=fg, fieldbackground=field, bordercolor=border,
                    lightcolor=panel, darkcolor=bg, troughcolor=panel, insertcolor=fg,
                    selectbackground=accent, selectforeground="#ffffff")
    for widget in ("TFrame", "TLabel", "TCheckbutton", "TLabelframe", "TPanedwindow"):
        style.configure(widget, background=bg, foreground=fg)
    style.configure("TLabelframe.Label", background=bg, foreground=fg)
    style.configure("TButton", background=panel, foreground=fg, bordercolor=border, focuscolor=accent)
    style.map("TButton", background=[("disabled", bg), ("pressed", accent), ("active", "#383a40")],
              foreground=[("disabled", "#6b6f75")])
    style.map("TCheckbutton", background=[("active", bg)], indicatorbackground=[("selected", accent)])
    # Asset-type selector (segmented Model / Level / Flash UI radio buttons).
    style.configure("Toolbutton", background=panel, foreground=fg, bordercolor=border)
    style.map("Toolbutton", background=[("selected", accent), ("active", "#383a40")],
              foreground=[("selected", "#ffffff")])
    style.configure("TEntry", fieldbackground=field, foreground=fg)
    style.map("TEntry", fieldbackground=[("disabled", bg)], foreground=[("disabled", "#6b6f75")])
    style.configure("TCombobox", fieldbackground=field, background=panel, foreground=fg, arrowcolor=fg)
    style.map("TCombobox", fieldbackground=[("readonly", field), ("disabled", bg)],
              foreground=[("disabled", "#6b6f75")], selectbackground=[("readonly", field)],
              selectforeground=[("readonly", fg)])
    style.configure("TMenubutton", background=panel, foreground=fg, arrowcolor=fg, bordercolor=border, relief="flat")
    style.map("TMenubutton", background=[("disabled", bg), ("pressed", "#383a40"), ("active", "#383a40")],
              foreground=[("disabled", "#6b6f75")], arrowcolor=[("disabled", "#6b6f75")])
    style.configure("TSpinbox", fieldbackground=field, background=panel, foreground=fg, arrowcolor=fg)
    style.configure("TNotebook", background=bg, bordercolor=border)
    style.configure("TNotebook.Tab", background=panel, foreground=muted, bordercolor=border)
    style.map("TNotebook.Tab", background=[("selected", bg)], foreground=[("selected", fg)])
    style.configure("Treeview", background=field, fieldbackground=field, foreground=fg, bordercolor=border)
    style.map("Treeview", background=[("selected", accent)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=panel, foreground=fg, bordercolor=border)
    style.map("Treeview.Heading", background=[("active", "#383a40")])
    style.configure("TScrollbar", background=panel, troughcolor=bg, arrowcolor=muted, bordercolor=border)
    # clam's default hover colour for scrollbars is light grey; keep every state dark.
    style.map("TScrollbar", background=[("disabled", bg), ("pressed", accent), ("active", "#4a4d55")],
              arrowcolor=[("disabled", "#4a4d55"), ("pressed", fg), ("active", fg)])
    style.configure("TProgressbar", background=accent, troughcolor=panel)
    # Classic Tk widgets (log, details text, combobox lists, viewport label).
    for pattern, value in (("*Background", bg), ("*Foreground", fg), ("*Text.Background", field),
                           ("*Text.Foreground", fg), ("*Text.insertBackground", fg),
                           ("*selectBackground", accent), ("*selectForeground", "#ffffff"),
                           ("*TCombobox*Listbox.background", field), ("*TCombobox*Listbox.foreground", fg),
                           ("*TCombobox*Listbox.selectBackground", accent),
                           ("*Menu.background", panel), ("*Menu.foreground", fg),
                           ("*Menu.activeBackground", accent), ("*Menu.activeForeground", "#ffffff"),
                           ("*Menu.disabledForeground", "#6b6f75"), ("*Menu.borderWidth", 1),
                           ("*Menu.relief", "flat")):
        root.option_add(pattern, value)
    root.configure(background=bg)
    TOOLTIP_COLORS.update(background="#2b2d31", foreground="#e6e6e6")
    try:
        # Dark title bar (Windows 10 2004+ / 11).
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except (AttributeError, OSError):
        pass


class _Tooltip:
    """Small hover help box for a Tk widget."""

    def __init__(self, tk, widget, text: str) -> None:
        self.tk, self.widget, self.text = tk, widget, text
        self.window = None
        self.pending = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.winfo_toplevel().bind("<Unmap>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._hide()
        self.pending = self.widget.after(400, self._show)

    def _show(self) -> None:
        self.pending = None
        self.window = window = self.tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        self.tk.Label(window, text=self.text, justify="left", wraplength=440,
                      background=TOOLTIP_COLORS["background"], foreground=TOOLTIP_COLORS["foreground"], relief="solid",
                      borderwidth=1, padx=8, pady=6).pack()
        # Measure the label, then keep the whole box on screen: flip above the
        # widget when there is no room below, and shift left at the right edge.
        window.update_idletasks()
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        if y + height > window.winfo_screenheight():
            y = self.widget.winfo_rooty() - height - 4
        x = max(0, min(x, window.winfo_screenwidth() - width))
        window.wm_geometry(f"+{x}+{max(0, y)}")
        # Leave is not sent when the window moves under a still pointer.
        self.pending = self.widget.after(10000, self._hide)

    def _hide(self, _event=None) -> None:
        if self.pending:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window:
            self.window.destroy()
            self.window = None


UI_SCALES = ("75%", "90%", "100%", "110%", "125%", "150%", "175%", "200%")
_BASE_FONT_SIZES: dict[str, int] = {}


def themed_scrolled_text_class(base, tk, ttk):
    """``ScrolledText`` whose scrollbar is a ttk widget: the classic ``tk.Scrollbar`` ignores the dark palette on Windows."""

    class ThemedScrolledText(base):
        def __init__(self, master=None, **kw):
            super().__init__(master, **kw)
            self.vbar.destroy()
            self.vbar = ttk.Scrollbar(self.frame, command=self.yview)
            # Re-pack the text inside its frame so the scrollbar sits to its right. Pack methods
            # on self are forwarded to the outer frame, so call the text widget's own ones.
            tk.Pack.pack_forget(self)
            self.vbar.pack(side=tk.RIGHT, fill=tk.Y)
            tk.Pack.pack_configure(self, side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.configure(yscrollcommand=self.vbar.set)

    return ThemedScrolledText


def apply_ui_scale(root, ttk, percent: int) -> None:
    """Resize every named Tk font (and list rows) to ``percent`` of its default.

    All widgets use Tk's named fonts, which update live, so the window
    re-lays itself out without a restart.
    """

    from tkinter import font as tkfont

    factor = max(50, min(300, percent)) / 100.0
    for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        try:
            named = tkfont.nametofont(name, root=root)
        except Exception:
            continue
        base = _BASE_FONT_SIZES.setdefault(name, int(named.cget("size")) or -12)
        size = round(base * factor)
        named.configure(size=size if size else (1 if base > 0 else -1))
    # Bold named font for the accent "Extract selected" button; follows the default size.
    default = tkfont.nametofont("TkDefaultFont", root=root)
    try:
        accent_font = tkfont.nametofont("AccentButtonFont", root=root)
        accent_font.configure(family=default.cget("family"), size=default.cget("size"), weight="bold")
    except Exception:
        # Keep the Font object alive: tkinter deletes a named font when its Python object is collected.
        root._accent_button_font = tkfont.Font(root=root, name="AccentButtonFont", family=default.cget("family"),
                                               size=default.cget("size"), weight="bold")
    linespace =tkfont.nametofont("TkDefaultFont", root=root).metrics("linespace")
    ttk.Style(root).configure("Treeview", rowheight=int(linespace * 1.35) + 2)


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


class AnimationExtractorApp:
    """Tkinter presentation layer; all file/process work stays off the UI thread."""

    def __init__(self) -> None:
        _configure_tk_runtime()
        try:
            import tkinter as tk
            from tkinter import filedialog, messagebox, ttk
            from tkinter.scrolledtext import ScrolledText
        except ImportError as error:
            raise RuntimeError(
                "Tkinter is required for the GUI. Run this with a regular Windows Python "
                "installation, not Blender's bundled Python, or select that interpreter "
                "in the GUI once it is running."
            ) from error
        self.tk = tk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.ttk = ttk
        self.ScrolledText = themed_scrolled_text_class(ScrolledText, tk, ttk)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            raise RuntimeError(
                "Tkinter is installed, but its Tcl/Tk runtime could not start. "
                "Repair the regular Windows Python installation or run the headless "
                "--scan mode. Blender's bundled Python does not include Tkinter. "
                f"Details: {error}"
            ) from error
        self.root.title("R&C 2016 Asset Browser and Extractor")
        self.dark = windows_prefers_dark()
        if self.dark:
            apply_dark_theme(self.root, ttk)
        scale = _load_settings().get("ui_scale", "100%")
        self.ui_scale_var = tk.StringVar(value=scale if scale in UI_SCALES else "100%")
        apply_ui_scale(self.root, ttk, int(self.ui_scale_var.get().rstrip("%")))
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.geometry("1420x980")
        self.root.minsize(1180, 900)
        try:
            self.root.state("zoomed")   # open maximised: the 3D view needs the room
        except Exception:
            pass
        self.events: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()
        self.records: list[ModelCandidate | PipelineModelCandidate] = []
        self.filtered_records: list[ModelCandidate | PipelineModelCandidate] = []
        self.runner: BatchRunner | PipelineBatchRunner | None = None
        self.worker: threading.Thread | None = None
        self.item_by_record: dict[str, ModelCandidate | PipelineModelCandidate] = {}
        self.progress_done = 0
        self.progress_total = 0
        self._build_variables()
        self._build_ui()
        self.root.after(100, self._drain_events)
        if self.source_var.get() and self.output_var.get():
            # Remembered from last time: list the models straight away.
            self.root.after(300, self.scan)

    def _build_variables(self) -> None:
        tk = self.tk
        saved = _load_settings()
        remembered = lambda key: saved.get(key, "") if Path(saved.get(key, "") or "\0").exists() else ""
        self.source_var = tk.StringVar(value=remembered("source"))
        self.output_var = tk.StringVar(value=saved.get("output", "") if remembered("source") else "")
        self.interpreter_var = tk.StringVar(value=remembered("python") or str(_default_interpreter()))
        self.blender_var = tk.StringVar(value=remembered("blender") or _default_blender())
        self.backend_var = tk.StringVar(value="Full archive pipeline")
        self.filter_var = tk.StringVar()
        self.folder_mode = "auto"  # "auto", "expanded" or "collapsed"; see toggle_expand_all
        self.state_var = tk.StringVar(value="All")
        self.prefix_var = tk.StringVar()
        self.skip_verified_var = tk.BooleanVar(value=True)
        # Export options (Options… window), remembered in the settings file.
        from Animation.pipeline.Research.AssetBrowserReview.model_export import FORMATS
        chosen = set((saved.get("export_formats") or "glb").split(","))
        self.export_format_vars = {key: tk.BooleanVar(value=key in chosen) for key in FORMATS}
        self.export_textures_var = tk.BooleanVar(value=saved.get("export_textures", "1") == "1")
        self.export_dds_var = tk.BooleanVar(value=saved.get("export_dds", "0") == "1")
        self.export_animations_var = tk.StringVar(value=saved.get("export_animations", "all") if saved.get("export_animations") in ("all", "none") else "all")
        self.export_lod_var = tk.StringVar(value=saved.get("export_lod", "Highest") if saved.get("export_lod") in ("Highest", "Lowest", "All") else "Highest")
        self.full_pipeline_var = tk.BooleanVar(value=False)
        self.export_summary_var = tk.StringVar()
        self.exported_names: set[str] = set()
        self.selection_var = tk.StringVar(value="0 models selected")
        self.progress_var = tk.StringVar(value="Step 1: click Browse… next to 'Game folder' and pick your unpacked game folder (e.g. CUSA01073). Hover any field for help.")

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        sources = ttk.LabelFrame(self.root, text="Sources and output")
        sources.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        sources.columnconfigure(1, weight=1)
        self._path_row(sources, 0, "Game folder (source)", self.source_var, self._browse_source, HELP["source"])
        self._path_row(sources, 1, "Output folder", self.output_var, self._browse_output, HELP["output"])
        option_row = ttk.Frame(sources)
        option_row.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 6))
        options_button = ttk.Button(option_row, text="Options…", command=self.show_options)
        options_button.pack(side="left")
        ttk.Label(option_row, textvariable=self.export_summary_var, foreground="#9aa0a6" if self.dark else "#6b7280"
                  ).pack(side="left", padx=10)
        self._tip(HELP["options"], options_button)
        self._build_options_window()

        actions = ttk.Frame(self.root)
        actions.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        actions.columnconfigure(1, weight=1)
        scan_button = ttk.Button(actions, text="Scan archive", command=self.scan)
        scan_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Label(actions, textvariable=self.progress_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        # Sits in the gap between Scan archive and Extract selected; the 4 px right pad
        # matches Extract's left pad so the bar is centred between the two buttons.
        self.progressbar = ttk.Progressbar(actions, mode="determinate", maximum=1, value=0)
        self.progressbar.grid(row=0, column=1, sticky="new", padx=(0, 4))
        accent_bg, accent_active, accent_disabled = (("#3d6fb6", "#2f5a97", "#8ea6c9") if self.dark
                                                     else ("#2563eb", "#1d4ed8", "#c7d2fe"))
        self.start_button = tk.Button(actions, text="Extract selected", command=self.start_batch, state="disabled",
                                      font="AccentButtonFont", relief="flat", borderwidth=0, padx=18, pady=6,
                                      cursor="hand2", foreground="#ffffff", disabledforeground=accent_disabled,
                                      background=accent_bg, activebackground=accent_active,
                                      activeforeground="#ffffff")
        self.start_button.grid(row=0, column=2, padx=4)
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel_batch, state="disabled")
        self.cancel_button.grid(row=0, column=3, padx=(4, 0))
        self._tip(HELP["scan"], scan_button)
        self._tip(HELP["extract"], self.start_button)
        self._tip(HELP["cancel"], self.cancel_button)

        main = ttk.Panedwindow(self.root, orient="vertical")
        main.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        top = ttk.Frame(main)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        main.add(top, weight=6)

        # Asset type: each type has its own list (left) and viewer (right); see AssetBrowser.show_asset_type.
        type_row = ttk.Frame(top)
        type_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(type_row, text="Asset type").pack(side="left", padx=(0, 8))
        self.asset_type_var = tk.StringVar(value="Model")
        self.asset_type_buttons = {}
        for kind in ("Model", "Level", "Flash UI"):
            button = ttk.Radiobutton(type_row, text=kind, value=kind, variable=self.asset_type_var, style="Toolbutton",
                                     command=lambda: self.browser.show_asset_type(self.asset_type_var.get()))
            button.pack(side="left", padx=(0, 2))
            self.asset_type_buttons[kind] = button

        # Model view: search/filters above the model list | review notebook.
        model_view = ttk.Frame(top)
        model_view.grid(row=1, column=0, sticky="nsew")
        model_view.columnconfigure(0, weight=1)
        model_view.rowconfigure(1, weight=1)

        filters = ttk.Frame(model_view)
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        filters.columnconfigure(1, weight=1)
        filter_label = ttk.Label(filters, text="Search  ⓘ")
        filter_label.grid(row=0, column=0, padx=(0, 8))
        filter_entry = ttk.Entry(filters, textvariable=self.filter_var)
        filter_entry.grid(row=0, column=1, sticky="ew")
        self.filter_var.trace_add("write", lambda *_args: self.on_filter_changed())
        self.expand_button = ttk.Button(filters, text="Expand all", command=self.toggle_expand_all)
        self._tip(HELP["expand"], self.expand_button)
        self.expand_button.grid(row=0, column=2, padx=(8, 0))
        state_label = ttk.Label(filters, text="State")
        state_label.grid(row=0, column=3, padx=(14, 8))
        state = ttk.Combobox(filters, textvariable=self.state_var, state="readonly", values=("All", "READY", "EXPORTED", "VERIFIED", "STALE", "NO ANIMSET", "PREVIOUS"), width=14)
        state.grid(row=0, column=4)
        self._tip(HELP["filter"], filter_label, filter_entry)
        self._tip(HELP["state"], state_label, state)
        state.bind("<<ComboboxSelected>>", lambda _event: self.refresh_table())
        select_button = ttk.Button(filters, text="Select visible", command=self.select_visible)
        select_button.grid(row=0, column=5, padx=(14, 4))
        self._tip(HELP["tree"], select_button)
        ttk.Button(filters, text="Clear selection", command=self.clear_selection).grid(row=0, column=6)

        browser_split = ttk.Panedwindow(model_view, orient="horizontal")
        browser_split.grid(row=1, column=0, sticky="nsew")
        table_frame = ttk.Frame(browser_split)
        browser_split.add(table_frame, weight=3)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("model", "animset", "state", "details"), show="tree headings", selectmode="extended")
        self.table.heading("#0", text="Folder / model")
        self.table.heading("model", text="Used by (actor)")
        self.table.heading("animset", text="Asset ID / animset")
        self.table.heading("state", text="Output state")
        self.table.heading("details", text="Details")
        self.table.column("#0", width=330, minwidth=160, anchor="w")
        self.table.column("model", width=170, anchor="w")
        self.table.column("animset", width=150, anchor="w")
        self.table.column("state", width=90, anchor="center")
        self.table.column("details", width=220, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.update_selection_label())
        self.table.bind("<Double-1>", self._table_double_click)

        from Animation.pipeline.Research.AssetBrowserReview.browser_ui import AssetBrowser
        self.browser = AssetBrowser(self, browser_split, type_host=top, model_view=model_view)
        self.root.after(200, lambda: browser_split.sashpos(0, int(self.root.winfo_width() * 0.53)))
        self.prefix_entry.configure(state="disabled")

        bottom = ttk.Frame(main)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)
        main.add(bottom, weight=1)
        self.log = self.ScrolledText(bottom, height=4, state="disabled", wrap="none", font="TkFixedFont")
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.tag_configure("error", foreground="#f87171" if self.dark else "#b91c1c")
        self.log.tag_configure("success", foreground="#34d399" if self.dark else "#047857")
        self.log.tag_configure("muted", foreground="#9aa0a6" if self.dark else "#6b7280")

        ttk.Label(self.root, textvariable=self.selection_var, anchor="w").grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))

    def _build_options_window(self) -> None:
        """All optional yes/no settings, in a window that is built once and shown by Options…"""
        tk, ttk = self.tk, self.ttk
        from Animation.pipeline.Research.AssetBrowserReview.model_export import FORMATS
        window = self.options_window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("Options")
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_options)
        window.columnconfigure(0, weight=1)
        muted = "#9aa0a6" if self.dark else "#6b7280"

        formats = ttk.LabelFrame(window, text="Export formats  ⓘ", padding=8)
        formats.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        for number, (key, (label, _suffix, needs_blender)) in enumerate(FORMATS.items()):
            box = ttk.Checkbutton(formats, text=label + ("  (needs Blender)" if needs_blender else ""),
                                  variable=self.export_format_vars[key])
            box.grid(row=number % 3, column=number // 3, sticky="w", padx=(0, 18), pady=2)
            self._tip(HELP["formats"], box)

        include = ttk.LabelFrame(window, text="Include", padding=8)
        include.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        ttk.Checkbutton(include, text="Textures (embedded in .glb; PNG files in a textures folder otherwise)",
                        variable=self.export_textures_var).grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(include, text="Also the original game textures (.dds, unchanged)",
                        variable=self.export_dds_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(include, text="Animations").grid(row=2, column=0, sticky="w", pady=(6, 2))
        ttk.Radiobutton(include, text="All", value="all", variable=self.export_animations_var).grid(row=2, column=1, sticky="w", padx=8, pady=(6, 2))
        ttk.Radiobutton(include, text="None", value="none", variable=self.export_animations_var).grid(row=2, column=2, sticky="w", pady=(6, 2))
        ttk.Label(include, text="To export one clip, pick it under the 3D view and tick 'Extract only this clip'.",
                  foreground=muted).grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Label(include, text="Detail level (LOD)").grid(row=4, column=0, sticky="w", pady=(6, 2))
        ttk.Combobox(include, textvariable=self.export_lod_var, values=("Highest", "Lowest", "All"), state="readonly",
                     width=10).grid(row=4, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 2))
        skip = ttk.Checkbutton(include, text="Skip models already exported with these options", variable=self.skip_verified_var)
        skip.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._tip(HELP["skip"], skip)

        programs = ttk.LabelFrame(window, text="Programs", padding=8)
        programs.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        programs.columnconfigure(1, weight=1)
        self._path_row(programs, 0, "Python", self.interpreter_var, self._browse_interpreter, HELP["python"])
        self._path_row(programs, 1, "Blender (optional)", self.blender_var, self._browse_blender, HELP["blender"])

        advanced = ttk.LabelFrame(window, text="Advanced", padding=8)
        advanced.grid(row=3, column=0, sticky="ew", padx=12, pady=6)
        advanced.columnconfigure(1, weight=1)
        full = ttk.Checkbutton(advanced, text="Build the full research Blender scene instead (slow; needs Blender)",
                               variable=self.full_pipeline_var)
        full.grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        self._tip(HELP["full_pipeline"], full)
        backend_label = ttk.Label(advanced, text="Backend  ⓘ")
        backend_label.grid(row=1, column=0, sticky="w", pady=4)
        backend = ttk.Combobox(advanced, textvariable=self.backend_var, state="readonly",
                                values=("Transform only", "Full archive pipeline"), width=28)
        backend.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        backend.bind("<<ComboboxSelected>>", lambda _event: self._backend_changed())
        self._tip(HELP["backend"], backend_label, backend)
        self.prefix_label = ttk.Label(advanced, text="Clip prefixes")
        self.prefix_label.grid(row=2, column=0, sticky="w", pady=4)
        self.prefix_entry = ttk.Entry(advanced, textvariable=self.prefix_var)
        self.prefix_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(advanced, text="transform only; comma-separated", foreground=muted).grid(row=2, column=2, sticky="w")
        self._tip(HELP["prefixes"], self.prefix_label, self.prefix_entry)

        display = ttk.Frame(window, padding=(12, 6))
        display.grid(row=4, column=0, sticky="ew")
        scale_label = ttk.Label(display, text="UI scale  ⓘ")
        scale_label.pack(side="left", padx=(0, 6))
        scale_box = ttk.Combobox(display, textvariable=self.ui_scale_var, values=UI_SCALES, state="readonly", width=6)
        scale_box.pack(side="left")
        scale_box.bind("<<ComboboxSelected>>", lambda _event: self._ui_scale_changed())
        self._tip(HELP["scale"], scale_label, scale_box)
        ttk.Button(display, text="Close", command=self._close_options).pack(side="right")

        for variable in (*self.export_format_vars.values(), self.export_textures_var, self.export_dds_var,
                         self.export_animations_var, self.export_lod_var, self.full_pipeline_var, self.backend_var):
            variable.trace_add("write", lambda *_args: self._options_changed())
        self._options_changed()

    def show_options(self) -> None:
        window = self.options_window
        window.deiconify()
        window.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - window.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + 60
        window.geometry(f"+{x}+{y}")
        window.lift()
        window.focus_set()

    def _close_options(self) -> None:
        self.options_window.withdraw()
        self._remember_settings()

    def _export_formats(self) -> tuple[str, ...]:
        return tuple(key for key, variable in self.export_format_vars.items() if variable.get())

    def _options_changed(self) -> None:
        if self.backend_var.get() != "Full archive pipeline":
            text = "Transform only: animation data from loose files"
        elif self.full_pipeline_var.get():
            text = "Full research Blender scene (needs Blender)"
        else:
            from Animation.pipeline.Research.AssetBrowserReview.model_export import FORMATS
            formats = [FORMATS[key][1] for key in self._export_formats()] or ["no format chosen"]
            parts = [", ".join(formats)]
            parts.append("textures" if self.export_textures_var.get() else "no textures")
            parts.append("all animations" if self.export_animations_var.get() == "all" else "no animations")
            if self.export_lod_var.get() != "Highest":
                parts.append(f"{self.export_lod_var.get()} LOD")
            text = "Export: " + " · ".join(parts)
        self.export_summary_var.set(text)

    def export_options(self):
        from Animation.pipeline.Research.AssetBrowserReview.model_export import ExportOptions
        return ExportOptions(formats=self._export_formats(), textures=self.export_textures_var.get(),
                             source_textures=self.export_dds_var.get(), animations=self.export_animations_var.get(),
                             lod=self.export_lod_var.get(), blender=self.blender_var.get().strip(),
                             skip_existing=self.skip_verified_var.get())

    def _path_row(self, parent, row: int, label: str, variable, command, tooltip: str) -> None:
        ttk = self.ttk
        label_widget = ttk.Label(parent, text=label + "  ⓘ")
        label_widget.grid(row=row, column=0, sticky="w", padx=(6, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        button = ttk.Button(parent, text="Browse…", command=command)
        button.grid(row=row, column=2, padx=(8, 6), pady=4)
        self._tip(tooltip, label_widget, entry, button)

    def _tip(self, text: str, *widgets) -> None:
        for widget in widgets:
            _Tooltip(self.tk, widget, text)

    def _remember_settings(self) -> None:
        _save_settings({"source": self.source_var.get().strip(), "output": self.output_var.get().strip(),
                        "python": self.interpreter_var.get().strip(), "blender": self.blender_var.get().strip(),
                        "ui_scale": self.ui_scale_var.get(), "export_formats": ",".join(self._export_formats()),
                        "export_textures": "1" if self.export_textures_var.get() else "0",
                        "export_dds": "1" if self.export_dds_var.get() else "0",
                        "export_animations": self.export_animations_var.get(), "export_lod": self.export_lod_var.get()})

    def _ui_scale_changed(self) -> None:
        apply_ui_scale(self.root, self.ttk, int(self.ui_scale_var.get().rstrip("%")))
        settings = _load_settings()
        settings["ui_scale"] = self.ui_scale_var.get()
        _save_settings(settings)

    def _browse_source(self) -> None:
        chosen = self.filedialog.askdirectory(title="Choose extracted PS4 source root")
        if chosen:
            self.source_var.set(chosen)
            if not self.output_var.get():
                source = Path(chosen)
                self.output_var.set(str(source.parent / f"{source.name} Animation Output"))
            self.scan()

    def _browse_output(self) -> None:
        chosen = self.filedialog.askdirectory(title="Choose a separate output root")
        if chosen:
            self.output_var.set(chosen)
            self._scan_exported()
            if self.records:
                self.refresh_table()
            self.browser.reload()

    def _browse_interpreter(self) -> None:
        chosen = self.filedialog.askopenfilename(title="Choose Python interpreter", filetypes=(("Python", "python*.exe"), ("All files", "*.*")))
        if chosen:
            self.interpreter_var.set(chosen)

    def _browse_blender(self) -> None:
        chosen = self.filedialog.askopenfilename(title="Choose Blender executable", filetypes=(("Blender", "blender.exe"), ("All files", "*.*")))
        if chosen:
            self.blender_var.set(chosen)

    def _backend_changed(self) -> None:
        self.state_var.set("All")
        self.records = []
        widget_state = "disabled" if self.backend_var.get() == "Full archive pipeline" else "normal"
        self.prefix_entry.configure(state=widget_state)
        self.prefix_label.configure(foreground="#9ca3af" if widget_state == "disabled" else "")
        self.refresh_table()
        self.progress_var.set("Scan the source root for this backend")

    def scan(self) -> None:
        if self.runner or (self.worker and self.worker.is_alive()) or self.browser.review:
            return
        source_text = self.source_var.get().strip()
        if not source_text:
            self.messagebox.showinfo("Source root", "Choose a source root first.")
            return
        source = Path(source_text)
        output = Path(self.output_var.get()) if self.output_var.get().strip() else None
        if self.backend_var.get() == "Full archive pipeline":
            if output is None:
                self.messagebox.showerror("Output root", "Choose a separate output root before indexing the archive.")
                return
            try:
                ensure_disjoint(source, output)
            except ValueError as error:
                self.messagebox.showerror("Output root", str(error))
                return
            python = Path(self.interpreter_var.get().strip())
            blender = Path(self.blender_var.get().strip())
            if not python.is_file():
                self.messagebox.showerror("Python not found", "No Python 3 was found on this PC. Install it from python.org, then restart the extractor.")
                return
            self._set_busy(scanning=True)
            self.progress_var.set("Building pipeline archive index…")
            self._append_log("Indexing archive models through the pipeline", "muted")
            self.worker = threading.Thread(target=self._pipeline_scan_worker,
                                           args=(source, output, python, blender), daemon=True)
            self.worker.start()
            return
        self._set_busy(scanning=True)
        self.progress_var.set("Scanning model files…")
        self._append_log("Scanning " + str(source), "muted")
        self.worker = threading.Thread(target=self._scan_worker, args=(source, output), daemon=True)
        self.worker.start()

    def _scan_worker(self, source: Path, output: Path | None) -> None:
        try:
            records = discover_models(source, output)
            self.events.put(("scan_done", {"records": records}))
        except Exception as error:
            self.events.put(("scan_error", {"message": str(error)}))

    def _pipeline_scan_worker(self, source: Path, output: Path, python: Path, blender: Path) -> None:
        try:
            scanner = PipelineIndexScanner(source, output, python, blender)
            records = scanner.scan()
            self.events.put(("scan_done", {"records": records, "reused": getattr(scanner, "reused", False)}))
        except Exception as error:
            self.events.put(("scan_error", {"message": str(error)}))

    def refresh_table(self) -> None:
        source = Path(self.source_var.get()).expanduser() if self.source_var.get().strip() else None
        output = Path(self.output_var.get()).expanduser() if self.output_var.get().strip() else None
        needle = self.filter_var.get().casefold().strip()
        state_filter = self.state_var.get()
        selected_models = {self._record_key(record) for record in self._selected_records()} if hasattr(self, "table") else set()
        open_folders = {item for item in self._folder_items() if self.table.item(item, "open")} if hasattr(self, "table") else set()
        terms = needle.split()
        self.filtered_records = []
        for record in self.records:
            is_pipeline = isinstance(record, PipelineModelCandidate)
            relative_model = record.archive_path if is_pipeline else record.relative_model
            haystack = (relative_model + (" " + record.model_id + " " + record.search_text if is_pipeline
                        else " " + (record.relative_animset or ""))).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            state, _reason = self._record_status(record, output)
            if state_filter != "All" and state != state_filter:
                continue
            self.filtered_records.append(record)
        for child in self.table.get_children():
            self.table.delete(child)
        self.item_by_record.clear()
        paths = [self._record_path(record) for record in self.filtered_records]
        parents = folder_layout(paths)
        # Open everything for a search or a short list; otherwise keep what was open.
        expand = bool(terms) or len(self.filtered_records) <= 150
        if self.folder_mode != "auto":
            open_folders = set()
        counts: dict[str, int] = {}
        for parent in parents:
            folder = parent
            while folder:
                counts[folder] = counts.get(folder, 0) + 1
                folder = folder.rpartition("/")[0]
        for number, (record, parent) in enumerate(zip(self.filtered_records, parents)):
            state, reason = self._record_status(record, output)
            item = f"model-{number}"
            self.item_by_record[item] = record
            self.table.insert(self._ensure_folder(parent, counts, open_folders, expand), "end", iid=item,
                              text=Path(self._record_path(record)).stem, values=(
                record.used_by if isinstance(record, PipelineModelCandidate) else "",
                record.model_id if isinstance(record, PipelineModelCandidate) else record.relative_animset or "—",
                state,
                reason,
            ))
            if self._record_key(record) in selected_models:
                self.table.selection_add(item)
        self.update_selection_label()
        self.start_button.configure(state="normal" if self.records and self.runner is None and not getattr(self, "scanning", False) and not self.browser.review else "disabled")
        self.progress_var.set(f"{len(self.records)} models found; {len(self.filtered_records)} visible")

    def on_filter_changed(self) -> None:
        # Typing a search returns the tree to automatic folding so matches are visible.
        if self.folder_mode != "auto":
            self.folder_mode = "auto"
            self.expand_button.configure(text="Expand all")
        self.refresh_table()

    def toggle_expand_all(self) -> None:
        # Cycles expanded -> collapsed -> expanded; the first press from auto opens everything.
        self.folder_mode = "collapsed" if self.folder_mode == "expanded" else "expanded"
        self.expand_button.configure(text="Expand all" if self.folder_mode == "collapsed" else "Collapse all")
        self.refresh_table()

    def _ensure_folder(self, folder: str, counts: dict[str, int], open_folders: set[str], expand: bool) -> str:
        if not folder:
            return ""
        item = "dir:" + folder
        if not self.table.exists(item):
            parent = self._ensure_folder(folder.rpartition("/")[0], counts, open_folders, expand)
            name = folder.rpartition('/')[2]
            if self.folder_mode == "expanded":
                is_open = True
            elif self.folder_mode == "collapsed":
                is_open = False
            else:
                # chunk/bangle folders always hold a complete model, so they stay collapsed in auto mode.
                is_open = (expand or item in open_folders) and name.casefold() not in ALWAYS_COLLAPSED_FOLDERS
            self.table.insert(parent, "end", iid=item, text=f"{name}  ({counts.get(folder, 0)})",
                              open=is_open, tags=("folder",))
        return item

    def _folder_items(self, parent: str = "") -> list[str]:
        found = []
        for child in self.table.get_children(parent):
            if child.startswith("dir:"):
                found.append(child)
                found.extend(self._folder_items(child))
        return found

    @staticmethod
    def _record_path(record: ModelCandidate | PipelineModelCandidate) -> str:
        return record.archive_path if isinstance(record, PipelineModelCandidate) else record.relative_model.replace("\\", "/")

    def _selected_records(self) -> list[ModelCandidate | PipelineModelCandidate]:
        """Selected models; a selected folder stands for every model inside it."""

        records, seen = [], set()

        def add(item: str) -> None:
            if item in self.item_by_record:
                if item not in seen:
                    seen.add(item)
                    records.append(self.item_by_record[item])
            else:
                for child in self.table.get_children(item):
                    add(child)

        for item in self.table.selection():
            add(item)
        return records

    def _table_double_click(self, event) -> None:
        # Double-clicking a folder just opens/closes it (Tk's default).
        if self.table.identify_row(event.y) in self.item_by_record:
            self._show_selected_details()

    def select_visible(self) -> None:
        self.table.selection_set(list(self.item_by_record))
        self.update_selection_label()

    def clear_selection(self) -> None:
        self.table.selection_remove(self.table.selection())
        self.update_selection_label()

    def update_selection_label(self) -> None:
        selected = self._selected_records() if hasattr(self, "table") else []
        self.selection_var.set(f"{len(selected)} models selected")
        items = self.table.selection() if hasattr(self, "table") else ()
        if len(items) == 1 and items[0] in self.item_by_record and hasattr(self, "browser"):
            self.browser.select_asset(self.item_by_record[items[0]])

    @staticmethod
    def _record_key(record: ModelCandidate | PipelineModelCandidate):
        return record.model_id if isinstance(record, PipelineModelCandidate) else record.model

    def _record_status(self, record: ModelCandidate | PipelineModelCandidate, output: Path | None):
        return self.record_status(record, output, self.exported_names)

    def _scan_exported(self) -> None:
        """Folder names in the output root that hold a portable export (export.json)."""
        names = set()
        text = self.output_var.get().strip()
        try:
            for entry in os.scandir(text) if text else ():
                if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "export.json")):
                    names.add(entry.name)
        except OSError:
            pass
        self.exported_names = names

    @staticmethod
    def record_status(record: ModelCandidate | PipelineModelCandidate, output: Path | None, exported: set[str] | None = None):
        if isinstance(record, PipelineModelCandidate):
            if exported is not None and record.export_name in exported:
                return "EXPORTED", "Exported to the output folder"
            if record.previous_status:
                return "PREVIOUS", "Prior run recorded; full cache hashes will be rechecked on run"
            return "READY", "Indexed model; no prior result"
        return status_for(record, output)

    def _show_selected_details(self) -> None:
        selected = self._selected_records()
        if not selected:
            return
        record = selected[0]
        output = Path(self.output_var.get()) if self.output_var.get().strip() else None
        state, reason = self._record_status(record, output)
        self.messagebox.showinfo(
            record.display_name,
            (f"Archive model:\n{record.archive_path}\n\nAsset ID: {record.model_id}" if isinstance(record, PipelineModelCandidate)
             else f"Model:\n{record.model}\n\nAnimset:\n{record.animset or 'not found'}") + f"\n\nState: {state}\n{reason}",
        )

    def start_batch(self) -> None:
        if self.runner is not None or (self.worker and self.worker.is_alive()) or self.browser.review:
            return
        selected = self._selected_records()
        if not selected:
            self.messagebox.showinfo("Selection", "Select one or more models first.")
            return
        output_text = self.output_var.get().strip()
        interpreter_text = self.interpreter_var.get().strip()
        if not output_text or not interpreter_text:
            self.messagebox.showerror("Configuration", "Choose an output root and Python interpreter first.")
            return
        output = Path(output_text)
        interpreter = Path(interpreter_text)
        try:
            ensure_disjoint(Path(self.source_var.get()), output)
        except ValueError as error:
            self.messagebox.showerror("Output root", str(error))
            return
        if not interpreter.is_file():
            self.messagebox.showerror("Python interpreter", f"File not found:\n{interpreter}")
            return
        pipeline_mode = self.backend_var.get() == "Full archive pipeline"
        export_mode = pipeline_mode and not self.full_pipeline_var.get()
        if export_mode:
            options = self.export_options()
            if not options.formats:
                self.messagebox.showinfo("Export formats", "Choose at least one export format under Options….")
                self.show_options()
                return
            needs_blender = [key for key in options.formats if key in ("fbx", "usd", "blend")]
            if needs_blender and not Path(options.blender or "\0").is_file():
                self.messagebox.showerror("Blender needed", "FBX, USD and .blend are converted by Blender. Set its "
                                          "location under Options…, or untick those formats.")
                self.show_options()
                return
        clip_only = self.browser.export_clip() if pipeline_mode else None
        if clip_only and not any(getattr(r, "model_id", None) == clip_only[0] for r in selected):
            clip_only = None
        if clip_only:
            self._append_log(f"Only clip '{clip_only[1]}' for the model shown in the 3D view", "muted")
        if not pipeline_mode and any(not isinstance(record, ModelCandidate) or record.animset is None for record in selected):
            self.messagebox.showerror("Selection", "One or more selected models have no exact same-directory animset. Deselect them or choose paired models.")
            return
        self._append_log(f"Batch started: {len(selected)} model(s)", "success")
        self._set_busy(running=True)
        self.progress_done = 0
        self.progress_total = len(selected)
        self.progressbar.configure(maximum=self.progress_total, value=0)
        self.progress_var.set(f"0/{len(selected)} models processed")
        if export_mode:
            self.runner = ExportBatchRunner(
                [record for record in selected if isinstance(record, PipelineModelCandidate)],
                Path(self.source_var.get()), output, options, clip_only,
                event=lambda kind, **values: self.events.put((kind, values)))
        elif pipeline_mode:
            blender = Path(self.blender_var.get().strip())
            if not blender.is_file():
                self.messagebox.showerror("Blender executable", f"File not found:\n{blender}")
                self._set_busy()
                self.runner = None
                return
            selectors = [record.archive_path for record in selected if isinstance(record, PipelineModelCandidate)]
            research_clip = None
            if clip_only:
                path = next(r.archive_path for r in selected if getattr(r, "model_id", None) == clip_only[0])
                research_clip = (path, clip_only[1])
            self.runner = PipelineBatchRunner(
                selectors, Path(self.source_var.get()), output, interpreter, blender,
                skip_verified=self.skip_verified_var.get(),
                event=lambda kind, **values: self.events.put((kind, values)), clip_only=research_clip)
        else:
            self.runner = BatchRunner(
                [record for record in selected if isinstance(record, ModelCandidate)], output, interpreter,
                parse_prefixes(self.prefix_var.get()), self.skip_verified_var.get(),
                event=lambda kind, **values: self.events.put((kind, values)))
        self.worker = threading.Thread(target=self._batch_worker, args=(self.runner,), daemon=True)
        self.worker.start()

    def _batch_worker(self, runner: BatchRunner) -> None:
        try:
            runner.run()
        except Exception as error:
            self.events.put(("batch_error", {"message": str(error)}))

    def cancel_batch(self) -> None:
        if self.runner:
            self._append_log("Cancellation requested…", "muted")
            self.runner.cancel()

    def _close(self) -> None:
        self.browser.cancel_review()
        if self.browser.review or self.runner:
            self._append_log("Closing after the active extractor stops…", "muted")
            if self.runner:
                self.runner.cancel()
            self.root.after(100, self._close_when_idle)
        else:
            self.browser.close()
            self.root.destroy()

    def _close_when_idle(self) -> None:
        if self.runner or self.browser.review:
            self.root.after(100, self._close_when_idle)
        else:
            self.browser.close()
            self.root.destroy()

    def _set_busy(self, scanning: bool = False, running: bool = False) -> None:
        self.scanning = scanning
        # A scan has no known length, so show a moving bar; batches count models.
        if scanning:
            self.progressbar.configure(mode="indeterminate")
            self.progressbar.start(12)
        else:
            self.progressbar.stop()
            self.progressbar.configure(mode="determinate")
        if scanning or running or (hasattr(self, "browser") and self.browser.review):
            self.start_button.configure(state="disabled")
            self.cancel_button.configure(state="normal" if running else "disabled")
        else:
            self.cancel_button.configure(state="disabled")
            self.start_button.configure(state="normal" if self.records else "disabled")

    def _append_log(self, message: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", tag or "")
        # Keep long archive/proof sessions from accumulating unbounded Tk text.
        if int(self.log.index("end-1c").split(".")[0]) > 3000:
            self.log.delete("1.0", "500.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, values = self.events.get_nowait()
                self._handle_event(kind, values)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_event(self, kind: str, values: dict[str, object]) -> None:
        if kind == "scan_done":
            self.records = values["records"]  # type: ignore[assignment]
            self._scan_exported()
            self._set_busy()
            self._remember_settings()
            self.refresh_table()
            if self.backend_var.get() == "Full archive pipeline":
                note = " (archive and index unchanged since the last scan; reused it)" if values.get("reused") else ""
                self._append_log(f"Pipeline index ready: {len(self.records)} model(s){note}", "success")
            else:
                paired = sum(isinstance(record, ModelCandidate) and record.animset is not None for record in self.records)
                self._append_log(f"Scan complete: {len(self.records)} model(s), {paired} exact animset pair(s)", "success")
        elif kind == "scan_error":
            self._set_busy()
            self.progress_var.set("Scan failed")
            self._append_log(str(values["message"]), "error")
            self.messagebox.showerror("Scan failed", str(values["message"]))
        elif kind == "batch_started":
            self.progress_done = 0
            self.progress_total = int(values["total"])
            self.progressbar.configure(maximum=max(1, self.progress_total), value=0)
            self._append_log(f"Running {values['total']} model(s)…", "muted")
        elif kind == "model_started":
            if "candidate" in values:
                candidate = values["candidate"]
                name = candidate.display_name
                path = candidate.relative_model
            else:
                path = str(values.get("model", ""))
                name = Path(path).stem
            self.progress_var.set(f"{values['index']}/{values['total']}: {name}")
            self._append_log(f"[{values['index']}/{values['total']}] {path}", "muted")
        elif kind == "log":
            self._append_log(str(values["message"]), "error" if values.get("level") == "error" else None)
        elif kind == "model_done":
            status = str(values["status"])
            if "candidate" in values:
                candidate = values["candidate"]
                name = candidate.display_name
            else:
                name = Path(str(values.get("model", ""))).stem
            tag = ("success" if status in {"completed", "verified", "delivered", "reused_verified_delivery", "exported", "skipped"}
                   else "error" if status in {"failed", "model_failed"} else "muted")
            self._append_log(f"{name}: {status} — {values['message']}", tag)
            if "candidate" not in values:
                model_path = str(values.get("model", ""))
                self.records = [replace(r, previous_status=status)
                                if isinstance(r, PipelineModelCandidate) and r.archive_path == model_path else r
                                for r in self.records]
                if status in {"exported", "skipped"}:
                    self._scan_exported()
            self.progress_done += 1
            self.progressbar.configure(value=self.progress_done)
            self.refresh_table()
        elif kind == "batch_done":
            if "summary" in values:
                summary = values["summary"]
                failed = bool(summary["failed"])
                message = ", ".join(f"{key} {value}" for key, value in summary.items() if key != "cancelled" and value) or "nothing to do"
                cancelled = bool(summary["cancelled"])
            else:
                failed = bool(values.get("returncode")) and not bool(values.get("cancelled"))
                message = f"exit code {values.get('returncode')}"
                cancelled = bool(values.get("cancelled"))
            self._append_log(f"Batch finished: {message}", "error" if failed else "success")
            self.runner = None
            self._set_busy()
            self.progress_var.set("Batch cancelled" if cancelled else "Batch complete")
            self.browser.reload()
        elif kind == "batch_error":
            self._append_log(str(values["message"]), "error")
            self.runner = None
            self._set_busy()
            self.progress_var.set("Batch failed before completion")
            self.messagebox.showerror("Batch failed", str(values["message"]))

    def run(self) -> None:
        self.root.mainloop()


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


def _configure_tk_runtime() -> None:
    """Point Tk at bundled data and initialize Tcl's library path early.

    Some Python 3.14 Windows builds do not transfer ``TCL_LIBRARY`` into the
    first Tcl interpreter.  Initializing a short-lived interpreter with the
    path set makes the later tkinter-created interpreter inherit the resolved
    Tcl library state.  All failures are left for the normal Tk error message.
    """

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.prefix)))
    if getattr(sys, "frozen", False):
        tcl_library = bundle_root / "lib" / "tcl8.6"
        tk_library = bundle_root / "lib" / "tk8.6"
        tcl_dll = bundle_root / "tcl86t.dll"
        tk_dll = bundle_root / "tk86t.dll"
    else:
        tcl_library = bundle_root / "tcl" / "tcl8.6"
        tk_library = bundle_root / "tcl" / "tk8.6"
        tcl_dll = bundle_root / "DLLs" / "tcl86t.dll"
        tk_dll = bundle_root / "DLLs" / "tk86t.dll"
    # TCL_LIBRARY is not consumed reliably by this Tcl build and a stale
    # inherited value makes Tcl_Init reject an otherwise valid init.tcl.
    os.environ.pop("TCL_LIBRARY", None)
    if tk_library.is_dir():
        os.environ["TK_LIBRARY"] = tk_library.as_posix()
    if not tcl_dll.is_file():
        return
    try:
        import ctypes

        global _TCL_BOOTSTRAP_DLL, _TK_BOOTSTRAP_DLL
        tcl = ctypes.WinDLL(str(tcl_dll))
        # Keep the handle alive until tkinter has created its interpreter.
        _TCL_BOOTSTRAP_DLL = tcl
        # Python 3.14's Tk package index may point at a non-existent ``bin``
        # directory. Loading the bundled Tk DLL first lets Tk_Init resolve the
        # already-loaded module without following that stale relative path.
        _TK_BOOTSTRAP_DLL = ctypes.WinDLL(str(tk_dll))
        tcl.Tcl_FindExecutable.argtypes = [ctypes.c_wchar_p]
        tcl.Tcl_FindExecutable.restype = None
        tcl.Tcl_FindExecutable(None)
        tcl.Tcl_CreateInterp.restype = ctypes.c_void_p
        interpreter = tcl.Tcl_CreateInterp()
        if not interpreter:
            return
        try:
            tcl.Tcl_SetVar.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
            tcl.Tcl_SetVar.restype = ctypes.c_char_p
            tcl.Tcl_SetVar(interpreter, b"tcl_library", tcl_library.as_posix().encode("utf-8"), 0)
            tcl.Tcl_Init.argtypes = [ctypes.c_void_p]
            tcl.Tcl_Init.restype = ctypes.c_int
            tcl_init_rc = int(tcl.Tcl_Init(interpreter))
            if tcl_init_rc == 0:
                # Set this only after Tcl_Init. This Python 3.14 build rejects
                # the variable during its first interpreter initialization,
                # but tkinter needs it when it creates its own interpreter.
                os.environ["TCL_LIBRARY"] = tcl_library.as_posix()
        finally:
            tcl.Tcl_DeleteInterp.argtypes = [ctypes.c_void_p]
            tcl.Tcl_DeleteInterp(interpreter)
    except (AttributeError, OSError, TypeError, ValueError):
        return


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

    from Animation.animation_transforms import main as transform_main

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
            from Animation.pipeline.Research.AssetBrowserReview import animation_preview, gl_shading, texture_preview  # noqa: F401
            from PIL import DdsImagePlugin, Image, PngImagePlugin  # noqa: F401  (Textures tab)
            from Animation.pipeline.Research.AssetBrowserReview import flash_tab, gfx_render  # noqa: F401  (Flash UI tab)
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
        AnimationExtractorApp().run()
    except RuntimeError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
