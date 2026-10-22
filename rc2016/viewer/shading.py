"""GLSL program for the preview: GPU skinning plus the game's opaque BRDF.

Runs on the viewport's legacy (compatibility) WGL context with GLSL 1.20, so
the existing fixed-function matrix stack still drives the camera.

Shading follows the game's deferred BRDF (rc2016.pipeline.game_shader; docs/shading.md):
diffuse = _c; specular = GGX with Schlick Fresnel, F0 = _g.rgb,
F90 = saturate(60 * F0), GGX alpha = 2^(-5.5 * gloss) (gloss = _g.a);
diffuse and specular are added; self-lit = _c.rgb * _c.a * K. Graphs whose
decoded writers differ (grey _g, _g.B self-lit, spec/gloss scales, the Blarg
saucer colour pick) are set per material from texture_preview.GRAPH_RULES. Normals use
_n as an RG tangent map without a green flip (the Blender builder's
convention), with the tangent frame taken from screen-space UV derivatives.
Lighting is either the fixed viewer rig (key, fill, sky/ground ambient) or the shared review
HDRI as pre-blurred maps (rc2016/viewer/environment.py), with an ACES fit or Khronos PBR Neutral
tonemap (LIGHTING below): viewing aids, not the game's lighting.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import struct
from rc2016.levels.scene_effects import HELPERS as SCENE_HELPERS, SCENE_UNIFORMS

ENVIRONMENT_TEXTURE_UNIT = 6   # shared review HDRI: prefiltered radiance bands (environment.py)
GRADE_TEXTURE_UNIT = 18        # captured display table (tone curve + colour grade; rc2016/levels/display.py); unit 16, 17 are the scene AO/shadow maps
# Viewer lighting shared by FRAGMENT and GLASS_FRAGMENT. uLighting 0 is the fixed viewer rig: key and
# fill lights plus a sky/ground hemisphere, in rig space; uLightFrame maps rig space to view space
# (identity: the rig turns with the camera; the level viewer fixes it to the world). uLighting 1 is
# the shared review HDRI in Blender world space (uViewToWorld): its sun as the key light (uSun*),
# diffuse from 9 irradiance SH coefficients of the rest, specular from GGX-prefiltered bands and
# Karis' analytic environment BRDF. All carry the world strength. The rig's diffuse keeps its
# historical factor 2 (diffuseScale); the HDRI's is physical (albedo x E / pi). display() applies exposure and the tonemap: 0 the ACES fit, 1 Khronos PBR
# Neutral with the sRGB transfer function (the view transform of the Blender review renders).
LIGHTING = """
uniform int uLighting; uniform bool uLightingEnabled; uniform mat3 uLightFrame; uniform mat3 uViewToWorld;
uniform vec3 uSH[9]; uniform sampler2D uEnvTex; uniform vec3 uSunDir; uniform vec3 uSunColour;
uniform int uTonemap; uniform float uExposure;
uniform sampler2D uGradeTex;   // captured display table (rc2016/levels/display.py): colour grade rows 0-31, tone curve row 32
vec3 rigKey() { return uLighting == 1 ? uSunDir * uViewToWorld : uLightFrame * normalize(vec3(-0.45, 0.65, 0.62)); }
vec3 rigFill() { return uLightFrame * normalize(vec3(0.7, 0.1, 0.4)); }
vec3 keyColour() { return !uLightingEnabled ? vec3(0.0) : (uLighting == 1 ? uSunColour : vec3(2.6, 2.5, 2.35)); }
float diffuseScale() { return uLighting == 1 ? 1.0 : 2.0; }
vec3 fillColour() { return !uLightingEnabled || uLighting == 1 ? vec3(0.0) : vec3(0.55, 0.6, 0.7); }
vec3 irradiance(vec3 n) {
    return max(uSH[0] * 0.282095 + uSH[1] * (0.488603 * n.y) + uSH[2] * (0.488603 * n.z) + uSH[3] * (0.488603 * n.x)
        + uSH[4] * (1.092548 * n.x * n.y) + uSH[5] * (1.092548 * n.y * n.z) + uSH[6] * (0.315392 * (3.0 * n.z * n.z - 1.0))
        + uSH[7] * (1.092548 * n.x * n.z) + uSH[8] * (0.546274 * (n.x * n.x - n.y * n.y)), 0.0);
}
#ifdef RC_SCENE_EFFECTS
// Lightgrid indirect light of the Level atmosphere lighting (rc2016/levels/lightgrid.py is the CPU reference and
// documents the layout). Eight cells around the fragment, weighted by trilinear * validity, each lerp(B, A, 0.5 +
// 0.5 d.n) + S (0.5 + 0.5 n.y) C; `cover` is how much of the sample lies on existing valid cells, and ambientLight
// fades to the single-probe SH ambient as it falls. The position and normal go to the game's native Y-up frame.
uniform bool uGridOn; uniform sampler3D uGridTable; uniform sampler3D uGridColour; uniform sampler3D uGridDirQ;
uniform mat4 uGridFromView; uniform vec2 uGridParams; uniform vec3 uGridInvSize;   // params: sky-term scale S, grid-to-ambient scale
const float GRID_COVER_GAIN = 4.0;
vec3 gridAmbient(vec3 Nv, out float cover) {
    vec3 P = (uGridFromView * vec4(vPos, 1.0)).xyz;
    vec3 n = normalize(mat3(uGridFromView) * Nv);
    vec3 s = P - 0.5, base = floor(s), f = s - base;
    vec3 acc = vec3(0.0); float total = 0.0, valid = 0.0;
    for (int i = 0; i < 8; i++) {
        vec3 b = vec3(mod(float(i), 2.0), mod(floor(float(i) * 0.5), 2.0), floor(float(i) * 0.25));
        vec3 cell = base + b, tw = mix(1.0 - f, f, b);
        float tri = tw.x * tw.y * tw.z;
        vec3 tile = floor(cell / 16.0);
        vec4 e = texture3D(uGridTable, (mod(tile, 64.0) + 0.5) / 64.0);
        if (e.a > 0.5) {
            vec3 c = e.rgb * 16.0 + (cell - tile * 16.0);
            vec2 yz = (c.yz + 0.5) * uGridInvSize.yz;
            vec4 dd = texture3D(uGridDirQ, vec3((c.x * 2.0 + 0.5) * uGridInvSize.x * 0.5, yz));
            vec4 qw = texture3D(uGridDirQ, vec3((c.x * 2.0 + 1.5) * uGridInvSize.x * 0.5, yz));
            vec3 o = s - cell;
            float v = clamp(8.0 * dot(qw.xyz - 0.5, o) + 16.0 * qw.w - 7.0, 0.0, 1.0);
            float w = max(3.814697265625e-06, tri * v);
            float x3 = uGridInvSize.x / 3.0;
            vec3 A = texture3D(uGridColour, vec3((c.x * 3.0 + 0.5) * x3, yz)).rgb;
            vec3 B = texture3D(uGridColour, vec3((c.x * 3.0 + 1.5) * x3, yz)).rgb;
            vec3 C = texture3D(uGridColour, vec3((c.x * 3.0 + 2.5) * x3, yz)).rgb;
            vec3 d = dd.xyz * 2.0 - 1.0;
            acc += w * (mix(B, A, 0.5 + 0.5 * dot(n, d)) + uGridParams.x * (0.5 + 0.5 * n.y) * C);
            total += w; valid += tri * v;
        }
    }
    cover = clamp(valid * GRID_COVER_GAIN, 0.0, 1.0);
    return total > 0.0 ? acc / total : vec3(0.0);
}
#endif
// Diffuse is albedo * (lights + ambient) / PI * diffuseScale().
vec3 ambientLight(vec3 N) {
    if (!uLightingEnabled) return vec3(0.0);
    if (uLighting == 1) {
#ifdef RC_SCENE_EFFECTS
        if (uGridOn) {
            float cover; vec3 g = gridAmbient(N, cover);
            return mix(irradiance(uViewToWorld * N), g * uGridParams.y, cover);
        }
#endif
        return irradiance(uViewToWorld * N);
    }
    return mix(vec3(0.10, 0.09, 0.08), vec3(0.30, 0.33, 0.38), dot(N, uLightFrame[1]) * 0.5 + 0.5);
}
// Band i of the atlas (six bands, sharpest at the top) at Blender's equirectangular (u, v).
vec3 envBand(float u, float v, float i) {
    return texture2D(uEnvTex, vec2(u, (i + clamp(1.0 - v, 0.0078125, 0.9921875)) / 6.0)).rgb;
}
vec3 prefiltered(vec3 d, float r) {
    float u = dot(d.xy, d.xy) > 1e-12 ? 0.5 - atan(d.y, d.x) / (2.0 * PI) : 0.0;
    float v = atan(d.z, length(d.xy)) / PI + 0.5;
    // environment.ROUGHNESS_LEVELS: 0.15, 0.3, 0.45, 0.6, 0.8, 1.0
    float level = clamp(r <= 0.6 ? (r - 0.15) / 0.15 : 3.0 + (r - 0.6) / 0.2, 0.0, 5.0);
    float i0 = floor(level);
    return mix(envBand(u, v, i0), envBand(u, v, min(i0 + 1.0, 5.0)), level - i0);
}
// Environment specular for GGX alpha a; the rig's version is its sky/ground ambient term.
vec3 envSpecular(vec3 N, vec3 V, vec3 F0, vec3 F90, float a, vec3 ambient) {
    if (!uLightingEnabled) return vec3(0.0);
    if (uLighting == 1) {
        float r = sqrt(a), nv = max(dot(N, V), 1e-4);
        vec4 k = r * vec4(-1.0, -0.0275, -0.572, 0.022) + vec4(1.0, 0.0425, 1.04, -0.04);
        float b = min(k.x * k.x, exp2(-9.28 * nv)) * k.x + k.y;
        vec2 ab = vec2(-1.04, 1.04) * b + k.zw;
        return prefiltered(uViewToWorld * reflect(-V, N), r) * (F0 * ab.x + F90 * ab.y);
    }
    return (F0 + (F90 - F0) * pow(1.0 - max(dot(N, V), 0.0), 5.0)) * ambient * (1.0 - 0.6 * a);
}
float srgbEncode(float x) { return x <= 0.0031308 ? 12.92 * x : 1.055 * pow(x, 1.0 / 2.4) - 0.055; }
// uTonemap 2, the game's final display chain for the Level atmosphere lighting (decoded from the Veldin capture; the
// exposure is that frame's runtime value, assumed for other levels; rc2016/levels/display.py is the CPU reference):
// exposure, the 1D tone curve over log2 [-8, 5] (table row 32, 1024 samples), sRGB, then the atmosphere's 32^3
// colour-grade LUT sampled trilinearly (blue slabs side by side in rows 0-31; bilinear in red and green inside a slab).
const float GAME_EXPOSURE = 4.553783893585205;
float gameCurve(float c) {
    float u = clamp((log2(max(c, 1e-10)) + 8.0) / 13.0, 0.0, 1.0);
    return texture2D(uGradeTex, vec2((u * 1023.0 + 0.5) / 1024.0, 32.5 / 33.0)).r;
}
vec3 gameGrade(vec3 c) {
    vec3 p = clamp(c, 0.0, 1.0) * 31.0;
    float slab = min(floor(p.b), 30.0), f = p.b - slab;
    float y = (p.g + 0.5) / 33.0;
    vec3 a = texture2D(uGradeTex, vec2((slab * 32.0 + p.r + 0.5) / 1024.0, y)).rgb;
    vec3 b = texture2D(uGradeTex, vec2((slab * 32.0 + 32.0 + p.r + 0.5) / 1024.0, y)).rgb;
    return mix(a, b, f);
}
vec3 gameDisplay(vec3 color) {
    color *= GAME_EXPOSURE;
    vec3 t = vec3(gameCurve(color.r), gameCurve(color.g), gameCurve(color.b));
    return gameGrade(vec3(srgbEncode(t.r), srgbEncode(t.g), srgbEncode(t.b)));
}
vec3 display(vec3 color) {
    color *= uExposure;
    if (uTonemap == 2) return gameDisplay(color);
    if (uTonemap == 1) {   // Khronos PBR Neutral: start compression 0.76, desaturation 0.15
        float x = min(color.r, min(color.g, color.b));
        color -= x < 0.08 ? x - 6.25 * x * x : 0.04;
        float peak = max(color.r, max(color.g, color.b));
        if (peak >= 0.76) {
            float newPeak = 1.0 - 0.0576 / (peak - 0.52);
            color *= newPeak / peak;
            color = mix(color, vec3(newPeak), 1.0 - 1.0 / (0.15 * (peak - newPeak) + 1.0));
        }
        color = clamp(color, 0.0, 1.0);
        return vec3(srgbEncode(color.r), srgbEncode(color.g), srgbEncode(color.b));
    }
    color = color * (2.51 * color + 0.03) / (color * (2.43 * color + 0.59) + 0.14);   // ACES fit
    return pow(clamp(color, 0.0, 1.0), vec3(1.0 / 2.2));
}
"""
LIGHTING += "\n#ifdef RC_SCENE_EFFECTS\n" + SCENE_HELPERS + "\n#else\nfloat sceneAO(){return 1.0;}\nfloat sceneShadow(vec3 p){return 1.0;}\nfloat sceneSpecularOcclusion(vec3 N,vec3 V,float a){return 1.0;}\n#endif\n"
LIGHTING_UNIFORMS = (*SCENE_UNIFORMS, "uLighting", "uLightingEnabled", "uLightFrame", "uViewToWorld", "uSH", "uEnvTex", "uSunDir", "uSunColour", "uTonemap", "uExposure", "uGradeTex", "uFogOn", "uFogColour", "uFogA", "uFogB", "uFogAlpha", "uFogBeta", "uFogMisc")

# Attribute locations for the two 4-wide skin influence sets. NVIDIA aliases the
# built-ins to fixed slots (0 vertex, 2 normal, 3 colour, 4 secondary colour,
# 5 fog, 8+ texcoords), and a collision fails the link, so use 1, 6, 7 and 9
# (texcoord 1, which the shader never reads).
ATTRIBUTES = {"aJoints0": 1, "aJoints1": 6, "aWeights0": 7, "aWeights1": 9}
STATIC_VECTOR_ATTRIBUTE = 4
TANGENT_ATTRIBUTE = 5   # decoded vertex tangent (unaliased fog-coordinate slot, as 4 is for the static vector)
BONE_TEXTURE_UNIT = 3
ROCK_TEXTURE_UNIT = 15   # RockBlend sole new sampler, four tiled source slices
ROCK_ALPHA_ATTRIBUTE = 14
GROUND_BLEND_ATTRIBUTE = 15
GLOW_PARALLAX_TEXTURE_UNIT = 13   # prompt 08; existing unit 5 carries the light mask
VEHICLE_PALETTE_TEXTURE_UNIT = 10   # EB1AC2CD second palette; prompt 05 reservation
PICK_TEXTURE_UNIT = 4   # colour-pick palette (blarg_saucer_colorpick)
ARCH_SEQUENCE_TEXTURE_UNIT = 8   # frozen AAED778C sequence; prompt 03 reservation
BNG_DETAIL_TEXTURE_UNIT = 11   # prompt 06 normal detail only
TINT_DETAIL_TEXTURE_UNIT = 9   # ColorTintVariants; sole new sampler for prompt04
PARALLAX_HEIGHT_TEXTURE_UNIT = 12   # separate source height-alpha slot
TINT_MASK_TEXTURE_UNIT = 5   # _h tint mask (mo_gbl_color_tint_01)
LEAF_CMA_TEXTURE_UNIT = 7   # Novalis tree leaf cma (rp_nov_plant_tree_foliage_transparent); the only free unit
RETICLE_TEXTURE_UNIT = 0  # Victor reticle reuses the unbound _c sampler; its bounded rule has no _c read
# Bones live in a 1-row RGBA32F texture (4 texels = one row-major matrix):
# a uniform array runs out of vertex constants near 200 joints (Clank has 150,
# bucketed to 256 it failed with NVIDIA C6020).
MAX_JOINTS = 4096

VERTEX = """
#version 120
uniform bool uSkin;
uniform sampler2D uBoneTex;
uniform float uBoneWidth;     // texels = 4 * joints
attribute vec4 aJoints0; attribute vec4 aJoints1;
attribute vec4 aWeights0; attribute vec4 aWeights1;
varying vec3 vPos; varying vec3 vNrm; varying vec2 vUV;
varying vec3 vTintWorldPos; varying vec3 vTintWorldNrm; varying vec3 vTintViewOffset; varying vec3 vTintWorldTan;
attribute float aRockAlpha; varying float vRockAlpha;
attribute vec3 aGroundBlend; varying vec3 vGroundBlend;
attribute vec3 aStaticPackedVector; varying vec3 vStaticPackedVector;
// Decoded vertex tangent, w the stored bitangent sign (position w); zero when the mesh has no decoded tangent.
attribute vec4 aTangent; varying vec3 vTangent; varying float vTangentSign;
// Rows of joint j's row-major skin matrix, as the columns of its transpose.
mat4 boneT(float j) {
    float x = j * 4.0 + 0.5;
    return mat4(texture2DLod(uBoneTex, vec2(x / uBoneWidth, 0.5), 0.0),
                texture2DLod(uBoneTex, vec2((x + 1.0) / uBoneWidth, 0.5), 0.0),
                texture2DLod(uBoneTex, vec2((x + 2.0) / uBoneWidth, 0.5), 0.0),
                texture2DLod(uBoneTex, vec2((x + 3.0) / uBoneWidth, 0.5), 0.0));
}
void main() {
    vec4 p = gl_Vertex; vec3 n = gl_Normal; vec3 tn = aTangent.xyz; vTangentSign = aTangent.w;
    vRockAlpha = aRockAlpha * aRockAlpha;
    vGroundBlend = vec3(aGroundBlend.xy * aGroundBlend.xy * aGroundBlend.z, aGroundBlend.z);
    // This opt-in source input is supplied separately from the unit shading normal.
    // The model viewer payload is in display basis (x, -z, y); return it to native Y-up.
    vStaticPackedVector = vec3(aStaticPackedVector.x, aStaticPackedVector.z, -aStaticPackedVector.y);
    float total = dot(aWeights0, vec4(1.0)) + dot(aWeights1, vec4(1.0));
    if (uSkin && total > 1e-4) {
        mat4 t = boneT(aJoints0.x) * aWeights0.x + boneT(aJoints0.y) * aWeights0.y
               + boneT(aJoints0.z) * aWeights0.z + boneT(aJoints0.w) * aWeights0.w
               + boneT(aJoints1.x) * aWeights1.x + boneT(aJoints1.y) * aWeights1.y
               + boneT(aJoints1.z) * aWeights1.z + boneT(aJoints1.w) * aWeights1.w;
        // v * transpose(M) == M * v
        p = gl_Vertex * t; n = (vec4(gl_Normal, 0.0) * t).xyz; tn = (vec4(aTangent.xyz, 0.0) * t).xyz;
    }
    vTangent = gl_NormalMatrix * tn;
    vTintWorldPos = vec3(p.x, p.z, -p.y); vTintWorldNrm = vec3(n.x, n.z, -n.y); vTintWorldTan = vec3(tn.x, tn.z, -tn.y);
    vec4 view = gl_ModelViewMatrix * p;
    // Camera-to-vertex offset in the vTintWorldPos frame (affine, so it interpolates exactly).
    vec3 o = (gl_ModelViewMatrixInverse * vec4(view.xyz, 0.0)).xyz; vTintViewOffset = vec3(o.x, o.z, -o.y);
    vPos = view.xyz; vNrm = gl_NormalMatrix * n; vUV = gl_MultiTexCoord0.xy;
    gl_Position = gl_ProjectionMatrix * view;
}
"""

FRAGMENT = """
#version 120
#extension GL_ARB_shader_texture_lod : enable
uniform bool uRockBlend; uniform sampler3D uRockTex; uniform vec3 uRockSrgb;
uniform vec4 uRockScale0; uniform vec4 uRockScale1;
uniform vec3 uRockTint; uniform vec4 uRockP; // exponent, tint amount, mask strength, sharpness
uniform vec3 uRockBreakup; // A3DAA047: enabled, 1/max(1e-5, world tile T), layer UV scale U
varying float vRockAlpha; varying vec3 vGroundBlend;
uniform int uMode;           // 0 solid, 1 base colour, 2 game BRDF, 3 flat colour (wireframe)
uniform vec3 uColor;
uniform sampler2D uTexC; uniform sampler2D uTexG; uniform sampler2D uTexN;
uniform bool uHasC; uniform bool uHasG; uniform bool uHasN;
uniform bool uSrgbC; uniform bool uSrgbG;
uniform int uGMode;           // 0 coloured F0 + gloss alpha, 1 basic_skin (grey, cap 0.5), 2 grey (R gloss, G specular)
uniform float uSpecScale; uniform float uGlossScale;
uniform bool uTwoColorMetal;
uniform vec3 uMetalDiffuseA; uniform vec3 uMetalDiffuseB;
uniform vec3 uMetalSpecA; uniform vec3 uMetalSpecB;
uniform vec4 uMetalP; // tint power, rim-gloss power, C texture scale, normal strength
uniform bool uParallax; uniform bool uParallaxNormal; uniform bool uHasParallaxHeight;
uniform sampler2D uTexParallaxHeight; uniform vec4 uParallaxP; // H, face/grazing steps, fade
uniform bool uFoliage; // FE32FE79/1E0AE45A close-range normal z weight
uniform float uAlphaCut; // leaf cut-out: discard when _c.a < threshold (0 = off); threshold inferred, see textures.FOLIAGE_ALPHA_CUTOFF
// Novalis tree leaf colour, rp_nov pass-0 writer 0476D929. uLeafP = (E, S, mapped-normal Fresnel, unused);
// palette U is a frozen preview input (textures.LEAF_PALETTE_U_PLACEHOLDER).
uniform bool uLeafColour; uniform vec4 uLeafP; uniform vec3 uLeafPalette; // uLeafPalette = cmb ramp sample
uniform bool uLiquidFlow; uniform vec3 uLiquidUV; // EA6006F6/BACB8758 C/G coordinates, time=0
uniform bool uFlatSurface; uniform vec3 uFlatColour; uniform vec4 uFlatSpec; // constant surfaces: basic_emissive 452EDC8C, goo floor 4A73CC38, basic_flat_color D169A814
uniform bool uBasicFlatColor; uniform float uBasicFlatEmissionScale; // D169A814: independent CB2 colour * scale additive output
uniform bool uVictorReticle; uniform sampler2D uTexReticle; uniform bool uHasReticle; uniform vec4 uReticleP; uniform vec3 uReticleF0;
uniform bool uFresnelTint; uniform vec3 uFresnelC; uniform float uFresnelE; // Novalis glow mushroom F69ADC40 albedo tint
// Hair_NewSpec_01 forward pass 94CE8CCE: uHairA = constant albedo, uHairB = F0 colour (x _g.G), uHairP = (rim exponent E,
// gloss scale, diffuse sigma (unused by the Lambert viewer diffuse), normal strength).
uniform bool uHair; uniform vec3 uHairA; uniform vec3 uHairB; uniform vec4 uHairP;
// basic_skin deferred writers 9AAD640B (uSkinKind 1) / 0E6CC87B (2): uSkinA = (cap, gloss Fresnel, spec Fresnel, exponent),
// uSkinB = (albedo, gloss, spec, gloss2), uSkinC = (spec2, detail UV scale, detail blend, unused).
uniform int uSkinKind; uniform vec4 uSkinA; uniform vec4 uSkinB; uniform vec4 uSkinC;
uniform sampler2D uTexCma; uniform bool uHasCma; uniform bool uSrgbCma;
uniform bool uLinearZ; // basic_colored_spec 63EEBF51: normal z = 1 - x*x - y*y
uniform bool uZaFresnel; uniform float uZaFresnelPower; // za_basic_normal_gloss geometry-normal rim suppression
uniform bool uSelfLitG;       // self-lit mask from _g.B instead of _c.a
uniform float uK;
uniform bool uPuddles; uniform sampler2D uPuddleTex; uniform bool uHasPuddleAtlas;
uniform vec4 uPuddle0; uniform vec4 uPuddle1; uniform vec4 uPuddle2; uniform vec3 uPuddle3;
uniform vec4 uPuddleNoiseScale; uniform vec4 uPuddleRippleScale;
uniform int uGlowKind; uniform vec3 uGlowColour; uniform vec4 uGlowP; // depth, bias, environment scale, vertex multiplier
uniform sampler2D uTexGlowParallax; uniform bool uHasGlowParallax; uniform bool uSrgbGlowParallax; uniform bool uSrgbGlowMask;
uniform int uBngKind; uniform vec3 uBngColour; uniform vec4 uBngP;
uniform vec3 uBngDetail; uniform sampler2D uTexBngDetail; uniform bool uHasBngDetail;
uniform sampler2D uTexP; uniform bool uHasP; uniform bool uSrgbP;
uniform vec3 uPick;           // colour pick: scale, blend, palette u (mask = _c.a)
uniform vec3 uSpecTint;       // F0 multiplier (mo_gbl_color_tint_01 0x6B591974; 1 elsewhere)
uniform bool uTint; uniform vec3 uTintA; uniform vec3 uTintB;
uniform vec4 uTintP;          // power A, power B, weight B, _h mask on weight B
uniform int uTintVariant; // 0 base, 1 random, 2 detail, 3 world, 4 world normal
uniform vec3 uTintPalette; uniform float uTintPaletteBlend;
uniform vec3 uTintDetailP; // scale, normal strength, unused
uniform vec3 uTintWorldOrigin;
uniform sampler2D uTexTintDetail; uniform bool uHasTintDetail; uniform bool uSrgbTintDetail;
uniform float uTintM;         // weight A per unit _h
uniform sampler2D uTexH; uniform bool uHasH;
uniform bool uArchSequence; uniform sampler2D uTexSequence; uniform bool uHasSequence;
uniform vec3 uArchColour; uniform vec4 uArchP; // intensity, linear multiplier, mask bias, power
uniform bool uVehicle; uniform sampler2D uTexVehicleP2; uniform bool uHasVehicleP2; uniform bool uSrgbVehicleP2;
uniform vec3 uVehicleA; uniform vec3 uVehicleB; uniform vec3 uVehicleP; // palette blend, rim suppression
uniform float uArchPhase; // t=0; instance interpolants frozen to zero
uniform bool uFacingGlow; uniform vec4 uFacingGlowP; uniform vec3 uFacingGlowQ; // amplitude, exponent, a scale, a offset; bias, g scale
uniform bool uBrightCentre; uniform bool uBrightWhite; uniform vec3 uBrightEdge; uniform vec3 uBrightColour; uniform vec4 uBrightP; // D14A48D7: exponent, weight, F0, gloss^2
varying vec3 vPos; varying vec3 vNrm; varying vec2 vUV;
varying vec3 vTintWorldPos; varying vec3 vTintWorldNrm; varying vec3 vTangent; varying vec3 vTintViewOffset;
varying vec3 vTintWorldTan; varying float vTangentSign;
const float PI = 3.14159265;
vec3 linear(vec3 c) { return pow(max(c, 0.0), vec3(2.2)); }
// @lighting
#ifdef GL_ARB_shader_texture_lod
// 5FADAD33 rel764-920: positive counts, original IMAGE_SAMPLE_D UV gradients.
vec2 parallaxUV(vec2 uv,vec2 D,int count,vec2 dx,vec2 dy) {
    float inv=1.0/float(count),depth=1.0,oldHeight=1.0;
    float upper=0.0,lower=0.0,hu=0.0,hl=0.0;vec2 p=uv;
    for(int i=0;i<count;i++) {
        p-=D*inv;float h=texture2DGradARB(uTexParallaxHeight,p,dx,dy).a;
        float nextDepth=depth-inv;
        if(h>nextDepth){upper=depth;lower=nextDepth;hu=oldHeight;hl=h;break;}
        oldHeight=h;depth=nextDepth;
    }
    float du=upper-hu,dl=lower-hl,intersection=0.0;
    if(du!=dl)intersection=(du*lower-upper*dl)/(du-dl);
    return uv-(1.0-intersection)*D;
}
// rel972/1048-1312/1448-1588: no saturate; source may brighten albedo.
float parallaxShadow(vec2 uv,vec2 ls,vec2 dx,vec2 dy) {
    float base=texture2DGradARB(uTexParallaxHeight,uv,dx,dy).a;
    float a=2.0*(texture2DGradARB(uTexParallaxHeight,uv+0.7699999809265137*ls,dx,dy).a-base)+(-1.5399999618530273);
    a=max(a,4.0*(texture2DGradARB(uTexParallaxHeight,uv+0.6600000262260437*ls,dx,dy).a-base)+(-2.640000104904175));
    a=max(a,1.0*(texture2DGradARB(uTexParallaxHeight,uv+0.8799999952316284*ls,dx,dy).a-base)+(-0.8799999952316284));
    a=max(a,6.0*(texture2DGradARB(uTexParallaxHeight,uv+0.550000011920929*ls,dx,dy).a-base)+(-3.3000001907348633));
    a=max(a,8.0*(texture2DGradARB(uTexParallaxHeight,uv+0.4399999976158142*ls,dx,dy).a-base)+(-3.5199999809265137));
    a=max(a,10.0*(texture2DGradARB(uTexParallaxHeight,uv+0.33000001311302185*ls,dx,dy).a-base)+(-3.3000001907348633));
    a=max(a,12.0*(texture2DGradARB(uTexParallaxHeight,uv+0.2199999988079071*ls,dx,dy).a-base)+(-2.6399998664855957));
    return 1.0-0.6000000238418579*a;
}
#else
// No implicit-gradient fallback: viewport disables POM and exposes a capability warning.
vec2 parallaxUV(vec2 uv,vec2 D,int count,vec2 dx,vec2 dy){return uv;}
float parallaxShadow(vec2 uv,vec2 ls,vec2 dx,vec2 dy){return 1.0;}
#endif
vec3 ggx(vec3 N, vec3 V, vec3 L, vec3 F0, vec3 F90, float a) {
    vec3 H = normalize(L + V);
    float NdL = max(dot(N, L), 0.0), NdV = max(dot(N, V), 1e-4), NdH = max(dot(N, H), 0.0);
    float a2 = a * a, d = NdH * NdH * (a2 - 1.0) + 1.0;
    float D = a2 / (PI * d * d), k = a * 0.5;
    float G = NdL / (NdL * (1.0 - k) + k) * NdV / (NdV * (1.0 - k) + k);
    vec3 F = F0 + (F90 - F0) * pow(1.0 - max(dot(V, H), 0.0), 5.0);
    return D * G * F / max(4.0 * NdL * NdV, 1e-4) * NdL;
}
// Shared surface lighting: hand rules and native DAGs use identical closures.
vec3 deferredDiffuse(vec3 N, vec3 albedo) {
    if (!uLightingEnabled) return albedo;
#ifndef RC_SCENE_EFFECTS
    return albedo * (max(dot(N, rigKey()), 0.0) * keyColour()
        + max(dot(N, rigFill()), 0.0) * fillColour() + ambientLight(N)) / PI * diffuseScale();
#else
    sceneCaptureNormal=N;float A=sceneAO();float NdK=max(dot(N,rigKey()),0.0),Sh=sceneShadow(vPos);
    vec3 D=(NdK*keyColour()*Sh
        +max(dot(N,rigFill()),0.0)*fillColour()*(0.5+0.5*A)+ambientLight(N)*A*sceneShadowedAmbient(Sh,NdK))/PI*diffuseScale();
#ifdef RC_SCENE_EFFECTS
    if(uSceneEnabled)for(int i=0;i<32;i++)if(i<uSceneLightCount){
        vec3 delta=uSceneLightPos[i].xyz-vPos;float d=length(delta);
        vec3 L=delta/max(d,0.0001);float w=pow(clamp(1.0-d*uSceneLightPos[i].w,0.0,1.0),4.0);
        float nl=max(dot(N,L),0.0);D+=uSceneLightColour[i]*w*nl*nl*(0.5+0.5*A);
    }
#endif
    return albedo*D;
#endif
}
vec3 deferredSpecular(vec3 N, vec3 V, vec3 F0, float gloss) {
    if (!uLightingEnabled) return vec3(0.0);
    vec3 F90 = clamp(60.0 * F0, 0.0, 1.0);
    float a = pow(2.0, -5.5 * gloss);
#ifndef RC_SCENE_EFFECTS
    return ggx(N,V,rigKey(),F0,F90,a)*keyColour()
        + ggx(N,V,rigFill(),F0,F90,a)*fillColour()
        + envSpecular(N,V,F0,F90,a,ambientLight(N));
#else
    vec3 S=ggx(N,V,rigKey(),F0,F90,a)*keyColour()*sceneShadow(vPos)
        + ggx(N,V,rigFill(),F0,F90,a)*fillColour()
        + envSpecular(N,V,F0,F90,a,ambientLight(N))*sceneSpecularOcclusion(N,V,a);
#ifdef RC_SCENE_EFFECTS
    if(uSceneEnabled)for(int i=0;i<32;i++)if(i<uSceneLightCount){
        vec3 delta=uSceneLightPos[i].xyz-vPos;float d=length(delta);
        float w=pow(clamp(1.0-d*uSceneLightPos[i].w,0.0,1.0),4.0);
        S+=ggx(N,V,delta/max(d,0.0001),F0,F90,a)*uSceneLightColour[i]*w;
    }
#endif
    return S;
#endif
}
// DB75867B material and terminal terms; frozen runtime leaves are stored in metadata.
vec3 puddleShade(vec3 G, vec3 V, vec3 c, vec2 g) {
    vec3 wp = -vTintWorldPos; // CB0[28..30] origin frozen to zero; native Y-up
    vec2 q = wp.xz - vec2(0.5);
    float sn=sin(uPuddle3.z), cn=cos(uPuddle3.z);
    vec2 noiseUV = uPuddle2.z*(vec2(0.5)+vec2(cn*q.x-sn*q.y,sn*q.x+cn*q.y));
    float n=texture2D(uPuddleTex,fract(noiseUV)*uPuddleNoiseScale.xy+uPuddleNoiseScale.zw).r;
    float m=clamp((n-uPuddle0.x)/(uPuddle1.z-uPuddle0.x),0.0,1.0);
    float r=clamp((n-uPuddle0.z)/(uPuddle0.w-uPuddle0.z),0.0,1.0);
    float b=r+uPuddle2.y*(1.0-2.0*r);
    float h=sqrt(clamp(b+uPuddle2.x*g.x*(1.0-b),0.0,1.0));
    float gloss=-0.09090909361839294*log2(1.0-(1.0-exp2(-11.0*h))); // geometric AA=1
    vec3 F0=vec3(g.y*clamp(0.30000001192092896+uPuddle1.x*m,0.0,1.0));
    vec3 D=c*(1.0-n*uPuddle2.w*m);
    vec3 N=G; float face=gl_FrontFacing?1.0:-1.0;
    if (uHasN) {
        // Derivative TBN substitutes for the native tangent interpolant; same source vector equation.
        vec3 dp1=dFdx(vPos),dp2=dFdy(vPos);vec2 du1=dFdx(vUV),du2=dFdy(vUV);
        vec3 p2=cross(dp2,G),p1=cross(G,dp1);
        vec3 T=p2*du1.x+p1*du2.x, B=p2*du1.y+p1*du2.y;
        T=normalize(T);B=normalize(-B);
        vec2 xy=texture2D(uTexN,vUV).rg*2.0-1.0;
        vec3 baseN=face*normalize(T*xy.x+B*xy.y+G*(1.0-dot(xy,xy)));
        vec3 wn=normalize(vTintWorldNrm);vec3 an=abs(wn);vec2 p;
        bool yd=an.y>=an.x && an.y>=an.z, xd=an.x>=an.y && an.x>=an.z;
        // Match native independent initial selectors, including exact X/Y magnitude ties.
        p=vec2(xd?wp.z:wp.x,yd?-wp.z:-wp.y);
        if(xd)p.x*=-sign(face*wn.x);
        else if(yd)p.y=sign(face*wn.y)*wp.z;
        else p.x*=sign(face*wn.z);
        p-=vec2(.5);float a=uPuddle1.w*PI/180.0;
        // rel744: v10=B, v11=A; rel1512 samples (v5,v6), with V scroll (t=0).
        vec2 rippleUV=uPuddle0.y*(vec2(.5)+vec2(cos(a)*p.x-sin(a)*p.y,cos(a)*p.y+sin(a)*p.x));
        xy=(texture2D(uPuddleTex,fract(rippleUV)*uPuddleRippleScale.xy+uPuddleRippleScale.zw).rg*2.0-1.0)*clamp(uPuddle3.y,0.0,1.0);
        vec3 rippleN=face*normalize(T*xy.x+B*xy.y+G*(1.0-dot(xy,xy)));
        float rw=clamp((m-.5*uPuddle1.z)/(1.0-uPuddle1.z),0.0,1.0);
        N=mix(baseN,rippleN,rw); // source rel1892..1900 has NO final normalization
    } else N*=face;
    vec3 L1=rigKey(),L2=rigFill();
    vec3 C1=keyColour(),C2=fillColour();
    vec3 E=ambientLight(N);
#ifdef RC_SCENE_EFFECTS
    vec3 Dl=deferredDiffuse(N,vec3(1.0));
    vec3 S=deferredSpecular(N,V,F0,gloss);
#else
    vec3 Dl=(max(dot(N,L1),0.0)*C1+max(dot(N,L2),0.0)*C2+E)/PI*diffuseScale();
    vec3 F90=clamp(60.0*F0,0.0,1.0);float aa=exp2(-5.5*gloss);
    vec3 S=ggx(N,V,L1,F0,F90,aa)*C1+ggx(N,V,L2,F0,F90,aa)*C2;
    S+=envSpecular(N,V,F0,F90,aa,E);
#endif
    S=clamp(mix(S,F0*E,uPuddle3.x),0.0,1.0); // probe opacity=0, instance fade=0
    sceneCaptureNormal=N;return uLightingEnabled ? S+D*Dl : D; // unlit inspection; same source surface
}
void main() {
    if (uMode == 3) { gl_FragColor = vec4(uColor, 1.0); return; }
    vec3 N = normalize(vNrm);
    if (!gl_FrontFacing) N = -N;
    vec3 V = normalize(-vPos);
    float zaWeight = 1.0;
    if (uZaFresnel) { // E08357DC: CB2 dw2; use geometry normal before the tangent map
        zaWeight = 1.0 - clamp(pow(1.00100004673 - clamp(dot(N, V), 0.0, 1.0), uZaFresnelPower), 0.0, 1.0);
    }
    vec2 materialUV=vUV;float parallaxShadowWeight=1.0;
    if (uMode == 2 && uLiquidFlow) materialUV += uLiquidUV.xy;
    bool rockBreakup = uMode == 2 && uRockBlend && uRockBreakup.x > 0.5;
    if (rockBreakup) materialUV = vUV * uRockBreakup.z; // A3DAA047 rel448/472: U scales the layer g/n samples only
    vec3 pomT=vec3(0.0),pomB=vec3(0.0),pomN=normalize(vNrm);
    vec2 pomDx=dFdx(vUV),pomDy=dFdy(vUV);
    if(uMode==2 && uParallaxNormal) {
        vec3 dp1=dFdx(vPos),dp2=dFdy(vPos);
        vec3 t=cross(dp2,pomN)*pomDx.x+cross(pomN,dp1)*pomDy.x;
        vec3 b=cross(dp2,pomN)*pomDx.y+cross(pomN,dp1)*pomDy.y;
        float ndv=dot(pomN,V);
        int count=int(mix(uParallaxP.z,uParallaxP.y,clamp(ndv,0.0,1.0)));
        // Preview guard for singular rays/degenerate UV frames; absent from source.
        if(dot(t,t)>0.0 && dot(b,b)>0.0) {
            pomT=normalize(t);pomB=-normalize(b);
        }
        if(uParallax && uHasParallaxHeight && count>0 && ndv!=0.0 && dot(pomT,pomT)>0.0) {
            float face=gl_FrontFacing?1.0:-1.0;
            vec2 D=face*uParallaxP.x*uParallaxP.w*vec2(dot(pomT,V),-dot(pomB,V))/ndv;
            // Finite-ray preview guard; source does not define a controlled overflow policy.
            if(abs(D.x)<=3.4e38 && abs(D.y)<=3.4e38) {
            materialUV=parallaxUV(vUV,D,count,pomDx,pomDy);
            vec3 light=rigKey(); // viewer key replaces CB0[128..130]
            vec2 ls=1.25*uParallaxP.x*uParallaxP.w*vec2(dot(pomT,light),-dot(pomB,light));
            parallaxShadowWeight=parallaxShadow(materialUV,ls,pomDx,pomDy);
            }
        }
    }
    vec4 c = uHasC ? texture2D(uTexC, materialUV) : vec4(uColor, 0.0);
    if (uHasC && c.a < uAlphaCut) discard;
    vec3 albedo = (uMode >= 1 && uHasC) ? (uSrgbC ? linear(c.rgb) : c.rgb) : linear(uColor);
    if (uMode == 2 && uVictorReticle) {
        // EB9C8254 lowered deferred outputs: UNORM albedo, constant F0/gloss, and separate self-lit scalar.
        float d = length(vUV - vec2(0.5)), b = 1.0 - d, e = abs(b), ring = uReticleP.z;
        vec3 sourceAlbedo = 2.0 * uReticleP.w * vec3(e, e + ring * (pow(e, 10.0) - e),
            b*b + ring * (pow(e, 20.0) - b*b));
        albedo = clamp(sourceAlbedo, 0.0, 1.0); // MRT1 is normalized albedo.
    }

    if (uMode >= 1 && uFacingGlow) albedo = vec3(1.0); // 0CD2AAFB: no albedo input, v76-v78 = 1
    if (uMode == 2 && uBrightCentre && uBrightWhite) albedo = vec3(1.0); // preview override: white_c stands in for the unbound _c
    if (uMode == 2 && uFlatSurface) albedo = uBasicFlatColor ? vec3(0.0) : uFlatColour; // D169A814 has no diffuse albedo term
    if (uMode == 2 && uPuddles && uHasPuddleAtlas) {
        vec3 g=texture2D(uTexG,vUV).rgb;if(uSrgbG)g=linear(g);
        vec3 color=puddleShade(normalize(vNrm),V,albedo,g.rg);
        gl_FragColor=vec4(displayFog(color),1.0);return;
    }
    if (uMode == 2 && uHasC && uHasP && !uVehicle && !uTint) {
        // blarg_saucer_colorpick writer: albedo + blend*a*albedo*(scale*a*pick - 1); the f16 G-buffer
        // can go negative, clamped here for display.
        vec3 p = texture2D(uTexP, vec2(uPick.z, 0.5)).rgb;
        p = uSrgbP ? linear(p) : p;
        albedo = max(albedo + uPick.y * c.a * albedo * (uPick.x * c.a * p - 1.0), 0.0);
    }
    float rockWeight = 0.0; vec3 rockG = vec3(0.0);
    if (uMode == 2 && uRockBlend) {
        float h = texture3D(uRockTex, vec3(fract(vUV)*uRockScale0.xy, 0.125)).r;
        if (uRockSrgb.x > 0.5) h = linear(vec3(h)).r;
        float m = 1.0 - clamp((1.0-vRockAlpha)*(6.0-5.0*h), 0.0, 1.0);
        float target = m == 0.0 ? (uRockP.w == 0.0 ? 1.0 : 0.0) : pow(abs(m), uRockP.w);
        rockWeight = vRockAlpha + uRockP.z * (target-vRockAlpha);
        if (rockBreakup) {
            // A3DAA047 rel276-732: the only colour is one _c sample at native world (x, z) / T.
            // The level viewer's position is world space; the model viewer's is model space.
            if (uHasC) { vec3 cw = texture2D(uTexC, vTintWorldPos.xz * uRockBreakup.y).rgb; albedo = uSrgbC ? linear(cw) : cw; }
        } else {
            vec3 c2 = texture3D(uRockTex, vec3(fract(vUV)*uRockScale0.zw, 0.375)).rgb;
            if (uRockSrgb.y > 0.5) c2 = linear(c2);
            albedo = mix(albedo, c2, rockWeight);
            vec3 tinted = clamp(pow(abs(uRockTint*albedo), vec3(uRockP.x)), 0.0, 1.0);
            albedo = mix(albedo, tinted, uRockP.y);
        }
        vec3 g2 = texture3D(uRockTex, vec3(fract(materialUV)*uRockScale1.xy, 0.625)).rgb;
        if (uRockSrgb.z > 0.5) g2 = linear(g2);
        vec3 g1 = texture2D(uTexG, materialUV).rgb; if (uSrgbG) g1 = linear(g1);
        rockG = mix(g1, g2, rockWeight);
    }
    vec2 tintWorldUV = vUV; float tintDetailMask = 1.0;
    if (uMode == 2 && uTintVariant >= 3) {
        // 69424FA0 cached Fragment SPIR-V: native Y-up, dominant geometry normal projection.
        vec3 wn = normalize(vTintWorldNrm); if (!gl_FrontFacing) wn = -wn;
        vec3 wp = vTintWorldPos + uTintWorldOrigin; vec3 an = abs(wn);
        if (an.x >= an.y && an.x >= an.z) tintWorldUV = vec2(-sign(wn.x) * wp.z, -wp.y);
        else if (an.y >= an.x && an.y >= an.z) tintWorldUV = vec2(wp.x, sign(wn.y) * wp.z);
        else tintWorldUV = vec2(sign(wn.z) * wp.x, -wp.y);
        tintWorldUV *= uTintDetailP.x;
        float d = uHasTintDetail ? texture2D(uTexTintDetail, tintWorldUV).r : 0.5;
        if (uSrgbTintDetail) d = linear(vec3(d)).r;
        tintDetailMask = clamp(1.25 * clamp(5.0 * d - 0.5, 0.0, 1.0), 0.0, 1.0);
    }
    if (uMode == 2 && uHasC && uTint) {
        // mo_gbl_color_tint_01 writer: lerp(B', A', m), A' = c + m(sat((A c)^pA) - c),
        // B' = c + wB(1 - kB h)(sat((B c)^pB) - c), m = wA h.
        float h = uHasH ? texture2D(uTexH, materialUV).r : 0.0;
        float m = uTintM * h, w = uTintP.z * (1.0 - uTintP.w * h);
        vec3 ta = clamp(pow(max(abs(uTintA * albedo), vec3(1e-8)), vec3(uTintP.x)), 0.0, 1.0);
        vec3 tb = clamp(pow(max(abs(uTintB * albedo), vec3(1e-8)), vec3(uTintP.y)), 0.0, 1.0);
        vec3 a2 = albedo + m * (ta - albedo), b2 = albedo + w * (tb - albedo);
        albedo = b2 + m * (a2 - b2);
        if (uTintVariant >= 3) {
            vec3 low = 2.0 * albedo * tintDetailMask;
            vec3 high = 1.0 - 2.0 * (1.0 - albedo) * (1.0 - tintDetailMask);
            albedo = mix(high, low, vec3(lessThan(albedo, vec3(0.5))));
        }
        if (uTintVariant != 0) albedo *= 1.0 + uTintPaletteBlend * (uTintPalette - 1.0);
    }
    if (uMode == 2 && uVehicle) { // EB1AC2CD rel 628-828; both 1D palettes use UV0.x
        vec3 p1 = uHasP ? texture2D(uTexP, vec2(vUV.x, 0.5)).rgb : vec3(1.0);
        vec3 p2 = uHasVehicleP2 ? texture2D(uTexVehicleP2, vec2(vUV.x, 0.5)).rgb : vec3(1.0);
        p1 = uSrgbP ? linear(p1) : p1; p2 = uSrgbVehicleP2 ? linear(p2) : p2;
        float d = abs(1.00100004673 - clamp(dot(N, V), 0.0, 1.0));
        float f = pow(d, 1.20000004768);
        vec3 a = uVehicleA * p1, b = uVehicleB * p2;
        vec3 paint = albedo * (1.0 + uVehicleP.x * (a + 2.0 * f * (b - a) - 1.0))
                     * (1.0 - uVehicleP.y * 1.20000004768 * d);
        float mask = uHasH ? texture2D(uTexH, vUV).r : 0.0;
        albedo = clamp(albedo + mask * (paint - albedo), 0.0, 1.0);
    }
    float archSelfLit = 0.0;
    if (uMode == 2 && uArchSequence) { // AAED778C rel 572-744, CB table 1
        float h = uHasH ? texture2D(uTexH, vUV).r : 0.0;
        float mask = uHasP ? texture2D(uTexP, vUV).r : 0.0;
        float hp = h == 0.0 ? (uArchP.w == 0.0 ? 1.0 : 0.0) : pow(abs(h), uArchP.w);
        float q = uHasSequence ? texture2D(uTexSequence, vec2(uArchPhase, 0.5)).r : 0.0;
        albedo = clamp(uArchColour * (hp + 0.5 * uArchP.y * h), 0.0, 1.0);
        archSelfLit = 5.0 * q * hp * uArchP.x * (uArchP.z + mask);
    }
    vec2 glowParallaxUV = vUV;
    vec4 bngG = uHasG ? texture2D(uTexG, vUV) : vec4(0.0);
    vec3 bngGl = uSrgbG ? linear(bngG.rgb) : bngG.rgb;
    float bngSelfLit = bngGl.b * uK;
    float bngRimA = 0.0, bngRimB = 0.0;
    if (uBngKind == 1) albedo *= vec3(1.0) + bngGl.b * (uBngColour - vec3(1.0));
    if (uBngKind == 4) bngSelfLit = uK * albedo.r * (1.0 + bngGl.b * (uBngColour.r - 1.0));
    // A5343A0C: rim from the interpolated geometry normal, before the tangent map. 14B1CC68: _m.G * K.
    if (uBngKind == 7) bngRimB = pow(abs(1.00100004673 - clamp(dot(N, V), 0.0, 1.0)), uBngP.y);
    vec4 bngM = uHasH ? texture2D(uTexH, vUV) : vec4(0.0);
    if (uBngKind == 9 || uBngKind == 10) bngSelfLit = bngM.g * uK;
    if (uMode == 2 && uHasN && !uArchSequence && !uVehicle) {
        vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos); vec2 du1 = dFdx(vUV), du2 = dFdy(vUV);
        vec3 p2 = cross(dp2, N), p1 = cross(N, dp1);
        vec3 T = p2 * du1.x + p1 * du2.x, B = p2 * du1.y + p1 * du2.y;
        float scale = inversesqrt(max(max(dot(T, T), dot(B, B)), 1e-20));
        if (uBngKind == 8 && dot(vTangent, vTangent) > 0.0) {
            // 76C79AA4 reads the vertex tangent and its stored bitangent sign h (position w, as in bindings.py):
            // h * cross(N, T) points along -grad v, and B here is +grad v.
            vec3 Tv = vTangent - N * dot(N, vTangent);
            if (dot(Tv, Tv) > 1e-12) {
                vec3 Bv = cross(N, normalize(Tv));
                T = normalize(Tv); B = -sign(vTangentSign) * Bv; scale = 1.0;
            }
        }
        vec2 rg = texture2D(uTexN, materialUV).rg * 2.0 - 1.0;
        if (uTwoColorMetal) rg *= clamp(uMetalP.w, 0.0, 1.0);
        if (uHair) rg *= uHairP.w;   // 94CE8CCE: x, y = 0.3 (2 RG - 1) at the close-range fade s = 1
        float z = sqrt(max(0.0, 1.0 - dot(rg, rg)));
        if (uTwoColorMetal) z = 1.0 - dot(rg, rg); // 1012E99D: source fade 2, not a square root
        if (uBngKind > 0 || uLinearZ) z = 1.0 - dot(rg, rg);
        if (uSkinKind > 0) z = 1.0 - dot(rg, rg); // forward skin shaders F0CD85FF/BED06840: z = 1 - x*x - y*y
        if (uGlowKind > 0) z = 1.0 - dot(rg, rg); // 2685144F / CA81B14F normal z weight
        if (uGlowKind == 1) { // geometry TBN, before the normal map; close-range fade s=2
            float face = gl_FrontFacing ? 1.0 : -1.0;
            glowParallaxUV += uGlowP.x * face * vec2(dot(V, normalize(T)), -dot(V, normalize(-B)));
        }
        if (uParallaxNormal) z = 1.0 - dot(rg, rg); // rel1660-1680
        if (uFoliage || (uLeafColour && uLeafP.z > 0.5)) z = 1.0 - dot(rg, rg); // native normal, s=2; engine fade omitted
        if (uZaFresnel) z = 1.0 - dot(rg, rg); // E08357DC normal z weight, close-range fade s=2
        if (uHair) z = 1.0 - dot(rg, rg);      // 94CE8CCE normal z weight
        if (uTintVariant != 0) {
            z = 1.0 - dot(rg, rg); // native close-range normal, CB0[95]=1
            if ((uTintVariant == 2 && uHasTintDetail) || (uTintVariant == 4 && uHasP)) {
                vec2 d = uTintVariant == 2 ? texture2D(uTexTintDetail, materialUV * uTintDetailP.x).rg
                                         : texture2D(uTexP, tintWorldUV).rg;
                d = (d * 2.0 - 1.0) * clamp(uTintDetailP.y, 0.0, 1.0);
                float dz = 1.0 - dot(d, d);
                rg = rg * dz + 0.5 * z * d; z *= dz; // 63419EE2 / A7C802B8
            }
        }
        // Image "up" is decreasing t (top row first), so the bitangent is -B.
        if (uRockBlend) z = 1.0 - dot(rg, rg);
        vec3 baseN = normalize(T * scale * rg.x - B * scale * rg.y + N * z);
        if (uBngKind == 3 || uBngKind == 6 || uBngKind == 8 || uBngKind == 9) {
            float rim = 1.00100004673 - clamp(dot(baseN, V), 0.0, 1.0);
            bngRimA = pow(abs(rim), uBngP.x);
            bngRimB = pow(abs(rim), uBngP.y);
        }
        if ((uBngKind == 2 || uBngKind == 3 || uBngKind == 6 || uBngKind == 8 || uBngKind == 9 || uBngKind == 10 || uBngKind == 11) && uHasBngDetail) {
            // F3DCD431/F087BE8D/F8161C61: detail xy masked by _g.a^2 (emissive/specfresnel), _g.B (fresnel).
            // 14B1CC68 / E680AA40: _m.R; 76C79AA4: _c.a. The close-range fade CB0[95] (1 - s) is taken as 1.
            float mask = (uBngKind == 3 || uBngKind == 11) ? bngGl.b : (uBngKind == 9 || uBngKind == 10) ? bngM.r : uBngKind == 8 ? c.a : bngG.a * bngG.a;
            vec2 drg = (texture2D(uTexBngDetail, vUV * uBngDetail.xy).rg * 2.0 - 1.0) * mask;
            float dz = 1.0 - dot(drg, drg);
            rg = rg * dz + drg * z * uBngDetail.z;
            z *= dz;
        }
        if (uSkinKind == 1 && uHasBngDetail && uSkinC.y > 0.0) {
            // F0CD85FF: w = blend * _g.B; N = (x1 z2 + w z1 x2, y1 z2 + w z1 y2, z1 z2), second map at UV0 * scale.
            vec2 drg = texture2D(uTexBngDetail, vUV * uSkinC.y).rg * 2.0 - 1.0;
            float dz = 1.0 - dot(drg, drg);
            rg = rg * dz + drg * z * (uSkinC.z * bngGl.b);
            z *= dz;
        }
        if (uRockBlend) {
            vec2 r2 = texture3D(uRockTex, vec3(fract(materialUV)*uRockScale1.zw, 0.875)).rg*2.0-1.0;
            vec3 n2 = normalize(T*scale*r2.x - B*scale*r2.y + N*(1.0-dot(r2,r2)));
            N = normalize(mix(baseN, n2, rockWeight));
        } else N = normalize(T * scale * rg.x - B * scale * rg.y + N * z);
    }
    if(uMode==2 && uParallaxNormal && uTintVariant==0 && dot(pomT,pomT)>0.0 && uHasN) {
        vec2 xy=texture2D(uTexN,materialUV).rg*2.0-1.0;
        N=(gl_FrontFacing?1.0:-1.0)*normalize(pomT*xy.x+pomB*xy.y+pomN*(1.0-dot(xy,xy)));
    }
    if (uMode == 2 && uLeafColour && uHasCma) {
        // 0476D929 / CDD7CD94: F = (1.001 - sat(N.V))^CB2[0]; L = S * mix(c.rgb, cma.r, F); (Novalis material S; global literal 0.5)
        // albedo = sat(cmb < 0.5 ? 2 cmb L : 1 - 2 (1 - cmb)(1 - L)). Novalis uses geometry normal; global bushes use the pass-0 mapped normal.
        float ndv = clamp(dot(uLeafP.z > 0.5 ? N : normalize(vNrm), V), 0.0, 1.0);
        float fresnel = pow(1.00100004673 - ndv, uLeafP.x);
        float cma = texture2D(uTexCma, materialUV).r;
        if (uSrgbCma) cma = linear(vec3(cma)).r;
        vec3 leaf = uLeafP.y * mix(albedo, vec3(cma), fresnel);
        vec3 cmb = uLeafPalette;
        albedo = clamp(mix(2.0 * cmb * leaf, 1.0 - 2.0 * (1.0 - cmb) * (1.0 - leaf), step(0.5, cmb)), 0.0, 1.0);
    }
    if (uMode == 2 && uFresnelTint) {
        // Novalis glow mushroom, pixel program F69ADC40: F = (1.001 - sat(N.V))^E; MRT1 rg = mix(C.rg, _c.rg, F), b = C.b.
        float ndv = clamp(dot(normalize(vNrm), V), 0.0, 1.0);
        float fresnel = pow(1.00100004673 - ndv, uFresnelE);
        albedo = vec3(mix(uFresnelC.r, albedo.r, fresnel), mix(uFresnelC.g, albedo.g, fresnel), uFresnelC.b);
    }
    float skinF = 0.0;
    vec3 skinTinted = vec3(1.0);
    if (uMode == 2 && uSkinKind > 0) {
        // 9AAD640B / 0E6CC87B MRT1: F = (1.001 - sat(N.V))^P; albedo = sat(_c * mix(P_alb, tint, F)); N is the vertex normal.
        float ndv = clamp((gl_FrontFacing ? 1.0 : -1.0) * dot(normalize(vNrm), V), 0.0, 1.0);
        skinF = clamp(pow(abs(1.00100004673 - ndv), uSkinA.w), 0.0, 1.0);
        skinTinted = albedo * mix(vec3(uSkinB.x), vec3(0.745404183864593505859375, 0.644479691982269287109375, 0.60382735729217529296875), skinF);
        albedo = clamp(skinTinted, 0.0, 1.0);
    }
    vec3 metalSpecTint = vec3(1.0);
    float metalRim = 0.0;
    float hairW = 1.0;
    if (uMode == 2 && uHair) {
        // 94CE8CCE: albedo is the material vec3 A (_c is sampled for alpha only); w = 1 - sat((1.001 - |N.V|)^E) on the
        // geometry normal scales the gloss argument and the accumulated specular. ZA's w is taken before the tangent map.
        albedo = uHairA;
        hairW = 1.0 - clamp(pow(abs(1.00100004673 - clamp(dot(normalize(vNrm) * (gl_FrontFacing ? 1.0 : -1.0), V), 0.0, 1.0)), uHairP.x), 0.0, 1.0);
        metalSpecTint = vec3(hairW);
    }
    if (uMode == 2 && uTwoColorMetal) {
        N *= gl_FrontFacing ? 1.0 : -1.0;
        float b = 1.00100004673 - clamp(dot(N, V), 0.0, 1.0);
        float t = pow(b, uMetalP.x); // source t is not clamped
        metalRim = clamp(pow(b, uMetalP.y), 0.0, 1.0);
        metalSpecTint = clamp(uMetalP.z * albedo * mix(uMetalSpecB, uMetalSpecA, t), 0.0, 1.0);
        albedo *= mix(uMetalDiffuseB, uMetalDiffuseA, t);
    }
    if (uBngKind == 3) albedo *= 1.0 + uBngP.x * bngGl.b * bngRimB;
    if (uBngKind == 6) albedo *= 1.0 - bngRimB;
    if (uBngKind == 7) albedo *= 1.0 + uBngP.x * bngRimB;
    if (uBngKind == 8) albedo *= 1.0 + uBngP.x * c.a * bngRimB;
    if (uBngKind == 9) albedo *= 1.0 + uBngP.x * bngM.r * bngRimB;
    float brightSelfLit = 0.0;
    if (uMode == 2 && uBrightCentre) { // D14A48D7: t = (1.001 - sat(N.V))^e; albedo = _c * (E t + C w (1 - t))
        float t = pow(abs(1.00100004673 - clamp(dot(N, V), 0.0, 1.0)), uBrightP.x);
        vec3 f = uBrightEdge * t + uBrightColour * (uBrightP.y * (1.0 - t));
        brightSelfLit = uK * albedo.r * f.r;
        albedo *= f;
    }
    vec3 L1 = rigKey(), L2 = rigFill();
    vec3 C1 = keyColour(), C2 = fillColour();
    vec3 ambient = ambientLight(N);
    float glowMask = uHasH ? texture2D(uTexH, vUV).r : 0.0;
    if (uSrgbGlowMask) glowMask = linear(vec3(glowMask)).r;
    if (uMode == 2 && uGlowKind == 1) { // cached %7388-7412: _c.rgb is never sampled
        // Engine's blended environment irradiance is replaced by the viewer ambient.
        albedo = glowMask * (uGlowColour + uGlowP.z * ambient);
    }
    albedo *= parallaxShadowWeight; // source MRT1 shadow AFTER tint, not specular
    vec3 color = deferredDiffuse(N, albedo);
    if (uMode == 2) {
        vec4 g = uHasG ? texture2D(uTexG, materialUV) : vec4(0.0);
        vec3 gl = uSrgbG ? linear(g.rgb) : g.rgb;
        if (uHasG || uVehicle || uBrightCentre || uFlatSurface || uVictorReticle) {
            vec3 F0; float gloss;
            if (uGMode == 1 && uSkinKind == 1) { // 9AAD640B MRT2: grey F0 and gloss, each lerped to its Fresnel form by F
                float sp = uSkinB.z * gl.g, gp = uSkinB.y * gl.r;
                F0 = vec3(min(uSkinA.x, mix(sp, clamp(uSkinA.z * sp, 0.0, 1.0), skinF)));
                gloss = sqrt(clamp(mix(gp, clamp(uSkinA.y * gp, 0.0, 1.0), skinF), 0.0, 1.0));
            } else if (uGMode == 1 && uSkinKind == 2) { // 0E6CC87B MRT2: the grey term times the unclamped tinted albedo
                float sp = uSkinC.x * uSkinB.z * gl.g, gp = uSkinB.w * uSkinB.y * gl.r;
                F0 = clamp(min(uSkinA.x, mix(sp, clamp(uSkinA.z * sp, 0.0, 1.0), skinF)) * skinTinted, 0.0, 1.0);
                gloss = sqrt(clamp(mix(gp, clamp(uSkinA.y * gp, 0.0, 1.0), skinF), 0.0, 1.0));
            } else if (uGMode == 1) {   // skin writer: F0 = grey min(cap 0.5, G), gloss = sqrt(sat(R)) (graph defaults, F term at 0)
                F0 = vec3(min(0.5, gl.g)); gloss = sqrt(clamp(gl.r, 0.0, 1.0));
            } else if (uGMode == 2) {   // basic_normal_gloss, incandescence, saucer colour pick
                F0 = vec3(uSpecScale * gl.g); gloss = sqrt(clamp(uGlossScale * gl.r, 0.0, 1.0));
            } else {                    // coloured F0; gloss = sqrt(sat(scale * a^2)), = a at scale 1
                F0 = uSpecScale * gl; gloss = sqrt(clamp(uGlossScale * g.a * g.a, 0.0, 1.0));
            }
            if (uZaFresnel) { // clamp each scaled rim weight BEFORE multiplying by _g
                F0 = vec3(gl.g * clamp(uSpecScale * zaWeight, 0.0, 1.0));
                gloss = sqrt(clamp(gl.r * clamp(uGlossScale * zaWeight, 0.0, 1.0), 0.0, 1.0));
            }
            if (uHair) { F0 = uHairB * gl.g; gloss = sqrt(clamp(gl.r * clamp(uHairP.y * hairW, 0.0, 1.0), 0.0, 1.0)); }
            if (uBngKind == 6) {
                F0 = vec3(uSpecScale * gl.g * (1.0 + uBngP.z * bngRimA));
                gloss = sqrt(clamp(uGlossScale * (gl.r + bngRimA), 0.0, 1.0));
            }
            if (uVehicle) { F0 = vec3(0.0, 0.0999755859375, 0.0999755859375); gloss = 0.948681201424; } // packed rel 860, AA=1
            if (uBrightCentre) { F0 = vec3(uBrightP.z); gloss = sqrt(clamp(uBrightP.w, 0.0, 1.0)); }
            if (uFlatSurface) { F0 = vec3(uFlatSpec.x); gloss = sqrt(clamp(uFlatSpec.y, 0.0, 1.0)); }
            if (uVictorReticle) { F0 = uReticleF0; gloss = 0.0; }
            if (uTintVariant >= 3) gloss = sqrt(clamp(uGlossScale * gl.r * 2.0 * tintDetailMask, 0.0, 1.0));
            if (uRockBlend) { F0 = vec3(uSpecScale*rockG.g); gloss = sqrt(clamp(uGlossScale*rockG.r, 0.0, 1.0)); }
            if (uTwoColorMetal) gloss = sqrt(clamp(uGlossScale * gl.r * (1.0 - metalRim), 0.0, 1.0));
            F0 *= uSpecTint;
            vec3 F90 = clamp(60.0 * F0, 0.0, 1.0);
            float a = pow(2.0, -5.5 * gloss);
            // Two-color T multiplies the accumulated specular lighting after GGX/F90.
            color += metalSpecTint * deferredSpecular(N, V, F0, gloss);
        }
        if (uVictorReticle) {
            float sampleR = uHasReticle ? texture2D(uTexReticle, vec2(1.0, 8.0 - 8.0 * uReticleP.x)).r : 0.0;
            float selfLit = exp2(5.0 * (0.20000000298023224 * log2(1.0 + sampleR * uReticleP.y))) - 1.0;
            color += albedo * selfLit; // source deferred self-lit is separate from albedo and presentation amplitude.
        } else if (uGlowKind == 1) { // %7398-7440: mask and offset parallax R, K=0x06F19114
            float p = uHasGlowParallax ? texture2D(uTexGlowParallax, glowParallaxUV).r : 0.0;
            if (uSrgbGlowParallax) p = linear(vec3(p)).r;
            color += albedo * uK * glowMask * (uGlowP.y + p);
        } else if (uGlowKind == 2) { // CA81B14F rel 684-700; attr3.w magnitude frozen to 1
            color += albedo * (uK * albedo.r * glowMask * uGlowP.w);
        } else if (uBngKind == 1 || uBngKind == 2 || uBngKind == 4 || uBngKind == 6 || uBngKind == 9 || uBngKind == 10) color += albedo * bngSelfLit;
        else if (uArchSequence) color += albedo * archSelfLit;
        else if (uBrightCentre) color += albedo * brightSelfLit;
        else if (uFlatSurface) color += albedo * uK;
        else if (uHasC) color += albedo * (uSelfLitG ? gl.b : c.a) * uK;
        if (uBasicFlatColor) color += uFlatColour * uBasicFlatEmissionScale;
        if (uFacingGlow) { // 0CD2AAFB rel 12552-12672 (instance flag bit 5 clear: v8 = v9 = 1)
            float f = pow(max(clamp(dot(N, V), 0.0, 1.0) - uFacingGlowQ.x, 0.0), uFacingGlowP.y);
            float a = uFacingGlowP.z * f + uFacingGlowP.w;
            color += uFacingGlowP.x * vec3(2.0 * a * (1.0 + a), uFacingGlowQ.y * f * (1.0 + a), 0.0);
        }
    }
    gl_FragColor = vec4(displayFog(color), 1.0);
}
"""


# Forward (glass) writers, drawn after the opaque batches with blending and no depth write
# (texture_preview.GRAPH_RULES 'forward'). The decoded material terms are exact; the engine's
# light loop, probes, screen-space reflections and fog are replaced by FRAGMENT's viewer rig
# (same lights and ambient), and colour is tonemapped before blending, not after.
# uKind 1, character_glass 34D5B21C (enm_blarg_space_commando/evidence/glass/CHARACTER_GLASS_DECODE.md):
#   uA = body B.rgb, gloss; uB = tint.rgb, tint scale; uC = alpha exp, env scale k, alpha bias, rim bias;
#   uD.xy = env exp, rim exp. Instance fade b = 0, attr0.w = 1.
# uKind 2, blarg_saucer_glass FEEC00CE (enm_blarg_saucer/evidence/GLASS_DECODE.md):
#   uA = pattern tiling, pattern normal strength, F0 base, gloss; uB.xyz = pick u, rim bias, rim exp.
#   Pattern (_n slot) and palette (_pick) textures; instance fade b = 0, CB0[95] = attr0.w = 1,
#   SSR weight 0, both lighting instance flags (32 diffuse, 64 specular) on.
# uKind 4, water PREVIEW (pok_uniq_water family; textures.WATER_PRESENTATION), not a decode:
#   uA = body colour, opacity; uB = F0, normal strength, gloss; uC/uD = tile A/B UV scale.xy, offset.zw
#   applied to native world (x, z). Tiles: _n slot (water_tile_01n) and _pick slot (water_tile_02n).
# uKind 5, bright_glow 93695188 (hero_bright_center_light_glow_variable; no texture, additive, back faces culled):
#   uA = colour C, gain; uB = low colour Clo, sharpness S; uC = dist_k, dist0, blink weight w, amplitude.
#   t = |1.001 - N.V|^(0.2 (1 - S)); x = (1 - dist_k (|eye - p| - dist0)) w gain (1 - t); alpha = sat(x);
#   rgb = 7 amp (Clo + (C - Clo) x^2). Amplitude is CB0[115] (labelled presentation value, as facing_glow).
# uKind 6, eye_cornea A36A97CB (hero_eye_cornea; _n slot hemisphere_flatter_n, _pick slot shared_eye_lens_c; alpha-over, back faces culled):
#   uA = fade start, fade end, power near, power far; uB = reflect base, reflect weight, highlight-normal scale, env-normal scale;
#   uC.x = highlight k (key 0xF8828985). Three normals from the dome map (xy scale 0.555 for Fresnel, uB.z for the highlight
#   lobe, uB.w for the environment), linear z. F = sat(base + (1.001 - sat(N.V))^4); lobe = 4 sat(r.H)^p (1 - fade) with r the
#   mirror of V about the highlight normal, H = normalize(0.49 V + 0.51 L), p lerped power near->far by sat(0.08 |p|), fade =
#   sat((|p| - start) / (end - start)); alpha = sat(lobe + F + 0.06); rgb = env(r_env) (weight + (1.001 - N.V)^4) +
#   (lens.r + 1) lobe (4 - 3 k). L, the engine light term (1), instance terms and the environment roughness are labelled limits.
# Glass writers that share the engine light loop (uKind 1 and 8 to 10): the specular lobe is GGX with Schlick
# Fresnel F = F0 + (sat(60 F0) - F0)(1 - V.H)^5 and the Smith-Schlick visibility k = (0.353553 (alpha + 1))^2
# (glassSpec; constants read from 4FC5703F and 4DF1D438 and present at the same positions in 34D5B21C); the
# viewer rig replaces the light list, shadows, probes and fog (t = 1), and tone mapping precedes the blend.
# uKind 8, basic_glass 4FC5703F (decode: NoRuleDecodes/basic_glass): uA = tint.rgb, rim bias P3; uB = body.rgb, gloss;
#   uC = P8, rim exponent P9. rim = sat(4 (1 - N.V)^P9), alpha = sat(P3 + rim), F0 = sat(T / luma(T)) (P8 + rim^10),
#   rgb = body * L + specular. alpha = sat(alpha0 attr0.w), attr0.w = uD.x = material render-state byte 22 / 255 (the
#   captured draw has 255). Instance fade b = 0, flags 0x61 (diffuse and specular terms on).
# uKind 9, character_glass_02 4DF1D438: uA = body.rgb, gloss; uB = tint.rgb, tint scale; uC = env k, env exponent,
#   alpha floor, alpha exponent; uD = rim colour.rgb, rim exponent; uE = rim multiplier, rim cap, normal U, normal V.
#   X = B (1 + k P E S), P = (1.001 - N.V)^e, S = sat(4 sqrt(1 - N.V)); Q = clamp(m (C + X) (1.001 - sat(N'.V))^r + X, 0, cap);
#   alpha = sat(sat(floor + sat(4 (1 - N.V)^x))); rgb = Q * L + specular with F0 = sat(scale sat(T / luma(T))).
#   N' is the _n normal map (xy = 2t - 1, strength CB0[95] and fade taken as 1 and 0).
# uKind 10, jn_glass E7118623: uA = tint.rgb, rim bias P3; uB = colour C.rgb, weight w; uC = P8, rim exponent P9, gloss.
#   alpha = sat(P3 + rim); rgb = C + w * specular, F0 = sat(T / luma(T)) (P8 + rim^10); no body term in this table.
# uKind 11, wpn_thumper_glove_glass 5226F404 (docs/material-decoding/thumper-glove-glass.md): uA = tint.rgb, rim bias P3;
#   uB = body C.rgb, normal strength; uC = pattern tiling, P8, gloss, rim exponent P9; uD = glow colour K.rgb, amplitude.
#   Patterns_cma (_n slot) at uv * tiling gives the normal (xy = sat(strength) (2t - 1), z weight 1 - x^2 - y^2) and S = its
#   alpha; fx_noisetile02 (_pick slot) at (u/2, v/2) and (u/2, v/2 + 1/2) gives X = sat(0.454545 (n1 + n2) - 0.363636) with
#   CB0[99] = 0. rim = sat(4 (1 - N.V)^P9) from the geometry normal, alpha = sat(sat(X + sat(P3 + rim)) attr0.w),
#   F0 = P8 C S + rim^10, gloss = sat(gloss C.r S), tint k = sat(T / luma(T)). rgb = k specular(F0, N') + K amp (L + X):
#   the light loop is the one of 4FC5703F; amp is CB0[115] (labelled presentation value), fog, SSR and probes are the viewer rig.
# uKind 12, ion_tubes 86DDE345 (enm_victor_von_ion_tubes_w_rust; docs/material-decoding/victor-von-ion-tubes.md): uA = tint C
#   (CB2[0..2]) and gain g (CB2[3]); uB.x = amplitude (CB0[115], labelled presentation value); uC = base colour; uD = rim colour.
#   rgb = 2 amp (1 + m) (base + rim F^5 + m C g), m = sat(API6.R + API5.G) from the one electricity image, F = max(N.V - 0.001, 0);
#   uB.y = t = CB0[99] scrolls both bolt samples (game time in seconds: 0 for static renders, the viewer clock when animating);
#   uB.z = c = CB0[107], the dissolve threshold: m = sat((t5 + t6 - c) / max(1e-5, 1 - c)) (a new value each game frame:
#   0 = full streaks for static renders, frame_fade(t) when animating);
#   fog blend 1, CB2[4] = 0, vertex alpha 1 (opaque, back faces culled).
GLASS_FRAGMENT = """
#version 120
uniform sampler2D uWaterHeightA; uniform sampler2D uWaterHeightB; uniform bool uHasWaterHeights;
uniform sampler2D uScreenNoiseA; uniform sampler2D uScreenNoiseB;
uniform bool uScreenSrgbN; uniform bool uScreenSrgbA; uniform bool uScreenSrgbB;
uniform int uKind;
uniform vec4 uA; uniform vec4 uB; uniform vec4 uC; uniform vec4 uD; uniform vec4 uE;
uniform sampler2D uTexN; uniform bool uHasN;
uniform sampler2D uTexP; uniform bool uHasP; uniform bool uSrgbP;
varying vec3 vPos; varying vec3 vNrm; varying vec2 vUV;
varying vec3 vTintWorldPos;
const float PI = 3.14159265;
vec3 linear(vec3 c) { return pow(max(c, 0.0), vec3(2.2)); }
float sat(float x) { return clamp(x, 0.0, 1.0); }
float screenLinear(float x) { return x <= 0.04045 ? x / 12.92 : pow((x + 0.055) / 1.055, 2.4); }
// @lighting
vec3 ggx(vec3 N, vec3 V, vec3 L, vec3 F0, vec3 F90, float a) {
    vec3 H = normalize(L + V);
    float NdL = max(dot(N, L), 0.0), NdV = max(dot(N, V), 1e-4), NdH = max(dot(N, H), 0.0);
    float a2 = a * a, d = NdH * NdH * (a2 - 1.0) + 1.0;
    float D = a2 / (PI * d * d), k = a * 0.5;
    float G = NdL / (NdL * (1.0 - k) + k) * NdV / (NdV * (1.0 - k) + k);
    vec3 F = F0 + (F90 - F0) * pow(1.0 - max(dot(V, H), 0.0), 5.0);
    return D * G * F / max(4.0 * NdL * NdV, 1e-4) * NdL;
}
vec3 corneaNormal(vec3 N, vec3 T, vec3 B, float scale, vec2 xy) {   // A36A97CB: linear z, T/B from derivatives (as glass_patterned)
    return normalize(T * scale * xy.x - B * scale * xy.y + N * (1.0 - dot(xy, xy)));
}
// Light-loop lobe of the glass writers: alpha is GGX alpha, F90 = sat(60 F0), k = (0.353553 (alpha + 1))^2.
vec3 ggxGlass(vec3 N, vec3 V, vec3 L, vec3 F0, vec3 F90, float a) {
    vec3 H = normalize(L + V);
    float NdL = max(dot(N, L), 0.0), NdV = max(dot(N, V), 1e-4), NdH = max(dot(N, H), 0.0);
    float a2 = a * a, d = NdH * NdH * (a2 - 1.0) + 1.0, kk = 0.125 * (a + 1.0) * (a + 1.0);
    float DV = a2 / (PI * d * d * (NdV * (1.0 - kk) + kk) * (NdL * (1.0 - kk) + kk));
    vec3 F = F0 + (F90 - F0) * pow(1.0 - max(dot(V, H), 0.0), 5.0);
    return F * (0.25 * DV * NdL);
}
vec3 glassSpec(vec3 N, vec3 V, vec3 L1, vec3 L2, vec3 C1, vec3 C2, vec3 F0, float a, vec3 ambient) {
    vec3 F90 = clamp(60.0 * F0, 0.0, 1.0);
    return ggxGlass(N, V, L1, F0, F90, a) * C1 + ggxGlass(N, V, L2, F0, F90, a) * C2 + envSpecular(N, V, F0, F90, a, ambient);
}
void main() {
    vec3 N = normalize(vNrm) * (gl_FrontFacing ? 1.0 : -1.0);
    vec3 V = normalize(-vPos);
    float ndv = sat(dot(N, V));
    float om = max(1.0 - ndv, 1e-6);          // pow(0, 0) is undefined in GLSL
    vec3 L1 = rigKey(), L2 = rigFill();
    vec3 C1 = keyColour(), C2 = fillColour();
    vec3 color; float alpha;
    if (uKind == 6) { // A36A97CB eye cornea over the eye sphere (see the uKind 6 comment)
        vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos); vec2 du1 = dFdx(vUV), du2 = dFdy(vUV);
        vec3 p2 = cross(dp2, N), p1 = cross(N, dp1);
        vec3 T = p2 * du1.x + p1 * du2.x, B = p2 * du1.y + p1 * du2.y;
        float tb = inversesqrt(max(max(dot(T, T), dot(B, B)), 1e-20));
        vec2 nm = uHasN ? texture2D(uTexN, vUV).rg * 2.0 - 1.0 : vec2(0.0);
        vec3 Nf = corneaNormal(N, T, B, tb, 0.555 * nm);
        vec3 Nh = corneaNormal(N, T, B, tb, uB.z * nm);
        vec3 Ne = corneaNormal(N, T, B, tb, uB.w * nm);
        float F4 = pow(abs(1.00100004673 - sat(dot(Nf, V))), 4.0);
        float dist = length(vPos);
        float power = uA.z + sat(0.08 * dist) * (uA.w - uA.z);
        float fade = sat((dist - uA.x) / (uA.y - uA.x));
        vec3 H = normalize(0.49 * V + 0.51 * L1);
        float lobe = 4.0 * pow(sat(dot(reflect(-V, Nh), H)), power) * (1.0 - fade);
        alpha = sat(lobe + sat(uB.x + F4) + 0.06);
        float lens = uHasP ? texture2D(uTexP, vUV).r : 0.0;
        if (uSrgbP) lens = linear(vec3(lens)).r;
        float a = pow(2.0, -5.5);   // presentation roughness of the environment reflection (probe/SSR cone not decoded)
        color = envSpecular(Ne, V, vec3(1.0), vec3(1.0), a, ambientLight(Ne)) * (uB.y + F4)
              + (lens + 1.0) * lobe * (4.0 - 3.0 * uC.x);
    } else if (uKind == 8 || uKind == 10) { // basic_glass 4FC5703F / jn_glass E7118623
        float rim = sat(4.0 * pow(om, uC.y));
        alpha = sat(sat(uA.w + rim) * uD.x);   // attr0.w = material opacity byte 22 / 255
        float luma = max(dot(vec3(0.2126, 0.7152, 0.0722), uA.rgb), 1e-6);
        vec3 F0 = clamp(uA.rgb / luma, 0.0, 1.0) * (uC.x + pow(rim, 10.0));
        float gloss = uKind == 8 ? uB.w : uC.z;
        float a = pow(2.0, -5.5 * sqrt(sat(gloss)));
        vec3 ambient = ambientLight(N);
        vec3 Ld = (max(dot(N, L1), 0.0) * C1 + max(dot(N, L2), 0.0) * C2 + ambient) / PI * diffuseScale();
        vec3 S = glassSpec(N, V, L1, L2, C1, C2, F0, a, ambient);
        color = uKind == 8 ? uB.rgb * Ld + S : uB.rgb + uB.w * S;
    } else if (uKind == 11) { // wpn_thumper_glove_glass 5226F404 (see the uKind 11 comment)
        float rim = sat(4.0 * pow(om, uC.w));
        vec4 pat = uHasN ? texture2D(uTexN, vUV * uC.x) : vec4(0.5, 0.5, 0.0, 0.0);
        float nA = uHasP ? texture2D(uTexP, vec2(0.5 * vUV.x, 0.5 * vUV.y)).r : 0.0;
        float nB = uHasP ? texture2D(uTexP, vec2(0.5 * vUV.x, 0.5 * vUV.y + 0.5)).r : 0.0;
        float X = sat(0.45454546 * (nA + nB) - 0.36363637);
        alpha = sat(X + sat(uA.w + rim));
        if (uHasN) {   // Patterns_cma RG at uv * tiling, linear z weight (as uKind 2); tangent frame from screen derivatives
            vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos); vec2 du1 = dFdx(vUV), du2 = dFdy(vUV);
            vec3 p2 = cross(dp2, N), p1 = cross(N, dp1);
            vec3 T = p2 * du1.x + p1 * du2.x, B = p2 * du1.y + p1 * du2.y;
            float scale = inversesqrt(max(max(dot(T, T), dot(B, B)), 1e-20));
            vec2 xy = sat(uB.w) * (pat.rg * 2.0 - 1.0);
            N = normalize(T * scale * xy.x - B * scale * xy.y + N * (1.0 - dot(xy, xy)));
        }
        vec3 F0 = uC.y * uB.rgb * pat.a + vec3(pow(rim, 10.0));
        float a = pow(2.0, -5.5 * sqrt(sat(uC.z * uB.r * pat.a)));
        float luma = max(dot(vec3(0.2126, 0.7152, 0.0722), uA.rgb), 1e-6);
        vec3 ambient = ambientLight(N);
        vec3 Ld = (max(dot(N, L1), 0.0) * C1 + max(dot(N, L2), 0.0) * C2 + ambient) / PI * diffuseScale();
        color = clamp(uA.rgb / luma, 0.0, 1.0) * glassSpec(N, V, L1, L2, C1, C2, F0, a, ambient) + uD.rgb * uD.w * (Ld + vec3(X));
    } else if (uKind == 9) { // character_glass_02 4DF1D438
        if (uHasN) {
            vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos); vec2 du1 = dFdx(vUV), du2 = dFdy(vUV);
            vec3 p2 = cross(dp2, N), p1 = cross(N, dp1);
            vec3 T = normalize(p2 * du1.x + p1 * du2.x), B = normalize(-(p2 * du1.y + p1 * du2.y));
            vec2 xy = texture2D(uTexN, vUV * vec2(uE.z, uE.w)).rg * 2.0 - 1.0;
            N = normalize(T * xy.x + B * xy.y + N * sqrt(max(1.0 - dot(xy, xy), 0.0)));
            ndv = sat(dot(N, V)); om = max(1.0 - ndv, 1e-6);
        }
        alpha = sat(uC.z + sat(4.0 * pow(om, uC.w)));
        vec3 ambient = ambientLight(N);
        vec3 X = uA.rgb * (1.0 + uC.x * pow(1.001 - ndv, uC.y) * ambient * sat(4.0 * sqrt(om)));   // E = viewer sky
        vec3 Q = clamp(uE.x * (uD.rgb + X) * pow(max(1.001 - ndv, 1e-6), uD.w) + X, 0.0, uE.y);
        float luma = max(dot(vec3(0.2126, 0.7152, 0.0722), uB.rgb), 1e-6);
        vec3 F0 = clamp(uB.w * clamp(uB.rgb / luma, 0.0, 1.0), 0.0, 1.0);
        float a = pow(2.0, -5.5 * sqrt(sat(uA.w)));
        vec3 Ld = (max(dot(N, L1), 0.0) * C1 + max(dot(N, L2), 0.0) * C2 + ambient) / PI * diffuseScale();
        color = Q * Ld + glassSpec(N, V, L1, L2, C1, C2, F0, a, ambient);
    } else if (uKind == 3) { // 8716DC7A, time=0; cutoff=0, vertex alpha=fog fade=1
        vec2 uv = floor(vUV * uC.z) / uC.z;
        float h = uHasN ? texture2D(uTexN, uv).r : 0.0;
        if (uScreenSrgbN) h = screenLinear(h);
        float base = texture2D(uScreenNoiseA, vUV).a;
        alpha = sat(uC.x * base * (1.0 - h) + uC.y * h);
        vec3 tint = mix(uB.rgb, uA.rgb, h);
        float na = uHasP ? texture2D(uTexP, vUV).b : 0.0, nb = texture2D(uScreenNoiseB, vUV).b;
        if (uSrgbP) na = screenLinear(na);
        if (uScreenSrgbB) nb = screenLinear(nb);
        color = (1.0 + mix(uB.w, uA.w, h)) * (1.0 - (1.0-na)*(1.0-nb)*(1.0-tint));
    } else if (uKind == 12) { // 86DDE345 Victor von Ion tubes forward colour (see the uKind 12 comment)
        float t5 = uHasP ? texture2D(uTexP, vec2(1.0 - 2.0 * vUV.y + uB.y, 0.25 * vUV.x - uB.y)).g : 0.0;   // API5 .G
        float t6 = uHasP ? texture2D(uTexP, vec2(2.0 * vUV.x + 0.3, 0.5 * vUV.y - uB.y)).r : 0.0;    // API6 .R
        if (uSrgbP) { t5 = screenLinear(t5); t6 = screenLinear(t6); }
        float m = sat((t5 + t6 - uB.z) / max(0.00001, 1.0 - uB.z));
        float F = max(ndv - 0.00100004673, 0.0), F2 = F * F;
        color = 2.0 * uB.x * (1.0 + m) * (uC.rgb + uD.rgb * (F2 * F2 * F) + m * uA.w * uA.rgb);
        alpha = 1.0;
    } else if (uKind == 7) { // prisoner number forward colour (8CEC816B / 37C85F4C), time 0, fog fade and vertex alpha 1
        // uA = distort, b scale, vignette w, alpha-channel flag; uB.w = amplitude (CB0[115], presentation value); uC = Kc; uD = Ko.
        float b5 = uHasP ? texture2D(uTexP, vUV).b : 0.0;                                   // API5 at (u, v)
        float b6 = uHasN ? texture2D(uTexN, vec2(vUV.x, 2.0 * vUV.y)).b : 0.0;              // API6 at (u, 2v)
        float b7 = texture2D(uScreenNoiseB, vec2(vUV.x, 10.0 * vUV.y)).b;                   // API7 at (u, 10v)
        if (uSrgbP) b5 = screenLinear(b5);
        if (uScreenSrgbN) b6 = screenLinear(b6);
        if (uScreenSrgbB) b7 = screenLinear(b7);
        vec4 s8 = texture2D(uScreenNoiseA, vUV + uA.x * (b5 + b6));                          // API8, distorted
        if (uScreenSrgbA) s8.rgb = vec3(screenLinear(s8.r), screenLinear(s8.g), screenLinear(s8.b));
        float tt = (1.0 - uA.y * b7) * s8.r * (uA.w > 0.5 ? s8.a : s8.g);
        float gv = sat((1.0 - length(vec2(vUV.x - 0.5, 2.0 * vUV.y - 1.0)) - uA.z) / (1.0 - uA.z));
        color = 3.5 * uB.w * tt * (2.0 * uC.rgb * gv + uD.rgb);
        alpha = 1.0;
    } else if (uKind == 5) { // 93695188 forward colour; sin frozen at 0; cutoff 0, alpha 1, fog_fade 1
        float t = pow(abs(1.00100004673 - ndv), 0.2 * (1.0 - uB.w));
        float x = (1.0 - uC.x * (length(vPos) - uC.y)) * uC.z * uA.w * (1.0 - t);
        alpha = sat(x);
        color = uC.w * 7.0 * (uB.rgb + (uA.rgb - uB.rgb) * x * x);
    } else if (uKind == 4) {
        vec2 xz = vTintWorldPos.xz;
        vec2 d = vec2(0.0);
        if (uE.y > 0.5) {
            // First source sample branch of F4D47ECE/59540726. Frozen engine time=0;
            // native world xz substitutes for unresolved CB0/attribute delta inputs.
            if (uHasWaterHeights) {
                float h5 = texture2D(uWaterHeightA, xz * vec2(0.105, 0.095)).r;
                float h6 = texture2D(uWaterHeightB, xz * vec2(0.095, 0.105) + vec2(0.248, 0.613)).r;
                float crossHeight = uE.x * (h5 + h6 - 1.0);
                if (uHasN) d += texture2D(uTexN, xz * vec2(0.315, 0.285) + vec2(0.133, 0.346) + crossHeight).rg * 2.0 - 1.0;
                if (uHasP) d += texture2D(uTexP, xz * vec2(0.285, 0.315) + crossHeight).rg * 2.0 - 1.0;
            } // Missing height inputs retain geometry normal; never invent sampled heights.
        } else {
            if (uHasN) d += texture2D(uTexN, xz * uC.xy + uC.zw).rg * 2.0 - 1.0;
            if (uHasP) d += texture2D(uTexP, xz * uD.xy + uD.zw).rg * 2.0 - 1.0;
        }
        d *= uB.y;
        // Tangent frame of the planar (x, z) mapping from screen derivatives, as for the patterned glass.
        vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos); vec2 du1 = dFdx(xz), du2 = dFdy(xz);
        vec3 p2 = cross(dp2, N), p1 = cross(N, dp1);
        vec3 T = p2 * du1.x + p1 * du2.x, B = p2 * du1.y + p1 * du2.y;
        float scale = inversesqrt(max(max(dot(T, T), dot(B, B)), 1e-20));
        N = normalize(N + T * scale * d.x + B * scale * d.y);
        float a = pow(2.0, -5.5 * sqrt(sat(uB.z)));
        vec3 F0 = vec3(uB.x), F90 = vec3(1.0);
        float F = uB.x + (1.0 - uB.x) * pow(1.0 - sat(dot(N, V)), 5.0);
        vec3 ambient = ambientLight(N);
        vec3 Ld = (max(dot(N, L1), 0.0) * C1 + max(dot(N, L2), 0.0) * C2 + ambient) / PI * diffuseScale();
        vec3 S = ggx(N, V, L1, F0, F90, a) * C1 + ggx(N, V, L2, F0, F90, a) * C2 + envSpecular(N, V, F0, F90, a, ambient);
        color = uA.rgb * Ld * (1.0 - F) + S;
        alpha = mix(uA.w, 1.0, F);
    } else if (uKind == 2) {
        float rim = sat(4.0 * pow(om, uB.z));
        alpha = sat(uB.y + rim);
        vec3 pick = uHasP ? texture2D(uTexP, vec2(uB.x, 0.5)).rgb : vec3(0.0);
        pick = uSrgbP ? linear(pick) : pick;
        vec3 F0 = vec3(greaterThan(pick, vec3(0.0))) * (uA.z + pow(rim, 10.0));   // sat(inf * pick) * (P2 + rim^10)
        vec3 F90 = vec3(greaterThan(F0, vec3(0.0)));
        if (uHasN) {   // Patterns_cma RG at uv * tiling: xy = sat(strength) * (2t - 1), z weight 1 - x^2 - y^2
            vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos); vec2 du1 = dFdx(vUV), du2 = dFdy(vUV);
            vec3 p2 = cross(dp2, N), p1 = cross(N, dp1);
            vec3 T = p2 * du1.x + p1 * du2.x, B = p2 * du1.y + p1 * du2.y;
            float scale = inversesqrt(max(max(dot(T, T), dot(B, B)), 1e-20));
            vec2 xy = sat(uA.y) * (texture2D(uTexN, vUV * uA.x).rg * 2.0 - 1.0);
            N = normalize(T * scale * xy.x - B * scale * xy.y + N * (1.0 - dot(xy, xy)));
        }
        vec3 ambient = ambientLight(N);
        float a = pow(2.0, -5.5 * sqrt(sat(uA.w)));
        vec3 Ld = (max(dot(N, L1), 0.0) * C1 + max(dot(N, L2), 0.0) * C2 + ambient) / PI * diffuseScale();
        vec3 S = ggx(N, V, L1, F0, F90, a) * C1 + ggx(N, V, L2, F0, F90, a) * C2
               + envSpecular(N, V, F0, F90, a, ambient);
        color = S + 0.400295 * pick * Ld;
    } else {
        alpha = sat(pow(1.001 - ndv, uC.x) * (uC.z + sat(uC.w + sat(4.0 * pow(om, uD.y)))));
        vec3 tint = clamp(uB.w * clamp(uB.rgb / max(dot(vec3(0.2126, 0.7152, 0.0722), uB.rgb), 1e-6), 0.0, 1.0), 0.0, 1.0);
        vec3 ambient = ambientLight(N);
        float a = pow(2.0, -5.5 * sqrt(sat(uA.w)));
        vec3 X = uA.rgb * (1.0 + uC.y * pow(1.001 - ndv, uD.x) * ambient * sat(4.0 * sqrt(om)));   // E = viewer sky
        vec3 Ld = (max(dot(N, L1), 0.0) * C1 + max(dot(N, L2), 0.0) * C2 + ambient) / PI * diffuseScale();
        vec3 R = glassSpec(N, V, L1, L2, C1, C2, tint, a, ambient);   // F0 = tint, F90 = sat(60 tint), as 4DF1D438
        color = R + X * Ld;
    }
    gl_FragColor = vec4(displayFog(color), alpha);
}
"""
# Scene geometry uses the same material evaluation/discard path as the displayed surface.
# The macro is emitted only for the level scene programs; model programs retain one output.
import re as _re
_SCENE_OUTPUT = """
vec3 sceneCaptureNormal;
#ifdef RC_SCENE_EFFECTS
uniform bool uSceneGeometry;
#endif
void sceneOutput(vec4 color){
#ifdef RC_SCENE_EFFECTS
 if(uSceneGeometry){gl_FragData[0]=vec4(normalize(sceneCaptureNormal)*0.5+0.5,1.0);gl_FragData[1]=vec4(max(-vPos.z,0.0));return;}
 gl_FragData[0]=color;
#else
 gl_FragColor=color;
#endif
}
"""
FOG = """
// Level atmosphere height fog (rc2016.levels.fog): the game's analytic fog, per fragment. The ramps
// (a, b, alpha, beta) and the start distance are camera-relative per-frame values; uFogOn 0 is no fog.
// uViewToWorld turns the view-space position into a camera-relative offset in the Z-up presentation
// basis, whose Z is the game's native Y, the height the profile is a function of. Applied to the lit
// colour before exposure and tonemap, the same units as the sky dome.
uniform int uFogOn; uniform vec3 uFogColour;
uniform vec4 uFogA; uniform vec4 uFogB; uniform vec4 uFogAlpha; uniform vec4 uFogBeta;
uniform vec4 uFogMisc;   // horizontal-ray c88, c89, exponent, start distance
float fogTransmittance() {
    vec3 rel = uViewToWorld * vPos;
    float dist = length(rel);
    float dy = rel.z;
    float tau;
    if (abs(dy) < 0.05) {
        tau = uFogMisc.x * dist + uFogMisc.y;   // near-horizontal: constant density, avoids a 1/dy blow-up in float
    } else {
        float hs = uFogMisc.w * dy / dist;
        vec4 ramp_s = clamp(uFogA * hs + uFogB, 0.0, 1.0);
        vec4 ramp_e = clamp(uFogA * dy + uFogB, 0.0, 1.0);
        vec4 part = uFogAlpha * (ramp_e * ramp_e - ramp_s * ramp_s) + uFogBeta * (ramp_e - ramp_s);
        tau = dist / dy * (part.x + part.y + part.z + part.w);
    }
    if (tau > 0.0) tau = pow(tau, uFogMisc.z);
    return clamp(exp2(-tau), 0.0, 1.0);
}
vec3 displayFog(vec3 color) {
    if (uFogOn != 0) color = mix(uFogColour, color, fogTransmittance());
    return display(color);
}
"""
FRAGMENT=_re.sub(r'gl_FragColor\s*=\s*([^;]+);',r'sceneOutput(\1);',FRAGMENT)
FRAGMENT=FRAGMENT.replace('vec3 N = normalize(vNrm);','vec3 N = normalize(vNrm); sceneCaptureNormal=N;',1)
FRAGMENT = FRAGMENT.replace("// @lighting\n", LIGHTING+FOG+_SCENE_OUTPUT)
GLASS_FRAGMENT = GLASS_FRAGMENT.replace("// @lighting\n", LIGHTING+FOG)
GLASS_KINDS = {"character_glass": 1, "glass_patterned": 2, "screen": 3, "water": 4, "bright_glow": 5,
               "eye_cornea": 6, "number_display": 7, "glass_basic": 8, "glass_cg02": 9, "glass_jn": 10, "glass_thumper": 11,
               "ion_tubes": 12}


def frame_fade(time, rate):
    """Presentation stand-in for the per-frame CB0[107]: a value in [0, 1) hashed from the game frame index floor(time * rate).

    The capture shows a new, uncorrelated value each frame; the engine's generator is unknown, so this is not its sequence."""
    n = int(time * rate) & 0xFFFFFFFF
    for _ in range(2):
        n = ((n ^ (n >> 16)) * 0x45D9F3B) & 0xFFFFFFFF
    return (n ^ (n >> 16)) / 4294967296.0


