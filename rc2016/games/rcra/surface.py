"""Rift Apart surface terms from a translated G-buffer writer (pass slot 4), and the forward colour of blended materials
(pass slot 1), for Blender and other renderers.

The writer ends in the engine's G-buffer packing, the same code in every graph. ``surface`` translates the writer with the
material bound (rc2016.games.rcra.shaders) and unpacks that code, using the decode of the engine's deferred apply program
(docs/rift-apart.md, "G-buffer and deferred lighting"):

    NormalSpec x, y   ftou(n.xy rsq(8 |n.z| + 8) 5791.205 + 2048), z sign in y bit 15  -> world normal n
    NormalSpec z      iadd(imul(Q(sqrt F0.r), 256), Q(sqrt F0.g)),  Q(x) = ftoi(255 x + 0.5)
    NormalSpec w      iadd(imul(Q(sqrt F0.b), 256), Q(gloss))
    DiffuseEmis rgb   albedo (an 8-bit UNORM target: clamped to [0, 1])
    DiffuseEmis a     occlusion when NormalSpec y bit 13 is set, else 0.2 log2(1 + min(E, 31)): emission E times albedo

F0 and gloss keep the game's 8-bit rounding (round(255 x) / 255, F0 squared after it); the 12-bit normal and the 8-bit
albedo, occlusion and emission are taken before their rounding. Everything the result still reads is a float expression
of interpolants, textures and named engine inputs (``PORTABLE_OPS``); integer operations that survive make the material
unsupported, never approximated.

``forward`` translates the forward program of a blended material (rc2016.games.rcra.material.material_blend) under the
forward profile of rc2016.games.rcra.shaders: every light source zero, so its target is the material's own colour and
alpha. It reads the view through the inputs VIEW.xyz (camera-to-point offset) and CAMERA.xyz (camera position).
"""
from collections import defaultdict

from rc2016.games.rcra import shaders as rshaders
from rc2016.shaders import sm5

# Float expression operations a node emitter must support (select takes a 'b' condition).
PORTABLE_OPS = {'const', 'input', 'channel', 'add', 'mul', 'div', 'min', 'max', 'neg', 'abs', 'sat', 'sqrt', 'rsq', 'rcp',
                'log2', 'exp2', 'frc', 'round_ne', 'round_ni', 'round_pi', 'round_z', 'sin', 'cos', 'select', 'eq', 'ne',
                'lt', 'ge', 'b_and', 'b_or', 'b_not', 'sample', 'ddx', 'ddy'}
# Interpolants a renderer supplies (engine vertex program layout, docs/rift-apart.md).
GEOMETRY_INPUTS = {'TEXCOORD.x': 'uv0.x', 'TEXCOORD.y': 'uv0.y', 'TEXCOORD1.x': 'uv1.x', 'TEXCOORD1.y': 'uv1.y',
                   'TEXCOORD2.x': 'normal.x', 'TEXCOORD2.y': 'normal.y', 'TEXCOORD2.z': 'normal.z',
                   'TEXCOORD3.x': 'tangent.x', 'TEXCOORD3.y': 'tangent.y', 'TEXCOORD3.z': 'tangent.z',
                   'TEXCOORD3.w': 'tangent.sign', 'TEXCOORD4.x': 'colour.r', 'TEXCOORD4.y': 'colour.g',
                   'TEXCOORD4.z': 'colour.b', 'TEXCOORD4.w': 'colour.a', 'SV_IsFrontFace': 'front_facing'}
# Per-draw and per-object values a material does not own, bound to the neutral draw: no dissolve, opaque, no render flags
# (class 0 lighting, occlusion mode decided by the material), instance 0, shader time 0. Viewer choices, labelled as such.
NEUTRAL_DRAW_FLOATS = {'TEXCOORD.z': 0.0, 'TEXCOORD.w': 1.0, 'TEXCOORD2.w': 0.25, 'GlobalWorldCBuffer.m_ShaderTimer': 0.0,
                       'GlobalWorldCBuffer.m_DeltaTime': 0.0, 'GlobalWorldCBuffer.m_TimeOfDay': 0.0,
                       'GlobalWorldCBuffer.m_MaterialLoDOffset': 0.0}
