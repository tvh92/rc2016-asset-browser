"""Source-exact captured compute 5881 core AO pass.
The exact gathered tile/taps and weighted group reduction are retained. A small,
viewer-generated 4x4 phase deinterleave feeds the recovered core. The capture's
other AO stages, auxiliary normal-biased shadow branch, native camera ABI and
Novalis runtime selection are not claimed. GAME_EQUIVALENCE is false.
"""
from __future__ import annotations
import ctypes
import math
import struct

GAME_EQUIVALENCE = False
SOURCE_CAPTURE_SHA256 = "1e04391ca973938db28c3dac9f06a1dc697972f872640eb0ebfcd807abda582c"
SOURCE_COMPUTE_5881_SHA256 = "08b4ac0306babbba1e40ec65de6d697d43b6101bbe9ba6c60d0db6683d899c3c"
CAPTURED_DEPTH_REFERENCES = (13.339613,14.260647,16.337622,21.783497,13.626520,14.612814,16.873425,23.104889,15.849823,18.865063,29.225634,24.700163)
CAPTURED_WEIGHTS = (0.019819906,0.018539824,0.016182886,0.012137163,0.019402599,0.018093016,0.015669011,0.011443027,0.016680935,0.014014790,0.0090465061,0.010703973)

_CORE_SOURCE = r'''#version 450
#if defined(GL_EXT_shader_explicit_arithmetic_types_int8)
#extension GL_EXT_shader_explicit_arithmetic_types_int8 : require
#elif defined(GL_NV_gpu_shader5)
#extension GL_NV_gpu_shader5 : require
#else
#error No extension available for Int8.
#endif
#if defined(GL_EXT_shader_explicit_arithmetic_types_int16)
#extension GL_EXT_shader_explicit_arithmetic_types_int16 : require
#elif defined(GL_AMD_gpu_shader_int16)
#extension GL_AMD_gpu_shader_int16 : require
#elif defined(GL_NV_gpu_shader5)
#extension GL_NV_gpu_shader5 : require
#else
#error No extension available for Int16.
#endif
#if defined(GL_ARB_gpu_shader_int64)
#extension GL_ARB_gpu_shader_int64 : require
#elif defined(GL_NV_gpu_shader5)
#extension GL_NV_gpu_shader5 : require
#else
#error No extension available for 64-bit integers.
#endif
layout(local_size_x = 8, local_size_y = 8, local_size_z = 1) in;

struct full_result_i32x2
{
    int _m0;
    int _m1;
};

struct full_result_u32x2
{
    uint _m0;
    uint _m1;
};

struct frexp_result_f32
{
    float _m0;
    int _m1;
};

layout(binding = 0, std430) readonly buffer ssbo_1
{
    uint data[];
} ssbo_1_1;

struct AuxData
{
    float xoffset;
    float yoffset;
    float xscale;
    float yscale;
    uvec4 ud_regs0;
    uvec4 ud_regs1;
    uvec4 ud_regs2;
    uvec4 ud_regs3;
    uvec4 buf_offsets0;
    uvec4 buf_offsets1;
    uvec2 buf_offsets2;
};

uniform AuxData push_data;

layout(binding = 2) uniform writeonly image2D cs_img1;
uniform sampler2DArray SPIRV_Cross_Combinedcs_img0cs_samp0;

shared uint shared_mem_u32[256];

void main()
{
    uint buf0_dword_off = bitfieldExtract(push_data.buf_offsets0.x, int(0u), int(8u)) >> 2u;
    uint _93 = gl_LocalInvocationID.x << 1u;
    uint _106 = ssbo_1_1.data[4u + buf0_dword_off];
    uint _110 = ssbo_1_1.data[5u + buf0_dword_off];
    uint _114 = ssbo_1_1.data[6u + buf0_dword_off];
    uint _118 = ssbo_1_1.data[7u + buf0_dword_off];
    uint _121 = ssbo_1_1.data[8u + buf0_dword_off];
    uint _125 = ssbo_1_1.data[9u + buf0_dword_off];
    uint _129 = ssbo_1_1.data[10u + buf0_dword_off];
    uint _133 = ssbo_1_1.data[11u + buf0_dword_off];
    uint _137 = ssbo_1_1.data[12u + buf0_dword_off];
    uint _141 = ssbo_1_1.data[13u + buf0_dword_off];
    uint _145 = ssbo_1_1.data[14u + buf0_dword_off];
    uint _149 = ssbo_1_1.data[15u + buf0_dword_off];
    precise float _160 = uintBitsToFloat(ssbo_1_1.data[0u + buf0_dword_off]) * float(int((((gl_WorkGroupID.x << 3u) + 4294967292u) + _93) + 1u));
    uint _161 = (_93 + (gl_LocalInvocationID.y << 5u)) << 2u;
    precise float _167 = uintBitsToFloat(ssbo_1_1.data[1u + buf0_dword_off]) * float(int((((gl_WorkGroupID.y << 3u) + 4294967292u) + (gl_LocalInvocationID.y << 1u)) + 1u));
    vec4 _172 = textureGather(SPIRV_Cross_Combinedcs_img0cs_samp0, vec3(_160, _167, roundEven(float(gl_WorkGroupID.z))));
    shared_mem_u32[_161 >> 2u] = floatBitsToUint(_172.w);
    shared_mem_u32[(_161 + 4u) >> 2u] = floatBitsToUint(_172.z);
    shared_mem_u32[(_161 + 64u) >> 2u] = floatBitsToUint(_172.x);
    shared_mem_u32[(_161 + 68u) >> 2u] = floatBitsToUint(_172.y);
    uint _196 = ((gl_LocalInvocationID.y << 4u) + gl_LocalInvocationID.x) << 2u;
    barrier();
    float _207 = uintBitsToFloat(_106);
    precise float _209 = (-0.5) + _207;
    float _220 = uintBitsToFloat(_110);
    precise float _221 = (-0.5) + _220;
    float _242 = uintBitsToFloat(_114);
    precise float _243 = (-0.5) + _242;
    float _274 = uintBitsToFloat(_118);
    precise float _275 = (-0.5) + _274;
    float _286 = uintBitsToFloat(_125);
    precise float _287 = (-0.5) + _286;
    float _288 = uintBitsToFloat(_121);
    precise float _289 = (-0.5) + _288;
    float _290 = uintBitsToFloat(_129);
    precise float _291 = (-0.5) + _290;
    float _369 = 1.0 / uintBitsToFloat(shared_mem_u32[(_196 + 272u) >> 2u]);
    precise float _370 = _207 * _369;
    precise float _373 = uintBitsToFloat(shared_mem_u32[(_196 + 276u) >> 2u]) * _370;
    precise float _374 = _373 + (-_209);
    precise float _377 = uintBitsToFloat(shared_mem_u32[(_196 + 268u) >> 2u]) * _370;
    precise float _378 = _377 + (-_209);
    precise float _380 = (-0.4000000059604644775390625) * _378;
    precise float _383 = (-0.4000000059604644775390625) * _374;
    precise float _386 = clamp(max(_374, _380), 0.0, 1.0) + clamp(max(_378, _383), 0.0, 1.0);
    precise float _409 = uintBitsToFloat(shared_mem_u32[(_196 + 208u) >> 2u]) * _370;
    precise float _410 = _409 + (-_209);
    precise float _411 = (-0.4000000059604644775390625) * _410;
    precise float _414 = uintBitsToFloat(shared_mem_u32[(_196 + 336u) >> 2u]) * _370;
    precise float _415 = _414 + (-_209);
    precise float _418 = (-0.4000000059604644775390625) * _415;
    precise float _419 = _386 + clamp(max(_415, _411), 0.0, 1.0);
    precise float _422 = _419 + clamp(max(_410, _418), 0.0, 1.0);
    precise float _423 = _220 * _369;
    precise float _426 = uintBitsToFloat(shared_mem_u32[(_196 + 264u) >> 2u]) * _423;
    precise float _427 = _426 + (-_221);
    precise float _430 = uintBitsToFloat(shared_mem_u32[(_196 + 280u) >> 2u]) * _423;
    precise float _431 = _430 + (-_221);
    precise float _432 = (-0.4000000059604644775390625) * _427;
    precise float _435 = uintBitsToFloat(shared_mem_u32[(_196 + 144u) >> 2u]) * _423;
    precise float _436 = _435 + (-_221);
    precise float _439 = (-0.4000000059604644775390625) * _431;
    precise float _442 = (-0.4000000059604644775390625) * _436;
    precise float _445 = uintBitsToFloat(shared_mem_u32[(_196 + 400u) >> 2u]) * _423;
    precise float _446 = _445 + (-_221);
    precise float _447 = clamp(max(_431, _432), 0.0, 1.0) + clamp(max(_427, _439), 0.0, 1.0);
    precise float _469 = (-0.4000000059604644775390625) * _446;
    precise float _470 = _447 + clamp(max(_446, _442), 0.0, 1.0);
    precise float _471 = _242 * _369;
    precise float _476 = uintBitsToFloat(shared_mem_u32[(_196 + 464u) >> 2u]) * _471;
    precise float _477 = _476 + (-_243);
    float _478 = uintBitsToFloat(_149);
    precise float _479 = (-0.5) + _478;
    precise float _481 = uintBitsToFloat(ssbo_1_1.data[16u + buf0_dword_off]) * _422;
    precise float _482 = _470 + clamp(max(_436, _469), 0.0, 1.0);
    precise float _484 = uintBitsToFloat(ssbo_1_1.data[17u + buf0_dword_off]) * _482;
    precise float _485 = _484 + _481;
    precise float _488 = uintBitsToFloat(shared_mem_u32[(_196 + 284u) >> 2u]) * _471;
    precise float _489 = _488 + (-_243);
    precise float _492 = uintBitsToFloat(shared_mem_u32[(_196 + 260u) >> 2u]) * _471;
    precise float _493 = _492 + (-_243);
    precise float _494 = (-0.4000000059604644775390625) * _493;
    precise float _497 = uintBitsToFloat(shared_mem_u32[(_196 + 80u) >> 2u]) * _471;
    precise float _498 = _497 + (-_243);
    precise float _501 = (-0.4000000059604644775390625) * _489;
    precise float _504 = (-0.4000000059604644775390625) * _498;
    precise float _505 = clamp(max(_489, _494), 0.0, 1.0) + clamp(max(_493, _501), 0.0, 1.0);
    precise float _518 = _274 * _369;
    precise float _521 = uintBitsToFloat(shared_mem_u32[(_196 + 256u) >> 2u]) * _518;
    precise float _522 = _521 + (-_275);
    precise float _525 = uintBitsToFloat(shared_mem_u32[(_196 + 288u) >> 2u]) * _518;
    precise float _526 = _525 + (-_275);
    precise float _527 = (-0.4000000059604644775390625) * _477;
    precise float _528 = _505 + clamp(max(_477, _504), 0.0, 1.0);
    precise float _531 = _528 + clamp(max(_498, _527), 0.0, 1.0);
    precise float _543 = uintBitsToFloat(ssbo_1_1.data[18u + buf0_dword_off]) * _531;
    precise float _544 = _543 + _485;
    precise float _545 = (-0.4000000059604644775390625) * _522;
    float _546 = uintBitsToFloat(_137);
    precise float _547 = _546 * _369;
    precise float _558 = (-0.4000000059604644775390625) * _526;
    precise float _563 = uintBitsToFloat(shared_mem_u32[(_196 + 528u) >> 2u]) * _518;
    precise float _564 = _563 + (-_275);
    precise float _567 = uintBitsToFloat(shared_mem_u32[(_196 + 16u) >> 2u]) * _518;
    precise float _568 = _567 + (-_275);
    precise float _569 = (-0.4000000059604644775390625) * _568;
    precise float _570 = clamp(max(_526, _545), 0.0, 1.0) + clamp(max(_522, _558), 0.0, 1.0);
    precise float _573 = (-0.4000000059604644775390625) * _564;
    precise float _574 = _570 + clamp(max(_564, _569), 0.0, 1.0);
    precise float _577 = _288 * _369;
    precise float _580 = uintBitsToFloat(shared_mem_u32[(_196 + 332u) >> 2u]) * _577;
    precise float _581 = _580 + (-_289);
    precise float _582 = (-0.4000000059604644775390625) * _581;
    precise float _585 = uintBitsToFloat(shared_mem_u32[(_196 + 204u) >> 2u]) * _577;
    precise float _586 = _585 + (-_289);
    precise float _587 = _574 + clamp(max(_568, _573), 0.0, 1.0);
    precise float _590 = uintBitsToFloat(shared_mem_u32[(_196 + 212u) >> 2u]) * _577;
    precise float _591 = _590 + (-_289);
    precise float _592 = (-0.4000000059604644775390625) * _591;
    precise float _599 = uintBitsToFloat(shared_mem_u32[(_196 + 340u) >> 2u]) * _577;
    precise float _600 = _599 + (-_289);
    precise float _601 = clamp(max(_581, _592), 0.0, 1.0) + clamp(max(_591, _582), 0.0, 1.0);
    precise float _602 = (-0.4000000059604644775390625) * _586;
    precise float _615 = (-0.4000000059604644775390625) * _600;
    precise float _626 = _601 + clamp(max(_600, _602), 0.0, 1.0);
    precise float _630 = uintBitsToFloat(ssbo_1_1.data[19u + buf0_dword_off]) * _587;
    precise float _631 = _630 + _544;
    precise float _632 = _626 + clamp(max(_586, _615), 0.0, 1.0);
    precise float _634 = uintBitsToFloat(ssbo_1_1.data[20u + buf0_dword_off]) * _632;
    precise float _635 = _634 + _631;
    precise float _636 = (-0.5) + _546;
    precise float _639 = uintBitsToFloat(shared_mem_u32[(_196 + 152u) >> 2u]) * _547;
    precise float _640 = _639 + (-_636);
    precise float _643 = uintBitsToFloat(shared_mem_u32[(_196 + 392u) >> 2u]) * _547;
    precise float _644 = _643 + (-_636);
    precise float _645 = (-0.4000000059604644775390625) * _640;
    precise float _648 = uintBitsToFloat(shared_mem_u32[(_196 + 136u) >> 2u]) * _547;
    precise float _649 = _648 + (-_636);
    precise float _652 = (-0.4000000059604644775390625) * _644;
    precise float _655 = (-0.4000000059604644775390625) * _649;
    precise float _658 = uintBitsToFloat(shared_mem_u32[(_196 + 408u) >> 2u]) * _547;
    precise float _659 = _658 + (-_636);
    precise float _660 = clamp(max(_644, _645), 0.0, 1.0) + clamp(max(_640, _652), 0.0, 1.0);
    precise float _661 = _286 * _369;
    precise float _664 = uintBitsToFloat(shared_mem_u32[(_196 + 140u) >> 2u]) * _661;
    precise float _665 = _664 + (-_287);
    precise float _668 = uintBitsToFloat(shared_mem_u32[(_196 + 404u) >> 2u]) * _661;
    precise float _669 = _668 + (-_287);
    precise float _670 = (-0.4000000059604644775390625) * _665;
    precise float _673 = (-0.4000000059604644775390625) * _669;
    precise float _676 = clamp(max(_669, _670), 0.0, 1.0) + clamp(max(_665, _673), 0.0, 1.0);
    precise float _679 = _478 * _369;
    precise float _680 = (-0.4000000059604644775390625) * _659;
    precise float _683 = uintBitsToFloat(shared_mem_u32[(_196 + 216u) >> 2u]) * _661;
    precise float _684 = _683 + (-_287);
    precise float _695 = _660 + clamp(max(_659, _655), 0.0, 1.0);
    precise float _698 = _695 + clamp(max(_649, _680), 0.0, 1.0);
    precise float _701 = uintBitsToFloat(shared_mem_u32[(_196 + 92u) >> 2u]) * _679;
    precise float _702 = _701 + (-_479);
    precise float _704 = uintBitsToFloat(ssbo_1_1.data[24u + buf0_dword_off]) * _698;
    precise float _705 = _704 + _635;
    precise float _728 = uintBitsToFloat(shared_mem_u32[(_196 + 452u) >> 2u]) * _679;
    precise float _729 = _728 + (-_479);
    precise float _730 = (-0.4000000059604644775390625) * _702;
    precise float _733 = (-0.4000000059604644775390625) * _729;
    precise float _736 = clamp(max(_729, _730), 0.0, 1.0) + clamp(max(_702, _733), 0.0, 1.0);
    precise float _747 = uintBitsToFloat(shared_mem_u32[(_196 + 68u) >> 2u]) * _679;
    precise float _748 = _747 + (-_479);
    precise float _749 = (-0.4000000059604644775390625) * _748;
    precise float _752 = uintBitsToFloat(shared_mem_u32[(_196 + 476u) >> 2u]) * _679;
    precise float _753 = _752 + (-_479);
    precise float _756 = uintBitsToFloat(shared_mem_u32[(_196 + 148u) >> 2u]) * _661;
    precise float _757 = _756 + (-_287);
    precise float _768 = (-0.4000000059604644775390625) * _757;
    precise float _771 = (-0.4000000059604644775390625) * _753;
    precise float _772 = _736 + clamp(max(_753, _749), 0.0, 1.0);
    precise float _775 = uintBitsToFloat(shared_mem_u32[(_196 + 396u) >> 2u]) * _661;
    precise float _776 = _775 + (-_287);
    precise float _779 = (-0.4000000059604644775390625) * _776;
    precise float _780 = _676 + clamp(max(_776, _768), 0.0, 1.0);
    precise float _783 = _780 + clamp(max(_757, _779), 0.0, 1.0);
    precise float _796 = uintBitsToFloat(shared_mem_u32[(_196 + 200u) >> 2u]) * _661;
    precise float _797 = _796 + (-_287);
    precise float _798 = (-0.4000000059604644775390625) * _797;
    precise float _803 = uintBitsToFloat(shared_mem_u32[(_196 + 344u) >> 2u]) * _661;
    precise float _804 = _803 + (-_287);
    precise float _805 = _772 + clamp(max(_748, _771), 0.0, 1.0);
    precise float _808 = (-0.4000000059604644775390625) * _804;
    precise float _809 = _783 + clamp(max(_804, _798), 0.0, 1.0);
    precise float _822 = (-0.4000000059604644775390625) * _684;
    precise float _825 = uintBitsToFloat(shared_mem_u32[(_196 + 328u) >> 2u]) * _661;
    precise float _826 = _825 + (-_287);
    precise float _827 = _809 + clamp(max(_797, _808), 0.0, 1.0);
    precise float _830 = (-0.4000000059604644775390625) * _826;
    precise float _831 = _827 + clamp(max(_826, _822), 0.0, 1.0);
    precise float _832 = _290 * _369;
    precise float _835 = uintBitsToFloat(shared_mem_u32[(_196 + 196u) >> 2u]) * _832;
    precise float _836 = _835 + (-_291);
    precise float _840 = uintBitsToFloat(ssbo_1_1.data[27u + buf0_dword_off]) * _805;
    precise float _841 = _840 + _705;
    precise float _844 = uintBitsToFloat(shared_mem_u32[(_196 + 468u) >> 2u]) * _832;
    precise float _845 = _844 + (-_291);
    precise float _846 = _831 + clamp(max(_684, _830), 0.0, 1.0);
    precise float _849 = uintBitsToFloat(shared_mem_u32[(_196 + 84u) >> 2u]) * _832;
    precise float _850 = _849 + (-_291);
    precise float _852 = uintBitsToFloat(ssbo_1_1.data[21u + buf0_dword_off]) * _846;
    precise float _853 = _852 + _841;
    precise float _856 = uintBitsToFloat(shared_mem_u32[(_196 + 76u) >> 2u]) * _832;
    precise float _857 = _856 + (-_291);
    precise float _858 = (-0.4000000059604644775390625) * _857;
    precise float _861 = (-0.4000000059604644775390625) * _845;
    precise float _874 = clamp(max(_845, _858), 0.0, 1.0) + clamp(max(_857, _861), 0.0, 1.0);
    precise float _885 = uintBitsToFloat(shared_mem_u32[(_196 + 460u) >> 2u]) * _832;
    precise float _886 = _885 + (-_291);
    precise float _889 = uintBitsToFloat(shared_mem_u32[(_196 + 324u) >> 2u]) * _832;
    precise float _890 = _889 + (-_291);
    precise float _891 = (-0.4000000059604644775390625) * _850;
    precise float _894 = (-0.4000000059604644775390625) * _886;
    precise float _895 = _874 + clamp(max(_886, _891), 0.0, 1.0);
    precise float _898 = (-0.4000000059604644775390625) * _836;
    precise float _899 = _895 + clamp(max(_850, _894), 0.0, 1.0);
    precise float _912 = uintBitsToFloat(shared_mem_u32[(_196 + 348u) >> 2u]) * _832;
    precise float _913 = _912 + (-_291);
    precise float _916 = (-0.4000000059604644775390625) * _913;
    precise float _917 = _899 + clamp(max(_913, _898), 0.0, 1.0);
    precise float _932 = uintBitsToFloat(shared_mem_u32[(_196 + 220u) >> 2u]) * _832;
    precise float _933 = _932 + (-_291);
    precise float _934 = (-0.4000000059604644775390625) * _933;
    precise float _935 = _917 + clamp(max(_836, _916), 0.0, 1.0);
    float _936 = uintBitsToFloat(_133);
    precise float _937 = (-0.5) + _936;
    precise float _940 = (-0.4000000059604644775390625) * _890;
    precise float _941 = _935 + clamp(max(_890, _934), 0.0, 1.0);
    precise float _942 = _936 * _369;
    precise float _947 = uintBitsToFloat(shared_mem_u32[(_196 + 320u) >> 2u]) * _942;
    precise float _948 = _947 + (-_937);
    precise float _951 = uintBitsToFloat(shared_mem_u32[(_196 + 20u) >> 2u]) * _942;
    precise float _952 = _951 + (-_937);
    precise float _953 = (-0.4000000059604644775390625) * _952;
    precise float _954 = _941 + clamp(max(_933, _940), 0.0, 1.0);
    precise float _957 = uintBitsToFloat(shared_mem_u32[(_196 + 532u) >> 2u]) * _942;
    precise float _958 = _957 + (-_937);
    precise float _961 = uintBitsToFloat(shared_mem_u32[(_196 + 352u) >> 2u]) * _942;
    precise float _962 = _961 + (-_937);
    precise float _965 = uintBitsToFloat(shared_mem_u32[(_196 + 12u) >> 2u]) * _942;
    precise float _966 = _965 + (-_937);
    precise float _969 = uintBitsToFloat(shared_mem_u32[(_196 + 224u) >> 2u]) * _942;
    precise float _970 = _969 + (-_937);
    precise float _971 = (-0.4000000059604644775390625) * _966;
    precise float _974 = (-0.4000000059604644775390625) * _958;
    precise float _977 = clamp(max(_958, _971), 0.0, 1.0) + clamp(max(_966, _974), 0.0, 1.0);
    float _988 = uintBitsToFloat(_141);
    precise float _989 = _988 * _369;
    precise float _992 = uintBitsToFloat(shared_mem_u32[(_196 + 524u) >> 2u]) * _942;
    precise float _993 = _992 + (-_937);
    precise float _996 = (-0.4000000059604644775390625) * _993;
    precise float _997 = _977 + clamp(max(_993, _953), 0.0, 1.0);
    precise float _1000 = _997 + clamp(max(_952, _996), 0.0, 1.0);
    precise float _1011 = (-0.4000000059604644775390625) * _962;
    precise float _1014 = uintBitsToFloat(shared_mem_u32[(_196 + 192u) >> 2u]) * _942;
    precise float _1015 = _1014 + (-_937);
    precise float _1016 = (-0.4000000059604644775390625) * _1015;
    precise float _1019 = _1000 + clamp(max(_962, _1016), 0.0, 1.0);
    precise float _1022 = (-0.4000000059604644775390625) * _970;
    precise float _1023 = _1019 + clamp(max(_1015, _1011), 0.0, 1.0);
    precise float _1026 = (-0.4000000059604644775390625) * _948;
    precise float _1027 = _1023 + clamp(max(_948, _1022), 0.0, 1.0);
    precise float _1030 = (-0.5) + _988;
    precise float _1033 = uintBitsToFloat(shared_mem_u32[(_196 + 72u) >> 2u]) * _989;
    precise float _1034 = _1033 + (-_1030);
    precise float _1037 = uintBitsToFloat(shared_mem_u32[(_196 + 472u) >> 2u]) * _989;
    precise float _1038 = _1037 + (-_1030);
    precise float _1041 = uintBitsToFloat(shared_mem_u32[(_196 + 88u) >> 2u]) * _989;
    precise float _1042 = _1041 + (-_1030);
    precise float _1045 = uintBitsToFloat(shared_mem_u32[(_196 + 456u) >> 2u]) * _989;
    precise float _1046 = _1045 + (-_1030);
    precise float _1049 = uintBitsToFloat(shared_mem_u32[(_196 + 412u) >> 2u]) * _989;
    precise float _1050 = _1049 + (-_1030);
    precise float _1051 = (-0.4000000059604644775390625) * _1034;
    precise float _1054 = (-0.4000000059604644775390625) * _1038;
    precise float _1057 = (-0.4000000059604644775390625) * _1042;
    precise float _1058 = clamp(max(_1038, _1051), 0.0, 1.0) + clamp(max(_1034, _1054), 0.0, 1.0);
    precise float _1061 = (-0.4000000059604644775390625) * _1046;
    precise float _1062 = _1058 + clamp(max(_1046, _1057), 0.0, 1.0);
    precise float _1070 = _1062 + clamp(max(_1042, _1061), 0.0, 1.0);
    precise float _1083 = uintBitsToFloat(shared_mem_u32[(_196 + 156u) >> 2u]) * _989;
    precise float _1084 = _1083 + (-_1030);
    precise float _1087 = uintBitsToFloat(shared_mem_u32[(_196 + 132u) >> 2u]) * _989;
    precise float _1088 = _1087 + (-_1030);
    precise float _1089 = (-0.4000000059604644775390625) * _1088;
    precise float _1092 = (-0.4000000059604644775390625) * _1050;
    precise float _1093 = _1070 + clamp(max(_1050, _1089), 0.0, 1.0);
    precise float _1096 = (-0.4000000059604644775390625) * _1084;
    precise float _1097 = _1093 + clamp(max(_1088, _1092), 0.0, 1.0);
    precise float _1109 = uintBitsToFloat(ssbo_1_1.data[22u + buf0_dword_off]) * _954;
    precise float _1110 = _1109 + _853;
    precise float _1113 = uintBitsToFloat(shared_mem_u32[(_196 + 388u) >> 2u]) * _989;
    precise float _1114 = _1113 + (-_1030);
    precise float _1117 = (-0.4000000059604644775390625) * _1114;
    precise float _1118 = _1027 + clamp(max(_970, _1026), 0.0, 1.0);
    float _1119 = uintBitsToFloat(_145);
    precise float _1120 = _1119 * _369;
    precise float _1121 = _1097 + clamp(max(_1114, _1096), 0.0, 1.0);
    precise float _1125 = uintBitsToFloat(ssbo_1_1.data[23u + buf0_dword_off]) * _1118;
    precise float _1126 = _1125 + _1110;
    precise float _1127 = _1121 + clamp(max(_1084, _1117), 0.0, 1.0);
    precise float _1129 = uintBitsToFloat(ssbo_1_1.data[25u + buf0_dword_off]) * _1127;
    precise float _1130 = _1129 + _1126;
    precise float _1131 = (-0.5) + _1119;
    precise float _1134 = uintBitsToFloat(shared_mem_u32[(_196 + 8u) >> 2u]) * _1120;
    precise float _1135 = _1134 + (-_1131);
    precise float _1138 = uintBitsToFloat(shared_mem_u32[(_196 + 536u) >> 2u]) * _1120;
    precise float _1139 = _1138 + (-_1131);
    precise float _1140 = (-0.4000000059604644775390625) * _1139;
    precise float _1143 = uintBitsToFloat(shared_mem_u32[(_196 + 24u) >> 2u]) * _1120;
    precise float _1144 = _1143 + (-_1131);
    precise float _1149 = uintBitsToFloat(shared_mem_u32[(_196 + 520u) >> 2u]) * _1120;
    precise float _1150 = _1149 + (-_1131);
    precise float _1153 = uintBitsToFloat(shared_mem_u32[(_196 + 128u) >> 2u]) * _1120;
    precise float _1154 = _1153 + (-_1131);
    precise float _1157 = uintBitsToFloat(shared_mem_u32[(_196 + 416u) >> 2u]) * _1120;
    precise float _1158 = _1157 + (-_1131);
    precise float _1161 = uintBitsToFloat(shared_mem_u32[(_196 + 384u) >> 2u]) * _1120;
    precise float _1162 = _1161 + (-_1131);
    precise float _1163 = (-0.4000000059604644775390625) * _1144;
    precise float _1166 = (-0.4000000059604644775390625) * _1150;
    precise float _1171 = uintBitsToFloat(shared_mem_u32[(_196 + 160u) >> 2u]) * _1120;
    precise float _1172 = _1171 + (-_1131);
    precise float _1173 = (-0.4000000059604644775390625) * _1135;
    precise float _1176 = clamp(max(_1139, _1173), 0.0, 1.0) + clamp(max(_1135, _1140), 0.0, 1.0);
    precise float _1177 = _1176 + clamp(max(_1150, _1163), 0.0, 1.0);
    precise float _1178 = _1177 + clamp(max(_1144, _1166), 0.0, 1.0);
    precise float _1179 = (-0.4000000059604644775390625) * _1154;
    precise float _1182 = (-0.4000000059604644775390625) * _1158;
    precise float _1183 = _1178 + clamp(max(_1158, _1179), 0.0, 1.0);
    precise float _1186 = (-0.4000000059604644775390625) * _1172;
    precise float _1187 = _1183 + clamp(max(_1154, _1182), 0.0, 1.0);
    precise float _1190 = (-0.4000000059604644775390625) * _1162;
    precise float _1191 = _1187 + clamp(max(_1162, _1186), 0.0, 1.0);
    precise float _1194 = _1191 + clamp(max(_1172, _1190), 0.0, 1.0);
    precise float _1196 = uintBitsToFloat(ssbo_1_1.data[26u + buf0_dword_off]) * _1194;
    precise float _1197 = _1196 + _1130;
    precise float _1199 = 2.0 * _1197;
    barrier();
    imageStore(cs_img1, ivec2(uvec2((gl_WorkGroupID.z & 3u) | (((gl_WorkGroupID.x * 8u) + gl_LocalInvocationID.x) << 2u), (gl_WorkGroupID.z >> 2u) | (((gl_WorkGroupID.y * 8u) + gl_LocalInvocationID.y) << 2u))), vec4(vec4(clamp(_1199, 0.0, 1.0), 0.0, 0.0, 0.0).x, vec4(0.0, 1.0, 0.0, 0.0).x, vec4(0.0, 1.0, 0.0, 0.0).x, vec4(0.0, 1.0, 0.0, 0.0).x));
}


'''



