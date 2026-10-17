"""One texture viewer: the preview on the left, the texture list with its export controls on the right.

The Model asset type's Textures tab (archive=False) lists the textures of the selected model. The Texture
asset type (archive=True) lists every texture in the archive, with the Maps menu and Filter. Each viewer keeps
its own list, filter, preview and export state, so the two never share anything."""
from __future__ import annotations

import base64
import io
import queue
from pathlib import Path

from PIL import Image

from rc2016.app.layout import Splitter
from rc2016.app.texture_grid import TextureGrid
from rc2016.app.workers import LatestWork


class TextureTab:
    TEXTURE_ROW_LIMIT=3000   # Treeview stays responsive; the filter narrows the rest
    ZOOM_STEPS=('Fit','50%','100%','200%','400%','800%')
    LIST_VIEW,GRID_VIEW='List','Grid'
    ALL_CHANNELS='All four'      # Channels choice: R, G, B and A side by side (Fit only, no pixel readout)
    DEFAULT_HIDDEN_MAPS=('_g','_n')   # The archive viewer hides these suffixes until the Maps menu unticks them

    def __init__(self,browser,host,archive):
        """Build one viewer into host. archive=True lists the whole archive; False lists the selected model's textures."""
        self.browser=browser;self.archive=archive
        self.app,self.tk,self.ttk,self.layout=browser.app,browser.tk,browser.ttk,browser.layout
        tk=self.tk
        self.texture_work=LatestWork();self.texture_list_work=LatestWork()
        self.texture_events=queue.Queue()
        self.model_texture_rows=[];self.archive_texture_rows=None;self.archive_texture_root=None
        self.texture_matches=[];self.texture_rows=[];self.texture_model=None
        self.texture_image=None;self.texture_facts=None;self.texture_photo=None
        self.texture_exporting=False;self.texture_filter_job=None
        self.texture_zoom=tk.StringVar(value='Fit');self.texture_centre=[0.5,0.5];self.texture_drag=None
        self.texture_layout=None;self.texture_view_path=None;self.texture_pixel=tk.StringVar(value='')
        self.texture_view=tk.StringVar(value=self.LIST_VIEW);self.hidden_maps=set(self.DEFAULT_HIDDEN_MAPS);self.map_vars={}
        self.texture_preview_buttons=[];self.texture_maps_button=None
        self._build(host)

    def _build(self,tab):
        """Preview (left) and the texture list with its export controls (right), like the 3D tab's layout."""
        tk,ttk,tip=self.tk,self.ttk,self.app._tip
        tab.columnconfigure(0,weight=1);tab.rowconfigure(0,weight=1)
        split=self.texture_split=Splitter(tab,side=520,stacked=330);split.grid(row=0,column=0,sticky='nsew')
        self.layout.register(split)
        left=split.first;left.columnconfigure(0,weight=1);left.rowconfigure(0,weight=1)
        right=split.second;right.configure(padding=(8,0,0,0));right.columnconfigure(0,weight=1);right.rowconfigure(1,weight=1)
        # Left: the image, how to read it, and what it is.
        self.texture_canvas=tk.Canvas(left,width=380,height=300,background='#2b2b2b',highlightthickness=0)
        self.texture_canvas.grid(row=0,column=0,sticky='nsew')
        self.texture_canvas.bind('<Configure>',lambda _e:self._draw_texture())
        self.texture_canvas.bind('<ButtonPress-1>',self._texture_press)
        self.texture_canvas.bind('<B1-Motion>',self._texture_drag)
        self.texture_canvas.bind('<Double-Button-1>',lambda _e:self._texture_toggle_fit())
        self.texture_canvas.bind('<Motion>',self._texture_hover)
        self.texture_canvas.bind('<Leave>',lambda _e:self.texture_pixel.set(''))
        # The wheel goes to the widget under the pointer (Windows sends it to the focused one), so listen application-wide.
        self.app.root.bind_all('<MouseWheel>',self._texture_wheel,add='+')
        options=ttk.Frame(left);options.grid(row=1,column=0,sticky='ew',pady=(4,0))
        channels_label=ttk.Label(options,text='Channels');channels_label.pack(side='left')
        # RGB by default: alpha is usually mask data (e.g. the _c self-lit mask), not transparency.
        self.texture_channels=tk.StringVar(value='RGB')
        box=ttk.Combobox(options,textvariable=self.texture_channels,values=('RGBA','RGB','R','G','B','Alpha',self.ALL_CHANNELS),state='readonly',width=9)
        box.pack(side='left',padx=4);box.bind('<<ComboboxSelected>>',lambda _e:self._draw_texture())
        self.texture_flip=tk.BooleanVar(value=False)
        flip=ttk.Checkbutton(options,text='Flip vertically',variable=self.texture_flip,command=self._draw_texture);flip.pack(side='left',padx=8)
        zoom_label=ttk.Label(options,text='Zoom');zoom_label.pack(side='left',padx=(4,0))
        zoom=ttk.Combobox(options,textvariable=self.texture_zoom,values=self.ZOOM_STEPS,state='readonly',width=6)
        zoom.pack(side='left',padx=4);zoom.bind('<<ComboboxSelected>>',lambda _e:self._texture_zoom_changed())
        tip('Fit: the whole texture in the frame. 50% to 800%: that many screen pixels per texture pixel, showing the middle of the texture.\n'
            'Drag to pan, mouse wheel to zoom at the cursor, double-click to switch between Fit and 100%.',zoom_label,zoom)
        # Only multi-slice textures (arrays, cube maps, volumes) have a slice to pick: hidden otherwise.
        self.texture_slice_frame=ttk.Frame(options)
        ttk.Label(self.texture_slice_frame,text='Slice').pack(side='left')
        self.texture_slice=tk.StringVar(value='1')
        self.texture_slice_box=ttk.Spinbox(self.texture_slice_frame,textvariable=self.texture_slice,from_=1,to=1,width=4,state='disabled',
                                           command=self._texture_slice_changed)
        self.texture_slice_box.pack(side='left',padx=4)
        self.texture_slice_box.bind('<Return>',lambda _e:self._texture_slice_changed())
        reset=ttk.Button(options,text='Reset',command=self._texture_reset);reset.pack(side='right')
        tip('Back to the starting view: Fit, RGB, unflipped, first slice. In the Texture type it also clears the Filter, returns to List view and restores the default Maps.',reset)
        self.texture_info=tk.StringVar(value='Select a source model to list its textures.' if not self.archive else 'Reading the archive\'s texture index…')
        ttk.Label(left,textvariable=self.texture_info,wraplength=400).grid(row=2,column=0,sticky='ew',pady=(4,0))
        ttk.Label(left,textvariable=self.texture_pixel,wraplength=400).grid(row=3,column=0,sticky='ew',pady=(2,0))
        actions=ttk.Frame(left);actions.grid(row=4,column=0,sticky='w',pady=(4,0))
        copy_preview=ttk.Button(actions,text='Copy image',command=self._copy_texture_preview,state='disabled')
        copy_preview.pack(side='left')
        self.texture_preview_buttons=[copy_preview]
        tip('Puts the preview on the clipboard, as the Channels and Flip settings show it, to paste into another program. The zoom is not applied.',copy_preview)
        # Right: which textures, then export, then who uses the selected one.
        top=ttk.Frame(right);top.grid(row=0,column=0,sticky='ew',pady=(0,4))
        top.columnconfigure(1,weight=1)
        filter_label=ttk.Label(top,text='Filter');filter_label.grid(row=0,column=0,padx=(0,4))
        self.texture_filter=tk.StringVar()
        filter_entry=ttk.Entry(top,textvariable=self.texture_filter);filter_entry.grid(row=0,column=1,sticky='ew')
        self.texture_filter.trace_add('write',lambda *_a:self._schedule_texture_filter())
        view_label=ttk.Label(top,text='View');view_label.grid(row=1,column=0,sticky='w',padx=(0,4),pady=(4,0))
        view=ttk.Combobox(top,textvariable=self.texture_view,values=(self.LIST_VIEW,self.GRID_VIEW),state='readonly',width=13)
        view.grid(row=1,column=1,sticky='w',pady=(4,0));view.bind('<<ComboboxSelected>>',lambda _e:self._texture_view_changed())
        tip('Words that must all appear in the texture path. A word with a leading - must not appear, e.g. -planet.',filter_label,filter_entry)
        tip('List: one row per texture with its format and size. Grid: a thumbnail per texture, drawn only where it is on screen.\n'
            'Arrow keys move, Shift extends, Ctrl-click toggles, Ctrl+A selects all, Escape clears.',view_label,view)
        if self.archive:
            self.texture_maps_button=ttk.Menubutton(top,text='');self.texture_maps_button.grid(row=1,column=2,sticky='w',padx=(8,0),pady=(4,0))
            self.texture_maps_menu=tk.Menu(self.texture_maps_button,tearoff=False);self.texture_maps_button.configure(menu=self.texture_maps_menu)
            self._update_map_button()
            tip('Textures whose name ends in a ticked suffix are hidden (the part after the last _ in the name). '
                '_g and _n are hidden by default; the count is how many archive textures have that suffix.',self.texture_maps_button)
        listing=ttk.Frame(right);listing.grid(row=1,column=0,sticky='nsew')
        listing.columnconfigure(0,weight=1);listing.rowconfigure(0,weight=1)
        tree_frame=self.texture_tree_frame=ttk.Frame(listing);tree_frame.grid(row=0,column=0,sticky='nsew')
        tree_frame.columnconfigure(0,weight=1);tree_frame.rowconfigure(0,weight=1)
        self.texture_tree=ttk.Treeview(tree_frame,columns=('format','size','where'),height=10,selectmode='extended')
        for column,text,width in [('#0','Texture',190),('format','Format',80),('size','Size',80),('where','Materials / folder',150)]:
            self.texture_tree.heading(column,text=text);self.texture_tree.column(column,width=width,stretch=column in ('#0','where'))
        self.texture_tree.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(tree_frame,orient='vertical',command=self.texture_tree.yview);scroll.grid(row=0,column=1,sticky='ns')
        self.texture_tree.configure(yscrollcommand=scroll.set)
        self.texture_tree.bind('<<TreeviewSelect>>',lambda _e:self._texture_selected())
        grid_frame=self.texture_grid_frame=ttk.Frame(listing);grid_frame.grid(row=0,column=0,sticky='nsew')
        self.texture_grid=TextureGrid(grid_frame,lambda path:self.browser.source_loader.texture_thumbnail(path),self._texture_grid_changed,self.app.root)
        grid_frame.grid_remove()
        export=ttk.LabelFrame(right,text='Export',padding=(4,2));export.grid(row=2,column=0,sticky='ew',pady=(6,0))
        self.export_formats={'texture':tk.BooleanVar(value=False),'dds':tk.BooleanVar(value=False),'png':tk.BooleanVar(value=True)}
        formats=ttk.Frame(export);formats.grid(row=0,column=0,sticky='w')
        for key,text in [('texture','Original .texture'),('dds','DDS'),('png','PNG')]:
            ttk.Checkbutton(formats,text=text,variable=self.export_formats[key]).pack(side='left',padx=(0,8))
        buttons=ttk.Frame(export);buttons.grid(row=1,column=0,sticky='w',pady=(4,0))
        self.export_selected_button=ttk.Button(buttons,text='Export selected…',command=lambda:self._export_textures(False),state='disabled')
        self.export_selected_button.pack(side='left',padx=(0,4))
        self.export_all_button=ttk.Button(buttons,text='Export all listed…',command=lambda:self._export_textures(True),state='disabled')
        self.export_all_button.pack(side='left')
        users=ttk.Frame(right);users.grid(row=3,column=0,sticky='ew',pady=(6,0));users.columnconfigure(1,weight=1)
        users_label=ttk.Label(users,text='Used by');users_label.grid(row=0,column=0,padx=(0,4))
        self.texture_users_var=tk.StringVar(value='');self.texture_user_paths=[]
        self.texture_users_box=ttk.Combobox(users,textvariable=self.texture_users_var,state='disabled')
        self.texture_users_box.grid(row=0,column=1,sticky='ew')
        self.texture_open_button=ttk.Button(users,text='Open model',state='disabled',
                                            command=lambda:self._open_texture_user())
        self.texture_open_button.grid(row=0,column=2,padx=(4,0))
        tip('Click a texture to preview it. Ctrl/Shift-click selects several for export.',self.texture_tree)
        tip('RGB: colour without alpha. In the game alpha is usually mask data (for _c textures the self-lit mask), not transparency.\nRGBA: alpha over a checkerboard.\nR, G, B, Alpha: that one channel in grey.\n'
            'All four: R, G, B and A side by side to compare. Always fitted to the frame, so zoom and the pixel readout do not apply.',channels_label,box)
        tip('Unflipped is as the game stores it, top row first. The model builds do not flip images; they flip the UV V coordinate instead.\nApplies to the preview and PNG export. Original .texture and DDS files are written unchanged.',flip)
        tip('Original .texture: the game file, unchanged.\nDDS: the game image as a .dds file, unchanged, full size.\nPNG: a decoded 8-bit image; honours Flip vertically.',formats)
        tip('Export the textures selected in the list.',self.export_selected_button)
        tip('Export every texture the list shows, after the Filter. In the Texture type that can be thousands.',self.export_all_button)
        tip('The models whose materials reference the selected texture (uses the asset-links index, built once).',users_label,self.texture_users_box)
        tip('Show the chosen model in the Model asset type.',self.texture_open_button)

    def activate(self):
        """The Texture asset type: read the archive's texture index on first show, and again if the source root changed."""
        root=self.app.source_var.get().strip()
        if not root:
            self._fill_texture_tree('Choose the extracted source archive first.');return
        if self.archive_texture_rows is not None and self.archive_texture_root==root:
            self._fill_texture_tree();return
        self._fill_texture_tree('Reading the archive\'s texture index…')
        self.texture_list_work.submit(lambda r=root:(r,self.browser.source_loader.archive_textures(r)))

    def _texture_reset(self):
        """Back to the starting view. The archive viewer also clears its Filter, returns to List view and restores the default Maps."""
        self.texture_channels.set('RGB');self.texture_flip.set(False);self.texture_drag=None
        self.texture_zoom.set('Fit');self._texture_zoom_changed()
        if self._archive_mode():
            self.texture_view.set(self.LIST_VIEW);self._texture_view_changed()
            self.hidden_maps=set(self.DEFAULT_HIDDEN_MAPS)
            for suffix,var in self.map_vars.items():var.set(suffix in self.hidden_maps)
            self._update_map_button()
            self.texture_filter.set('')   # its trace refreshes the list after the usual short delay
        self.texture_slice.set('1');self._texture_slice_changed()
        self._draw_texture()

    def close(self):
        self.texture_work.close();self.texture_list_work.close();self.texture_grid.close()

    def _set_texture_slices(self,count):
        """Show the Slice spinbox only for a texture with more than one slice."""
        if count>1:
            self.texture_slice_frame.pack(side='left')
            self.texture_slice_box.configure(to=count,state='normal')
        else:
            self.texture_slice_frame.pack_forget()
            self.texture_slice_box.configure(state='disabled')

    # Listing
    def _archive_mode(self):
        return self.archive

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

    def _schedule_texture_filter(self):
        if self.texture_filter_job is not None:self.app.root.after_cancel(self.texture_filter_job)
        self.texture_filter_job=self.app.root.after(250,self._run_texture_filter)

    def _run_texture_filter(self):
        if self.texture_filter_job is not None:self.app.root.after_cancel(self.texture_filter_job)
        self.texture_filter_job=None
        self._fill_texture_tree()

    def _grid_view(self):
        return self.texture_view.get()==self.GRID_VIEW

    def _texture_view_changed(self):
        if self._grid_view():
            self.texture_tree_frame.grid_remove();self.texture_grid_frame.grid()
        else:
            self.texture_grid_frame.grid_remove();self.texture_tree_frame.grid()
        self._fill_texture_tree()

    def _texture_grid_changed(self):
        self._texture_selected()

    def _filter_words(self):
        """(words that must appear, words that must not) from the Filter box; a leading - means must not."""
        words=self.texture_filter.get().lower().split()
        excluded=[w[1:] for w in words if w.startswith('-') and len(w)>1]
        return [w for w in words if not (w.startswith('-') and len(w)>1)],excluded

    def _fill_texture_tree(self,message=None):
        archive=self._archive_mode();grid=self._grid_view()
        source=(self.archive_texture_rows or []) if archive else self.model_texture_rows
        include,excluded=self._filter_words()
        hidden=self.hidden_maps if archive else set()
        self.texture_matches=[r for r in source if r.get('suffix','') not in hidden
                              and all(w in r['path'].lower() for w in include)
                              and not any(w in r['path'].lower() for w in excluded)]
        # The grid draws only what is on screen, so it lists every match; the tree keeps its row limit.
        self.texture_rows=self.texture_matches if grid else self.texture_matches[:self.TEXTURE_ROW_LIMIT]
        self.texture_tree.delete(*self.texture_tree.get_children())
        if not grid:
            for i,row in enumerate(self.texture_rows):
                self.texture_tree.insert('','end',iid=str(i),text=Path(row['path']).name,values=self._texture_row_values(row,archive))
        self.texture_grid.set_rows(self.texture_rows if grid else [])
        self.texture_work.submit(lambda:None)
        self.texture_image=None;self.texture_facts=None;self._draw_texture()
        self._set_texture_slices(1);self.texture_slice.set('1')
        idle=not self.texture_exporting
        self.export_all_button.configure(state='normal' if self.texture_matches and idle else 'disabled')
        self.export_selected_button.configure(state='disabled')
        if message:self.texture_info.set(message);return
        shown='' if len(self.texture_matches)<=len(self.texture_rows) else f' Showing the first {len(self.texture_rows):,}; type in Filter to narrow.'
        if archive:
            hiding=sum(1 for r in source if r.get('suffix','') in hidden)
            maps=f' {hiding:,} hidden as maps (Maps menu).' if hiding else ''
            text=(f'{len(self.texture_matches):,} of {len(source):,} archive textures match.{shown}{maps}' if include or excluded
                  else f'{len(source):,} textures in the archive, with or without a model.{shown}{maps}')
        elif self.texture_model is None:
            text='Select a source model to list its textures, or show the whole archive.'
        elif not source:
            text='This model\'s materials reference no textures.'
        else:
            text=(f'{len(self.texture_matches)} of {len(source)} model textures match.' if include or excluded else
                  f'{len(source)} textures referenced by this model\'s materials.')+' Ctrl/Shift-click to select several.'
        self.texture_info.set(text)

    def _rebuild_map_menu(self):
        """One tick box per suffix in the archive (most common first), with its texture count."""
        from collections import Counter
        counts=Counter(r.get('suffix','') for r in self.archive_texture_rows or [])
        suffixes=[s for s,_n in counts.most_common(40)]
        suffixes+=[s for s in sorted(self.hidden_maps) if s not in suffixes]
        menu=self.texture_maps_menu;menu.delete(0,'end');self.map_vars={}
        for suffix in suffixes:
            var=self.tk.BooleanVar(value=suffix in self.hidden_maps);self.map_vars[suffix]=var
            menu.add_checkbutton(label=f"{suffix or '(no suffix)'}   {counts.get(suffix,0):,}",variable=var,command=self._map_toggled)
        menu.add_separator();menu.add_command(label='Show all',command=self._show_all_maps)

    def _map_toggled(self):
        self.hidden_maps={s for s,var in self.map_vars.items() if var.get()}
        self._update_map_button();self._schedule_texture_filter()

    def _show_all_maps(self):
        for var in self.map_vars.values():var.set(False)
        self._map_toggled()

    def _update_map_button(self):
        if self.texture_maps_button is None:return
        hidden=len(self.hidden_maps)
        self.texture_maps_button.configure(text=f'Maps: {hidden} suffix{"es" if hidden!=1 else ""} hidden' if hidden else 'Maps: none hidden')

    @staticmethod
    def _texture_row_values(row,archive):
        size=f"{row['width']}×{row['height']}" if row.get('width') else ''
        if archive:where=str(Path(row['path']).parent).replace('\\','/')
        else:where=', '.join(row['materials'])+(' (graph default)' if row.get('via_graph') else '')
        return (row.get('format_name',''),size,where)

    # Viewing
    def _texture_selection(self):
        """Indices into texture_rows of the selected textures, in the view that is showing."""
        if self._grid_view():return self.texture_grid.selected_indices()
        return [int(i) for i in self.texture_tree.selection()]

    def _texture_focus(self):
        if self._grid_view():return self.texture_grid.focus
        focus=self.texture_tree.focus()
        return int(focus) if focus else None

    def _selected_texture_paths(self):
        return [self.texture_rows[i]['path'] for i in self._texture_selection()]

    def _texture_selected(self):
        paths=self._selected_texture_paths()
        self.export_selected_button.configure(state='normal' if paths and not self.texture_exporting else 'disabled')
        focus=self._texture_focus()
        if focus is None:return
        path=self.texture_rows[focus]['path']
        if self.texture_facts and self.texture_facts['path']==path:return
        self._show_texture_users(path)
        self.texture_slice.set('1')
        self.texture_info.set('Decoding '+path+' …')
        self.texture_work.submit(self.browser.source_loader.texture_image,path,0)

    def _show_texture_users(self,path):
        """Fill the Used by list with the models whose materials reference this texture."""
        self.texture_user_paths=[]
        self.texture_users_box.configure(values=(),state='disabled');self.texture_open_button.configure(state='disabled')
        self.texture_users_var.set('Reading asset links…')
        def show(links):
            focus=self._texture_focus()
            if focus is None or self.texture_rows[focus]['path']!=path:return   # another texture was chosen meanwhile
            if links is None:
                self.texture_users_var.set(self.browser.links.message);return
            users=links.texture_users(path)
            self.texture_user_paths=[u['model'] for u in users]
            labels=[u['model']+(' (graph default)' if u['via_graph'] else '') for u in users]
            self.texture_users_box.configure(values=labels,state='readonly' if users else 'disabled')
            self.texture_users_var.set(labels[0] if labels else 'No model\'s materials reference this texture.')
            self.texture_open_button.configure(state='normal' if users else 'disabled')
            if len(users)>1:self.texture_users_box.set(f'{labels[0]}')
        self.browser.links.request(show,key='texture users')

    def _open_texture_user(self):
        index=self.texture_users_box.current()
        paths=self.texture_user_paths
        if paths:self.browser.open_model(paths[index if 0<=index<len(paths) else 0])

    def _texture_slice_changed(self):
        if not self.texture_facts or self.texture_facts['array_size']<2:return
        try:index=int(self.texture_slice.get())-1
        except ValueError:return
        index=min(max(0,index),self.texture_facts['array_size']-1)
        if index!=self.texture_facts['slice']:
            self.texture_work.submit(self.browser.source_loader.texture_image,self.texture_facts['path'],index)

    def _show_texture(self,payload):
        self.texture_image,self.texture_facts=payload
        f=self.texture_facts
        if f['path']!=self.texture_view_path:   # a different texture starts in Fit, centred
            self.texture_view_path=f['path'];self.texture_centre=[0.5,0.5]
        preview='' if f['preview_size']==f['full_size'] else f" (preview {f['preview_size'][0]}×{f['preview_size'][1]})"
        slices=f" · slice {f['slice']+1} of {f['array_size']}" if f['array_size']>1 else ''
        self.texture_info.set(f"{f['path']}\n{f['format_name']} (DXGI {f['format']}, {'sRGB' if f['srgb'] else 'linear / Non-Color'}) · "
                              f"{f['full_size'][0]}×{f['full_size'][1]}{preview}{slices} · mode {self.texture_image.mode}")
        self._set_texture_slices(f['array_size'])
        self.texture_slice.set(str(f['slice']+1))
        for i,row in enumerate(self.texture_rows):   # fill in facts the archive listing lacks
            if row['path']==f['path'] and not row.get('width'):
                row.update(format_name=f['format_name'],width=f['full_size'][0],height=f['full_size'][1])
                if not self._grid_view():self.texture_tree.item(str(i),values=self._texture_row_values(row,self._archive_mode()))
        self._draw_texture()

    def _channel_image(self,image,channels):
        """The texture as the Channels choice shows it, before Flip and zoom."""
        from PIL import Image
        bands=image.getbands()
        if channels in ('R','G','B','Alpha'):
            band={'Alpha':'A'}.get(channels,channels)
            if image.mode=='L':return image.copy()
            if band in bands:return image.getchannel(band)
            return Image.new('L',image.size,255 if band=='A' else 0)
        if channels=='RGB' or 'A' not in bands:return image.convert('RGB')
        return image.convert('RGBA')

    @staticmethod
    def _channel_sheet(source,flip,cell_w,cell_h):
        """R, G, B and A of the texture side by side in a 2×2 sheet, each in grey, labelled and fitted to its cell."""
        from PIL import Image, ImageDraw
        gap=8
        sheet=Image.new('RGB',(2*cell_w+gap,2*cell_h+gap),(43,43,43))
        draw=ImageDraw.Draw(sheet)
        rgb=source.convert('RGB')
        alpha=source.getchannel('A') if 'A' in source.getbands() else Image.new('L',source.size,255)
        for number,(name,channel) in enumerate([('R',rgb.getchannel('R')),('G',rgb.getchannel('G')),
                                                ('B',rgb.getchannel('B')),('A',alpha)]):
            if flip:channel=channel.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            iw,ih=channel.size
            scale=min(cell_w/iw,cell_h/ih)
            size=(max(1,round(iw*scale)),max(1,round(ih*scale)))
            tile=channel.resize(size,Image.Resampling.NEAREST if scale>1 else Image.Resampling.LANCZOS)
            x,y=(number%2)*(cell_w+gap),(number//2)*(cell_h+gap)
            sheet.paste(tile.convert('RGB'),(x+(cell_w-size[0])//2,y+(cell_h-size[1])//2))
            draw.text((x+6,y+4),name,fill=(255,255,255))
        return sheet

    def _preview_export_image(self):
        """The texture as the preview shows its Channels and Flip, at full size (zoom is not applied)."""
        from PIL import Image
        source=self.texture_image
        if self.texture_channels.get()==self.ALL_CHANNELS:
            return self._channel_sheet(source,self.texture_flip.get(),*source.size)
        image=self._channel_image(source,self.texture_channels.get())
        if self.texture_flip.get():image=image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return image

    def _copy_texture_preview(self):
        """Put the preview on the Windows clipboard as an image (through PowerShell, which owns the clipboard API)."""
        if self.texture_image is None:return
        import subprocess, tempfile, os
        handle,temp=tempfile.mkstemp(suffix='.png');os.close(handle)
        try:
            self._preview_export_image().save(temp,format='PNG')
            literal=temp.replace("'","''")
            script=("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                    f"$bytes=[System.IO.File]::ReadAllBytes('{literal}');"
                    "$stream=New-Object System.IO.MemoryStream(,$bytes);"
                    "[System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromStream($stream))")
            subprocess.run(['powershell','-NoProfile','-STA','-Command',script],check=True,timeout=60,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.texture_info.set('Copied the preview to the clipboard.')
        except (OSError,subprocess.SubprocessError) as error:
            self.texture_info.set(f'Cannot copy the preview: {error}')
        finally:
            try:os.remove(temp)
            except OSError:pass

    def _draw_texture(self):
        canvas=self.texture_canvas
        canvas.delete('all')
        width,height=max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
        self.texture_layout=None
        self.texture_save_buttons_state('normal' if self.texture_image is not None else 'disabled')
        if self.texture_image is None:
            self.texture_photo=None;return
        from PIL import Image
        if self.texture_channels.get()==self.ALL_CHANNELS:
            sheet=self._channel_sheet(self.texture_image,self.texture_flip.get(),max(1,(width-8)//2),max(1,(height-8)//2))
            self._show_texture_photo(sheet,width,height);return
        image=self._channel_image(self.texture_image,self.texture_channels.get())
        if self.texture_flip.get():image=image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        iw,ih=image.size
        if self.texture_zoom.get()=='Fit':
            scale=min(width/iw,height/ih);box=(0,0,iw,ih)
        else:
            # A window onto the texture at the chosen zoom, centred on texture_centre and kept inside the image.
            scale=int(self.texture_zoom.get().rstrip('%'))/100
            view_w,view_h=min(iw,width/scale),min(ih,height/scale)
            left=min(max(self.texture_centre[0]*iw-view_w/2,0),iw-view_w)
            top=min(max(self.texture_centre[1]*ih-view_h/2,0),ih-view_h)
            box=(int(left),int(top),min(iw,int(left)+max(1,round(view_w))),min(ih,int(top)+max(1,round(view_h))))
        image=image.crop(box)
        size=(max(1,round(image.size[0]*scale)),max(1,round(image.size[1]*scale)))
        image=image.resize(size,Image.Resampling.NEAREST if scale>1 else Image.Resampling.LANCZOS)
        # Where the picture sits on the canvas, for the pixel readout and the pointer maths.
        self.texture_layout=dict(box=box,scale=scale,origin=(width//2-size[0]//2,height//2-size[1]//2),size=(iw,ih))
        self._show_texture_photo(image,width,height)

    def _show_texture_photo(self,image,width,height):
        """Draw a Pillow image centred on the preview canvas; RGBA is composited over a checkerboard."""
        from PIL import Image
        import base64, io
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
        self.texture_canvas.create_image(width//2,height//2,image=self.texture_photo,anchor='center')

    def texture_save_buttons_state(self,state):
        for button in self.texture_preview_buttons:button.configure(state=state)

    def _texture_point(self,x,y):
        """Canvas position -> (x, y) in the displayed image's pixels, or None off the picture."""
        layout=self.texture_layout
        if not layout:return None
        left,top,right,bottom=layout['box']
        sx=left+(x-layout['origin'][0])/layout['scale'];sy=top+(y-layout['origin'][1])/layout['scale']
        if left<=sx<right and top<=sy<bottom:return sx,sy
        return None

    def _texture_hover(self,event):
        point=self._texture_point(event.x,event.y)
        image=self.texture_image
        if point is None or image is None:
            self.texture_pixel.set('');return
        ix,iy=int(point[0]),int(point[1])
        if self.texture_flip.get():iy=image.size[1]-1-iy   # the readout reports the stored, unflipped row
        value=image.getpixel((ix,iy))
        values=value if isinstance(value,tuple) else (value,)
        bands=' '.join(f'{name} {number}' for name,number in zip(image.getbands(),values))
        self.texture_pixel.set(f'Pixel x {ix}, y {iy} of {image.size[0]}×{image.size[1]}: {bands}')

    def _texture_press(self,event):
        if self.texture_zoom.get()!='Fit':self.texture_drag=(event.x,event.y,list(self.texture_centre))

    def _texture_drag(self,event):
        layout=self.texture_layout
        if not self.texture_drag or not layout:return
        x0,y0,centre=self.texture_drag
        iw,ih=layout['size']
        dx=(event.x-x0)/layout['scale']/iw;dy=(event.y-y0)/layout['scale']/ih
        self.texture_centre=[min(max(centre[0]-dx,0),1),min(max(centre[1]-dy,0),1)]
        self._draw_texture()

    def _texture_wheel(self,event):
        try:under=self.app.root.winfo_containing(event.x_root,event.y_root)
        except Exception:return
        if under is self.texture_grid.canvas:
            if self._grid_view():self.texture_grid.wheel(event.delta)
            return
        if under is not self.texture_canvas or self.texture_image is None:return
        point=self._texture_point(event.x,event.y)
        if point is not None:   # zoom towards the pointer
            iw,ih=self.texture_layout['size']
            self.texture_centre=[point[0]/iw,point[1]/ih]
        steps=self.ZOOM_STEPS.index(self.texture_zoom.get())+(1 if event.delta>0 else -1)
        steps=min(max(steps,0),len(self.ZOOM_STEPS)-1)
        self.texture_zoom.set(self.ZOOM_STEPS[steps])
        if steps==0:self.texture_centre=[0.5,0.5]
        self._draw_texture()

    def _texture_zoom_changed(self):
        if self.texture_zoom.get()=='Fit':self.texture_centre=[0.5,0.5]
        self._draw_texture()

    def _texture_toggle_fit(self):
        self.texture_zoom.set('100%' if self.texture_zoom.get()=='Fit' else 'Fit')
        self._texture_zoom_changed()

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
            try:self.texture_events.put(('done',(folder,self.browser.source_loader.export_textures(paths,folder,formats,flip,progress))))
            except Exception as error:self.texture_events.put(('error',str(error)))
        threading.Thread(target=work,daemon=True).start()
        self.texture_info.set(f'Exporting {len(paths):,} textures to {folder} …')

    def drain(self):
        try:
            while True:
                generation,payload,error=self.texture_list_work.events.get_nowait()
                if generation!=self.texture_list_work.generation:continue
                if error:
                    if self._archive_mode():self._fill_texture_tree('Cannot read the archive\'s textures: '+error)
                elif payload is not None:
                    self.archive_texture_root,self.archive_texture_rows=payload
                    self.texture_grid.forget_thumbnails();self._rebuild_map_menu()
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
                self.export_selected_button.configure(state='normal' if self._texture_selection() else 'disabled')
                if kind=='error':
                    self.texture_info.set('Export failed: '+value);self.app._append_log('Texture export failed: '+value,'error');continue
                folder,rows=value
                failed=[r for r in rows if r['error']]
                written=sum(len(r['files']) for r in rows)
                self.texture_info.set(f'Wrote {written:,} files for {len(rows)-len(failed):,} of {len(rows):,} textures to {folder}.'
                                      +(f' {len(failed)} failed; see the log.' if failed else ''))
                for r in failed:self.app._append_log(f"Texture export failed for {r['path']}: {r['error']}",'error')
        except queue.Empty:pass