NEUTRAL_DRAW_DYNAMIC = {'MatDrawCB': 0}   # m_RenderFlags and the other per-draw words read through the instance index
SURFACE_TERMS = ('albedo.r', 'albedo.g', 'albedo.b', 'f0.r', 'f0.g', 'f0.b', 'gloss', 'normal.x', 'normal.y', 'normal.z',
                 'occlusion', 'emission', 'clip')
NORMAL_SCALE = 5791.205


class Unsupported(ValueError):
    pass


# Engine values a Blender material cannot supply, bound to review values (everything not listed is 0): adapted
# luminance 1 (as the viewer's RCRA_ADAPTED_LUMINANCE), the pixel centre of a 1920x1080 view for screen-space dither.
REVIEW_VALUES = {'GlobalWorldCBuffer.m_AdaptedLuminance': 1.0, 'SV_POSITION.x': 0.5, 'SV_POSITION.y': 0.5,
                 'SV_POSITION.z': 0.5, 'SV_POSITION.w': 1.0, 'GlobalViewportCBuffer.m_Dimensions': 1920.0,
                 'GlobalViewportCBuffer.m_Dimensions+4': 1080.0, 'GlobalViewportCBuffer.m_InvDimensions': 1 / 1920.0,
                 'GlobalViewportCBuffer.m_InvDimensions+4': 1 / 1080.0, 'GlobalViewportCBuffer.m_NearClip': 0.1,
                 'GlobalViewportCBuffer.m_FarClip': 1000.0}


class _Bindings(rshaders.NeutralEngine):
    """Material constants, the neutral engine and draw, and review values for every other non-geometry input."""

    def __init__(self, material):
        super().__init__(material)
        self.review = {}

    def _review(self, name):
        value = NEUTRAL_DRAW_FLOATS.get(name, REVIEW_VALUES.get(name, 0.0))
        if name not in NEUTRAL_DRAW_FLOATS:
            self.review[name] = value
        return self.graph.const('f', value)

    def input(self, name):
        if name == 'SV_IsFrontFace.x':   # read only as a truth value: a boolean input
            return self.graph.input('SV_IsFrontFace', 'b')
        if name in GEOMETRY_INPUTS:
            return None
        return self._review(name)

    def constant(self, name):
        bound = super().constant(name)
        return bound if bound is not None else self._review(name)

    def dynamic_constant(self, buffer, offset, index):
        if buffer in NEUTRAL_DRAW_DYNAMIC:
            return self.graph.const('i', NEUTRAL_DRAW_DYNAMIC[buffer])
        self.review['%s+%d[dynamic]' % (buffer, offset)] = 0.0
        return self.graph.const('i', 0)


FORWARD_FLAGS_NAME = 'MatDrawCB.m_RenderFlags'


class _ForwardBindings(_Bindings):
    """_Bindings with the forward profile: lit draw, light sources zero, engine textures and the view bound."""

    def input(self, name):
        if name in rshaders.FORWARD_VIEW_INPUTS:
            return self.graph.input(rshaders.FORWARD_VIEW_INPUTS[name], 'f')
        return super().input(name)

    def _view_constant(self, name):
        if name in rshaders.FORWARD_VIEW_INPUTS:
            return self.graph.input(rshaders.FORWARD_VIEW_INPUTS[name], 'f')
        if name in rshaders.FORWARD_VIEW_FLOATS:
            return self.graph.const('f', rshaders.FORWARD_VIEW_FLOATS[name])
        return super().constant(name)

    def __init__(self, material, draw_flags):
        super().__init__(material)
        self.draw_flags = draw_flags

    def constant(self, name):
        if name == FORWARD_FLAGS_NAME:   # the draw index folds to 0 (TEXCOORD2.w bound), so the read is a plain constant
            return self.graph.const('f', sm5.bits_float(self.draw_flags))
        return self._view_constant(name)

    def resource(self, graph, op, binding, args):
        if binding in rshaders.FORWARD_RESOURCES:   # an integer read (ld_structured) takes the same value's bits
            value = rshaders.FORWARD_RESOURCES[binding]
            kind = 'f' if op.startswith('sample') or binding == 'g_ViewDepthBuffer' else 'i'
            return [graph.const(kind, value if kind == 'f' else int(value))] * 4
        return None


