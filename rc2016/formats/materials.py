"""Resolve PS4 model material and texture dependencies from source references."""
import os
import json
import math
import hashlib
import struct
from pathlib import Path

from rc2016.formats.container import read_container
from rc2016.common import asset_path, dump, file_hash, load, read, sha, write
from rc2016.formats.index import references
from rc2016.formats.texture import decode_texture
from rc2016.formats.texture_transport import TextureDecodeCache,validate_source_transport
from rc2016.shaders.families import preflight_graph


# Stay below the traditional Windows MAX_PATH boundary even for the temporary
# DDS write and the longer derived PNG used for R8G8_UNORM normals.
BLENDER_SAFE_TEXTURE_PATH_CHARS=240

# Runtime capture and source DDS evidence pin this one Ratchet gloss image to
# BC7 sRGB sampling. Keep the exception bound to both decoded source identities.
RATCHET_BODY_GLOSS_SRGB=(
    'material/character/hero/hero_ratchet_body/hero_ratchet_body.material',
    '85149578f9b8eae6bb7d85eb2e7c74215956ff3533e1f80699b205bc7c73d312',
    'textures/character/hero/ratchet/ratchet_body_g.texture',
    'dd3baf7bf56554b96d123fc4e332c22620e16a4c866df2cdc395849f952acde4',
)

# DXGI formats the GPU decodes as sRGB when sampling.  The shadPS4 RenderDoc
# capture (G-buffer writer: specular/gloss bound as BC7_UNORM_SRGB, written to an
# R8G8B8A8_SRGB target and read back as linear F0 by the lighting pass) shows the
# hardware format, not the _c/_g/_n suffix, decides the transfer.  See
# docs/shading.md.  The Ratchet exception above is
# now implied by this rule and kept only as a fail-closed regression anchor.
SRGB_DXGI_FORMATS=frozenset({29,72,75,78,91,93,99})


def native_texture_colorspace(fmt, role):
    """Blender colourspace that reproduces the GPU's sampling of this texture."""
    if role=='normal':
        return 'Non-Color'
    if fmt is None:   # format unknown: previous suffix rule
        return 'sRGB' if role=='base_color' else 'Non-Color'
    return 'sRGB' if int(fmt) in SRGB_DXGI_FORMATS else 'Non-Color'


# This key is named by the hash-pinned Sheepinator source audit.  Other
# 4-byte values stay raw until a native record independently establishes
# their scalar type and meaning.
KNOWN_FLOAT_PARAMETER_KEYS={0x24F4CA4C:'Glossiness'}
MATERIAL_PARAMETER_KEYS=0xF2DC60EC
MATERIAL_PARAMETER_VALUES=0x5DA317BF


def extract_material_parameters(data, *, expected_sha256=None):
    """Extract only known, bounded DAT1 material-instance parameter fields.

    The source record layout is: a DAT1 section table, a key section of
    (value-relative offset, byte size) u16 pairs followed by a property hash,
    and a value
    section.  A property is typed only when its hash is in the evidence-backed
    schema above.  Unknown properties are returned as raw bytes.
    """
    data=bytes(data)
    digest=hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest.lower()!=expected_sha256.lower():
        raise ValueError('Material source SHA-256 mismatch')
    if len(data)<16 or struct.unpack_from('<I',data,0)[0]!=0x44415431:
        raise ValueError('Invalid DAT1 material header')
    declared=struct.unpack_from('<I',data,8)[0]
    count=struct.unpack_from('<H',data,12)[0]
    table_end=16+count*12
    if declared!=len(data) or table_end>declared:
        raise ValueError('Invalid DAT1 size or section table bounds')
    sections={}
    for i in range(count):
        tag,offset,size=struct.unpack_from('<III',data,16+i*12)
        if offset<table_end or offset+size>declared or tag in sections:
            raise ValueError('Invalid or duplicate DAT1 section bounds')
        sections[tag]=(offset,size)
    has_keys=MATERIAL_PARAMETER_KEYS in sections
    has_values=MATERIAL_PARAMETER_VALUES in sections
    if not has_keys and not has_values:
        return dict(schema='rcps4-material-instance-parameters-v1',source_sha256=digest,
                    status='no_parameter_sections',parameters=[],unsupported_parameters=[])
    if has_keys!=has_values:
        missing='key' if not has_keys else 'value'
        raise ValueError(f'Material parameter section pair is incomplete; missing {missing} section')
    key_base,key_size=sections[MATERIAL_PARAMETER_KEYS]
    value_base,value_size=sections[MATERIAL_PARAMETER_VALUES]
    # The native key records are 8-byte structures: u16 relative offset,
    # u16 byte size, u32 property hash. This shape repeats in glass and body.
    if key_size%8:
        raise ValueError('Material parameter key section is not a sequence of 8-byte entries')
    parameters=[];unsupported=[];seen=set()
    for pos in range(key_base,key_base+key_size,8):
        relative,size,key=struct.unpack_from('<HHI',data,pos)
        if relative+size>value_size:
            raise ValueError(f'Material parameter 0x{key:08X} exceeds value section bounds')
        if (relative,size,key) in seen:
            continue
        seen.add((relative,size,key))
        payload=data[value_base+relative:value_base+relative+size]
        row=dict(key=f'0x{key:08X}',key_hash=key,entry_file_offset=pos,
                 value_relative_offset=relative,value_file_offset=value_base+relative,
                 size=size,raw_hex=payload.hex())
        label=KNOWN_FLOAT_PARAMETER_KEYS.get(key)
        if label:
            if size!=4:
                raise ValueError(f'{label} parameter must be exactly one float32')
            value=struct.unpack('<f',payload)[0]
            if not math.isfinite(value):
                raise ValueError(f'{label} parameter is not a finite float32')
            row.update(name=label,type='float32',value=value)
            parameters.append(row)
        else:
            row.update(type='unsupported_untyped',reason='No source-validated key/type mapping')
            unsupported.append(row)
    return dict(schema='rcps4-material-instance-parameters-v1',source_sha256=digest,
        status='parsed',parameter_key_section=dict(file_offset=key_base,size=key_size),
        parameter_value_section=dict(file_offset=value_base,size=value_size),
        parameters=parameters,unsupported_parameters=unsupported,
        unsupported_operations=['Unknown property hashes and value types',
            'Compiled shader binding, unit/conversion semantics, and runtime upload mapping'])


