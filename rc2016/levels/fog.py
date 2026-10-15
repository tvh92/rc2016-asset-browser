"""Approximate distance haze for the Level atmosphere lighting (data only, no GL).

This is a viewer approximation, not decoded game data. The atmosphere's own scattering tables and its
fog inputs are undecoded (the header fields 84 and 156 are unresolved), so the haze is a plain
exponential extinction toward the sky's horizon colour:

    colour = mix(horizon, surface, exp(-density * distance))

``distance`` is the view-space distance in level units. The surface colour is the lit colour before
exposure and tonemap, in the same units as the sky dome, so the haze and the sky agree on exposure.
``FOG_DENSITY`` was tuned by eye on one in-game Novalis screenshot (distant mountains and city turn
hazy blue-grey while the near terrain stays unchanged); it is not a game value.
"""

# Extinction per level unit. exp(-density * d) is 0.5 at about 0.69 / density units.
FOG_DENSITY = 0.0005


def horizon_colour(profile):
    """RGB of the lowest-elevation bin of a clear-sky profile (``sky.clear_sky_profile``), or None."""
    if not profile:
        return None
    return tuple(float(x) for x in profile[0])


def fog_transmittance(distance, density=FOG_DENSITY):
    """Fraction of the surface colour that survives to the camera at ``distance`` (1 at 0, towards 0 far away)."""
    import math
    return math.exp(-max(float(density), 0.0) * max(float(distance), 0.0))


def apply_fog(colour, distance, fog_colour, density=FOG_DENSITY):
    """CPU reference of the shader's blend: ``fog + (colour - fog) * transmittance`` per channel."""
    t = fog_transmittance(distance, density)
    return tuple(f + (c - f) * t for c, f in zip(colour, fog_colour))


def fog_state(sky, lighting, enabled=True, density=FOG_DENSITY):
    """(density, RGB) uniform values for a frame: haze only for the Level atmosphere lighting with a
    captured clear-sky profile and ``enabled``; otherwise density 0 (no fog)."""
    colour = horizon_colour((sky or {}).get('clear_sky'))
    if not enabled or lighting != 'Level atmosphere' or colour is None:
        return 0.0, (0.0, 0.0, 0.0)
    return float(density), colour
