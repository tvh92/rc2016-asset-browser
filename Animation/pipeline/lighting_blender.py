"""Validate PS4 cubemap faces and install a compact Novalis lat-long environment in Blender."""
from itertools import product
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

from .common import file_hash


def _face_direction(face,u,v):
    return ((1,-v,-u),(-1,-v,u),(u,1,v),(u,-1,-v),(u,-v,1),(-u,-v,-1))[face]


def _direction_face_uv(direction,exclude=None):
    x,y,z=direction;values=(abs(x),abs(y),abs(z));axes=sorted(range(3),key=lambda i:values[i],reverse=True)
    axis=next(i for i in axes if exclude is None or i!=exclude)
    face={(0,True):0,(0,False):1,(1,True):2,(1,False):3,(2,True):4,(2,False):5}[(axis,direction[axis]>0)]
    scale=values[axis]
    if face==0:return face,-z/scale,-y/scale
    if face==1:return face,z/scale,-y/scale
    if face==2:return face,x/scale,z/scale
    if face==3:return face,x/scale,-z/scale
    if face==4:return face,x/scale,-y/scale
    return face,-x/scale,-y/scale


def _seams(size):
    result=[];t=np.linspace(-1+1/size,1-1/size,size)
    for face in range(6):
        axis=face//2
        for edge in range(4):
            u=np.full(size,-1 if edge==0 else 1) if edge<2 else t
            v=t if edge<2 else np.full(size,-1 if edge==2 else 1)
            mapped=[_direction_face_uv(_face_direction(face,a,b),exclude=axis) for a,b in zip(u,v)]
            other=mapped[0][0]
            if face>other:continue
            if not all(x[0]==other for x in mapped):raise AssertionError('Cubemap edge changed target face')
            row=np.rint((v+1)*.5*(size-1)).astype(int);col=np.rint((u+1)*.5*(size-1)).astype(int)
            other_col=np.rint((np.asarray([x[1] for x in mapped])+1)*.5*(size-1)).astype(int)
            other_row=np.rint((np.asarray([x[2] for x in mapped])+1)*.5*(size-1)).astype(int)
            result.append((face,row,col,other,other_row,other_col))
    if len(result)!=12:raise AssertionError('Cubemap must have twelve unique seams')
    return result


def _load_faces(descriptor):
    result=[]
    for expected,entry in enumerate(descriptor['faces']):
        path=Path(entry['path'])
        if entry['index']!=expected or file_hash(path)!=entry['sha256']:raise ValueError('Game-lighting face identity changed')
        image=bpy.data.images.load(str(path),check_existing=False)
        if tuple(image.size)!=(descriptor['width'],descriptor['height']):raise ValueError('Game-lighting face dimensions changed')
        pixels=np.empty(len(image.pixels),dtype=np.float32);image.pixels.foreach_get(pixels)
        result.append(pixels.reshape(image.size[1],image.size[0],4)[:,:,:3]);bpy.data.images.remove(image)
    return result


def _orient_faces(raw):
    variants=[]
    for face in raw:
        states=[np.rot90(face,k) for k in range(4)];mirrored=np.fliplr(face);states.extend(np.rot90(mirrored,k) for k in range(4))
        variants.append(states)
    logged=[[np.log1p(np.maximum(x,0)) for x in row] for row in variants];edges=_seams(raw[0].shape[0]);costs={}
    for face,row,col,other,orow,ocol in edges:
        costs[(face,other)]=np.asarray([[np.mean(np.abs(logged[face][a][row,col]-logged[other][b][orow,ocol]))
            for b in range(8)] for a in range(8)])
    best=None
    for states in product(range(8),repeat=6):
        cost=sum(costs[(face,other)][states[face],states[other]] for face,_,_,other,_,_ in edges)
        candidate=(float(cost),states)
        if best is None or candidate<best:best=candidate
    _,states=best;oriented=[variants[i][state] for i,state in enumerate(states)];seam_values=[];inner_values=[]
    oriented_log=[logged[i][state] for i,state in enumerate(states)]
    for face,row,col,other,orow,ocol in edges:
        seam_values.append(np.abs(oriented_log[face][row,col]-oriented_log[other][orow,ocol]))
    for face in oriented_log:
        inner_values.extend([np.abs(face[:,0]-face[:,1]),np.abs(face[:,-1]-face[:,-2]),np.abs(face[0]-face[1]),np.abs(face[-1]-face[-2])])
    seam=float(np.mean(seam_values));interior=float(np.mean(inner_values));ratio=seam/max(interior,1e-12)
    if ratio>2.0:raise ValueError('Novalis cubemap edge continuity did not validate: '+str(ratio))
    return oriented,dict(d4_states=list(states),seam_log_mae=seam,interior_edge_gradient_log_mae=interior,seam_to_interior_ratio=ratio)


