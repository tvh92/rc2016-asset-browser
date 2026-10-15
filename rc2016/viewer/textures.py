"""Textures for the 3D preview and the Textures tab, read in memory.

Only `export_textures` writes files, and only into the folder the user picks.

The viewer uploads the game's block-compressed data (BC1-BC7) unchanged; the
graphics card decodes it. Only the material's `_c` (base colour) texture is
used for the Textured mode; Shaded also uses `_g`, `_n`, the self-lit term and
the per-graph rules in GRAPH_RULES. This is a viewing aid, not the game shading.
"""
from __future__ import annotations

from pathlib import Path
import struct
import threading

from rc2016.formats.archive import asset_data, asset_references, decompress, lz4_block
from rc2016.formats.index import references
from rc2016.formats.texture import dds_bytes, parse_header, ps4_deswizzle_slices

# DXGI format -> OpenGL internal format. sRGB variants are uploaded as their
# UNORM twin on purpose: the fixed-function viewer lights in display space.
GL_COMPRESSED = {
    71: 0x83F1, 72: 0x83F1,   # BC1  -> COMPRESSED_RGBA_S3TC_DXT1
    74: 0x83F2, 75: 0x83F2,   # BC2  -> DXT3
    77: 0x83F3, 78: 0x83F3,   # BC3  -> DXT5
    80: 0x8DBB,               # BC4  -> COMPRESSED_RED_RGTC1
    83: 0x8DBD,               # BC5  -> COMPRESSED_RG_RGTC2
    95: 0x8E8F, 96: 0x8E8E,   # BC6H -> BPTC float (unsigned / signed)
    98: 0x8E8C, 99: 0x8E8C,   # BC7  -> COMPRESSED_RGBA_BPTC_UNORM
}
GL_UNCOMPRESSED = {28: 0x1908, 29: 0x1908, 61: 0x1909, 49: 0x8227}   # RGBA8, RGBA8 sRGB, R8 (luminance), R8G8 (RG)
SRGB_FORMATS = {29, 72, 75, 78, 91, 93, 99}   # decoded to linear in the shader
# Below this the small built-in mip is too blurry; use the streamed full size.
MIN_PREVIEW_SIZE = 512   # ~0.25 s per 1024² texture to untile


ROLES = ('_c', '_g', '_n', '_pick', '_h')   # base colour, specular + gloss, tangent normal, colour-pick palette, tint mask
PICK_SUFFIX = '_colorpick_c'
# Novalis tree leaf (rp_nov_plant_tree_foliage_transparent, writer 0476D929). Slot hashes are read from the
# material slot tables of the Novalis tree leaf materials; the cma/cmb roles are the names of the bound
# textures. Source resource tables establish the slots; the equation and E/S/Y are decoded.
LEAF_CMA_SLOT = 0x49A7866B96F96A72
LEAF_CMB_SLOT = 0x49A7866B4984DA89
LEAF_GLOBAL_NORMAL_SLOT = 0x49A7866B67D6383E
# Material keys for the leaf writer's CB2 dwords (foliage/README.md): E = CB2[0] Fresnel exponent,
# S = CB2[2] colour scale, Y = CB2[1] palette V. Per-material values, read like the other material terms.
LEAF_EXPONENT_KEY, LEAF_SCALE_KEY, LEAF_PALETTE_Y_KEY = 0x9BCD75AC, 0xAAC47D17, 0x0F5B49E2
# PLACEHOLDER: palette U. The writer multiplies GPU record floats at offsets 112 and 120 (transform block,
# instance/report.md); the CPU producer-to-zone linkage is unproven, so U is fixed at 0.5.
LEAF_PALETTE_U_PLACEHOLDER = 0.5
# Novalis giant glow mushroom writer, deferred pass ('nov_plant_mushroom_glow_fresnel_01' graph, pixel program
# 0xF69ADC40). MRT1 RGB (albedo) = mix(C.rg, _c.rg, F) with F = (1.001 - N.V)^E, B = C.b; MRT1 alpha is the
# self-lit term 0.2 log2(K _g.B + 1). C = CB2[0..2] (vec3 key), E = CB2[3], K = CB2[4]. Graph defaults: C
# (0.381, 0.687, 1), E 0.25, K 2. The writer's second Fresnel (sign-folded, unnormalised) is taken as the same F.
MUSHROOM_FRESNEL_COLOUR_KEY, MUSHROOM_FRESNEL_EXPONENT_KEY, MUSHROOM_SELF_LIT_KEY = 0x3DA84DFD, 0x764792E8, 0xA71C1591
MUSHROOM_FRESNEL_PRESENTATION = dict(colour=(0.38132601976394653, 0.68668532371521, 1.0), exponent=0.25, self_lit=2.0)
# hair_newspec_01 forward colour pass (pass 0, pixel program 94CE8CCE; serialized resources api5/6/7 = _c/_g/_n).
# _c is sampled for alpha only (coverage, dmask 8); the albedo is the material vec3 A (CB2 dw0-2), F0 = B (dw4-6) * _g.G
# and the gloss argument is 0.71 * _g.R, both scaled by w = 1 - sat((1.001 - |N.V|)^E) (E = dw9, geometry normal);
# the tangent normal is x, y = 0.3 (2 RG - 1), z = 1 - x^2 - y^2. dw3/dw7 are two diffuse roughness values.
HAIR_KEYS = dict(albedo=0xA67F3D37, spec=0xFEAF9A36, rough_a=0x2B6D6A7B, rough_b=0xB2643BC1, rim=0x4D385B45)
HAIR_SLOTS = {'_c': 0x49A7866B1776B09C, '_g': 0x49A7866B6F88A378, '_n': 0x49A7866BE189D5CA}
HAIR_DEFAULTS = dict(albedo=(0.0043914420530200005, 0.0043914420530200005, 0.006995410192757845),
                     spec=(0.16202937066555023, 0.08437620848417282, 0.16202937066555023),
                     rough_a=0.8500000238418579, rough_b=0.009999999776482582, rim=3.0)
HAIR_GLOSS_SCALE, HAIR_NORMAL_STRENGTH = 0.71, 0.3
# mo_gbl_color_tint_01 writer BE8C3EE0 (Animation/pipeline/Research/Materials/ColorTint01/COLOR_TINT_DECODE.md)
TINT_KEYS = dict(spec_tint=0x6B591974, mask_b=0x8ED5C5E8, colour_a=0xA815C29F, colour_b=0x311C9325, power_a=0x9E7A020A,
                 weight_b=0x482ECFEB, weight_a=0xD1279E51, power_b=0x077353B0)
TINT_G_SWITCH = 0x7107A2D8   # < 0.5: grey _g (R gloss, G spec); >= 0.5: coloured _g (rgb F0, gloss a^2)
# Self-lit K (= albedo * mask * K) by family: the default deferred key, then the
# coloured-gloss-emissive and emissive-in-c-alpha keys (rc2016/shaders/families.py).
# The key in the graph's own parameter table wins: materials can store keys their graph never reads.
SELF_LIT_K_KEYS = (0x66EFA55B, 0x06F19114, 0xF8C61683)
SPECULAR_SCALE_KEY, GLOSS_SCALE_KEY = 0x62835AF9, 0x24F4CA4C
# Antenna glow shell forward colour pass 93695188: amplitude is engine constant CB0 dword 115, not decoded.
# Same labelled convention as facing_glow: captured 0.01 (near off), presentation value 0.5. The blink
# sine and the cutoff, vertex alpha and fog fade are frozen (sin 0, cutoff 0, alpha 1, fog_fade 1).
BRIGHT_GLOW_PRESENTATION = dict(amplitude=0.5, amplitude_input='CB0 dword 115', captured_amplitude=0.01,
                                presentation=True, writer='93695188',
                                frozen_inputs=dict(sin=0.0, cutoff=0.0, alpha=1.0, fog_fade=1.0))
# Eye cornea forward colour pass A36A97CB (hero_eye_cornea): literals read from the instruction words.
# Fresnel/normal scales and the highlight lobe; the light loop, probes, screen-space reflections, fog and
# the per-instance terms (CB0 dword 95 normal strength, instance floats at byte 8/12, flag bits) are not decoded.
EYE_CORNEA_CONSTANTS = dict(fresnel_normal_scale=0.555, fresnel_bias=1.00100004673, fresnel_power=4.0,
                            half_vector_view_weight=0.49, distance_k=0.08, alpha_bias=0.06, highlight_gain=4.0,
                            highlight_gain_slope=3.0, writer='A36A97CB',
                            frozen_inputs=dict(normal_strength=1.0, instance_fade=0.0, instance_alpha=1.0, light_term=1.0,
                                               fog_fade=1.0))
