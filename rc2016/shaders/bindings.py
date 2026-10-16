"""Bounded native pass contracts, parameter substitution and portable IR.

No shader family/name/hash chooses equations. Ambiguous serialized contracts
and engine values remain diagnostics, preserving the existing preview fallback.
"""
import hashlib
import json
import math
import struct
import threading
from collections import OrderedDict
from functools import lru_cache

from rc2016.shaders.ir import DAG, bits
from rc2016.shaders.translator import compile_graph, lower_deferred, sampler_mode_hits, sections
from rc2016.shaders.families import graph_parameter_tables
from rc2016.common import asset_path

PORTABLE_OPS={'const','texture','input','add','sub','mul','div','neg','abs','min',
              'max','sqrt','rsqrt','rcp','log2','exp2','pow','sat','sin','cos',
              'lt','le','gt','ge','eq','ne','select','floor','fract'}


_POOL=None
_POOL_WORKERS=0
_POOL_LOCK=threading.Lock()


def use_compile_processes(workers=None):
    """Compile graphs in up to ``workers`` worker processes (default: CPU count - 1, at most 6);
    0 compiles in this process. The GIL serialises compilation on loader threads, so the
    viewers opt in; a library import (Blender, the pipeline) never starts processes. The pool
    starts with the first compile; a broken pool falls back to compiling here."""
    global _POOL,_POOL_WORKERS
    import os
    if workers is None:workers=max(0,min(6,(os.cpu_count() or 1)-1))
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown(wait=False,cancel_futures=True);_POOL=None
        _POOL_WORKERS=workers if workers>1 else 0


def _pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None and _POOL_WORKERS:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor
            # Spawn (the Windows default) everywhere: forking a process with Tk and loader threads is unsafe.
            _POOL=ProcessPoolExecutor(_POOL_WORKERS,mp_context=multiprocessing.get_context('spawn'))
        return _POOL


def _pooled(function,data):
    """function(data) in a worker process when the pool is on, else here."""
    pool=_pool()
    if pool is not None:
        try:
            return pool.submit(function,data).result()
        except Exception as exc:
            from concurrent.futures.process import BrokenProcessPool
            import pickle
            if not isinstance(exc,(BrokenProcessPool,OSError,pickle.PicklingError)):
                raise
            use_compile_processes(0)   # compile here from now on
    return function(data)


def _compile_independent_samplers(data):
    return compile_graph(data,allow_independent_samplers=True)


def _compile_strict_counted(data):
    """(graph, whether the sampler mode never mattered): the second part is what lets one compile
    serve both modes. Module-level so a worker process can run it."""
    before=sampler_mode_hits()
    graph=compile_graph(data)
    return graph,sampler_mode_hits()==before


def _compile_independent_counted(data):
    before=sampler_mode_hits()
    graph=_compile_independent_samplers(data)
    return graph,sampler_mode_hits()==before


class _CompiledGraphs:
    """Compiled graphs per (graph bytes, sampler mode), least recently used first out. A graph that
    compiles the same in both modes is stored for both: the world-position previews and the strict
    native translation of one material would otherwise compile it twice."""
    def __init__(self,maxsize=64):
        self.maxsize=maxsize;self.graphs=OrderedDict();self.lock=threading.Lock()

    def _store(self,key,graph):
        self.graphs[key]=graph;self.graphs.move_to_end(key)
        while len(self.graphs)>self.maxsize:self.graphs.popitem(last=False)

    def __call__(self,data,allow_independent_samplers=False):
        mode=bool(allow_independent_samplers);key=(data,mode)
        with self.lock:
            graph=self.graphs.get(key)
            if graph is not None:
                self.graphs.move_to_end(key);return graph
        graph,same=_pooled(_compile_independent_counted if mode else _compile_strict_counted,data)
        with self.lock:
            self._store(key,graph)
            if same and isinstance(graph,dict) and 'programs' in graph:self._store((data,not mode),graph)
        return graph

    def cache_clear(self):
        with self.lock:self.graphs.clear()


_compiled=_CompiledGraphs()


_GRAPH_LOCKS={}
_GRAPH_LOCKS_LOCK=threading.Lock()


def _once(kind,data,function):
    """function(data) once per graph: level loads translate many materials over a few dozen
    graphs on several threads, and concurrent callers for one graph would otherwise each
    compute it. They wait for the first instead."""
    with _GRAPH_LOCKS_LOCK:
        lock=_GRAPH_LOCKS.setdefault((kind,hashlib.sha256(data).digest()),threading.Lock())
    with lock:
        return function(data)


def compiled(data,allow_independent_samplers=False):
    """Compile once per graph and descriptor mode; strict and preview caches are separate."""
    return _once('compiled',bytes(data),lambda d:_compiled(d,bool(allow_independent_samplers)))


