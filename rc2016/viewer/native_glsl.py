"""Portable native surface DAG -> GLSL 1.20, with the Blender emitter's UV convention.

The source pass contract remains bounded by shaders.bindings. This frontend does
not establish runtime draw selection or game equivalence.
"""
from __future__ import annotations

import hashlib
import math

from rc2016.shaders.bindings import PORTABLE_OPS
from rc2016.shaders.ir import DAG, compute
from rc2016.viewer.textures import TextureDiskCache

EMITTER_VERSION = 5
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
            elif preview and n.get('kind') == 'preview_vertex_alpha_squared':
                expr = 'vRockAlpha'  # model colour alpha squared in the vertex stage; 1 on level instances
            elif n.get('kind') == 'front_facing' or (preview and n.get('kind') == 'preview_front_face'):
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
            elif n.get('kind') == 'front_facing':
                value = (preview_inputs or {}).get('front_facing', 1.)
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


TRANSLATION_DISK_CACHE_VERSION = 1
TRANSLATION_DISK_CACHE_BYTES = 256 * 1024 ** 2


def _translation_code_sha256():
    """The translator's own sources: any change to them invalidates every cached translation."""
    from pathlib import Path
    package = Path(__file__).resolve().parents[1]
    files = sorted((package / 'shaders').glob('*.py')) + [
        package / 'formats' / 'materials.py', package / 'formats' / 'container.py', package / 'common.py',
        package / 'viewer' / 'native_glsl.py', package.parent / 'third_party' / 'opengnm' / 'gcn_types.h']
    try:
        return hashlib.sha256(b''.join(f.name.encode() + f.read_bytes() for f in files)).hexdigest()
    except OSError:   # frozen executable without sources: no disk cache
        return None


_TRANSLATION_CODE_SHA256 = _translation_code_sha256()


def _to_json(value):
    """Exact JSON form: tuples and dicts with non-string (or tag-like) keys are tagged."""
    if isinstance(value, tuple):
        return {'__tuple__': [_to_json(v) for v in value]}
    if isinstance(value, list):
        return [_to_json(v) for v in value]
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value) and not (len(value) == 1 and next(iter(value)) in ('__tuple__', '__items__')):
            return {k: _to_json(v) for k, v in value.items()}
        return {'__items__': [[_to_json(k), _to_json(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f'{type(value).__name__} is not stored in the translation cache')


def _from_json(value):
    if isinstance(value, list):
        return [_from_json(v) for v in value]
    if isinstance(value, dict):
        if len(value) == 1 and '__tuple__' in value:
            return tuple(_from_json(v) for v in value['__tuple__'])
        if len(value) == 1 and '__items__' in value:
            return {_from_json(k): _from_json(v) for k, v in value['__items__']}
        return {k: _from_json(v) for k, v in value.items()}
    return value


class TranslationDiskCache(TextureDiskCache):
    """prepare_translation results under ``folder``, one JSON file each, across sessions.

    Keyed by the material bytes, its graph bytes and the translator source hash, so a changed
    asset or translator never reuses an old result. Results that do not survive a JSON round trip
    unchanged are not stored. Files are written to a temporary name and renamed into place; a
    file that fails a check is ignored and rewritten. Eviction is least recently used, as for
    TextureDiskCache. Tuples and non-string dict keys are tagged so results load back exactly.
    """
    SUFFIX = '.json'

    def __init__(self, folder, limit=TRANSLATION_DISK_CACHE_BYTES):
        super().__init__(folder, limit)

    def key(self, material_data, graph_datas):
        if _TRANSLATION_CODE_SHA256 is None:
            return None
        return hashlib.sha256(repr((TRANSLATION_DISK_CACHE_VERSION, _TRANSLATION_CODE_SHA256,
                                    hashlib.sha256(material_data).hexdigest(),
                                    [hashlib.sha256(g).hexdigest() for g in graph_datas])).encode()).hexdigest()

    def get(self, key):
        import json, os
        path = self._file(key)
        try:
            with open(path, 'rb') as f:
                stored = json.loads(f.read().decode('utf-8'))
            if stored.get('key') != key:
                return None
            result = _from_json(stored['result'])
            if not isinstance(result, dict):
                return None
            os.utime(path)   # recency for eviction
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        return result

    def put(self, key, result):
        import json, os, uuid
        try:
            text = json.dumps(dict(key=key, result=_to_json(result)), allow_nan=False)
            if _from_json(json.loads(text)['result']) != result:
                return
        except (TypeError, ValueError):
            return
        blob = text.encode('utf-8')
        path = self._file(key)
        temp = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp, 'wb') as f:
                f.write(blob)
            os.replace(temp, path)
        except OSError:
            try: temp.unlink()
            except OSError: pass
            return
        self._grow(len(blob))


def prepare_material_translation(archive, path):
    """Material-bound receipt, cached on the archive and, with a TranslationDiskCache on
    ``archive.translation_disk_cache``, on disk across sessions; malformed shaders fail closed."""
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
        disk, key = getattr(archive, 'translation_disk_cache', None), None
        try:
            material_id = entry(path, 'material')
            material = asset_data(archive, material_id)
            graphs = list(dict.fromkeys(r['path'] for r in asset_references(archive, material_id) if r['path'].endswith('.materialgraph')))
            graph_datas = [bytes(asset_data(archive, entry(g, 'materialgraph'))) for g in graphs]
            key = disk.key(bytes(material), graph_datas) if disk is not None else None
            result = disk.get(key) if key else None
            if result is None:
                result = prepare_translation(graph_datas, material, extract_material_parameters(material))
                if key:
                    disk.put(key, result)
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
