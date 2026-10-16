"""Emit portable bound shader IR, then verify actual persisted node topology."""
import hashlib
import json


STATIC_VECTOR_ATTRIBUTE = 'RC static packed vector'


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def tree_signature(tree):
    """Includes operations, constants, image interpretation, links and groups."""
    rows=[]
    for n in sorted(tree.nodes,key=lambda n:n.name):
        row=dict(name=n.name,type=n.bl_idname,operation=getattr(n,'operation',None),
            uv_map=getattr(n,'uv_map',None),inputs=[])
        for s in n.inputs:
            value=getattr(s,'default_value',None)
            if value is not None:
                try:value=list(value)
                except TypeError:pass
            row['inputs'].append(dict(identifier=s.identifier,value=value))
        if n.type=='VALUE':row['value']=n.outputs[0].default_value
        if n.type=='TEX_IMAGE':
            row['image']=dict(path=n.image.get('source_texture_path'),sha=n.image.get('source_texture_sha256'),
                colorspace=n.image.colorspace_settings.name,alpha=n.image.alpha_mode)
            row.update(interpolation=n.interpolation,extension=n.extension)
        if n.type=='ATTRIBUTE':row['attribute_name']=n.attribute_name
        if n.type=='NORMAL_MAP':row.update(space=n.space,uv_map=n.uv_map)
        if n.type=='GROUP':row['group']=tree_signature(n.node_tree)
        rows.append(row)
    links=sorted((l.from_node.name,l.from_socket.identifier,l.to_node.name,l.to_socket.identifier) for l in tree.links)
    return _digest(dict(nodes=rows,links=links))


