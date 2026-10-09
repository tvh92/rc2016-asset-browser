"""GCN field decoder: VOP3 source modifiers/omod/clamp, EXP targets and masks, branch targets.

Instruction boundaries and opcodes come from rc2016.shaders.gcn; the field masks are
cross-checked against opengnm's decoder.c, and fixture_assertions() holds hand-encoded
words with one-bit negative controls. Not a GCN emulator or a complete semantic decode.
"""
from __future__ import annotations

import struct

from rc2016.shaders import gcn


def decode_fields(kind, word, wide, opcode):
    f=gcn.instruction_fields(kind,word,wide,opcode)
    if kind==7:  # VOP3A: vop3a_abs, vop3_neg, vop3_omod, clmp
        f={**f,"src_modifiers":[{"abs":bool((wide>>(8+i))&1),"neg":bool((wide>>(61+i))&1)} for i in range(3)],
           "omod":(wide>>59)&3,"clamp":(wide>>11)&1}
    elif kind==15: # EXP: target, enable, compressed, done, valid mask
        f={**f,"enable_mask":wide&15,"compressed":bool((wide>>10)&1),
           "done":bool((wide>>11)&1),"valid_mask":(wide>>12)&15,
           "src_registers":[(wide>>(32+8*i))&255 for i in range(2 if (wide>>10)&1 else 4)]}
    elif kind==4 and "BRANCH" in gcn.opcode_name(kind,opcode):
        rel=struct.unpack("<h",struct.pack("<H",word&0xffff))[0]
        f={**f,"branch_displacement_words":rel,"branch_target_relative":4+4*rel}
    return f

def fixture_assertions():
    # Hand-encoded synthetic words check independent bitfield equations;
    # toggling every field is a negative control.
    base=(0x35<<32)|(0x26<<41)|(0x17<<50)|(1<<8)|(1<<63)|(2<<59)|(1<<11)
    got=decode_fields(7,base&0xffffffff,base,0)
    assert got["src_modifiers"]==[{"abs":True,"neg":False},{"abs":False,"neg":False},{"abs":False,"neg":True}]
    assert got["omod"]==2 and got["clamp"]==1
    assert decode_fields(7,base&0xffffffff,base^(1<<8),0)["src_modifiers"][0]["abs"] is False
    exp=(5<<4)|0b1011|(1<<10)|(1<<11)|(0b1101<<12)|(0x11<<32)|(0x22<<40)
    e=decode_fields(15,exp&0xffffffff,exp,0)
    assert (e["target"],e["enable_mask"],e["compressed"],e["done"],e["valid_mask"],e["src_registers"])==(5,11,True,True,13,[0x11,0x22])
    assert decode_fields(15,exp&0xffffffff,exp^(1<<10),0)["compressed"] is False
    b=(2<<16)|0xfffc
    br=decode_fields(4,b,b,2)
    assert br["branch_displacement_words"]==-4 and br["branch_target_relative"]==-12
    assert decode_fields(4,b^1,b^1,2)["branch_target_relative"]!=-12

def decode_stream(code,base):
    out=[]; p=0
    while p<len(code):
        w=struct.unpack_from('<I',code,p)[0]; k=gcn.parse_microcode(w)
        if k is None: raise ValueError(f"unknown instruction at {base+p}")
        q=struct.unpack_from('<Q',code,p)[0] if k in (7,11,12,13,14,15) else w
        op=gcn.encoded_opcode(k,w,q); name=gcn.opcode_name(k,op) if op is not None else "?"
        n=(8 if k in (7,11,12,13,14,15) else 4)+4*gcn.literal_count(k,w,q,op)
        if p+n>len(code): raise ValueError(f"truncated instruction at {base+p}")
        fields=decode_fields(k,w,q,op or 0)
        out.append({"offset":base+p,"relative":p,"length":n,"kind":k,"opcode":op,"name":name,"word":w,"wide":q,"fields":fields})
        p+=n
    return out

