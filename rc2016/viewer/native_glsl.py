"""Portable native surface DAG -> GLSL 1.20, with the Blender emitter's UV convention.

The source pass contract remains bounded by shaders.bindings. This frontend does
not establish runtime draw selection or game equivalence.
"""
from __future__ import annotations

import hashlib
import math

from rc2016.shaders.bindings import PORTABLE_OPS
from rc2016.shaders.ir import DAG, compute

EMITTER_VERSION = 3
# Reserve vertex bone matrices (3) and shared environment lighting (6).
TEXTURE_UNITS = (0, 1, 2, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15)


def _literal(value):
    if not math.isfinite(value):
        raise ValueError('Non-finite native shader constant')
    text = format(float(value), '.17g')
    return text if '.' in text or 'e' in text else text + '.0'


def emit_surface(translation, srgb=None, *, experimental=False):
    """Return declarations, body and sampler descriptors; reject unsupported inputs.

    srgb is keyed by native API and records the hardware image interpretation.
    RGB arrives linear from the sampler; alpha never passes through a transfer
    function. The uploader must decode sRGB before filtering. Sample identity includes
    sample id, API and coordinates, exactly as shaders.nodes._frontend.
    """
    preview = experimental and translation.get('status') == 'experimental'
    if preview:
        from rc2016.viewer.experimental_materials import valid_contract
        if not valid_contract(translation):
            raise ValueError('Unknown experimental surface contract')
    if translation.get('status') != 'ready' and not preview:
        raise ValueError('Native surface requires a ready translation')
    # Viewports upload V-unflipped UV0 and top-down image rows. Relative to
    # nodes.py, both the input and each sample have already changed basis.
    srgb = srgb or {}
    bindings = {r['api']: r for r in translation['texture_bindings']}
    if len(bindings) > len(TEXTURE_UNITS):
        raise ValueError('Native surface exceeds available texture units')
    samplers = [dict(binding, uniform=f'uNativeTex{api}', unit=TEXTURE_UNITS[i],
                     srgb=bool(srgb.get(api))) for i, (api, binding) in enumerate(sorted(bindings.items()))]
    declarations = '\n'.join(f"uniform sampler2D {r['uniform']};" for r in samplers)
    lines, names, samples, visiting = [], {}, {}, set()
    simple = {'add': '+', 'sub': '-', 'mul': '*'}
    comparisons = {'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>=', 'eq': '==', 'ne': '!='}

    def visit(i):
        if i in names:
            return names[i]
        if i in visiting or not isinstance(i, int) or not 0 <= i < len(translation['nodes']):
            raise ValueError('Invalid or cyclic native IR reference')
        visiting.add(i)
        n = translation['nodes'][i]; op = n['op']; args = n['args']
        if op not in PORTABLE_OPS:
            raise ValueError('Unsupported native operation ' + op)
        a = [visit(j) for j in args]
        if op == 'const':
            expr = _literal(n['value'])
        elif op == 'input':
            if preview and n.get('kind') in ('preview_position','preview_normal') and n.get('channel') in (0,1,2):
                expr = ('vTintWorldPos' if n['kind'] == 'preview_position' else 'vTintWorldNrm') + '.' + 'xyz'[n['channel']]
            elif preview and n.get('kind') == 'preview_front_face':
                expr = '(gl_FrontFacing ? 1.0 : 0.0)'
            elif n.get('kind') != 'attribute' or n.get('attribute') != 0 or n.get('channel') not in (0, 1):
                raise ValueError('Only UV0.xy native inputs are supported')
            else:
                expr = 'vUV.x' if n['channel'] == 0 else 'vUV.y'
        elif op == 'texture':
            if n['api'] not in bindings or len(a) != 2 or n['channel'] not in range(4):
                raise ValueError('Unbound or non-2D native texture sample')
            key = (n['sample'], n['api'], tuple(args))
            if key not in samples:
                sample = f'nativeSample{len(samples)}'; samples[key] = sample
                lines.append(f'    vec4 {sample} = texture2D(uNativeTex{n["api"]}, vec2({a[0]}, {a[1]}));')
            expr = samples[key] + '.' + 'rgba'[n['channel']]
        elif op in simple:
            expr = f'({a[0]} {simple[op]} {a[1]})'
        elif op in comparisons:
            expr = f'({a[0]} {comparisons[op]} {a[1]} ? 1.0 : 0.0)'
        elif op == 'select':
            expr = f'({a[0]} * ({a[1]} - {a[2]}) + {a[2]})'
        elif op == 'div':
            expr = f'({a[1]} == 0.0 ? 0.0 : {a[0]} / {a[1]})'
        elif op == 'neg':
            expr = f'(-{a[0]})'
        elif op in ('min', 'max', 'pow'):
            expr = f'{"nativePower" if op == "pow" else op}({a[0]}, {a[1]})'
        elif op == 'rcp':
            expr = f'nativePower({a[0]}, -1.0)'
        elif op == 'rsqrt':
            expr = f'nativePower({a[0]}, -0.5)'
        elif op == 'sqrt':
            expr = f'sqrt(max({a[0]}, 0.0))'
        elif op == 'log2':
            expr = f'({a[0]} > 0.0 ? log2({a[0]}) : 0.0)'
        elif op == 'exp2':
            expr = f'exp2({a[0]})'
        elif op == 'sat':
            expr = f'clamp({a[0]}, 0.0, 1.0)'
        else:
            expr = f'{op}({a[0]})'
        name = f'native{i}'; lines.append(f'    float {name} = {expr};')
        names[i] = name; visiting.remove(i)
        return name

    outputs = translation['outputs']
    for label in ('albedo', 'F0'):
        values = [visit(i) for i in outputs[label]]
        lines.append(f'    vec3 {label} = vec3({", ".join(values)});')
    for label in ('gloss', 'emission'):
        value = visit(outputs[label]); lines.append(f'    float {label} = {value};')
    if translation.get('normal'):
        xyz = [visit(i) for i in translation['normal']['xyz']]
        lines.append(f'    vec3 nativeNormal = nativeNormalize(vec3({", ".join(xyz)}));')
        lines.append('    N = nativeTangentNormal(N, nativeNormal);')
    key = (EMITTER_VERSION, translation['translation_sha256'], tuple(sorted(srgb.items())))
    return dict(declarations=declarations, body='\n'.join(lines), samplers=samplers, key=key)


