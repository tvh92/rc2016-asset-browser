"""GDeflate through Microsoft DirectStorage's CPU codec, loaded from the game's own install.

Rift Apart ships ``dstoragecore.dll`` (DirectStorage 1.2) beside the executable. Its
``DStorageCreateCompressionCodecCore(DSTORAGE_COMPRESSION_FORMAT_GDEFLATE, threads, IID, out)``
returns an ``IDStorageCompressionCodec``; vtable slot 4 is ``DecompressBuffer``. The front-end
``dstorage.dll`` export returns E_NOTIMPL for the same call, so the core DLL is used. Nothing is
vendored: without the DLL, GDeflate blocks fail with a clear message and LZ4 blocks still read.
"""
import ctypes
import os
import threading
import uuid
from pathlib import Path

FORMAT_GDEFLATE = 1
IID_CODEC = '84ef5121-9b43-4d03-b5c1-cc34606b262d'
DLL_NAME = 'dstoragecore.dll'
LOAD_WITH_ALTERED_SEARCH_PATH = 0x8


class GDeflateUnavailable(RuntimeError):
    pass


class _GUID(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]


_DECOMPRESS = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)) if os.name == 'nt' else None
_RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p) if os.name == 'nt' else None


class GDeflate:
    """One DirectStorage codec per calling thread (the codec's own thread pool decodes tiles)."""

    def __init__(self, folder, threads=None):
        self.path = Path(folder) / DLL_NAME
        if os.name != 'nt':
            raise GDeflateUnavailable('GDeflate needs Windows DirectStorage (%s)' % DLL_NAME)
        if not self.path.is_file():
            raise GDeflateUnavailable('GDeflate needs %s from the Rift Apart install; not found at %s'
                                      % (DLL_NAME, self.path))
        try:
            self._dll = ctypes.WinDLL(str(self.path), winmode=LOAD_WITH_ALTERED_SEARCH_PATH)
            self._create = self._dll.DStorageCreateCompressionCodecCore
        except (OSError, AttributeError) as error:
            raise GDeflateUnavailable('Cannot load the DirectStorage codec from %s: %s' % (self.path, error)) from None
        self._create.restype = ctypes.c_long
        self._create.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self._iid = _GUID()
        ctypes.memmove(self._iid.data, uuid.UUID(IID_CODEC).bytes_le, 16)
        self.threads = threads or max(1, min(4, os.cpu_count() or 1))
        self._local = threading.local()
        self._all, self._lock = [], threading.Lock()
        self._codec()     # fail now, not on the first texture

    def _codec(self):
        codec = getattr(self._local, 'codec', None)
        if codec is None:
            pointer = ctypes.c_void_p()
            hr = self._create(FORMAT_GDEFLATE, self.threads, ctypes.byref(self._iid), ctypes.byref(pointer))
            if hr != 0 or not pointer.value:
                raise GDeflateUnavailable('DStorageCreateCompressionCodecCore failed (HRESULT %08X)' % (hr & 0xFFFFFFFF))
            table = ctypes.cast(ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            codec = (pointer, _DECOMPRESS(table[4]), _RELEASE(table[2]))
            self._local.codec = codec
            with self._lock:
                self._all.append(codec)
        return codec

    def decompress(self, data, size):
        pointer, decompress, _release = self._codec()
        out = ctypes.create_string_buffer(size)
        produced = ctypes.c_size_t()
        hr = decompress(pointer, bytes(data), len(data), out, size, ctypes.byref(produced))
        if hr != 0:
            raise ValueError('GDeflate block failed (HRESULT %08X)' % (hr & 0xFFFFFFFF))
        if produced.value != size:
            raise ValueError('GDeflate block gave %d bytes, expected %d' % (produced.value, size))
        return out.raw

    def close(self):
        with self._lock:
            codecs, self._all = self._all, []
        for pointer, _decompress, release in codecs:
            release(pointer)
        self._local = threading.local()