def glass_uniforms(forward, time=None):
    """(uKind, uA, uB, uC, uD) for a texture_preview 'forward' entry.

    ``time`` is the model view's clip clock for the time-driven kinds: it replaces the ion tubes' static CB0[99], and for
    the ion tubes and the number displays the static CB0[107] by the per-frame ``frame_fade``; None keeps the static values."""
    p = forward["params"]
    if forward["kind"] == "screen":
        return (3, (*p["colour_a"], p["intensity_a"]), (*p["colour_b"], p["intensity_b"]),
                (p["alpha_base"], p["mask_strength"], p["grid"], 0.0), (0.0,) * 4)
    if forward["kind"] == "number_display":
        w = p["vignette_w"] if time is None else p["vignette_w"] + p["vignette_slope"] * frame_fade(time, p["fade_frame_rate"])
        return (GLASS_KINDS["number_display"], (p["distort"], p["b_scale"], w, 1.0 if p["alpha_channel"] else 0.0), (0.0, 0.0, 0.0, p["amplitude"]),
                (*p["kc"], 0.0), (*p["ko"], 0.0))
    if forward["kind"] == "ion_tubes":
        clock = (p["time"], p["fade"]) if time is None else (time, frame_fade(time, p["fade_frame_rate"]))
        return (GLASS_KINDS["ion_tubes"], (*p["colour"], p["gain"]), (p["amplitude"], *clock, 0.0),
                (*p["base"], 0.0), (*p["rim"], 0.0))
    if forward["kind"] == "bright_glow":
        return (5, (*p["colour"], p["gain"]), (*p["colour_lo"], p["sharp"]),
                (p["dist_k"], p["dist0"], p["weight"], p["amplitude"]), (0.0,) * 4)
    if forward["kind"] == "eye_cornea":
        return (6, (p["fade_start"], p["fade_end"], p["power_near"], p["power_far"]),
                (p["reflect_base"], p["reflect_weight"], p["highlight_normal"], p["env_normal"]),
                (p["highlight_k"], 0.0, 0.0, 0.0), (0.0,) * 4)
    if forward["kind"] == "water":
        sampling = forward.get("sampling") or {}
        return (4, (*p["body"], p["opacity"]), (p["f0"], p["normal_strength"], p["gloss"], 0.0),
                tuple(p["uv_a"]), tuple(p["uv_b"]),
                (float(sampling.get("displacement", 0.0)), 1.0 if sampling else 0.0, 0.0, 0.0))
    if forward["kind"] == "glass_basic":
        return (GLASS_KINDS["glass_basic"], (*p["tint"], p["rim_bias"]), (*p["body"], p["gloss"]), (p["f0_base"], p["rim_exp"], 0.0, 0.0), (p["alpha_scale"], 0.0, 0.0, 0.0), (0.0,) * 4)
    if forward["kind"] == "glass_jn":
        return (GLASS_KINDS["glass_jn"], (*p["tint"], p["rim_bias"]), (*p["colour"], p["weight"]), (p["f0_base"], p["rim_exp"], p["gloss"], 0.0),
                (p["alpha_scale"], 0.0, 0.0, 0.0), (0.0,) * 4)
    if forward["kind"] == "glass_thumper":
        return (GLASS_KINDS["glass_thumper"], (*p["tint"], p["rim_bias"]), (*p["body"], p["normal_strength"]),
                (p["tiling"], p["f0_base"], p["gloss"], p["rim_exp"]), (*p["glow_colour"], p["amplitude"]), (0.0,) * 4)
    if forward["kind"] == "glass_cg02":
        return (GLASS_KINDS["glass_cg02"], (*p["body"], p["gloss"]), (*p["tint"], p["tint_scale"]),
                (p["env_scale"], p["env_exp"], p["alpha_floor"], p["alpha_exp"]), (*p["rim_colour"], p["rim_exp"]),
                (p["rim_mult"], p["rim_cap"], p["normal_u"], p["normal_v"]))
    if forward["kind"] == "glass_patterned":
        return (2, (p["tiling"], p["normal_strength"], p["f0"], p["gloss"]),
                (p["pick_u"], p["rim_bias"], p["rim_exp"], 0.0), (0.0,) * 4, (0.0,) * 4)
    return (1, (*p["body"], p["gloss"]), (*p["tint"], p["tint_scale"]),
            (p["alpha_exp"], p["env_scale"], p["alpha_bias"], p["rim_bias"]), (p["env_exp"], p["rim_exp"], 0.0, 0.0))


