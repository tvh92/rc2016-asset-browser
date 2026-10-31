"""One-command orchestration. All runtime output stays below OutputRoot."""
import argparse
import copy
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from rc2016.pipeline import VERSION
from rc2016.formats import archive as archive_module
from rc2016.formats.archive import Archive,locate
from rc2016.formats.clips import extract_set
from rc2016.formats.animation_sources import (CINEMATIC_LIMITATION,CINEMATIC_SOURCE,animset_rig_model,borrowed_animset,
    cinematic_sources,clip_fits_source,translation_retarget)
from rc2016.formats.animation_transforms import model_skeleton
from rc2016.pipeline.clip_selection import ClipSelectionError, resolve_clip_selection
from rc2016.common import dump,ensure_separate,extended,file_hash,load,read,safe_name,sha,unzip_json,write,promote
from rc2016.formats.index import AssetIndex
from rc2016.pipeline.projectile_mounts import idle_offsets
from rc2016.pipeline.lighting import NOVALIS_REGION,inventory_lighting,prepare_game_lighting
from rc2016.formats.skeleton import inspect_model,rest_inheritance_rule
from rc2016.formats.native_model import UnsupportedModelError,inspect_native_model
from rc2016.formats.materials import prepare_materials,preflight_texture_output
from rc2016.formats.texture_transport import image_interpretation_key,image_interpretation_identity


class Log:
    def __init__(self,path):
        self.file=open(path,'a',encoding='utf-8');self.sequence=0
    def __call__(self,event,**values):
        self.sequence+=1
        line=json.dumps(dict(sequence=self.sequence,event=event,**values),sort_keys=True)
        self.file.write(line+'\n');self.file.flush()
        if event not in ('decoded',):print(line,flush=True)


# Regenerable intermediates of an output root: raw/decompressed copies of archive entries
# (Archive.decode rewrites a missing file) and the Blender candidate/checkpoint files that
# promote() copies into each model folder, and the converted DDS under cache/t (packed into the
# blend during the build). Together they were ~85% of a run (Blarg: 0.8 of 1.07 GB).
# Delivered model folders, reports, hashes and the asset index are kept.
INTERMEDIATE_DIRS = ('blender', 'compressed_assets', 'decoded_sources', 'work/tmp', 'cache/t')


def prune_intermediates(output_root, log):
    freed = 0
    # Extended-length form on Windows: unpacked asset names can push paths past 260 characters.
    root = Path('\\\\?\\'+str(Path(output_root).resolve())) if os.name == 'nt' else Path(output_root)
    for name in INTERMEDIATE_DIRS:
        folder = root/name
        if not folder.is_dir():
            continue
        for child in list(folder.iterdir()):
            if child.is_dir():
                freed += sum(f.stat().st_size for f in child.rglob('*') if f.is_file())
                shutil.rmtree(child)
            else:
                freed += child.stat().st_size
                child.unlink()
    log('intermediates_pruned', folders=list(INTERMEDIATE_DIRS), bytes=freed)


# Working blends (candidates, checkpoints, assembly inputs) live in these folders; deliveries do not.
WORKING_BLEND_DIRS = ('blender','variant_assemblies','work','cache','compressed_assets','decoded_sources')


# The reference index the viewer reads to find each model's animsets (viewer/animation.model_animsets).
# Without it every model in the folder loses its animation list, so blend-only output keeps these two files.
VIEWER_INDEX_FILES = ('reports/asset_index.json','reports/asset_references.sqlite')
# Also kept by a blend-only cleanup: the browser's model list and the actor reference index (rebuilding it decodes
# every actor of the archive on the next run).
KEPT_FILES = VIEWER_INDEX_FILES+('reports/gui_model_index.json','reports/gui_scan_stamp.json')
KEPT_FOLDERS = ('cache/actor_index',)
LOG_FOLDER = 'reports/logs'


def keep_only_blends(output_root, keep_logs=False):
    """Blend-only output: delete the pipeline's files below OutputRoot except delivered .blend files (each model's
    <Model>.blend, final/*.blend and groups/*.blend), including deliveries from earlier runs in the same folder, and
    except KEPT_FILES and KEPT_FOLDERS. ``keep_logs`` also keeps LOG_FOLDER: the pipeline log and the Blender logs
    of a run that had errors. Folders the pipeline does not write are left alone: hidden ones (the browser's
    thumbnails in .cache) and portable exports (a folder holding export.json)."""
    def long_path(path):
        path=str(Path(path).resolve())
        return '\\\\?\\'+path if os.name=='nt' and not path.startswith('\\\\?\\') else path
    root=long_path(output_root);removed=0
    kept_folders=KEPT_FOLDERS+((LOG_FOLDER,) if keep_logs else ())
    for folder,dirs,files in os.walk(root):
        relative_folder=os.path.relpath(folder,root).replace(os.sep,'/')
        if relative_folder=='.':
            dirs[:]=[name for name in dirs if not name.startswith('.')]
        elif 'export.json' in files or any(relative_folder==k or relative_folder.startswith(k+'/') for k in kept_folders):
            dirs[:]=[];continue
        top=relative_folder.split('/')[0]
        for name in files:
            relative=name if relative_folder=='.' else relative_folder+'/'+name
            if relative in KEPT_FILES:
                continue
            working=top in WORKING_BLEND_DIRS or 'candidate' in name.lower() or 'checkpoint' in name.lower()
            if working or not name.lower().endswith('.blend'):
                os.remove(os.path.join(folder,name));removed+=1
    return removed


def lz4_warning():
    """A loud warning (never a failure) when only the slow pure-Python LZ4 decoder is available."""
    if archive_module._lz4_c is not None:
        return None
    return ('The lz4 package is not installed for '+sys.executable+'; archive and texture streams use the '
        'pure-Python LZ4 decoder, which is much slower. Install it with: "'+sys.executable+'" -m pip install lz4')


def archive_file_stamps(root):
    """[size, mtime_ns] of every file directly in the archive folder (metadata and segments)."""
    stamps={}
    with os.scandir(extended(root)) as entries:
        for entry in entries:
            if entry.is_file():
                stat=entry.stat();stamps[entry.name]=[stat.st_size,stat.st_mtime_ns]
    return stamps


def record_verified_sources(archive):
    """Remember the source hash of every asset read this run (Archive.decode reads through bytes())."""
    verified={};read_bytes=archive.bytes
    def recording(key):
        data,digest=read_bytes(key);verified[key]=digest
        return data,digest
    archive.bytes=recording
    return verified


def sources_unchanged_since_start(archive,keys,start_stamps,verified):
    """True when the archive files are unchanged since the run began and every mapped key was
    hashed this run with its recorded hash; then the promotion re-read is not needed."""
    if start_stamps is None or archive_file_stamps(archive.root)!=start_stamps:
        return False
    return all(key not in archive.mapping or ('loose_path' not in archive.entries[key]
               and verified.get(key)==archive.mapping[key]['source_sha256']) for key in keys)


ACTOR_INDEX_SCHEMA='rcps4-actor-reference-index-v1'


def load_or_build_actor_index(index,archive,output_root,stamps,log):
    """Reuse the actor reference graph across runs while the archive, layout and index code
    are unchanged (stamp); otherwise decode every actor once and store the graph."""
    stamp=sha(json.dumps(dict(schema=ACTOR_INDEX_SCHEMA,layout=archive.layout_identity,metadata=archive.metadata,
        index_source=file_hash(ROOT/'rc2016/formats/index.py'),archive_files=stamps),sort_keys=True).encode())
    folder=Path(output_root)/'cache'/'actor_index';path=folder/(stamp[:24]+'.json')
    actors={key for key,entry in archive.entries.items() if entry['type']=='actor'}
    try:cached=load(path) if path.is_file() else None
    except (OSError,ValueError):cached=None
    if (isinstance(cached,dict) and cached.get('stamp')==stamp and isinstance(cached.get('graph'),dict)
            and set(cached['graph'])==actors):
        index.graph.update(cached['graph']);index.errors.extend(cached.get('errors',[]))
        log('actor_reference_index_reused',actors=len(actors),stamp=stamp[:24])
        return True
    index.actor_index()
    try:
        folder.mkdir(parents=True,exist_ok=True)
        dump(path,dict(stamp=stamp,graph={key:index.graph[key] for key in actors if key in index.graph},
            errors=[e for e in index.errors if e.get('asset_id') in actors]))
        for old in folder.glob('*.json'):
            if old!=path:old.unlink()
    except OSError:pass
    return False


def remove_empty_dirs(root):
    """Delete empty folders below ``root`` (deepest first); runs recreate the ones they need."""
    for folder,_dirs,_files in sorted(os.walk(root),key=lambda row:-len(row[0])):
        if Path(folder)!=Path(root):
            try:os.rmdir(folder)
            except OSError:pass


def run_selected_models(selection, manifest, process, checkpoint, save_manifest, log, model_name=lambda key: key):
    """Run each selected model independently, persisting a result after each."""
    for key in selection:
        log('model_started', model=model_name(key))
        try:
            manifest['models'][key] = process(key)
        except UnsupportedModelError as exc:
            result = dict(status='UNSUPPORTED', model=model_name(key), reason=str(exc))
            manifest['models'][key] = result
            log('model_unsupported', **result)
        except Exception as exc:
            error = dict(model=model_name(key), error=str(exc), traceback=traceback.format_exc())
            manifest['errors'].append(error)
            log('model_failed', **error)
            manifest['models'][key] = dict(status='FAILED', **error)
        checkpoint()
        save_manifest()


def verified_cache_hit(old, fingerprint, final, validation_path):
    """Check the blend and validation report (and any preview hash an older report recorded)."""
    if (old.get('fingerprint') != fingerprint
            or old.get('final_sha256') != file_hash(final)
            or old.get('validation_sha256') != file_hash(validation_path)):
        return False
    validation = load(validation_path)
    return validation.get('status')=='passed' and all(Path(p['path']).is_file() and file_hash(p['path']) == p['sha256']
               for p in validation.get('previews') or [])


