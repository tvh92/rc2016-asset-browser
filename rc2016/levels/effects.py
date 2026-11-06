"""Visual-effect placements of a `.zone`, the effects' component records, and the light cones among them (docs/levels.md,
"Light beams").

A zone lists the visual effects it places in three sections, decoded here on every zone of the game (5114 placements,
all with the same record shape):

    0x3D5E2FEF  u32[n]   absolute file offsets of NUL-terminated `.visualeffect` paths
    0x927C4EC3  u32[n]   offsets of placement records inside the data heap 0x06ABCAB2
    placement record (0x60 bytes): +0x00 float32[16] 4x4 matrix, row-vector convention (rows 0 to 2 the local axes,
                                    row 3 the position), +0x40 u32 kind (5 in all), +0x44 u32 record size (0x60),
                                    +0x48 float32[4] (1.0 in all 27 Aridia light cones), +0x5C u32 whose low byte is the
                                    index into the path list (the high byte is 0xFF)

A `.visualeffect` is a particle system. Its components are the 272-byte records of section 0xB86CF442:

    +0x3C, +0x40  float32      size pair: the sprite size of a sprite component, 1.0 and 1.0 on all 426 mesh components
    +0x44         u64          asset id of the component's model: the CRC-64 of its path (zone_format.path_crc64),
                               0 for components that draw none; 274 of the 277 effects that name a model have a
                               component with its id
    +0xF4         u32          flag word (0x0C0CC82x for the mesh and the unlisted components, 0x0E0CE82x for sprites)
    +0x103        u8           kind: 2 spawns a light (eboot 0x16FD340 builds a light record from the component and calls the
                               light system, 0x1852870; 337 components in the game), 0xA to 0xD draw a model (0xC with the
                               model asset at +0x44, 0xA and 0xD also with a material at +0x4C), 0 and 1 are sprites with a
                               material at +0x4C. A model kind without a model asset (72 components, the light cones' third
                               component among them) creates nothing: the model handler 0x2A50890 returns when the asset id's
                               top bit is clear
    +0x108        u8[7]        indices into the parameter table, one per property slot

A component's slot byte selects an entry of 0xD78E007C (u32 hash, u32 value index, u32 key count, float32 scale at +12
that the values multiply: 1/32767 for a plain value, 3/32767 for a value up to 3); its keys are the 16-byte entries
[index, index + count) of 0xB1F4C248 (four s16 values at +0, +2, +4, +6, 0x7FFF being 1.0 before the scale, then a
four-value spread at +8 that the spawn adds at random, its raw w choosing the random vector). With more than one key the
value's raw w over 32767 is the key's time over the particle's normalised age, and the keys are interpolated linearly
(``parameters`` keeps them as ``keys``; ``parameter_at`` evaluates them). The index differs from the entry number once an
earlier entry has several keys (the health crate's idle effect from entry 10 on); every light-cone effect has single keys
at their own index, so their readings are unchanged. The hash is
not a name that can be reproduced. The evaluator is eboot function 0x170A3D0, the function that packs a particle for the
GPU 0x170D940: it evaluates slots 0, 1, 3, 4 and 5 of the component at spawn (slots 2 and 6 are left to the per-frame
evaluator 0x170A750). Slot 1 times slot 4 is the particle's tint, stored as three s16 words (x 1024) at +0x18 of the 48-byte
particle record. The mesh submit (0x2A51FF0) reads those words back (x 1/1024), multiplies them by the effect's colour (the
placement's floats at +0x48, when the component's flag 0x4000 is set; 1.0, 1.0, 1.0 in every cone) and hands the vector to
0x182E360, which stores it in the instance's extras block at +0x3C; the record filler 0x1838630 copies that to +0xB0 of the
272-byte per-instance record, the three floats the cone pixel program reads as its colour. Slot 5 is quantised to two bytes
(R and G) in the particle record, which the mesh submit takes as an intensity byte. Slot 5 is also the component's colour in
the sense of the names: on the colour-named flare effects of the game its RGB follows the name (mean cosine 0.9 to 0.99),
and in the warm and blue wide light cones it is the one entry in which the two effects differ. How its hue reaches the cone's
colour, which the code above does not show (the tint carries no hue: Med (0.75, 0.75, 1.0), Small (0.375, 0.375, 0.5), both
wide cones white), is not decoded.

Slot numbering: the numbers above count the packer's evaluated descriptors, not the slot bytes. In slot-byte numbering
(b0 at +0x108, as ``slots`` lists them and ``spawn_terms`` reads them) the packer evaluates b1, b2, b4, b5 and b6, and the
particle colour at +0x18 is b2 x b5 with b4 the full quad size; this is captured on the health crate's idle-effect sprites.
``mesh_colour`` indexes ``slots`` with the numbers above, so its ``tint`` is b1 x b4 (on the cones velocity 1 x the size
term) and its ``hue`` is b5. That the cone colour is b2 x b5 is inferred and not checked against a captured cone draw, and it
would change every cone's brightness (Med x0.44, Small x6, the wide cones x2.2 to x3), so the cone colour keeps its
reading until a cone capture settles it.

The light cones are the `gbl_amb_Light_Cone_*` effects. Each draws one cone model through a component with the model's
id, with the additive two-sided material `gbl_amb_Light_Cone_Circular_Med` (flags 0x20D, a captured draw state,
docs/shading.md). The placement matrix aims the cone directly: its local +Z (circular cone, apex at the origin, 10 long)
or -Y (wide hanging cone, 2 long) is the aim axis of the spot light that stands at the same place (all 59 of Quartu's
wide cones lie within 0.6 units of a spot record, with the axis opposite the matrix's +Y row).

Not decoded: the other components (sprites for the flare at the lamp, and records without a model), the intensity the
particle update applies to the colour before the pixel program reads it from a per-particle record, and the effect's timing.
The viewer draws the cone model at scale 1 (the mesh component's size pair) in the mesh component's colour and opacity
through the game's pixel program (rc2016.levels.beams, rc2016.levels.cone_shader).
"""
from __future__ import annotations

