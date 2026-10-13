"""On-selection native geometry loading. No Blender, renders, receipt writes or shaders."""
from __future__ import annotations
from collections import OrderedDict
import json
from pathlib import Path
import threading
import numpy as np
from rc2016.formats.archive import Archive, asset_references, locate, decompress
from rc2016.formats.native_model import decode_model
from rc2016.formats.container import section_table


def presentation_basis(value):
    x,y,z=value
    return (x,-z,y)


def _basis_array(values):
    """presentation_basis over an (n, 3) array: columns (x, -z, y)."""
    values=np.asarray(values,dtype=np.float64).reshape(-1,3)
    out=np.empty_like(values)
    out[:,0]=values[:,0];out[:,1]=-values[:,2];out[:,2]=values[:,1]
    return out


def _mesh_payload_arrays(decoded, meshes, selected):
    """mesh_payload for decode_model(..., as_arrays=True) meshes: vertices, normals and triangles
    as numpy arrays (viewport.prepare_viewport takes them as-is); uvs, skin and rock alpha as the
    same flat lists as the list path, since the viewport tests those for truth."""
    vertices,normals,triangles,uvs,batches,joints,weights,alpha,alpha_sources=[],[],[],[],[],[],[],[],[]
    base=triangle_total=0
    for mesh in meshes:
        positions=np.asarray(mesh['positions'],dtype=np.float64).reshape(-1,3);count=len(positions)
        vertices.append(_basis_array(positions));normals.append(_basis_array(mesh['normals']))
        colors=mesh.get('colors')
        alpha.append(np.asarray(colors,dtype=np.float64).reshape(-1,4)[:,3] if colors else np.ones(count))
        alpha_sources.append(dict(mesh=mesh['index'], source='native_colors_alpha' if colors else 'viewer_white_default_1_runtime_input_unresolved'))
        if mesh.get('skin_joint_array') is not None:
            joints.append(np.asarray(mesh['skin_joint_array'],dtype=np.float64));weights.append(np.asarray(mesh['skin_weight_array'],dtype=np.float64))
        else:
            mesh_joints=np.zeros((count,8));mesh_weights=np.zeros((count,8))
            for i,influences in enumerate(mesh.get('weights') or []):
                for slot,(j,w) in enumerate((influences or [])[:8]):
                    mesh_joints[i,slot]=float(j);mesh_weights[i,slot]=float(w)
            joints.append(mesh_joints);weights.append(mesh_weights)
        uv0=mesh.get('uv0')
        if uvs is not None and uv0 is not None and len(uv0):
            uv0=np.asarray(uv0,dtype=np.float64).reshape(-1,2)
            uvs.append(np.stack((uv0[:,0],1.0-uv0[:,1]),axis=1))
        else:uvs=None
        tris=np.asarray(mesh['triangles'],dtype=np.int64).reshape(-1,3)
        batches.append(((mesh.get('material') or {}).get('index'),triangle_total,len(tris)))
        triangles.append(tris+base if base else tris)
        base+=count;triangle_total+=len(tris)
    if not base or not triangle_total:raise ValueError('Selected source look/LOD has no geometry')
    return dict(vertices=np.concatenate(vertices),normals=np.concatenate(normals),triangles=np.concatenate(triangles),
                uvs=np.concatenate(uvs).tolist() if uvs is not None else None,batches=batches,
                skin_joints=np.concatenate(joints).ravel().tolist(),skin_weights=np.concatenate(weights).ravel().tolist(),
                rock_alpha=np.concatenate(alpha).tolist(),rock_alpha_sources=alpha_sources,
                source_sha256=decoded['source_sha256'],selected_mesh_indices=sorted(selected),
                source_exact_geometry=True,game_equivalence=False,material_preview='neutral geometry inspection')