HELPERS = """
// Match ShaderNodeMath POWER, including signed integer powers and zero guards.
float nativePower(float x, float y) {
    if (x == 0.0) return y == 0.0 ? 1.0 : 0.0;
    if (x < 0.0) {
        float integer = floor(y);
        if (y != integer) return 0.0;
        return pow(-x, y) * (mod(abs(integer), 2.0) == 0.0 ? 1.0 : -1.0);
    }
    return pow(x, y);
}
vec3 nativeNormalize(vec3 v) {
    float size = dot(v, v);
    return size > 0.0 ? v * inversesqrt(size) : vec3(0.0);
}
vec3 nativeTangentNormal(vec3 N, vec3 tangentNormal) {
    vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos);
    vec2 du1 = dFdx(vUV), du2 = dFdy(vUV);
    vec3 p2 = cross(dp2, N), p1 = cross(N, dp1);
    vec3 T = p2 * du1.x + p1 * du2.x, B = p2 * du1.y + p1 * du2.y;
    // Relative to Blender UV0, game-space V and top-down rows reverse the bitangent.
    // Blender Normal Map normalizes its tangent basis independently.
    if (dot(T,T) == 0.0 || dot(B,B) == 0.0) return N;
    return nativeNormalize(normalize(T)*tangentNormal.x - normalize(B)*tangentNormal.y + N*tangentNormal.z);
}
"""


def fragment_source(translation, srgb=None, *, experimental=False):
    from rc2016.viewer.shading import FRAGMENT
    surface = emit_surface(translation, srgb, experimental=experimental)
    # Reuse all shared lighting, tonemap and BRDF helpers. Unused hand-rule code
    # is eliminated by the GLSL compiler; only the material-value source changes.
    prefix = FRAGMENT.split('void main() {', 1)[0]
    source = prefix + surface['declarations'] + HELPERS + '\nvoid main() {\n'
    source += '    vec3 N = normalize(vNrm) * (gl_FrontFacing ? 1.0 : -1.0); sceneCaptureNormal=N;\n'
    source += '    vec3 V = normalize(-vPos);\n' + surface['body'] + '\n'
    source += '    vec3 color = deferredDiffuse(N, albedo) + deferredSpecular(N, V, F0, gloss);\n'
    source += '    sceneOutput(vec4(display(color + albedo * emission), 1.0));\n}\n'
    surface['source'] = source
    # Includes current shared shader helpers, so lighting edits invalidate programs.
    surface['key'] += (hashlib.sha256(source.encode()).hexdigest(),)
    return surface


