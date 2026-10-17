"""Tk asset/result browser added to the existing extractor; workers never touch Tk."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
import queue
import threading
import uuid
from rc2016.app import catalog
from rc2016.common import remember_var
from rc2016.app.layout import SplitLayout, Splitter, regrid
from rc2016.viewer.loader import SourcePreviewLoader
from rc2016.viewer.viewport import ModelViewport
from rc2016.app.texture_tab import TextureTab
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
        entry = (payload.get('textures') or {}).get(m.get('index', i), {})
        forward = entry.get('forward') or {}
        sampling = forward.get('sampling') or {}
        liquid = entry.get('lava_surface') or entry.get('liquid_flow') or sampling
        notes = list(liquid.get('limitations') or ())
        if sampling:
            notes.insert(0, sampling['time_substitution'])
            notes.append('Water normal combination, body colour, opacity, F0 and gloss use inferred presentation values.')
        elif forward.get('kind') == 'water':
            notes.append('Water family fallback: frozen-time mapping, normal combination and shading are inferred presentation choices.')
        for note in dict.fromkeys(notes):
            lines.append('        Preview limitation: ' + note)
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


def lod_choices_differ(looks, mesh_count, look=0):
    """Whether Highest, Lowest and All select different meshes for ``look`` (else the LOD choice does nothing)."""
    from rc2016.formats.native_model import _selected_meshes
    try:
        return len({tuple(_selected_meshes(looks, mesh_count, mode, look)) for mode in ('Highest', 'Lowest', 'All')}) > 1
    except ValueError:
        return False


def material_toggles(textures):
    """(native translation applies, experimental preview applies) for a payload's per-slot material entries."""
    entries = list((textures or {}).values())
    return (any(e.get('native_translation') for e in entries), any(e.get('experimental_preview') for e in entries))