_DEINTERLEAVE=r"""#version 430
layout(local_size_x=8,local_size_y=8,local_size_z=1) in;
layout(r32f,binding=0) uniform writeonly image2DArray uPhases;
uniform sampler2D uDepth;
uniform ivec2 uInputSize; uniform ivec2 uPhaseSize; uniform ivec2 uArraySize;
uniform float uBackgroundDepth;
void main(){
 ivec2 q=ivec2(gl_GlobalInvocationID.xy);
 if(any(greaterThanEqual(q,uArraySize)))return;
 for(int py=0;py<4;++py)for(int px=0;px<4;++px){
  ivec2 hp=q*4+ivec2(px,py);
  ivec2 p=min(hp*2,uInputSize-ivec2(1));
  float d0=texelFetch(uDepth,p,0).r;
  float d1=texelFetch(uDepth,min(p+ivec2(1,0),uInputSize-ivec2(1)),0).r;
  float d2=texelFetch(uDepth,min(p+ivec2(0,1),uInputSize-ivec2(1)),0).r;
  float d3=texelFetch(uDepth,min(p+ivec2(1,1),uInputSize-ivec2(1)),0).r;
  d0=d0>0.0?d0:uBackgroundDepth;d1=d1>0.0?d1:uBackgroundDepth;
  d2=d2>0.0?d2:uBackgroundDepth;d3=d3>0.0?d3:uBackgroundDepth;
  float z=min(min(d0,d1),min(d2,d3));
  imageStore(uPhases,ivec3(q,4*py+px),vec4(z));
 }
}"""

