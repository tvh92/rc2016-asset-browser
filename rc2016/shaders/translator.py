"""Native PS4 GCN -> scalar DAG -> deferred material outputs.

No graph-name, shader-ID or code-hash recipe dispatch. Hashes identify source
bytes only. Unsupported reachable instructions/branches remain diagnostics.

Forward branches and the exec mask are linearised by predication: a block
predicate (uniform control flow) and the exec predicate (per-lane activity)
are 0/1 DAG values, and each register write merges as select(active, new, old).
Branches on exec state are resolved for an active lane, which assumes scalar
state written inside an exec-skipped region is not read after it, as in
structured compiler output. Backward branches (loops) remain unsupported.
"""
from collections import Counter
import hashlib
import math
from pathlib import Path
import re
import struct
import sys

from rc2016.shaders.ir import DAG, bits, from_bits

VERSION=6
ROOT=Path(__file__).resolve().parents[2]
SHADER_SECTION=0x873D1E6C
# VALU instructions that also write a scalar carry/flag register pair.
CARRY_OUT={'V_ADD_I32','V_SUB_I32','V_SUBREV_I32','V_ADDC_U32','V_SUBB_U32','V_SUBBREV_U32',
           'V_DIV_SCALE_F32','V_DIV_SCALE_F64'}

# Variants that differ from the base opcode only for zero times infinity/NaN
# (DX9 legacy multiply), NaN operands, or by clamping an infinite result to
# the largest finite float. The IR has finite values only, where they agree.
MAX_PREDICATE_ATOMS=12
PREDICATE_OPS={'mul','max','sub','select'}
MASK_OP=re.compile(r'S_(AND|OR|XOR|ANDN2|ORN2|NAND|NOR|XNOR|MOV|NOT)(_SAVEEXEC)?_B64')
UNIFORM_INT_OPS={'u_and','u_or','u_xor','u_andn2','u_shl','u_shr','u_eq','u_lt','u_le','i_lt','i_le','u_bit'}
UNIFORM_SOP2={'S_AND_B32':'u_and','S_OR_B32':'u_or','S_XOR_B32':'u_xor','S_ANDN2_B32':'u_andn2',
              'S_LSHL_B32':'u_shl','S_LSHR_B32':'u_shr'}
FINITE_ALIASES={'V_MUL_LEGACY_F32':'V_MUL_F32','V_MAC_LEGACY_F32':'V_MAC_F32',
                'V_MAD_LEGACY_F32':'V_MAD_F32','V_MIN_LEGACY_F32':'V_MIN_F32',
                'V_MAX_LEGACY_F32':'V_MAX_F32','V_RCP_CLAMP_F32':'V_RCP_F32',
                'V_RSQ_CLAMP_F32':'V_RSQ_F32','V_LOG_CLAMP_F32':'V_LOG_F32'}


def decoder():
    # The GCN field decoder, cross-checked against opengnm's decoder.
    from rc2016.shaders import gcn_fields
    return gcn_fields


def sections(data):
    if len(data)<16 or struct.unpack_from('<I',data)[0]!=0x44415431:
        raise ValueError('Invalid DAT1 shader graph')
    declared,count=struct.unpack_from('<IH',data,8)
    if declared!=len(data) or 16+count*12>len(data):raise ValueError('DAT1 bounds')
    result={}
    for i in range(count):
        tag,off,size=struct.unpack_from('<III',data,16+12*i)
        if tag in result or off<16+count*12 or off+size>len(data):raise ValueError('DAT1 section bounds')
        result[tag]=(off,size)
    return result


def pixel_programs(data):
    spans=sections(data)
    if SHADER_SECTION not in spans:return []
    lo,size=spans[SHADER_SECTION];hi=lo+size
    result=[]
    starts=[lo+m.start() for m in re.finditer(b'Shdr',data[lo:hi]) if m.start()<3 or data[lo+m.start()-3:lo+m.start()]!=b'Orb']
    for pos in starts:
        if pos+16>hi:raise ValueError('Truncated Shdr')
        if data[pos+8]!=2:continue
        count=data[pos+19]
        stage=pos+16
        usage=stage+0x3c
        input_count=data[stage+0x38]
        stage_size=(0x3c+count*4+input_count*2+3)&~3
        code_start=stage+stage_size
        if data[pos+9]*4!=stage_size:raise ValueError('Shdr stage/header mismatch')
        declared=struct.unpack_from('<I',data,stage)[0]&0x7fffff
        orb=code_start+declared-28
        end=min([x for x in starts if x>pos]+[hi])
        if orb+28>end or data[orb:orb+7]!=b'OrbShdr':raise ValueError('Shdr/OrbShdr boundary mismatch')
        length=struct.unpack_from('<I',data,orb+8)[0]>>8
        if length<=0 or code_start+length>orb:raise ValueError('Native code exceeds envelope')
        slots=[]
        for p in range(usage,usage+count*4,4):
            kind,api,reg,flags=struct.unpack_from('<4B',data,p)
            slots.append(dict(kind=kind,api=api,reg=reg,flags=flags))
        code=data[code_start:code_start+length]
        result.append(dict(header_offset=pos,code_offset=code_start,code_size=len(code),
            code_sha256=hashlib.sha256(code).hexdigest(),shader_id=f'0x{struct.unpack_from("<I",data,orb+16)[0]:08x}',
            usage_slots=slots,code=code))
    return result


