"""PS4 texture built/stream decoder and DDS writer for this game."""
from collections import OrderedDict
from pathlib import Path
import struct
import threading

from rc2016.formats.container import read_container
from rc2016.formats.archive import lz4_block
from rc2016.common import file_hash, read, sha, write

HEADER = 0x4EDE3593
BLOCK_BYTES = {71:8, 72:8, 74:16, 75:16, 77:16, 78:16, 80:8, 83:16, 95:16, 96:16, 98:16, 99:16}
UNCOMPRESSED_BPP = {28:4, 29:4, 49:2, 61:1}


def parse_header(built):
    root=read_container(built,0,{},light=True)
    section=next((s for s in root['sections'] if int(s['label_hash'],16)==HEADER),None)
    if not section or section['size']<36:
        raise ValueError('Texture lacks the 36-byte PS4 texture header')
    data=built[section['offset']:section['offset']+section['size']]
    built_size=struct.unpack_from('<I',data,0)[0]
    stream_size=struct.unpack_from('<I',data,4)[0]
    hd_width,hd_height,sd_width,sd_height=struct.unpack_from('<4H',data,8)
    array_size,stex_format,planes=struct.unpack_from('<HBB',data,16)
    fmt=struct.unpack_from('<H',data,20)[0]
    # Non-power-of-two sizes (UI art, 1920x1080 screens) are stored padded to
    # whole 8x8-block tiles like every other texture; all 369 in the corpus
    # have built sizes equal to that padded top mip.
    for label,value in [('HD width',hd_width),('HD height',hd_height),('SD width',sd_width),('SD height',sd_height)]:
        if value>16384:
            raise ValueError(f'Unsupported {label}: {value}')
    if fmt not in BLOCK_BYTES and fmt not in UNCOMPRESSED_BPP:
        raise ValueError(f'Unsupported PS4/DXGI texture format {fmt}')
    # Bytes 30/32 are SD/HD mip counts. The local RCRA-Forge parser reads
    # this exact field sequence and selects the relevant count for each tier.
    # A zero count is normalized to one there, so bound the effective count.
    sd_mip_count,sd_mip_reserved,hd_mip_count,hd_mip_reserved=struct.unpack_from('<4B',data,30)
    for label,count,width,height in (
        ('SD',sd_mip_count,sd_width,sd_height),('HD',hd_mip_count,hd_width,hd_height)):
        if width and height:
            max_mips=max(width,height).bit_length()
            if max(1,count)>max_mips:
                raise ValueError(f'Unsupported {label} mip count {count} for {width}x{height} texture (maximum {max_mips})')
    appended=built[root['declared_size']:]
    if built_size!=len(appended):
        raise ValueError(f'Built texture payload size mismatch ({built_size} != {len(appended)})')
    return dict(schema='rcps4-texture-v1',declared_size=root['declared_size'],header_size=section['size'],
        built_size=built_size,stream_size=stream_size,hd_width=hd_width,hd_height=hd_height,
        sd_width=sd_width,sd_height=sd_height,array_size=array_size,stex_format=stex_format,
        planes=planes,format=fmt,sd_mip_count=sd_mip_count,hd_mip_count=hd_mip_count,
        sd_mip_count_offset=30,hd_mip_count_offset=32,
        sd_mip_reserved_byte=sd_mip_reserved,hd_mip_reserved_byte=hd_mip_reserved,
        raw_header_hex=data.hex(),built_payload=appended)