# Decoded writers that do not read _g as coloured F0 + gloss alpha (first matching prefix wins).
# g_mode 'grey': F0 = grey spec*_g.G, gloss = sqrt(sat(gloss*_g.R)); 'grey_skin': the basic_skin
# front-end (F0 cap 0.5). self_lit 'g_blue' takes the mask from _g.B instead of _c.a.
GRAPH_RULES = (
    # Cached SPIR-V E08357DC; Materials/ZaBasicNormalGloss/ZA_BASIC_NORMAL_GLOSS_DECODE.md.
    ('required/materials/za_basic_normal_gloss.',
     dict(g_mode='grey', self_lit_key=None, za_fresnel_key=0x4D385B45)),
    ('materialgraph/global/basic_skin/', dict(g_mode='grey_skin')),
    # Writer 5EBDECA4 (no self-lit) is embedded only by these four (CorpusMismatchScan/writer_join.json).
    *((f'required/materials/basic_normal_gloss{suffix}.', dict(g_mode='grey', self_lit_key=None))
      for suffix in ('', '_detail', '_monitor_dep', '_aniso')),
    # Other basic_normal_gloss* graphs (emissive, fresnel, ...) have undecoded writers: grey _g by name
    # only (as before 2026-10-08), self-lit key from the graph's own table.
    # Six independently traced writers; Materials/BasicNormalGlossVariants/BASIC_NORMAL_GLOSS_VARIANTS_DECODE.md.
    ('required/materials/basic_normal_gloss_emissive.',
     dict(g_mode='grey', self_lit='g_blue', self_lit_key=0x66EFA55B, bng=1)),
    ('required/materials/basic_normal_gloss_emissive_detail.',
     dict(g_mode='grey', self_lit='g_blue', self_lit_key=0x66EFA55B, bng=2)),
    ('required/materials/basic_normal_gloss_fresnel_detail.',
     dict(g_mode='grey', self_lit_key=None, bng=3)),
    ('required/materials/basic_normal_gloss_emissive_colored_spec.',
     dict(g_mode='grey', self_lit_key=0x66EFA55B, bng=4)),
    ('required/materials/basic_normal_gloss_aniso_incan.',
     dict(g_mode='grey', self_lit_key=0x06F19114, bng=5)),
    ('required/materials/basic_normal_gloss_specfresnel_emissive.',
     dict(g_mode='grey', self_lit='g_blue', self_lit_key=0x66EFA55B, bng=6)),
    ('required/materials/basic_normal_gloss', dict(g_mode='grey')),
    # Coloured-gloss rim writers, from the native translator's material-bound DAG (pass 0 and 1 agree):
    # coloured F0 = spec * _g.rgb, gloss = sqrt(sat(Q * _g.a^2)), albedo = _c * (1 + A * mask * rim^P) with
    # rim = 1.001 - sat(N.V), A = 0xAAC47D17, P = 0xB7BAC19F. A5343A0C (7): N is the interpolated geometry
    # normal, mask 1, self-lit _c.a * K. 76C79AA4 (8): N is the tangent-mapped _n normal, mask _c.a, no self-lit.
    # 14B1CC68 (9): N is the mapped normal, mask = the _m slot's R, self-lit = _m.G * K. The detail-normal
    # samples of 8 and 9 (slot 0x45A52060) are not composed.
    ('required/materials/basic_normal_coloredgloss_emissive_fresnel.', dict(self_lit_key=0x06F19114, bng=7)),
    ('required/materials/basic_normal_coloredgloss_fresnel_detail.', dict(self_lit_key=None, bng=8)),
    ('required/materials/basic_normla_gloss_fresnel_detail_emissive.', dict(self_lit='h_green', self_lit_key=0x66EFA55B, bng=9)),
    ('materialgraph/global/basic_incandescence/', dict(g_mode='grey', self_lit='g_blue', self_lit_key=0x5FD56522)),
    # Other models/enm_blarg_saucer/evidence/COLORPICK_DECODE.md (writer 66744D01)
    ('materialgraph/global/basic_colored_spec_emmisive_in_c_alpha/blarg_saucer_colorpick.',
     dict(g_mode='grey', self_lit='g_blue', self_lit_key=0x06F19114, pick=(0x40520184, 0xD257B634, 0x124A2F94))),
    # Cached SPIR-V 63EEBF51 (ID in the graph's OrbShdr): coloured F0 = P*_g.rgb, gloss = sqrt(Q*_g.a^2), no self-lit;
    # Materials/BasicColoredSpec/BASIC_COLORED_SPEC_DECODE.md. Exact graph only; 1BC3FDDF/F8FBB38F are undecoded.
    ('materialgraph/global/basic_colored_spec/basic_colored_spec.',
     dict(g_mode='colored', self_lit_key=None)),
    # Exact graph only: the jn_/_detail/_random_color tint variants have their own, undecoded writers.
    ('materialgraph/environment/global/gbl_color_tint_01/mo_gbl_color_tint_01.',
     dict(g_switch=TINT_G_SWITCH, self_lit_key=None, tint=True)),
    # Forward (glass) writers: drawn after the opaque batches with the material's blend state.
    # 'params' are (name, key, components) read as material value, else graph default.
    # Other models/enm_blarg_space_commando/evidence/glass/CHARACTER_GLASS_DECODE.md (34D5B21C)
    ('materialgraph/global/basic_glass/character_glass.',
     dict(forward='character_glass', params=(
         ('body', 0x7C456482, 3), ('gloss', 0x24F4CA4C, 1), ('tint', 0x4B4DAB19, 3), ('tint_scale', 0xEF9CE5DD, 1),
         ('alpha_exp', 0x2B68D24C, 1), ('env_scale', 0x80C092EE, 1), ('alpha_bias', 0x4E5B65FC, 1),
         ('rim_bias', 0x42170E5E, 1), ('env_exp', 0xACE4D867, 1), ('rim_exp', 0xECE213A3, 1)))),
    # PROVISIONAL (review only, not game-matched). Writer 4DF1D438 is confirmed for veh_micron_ship_glass only;
    # the other 10 materials are assumed. Alpha constants are grounded (module_10182). Colour is an approximation:
    # the rim colour, rim multiplier/pow/cap, the env sample and fog are not in the kernel yet.
    # Decode: Animation/pipeline/Research/Materials/NoRuleDecodes/character_glass_02/decode.md.
    ('materialgraph/global/basic_glass/character_glass_02.',
     dict(forward='character_glass', constants=dict(alpha_exp=0.0, alpha_bias=0.0), params=(
         ('body', 0x7C456482, 3), ('gloss', 0x24F4CA4C, 1), ('tint', 0x4B4DAB19, 3), ('tint_scale', 0xEF9CE5DD, 1),
         ('env_scale', 0x80C092EE, 1), ('rim_bias', 0x42170E5E, 1), ('env_exp', 0xACE4D867, 1), ('rim_exp', 0xECE213A3, 1)))),
    # Other models/enm_blarg_saucer/evidence/GLASS_DECODE.md (FEEC00CE). The graph binds two textures,
    # by slot hash: the colour-pick palette (api5) and Patterns_cma in the _n slot (api6). The
    # material's _c/_g use slots this graph does not have, so the writer never reads them.
    ('materialgraph/global/basic_glass_patterned/blarg_saucer_glass.',
     dict(forward='glass_patterned', slots={'_pick': 0x628AD5A840ED6C83, '_n': 0x49A7866B7AE34E7A}, params=(
         ('tiling', 0x375AF9AC, 1), ('normal_strength', 0xCC7457CD, 1), ('f0', 0x223DC13C, 1),
         ('gloss', 0xD2891F48, 1), ('pick_u', 0x124A2F94, 1), ('rim_bias', 0xAF7D5E6B, 1), ('rim_exp', 0x6D6903B7, 1)))),
    # AAED778C at t=0; Materials/ArchEmissiveSequence/ARCH_EMISSIVE_SEQUENCE_DECODE.md.
    ('materialgraph/environment/global/architecture/gbl_arch_emissive_sequence_01/mo_gbl_arch_emissive_sequence_01.',
     dict(g_mode='grey', self_lit_key=None, arch_sequence=True)),
    # EB1AC2CD bounded deferred writer; Materials/VehicleRandomColor/VEHICLE_RANDOM_COLOR_DECODE.md.
    ('materialgraph/vehicle/mo_veh_random_color_01/mo_veh_random_color_01.',
     dict(g_mode='vehicle', self_lit_key=None, vehicle=True)),
    # ColorTintVariants exact writers; complete tint-POM writer 4E4D7894.
    ('materialgraph/environment/global/gbl_color_tint_01/jn_mo_gbl_color_tint_01_random_color.',
     dict(g_mode='grey', self_lit_key=None, tint=True, tint_variant='random')),
    ('materialgraph/environment/global/gbl_color_tint_01/jn_color_tint_detail.',
     dict(g_mode='grey', self_lit_key=None, tint=True, tint_variant='detail')),
    ('materialgraph/environment/global/gbl_color_tint_01/jn_color_tint_detail_parallax.',
     dict(g_mode='grey', self_lit_key=None, tint=True, tint_variant='detail', parallax='tint')),
    ('materialgraph/environment/global/gbl_color_tint_01/jn_gbl_color_tint_01_world_detail.',
     dict(g_mode='grey', self_lit_key=None, tint=True, tint_variant='world')),
    ('materialgraph/environment/global/gbl_color_tint_01/jn_gbl_color_tint_01_world_detail_normal.',
     dict(g_mode='grey', self_lit_key=None, tint=True, tint_variant='world_normal')),
    # 5FADAD33 / cached E80EA4C1; Materials/ParallaxOcclusion/PARALLAX_OCCLUSION_DECODE.md.
    # Native 5FADAD33 height-alpha march/intersection and seven-sample albedo shadows.
    ('materialgraph/global/basic_parallax_occlusion/basic_parallax_occlusion.',
     dict(g_mode='grey', self_lit_key=None, parallax_outputs=True, parallax=True)),
    # 2685144F cached forward material kernel and CA81B14F deferred writer; Materials/GlowMask.
    ('required/materials/jn_basic_normal_gloss_glow_mask.',
     dict(g_mode='grey', self_lit='glow_mask', self_lit_key=0x06F19114, glow=1)),
    ('required/materials/jn_basic_normal_gloss_glow_mask_02.',
     dict(g_mode='grey', self_lit='glow_mask02', self_lit_key=0x06F19114, glow=2)),

    # 886AAC94 resource-table deferred writer; Materials/RockBlend/ROCK_BLEND_DECODE.md.
    ('materialgraph/environment/global/rock/mo_blend_01/mo_blend_01.',
     dict(g_mode='grey', self_lit_key=None, rock_blend=True)),
    # A3DAA047 deferred writer (same vertex programs as 886AAC94): one breakup colour sample at native
    # world (x, z) / T, layer g/n at U * UV0, no layer colour or tint; RockBlend/ROCK_BLEND_DECODE.md.
    ('materialgraph/environment/global/rock/mo_blend_01/rp_mo_blend_color_breakup_01.',
     dict(g_mode='grey', self_lit_key=None, rock_blend='color_breakup')),
    # FE32FE79/B01E7CB2 cached deferred outputs; Materials/Foliage/FOLIAGE_DECODE.md.
    ('materialgraph/global/basic_foliage/basic_foliage.',
     dict(g_mode='grey', self_lit_key=None, foliage=True, alpha_cut=True)),
    # Tree leaves and shrubs: decoded pass-0 overlay (Novalis 0476D929 / global CDD7CD94),
    # exact source slots and material parameters, frozen preview palette U = 0.5.
    # Global has mapped-normal Fresnel and zero MRT2; runtime pass selection is unproven.
    ('materialgraph/environment/global/plant/gbl_plant_tree_foliage_transparent_01/rp_nov_plant_tree_foliage_transparent.',
     dict(alpha_cut=True, leaf_colour='novalis')),
    ('materialgraph/environment/global/plant/gbl_plant_tree_foliage_transparent_01/gbl_plant_tree_foliage_transparent_01.',
     dict(alpha_cut=True, leaf_colour='global')),
    # Novalis giant glow mushroom writer (no forward pass; opaque flags 0x230): Fresnel albedo tint and
    # self-lit mask _g.B. The second MRT2 gloss channel is not decoded; grey F0 (MRT2 RGB = _g.G) is shared.
    ('materialgraph/environment/novalis/plant/nov_plant_mushroom_glow_fresnel_01/rp_nov_plant_mushroom_glow_fresnel_01.',
     dict(g_mode='grey', self_lit='g_blue', self_lit_key=MUSHROOM_SELF_LIT_KEY, fresnel_tint=True)),
    # Forward colour pass 94CE8CCE of Hair_NewSpec_01 (Skidd's agent, mayor, Gadgetron CEO, plumber beard): constant
    # albedo/specular colours, _c alpha cut-out, ZA-style rim weight. See docs/viewer.md (Hair).
    ('required/materials/hair_newspec_01.',
     dict(g_mode='grey', self_lit_key=None, alpha_cut=True, hair=True)),
    # Cached forward 8716DC7A at t=0; Materials/StaleSelfLitKeys/STALE_SELF_LIT_KEYS_DECODE.md.
    ('materialgraph/environment/nebula/ambient/neb_amb_screen_01/mo_neb_amb_screen_01.',
     dict(self_lit_key=None, screen=True)),
    # 1012E99D/EC1E7CD3 source forward composition; Materials/TwoColorGlossMetal.
    # Viewer lights, no SSR/fog, instance opacity 1, close-range normal/AA; no runtime draw join.
    ('materialgraph/global/basic_normal_gloss_metal/basic_normal_two_color_gloss_metal.',
     dict(g_mode='grey', self_lit_key=None, two_color_metal=True)),
    # DB75867B forward terminal, explicit viewer leaves; Materials/GroundPuddles/GROUND_PUDDLES_DECODE.md.
    ('materialgraph/environment/kalebo/jn_ground_puddles.',
     dict(g_mode='grey', self_lit_key=None, puddles=True)),
    # Water PREVIEW, not a decode (family prefix, so rule coverage counts it as a fallback). The colour
    # writers f4d47ece/5476807b (light loops, 63 samples) are not translated. Decoded: the alpha-over
    # state, the textures by slot (water_tile_01n/02n), and from the normal pre-pass b9a5ef0f the
    # world-planar UV (native x, z) scale literals of the two tiles. Body colour is key 0x1DAF77B8,
    # an inferred role; opacity and gloss are presentation values (WATER_PRESENTATION).
    ('materialgraph/visualeffect/environment/pokitaru/unique/pok_uniq_water/',
     dict(forward='water', slots={'_n': 0x49A7866B69E7942C, '_pick': 0x49A7866B0D86F9E5}, params=(
         ('body', 0x1DAF77B8, 3),))),
    # Forward colour pass 0CD2AAFB rel 12552-12672: white base + red facing glow; Materials/QwarkBulb/README.md.
    ('materialgraph/character/npc/npc_qwark_robot_bulb/npc_qwark_robot_bulb.',
     dict(self_lit_key=None, facing_glow=True)),
    # Deferred writer D14A48D7 (material-bound DAG, pass 0): t = (1.001 - sat(N.V))^(0.2 (1 - S)) with the
    # tangent-mapped normal; albedo = _c * (E t + A M w (1 - t)); self-lit = K * _c.R * that red factor;
    # F0 = grey Fs, gloss = sqrt(sat(G)). w is a blink sat(1 + (1 - B)(0.5 sin(phase) - 0.5)), frozen at sin = 0.
    ('materialgraph/character/hero/hero_bright_center_light/hero_bright_center_light_variable.',
     dict(self_lit='bright_center', self_lit_key=0xB7305C21, bright_center=True)),
    # Forward colour pass 93695188 (one FP16 target, no texture), flags 0x200039: additive, back faces culled.
    # The antenna glow shell (enm_qwark_light_glow and the other materials on this graph); no _c/_g/_n is read.
    ('materialgraph/character/hero/hero_bright_center_light_glow/hero_bright_center_light_glow_variable.',
     dict(self_lit_key=None, forward='bright_glow', constants=BRIGHT_GLOW_PRESENTATION, params=(
         ('colour', 0x4E6E03FF, 3), ('colour_lo', 0x68538B05, 3), ('dist_k', 0xD76F8651, 1), ('gain', 0x5DE2C7F0, 1),
         ('dist0', 0xCF085666, 1), ('steady', 0x01B78BF6, 1), ('sharp', 0x60DC3D2F, 1), ('rate', 0xB162C569, 1)))),
    # Forward colour pass A36A97CB (flags 0x3A: alpha-over, back faces culled); the cornea over the eye sphere.
    # Slots: hemisphere_flatter_n (_n) and shared_eye_lens_c (_pick, red channel). Constants buffer dwords 0-8.
    ('materialgraph/character/hero/hero_eye_cornea/hero_eye_cornea.',
     dict(self_lit_key=None, forward='eye_cornea', constants=EYE_CORNEA_CONSTANTS,
          slots={'_n': 0x49A7866B7AE34E7A, '_pick': 0x49A7866BABDAD780}, params=(
         ('fade_end', 0x8CE80F85, 1), ('power_far', 0xB0624EB0, 1), ('reflect_base', 0xAF7D5E6B, 1),
         ('power_near', 0x621FAA21, 1), ('fade_start', 0xDBAD0A10, 1), ('reflect_weight', 0x223DC13C, 1),
         ('env_normal', 0x3F13004B, 1), ('highlight_k', 0xF8828985, 1), ('highlight_normal', 0xE14A15D1, 1)))),
)
# Leaf cut-out (alpha test). The deferred foliage writers kill a fragment when its coverage, `_c`
# alpha (basic_foliage FE32FE79/B01E7CB2; the tree graphs' 0476D929/6F0C1A0C), is below the
# interpolated vertex output attr0.z. CPU static-mesh builder 0x182a9b0 fills record byte 268
# from material section E1275683 bytes 20/21: min((byte20 + byte21) * float32(1/255), 1)
# at the no-LOD-fade baseline (writer 0x1841990). Levels/Foliage/README.md: Threshold recovery.
# Runtime draw cut/fade and the special flag-8 override are omitted. Missing/truncated state
# retains the INFERRED half-coverage preview fallback; it is not an engine default.
FOLIAGE_ALPHA_CUTOFF_FALLBACK = 0.5
# Water preview values that are not decoded (labelled presentation): opacity at normal incidence
# (Fresnel raises it to 1 at grazing angles), F0 0.02 (water, IOR 1.33), gloss of the reflection.
# The tile UV transforms are the pre-pass b9a5ef0f literals at time 0.
WATER_PRESENTATION = dict(opacity=0.8, f0=0.02, gloss=0.9, normal_strength=0.5,
                          uv_a=(0.095, 0.105, 0.248, 0.613), uv_b=(0.105, 0.095, 0.0, 0.0))
