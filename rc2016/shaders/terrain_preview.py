"""Opt-in Novalis layer review, separate from production native acceptance.

Source arithmetic and bindings are recovered from an exact native source pass.
Painted blend channels and sampling coordinates are explicit review controls.
The generated portable IR can be consumed by both GLSL and Blender emitters.
"""
from collections import deque
import hashlib
import json
import math
import struct
from rc2016.formats.materials import MATERIAL_TEXTURE_SLOTS,texture_slot_records,extract_material_parameters
from rc2016.shaders.translator import pixel_programs,sections,CARRY_OUT,Compiler,lower_deferred
from rc2016.shaders.bindings import pass_contracts,_parameter_values,PORTABLE_OPS
from rc2016.shaders.gcn_fields import decode_stream
from rc2016.shaders.ir import DAG

MATERIAL='material/environment/novalis/ground/nov_ground_grass_01/nov_ground_grass_01.material'
GRAPH='materialgraph/environment/gaspar/rock/gas_rock_tile_01/gas_rock_tile_01.materialgraph'
MATERIAL_SHA='74166e3f912e20aaa341254c36be533359a95417d44d6d39e0b4d0592c993b66'
GRAPH_SHA='2a4bc1df5ada906e9dc64f1bebcd000076a4cfc46799fd9891feb31df8a46770'
CODE_SHA='07b2b05324b6bcaf675bb6bb3d3833a5087f38017a88bb13ebaf365159d28a3d'
DEFAULT_CONTROLS=(0.75,0.5,1.0)
CONTRACT='novalis-ground-uniform-layers-v1'
NOTE='Ground layers: uniform blend channels and UV0 preview; original painted paths unresolved.'
LIMITATIONS=[
    'Experimental layer review: uniform red/green channels replace unresolved painted streams.',
    'UV0 times review scale replaces the unresolved planar/parallax sampling path.',
    'Geometry normal; source composed normals, derivative AA, sampler state and runtime pass selection are not reproduced.',
    'Source pass/texture/parameter bindings and color/F0/flat gloss arithmetic are retained; this is not a ready production native material.']

class SuffixCompiler(Compiler):
    def instruction(self,i):
        # The production unsupported-load placeholder clobbers four registers.
        # FORMAT_X actually writes one. Keep its value unsupported, while
        # preserving adjacent proven arithmetic in this research-only slice.
        if i['name']=='GCN_TBUFFER_LOAD_FORMAT_X':
            self.current=i['relative']
            self.v[i['fields']['dst'][0]]=self.unknown('TBUFFER_LOAD_FORMAT_X: unresolved source value')
            return
        return super().instruction(i)

