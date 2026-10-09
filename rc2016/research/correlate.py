"""Per-bit contingency counts and associations; these do not assign flag meanings."""
from collections import defaultdict
from pathlib import PurePosixPath
import math
import numpy as np

from rc2016.levels.zone_format import decode_zone
from rc2016.formats.index import references
from rc2016.formats.archive import decompress
from .source import namespace, level_name


class Correlations:
    def __init__(self):
        self.pools = {}
        self.features = {}

    def add(self, flags, features, pool='all'):
        flags = np.asarray(flags, dtype=np.uint32)
        row = self.pools.setdefault(pool, {'total': 0, 'bits': [0] * 32})
        row['total'] += len(flags)
        bit_masks = [(flags & (1 << bit)) != 0 for bit in range(32)]
        for bit, mask in enumerate(bit_masks):
            row['bits'][bit] += int(mask.sum())
        for name, feature in sorted(features.items()):
            feature = np.asarray(feature, dtype=bool)
            if len(feature) != len(flags):
                raise ValueError('Feature and flag counts differ')
            joint = self.features.setdefault((pool, name), {'positive': 0, 'joint': [0] * 32})
            joint['positive'] += int(feature.sum())
            for bit, mask in enumerate(bit_masks):
                joint['joint'][bit] += int((mask & feature).sum())

    def finish(self):
        results = []
        for (pool, name), feature in sorted(self.features.items()):
            population = self.pools[pool]
            n, positive = population['total'], feature['positive']
            if positive in (0, n):
                continue
            for bit in range(32):
                bit_count = population['bits'][bit]
                if bit_count in (0, n):
                    continue
                n11 = feature['joint'][bit]
                n10, n01 = bit_count - n11, positive - n11
                n00 = n - n11 - n10 - n01
                denom = math.sqrt(bit_count * (n - bit_count) * positive * (n - positive))
                phi = (n11 * n00 - n10 * n01) / denom
                mutual = 0.0
                for count, a, b in ((n11, bit_count, positive), (n10, bit_count, n - positive),
                                    (n01, n - bit_count, positive), (n00, n - bit_count, n - positive)):
                    if count:
                        mutual += count / n * math.log2(count * n / (a * b))
                results.append(dict(bit=bit, feature=name, pool=pool, observations=n,
                                    n00=n00, n01=n01, n10=n10, n11=n11, phi=phi,
                                    mutual_information=mutual, confidence='low',
                                    evidence='Observed association only; repeated placements are not independent samples'))
        results.sort(key=lambda r: (r['bit'], -abs(r['phi']), -r['mutual_information'], r['feature']))
        kept, counts = [], defaultdict(int)
        for row in results:
            key = (row['pool'], row['bit'])
            if counts[key] < 8:
                kept.append(row)
                counts[key] += 1
        return dict(pools=self.pools, associations=kept, comparisons=len(results),
                    caution='Exploratory comparisons; no causal interpretation or multiple-testing correction')


