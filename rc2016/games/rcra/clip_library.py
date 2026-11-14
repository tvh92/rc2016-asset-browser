"""Rift Apart clip library for the 3D preview and the portable export (the ``Game.animation_library`` hook).

``RcraAnimationLibrary`` is ``viewer.animation.AnimationLibrary`` with Rift Apart sources: the same rows,
inverse binds, inheritance rule, classification, additive preview and skin matrices, but each clip is its
own ``.animclip`` asset decoded by ``rcra.animation.decode_clip``. Clips are found through the dag, never
by joint coverage alone:

* **Animsets** the model's actors use: an actor or config that depends on the model, and the configs it
  depends on, name the animsets; each animset depends on its clips.
* **Same-folder animsets**: the animsets in the model's own folder whose first clip drives the model's
  skeleton (most of its joints, by joint hash). Outfit models such as ``hero_ratchet_pirate.model`` are
  assembled at runtime and no actor names them; their character folder holds the animset.
* **Cutscene shots** (``with_cinematic``): clips under ``cinematics/`` whose name ends in
  ``_shot<N>_<performer>`` where the performer is the model's file stem or one of its folder names holding
  an underscore (optionally followed by ``_master<N>`` or a copy number), as for R&C 2016. The clip's
  ``.cinematic2`` (the asset that depends on it) is its source.

Each found clip is still classified on its first frame; one that does not drive the skeleton is listed as
unplayable.
"""
from __future__ import annotations

import copy
import re
import tempfile
from pathlib import Path

import numpy as np

from rc2016.common import sha
from rc2016.games.rcra.animation import decode_clip, header
from rc2016.viewer.animation import AnimationLibrary

CUTSCENE_SOURCE = 'cinematic2'
PERFORMER = re.compile(r'_shot\d+[a-z]?_(.+)$')
COPY_SUFFIX = re.compile(r'(_master\d*|\d+)$')
# A same-folder animset is kept when its first clip names at least this share of the model's joints.
FOLDER_FIT = 0.5
FOLLOW = ('actor', 'config')


def _kind(path):
    name = path.rsplit('/', 1)[-1]
    return name.rsplit('.', 1)[-1] if '.' in name else ''


class _DagIndex:
    """Reverse dag dependencies and the cutscene performer index of one RcraSource, built once."""

    def __init__(self, source):
        dag = source.dag
        self.paths = [p.replace('\\', '/').lower() for p in dag.paths]
        self.by_path = {p: i for i, p in enumerate(self.paths)}
        users = {}
        starts, deps = dag.dep_starts, dag.deps
        stops = np.nonzero(deps == -1)[0]
        for index, start in enumerate(starts.tolist()):
            if start < 0:
                continue
            end = int(stops[np.searchsorted(stops, start)]) if len(stops) else len(deps)
            for target in deps[start:end].tolist():
                users.setdefault(int(target), []).append(index)
        self.users = users
        self.dag = dag
        self.performers = {}
        for index, path in enumerate(self.paths):
            if path.startswith('cinematics/') and path.endswith('.animclip'):
                found = PERFORMER.search(path.rsplit('/', 1)[-1][:-len('.animclip')])
                if found:   # listed as written and without a _master<N> or copy-number suffix
                    for name in {found.group(1), COPY_SUFFIX.sub('', found.group(1))}:
                        self.performers.setdefault(name, []).append(index)
        self.animsets = [i for i, p in enumerate(self.paths) if p.endswith('.animset')]

    def deps(self, index):
        return self.dag.dependencies(index)


def dag_index(source):
    index = getattr(source, '_rcra_clip_index', None)
    if index is None:
        index = source._rcra_clip_index = _DagIndex(source)
    return index


def _key(source, path):
    keys = source.resolve(path)
    return keys[0] if len(keys) == 1 else None


def model_animsets(source, model_path):
    """Animsets the model's actors and configs name, in dag order (paths with '/')."""
    index = dag_index(source)
    model = index.by_path.get(model_path.replace('\\', '/').lower())
    if model is None:
        return []
    queue = [u for u in index.users.get(model, []) if _kind(index.paths[u]) in FOLLOW]
    seen, found = set(), []
    while queue:
        node = queue.pop(0)
        if node in seen:
            continue
        seen.add(node)
        for dep in index.deps(node):
            kind = _kind(index.paths[dep])
            if kind == 'animset':
                found.append(index.paths[dep])
            elif kind == 'config':
                queue.append(dep)
    return list(dict.fromkeys(found))


def animset_clips(source, animset):
    """Clip paths an animset depends on, in its dependency order."""
    index = dag_index(source)
    node = index.by_path.get(animset)
    if node is None:
        return []
    return [index.paths[d] for d in index.deps(node) if index.paths[d].endswith('.animclip')]


