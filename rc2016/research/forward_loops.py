"""Material-only terms of forward light-loop programs (read-only research, no rendering).

The forward colour programs of a materialgraph walk the engine light list in a loop that the translator
(`rc2016.shaders.translator`) refuses ("Loop at N not shown to end"). The loop only accumulates engine light
into registers; everything the material decides is computed before it (loop-head registers) or after it
(the final colour). `OpaqueLoopCompiler` runs the same translator but, at a taken backward branch, keeps the
first pass for control flow only and replaces every register the pass changed with an explicit
`unsupported` node named `loop-varying VGPR n (loop at b)`. Everything outside the loop is then the
translator's own exact DAG, with the loop outputs left as named unknowns.

This is a research tool: it proves which terms of a program do not depend on the light list, it does not
produce a renderable material. Texture samples whose descriptor the pass contract cannot join are kept as
distinct `unsupported` nodes carrying `api_guess`/`channel_guess`/`sample_offset` (the resource-table slot
of the sample's T# is the API, as in `rc2016.research.forward_programs`).
"""
from __future__ import annotations

import struct

from rc2016.shaders import gcn_fields, translator
from rc2016.shaders.ir import compute, from_bits, signed  # noqa: F401  (compute is the evaluator's op table)

# Instance record bits tested by the lit forward paths (docs/material-decoding/ratchet-forward-passes.md).
INSTANCE_FLAGS_LIT = 0x2261


def _image_info(code, instructions):
    """{instruction offset: dict(api, sampler, channels)} for every IMAGE_* instruction, S_LOADs tracked linearly."""
    loads, info = {}, {}
    for i in instructions:
        name, f = i['name'], i['fields']
        if name in ('GCN_S_LOAD_DWORDX8', 'GCN_S_LOAD_DWORDX4'):
            base, off = f['src']
            if f['imm'][0]:
                dwords = off
            elif off == 255:
                dwords = struct.unpack_from('<I', code, i['offset'] + 4)[0]
            else:
                dwords = None
            loads[f['dst'][0]] = (base, dwords)
        elif name.startswith('GCN_IMAGE_'):
            _, texture, sampler = f['src'][:3]
            w = i['wide']
            mask = (w >> 8) & 15
            lt, ls = loads.get(texture), loads.get(sampler)
            info[i['offset']] = dict(
                api=lt[1] // 8 if lt and lt[1] is not None and lt[1] % 8 == 0 else None,
                sampler=ls[1] // 4 if ls and ls[1] is not None and ls[1] % 4 == 0 else None,
                channels=[c for c in range(4) if mask & (1 << c)], opcode=name[len('GCN_'):])
    return info


