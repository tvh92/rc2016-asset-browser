"""Portable controls embedded verbatim in each library. Run once in Text Editor."""
import json
import bpy


def catalog():
    return json.loads(bpy.data.texts['Animation Catalog.json'].as_string())


def rig():
    return bpy.data.objects[catalog()['rig']]


def set_fps(fps):
    scene=bpy.context.scene
    scene.render.fps=max(1,round(fps));scene.render.fps_base=scene.render.fps/fps


def select_clip(index):
    data=catalog();entry=data['clips'][index];obj=rig();scene=bpy.context.scene
    for track in obj.animation_data.nla_tracks:track.mute=True
    action=bpy.data.actions[entry['action']]
    obj.animation_data.action=action;obj.animation_data.action_slot=action.slots[0]
    set_fps(entry['fps']);scene.frame_start=1;scene.frame_end=entry['frames'];scene.frame_set(1)
    scene['active_source_clip']=entry.get('selector_label',entry['name']);bpy.context.view_layer.update()


def play_all():
    data=catalog();obj=rig();scene=bpy.context.scene
    obj.animation_data.action=None
    for track in obj.animation_data.nla_tracks:track.mute=False
    set_fps(data['overview_fps']);scene.frame_start=1;scene.frame_end=data['timeline'][-1]['end_inclusive'] if data['timeline'] else 1
    scene.frame_set(1);scene['active_source_clip']='Sequential overview';bpy.context.view_layer.update()


def jump_to(index):
    play_all();bpy.context.scene.frame_set(catalog()['timeline'][index]['start'])


def restore_rest():
    obj=rig();scene=bpy.context.scene
    for track in obj.animation_data.nla_tracks:track.mute=True
    action=bpy.data.actions[catalog()['rest_action']]
    obj.animation_data.action=action;obj.animation_data.action_slot=action.slots[0]
    scene.frame_start=1;scene.frame_end=1;scene.frame_set(1)
    scene['active_source_clip']='Original source rest pose';bpy.context.view_layer.update()


class PS4ANIM_OT_control(bpy.types.Operator):
    bl_idname='ps4anim.control'
    bl_label='Animation control'
    operation:bpy.props.StringProperty()
    def execute(self,context):
        try:
            index=int(context.scene.ps4_clip_choice)
            if self.operation=='select':select_clip(index)
            elif self.operation=='all':play_all()
            elif self.operation=='jump':jump_to(index)
            elif self.operation=='rest':restore_rest()
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'FINISHED'}


class PS4ANIM_PT_controls(bpy.types.Panel):
    bl_label='PS4 source animations'
    bl_idname='PS4ANIM_PT_controls'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='Animations'
    def draw(self,context):
        layout=self.layout;scene=context.scene;data=catalog()
        if data['clips']:
            layout.prop(scene,'ps4_clip_choice',text='Clip')
            for operation,label in [('select','Load selected clip'),('all','Load all clips in order'),('jump','Jump to selected boundary')]:
                layout.operator('ps4anim.control',text=label).operation=operation
        layout.operator('ps4anim.control',text='Restore original rest pose').operation='rest'
        active=scene.get('active_source_clip','')
        if rig().animation_data.action is None:
            active=next((e.get('selector_label',e['name']) for e in data['timeline'] if e['start']<=scene.frame_current<e['end_exclusive']),'Overview')
        layout.label(text='Active: '+active)
        layout.prop(scene,'frame_current',text='Frame')
        layout.operator('screen.animation_play',text='Play / pause',icon='PLAY')
        layout.operator('render.render',text='Render current frame')
        layout.label(text='Visual inspection belongs to you.')


def register():
    global _choices
    for cls in (PS4ANIM_PT_controls,PS4ANIM_OT_control):
        old=getattr(bpy.types,cls.__name__,None)
        if old:bpy.utils.unregister_class(old)
    _choices=[(str(i),e.get('selector_label',e['name']),
        f"{e['kind']} | {e.get('experimental_reason','')}") for i,e in enumerate(catalog()['clips'])] or [('0','No supported clips','Rest pose only')]
    bpy.types.Scene.ps4_clip_choice=bpy.props.EnumProperty(items=_choices)
    bpy.utils.register_class(PS4ANIM_OT_control);bpy.utils.register_class(PS4ANIM_PT_controls)


if __name__=='__main__':register()
