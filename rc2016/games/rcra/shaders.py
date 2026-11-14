"""Rift Apart (PC) materialgraph shader programs: the pass table, the generic material binding and a neutral engine profile.

A ``.materialgraph`` carries its compiled programs in DAT1 section 0xBBFC8900: entries on 16-byte boundaries (zero bytes
may sit between them), each a 12-byte header (u32 magic 'IGSH', u32 SM5 size, u32 DXIL size) followed by the SM5 DXBC
container and the DXIL one. Raytracing libraries use magic 'IGRS' and are DXIL only. Section 0x8C049CCA is the pass
table: 18 or 36 records of (u32 flags, u32 hash, u32 entry offset from the section start, u32 entry size), the record
index being the pass slot; section 0xF9C35F30 is one such record for the raytracing library.

Pass slots as their programs show them (outputs and bindings, over all 1,474 graphs): 1 forward pixel program (one
target, does its own lighting), 4 and 5 G-buffer pixel programs (4 and 3 targets: packed normal/specular, albedo and
occlusion or emission, view depth, and velocity in 4), 8 depth-only pixel program, 11 a two-target pixel program,
15/16 the GI voxelization pixel/geometry programs, 3/7/10/13/17 vertex programs of vertex-deforming graphs.

Material binding is generic and needs no per-graph decoding: constant buffer cb2 (``MultiCustomMaterialCB``) holds the
graph's float defaults overwritten by the material's parameter values at the graph's byte offsets, and texture unit
``t(5 + u)`` (``textureUnit<5+u>``) binds the graph's slot-table row ``u``: the material's texture for that slot key, else
the row's default texture.
"""
import re
import struct

from rc2016.games.rcra import material as rmat
from rc2016.games.rcra.toc import sections
from rc2016.shaders import dxbc, sm5

SHADERS, PASSES, RAYTRACING = 0xBBFC8900, 0x8C049CCA, 0xF9C35F30
ENTRY_MAGIC, RAYTRACING_MAGIC = 0x48534749, 0x53524749    # 'IGSH', 'IGRS'
SLOT_FORWARD, SLOT_GBUFFER, SLOT_GBUFFER_SHORT, SLOT_DEPTH = 1, 4, 5, 8
MATERIAL_BUFFER = 'MultiCustomMaterialCB.g_CustomMaterial_Buffer'
FIRST_MATERIAL_UNIT = 5

# Engine inputs that switch whole engine features off. With them the forward and G-buffer programs of almost every graph
# translate without unbounded loops: no local lights or probes (empty lookup lists), no debug view, no optional features.
# These are viewer choices, not game data; every other engine input stays a named input.
NEUTRAL_INTEGERS = {
    'GlobalWorldCBuffer.m_LightLookupWordCount': 0,
    'GlobalWorldCBuffer.m_EnvProbeLookupWordCount': 0,
    'GlobalWorldCBuffer.m_FeatureFlags': 0,
    'GlobalWorldCBuffer.m_RTFlags': 0,
    'GlobalViewportCBuffer.m_MiscFlags': 0,
}
NEUTRAL_FLOATS = {'GlobalWorldCBuffer.m_KeyLightTapScale': 1.0}    # sun shadow PCF: 8 taps

# Forward profile (pass slot 1), for exporting blended materials. The forward programs light themselves (key light, light
# grid, probes, GI, SSR); an export lights the material's G-buffer surface with the renderer's own lights instead. So every
# light source a forward program reads is zero (every engine constant not bound here is zero), and the forward colour is
# the material's own unlit contribution: emission, self-lit terms and effects. These are export choices, not game data.
# - Draw flags: MatDrawCB.m_RenderFlags holds the material's lit bit (material.draw_flags, decoded); everything else in
#   it (lighting class, per-object state) is 0.
# - Engine textures and buffers: the stored view depth at the far clip (no soft depth fade). The lighting inputs (light
#   grid and lists, shadow atlas, gobos, probes, SSR, AO, the BRDF lookup) read 0, as the light sources they belong to are
#   zero. Programs that read scene colour, the G-buffer or per-object buffers stay unsupported.
FORWARD_FAR_CLIP = 1000.0
FORWARD_LIGHTING = ('g_LightGridLookup', 'g_LightLookupView', 'g_LightZBinView', 'g_LightGridData', 'g_LightVolumes',
                    'g_Lights', 'g_ShadowAtlas', 'g_GoboAtlas', 'g_EnvProbeEnvs', 'g_EnvProbeArray', 'g_EnvProbeDefault',
                    'g_SSRFinalTex', 'g_AoSsrBuffer', 'g_BrdfLookup')
