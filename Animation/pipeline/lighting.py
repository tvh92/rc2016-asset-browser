"""Discover source lighting grids and HDR volume-reflection resources without guessing their runtime encoding."""
from collections import defaultdict
from itertools import groupby
import math
from pathlib import Path
import re
import struct

from Animation.animation_audit import read_container
from .common import asset_path, dump, file_hash, read, write
from .texture import dds_bytes, parse_header, ps4_deswizzle_top, top_mip_size

LIGHTGRID_DATA = 0x9989BB49
LIGHTGRID_REFERENCES = 0x96D77BBD
GRID_PATTERN = re.compile(r'^(.*?)(?:/lightgrids)?/grid_(-?\d+)_(-?\d+)_(-?\d+)\.lightgrid$')
REFLECTION_PREFIX = 'textures/lighting/volumereflections/'
ENVPROBE_PREFIX = 'levels/novalis/openarea/openworld/default_region/envprobes/'
NOVALIS_REGION = 'levels/novalis/openarea/openworld/day'
NOVALIS_PROBE = 'textures/lighting/volumereflections/0xad76696216314d22/reflection.texture'
NOVALIS_LIGHTING_ZONE = 'levels/novalis/openarea/openworld/default_region/lighting.zone'
NOVALIS_ATMOSPHERE = 'atmosphere/atm_novalis/atm_novalis.atmosphere'
ZONE_GRID_PATHS = 0x3E36401D
ZONE_GRID_COORDINATES = 0xD6E15BC0
ATMOSPHERE_HEADER = 0x02F06D4E
ATMOSPHERE_LOOKUP_DATA = 0xE7997256


def level_region(path):
    match=GRID_PATTERN.match(path)
    return match.group(1) if match else None


def grid_coordinates(path):
    match=GRID_PATTERN.match(path)
    return tuple(map(int,match.groups()[1:])) if match else None


def _steps(values):
    values=sorted(set(values))
    differences=[b-a for a,b in zip(values,values[1:]) if b>a]
    return math.gcd(*differences) if differences else None


def _occurrences(entry):
    return [row for row in entry['occurrences'] if row['available']]


def _ref_hashes(entry):
    return sorted({row.get('ref_hash','').lower() for row in _occurrences(entry) if row.get('ref_hash')})