def evaluate_surface(translation, uv, sample, *, preview_inputs=None):
    """CPU oracle for the emitted frontend; sample(api, viewer_uv) returns linear RGBA.

    Normal is the normalized tangent-space slice, or None for geometry normal.
    uv is viewer UV0 in game space. The viewer has already undone the decoder's
    Blender V flip; top-down GL uploads undo the per-sample Blender flip too.
    """
    values, samples = {}, {}
    def visit(i):
        if i in values: return values[i]
        n = translation['nodes'][i]; op = n['op']; a = [visit(j) for j in n['args']]
        if op == 'const': value = n['value']
        elif op == 'input':
            if n.get('kind') == 'attribute' and n.get('attribute') == 0:
                value = uv[n['channel']]
            elif preview_inputs is not None and translation.get('status') == 'experimental':
                value = preview_inputs[n['name']]
            else:
                raise ValueError('Unbound frontend input '+n['name'])
        elif op == 'texture':
            key = (n['sample'], n['api'], tuple(n['args']))
            if key not in samples: samples[key] = sample(n['api'], (a[0], a[1]))
            value = samples[key][n['channel']]
        elif op == 'select': value = a[0] * (a[1] - a[2]) + a[2]
        elif op == 'div': value = a[0] / a[1] if a[1] else 0.
        elif op in ('pow', 'rcp', 'rsqrt'):
            x, y = (a if op == 'pow' else (a[0], -1. if op == 'rcp' else -.5))
            value = 0. if (x == 0 and y < 0) or (x < 0 and y != math.floor(y)) else x ** y
        elif op == 'sqrt': value = math.sqrt(max(a[0], 0.))
        elif op == 'log2': value = math.log2(a[0]) if a[0] > 0 else 0.
        else: value = compute(op, a)
        values[i] = value; return value
    o = translation['outputs']
    result = {label: [visit(i) for i in o[label]] for label in ('albedo', 'F0')}
    result.update((label, visit(o[label])) for label in ('gloss', 'emission'))
    normal = translation.get('normal')
    xyz = [visit(i) for i in normal['xyz']] if normal else None
    length = math.sqrt(sum(v*v for v in xyz)) if xyz else 0.
    result['normal'] = ([v / length for v in xyz] if length else [0., 0., 0.]) if normal else None
    return result


def prepare_material_translation(archive, path):
    """Material-bound receipt, cached on the archive; malformed shaders fail closed."""
    from rc2016.formats.archive import asset_data, asset_references
    from rc2016.formats.materials import extract_material_parameters
    from rc2016.shaders.bindings import prepare_translation
    from rc2016.viewer.textures import _archive_cache, _remember
    cache = _archive_cache(archive, '_native_translation_cache')
    found = cache.get(path)
    if found is None:
        def entry(p, kind):
            ids = [i for i in archive.resolve(p) if archive.entries[i]['type'] == kind]
            if len(ids) != 1: raise ValueError('Ambiguous source ' + p)
            return ids[0]
        try:
            material_id = entry(path, 'material')
            material = asset_data(archive, material_id)
            graphs = list(dict.fromkeys(r['path'] for r in asset_references(archive, material_id) if r['path'].endswith('.materialgraph')))
            result = prepare_translation([asset_data(archive, entry(g, 'materialgraph')) for g in graphs], material,
                                         extract_material_parameters(material))
        except (ValueError, KeyError, IndexError, OSError) as exc:
            result = dict(status='unsupported', diagnostics=[f'{type(exc).__name__}: {exc}'], game_equivalence=False)
        found = _remember(cache, path, result)
    return found


def selected_translation(archive, path, paths=None):
    """Fully ported exact viewer rules win; native replaces generic/family guesses."""
    from rc2016.formats.links import EXACT, rule_coverage
    from rc2016.formats.materials import _viewer_rule_wins_translation, viewer_graph_rule
    from rc2016.viewer.textures import material_texture_paths
    from rc2016.formats.archive import asset_references
    paths = paths if paths is not None else material_texture_paths(archive, path)
    if paths.get('forward'): return None
    ids = [i for i in archive.resolve(path) if archive.entries[i]['type'] == 'material']
    if len(ids) != 1: return None
    graphs = [r['path'] for r in asset_references(archive, ids[0]) if r['path'].endswith('.materialgraph')]
    if rule_coverage(graphs)[0] == EXACT:
        bound = [v for v in paths.values() if isinstance(v, str) and v.endswith('.texture')]
        rule = viewer_graph_rule(archive, path, bound)
        if rule.get('applies') and _viewer_rule_wins_translation(True, rule): return None
    translation = prepare_material_translation(archive, path)
    if translation.get('status') != 'ready': return None
    # Validate portable GLSL shape before advertising the route in coverage/UI.
    emit_surface(translation)
    from rc2016.viewer.textures import texture_header, GL_COMPRESSED, GL_UNCOMPRESSED
    for binding in translation['texture_bindings']:
        header = texture_header(archive, binding['path'])   # memoized on the archive
        if max(1, header.get('array_size', 1)) != 1 or header['format'] not in GL_COMPRESSED | GL_UNCOMPRESSED:
            return None
    return translation