def _frontend(translation,images):
    import bpy
    g=bpy.data.node_groups.new('RC PS4 translated '+translation['translation_sha256'][:16],'ShaderNodeTree')
    for name,kind in [('Albedo','NodeSocketColor'),('F0','NodeSocketColor'),('Gloss','NodeSocketFloat'),
                      ('Emission multiplier','NodeSocketFloat'),('Normal','NodeSocketVector')]:
        g.interface.new_socket(name=name,in_out='OUTPUT',socket_type=kind)
    nodes=g.nodes;links=g.links;out=nodes.new('NodeGroupOutput');out.name='Translated outputs'
    uv=nodes.new('ShaderNodeUVMap');uv.uv_map='UV0';uv.name='Source UV0'
    xy=nodes.new('ShaderNodeSeparateXYZ');links.new(uv.outputs[0],xy.inputs[0])
    # UV0 is imported with V flipped (v_blender = 1 - v_game). Native UV arithmetic
    # runs in game space and each sample coordinate is flipped back, so offsets,
    # non-integer tiling, constant coordinates and UV masks stay exact.
    def flip_v(socket):
        n=nodes.new('ShaderNodeMath');n.operation='SUBTRACT';n.inputs[0].default_value=1
        links.new(socket,n.inputs[1]);return n.outputs[0]
    game_uv=[xy.outputs[0],flip_v(xy.outputs[1])]
    preview_inputs = {}
    if translation.get('status') in ('experimental', 'partial_source_rule'):
        # Geometry is Blender world Z-up; the explicit preview adapter uses the
        # viewer's native Y-up basis (x,z,-y). This does not bind engine CB0.
        geometry = nodes.new('ShaderNodeNewGeometry');geometry.name='Experimental viewer geometry adapter'
        for kind, socket in (('preview_position','Position'), ('preview_normal','Normal')):
            separate = nodes.new('ShaderNodeSeparateXYZ');links.new(geometry.outputs[socket],separate.inputs[0])
            native = [separate.outputs[0],separate.outputs[2],separate.outputs[1]]
            negate = nodes.new('ShaderNodeMath');negate.operation='MULTIPLY';negate.inputs[1].default_value=-1
            links.new(native[2],negate.inputs[0]);native[2]=negate.outputs[0]
            for channel, value in enumerate(native):preview_inputs[(kind,channel)] = value
        # Explicit diagnostic POINT/FLOAT_COLOR attribute: (masked R^2, masked G^2, key flag, 1).
        # Missing attributes return zero, retaining the uniform review fallback.
        if any(n.get('kind') == 'preview_ground_blend' for n in translation['nodes']):
            paint=nodes.new('ShaderNodeAttribute');paint.attribute_name='RC zone ground blend'
            split=nodes.new('ShaderNodeSeparateColor');split.mode='RGB';links.new(paint.outputs['Color'],split.inputs[0])
            for channel in (0,1,2):preview_inputs[('preview_ground_blend',channel)]=split.outputs[channel]
        if any(n.get('kind') == 'preview_vertex_alpha_squared' for n in translation['nodes']):
            # Explicit POINT/FLOAT_COLOR diagnostic: masked alpha^2 in R, presence in G.
            # Colour output of a missing attribute is zero, so fallback is white 1.
            alpha=nodes.new('ShaderNodeAttribute');alpha.attribute_name='RC zone alpha squared'
            split=nodes.new('ShaderNodeSeparateColor');split.mode='RGB';links.new(alpha.outputs['Color'],split.inputs[0])
            missing=nodes.new('ShaderNodeMath');missing.operation='SUBTRACT';missing.inputs[0].default_value=1
            links.new(split.outputs[1],missing.inputs[1])
            value=nodes.new('ShaderNodeMath');value.operation='ADD'
            links.new(split.outputs[0],value.inputs[0]);links.new(missing.outputs[0],value.inputs[1])
            preview_inputs[('preview_vertex_alpha_squared',None)]=value.outputs[0]
        if any(n.get('kind') == 'preview_static_vector' for n in translation['nodes']):
            # This is a caller-provided POINT/FLOAT_VECTOR mesh attribute in
            # the source's native Y-up basis. Read xyz directly: it is not a
            # geometry-normal alias, and must not be normalized or remapped.
            packed=nodes.new('ShaderNodeAttribute');packed.name='Required native static vector attribute'
            packed.attribute_name=STATIC_VECTOR_ATTRIBUTE
            split=nodes.new('ShaderNodeSeparateXYZ');links.new(packed.outputs['Vector'],split.inputs[0])
            for channel in (0,1,2):preview_inputs[('preview_static_vector',channel)]=split.outputs[channel]
        front = nodes.new('ShaderNodeMath');front.operation='SUBTRACT';front.inputs[0].default_value=1
        links.new(geometry.outputs['Backfacing'],front.inputs[1]);preview_inputs[('preview_front_face',None)] = front.outputs[0]
    cache={};samples={};bindings={r['api']:r for r in translation['texture_bindings']}
    simple={'add':'ADD','sub':'SUBTRACT','mul':'MULTIPLY','div':'DIVIDE','abs':'ABSOLUTE',
        'min':'MINIMUM','max':'MAXIMUM','sqrt':'SQRT','sin':'SINE','cos':'COSINE',
        'pow':'POWER','lt':'LESS_THAN','gt':'GREATER_THAN',
        'floor':'FLOOR','fract':'FRACT'}
    def math_node(operation,args,extra=()):
        n=nodes.new('ShaderNodeMath');n.operation=operation
        for j,s in enumerate(args):links.new(s,n.inputs[j])
        for j,v in extra:n.inputs[j].default_value=v
        return n.outputs[0]
    def emit(i):
        if i in cache:return cache[i]
        n=translation['nodes'][i];op=n['op'];args=n['args']
        if op=='const':
            node=nodes.new('ShaderNodeValue');node.outputs[0].default_value=n['value'];socket=node.outputs[0]
        elif op=='input':
            if n.get('kind') == 'attribute' and n.get('attribute') == 0 and n.get('channel') in (0,1):
                socket=game_uv[n['channel']]
            elif n.get('kind') == 'front_facing':
                if 'front_facing' not in cache:
                    geometry=nodes.new('ShaderNodeNewGeometry');geometry.name='Native front facing'
                    front=nodes.new('ShaderNodeMath');front.operation='SUBTRACT';front.inputs[0].default_value=1
                    links.new(geometry.outputs['Backfacing'],front.inputs[1]);cache['front_facing']=front.outputs[0]
                socket=cache['front_facing']
            elif translation.get('status') in ('experimental', 'partial_source_rule') and (n.get('kind'),n.get('channel')) in preview_inputs:
                socket=preview_inputs[(n['kind'],n.get('channel'))]
            else:raise ValueError('Unbound Blender input '+n.get('name',''))
        elif op=='texture':
            key=(n['sample'],n['api'],tuple(args))
            if key not in samples:
                image=nodes.new('ShaderNodeTexImage');binding=bindings[n['api']]
                image.image=images[binding['path']];image.name=f"API{n['api']} sample{n['sample']}"
                image.interpolation='Linear';image.extension='REPEAT'
                image['source_texture_path']=binding['path'];image['source_texture_sha256']=image.image.get('source_texture_sha256')
                image['source_texture_interpretation']=image.image.get('source_texture_interpretation','')
                coords=nodes.new('ShaderNodeCombineXYZ')
                for j,a in enumerate(args):links.new(emit(a) if j!=1 else flip_v(emit(a)),coords.inputs[j])
                links.new(coords.outputs[0],image.inputs['Vector'])
                split=nodes.new('ShaderNodeSeparateColor');split.mode='RGB';links.new(image.outputs['Color'],split.inputs[0])
                samples[key]=[split.outputs[j] for j in range(3)]+[image.outputs['Alpha']]
            socket=samples[key][n['channel']]
        else:
            a=[emit(j) for j in args]
            if op in simple:socket=math_node(simple[op],a)
            elif op=='neg':socket=math_node('MULTIPLY',a,[(1,-1)])
            elif op=='rcp':
                socket=math_node('POWER',a,[(1,-1)])
            elif op=='rsqrt':socket=math_node('POWER',a,[(1,-.5)])
            elif op=='log2':socket=math_node('LOGARITHM',a,[(1,2)])
            elif op=='exp2':
                node=nodes.new('ShaderNodeMath');node.operation='POWER';node.inputs[0].default_value=2
                links.new(a[0],node.inputs[1]);socket=node.outputs[0]
            elif op=='sat':socket=math_node('MINIMUM',[math_node('MAXIMUM',a,[(1,0)])],[(1,1)])
            elif op in ('ge','le'):
                cmp=math_node('LESS_THAN' if op=='ge' else 'GREATER_THAN',a)
                socket=math_node('MULTIPLY',[cmp],[(1,-1)])
                socket=math_node('ADD',[socket],[(1,1)])
            elif op in ('eq','ne'):
                diff=math_node('SUBTRACT',a);absolute=math_node('ABSOLUTE',[diff])
                # Comparisons operate on scalar floats: exact equality, no epsilon.
                unequal=math_node('GREATER_THAN',[absolute],[(1,0)])
                socket=unequal if op=='ne' else math_node('ADD',[math_node('MULTIPLY',[unequal],[(1,-1)])],[(1,1)])
            elif op=='select':
                # Shader predicates are 0/1; arithmetic select retains both branches.
                diff=math_node('SUBTRACT',[a[1],a[2]])
                socket=math_node('ADD',[math_node('MULTIPLY',[a[0],diff]),a[2]])
            else:raise ValueError('Unsupported emitter operation '+op)
        cache[i]=socket;return socket
    def color(name,roots):
        n=nodes.new('ShaderNodeCombineColor');n.mode='RGB';n.name=name
        for j,i in enumerate(roots):links.new(emit(i),n.inputs[j])
        return n.outputs[0]
    outputs=translation['outputs']
    links.new(color('Native albedo',outputs['albedo']),out.inputs['Albedo'])
    links.new(color('Native specular F0',outputs['F0']),out.inputs['F0'])
    links.new(emit(outputs['gloss']),out.inputs['Gloss'])
    links.new(emit(outputs['emission']),out.inputs['Emission multiplier'])
    normal=translation['normal']
    if normal:
        xyz=nodes.new('ShaderNodeCombineXYZ')
        for j,i in enumerate(normal['xyz']):links.new(emit(i),xyz.inputs[j])
        norm=nodes.new('ShaderNodeVectorMath');norm.operation='NORMALIZE';links.new(xyz.outputs[0],norm.inputs[0])
        scale=nodes.new('ShaderNodeVectorMath');scale.operation='SCALE';scale.inputs[3].default_value=.5
        links.new(norm.outputs[0],scale.inputs[0])
        offset=nodes.new('ShaderNodeVectorMath');offset.operation='ADD';offset.inputs[1].default_value=(.5,.5,.5)
        links.new(scale.outputs[0],offset.inputs[0])
        nm=nodes.new('ShaderNodeNormalMap');nm.uv_map='UV0';nm.inputs['Strength'].default_value=1
        links.new(offset.outputs[0],nm.inputs['Color']);links.new(nm.outputs[0],out.inputs['Normal'])
    else:
        geometry=nodes.new('ShaderNodeNewGeometry');links.new(geometry.outputs['Normal'],out.inputs['Normal'])
    g['translation_sha256']=translation['translation_sha256'];g['code_sha256']=translation['code_sha256']
    return g