def verified_source_model_reuse_after_orchestration_change(old,fingerprint,final,validation_path,
        model_root,archive,key,previous_code,current_code):
    """Reuse pinned Fusion inputs when only assembly orchestration code changed.

    Builder, geometry, materials, decoder, animation and defaults code must be
    byte-identical. Every indexed dependency is re-hashed against the archive
    and its decoded cache, and the saved blend/reopen report must still verify.
    """
    if key not in {'09e11ce58f6ade64fd6e','e39cd2d0cd8d3632c0fa','7ad48452acac82c9edee','7699113e93128f485d10','86f736dced71288e7503','9ac3173ba918be13292b','d70fd1cb02acc9650b6b','7a795a33a3d2c78edd33','35fc18518fd23793517a','568ebae49373ef2060ed','a88557a5223d580f11bd'}:
        return False
    if not previous_code or not current_code:
        return False
    normalized_old={str(k).replace('\\','/').lower():v for k,v in previous_code.items()}
    normalized_new={str(k).replace('\\','/').lower():v for k,v in current_code.items()}
    changed={name for name in set(normalized_old)|set(normalized_new) if normalized_old.get(name)!=normalized_new.get(name)}
    # Per-weapon scripts are bound by their own contract hashes.
    allowed={'rc2016/pipeline/run.py'}
    if not changed or not all(name in allowed or name.startswith('rc2016/pipeline/weapons/') for name in changed):
        return False
    validation_path=Path(validation_path);final=Path(final)
    candidate=dict(old);candidate['fingerprint']=fingerprint
    if not verified_cache_hit(candidate,fingerprint,final,validation_path):
        return False
    manifest_path=Path(model_root)/'decoded_sources'/'manifest.json'
    if not manifest_path.is_file():return False
    try:assets=load(manifest_path).get('assets',[])
    except Exception:return False
    if not assets:return False
    seen=set()
    for row in assets:
        asset_id=row.get('asset_id')
        if not asset_id or asset_id in seen or asset_id not in archive.entries:return False
        seen.add(asset_id)
        entry=archive.entries[asset_id]
        if row.get('path')!=entry['path']:return False
        try:
            _data,source_hash=archive.bytes(asset_id)
            decoded=archive.decode(asset_id)
        except Exception:return False
        if source_hash!=row.get('source_sha256') or file_hash(decoded)!=row.get('decoded_sha256'):
            return False
    return key in seen


def select_models(index, entries, selectors=(), all_models=False):
    """Resolve repeated exact selectors through the same archive index as CLI."""
    if all_models:
        return [entry['id'] for entry in entries.values() if entry['type'] == 'model']
    return list(dict.fromkeys(key for selector in selectors for key in index.select(selector)))


def animation_coverage(entries, animset_errors=()):
    """Summarize clip-level source classes without implying runtime invocation."""
    count_names=('source_related_partial_clips','included_partial_actions','unsupported_partial_clips',
        'source_related_additive_clips','included_additive_actions','unsupported_additive_clips',
        'experimental_inheritance_clips','experimental_inheritance_actions',
        'experimental_partial_clips','experimental_additive_clips',
        'unrelated_clips','clip_decoder_failures','unsupported_unclassified_clips',
        'unclassified_clips','animset_decoder_or_container_failures','source_related_runtime_invocation_unknown')
    counts=Counter({name:0 for name in count_names});reasons=Counter();invocation_unknown=0
    for entry in entries:
        kind=entry.get('kind');status=entry.get('status');family=entry.get('family')
        reason=entry.get('reason')
        if reason and status!='included':reasons[str(reason)]+=1
        if kind=='unrelated' or status=='unrelated':
            counts['unrelated_clips']+=1
            continue
        # extract_set assigns the family before its inheritance preflight can
        # replace `kind` with `unsupported`; retain that source-family evidence.
        if family=='partial' or kind in ('absolute skeletal','partial absolute skeletal'):
            counts['source_related_partial_clips']+=1;invocation_unknown+=1
            if status=='included':counts['included_partial_actions']+=1
            else:counts['unsupported_partial_clips']+=1
            if entry.get('experimental'):
                counts['experimental_inheritance_clips']+=1
                counts['experimental_inheritance_actions']+=len(entry.get('experimental_action_names',[]))
                counts['experimental_partial_clips']+=1
            continue
        if family=='additive' or kind=='relative/additive-style layer':
            counts['source_related_additive_clips']+=1;invocation_unknown+=1
            if status=='included':counts['included_additive_actions']+=1
            else:counts['unsupported_additive_clips']+=1
            if entry.get('experimental'):
                counts['experimental_inheritance_clips']+=1
                counts['experimental_inheritance_actions']+=len(entry.get('experimental_action_names',[]))
                counts['experimental_additive_clips']+=1
            continue
        reason_text=str(reason or '').lower()
        decoder_reason=any(token in reason_text for token in ('codec','decode','channel','format','version','packed','sample stride'))
        if status=='malformed' or entry.get('sample_roundtrip_exact') is False or (status=='unsupported' and family is None and decoder_reason):
            counts['clip_decoder_failures']+=1
        elif status=='unsupported' or kind in ('unsupported','malformed'):
            counts['unsupported_unclassified_clips']+=1
        else:
            counts['unclassified_clips']+=1

    animset_reason_counts=Counter()
    for error in animset_errors:
        reason=error.get('reason') or '; '.join(str(x) for x in error.get('errors',[])) or error.get('status') or 'unspecified animset extraction error'
        animset_reason_counts[str(reason)]+=1
    counts['animset_decoder_or_container_failures']=sum(animset_reason_counts.values())
    counts['source_related_runtime_invocation_unknown']=invocation_unknown
    return dict(schema='ps4-animation-coverage-v1',counts=dict(sorted(counts.items())),
        unsupported_clip_reason_counts=dict(sorted(reasons.items())),
        animset_error_reason_counts=dict(sorted(animset_reason_counts.items())),
        runtime_invocation='unknown for every source-related clip; actor references establish source relation, not runtime selection',
        source_relation_note='Partial/additive source classes are derived from extract_set evidence and remain distinct from unrelated clips and decode failures.')


RETARGET_LIMITATION=('Viewing rule, not decoded game behaviour: the animset was built for a model of other proportions, '
    'so each translation is the rest translation plus the clip offset from the rest of that model, scaled by the ratio of '
    'the two rest lengths; rotations and scales are as decoded.')


def animset_translation_retarget(archive,path,rows,model_id):
    """``translation_retarget`` from the model an animset names to ``rows`` ({model, limitation, joints}), or None.

    None when the animset names this model, no single model, or a model with the same rest rig. The viewer's
    AnimationLibrary applies the same rule."""
    source=animset_rig_model(read(path))
    ids=[k for k in archive.resolve(source) if archive.entries[k]['type']=='model'] if source and rows else []
    if len(ids)!=1 or ids[0]==model_id:return None
    try:joints=translation_retarget(rows,model_skeleton(archive.asset_data(ids[0])))
    except (ValueError,KeyError):return None   # KeyError: a model without a joint section
    return dict(model=archive.entries[ids[0]]['path'],limitation=RETARGET_LIMITATION,joints=joints) if joints else None


