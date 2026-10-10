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

from rc2016.formats.archive import decompress, lz4_block
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
# mo_gbl_color_tint_01 writer BE8C3EE0 (Animation/pipeline/Research/Materials/ColorTint01/COLOR_TINT_DECODE.md)
TINT_KEYS = dict(spec_tint=0x6B591974, mask_b=0x8ED5C5E8, colour_a=0xA815C29F, colour_b=0x311C9325, power_a=0x9E7A020A,
                 weight_b=0x482ECFEB, weight_a=0xD1279E51, power_b=0x077353B0)
TINT_G_SWITCH = 0x7107A2D8   # < 0.5: grey _g (R gloss, G spec); >= 0.5: coloured _g (rgb F0, gloss a^2)
# Self-lit K (= albedo * mask * K) by family: the default deferred key, then the
# coloured-gloss-emissive and emissive-in-c-alpha keys (rc2016/shaders/families.py).
# The key in the graph's own parameter table wins: materials can store keys their graph never reads.
SELF_LIT_K_KEYS = (0x66EFA55B, 0x06F19114, 0xF8C61683)
SPECULAR_SCALE_KEY, GLOSS_SCALE_KEY = 0x62835AF9, 0x24F4CA4C
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
    ('materialgraph/global/basic_incandescence/', dict(g_mode='grey', self_lit='g_blue', self_lit_key=0x5FD56522)),
    # Other models/enm_blarg_saucer/evidence/COLORPICK_DECODE.md (writer 66744D01)
    ('materialgraph/global/basic_colored_spec_emmisive_in_c_alpha/blarg_saucer_colorpick.',
     dict(g_mode='grey', self_lit='g_blue', self_lit_key=0x06F19114, pick=(0x40520184, 0xD257B634, 0x124A2F94))),
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
    # FE32FE79/B01E7CB2 cached deferred outputs; Materials/Foliage/FOLIAGE_DECODE.md.
    ('materialgraph/global/basic_foliage/basic_foliage.',
     dict(g_mode='grey', self_lit_key=None, foliage=True, alpha_cut=True)),
    # Tree leaf graphs: alpha-tested deferred writers (opaque, two-sided materials) whose `_c` alpha is
    # coverage only. Everything else keeps the generic reading: their g/n/cma/cmb terms are not decoded.
    ('materialgraph/environment/global/plant/gbl_plant_tree_foliage_transparent_01/rp_nov_plant_tree_foliage_transparent.',
     dict(alpha_cut=True)),
    ('materialgraph/environment/global/plant/gbl_plant_tree_foliage_transparent_01/gbl_plant_tree_foliage_transparent_01.',
     dict(alpha_cut=True)),
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
)
# Leaf cut-out (alpha test). The deferred foliage writers kill a fragment when its coverage, `_c`
# alpha (basic_foliage FE32FE79/B01E7CB2; the tree graphs' 0476D929/6F0C1A0C), is below the
# interpolated vertex output attr0.z. The vertex shaders read attr0.z from the engine's per-instance
# record (byte 268 of a 272-byte record), which is runtime data, not a graph or material constant.
# This default is therefore INFERRED, the conventional half coverage; it is not decoded game data.
FOLIAGE_ALPHA_CUTOFF = 0.5
# Water preview values that are not decoded (labelled presentation): opacity at normal incidence
# (Fresnel raises it to 1 at grazing angles), F0 0.02 (water, IOR 1.33), gloss of the reflection.
# The tile UV transforms are the pre-pass b9a5ef0f literals at time 0.
WATER_PRESENTATION = dict(opacity=0.8, f0=0.02, gloss=0.9, normal_strength=0.5,
                          uv_a=(0.095, 0.105, 0.248, 0.613), uv_b=(0.105, 0.095, 0.0, 0.0))
# Texture slot tables: 16-byte rows (path string offset u32, flags u32, slot hash u64), stored twice.
GRAPH_SLOT_SECTION, MATERIAL_SLOT_SECTION = 0x1CAFE804, 0xB967FF7A
# Blend/cull per material: .material section 0xE1275683, u32 at byte 16 (rc2016.pipeline.render_state).
MATERIAL_FLAGS_SECTION = 0xE1275683
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


def _graph_rule(graphs):
    return next((rule for prefix, rule in GRAPH_RULES for g in graphs if g.startswith(prefix)), {})


def _role(path):
    stem = Path(path).stem.lower()
    if stem.endswith(PICK_SUFFIX):
        return '_pick'
    return next((r for r in ('_c', '_g', '_n', '_h') if stem.endswith(r)), None)


