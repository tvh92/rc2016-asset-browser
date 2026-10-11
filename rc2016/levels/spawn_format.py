"""Bound factory inputs and an explicitly approximate enemy inspection preview.

No script evaluation or runtime placement claims. Model identity comes exclusively
from the referenced template actor's decoded primary-model pointer.
"""
import copy
import math
import struct
from collections import Counter

from rc2016.formats.container import game_crc32
from rc2016.levels.actor_format import _table, _chunk, _string

NODES = 0x5E54ACCF
LINKS = 0xBEAB52E7
PARAMETERS = 0xD86A7934
NAMES = 0xEF8637D5
GROUPS = 0x0CF58A6E
MEMBERS = 0x041071EF
FACTORY = 'SunsetSpawnerFactoryScriptAction'
TEMPLATES = 0xEDD5E282
LOCATIONS = 0x951BD5B6
INITIAL_COUNT = 0x90D607B0
POLICY = 'experimental: round-robin templates/locations; centred 2.5-unit XZ grid at repeated anchors; template linear matrix retained'


def decode_factories(data, actors):
    """Decode bounded literal factory inputs; preserve unsupported bindings as reasons.

    Node records are <QHHHHHHIII>, bindings <III>, parameters <HHHHQQ>.
    The node payload is not evaluated. Counts driven by other nodes stay unresolved.
    """
    table = _table(data)
    names = [off for (off,) in struct.iter_unpack('<I', _chunk(data, table, NAMES, 4))]
    links = list(struct.iter_unpack('<III', _chunk(data, table, LINKS, 12)))
    params = list(struct.iter_unpack('<HHHHQQ', _chunk(data, table, PARAMETERS, 24)))
    members = [x for (x,) in struct.iter_unpack('<H', _chunk(data, table, MEMBERS, 2))]
    groups = {}
    group_receipts = {}
    for _index, start, packed_count, pad, ident in struct.iter_unpack('<IIIIQ', _chunk(data, table, GROUPS, 24)):
        count = packed_count & 0xFFFF
        if pad != 0xFFFFFFFF or start + count > len(members):
            raise ValueError('Actor group range or sentinel invalid')
        indices = members[start:start + count]
        if any(i >= len(actors) for i in indices) or ident in groups:
            raise ValueError('Actor group member or duplicate ID invalid')
        groups[ident] = indices
        group_receipts[ident] = dict(section=f'{GROUPS:08X}', offset=table[GROUPS][0] + 24 * len(group_receipts),
                                    members_section=f'{MEMBERS:08X}', member_start=start,
                                    count=count, flags_raw=packed_count >> 16)
    result = []
    for i, row in enumerate(struct.iter_unpack('<QHHHHHHIII', _chunk(data, table, NODES, 32))):
        ident, _inputs, _outstart, _outputs, first, count, action, crc, offset, size = row
        if action >= len(names):
            raise ValueError(f'Script node {i}: action name/CRC invalid')
        name = _string(data, table, names[action])
        if game_crc32(name) != crc:
            raise ValueError(f'Script node {i}: action name/CRC invalid')
        if count and first + count > len(links):
            raise ValueError(f'Script node {i}: binding range invalid')
        if name != FACTORY:
            continue
        factory = dict(node=i, id=f'{ident:016X}', action=FACTORY,
                       section=f'{NODES:08X}', offset=table[NODES][0] + i * 32,
                       payload_offset=offset, payload_size=size, bindings=[], reasons=[])
        for pin, param_index, tail in links[first:first + count]:
            binding = dict(pin=f'{pin:08X}', parameter=param_index, tail=f'{tail:08X}',
                           section=f'{LINKS:08X}', offset=table[LINKS][0] + 12 * (first + len(factory['bindings'])))
            if param_index >= len(params):
                raise ValueError(f'Factory {i}: parameter index invalid')
            kind, name, target, pad, value, extra = params[param_index]
            binding.update(kind=kind, value=f'{value:016X}', actor_indices=[],
                           parameter_section=f'{PARAMETERS:08X}', parameter_offset=table[PARAMETERS][0] + 24 * param_index,
                           actor_index_raw=target)
            if pin in (TEMPLATES, LOCATIONS):
                if kind != 5:
                    binding['reason'] = 'nonliteral actor input'
                elif target != 0xFFFF:
                    if target >= len(actors) or actors[target]['id'] != f'{value:016X}':
                        raise ValueError(f'Factory {i}: actor index/ID mismatch')
                    binding['actor_indices'] = [target]
                elif value in groups:
                    binding['actor_indices'] = groups[value]
                    binding['group'] = group_receipts[value]
                else:
                    binding['reason'] = 'external or unresolved actor/group reference'
            elif pin == INITIAL_COUNT:
                literal = struct.unpack('<f', struct.pack('<I', value & 0xFFFFFFFF))[0]
                if kind == 2 and math.isfinite(literal) and literal >= 0 and literal.is_integer():
                    binding['count'] = int(literal)
                else:
                    binding['reason'] = 'nonliteral or nonintegral initial count'
            factory['bindings'].append(binding)
        for pin, key in ((TEMPLATES, 'templates'), (LOCATIONS, 'locations')):
            bindings = [b for b in factory['bindings'] if b['pin'] == f'{pin:08X}']
            factory[key] = [index for b in bindings for index in b['actor_indices']]
            factory['reasons'].extend(b['reason'] for b in bindings if b.get('reason'))
            if not factory[key]:
                factory['reasons'].append('no bound ' + key)
        counts = [b for b in factory['bindings'] if b['pin'] == f'{INITIAL_COUNT:08X}']
        factory['initial_count'] = counts[0].get('count') if len(counts) == 1 else None
        if factory['initial_count'] is None:
            factory['reasons'].append('initial count not a single literal')
        result.append(factory)
    return result


