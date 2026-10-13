"""Where pixel-shader interpolants come from: PS input semantics joined to vertex-shader exports.

A pixel program's ``attributeN`` is its N-th input semantic. The vertex program
exporting that semantic writes it to a parameter slot (EXP target 32 + slot);
its expression over the vertex inputs (fetched into VGPRs by input semantic)
says what the interpolant is, for example a vertex colour passed through.

Header layouts follow Gnm: ShaderCommonData (8 bytes: 23-bit code size, input
usage-slot count in the top byte), then the stage registers. PsShader has 12
registers, the input-semantic count at 0x38 and usage slots at 0x3c, then
2-byte PixelInputSemantic entries. VsShader has 7 registers, then input and
export semantic counts at 0x24 and 0x25, usage slots at 0x28, then 4-byte
VertexInputSemantic entries and the export semantics. The export entry width
is not assumed: a layout is accepted only when it reproduces the header's own
stage size, and is reported as unverified otherwise. Diagnostic only; nothing
here binds a material.
"""
import re
import struct

from rc2016.shaders.translator import SHADER_SECTION, Compiler, decoder, sections

PIXEL = 2


def _starts(data):
    spans = sections(data)
    if SHADER_SECTION not in spans:
        return []
    lo, size = spans[SHADER_SECTION]
    return [lo + m.start() for m in re.finditer(b'Shdr', data[lo:lo + size])
            if m.start() < 3 or data[lo + m.start() - 3:lo + m.start()] != b'Orb'], lo + size


def _code(data, pos, stage, stage_size, starts, hi):
    declared = struct.unpack_from('<I', data, stage)[0] & 0x7fffff
    code_start = stage + stage_size
    orb = code_start + declared - 28
    end = min([x for x in starts if x > pos] + [hi])
    if orb + 28 > end or data[orb:orb + 7] != b'OrbShdr':
        return None
    length = struct.unpack_from('<I', data, orb + 8)[0] >> 8
    if length <= 0 or code_start + length > orb:
        return None
    return data[code_start:code_start + length], f'0x{struct.unpack_from("<I", data, orb + 16)[0]:08x}'


def _slots(data, usage, count):
    return [dict(zip(('kind', 'api', 'reg', 'flags'), struct.unpack_from('<4B', data, p)))
            for p in range(usage, usage + count * 4, 4)]


def shader_headers(data):
    """Every Shdr envelope: pixel input semantics, or the vertex semantic tables when the layout checks out."""
    found = _starts(data)
    if not found:
        return []
    starts, hi = found
    rows = []
    for pos in starts:
        if pos + 0x40 > hi:
            continue
        kind, words, count = data[pos + 8], data[pos + 9], data[pos + 19]
        stage = pos + 16
        row = dict(header_offset=pos, type=kind, stage_size=words * 4, usage_count=count)
        if kind == PIXEL:
            inputs = data[stage + 0x38]
            semantics = stage + 0x3c + count * 4
            row['inputs'] = [dict(semantic=w & 0xff, default=(w >> 8) & 3, flat=bool(w >> 10 & 1),
                                  linear=bool(w >> 11 & 1), custom=bool(w >> 12 & 1))
                             for w in struct.unpack_from(f'<{inputs}H', data, semantics)]
            rows.append(row)
            continue
        inputs, exports = data[stage + 0x24], data[stage + 0x25]
        widths = [w for w in (2, 4) if (0x28 + count * 4 + inputs * 4 + exports * w + 3) & ~3 == words * 4]
        row.update(input_count=inputs, export_count=exports, export_entry_bytes=widths[0] if len(widths) == 1 else None)
        if len(widths) != 1:
            row['layout'] = 'unverified'
            rows.append(row)
            continue
        row['layout'] = 'verified'
        usage = stage + 0x28
        base = usage + count * 4
        row['usage_slots'] = _slots(data, usage, count)
        row['inputs'] = [dict(zip(('semantic', 'vgpr', 'size'), struct.unpack_from('<3B', data, base + 4 * j)))
                         for j in range(inputs)]
        base += inputs * 4
        row['exports'] = [dict(semantic=data[base + widths[0] * j], slot=data[base + widths[0] * j + 1] & 31)
                          for j in range(exports)]
        code = _code(data, pos, stage, words * 4, starts, hi)
        if code:
            row['code'], row['shader_id'] = code
        rows.append(row)
    return rows