def _trace(p,gd,md):
    contracts=pass_contracts(gd)
    overrides={(r["slot_key"],r["type_hash"]):r for r in texture_slot_records(md,MATERIAL_TEXTURE_SLOTS)}
    spans=sections(gd); base,size=spans[0xB6D49474]; sb,ss=spans[0x873D1E6C]
    ins=decode_stream(p['code'],0); bypos={r['relative']:j for j,r in enumerate(ins)}
    initial=[None]*128
    for slot in p['usage_slots']:
        width=8 if slot['kind']==0 else 4 if slot['kind'] in (1,2) else 2
        for j in range(width):
            initial[slot['reg']+j]=('descriptor',slot['kind'],slot['api'],j)
    incoming={0:tuple(initial)}; queue=deque([0]); visits=0
    def transform(i,state):
        s=list(state); k=i['kind']; f=i['fields']; w=i['word']; q=i['wide']; n=i['name'].replace('GCN_','').replace('_E64','')
        if k==10:
            origin=s[((w>>9)&63)*2]; dst=(w>>15)&127
            count=int(n.rsplit('X',1)[1]) if 'DWORDX' in n else 1
            off=(w&255)*4 if w&256 else (s[w&255][1] if s[w&255] and s[w&255][0]=='constant' else None)
            for j in range(count):
                s[dst+j]=None
                if origin and origin[0]=='descriptor' and off is not None:
                    kind=origin[1]
                    if kind==2 and 'BUFFER_LOAD' in n and origin[3]==0:
                        s[dst+j]=('cb',origin[2],off//4+j)
                    if kind in (0x13,0x15) and origin[3]==0:
                        stride=32 if kind==0x13 else 16
                        if off%stride==0:
                            s[dst+j]=('descriptor',0 if kind==0x13 else 1,off//stride,j)
        elif k in (0,1,2,3):
            dst=f.get('dst',[None])[0] if f.get('dst') else ((w>>16)&127 if k==3 and not n.startswith(('S_CMPK','S_SETREG')) else None)
            if dst is not None:
                count=2 if 'B64' in n else 1
                copied=[None]*count
                if n in ('S_MOV_B32','S_MOV_B64'):
                    src=f['src'][0]
                    if src<=127:copied=s[src:src+count]
                    elif 128<=src<=208:
                        val=src-128 if src<=192 else 192-src
                        copied=[('constant',val&0xffffffff)]+([('constant',0xffffffff if val<0 else 0)] if count==2 else [])
                    elif src==255 and count==1:copied=[('constant',struct.unpack_from('<I',p['code'],i['relative']+4)[0])]
                for j in range(count):s[dst+j]=copied[j]
        elif k in (5,6,7,8):
            if 'CMP' in n or n in ('V_READFIRSTLANE_B32','V_READLANE_B32'):
                dst=f['dst'][0]; s[dst]=None
                if 'CMP' in n and dst+1<128:s[dst+1]=None
            if n in CARRY_OUT:
                dst=106 if k==6 else (q>>8)&127
                s[dst]=None
                if dst+1<128:s[dst+1]=None
        return tuple(s)
    while queue:
        j=queue.popleft(); visits+=1; i=ins[j]; state=transform(i,incoming[j]); n=i['name'].replace('GCN_',''); edges=[]
        if 'BRANCH' in n:
            target=i['relative']+i['fields']['branch_target_relative']
            assert target in bypos,(n,target)
            edges.append(bypos[target])
            if n!='S_BRANCH' and j+1<len(ins):edges.append(j+1)
        elif n not in ('S_ENDPGM','S_SETPC_B64','S_SWAPPC_B64') and j+1<len(ins):edges.append(j+1)
        for nxt in edges:
            previous=incoming.get(nxt)
            merged=state if previous is None else tuple(x if x==y else None for x,y in zip(previous,state))
            if merged!=previous:incoming[nxt]=merged;queue.append(nxt)
        assert visits<100000,'CFG did not converge'
    samples=[]; loads=[]
    for j,i in enumerate(ins):
        if j not in incoming:continue
        if i['kind']==14:
            q=i['wide']; reg=((q>>48)&31)*4; samp=((q>>53)&31)*4
            samples.append(dict(offset=i['relative'],opcode=i['name'],resource_sgpr=reg,sampler_sgpr=samp,
                                resource_descriptor=incoming[j][reg],sampler_descriptor=incoming[j][samp],
                                destination_vgpr=(q>>40)&255,channel_mask=(q>>8)&15))
        if i['kind']==10:
            w=i['word']; origin=incoming[j][((w>>9)&63)*2]
            if origin and origin[0]=='descriptor' and origin[1] in (0x13,0x15):
                loads.append(dict(offset=i['relative'],opcode=i['name'],pointer=origin,immediate=bool(w&256),byte_offset=(w&255)*4 if w&256 else None,destination_sgpr=(w>>15)&127))
    joins=[]
    for j in range(size//20):
        cb,rm,tech,rel,bs=struct.unpack_from('<5I',gd,base+20*j)
        if bs and sb+rel+36==p['header_offset']:
            assert rel+bs<=ss
            c=contracts[j//13]
            bound=[]
            for api,row in enumerate(c['resources'],5):
                chosen=overrides.get((row['slot_key'],row['type_hash']),row) if row['flag']==1 else row
                bound.append(dict(api=api,slot_key=f"0x{row['slot_key']:08X}",path=chosen['path'],type_hash=row['type_hash']))
            values,parameters=_parameter_values(c,extract_material_parameters(md))
            joins.append(dict(record=j,pass_index=j//13,resource_mask=hex(rm),bound_resources=bound,
                              bound_cb2_dwords=values,parameter_receipt=parameters))
    assert len(joins)==1
    allowed={r['api'] for r in joins[0]['bound_resources']}
    resource_mask=int(joins[0]['resource_mask'],16)
    for sample in samples:
        resource,sampler=sample['resource_descriptor'],sample['sampler_descriptor']
        assert resource and sampler and resource[3]==sampler[3]==0,sample
        assert resource[2]==sampler[2] and resource[2] in allowed,sample
        assert resource_mask & (1<<resource[2]),sample
    # Decode only the straight-line suffix after all mask/branch transitions.
    # Every upstream VGPR is an explicit boundary input, never a guessed value.
    boundaries=[]
    for i in ins:
        n=i['name']
        if 'BRANCH' in n or 'SAVEEXEC' in n:
            boundaries.append(i['relative']+i['length'])
            if 'BRANCH' in n:boundaries.append(i['relative']+i['fields']['branch_target_relative'])
    start=max(boundaries,default=0); cj=bypos[start]
    compiler=SuffixCompiler(p['code'],p['usage_slots'])
    compiler.descriptors={}; compiler.s={}
    for reg,value in enumerate(incoming[cj]):
        if value and value[0]=='descriptor':
            compiler.descriptors[reg]=dict(kind=value[1],api=value[2],word=value[3])
        elif value and value[0]=='cb':
            compiler.s[reg]=compiler.ir.node('input',name=f'CB{value[1]}[{value[2]}]',kind='cb',api=value[1],dword=value[2])
        elif value and value[0]=='constant':
            compiler.s[reg]=compiler.ir.const(value[1]);compiler.scalar_bits[reg]=value[1]
    for reg in range(256):
        compiler.v[reg]=compiler.ir.node('input',name=f'BoundaryVGPR{reg}@{start}',kind='suffix_boundary',register=reg)
    excluded_exec_restores=[]
    for i in ins[cj:]:
        if i['name']=='GCN_S_MOV_B64' and i['fields'].get('dst',[None])[0]==126:
            excluded_exec_restores.append(i['relative'])
            continue
        compiler.instruction(i)
    suffix=dict(start_offset=start,exports=compiler.exports,
                nodes=compiler.ir.nodes,samples=compiler.samples,excluded_exec_restores=excluded_exec_restores,
                equations={str(k):[compiler.ir.describe(r,15) if r is not None else None for r in v] for k,v in compiler.exports.items()},
                limit='Straight-line output suffix only; upstream registers are explicit boundary inputs. TBUFFER_FORMAT_X value remains unknown and has its actual one-register write width. Not a ready material or normal/parallax reconstruction.')
    return dict(shader_id=p['shader_id'],code_sha256=p['code_sha256'],usage=p['usage_slots'],
                instructions=len(ins),cfg_visits=visits,loads=loads,samples=samples,serialized_pass_join=joins,
                all_sample_descriptors_known=all(s['resource_descriptor'] and s['sampler_descriptor'] for s in samples),straight_line_suffix=suffix)

def prepare_preview(archive,path):
    """Separate experimental payload, never a native_translation receipt."""
    if path.lower()!=MATERIAL:return None
    cache=getattr(archive,'_ground_preview_cache',None)
    if cache is None:cache=archive._ground_preview_cache={}
    if path not in cache:
        from rc2016.levels.scene import asset_bytes
        md,gd=asset_bytes(archive,path),asset_bytes(archive,GRAPH)
        if hashlib.sha256(md).hexdigest()!=MATERIAL_SHA or hashlib.sha256(gd).hexdigest()!=GRAPH_SHA:
            cache[path]=None
        else:
            candidates=[p for p in pixel_programs(gd) if p['code_sha256']==CODE_SHA]
            if len(candidates)!=1:raise ValueError('Ambiguous experimental source writer')
            trace=_trace(candidates[0],gd,md)
            suffix=trace['straight_line_suffix'];join=trace['serialized_pass_join'][0]
            lowered=lower_deferred(dict(nodes=suffix['nodes'],exports={str(k):v for k,v in suffix['exports'].items()},samples=suffix['samples']))
            cache[path]=dict(nodes=lowered['nodes'],outputs=lowered['outputs'],bindings=join['bound_resources'],
                cb2={str(k):v for k,v in join['bound_cb2_dwords'].items()},source_parameters=join['parameter_receipt'],
                source=dict(material_sha256=MATERIAL_SHA,graph_sha256=GRAPH_SHA,code_sha256=CODE_SHA,
                            pass_index=join['pass_index'],record=join['record'],suffix_offset=suffix['start_offset']))
    base=cache[path]
    if base is None:return None
    return bind_preview(base,DEFAULT_CONTROLS)

def bind_preview(base,controls=DEFAULT_CONTROLS):
    """Explicit review input substitution, with all other dependencies rejected."""
    red,green,scale=map(float,controls)
    if not all(math.isfinite(v) for v in (red,green,scale)) or not (0<=red<=1 and 0<=green<=1 and 0.01<=scale<=100):
        raise ValueError('Ground blend channels must be 0–1 and UV scale 0.01–100')
    ir=DAG(base['nodes']);substitutions={}
    cb2={int(k):v for k,v in base['cb2'].items()}
    uv=[ir.node('input',name='UV0.'+str(ch),kind='attribute',attribute=0,channel=ch) for ch in (0,1)]
    uv=[ir.op('mul',v,ir.const(scale)) for v in uv]
    for i,n in enumerate(list(ir.nodes)):
        if n['op']=='texture':substitutions[i]=ir.node('texture',uv,api=n['api'],sample=n['sample'],channel=n['channel'])
        elif n['op']=='input' and n.get('kind')=='cb' and n['api']==2 and n['dword'] in cb2:
            substitutions[i]=ir.const(cb2[n['dword']])
        elif n['op']=='input' and n.get('kind')=='attribute' and n.get('attribute')==4 and n.get('channel') in (0,1):
            substitutions[i]=ir.const((red if n['channel']==0 else green)**2)
    o=base['outputs'];roots=[*o['albedo'],*o['F0'],o['gloss'],o['emission']]
    roots=ir.replace(roots,substitutions)
    compact=DAG();remap={}
    def copy(i):
        if i not in remap:
            n=ir.nodes[i]
            if n['op'] not in PORTABLE_OPS:raise ValueError('Unresolved experimental output: '+n.get('reason',n['op']))
            if n['op']=='input' and not (n.get('kind')=='attribute' and n.get('attribute')==0 and n.get('channel') in (0,1)):
                raise ValueError('Unresolved experimental input: '+n['name'])
            remap[i]=compact.node(n['op'],[copy(j) for j in n['args']],**{k:v for k,v in n.items() if k not in ('op','args')})
        return remap[i]
    roots=[copy(i) for i in roots];active={n['api'] for n in compact.nodes if n['op']=='texture'}
    bindings=[b for b in base['bindings'] if b['api'] in active]
    assert active=={b['api'] for b in bindings}
    result=dict(status='experimental',experimental=True,automatic_ready=False,game_equivalence=False,
        preview_contract=CONTRACT,label=NOTE,material_sha256=MATERIAL_SHA,graph_sha256=GRAPH_SHA,code_sha256=CODE_SHA,
        pass_contract_index=base['source']['pass_index'],nodes=compact.nodes,
        outputs=dict(albedo=roots[:3],F0=roots[3:6],gloss=roots[6],emission=roots[7]),normal=None,
        texture_bindings=bindings,parameters=base['source_parameters'],source=base['source'],
        preview_controls=dict(red=red,green=green,uv_scale=scale),limitations=list(LIMITATIONS),preview_source=base)
    result['translation_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result

def rebind_preview(receipt,controls):
    if receipt.get('preview_contract')!=CONTRACT:raise ValueError('Unknown ground preview contract')
    return bind_preview(receipt['preview_source'],controls)