def mesh_payload(decoded, lod='Highest', look=0):
    from rc2016.formats.native_model import _selected_meshes
    selected=set(_selected_meshes(decoded['looks'],decoded['mesh_record_count'],lod,look))
    chosen=[m for m in decoded['meshes'] if m['index'] in selected]
    if chosen and all(isinstance(m['positions'],np.ndarray) for m in chosen):
        return _mesh_payload_arrays(decoded,chosen,selected)
    vertices,normals,triangles,uvs,batches=[],[],[],[],[]
    # Up to 8 skin influences per vertex as flat (joint, weight) lists; all-zero
    # weights mean "not skinned" and the vertex keeps its rest position.
    skin_joints,skin_weights=[],[]
    rock_alpha=[];rock_alpha_sources=[]
    for mesh in decoded['meshes']:
        if mesh['index'] not in selected:continue
        base=len(vertices)
        # presentation_basis inlined: these loops dominate large models.
        vertices.extend([(x,-z,y) for x,y,z in mesh['positions']])
        normals.extend([(x,-z,y) for x,y,z in mesh['normals']])
        rock_alpha.extend([float(c[3]) for c in mesh['colors']] if mesh.get('colors') else [1.0]*len(mesh['positions']))
        rock_alpha_sources.append(dict(mesh=mesh['index'], source='native_colors_alpha' if mesh.get('colors') else 'viewer_white_default_1_runtime_input_unresolved'))
        if mesh.get('weights'):
            for influences in mesh['weights']:
                pairs=(influences or [])[:8]
                skin_joints.extend([float(j) for j,_w in pairs]+[0.0]*(8-len(pairs)))
                skin_weights.extend([float(w) for _j,w in pairs]+[0.0]*(8-len(pairs)))
        else:
            skin_joints.extend([0.0]*(8*len(mesh['positions'])));skin_weights.extend([0.0]*(8*len(mesh['positions'])))
        # The decoder flips V for Blender; textures upload top row first, so flip back.
        if uvs is not None and mesh.get('uv0'):uvs.extend([(u,1.0-v) for u,v in mesh['uv0']])
        else:uvs=None  # untextured if any selected mesh lacks UV0
        # (material slot, first triangle, triangle count) for per-material drawing.
        batches.append(((mesh.get('material') or {}).get('index'),len(triangles),len(mesh['triangles'])))
        triangles.extend([(a+base,b+base,c+base) for a,b,c in mesh['triangles']] if base else [tuple(t) for t in mesh['triangles']])
    if not vertices or not triangles:raise ValueError('Selected source look/LOD has no geometry')
    return dict(vertices=vertices,normals=normals,triangles=triangles,uvs=uvs,batches=batches,
                skin_joints=skin_joints,skin_weights=skin_weights,rock_alpha=rock_alpha,rock_alpha_sources=rock_alpha_sources,
                source_sha256=decoded['source_sha256'],selected_mesh_indices=sorted(selected),
                source_exact_geometry=True,game_equivalence=False,material_preview='neutral geometry inspection')


def material_rules(archive, materials, translations=None):
    """{material path: {'graphs', 'rule_kind', 'rule'}}: which viewer shading rule each material gets
    (exact rule, selected native translation, family fallback or generic reading).
    ``translations`` ({path: selected translation or None}, from material_textures) skips selecting it again."""
    from rc2016.formats.links import rule_coverage
    rules={}
    for material in materials:
        path=material.get('path')
        if not path or path in rules:continue
        try:
            ids=[k for k in archive.resolve(path) if archive.entries[k]['type']=='material']
            if len(ids)!=1:continue
            graphs=[r['path'] for r in asset_references(archive,ids[0])
                    if not r.get('resolution_error') and r['path'].endswith('.materialgraph')]
        except (ValueError,KeyError,OSError):
            continue
        from rc2016.viewer.native_glsl import selected_translation
        if translations is not None and path in translations: native = translations[path]
        else:
            try: native = selected_translation(archive, path)
            except (ValueError, KeyError, IndexError, OSError): native = None
        kind,rule=rule_coverage(graphs, native)
        rules[path]=dict(graphs=graphs,rule_kind=kind,rule=rule,
                         native_limitations=native.get('limitations', []) if native else [])
    return rules