def _latlong(faces,width=1024,height=512):
    x=(np.arange(width)+.5)/width*2*np.pi-np.pi;y=(np.arange(height)+.5)/height*np.pi-np.pi/2
    longitude,latitude=np.meshgrid(x,y);coslat=np.cos(latitude)
    blender=np.stack((coslat*np.cos(longitude),coslat*np.sin(longitude),np.sin(latitude)),axis=-1)
    # Native model registration: game X,Y,Z maps to Blender X,-Z,Y.
    direction=np.stack((blender[:,:,0],blender[:,:,2],-blender[:,:,1]),axis=-1)
    absolute=np.abs(direction);axis=np.argmax(absolute,axis=-1);result=np.zeros((height,width,3),dtype=np.float32)
    for face in range(6):
        face_axis=face//2;positive=face%2==0;mask=(axis==face_axis)&((direction[:,:,face_axis]>0)==positive)
        vectors=direction[mask];scale=np.abs(vectors[:,face_axis])
        if face==0:u,v=-vectors[:,2]/scale,-vectors[:,1]/scale
        elif face==1:u,v=vectors[:,2]/scale,-vectors[:,1]/scale
        elif face==2:u,v=vectors[:,0]/scale,vectors[:,2]/scale
        elif face==3:u,v=vectors[:,0]/scale,-vectors[:,2]/scale
        elif face==4:u,v=vectors[:,0]/scale,-vectors[:,1]/scale
        else:u,v=-vectors[:,0]/scale,-vectors[:,1]/scale
        col=np.clip(np.rint((u+1)*.5*(faces[face].shape[1]-1)).astype(int),0,faces[face].shape[1]-1)
        row=np.clip(np.rint((v+1)*.5*(faces[face].shape[0]-1)).astype(int),0,faces[face].shape[0]-1)
        result[mask]=faces[face][row,col]
    return result


