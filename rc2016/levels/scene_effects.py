"""Live scene buffers and viewer-allocated key shadows; native selection is unproven."""
import ctypes as C
AO_UNIT, SHADOW_UNIT = 16, 17
SCENE_UNIFORMS = ('uSceneEnabled','uSceneAOEnabled','uSceneShadowEnabled','uSceneAO','uSceneShadow',
                  'uSceneSize','uSceneShadowSize','uSceneShadowMatrix','uSceneLightCount','uSceneLightPos','uSceneLightColour')
HELPERS = '''
uniform bool uSceneEnabled,uSceneAOEnabled,uSceneShadowEnabled;
uniform sampler2D uSceneAO; uniform sampler2DShadow uSceneShadow;
uniform vec2 uSceneSize,uSceneShadowSize; uniform mat4 uSceneShadowMatrix;
uniform int uSceneLightCount; uniform vec4 uSceneLightPos[32]; uniform vec3 uSceneLightColour[32];
float sceneAO(){return uSceneEnabled&&uSceneAOEnabled?texture2D(uSceneAO,gl_FragCoord.xy/uSceneSize).r:1.0;}
// Fragment540 nine weighted linear Less comparisons. Allocation is viewer defined.
float sceneShadow(vec3 p){
 if(!uSceneEnabled||!uSceneShadowEnabled)return 1.0;
 vec4 h=uSceneShadowMatrix*vec4(p,1.0);vec3 q=h.xyz/h.w;
 if(any(lessThan(q,vec3(0.0)))||any(greaterThan(q,vec3(1.0))))return 1.0;
 vec2 st=q.xy*uSceneShadowSize+0.5,f=fract(st),base=(floor(st)-0.5)/uSceneShadowSize;
 vec2 lo=4.0-3.0*f,hi=1.0+3.0*f;
 vec2 a=(3.0-2.0*f)/lo-2.0,b=(3.0+f)/7.0,c=f/hi+2.0;
 vec3 wx=vec3(lo.x,7.0,hi.x),wy=vec3(lo.y,7.0,hi.y);
 vec3 dx=vec3(a.x,b.x,c.x)/uSceneShadowSize.x,dy=vec3(a.y,b.y,c.y)/uSceneShadowSize.y;
 float total=0.0;for(int y=0;y<3;y++)for(int x=0;x<3;x++)total+=wx[x]*wy[y]*shadow2D(uSceneShadow,vec3(base+vec2(dx[x],dy[y]),q.z)).r;
 return total*0.0069444444961845874786376953125;
}
float sceneSpecularOcclusion(vec3 N,vec3 V,float a){
 float A=sceneAO(),nv=clamp(dot(N,V),0.0,1.0),t=nv*nv+(1.0-nv*nv)*A*A*A;return mix(t,A,a);
}
'''

def shadow_matrices(bounds,direction):
    import numpy as np
    low,high=np.asarray(bounds,dtype=float);centre=(low+high)*.5;extent=max(float(np.linalg.norm(high-low)),1.)
    L=np.asarray(direction,dtype=float);L/=np.linalg.norm(L);eye=centre+L*extent;f=-L
    up=np.array([0.,1.,0.]) if abs(f[1])<.95 else np.array([1.,0.,0.])
    s=np.cross(f,up);s/=np.linalg.norm(s);u=np.cross(s,f)
    view=np.eye(4);view[:3,:3]=np.stack((s,u,-f));view[:3,3]=-view[:3,:3]@eye
    corners=np.array([[x,y,z,1.] for x in (low[0],high[0]) for y in (low[1],high[1]) for z in (low[2],high[2])]);v=corners@view.T
    lo=v.min(axis=0);hi=v.max(axis=0);hi[:3]=np.maximum(hi[:3],lo[:3]+1.)
    proj=np.eye(4);proj[0,0]=2/(hi[0]-lo[0]);proj[1,1]=2/(hi[1]-lo[1]);proj[2,2]=-2/(hi[2]-lo[2])
    proj[:3,3]=(-(hi[0]+lo[0])/(hi[0]-lo[0]),-(hi[1]+lo[1])/(hi[1]-lo[1]),(hi[2]+lo[2])/(hi[2]-lo[2]))
    bias=np.eye(4)*.5;bias[3,3]=1.;bias[:3,3]=.5
    return view,proj,bias@proj@view