class SourcePreviewLoader:
    """One archive metadata instance and one selected decoded model; no corpus model decode."""
    def __init__(self):
        self.archive=None
        self.source_root=None
        self.current=None
        self.identity=None
        self.texture_cache={}
        self.animation=None
        # Recently viewed models: identity -> decoded model + its animation library, so
        # switching back (or changing look/LOD) skips the decode. Bounded by count and by
        # total vertices. Assigning identity=None from outside empties it (forces a decode).
        self._recent=OrderedDict()
        self._recent_identity=None
        # Model loads and clip decodes run on different worker threads.
        self.lock=threading.Lock()

    def pose(self, clip_index):
        with self.lock:
            if self.animation is None:raise ValueError('No animation library is loaded for this model')
            return self.animation.pose(clip_index)

    def _ensure_archive(self, source_root):
        source_root=Path(source_root).expanduser().resolve()
        if self.source_root!=source_root:
            archive_root=locate(source_root)
            # Archive initialization reads metadata and prior source_mapping only.
            # This path is never checkpointed or decoded using Archive.decode().
            self.archive=Archive(archive_root,Path(__file__).resolve().parents[2]/'.cache'/'source_preview',lambda *_a,**_k:None)
            self.archive.verify_repeats=False   # preview only: read the first of an asset's repeated copies
            from rc2016.viewer.textures import TextureDiskCache
            self.archive.texture_disk_cache=TextureDiskCache(Path(__file__).resolve().parents[2]/'.cache'/'texture_preview')
            from rc2016.viewer.native_glsl import TranslationDiskCache
            self.archive.translation_disk_cache=TranslationDiskCache(Path(__file__).resolve().parents[2]/'.cache'/'native_translation')
            self.source_root=source_root
            self.identity=None;self.current=None;self.texture_cache={}
            self._recent.clear();self._recent_identity=None
        return self.archive

    def archive_textures(self, source_root):
        """Every texture in the archive, model or not: [{'path', 'folder'}] sorted by path."""
        with self.lock:
            archive=self._ensure_archive(source_root)
        paths=sorted({e['path'] for e in archive.entries.values()
                      if e['type']=='texture' and e['built_path'].lower().endswith('.built')})
        return [dict(path=p,suffix='',materials=[],via_graph=False) for p in paths]

    # Flash UI (.gfx/.swf) ---------------------------------------------------
    def ui_files(self, source_root):
        """Every Scaleform/Flash UI file in the archive, sorted by path."""
        with self.lock:
            archive=self._ensure_archive(source_root)
        return sorted({e['path'] for e in archive.entries.values() if e['type'] in ('gfx','swf')})

    def _gfx_bytes(self, path):
        ids=self.archive.resolve(path) if self.archive else []
        if not ids:return None
        data=self.archive.bytes(ids[0])[0]
        return data

    def _gfx_import(self, url):
        # ImportAssets URLs are bare file names; match them to archive UI files by name.
        name=url.replace('\\','/').rsplit('/',1)[-1].lower()
        stem=name.rsplit('.',1)[0]
        index=getattr(self,'_gfx_names',None)
        if index is None or index[0] is not self.archive:
            # First UI file (entry order) per lower-case stem: a file-name match implies a stem match.
            names={}
            for entry in self.archive.entries.values():
                if entry['type'] in ('gfx','swf'):
                    names.setdefault(entry['path'].rsplit('/',1)[-1].lower().rsplit('.',1)[0],entry['path'])
            index=self._gfx_names=(self.archive,names)
        path=index[1].get(stem)
        return self._gfx_bytes(path) if path is not None else None

    def gfx_library(self, path):
        from rc2016.flash.gfx import Library, parse
        cache=getattr(self,'_gfx_cache',None)
        if cache is None or cache[0]!=self.archive:
            cache=self._gfx_cache=(self.archive,{})
        if path not in cache[1]:
            data=self._gfx_bytes(path)
            if data is None:raise ValueError('UI file not found in the archive: '+path)
            cache[1][path]=Library(parse(data),self._gfx_import)
        return cache[1][path]

    def gfx_symbols(self, path):
        from rc2016.flash.gfx import symbols
        library=self.gfx_library(path)
        movie=library.movie
        return dict(path=path,symbols=symbols(movie),version=movie.version,signature=movie.signature,
                    imports=sorted({url for url,_name in movie.imports.values()}),
                    errors=list(movie.tag_counts.get('errors',[])))

    def gfx_image(self, path, char_id, frame, size, background=None):
        from rc2016.flash.gfx import collect, render
        library=self.gfx_library(path)
        return render(collect(library,char_id,frame),size=size,background=background)

    def gfx_export(self, path, folder, char_id, frames, size, name, include_source=False, progress=None):
        """PNG per (frame, label) into folder; optionally the original .gfx bytes too."""
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        written=[]
        if include_source:
            target=folder/Path(path).name
            target.write_bytes(self._gfx_bytes(path));written.append(str(target))
        for number,(frame,label) in enumerate(frames,1):
            image=self.gfx_image(path,char_id,frame,size)
            suffix='' if len(frames)==1 else '_'+(label or f'frame{frame+1:03d}')
            safe=''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in name+suffix)
            target=folder/f'{safe}.png'
            image.save(target);written.append(str(target))
            if progress:progress(number,len(frames),target.name)
        return written

    def texture_image(self, texture_path, index=0):
        """(Pillow image, facts) for the Textures tab; reuses the 3D view's decoded textures."""
        from rc2016.viewer.textures import FORMAT_NAMES, load_texture, to_image
        archive=self.archive
        if archive is None:raise ValueError('No source archive is loaded')
        texture=self.texture_cache.get(texture_path)
        if texture is None:
            texture=load_texture(archive,texture_path)
            self.texture_cache[texture_path]=texture
        index=min(max(0,index),texture.get('array_size',1)-1)
        facts=dict(path=texture_path,format=texture['format'],format_name=FORMAT_NAMES.get(texture['format'],str(texture['format'])),
                   preview_size=(texture['width'],texture['height']),full_size=texture['full_size'],srgb=texture['srgb'],
                   array_size=texture.get('array_size',1),slice=index)
        return to_image(texture,index),facts

    def export_textures(self, paths, folder, formats, flip=False, progress=None):
        from rc2016.viewer.textures import export_textures
        if self.archive is None:raise ValueError('No source archive is loaded')
        return export_textures(self.archive,paths,folder,formats,flip,progress)

    RECENT_MODELS = 6
    RECENT_VERTEX_BUDGET = 1_500_000

    PURPOSES = ('view', 'export')

    def load(self, source_root, model_id, source_path, lod='Highest', look=0, output_root=None, defer_animation=False, purpose='view'):
        """Payload for the viewport. With ``defer_animation`` a skinned model returns at once
        with ``animation_pending=True`` and no clips; call ``load_animation`` (another worker)
        to build its clip library.

        ``purpose='export'`` decodes geometry, skeleton and material paths only (list-valued
        meshes, as export.models.mesh_arrays reads them): no viewer textures, texture list,
        headers, material rules, native translation or viewport arrays, and the returned
        payload carries no geometry. Read ``current``/``animation`` afterwards as before.
        """
        if purpose not in self.PURPOSES:raise ValueError('Unknown load purpose: '+str(purpose))
        return self._load(source_root, model_id, source_path, lod, look, output_root, defer_animation, purpose)

    def load_animation(self, source_root, model_id, source_path, output_root=None):
        """Build (or reuse) the selected model's clip library: {model_id, source_path, clips, animsets, animation_note}."""
        with self.lock:
            self._ensure_archive(source_root)
            identity=(model_id,source_path,str(output_root) if output_root else None)
            cached=self._recent.get(identity)
            if cached is None:
                raise ValueError('The model is no longer loaded; select it again')
        # Built outside self.lock, so selecting another model is not blocked behind it.
        self._build_animation(cached,output_root)
        with self.lock:
            if self.identity==identity:self.current,self.animation=cached['decoded'],cached['animation']
            decoded=cached['decoded']
            return dict(model_id=model_id,source_path=source_path,animsets=decoded['animsets'],
                        clips=cached['animation'].labels() if cached['animation'] else [],animation_note=decoded['animation_note'])

    def _build_animation(self, cached, output_root):
        with cached['build_lock']:
            self._build_animation_locked(cached,output_root)

    def _build_animation_locked(self, cached, output_root):
        decoded=cached['decoded']
        if cached['animation_built']:return
        cached['animation_built']=True
        cached['animation']=None;decoded['animation_note']='';decoded['animsets']=[]
        if not decoded['joint_count']:return
        try:
            from rc2016.viewer.animation import AnimationLibrary, model_animsets
            animsets=model_animsets(self.archive,output_root,decoded['_archive_path']) if output_root else []
            cached['animation']=AnimationLibrary(self.archive,cached['data'],animsets)
            decoded['animsets']=list(cached['animation'].animset_data)
            if not output_root:decoded['animation_note']="Set an output root: animation sets are found through the scan's reference index there."
            elif not animsets:decoded['animation_note']='No animation set found for this model.'
        except Exception as error:  # the model still displays without clips
            cached['animation']=None;decoded['animation_note']=f'Animations unavailable: {error}'

    def _remember(self, identity, cached):
        self._recent[identity]=cached;self._recent.move_to_end(identity)
        while len(self._recent)>1 and (len(self._recent)>self.RECENT_MODELS or
                sum(c['vertices'] for c in self._recent.values())>self.RECENT_VERTEX_BUDGET):
            self._recent.popitem(last=False)

    def _decode(self, archive, model_id, source_path, purpose, texture_cache):
        """Decode one model into a _recent entry. Runs without self.lock."""
        packed,packed_hash=archive.bytes(model_id)
        data,codec=decompress(packed)
        decoded=decode_model(source_path,lod='All',data=data,as_arrays=purpose=='view')
        sections=section_table(data,0)
        joint_count=next((s['size']//16 for s in sections if s['label_hash']=='0x15DF9D3B'),0)
        decoded.update(packed_sha256=packed_hash,codec=codec,joint_count=joint_count)
        if purpose=='view':
            from rc2016.viewer.textures import FORMAT_NAMES, material_textures, model_texture_list, texture_header
            translations={}
            decoded['textures']=material_textures(archive,decoded['materials'],texture_cache,translations)
            decoded['texture_list']=model_texture_list(archive,decoded['materials'])
            for row in decoded['texture_list']:
                try:
                    header=texture_header(archive,row['path'])
                    full=(header['hd_width'],header['hd_height']) if header['hd_width'] else (header['sd_width'],header['sd_height'])
                    row.update(format=header['format'],format_name=FORMAT_NAMES.get(header['format'],str(header['format'])),width=full[0],height=full[1])
                except (ValueError,KeyError,OSError) as error:
                    row.update(format_name='unreadable',error=str(error))
            decoded['material_rules']=material_rules(archive,decoded['materials'],translations)
        else:
            decoded.update(textures={},texture_list=[],material_rules={})
        decoded['_archive_path']=source_path;decoded['animation_note']='';decoded['animsets']=[]
        return dict(decoded=decoded,data=data,animation=None,animation_built=False,build_lock=threading.Lock(),
                    vertices=sum(len(m['positions']) for m in decoded['meshes']))

    def _load(self, source_root, model_id, source_path, lod, look, output_root, defer_animation=False, purpose='view'):
        # self.lock covers loader state only; the decode, texture reads and viewport arrays run
        # outside it so clip, pose and texture-list calls are not queued behind a model decode.
        with self.lock:
            archive=self._ensure_archive(source_root)
            entry=archive.entries.get(model_id)
            if not entry or entry['type']!='model' or entry['path']!=source_path:
                raise ValueError('Selected asset ID and archive path do not match the canonical source index')
            if self.identity!=self._recent_identity:
                self._recent.clear()   # identity was reset from outside: decode afresh
            # Animation sets are found through the output root, so it is part of the identity.
            identity=(model_id,source_path,str(output_root) if output_root else None)+(('export',) if purpose=='export' else ())
            cached=self._recent.get(identity)
            if cached is not None:
                self._remember(identity,cached)
            texture_cache=self.texture_cache
        if cached is None:
            cached=self._decode(archive,model_id,source_path,purpose,texture_cache)
            with self.lock:
                if self.archive is not archive:
                    raise ValueError('The source archive changed while the model loaded; select it again')
                cached=self._recent.get(identity) or cached
                self._remember(identity,cached)
        pending=bool(defer_animation and cached['decoded']['joint_count'] and not cached['animation_built'])
        if not pending:
            self._build_animation(cached,output_root)
        with self.lock:
            self.current,self.animation=cached['decoded'],cached['animation']
            self.identity=self._recent_identity=identity
        current,animation=cached['decoded'],cached['animation']
        info=dict(model_id=model_id,source_path=source_path,archive_root=str(archive.root),
            source_packed_sha256=current['packed_sha256'],codec=current['codec'],
            kind='skinned' if current['joint_count'] else 'static',joint_count=current['joint_count'],
            looks=current['looks'],lod=lod,look=look,mesh_record_count=current['mesh_record_count'],
            animsets=current['animsets'],clips=animation.labels() if animation else [],
            animation_note='Loading animations…' if pending else current['animation_note'],animation_pending=pending)
        if purpose=='export':
            info.update(purpose='export',materials=[dict(index=m.get('index',i),name=m.get('name'),path=m.get('path'))
                                                    for i,m in enumerate(current['materials'])])
            return info
        result=mesh_payload(current,lod,look)
        result.update(info,textures=current['textures'],texture_list=current['texture_list'],
            materials=[dict(index=m.get('index',i),name=m.get('name'),path=m.get('path'),**current['material_rules'].get(m.get('path'),{}))
                       for i,m in enumerate(current['materials'])],
            limitation='Native bind/rest geometry only. Neutral viewer shading; no runtime material, animation, attachment or game-equivalence claim.')
        from rc2016.viewer.viewport import prepare_viewport
        result["_prepared_viewport"]=prepare_viewport(result)
        return result
