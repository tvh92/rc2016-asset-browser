"""Source-placement actor layer; primary model only, no runtime execution.

Ambiguous drawing components and referenced attachment/spawn templates fail closed.
Dependencies do not imply inheritance. See docs/levels.md for the preview boundary.
"""
from array import array
from collections import Counter, defaultdict
import hashlib
import itertools
import math

from rc2016.levels.actor_format import decode_actor, decode_zone_actors, read_component, IDENTITY
from rc2016.levels.scene import asset_bytes, mirrored, resolve_overrides, hidden_by_default
from rc2016.research.source import Asset

KNOWN_UNSUPPORTED = frozenset((
    'character/npc/npc_skidd_mcmarx/npc_skidd_mcmarx.model',
    'character/npc/npc_skidds_agent/npc_skidds_agent.model',
    'character/npc/npc_ryno_dealer/npc_ryno_dealer.model',
))
AMBIGUOUS_PLACEMENT = frozenset((
    'environment/nebula/architecture/rv_neb_arch_airlock_door/rv_neb_arch_airlock_door.model',
))
# The payload proves these can affect drawing; runtime defaults/order are unproven.
DRAW_COMPONENTS = frozenset((
    'ScalePrototyping', 'ScaleOverTime', 'PositionOffset', 'CurveFollowerAttach',
    'MaterialOverrideComponent', 'StaticMaterialOverrideList', 'StaticMaterialOverride',
    'MaterialOverrideSyncComponent', 'RenderOverrideConstantSyncComponent',
    'TriggerResponderChangeMaterial', 'AttachmentList', 'HoverboardAttach',
))
REFERENCE_COMPONENTS = frozenset(('ActorAttachment', 'SpawnerObject', 'SpawnerBot', 'SpawnerBotStatic'))


def _references(value, keys):
    """Only typed integer actor references, never arbitrary numbers or string matches."""
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys:
                values = child if isinstance(child, list) else [child]
                result.update(f'{x:016X}' for x in values if isinstance(x, int) and not isinstance(x, bool) and x)
            result.update(_references(child, keys))
    elif isinstance(value, list):
        for child in value:
            result.update(_references(child, keys))
    return result


