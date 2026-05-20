"""Tests for ``strip_avatar_metadata``.

Spec: docs/superpowers/specs/2026-05-19-card-imports-design.md §7.
The strip preserves IHDR/IDAT/IEND + the ``chara`` / ``ccv3`` tEXt
chunks and drops everything else.
"""

from __future__ import annotations

import struct
import zlib

from grimoire.characters.ingest import strip_avatar_metadata


def _chunk(kind: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return length + kind + data + crc


def _png(chunks: list[bytes]) -> bytes:
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
    end = _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"".join(chunks) + idat + end


def _iter_chunks(payload: bytes) -> list[tuple[bytes, bytes]]:
    pos = 8
    end = len(payload)
    out: list[tuple[bytes, bytes]] = []
    while pos + 12 <= end:
        (length,) = struct.unpack(">I", payload[pos : pos + 4])
        kind = payload[pos + 4 : pos + 8]
        data = payload[pos + 8 : pos + 8 + length]
        out.append((kind, data))
        pos = pos + 8 + length + 4
        if kind == b"IEND":
            break
    return out


def test_strip_keeps_chara_chunk_drops_others() -> None:
    payload = _png(
        [
            _chunk(b"tEXt", b"chara\x00" + b"PAYLOAD"),
            _chunk(b"tEXt", b"Software\x00Photoshop"),
            _chunk(b"iCCP", b"profile\x00\x00..."),
        ]
    )
    stripped = strip_avatar_metadata(payload)
    chunks = _iter_chunks(stripped)
    text_keys = [data.split(b"\x00", 1)[0].decode() for kind, data in chunks if kind == b"tEXt"]
    kinds = {kind for kind, _ in chunks}
    assert "chara" in text_keys
    assert "Software" not in text_keys
    assert b"iCCP" not in kinds
    # IHDR / IDAT / IEND are preserved
    assert b"IHDR" in kinds
    assert b"IDAT" in kinds
    assert b"IEND" in kinds


def test_strip_keeps_ccv3_chunk() -> None:
    payload = _png(
        [
            _chunk(b"tEXt", b"ccv3\x00" + b"V3PAYLOAD"),
            _chunk(b"tEXt", b"Comment\x00created with X"),
        ]
    )
    stripped = strip_avatar_metadata(payload)
    chunks = _iter_chunks(stripped)
    text_keys = [data.split(b"\x00", 1)[0].decode() for kind, data in chunks if kind == b"tEXt"]
    assert "ccv3" in text_keys
    assert "Comment" not in text_keys


def test_strip_short_circuits_non_png_bytes() -> None:
    not_png = b"this is not a png"
    assert strip_avatar_metadata(not_png) == not_png


def test_strip_is_idempotent() -> None:
    payload = _png(
        [
            _chunk(b"tEXt", b"chara\x00X"),
            _chunk(b"tEXt", b"Software\x00Foo"),
        ]
    )
    once = strip_avatar_metadata(payload)
    twice = strip_avatar_metadata(once)
    assert once == twice
