"""Rift Apart materials in Blender: the game's deferred BRDF as a node group, and node trees emitted from surface recipes.

Runs inside Blender only (``bpy``). A recipe (``rc2016.games.rcra.surface.export_recipe``) is the material's translated
G-buffer writer reduced to float expressions of its surface terms; ``apply_recipe`` turns every expression node into a
Math node, so the material is the game's own arithmetic, not a fitted approximation.

Inputs the recipe reads, in Blender:
- ``TEXCOORD.xy`` (UV0) and ``TEXCOORD1.xy`` (UV1): the mesh's first and second UV maps, with game v = 1 - Blender v.
  Each texture lookup flips back (images are stored top row first and Blender reads them bottom row first).
- ``TEXCOORD2.xyz`` (world normal): Geometry Normal, un-flipped on back faces as the game's interpolant is.
- ``TEXCOORD3.xyz`` and ``.w`` (tangent and bitangent sign): Blender's UV0 tangent space (MikkTSpace). The bitangent
  follows Blender's +v, which is the game's -v, the orientation the viewer uses. The game's stored vertex tangent is not
  decoded yet, so this is labelled on every material.
- ``TEXCOORD4`` (vertex colour): the mesh's first colour attribute, else 1.
- ``SV_IsFrontFace``: not Geometry Backfacing.
- ``VIEW.xyz`` and ``CAMERA.xyz`` (forward recipes): the offset from the camera to the shaded point, -Incoming x View
  Distance, and the camera position, Position + Incoming x View Distance, both converted to game space.

Vectors convert between game space (Y-up) and Blender (Z-up, the glTF importer's turn): game (x, y, z) = Blender (x, z, -y).

BRDF group "RC RA Deferred BRDF", from the engine's class-0 deferred apply program (docs/rift-apart.md):
- **Specular:** GGX with Blender roughness r = 0.9725 - 0.7514 gloss (Blender alpha = r^2, the game's alpha). Metallic
  BSDF with F82 Fresnel and a white edge tint is Schlick with F90 = 1, as in the game. Cycles' Smith term is exact; the game
  uses the joint approximation.
- **Diffuse:** Lambert, plus the self-lit term albedo x E.
- **Light units:** the game's lights carry no 1/pi, so a Blender light's strength is pi x the game's colour, as for R&C 2016.
- **Occlusion:** the game applies it to ambient light only, which Cycles cannot separate. "Occlusion on diffuse" (default 0)
  multiplies the diffuse colour by it instead.

Blended materials (entry ``blend`` additive, blended or modulate) carry a forward recipe: the colour and alpha the game
blends, with every engine light source zero, and a background tint B where the program passes the scene behind it
through (colour = A + B x background). ``apply_recipe`` composes it with a Transparent BSDF by the mode, and for 'blended'
adds the G-buffer surface lit by Blender (its emission left to the forward colour, which already holds it):
- blended: Transparent(mix(white, B, alpha)) + alpha x (BRDF + Emission(colour)), i.e. mix(Transparent, body, alpha)
  when B = 0
- additive: Transparent(1 + alpha B) + Emission(colour, strength alpha)
- modulate: Transparent tinted by mix(white, colour, alpha)
"""
import hashlib
import json
from pathlib import Path

import bpy

BRDF_GROUP = 'RC RA Deferred BRDF'
GROUP_VERSION = 1
SURFACE_PREFIX = 'RC RA surface '
RECIPE_PROPERTY = 'rc_rcra_recipe_sha256'
FORWARD_PREFIX = 'RC RA forward '
FORWARD_PROPERTY = 'rc_rcra_forward_sha256'
BLEND_MODES = ('opaque', 'additive', 'blended', 'modulate')
MATH = {'add': 'ADD', 'mul': 'MULTIPLY', 'div': 'DIVIDE', 'min': 'MINIMUM', 'max': 'MAXIMUM', 'abs': 'ABSOLUTE',
        'sqrt': 'SQRT', 'rsq': 'INVERSE_SQRT', 'frc': 'FRACT', 'round_ne': 'ROUND', 'round_ni': 'FLOOR',
        'round_pi': 'CEIL', 'round_z': 'TRUNC', 'sin': 'SINE', 'cos': 'COSINE', 'lt': 'LESS_THAN'}


def recipe_sha256(recipe):
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# --------------------------------------------------------------------------------------------------- BRDF group

def _socket(tree, name, in_out, kind, default=None):
    socket = tree.interface.new_socket(name=name, in_out=in_out, socket_type=kind)
    if default is not None:
        socket.default_value = default
    return socket