def texture_output_dir(output_root, model_root, export_textures):
    output_root=Path(output_root)
    model_root=Path(model_root)
    if export_textures:
        return model_root/'textures'
    # The source-path DDS key is already 64 hex characters. A readable model
    # stem here can make otherwise valid output roots exceed Blender's path
    # limit (e.g. the source-linked Fusion wearable). Hash its relative model
    # directory so separate multipart libraries stay isolated and bounded.
    relative=model_root.relative_to(output_root).as_posix()
    return output_root/'cache'/'t'/sha(relative.encode('utf-8'))[:20]


def preflight_texture_output(output_root, model_root, export_textures):
    """Fail early if the shortest generated DDS path is not Blender-safe."""
    root=Path(output_root)
    model_root=Path(model_root)
    texture_dir=texture_output_dir(root,model_root,export_textures)
    probe=texture_dir/('0'*64+'.dds')
    absolute=os.path.abspath(str(probe))
    path_units=len(absolute.encode('utf-16-le'))//2
    if path_units>BLENDER_SAFE_TEXTURE_PATH_CHARS:
        excess=path_units-BLENDER_SAFE_TEXTURE_PATH_CHARS
        raise ValueError(
            f'OutputRoot is too long for Blender-safe converted texture paths '
            f'({path_units} UTF-16 path units; maximum {BLENDER_SAFE_TEXTURE_PATH_CHARS}). '
            f'Shorten -OutputRoot by at least {excess} UTF-16 path units and retry: {absolute}')
    return Path(absolute).parent


def texture_output_path(texture_dir, source_path):
    """Use a full SHA-256 source-path key instead of mirroring deep asset paths."""
    canonical=asset_path(source_path)
    return Path(texture_dir)/(sha(canonical.encode('utf-8'))+'.dds')


def texture_binding(source_path, built_asset_id, stream_asset_id, colorspace, report):
    """Keep source identity in the manifest while recording the generated file path."""
    binding=dict(path=source_path,role=texture_role(source_path),built_asset_id=built_asset_id,
        stream_asset_id=stream_asset_id,colorspace=colorspace)
    binding.update(report)
    return binding


def texture_role(path):
    stem=Path(path).stem.lower()
    for suffix,role in (('_c','base_color'),('_g','colored_specular_gloss'),('_n','normal'),
                        ('_e','emission'),('_m','packed_mask'),('_ao','ambient_occlusion')):
        if stem.endswith(suffix):
            return role
    return 'additional'


def material_texture_colorspace(material_path, material_sha256, texture_path,
                                texture_sha256, default_colorspace):
    """Apply only the captured Ratchet material/image transfer override."""
    key=(asset_path(material_path),str(material_sha256).lower(),
         asset_path(texture_path),str(texture_sha256).lower())
    if key==RATCHET_BODY_GLOSS_SRGB:
        return 'sRGB'
    return default_colorspace


def _one(candidates,label):
    if len(candidates)!=1:
        raise ValueError(f'{label} must resolve exactly once; found {len(candidates)}')
    return candidates[0]


def graph_semantics(graph_paths):
    names=' '.join(Path(path).stem.lower() for path in graph_paths)
    if 'glass' in names:
        return 'thin_glass'
    if any(token in names for token in ('transparent','holo','masked','alpha')):
        return 'compiled_transparency_unresolved'
    return 'ordinary_surface'


def validated_profile(material_path, material_sha256):
    """Return a recipe only when its immutable source identity matches exactly."""
    profile_path=Path(__file__).with_name('material_profiles')/(material_sha256+'.json')
    if not profile_path.is_file():
        return None
    profile=load(profile_path)
    if profile.get('schema')!='rcps4-material-profile-v1':
        raise ValueError('Unsupported material profile schema: '+str(profile_path))
    if profile.get('material_sha256')!=material_sha256:
        raise ValueError('Material profile filename/hash mismatch: '+str(profile_path))
    if asset_path(profile.get('material_path',''))!=asset_path(material_path):
        raise ValueError('Material profile source path mismatch: '+str(profile_path))
    if not isinstance(profile.get('recipe'),dict) or not profile['recipe'].get('kind'):
        raise ValueError('Material profile has no recipe kind: '+str(profile_path))
    # The presentation-only Combuster controls are intentionally narrow.
    # Reject malformed/over-broad values before they reach Blender graph code.
    if profile.get('evidence_class')=='presentation_tuned':
        if profile.get('game_equivalence') is not False:
            raise ValueError('Presentation-tuned material profile must explicitly disclaim game equivalence: '+str(profile_path))
        presentation=profile['recipe'].get('presentation_overrides')
        if presentation is not None:
            expected=dict(disable_c_alpha_emission=True,principled_emission_strength=0.0,
                          principled_roughness=0.48,principled_metallic=0.0,
                          retain_source_base_color=True,retain_source_normal=True)
            if presentation!=expected:
                raise ValueError('Unsupported presentation override set in material profile: '+str(profile_path))
            bindings=profile.get('source_texture_bindings')
            if not isinstance(bindings,list) or {x.get('role') for x in bindings}!={'base_color','colored_specular_gloss','normal'}:
                raise ValueError('Presentation profile must pin C/G/N source texture references: '+str(profile_path))
    result=dict(profile)
    result['profile_path']=str(profile_path.resolve())
    result['profile_sha256']=file_hash(profile_path)
    return result


