"""Archive-wide links between assets: which models use a texture, which levels and zones place a
model, and which shading rule each material of a model gets in the viewer.

``reports/asset_references.sqlite`` only records actor -> model references, so this module reads
the archive once and stores the other edges in ``.cache/shared_index/links_<identity>.sqlite``:

- model -> material (the model file's material references, in file order);
- material -> texture and material -> material graph (the material file's references);
- material graph -> texture (graph defaults);
- zone -> model with its instance count (the zone's model-instance table, docs/levels.md);
- level -> zone (the `.level` zone list).

The identity covers the archive layout and the source of build_links and SCHEMA, so a different
archive or a change to what is stored rebuilds the table; query changes do not. A full build reads about 20,000 assets and takes about half a minute.
Shading-rule coverage is not stored: it is derived from the stored graph paths and the viewer's
GRAPH_RULES when asked, so a new rule shows up without a rebuild.
"""
from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
import sqlite3
import uuid

from rc2016.common import asset_path
from rc2016.formats.archive import decompress
from rc2016.formats.index import SHARED_INDEX_DIR, references

SCHEMA = """
CREATE TABLE model_materials (model TEXT, material TEXT, position INTEGER);
CREATE TABLE material_refs (material TEXT, target TEXT, kind TEXT);
CREATE TABLE graph_textures (graph TEXT, texture TEXT);
CREATE TABLE zone_models (zone TEXT, model TEXT, instances INTEGER);
CREATE TABLE level_zones (level TEXT, zone TEXT);
CREATE TABLE errors (path TEXT, error TEXT);
CREATE INDEX model_materials_model ON model_materials(model);
CREATE INDEX model_materials_material ON model_materials(material);
CREATE INDEX material_refs_material ON material_refs(material);
CREATE INDEX material_refs_target ON material_refs(target);
CREATE INDEX graph_textures_texture ON graph_textures(texture);
CREATE INDEX zone_models_model ON zone_models(model);
CREATE INDEX level_zones_zone ON level_zones(zone);
"""

# Coverage kinds, from most to least specific (docs/viewer.md, "_g meaning per material graph").
EXACT, FAMILY, GENERIC, NO_GRAPH = 'exact rule', 'family fallback', 'generic reading', 'no material graph'
COVERAGE_KINDS = (EXACT, FAMILY, GENERIC, NO_GRAPH)


def rule_coverage(graphs):
    """(kind, matched GRAPH_RULES prefix or None) for a material's graph paths.

    Mirrors rc2016.viewer.textures._graph_rule (first matching rule wins). A prefix that ends in a
    dot names one exact graph (a decoded writer); other prefixes are family fallbacks; no match is
    the generic reading (coloured F0 + gloss alpha)."""
    from rc2016.viewer.textures import GRAPH_RULES
    if not graphs:
        return NO_GRAPH, None
    for prefix, _rule in GRAPH_RULES:
        if any(g.startswith(prefix) for g in graphs):
            return (EXACT if prefix.endswith('.') else FAMILY), prefix
    return GENERIC, None


def links_path(archive):
    try:
        source = inspect.getsource(build_links) + SCHEMA
    except OSError:   # frozen executable without sources
        source = 'frozen'
    identity = hashlib.sha256(f'{archive.layout_identity}:{source}'.encode()).hexdigest()
    return SHARED_INDEX_DIR / f'links_{identity[:16]}.sqlite'


def _canonical(path):
    try:
        return asset_path(path)
    except ValueError:
        return None


def _paths(archive, kind):
    return sorted(e['path'] for e in archive.entries.values() if e['type'] == kind and
                  any(o.get('available') for o in e.get('occurrences', [{'available': True}])))


def _data(archive, path, kind):
    ids = [k for k in archive.resolve(path) if archive.entries[k]['type'] == kind]
    if len(ids) != 1:
        raise ValueError(f'{path}: expected one {kind} entry, found {len(ids)}')
    return decompress(archive.bytes(ids[0])[0])[0]


