"""Fail-closed visual review status from reopened Blender material/mesh evidence.

This module classifies presentation risk only. It does not alter a shader or
establish game-shader equivalence.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Mapping

SCHEMA = 'rcps4-material-presentation-gate-v1'
PROFILED = 'profiled_presentation'
REVIEW = 'review_required_full_reconstruction'
NOT_APPLICABLE = 'source_only_or_not_applicable'
_HASH = re.compile(r'^[0-9a-f]{64}$', re.IGNORECASE)


def _edge(links, from_node, from_socket, to_node, to_socket):
    return [row for row in links if row.get('from_node') == from_node
            and row.get('from_socket_index') == from_socket
            and row.get('to_node') == to_node
            and row.get('to_socket_index') == to_socket]


def _stats(image):
    stats = image.get('channel_stats') or {}
    alpha = stats.get('A') or stats.get('alpha') or {}
    rgb = [stats.get(ch) or {} for ch in 'RGB']
    try:
        median = float(alpha.get('p50', alpha.get('median')))
        mean = float(alpha.get('mean'))
    except (TypeError, ValueError):
        median = mean = None
    try:
        rgb_means = [float(row['mean']) for row in rgb]
        chroma = max(rgb_means) - min(rgb_means)
    except (KeyError, TypeError, ValueError):
        rgb_means = None
        chroma = None
    if median is not None and not math.isfinite(median): median = None
    if mean is not None and not math.isfinite(mean): mean = None
    if chroma is not None and not math.isfinite(chroma): chroma = None
    return {'alpha_median': median, 'alpha_mean': mean,
            'rgb_channel_means': rgb_means, 'rgb_mean_chroma': chroma}


def _valid_receipt(receipt, material_path, material_sha):
    if not isinstance(receipt, Mapping):
        return False
    receipt_path = str(receipt.get('source_material_path', '')).replace('\\', '/').lower()
    source_path = str(material_path or '').replace('\\', '/').lower()
    receipt_sha = str(receipt.get('source_material_sha256', '')).lower()
    profile_sha = str(receipt.get('profile_sha256', '')).lower()
    return bool(source_path and _HASH.fullmatch(str(material_sha or ''))
                and receipt.get('verified') is True
                and receipt_path == source_path
                and receipt_sha == str(material_sha).lower()
                and _HASH.fullmatch(profile_sha)
                and isinstance(receipt.get('recipe'), Mapping))


def _trace_active_mix(material, links):
    """Return a checked Material Output -> Mix Shader -> reconstruction trace."""
    nodes = material.get('nodes')
    if not isinstance(nodes, list):
        return {'valid': False, 'reason': 'node_table_missing'}
    by_id = {}
    for node in nodes:
        if not isinstance(node, Mapping) or not node.get('id') or node['id'] in by_id:
            return {'valid': False, 'reason': 'node_table_malformed'}
        by_id[node['id']] = node
    outputs = [n for n in nodes if n.get('type') == 'OUTPUT_MATERIAL' and n.get('active') is True]
    if len(outputs) != 1:
        return {'valid': False, 'reason': 'active_material_output_ambiguous'}
    output = outputs[0]
    surface_index = output.get('surface_socket_index')
    if not isinstance(surface_index, int):
        return {'valid': False, 'reason': 'surface_socket_index_missing'}
    surface_links = [e for e in links if e.get('to_node') == output['id']
                     and e.get('to_socket_index') == surface_index]
    if len(surface_links) != 1:
        return {'valid': False, 'reason': 'active_surface_link_missing_or_ambiguous'}
    edge = surface_links[0]
    node = by_id.get(edge.get('from_node'))
    if node is None or edge.get('from_socket_index') != 0:
        return {'valid': False, 'reason': 'active_surface_link_malformed'}
    if node.get('type') != 'MIX_SHADER':
        return {'valid': True, 'is_mix': False, 'output_node': output['id'],
                'surface_node': node['id']}
    factor_links = [e for e in links if e.get('to_node') == node['id'] and e.get('to_socket_index') == 0]
    # Mix factor input must be a finite, unlinked scalar. The numeric socket
    # default is captured on the node's input_defaults map.
    if factor_links:
        return {'valid': False, 'reason': 'mix_factor_is_linked'}
    defaults = node.get('input_defaults')
    if not isinstance(defaults, Mapping) or 0 not in defaults and '0' not in defaults:
        return {'valid': False, 'reason': 'mix_factor_default_missing'}
    try:
        factor = float(defaults.get(0, defaults.get('0')))
    except (TypeError, ValueError):
        return {'valid': False, 'reason': 'mix_factor_default_malformed'}
    if not math.isfinite(factor) or not 0.0 <= factor <= 1.0:
        return {'valid': False, 'reason': 'mix_factor_default_out_of_range'}
    input1 = [e for e in links if e.get('to_node') == node['id'] and e.get('to_socket_index') == 1]
    input2 = [e for e in links if e.get('to_node') == node['id'] and e.get('to_socket_index') == 2]
    if len(input1) != 1 or len(input2) != 1:
        return {'valid': False, 'reason': 'mix_shader_inputs_missing_or_ambiguous', 'factor': factor}
    branch1, branch2 = by_id.get(input1[0].get('from_node')), by_id.get(input2[0].get('from_node'))
    if branch1 is None or branch2 is None or input1[0].get('from_socket_index') != 0 or input2[0].get('from_socket_index') != 0:
        return {'valid': False, 'reason': 'mix_shader_branch_link_malformed', 'factor': factor}
    full_reconstruction = branch1.get('type') == 'BSDF_PRINCIPLED' and branch2.get('type') == 'ADD_SHADER'
    return {'valid': True, 'is_mix': True, 'factor': factor,
            'full_reconstruction': full_reconstruction,
            'mix_node': node['id'], 'principled_node': branch1['id'] if branch1.get('type') == 'BSDF_PRINCIPLED' else None,
            'reconstruction_node': branch2['id'] if branch2.get('type') == 'ADD_SHADER' else None,
            'output_node': output['id']}


def _trace_gloss(material, mix_trace, links):
    nodes = {n['id']: n for n in material.get('nodes', []) if isinstance(n, Mapping) and n.get('id')}
    add_id = mix_trace.get('reconstruction_node')
    if not add_id or add_id not in nodes:
        return {'valid': False, 'reason': 'reconstruction_add_node_missing'}
    add = nodes[add_id]
    add_inputs = {i: [e for e in links if e.get('to_node') == add_id and e.get('to_socket_index') == i]
                  for i in (0, 1)}
    if any(len(edges) != 1 for edges in add_inputs.values()):
        return {'valid': False, 'reason': 'reconstruction_add_inputs_malformed'}
    shader_nodes = [nodes.get(edges[0].get('from_node')) for edges in add_inputs.values()]
    if {n.get('type') for n in shader_nodes if n} != {'BSDF_DIFFUSE', 'BSDF_GLOSSY'}:
        return {'valid': False, 'reason': 'reconstruction_is_not_diffuse_plus_glossy'}
    glossy = next((n for n in shader_nodes if n and n.get('type') == 'BSDF_GLOSSY'), None)
    if glossy is None:
        return {'valid': False, 'reason': 'reconstruction_glossy_node_missing'}
    # Use the actual glossy input links; indexes are Blender node socket indexes.
    color_links = [e for e in links if e.get('to_node') == glossy['id'] and e.get('to_socket_index') == 0]
    rough_links = [e for e in links if e.get('to_node') == glossy['id'] and e.get('to_socket_index') == 1]
    if len(color_links) != 1 or len(rough_links) != 1:
        return {'valid': False, 'reason': 'glossy_inputs_malformed'}
    image_edge = color_links[0]
    image = nodes.get(image_edge.get('from_node'))
    if image is None or image.get('type') != 'TEX_IMAGE' or image_edge.get('from_socket_index') != 0:
        return {'valid': False, 'reason': 'gloss_color_not_direct_texture'}
    rough_edge = rough_links[0]
    math_node = nodes.get(rough_edge.get('from_node'))
    if math_node is None or math_node.get('type') != 'MATH' or math_node.get('operation') != 'SUBTRACT' or rough_edge.get('from_socket_index') != 0:
        return {'valid': False, 'reason': 'gloss_roughness_not_subtract'}
    math_inputs = math_node.get('input_defaults') or {}
    default_one = math_inputs.get(0, math_inputs.get('0'))
    try: default_one = float(default_one)
    except (TypeError, ValueError): return {'valid': False, 'reason': 'roughness_subtract_constant_missing'}
    if not math.isclose(default_one, 1.0, abs_tol=1e-8):
        return {'valid': False, 'reason': 'roughness_subtract_constant_not_one'}
    alpha_edges = [e for e in links if e.get('to_node') == math_node['id'] and e.get('to_socket_index') == 1]
    if len(alpha_edges) != 1 or alpha_edges[0].get('from_node') != image['id'] or alpha_edges[0].get('from_socket_index') != 1:
        return {'valid': False, 'reason': 'roughness_alpha_not_same_texture'}
    image_info = image.get('image') or {}
    texture_path = image_info.get('source_texture_path')
    texture_sha = str(image_info.get('source_texture_sha256', '')).lower()
    bindings = material.get('texture_bindings') or []
    binding = next((b for b in bindings if isinstance(b, Mapping) and b.get('path') == texture_path
                    and str(b.get('dds_sha256', '')).lower() == texture_sha), None)
    if binding is None or binding.get('role') != 'colored_specular_gloss':
        return {'valid': False, 'reason': 'gloss_texture_role_or_identity_unverified'}
    colorspace = image_info.get('colorspace')
    alpha_mode = image_info.get('alpha_mode')
    stats = _stats(image_info)
    return {'valid': True, 'glossy_node': glossy['id'], 'image_path': texture_path,
            'image_sha256': texture_sha, 'role': binding['role'],
            'colorspace': colorspace, 'alpha_mode': alpha_mode, **stats,
            'interpretation_matches_convention': colorspace == 'Non-Color' and alpha_mode == 'CHANNEL_PACKED'}


def _status(material, slot_index, mesh_index, polygon_count):
    path = material.get('source_material_path')
    digest = str(material.get('source_material_sha256', '')).lower()
    bindings = material.get('texture_bindings') or []
    roles = {b.get('role') for b in bindings if isinstance(b, Mapping)}
    base_gloss = {'base_color', 'colored_specular_gloss'} <= roles
    receipt = material.get('exact_profile_receipt')
    profile_valid = _valid_receipt(receipt, path, digest)
    profile_claim_present = material.get('profile_claim_present') is True or receipt is not None
    base = {'mesh_index': mesh_index, 'slot_index': slot_index, 'polygon_count': polygon_count,
            'source_material_path': path, 'source_material_sha256': digest or None,
            'game_equivalence': False}
    if polygon_count == 0:
        return {**base, 'status': NOT_APPLICABLE, 'reason_codes': ['slot_not_polygon_used']}
    if not base_gloss:
        return {**base, 'status': NOT_APPLICABLE, 'reason_codes': ['source_cg_pair_absent']}
    links = material.get('links')
    if not isinstance(links, list):
        return {**base, 'status': REVIEW, 'reason_codes': ['active_graph_links_missing']}
    mix = _trace_active_mix(material, links)
    if not mix['valid']:
        return {**base, 'status': REVIEW,
                'reason_codes': ['active_output_path_unresolved', mix['reason']]}
    if profile_claim_present and not profile_valid:
        return {**base, 'status': REVIEW,
                'reason_codes': ['exact_profile_receipt_invalid'],
                'effective_reconstruction_mix': mix.get('factor'),
                'profile_receipt_valid': False}
    gloss_trace = None
    if mix.get('is_mix') and mix.get('full_reconstruction'):
        gloss_trace = _trace_gloss(material, mix, links)
        if not gloss_trace.get('valid'):
            return {**base, 'status': REVIEW,
                    'reason_codes': ['active_reconstruction_path_unresolved', gloss_trace['reason']],
                    'effective_reconstruction_mix': mix.get('factor')}
    if profile_valid:
        if gloss_trace and not gloss_trace.get('interpretation_matches_convention'):
            return {**base, 'status': REVIEW,
                    'reason_codes': ['profile_texture_interpretation_mismatch'],
                    'effective_reconstruction_mix': mix.get('factor'),
                    'profile_receipt_valid': True}
        profile_factor = receipt.get('recipe', {}).get('reconstruction_mix')
        if profile_factor is not None:
            if not mix.get('is_mix') or not mix.get('full_reconstruction'):
                return {**base, 'status': REVIEW,
                        'reason_codes': ['profile_recipe_does_not_match_reopened_graph'],
                        'effective_reconstruction_mix': mix.get('factor'),
                        'profile_receipt_valid': True}
            try: matches = math.isclose(float(profile_factor), mix['factor'], abs_tol=1e-6)
            except (TypeError, ValueError): matches = False
            if not matches:
                return {**base, 'status': REVIEW,
                        'reason_codes': ['profile_recipe_does_not_match_reopened_graph'],
                        'effective_reconstruction_mix': mix.get('factor')}
        return {**base, 'status': PROFILED,
                'reason_codes': ['exact_source_profile_receipt_matches'],
                'profile_sha256': receipt['profile_sha256'],
                'effective_reconstruction_mix': mix.get('factor'),
                'gloss_evidence': gloss_trace}
    if mix.get('is_mix') and mix.get('full_reconstruction') and math.isclose(mix.get('factor', -1), 1.0, abs_tol=1e-6):
        reasons = ['unprofiled_factor_one_reconstruction',
                   'factor_one_reconstruction_washout_risk']
        if gloss_trace:
            if not gloss_trace.get('interpretation_matches_convention'):
                reasons.append('gloss_image_interpretation_mismatch')
            alpha = gloss_trace.get('alpha_median')
            chroma = gloss_trace.get('rgb_mean_chroma')
            if alpha is not None and alpha >= 0.95 and chroma is not None and chroma >= 0.08:
                reasons.append('smooth_chromatic_glossy_lobe_risk')
        return {**base, 'status': REVIEW, 'reason_codes': reasons,
                'effective_reconstruction_mix': mix['factor'], 'gloss_evidence': gloss_trace,
                'profile_receipt_valid': False}
    return {**base, 'status': NOT_APPLICABLE,
            'reason_codes': ['full_unprofiled_reconstruction_not_selected'],
            'effective_reconstruction_mix': mix.get('factor'),
            'profile_receipt_valid': False}


def evaluate_material_presentation(reopened_scene: Mapping[str, Any]) -> dict[str, Any]:
    """Classify every reopened mesh material slot by its *polygon use* and active graph.

    Expected input is a compact snapshot produced from a reopened .blend:
    ``meshes[]`` contains ``polygons`` (rows with ``material_index``) and
    ``material_slots[]`` rows with ``slot_index`` and a normalized ``material``
    record. Materials carry source identity, ``texture_bindings``, node rows,
    socket-indexed links, active Material Output rows, and optional exact profile
    receipt. This function reads no material names and does not load image bytes.
    """
    results = []
    for mesh_index, mesh in enumerate(reopened_scene.get('meshes', [])):
        polygons = mesh.get('polygons')
        if polygons is None:
            polygons = [{'material_index': i} for i in mesh.get('polygon_material_indices', [])]
        counts = Counter(row.get('material_index') for row in polygons if isinstance(row, Mapping))
        for slot_position, slot in enumerate(mesh.get('material_slots', [])):
            slot_index = slot.get('slot_index', slot_position)
            material = slot.get('material')
            results.append(_status(material if isinstance(material, Mapping) else {},
                                   slot_index, mesh_index, counts.get(slot_index, 0)))
    return {'schema': SCHEMA, 'status_counts': dict(Counter(row['status'] for row in results)),
            'materials': results,
            'game_equivalence': False,
            'meaning': 'Visual presentation review status only; no shader construction or game-equivalence claim.'}

