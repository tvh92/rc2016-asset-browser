"""Test whether engine CB0 words and pixel position form a world-position reconstruction.

Hypothesis under test: CB0 dwords 92..107 are four corner vectors A, C, B, D
interpolated across the pixel position, ray = A + B*y + x*(C + D*y), and
CB0[28..30] a camera position, so that camera + ray * POS_W is world position.
Expressions are expanded into polynomials over named inputs (opaque
subexpressions are atoms) and compared per channel with these shapes, up to a
constant factor. The result reports which shapes occur and which engine inputs
remain after substituting the matched expressions. Diagnostic only: nothing
here claims what the engine stores in CB0 or binds a material.
"""
from collections import Counter

from rc2016.shaders.ir import DAG

MAX_TERMS = 64
ENGINE = ('CB0[', 'ps.POS')


def _mul(p, q):
    result = {}
    for a, x in p.items():
        for b, y in q.items():
            key = tuple(sorted(a + b))
            result[key] = result.get(key, 0.) + x * y
    return {k: v for k, v in result.items() if v}


def _add(p, q, sign=1.):
    result = dict(p)
    for k, v in q.items():
        result[k] = result.get(k, 0.) + sign * v
    return {k: v for k, v in result.items() if v}


def polynomials(ir, roots):
    """{node: polynomial} for reachable nodes; None where the expansion exceeds MAX_TERMS."""
    memo = {}

    def visit(i):
        if i in memo:
            return memo[i]
        n = ir.nodes[i]
        op, args = n['op'], n.get('args', [])
        if op == 'const':
            p = {(): float(n['value'])} if n['value'] else {}
        elif op == 'input':
            p = {(n['name'],): 1.}
        elif op in ('add', 'sub', 'mul', 'neg'):
            parts = [visit(a) for a in args]
            if any(x is None for x in parts):
                p = None
            elif op == 'add':
                p = _add(parts[0], parts[1])
            elif op == 'sub':
                p = _add(parts[0], parts[1], -1.)
            elif op == 'neg':
                p = {k: -v for k, v in parts[0].items()}
            else:
                p = _mul(parts[0], parts[1])
            if p is not None and len(p) > MAX_TERMS:
                p = None
        else:
            p = {(f'node{i}',): 1.}
        memo[i] = p
        return p

    for i in sorted(ir.reachable(roots)):
        visit(i)
    return memo


def target_shapes(c):
    """Polynomials of the hypothesis for vector component c (0..3)."""
    word = lambda base: f'CB0[{base + c}]'
    ray = {(word(92),): 1., tuple(sorted((word(100), 'ps.POS_Y'))): 1.,
           tuple(sorted((word(96), 'ps.POS_X'))): 1., tuple(sorted((word(104), 'ps.POS_X', 'ps.POS_Y'))): 1.}
    depth = _mul(ray, {('ps.POS_W',): 1.})
    shapes = {'ray': ray, 'ray*w': depth}
    if c < 3:
        shapes['camera+ray*w'] = _add(depth, {(f'CB0[{28 + c}]',): 1.})
    return shapes


def _proportional(p, q):
    if not p or set(p) != set(q):
        return None
    key = next(iter(q))
    scale = p[key] / q[key]
    return scale if all(abs(p[k] - scale * q[k]) <= 1e-6 * max(1., abs(p[k])) for k in q) else None


def analyse(nodes, roots):
    """Matched shapes and the engine inputs left after substituting them."""
    ir = DAG(nodes)
    polys = polynomials(ir, roots)
    shapes = [(f'{name}.{"xyzw"[c]}', shape) for c in range(4) for name, shape in target_shapes(c).items()]
    matches, substitutions = Counter(), {}
    for i, p in polys.items():
        if not p:
            continue
        for label, shape in shapes:
            scale = _proportional(p, shape)
            if scale is not None:
                matches[label if abs(scale - 1) < 1e-6 else f'{label} x{scale:g}'] += 1
                substitutions[i] = ir.node('input', name=f'reconstructed.{label}', kind='reconstructed')
                break
    replaced = ir.replace(roots, substitutions)
    residual = sorted({ir.nodes[i]['name'] for i in ir.reachable(replaced)
                       if ir.nodes[i]['op'] == 'input' and ir.nodes[i]['name'].startswith(ENGINE)})
    return dict(matches=dict(matches), residual=residual)