class ModelFeatures:
    """Optional metadata-only reads: use native table helpers, no geometry expansion."""
    def __init__(self, corpus):
        self.corpus = corpus
        self.models, self.materials = {}, {}
        self.errors = {}

    def load(self, path):
        if self.corpus.archive:
            keys = self.corpus.archive.resolve(path)
            if len(keys) != 1:
                raise ValueError(f'Expected one indexed asset for {path}, got {len(keys)}')
            raw, _ = self.corpus.archive.bytes(keys[0])
            return decompress(raw)[0]
        return (self.corpus.root / path).read_bytes()

    def get(self, path):
        if path in self.models:
            return self.models[path]
        try:
            from rc2016.formats import native_model as model
            data = self.load(path)
            table = model._sections(data)
            mesh_data = model._chunk(data, table, model.MESHES, True)
            if len(mesh_data) % model.MESH_STRIDE:
                raise ValueError('Mesh table has trailing bytes')
            meshes = [model._mesh_record(mesh_data, i) for i in range(len(mesh_data) // model.MESH_STRIDE)]
            features = {f'mesh_flag/0x{1 << bit:04X}' for bit in range(16)
                        if any(m['flags'] & (1 << bit) for m in meshes)}
            materials = model._materials(data, model._chunk(data, table, model.MATERIALS), table['_string_bounds'])
            used = {m['material_index'] for m in meshes}
            for material in materials:
                if material['index'] not in used or not material['path']:
                    continue
                material_path = material['path']
                if material_path not in self.materials:
                    try:
                        refs = references(self.load(material_path))
                        self.materials[material_path] = {r['path'] for r in refs if r['path'].endswith('.materialgraph')}
                    except Exception as exc:
                        self.errors[material_path] = str(exc)
                        self.materials[material_path] = None
                graphs = self.materials[material_path]
                if graphs is not None:
                    features.update('material_graph/' + p.removesuffix('.materialgraph') for p in graphs)
            result = {'features': features, 'graphs_complete': all(self.materials.get(m['path']) is not None
                       for m in materials if m['index'] in used and m['path'])}
        except Exception as exc:
            self.errors[path] = str(exc)
            result = None
        self.models[path] = result
        return result


def zone_features(asset, correlations, metadata=None):
    decoded = decode_zone(asset.data)  # Require the existing placement invariants before correlating.
    instances = decoded['instances']
    if not instances:
        return 0
    flags = np.array([int(i['flags'], 16) for i in instances], dtype=np.uint32)
    matrix = np.array([i['matrix_rows'] for i in instances], dtype=float)
    axes = matrix[:, :3, :3]
    scales = np.linalg.norm(axes, axis=2)
    cosines = []
    for a, b in ((0, 1), (0, 2), (1, 2)):
        denominator = scales[:, a] * scales[:, b]
        cosines.append(np.divide(np.abs((axes[:, a] * axes[:, b]).sum(axis=1)), denominator,
                                  out=np.zeros(len(axes)), where=denominator != 0))
    model_paths = [decoded['models'][i['model_index']].lower() for i in instances]
    stems = [PurePosixPath(p).stem for p in model_paths]
    features = {'mirrored_matrix': np.linalg.det(axes) < 0,
                'sheared_matrix': np.max(cosines, axis=0) > 1e-4,
                'scale_above_1': scales.max(axis=1) > 1.001,
                'scale_below_1': scales.min(axis=1) < .999,
                'record_tail': [bool(i['tail']) for i in instances]}
    for prefix in ('rb_', 'tb_', 'rp_', 'rk_', 'gbl_', 'enm_', 'hero_'):
        features['model_prefix/' + prefix] = [p.startswith(prefix) for p in stems]
    for suffix in ('_distant', '_tfrag', '_imposter'):
        features['model_contains/' + suffix] = [suffix in p for p in stems]
    pool = namespace(asset.path)
    zone = PurePosixPath(asset.path).stem
    features['zone/' + zone] = np.ones(len(instances), dtype=bool)
    features['zone_category/' + zone.split('_')[0]] = np.ones(len(instances), dtype=bool)
    features['level/' + level_name(asset.path)] = np.ones(len(instances), dtype=bool)
    correlations.add(flags, features, pool)
    if metadata:
        infos = [metadata.get(p) for p in sorted(set(model_paths))]
        by_path = dict(zip(sorted(set(model_paths)), infos))
        present = np.array([by_path[p] is not None for p in model_paths])
        known = [by_path[p] for p, valid in zip(model_paths, present) if valid]
        names = set().union(*(x['features'] for x in known)) if known else set()
        mesh_features = {name: [name in x['features'] for x in known] for name in names if name.startswith('mesh_flag/')}
        correlations.add(flags[present], mesh_features, pool + '/models_with_mesh_metadata')
        graphs_present = np.array([by_path[p] is not None and by_path[p]['graphs_complete'] for p in model_paths])
        known_graphs = [by_path[p] for p, valid in zip(model_paths, graphs_present) if valid]
        graph_features = {name: [name in x['features'] for x in known_graphs]
                          for name in names if name.startswith('material_graph/')}
        correlations.add(flags[graphs_present], graph_features, pool + '/models_with_complete_material_references')
    return len(instances)