import math
import re
import struct

from rc2016.levels.scene import asset_bytes
from rc2016.levels.zone_format import path_crc64, sections

EFFECT_PATHS = 0x3D5E2FEF
EFFECT_OFFSETS = 0x927C4EC3
DATA_HEAP = 0x06ABCAB2
RECORD_SIZE = 0x60
RECORD_KIND = 5
COMPONENTS = 0xB86CF442
PARAMETER_VALUES = 0xB1F4C248
PARAMETER_SCALES = 0xD78E007C
COMPONENT_SIZE = 0x110
LIGHT_CONE = re.compile(r'light_cone', re.IGNORECASE)
COLOUR_SLOT = 5
TINT_SLOTS = (1, 4)         # the spawn tint is the product of these two slots' values
COLOUR_FLAG = 0x4000        # component flag: the tint is multiplied by the placement's colour floats
OPACITY_BYTE = 22           # of the material's render-state section 0xE1275683: attribute0.w = byte / 255 times the draw's opacity
SLOT_LIST = 0x108


def _cstring(data, offset):
    return data[offset:data.index(b'\0', offset)].decode('ascii', 'replace').replace('\\', '/')


def decode_zone_effects(data, source_path=''):
    """The visual-effect placements of one decoded zone: dicts with ``effect`` (path), ``matrix`` (16 floats), ``position``,
    ``axis`` (the unit local +Z row), ``tint`` (the floats at +0x48, the effect's colour multiplier), ``brightness`` (the byte
    at +0x5F over 255, which the effect object keeps as its brightness factor), ``source_path`` and ``source_ordinal``. A zone without the sections has none; a record
    that does not have the decoded shape is left out."""
    table = sections(data)
    if not all(tag in table for tag in (EFFECT_PATHS, EFFECT_OFFSETS, DATA_HEAP)):
        return []
    path_offset, path_size = table[EFFECT_PATHS]
    index_offset, index_size = table[EFFECT_OFFSETS]
    heap_offset, heap_size = table[DATA_HEAP]
    paths = [_cstring(data, offset) for offset in struct.unpack_from(f'<{path_size // 4}I', data, path_offset)]
    placements = []
    for ordinal, relative in enumerate(struct.unpack_from(f'<{index_size // 4}I', data, index_offset)):
        start = heap_offset + relative
        if relative + RECORD_SIZE > heap_size:
            continue
        matrix = struct.unpack_from('<16f', data, start)
        kind, size = struct.unpack_from('<II', data, start + 0x40)
        index = data[start + 0x5C]
        if (kind != RECORD_KIND or size != RECORD_SIZE or index >= len(paths) or not all(math.isfinite(v) for v in matrix)
                or (matrix[3], matrix[7], matrix[11], matrix[15]) != (0.0, 0.0, 0.0, 1.0)):
            continue
        row = matrix[8:11]
        length = math.sqrt(sum(v * v for v in row))
        placements.append(dict(effect=paths[index], matrix=list(matrix), position=list(matrix[12:15]),
                               tint=list(struct.unpack_from('<3f', data, start + 0x48)), brightness=data[start + 0x5F] / 255.0,
                               axis=[v / length for v in row] if length > 0.0 else [0.0, 0.0, 1.0],
                               source_path=source_path, source_ordinal=ordinal))
    return placements


