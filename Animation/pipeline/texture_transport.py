"""Source-to-DDS texture transport checks; this makes no shader-semantic claims."""
import hashlib
import struct

from .archive import lz4_block
from .common import asset_path, sha
from .texture import (BLOCK_BYTES, UNCOMPRESSED_BPP, parse_header,
    ps4_deswizzle_top, read, top_mip_size)


def image_interpretation_identity(binding):
    """Return the stable Blender-image identity, separate from DDS byte identity."""
    fmt=binding.get('format')
    conversion=(binding.get('conversion_kind') or
        ('R8G8_UNORM_DDS_TO_RGBA8_PNG_V1' if fmt==49 else
         'R8_UNORM_DDS_TO_RGBA8_PNG_V1' if fmt==61 else 'DDS_NATIVE_V1'))
    return dict(source_path=asset_path(binding.get('source_path',binding.get('path',''))),
        dds_sha256=str(binding.get('dds_sha256','')).lower(),
        colorspace=binding.get('colorspace','sRGB'),
        alpha_mode=binding.get('alpha_mode','CHANNEL_PACKED'),
        conversion=conversion)


def image_interpretation_key(binding):
    identity=image_interpretation_identity(binding)
    return (identity['source_path'],identity['dds_sha256'],identity['colorspace'],
        identity['alpha_mode'],identity['conversion'])


def _occurrence_summary(entry):
    return [dict(index=index,archive=o.get('archive'), offset=o.get('offset'), size=o.get('size'),
                 available=bool(o.get('available'))) for index,o in enumerate(entry.get('occurrences', []))]


def _limited(binding, reason, stream_sha=None):
    return dict(source_path=binding['path'], built_asset_id=binding.get('built_asset_id'),
        stream_asset_id=binding.get('stream_asset_id'), status='limited', source_exact_top_mip=None,
        reason=reason, stream_sha256=stream_sha)