def folder_animsets(source, model_path, joint_hashes, exclude=()):
    """Animsets in the model's folder whose first clip names at least FOLDER_FIT of the model's joints."""
    index = dag_index(source)
    folder = model_path.replace('\\', '/').lower().rsplit('/', 1)[0] + '/'
    found = []
    for node in index.animsets:
        path = index.paths[node]
        if not path.startswith(folder) or '/' in path[len(folder):] or path in exclude:
            continue
        for clip in animset_clips(source, path):
            key = _key(source, clip)
            if key is None:
                continue
            try:
                decoded = decode_clip(source.bytes(key)[0], first_frames=1)
            except ValueError:
                continue
            clip_hashes = {int(j['hash'], 16) for j in decoded['joints']}
            if joint_hashes and len(clip_hashes & joint_hashes) >= FOLDER_FIT * len(joint_hashes):
                found.append(path)
            break
    return found


def cutscene_clips(source, model_path):
    """[(cinematic2 path, clip path)] of the model's performances (see the module notes)."""
    index = dag_index(source)
    parts = model_path.replace('\\', '/').lower().split('/')
    names = {Path(parts[-1]).stem} | {p for p in parts[:-1] if '_' in p}
    found = []
    for node in sorted({n for name in names for n in index.performers.get(name, [])}):
        owners = [index.paths[u] for u in index.users.get(node, []) if index.paths[u].endswith('.cinematic2')]
        owner = owners[0] if owners else index.paths[node].rsplit('/', 1)[0] + '.cinematic2'
        found.append((owner, index.paths[node]))
    return sorted(set(found))


class RcraAnimationLibrary(AnimationLibrary):
    """``AnimationLibrary`` over a Rift Apart model and its ``.animclip`` assets."""

    def __init__(self, source, model_bytes, model_path, clips=True):
        from rc2016.formats.skeleton import inheritance_rule, inspect_model, rest_inheritance_rule
        self.source, self.model_path = source, model_path
        self.rows, self.inverse, self.clips, self.animset_data = [], [], [], {}
        self._kinds, self.retarget, self._cutscenes, self._last = {}, {}, None, (None, None)
        self.inheritance = self.rest_inheritance = 'full'
        self.names = {}
        with tempfile.TemporaryDirectory(prefix='rcra-anim-') as tmp:
            model_file = Path(tmp) / 'selected.model'
            model_file.write_bytes(bytes(model_bytes)[36:])   # the DAT1 after the toc header
            inspected = inspect_model(model_file)
        self.rows = inspected['skeleton']
        self.inverse = [np.array(m) for m in inspected['inverse_bind_matrices']]
        self.inheritance = inheritance_rule(inspected['profile'])
        self.rest_inheritance = rest_inheritance_rule(inspected['profile'])
        self.names = {int(r['hash'], 16): r['name'] for r in self.rows}
        if not clips or not self.rows or len(self.inverse) != len(self.rows):
            return
        animsets = model_animsets(source, model_path)
        animsets += folder_animsets(source, model_path, set(self.names), exclude=animsets)
        for animset in animsets:
            for clip in animset_clips(source, animset):
                self._add(animset, clip, 'animset')

    def _add(self, owner, clip_path, kind):
        key = _key(self.source, clip_path)
        if key is None:
            return
        entry = dict(animset=owner, offset=0, size=None, key=key, path=clip_path, name=Path(clip_path).stem,
                     seconds=None, source=kind)
        try:
            facts = header(self._bytes(entry))
        except ValueError:
            facts = {}
        entry.update(name=facts.get('name') or entry['name'], seconds=facts.get('duration_seconds'))
        self.animset_data.setdefault(owner, None)
        self.clips.append(entry)

    def _bytes(self, clip):
        key, data = self._last
        if key != clip['key']:
            data = self.source.bytes(clip['key'])[0]
            self._last = (clip['key'], data)
        return data

    def _clip_digest(self, clip):
        return sha(self._bytes(clip))

    def _decode_clip(self, clip, first_frames=None):
        return decode_clip(self._bytes(clip), self.names, first_frames=first_frames, label=clip['name'])

    def with_cinematic(self, archive=None, model_path=None, replace=False, sources=None):
        """Copy that also offers the model's cutscene shots (``cutscene_clips``); with ``replace`` only them."""
        other = copy.copy(self)
        other.clips = [] if replace else list(self.clips)
        other.animset_data = {} if replace else dict(self.animset_data)
        other._kinds = self._kinds
        if self._cutscenes is None and self.rows:
            self._cutscenes = cutscene_clips(self.source, model_path or self.model_path)
        for owner, clip in self._cutscenes or ():
            other._add(owner, clip, CUTSCENE_SOURCE)
        return other


def animation_library(source, model_bytes, model_path, clips=True):
    """The ``Game.animation_library`` hook: the model's clip library (``clips=False``: skeleton only)."""
    return RcraAnimationLibrary(source, model_bytes, model_path, clips=clips)