def _representative_grid(archive, entry):
    path=archive.decode(entry['id']);data=read(path);root=read_container(data,0,{})
    sections=[]
    for section in root['sections']:
        blob=data[section['offset']:section['offset']+section['size']]
        item=dict(label=section['label_hash'],size=section['size'])
        label=int(section['label_hash'],16)
        if label==LIGHTGRID_DATA:
            item.update(u32_values=len(blob)//4,
                structural_candidate='4096 records x 6 packed uint32 values' if len(blob)==16*16*16*24 else 'unknown packed record layout')
        elif label==LIGHTGRID_REFERENCES:
            item['u32_values']=list(struct.unpack('<%dI'%(len(blob)//4),blob))
        sections.append(item)
    return dict(path=entry['path'],decoded_sha256=file_hash(path),declared_size=root['declared_size'],sections=sections)


def _lightgrid_probe_assignment(archive, entry):
    """Decode the lightgrid's local-probe string table and selector channel.

    Each Novalis day lightgrid has a small 0x96D77BBD table of byte offsets into
    the DAT1 string pool.  The 0x9989BB49 payload is 4096 24-byte records.  In
    the records audited so far, bytes 20 and 22 are probe-table indices, byte
    23 is 255, and byte 21 varies from 0 to 255.  Equal indices represent one
    probe; unequal indices preserve a two-probe pair with a blend byte.
    Boundary cells support interpreting G/255 as weight toward the first
    listed probe, but keep that as a hypothesis until a runtime sampler
    confirms it.  The other packed coefficient fields remain unresolved.
    """
    data=read(archive.decode(entry['id'])); root=read_container(data,0,{})
    sections={s['label_hash']:data[s['offset']:s['offset']+s['size']] for s in root['sections']}
    refs_blob=sections.get('0x96D77BBD'); records_blob=sections.get('0x9989BB49')
    if refs_blob is None or records_blob is None or len(refs_blob)%4 or len(records_blob)%24:
        raise ValueError('Novalis lightgrid lacks the expected reference/data sections')
    refs=struct.unpack('<%dI'%(len(refs_blob)//4),refs_blob)
    paths=[]
    for offset in refs:
        if not 0 <= offset < len(data): raise ValueError('Lightgrid probe string offset is outside the file')
        end=data.find(b'\0',offset)
        if end<0: raise ValueError('Lightgrid probe string is unterminated')
        text=data[offset:end].decode('utf-8','replace')
        normalized=asset_path(text)
        if not normalized.startswith(ENVPROBE_PREFIX):
            raise ValueError('Lightgrid probe table contains a non-Novalis environment path: '+text)
        paths.append(normalized)
    pair_counts=defaultdict(int); single_counts=defaultdict(int); blend_values=[]; reverse_pairs=0; invalid=0; valid=0
    for offset in range(0,len(records_blob),24):
        r,g,b,a=records_blob[offset+20:offset+24]
        if a==255 and r<len(paths) and b<len(paths):
            pair_counts[(int(r),int(b))]+=1
            if r==b: single_counts[int(r)]+=1
            else:
                blend_values.append(int(g))
                if r>b: reverse_pairs+=1
            valid+=1
        else:
            invalid+=1
    record_count=len(records_blob)//24
    dominant=max(pair_counts.items(),key=lambda item:(item[1],-item[0][0],-item[0][1]),default=((None,None),0))
    dominant_probe=paths[dominant[0][0]] if dominant[0][0] is not None and dominant[0][0]==dominant[0][1] else None
    return dict(path=entry['path'],coordinates=grid_coordinates(entry['path']),record_count=record_count,
        probe_paths=paths,probe_selector=dict(channel='record_bytes_20_21_22_23',
            evidence='R/B are in-table indices, A==255; unequal pairs are ordered R<=B and G is a per-record blend byte; boundary cells support G/255 weight toward the first index (runtime confirmation pending)',
            pair_counts={f'{a}:{b}':v for (a,b),v in sorted(pair_counts.items())},
            single_probe_counts={str(k):v for k,v in sorted(single_counts.items())},valid_records=valid,unresolved_records=invalid,
            valid_fraction=valid/record_count,dominant_pair=list(dominant[0]),dominant_probe=dominant_probe,
            dominant_records=dominant[1],dominant_fraction=dominant[1]/record_count,
            unequal_pair_records=len(blend_values),reverse_pair_records=reverse_pairs,
            blend_byte_min=min(blend_values) if blend_values else None,blend_byte_max=max(blend_values) if blend_values else None),status='PROBE_STRING_TABLE_AND_PAIR_SELECTOR_DECODED_COEFFICIENTS_PENDING')


def _decode_lighting_zone_grid_map(data, resolve):
    """Decode the zone's exact grid path, coordinate, and ordinal association."""
    root=read_container(data,0,{})
    sections={int(section['label_hash'],16):data[section['offset']:section['offset']+section['size']]
        for section in root['sections']}
    paths_blob=sections.get(ZONE_GRID_PATHS); coordinates_blob=sections.get(ZONE_GRID_COORDINATES)
    if paths_blob is None or coordinates_blob is None or len(paths_blob)%4 or len(coordinates_blob)%16:
        raise ValueError('Novalis lighting zone lacks the expected grid path/coordinate tables')
    path_offsets=struct.unpack('<%dI'%(len(paths_blob)//4),paths_blob)
    if len(path_offsets)!=len(coordinates_blob)//16:
        raise ValueError('Novalis lighting zone grid tables have different lengths')
    grids=[]
    for index,string_offset in enumerate(path_offsets):
        if not 0 <= string_offset < len(data):
            raise ValueError('Novalis lighting zone grid string offset is outside the file')
        end=data.find(b'\0',string_offset)
        if end<0: raise ValueError('Novalis lighting zone grid string is unterminated')
        path=asset_path(data[string_offset:end].decode('utf-8','strict'))
        path_coordinates=grid_coordinates(path)
        if not path.startswith(NOVALIS_REGION+'/') or path_coordinates is None:
            raise ValueError('Novalis lighting zone table contains a non-day-grid path: '+path)
        x,y,z,ordinal=struct.unpack_from('<3fI',coordinates_blob,index*16)
        if ordinal!=index or not all(math.isfinite(v) and v.is_integer() for v in (x,y,z)):
            raise ValueError('Novalis lighting zone contains a non-integral coordinate or broken ordinal')
        coordinates=(int(x),int(y),int(z))
        if coordinates!=path_coordinates:
            raise ValueError('Novalis lighting zone path and coordinate record disagree: '+path)
        asset_ids=resolve(path)
        if len(asset_ids)!=1:
            raise ValueError('Novalis lighting zone grid path does not resolve uniquely: '+path)
        grids.append(dict(index=index,path=path,coordinates=list(coordinates),asset_id=asset_ids[0],string_offset=string_offset))
    if len({row['path'] for row in grids})!=len(grids) or len({tuple(row['coordinates']) for row in grids})!=len(grids):
        raise ValueError('Novalis lighting zone grid table contains duplicate paths or coordinates')
    coordinates=[tuple(row['coordinates']) for row in grids]
    return dict(status='GRID_PATH_COORDINATE_AND_INDEX_TABLE_DECODED_DIRECT_LIGHTS_PENDING',grid_count=len(grids),
        coordinate_bounds=dict(min=[min(c[i] for c in coordinates) for i in range(3)],
            max=[max(c[i] for c in coordinates) for i in range(3)],
            coordinate_steps=[_steps(c[i] for c in coordinates) for i in range(3)]),grids=grids)


def _lighting_zone_grid_map(archive, entry):
    decoded=archive.decode(entry['id'])
    result=_decode_lighting_zone_grid_map(read(decoded),archive.resolve)
    result.update(schema='rcps4-novalis-lighting-zone-grid-map-v1',zone_path=entry['path'],
        zone_asset_id=entry['id'],source_sha256=archive.mapping[entry['id']]['source_sha256'],
        decoded_sha256=file_hash(decoded))
    return result


def _decode_ps4_atmosphere_header(data):
    """Decode fields established for the direct-DAT1 PS4/Sunset atmosphere layout."""
    root=read_container(data,0,{})
    if struct.unpack_from('<I',data,4)[0]!=0xDC85AF48:
        raise ValueError('Atmosphere DAT1 has an unexpected PS4 asset type')
    sections={int(section['label_hash'],16):data[section['offset']:section['offset']+section['size']]
        for section in root['sections']}
    header=sections.get(ATMOSPHERE_HEADER);lookup=sections.get(ATMOSPHERE_LOOKUP_DATA)
    if header is None or len(header)!=131552 or lookup is None or len(lookup)!=2244624:
        raise ValueError('Atmosphere does not match the PS4/Sunset-era two-section layout')
    direction=struct.unpack_from('<3f',header,72);length=math.sqrt(sum(value*value for value in direction))
    if not all(math.isfinite(value) for value in direction) or abs(length-1)>1e-5:
        raise ValueError('Atmosphere direction field is not a unit vector')
    # Native model registration: game X,Y,Z maps to Blender X,-Z,Y.
    blender_ray=(direction[0],-direction[2],direction[1])
    scalar=struct.unpack_from('<f',header,84)[0]
    direct_rgb=list(struct.unpack_from('<3f',header,96))
    disk_colour=list(struct.unpack_from('<3f',header,136))
    radius=struct.unpack_from('<f',header,148)[0]
    float_156=struct.unpack_from('<f',header,156)[0]
    return dict(status='SUN_RAY_DIRECTION_DECODED_SURFACE_RADIANCE_CANDIDATE',
        layout='PS4/Sunset-era direct DAT1; 131552-byte header plus 2244624-byte lookup payload',
        container_type='0xDC85AF48',sun_ray_direction_game_xyz=list(direction),
        sun_ray_direction_blender_xyz=list(blender_ray),sun_direction_length=length,
        atmosphere_sun_candidate=dict(status='DIRECTION_CONFIRMED_RGB96_DIAGNOSTIC_RUNTIME_NAMES_PENDING',
            ray_direction_game_xyz=list(direction),ray_direction_blender_xyz=list(blender_ray),
            surface_irradiance_rgb_candidate=direct_rgb,disk_colour_rgb_candidate=disk_colour,
            angular_radius_radians=radius,
            rejected_strength_candidates=dict(float_84=scalar,float_156=float_156),
            evidence='The unit ray is exact. RGB96 changes sharply between matched day/night atmospheres and is retained as the direct surface-light RGB candidate. Cross-level pairs reject float84 as strength, and no radiometric multiplier role is established for float156. RGB136 plus radius may control the visible disk rather than surface irradiance.'),
        unresolved_candidate_fields=dict(float_84=scalar,
            rgb_96=direct_rgb,rgb_108=list(struct.unpack_from('<3f',header,108)),
            rgb_120=list(struct.unpack_from('<3f',header,120)),u32_132=struct.unpack_from('<I',header,132)[0],
            rgb_136=disk_colour,float_148=radius,float_152=struct.unpack_from('<f',header,152)[0],
            float_156=float_156,float_160=struct.unpack_from('<f',header,160)[0],
            float_164=struct.unpack_from('<f',header,164)[0]))


def _novalis_atmosphere(archive,entry):
    decoded=archive.decode(entry['id']);result=_decode_ps4_atmosphere_header(read(decoded))
    result.update(schema='rcps4-novalis-atmosphere-header-v1',source_path=entry['path'],asset_id=entry['id'],
        source_sha256=archive.mapping[entry['id']]['source_sha256'],decoded_sha256=file_hash(decoded),
        cross_asset_evidence='The same offset is unit length within 5e-8 in all 38 PS4 atmosphere assets; Y values match authored degree elevations. The adjacent 150-to-1000 scalar remains unchanged in several day/night or visual-mode pairs and is not direct surface-light strength. RGB96 changes sharply across matched day/night atmospheres and is retained as a source-valued surface-light candidate without float84 or float156 multiplication. Outdoor sun-radius candidates include 0.001 to 0.063 radians.')
    return result


def inventory_lighting(archive, output_path, log):
    grids=[];by_ref=defaultdict(list)
    for entry in archive.entries.values():
        if entry['type']!='lightgrid':continue
        coord=grid_coordinates(entry['path']);region=level_region(entry['path'])
        record=dict(asset_id=entry['id'],path=entry['path'],region=region,coordinates=coord,ref_hashes=_ref_hashes(entry))
        grids.append(record)
        for ref_hash in record['ref_hashes']:by_ref[ref_hash].append(record)

    regions=[]
    for region,items in groupby(sorted(grids,key=lambda x:x['region'] or ''),lambda x:x['region']):
        rows=list(items);coords=[x['coordinates'] for x in rows if x['coordinates']]
        representative=next((archive.entries[x['asset_id']] for x in rows if x['coordinates']),archive.entries[rows[0]['asset_id']])
        bounds=None
        if coords:
            bounds=dict(min=[min(c[i] for c in coords) for i in range(3)],max=[max(c[i] for c in coords) for i in range(3)],
                coordinate_steps=[_steps(c[i] for c in coords) for i in range(3)])
        regions.append(dict(region=region,tile_count=len(rows),coordinate_bounds=bounds,
            ref_hashes=sorted({h for row in rows for h in row['ref_hashes']}),representative=_representative_grid(archive,representative)))

    reflections=[]
    for entry in archive.entries.values():
        if entry['type']!='texture' or not entry['path'].startswith(REFLECTION_PREFIX):continue
        if not entry['built_path'].lower().endswith('.built'):continue
        decoded=archive.decode(entry['id']);info=parse_header(read(decoded));width=info['sd_width'];height=info['sd_height']
        top=top_mip_size(width,height,info['format'])
        refs=_ref_hashes(entry);linked=sorted({grid['region'] for ref_hash in refs for grid in by_ref.get(ref_hash,[]) if grid['region']})
        reflections.append(dict(asset_id=entry['id'],path=entry['path'],source_sha256=archive.mapping[entry['id']]['source_sha256'],
            decoded_sha256=file_hash(decoded),ref_hashes=refs,linked_regions=linked,width=width,height=height,
            dxgi_format=info['format'],stex_format=info['stex_format'],array_size=info['array_size'],planes=info['planes'],
            payload_bytes=info['built_size'],single_2d_top_surface_bytes=top,
            layout_status='UNSUPPORTED_PACKED_MULTI_SURFACE' if info['built_size']>top else 'ordinary_2d_top_surface'))

    local_envprobes=[]
    for entry in archive.entries.values():
        if entry['type']!='texture' or not entry['path'].startswith(ENVPROBE_PREFIX): continue
        decoded=archive.decode(entry['id']);info=parse_header(read(decoded))
        local_envprobes.append(dict(asset_id=entry['id'],path=entry['path'],source_sha256=archive.mapping[entry['id']]['source_sha256'],
            decoded_sha256=file_hash(decoded),width=info['sd_width'],height=info['sd_height'],dxgi_format=info['format'],
            stex_format=info['stex_format'],array_size=info['array_size'],planes=info['planes'],payload_bytes=info['built_size'],
            layout_status='ordinary_2d_top_surface' if info['built_size']==top_mip_size(info['sd_width'],info['sd_height'],info['format']) else 'additional_mips_or_stream_data'))

    assignments=[]
    day_grid_count=0
    tableless_day_grid_count=0
    tableless_day_grid_records=0
    for entry in archive.entries.values():
        if entry['type']!='lightgrid' or not entry.get('path','').startswith(NOVALIS_REGION+'/'): continue
        day_grid_count += 1
        try: assignments.append(_lightgrid_probe_assignment(archive,entry))
        except Exception as exc:
            # A fixed-size 0x9989BB49 block without the string table is a
            # deliberate tableless/sentinel tile in Novalis (all selector
            # bytes are 0xFF), not a malformed asset. Keep it counted so the
            # report distinguishes absent local probes from decoded ones.
            data = read(archive.decode(entry['id']))
            root = read_container(data,0,{})
            has_probe_table = any(s['label_hash']=='0x96D77BBD' for s in root['sections'])
            data_section = next((s for s in root['sections'] if s['label_hash']=='0x9989BB49'),None)
            if not has_probe_table and data_section is not None and data_section['size'] % 24 == 0:
                tableless_day_grid_count += 1
                tableless_day_grid_records += data_section['size']//24
            log('warning',message='Lightgrid probe assignment skipped: '+entry.get('path','')+' | '+str(exc))
    assignment_report=output_path.parent/'novalis_lightgrid_probe_assignments.json'
    total_records=sum(row['record_count'] for row in assignments)
    valid_records=sum(row['probe_selector']['valid_records'] for row in assignments)
    unequal_records=sum(row['probe_selector']['unequal_pair_records'] for row in assignments)
    reverse_records=sum(row['probe_selector']['reverse_pair_records'] for row in assignments)
    blend_mins=[row['probe_selector']['blend_byte_min'] for row in assignments if row['probe_selector']['blend_byte_min'] is not None]
    blend_maxs=[row['probe_selector']['blend_byte_max'] for row in assignments if row['probe_selector']['blend_byte_max'] is not None]
    aggregate=dict(grid_count=len(assignments),total_day_grid_count=day_grid_count,
        tableless_day_grid_count=tableless_day_grid_count,tableless_day_grid_records=tableless_day_grid_records,
        total_selector_records=total_records+tableless_day_grid_records,
        record_count=total_records,valid_records=valid_records,
        unresolved_records=total_records-valid_records,unequal_pair_records=unequal_records,
        reverse_pair_records=reverse_records,blend_byte_min=min(blend_mins) if blend_mins else None,
        blend_byte_max=max(blend_maxs) if blend_maxs else None)
    dump(assignment_report,dict(schema='rcps4-novalis-lightgrid-probe-assignments-v1',status='PROBE_STRING_TABLE_AND_PAIR_SELECTOR_DECODED_COEFFICIENTS_PENDING',aggregate=aggregate,grids=assignments))
    zone_ids=archive.resolve(NOVALIS_LIGHTING_ZONE)
    if len(zone_ids)==1:
        zone_map=_lighting_zone_grid_map(archive,archive.entries[zone_ids[0]])
        zone_report=output_path.parent/'novalis_lighting_zone_grid_map.json'
        dump(zone_report,zone_map)
        zone_summary=dict(report=str(zone_report.resolve()),status=zone_map['status'],grid_count=zone_map['grid_count'],
            coordinate_bounds=zone_map['coordinate_bounds'])
    else:
        zone_summary=dict(status='ZONE_ASSET_MISSING_OR_AMBIGUOUS',grid_count=0)
        log('warning',message='Novalis lighting zone path did not resolve uniquely: '+NOVALIS_LIGHTING_ZONE)
    atmosphere_ids=archive.resolve(NOVALIS_ATMOSPHERE)
    if len(atmosphere_ids)==1:
        atmosphere=_novalis_atmosphere(archive,archive.entries[atmosphere_ids[0]])
        atmosphere_report=output_path.parent/'novalis_atmosphere_header.json';dump(atmosphere_report,atmosphere)
        atmosphere['report']=str(atmosphere_report.resolve())
    else:
        atmosphere=dict(status='ATMOSPHERE_ASSET_MISSING_OR_AMBIGUOUS')
        log('warning',message='Novalis atmosphere path did not resolve uniquely: '+NOVALIS_ATMOSPHERE)
    result=dict(schema='rcps4-lighting-inventory-v1',status='DISCOVERED_NOT_CONVERTED',
        source='Archive layout RefHash association plus decoded DAT1 headers',lightgrid_assets=len(grids),regions=regions,
        volume_reflections=reflections,local_environment_probes=local_envprobes,
        novalis_lightgrid_probe_assignments=dict(report=str(assignment_report.resolve()),**aggregate,
            selector_status='PROBE_STRING_TABLE_AND_PAIR_SELECTOR_DECODED_COEFFICIENTS_PENDING'),
        novalis_lighting_zone_grid_map=zone_summary,novalis_atmosphere=atmosphere,limitations=[
            'Light-grid packed coefficients and volume-reflection multi-surface layout are not decoded.',
            'The Novalis atmosphere supplies an exact sun-ray direction. RGB96 is retained as a source-valued surface-light candidate because it changes sharply between matched day/night atmospheres; float84 and float156 are not used as strength multipliers. Runtime field names still need confirmation.',
            'Novalis day lightgrids expose their local environment-probe string tables and per-record two-probe selector pairs; the zone now supplies an exact 727-entry grid path/coordinate/index map, while blend direction, irradiance coefficients, and a model asset world position still need runtime confirmation.',
            'The validated global probe can be connected to Blender, but complete position-dependent game lighting still requires runtime association and scale data.'
        ])
    dump(output_path,result);log('lighting_inventory',lightgrids=len(grids),regions=len(regions),volume_reflections=len(reflections),local_environment_probes=len(local_envprobes),status=result['status'])
    return result


def prepare_game_lighting(archive, inventory, output_root, region, log, atmosphere_sun=False):
    """Extract the six full-resolution PS4 cubemap faces for one deterministic level probe."""
    candidates=[item for item in inventory['volume_reflections'] if region in item['linked_regions']]
    if not candidates:raise ValueError('No volume reflection is associated with lighting region: '+region)
    candidates.sort(key=lambda item:archive.entries[item['asset_id']]['order'])
    selected=next((item for item in candidates if item['path']==NOVALIS_PROBE),None)
    if not selected:raise ValueError('Validated Novalis reflection probe is absent: '+NOVALIS_PROBE)
    entry=archive.entries[selected['asset_id']]
    decoded=archive.decode(entry['id']);info=parse_header(read(decoded))
    if (info['format'],info['stex_format'],info['sd_width'],info['sd_height'])!=(95,64,512,512):
        raise ValueError('Novalis probe does not match the validated BC6H/STEX64 512x512 layout')
    payload=info['built_payload'];surface_size=top_mip_size(512,512,95)
    if len(payload)<6*surface_size:raise ValueError('Novalis probe lacks six complete top-level surfaces')
    folder=output_root/'lighting'/'novalis'/Path(entry['path']).parent.name/'faces';folder.mkdir(parents=True,exist_ok=True)
    faces=[]
    for index in range(6):
        tiled=payload[index*surface_size:(index+1)*surface_size]
        linear=ps4_deswizzle_top(tiled,512,512,95);path=folder/f'face_{index:02d}.dds'
        write(path,dds_bytes(512,512,95,linear));faces.append(dict(index=index,path=str(path.resolve()),sha256=file_hash(path)))
    result=dict(schema='rcps4-game-lighting-v1',status='EXTRACTED_PENDING_BLENDER_EDGE_VALIDATION',region=region,
        source_path=entry['path'],source_asset_id=entry['id'],source_sha256=archive.mapping[entry['id']]['source_sha256'],
        decoded_sha256=file_hash(decoded),width=512,height=512,dxgi_format=95,stex_format=64,faces=faces,
        candidate_count=len(candidates),selection='validated RefHash-linked Novalis probe; source identity fixed by path',
        remaining_payload_status='Lower mips/padding retained in exact decoded source; not interpreted as additional top-level faces')
    if atmosphere_sun:
        atmosphere=inventory.get('novalis_atmosphere',{})
        candidate=atmosphere.get('atmosphere_sun_candidate',{})
        if candidate.get('status')!='DIRECTION_CONFIRMED_RGB96_DIAGNOSTIC_RUNTIME_NAMES_PENDING':
            raise ValueError('Novalis RGB96 atmosphere Sun candidate is unavailable')
        rgb=[float(value) for value in candidate['surface_irradiance_rgb_candidate']]
        ray=[float(value) for value in candidate['ray_direction_blender_xyz']]
        angle=float(candidate['angular_radius_radians'])
        if not all(math.isfinite(value) for value in rgb+ray+[angle]) or max(rgb)<=0 or angle<=0:
            raise ValueError('Novalis atmosphere Sun candidate contains invalid values')
        result['atmosphere_sun']=dict(mode='rgb96_direct',status='SOURCE_VALUES_RUNTIME_FIELD_NAMES_PENDING',
            source_path=atmosphere['source_path'],source_sha256=atmosphere['source_sha256'],
            ray_direction_game_xyz=candidate['ray_direction_game_xyz'],ray_direction_blender_xyz=ray,
            surface_irradiance_rgb=rgb,angular_radius_radians=angle,
            mapping='Blender Sun color normalized by max(RGB96), energy=max(RGB96), reproducing RGB96 exactly; no exposure or float84/float156 multiplier')
    report=output_root/'reports'/'selected_game_lighting.json';dump(report,result)
    log('game_lighting_extracted',region=region,probe=entry['path'],faces=6,status=result['status'])
    return result
