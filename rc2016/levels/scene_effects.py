"""Live scene buffers and the key-light shadow cascades (rc2016.levels.shadow_cascades); native selection is unproven."""
import ctypes as C
import time
from rc2016.levels.capture_calibration import frame
AO_UNIT, SHADOW_UNIT = 16, 17
GRID_UNITS = (19, 20, 21)   # lightgrid tile table, colour atlas, direction/plane atlas (unit 18 is the display table)
PROBE_UNITS = (22, 23)      # window glass probe volume: per-cell selector atlas, probe cube atlas (rc2016.levels.probe_volume)
# Scene content changes (models, instances, materials) arrive many times a second during a load; each
# shadow refresh redraws the whole unculled level, so content-driven refreshes wait this long apart.
SHADOW_REFRESH_SECONDS = 2.0
# The cascades follow the camera (the game refits them every frame). Viewer choice: they are refitted once the camera
# has rested AO_SETTLE_SECONDS, or at most this often while it moves, and only cascades whose snapped fit changed redraw.
SHADOW_FOLLOW_SECONDS = 0.5
# Visual calibration, not decoded data, used only by the Level atmosphere lighting: the sky (ambient) diffuse term
# is scaled by this factor where the key light is cast-shadowed and would otherwise reach the surface. The
# atmosphere's RGB96 sun is about 12 % of horizontal-ground light, so without it shadows keep most of the lit
# value (lit/shadow 1.1 to 1.4 on grass), where grass in cast shadow in one in-game Novalis screenshot sits near
# 1/10 of its sunlit display luminance. At 0.05 the viewer reaches about 1/9 on grass; the floor that remains
# (about 0.014 display luminance, 0.011 in the game) is specular, self-lit and fog, which this leaves alone.
# Sunlit pixels are untouched. Part of the game's depth is probably occlusion of the sky by the caster itself
# (trees, buildings), which this models only as a constant.
SHADOW_AMBIENT = 0.05
# Per atmosphere (lower case path), where the Novalis factor does not hold: Aridia's sun RGB96 is only about 15 % of its
# ambient, and in one in-game frame its ground is lit evenly (display 0.25, 0.24, 0.19 on grass in the open), so the
# factor is 1 there (visual fit to that one frame).
LEVEL_SHADOW_AMBIENT = {'atmosphere/atm_aridia/atm_aridia.atmosphere': 1.0}
ATMOSPHERE_LIGHTING = 'Level atmosphere'


def shadow_ambient(atmosphere):
    """The ambient factor under cast shadow for an atmosphere path: ``LEVEL_SHADOW_AMBIENT`` or ``SHADOW_AMBIENT``."""
    captured = frame(atmosphere)
    if captured:
        return captured['shadow_ambient']
    return LEVEL_SHADOW_AMBIENT.get((atmosphere or '').lower(), SHADOW_AMBIENT)


def shadowed_ambient_factor(shadow, ndl, ambient=SHADOW_AMBIENT):
    """CPU mirror of sceneShadowedAmbient: 1 for a lit surface (shadow 1) or one facing away from the key
    light (ndl 0), ambient for a surface the key light would reach but the shadow map blocks (shadow 0)."""
    reach = min(max(ndl * 4.0, 0.0), 1.0)
    return 1.0 - (1.0 - ambient) * (1.0 - shadow) * reach

# AO is screen space, so it is valid for one camera. It is rendered once the camera has rested
# this long and reused until the camera or scene changes; frames in between draw without AO.
AO_SETTLE_SECONDS = 0.15
SCENE_UNIFORMS = ('uSceneEnabled','uSceneAOEnabled','uSceneShadowEnabled','uSceneAO','uSceneShadow',
                  'uSceneSize','uSceneShadowSize','uSceneShadowView','uSceneCascadeScale','uSceneCascadeOffset','uSceneCascadeBounds',
                  'uSceneShadowLanes','uSceneShadowSplit','uSceneLightCount','uSceneLightPos','uSceneLightColour','uSceneLightDir','uSceneLightSpot','uSceneShadowAmbient',
                  'uGridOn','uGridTable','uGridColour','uGridDirQ','uGridFromView','uGridParams','uGridInvSize')