def stream_evidence(data):
    """(rotated-stream PS attributes, common squared channels, channels by PS header).
    World-position adapter evidence, empty where analysis fails."""
    from rc2016.shaders.interpolants import rotated_stream_attributes, squared_stream_evidence
    try:normal_attributes=set(rotated_stream_attributes(data))
    except (ValueError,KeyError,IndexError,struct.error):normal_attributes=set()
    try:stream_channels,by_program=squared_stream_evidence(data)
    except (ValueError,KeyError,IndexError,struct.error):stream_channels,by_program={},{}
    return normal_attributes,stream_channels,by_program


@lru_cache(maxsize=64)
def _stream_evidence(data):
    return _pooled(stream_evidence,data)


def rotated_evidence(data):
    """The rotated-stream half of stream_evidence (module-level: it runs in a worker process)."""
    from rc2016.shaders.interpolants import rotated_stream_attributes
    try:return set(rotated_stream_attributes(data))
    except (ValueError,KeyError,IndexError,struct.error):return set()


def squared_evidence(data):
    """The squared-channel half of stream_evidence: (common channels, channels by PS header)."""
    from rc2016.shaders.interpolants import squared_stream_evidence
    try:return squared_stream_evidence(data)
    except (ValueError,KeyError,IndexError,struct.error):return {},{}


@lru_cache(maxsize=64)
def _rotated_evidence(data):
    return _pooled(rotated_evidence,data)


@lru_cache(maxsize=64)
def _squared_evidence(data):
    return _pooled(squared_evidence,data)


def graph_stream_evidence(data,program=None,*,normals=True,channels=True):
    """stream_evidence once per graph, in a worker process when the pool is on: it depends on
    the graph alone, while every material using the graph asks for it. ``normals=False`` or
    ``channels=False`` computes (and returns empty for) only the other half, each cached on its own."""
    if normals and channels:
        normal_attributes,stream_channels,by_program=_once('stream_evidence',bytes(data),_stream_evidence)
        if program is not None:stream_channels=by_program.get(program.get('header_offset'),{})
        return set(normal_attributes),dict(stream_channels)
    normal_attributes=set(_once('rotated_evidence',bytes(data),_rotated_evidence)) if normals else set()
    stream_channels={}
    if channels:
        common,by_program=_once('squared_evidence',bytes(data),_squared_evidence)
        stream_channels=by_program.get(program.get('header_offset'),{}) if program is not None else common
    return normal_attributes,dict(stream_channels)


class _LazyEvidence:
    """Stream evidence that is analysed when _bind first reads it: the rotated normals only matter
    when the program has a reconstructed world position, the squared channels only when it reads
    an interpolant, and most programs a level offers for previews need neither."""
    def __init__(self,load):
        self._load=load;self._value=None

    def _get(self):
        if self._value is None:self._value=self._load()
        return self._value

    def __bool__(self):return bool(self._get())
    def __iter__(self):return iter(self._get())
    def __len__(self):return len(self._get())
    def __contains__(self,item):return item in self._get()
    def get(self,key,default=None):return self._get().get(key,default)


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


def bind_front_face(ir,roots):
    """Bind the FRONT_FACE pixel input where the program only tests it against zero.

    The test becomes 1 - front_facing (0/1), which both frontends supply. This
    relies on the GCN convention that the input is nonzero for front faces (as
    used by Mesa's radeonsi driver); it is not captured from the game. Any other
    use leaves the input unbound.
    """
    reachable=ir.reachable(roots)
    face={i for i in reachable if ir.nodes[i]['op']=='input' and ir.nodes[i].get('kind')=='ps_input'
          and ir.nodes[i].get('input')=='FRONT_FACE'}
    if not face:return roots,False
    facing=ir.node('input',name='front_facing',kind='front_facing');substitutions={}
    for i in reachable:
        n=ir.nodes[i]
        if n['op']!='u_eq' or not face&set(n['args']):continue
        other=[a for a in n['args'] if a not in face]
        if len(other)==1 and ir.value(other[0]) is not None and bits(ir.value(other[0]))==0:
            substitutions[i]=ir.op('sub',ir.const(1.),facing)
    bound=ir.replace(roots,substitutions)
    if face&ir.reachable(bound):return roots,False
    return bound,True


def _lowered(program):
    """Material-independent lowering, cached on the compiled program (callers get a fresh limitations list)."""
    cached=program.get('_lowered')
    if cached is None:cached=program['_lowered']=lower_deferred(program['ir'])
    return dict(cached,limitations=list(cached['limitations']))