def geometry_game_equivalence(native_sources, mesh_manifest, validation):
    """Separate reopened structural integrity from source-semantic equivalence."""
    geometry=validation.get('geometry_validation') or {}
    integrity=validation.get('native_asset_integrity') or {}
    expected=[d for d in native_sources if d and d.get('visible',True)]
    parts=geometry.get('parts') or []
    reasons=Counter()
    part_by_hash=Counter(str(p.get('sha256','')).lower() for p in parts)
    expected_by_hash=Counter(str(d.get('sha256','')).lower() for d in expected)
    source_rows=[]
    structural_ok=bool(expected) and validation.get('status')=='passed' and validation.get('scene_preservation_passed') is True
    if not expected:reasons['no selected visible native source geometry']+=1
    if validation.get('status')!='passed':reasons['separate-process validation did not pass']+=1
    if validation.get('scene_preservation_passed') is not True:reasons['scene preservation was not confirmed after reopen']+=1
    if len(parts)!=len(expected):
        structural_ok=False;reasons['reopened geometry part count does not match selected visible source count']+=1
    if part_by_hash!=expected_by_hash:
        structural_ok=False;reasons['reopened geometry source hashes do not match selected visible source summaries']+=1
    if integrity.get('status') not in ('structural_integrity_passed_semantics_reported_separately',
                                       'source_reconstruction_and_delivery_integrity'):
        structural_ok=False;reasons['native mesh reopen integrity gate is absent or failed']+=1

    proof_rows=integrity.get('source_subset_proofs') or []
    mesh_total=0
    for descriptor in expected:
        digest=str(descriptor.get('sha256','')).lower()
        summary=descriptor.get('summary') or {}
        selected=int(summary.get('selected_mesh_count',0) or 0)
        mesh_total+=selected
        matched=[p for p in parts if str(p.get('sha256','')).lower()==digest]
        part=matched[0] if len(matched)==1 else None
        expected_matches=expected_by_hash[digest]
        source_ok=bool(digest and len(matched)==expected_matches and part and part.get('status')=='native_ps4_geometry_decoded'
                       and int(part.get('mesh_count',-1))==selected
                       and part.get('source_topology_uv_normals_weights_material_assignments_preserved') is True)
        if len(matched)!=expected_matches:reasons[f"reopened source hash match count {len(matched)}; selected count {expected_matches} for {digest or 'missing hash'}"]+=1
        if not part or not source_ok:
            reasons[f"selected source structural checks incomplete: {descriptor.get('path',digest)}"]+=1
        source_rows.append(dict(path=descriptor.get('path'),role=descriptor.get('role','primary'),source_sha256=digest or None,
            selected_mesh_count=selected,reopened_part_count=len(matched),structural_pass=source_ok))
    if int(integrity.get('meshes',-1))!=mesh_total:
        structural_ok=False;reasons['reopened native mesh count does not match selected source summaries']+=1
    if any(not row['structural_pass'] for row in source_rows):structural_ok=False

    manifest_reopen_status=None
    primary=next((d for d in expected if d.get('role','primary')=='primary'),expected[0] if expected else None)
    if primary:
        primary_hash=str(primary.get('sha256','')).lower()
        manifest_hash=str(mesh_manifest.get('source_sha256','')).lower()
        primary_mesh_count=int((primary.get('summary') or {}).get('selected_mesh_count',0) or 0)
        manifest_meshes=mesh_manifest.get('meshes') or []
        manifest_ok=(manifest_hash==primary_hash and mesh_manifest.get('selected_mesh_count')==primary_mesh_count
                     and len(manifest_meshes)==primary_mesh_count)
        manifest_reopen_status=mesh_manifest.get('reopened_validation_status')
        if not manifest_ok:
            structural_ok=False;reasons['primary mesh manifest source/count does not match selected native summary']+=1
        if manifest_reopen_status and not str(manifest_reopen_status).startswith('passed'):
            structural_ok=False;reasons['primary mesh manifest reopened validation status is not passed']+=1
        checks=[m.get('reopened_value_checks',{}).get('status') for m in manifest_meshes
                if m.get('reopened_value_checks') is not None]
        if checks and any(status!='passed' for status in checks):
            structural_ok=False;reasons['primary mesh manifest contains failed reopened value checks']+=1
    else:
        manifest_ok=False

    # Prefer semantic evidence serialized by the separate-process validation.
    # Fall back to source summaries only when the reopened report predates the
    # semantic block; a missing claim remains unknown and can never pass.
    proof_by_source={}
    for proof in proof_rows:
        key=(str(proof.get('source_model_sha256','')).lower(),int(proof.get('subset',-1)))
        if key[0]:proof_by_source[key]=proof.get('semantic_evidence')
    semantic_counts={aspect:Counter({'proven':0,'unproven':0,'unknown':0})
                     for aspect in ('uv0','normals','tangents')}
    semantic_reasons=Counter();semantic_subset_count=0
    for descriptor in expected:
        digest=str(descriptor.get('sha256','')).lower();summary=descriptor.get('summary') or {}
        part=next((p for p in parts if str(p.get('sha256','')).lower()==digest),{})
        reopen_rows={int(x.get('subset',-1)):x.get('semantic_evidence')
                     for x in part.get('subset_semantic_evidence',[]) if isinstance(x,dict)}
        source_meshes=summary.get('meshes') or []
        count=int(summary.get('selected_mesh_count',0) or 0)
        for subset in range(count):
            semantic_subset_count+=1
            evidence=proof_by_source.get((digest,subset)) or reopen_rows.get(subset)
            if evidence is None and subset<len(source_meshes):evidence=source_meshes[subset].get('semantic_evidence')
            evidence=evidence if isinstance(evidence,dict) else {}
            for aspect in semantic_counts:
                item=evidence.get(aspect)
                confidence=item.get('semantic_confidence') if isinstance(item,dict) else None
                if not confidence and aspect=='normals':confidence=integrity.get('unsupported_normal_semantics')
                if not confidence and aspect=='tangents':confidence=integrity.get('unsupported_tangent_semantics')
                proven=((isinstance(item,dict) and item.get('semantic_equivalence_proven') is True)
                        or confidence=='proven' or (isinstance(confidence,str) and confidence.startswith('validated_')))
                key='proven' if proven else ('unproven' if confidence else 'unknown')
                semantic_counts[aspect][key]+=1
                if not proven:
                    reason=f"{aspect}: {confidence or 'semantic evidence missing'}"
                    semantic_reasons[reason]+=1

    # Explicit false aggregate claims are retained as blockers even if a stale
    # or partial subset block looks optimistic.
    aggregate_uv_claims=[integrity.get('game_accurate_uv_claim'),geometry.get('game_accurate_uv_claim'),
                         mesh_manifest.get('game_accurate_uv_claim'),
                         *[(d.get('summary') or {}).get('game_accurate_uv_claim') for d in expected]]
    aggregate_uv_claim_false=any(value is False for value in aggregate_uv_claims)
    if aggregate_uv_claim_false:semantic_reasons['explicit game_accurate_uv_claim=false']+=1
    has_unproven=aggregate_uv_claim_false or any(semantic_counts[a]['unproven'] for a in semantic_counts)
    has_unknown=any(semantic_counts[a]['unknown'] for a in semantic_counts)
    semantics_proven=semantic_subset_count>0 and not has_unproven and not has_unknown
    equivalent=bool(structural_ok and semantics_proven)
    status=('geometry_equivalent' if equivalent else
            'structurally_validated_semantics_unproven' if structural_ok and has_unproven else
            'structurally_validated_semantics_unknown' if structural_ok else 'structural_validation_incomplete')
    return dict(schema='ps4-geometry-game-equivalence-v1',status=status,equivalent=equivalent,
        structural=dict(status='passed' if structural_ok else 'incomplete',selected_visible_source_count=len(expected),
            primary_source_count=sum(row['role']=='primary' for row in source_rows),
            visible_attachment_source_count=sum(row['role']!='primary' for row in source_rows),
            reopened_source_match_count=sum(row['structural_pass'] for row in source_rows),
            selected_mesh_count=mesh_total,mesh_manifest_primary_match=manifest_ok,
            mesh_manifest_reopen_status=manifest_reopen_status or 'not_recorded',sources=source_rows,
            reasons=dict(sorted(reasons.items()))),
        semantic_equivalence=dict(status='proven' if semantics_proven else 'unproven_and_unknown' if has_unproven and has_unknown else
            'unproven' if has_unproven else 'unknown',
            subset_count=semantic_subset_count,aggregate_uv_claim_false=aggregate_uv_claim_false,
            counts={aspect:dict(sorted(counts.items())) for aspect,counts in semantic_counts.items()},
            reasons=dict(sorted(semantic_reasons.items()))),
        conclusion=('All selected visible source meshes passed reopen structure and every UV0, normal, and tangent semantic claim is proven.'
                    if equivalent else 'Structural validation does not prove source semantic equivalence; every relevant UV0, normal, and tangent semantic must be explicitly proven.'))


def blender(args,job,validate,log,script='builder.py'):
    name=Path(job).stem+('_validate' if validate else '_build')
    output=args.output_root/'reports/logs'/(name+'.log')
    command=[args.blender,'--background','--factory-startup','--disable-autoexec','--python-exit-code','1',
        '--python',str(Path(__file__).with_name(script)),'--','--job',str(job)]
    if validate:command.append('--validate')
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['TEMP']=str(args.output_root/'work/tmp');env['TMP']=env['TEMP']
    env['BLENDER_USER_RESOURCES']=str(args.output_root/'work/blender_user')
    log('blender_started',stage='validate' if validate else 'build',job=str(job))
    with open(output,'w',encoding='utf-8') as stream:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env)
        for line in process.stdout:
            stream.write(line);stream.flush()
            if line.startswith(('BAKED','BUILT','VERIFIED','VALIDATION','Error','Traceback')):print(line.rstrip(),flush=True)
        code=process.wait();process.stdout.close()
    if code:raise RuntimeError('Blender failed; see '+str(output))