def build_brdf_group():
    """The shared "RC RA Deferred BRDF" group (rebuilt when its version changes)."""
    group = bpy.data.node_groups.get(BRDF_GROUP)
    if group is not None and group.get('rc_version') == GROUP_VERSION:
        return group
    if group is None:
        group = bpy.data.node_groups.new(BRDF_GROUP, 'ShaderNodeTree')
    group.nodes.clear()
    group.interface.clear()
    group['rc_version'] = GROUP_VERSION
    _socket(group, 'Albedo', 'INPUT', 'NodeSocketColor', (0.5, 0.5, 0.5, 1.0))
    _socket(group, 'F0', 'INPUT', 'NodeSocketColor', (0.04, 0.04, 0.04, 1.0))
    _socket(group, 'Gloss', 'INPUT', 'NodeSocketFloat', 0.5)
    _socket(group, 'Normal', 'INPUT', 'NodeSocketVector')
    _socket(group, 'Occlusion', 'INPUT', 'NodeSocketFloat', 1.0)
    _socket(group, 'Emission', 'INPUT', 'NodeSocketFloat', 0.0)
    _socket(group, 'Occlusion on diffuse', 'INPUT', 'NodeSocketFloat', 0.0)
    _socket(group, 'Shader', 'OUTPUT', 'NodeSocketShader')
    nodes, links = group.nodes, group.links
    inp, out = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    rough = nodes.new('ShaderNodeMath')
    rough.operation = 'MULTIPLY_ADD'
    rough.name = 'Roughness 0.9725 - 0.7514 gloss'
    links.new(inp.outputs['Gloss'], rough.inputs[0])
    rough.inputs[1].default_value = -0.7514
    rough.inputs[2].default_value = 0.9725
    spec = nodes.new('ShaderNodeBsdfMetallic')
    spec.name = 'Specular GGX Schlick F90 1'
    spec.distribution = 'GGX'
    spec.fresnel_type = 'F82'
    links.new(inp.outputs['F0'], spec.inputs['Base Color'])
    spec.inputs['Edge Tint'].default_value = (1.0, 1.0, 1.0, 1.0)
    links.new(rough.outputs[0], spec.inputs['Roughness'])
    links.new(inp.outputs['Normal'], spec.inputs['Normal'])
    # Diffuse colour x (1 + f (occlusion - 1)), f = "Occlusion on diffuse".
    occ = nodes.new('ShaderNodeMath')
    occ.operation = 'SUBTRACT'
    links.new(inp.outputs['Occlusion'], occ.inputs[0])
    occ.inputs[1].default_value = 1.0
    scale = nodes.new('ShaderNodeMath')
    scale.operation = 'MULTIPLY_ADD'
    links.new(occ.outputs[0], scale.inputs[0])
    links.new(inp.outputs['Occlusion on diffuse'], scale.inputs[1])
    scale.inputs[2].default_value = 1.0
    tint = nodes.new('ShaderNodeVectorMath')
    tint.operation = 'SCALE'
    links.new(inp.outputs['Albedo'], tint.inputs[0])
    links.new(scale.outputs[0], tint.inputs['Scale'])
    diffuse = nodes.new('ShaderNodeBsdfDiffuse')
    diffuse.name = 'Diffuse Lambert'
    links.new(tint.outputs[0], diffuse.inputs['Color'])
    diffuse.inputs['Roughness'].default_value = 0.0
    links.new(inp.outputs['Normal'], diffuse.inputs['Normal'])
    emission = nodes.new('ShaderNodeEmission')
    emission.name = 'Self-lit albedo x E'
    links.new(inp.outputs['Albedo'], emission.inputs['Color'])
    links.new(inp.outputs['Emission'], emission.inputs['Strength'])
    add = nodes.new('ShaderNodeAddShader')
    links.new(diffuse.outputs[0], add.inputs[0])
    links.new(spec.outputs[0], add.inputs[1])
    total = nodes.new('ShaderNodeAddShader')
    links.new(add.outputs[0], total.inputs[0])
    links.new(emission.outputs[0], total.inputs[1])
    links.new(total.outputs[0], out.inputs['Shader'])
    for x, node in enumerate((inp, rough, spec, occ, scale, tint, diffuse, emission, add, total, out)):
        node.location = (x * 220, 0)
    return group


# ----------------------------------------------------------------------------------------------- surface emitter

