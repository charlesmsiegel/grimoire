"""Pure-Python GGUF metadata reader.

Reads key-value metadata from the GGUF file header without loading
tensors or requiring llama-cpp-python.  Understands GGUF v2 and v3.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

GGUF_MAGIC = 0x46554747  # "GGUF" little-endian

# Value-type tags (GGUF spec).
_UINT8 = 0
_INT8 = 1
_UINT16 = 2
_INT16 = 3
_UINT32 = 4
_INT32 = 5
_FLOAT32 = 6
_BOOL = 7
_STRING = 8
_ARRAY = 9
_UINT64 = 10
_INT64 = 11
_FLOAT64 = 12

_SCALAR_FMT: dict[int, str] = {
    _UINT8: "<B",
    _INT8: "<b",
    _UINT16: "<H",
    _INT16: "<h",
    _UINT32: "<I",
    _INT32: "<i",
    _FLOAT32: "<f",
    _BOOL: "<?",
    _UINT64: "<Q",
    _INT64: "<q",
    _FLOAT64: "<d",
}


class GGUFError(Exception):
    pass


def _read(f: Any, fmt: str) -> Any:
    size = struct.calcsize(fmt)
    data = f.read(size)
    if len(data) < size:
        raise GGUFError("unexpected end of file")
    return struct.unpack(fmt, data)[0]


_MAX_STRING_BYTES = 100 * 1024 * 1024  # 100 MiB
_MAX_ARRAY_ELEMENTS = 1_000_000


def _read_string(f: Any) -> str:
    length = _read(f, "<Q")
    if length > _MAX_STRING_BYTES:
        raise GGUFError(f"string length {length} exceeds safety cap")
    data = f.read(length)
    if len(data) < length:
        raise GGUFError("unexpected end of file reading string")
    return data.decode("utf-8", errors="replace")


def _read_value(f: Any, vtype: int) -> Any:
    if vtype == _STRING:
        return _read_string(f)
    if vtype == _ARRAY:
        elem_type = _read(f, "<I")
        count = _read(f, "<Q")
        if count > _MAX_ARRAY_ELEMENTS:
            raise GGUFError(f"array count {count} exceeds safety cap")
        return [_read_value(f, elem_type) for _ in range(count)]
    fmt = _SCALAR_FMT.get(vtype)
    if fmt is None:
        raise GGUFError(f"unknown value type {vtype}")
    return _read(f, fmt)


def read_metadata(path: str | Path) -> dict[str, Any]:
    """Return all metadata key-value pairs from a GGUF file."""
    path = Path(path)
    if not path.is_file():
        raise GGUFError(f"not a file: {path}")

    with open(path, "rb") as f:
        magic = _read(f, "<I")
        if magic != GGUF_MAGIC:
            raise GGUFError(f"not a GGUF file (magic 0x{magic:08x})")
        version = _read(f, "<I")
        if version not in (2, 3):
            raise GGUFError(f"unsupported GGUF version {version}")
        _tensor_count = _read(f, "<Q")
        kv_count = _read(f, "<Q")

        metadata: dict[str, Any] = {}
        for _ in range(kv_count):
            key = _read_string(f)
            vtype = _read(f, "<I")
            value = _read_value(f, vtype)
            metadata[key] = value

    return metadata


def introspect(path: str | Path) -> dict[str, Any]:
    """Return a curated summary of a GGUF model's metadata."""
    meta = read_metadata(path)

    arch = meta.get("general.architecture", "")
    ctx_key = f"{arch}.context_length" if arch else None
    embed_key = f"{arch}.embedding_length" if arch else None

    has_chat_template = bool(meta.get("tokenizer.chat_template"))

    return {
        "architecture": arch or None,
        "name": meta.get("general.name") or None,
        "context_length": meta.get(ctx_key) if ctx_key else None,
        "embedding_length": meta.get(embed_key) if embed_key else None,
        "has_chat_template": has_chat_template,
        "file_type": meta.get("general.file_type"),
        "quantization_version": meta.get("general.quantization_version"),
    }
