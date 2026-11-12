"""The game's bloom around self-lit materials in a pipeline build (the Blender mirror of the model viewer's emitter bloom).

A material is self-lit by the viewer's rule (rc2016.viewer.bloom.self_lit, read from the material's recorded viewer graph rule or
native translation, or a game-shader 'Self-lit K' above 0): the Clank bots' eyes and bulbs, Zed's visor. Those materials get a
Material Index and the scene compositor feeds them through the decoded bright pass and one Gaussian per chain
(rc2016.pipeline.health_effect.bright_pass_blur), into the game tonemap's Bloom input, which adds it to the tone-curve output
before the sRGB encode as the game's composite does:

- deferred emitters (pass index 3) go through the lit-scene chain: weight 0.82, sigma 75.9 px at 1080p;
- the additive antenna glow shell (pass index 4), a forward draw, through the sprite chain: weight 1, sigma 10.9 px, because the
  game's sprite chain blooms what its transparent pass added.

The gate gain is the game view's exposure, as in the viewer's Game (captured) tonemap. Built only with the game tonemap
(``run.py --tonemap game``): under the file's own view transform the viewer gates at the lighting exposure, where these emitters
stay below the gate. Limitations: each chain is a Gaussian stand-in for the decoded pyramid; only the masked materials bloom (the
game's lit chain also blooms review-lighting highlights, which would haze the model); the bright-pass constants are the nanotech
capture's (another frame used 1/9, -1/3, 0.5). ``bpy`` is imported inside the Blender functions.
"""
from __future__ import annotations

import json

from rc2016.viewer.bloom import self_lit

LIT_PASS_INDEX, SPRITE_PASS_INDEX = 3, 4         # Material Index of deferred emitters and of the glow shell
LABEL = 'Emitter bloom | '


def material_kind(properties, self_lit_k=0.0):
    """'lit', 'sprite' or None for one material from its custom properties (a dict of the recorded JSON strings) and the
    'Self-lit K' of a game-shader material."""
    rule = json.loads(properties['rc_viewer_graph_rule']) if properties.get('rc_viewer_graph_rule') else {}
    if rule.get('applies') is False:
        rule = {}
    ported = rule.get('ported') or ()
    if 'bright_glow' in ported:
        return 'sprite'
    translation = None
    if str(properties.get('shader_status', '')).startswith('native_translated') and properties.get('rc_shader_translation'):
        translation = json.loads(properties['rc_shader_translation'])
    if self_lit(rule.get('k') or self_lit_k, 'bright_center' in ported, False, translation):
        return 'lit'
    return None


def _self_lit_k(material):
    """The largest 'Self-lit K' input default of the material's node groups (game_shader parameters), else 0."""
    best = 0.0
    for node in (material.node_tree.nodes if material.node_tree else ()):
        socket = node.inputs.get('Self-lit K') if hasattr(node, 'inputs') else None
        if socket is not None and not socket.is_linked:
            best = max(best, float(socket.default_value))
    return best


def add_emitter_bloom(scene):
    """Tag the self-lit materials of the scene's meshes and add their bloom to the game tonemap's Bloom input. Returns the
    receipt (also stored as scene['emitter_bloom']), or None when the scene has no game tonemap or no self-lit material."""
    from rc2016.pipeline import health_effect as fx
    tree = scene.compositing_node_group
    game = fx._game_view_node(tree) if tree is not None else None
    if game is None:
        return None
    kinds = {}
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.get(fx.FAMILY):
            continue
        for slot in obj.material_slots:
            material = slot.material
            if material is not None and material.name not in kinds:
                kinds[material.name] = (material, material_kind(dict(material.items()), _self_lit_k(material)))
    tagged = {name: kind for name, (material, kind) in kinds.items() if kind}
    if not tagged:
        return None
    for name, kind in tagged.items():
        kinds[name][0].pass_index = LIT_PASS_INDEX if kind == 'lit' else SPRITE_PASS_INDEX
    scene.view_layers[0].use_pass_material_index = True
    for node in [n for n in tree.nodes if n.name.startswith(LABEL)]:
        tree.nodes.remove(node)
    layers = next((n for n in tree.nodes if n.bl_idname == 'CompositorNodeRLayers'), None) or fx._node(tree, 'CompositorNodeRLayers')
    image = game.inputs['Image'].links[0].from_socket if game.inputs['Image'].links else layers.outputs['Image']
    exposure = 2.0 ** float(game.inputs['Exposure (stops)'].default_value)
    nodes_before = set(tree.nodes)
    total = None
    chains = []
    for kind, pass_index, weight, sigma in (('lit', LIT_PASS_INDEX, fx.LIT_WEIGHT, fx.SIGMA_LIT_PX),
                                            ('sprite', SPRITE_PASS_INDEX, fx.SPRITE_WEIGHT, fx.SIGMA_SPRITE_PX)):
        if kind not in tagged.values():
            continue
        blurred = fx.bright_pass_blur(scene, tree, image, layers.outputs['Material Index'], pass_index, exposure, weight, sigma)
        total = blurred if total is None else fx._mix(tree, 'ADD', total, blurred)
        chains.append(dict(kind=kind, pass_index=pass_index, weight=weight, sigma_px_1080=sigma))
    feeding = game.inputs['Bloom'].links[0].from_socket if game.inputs['Bloom'].links else None   # the health crate's bloom
    tree.links.new(total if feeding is None else fx._mix(tree, 'ADD', feeding, total), game.inputs['Bloom'])
    for node in set(tree.nodes) - nodes_before:
        node.name = LABEL + node.bl_idname.replace('CompositorNode', '').replace('ShaderNode', '')
    scene.render.use_compositing = True
    receipt = dict(materials=dict(sorted(tagged.items())), chains=chains, exposure=exposure,
                   limitation='a Gaussian stand-in per decoded chain; only self-lit materials bloom; nanotech bright-pass constants')
    scene['emitter_bloom'] = json.dumps(receipt, sort_keys=True)
    return receipt