def apply_game_lighting(scene,descriptor,exr_path):
    faces,evidence=_orient_faces(_load_faces(descriptor));rgb=_latlong(faces)
    rgba=np.ones((rgb.shape[0],rgb.shape[1],4),dtype=np.float32);rgba[:,:,:3]=rgb
    generated=bpy.data.images.new('Novalis game reflection environment',width=rgb.shape[1],height=rgb.shape[0],alpha=True,float_buffer=True)
    generated.colorspace_settings.name='Non-Color';generated.pixels.foreach_set(rgba.ravel());generated.file_format='OPEN_EXR';generated.filepath_raw=str(exr_path)
    generated.save();bpy.data.images.remove(generated)
    environment_image=bpy.data.images.load(str(exr_path),check_existing=False);environment_image.name='Novalis game reflection environment'
    environment_image.colorspace_settings.name='Non-Color';environment_image['source_probe_path']=descriptor['source_path']
    environment_image['source_probe_sha256']=descriptor['source_sha256'];environment_image['lighting_region']=descriptor['region'];environment_image.pack()
    world=scene.world or bpy.data.worlds.new('PS4 Novalis World');scene.world=world;world.use_nodes=True
    nodes=world.node_tree.nodes;links=world.node_tree.links
    environment=nodes.get('Novalis Game Reflection Probe') or nodes.new('ShaderNodeTexEnvironment')
    old=environment.image;environment.name='Novalis Game Reflection Probe';environment.label=descriptor['source_path'];environment.image=environment_image
    # The packed game probe is already scene-linear. Do not apply a Blender
    # display/exposure convention here: no runtime exposure scalar has been
    # recovered from the Novalis level data yet.
    background=nodes.get('Background') or nodes.new('ShaderNodeBackground');background.inputs['Strength'].default_value=1.0
    output=nodes.get('World Output') or nodes.new('ShaderNodeOutputWorld')
    for link in list(links):
        if (link.to_node==background and link.to_socket==background.inputs['Color']) or (link.to_node==output and link.to_socket==output.inputs['Surface']):links.remove(link)
    links.new(environment.outputs['Color'],background.inputs['Color']);links.new(background.outputs['Background'],output.inputs['Surface'])
    output.is_active_output=True
    if old and old.users==0:bpy.data.images.remove(old)
    sun_evidence=None
    if descriptor.get('atmosphere_sun'):
        source=descriptor['atmosphere_sun'];sun_rgb=[float(value) for value in source['surface_irradiance_rgb']]
        peak=max(sun_rgb);ray=Vector(source['ray_direction_blender_xyz']).normalized()
        light=bpy.data.lights.new('Novalis atmosphere Sun','SUN');light.color=tuple(value/peak for value in sun_rgb)
        light.energy=peak;light.angle=float(source['angular_radius_radians'])
        sun=bpy.data.objects.new('Novalis atmosphere Sun',light);scene.collection.objects.link(sun)
        # Blender Sun rays travel along local -Z.
        sun.rotation_euler=ray.to_track_quat('-Z','Y').to_euler()
        sun['lighting_role']='novalis_atmosphere_sun';sun['source_atmosphere']=source['source_path']
        sun['source_atmosphere_sha256']=source['source_sha256'];sun['source_rgb96']=sun_rgb
        sun['semantic_status']=source['status']
        scene['lighting_atmosphere_path']=source['source_path'];scene['lighting_sun_rgb96']=sun_rgb
        scene['lighting_sun_ray_blender_xyz']=list(ray);scene['lighting_sun_energy']=peak
        scene['lighting_sun_angle']=light.angle
        sun_evidence=dict(status=source['status'],source_path=source['source_path'],source_sha256=source['source_sha256'],
            ray_direction_blender_xyz=list(ray),surface_irradiance_rgb=sun_rgb,color=list(light.color),energy=light.energy,
            angular_radius_radians=light.angle,mapping=source['mapping'])
    scene['lighting_source']=('novalis_game_reflection_probe_plus_atmosphere_sun' if sun_evidence else 'novalis_game_reflection_probe');scene['lighting_region']=descriptor['region']
    scene['lighting_probe_path']=descriptor['source_path'];scene['lighting_probe_sha256']=descriptor['source_sha256']
    scene['lighting_world_strength']=1.0
    scene['lighting_orientation_evidence']=str(evidence)
    return dict(status=('NOVALIS_REFLECTION_PROBE_AND_ATMOSPHERE_SUN_CONNECTED' if sun_evidence else 'NOVALIS_REFLECTION_PROBE_CONNECTED'),environment_width=rgb.shape[1],environment_height=rgb.shape[0],
        environment_exr=str(exr_path),environment_exr_sha256=file_hash(exr_path),world_strength=1.0,
        radiometric_status='UNSCALED_GAME_LINEAR_PROBE_PLUS_DIRECT_RGB96_NO_EXPOSURE' if sun_evidence else 'UNSCALED_GAME_LINEAR_PROBE_RUNTIME_EXPOSURE_NOT_DECODED',
        atmosphere_sun=sun_evidence,**evidence)
