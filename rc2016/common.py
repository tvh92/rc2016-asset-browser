"""Deterministic output, Windows path handling, and integrity helpers."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil


def settings_dir():
    """Per-user settings folder. Settings saved under the browser's old name are copied over once."""
    base = Path(os.environ.get('APPDATA', Path.home()))
    folder = base / 'RC2016 Asset Browser'
    old = base / 'PS4 Animation Extractor'
    if not folder.exists() and old.is_dir():
        shutil.copytree(old, folder)
    return folder


def _settings_file():
    return settings_dir() / 'settings.json'


def _read_settings_file():
    try:
        data = json.loads(_settings_file().read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_var(variable, key, allowed=None):
    """Restore a Tk variable from the settings file and save every later change under key.

    Booleans are stored as "1"/"0"; a stored text outside allowed is ignored, so the variable keeps
    its default.  Reading and writing the settings file are best-effort and never raise."""
    is_flag = isinstance(variable.get(), bool)
    stored = _read_settings_file().get(key)
    if isinstance(stored, str):
        value = stored == '1' if is_flag else stored
        if is_flag or allowed is None or value in allowed:
            variable.set(value)

    def save(*_args):
        value = variable.get()
        data = _read_settings_file()
        data[key] = ('1' if value else '0') if is_flag else str(value)
        try:
            path = _settings_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except OSError:
            pass

    variable.trace_add('write', save)
    return variable


def extended(path):
    p = os.path.abspath(path)
    if os.name != 'nt' or p.startswith('\\\\?\\'):
        return p
    return '\\\\?\\UNC\\' + p[2:] if p.startswith('\\\\') else '\\\\?\\' + p


def read(path):
    with open(extended(path), 'rb') as f:
        return f.read()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with open(extended(path), 'rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, data):
    path = Path(path)
    os.makedirs(extended(path.parent), exist_ok=True)
    temp = extended(str(path) + '.tmp')
    with open(temp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, extended(path))


def dump(path, value):
    write(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode())


def load(path):
    return json.loads(read(path))


def zip_json(path, value):
    write(path, gzip.compress(json.dumps(value, separators=(',', ':'), allow_nan=False).encode(), mtime=0))


def unzip_json(path):
    return json.loads(gzip.decompress(read(path)))


def safe_name(name):
    stem = re.sub(r'[^A-Za-z0-9_.-]', '_', name).strip(' .')[:64] or 'asset'
    if stem.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(10)), *('LPT'+str(i) for i in range(10))}:
        stem = '_' + stem
    return stem


def asset_path(value):
    value = value.strip('"').replace('\\', '/').lower()
    parts = value.split('/')
    if not value or any(p in ('', '.', '..') for p in parts) or ':' in value or value.startswith('/'):
        raise ValueError('Unsafe archive asset path: ' + value)
    return '/'.join(parts)


def disk_asset(value):
    # Preserve hierarchy and ordinary source names. Encode characters Windows cannot store.
    return '/'.join(''.join('%%%02X' % ord(c) if c in '<>:"|?*%' or ord(c) < 32 else c for c in p).rstrip(' .')
                    if p.split('.')[0].upper() not in {'CON','PRN','AUX','NUL',*('COM'+str(i) for i in range(10)),*('LPT'+str(i) for i in range(10))}
                    else '%5F'+p for p in asset_path(value).split('/'))


def ensure_separate(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('OutputRoot and ExtractedRoot must be disjoint directories')
    return source, output


def promote(candidate, final, force=False):
    """Atomic publication after validation, with an OS-level no-clobber operation."""
    candidate,final=Path(candidate),Path(final)
    if final.exists() and not force:
        raise FileExistsError('Final already exists; -Force is required: '+str(final))
    temp=final.with_suffix('.blend.tmp')
    shutil.copyfile(candidate,temp)
    if force:os.replace(temp,final)
    elif os.name=='nt':os.rename(temp,final)
    else:
        os.link(temp,final);temp.unlink()
