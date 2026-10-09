"""Reference graph backed by actual decoded path strings, never filename pairing."""
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from rc2016.common import asset_path, dump, file_hash, read, sha

SHARED_INDEX_DIR = Path(__file__).resolve().parents[2]/'.cache'/'shared_index'
try:
    _INDEX_SOURCE_SHA256 = sha(Path(__file__).read_bytes())
except OSError:              # frozen executable without .py sources
    _INDEX_SOURCE_SHA256 = 'frozen'

REFERENCE = re.compile(rb'[A-Za-z0-9_][A-Za-z0-9_ .@()\-/\\]*\.(?:actor|model|animset|animclip|fbx|texture|dds|materialgraph|material|config|conduit|visualeffect|performanceset)\x00', re.I)
TRAVERSE = {'actor', 'model', 'animset', 'material', 'materialgraph', 'config'}


def references(data):
    result=[]
    for match in REFERENCE.finditer(data):
        original=match.group()[:-1].decode('ascii')
        # Some real actor strings escape separators twice. Normalize interior separators for lookup only.
        value=re.sub(r'/+','/',original.replace('\\','/'))
        try:canonical=asset_path(value)
        except ValueError as exc:
            result.append(dict(path=original,byte_offset=match.start(),resolution_error=str(exc),evidence='invalid source reference'))
            continue
        result.append(dict(path=canonical,original_reference=original,byte_offset=match.start(),evidence='null-terminated source path'))
    return result


class AssetIndex:
    def __init__(self, archive):
        self.archive, self.graph, self.errors = archive, {}, []

    def scan(self, key):
        if key in self.graph:
            return self.graph[key]
        try:
            refs = references(read(self.archive.decode(key)))
            for ref in refs:
                ref['resolved_ids'] = [] if ref.get('resolution_error') else self.archive.resolve(ref['path'])
            self.graph[key] = refs
        except Exception as exc:
            self.errors.append(dict(asset_id=key, path=self.archive.entries[key]['path'], error=str(exc)))
            self.graph[key] = []
        return self.graph[key]

    def actor_index(self):
        # These tiny descriptors are the only global decode pass. No model/texture corpus expansion.
        actors = [e for e in self.archive.entries.values() if e['type'] == 'actor']
        for i, e in enumerate(actors):
            self.scan(e['id'])
            if i % 200 == 0:
                self.archive.log('actor_reference_index', completed=i, total=len(actors))
                self.archive.checkpoint()
        self.archive.checkpoint()

    def select(self, selector):
        normalized = selector.replace('\\', '/').lower()
        exact = [e for e in self.archive.entries.values() if e['type'] in ('actor','model') and
                 (e['path'] == normalized or Path(e['path']).stem == normalized)]
        models = [e['id'] for e in exact if e['type'] == 'model']
        if not models:
            for e in exact:
                for ref in self.scan(e['id']):
                    models += [k for k in ref['resolved_ids'] if self.archive.entries[k]['type'] == 'model']
        models = list(dict.fromkeys(models))
        if len(models) != 1:
            candidates = [e['path'] for e in self.archive.entries.values() if e['type'] == 'model' and normalized in e['path']]
            raise ValueError('Model selector must resolve exactly one model; use a full archive path. Candidates: '+repr(candidates[:50]))
        return models

    def related(self, model):
        actors = [key for key, refs in self.graph.items() if self.archive.entries[key]['type']=='actor'
                  and any(model in r['resolved_ids'] for r in refs)]
        queue, visited = [model, *actors], set()
        edges, unresolved = [], []
        while queue:
            key = queue.pop(0)
            if key in visited:
                continue
            visited.add(key)
            if self.archive.entries[key]['type'] not in TRAVERSE:
                continue
            for ref in self.scan(key):
                edges.append(dict(source=key, **ref))
                if not ref['resolved_ids']:
                    unresolved.append(dict(source=key, **ref, reason='No exact indexed asset path'))
                elif len(ref['resolved_ids']) > 1:
                    unresolved.append(dict(source=key, **ref, reason='Multiple platform/language assets; profile required'))
                else:
                    target = ref['resolved_ids'][0]
                    # An attached actor has its own animation library. Do not merge its clips into this model.
                    if self.archive.entries[target]['type'] == 'actor' and target not in actors:
                        visited.add(target)
                    else:
                        queue.append(target)
        classify = lambda kind: sorted((k for k in visited if self.archive.entries[k]['type'] in kind),
                                     key=lambda k:self.archive.entries[k]['order'])
        return dict(model=model, actors=actors, animsets=classify({'animset'}),
            attachment_models=[k for k in classify({'model'}) if k!=model], geometry=classify({'fbx'}),
            textures_materials=classify({'texture','dds','material','materialgraph'}),
            related_assets=classify(set(e['type'] for e in self.archive.entries.values())),
            unresolved=unresolved, reference_edges=edges)

    def save(self):
        """Store the archive-wide assets table once, shared, and this run's references locally.

        The assets table (~194k rows, ~55 MB) is identical for every run of one archive; it is
        stored once in SHARED_INDEX_DIR, named by a hash of its rows. references_found differs per
        run (~11k rows) and stays in reports/asset_references.sqlite. reports/asset_index.json
        names both; open_asset_index() presents them as the former single database.
        """
        reports = self.archive.output/'reports'
        rows = [(e['id'],e['path'],e['original_path'],e['type'],e['built_path'],e['language'],e['order'],
                 int(any(o['available'] for o in e['occurrences']))) for e in self.archive.entries.values()]
        refs = [(k,r['path'],r['byte_offset']) for k,refs_ in self.graph.items() for r in refs_]
        identity = getattr(self.archive, 'layout_identity', None)
        # The row tuples above are built here, so this file's source is part of their identity.
        write_split_index(reports, rows, refs, identity and sha((identity+_INDEX_SOURCE_SHA256).encode()))
        dump(reports/'index_errors.json', self.errors)