def material_evidence(profile, semantics, bindings, graph_assets):
    """Separate reproducible Blender recipes from evidence of game shader behavior."""
    profile_bound=bool(profile)
    profile_provenance=profile.get('source_provenance',{}) if profile else {}
    roles={b.get('role') for b in bindings}
    generic_cg=not profile_bound and {'base_color','colored_specular_gloss'}<=roles
    return dict(schema='rcps4-material-evidence-v1',
        source_bindings=dict(status='source_exact' if bindings or graph_assets else 'unavailable',
            material_identity='path_and_sha256' if profile_bound else 'extracted_material_path_and_sha256',
            texture_bindings='exact_references_and_decoded_assets' if bindings else 'none',
            texture_reference_origins=[dict(path=b.get('path'),dds_sha256=b.get('dds_sha256'),
                references=b.get('source_references',[])) for b in bindings],
            materialgraph_bindings='exact_references_and_hashes' if graph_assets else 'none',
            evidence=profile_provenance.get('shader_resource_binding','') or
                'Extracted material/materialgraph references and decoded source asset hashes.'),
        blender_recipe=dict(status='hash_bound_reproducible' if profile_bound else 'generic_reproducible_approximation',
            profile_path=profile.get('profile_path') if profile else None,
            profile_sha256=profile.get('profile_sha256') if profile else None,
            recipe_kind=profile.get('recipe',{}).get('kind') if profile else semantics,
            means='Recipe identity and persistence only; does not establish game shader equivalence.'),
        capture_runtime_equivalence=dict(status='unknown',game_equivalence=False,
            reason='No capture/runtime evidence binds this material instance to executed shader behavior.'),
        generic_cg_presentation_gate=dict(
            status='review_required_before_material_equivalence_claim' if generic_cg else
                'not_applicable_profile_bound_or_cg_inputs_absent',
            triggered=generic_cg,
            reason=('Unprofiled C/G materials use the builder default reconstruction mix of 1.0; '
                'the factor is unresolved presentation tuning, not source shader evidence.'
                if generic_cg else None),
            builder_default_reconstruction_mix=1.0 if generic_cg else None,
            effect='Reporting only; does not change recipe, delivery status, or game_equivalence.',
            required_evidence=('Reopened active-output graph plus model/source-hash-bound profile decision '
                'and a separately labelled presentation review; none establishes game equivalence.'
                if generic_cg else None)),
        saved_node_output_reachability=dict(status='deferred_until_reopened_blend',
            means='Per-binding connected/disconnected/unresolved status is recorded by builder validation after reopening the saved material node graph.'),
        unsupported_operations=['Compiled materialgraph execution and runtime parameter binding',
            'Exact game shader output equivalence'])


def shader_build_status(profile, semantics, bindings, shader_preflights):
    """Describe the builder recipe without calling a preflight family applied."""
    if profile:
        return 'validated_hash_bound_recipe'
    if len(shader_preflights)==1 and shader_preflights[0].get('family')=='character_glass':
        return 'partial_native_character_glass'
    if semantics=='thin_glass':
        return 'thin_glass_approximation'
    if len(shader_preflights)>1:
        base_rows=[row for row in bindings if row.get('role')=='base_color']
        real_base=any(not row.get('source_references') or any(
            not ref.get('graph_fallback') for ref in row.get('source_references',[])) for row in base_rows)
        return ('ambiguous_family_color_only_preview' if real_base else
                'ambiguous_family_color_only_missing_base_color')
    def has_real_binding(role):
        return any(row.get('role')==role and (not row.get('source_references') or any(
            not ref.get('graph_fallback') for ref in row.get('source_references',[]))) for row in bindings)
    if len(shader_preflights)==1 and shader_preflights[0].get('status')=='supported_partial':
        if has_real_binding('base_color') and has_real_binding('colored_specular_gloss'):
            return 'partial_skin_frontend'
        return ('partial_family_color_only_preview' if has_real_binding('base_color') else
            'partial_family_color_only_missing_base_color')
    base_rows=[row for row in bindings if row.get('role')=='base_color']
    real_base=any(not row.get('source_references') or any(
        not ref.get('graph_fallback') for ref in row.get('source_references',[])) for row in base_rows)
    return 'color_only_preview' if real_base else 'color_only_preview_missing_base_color'


def _texture_reference(path, reference_kind, source_path, byte_offset):
    """Preserve which native source record named this exact image path."""
    normalized=asset_path(path)
    graph_fallback=(reference_kind=='materialgraph_reference' and
        normalized.startswith('textures/default_pink_shader/'))
    return dict(reference_kind=reference_kind,source_path=asset_path(source_path),
        byte_offset=byte_offset,graph_fallback=graph_fallback)