FORWARD_RESOURCES = dict({name: 0.0 for name in FORWARD_LIGHTING}, g_ViewDepthBuffer=FORWARD_FAR_CLIP)
# - View: the programs rebuild the view-space position from SV_POSITION (screen xy, view depth w) and turn it into world
#   space with m_ViewToWorldMat. With these values the view-space position is SV_POSITION.(x, y, w) and view space has the
#   world's axes, so a renderer supplies SV_POSITION.xyw as the world offset from the camera to the point (input
#   'VIEW.xyz') and the matrix translation as the camera's world position ('CAMERA.xyz'), both in game space.
FORWARD_VIEW_FLOATS = {
    'GlobalViewportCBuffer.m_InvDimensions': 1.0, 'GlobalViewportCBuffer.m_InvDimensions+4': 1.0,
    'GlobalViewportCBuffer.m_ScreenToViewA': 1.0, 'GlobalViewportCBuffer.m_ScreenToViewA+4': 1.0,
    'GlobalViewportCBuffer.m_ScreenToViewA+8': 0.0, 'GlobalViewportCBuffer.m_ScreenToViewA+12': 0.0,
    'GlobalViewportCBuffer.m_ScreenToViewB': 0.0, 'GlobalViewportCBuffer.m_ScreenToViewB+4': 1.0,
    'GlobalViewportCBuffer.m_ViewToWorldMat': 1.0, 'GlobalViewportCBuffer.m_ViewToWorldMat+20': 1.0,
    'GlobalViewportCBuffer.m_ViewToWorldMat+40': 1.0,
    'GlobalViewportCBuffer.m_FarClip': FORWARD_FAR_CLIP,
    'GlobalViewportCBuffer.m_CustomClipPlane+12': 1.0,   # the custom clip plane (0, 0, 0, 1) clips nothing
}
FORWARD_VIEW_INPUTS = {'SV_POSITION.x': 'VIEW.x', 'SV_POSITION.y': 'VIEW.y', 'SV_POSITION.w': 'VIEW.z',
                       'GlobalViewportCBuffer.m_ViewToWorldMat+48': 'CAMERA.x',
                       'GlobalViewportCBuffer.m_ViewToWorldMat+52': 'CAMERA.y',
                       'GlobalViewportCBuffer.m_ViewToWorldMat+56': 'CAMERA.z'}


def pass_table(graph_data):
    """{slot: (flags, hash, entry offset, entry size)} of the filled pass slots."""
    dat = graph_data[36:]
    table = sections(dat)
    if PASSES not in table or SHADERS not in table:
        raise ValueError('Material graph has no shader or pass section')
    offset, size = table[PASSES]
    rows = {}
    for slot in range(size // 16):
        record = struct.unpack_from('<4I', dat, offset + 16 * slot)
        if record[3]:
            rows[slot] = record
    return rows


def pass_blobs(graph_data, slot):
    """(SM5 DXBC bytes or None, DXIL DXBC bytes or None) of one pass slot."""
    dat = graph_data[36:]
    base, size = sections(dat)[SHADERS]
    _flags, _hash, offset, length = pass_table(graph_data)[slot]
    if offset + length > size:
        raise ValueError('Pass entry outside the shader section')
    magic, sm5_size, dxil_size = struct.unpack_from('<3I', dat, base + offset)
    if magic != ENTRY_MAGIC or 12 + sm5_size + dxil_size != length:
        raise ValueError('Pass entry header does not match its record')
    start = base + offset + 12
    return (dat[start:start + sm5_size] or None), (dat[start + sm5_size:start + sm5_size + dxil_size] or None)


def pass_shader(graph_data, slot):
    """The decoded SM5 program of a pass slot."""
    blob = pass_blobs(graph_data, slot)[0]
    if blob is None:
        raise ValueError('Pass slot %d has no SM5 program' % slot)
    return dxbc.shader(blob)


def _layout(graph_data):
    """graph_layout, or no slots, parameters and defaults for a graph without those sections (template graphs)."""
    try:
        return rmat.graph_layout(graph_data)
    except ValueError as error:
        if 'no slot, parameter or default section' not in str(error):
            raise
        return [], {}, ()


def material_constants(graph_data, material_data):
    """cb2 as floats: the graph's defaults overwritten by the material's values at the graph's byte offsets."""
    _rows, offsets, defaults = _layout(graph_data)
    cb2 = list(defaults)
    for key, values in rmat.material_parameters(material_data).items():
        if key in offsets:
            for i, value in enumerate(values):
                at = offsets[key] // 4 + i
                cb2 += [0.0] * (at + 1 - len(cb2))
                cb2[at] = float(value)
    return cb2


def constant_name(index):
    """The translator's input name of cb2 dword ``index``."""
    return MATERIAL_BUFFER + ('+%d' % (4 * index) if index else '')


def material_units(graph_data, material_data):
    """{'textureUnit<n>': texture path}: each slot-table row's material texture, else the graph default."""
    slots = dict(rmat.material_slots(material_data))
    rows, _offsets, _defaults = _layout(graph_data)
    # Some material and graph strings double their separators ("textures//default_pink_shader//..."); the toc does not.
    return {'textureUnit%d' % (FIRST_MATERIAL_UNIT + unit): re.sub('/+', '/', slots.get(key) or default)
            for unit, key, default in rows}


class NeutralEngine(sm5.Bindings):
    """NEUTRAL_INTEGERS/NEUTRAL_FLOATS bound as constants; ``material`` (cb2 floats) bound when given."""

    def __init__(self, material=None):
        self.graph = None
        self.material = {constant_name(i): v for i, v in enumerate(material or ())}

    def constant(self, name):
        if name in NEUTRAL_INTEGERS:
            return self.graph.const('i', NEUTRAL_INTEGERS[name])
        if name in NEUTRAL_FLOATS:
            return self.graph.const('f', NEUTRAL_FLOATS[name])
        if name in self.material:
            return self.graph.const('f', self.material[name])
        return None


def translate(shader, material=None):
    """Translate a pass program under the neutral engine profile (and the material's cb2 when given)."""
    bindings = NeutralEngine(material)
    translator = sm5.Translator(shader, bindings)
    bindings.graph = translator.g
    return translator.run()
