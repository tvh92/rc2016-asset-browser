"""Model discovery, output verification and the background batch runners (scan, export, pipeline)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Callable, Iterable, Sequence

from rc2016.app.settings import PIPELINE_CLI, PROJECT_ROOT, TRANSFORM_CLI


OUTPUT_SCHEMA = "ps4-v15-source-sqt-v1"
IGNORED_DIRECTORY_NAMES = {".git", "__pycache__", ".staging"}
WINDOWS_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}



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
                 blend_only: bool = False, clip_only: tuple[str, str] | None = None, group_blend: str = ""):
        self.selectors = tuple(dict.fromkeys(s.strip() for s in selectors if s.strip()))
        # (archive path, clip name): that model is built in its own run with only this clip.
        self.clip_only = clip_only if clip_only and clip_only[0] in self.selectors else None
        self.extracted_root = extracted_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()
        self.python_executable = python_executable.expanduser().resolve()
        self.blender_executable = blender_executable.expanduser().resolve()
        self.skip_verified = skip_verified
        self.blend_only = blend_only
        self.group_blend = group_blend.strip()
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
        if clips:   # the viewer can offer a cutscene shot, which only the cutscene scan lists
            command.append("--cinematic-clips")
        # Blend-only output keeps no receipts, so every run rebuilds.
        if self.skip_verified and not self.blend_only:
            command.append("--reuse-cache")
        else:
            command.append("--force")
        if self.blend_only:
            command.append("--blend-only")
        if self.group_blend:
            command.extend(("--group-blend", self.group_blend))
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
        # Closing while the reader is still inside a read would block on the
        # buffer lock, so close only once the reader has seen end of output.
        reader.join(timeout=2)
        if not reader.is_alive():
            process.stdout.close()
        self.process = None
        return return_code


class ExportBatchRunner:
    """Export selected archive models to portable files in this process (no Blender unless a
    Blender format is chosen); same events as the other runners."""

    def __init__(self, records: Sequence[PipelineModelCandidate], extracted_root: Path, output_root: Path,
                 options, clip_only: tuple[str, str] | None = None, event: Callable[..., None] | None = None,
                 combine_name: str = "", clip_choices: dict | None = None):
        self.records = list(records)
        # model_id -> the clips ticked for it; a model not listed exports the clips of ``options`` as they are.
        self.clip_choices = dict(clip_choices or {})
        self.combine_name = combine_name.strip()   # also write the exported models as one file under groups/
        self.extracted_root = extracted_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()
        self.options = options
        self.clip_only = clip_only   # (model_id, clip label): that model gets only this clip
        self.event = event or (lambda *_args, **_kwargs: None)
        self.cancel_requested = threading.Event()
        self.exporter = None

    def cancel(self) -> None:
        # Only the models still queued are stopped: the one running now is left to finish and is recorded.
        self.cancel_requested.set()

    def run(self) -> dict[str, int | bool]:
        from dataclasses import replace as replace_options
        from rc2016.export.models import Cancelled, ModelExporter
        summary = {"total": len(self.records), "exported": 0, "skipped": 0, "failed": 0, "cancelled": False}
        self.event("batch_started", total=len(self.records))
        parts = []   # glb files of the models that exported or were already there
        self.exporter = ModelExporter(self.extracted_root, self.output_root,
                                      log=lambda message: self.event("log", level="output", message=message))
        for index, record in enumerate(self.records, start=1):
            if self.cancel_requested.is_set():
                summary["cancelled"] = True
                break
            self.event("model_started", index=index, total=len(self.records), model=record.archive_path)
            options = self.options
            if self.clip_only and self.clip_only[0] == record.model_id:
                # The viewer may have offered a real-time cutscene shot, which only exists in the cutscene scan.
                options = replace_options(options, animations="clips", clips=(self.clip_only[1],), cinematic=True)
            elif record.model_id in self.clip_choices:
                chosen = self.clip_choices[record.model_id]
                options = replace_options(options, animations="clips", clips=tuple(chosen)) if chosen \
                    else replace_options(options, animations="none")
            try:
                result = self.exporter.export(record.model_id, record.archive_path, options)
                summary[result["status"]] += 1
                if result["status"] in ("exported", "skipped") and self.combine_name and "glb" in self.options.formats:
                    stem = safe_name(Path(record.archive_path).stem) + self.options.file_suffix
                    parts.append(Path(result["folder"]) / f"{stem}.glb")
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
        if parts and not summary["cancelled"]:
            summary["combined"] = str(self._combine(parts))
        self.event("batch_done", summary=summary)
        return summary

    def _combine(self, parts):
        """One glTF of every exported model (their own nodes, skins and animations), then FBX/USD from it
        through Blender when those are chosen."""
        from rc2016.export.merge_glb import merge_glb, read_glb
        from rc2016.export.models import FORMATS
        groups = self.output_root / "groups"
        groups.mkdir(parents=True, exist_ok=True)
        name = safe_name(self.combine_name)
        target = groups / f"{name}.glb"
        merge_glb(parts, target)
        self.event("log", level="output", message=f"Combined {len(parts)} model(s) into {target}")
        blender_formats = [f for f in self.options.formats if FORMATS[f][2] and f != 'glb']
        if blender_formats and self.options.blender:
            document, blob = read_glb(target)
            self.exporter._blender_convert(groups, name, document, blob, blender_formats, self.options.blender)
        return target


class PipelineIndexScanner:
    """Ask the canonical pipeline to enumerate models from its archive index."""

    def __init__(self, extracted_root: Path, output_root: Path, python_executable: Path,
                 blender_executable: Path):
        self.extracted_root = extracted_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()
        self.python_executable = python_executable.expanduser().resolve()
        self.blender_executable = blender_executable.expanduser().resolve()

    # Pipeline sources whose code shapes the model index; a change forces a real scan.
    STAMP_SOURCES = ("rc2016/pipeline/run.py", "rc2016/formats/archive.py",
                     "rc2016/formats/index.py", "rc2016/common.py")
    STAMP_OUTPUTS = ("gui_model_index.json", "asset_index.json", "asset_references.sqlite")

    def _input_stamp(self, walked: Callable[[int], None] | None = None) -> dict[str, object]:
        """Every file under the extracted root (size, mtime) plus the indexing code's hashes.

        ``walked(count)`` is called every few hundred files."""
        files = []
        for folder, dirs, names in os.walk(self.extracted_root):
            dirs.sort()
            for name in sorted(names):
                path = Path(folder) / name
                stat = path.stat()
                files.append([path.relative_to(self.extracted_root).as_posix(), stat.st_size, stat.st_mtime_ns])
                if walked and len(files) % 500 == 0:
                    walked(len(files))
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

    # Share of the progress bar: the file walk, then the CLI (up to its last event), then reading its result.
    WALK_SHARE, CLI_START, CLI_END = 0.15, 0.2, 0.92

    def scan(self, reuse: bool = True, progress: Callable[[float], None] | None = None) -> list[PipelineModelCandidate]:
        """List models through the pipeline CLI.

        With ``reuse``, an unchanged archive (same files, sizes and mtimes), unchanged
        indexing code and untouched index outputs reuse the previous scan's model list
        instead of re-running the ~1.5 s CLI pass. ``progress(fraction)`` (0 to 1, only
        rising) reports the walk of the archive files and the CLI's own progress events.
        """
        report = progress or (lambda _fraction: None)
        _require_source_interpreter(self.python_executable)
        self.output_root.mkdir(parents=True, exist_ok=True)
        reports = self.output_root / "reports"
        index_json = reports / "gui_model_index.json"
        stamp_path = reports / "gui_scan_stamp.json"
        try:
            previous_stamp = json.loads(stamp_path.read_text(encoding="utf-8")) if reuse else None
        except (OSError, ValueError):
            previous_stamp = None
        # The previous walk's file count is the best estimate of this one's length.
        expected = len((previous_stamp or {}).get("inputs", {}).get("files", ())) or 0
        report(0.0)
        inputs = self._input_stamp(lambda count: report(self.WALK_SHARE * min(1.0, count / expected) if expected
                                                         else self.WALK_SHARE * (1 - 1 / (1 + count / 20000))))
        report(self.WALK_SHARE)
        reused = bool(previous_stamp) and previous_stamp.get("inputs") == inputs and \
            previous_stamp.get("outputs") == self._output_stamp(reports)
        if reused:
            # The reference index also names a shared asset table outside this folder; when that is gone or
            # changed, animation sets cannot be found, so the scan runs again and rewrites it.
            from rc2016.formats.index import open_asset_index
            try:
                open_asset_index(self.output_root).close()
            except Exception:
                reused = False
        if not reused:
            command = [str(self.python_executable), "-u", str(PIPELINE_CLI),
                       "--extracted-root", str(self.extracted_root),
                       "--output-root", str(self.output_root),
                       "--blender", str(self.blender_executable),
                       "--list-models-json", str(index_json)]
            report(self.CLI_START)
            # Run hidden: without this flag a windowless GUI gives the child its own
            # visible console, and closing that window aborts the scan.
            process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            tail: list[str] = []
            for line in process.stdout:
                tail = (tail + [line])[-200:]
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") == "actor_reference_index" and event.get("total"):
                    report(self.CLI_START + (self.CLI_END - self.CLI_START) * min(1.0, event["completed"] / event["total"]))
            returncode = process.wait()
            if returncode:
                raise RuntimeError("".join(tail).strip() or f"Archive index scan exited with code {returncode}")
            report(self.CLI_END)
        payload = json.loads(index_json.read_text(encoding="utf-8"))
        if not reused:
            try:
                stamp_path.write_text(json.dumps(dict(inputs=inputs, outputs=self._output_stamp(reports))),
                                      encoding="utf-8")
            except OSError:
                pass   # only loses the reuse next time
        self.reused = reused
        report(0.95)
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
        from rc2016.formats.index import open_asset_index
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

