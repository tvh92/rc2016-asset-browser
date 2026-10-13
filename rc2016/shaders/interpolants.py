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


def vertex_exports(row):
    """{parameter slot: [IR node per channel]} and the DAG, or (None, reason)."""
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
