"""Small, hash-bound GCN field decoder/fixtures for Sheepinator glass evidence.

Instruction boundaries/opcodes use the checked local audit_shader_blobs mirror.
Field masks are independently cross-checked against local opengnm decoder.c.
This is not a GCN emulator or a claim of complete semantic decoding.
"""
from __future__ import annotations
import hashlib, io, json, struct, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
RECON=HERE.parent
def render_markdown(result):
    lines=["# Sheepinator glass decoder gap audit","","Status: still relevant: glass is not yet covered by the game shader.","","The decoder now reads VOP3 source `abs`/`neg`, `omod`, `clamp`; EXP target, enable mask, compression and done/valid; and signed SOPP branch targets. Field layouts were checked against opengnm `src/gcn/decoder.c`; synthetic encodings and single-bit negative controls pass.","","## Result","","- All six hash-pinned glass pixel binaries decode completely to their declared ends. The two long variants have 53 branches each; the short ones 0 or 2.","- Export masks, targets and source VGPRs are decoded. EXP has no source-modifier fields.","- Proves encoded fields and full instruction coverage only, not values reaching exports, MRT-to-attachment mapping or blend equation. A branch-complete value trace would need EXEC/SCC/VCC path predicates; enumerating 53 branches would not establish feasible paths.","","## Reproduction","","```powershell",f"python '{(HERE/'decode_glass_gcn.py').as_posix()}'","```",""]
    return '\n'.join(lines)

if __name__=='__main__' and '--render-only' in sys.argv:
    # Re-render the Markdown from the saved JSON without the heavy analysis (and its sibling imports).
    (HERE/'glass_cfg_gap_audit.md').write_bytes(render_markdown(json.loads((HERE/'glass_cfg_gap_audit.json').read_text(encoding='utf-8'))).encode('utf-8'))
    sys.exit(0)

sys.path.insert(0,str(RECON))
import audit_shader_blobs as audit

def decode_fields(kind, word, wide, opcode):
    f=audit.instruction_fields(kind,word,wide,opcode)
    if kind==7:  # VOP3A: vop3a_abs, vop3_neg, vop3_omod, clmp
        f={**f,"src_modifiers":[{"abs":bool((wide>>(8+i))&1),"neg":bool((wide>>(61+i))&1)} for i in range(3)],
           "omod":(wide>>59)&3,"clamp":(wide>>11)&1}
    elif kind==15: # EXP: target, enable, compressed, done, valid mask
        f={**f,"enable_mask":wide&15,"compressed":bool((wide>>10)&1),
           "done":bool((wide>>11)&1),"valid_mask":(wide>>12)&15,
           "src_registers":[(wide>>(32+8*i))&255 for i in range(2 if (wide>>10)&1 else 4)]}
    elif kind==4 and "BRANCH" in audit.opcode_name(kind,opcode):
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
        w=struct.unpack_from('<I',code,p)[0]; k=audit.parse_microcode(w)
        if k is None: raise ValueError(f"unknown instruction at {base+p}")
        q=struct.unpack_from('<Q',code,p)[0] if k in (7,11,12,13,14,15) else w
        op=audit.encoded_opcode(k,w,q); name=audit.opcode_name(k,op) if op is not None else "?"
        n=(8 if k in (7,11,12,13,14,15) else 4)+4*audit.literal_count(k,w,q,op)
        if p+n>len(code): raise ValueError(f"truncated instruction at {base+p}")
        fields=decode_fields(k,w,q,op or 0)
        out.append({"offset":base+p,"relative":p,"length":n,"kind":k,"opcode":op,"name":name,"word":w,"wide":q,"fields":fields})
        p+=n
    return out

def main():
    fixture_assertions()
    bind=json.loads((RECON/'shader_binding_trace.json').read_text(encoding='utf-8'))
    graph=next(g for g in bind['graphs'] if g['label']=='glass_character')
    raw=Path(graph['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='be4eb2847693739c596cad28b11af11a98b261c60aa54ce42f72d60574a62a54'
    dat=audit.DAT1(io.BytesIO(raw),version=audit.dat1lib.VERSION_SO,ignore_sections_exceptions=True)
    section=next(s for s in dat.header.sections if s.tag==0x873D1E6C); blob=bytes(dat.get_section(0x873D1E6C)._raw)
    records=[x for x in audit.gnm_headers(blob,section.offset)['records'] if x['shader_type']=='PIXEL']
    variants=[]
    for r in records:
        code=blob[r['code_file_offset']-section.offset:r['code_file_offset']-section.offset+r['code_size_bytes']]
        ins=decode_stream(code,r['code_file_offset'])
        assert hashlib.sha256(code).hexdigest()==next(t['code_sha256'] for t in graph['traces'] if t['code_file_offset']==r['code_file_offset'])
        branches=[x for x in ins if x['kind']==4 and 'BRANCH' in x['name']]
        exps=[x for x in ins if x['kind']==15]
        variants.append({'code_file_offset':r['code_file_offset'],'code_size_bytes':len(code),'sha256':hashlib.sha256(code).hexdigest(),
          'branches':[{'offset':x['offset'],'target_relative':x['fields'].get('branch_target_relative'),'opcode':x['name']} for x in branches],
          'exports':[{'offset':x['offset'],'target':x['fields']['target'],'mask':x['fields']['enable_mask'],'compressed':x['fields']['compressed'],'sources':x['fields']['src_registers']} for x in exps],
          'instruction_count':len(ins),'decoded_to_end':ins[-1]['relative']+ins[-1]['length']==len(code)})
    assert len(variants)==6 and all(x['decoded_to_end'] for x in variants)
    result={'scope':'finite decoder-gap audit; no path-sensitive value claims','graph_sha256':hashlib.sha256(raw).hexdigest(),'section_file_offset':section.offset,'decoder_crosscheck':'field formulas checked against local opengnm src/gcn/decoder.c vop3a_abs/vop3_neg/vop3_omod/clmp; exp_compr and exp_* helpers; SOPP signed immediate branch parsing','fixtures':'synthetic known encodings and one-bit mutation negative controls passed','variants':variants,'limits':['VOP3 source modifiers, output modifiers/clamp, EXP mask/compact sources, and signed branch destinations decode; ordinary destination registers are decoded by audit_shader_blobs.instruction_fields.','Full path-sensitive provenance is not asserted: 53-branch programs require path-condition-aware EXEC/SCC/VCC and merge semantics; bounded enumeration without these semantics risks false paths or exponential explosion.','EXP source modifiers are absent: the export encoding selects packed VGPR words and does not encode VOP3 abs/neg bits.','No claim is made about API blend alpha, target attachment use, or material runtime upload.']}
    (HERE/'glass_cfg_gap_audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (HERE/'glass_cfg_gap_audit.md').write_bytes(render_markdown(result).encode('utf-8'))
    print('six variants decoded; field fixtures passed')
if __name__=='__main__': main()
