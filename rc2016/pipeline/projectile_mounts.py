"""Where a projectile rests on its weapon when both are put in one group .blend.

A projectile model is built around its own origin, so on its own it lands at the world origin and not in the
weapon. The idle position is a point in the weapon's native frame (Y-up, game units); the group build moves
the projectile member there. Native values are kept here and converted to the presentation basis only when the
objects are moved (``group_blend.py``).

A rack carries several copies of the projectile on one weapon joint (the Warmonger's loaded rockets): each copy is a
native row-vector matrix in the weapon's bind pose, and the group build parents the copies to that joint so they
follow its clips.
"""
from __future__ import annotations

import math

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

WARMONGER = 'equipment/weapon/wpn_warmonger/wpn_warmonger.model'
WARMONGER_V1 = 'equipment/weapon/wpn_warmonger/bangle_set/wpn_warmonger_bangle_v1_barrel/wpn_warmonger_bangle_v1_barrel.model'
WARMONGER_V5 = 'equipment/weapon/wpn_warmonger/bangle_set/wpn_warmonger_bangle_v5_barrel/wpn_warmonger_bangle_v5_barrel.model'
WARMONGER_ROCKET = 'equipment/projectile/proj_warmonger/proj_warmonger.model'

# Each rack: the barrel model that selects it, the weapon models whose armature carries ``joint`` (first delivered one
# wins: the body when it is selected, since the barrel part is then skinned to the body's rig), the projectile, and
# a ring of copies about the weapon's +Z axis. A copy at roll r (degrees) sits at centre + radius (-sin r, cos r, 0)
# with its local +Y pointing away from the axis (its frame rolled by r about +Z), at unit scale.
#
# Peacemaker (Warmonger V5): a shadPS4 capture of the V5 in Ratchet's hand draws eight proj_warmonger copies (one
# instanced G-buffer draw, eight 272-byte instance records). Their world matrices relative to the weapon's record,
# taken back to the bind pose through the captured barrel joint pose (the barrel was 6.35 mm below its rest in that
# frame, and the copies sat concentric with it), give this ring: centre on the ``barrel`` joint axis (0, 0.21018), in
# the eight channels at 45-degree steps from the top. The weapon config gives the V5 eight rockets (kAmmo 8).
#
# Warmonger V1: not captured. Its barrel has four channels, centred at 45, 135, 225 and 315 degrees; the copies take
# the V5 centre and roll convention, and the radius that gives the same missile-to-channel-floor gap as the captured V5
# (floor 0.136 against 0.176). An inference, so the rack is labelled as such. The config gives the V1 six rockets
# (kAmmo 6), which the four channels do not hold; four copies follow the geometry.
RACKS = (
    dict(name='Peacemaker (Warmonger V5) rocket rack', tag='V5', barrel=WARMONGER_V5, joint='barrel',
         joint_owners=(WARMONGER, WARMONGER_V5), projectile=WARMONGER_ROCKET,
         centre=(0.0, 0.21016, 0.46211), radius=0.20544, rolls=(0.0, -45.0, -90.0, -135.0, 180.0, 135.0, 90.0, 45.0),
         evidence='captured: Research/captures/2026-11-01_peacemaker-v5 (instance records of the instanced rocket draw)'),
    dict(name='Warmonger V1 rocket rack', tag='V1', barrel=WARMONGER_V1, joint='barrel',
         joint_owners=(WARMONGER, WARMONGER_V1), projectile=WARMONGER_ROCKET,
         centre=(0.0, 0.21016, 0.46211), radius=0.166, rolls=(-45.0, -135.0, 135.0, 45.0),
         evidence='inferred: V5 centre and roll convention, radius from the V1 channel floor; not captured'),
)


def _key(path):
    return str(path).replace(chr(92), '/').casefold()


def idle_offsets(paths):
    """{path: native (x, y, z)} for each projectile in ``paths`` whose weapon is also in ``paths``."""
    keys = {_key(path): path for path in paths}
    found = {}
    for mount in IDLE_MOUNTS:
        if not any(weapon in keys for weapon in mount['weapons']):
            continue
        for projectile, offset in mount['projectiles'].items():
            if projectile in keys:
                found[keys[projectile]] = offset
    return found


def rack_matrix(centre, radius, roll):
    """Native row-vector matrix (``p_weapon = p_local @ M``) of one rack copy at ``roll`` degrees."""
    t = math.radians(roll)
    c, s = math.cos(t), math.sin(t)
    x, y, z = centre
    return [[c, s, 0.0, 0.0], [-s, c, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [x - radius * s, y + radius * c, z, 1.0]]


def rack_placements(paths):
    """{projectile path: [rack, ...]} for each projectile in ``paths`` with a rack whose barrel model is also in
    ``paths``, in ``RACKS`` order (V5, then V1, when both barrels are selected for one weapon group).

    A rack is dict(name, tag, joint, owner (the path whose armature carries the joint), matrices, evidence)."""
    keys = {_key(path): path for path in paths}
    found = {}
    for rack in RACKS:
        projectile = keys.get(rack['projectile'])
        if projectile is None or rack['barrel'] not in keys:
            continue
        owner = next(keys[weapon] for weapon in rack['joint_owners'] if weapon in keys)
        found.setdefault(projectile, []).append(dict(
            name=rack['name'], tag=rack['tag'], joint=rack['joint'], owner=owner, evidence=rack['evidence'],
            matrices=[rack_matrix(rack['centre'], rack['radius'], roll) for roll in rack['rolls']]))
    return found