_UPSAMPLE=r"""#version 430
layout(local_size_x=8,local_size_y=8,local_size_z=1) in;
layout(r8,binding=0) uniform writeonly image2D uOutput;
uniform sampler2D uHalfAO; uniform sampler2D uDepth;
uniform ivec2 uInputSize; uniform ivec2 uHalfSize;
void main(){
 ivec2 p=ivec2(gl_GlobalInvocationID.xy);
 if(any(greaterThanEqual(p,uInputSize)))return;
 if(texelFetch(uDepth,p,0).r<=0.0){imageStore(uOutput,p,vec4(1.0));return;}
 vec2 h=(vec2(p)+0.5)*vec2(uHalfSize)/vec2(uInputSize)-0.5;
 ivec2 b=ivec2(floor(h));vec2 f=fract(h);
 ivec2 hi=uHalfSize-ivec2(1);
 float a=texelFetch(uHalfAO,clamp(b,ivec2(0),hi),0).r;
 float c=texelFetch(uHalfAO,clamp(b+ivec2(1,0),ivec2(0),hi),0).r;
 float d=texelFetch(uHalfAO,clamp(b+ivec2(0,1),ivec2(0),hi),0).r;
 float e=texelFetch(uHalfAO,clamp(b+ivec2(1,1),ivec2(0),hi),0).r;
 float x=mix(mix(a,c,f.x),mix(d,e,f.x),f.y);
 imageStore(uOutput,p,vec4(x));
}"""


