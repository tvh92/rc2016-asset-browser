"""Where a pilot sits in its vehicle when both are put in one group .blend.

A pilot is a separate actor that the vehicle's actor spawns (``NefMechBoss.Pilot`` in ``enm_nefarious_mech.actor``
names ``enm_nefarious_mech_pilot.actor``, which plays ``enm_nefarious_mech_pilot.animset``). Its clips are authored
in the frame of one vehicle joint and pair with the vehicle's clips by name (``Engaged_Idle_enm_Nefarious`` with
``Engaged_Idle_enm_Nefarious_Mech``). The joint is not named in the actor data (the boss code attaches the pilot), so
each entry records how it was established.

The group build (``group_blend.py``) puts the pilot's origin on that joint's posed frame (the game's joint frames;
their rest rotations are identity for these rigs) and starts each pilot clip with the vehicle clip of the same name.
"""
from __future__ import annotations

from pathlib import Path

NEFARIOUS = 'character/enemy/enm_nefarious/enm_nefarious.model'
NEFARIOUS_MECH = 'character/enemy/enm_nefarious_mech/enm_nefarious_mech.model'

# Nefarious's mech: with the pilot's origin on the mech's posed ``chest`` frame, his pelvis stays 0.42 above and 0.24
# in front of ``chair_seat_base`` (within 0.03) in 28 of the 35 clip pairs; the other seven (dance, knockback, run,
# take-off, landing) move him in the seat on purpose. Every other joint, the mech origin included, leaves the pelvis
# below the seat, above the dome or drifting from clip to clip.
PILOTS = (
    dict(name='Nefarious in his mech', vehicles=(NEFARIOUS_MECH,), pilot=NEFARIOUS, joint='chest',
         evidence='fitted: the pilot clips hold his pelvis on chair_seat_base only in the chest frame; '
                  'the attach joint is not in the actor data'),
)


def _key(path):
    return str(path).replace(chr(92), '/').casefold()


def clip_suffix(path):
    """The model's clip-name suffix: ``_enm_nefarious`` for ``.../enm_nefarious.model``."""
    return '_' + Path(_key(path)).stem


def pilot_mounts(paths):
    """{pilot path: dict(name, owner, joint, evidence, clips)} for each pilot in ``paths`` whose vehicle is also in
    ``paths`` (the first listed vehicle that is). ``clips`` holds the clip-name suffixes of both models."""
    keys = {_key(path): path for path in paths}
    found = {}
    for entry in PILOTS:
        pilot = keys.get(entry['pilot'])
        owner = next((keys[vehicle] for vehicle in entry['vehicles'] if vehicle in keys), None)
        if pilot is None or owner is None:
            continue
        found[pilot] = dict(name=entry['name'], owner=owner, joint=entry['joint'], evidence=entry['evidence'],
                            clips=dict(suffix=clip_suffix(pilot), owner_suffix=clip_suffix(owner)))
    return found
