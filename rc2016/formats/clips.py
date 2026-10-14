"""All embedded clips, exact samples, explicit classification and unsupported rows."""
import math
import statistics
import struct
from rc2016.formats.container import inspect, read_container
from rc2016.formats.animation_samples import decode_clip
from rc2016.formats.animation_transforms import sample_transforms
from rc2016.common import dump, read, sha, write, zip_json
from rc2016.formats.skeleton import worlds
from rc2016.formats.animation_sources import crc32
import numpy as np


def worlds_batch(rows, frames, inheritance='full'):
    """``skeleton.worlds`` for many frames at once: (F, J, 4, 4) float64, frame f equal to
    ``worlds(rows, frames[f], inheritance)`` (pass ``[()]`` for the rest pose). The loop is over joints only."""
    count = len(rows)
    rest_s = np.array([r['scale'] for r in rows], float).reshape(count, 3)
    rest_q = np.array([r['rotation_xyzw'] for r in rows], float).reshape(count, 4)
    rest_t = np.array([r['translation'] for r in rows], float).reshape(count, 3)
    n = len(frames)
    S, Q, T = np.empty((n, count, 3)), np.empty((n, count, 4)), np.empty((n, count, 3))
    positions = {}
    for f, frame in enumerate(frames):
        if not len(frame):
            S[f], Q[f], T[f] = rest_s, rest_q, rest_t
            continue
        hashes = tuple(r['hash'] for r in frame)
        pos = positions.get(hashes)
        if pos is None:
            where = {h: i for i, h in enumerate(hashes)}   # last duplicate wins, like worlds()
            pos = positions[hashes] = np.array([where.get(r['hash'], -1) for r in rows], np.intp)
        have = (pos >= 0)[:, None]
        pos = np.maximum(pos, 0)
        fq = [r['rotation_xyzw'] for r in frame]
        has_q = np.array([bool(q) for q in fq])[pos][:, None] & have
        fq = np.array([q or (0., 0., 0., 1.) for q in fq], float)
        S[f] = np.where(have, np.array([r['scale'] for r in frame], float)[pos], rest_s)
        T[f] = np.where(have, np.array([r['translation'] for r in frame], float)[pos], rest_t)
        Q[f] = np.where(has_q, fq[pos], rest_q)
    x, y, z, w = (Q / np.linalg.norm(Q, axis=2, keepdims=True)).transpose(2, 0, 1)
    R = np.empty((n, count, 3, 3))
    R[..., 0, 0], R[..., 0, 1], R[..., 0, 2] = 1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)
    R[..., 1, 0], R[..., 1, 1], R[..., 1, 2] = 2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)
    R[..., 2, 0], R[..., 2, 1], R[..., 2, 2] = 2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)
    R *= S[:, :, None, :]   # R @ diag(S)
    ssc = inheritance == 'segment_scale_compensation_bit0'
    out = np.empty((n, count, 4, 4))
    for j, r in enumerate(rows):
        m = np.zeros((n, 4, 4)); m[:, 3, 3] = 1.0
        m[:, :3, :3] = R[:, j]; m[:, :3, 3] = T[:, j]
        parent = r['parent']
        if parent >= 0:
            if ssc and r['flags'] & 1:
                ps = S[:, parent]
                inv = np.divide(1.0, ps, out=np.zeros_like(ps), where=ps != 0)
                m[:, :3, :3] *= inv[:, :, None]   # diag(1/parent scale) @ basis
            m = out[:, parent] @ m
        out[:, j] = m
    return out


def classify(rows, frames):
    rest = {r['hash']:r for r in rows}
    matched = [r for r in frames[0] if r['hash'] in rest]
    if not matched:
        return 'unrelated', dict(reason='No source joint hash matches the selected model')
    extent = max(math.dist(r['translation'],[0,0,0]) for r in rows)
    ratios = [math.dist(r['translation'],[0,0,0])/math.dist(rest[r['hash']]['translation'],[0,0,0])
              for r in matched if rest[r['hash']]['parent'] >= 0 and math.dist(rest[r['hash']]['translation'],[0,0,0]) > max(extent*1e-4,1e-12)]
    median = statistics.median(ratios) if ratios else None
    evidence = dict(translation_rest_median_ratio=median,evidence_joints=len(ratios),
        classification_basis='Source translation/rest-length ratio; heuristic, not a recovered runtime layer flag',
        classification_ambiguous=False)
    if median is not None and median <= .05:
        return 'relative/additive-style layer', evidence
    if median is not None and median >= .9:
        return ('absolute skeletal' if len(matched)==len(rows) else 'partial absolute skeletal'), evidence
    evidence.update(reason='Insufficient or intermediate translation evidence; hash-bound classification override required',classification_ambiguous=True)
    return 'unsupported', evidence


