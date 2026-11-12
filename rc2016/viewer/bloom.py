"""The game's bloom, decoded from the nanotech crate capture (RenderDoc replay of one in-game frame), for the model viewer's
float scene target.

Decoded (pixel-program IR, per-draw constant buffers and the intermediate targets of that frame; every stage reproduces the
captured target to within its R11G11B10 quantisation, the final bloom texture to a mean error of 2e-4 against a maximum of 1.25):

1. A 13-tap downsample of the HDR scene to half resolution (program b01a7971): the call-of-duty tap groups (centre 0.5,
   four corners 0.125) in the clamped space sat(0.09182 c), each tap weighted 1 / (luma + 0.025), result x 10.891. The frame holds
   two such images: SCENE_BEFORE (a copy taken before the transparent pass: panes, sprites) and SCENE_AFTER.
2. Two luma-gated, chroma-normalised bright passes into a half-resolution target padded to a multiple of 32
   (programs 982bf018 and 1e4d3846), gain k = exposure / 8, offset -0.5:
       out = weight x sat(k L - 0.5) x c / L, L = max(1/1024, luma(c))
   with c = min(before, after) and weight 0.82 (the bloom of the lit scene), or c = after - min(before, after) = what the
   transparent pass added and weight 1.0 (the bloom of the sprites and panes).
3. A Gaussian pyramid: ten 2:1 downsamples alternating x and y (program cd7e7fc9, taps at +-1.686 source texels, weights
   0.18499 / 0.63002 / 0.18499), then eight upsample-blurs alternating y and x (program 65d43c4f, taps at +-0.5, 1.5, 2.5 source
   texels, weights 0.15058 (+-1.5), 0.31501 (+-0.5), 0.034408 (+-2.5)). Three of the x upsamples are lerped over the pyramid image of
   their level with alpha 0.75, 0.8 and 0.9 (SRC_ALPHA / ONE_MINUS_SRC_ALPHA into the target that holds it); the others
   overwrite. The sprite chain stops after four downsamples and two upsamples, the last added (SRC_ALPHA / ONE, alpha 1)
   into the first chain's result: the bloom texture is at half the padded half resolution (480 x 272 at 1920 x 1080).
4. The composite (program f71f0962) adds the bloom to the tone-curve output before the sRGB encode and the colour grade:
       display = grade(srgb(sat(curve(exposure x scene) + bloom)))
   A second bloom layer (480 x 270, a different chain) and a lens-dirt texture multiply (7 x lens x bloom) are in the program;
   in the captured frame the second layer is below 0.002 and the dirt term fits 0 over the crate (the dirt texture itself did
   not replay), so neither is drawn.

Not decoded: the second bloom layer's chain, the lens-dirt image, the film grain (3 % of a noise texture) and, in the model
viewer, the exposure (the game's runtime value is 3.71 in the captured frame; the viewer's Game tonemap uses the exposure of its
captured display table, shading.GAME_EXPOSURE, and its other tonemaps the lighting exposure alone: gate_exposure). The viewer's scene is the model alone, so only the model and its effects bloom.
Research/captures/2026-11-08_nanotech-crate/README.md (local) has the stage-by-stage comparison.
"""
from __future__ import annotations

import numpy as np

# Program constants (per-draw constant buffers of the capture).
CLAMP_SCALE, BACK_SCALE, LUMA_EPSILON = 0.09182, 10.891, 0.025      # b01a7971: sat(0.09182 c), x 10.891, weight 1 / (luma + 0.025)
GATE_OFFSET = -0.5                                                  # sat(k L - 0.5), k = exposure x 0.125
LIT_WEIGHT, SPRITE_WEIGHT = 0.82, 1.0
DOWN_OFFSET, DOWN_SIDE, DOWN_CENTRE = 1.686, 0.18499, 0.63002        # source texels, tap weights
UP_W0, UP_W1, UP_W2 = 0.034408, 0.15058, 0.31501                     # taps +-2.5, +-1.5, +-0.5
LERP_ALPHA = {8: 0.75, 6: 0.8, 4: 0.9}                              # destination pyramid level -> upsample lerp alpha
GATE_GAIN_PER_EXPOSURE = 0.125
LUMA = (0.2126, 0.7152, 0.0722)
PAD = 32
LEVELS = 10          # downsample steps of the lit chain
SPRITE_LEVELS = 4    # downsample steps of the sprite chain
CHANNEL_FLOOR = 1.5258789e-05