# Texture slot tables: 16-byte rows (path string offset u32, flags u32, slot hash u64), stored twice.
GRAPH_SLOT_SECTION, MATERIAL_SLOT_SECTION = 0x1CAFE804, 0xB967FF7A
# Blend/cull per material: .material section 0xE1275683, u32 at byte 16 (rc2016.pipeline.render_state).
MATERIAL_FLAGS_SECTION = 0xE1275683
# Fur-shell render-state bit (flags & 0x80000): the engine draws extra alpha-over passes that are not modelled.
FUR_SHELL_FLAG = 0x80000
# Fur-shell materials with no material graph in the file, read as 'grey' like their sibling skin material
# (Skin_Only, same _c/_g/_n textures). An inference from the shared textures, not a decoded rule. Without
# it the body reads coloured F0 = _g.rgb, which turns the grey-layout _g (B about 1) into a blue specular.
# Lower-case archive paths; the fur-shell bit must also be set.
FUR_SHELL_GREY_READING = frozenset({'material/character/npc/npc_big_al_tools/npc_big_al_skin_and_fur.material'})
BLEND_MODES = {0: 'opaque', 1: 'additive', 2: 'alpha_over'}   # flags & 3; mode 3 has no captured example


def _sections(data):
    """{tag: (offset, size)} of a DAT1 blob; empty when it has no readable section table."""
    try:
        count = struct.unpack_from('<H', data, 12)[0]
        rows = [struct.unpack_from('<III', data, 16 + i * 12) for i in range(count)]
    except struct.error:
        return {}
    return {tag: (offset, size) for tag, offset, size in rows if offset + size <= len(data)}


def texture_slots(data, tag):
    """{slot hash: texture path} from a graph (GRAPH_SLOT_SECTION) or material (MATERIAL_SLOT_SECTION)."""
    offset, size = _sections(data).get(tag, (0, 0))
    slots = {}
    for row in range(offset, offset + size - 15, 16):
        name, _flags, slot = struct.unpack_from('<IIQ', data, row)
        end = data.find(b'\0', name)
        if 0 < name < len(data) and end > name:
            slots.setdefault(slot, data[name:end].decode('latin-1').replace('\\', '/').lower())
    return slots


def material_flags(data):
    """Render-state flags word of a decoded .material, or None."""
    offset, size = _sections(data).get(MATERIAL_FLAGS_SECTION, (0, 0))
    return struct.unpack_from('<I', data, offset + 16)[0] if size >= 20 else None


def blend_state(flags):
    """{'blend': 'opaque'/'additive'/'alpha_over'/None, 'two_sided': bool} from a flags word."""
    if flags is None:
        return dict(blend=None, two_sided=False)
    return dict(blend=BLEND_MODES.get(flags & 3), two_sided=bool(flags & 4))


def fur_shell_grey_reading(material_path, graphs, data):
    """True for a listed fur-shell material that names no graph and has the fur-shell flag bit."""
    if graphs or not material_path or material_path.lower() not in FUR_SHELL_GREY_READING:
        return False
    flags = material_flags(data)
    return flags is not None and bool(flags & FUR_SHELL_FLAG)


def _graph_rule(graphs):
    return next((rule for prefix, rule in GRAPH_RULES for g in graphs if g.startswith(prefix)), {})


def _role(path):
    stem = Path(path).stem.lower()
    if stem.endswith(PICK_SUFFIX):
        return '_pick'
    return next((r for r in ('_c', '_g', '_n', '_h') if stem.endswith(r)), None)


# Guards every cache below: per-archive memo dicts and the caller-owned texture caches of
# material_textures (the level viewer resolves materials from a thread pool).
_CACHE_LOCK = threading.RLock()


def _archive_cache(archive, name):
    """A dict kept on the archive object under ``name`` (a throwaway dict when it takes no attributes)."""
    cache = getattr(archive, name, None)
    if cache is None:
        with _CACHE_LOCK:
            cache = getattr(archive, name, None)
            if cache is None:
                cache = {}
                try:
                    setattr(archive, name, cache)
                except AttributeError:
                    pass   # objects without attributes just skip caching
    return cache


def _remember(cache, key, value):
    """cache.setdefault under the lock: the first stored value wins for every thread."""
    with _CACHE_LOCK:
        return cache.setdefault(key, value)


def _graph_info(archive, graph_id):
    """(texture paths, slots, parameter keys, defaults, verified skin roles) of one graph.

    Many materials share a graph (a level loads hundreds of materials over a few dozen graphs),
    so the parse is kept on the archive object, per entry id. Callers must not mutate the result.
    """
    cache = _archive_cache(archive, '_graph_info_cache')
    found = cache.get(graph_id)
    if found is None:
        graph_data = asset_data(archive, graph_id)
        textures = [r['path'] for r in asset_references(archive, graph_id) if r['path'].endswith('.texture')]
        slots = texture_slots(graph_data, GRAPH_SLOT_SECTION)
        keys, defaults = frozenset(), {}
        try:
            from rc2016.shaders.families import graph_parameter_tables
            tables, defaults = graph_parameter_tables(graph_data)
            keys = frozenset(row['key_hash'] for table in tables for row in table)
        except (ValueError, struct.error):
            defaults = {}
        # Reuse the pipeline's path-and-hash verified skin slot roles. Stale
        # swarmer bindings and the secondary normal must not win by suffix.
        import hashlib
        from rc2016.shaders.families import FAMILY_REGISTRY
        graph_path = archive.entries[graph_id]['path'].replace('\\', '/').lower()
        spec = FAMILY_REGISTRY.get((graph_path, hashlib.sha256(graph_data).hexdigest()), {})
        role_slots = {}
        if spec.get('family') in ('basic_skin', 'skin_emissive'):
            roles = {'base_color': '_c', 'colored_specular_gloss': '_g', 'normal': '_n'}
            for key, role in spec['texture_slots'].items():
                if role in roles:
                    matches = [slot for slot in slots if slot & 0xFFFFFFFF == int(key, 16)]
                    if len(matches) == 1:
                        role_slots[roles[role]] = matches[0]
        found = _remember(cache, graph_id, (textures, slots, keys, defaults, role_slots))
    return found


