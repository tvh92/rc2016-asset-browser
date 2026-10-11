"""Model thumbnails: cache index, PNG writer, offscreen renderer and the batch CLI.

The renderer runs as its own process so the browser's Tk thread and 3D view never block and
a batch can be cancelled::

    python -m rc2016.app.thumbnails --source <game root> --output <output root> [--ids-file F | --all]

Thumbnails are written to ``<output>/.cache/thumbnails/<model id>.png`` and described by
``index.json`` in the same folder. One JSON line per finished model goes to stdout.
The pure parts (index, PNG, downsampling, argument handling) import without Tk or OpenGL.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import zlib

THUMBNAIL_VERSION = 2      # bump when rendering changes: invalidates every cached thumbnail
DEFAULT_SIZE = 192
SUPERSAMPLE = 2            # render at 2x and box-filter down: the legacy pipeline has no MSAA
INDEX_NAME = 'index.json'
STATUSES = ('ok', 'unsupported', 'error')


# Cache layout and index ------------------------------------------------------
def cache_dir(output_root) -> Path:
    """Folder that holds the thumbnails of one output root (never the source folders or the repository)."""
    return Path(output_root) / '.cache' / 'thumbnails'


def safe_name(model_id) -> str:
    """File stem for a model id: ids are hex, anything else is reduced to a safe form."""
    return re.sub(r'[^A-Za-z0-9_-]+', '_', str(model_id)).strip('_') or 'model'


def write_atomic(path: Path, data: bytes) -> None:
    """Write beside the target, then replace it: readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


class ThumbnailIndex:
    """``index.json``: model id -> {source_hash, renderer_version, size, status, reason}."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.models: dict[str, dict] = {}
        self.dirty = False
        self.last_saved = time.monotonic()

    @property
    def path(self) -> Path:
        return self.folder / INDEX_NAME

    @classmethod
    def load(cls, folder) -> 'ThumbnailIndex':
        index = cls(folder)
        try:
            document = json.loads(index.path.read_text(encoding='utf8'))
            models = document.get('models')
            if isinstance(models, dict):
                index.models = {str(k): v for k, v in models.items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            pass   # missing or damaged index: everything is simply rendered again
        return index

    def png_path(self, model_id) -> Path:
        return self.folder / f'{safe_name(model_id)}.png'

    def get(self, model_id):
        return self.models.get(model_id)

    def is_current(self, model_id, size, source_hash=None) -> bool:
        """True when the recorded outcome is still valid for this renderer version, size and source.

        ``source_hash`` None skips the source comparison (the browser does not hash archive bytes).
        A recorded success also needs its PNG on disk."""
        entry = self.models.get(model_id)
        if not entry or entry.get('renderer_version') != THUMBNAIL_VERSION or entry.get('size') != size:
            return False
        if source_hash is not None and entry.get('source_hash') != source_hash:
            return False
        if entry.get('status') == 'ok':
            return self.png_path(model_id).is_file()
        return entry.get('status') in STATUSES

    def record(self, model_id, status, source_hash=None, size=DEFAULT_SIZE, reason='', **extra) -> dict:
        if status not in STATUSES:
            raise ValueError(f'Unknown thumbnail status: {status}')
        entry = dict(source_hash=source_hash, renderer_version=THUMBNAIL_VERSION, size=size,
                     status=status, reason=str(reason)[:500], **extra)
        self.models[model_id] = entry
        self.dirty = True
        return entry

    def write_png(self, model_id, data: bytes) -> Path:
        path = self.png_path(model_id)
        write_atomic(path, data)
        return path

    def save(self, force=False) -> None:
        if not (self.dirty or force):
            return
        document = dict(version=THUMBNAIL_VERSION, models=self.models)
        write_atomic(self.path, json.dumps(document, separators=(',', ':')).encode('utf8'))
        self.dirty = False
        self.last_saved = time.monotonic()

    def save_if_due(self, seconds=3.0) -> None:
        if self.dirty and time.monotonic() - self.last_saved >= seconds:
            self.save()


def missing_ids(index: ThumbnailIndex, model_ids, size=DEFAULT_SIZE):
    """Ids (in the given order) that have no valid cached outcome yet."""
    return [m for m in model_ids if not index.is_current(m, size)]


def read_ids_file(path) -> list[str]:
    """Model ids in priority order: one per line, blanks and ``#`` comments ignored, duplicates dropped."""
    seen, ids = set(), []
    for line in Path(path).read_text(encoding='utf8').splitlines():
        text = line.strip()
        if text and not text.startswith('#') and text not in seen:
            seen.add(text)
            ids.append(text)
    return ids