def listing(instructions, upto, limit=14, before=None):
    """Compact opcode listing of the first instructions through offset upto, or of
    the `before` instructions that end at upto."""
    rows = []
    if before is not None:
        end = next((n for n, i in enumerate(instructions) if i['relative'] >= upto), len(instructions) - 1)
        instructions, limit = instructions[max(0, end - before):end + 1], before + 1
    for i in instructions[:limit]:
        f = i['fields']
        operands = ','.join(str(x) for x in (f.get('dst') or []) + (f.get('src') or []))
        rows.append(f"{i['relative']}:{i['name'].replace('GCN_', '')}({operands})")
        if i['relative'] >= upto:
            break
    return ' '.join(rows)


def vertex_exports(row, position=False):
    """{parameter slot: [IR node per channel]} and the DAG, or (None, reason).

    With position=True the parameter map also holds the clip position export
    (EXP target 12) under the key 'position'."""
    if 'code' not in row:
        return None, 'no verified vertex program'
    compiler = Compiler(row['code'], row['usage_slots'])
    for entry in row['inputs']:
        for j in range(entry['size']):
            compiler.v[entry['vgpr'] + j] = compiler.ir.node(
                'input', name=f"vertex.semantic{entry['semantic']}.{'xyzw'[j]}", kind='vertex_input',
                semantic=entry['semantic'], channel=j)
    compiler.fetch_call = True
    # Per-draw flag branches (for example a buffer word & 3) are traced on both paths.
    compiler.runtime_conditions = True
    instructions = decoder().decode_stream(row['code'], 0)
    try:
        compiler.run(instructions)
    except (ValueError, IndexError, KeyError, struct.error) as exc:
        return None, f'{type(exc).__name__}: {exc}; before: ' + listing(instructions, compiler.current, before=10)
    params = {t - 32: v for t, v in compiler.exports.items() if 32 <= t < 64}
    if position and 12 in compiler.exports:
        params['position'] = compiler.exports[12]
    return (params, compiler.ir), None


def interpolant_sources(data, attributes, depth=6):
    """{'attributeN': {'semantic': s, 'sources': [description, ...]}} for the PS attribute indices given."""
    headers = shader_headers(data)
    pixel = [h for h in headers if h['type'] == PIXEL]
    vertex = [h for h in headers if h['type'] != PIXEL]
    compiled = {}
    result = {}
    for index in sorted(attributes):
        semantics = sorted({p['inputs'][index]['semantic'] for p in pixel if index < len(p['inputs'])})
        entry = dict(semantics=semantics, sources=[])
        for row in vertex:
            if row.get('layout') != 'verified':
                entry['sources'].append(f"vertex program at {row['header_offset']}: layout unverified")
                continue
            slots = [e['slot'] for e in row['exports'] if e['semantic'] in semantics]
            if not slots:
                continue
            key = row['header_offset']
            if key not in compiled:
                compiled[key] = vertex_exports(row)
            exports, reason = compiled[key]
            if exports is None:
                entry['sources'].append(f"{row.get('shader_id', key)}: {reason}")
                continue
            params, ir = exports
            for slot in slots:
                channels = params.get(slot)
                described = ('not exported' if channels is None else
                             ', '.join('-' if c is None else ir.describe(c, depth) for c in channels))
                entry['sources'].append(f"{row.get('shader_id', key)} param{slot}: {described}")
        entry['sources'] = sorted(set(entry['sources']))
        result[f'attribute{index}'] = entry
    return result


def normalised_attributes(data):
    """PS attribute indices whose xyz is a normalised vector in every vertex program exporting it.

    Evidence for the experimental adapter: each of the three channels must reach an
    rsqrt in every traced vertex export of the attribute's single semantic. This
    says the interpolant is unit length, not which vector it is.
    """
    headers = shader_headers(data)
    pixel = [h for h in headers if h['type'] == PIXEL]
    vertex = [h for h in headers if h['type'] != PIXEL]
    compiled, found = {}, set()
    count = max((len(p['inputs']) for p in pixel), default=0)
    for index in range(count):
        semantics = {p['inputs'][index]['semantic'] for p in pixel if index < len(p['inputs'])}
        if len(semantics) != 1:
            continue
        rows = [r for r in vertex if any(e['semantic'] in semantics for e in r.get('exports', []))]
        if not rows or any(r.get('layout') != 'verified' for r in rows):
            continue
        ok = True
        for row in rows:
            key = row['header_offset']
            if key not in compiled:
                compiled[key] = vertex_exports(row)
            exports, _ = compiled[key]
            if exports is None:
                ok = False; break
            params, ir = exports
            for slot in [e['slot'] for e in row['exports'] if e['semantic'] in semantics]:
                channels = params.get(slot)
                if not channels or len(channels) < 3 or any(c is None or not any(
                        ir.nodes[j]['op'] == 'rsqrt' for j in ir.reachable([c])) for c in channels[:3]):
                    ok = False
        if ok:
            found.add(index)
    return found