def _control_words(w,h):
    if w<=0 or h<=0: raise ValueError("phase dimensions must be positive")
    return b"".join(struct.pack("<f",v) for v in
        [1.0/w,1.0/h,0.0,0.0,*CAPTURED_DEPTH_REFERENCES,*CAPTURED_WEIGHTS])


def _compute_source():
    s="#version 430\n"+_CORE_SOURCE[_CORE_SOURCE.index("layout(local_size_x"):]
    s=s.replace("layout(binding = 2) uniform writeonly image2D cs_img1;",
                "layout(r8,binding=0) uniform writeonly image2D cs_img1;",1)
    s=s.replace("uniform AuxData push_data;","uniform ivec2 uOutputSize;",1)
    s=s.replace("uint buf0_dword_off = bitfieldExtract(push_data.buf_offsets0.x, int(0u), int(8u)) >> 2u;",
                "uint buf0_dword_off = 0u;",1)
    s=s.replace("SPIRV_Cross_Combinedcs_img0cs_samp0","uDepthArray")
    s=s.replace("uniform sampler2DArray uDepthArray;",
                "layout(binding=0) uniform sampler2DArray uDepthArray;",1)
    old="""imageStore(cs_img1, ivec2(uvec2((gl_WorkGroupID.z & 3u) | (((gl_WorkGroupID.x * 8u) + gl_LocalInvocationID.x) << 2u), (gl_WorkGroupID.z >> 2u) | (((gl_WorkGroupID.y * 8u) + gl_LocalInvocationID.y) << 2u))), vec4(vec4(clamp(_1199, 0.0, 1.0), 0.0, 0.0, 0.0).x, vec4(0.0, 1.0, 0.0, 0.0).x, vec4(0.0, 1.0, 0.0, 0.0).x, vec4(0.0, 1.0, 0.0, 0.0).x));"""
    new="""ivec2 out_xy=ivec2((gl_WorkGroupID.z&3u)|(((gl_WorkGroupID.x*8u)+gl_LocalInvocationID.x)<<2u),(gl_WorkGroupID.z>>2u)|(((gl_WorkGroupID.y*8u)+gl_LocalInvocationID.y)<<2u));
if(all(lessThan(out_xy,uOutputSize)))imageStore(cs_img1,out_xy,vec4(clamp(_1199,0.0,1.0)));"""
    if old not in s: raise RuntimeError("5881 output store pattern mismatch")
    return s.replace(old,new,1)