def process_model(key,index,args,code_hash,previous,log):
    archive=index.archive;asset=archive.entries[key]
    stem=safe_name(Path(asset['path']).stem)+'_'+key[:8]
    model_root=args.output_root/stem
    # Check the shortest generated texture path before decoding model/material
    # assets; late Blender image-load failures are difficult to diagnose.
    preflight_texture_output(args.output_root,model_root,args.export_textures)
    for folder in ('meshes','textures','materials','decoded_sources','animation_cache','reports','validation'):
        (model_root/folder).mkdir(parents=True,exist_ok=True)
    report_dir=model_root/'reports'
    related=index.related(key)
    # A reused actor index decodes no actors; decode this model's related ones so their
    # source hashes and decoded files are current for the fingerprint and inventory.
    for source_key in related['related_assets']:
        if archive.entries[source_key]['type']=='actor':
            try:archive.decode(source_key)
            except Exception:pass     # recorded in index_errors by the actor scan, as before
    dump(report_dir/'references.json',related)
    model_path=archive.decode(key)
    bundled=Path(__file__).with_name('model_profiles')/(file_hash(model_path)+'.json')
    override=load(args.profile) if args.profile else load(bundled) if bundled.exists() else None
    model=inspect_model(model_path,override)
    native_summary=inspect_native_model(model_path,args.import_lod)
    dump(model_root/'meshes/manifest.json',native_summary)
    material_library=prepare_materials(archive,native_summary,model_root,args.export_textures,log,model_root=model_root)
    native_geometry=dict(path=str(model_path),sha256=file_hash(model_path),lod=args.import_lod,look=0,
        name=safe_name(Path(asset['path']).stem),materials=material_library,summary=native_summary,
        role='primary',visible=True)
    mesh_position_correction=model['profile'].get('native_mesh_position_correction')
    if mesh_position_correction:
        if mesh_position_correction.get('source_sha256')!=native_geometry['sha256']:
            raise ValueError('Profiled native mesh position correction does not match the primary source model')
        native_geometry['mesh_position_correction']=mesh_position_correction
    primary_by_hash={r['hash']:r['name'] for r in model['skeleton']}
    native_parts=[];attachment_results=[];part_material_libraries=[]
    for attachment in related['attachment_models']:
        attachment_asset=archive.entries[attachment]
        tokens=Path(attachment_asset['path']).stem.lower().split('_')
        if 'base' not in tokens:
            attachment_results.append(dict(path=attachment_asset['path'],status='identified_variant_not_imported',
                reason='Referenced alternate without a source-authored base token; kept out of the default visible assembly'))
            continue
        try:
            part_path=archive.decode(attachment);part_model=inspect_model(part_path)
            joint_names=[primary_by_hash.get(r['hash']) for r in part_model['skeleton']]
            missing=[r['hash'] for r,name in zip(part_model['skeleton'],joint_names) if not name]
            if missing:raise ValueError('Attachment skeleton contains joints absent from primary actor: '+repr(missing[:20]))
            part_summary=inspect_native_model(part_path,args.import_lod)
            part_name=safe_name(Path(attachment_asset['path']).stem)
            part_library=prepare_materials(archive,part_summary,model_root/'part_assets'/part_name,
                args.export_textures,log,model_root=model_root)
            part_material_libraries.append(part_library)
            native_parts.append(dict(path=str(part_path),sha256=file_hash(part_path),lod=args.import_lod,look=0,
                name=part_name,materials=part_library,summary=part_summary,joint_names=joint_names,
                role='source_config_default_attachment',visible=True))
            attachment_results.append(dict(path=attachment_asset['path'],status='assembled_visible_default',
                mesh_count=part_summary['selected_mesh_count'],joint_count=part_model['joint_count'],
                evidence='Authoritative reference graph plus source-authored base token'))
        except Exception as exc:
            attachment_results.append(dict(path=attachment_asset['path'],status='failed',reason=str(exc)))
    profile_geometry_override=model['profile'].get('geometry',{})
    model['profile']['geometry']=dict(status='native_ps4_geometry_supported',
        mesh_count=native_summary['selected_mesh_count'],vertex_count=native_summary['vertex_count'],
        lod=args.import_lod,look=0,tangent_status='packed_uint32_retained_uninterpreted')
    geometry=None
    geometry_ids=related['geometry']
    if profile_geometry_override.get('asset_path'):
        geometry_ids=archive.resolve(profile_geometry_override['asset_path'])
    if len(geometry_ids)==1:
        geometry_path=archive.decode(geometry_ids[0])
        if archive.entries[geometry_ids[0]]['type']!='fbx':raise ValueError('Only the FBX geometry adapter is supported')
        geometry=dict(path=str(geometry_path),sha256=file_hash(geometry_path))
        expected=model['profile'].get('geometry',{}).get('sha256')
        if expected and geometry['sha256']!=expected:raise ValueError('Profile FBX hash mismatch')
        if geometry_ids[0] not in related['related_assets']:related['related_assets'].append(geometry_ids[0])
        # Keep the native profile authoritative; this FBX is a comparison/
        # fallback descriptor, not the geometry selected by builder.py.
        model['profile']['geometry']['fbx_reference']=geometry
        model['profile']['geometry']['fbx_reference_status']='available_for_numerical_comparison_or_fallback'
    no_skeleton=bool(getattr(args,'no_skeleton',False))
    # Joints without clips (--no-animations) takes the same route as no skeleton for the animation sources.
    no_clips=no_skeleton or bool(getattr(args,'no_animations',False))
    if no_skeleton:
        # Static export: no joints, skin weights or clips. The builder takes its static-geometry path.
        model['skeleton']=[]
    dump(report_dir/'model.json',model)
    dump(report_dir/'model_profile.json',model['profile'])
    # Preserve the small authoritative descriptors. Model variants and bulk payloads
    # remain indexed until selected, so a base-character build does not expand every outfit.
    dependency_errors=[]
    for dependency in related['related_assets']:
        if archive.entries[dependency]['type'] not in {'actor','config','material','materialgraph','animset'}:
            continue
        try:archive.decode(dependency)
        except Exception as exc:dependency_errors.append(dict(asset_id=dependency,path=archive.entries[dependency]['path'],reason=str(exc)))
    archive.checkpoint()
    entries=[];action_entries=[];animset_errors=[]
    # Referenced animsets first; then the opt-in sources (borrowed animsets, cutscene clips).
    sources=[] if no_clips else [dict(asset_id=animset,source_type='animset') for animset in related['animsets']]
    for borrowed in [] if no_clips else getattr(args,'borrow_animset',None) or ():
        sources.append(borrowed_animset(archive,index.graph,borrowed,model['skeleton'],key))
    if getattr(args,'cinematic_clips',False) and not no_clips:
        found,errors=cinematic_sources(archive,model['skeleton'],archive.entries[key]['path'])
        animset_errors.extend(dict(animset=e['path'],reason=e['reason'],status='unreadable_cutscene') for e in errors)
        sources.extend(dict(asset_id=f['asset_id'],source_type=CINEMATIC_SOURCE,limitation=CINEMATIC_LIMITATION,
            clip_names={c['offset']:c['name'] for c in f['clips']},
            provenance=dict(clip_name_evidence='name hash equals the game CRC32 of a string in the same cutscene file'))
            for f in found)
    related['extra_animation_sources']=[dict(asset_id=s['asset_id'],path=archive.entries[s['asset_id']]['path'],
        source_type=s['source_type'],**s.get('provenance',{})) for s in sources if s['source_type']!='animset']
    for row in related['extra_animation_sources']:
        if row['asset_id'] not in related['related_assets']:related['related_assets'].append(row['asset_id'])
    if related['extra_animation_sources']:dump(report_dir/'references.json',related)
    for source in sources:
        animset=source['asset_id']
        try:
            path=archive.decode(animset)
            cache=model_root/'animation_cache'/animset
            clips,errors=extract_set(path,model['skeleton'],cache,model['profile'],log,
                experimental_ambiguous_inheritance=args.experimental_ambiguous_inheritance,
                clip_names=source.get('clip_names'))
            retarget=animset_translation_retarget(archive,path,model['skeleton'],key) if source['source_type']=='animset' else None
            for entry in clips:
                entry['animset_path']=archive.entries[animset]['path']
                # Only clips that sit on the animset's own rig are moved (clip_fits_source); the samples cache stays as decoded.
                if (retarget and entry.get('kind') in ('absolute skeletal','partial absolute skeletal') and entry.get('samples')
                        and clip_fits_source(unzip_json(entry['samples'])['frames'][0],retarget['joints'])):
                    entry['translation_retarget']=retarget
                if source['source_type']!='animset':
                    entry.update(source_type=source['source_type'],source_limitation=source['limitation'],
                        source_provenance=source.get('provenance',{}))
                base_action=f"{len(entries):05d} | "+safe_name(entry['name'] or 'unnamed')[:40]
                if entry.get('experimental_inheritance_preview'):
                    rules=('full','segment_scale_compensation_bit0')
                    entry['experimental_action_names']=[f"{base_action} | EXPERIMENTAL {'FULL INHERITANCE' if rule=='full' else 'SSC0'}" for rule in rules]
                    entry['action']=entry['experimental_action_names'][1]
                    for rule,action_name in zip(rules,entry['experimental_action_names']):
                        action_entry=dict(entry)
                        action_entry.update(action=action_name,inheritance=rule,
                            experimental_selected_inheritance=rule,
                            selector_label=f"{entry['name']} | EXPERIMENTAL {'FULL INHERITANCE' if rule=='full' else 'SSC0'}")
                        action_entries.append(action_entry)
                else:
                    entry['action']=base_action
                    if entry['status']=='included':action_entries.append(entry)
                entries.append(entry)
            if errors:animset_errors.append(dict(animset=archive.entries[animset]['path'],errors=errors))
        except Exception as exc:
            animset_errors.append(dict(animset=archive.entries[animset]['path'],reason=str(exc),status='unsupported_or_malformed',raw_source_retained=True))
    try:
        selected_source_rows, catalog = resolve_clip_selection(entries, args.clip, picked=getattr(args,'clip_picked',False))
    except ClipSelectionError as exc:
        _all_supported, catalog = resolve_clip_selection(entries)
        for row in catalog:
            row['selection_status'] = 'selection_error'
            row['selection_provenance']['selection_mode'] = 'explicit_invalid'
            row['selection_provenance']['selectors'] = list(args.clip or ())
        dump(report_dir/'clips.json',catalog)
        dump(report_dir/'clip_selection_error.json',dict(status='invalid',selectors=list(args.clip or ()),error=str(exc)))
        raise ValueError('Clip selection failed after source catalog decode and before Blender: '+str(exc)) from exc
    selected_keys={(row.get('animset_path'),row.get('order'),row.get('raw_sha256')) for row in selected_source_rows}
    action_entries=[row for row in action_entries if (row.get('animset_path'),row.get('order'),row.get('raw_sha256')) in selected_keys]
    entries=catalog
    included=selected_source_rows
    # --active-clip: the baked Action left active (and framed by the camera) instead of the sequential overview.
    active_clip_index=None
    if getattr(args,'active_clip',None):
        try:
            active_rows,_catalog=resolve_clip_selection(entries,[args.active_clip])
        except ClipSelectionError as exc:
            raise ValueError('Active clip selection failed before Blender: '+str(exc)) from exc
        active_key=(active_rows[0].get('animset_path'),active_rows[0].get('order'),active_rows[0].get('raw_sha256'))
        matches=[i for i,row in enumerate(action_entries) if (row.get('animset_path'),row.get('order'),row.get('raw_sha256'))==active_key]
        if len(matches)!=1:
            raise ValueError('Active clip %s must be one selected, baked Action (found %d)'%(args.active_clip,len(matches)))
        active_clip_index=matches[0]
    explicit_clip_selection=bool(args.clip)
    selection_identity=dict(mode='explicit' if explicit_clip_selection else 'all_supported',
        selectors=list(args.clip or ()),
        selected=[dict(animset_path=row.get('animset_path'),order=row.get('order'),name_hash=row.get('name_hash'),raw_sha256=row.get('raw_sha256')) for row in selected_source_rows])
    omitted_by_user_selection=[row for row in entries if row.get('selection_status')=='omitted_by_user_selection']
    coverage=animation_coverage(entries,animset_errors)
    limitations=dict(unsupported_clips=[e for e in entries if e['status']!='included'],
        experimental_clips=[e for e in entries if e.get('experimental')],
        experimental_action_count=sum(len(e.get('experimental_action_names',[])) for e in entries),
        experimental_inheritance_mode=('full_and_ssc0_candidates_ssc0_preferred_unproven' if args.experimental_ambiguous_inheritance else 'disabled'),
        animation_coverage=coverage,
        catalog_experimental_action_count=sum(len(e.get('experimental_action_names',[])) for e in entries),
        animation_selection=dict(**selection_identity,omitted_by_user_selection=omitted_by_user_selection,
            delivery_complete_animation_library=not explicit_clip_selection),
        animset_errors=animset_errors,dependency_errors=dependency_errors,index_errors=index.errors,
        unresolved_references=related['unresolved'],
        attachments=attachment_results,
        geometry=model['profile']['geometry'],
        materials=dict(status='source_channels_connected_compiled_graph_semantics_limited',library=material_library,
            game_equivalence=False,
            game_equivalence_reasons=['Compiled materialgraph execution/runtime bindings are unresolved; recipe integrity is not shader equivalence.']),
        lighting=(dict(status='novalis_reflection_probe_selected_grid_spatial_map_probe_pairs_and_atmosphere_sun_ray_decoded_rgb96_surface_light_candidate_pending_runtime_names',
            region=args.game_lighting['region'],probe=args.game_lighting['source_path']) if args.game_lighting else
            dict(status='veldin_capture_presentation',limitation="Captured Veldin key-light colour/strength, ambient, exposure and tonemap; light direction is presentational and the model's own level lighting is not captured") if args.veldin_look else
            dict(status='external_hdri')),
        features_not_decoded=['Compiled materialgraph runtime parameters','Runtime blend partners/weights','Root-motion path channels','VFX/shader animation','Unrecognized model section semantics'],
        no_animation_found=not sources,visual_review='User only; no AI visual inspection or pass/fail gate')
    dump(report_dir/'clips.json',entries)
    dump(report_dir/'limitations.json',limitations)
    decoded_inventory=[]
    for source_key in related['related_assets']:
        if source_key in archive.mapping:
            decoded_inventory.append(dict(asset_id=source_key,path=archive.entries[source_key]['path'],
                decoded_path=archive.mapping[source_key]['decoded'],
                source_sha256=archive.mapping[source_key]['source_sha256'],
                decoded_sha256=archive.mapping[source_key]['decoded_sha256']))
    dump(model_root/'decoded_sources/manifest.json',dict(schema='rcps4-decoded-source-links-v1',assets=decoded_inventory,
        note='Decoded files are held in the shared hash-addressed OutputRoot cache; this per-model manifest records exact paths and hashes.'))
    all_material_errors=material_library['errors']+[e for library in part_material_libraries for e in library['errors']]
    attachment_errors=[e for e in attachment_results if e['status']=='failed']
    if args.fail_on_missing_assets and (all_material_errors or related['unresolved'] or dependency_errors or attachment_errors):
        raise ValueError('Missing or ambiguous required assets under -FailOnMissingAssets; see reports')
    final=model_root/(safe_name(Path(asset['path']).stem)+'.blend')
    fingerprint=sha(json.dumps(dict(code=code_hash,metadata=archive.metadata,profile=model['profile'],
        sources={k:archive.mapping[k]['source_sha256'] for k in related['related_assets'] if k in archive.mapping},
        textures={x['path']:(x['built_sha256'],x.get('stream_sha256')) for x in material_library['textures']},
        import_lod=args.import_lod,export_textures=args.export_textures,
        experimental_ambiguous_inheritance=args.experimental_ambiguous_inheritance,
        animation_selection=selection_identity,active_clip=active_clip_index,skeleton='none' if no_skeleton else 'joints',
        animation_sources=dict(cinematic_clips=bool(getattr(args,'cinematic_clips',False)),
            borrowed_animsets=list(getattr(args,'borrow_animset',None) or ())),
        lighting=(dict(source='novalis',probe=args.game_lighting['source_sha256'],faces=[x['sha256'] for x in args.game_lighting['faces']],
            atmosphere_sun=args.game_lighting.get('atmosphere_sun'))
            if args.game_lighting else dict(source='veldin',**args.veldin_look) if args.veldin_look else
            dict(source='hdri',sha256=file_hash(args.hdri),settings={k:v for k,v in args.shared_lighting.items()
                if k not in ('config_path','config_sha256')})),blender=args.blender),sort_keys=True).encode())
    old=previous.get(key,{})
    if final.exists() and not args.force:
        if not args.reuse_cache:
            raise FileExistsError('Final blend already exists. Use -ReuseCache or -Force: '+str(final))
        validation_path=model_root/'validation/validation.json'
        if verified_cache_hit(old,fingerprint,final,validation_path):
            log('reused_verified_delivery',model=asset['path'],final=str(final));return old
        if (not explicit_clip_selection
                and old.get('delivery_complete_animation_library') is not False
                and verified_source_model_reuse_after_orchestration_change(old,fingerprint,final,validation_path,
                model_root,archive,key,getattr(args,'previous_implementation_sha256',{}),
                getattr(args,'current_implementation_sha256',{}))):
            old['fingerprint']=fingerprint
            previous[key]=old
            log('reused_source_audited_delivery',model=asset['path'],final=str(final),
                reason='only variant assembly orchestration code changed; builder implementation and every decoded archive dependency revalidated')
            return old
        raise FileExistsError('Final blend already exists or changed. -Force is required to replace it: '+str(final))
    checkpoint=args.output_root/'blender'/(stem+'_checkpoint.blend')
    if args.force and checkpoint.exists():checkpoint.unlink()
    candidate=args.output_root/'blender'/(stem+'_candidate.blend')
    rules=model['profile']['inheritance_candidates']
    rest_rule=rest_inheritance_rule(model['profile'])
    if not rules and not model['profile'].get('inheritance'):
        # A rest-only unsupported file cannot claim an inferred runtime inheritance rule.
        limitations['rest_pose_inheritance']='Ordinary hierarchy diagnostic; source inverse-bind evidence unavailable'
    if model['profile'].get('rest_inheritance') and model['profile'].get('inheritance') not in (None,rest_rule):
        # Skinning is clip world * rest inverse, so the clips' rule departs from the bind pose where the rules differ.
        limitations['rest_pose_inheritance']=('Rest pose and inverse binds use '+rest_rule+'; clips use '+model['profile']['inheritance']+
            ' (profile-selected split, inferred from animated basis shear, not a trace of the runtime evaluator)')
    texture_transport=[];seen_transport=set()
    for library in [material_library,*part_material_libraries]:
        bindings=list(library.get('textures',[]))
        bindings.extend(binding for material in library.get('materials',[])
            for binding in material.get('textures',[]))
        for binding in bindings:
            identity=image_interpretation_key(binding)
            if identity in seen_transport:continue
            seen_transport.add(identity)
            image_identity=image_interpretation_identity(binding)
            check=binding.get('texture_transport',{})
            texture_transport.append(dict(source_path=binding['path'],dds_sha256=binding.get('dds_sha256'),
                dimensions=[binding.get('width'),binding.get('height')],colorspace=binding['colorspace'],
                alpha_mode=image_identity['alpha_mode'],conversion=image_identity['conversion'],
                image_identity=image_identity,
                packed_dimensions=binding.get('dimensions'),
                status=check.get('status','limited'),source_exact_top_mip=check.get('source_exact_top_mip'),
                reason=check.get('reason','transport evidence unavailable'),built_asset_id=binding.get('built_asset_id'),
                stream_asset_id=binding.get('stream_asset_id'),format=binding.get('format'),
                built_path=binding.get('built_path'),
                built_sha256=check.get('built_sha256'),stream_sha256=check.get('stream_sha256'),
                stream_size=check.get('stream_size'),stream_occurrences=check.get('stream_occurrences',[]),
                source_evidence=check))
    job=dict(schema=VERSION,fingerprint=fingerprint,model=model,geometry=geometry,native_geometry=native_geometry,native_parts=native_parts,
        animation_selection=selection_identity,active_clip_index=active_clip_index,clip_catalog=entries,
        texture_transport=texture_transport,
        experimental_ambiguous_inheritance=args.experimental_ambiguous_inheritance,
        clips=action_entries,limitations=limitations,candidate=str(candidate),checkpoint=str(checkpoint),
        validation_report=str(model_root/'validation/validation.json'),
        hdri=str(args.hdri) if args.hdri else None,game_lighting=args.game_lighting,veldin_look=args.veldin_look,
        shared_lighting=args.shared_lighting,
        game_lighting_exr=(args.game_lighting['environment_exr'] if args.game_lighting else None),
        rest_inheritance=rest_rule,matrix_tolerance=.0001)
    job_path=args.output_root/'work'/(stem+'_job.json');dump(job_path,job)
    log('model_ready',model=asset['path'],joint_count=model['joint_count'],included_source_clips=len(included),
        baked_actions=len(action_entries),total_source_clips=len(entries))
    blender(args,job_path,False,log);blender(args,job_path,True,log)
    if native_geometry:
        reopened=load(model_root/'validation/validation.json')
        integrity=reopened.get('native_asset_integrity')
        if not integrity or integrity.get('status')!='structural_integrity_passed_semantics_reported_separately':
            raise RuntimeError('Reopened native semantic/integrity report is missing or has an unexpected status')
        if integrity.get('game_accurate_uv_claim') is not False and native_summary['source_units']['uv0_record_stride']==8:
            raise RuntimeError('Reopened 8-byte UV report did not explicitly reject a game-accurate UV claim')
        proof_rows=[row for row in integrity['source_subset_proofs']
                    if row.get('source_model_sha256')==native_geometry['sha256']]
        proof_by_subset={int(row['subset']):row for row in proof_rows}
        manifest=load(model_root/'meshes/manifest.json')
        if manifest.get('source_sha256')!=native_geometry['sha256']:
            raise RuntimeError('Native mesh manifest source hash differs from reopened validation')
        for mesh in manifest['meshes']:
            subset=int(mesh['index']);proof=proof_by_subset.get(subset)
            if proof is None:
                raise RuntimeError(f'Reopened native validation lacks primary subset {subset}')
            uv8=int(mesh['semantic_evidence']['uv0']['record_stride_bytes'])==8
            mesh['reopened_value_checks']=dict(
                status=proof['byte_and_roundtrip_integrity'],
                primary_loop_max_error=proof['uv0_primary_loop_roundtrip_max_error'],
                secondary_words_exact=proof['uv0_secondary_words_exact'])
            mesh['game_accurate_uv_claim']=False if uv8 else True
            if uv8 and mesh['semantic_evidence']['uv0']['semantic_confidence']!='unproven_by_matching_fbx_controls':
                raise RuntimeError(f'8-byte UV subset {subset} has an invalid semantic-confidence label')
        manifest['reopened_validation_source_sha256']=native_geometry['sha256']
        manifest['reopened_validation_status']='passed_structural_integrity_semantics_reported_separately'
        manifest['game_accurate_uv_claim']=False if native_summary['source_units']['uv0_record_stride']==8 else True
        dump(model_root/'meshes/manifest.json',manifest)
    # Archive files untouched since the run began (size + mtime) and every source hashed this
    # run: nothing to re-read. Otherwise re-read and re-hash every source and the metadata.
    if not sources_unchanged_since_start(archive,related['related_assets'],getattr(args,'archive_stamps',None),
                                         getattr(args,'verified_sources',{})):
        for source_key in related['related_assets']:
            if source_key in archive.mapping and archive.bytes(source_key)[1]!=archive.mapping[source_key]['source_sha256']:
                raise RuntimeError('Source asset changed during build: '+archive.entries[source_key]['path'])
        if any(file_hash(archive.root/name)!=digest for name,digest in archive.metadata.items()):
            raise RuntimeError('Archive metadata changed during build')
    # Promotion follows a separate-process reopen validation. Atomic replace is only allowed with -Force.
    if final.exists() and not args.force:raise FileExistsError('Final appeared during build: '+str(final))
    promote(candidate,final,args.force)
    validation=load(model_root/'validation/validation.json')
    if geometry or native_geometry:
        limitations['geometry']=validation['geometry_validation']
    limitations['unset_material_slots']=[row for library in [material_library,*part_material_libraries]
        for row in library.get('unset_material_slots',[])]
    limitations['missing_source_textures']=[dict(material=m.get('path'),**{k:e[k] for k in ('path','status','reason','graph_slot_keys','material_slot_keys')})
        for library in [material_library,*part_material_libraries] for m in library['materials'] for e in m.get('texture_errors',[])]
    limitations['geometry_game_equivalence']=geometry_game_equivalence(
        [native_geometry,*native_parts],load(model_root/'meshes/manifest.json'),validation)
    dump(report_dir/'limitations.json',limitations)
    libraries=[material_library,*part_material_libraries]
    texture_errors=[e for library in libraries for m in library['materials'] for e in m.get('texture_errors',[])]
    explicit_status=('COMPLETE_MESH_ANIMATIONS_TEXTURE_LIMITATION' if texture_errors or any(x['errors'] for x in libraries) else
        'COMPLETE_MESH_ANIMATIONS_MATERIAL_LIMITATION' if any(x['shader_limitations'] for x in libraries) else
        'COMPLETE_MESH_MATERIALS_ANIMATIONS')
    result=dict(status=explicit_status,model=asset['path'],final=str(final),final_sha256=file_hash(final),
        fingerprint=fingerprint,joint_count=model['joint_count'],included_clips=len(included),source_clip_count=len(entries),
        experimental_clip_count=sum(1 for e in included if e.get('experimental')),
        included_action_count=len(action_entries),delivery_complete_animation_library=not explicit_clip_selection,
        animation_selection=selection_identity,
        experimental_action_count=sum(len(e.get('experimental_action_names',[])) for e in included),
        classifications=dict(Counter(e['kind'] for e in entries)),validation=str(model_root/'validation/validation.json'),
        validation_sha256=file_hash(model_root/'validation/validation.json'),limitations=str(report_dir/'limitations.json'),
        automated=['archive discovery','asset index','actor reference scan','selective asset extraction/decompression',
        'skeleton and inverse-bind inspection','exact v15/v21 samples and raw clips','classification evidence','affine action baking',
        'embedded controls','source-order NLA','separate-process key and sampled-pose validation'],
        lighting=(dict(source='Novalis game reflection probe',region=args.game_lighting['region'],probe=args.game_lighting['source_path'],
            limitation='Cycles world conversion; Novalis grid mapping, probe selectors, and atmosphere sun-ray direction are decoded. RGB96 is a source-valued surface-light candidate; model placement, light-grid coefficients, and runtime field names remain unresolved') if args.game_lighting else
            dict(source='Veldin capture game look',limitation='Captured key light/ambient/exposure; tonemap as compositor nodes (trilinear grade, max 0.0035 from the OCIO tetrahedral view)') if args.veldin_look else dict(source='External HDRI')),
        unsupported=['Compiled materialgraph runtime parameters','Runtime layer composition','Unresolved attachment assembly','Root-motion paths/VFX'],
        visual_review_performed=False)
    # COMPLETE_* describes delivery coverage and animation/mesh integrity only.
    result['material_game_equivalence']=dict(status='unproven',equivalent=False,
        reasons=['The completed delivery validates extracted source bindings and/or Blender recipe integrity, not capture/runtime shader equivalence.'])
    geometry_equivalence=limitations['geometry_game_equivalence']
    result['geometry_game_equivalence']=dict(status=geometry_equivalence['status'],
        equivalent=geometry_equivalence['equivalent'],
        structural_status=geometry_equivalence['structural']['status'],
        details=str(report_dir/'limitations.json'))
    log('delivered',model=asset['path'],final=str(final),clips=len(included))
    return result