def self_lit(k=0.0, bright_center=False, glow_shell=False, translation=None):
    """Whether a material emits light of its own, so its frame blooms (the viewer's self_lit_entry and the Blender front-end's
    emitter bloom both use this): a self-lit gain k above 0 (the uK of every self-lit term), a bright_center bulb, the additive
    antenna glow shell, or a native translation whose emission output is not constant 0."""
    if float(k or 0.0) > 0.0 or bright_center or glow_shell:
        return True
    if not translation:
        return False
    node = translation['nodes'][translation['outputs']['emission']]
    return not (node.get('op') == 'const' and float(node.get('value', 0.0)) <= 0.0)


def padded_half(width, height):
    """The half-resolution size of a (width, height) viewport, padded up to a multiple of 32 (960 x 544 for 1920 x 1080)."""
    up = lambda n: max(PAD, -(-((n + 1) // 2) // PAD) * PAD)
    return up(width), up(height)


def level_sizes(width, height):
    """Sizes of the pyramid images D0..D10: D0 is the padded half resolution; odd steps halve x, even steps halve y."""
    w, h = padded_half(width, height)
    sizes = [(w, h)]
    for step in range(1, LEVELS + 1):
        w, h = (max(1, w // 2), h) if step % 2 else (w, max(1, h // 2))
        sizes.append((w, h))
    return sizes


def bloom_size(width, height):
    """Size of the bloom texture: half of the padded half resolution in each direction."""
    w, h = padded_half(width, height)
    return w // 2, h // 2


def gate_exposure(lighting_exposure, game_display, game_exposure):
    """The exposure the bright-pass gate takes: the one the display applies to the scene. The game display multiplies by its
    captured exposure (`game_exposure`); the viewer's other tonemaps by the lighting exposure alone, so gating them at the
    game exposure would let surfaces that display mid-grey (the lit health crate panes) bloom."""
    return lighting_exposure * (game_exposure if game_display else 1.0)


def passes(width, height, exposure):
    """The pass list for one viewport size as dicts: kind (down13, prefilter, down, up), source image names, destination name,
    destination size, uniforms and blend (None, 'lerp', 'add'). Names: before, after (half-resolution scenes), D0..D10, bloom."""
    sizes = level_sizes(width, height)
    gain = exposure * GATE_GAIN_PER_EXPOSURE
    out = [dict(kind='prefilter', src=('before', 'after'), dst='D0', size=sizes[0], gain=gain, weight=LIT_WEIGHT, diff=False, blend=None)]
    for step in range(1, LEVELS + 1):
        src, dst = sizes[step - 1], sizes[step]
        axis = (1.0, 0.0) if step % 2 else (0.0, 1.0)
        out.append(dict(kind='down', src=('D%d' % (step - 1),), dst='D%d' % step, size=dst, blend=None,
                        offset=(axis[0] * DOWN_OFFSET / src[0], axis[1] * DOWN_OFFSET / src[1])))
    # Up chain: from D10 back to the bloom texture. Up step n reads level n and writes level n - 1 (the last, n = 3, writes the
    # bloom texture); it doubles the axis the down step n halved (y for even n, x for odd n). The x steps into the levels in
    # LERP_ALPHA are lerped over the pyramid image that level already holds.
    for n in range(LEVELS, 2, -1):
        src, dst = sizes[n], sizes[n - 1]
        axis = (1.0, 0.0) if n % 2 else (0.0, 1.0)
        last = n == 3
        alpha = LERP_ALPHA.get(n - 1)
        out.append(dict(kind='up', src=('D%d' % n,), dst='bloom' if last else 'D%d' % (n - 1),
                        size=bloom_size(width, height) if last else dst, offset=(axis[0] / src[0], axis[1] / src[1]),
                        alpha=alpha or 1.0, blend='lerp' if alpha else None))
    # Sprite chain: the same pyramid, four steps down, then two up added into the bloom texture.
    out.append(dict(kind='prefilter', src=('before', 'after'), dst='D0', size=sizes[0], gain=gain, weight=SPRITE_WEIGHT, diff=True, blend=None))
    for step in range(1, SPRITE_LEVELS + 1):
        src, dst = sizes[step - 1], sizes[step]
        axis = (1.0, 0.0) if step % 2 else (0.0, 1.0)
        out.append(dict(kind='down', src=('D%d' % (step - 1),), dst='D%d' % step, size=dst, blend=None,
                        offset=(axis[0] * DOWN_OFFSET / src[0], axis[1] * DOWN_OFFSET / src[1])))
    for n in (SPRITE_LEVELS, SPRITE_LEVELS - 1):
        src, dst = sizes[n], sizes[n - 1]
        horizontal = (n % 2 == 1)
        axis = (1.0, 0.0) if horizontal else (0.0, 1.0)
        out.append(dict(kind='up', src=('D%d' % n,), dst='D%d' % (n - 1), size=dst, offset=(axis[0] / src[0], axis[1] / src[1]), alpha=1.0, blend=None))
    out[-1].update(dst='bloom', size=bloom_size(width, height), blend='add')
    return out


# ---------------------------------------------------------------------------------------------------------------------
# CPU reference (numpy), the same arithmetic as the fragment programs below; used by the tests and the capture comparison.

def _bilinear(image, u, v):
    """Clamp-to-edge bilinear at normalised coordinates."""
    h, w = image.shape[:2]
    x = np.clip(u * w - 0.5, 0, w - 1)
    y = np.clip(v * h - 0.5, 0, h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return (image[y0, x0] * (1 - fx) + image[y0, x1] * fx) * (1 - fy) + (image[y1, x0] * (1 - fx) + image[y1, x1] * fx) * fy


def _grid(size):
    ys, xs = np.mgrid[0:size[1], 0:size[0]]
    return (xs + 0.5) / size[0], (ys + 0.5) / size[1]


def downsample13(scene, size):
    """The 13-tap downsample of a (h, w, 3) scene to `size` (width, height)."""
    sh, sw = scene.shape[:2]
    u, v = _grid(size)
    luma = np.array(LUMA, np.float32)
    taps = {}

    def tap(ox, oy):
        if (ox, oy) not in taps:
            s = np.clip(CLAMP_SCALE * _bilinear(scene, u + ox / sw, v + oy / sh), 0.0, 1.0)
            taps[(ox, oy)] = (s, 1.0 / (s @ luma + LUMA_EPSILON)[..., None])
        return taps[(ox, oy)]

    def group(offsets):
        total, accumulated = 0.0, 0.0
        for o in offsets:
            s, w = tap(*o)
            total, accumulated = total + w, accumulated + w * s
        return accumulated / total

    centre = group([(-1, -1), (1, -1), (-1, 1), (1, 1)])
    corners = (group([(-2, -2), (0, -2), (-2, 0), (0, 0)]), group([(0, -2), (2, -2), (0, 0), (2, 0)]),
               group([(-2, 0), (0, 0), (-2, 2), (0, 2)]), group([(0, 0), (2, 0), (0, 2), (2, 2)]))
    return BACK_SCALE * (0.5 * centre + 0.125 * sum(corners))


def _prefilter(before, after, gain, weight, diff):
    low = np.minimum(before, after)
    c = np.maximum(CHANNEL_FLOOR, after - low if diff else low)
    luma = np.maximum(1 / 1024.0, c @ np.array(LUMA, np.float32))[..., None]
    return weight * np.clip(gain * luma + GATE_OFFSET, 0.0, 1.0) * np.maximum(0.0, c / luma)


def _down(image, size, offset):
    u, v = _grid(size)
    s = _bilinear(image, u - offset[0], v - offset[1]) + _bilinear(image, u + offset[0], v + offset[1])
    return DOWN_SIDE * s + DOWN_CENTRE * _bilinear(image, u, v)


def _up(image, size, offset):
    u, v = _grid(size)
    t = lambda k: _bilinear(image, u + k * offset[0], v + k * offset[1])
    return UP_W1 * (t(-1.5) + t(1.5)) + UP_W2 * (t(-0.5) + t(0.5)) + UP_W0 * (t(2.5) + t(-2.5))


def reference_chain(before, after, exposure):
    """The bloom texture (h, w, 3) from the two half-resolution scenes of `padded_half` size (float arrays, scene-linear)."""
    height, width = before.shape[:2]
    images = {'before': before.astype(np.float32), 'after': after.astype(np.float32)}
    bloom = None
    for p in passes(width * 2, height * 2, exposure):
        if p['kind'] == 'prefilter':
            images[p['dst']] = _prefilter(images['before'], images['after'], p['gain'], p['weight'], p['diff'])
        elif p['kind'] == 'down':
            images[p['dst']] = _down(images[p['src'][0]], p['size'], p['offset'])
        else:
            up = _up(images[p['src'][0]], p['size'], p['offset'])
            if p['blend'] == 'lerp':
                up = (1 - p['alpha']) * images[p['dst']] + p['alpha'] * up
            elif p['blend'] == 'add':
                up = images[p['dst']] + p['alpha'] * up
            images[p['dst']] = up
    return images['bloom']


def reference(before_scene, after_scene, exposure):
    """Bloom texture of two full-resolution (h, w, 3) scenes: the scene before the transparent pass and after it."""
    height, width = after_scene.shape[:2]
    size = padded_half(width, height)
    return reference_chain(downsample13(before_scene, size), downsample13(after_scene, size), exposure)


# ---------------------------------------------------------------------------------------------------------------------
# GLSL (version 120) for ModelViewport's bloom passes; the same arithmetic as the reference above.

VERTEX = """
#version 120
void main() { gl_Position = gl_Vertex; }
"""

DOWN13_FRAGMENT = """
#version 120
uniform sampler2D uSrc; uniform vec2 uSrcSize; uniform vec2 uSize;
const float K = %(clamp)s, EPS = %(eps)s, BACK = %(back)s;
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
vec2 gUV;
vec3 tapColour(float x, float y) { return clamp(texture2D(uSrc, gUV + vec2(x, y) / uSrcSize).rgb * K, 0.0, 1.0); }
float tapWeight(vec3 s) { return 1.0 / (dot(s, LUMA) + EPS); }
vec3 group(vec3 a, vec3 b, vec3 c, vec3 d) {
    float wa = tapWeight(a), wb = tapWeight(b), wc = tapWeight(c), wd = tapWeight(d);
    return (a * wa + b * wb + c * wc + d * wd) / (wa + wb + wc + wd);
}
void main() {
    gUV = gl_FragCoord.xy / uSize;
    vec3 a = tapColour(-2.0, -2.0), b = tapColour(0.0, -2.0), c = tapColour(2.0, -2.0);
    vec3 d = tapColour(-2.0, 0.0), e = tapColour(0.0, 0.0), f = tapColour(2.0, 0.0);
    vec3 g = tapColour(-2.0, 2.0), h = tapColour(0.0, 2.0), i = tapColour(2.0, 2.0);
    vec3 j = tapColour(-1.0, -1.0), k = tapColour(1.0, -1.0), l = tapColour(-1.0, 1.0), m = tapColour(1.0, 1.0);
    vec3 centre = group(j, k, l, m);
    vec3 corners = group(a, b, d, e) + group(b, c, e, f) + group(d, e, g, h) + group(e, f, h, i);
    gl_FragColor = vec4(BACK * (0.5 * centre + 0.125 * corners), 1.0);
}
""" % dict(clamp=repr(CLAMP_SCALE), eps=repr(LUMA_EPSILON), back=repr(BACK_SCALE))

PREFILTER_FRAGMENT = """
#version 120
uniform sampler2D uBefore; uniform sampler2D uAfter; uniform vec2 uSize;
uniform float uGain, uWeight; uniform bool uDiff;
void main() {
    vec2 uv = gl_FragCoord.xy / uSize;
    vec3 a = texture2D(uBefore, uv).rgb, b = texture2D(uAfter, uv).rgb;
    vec3 low = min(a, b);
    vec3 c = max(vec3(1.5258789e-05), uDiff ? b - low : low);
    float luma = max(1.0 / 1024.0, dot(c, vec3(0.2126, 0.7152, 0.0722)));
    gl_FragColor = vec4(uWeight * clamp(uGain * luma + (%(offset)s), 0.0, 1.0) * max(c / luma, 0.0), 1.0);
}
""" % dict(offset=repr(GATE_OFFSET))

DOWN_FRAGMENT = """
#version 120
uniform sampler2D uSrc; uniform vec2 uSize; uniform vec2 uOffset;
void main() {
    vec2 uv = gl_FragCoord.xy / uSize;
    gl_FragColor = vec4(%(side)s * (texture2D(uSrc, uv - uOffset).rgb + texture2D(uSrc, uv + uOffset).rgb)
                        + %(centre)s * texture2D(uSrc, uv).rgb, 1.0);
}
""" % dict(side=repr(DOWN_SIDE), centre=repr(DOWN_CENTRE))

UP_FRAGMENT = """
#version 120
uniform sampler2D uSrc; uniform vec2 uSize; uniform vec2 uOffset; uniform float uAlpha;
vec3 tap(vec2 uv, float k) { return texture2D(uSrc, uv + k * uOffset).rgb; }
void main() {
    vec2 uv = gl_FragCoord.xy / uSize;
    vec3 c = %(w1)s * (tap(uv, -1.5) + tap(uv, 1.5)) + %(w2)s * (tap(uv, -0.5) + tap(uv, 0.5)) + %(w0)s * (tap(uv, 2.5) + tap(uv, -2.5));
    gl_FragColor = vec4(c, uAlpha);
}
""" % dict(w0=repr(UP_W0), w1=repr(UP_W1), w2=repr(UP_W2))

PROGRAMS = {
    'down13': (DOWN13_FRAGMENT, ('uSrc', 'uSrcSize', 'uSize')),
    'prefilter': (PREFILTER_FRAGMENT, ('uBefore', 'uAfter', 'uSize', 'uGain', 'uWeight', 'uDiff')),
    'down': (DOWN_FRAGMENT, ('uSrc', 'uSize', 'uOffset')),
    'up': (UP_FRAGMENT, ('uSrc', 'uSize', 'uOffset', 'uAlpha')),
}
