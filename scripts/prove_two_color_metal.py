"""Hidden WGL proof: remove only our exact rule in memory for the before image."""
import argparse,ctypes,hashlib,json,struct,sys,time,zlib,os
from pathlib import Path
REPO=Path(__file__).resolve().parents[1];sys.path.insert(0,str(REPO))
from rc2016.app.main import AnimationExtractorApp
from rc2016.viewer import textures as tp
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True, type=Path, help='Validated game archive root')
parser.add_argument('--research', required=True, type=Path, help='Private research workspace')
parser.add_argument('--output', required=True, type=Path, help='Proof folder outside the repository')
args=parser.parse_args()
OUT=args.output.resolve()
if OUT.is_relative_to(REPO.resolve()):parser.error('Proofs belong outside the code repository')
OUT.mkdir(parents=True,exist_ok=True)
EXPECTED_GRAPH_SHA='342877ebd945320f167e0d1eb269a8279a7edbba85f2867c7fd8fecfc2e2a93e'
GRAPH='materialgraph/global/basic_normal_gloss_metal/basic_normal_two_color_gloss_metal.'
MODEL='environment/batalia/gameplay/rb_bat_battery_cannon/rb_bat_battery_cannon.model'
def grab(view):
    view._draw();w,h=view.winfo_width(),view.winfo_height()
    gl=view._gl;gl.glReadBuffer(0x0404);gl.glPixelStorei(0x0D05,1)
    p=(ctypes.c_ubyte*(w*h*3))()
    gl.glReadPixels.argtypes=[ctypes.c_int]*4+[ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p]
    gl.glReadPixels(0,0,w,h,0x1907,0x1401,p)
    assert gl.glGetError()==0
    rgb=bytes(p);assert len(set(rgb[i:i+3] for i in range(0,len(rgb),3)))>20,'Blank framebuffer'
    return w,h,rgb

def save(path,w,h,rgb):
    def chunk(t,d):return struct.pack('>I',len(d))+t+d+struct.pack('>I',zlib.crc32(t+d)&0xffffffff)
    stride=w*3;scan=b''.join(b'\0'+rgb[y*stride:(y+1)*stride] for y in range(h-1,-1,-1))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(scan))+chunk(b'IEND',b''))


