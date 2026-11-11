"""Synthetic PNG character cards, built with the standard library. Every picture is a few grey pixels made
here and every card is one of ``card_fixtures``' invented ones: no real card is ever stored in the repository."""

from __future__ import annotations

import base64
import json
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, body: bytes = b"", *, crc: int | None = None) -> bytes:
    """One PNG chunk; ``crc`` overrides the checksum (to build a damaged one)."""
    found = zlib.crc32(body, zlib.crc32(kind)) & 0xFFFFFFFF if crc is None else crc
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", found)


def text_chunk(keyword: str, text: bytes) -> bytes:
    return chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + text)


def picture_chunks(shade: int = 128, size: int = 2) -> list[bytes]:
    """The chunks of a valid ``size`` x ``size`` 8-bit greyscale picture of one ``shade``."""
    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    rows = b"".join(b"\x00" + bytes([shade]) * size for _ in range(size))
    return [chunk(b"IHDR", header), chunk(b"IDAT", zlib.compress(rows)), chunk(b"IEND")]


def png(*extra: bytes, shade: int = 128) -> bytes:
    """A valid PNG with ``extra`` chunks between its header and its picture data."""
    header, data, end = picture_chunks(shade)
    return SIGNATURE + header + b"".join(extra) + data + end


def embed(document: object) -> bytes:
    """A card document as a text chunk holds it: base64 of its JSON."""
    raw = document if isinstance(document, bytes) else json.dumps(document, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw)


def png_card(document: object = None, *, keyword: str = "ccv3", shade: int = 128, **chunks: object) -> bytes:
    """A PNG card holding ``document`` under ``keyword``; ``chunks`` adds more ``keyword=document`` text chunks."""
    found = [] if document is None else [text_chunk(keyword, embed(document))]
    found += [text_chunk(key, embed(value)) for key, value in chunks.items()]
    return png(*found, shade=shade)


def as_v2(document: dict) -> dict:
    """A V3 fixture as a Character Card V2 document: the same fields under the older spec."""
    data = {key: value for key, value in document["data"].items()
            if key not in ("assets", "group_only_greetings", "nickname", "source", "creation_date", "modification_date")}
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}