class OpaqueLoopCompiler(translator.Compiler):
    """`translator.Compiler` with backward branches kept opaque (see the module docstring)."""

    def run(self, instructions):
        index = {i['relative']: n for n, i in enumerate(instructions)}
        self.instructions = instructions
        self.images = _image_info(self.code, instructions)
        self.snapshots, self.loops = {}, []
        n = 0
        while n < len(instructions):
            i = instructions[n]
            self.position = n
            if i['relative'] in self.pending:
                self.bp = self.p_or(self.bp, self.pending.pop(i['relative']))
            if self.ir.value(self.bp) == 0:
                n += 1
                continue
            self.snapshots.setdefault(i['relative'], (dict(self.v), dict(self.s), self.exec))
            self.step(i)
            if self.back is not None:
                target, taken = self.back
                self.back = None
                if self.ir.value(taken) != 0:
                    self._close_loop(i, target, taken)
            n += 1

    def _close_loop(self, instruction, target, taken):
        v0, s0, e0 = self.snapshots[target]
        changed_v = [r for r, x in self.v.items() if v0.get(r) != x]
        changed_s = [r for r, x in self.s.items() if s0.get(r) != x]
        self.loops.append(dict(target=target, branch=instruction['relative'], vgprs=changed_v, sgprs=changed_s))
        for r in changed_v:
            self.v[r] = self.unknown(f'loop-varying VGPR {r} (loop at {target})', loop=target)
        masks = {r for r in changed_s if r in self.masks or (r - 1) in self.masks}
        for r in changed_s:
            if r not in masks:
                self.s[r] = self.unknown(f'loop-varying SGPR {r} (loop at {target})', loop=target)
        for r in sorted(r for r in masks if r in self.masks):
            m = self.unknown(f'loop-varying lane mask s{r} (loop at {target})', loop=target)
            self.s[r], self.s[r + 1], self.masks[r] = m, self.mask_high(m), m
        if self.exec != e0:
            self.exec = self.unknown(f'loop-varying exec (loop at {target})', loop=target)
        after = instruction['relative'] + instruction['length']
        self.pending[after] = self.p_or(self.pending.get(after, self.ir.const(0.)), taken)

    def unknown(self, reason, *args, **meta):
        if reason.startswith(('Unsupported indirect', 'Incomplete', 'Unresolved/unsupported image')) and not args:
            sequence = self.__dict__.setdefault('_channel_sequence', {})
            j = sequence.get(self.current, 0)
            sequence[self.current] = j + 1
            info = self.__dict__.get('images', {}).get(self.current)
            if info and j < len(info['channels']):
                meta = dict(meta, sample_offset=self.current, api_guess=info['api'], channel_guess=info['channels'][j])
        return super().unknown(reason, *args, **meta)

    def _scc_condition(self, offset):
        """SCC of a frame flag test the translator cannot fold: one named unknown per use."""
        atom = self.unknown(f'runtime SCC @{offset} from {self.scc_source}'[:200], scc_atom=True)
        self.scc = ('uniform', atom)
        return atom

    def instruction(self, i):
        name = i['name'].replace('GCN_', '').replace('_E64', '')
        if name == 'S_CSELECT_B64' and i['kind'] == 1:
            f = i['fields']
            a, b = (self.mask(r) for r in f['src'][:2])
            if a is not None and b is not None:
                if self.scc == ('exec',):
                    cond = self.ir.const(1.)
                elif self.scc and self.scc[0] == 'mask':
                    cond = self.ir.const(0. if self.ir.value(self.scc[1]) == 0 else 1.)
                elif self.scc and self.scc[0] == 'uniform':
                    cond = self.scc[1]
                else:
                    cond = self._scc_condition(i['relative'])
                self.set_mask(f['dst'][0], self.p_select(cond, a, b))
                return
        return super().instruction(i)

    def branch(self, i, name, f):
        try:
            return super().branch(i, name, f)
        except ValueError as error:
            if 'unresolved SCC' not in str(error):
                raise
            self._scc_condition(i['relative'])
            return super().branch(i, name, f)


def compile_opaque(program):
    """Compile one `translator.pixel_programs` entry with opaque loops; returns the finished compiler."""
    compiler = OpaqueLoopCompiler(program['code'], program['usage_slots'], program.get('ps_input_addr'),
                                  program.get('ps_input_ena'), program.get('vgpr_count'))
    compiler.run(gcn_fields.decode_stream(program['code'], 0))
    return compiler


def bind_frozen(compiler, *, flags=INSTANCE_FLAGS_LIT, time=0.0, normal_scale=1.0, geometric_aa=0.0, pass_word=0):
    """Substitute the named frozen engine inputs; returns (export roots of target 0, substitutions).

    The three leading TBUFFER_LOAD_FORMAT_X reads are the instance record: the one the lit-path bit tests read
    is the flag word, the other two are the fade slope and offset (taken as 0, no fade). CB0[135] is a per-pass word
    (``pass_word``; captured 0x40 in the G-buffer and depth-equal forward passes, 0x2 in the blended forward pass, see
    docs/material-decoding/thumper-glove-glass.md); CB0[95] the normal-map fade, CB0[99] the time, `attribute1.2` the
    geometric-AA width.
    """
    ir = compiler.ir
    loads = sorted((n.get('offset'), i) for i, n in enumerate(ir.nodes)
                   if n['op'] == 'unsupported' and n.get('reason', '').startswith('TBUFFER_LOAD_FORMAT_X'))[:3]
    tested = {i for _, i in loads for n in ir.nodes if n['op'] == 'u_and' and i in n['args']}
    if len(tested) != 1:
        raise ValueError('instance flag word is not unique')
    flag = tested.pop()
    subs = {i: ir.const(0.) for _, i in loads}
    subs[flag] = ir.const(from_bits(flags))
    named = {'CB0[135]': from_bits(pass_word), 'CB0[95]': normal_scale, 'CB0[99]': time, 'attribute1.2': geometric_aa}
    for i, n in enumerate(ir.nodes):
        if n['op'] == 'input' and n['name'] in named:
            subs[i] = ir.const(named[n['name']])
    return ir.replace(list(compiler.exports[0]), subs), subs