def _bind(program,contract,material_data,instance,world_position=False,normal_attributes=(),stream_channels=None,static_attributes=(),source_preview_policy=None):
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
    if source_preview_policy:
        preview_input=source_preview_policy['input']
        substitutions.update({i:ir.const(preview_input['value']) for i,n in enumerate(list(ir.nodes))
            if n['op']=='input' and n.get('kind')==preview_input['kind'] and
               n.get('api')==preview_input['api'] and n.get('dword')==preview_input['dword']})
        if not any(n['op']=='input' and n.get('kind')==preview_input['kind'] and
                   n.get('api')==preview_input['api'] and n.get('dword')==preview_input['dword']
                   for n in ir.nodes):
            raise ValueError('Exact source preview input is absent from the matched writer')
        selector=source_preview_policy['selector']
        selector_row=next((row for row in receipt if row.get('key_hash')==selector['key_hash']),None)
        if not selector_row or selector_row.get('values')!=[selector['value']] or selector_row.get('source')!='graph_default':
            raise ValueError('Constructor Button source selector differs from its graph default')
        lowered['limitations'].append(
            'Constructor Bot chest-button source preview: CB0[99] is frozen at 0 as a time/phase presentation input; '
            'the engine upload and runtime value are unverified. The serialized source selector 0x2A40709D is 1.0; '
            'the graph compares it with 0.5 and selects the source false branch.')
        lowered['limitations'].append(
            'Constructor Bot chest-button MRT0 alpha comes from TBUFFER_LOAD_FORMAT_X and is omitted; its semantics '
            'are unknown and it is not interpreted as opacity. MRT0 RGB contains a front-face-polarized encoded '
            'attribute normal, but this preview uses geometry normals because tangent-frame reconstruction is unproven.')
    replaced=ir.replace(roots,substitutions);adapted=0;neutral_masks=[];static_used=set()
    if world_position:
        # Explicit review input for the decoded rock Blend Mask slot. An empty
        # instance row is not permission to inherit a graph texture. Only its
        # scalar R samples may use the neutral review value; colours, normals,
        # other slots and ordinary native binding remain unsupported.
        masks={}
        for i in ir.reachable(replaced):
            n=ir.nodes[i];row=bound.get(n.get('api'),{})
            if (n['op']=='texture' and n.get('channel')==0 and not row.get('path')
                    and row.get('source')=='material_instance'
                    and (row.get('slot_key'),row.get('type_hash'))==BLEND_MASK_SLOT):
                masks[i]=ir.const(1.)
                neutral_masks.append(dict(api=n['api'],sample=n['sample'],channel=0,
                    slot_key=row['slot_key'],type_hash=row['type_hash'],review_value=1.))
        if masks:
            replaced=ir.replace(replaced,masks)
            lowered['limitations'].append('Experimental neutral blend mask: explicitly unset material Blend Mask R samples use uniform review value 1. The game empty-slot behavior is unverified; this substitutes a review input, never a decoded texture or runtime default. Original colour layers and blend arithmetic are retained.')
        from rc2016.shaders.engine_inputs import reconstruct_world_position
        replaced,adapted=reconstruct_world_position(ir,replaced)
        normals={}
        for i in ir.reachable(replaced):
            n=ir.nodes[i]
            if n['op']=='input' and n.get('kind')=='attribute' and adapted and n.get('attribute') in normal_attributes and n.get('channel') in (0,1,2):
                normals[i]=ir.node('input',name=f"preview_normal.{n['channel']}",kind='preview_normal',channel=n['channel'])
        normal_adapted=bool(normals)
        for i in ir.reachable(replaced):
            n=ir.nodes[i]
            if n['op']=='input' and n.get('kind')=='attribute' and n.get('attribute') in static_attributes and n.get('channel') in (0,1,2):
                normals[i]=ir.node('input',name=f"preview_static_vector.{n['channel']}",kind='preview_static_vector',channel=n['channel'])
                static_used.add((n['attribute'],n['channel']))
        colours={};painted=set();painted_evidence=[]
        for i in ir.reachable(replaced):
            n=ir.nodes[i]
            if n['op']!='input' or n.get('kind')!='attribute':continue
            proven=(stream_channels or {}).get((n.get('attribute'),n.get('channel')))
            if proven is None or proven[0]!=COLOUR_SEMANTIC or proven[1] not in (0,1,3):continue
            component=proven[1];painted.add('rgba'[component])
            painted_evidence.append(dict(attribute=n['attribute'],channel=n['channel'],semantic=proven[0],component=component))
            if component==3:
                colours[i]=ir.node('input',name='preview_vertex_alpha_squared',kind='preview_vertex_alpha_squared')
            else:
                masked=ir.node('input',name=f'preview_ground_blend.{component}',kind='preview_ground_blend',channel=component)
                present=ir.node('input',name='preview_ground_blend.2',kind='preview_ground_blend',channel=2)
                colours[i]=ir.op('add',masked,ir.op('sub',ir.const(1.),present))
        if not adapted and not colours and not static_used:
            raise ValueError('No camera + ray * POS_W world-position expression or proven squared colour channel')
        if colours:
            lowered['limitations'].append(f'Experimental: interpolants of this pixel program trace to vertex semantic {COLOUR_SEMANTIC} squared components '+','.join(sorted(painted))+'. The runtime flag path and colour-stream identity are inferred; level instances use candidate keyed channels where available. Generic red/green fallback is white 1, not Novalis review controls; standalone red/green stream transport remains unresolved.')
            normals.update(colours)
        if normals:
            replaced=ir.replace(replaced,normals)
            if normal_adapted:lowered['limitations'].append('Experimental: interpolants '+', '.join(f'attribute{a}.xyz' for a in sorted(normal_attributes))+' are, in every traced vertex program and flag path, per-draw words times one vertex stream (a transformed stream, optionally normalised) and are read as the geometry normal; that the stream is the normal and the matrix object-to-world is inferred, and the packed vertex frame is not reconstructed.')
        if static_used:lowered['limitations'].append('Experimental ordinary-static vector input: source vertex producers in the exact pass prove M*(2*semantic1.xyz-1) under material-derived static flags. The raw packed model word at vertex+8 supplies recentered UNORM xyz without normalization, and the source CPU axis-length classifier selects the per-instance matrix. Fetch identity and absence of alternate/deformed streams are inferred; runtime pass choice is unverified. Skinned and alternate-codec geometry are excluded. Shader colour layers and blend arithmetic are retained.')
        if adapted:lowered['limitations'].append(f'Experimental: {adapted} expressions of the form CB0[28..30] + (CB0[92..95] + CB0[100..103]*POS_Y + POS_X*(CB0[96..99] + CB0[104..107]*POS_Y)) * POS_W are read as world position and replaced by viewer world position. This engine meaning is inferred from the expression shape, not decoded from the game.')
    replaced,zero_guards=guard_source_zero_powers(ir,replaced)
    replaced,front_face=bind_front_face(ir,replaced)
    if front_face:
        lowered['limitations'].append('FRONT_FACE is bound as front-facing (gl_FrontFacing / 1 - Backfacing) through its zero test, assuming the GCN convention nonzero = front face; not captured from the game.')
    if zero_guards:
        lowered['limitations'].append('Source LOG/MUL/EXP with a proven nonnegative base and positive bound exponent preserves zero; positive-input arithmetic is unchanged. Other singular math retains portable frontend semantics.')
    outputs=dict(albedo=replaced[:3],F0=replaced[3:6],gloss=replaced[6],emission=replaced[7])
    if normal:normal=dict(normal,xyz=replaced[8:11])
    used=ir.reachable(replaced);errors=[]
    for i in sorted(used):
        n=ir.nodes[i]
        if n['op'] not in PORTABLE_OPS:errors.append(dict(node=i,reason=n.get('reason',n['op'])))
        elif n['op']=='input' and not (n.get('kind')=='attribute' and n.get('attribute')==0 and n.get('channel') in (0,1)) and n.get('kind')!='front_facing' \
                and not (world_position and n.get('kind') in (('preview_position','preview_normal') if adapted else ()) + ('preview_vertex_alpha_squared','preview_ground_blend','preview_static_vector')):
            errors.append(dict(node=i,reason='Unbound engine/interpolant input '+n['name']))
        elif n['op']=='texture' and not bound.get(n['api'],{}).get('path'):
            errors.append(dict(node=i,reason='Sampled material texture is unset or unbound',api=n['api']))
    if errors:raise ValueError(json.dumps(errors[:12],sort_keys=True))
    if static_attributes and not static_used:raise ValueError('No live source-proven static vector channel')
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
    independent={ (n['api'],n['sampler_api'],n['sample']) for n in compact.nodes
        if n['op']=='texture' and n.get('resource_table') is True and n.get('sampler_table') is True
        and n.get('sampler_api')!=n['api'] }
    if independent:
        if not world_position:raise ValueError('Independent texture/sampler APIs require experimental binding')
        lowered['limitations'].append('Experimental independent descriptor indices: exact source resource APIs select material textures; separately proved sampler APIs are retained as evidence. Native sampler state is unverified; portable filtering and addressing remain review substitutes.')
    return dict(nodes=compact.nodes,outputs=outputs,normal=normal,
        texture_bindings=[bound[a] for a in sorted(active)],parameters=receipt,
        pass_contract_index=contract['index'],limitations=lowered['limitations'],source_zero_power_guards=zero_guards,
        **({'source_shader_record':dict(contract['source_shader_record'])} if 'source_shader_record' in contract else {}),
        **({'painted_channel_evidence':sorted(painted_evidence,key=lambda row:(row['attribute'],row['channel']))}
           if world_position and colours else {}),
        **({'world_position_terms':adapted} if adapted else {}),
        **({'static_vector_inputs':[dict(attribute=a,channel=c) for a,c in sorted(static_used)]} if static_used else {}),
        **({'neutral_blend_mask_inputs':neutral_masks} if neutral_masks else {}),
        **({'independent_sampler_inputs':[dict(api=a,sampler_api=s,sample=i)
            for a,s,i in sorted(independent)]} if independent else {}))


