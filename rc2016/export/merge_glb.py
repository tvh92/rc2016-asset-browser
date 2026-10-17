"""Combine several exported .glb files into one .glb, each model keeping its own nodes, skins and animations.

Every glTF index is shifted by the count of that kind already in the merged document, and the binary
chunks are appended one after another with their buffer views moved to match. Models keep their
native placement (the glTF scene roots are concatenated); nothing is re-posed or re-scaled.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import struct

from rc2016.export.models import tag_model, write_glb

GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942
SHIFTED_LISTS = ('accessors', 'bufferViews', 'meshes', 'materials', 'images', 'textures', 'samplers', 'nodes',
                 'skins', 'animations')


def read_glb(path):
    """(document, binary chunk) of a GLB 2.0 file."""
    data = Path(path).read_bytes()
    magic, version, _length = struct.unpack_from('<III', data, 0)
    if magic != GLB_MAGIC or version != 2:
        raise ValueError(f'{path} is not a glTF 2.0 binary file')
    offset, document, binary = 12, None, b''
    while offset + 8 <= len(data):
        chunk_length, kind = struct.unpack_from('<II', data, offset)
        chunk = data[offset + 8:offset + 8 + chunk_length]
        offset += 8 + chunk_length
        if kind == JSON_CHUNK:
            document = json.loads(chunk.decode('utf-8'))
        elif kind == BIN_CHUNK:
            binary = chunk
    if document is None:
        raise ValueError(f'{path} has no JSON chunk')
    return document, binary


def _shift_texture_refs(value, by, parent=''):
    """Move texture indices inside a material (and its extensions) by ``by``: the info objects sit under keys
    that end in 'Texture' (baseColorTexture, normalTexture, ...)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict) and key.lower().endswith('texture') and 'index' in item:
                item['index'] += by
            _shift_texture_refs(item, by, key)
    elif isinstance(value, list):
        for item in value:
            _shift_texture_refs(item, by, parent)
    return value


def _shift_accessor_refs(accessor, by):
    if 'bufferView' in accessor:
        accessor['bufferView'] += by
    sparse = accessor.get('sparse')
    if sparse:
        sparse['indices']['bufferView'] += by
        sparse['values']['bufferView'] += by
    return accessor


def merge_glb(paths, out_path):
    """Write one GLB holding every file in ``paths`` in order. Returns the number of models merged."""
    merged = dict(asset=dict(version='2.0', generator='RC2016 Asset Browser (combined export)'), scene=0,
                  scenes=[dict(nodes=[])], buffers=[], extensionsUsed=[], extensionsRequired=[])
    for key in SHIFTED_LISTS:
        merged[key] = []
    binary = bytearray()
    for path in paths:
        document, data = read_glb(path)
        tag_model(document, Path(path).stem)   # the model each of its nodes belongs to
        base = {key: len(merged[key]) for key in SHIFTED_LISTS}
        while len(binary) % 4:
            binary.append(0)
        shift = len(binary)
        binary += data
        for view in document.get('bufferViews', []):
            moved = dict(view, buffer=0, byteOffset=view.get('byteOffset', 0) + shift)
            merged['bufferViews'].append(moved)
        for accessor in document.get('accessors', []):
            merged['accessors'].append(_shift_accessor_refs(copy.deepcopy(accessor), base['bufferViews']))
        for image in document.get('images', []):
            moved = copy.deepcopy(image)
            if 'bufferView' in moved:
                moved['bufferView'] += base['bufferViews']
            merged['images'].append(moved)
        for sampler in document.get('samplers', []):
            merged['samplers'].append(copy.deepcopy(sampler))
        for texture in document.get('textures', []):
            moved = copy.deepcopy(texture)
            if 'source' in moved:
                moved['source'] += base['images']
            if 'sampler' in moved:
                moved['sampler'] += base['samplers']
            merged['textures'].append(moved)
        for material in document.get('materials', []):
            merged['materials'].append(_shift_texture_refs(copy.deepcopy(material), base['textures']))
        for mesh in document.get('meshes', []):
            moved = copy.deepcopy(mesh)
            for primitive in moved.get('primitives', []):
                primitive['attributes'] = {name: index + base['accessors'] for name, index in primitive['attributes'].items()}
                if 'indices' in primitive:
                    primitive['indices'] += base['accessors']
                if 'material' in primitive:
                    primitive['material'] += base['materials']
                for target in primitive.get('targets', []):
                    for name in target:
                        target[name] += base['accessors']
            merged['meshes'].append(moved)
        for node in document.get('nodes', []):
            moved = copy.deepcopy(node)
            if 'children' in moved:
                moved['children'] = [index + base['nodes'] for index in moved['children']]
            if 'mesh' in moved:
                moved['mesh'] += base['meshes']
            if 'skin' in moved:
                moved['skin'] += base['skins']
            merged['nodes'].append(moved)
        for skin in document.get('skins', []):
            moved = copy.deepcopy(skin)
            moved['joints'] = [index + base['nodes'] for index in moved['joints']]
            if 'skeleton' in moved:
                moved['skeleton'] += base['nodes']
            if 'inverseBindMatrices' in moved:
                moved['inverseBindMatrices'] += base['accessors']
            merged['skins'].append(moved)
        for animation in document.get('animations', []):
            moved = copy.deepcopy(animation)
            for sampler in moved.get('samplers', []):
                sampler['input'] += base['accessors']
                sampler['output'] += base['accessors']
            for channel in moved.get('channels', []):
                if 'node' in channel.get('target', {}):
                    channel['target']['node'] += base['nodes']
            merged['animations'].append(moved)
        scenes = document.get('scenes', [])
        if scenes:
            roots = scenes[document.get('scene', 0)].get('nodes', [])
            merged['scenes'][0]['nodes'] += [index + base['nodes'] for index in roots]
        for name in ('extensionsUsed', 'extensionsRequired'):
            merged[name] = sorted(set(merged[name]) | set(document.get(name, [])))
    for key in SHIFTED_LISTS + ('extensionsUsed', 'extensionsRequired'):
        if not merged[key]:
            del merged[key]
    merged['buffers'] = [dict(byteLength=len(binary))]
    write_glb(Path(out_path), merged, bytes(binary))
    return len(paths)