def lighting_controls(ttk, parent, viewport, lighting_var, tonemap_var, note_parent=None):
    """Lighting source selector; returns its combobox. The note about a pending HDRI goes in ``note_parent``."""
    def lighting_changed(_event=None):
        if lighting_var.get() == 'Shared HDRI':
            tonemap_var.set('Khronos PBR Neutral')
        viewport.set_lighting(lighting_var.get(), tonemap_var.get())
    box = ttk.Combobox(parent, textvariable=lighting_var, values=viewport.LIGHTINGS, state='readonly', width=12)
    box.pack(side='left', padx=(4, 0)); box.bind('<<ComboboxSelected>>', lighting_changed)
    note = ttk.Label(note_parent or parent, text='', foreground='#6b7280')
    note.pack(side='left', padx=4)
    viewport.on_lighting_changed = lambda: note.configure(text=viewport.lighting_note or '')
    return box


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
    ASSET_TYPES=('Model','Level','Flash UI','Texture')

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
        self.layout = getattr(app, 'split_layout', None) or SplitLayout()
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
        from rc2016.app.gallery import Gallery   # thumbnail grid of the selected or searched models: the list's Grid view
        self.gallery=Gallery(self,app.list_host)
        self.model_textures=TextureTab(self,textures,archive=False)
        self._build_type_views(type_host if type_host is not None else parent.master,model_view if model_view is not None else parent)
        # The viewport is the big pane and grows with the window; the controls sit beside it (Vertical layout)
        # or below it in two columns (Horizontal, see _arrange_view_controls). Drag the sash to resize either.
        view.columnconfigure(0,weight=1);view.rowconfigure(0,weight=1)
        self.view_split=split=Splitter(view,side=430,stacked=180);split.grid(row=0,column=0,sticky='nsew')
        self.layout.register(split)
        holder=split.first;holder.columnconfigure(0,weight=1);holder.rowconfigure(0,weight=1)
        side=split.second;side.configure(padding=(8,0,0,0));side.columnconfigure(0,weight=1);side.rowconfigure(8,weight=1)
        self.viewport=ModelViewport(holder,width=380,height=560)
        self.viewport.grid(row=0,column=0,sticky='nsew')
        tip=app._tip
        # View: camera and draw mode.
        viewrow=ttk.Frame(side);viewrow.grid(row=0,column=0,sticky='ew',pady=(0,4))
        ttk.Label(viewrow,text='View').pack(side='left')
        self.view_mode=self.tk.StringVar(value='Shaded')
        self.mode_box=mode_box=ttk.Combobox(viewrow,textvariable=self.view_mode,values=self.viewport.MODES,state='readonly',width=16)
        mode_box.pack(side='left',padx=4)
        mode_box.bind('<<ComboboxSelected>>',self._mode_changed)
        reset_button=ttk.Button(viewrow,text='Reset view',style='Compact.TButton',command=self.viewport._reset_camera)
        reset_button.pack(side='left',padx=4)
        tip('Shaded: the game BRDF (_c base colour, _g gloss, _n normals, self-lit) under the chosen lighting. It predicts the Blender review render, not game lighting.\nTextured: base colour only. Solid and Wireframe: geometry only.',mode_box)
        tip('Frame the whole model again.\nDrag orbits, right-drag pans, the wheel zooms.',reset_button)
        # Source: which meshes of the file are drawn. Shown only when the model has a choice (see _update_source_controls).
        self.source_row=lodrow=ttk.Frame(side);lodrow.grid(row=1,column=0,sticky='ew',pady=(0,4))
        self.native_look=self.tk.StringVar(value='0')
        self.native_look_label=ttk.Label(lodrow,text='Look')
        self.native_look_box=ttk.Combobox(lodrow,textvariable=self.native_look,values=(),state='disabled',width=4)
        self.native_look_box.bind('<<ComboboxSelected>>',lambda _e:self._reload_source())
        self.native_lod=self.tk.StringVar(value='Highest')
        self.native_lod_label=ttk.Label(lodrow,text='LOD')
        self.native_lod_label.pack(side='left')
        self.native_lod_box=ttk.Combobox(lodrow,textvariable=self.native_lod,values=('Highest','Lowest','All'),state='disabled',width=9)
        self.native_lod_box.pack(side='left',padx=4)
        self.native_lod_box.bind('<<ComboboxSelected>>',lambda _e:self._reload_source())
        tip("Detail level of the meshes shown. Highest and Lowest use the model's own LOD membership; All also draws meshes no look references (a diagnostic).\nOnly shown for models whose LODs differ.",self.native_lod_box)
        tip('Source look: a model can hold several alternative mesh sets. Only shown when it does.',self.native_look_label,self.native_look_box)
        lodrow.grid_remove()
        # Shading: lighting, then the tonemap that turns its output into screen colours.
        self.lighting_var=remember_var(self.tk.StringVar(value=self.viewport.LIGHTINGS[0]),'model.lighting',self.viewport.LIGHTINGS)
        self.lighting_enabled_var=remember_var(self.tk.BooleanVar(value=True),'model.lighting_enabled')
        self.lighting_group=lighting_group=ttk.LabelFrame(side,text='Shading',padding=(4,2))
        lighting_group.grid(row=2,column=0,sticky='ew',pady=(0,4))
        self.lighting_enabled_check=ttk.Checkbutton(
            lighting_group,text='Lighting',variable=self.lighting_enabled_var,
            command=lambda:self.viewport.set_lighting_enabled(self.lighting_enabled_var.get()))
        self.lighting_enabled_check.grid(row=0,column=0,sticky='w')
        self.lighting_controls=ttk.Frame(lighting_group)
        self.lighting_controls.grid(row=0,column=1,sticky='w')
        self.tonemap_var=remember_var(self.tk.StringVar(value=self.viewport.TONEMAPS[0]),'model.tonemap',self.viewport.TONEMAPS)
        lighting_note_row=ttk.Frame(lighting_group);lighting_note_row.grid(row=1,column=0,columnspan=4,sticky='w')
        lighting_box=lighting_controls(ttk,self.lighting_controls,self.viewport,self.lighting_var,self.tonemap_var,note_parent=lighting_note_row)
        tonemap_label=ttk.Label(lighting_group,text='Tonemap')
        tonemap_label.grid(row=0,column=2,sticky='w',padx=(12,0))
        tonemap_box=ttk.Combobox(lighting_group,textvariable=self.tonemap_var,values=self.viewport.TONEMAPS,state='readonly',width=19)
        tonemap_box.grid(row=0,column=3,sticky='w',padx=4)
        tonemap_box.bind('<<ComboboxSelected>>',lambda _e:self.viewport.set_lighting(tonemap=self.tonemap_var.get()))
        tip('Master switch for lighting. Off shows the unlit surface colour; self-lit emission stays.',self.lighting_enabled_check)
        tip('Viewer rig: fixed key, fill and sky light that turns with the camera (what the gallery thumbnails use).\nShared HDRI: the shared review HDRI, as the Blender review render uses. Choosing it also selects Khronos PBR Neutral.',lighting_box)
        tip('Maps the lit HDR result to screen colours.\nACES fit: filmic, desaturates highlights (gallery thumbnails).\nKhronos PBR Neutral: keeps base colours accurate until highlights.',tonemap_label,tonemap_box)
        # Materials: only the toggles that change something for this model (see _update_source_controls).
        self.native_translation_var=remember_var(self.tk.BooleanVar(value=True),'model.native_translation')
        self.experimental_materials_var=remember_var(self.tk.BooleanVar(value=False),'model.experimental_materials')
        self.materials_group=materials=ttk.LabelFrame(side,text='Materials',padding=(4,2))
        materials.grid(row=3,column=0,sticky='ew',pady=(0,4))
        self.native_check=ttk.Checkbutton(materials,text='Native translation',variable=self.native_translation_var,
                                          command=self._native_translation_changed)
        self.experimental_check=ttk.Checkbutton(materials,text='Experimental previews',variable=self.experimental_materials_var,
                                                command=self._experimental_materials_changed)
        tip('Shade materials from their translated game shader program instead of the generic reading. Replaces the fallback rules only; exact hand-decoded rules stay.\nShown when a material of this model has a translation; Shaded mode only.',self.native_check)
        tip('Best-effort material previews: pinned Novalis mountain and terrain materials, plus a generic adapter that substitutes viewer geometry for inputs that are not bound (matte lighting; paint and UVs inferred, runtime unverified).\nReplaces the native translation where one exists. Not game-equivalent.\nShown when a material of this model has such a preview; Shaded mode only.',self.experimental_check)
        materials.grid_remove()
        self.native_applicable=self.experimental_applicable=False
        self.viewport.set_lighting_enabled(self.lighting_enabled_var.get())
        self.viewport.set_native_translation(self.native_translation_var.get())
        self.viewport.set_experimental_materials(False)
        self.anim_box=anim=ttk.Frame(side);anim.grid(row=4,column=0,sticky='ew',pady=(0,6))
        anim.columnconfigure(1,weight=1)
        # Row 0: the clip picker. Row 1: play/pause and the scrubber, which takes the whole width. Row 2: export option and frame readout.
        ttk.Label(anim,text='Animation').grid(row=0,column=0,sticky='w',padx=(0,6))
        self.clip_choice=tk.StringVar(value=REST_POSE)
        # A themed button that opens a searchable clip list (_open_clip_picker), not a Tk menu.
        self.clip_button=ttk.Menubutton(anim,textvariable=self.clip_choice,state='disabled',width=22)
        self.clip_button.bind('<ButtonPress-1>',self._open_clip_picker)
        self.clip_button.bind('<KeyPress-space>',self._open_clip_picker)
        self.clip_button.grid(row=0,column=1,columnspan=2,sticky='ew')
        self.clip_labels=[];self.clip_index=-1;self.clip_popup=None
        self.play_button=ttk.Button(anim,text='Pause',width=7,command=self._toggle_play,state='disabled')
        self.play_button.grid(row=1,column=0,sticky='ew',padx=(0,6),pady=(4,0))
        self.frame_pos=tk.DoubleVar(value=0)
        self.frame_slider=ttk.Scale(anim,from_=0,to=1,variable=self.frame_pos,command=self._scrub,state='disabled')
        self.frame_slider.grid(row=1,column=1,columnspan=2,sticky='ew',pady=(4,0))
        self.clip_only_var=tk.BooleanVar(value=False)
        self.clip_only_check=ttk.Checkbutton(anim,text='Export only this clip',variable=self.clip_only_var,state='disabled')
        self.clip_only_check.grid(row=2,column=0,columnspan=2,sticky='w',pady=(2,0))
        tip('Export option: when ticked, Extract selected exports just this clip for the model shown here instead of all its clips.\nPick a clip first.',self.clip_only_check)
        self._fill_clip_menu([])
        self.frame_text=tk.StringVar(value='')
        ttk.Label(anim,textvariable=self.frame_text,width=18,anchor='e').grid(row=2,column=2,sticky='e',pady=(2,0))   # fits '1234/1234 @ 29.97 fps'
        tip('Play or pause the clip.',self.play_button)
        tip('Drag to scrub through the clip frame by frame.',self.frame_slider)
        tip('Pick a clip to play on the rest pose. Skinned models only.',self.clip_button)
        self.anim_work=LatestWork()
        self.animation=None;self.anim_frame=0;self.playing=False;self.play_job=None
        # Load errors and shader warnings only; the model's counts and ID are in Details.
        self.model_info=self.tk.StringVar(value='Select one source model. It loads directly; no Blender is needed.')
        self.info_label=ttk.Label(side,textvariable=self.model_info,wraplength=400)
        self.info_label.grid(row=5,column=0,sticky='ew')
        self.model_info.trace_add('write',lambda *_a:self.info_label.grid() if self.model_info.get() else self.info_label.grid_remove())
        self.view_hint=ttk.Label(side,text='Drag: orbit · Right drag: pan · Wheel: zoom',foreground='#6b7280');self.view_hint.grid(row=6,column=0,sticky='w',pady=5)
        self.layout.listen(self._arrange_view_controls)
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

    def _arrange_view_controls(self,stacked):
        """Vertical layout: one column of controls. Horizontal (stacked) layout: two columns, so the viewport keeps the height."""
        side=self.view_split.second
        side.configure(padding=(0,6,0,0) if stacked else (8,0,0,0))
        side.columnconfigure(1,weight=1 if stacked else 0,uniform='controls' if stacked else '')
        side.columnconfigure(0,uniform='controls' if stacked else '')
        # Stacked: the animation controls sit beside the view/shading column, spanning its first rows so no row stretches.
        for widget,vrow,srow,sticky in ((self.mode_box.master,0,0,'new'),(self.source_row,1,1,'new'),(self.lighting_group,2,2,'new'),(self.view_hint,6,3,'nw')):
            regrid(widget,row=srow if stacked else vrow,column=0,rowspan=1,sticky=sticky,padx=(0,12) if stacked else 0)
        for widget,vrow,srow,span in ((self.anim_box,4,0,3),(self.materials_group,3,3,1),(self.info_label,5,4,1)):
            regrid(widget,row=srow if stacked else vrow,column=1 if stacked else 0,rowspan=span if stacked else 1,sticky='new',padx=0)

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
                                    on_open_model=self.open_model,layout=self.layout,remember=True)
        self.level_panel.frame.grid(row=0,column=0,sticky='nsew')
        # Flash UI (flash_tab.py): the .gfx/.swf list on the left; reads the list on first show.
        from rc2016.flash.tab import FlashTab
        self.flash_view=ttk.Panedwindow(host,orient='horizontal')
        flash_list=ttk.LabelFrame(self.flash_view,text='Flash UI files',padding=6)
        flash_right=ttk.LabelFrame(self.flash_view,text='Flash UI viewer',padding=6)
        self.flash_view.add(flash_list,weight=1);self.flash_view.add(flash_right,weight=4)
        self.flash_tab=FlashTab(app,flash_right,self.source_loader,list_parent=flash_list,layout=self.layout)
        # Texture (whole archive): its own viewer, with its own list, filter and preview.
        self.texture_view_host=ttk.Frame(host)
        self.archive_textures=TextureTab(self,self.texture_view_host,archive=True)
        self.type_views={'Model':model_view,'Level':self.level_view,'Flash UI':self.flash_view,'Texture':self.texture_view_host}

    def show_asset_type(self,kind):
        """Show one asset type's list and viewer; the others are hidden, not destroyed."""
        if kind not in self.type_views:raise ValueError(f'Unknown asset type: {kind}')
        for name,widget in self.type_views.items():
            if name!=kind:widget.grid_remove()
        self.type_views[kind].grid(**self._type_grid)
        self.asset_type=kind
        tools=getattr(self.app,'model_tools',None)
        if tools is not None:
            tools.grid() if kind=='Model' else tools.grid_remove()
        first=kind not in self._types_shown
        self._types_shown.add(kind)
        if kind=='Level' and not self.level_panel.levels:
            self.level_panel.list_levels()
        elif kind=='Flash UI' and not self.flash_tab.files:
            self.flash_tab.load_files()
        elif kind=='Texture' and first:
            self.archive_textures.activate()
        if first and kind in ('Level','Flash UI'):
            pane=self.type_views[kind]
            def place_sash():
                width=pane.winfo_width()
                if width>1:pane.sashpos(0,max(220,int(width*0.24)))
            self.app.root.after(50,place_sash)

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
            self.model_textures._clear_texture_list('Loose-file transform mode has no archive textures to list.')
            self.viewport.clear('Loose-file transform mode has no archive geometry identity. Use the full archive browser to preview native models.')

    def _load_source_identity(self,model_id,source_path,source_root):
        selection=(str(source_root),model_id,source_path)
        if selection==self.source_selection and self.source_payload is not None:return
        self.source_selection=selection
        self.source_payload=None
        self.source_text=''
        self.model_textures._clear_texture_list('Loading the selected model\'s textures…')
        self.native_look.set('0');self.native_lod.set('Highest')
        self.viewport.clear('Loading selected source geometry…')
        self.native_look_box.configure(state='disabled');self.native_lod_box.configure(state='disabled')
        self.source_row.grid_remove();self.materials_group.grid_remove()   # re-shown for the model that loads
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
        """(model_id, clip label) when 'Export only this clip' is ticked, else None."""
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
        self.viewport.set_experimental_materials(self.experimental_materials_var.get())

    def _mode_changed(self,_event=None):
        self.viewport.set_mode(self.view_mode.get())
        self._sync_material_state()

    def _sync_material_state(self):
        """Material toggles only change Shaded mode, so they are inert in the other modes."""
        state='normal' if self.view_mode.get()=='Shaded' else 'disabled'
        self.native_check.configure(state=state);self.experimental_check.configure(state=state)

    def _update_source_controls(self,payload):
        """Show the LOD, look and material toggles only where they change something for this model."""
        looks=payload.get('looks') or []
        look=int(self.native_look.get() or 0)
        multi_look=len(looks)>1
        lod=lod_choices_differ(looks,payload.get('mesh_record_count',0),look) if looks else False
        self.native_look_box.configure(values=[str(r['index']) for r in looks],state='readonly' if multi_look else 'disabled')
        self.native_lod_box.configure(state='readonly' if lod else 'disabled')
        for widget in (self.native_look_label,self.native_look_box):
            if multi_look:widget.pack(side='left',padx=(0,4),before=self.native_lod_label)
            else:widget.pack_forget()
        self.source_row.grid() if (lod or multi_look) else self.source_row.grid_remove()
        self.native_applicable,self.experimental_applicable=material_toggles(payload.get('textures'))
        self.native_check.grid_remove();self.experimental_check.grid_remove()
        if self.native_applicable:self.native_check.grid(row=0,column=0,sticky='w')
        if self.experimental_applicable:self.experimental_check.grid(row=0,column=1,sticky='w',padx=(8,0))
        applicable=self.native_applicable or self.experimental_applicable
        self.materials_group.grid() if applicable else self.materials_group.grid_remove()
        # A toggle that is hidden must not keep changing the picture.
        self.viewport.set_experimental_materials(self.experimental_applicable and self.experimental_materials_var.get())
        self._sync_material_state()

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
                            self.model_textures._clear_texture_list(error)
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
                        self.model_info.set('')
                        self._update_source_controls(payload)
                        identity=(payload['model_id'],payload['source_path'])
                        self.metadata.setdefault(identity,{})['kind']=payload['kind']
                        self._set_clips(payload)
                        if payload.get('animation_pending') and self.source_selection:
                            self.clip_list_work.submit(self.source_loader.load_animation,self.source_selection[0],
                                                       payload['model_id'],payload['source_path'],self.source_output_root)
                        self.model_textures._set_texture_list(payload)
                        self.source_text=source_details(payload,getattr(self.selected_record,'used_by','') or '',self.viewport.native_translation)
                        self._request_placements(payload['source_path'])
                        self._render_details()
                        self.model_info.set('\n'.join(t for t in (self.viewport.shading_error,getattr(self.viewport,'shading_warning','')) if t))
                    elif kind=='animation':self._start_animation(payload)
                    elif kind=='clip_list':self._apply_clip_list(payload)
                    elif kind=='artifact':self._open(payload)
            except queue.Empty:pass
        self.model_textures.drain();self.archive_textures.drain()
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
        self.model_textures.close();self.archive_textures.close();self.flash_tab.close();self.level_panel.close()
        self.gallery.close()
        self.links.close()
        self.cancel_review()
