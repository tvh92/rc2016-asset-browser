"""On-selection native geometry loading. No Blender, renders, receipt writes or shaders."""
from __future__ import annotations
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from Animation.pipeline.archive import Archive, locate, decompress
from Animation.pipeline.native_model import decode_model
from Animation.animation_audit import section_table


def presentation_basis(value):
    x,y,z=value
    return (x,-z,y)


def mesh_payload(decoded, lod='Highest', look=0):
    from Animation.pipeline.native_model import _selected_meshes
    selected=set(_selected_meshes(decoded['looks'],decoded['mesh_record_count'],lod,look))
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
            self.archive=Archive(archive_root,Path(__file__).parent/'source_preview_cache',lambda *_a,**_k:None)
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
        for entry in self.archive.entries.values():
            if entry['type'] in ('gfx','swf'):
                base=entry['path'].rsplit('/',1)[-1].lower()
                if base==name or base.rsplit('.',1)[0]==stem:
                    return self._gfx_bytes(entry['path'])
        return None

    def gfx_library(self, path):
        from .gfx_render import Library, parse
        cache=getattr(self,'_gfx_cache',None)
        if cache is None or cache[0]!=self.archive:
            cache=self._gfx_cache=(self.archive,{})
        if path not in cache[1]:
            data=self._gfx_bytes(path)
            if data is None:raise ValueError('UI file not found in the archive: '+path)
            cache[1][path]=Library(parse(data),self._gfx_import)
        return cache[1][path]

    def gfx_symbols(self, path):
        from .gfx_render import symbols
        library=self.gfx_library(path)
        movie=library.movie
        return dict(path=path,symbols=symbols(movie),version=movie.version,signature=movie.signature,
                    imports=sorted({url for url,_name in movie.imports.values()}),
                    errors=list(movie.tag_counts.get('errors',[])))

    def gfx_image(self, path, char_id, frame, size, background=None):
        from .gfx_render import collect, render
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
        from .texture_preview import FORMAT_NAMES, load_texture, to_image
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
        from .texture_preview import export_textures
        if self.archive is None:raise ValueError('No source archive is loaded')
        return export_textures(self.archive,paths,folder,formats,flip,progress)

    RECENT_MODELS = 6
    RECENT_VERTEX_BUDGET = 1_500_000

    def load(self, source_root, model_id, source_path, lod='Highest', look=0, output_root=None, defer_animation=False):
        """Payload for the viewport. With ``defer_animation`` a skinned model returns at once
        with ``animation_pending=True`` and no clips; call ``load_animation`` (another worker)
        to build its clip library."""
        with self.lock:
            return self._load(source_root, model_id, source_path, lod, look, output_root, defer_animation)

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
            from .animation_preview import AnimationLibrary, model_animsets
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

    def _load(self, source_root, model_id, source_path, lod, look, output_root, defer_animation=False):
        self._ensure_archive(source_root)
        entry=self.archive.entries.get(model_id)
        if not entry or entry['type']!='model' or entry['path']!=source_path:
            raise ValueError('Selected asset ID and archive path do not match the canonical source index')
        if self.identity!=self._recent_identity:
            self._recent.clear()   # identity was reset from outside: decode afresh
        # Animation sets are found through the output root, so it is part of the identity.
        identity=(model_id,source_path,str(output_root) if output_root else None)
        cached=self._recent.get(identity)
        if cached is not None:
            self._remember(identity,cached)
        else:
            packed,packed_hash=self.archive.bytes(model_id)
            data,codec=decompress(packed)
            # The existing decoder takes a file path. Keep just this selected model
            # in a private temporary directory; never write extracted archive trees.
            with tempfile.TemporaryDirectory(prefix='rcps4-view-') as tmp:
                file=Path(tmp)/'selected.model';file.write_bytes(data)
                decoded=decode_model(file,lod='All',expected_sha256=hashlib.sha256(data).hexdigest())
            sections=section_table(data,0)
            joint_count=next((s['size']//16 for s in sections if s['label_hash']=='0x15DF9D3B'),0)
            decoded.update(packed_sha256=packed_hash,codec=codec,joint_count=joint_count)
            from .texture_preview import material_textures, model_texture_list
            decoded['textures']=material_textures(self.archive,decoded['materials'],self.texture_cache)
            decoded['texture_list']=model_texture_list(self.archive,decoded['materials'])
            from .texture_preview import FORMAT_NAMES, texture_header
            for row in decoded['texture_list']:
                try:
                    header=texture_header(self.archive,row['path'])
                    full=(header['hd_width'],header['hd_height']) if header['hd_width'] else (header['sd_width'],header['sd_height'])
                    row.update(format=header['format'],format_name=FORMAT_NAMES.get(header['format'],str(header['format'])),width=full[0],height=full[1])
                except (ValueError,KeyError,OSError) as error:
                    row.update(format_name='unreadable',error=str(error))
            decoded['_archive_path']=source_path;decoded['animation_note']='';decoded['animsets']=[]
            cached=dict(decoded=decoded,data=data,animation=None,animation_built=False,build_lock=threading.Lock(),
                        vertices=sum(len(m['positions']) for m in decoded['meshes']))
            self._remember(identity,cached)
        pending=bool(defer_animation and cached['decoded']['joint_count'] and not cached['animation_built'])
        if not pending:
            self._build_animation(cached,output_root)
        self.current,self.animation=cached['decoded'],cached['animation']
        self.identity=self._recent_identity=identity
        result=mesh_payload(self.current,lod,look)
        result.update(model_id=model_id,source_path=source_path,archive_root=str(self.archive.root),
            source_packed_sha256=self.current['packed_sha256'],codec=self.current['codec'],
            kind='skinned' if self.current['joint_count'] else 'static',joint_count=self.current['joint_count'],
            looks=self.current['looks'],lod=lod,look=look,textures=self.current['textures'],
            texture_list=self.current['texture_list'],mesh_record_count=self.current['mesh_record_count'],
            materials=[dict(index=m.get('index',i),name=m.get('name'),path=m.get('path')) for i,m in enumerate(self.current['materials'])],
            animsets=self.current['animsets'],
            clips=self.animation.labels() if self.animation else [],
            animation_note='Loading animations…' if pending else self.current['animation_note'],animation_pending=pending,
            limitation='Native bind/rest geometry only. Neutral viewer shading; no runtime material, animation, attachment or game-equivalence claim.')
        from .viewport import prepare_viewport
        result["_prepared_viewport"]=prepare_viewport(result)
        return result
