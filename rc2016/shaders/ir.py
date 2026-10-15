"""Typed scalar shader DAG, shared by the native compiler and Blender emitter.

Node IDs are references; constants always have their own nodes. Unknown values
remain explicit and only reachable unknowns prevent emitting a material.
"""
import math
import struct


OPS = {'add', 'sub', 'mul', 'div', 'neg', 'abs', 'min', 'max', 'sqrt',
       'rsqrt', 'rcp', 'log2', 'exp2', 'pow', 'sat', 'sin', 'cos',
       'lt', 'le', 'gt', 'ge', 'eq', 'ne', 'select', 'pack', 'bit_and', 'bit_or',
       'floor', 'fract'} | {'u_and', 'u_or', 'u_xor', 'u_andn2', 'u_shl', 'u_shr',
       'u_eq', 'u_lt', 'u_le', 'i_lt', 'i_le', 'u_bit', 'u_add', 'u_sub', 'u_mul', 'i_shr',
       'u_bfe', 'u_sad', 'i2f', 'u2f', 'f2i', 'f2u'}
# Uniform 32-bit integer operations. Operands and bitwise results are F32
# values whose bit pattern is the integer (as scalar constant-buffer words are
# read); comparisons return 0/1. They fold once material constants are bound
# and are not portable: neither frontend emits them.


def bits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def from_bits(b):
    return struct.unpack('<f', struct.pack('<I', b & 0xffffffff))[0]


def signed(b):
    return b - (1 << 32) if b & 0x80000000 else b


def _f2i(x, low, high):
    # V_CVT_[IU]32_F32: truncate toward zero, saturate, NaN -> 0.
    if math.isnan(x): return from_bits(0)
    return from_bits(int(max(low, min(high, math.trunc(x)))) if math.isfinite(x) else (high if x > 0 else low))


# One table for every operation, built once: compute() runs for every constant-folded operation.
_COMPUTE = {
    'add': lambda a: a[0]+a[1], 'sub': lambda a: a[0]-a[1],
    'mul': lambda a: a[0]*a[1], 'div': lambda a: a[0]/a[1],
    'neg': lambda a: -a[0], 'abs': lambda a: abs(a[0]),
    'min': lambda a: min(a), 'max': lambda a: max(a),
    'sqrt': lambda a: math.sqrt(a[0]), 'rsqrt': lambda a: 1/math.sqrt(a[0]),
    'rcp': lambda a: 1/a[0], 'log2': lambda a: math.log2(a[0]),
    'exp2': lambda a: 2**a[0], 'pow': lambda a: a[0]**a[1],
    'sat': lambda a: max(0.,min(1.,a[0])), 'sin':lambda a: math.sin(a[0]),
    'cos':lambda a: math.cos(a[0]), 'lt':lambda a: float(a[0]<a[1]),
    'le':lambda a: float(a[0]<=a[1]), 'gt':lambda a: float(a[0]>a[1]),
    'ge':lambda a: float(a[0]>=a[1]), 'eq':lambda a: float(a[0]==a[1]),
    'ne':lambda a: float(a[0]!=a[1]), 'select':lambda a: a[1] if a[0] else a[2],
    'pack':lambda a: list(a), 'bit_and':lambda a: int(a[0])&int(a[1]),
    'bit_or':lambda a: int(a[0])|int(a[1]),
    'floor':lambda a: float(math.floor(a[0])), 'fract':lambda a: a[0]-math.floor(a[0]),
    'u_and':lambda a: from_bits(bits(a[0])&bits(a[1])), 'u_or':lambda a: from_bits(bits(a[0])|bits(a[1])),
    'u_xor':lambda a: from_bits(bits(a[0])^bits(a[1])), 'u_andn2':lambda a: from_bits(bits(a[0])&~bits(a[1])),
    'u_shl':lambda a: from_bits(bits(a[0])<<(bits(a[1])&31)), 'u_shr':lambda a: from_bits(bits(a[0])>>(bits(a[1])&31)),
    'u_eq':lambda a: float(bits(a[0])==bits(a[1])), 'u_lt':lambda a: float(bits(a[0])<bits(a[1])),
    'u_le':lambda a: float(bits(a[0])<=bits(a[1])), 'i_lt':lambda a: float(signed(bits(a[0]))<signed(bits(a[1]))),
    'i_le':lambda a: float(signed(bits(a[0]))<=signed(bits(a[1]))), 'u_bit':lambda a: float((bits(a[0])>>(bits(a[1])&31))&1),
    'u_add':lambda a: from_bits(bits(a[0])+bits(a[1])), 'u_sub':lambda a: from_bits(bits(a[0])-bits(a[1])),
    'u_mul':lambda a: from_bits(bits(a[0])*bits(a[1])), 'i_shr':lambda a: from_bits(signed(bits(a[0]))>>(bits(a[1])&31)),
    'u_sad':lambda a: from_bits(abs(bits(a[0])-bits(a[1]))+bits(a[2])),
    'u_bfe':lambda a: from_bits((bits(a[0])>>(bits(a[1])&31))&((1<<(bits(a[2])&31))-1)),
    'i2f':lambda a: float(signed(bits(a[0]))), 'u2f':lambda a: float(bits(a[0])),
    'f2i':lambda a: _f2i(a[0], -2**31, 2**31-1), 'f2u':lambda a: _f2i(a[0], 0, 2**32-1),
}


