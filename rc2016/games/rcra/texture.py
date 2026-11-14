"""Rift Apart (PC) ``.texture`` assets: header, mip chains and the viewer's loaded-texture dict.

An asset is the 36-byte toc header (u32 type 0x8F53A199, u32 DAT1 size, u32 SD byte count, ...),
a small DAT1 whose one section 0x4EDE3593 is the 44-byte texture header, then the SD mip chain.
A streamed texture's HD stream (its headerless toc row, ``RcraSource.texture_stream``) holds the
larger top mips the SD chain lacks. On PC every mip is stored linearly in DXGI order (no tiling):
an array texture stores each slice's whole chain back to back, and a cube map (an SD payload exactly
six chains long: sky and probe textures) its six faces the same way. Header fields (shared with the 2016
game): u32 SD bytes, u32 HD bytes, u16 HD width, height, u16 SD width, height, u16 array size,
u8 Insomniac format, u8 planes, u16 DXGI format, ..., u8 SD mip count at +30, u8 HD mip count at +32.
"""
import struct
from pathlib import Path

from rc2016.formats.texture import BLOCK_BYTES, UNCOMPRESSED_BPP, dds_bytes, top_mip_size
from rc2016.games.rcra.toc import sections

TEXTURE_MAGIC = 0x8F53A199
HEADER_SECTION = 0x4EDE3593
ASSET_HEADER = 36
MIN_PREVIEW_SIZE = 512     # like viewer.textures: below this the SD top mip is too blurry, use the HD stream
HEADER_FACTS = ('format', 'hd_width', 'hd_height', 'sd_width', 'sd_height', 'array_size')
# Data formats seen only in vertex-animation, probe and lookup textures: sized, never previewed.
DATA_BPP = {2: 16, 10: 8, 16: 8, 34: 4, 35: 4, 54: 2}   # RGBA32F, RGBA16F, RG32F, RG16F, RG16, R16F


def parse_header(data):
    """Header facts of a texture asset, plus where its SD chain starts."""
    if len(data) < ASSET_HEADER + 16 or struct.unpack_from('<I', data, 0)[0] != TEXTURE_MAGIC:
        raise ValueError('Not a Rift Apart texture (expected type 0x%08X)' % TEXTURE_MAGIC)
    dat1 = data[ASSET_HEADER:]
    size = struct.unpack_from('<I', dat1, 8)[0]
    table = sections(dat1[:size])
    if HEADER_SECTION not in table:
        raise ValueError('Texture has no 0x4EDE3593 header section')
    offset, length = table[HEADER_SECTION]
    if length < 34:
        raise ValueError('Texture header section is too short')
    sd_len, hd_len, hd_w, hd_h, sd_w, sd_h, array, stex, planes, fmt = struct.unpack_from('<IIHHHHHBBH', dat1, offset)
    sd_mips, hd_mips = dat1[offset + 30], dat1[offset + 32]
    payload = ASSET_HEADER + size
    if payload + sd_len != len(data):
        raise ValueError('Texture holds %d bytes after its header, header says %d' % (len(data) - payload, sd_len))
    array = max(1, array)
    faces = 1
    if supported(fmt) and sd_len:
        chain = sum(chain_sizes(fmt, sd_w, sd_h, sd_mips)) * array
        if sd_len == 6 * chain:     # cube maps (sky and probe textures) store six faces' chains
            faces = 6
        elif sd_len != chain:
            raise ValueError('SD payload is %d bytes, its mip chain %d' % (sd_len, chain))
    return dict(format=fmt, insomniac_format=stex, planes=planes, array_size=array, faces=faces,
                sd_width=sd_w, sd_height=sd_h, sd_mips=sd_mips, sd_size=sd_len,
                hd_width=hd_w if hd_len else 0, hd_height=hd_h if hd_len else 0, hd_mips=hd_mips if hd_len else 0,
                stream_size=hd_len, payload_offset=payload)


def supported(fmt):
    return fmt in BLOCK_BYTES or fmt in UNCOMPRESSED_BPP or fmt in DATA_BPP


