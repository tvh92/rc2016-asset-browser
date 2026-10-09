"""Pure visibility contract for the Fusion Grenade geometry presentation scene."""

EXPECTED_VISIBLE_SECTIONS = {
    "V1": {"v1": {0, 1}, "v5": set()},
    "V5": {"v1": {0, 1}, "v5": {0, 1, 2}},
}


def assert_visible_source_sections(variant, actual):
    """Fail if a state omits V1 shared source geometry or includes wrong parts."""
    if variant not in EXPECTED_VISIBLE_SECTIONS:
        raise AssertionError(f"Unknown Fusion variant state: {variant}")
    expected = EXPECTED_VISIBLE_SECTIONS[variant]
    normalized = {key: set(actual.get(key, ())) for key in ("v1", "v5")}
    if normalized != expected:
        raise AssertionError(
            f"{variant} visible native source sections differ: expected {expected}, got {normalized}")
