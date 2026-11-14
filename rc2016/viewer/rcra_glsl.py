"""Rift Apart translated materials in the viewer: recipe DAGs (rc2016.games.rcra.surface) -> GLSL 1.20 fragment programs.

A receipt (rc2016.games.rcra.viewer_materials) carries a material's G-buffer surface recipe, its forward recipe when the game
draws it blended, the blend mode and the layout of each texture binding. Every recipe node becomes one GLSL statement, so
the viewer runs the game's own arithmetic, as the Blender emitter does (rc2016/export/rcra_blender.py).

Inputs, in the viewer's varyings:
- TEXCOORD.xy / TEXCOORD1.xy: vUV / vUV1, game UVs (the viewer uploads V unflipped and image rows top first).
- TEXCOORD2.xyz: the world normal vTintWorldNrm (native Y-up), not turned on back faces, as the game's interpolant.
- TEXCOORD3.xyz and .w: the vertex tangent frame of native_glsl.FRAME_HELPERS (the UV-derivative frame while the Rift Apart
  tangent stream is not decoded), bitangent along the game's -v as in the Blender export.
- TEXCOORD4: vertex colour, 1 (the viewer does not draw the colour stream).
- SV_IsFrontFace: gl_FrontFacing. VIEW.xyz: vTintViewOffset (camera to point); CAMERA.xyz: vTintWorldPos - vTintViewOffset.

Textures bind as uNativeTex<binding> (the native path's uniforms, so the viewport's binding code serves both games). A
binding's layout is 'slice' (one 2D texture), 'array' (a padded atlas of every slice, picked by round(z), clamped) or
'volume' (a padded atlas, trilinear in depth with wrap), as rc2016.games.rcra.blender_export writes them for Blender.

Lighting is the Rift Apart deferred BRDF on the viewer's review rig (docs/rift-apart.md): GGX with alpha = (0.9725 - 0.7514
gloss)^2, the Smith joint visibility approximation, Schlick with F90 = 1, Lambert; occlusion on ambient light only.
"""
from __future__ import annotations

import hashlib
import json
import math

EMITTER_VERSION = 1
PASS_UNIFORM = 'uRcraPass'   # forward programs: 0 the multiply pass (background factor), 1 the colour pass
GEOMETRY = {'TEXCOORD.x': 'vUV.x', 'TEXCOORD.y': 'vUV.y', 'TEXCOORD1.x': 'vUV1.x', 'TEXCOORD1.y': 'vUV1.y',
            'TEXCOORD2.x': 'vTintWorldNrm.x', 'TEXCOORD2.y': 'vTintWorldNrm.y', 'TEXCOORD2.z': 'vTintWorldNrm.z',
            'TEXCOORD3.x': 'rcraTangent.x', 'TEXCOORD3.y': 'rcraTangent.y', 'TEXCOORD3.z': 'rcraTangent.z',
            'TEXCOORD3.w': 'rcraHandedness', 'TEXCOORD4.x': '1.0', 'TEXCOORD4.y': '1.0', 'TEXCOORD4.z': '1.0',
            'TEXCOORD4.w': '1.0', 'SV_IsFrontFace': '(gl_FrontFacing ? 1.0 : 0.0)',
            'VIEW.x': 'vTintViewOffset.x', 'VIEW.y': 'vTintViewOffset.y', 'VIEW.z': 'vTintViewOffset.z',
            'CAMERA.x': 'rcraCamera.x', 'CAMERA.y': 'rcraCamera.y', 'CAMERA.z': 'rcraCamera.z'}
LIMITATION = ('Viewer: vertex colour reads 1, the tangent frame comes from UV derivatives (the stored tangent is not decoded), '
              'and the review rig and environment stand in for the game\'s lights.')


