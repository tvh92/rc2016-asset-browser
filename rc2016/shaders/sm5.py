"""Shader Model 5 program -> typed expression DAG, with engine inputs named from the program's reflection.

Every SM5 register component becomes a node of a hash-consed DAG. Node types are 'f' (32-bit float), 'i' (32-bit
integer bits) and 'b' (a comparison result; SM5 stores it as 0xFFFFFFFF/0). A register keeps whatever node was written;
reading it with another type inserts a bitcast, which folds for constants. Constant folding uses float32 arithmetic.

Structured control flow is linearised by predication: if/else, switch, return and discard become select() and an
accumulated discard predicate. A loop is unrolled while its continuation predicate folds to a constant (so a light loop
over an empty light list disappears when its count is bound to 0); a loop whose trip count stays unknown poisons every
register it writes with an 'unknown' node, so outputs that do not depend on it still translate.

Constant buffer reads become 'input' nodes named '<cbuffer>.<variable>+<byte offset>' from the RDEF reflection;
interpolants become 'input' nodes named '<semantic>.<component>'; texture samples and buffer loads become 'sample'/'load'
nodes (four 'channel' nodes each) carrying the binding name. A ``Bindings`` object can replace any of these with values
during translation; that is how neutral engine inputs (no lights, default probe) are applied.
"""
from __future__ import annotations

import math
import struct

import numpy as np

from rc2016.shaders import dxbc

U32 = 0xFFFFFFFF
MAX_LOOP_ITERATIONS = 64           # unrolled iterations while the loop's continuation folds to a constant
MAX_PREDICATED_ITERATIONS = 4      # further iterations allowed while it does not (data-dependent trip count)
TRUE_BITS = U32


def f32(x):
    with np.errstate(all='ignore'):
        return float(np.float32(x))


def float_bits(x):
    return struct.unpack('<I', struct.pack('<f', np.float32(x)))[0]


def bits_float(b):
    return float(np.frombuffer(struct.pack('<I', b & U32), dtype=np.float32)[0])


def _signed(b):
    b &= U32
    return b - (1 << 32) if b & 0x80000000 else b


def _round_ne(x):
    return float(np.rint(np.float32(x)))


def _half_bits(x):
    return int(np.array(np.float32(x), dtype=np.float16).view(np.uint16))


def _half_float(b):
    return float(np.array(b & 0xFFFF, dtype=np.uint16).view(np.float16))


def _ftoi(x, low, high):
    if math.isnan(x):
        return 0
    if math.isinf(x):
        return high if x > 0 else low
    return int(max(low, min(high, math.trunc(x))))


def _bfe(width, offset, value, signed):
    width &= 31
    offset &= 31
    if width == 0:
        return 0
    if width + offset < 32:
        field = (value << (32 - width - offset)) & U32
    else:
        field = value & U32
    shift = 32 - width
    return (_signed(field) >> shift) & U32 if signed else field >> shift


def _f(op):
    def run(a):
        with np.errstate(all='ignore'):
            return float(op(*[np.float32(x) for x in a]))
    return run