HELPERS = '''
uniform bool uSceneEnabled,uSceneAOEnabled,uSceneShadowEnabled;
uniform sampler2D uSceneAO; uniform sampler2DShadow uSceneShadow;
uniform vec2 uSceneSize,uSceneShadowSize; uniform mat4 uSceneShadowView;
uniform vec4 uSceneCascadeScale[6],uSceneCascadeOffset[6],uSceneCascadeBounds[6],uSceneShadowLanes[3]; uniform vec2 uSceneShadowSplit;
uniform int uSceneLightCount; uniform vec4 uSceneLightPos[32]; uniform vec3 uSceneLightColour[32]; uniform float uSceneShadowAmbient;
// Spot cones (inferred, rc2016.levels.scene_lighting.SPOT_CONE_OFFSET): Dir.xyz the aim in view space and Dir.w 1 for a spot (0 for a
// point); Spot.x the outer half-angle cosine, Spot.y 1 / (inner - outer cosine). L points from the surface to the light.
uniform vec4 uSceneLightDir[32]; uniform vec2 uSceneLightSpot[32];
float sceneSpot(int i,vec3 L){
 if(uSceneLightDir[i].w<0.5)return 1.0;
 float t=clamp((dot(-L,uSceneLightDir[i].xyz)-uSceneLightSpot[i].x)*uSceneLightSpot[i].y,0.0,1.0);return t*t*(3.0-2.0*t);
}
float sceneAO(){return uSceneEnabled&&uSceneAOEnabled?texture2D(uSceneAO,gl_FragCoord.xy/uSceneSize).r:1.0;}
// Sun cascades as the game's screen-shadow compute samples them (docs/material-decoding/shadow-atlas.md): cascade from
// the frustum-hull lanes, blend into the next one over the last 1/16 of each band, fade to lit near the shadow
// distance, receiver pushed along L + sat(7 N.L + 2) N, nine weighted linear Less comparisons per cascade.
float sceneShadowTile(vec3 l,int j){
 vec4 cs=uSceneCascadeScale[j],co=uSceneCascadeOffset[j],cb=uSceneCascadeBounds[j];
 vec2 uv=clamp(l.xy*cs.xy+co.xy,cb.xy,cb.zw);float z=l.z*cs.z+co.z;
 vec2 st=uv*uSceneShadowSize+0.5,f=fract(st),base=(floor(st)-0.5)/uSceneShadowSize;
 vec2 lo=4.0-3.0*f,hi=1.0+3.0*f;
 vec2 a=(3.0-2.0*f)/lo-2.0,b=(3.0+f)/7.0,c=f/hi+2.0;
 vec3 wx=vec3(lo.x,7.0,hi.x),wy=vec3(lo.y,7.0,hi.y);
 vec3 dx=vec3(a.x,b.x,c.x)/uSceneShadowSize.x,dy=vec3(a.y,b.y,c.y)/uSceneShadowSize.y;
 float total=0.0;for(int y=0;y<3;y++)for(int x=0;x<3;x++)total+=wx[x]*wy[y]*shadow2D(uSceneShadow,vec3(base+vec2(dx[x],dy[y]),z)).r;
 return total*0.0069444444961845874786376953125;
}
float sceneShadow(vec3 p,vec3 N){
 if(!uSceneEnabled||!uSceneShadowEnabled)return 1.0;
 vec3 l=(uSceneShadowView*vec4(p,1.0)).xyz;
 vec4 q=l.x*uSceneShadowLanes[0]+l.y*uSceneShadowLanes[1]+uSceneShadowLanes[2];
 float M=max(max(q.x,q.y),max(q.z,q.w)),fade=clamp(10.0*M-9.0,0.0,1.0);
 if(fade>=1.0)return 1.0;
 float f=max(0.0,uSceneShadowSplit.x*log2(max(M,1e-30))+uSceneShadowSplit.y),jf=min(floor(f),5.0),fr=f-floor(f);
 int j=int(jf),j1=int(min(jf+1.0,5.0));
 float blend=max(0.0,16.0*fr-fade-15.0);
 vec3 L=-vec3(uSceneShadowView[0][2],uSceneShadowView[1][2],uSceneShadowView[2][2]);
 float w=mix(uSceneCascadeOffset[j].w,uSceneCascadeOffset[j1].w,clamp(2.0*fr-1.0,0.0,1.0));
 vec3 lp=(uSceneShadowView*vec4(p+(L+clamp(7.0*dot(N,L)+2.0,0.0,1.0)*N)*w,1.0)).xyz;
 float v=sceneShadowTile(lp,j);
 if(blend>0.0)v=mix(v,sceneShadowTile(lp,j1),blend);
 return mix(v,1.0,fade);
}
// Visual calibration (see SHADOW_AMBIENT): scales the sky diffuse where the key light is cast-shadowed; 1 when lit.
float sceneShadowedAmbient(float shadow,float ndl){return 1.0-(1.0-uSceneShadowAmbient)*(1.0-shadow)*clamp(ndl*4.0,0.0,1.0);}
float sceneSpecularOcclusion(vec3 N,vec3 V,float a){
 float A=sceneAO(),nv=clamp(dot(N,V),0.0,1.0),t=nv*nv+(1.0-nv*nv)*A*A*A;return mix(t,A,a);
}
'''