def validate_source_transport(binding, built_entry, stream_entry, stream_bytes, dds_bytes,
                              built_asset_id=None, stream_asset_id=None):
    """Compare indexed source identity, LZ4 output, deswizzled top mip and DDS payload."""
    built_asset_id=built_asset_id or binding.get('built_asset_id')
    stream_asset_id=stream_asset_id if stream_asset_id is not None else binding.get('stream_asset_id')
    result=dict(source_path=binding['path'],built_asset_id=built_asset_id,
        stream_asset_id=stream_asset_id,stream_occurrences=_occurrence_summary(stream_entry) if stream_entry else [])
    if built_entry is None or built_asset_id is None or built_entry.get('id') not in (None,built_asset_id):
        result.update(status='failed',source_exact_top_mip=False,reason='built archive identity is missing or inconsistent')
        return result
    if asset_path(built_entry.get('path','')) != asset_path(binding['path']):
        result.update(status='failed',source_exact_top_mip=False,reason='built canonical source path mismatch')
        return result
    if stream_entry is None:
        result.update(status='limited',source_exact_top_mip=None,reason='no HD stream indexed')
        return result
    if stream_asset_id is None or stream_entry.get('id') not in (None,stream_asset_id):
        result.update(status='failed',source_exact_top_mip=False,reason='stream entry is present but binding lacks its archive identity')
        return result
    if asset_path(stream_entry.get('path','')) != asset_path(binding['path']):
        result.update(status='failed',source_exact_top_mip=False,reason='stream archive identity or canonical path mismatch')
        return result
    if not stream_bytes:
        return _limited(binding,'HD stream has no available archive bytes')
    stream_hash=sha(stream_bytes)
    if binding.get('stream_sha256') not in (None,stream_hash):
        result.update(status='failed',source_exact_top_mip=False,reason='archive stream bytes differ from the hash recorded by the decode path')
        return result
    result['stream_sha256']=stream_hash
    occurrences=stream_entry.get('occurrences',[])
    if not occurrences or not any(o.get('available') for o in occurrences):
        return _limited(binding,'HD stream archive range is unavailable',stream_hash)
    if any(not o.get('available') for o in occurrences):
        result.update(status='failed',source_exact_top_mip=False,reason='one or more indexed stream occurrences are out of archive bounds')
        return result
    sizes={o.get('size') for o in occurrences}
    if len(sizes)!=1 or next(iter(sizes))!=len(stream_bytes):
        result.update(status='failed',source_exact_top_mip=False,reason='stream occurrence bounds disagree with extracted stream length')
        return result
    try:
        built_data=read(binding['built_path'])
        info=parse_header(built_data)
    except Exception as exc:
        result.update(status='failed',source_exact_top_mip=False,reason='built texture header could not be validated: '+str(exc))
        return result
    fmt=info['format'];width=info['hd_width'];height=info['hd_height']
    result.update(format=fmt,width=width,height=height,stream_size=info['stream_size'],
        sd_mip_count=info['sd_mip_count'],hd_mip_count=info['hd_mip_count'],
        sd_mip_count_offset=info['sd_mip_count_offset'],hd_mip_count_offset=info['hd_mip_count_offset'],
        built_sha256=sha(built_data),
        stream_occurrences=_occurrence_summary(stream_entry))
    if fmt not in BLOCK_BYTES and fmt not in UNCOMPRESSED_BPP:
        return _limited(binding,f'format {fmt} is outside top-mip byte comparison support',stream_hash)
    if not width or not height or not info['stream_size']:
        return _limited(binding,'built header has no supported HD stream dimensions/size',stream_hash)
    if len(stream_bytes)==info['stream_size']:
        expanded=stream_bytes
        stream_encoding='raw'
    else:
        try:
            expanded=lz4_block(stream_bytes,info['stream_size'])
            stream_encoding='lz4_block'
        except Exception as exc:
            result.update(status='failed',source_exact_top_mip=False,reason='HD stream LZ4 decode failed: '+str(exc))
            return result
    if len(expanded)!=info['stream_size']:
        result.update(status='failed',source_exact_top_mip=False,reason='HD stream LZ4 output size disagrees with built header')
        return result
    size=top_mip_size(width,height,fmt)
    if len(expanded)<size:
        result.update(status='failed',source_exact_top_mip=False,reason='expanded HD stream is smaller than its top mip')
        return result
    try:
        expected=ps4_deswizzle_top(expanded[:size],width,height,fmt)
    except Exception as exc:
        result.update(status='failed',source_exact_top_mip=False,reason='HD top mip deswizzle failed: '+str(exc))
        return result
    if len(dds_bytes)<148 or dds_bytes[:4]!=b'DDS ' or dds_bytes[84:88]!=b'DX10':
        result.update(status='failed',source_exact_top_mip=False,reason='pipeline DDS header is not a DX10 DDS')
        return result
    dds_height,dds_width=struct.unpack_from('<II',dds_bytes,12)
    dds_format=struct.unpack_from('<I',dds_bytes,128)[0]
    actual=dds_bytes[148:148+size]
    result.update(dds_width=dds_width,dds_height=dds_height,dds_format=dds_format,
        stream_encoding=stream_encoding,expanded_stream_size=len(expanded),top_mip_bytes=size,
        source_top_mip_sha256=sha(expected),pipeline_top_mip_sha256=sha(actual))
    if (dds_width,dds_height,dds_format)!=(width,height,fmt) or len(actual)!=size or actual!=expected:
        result.update(status='failed',source_exact_top_mip=False,reason='deswizzled HD top mip differs from pipeline DDS payload/header')
    else:
        result.update(status='passed',source_exact_top_mip=True,reason='supported compressed or uncompressed top mip is byte-identical to pipeline DDS')
    return result