def apply_translated_material(material,source, *, experimental=False):
    from rc2016.pipeline import game_shader
    translation=source.get('shader_experimental_preview' if experimental else 'shader_translation',{})
    if experimental:
        from rc2016.viewer.native_glsl import emit_surface
        emit_surface(translation,experimental=True)  # same explicit input/contract gate
    elif translation.get('status')!='ready':return False
    # Hand-audited profiles and more complete skin/forward frontends remain intact.
    if source.get('validated_profile') or any(p.get('family') in ('skin_emissive','basic_skin','character_glass')
        for p in source.get('shader_preflight',[])):return False
    images={n.get('source_texture_path'):n.image for n in material.node_tree.nodes if n.type=='TEX_IMAGE' and n.image}
    if any(r['path'] not in images for r in translation['texture_bindings']):
        raise ValueError('Translated shader lacks exact source texture image')
    backup=material.copy();backup.name=material.name+' | pre-RC-game-shader';backup.use_fake_user=True
    g=_frontend(translation,images)
    nodes=material.node_tree.nodes;links=material.node_tree.links
    for n in list(nodes):
        if n.type!='TEX_IMAGE':nodes.remove(n)
    front=nodes.new('ShaderNodeGroup');front.node_tree=g;front.name='Experimental material preview' if experimental else 'Native translated material'
    brdf=nodes.new('ShaderNodeGroup');brdf.node_tree=game_shader.build_group();brdf.name='RC PS4 game shader'
    for name,index in [('Albedo',0),('Emission multiplier',1),('F0',2),('Gloss',3),('Normal',4)]:
        links.new(front.outputs[name],brdf.inputs[index])
    for index in (5,6,7):brdf.inputs[index].default_value=1
    out=nodes.new('ShaderNodeOutputMaterial');links.new(brdf.outputs['Shader'],out.inputs['Surface'])
    material['rc_shader_translation']=json.dumps(translation,sort_keys=True)
    material['rc_game_shader']=True;material['game_equivalence']=False
    material['rc_shader']='RC PS4 Deferred BRDF with experimental frontend' if experimental else 'RC PS4 Deferred BRDF with native translated frontend'
    material['shader_preview_status']='experimental_material_preview' if experimental else 'native_translated_deferred_frontend'
    material['rc_translation_tree_sha256']=tree_signature(material.node_tree)
    return True