# Texture slot tables: 16-byte records (u32 path string offset, u32 flag, u32 slot key,
# u32 type hash). Material section 0xB967FF7A binds instance textures by slot key; graph
# section 0x1CAFE804 lists the slots its shaders declare (flag 1 = instance-overridable,
# flag 0 = fixed graph texture). A material record whose key the graph never declares is
# not sampled by any of the graph's shaders (enm_puffer_fish keeps stale enm_alien_swarmer
# records this way).
MATERIAL_TEXTURE_SLOTS=0xB967FF7A
GRAPH_TEXTURE_SLOTS=0x1CAFE804


def texture_slot_records(data, section_tag):
    data=bytes(data)
    if len(data)<16 or struct.unpack_from('<I',data,0)[0]!=0x44415431:
        return []
    count=struct.unpack_from('<H',data,12)[0]
    sections={}
    for i in range(count):
        tag,off,size=struct.unpack_from('<III',data,16+12*i)
        sections[tag]=(off,size)
    if section_tag not in sections:
        return []
    off,size=sections[section_tag]
    if size%16 or off+size>len(data):
        raise ValueError('Texture slot table is not 16-byte records within the file: 0x%08X'%section_tag)
    rows=[]
    for pos in range(off,off+size,16):
        string_offset,flag,key,type_hash=struct.unpack_from('<IIII',data,pos)
        end=data.find(b'\0',string_offset)
        if not 0<string_offset<len(data) or end<0:
            raise ValueError('Texture slot record has no path string at offset %d'%pos)
        # An empty string is an explicitly unset slot (enm_blarg_standard_body binds key
        # 0x0056CBD0 to ""); keep the record as evidence, it names no texture.
        raw_path=data[string_offset:end].decode('ascii')
        rows.append(dict(path=asset_path(raw_path) if raw_path else None,slot_key=key,
            flag=flag,type_hash=type_hash,string_offset=string_offset,record_file_offset=pos))
    return rows


SLOT_ROLE_PRIORITY=('base_color','colored_specular_gloss','normal','secondary_normal')


def resolve_texture_slots(material_data, graph_datas, shader_preflights, texture_paths, texture_references):
    """Drop material textures bound only through slots no graph declares.

    Annotates material_direct references in place with their slot key. Returns
    (kept paths, unbound evidence rows, {path: role override}); role overrides come
    only from a registered family's source-verified slot-key map. Without both
    slot tables nothing is changed.
    """
    material_rows=texture_slot_records(material_data,MATERIAL_TEXTURE_SLOTS)
    graph_rows=[row for data in graph_datas for row in texture_slot_records(data,GRAPH_TEXTURE_SLOTS)]
    if not material_rows or not graph_rows:
        return texture_paths,[],{}
    declared={row['slot_key'] for row in graph_rows}
    by_offset={row['string_offset']:row for row in material_rows}
    for refs in texture_references.values():
        for ref in refs:
            row=by_offset.get(ref.get('byte_offset')) if ref['reference_kind']=='material_direct' else None
            if row is not None:
                ref['slot_key']='0x%08X'%row['slot_key']
                ref['graph_slot_declared']=row['slot_key'] in declared
    kept,unbound=[],[]
    for path in texture_paths:
        refs=texture_references.get(path,[])
        if any(ref['reference_kind']!='material_direct' or ref.get('graph_slot_declared',True) for ref in refs):
            kept.append(path)
        else:
            unbound.append(dict(path=path,references=refs,
                reason='Material binds this texture only under slot keys that no referenced materialgraph declares (section 0x1CAFE804); no shader samples it.'))
    slot_map={}
    for preflight in shader_preflights:
        for key,role in (preflight.get('texture_slots') or {}).items():
            slot_map[int(key,16)]=role
    roles={}
    for path in kept:
        keys={int(ref['slot_key'],16) for ref in texture_references.get(path,[]) if ref.get('graph_slot_declared')}
        found=sorted({slot_map[k] for k in keys if k in slot_map},key=SLOT_ROLE_PRIORITY.index)
        if found:
            roles[path]=dict(role=found[0],slot_roles=found)
    return kept,unbound,roles


def classify_absent_texture(path, refs, material_data, graph_datas):
    """Describe a texture path that has no entry at all in the shipped archive.

    A graph slot default is replaced when the material binds the same slot key to
    another path; every other reference (a declared material binding, a graph
    default the instance keeps, or a reference without slot evidence) is treated as
    sampled. Nothing is substituted for the missing image either way.
    """
    material_rows=texture_slot_records(material_data,MATERIAL_TEXTURE_SLOTS)
    graph_rows=[row for data in graph_datas for row in texture_slot_records(data,GRAPH_TEXTURE_SLOTS)]
    normalized=asset_path(path)
    instance={}
    for row in material_rows:
        instance.setdefault(row['slot_key'],set()).add(row['path'])
    graph_keys=sorted({row['slot_key'] for row in graph_rows if row['path']==normalized})
    material_keys=sorted({row['slot_key'] for row in material_rows if row['path']==normalized})
    overridden=[key for key in graph_keys if instance.get(key,set())-{None,normalized}]
    sampled=(any(ref['reference_kind']=='material_direct' and ref.get('graph_slot_declared',True) for ref in refs)
        or (any(ref['reference_kind']!='material_direct' for ref in refs) and (not graph_keys or len(overridden)!=len(graph_keys))))
    row=dict(path=path,references=refs,graph_slot_keys=['0x%08X'%k for k in graph_keys],
        material_slot_keys=['0x%08X'%k for k in material_keys],
        overridden_graph_slot_keys=['0x%08X'%k for k in overridden])
    if sampled:
        row.update(status='missing_from_archive',
            reason='Texture is bound to a sampled slot but has no entry in the shipped archive layout; no image is recoverable and no substitute is applied.')
    else:
        row.update(status='overridden_graph_default_missing_from_archive',
            reason='Graph slot default has no entry in the shipped archive layout; the material instance binds every such slot key to another texture, so it is not sampled.')
    return row


