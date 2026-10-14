"""Tk asset/result browser added to the existing extractor; workers never touch Tk."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
import queue
import threading
import uuid
from rc2016.app import catalog
from rc2016.viewer.loader import SourcePreviewLoader
from rc2016.viewer.viewport import ModelViewport
from rc2016.app.workers import LatestWork, ReviewProcess, open_local, review_command

ROOT = Path(__file__).resolve().parents[2]
REST_POSE = 'Rest pose (no animation)'


def source_details(payload, used_by='', native_translation=True):
    """Details tab text for one decoded source model, read from the game files only."""
    lines = [payload['source_path'], f"Asset ID: {payload['model_id']}"]
    if used_by:
        lines.append(f'Used by: {used_by}')
    joints = payload.get('joint_count') or 0
    lines.append(f"Geometry: skinned, {joints} joints" if joints else 'Geometry: static (no skeleton)')
    looks = payload.get('looks') or []
    lines.append(f"Meshes: {payload.get('mesh_record_count', '?')} in the file · {len(looks)} looks · "
                 f"showing look {payload.get('look', 0)}, {payload.get('lod', 'Highest')} LOD: "
                 f"{len(payload.get('selected_mesh_indices') or [])} meshes, "
                 f"{len(payload['vertices']):,} vertices, {len(payload['triangles']):,} triangles")
    materials = payload.get('materials') or []
    lines.append(f'Materials ({len(materials)}), with the viewer shading rule each one gets:')
    for i, m in enumerate(materials):
        lines.append(f"  {m.get('index', i)}: {m.get('name') or '(unnamed)'}" + (f" — {m['path']}" if m.get('path') else ' — no material file'))
        if m.get('rule_kind'):
            shown = m
            if not native_translation and m.get('rule_kind') == 'native translation':
                from rc2016.formats.links import rule_coverage
                kind, rule = rule_coverage(m.get('graphs') or [])
                shown = dict(m, rule_kind=kind, rule=rule)
            lines.append('      ' + rule_text(shown))
            for limitation in m.get('native_limitations', ()):
                lines.append('        ' + limitation)
    textures = payload.get('texture_list') or []
    graph = sum(1 for t in textures if t.get('via_graph'))
    lines.append(f"Textures: {len(textures)}" + (f" ({graph} graph defaults)" if graph else '') + ' — listed in the Textures tab')
    clips, animsets = payload.get('clips') or [], payload.get('animsets') or []
    if clips:
        lines.append(f"Animations: {len(clips)} clips from {len(animsets)} animation set{'s' if len(animsets) != 1 else ''}:")
        lines += [f'  {a}' for a in animsets]
    else:
        lines.append('Animations: ' + (payload.get('animation_note') or ('none; static model' if not joints else 'none found')))
    lines.append(f"Packed source SHA-256: {payload.get('source_packed_sha256', '?')} ({payload.get('codec', '?')})")
    return '\n'.join(lines)


def rule_text(material):
    """'shading: exact rule (graph x)' and similar for a payload material with rule_kind."""
    stem = lambda path: path.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    graphs = ', '.join(stem(g) for g in material.get('graphs') or []) or 'none'
    kind = material['rule_kind']
    if kind == 'native translation':
        return f"shading: native translation — bounded deferred surface of graph {graphs}; game equivalence false"
    if kind == 'exact rule':
        return f'shading: exact rule — decoded writer of graph {graphs}'
    if kind == 'family fallback':
        return f"shading: family fallback ({material['rule'].rstrip('/')}*) — graph {graphs} has no rule of its own"
    if kind == 'generic reading':
        return f'shading: generic reading (coloured F0 + gloss alpha) — graph {graphs} has no decoded rule'
    return 'shading: generic reading — the material names no material graph'


def lighting_controls(ttk, parent, viewport, lighting_var, tonemap_var):
    """Lighting source selector. Tonemap lives in the separate Display group."""
    def lighting_changed(_event=None):
        if lighting_var.get() == 'Shared HDRI':
            tonemap_var.set('Khronos PBR Neutral')
        viewport.set_lighting(lighting_var.get(), tonemap_var.get())
    box = ttk.Combobox(parent, textvariable=lighting_var, values=viewport.LIGHTINGS, state='readonly', width=12)
    box.pack(side='left', padx=(4, 0)); box.bind('<<ComboboxSelected>>', lighting_changed)
    note = ttk.Label(parent, text='', foreground='#6b7280')
    note.pack(side='left', padx=4)
    viewport.on_lighting_changed = lambda: note.configure(text=viewport.lighting_note or '')


MODEL_SUFFIX_TOKENS = ('enm', 'hero', 'npc', 'wpn')


def clip_tokens(name):
    """Grouping words of a clip name: its ``_`` parts, cut before a model suffix (``Idle_enm_X`` -> Idle)."""
    parts = [part for part in name.split('_') if part]
    for k in range(1, len(parts)):
        if parts[k] in MODEL_SUFFIX_TOKENS:
            return parts[:k]
    return parts


def group_clips(labels):
    """Menu tree for clip labels: [(text, clip index | subtree)], sorted case-insensitively.

    Labels are ``"animset: Name"`` when a model has several animsets, else ``"Name"``.
    Clips group by animset, then word by word: a word shared by several clips becomes a folder
    (Jetpack > One Hand > Fire), so the word that separates a clip's family is found wherever it sits.
    Clips alone on their word go under Other. A folder whose only child is a folder is merged
    into it (Aim > Vertical becomes Aim Vertical).
    """
    def ordered(items):
        return sorted(items, key=lambda item: (item[0].lower(), item[1]))

    def fold(label, children):
        while len(children) == 1 and isinstance(children[0][1], list):
            label, children = f'{label} {children[0][0]}', children[0][1]
        return (label, children)

    def build(items):
        """items: (words left, name, index) -> menu nodes."""
        groups, alone, folders = {}, [], []
        for words, name, index in sorted(items, key=lambda item: (item[1].lower(), item[2])):
            if words:
                groups.setdefault(words[0].casefold(), []).append((words[0], words[1:], name, index))
            else:
                alone.append((name, index))
        for members in groups.values():
            if len(members) == 1:
                alone.append((members[0][2], members[0][3]))
            else:
                folders.append(fold(members[0][0], build([(rest, name, index) for _word, rest, name, index in members])))
        folders.sort(key=lambda node: node[0].lower())
        if not folders:
            return ordered(alone)
        return folders + ([('Other', ordered(alone))] if alone else [])

    sets = {}
    for index, label in enumerate(labels):
        animset, _sep, name = label.rpartition(': ')
        sets.setdefault(animset, []).append((clip_tokens(name), name, index))
    if len(sets) <= 1:
        return build(next(iter(sets.values()), []))
    return [(animset, build(items)) for animset, items in sorted(sets.items(), key=lambda s: s[0].lower())]


def count_clips(tree):
    return sum(1 if isinstance(node, int) else count_clips(node) for _text, node in tree)


def normalize_details(details):
    d=asdict(details)
    blend=d.get('blend') or {}
    d.update(blend_path=blend.get('path'),blend_sha256=blend.get('declared_sha256'),blend_hash_status='hash_verified' if blend.get('hash_status')=='match' else blend.get('hash_status','missing'))
    status=d.get('status','UNKNOWN')
    support=('unsupported' if status=='UNSUPPORTED' else 'failed' if status=='FAILED' else
             'limited' if 'LIMITATION' in status or status=='ARMATURE_ANIMATIONS_ONLY' else
             'supported' if status.startswith('COMPLETE_') else 'unknown')
    validation=d.get('validation') or {}
    d['integrity_verified']=d['blend_hash_status']=='hash_verified' and validation.get('hash_status')=='match'
    d['support_status']=support
    d.setdefault('animation_status','available' if d.get('supported_clips') else 'none' if support!='unknown' else 'unknown')
    summaries=list(d.get('summary') or [])
    summaries.insert(0,'Build: '+status.replace('_',' ').lower())
    summaries.append('Master integrity: '+d['blend_hash_status'].replace('_',' '))
    summaries.append(f"Source clips: {len(d.get('supported_clips',[]))} supported, {len(d.get('unsupported_clips',[]))} unsupported, {len(d.get('unknown_clips',[]))} unknown")
    summaries.append('Backend reuse rechecks source and implementation fingerprints. Browsing does not certify reuse.')
    summaries.append('COMPLETE describes delivery coverage. Visual review and game equivalence are separate.')
    d['summary']=summaries
    return d


class AssetBrowser:
    ASSET_TYPES=('Model','Level','Flash UI')

    def __init__(self, app, parent, type_host=None, model_view=None):
        """``parent``: the model Panedwindow (model list | this review frame). ``type_host``: the
        frame where the asset-type views swap (default ``parent.master``); ``model_view``: the
        widget holding the model list and this frame (default ``parent``)."""
        self.app, self.tk, self.ttk = app, app.tk, app.ttk
        self.meta_work, self.detail_work, self.artifact_work = LatestWork(), LatestWork(), LatestWork()
        self.source_work=LatestWork()
        # Clip libraries load after the mesh is shown (SourcePreviewLoader.load_animation).
        self.clip_list_work=LatestWork();self.source_output_root=None
        self.source_loader=SourcePreviewLoader()
        self.source_selection=None
        self.source_payload=None
        self.refs, self.metadata, self.details = [], {}, None
        self.active_ref = None
        self.review = None
        self.review_report = None
        self.review_worker = None
        self.review_events = queue.Queue()
        self.selected_identity = None
        self.closed = False
        self.generation = 0
        self.selected_record = None
        self.source_text = ''
        self.build_text = ''
        self.placement_text = ''
        from rc2016.app.links import LinkService
        self.links = LinkService(app)   # archive-wide links: texture users, level placements, coverage
        tk, ttk = self.tk, self.ttk
        self.frame = ttk.LabelFrame(parent, text='Asset and result review', padding=8)
        parent.add(self.frame, weight=2)
        self.frame.columnconfigure(0,weight=1)
        self.frame.rowconfigure(1,weight=1)
        self.status_var=tk.StringVar(value='Select an asset to load its geometry. Blender is optional.')
        ttk.Label(self.frame,textvariable=self.status_var,wraplength=410).grid(row=0,column=0,sticky='ew',pady=3)
        # Model sub-tabs only; Level and Flash UI are separate asset types (_build_type_views).
        self.notebook=notebook=ttk.Notebook(self.frame);notebook.grid(row=1,column=0,sticky='nsew')
        result=ttk.Frame(notebook,padding=6);controls=ttk.Frame(notebook,padding=6)
        view=ttk.Frame(notebook,padding=6)
        textures=ttk.Frame(notebook,padding=6)
        notebook.add(view,text='3D model');notebook.add(textures,text='Textures')
        notebook.add(result,text='Details')
        from rc2016.app.gallery import Gallery   # the Gallery tab: thumbnail grid of the selected or searched models
        self.gallery=Gallery(self,notebook)
        self._build_texture_tab(textures)
        self._build_type_views(type_host if type_host is not None else parent.master,model_view if model_view is not None else parent)
        view.columnconfigure(0,weight=1);view.rowconfigure(0,weight=1)
        self.viewport=ModelViewport(view,width=420,height=280)
        self.viewport.grid(row=0,column=0,sticky='nsew')
        native=ttk.Frame(view);native.grid(row=1,column=0,sticky='ew',pady=6)
        ttk.Label(native,text='Source look').pack(side='left')
        self.native_look=self.tk.StringVar(value='0')
        self.native_look_box=ttk.Combobox(native,textvariable=self.native_look,values=(),state='disabled',width=4)
        self.native_look_box.pack(side='left',padx=4)
        self.native_look_box.bind('<<ComboboxSelected>>',lambda _e:self._reload_source())
        self.native_lod=self.tk.StringVar(value='Highest')
        self.native_lod_box=ttk.Combobox(native,textvariable=self.native_lod,values=('Highest','Lowest','All'),state='disabled',width=9)
        self.native_lod_box.pack(side='left',padx=4)
        self.native_lod_box.bind('<<ComboboxSelected>>',lambda _e:self._reload_source())
        ttk.Button(native,text='Reset view',command=self.viewport._reset_camera).pack(side='left',padx=4)
        self.view_mode=self.tk.StringVar(value='Shaded')
        mode_box=ttk.Combobox(native,textvariable=self.view_mode,values=self.viewport.MODES,state='readonly',width=17)
        mode_box.pack(side='left',padx=4)
        mode_box.bind('<<ComboboxSelected>>',lambda _e:self.viewport.set_mode(self.view_mode.get()))
        self.lighting_var=self.tk.StringVar(value=self.viewport.LIGHTINGS[0])
        self.lighting_enabled_var=self.tk.BooleanVar(value=True)
        lighting_group=ttk.LabelFrame(view,text='Lighting',padding=(4,2))
        lighting_group.grid(row=2,column=0,sticky='ew',pady=(0,4))
        self.lighting_enabled_check=ttk.Checkbutton(
            lighting_group,text='On',variable=self.lighting_enabled_var,
            command=lambda:self.viewport.set_lighting_enabled(self.lighting_enabled_var.get()))
        self.lighting_enabled_check.pack(side='left')
        self.lighting_controls=ttk.Frame(lighting_group)
        self.lighting_controls.pack(side='left')
        self.tonemap_var=self.tk.StringVar(value=self.viewport.TONEMAPS[0])
        lighting_controls(ttk,self.lighting_controls,self.viewport,self.lighting_var,self.tonemap_var)
        display=ttk.LabelFrame(view,text='Display',padding=(4,2))
        display.grid(row=3,column=0,sticky='ew',pady=(0,4))
        ttk.Label(display,text='Tonemap').pack(side='left')
        tonemap_box=ttk.Combobox(display,textvariable=self.tonemap_var,values=self.viewport.TONEMAPS,state='readonly',width=19)
        tonemap_box.pack(side='left',padx=4)
        tonemap_box.bind('<<ComboboxSelected>>',lambda _e:self.viewport.set_lighting(tonemap=self.tonemap_var.get()))
        materials=ttk.LabelFrame(view,text='Materials',padding=(4,2))
        materials.grid(row=4,column=0,sticky='ew',pady=(0,4))
        self.native_translation_var=self.tk.BooleanVar(value=True)
        ttk.Checkbutton(materials,text='Native translation',variable=self.native_translation_var,
                        command=self._native_translation_changed).pack(side='left')
        self.material_previews_var=self.tk.BooleanVar(value=True)
        self.experimental_materials_var=self.tk.BooleanVar(value=False)
        experimental=ttk.LabelFrame(view,text='Experimental',padding=(4,2))
        experimental.grid(row=5,column=0,sticky='ew',pady=(0,4))
        ttk.Checkbutton(experimental,text='Enable',variable=self.experimental_materials_var,
                        command=self._experimental_materials_changed).pack(side='left')
        ttk.Checkbutton(experimental,text='Material previews',variable=self.material_previews_var,
                        command=self._experimental_materials_changed).pack(side='left',padx=(8,0))
        ttk.Label(experimental,text='Mountain albedo: matte. Empty blend mask: review value 1; runtime unverified.',foreground='#6b7280').pack(side='left',padx=8)
        self.viewport.set_lighting_enabled(self.lighting_enabled_var.get())
        self.viewport.set_experimental_materials(
            self.experimental_materials_var.get() and self.material_previews_var.get())
        anim=ttk.Frame(view);anim.grid(row=6,column=0,sticky='ew',pady=(0,6))
        anim.columnconfigure(3,weight=1)
        ttk.Label(anim,text='Animation').grid(row=0,column=0,padx=(0,4))
        self.clip_choice=tk.StringVar(value=REST_POSE)
        # A themed button that opens a searchable clip list (_open_clip_picker), not a Tk menu.
        self.clip_button=ttk.Menubutton(anim,textvariable=self.clip_choice,state='disabled',width=34)
        self.clip_button.bind('<ButtonPress-1>',self._open_clip_picker)
        self.clip_button.bind('<KeyPress-space>',self._open_clip_picker)
        self.clip_button.grid(row=0,column=1,padx=4)
        self.clip_labels=[];self.clip_index=-1;self.clip_popup=None
        self.clip_only_var=tk.BooleanVar(value=False)
        self.clip_only_check=ttk.Checkbutton(anim,text='Extract only this clip',variable=self.clip_only_var,state='disabled')
        self.clip_only_check.grid(row=1,column=1,columnspan=4,sticky='w',padx=4,pady=(2,0))
        self._fill_clip_menu([])
        self.play_button=ttk.Button(anim,text='Pause',width=7,command=self._toggle_play,state='disabled')
        self.play_button.grid(row=0,column=2,padx=4)
        self.frame_pos=tk.DoubleVar(value=0)
        self.frame_slider=ttk.Scale(anim,from_=0,to=1,variable=self.frame_pos,command=self._scrub,state='disabled')
        self.frame_slider.grid(row=0,column=3,sticky='ew',padx=4)
        self.frame_text=tk.StringVar(value='')
        ttk.Label(anim,textvariable=self.frame_text,width=18,anchor='w').grid(row=0,column=4,sticky='w')   # fits '1234/1234 @ 29.97 fps'
        self.anim_work=LatestWork()
        self.animation=None;self.anim_frame=0;self.playing=False;self.play_job=None
        self.model_info=self.tk.StringVar(value='Select one source model. It loads directly; no Blender is needed.')
        ttk.Label(view,textvariable=self.model_info,wraplength=410).grid(row=7,column=0,sticky='ew')
        ttk.Label(view,text='Drag: orbit · Right drag: pan · Wheel: zoom\nShaded = game BRDF (_c, _g gloss, _n normals, self-lit) under the viewer rig or the shared review HDRI (predicts the Blender review render), not game lighting',foreground='#6b7280',wraplength=520).grid(row=8,column=0,sticky='w',pady=5)
        result.columnconfigure(0,weight=1);result.rowconfigure(0,weight=1)
        self.description=app.ScrolledText(result,height=8,wrap='word',state='disabled',font='TkDefaultFont')
        self.description.grid(row=0,column=0,sticky='nsew')
        links=ttk.Frame(result);links.grid(row=3,column=0,sticky='ew')
        for text, fn in [('Open Blender',self._open_blend),('Artifacts / logs',self._open_folder)]:
            ttk.Button(links,text=text,command=fn).pack(side='left',padx=2)
        controls.columnconfigure(1,weight=1)
        self.inspect_button=ttk.Button(controls,text='Load saved scene controls',command=lambda:self.start_review('inspect'),state='disabled')
        self.inspect_button.grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,8))
        self.clip_var=tk.StringVar(value='Saved pose');self.variant_var=tk.StringVar(value='Saved visibility');self.lod_var=tk.StringVar(value='Saved LOD')
        self.clip_box=self._selector(controls,1,'Clip',self.clip_var)
        self.frame_var=tk.StringVar(value='1')
        ttk.Label(controls,text='Source frame').grid(row=2,column=0,sticky='w')
        self.frame_box=ttk.Spinbox(controls,textvariable=self.frame_var,from_=1,to=1,state='disabled',width=12)
        self.frame_box.grid(row=2,column=1,sticky='ew',pady=5)
        self.timing_var=tk.StringVar(value='Load the scene to read supported source timing.')
        ttk.Label(controls,textvariable=self.timing_var,wraplength=390).grid(row=3,column=0,columnspan=2,sticky='ew')
        self.variant_box=self._selector(controls,4,'Variant',self.variant_var)
        self.lod_box=self._selector(controls,5,'Look / LOD',self.lod_var)
        self.selector_note=tk.StringVar(value='Variants and LODs are available only when source identities are recorded in this saved scene.')
        ttk.Label(controls,textvariable=self.selector_note,wraplength=390).grid(row=6,column=0,columnspan=2,sticky='ew',pady=6)
        ttk.Button(controls,text='Cancel review',command=self.cancel_review).grid(row=8,column=0,columnspan=2,sticky='ew')
        ttk.Label(controls,text='Shared review HDRI · Cycles OptiX RTX 3060\nSource timing; additive clips are standalone previews.',wraplength=390,foreground='#6b7280').grid(row=9,column=0,columnspan=2,sticky='ew',pady=8)
        self.clip_box.bind('<<ComboboxSelected>>',lambda _e:self._clip_changed())
        self.reload()
        app.root.after(100,self._drain)

    # Asset types (Model / Level / Flash UI) --------------------------------
    def _build_type_views(self,host,model_view):
        """Level and Flash UI views: a file list on the left, the viewer on the right. They are
        built once and only hidden on a switch, so loaded levels and UI files keep their state."""
        tk,ttk,app=self.tk,self.ttk,self.app
        info=model_view.grid_info()
        self._type_grid={k:info[k] for k in ('row','column','rowspan','columnspan','sticky','padx','pady') if k in info} or dict(row=1,column=0,sticky='nsew')
        self.asset_type=self.ASSET_TYPES[0]
        self._types_shown={self.asset_type}
        # Level viewer (level_panel.py): own archive instance and worker; lists levels on first show.
        from rc2016.levels.panel import LevelPanel
        self.level_view=ttk.Panedwindow(host,orient='horizontal')
        level_list=ttk.LabelFrame(self.level_view,text='Levels',padding=6)
        level_right=ttk.LabelFrame(self.level_view,text='Level viewer',padding=6)
        self.level_view.add(level_list,weight=1);self.level_view.add(level_right,weight=4)
        level_right.columnconfigure(0,weight=1);level_right.rowconfigure(0,weight=1)
        self.level_panel=LevelPanel(level_right,tk,ttk,lambda:app.source_var.get().strip(),list_parent=level_list,
                                    on_open_model=self.open_model)
        self.level_panel.frame.grid(row=0,column=0,sticky='nsew')
        # Flash UI (flash_tab.py): the .gfx/.swf list on the left; reads the list on first show.
        from rc2016.flash.tab import FlashTab
        self.flash_view=ttk.Panedwindow(host,orient='horizontal')
        flash_list=ttk.LabelFrame(self.flash_view,text='Flash UI files',padding=6)
        flash_right=ttk.LabelFrame(self.flash_view,text='Flash UI viewer',padding=6)
        self.flash_view.add(flash_list,weight=1);self.flash_view.add(flash_right,weight=4)
        self.flash_tab=FlashTab(app,flash_right,self.source_loader,list_parent=flash_list)
        self.type_views={'Model':model_view,'Level':self.level_view,'Flash UI':self.flash_view}

    def show_asset_type(self,kind):
        """Show one asset type's list and viewer; the others are hidden, not destroyed."""
        if kind not in self.type_views:raise ValueError(f'Unknown asset type: {kind}')
        for name,widget in self.type_views.items():
            if name!=kind:widget.grid_remove()
        self.type_views[kind].grid(**self._type_grid)
        self.asset_type=kind
        first=kind not in self._types_shown
        self._types_shown.add(kind)
        if kind=='Level' and not self.level_panel.levels:
            self.level_panel.list_levels()
        elif kind=='Flash UI' and not self.flash_tab.files:
            self.flash_tab.load_files()
        if first and kind!='Model':
            pane=self.type_views[kind]
            def place_sash():
                width=pane.winfo_width()
                if width>1:pane.sashpos(0,max(220,int(width*0.24)))
            self.app.root.after(50,place_sash)

    # Texture viewer ------------------------------------------------------
    MODEL_TEXTURES,ARCHIVE_TEXTURES='This model','Whole archive'
    TEXTURE_ROW_LIMIT=3000   # Treeview stays responsive; the filter narrows the rest

    def _build_texture_tab(self,tab):
        tk,ttk=self.tk,self.ttk
        self.texture_work=LatestWork();self.texture_list_work=LatestWork()
        self.texture_events=queue.Queue()
        self.model_texture_rows=[];self.archive_texture_rows=None;self.archive_texture_root=None
        self.texture_matches=[];self.texture_rows=[];self.texture_model=None
        self.texture_image=None;self.texture_facts=None;self.texture_photo=None
        self.texture_exporting=False;self.texture_filter_job=None
        tab.columnconfigure(0,weight=1);tab.rowconfigure(2,weight=1)
        top=ttk.Frame(tab);top.grid(row=0,column=0,sticky='ew',pady=(0,4))
        top.columnconfigure(3,weight=1)
        ttk.Label(top,text='Show').grid(row=0,column=0)
        self.texture_mode=tk.StringVar(value=self.MODEL_TEXTURES)
        mode=ttk.Combobox(top,textvariable=self.texture_mode,values=(self.MODEL_TEXTURES,self.ARCHIVE_TEXTURES),state='readonly',width=13)
        mode.grid(row=0,column=1,padx=4);mode.bind('<<ComboboxSelected>>',lambda _e:self._texture_mode_changed())
        ttk.Label(top,text='Filter').grid(row=0,column=2,padx=(8,4))
        self.texture_filter=tk.StringVar()
        ttk.Entry(top,textvariable=self.texture_filter).grid(row=0,column=3,sticky='ew')
        self.texture_filter.trace_add('write',lambda *_a:self._schedule_texture_filter())
        listing=ttk.Frame(tab);listing.grid(row=1,column=0,sticky='ew')
        listing.columnconfigure(0,weight=1)
        self.texture_tree=ttk.Treeview(listing,columns=('format','size','where'),height=7,selectmode='extended')
        for column,text,width in [('#0','Texture',190),('format','Format',80),('size','Size',80),('where','Materials / folder',150)]:
            self.texture_tree.heading(column,text=text);self.texture_tree.column(column,width=width,stretch=column in ('#0','where'))
        self.texture_tree.grid(row=0,column=0,sticky='ew')
        scroll=ttk.Scrollbar(listing,orient='vertical',command=self.texture_tree.yview);scroll.grid(row=0,column=1,sticky='ns')
        self.texture_tree.configure(yscrollcommand=scroll.set)
        self.texture_tree.bind('<<TreeviewSelect>>',lambda _e:self._texture_selected())
        self.texture_canvas=tk.Canvas(tab,width=420,height=240,background='#2b2b2b',highlightthickness=0)
        self.texture_canvas.grid(row=2,column=0,sticky='nsew',pady=4)
        self.texture_canvas.bind('<Configure>',lambda _e:self._draw_texture())
        options=ttk.Frame(tab);options.grid(row=3,column=0,sticky='ew')
        ttk.Label(options,text='Channels').pack(side='left')
        # RGB by default: alpha is usually mask data (e.g. the _c self-lit mask), not transparency.
        self.texture_channels=tk.StringVar(value='RGB')
        box=ttk.Combobox(options,textvariable=self.texture_channels,values=('RGBA','RGB','R','G','B','Alpha'),state='readonly',width=6)
        box.pack(side='left',padx=4);box.bind('<<ComboboxSelected>>',lambda _e:self._draw_texture())
        self.texture_flip=tk.BooleanVar(value=False)
        ttk.Checkbutton(options,text='Flip vertically',variable=self.texture_flip,command=self._draw_texture).pack(side='left',padx=8)
        ttk.Label(options,text='Slice').pack(side='left')
        self.texture_slice=tk.StringVar(value='1')
        self.texture_slice_box=ttk.Spinbox(options,textvariable=self.texture_slice,from_=1,to=1,width=4,state='disabled',
                                           command=self._texture_slice_changed)
        self.texture_slice_box.pack(side='left',padx=4)
        self.texture_slice_box.bind('<Return>',lambda _e:self._texture_slice_changed())
        export=ttk.Frame(tab);export.grid(row=4,column=0,sticky='ew',pady=(4,0))
        ttk.Label(export,text='Export as').pack(side='left')
        self.export_formats={'texture':tk.BooleanVar(value=False),'dds':tk.BooleanVar(value=False),'png':tk.BooleanVar(value=True)}
        for key,text in [('texture','Original .texture'),('dds','DDS'),('png','PNG')]:
            ttk.Checkbutton(export,text=text,variable=self.export_formats[key]).pack(side='left',padx=3)
        self.export_selected_button=ttk.Button(export,text='Export selected…',command=lambda:self._export_textures(False),state='disabled')
        self.export_selected_button.pack(side='left',padx=(8,2))
        self.export_all_button=ttk.Button(export,text='Export listed…',command=lambda:self._export_textures(True),state='disabled')
        self.export_all_button.pack(side='left',padx=2)
        users=ttk.Frame(tab);users.grid(row=5,column=0,sticky='ew',pady=(4,0));users.columnconfigure(1,weight=1)
        ttk.Label(users,text='Used by').grid(row=0,column=0,padx=(0,4))
        self.texture_users_var=tk.StringVar(value='');self.texture_user_paths=[]
        self.texture_users_box=ttk.Combobox(users,textvariable=self.texture_users_var,state='disabled')
        self.texture_users_box.grid(row=0,column=1,sticky='ew')
        self.texture_open_button=ttk.Button(users,text='Open model',state='disabled',
                                            command=lambda:self._open_texture_user())
        self.texture_open_button.grid(row=0,column=2,padx=(4,0))
        self.texture_info=tk.StringVar(value='Select a source model to list its textures, or show the whole archive.')
        ttk.Label(tab,textvariable=self.texture_info,wraplength=410).grid(row=6,column=0,sticky='ew',pady=(4,0))
        ttk.Label(tab,text='Unflipped = as stored in the game, top row first. The model builds do not flip images; they flip UV V instead.\n'
                  'Flip applies to the preview and PNG export. Original .texture and DDS are written unchanged, at full size.',
                  foreground='#6b7280',wraplength=410).grid(row=7,column=0,sticky='w',pady=4)

    # Listing
    def _archive_mode(self):
        return self.texture_mode.get()==self.ARCHIVE_TEXTURES

    def _set_texture_list(self,payload):
        identity=(payload['model_id'],payload['source_path'])
        if identity==self.texture_model:return
        self.texture_model=identity
        self.model_texture_rows=list(payload.get('texture_list') or [])
        if not self._archive_mode():self._fill_texture_tree()

    def _clear_texture_list(self,message):
        self.texture_model=None;self.model_texture_rows=[]
        if self._archive_mode():return
        self._fill_texture_tree(message)

    def _texture_mode_changed(self):
        if not self._archive_mode():
            self._fill_texture_tree();return
        root=self.app.source_var.get().strip()
        if not root:
            self._fill_texture_tree('Choose the extracted source archive first.');return
        if self.archive_texture_rows is not None and self.archive_texture_root==root:
            self._fill_texture_tree();return
        self._fill_texture_tree('Reading the archive\'s texture index…')
        self.texture_list_work.submit(lambda r=root:(r,self.source_loader.archive_textures(r)))

    def _schedule_texture_filter(self):
        if self.texture_filter_job is not None:self.app.root.after_cancel(self.texture_filter_job)
        self.texture_filter_job=self.app.root.after(250,self._run_texture_filter)

    def _run_texture_filter(self):
        if self.texture_filter_job is not None:self.app.root.after_cancel(self.texture_filter_job)
        self.texture_filter_job=None
        self._fill_texture_tree()

    def _fill_texture_tree(self,message=None):
        archive=self._archive_mode()
        source=(self.archive_texture_rows or []) if archive else self.model_texture_rows
        words=self.texture_filter.get().lower().split()
        self.texture_matches=[r for r in source if all(w in r['path'].lower() for w in words)]
        self.texture_rows=self.texture_matches[:self.TEXTURE_ROW_LIMIT]
        self.texture_tree.delete(*self.texture_tree.get_children())
        for i,row in enumerate(self.texture_rows):
            self.texture_tree.insert('','end',iid=str(i),text=Path(row['path']).name,values=self._texture_row_values(row,archive))
        self.texture_work.submit(lambda:None)
        self.texture_image=None;self.texture_facts=None;self._draw_texture()
        self.texture_slice_box.configure(state='disabled');self.texture_slice.set('1')
        idle=not self.texture_exporting
        self.export_all_button.configure(state='normal' if self.texture_matches and idle else 'disabled')
        self.export_selected_button.configure(state='disabled')
        if message:self.texture_info.set(message);return
        shown='' if len(self.texture_matches)<=len(self.texture_rows) else f' Showing the first {len(self.texture_rows):,}; type in Filter to narrow.'
        if archive:
            text=f'{len(self.texture_matches):,} of {len(source):,} archive textures match.{shown}' if words else f'{len(source):,} textures in the archive, with or without a model.{shown}'
        elif self.texture_model is None:
            text='Select a source model to list its textures, or show the whole archive.'
        elif not source:
            text='This model\'s materials reference no textures.'
        else:
            text=(f'{len(self.texture_matches)} of {len(source)} model textures match.' if words else
                  f'{len(source)} textures referenced by this model\'s materials.')+' Ctrl/Shift-click to select several.'
        self.texture_info.set(text)

    @staticmethod
    def _texture_row_values(row,archive):
        size=f"{row['width']}×{row['height']}" if row.get('width') else ''
        if archive:where=str(Path(row['path']).parent).replace('\\','/')
        else:where=', '.join(row['materials'])+(' (graph default)' if row.get('via_graph') else '')
        return (row.get('format_name',''),size,where)

    # Viewing
    def _selected_texture_paths(self):
        return [self.texture_rows[int(i)]['path'] for i in self.texture_tree.selection()]

    def _texture_selected(self):
        paths=self._selected_texture_paths()
        self.export_selected_button.configure(state='normal' if paths and not self.texture_exporting else 'disabled')
        focus=self.texture_tree.focus()
        if not focus:return
        path=self.texture_rows[int(focus)]['path']
        if self.texture_facts and self.texture_facts['path']==path:return
        self._show_texture_users(path)
        self.texture_slice.set('1')
        self.texture_info.set('Decoding '+path+' …')
        self.texture_work.submit(self.source_loader.texture_image,path,0)

    def _show_texture_users(self,path):
        """Fill the Used by list with the models whose materials reference this texture."""
        self.texture_user_paths=[]
        self.texture_users_box.configure(values=(),state='disabled');self.texture_open_button.configure(state='disabled')
        self.texture_users_var.set('Reading asset links…')
        def show(links):
            focus=self.texture_tree.focus()
            if not focus or self.texture_rows[int(focus)]['path']!=path:return   # another texture was chosen meanwhile
            if links is None:
                self.texture_users_var.set(self.links.message);return
            users=links.texture_users(path)
            self.texture_user_paths=[u['model'] for u in users]
            labels=[u['model']+(' (graph default)' if u['via_graph'] else '') for u in users]
            self.texture_users_box.configure(values=labels,state='readonly' if users else 'disabled')
            self.texture_users_var.set(labels[0] if labels else 'No model\'s materials reference this texture.')
            self.texture_open_button.configure(state='normal' if users else 'disabled')
            if len(users)>1:self.texture_users_box.set(f'{labels[0]}')
        self.links.request(show,key='texture users')

    def _open_texture_user(self):
        index=self.texture_users_box.current()
        paths=self.texture_user_paths
        if paths:self.open_model(paths[index if 0<=index<len(paths) else 0])

    def open_model(self,path):
        """Show an archive model in the Model asset type: select it in the tree (which loads it) and
        bring up the 3D model tab. Clears the search and filters when they hide it."""
        app=self.app
        record=next((r for r in app.records if getattr(r,'archive_path',None)==path),None)
        if record is None:
            self.app._append_log(f'{path} is not in the model list; scan the game folder first.','error');return False
        app.asset_type_var.set('Model');self.show_asset_type('Model')
        item=next((i for i,r in app.item_by_record.items() if r is record),None)
        if item is None:
            app.filter_var.set('');app.state_var.set('All')
            if hasattr(app,'shading_var'):app.shading_var.set('All')
            app.refresh_table()
            item=next((i for i,r in app.item_by_record.items() if r is record),None)
        if item is None:return False
        app.table.selection_set(item);app.table.focus(item);app.table.see(item)
        self.notebook.select(0)
        return True

    def _texture_slice_changed(self):
        if not self.texture_facts or self.texture_facts['array_size']<2:return
        try:index=int(self.texture_slice.get())-1
        except ValueError:return
        index=min(max(0,index),self.texture_facts['array_size']-1)
        if index!=self.texture_facts['slice']:
            self.texture_work.submit(self.source_loader.texture_image,self.texture_facts['path'],index)

    def _show_texture(self,payload):
        self.texture_image,self.texture_facts=payload
        f=self.texture_facts
        preview='' if f['preview_size']==f['full_size'] else f" (preview {f['preview_size'][0]}×{f['preview_size'][1]})"
        slices=f" · slice {f['slice']+1} of {f['array_size']}" if f['array_size']>1 else ''
        self.texture_info.set(f"{f['path']}\n{f['format_name']} (DXGI {f['format']}, {'sRGB' if f['srgb'] else 'linear / Non-Color'}) · "
                              f"{f['full_size'][0]}×{f['full_size'][1]}{preview}{slices} · mode {self.texture_image.mode}")
        self.texture_slice_box.configure(to=f['array_size'],state='normal' if f['array_size']>1 else 'disabled')
        self.texture_slice.set(str(f['slice']+1))
        for i,row in enumerate(self.texture_rows):   # fill in facts the archive listing lacks
            if row['path']==f['path'] and not row.get('width'):
                row.update(format_name=f['format_name'],width=f['full_size'][0],height=f['full_size'][1])
                self.texture_tree.item(str(i),values=self._texture_row_values(row,self._archive_mode()))
        self._draw_texture()

    def _draw_texture(self):
        canvas=self.texture_canvas
        canvas.delete('all')
        width,height=max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
        if self.texture_image is None:
            self.texture_photo=None;return
        from PIL import Image
        import base64, io
        image=self.texture_image
        channels=self.texture_channels.get()
        bands=image.getbands()
        if channels in ('R','G','B','Alpha'):
            band={'Alpha':'A'}.get(channels,channels)
            if image.mode=='L':image=image.copy()
            elif band in bands:image=image.getchannel(band)
            else:image=Image.new('L',image.size,255 if band=='A' else 0)
        elif channels=='RGB' or 'A' not in bands:image=image.convert('RGB')
        else:image=image.convert('RGBA')
        if self.texture_flip.get():image=image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        scale=min(width/image.size[0],height/image.size[1])
        size=(max(1,int(image.size[0]*scale)),max(1,int(image.size[1]*scale)))
        image=image.resize(size,Image.Resampling.NEAREST if scale>1 else Image.Resampling.LANCZOS)
        if image.mode=='RGBA':
            # Composite over a checkerboard so transparent areas are visible.
            cell=8
            board=Image.new('RGB',(2,2),(102,102,102));board.putpixel((0,0),(153,153,153));board.putpixel((1,1),(153,153,153))
            board=board.resize((2*cell,2*cell),Image.Resampling.NEAREST)
            checker=Image.new('RGB',image.size)
            for y in range(0,image.size[1],2*cell):
                for x in range(0,image.size[0],2*cell):checker.paste(board,(x,y))
            checker.paste(image,(0,0),image);image=checker
        buffer=io.BytesIO();image.save(buffer,format='PNG')
        self.texture_photo=self.tk.PhotoImage(data=base64.b64encode(buffer.getvalue()),format='png')
        canvas.create_image(width//2,height//2,image=self.texture_photo,anchor='center')

    # Export
    def _export_textures(self,everything):
        paths=[r['path'] for r in self.texture_matches] if everything else self._selected_texture_paths()
        formats={k for k,v in self.export_formats.items() if v.get()}
        if not paths:return
        if not formats:
            self.texture_info.set('Tick at least one export format.');return
        if len(paths)>200 and not self.app.messagebox.askyesno('Export textures',
                f'Export {len(paths):,} textures? Large exports can take a long time and use a lot of disk space.'):
            return
        folder=self.app.filedialog.askdirectory(title='Choose a folder for the exported textures')
        if not folder:return
        self.texture_exporting=True
        self.export_all_button.configure(state='disabled');self.export_selected_button.configure(state='disabled')
        flip=self.texture_flip.get()
        def progress(done,total,path):self.texture_events.put(('progress',(done,total,path)))
        def work():
            try:self.texture_events.put(('done',(folder,self.source_loader.export_textures(paths,folder,formats,flip,progress))))
            except Exception as error:self.texture_events.put(('error',str(error)))
        threading.Thread(target=work,daemon=True).start()
        self.texture_info.set(f'Exporting {len(paths):,} textures to {folder} …')

    def _drain_textures(self):
        try:
            while True:
                generation,payload,error=self.texture_list_work.events.get_nowait()
                if generation!=self.texture_list_work.generation:continue
                if error:
                    if self._archive_mode():self._fill_texture_tree('Cannot read the archive\'s textures: '+error)
                elif payload is not None:
                    self.archive_texture_root,self.archive_texture_rows=payload
                    if self._archive_mode():self._fill_texture_tree()
        except queue.Empty:pass
        try:
            while True:
                generation,payload,error=self.texture_work.events.get_nowait()
                if generation!=self.texture_work.generation:continue
                if error:
                    self.texture_info.set('Cannot show texture: '+error)
                    self.texture_image=None;self.texture_facts=None;self._draw_texture()
                elif payload is not None:self._show_texture(payload)
        except queue.Empty:pass
        try:
            while True:
                kind,value=self.texture_events.get_nowait()
                if kind=='progress':
                    done,total,path=value;self.texture_info.set(f'Exported {done:,}/{total:,}: {Path(path).name}')
                    continue
                self.texture_exporting=False
                self.export_all_button.configure(state='normal' if self.texture_matches else 'disabled')
                self.export_selected_button.configure(state='normal' if self.texture_tree.selection() else 'disabled')
                if kind=='error':
                    self.texture_info.set('Export failed: '+value);self.app._append_log('Texture export failed: '+value,'error');continue
                folder,rows=value
                failed=[r for r in rows if r['error']]
                written=sum(len(r['files']) for r in rows)
                self.texture_info.set(f'Wrote {written:,} files for {len(rows)-len(failed):,} of {len(rows):,} textures to {folder}.'
                                      +(f' {len(failed)} failed; see the log.' if failed else ''))
                for r in failed:self.app._append_log(f"Texture export failed for {r['path']}: {r['error']}",'error')
        except queue.Empty:pass

    def _selector(self,parent,row,label,variable):
        self.ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=(0,6))
        box=self.ttk.Combobox(parent,textvariable=variable,state='disabled')
        box.grid(row=row,column=1,sticky='ew',pady=5)
        return box

    def reload(self):
        # Saved results come only from the output root; they fill Result details for the selected source.
        roots=[]
        if self.app.output_var.get().strip():roots.append(Path(self.app.output_var.get()))
        self.status_var.set('Reading saved reports…')
        self.meta_work.submit(self._load_results,roots)

    @staticmethod
    def _load_results(roots):
        refs=catalog.discover_results(roots)
        priority={str(Path(r).resolve()):i for i,r in enumerate(roots)}
        refs.sort(key=lambda ref:(priority.get(str(ref.root_path),len(roots)),ref.report_kind!='aggregate',ref.label,str(ref.report_path)))
        return refs,{}   # per-ref inspection is lazy: see _choose_result

    def select_asset(self,record):
        identity=(getattr(record,'model_id',None),getattr(record,'archive_path',None))
        if identity == self.selected_identity and self.source_payload is not None and self.source_selection and str(self.app.source_var.get()) == self.source_selection[0]:return
        self.selected_identity=identity
        self.selected_record=record
        self._load_source(record)
        found=next((i for i,r in enumerate(self.refs) if (r.model_id,r.source_path)==identity),None)
        if found is not None:
            self._choose_result(found)
        else:
            self._reset('No build of this model in the output root.')


    @staticmethod
    def _recorded_source_root(ref):
        # Result summaries point at deliveries below a run directory; reports are parsed once (catalog cache).
        try:
            paths=[catalog._report(ref.report_path)[0]]
            case=catalog.report_row(ref) if ref.report_kind=='aggregate' else None
            if case:
                blend=case.get('delivery',{}).get('blend',{}).get('path')
                if blend:paths.append(catalog._report(Path(blend).parent.parent/'reports'/'manifest.json')[0])
            for data in paths:
                if data.get('source_root'):return data['source_root']
        except (OSError,ValueError,AttributeError):pass
        return ''

    def _source_root(self,ref):
        # First ref per identity wins; computed on selection instead of for every saved result.
        entry=self.metadata.setdefault((ref.model_id,ref.source_path),{})
        if 'source_root' not in entry:entry['source_root']=self._recorded_source_root(ref)
        return entry['source_root']

    def _load_source(self,record):
        if getattr(record,'model_id',None):
            self._load_source_identity(record.model_id,record.archive_path,self.app.source_var.get())
        else:
            self.source_work.submit(lambda:None)
            self.source_selection=None
            self._clear_texture_list('Loose-file transform mode has no archive textures to list.')
            self.viewport.clear('Loose-file transform mode has no archive geometry identity. Use the full archive browser to preview native models.')

    def _load_source_identity(self,model_id,source_path,source_root):
        selection=(str(source_root),model_id,source_path)
        if selection==self.source_selection and self.source_payload is not None:return
        self.source_selection=selection
        self.source_payload=None
        self.source_text=''
        self._clear_texture_list('Loading the selected model\'s textures…')
        self.native_look.set('0');self.native_lod.set('Highest')
        self.viewport.clear('Loading selected source geometry…')
        self.native_look_box.configure(state='disabled');self.native_lod_box.configure(state='disabled')
        if not source_root:
            self.source_work.submit(lambda:None)
            self.viewport.clear('Choose the extracted source archive to load this model. No Blender is required.')
            return
        self._stop_animation(reset=True)
        self.clip_list_work.submit(lambda:None)   # drop the previous model's pending clip list
        self.source_output_root=self._output_root()
        self.source_work.submit(self.source_loader.load,source_root,model_id,source_path,'Highest',0,self.source_output_root,True)

    def _output_root(self):
        # The pipeline's reference index there links models to their animsets.
        text=self.app.output_var.get().strip()
        return text or None

    def _reload_source(self):
        if not self.source_selection:return
        root,ident,path=self.source_selection
        self._stop_animation(reset=True)
        self.viewport.clear('Loading source look/LOD…')
        self.clip_list_work.submit(lambda:None)
        self.source_output_root=self._output_root()
        self.source_work.submit(self.source_loader.load,root,ident,path,self.native_lod.get(),int(self.native_look.get()),self.source_output_root,True)

    # Animation playback -------------------------------------------------
    def _set_clips(self,payload):
        clips=payload.get('clips') or []
        self._fill_clip_menu(clips)
        self.clip_choice.set(REST_POSE)
        usable=bool(clips) and self.viewport.can_animate
        self.clip_button.configure(state='normal' if usable else 'disabled')
        if not clips:
            self.frame_text.set(payload.get('animation_note') or ('Static model' if payload.get('kind')=='static' else ''))
        elif not self.viewport.can_animate:
            self.frame_text.set('Needs shader support')
        else:
            self.frame_text.set(f'{len(clips)} clips')

    def _apply_clip_list(self,clips):
        """Clips that finished loading after the mesh: only for the model still shown."""
        payload=self.source_payload
        if not payload or (payload['model_id'],payload['source_path'])!=(clips['model_id'],clips['source_path']):return
        payload.update(clips=clips['clips'],animsets=clips['animsets'],animation_note=clips['animation_note'],animation_pending=False)
        self._set_clips(payload)
        self.source_text=source_details(payload,getattr(self.selected_record,'used_by','') or '',self.viewport.native_translation)
        self._render_details()

    def _fill_clip_menu(self,clips):
        """Clip labels for the picker: rest pose, then folders by animset -> action prefix."""
        self._close_clip_picker()
        self.clip_labels=list(clips)

    def _open_clip_picker(self,_event=None):
        """Drop-down list under the Animation button: a search box over a folder tree of clips."""
        if self.clip_button.instate(['disabled']):return 'break'
        if self.clip_popup is not None:
            self._close_clip_picker();return 'break'
        tk,ttk,button=self.tk,self.ttk,self.clip_button
        top=self.clip_popup=tk.Toplevel(button);top.wm_overrideredirect(True)
        frame=ttk.Frame(top,padding=4,relief='solid',borderwidth=1);frame.pack(fill='both',expand=True)
        search=tk.StringVar()
        entry=ttk.Entry(frame,textvariable=search);entry.pack(fill='x',pady=(0,4))
        tree=ttk.Treeview(frame,show='tree',selectmode='browse')
        scroll=ttk.Scrollbar(frame,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');tree.pack(side='left',fill='both',expand=True)

        def fill(*_args):
            tree.delete(*tree.get_children())
            tree.insert('','end',iid='clip:-1',text=REST_POSE)
            terms=search.get().casefold().split()
            if terms:   # flat list of matches while searching
                for i,label in sorted(enumerate(self.clip_labels),key=lambda item:item[1].lower()):
                    if all(t in label.casefold() for t in terms):tree.insert('','end',iid=f'clip:{i}',text=label)
            else:
                def add(parent,nodes):
                    for n,(text,node) in enumerate(nodes):
                        if isinstance(node,int):
                            tree.insert(parent,'end',iid=f'clip:{node}',text=text)
                        else:
                            iid=f'{parent or "group"}/{n}'
                            tree.insert(parent,'end',iid=iid,text=f'{text}  ({count_clips(node)})')
                            add(iid,node)
                add('',group_clips(self.clip_labels))
            current=f'clip:{self.clip_index}'
            if tree.exists(current):
                tree.see(current);tree.selection_set(current);tree.focus(current)

        def choose(item):
            if item.startswith('clip:'):
                index=int(item[5:]);self._close_clip_picker()
                self._choose_clip(index,REST_POSE if index<0 else self.clip_labels[index])
            elif item:
                tree.item(item,open=not tree.item(item,'open'));place()

        def click(event):
            inside=(0<=event.x_root-top.winfo_rootx()<top.winfo_width() and
                    0<=event.y_root-top.winfo_rooty()<top.winfo_height())
            if not inside:   # the grab sends outside clicks here: close the list
                self._close_clip_picker();return 'break'

        pressed=[None]   # row the current press started on

        def press(event):
            pressed[0]=tree.identify_row(event.y)

        def release(event):
            # A click counts only on the row it started on, so a press dragged off a row does nothing.
            row,pressed[0]=pressed[0],None
            if event.widget is tree and row and row==tree.identify_row(event.y) and tree.identify_element(event.x,event.y)!='Treeitem.indicator':
                choose(row)

        def first_match(_event=None):
            leaves=[i for i in tree.get_children() if i.startswith('clip:') and i!='clip:-1']
            if search.get().strip():
                choose((leaves or ['clip:-1'])[0])
            else:
                choose((tree.selection() or ('clip:-1',))[0])

        def into_list(_event=None):
            tree.focus_set()
            item=tree.focus() or next(iter(tree.get_children()),'')
            if item:tree.focus(item);tree.selection_set(item)

        # Below the button, at least 380 px wide, flipped above it at the bottom of the screen.
        # The height follows the rows currently shown (resized on search and folder open/close);
        # a flipped list keeps its bottom edge on the button.
        top.update_idletasks()
        row_height=int(ttk.Style(top).lookup('Treeview','rowheight') or 20)
        width=max(button.winfo_width(),380)
        x=max(0,min(button.winfo_rootx(),top.winfo_screenwidth()-width))
        below=button.winfo_rooty()+button.winfo_height()
        max_height=top.winfo_screenheight()//2
        flip=below+max_height>top.winfo_screenheight()

        def visible_rows(parent=''):
            return sum(1+(visible_rows(i) if tree.item(i,'open') else 0) for i in tree.get_children(parent))

        pinned=[None]   # a flipped list keeps the top edge it opened with

        def place(_event=None):
            if self.clip_popup is not top:return
            top.update_idletasks()
            rows=max(4,min(18,visible_rows()))
            height=min(rows*row_height+entry.winfo_reqheight()+16,max_height)
            if flip:
                # Growing downward from a fixed top edge keeps every row under the pointer when a folder
                # opens; the list may cover the button while it is open.
                if pinned[0] is None:pinned[0]=max(0,button.winfo_rooty()-height)
                y=pinned[0];height=min(height,top.winfo_screenheight()-y)
            else:y=below
            top.geometry(f'{width}x{height}+{x}+{y}')

        def refill(*_args):
            fill();place()

        # <<TreeviewOpen>> fires before the item opens: measure after it.
        tree.bind('<<TreeviewOpen>>',lambda _e:top.after_idle(place))
        tree.bind('<<TreeviewClose>>',lambda _e:top.after_idle(place))
        search.trace_add('write',refill);fill()
        top.bind('<ButtonPress-1>',click,add='+')
        tree.bind('<ButtonPress-1>',press)
        # The second press of a double-click must not toggle or choose: the first click already acted.
        tree.bind('<Double-ButtonPress-1>',lambda _e:'break')
        tree.bind('<ButtonRelease-1>',release)
        tree.bind('<Return>',lambda _e:choose(tree.focus()))
        entry.bind('<Return>',first_match)
        entry.bind('<Down>',into_list)
        top.bind('<Escape>',lambda _e:self._close_clip_picker())
        place()
        top.lift();top.grab_set();entry.focus_set()
        return 'break'

    def _close_clip_picker(self):
        top,self.clip_popup=self.clip_popup,None
        if top is not None:
            try:
                top.grab_release();top.destroy()
            except self.tk.TclError:
                pass

    def export_clip(self):
        """(model_id, clip label) when 'Extract only this clip' is ticked, else None."""
        if not self.clip_only_var.get() or self.clip_index<0 or not self.source_payload:return None
        return self.source_payload['model_id'],self.clip_labels[self.clip_index]

    def _choose_clip(self,index,label):
        self._stop_animation(reset=False)
        self.clip_choice.set(label)
        self.clip_index=index
        if index<0:self.clip_only_var.set(False)
        self.clip_only_check.configure(state='normal' if index>=0 else 'disabled')
        if index<0:
            self.animation=None;self.viewport.clear_pose()
            self.play_button.configure(state='disabled');self.frame_slider.configure(state='disabled')
            self.frame_text.set('Rest pose');return
        self.frame_text.set('Decoding…')
        self.anim_work.submit(self.source_loader.pose,index)

    def _start_animation(self,animation):
        self.animation=animation;self.anim_frame=0
        count=len(animation['frames'])
        self.frame_slider.configure(from_=0,to=max(1,count-1),state='normal')
        self.play_button.configure(state='normal')
        self._show_frame(0)
        self.playing=count>1;self.play_button.configure(text='Pause' if self.playing else 'Play')
        if self.playing:self._schedule_tick()

    def _show_frame(self,frame):
        if not self.animation:return
        count=len(self.animation['frames'])
        self.anim_frame=frame%count
        self.viewport.set_pose(self.animation['frames'][self.anim_frame])
        self.frame_pos.set(self.anim_frame)
        self.frame_text.set(f'{self.anim_frame+1}/{count} @ {self.animation["fps"]:g} fps')

    def _schedule_tick(self):
        delay=max(10,int(round(1000/max(1.0,self.animation['fps']))))
        self.play_job=self.app.root.after(delay,self._tick)

    def _tick(self):
        self.play_job=None
        if not (self.playing and self.animation) or self.closed:return
        self._show_frame(self.anim_frame+1)
        self._schedule_tick()

    def _toggle_play(self):
        if not self.animation:return
        self.playing=not self.playing
        self.play_button.configure(text='Pause' if self.playing else 'Play')
        if self.playing and self.play_job is None:self._schedule_tick()

    def _scrub(self,_value):
        if not self.animation:return
        frame=int(round(self.frame_pos.get()))
        if frame!=self.anim_frame:
            self.playing=False;self.play_button.configure(text='Play')
            self._show_frame(frame)

    def _stop_animation(self,reset):
        self.playing=False
        if self.play_job is not None:
            self.app.root.after_cancel(self.play_job);self.play_job=None
        self.anim_work.submit(lambda:None)   # drop a pending decode for the old model
        if reset:
            self.animation=None
            self._fill_clip_menu([]);self.clip_button.configure(state='disabled');self.clip_choice.set(REST_POSE)
            self.clip_index=-1;self.clip_only_var.set(False);self.clip_only_check.configure(state='disabled')
            self.play_button.configure(state='disabled',text='Pause');self.frame_slider.configure(state='disabled')
            self.frame_text.set('')

    def _reset(self,message):
        self.generation+=1
        self.detail_work.submit(lambda:None)
        self.artifact_work.submit(lambda:None)
        self.active_ref,self.details,self.review_report=None,None,None
        self.inspect_button.configure(state='disabled')
        for box,var,value in [(self.clip_box,self.clip_var,'Saved pose'),(self.variant_box,self.variant_var,'Saved visibility'),(self.lod_box,self.lod_var,'Saved LOD')]:
            box.configure(values=(),state='disabled');var.set(value)
        self.frame_box.configure(state='disabled')
        self.status_var.set(message)
        self.build_text=message
        self._render_details()

    def _request_placements(self,path):
        from rc2016.app.links import placement_lines
        self.placement_text='Placed in levels: reading asset links…'
        def show(links):
            if not self.source_payload or self.source_payload['source_path']!=path:return
            self.placement_text=('\n'.join(placement_lines(links.model_placements(path))) if links is not None
                                 else 'Placed in levels: '+self.links.message)
            self._render_details()
        self.links.request(show,key='placements')

    def _choose_result(self,index):
        if index<0 or index>=len(self.refs):return
        self._reset('Checking selected result receipts and files…')
        self.active_ref=self.refs[index]
        ref=self.active_ref
        self._load_source_identity(ref.model_id,ref.source_path,self.app.source_var.get().strip() or self._source_root(ref))
        self.detail_work.submit(lambda ref:catalog.inspect_result(ref,verify_blend=True),self.active_ref)

    def _describe(self,message):
        self.description.configure(state='normal');self.description.delete('1.0','end')
        self.description.insert('1.0',message);self.description.configure(state='disabled')

    def _native_translation_changed(self):
        self.viewport.set_native_translation(self.native_translation_var.get())
        if self.source_payload:
            self.source_text = source_details(self.source_payload, getattr(self.selected_record,'used_by','') or '',
                                              self.viewport.native_translation)
            self._render_details()

    def _experimental_materials_changed(self):
        self.viewport.set_experimental_materials(
            self.experimental_materials_var.get() and self.material_previews_var.get())

    def _render_details(self):
        # Source facts from the game files first; a saved build in the output root, when present, below.
        parts=[self.source_text or 'Loading the model from the game files…']
        if self.source_text and self.placement_text:parts.append(self.placement_text)
        if self.build_text:parts.append('Build in the output root\n'+self.build_text)
        self._describe('\n\n'.join(parts))

    def _show_details(self,details):
        self.details=normalize_details(details)
        d=self.details
        self.status_var.set(f"{d.get('kind','unknown').title()} · {d.get('support_status','unknown').title()}")
        self.build_text='\n'.join([*d.get('summary',[]),*d.get('limitations',[])])
        self._render_details()
        okay=d.get('integrity_verified') and bool(d.get('blend_path'))
        self.inspect_button.configure(state='normal' if okay and self.review is None else 'disabled')

    def _open_blend(self):
        if self.details and self.details.get('blend_path'):
            blender=self.app.blender_var.get().strip()
            if not blender or not Path(blender).is_file():
                self.status_var.set('Choose Blender to open this optional saved scene. The 3D model tab works without it.')
                return
            self._open(self.details['blend_path'],blender=blender)

    def _open_folder(self):
        if self.details and self.details.get('blend_path'):self._open(Path(self.details['blend_path']).parent)
        elif self.active_ref:self._open(self.active_ref.root_path)

    def _open(self,path,blender=None):
        try:open_local(path,blender)
        except Exception as error:self.app.messagebox.showerror('Open artifact',str(error))

    def _clip_changed(self):
        index=self.clip_box.current()-1
        rows=(self.review_report or {}).get('clips',[])
        if index<0:
            self.frame_box.configure(state='disabled');self.timing_var.set('Saved pose and frame are retained.');return
        row=rows[index]
        self.frame_box.configure(from_=row['frame_start'],to=row['frame_end'],state='normal')
        self.frame_var.set(str(row['frame_start']))
        self.timing_var.set(f"Frames {row['frame_start']}–{row['frame_end']} at {row['fps']:g} source fps · {row.get('kind') or 'source clip'}")

    def start_review(self,mode):
        if self.review or self.app.runner or (self.app.worker and self.app.worker.is_alive()):
            self.status_var.set('Wait for the active scan, batch or review to finish.');return
        if not self.details or not self.details.get('integrity_verified'):return
        blender=Path(self.app.blender_var.get())
        if not blender.is_file():self.status_var.set('Choose a valid Blender executable.');return
        request={'schema':'rcps4-asset-review-request-v1','mode':mode,
                 'master_blend':str(self.details['blend_path']),'master_sha256':self.details['blend_sha256']}
        run_dir=ROOT/'.cache'/'reviews'/uuid.uuid4().hex
        run_dir.mkdir(parents=True)
        request.update(output_dir=str(run_dir),report_path=str(run_dir/'report.json'))
        request_path=run_dir/'request.json';request_path.write_text(json.dumps(request,indent=2),encoding='utf8')
        token=self.generation
        self.review=ReviewProcess(review_command(blender,ROOT/'rc2016'/'pipeline'/'review.py',request_path),run_dir/'report.json',ROOT,event=lambda kind,**v:self.review_events.put((kind,v)))
        self.status_var.set('Reading saved scene…')
        self.inspect_button.configure(state='disabled')
        self.app.start_button.configure(state='disabled')
        def work():
            try:
                report=self.review.run()
                self.review_events.put(('review_done',{'report':report,'token':token,'mode':mode}))
            except Exception as error:self.review_events.put(('review_error',{'message':str(error),'token':token}))
        self.review_worker=threading.Thread(target=work,daemon=True);self.review_worker.start()

    def cancel_review(self):
        if self.review:
            self.status_var.set('Stopping the owned review process…')
            threading.Thread(target=self.review.cancel,daemon=True).start()

    def _show_controls(self,report):
        self.review_report=report
        for key,box,var,first in [('clips',self.clip_box,self.clip_var,'Saved pose'),('variants',self.variant_box,self.variant_var,'Saved visibility'),('lods',self.lod_box,self.lod_var,'Saved LOD')]:
            rows=report.get(key,[])
            values=[first]+[r.get('selector') or r.get('label') for r in rows]
            box.configure(values=values,state='readonly' if rows else 'disabled');box.current(0)
        self._clip_changed()
        self.selector_note.set('\n'.join(filter(None,[report.get('variants_unavailable_reason'),report.get('lods_unavailable_reason')])))

    def _drain(self):
        if self.closed:return
        for worker,kind in [(self.meta_work,'metadata'),(self.detail_work,'details'),(self.artifact_work,'artifact'),(self.source_work,'source'),(self.anim_work,'animation'),(self.clip_list_work,'clip_list')]:
            try:
                while True:
                    generation,payload,error=worker.events.get_nowait()
                    if generation!=worker.generation:continue
                    if error:
                        if kind=='animation':
                            self.frame_text.set('Cannot play');self.model_info.set(error);continue
                        if kind=='clip_list':
                            self.frame_text.set('Animations unavailable');self.status_var.set(error);continue
                        self.status_var.set(error)
                        if kind=='source':
                            self.viewport.clear(error);self.model_info.set(error)
                            self._clear_texture_list(error)
                            path,ident=(self.source_selection or ('','',''))[2],(self.source_selection or ('','',''))[1]
                            self.source_text=f'{path}\nAsset ID: {ident}\nThis model could not be loaded from the game files:\n{error}'
                            self._render_details()
                        continue
                    if payload is None:continue
                    if kind=='metadata':
                        self.refs,self.metadata=payload
                        if self.source_payload is not None:
                            identity=(self.source_payload['model_id'],self.source_payload['source_path'])
                            self.metadata.setdefault(identity,{})['kind']=self.source_payload['kind']
                        self.status_var.set(f'{len(self.refs)} saved results in the output root.')
                        self.app.refresh_table()
                    elif kind=='details':self._show_details(payload)
                    elif kind=='source':
                        self.source_payload=payload
                        self.viewport.set_mesh(payload)
                        self.model_info.set(f"{len(payload['vertices']):,} vertices · {len(payload['triangles']):,} triangles · {payload['kind']}\nSource ID: {payload['model_id']}")
                        self.native_look_box.configure(values=[str(r['index']) for r in payload['looks']],state='readonly' if payload['looks'] else 'disabled')
                        self.native_lod_box.configure(state='readonly' if payload['looks'] else 'disabled')
                        identity=(payload['model_id'],payload['source_path'])
                        self.metadata.setdefault(identity,{})['kind']=payload['kind']
                        self._set_clips(payload)
                        if payload.get('animation_pending') and self.source_selection:
                            self.clip_list_work.submit(self.source_loader.load_animation,self.source_selection[0],
                                                       payload['model_id'],payload['source_path'],self.source_output_root)
                        self._set_texture_list(payload)
                        self.source_text=source_details(payload,getattr(self.selected_record,'used_by','') or '',self.viewport.native_translation)
                        self._request_placements(payload['source_path'])
                        self._render_details()
                        if self.viewport.shading_error:self.model_info.set(self.model_info.get()+'\n'+self.viewport.shading_error)
                        if getattr(self.viewport,'shading_warning',''):self.model_info.set(self.model_info.get()+'\n'+self.viewport.shading_warning)
                    elif kind=='animation':self._start_animation(payload)
                    elif kind=='clip_list':self._apply_clip_list(payload)
                    elif kind=='artifact':self._open(payload)
            except queue.Empty:pass
        self._drain_textures()
        self.links.drain()
        self.flash_tab.drain()
        try:
            while True:
                kind,values=self.review_events.get_nowait()
                if kind=='log':self.app._append_log(str(values['message']))
                else:
                    self.review=None
                    self.app._set_busy()
                    if self.details and self.details.get('integrity_verified'):
                        self.inspect_button.configure(state='normal')
                    if values.get('token')!=self.generation:continue
                    self.inspect_button.configure(state='normal')
                    if kind=='review_error':self.status_var.set(values['message']);self.app._append_log(values['message'],'error')
                    else:
                        report=values['report']
                        if report.get('status')=='cancelled':self.status_var.set('Review cancelled. Master was not saved.');continue
                        self._show_controls(report);self.status_var.set('Saved scene controls loaded. Choose a source frame or keep the saved pose.')
        except queue.Empty:pass
        self.app.root.after(100,self._drain)

    def close(self):
        self.closed=True
        self.playing=False
        if self.play_job is not None:self.app.root.after_cancel(self.play_job);self.play_job=None
        self.meta_work.close();self.detail_work.close();self.artifact_work.close();self.source_work.close();self.anim_work.close();self.clip_list_work.close()
        self.texture_work.close();self.texture_list_work.close();self.flash_tab.close();self.level_panel.close()
        self.gallery.close()
        self.links.close()
        self.cancel_review()