OPAQUE_UNIFORMS = (
    "uPuddles", "uPuddleTex", "uHasPuddleAtlas", "uPuddle0", "uPuddle1", "uPuddle2", "uPuddle3", "uPuddleNoiseScale", "uPuddleRippleScale",
    "uRockBlend", "uRockTex", "uRockSrgb", "uRockScale0", "uRockScale1", "uRockTint", "uRockP", "uRockBreakup",
    "uSkin", "uBoneTex", "uBoneWidth", "uMode", "uColor", "uTexC", "uTexG", "uTexN", "uHasC", "uHasG", "uHasN",
    "uSrgbC", "uSrgbG", "uGMode", "uSpecScale", "uGlossScale", "uSelfLitG", "uK",
    "uTwoColorMetal", "uMetalDiffuseA", "uMetalDiffuseB", "uMetalSpecA", "uMetalSpecB", "uMetalP",
    "uParallax", "uParallaxNormal", "uHasParallaxHeight", "uTexParallaxHeight", "uParallaxP",
    "uFoliage", "uAlphaCut", "uLeafColour", "uLeafP", "uLeafPalette", "uTexCma", "uHasCma", "uSrgbCma",
    "uFresnelTint", "uFresnelC", "uFresnelE", "uLiquidFlow", "uLiquidUV", "uFlatSurface", "uFlatColour", "uFlatSpec", "uBasicFlatColor", "uBasicFlatEmissionScale", "uVictorReticle", "uTexReticle", "uHasReticle", "uReticleP", "uReticleF0",
    "uHair", "uHairA", "uHairB", "uHairP",
    "uSkinKind", "uSkinA", "uSkinB", "uSkinC",
    "uLinearZ", "uZaFresnel", "uZaFresnelPower",
    "uGlowKind", "uGlowColour", "uGlowP", "uTexGlowParallax", "uHasGlowParallax", "uSrgbGlowParallax", "uSrgbGlowMask",
    "uVehicle", "uTexVehicleP2", "uHasVehicleP2", "uSrgbVehicleP2", "uVehicleA", "uVehicleB", "uVehicleP",
    "uBngKind", "uBngColour", "uBngP", "uBngDetail", "uTexBngDetail", "uHasBngDetail",
    "uTexP", "uHasP", "uSrgbP", "uPick", "uSpecTint", "uTint", "uTintA", "uTintB", "uTintP", "uTintM",
    "uTintVariant", "uTintPalette", "uTintPaletteBlend", "uTintDetailP", "uTintWorldOrigin",
    "uTexTintDetail", "uHasTintDetail", "uSrgbTintDetail", "uTexH", "uHasH", "uArchSequence", "uTexSequence", "uHasSequence", "uArchColour", "uArchP", "uArchPhase",
    "uFacingGlow", "uFacingGlowP", "uFacingGlowQ", "uBrightCentre", "uBrightWhite", "uBrightEdge", "uBrightColour", "uBrightP",
    "uSceneGeometry", *LIGHTING_UNIFORMS)