MAX_FLAGS = 6


def _squared_stream(ir, channel):
    """(semantic, component) when some setting of the runtime flags folds the channel to
    v * v for one vertex input v; None otherwise."""
    flags = sorted(j for j in ir.reachable([channel]) if ir.nodes[j].get('kind') == 'runtime_flag')
    if len(flags) > MAX_FLAGS:
        return None
    for bits in range(1 << len(flags)):
        folded = ir.replace([channel], {f: ir.const(float(bits >> k & 1)) for k, f in enumerate(flags)})[0]
        n = ir.nodes[folded]
        if n['op'] == 'mul' and n['args'][0] == n['args'][1]:
            v = ir.nodes[n['args'][0]]
            if v['op'] == 'input' and v.get('kind') == 'vertex_input':
                return v['semantic'], v['channel']
    return None


def squared_stream_channels(data):
    """{(PS attribute index, channel): (vertex semantic, component)} for interpolant channels
    that every vertex program exporting them can pass as a squared vertex stream value.

    Evidence for the experimental adapter only: the runtime flags that select that path
    are per-draw data, so the path is a choice, not a decoded draw state.
    """
    headers = shader_headers(data)
    pixel = [h for h in headers if h['type'] == PIXEL]
    vertex = [h for h in headers if h['type'] != PIXEL]
    compiled, found = {}, {}
    count = max((len(p['inputs']) for p in pixel), default=0)
    for index in range(count):
        semantics = {p['inputs'][index]['semantic'] for p in pixel if index < len(p['inputs'])}
        rows = [r for r in vertex if any(e['semantic'] in semantics for e in r.get('exports', []))]
        if len(semantics) != 1 or not rows:
            continue
        for channel in range(4):
            sources = set()
            for row in rows:
                key = row['header_offset']
                if key not in compiled:
                    compiled[key] = vertex_exports(row)
                exports, _ = compiled[key]
                if exports is None:
                    sources.add(None); break
                params, ir = exports
                for slot in [e['slot'] for e in row['exports'] if e['semantic'] in semantics]:
                    values = params.get(slot)
                    value = values[channel] if values and channel < len(values) else None
                    sources.add(None if value is None else _squared_stream(ir, value))
            if len(sources) == 1 and None not in sources:
                found[(index, channel)] = sources.pop()
    return found


MAX_FORM_TERMS = 256


FLAG = 'runtime_flag@'


def _flag_mul(p, q):
    """Polynomial product where runtime flags are 0/1 (f*f = f)."""
    result = {}
    for a, x in p.items():
        for b, y in q.items():
            flags = sorted({f for f in a + b if f.startswith(FLAG)})
            key = tuple(sorted(flags + [f for f in a + b if not f.startswith(FLAG)]))
            result[key] = result.get(key, 0.) + x * y
    return {k: v for k, v in result.items() if v}


def _flag_only(p):
    return p is not None and all(f.startswith(FLAG) for key in p for f in key)


