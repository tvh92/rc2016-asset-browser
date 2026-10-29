"""Where a projectile rests on its weapon when both are put in one group .blend.

A projectile model is built around its own origin, so on its own it lands at the world origin and not in the
weapon. The idle position is a point in the weapon's native frame (Y-up, game units); the group build moves
the projectile member there. Native values are kept here and converted to the presentation basis only when the
objects are moved (``group_blend.py``).
"""
from __future__ import annotations

# Each entry: weapon archive paths (case folded; the main model and its V1/V5 barrel models share one frame, so
# any of them selected is enough) and {projectile archive path (case folded): (x, y, z) of the projectile centre}.
#
# Bouncer: the bore axis is the line x = 0, y = 0.174 (the ``barrel`` joint's height) along +Z. The cup floor
# (main model) is at z = 0.774 and the rim (both barrel models) at z = 0.930; the V1 and V5 barrels share that
# bore, so one position serves both. The small ball's body is a sphere of radius 0.145 (its nozzles reach
# 0.192), the big one a smooth sphere of radius 0.156; resting on the floor puts each centre level with the rim.
IDLE_MOUNTS = (
    dict(weapons=('equipment/weapon/wpn_bouncer/wpn_bouncer.model',
                  'equipment/weapon/wpn_bouncer/bangle_set/bangle_v1/wpn_bouncer_bangle_v1.model',
                  'equipment/weapon/wpn_bouncer/bangle_set/bangle_v5/wpn_bouncer_bangle_v5.model'),
         projectiles={'equipment/projectile/proj_bouncer/proj_bouncer.model': (0.0, 0.174, 0.92),
                      'equipment/projectile/proj_bouncer_big/proj_bouncer_big.model': (0.0, 0.174, 0.93)}),
)


def idle_offsets(paths):
    """{path: native (x, y, z)} for each projectile in ``paths`` whose weapon is also in ``paths``."""
    keys = {str(path).replace(chr(92), '/').casefold(): path for path in paths}
    found = {}
    for mount in IDLE_MOUNTS:
        if not any(weapon in keys for weapon in mount['weapons']):
            continue
        for projectile, offset in mount['projectiles'].items():
            if projectile in keys:
                found[keys[projectile]] = offset
    return found