# PNG and downsampling --------------------------------------------------------
def encode_png_rgba(width, height, rgba_rows) -> bytes:
    """RGBA PNG from top-down 8-bit RGBA rows."""
    if len(rgba_rows) != width * height * 4:
        raise ValueError('RGBA buffer does not match the image size')
    stride = width * 4
    raw = b''.join(b'\x00' + rgba_rows[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def downsample_rgba(rgba_bottom_up, width, height, factor):
    """Box-filter a glReadPixels RGBA buffer (bottom row first) down by ``factor``.

    Colour is averaged weighted by alpha, so transparent background never darkens the edge.
    Returns (width // factor, height // factor, top-down RGBA bytes)."""
    if factor < 1 or width % factor or height % factor:
        raise ValueError('Downsample factor must divide the image size')
    out_w, out_h = width // factor, height // factor
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        image = np.frombuffer(rgba_bottom_up, np.uint8).reshape(height, width, 4)[::-1].astype(np.float32)
        weight = image[..., 3:4]
        premultiplied = np.concatenate([image[..., :3] * weight, weight], axis=2)
        blocks = premultiplied.reshape(out_h, factor, out_w, factor, 4).sum(axis=(1, 3))
        alpha_sum = blocks[..., 3:4]
        rgb = np.where(alpha_sum > 0, blocks[..., :3] / np.maximum(alpha_sum, 1e-6), 0.0)
        alpha = alpha_sum / (factor * factor)
        result = np.concatenate([rgb, alpha], axis=2)
        return out_w, out_h, np.clip(np.rint(result), 0, 255).astype(np.uint8).tobytes()
    out = bytearray(out_w * out_h * 4)
    stride, area = width * 4, factor * factor
    for y in range(out_h):
        for x in range(out_w):
            r = g = b = a = 0
            for dy in range(factor):
                row = (height - 1 - (y * factor + dy)) * stride
                for dx in range(factor):
                    i = row + (x * factor + dx) * 4
                    w = rgba_bottom_up[i + 3]
                    r += rgba_bottom_up[i] * w
                    g += rgba_bottom_up[i + 1] * w
                    b += rgba_bottom_up[i + 2] * w
                    a += w
            o = (y * out_w + x) * 4
            if a:
                out[o:o + 3] = (round(r / a), round(g / a), round(b / a))
            out[o + 3] = round(a / area)
    return out_w, out_h, bytes(out)


# Command line ----------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(prog='python -m rc2016.app.thumbnails', description=__doc__.splitlines()[0])
    parser.add_argument('--source', required=True, type=Path, help='extracted game archive root (read only)')
    parser.add_argument('--output', required=True, type=Path, help='output root; thumbnails go to <output>/.cache/thumbnails')
    parser.add_argument('--size', type=int, default=DEFAULT_SIZE, help='thumbnail edge in pixels (32-512)')
    parser.add_argument('--ids-file', type=Path, help='model ids to render, one per line, in priority order')
    parser.add_argument('--all', action='store_true', help='render every archive model (after any ids from --ids-file)')
    parser.add_argument('--limit', type=int, default=0, help='stop after this many models (0 = no limit)')
    parser.add_argument('--force', action='store_true', help='render again even when the cache is current')
    parser.add_argument('--no-fbo', action='store_true', help='read the window back buffer instead of an offscreen framebuffer')
    return parser


def parse_arguments(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 32 <= args.size <= 512:
        parser.error('--size must be between 32 and 512')
    if not args.ids_file and not args.all:
        parser.error('give --ids-file and/or --all')
    return args


def select_ids(args, archive_models) -> list[str]:
    """Priority list: ids file first (in file order), then every other archive model sorted by path."""
    ordered = read_ids_file(args.ids_file) if args.ids_file else []
    if args.all:
        listed = set(ordered)
        ordered += [model_id for _path, model_id in sorted(archive_models) if model_id not in listed]
    return ordered[:args.limit] if args.limit > 0 else ordered


# Offscreen renderer ----------------------------------------------------------
class FramebufferError(RuntimeError):
    pass


_FBO_FUNCTIONS = {
    'GenFramebuffers': (None, [ctypes.c_int, ctypes.c_void_p]),
    'BindFramebuffer': (None, [ctypes.c_uint, ctypes.c_uint]),
    'GenRenderbuffers': (None, [ctypes.c_int, ctypes.c_void_p]),
    'BindRenderbuffer': (None, [ctypes.c_uint, ctypes.c_uint]),
    'RenderbufferStorage': (None, [ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_int]),
    'FramebufferRenderbuffer': (None, [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]),
    'CheckFramebufferStatus': (ctypes.c_uint, [ctypes.c_uint]),
    'DeleteFramebuffers': (None, [ctypes.c_int, ctypes.c_void_p]),
    'DeleteRenderbuffers': (None, [ctypes.c_int, ctypes.c_void_p]),
}


def load_fbo_functions(gl):
    """GL 3 / ARB framebuffer entry points, else the EXT ones; raises when neither set exists."""
    if os.name != 'nt':
        raise FramebufferError('WGL only')
    gl.wglGetProcAddress.argtypes = [ctypes.c_char_p]
    gl.wglGetProcAddress.restype = ctypes.c_void_p
    for suffix in ('', 'EXT'):
        functions = {}
        for name, (restype, argtypes) in _FBO_FUNCTIONS.items():
            address = gl.wglGetProcAddress(f'gl{name}{suffix}'.encode())
            if not address or address in (1, 2, 3, ctypes.c_void_p(-1).value):
                break
            functions[name] = ctypes.WINFUNCTYPE(restype, *argtypes)(address)
        else:
            return functions
    raise FramebufferError('framebuffer objects are not available')


class _ClearProxy:
    """The viewport's GL handle, with the clear colour replaced (transparent, or opaque grey)."""

    def __init__(self, gl, clear):
        self._gl, self._clear = gl, clear

    def __getattr__(self, name):
        return getattr(self._gl, name)

    def glClearColor(self, *_ignored):
        self._gl.glClearColor(*self._clear)


class _CaptureSwap:
    """Stands in for gdi32: ``SwapBuffers`` reads the finished frame instead of presenting it."""

    def __init__(self, viewport):
        self.viewport = viewport

    def SwapBuffers(self, _hdc):
        self.viewport.capture_frame()
        return 1


def thumbnail_viewport_class():
    """ModelViewport subclass that draws into an offscreen RGBA framebuffer (imported lazily: needs Tk)."""
    from rc2016.viewer.viewport import ModelViewport

    class ThumbnailViewport(ModelViewport):
        GL_FRAMEBUFFER, GL_RENDERBUFFER, GL_COLOR_ATTACHMENT0, GL_DEPTH_ATTACHMENT = 0x8D40, 0x8D41, 0x8CE0, 0x8D00
        GL_RGBA8, GL_DEPTH_COMPONENT24, GL_FRAMEBUFFER_COMPLETE = 0x8058, 0x81A6, 0x8CD5

        def __init__(self, parent, edge, use_fbo=True, **kwargs):
            super().__init__(parent, width=edge, height=edge, **kwargs)
            self.edge, self.use_fbo = edge, use_fbo
            self.fbo = None
            self.fbo_error = ''
            self.transparent = False
            self.want_capture = False
            self.frame = None

        def _create_context(self):
            super()._create_context()
            gl = self._gl
            gl.glReadBuffer.argtypes = [ctypes.c_uint]
            gl.glReadPixels.argtypes = [ctypes.c_int] * 4 + [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
            gl.glFinish.argtypes = []
            if self.use_fbo:
                try:
                    self._make_fbo()
                except Exception as exc:   # no usable framebuffers: fall back to the window back buffer
                    self.fbo, self.fbo_error = None, str(exc)
            self.transparent = self.fbo is not None
            self._gl = _ClearProxy(gl, (0.0, 0.0, 0.0, 0.0) if self.transparent else (0.14, 0.15, 0.17, 1.0))
            self._gdi32 = _CaptureSwap(self)
            self._make_current()

        def _make_fbo(self):
            gl, edge = self._gl, self.edge
            if self.winfo_width() != edge or self.winfo_height() != edge:
                raise FramebufferError(f'viewport is {self.winfo_width()}x{self.winfo_height()}, expected {edge}x{edge}')
            fn = load_fbo_functions(gl)
            frame, colour, depth = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
            fn['GenFramebuffers'](1, ctypes.byref(frame))
            fn['GenRenderbuffers'](1, ctypes.byref(colour))
            fn['GenRenderbuffers'](1, ctypes.byref(depth))
            fn['BindRenderbuffer'](self.GL_RENDERBUFFER, colour.value)
            fn['RenderbufferStorage'](self.GL_RENDERBUFFER, self.GL_RGBA8, edge, edge)
            fn['BindRenderbuffer'](self.GL_RENDERBUFFER, depth.value)
            fn['RenderbufferStorage'](self.GL_RENDERBUFFER, self.GL_DEPTH_COMPONENT24, edge, edge)
            fn['BindFramebuffer'](self.GL_FRAMEBUFFER, frame.value)
            fn['FramebufferRenderbuffer'](self.GL_FRAMEBUFFER, self.GL_COLOR_ATTACHMENT0, self.GL_RENDERBUFFER, colour.value)
            fn['FramebufferRenderbuffer'](self.GL_FRAMEBUFFER, self.GL_DEPTH_ATTACHMENT, self.GL_RENDERBUFFER, depth.value)
            status = fn['CheckFramebufferStatus'](self.GL_FRAMEBUFFER)
            if status != self.GL_FRAMEBUFFER_COMPLETE:
                fn['BindFramebuffer'](self.GL_FRAMEBUFFER, 0)
                raise FramebufferError(f'framebuffer incomplete (0x{status:X})')
            self.fbo = frame.value
            self._fbo_bind = fn['BindFramebuffer']

        def _make_current(self):
            current = super()._make_current()
            if current and self.fbo is not None:
                self._fbo_bind(self.GL_FRAMEBUFFER, self.fbo)
            return current

        def capture_frame(self):
            if not self.want_capture:
                return
            gl, edge = self._gl, self.edge
            gl.glFinish()
            gl.glReadBuffer(self.GL_COLOR_ATTACHMENT0 if self.fbo is not None else 0x0405)   # 0x0405 = GL_BACK
            gl.glPixelStorei(0x0D05, 1)
            pixels = (ctypes.c_ubyte * (edge * edge * 4))()
            gl.glReadPixels(0, 0, edge, edge, 0x1908, 0x1401, pixels)
            self.frame = bytes(pixels)

        def read_frame(self):
            """Draw the current mesh once and return its RGBA bytes (bottom row first)."""
            self.want_capture, self.frame = True, None
            try:
                self._draw()
            finally:
                self.want_capture = False
            if self.frame is None:
                raise RuntimeError('the frame could not be read back')
            return self.frame

    return ThumbnailViewport


def render_model(view, payload, size, supersample):
    """Set the payload, draw it once and return (png bytes, notes). Raises ValueError when it cannot be drawn."""
    if not view.set_mesh(payload):
        raise ValueError(view._error or 'the mesh could not be displayed')
    view.set_mode('Shaded')
    frame = view.read_frame()
    width = view.edge
    out_w, out_h, rgba = downsample_rgba(frame, width, width, supersample)
    if not any(rgba[3::4]):
        raise RuntimeError('empty render: nothing was drawn')
    notes = [view.shading_error] if view.shading_error else []
    return encode_png_rgba(out_w, out_h, rgba), notes


def emit(**fields):
    print(json.dumps(fields, separators=(',', ':')), flush=True)


def run(args) -> int:
    """Render the requested models; returns the process exit code."""
    import tkinter as tk
    from rc2016.viewer import textures
    from rc2016.viewer.loader import SourcePreviewLoader

    # Thumbnails do not need the streamed HD mips (interactive viewing is unaffected: this is this process only).
    textures.MIN_PREVIEW_SIZE = 0
    size, supersample = args.size, SUPERSAMPLE
    folder = cache_dir(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    index = ThumbnailIndex.load(folder)
    loader = SourcePreviewLoader()
    archive = loader._ensure_archive(args.source)
    models = [(entry['path'], model_id) for model_id, entry in archive.entries.items() if entry['type'] == 'model']
    ids = select_ids(args, models)
    total = len(ids)
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes('-alpha', 0.0)
    edge = size * supersample
    root.geometry(f'{edge}x{edge}+0+0')
    view = thumbnail_viewport_class()(root, edge, use_fbo=not args.no_fbo)
    view.pack(fill='both', expand=True)
    root.update()
    if view._context_error or view._hrc is None:
        emit(event='fatal', reason=view._context_error or 'no OpenGL context')
        root.destroy()
        return 2
    emit(event='start', total=total, fbo=view.fbo is not None, fbo_error=view.fbo_error, transparent=view.transparent,
         version=THUMBNAIL_VERSION, size=size)
    done = 0
    try:
        for number, model_id in enumerate(ids, 1):
            started = time.perf_counter()
            entry = archive.entries.get(model_id)
            status, reason, source_hash, notes, load_s, render_s = 'ok', '', None, [], 0.0, 0.0
            try:
                if not entry or entry['type'] != 'model':
                    raise KeyError('not a model in this archive')
                _data, source_hash = archive.bytes(model_id)
                if not args.force and index.is_current(model_id, size, source_hash):
                    emit(event='model', id=model_id, status=index.get(model_id)['status'], skipped=True, n=number, total=total)
                    continue
                payload = loader.load(args.source, model_id, entry['path'], 'Highest', 0, None, True)
                load_s = time.perf_counter() - started
                png, notes = render_model(view, payload, size, supersample)
                render_s = time.perf_counter() - started - load_s
                index.write_png(model_id, png)
            except (ValueError, KeyError, NotImplementedError) as error:
                status, reason = 'unsupported', f'{type(error).__name__}: {error}'
            except Exception as error:   # one failure never stops the batch
                status, reason = 'error', f'{type(error).__name__}: {error}'
            if status != 'ok':
                png_path = index.png_path(model_id)
                if png_path.exists():
                    png_path.unlink()
            index.record(model_id, status, source_hash, size, reason or '; '.join(notes))
            seconds = time.perf_counter() - started
            emit(event='model', id=model_id, status=status, reason=reason, seconds=round(seconds, 3),
                 load_s=round(load_s, 3), render_s=round(render_s, 3), n=number, total=total)
            done += 1
            # Bound memory: only the model being drawn stays decoded, texture cache is trimmed.
            loader.identity = None
            if len(loader.texture_cache) > 400:
                loader.texture_cache.clear()
            view.clear()
            index.save_if_due()
            root.update_idletasks()
    finally:
        index.save()
        emit(event='done', rendered=done, total=total)
        try:
            root.destroy()
        except Exception:
            pass
    return 0


class RendererProcess:
    """One owned renderer child: JSON status lines arrive on ``events``; no shell command string."""

    def __init__(self, source, output, ids=None, all_models=False, size=DEFAULT_SIZE, cwd=None, python=None, ids_dir=None):
        self.events = queue.Queue()
        self.process = None
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.ids = list(ids or [])
        self.command = None
        self.source, self.output, self.size, self.all_models = source, output, size, all_models
        self.cwd, self.python = cwd, python
        self.ids_dir = Path(ids_dir) if ids_dir else cache_dir(output)

    def start(self):
        folder = cache_dir(self.output)
        folder.mkdir(parents=True, exist_ok=True)
        command = [str(self.python or sys.executable), '-m', 'rc2016.app.thumbnails', '--source', str(self.source),
                   '--output', str(self.output), '--size', str(self.size)]
        if self.ids:
            ids_file = self.ids_dir / f'queue-{os.getpid()}-{id(self)}.txt'
            ids_file.write_text('\n'.join(self.ids) + '\n', encoding='utf8')
            command += ['--ids-file', str(ids_file)]
            self._ids_file = ids_file
        if self.all_models:
            command.append('--all')
        self.command = command
        self.log = (folder / 'renderer.log').open('wb')
        self.process = subprocess.Popen(command, cwd=self.cwd, stdout=subprocess.PIPE, stderr=self.log,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, daemon=True, name='thumbnail-reader').start()
        return self

    def _read(self):
        try:
            for raw in self.process.stdout:
                try:
                    self.events.put(json.loads(raw.decode('utf8', 'replace')))
                except ValueError:
                    continue
        finally:
            code = self.process.wait()
            self.events.put(dict(event='exit', code=code))
            self.finished.set()

    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            if os.name == 'nt':
                try:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=3, check=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        for attribute in ('log',):
            handle = getattr(self, attribute, None)
            if handle:
                handle.close()
        ids_file = getattr(self, '_ids_file', None)
        if ids_file:
            try:
                ids_file.unlink()
            except OSError:
                pass


def main(argv=None) -> int:
    args = parse_arguments(argv)
    return run(args)


if __name__ == '__main__':
    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(main())