def _foliage_alpha_cutoff(data):
    """Decoded static-mesh baseline; malformed/missing state uses a labelled preview fallback."""
    offset, size = _sections(data).get(MATERIAL_FLAGS_SECTION, (0, 0))
    if size < 22:
        return FOLIAGE_ALPHA_CUTOFF_FALLBACK
    flags = struct.unpack_from('<I', data, offset + 16)[0]
    base, reference = data[offset + 20:offset + 22]
    # Material loader 0x1841c80 floors byte21 only for blended state without bit 0x80.
    if flags & 3 and not flags & 0x80:
        reference = max(reference, 4)
    # Match scalar CPU float32 multiplication, including its rounding at alpha boundaries.
    scale = struct.unpack('<f', struct.pack('<f', 1. / 255.))[0]
    threshold = struct.unpack('<f', struct.pack('<f', (base + reference) * scale))[0]
    return min(threshold, 1.)


def material_texture_paths(archive, material_path: str) -> dict:
    """{'_c'|'_g'|'_n': texture path, 'k': float} for one material.

    Textures the material references directly win; the material graph's own
    defaults fill roles the material leaves out.
    """

    material_ids = [k for k in archive.resolve(material_path) if archive.entries[k]['type'] == 'material']
    if len(material_ids) != 1:
        return {}
    data = asset_data(archive, material_ids[0])
    found = [r['path'] for r in asset_references(archive, material_ids[0]) if not r.get('resolution_error')]
    textures = [p for p in found if p.endswith('.texture')]
    graphs = [p for p in found if p.endswith('.materialgraph')]
    graph_keys, defaults, slots, role_slots = set(), {}, {}, {}
    for graph in graphs:
        ids = [k for k in archive.resolve(graph) if archive.entries[k]['type'] == 'materialgraph']
        if len(ids) == 1:
            graph_textures, graph_slots, keys, graph_defaults, graph_roles = _graph_info(archive, ids[0])
            textures += graph_textures
            slots.update(graph_slots)
            graph_keys |= keys
            defaults.update(graph_defaults)
            role_slots.update(graph_roles)
    result = {}
    for path in dict.fromkeys(textures):
        role = _role(path)
        if role and role not in result:
            result[role] = path
    if role_slots:
        instance_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in role_slots.items():
            result.pop(role, None)
            path = instance_slots.get(slot) or slots.get(slot)
            if path:
                result[role] = path
    rows = {}
    try:
        from rc2016.formats.materials import extract_material_parameters
        parameters = extract_material_parameters(data)
        rows = {row.get('key_hash'): row for row in parameters.get('parameters', []) + parameters.get('unsupported_parameters', [])}
    except (ValueError, struct.error):
        pass

    def value(key, fallback):
        """Material instance value, else the graph default, else ``fallback``."""
        row = rows.get(key)
        if row and row.get('size', 0) >= 4:
            try:
                return struct.unpack('<f', bytes.fromhex(row['raw_hex'])[:4])[0]
            except (ValueError, struct.error):
                pass
        default = defaults.get(key, fallback)
        return float(default[0] if isinstance(default, (list, tuple)) else default)

    def vec3(key, fallback):
        """Three-float instance value, else the graph default, else ``fallback``."""
        row = rows.get(key)
        if row and row.get('size', 0) >= 12:
            try:
                return list(struct.unpack('<3f', bytes.fromhex(row['raw_hex'])[:12]))
            except (ValueError, struct.error):
                pass
        default = defaults.get(key, fallback)
        return [float(x) for x in default] if isinstance(default, (list, tuple)) and len(default) == 3 else list(fallback)

    rule = _graph_rule(graphs)
    result['g_mode'] = rule.get('g_mode', 'colored')
    if fur_shell_grey_reading(material_path, graphs, data):
        result['g_mode'] = 'grey'
        result['g_mode_source'] = 'fur_shell_sibling_inferred'
    if 'g_switch' in rule:
        result['g_mode'] = 'colored' if value(rule['g_switch'], 0.0) >= 0.5 else 'grey'
    result['self_lit'] = rule.get('self_lit', 'c_alpha')
    if 'za_fresnel_key' in rule:
        result['za_fresnel_power'] = value(rule['za_fresnel_key'], 8.0)
    if 'self_lit_key' in rule:
        key = rule['self_lit_key']
    elif graph_keys:   # the graph's own key
        key = next((k for k in SELF_LIT_K_KEYS if k in graph_keys), None)
    else:              # no readable graph: the first key the material stores
        key = next((k for k in SELF_LIT_K_KEYS if k in rows), None)
    result['k'] = value(key, 0.0) if key is not None else 0.0
    result['spec'] = value(SPECULAR_SCALE_KEY, 1.0)
    result['gloss'] = value(GLOSS_SCALE_KEY, 1.0)
    if rule.get('two_color_metal'):
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        # Source pass 0: api5 C, api6 N, api7 G; pass 1 omits N. Never choose by suffix.
        for role, slot in (('_c', 0x49A7866BABDAD780), ('_g', 0x49A7866B1576D848), ('_n', 0x49A7866B7AE34E7A)):
            path = material_slots.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result['two_color_metal'] = dict(
            diffuse_a=vec3(0xD3E7B61B, (.1778884, 1., .8468732)),
            diffuse_b=vec3(0xA660CD56, (.3915725, .09530747, 0.)),
            spec_a=vec3(0x72D80ADC, (0., 1., .05780543)),
            spec_b=vec3(0xE00CD247, (1., 0., 1.)),
            power=value(0x4A4437A7, 1.), gloss_power=value(0x010CB8D3, 4.),
            texture_scale=value(0x2F076BCD, 6.), normal_strength=value(0xDCE99DE5, 1.),
            writer='1012E99D', scope='source_material_terms_with_viewer_lighting',
            runtime=dict(lighting_flags=96, ssr_weight=0., fog_fade=1., debug=0,
                         instance_opacity=1., alpha_cutoff=0., normal_fade=2., geometric_aa=1.))
    if rule.get('foliage'):
        # Actual C/G/N slot IDs; six material G paths do not have a _g suffix.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in (('_c', 0x49A7866BABDAD780), ('_g', 0x49A7866B1576D848), ('_n', 0x49A7866B7AE34E7A)):
            path = material_slots.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result['foliage'] = True
    if rule.get('alpha_cut'):
        result['alpha_cut'] = _foliage_alpha_cutoff(data)
    if rule.get('hair'):
        # Serialized graph slots, never suffixes: the material also stores stale C/G/N keys of another graph
        # (the plumber beard binds Shared_Hair_Plumber_c only under one), and its _g key names Shared_Hair_02_g.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in HAIR_SLOTS.items():
            path = material_slots.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result.pop('_h', None)
        result.update(k=0., spec=1., gloss=1., self_lit='none')
        sigma = lambda g: 2.0 ** (-5.5 * (min(max(g, 0.0), 1.0) ** 0.5))
        rough_a, rough_b = value(HAIR_KEYS['rough_a'], HAIR_DEFAULTS['rough_a']), value(HAIR_KEYS['rough_b'], HAIR_DEFAULTS['rough_b'])
        result['hair'] = dict(albedo=vec3(HAIR_KEYS['albedo'], HAIR_DEFAULTS['albedo']), spec=vec3(HAIR_KEYS['spec'], HAIR_DEFAULTS['spec']),
            rim_exponent=value(HAIR_KEYS['rim'], HAIR_DEFAULTS['rim']), gloss_scale=HAIR_GLOSS_SCALE,
            normal_strength=HAIR_NORMAL_STRENGTH, rough_a=rough_a, rough_b=rough_b,
            diffuse_sigma=0.4 * max(sigma(rough_a), sigma(rough_b)) + 0.6 * min(sigma(rough_a), sigma(rough_b)),
            writer='94CE8CCE', keys={k: v for k, v in HAIR_KEYS.items()})
    if rule.get('leaf_colour'):
        # cma (Fresnel-mixed R) and cmb (palette ramp) by slot hash: material slot, else the graph's.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in (('_cma', LEAF_CMA_SLOT), ('_cmb', LEAF_CMB_SLOT)):
            path = material_slots.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        global_leaf = rule['leaf_colour'] == 'global'
        if global_leaf:
            # CDD7CD94 pass 0: API5 C, API6 N; literal zero MRT2 (F0/gloss), zero self-lit.
            for role, slot in (('_c',0x49A7866BABDAD780),('_n',LEAF_GLOBAL_NORMAL_SLOT)):
                path = material_slots.get(slot) or slots.get(slot)
                if path: result[role] = path
                else: result.pop(role,None)
            result.pop('_g',None); result.update(g_mode='grey',k=0.0,spec=1.0,gloss=1.0)
        if '_cma' in result and '_cmb' in result:
            # Global U = .5 + .5*sin(record112*record120), V = float32(.65 + .2*U).
            # U remains the same frozen preview input; global V/S are writer literals, not material keys.
            palette_y = struct.unpack('<f',struct.pack('<f',.6499999761581421+.20000000298023224*LEAF_PALETTE_U_PLACEHOLDER))[0] if global_leaf else value(LEAF_PALETTE_Y_KEY,.5)
            result['leaf_colour'] = dict(exponent=value(LEAF_EXPONENT_KEY,1.0),scale=.5 if global_leaf else value(LEAF_SCALE_KEY,1.0),
                palette_uv=(LEAF_PALETTE_U_PLACEHOLDER,palette_y),writer='CDD7CD94' if global_leaf else '0476D929',
                keys=dict(exponent=LEAF_EXPONENT_KEY) if global_leaf else dict(exponent=LEAF_EXPONENT_KEY,scale=LEAF_SCALE_KEY,palette_y=LEAF_PALETTE_Y_KEY),
                mapped_fresnel=global_leaf,placeholder_u=True)
    if rule.get('fresnel_tint'):
        # Graph writer reads only _c and _g (API7 and API5); the Fresnel tint needs no extra texture.
        fallback = MUSHROOM_FRESNEL_PRESENTATION
        result['fresnel_tint'] = dict(colour=vec3(MUSHROOM_FRESNEL_COLOUR_KEY, fallback['colour']),
            exponent=value(MUSHROOM_FRESNEL_EXPONENT_KEY, fallback['exponent']), writer='F69ADC40',
            keys=dict(colour=MUSHROOM_FRESNEL_COLOUR_KEY, exponent=MUSHROOM_FRESNEL_EXPONENT_KEY, self_lit=MUSHROOM_SELF_LIT_KEY))
    if rule.get('parallax_outputs'):
        result['parallax_normal'] = True
        # api5 is a separate height texture, sometimes also named _c; use C/G/N slot identities.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in (('_c', 0x49A7866BABDAD780), ('_g', 0x49A7866B1576D848), ('_n', 0x49A7866B7AE34E7A)):
            path = material_slots.get(slot) or slots.get(slot)
            if path:
                result[role] = path
    if rule.get('parallax'):
        height_slot = 0x49A7866BB521DFCB
        height_path = texture_slots(data, MATERIAL_SLOT_SECTION).get(height_slot) or slots.get(height_slot)
        if height_path: result['_parallax_height'] = height_path
        tint_pom = rule['parallax'] == 'tint'
        result['parallax_normal'] = True
        result['parallax'] = dict(kind=2 if tint_pom else 1,
            height_scale=value(0xEE0F9FF8, 1.0) if tint_pom else value(0x47F581F6, 0.1),
            steps_front=16.0 if tint_pom else value(0xD709BC41, 16.0),
            steps_grazing=64.0 if tint_pom else value(0xD60526F5, 64.0),
            fade=1.0, shadows=True, writer='4E4D7894' if tint_pom else '5FADAD33',
            runtime='viewer derivative TBN, fade=1, CB0[95]=1, viewer key light; singular rays skip POM')
    if rule.get('bng'):
        kind = rule['bng']
        # Bind by serialized slot identity: suffixes do not identify normal/detail or mo_black_h.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        bindings = {'_c': 0x49A7866BABDAD780, '_g': 0x49A7866B1576D848, '_n': 0x49A7866B7AE34E7A}
        if kind in (2, 3, 6):
            bindings['_bng_detail'] = 0x49A7866B45A52060
        if kind == 9:   # _m mask: R gates the rim, G is the self-lit mask
            bindings['_h'] = 0x49A7866BA6C3B1D5
        for role, slot in bindings.items():
            path = material_slots.get(slot) or slots.get(slot)
            if path:
                result[role] = path
        colour = vec3(0xD21B698F, (1.0, 1.0, 1.0))
        params = (0.0, 0.0, 0.0, 0.0)
        detail = (1.0, 1.0, 0.0)
        if kind == 2:  # F3DCD431 / C24F9FA7 pulse is 1 at t=0, even with a nonzero rate.
            result['k'] *= value(0x5FD56522, 10.0) * colour[0]
        if kind == 3:
            params = (value(0xAAC47D17, 1.0), value(0xB7BAC19F, 1.0), 0.0, 0.0)
        if kind == 4:
            result['spec_tint'] = vec3(0x43C53ECA, (1.0, 1.0, 1.0))
        if kind == 5:
            result['gloss'] = value(0x9856BA09, 1.0)
        if kind == 6:
            params = (value(0x9ACE7120, 3.0), value(0x07CC3BD9, 1.0), value(0xD94E1303, 10.0), 0.0)
        if kind in (7, 8, 9):
            params = (value(0xAAC47D17, 1.0), value(0xB7BAC19F, 1.0), 0.0, 0.0)
        if kind in (2, 3):
            detail = (value(0x34493800, 1.0), value(0xAD4069BA, 1.0), value(0x49AFC3A0, 0.75))
        if kind == 6:
            detail = (value(0x34493800, 1.0), value(0xAD4069BA, 1.0), value(0x49AFC3A0, 0.75))
        result['bng'] = dict(kind=kind, colour=colour, params=params, detail=detail)
    if rule.get('pick') and result.get('_pick'):
        scale, blend, u = rule['pick']
        result['pick'] = dict(scale=value(scale, 0.0), blend=value(blend, 0.0), u=value(u, 0.0))
    else:
        result.pop('_pick', None)
    if rule.get('tint'):
        k = TINT_KEYS
        result['spec_tint'] = vec3(k['spec_tint'], (1.0, 1.0, 1.0))
        result['tint'] = dict(a=vec3(k['colour_a'], (0.0, 0.0, 0.0)), b=vec3(k['colour_b'], (0.0, 0.0, 0.0)),
                              power_a=value(k['power_a'], 1.0), power_b=value(k['power_b'], 1.0),
                              weight_a=value(k['weight_a'], 0.0), weight_b=value(k['weight_b'], 0.0),
                              mask_b=value(k['mask_b'], 0.0))
    elif rule.get('bng') != 9:   # the 14B1CC68 rim/self-lit mask uses the _h role
        result.pop('_h', None)
    if rule.get('tint_variant'):
        # Exact source slots: default detail-colour and palette names are ambiguous.
        kind = rule['tint_variant']
        wanted = {'_c': 0x49A7866BABDAD780, '_g': 0x49A7866B1576D848,
                  '_n': 0x49A7866B7AE34E7A, '_h': 0x49A7866B93BB7340}
        if kind == 'detail': wanted['_tint_detail'] = 0x49A7866B4A362E9B
        if kind in ('world', 'world_normal'): wanted['_tint_detail'] = 0x49A7866B0056CBD0
        if kind == 'world_normal': wanted['_pick'] = 0x49A7866B67D6383E
        if kind != 'detail': wanted['_tint_palette'] = 0x628AD5A840ED6C83
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in wanted.items():
            path = material_slots.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result['spec_tint'] = [1.0, 1.0, 1.0]   # not read by these writers
        t = result['tint']; t['variant'] = kind
        if kind == 'detail': t['mask_b'] = 1.0   # this graph has no mask-strength key
        t['detail_scale'] = value(0x93F9F959 if kind == 'detail' else 0xAC34BF6E, 1.0)
        t['detail_strength'] = (1.0 if rule.get('parallax') == 'tint' else
                                value(0x3BBC1444 if kind == 'detail' else 0xB23D4CB3, 1.0))
        # abs(attr3.w) is an instance-buffer product; freeze magnitude .5, preserve material override.
        t['instance_palette'] = 0.5
        override = value(0x26DCC4CE, 0.0)
        t['palette_u'] = 0.5 * (1.0 - override) + value(0xCD0DCE36, 1.0) * override
        t['palette_blend'] = value(0x5016F9F7, 1.0) if kind != 'detail' else 0.0
        t['palette_rgb'] = [1.0, 1.0, 1.0]
        t['world_origin'] = [0.0, 0.0, 0.0]   # archive model browser has no level-instance placement
    if rule.get('arch_sequence'):
        # This writer has no _c or normal sample. Resolve its three scalar inputs by source slot,
        # never by the shared _h suffix. _pick reuses unit 4 for the extra scalar mask.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in (('_g', 0x49A7866B1576D848), ('_h', 0x49A7866BC7FB024E), ('_sequence', 0x628AD5A80A2038A8), ('_pick', 0x49A7866BE0B8D722)):
            path = material_slots.get(slot) or slots.get(slot)
            if path:
                result[role] = path
        result['spec'], result['gloss'] = 1.0, 5.0
        result['arch_sequence'] = dict(colour=vec3(0x7F058428, (1.0, 1.0, 1.0)),
            intensity=value(0x77AF2D2B, 1.0), linear=value(0x8A43287F, 1.0),
            phase=value(0x60DE8181, 0.0), mask_bias=value(0x33BD9BA6, 0.1), power=value(0x23FEED4B, 1.0),
            rate=value(0xD7B41D33, 1.0), attr1_enabled=value(0x7F87B733, 0.0) >= 1.0,
            attr3_enabled=value(0x1418C201, 0.0) >= 1.0, time=0.0, attr1=0.0, attr3=0.0,
            writer='AAED778C')
    if rule.get('vehicle'):
        # CB table 1 and its own slot table: _g, _n and both flakes are absent from EB1AC2CD.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in (('_c', 0x49A7866BABDAD780), ('_pick', 0x628AD5A8603FF271),
                           ('_vehicle_p2', 0x628AD5A8AB25DA7A), ('_h', 0x49A7866B0005F1E9)):
            path = material_slots.get(slot) or slots.get(slot)
            if path:
                result[role] = path
        result.pop('_g', None); result.pop('_n', None)
        result['vehicle'] = dict(a=vec3(0x4F1684D2, (1.0, 1.0, 1.0)), b=vec3(0xB5D46F28, (1.0, 1.0, 1.0)),
            blend=value(0xF0A64F9B, 1.0), rim=value(0x148C2635, 1.0), palette_coordinate='UV0.x', writer='EB1AC2CD')
    if rule.get('glow'):
        # Writer descriptors and source table keys, not texture-name guesses.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        wanted = {'_c': 0x49A7866BABDAD780, '_g': 0x49A7866B1576D848,
                  '_n': 0x49A7866B7AE34E7A, '_h': 0x49A7866B0005F1E9}
        if rule['glow'] == 1:
            wanted['_glow_parallax'] = 0x49A7866BE0B8D722
        for role, slot in wanted.items():
            path = material_slots.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result['glow'] = dict(kind=rule['glow'], colour=vec3(0x1AAD6A99, (0.0, 0.0, 0.0)),
            depth=value(0x5DB5EFC7, 0.04), bias=value(0x33BD9BA6, 0.1),
            environment_scale=value(0x8A43287F, 1.0), vertex_fade=value(0x9E0BA553, 0.0),
            vertex_magnitude=1.0, writer='2685144F' if rule['glow'] == 1 else 'CA81B14F')
    if rule.get('puddles'):
        # Source pass 0/table 0 DB75867B; serialized slots, never name suffixes.
        instance = texture_slots(data, MATERIAL_SLOT_SECTION)
        wanted = {'_c': 0x49A7866BABDAD780, '_g': 0x49A7866B1576D848,
                  '_n': 0x49A7866B7AE34E7A, '_puddle_noise': 0x49A7866B0056CBD0,
                  '_puddle_ripple': 0x49A7866B75FFE911}
        for role, slot in wanted.items():
            path = instance.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result.pop('_h', None); result.pop('_pick', None)
        keys = (0x72A7B503,0x0B545512,0x36E3884B,0x072BC1D7,0x6F0C6E9C,
                0xE8348E60,0xFD911BCC,0xBA216A14,0x6BA9C99C,0x79583FDA,
                0x220C9B5C,0x4295546E,0xDA56E3B0,0x42B6B6AE,0xCEF83FBC)
        fallback = (.566,.02,.433,.788,1.5,.015,.477,180.,1.,0.,.02,.9,.4,.1,0.)
        p = [value(k,d) for k,d in zip(keys,fallback)]
        import math
        if not all(math.isfinite(x) for x in p) or p[6] == p[0] or p[3] == p[2] or p[6] == 1:
            raise ValueError('Undefined GroundPuddles threshold/parameter')
        result.update(k=0., spec=1., gloss=1., self_lit='none')
        result['puddles'] = dict(params=p, writer='DB75867B', time=0., time_input='CB0 dword 99',
            frozen_inputs=dict(origin=(0.,0.,0.), instance_axis_lengths=(1.,1.), fade=0.,
                normal_scale=2., geometric_aa=1., opacity=1., fog=1., ssr=False, debug=0,
                light_flags=96, light_multiplier=(1.,1.,1.), probe_opacity=0.,
                diffuse_specular_environment='viewer rig'), runtime_pass_verified=False)
    if rule.get('rock_blend'):
        # 886AAC94 resource table: mask, gA/gB, nA/nB, cA/cB, in bounded source table order.
        # A3DAA047 (color_breakup): mask, gA/gB, nA/nB and one breakup colour (pass contract api 5-10).
        breakup = rule['rock_blend'] == 'color_breakup'
        instance = texture_slots(data, MATERIAL_SLOT_SECTION)
        wanted = {'_rock_mask': 0x49A7866B3404C763, '_g': 0x49A7866B7BF812C7,
                  '_rock_g2': 0x49A7866B42752E02, '_n': 0x49A7866B146D84F5,
                  '_rock_n2': 0x49A7866B2DE0B830, '_c': 0x49A7866B969AFD6A,
                  '_rock_c2': 0x49A7866BAF17C1AF}
        if breakup:
            # The graph does not declare the layer-colour slots; a stale instance binding is never read.
            del wanted['_rock_c2']; wanted['_c'] = 0x49A7866BF9937BC9
            result.pop('_rock_c2', None)
        for role, slot in wanted.items():
            path = instance.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result['spec'] = value(0xFEAF9A36, 1.0)
        if breakup:
            # CB2 table 0: P dw0, Q dw1, T dw2 (world tile), K dw3, U dw4 (layer UV scale), S dw5.
            result['rock_blend'] = dict(variant='color_breakup', world_tile=value(0xFD70EF49, 10.0),
                layer_uv_scale=value(0xE8D31733, 1.0), mask_strength=value(0x21C2A812, 1.0),
                sharpness=value(0xF2634949, 1.0), world_axes='native x, z', writer='A3DAA047')
        else:
            result['rock_blend'] = dict(tint=vec3(0x1AAD6A99, (1.0, 1.0, 1.0)),
                exponent=value(0x965B2DCD, 1.0), tint_amount=value(0xF0A64F9B, 0.0),
                mask_strength=value(0x21C2A812, 1.0), sharpness=value(0xF2634949, 1.0), writer='886AAC94')
    if rule.get('screen'):
        # 8716DC7A: timeline api5 is sampled once at t=0; api6 reads alpha only.
        # Existing forward units 0/1/2/4 carry api6/api9/api7/api8. No new unit.
        instance = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in (('_screen_timeline', 0x628AD5A840ED6C83), ('_c', 0x49A7866B0056CBD0),
                           ('_n', 0x49A7866B7A530738), ('_pick', 0x49A7866B4AC18E9F), ('_g', 0x49A7866B5DBA9ADC)):
            path = instance.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result.pop('_h', None)
        result['forward'] = dict(kind='screen', params=dict(
            colour_a=vec3(0x4E14D6BE, (1.0, 0.822785735, 0.407240212)),
            colour_b=vec3(0x54CC2EDE, (0.254152089, 1.0, 0.356400132)),
            alpha_base=value(0x5AEFB7E9, 0.15), mask_strength=value(0x33648910, 1.0),
            grid_max=value(0x901F1F8A, 1024.0), grid_divisor=value(0xD5CE3EFE, 1.0),
            intensity_b=value(0x1F4CDDD9, 0.5), intensity_a=value(0xCD131891, 1.0)),
            time=0.0, time_input='CB0 dword 99', rate=value(0xD7B41D33, 0.1), writer='8716DC7A',
            frozen_inputs=dict(cutoff=0.0, alpha=1.0, fog_fade=1.0), **blend_state(material_flags(data)))
    if rule.get('forward'):
        # Roles the writer binds by slot hash: the material's texture in that slot, else the graph's.
        material_slots = texture_slots(data, MATERIAL_SLOT_SECTION)
        for role, slot in rule.get('slots', {}).items():
            path = material_slots.get(slot) or slots.get(slot)
            if path:
                result[role] = path
        params = {name: vec3(key, (0.0, 0.0, 0.0)) if size == 3 else value(key, 0.0) for name, key, size in rule['params']}
        params.update(rule.get('constants', {}))  # graph constants, not read from the material
        if rule['forward'] == 'water':
            params.update(WATER_PRESENTATION)
        if rule['forward'] == 'bright_glow':
            # t = |1.001 - N.V|^e with e = 0.2 (1 - S); w = sat(1 + (1 - B)(0.5 sin - 0.5)) at sin = 0.
            params['exponent'] = 0.2 * (1.0 - params['sharp'])
            params['weight'] = min(1.0, max(0.0, 1.0 - 0.5 * (1.0 - params['steady'])))
        result['forward'] = dict(kind=rule['forward'], params=params, **blend_state(material_flags(data)))
    if rule.get('facing_glow'):
        # No texture, parameter, vertex colour or instance input: albedo is 1.0 and the glow is
        # f = pow(sat(N.V) - bias, exponent), a = a_scale*f + a_offset,
        # rgb += amplitude * (2a(1 + a), g_scale*f*(1 + a), 0). Literals from the instruction words.
        # amplitude = CB0 dword 115 (captured 0.01, near off); 0.5 is the labelled presentation value
        # (x1 in the monitor writer 0x4dd02bef's 2*dw115). Blink timing is not decoded.
        result['facing_glow'] = dict(albedo=[1.0, 1.0, 1.0], bias=0.001, exponent=7.5,
            a_scale=0.30324259, a_offset=0.06301001, g_scale=0.03041702,
            amplitude=0.5, amplitude_input='CB0 dword 115', captured_amplitude=0.01,
            presentation=True, writer='0CD2AAFB')
    if rule.get('bright_center'):
        steady = value(0x01B78BF6, 0.15)
        intensity = value(0x398AD3E2, 5.0)
        result['bright_center'] = dict(
            edge=vec3(0x16B8AB81, (0.0, 0.0, 0.0)),
            centre=[x * intensity for x in vec3(0x5BC2D1F0, (1.0, 0.5271151, 0.462077))],
            exponent=0.2 * (1.0 - value(0x60DC3D2F, 0.9)),
            weight=min(1.0, max(0.0, 1.0 - 0.5 * (1.0 - steady))),
            f0=value(0x08FE9F29, 0.2), gloss_sq=value(0x0B065E94, 0.35), steady=steady, writer='D14A48D7')
        result['spec'] = result['gloss'] = 1.0   # F0 and gloss are the writer's constants
    return result


