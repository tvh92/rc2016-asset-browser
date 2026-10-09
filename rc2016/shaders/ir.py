"""Typed scalar shader DAG, shared by the native compiler and Blender emitter.

Node IDs are references; constants always have their own nodes. Unknown values
remain explicit and only reachable unknowns prevent emitting a material.
"""
import math


OPS = {'add', 'sub', 'mul', 'div', 'neg', 'abs', 'min', 'max', 'sqrt',
       'rsqrt', 'rcp', 'log2', 'exp2', 'pow', 'sat', 'sin', 'cos',
       'lt', 'le', 'gt', 'ge', 'eq', 'ne', 'select', 'pack', 'bit_and', 'bit_or'}


def compute(op, a):
    return {
        'add': lambda: a[0]+a[1], 'sub': lambda: a[0]-a[1],
        'mul': lambda: a[0]*a[1], 'div': lambda: a[0]/a[1],
        'neg': lambda: -a[0], 'abs': lambda: abs(a[0]),
        'min': lambda: min(a), 'max': lambda: max(a),
        'sqrt': lambda: math.sqrt(a[0]), 'rsqrt': lambda: 1/math.sqrt(a[0]),
        'rcp': lambda: 1/a[0], 'log2': lambda: math.log2(a[0]),
        'exp2': lambda: 2**a[0], 'pow': lambda: a[0]**a[1],
        'sat': lambda: max(0.,min(1.,a[0])), 'sin':lambda: math.sin(a[0]),
        'cos':lambda: math.cos(a[0]), 'lt':lambda: float(a[0]<a[1]),
        'le':lambda: float(a[0]<=a[1]), 'gt':lambda: float(a[0]>a[1]),
        'ge':lambda: float(a[0]>=a[1]), 'eq':lambda: float(a[0]==a[1]),
        'ne':lambda: float(a[0]!=a[1]), 'select':lambda: a[1] if a[0] else a[2],
        'pack':lambda: list(a), 'bit_and':lambda: int(a[0])&int(a[1]),
        'bit_or':lambda: int(a[0])|int(a[1]),
    }[op]()


class DAG:
    def __init__(self, nodes=None):
        self.nodes=list(nodes or [])
        self._cache={}

    def node(self,op,args=(),**meta):
        key=(op,tuple(args),repr(sorted(meta.items())))
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
        values=[self.value(x) for x in args]
        if all(v is not None for v in values):
            try:
                v=compute(op,values)
                if isinstance(v,(int,float)) and math.isfinite(v):return self.const(v)
            except (ValueError,ZeroDivisionError,OverflowError):pass
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