GROUP_STATUSES=('COMPLETE_MESH_MATERIALS_ANIMATIONS','COMPLETE_MESH_ANIMATIONS_MATERIAL_LIMITATION',
    'COMPLETE_MESH_ANIMATIONS_TEXTURE_LIMITATION','ARMATURE_ANIMATIONS_ONLY')


def group_member_labels(paths):
    """Short collection label per group member, e.g. ``grind.model`` for ``hero_ratchet_bangle_boot_grind.model``.

    Several members lose the underscore-words they all share at the start of the file name, always keeping
    the last word of each name. A label that still repeats falls back to the full file name, then to the full
    archive path, so every label stays unique. A single member keeps its whole file name."""
    names = [Path(path).name for path in paths]
    if len(names) < 2:
        return names
    words = [name[:-len('.model')].split('_') if name.endswith('.model') else name.split('_') for name in names]
    shared = 0
    while all(len(w) > shared + 1 and w[shared] == words[0][shared] for w in words):
        shared += 1
    def label(name, w):
        suffix = '.model' if name.endswith('.model') else ''
        return '_'.join(w[shared:]) + suffix
    short = [label(n, w) for n, w in zip(names, words)]
    labels = []
    for index, (name, text) in enumerate(zip(names, short)):
        if short.count(text) > 1:
            text = name
            if names.count(name) > 1:
                text = paths[index]
        labels.append(text)
    return labels