def top_mip_size(width,height,fmt):
    if fmt in BLOCK_BYTES:
        return max(1,(width+3)//4)*max(1,(height+3)//4)*BLOCK_BYTES[fmt]
    return width*height*UNCOMPRESSED_BPP[fmt]


def _tile_layout(width,height,fmt):
    if fmt in BLOCK_BYTES:block=4;bytes_per_block=BLOCK_BYTES[fmt]
    else:block=1;bytes_per_block=UNCOMPRESSED_BPP[fmt]
    blocks_w=(width+block-1)//block;blocks_h=(height+block-1)//block
    return blocks_w,blocks_h,(blocks_w+7)//8*8,(blocks_h+7)//8*8,bytes_per_block


def tiled_top_mip_size(width,height,fmt):
    """Bytes one tiled top mip occupies, padded to whole 8x8-block tiles."""
    _bw,_bh,padded_w,padded_h,bytes_per_block=_tile_layout(width,height,fmt)
    return padded_w*padded_h*bytes_per_block


def ps4_deswizzle_slices(data,width,height,fmt,count):
    """Top mip of each array slice; slices are stored back to back, each tile-padded."""
    step=tiled_top_mip_size(width,height,fmt)
    return [ps4_deswizzle_top(data[i*step:(i+1)*step],width,height,fmt) for i in range(count)]


# Untiling index arrays depend only on the padded block grid; most textures share a few sizes.
_TARGET_CACHE_MAX_ENTRIES=1<<22   # larger grids (16 MB+ of indices) are rebuilt each time
_target_cache=OrderedDict()
_target_lock=threading.Lock()


def _build_targets(padded_w,padded_h):
    import numpy as np
    m=np.arange(64)
    x=(m&1)|((m>>1)&2)|((m>>2)&4)
    y=((m>>1)&1)|((m>>2)&2)|((m>>3)&4)
    ty,tx=np.meshgrid(np.arange(padded_h//8),np.arange(padded_w//8),indexing='ij')
    return ((ty[...,None]*8+y)*padded_w+tx[...,None]*8+x).ravel()


def _deswizzle_targets(padded_w,padded_h):
    """Read-only destination index of each source block (bounded, thread-safe LRU of 8 grids)."""
    key=(padded_w,padded_h)
    with _target_lock:
        hit=_target_cache.get(key)
        if hit is not None:
            _target_cache.move_to_end(key);return hit
    target=_build_targets(padded_w,padded_h)
    if target.size>_TARGET_CACHE_MAX_ENTRIES:
        return target
    target.flags.writeable=False
    with _target_lock:
        _target_cache[key]=target
        while len(_target_cache)>8:_target_cache.popitem(last=False)
    return target


def ps4_deswizzle_top(data,width,height,fmt):
    """Untile one PS4 Thin1D-style top mip in 8x8 blocks.

    The ordering is the six-bit x0,y0,x1,y1,x2,y2 pattern used by the public
    MIT pyswizzle implementation. Keeping it here avoids a runtime dependency.
    """
    blocks_w,blocks_h,padded_w,padded_h,bytes_per_block=_tile_layout(width,height,fmt)
    needed=padded_w*padded_h*bytes_per_block
    if len(data)<needed:
        raise ValueError('PS4 texture stream is smaller than its top mip')
    try:
        import numpy as np
    except ImportError:
        np=None
    if np is not None:
        # Same mapping as the loop below, done as one gather.
        target=_deswizzle_targets(padded_w,padded_h)
        blocks=np.frombuffer(data,dtype=np.uint8,count=needed).reshape(-1,bytes_per_block)
        out=np.empty_like(blocks);out[target]=blocks
        out=out.reshape(padded_h,padded_w,bytes_per_block)[:blocks_h,:blocks_w]
        return out.tobytes()
    padded=bytearray(needed);source=0
    for tile_y in range(padded_h//8):
        for tile_x in range(padded_w//8):
            for morton in range(64):
                x=(morton&1)|((morton>>1)&2)|((morton>>2)&4)
                y=((morton>>1)&1)|((morton>>2)&2)|((morton>>3)&4)
                target=((tile_y*8+y)*padded_w+tile_x*8+x)*bytes_per_block
                padded[target:target+bytes_per_block]=data[source:source+bytes_per_block]
                source+=bytes_per_block
    if padded_w==blocks_w and padded_h==blocks_h:
        return bytes(padded)
    result=bytearray(blocks_w*blocks_h*bytes_per_block)
    row_bytes=blocks_w*bytes_per_block
    for row in range(blocks_h):
        result[row*row_bytes:(row+1)*row_bytes]=padded[row*padded_w*bytes_per_block:row*padded_w*bytes_per_block+row_bytes]
    return bytes(result)


def dds_bytes(width,height,fmt,pixels,array_size=1):
    """DX10 DDS of one top mip; with array_size > 1, `pixels` holds that many slices back to back."""
    size=top_mip_size(width,height,fmt)
    if array_size>1:
        if len(pixels)<size*array_size:
            raise ValueError(f'Texture payload is smaller than {array_size} top-mip slices')
        header=bytearray(dds_bytes(width,height,fmt,pixels[:size])[:148])
        struct.pack_into('<I',header,128+12,array_size)   # DX10 arraySize
        return bytes(header)+pixels[:size*array_size]
    if len(pixels)<size:
        raise ValueError(f'Texture payload is smaller than its top mip ({len(pixels)} < {size})')
    flags=0x1|0x2|0x4|0x1000|0x80000
    header=bytearray(b'DDS ')
    header.extend(struct.pack('<7I',124,flags,height,width,size,0,1))
    header.extend(bytes(44))
    header.extend(struct.pack('<II4s',32,4,b'DX10'))
    header.extend(bytes(20))
    header.extend(struct.pack('<5I',0x1000,0,0,0,0))
    header.extend(struct.pack('<5I',fmt,3,0,1,0))
    if len(header)!=148:
        raise AssertionError('DDS DX10 header length')
    return bytes(header)+pixels[:size]


def decode_texture(built_path,stream_bytes,output_path):
    built=read(built_path);info=parse_header(built)
    if stream_bytes is not None and info['stream_size'] and info['hd_width'] and info['hd_height']:
        packed=stream_bytes if len(stream_bytes)==info['stream_size'] else lz4_block(stream_bytes,info['stream_size'])
        width,height=info['hd_width'],info['hd_height']
        pixels=ps4_deswizzle_top(packed,width,height,info['format'])
        source=('stream_raw_top_mip_ps4_deswizzled' if len(stream_bytes)==info['stream_size']
                else 'stream_lz4_top_mip_ps4_deswizzled')
    elif info['built_payload'] and info['sd_width'] and info['sd_height']:
        width,height=info['sd_width'],info['sd_height']
        pixels=ps4_deswizzle_top(info['built_payload'],width,height,info['format'])
        source='built_top_mip_ps4_deswizzled'
    else:
        raise ValueError('Texture has no usable built or stream payload')
    blob=dds_bytes(width,height,info['format'],pixels)
    write(output_path,blob)
    report={k:v for k,v in info.items() if k!='built_payload'}
    report.update(width=width,height=height,payload_source=source,top_mip_bytes=top_mip_size(width,height,info['format']),
                  built_sha256=sha(built),stream_sha256=sha(stream_bytes) if stream_bytes is not None else None,
                  dds_path=str(output_path),dds_sha256=file_hash(output_path))
    return report