class _Unpack:
    def __init__(self, graph):
        self.g = graph

    def node(self, i):
        return self.g.nodes[i]

    def value(self, i):
        return self.g.value(i)

    def strip_or(self, i, out):
        """Leaves of an or-tree (bit fields), constants dropped into ``out['bits']``."""
        n = self.node(i)
        if n['op'] == 'or':
            for a in n['args']:
                self.strip_or(a, out)
        elif n['op'] == 'const':
            out['bits'] |= n['value']
        else:
            out['parts'].append(i)
        return out

    def quantised(self, i):
        """x of ftoi(add(mul(x, 255), 0.5)) (or ftou), else None."""
        n = self.node(i)
        if n['op'] not in ('ftoi', 'ftou'):
            return None
        a = self.node(n['args'][0])
        if a['op'] != 'add' or self.value(a['args'][1]) != 0.5:
            return None
        m = self.node(a['args'][0])
        if m['op'] != 'mul':
            return None
        for x, k in ((0, 1), (1, 0)):
            if self.value(m['args'][k]) == 255.0:
                return m['args'][x]
        return None

    def byte_pair(self, i):
        """(high, low) float expressions of iadd(imul(Q(high), 256), Q(low)), pushing through selects."""
        n = self.node(i)
        if n['op'] == 'select':
            c, a, b = n['args']
            (ha, la), (hb, lb) = self.byte_pair(a), self.byte_pair(b)
            return self.g.select(c, ha, hb), self.g.select(c, la, lb)
        g = self.g
        if n['op'] == 'const' and n['type'] == 'i':   # a constant word: both bytes exact
            return g.const('f', (n['value'] >> 8 & 0xFF) / 255.0), g.const('f', (n['value'] & 0xFF) / 255.0)
        single = self.quantised(i)
        if single is not None:                       # only the low byte written
            return g.const('f', 0.0), single
        if n['op'] == 'iadd':
            for x, y in ((0, 1), (1, 0)):
                m = self.node(n['args'][x])
                if m['op'] == 'imul' and self.value(m['args'][1]) == 256:
                    high, low = self.quantised(m['args'][0]), self.byte(n['args'][y])
                    if high is not None and low is not None:
                        return high, low
                if m['op'] == 'const' and m['type'] == 'i' and m['value'] & 0xFF == 0:
                    low = self.quantised(n['args'][y])
                    if low is not None:
                        return g.const('f', (m['value'] >> 8 & 0xFF) / 255.0), low
        raise Unsupported('G-buffer byte pair is not the engine packing')

    def byte(self, i):
        """x of Q(x), or a constant byte as x = byte / 255."""
        n = self.node(i)
        if n['op'] == 'const' and n['type'] == 'i' and n['value'] < 256:
            return self.g.const('f', n['value'] / 255.0)
        return self.quantised(i)

    def normal_component(self, i):
        """(component, z) of ftou(add(mul(mul(rsq(add(mul(abs(z), 8), 8)), component), 5791.205), 2048))."""
        n = self.node(i)
        if n['op'] == 'select':
            c, a, b = n['args']
            (ca, za), (cb, zb) = self.normal_component(a), self.normal_component(b)
            return self.g.select(c, ca, cb), self.g.select(c, za, zb)
        if n['op'] != 'ftou':
            raise Unsupported('G-buffer normal is not the engine packing')
        a = self.node(n['args'][0])
        if a['op'] != 'add' or self.value(a['args'][1]) != 2048.0:
            raise Unsupported('G-buffer normal offset')
        m = self.node(a['args'][0])
        if m['op'] != 'mul' or abs((self.value(m['args'][1]) or 0) - NORMAL_SCALE) > 0.01:
            raise Unsupported('G-buffer normal scale')
        p = self.node(m['args'][0])
        if p['op'] != 'mul':
            raise Unsupported('G-buffer normal product')
        for r, c in ((0, 1), (1, 0)):
            q = self.node(p['args'][r])
            if q['op'] == 'rsq':
                s = self.node(q['args'][0])
                if s['op'] == 'add' and self.value(s['args'][1]) == 8.0:
                    t = self.node(s['args'][0])
                    if t['op'] == 'mul' and self.value(t['args'][1]) == 8.0 and self.node(t['args'][0])['op'] == 'abs':
                        return p['args'][c], self.node(t['args'][0])['args'][0]
        raise Unsupported('G-buffer normal weight')

    def normal_word(self, i):
        """(component, z, constant bits, other bit-field parts) of a NormalSpec x or y word."""
        fields = self.strip_or(i, dict(bits=0, parts=[]))
        packed, other = [], []
        for part in fields['parts']:
            (packed if self.node(part)['op'] in ('ftou', 'select') and self._is_normal(part) else other).append(part)
        if len(packed) != 1:
            raise Unsupported('G-buffer normal word has %d packed normals' % len(packed))
        component, z = self.normal_component(packed[0])
        return component, z, fields['bits'], other

    def _is_normal(self, i):
        try:
            self.normal_component(i)
            return True
        except Unsupported:
            return False

    def bit13(self, parts, bits):
        """The condition of NormalSpec y bit 13 (occlusion mode): constant, or bfi(1, 13, b2i(cond), 0)."""
        if bits & 0x2000:
            return self.g.const('b', True)
        for part in parts:
            n = self.node(part)
            if n['op'] == 'bfi' and [self.value(a) for a in n['args'][:2]] == [1, 13] and self.value(n['args'][3]) == 0:
                return self.g.boolean(n['args'][2])
        return self.g.const('b', False)

    def emission(self, i):
        """E of mul(log2(add(min(E, 31), 1)), 0.2), pushing through selects; None when not that form."""
        g = self.g
        n = self.node(i)
        if n['op'] == 'select':
            c, a, b = n['args']
            ea, eb = self.emission(a), self.emission(b)
            if ea is None or eb is None:
                return None
            return g.select(c, ea, eb)
        if n['op'] == 'const':   # a constant alpha decodes exactly: E = 2^(5 a) - 1
            return g.const('f', 2.0 ** (5.0 * n['value']) - 1.0)
        if n['op'] != 'mul' or abs((self.value(n['args'][1]) or 0) - 0.2) > 1e-6:
            return None
        lg = self.node(n['args'][0])
        if lg['op'] != 'log2':
            return None
        ad = self.node(lg['args'][0])
        if ad['op'] != 'add' or self.value(ad['args'][1]) != 1.0:
            return None
        mn = self.node(ad['args'][0])
        if mn['op'] != 'min' or self.value(mn['args'][1]) != 31.0:
            return None
        return mn['args'][0]


