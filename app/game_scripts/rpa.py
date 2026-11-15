"""Read files out of Ren'Py archives (.rpa, versions 2.0 and 3.0).

The archive index is a pickle. A pickle from a game folder must never be trusted
(unpickling can run code), so it is loaded with an Unpickler that refuses every class:
only plain dicts, lists, tuples, strings, bytes and numbers get through.
"""
from __future__ import annotations

import io
import pickle
import zlib
from pathlib import Path


class ArchiveError(Exception):
    pass


import _codecs

# Python-2-compatible pickles (protocol 2, what Ren'Py writes) name these to rebuild
# byte strings. They only build data; everything else is refused.
_ALLOWED = {("builtins", "bytes"): bytes, ("__builtin__", "bytes"): bytes,
            ("builtins", "str"): str, ("__builtin__", "str"): str,
            ("__builtin__", "unicode"): str, ("_codecs", "encode"): _codecs.encode}


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        try:
            return _ALLOWED[(module, name)]
        except KeyError:
            raise ArchiveError(f"archive index wants {module}.{name}: refused") from None


def _safe_loads(data: bytes):
    return _SafeUnpickler(io.BytesIO(data)).load()


class RpaArchive:
    """Index of one .rpa file: {name: (offset, length, prefix)}."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            head = f.readline(512)
            try:
                parts = head.decode("latin-1").split()
            except Exception as e:
                raise ArchiveError("not an archive") from e
            if not parts or parts[0] not in ("RPA-3.0", "RPA-2.0"):
                raise ArchiveError(f"unsupported archive: {parts[:1]}")
            try:
                offset = int(parts[1], 16)
                key = int(parts[2], 16) if parts[0] == "RPA-3.0" else 0
            except (IndexError, ValueError) as e:
                raise ArchiveError("bad archive header") from e
            f.seek(offset)
            try:
                raw = _safe_loads(zlib.decompress(f.read()))
            except ArchiveError:
                raise
            except Exception as e:
                raise ArchiveError(f"bad archive index: {e}") from e
        if not isinstance(raw, dict):
            raise ArchiveError("bad archive index")
        self.index: dict[str, tuple[int, int, bytes]] = {}
        for name, entries in raw.items():
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            if not isinstance(name, str) or not entries:
                continue
            e = entries[0]
            o, ln = int(e[0]) ^ key, int(e[1]) ^ key
            pre = e[2] if len(e) > 2 and e[2] else b""
            if isinstance(pre, str):
                pre = pre.encode("latin-1")
            self.index[name.replace("\\", "/")] = (o, ln, pre)

    def names(self) -> list[str]:
        return list(self.index)

    def read(self, name: str) -> bytes:
        o, ln, pre = self.index[name]
        with open(self.path, "rb") as f:
            f.seek(o)
            return pre + f.read(ln)              # Ren'Py: prefix, then length bytes