viewdir=REPO/'rc2016/viewer'
LOCK=viewdir/'.shader_proof.lock'
fd=os.open(LOCK,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,b'TwoColorGlossMetal hidden GPU proof');os.close(fd)
app=None;original=tp.GRAPH_RULES
try:
    app=AnimationExtractorApp();app.root.attributes('-alpha',0.0);app.root.geometry('1500x1000')
    app.source_var.set(str(args.source));app.blender_var.set('')
    output=args.research;app.output_var.set(str(output))
    loader=app.browser.source_loader;loader._ensure_archive(args.source)
    ids=[i for i in loader.archive.resolve(MODEL) if loader.archive.entries[i]['type']=='model']
    assert ids==['f696247894031f0d7010'],ids
    row=dict(id=ids[0])
    graph_ids=[i for i in loader.archive.resolve(GRAPH+'materialgraph') if loader.archive.entries[i]['type']=='materialgraph']
    assert len(graph_ids)==1
    graph_bytes=tp.decompress(loader.archive.bytes(graph_ids[0])[0])[0]
    assert hashlib.sha256(graph_bytes).hexdigest()==EXPECTED_GRAPH_SHA
    errors=[];app.root.report_callback_exception=lambda typ,value,tb:errors.append(str(value))
    hashes={name:hashlib.sha256((viewdir/name).read_bytes()).hexdigest() for name in ('textures.py','shading.py','viewport.py')}
    tp.GRAPH_RULES=tuple((p,r) for p,r in original if p!=GRAPH)
    assert len(tp.GRAPH_RULES)==len(original)-1
    # Load the canonical model directly; asynchronous table refresh can select another row.
    loader=app.browser.source_loader
    payload=loader.load(app.source_var.get(),row['id'],MODEL,'Highest',0,output,True)
    assert payload['source_path']==MODEL and payload['model_id']==row['id']
    view=app.browser.viewport;view.set_mesh(payload);app.root.update()
    assert view._hrc and not view._context_error,view._context_error
    view.set_mode('Shaded');assert not view.shading_error,view.shading_error
    used={s for s,first,count in payload['batches'] if count}
    before_params={s:{k:v for k,v in e.items() if not k.startswith('_')} for s,e in payload['textures'].items() if s in used}
    assert all('two_color_metal' not in e for e in before_params.values())
    camera=(view._yaw,view._pitch,view._zoom,tuple(view._pan));bounds=view._bounds
    w,h,before=grab(view);save(OUT/'battery_cannon_before.png',w,h,before)
    tp.GRAPH_RULES=original;loader=app.browser.source_loader
    after_payload=dict(payload,textures=tp.material_textures(loader.archive,payload['materials'],loader.texture_cache))
    after_params={s:{k:v for k,v in e.items() if not k.startswith('_')} for s,e in after_payload['textures'].items() if s in used}
    target={s for s,e in after_params.items() if e.get('two_color_metal')}
    assert target
    unaffected={s for s in used if s not in target}
    assert all(before_params[s]==after_params[s] for s in unaffected),'Unrelated material changed'
    for key in ('vertices','normals','uvs','triangles','batches'):assert payload.get(key)==after_payload.get(key)
    view.set_mesh(after_payload);view._bounds=bounds;view._yaw,view._pitch,view._zoom,pan=camera;view._pan=list(pan)
    w2,h2,after=grab(view);assert (w,h)==(w2,h2);assert not view.shading_error,view.shading_error
    assert all(view._gl_textures[s].get('two_color_metal') for s in target),'Metadata lost during upload'
    save(OUT/'battery_cannon_after.png',w,h,after)
    changed=sum(before[i:i+3]!=after[i:i+3] for i in range(0,len(before),3));assert changed>100,changed
    from PIL import Image,ImageDraw
    top=Image.open(OUT/'battery_cannon_before.png');bottom=Image.open(OUT/'battery_cannon_after.png')
    combined=Image.new('RGB',(w,2*h+64),(36,38,43));combined.paste(top,(0,32));combined.paste(bottom,(0,h+64))
    draw=ImageDraw.Draw(combined);draw.text((12,10),'Before: generic colored F0 / alpha gloss',fill='white')
    draw.text((12,h+42),'After: two-color diffuse + post-BRDF specular tint / grey G + rim gloss; viewer lighting',fill='white')
    combined.save(OUT/'battery_cannon_old_vs_decoded.png')
    gl=view._gl;gl.glGetString.restype=ctypes.c_char_p;assert not errors,errors
    limits={}
    for name,enum in (('fragment_texture_units',0x8872),('combined_texture_units',0x8B4D)):
        value=ctypes.c_int();gl.glGetIntegerv(enum,ctypes.byref(value));limits[name]=value.value
    assert gl.glGetError()==0
    after_hashes={name:hashlib.sha256((viewdir/name).read_bytes()).hexdigest() for name in hashes}
    assert hashes==after_hashes,'Concurrent shared edit during proof'
    report=dict(status='passed',graph=GRAPH+'materialgraph',model=MODEL,asset_id=row['id'],graph_sha256=EXPECTED_GRAPH_SHA,source_sha256=payload['source_sha256'],used_slots=sorted(used),
        target_slots=sorted(target),unrelated_slots_verified_unchanged=sorted(unaffected),before=before_params,after=after_params,camera=camera,bounds=bounds,
        geometry_hidden=False,geometry_unchanged=True,viewport_size=[w,h],changed_pixels=changed,
        renderer=gl.glGetString(0x1F01).decode(),texture_limits=limits,new_samplers=0,shader_error=view.shading_error,callback_errors=errors,
        rule_removed_only_in_memory=True,viewer_files_unchanged_during_proof=True,viewer_hashes=hashes,blender_launched=False,
        visual_review='pending',game_capture=False,files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.png')},
        target_texture_inputs={s:{k:dict(path=t['path'],width=t['width'],height=t['height'],format=t['format'],srgb=t['srgb'],pixels_sha256=hashlib.sha256(t['pixels']).hexdigest()) for k,t in e.items() if k.startswith('_') and t} for s,e in after_payload['textures'].items() if s in target})
    (OUT/'battery_cannon_proof_result.json').write_text(json.dumps(report,indent=2));print(json.dumps(dict(status=report['status'],target_slots=sorted(target),unrelated_slots_verified_unchanged=sorted(unaffected),changed_pixels=changed,renderer=report['renderer'],size=[w,h],texture_limits=limits)))
finally:
    tp.GRAPH_RULES=original
    if app:app.browser.close();app.root.destroy()
    LOCK.unlink()
