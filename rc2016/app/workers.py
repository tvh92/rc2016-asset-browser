"""Bounded background work and owned-process lifecycle; no shell command strings."""
from __future__ import annotations
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import base64
import io
import json
import math
import os
from pathlib import Path
import queue
import struct
import subprocess
import threading
import time


class LatestWork:
    """One active and one latest pending selection; obsolete results never reach UI."""
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='asset-metadata')
        self.lock = threading.Lock()
        self.generation = 0
        self.pending = None
        self.active = False
        self.closed = False
        self.events = queue.Queue()

    def submit(self, fn, *args):
        with self.lock:
            if self.closed:
                return None
            self.generation += 1
            self.pending = (self.generation, fn, args)
            if not self.active:
                self.active = True
                self.executor.submit(self._drain)
            return self.generation

    def _drain(self):
        while True:
            with self.lock:
                work, self.pending = self.pending, None
                if self.closed or work is None:
                    self.active = False
                    return
            generation, fn, args = work
            try:
                payload = fn(*args)
                result = (generation, payload, None)
            except Exception as error:
                result = (generation, None, str(error))
            with self.lock:
                if not self.closed and generation == self.generation:
                    self.events.put(result)

    def close(self):
        with self.lock:
            self.closed = True
            self.pending = None
        self.executor.shutdown(wait=False, cancel_futures=True)


class ThumbnailCache:
    """Eight small thumbnails; PNG bounds checked before any decoder allocation."""
    def __init__(self, capacity=8):
        self.capacity = capacity
        self.cache = OrderedDict()

    def load(self, file, expected_hash):
        import hashlib
        def sha256_file(path):
            digest = hashlib.sha256()
            with Path(path).open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            return digest.hexdigest()
        file = Path(file)
        if not file.is_file():
            raise ValueError('Preview missing')
        stat = file.stat()
        if stat.st_size > 32 * 1024 * 1024:
            raise ValueError('Preview too large for thumbnail; open the artifact instead')
        digest = sha256_file(file)
        if not expected_hash or digest != expected_hash:
            raise ValueError('Preview stale or unverified: recorded image hash does not match')
        key = (str(file.resolve()), digest)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        data = file.read_bytes()
        if data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) < 33:
            raise ValueError('Invalid PNG preview')
        width, height = struct.unpack('>II', data[16:24])
        if width < 1 or height < 1 or width * height > 8_000_000:
            raise ValueError('Invalid or oversized PNG dimensions')
        scale = max(1, math.ceil(width / 400), math.ceil(height / 250))
        try:
            from PIL import Image
        except ImportError:
            # Tk has a native PNG decoder. Only one bounded image is decoded on UI.
            pass
        else:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                im.thumbnail((400, 250))
                out = io.BytesIO()
                im.save(out, format='PNG')
                data, scale = out.getvalue(), 1
        result = (base64.b64encode(data), scale)
        # The fallback can cache bigger encoded PNGs: bound total bytes as well.
        self.cache[key] = result
        while len(self.cache) > self.capacity or sum(len(v[0]) for v in self.cache.values()) > 8_000_000:
            self.cache.popitem(last=False)
        return result


class ReviewProcess:
    """Single submission, live logs, cancel-before-launch, and reap on every exit."""
    def __init__(self, command, report_path, cwd, event=None):
        self.command = list(map(str, command))
        self.report_path = Path(report_path)
        self.cwd = Path(cwd)
        self.event = event or (lambda *_a, **_kw: None)
        self.cancel_requested = threading.Event()
        self.lock = threading.Lock()
        self.started = False
        self.process = None

    def cancel(self):
        self.cancel_requested.set()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            self._terminate(process)

    @staticmethod
    def _terminate(process):
        if os.name == 'nt':
            try:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def run(self):
        with self.lock:
            if self.started:
                raise RuntimeError('Review already submitted')
            self.started = True
        if self.cancel_requested.is_set():
            return {'status': 'cancelled'}
        if self.report_path.exists():
            raise ValueError('Review report already exists; choose a new isolated run')
        process = None
        try:
            process = subprocess.Popen(self.command, cwd=self.cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding='utf8', errors='replace', bufsize=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            with self.lock:
                self.process = process
            if self.cancel_requested.is_set():
                self._terminate(process)
            lines = queue.Queue(maxsize=512)
            reader_done = threading.Event()
            def reader():
                try:
                    for line in process.stdout:
                        while True:
                            try:
                                lines.put(line, timeout=.1)
                                break
                            except queue.Full:
                                if self.cancel_requested.is_set(): return
                finally:
                    reader_done.set()
            threading.Thread(target=reader, daemon=True).start()
            with (self.report_path.parent / 'process.log').open('w', encoding='utf8') as log:
                while process.poll() is None or not reader_done.is_set() or not lines.empty():
                    if self.cancel_requested.is_set() and process.poll() is None:
                        self._terminate(process)
                    try: line = lines.get(timeout=.1)
                    except queue.Empty: continue
                    if line is None: break
                    log.write(line);log.flush()
                    self.event('log', message=line.rstrip())
            code = process.wait()
            if self.cancel_requested.is_set():
                return {'status': 'cancelled'}
            if not self.report_path.is_file():
                raise RuntimeError(f'Review exited {code} without a result; see process.log')
            report = json.loads(self.report_path.read_text(encoding='utf8'))
            if code or report.get('status') != 'passed':
                raise RuntimeError(report.get('error') or f'Review exited {code}; see process.log')
            return report
        finally:
            if process is not None:
                if process.poll() is None: self._terminate(process)
                if process.stdout: process.stdout.close()
            with self.lock:
                self.process = None


def review_command(blender, adapter, request):
    return [str(blender), '--background', '--factory-startup', '--disable-autoexec',
        '--python-exit-code', '1', '--python', str(adapter), '--', '--request', str(request)]


def open_local(path, blender=None):
    path = Path(path).expanduser().resolve(strict=True)
    if blender is not None:
        executable = Path(blender).resolve(strict=True)
        return subprocess.Popen([str(executable), '--disable-autoexec', str(path)],
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if os.name == 'nt':
        os.startfile(str(path))
    else:
        subprocess.Popen(['xdg-open', str(path)])