def write_split_index(reports, asset_rows, reference_rows, rows_identity=None):
    """Shared assets table (named by a hash of its sorted rows) plus per-run references.

    ``rows_identity`` (Archive.layout_identity) names rows already hashed once: the content
    hash is then read from a small shared_index sidecar instead of sorting/serializing ~194k
    rows again. Unchanged reference tables and pointers are left in place, not rewritten.
    """
    reports = Path(reports)
    SHARED_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    sidecar = SHARED_INDEX_DIR/f'rows_{rows_identity[:24]}.txt' if rows_identity else None
    content = None
    if sidecar and sidecar.is_file():
        known = sidecar.read_text(encoding='ascii').strip()
        if re.fullmatch(r'[0-9a-f]{64}', known) and (SHARED_INDEX_DIR/f'assets_{known[:16]}.sqlite').is_file():
            content = known
    rows = None
    if content is None:
        rows = sorted(tuple(r) for r in asset_rows)
        content = sha(json.dumps(rows, separators=(',', ':')).encode())
        if sidecar:
            try:sidecar.write_text(content, encoding='ascii')
            except OSError:pass
    shared = SHARED_INDEX_DIR/f'assets_{content[:16]}.sqlite'
    if not shared.is_file():
        if rows is None:
            rows = sorted(tuple(r) for r in asset_rows)
        temp = SHARED_INDEX_DIR/f'assets_building_{os.getpid()}_{uuid.uuid4().hex}.sqlite'
        db = sqlite3.connect(temp)
        with db:
            db.execute('CREATE TABLE assets (id TEXT PRIMARY KEY, path TEXT, original_path TEXT, type TEXT, built_path TEXT, language TEXT, archive_order INTEGER, available INTEGER)')
            db.executemany('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?)', rows)
            db.execute('CREATE INDEX asset_paths ON assets(path)')
        db.close()
        try:
            os.replace(temp, shared)
        except OSError:          # another run published the same content first
            temp.unlink(missing_ok=True)
    refs = reports/'asset_references.sqlite'
    reference_rows = [tuple(r) for r in reference_rows]
    if _stored_references(refs) != reference_rows:
        refs.unlink(missing_ok=True)
        db = sqlite3.connect(refs)
        with db:
            db.execute('CREATE TABLE references_found (source TEXT, target_path TEXT, byte_offset INTEGER)')
            db.executemany('INSERT INTO references_found VALUES (?,?,?)', reference_rows)
        db.close()
    pointer = dict(
        schema='ps4-split-asset-index-v1', assets=str(shared.resolve()), assets_rows_sha256=content,
        assets_file_sha256=file_hash(shared), references=refs.name,
        open_with='rc2016.formats.index.open_asset_index(output_root)')
    try:
        unchanged = json.loads((reports/'asset_index.json').read_text(encoding='utf-8')) == pointer
    except (OSError, ValueError):
        unchanged = False
    if not unchanged:
        dump(reports/'asset_index.json', pointer)
    (reports/'asset_index.sqlite').unlink(missing_ok=True)


def _stored_references(path):
    """Rows of an existing references table in insertion order, or None."""
    if not path.is_file():
        return None
    try:
        db = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
        try:
            return db.execute('SELECT source, target_path, byte_offset FROM references_found ORDER BY rowid').fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return None


def migrate_legacy_index(output_root):
    """Convert a run's legacy reports/asset_index.sqlite to the split layout (no data loss)."""
    reports = Path(output_root)/'reports'
    legacy = reports/'asset_index.sqlite'
    db = sqlite3.connect(f'file:{legacy.as_posix()}?mode=ro', uri=True)
    assets = db.execute('SELECT id,path,original_path,type,built_path,language,archive_order,available FROM assets').fetchall()
    refs = db.execute('SELECT source,target_path,byte_offset FROM references_found').fetchall()
    db.close()
    write_split_index(reports, assets, refs)
    check = open_asset_index(output_root)
    assert check.execute('SELECT count(*) FROM assets').fetchone()[0] == len(assets)
    assert sorted(check.execute('SELECT * FROM references_found').fetchall()) == sorted(refs)
    check.close()


def open_asset_index(output_root):
    """Read-only connection exposing tables `assets` and `references_found` for a run.

    Uses the split layout (shared assets + per-run references) when reports/asset_index.json
    exists, else a legacy per-run reports/asset_index.sqlite.
    """
    reports = Path(output_root)/'reports'
    pointer = reports/'asset_index.json'
    if not pointer.is_file():
        legacy = reports/'asset_index.sqlite'
        if not legacy.is_file():
            raise FileNotFoundError(f'No asset index in {reports}')
        return sqlite3.connect(f'file:{legacy.as_posix()}?mode=ro', uri=True)
    info = json.loads(pointer.read_text(encoding='utf-8'))
    assets = Path(info['assets'])
    if not assets.is_file() or file_hash(assets) != info['assets_file_sha256']:
        raise FileNotFoundError(f'Shared asset table is missing or changed: {assets}')
    db = sqlite3.connect(f'file:{assets.as_posix()}?mode=ro', uri=True)
    db.execute('ATTACH DATABASE ? AS run', (f'file:{(reports/info["references"]).as_posix()}?mode=ro',))
    db.execute('CREATE TEMP VIEW references_found AS SELECT * FROM run.references_found')
    return db