def family(name, kind):
    if kind == 'relative/additive-style layer': return 'additive'
    if kind == 'partial absolute skeletal': return 'partial'
    name = name.lower()
    for group,words in [('locomotion',('walk','run','sprint','strafe','move')),('jumps',('jump','fall','land','leap')),
                        ('attacks',('attack','fire','shoot','swing','reload')),('attachments',('attach','deploy','pack'))]:
        if any(w in name for w in words): return group
    return 'other_families'


# Default when FULL and SSC0 both fit the inverse binds but diverge on animated scale and
# no hash-bound profile chooses. See Research/AnimationCoverage/Inheritance/SSC0Generalization.
SSC0_DEFAULT_EVIDENCE = (
    'Engine reference-pose builder (eboot 0x193a410) tests joint-record byte +6 bit 0 and folds the '
    'parent local scale into only those joints, so the runtime compensates parent scale for bit-0 '
    'joints; Toxic Crab stretchy-IK chains match SSC0; Large Amoeboid bit-0 joints shear to '
    'near-degenerate bases under FULL (max 1.0) but stay orthogonal under SSC0 in most clips. '
    'Exact compensation order for non-uniform parent scale is not capture-verified.')


def experimental_inheritance_preview(entry, candidates, difference, enabled=False,
                                     working_rule='segment_scale_compensation_bit0'):
    """Admit only decoded, source-related inheritance-ambiguous clips as previews.

    This does not select or prove a game rule. The caller can bake both
    candidates while keeping the source class and exact provenance.
    """
    if not enabled or working_rule != 'segment_scale_compensation_bit0':
        return None
    if len(candidates) != 2 or set(candidates) != {'full', 'segment_scale_compensation_bit0'}:
        return None
    if not math.isfinite(float(difference)) or float(difference) < 1e-7:
        return None
    source_kind = entry.get('source_kind', entry.get('kind'))
    if entry.get('sample_roundtrip_exact') is not True or source_kind not in {
            'absolute skeletal', 'partial absolute skeletal', 'relative/additive-style layer'}:
        return None
    result = dict(entry)
    result.update(kind=source_kind, status='included', inheritance=working_rule,
        experimental=True, experimental_inheritance_preview=True,
        experimental_inheritance_candidates=list(candidates),
        experimental_selected_inheritance=working_rule,
        experimental_inheritance_difference=float(difference),
        experimental_game_accuracy='unproven',
        experimental_reason=('Full and segment-scale-compensation candidates diverge on animated scales; '
                             'both are experimental and SSC0 is the preferred working preview, not a game-runtime claim.'),
        selector_label=f"{entry['name']} | EXPERIMENTAL SSC0")
    return result


def ordered_containers(path):
    audit = inspect(path)
    containers = {c['offset']:c for c in audit['containers']}
    data = read(path)
    ordered, seen = [], set()
    def visit(c):
        if c['offset'] in seen: raise ValueError('Duplicate/cyclic animation directory target')
        seen.add(c['offset'])
        if c['metadata']:
            ordered.append(c); return
        sections = {s['type_name']:s for s in c['sections'] if s['type_name']}
        lookup, payload = sections.get('anim_clip_lookup'),sections.get('anim_clip_data')
        if lookup and payload:
            import struct
            if lookup['size']%16: raise ValueError('Misaligned animation lookup')
            for pos in range(lookup['offset'],lookup['offset']+lookup['size'],16):
                h,relative = struct.unpack_from('<II',data,pos)
                target = payload['offset']+relative
                if target not in containers or target+containers[target]['declared_size']>payload['offset']+payload['size']:
                    raise ValueError('Animation lookup target outside known payload')
                child = containers[target]
                if child['metadata'] and child['metadata']['name_hash'] != f'0x{h:08X}':
                    raise ValueError('Directory/clip name hash mismatch')
                visit(child)
        else:
            # Nested wrapper layout can lack its own directory. Preserve physical archive order explicitly.
            children=[v for v in containers.values() if v['metadata'] and c['offset']<v['offset'] and v['offset']+v['declared_size']<=c['offset']+c['declared_size']]
            for child in sorted(children,key=lambda x:x['offset']): visit(child)
    if 0 not in containers: raise ValueError('Missing root animation container')
    visit(containers[0])
    other = [c for c in containers.values() if c['metadata'] and c['offset'] not in seen]
    if other: raise ValueError('Unaccounted embedded clips outside animation directory')
    return ordered, audit['errors']