class Compiler:
    def __init__(self,code,usage_slots):
        self.code=code;self.ir=DAG();self.v={};self.s={};self.descriptors={};self.exports={}
        self.samples=[];self.diagnostics=[];self.current=0;self.scalar_bits={}
        self.exec=self.ir.const(1.);self.bp=self.ir.const(1.);self.masks={};self.scc=None;self.scc_source='program start'
        self.pending={};self.premerged=set();self.exec_masked_export=False
        self.pred_tables={}
        self.slots=usage_slots
        for slot in usage_slots:
            if slot['reg']<16:
                width=8 if slot['kind']==0 else 4 if slot['kind'] in (1,2) else 2
                for j in range(width):self.descriptors[slot['reg']+j]=dict(slot,word=j)

    def unknown(self,reason,*args):
        return self.ir.node('unsupported',args,reason=reason,offset=self.current)

    def getv(self,i):
        if i not in self.v:self.v[i]=self.unknown(f'Uninitialized VGPR {i}')
        return self.v[i]

    def gets(self,i):
        if i not in self.s:self.s[i]=self.unknown(f'Uninitialized SGPR {i}')
        return self.s[i]

    # Lane and block predicates are 0/1 values built from atoms (comparisons,
    # uniform conditions) with and (mul), or (max), not (1 - x) and select.
    # Each result is folded by its truth table, held as a bitmask over atom
    # assignments, so a restored exec mask is the constant 1 again and equal
    # predicates share a node.
    def predicate(self,value):
        if self.ir.value(value) is not None or self.ir.nodes[value]['op'] not in PREDICATE_OPS:return value
        atoms=set();pending=[value];seen=set()
        while pending:
            i=pending.pop()
            if i in seen or self.ir.value(i) is not None:continue
            seen.add(i)
            if self.ir.nodes[i]['op'] in PREDICATE_OPS:pending.extend(self.ir.nodes[i]['args'])
            else:atoms.add(i)
        if len(atoms)>MAX_PREDICATE_ATOMS:return value
        atoms=sorted(atoms);size=1<<len(atoms);full=(1<<size)-1
        tables={a:sum(1<<k for k in range(size) if (k>>j)&1) for j,a in enumerate(atoms)}
        def table(i):
            if i in tables:return tables[i]
            constant=self.ir.value(i)
            if constant is not None:
                if constant not in (0,1):raise ValueError('non-boolean')
                return full if constant else 0
            n=self.ir.nodes[i];a=n['args']
            if n['op']=='mul':r=table(a[0])&table(a[1])
            elif n['op']=='max':r=table(a[0])|table(a[1])
            elif n['op']=='sub' and self.ir.value(a[0])==1:r=full^table(a[1])
            elif n['op']=='select':p=table(a[0]);r=(p&table(a[1]))|((full^p)&table(a[2]))
            else:raise ValueError('non-boolean')
            tables[i]=r;return r
        try:result=table(value)
        except ValueError:return value
        if result in (0,full):return self.ir.const(float(result==full))
        # Canonical key over the atoms the function depends on.
        used=[j for j in range(len(atoms)) if ((result>>(1<<j))&full&~tables[atoms[j]])!=(result&full&~tables[atoms[j]])]
        reduced=0
        for k in range(1<<len(used)):
            source=sum(1<<used[q] for q in range(len(used)) if (k>>q)&1)
            reduced|=((result>>source)&1)<<k
        key=(tuple(atoms[j] for j in used),reduced)
        if len(used)==1 and reduced==0b10:return atoms[used[0]]
        return self.pred_tables.setdefault(key,value)

    def p_and(self,a,b):return self.predicate(self.ir.op('mul',a,b))

    def p_not(self,a):return self.predicate(self.ir.op('sub',self.ir.const(1.),a))

    def p_or(self,a,b):return self.predicate(self.ir.op('max',a,b))

    def p_xor(self,a,b):
        return self.predicate(self.ir.op('max',self.ir.op('mul',a,self.ir.op('sub',self.ir.const(1.),b)),
                                         self.ir.op('mul',b,self.ir.op('sub',self.ir.const(1.),a))))

    def p_select(self,p,a,b):return self.predicate(self.ir.op('select',p,a,b))

    def mask_high(self,mask):
        return self.ir.node('unsupported',[mask],reason='Lane mask high word')

    def mask(self,raw):
        """Lane predicate held by a 64-bit scalar operand, or None."""
        if raw==126:return self.exec
        if raw==128:return self.ir.const(0.)
        if raw==193:return self.ir.const(1.)
        m=self.masks.get(raw)
        if m is not None and self.s.get(raw)==m and self.s.get(raw+1)==self.mask_high(m):return m
        return None

    def set_mask(self,dst,value):
        old=self.mask(dst)
        if old is not None:value=self.p_select(self.bp,value,old)
        for reg in (dst,dst+1):self.descriptors.pop(reg,None);self.scalar_bits.pop(reg,None)
        self.s[dst]=value;self.s[dst+1]=self.mask_high(value);self.masks[dst]=value
        self.premerged.update((dst,dst+1))

    def set_exec(self,value):
        self.exec=self.p_select(self.bp,value,self.exec)

    def mask_op(self,k,name,f):
        """Exact lane-mask algebra for 64-bit scalar logic on known masks; False if not applicable."""
        m=MASK_OP.fullmatch(name)
        if not m:return False
        op,saveexec=m.group(1),bool(m.group(2))
        dst=f['dst'][0];raws=f['src'][:1 if k==0 else 2];srcs=[self.mask(r) for r in raws]
        if any(x is None for x in srcs):return False
        # Integer 64-bit constants (address or descriptor words) keep the scalar path.
        if dst!=126 and not saveexec and all(r in (128,193) for r in raws):return False
        binary={'AND':lambda a,b:self.p_and(a,b),'OR':self.p_or,'XOR':self.p_xor,
                'ANDN2':lambda a,b:self.p_and(a,self.p_not(b)),'ORN2':lambda a,b:self.p_or(a,self.p_not(b)),
                'NAND':lambda a,b:self.p_not(self.p_and(a,b)),'NOR':lambda a,b:self.p_not(self.p_or(a,b)),
                'XNOR':lambda a,b:self.p_not(self.p_xor(a,b))}
        if saveexec:
            # D = EXEC; EXEC = S0 op EXEC; SCC = (EXEC != 0).
            if op not in binary or dst==126:return False
            old=self.exec;self.set_mask(dst,old);self.set_exec(binary[op](srcs[0],old));self.scc=('exec',)
            return True
        if k==0 and op in ('MOV','NOT'):
            value=srcs[0] if op=='MOV' else self.p_not(srcs[0])
        elif k==1 and op in binary:value=binary[op](*srcs)
        else:return False
        if dst==126:self.set_exec(value)
        else:self.set_mask(dst,value)
        if op!='MOV':
            self.scc=('exec',) if dst==126 else None;self.scc_source=name+' (lane mask)'
        return True

    def scalar_operand_bits(self,raw,literal_bits):
        if raw<=127:return self.scalar_bits.get(raw)
        if raw==255:return literal_bits
        return self.inline_bits(raw)

    def uniform(self,raw,literal_bits):
        """Uniform 32-bit scalar as an F32-bit-pattern node: known bits, a constant-buffer word or a uniform op."""
        known=self.scalar_operand_bits(raw,literal_bits)
        if known is not None:
            value=from_bits(known)
            return self.ir.const(value) if math.isfinite(value) else None
        node=self.s.get(raw) if raw<=127 else None
        if node is None:return None
        n=self.ir.nodes[node]
        return node if (n['op']=='input' and n.get('kind')=='cb') or n['op'] in UNIFORM_INT_OPS else None

    def uniform_condition(self,name,a,b):
        """SCC of S_CMP(K)/S_BITCMP on uniform operands, or None."""
        if a is None or b is None:return None
        if name.startswith('S_BITCMP'):
            bit=self.ir.op('u_bit',a,b)
            return bit if name.startswith('S_BITCMP1') else self.p_not(bit)
        m=re.match(r'S_CMPK?_(EQ|LG|LT|LE|GT|GE)_([UI])32$',name)
        if not m:return None
        relation,kind=m.groups();lt,le=('u_lt','u_le') if kind=='U' else ('i_lt','i_le')
        if relation in ('EQ','LG'):
            c=self.ir.op('u_eq',a,b);return c if relation=='EQ' else self.p_not(c)
        return {'LT':lambda:self.ir.op(lt,a,b),'LE':lambda:self.ir.op(le,a,b),
                'GT':lambda:self.ir.op(lt,b,a),'GE':lambda:self.ir.op(le,b,a)}[relation]()

    def uniform_sop(self,k,name,f,i):
        """Exact SOP2 integer logic on uniform values; SCC = result != 0. False if not applicable."""
        if k!=1 or name not in UNIFORM_SOP2:return False
        literal_bits=struct.unpack_from('<I',self.code,i['relative']+4)[0] if i['length']>4 else None
        a,b=(self.uniform(r,literal_bits) for r in f['src'][:2])
        if a is None or b is None:return False
        dst=f['dst'][0];value=self.ir.op(UNIFORM_SOP2[name],a,b);constant=self.ir.value(value)
        self.descriptors.pop(dst,None)
        if constant is not None:
            # Keep known words in the integer form scalar moves use, for addressing.
            word=bits(constant);self.s[dst]=self.ir.const(word);self.scalar_bits[dst]=word
            self.scc=('uniform',self.ir.const(float(word!=0)))
        else:
            self.s[dst]=value;self.scalar_bits.pop(dst,None)
            self.scc=('uniform',self.p_not(self.ir.op('u_eq',value,self.ir.const(0.))))
        return True

    def unpack(self,value):
        n=self.ir.nodes[value]
        if n['op']=='pack':return list(n['args'])
        if n['op']=='const' and isinstance(n['value'],float):
            # A packed register written as one 32-bit word: two F16 halves, low first.
            word=bits(n['value']);halves=struct.unpack('<2e',struct.pack('<I',word))
            return [self.ir.const(h) for h in halves] if all(map(math.isfinite,halves)) else None
        if n['op']=='select':
            a,b=self.unpack(n['args'][1]),self.unpack(n['args'][2])
            if a and b:return [self.ir.op('select',n['args'][0],x,y) for x,y in zip(a,b)]
        return None

    def branch(self,i,name,f):
        target=i['relative']+f['branch_target_relative']
        if target<=i['relative']:raise ValueError(f'Backward branch unsupported at {i["relative"]}: {name}')
        one=self.ir.const(1.);zero=self.ir.const(0.)
        # For an active lane exec is nonzero, so exec tests have a fixed outcome.
        if name=='S_BRANCH':condition=one
        elif name=='S_CBRANCH_EXECZ':condition=zero
        elif name=='S_CBRANCH_EXECNZ':condition=one
        elif name in ('S_CBRANCH_SCC0','S_CBRANCH_SCC1'):
            if self.scc==('exec',):condition=one
            elif self.scc and self.scc[0]=='uniform':condition=self.scc[1]
            else:raise ValueError(f'Control flow unsupported at {i["relative"]}: {name} on unresolved SCC from {self.scc_source}')
            if name=='S_CBRANCH_SCC0':condition=self.p_not(condition)
        else:raise ValueError(f'Control flow unsupported at {i["relative"]}: {name}')
        self.pending[target]=self.p_or(self.pending.get(target,zero),self.p_and(self.bp,condition))
        self.bp=self.p_and(self.bp,self.p_not(condition))

    def clobber_s(self,start,count,reason):
        """Every SGPR write not modelled exactly must drop known bits/descriptors."""
        for reg in range(start,start+count):
            self.s[reg]=self.unknown(reason);self.descriptors.pop(reg,None);self.scalar_bits.pop(reg,None)

    def float_bits(self,bits):
        value=struct.unpack('<f',struct.pack('<I',bits&0xffffffff))[0]
        return self.ir.const(value) if math.isfinite(value) else self.unknown(f'Non-finite F32 bits 0x{bits&0xffffffff:08x}')

    @staticmethod
    def inline_bits(raw):
        """Raw 32-bit pattern of an inline constant (GCN2 has no 1/(2*pi))."""
        if 128<=raw<=192:return raw-128
        if 193<=raw<=208:return (192-raw)&0xffffffff
        if 240<=raw<=247:return struct.unpack('<I',struct.pack('<f',(.5,-.5,1.,-1.,2.,-2.,4.,-4.)[raw-240]))[0]
        return None

    def operand(self,raw,literal):
        """F32 operand. Integer inline constants are bit patterns, not values."""
        if raw>=256:return self.getv(raw-256)
        if raw<=127:
            if raw in self.scalar_bits:return self.float_bits(self.scalar_bits[raw])
            return self.gets(raw)
        bits=self.inline_bits(raw)
        if bits is not None:return self.float_bits(bits)
        if raw==255:return self.ir.const(literal) if math.isfinite(literal) else self.unknown('Non-finite F32 literal')
        return self.unknown(f'Unsupported encoded operand {raw}')

    def load(self,i):
        w=i['word'];name=i['name'];base=((w>>9)&63)*2;dst=(w>>15)&127
        count=int(name.rsplit('X',1)[1]) if 'DWORDX'in name else 1
        imm=bool(w&256)
        offset=(w&255)*4 if imm else self.ir.value(self.gets(w&255))
        origin=self.descriptors.get(base,{})
        for j in range(count):
            reg=dst+j;self.descriptors.pop(reg,None);self.scalar_bits.pop(reg,None)
            if origin.get('kind')==2 and 'BUFFER_LOAD'in name and offset is not None:
                dword=int(offset)//4+j
                self.s[reg]=self.ir.node('input',name=f"CB{origin['api']}[{dword}]",kind='cb',api=origin['api'],dword=dword)
            elif origin.get('kind')==0x1b and offset is not None:
                slot=next((s for s in self.slots if s['reg']==16+int(offset)//4),None)
                if slot:self.descriptors[reg]=dict(slot,word=j)
                self.s[reg]=self.unknown('Extended descriptor value')
            elif origin.get('kind') in (0x13,0x15) and offset is not None:
                is_resource=origin['kind']==0x13
                self.descriptors[reg]=dict(kind=0 if is_resource else 1,api=int(offset)//(32 if is_resource else 16),word=j)
                self.s[reg]=self.unknown('Descriptor word')
            else:self.s[reg]=self.unknown('Unresolved scalar memory load')

    def instruction(self,i):
        self.current=i['relative'];k=i['kind'];w=i['word'];q=i['wide'];f=i['fields']
        name=i['name'].replace('GCN_','').replace('_E64','')
        if k==3 and not f.get('dst'):
            # The shared decoder exposes no SOPK fields; SDST is bits 22:16.
            # Compares write only SCC and SETREG writes a hardware register.
            f=dict(f,dst=[] if name.startswith(('S_CMPK','S_SETREG')) else [(w>>16)&127])
        if k in (0,1) and self.mask_op(k,name,f):return
        if ('SAVEEXEC' in name or name in ('S_SETPC_B64','S_SWAPPC_B64') or
            (k in (0,1,2,3) and name!='S_WQM_B64' and any(r in (126,127) for r in f.get('dst',[])))):
            raise ValueError(f'Execution-mask/control flow unsupported at {i["relative"]}: {name}')
        if k==2:
            # SOPC: S0 bits 7:0, S1 bits 15:8; only SCC is written.
            literal_bits=struct.unpack_from('<I',self.code,i['relative']+4)[0] if i['length']>4 else None
            c=self.uniform_condition(name,*(self.uniform(r,literal_bits) for r in (w&255,(w>>8)&255)))
            self.scc=('uniform',c) if c is not None else None
            if c is None:self.scc_source=name
            return
        if k==3 and name.startswith('S_CMPK_'):
            simm=w&0xffff
            if name.endswith('_I32'):simm=struct.unpack('<h',struct.pack('<H',simm))[0]&0xffffffff
            c=self.uniform_condition(name,self.uniform((w>>16)&127,None),self.uniform(255,simm))
            self.scc=('uniform',c) if c is not None else None
            if c is None:self.scc_source=name
            return
        if self.uniform_sop(k,name,f,i):return
        if k==1 and name=='S_CSELECT_B32' and self.scc and self.scc[0]=='uniform':
            literal_bits=struct.unpack_from('<I',self.code,i['relative']+4)[0] if i['length']>4 else None
            a,b=(self.uniform(r,literal_bits) for r in f['src'][:2]);dst=f['dst'][0]
            if a is not None and b is not None:
                value=self.ir.op('select',self.scc[1],a,b);constant=self.ir.value(value)
                self.descriptors.pop(dst,None)
                if constant is not None:
                    word=bits(constant);self.s[dst]=self.ir.const(word);self.scalar_bits[dst]=word
                else:self.s[dst]=value;self.scalar_bits.pop(dst,None)
                return
        if k in (0,1,3) and not name.startswith(('S_MOV','S_CMOV','S_GETPC')):self.scc=None;self.scc_source=name
        literal=struct.unpack_from('<f',self.code,i['relative']+i['length']-4)[0] if i['length']>4 else 0.
        if k==10:self.load(i);return
        if k==9:
            attr=(w>>10)&63;chan=(w>>8)&3
            # P1 is P2's partial accumulator; flat V_INTERP_MOV is not a perspective interpolant.
            self.v[f['dst'][0]]=(self.ir.node('input',name=f'attribute{attr}.{chan}',kind='attribute',attribute=attr,channel=chan)
                if 'P2' in name else self.unknown(name))
            return
        if k==14:
            api=self.descriptors.get(((q>>48)&31)*4,{}).get('api')
            mask=(q>>8)&15;coord=(q>>32)&255;dst=(q>>40)&255
            sample=len(self.samples);uv=[self.getv(coord+j) for j in range(2)]
            self.samples.append(dict(api=api,coordinates=uv,offset=i['relative'],opcode=name))
            for j,ch in enumerate(c for c in range(4) if mask&(1<<c)):
                # LZ forces mip 0 with the same u,v address; Blender samples the base level too.
                self.v[dst+j]=(self.ir.node('texture',uv,api=api,sample=sample,channel=ch)
                    if api is not None and name in ('IMAGE_SAMPLE','IMAGE_SAMPLE_LZ') else self.unknown('Unresolved/unsupported image sample'))
            return
        if k==15:
            channels=[]
            if f['compressed']:
                for reg in f['src_registers']:
                    value=self.getv(reg);n=self.ir.nodes[value];pair=self.unpack(value)
                    channels.extend(pair or [self.unknown('Unresolved packed export from '+(n.get('reason') or n['op']))]*2)
            else:channels=[self.getv(reg) for reg in f['src_registers']]
            if self.ir.value(self.exec)!=1:self.exec_masked_export=True
            self.exports[f['target']]=[v if f['enable_mask']&(1<<j) else None for j,v in enumerate(channels)]
            return
        if k==4:
            if name=='S_ENDPGM':self.bp=self.ir.const(0.)
            elif 'BRANCH'in name:self.branch(i,name,f)
            return
        if k in (0,1,2,3):
            dst=f.get('dst',[None])[0]
            if dst is None:return
            count=2 if 'B64'in name else 1
            if name in ('S_MOV_B32','S_MOV_B64'):
                raw=f['src'][0]
                if raw<=127:
                    # Keep integer address/offset values; F32 readers use the copied bits.
                    rows=[(self.gets(raw+j),self.scalar_bits.get(raw+j),self.descriptors.get(raw+j)) for j in range(count)]
                else:
                    # Scalar words keep integer values for addressing and raw bits for F32 readers.
                    bits=struct.unpack_from('<I',self.code,i['relative']+4)[0] if raw==255 else self.inline_bits(raw)
                    if bits is None:value=self.unknown(f'Unsupported encoded operand {raw}')
                    elif raw==255 or raw<=208:value=self.ir.const(bits if raw==255 else raw-128 if raw<=192 else 192-raw)
                    else:value=self.float_bits(bits)
                    rows=[(value,bits,None)]
                    if count==2:
                        # Integer inline constants sign-extend; float/literal 64-bit sources are not modelled.
                        high=(0xffffffff if 193<=raw<=208 else 0) if 128<=raw<=208 else None
                        rows.append((self.ir.const(-1 if high else 0),high,None) if high is not None
                                    else (self.unknown('Unmodelled 64-bit scalar source'),None,None))
                for j,(value,bits,descriptor) in enumerate(rows):
                    self.s[dst+j]=value
                    if bits is not None:self.scalar_bits[dst+j]=bits
                    else:self.scalar_bits.pop(dst+j,None)
                    if descriptor is not None:self.descriptors[dst+j]=dict(descriptor)
                    else:self.descriptors.pop(dst+j,None)
            elif name=='S_MOVK_I32':
                simm=struct.unpack('<h',struct.pack('<H',w&0xffff))[0]
                self.s[dst]=self.ir.const(simm);self.scalar_bits[dst]=simm&0xffffffff;self.descriptors.pop(dst,None)
            elif name=='S_WQM_B64' and dst==126:pass  # whole-quad helpers do not change active lanes' values
            else:self.clobber_s(dst,count,name)
            return
        if k==13 and name in ('TBUFFER_LOAD_FORMAT_X','TBUFFER_LOAD_FORMAT_XY',
                               'TBUFFER_LOAD_FORMAT_XYZ','TBUFFER_LOAD_FORMAT_XYZW'):
            # MTBUF loads write only their component span, plus the optional
            # texture-failure status VGPR (TFE bit 55). Values remain unknown;
            # preserving neighbouring registers does not bind the buffer.
            count={'X':1,'XY':2,'XYZ':3,'XYZW':4}[name.rsplit('_',1)[1]]+((q>>55)&1)
            dst=f['dst'][0]
            for j in range(count):self.v[dst+j]=self.unknown(name)
            return
        if k in (11,12,13):
            dst=f.get('dst',[0])[0]
            for j in range(4):self.v[dst+j]=self.unknown(name)
            return
        if k not in (5,6,7,8):return
        src=list(f['src'])
        if k in (6,8):src[1]+=256
        if name in ('V_MADMK_F32','V_MADAK_F32'):
            # VOP2 embeds a literal while only its first source is a generic operand.
            a=self.operand(src[0],0.);b=self.operand(src[1],0.);c=self.ir.const(literal)
            args=[a,c,b] if name=='V_MADMK_F32' else [a,b,c]
        else:args=[self.operand(r,literal) for r in src]
        if k==7:
            for j,m in enumerate(f['src_modifiers']):
                if m['abs']:args[j]=self.ir.op('abs',args[j])
                if m['neg']:args[j]=self.ir.op('neg',args[j])
        d=f['dst'][0]
        if name in ('V_READFIRSTLANE_B32','V_READLANE_B32'):
            self.clobber_s(d,1,name);return
        if name in CARRY_OUT:
            # VOP2 forms write VCC; VOP3b forms name their SGPR pair in bits 14:8.
            self.clobber_s(106 if k==6 else (q>>8)&127,2,name)
        if 'CMP'in name:
            self.clobber_s(d,2,name)
            cmp=next((op for token,op in [('NE','ne'),('LE','le'),('GE','ge'),('LT','lt'),('GT','gt'),('EQ','eq')] if '_'+token+'_'in name),None)
            value=self.ir.op(cmp,*args[:2]) if cmp else self.unknown(name,*args[:2])
            # Inactive lanes write 0; CMPX also writes the result to EXEC.
            self.set_mask(d,self.p_and(value,self.exec))
            if 'CMPX' in name:self.set_exec(self.p_and(self.exec,value))
            return
        name=FINITE_ALIASES.get(name,name)
        unary={'V_MOV_B32':None,'V_RSQ_F32':'rsqrt','V_RCP_F32':'rcp','V_SQRT_F32':'sqrt',
               'V_LOG_F32':'log2','V_EXP_F32':'exp2','V_COS_F32':'cos','V_SIN_F32':'sin',
               'V_FLOOR_F32':'floor','V_FRACT_F32':'fract'}
        binary={'V_MUL_F32':'mul','V_ADD_F32':'add','V_SUB_F32':'sub','V_MIN_F32':'min','V_MAX_F32':'max'}
        if name in ('V_COS_F32','V_SIN_F32'):
            # GCN trigonometric operands are cycles, Blender/IR operands radians.
            value=self.ir.op(unary[name],self.ir.op('mul',args[0],self.ir.const(2*math.pi)))
        elif name in unary:value=args[0] if unary[name] is None else self.ir.op(unary[name],args[0])
        elif name in binary:value=self.ir.op(binary[name],*args[:2])
        elif name=='V_SUBREV_F32':value=self.ir.op('sub',args[1],args[0])
        elif name=='V_MAC_F32':value=self.ir.op('add',self.getv(d),self.ir.op('mul',*args[:2]))
        elif name in ('V_MAD_F32','V_FMA_F32','V_MADMK_F32','V_MADAK_F32'):
            value=self.ir.op('add',self.ir.op('mul',*args[:2]),args[2])
        elif name=='V_CEIL_F32':value=self.ir.op('neg',self.ir.op('floor',self.ir.op('neg',args[0])))
        elif name=='V_TRUNC_F32':
            value=self.ir.op('select',self.ir.op('ge',args[0],self.ir.const(0.)),self.ir.op('floor',args[0]),
                             self.ir.op('neg',self.ir.op('floor',self.ir.op('neg',args[0]))))
        elif name in ('V_MIN3_F32','V_MAX3_F32'):
            op='min' if name=='V_MIN3_F32' else 'max'
            value=self.ir.op(op,self.ir.op(op,args[0],args[1]),args[2])
        elif name=='V_MED3_F32':
            value=self.ir.op('max',self.ir.op('min',args[0],args[1]),
                             self.ir.op('min',self.ir.op('max',args[0],args[1]),args[2]))
        elif name=='V_CVT_PKRTZ_F16_F32':value=self.ir.op('pack',*args[:2])
        elif name=='V_CVT_PKNORM_U16_F32':value=self.ir.op('pack',*(self.ir.op('sat',x) for x in args[:2]))
        elif name=='V_CVT_PKNORM_I16_F32':
            value=self.ir.op('pack',*(self.ir.op('max',self.ir.const(-1.),self.ir.op('min',self.ir.const(1.),x)) for x in args[:2]))
        elif name=='V_CNDMASK_B32':
            raw=src[2] if k==7 else 106
            predicate=self.mask(raw)
            if predicate is None:predicate=args[2] if k==7 else self.gets(106)
            value=self.ir.op('select',predicate,args[1],args[0])
        else:value=self.unknown(name,*args)
        if k==7:
            if f['omod']:value=self.ir.op('mul',value,self.ir.const({1:2,2:4,3:.5}[f['omod']]))
            if f['clamp']:value=self.ir.op('sat',value)
        self.v[d]=value

    def step(self,i):
        """Execute one instruction and merge its writes under the active predicates."""
        bp,live_exec=self.bp,self.exec
        if self.ir.value(bp)==1 and self.ir.value(live_exec)==1:
            self.instruction(i);return
        v0,s0=dict(self.v),dict(self.s);e0={t:list(x) for t,x in self.exports.items()};self.premerged=set()
        self.instruction(i)
        dont_care=lambda n:self.ir.nodes[n]['op']=='unsupported' and self.ir.nodes[n].get('reason','').startswith('Uninitialized')
        live=self.p_and(bp,live_exec)
        for reg,value in list(self.v.items()):
            old=v0.get(reg)
            if old is not None and old!=value and not dont_care(old):self.v[reg]=self.ir.op('select',live,value,old)
        if self.ir.value(bp)!=1:
            for reg,value in list(self.s.items()):
                old=s0.get(reg)
                if reg in self.premerged or old is None or old==value or dont_care(old):continue
                self.s[reg]=self.ir.op('select',bp,value,old)
                self.descriptors.pop(reg,None);self.scalar_bits.pop(reg,None)
            for target,channels in list(self.exports.items()):
                old=e0.get(target)
                if old is None or old==channels:continue
                self.exports[target]=[n if o is None else o if n is None else self.ir.op('select',bp,n,o)
                                      for n,o in zip(channels,old)]

    def compile(self):
        instructions=decoder().decode_stream(self.code,0)
        for i in instructions:
            if i['relative'] in self.pending:self.bp=self.p_or(self.bp,self.pending.pop(i['relative']))
            if self.ir.value(self.bp)==0:continue
            self.step(i)
        if not all(t in self.exports and all(x is not None for x in self.exports[t]) for t in (0,1,2)):
            raise ValueError('Not a complete three-target deferred writer')
        if self.exec_masked_export:
            # Fail closed until the discard predicate is carried to both frontends.
            raise ValueError('Exec-masked exports (alpha test/discard) are not translated')
        return dict(nodes=self.ir.nodes,exports={str(k):v for k,v in self.exports.items()},exec_masked_export=self.exec_masked_export,
            samples=self.samples,instruction_count=len(instructions),opcode_counts=dict(Counter(i['name'] for i in instructions)))


def compile_program(code,usage_slots):
    return Compiler(code,usage_slots).compile()


def _terms(ir,index,coefficient=1.,result=None):
    """Canonical additive terms; retain nonlinear subexpressions as atoms."""
    result={} if result is None else result
    n=ir.nodes[index];args=n['args'];op=n['op']
    if op in ('add','sub'):
        _terms(ir,args[0],coefficient,result)
        _terms(ir,args[1],coefficient*(1 if op=='add' else -1),result)
    elif op=='neg':_terms(ir,args[0],-coefficient,result)
    elif op=='mul' and any(ir.value(i) is not None for i in args):
        j=0 if ir.value(args[0]) is not None else 1
        _terms(ir,args[1-j],coefficient*ir.value(args[j]),result)
    else:
        key='constant' if op=='const' else index
        value=coefficient*(n['value'] if op=='const' else 1)
        result[key]=result.get(key,0.)+value
    return {k:v for k,v in result.items() if abs(v)>1e-12}


def _gloss_without_aa(ir,index):
    """Match the engine G-buffer encoding algebra, not a named shader.

    -log2(1 - W*(1-exp2(-11*g)))/11 becomes g at W=1. Other
    encodings are retained, and unresolved terms then block translation.
    """
    n=ir.nodes[index]
    if n['op']!='mul':return None
    args=n['args'];j=next((j for j,x in enumerate(args) if ir.value(x) is not None and abs(ir.value(x)+1/11)<1e-7),None)
    if j is None:return None
    log=ir.nodes[args[1-j]]
    if log['op']!='log2':return None
    arg=log['args'][0]
    reachable=ir.reachable([arg])
    for exp in reachable:
        e=ir.nodes[exp]
        if e['op']!='exp2':continue
        m=ir.nodes[e['args'][0]]
        if m['op']!='mul':continue
        k=next((j for j,x in enumerate(m['args']) if ir.value(x) is not None and abs(ir.value(x)+11)<1e-6),None)
        if k is None:continue
        # Verify both algebraic endpoints; arbitrary unknown width is preserved
        # as an atom, so a wrong encoding cannot pass just a numeric example.
        endpoint=ir.replace([arg],{exp:ir.const(1.)})[0]
        if _terms(ir,endpoint)!={'constant':1.}:continue
        # Linear in exp only: no nonlinear term may enclose the exp node.
        def linear(i):
            if i==exp:return True
            if exp not in ir.reachable([i]):return True
            z=ir.nodes[i];a=z['args']
            if z['op'] in ('add','sub','neg'):return all(linear(q) for q in a)
            if z['op']=='mul':return sum(exp in ir.reachable([q]) for q in a)==1 and all(linear(q) for q in a)
            return False
        if linear(arg):return m['args'][1-k]
    return None


def _native_normal(ir,roots):
    """Recover an actual encoded XY/Z normal slice before vertex TBN transport."""
    for index in sorted(ir.reachable(roots)):
        terms=_terms(ir,index)
        if len(terms)!=3 or terms.get('constant')!=1.:continue
        squares=[i for i,c in terms.items() if i!='constant' and c==-1.]
        if len(squares)!=2:continue
        xy=[];textures=[]
        for square in squares:
            n=ir.nodes[square]
            if n['op']!='mul' or n['args'][0]!=n['args'][1]:break
            x=n['args'][0];xy.append(x)
            tex=[ir.nodes[i] for i in ir.reachable([x]) if ir.nodes[i]['op']=='texture']
            if len(tex)!=1:break
            textures.append(tex[0])
        if len(textures)!=2:continue
        if textures[0]['sample']!=textures[1]['sample'] or {t['channel'] for t in textures}!={0,1}:continue
        if textures[0]['channel']==1:xy.reverse()
        return dict(xyz=[*xy,index],sample=textures[0]['sample'],api=textures[0]['api'])
    return None


def lower_deferred(program):
    """Apply only shared render-target ABI and explicit portability policies."""
    ir=DAG(program['nodes']);exports=program['exports'];limitations=[]
    gloss=exports['2'][3]
    no_aa=_gloss_without_aa(ir,gloss)
    if no_aa is not None:
        gloss=no_aa;limitations.append('Derivative/geometric specular-AA width is 1; gloss is the native flat-surface value.')
    normal=_native_normal(ir,exports['0'][:3])
    if normal:
        limitations.append('Native tangent-normal slice is used with Blender UV tangents; original packed vertex TBN is not reconstructed.')
        samples={ir.nodes[i]['sample'] for i in ir.reachable(exports['0'][:3])
            if ir.nodes[i]['op']=='texture'}
        if samples-{normal['sample']}:
            limitations.append('Normal recovery retains one decoded texture slice; composition involving other samples in the native normal output is not reproduced.')
    else:limitations.append('No portable tangent-normal slice was recovered; geometry normal is used.')
    alpha=exports['1'][3]
    emission=ir.op('sub',ir.op('exp2',ir.op('mul',ir.const(5.),alpha)),ir.const(1.))
    outputs=dict(albedo=exports['1'][:3],F0=exports['2'][:3],gloss=gloss,emission=emission)
    roots=[*outputs['albedo'],*outputs['F0'],gloss,emission]
    if normal:roots+=normal['xyz']
    used=ir.reachable(roots)
    unresolved=[dict(node=i,**ir.nodes[i]) for i in sorted(used) if ir.nodes[i]['op']=='unsupported']
    return dict(nodes=ir.nodes,outputs=outputs,normal=normal,samples=program['samples'],
        unresolved=unresolved,limitations=limitations,reachable_nodes=sorted(used))


def compile_graph(data):
    results=[]
    for program in pixel_programs(data):
        row={k:v for k,v in program.items() if k!='code'}
        try:row.update(status='compiled',ir=compile_program(program['code'],program['usage_slots']))
        except (ValueError,IndexError,KeyError,struct.error) as exc:row.update(status='unsupported',reason=str(exc))
        results.append(row)
    return dict(schema='rcps4-native-shader-ir-v1',translator_version=VERSION,
        graph_sha256=hashlib.sha256(data).hexdigest(),programs=results,
        limitations=['Deferred writers only; forward branches and exec masks are linearised by predication, loops and forward light loops are unsupported.',
                    'Float16 target quantization is recorded at exports but the material DAG retains float precision.',
                    'Compiled IR alone does not establish per-pass CB or resource binding, vertex TBN, or runtime selection.'])
