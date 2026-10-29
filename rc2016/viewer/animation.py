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
import hashlib
from pathlib import Path
import tempfile

import numpy as np

from rc2016.formats.animation_samples import decode_clip
from rc2016.formats.animation_transforms import sample_transforms
from rc2016.formats.archive import decompress
from rc2016.formats.clips import classify, ordered_containers
from rc2016.formats.animation_sources import (animset_rig_model, borrowed_animset, clip_fits_source, crc_string_names,
                                              retarget_frames, translation_retarget)
from rc2016.formats.animation_transforms import model_skeleton
from rc2016.formats.index import AssetIndex, open_asset_index, references
from rc2016.common import sha
from rc2016.formats.skeleton import bundled_profile, inheritance_rule, inspect_model, rest_inheritance_rule, worlds

# Native (x, y, z) -> preview (x, -z, y), the same basis as the geometry.
BASIS = np.array([[1.0, 0, 0, 0], [0, 0, -1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]])
PLAYABLE = ('absolute skeletal', 'partial absolute skeletal', 'relative/additive-style layer')
# AnimationLibrary.clip_notes: why a clip list may hide a clip. The others play on their own.
HIDDEN_NOTES = {'duplicate': 'a copy of another clip', 'layer': 'an additive layer (aim, head look, lean, twitch)',
                'unplayable': 'does not drive this skeleton'}


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


def actor_graph(archive):
    """Reference graph of every actor, as the pipeline's actor index builds it (decodes each actor once)."""
    index = AssetIndex(archive)
    index.actor_index()
    return index.graph


def borrowable_animsets(archive, graph, model_id, model_bytes, exclude=()):
    """Animsets the game pairs with another model whose rest rig matches this model, not already in ``exclude``.

    The rule of the pipeline's ``--borrow-animset``: ``borrowed_animset`` accepts an animset only when a model the
    game pairs it with has the same rest rig. Paths come back in archive order and are not decoded here."""
    rows = model_skeleton(model_bytes)
    candidates = []
    for key, refs in graph.items():
        if archive.entries[key]['type'] != 'actor':
            continue
        for ref in refs:
            for target in ref.get('resolved_ids') or ():
                if archive.entries[target]['type'] == 'animset':
                    candidates.append(archive.entries[target]['path'])
    found = []
    for path in dict.fromkeys(candidates):
        if path in exclude:
            continue
        try:
            borrowed_animset(archive, graph, path, rows, model_id)
        except (ValueError, KeyError):   # KeyError: a paired model without a joint section (a static prop)
            continue
        found.append(path)
    return found