GLASS_UNIFORMS = ("uSkin", "uBoneTex", "uBoneWidth", "uKind", "uA", "uB", "uC", "uD", "uE",
                  "uWaterHeightA", "uWaterHeightB", "uHasWaterHeights", "uTexN", "uHasN", "uTexP", "uHasP", "uSrgbP",
                  "uScreenNoiseA", "uScreenNoiseB", "uScreenSrgbN", "uScreenSrgbA", "uScreenSrgbB", *LIGHTING_UNIFORMS)

# Level viewer: one draw per model and material for all its instances. The zone's row-vector
# instance matrix arrives as four per-instance attributes (rows; docs/levels.md), in texcoord
# 2-5's aliased slots, which no shader reads. Geometry and matrices stay in native Y-up space;
# the camera matrix carries the presentation basis.
INSTANCE_ATTRIBUTES = {"aInst0": 10, "aInst1": 11, "aInst2": 12, "aInst3": 13, "aRockAlpha": ROCK_ALPHA_ATTRIBUTE, "aGroundBlend": GROUND_BLEND_ATTRIBUTE, "aStaticPackedVector": STATIC_VECTOR_ATTRIBUTE}
INSTANCED_VERTEX = """
#version 120
attribute vec4 aInst0; attribute vec4 aInst1; attribute vec4 aInst2; attribute vec4 aInst3;
attribute float aRockAlpha; attribute vec3 aGroundBlend;
attribute vec3 aStaticPackedVector;
varying vec3 vGroundBlend;
varying vec3 vPos; varying vec3 vNrm; varying vec2 vUV;
varying vec3 vTintWorldPos; varying vec3 vTintWorldNrm; varying vec3 vTintViewOffset; varying vec3 vTintWorldTan;
varying float vRockAlpha; varying vec3 vStaticPackedVector;
varying vec3 vTangent; varying float vTangentSign;   // the level viewer draws no tangent stream: zero keeps the derivative frame
void main() {
    vTangent = vec3(0.0); vTintWorldTan = vec3(0.0); vTangentSign = 0.0;
    vRockAlpha = aRockAlpha * aRockAlpha; // zone position-keyed colour alpha (inferred), else 1
    vGroundBlend = vec3(aGroundBlend.xy * aGroundBlend.xy * aGroundBlend.z, aGroundBlend.z);
    vec3 a = aInst0.xyz, b = aInst1.xyz, c = aInst2.xyz;
    vec3 p = gl_Vertex.x * a + gl_Vertex.y * b + gl_Vertex.z * c + aInst3.xyz;
    // Columns b x c, c x a, a x b = det * inverse-transpose: exact for the zones' sheared and
    // mirrored instances once the determinant's sign is restored.
    vec3 n = mat3(cross(b, c), cross(c, a), cross(a, b)) * gl_Normal * sign(dot(a, cross(b, c)));
    float la = dot(a, a), lb = dot(b, b), lc = dot(c, c);
    float det = dot(a, cross(b, c));
    mat3 cofactors = mat3(cross(b, c), cross(c, a), cross(a, b));
    // Experimental source-selector preview: compare squared axis lengths, then
    // use the source CPU helper's linear fast path or inverse-transpose branch.
    if (abs(la - lb) < 0.001 && abs(la - lc) < 0.001) {
        vStaticPackedVector = aStaticPackedVector.x * a + aStaticPackedVector.y * b + aStaticPackedVector.z * c;
    } else if (abs(det) >= 0.00000095367431640625) {
        vStaticPackedVector = (cofactors * aStaticPackedVector) / det;
    } else {
        vStaticPackedVector = cofactors * aStaticPackedVector;
    }
    vTintWorldPos = p; vTintWorldNrm = n;   // native Y-up world space, as the tint writers read it
    vec4 view = gl_ModelViewMatrix * vec4(p, 1.0);
    vTintViewOffset = (gl_ModelViewMatrixInverse * vec4(view.xyz, 0.0)).xyz;   // p minus the camera, native world
    vPos = view.xyz; vNrm = gl_NormalMatrix * n; vUV = gl_MultiTexCoord0.xy;
    gl_Position = gl_ProjectionMatrix * view;
}
"""
# Level viewer click-to-identify: each model drawn in a flat id colour into the back buffer.
# Leaf cut-out materials sample `_c` alpha so a click passes through their holes (uPickCut = 0: off).
PICK_FRAGMENT = """
#version 120
uniform vec3 uPickColour;
uniform sampler2D uTexC; uniform float uPickCut;
varying vec2 vUV;
void main() {
    if (uPickCut > 0.0 && texture2D(uTexC, vUV).a < uPickCut) discard;
    gl_FragColor = vec4(uPickColour, 1.0);
}
"""
# Level sky (rc2016/levels/sky.py), drawn around the camera before the level: native Y-up
# positions (the modelview holds only the camera rotation), DirectX-convention UV0.
SKY_VERTEX = """
#version 120
varying vec3 vDir; varying vec2 vUV;
void main() { vDir = gl_Vertex.xyz; vUV = gl_MultiTexCoord0.xy; gl_Position = ftransform(); }
"""
# uSkyMode 0: clear sky, colour by elevation from the profile (uProfile, uProfileStep degrees per bin).
# uSkyMode 1: port of the Rk_Sky_Dome cloud fragment shader 0x8d3c81d0. uP[0..5] hold its constant
# dwords 0-22; uSunRay is the atmosphere's sun ray (native). Engine inputs use presentation values:
# time 0, alpha multiplier 1, alpha-test threshold 0. The stars term (dword 22) is not ported. The shader
# ends in mix(fogColour, lit, T) with T a vertex output (undecoded: the dome's vertex shader is not in the
# cache). Inferred, not decoded: T is the level's height fog (uFog*, rc2016.levels.fog) of a point
# SKY_FOG_DISTANCE along the view ray from the camera, i.e. the dome at infinity.
SKY_FRAGMENT = """
#version 120
const float PI = 3.14159265;
vec3 linear(vec3 c) { return pow(max(c, 0.0), vec3(2.2)); }
// @lighting
varying vec3 vDir; varying vec2 vUV;
uniform int uSkyMode;
uniform vec3 uProfile[15]; uniform float uProfileStep;
uniform sampler2D uDome; uniform sampler2D uNoise; uniform bool uDomeSrgb; uniform bool uNoiseSrgb;
uniform vec4 uP[6]; uniform vec3 uSunRay;
uniform int uFogOn; uniform vec3 uFogColour;
uniform vec4 uFogA; uniform vec4 uFogB; uniform vec4 uFogAlpha; uniform vec4 uFogBeta;
uniform vec4 uFogMisc;   // as in FOG: horizontal-ray c88, c89, exponent, start distance
const float SKY_FOG_DISTANCE = 10000.0;   // beyond about 3000 units the result no longer changes (Novalis)
// The FOG transmittance of the point SKY_FOG_DISTANCE away along the native direction dir (y is height).
float skyFogTransmittance(vec3 dir) {
    float dist = SKY_FOG_DISTANCE, dy = dist * dir.y, tau;
    if (abs(dy) < 0.05) {
        tau = uFogMisc.x * dist + uFogMisc.y;
    } else {
        float hs = uFogMisc.w * dy / dist;
        vec4 ramp_s = clamp(uFogA * hs + uFogB, 0.0, 1.0);
        vec4 ramp_e = clamp(uFogA * dy + uFogB, 0.0, 1.0);
        vec4 part = uFogAlpha * (ramp_e * ramp_e - ramp_s * ramp_s) + uFogBeta * (ramp_e - ramp_s);
        tau = dist / dy * (part.x + part.y + part.z + part.w);
    }
    if (tau > 0.0) tau = pow(tau, uFogMisc.z);
    return clamp(exp2(-tau), 0.0, 1.0);
}
// Sky-only display transform (visual calibration, not decoded; docs/levels.md "Sky"): Khronos PBR Neutral
// (as display() with uTonemap 1) at SKY_EXPOSURE. Against one in-game Novalis frame the captured clear sky
// (probe units) came out 0.8 to 0.85 of the displayed linear value in every channel, which a near-linear curve
// reproduces and the ACES fit (brighter and paler in the mid-tones) does not.
const float SKY_EXPOSURE = 0.9;
vec3 skyDisplay(vec3 color) {
    if (uTonemap == 2) return display(color);   // the captured game display chain (display()), exposure included
    color *= uExposure * SKY_EXPOSURE;
    float x = min(color.r, min(color.g, color.b));
    color -= x < 0.08 ? x - 6.25 * x * x : 0.04;
    float peak = max(color.r, max(color.g, color.b));
    if (peak >= 0.76) {
        float newPeak = 1.0 - 0.0576 / (peak - 0.52);
        color *= newPeak / peak;
        color = mix(color, vec3(newPeak), 1.0 - 1.0 / (0.15 * (peak - newPeak) + 1.0));
    }
    color = clamp(color, 0.0, 1.0);
    return vec3(srgbEncode(color.r), srgbEncode(color.g), srgbEncode(color.b));
}
float P(int i) { return uP[i / 4][i - 4 * (i / 4)]; }
vec4 fetch(sampler2D s, vec2 uv, bool srgb) { vec4 c = texture2D(s, uv); return srgb ? vec4(linear(c.rgb), c.a) : c; }
void main() {
    vec3 dir = normalize(vDir);
    if (uSkyMode == 0) {
        float x = clamp(degrees(asin(clamp(dir.y, -1.0, 1.0))) / uProfileStep - 0.5, 0.0, 14.0);
        int i = int(floor(x)), j = i < 14 ? i + 1 : 14;
        gl_FragColor = vec4(skyDisplay(mix(uProfile[i], uProfile[j], x - float(i))), 1.0);
        return;
    }
    float t = 0.0;
    float n0 = fetch(uNoise, vec2(26.0 * vUV.x + P(21) * t, 26.0 * vUV.y), uNoiseSrgb).x;
    vec2 d = vUV + 0.014 * n0;
    vec4 s3 = fetch(uDome, vec2(0.5 * d.x + P(3) + P(7) * t, d.y), uDomeSrgb);
    vec4 s4 = fetch(uDome, vec2(0.5 * d.x + P(11) + P(12) * t, d.y), uDomeSrgb);
    float r = s4.x, g = s4.y, b = s3.z, a = s3.w;
    float c = max(clamp(-dot(uSunRay, dir), 0.0, 1.0), 1e-6);
    float e1 = P(15) * (1.26 - 1.2 * P(14)) + 1.2 * P(14);
    float k = clamp(1.0 - 0.1 * P(19) * P(17) * pow(c, e1) * (g + a), 0.0, 1.0);
    vec3 A = vec3(P(0), P(1), P(2)), B = vec3(P(4), P(5), P(6)), C = vec3(P(8), P(9), P(10));
    vec3 rgb = k * (A * ((1.0 - a) * P(16) * r) + C * (P(18) * b) + B * (P(19) * pow(c, P(14)) * (r * (1.0 - a) + b)));
    float coverage = clamp(P(13) * (P(20) * (1.0 - a) * g + a), 0.0, 1.0);
    if (coverage + 1e-7 <= 0.0) discard;   // alpha test against the (presentation) threshold 0
    if (uFogOn != 0) rgb = mix(uFogColour, rgb, skyFogTransmittance(dir));   // the same fog colour as the geometry
    gl_FragColor = vec4(skyDisplay(rgb), coverage);
}
""".replace("// @lighting\n", LIGHTING)
SKY_UNIFORMS = (*LIGHTING_UNIFORMS, "uSkyMode", "uProfile", "uProfileStep", "uDome", "uNoise", "uDomeSrgb",
                "uNoiseSrgb", "uSunRay", *(f"uP[{i}]" for i in range(6)))