class SceneAO:
    """Full-size R8 result from source-exact 5881 taps/reduction."""

    T2D,T2DA=0x0DE1,0x8C1A; TEX0=0x84C0
    R8,R32F,RED,FLOAT,UBYTE=0x8229,0x822E,0x1903,0x1406,0x1401
    MINF,MAGF,WRAPS,WRAPT,NEAREST,CLAMP=0x2801,0x2800,0x2802,0x2803,0x2600,0x812F
    SSBO,DYNAMIC,WRITE=0x90D2,0x88E8,0x88B9
    BARRIER=0x28
    FUNCS={
     "glBindImageTexture":(None,[ctypes.c_uint,ctypes.c_uint,ctypes.c_int,ctypes.c_ubyte,ctypes.c_int,ctypes.c_uint,ctypes.c_uint]),
     "glDispatchCompute":(None,[ctypes.c_uint,ctypes.c_uint,ctypes.c_uint]),
     "glMemoryBarrier":(None,[ctypes.c_uint]),
     "glGenBuffers":(None,[ctypes.c_int,ctypes.c_void_p]),
     "glDeleteBuffers":(None,[ctypes.c_int,ctypes.c_void_p]),
     "glBindBuffer":(None,[ctypes.c_uint,ctypes.c_uint]),
     "glBufferData":(None,[ctypes.c_uint,ctypes.c_ssize_t,ctypes.c_void_p,ctypes.c_uint]),
     "glBindBufferBase":(None,[ctypes.c_uint,ctypes.c_uint,ctypes.c_uint]),
     "glTexImage3D":(None,[ctypes.c_uint,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p]),
     "glUniform2i":(None,[ctypes.c_int,ctypes.c_int,ctypes.c_int]),
     "glGetIntegerv":(None,[ctypes.c_uint,ctypes.c_void_p]),
     "glGetIntegeri_v":(None,[ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p]),
    }

    def __init__(self,gl,shading):
        self.gl,self.shading=gl,shading;self.fn=self._load(gl)
        self.pre=self._program(_DEINTERLEAVE);self.core=self._program(_compute_source());self.up=self._program(_UPSAMPLE)
        self.array=self.half_output=self.output=self.ssbo=0;self.extent=self.array_extent=self.half_extent=None
        self.pre_u=[shading.glGetUniformLocation(self.pre,n.encode()) for n in ("uDepth","uInputSize","uPhaseSize","uArraySize","uBackgroundDepth")]
        self.core_depth=shading.glGetUniformLocation(self.core,b"uDepthArray")
        self.core_size=shading.glGetUniformLocation(self.core,b"uOutputSize")
        self.up_u=[shading.glGetUniformLocation(self.up,n.encode()) for n in ("uHalfAO","uDepth","uInputSize","uHalfSize")]
        gl.glGenTextures.argtypes=[ctypes.c_int,ctypes.c_void_p];gl.glDeleteTextures.argtypes=[ctypes.c_int,ctypes.c_void_p]
        gl.glBindTexture.argtypes=[ctypes.c_uint,ctypes.c_uint];gl.glTexParameteri.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.c_int]
        gl.glTexImage2D.argtypes=[ctypes.c_uint,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p]
        ids=(ctypes.c_uint*3)();gl.glGenTextures(3,ids);self.array,self.half_output,self.output=ids[0],ids[1],ids[2]
        b=ctypes.c_uint();self.fn["glGenBuffers"](1,ctypes.byref(b));self.ssbo=b.value

    @classmethod
    def _load(cls,gl):
        gl.wglGetProcAddress.argtypes=[ctypes.c_char_p];gl.wglGetProcAddress.restype=ctypes.c_void_p
        out={}
        for n,(r,a) in cls.FUNCS.items():
            p=gl.wglGetProcAddress(n.encode())
            if p:out[n]=ctypes.WINFUNCTYPE(r,*a)(p)
            elif hasattr(gl,n):
                # Core 1.1 entry points are exported by opengl32, not returned by WGL.
                fn=getattr(gl,n);fn.argtypes=a;fn.restype=r;out[n]=fn
            else:raise RuntimeError(f"OpenGL function {n} unavailable")
        return out

    def _program(self,src):
        sh=self.shading;obj=sh._compile(0x91B9,src);p=sh.glCreateProgram()
        sh.glAttachShader(p,obj);sh.glLinkProgram(p);sh.glDeleteShader(obj)
        ok=ctypes.c_int();sh.glGetProgramiv(p,0x8B82,ctypes.byref(ok))
        if not ok.value:
            msg=ctypes.create_string_buffer(8192);sh.glGetProgramInfoLog(p,8192,None,msg)
            sh.glDeleteProgram(p);raise RuntimeError("AO compute link failed: "+msg.value.decode(errors="replace"))
        return p

    def _allocate(self,w,h):
        hw,hh=(w+1)//2,(h+1)//2;aw,ah=(hw+3)//4,(hh+3)//4;gl=self.gl
        for target,obj,tw,th,internal in ((self.T2DA,self.array,aw,ah,self.R32F),
                                          (self.T2D,self.half_output,hw,hh,self.R8),
                                          (self.T2D,self.output,w,h,self.R8)):
            gl.glBindTexture(target,obj)
            for k,v in ((self.MINF,self.NEAREST),(self.MAGF,self.NEAREST),(self.WRAPS,self.CLAMP),(self.WRAPT,self.CLAMP)):gl.glTexParameteri(target,k,v)
            if target==self.T2DA:self.fn["glTexImage3D"](target,0,internal,tw,th,16,0,self.RED,self.FLOAT,None)
            else:gl.glTexImage2D(target,0,internal,tw,th,0,self.RED,self.UBYTE,None)
        self.extent,self.array_extent,self.half_extent=(w,h),(aw,ah),(hw,hh)

    def _capture_state(self):
        """Capture the bindings this backend mutates, including indexed slots."""
        f=self.fn; iv=ctypes.c_int
        def get(pname):
            v=iv();f["glGetIntegerv"](pname,ctypes.byref(v));return v.value
        def geti(pname,index):
            v=iv();f["glGetIntegeri_v"](pname,index,ctypes.byref(v));return v.value
        active=get(0x84E0);program=get(0x8B8D)
        self.shading.glActiveTexture(self.TEX0)
        tex2d,tex2da=get(0x8069),get(0x8C1D)
        self.shading.glActiveTexture(self.TEX0+1);tex2d1=get(0x8069)
        ssbo_generic=get(0x90D3);ssbo_indexed=geti(0x90D3,0)
        image=(geti(0x8F3A,0),geti(0x8F3B,0),geti(0x8F3C,0),
               geti(0x8F3D,0),geti(0x8F3E,0),geti(0x906E,0))
        return active,program,tex2d,tex2da,tex2d1,ssbo_generic,ssbo_indexed,image

    def _restore_state(self,state):
        active,program,tex2d,tex2da,tex2d1,ssbo_generic,ssbo_indexed,image=state
        f=self.fn;gl=self.gl;sh=self.shading
        sh.glActiveTexture(self.TEX0)
        gl.glBindTexture(self.T2D,tex2d);gl.glBindTexture(self.T2DA,tex2da)
        sh.glActiveTexture(self.TEX0+1);gl.glBindTexture(self.T2D,tex2d1)
        f["glBindBufferBase"](self.SSBO,0,ssbo_indexed)
        f["glBindBuffer"](self.SSBO,ssbo_generic)
        if image[0]:f["glBindImageTexture"](0,image[0],image[1],image[2],image[3],image[4],image[5])
        else:f["glBindImageTexture"](0,0,0,0,0,0x88B8,0x8058)
        sh.glUseProgram(program);sh.glActiveTexture(active)

    def render(self,depth_tex,normal_tex,width,height,projection):
        """Return full-size R8 from positive linear depth, with a viewer half-depth adapter.

        Captured 5881 consumes half-viewport linear depth. This path reduces the
        supplied full-size viewer depth with a 2x2 minimum, maps background zero
        to the perspective far plane, runs source-exact phase/core equations at
        half resolution, then bilinearly upsamples and restores background to 1.
        The 5845 depth conversion and subsequent native filters are not included.
        """
        del normal_tex
        w,h=int(width),int(height)
        if w<=0 or h<=0 or not depth_tex:raise ValueError("positive dimensions and depth texture required")
        _projection_diagonal(projection);far=_projection_far(projection)
        state=self._capture_state()
        try:
            # Allocation mutates both texture targets; use the captured unit-0 bindings.
            if self.extent!=(w,h):
                self.shading.glActiveTexture(self.TEX0)
                self._allocate(w,h)
            hw,hh=self.half_extent;aw,ah=self.array_extent;gl=self.gl;sh=self.shading;f=self.fn
            sh.glActiveTexture(self.TEX0);gl.glBindTexture(self.T2D,int(depth_tex))
            sh.glUseProgram(self.pre);sh.glUniform1i(self.pre_u[0],0)
            f["glUniform2i"](self.pre_u[1],w,h);f["glUniform2i"](self.pre_u[2],hw,hh)
            f["glUniform2i"](self.pre_u[3],aw,ah);sh.glUniform1f(self.pre_u[4],far)
            f["glBindImageTexture"](0,self.array,0,1,0,self.WRITE,self.R32F)
            f["glDispatchCompute"]((aw+7)//8,(ah+7)//8,1);f["glMemoryBarrier"](self.BARRIER)
            sh.glUseProgram(self.core);sh.glUniform1i(self.core_depth,0);f["glUniform2i"](self.core_size,hw,hh)
            control=ctypes.create_string_buffer(_control_words(aw,ah));f["glBindBuffer"](self.SSBO,self.ssbo)
            f["glBufferData"](self.SSBO,ctypes.sizeof(control)-1,control,self.DYNAMIC);f["glBindBufferBase"](self.SSBO,0,self.ssbo)
            gl.glBindTexture(self.T2DA,self.array);f["glBindImageTexture"](0,self.half_output,0,0,0,self.WRITE,self.R8)
            f["glDispatchCompute"]((aw+7)//8,(ah+7)//8,16);f["glMemoryBarrier"](self.BARRIER)
            sh.glUseProgram(self.up);sh.glUniform1i(self.up_u[0],0);sh.glUniform1i(self.up_u[1],1)
            f["glUniform2i"](self.up_u[2],w,h);f["glUniform2i"](self.up_u[3],hw,hh)
            sh.glActiveTexture(self.TEX0);gl.glBindTexture(self.T2D,self.half_output)
            sh.glActiveTexture(self.TEX0+1);gl.glBindTexture(self.T2D,int(depth_tex))
            f["glBindImageTexture"](0,self.output,0,0,0,self.WRITE,self.R8)
            f["glDispatchCompute"]((w+7)//8,(h+7)//8,1);f["glMemoryBarrier"](self.BARRIER)
            return self.output
        finally:self._restore_state(state)

    def close(self):
        for n in ("pre","core","up"):
            p=getattr(self,n,0)
            if p:self.shading.glDeleteProgram(p);setattr(self,n,0)
        if self.array or self.half_output or self.output:
            self.gl.glDeleteTextures(3,(ctypes.c_uint*3)(self.array,self.half_output,self.output));self.array=self.half_output=self.output=0
        if self.ssbo:self.fn["glDeleteBuffers"](1,(ctypes.c_uint*1)(self.ssbo));self.ssbo=0


def _projection_diagonal(m):
    if hasattr(m,"ravel"):m=m.ravel()
    a=list(m)
    if len(a)==4 and all(hasattr(x,"__iter__") for x in a):a=[v for row in a for v in row]
    if len(a)!=16:raise ValueError("projection must be flat or 4x4")
    x,y=float(a[0]),float(a[5])
    if not(math.isfinite(x) and math.isfinite(y) and x!=0 and y!=0):raise ValueError("invalid projection diagonal")
    return x,y


def _projection_far(m):
    """Recover positive far plane from a conventional OpenGL perspective matrix."""
    if hasattr(m,"ravel"):m=m.ravel()
    a=list(m)
    if len(a)==4 and all(hasattr(x,"__iter__") for x in a):a=[v for row in a for v in row]
    if len(a)!=16:raise ValueError("projection must be flat or 4x4")
    den=float(a[10])+1.0;num=float(a[14])
    far=num/den if den else float("nan")
    if not(math.isfinite(far) and far>0):raise ValueError("projection must be finite perspective with positive far plane")
    return far


def _phase_index(x,y):
    return 4*(y&3)+(x&3)