def program_contracts(data,program,contracts,allow_independent_samplers=False):
    """Join native envelope to its source pass, never to a sorted texture list.

    Corpus controls establish 13 serialized shader records per BC pass. The
    byte pointer and resource bitmap are checked again on every new graph.
    Static tables require complete, path-proven resource/sampler words with
    source table pointers. Native binding requires equal APIs. Experimental
    binding may retain a separately proved sampler index; only resource APIs
    select textures and satisfy the B6 span/bitmap. Dynamic and mixed layouts
    remain unsupported.
    """
    spans=sections(data);base,size=spans.get(0xB6D49474,(0,0))
    if size!=len(contracts)*13*20:raise ValueError('Unsupported serialized shader/pass partition')
    shader_base,shader_size=spans[0x873D1E6C];matches=[]
    used={n['api'] for n in program['ir']['nodes'] if n['op']=='texture'}
    apis={s['api'] for s in program['usage_slots'] if s['kind']==0 and s['api']>=5}
    textures=[n for n in program['ir']['nodes'] if n['op']=='texture']
    pointers=[s for s in program['usage_slots'] if s['kind'] in (0x13,0x15)]
    static_table=(not apis and len(pointers)==2 and {s['kind'] for s in pointers}=={0x13,0x15}
                  and all(s['api']==0 and s.get('flags')==16 for s in pointers)
                  and bool(textures) and all(n.get('resource_table') is True and (n.get('sampler_api')==n['api']
                      or (allow_independent_samplers and n.get('sampler_table') is True
                          and type(n.get('sampler_api')) is int and 0<=n['sampler_api']<32)) for n in textures))
    if not static_table and any(n.get('resource_table') for n in textures):return []
    cb=[n['dword'] for n in program['ir']['nodes'] if n['op']=='input' and n.get('kind')=='cb' and n.get('api')==2]
    joined=[]
    for i in range(size//20):
        cb_mask,resource_mask,record_word2,relative,blob_size=struct.unpack_from('<5I',data,base+i*20)
        if not blob_size or shader_base+relative+36!=program['header_offset']:continue
        if relative+blob_size>shader_size:raise ValueError('Serialized shader blob exceeds shader section')
        joined.append(i)
        if blob_size<=36:continue
        if 'code_offset' in program and program['code_offset']+program['code_size']+28>shader_base+relative+blob_size:
            raise ValueError('Serialized shader blob does not contain the source program')
        contract=contracts[i//13]
        if not static_table and (not apis or max(apis)-4!=len(contract['resources'])):continue
        if any(a is None or not 5<=a<min(32,5+len(contract['resources'])) or not resource_mask&(1<<a) for a in used):continue
        if cb and (not cb_mask&4 or contract['cb_bytes']!=(max(cb)+1+3)//4*16):continue
        matches.append(dict(contract,source_shader_record=dict(index=i,file_offset=base+i*20,
            pass_index=i//13,index_in_pass=i%13,cb_mask=cb_mask,resource_mask=resource_mask,
            word2_uninterpreted=record_word2,shader_relative_offset=relative,
            shader_blob_size=blob_size,shader_header_offset=program['header_offset'])))
    if len(joined)>1:raise ValueError('Ambiguous serialized shader/pass pointer')
    return matches


def surface_components(bound):
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
            elif op=='input':record=[op,n['kind'],n.get('attribute'),n.get('channel')]
            elif op=='texture':
                b=bindings[n['api']]
                record=[op,b['path'],b['type_hash'],n['api']==normal_api,n['channel'],args]
            else:record=[op,args]
            memo[i]=hashlib.sha256(json.dumps(record,separators=(',',':')).encode()).hexdigest()
        return memo[i]
    result={key:[node(i) for i in value] if isinstance(value,list) else node(value)
        for key,value in bound['outputs'].items()}
    result['normal']=[node(i) for i in normal['xyz']] if normal else None
    return result


def surface_signature(bound):
    """Combined structural identity, unchanged by diagnostic receipt metadata."""
    return hashlib.sha256(json.dumps(surface_components(bound),sort_keys=True).encode()).hexdigest()


def surface_component_signatures(bound):
    """Per-output structural identities for diagnostics, not numeric equivalence."""
    return {key:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
            for key,value in surface_components(bound).items()}


WORLD_POSITION_CONTRACT='world-position-reconstruction-v1'
PAINT_STREAM_CONTRACT='squared-colour-stream-v1'
# Vertex input semantic read as the model RGBA8 colour stream by the experimental adapter
# (traced exports carry semantic 3 as UV0); an inference, not a decoded binding.
COLOUR_SEMANTIC=5
# Serialized slot identity of the decoded scalar rock Blend Mask.
BLEND_MASK_SLOT=(0x3404C763,0x49A7866B)

# One bounded source preview for the Constructor Bot chest button. The graph's
# deferred writer is exact and portable after substituting the unbound phase
# input. CB0[99] is not established as a runtime upload; its zero is a named
# preview value. Every identity below is required, so this policy cannot leak
# to another graph/material or a source-identical shader in another graph.
CONSTRUCTO_BUTTON_SOURCE_PREVIEW=dict(
    graph_path='materialgraph/character/enemy/enm_constructo_bot_chest_button/enm_constructo_bot_chest_button.materialgraph',
    graph_sha256='353744cf5d88b461cefeb3e5c6e9fd6f77e6d3361db72ceaa76857d4137e10df',
    material_path='material/character/enemy/enm_constructo_bot_chest_button/enm_constructo_bot_chest_button.material',
    material_sha256='db3cc6657e9a29766542fb582297d4209c6964f52137603417e30ae805d62cea',
    shader_id='0x7322f86e',
    code_sha256='c2d7c69aa85d6820347b4f9e65020d94751452bd7c200b236292c5ea20264edb',
    input=dict(kind='cb',api=0,dword=99,value=0.0,name='CB0[99]'),
    selector=dict(key_hash=0x2A40709D,dword=0,value=1.0))


def _source_preview_policy(graph_paths, graph_sha256, material_path, material_sha256, program):
    """Return a source-preview input only for the exact audited material/writer."""
    rule=CONSTRUCTO_BUTTON_SOURCE_PREVIEW
    if not isinstance(graph_paths,(list,tuple)) or len(graph_paths)!=1:return None
    try:
        normalized_graph=asset_path(str(graph_paths[0]))
        normalized_material=asset_path(str(material_path or ''))
    except ValueError:
        return None
    if (normalized_graph!=rule['graph_path'] or graph_sha256!=rule['graph_sha256'] or
        normalized_material!=rule['material_path'] or material_sha256!=rule['material_sha256'] or
        program.get('shader_id')!=rule['shader_id'] or program.get('code_sha256')!=rule['code_sha256']):
        return None
    return dict(rule)


def validate_source_preview_inputs(translation):
    """Reject edited or stale source-preview input receipts before Blender acceptance."""
    rows=translation.get('source_preview_inputs',[])
    if not rows:return True
    rule=CONSTRUCTO_BUTTON_SOURCE_PREVIEW
    expected=dict(name=rule['input']['name'],kind=rule['input']['kind'],api=rule['input']['api'],
        dword=rule['input']['dword'],value=rule['input']['value'],graph_path=rule['graph_path'],
        graph_sha256=rule['graph_sha256'],material_path=rule['material_path'],
        material_sha256=rule['material_sha256'],shader_id=rule['shader_id'],
        code_sha256=rule['code_sha256'],
        meaning='time/phase-like source input; runtime upload/value unverified',
        selector_key_hash='0x%08X'%rule['selector']['key_hash'],selector_value=rule['selector']['value'])
    if rows != [expected] or translation.get('shader_id')!=rule['shader_id'] or \
            translation.get('code_sha256')!=rule['code_sha256'] or \
            translation.get('graph_sha256')!=rule['graph_sha256']:
        raise ValueError('Source preview input receipt does not match the exact Constructor Bot chest-button source')
    return True


def prepare_translation(graph_datas,material_data,instance,world_position=False,*,static_vector=False,
                        source_graph_paths=None,source_material_path=None):
    """Bound portable translation. world_position=True builds the experimental
    receipt that adapts inferred world position or source-proven squared colour
    channels. Paint-only writers require no position or normal substitution;
    other engine inputs still fail closed. The receipt is never ready."""
    result=dict(schema='rcps4-bound-shader-translation-v1',status='unsupported',diagnostics=[],game_equivalence=False)
    if len(graph_datas)!=1:
        result['diagnostics'].append('Translation requires one unambiguous source graph');return result
    if static_vector and not world_position:raise ValueError('Static vector inputs require experimental binding')
    data=bytes(graph_datas[0]);graph=compiled(data,allow_independent_samplers=world_position)
    material_sha256=hashlib.sha256(bytes(material_data)).hexdigest()
    # The stream analysis (seconds on a large graph) is asked for per matched program below: most
    # graphs a level offers for previews have no program that compiles, and never need it.
    normal_attributes=set();stream_channels={}
    try:contracts=pass_contracts(data)
    except ValueError as exc:result['diagnostics'].append(str(exc));return result
    candidates=[]
    for program in graph['programs']:
        if program['status']!='compiled':
            result['diagnostics'].append(dict(shader_id=program['shader_id'],reason='compile: '+program.get('reason','')));continue
        try:matched=program_contracts(data,program,contracts,allow_independent_samplers=world_position)
        except ValueError as exc:
            result['diagnostics'].append(str(exc));continue
        if world_position:
            normal_attributes=_LazyEvidence(lambda program=program:graph_stream_evidence(data,program,channels=False)[0])
            stream_channels=_LazyEvidence(lambda program=program:graph_stream_evidence(data,program,normals=False)[1])
        static_attributes={};static_proof=[]
        if static_vector:
            from rc2016.shaders.static_vectors import static_vector_evidence
            from rc2016.pipeline.render_state import material_flags
            static_attributes,static_proof=static_vector_evidence(data,program,material_flags(data=material_data))
            if not static_attributes:continue
        for contract in matched:
            if world_position:
                split=[dict(api=s['api'],sampler_api=s['sampler_api'],code_offset=s['offset'])
                    for s in program.get('ir',{}).get('samples',[]) if s.get('resource_table') is True
                    and s.get('sampler_table') is True and s.get('api')!=s.get('sampler_api')]
                if split:
                    result.setdefault('independent_sampler_candidates',[]).append(dict(
                        shader_id=program['shader_id'],code_sha256=program['code_sha256'],
                        source_shader_record=contract['source_shader_record'],samples=split))
            preview_policy=None if world_position else _source_preview_policy(
                source_graph_paths,graph['graph_sha256'],source_material_path,material_sha256,program)
            try:bound=_bind(program,contract,material_data,instance,world_position,normal_attributes,
                stream_channels,static_attributes,preview_policy)
            except ValueError as exc:
                result['diagnostics'].append(dict(shader_id=program['shader_id'],contract=contract['index'],reason=str(exc)));continue
            if static_proof:bound['static_vector_evidence']=static_proof
            signature=json.dumps({k:bound[k] for k in ('nodes','outputs','normal','texture_bindings')},sort_keys=True)
            candidates.append(dict(bound=bound,program=program,signature=signature,
                surface_signature=surface_signature(bound),source_preview_policy=preview_policy))
    if not candidates:
        result['diagnostics'].append('No portable deferred writer with a bounded source pass contract');return result
    signatures={c['surface_signature'] for c in candidates}
    if len(signatures)!=1:
        result['candidate_surfaces']=[dict(shader_id=c['program']['shader_id'],code_sha256=c['program']['code_sha256'],
            pass_contract_index=c['bound']['pass_contract_index'],surface_sha256=c['surface_signature'],
            texture_bindings=c['bound']['texture_bindings'],component_sha256=surface_component_signatures(c['bound']),
            **({'source_shader_record':c['bound']['source_shader_record']} if 'source_shader_record' in c['bound'] else {})) for c in candidates]
        result['differing_components']=[key for key in ('albedo','F0','gloss','emission','normal')
            if len({c['component_sha256'][key] for c in result['candidate_surfaces']})>1]
        result['diagnostics'].append('Multiple portable native variants produce different material programs');return result
    candidate=candidates[0];program=candidate['program']
    result.update(candidate['bound'],status='ready',graph_sha256=graph['graph_sha256'],
        code_sha256=program['code_sha256'],code_offset=program['code_offset'],shader_id=program['shader_id'],
        equivalent_candidates=len(candidates),translator_version=graph['translator_version'],binding_version=9,
        surface_sha256=candidate['surface_signature'],candidate_sources=[dict(
            shader_id=c['program']['shader_id'],code_sha256=c['program']['code_sha256'],
            pass_contract_index=c['bound']['pass_contract_index'],
            **({'source_shader_record':c['bound']['source_shader_record']} if 'source_shader_record' in c['bound'] else {})) for c in candidates])
    if candidate['source_preview_policy']:
        rule=candidate['source_preview_policy']
        result['source_preview_inputs']=[dict(name=rule['input']['name'],kind=rule['input']['kind'],
            api=rule['input']['api'],dword=rule['input']['dword'],value=rule['input']['value'],
            graph_path=rule['graph_path'],graph_sha256=rule['graph_sha256'],
            material_path=rule['material_path'],material_sha256=rule['material_sha256'],
            shader_id=rule['shader_id'],code_sha256=rule['code_sha256'],
            meaning='time/phase-like source input; runtime upload/value unverified',
            selector_key_hash='0x%08X'%rule['selector']['key_hash'],
            selector_value=rule['selector']['value'])]
    result['limitations']=list(dict.fromkeys(
        limitation for c in candidates for limitation in c['bound']['limitations']))
    result['limitations']+=graph['limitations']+[
        'B6 native-envelope pointer selects its 13-record pass partition; BC counts partition resources/parameters and padded default blocks. Material APIs start at 5, bitmap/span and padded CB2 checks bound support.',
        'Portable candidates must have identical source-bound surface expressions, including UV arithmetic, channels and normal interpretation. API/sample labels are ignored only for this comparison; omitted runtime terms and runtime variant selection are not established.',
        'UV attribute 0.xy is source UV0 in game space (V unflipped for arithmetic, flipped back per sample). Runtime draw selection is not established.',
        'Texture sampling uses Blender linear filtering and repeat addressing; native sampler-state translation is not implemented.',
        'Shared deferred BRDF is the existing Blender approximation; this is not full game shader equivalence.']
    if world_position:
        position_preview=bool(candidate['bound'].get('world_position_terms'))
        result.update(status='experimental',preview_contract=('static-packed-vector-preview-v1' if static_vector else WORLD_POSITION_CONTRACT if position_preview else PAINT_STREAM_CONTRACT),automatic_ready=False,
            material_sha256=hashlib.sha256(bytes(material_data)).hexdigest())
        result['limitations'].insert(0,
            'Experimental world-position preview: an inferred engine meaning of CB0 and pixel position replaces runtime data; not a ready translation.' if position_preview else
            'Experimental squared-colour preview: source-proven vertex squares use candidate colour streams; their runtime binding and flag selection remain inferred. No world-position or normal substitution; not a ready translation.')
    result['translation_sha256']=hashlib.sha256(candidate['signature'].encode()).hexdigest()
    return result


def bound_attribute_uses(graph_data,material_data,instance,limit=4):
    """Diagnostic: with material constants bound and world position adapted, the
    outputs each PS attribute reaches and the smallest clamped (sat/min/max) and other
    expressions using it, per portable program and pass. Interpolants stay unbound."""
    from rc2016.shaders.engine_inputs import reconstruct_world_position
    data=bytes(graph_data);graph=compiled(data);lines=[];seen=set()
    contracts=pass_contracts(data)
    for program in graph['programs']:
        if program['status']!='compiled':continue
        try:matched=program_contracts(data,program,contracts)
        except ValueError:continue
        for contract in matched:
            values,_=_parameter_values(contract,instance)
            lowered=_lowered(program);ir=DAG(lowered['nodes']);outputs=lowered['outputs']
            named=[('albedo',r) for r in outputs['albedo']]+[('F0',r) for r in outputs['F0']]+[('gloss',outputs['gloss']),('emission',outputs['emission'])]
            if lowered['normal']:named+=[('normal',r) for r in lowered['normal']['xyz']]
            substitutions={i:ir.const(values[n['dword']]) for i,n in enumerate(list(ir.nodes))
                if n['op']=='input' and n.get('kind')=='cb' and n['api']==2 and n['dword'] in values}
            roots=ir.replace([r for _,r in named],substitutions)
            roots,_=reconstruct_world_position(ir,roots)
            live=ir.reachable(roots);order=sorted(live)
            # One pass in index order (arguments precede users): attributes and size below each node.
            below={};size={}
            for i in order:
                n=ir.nodes[i];args=n.get('args',[])
                below[i]=frozenset({n['attribute']}) if n['op']=='input' and n.get('kind')=='attribute' else \
                    frozenset().union(*(below[j] for j in args)) if args else frozenset()
                size[i]=1+sum(size[j] for j in args) if args else 1
            block=[f"pixel {program['shader_id']} pass {contract['index']}:"]
            for a in sorted(set().union(*(below[r] for r in roots))):
                outs=sorted({named[k][0] for k,r in enumerate(roots) if a in below[r]})
                clamps=sorted((i for i in order if a in below[i] and ir.nodes[i]['op'] in ('sat','min','max') and size[i]<=200),
                              key=lambda i:size[i],reverse=True)
                block.append(f"  attribute{a}: reaches {', '.join(outs)}")
                block+=[f'    clamp: {ir.describe(i,10)[:500]}' for i in clamps[:limit]]
            key='\n'.join(block[1:])
            if key in seen:lines.append(block[0]+' same as an earlier pass');continue
            seen.add(key);lines+=block
    return lines