def _rounded(g, x):
    """round(255 x) / 255 as the G-buffer stores x (x >= 0 here)."""
    return g.op('mul', g.op('round_ni', g.op('add', g.op('mul', x, g.const('f', 255.0)), g.const('f', 0.5))),
                g.const('f', 1.0 / 255.0))


def _split_select(g, i, condition):
    """(when condition, otherwise) branches of a select on ``condition`` (or its negation), else (i, i)."""
    n = g.nodes[i]
    if n['op'] == 'select':
        c, a, b = n['args']
        if c == condition:
            return a, b
        if g.nodes[c]['op'] == 'b_not' and g.nodes[c]['args'][0] == condition:
            return b, a
    return i, i


def surface(graph_data, material_data, slot=rshaders.SLOT_GBUFFER):
    """The material's surface terms as one expression DAG.

    Returns dict(graph, terms {name: node}, textures {binding: path}, inputs [names], limitations [text]).
    Raises Unsupported when the writer is not the engine packing or keeps non-portable operations."""
    shader = rshaders.pass_shader(graph_data, slot)
    cb2 = rshaders.material_constants(graph_data, material_data)
    bindings = _Bindings(cb2)
    translator = sm5.Translator(shader, bindings)
    bindings.graph = translator.g
    tr = translator.run()
    g = tr.graph
    out = lambda r, c: tr.outputs.get(('o', r, c))   # noqa: E731
    if any(out(r, c) is None for r, c in ((0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1), (1, 2), (1, 3))):
        raise Unsupported('Writer does not write the NormalSpec and DiffuseEmis targets')
    unknown = g.unknowns([out(r, c) for r in (0, 1) for c in range(4)] + [tr.discard])
    if unknown:
        raise Unsupported('Writer depends on unknown values: ' + ', '.join(unknown[:3]))
    u = _Unpack(g)
    for key in [k for k in tr.outputs if k[0] == 'o' and k[1] == 1]:
        tr.outputs[key] = g.cast(tr.outputs[key], 'f')   # an 8-bit UNORM target: integer-typed words hold float bits
    nx, nz, _xbits, _xparts = u.normal_word(out(0, 0))
    ny, nz_y, ybits, yparts = u.normal_word(out(0, 1))
    if nz != nz_y:
        raise Unsupported('Normal words disagree on z')
    f0r, f0g = u.byte_pair(out(0, 2))
    f0b, gloss = u.byte_pair(out(0, 3))
    occlusion_mode = u.bit13(yparts, ybits)
    one, zero = g.const('f', 1.0), g.const('f', 0.0)
    occlusion_branch, emission_branch = _split_select(g, out(1, 3), occlusion_mode)
    emission = u.emission(emission_branch)
    mode = g.value(occlusion_mode)
    if mode is True:
        occlusion, emission = out(1, 3), zero
    elif mode is False:
        if emission is None:
            raise Unsupported('Emission alpha is not the engine encoding')
        occlusion = one
    else:
        if emission is None:
            raise Unsupported('Emission alpha is not the engine encoding')
        occlusion = g.select(occlusion_mode, occlusion_branch, one)
        emission = g.select(occlusion_mode, zero, emission)
    terms = {
        'albedo.r': g.op('sat', out(1, 0)), 'albedo.g': g.op('sat', out(1, 1)), 'albedo.b': g.op('sat', out(1, 2)),
        'normal.x': nx, 'normal.y': ny, 'normal.z': nz,
        'occlusion': g.op('sat', occlusion), 'emission': g.op('max', g.op('min', emission, g.const('f', 31.0)), zero),
        'clip': tr.discard,
    }
    for name, byte in (('f0.r', f0r), ('f0.g', f0g), ('f0.b', f0b)):
        stored = _rounded(g, g.op('sat', byte))
        terms[name] = g.op('mul', stored, stored)
    terms['gloss'] = _rounded(g, g.op('sat', gloss))
    limitations = ['Per-draw values are the neutral draw: no dissolve, opaque, no render flags (lighting class 0), '
                   'shader time 0.', 'The 12-bit normal and the 8-bit albedo, occlusion and emission are used before the '
                   "G-buffer's rounding; F0 and gloss keep it."]
    return _portable(g, terms, bindings, graph_data, material_data, tr, limitations, 'Surface')