def validate_translated_material(source,material, *, experimental=False):
    expected=source['shader_experimental_preview' if experimental else 'shader_translation']
    from rc2016.shaders.bindings import validate_source_preview_inputs
    validate_source_preview_inputs(expected)
    stored=json.loads(material.get('rc_shader_translation','{}'))
    assert stored==expected,'Persisted native translation receipt changed'
    signature=tree_signature(material.node_tree)
    assert signature==material.get('rc_translation_tree_sha256'),'Translated node operations, constants or links changed'
    front=material.node_tree.nodes.get('Experimental material preview' if experimental else 'Native translated material')
    assert front and front.node_tree['translation_sha256']==expected['translation_sha256']
    static_vector_receipt=None
    if (experimental and expected.get('status') == 'experimental' and
            any(n.get('op') == 'input' and n.get('kind') == 'preview_static_vector'
                for n in expected.get('nodes', []))):
        static_vector_receipt=_validate_static_vector_mesh_attribute(material)
        attrs=[n for n in front.node_tree.nodes if n.type=='ATTRIBUTE' and
               n.attribute_name==STATIC_VECTOR_ATTRIBUTE]
        assert len(attrs)==1 and attrs[0].outputs.get('Vector'), \
            'Static-vector preview must read the dedicated mesh Vector attribute directly'
    actual=[n for n in front.node_tree.nodes if n.type=='TEX_IMAGE']
    for binding in expected['texture_bindings']:
        textures=[b for b in source['textures'] if b['path']==binding['path']]
        assert len(textures)==1,'Ambiguous source image interpretation'
        texture=textures[0]
        matches=[n for n in actual if n.get('source_texture_path')==binding['path']]
        assert matches,'Native API source image missing'
        for n in matches:
            assert n.image.get('source_texture_sha256')==texture['dds_sha256']
            assert n.image.colorspace_settings.name==texture['colorspace']
            assert n.image.alpha_mode=='CHANNEL_PACKED'
    result=dict(status='passed_experimental_preview' if experimental else 'passed_native_translation',code_sha256=expected['code_sha256'],
        translation_sha256=expected['translation_sha256'],node_count=len(front.node_tree.nodes),
        limitations=expected['limitations'],game_equivalence=False)
    if static_vector_receipt is not None:
        result['static_vector_attribute']=static_vector_receipt
    return result