def _graph_info(archive, graph_id):
    """(texture paths, slots, parameter key hashes, defaults) of one material graph entry.

    Many materials share a graph (a level loads hundreds of materials over a few dozen graphs),
    so the parse is kept on the archive object, per entry id. Callers must not mutate the result.
    """
    cache = getattr(archive, '_graph_info_cache', None)
    if cache is None:
        cache = {}
        try:
            archive._graph_info_cache = cache
        except AttributeError:
            pass   # objects without attributes just skip caching
    if graph_id not in cache:
        graph_data = decompress(archive.bytes(graph_id)[0])[0]
        textures = [r['path'] for r in references(graph_data) if r['path'].endswith('.texture')]
        slots = texture_slots(graph_data, GRAPH_SLOT_SECTION)
        keys, defaults = frozenset(), {}
        try:
            from rc2016.shaders.families import graph_parameter_tables
            tables, defaults = graph_parameter_tables(graph_data)
            keys = frozenset(row['key_hash'] for table in tables for row in table)
        except (ValueError, struct.error):
            defaults = {}
        cache[graph_id] = (textures, slots, keys, defaults)
    return cache[graph_id]


def material_texture_paths(archive, material_path: str) -> dict:
    """{'_c'|'_g'|'_n': texture path, 'k': float} for one material.

    Textures the material references directly win; the material graph's own
    defaults fill roles the material leaves out.
    """

    material_ids = [k for k in archive.resolve(material_path) if archive.entries[k]['type'] == 'material']
    if len(material_ids) != 1:
        return {}
    data = decompress(archive.bytes(material_ids[0])[0])[0]
    found = [r['path'] for r in references(data) if not r.get('resolution_error')]
    textures = [p for p in found if p.endswith('.texture')]
    graphs = [p for p in found if p.endswith('.materialgraph')]
    graph_keys, defaults, slots = set(), {}, {}
    for graph in graphs:
        ids = [k for k in archive.resolve(graph) if archive.entries[k]['type'] == 'materialgraph']
        if len(ids) == 1:
            graph_textures, graph_slots, keys, graph_defaults = _graph_info(archive, ids[0])
            textures += graph_textures
            slots.update(graph_slots)
            graph_keys |= keys
            defaults.update(graph_defaults)
    result = {}
    for path in dict.fromkeys(textures):
        role = _role(path)
        if role and role not in result:
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
        result['alpha_cut'] = FOLIAGE_ALPHA_CUTOFF
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
    else:
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
        instance = texture_slots(data, MATERIAL_SLOT_SECTION)
        wanted = {'_rock_mask': 0x49A7866B3404C763, '_g': 0x49A7866B7BF812C7,
                  '_rock_g2': 0x49A7866B42752E02, '_n': 0x49A7866B146D84F5,
                  '_rock_n2': 0x49A7866B2DE0B830, '_c': 0x49A7866B969AFD6A,
                  '_rock_c2': 0x49A7866BAF17C1AF}
        for role, slot in wanted.items():
            path = instance.get(slot) or slots.get(slot)
            if path: result[role] = path
            else: result.pop(role, None)
        result['spec'] = value(0xFEAF9A36, 1.0)
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
        if rule['forward'] == 'water':
            params.update(WATER_PRESENTATION)
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
    info = parse_header(decompress(archive.bytes(built[0])[0])[0])
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