def material_base_color_path(archive, material_path: str) -> str | None:
    """The `_c` texture a material references directly, else via its graph."""

    return material_texture_paths(archive, material_path).get('_c')


def _read_top_mip(archive, texture_path: str, full: bool) -> dict:
    """Untiled top mip of every array slice.

    `full` takes the streamed HD mip whenever there is one; otherwise the
    built SD mip is used once it is at least MIN_PREVIEW_SIZE. Array textures
    (38 in the corpus, all built-only) store their slices back to back.
    """

    ids = archive.resolve(texture_path)
    built = [k for k in ids if archive.entries[k]['built_path'].lower().endswith('.built')]
    stream = [k for k in ids if archive.entries[k]['built_path'].lower().endswith('.stream')]
    if len(built) != 1:
        raise ValueError(f'Texture {texture_path} has {len(built)} built assets')
    info = parse_header(asset_data(archive, built[0]))
    _remember(_archive_cache(archive, '_texture_header_cache'), built[0], _header_facts(info))
    fmt = info['format']
    small = max(info['sd_width'], info['sd_height'])
    use_stream = (len(stream) == 1 and info['stream_size'] and info['hd_width'] and info['hd_height']
                  and (full or small < MIN_PREVIEW_SIZE or not info['built_payload']))
    count = 1
    if use_stream:
        packed = archive.bytes(stream[0])[0]
        payload = packed if len(packed) == info['stream_size'] else lz4_block(packed, info['stream_size'])
        width, height = info['hd_width'], info['hd_height']
    elif info['built_payload'] and info['sd_width'] and info['sd_height']:
        payload, width, height = info['built_payload'], info['sd_width'], info['sd_height']
        count = max(1, info['array_size'])
    else:
        raise ValueError(f'Texture {texture_path} has no usable payload')
    slices = ps4_deswizzle_slices(payload, width, height, fmt, count)
    full_size = (info['hd_width'], info['hd_height']) if info['hd_width'] else (info['sd_width'], info['sd_height'])
    return dict(path=texture_path, width=width, height=height, format=fmt, pixels=slices[0], slices=slices,
                array_size=count, full_size=full_size, srgb=fmt in SRGB_FORMATS)


