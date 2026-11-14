"""Direct3D shader containers (DXBC): chunks, reflection and the Shader Model 4/5 token stream.

The container is the 'DXBC' header (16-byte hash, u32 version 1, u32 total size, u32 chunk count, chunk
offsets) followed by (fourcc, u32 size, payload) chunks. SM5 programs carry RDEF (reflection), ISGN/OSGN
(or OSG5) signatures and SHEX; the SM6 copies carry DXIL instead, which this module only identifies.

The token encoding follows Microsoft's d3d11TokenizedProgramFormat.hpp (Windows driver kit): an opcode
token (type in bits 0..10, controls in 11..23, length in DWORDs in 24..30, extended bit 31) and operand
tokens (component count 0..1, selection mode 2..3, mask/swizzle 4..11, type 12..19, index dimension
20..21, index representations 22..30, extended bit 31). Decoding is checked against d3dcompiler_47's
disassembler over the Rift Apart corpus (tests/test_dxbc.py); nothing here emulates a shader.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

PROGRAM_TYPES = {0: 'ps', 1: 'vs', 2: 'gs', 3: 'hs', 4: 'ds', 5: 'cs', 0xFFF0: 'lib'}

OPCODES = (
    'add and break breakc call callc case continue continuec cut default deriv_rtx deriv_rty discard div dp2 dp3 dp4 '
    'else emit emitthencut endif endloop endswitch eq exp frc ftoi ftou ge iadd if ieq ige ilt imad imax imin imul ine '
    'ineg ishl ishr itof label ld ld_ms log loop lt mad min max customdata mov movc mul ne nop not or resinfo ret retc '
    'round_ne round_ni round_pi round_z rsq sample sample_c sample_c_lz sample_l sample_d sample_b sqrt switch sincos '
    'udiv ult uge umul umad umax umin ushr utof xor dcl_resource dcl_constantbuffer dcl_sampler dcl_index_range '
    'dcl_outputtopology dcl_inputprimitive dcl_maxout dcl_input dcl_input_sgv dcl_input_siv dcl_input_ps '
    'dcl_input_ps_sgv dcl_input_ps_siv dcl_output dcl_output_sgv dcl_output_siv dcl_temps dcl_indexabletemp '
    'dcl_globalflags reserved0 lod gather4 sample_pos sample_info reserved1 hs_decls hs_control_point_phase '
    'hs_fork_phase hs_join_phase emit_stream cut_stream emitthencut_stream interface_call bufinfo deriv_rtx_coarse '
    'deriv_rtx_fine deriv_rty_coarse deriv_rty_fine gather4_c gather4_po gather4_po_c rcp f32tof16 f16tof32 uaddc '
    'usubb countbits firstbit_hi firstbit_lo firstbit_shi ubfe ibfe bfi bfrev swapc dcl_stream dcl_function_body '
    'dcl_function_table dcl_interface dcl_input_control_point_count dcl_output_control_point_count dcl_tessellator_domain '
    'dcl_tessellator_partitioning dcl_tessellator_output_primitive dcl_hs_max_tessfactor '
    'dcl_hs_fork_phase_instance_count dcl_hs_join_phase_instance_count dcl_thread_group dcl_uav_typed dcl_uav_raw '
    'dcl_uav_structured dcl_tgsm_raw dcl_tgsm_structured dcl_resource_raw dcl_resource_structured ld_uav_typed '
    'store_uav_typed ld_raw store_raw ld_structured store_structured atomic_and atomic_or atomic_xor atomic_cmp_store '
    'atomic_iadd atomic_imax atomic_imin atomic_umax atomic_umin imm_atomic_alloc imm_atomic_consume imm_atomic_iadd '
    'imm_atomic_and imm_atomic_or imm_atomic_xor imm_atomic_exch imm_atomic_cmp_exch imm_atomic_imax imm_atomic_imin '
    'imm_atomic_umax imm_atomic_umin sync dadd dmax dmin dmul deq dge dlt dne dmov dmovc dtof ftod eval_snapped '
    'eval_sample_index eval_centroid dcl_gsinstances abort debug_break reserved_11_1 ddiv dfma drcp msad dtoi dtou itod '
    'utod reserved_11_2 gather4_feedback gather4_c_feedback gather4_po_feedback gather4_po_c_feedback ld_feedback '
    'ld_ms_feedback ld_uav_typed_feedback ld_raw_feedback ld_structured_feedback sample_l_feedback sample_c_lz_feedback '
    'sample_clamp_feedback sample_b_feedback sample_d_feedback sample_c_clamp_feedback check_access_fully_mapped').split()
OPCODE = {name: i for i, name in enumerate(OPCODES)}

OPERAND_TYPES = (
    'r v o x l d s t cb icb label vPrim oDepth null rasterizer oMask stream function_body function_table interface '
    'function_input function_output vOutputControlPointID vForkInstanceID vJoinInstanceID vicp vocp vpc vDomain this '
    'u g vThreadID vThreadGroupID vThreadIDInGroup vCoverage vThreadIDInGroupFlattened vGSInstanceID oDepthGE oDepthLE '
    'vCycleCounter oStencilRef vInnerCoverage').split()

RESOURCE_DIMENSIONS = ('unknown buffer texture1d texture2d texture2dms texture3d texturecube texture1darray '
                       'texture2darray texture2dmsarray texturecubearray raw_buffer structured_buffer').split()
RETURN_TYPES = (None, 'unorm', 'snorm', 'sint', 'uint', 'float', 'mixed', 'double', 'continued')
INTERPOLATION = ('undefined constant linear linear_centroid linear_noperspective linear_noperspective_centroid '
                 'linear_sample linear_noperspective_sample').split()
SYSTEM_VALUES = {0: None, 1: 'position', 2: 'clip_distance', 3: 'cull_distance', 4: 'rendertarget_array_index',
                 5: 'viewport_array_index', 6: 'vertex_id', 7: 'primitive_id', 8: 'instance_id', 9: 'is_front_face',
                 10: 'sample_index', 64: 'target', 65: 'depth', 66: 'coverage', 67: 'depth_ge', 68: 'depth_le'}
COMPONENT_TYPES = (None, 'uint', 'sint', 'float')
MODIFIERS = (None, 'neg', 'abs', 'absneg')
# Opcodes whose bit 18 selects "nonzero" (_nz) instead of "zero" (_z).
CONDITIONAL = {'breakc', 'callc', 'continuec', 'discard', 'if', 'retc'}
SATURATE_BIT = 1 << 13
DXBC_MAGIC = b'DXBC'


class DxbcError(ValueError):
    pass


def chunks(blob):
    """{fourcc: payload bytes} of one DXBC container (a repeated fourcc keeps its first payload)."""
    if blob[:4] != DXBC_MAGIC or len(blob) < 32:
        raise DxbcError('Not a DXBC container')
    size, count = struct.unpack_from('<II', blob, 24)
    if size > len(blob) or 32 + 4 * count > size:
        raise DxbcError('DXBC container is truncated')
    result = {}
    for offset in struct.unpack_from('<%dI' % count, blob, 32):
        if offset + 8 > size:
            raise DxbcError('DXBC chunk outside its container')
        fourcc, length = blob[offset:offset + 4].decode('latin-1'), struct.unpack_from('<I', blob, offset + 4)[0]
        if offset + 8 + length > size:
            raise DxbcError('DXBC chunk %s outside its container' % fourcc)
        result.setdefault(fourcc, blob[offset + 8:offset + 8 + length])
    return result


def find_containers(data, start=0, stop=None):
    """[(offset, size)] of the DXBC containers laid back to back or scattered in ``data``."""
    stop = len(data) if stop is None else stop
    found, at = [], data.find(DXBC_MAGIC, start, stop)
    while at >= 0 and at + 32 <= stop:
        size = struct.unpack_from('<I', data, at + 24)[0]
        if struct.unpack_from('<I', data, at + 20)[0] == 1 and 32 <= size <= stop - at:
            found.append((at, size))
            at = data.find(DXBC_MAGIC, at + size, stop)
        else:
            at = data.find(DXBC_MAGIC, at + 4, stop)
    return found


def _cstring(data, offset):
    end = data.find(b'\0', offset)
    if offset >= len(data) or end < 0:
        raise DxbcError('Reflection string outside its chunk')
    return data[offset:end].decode('latin-1')


# --------------------------------------------------------------------------------------------- reflection

@dataclass
class Variable:
    name: str
    offset: int
    size: int
    type_class: int
    type: int
    rows: int
    columns: int
    elements: int
    members: list = field(default_factory=list)   # [(name, offset, Variable)] of a struct


@dataclass
class ConstantBuffer:
    name: str
    size: int
    kind: int
    variables: list


@dataclass
class Binding:
    name: str
    input_type: int        # D3D_SHADER_INPUT_TYPE: 0 cbuffer, 2 texture, 3 sampler, 5 structured, 4/6.. UAVs
    return_type: int
    dimension: int
    samples: int
    point: int
    count: int
    flags: int
    space: int = 0


@dataclass
class Reflection:
    constant_buffers: list
    bindings: list
    program_type: int
    version: tuple

    def binding(self, input_type, point):
        """The binding of a register (input type class: 'cb', 't', 's' or 'u'), or None."""
        classes = {'cb': {0}, 't': {2, 5, 7, 12}, 's': {3}, 'u': {4, 6, 8, 9, 10, 11, 13}}[input_type]
        for b in self.bindings:
            if b.input_type in classes and b.point <= point < b.point + max(1, b.count):
                return b
        return None

    def constant_buffer(self, name):
        return next((c for c in self.constant_buffers if c.name == name), None)


def _type(data, offset, depth=0):
    if depth > 16:
        raise DxbcError('Reflection type nests too deeply')
    cls, kind, rows, columns, elements, member_count, member_offset = struct.unpack_from('<6HI', data, offset)
    members = []
    for i in range(member_count):
        name, type_offset, at = struct.unpack_from('<3I', data, member_offset + 12 * i)
        members.append((_cstring(data, name), at, _type(data, type_offset, depth + 1)))
    return cls, kind, rows, columns, elements, members


def reflection(rdef):
    """Constant buffers (with their variables and struct members) and resource bindings of an RDEF chunk."""
    cb_count, cb_offset, bind_count, bind_offset, minor, major, program_type, _flags, _creator = \
        struct.unpack_from('<4I2BH2I', rdef, 0)
    variable_size, binding_size = 24, 32
    if major >= 5 and rdef[28:32] == b'RD11':
        # 'RD11', then u32 header size (60), constant buffer, binding, variable, type and member record sizes.
        binding_size, variable_size = struct.unpack_from('<2I', rdef, 40)
    bindings = []
    for i in range(bind_count):
        at = bind_offset + binding_size * i
        name, *rest = struct.unpack_from('<8I', rdef, at)
        space = struct.unpack_from('<I', rdef, at + 32)[0] if binding_size >= 40 else 0
        bindings.append(Binding(_cstring(rdef, name), *rest, space=space))
    buffers = []
    for i in range(cb_count):
        name, var_count, var_offset, size, _flags, kind = struct.unpack_from('<6I', rdef, cb_offset + 24 * i)
        variables = []
        for j in range(var_count):
            at = var_offset + variable_size * j
            vname, start, vsize, _vflags, type_offset = struct.unpack_from('<5I', rdef, at)
            cls, vkind, rows, columns, elements, members = _type(rdef, type_offset)
            variables.append(Variable(_cstring(rdef, vname), start, vsize, cls, vkind, rows, columns, elements,
                                      _flatten(members)))
        buffers.append(ConstantBuffer(_cstring(rdef, name), size, kind, variables))
    return Reflection(buffers, bindings, program_type, (major, minor))


def _flatten(members):
    return [(name, at, Variable(name, at, 0, cls, kind, rows, columns, elements, _flatten(sub)))
            for name, at, (cls, kind, rows, columns, elements, sub) in members]


@dataclass
class SignatureElement:
    name: str
    index: int
    system_value: int
    component_type: int
    register: int
    mask: int
    used: int
    stream: int = 0

    @property
    def semantic(self):
        return '%s%d' % (self.name, self.index) if self.index else self.name


def signature(payload, fourcc):
    """Elements of an ISGN/OSGN (24 bytes each), OSG5 (28, stream first) or ISG1/OSG1 (32) chunk."""
    count = struct.unpack_from('<I', payload, 0)[0]
    stride = {'ISGN': 24, 'OSGN': 24, 'PCSG': 24, 'OSG5': 28, 'ISG1': 32, 'OSG1': 32, 'PSG1': 32}[fourcc]
    elements = []
    for i in range(count):
        at = 8 + stride * i
        stream = 0
        if stride >= 28:
            stream = struct.unpack_from('<I', payload, at)[0]
            at += 4
        name, index, sv, ctype, register = struct.unpack_from('<5I', payload, at)
        mask, used = payload[at + 20], payload[at + 21]
        elements.append(SignatureElement(_cstring(payload, name), index, sv, ctype, register, mask, used, stream))
    return elements


# --------------------------------------------------------------------------------------------- token stream

@dataclass
class Operand:
    type: str
    indices: tuple = ()        # per dimension: (immediate, relative Operand or None)
    components: int = 4        # 0, 1 or 4 (N-component operands are rejected)
    mode: str = 'swizzle'      # 'mask', 'swizzle' or 'select'
    mask: int = 0xF            # write mask (mode 'mask')
    swizzle: tuple = (0, 1, 2, 3)
    modifier: str | None = None
    values: tuple = ()         # literal dwords of an immediate operand
    nonuniform: bool = False

    def register(self):
        """The first index when it is a plain immediate (r3, cb2, t5), else None."""
        if not self.indices:
            return None
        imm, rel = self.indices[0]
        return imm if rel is None else None

    def selected(self):
        """The four source components this operand reads, in destination lane order."""
        if self.components == 1 and self.mode != 'select':
            return (0, 0, 0, 0)
        if self.mode == 'select':
            return (self.swizzle[0],) * 4
        if self.mode == 'mask':
            return (0, 1, 2, 3)
        return self.swizzle

    def __str__(self):
        if self.type == 'l':
            return 'l(%s)' % ', '.join('0x%08x' % v for v in self.values)
        text = self.type
        for k, (imm, rel) in enumerate(self.indices):
            inner = str(imm) if rel is None else ('%s + %d' % (rel, imm) if imm else str(rel))
            text += inner if (k == 0 and rel is None and self.type not in ('cb', 'icb', 'x')
                              and len(self.indices) == 1) else '[%s]' % inner
        if self.components == 4:
            if self.mode == 'mask':
                text += '.' + ''.join('xyzw'[i] for i in range(4) if self.mask >> i & 1) if self.mask != 0xF else ''
            elif self.mode == 'select':
                text += '.' + 'xyzw'[self.swizzle[0]]
            else:
                text += '.' + ''.join('xyzw'[i] for i in self.swizzle)
        if self.modifier in ('abs', 'absneg'):
            text = '|%s|' % text
        if self.modifier in ('neg', 'absneg'):
            text = '-' + text
        return text


@dataclass
class Instruction:
    opcode: str
    operands: list
    saturate: bool = False
    nonzero: bool = False             # conditional opcodes: test nonzero (_nz) rather than zero (_z)
    controls: int = 0                 # raw opcode-specific control bits 11..23
    offsets: tuple = (0, 0, 0)        # sample offsets from an extended token
    resource_dimension: int | None = None
    return_types: tuple | None = None
    data: tuple = ()                  # declaration payload dwords that are not operands
    length: int = 0
    position: int = 0                 # DWORD index of the opcode token in the program

    def __str__(self):
        name = self.opcode + ('_sat' if self.saturate else '')
        if self.opcode in CONDITIONAL:
            name += '_nz' if self.nonzero else '_z'
        return (name + ' ' + ', '.join(str(o) for o in self.operands)).strip()


@dataclass
class Program:
    type: str
    version: tuple
    declarations: list
    instructions: list
    immediate_constants: tuple = ()   # dwords of dcl_immediateConstantBuffer


def _operand(tokens, at):
    token = tokens[at]
    at += 1
    count_code = token & 3
    if count_code == 3:
        raise DxbcError('N-component operand')
    components = (0, 1, 4)[count_code]
    mode_code = (token >> 2) & 3
    operand = Operand(type=OPERAND_TYPES[(token >> 12) & 0xFF] if ((token >> 12) & 0xFF) < len(OPERAND_TYPES)
                      else 'type%d' % ((token >> 12) & 0xFF), components=components)
    if components == 4:
        operand.mode = ('mask', 'swizzle', 'select')[mode_code] if mode_code < 3 else 'swizzle'
        if operand.mode == 'mask':
            operand.mask = (token >> 4) & 0xF
        elif operand.mode == 'swizzle':
            operand.swizzle = tuple((token >> (4 + 2 * i)) & 3 for i in range(4))
        else:
            operand.swizzle = ((token >> 4) & 3,) * 4
    if token & 0x80000000:
        extended = tokens[at]
        at += 1
        if extended & 0x3F == 1:
            operand.modifier = MODIFIERS[(extended >> 6) & 0xFF] if ((extended >> 6) & 0xFF) < 4 else None
            operand.nonuniform = bool(extended >> 17 & 1)
        if extended & 0x80000000:
            raise DxbcError('Chained extended operand tokens')
    dimension = (token >> 20) & 3
    indices = []
    for k in range(dimension):
        representation = (token >> (22 + 3 * k)) & 7
        imm, rel = 0, None
        if representation in (0, 3):
            imm = tokens[at]
            at += 1
        elif representation in (1, 4):
            imm = tokens[at] | tokens[at + 1] << 32
            at += 2
        if representation in (2, 3, 4):
            rel, at = _operand(tokens, at)
        if representation > 4:
            raise DxbcError('Unknown operand index representation %d' % representation)
        indices.append((imm, rel))
    operand.indices = tuple(indices)
    if operand.type == 'l':
        operand.values = tuple(tokens[at:at + components])
        at += components
    elif operand.type == 'd':
        operand.values = tuple(tokens[at:at + 2 * components])
        at += 2 * components
    return operand, at


# Declarations whose payload after the opcode token is plain dwords, not an operand (or operand + dwords).
_RAW_DECLARATIONS = {'dcl_temps', 'dcl_globalflags', 'dcl_maxout', 'dcl_gsinstances', 'dcl_thread_group',
                     'dcl_input_control_point_count', 'dcl_output_control_point_count', 'dcl_hs_max_tessfactor',
                     'dcl_hs_fork_phase_instance_count', 'dcl_hs_join_phase_instance_count', 'dcl_indexabletemp',
                     'dcl_function_body', 'dcl_function_table', 'dcl_interface', 'dcl_outputtopology',
                     'dcl_inputprimitive', 'dcl_tessellator_domain', 'dcl_tessellator_partitioning',
                     'dcl_tessellator_output_primitive', 'hs_decls', 'hs_control_point_phase', 'hs_fork_phase',
                     'hs_join_phase', 'dcl_stream'}
# Declarations with one operand followed by trailing dwords (return types, strides, interpolation...).
_TRAILING = {'dcl_resource': 1, 'dcl_uav_typed': 1, 'dcl_resource_structured': 1, 'dcl_uav_structured': 1,
             'dcl_tgsm_raw': 1, 'dcl_tgsm_structured': 2, 'dcl_input_sgv': 1, 'dcl_input_siv': 1,
             'dcl_input_ps_sgv': 1, 'dcl_input_ps_siv': 1, 'dcl_output_sgv': 1, 'dcl_output_siv': 1,
             'dcl_index_range': 1}


def program(shex):
    """Decode an SHEX/SHDR chunk: declarations and instructions with their operands."""
    if len(shex) < 8 or len(shex) % 4:
        raise DxbcError('Program chunk has a bad size')
    tokens = struct.unpack('<%dI' % (len(shex) // 4), shex)
    version, length = tokens[0], tokens[1]
    if length > len(tokens):
        raise DxbcError('Program length past its chunk')
    kind = PROGRAM_TYPES.get(version >> 16, 'type%d' % (version >> 16))
    declarations, instructions, icb = [], [], ()
    at = 2
    while at < length:
        token = tokens[at]
        code = token & 0x7FF
        if code >= len(OPCODES):
            raise DxbcError('Unknown opcode %d at dword %d' % (code, at))
        name = OPCODES[code]
        if name == 'customdata':
            size = tokens[at + 1]
            if size < 2 or at + size > length:
                raise DxbcError('Custom data block outside the program')
            if token >> 11 == 3:   # D3D10_SB_CUSTOMDATA_DCL_IMMEDIATE_CONSTANT_BUFFER
                icb = tuple(tokens[at + 2:at + size])
            at += size
            continue
        size = (token >> 24) & 0x7F
        if size == 0:
            raise DxbcError('Zero-length %s at dword %d' % (name, at))
        end = at + size
        if end > length:
            raise DxbcError('%s at dword %d runs past the program' % (name, at))
        ins = Instruction(name, [], controls=(token >> 11) & 0x1FFF, length=size, position=at)
        ins.saturate = bool(token & SATURATE_BIT) and not name.startswith('dcl_')
        ins.nonzero = bool(token >> 18 & 1)
        cursor = at + 1
        extended = token & 0x80000000
        while extended:
            ext = tokens[cursor]
            cursor += 1
            kind_code = ext & 0x3F
            if kind_code == 1:
                ins.offsets = tuple(((ext >> s) & 0xF) - (16 if (ext >> s) & 0x8 else 0) for s in (9, 13, 17))
            elif kind_code == 2:
                ins.resource_dimension = (ext >> 6) & 0x1F
            elif kind_code == 3:
                ins.return_types = tuple((ext >> (6 + 4 * i)) & 0xF for i in range(4))
            extended = ext & 0x80000000
        if name in _RAW_DECLARATIONS:
            ins.data = tokens[cursor:end]
        elif name.startswith('dcl_') or name == 'dcl_resource':
            trailing = _TRAILING.get(name, 0)
            if name in ('dcl_uav_raw', 'dcl_resource_raw', 'dcl_sampler', 'dcl_constantbuffer', 'dcl_input',
                        'dcl_input_ps', 'dcl_output') or trailing:
                operand, cursor = _operand(tokens, cursor)
                ins.operands = [operand]
            ins.data = tokens[cursor:end]
        else:
            while cursor < end:
                operand, cursor = _operand(tokens, cursor)
                ins.operands.append(operand)
            if cursor != end:
                raise DxbcError('%s operands overrun the instruction at dword %d' % (name, at))
        (declarations if name.startswith('dcl_') else instructions).append(ins)
        at = end
    return Program(kind, ((version >> 4) & 0xF, version & 0xF), declarations, instructions, icb)


@dataclass
class Shader:
    """One SM5 DXBC program with its reflection and signatures."""
    program: Program
    reflection: Reflection | None
    inputs: list
    outputs: list

    @property
    def stage(self):
        return self.program.type


def is_dxil(blob):
    return 'DXIL' in chunks(blob)


def shader(blob):
    """Decode an SM4/SM5 DXBC container (raises DxbcError for DXIL or malformed data)."""
    parts = chunks(blob)
    code = parts.get('SHEX') or parts.get('SHDR')
    if code is None:
        raise DxbcError('DXBC container has no SM4/SM5 program (DXIL)' if 'DXIL' in parts else 'No program chunk')
    sig_in = next((signature(parts[k], k) for k in ('ISGN', 'ISG1') if k in parts), [])
    sig_out = next((signature(parts[k], k) for k in ('OSGN', 'OSG5', 'OSG1') if k in parts), [])
    return Shader(program(code), reflection(parts['RDEF']) if 'RDEF' in parts else None, sig_in, sig_out)
