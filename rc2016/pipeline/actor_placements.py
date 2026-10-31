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

import math

import numpy as np

from rc2016.levels.actor_format import decode_actor, decode_zone_actors, read_component
from rc2016.levels.curves import CURVE_RECORD_KIND, decode_curve, frame_matrix, sample


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
    """Rows with the other placed actors lined up behind the anchor (``run.py --group-lead``), a viewing rule.

    The Pokitaru convoy placements are spawn positions, not a towing arrangement: convoys #1 and #3 put the five tanks
    along the locomotive's local +Z, #2 along its local +X, #1 has the locomotive behind the tank whose tow arm points
    away from it, and the line drifts sideways (3.6 units over convoy #1). The tanks themselves hitch in a straight
    line: each tank's arm reaches into the next tank's rear. So the anchor leads and the others follow on one line:
    the anchor axis (local +-X, +-Y or +-Z) nearest the line from the nearest to the farthest other actor, one median
    spacing of that line apart, the farthest actor first behind the anchor and so on in reverse distance order. Every
    member keeps its own rotation; the anchor stays at its own build's position. Needs two other actors.
    """
    anchor = next(i for i, row in enumerate(rows) if np.allclose(row['native_matrix'], np.eye(4)))
    order = sorted((i for i in range(len(rows)) if i != anchor),
                   key=lambda i: np.linalg.norm(np.array(rows[i]['native_matrix'])[3, :3]))
    if len(order) < 2:
        raise ValueError('--group-lead needs at least two other placed actors to give a spacing')
    points = [np.array(rows[i]['native_matrix'])[3, :3] for i in order]
    spacing = float(np.median([np.linalg.norm(b - a) for a, b in zip(points, points[1:])]))
    line = points[-1] - points[0]
    axis = np.zeros(3)
    axis[int(np.argmax(np.abs(line)))] = np.sign(line[np.argmax(np.abs(line))])
    moved = [dict(row) for row in rows]
    for rank, i in enumerate(reversed(order), 1):
        matrix = np.array(rows[i]['native_matrix'])
        matrix[3, :3] = -axis * spacing * rank   # behind the anchor along its own axis
        moved[i]['native_matrix'] = matrix.tolist()
        moved[i]['lead_rank'] = rank
    moved[anchor]['lead'] = dict(spacing=spacing, axis=axis.tolist(),
                                 rule='others lined up behind the anchor along its nearest axis, median spacing apart, '
                                      'farthest first')
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


def flight_path_track(archive, zone_path, rows, speed, fps):
    """Per-frame native matrices of led rows (``lead_with_anchor``) flying the anchor's flight path (``--group-path``).

    The anchor's ``ContainerShip`` component in the zone names its ``FlightPath``, the id of a curve actor of the same
    zone (``Hydroharvester #1 path``); its spline is decoded by ``levels.curves``. The anchor starts at the curve point
    nearest its zone position and moves along increasing arc length at ``speed`` units per second for one lap (closed
    curve) or to the end (open); each other row trails ``lead_rank`` spacings behind along the curve. Every member's
    local +Z follows the curve tangent and +Y its up vector. Matrices are relative to the anchor's first-frame pose, so
    frame 1 puts the anchor where its own build does. The direction, speed and +Z-forward are viewing choices: the
    game's speed and travel direction are not decoded.

    Returns (tracks, info): tracks[i] is the list of row-vector matrices of rows[i]; info records the path, its length,
    the start distance, speed, fps and frame count, and ``preview``: curve points every unit, relative like the tracks.
    """
    ids = [k for k in archive.resolve(zone_path.replace('\\', '/').lower()) if archive.entries[k]['type'] == 'zone']
    data = archive.asset_data(ids[0])
    actors = {actor['id']: actor for actor in decode_zone_actors(data)['actors']}
    anchor = next(row for row in rows if 'lead' in row)
    flight = None
    for component in actors[anchor['id']]['components']:
        if component['name'] == 'ContainerShip':
            flight = (read_component(data, component) or {}).get('value', {}).get('FlightPath')
    if flight is None:
        raise ValueError(f"Anchor {anchor['name']} has no ContainerShip FlightPath")
    path = actors.get(f'{int(flight):016X}')
    if path is None or path['record_kind'] != CURVE_RECORD_KIND:
        raise ValueError(f'FlightPath {int(flight):016X} is not a curve actor of {zone_path}')
    curve = decode_curve(data, path['record_offset'], path['record_size'])
    to_zone = np.array(path['matrix_rows'], dtype=float)
    spawn = np.array(actors[anchor['id']]['matrix_rows'], dtype=float)[3, :3]
    local = (np.append(spawn, 1.0) @ np.linalg.inv(to_zone))[:3]
    total = float(curve['arc'][-1])
    candidates = np.linspace(0.0, total, max(int(total * 4), 2))
    start = float(min(candidates, key=lambda s: np.linalg.norm(sample(curve, s)[0] - local)))
    spacing = anchor['lead']['spacing']
    step = speed / fps
    frames = math.ceil(total / step) if curve['closed'] else max(math.floor((total - start) / step), 1) + 1

    def pose(distance):
        return frame_matrix(*sample(curve, distance)) @ to_zone

    origin = np.linalg.inv(pose(start))
    tracks = []
    for row in rows:
        behind = row.get('lead_rank', 0) * spacing
        tracks.append([np.round(pose(start + f * step - behind) @ origin, 6).tolist() for f in range(frames)])
    preview = [np.round((np.append(frame_matrix(*sample(curve, s))[3, :3], 1.0) @ to_zone @ origin)[:3], 4).tolist()
               for s in np.arange(0.0, total + 1e-6, 1.0)]
    info = dict(path=path['name'], path_id=path['id'], path_actor=path['actor_path'], length=total,
                closed=curve['closed'], start=start, speed=speed, fps=fps, frames=frames, preview=preview,
                rule='anchor from the curve point nearest its zone position along increasing arc length; others '
                     'trail by lead rank x spacing; +Z along the tangent; speed and direction are viewing choices')
    return tracks, info
