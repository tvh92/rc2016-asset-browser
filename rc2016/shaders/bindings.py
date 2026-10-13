"""Bounded native pass contracts, parameter substitution and portable IR.

No shader family/name/hash chooses equations. Ambiguous serialized contracts
and engine values remain diagnostics, preserving the existing preview fallback.
"""
import hashlib
import json
import math
import struct
from functools import lru_cache

from rc2016.shaders.ir import DAG
from rc2016.shaders.translator import compile_graph, lower_deferred, sections
from rc2016.shaders.families import graph_parameter_tables

PORTABLE_OPS={'const','texture','input','add','sub','mul','div','neg','abs','min',
              'max','sqrt','rsqrt','rcp','log2','exp2','pow','sat','sin','cos',
              'lt','le','gt','ge','eq','ne','select','floor','fract'}


@lru_cache(maxsize=64)
def compiled(data):
    return compile_graph(data)


def pass_contracts(data):
    """Partition raw layout rows by source counts, including empty passes.

    Offset resets cannot represent an empty layout or one whose first key has
    a nonzero offset. BC counts bound both row slices; padded CB blocks also
    partition A59 defaults. Every row and default byte must be assigned.
    """
    from rc2016.formats.materials import texture_slot_records, GRAPH_TEXTURE_SLOTS
    spans=sections(data)
    base,size=spans.get(0xBC93FB5E,(0,0))
    if not size or size%24:raise ValueError('Missing bounded 24-byte pass contracts')
    resources=texture_slot_records(data,GRAPH_TEXTURE_SLOTS)
    tables,_=graph_parameter_tables(data)  # bounds and dword alignment
    entries=[entry for table in tables for entry in table]
    default_base,default_size=spans.get(0xA59F667B,(0,0))
    contracts=[];cursor=parameter_cursor=default_cursor=0
    for index in range(size//24):
        pos=base+index*24
        count=struct.unpack_from('<H',data,pos+14)[0]
        params=struct.unpack_from('<H',data,pos+18)[0]
        padded=struct.unpack_from('<I',data,pos+20)[0]
        if parameter_cursor+params>len(entries):
            raise ValueError('Pass parameter count exceeds layout rows')
        table=entries[parameter_cursor:parameter_cursor+params]
        extent=max((e['dword']*4+e['size_bytes'] for e in table),default=0)
        if padded!=(extent+15)//16*16 or cursor+count>len(resources):
            raise ValueError('Pass contract does not partition resources and padded CB layout')
        if any(e['size_bytes'] not in (4,8,12,16) for e in table):
            raise ValueError('Unsupported material parameter width')
        occupied=[]
        for e in table:occupied+=list(range(e['dword'],e['dword']+e['size_bytes']//4))
        if len(occupied)!=len(set(occupied)):raise ValueError('Overlapping material parameter layout')
        if len({e['key_hash'] for e in table})!=len(table):
            raise ValueError('Duplicate material parameter key in pass layout')
        if default_cursor+padded>default_size:
            raise ValueError('Pass defaults exceed padded default blocks')
        defaults={}
        for entry in table:
            width=entry['size_bytes']//4
            values=struct.unpack_from('<%df'%width,data,default_base+default_cursor+entry['dword']*4)
            if not all(math.isfinite(v) for v in values):
                raise ValueError('Non-finite graph parameter default')
            defaults[entry['key_hash']]=values[0] if width==1 else list(values)
        contracts.append(dict(index=index,resources=resources[cursor:cursor+count],
            table=table,cb_bytes=padded,defaults=defaults))
        cursor+=count;parameter_cursor+=params;default_cursor+=padded
    if cursor!=len(resources):raise ValueError('Unassigned graph resource records')
    if parameter_cursor!=len(entries):raise ValueError('Unassigned graph parameter records')
    if default_cursor!=default_size:raise ValueError('Unassigned graph default bytes')
    return contracts


def _parameter_values(contract,instance):
    rows={}
    for row in instance.get('parameters',[])+instance.get('unsupported_parameters',[]):
        key=row['key_hash'];raw=bytes.fromhex(row['raw_hex'])
        if key in rows and rows[key]!=raw:raise ValueError('Conflicting material parameter overrides')
        rows[key]=raw
    values={};receipt=[]
    for entry in contract['table']:
        key=entry['key_hash'];width=entry['size_bytes']//4
        raw=rows.get(key)
        if raw is not None:
            if len(raw)!=entry['size_bytes']:raise ValueError('Material override width differs from CB layout')
            val=list(struct.unpack('<%df'%width,raw));origin='material_instance'
        else:
            val=contract['defaults'].get(key)
            val=val if isinstance(val,list) else [val]
            if len(val)!=width or any(v is None for v in val):continue
            origin='graph_default'
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in val):
            raise ValueError('Non-finite float material parameter')
        for j,v in enumerate(val):values[entry['dword']+j]=v
        receipt.append(dict(key_hash=key,dword=entry['dword'],values=val,source=origin))
    return values,receipt


def guard_source_zero_powers(ir,roots):
    """Preserve the finite result of native LOG/MUL/EXP at zero.

    GCN log2(0) is -infinity; a proven positive exponent followed by exp2
    produces zero. Both portable emitters intentionally use finite log guards,
    so retain the original positive-input instruction chain and gate its result.
    Only abs() or a nonnegative literal proves the base's domain here. Unknown,
    zero and negative exponents and standalone logarithms are left intact.
    """
    memo={};guards=[]
    def visit(i):
        if i in memo:return memo[i]
        n=ir.nodes[i];args=[visit(j) for j in n['args']]
        value=(ir.op(n['op'],*args) if n['op'] in PORTABLE_OPS-{'const','input','texture'}
            else ir.node(n['op'],args,**{k:v for k,v in n.items() if k not in ('op','args')}))
        if n['op']=='exp2':
            term=ir.nodes[args[0]];exponent=1.;log=args[0]
            if term['op']=='mul':
                pair=term['args'];j=next((j for j,q in enumerate(pair) if ir.value(q) is not None),None)
                if j is not None:exponent=ir.value(pair[j]);log=pair[1-j]
                else:exponent=None
            entry=ir.nodes[log]
            if entry['op']=='log2' and exponent is not None and exponent>0:
                base=entry['args'][0];source=ir.nodes[base];literal=ir.value(base)
                if source['op']=='abs' or (literal is not None and literal>=0):
                    zero=ir.const(0.);positive=ir.op('gt',base,zero)
                    value=ir.op('select',positive,value,zero)
                    guards.append(dict(source_exp2_node=i,exponent=exponent))
        memo[i]=value;return value
    return [visit(i) for i in roots],guards


def _lowered(program):
    """Material-independent lowering, cached on the compiled program (callers get a fresh limitations list)."""
    cached=program.get('_lowered')
    if cached is None:cached=program['_lowered']=lower_deferred(program['ir'])
    return dict(cached,limitations=list(cached['limitations']))


def _bind(program,contract,material_data,instance):
    from rc2016.formats.materials import texture_slot_records, MATERIAL_TEXTURE_SLOTS
    overrides={}
    for row in texture_slot_records(material_data,MATERIAL_TEXTURE_SLOTS):
        key=(row['slot_key'],row['type_hash'])
        if key in overrides and overrides[key]['path']!=row['path']:
            raise ValueError('Conflicting source texture overrides')
        overrides[key]=row
    bound={}
    for j,row in enumerate(contract['resources']):
        chosen=overrides.get((row['slot_key'],row['type_hash']),row) if row['flag']==1 else row
        if row['flag'] not in (0,1):raise ValueError('Unsupported resource override flag')
        bound[5+j]=dict(api=5+j,path=chosen['path'],slot_key=row['slot_key'],
            type_hash=row['type_hash'],source='material_instance' if chosen is not row else 'graph_default')
    values,receipt=_parameter_values(contract,instance)
    lowered=_lowered(program);ir=DAG(lowered['nodes']);outputs=lowered['outputs']
    normal=lowered['normal']
    if normal and bound.get(normal['api'],{}).get('path') is None:
        lowered['limitations'].append('Explicitly unset normal texture uses geometry normal.')
        normal=None
    roots=[*outputs['albedo'],*outputs['F0'],outputs['gloss'],outputs['emission']]
    if normal:roots+=normal['xyz']
    substitutions={i:ir.const(values[n['dword']]) for i,n in enumerate(list(ir.nodes))
        if n['op']=='input' and n.get('kind')=='cb' and n['api']==2 and n['dword'] in values}
    replaced=ir.replace(roots,substitutions)
    replaced,zero_guards=guard_source_zero_powers(ir,replaced)
    if zero_guards:
        lowered['limitations'].append('Source LOG/MUL/EXP with a proven nonnegative base and positive bound exponent preserves zero; positive-input arithmetic is unchanged. Other singular math retains portable frontend semantics.')
    outputs=dict(albedo=replaced[:3],F0=replaced[3:6],gloss=replaced[6],emission=replaced[7])
    if normal:normal=dict(normal,xyz=replaced[8:11])
    used=ir.reachable(replaced);errors=[]
    for i in sorted(used):
        n=ir.nodes[i]
        if n['op'] not in PORTABLE_OPS:errors.append(dict(node=i,reason=n.get('reason',n['op'])))
        elif n['op']=='input' and not (n.get('kind')=='attribute' and n.get('attribute')==0 and n.get('channel') in (0,1)):
            errors.append(dict(node=i,reason='Unbound engine/interpolant input '+n['name']))
        elif n['op']=='texture' and not bound.get(n['api'],{}).get('path'):
            errors.append(dict(node=i,reason='Sampled material texture is unset or unbound',api=n['api']))
    if errors:raise ValueError(json.dumps(errors[:12],sort_keys=True))
    # Compact after substitutions so receipts never expose dead engine inputs.
    compact=DAG();remap={}
    def copy(i):
        if i not in remap:
            n=ir.nodes[i];remap[i]=compact.node(n['op'],[copy(j) for j in n['args']],
                **{k:v for k,v in n.items() if k not in ('op','args')})
        return remap[i]
    newroots=[copy(i) for i in replaced]
    outputs=dict(albedo=newroots[:3],F0=newroots[3:6],gloss=newroots[6],emission=newroots[7])
    if normal:normal=dict(normal,xyz=newroots[8:11])
    active={n['api'] for n in compact.nodes if n['op']=='texture'}
    return dict(nodes=compact.nodes,outputs=outputs,normal=normal,
        texture_bindings=[bound[a] for a in sorted(active)],parameters=receipt,
        pass_contract_index=contract['index'],limitations=lowered['limitations'],source_zero_power_guards=zero_guards)


def program_contracts(data,program,contracts):
    """Join native envelope to its source pass, never to a sorted texture list.

    Corpus controls establish 13 serialized shader records per BC pass. The
    byte pointer and resource bitmap are checked again on every new graph.
    Broader/dynamic descriptor layouts are deliberately left unsupported.
    """
    spans=sections(data);base,size=spans.get(0xB6D49474,(0,0))
    if size!=len(contracts)*13*20:raise ValueError('Unsupported serialized shader/pass partition')
    shader_base,shader_size=spans[0x873D1E6C];matches=[]
    used={n['api'] for n in program['ir']['nodes'] if n['op']=='texture'}
    apis={s['api'] for s in program['usage_slots'] if s['kind']==0 and s['api']>=5}
    cb=[n['dword'] for n in program['ir']['nodes'] if n['op']=='input' and n.get('kind')=='cb' and n.get('api')==2]
    for i in range(size//20):
        cb_mask,resource_mask,technique,relative,blob_size=struct.unpack_from('<5I',data,base+i*20)
        if not blob_size or shader_base+relative+36!=program['header_offset']:continue
        if relative+blob_size>shader_size:raise ValueError('Serialized shader blob exceeds shader section')
        contract=contracts[i//13]
        if not apis or max(apis)-4!=len(contract['resources']):continue
        if any(a is None or a>=32 or not resource_mask&(1<<a) for a in used):continue
        if cb and (not cb_mask&4 or contract['cb_bytes']!=(max(cb)+1+3)//4*16):continue
        matches.append(contract)
    return matches


def surface_signature(bound):
    """Exact structural identity of the portable, source-bound surface slice.

    API registers, sample labels and DAG indices do not change a pure sample
    operation. Source image identity, normal interpretation, UV expressions,
    channels, literals and ordered math operations do. This is not an
    algebraic simplifier or a proof that omitted runtime terms agree.
    """
    bindings={b['api']:b for b in bound['texture_bindings']}
    normal=bound['normal'];normal_api=normal['api'] if normal else None
    memo={}
    def node(i):
        if i not in memo:
            n=bound['nodes'][i];op=n['op'];args=[node(j) for j in n['args']]
            if op=='const':record=[op,n['value']]
            elif op=='input':record=[op,n['kind'],n['attribute'],n['channel']]
            elif op=='texture':
                b=bindings[n['api']]
                record=[op,b['path'],b['type_hash'],n['api']==normal_api,n['channel'],args]
            else:record=[op,args]
            memo[i]=hashlib.sha256(json.dumps(record,separators=(',',':')).encode()).hexdigest()
        return memo[i]
    result={key:[node(i) for i in value] if isinstance(value,list) else node(value)
        for key,value in bound['outputs'].items()}
    result['normal']=[node(i) for i in normal['xyz']] if normal else None
    return hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()


def prepare_translation(graph_datas,material_data,instance):
    result=dict(schema='rcps4-bound-shader-translation-v1',status='unsupported',diagnostics=[],game_equivalence=False)
    if len(graph_datas)!=1:
        result['diagnostics'].append('Translation requires one unambiguous source graph');return result
    data=bytes(graph_datas[0]);graph=compiled(data)
    try:contracts=pass_contracts(data)
    except ValueError as exc:result['diagnostics'].append(str(exc));return result
    candidates=[]
    for program in graph['programs']:
        if program['status']!='compiled':continue
        try:matched=program_contracts(data,program,contracts)
        except ValueError as exc:
            result['diagnostics'].append(str(exc));continue
        for contract in matched:
            try:bound=_bind(program,contract,material_data,instance)
            except ValueError as exc:
                result['diagnostics'].append(dict(shader_id=program['shader_id'],contract=contract['index'],reason=str(exc)));continue
            signature=json.dumps({k:bound[k] for k in ('nodes','outputs','normal','texture_bindings')},sort_keys=True)
            candidates.append(dict(bound=bound,program=program,signature=signature,
                surface_signature=surface_signature(bound)))
    if not candidates:
        result['diagnostics'].append('No portable deferred writer with a bounded source pass contract');return result
    signatures={c['surface_signature'] for c in candidates}
    if len(signatures)!=1:
        result['diagnostics'].append('Multiple portable native variants produce different material programs');return result
    candidate=candidates[0];program=candidate['program']
    result.update(candidate['bound'],status='ready',graph_sha256=graph['graph_sha256'],
        code_sha256=program['code_sha256'],code_offset=program['code_offset'],shader_id=program['shader_id'],
        equivalent_candidates=len(candidates),translator_version=graph['translator_version'],binding_version=3,
        surface_sha256=candidate['surface_signature'],candidate_sources=[dict(
            shader_id=c['program']['shader_id'],code_sha256=c['program']['code_sha256'],
            pass_contract_index=c['bound']['pass_contract_index']) for c in candidates])
    result['limitations']=list(dict.fromkeys(
        limitation for c in candidates for limitation in c['bound']['limitations']))
    result['limitations']+=graph['limitations']+[
        'B6 native-envelope pointer selects its 13-record pass partition; BC counts partition resources/parameters and padded default blocks. Material APIs start at 5, bitmap/span and padded CB2 checks bound support.',
        'Portable candidates must have identical source-bound surface expressions, including UV arithmetic, channels and normal interpretation. API/sample labels are ignored only for this comparison; omitted runtime terms and runtime variant selection are not established.',
        'UV attribute 0.xy is source UV0 in game space (V unflipped for arithmetic, flipped back per sample). Runtime draw selection is not established.',
        'Texture sampling uses Blender linear filtering and repeat addressing; native sampler-state translation is not implemented.',
        'Shared deferred BRDF is the existing Blender approximation; this is not full game shader equivalence.']
    result['translation_sha256']=hashlib.sha256(candidate['signature'].encode()).hexdigest()
    return result