def compute(op, a):
    return _COMPUTE[op](a)


class DAG:
    def __init__(self, nodes=None):
        self.nodes=list(nodes or [])
        self._cache={}

    def node(self,op,args=(),**meta):
        # The key's metadata part is repr(sorted(meta.items())); the common no-metadata and
        # one-field (constant) cases build the same string without sorting.
        if not meta:key=(op,tuple(args),'[]')
        elif len(meta)==1:key=(op,tuple(args),'['+repr(next(iter(meta.items())))+']')
        else:key=(op,tuple(args),repr(sorted(meta.items())))
        if key not in self._cache:
            self._cache[key]=len(self.nodes)
            self.nodes.append(dict(op=op,args=list(args),**meta))
        return self._cache[key]

    def const(self,value):
        if not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('Non-finite shader literal')
        return self.node('const',value=value)

    def value(self,index):
        n=self.nodes[index]
        return n['value'] if n['op']=='const' else None

    def op(self,op,*args):
        if op not in OPS:raise ValueError('Unknown shader IR operation: '+op)
        nodes=self.nodes;values=[]
        for x in args:
            n=nodes[x];values.append(n['value'] if n['op']=='const' else None)
        if None not in values:
            try:
                v=compute(op,values)
                if isinstance(v,(int,float)) and math.isfinite(v):return self.const(v)
            except (ValueError,ZeroDivisionError,OverflowError):pass
        if op=='u_sad':
            if args[0]==args[1]:return args[2]
            for first,second in ((0,1),(1,0)):
                if values[first] is not None and bits(values[first])==0:
                    return self.op('u_add',args[second],args[2])
        if op=='mul':
            if 0 in values:return self.const(0.)
            if values[0]==1:return args[1]
            if values[1]==1:return args[0]
        if op=='add':
            if values[0]==0:return args[1]
            if values[1]==0:return args[0]
        if op=='sub':
            if args[0]==args[1]:return self.const(0.)
            if values[1]==0:return args[0]
        if op=='select':
            if args[1]==args[2]:return args[1]
            if values[0] is not None:return args[1] if values[0] else args[2]
        if op=='neg' and self.nodes[args[0]]['op']=='neg':return self.nodes[args[0]]['args'][0]
        return self.node(op,args)

    def reachable(self,roots):
        visited=set(); pending=list(roots)
        while pending:
            i=pending.pop()
            if i in visited:continue
            if not isinstance(i,int) or not 0<=i<len(self.nodes):raise ValueError('Invalid IR reference')
            visited.add(i);pending.extend(self.nodes[i].get('args',[]))
        return visited

    def evaluate(self,root,inputs=None,textures=None):
        inputs=inputs or {};textures=textures or {};values={}
        def visit(i):
            if i in values:return values[i]
            n=self.nodes[i]; op=n['op']
            if op=='const':v=n['value']
            elif op=='input':v=inputs[n['name']]
            elif op=='texture':v=textures[(n['sample'],n['channel'])]
            elif op=='unsupported':raise ValueError(n['reason'])
            else:v=compute(op,[visit(j) for j in n['args']])
            values[i]=v;return v
        return visit(root)

    def replace(self,roots,substitutions):
        memo=dict(substitutions)
        def visit(i):
            if i in memo:return memo[i]
            n=self.nodes[i]
            args=[visit(j) for j in n.get('args',[])]
            if n['op'] in OPS:r=self.op(n['op'],*args)
            else:r=self.node(n['op'],args,**{k:v for k,v in n.items() if k not in ('op','args')})
            memo[i]=r;return r
        return [visit(i) for i in roots]

    def describe(self,root,depth=5):
        n=self.nodes[root]
        if n['op']=='const':return str(n['value'])
        if n['op']=='input':return n['name']
        if n['op']=='texture':return f"texture(API{n['api']}, sample{n['sample']}).{'RGBA'[n['channel']]}"
        if depth==0:return f'node{root}'
        return n['op']+'('+', '.join(self.describe(i,depth-1) for i in n['args'])+')'
