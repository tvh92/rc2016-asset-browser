"""Group the objects Blender imports from a combined or single-model .glb into one collection per model.

The glTF writer tags every node with the model it belongs to (``MODEL_TAG`` in extras, see
``rc2016.export.models.tag_model``); Blender's glTF importer keeps that as a custom property on each
object. Objects without a tag (an armature the importer makes from a skin, for instance) take the model
of the meshes they deform, or of their parent or child. ``assign_models`` is plain Python so it can be
tested outside Blender; ``group_by_model`` applies it to the live scene (Blender only).
"""
from __future__ import annotations

MODEL_TAG = 'rc2016_model'


def assign_models(records):
    """Model name for each object record, by object name. A record is a dict with ``name``, ``tag`` (the
    model tag or None), ``parent`` (name or None), ``children`` (names) and ``armatures`` (names of the
    armature objects this mesh is deformed by). Objects no rule reaches map to None."""
    by_name = {record['name']: record for record in records}
    model = {record['name']: record.get('tag') for record in records}
    for _ in range(4):   # each pass lets a model spread one step further through the hierarchy
        changed = False
        for record in records:
            name = record['name']
            if model[name] is not None:
                continue
            found = None
            if record.get('parent') in by_name:
                found = model[record['parent']]
            if found is None:
                for other in record.get('children', []) + record.get('armatures', []):
                    if other in by_name and model.get(other) is not None:
                        found = model[other]
                        break
            if found is None:
                # An armature is used by the meshes that deform with it.
                for other in records:
                    if name in other.get('armatures', []) and model.get(other['name']) is not None:
                        found = model[other['name']]
                        break
            if found is not None:
                model[name] = found
                changed = True
        if not changed:
            break
    return model


def group_by_model(scene):
    """Move every imported object that belongs to a model into a collection named after that model,
    under the scene collection. The importer's own collection is removed when it ends up empty."""
    import bpy

    objects = list(scene.objects)
    records = [dict(name=obj.name,
                    tag=obj.get(MODEL_TAG),
                    parent=obj.parent.name if obj.parent else None,
                    children=[child.name for child in obj.children],
                    armatures=[mod.object.name for mod in obj.modifiers if mod.type == 'ARMATURE' and mod.object])
               for obj in objects]
    owner = assign_models(records)
    before = set(bpy.data.collections)
    groups = {}
    for obj in objects:
        if owner.get(obj.name):
            groups.setdefault(owner[obj.name], []).append(obj)
    created = []
    for model in sorted(groups):
        collection = bpy.data.collections.new(model)
        scene.collection.children.link(collection)
        created.append(collection)
        for obj in groups[model]:
            for holder in list(obj.users_collection):
                holder.objects.unlink(obj)
            collection.objects.link(obj)
    for collection in list(bpy.data.collections):
        if collection in before and not collection.objects and not collection.children:
            bpy.data.collections.remove(collection)
    return [collection.name for collection in created]