class _Emitter:
    def __init__(self, tree, recipe, images, uv_maps, colour_attribute, interpolation='Linear', fixed=None):
        self.fixed = fixed or {}   # input name -> float, replacing the mesh/camera value (checks)
        self.tree, self.nodes, self.links = tree, tree.nodes, tree.links
        self.recipe, self.images = recipe, images
        self.uv_maps, self.colour_attribute = uv_maps, colour_attribute
        self.interpolation = interpolation
        self.memo, self.inputs, self.samples = {}, {}, {}
        self.count = 0

    def node(self, kind, **props):
        n = self.nodes.new(kind)
        for key, value in props.items():
            setattr(n, key, value)
        n.location = ((self.count // 40) * 200, -(self.count % 40) * 60)
        self.count += 1
        return n

    def math(self, operation, *args, clamp=False):
        n = self.node('ShaderNodeMath', operation=operation, use_clamp=clamp)
        for k, a in enumerate(args):
            self.feed(a, n.inputs[k])
        return n.outputs[0]

    def feed(self, value, socket):
        if isinstance(value, (int, float)):
            socket.default_value = float(value)
        else:
            self.links.new(value, socket)

    # A value is a float or an output socket. Constants stay floats so they become socket defaults.
    def value(self, index):
        if index in self.memo:
            return self.memo[index]
        n = self.recipe['nodes'][index]
        op = n['op']
        if op == 'const':
            v = float(n['value']) if n['type'] != 'b' else (1.0 if n['value'] else 0.0)
        elif op == 'input':
            v = self.input(n['name'])
        elif op == 'channel':
            v = self.sample(n['args'][0])[n['index']]
        elif op in ('ddx', 'ddy'):
            v = 0.0   # derivatives of a constant-rate surface; the game's screen-space derivative is not available
        else:
            a = [self.value(x) for x in n['args']]
            v = self.operation(op, a)
        self.memo[index] = v
        return v

    def operation(self, op, a):
        if op in MATH:
            return self.math(MATH[op], *a)
        if op == 'neg':
            return self.math('MULTIPLY', a[0], -1.0)
        if op == 'sat':
            return self.math('ADD', a[0], 0.0, clamp=True)
        if op == 'rcp':
            return self.math('DIVIDE', 1.0, a[0])
        if op == 'log2':
            return self.math('LOGARITHM', a[0], 2.0)
        if op == 'exp2':
            return self.math('POWER', 2.0, a[0])
        if op == 'ge':
            return self.math('SUBTRACT', 1.0, self.math('LESS_THAN', a[0], a[1]))
        if op in ('eq', 'ne'):
            n = self.node('ShaderNodeMath', operation='COMPARE')
            self.feed(a[0], n.inputs[0])
            self.feed(a[1], n.inputs[1])
            n.inputs[2].default_value = 0.0
            return n.outputs[0] if op == 'eq' else self.math('SUBTRACT', 1.0, n.outputs[0])
        if op == 'b_and':
            return self.math('MULTIPLY', a[0], a[1])
        if op == 'b_or':
            return self.math('MAXIMUM', a[0], a[1])
        if op == 'b_not':
            return self.math('SUBTRACT', 1.0, a[0])
        if op == 'select':   # b + c (a - b), c in {0, 1}
            c, x, y = a
            return self.math('MULTIPLY_ADD', c, self.math('SUBTRACT', x, y), y)
        raise ValueError('Recipe operation not emitted: ' + op)

    # -- inputs
    def geometry(self):
        if 'geometry' not in self.inputs:
            self.inputs['geometry'] = self.node('ShaderNodeNewGeometry')
        return self.inputs['geometry']

    def game_vector(self, socket):
        """A Blender world vector as game (x, y, z) = Blender (x, z, -y)."""
        sep = self.node('ShaderNodeSeparateXYZ')
        self.links.new(socket, sep.inputs[0])
        return [sep.outputs[0], sep.outputs[2], self.math('MULTIPLY', sep.outputs[1], -1.0)]

    def frame(self):
        if 'frame' in self.inputs:
            return self.inputs['frame']
        g = self.geometry()
        # Cycles turns the shading normal towards the viewer on back faces; the game's interpolant is not turned.
        facing = self.math('MULTIPLY_ADD', g.outputs['Backfacing'], -2.0, 1.0)
        normal = self.node('ShaderNodeVectorMath', operation='SCALE')
        self.links.new(g.outputs['Normal'], normal.inputs[0])
        self.links.new(facing, normal.inputs['Scale'])
        probes = {}
        for name, colour in (('tangent', (1.0, 0.5, 0.5, 1.0)), ('bitangent', (0.5, 1.0, 0.5, 1.0))):
            probe = self.node('ShaderNodeNormalMap', space='TANGENT', uv_map=self.uv_maps[0] if self.uv_maps else '')
            probe.inputs['Color'].default_value = colour
            probe.inputs['Strength'].default_value = 1.0
            probes[name] = probe.outputs[0]
        cross = self.node('ShaderNodeVectorMath', operation='CROSS_PRODUCT')
        self.links.new(normal.outputs[0], cross.inputs[0])
        self.links.new(probes['tangent'], cross.inputs[1])
        dot = self.node('ShaderNodeVectorMath', operation='DOT_PRODUCT')
        self.links.new(cross.outputs[0], dot.inputs[0])
        self.links.new(probes['bitangent'], dot.inputs[1])
        sign = self.math('MULTIPLY_ADD', self.math('LESS_THAN', dot.outputs['Value'], 0.0), -2.0, 1.0)
        self.inputs['frame'] = dict(normal=self.game_vector(normal.outputs[0]), tangent=self.game_vector(probes['tangent']),
                                    sign=sign)
        return self.inputs['frame']

    def uv(self, layer):
        key = 'uv%d' % layer
        if key not in self.inputs:
            if layer >= len(self.uv_maps):
                self.inputs[key] = [0.0, 0.0]
            else:
                uv = self.node('ShaderNodeUVMap', uv_map=self.uv_maps[layer])
                sep = self.node('ShaderNodeSeparateXYZ')
                self.links.new(uv.outputs['UV'], sep.inputs[0])
                self.inputs[key] = [sep.outputs[0], self.math('SUBTRACT', 1.0, sep.outputs[1])]
        return self.inputs[key]

    def colour(self):
        if 'colour' not in self.inputs:
            if self.colour_attribute:
                attr = self.node('ShaderNodeVertexColor', layer_name=self.colour_attribute)
                sep = self.node('ShaderNodeSeparateColor')
                self.links.new(attr.outputs['Color'], sep.inputs[0])
                self.inputs['colour'] = [sep.outputs[0], sep.outputs[1], sep.outputs[2], attr.outputs['Alpha']]
            else:
                self.inputs['colour'] = [1.0, 1.0, 1.0, 1.0]
        return self.inputs['colour']

    def view(self):
        """(camera-to-point offset, camera position) as game vectors."""
        if 'view' not in self.inputs:
            g = self.geometry()
            camera = self.node('ShaderNodeCameraData')
            towards = self.node('ShaderNodeVectorMath', operation='SCALE')
            self.links.new(g.outputs['Incoming'], towards.inputs[0])
            self.links.new(camera.outputs['View Distance'], towards.inputs['Scale'])
            offset = self.node('ShaderNodeVectorMath', operation='SCALE')
            self.links.new(towards.outputs[0], offset.inputs[0])
            offset.inputs['Scale'].default_value = -1.0
            eye = self.node('ShaderNodeVectorMath', operation='ADD')
            self.links.new(g.outputs['Position'], eye.inputs[0])
            self.links.new(towards.outputs[0], eye.inputs[1])
            self.inputs['view'] = (self.game_vector(offset.outputs[0]), self.game_vector(eye.outputs[0]))
        return self.inputs['view']

    def input(self, name):
        axis = {'x': 0, 'y': 1, 'z': 2, 'w': 3}
        if name in self.fixed:
            return float(self.fixed[name])
        if name in ('VIEW.x', 'VIEW.y', 'VIEW.z'):
            return self.view()[0][axis[name[-1]]]
        if name in ('CAMERA.x', 'CAMERA.y', 'CAMERA.z'):
            return self.view()[1][axis[name[-1]]]
        if name == 'SV_IsFrontFace':
            return self.math('SUBTRACT', 1.0, self.geometry().outputs['Backfacing'])
        semantic, _, component = name.partition('.')
        c = axis.get(component)
        if semantic == 'TEXCOORD' and c in (0, 1):
            return self.uv(0)[c]
        if semantic == 'TEXCOORD1' and c in (0, 1):
            return self.uv(1)[c]
        if semantic == 'TEXCOORD2' and c in (0, 1, 2):
            return self.frame()['normal'][c]
        if semantic == 'TEXCOORD3':
            return self.frame()['sign'] if c == 3 else self.frame()['tangent'][c]
        if semantic == 'TEXCOORD4':
            return self.colour()[c]
        raise ValueError('Recipe input not available in Blender: ' + name)

    # -- textures
    def sample(self, index):
        if index in self.samples:
            return self.samples[index]
        n = self.recipe['nodes'][index]
        image = self.images.get(n['binding'])
        if image is None:
            raise ValueError('No image for ' + n['binding'])
        u, v = self.value(n['args'][0]), self.value(n['args'][1])
        if isinstance(image, Volume):
            self.samples[index] = self.volume(image, u, v, self.value(n['args'][2]), n['binding'])
            return self.samples[index]
        combine = self.node('ShaderNodeCombineXYZ')
        self.feed(u, combine.inputs[0])
        self.feed(self.math('SUBTRACT', 1.0, v) if not isinstance(v, float) else 1.0 - v, combine.inputs[1])

        def lookup(img, label):
            tex = self.node('ShaderNodeTexImage', image=img, interpolation=self.interpolation, extension='REPEAT')
            tex.name = label
            self.links.new(combine.outputs[0], tex.inputs['Vector'])
            sep = self.node('ShaderNodeSeparateColor')
            self.links.new(tex.outputs['Color'], sep.inputs[0])
            return [sep.outputs[0], sep.outputs[1], sep.outputs[2], tex.outputs['Alpha']]
        if not isinstance(image, list):
            self.samples[index] = lookup(image, 'Unit %s' % n['binding'])
            return self.samples[index]
        # Array slice round(z), clamped to the array, picks one slice's lookup.
        last = float(len(image) - 1)
        z = self.math('MINIMUM', self.math('MAXIMUM', self.math('ROUND', self.value(n['args'][2])), 0.0), last)
        channels = [0.0, 0.0, 0.0, 0.0]
        for k, img in enumerate(image):
            pick = self.node('ShaderNodeMath', operation='COMPARE')
            self.links.new(z, pick.inputs[0])
            pick.inputs[1].default_value = float(k)
            pick.inputs[2].default_value = 0.25
            looked = lookup(img, 'Unit %s slice %d' % (n['binding'], k))
            channels = [self.math('MULTIPLY_ADD', pick.outputs[0], c, total) if k else
                        self.math('MULTIPLY', pick.outputs[0], c) for c, total in zip(looked, channels)]
        self.samples[index] = channels
        return channels


    def volume(self, vol, u, v, z, binding):
        """Trilinear wrap lookup in a padded atlas: bilinear in the slices floor(s) and floor(s) + 1 (mod depth), s = z depth
        - 0.5, mixed by frac(s)."""
        d, cols, w, h = float(vol.depth), vol.columns, float(vol.width), float(vol.height)
        rows = -(-vol.depth // cols)
        aw, ah = cols * (w + 2.0), rows * (h + 2.0)
        s = self.math('SUBTRACT', self.math('MULTIPLY', z, d), 0.5)
        k0 = self.math('FLOOR', s)
        f = self.math('SUBTRACT', s, k0)
        fu, fv = self.math('FRACT', u), self.math('FRACT', v)
        looked = []
        for step in (0.0, 1.0):
            k = self.math('FLOORED_MODULO', self.math('ADD', k0, step), d)
            row = self.math('FLOOR', self.math('DIVIDE', k, float(cols)))
            col = self.math('SUBTRACT', k, self.math('MULTIPLY', row, float(cols)))
            x = self.math('DIVIDE', self.math('ADD', self.math('MULTIPLY', col, w + 2.0), self.math('MULTIPLY_ADD', fu, w, 1.0)), aw)
            y = self.math('DIVIDE', self.math('ADD', self.math('MULTIPLY', row, h + 2.0), self.math('MULTIPLY_ADD', fv, h, 1.0)), ah)
            combine = self.node('ShaderNodeCombineXYZ')
            self.feed(x, combine.inputs[0])
            self.feed(self.math('SUBTRACT', 1.0, y), combine.inputs[1])   # atlas rows are stored top first
            tex = self.node('ShaderNodeTexImage', image=vol.image, interpolation=self.interpolation, extension='CLIP')
            tex.name = 'Unit %s volume %d' % (binding, int(step))
            self.links.new(combine.outputs[0], tex.inputs['Vector'])
            sep = self.node('ShaderNodeSeparateColor')
            self.links.new(tex.outputs['Color'], sep.inputs[0])
            looked.append([sep.outputs[0], sep.outputs[1], sep.outputs[2], tex.outputs['Alpha']])
        return [self.math('MULTIPLY_ADD', f, self.math('SUBTRACT', b, a), a) for a, b in zip(*looked)]


SURFACE_OUTPUTS = (('Albedo', 'NodeSocketColor'), ('F0', 'NodeSocketColor'), ('Gloss', 'NodeSocketFloat'),
                   ('Normal', 'NodeSocketVector'), ('Occlusion', 'NodeSocketFloat'), ('Emission', 'NodeSocketFloat'),
                   ('Alpha', 'NodeSocketFloat'))


def build_surface_group(recipe, images, uv_maps, colour_attribute=None, interpolation='Linear'):
    """A node group computing the recipe's surface terms (Albedo, F0, Gloss, Normal, Occlusion, Emission, Alpha)."""
    digest = recipe_sha256(recipe)
    name = SURFACE_PREFIX + digest[:16]
    group = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    group[RECIPE_PROPERTY] = digest
    for socket, kind in SURFACE_OUTPUTS:
        _socket(group, socket, 'OUTPUT', kind)
    out = group.nodes.new('NodeGroupOutput')
    em = _Emitter(group, recipe, images, uv_maps, colour_attribute, interpolation)
    terms = {k: em.value(i) for k, i in recipe['terms'].items()}

    def combine(names, kind):
        node = em.node(kind)
        for k, n in enumerate(names):
            em.feed(terms[n], node.inputs[k])
        return node.outputs[0]
    group.links.new(combine(('albedo.r', 'albedo.g', 'albedo.b'), 'ShaderNodeCombineColor'), out.inputs['Albedo'])
    group.links.new(combine(('f0.r', 'f0.g', 'f0.b'), 'ShaderNodeCombineColor'), out.inputs['F0'])
    # Game normal (x, y, z) -> Blender (x, -z, y), normalised.
    normal = em.node('ShaderNodeCombineXYZ')
    em.feed(terms['normal.x'], normal.inputs[0])
    em.feed(em.math('MULTIPLY', terms['normal.z'], -1.0) if not isinstance(terms['normal.z'], float)
            else -terms['normal.z'], normal.inputs[1])
    em.feed(terms['normal.y'], normal.inputs[2])
    unit = em.node('ShaderNodeVectorMath', operation='NORMALIZE')
    group.links.new(normal.outputs[0], unit.inputs[0])
    group.links.new(unit.outputs[0], out.inputs['Normal'])
    for socket, term in (('Gloss', 'gloss'), ('Occlusion', 'occlusion'), ('Emission', 'emission')):
        em.feed(terms[term], out.inputs[socket])
    clip = terms['clip']
    em.feed(em.math('SUBTRACT', 1.0, clip) if not isinstance(clip, float) else 1.0 - clip, out.inputs['Alpha'])
    out.location = ((em.count // 40 + 1) * 200, 0)
    return group


FORWARD_OUTPUTS = (('Colour', 'NodeSocketColor'), ('Alpha', 'NodeSocketFloat'), ('Background', 'NodeSocketColor'))


def build_forward_group(recipe, images, uv_maps, colour_attribute=None, interpolation='Linear', fixed=None):
    """A node group computing a forward recipe's Colour (HDR), Alpha (with the program's discard applied) and Background
    tint."""
    digest = recipe_sha256(recipe)
    group = bpy.data.node_groups.new(FORWARD_PREFIX + digest[:16], 'ShaderNodeTree')
    group[FORWARD_PROPERTY] = digest
    for socket, kind in FORWARD_OUTPUTS:
        _socket(group, socket, 'OUTPUT', kind)
    out = group.nodes.new('NodeGroupOutput')
    em = _Emitter(group, recipe, images, uv_maps, colour_attribute, interpolation, fixed)
    terms = {k: em.value(i) for k, i in recipe['terms'].items()}
    colour = em.node('ShaderNodeCombineColor')
    for k, name in enumerate(('colour.r', 'colour.g', 'colour.b')):
        em.feed(terms[name], colour.inputs[k])
    group.links.new(colour.outputs[0], out.inputs['Colour'])
    tint = em.node('ShaderNodeCombineColor')
    for k, name in enumerate(('background.r', 'background.g', 'background.b')):
        em.feed(terms.get(name, 0.0), tint.inputs[k])
    group.links.new(tint.outputs[0], out.inputs['Background'])
    clip = terms['clip']
    keep = em.math('SUBTRACT', 1.0, clip) if not isinstance(clip, float) else 1.0 - clip
    alpha = terms['alpha']
    em.feed(alpha * keep if isinstance(alpha, float) and isinstance(keep, float) else em.math('MULTIPLY', alpha, keep),
            out.inputs['Alpha'])
    out.location = ((em.count // 40 + 1) * 200, 0)
    return group


def load_image(path, colourspace, kind='png', width=0, height=0):
    """A unit texture as written by rc2016.games.rcra.blender_export: PNG, or raw float32 RGBA rows top first."""
    if kind == 'float':
        import numpy as np
        name = Path(path).stem
        image = bpy.data.images.get(name)
        if image is None or image.get('rc_source') != str(path):
            data = np.fromfile(str(path), dtype='<f4').reshape(height, width, 4)[::-1]   # Blender rows bottom first
            image = bpy.data.images.new(name, width, height, alpha=True, float_buffer=True)
            image.pixels.foreach_set(np.ascontiguousarray(data).ravel())
            image.file_format = 'OPEN_EXR'
            image['rc_source'] = str(path)
            image.pack()
    else:
        image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = colourspace
    image.alpha_mode = 'CHANNEL_PACKED'
    return image


class Volume:
    """A 3D texture's padded slice atlas (rc2016.games.rcra.blender_export.write_volume_texture)."""

    def __init__(self, image, depth, columns, width, height):
        self.image, self.depth, self.columns, self.width, self.height = image, depth, columns, width, height


def _load_row(row):
    if 'slices' in row:   # a texture array sampled at a run-time slice: one image per slice
        return [_load_row(r) for r in row['slices']]
    if 'volume' in row:
        v = row['volume']
        image = load_image(row['path'], row['colourspace'], row.get('kind', 'png'), row.get('width', 0), row.get('height', 0))
        return Volume(image, v['depth'], v['columns'], v['width'], v['height'])
    return load_image(row['path'], row['colourspace'], row.get('kind', 'png'), row.get('width', 0), row.get('height', 0))


def load_images(rows):
    return {binding: _load_row(row) for binding, row in rows.items()}


def _surface_shader(tree, entry, images, uv_maps, colour_attribute, blended):
    """The surface group feeding the BRDF group; returns the shader socket (alpha clip applied when opaque)."""
    recipe = entry['recipe']
    surface = build_surface_group(recipe, images, uv_maps, colour_attribute)
    s = tree.nodes.new('ShaderNodeGroup')
    s.node_tree, s.name, s.location = surface, 'Translated surface', (0, 0)
    b = tree.nodes.new('ShaderNodeGroup')
    b.node_tree, b.name, b.location = build_brdf_group(), 'Game BRDF', (300, 0)
    for socket in ('Albedo', 'F0', 'Gloss', 'Normal', 'Occlusion') + (() if blended else ('Emission',)):
        tree.links.new(s.outputs[socket], b.inputs[socket])
    clip = recipe['nodes'][recipe['terms']['clip']]
    if clip['op'] != 'const' or clip['value']:   # alpha-tested: the writer's discard becomes a transparent mix
        mix = tree.nodes.new('ShaderNodeMixShader')
        clear = tree.nodes.new('ShaderNodeBsdfTransparent')
        mix.location, clear.location = (560, 0), (300, -250)
        tree.links.new(s.outputs['Alpha'], mix.inputs['Fac'])
        tree.links.new(clear.outputs[0], mix.inputs[1])
        tree.links.new(b.outputs['Shader'], mix.inputs[2])
        return mix.outputs[0], surface
    return b.outputs['Shader'], surface


def _forward_shader(tree, entry, images, uv_maps, colour_attribute, lit):
    """The forward group composed over a Transparent BSDF by the entry's blend mode."""
    group = build_forward_group(entry['forward'], images, uv_maps, colour_attribute)
    f = tree.nodes.new('ShaderNodeGroup')
    f.node_tree, f.name, f.location = group, 'Translated forward', (0, -400)
    nodes, links = tree.nodes, tree.links
    clear = nodes.new('ShaderNodeBsdfTransparent')
    clear.location = (300, -600)
    mode = entry['blend']
    if mode == 'modulate':
        tint = nodes.new('ShaderNodeMix')
        tint.data_type, tint.location = 'RGBA', (300, -400)
        links.new(f.outputs['Alpha'], tint.inputs['Factor'])
        tint.inputs['A'].default_value = (1.0, 1.0, 1.0, 1.0)
        links.new(f.outputs['Colour'], tint.inputs['B'])
        links.new(tint.outputs['Result'], clear.inputs['Color'])
        return clear.outputs[0], group
    emit = nodes.new('ShaderNodeEmission')
    emit.location = (300, -400)
    links.new(f.outputs['Colour'], emit.inputs['Color'])
    total = nodes.new('ShaderNodeAddShader')
    total.location = (1000, -400)
    links.new(clear.outputs[0], total.inputs[0])
    if mode == 'additive':
        through = nodes.new('ShaderNodeVectorMath')   # 1 + alpha B
        through.operation, through.location = 'MULTIPLY_ADD', (120, -600)
        links.new(f.outputs['Background'], through.inputs[0])
        links.new(f.outputs['Alpha'], through.inputs[1])
        through.inputs[2].default_value = (1.0, 1.0, 1.0)
        links.new(through.outputs[0], clear.inputs['Color'])
        links.new(f.outputs['Alpha'], emit.inputs['Strength'])
        links.new(emit.outputs[0], total.inputs[1])
        return total.outputs[0], group
    through = nodes.new('ShaderNodeMix')   # mix(white, B, alpha) = 1 - alpha + alpha B
    through.data_type, through.location = 'RGBA', (120, -600)
    links.new(f.outputs['Alpha'], through.inputs['Factor'])
    through.inputs['A'].default_value = (1.0, 1.0, 1.0, 1.0)
    links.new(f.outputs['Background'], through.inputs['B'])
    links.new(through.outputs['Result'], clear.inputs['Color'])
    body = emit.outputs[0]
    if lit is not None:
        combine = nodes.new('ShaderNodeAddShader')
        combine.location = (560, -400)
        links.new(lit, combine.inputs[0])
        links.new(emit.outputs[0], combine.inputs[1])
        body = combine.outputs[0]
    weight = nodes.new('ShaderNodeMixShader')   # alpha x body: the first input is left empty (no light)
    weight.location = (800, -400)
    links.new(f.outputs['Alpha'], weight.inputs['Fac'])
    links.new(body, weight.inputs[2])
    links.new(weight.outputs[0], total.inputs[1])
    return total.outputs[0], group


def apply_recipe(material, entry, uv_maps, colour_attribute=None):
    """Replace ``material``'s nodes with the translated material.

    ``entry``: dict(blend, recipe (G-buffer surface), forward (blended materials), images {binding: row}, limitations).
    An opaque entry needs ``recipe``; a blended one needs ``forward`` and uses ``recipe`` for its lit part when present."""
    mode = entry.get('blend', 'opaque')
    if mode not in BLEND_MODES:
        raise ValueError('Unknown blend mode ' + str(mode))
    images = load_images(entry['images'])
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new('ShaderNodeOutputMaterial')
    output.location = (1100, 0)
    shader, surface, forward = None, None, None
    if entry.get('recipe'):
        shader, surface = _surface_shader(tree, entry, images, uv_maps, colour_attribute, mode != 'opaque')
    if mode != 'opaque':
        shader, forward = _forward_shader(tree, entry, images, uv_maps, colour_attribute,
                                          shader if mode == 'blended' else None)
        if hasattr(material, 'surface_render_method'):   # EEVEE draws it sorted and blended
            material.surface_render_method = 'BLENDED'
    if shader is None:
        raise ValueError('Entry has no surface recipe')
    tree.links.new(shader, output.inputs['Surface'])
    for key, group, prop in (('recipe', surface, RECIPE_PROPERTY), ('forward', forward, FORWARD_PROPERTY)):
        if group is not None:
            material[prop] = group[prop]
        elif prop in material:
            del material[prop]
    material['rc_rcra_blend'] = mode
    material['rc_rcra_limitations'] = '\n'.join(entry.get('limitations', []))
    return material


def translated(material):
    """True for a material apply_recipe built."""
    return bool(material.get(RECIPE_PROPERTY) or material.get(FORWARD_PROPERTY))


def _images_missing(material, group):
    problems = []
    for node in group.nodes:
        if node.type == 'TEX_IMAGE':
            image = node.image
            if image is None or not (image.packed_file or image.has_data or image.size[0]):
                problems.append('%s: %s has no image data' % (material.name, node.name))
    return problems


def validate_material(material):
    """Problems with a translated material after a reopen; empty when it is intact."""
    if not translated(material):
        return ['%s: no translated recipe' % material.name]
    problems = []
    tree = material.node_tree
    mode = material.get('rc_rcra_blend', 'opaque')
    digest = material.get(RECIPE_PROPERTY)
    if mode == 'opaque' and not digest:
        problems.append('%s: opaque material without a surface recipe' % material.name)
    if digest:
        surface = tree.nodes.get('Translated surface') if tree else None
        brdf = tree.nodes.get('Game BRDF') if tree else None
        if surface is None or surface.node_tree is None or surface.node_tree.get(RECIPE_PROPERTY) != digest:
            problems.append('%s: surface group missing or not its recipe' % material.name)
        if brdf is None or brdf.node_tree is None or brdf.node_tree.get('rc_version') != GROUP_VERSION:
            problems.append('%s: BRDF group missing or out of date' % material.name)
        if surface is not None and surface.node_tree is not None:
            problems += _images_missing(material, surface.node_tree)
            for socket in surface.node_tree.nodes['Group Output'].inputs[:len(SURFACE_OUTPUTS)]:
                if not socket.is_linked and socket.name not in ('Gloss', 'Occlusion', 'Emission', 'Alpha'):
                    problems.append('%s: surface output %s unlinked' % (material.name, socket.name))
    if mode != 'opaque':
        forward = tree.nodes.get('Translated forward') if tree else None
        want = material.get(FORWARD_PROPERTY)
        if not want or forward is None or forward.node_tree is None or forward.node_tree.get(FORWARD_PROPERTY) != want:
            problems.append('%s: forward group missing or not its recipe' % material.name)
        elif not forward.node_tree.nodes['Group Output'].inputs['Colour'].is_linked:
            problems.append('%s: forward colour unlinked' % material.name)
        else:
            problems += _images_missing(material, forward.node_tree)
    return problems