def loop_head(compiler, subs, loop=0):
    """{vgpr: node} of the registers live at the head of the ``loop``-th loop, frozen inputs substituted."""
    v0 = compiler.snapshots[compiler.loops[loop]['target']][0]
    regs = sorted(v0)
    return dict(zip(regs, compiler.ir.replace([v0[r] for r in regs], subs)))


def evaluate(ir, root, inputs, samples=None, loops=None):
    """Numeric value of a node. ``inputs`` maps input names, ``samples`` maps (api, channel) of an unresolved
    texture sample to its value and ``loops`` maps a loop-varying register number to its value."""
    samples, loops, memo = samples or {}, loops or {}, {}
    nodes = ir.nodes

    def visit(i):
        if i in memo:
            return memo[i]
        n = nodes[i]
        op = n['op']
        if op == 'const':
            value = n['value']
        elif op == 'input':
            value = inputs[n['name']]
        elif op == 'texture':
            value = samples[(n['api'], n['channel'])]
        elif op == 'unsupported':
            if 'sample_offset' in n:
                value = samples[(n['api_guess'], n['channel_guess'])]
            elif 'loop-varying VGPR' in n.get('reason', ''):
                value = loops[int(n['reason'].split('VGPR ')[1].split()[0])]
            else:
                raise ValueError(n.get('reason', 'unsupported'))
        else:
            value = compute(op, [visit(j) for j in n['args']])
        memo[i] = value
        return value

    return visit(root)


def dependencies(ir, root):
    """Names of the inputs, texture samples and loop registers a node reads."""
    names = set()
    for i in ir.reachable([root]):
        n = ir.nodes[i]
        if n['op'] == 'input':
            names.add(n['name'])
        elif n['op'] == 'unsupported':
            if 'sample_offset' in n:
                names.add('S%s.%s' % (n['api_guess'], 'RGBA'[n['channel_guess']]))
            elif 'loop-varying VGPR' in n.get('reason', ''):
                names.add('LOOP%s' % n['reason'].split('VGPR ')[1].split()[0])
            else:
                names.add('?' + n.get('reason', '')[:30])
    return names


def loop_body_tokens(program, loop=0):
    """(opcode, constant operands, literal) of every instruction in the body of the ``loop``-th backward branch.

    Constant operands are the encoded sources that are not registers (VCC/EXEC halves, inline constants, the
    literal marker 255); register numbers are left out so two programs with different register allocation
    compare equal when they execute the same loop.
    """
    code = program['code']
    instructions = gcn_fields.decode_stream(code, 0)
    found = [n for n, i in enumerate(instructions)
             if 'BRANCH' in i['name'] and i['fields'].get('branch_target_relative', 0) < 0]
    end = found[loop]
    target = instructions[end]['relative'] + instructions[end]['fields']['branch_target_relative']
    start = next(n for n, i in enumerate(instructions) if i['relative'] == target)
    tokens = []
    for i in instructions[start:end + 1]:
        wide = i['kind'] in (7, 11, 12, 13, 14, 15)
        literal = (struct.unpack_from('<I', code, i['relative'] + i['length'] - 4)[0]
                   if i['length'] > (8 if wide else 4) else None)
        constants = tuple(r for r in i['fields'].get('src', []) or [] if 104 <= r < 256)
        tokens.append((i['name'], constants, literal))
    return tokens