# Viewer reading keys that change the result beyond the generic _g/self-lit front-end
# ('screen' is the viewer's forward 'screen' entry; 'bng'/'bng_detail' the basic_normal_gloss variants).
VIEWER_RULE_EXTRAS=('pick','tint','spec_tint','arch_sequence','vehicle','glow','two_color_metal','puddles',
    'foliage','za_fresnel_power','screen','rock_blend','bng','bng_detail','facing_glow','bright_center','parallax','alpha_cut','leaf_colour','bright_glow','fresnel_tint','hair')
# Terms rc_game_shader.apply_viewer_graph_rule builds (same GLSL math as gl_shading.FRAGMENT/GLASS_FRAGMENT).
VIEWER_RULE_BUILT=frozenset(VIEWER_RULE_EXTRAS)-{'parallax'}
# Extra source images each term samples (texture_preview.material_texture_paths roles).
VIEWER_TERM_ROLES=dict(pick=('_pick',),tint=('_h','_tint_detail','_tint_palette','_pick'),arch_sequence=('_h','_sequence','_pick'),
    vehicle=('_pick','_vehicle_p2','_h'),glow=('_h','_glow_parallax'),puddles=('_puddle_noise','_puddle_ripple'),
    screen=('_screen_timeline','_c','_n','_pick','_g'),rock_blend=('_rock_mask','_rock_c2','_rock_g2','_rock_n2'),bng=('_h',),bng_detail=('_bng_detail',),parallax=('_parallax_height',),leaf_colour=('_cma','_cmb'))
# Writers whose tangent normal z is 1-x*x-y*y (gl_shading.FRAGMENT), not sqrt(1-x*x-y*y).
VIEWER_LINEAR_Z_TERMS=('bng','foliage','za_fresnel_power','glow','two_color_metal','rock_blend','parallax','hair')


def _viewer_rule_wins_translation(translated, rule):
    """Keep proven C/G/N for POM-only omissions; other missing terms retain the native route."""
    unported = set(rule.get('unported') or ())
    return not (translated and unported and not unported.issubset({'parallax'}))


def final_shader_status(build_status, rule, bound_paths, shader_translation, profile, shader_preflights, graph_paths=()):
    """The Blender front-end a material is built with: ``build_status`` (shader_build_status) unless a
    usable viewer graph rule or a ready native translation replaces it.

    As in the viewer (native_glsl.selected_translation), only an exact graph rule keeps precedence over a
    ready translation; a family-prefix or generic reading yields to it.
    """
    from rc2016.formats.links import EXACT, rule_coverage
    # The arch-sequence writer never samples _c.
    anchor=rule.get('textures',{}).get('_g' if 'arch_sequence' in rule.get('ported',()) else '_c')
    # The facing-glow writer (Qwark bulb) and the glow shell (93695188) sample no texture at all.
    viewer_ready=(build_status.startswith('color_only_preview') and rule.get('applies') and
                  (anchor in bound_paths or 'facing_glow' in rule.get('ported',()) or 'bright_glow' in rule.get('ported',())))
    translated=(shader_translation.get('status')=='ready' and not profile and
        not any(p.get('family') in ('skin_emissive','basic_skin','character_glass') for p in shader_preflights))
    exact=rule_coverage(list(graph_paths))[0]==EXACT
    if viewer_ready and (not translated or (exact and _viewer_rule_wins_translation(translated, rule))):
        # Built in Blender by rc_game_shader.apply_viewer_graph_rule (same reading as the asset viewer).
        # A fully ported viewer rule wins over the native translation, which drops tint/pick/foliage
        # terms and the normal map (battery cannon: black trim built white); geometry._material follows.
        return 'viewer_graph_rule_frontend'
    if translated:
        return 'native_translated_deferred_frontend'
    return build_status


def _viewer_constant_samples(archive, paths):
    """CPU samples the viewer takes in texture_preview.material_textures (same helper functions)."""
    from rc2016.viewer.textures import load_texture,tint_palette_sample,leaf_palette_sample
    tint=paths.get('tint') or {}
    if tint.get('variant') and paths.get('_tint_palette'):
        tint['palette_rgb']=tint_palette_sample(load_texture(archive,paths['_tint_palette']),tint['palette_u'])
        tint['palette_source']=paths['_tint_palette']
    leaf=paths.get('leaf_colour')
    if leaf:
        leaf['palette_rgb']=leaf_palette_sample(load_texture(archive,paths['_cmb']),*leaf['palette_uv'])
        leaf['palette_source']=paths['_cmb']
    forward=paths.get('forward') or {}
    if forward.get('kind')=='screen':
        if not paths.get('_screen_timeline'):raise ValueError('Screen timeline source unavailable')
        q=tint_palette_sample(load_texture(archive,paths['_screen_timeline']),0.5)[0]
        p=forward['params'];divisor=p['grid_divisor']
        if divisor<=0:raise ValueError('Screen grid divisor must be positive')
        p['grid']=p['grid_max']*min(1.0,max(0.0,q/divisor))
        if p['grid']<=0:raise ValueError('Screen grid has no defined t=0 sample coordinates')
        forward['timeline_sample']=q;forward['timeline_source']=paths['_screen_timeline']


