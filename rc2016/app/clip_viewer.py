"""Viewport beside the clip picker: plays the clip clicked in the list on its model."""
from __future__ import annotations

import copy
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from rc2016.viewer.loader import SourcePreviewLoader
from rc2016.viewer.viewport import ModelViewport


class ClipViewer:
    """Plays one clip of a model in a ModelViewport of its own. It loads through its own SourcePreviewLoader, so the
    browser's model and clip are left as they are. Reads run on one worker thread, in the order they were asked for;
    the viewport and the controls are only touched on the Tk thread."""

    POLL_MS = 40

    def __init__(self, tk, ttk, parent, source_root, index_root=None, borrowed=False, cinematic=False):
        self.tk, self.ttk, self.parent = tk, ttk, parent
        self.source_root = str(source_root)
        self.index_root = str(index_root) if index_root else None
        self.borrowed = borrowed and self.index_root is not None   # the export's rule: borrowed needs the index
        self.cinematic = cinematic
        self.loader = SourcePreviewLoader()
        self.loaded = None   # worker thread only: (model_id, source_path) whose clip library the loader holds
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-viewer")
        self.pending = deque()   # futures in the order they were asked for
        self.frames, self.fps, self.name, self.frame, self.playing = [], 30.0, "", 0, False
        self.poll_job = self.tick_job = None
        self.closed = False
        self.status = tk.StringVar(value="")
        self.position = tk.DoubleVar(value=0)

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.viewport = ModelViewport(parent, width=360, height=340)
        self.viewport.grid(row=0, column=0, sticky="nsew")
        controls = ttk.Frame(parent)
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        controls.columnconfigure(1, weight=1)
        self.play_button = ttk.Button(controls, text="Play", width=7, command=self.toggle)
        self.play_button.grid(row=0, column=0)
        self.slider = ttk.Scale(controls, from_=0, to=1, variable=self.position, command=self._scrub)
        self.slider.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(parent, textvariable=self.status, style="Muted.TLabel", wraplength=340).grid(row=2, column=0, sticky="w",
                                                                                              pady=(6, 0))
        self.play_button.state(["disabled"])
        self.slider.state(["disabled"])
        self.viewport.clear("Click an animation's name to play it here.")
        self._poll()

    # Requests (Tk thread)

    def show(self, record, label) -> None:
        """Play ``label`` on ``record``'s model. The model loads first when it is not the one shown."""
        self.pending.append(self.executor.submit(self._read, (record.model_id, record.archive_path), label))
        self.status.set("Loading the animation…")

    def close(self) -> None:
        self.closed = True
        for job in (self.poll_job, self.tick_job):
            if job is not None:
                self.parent.after_cancel(job)
        self.poll_job = self.tick_job = None
        self.executor.shutdown(wait=False, cancel_futures=True)

    # Worker thread

    def _read(self, key, label):
        """Load the model when it is new, then the pose of ``label``. Returns (mesh or None, pose or None, message).
        The mesh is returned even when the clip is missing, so the model still shows."""
        model_id, source_path = key
        mesh = None
        if self.loaded != key:
            self.loaded = None
            mesh = self.loader.load(self.source_root, model_id, source_path, "Highest", 0, self.index_root, True)
            self.loader.load_animation(self.source_root, model_id, source_path, self.index_root, self.borrowed)
            self.loader.animation = self._offered(self.loader.animation)
            self.loaded = key
        library = self.loader.animation
        labels = library.labels() if library is not None and library.rows else []
        if label not in labels:
            return mesh, None, f"'{label}' is not offered for this model"
        return mesh, self.loader.pose(labels.index(label)), ""

    def _offered(self, library):
        """The clips the export offers. A preview load also adds the cutscene shots; the export adds them only when
        cinematic, and only then do its animset labels differ (no ``stem:`` prefix), so the list must match it."""
        from rc2016.formats.animation_sources import CINEMATIC_SOURCE
        if library is None or self.cinematic:
            return library
        shots = {c["animset"] for c in library.clips if c.get("source") == CINEMATIC_SOURCE}
        other = copy.copy(library)
        other.clips = [c for c in library.clips if c.get("source") != CINEMATIC_SOURCE]
        other.animset_data = {path: data for path, data in library.animset_data.items() if path not in shots}
        return other

    # Tk thread

    def _poll(self) -> None:
        self.poll_job = None
        while self.pending and self.pending[0].done():
            future = self.pending.popleft()
            try:
                self._apply(*future.result())
            except Exception as error:   # a model that cannot be loaded says why, in place of the pose
                self._fail(str(error))
        if not self.closed:
            self.poll_job = self.parent.after(self.POLL_MS, self._poll)

    def _apply(self, mesh, pose, message) -> None:
        if mesh is not None:
            self.viewport.set_mesh(mesh)
        if pose is None:
            self._fail(message)
            return
        if not self.viewport.can_animate:
            self._fail("Needs shader support")
            return
        self.frames, self.fps, self.name = pose["frames"], pose["fps"], pose["name"]
        count = len(self.frames)
        self.playing = count > 1
        self.play_button.state(["!disabled"])
        self.slider.configure(to=max(1, count - 1))
        self.slider.state(["!disabled"])
        self._show_frame(0)
        self._sync()

    def _fail(self, message: str) -> None:
        self.frames, self.playing = [], False
        self.play_button.state(["disabled"])
        self.slider.state(["disabled"])
        self.viewport.clear_pose()
        self._sync()
        self.status.set(message)

    def _show_frame(self, frame: int) -> None:
        count = len(self.frames)
        self.frame = frame % count
        self.viewport.set_pose(self.frames[self.frame])
        self.position.set(self.frame)
        self.play_button.configure(text="Pause" if self.playing else "Play")
        self.status.set(f"{self.name}  ·  frame {self.frame + 1} of {count} at {self.fps:g} fps")

    def _sync(self) -> None:
        """Time-driven materials and the frame clock run only while the clip plays."""
        self.viewport.set_effects_running(self.playing)
        if self.playing and self.tick_job is None:
            self._schedule_tick()
        elif not self.playing and self.tick_job is not None:
            self.parent.after_cancel(self.tick_job)
            self.tick_job = None

    def _schedule_tick(self) -> None:
        delay = max(10, int(round(1000 / max(1.0, self.fps))))
        self.tick_job = self.parent.after(delay, self._tick)

    def _tick(self) -> None:
        self.tick_job = None
        if self.playing and self.frames and not self.closed:
            self._show_frame(self.frame + 1)
            self._schedule_tick()

    def toggle(self) -> None:
        if not self.frames:
            return
        self.playing = not self.playing
        self.play_button.configure(text="Pause" if self.playing else "Play")
        self._sync()

    def _scrub(self, value) -> None:
        if not self.frames:
            return
        frame = int(round(float(value)))
        if self.playing:
            self.playing = False
            self._sync()
        if frame != self.frame:
            self._show_frame(frame)
