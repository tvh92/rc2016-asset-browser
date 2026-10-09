"""Guarded execution of vendored ALERT readers, plus schema-only serialization traces."""
import ast
from contextlib import redirect_stdout, redirect_stderr
import importlib
import inspect
import io
from pathlib import Path
import re
import struct
import sys
import textwrap
from functools import lru_cache

from .source import tag_name


class QuietBuffer(io.StringIO):
    def write(self, value):
        # Old readers print diagnostics. Retain a bounded warning, never a value dump.
        if self.tell() < 2048:
            return super().write(value[:2048 - self.tell()])
        return len(value)


def load_library():
    root = str(Path(__file__).resolve().parents[2] / 'third_party' / 'ALERT')
    added = root not in sys.path
    if added:
        sys.path.insert(0, root)
    try:
        return importlib.import_module('dat1lib')
    finally:
        if added:
            sys.path.remove(root)


@lru_cache(maxsize=None)
def class_info(cls):
    source = textwrap.dedent(inspect.getsource(cls))
    tree = ast.parse(source)
    strides = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ENTRY_SIZE'
                                                for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                strides.add(node.value.value)
    comment = re.search(r'TAG\s*=\s*0x[\da-fA-F]+\s*#\s*(.*)', source)
    return dict(class_name=cls.__name__, module=cls.__module__,
                comment_name=comment.group(1).strip(' "') if comment else None,
                entry_size=next(iter(strides)) if len(strides) == 1 else None)


def schema(value, prefix='$', result=None):
    """Flatten keys and types. Array indices and primitive values are never emitted."""
    if result is None:
        result = {}
    if len(result) >= 20000:
        raise ValueError('Schema path budget exceeded')
    kind = ('null' if value is None else 'boolean' if isinstance(value, bool)
            else 'object' if isinstance(value, dict) else 'array' if isinstance(value, list)
            else 'integer' if isinstance(value, int) else 'float' if isinstance(value, float)
            else 'string' if isinstance(value, str) else type(value).__name__)
    result.setdefault(prefix, set()).add(kind)
    if isinstance(value, dict):
        for key, item in sorted(value.items(), key=lambda x: str(x[0])):
            schema(item, prefix + '.' + str(key), result)
    elif isinstance(value, list):
        for item in value:
            schema(item, prefix + '[]', result)
    return result


class ExactStream(io.BytesIO):
    def read(self, count=-1):
        remaining = len(self.getbuffer()) - self.tell()
        if count < 0:
            count = remaining
        if count > remaining:
            raise ValueError(f'Serialized read beyond section: {count} > {remaining}')
        return super().read(count)


def guarded_serialized(base):
    class Guarded(base):
        def __init__(self, data, container):
            # Deliberately call the generic reader, avoiding the Zone_I16 bypass.
            super(base, self).__init__(data, container)
            self._debug_depth = 0
            self.frames, self.nodes, self.skipped_bytes = [], 0, 0
            stream = ExactStream(data)
            self.root = self._deserialize(stream)
            self.extras = []
            while stream.tell() < len(data):
                self.extras.append(self._deserialize(stream))
            self.consumed_bytes = stream.tell()

        def _deserialize_object(self, stream):
            if len(self.frames) >= 64:
                raise ValueError('Serialized nesting exceeds 64')
            pos = stream.tell()
            header = stream.read(16)
            zero, magic, count, length = struct.unpack('<IIII', header)
            if zero != 0 or magic not in (0x03150044, 0x07201969):
                raise ValueError(f'Unsupported serialized header {zero:#x}, {magic:#x}')
            if count > 100000 or length < count * 12 or pos + 16 + length > len(stream.getbuffer()):
                raise ValueError('Serialized object count/length outside section')
            frame = {'aligned_end': None}
            self.frames.append(frame)
            stream.seek(pos)
            try:
                value = super()._deserialize_object(stream)
                end = pos + 16 + length
                if stream.tell() != end:
                    raise ValueError('Serialized object exceeded declared length')
                skipped = end - frame['aligned_end']
                self.skipped_bytes += skipped
                return value
            finally:
                self.frames.pop()

        def _deserialize_node(self, stream, item_type):
            self.nodes += 1
            if self.nodes > 1000000:
                raise ValueError('Serialized node budget exceeded')
            supported = {0, 1, 2, 4, 5, 6, 8, 10, 13, 15, 17, 19}
            if self.version == 201800:
                supported |= {11, 45}
            if item_type not in supported:
                raise ValueError(f'Unsupported serialized node type {item_type:#x}')
            return super()._deserialize_node(stream, item_type)

        def _deserialize_string(self, stream):
            # Keep the vendored wire layout, but validate NUL and accept source UTF-8.
            # Schema emission discards these values after structural reading.
            fmt = '<III' if self.version == 201800 else '<IIQ'
            length, _, _ = struct.unpack(fmt, stream.read(struct.calcsize(fmt)))
            raw = stream.read(length)
            if stream.read(1) != b'\0':
                raise ValueError('Serialized string lacks declared NUL terminator')
            self._align(stream, 4)
            return raw.decode('utf-8')

        def _align(self, stream, alignment):
            super()._align(stream, alignment)
            if self.frames:
                self.frames[-1]['aligned_end'] = stream.tell()
    return Guarded