def viewer_graph_rule(archive, material_path, bound_paths=None):
    """The asset viewer's per-graph material reading, for the Blender front-end.

    Same function the viewer's Shaded mode uses (texture_preview.material_texture_paths):
    _g layout (grey R/G, skin grey cap 0.5, coloured rgb/alpha), specular and gloss scales,
    effective self-lit K and mask channel, the exact paths it binds, and every graph term
    (tints, palettes, rims, detail normals, glow, ...) with the viewer's frozen values.
    A term is 'ported' when the Blender front-end builds it and every image it samples is a
    bound source texture (``bound_paths``); otherwise it is listed in 'unported' with a reason.
    """
    try:
        from rc2016.viewer.textures import material_texture_paths
        paths=material_texture_paths(archive,material_path)
    except Exception as exc:
        return dict(applies=False,reason='viewer reading unavailable: %s'%exc)
    if not paths:
        return dict(applies=False,reason='material asset did not resolve to one entry')
    forward=paths.get('forward') or {}
    if forward and forward.get('kind') not in ('screen','bright_glow'):
        return dict(applies=False,reason='forward (glass/VFX) graph; handled by its own front-ends')
    try:
        _viewer_constant_samples(archive,paths)
    except Exception as exc:
        return dict(applies=False,reason='viewer constant sample unavailable: %s'%exc)
    bng=paths.get('bng') or {}
    present=[key for key in VIEWER_RULE_EXTRAS if (forward.get('kind')=='screen' and forward if key=='screen' else
        forward.get('kind')=='bright_glow' if key=='bright_glow' else
        paths.get('_bng_detail') and bng.get('kind') in (2,3,6) if key=='bng_detail' else
        bng.get('kind') in (1,3,4,6,7,8,9) if key=='bng' else key in paths if key=='za_fresnel_power' else paths.get(key))]
    roles={'_c','_g','_n'}|{role for key in present for role in VIEWER_TERM_ROLES.get(key,())}
    textures={role:paths.get(role) for role in sorted(roles) if paths.get(role) or role in ('_c','_g','_n')}
    bound={str(path).lower() for path in (bound_paths or ())}
    ported,unported,reasons=[],[],{}
    for key in present:
        missing=[textures[role] for role in VIEWER_TERM_ROLES.get(key,()) if textures.get(role) and bound_paths is not None
                 and textures[role].lower() not in bound]
        if key == 'parallax':reasons[key]='multi-step height-alpha view-ray UV march and seven height shadows are not built in Blender; unshifted C/G/N only'
        elif key not in VIEWER_RULE_BUILT:reasons[key]='not built in the Blender front-end'
        elif missing:reasons[key]='sampled source texture is not a bound material texture: '+', '.join(missing)
        else:ported.append(key);continue
        unported.append(key)
    terms={key:paths[key] for key in ('pick','tint','spec_tint','arch_sequence','vehicle','glow','two_color_metal',
        'puddles','za_fresnel_power','rock_blend','bng','facing_glow','bright_center','parallax','alpha_cut','leaf_colour','fresnel_tint','hair') if key in paths and key in present+['bng']}
    if forward.get('kind')=='screen':terms['screen']=forward
    if forward.get('kind')=='bright_glow':terms['bright_glow']=forward
    linear_z=bool(bng) or any(key in present for key in VIEWER_LINEAR_Z_TERMS) or bool((paths.get('tint') or {}).get('variant')) or bool((paths.get('leaf_colour') or {}).get('mapped_fresnel'))
    rule=dict(applies=True,schema='rcps4-viewer-graph-rule-v2',
        source='rc2016/viewer/textures.py material_texture_paths',
        g_mode=paths.get('g_mode','colored'),self_lit=paths.get('self_lit','c_alpha'),
        k=float(paths.get('k',0.0) or 0.0),spec=float(paths.get('spec',1.0)),gloss=float(paths.get('gloss',1.0)),
        textures=textures,terms=terms,ported=ported,unported=unported,unported_reasons=reasons,
        normal_z='1-x*x-y*y' if linear_z else 'sqrt(1-x*x-y*y)')
    return json.loads(json.dumps(rule))


def _decode_texture_cached(cache, built_path, built_sha256, stream_data, stream_hash, dds_path):
    """decode_texture(), served from the shared hash-addressed cache when the same built
    and stream bytes were decoded before by the same decoder sources."""
    key=cache.key('dds',built_sha256,stream_hash) if cache is not None and built_sha256 else None
    hit=cache.get_dds(key) if key else None
    if hit:
        report,data=hit
        if report.get('built_sha256')==built_sha256 and report.get('stream_sha256')==stream_hash:
            write(dds_path,data)
            report=dict(report);report['dds_path']=str(dds_path)
            return report
    report=decode_texture(built_path,stream_data,dds_path)
    if key and report.get('built_sha256')==built_sha256:
        cache.put_dds(key,report,read(dds_path))
    return report