def decode_effect(data):
    """A decoded `.visualeffect`: ``components`` (dicts with ``asset`` (the u64 model id, 0 for none), ``size`` (the pair at
    +0x3C), ``flags`` (+0xF4), ``kind`` (+0x103) and ``slots`` (the seven parameter indices)) and ``parameters`` (dicts with ``hash``, ``value``
    (four floats: the u16 values times the scale) and ``scale``). An effect without the sections has neither."""
    table = sections(data)
    components, parameters = [], []
    if COMPONENTS in table:
        offset, size = table[COMPONENTS]
        for n in range(size // COMPONENT_SIZE):
            start = offset + n * COMPONENT_SIZE
            components.append(dict(asset=struct.unpack_from('<Q', data, start + 0x44)[0],
                                   size=struct.unpack_from('<2f', data, start + 0x3C),
                                   flags=struct.unpack_from('<I', data, start + 0xF4)[0], kind=data[start + 0x103],
                                   slots=list(struct.unpack_from('<7B', data, start + SLOT_LIST))))
    if PARAMETER_VALUES in table and PARAMETER_SCALES in table:
        values, scales = table[PARAMETER_VALUES], table[PARAMETER_SCALES]
        for n in range(scales[1] // 16):
            # Entry n of 0xD78E007C: (hash, value index, key count, scale); its keys are 0xB1F4C248 entries
            # [index, index + count) (eboot 0x170A3D0 / 0x170A450).
            parameter_hash, index, count = struct.unpack_from('<3I', data, scales[0] + n * 16)
            scale = struct.unpack_from('<f', data, scales[0] + n * 16 + 12)[0]
            if count < 1 or (index + count) * 16 > values[1]:
                break
            keys = [struct.unpack_from('<8h', data, values[0] + (index + k) * 16) for k in range(count)]
            raw = keys[0]
            parameters.append(dict(hash=parameter_hash, index=index, count=count, scale=scale,
                                   value=[v * scale for v in raw[:4]], spread=[v * scale for v in raw[4:]],
                                   keys=[dict(time=key[3] / 32767.0 if count > 1 else 0.0, value=[v * scale for v in key[:3]],
                                              spread=[v * scale for v in key[4:7]], random=key[7]) for key in keys]))
    return dict(components=components, parameters=parameters)


# Slot bytes b0..b6 (+0x108 + k) as the particle packer 0x170D940 evaluates them (descriptors at runtime +0x100, +0x140,
# +0x1C0, +0x200, +0x240 are b1, b2, b4, b5, b6): b1 velocity term, b2 x b5 the particle colour P2 stored at particle +0x18,
# b4 the full quad width and height, b6.x the alpha and b6.y the discard threshold (bytes over 255); b0 and b3 are evaluated
# per frame. Captured on the health crate's idle-effect sprites (sizes and colours exactly).
# Research/captures/2026-11-05_gaspar-crates/nanotech_verify/README.md (local).
SLOT_VELOCITY, SLOT_COLOUR_A, SLOT_SIZE, SLOT_COLOUR_B, SLOT_ALPHA = 1, 2, 4, 5, 6


def parameter_at(parameter, t=0.0):
    """A parameter's xyz at the particle's normalised age t: its single key, or the keys interpolated linearly by their
    times (the curve evaluator 0x170A450). The random spread is not applied."""
    keys = parameter['keys']
    if len(keys) == 1 or t <= keys[0]['time']:
        return list(keys[0]['value'])
    for a, b in zip(keys, keys[1:]):
        if t <= b['time']:
            f = (t - a['time']) / (b['time'] - a['time']) if b['time'] > a['time'] else 1.0
            return [x + (y - x) * f for x, y in zip(a['value'], b['value'])]
    return list(keys[-1]['value'])


def spawn_terms(effect, component, t=0.0):
    """The decoded spawn terms of one component at normalised age t, or None when a slot lists no parameter: ``colour``
    (b2 x b5, the particle colour P2), ``size`` (b4 xy, full quad width and height), ``alpha`` and ``discard`` (b6 x, y)
    and ``velocity`` (b1). The light cones' colour (mesh_colour) still reads its own slots; see that function."""
    slots = effect['components'][component]['slots']
    parameters = effect['parameters']
    if max(slots[k] for k in (SLOT_VELOCITY, SLOT_COLOUR_A, SLOT_SIZE, SLOT_COLOUR_B, SLOT_ALPHA)) >= len(parameters):
        return None
    a, b = parameter_at(parameters[slots[SLOT_COLOUR_A]], t), parameter_at(parameters[slots[SLOT_COLOUR_B]], t)
    size, alpha = parameter_at(parameters[slots[SLOT_SIZE]], t), parameter_at(parameters[slots[SLOT_ALPHA]], t)
    return dict(colour=[x * y for x, y in zip(a, b)], size=size[:2], alpha=alpha[0], discard=alpha[1],
                velocity=parameter_at(parameters[slots[SLOT_VELOCITY]], t))


def effect_model_path(archive, effect_path):
    """The `.model` path a `.visualeffect` names, or None."""
    data = asset_bytes(archive, effect_path)
    models = [_cstring(data, m.start()) for m in re.finditer(rb'[A-Za-z0-9_][ -~]*\.model\0', data, re.IGNORECASE)]
    return models[0] if models else None


def mesh_scale(effect, model_path):
    """The scale of the effect's mesh component: the size pair of the one component whose asset id is the CRC-64 of
    ``model_path``, when its two values agree; None when there is not exactly one such component."""
    mesh = [c for c in effect['components'] if c['asset'] == path_crc64(model_path)]
    if len(mesh) != 1 or mesh[0]['size'][0] != mesh[0]['size'][1]:
        return None
    return float(mesh[0]['size'][0])


def mesh_colour(effect, model_path):
    """The mesh component's colour terms, or None when there is not exactly one mesh component or a slot lists no parameter:
    ``tint`` (slot 1 times slot 4, the spawn tint the code hands to the pixel program), ``hue`` (the RGB of the colour slot,
    5) and ``flags``. The spread added at spawn is zero for these parameters and is not applied."""
    mesh = [c for c in effect['components'] if c['asset'] == path_crc64(model_path)]
    if len(mesh) != 1:
        return None
    first, second = (mesh[0]['slots'][slot] for slot in TINT_SLOTS)
    hue = mesh[0]['slots'][COLOUR_SLOT]
    if max(first, second, hue) >= len(effect['parameters']):
        return None
    values = [effect['parameters'][i]['value'][:3] for i in (first, second, hue)]
    return dict(tint=[a * b for a, b in zip(values[0], values[1])], hue=list(values[2]), flags=mesh[0]['flags'])


def cone_colour(terms, placement_tint=(1.0, 1.0, 1.0)):
    """The colour the pixel program reads for a mesh component (``mesh_colour``'s terms): the tint's magnitude, which the code
    decodes, in the hue of the colour slot, which the code does not route to it (inferred: the in-game beams are orange where
    the tint alone is cool white). The hue is the colour slot's RGB scaled to its largest channel 1 (white when it is zero), the
    magnitude the largest channel of the tint, and the placement's colour multiplies when the component's flag asks for it."""
    peak = max(terms['hue'])
    hue = [v / peak for v in terms['hue']] if peak > 0.0 else [1.0, 1.0, 1.0]
    magnitude = max(terms['tint'])
    scale = placement_tint if terms['flags'] & COLOUR_FLAG else (1.0, 1.0, 1.0)
    return [h * magnitude * s for h, s in zip(hue, scale)]


def material_opacity(archive, material_path):
    """attribute0.w of a draw of the material: byte 22 of its render-state section 0xE1275683 over 255 (times the draw's own
    opacity, 1 here), as for the captured glass draws (docs/shading.md); 1.0 when the section is absent."""
    data = asset_bytes(archive, material_path)
    for k in range(struct.unpack_from('<H', data, 12)[0]):
        tag, offset, size = struct.unpack_from('<III', data, 16 + 12 * k)
        if tag == 0xE1275683 and size > OPACITY_BYTE:
            return data[offset + OPACITY_BYTE] / 255.0
    return 1.0


def is_light_cone(effect_path):
    return LIGHT_CONE.search(effect_path.rsplit('/', 1)[-1]) is not None


def load_light_cones(archive, zone_paths):
    """The light cones to draw for the zones of a level.

    Returns ``cones`` (placements of the cone models, each with its ``model`` path, ``scale`` (the mesh component's),
    ``colour`` (``cone_colour``), ``opacity`` (the material's, ``material_opacity``) and the matrix scaled), ``geometry`` ({model path: {'positions',
    'normals' float32 (n, 3), 'uvs' float32 (n, 2), 'indices' uint32}} in model space), ``noise`` (the pixel program's noise
    texture, rc2016.viewer.textures.load_texture, or None without cones) and ``undrawn`` ({effect file name: count} of the light-cone placements that are decoded but not drawn,
    with the reason in ``undrawn_reasons``)."""
    import numpy as np
    from rc2016.levels.scene import model_geometry
    cones, geometry, opacity, undrawn, reasons, seen, decoded = [], {}, {}, {}, {}, set(), {}

    def skip(placement, reason):
        name = placement['effect'].rsplit('/', 1)[-1]
        undrawn[name] = undrawn.get(name, 0) + 1
        reasons[name] = reason

    for original in zone_paths:
        path = original.replace('\\', '/').lstrip('?').lower()
        if not path.endswith('.zone') or path in seen:
            continue
        seen.add(path)
        if len(archive.resolve(path)) != 1:
            continue
        for placement in decode_zone_effects(asset_bytes(archive, path), path):
            if not is_light_cone(placement['effect']):
                continue
            if placement['effect'] not in decoded:
                model = effect_model_path(archive, placement['effect'])
                effect = decode_effect(asset_bytes(archive, placement['effect'])) if model else None
                scale = mesh_scale(effect, model) if model else None
                terms = mesh_colour(effect, model) if model else None
                decoded[placement['effect']] = (model, scale, terms)
            model, scale, terms = decoded[placement['effect']]
            if model is None or scale is None or terms is None:
                skip(placement, 'no single mesh component with a model, scale and colour')
                continue
            placement['model'], placement['scale'] = model, scale
            placement['colour'] = cone_colour(terms, placement['tint'])
            cones.append(placement)
    kept = []
    for cone in cones:
        if cone['model'] not in geometry:
            loaded = model_geometry(archive, cone['model'])
            opacity[cone['model']] = material_opacity(archive, loaded['materials'][0]['path'])
            geometry[cone['model']] = dict(positions=np.asarray(loaded['positions'], np.float32).reshape(-1, 3),
                                           normals=np.asarray(loaded['normals'], np.float32).reshape(-1, 3),
                                           uvs=np.asarray(loaded['uvs'], np.float32).reshape(-1, 2),
                                           indices=np.asarray(loaded['indices'], np.uint32))
        matrix = np.asarray(cone['matrix'], np.float64).reshape(4, 4)
        matrix[:3, :3] *= cone['scale']
        cone['matrix'] = matrix.reshape(-1).tolist()
        cone['opacity'] = opacity[cone['model']]
        kept.append(cone)
    noise = None
    if kept:
        from rc2016.levels.cone_shader import NOISE_TEXTURE
        from rc2016.viewer.textures import load_texture
        noise = load_texture(archive, NOISE_TEXTURE)
    return dict(cones=kept, geometry=geometry, noise=noise, undrawn=undrawn, undrawn_reasons=reasons)


__all__ = ['cone_colour', 'decode_effect', 'decode_zone_effects', 'effect_model_path', 'is_light_cone', 'load_light_cones',
           'material_opacity', 'mesh_colour', 'mesh_scale', 'parameter_at', 'spawn_terms']