DEBRIS_WORDS = {'chunk','stump'}


def attach_owners(paths):
    """{part path: body path} for each ``<folder>/bangle_set/<name>/<name>.model`` in ``paths`` whose body model
    (a ``.model`` directly in ``<folder>``) is also in ``paths``. Attach plates and pads (``zurkon_..._attach``,
    ``thumper_..._pads``) are the body's skeleton cut down, so they follow its clips; a ``chunk`` or ``stump`` is
    debris with a motion of its own and stays a separate model."""
    keyed = {str(path).replace(chr(92),'/').casefold(): path for path in paths}
    bodies = {}
    for key,path in keyed.items():
        folder,_,name = key.rpartition('/')
        if name.endswith('.model'):bodies.setdefault(folder,[]).append(path)
    owners = {}
    for key,path in keyed.items():
        folder,separator,rest = key.partition('/bangle_set/')
        stem = rest.rpartition('/')[2][:-len('.model')]
        if separator and rest.endswith('.model') and not DEBRIS_WORDS.intersection(stem.split('_')) and len(bodies.get(folder,[])) == 1:
            owners[path] = bodies[folder][0]
    return owners


def assemble_group_blend(selection,manifest,args,log,archive=None):
    """One .blend holding every delivered selected model, framed by one camera (rc2016/pipeline/group_blend.py).

    Runs after the per-model builds, so each member is its own finished, validated .blend. A failure is
    recorded and reported, and never discards the per-model results."""
    name=safe_name(args.group_blend)
    delivered=[]
    for key in selection:
        result=manifest['models'].get(key,{})
        final=result.get('final')
        if result.get('status') in GROUP_STATUSES and final and Path(final).is_file():
            delivered.append((result.get('model',key),final))
    if getattr(args,'group_actors',''):
        try:members=placed_group_members(archive,selection,delivered,args,log)
        except Exception as exc:
            error=dict(group=name,error=str(exc),traceback=traceback.format_exc())
            manifest['errors'].append(error)
            manifest['group_blend']=dict(status='FAILED',name=name,error=str(exc))
            log('group_blend_failed',**error);return
        run_group_blend(name,members,manifest,args,log);return
    labels=group_member_labels([path for path,_ in delivered])
    mounts=idle_offsets([path for path,_ in delivered])
    owners=attach_owners([path for path,_ in delivered])
    label_of={path:label for label,(path,_) in zip(labels,delivered)}
    members=[dict(name=label,blend=final,sha256=file_hash(final),**({'native_offset':list(mounts[path])} if path in mounts else {}),
        **({'rig_of':label_of[owners[path]]} if path in owners else {}))
        for label,(path,final) in zip(labels,delivered)]
    run_group_blend(name,members,manifest,args,log)


GROUP_PATH_FPS=30   # frame rate of the baked flight-path tracks


def placed_group_members(archive,selection,delivered,args,log=None):
    """Group members for ``--group-actors ZONE:PREFIX``: one per matching zone actor, at its zone matrix relative to
    the first matching actor of the first selected model (``actor_placements.zone_placements``). Every placed actor's
    model must be selected and delivered. A placement is a rest arrangement, so members are shown in their bind pose.
    With ``--group-path`` every member also gets ``native_track``, its per-frame matrices flying the anchor's flight
    path (``actor_placements.flight_path_track``), and the anchor carries the path record as ``flight_path``."""
    from rc2016.pipeline.actor_placements import flight_path_track,lead_with_anchor,placement_labels,zone_placements
    zone,separator,prefix=args.group_actors.partition(':')
    if not separator or not prefix:
        raise ValueError('--group-actors takes ZONE:PREFIX, for example levels/pokitaru/.../gp_flight.zone:Hydroharvester #1')
    if not selection:
        raise ValueError('--group-actors needs a selected model as its anchor')
    rows,skipped=zone_placements(archive,zone,prefix,archive.entries[selection[0]]['path'])
    if skipped and log:
        log('group_actors_without_model',zone=zone,actors=skipped)
    if getattr(args,'group_lead',False):
        rows=lead_with_anchor(rows)
    finals={path.replace(chr(92),'/').casefold():final for path,final in delivered}
    missing=sorted({row['model'] for row in rows if row['model'] not in finals})
    if missing:
        raise ValueError('Placed actors need these models selected and delivered: '+', '.join(missing))
    members=[dict(name=label,blend=finals[row['model']],sha256=file_hash(finals[row['model']]),bind_pose=True,
                  native_matrix=row['native_matrix'],placement=dict(zone=zone,actor=row['actor'],actor_id=row['id'],
                  **({'lead':row['lead']} if 'lead' in row else {})))
             for label,row in zip(placement_labels(rows),rows)]
    if getattr(args,'group_path',False):
        tracks,info=flight_path_track(archive,zone,rows,args.group_path_speed,GROUP_PATH_FPS)
        for member,row,track in zip(members,rows,tracks):
            member.update(native_matrix=track[0],native_track=dict(fps=GROUP_PATH_FPS,matrices=track))
            if 'lead' in row:
                member['flight_path']=info
        if log:
            log('group_flight_path',path=info['path'],length=info['length'],frames=info['frames'],speed=info['speed'])
    return members


