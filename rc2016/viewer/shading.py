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

ENVIRONMENT_TEXTURE_UNIT = 6   # shared review HDRI: prefiltered radiance bands (environment.py)
# Viewer lighting shared by FRAGMENT and GLASS_FRAGMENT. uLighting 0 is the fixed viewer rig: key and
# fill lights plus a sky/ground hemisphere, in rig space; uLightFrame maps rig space to view space
# (identity: the rig turns with the camera; the level viewer fixes it to the world). uLighting 1 is
# the shared review HDRI in Blender world space (uViewToWorld): its sun as the key light (uSun*),
# diffuse from 9 irradiance SH coefficients of the rest, specular from GGX-prefiltered bands and
# Karis' analytic environment BRDF. All carry the world strength. The rig's diffuse keeps its
# historical factor 2 (diffuseScale); the HDRI's is physical (albedo x E / pi). display() applies exposure and the tonemap: 0 the ACES fit, 1 Khronos PBR
# Neutral with the sRGB transfer function (the view transform of the Blender review renders).
LIGHTING = """
uniform int uLighting; uniform mat3 uLightFrame; uniform mat3 uViewToWorld;
uniform vec3 uSH[9]; uniform sampler2D uEnvTex; uniform vec3 uSunDir; uniform vec3 uSunColour;
uniform int uTonemap; uniform float uExposure;
vec3 rigKey() { return uLighting == 1 ? uSunDir * uViewToWorld : uLightFrame * normalize(vec3(-0.45, 0.65, 0.62)); }
vec3 rigFill() { return uLightFrame * normalize(vec3(0.7, 0.1, 0.4)); }
vec3 keyColour() { return uLighting == 1 ? uSunColour : vec3(2.6, 2.5, 2.35); }
float diffuseScale() { return uLighting == 1 ? 1.0 : 2.0; }
vec3 fillColour() { return uLighting == 1 ? vec3(0.0) : vec3(0.55, 0.6, 0.7); }
vec3 irradiance(vec3 n) {
    return max(uSH[0] * 0.282095 + uSH[1] * (0.488603 * n.y) + uSH[2] * (0.488603 * n.z) + uSH[3] * (0.488603 * n.x)
        + uSH[4] * (1.092548 * n.x * n.y) + uSH[5] * (1.092548 * n.y * n.z) + uSH[6] * (0.315392 * (3.0 * n.z * n.z - 1.0))
        + uSH[7] * (1.092548 * n.x * n.z) + uSH[8] * (0.546274 * (n.x * n.x - n.y * n.y)), 0.0);
}
// Diffuse is albedo * (lights + ambient) / PI * diffuseScale().
vec3 ambientLight(vec3 N) {
    if (uLighting == 1) return irradiance(uViewToWorld * N);
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
vec3 display(vec3 color) {
    color *= uExposure;
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
LIGHTING_UNIFORMS = ("uLighting", "uLightFrame", "uViewToWorld", "uSH", "uEnvTex", "uSunDir", "uSunColour", "uTonemap", "uExposure")

# Attribute locations for the two 4-wide skin influence sets. NVIDIA aliases the
# built-ins to fixed slots (0 vertex, 2 normal, 3 colour, 4 secondary colour,
# 5 fog, 8+ texcoords), and a collision fails the link, so use 1, 6, 7 and 9
# (texcoord 1, which the shader never reads).
ATTRIBUTES = {"aJoints0": 1, "aJoints1": 6, "aWeights0": 7, "aWeights1": 9}
BONE_TEXTURE_UNIT = 3
ROCK_TEXTURE_UNIT = 15   # RockBlend sole new sampler, four tiled source slices
ROCK_ALPHA_ATTRIBUTE = 14
GLOW_PARALLAX_TEXTURE_UNIT = 13   # prompt 08; existing unit 5 carries the light mask
VEHICLE_PALETTE_TEXTURE_UNIT = 10   # EB1AC2CD second palette; prompt 05 reservation
PICK_TEXTURE_UNIT = 4   # colour-pick palette (blarg_saucer_colorpick)
ARCH_SEQUENCE_TEXTURE_UNIT = 8   # frozen AAED778C sequence; prompt 03 reservation
BNG_DETAIL_TEXTURE_UNIT = 11   # prompt 06 normal detail only
TINT_DETAIL_TEXTURE_UNIT = 9   # ColorTintVariants; sole new sampler for prompt04
PARALLAX_HEIGHT_TEXTURE_UNIT = 12   # separate source height-alpha slot
TINT_MASK_TEXTURE_UNIT = 5   # _h tint mask (mo_gbl_color_tint_01)
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
varying vec3 vTintWorldPos; varying vec3 vTintWorldNrm;
attribute float aRockAlpha; varying float vRockAlpha;
// Rows of joint j's row-major skin matrix, as the columns of its transpose.
mat4 boneT(float j) {
    float x = j * 4.0 + 0.5;
    return mat4(texture2DLod(uBoneTex, vec2(x / uBoneWidth, 0.5), 0.0),
                texture2DLod(uBoneTex, vec2((x + 1.0) / uBoneWidth, 0.5), 0.0),
                texture2DLod(uBoneTex, vec2((x + 2.0) / uBoneWidth, 0.5), 0.0),
                texture2DLod(uBoneTex, vec2((x + 3.0) / uBoneWidth, 0.5), 0.0));
}
void main() {
    vec4 p = gl_Vertex; vec3 n = gl_Normal;
    vRockAlpha = aRockAlpha * aRockAlpha;
    float total = dot(aWeights0, vec4(1.0)) + dot(aWeights1, vec4(1.0));
    if (uSkin && total > 1e-4) {
        mat4 t = boneT(aJoints0.x) * aWeights0.x + boneT(aJoints0.y) * aWeights0.y
               + boneT(aJoints0.z) * aWeights0.z + boneT(aJoints0.w) * aWeights0.w
               + boneT(aJoints1.x) * aWeights1.x + boneT(aJoints1.y) * aWeights1.y
               + boneT(aJoints1.z) * aWeights1.z + boneT(aJoints1.w) * aWeights1.w;
        // v * transpose(M) == M * v
        p = gl_Vertex * t; n = (vec4(gl_Normal, 0.0) * t).xyz;
    }
    vTintWorldPos = vec3(p.x, p.z, -p.y); vTintWorldNrm = vec3(n.x, n.z, -n.y);
    vec4 view = gl_ModelViewMatrix * p;
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
varying float vRockAlpha;
uniform int uMode;            // 0 solid, 1 base colour, 2 game BRDF, 3 flat colour (wireframe)
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
varying vec3 vPos; varying vec3 vNrm; varying vec2 vUV;
varying vec3 vTintWorldPos; varying vec3 vTintWorldNrm;
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
    return albedo * (max(dot(N, rigKey()), 0.0) * keyColour()
        + max(dot(N, rigFill()), 0.0) * fillColour() + ambientLight(N)) / PI * diffuseScale();
}
vec3 deferredSpecular(vec3 N, vec3 V, vec3 F0, float gloss) {
    vec3 F90 = clamp(60.0 * F0, 0.0, 1.0);
    float a = pow(2.0, -5.5 * gloss);
    return ggx(N,V,rigKey(),F0,F90,a)*keyColour()
        + ggx(N,V,rigFill(),F0,F90,a)*fillColour()
        + envSpecular(N,V,F0,F90,a,ambientLight(N));
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
    vec3 Dl=(max(dot(N,L1),0.0)*C1+max(dot(N,L2),0.0)*C2+E)/PI*diffuseScale();
    vec3 F90=clamp(60.0*F0,0.0,1.0);float aa=exp2(-5.5*gloss);
    vec3 S=ggx(N,V,L1,F0,F90,aa)*C1+ggx(N,V,L2,F0,F90,aa)*C2;
    S+=envSpecular(N,V,F0,F90,aa,E);
    S=clamp(mix(S,F0*E,uPuddle3.x),0.0,1.0); // probe opacity=0, instance fade=0
    return S+D*Dl; // SSR off; fog=1; opacity=1; debug=0; no self-lit input
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
    vec3 albedo = (uMode >= 1 && uHasC) ? (uSrgbC ? linear(c.rgb) : c.rgb) : linear(uColor);
    if (uMode >= 1 && uFacingGlow) albedo = vec3(1.0); // 0CD2AAFB: no albedo input, v76-v78 = 1
    if (uMode == 2 && uPuddles && uHasPuddleAtlas) {
        vec3 g=texture2D(uTexG,vUV).rgb;if(uSrgbG)g=linear(g);
        vec3 color=puddleShade(normalize(vNrm),V,albedo,g.rg);
        gl_FragColor=vec4(display(color),1.0);return;
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
        vec3 c2 = texture3D(uRockTex, vec3(fract(vUV)*uRockScale0.zw, 0.375)).rgb;
        if (uRockSrgb.y > 0.5) c2 = linear(c2);
        albedo = mix(albedo, c2, rockWeight);
        vec3 tinted = clamp(pow(abs(uRockTint*albedo), vec3(uRockP.x)), 0.0, 1.0);
        albedo = mix(albedo, tinted, uRockP.y);
        vec3 g2 = texture3D(uRockTex, vec3(fract(vUV)*uRockScale1.xy, 0.625)).rgb;
        if (uRockSrgb.z > 0.5) g2 = linear(g2);
        vec3 g1 = texture2D(uTexG, vUV).rgb; if (uSrgbG) g1 = linear(g1);
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
    if (uMode == 2 && uHasN && !uArchSequence && !uVehicle) {
        vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos); vec2 du1 = dFdx(vUV), du2 = dFdy(vUV);
        vec3 p2 = cross(dp2, N), p1 = cross(N, dp1);
        vec3 T = p2 * du1.x + p1 * du2.x, B = p2 * du1.y + p1 * du2.y;
        float scale = inversesqrt(max(max(dot(T, T), dot(B, B)), 1e-20));
        vec2 rg = texture2D(uTexN, materialUV).rg * 2.0 - 1.0;
        if (uTwoColorMetal) rg *= clamp(uMetalP.w, 0.0, 1.0);
        float z = sqrt(max(0.0, 1.0 - dot(rg, rg)));
        if (uTwoColorMetal) z = 1.0 - dot(rg, rg); // 1012E99D: source fade 2, not a square root
        if (uBngKind > 0) z = 1.0 - dot(rg, rg);
        if (uGlowKind > 0) z = 1.0 - dot(rg, rg); // 2685144F / CA81B14F normal z weight
        if (uGlowKind == 1) { // geometry TBN, before the normal map; close-range fade s=2
            float face = gl_FrontFacing ? 1.0 : -1.0;
            glowParallaxUV += uGlowP.x * face * vec2(dot(V, normalize(T)), -dot(V, normalize(-B)));
        }
        if (uParallaxNormal) z = 1.0 - dot(rg, rg); // rel1660-1680
        if (uFoliage) z = 1.0 - dot(rg, rg); // native normal, s=2; engine fade omitted
        if (uZaFresnel) z = 1.0 - dot(rg, rg); // E08357DC normal z weight, close-range fade s=2
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
        if (uBngKind == 3 || uBngKind == 6) {
            float rim = 1.00100004673 - clamp(dot(baseN, V), 0.0, 1.0);
            bngRimA = pow(abs(rim), uBngP.x);
            bngRimB = pow(abs(rim), uBngP.y);
        }
        if ((uBngKind == 2 || uBngKind == 3 || uBngKind == 6) && uHasBngDetail) {
            // F3DCD431/F087BE8D/F8161C61: detail xy masked by _g.a^2 (emissive/specfresnel), _g.B (fresnel).
            float mask = uBngKind == 3 ? bngGl.b : bngG.a * bngG.a;
            vec2 drg = (texture2D(uTexBngDetail, vUV * uBngDetail.xy).rg * 2.0 - 1.0) * mask;
            float dz = 1.0 - dot(drg, drg);
            rg = rg * dz + drg * z * uBngDetail.z;
            z *= dz;
        }
        if (uRockBlend) {
            vec2 r2 = texture3D(uRockTex, vec3(fract(vUV)*uRockScale1.zw, 0.875)).rg*2.0-1.0;
            vec3 n2 = normalize(T*scale*r2.x - B*scale*r2.y + N*(1.0-dot(r2,r2)));
            N = normalize(mix(baseN, n2, rockWeight));
        } else N = normalize(T * scale * rg.x - B * scale * rg.y + N * z);
    }
    if(uMode==2 && uParallaxNormal && uTintVariant==0 && dot(pomT,pomT)>0.0 && uHasN) {
        vec2 xy=texture2D(uTexN,materialUV).rg*2.0-1.0;
        N=(gl_FrontFacing?1.0:-1.0)*normalize(pomT*xy.x+pomB*xy.y+pomN*(1.0-dot(xy,xy)));
    }
    vec3 metalSpecTint = vec3(1.0);
    float metalRim = 0.0;
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
        if (uHasG || uVehicle) {
            vec3 F0; float gloss;
            if (uGMode == 1) {          // skin writer: F0 = grey min(cap 0.5, G), gloss = sqrt(sat(R)) (graph defaults, F term at 0)
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
            if (uBngKind == 6) {
                F0 = vec3(uSpecScale * gl.g * (1.0 + uBngP.z * bngRimA));
                gloss = sqrt(clamp(uGlossScale * (gl.r + bngRimA), 0.0, 1.0));
            }
            if (uVehicle) { F0 = vec3(0.0, 0.0999755859375, 0.0999755859375); gloss = 0.948681201424; } // packed rel 860, AA=1
            if (uTintVariant >= 3) gloss = sqrt(clamp(uGlossScale * gl.r * 2.0 * tintDetailMask, 0.0, 1.0));
            if (uRockBlend) { F0 = vec3(uSpecScale*rockG.g); gloss = sqrt(clamp(uGlossScale*rockG.r, 0.0, 1.0)); }
            if (uTwoColorMetal) gloss = sqrt(clamp(uGlossScale * gl.r * (1.0 - metalRim), 0.0, 1.0));
            F0 *= uSpecTint;
            vec3 F90 = clamp(60.0 * F0, 0.0, 1.0);
            float a = pow(2.0, -5.5 * gloss);
            // Two-color T multiplies the accumulated specular lighting after GGX/F90.
            color += metalSpecTint * deferredSpecular(N, V, F0, gloss);
        }
        if (uGlowKind == 1) { // %7398-7440: mask and offset parallax R, K=0x06F19114
            float p = uHasGlowParallax ? texture2D(uTexGlowParallax, glowParallaxUV).r : 0.0;
            if (uSrgbGlowParallax) p = linear(vec3(p)).r;
            color += albedo * uK * glowMask * (uGlowP.y + p);
        } else if (uGlowKind == 2) { // CA81B14F rel 684-700; attr3.w magnitude frozen to 1
            color += albedo * (uK * albedo.r * glowMask * uGlowP.w);
        } else if (uBngKind == 1 || uBngKind == 2 || uBngKind == 4 || uBngKind == 6) color += albedo * bngSelfLit;
        else if (uArchSequence) color += albedo * archSelfLit;
        else if (uHasC) color += albedo * (uSelfLitG ? gl.b : c.a) * uK;
        if (uFacingGlow) { // 0CD2AAFB rel 12552-12672 (instance flag bit 5 clear: v8 = v9 = 1)
            float f = pow(max(clamp(dot(N, V), 0.0, 1.0) - uFacingGlowQ.x, 0.0), uFacingGlowP.y);
            float a = uFacingGlowP.z * f + uFacingGlowP.w;
            color += uFacingGlowP.x * vec3(2.0 * a * (1.0 + a), uFacingGlowQ.y * f * (1.0 + a), 0.0);
        }
    }
    gl_FragColor = vec4(display(color), 1.0);
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
GLASS_FRAGMENT = """
#version 120
uniform sampler2D uScreenNoiseA; uniform sampler2D uScreenNoiseB;
uniform bool uScreenSrgbN; uniform bool uScreenSrgbA; uniform bool uScreenSrgbB;
uniform int uKind;
uniform vec4 uA; uniform vec4 uB; uniform vec4 uC; uniform vec4 uD;
uniform sampler2D uTexN; uniform bool uHasN;
uniform sampler2D uTexP; uniform bool uHasP; uniform bool uSrgbP;
varying vec3 vPos; varying vec3 vNrm; varying vec2 vUV;
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
void main() {
    vec3 N = normalize(vNrm) * (gl_FrontFacing ? 1.0 : -1.0);
    vec3 V = normalize(-vPos);
    float ndv = sat(dot(N, V));
    float om = max(1.0 - ndv, 1e-6);          // pow(0, 0) is undefined in GLSL
    vec3 L1 = rigKey(), L2 = rigFill();
    vec3 C1 = keyColour(), C2 = fillColour();
    vec3 color; float alpha;
    if (uKind == 3) { // 8716DC7A, time=0; cutoff=0, vertex alpha=fog fade=1
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
        vec3 one = vec3(1.0);   // R carries no Fresnel: alpha holds the rim
        vec3 R = tint * (ggx(N, V, L1, one, one, a) * C1 + ggx(N, V, L2, one, one, a) * C2 + envSpecular(N, V, one, one, a, ambient));
        color = R + X * Ld;
    }
    gl_FragColor = vec4(display(color), alpha);
}
"""
FRAGMENT = FRAGMENT.replace("// @lighting\n", LIGHTING)
GLASS_FRAGMENT = GLASS_FRAGMENT.replace("// @lighting\n", LIGHTING)
GLASS_KINDS = {"character_glass": 1, "glass_patterned": 2, "screen": 3}


def glass_uniforms(forward):
    """(uKind, uA, uB, uC, uD) for a texture_preview 'forward' entry."""
    p = forward["params"]
    if forward["kind"] == "screen":
        return (3, (*p["colour_a"], p["intensity_a"]), (*p["colour_b"], p["intensity_b"]),
                (p["alpha_base"], p["mask_strength"], p["grid"], 0.0), (0.0,) * 4)
    if forward["kind"] == "glass_patterned":
        return (2, (p["tiling"], p["normal_strength"], p["f0"], p["gloss"]),
                (p["pick_u"], p["rim_bias"], p["rim_exp"], 0.0), (0.0,) * 4, (0.0,) * 4)
    return (1, (*p["body"], p["gloss"]), (*p["tint"], p["tint_scale"]),
            (p["alpha_exp"], p["env_scale"], p["alpha_bias"], p["rim_bias"]), (p["env_exp"], p["rim_exp"], 0.0, 0.0))


OPAQUE_UNIFORMS = (
    "uPuddles", "uPuddleTex", "uHasPuddleAtlas", "uPuddle0", "uPuddle1", "uPuddle2", "uPuddle3", "uPuddleNoiseScale", "uPuddleRippleScale",
    "uRockBlend", "uRockTex", "uRockSrgb", "uRockScale0", "uRockScale1", "uRockTint", "uRockP",
    "uSkin", "uBoneTex", "uBoneWidth", "uMode", "uColor", "uTexC", "uTexG", "uTexN", "uHasC", "uHasG", "uHasN",
    "uSrgbC", "uSrgbG", "uGMode", "uSpecScale", "uGlossScale", "uSelfLitG", "uK",
    "uTwoColorMetal", "uMetalDiffuseA", "uMetalDiffuseB", "uMetalSpecA", "uMetalSpecB", "uMetalP",
    "uParallax", "uParallaxNormal", "uHasParallaxHeight", "uTexParallaxHeight", "uParallaxP",
    "uFoliage",
    "uZaFresnel", "uZaFresnelPower",
    "uGlowKind", "uGlowColour", "uGlowP", "uTexGlowParallax", "uHasGlowParallax", "uSrgbGlowParallax", "uSrgbGlowMask",
    "uVehicle", "uTexVehicleP2", "uHasVehicleP2", "uSrgbVehicleP2", "uVehicleA", "uVehicleB", "uVehicleP",
    "uBngKind", "uBngColour", "uBngP", "uBngDetail", "uTexBngDetail", "uHasBngDetail",
    "uTexP", "uHasP", "uSrgbP", "uPick", "uSpecTint", "uTint", "uTintA", "uTintB", "uTintP", "uTintM",
    "uTintVariant", "uTintPalette", "uTintPaletteBlend", "uTintDetailP", "uTintWorldOrigin",
    "uTexTintDetail", "uHasTintDetail", "uSrgbTintDetail", "uTexH", "uHasH", "uArchSequence", "uTexSequence", "uHasSequence", "uArchColour", "uArchP", "uArchPhase",
    "uFacingGlow", "uFacingGlowP", "uFacingGlowQ", *LIGHTING_UNIFORMS)
GLASS_UNIFORMS = ("uSkin", "uBoneTex", "uBoneWidth", "uKind", "uA", "uB", "uC", "uD",
                  "uTexN", "uHasN", "uTexP", "uHasP", "uSrgbP",
                  "uScreenNoiseA", "uScreenNoiseB", "uScreenSrgbN", "uScreenSrgbA", "uScreenSrgbB", *LIGHTING_UNIFORMS)

# Level viewer: one draw per model and material for all its instances. The zone's row-vector
# instance matrix arrives as four per-instance attributes (rows; docs/levels.md), in texcoord
# 2-5's aliased slots, which no shader reads. Geometry and matrices stay in native Y-up space;
# the camera matrix carries the presentation basis.
INSTANCE_ATTRIBUTES = {"aInst0": 10, "aInst1": 11, "aInst2": 12, "aInst3": 13}
INSTANCED_VERTEX = """
#version 120
attribute vec4 aInst0; attribute vec4 aInst1; attribute vec4 aInst2; attribute vec4 aInst3;
varying vec3 vPos; varying vec3 vNrm; varying vec2 vUV;
varying vec3 vTintWorldPos; varying vec3 vTintWorldNrm;
varying float vRockAlpha;
void main() {
    vRockAlpha = 1.0; // level colour transport is outside the isolated model proof
    vec3 a = aInst0.xyz, b = aInst1.xyz, c = aInst2.xyz;
    vec3 p = gl_Vertex.x * a + gl_Vertex.y * b + gl_Vertex.z * c + aInst3.xyz;
    // Columns b x c, c x a, a x b = det * inverse-transpose: exact for the zones' sheared and
    // mirrored instances once the determinant's sign is restored.
    vec3 n = mat3(cross(b, c), cross(c, a), cross(a, b)) * gl_Normal * sign(dot(a, cross(b, c)));
    vTintWorldPos = p; vTintWorldNrm = n;   // native Y-up world space, as the tint writers read it
    vec4 view = gl_ModelViewMatrix * vec4(p, 1.0);
    vPos = view.xyz; vNrm = gl_NormalMatrix * n; vUV = gl_MultiTexCoord0.xy;
    gl_Position = gl_ProjectionMatrix * view;
}
"""
# Level viewer click-to-identify: each model drawn in a flat id colour into the back buffer.
PICK_FRAGMENT = """
#version 120
uniform vec3 uPickColour;
void main() { gl_FragColor = vec4(uPickColour, 1.0); }
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
# time 0, alpha multiplier 1, alpha-test threshold 0, no fog. The stars term (dword 22) is not ported.
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
float P(int i) { return uP[i / 4][i - 4 * (i / 4)]; }
vec4 fetch(sampler2D s, vec2 uv, bool srgb) { vec4 c = texture2D(s, uv); return srgb ? vec4(linear(c.rgb), c.a) : c; }
void main() {
    vec3 dir = normalize(vDir);
    if (uSkyMode == 0) {
        float x = clamp(degrees(asin(clamp(dir.y, -1.0, 1.0))) / uProfileStep - 0.5, 0.0, 14.0);
        int i = int(floor(x)), j = i < 14 ? i + 1 : 14;
        gl_FragColor = vec4(display(mix(uProfile[i], uProfile[j], x - float(i))), 1.0);
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
    gl_FragColor = vec4(display(rgb), coverage);
}
""".replace("// @lighting\n", LIGHTING)
SKY_UNIFORMS = (*LIGHTING_UNIFORMS, "uSkyMode", "uProfile", "uProfileStep", "uDome", "uNoise", "uDomeSrgb",
                "uNoiseSrgb", "uSunRay", *(f"uP[{i}]" for i in range(6)))


class ShaderError(RuntimeError):
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
        vertex = self._compile(0x8B31, vertex_source)
        fragment = self._compile(0x8B30, fragment_source)
        program = self.glCreateProgram()
        self.glAttachShader(program, vertex); self.glAttachShader(program, fragment)
        for name, location in attributes.items():
            self.glBindAttribLocation(program, location, name.encode())
        self.glLinkProgram(program)
        self.glDeleteShader(vertex); self.glDeleteShader(fragment)
        ok = ctypes.c_int(0)
        self.glGetProgramiv(program, 0x8B82, ctypes.byref(ok))   # LINK_STATUS
        if not ok.value:
            log = ctypes.create_string_buffer(4096)
            self.glGetProgramInfoLog(program, 4096, None, log)
            raise ShaderError(log.value.decode(errors="replace"))
        return program, {name: self.glGetUniformLocation(program, name.encode()) for name in names}

    def program(self, joints=1):
        """The linked program; bones come from a texture, so one program fits every rig."""
        if joints > MAX_JOINTS:
            raise ShaderError(f"{joints} joints exceed the {MAX_JOINTS}-joint preview limit")
        size = MAX_JOINTS
        if size not in self.programs:
            self.programs[size] = (*self._link(VERTEX, FRAGMENT, dict(ATTRIBUTES, aRockAlpha=ROCK_ALPHA_ATTRIBUTE), OPAQUE_UNIFORMS), size)
        return self.programs[size]

    def native_program(self, translation, srgb, instanced=False):
        from rc2016.viewer.native_glsl import fragment_source
        from rc2016.viewer.native_glsl import EMITTER_VERSION
        vertex = INSTANCED_VERTEX if instanced else VERTEX
        key = ('native', EMITTER_VERSION, instanced, translation['translation_sha256'],
               tuple(sorted(srgb.items())), FRAGMENT, vertex)
        if key not in self.programs:
            surface = fragment_source(translation, srgb)
            names = (*OPAQUE_UNIFORMS, *(s['uniform'] for s in surface['samplers']))
            attributes = INSTANCE_ATTRIBUTES if instanced else dict(ATTRIBUTES, aRockAlpha=ROCK_ALPHA_ATTRIBUTE)
            self.programs[key] = self._link(vertex, surface['source'], attributes, names)
        return self.programs[key]

    def glass_program(self):
        """The forward (glass) program: same vertex shader and attributes, GLASS_FRAGMENT."""
        if "glass" not in self.programs:
            self.programs["glass"] = self._link(VERTEX, GLASS_FRAGMENT, ATTRIBUTES, GLASS_UNIFORMS)
        return self.programs["glass"]

    def instanced_program(self, glass=False):
        """Level viewer: INSTANCED_VERTEX with the opaque or the glass fragment shader."""
        key = "instanced_glass" if glass else "instanced"
        if key not in self.programs:
            self.programs[key] = self._link(INSTANCED_VERTEX, GLASS_FRAGMENT if glass else FRAGMENT,
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
            self.programs["pick"] = self._link(INSTANCED_VERTEX, PICK_FRAGMENT, INSTANCE_ATTRIBUTES, ("uPickColour",))
        return self.programs["pick"]