# op: (result type, argument types, fold function on Python values)
OPS = {
    'add': ('f', 'ff', _f(lambda a, b: a + b)),
    'mul': ('f', 'ff', _f(lambda a, b: a * b)),
    'div': ('f', 'ff', _f(lambda a, b: a / b)),
    'min': ('f', 'ff', _f(np.fmin)),
    'max': ('f', 'ff', _f(np.fmax)),
    'neg': ('f', 'f', _f(lambda a: -a)),
    'abs': ('f', 'f', _f(abs)),
    'sat': ('f', 'f', _f(lambda a: np.float32(0) if np.isnan(a) else np.clip(a, 0, 1))),
    'sqrt': ('f', 'f', _f(np.sqrt)),
    'rsq': ('f', 'f', _f(lambda a: np.float32(1) / np.sqrt(a))),
    'rcp': ('f', 'f', _f(lambda a: np.float32(1) / a)),
    'log2': ('f', 'f', _f(np.log2)),
    'exp2': ('f', 'f', _f(np.exp2)),
    'frc': ('f', 'f', _f(lambda a: a - np.floor(a))),
    'round_ne': ('f', 'f', _f(np.rint)),
    'round_ni': ('f', 'f', _f(np.floor)),
    'round_pi': ('f', 'f', _f(np.ceil)),
    'round_z': ('f', 'f', _f(np.trunc)),
    'sin': ('f', 'f', _f(np.sin)),
    'cos': ('f', 'f', _f(np.cos)),
    'ddx': ('f', 'f', None), 'ddy': ('f', 'f', None),
    'eq': ('b', 'ff', lambda a: a[0] == a[1]),
    'ne': ('b', 'ff', lambda a: not (a[0] == a[1])),
    'lt': ('b', 'ff', lambda a: a[0] < a[1]),
    'ge': ('b', 'ff', lambda a: a[0] >= a[1]),
    'b_and': ('b', 'bb', lambda a: a[0] and a[1]),
    'b_or': ('b', 'bb', lambda a: a[0] or a[1]),
    'b_not': ('b', 'b', lambda a: not a[0]),
    'iadd': ('i', 'ii', lambda a: (a[0] + a[1]) & U32),
    'imul': ('i', 'ii', lambda a: (_signed(a[0]) * _signed(a[1])) & U32),
    'umul_hi': ('i', 'ii', lambda a: (a[0] * a[1]) >> 32),
    'imul_hi': ('i', 'ii', lambda a: ((_signed(a[0]) * _signed(a[1])) >> 32) & U32),
    'udiv': ('i', 'ii', lambda a: U32 if a[1] == 0 else a[0] // a[1]),
    'urem': ('i', 'ii', lambda a: U32 if a[1] == 0 else a[0] % a[1]),
    'ineg': ('i', 'i', lambda a: (-a[0]) & U32),
    'iabs': ('i', 'i', lambda a: abs(_signed(a[0])) & U32),
    'and': ('i', 'ii', lambda a: a[0] & a[1]),
    'or': ('i', 'ii', lambda a: a[0] | a[1]),
    'xor': ('i', 'ii', lambda a: a[0] ^ a[1]),
    'not': ('i', 'i', lambda a: ~a[0] & U32),
    'ishl': ('i', 'ii', lambda a: (a[0] << (a[1] & 31)) & U32),
    'ishr': ('i', 'ii', lambda a: (_signed(a[0]) >> (a[1] & 31)) & U32),
    'ushr': ('i', 'ii', lambda a: a[0] >> (a[1] & 31)),
    'imin': ('i', 'ii', lambda a: min(a, key=_signed)),
    'imax': ('i', 'ii', lambda a: max(a, key=_signed)),
    'umin': ('i', 'ii', min), 'umax': ('i', 'ii', max),
    'ieq': ('b', 'ii', lambda a: a[0] == a[1]),
    'ine': ('b', 'ii', lambda a: a[0] != a[1]),
    'ilt': ('b', 'ii', lambda a: _signed(a[0]) < _signed(a[1])),
    'ige': ('b', 'ii', lambda a: _signed(a[0]) >= _signed(a[1])),
    'ult': ('b', 'ii', lambda a: a[0] < a[1]),
    'uge': ('b', 'ii', lambda a: a[0] >= a[1]),
    'itof': ('f', 'i', lambda a: f32(_signed(a[0]))),
    'utof': ('f', 'i', lambda a: f32(a[0])),
    'ftoi': ('i', 'f', lambda a: _ftoi(a[0], -2**31, 2**31 - 1) & U32),
    'ftou': ('i', 'f', lambda a: _ftoi(a[0], 0, U32)),
    'f32tof16': ('i', 'f', lambda a: _half_bits(a[0])),
    'f16tof32': ('f', 'i', lambda a: f32(_half_float(a[0]))),
    'ubfe': ('i', 'iii', lambda a: _bfe(a[0], a[1], a[2], False)),
    'ibfe': ('i', 'iii', lambda a: _bfe(a[0], a[1], a[2], True)),
    'bfi': ('i', 'iiii', lambda a: (lambda m: ((a[2] << (a[1] & 31)) & m) | (a[3] & ~m & U32))(
        (((1 << (a[0] & 31)) - 1) << (a[1] & 31)) & U32)),
    'bfrev': ('i', 'i', lambda a: int('{:032b}'.format(a[0])[::-1], 2)),
    'countbits': ('i', 'i', lambda a: bin(a[0]).count('1')),
    'firstbit_hi': ('i', 'i', lambda a: U32 if a[0] == 0 else 31 - (a[0].bit_length() - 1)),
    'firstbit_lo': ('i', 'i', lambda a: U32 if a[0] == 0 else (a[0] & -a[0]).bit_length() - 1),
    'firstbit_shi': ('i', 'i', lambda a: (lambda v: U32 if v in (0, U32) else 31 - ((v if not v & 0x80000000 else ~v & U32).bit_length() - 1))(a[0])),
    'f2bits': ('i', 'f', lambda a: float_bits(a[0])),
    'bits2f': ('f', 'i', lambda a: bits_float(a[0])),
    'b2i': ('i', 'b', lambda a: U32 if a[0] else 0),
}
# select(cond: b, a, b) has the type of its operands; 'channel' takes its sample/load node's channel type.


class Unknown(Exception):
    pass


class Graph:
    """Hash-consed typed DAG. Nodes are dicts: op, type, args, and op-specific fields (value, name, reason...)."""

    def __init__(self):
        self.nodes = []
        self._cache = {}

    def _add(self, op, type_, args=(), **meta):
        key = (op, type_, tuple(args), tuple(sorted(meta.items())))
        index = self._cache.get(key)
        if index is None:
            index = self._cache[key] = len(self.nodes)
            self.nodes.append(dict(op=op, type=type_, args=list(args), **meta))
        return index

    # -- leaves
    def const(self, type_, value):
        if type_ == 'f':
            value = f32(value)
            index = self._add('const', 'f', bits=float_bits(value))
            self.nodes[index]['value'] = value
            return index
        if type_ == 'b':
            return self._add('const', 'b', value=bool(value))
        return self._add('const', 'i', value=int(value) & U32)

    def input(self, name, type_='f'):
        return self._add('input', type_, name=name)

    def unknown(self, reason, type_='f'):
        return self._add('unknown', type_, reason=reason)

    def value(self, index):
        n = self.nodes[index]
        return n['value'] if n['op'] == 'const' else None

    def is_const(self, index):
        return self.nodes[index]['op'] == 'const'

    def type(self, index):
        return self.nodes[index]['type']

    # -- conversions
    def cast(self, index, type_):
        have = self.type(index)
        if have == type_:
            return index
        if have == 'b':
            index, have = self.op('b2i', index), 'i'
            if type_ == 'i':
                return index
        if type_ == 'b':
            return self.op('ine', self.cast(index, 'i'), self.const('i', 0))
        n = self.nodes[index]
        if n['op'] == 'select':
            c, a, b = n['args']
            return self.select(c, self.cast(a, type_), self.cast(b, type_))
        if have == 'f' and type_ == 'i':
            if n['op'] == 'bits2f':
                return n['args'][0]
            return self.op('f2bits', index)
        n = self.nodes[index]
        if n['op'] == 'f2bits':
            return n['args'][0]
        return self.op('bits2f', index)

    def boolean(self, index):
        """The truth of a register value as SM5 tests it (any bit set)."""
        n = self.nodes[index]
        if n['type'] == 'b':
            return index
        if n['op'] == 'b2i':
            return n['args'][0]
        if n['op'] == 'f2bits' and self.nodes[n['args'][0]]['op'] == 'bits2f':
            return self.boolean(self.nodes[n['args'][0]]['args'][0])
        if n['type'] == 'f' and n['op'] == 'bits2f':
            return self.boolean(n['args'][0])
        if n['op'] == 'select':
            c, a, b = n['args']
            av, bv = self.value(a), self.value(b)
            if av is not None and bv is not None:
                at, bt = (av != 0, bv != 0) if n['type'] != 'f' else (float_bits(av) != 0, float_bits(bv) != 0)
                if at and not bt:
                    return c
                if bt and not at:
                    return self.op('b_not', c)
        return self.op('ine', self.cast(index, 'i'), self.const('i', 0))

    # -- operations with folding
    def op(self, name, *args):
        result, arg_types, fold = OPS[name]
        args = tuple(self.cast(a, t) for a, t in zip(args, arg_types))
        values = [self.value(a) for a in args]
        if fold is not None and None not in values:
            try:
                return self.const(result, fold(values))
            except (ValueError, OverflowError, ZeroDivisionError):
                pass
        simplified = self._simplify(name, args, values)
        if simplified is not None:
            return simplified
        return self._add(name, result, args)

    def never_negative(self, index, depth=8):
        """True when the float value is >= 0, -0 or NaN: never less than 0 (comparisons with NaN are false)."""
        n = self.nodes[index]
        op = n['op']
        if op == 'const':
            return n['type'] == 'f' and not n['value'] < 0.0
        if op in ('sat', 'abs', 'sqrt', 'rsq', 'exp2'):
            return True
        if depth == 0:
            return False
        args = n['args']
        if op == 'mul' and any(self.value(x) == 0.0 for x in args):
            return True   # +-0 or NaN
        if op in ('add', 'mul', 'div', 'min'):
            return all(self.never_negative(x, depth - 1) for x in args)
        if op == 'max':   # D3D max returns the other operand for NaN
            return any(self.never_negative(x, depth - 1) for x in args)
        if op == 'select':
            return all(self.never_negative(x, depth - 1) for x in args[1:])
        return False

    def _simplify(self, name, args, values):
        a = args
        v = values
        if name == 'lt' and v[1] is not None and v[1] <= 0.0 and self.never_negative(a[0]):
            return self.const('b', False)
        if name in ('ushr', 'ishr', 'ishl') and v[0] == 0:
            return self.const('i', 0)   # a shifted zero
        if name == 'mul':
            for x, y in ((0, 1), (1, 0)):
                if v[x] == 1.0:
                    return a[y]
        if name == 'add':
            for x, y in ((0, 1), (1, 0)):
                if v[x] is not None and v[x] == 0.0 and float_bits(v[x]) == 0x80000000:
                    return a[y]   # -0 + y == y exactly
        if name in ('b_and', 'b_or'):
            for x, y in ((0, 1), (1, 0)):
                if v[x] is not None:
                    if name == 'b_and':
                        return a[y] if v[x] else self.const('b', False)
                    return self.const('b', True) if v[x] else a[y]
            if a[0] == a[1]:
                return a[0]
        if name == 'b_not' and self.nodes[a[0]]['op'] == 'b_not':
            return self.nodes[a[0]]['args'][0]
        if name == 'and':
            # and(mask, x) with a comparison mask -> select(cond, x, 0)
            for x, y in ((0, 1), (1, 0)):
                n = self.nodes[a[x]]
                if n['op'] == 'b2i':
                    return self.select(n['args'][0], a[y], self.const('i', 0))
                if v[x] == U32:
                    return a[y]
                if v[x] == 0:
                    return self.const('i', 0)
        if name == 'or':
            for x, y in ((0, 1), (1, 0)):
                if v[x] == 0:
                    return a[y]
            na, nb = self.nodes[a[0]], self.nodes[a[1]]
            if na['op'] == 'b2i' and nb['op'] == 'b2i':
                return self.op('b2i', self.op('b_or', na['args'][0], nb['args'][0]))
        if name == 'not' and self.nodes[a[0]]['op'] == 'b2i':
            return self.op('b2i', self.op('b_not', self.nodes[a[0]]['args'][0]))
        if name == 'iadd':
            for x, y in ((0, 1), (1, 0)):
                if v[x] == 0:
                    return a[y]
        if name == 'ine' and v[1] == 0 and self.nodes[a[0]]['op'] == 'b2i':
            return self.nodes[a[0]]['args'][0]
        if name == 'ieq' and v[1] == 0 and self.nodes[a[0]]['op'] == 'b2i':
            return self.op('b_not', self.nodes[a[0]]['args'][0])
        return None

    def select(self, cond, a, b):
        cond = self.boolean(cond)
        if a == b:
            return a
        c = self.value(cond)
        if c is not None:
            return a if c else b
        ta, tb = self.type(a), self.type(b)
        if ta != tb:
            # Mixed register contents: keep the float view when either side is a float constant 0.
            want = 'f' if 'f' in (ta, tb) and 'b' not in (ta, tb) else 'i'
            a, b = self.cast(a, want), self.cast(b, want)
        n = self.nodes[cond]
        if n['op'] == 'b_not':
            return self.select(n['args'][0], b, a)
        return self._add('select', self.type(a), (cond, a, b))

    def channel(self, source, index, type_):
        return self._add('channel', type_, (source,), index=index)

    # -- analysis
    def reachable(self, roots):
        seen, stack = set(), [r for r in roots if r is not None]
        while stack:
            i = stack.pop()
            if i in seen:
                continue
            seen.add(i)
            stack.extend(self.nodes[i]['args'])
        return seen

    def unknowns(self, roots):
        return sorted({self.nodes[i]['reason'] for i in self.reachable(roots) if self.nodes[i]['op'] == 'unknown'})

    def leaves(self, roots, op):
        return sorted({self.nodes[i]['name'] for i in self.reachable(roots) if self.nodes[i]['op'] == op
                       and 'name' in self.nodes[i]})


class Bindings:
    """Values substituted during translation. Every method returns None to leave the input symbolic."""

    def input(self, name):                      # interpolant 'TEXCOORD3.x' or system value 'SV_POSITION.y'
        return None

    def constant(self, name):                   # 'GlobalViewportCBuffer.m_ViewToWorldMat+16'
        return None

    def dynamic_constant(self, buffer, offset, index):   # cb[r.x + n]: buffer name, byte offset of n, index node
        return None

    def resource(self, graph, op, binding, args):        # sample/ld on a named binding -> 4 node ids or None
        return None


def _cb_name(reflection, slot, byte):
    buffer = None
    if reflection is not None:
        binding = reflection.binding('cb', slot)
        if binding is not None:
            buffer = reflection.constant_buffer(binding.name)
    if buffer is None:
        return 'cb%d+%d' % (slot, byte)
    for var in buffer.variables:
        if var.offset <= byte < var.offset + max(var.size, 4):
            # A buffer's only struct variable (g_VP, g_World) is left out of the name; any other variable is named.
            path, rest = [buffer.name], byte - var.offset
            if not var.members or len(buffer.variables) > 1:
                path.append(var.name)
            while var.members:
                # A struct: the member with the largest offset at or below the byte (members carry no size).
                name, at, member = max((m for m in var.members if m[1] <= rest), key=lambda m: m[1],
                                       default=(None, 0, None))
                if name is None:
                    break
                path.append(name)
                rest -= at
                var = member
            return '.'.join(path) + ('+%d' % rest if rest else '')
    return '%s+%d' % (buffer.name, byte)


def _binding_name(reflection, kind, register):
    if reflection is not None:
        b = reflection.binding(kind, register)
        if b is not None:
            return b.name
    return '%s%d' % (kind, register)


def _blocks(instructions):
    """Matching structure: for each if/else/loop/switch/case position, its partners."""
    match, stack = {}, []
    for i, ins in enumerate(instructions):
        op = ins.opcode
        if op in ('if', 'loop', 'switch'):
            stack.append([op, i])
        elif op == 'else':
            stack[-1].append(i)
        elif op in ('endif', 'endloop', 'endswitch'):
            entry = stack.pop()
            match[entry[1]] = (entry[2:], i)
    if stack:
        raise dxbc.DxbcError('Unbalanced control flow')
    return match


class _Flow:
    """Predicates of the current point: active lanes, plus per-loop and per-switch break state."""

    def __init__(self, active):
        self.active = active


class Translator:
    FLOAT_OPS = {'add', 'mul', 'mad', 'div', 'min', 'max', 'sqrt', 'rsq', 'rcp', 'log', 'exp', 'frc', 'round_ne',
                 'round_ni', 'round_pi', 'round_z', 'dp2', 'dp3', 'dp4', 'sincos', 'eq', 'ne', 'lt', 'ge',
                 'deriv_rtx', 'deriv_rty', 'deriv_rtx_coarse', 'deriv_rtx_fine', 'deriv_rty_coarse',
                 'deriv_rty_fine', 'ftoi', 'ftou', 'f32tof16', 'mov', 'movc', 'swapc'}

    def __init__(self, shader, bindings=None, graph=None):
        self.shader = shader
        self.reflection = shader.reflection
        self.bindings = bindings or Bindings()
        self.g = graph or Graph()
        self.regs = {}            # ('r', n, c) / ('x', n, i, c) / ('o', n, c) -> node
        self.outputs = {}
        self.discard = self.g.const('b', False)
        self.alive = self.g.const('b', True)    # lanes that have not returned
        self.outer = self.g.const('b', True)    # entry predicate of the region being run (absolute)
        self.stores = []          # UAV stores: dict(binding, address, values {lane: node}, active)
        self.unsupported = []
        self.loops = []           # (position, iterations or None)
        self.instructions = shader.program.instructions
        self.match = _blocks(self.instructions)
        self.icb = shader.program.immediate_constants
        self._inputs = {}
        for e in shader.inputs:
            for c in range(4):
                if e.mask >> c & 1:
                    self._inputs[(e.register, c)] = e
        self._indexable = {}
        for d in shader.program.declarations:
            if d.opcode == 'dcl_indexabletemp':
                self._indexable[d.data[0]] = (d.data[1], d.data[2])

    # ------------------------------------------------------------------ operands
    def _index(self, imm, rel):
        if rel is None:
            return imm, None
        value = self.read(rel, 'i')[0]
        v = self.g.value(value)
        if v is not None:
            return (imm + v) & U32, None
        return imm, value

    def _read_component(self, operand, comp, want):
        g = self.g
        t = operand.type
        if t == 'l':
            raw = operand.values[comp if len(operand.values) == 4 else 0]
            return g.const('f', bits_float(raw)) if want == 'f' else g.const('i', raw)
        if t == 'r':
            reg = operand.indices[0][0]
            node = self.regs.get(('r', reg, comp))
            return node if node is not None else g.const('i', 0)
        if t == 'x':
            reg = operand.indices[0][0]
            index, dynamic = self._index(*operand.indices[1])
            if dynamic is not None:
                return self._dynamic_indexable(reg, index, dynamic, comp)
            node = self.regs.get(('x', reg, index, comp))
            return node if node is not None else g.const('i', 0)
        if t == 'v':
            if len(operand.indices) != 1 or operand.indices[0][1] is not None:
                raise Unknown('indexed input register')
            reg = operand.indices[0][0]
            element = self._inputs.get((reg, comp))
            name = '%s.%s' % (element.semantic if element else 'v%d' % reg, 'xyzw'[comp])
            bound = self.bindings.input(name)
            if bound is not None:
                return bound
            itype = 'f'
            if element is not None and element.component_type in (1, 2):
                itype = 'i'
            return g.input(name, itype)
        if t == 'cb':
            slot = operand.indices[0][0]
            index, dynamic = self._index(*operand.indices[1])
            if dynamic is not None:
                buffer = _cb_name(self.reflection, slot, index * 16 + 4 * comp).split('.')[0].split('+')[0]
                bound = self.bindings.dynamic_constant(buffer, index * 16 + 4 * comp, dynamic)
                if bound is not None:
                    return bound
                node = g._add('cbload', 'f', (dynamic,), buffer=buffer, offset=index * 16 + 4 * comp)
                return node
            name = _cb_name(self.reflection, slot, index * 16 + 4 * comp)
            bound = self.bindings.constant(name)
            if bound is not None:
                return bound
            return g.input(name)
        if t == 'icb':
            index, dynamic = self._index(*operand.indices[0])
            if dynamic is None:
                at = 4 * index + comp
                if at >= len(self.icb):
                    raise Unknown('immediate constant buffer read past its end')
                raw = self.icb[at]
                return g.const('f', bits_float(raw)) if want == 'f' else g.const('i', raw)
            rows = len(self.icb) // 4
            # Dynamic icb index: a select chain over the rows.
            node = g.const('i', self.icb[comp])
            for row in range(1, rows):
                raw = self.icb[4 * row + comp]
                pick = g.op('ieq', g.op('iadd', dynamic, g.const('i', index)), g.const('i', row))
                node = g.select(pick, g.const('i', raw), node)
            return node
        if t in ('vPrim', 'vCoverage', 'vThreadID', 'vThreadGroupID', 'vThreadIDInGroup', 'vThreadIDInGroupFlattened',
                 'vGSInstanceID'):
            return g.input(t + '.' + 'xyzw'[comp], 'i')
        raise Unknown('operand type ' + t)

    def _dynamic_indexable(self, reg, index, dynamic, comp):
        size = self._indexable.get(reg, (0, 4))[0]
        g = self.g
        node = g.const('i', 0)
        for k in range(size):
            value = self.regs.get(('x', reg, k, comp), g.const('i', 0))
            pick = g.op('ieq', g.op('iadd', dynamic, g.const('i', index)), g.const('i', k))
            node = g.select(pick, value, node)
        return node

    def read(self, operand, want):
        """Four component nodes (in destination lane order), converted to ``want`` and with modifiers applied."""
        g = self.g
        lanes = operand.selected()
        cache = {}
        out = []
        for comp in lanes:
            if comp not in cache:
                node = self._read_component(operand, comp, want)
                if want != 'raw':
                    node = g.cast(node, want)
                    if operand.modifier in ('abs', 'absneg'):
                        node = g.op('abs', node) if want == 'f' else g.op('iabs', node)
                    if operand.modifier in ('neg', 'absneg'):
                        node = g.op('neg', node) if want == 'f' else g.op('ineg', node)
                cache[comp] = node
            out.append(cache[comp])
        return out

    def write(self, operand, lane, value, saturate=False, flow=None):
        g = self.g
        if saturate:
            value = g.op('sat', g.cast(value, 'f'))
        t = operand.type
        if t == 'null':
            return
        if operand.components == 4 and operand.mode == 'mask' and not operand.mask >> lane & 1:
            return
        if t == 'r':
            key = ('r', operand.indices[0][0], lane)
        elif t == 'o':
            key = ('o', operand.indices[0][0], lane)
        elif t == 'x':
            reg = operand.indices[0][0]
            index, dynamic = self._index(*operand.indices[1])
            if dynamic is not None:
                size = self._indexable.get(reg, (0, 4))[0]
                for k in range(size):
                    pick = g.op('ieq', g.op('iadd', dynamic, g.const('i', index)), g.const('i', k))
                    old = self.regs.get(('x', reg, k, lane), g.const('i', 0))
                    active = g.op('b_and', flow.active, pick)
                    self.regs[('x', reg, k, lane)] = g.select(active, value, old)
                return
            key = ('x', reg, index, lane)
        elif t == 'oDepth':
            key = ('oDepth', 0, 0)
        elif t == 'oMask':
            key = ('oMask', 0, 0)
        else:
            raise Unknown('destination ' + t)
        old = self.regs.get(key)
        if old is None:
            old = g.const(g.type(value), 0)
        self.regs[key] = g.select(flow.active, value, old) if flow is not None else value

    def lanes(self, operand):
        if operand.type == 'null':
            return []
        if operand.components == 4 and operand.mode == 'mask':
            return [c for c in range(4) if operand.mask >> c & 1]
        if operand.components == 1:
            return [0]
        return [0, 1, 2, 3]

    # ------------------------------------------------------------------ execution
    def run(self):
        flow = _Flow(self.g.const('b', True))
        self._block(0, len(self.instructions), flow, [])
        g = self.g
        outputs = {}
        for key, node in self.regs.items():
            if key[0] in ('o', 'oDepth', 'oMask'):
                outputs[key] = node
        translation = Translation(g, outputs, self.discard, self.unsupported, self.loops, self.shader)
        translation.stores = self.stores
        return translation

    def _block(self, start, end, flow, loops):
        """Execute instructions [start, end) under ``flow``; ``loops`` is the stack of loop/switch states."""
        g = self.g
        i = start
        while i < end:
            ins = self.instructions[i]
            op = ins.opcode
            if op == 'if':
                cond = g.boolean(self.read(ins.operands[0], 'i')[0])
                if not ins.nonzero:
                    cond = g.op('b_not', cond)
                partners, close = self.match[i]
                else_at = partners[0] if partners else None
                taken = g.value(cond)
                if taken is not None:   # uniform after folding: run only the branch that executes
                    if taken:
                        self._block(i + 1, else_at if else_at is not None else close, flow, loops)
                    elif else_at is not None:
                        self._block(else_at + 1, close, flow, loops)
                elif g.value(flow.active) is not False:
                    active = flow.active
                    then = self._region(i + 1, else_at if else_at is not None else close, flow, loops,
                                        g.op('b_and', active, cond))
                    other = (self._region(else_at + 1, close, flow, loops,
                                          g.op('b_and', active, g.op('b_not', cond))) if else_at is not None
                             else self._identity(loops))
                    self._merge(active, [(cond, then)], other, flow, loops)
                i = close + 1
                continue
            if op == 'loop':
                _partners, close = self.match[i]
                self._loop(i, close, flow, loops)
                i = close + 1
                continue
            if op == 'switch':
                _partners, close = self.match[i]
                self._switch(i, close, flow, loops)
                i = close + 1
                continue
            if op in ('break', 'breakc', 'continue', 'continuec'):
                cond = flow.active
                if op.endswith('c'):
                    c = g.boolean(self.read(ins.operands[0], 'i')[0])
                    if not ins.nonzero:
                        c = g.op('b_not', c)
                    cond = g.op('b_and', cond, c)
                if op.startswith('break'):
                    state = loops[-1]
                    state['broken'] = g.op('b_or', state['broken'], cond)
                else:
                    state = next(s for s in reversed(loops) if s['kind'] == 'loop')
                    state['continued'] = g.op('b_or', state['continued'], cond)
                flow.active = g.op('b_and', flow.active, g.op('b_not', cond))
                i += 1
                continue
            if op in ('ret', 'retc'):
                cond = flow.active
                if op == 'retc':
                    c = g.boolean(self.read(ins.operands[0], 'i')[0])
                    if not ins.nonzero:
                        c = g.op('b_not', c)
                    cond = g.op('b_and', cond, c)
                self.alive = g.op('b_and', self.alive, g.op('b_not', cond))
                flow.active = g.op('b_and', flow.active, g.op('b_not', cond))
                i += 1
                continue
            if op == 'discard':
                c = g.boolean(self.read(ins.operands[0], 'i')[0])
                if not ins.nonzero:
                    c = g.op('b_not', c)
                hit = g.op('b_and', flow.active, c)
                self.discard = g.op('b_or', self.discard, hit)
                flow.active = g.op('b_and', flow.active, g.op('b_not', hit))
                self.alive = g.op('b_and', self.alive, g.op('b_not', hit))
                i += 1
                continue
            if op in ('case', 'default', 'endswitch', 'endif', 'endloop', 'else', 'label'):
                raise dxbc.DxbcError('Unexpected %s at instruction %d' % (op, i))
            if g.value(flow.active) is False:
                i += 1
                continue
            try:
                self._instruction(ins, flow)
            except Unknown as error:
                self.unsupported.append('%s: %s' % (op, error))
                for operand in ins.operands[:self._destinations(ins)]:
                    for lane in self.lanes(operand):
                        try:
                            self.write(operand, lane, g.unknown('%s at %d' % (op, ins.position)), flow=flow)
                        except Unknown:
                            pass
            i += 1

    def _region(self, start, end, flow, loops, entry):
        """Run [start, end) as if every lane entered it; return its effects relative to that entry.

        ``entry`` is the region's entry predicate relative to the enclosing region (kept for UAV stores)."""
        g = self.g
        saved_regs, saved_discard, saved_alive, saved_active = self.regs, self.discard, self.alive, flow.active
        saved_outer = self.outer
        self.outer = g.op('b_and', saved_outer, entry)
        saved_states = [(s['broken'], s['continued']) for s in loops]
        self.regs = dict(saved_regs)
        self.discard, self.alive, flow.active = g.const('b', False), g.const('b', True), g.const('b', True)
        for state in loops:
            state['broken'] = state['continued'] = g.const('b', False)
        self._block(start, end, flow, loops)
        result = dict(regs=self.regs, discard=self.discard, alive=self.alive,
                      states=[(s['broken'], s['continued']) for s in loops])
        self.regs, self.discard, self.alive, flow.active = saved_regs, saved_discard, saved_alive, saved_active
        self.outer = saved_outer
        for state, (broken, continued) in zip(loops, saved_states):
            state['broken'], state['continued'] = broken, continued
        return result

    def _identity(self, loops):
        f = self.g.const('b', False)
        return dict(regs=self.regs, discard=f, alive=self.g.const('b', True), states=[(f, f)] * len(loops))

    def _merge(self, active, choices, default, flow, loops):
        """Join region results: ``choices`` [(condition, result)] are mutually exclusive, ``default`` covers the rest,
        and lanes outside ``active`` keep their state."""
        g = self.g

        def pick(get):
            node = get(default)
            for cond, result in reversed(choices):
                node = g.select(cond, get(result), node)
            return node
        before = self.regs
        keys = set(before) | set(default['regs'])
        for _cond, result in choices:
            keys |= set(result['regs'])
        merged = {}
        for key in keys:
            old = before.get(key)
            present = [r['regs'][key] for r in [default] + [c[1] for c in choices] if key in r['regs']]
            zero = g.const(g.type(present[0] if present else old), 0)
            fallback = old if old is not None else zero
            value = pick(lambda r: r['regs'].get(key, fallback))
            merged[key] = g.select(active, value, fallback)
        self.regs = merged
        self.discard = g.op('b_or', self.discard, g.op('b_and', active, pick(lambda r: r['discard'])))
        died = g.op('b_and', active, g.op('b_not', pick(lambda r: r['alive'])))
        self.alive = g.op('b_and', self.alive, g.op('b_not', died))
        for k, state in enumerate(loops):
            state['broken'] = g.op('b_or', state['broken'], g.op('b_and', active, pick(lambda r: r['states'][k][0])))
            state['continued'] = g.op('b_or', state['continued'],
                                      g.op('b_and', active, pick(lambda r: r['states'][k][1])))
        flow.active = self._restore_breaks(active, loops)

    def _restore_breaks(self, active, loops):
        # Lanes that broke, continued or returned inside a branch stay inactive after it joins.
        g = self.g
        for state in loops:
            active = g.op('b_and', active, g.op('b_not', state['broken']))
            if state['kind'] == 'loop':
                active = g.op('b_and', active, g.op('b_not', state['continued']))
        return g.op('b_and', active, self.alive)

    def _written(self, start, end):
        keys = set()
        for ins in self.instructions[start:end]:
            if ins.opcode in ('if', 'else', 'endif', 'loop', 'endloop', 'break', 'breakc', 'continue', 'continuec',
                              'switch', 'case', 'default', 'endswitch', 'ret', 'retc', 'discard'):
                continue
            for operand in ins.operands[:self._destinations(ins)]:
                if operand.type in ('r', 'o'):
                    for lane in self.lanes(operand):
                        keys.add((operand.type, operand.indices[0][0], lane))
                elif operand.type == 'x':
                    reg = operand.indices[0][0]
                    for k in range(self._indexable.get(reg, (0, 4))[0]):
                        for lane in self.lanes(operand):
                            keys.add(('x', reg, k, lane))
        return keys

    def _loop(self, start, close, flow, loops):
        """Run the loop as a region entered by every lane, then merge it with select(entry, after, before).

        Inside the region predicates are relative to the loop entry, so a trip count that folds (a fixed tap count, or
        an empty light list) unrolls even when the loop sits under a data-dependent branch."""
        g = self.g
        entry = flow.active
        before, discard_before, alive_before = dict(self.regs), self.discard, self.alive
        self.discard, self.alive = g.const('b', False), g.const('b', True)
        saved_outer = self.outer
        self.outer = g.op('b_and', saved_outer, entry)
        stores_before = len(self.stores)
        node_mark = len(g.nodes)
        predicated = 0
        body = self.instructions[start + 1:close]
        # Data-dependent trip counts are unrolled only for small loops without nested loops (PCF taps and the like).
        predicated_limit = MAX_PREDICATED_ITERATIONS if len(body) <= 64 and not any(
            x.opcode == 'loop' for x in body) else 0
        state = dict(kind='loop', broken=g.const('b', False), continued=g.const('b', False))
        iterations = 0
        while True:
            state['continued'] = g.const('b', False)
            continuing = g.op('b_and', g.op('b_not', state['broken']), self.alive)
            flow.active = continuing
            if g.value(continuing) is False:
                break
            if not g.is_const(continuing):
                predicated += 1
            if (iterations >= MAX_LOOP_ITERATIONS or predicated > predicated_limit
                    or len(g.nodes) - node_mark > 200000):
                iterations = None
                break
            self._block(start + 1, close, flow, [state])
            iterations += 1
        self.loops.append((self.instructions[start].position, iterations))
        self.outer = saved_outer
        if iterations is None:
            del self.stores[stores_before:]
            # Trip count unknown: keep the registers from before and poison what the loop writes.
            reason = 'loop at %d' % self.instructions[start].position
            after = dict(before)
            for key in self._written(start + 1, close):
                after[key] = g.unknown(reason, 'f')
            if not g.is_const(self.discard) or g.value(self.discard):
                self.discard = g.unknown(reason, 'b')
            if not g.is_const(self.alive) or not g.value(self.alive):
                self.alive = g.unknown(reason, 'b')
        else:
            after = self.regs
        merged = {}
        for key in set(before) | set(after):
            old = before.get(key)
            new = after.get(key, old)
            if old is None:
                old = g.const(g.type(new), 0)
            merged[key] = g.select(entry, new, old)
        self.regs = merged
        self.discard = g.op('b_or', discard_before, g.op('b_and', entry, self.discard))
        self.alive = g.op('b_and', alive_before, g.op('b_or', g.op('b_not', entry), self.alive))
        flow.active = self._restore_breaks(g.op('b_and', entry, self.alive), loops)

    def _switch(self, start, close, flow, loops):
        g = self.g
        selector = self.read(self.instructions[start].operands[0], 'i')[0]
        active = flow.active
        # Case labels at this nesting level; consecutive labels share the body that follows them.
        labels, depth = [], 0
        for k in range(start + 1, close):
            op = self.instructions[k].opcode
            if op in ('if', 'loop', 'switch'):
                depth += 1
            elif op in ('endif', 'endloop', 'endswitch'):
                depth -= 1
            elif depth == 0 and op in ('case', 'default'):
                labels.append(k)

        def test(k):
            c = self.instructions[k]
            return g.op('ieq', selector, g.const('i', c.operands[0].values[0])) if c.opcode == 'case' else None
        others = g.const('b', False)
        for k in labels:
            if test(k) is not None:
                others = g.op('b_or', others, test(k))
        state = dict(kind='switch', broken=g.const('b', False), continued=g.const('b', False))
        stack = loops + [state]
        choices, default, hit, has_default = [], None, g.const('b', False), False
        for n, k in enumerate(labels):
            t = test(k)
            has_default = has_default or t is None
            hit = g.op('b_or', hit, t if t is not None else g.op('b_not', others))
            stop = labels[n + 1] if n + 1 < len(labels) else close
            if stop == k + 1:
                continue
            result = self._region(k + 1, stop, flow, stack, g.op('b_and', active, hit))
            if has_default:
                default = result
            else:
                choices.append((hit, result))
            hit, has_default = g.const('b', False), False
        if default is None:
            default = self._identity(stack)
        self._merge(active, choices, default, flow, loops)

    @staticmethod
    def _destinations(ins):
        op = ins.opcode
        if op in ('sincos', 'udiv', 'umul', 'imul', 'uaddc', 'usubb', 'swapc'):
            return 2
        if op in ('store_uav_typed', 'store_raw', 'store_structured') or op.startswith('atomic_'):
            return 0
        if op.startswith('imm_atomic'):
            return 1
        return 1 if ins.operands else 0

    # ------------------------------------------------------------------ instructions
    def _instruction(self, ins, flow):
        g = self.g
        op = ins.opcode
        o = ins.operands
        sat = ins.saturate

        def unary(fn, want='f'):
            dst = o[0]
            a = self.read(o[1], want)
            for lane in self.lanes(dst):
                self.write(dst, lane, fn(a[lane]), sat, flow)

        def binary(fn, want='f'):
            dst = o[0]
            a, b = self.read(o[1], want), self.read(o[2], want)
            for lane in self.lanes(dst):
                self.write(dst, lane, fn(a[lane], b[lane]), sat, flow)

        simple_f = {'add': 'add', 'mul': 'mul', 'div': 'div', 'min': 'min', 'max': 'max'}
        if op in simple_f:
            return binary(lambda a, b: g.op(simple_f[op], a, b))
        if op == 'mad':
            dst = o[0]
            a, b, c = self.read(o[1], 'f'), self.read(o[2], 'f'), self.read(o[3], 'f')
            for lane in self.lanes(dst):
                self.write(dst, lane, g.op('add', g.op('mul', a[lane], b[lane]), c[lane]), sat, flow)
            return
        unary_f = {'sqrt': 'sqrt', 'rsq': 'rsq', 'rcp': 'rcp', 'log': 'log2', 'exp': 'exp2', 'frc': 'frc',
                   'round_ne': 'round_ne', 'round_ni': 'round_ni', 'round_pi': 'round_pi', 'round_z': 'round_z'}
        if op in unary_f:
            return unary(lambda a: g.op(unary_f[op], a))
        if op.startswith('deriv_rt'):
            return unary(lambda a: g.op('ddx' if 'rtx' in op else 'ddy', a))
        if op in ('dp2', 'dp3', 'dp4'):
            n = int(op[2])
            dst = o[0]
            a, b = self.read(o[1], 'f'), self.read(o[2], 'f')
            total = g.op('mul', a[0], b[0])
            for k in range(1, n):
                total = g.op('add', total, g.op('mul', a[k], b[k]))
            for lane in self.lanes(dst):
                self.write(dst, lane, total, sat, flow)
            return
        if op == 'sincos':
            a = self.read(o[2], 'f')
            for k, fn in ((0, 'sin'), (1, 'cos')):
                for lane in self.lanes(o[k]):
                    self.write(o[k], lane, g.op(fn, a[lane]), sat, flow)
            return
        if op in ('eq', 'ne', 'lt', 'ge'):
            return binary(lambda a, b: g.op(op, a, b))
        if op == 'mov':
            dst = o[0]
            a = self.read(o[1], 'f' if (sat or o[1].modifier) else 'raw')
            for lane in self.lanes(dst):
                self.write(dst, lane, a[lane], sat, flow)
            return
        if op == 'movc':
            dst = o[0]
            want = 'f' if sat or o[2].modifier or o[3].modifier else 'raw'
            c, a, b = self.read(o[1], 'raw'), self.read(o[2], want), self.read(o[3], want)
            for lane in self.lanes(dst):
                self.write(dst, lane, g.select(g.boolean(c[lane]), a[lane], b[lane]), sat, flow)
            return
        if op == 'swapc':
            c, a, b = self.read(o[2], 'raw'), self.read(o[3], 'raw'), self.read(o[4], 'raw')
            for lane in self.lanes(o[0]):
                self.write(o[0], lane, g.select(g.boolean(c[lane]), b[lane], a[lane]), False, flow)
            for lane in self.lanes(o[1]):
                self.write(o[1], lane, g.select(g.boolean(c[lane]), a[lane], b[lane]), False, flow)
            return
        int_binary = {'iadd': 'iadd', 'and': 'and', 'or': 'or', 'xor': 'xor', 'ishl': 'ishl', 'ishr': 'ishr',
                      'ushr': 'ushr', 'imin': 'imin', 'imax': 'imax', 'umin': 'umin', 'umax': 'umax',
                      'ieq': 'ieq', 'ine': 'ine', 'ilt': 'ilt', 'ige': 'ige', 'ult': 'ult', 'uge': 'uge'}
        if op in int_binary:
            if op in ('and', 'or'):
                return self._logic(ins, flow)
            return binary(lambda a, b: g.op(int_binary[op], a, b), 'i')
        if op in ('ineg', 'not', 'bfrev', 'countbits', 'firstbit_hi', 'firstbit_lo', 'firstbit_shi', 'f16tof32'):
            return unary(lambda a: g.op(op, a), 'i')
        if op in ('itof', 'utof'):
            return unary(lambda a: g.op(op, a), 'i')
        if op in ('ftoi', 'ftou', 'f32tof16'):
            return unary(lambda a: g.op(op, a), 'f')
        if op == 'imad' or op == 'umad':
            dst = o[0]
            a, b, c = self.read(o[1], 'i'), self.read(o[2], 'i'), self.read(o[3], 'i')
            for lane in self.lanes(dst):
                self.write(dst, lane, g.op('iadd', g.op('imul', a[lane], b[lane]), c[lane]), sat, flow)
            return
        if op in ('imul', 'umul'):
            a, b = self.read(o[2], 'i'), self.read(o[3], 'i')
            for lane in self.lanes(o[0]):
                self.write(o[0], lane, g.op('imul_hi' if op == 'imul' else 'umul_hi', a[lane], b[lane]), False, flow)
            for lane in self.lanes(o[1]):
                self.write(o[1], lane, g.op('imul', a[lane], b[lane]), False, flow)
            return
        if op == 'udiv':
            a, b = self.read(o[2], 'i'), self.read(o[3], 'i')
            for lane in self.lanes(o[0]):
                self.write(o[0], lane, g.op('udiv', a[lane], b[lane]), False, flow)
            for lane in self.lanes(o[1]):
                self.write(o[1], lane, g.op('urem', a[lane], b[lane]), False, flow)
            return
        if op in ('ubfe', 'ibfe'):
            dst = o[0]
            w, s, v = self.read(o[1], 'i'), self.read(o[2], 'i'), self.read(o[3], 'i')
            for lane in self.lanes(dst):
                self.write(dst, lane, g.op(op, w[lane], s[lane], v[lane]), False, flow)
            return
        if op == 'bfi':
            dst = o[0]
            w, s, v, b = (self.read(x, 'i') for x in o[1:5])
            for lane in self.lanes(dst):
                self.write(dst, lane, g.op('bfi', w[lane], s[lane], v[lane], b[lane]), False, flow)
            return
        if op in ('sample', 'sample_l', 'sample_b', 'sample_d', 'sample_c', 'sample_c_lz', 'gather4', 'gather4_c',
                  'ld', 'ld_ms', 'ld_structured', 'ld_raw', 'resinfo', 'bufinfo', 'lod', 'sample_info', 'sample_pos',
                  'ld_uav_typed', 'gather4_po', 'gather4_po_c'):
            return self._resource(ins, flow)
        if op == 'store_uav_typed' and o[0].type == 'u' and o[0].indices[0][1] is None:
            values = self.read(o[2], 'raw')
            self.stores.append(dict(binding=_binding_name(self.reflection, 'u', o[0].indices[0][0]),
                                    address=self.read(o[1], 'i'),
                                    values={lane: values[lane] for lane in self.lanes(o[0])},
                                    active=g.op('b_and', self.outer, flow.active)))
            return
        if op == 'sync':
            return
        if op in ('store_uav_typed', 'store_raw', 'store_structured') or op.startswith('atomic_') \
                or op.startswith('imm_atomic'):
            self.unsupported.append(op)
            if op.startswith('imm_atomic'):
                for lane in self.lanes(o[0]):
                    self.write(o[0], lane, g.unknown(op), False, flow)
            return
        if op == 'nop':
            return
        raise Unknown('opcode')

    def _logic(self, ins, flow):
        g = self.g
        dst = ins.operands[0]
        a, b = self.read(ins.operands[1], 'raw'), self.read(ins.operands[2], 'raw')
        name = ins.opcode
        for lane in self.lanes(dst):
            x, y = a[lane], b[lane]
            tx, ty = g.type(x), g.type(y)
            if tx == 'b' and ty == 'b':
                value = g.op('b_and' if name == 'and' else 'b_or', x, y)
            elif name == 'and' and 'b' in (tx, ty):
                cond, other = (x, y) if tx == 'b' else (y, x)
                value = g.select(cond, other, g.const(g.type(other), 0))
            else:
                value = g.op(name, x, y)
                # Keep a float view of masked floats (and r0.x, mask, l(1.0)) so later float math stays typed.
                nv = g.nodes[value]
                if nv['op'] == 'select' and 'f' in (tx, ty):
                    value = g.cast(value, 'f')
            self.write(dst, lane, value, False, flow)

    def _resource(self, ins, flow):
        g = self.g
        op = ins.opcode
        o = ins.operands
        # Operand layouts (d3d11 tokenized format): dst, address, resource[, sampler][, extra...].
        layout = {'sample': (1, 2, 3, ()), 'sample_l': (1, 2, 3, (4,)), 'sample_b': (1, 2, 3, (4,)),
                  'sample_d': (1, 2, 3, (4, 5)), 'sample_c': (1, 2, 3, (4,)), 'sample_c_lz': (1, 2, 3, (4,)),
                  'gather4': (1, 2, 3, ()), 'gather4_c': (1, 2, 3, (4,)), 'lod': (1, 2, 3, ()),
                  'gather4_po': (1, 3, 4, (2,)), 'gather4_po_c': (1, 3, 4, (2, 5)),
                  'ld': (1, 2, None, ()), 'ld_ms': (1, 2, None, (3,)), 'ld_uav_typed': (1, 2, None, ()),
                  'ld_raw': (1, 2, None, ()), 'ld_structured': (1, 3, None, (2,)),
                  'resinfo': (1, 2, None, ()), 'bufinfo': (None, 1, None, ()), 'sample_info': (None, 1, None, ()),
                  'sample_pos': (2, 1, None, ())}[op]
        address_at, resource_at, sampler_at, extras = layout
        resource = o[resource_at]
        if resource.type == 'g':
            raise Unknown('groupshared memory')
        kind = 'u' if resource.type == 'u' else 't'
        if resource.indices and resource.indices[0][1] is not None:
            raise Unknown('dynamically indexed resource')
        register = resource.indices[0][0]
        binding = _binding_name(self.reflection, kind, register)
        sampler = None
        if sampler_at is not None:
            sampler = _binding_name(self.reflection, 's', o[sampler_at].indices[0][0])
        args = []
        if address_at is not None:
            want = 'i' if op.startswith('ld') or op in ('resinfo', 'sample_pos') else 'f'
            args.extend(self.read(o[address_at], want))
        for e in extras:
            want = 'i' if op in ('ld_structured', 'ld_ms', 'gather4_po', 'gather4_po_c') and e in (2, 3) else 'f'
            args.extend(self.read(o[e], want))
        bound = self.bindings.resource(g, op, binding, args)
        rtype = 'f'
        if ins.return_types and ins.return_types[0] in (3, 4):
            rtype = 'i'
        if op in ('ld_raw', 'ld_structured', 'bufinfo', 'sample_info') or (ins.return_types and ins.return_types[0] == 6):
            rtype = 'i'
        if bound is None:
            source = g._add('sample' if op.startswith(('sample', 'gather', 'lod')) else 'load', 'v', tuple(args),
                            opcode=op, binding=binding, sampler=sampler, offsets=ins.offsets,
                            dimension=ins.resource_dimension, controls=ins.controls)
            channels = [g.channel(source, c, rtype) for c in range(4)]
        else:
            channels = bound
        dst = o[0]
        swizzle = resource.selected()
        for lane in self.lanes(dst):
            self.write(dst, lane, channels[swizzle[lane]], ins.saturate, flow)


class Translation:
    """The translated program: output nodes keyed ('o', register, component), the discard predicate, diagnostics."""

    def __init__(self, graph, outputs, discard, unsupported, loops, shader):
        self.graph, self.outputs, self.discard = graph, outputs, discard
        self.unsupported, self.loops, self.shader = unsupported, loops, shader

    def output(self, register, component):
        return self.outputs.get(('o', register, component))

    def status(self):
        """'ready' when no output depends on an unknown value, else 'partial' (or 'empty' with no outputs)."""
        roots = list(self.outputs.values())
        if not roots:
            return 'empty'
        return 'partial' if self.graph.unknowns(roots + [self.discard]) else 'ready'


def translate(shader, bindings=None):
    return Translator(shader, bindings).run()


# ---------------------------------------------------------------------------------------------- evaluation

def evaluate(graph, roots, inputs=None, resource=None, constants=None):
    """Evaluate nodes numerically. ``inputs``: {name: value}, a float for a float input (pass bits_float(n) for integer
    data the program reads from a constant buffer), an int for an integer input; ``resource(node, args) -> 4 values``;
    ``constants``: {(buffer, offset): value} for dynamic cbloads (index ignored). Returns {root: value}."""
    inputs = inputs if inputs is not None else {}
    constants = constants if constants is not None else {}
    values = {}
    nodes = graph.nodes

    def visit(i):
        if i in values:
            return values[i]
        n = nodes[i]
        op = n['op']
        if op == 'const':
            v = n['value']
        elif op == 'input':
            v = inputs[n['name']]
            v = f32(v) if n['type'] == 'f' else int(v) & U32
        elif op == 'unknown':
            raise Unknown(n['reason'])
        elif op == 'cbload':
            v = f32(constants[(n['buffer'], n['offset'])])
        elif op == 'select':
            v = visit(n['args'][1]) if visit(n['args'][0]) else visit(n['args'][2])
        elif op == 'channel':
            v = visit(n['args'][0])[n['index']]
            v = f32(v) if n['type'] == 'f' else int(v) & U32
        elif op in ('sample', 'load'):
            v = resource(n, [visit(a) for a in n['args']])
        elif op in ('ddx', 'ddy'):
            v = 0.0
        else:
            v = OPS[op][2]([visit(a) for a in n['args']])
            if OPS[op][0] == 'f':
                v = f32(v)
        values[i] = v
        return v
    return {r: visit(r) for r in roots}