class ShaderError(RuntimeError):
    pass


# Linked program binaries (glGetProgramBinary), keyed by the shader sources, attribute bindings and the
# driver's vendor/renderer/version strings. Any failure falls back to compiling from source.
PROGRAM_CACHE_DIR = Path(__file__).resolve().parents[2] / '.cache' / 'gl_programs'
PROGRAM_CACHE_VERSION = 1


def program_cache_key(vertex_source, fragment_source, attributes, driver):
    """Hex key of one program for one driver (``driver``: vendor, renderer and version bytes)."""
    import hashlib
    digest = hashlib.sha256(repr((PROGRAM_CACHE_VERSION, driver, sorted(attributes.items()))).encode())
    for text in (vertex_source, fragment_source):
        digest.update(b'\0' + text.encode())
    return digest.hexdigest()[:40]


def read_program_binary(path):
    """(binary format, bytes) stored by write_program_binary, or None."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b'RCPB':
        return None
    binary_format, size = struct.unpack_from('<II', data, 4)
    return (binary_format, data[12:]) if size == len(data) - 12 and size else None


def write_program_binary(path, binary_format, blob):
    """Atomic write (temporary file + rename); errors only lose the speed-up."""
    import os
    import threading
    path = Path(path)
    temp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident():x}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b'RCPB' + struct.pack('<II', binary_format, len(blob)) + bytes(blob))
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass


class Shading:
    """GL 2.0 entry points and one linked program per skeleton size."""

    NAMES = {
        "glCreateShader": (ctypes.c_uint, [ctypes.c_uint]),
        "glShaderSource": (None, [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p]),
        "glCompileShader": (None, [ctypes.c_uint]),
        "glGetShaderiv": (None, [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]),
        "glGetShaderInfoLog": (None, [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p]),
        "glCreateProgram": (ctypes.c_uint, []),
        "glAttachShader": (None, [ctypes.c_uint, ctypes.c_uint]),
        "glBindAttribLocation": (None, [ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p]),
        "glLinkProgram": (None, [ctypes.c_uint]),
        "glGetProgramiv": (None, [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]),
        "glGetProgramInfoLog": (None, [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p]),
        "glUseProgram": (None, [ctypes.c_uint]),
        "glDeleteProgram": (None, [ctypes.c_uint]),
        "glDeleteShader": (None, [ctypes.c_uint]),
        "glGetUniformLocation": (ctypes.c_int, [ctypes.c_uint, ctypes.c_char_p]),
        "glUniform1i": (None, [ctypes.c_int, ctypes.c_int]),
        "glUniform1f": (None, [ctypes.c_int, ctypes.c_float]),
        "glUniform3f": (None, [ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float]),
        "glUniform4f": (None, [ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float]),
        "glUniform3fv": (None, [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]),
        "glUniformMatrix3fv": (None, [ctypes.c_int, ctypes.c_int, ctypes.c_ubyte, ctypes.c_void_p]),
        "glUniformMatrix4fv": (None, [ctypes.c_int, ctypes.c_int, ctypes.c_ubyte, ctypes.c_void_p]),
        "glVertexAttribPointer": (None, [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_ubyte, ctypes.c_int, ctypes.c_void_p]),
        "glEnableVertexAttribArray": (None, [ctypes.c_uint]),
        "glDisableVertexAttribArray": (None, [ctypes.c_uint]),
        "glVertexAttrib4f": (None, [ctypes.c_uint, ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float]),
        "glActiveTexture": (None, [ctypes.c_uint]),
    }

    # GL 4.1 / ARB_get_program_binary; without them programs are always compiled from source.
    BINARY_NAMES = {
        "glGetProgramBinary": (None, [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p]),
        "glProgramBinary": (None, [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]),
        "glProgramParameteri": (None, [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]),
    }
    program_cache = True   # class switch for the on-disk program binaries

    def __init__(self, gl):
        gl.wglGetProcAddress.argtypes = [ctypes.c_char_p]
        gl.wglGetProcAddress.restype = ctypes.c_void_p
        for name, (result, arguments) in self.NAMES.items():
            address = gl.wglGetProcAddress(name.encode())
            if not address:
                raise ShaderError(f"OpenGL 2.0 function {name} is unavailable")
            setattr(self, name, ctypes.WINFUNCTYPE(result, *arguments)(address))
        gl.glGetString.restype = ctypes.c_char_p
        self.supports_parallax = b'GL_ARB_shader_texture_lod' in (gl.glGetString(0x1F03) or b'').split()
        self.programs = {}
        self.binary = None
        try:
            functions = {}
            for name, (result, arguments) in self.BINARY_NAMES.items():
                address = gl.wglGetProcAddress(name.encode())
                if not address:
                    raise LookupError(name)
                functions[name] = ctypes.WINFUNCTYPE(result, *arguments)(address)
            count = ctypes.c_int(0)
            gl.glGetIntegerv.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            gl.glGetIntegerv(0x87FE, ctypes.byref(count))   # GL_NUM_PROGRAM_BINARY_FORMATS
            driver = tuple(gl.glGetString(code) or b'' for code in (0x1F00, 0x1F01, 0x1F02))   # vendor, renderer, version
            if count.value > 0 and all(driver):
                self.binary = dict(functions, driver=driver)
            gl.glGetError()
        except Exception:
            self.binary = None

    def _compile(self, kind, source):
        shader = self.glCreateShader(kind)
        text = ctypes.c_char_p(source.encode())
        self.glShaderSource(shader, 1, ctypes.byref(text), None)
        self.glCompileShader(shader)
        ok = ctypes.c_int(0)
        self.glGetShaderiv(shader, 0x8B81, ctypes.byref(ok))   # COMPILE_STATUS
        if not ok.value:
            log = ctypes.create_string_buffer(4096)
            self.glGetShaderInfoLog(shader, 4096, None, log)
            raise ShaderError(log.value.decode(errors="replace"))
        return shader

    def _link(self, vertex_source, fragment_source, attributes, names):
        cache = None
        if self.binary and self.program_cache:
            cache = PROGRAM_CACHE_DIR / f"{program_cache_key(vertex_source, fragment_source, attributes, self.binary['driver'])}.bin"
            program = self._load_binary(cache)
            if program:
                return program, {name: self.glGetUniformLocation(program, name.encode()) for name in names}
        program = self._link_source(vertex_source, fragment_source, attributes, cache is not None)
        if cache is not None:
            self._save_binary(cache, program)
        return program, {name: self.glGetUniformLocation(program, name.encode()) for name in names}

    def _load_binary(self, path):
        """A program from a stored binary, or 0 when it is absent or the driver rejects it."""
        stored = read_program_binary(path)
        if not stored:
            return 0
        program = self.glCreateProgram()
        try:
            self.binary["glProgramBinary"](program, stored[0], stored[1], len(stored[1]))
            ok = ctypes.c_int(0)
            self.glGetProgramiv(program, 0x8B82, ctypes.byref(ok))   # LINK_STATUS
            if ok.value:
                return program
        except Exception:
            pass
        self.glDeleteProgram(program)
        return 0

    def _save_binary(self, path, program):
        try:
            size = ctypes.c_int(0)
            self.glGetProgramiv(program, 0x8741, ctypes.byref(size))   # GL_PROGRAM_BINARY_LENGTH
            if size.value <= 0:
                return
            blob, written, binary_format = ctypes.create_string_buffer(size.value), ctypes.c_int(0), ctypes.c_uint(0)
            self.binary["glGetProgramBinary"](program, size.value, ctypes.byref(written), ctypes.byref(binary_format), blob)
            if 0 < written.value <= size.value:
                write_program_binary(path, binary_format.value, blob.raw[:written.value])
        except Exception:
            pass

    def _link_source(self, vertex_source, fragment_source, attributes, retrievable=False):
        vertex = self._compile(0x8B31, vertex_source)
        fragment = self._compile(0x8B30, fragment_source)
        program = self.glCreateProgram()
        self.glAttachShader(program, vertex); self.glAttachShader(program, fragment)
        for name, location in attributes.items():
            self.glBindAttribLocation(program, location, name.encode())
        if retrievable:
            self.binary["glProgramParameteri"](program, 0x8257, 1)   # GL_PROGRAM_BINARY_RETRIEVABLE_HINT
        self.glLinkProgram(program)
        self.glDeleteShader(vertex); self.glDeleteShader(fragment)
        ok = ctypes.c_int(0)
        self.glGetProgramiv(program, 0x8B82, ctypes.byref(ok))   # LINK_STATUS
        if not ok.value:
            log = ctypes.create_string_buffer(4096)
            self.glGetProgramInfoLog(program, 4096, None, log)
            raise ShaderError(log.value.decode(errors="replace"))
        return program

    def program(self, joints=1):
        """The linked program; bones come from a texture, so one program fits every rig."""
        if joints > MAX_JOINTS:
            raise ShaderError(f"{joints} joints exceed the {MAX_JOINTS}-joint preview limit")
        size = MAX_JOINTS
        if size not in self.programs:
            self.programs[size] = (*self._link(VERTEX, FRAGMENT, dict(ATTRIBUTES, aRockAlpha=ROCK_ALPHA_ATTRIBUTE, aGroundBlend=GROUND_BLEND_ATTRIBUTE, aStaticPackedVector=STATIC_VECTOR_ATTRIBUTE, aTangent=TANGENT_ATTRIBUTE), OPAQUE_UNIFORMS), size)
        return self.programs[size]

    def native_program_key(self, translation, srgb, instanced=False, scene=False, experimental=False, rule_preview=False):
        """The ``programs`` key native_program links under; present once that program is linked."""
        from rc2016.viewer.native_glsl import EMITTER_VERSION
        return ('native', EMITTER_VERSION, instanced, scene, experimental, rule_preview, translation['translation_sha256'],
                tuple(sorted(srgb.items())), FRAGMENT, INSTANCED_VERTEX if instanced else VERTEX)

    def native_program(self, translation, srgb, instanced=False, scene=False, experimental=False, rule_preview=False):
        from rc2016.viewer.native_glsl import fragment_source
        vertex = INSTANCED_VERTEX if instanced else VERTEX
        key = self.native_program_key(translation, srgb, instanced, scene, experimental, rule_preview)
        if key not in self.programs:
            surface = fragment_source(translation, srgb, experimental=experimental, rule_preview=rule_preview)
            names = (*OPAQUE_UNIFORMS, *(s['uniform'] for s in surface['samplers']))
            attributes = INSTANCE_ATTRIBUTES if instanced else dict(ATTRIBUTES, aRockAlpha=ROCK_ALPHA_ATTRIBUTE, aGroundBlend=GROUND_BLEND_ATTRIBUTE, aStaticPackedVector=STATIC_VECTOR_ATTRIBUTE, aTangent=TANGENT_ATTRIBUTE)
            source=surface['source'].replace('#version 120', '#version 120\n#define RC_SCENE_EFFECTS 1',1) if scene else surface['source']
            self.programs[key] = self._link(vertex, source, attributes, names)
        return self.programs[key]

    def glass_program(self):
        """The forward (glass) program: same vertex shader and attributes, GLASS_FRAGMENT."""
        if "glass" not in self.programs:
            attributes = dict(ATTRIBUTES, aRockAlpha=ROCK_ALPHA_ATTRIBUTE,
                              aGroundBlend=GROUND_BLEND_ATTRIBUTE,
                              aStaticPackedVector=STATIC_VECTOR_ATTRIBUTE, aTangent=TANGENT_ATTRIBUTE)
            self.programs["glass"] = self._link(VERTEX, GLASS_FRAGMENT, attributes, GLASS_UNIFORMS)
        return self.programs["glass"]

    def instanced_program(self, glass=False, scene=False):
        """Level viewer: INSTANCED_VERTEX with the opaque or the glass fragment shader."""
        key = "instanced_glass" if glass else ("instanced_scene" if scene else "instanced")
        if key not in self.programs:
            fragment=GLASS_FRAGMENT if glass else FRAGMENT
            if scene and not glass:fragment=fragment.replace("#version 120","#version 120\n#define RC_SCENE_EFFECTS 1",1)
            self.programs[key] = self._link(INSTANCED_VERTEX, fragment,
                                            INSTANCE_ATTRIBUTES, GLASS_UNIFORMS if glass else OPAQUE_UNIFORMS)
        return self.programs[key]

    def sky_program(self):
        """Level viewer sky: clear-sky profile and the Rk_Sky_Dome cloud port (SKY_FRAGMENT)."""
        if "sky" not in self.programs:
            self.programs["sky"] = self._link(SKY_VERTEX, SKY_FRAGMENT, {}, SKY_UNIFORMS)
        return self.programs["sky"]

    def pick_program(self):
        """Level viewer click-to-identify: INSTANCED_VERTEX writing a flat id colour (uPickColour)."""
        if "pick" not in self.programs:
            self.programs["pick"] = self._link(INSTANCED_VERTEX, PICK_FRAGMENT, INSTANCE_ATTRIBUTES, ("uPickColour", "uTexC", "uPickCut"))
        return self.programs["pick"]