class SceneEffects:
    def __init__(self,gl,shading):
        self.gl,self.sh=gl,shading;self.size=None;self.fbos=[];self.textures=[];self.ao=None;self.ao_texture=0
        self.shadow_size=2048;self.shadow_key=None;self.shadow_matrix=None;self.view_shadow_matrix=None
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
        self.shadow_depth=self._texture(self.shadow_size,self.shadow_size,0x81A5,0x1902,0x1403,True)
        self.shadow_fbo=self._fbo();self.attach(0x8D40,0x8D00,0x0DE1,self.shadow_depth,0);self.gl.glDrawBuffer(0);self.gl.glReadBuffer(0)
        if self.check(0x8D40)!=0x8CD5:raise RuntimeError('Scene shadow framebuffer incomplete')
        self.bind(0x8D40,0);self.sh.glActiveTexture(0x84C0)
    def prepare(self,view,draw_geometry):
        import numpy as np
        gl=self.gl;w,h=max(1,view.winfo_width()),max(1,view.winfo_height());self.resize(w,h)
        projection=(C.c_float*16)();modelview=(C.c_float*16)();gl.glGetFloatv(0x0BA7,projection);gl.glGetFloatv(0x0BA6,modelview)
        vm=np.array(modelview,dtype=float).reshape(4,4).T;gl.glPushAttrib(0x00004000|0x00000100|0x00000800|0x00002000)
        try:
            if view.scene_shadows:
                if view.lighting=='Shared HDRI' and view._environment:
                    p=view._environment['sun_direction'];direction=(p[0],p[2],-p[1])
                else:
                    from rc2016.viewer.viewport import RIG_TO_WORLD
                    direction=np.linalg.solve(np.array(view.OBJECT_TO_WORLD),np.array(RIG_TO_WORLD)@np.array([-.45,.65,.62]))
                bounds=view.scene_bounds();key=(bounds,tuple(direction),view._scene_revision)
                if key!=self.shadow_key:
                    sv,sp,self.shadow_matrix=shadow_matrices(bounds,direction);self.bind(0x8D40,self.shadow_fbo)
                    gl.glViewport(0,0,self.shadow_size,self.shadow_size);gl.glClear(0x00000100)
                    gl.glMatrixMode(0x1701);gl.glLoadMatrixf((C.c_float*16)(*sp.T.flat));gl.glMatrixMode(0x1700);gl.glLoadMatrixf((C.c_float*16)(*sv.T.flat))
                    draw_geometry(shadow=True);self.shadow_key=key
            gl.glMatrixMode(0x1701);gl.glLoadMatrixf(projection);gl.glMatrixMode(0x1700);gl.glLoadMatrixf(modelview)
            if view.scene_ao:
                self.bind(0x8D40,self.geometry_fbo);self.draw_buffers(2,(C.c_uint*2)(0x8CE0,0x8CE1))
                gl.glViewport(0,0,w,h);gl.glClearColor(0.,0.,0.,0.);gl.glClear(0x00004000|0x00000100);draw_geometry(shadow=False);self.bind(0x8D40,0)
                if self.ao is None:
                    from rc2016.levels.scene_ao import SceneAO
                    self.ao=SceneAO(gl,self.sh)
                self.ao_texture=self.ao.render(self.depth,self.normal,w,h,list(projection))
            self.view_shadow_matrix=None if self.shadow_matrix is None else self.shadow_matrix@np.linalg.inv(vm)
        finally:
            self.bind(0x8D40,0);gl.glPopAttrib();gl.glMatrixMode(0x1701);gl.glLoadMatrixf(projection);gl.glMatrixMode(0x1700);gl.glLoadMatrixf(modelview);self.sh.glActiveTexture(0x84C0)
    def apply(self,view,u):
        sh,gl=self.sh,self.gl;ready=not view._scene_geometry and view.scene_effects_active()
        sh.glUniform1i(u['uSceneEnabled'],int(ready));sh.glUniform1i(u.get('uSceneGeometry',-1),int(view._scene_geometry))
        sh.glUniform1i(u['uSceneAOEnabled'],int(ready and view.scene_ao and bool(self.ao_texture)))
        sh.glUniform1i(u['uSceneShadowEnabled'],int(ready and view.scene_shadows and self.view_shadow_matrix is not None))
        sh.glUniform1i(u['uSceneAO'],AO_UNIT);sh.glUniform1i(u['uSceneShadow'],SHADOW_UNIT)
        self.uniform2(u['uSceneSize'],*self.size);self.uniform2(u['uSceneShadowSize'],self.shadow_size,self.shadow_size)
        for unit,texture in ((AO_UNIT,self.ao_texture),(SHADOW_UNIT,self.shadow_depth)):
            sh.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x0DE1,texture)
        if self.view_shadow_matrix is not None:self.uniform_matrix(u['uSceneShadowMatrix'],1,0,(C.c_float*16)(*self.view_shadow_matrix.T.flat))
        sh.glActiveTexture(0x84C0)
    def close(self):
        if self.ao:self.ao.close();self.ao=None
        if self.textures:self.gl.glDeleteTextures(len(self.textures),(C.c_uint*len(self.textures))(*self.textures));self.textures=[]
        if self.fbos:self.delete(len(self.fbos),(C.c_uint*len(self.fbos))(*self.fbos));self.fbos=[]
        self.size=None;self.shadow_key=None;self.ao_texture=0;self.shadow_matrix=None;self.view_shadow_matrix=None