def cascade_keys(fit):
    """Per cascade, the snapped fit that decides whether its tile must be redrawn."""
    import numpy as np
    return tuple((tuple(int(v) for v in np.round(fit['centres'][j]/fit['texels'][j])),round(float(fit['widths'][j]),6),
                  int(round(fit['z_near'][j]/fit['texels'][j]))) for j in range(len(fit['widths'])))

class SceneEffects:
    def __init__(self,gl,shading):
        self.gl,self.sh=gl,shading;self.size=None;self.fbos=[];self.textures=[];self.ao=None;self.ao_texture=0
        self.shadow_tile=None;self.shadow_size=None;self.shadow_key=None;self.cascade_fit=None;self.view_shadow=None;self.depth_clamp=None
        self.shadow_time=0.;self.shadow_retry=None
        self.ao_key=None;self.ao_valid=False;self.ao_retry=None
        ui,si,vp=C.c_uint,C.c_int,C.c_void_p
        def fn(n,result,args):
            address=gl.wglGetProcAddress(n.encode())
            if not address:raise RuntimeError('Scene preview requires '+n)
            return C.WINFUNCTYPE(result,*args)(address)
        self.gen=fn('glGenFramebuffers',None,[si,vp]);self.bind=fn('glBindFramebuffer',None,[ui,ui])
        self.attach=fn('glFramebufferTexture2D',None,[ui,ui,ui,ui,si]);self.check=fn('glCheckFramebufferStatus',ui,[ui])
        self.delete=fn('glDeleteFramebuffers',None,[si,vp]);self.draw_buffers=fn('glDrawBuffers',None,[si,vp])
        self.uniform2=fn('glUniform2f',None,[si,C.c_float,C.c_float]);self.uniform4=fn('glUniform4fv',None,[si,si,vp])
        self.uniform_matrix=fn('glUniformMatrix4fv',None,[si,si,C.c_ubyte,vp])
        gl.glGetFloatv.argtypes=[ui,vp];gl.glGetIntegerv.argtypes=[ui,vp];gl.glPushAttrib.argtypes=[ui]
        gl.glReadBuffer.argtypes=[ui];gl.glDrawBuffer.argtypes=[ui]
        maximum=si();gl.glGetIntegerv(0x8872,C.byref(maximum))
        if maximum.value<=SHADOW_UNIT:raise RuntimeError('Scene preview requires 18 fragment texture units')
        # Lightgrid lookup (optional): three 3D textures on units above the display table.
        self.grid_textures=[];self.grid_source=None;self.grid_size=None;self.view_to_native=None;self.grid_error=None;self.grid_failed=None
        try:
            if maximum.value<=max(GRID_UNITS):raise RuntimeError('lightgrid needs %d fragment texture units'%(max(GRID_UNITS)+1))
            self.image3d=fn('glTexImage3D',None,[ui,si,si,si,si,si,si,ui,ui,vp])
        except RuntimeError as exc:
            self.image3d=None;self.grid_error=str(exc)
        # Probe volume of the window glass (optional): the selector atlas (3D) and the BC6H probe atlas (2D).
        self.probe_textures=[];self.probe_source=None;self.probe_grid=None;self.probe_failed=None;self.probe_error=None
        try:
            if maximum.value<=max(PROBE_UNITS):raise RuntimeError('the probe volume needs %d fragment texture units'%(max(PROBE_UNITS)+1))
            self.compressed2d=fn('glCompressedTexImage2D',None,[ui,si,ui,si,si,si,si,vp])
        except RuntimeError as exc:
            self.compressed2d=None;self.probe_error=str(exc)
    def _texture(self,w,h,internal,fmt,typ,shadow=False):
        gl=self.gl;obj=C.c_uint();gl.glGenTextures(1,C.byref(obj));self.textures.append(obj.value)
        self.sh.glActiveTexture(0x84C0+SHADOW_UNIT);gl.glBindTexture(0x0DE1,obj)
        for p,v in ((0x2801,0x2601 if shadow else 0x2600),(0x2800,0x2601 if shadow else 0x2600),(0x2802,0x812F),(0x2803,0x812F)):gl.glTexParameteri(0x0DE1,p,v)
        if shadow:gl.glTexParameteri(0x0DE1,0x884C,0x884E);gl.glTexParameteri(0x0DE1,0x884D,0x0201)
        gl.glTexImage2D(0x0DE1,0,internal,w,h,0,fmt,typ,None);return obj.value
    def _fbo(self):
        obj=C.c_uint();self.gen(1,C.byref(obj));self.fbos.append(obj.value);self.bind(0x8D40,obj);return obj.value
    def resize(self,w,h):
        if self.size==(w,h):return
        self.close();self.size=(w,h)
        self.normal=self._texture(w,h,0x8059,0x1908,0x8368);self.depth=self._texture(w,h,0x822E,0x1903,0x1406)
        self.raster_depth=self._texture(w,h,0x8CAC,0x1902,0x1406);self.geometry_fbo=self._fbo()
        for attachment,texture in ((0x8CE0,self.normal),(0x8CE1,self.depth),(0x8D00,self.raster_depth)):self.attach(0x8D40,attachment,0x0DE1,texture,0)
        self.draw_buffers(2,(C.c_uint*2)(0x8CE0,0x8CE1))
        if self.check(0x8D40)!=0x8CD5:raise RuntimeError('Scene geometry framebuffer incomplete')
        from rc2016.levels.shadow_cascades import TILE,atlas_size
        if self.shadow_tile is None:   # the game's 1792 tiles, smaller only where the driver's texture limit needs it
            limit=C.c_int();self.gl.glGetIntegerv(0x0D33,C.byref(limit));self.shadow_tile=min(TILE,max(256,limit.value//3))
            self.gl.glGetError();self.gl.glEnable(0x864F);self.depth_clamp=not self.gl.glGetError();self.gl.glDisable(0x864F);self.gl.glGetError()
        self.shadow_size=atlas_size(self.shadow_tile)
        self.shadow_depth=self._texture(*self.shadow_size,0x81A5,0x1902,0x1403,True)
        self.shadow_fbo=self._fbo();self.attach(0x8D40,0x8D00,0x0DE1,self.shadow_depth,0);self.gl.glDrawBuffer(0);self.gl.glReadBuffer(0)
        if self.check(0x8D40)!=0x8CD5:raise RuntimeError('Scene shadow framebuffer incomplete')
        self.bind(0x8D40,0);self.sh.glActiveTexture(0x84C0)
    def prepare(self,view,draw_geometry):
        import numpy as np
        gl=self.gl;w,h=max(1,view.winfo_width()),max(1,view.winfo_height());self.resize(w,h)
        projection=(C.c_float*16)();modelview=(C.c_float*16)();gl.glGetFloatv(0x0BA7,projection);gl.glGetFloatv(0x0BA6,modelview)
        vm=np.array(modelview,dtype=float).reshape(4,4).T;self.view_to_native=np.linalg.inv(vm);gl.glPushAttrib(0x00004000|0x00000100|0x00000800|0x00002000|0x00080000)
        try:
            if view.scene_shadows:
                if view.lighting in view.ENVIRONMENT_LIGHTINGS and view._environment_lighting==view.lighting and view._environment:
                    p=view._environment['sun_direction'];direction=(p[0],p[2],-p[1])
                else:
                    from rc2016.viewer.viewport import RIG_TO_WORLD
                    direction=np.linalg.solve(np.array(view.OBJECT_TO_WORLD),np.array(RIG_TO_WORLD)@np.array([-.45,.65,.62]))
                self._prepare_shadow(view,draw_geometry,projection,modelview,direction)
            gl.glMatrixMode(0x1701);gl.glLoadMatrixf(projection);gl.glMatrixMode(0x1700);gl.glLoadMatrixf(modelview)
            if view.scene_ao:
                key=(tuple(projection),tuple(modelview),w,h,view._scene_revision)
                if key!=self.ao_key:self.ao_valid=False
                if key!=self.ao_key and self._ao_due(view,time.perf_counter()):
                    self.bind(0x8D40,self.geometry_fbo);self.draw_buffers(2,(C.c_uint*2)(0x8CE0,0x8CE1))
                    gl.glViewport(0,0,w,h);gl.glClearColor(0.,0.,0.,0.);gl.glClear(0x00004000|0x00000100);draw_geometry(shadow=False);self.bind(0x8D40,0)
                    if self.ao is None:
                        from rc2016.levels.scene_ao import SceneAO
                        self.ao=SceneAO(gl,self.sh)
                    self.ao_texture=self.ao.render(self.depth,self.normal,w,h,list(projection))
                    self.ao_key=key;self.ao_valid=True
            if self.cascade_fit is None:self.view_shadow=None
            else:basis=np.eye(4);basis[:3,:3]=self.cascade_fit['basis'];self.view_shadow=basis@self.view_to_native
        finally:
            self.bind(0x8D40,0);gl.glPopAttrib();gl.glMatrixMode(0x1701);gl.glLoadMatrixf(projection);gl.glMatrixMode(0x1700);gl.glLoadMatrixf(modelview);self.sh.glActiveTexture(0x84C0)
    def _prepare_shadow(self,view,draw_geometry,projection,modelview,direction):
        """Refit the cascades to the camera and redraw the tiles whose snapped fit changed (all of them for a new
        light direction or scene content). Without depth clamp the near planes move up to the scene's top in light space."""
        import numpy as np
        from rc2016.levels import shadow_cascades as sc
        gl=self.gl;camera=sc.camera_from_gl(list(projection),list(modelview));caster_min=None
        if not self.depth_clamp:
            low,high=view.scene_bounds();corners=np.array([[x,y,z] for x in (low[0],high[0]) for y in (low[1],high[1]) for z in (low[2],high[2])])
            caster_min=float((corners@sc.light_basis(direction)[2]).min())
        fit=sc.fit(*camera,direction,tile=self.shadow_tile,caster_min_z=caster_min);keys=cascade_keys(fit)
        key=(view._scene_revision,tuple(float(x) for x in direction),keys);now=time.perf_counter()
        if key==self.shadow_key or not self._shadow_due(view,key,now):return
        full=self.shadow_key is None or key[:2]!=self.shadow_key[:2]
        dirty=[j for j in range(sc.CASCADES) if full or keys[j]!=self.shadow_key[2][j]]
        self.bind(0x8D40,self.shadow_fbo);gl.glEnable(0x0C11)
        if self.depth_clamp:gl.glEnable(0x864F)
        tile=self.shadow_tile
        for j in dirty:
            x0,y0=sc.tile_origin(j,tile);gl.glViewport(x0,y0,tile,tile);gl.glScissor(x0,y0,tile,tile);gl.glClear(0x00000100)
            sv,sp=sc.gl_matrices(fit,j)
            gl.glMatrixMode(0x1701);gl.glLoadMatrixf((C.c_float*16)(*sp.T.flat));gl.glMatrixMode(0x1700);gl.glLoadMatrixf((C.c_float*16)(*sv.T.flat))
            draw_geometry(shadow=True,planes=sc.cascade_planes(fit,j))
        gl.glDisable(0x0C11)
        if self.depth_clamp:gl.glDisable(0x864F)
        self.cascade_fit=fit;self.shadow_key=key;self.shadow_time=time.perf_counter()
    def _ao_due(self,view,now):
        """Render AO once the camera has rested AO_SETTLE_SECONDS; until then schedule one redraw."""
        wait=getattr(view,'_camera_changed_at',float('-inf'))+AO_SETTLE_SECONDS-now
        if wait<=0:return True
        if self.ao_retry is None:
            def retry():
                self.ao_retry=None
                view._schedule_draw()
            self.ao_retry=(view,view.after(int(wait*1000)+1,retry))
        return False
    def _shadow_due(self,view,key,now):
        """Refresh at once for a new light direction or the first map; rate-limit content changes, refit to a moved
        camera once it rests (or every SHADOW_FOLLOW_SECONDS), and schedule one redraw so the final view gets its map."""
        if self.shadow_key is None or key[1]!=self.shadow_key[1]:return True
        if key[0]!=self.shadow_key[0]:wait=self.shadow_time+SHADOW_REFRESH_SECONDS-now
        else:wait=min(getattr(view,'_camera_changed_at',float('-inf'))+AO_SETTLE_SECONDS,self.shadow_time+SHADOW_FOLLOW_SECONDS)-now
        if wait<=0:return True
        if self.shadow_retry is None:
            def retry():
                self.shadow_retry=None
                view._schedule_draw()
            self.shadow_retry=(view,view.after(int(wait*1000)+1,retry))
        return False
    def _upload_grid(self,grid):
        """Upload a lightgrid.LightGrid to three 3D textures; returns False (and records why) when the driver refuses."""
        import numpy as np
        gl=self.gl;self.release_grid();colour,dirq=grid.atlas()
        names=(C.c_uint*3)();gl.glGenTextures(3,names);self.grid_textures=list(names)
        gl.glPixelStorei(0x0CF5,1)
        planes=((grid.table,0x881A,0x1908,0x1406),(colour,0x8C3A,0x1907,0x8C3B),(dirq,0x8058,0x1908,0x1401))
        try:
            for unit,name,(data,internal,fmt,typ) in zip(GRID_UNITS,self.grid_textures,planes):
                self.sh.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x806F,name)
                for parameter,value in ((0x2801,0x2600),(0x2800,0x2600),(0x2802,0x812F),(0x2803,0x812F),(0x8072,0x812F)):gl.glTexParameteri(0x806F,parameter,value)
                data=np.ascontiguousarray(data);d,h,w=data.shape[:3]
                self.image3d(0x806F,0,internal,w,h,d,0,fmt,typ,data.ctypes.data_as(C.c_void_p))
                if gl.glGetError():raise RuntimeError('glTexImage3D failed for the lightgrid')
        except Exception as exc:
            self.grid_error=str(exc);self.release_grid();self.sh.glActiveTexture(0x84C0);return False
        gl.glPixelStorei(0x0CF5,4);self.sh.glActiveTexture(0x84C0)
        ax,ay,az=grid.atlas_tiles;self.grid_size=(ax*16,ay*16,az*16);self.grid_source=grid;return True
    def release_grid(self):
        if self.grid_textures:self.gl.glDeleteTextures(len(self.grid_textures),(C.c_uint*len(self.grid_textures))(*self.grid_textures))
        self.grid_textures=[];self.grid_source=None;self.grid_size=None;self.release_probes()
    def _upload_probes(self,grid,volume):
        """Upload a probe_volume.ProbeVolume: its selectors in the lightgrid atlas layout (3D RGBA8, NEAREST) and its BC6H
        probe images in one 2D atlas (LINEAR, mip 0 only). False (and the reason recorded) when the driver refuses."""
        import numpy as np
        gl=self.gl;self.release_probes()
        try:
            limit=C.c_int();gl.glGetIntegerv(0x0D33,C.byref(limit))
            columns=max(1,min(len(volume.blocks),limit.value//512))
            atlas,(columns,rows)=volume.atlas_blocks(columns)
            if rows*256>limit.value:raise RuntimeError('%d probes exceed the %d texel texture limit'%(len(volume.blocks),limit.value))
            selectors=grid.atlas_layout(volume.selectors)
            names=(C.c_uint*2)();gl.glGenTextures(2,names);self.probe_textures=list(names)
            gl.glPixelStorei(0x0CF5,1)
            self.sh.glActiveTexture(0x84C0+PROBE_UNITS[0]);gl.glBindTexture(0x806F,names[0])
            for parameter,value in ((0x2801,0x2600),(0x2800,0x2600),(0x2802,0x812F),(0x2803,0x812F),(0x8072,0x812F)):gl.glTexParameteri(0x806F,parameter,value)
            d,h,w=selectors.shape[:3]
            self.image3d(0x806F,0,0x8058,w,h,d,0,0x1908,0x1401,selectors.ctypes.data_as(C.c_void_p))
            if gl.glGetError():raise RuntimeError('glTexImage3D failed for the probe selectors')
            self.sh.glActiveTexture(0x84C0+PROBE_UNITS[1]);gl.glBindTexture(0x0DE1,names[1])
            for parameter,value in ((0x2801,0x2601),(0x2800,0x2601),(0x2802,0x812F),(0x2803,0x812F),(0x813D,0)):gl.glTexParameteri(0x0DE1,parameter,value)
            data=np.ascontiguousarray(atlas)
            self.compressed2d(0x0DE1,0,0x8E8F,columns*512,rows*256,0,data.nbytes,data.ctypes.data_as(C.c_void_p))   # BC6H unsigned
            if gl.glGetError():raise RuntimeError('glCompressedTexImage2D failed for the probe atlas')
        except Exception as exc:
            self.probe_error=str(exc);self.release_probes();gl.glPixelStorei(0x0CF5,4);self.sh.glActiveTexture(0x84C0);return False
        gl.glPixelStorei(0x0CF5,4);self.sh.glActiveTexture(0x84C0)
        self.probe_grid=(columns,rows);self.probe_source=volume;return True
    def release_probes(self):
        if self.probe_textures:self.gl.glDeleteTextures(len(self.probe_textures),(C.c_uint*len(self.probe_textures))(*self.probe_textures))
        self.probe_textures=[];self.probe_source=None;self.probe_grid=None
    def _probes_ready(self,view,grid):
        """The ProbeVolume to sample this frame with ``grid`` (uploaded on first use), or None."""
        volume=view._environment.get('probe_volume') if grid is not None else None
        if volume is None or self.compressed2d is None or not volume.blocks:return None
        if self.probe_source is not volume:
            if self.probe_failed is volume or not self._upload_probes(grid,volume):
                self.probe_failed=volume;return None
        return volume
    def _grid_ready(self,view):
        """The LightGrid to sample this frame (uploaded on first use), or None."""
        if self.image3d is None or self.view_to_native is None or not getattr(view,'lightgrid_enabled',True):return None
        if view.lighting!=ATMOSPHERE_LIGHTING or view._environment_lighting!=view.lighting or not view._environment:return None
        grid=view._environment.get('lightgrid')
        if grid is None:return None
        if self.grid_source is not grid:
            if self.grid_failed is grid or not self._upload_grid(grid):
                self.grid_failed=grid;return None
        return grid
    def apply(self,view,u):
        sh,gl=self.sh,self.gl;ready=not view._scene_geometry and view.scene_effects_active()
        grid=self._grid_ready(view) if ready else None
        sh.glUniform1i(u.get('uGridOn',-1),int(grid is not None))
        if grid is not None:
            from rc2016.levels.lightgrid import SKY_TERM_SCALE,ambient_scale
            for name,unit,texture in zip(('uGridTable','uGridColour','uGridDirQ'),GRID_UNITS,self.grid_textures):
                sh.glUniform1i(u.get(name,-1),unit);sh.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x806F,texture)
            sh.glActiveTexture(0x84C0)
            self.uniform_matrix(u.get('uGridFromView',-1),1,0,(C.c_float*16)(*self.view_to_native.T.flat))
            self.uniform2(u.get('uGridParams',-1),(getattr(view,'_sky',None) or {}).get('sky_scale') or SKY_TERM_SCALE,ambient_scale((getattr(view,'_sky',None) or {}).get('atmosphere')))
            sh.glUniform3f(u.get('uGridInvSize',-1),*(1.0/s for s in self.grid_size))
        probes=self._probes_ready(view,grid)
        sh.glUniform1i(u.get('uProbeOn',-1),int(probes is not None))
        if probes is not None:
            for name,unit,target,texture in zip(('uProbeSel','uProbeAtlas'),PROBE_UNITS,(0x806F,0x0DE1),self.probe_textures):
                sh.glUniform1i(u.get(name,-1),unit);sh.glActiveTexture(0x84C0+unit);gl.glBindTexture(target,texture)
            sh.glActiveTexture(0x84C0);self.uniform2(u.get('uProbeAtlasGrid',-1),*(float(x) for x in self.probe_grid))
        sh.glUniform1i(u['uSceneEnabled'],int(ready));sh.glUniform1i(u.get('uSceneGeometry',-1),int(view._scene_geometry))
        sh.glUniform1i(u['uSceneAOEnabled'],int(ready and view.scene_ao and self.ao_valid and bool(self.ao_texture)))
        sh.glUniform1i(u['uSceneShadowEnabled'],int(ready and view.scene_shadows and self.view_shadow is not None))
        sh.glUniform1i(u['uSceneAO'],AO_UNIT);sh.glUniform1i(u['uSceneShadow'],SHADOW_UNIT)
        sh.glUniform1f(u.get('uSceneShadowAmbient',-1),shadow_ambient((getattr(view,'_sky',None) or {}).get('atmosphere')) if view.lighting==ATMOSPHERE_LIGHTING else 1.0)
        self.uniform2(u['uSceneSize'],*self.size);self.uniform2(u['uSceneShadowSize'],*self.shadow_size)
        for unit,texture in ((AO_UNIT,self.ao_texture),(SHADOW_UNIT,self.shadow_depth)):
            sh.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x0DE1,texture)
        if self.view_shadow is not None:
            import math
            from rc2016.levels.shadow_cascades import CASCADES,SPLIT_RATIO,lookup_constants
            fit=self.cascade_fit;self.uniform_matrix(u.get('uSceneShadowView',-1),1,0,(C.c_float*16)(*self.view_shadow.T.flat))
            for name,values in zip(('uSceneCascadeScale','uSceneCascadeOffset','uSceneCascadeBounds'),lookup_constants(fit,self.shadow_size)):
                self.uniform4(u.get(name,-1),CASCADES,(C.c_float*(4*CASCADES))(*values.flat))
            self.uniform4(u.get('uSceneShadowLanes',-1),3,(C.c_float*12)(*fit['lanes'].T.flat))
            self.uniform2(u.get('uSceneShadowSplit',-1),1.0/math.log2(SPLIT_RATIO),float(CASCADES))
        sh.glActiveTexture(0x84C0)
    def close(self):   # keeps the lightgrid textures (a resize closes the buffers); release_grid frees them
        for name in ('shadow_retry','ao_retry'):
            pending=getattr(self,name,None)
            if pending:
                setattr(self,name,None);view,after=pending
                try:view.after_cancel(after)
                except Exception:pass
        if self.ao:self.ao.close();self.ao=None
        if self.textures:self.gl.glDeleteTextures(len(self.textures),(C.c_uint*len(self.textures))(*self.textures));self.textures=[]
        if self.fbos:self.delete(len(self.fbos),(C.c_uint*len(self.fbos))(*self.fbos));self.fbos=[]
        self.size=None;self.shadow_key=None;self.ao_texture=0;self.cascade_fit=None;self.view_shadow=None
        self.ao_key=None;self.ao_valid=False
