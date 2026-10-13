"""Explicit presentation previews; never automatic material readiness.

Pinned previews adapt one source-hashed material each. The generic
world-position preview applies to any material that is not ready and binds once
inferred camera + ray * POS_W expressions become viewer world position.

The mountain preview deliberately chooses the simpler source writer and adapts
its unbound position/normal inputs to viewer geometry. Full runtime selection,
frame reconstruction and detail fade remain unsupported by the native binder.
"""
import hashlib
import json
import struct

from rc2016.shaders import bindings, translator
from rc2016.shaders.ir import DAG

MATERIAL = 'material/environment/pokitaru/ground/pok_gnd_sand_floor_blend_01/rp_nov_gnd_dirt_rock_blend_01.material'
GRAPH = 'materialgraph/environment/pokitaru/rock/pok_rock_blend_01/rp_nov_grass_blend_detail_01.materialgraph'
MATERIAL_SHA = '9dfecd6d5175c0ddc6b74817f47c0b0e8165ead5d36e5cb3866911583a8fdef4'
GRAPH_SHA = '1ab26db5076714a7ea1a809fa636a6842f4880d526ac63afb9860c4c99180b5c'
CODE_SHA = 'cd12d26c4f6e6b571ae98eb9980ab417e3abc05e19a1aa6dfeb8284c6f140785'
CONTRACT = 'novalis-mountain-simpler-v1'
NOTE = 'Mountain albedo preview: simpler grass/rock writer, viewer geometry, matte lighting; detail and runtime variant unverified.'