TEXTURE_DISK_CACHE_VERSION = 1
TEXTURE_DISK_CACHE_BYTES = 2 * 1024 ** 3
_TEXTURE_DISK_MAGIC = b'RCTX'
try:   # the decoders' own source: any change to them invalidates every cached texture
    import hashlib as _hashlib
    _TEXTURE_DECODER_SHA256 = _hashlib.sha256(b''.join(
        (Path(__file__).resolve().parents[1] / name).read_bytes()
        for name in ('viewer/textures.py', 'formats/texture.py', 'formats/archive.py', 'formats/container.py'))).hexdigest()
except OSError:   # frozen executable without sources: no disk cache
    _TEXTURE_DECODER_SHA256 = None


class TextureDiskCache:
    """Decoded top mips (load_texture results without GL fields) under ``folder``.

    Keyed by the archive metadata hashes, the texture's archive occurrences and the decoder
    source hash. Each file is ``RCTX`` + u32 header length + JSON header (with the payload
    SHA-256) + slice bytes, written to a unique temporary file and renamed into place. A file
    that fails any check is ignored and rewritten. Least recently used files are removed once
    the folder exceeds ``limit`` bytes.
    """

    SUFFIX = '.rctx'

    def __init__(self, folder, limit=TEXTURE_DISK_CACHE_BYTES):
        self.folder, self.limit = Path(folder), limit
        self._lock = threading.Lock()
        self._total = None

    def key(self, archive, texture_path):
        metadata = getattr(archive, 'metadata', None)
        if not metadata or _TEXTURE_DECODER_SHA256 is None:
            return None
        rows = []
        for k in sorted(archive.resolve(texture_path)):
            e = archive.entries[k]
            if 'loose_path' in e:
                return None
            rows.append((k, e['built_path'], [(o['archive'], o['offset'], o['size']) for o in e['occurrences'] if o['available']][:1]))
        import hashlib
        return hashlib.sha256(repr((TEXTURE_DISK_CACHE_VERSION, _TEXTURE_DECODER_SHA256, sorted(metadata.items()),
                                    texture_path, rows)).encode()).hexdigest()

    def _file(self, key):
        return self.folder / key[:2] / f'{key}{self.SUFFIX}'

    def get(self, key):
        import hashlib, json, os
        path = self._file(key)
        try:
            with open(path, 'rb') as f:
                blob = f.read()
            if blob[:4] != _TEXTURE_DISK_MAGIC:
                return None
            size = struct.unpack_from('<I', blob, 4)[0]
            header = json.loads(blob[8:8 + size].decode('utf-8'))
            payload = blob[8 + size:]
            if header.get('key') != key or hashlib.sha256(payload).hexdigest() != header.get('payload_sha256') \
                    or sum(header['slice_sizes']) != len(payload) or not header['slice_sizes']:
                return None
            slices, at = [], 0
            for n in header['slice_sizes']:
                slices.append(payload[at:at + n]); at += n
            texture = header['texture']
            texture.update(full_size=tuple(texture['full_size']), pixels=slices[0], slices=slices)
            os.utime(path)   # recency for eviction
        except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error):
            return None
        return texture

    def put(self, key, texture):
        import hashlib, json, os, uuid
        slices = texture['slices']
        payload = b''.join(slices)
        fields = {k: texture[k] for k in ('path', 'width', 'height', 'format', 'array_size', 'full_size', 'srgb')}
        header = json.dumps(dict(key=key, texture=fields, slice_sizes=[len(s) for s in slices],
                                 payload_sha256=hashlib.sha256(payload).hexdigest())).encode('utf-8')
        path = self._file(key)
        temp = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp, 'wb') as f:
                f.write(_TEXTURE_DISK_MAGIC + struct.pack('<I', len(header)) + header + payload)
            os.replace(temp, path)
        except OSError:
            try: temp.unlink()
            except OSError: pass
            return
        self._grow(8 + len(header) + len(payload))

    def _grow(self, added):
        with self._lock:
            if self._total is None:
                self._total = sum(size for _p, size, _t in self._scan())
            else:
                self._total += added
            if self._total <= self.limit:
                return
            files = sorted(self._scan(), key=lambda row: row[2])
            self._total = sum(size for _p, size, _t in files)
            for path, size, _t in files:
                if self._total <= self.limit * 0.9:
                    break
                try:
                    path.unlink(); self._total -= size
                except OSError:
                    pass

    def _scan(self):
        rows = []
        try:
            for sub in self.folder.iterdir():
                if sub.is_dir():
                    for path in sub.glob('*' + self.SUFFIX):
                        try:
                            st = path.stat(); rows.append((path, st.st_size, st.st_mtime_ns))
                        except OSError:
                            pass
        except OSError:
            pass
        return rows


