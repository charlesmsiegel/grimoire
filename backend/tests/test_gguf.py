"""Tests for the pure-Python GGUF metadata reader."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from grimoire.gguf import GGUFError, introspect, read_metadata

GGUF_MAGIC = 0x46554747


def _write_string(parts: list[bytes], s: str) -> None:
    encoded = s.encode("utf-8")
    parts.append(struct.pack("<Q", len(encoded)))
    parts.append(encoded)


def _write_kv(parts: list[bytes], key: str, vtype: int, value: bytes) -> None:
    _write_string(parts, key)
    parts.append(struct.pack("<I", vtype))
    parts.append(value)


def make_gguf(metadata: dict[str, tuple[int, bytes]], version: int = 3) -> bytes:
    """Build a minimal GGUF file with the given metadata KV pairs."""
    parts: list[bytes] = []
    parts.append(struct.pack("<I", GGUF_MAGIC))
    parts.append(struct.pack("<I", version))
    parts.append(struct.pack("<Q", 0))  # tensor count
    parts.append(struct.pack("<Q", len(metadata)))  # kv count
    for key, (vtype, value) in metadata.items():
        _write_kv(parts, key, vtype, value)
    return b"".join(parts)


def _uint32(v: int) -> tuple[int, bytes]:
    return (4, struct.pack("<I", v))


def _string(v: str) -> tuple[int, bytes]:
    encoded = v.encode("utf-8")
    return (8, struct.pack("<Q", len(encoded)) + encoded)


def _bool(v: bool) -> tuple[int, bytes]:
    return (7, struct.pack("<?", v))


def test_read_metadata_basic(tmp_path: Path) -> None:
    data = make_gguf(
        {
            "general.architecture": _string("llama"),
            "general.name": _string("Test Model"),
            "llama.context_length": _uint32(4096),
            "llama.embedding_length": _uint32(2048),
        }
    )
    path = tmp_path / "test.gguf"
    path.write_bytes(data)

    meta = read_metadata(path)
    assert meta["general.architecture"] == "llama"
    assert meta["general.name"] == "Test Model"
    assert meta["llama.context_length"] == 4096
    assert meta["llama.embedding_length"] == 2048


def test_introspect(tmp_path: Path) -> None:
    data = make_gguf(
        {
            "general.architecture": _string("llama"),
            "general.name": _string("My GGUF Model"),
            "llama.context_length": _uint32(8192),
            "llama.embedding_length": _uint32(4096),
            "tokenizer.chat_template": _string("{% for msg in messages %}...{% endfor %}"),
        }
    )
    path = tmp_path / "model.gguf"
    path.write_bytes(data)

    info = introspect(path)
    assert info["architecture"] == "llama"
    assert info["name"] == "My GGUF Model"
    assert info["context_length"] == 8192
    assert info["embedding_length"] == 4096
    assert info["has_chat_template"] is True


def test_introspect_no_chat_template(tmp_path: Path) -> None:
    data = make_gguf(
        {
            "general.architecture": _string("bert"),
            "bert.context_length": _uint32(512),
            "bert.embedding_length": _uint32(768),
        }
    )
    path = tmp_path / "embed.gguf"
    path.write_bytes(data)

    info = introspect(path)
    assert info["architecture"] == "bert"
    assert info["has_chat_template"] is False
    assert info["context_length"] == 512
    assert info["embedding_length"] == 768


def test_bad_magic(tmp_path: Path) -> None:
    path = tmp_path / "bad.gguf"
    path.write_bytes(b"\x00\x00\x00\x00")
    with pytest.raises(GGUFError, match="not a GGUF file"):
        read_metadata(path)


def test_not_a_file(tmp_path: Path) -> None:
    with pytest.raises(GGUFError, match="not a file"):
        read_metadata(tmp_path / "nonexistent.gguf")