def build_preview(material, graph):
    """Hash-bound source slice plus explicit geometry adapters; separate receipt."""
    from rc2016.formats.materials import extract_material_parameters, texture_slot_records, MATERIAL_TEXTURE_SLOTS
    if hashlib.sha256(material).hexdigest() != MATERIAL_SHA or hashlib.sha256(graph).hexdigest() != GRAPH_SHA:
        raise ValueError('Experimental mountain source changed')
    program = next(p for p in translator.pixel_programs(graph) if p['code_sha256'] == CODE_SHA)
    if struct.unpack_from('<2I', graph, program['header_offset']+48) != (0x1b02, 0x1b02):
        raise ValueError('Experimental pixel envelope changed')
    compiler = translator.Compiler(program['code'], program['usage_slots'])
    for reg, name in enumerate(('barycentric0','barycentric1','fragment_x','fragment_y','view_depth','front_face_word')):
        compiler.v[reg] = compiler.ir.node('input', name=name, kind='preview_builtin')
    raw = compiler.compile()
    program = dict(program, ir=raw)
    contracts = bindings.program_contracts(graph, program, bindings.pass_contracts(graph))
    if len(contracts) != 1 or contracts[0]['index'] != 1:
        raise ValueError('Experimental source pass join changed')
    contract = contracts[0]
    values, parameters = bindings._parameter_values(contract, extract_material_parameters(material))
    lowered = translator.lower_deferred(raw)
    ir = DAG(lowered['nodes'])
    substitutions = {}
    # Only the exact source-hashed simpler pass. These are preview substitutions,
    # not claims that the frame polynomial is bound to native runtime position.
    projected = [s for s in lowered['samples'] if s['api'] == 9]
    if len(projected) != 1:
        raise ValueError('Experimental projected sample changed')
    for coordinate, channel, origin in zip(projected[0]['coordinates'], (0,2), (28,30)):
        outer = ir.nodes[coordinate]
        if outer['op'] != 'mul': raise ValueError('Experimental projected UV shape changed')
        candidates = [a for a in outer['args'] if ir.nodes[a]['op'] == 'add' and
                      any(ir.nodes[j].get('name') == f'CB0[{origin}]' for j in ir.nodes[a]['args'])]
        if len(candidates) != 1: raise ValueError('Experimental projected origin changed')
        substitutions[candidates[0]] = ir.node('input', name=f'preview_position.{channel}', kind='preview_position', channel=channel)
    for i,n in enumerate(list(ir.nodes)):
        if n['op'] != 'input': continue
        if n.get('kind') == 'cb' and n['api'] == 2:
            substitutions[i] = ir.const(values[n['dword']])
        elif n.get('kind') == 'attribute' and n['attribute'] == 2 and n['channel'] < 3:
            ch = n['channel']
            substitutions[i] = ir.node('input', name=f'preview_normal.{ch}', kind='preview_normal', channel=ch)
        elif n['name'] == 'front_face_word':
            substitutions[i] = ir.node('input', name='preview_front_face', kind='preview_front_face')
    o = lowered['outputs']
    # The two no-specular resources have no built asset in the validated archive.
    # Preview the source albedo only. Matte F0/zero gloss are presentation values,
    # never decoded substitutes for the missing resource or native output.
    matte = ir.const(0.)
    roots = ir.replace([*o['albedo'],matte,matte,matte,matte,o['emission']], substitutions)
    roots, guards = bindings.guard_source_zero_powers(ir, roots)
    used = ir.reachable(roots)
    for i in used:
        n = ir.nodes[i]
        if n['op'] not in bindings.PORTABLE_OPS: raise ValueError('Unresolved experimental operation')
        if n['op'] == 'input' and not (
                (n.get('kind') == 'attribute' and n.get('attribute') == 0 and n.get('channel') in (0,1)) or
                (n.get('kind') in ('preview_position','preview_normal') and n.get('channel') in (0,1,2)) or
                n.get('kind') == 'preview_front_face'):
            raise ValueError('Unresolved experimental input '+n['name'])
    compact = DAG(); memo = {}
    def copy(i):
        if i not in memo:
            n = ir.nodes[i]
            memo[i] = compact.node(n['op'],[copy(j) for j in n['args']],**{k:v for k,v in n.items() if k not in ('op','args')})
        return memo[i]
    roots = [copy(i) for i in roots]
    overrides = {}
    for row in texture_slot_records(material, MATERIAL_TEXTURE_SLOTS):
        key = (row['slot_key'],row['type_hash'])
        if key in overrides and overrides[key]['path'] != row['path']:
            raise ValueError('Conflicting experimental texture override')
        overrides[key] = row
    active = {n['api'] for n in compact.nodes if n['op'] == 'texture'}
    textures = []
    for api in sorted(active):
        row = contract['resources'][api-5]
        chosen = overrides.get((row['slot_key'],row['type_hash']),row) if row['flag'] == 1 else row
        if row['flag'] not in (0,1) or not chosen['path']: raise ValueError('Unbound experimental texture')
        textures.append(dict(api=api,path=chosen['path'],slot_key=row['slot_key'],type_hash=row['type_hash']))
    result = dict(status='experimental', preview_contract=CONTRACT, label=NOTE,
        material_sha256=MATERIAL_SHA, graph_sha256=GRAPH_SHA, code_sha256=CODE_SHA,
        nodes=compact.nodes, outputs=dict(albedo=roots[:3],F0=roots[3:6],gloss=roots[6],emission=roots[7]),
        normal=None, texture_bindings=textures, parameters=parameters, pass_contract_index=1,
        limitations=[NOTE,'Albedo only: F0=0/gloss=0 are explicit matte presentation settings; rk_no_spec_c has no built source asset.',
                     'Original packed tangent transport and detailed sampled-normal composition are omitted.',
                     *lowered['limitations']], source_zero_power_guards=guards, game_equivalence=False,
        automatic_ready=False)
    result['translation_sha256'] = hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result


def has_preview(path, native_ready=False):
    """Whether prepare_preview may produce a preview for ``path``: a pinned preview material, or
    any material without a ready native translation (the generic world-position adapter)."""
    from rc2016.shaders import terrain_preview
    return path.replace('\\','/').lower() in (MATERIAL, terrain_preview.MATERIAL) or not native_ready


def world_position_preview(archive, path):
    """Generic experimental receipt for a material that is not ready natively and
    whose only engine terms are reconstructed world position; None otherwise."""
    from rc2016.viewer.native_glsl import prepare_material_translation
    if archive is None: return None
    native = prepare_material_translation(archive, path)
    # Opportunistic: only materials whose native binding stops at engine CB0 or pixel-position inputs.
    if native.get('status') == 'ready' or not any('CB0[' in str(d) or 'ps.POS' in str(d) for d in native.get('diagnostics', [])):
        return None
    try:
        result = world_position_translation(archive, path)
    except (ValueError, KeyError, IndexError, OSError, struct.error):
        return None
    return result if result.get('status') == 'experimental' else None