class NotPlayable(ValueError):
    """A clip decoded but is classified as not driving this model's skeleton (unrelated or ambiguous)."""


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
            # Clips compose with ``inheritance``; the rest pose (glTF joint nodes) with ``rest_inheritance``, the rule
            # that reproduces the stored inverse binds. They differ only for a profile that names both (npc_sheep).
            self.inheritance = inheritance_rule(inspected['profile'])
            self.rest_inheritance = rest_inheritance_rule(inspected['profile']) if (
                inspected['profile'].get('rest_inheritance')) else self.inheritance
            self.names = {int(r['hash'], 16): r['name'] for r in self.rows}
            self.clips, self.animset_data = [], {}
            self._kinds = {}   # (animset, offset) -> first-frame classification; shared with copies
            # animset -> {model, joints}: the animset was built for a model of other proportions (Mr. Zurkon
            # and Zurkon Jr. play Evil Zurkon's set), so its absolute clips are moved onto this rig (translation_retarget).
            self.retarget = {}
            if not self.rows or len(self.inverse) != len(self.rows):
                return
            source_rigs = {}   # source model path -> translation_retarget mapping or None
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
                source = animset_rig_model(data) if containers else None
                if source and source not in source_rigs:
                    source_rigs[source] = self._source_retarget(archive, source, model_bytes)
                if source and source_rigs[source]:
                    self.retarget[animset] = dict(model=source, joints=source_rigs[source])
                file_names = None   # {name hash: string} of this animset, read for the first clip whose own name does not match
                for container in containers:
                    name = container['name']
                    metadata = container['metadata']
                    if not metadata.get('name_hash_matches_name'):
                        # The name the container reader found is another string (Mrs. Zurkon's version-143 clips read
                        # 'igAnimWrinkleBG'): use the string whose game CRC32 is the clip's name hash, as the cutscene clips do.
                        if file_names is None:
                            file_names = crc_string_names(data)
                        name = file_names.get(int(metadata['name_hash'], 16)) or name
                    self.clips.append(dict(animset=animset, offset=container['offset'], size=container['declared_size'],
                                           name=name))

    def _source_retarget(self, archive, source, model_bytes):
        """``translation_retarget`` from the model ``source`` names to this one; None for the same rig or no such model."""
        ids = [k for k in archive.resolve(source) if archive.entries[k]['type'] == 'model'] if archive else []
        if len(ids) != 1:
            return None
        data = archive.asset_data(ids[0])
        if data == model_bytes:
            return None
        try:
            return translation_retarget(self.rows, model_skeleton(data))
        except (ValueError, KeyError):   # KeyError: a model without a joint section
            return None

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
            other.clips.extend(dict(animset=source['path'], offset=c['offset'], size=c['declared_size'], name=c['name'],
                                    source=CINEMATIC_SOURCE) for c in clips)
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

    def clip_notes(self):
        """Per clip, in ``clips`` order: None for a clip that plays on its own, else a HIDDEN_NOTES key: 'duplicate'
        (the same bytes as an earlier clip, as when an animset repeats another one's clip), 'layer' (a relative or
        additive-style layer, which the preview shows on the rest pose) or 'unplayable'. The layer test is the
        pipeline's ``classify`` on the clip's first frame, so only that frame is decoded."""
        seen, notes = set(), []
        for clip in self.clips:
            data = self.animset_data[clip['animset']]
            digest = hashlib.sha1(data[clip['offset']:clip['offset'] + clip['size']]).digest() if clip.get('size') else None
            if digest in seen:
                notes.append('duplicate')
                continue
            if digest:
                seen.add(digest)
            key = (clip['animset'], clip['offset'])
            if key not in self._kinds:
                try:
                    start = decode_clip(data, clip['offset'], self.names, first_frames=1)
                    self._kinds[key] = classify(self.rows, sample_transforms(start))[0]
                except Exception:   # the full decode in pose() fails the same way
                    self._kinds[key] = 'unsupported'
            kind = self._kinds[key]
            notes.append('layer' if kind == 'relative/additive-style layer' else None if kind in PLAYABLE else 'unplayable')
        return notes

    def clip_frames(self, index):
        """(decoded clip, kind, frames) for clip ``index``: the source samples as this model plays them.

        Additive-style clips become standalone poses on the rest pose; absolute clips of an animset built for a
        model of other proportions get their translations moved onto this rig (``retarget``) when they sit on that
        model's rig (``clip_fits_source``). Raises NotPlayable
        for a clip that does not drive the skeleton."""
        entry = self.clips[index]
        clip = decode_clip(self.animset_data[entry['animset']], entry['offset'], self.names)
        frames = sample_transforms(clip)
        kind, _evidence = classify(self.rows, frames)
        if kind not in PLAYABLE:
            raise NotPlayable(f"'{entry['name']}' is {kind}; it does not drive this model's skeleton")
        if kind == 'relative/additive-style layer':
            rest = {r['hash']: r for r in self.rows}
            frames = [[dict(row, translation=[a + b for a, b in zip(rest[row['hash']]['translation'], row['translation'])],
                            rotation_xyzw=_quat_mul(rest[row['hash']]['rotation_xyzw'], row['rotation_xyzw'])
                            if row['rotation_xyzw'] else None,
                            scale=[a * b for a, b in zip(rest[row['hash']]['scale'], row['scale'])])
                       for row in frame if row['hash'] in rest] for frame in frames]
        elif entry['animset'] in getattr(self, 'retarget', {}):
            joints = self.retarget[entry['animset']]['joints']
            if frames and clip_fits_source(frames[0], joints):
                frames = retarget_frames(frames, joints)
        return clip, kind, frames

    def pose(self, index):
        """Per-frame skin matrices (joints x 4 x 4, preview basis) for clip ``index``."""
        entry = self.clips[index]
        clip, kind, frames = self.clip_frames(index)
        inverse = np.stack(self.inverse)
        poses = []
        for frame in frames:
            # Skin = clip world * stored inverse bind, as in the pipeline's Blender rig. Where the rest rule differs
            # from the clip rule the stored bind is not undone exactly by a clip frame at the rest pose; that
            # residue (npc_sheep: 2.05e-3 on three right-arm joints) is kept, not corrected.
            skin = np.stack(worlds(self.rows, frame, self.inheritance)) @ inverse
            poses.append((BASIS @ skin @ BASIS.T).astype(np.float32))
        return dict(name=entry['name'], kind=kind, fps=float(clip['metadata']['sample_rate_fps'] or 30.0),
                    frames=poses, joint_count=len(self.rows))