def load_texture(archive, texture_path: str) -> dict:
    """Top mip of one game texture as GPU-ready blocks plus its GL format.

    With a TextureDiskCache on ``archive.texture_disk_cache`` the decoded top mip is
    reused from disk across sessions.
    """

    disk = getattr(archive, 'texture_disk_cache', None)
    key = disk.key(archive, texture_path) if disk is not None else None
    texture = disk.get(key) if key else None
    if texture is None:
        texture = _read_top_mip(archive, texture_path, full=False)
        if key and texture['format'] in GL_COMPRESSED.keys() | GL_UNCOMPRESSED.keys():
            disk.put(key, texture)
    fmt = texture['format']
    if fmt not in GL_COMPRESSED and fmt not in GL_UNCOMPRESSED:
        raise ValueError(f'Texture format {fmt} is not previewable')
    texture.update(gl_format=GL_COMPRESSED.get(fmt), gl_pixel_format=GL_UNCOMPRESSED.get(fmt))
    return texture


def model_texture_list(archive, materials: list[dict]) -> list[dict]:
    """Every `.texture` the model's materials reference, directly or via their graph.

    One row per texture path: {'path', 'suffix', 'materials': [material names],
    'via_graph': True when only a material graph references it}.
    """

    rows = {}
    for material in materials:
        if not material.get('path'):
            continue
        try:
            ids = [k for k in archive.resolve(material['path']) if archive.entries[k]['type'] == 'material']
            if len(ids) != 1:
                continue
            found = [r['path'] for r in asset_references(archive, ids[0]) if not r.get('resolution_error')]
            direct = [p for p in found if p.endswith('.texture')]
            via_graph = []
            for graph in (p for p in found if p.endswith('.materialgraph')):
                gids = [k for k in archive.resolve(graph) if archive.entries[k]['type'] == 'materialgraph']
                if len(gids) == 1:
                    via_graph += [r['path'] for r in asset_references(archive, gids[0])
                                  if r['path'].endswith('.texture')]
        except (ValueError, KeyError, OSError):
            continue
        name = material.get('name') or Path(material['path']).stem
        for path, graph_only in [(p, False) for p in direct] + [(p, True) for p in via_graph]:
            row = rows.setdefault(path, dict(path=path, suffix=_suffix(path), materials=[], via_graph=graph_only))
            row['via_graph'] = row['via_graph'] and graph_only
            if name not in row['materials']:
                row['materials'].append(name)
    return list(rows.values())


def _suffix(path: str) -> str:
    stem = Path(path).stem
    return '_' + stem.rsplit('_', 1)[1] if '_' in stem else ''


def load_texture_full(archive, texture_path: str) -> dict:
    """Like load_texture, but always the largest available top mip (for export)."""

    return _read_top_mip(archive, texture_path, full=True)


HEADER_FACTS = ('format', 'hd_width', 'hd_height', 'sd_width', 'sd_height', 'array_size')


def _header_facts(info):
    return {k: v for k, v in info.items() if k in HEADER_FACTS}


def texture_header(archive, texture_path: str) -> dict:
    """Header facts only (format and sizes), without untiling any pixels.

    Memoized on the archive per built asset, also by _read_top_mip's own parse.
    """

    built = [k for k in archive.resolve(texture_path) if archive.entries[k]['built_path'].lower().endswith('.built')]
    if len(built) != 1:
        raise ValueError(f'Texture {texture_path} has {len(built)} built assets')
    cache = _archive_cache(archive, '_texture_header_cache')
    facts = cache.get(built[0])
    if facts is None:
        facts = _remember(cache, built[0], _header_facts(parse_header(asset_data(archive, built[0]))))
    return dict(facts)


# Pillow lacks the sRGB twins of BC1-BC3; their blocks are identical, only the
# sampling colourspace differs, and to_image never converts colourspace.
PILLOW_FORMAT = {72: 71, 75: 74, 78: 77}


def to_image(texture: dict, index: int = 0):
    """Pillow image of one array slice of a loaded top mip, values exactly as stored.

    No colourspace change. Block-compressed formats are decoded by Pillow's
    DDS reader; RGBA8, R8 and R8G8 are converted directly (R8G8 gets B = 0).
    BC6H is HDR and Pillow returns it clamped to 8-bit.
    """

    from PIL import Image   # optional: only the texture tab and PNG export need it
    import io
    fmt, width, height = texture['format'], texture['width'], texture['height']
    pixels = texture.get('slices', [texture['pixels']])[index]
    if fmt in (28, 29):
        return Image.frombytes('RGBA', (width, height), pixels[:width * height * 4])
    if fmt == 61:
        return Image.frombytes('L', (width, height), pixels[:width * height])
    if fmt == 49:
        rg = pixels[:width * height * 2]
        red = Image.frombytes('L', (width, height), rg[0::2])
        green = Image.frombytes('L', (width, height), rg[1::2])
        return Image.merge('RGB', (red, green, Image.new('L', (width, height), 0)))
    image = Image.open(io.BytesIO(dds_bytes(width, height, PILLOW_FORMAT.get(fmt, fmt), pixels)))
    image.load()
    return image


FORMAT_NAMES = {28: 'RGBA8', 29: 'RGBA8 sRGB', 49: 'R8G8', 61: 'R8', 71: 'BC1', 72: 'BC1 sRGB',
                74: 'BC2', 75: 'BC2 sRGB', 77: 'BC3', 78: 'BC3 sRGB', 80: 'BC4', 83: 'BC5',
                95: 'BC6H', 96: 'BC6H signed', 98: 'BC7', 99: 'BC7 sRGB'}


def export_textures(archive, paths, folder, formats, flip=False, progress=None) -> list[dict]:
    """Write each texture as any of 'texture' (exact game asset), 'dds', 'png'.

    `texture` writes the decompressed built asset and, when present, the
    streamed payload exactly as archived (`.texture.stream`). DDS keeps the
    original block-compressed data of the largest top mip, never flipped; an
    array texture becomes one DX10 array DDS and one PNG per slice.
    `flip` turns PNG output upside down. Returns one result row per texture.
    """

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    used, results = set(), []
    for number, path in enumerate(paths, 1):
        stem = Path(path).stem
        name, n = stem, 2
        while name.lower() in used:
            name, n = f'{stem}__{n}', n + 1
        used.add(name.lower())
        row = dict(path=path, files=[], error=None)
        try:
            if 'texture' in formats:
                ids = archive.resolve(path)
                for key in ids:
                    built_path = archive.entries[key]['built_path'].lower()
                    if built_path.endswith('.built'):
                        target = folder / f'{name}.texture'
                        target.write_bytes(decompress(archive.bytes(key)[0])[0])
                        row['files'].append(str(target))
                    elif built_path.endswith('.stream'):
                        target = folder / f'{name}.texture.stream'
                        target.write_bytes(archive.bytes(key)[0])
                        row['files'].append(str(target))
            if 'dds' in formats or 'png' in formats:
                texture = load_texture_full(archive, path)
                count = texture['array_size']
                if 'dds' in formats:   # arrays become one DX10 texture array
                    target = folder / f'{name}.dds'
                    target.write_bytes(dds_bytes(texture['width'], texture['height'], texture['format'],
                                                 b''.join(texture['slices']), count))
                    row['files'].append(str(target))
                if 'png' in formats:   # arrays become one PNG per slice
                    for index in range(count):
                        image = to_image(texture, index)
                        if flip:
                            from PIL import Image
                            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                        target = folder / (f'{name}.png' if count == 1 else f'{name}_slice{index:02d}.png')
                        image.save(target)
                        row['files'].append(str(target))
        except Exception as error:   # one bad texture must not stop the rest
            row['error'] = f'{type(error).__name__}: {error}'
        results.append(row)
        if progress:
            progress(number, len(paths), path)
    return results


def tint_palette_sample(texture, u):
    """Fixed 1D source palette: linear repeat filtering, sRGB decode before interpolation."""
    import math
    im = to_image(texture).convert('RGB')
    x = float(u) * im.width - 0.5; lo = math.floor(x); f = x - lo
    def pixel(i):
        rgb = [v / 255.0 for v in im.getpixel((i % im.width, 0))]
        if texture.get('srgb'):
            rgb = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return rgb
    a, b = pixel(lo), pixel(lo + 1)
    return [a[i] * (1.0 - f) + b[i] * f for i in range(3)]


def leaf_palette_sample(texture, u, v):
    """Frozen cmb sample: top-mip repeat/bilinear, decode sRGB before filtering.
    This is the preview sampler contract; native descriptor/wrap/mip state is unproven.
    """
    import math
    im = to_image(texture).convert('RGB')
    x, y = float(u)*im.width-.5, float(v)*im.height-.5
    ix, iy = math.floor(x), math.floor(y); fx, fy = x-ix, y-iy
    rgb = [0.0]*3
    for dx, dy, weight in ((0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),(0,1,(1-fx)*fy),(1,1,fx*fy)):
        for i, byte in enumerate(im.getpixel(((ix+dx)%im.width,(iy+dy)%im.height))):
            c = byte/255.0
            if texture.get('srgb'):
                c = c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4
            rgb[i] += weight*c
    return rgb


def rock_texture_volume(textures):
    """Four stored-value RGBA slices on unit 15, tiled without resampling.

    Each source keeps its texel spacing through its own UV scale. Sizes must
    divide the shared volume dimensions; unsupported sizes retain layer A.
    """
    from PIL import Image
    width = max(t['width'] for t in textures); height = max(t['height'] for t in textures)
    if any(width % t['width'] or height % t['height'] for t in textures):
        raise ValueError('RockBlend texture sizes cannot tile without resampling')
    slices = []
    for texture in textures:
        image = to_image(texture).convert('RGBA'); tile = Image.new('RGBA', (width, height))
        for y in range(0, height, image.height):
            for x in range(0, width, image.width): tile.paste(image, (x, y))
        slices.append(tile.tobytes())
    return dict(path='rock-volume:' + '|'.join(t['path'] for t in textures), width=width, height=height,
                pixels=b''.join(slices), scales=[(t['width']/width, t['height']/height) for t in textures],
                source_paths=[t['path'] for t in textures])