def linear_form(ir, root, limit=MAX_FORM_TERMS):
    """Polynomial of a vertex expression over vertex inputs, constant-buffer words and
    runtime flags; any other operation (rsqrt, texture fetch, bit logic) is an opaque
    atom named op#node. A select or max on runtime flags alone is expanded exactly
    (c*a + (1-c)*b, a + b - a*b), since flags are 0 or 1. {(factor names...):
    coefficient}, or None past limit terms."""
    from rc2016.shaders.engine_inputs import _add
    memo = {}

    def visit(i):
        if i in memo:
            return memo[i]
        n = ir.nodes[i]
        op, args = n['op'], n.get('args', [])
        p = False
        if op == 'const':
            p = {(): float(n['value'])} if n['value'] else {}
        elif op == 'input':
            p = {(n['name'],): 1.}
        elif op in ('add', 'sub', 'mul', 'neg'):
            parts = [visit(a) for a in args]
            if any(x is None for x in parts):
                p = None
            elif op == 'neg':
                p = {k: -v for k, v in parts[0].items()}
            elif op == 'mul':
                p = _flag_mul(parts[0], parts[1])
            else:
                p = _add(parts[0], parts[1], -1. if op == 'sub' else 1.)
        elif op == 'select' and _flag_only(visit(args[0])):
            c, a, b = (visit(x) for x in args)
            if a is not None and b is not None:
                p = _add(_flag_mul(c, a), _flag_mul(_add({(): 1.}, c, -1.), b))
        elif op == 'max' and _flag_only(visit(args[0])) and _flag_only(visit(args[1])):
            a, b = visit(args[0]), visit(args[1])
            p = _add(_add(a, b), _flag_mul(a, b), -1.)
        elif op == 'unsupported' and n.get('reason') == 'Unresolved scalar memory load':
            # Named by instruction and dword, so rows of one matrix load stay apart.
            at, word = n.get('load_offset'), n.get('word', 0)
            byte = f'?+{4 * word}' if at is None else at + 4 * word
            p = {(f"mem(s{n.get('base')}+{byte})@{n.get('offset')}",): 1.}
        if p is False:
            p = {(f'{op}#{i}',): 1.}
        if p is not None and len(p) > limit:
            p = None
        memo[i] = p
        return p

    return visit(root)


def format_form(form):
    if form is None:
        return 'too many terms'
    if not form:
        return '0'
    terms = []
    for key in sorted(form, key=lambda k: (len(k), k)):
        v = form[key]
        factor = '*'.join(key)
        if not factor:
            terms.append(f'{v:g}')
        elif v == 1:
            terms.append(factor)
        elif v == -1:
            terms.append('-' + factor)
        else:
            terms.append(f'{v:g}*{factor}')
    return ' + '.join(terms).replace('+ -', '- ')


def _normalised_parts(ir, node):
    """(vector component, rsqrt node) for mul(rsqrt(s), v) in either order; (node, None) otherwise."""
    n = ir.nodes[node]
    if n['op'] == 'mul':
        a, b = n['args']
        if ir.nodes[a]['op'] == 'rsqrt':
            return b, a
        if ir.nodes[b]['op'] == 'rsqrt':
            return a, b
    return node, None


def vector_identity(ir, channels, max_flags=MAX_FLAGS):
    """Per setting of the reachable runtime flags, the linear forms of a vector export.

    [(flag setting, [(component form, normalised)...], {atom: description})], with
    settings that give identical forms merged. A component is reported as the vector
    before its rsqrt normalisation when the export is mul(rsqrt(s), v). This is the
    evidence for which vertex stream and which constant-buffer matrix make the
    vector; nothing here names the vector.
    """
    flags = sorted(j for j in ir.reachable([c for c in channels if c is not None])
                   if ir.nodes[j].get('kind') == 'runtime_flag')
    if len(flags) > max_flags:
        return _symbolic_identity(ir, channels)
    groups = {}
    for bits in range(1 << len(flags)):
        setting = ' '.join(f"{ir.nodes[f]['name'].split('@')[1]}={bits >> k & 1}" for k, f in enumerate(flags))
        folded = ir.replace([c for c in channels if c is not None],
                            {f: ir.const(float(bits >> k & 1)) for k, f in enumerate(flags)})
        parts, atoms = [], {}
        for node in folded:
            vector, norm = _normalised_parts(ir, node)
            form = linear_form(ir, vector)
            parts.append((format_form(form), norm is not None))
            for key in (form or {}):
                for factor in key:
                    if '#' in factor:
                        atom = ir.nodes[int(factor.split('#')[1])]
                        atoms.setdefault(factor, atom['reason'] if atom['op'] == 'unsupported' and atom.get('reason')
                                         else ir.describe(int(factor.split('#')[1]), 3))
            if norm is not None:
                atoms.setdefault(f'rsqrt#{norm}', 'rsqrt(' + format_form(linear_form(ir, ir.nodes[norm]['args'][0])) + ')')
        key = tuple(parts)
        if key in groups:
            groups[key][0].append(setting or 'no flags')
        else:
            groups[key] = ([setting or 'no flags'], atoms)
    return [(settings, list(key), atoms) for key, (settings, atoms) in groups.items()]


REPORT_FLAGS = 10


SYMBOLIC_TERMS = 4096
SYMBOLIC_FLAGS = 12