def run_group_blend(name,members,manifest,args,log):
    """Build, reopen-validate and promote ``groups/<name>.blend`` from ``members`` (see ``assemble_group_blend``)."""
    if not members:
        manifest['group_blend']=dict(status='skipped',name=name,reason='no selected model was delivered')
        log('group_blend_skipped',name=name);return
    final=args.output_root/'groups'/(name+'.blend')
    candidate=args.output_root/'blender'/(name+'_group_candidate.blend')
    job_path=args.output_root/'work'/(name+'_group_job.json')
    try:
        final.parent.mkdir(parents=True,exist_ok=True)
        dump(job_path,dict(members=members,candidate=str(candidate),hdri=str(args.hdri) if args.hdri else None,
            shared_lighting=args.shared_lighting))
        blender(args,job_path,False,log,'group_blend.py')
        blender(args,job_path,True,log,'group_blend.py')
        promote(candidate,final,force=True)
        manifest['group_blend']=dict(status='delivered',name=name,final=str(final),final_sha256=file_hash(final),
            members=[member['name'] for member in members])
        log('group_blend_delivered',final=str(final),members=len(members))
    except Exception as exc:
        error=dict(group=name,error=str(exc),traceback=traceback.format_exc())
        manifest['errors'].append(error)
        manifest['group_blend']=dict(status='FAILED',name=name,error=str(exc))
        log('group_blend_failed',**error)