def extract_set(path, rows, cache, profile, log, experimental_ambiguous_inheritance=False, clip_names=None):
    """Every embedded clip of an animset in directory order, decoded, classified and cached.

    ``clip_names`` ({container offset: name}) restricts extraction to those clips and names them, for
    containers without an 'animclip built' string (cutscenes); each name must hash (game CRC32) to the
    clip's own name hash. Directory order indices stay those of the whole source file.
    """
    data = read(path); containers, errors = ordered_containers(path)
    names = {int(r['hash'],16):r['name'] for r in rows}
    entries = []
    for order,c in enumerate(containers):
        if clip_names is not None and c['offset'] not in clip_names: continue
        name = c['name'] if clip_names is None else clip_names[c['offset']]
        raw = data[c['offset']:c['offset']+c['declared_size']]
        digest = sha(raw); stem=f'{order:05d}_{digest[:12]}'
        raw_path=cache/(stem+'.animclip'); sample_path=cache/(stem+'.json.gz')
        write(raw_path,raw)
        entry=dict(order=order,name=name,name_hash=c['metadata']['name_hash'],offset=c['offset'],
            raw_sha256=digest,raw_clip=str(raw_path),animset_sha256=sha(data),metadata=c['metadata'])
        try:
            clip=decode_clip(data,c['offset'],names)
            if clip_names is None:
                if not clip['metadata'].get('name_hash_matches_name'): raise ValueError('Clip name/hash mismatch')
            else:
                if crc32(name) != int(c['metadata']['name_hash'],16): raise ValueError('Clip name/hash mismatch')
                clip['name']=name
            frames=sample_transforms(clip)
            kind,evidence=classify(rows,frames)
            override=profile.get('classification_overrides',{}).get(digest)
            if override:
                if override not in ('absolute skeletal','partial absolute skeletal','relative/additive-style layer','unrelated'):
                    raise ValueError('Invalid classification override')
                kind=override; evidence['profile_override']=True; evidence['classification_ambiguous']=False
            source_kind=kind
            unsampled=[r['hash'] for r in rows if r['hash'] not in {x['hash'] for x in frames[0]}]
            entry.update(kind=kind,source_kind=source_kind,**evidence,frames=len(frames),fps=clip['metadata']['sample_rate_fps'],
                source_duration_seconds=clip['metadata']['duration_seconds'],sample_sha256=clip['sample_sha256'],
                sample_roundtrip_exact=clip['sample_roundtrip_exact'],unsampled_model_hashes=unsampled,
                unmatched_hashes=[r['hash'] for r in frames[0] if r['name'] is None],
                collapsed_rotation_hashes=sorted({r['hash'] for f in frames for r in f if r['rotation_sentinel']}),
                samples=str(sample_path),family=family(clip['name'],kind),
                preview_note=('Standalone rest T + delta T, rest Q * delta Q, rest S * delta S. Runtime blend partner/weight unresolved.'
                    if kind=='relative/additive-style layer' else 'Absolute source SQT; unsampled model joints use source rest.'))
            zip_json(sample_path,dict(clip=clip,frames=frames))
            entry['status']='included' if kind not in ('unsupported','unrelated') else kind
            # Infer a rule only when candidates are actually equivalent over every sample.
            if entry['status']=='included' and not profile.get('inheritance'):
                candidates=profile['inheritance_candidates']
                if len(candidates)==2:
                    a=worlds_batch(rows,frames,'full');b=worlds_batch(rows,frames,'segment_scale_compensation_bit0')
                    worst=float(np.max(np.abs(a-b))) if a.size else 0.
                    if worst < 1e-7:
                        entry['inheritance']='full'; entry['inheritance_evidence']='Both inverse-bind-compatible rules are numerically equivalent on every source sample'
                    else:
                        preview=experimental_inheritance_preview(dict(entry,inheritance_candidate_difference=worst),
                            candidates,worst,enabled=experimental_ambiguous_inheritance)
                        if preview:
                            entry.clear();entry.update(preview)
                        else:
                            entry.update(inheritance='segment_scale_compensation_bit0',
                                inheritance_basis='pipeline_default_ssc0',
                                inheritance_evidence=SSC0_DEFAULT_EVIDENCE,
                                inheritance_candidate_difference=worst)
                else:
                    entry.update(status='unsupported',kind='unsupported',reason='Model inverse binds do not establish an inheritance rule; profile required')
            else:
                entry['inheritance']=profile.get('inheritance')
        except (ValueError,KeyError,IndexError,OverflowError,struct.error) as exc:
            message=str(exc)
            unsupported=any(word in message for word in ('not validated','unsupported','only PS4','unknown SQT'))
            entry.update(status='unsupported' if unsupported else 'malformed',kind='unsupported' if unsupported else 'malformed',reason=message)
        entries.append(entry)
        if order%50==0: log('clip_decode',order=order,name=entry['name'],status=entry['status'])
    return entries,errors