def _portable(g, terms, bindings, graph_data, material_data, tr, limitations, what):
    """The result dict, after checking that every term is a portable float expression of geometry and material textures."""
    roots = list(terms.values())
    reach = g.reachable(roots)
    bad = sorted({g.nodes[i]['op'] for i in reach} - PORTABLE_OPS)
    if bad:
        raise Unsupported('Non-portable operations remain: ' + ', '.join(bad))
    if g.unknowns(roots):
        raise Unsupported('%s depends on unknown values' % what)
    textures = {}
    units = rshaders.material_units(graph_data, material_data)
    for i in reach:
        n = g.nodes[i]
        if n['op'] == 'sample':
            if n['binding'] not in units:
                raise Unsupported('%s samples engine texture %s' % (what, n['binding']))
            textures[n['binding']] = units[n['binding']]
        if n['op'] == 'load':
            raise Unsupported('%s loads from %s' % (what, n['binding']))
    inputs = sorted({g.nodes[i]['name'] for i in reach if g.nodes[i]['op'] == 'input'})
    engine = sorted(bindings.review)
    limitations = list(limitations)
    if engine:
        limitations.append('Engine inputs at review values (translation-time, may not all reach the %s): ' % what.lower() +
                           ', '.join('%s = %g' % (name, bindings.review[name]) for name in engine))
    return dict(graph=g, terms=terms, textures=textures, inputs=inputs, limitations=limitations,
                program_status=tr.status(), outputs=dict(tr.outputs))