class ActorCatalog:
    """One load's cached descriptors and source placements with audit receipts."""
    def __init__(self, archive):
        self.archive, self.descriptors, self.actors = archive, {}, []
        self.references = defaultdict(set)
        self.static = defaultdict(list)
        self.duplicates = []
        self.factories, self.zone_actors = [], {}

    def descriptor(self, path):
        key = path.lower()
        if key not in self.descriptors:
            if not self.archive.resolve(path):
                self.descriptors[key] = dict(reason='missing actor asset')
            else:
                data = asset_bytes(self.archive, path)
                decoded = decode_actor(data)
                decoded['sha256'] = hashlib.sha256(data).hexdigest()
                decoded['drawing_components'] = self.components(data, path, decoded['components'])
                self.descriptors[key] = decoded
        return self.descriptors[key]

    def components(self, data, path, components):
        context, result = None, []
        for component in components:
            if component['name'] not in DRAW_COMPONENTS | REFERENCE_COMPONENTS:
                continue
            if context is None:
                context = Asset(path, data)
            result.append(dict(component, decoded=read_component(data, component, asset=context)))
        return result

    def add_zone(self, path, static_scene):
        data = asset_bytes(self.archive, path)
        zone = decode_zone_actors(data)
        components = self.components(data, path, zone['components'])
        by_index = {c['definition_offset']: c for c in components}
        digest = hashlib.sha256(data).hexdigest()
        for actor in zone['actors']:
            actor = dict(actor, zone=path, zone_sha256=digest)
            actor['drawing_components'] = [by_index[c['definition_offset']] for c in actor['components'] if c['definition_offset'] in by_index]
            descriptor = self.descriptor(actor['actor_path'])
            actor['model'] = descriptor.get('primary_model')
            actor['binding'] = descriptor.get('primary_reference')
            actor['actor_sha256'] = descriptor.get('sha256')
            for c in actor['drawing_components']:
                root = (c.get('decoded') or {}).get('value', {})
                for ident in _references(root, {'AttachedActor', 'AttachedList'}):
                    self.references[ident].add('attachment placement unproven')
                for ident in _references(root, {'SpawnTemplate', 'SpawneeTemplate'}):
                    self.references[ident].add('spawn template, not an initial placement')
            self.actors.append(actor)
        from rc2016.levels.spawn_format import decode_factories
        placed = self.actors[-len(zone['actors']):] if zone['actors'] else []
        self.zone_actors[path] = placed
        self.factories.extend(dict(f, zone=path, zone_sha256=digest) for f in decode_factories(data, placed))
        for model in static_scene['models']:
            for key, rows in model['groups'].items():
                for i in range(0, len(rows), 16):
                    matrix = tuple(rows[i:i+16])
                    cell = tuple(math.floor(matrix[x] / .05) for x in (12,13,14))
                    self.static[(model['path'].lower(), cell)].append((path, matrix, key[1]))
        return len(zone['actors'])

    def classify(self, actor):
        descriptor = self.descriptors[actor['actor_path'].lower()]
        model = actor.get('model')
        reason = descriptor.get('reason')
        if not reason and not model:
            reason = 'no primary render model'
        if model:
            lower = model.lower()
            if lower in KNOWN_UNSUPPORTED:
                reason = 'known unsupported model: ' + lower
            elif lower in AMBIGUOUS_PLACEMENT:
                reason = 'airlock placement disagrees with static control'
            elif lower.startswith('visualeffect/') or actor['actor_path'].lower().startswith('visualeffect/'):
                reason = 'VFX outside actor layer'
            elif tuple(x for row in descriptor['matrix_rows'] for x in row) != IDENTITY:
                reason = 'nonidentity actor-local transform unproven'
            elif self.references.get(actor['id']):
                reason = '; '.join(sorted(self.references[actor['id']]))
            else:
                components = descriptor['drawing_components'] + actor['drawing_components']
                unknown = sorted({c['name'] for c in components if c['name'] in DRAW_COMPONENTS})
                if unknown:
                    reason = 'drawing component unproven: ' + ', '.join(unknown)
            matrix = tuple(x for row in actor['matrix_rows'] for x in row)
            cell = tuple(math.floor(matrix[x] / .05) for x in (12,13,14))
            for delta in itertools.product((-1,0,1), repeat=3):
                adjacent = tuple(a+b for a,b in zip(cell,delta))
                for zone, other, overrides in self.static.get((lower, adjacent), ()):
                    distance = math.dist(matrix[12:15],other[12:15])
                    if distance >= .05:
                        continue
                    difference = max(abs(a-b) for a,b in zip(matrix,other))
                    receipt = dict(actor_zone=actor['zone'], id=actor['id'], name=actor['name'],
                                   model=model, static_zone=zone, translation_distance=distance,
                                   max_matrix_delta=difference, same_matrix=difference < 1e-6)
                    self.duplicates.append(receipt)
                    if difference >= 1e-6:
                        reason = reason or 'coincident static transform differs (unproven)'
                    if difference < 1e-6:
                        # Conservative suppression even when materials differ; report rather than double draw.
                        reason = reason or 'coincident static model (actor suppressed)'
        actor['reason'] = reason
        return reason

    def candidates(self):
        self.duplicates.clear()
        for actor in self.actors:
            self.classify(actor)
        ambiguous = {d['model'].lower() for d in self.duplicates if not d['same_matrix']}
        for actor in self.actors:
            if (actor.get('model') or '').lower() in ambiguous and not actor['reason']:
                actor['reason'] = 'model class has a conflicting static placement control'
        return [a for a in self.actors if a['reason'] is None]

    def summary(self):
        reasons = Counter(a.get('reason') for a in self.actors if a.get('reason'))
        classes = Counter((a['reason'], a['actor_path'], a.get('model') or '') for a in self.actors if a.get('reason'))
        drawn = [a for a in self.actors if a.get('drawn')]
        return dict(actors=len(self.actors), bound=sum(bool(a.get('model')) for a in self.actors),
                    resolved=len(drawn), drawn=sum(not hidden_by_default(a['model']) for a in drawn),
                    hidden=sum(hidden_by_default(a['model']) for a in drawn), rigid=sum(not a.get('skinned') for a in drawn),
                    skinned=sum(bool(a.get('skinned')) for a in drawn), unresolved=sum(reasons.values()),
                    reasons=dict(reasons), unresolved_classes=[dict(reason=r, actor_path=p, model=m or None, count=n)
                                                          for (r,p,m),n in sorted(classes.items())],
                    skipped_skinned=sum(bool(a.get('skip_skin')) or (a.get('model') or '').lower() in KNOWN_UNSUPPORTED for a in self.actors),
                    duplicates=self.duplicates)


def actor_groups(actors, materials):
    """Reuse exact per-instance slot resolution and retain identity alongside each full matrix."""
    groups, replacements = {}, set()
    for actor in actors:
        raw = tuple((o['slot_hash'],o['material'],o['material_hash']) for o in actor['material_overrides'])
        changes, outcomes = resolve_overrides(materials, raw)
        actor['material_outcomes'] = outcomes
        key = (mirrored(actor['matrix_rows']), changes)
        groups.setdefault(key, array('f')).extend(x for row in actor['matrix_rows'] for x in row)
        actor['group_key'] = key
        replacements.update(path for _slot,path in changes)
    return groups, replacements
