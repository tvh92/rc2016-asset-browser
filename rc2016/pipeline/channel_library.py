"""Single-pass exact allocation: omit only scalar channels invariant across the library.

A channel becomes active on its first exact float32 deviation from source rest.
That discovery backfills every preceding Action with its constant reset curve.
Thus every channel that can move has a curve in EVERY Action, including rest.
Only channels that never move are absent; no Action-switch reset assumption.
"""
import json
import numpy as np


STATE_KEY = 'source_channel_library_state'


class ChannelLibrary:
    def __init__(self, rig, actions, add_curve):
        self.rig = rig
        self.actions = actions
        self.add_curve = add_curve
        self.state = json.loads(rig.get(STATE_KEY, '{}')) or {
            'schema': 'source-invariant-channel-library-v1', 'channels': [], 'actions': []}
        self.by_key = {(c['path'], c['index']): c for c in self.state['channels']}
        if len(self.by_key)!=len(self.state['channels']):
            raise ValueError('Duplicate channel in persisted library state')
        for name in self.state['actions']:
            if name not in actions: raise ValueError('Missing persisted library Action: '+name)
        self.seen = set()

    def emit(self, bag, path, index, values):
        values = np.asarray(values, dtype=np.float32)
        if not len(values) or not np.isfinite(values).all():
            raise ValueError('Empty or nonfinite source channel: '+path)
        key = path, index
        if key in self.seen: raise ValueError('Duplicate channel emitted: '+path)
        self.seen.add(key)
        channel = self.by_key.get(key)
        if channel is None:
            if self.state['actions']:
                raise ValueError('Library channel domain changed: '+path)
            if not np.all(values == values[0]):
                raise ValueError('Initialize the library with the single-frame source rest Action')
            channel = dict(path=path, index=index, value=float(values[0]), active=False)
            self.state['channels'].append(channel)
            self.by_key[key] = channel
            self.rig.path_resolve(path)[index] = channel['value']
        if not channel['active'] and not np.all(values == np.float32(channel['value'])):
            for name in self.state['actions']:
                action = self.actions[name]
                previous_bag = action.layers[0].strips[0].channelbags[0]
                self.add_curve(previous_bag, path, index, [channel['value']])
            channel['active'] = True
        if channel['active']:
            self.add_curve(bag, path, index, values)

    def finish(self, action):
        if self.seen != set(self.by_key):
            raise ValueError('Action has incomplete scalar channel domain: '+action.name)
        if action.name in self.state['actions']:
            raise ValueError('Action already in channel library: '+action.name)
        self.state['actions'].append(action.name)
        action['source_channel_library'] = self.state['schema']
        self.rig[STATE_KEY] = json.dumps(self.state, separators=(',', ':'), allow_nan=False)
        self.seen.clear()

    def summary(self):
        active = sum(c['active'] for c in self.state['channels'])
        return dict(schema=self.state['schema'], actions=len(self.state['actions']),
                    scalar_channels=len(self.state['channels']), active_channels=active,
                    invariant_channels=len(self.state['channels'])-active,
                    full_curve_count=len(self.state['channels'])*len(self.state['actions']),
                    retained_curve_count=active*len(self.state['actions']),
                    comparison='exact float32 equality; no tolerance or quantization')


def validate_channel_library(rig, actions):
    """Assert complete reset coverage for moving channels in every saved Action."""
    library=ChannelLibrary(rig,actions,lambda *args:None)
    expected={(c['path'],c['index']) for c in library.state['channels'] if c['active']}
    curves=keys=0
    for name in library.state['actions']:
        action=actions[name]
        bag=action.layers[0].strips[0].channelbags[0]
        actual={(c.data_path,c.array_index) for c in bag.fcurves}
        assert actual==expected and len(bag.fcurves)==len(expected),('Incomplete reset-channel coverage',name)
        assert action.use_fake_user,('Library Action is not persistent',name)
        curves+=len(bag.fcurves);keys+=sum(len(c.keyframe_points) for c in bag.fcurves)
    for c in library.state['channels']:
        if not c['active']:
            assert rig.path_resolve(c['path'])[c['index']]==np.float32(c['value']),('Invariant channel default changed',c['path'],c['index'])
    return dict(**library.summary(),fcurves=curves,keyframes=keys)