def _symbolic_identity(ir, channels, max_flags=SYMBOLIC_FLAGS):
    """vector_identity for many flags: expand flags symbolically, then specialise per
    setting of the flags that remain in the polynomial (others sit inside atoms).
    None when that is still more than max_flags flags or too many terms."""
    forms = [linear_form(ir, c, SYMBOLIC_TERMS) if c is not None else {} for c in channels]
    if any(f is None for f in forms):
        return None
    names = sorted({f for form in forms for key in form for f in key if f.startswith(FLAG)},
                   key=lambda f: int(f.split('@')[1]))
    if len(names) > max_flags:
        return None
    groups = {}
    for bits in range(1 << len(names)):
        ones = {f for k, f in enumerate(names) if bits >> k & 1}
        parts = []
        for form in forms:
            special = {}
            for key, v in form.items():
                flags = {f for f in key if f.startswith(FLAG)}
                if flags <= ones:
                    rest = tuple(f for f in key if not f.startswith(FLAG))
                    special[rest] = special.get(rest, 0.) + v
            parts.append((format_form({k: v for k, v in special.items() if v}), False))
        setting = ' '.join(f"{f.split('@')[1]}={bits >> k & 1}" for k, f in enumerate(names)) or 'no flags'
        groups.setdefault(tuple(parts), []).append(setting)
    atoms = {}
    for form in forms:
        for key in form:
            for factor in key:
                if '#' in factor:
                    atoms.setdefault(factor, ir.describe(int(factor.split('#')[1]), 3))
    note = 'flags inside atoms are not enumerated'
    return [(settings, list(parts), dict(atoms, **{'(symbolic)': note})) for parts, settings in groups.items()]


def attribute_report(data, attributes=None):
    """Lines describing every vertex program's inputs and, per PS attribute (all, or
    the indices given), each exporting program's vector forms, plus the clip
    position export for comparison. Unresolved loads print as mem(s<base>+<byte>)@<instruction offset>."""
    headers = shader_headers(data)
    pixel = [h for h in headers if h['type'] == PIXEL]
    vertex = [h for h in headers if h['type'] != PIXEL]
    lines, compiled = [], {}
    for row in vertex:
        name = row.get('shader_id', row['header_offset'])
        if row.get('layout') != 'verified':
            lines.append(f'vertex {name}: layout unverified')
            continue
        inputs = ', '.join(f"semantic{e['semantic']}[{e['size']}]" for e in row['inputs'])
        exports = ', '.join(f"semantic{e['semantic']}->param{e['slot']}" for e in row['exports'])
        lines.append(f'vertex {name}: inputs {inputs}; exports {exports}')
        compiled[row['header_offset']] = vertex_exports(row, position=True)
        exported, reason = compiled[row['header_offset']]
        if exported is None:
            lines.append(f'  trace failed: {reason}')
            continue
        params, ir = exported
        if params.get('position'):
            lines.extend(_identity_lines(ir, params['position'], '  clip position'))
    count = max((len(p['inputs']) for p in pixel), default=0)
    for index in range(count):
        if attributes is not None and index not in attributes:
            continue
        semantics = sorted({p['inputs'][index]['semantic'] for p in pixel if index < len(p['inputs'])})
        lines.append(f'attribute{index} (PS semantics {semantics})')
        for row in vertex:
            exported, _ = compiled.get(row['header_offset'], (None, None))
            if exported is None:
                continue
            params, ir = exported
            for slot in [e['slot'] for e in row['exports'] if e['semantic'] in semantics]:
                channels = params.get(slot)
                label = f"  {row.get('shader_id', row['header_offset'])} param{slot}"
                if not channels:
                    lines.append(label + ': not exported')
                else:
                    lines.extend(_identity_lines(ir, channels, label))
    return lines


def _identity_lines(ir, channels, label):
    groups = vector_identity(ir, channels, REPORT_FLAGS)
    if groups is None:
        return [f'{label}: more than {REPORT_FLAGS} runtime flags, and more than {SYMBOLIC_FLAGS} or {SYMBOLIC_TERMS} terms when expanded']
    lines, zero = [], []
    for settings, parts, atoms in groups:
        if all(form == '0' for form, _ in parts):
            zero.extend(settings)
            continue
        lines.append(f"{label} [{'; '.join(settings[:4])}{' ...' if len(settings) > 4 else ''}]:")
        for c, (form, normalised) in enumerate(parts):
            lines.append(f"    {'xyzw'[c]}{' (before rsqrt)' if normalised else ''} = {form[:600]}")
        for atom, text in sorted(atoms.items()):
            lines.append(f'    where {atom} = {text[:300]}')
    if zero:
        lines.append(f'{label}: zero on {len(zero)} flag settings')
    return lines