def load_texture(archive, texture_path: str) -> dict:
    """Top mip of one game texture as GPU-ready blocks plus its GL format."""

    texture = _read_top_mip(archive, texture_path, full=False)
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
            found = [r['path'] for r in references(decompress(archive.bytes(ids[0])[0])[0]) if not r.get('resolution_error')]
            direct = [p for p in found if p.endswith('.texture')]
            via_graph = []
            for graph in (p for p in found if p.endswith('.materialgraph')):
                gids = [k for k in archive.resolve(graph) if archive.entries[k]['type'] == 'materialgraph']
                if len(gids) == 1:
                    via_graph += [r['path'] for r in references(decompress(archive.bytes(gids[0])[0])[0])
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


def texture_header(archive, texture_path: str) -> dict:
    """Header facts only (format and sizes), without untiling any pixels."""

    built = [k for k in archive.resolve(texture_path) if archive.entries[k]['built_path'].lower().endswith('.built')]
    if len(built) != 1:
        raise ValueError(f'Texture {texture_path} has {len(built)} built assets')
    info = parse_header(decompress(archive.bytes(built[0])[0])[0])
    return {k: v for k, v in info.items() if k in ('format', 'hd_width', 'hd_height', 'sd_width', 'sd_height')}


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


def _with_pixels(archive, cache: dict, path):
    """cache[path] for CPU sampling: reloaded when a caller dropped its 'pixels' (see material_textures)."""
    texture = cache.get(path)
    if texture and 'pixels' not in texture:
        texture = cache[path] = load_texture(archive, path)
    return texture


def material_textures(archive, materials: list[dict], cache: dict) -> dict[int, dict]:
    """Material slot -> {'_c'/'_g'/'_n'/'_pick': texture or None, 'k', 'g_mode', 'self_lit', 'spec', 'gloss'[, 'pick']}.

    Missing or unloadable textures are None and that part is shaded without
    them. ``cache`` maps texture path -> loaded texture/None across models.
    A caller may drop 'pixels' and 'slices' from cached textures it has
    uploaded (the level loader does); sources sampled here on the CPU (tint
    palette, screen timeline, puddle and RockBlend layers) are then reloaded.
    """

    result = {}
    for material in materials:
        entry = {role: None for role in ROLES}
        try:
            paths = material_texture_paths(archive, material['path']) if material.get('path') else {}
        except (ValueError, KeyError, OSError):
            paths = {}
        entry.update(k=paths.get('k', 0.0), g_mode=paths.get('g_mode', 'colored'), self_lit=paths.get('self_lit', 'c_alpha'),
                     spec=paths.get('spec', 1.0), gloss=paths.get('gloss', 1.0))
        for key in ('pick', 'tint', 'spec_tint', 'forward', 'arch_sequence', 'bng', 'vehicle', 'glow', 'two_color_metal', 'puddles', 'facing_glow', 'parallax', 'parallax_normal'):
            if paths.get(key):
                entry[key] = paths[key]
        if paths.get('foliage'):
            entry['foliage'] = True
        if paths.get('alpha_cut'):
            entry['alpha_cut'] = float(paths['alpha_cut'])
        if 'za_fresnel_power' in paths:
            entry['za_fresnel_power'] = paths['za_fresnel_power']
        for role in (*ROLES, *(('_parallax_height',) if paths.get('parallax') else ()), *(('_sequence',) if paths.get('arch_sequence') else ()), *(('_bng_detail',) if paths.get('bng') else ()), *(('_vehicle_p2',) if paths.get('vehicle') else ()), *(('_glow_parallax',) if paths.get('glow', {}).get('kind') == 1 else ())):
            path = paths.get(role)
            if not path:
                continue
            if path not in cache:
                try:
                    cache[path] = load_texture(archive, path)
                except (ValueError, KeyError, OSError):
                    cache[path] = None
            entry[role] = cache[path]
        if paths.get('tint', {}).get('variant'):
            entry['tint'] = dict(entry['tint'])
            for role in ('_tint_detail', '_tint_palette'):
                path = paths.get(role)
                if path and path not in cache:
                    try: cache[path] = load_texture(archive, path)
                    except (ValueError, KeyError, OSError): cache[path] = None
                if role == '_tint_detail': entry[role] = cache.get(path)
                elif cache.get(path):
                    entry['tint']['palette_rgb'] = tint_palette_sample(_with_pixels(archive, cache, path), entry['tint']['palette_u'])
                    entry['tint']['palette_source'] = path
        if paths.get('forward', {}).get('kind') == 'screen':
            path = paths.get('_screen_timeline')
            if path and path not in cache:
                cache[path] = load_texture(archive, path)
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
                if path and path not in cache: cache[path]=load_texture(archive,path)
                sources.append(cache.get(path))
            if not all(sources) or not entry.get('_g') or not entry.get('_c'):
                raise ValueError('GroundPuddles source texture unavailable')
            key=('GroundPuddlesAtlas',*(t['path'] for t in sources))
            if key not in cache: cache[key]=puddle_texture_atlas(*(_with_pixels(archive,cache,t['path']) for t in sources))
            entry['_puddle_atlas']=cache[key]
            entry['puddles']=dict(entry['puddles'], scales=cache[key]['scales'])
            if not entry.get('_n'): entry['puddles']['writer']='4A6CDAAA'
        if paths.get('rock_blend'):
            entry['rock_blend'] = paths['rock_blend']
            extra = []
            for role in ('_rock_mask', '_rock_c2', '_rock_g2', '_rock_n2'):
                path = paths.get(role)
                if path and path not in cache:
                    try: cache[path] = load_texture(archive, path)
                    except (ValueError, KeyError, OSError): cache[path] = None
                extra.append(_with_pixels(archive, cache, path))
            if all(extra) and all(entry.get(role) for role in ('_c', '_g', '_n')):
                try:
                    entry['_rock_volume'] = rock_texture_volume(extra)
                    entry['rock_blend']['extra_srgb'] = [bool(t.get('srgb')) for t in extra[:3]]
                except ValueError as exc:
                    entry['rock_blend'] = dict(entry['rock_blend'], unresolved_second_layer=True, volume_error=str(exc))
            else:
                entry['rock_blend'] = dict(entry['rock_blend'], unresolved_second_layer=True)
        result[material['index']] = entry
    return result