def validate_reopened_texture_bindings(expected, images):
    """Check packed bytes and Blender image interpretation after a fresh reopen."""
    report=[]
    for binding in expected:
        row=dict(source_path=binding['source_path'],status=binding.get('status','limited'),
            source_exact_top_mip=binding.get('source_exact_top_mip'),reason=binding.get('reason',''),
            built_asset_id=binding.get('built_asset_id'),stream_asset_id=binding.get('stream_asset_id'),
            dds_sha256=binding.get('dds_sha256'),format=binding.get('format'),
            dimensions=binding.get('dimensions'),source_evidence=binding.get('source_evidence',{}))
        identity=image_interpretation_identity(binding)
        row.update(image_identity=identity)
        candidates=[]
        for candidate in images:
            try:
                candidate_identity=dict(source_path=asset_path(candidate.get('source_texture_path') or ''),
                    dds_sha256=str(candidate.get('source_texture_sha256') or '').lower(),
                    colorspace=candidate.colorspace_settings.name,
                    alpha_mode=candidate.alpha_mode,
                    conversion=candidate.get('blender_conversion') or 'DDS_NATIVE_V1')
            except (ValueError,AttributeError):
                continue
            if candidate_identity==identity:candidates.append(candidate)
        if len(candidates)!=1:
            row.update(status='failed',source_exact_top_mip=False,reopened=False,
                reason=f'expected one reopened source image, found {len(candidates)}')
            report.append(row);continue
        image=candidates[0]
        try:
            packed=bytes(image.packed_file.data)
            packed_sha=hashlib.sha256(packed).hexdigest()
            expected_sha=binding.get('dds_sha256')
            image_sha=expected_sha
            image_size=binding.get('packed_dimensions',binding.get('dimensions',()))
            conversion_ok=True
            if binding.get('format')==49:
                image_sha=image.get('blender_packed_sha256')
                expected_size=binding.get('packed_dimensions',binding.get('dimensions',()))
                image_size=expected_size
                conversion_ok=(image.get('blender_conversion')=='R8G8_UNORM_DDS_TO_RGBA8_PNG_V1'
                    and image.get('packed_container')=='PNG with exact R/G and shader-reconstructed normal Z from R8G8_UNORM DDS'
                    and image.get('source_texture_sha256')==expected_sha
                    and tuple(image.get('blender_dimensions',()))==tuple(expected_size)
                    and packed.startswith(b'\x89PNG\r\n\x1a\n')
                    and image_sha is not None)
            elif binding.get('format')==61:
                image_sha=image.get('blender_packed_sha256')
                expected_size=binding.get('packed_dimensions',binding.get('dimensions',()))
                image_size=expected_size
                conversion_ok=(image.get('blender_conversion')=='R8_UNORM_DDS_TO_RGBA8_PNG_V1'
                    and image.get('packed_container')=='PNG with exact R8_UNORM scalar replicated to RGB'
                    and image.get('source_texture_sha256')==expected_sha
                    and tuple(image.get('blender_dimensions',()))==tuple(expected_size)
                    and packed.startswith(b'\x89PNG\r\n\x1a\n') and image_sha is not None)
            settings=(tuple(image.size)==tuple(image_size) and
                image.alpha_mode==identity['alpha_mode'] and
                image.colorspace_settings.name==binding.get('colorspace') and packed_sha==image_sha
                and conversion_ok)
            row.update(reopened=settings,reopened_packed_sha256=packed_sha,
                reopened_dimensions=list(image.size),reopened_colorspace=image.colorspace_settings.name,
                reopened_alpha_mode=image.alpha_mode)
            if binding.get('format')==49:
                row.update(blender_conversion=image.get('blender_conversion'),
                    blender_packed_sha256=image_sha,conversion_marker_valid=conversion_ok)
            elif binding.get('format')==61:
                row.update(blender_conversion=image.get('blender_conversion'),
                    blender_packed_sha256=image_sha,conversion_marker_valid=conversion_ok)
            if not settings:
                row.update(status='failed',source_exact_top_mip=False,reason='reopened packed DDS bytes or image settings differ from binding')
            elif binding.get('status')=='failed':
                row.update(status='failed',source_exact_top_mip=False)
            elif binding.get('status')=='passed' and binding.get('source_exact_top_mip') is True:
                row.update(status='passed',source_exact_top_mip=True)
            else:
                row.update(status='limited',source_exact_top_mip=None)
            report.append(row)
        except Exception as exc:
            row.update(status='failed',source_exact_top_mip=False,reopened=False,reason='reopened image validation failed: '+str(exc))
            report.append(row)
    return report