def build_links(archive, progress=None, cancel=None):
    """Read every model, material, graph, zone and level of the archive and write the links table.

    ``progress(done, total, stage)`` is called now and then; ``cancel()`` returning True stops the
    build without writing. Returns the table's path."""
    from rc2016.levels.level_format import decode_level
    from rc2016.levels.zone_format import decode_zone
    jobs = [(kind, path) for kind in ('model', 'material', 'materialgraph', 'zone', 'level') for path in _paths(archive, kind)]
    rows = {name: [] for name in ('model_materials', 'material_refs', 'graph_textures', 'zone_models', 'level_zones', 'errors')}
    for done, (kind, path) in enumerate(jobs):
        if cancel and cancel():
            return None
        if progress and done % 500 == 0:
            progress(done, len(jobs), kind)
        try:
            data = _data(archive, path, kind)
            if kind == 'zone':
                try:
                    zone = decode_zone(data)
                except ValueError as exc:
                    if 'lacks instance sections' in str(exc):
                        continue   # sound, nav and gameplay zones place no models
                    raise
                counts = {}
                for instance in zone['instances']:
                    counts[instance['model_index']] = counts.get(instance['model_index'], 0) + 1
                for index, count in counts.items():
                    model = _canonical(zone['models'][index])
                    if model:
                        rows['zone_models'].append((path, model, count))
                continue
            if kind == 'level':
                for zone in decode_level(data)['zones']:
                    zone = _canonical(zone)
                    if zone:
                        rows['level_zones'].append((path, zone))
                continue
            found = [r['path'] for r in references(data) if not r.get('resolution_error')]
            if kind == 'model':
                materials = [p for p in dict.fromkeys(found) if p.endswith('.material')]
                rows['model_materials'] += [(path, m, i) for i, m in enumerate(materials)]
            elif kind == 'material':
                for target in dict.fromkeys(found):
                    if target.endswith('.texture'):
                        rows['material_refs'].append((path, target, 'texture'))
                    elif target.endswith('.materialgraph'):
                        rows['material_refs'].append((path, target, 'graph'))
            else:
                rows['graph_textures'] += [(path, t) for t in dict.fromkeys(found) if t.endswith('.texture')]
        except Exception as exc:   # one unreadable asset must not stop the index
            rows['errors'].append((path, f'{type(exc).__name__}: {exc}'))
    if progress:
        progress(len(jobs), len(jobs), 'writing')
    target = links_path(archive)
    SHARED_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    temporary = SHARED_INDEX_DIR / f'links_building_{os.getpid()}_{uuid.uuid4().hex}.sqlite'
    db = sqlite3.connect(temporary)
    with db:
        db.executescript(SCHEMA)
        for table, values in rows.items():
            if values:
                db.executemany(f'INSERT INTO {table} VALUES ({",".join("?" * len(values[0]))})', values)
    db.close()
    try:
        os.replace(temporary, target)
    except OSError:   # another process published the same table first
        temporary.unlink(missing_ok=True)
    return target


class AssetLinks:
    """Read-only queries over a links table. Paths are canonical archive paths (lower case, '/')."""

    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(f'file:{self.path.as_posix()}?mode=ro', uri=True, check_same_thread=False)

    @classmethod
    def open(cls, archive, build=False, progress=None, cancel=None):
        """The archive's links table, building it first when ``build`` is set; None if absent."""
        path = links_path(archive)
        if not path.is_file():
            if not build:
                return None
            path = build_links(archive, progress, cancel)
            if path is None:
                return None
        return cls(path)

    def close(self):
        self.db.close()

    def _all(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    def texture_users(self, texture):
        """[{'model', 'materials': [material paths], 'via_graph': True if only graph defaults reach it}],
        sorted by model path."""
        texture = texture.lower()
        direct = self._all('SELECT mm.model, mm.material FROM material_refs r JOIN model_materials mm ON mm.material = r.material '
                           "WHERE r.target = ? AND r.kind = 'texture'", texture)
        graph = self._all('SELECT mm.model, mm.material FROM graph_textures g JOIN material_refs r ON r.target = g.graph '
                          "AND r.kind = 'graph' JOIN model_materials mm ON mm.material = r.material WHERE g.texture = ?", texture)
        users = {}
        for rows, via_graph in ((direct, False), (graph, True)):
            for model, material in rows:
                user = users.setdefault(model, dict(model=model, materials=[], via_graph=via_graph))
                user['via_graph'] = user['via_graph'] and via_graph
                if material not in user['materials']:
                    user['materials'].append(material)
        return [users[k] for k in sorted(users)]

    def model_placements(self, model):
        """[{'zone', 'instances', 'levels': [the .level files listing the zone]}], most instances first."""
        zones = {}
        for level, zone, count in self._all('SELECT l.level, z.zone, z.instances FROM zone_models z '
                                            'LEFT JOIN level_zones l ON l.zone = z.zone WHERE z.model = ?', model.lower()):
            entry = zones.setdefault(zone, dict(zone=zone, instances=count, levels=[]))
            if level and level not in entry['levels']:
                entry['levels'].append(level)
        return sorted(zones.values(), key=lambda p: (-p['instances'], p['zone']))

    def model_materials(self, model):
        return [m for (m,) in self._all('SELECT material FROM model_materials WHERE model = ? ORDER BY position', model.lower())]

    def material_graphs(self, material):
        return [g for (g,) in self._all("SELECT target FROM material_refs WHERE material = ? AND kind = 'graph'", material.lower())]

    def coverage(self, model):
        """[{'material', 'graphs', 'kind', 'rule'}] for each material the model references."""
        result = []
        for material in self.model_materials(model):
            graphs = self.material_graphs(material)
            kind, rule = rule_coverage(graphs)
            result.append(dict(material=material, graphs=graphs, kind=kind, rule=rule))
        return result

    def models_by_coverage(self):
        """{model path: set of coverage kinds of its materials}, for every model with materials."""
        graphs = {}
        for material, graph in self._all("SELECT material, target FROM material_refs WHERE kind = 'graph'"):
            graphs.setdefault(material, []).append(graph)
        kinds, cache = {}, {}
        for model, material in self._all('SELECT model, material FROM model_materials'):
            key = tuple(graphs.get(material, ()))
            if key not in cache:
                cache[key] = rule_coverage(list(key))[0]
            kinds.setdefault(model, set()).add(cache[key])
        return kinds

    def errors(self):
        return self._all('SELECT path, error FROM errors ORDER BY path')


__all__ = ['AssetLinks', 'COVERAGE_KINDS', 'EXACT', 'FAMILY', 'GENERIC', 'NO_GRAPH', 'build_links', 'links_path', 'rule_coverage']