def puddle_texture_atlas(noise, ripple):
    """One linear RGBA8 sampler: two native-size tiles with repeat gutters, no resize.

    Sampling fract(uv)*scale+offset exactly retains top-mip bilinear repeat, including seams.
    This is a bounded viewer transport, not a recovered engine sampler or mip policy.
    """
    from PIL import Image
    import hashlib
    if noise.get('srgb') or ripple.get('srgb'):
        raise ValueError('GroundPuddles noise/normal must be linear data')
    images = [to_image(t).convert('RGBA') for t in (noise, ripple)]
    width = sum(i.width + 2 for i in images); height = max(i.height + 2 for i in images)
    out = Image.new('RGBA', (width, height)); scales=[]; x=0
    for im in images:
        w,h=im.size; out.paste(im,(x+1,1))
        out.paste(im.crop((w-1,0,w,h)),(x,1));out.paste(im.crop((0,0,1,h)),(x+w+1,1))
        out.paste(im.crop((0,h-1,w,h)),(x+1,0));out.paste(im.crop((0,0,w,1)),(x+1,h+1))
        for dx,dy,sx,sy in ((0,0,w-1,h-1),(w+1,0,0,h-1),(0,h+1,w-1,0),(w+1,h+1,0,0)):
            out.putpixel((x+dx,dy),im.getpixel((sx,sy)))
        scales.append((w/width,h/height,(x+1)/width,1/height));x+=w+2
    pixels=out.tobytes();digest=hashlib.sha256(pixels).hexdigest()
    return dict(path='GroundPuddlesAtlas:'+digest, width=width,height=height,format=28,
        gl_pixel_format=0x1908,srgb=False,pixels=pixels,scales=scales,
        sources=[dict(path=t['path'],width=t['width'],height=t['height'],format=t['format'],
            pixels_sha256=hashlib.sha256(t['pixels']).hexdigest()) for t in (noise,ripple)])


def _cached_texture(archive, cache: dict, path, tolerate=True):
    """cache[path], loading it once. ``tolerate`` stores None for an unloadable texture;
    otherwise the error propagates and nothing is stored. Loads run outside the lock."""
    with _CACHE_LOCK:
        if path in cache:
            return cache[path]
    try:
        texture = load_texture(archive, path)
    except (ValueError, KeyError, OSError):
        if not tolerate:
            raise
        texture = None
    return _remember(cache, path, texture)


def _with_pixels(archive, cache: dict, path):
    """cache[path] for CPU sampling: reloaded when a caller dropped its 'pixels' (see material_textures)."""
    with _CACHE_LOCK:
        texture = cache.get(path)
    if texture and 'pixels' not in texture:
        texture = load_texture(archive, path)
        with _CACHE_LOCK:
            cache[path] = texture
    return texture


def material_textures(archive, materials: list[dict], cache: dict, translations: dict | None = None,
                      previews: bool = True) -> dict[int, dict]:
    """Material slot -> {'_c'/'_g'/'_n'/'_pick': texture or None, 'k', 'g_mode', 'self_lit', 'spec', 'gloss'[, 'pick']}.

    Missing or unloadable textures are None and that part is shaded without
    them. ``cache`` maps texture path -> loaded texture/None across models.
    A caller may drop 'pixels' and 'slices' from cached textures it has
    uploaded (the level loader does); sources sampled here on the CPU (tint
    palette, screen timeline, puddle and RockBlend layers) are then reloaded.
    ``translations``, when given, receives {material path: selected native translation or None}
    for materials whose texture paths resolved, so material_rules need not select it again.
    ``previews`` prepares the opt-in experimental material previews; their source analysis is
    expensive, so a caller that has not enabled them passes False and prepares them on demand.
    Thread-safe for concurrent calls sharing one ``cache``.
    """

    result = {}
    for material in materials:
        entry = {role: None for role in ROLES}
        resolved = False
        try:
            paths = material_texture_paths(archive, material['path']) if material.get('path') else {}
            resolved = True
        except (ValueError, KeyError, OSError):
            paths = {}
        entry.update(k=paths.get('k', 0.0), g_mode=paths.get('g_mode', 'colored'), self_lit=paths.get('self_lit', 'c_alpha'),
                     spec=paths.get('spec', 1.0), gloss=paths.get('gloss', 1.0))
        for key in ('pick', 'tint', 'spec_tint', 'forward', 'arch_sequence', 'bng', 'vehicle', 'glow', 'two_color_metal', 'puddles', 'facing_glow', 'bright_center', 'parallax', 'parallax_normal', 'hair', 'fresnel_tint'):
            if paths.get(key):
                entry[key] = paths[key]
        if paths.get('foliage'):
            entry['foliage'] = True
        if paths.get('alpha_cut'):
            entry['alpha_cut'] = float(paths['alpha_cut'])
        if 'za_fresnel_power' in paths:
            entry['za_fresnel_power'] = paths['za_fresnel_power']
        for role in (*ROLES, *(('_parallax_height',) if paths.get('parallax') else ()), *(('_sequence',) if paths.get('arch_sequence') else ()), *(('_bng_detail',) if paths.get('bng') else ()), *(('_vehicle_p2',) if paths.get('vehicle') else ()), *(('_glow_parallax',) if paths.get('glow', {}).get('kind') == 1 else ()), *(('_cma',) if paths.get('leaf_colour') else ())):
            path = paths.get(role)
            if not path:
                continue
            entry[role] = _cached_texture(archive, cache, path)
        if paths.get('tint', {}).get('variant'):
            entry['tint'] = dict(entry['tint'])
            for role in ('_tint_detail', '_tint_palette'):
                path = paths.get(role)
                if path: _cached_texture(archive, cache, path)
                if role == '_tint_detail': entry[role] = cache.get(path)
                elif cache.get(path):
                    entry['tint']['palette_rgb'] = tint_palette_sample(_with_pixels(archive, cache, path), entry['tint']['palette_u'])
                    entry['tint']['palette_source'] = path
        if paths.get('leaf_colour'):
            # Placeholder: one fixed-coordinate read of the palette ramp (no per-instance UV yet).
            path = paths['_cmb']
            _cached_texture(archive, cache, path)
            ramp = _with_pixels(archive, cache, path)
            if ramp:
                entry['leaf_colour'] = dict(paths['leaf_colour'], palette_rgb=leaf_palette_sample(ramp, *paths['leaf_colour']['palette_uv']))
        if paths.get('forward', {}).get('kind') == 'screen':
            path = paths.get('_screen_timeline')
            if path: _cached_texture(archive, cache, path, tolerate=False)
            timeline = _with_pixels(archive, cache, path)
            if not timeline: raise ValueError('Screen timeline source unavailable')
            # Linear filtering at normalized .5: exact RGBA8 source timeline, 512x1.
            q = tint_palette_sample(timeline, 0.5)[0]
            p = entry['forward']['params']; divisor = p['grid_divisor']
            if divisor <= 0: raise ValueError('Screen grid divisor must be positive')
            p['grid'] = p['grid_max'] * min(1.0, max(0.0, q / divisor))
            if p['grid'] <= 0: raise ValueError('Screen grid has no defined t=0 sample coordinates')
            entry['forward']['timeline_sample'] = q
            entry['forward']['timeline_source'] = path
        if paths.get('puddles'):
            sources=[]
            for role in ('_puddle_noise','_puddle_ripple'):
                path=paths.get(role)
                if path: _cached_texture(archive,cache,path,tolerate=False)
                sources.append(cache.get(path))
            if not all(sources) or not entry.get('_g') or not entry.get('_c'):
                raise ValueError('GroundPuddles source texture unavailable')
            key=('GroundPuddlesAtlas',*(t['path'] for t in sources))
            atlas=cache.get(key)
            if atlas is None: atlas=_remember(cache,key,puddle_texture_atlas(*(_with_pixels(archive,cache,t['path']) for t in sources)))
            entry['_puddle_atlas']=atlas
            entry['puddles']=dict(entry['puddles'], scales=atlas['scales'])
            if not entry.get('_n'): entry['puddles']['writer']='4A6CDAAA'
        if paths.get('rock_blend'):
            entry['rock_blend'] = paths['rock_blend']
            extra = []
            # color_breakup has no layer-B colour: its unread colour slice repeats gB to keep the volume layout.
            roles = ('_rock_mask', '_rock_g2', '_rock_g2', '_rock_n2') if paths['rock_blend'].get('variant') == 'color_breakup' \
                else ('_rock_mask', '_rock_c2', '_rock_g2', '_rock_n2')
            for role in roles:
                path = paths.get(role)
                if path: _cached_texture(archive, cache, path)
                extra.append(_with_pixels(archive, cache, path))
            if all(extra) and all(entry.get(role) for role in ('_c', '_g', '_n')):
                try:
                    entry['_rock_volume'] = rock_texture_volume(extra)
                    entry['rock_blend']['extra_srgb'] = [bool(t.get('srgb')) for t in extra[:3]]
                except ValueError as exc:
                    entry['rock_blend'] = dict(entry['rock_blend'], unresolved_second_layer=True, volume_error=str(exc))
            else:
                entry['rock_blend'] = dict(entry['rock_blend'], unresolved_second_layer=True)
        if material.get('path'):
            from rc2016.viewer.native_glsl import selected_translation
            try:
                try:
                    translated = selected_translation(archive, material['path'], paths)
                except (ValueError, KeyError, IndexError, OSError):
                    if resolved and translations is not None: translations[material['path']] = None
                    raise
                if resolved and translations is not None: translations[material['path']] = translated
                if translated:
                    native_textures = {}
                    for binding in translated['texture_bindings']:
                        path = binding['path']
                        # Native uploads use a separate sRGB/linear GL object. A caller that
                        # dropped this texture's pixels restores them for a variant it has not
                        # uploaded yet (the level loader's restore_native_pixels); reloading here
                        # would decode a shared texture again for every material binding it.
                        texture = _cached_texture(archive, cache, path, tolerate=False)
                        if not texture or texture.get('array_size', 1) != 1:
                            raise ValueError('Native source texture unavailable or not 2D: ' + path)
                        native_textures[binding['api']] = texture
                    entry['native_translation'] = translated
                    entry['native_textures'] = native_textures
            except (ValueError, KeyError, IndexError, OSError) as exc:
                entry['native_error'] = str(exc)
            from rc2016.viewer.experimental_materials import prepare_preview, preview_with_fallback
            try:
                preview = prepare_preview(archive, material['path']) if previews else None
                if preview:
                    preview_textures = {}
                    for binding in preview['texture_bindings']:
                        texture = _cached_texture(archive, cache, binding['path'], tolerate=False)
                        if not texture or texture.get('array_size',1) != 1:
                            raise ValueError('Experimental source texture unavailable: '+binding['path'])
                        preview_textures[binding['api']] = texture
                    entry['experimental_preview'] = preview_with_fallback(preview, entry)
                    entry['experimental_textures'] = preview_textures
            except (ValueError, KeyError, IndexError, OSError, StopIteration) as exc:
                entry['experimental_error'] = str(exc)
        result[material['index']] = entry
    return result