def world_position_translation(archive, path):
    """The world-position adapter's translation attempt (with diagnostics), cached on the archive."""
    from rc2016.formats.archive import asset_data, asset_references
    from rc2016.formats.materials import extract_material_parameters
    from rc2016.viewer.textures import _archive_cache, _remember
    cache = _archive_cache(archive, '_world_position_preview_cache')
    if path not in cache:
        ids = [i for i in archive.resolve(path) if archive.entries[i]['type'] == 'material']
        if len(ids) != 1: raise ValueError('Ambiguous experimental source '+path)
        material = asset_data(archive, ids[0])
        graphs = list(dict.fromkeys(r['path'] for r in asset_references(archive, ids[0]) if r['path'].endswith('.materialgraph')))
        datas = []
        for g in graphs:
            gids = [i for i in archive.resolve(g) if archive.entries[i]['type'] == 'materialgraph']
            if len(gids) != 1: raise ValueError('Ambiguous experimental source '+g)
            datas.append(bytes(asset_data(archive, gids[0])))
        disk = getattr(archive, 'translation_disk_cache', None)
        key = disk.key(bytes(material), datas, variant='world_position') if disk is not None else None
        result = disk.get(key) if key else None
        if result is None:
            result = bindings.prepare_translation(datas, material, extract_material_parameters(material), world_position=True)
            if key: disk.put(key, result)
        _remember(cache, path, result)
    return cache[path]


def prepare_preview(archive, path):
    from rc2016.shaders import terrain_preview
    if path.replace('\\','/').lower() == terrain_preview.MATERIAL:
        return terrain_preview.prepare_preview(archive, path)
    if path.replace('\\','/').lower() != MATERIAL: return world_position_preview(archive, path)
    from rc2016.formats.archive import asset_data
    from rc2016.viewer.textures import _archive_cache, _remember
    cache = _archive_cache(archive,'_experimental_material_cache')
    if path not in cache:
        def read(p, kind):
            ids = [i for i in archive.resolve(p) if archive.entries[i]['type'] == kind]
            if len(ids) != 1: raise ValueError('Ambiguous experimental source '+p)
            return asset_data(archive,ids[0])
        _remember(cache,path,build_preview(read(path,'material'),read(GRAPH,'materialgraph')))
    return cache[path]


def preview_with_fallback(receipt, fallback):
    """Keep a richer normal-mapped fallback when inferred normals drive albedo.

    The raw experimental receipt stays available to the census. Normalisation
    alone does not identify a vertex vector as the geometry normal, and replacing
    distinct vectors can select a different colour layer. This selection guard
    applies only to the generic adapter, not independently pinned previews.
    """
    if (receipt.get('status') != 'experimental'
            or receipt.get('preview_contract') != bindings.WORLD_POSITION_CONTRACT
            or receipt.get('normal') or not fallback.get('_n')):
        return receipt
    ir = DAG(receipt['nodes'])
    if not any(ir.nodes[i].get('kind') == 'preview_normal'
               for i in ir.reachable(receipt['outputs']['albedo'])):
        return receipt
    reason = ('Viewer override suppressed: inferred geometry-normal inputs drive albedo, '
              'but no tangent-normal slice is recovered and the fallback has a source normal map. '
              'Normalised vertex vectors need not be the same geometry normal; their identity, '
              'space and per-draw selection remain unverified. The richer fallback is retained.')
    return dict(receipt, viewer_override=False, viewer_override_limitation=reason,
                limitations=[reason, *receipt.get('limitations', [])])


def valid_contract(receipt):
    """Only independently pinned preview contracts reach experimental emitters."""
    from rc2016.shaders import terrain_preview
    identity = tuple(receipt.get(k) for k in ('preview_contract','code_sha256','graph_sha256','material_sha256'))
    if receipt.get('preview_contract') == bindings.WORLD_POSITION_CONTRACT:
        # Generic adapter: source-hashed per receipt; the emitters still admit only
        # UV0, front facing and viewer world position as inputs.
        return (receipt.get('status') == 'experimental' and receipt.get('automatic_ready') is False
                and receipt.get('game_equivalence') is False and all(identity[1:]))
    return identity in ((CONTRACT,CODE_SHA,GRAPH_SHA,MATERIAL_SHA),
                       (terrain_preview.CONTRACT,terrain_preview.CODE_SHA,terrain_preview.GRAPH_SHA,terrain_preview.MATERIAL_SHA))