def chain_sizes(fmt, width, height, count):
    """Byte size of each of ``count`` mips starting at width x height."""
    if not supported(fmt):
        raise ValueError(f'Texture format {fmt} is not supported')
    sizes = []
    for _ in range(max(1, count)):
        sizes.append(width * height * DATA_BPP[fmt] if fmt in DATA_BPP else top_mip_size(width, height, fmt))
        width, height = max(1, width // 2), max(1, height // 2)
    return sizes


def _slices(payload, fmt, width, height, mips, count, label):
    """The top mip of each array slice from a linear chain of ``mips`` mips per slice."""
    sizes = chain_sizes(fmt, width, height, mips)
    stride = sum(sizes)
    if len(payload) < stride * count:
        raise ValueError(f'{label} holds {len(payload)} bytes, {count} slice(s) of {stride} needed')
    return [payload[i * stride:i * stride + sizes[0]] for i in range(count)]


def _entry(source, texture_path):
    keys = [k for k in source.resolve(texture_path) if source.entries[k]['type'] == 'texture']
    if len(keys) != 1:
        raise ValueError(f'Texture {texture_path} has {len(keys)} assets')
    return keys[0]


def read_top_mip(source, texture_path, full):
    """The viewer's loaded-texture dict (viewer.textures._read_top_mip shape) of a Rift Apart texture.

    ``full`` takes the HD stream whenever there is one; otherwise the SD top mip is used once it is
    at least MIN_PREVIEW_SIZE (smaller ones also take the stream)."""
    key = _entry(source, texture_path)
    data = source.asset_data(key)
    info = parse_header(data)
    fmt, count = info['format'], info['array_size'] * info['faces']   # cube faces read as slices
    small = max(info['sd_width'], info['sd_height'])
    stream = None
    if info['stream_size'] and info['hd_width'] and (full or small < MIN_PREVIEW_SIZE or not info['sd_size']):
        stream = source.texture_stream(key)
    if stream is not None:
        if len(stream) != info['stream_size']:
            raise ValueError(f'Texture {texture_path} stream holds {len(stream)} bytes, header says {info["stream_size"]}')
        width, height = info['hd_width'], info['hd_height']
        slices = _slices(stream, fmt, width, height, info['hd_mips'], count, 'HD stream')
    elif info['sd_size'] and info['sd_width'] and info['sd_height']:
        width, height = info['sd_width'], info['sd_height']
        slices = _slices(data[info['payload_offset']:], fmt, width, height, info['sd_mips'], count, 'SD chain')
    else:
        raise ValueError(f'Texture {texture_path} has no usable payload')
    from rc2016.viewer.textures import SRGB_FORMATS
    full_size = (info['hd_width'], info['hd_height']) if info['hd_width'] else (info['sd_width'], info['sd_height'])
    return dict(path=texture_path, width=width, height=height, format=fmt, pixels=slices[0], slices=slices,
                array_size=count, full_size=full_size, srgb=fmt in SRGB_FORMATS)


def load_texture(source, texture_path, hd=False):
    """viewer.textures.load_texture for Rift Apart: the top mip with its GL upload formats."""
    from rc2016.viewer import textures
    texture = read_top_mip(source, texture_path, full=hd and textures.MIN_PREVIEW_SIZE > 0)
    fmt = texture['format']
    if fmt not in textures.GL_COMPRESSED and fmt not in textures.GL_UNCOMPRESSED:
        raise ValueError(f'Texture format {fmt} is not previewable')
    texture.update(gl_format=textures.GL_COMPRESSED.get(fmt), gl_pixel_format=textures.GL_UNCOMPRESSED.get(fmt))
    return texture


def load_texture_full(source, texture_path):
    return read_top_mip(source, texture_path, full=True)


def texture_header(source, texture_path):
    """Header facts only (format and sizes), memoized on the source."""
    key = _entry(source, texture_path)
    cache = source.__dict__.setdefault('_texture_header_cache', {})
    facts = cache.get(key)
    if facts is None:
        info = parse_header(source.asset_data(key))
        facts = cache[key] = {k: info[k] for k in HEADER_FACTS}
    return dict(facts)


def archive_textures(source):
    """Every texture in the install: [{'path', 'folder'}] sorted by path (no entry dicts built)."""
    from rc2016.viewer.textures import is_placeholder_texture
    paths = [source.paths[source._index(k)] for k in source.ids_of_type('texture')]
    return [dict(path=p, folder=p.rsplit('/', 1)[0] if '/' in p else '') for p in paths if not is_placeholder_texture(p)]


def export_textures(source, paths, folder, formats, flip=False, progress=None):
    """viewer.textures.export_textures for Rift Apart: 'texture' writes the asset as stored (toc header
    included) and its HD stream as ``.texture.stream``; 'dds' and 'png' use the largest top mip."""
    from rc2016.viewer.textures import to_image
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
                key = _entry(source, path)
                target = folder / f'{name}.texture'
                target.write_bytes(source.asset_data(key))
                row['files'].append(str(target))
                stream = source.texture_stream(key)
                if stream is not None:
                    target = folder / f'{name}.texture.stream'
                    target.write_bytes(stream)
                    row['files'].append(str(target))
            if 'dds' in formats or 'png' in formats:
                texture = load_texture_full(source, path)
                count = texture['array_size']
                if 'dds' in formats:
                    target = folder / f'{name}.dds'
                    target.write_bytes(dds_bytes(texture['width'], texture['height'], texture['format'],
                                                 b''.join(texture['slices']), count))
                    row['files'].append(str(target))
                if 'png' in formats:
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