FORWARD_TERMS = ('colour.r', 'colour.g', 'colour.b', 'alpha', 'clip')


def forward(graph_data, material_data):
    """The forward program's target under the forward profile: colour (unclamped, HDR), alpha and the discard.

    Returns the same dict as ``surface`` with terms FORWARD_TERMS. Raises Unsupported when the target depends on anything
    but geometry, the view inputs and the material's own textures and constants."""
    shader = rshaders.pass_shader(graph_data, rshaders.SLOT_FORWARD)
    from rc2016.games.rcra.material import draw_flags
    flags = draw_flags(material_data)
    bindings = _ForwardBindings(rshaders.material_constants(graph_data, material_data), flags)
    translator = sm5.Translator(shader, bindings)
    bindings.graph = translator.g
    tr = translator.run()
    g = tr.graph
    target = [tr.outputs.get(('o', 0, c)) for c in range(4)]
    if None in target:
        raise Unsupported('Forward program does not write a colour and alpha target')
    target = [g.cast(x, 'f') for x in target]
    terms = {'colour.r': target[0], 'colour.g': target[1], 'colour.b': target[2], 'alpha': g.op('sat', target[3]),
             'clip': tr.discard}
    limitations = [
        'Forward colour with every engine light source zero (key light, light grid, probes, GI, reflections): the '
        "material's own emission and effects. The renderer's lights shade its G-buffer surface instead of the forward "
        "program's lighting.",
        "Draw: render flags hold only the material's lit bit (%s), no dissolve, no particle values, shader time 0; "
        'stored scene depth at the far clip (no soft depth fade); view space with the world axes.'
        % ('lit' if flags else 'unlit: lighting term 1')]
    return _portable(g, terms, bindings, graph_data, material_data, tr, limitations, 'Forward colour')


def export_recipe(result):
    """A JSON-ready recipe: the reachable nodes renumbered, the terms, the textures and the limitations."""
    g, terms = result['graph'], result['terms']
    order = sorted(g.reachable(list(terms.values())))
    index = {old: new for new, old in enumerate(order)}
    nodes = []
    for old in order:
        n = dict(g.nodes[old])
        n['args'] = [index[a] for a in n['args']]
        for key in ('offsets',):
            if key in n:
                n[key] = list(n[key])
        nodes.append(n)
    return dict(version=1, nodes=nodes, terms={k: index[v] for k, v in terms.items()}, textures=result['textures'],
                inputs=result['inputs'], limitations=result['limitations'])


def evaluate_recipe(recipe, inputs, sample):
    """Evaluate a recipe's terms numerically (reference for emitter checks). ``sample(node, args) -> 4 floats``."""
    graph = sm5.Graph()
    graph.nodes = [dict(n) for n in recipe['nodes']]
    values = sm5.evaluate(graph, list(recipe['terms'].values()), defaultdict(float, inputs), sample)
    return {name: values[i] for name, i in recipe['terms'].items()}
