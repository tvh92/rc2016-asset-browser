"""Bit-0 scale-compensation audit: rest-pose fits and animated-basis cleanliness per rule (read-only).

python -m rc2016.research.inheritance_audit <game or asset_archive root> --out <folder>

Two measurements for the three readings of joint-record bit 0 (``full``, SSC0 ``diag(1/parent S) R S`` and the
profile-only after-rotation ``R diag(S/parent S)``):

* ``rest``: the largest inverse-bind error of every model that carries inverse binds, per rule.
* ``clips``: for every absolute clip of the animsets the game pairs with a rig that has bit-0 joints, how far
  the world basis of those joints is from orthogonal (largest |cos| between basis columns) and from
  non-degenerate (condition number) under SSC0 and under the after-rotation rule.

Rest fits say which operand order reproduces the stored bind pose. The clip measurement is plausibility, not a
decode: a compensation is meant to cancel the parent's scale, so a rule that shears ordinary walk and idle
loops is unlikely to be what the authors saw. Archive bytes are read in memory; nothing is written outside ``--out``.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from rc2016.common import dump
from rc2016.formats.animation_samples import decode_clip
from rc2016.formats.animation_sources import clip_directory
from rc2016.formats.animation_transforms import sample_transforms
from rc2016.formats.archive import Archive, locate
from rc2016.formats.clips import classify, worlds_batch
from rc2016.formats.skeleton import AFTER_ROTATION, INVERSE_BIND_TOLERANCE, SSC0, inspect_model, inverse_bind_error
from rc2016.research.npc_audit import MemoryIndex

RULES = ('full', SSC0, AFTER_ROTATION)


def basis_shear(world, joints):
    """(largest |cos| between the columns of any listed joint's world basis, largest condition number)."""
    basis = np.asarray(world)[:, joints, :3, :3]
    norms = np.linalg.norm(basis, axis=2)
    unit = basis / np.where(norms[:, :, None, :] > 1e-9, norms[:, :, None, :], 1.0)
    gram = np.einsum('fjik,fjil->fjkl', unit, unit)
    cosine = np.maximum(np.maximum(abs(gram[..., 0, 1]), abs(gram[..., 0, 2])), abs(gram[..., 1, 2]))
    singular = np.linalg.svd(basis, compute_uv=False)
    return float(cosine.max()), float((singular[..., 0] / np.maximum(singular[..., 2], 1e-12)).max())


def bit0_joints(rows):
    return [i for i, r in enumerate(rows) if r['flags'] & 1 and r['parent'] >= 0]


def rest_row(path, rows, inverses):
    row = dict(path=path, joints=len(rows), bit0_joints=len(bit0_joints(rows)))
    row['bit0_under_non_unit_parent'] = sum(
        1 for i in bit0_joints(rows) if max(abs(x - 1) for x in rows[rows[i]['parent']]['scale']) > 1e-4)
    row['errors'] = {rule: inverse_bind_error(rows, inverses, rule) for rule in RULES}
    return row


def clip_rows(rows, data, names, joints):
    result = []
    for clip in clip_directory(data):
        try:
            frames = sample_transforms(decode_clip(data, clip['offset'], names))
            if classify(rows, frames)[0] not in ('absolute skeletal', 'partial absolute skeletal'):
                continue
            ssc, after = worlds_batch(rows, frames, SSC0), worlds_batch(rows, frames, AFTER_ROTATION)
            ssc_cos, ssc_cond = basis_shear(ssc, joints)
            after_cos, after_cond = basis_shear(after, joints)
            result.append(dict(name=clip['name'], divergence=float(np.max(np.abs(ssc - after))),
                               ssc0_cos=ssc_cos, ssc0_cond=ssc_cond, after_cos=after_cos, after_cond=after_cond))
        except (ValueError, KeyError, IndexError, OverflowError):
            continue
    return result


def audit(archive, log=print):
    index = MemoryIndex(archive)
    index.actor_index()
    models = sorted((e for e in archive.entries.values() if e['type'] == 'model'), key=lambda e: e['path'])
    folder = Path(tempfile.mkdtemp(prefix='rc2016-inheritance-'))
    rest, clips = [], []
    for number, entry in enumerate(models, 1):
        if number % 500 == 0:
            log(f'[{number}/{len(models)}] {entry["path"]}')
        file = folder / 'model.model'
        file.write_bytes(archive.asset_data(entry['id']))
        try:
            inspected = inspect_model(file)
        except ValueError:
            continue
        rows, inverses = inspected['skeleton'], [np.array(m) for m in inspected['inverse_bind_matrices']]
        if not rows or len(inverses) != len(rows):
            continue
        rest.append(rest_row(entry['path'], rows, inverses))
        joints = bit0_joints(rows)
        if not joints:
            continue
        names = {int(r['hash'], 16): r['name'] for r in rows}
        found = []
        for animset in index.related(entry['id'])['animsets']:
            found.extend(clip_rows(rows, archive.asset_data(animset), names, joints))
        clips.append(dict(path=entry['path'], bit0_joints=len(joints), clips=found))
    return dict(schema='rc2016-inheritance-audit-v1', tolerance=INVERSE_BIND_TOLERANCE, rest=rest, rigs=clips)


def summary(result):
    """Counts the decision rests on."""
    tolerance, rest = result['tolerance'], result['rest']
    fits = {rule: sum(1 for r in rest if r['errors'][rule] is not None and r['errors'][rule] < tolerance) for rule in RULES}
    clips = [(rig, c) for rig in result['rigs'] for c in rig['clips'] if c['divergence'] > 1e-6]
    medians = {}
    for rig in result['rigs']:
        diverging = [c for c in rig['clips'] if c['divergence'] > 1e-6]
        if diverging:
            medians[rig['path']] = (float(np.median([c['ssc0_cos'] for c in diverging])),
                                    float(np.median([c['after_cos'] for c in diverging])))
    return dict(
        models_with_inverse_binds=len(rest), rest_fits=fits,
        rest_worst_after_rotation=max((r['errors'][AFTER_ROTATION] for r in rest if r['errors'][AFTER_ROTATION] is not None), default=None),
        rest_rigs_where_after_rotation_is_strictly_better=sorted(
            r['path'] for r in rest if r['errors'][SSC0] is not None and r['errors'][AFTER_ROTATION] is not None
            and r['errors'][SSC0] > 1e-4 and r['errors'][AFTER_ROTATION] < 1e-4),
        diverging_clips=len(clips), diverging_rigs=len(medians),
        clips_ssc0_less_sheared=sum(1 for _, c in clips if c['after_cos'] > c['ssc0_cos'] + 1e-3),
        clips_after_rotation_less_sheared=sum(1 for _, c in clips if c['ssc0_cos'] > c['after_cos'] + 1e-3),
        clips_after_rotation_condition_over_100=sum(1 for _, c in clips if c['after_cond'] > 100),
        clips_ssc0_condition_over_100=sum(1 for _, c in clips if c['ssc0_cond'] > 100),
        rigs_ssc0_median_less_sheared=sum(1 for s, a in medians.values() if s < a - 1e-3),
        rigs_after_rotation_median_less_sheared=sum(1 for s, a in medians.values() if a < s - 1e-3))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('root', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    out, root = args.out.resolve(), args.root.resolve()
    if out.is_relative_to(repo) or out.is_relative_to(root):
        parser.error('--out must be outside the repository and source root')
    out.mkdir(parents=True, exist_ok=True)
    archive = Archive(locate(root), out / 'archive_scratch', lambda *a, **k: None)
    archive.verify_repeats = False
    result = audit(archive, log=lambda text: print(text, file=sys.stderr, flush=True))
    dump(out / 'inheritance_audit.json', result)
    print(json.dumps(summary(result), indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
