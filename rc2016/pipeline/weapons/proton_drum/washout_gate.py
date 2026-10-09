"""Fail-closed comparison helpers for hash-bound Proton presentation profiles."""
STRUCTURE_FIELDS=('materials','polygon_used_materials','camera','world','colour_management')

def validate_source_structure(actual, expected):
    """Compare source/graph/layout/light invariants, intentionally not whole blend hash."""
    mismatches=[key for key in STRUCTURE_FIELDS if actual.get(key)!=expected.get(key)]
    if mismatches:
        raise ValueError('Proton source structure differs in: '+', '.join(mismatches))
    return {'matched_fields':list(STRUCTURE_FIELDS),
            'actual_blend_sha256':actual.get('source_blend_sha256'),
            'reference_blend_sha256':expected.get('source_blend_sha256'),
            'blend_hash_changed':actual.get('source_blend_sha256')!=expected.get('source_blend_sha256')}

def validate_model_contract(actual, expected):
    """Require the exact role-specific source model IDs and decoded hashes."""
    normalized={role:{'asset_id':actual[role]['asset_id'],'sha256':actual[role]['source_model_sha256']}
                for role in expected}
    if normalized!=expected:
        raise ValueError('Proton assembly model IDs/hashes differ')
    return normalized