def preview_placements(factories, zone_actors, descriptor):
    """Approximate all bound enemy factories together, regardless of encounter state.

    A location's volume/path linear matrix is never used as enemy scale. Only
    its translation anchors the explicit inspection grid. Template matrices and
    source records remain unchanged. No factory expands without a structural link.
    """
    from rc2016.levels.actors import KNOWN_UNSUPPORTED, DRAW_COMPONENTS
    from rc2016.levels.actor_format import IDENTITY
    placements, reports = [], []
    for source in factories:
        actors = zone_actors[source['zone']]
        templates = [actors[i] for i in source['templates']]
        # Namespace selects the preview scope, never the actor-to-model binding.
        if not any(a['actor_path'].lower().startswith('character/enemy/') for a in templates):
            continue
        report = dict(source, policy=POLICY, emitted=0)
        reports.append(report)
        reasons = list(source['reasons'])
        for template in templates:
            desc = descriptor(template['actor_path'])
            if not template.get('model') or not template.get('binding'):
                reasons.append('template has no structural primary model')
            elif template['model'].lower() in KNOWN_UNSUPPORTED:
                reasons.append('known unsupported model')
            local = desc.get('matrix_rows')
            if local and tuple(x for row in local for x in row) != IDENTITY:
                reasons.append('nonidentity actor-local transform unproven')
            if not template['actor_path'].lower().startswith('character/enemy/'):
                reasons.append('mixed enemy/nonenemy templates')
            unknown = sorted({c['name'] for c in desc.get('drawing_components', []) + template.get('drawing_components', [])
                              if c['name'] in DRAW_COMPONENTS})
            if unknown:
                reasons.append('template drawing component unproven: ' + ', '.join(unknown))
        report['reasons'] = sorted(set(reasons))
        if reasons:
            continue
        locations = [actors[i] for i in source['locations']]
        count = source['initial_count']
        populations = Counter(i % len(locations) for i in range(count))
        for i in range(count):
            slot = i % len(locations)
            location, template = locations[slot], templates[i % len(templates)]
            ordinal, population = i // len(locations), populations[slot]
            width = math.ceil(math.sqrt(population))
            height = math.ceil(population / width)
            shift = (2.5 * (ordinal % width - (width - 1) / 2), 0.,
                     2.5 * (ordinal // width - (height - 1) / 2))
            actor = copy.deepcopy(template)
            actor.pop('drawn', None)
            actor['reason'] = None
            actor['id'] = f"preview:{source['id']}:{i}"
            actor['name'] = f"Experimental spawn {i + 1}: {template['name'] or template['actor_path']}"
            actor['matrix_rows'][3][:3] = [location['matrix_rows'][3][j] + shift[j] for j in range(3)]
            actor['spawn_preview'] = dict(factory_id=source['id'], factory_node=source['node'],
                factory_section=source['section'], factory_offset=source['offset'],
                template_id=template['id'], template_record_offset=template['record_offset'],
                location_id=location['id'], location_actor_path=location['actor_path'],
                location_record_offset=location['record_offset'], source_anchor=location['matrix_rows'][3][:3],
                inspection_offset=shift, policy=POLICY)
            placements.append(actor)
            report['emitted'] += 1
    return placements, reports


def preview_summary(placements, reports):
    reasons = Counter(reason for r in reports for reason in r['reasons'])
    reasons.update(a['reason'] for a in placements if a.get('reason'))
    return dict(experimental=True, policy=POLICY, factories=len(reports),
                requested=sum(r['initial_count'] or 0 for r in reports), generated=len(placements),
                drawn=sum(bool(a.get('drawn')) for a in placements),
                unresolved_factories=sum(bool(r['reasons']) for r in reports),
                unresolved=sum((r['initial_count'] or 0) for r in reports if r['reasons']) +
                           sum(bool(a.get('reason')) for a in placements), reasons=dict(reasons))