def model_path_key(path):
    """Archive path as a --clip-map key: '/' separators, case folded."""
    return str(path).replace(chr(92),'/').casefold()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extracted-root',type=Path,required=True);parser.add_argument('--output-root',type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=False);group.add_argument('--model',action='append',help='model name or archive path; repeat for a selected batch');group.add_argument('--all-models',action='store_true')
    parser.add_argument('--clip',action='append',default=[],help='Exact animation selector; repeat. Accepts action:<label>, name:<source name>, hash:<name hash>, or an unambiguous exact name/action. Default: all supported clips.')
    parser.add_argument('--list-models-json',type=Path,help='write indexed model IDs and paths, then exit')
    parser.add_argument('--blender',required=True);parser.add_argument('--hdri',type=Path,help='Override only the HDRI file of the shared lighting')
    parser.add_argument('--lighting-config',type=Path,default=ROOT/'assets/lighting/shared_lighting.json',
        help='Shared review lighting file (HDRI, world strength, view transform, look, exposure); default: assets/lighting/shared_lighting.json')
    parser.add_argument('--profile',type=Path);parser.add_argument('--force',action='store_true')
    parser.add_argument('--reuse-cache',action='store_true');parser.add_argument('--export-textures',action='store_true')
    parser.add_argument('--keep-intermediates',action='store_true',
        help='Keep the archive extraction cache and Blender candidate/checkpoint files after a successful run')
    parser.add_argument('--import-lod',choices=('All','Highest','Lowest'),default='Highest')
    parser.add_argument('--fail-on-missing-assets',action='store_true')
    parser.add_argument('--experimental-ambiguous-inheritance',action='store_true',
        help='Include both decoded full and SSC0 experimental Actions for source-related clips blocked by inheritance ambiguity; SSC0 is preferred, runtime accuracy remains unproven')
    parser.add_argument('--cinematic-clips',action='store_true',
        help='Also offer the real-time cutscene (cinematic2) clips the model performs, covering all its joints; clips are named only by an exact CRC32 match. Pick one with --clip.')
    parser.add_argument('--active-clip',default=None,
        help='Exact selector (as --clip) of the one baked clip left as the active Action in the saved file, and framed by the review camera; default: the sequential overview of every clip')
    parser.add_argument('--borrow-animset',action='append',default=[],
        help='Also offer this animset (archive path) when the game pairs it with another model whose rest rig matches the selected model; repeat for several')
    parser.add_argument('--group-blend',default='',help='Also write one .blend under groups/ holding every selected model that was delivered, framed by one camera. Names the file.')
    parser.add_argument('--group-actors',default='',metavar='ZONE:PREFIX',
        help='With --group-blend: fill the group with the actors of zone ZONE (archive path) whose placement name starts with PREFIX, each at its zone matrix relative to the first such actor of the first --model; repeated models are allowed, every placed model must be selected')
    parser.add_argument('--group-lead',action='store_true',
        help='With --group-actors: line the other actors up behind the anchor along its nearest axis, one median spacing apart (a viewing rule for convoys whose spawn placements are not the towing order)')
    parser.add_argument('--group-path',action='store_true',
        help="With --group-lead: animate the group flying the anchor's ContainerShip FlightPath curve for one lap, the others trailing along it (speed and direction are viewing choices)")
    parser.add_argument('--group-path-speed',type=float,default=10.0,
        help="Preview speed of --group-path in game units per second (default 10; the game's speed is not decoded)")
    parser.add_argument('--blend-only',action='store_true',help='After a fully successful run, keep only the delivered .blend files and the viewer animation index: delete reports, meshes, textures, caches and validation')
    parser.add_argument('--lighting-source',choices=('veldin','novalis','hdri'),default='hdri',
        help='hdri (default): the shared review lighting from --lighting-config; veldin: opt-in captured Veldin key light, ambient, exposure and RC PS4 Game tonemap; novalis: decoded probe')
    parser.add_argument('--novalis-atmosphere-sun',action='store_true',help='Add the source-valued Novalis RGB96 Sun candidate; runtime field names remain pending')
    parser.add_argument('--no-skeleton',action='store_true',help='Export static meshes only: no joints, skin weights, rig or animation. Mirrors the viewer export option Skeleton off.')
    parser.add_argument('--no-animations',action='store_true',help='Keep the joints and skin weights but write no animation clips. Mirrors the viewer export option Include animations off.')
    parser.add_argument('--clip-map',type=Path,default=None,help='JSON file {"<archive path>": ["label:<viewer clip label>", ...]} (any --clip selector works): each listed model gets only those clips, in this one run (the Blender picker). Selectors that match only clips the pipeline cannot build are left out, and one that matches identical copies of a clip takes the first. An empty list writes that model without animations. Models not listed get every clip. Cannot be combined with --clip, --no-animations or --no-skeleton.')
    args=parser.parse_args()
    if bool(args.list_models_json) == bool(args.model or args.all_models):
        parser.error('Choose exactly one of --model/--all-models or --list-models-json')
    if args.no_skeleton and (args.clip or args.active_clip or args.cinematic_clips or args.borrow_animset):
        parser.error('--no-skeleton exports no animation; drop --clip, --active-clip, --cinematic-clips and --borrow-animset')
    if args.no_animations and (args.clip or args.active_clip or args.cinematic_clips or args.borrow_animset):
        parser.error('--no-animations writes no clips; drop --clip, --active-clip, --cinematic-clips and --borrow-animset')
    if args.clip_map and (args.clip or args.no_animations or args.no_skeleton):
        parser.error('--clip-map chooses the clips of each model; drop --clip, --no-animations and --no-skeleton')
    if args.group_actors and not args.group_blend:
        parser.error('--group-actors places the members of a group: add --group-blend NAME')
    if args.group_lead and not args.group_actors:
        parser.error('--group-lead moves a placed anchor: add --group-actors ZONE:PREFIX')
    if args.group_path and not args.group_lead:
        parser.error('--group-path flies a led convoy: add --group-lead')
    if args.group_path_speed<=0:
        parser.error('--group-path-speed must be positive')
    clip_map={}
    if args.clip_map:
        clip_map={model_path_key(path):list(clips) for path,clips in load(args.clip_map).items()}
    args.extracted_root,args.output_root=ensure_separate(args.extracted_root,args.output_root)
    if not args.extracted_root.is_dir():parser.error('ExtractedRoot is not a directory')
    from rc2016.pipeline.scene_defaults import load_shared_lighting
    try:args.shared_lighting=load_shared_lighting(args.lighting_config)
    except (OSError,ValueError) as exc:parser.error('Shared lighting file is unusable: %s'%exc)
    if args.hdri:
        args.hdri=args.hdri.resolve()
        if not args.hdri.is_file():parser.error('Required HDRI is missing: '+str(args.hdri))
        args.shared_lighting['hdri_path']=str(args.hdri)
    if args.lighting_source=='hdri':
        args.hdri=Path(args.shared_lighting['hdri_path'])
    args.veldin_look=None
    if args.lighting_source=='veldin':
        # The tonemap is built as compositor nodes from these tables: no OCIO setup is needed.
        view_dir=ROOT/'assets'/'game_view'
        args.veldin_look=dict(view_dir=str(view_dir),rc_game_view_sha256=file_hash(view_dir/'rc_game_view.json'),
            tonemap_sha256=file_hash(view_dir/'rc_tonemap.spi1d'),grade_sha256=file_hash(view_dir/'rc_grade.cube'),
            game_look_sha256=file_hash(ROOT/'rc2016/pipeline/game_look.py'),
            compositor_sha256=file_hash(ROOT/'rc2016/pipeline/game_view_compositor.py'))
    # Only the lock and log folders up front; a --list-models-json scan needs nothing else, and
    # remove_empty_dirs() tidies whatever a run leaves empty.
    for name in ('work','reports/logs'):
        (args.output_root/name).mkdir(parents=True,exist_ok=True)
    lock=args.output_root/'work/pipeline.lock'
    # OS advisory locking releases on process termination; stale files never block resume.
    lock_stream=open(lock,'a+b')
    if os.name=='nt':
        import msvcrt
        lock_stream.seek(0);lock_stream.write(b'0');lock_stream.flush();lock_stream.seek(0)
        try:msvcrt.locking(lock_stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('Another pipeline owns this OutputRoot')
    log=Log(args.output_root/'reports/logs/pipeline.jsonl')
    manifest_path=args.output_root/'reports/manifest.json'
    prior_manifest=load(manifest_path) if manifest_path.exists() else {}
    previous=prior_manifest.get('models',{})
    args.previous_implementation_sha256=prior_manifest.get('implementation_sha256',{})
    keep_blends_only=False
    manifest=dict(schema='ps4-animation-pipeline-v1',version=VERSION,source_root=str(args.extracted_root),output_root=str(args.output_root),models=previous.copy(),errors=[])
    try:
        warning=lz4_warning()
        if warning:
            log('warning',message=warning)
            print('WARNING: '+warning,file=sys.stderr,flush=True)
        archive_root=locate(args.extracted_root)
        # Stamped before Archive() hashes the metadata, so any later change is seen.
        args.archive_stamps=archive_file_stamps(archive_root)
        archive=Archive(archive_root,args.output_root,log);archive.index_loose(args.extracted_root)
        args.verified_sources=record_verified_sources(archive)
        manifest.update(archive_root=str(archive.root),archive_metadata_sha256=archive.metadata,archive_warnings=archive.warnings,
            indexed_assets=len(archive.entries),source_policy='read-only source; each selected entry and duplicate occurrence hashed',
            source_order='layout.csv order across animsets; recursive lookup order within animsets; physical order within directory-less wrappers')
        index=AssetIndex(archive);load_or_build_actor_index(index,archive,args.output_root,args.archive_stamps,log);index.save()
        if args.list_models_json:
            rows=[dict(id=e['id'],path=e['path'],available=any(o['available'] for o in e['occurrences']))
                  for e in archive.entries.values() if e['type']=='model']
            dump(args.list_models_json,dict(models=rows))
            log('model_index_ready',count=len(rows),path=str(args.list_models_json))
            # The unpacked actor files were only needed to build the index; tidying up never fails a scan.
            if not args.keep_intermediates:
                try:prune_intermediates(args.output_root,log)
                except OSError as exc:log('intermediates_prune_skipped',reason=str(exc))
            return 0
        selection=select_models(index,archive.entries,args.model,args.all_models)
        for name in ('work/tmp','compressed_assets','decoded_sources','animation_cache','blender','final','cache'):
            (args.output_root/name).mkdir(parents=True,exist_ok=True)
        # Reject a Blender-inaccessible OutputRoot before lighting or model
        # asset extraction begins. process_model repeats the guard for safety.
        for selected_key in selection:
            selected_asset=archive.entries[selected_key]
            selected_stem=safe_name(Path(selected_asset['path']).stem)+'_'+selected_key[:8]
            preflight_texture_output(args.output_root,args.output_root/selected_stem,args.export_textures)
        # Everything a build depends on: decoders, shader analysis, the pipeline (weapons included),
        # the viewer's per-graph material reading (materials.viewer_graph_rule), profiles and data.
        code={p.relative_to(ROOT).as_posix():file_hash(p) for folder in ('formats','shaders','pipeline')
              for p in sorted((ROOT/'rc2016'/folder).rglob('*.py'))}
        for relative in ('rc2016/common.py','rc2016/viewer/textures.py','third_party/opengnm/gcn_types.h',
                         'rc2016/pipeline/material_flags.json.gz'):
            code[relative]=file_hash(ROOT/relative)
        for folder in ('model_profiles','material_profiles'):
            for path in sorted((ROOT/'rc2016/pipeline'/folder).glob('*.json')):code[path.relative_to(ROOT).as_posix()]=file_hash(path)
        args.current_implementation_sha256=code
        manifest['implementation_sha256']=code;code_hash=sha(json.dumps(code,sort_keys=True).encode())
        # Only the Novalis source consumes the light-grid inventory; the scan decodes ~700 grids (~60 s per run).
        if args.lighting_source=='novalis':
            lighting_path=args.output_root/'reports/lighting_inventory.json'
            lighting=inventory_lighting(archive,lighting_path,log);archive.checkpoint()
            manifest['lighting_inventory']=str(lighting_path);manifest['lighting_status']=lighting['status']
            args.game_lighting=prepare_game_lighting(archive,lighting,args.output_root,NOVALIS_REGION,log,args.novalis_atmosphere_sun)
        else:
            manifest['lighting_status']=f'inventory_skipped_for_{args.lighting_source}_lighting'
            args.game_lighting=None
        manifest['lighting_source']=args.lighting_source
        if args.game_lighting:
            args.game_lighting['environment_exr']=str((args.output_root/'lighting'/'novalis'/'novalis_environment.exr').resolve())
            dump(args.output_root/'reports/selected_game_lighting.json',args.game_lighting)
            manifest['selected_game_lighting']=args.game_lighting
        def model_arguments(key):
            """The run's arguments for one model: its own clip list when --clip-map names it."""
            choice=clip_map.get(model_path_key(archive.entries[key]['path']))
            if choice is None:return args
            chosen=copy.copy(args);chosen.clip=choice;chosen.no_animations=not choice;chosen.clip_picked=True
            return chosen
        run_selected_models(
            selection, manifest,
            lambda key: process_model(key,index,model_arguments(key),code_hash,previous,log),
            archive.checkpoint, lambda: dump(manifest_path,manifest), log,
            model_name=lambda key: archive.entries[key]['path'])
        if args.group_blend:
            assemble_group_blend(selection,manifest,args,log,archive)
        index.save()
        validations={};unresolved={};unsupported={};geometry_models={}
        for key,result in manifest['models'].items():
            # A model recorded by an earlier run whose output folder was since deleted has no reports to aggregate.
            if result.get('validation') and Path(result['validation']).is_file() and Path(result['limitations']).is_file():
                validations[key]=load(result['validation']);limits=load(result['limitations'])
                unresolved[key]=dict(references=limits['unresolved_references'],attachments=limits['attachments'],dependency_errors=limits['dependency_errors'])
                unsupported[key]=dict(clips=limits['unsupported_clips'],animsets=limits['animset_errors'],geometry=limits['geometry'],features=limits['features_not_decoded'],animation_coverage=limits.get('animation_coverage'))
                geometry_models[key]=limits.get('geometry_game_equivalence')
        dump(args.output_root/'reports/validation_report.json',dict(models=validations,errors=manifest['errors'],ai_visual_review=False))
        dump(args.output_root/'reports/unresolved_references.json',unresolved)
        aggregate=Counter()
        for key in sorted(unsupported):
            aggregate.update(unsupported[key].get('animation_coverage',{}).get('counts',{}))
        unsupported_report=dict(unsupported,animation_coverage_summary=dict(schema='ps4-animation-coverage-aggregate-v1',
            counts=dict(sorted(aggregate.items())),models_with_coverage=sum(bool(value.get('animation_coverage')) for value in unsupported.values()),
            runtime_invocation='unknown for every source-related clip; see each model coverage for reasons'))
        dump(args.output_root/'reports/unsupported_clips.json',unsupported_report)
        success_statuses={'COMPLETE_MESH_MATERIALS_ANIMATIONS','COMPLETE_MESH_ANIMATIONS_MATERIAL_LIMITATION','COMPLETE_MESH_ANIMATIONS_TEXTURE_LIMITATION','ARMATURE_ANIMATIONS_ONLY'}
        selected_geometry={key:(geometry_models.get(key) or dict(schema='ps4-geometry-game-equivalence-v1',
            status='geometry_validation_unavailable',equivalent=False,
            structural=dict(status='unavailable',selected_visible_source_count=0,primary_source_count=0,
                visible_attachment_source_count=0,reopened_source_match_count=0,selected_mesh_count=0,
                mesh_manifest_primary_match=False,sources=[],reasons={'no successful reopened validation report':1}),
            semantic_equivalence=dict(status='unknown',subset_count=0,
                counts={aspect:dict(proven=0,unproven=0,unknown=0) for aspect in ('uv0','normals','tangents')},
                reasons={'no successful reopened validation report':1}),
            conclusion='No successful reopened geometry validation is available for this selected model.'))
            for key in selection}
        geometry_reasons=Counter();geometry_statuses=Counter();geometry_source_counts=Counter()
        for report in selected_geometry.values():
            geometry_statuses[report['status']]+=1
            structural=report.get('structural',{})
            for name in ('selected_visible_source_count','primary_source_count','visible_attachment_source_count',
                         'reopened_source_match_count','selected_mesh_count'):
                geometry_source_counts[name]+=int(structural.get(name,0) or 0)
            geometry_reasons.update(structural.get('reasons',{}))
            geometry_reasons.update(report.get('semantic_equivalence',{}).get('reasons',{}))
        run_geometry=dict(schema='ps4-geometry-game-equivalence-aggregate-v1',
            equivalent=bool(selected_geometry) and all(report.get('equivalent') is True for report in selected_geometry.values()),
            status='geometry_equivalent' if selected_geometry and all(report.get('equivalent') is True for report in selected_geometry.values()) else 'geometry_equivalence_unproven',
            selected_model_count=len(selected_geometry),model_status_counts=dict(sorted(geometry_statuses.items())),
            source_counts=dict(sorted(geometry_source_counts.items())),reasons=dict(sorted(geometry_reasons.items())),models=selected_geometry)
        dump(args.output_root/'reports/global_summary.json',dict(selected=len(selection),
            succeeded=sum(manifest['models'][k].get('status') in success_statuses for k in selection),
            unsupported=sum(manifest['models'][k].get('status')=='UNSUPPORTED' for k in selection),failed=len(manifest['errors']),
            geometry_game_equivalence=run_geometry))
        dump(manifest_path,manifest)
        if not manifest['errors'] and not args.keep_intermediates:
            prune_intermediates(args.output_root,log)
        if args.blend_only:
            keep_blends_only=True
            if manifest['errors']:log('blend_only_logs_kept',folder=str(args.output_root/LOG_FOLDER),
                reason='the run had errors: the pipeline log and the Blender logs are kept')
        log('finished',models=len(selection),failed=len(manifest['errors']))
        return 1 if manifest['errors'] else 0
    except Exception as exc:
        manifest['errors'].append(dict(error=str(exc),traceback=traceback.format_exc()));dump(manifest_path,manifest)
        log('pipeline_failed',message=str(exc));keep_blends_only=args.blend_only;return 1
    finally:
        log.file.close();lock_stream.close()
        try:lock.unlink()
        except OSError:pass
        if keep_blends_only:
            # Runs after the log and lock are closed, so their files can be removed too.
            errors=bool(manifest['errors'])
            removed=keep_only_blends(args.output_root,keep_logs=errors)
            print('blend-only output: removed %d files other than the delivered .blend files%s'%(
                removed,' (the logs of the failed run are kept in %s)'%(args.output_root/LOG_FOLDER) if errors else ''),flush=True)
        remove_empty_dirs(args.output_root)


if __name__=='__main__':raise SystemExit(main())