def _literal(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Non-finite recipe constant')
    text = format(value, '.9g')
    return text if '.' in text or 'e' in text or 'n' in text else text + '.0'


def uniform(binding):
    return 'uNativeTex' + binding


class _Emitter:
    def __init__(self, recipe, layouts, prefix):
        self.recipe, self.layouts, self.prefix = recipe, layouts, prefix
        self.lines, self.names, self.samples = [], {}, {}

    def value(self, i):
        if i in self.names:
            return self.names[i]
        n = self.recipe['nodes'][i]
        op, args = n['op'], n['args']
        if op == 'const':
            expr = ('1.0' if n['value'] else '0.0') if n.get('type') == 'b' else _literal(n['value'])
            self.names[i] = expr
            return expr
        if op == 'input':
            if n['name'] not in GEOMETRY:
                raise ValueError('Recipe input not available in the viewer: ' + n['name'])
            self.names[i] = GEOMETRY[n['name']]
            return self.names[i]
        if op == 'channel':
            expr = self.sample(args[0]) + '.' + 'rgba'[n['index']]
        else:
            a = [self.value(x) for x in args]
            expr = self.operation(op, a)
        name = '%s%d' % (self.prefix, i)
        self.lines.append('    float %s = %s;' % (name, expr))
        self.names[i] = name
        return name

    @staticmethod
    def operation(op, a):
        binary = {'add': '+', 'mul': '*', 'div': '/'}
        if op in binary:
            return '(%s %s %s)' % (a[0], binary[op], a[1])
        unary = {'abs': 'abs', 'sqrt': 'sqrt', 'rsq': 'inversesqrt', 'log2': 'log2', 'exp2': 'exp2', 'sin': 'sin',
                 'cos': 'cos', 'round_ni': 'floor', 'round_pi': 'ceil', 'round_ne': 'rcraRoundEven', 'round_z': 'rcraTrunc'}
        if op in unary:
            return '%s(%s)' % (unary[op], a[0])
        if op in ('min', 'max'):
            return '%s(%s, %s)' % (op, a[0], a[1])
        if op == 'neg':
            return '(-%s)' % a[0]
        if op == 'sat':
            return 'clamp(%s, 0.0, 1.0)' % a[0]
        if op == 'rcp':
            return '(1.0 / %s)' % a[0]
        if op == 'frc':
            return '(%s - floor(%s))' % (a[0], a[0])
        if op in ('eq', 'ne', 'lt', 'ge'):
            return '(%s %s %s ? 1.0 : 0.0)' % (a[0], {'eq': '==', 'ne': '!=', 'lt': '<', 'ge': '>='}[op], a[1])
        if op == 'b_and':
            return '(%s * %s)' % (a[0], a[1])
        if op == 'b_or':
            return 'max(%s, %s)' % (a[0], a[1])
        if op == 'b_not':
            return '(1.0 - %s)' % a[0]
        if op == 'select':
            return '(%s != 0.0 ? %s : %s)' % (a[0], a[1], a[2])
        if op == 'ddx':
            return 'dFdx(%s)' % a[0]
        if op == 'ddy':
            return '(-dFdy(%s))' % a[0]   # D3D screen y runs down, GL's up
        raise ValueError('Recipe operation not emitted: ' + op)

    def sample(self, i):
        if i in self.samples:
            return self.samples[i]
        n = self.recipe['nodes'][i]
        binding = n['binding']
        layout = self.layouts.get(binding)
        if layout is None:
            raise ValueError('No texture for ' + binding)
        u, v = self.value(n['args'][0]), self.value(n['args'][1])
        name = '%ss%d' % (self.prefix, i)
        sampler = uniform(binding)
        if layout['kind'] == 'slice':
            expr = 'texture2D(%s, vec2(%s, %s))' % (sampler, u, v)
        else:
            z = self.value(n['args'][2])
            grid = 'vec4(%s, %s, %s, %s)' % tuple(_literal(x) for x in (layout['columns'], layout['width'], layout['height'],
                                                                        -(-layout['depth'] // layout['columns'])))
            helper = 'rcraVolume' if layout['kind'] == 'volume' else 'rcraArray'
            expr = '%s(%s, vec2(%s, %s), %s, %s, %s)' % (helper, sampler, u, v, z, _literal(layout['depth']), grid)
        self.lines.append('    vec4 %s = %s;' % (name, expr))
        self.samples[i] = name
        return name

    def term(self, name, default='0.0'):
        terms = self.recipe['terms']
        return self.value(terms[name]) if name in terms else default


HELPERS = """
float rcraRoundEven(float x) { float r = floor(x + 0.5); return (r - x == 0.5 && mod(r, 2.0) != 0.0) ? r - 1.0 : r; }
float rcraTrunc(float x) { return x < 0.0 ? ceil(x) : floor(x); }
// Padded slice atlas (rc2016.games.rcra.blender_export.volume_atlas): g = (columns, tile width, tile height, rows); each tile
// carries a one-texel border copied from its opposite edge, so a bilinear lookup inside it wraps as the texture does.
vec4 rcraTile(sampler2D t, vec2 uv, float k, vec4 g) {
    float row = floor(k / g.x), col = k - row * g.x;
    vec2 f = uv - floor(uv);
    vec2 p = vec2(col * (g.y + 2.0) + 1.0 + f.x * g.y, row * (g.z + 2.0) + 1.0 + f.y * g.z);
    return texture2D(t, p / vec2(g.x * (g.y + 2.0), g.w * (g.z + 2.0)));
}
vec4 rcraArray(sampler2D t, vec2 uv, float z, float n, vec4 g) { return rcraTile(t, uv, clamp(floor(z + 0.5), 0.0, n - 1.0), g); }
vec4 rcraVolume(sampler2D t, vec2 uv, float z, float n, vec4 g) {
    float s = z * n - 0.5, k = floor(s);
    return mix(rcraTile(t, uv, mod(k, n), g), rcraTile(t, uv, mod(k + 1.0, n), g), s - k);
}
// Rift Apart deferred BRDF (class-0 apply program) on the review rig.
float rcraVisibility(float NdL, float NdV, float a) {
    return 0.5 / max(NdL * (NdV * (1.0 - a) + a) + NdV * (NdL * (1.0 - a) + a), 1e-6);
}
vec3 rcraLobe(vec3 N, vec3 V, vec3 L, vec3 F0, float a) {
    vec3 H = normalize(L + V);
    float NdL = max(dot(N, L), 0.0), NdV = max(dot(N, V), 1e-4), NdH = max(dot(N, H), 0.0);
    float a2 = a * a, d = NdH * NdH * (a2 - 1.0) + 1.0;
    vec3 F = F0 + (1.0 - F0) * pow(1.0 - max(dot(V, H), 0.0), 5.0);
    return a2 / (PI * d * d) * rcraVisibility(NdL, NdV, a) * F * NdL;
}
vec3 rcraDiffuse(vec3 N, vec3 albedo, float occlusion) {
    if (!uLightingEnabled) return albedo;
    vec3 previewL; float previewW = previewLightWeight(previewL);
    vec3 preview = previewW > 0.0 ? uPreviewLightColour * previewW * max(dot(N, previewL), 0.0) : vec3(0.0);
    return albedo * ((max(dot(N, rigKey()), 0.0) * keyColour() + max(dot(N, rigFill()), 0.0) * fillColour()
        + ambientLight(N) * occlusion) / PI * diffuseScale() + preview);
}
vec3 rcraSpecular(vec3 N, vec3 V, vec3 F0, float gloss) {
    if (!uLightingEnabled) return vec3(0.0);
    float r = 0.9725 - 0.7514 * clamp(gloss, 0.0, 1.0), a = r * r;
    vec3 previewL; float previewW = previewLightWeight(previewL);
    vec3 S = rcraLobe(N, V, rigKey(), F0, a) * keyColour() + rcraLobe(N, V, rigFill(), F0, a) * fillColour()
        + envSpecular(N, V, F0, vec3(1.0), a, ambientLight(N));
    if (previewW > 0.0) S += rcraLobe(N, V, previewL, F0, a) * uPreviewLightColour * previewW;
    return S;
}
"""


def _prefix():
    from rc2016.viewer.shading import FRAGMENT
    prefix = FRAGMENT.split('void main() {', 1)[0]
    extra = []
    for declaration in ('varying vec2 vUV1;', 'varying mat3 vNativeToView;'):
        if declaration.split()[-1].rstrip(';') not in prefix:
            extra.append(declaration)
    return prefix + '\n'.join(extra) + '\n'


def _frame_lines():
    return ['    vec3 rcraTangent = nativeVertexTangent();',
            '    float rcraHandedness = nativeFrameHandedness(rcraTangent);',
            '    vec3 rcraCamera = vTintWorldPos - vTintViewOffset;']


def _surface(emitter, discard=True):
    """Lines computing the G-buffer surface terms into rcraAlbedo, rcraF0, rcraGloss, rcraNormal (view), rcraOcclusion,
    rcraEmission, and the discard."""
    t = emitter.term
    lines = emitter.lines
    albedo = 'vec3(%s, %s, %s)' % (t('albedo.r'), t('albedo.g'), t('albedo.b'))
    f0 = 'vec3(%s, %s, %s)' % (t('f0.r'), t('f0.g'), t('f0.b'))
    normal = 'vec3(%s, %s, %s)' % (t('normal.x'), t('normal.y'), t('normal.z'))
    gloss, occlusion, emission, clip = t('gloss'), t('occlusion', '1.0'), t('emission'), t('clip')
    lines += (['    if (%s != 0.0) discard;' % clip] if discard else []) + [
              '    vec3 rcraAlbedo = clamp(%s, 0.0, 1.0); vec3 rcraF0 = %s; float rcraGloss = %s;' % (albedo, f0, gloss),
              '    float rcraOcclusion = %s; float rcraEmission = %s;' % (occlusion, emission),
              '    vec3 rcraWorldN = %s;' % normal,
              '    vec3 rcraNormal = dot(rcraWorldN, rcraWorldN) > 0.0 ? normalize(vNativeToView * rcraWorldN) : N;']


def fragment_source(receipt):
    """The fragment program of a receipt: {'source', 'samplers' [uniform names], 'key'}."""
    layouts = receipt['layouts']
    body = _frame_lines()
    surface = forward = None
    if receipt.get('surface'):
        surface = _Emitter(receipt['surface'], layouts, 'g')
        _surface(surface, discard=not receipt.get('forward'))
        body += surface.lines
    if receipt.get('forward'):
        forward = _Emitter(receipt['forward'], layouts, 'f')
        t = forward.term
        forward.lines += ['    if (%s != 0.0) discard;' % t('clip'),
                          '    vec3 rcraColour = vec3(%s, %s, %s);' % (t('colour.r'), t('colour.g'), t('colour.b')),
                          '    vec3 rcraBackground = vec3(%s, %s, %s);' % (t('background.r'), t('background.g'),
                                                                          t('background.b')),
                          '    float rcraAlpha = clamp(%s, 0.0, 1.0);' % t('alpha')]
        body += forward.lines
    blend = receipt.get('blend', 'opaque')
    lit = ('rcraDiffuse(rcraNormal, rcraAlbedo, rcraOcclusion) + rcraSpecular(rcraNormal, V, rcraF0, rcraGloss)'
           if surface else 'vec3(0.0)')
    if blend == 'opaque':
        if surface is None:
            raise ValueError('An opaque receipt needs a surface recipe')
        body.append('    sceneOutput(vec4(displayFog(%s + rcraAlbedo * rcraEmission), 1.0));' % lit)
    elif blend == 'modulate':
        body.append('    sceneOutput(vec4(mix(vec3(1.0), rcraColour, rcraAlpha), 1.0));')
    else:
        factor = ('mix(vec3(1.0), rcraBackground, rcraAlpha)' if blend == 'blended'
                  else 'vec3(1.0) + rcraAlpha * rcraBackground')
        colour = '(%s + rcraColour) * rcraAlpha' % lit if blend == 'blended' else 'rcraColour * rcraAlpha'
        body.append('    if (%s == 0) { sceneOutput(vec4(%s, 1.0)); return; }' % (PASS_UNIFORM, factor))
        body.append('    sceneOutput(vec4(display(%s), rcraAlpha));' % colour)
    declarations = '\n'.join('uniform sampler2D %s;' % uniform(b) for b in sorted(layouts))
    declarations += '\nuniform int %s;\n' % PASS_UNIFORM
    from rc2016.viewer.native_glsl import FRAME_HELPERS, HELPERS as NATIVE_HELPERS
    source = (_prefix() + declarations + NATIVE_HELPERS + FRAME_HELPERS + HELPERS + '\nvoid main() {\n'
              '    vec3 N = normalize(vNrm) * (gl_FrontFacing ? 1.0 : -1.0); sceneCaptureNormal = N;\n'
              '    vec3 V = normalize(-vPos);\n' + '\n'.join(body) + '\n}\n')
    key = (EMITTER_VERSION, receipt['translation_sha256'], hashlib.sha256(source.encode()).hexdigest())
    return dict(source=source, samplers=[uniform(b) for b in sorted(layouts)], key=key)


def receipt_sha256(surface, forward, blend, layouts):
    payload = dict(version=EMITTER_VERSION, surface=surface, forward=forward, blend=blend, layouts=layouts)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