def prepare_materials(archive, model_geometry, output_dir, export_textures, log, model_root=None, decode_cache=None):
    output_dir=Path(output_dir)
    # Decoded DDS and transport outcomes are shared by every model and run of this output root.
    decode_cache=decode_cache if decode_cache is not None else TextureDecodeCache(Path(archive.output)/'cache'/'textures')
    material_dir=output_dir/'materials';material_dir.mkdir(parents=True,exist_ok=True)
    model_root=Path(model_root) if model_root is not None else output_dir
    texture_dir=preflight_texture_output(archive.output,model_root,export_textures)
    texture_dir.mkdir(parents=True,exist_ok=True)
    material_results=[];texture_cache={};texture_interpretations={};errors=[];unset_slots=[]
    for source in model_geometry['materials']:
        if not source['path']:
            # The model's own slot record names no material asset (Telepathipus
            # V2 'new_brain'). Keep the polygons on a labelled placeholder and
            # never substitute a similarly named material.
            result=dict(index=source['index'],name=source['name'],path='',source_sha256='',status='unset',
                reason='Model material slot has an empty source path; no material asset is referenced',
                textures=[],shader_status='unset_source_material',game_equivalence=False)
            unset_slots.append(result.copy());material_results.append(result)
            dump(material_dir/(f"{source['index']:03d}_"+source['name'].replace('/','_').replace('\\','_')+'.json'),result)
            continue
        canonical=asset_path(source['path'])
        material_ids=[k for k in archive.resolve(canonical) if archive.entries[k]['type']=='material']
        try:
            material_id=_one(material_ids,'Material '+canonical)
            material_path=archive.decode(material_id);data=read(material_path)
            material_sha256=file_hash(material_path)
            root=read_container(data,0,{})
            instance_parameters=extract_material_parameters(data)
            found=references(data)
            graph_paths=list(dict.fromkeys(r['path'] for r in found if r['path'].endswith('.materialgraph')))
            texture_references={}
            for ref in found:
                if ref['path'].endswith('.texture'):
                    texture_references.setdefault(ref['path'],[]).append(
                        _texture_reference(ref['path'],'material_direct',canonical,ref.get('byte_offset')))
            texture_paths=[r['path'] for r in found if r['path'].endswith('.texture')]
            graph_assets=[];shader_preflights=[];graph_datas=[]
            for graph_path in graph_paths:
                graph_ids=[k for k in archive.resolve(graph_path) if archive.entries[k]['type']=='materialgraph']
                graph_id=_one(graph_ids,'Material graph '+graph_path)
                graph_file=archive.decode(graph_id);graph_data=read(graph_file)
                graph_sha256=file_hash(graph_file);graph_datas.append(graph_data)
                graph_assets.append(dict(path=graph_path,asset_id=graph_id,sha256=graph_sha256))
                shader_preflights.append(preflight_graph(graph_path,graph_data,instance_parameters))
                for ref in references(graph_data):
                    if ref['path'].endswith('.texture'):
                        texture_paths.append(ref['path'])
                        texture_references.setdefault(ref['path'],[]).append(
                            _texture_reference(ref['path'],'materialgraph_reference',graph_path,ref.get('byte_offset')))
            texture_paths=list(dict.fromkeys(texture_paths))
            texture_paths,unbound_texture_references,slot_roles=resolve_texture_slots(
                data,graph_datas,shader_preflights,texture_paths,texture_references)
            from rc2016.shaders.bindings import prepare_translation
            shader_translation=prepare_translation(graph_datas,data,instance_parameters)
            bindings=[];texture_errors=[]
            for texture_path in texture_paths:
                decoded=texture_cache.get(texture_path)
                if decoded is None:
                    ids=archive.resolve(texture_path)
                    if not ids:
                        absent=classify_absent_texture(texture_path,texture_references.get(texture_path,[]),
                            data,graph_datas)
                        if absent['status']=='missing_from_archive':
                            texture_errors.append(absent)
                        else:
                            unbound_texture_references.append(absent)
                        log('texture_absent_from_archive',asset=texture_path,material=canonical,status=absent['status'])
                        continue
                    built=[k for k in ids if archive.entries[k]['built_path'].lower().endswith('.built')]
                    stream=[k for k in ids if archive.entries[k]['built_path'].lower().endswith('.stream')]
                    built_id=_one(built,'Built texture '+texture_path)
                    if len(stream)>1:
                        raise ValueError('Texture stream is ambiguous: '+texture_path)
                    built_path=archive.decode(built_id)
                    stream_data,stream_hash=archive.bytes(stream[0]) if stream else (None,None)
                    dds_path=texture_output_path(texture_dir,texture_path)
                    built_sha256=(getattr(archive,'mapping',{}).get(built_id) or {}).get('decoded_sha256')
                    report=_decode_texture_cached(decode_cache,built_path,built_sha256,stream_data,stream_hash,dds_path)
                else:
                    built_id=decoded['built_asset_id'];stream_id=decoded.get('stream_asset_id')
                    built_path=Path(decoded['built_path'])
                    stream_data,stream_hash=(None,decoded.get('stream_sha256'))
                texture_format=(report if decoded is None else decoded).get('format')
                default_colorspace=native_texture_colorspace(texture_format,texture_role(texture_path))
                colorspace=material_texture_colorspace(canonical,material_sha256,texture_path,
                    report['dds_sha256'] if decoded is None else decoded['dds_sha256'],default_colorspace)
                if decoded is None:
                    binding=texture_binding(texture_path,built_id,stream[0] if stream else None,
                        colorspace,report)
                else:
                    binding=dict(decoded,colorspace=colorspace)
                    binding.pop('colorspace_override',None)
                if colorspace!=default_colorspace:
                    binding['colorspace_override']='captured Ratchet body _g exact material and decoded texture hash'
                binding['alpha_mode']='CHANNEL_PACKED'
                binding['conversion_kind']=('R8G8_UNORM_DDS_TO_RGBA8_PNG_V1' if binding.get('format')==49 else
                    'R8_UNORM_DDS_TO_RGBA8_PNG_V1' if binding.get('format')==61 else 'DDS_NATIVE_V1')
                interpretation_key=(asset_path(binding['path']),binding['dds_sha256'],binding['colorspace'],
                    binding['alpha_mode'],binding['conversion_kind'])
                texture_interpretations[interpretation_key]=dict(path=binding['path'],dds_sha256=binding['dds_sha256'],
                    colorspace=binding['colorspace'],alpha_mode=binding['alpha_mode'],
                    conversion_kind=binding['conversion_kind'])
                if decoded is None:
                    binding['stream_sha256']=stream_hash
                    binding['built_path']=str(built_path)
                    transport=validate_source_transport(binding,archive.entries[built_id],
                        archive.entries[stream[0]] if stream else None,stream_data,read(dds_path),
                        built_asset_id=built_id,stream_asset_id=stream[0] if stream else None,
                        transport_cache=decode_cache)
                    transport['stream_sha256']=stream_hash
                    binding['source_exact_top_mip']=transport['source_exact_top_mip']
                    binding['texture_transport']=transport
                    binding['dimensions']=[report['width'],report['height']]
                    texture_cache[texture_path]=dict(binding)
                else:
                    binding['stream_sha256']=decoded.get('stream_sha256')
                    binding['source_exact_top_mip']=decoded.get('source_exact_top_mip')
                    binding['texture_transport']=decoded.get('texture_transport',{})
                binding=dict(binding,source_references=texture_references.get(texture_path,[]))
                if texture_path in slot_roles:
                    binding['role']=slot_roles[texture_path]['role']
                    binding['slot_roles']=slot_roles[texture_path]['slot_roles']
                bindings.append(binding)
                if decoded is None:
                    log('texture_decoded',asset=texture_path,width=report['width'],height=report['height'],format=report['format'])
            profile=validated_profile(canonical,material_sha256)
            semantics=graph_semantics(graph_paths)
            evidence=material_evidence(profile,semantics,bindings,graph_assets)
            result=dict(index=source['index'],name=source['name'],path=canonical,asset_id=material_id,
                source_sha256=material_sha256,sections=[dict(label=s['label_hash'],size=s['size']) for s in root['sections']],
                instance_parameters=instance_parameters,
                materialgraphs=graph_assets,textures=bindings,graph_semantics=semantics,
                unbound_texture_references=unbound_texture_references,
                texture_errors=texture_errors,
                shader_preflight=shader_preflights,
                shader_translation=shader_translation,
                validated_profile=profile,
                shader_family_status=[row['status'] for row in shader_preflights],
                material_evidence=evidence,game_equivalence=False,
                game_equivalence_reasons=['Compiled shader execution and runtime binding are not established by extracted references or a Blender recipe.'],
                shader_status=shader_build_status(profile,semantics,bindings,shader_preflights))
            bound={binding['path'] for binding in bindings}
            result['viewer_graph_rule']=viewer_graph_rule(archive,canonical,bound)
            result['shader_status']=final_shader_status(result['shader_status'],result['viewer_graph_rule'],bound,
                shader_translation,profile,shader_preflights,graph_paths)
        except Exception as exc:
            result=dict(index=source['index'],name=source['name'],path=canonical,status='failed',reason=str(exc),textures=[])
            errors.append(result.copy())
        material_results.append(result)
        dump(material_dir/(f"{source['index']:03d}_"+source['name'].replace('/','_').replace('\\','_')+'.json'),result)
    # The decode cache can hold textures of a material that failed after decoding
    # them; only textures a built material binds become Blender images.
    bound_paths={binding['path'] for result in material_results for binding in result.get('textures',[])}
    orphaned=[dict(path=row['path'],built_asset_id=row.get('built_asset_id'),dds_sha256=row.get('dds_sha256'),
        reason='Decoded for a material that later failed; no built material binds it, so no Blender image is created.')
        for path,row in texture_cache.items() if path not in bound_paths]
    manifest=dict(schema='rcps4-material-library-v1',materials=material_results,
        game_equivalence=False,
        game_equivalence_reasons=['Material profile validation establishes a reproducible source-hash-bound Blender recipe, not game shader equivalence.'],
        textures=[row for path,row in texture_cache.items() if path in bound_paths],
        orphaned_decoded_textures=orphaned,
        texture_interpretations=[row for row in texture_interpretations.values() if row['path'] in bound_paths],
        errors=errors,unset_material_slots=unset_slots,external_texture_export=bool(export_textures),
        shader_limitations=['Compiled materialgraph execution is not decoded; recognized families and validated recipes are Blender reconstructions.',
            'Generic materials expose source channels conservatively; model-specific tuning is applied only by an exact source-path and SHA-256 profile.'])
    dump(material_dir/'manifest.json',manifest)
    return manifest