class StringContext:
    def __init__(self, asset, version):
        self.version, self.asset = version, asset

    def get_string(self, offset):
        offset = int(offset)
        data = self.asset.data
        table_end = 16 + 12 * len(self.asset.table)
        pool_end = min((off for off, _ in self.asset.table.values()), default=len(data))
        end = data.find(b'\0', offset, pool_end)
        if not table_end <= offset < pool_end or end <= offset or (offset > table_end and data[offset - 1] != 0):
            raise ValueError(f'Name offset {offset:#x} is not a string-pool start')
        value = data[offset:end].decode('utf-8')
        if not value.isprintable():
            raise ValueError(f'Name offset {offset:#x} is not printable')
        return value


class CrossReference:
    def __init__(self):
        self.library = load_library()
        from dat1lib.types.sections import SerializedSection, Section
        self.serialized = guarded_serialized(SerializedSection)
        self.classes, self.info = {}, {}
        for family in ('zone', 'level', 'zonestatic'):
            package = importlib.import_module('dat1lib.types.sections.' + family)
            folder = Path(package.__file__).parent
            for file in sorted(folder.glob('*.py')):
                if file.stem == '__init__':
                    continue
                module = importlib.import_module(package.__name__ + '.' + file.stem)
                for _, cls in inspect.getmembers(module, inspect.isclass):
                    if cls.__module__ != module.__name__ or not issubclass(cls, Section) or not cls.TAG:
                        continue
                    info = class_info(cls)
                    info['serialized'] = issubclass(cls, SerializedSection)
                    self.classes.setdefault(cls.TAG, []).append(cls)
                    self.info.setdefault(cls.TAG, []).append(info)

    def metadata(self, tag):
        return self.info.get(tag, [])

    def probe_class(self, cls, raw, context):
        diagnostic = QuietBuffer()
        try:
            info = class_info(cls)
            with redirect_stdout(diagnostic), redirect_stderr(diagnostic):
                instance = cls(raw, context)
            stride = info['entry_size']
            if stride is None:
                # Common parent readers declare strides rather than the concrete class.
                for parent in cls.__mro__[1:]:
                    if parent.__name__ == 'ReferencesSection':
                        stride = 16
                    if parent.__name__ == 'UintUintMapSection':
                        stride = 8
            leftover = len(raw) % stride if stride else None
            status = 'leftover_bytes' if leftover else 'parses_cleanly'
            parsed_attributes = sorted(k for k in vars(instance) if k not in ('_raw', '_dat1', 'version'))
            if not parsed_attributes:
                status = 'opaque_reader'
            return dict(class_name=cls.__name__, status=status, leftover_bytes=leftover,
                        consumption_evidence='fixed stride in reader' if stride else 'not measured',
                        parsed_attributes=parsed_attributes, warnings=bool(diagnostic.getvalue()))
        except Exception as exc:
            return dict(class_name=cls.__name__, status='raises', error=f'{type(exc).__name__}: {exc}')

    def probe(self, asset, tag):
        raw = asset.chunk(tag)
        context = StringContext(asset, self.library.VERSION_SO)
        results = []
        for cls in self.classes.get(tag, []):
            from dat1lib.types.sections import SerializedSection
            if not issubclass(cls, SerializedSection):
                results.append(self.probe_class(cls, raw, context))
        if any(i['serialized'] for i in self.metadata(tag)):
            # Both are hypotheses about a sibling-engine reader, not a detected game version.
            for version in (self.library.VERSION_SO, self.library.VERSION_RCRA):
                try:
                    diagnostic = QuietBuffer()
                    with redirect_stdout(diagnostic), redirect_stderr(diagnostic):
                        obj = self.serialized(raw, StringContext(asset, version))
                    tree = schema({'root': obj.root, 'extras': obj.extras})
                    results.append(dict(class_name='Generic SerializedSection', version_hypothesis=version,
                                        status='leftover_bytes' if obj.skipped_bytes else 'parses_cleanly',
                                        consumed_bytes=obj.consumed_bytes, leftover_bytes=obj.skipped_bytes,
                                        nodes=obj.nodes, schema={k: sorted(v) for k, v in sorted(tree.items())},
                                        warnings=bool(diagnostic.getvalue())))
                except Exception as exc:
                    results.append(dict(class_name='Generic SerializedSection', version_hypothesis=version,
                                        status='raises', error=f'{type(exc).__name__}: {exc}'))
        return results


class HashIndex:
    """ALERT CRC algorithms, including normalized asset paths and literal name strings."""
    def __init__(self, paths=()):
        load_library()
        self.maps = {4: {}, 8: {}}
        self.seen = set()
        self.add(paths)

    def add(self, strings):
        from dat1lib.crc32 import hash as crc32
        from dat1lib.crc64 import hash as crc64
        for string in sorted(set(strings)):
            if string in self.seen:
                continue
            self.seen.add(string)
            self._numeric_keys = {}
            for width, value, algorithm in ((4, crc32(string), 'crc32-normalized'),
                                            (4, crc32(string, False), 'crc32-literal'),
                                            (8, crc64(string), 'crc64-normalized')):
                self.maps[width].setdefault(value, (string, algorithm))

    def match(self, values, width):
        """Sorted lookup avoids repeatedly scanning all indexed hashes per field."""
        import numpy as np
        keys = getattr(self, '_numeric_keys', {}).get(width)
        if keys is None:
            if not hasattr(self, '_numeric_keys'):
                self._numeric_keys = {}
            keys = self._numeric_keys[width] = np.array(sorted(self.maps[width]), dtype=f'<u{width}')
        if not len(keys):
            return np.zeros(len(values), dtype=bool)
        indices = np.searchsorted(keys, values)
        valid = indices < len(keys)
        return valid & (keys[np.minimum(indices, len(keys) - 1)] == values)
