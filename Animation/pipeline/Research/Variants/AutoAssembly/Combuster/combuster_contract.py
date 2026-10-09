"""Hash-pinned Combuster source linkage and source-local assembly checks."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from Animation.pipeline.variant_links import resolve_source_linked_skin_pair

ASSETS = {
    'main': ('35fc18518fd23793517a', '4fc16bd85df3c7475e1f9e6f91c0033d49d648811c429fce8d67fdbe8b197e0a'),
    'actor': ('d69c4fd50e6200e0664c', 'ba11f218eec05b070878bb94ada87f53674122b8e4d4bfc9036ad3476c343417'),
    'animset': ('b241897a99bf2835d5e1', 'f2ddf80c0403964ec18df0fc307f42d7f9f9d4d308ae72b82970f31844e41b5c'),
    'v1': ('568ebae49373ef2060ed', 'bdc2274b4657267de7d35e0b61b1dd9689763c1398dcadeaa24bef4824492842'),
    'v5': ('a88557a5223d580f11bd', 'e1d8128a197931b9e354e283345366c36a236beeb7af6ac3aaceac6fe3e335f1'),
    'config': ('993b32a5f4bb1fc65031', '437a7b6a33e9053a44e2921e2a53b8d1e61fe6a0cf05eb67b10da6ae562d61f4'),
}
PATHS = {
    'main': 'equipment/weapon/wpn_blaster_combuster/wpn_blaster_combuster.model',
    'actor': 'equipment/weapon/wpn_blaster_combuster/wpn_blaster_combuster.actor',
    'animset': 'equipment/weapon/wpn_blaster_combuster/wpn_blaster_combuster.animset',
}
CONFIG_TRACE = 'Animation/pipeline/Research/Variants/SourceLinks/combuster_config_trace.json'
CONFIG_SHA = '437a7b6a33e9053a44e2921e2a53b8d1e61fe6a0cf05eb67b10da6ae562d61f4'
V1_JOINTS = {'a_body','CTR_hose','barrel_inner','LF_tank','RT_tank','barrel_outter'}
SUPPORTED_CLIP = 'Turn_360_wpn_Blaster_Combuster'
AMBIGUOUS_CLIPS = {'Fire_wpn_Blaster_Combuster','Open_wpn_Blaster_Combuster','Idle_wpn_Blaster_Combuster'}
SCHEMA = 'combuster-source-linked-assembly-v1'

def resolve_combuster_pair(archive, selected_id):
    expected={'config_id':ASSETS['config'][0],'config_sha256':ASSETS['config'][1],
        'v1_id':ASSETS['v1'][0],'v1_sha256':ASSETS['v1'][1],
        'v5_id':ASSETS['v5'][0],'v5_sha256':ASSETS['v5'][1]}
    pair=resolve_source_linked_skin_pair(archive,selected_id,expected)
    if pair.get('status')!='source_exact': return pair
    for role in ('main','actor','animset'):
        ident,digest=ASSETS[role]
        if archive.resolve(PATHS[role]) != [ident]:
            return {'status':'unsupported','reason':f'pinned {role} source path/asset ID changed'}
        decoded=archive.decode(ident)
        if hashlib.sha256(decoded.read_bytes()).hexdigest()!=digest:
            return {'status':'unsupported','reason':f'pinned {role} source decoded hash changed'}
    if selected_id!=ASSETS['v1'][0]: return {'status':'not_applicable','reason':'not exact Combuster V1'}
    trace={**pair,'models':{'v1':pair['models']['DefaultSkinModels'],'v5':pair['models']['UpgradedSkinModels']},
        'main':{'asset_id':ASSETS['main'][0],'path':PATHS['main'],'sha256':ASSETS['main'][1]},
        'actor':{'asset_id':ASSETS['actor'][0],'path':PATHS['actor'],'sha256':ASSETS['actor'][1]},
        'animset':{'asset_id':ASSETS['animset'][0],'path':PATHS['animset'],'sha256':ASSETS['animset'][1]}}
    trace['limitations']=['Config-authored V1/V5 mapping is source-exact.','Runtime actor version choice is not established.',
        'Placement uses same-source-coordinate native model matrices and matched source rigs; no config attachment matrix is present.',
        'Materials are pipeline reconstructions; game shader equivalence and tangent semantics are unproven.']
    return trace

def assert_joint_subset(main_rows, part_rows, tolerance=1e-6):
    main_order=list(main_rows);main={r['name']:r for r in main_rows}; names={r['name'] for r in part_rows}
    if names!=V1_JOINTS or len(names)!=len(part_rows): raise AssertionError(f'Unexpected Combuster package joint set: {names}')
    for row in part_rows:
        parent=row['parent']; parent_name=part_rows[parent]['name'] if parent>=0 else None
        target=main.get(row['name'])
        if not target: raise AssertionError(f'Missing shared rig joint {row["name"]}')
        target_parent=main_order[target['parent']]['name'] if target['parent']>=0 else None
        if parent_name!=target_parent: raise AssertionError(f'Parent mismatch for {row["name"]}')
        for key in ('scale','rotation_xyzw','translation'):
            err=max(abs(float(a)-float(b)) for a,b in zip(row[key],target[key]))
            if err>tolerance: raise AssertionError(f'Rest {key} mismatch at {row["name"]}: {err}')
    return [{'name':r['name'],'main_index':next(i for i,x in enumerate(main_order) if x['name']==r['name'])} for r in part_rows]

VISIBLE={'V1':{'main':{0,1},'v1':{0,1},'v5':set()},'V5':{'main':{0,1},'v1':set(),'v5':{0,1,2}}}
def assert_visible(state,actual):
    got={k:set(actual.get(k,())) for k in ('main','v1','v5')}
    if state not in VISIBLE or got!=VISIBLE[state]: raise AssertionError(f'{state}: expected {VISIBLE.get(state)}, got {got}')

def negative_completeness_control():
    try: assert_visible('V5',{'main':{0},'v1':set(),'v5':{0,1,2}})
    except AssertionError: return True
    raise AssertionError('Missing shared main sections passed negative control')

def assert_clip_coverage(included, unsupported, experimental=False):
    unexpected={r.get('name') for r in included if r.get('name') not in AMBIGUOUS_CLIPS|{SUPPORTED_CLIP}}
    if unexpected:raise AssertionError(f'Unexpected Combuster included clip set: {unexpected}')
    supported=[r for r in included if r.get('name')==SUPPORTED_CLIP]
    if len(supported)!=1 or supported[0].get('status')!='included':
        raise AssertionError(f'Only one source-supported Turn_360 clip is expected: {supported}')
    rows={r.get('name'):r for r in unsupported if r.get('name') in AMBIGUOUS_CLIPS}
    ambiguous=[r for r in included if r.get('name') in AMBIGUOUS_CLIPS]
    if experimental:
        if rows or any(not r.get('experimental_inheritance_preview') for r in ambiguous) or {r['name'] for r in ambiguous}!=AMBIGUOUS_CLIPS:
            raise AssertionError('Experimental mode must represent ambiguous clips as labelled included candidates')
        for name in AMBIGUOUS_CLIPS:
            candidates=[r for r in ambiguous if r['name']==name]
            if len(candidates)!=2 or {r.get('inheritance') for r in candidates}!={'full','segment_scale_compensation_bit0'}:
                raise AssertionError(f'{name}: both labelled inheritance candidates are required')
            first=candidates[0]
            for row in candidates:
                if (row.get('status')!='included' or row.get('experimental_game_accuracy')!='unproven' or
                    set(row.get('experimental_inheritance_candidates',[]))!={'full','segment_scale_compensation_bit0'} or
                    any(row.get(k)!=first.get(k) for k in ('raw_sha256','sample_sha256','frames','fps','animset_sha256')) or
                    not row.get('action') or not row.get('raw_clip') or not row.get('samples') or
                    'animated scales' not in row.get('experimental_reason','').lower() or
                    'not a game-runtime claim' not in row.get('experimental_reason','').lower()):
                    raise AssertionError(f'{name}: experimental candidate lost source timing/hash metadata: {row}')
        return {'mode':'experimental_unproven','supported':[dict(name=SUPPORTED_CLIP,action=supported[0]['action'],
            raw_sha256=supported[0]['raw_sha256'],sample_sha256=supported[0]['sample_sha256'],frames=supported[0]['frames'],fps=supported[0]['fps'])],
            'experimental':[dict(name=r['name'],action=r['action'],inheritance=r['inheritance'],raw_sha256=r['raw_sha256'],
                sample_sha256=r['sample_sha256'],frames=r['frames'],fps=r['fps'],animset_sha256=r['animset_sha256'],
                reason=r['experimental_reason'],status='experimental_unproven') for r in sorted(ambiguous,key=lambda x:(x['name'],x['inheritance']))],
            'unsupported':[]}
    if rows or {r['name'] for r in ambiguous}!=AMBIGUOUS_CLIPS or len(ambiguous)!=len(AMBIGUOUS_CLIPS) or any(
            r.get('status')!='included' or r.get('inheritance')!='segment_scale_compensation_bit0' or
            r.get('inheritance_basis')!='pipeline_default_ssc0' or r.get('experimental_inheritance_preview') for r in ambiguous):
        raise AssertionError(f'Default mode must include Fire/Open/Idle once under the default SSC0 rule: included={ambiguous}, unsupported={rows}')
    return {'mode':'default_ssc0','supported':[dict(name=SUPPORTED_CLIP,action=supported[0]['action'],
        raw_sha256=supported[0]['raw_sha256'],sample_sha256=supported[0]['sample_sha256'],frames=supported[0]['frames'],fps=supported[0]['fps'])],
        'default_ssc0':[dict(name=r['name'],action=r['action'],inheritance=r['inheritance'],raw_sha256=r['raw_sha256'],
            sample_sha256=r['sample_sha256'],frames=r['frames'],fps=r['fps'],animset_sha256=r['animset_sha256'],
            difference=r.get('inheritance_candidate_difference'),status='default_SSC0') for r in sorted(ambiguous,key=lambda x:x['name'])],
        'experimental':[],'unsupported':[]}

def receipt_fingerprint(inputs,blend_sha256,validation_sha256):
    data={'schema':SCHEMA,'inputs':inputs,'blend_sha256':blend_sha256,'validation_sha256':validation_sha256}
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def verify_receipt(receipt,inputs,blend_path,validation_path):
    if not isinstance(receipt,dict) or receipt.get('schema')!=SCHEMA or receipt.get('status')!='passed' or receipt.get('inputs')!=inputs:
        return {'valid':False,'reason':'Combuster receipt schema/status/input mismatch'}
    blend_path,validation_path=Path(blend_path),Path(validation_path)
    if not blend_path.is_file() or not validation_path.is_file(): return {'valid':False,'reason':'delivery/report missing'}
    bh=hashlib.sha256(blend_path.read_bytes()).hexdigest(); vh=hashlib.sha256(validation_path.read_bytes()).hexdigest()
    if receipt.get('blend_path')!=str(blend_path.resolve()) or receipt.get('blend_sha256')!=bh or receipt.get('validation_path')!=str(validation_path.resolve()) or receipt.get('validation_sha256')!=vh:
        return {'valid':False,'reason':'delivery/report path or hash mismatch'}
    report=json.loads(validation_path.read_text(encoding='utf-8'))
    if report.get('status')!='passed' or report.get('blend_sha256')!=bh or report.get('delivered_blend_sha256')!=bh:
        return {'valid':False,'reason':'reopen report does not match delivered blend'}
    want=receipt_fingerprint(inputs,bh,vh)
    if receipt.get('fingerprint')!=want: return {'valid':False,'reason':'Combuster fingerprint mismatch'}
    return {'valid':True,'fingerprint':want,'reason':'all source, implementation and reopen hashes match'}
