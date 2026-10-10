"""The browser's access to the archive-wide links table (rc2016/formats/links.py).

The table is opened, or built once per archive (about half a minute), on a worker thread with its
own archive instance. Requests queue a callback that runs on the Tk thread once the table is
ready, with the AssetLinks object, or None when it could not be read (``message`` says why).
"""
from __future__ import annotations

import queue
import threading


class LinkService:
    def __init__(self, app):
        self.app = app
        self.links = self.coverage = None   # AssetLinks; {model path: set of coverage kinds}
        self.root = None
        self.message = ''
        self.building = None   # the source root being opened
        self.waiting = []
        self.events = queue.Queue()
        self._status = None   # the progress line's text before indexing, restored afterwards

    def request(self, callback, key=None):
        """Run ``callback(links)`` on the Tk thread once the current source root's table is open.
        A pending callback with the same ``key`` is replaced instead of queued twice."""
        root = self.app.source_var.get().strip()
        if self.links is not None and self.root == root:
            callback(self.links)
            return
        if not root:
            self.message = 'Choose the extracted game folder first.'
            callback(None)
            return
        self.waiting = [(k, c) for k, c in self.waiting if key is None or k != key] + [(key, callback)]
        if self.building != root:
            self.building = root
            threading.Thread(target=self._work, args=(root,), daemon=True).start()

    def _work(self, root):
        try:
            from rc2016.formats.links import AssetLinks, links_path
            from rc2016.levels.scene import open_archive
            archive = open_archive(root)
            if not links_path(archive).is_file():
                self.events.put(('progress', root, 'Indexing which models, levels and textures link to each other '
                                                   '(once per archive, about half a minute)…'))
            links = AssetLinks.open(archive, build=True, progress=lambda done, total, stage: self.events.put(
                ('progress', root, f'Indexing asset links: {done:,}/{total:,} ({stage})')))
            coverage = links.models_by_coverage()
            self.events.put(('ready', root, (links, coverage)))
        except Exception as exc:
            self.events.put(('error', root, f'Asset links unavailable: {type(exc).__name__}: {exc}'))

    def drain(self):
        """Apply worker results; call from the Tk thread's periodic drain."""
        while True:
            try:
                kind, root, value = self.events.get_nowait()
            except queue.Empty:
                return
            if root != self.building:
                continue   # the source root changed meanwhile
            if kind == 'progress':
                if self._status is None:
                    self._status = self.app.progress_var.get()
                self.message = value
                self.app.progress_var.set(value)
                continue
            self.building = None
            if self._status is not None and self.app.progress_var.get() == self.message:
                self.app.progress_var.set(self._status)
            self._status = None
            if kind == 'ready':
                if self.links is not None:
                    self.links.close()
                (self.links, self.coverage), self.root, self.message = value, root, ''
            else:
                self.message = value
                self.app._append_log(value, 'error')
            waiting, self.waiting = self.waiting, []
            for _key, callback in waiting:
                callback(self.links if kind == 'ready' else None)

    def close(self):
        if self.links is not None:
            self.links.close()
            self.links = None


def placement_lines(placements, limit=12):
    """Details-tab lines for AssetLinks.model_placements()."""
    if not placements:
        return ['Placed in levels: none (no zone model-instance table places this model)']
    stem = lambda path: path.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    total = sum(p['instances'] for p in placements)
    levels = {level for p in placements for level in p['levels']}
    lines = [f"Placed in levels: {total:,} instance{'s' if total != 1 else ''} in {len(placements)} zone"
             f"{'s' if len(placements) != 1 else ''} of {len(levels)} level{'s' if len(levels) != 1 else ''}"]
    for p in placements[:limit]:
        level = ', '.join(stem(level) for level in p['levels']) or '(no level lists this zone)'
        lines.append(f"  {level} · {stem(p['zone'])}: {p['instances']:,}")
    if len(placements) > limit:
        lines.append(f'  … and {len(placements) - limit} more zones')
    return lines


__all__ = ['LinkService', 'placement_lines']