def _validate_static_vector_mesh_attribute(material):
    """Require the explicit native vector input on every mesh using this material."""
    try:
        import bpy
    except ImportError as exc:
        raise AssertionError('Static-vector preview validation requires Blender mesh users') from exc
    users=[]
    for obj in bpy.data.objects:
        if obj.type!='MESH' or obj.data is None:
            continue
        if not any(slot.material is material for slot in obj.material_slots):
            continue
        attr=obj.data.attributes.get(STATIC_VECTOR_ATTRIBUTE)
        assert attr is not None, f'{obj.name} lacks required {STATIC_VECTOR_ATTRIBUTE!r} mesh attribute'
        assert attr.domain=='POINT' and attr.data_type=='FLOAT_VECTOR', \
            f'{obj.name} {STATIC_VECTOR_ATTRIBUTE!r} must be POINT/FLOAT_VECTOR'
        assert len(attr.data)==len(obj.data.vertices), \
            f'{obj.name} {STATIC_VECTOR_ATTRIBUTE!r} length differs from mesh vertices'
        users.append(obj)
    assert users, 'No mesh object uses the material with its required static-vector attribute'
    return dict(name=STATIC_VECTOR_ATTRIBUTE,domain='POINT',data_type='FLOAT_VECTOR',
                mesh_user_count=len(users),vertex_counts=[len(obj.data.vertices) for obj in users],
                vector_basis='caller-provided native Y-up xyz, passed through unchanged',
                source_binding_proven=False,game_equivalence=False)
