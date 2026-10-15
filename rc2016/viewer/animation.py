"""Source animation clips for the 3D preview, decoded in memory; nothing is written.

Uses the pipeline's own decoders: ``inspect_model`` (skeleton rows, inverse
binds, inheritance candidates), ``ordered_containers``/``decode_clip``/
``sample_transforms`` (exact source samples) and ``worlds`` (joint world
matrices). The skin matrix of joint j at a frame is ``world_j @ inverse_bind_j``
in native units (checked: identity at rest to ~1e-6 on Toxic Crab and Clank).

Viewing aid only: constant per-frame sampling at source fps, no blending,
root motion, attachments or runtime layers. Additive-style clips are shown
standalone (rest T + delta T, rest Q * delta Q, rest S * delta S), as the
pipeline's preview note does. Inheritance follows the pipeline: the model's bundled profile rule, else the
single inverse-bind-compatible rule, else ``segment_scale_compensation_bit0``.
"""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile

import numpy as np

from rc2016.formats.animation_samples import decode_clip
from rc2016.formats.animation_transforms import sample_transforms
from rc2016.formats.archive import decompress
from rc2016.formats.clips import classify, ordered_containers
from rc2016.formats.index import open_asset_index, references
from rc2016.common import sha
from rc2016.formats.skeleton import bundled_profile, inheritance_rule, inspect_model, worlds

# Native (x, y, z) -> preview (x, -z, y), the same basis as the geometry.
BASIS = np.array([[1.0, 0, 0, 0], [0, 0, -1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]])
PLAYABLE = ('absolute skeletal', 'partial absolute skeletal', 'relative/additive-style layer')


def _blob(archive, path, kind):
    ids = [k for k in archive.resolve(path) if archive.entries[k]['type'] == kind]
    if len(ids) != 1:
        return None
    return decompress(archive.bytes(ids[0])[0])[0]


def model_animsets(archive, output_root, model_path):
    """Animsets reached from the actors that use this model (through their configs).

    Mirrors the pipeline's reference walk: actors come from the run's
    ``asset_references.sqlite``; other actors they attach are not followed,
    because an attached actor has its own animation library.
    """
    try:
        db = open_asset_index(output_root)
        try:
            actors = [a for (a,) in db.execute(
                "SELECT DISTINCT a.path FROM references_found r JOIN assets a ON a.id = r.source "
                "WHERE r.target_path = ? AND a.type = 'actor'", (model_path,))]
        finally:
            db.close()
    except Exception:
        return []
    queue, seen, found = list(actors), set(), []
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        data = _blob(archive, path, 'actor' if path.endswith('.actor') else 'config')
        if data is None:
            continue
        for ref in references(data):
            target = ref.get('path', '')
            if target.endswith('.animset'):
                found.append(target)
            elif target.endswith('.config'):
                queue.append(target)
    return list(dict.fromkeys(found))


def _quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]


class AnimationLibrary:
    """Skeleton of one model plus the clip directory of its animsets."""

    def __init__(self, archive, model_bytes, animsets):
        with tempfile.TemporaryDirectory(prefix='rcps4-anim-') as tmp:
            model_file = Path(tmp) / 'selected.model'
            model_file.write_bytes(model_bytes)
            inspected = inspect_model(model_file, bundled_profile(sha(model_bytes)))
            self.rows = inspected['skeleton']
            self.inverse = [np.array(m) for m in inspected['inverse_bind_matrices']]
            self.inheritance = inheritance_rule(inspected['profile'])
            self.names = {int(r['hash'], 16): r['name'] for r in self.rows}
            self.clips, self.animset_data = [], {}
            if not self.rows or len(self.inverse) != len(self.rows):
                return
            for number, animset in enumerate(animsets):
                data = _blob(archive, animset, 'animset')
                if data is None:
                    continue
                file = Path(tmp) / f'{number}.animset'
                file.write_bytes(data)
                try:
                    containers, _errors = ordered_containers(file)
                except ValueError:
                    continue
                self.animset_data[animset] = data
                for container in containers:
                    self.clips.append(dict(animset=animset, offset=container['offset'], name=container['name']))

    def with_cinematic(self, archive, replace=False, sources=None):
        """Copy of this library that also offers the real-time cutscene shots covering the whole model.

        The shots come from ``animation_sources.cinematic_sources`` (clips named by an exact CRC32 match,
        joints covering every model joint); pass its result as ``sources`` to avoid scanning twice. With
        ``replace`` the copy holds only those shots. The original library is left unchanged. Cutscene
        clips carry ``source='cinematic2'`` and decode through the same path as animset clips.
        """
        from rc2016.formats.animation_sources import CINEMATIC_SOURCE, cinematic_sources
        other = copy.copy(self)
        other.clips = [] if replace else list(self.clips)
        other.animset_data = {} if replace else dict(self.animset_data)
        if sources is None:
            sources, _errors = cinematic_sources(archive, self.rows) if self.rows else ([], [])
        for source in sources:
            clips = [c for c in source['clips'] if c['name']]
            if not clips:
                continue
            other.animset_data[source['path']] = archive.asset_data(source['asset_id'])
            other.clips.extend(dict(animset=source['path'], offset=c['offset'], name=c['name'], source=CINEMATIC_SOURCE)
                               for c in clips)
        return other

    def labels(self):
        several = len(self.animset_data) > 1
        plain = [c['name'] for c in self.clips if c.get('source') == 'cinematic2']
        labels = []
        for c in self.clips:
            # Cutscene shot names already carry their cutscene; prefix only when two shots share a name.
            prefix = several and not (c.get('source') == 'cinematic2' and plain.count(c['name']) == 1)
            labels.append((f"{Path(c['animset']).stem}: " if prefix else '') + c['name'])
        return labels

    def pose(self, index):
        """Per-frame skin matrices (joints x 4 x 4, preview basis) for clip ``index``."""
        entry = self.clips[index]
        clip = decode_clip(self.animset_data[entry['animset']], entry['offset'], self.names)
        frames = sample_transforms(clip)
        kind, _evidence = classify(self.rows, frames)
        if kind not in PLAYABLE:
            raise ValueError(f"'{entry['name']}' is {kind}; it does not drive this model's skeleton")
        if kind == 'relative/additive-style layer':
            rest = {r['hash']: r for r in self.rows}
            frames = [[dict(row, translation=[a + b for a, b in zip(rest[row['hash']]['translation'], row['translation'])],
                            rotation_xyzw=_quat_mul(rest[row['hash']]['rotation_xyzw'], row['rotation_xyzw'])
                            if row['rotation_xyzw'] else None,
                            scale=[a * b for a, b in zip(rest[row['hash']]['scale'], row['scale'])])
                       for row in frame if row['hash'] in rest] for frame in frames]
        inverse = np.stack(self.inverse)
        poses = []
        for frame in frames:
            skin = np.stack(worlds(self.rows, frame, self.inheritance)) @ inverse
            poses.append((BASIS @ skin @ BASIS.T).astype(np.float32))
        return dict(name=entry['name'], kind=kind, fps=float(clip['metadata']['sample_rate_fps'] or 30.0),
                    frames=poses, joint_count=len(self.rows))
