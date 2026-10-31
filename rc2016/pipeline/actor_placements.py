"""Group .blend members placed where a zone places their actors (``run.py --group-actors ZONE:PREFIX``).

Some vehicles are several actors the game places together: each Pokitaru Hydro Harvester is a "locomotive" actor
(``moby1264``, the Harvester model) and five "tank segment" actors (``moby1265``, the Tank model) in
``gp_flight.zone``, named ``Hydroharvester #1 locomotive``, ``Hydroharvester #1 tank segment`` and so on. The
locomotive's ``ContainerShip`` component names only the trailer actor, a container group and a flight path, not
offsets, so the zone placements are the only decoded arrangement. Each matching actor becomes one member at its zone
matrix relative to an anchor actor, so the anchor's model stays where its own build puts it.

Matrices stay native (Y-up, game units, row vectors: ``p_world = p_local @ M``, see ``levels/actor_format.py``);
``group_blend.py`` converts them to the presentation basis. These are source placements, not runtime motion: the
game moves a convoy along its flight path.
"""
from __future__ import annotations

import numpy as np

from rc2016.levels.actor_format import decode_actor, decode_zone_actors


def _model_path(archive, actor_path):
    """Archive path of an actor's primary model (case folded), or None for an actor without one."""
    ids = [k for k in archive.resolve(actor_path.replace('\\', '/').lower()) if archive.entries[k]['type'] == 'actor']
    if len(ids) != 1:
        raise ValueError(f'Actor {actor_path} resolves to {len(ids)} assets')
    primary = decode_actor(archive.asset_data(ids[0])).get('primary_model')
    return primary.replace('\\', '/').casefold() if primary else None


def zone_placements(archive, zone_path, prefix, anchor_model):
    """Actors of ``zone_path`` whose placement name starts with ``prefix`` (case-insensitive), in zone order.

    Returns (rows, skipped). Each row has the actor's name, id, actor path, primary model path (case folded) and
    ``native_matrix``: its zone matrix relative to the anchor, the first matching actor whose primary model is
    ``anchor_model``, as nested lists (row-vector convention). Matching actors without a primary model (the convoy's
    ``Hydroharvester #1 path`` curve) are listed in ``skipped`` by name and actor path. Raises when nothing matches or
    no actor is the anchor.
    """
    ids = [k for k in archive.resolve(zone_path.replace('\\', '/').lower()) if archive.entries[k]['type'] == 'zone']
    if len(ids) != 1:
        raise ValueError(f'Zone {zone_path} resolves to {len(ids)} assets')
    wanted = prefix.casefold()
    actors = [a for a in decode_zone_actors(archive.asset_data(ids[0]))['actors']
              if (a['name'] or '').casefold().startswith(wanted)]
    if not actors:
        raise ValueError(f'No actor of {zone_path} is named {prefix}...')
    rows, skipped = [], []
    for actor in actors:
        model = _model_path(archive, actor['actor_path'])
        if model is None:
            skipped.append(dict(name=actor['name'], actor=actor['actor_path']))
            continue
        rows.append(dict(name=actor['name'], id=actor['id'], actor=actor['actor_path'], model=model,
                         matrix=np.array(actor['matrix_rows'], dtype=float)))
    anchor = next((row for row in rows if row['model'] == anchor_model.casefold()), None)
    if anchor is None:
        raise ValueError(f'No matching actor of {zone_path} places {anchor_model}')
    inverse = np.linalg.inv(anchor['matrix'])
    for row in rows:
        row['native_matrix'] = (row.pop('matrix') @ inverse).tolist()
    return rows, skipped


def lead_with_anchor(rows):
    """Rows with the anchor moved to the head of the other placed actors (``run.py --group-lead``), a viewing rule.

    The Pokitaru convoy placements are spawn positions, not a towing arrangement: convoys #1 and #3 put the five tanks
    along the locomotive's local +Z, #2 along its local +X, and #1 has the locomotive behind the tank whose tow arm
    points away from it. The tanks themselves hitch: each tank's arm reaches into the next tank's rear. So the others
    keep their placements, ordered by distance from the anchor, and the anchor goes one median spacing of that line
    beyond its far end, along the line from its nearest to its farthest member, with its own rotation. Every matrix is
    then re-expressed relative to the moved anchor, which stays at its own build's position. Needs two other actors.
    """
    anchor = next(i for i, row in enumerate(rows) if np.allclose(row['native_matrix'], np.eye(4)))
    others = sorted((row for i, row in enumerate(rows) if i != anchor),
                    key=lambda row: np.linalg.norm(np.array(row['native_matrix'])[3, :3]))
    if len(others) < 2:
        raise ValueError('--group-lead needs at least two other placed actors to give a spacing')
    points = [np.array(row['native_matrix'])[3, :3] for row in others]
    spacing = float(np.median([np.linalg.norm(b - a) for a, b in zip(points, points[1:])]))
    direction = (points[-1] - points[0]) / np.linalg.norm(points[-1] - points[0])
    head = points[-1] + direction * spacing
    moved = []
    for row in rows:
        matrix = np.array(row['native_matrix'])
        matrix[3, :3] -= head   # relative to an anchor translated by ``head`` (rotations are unchanged)
        moved.append(dict(row, native_matrix=matrix.tolist()))
    moved[anchor]['native_matrix'] = np.eye(4).tolist()
    moved[anchor]['lead'] = dict(spacing=spacing, zone_offset=head.tolist(),
                                 rule='anchor moved one median spacing beyond the far end of the other actors')
    return moved


def placement_labels(rows):
    """One unique collection label per placed actor: its name, numbered when several actors share it."""
    names = [row['name'] for row in rows]
    seen = {}
    labels = []
    for name in names:
        if names.count(name) > 1:
            seen[name] = seen.get(name, 0) + 1
            labels.append(f'{name} {seen[name]}')
        else:
            labels.append(name)
    return labels
