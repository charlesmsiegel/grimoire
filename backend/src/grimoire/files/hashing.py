"""Content hashing.

A single helper used by the State Store / Library indexer to detect actual
content changes independently of file mtime. SHA-256 over UTF-8 bytes with
line endings normalized to ``\\n`` so a file rewritten with CRLF won't be
treated as changed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def _normalize(data: str | bytes) -> bytes:
    encoded = data.encode("utf-8") if isinstance(data, str) else data
    return encoded.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def content_hash(data: str | bytes | Path) -> str:
    """Return the lowercase hex SHA-256 of ``data``.

    Accepts a string, bytes, or a ``Path`` (which is read as UTF-8 bytes).
    Line endings are normalized to ``\\n`` before hashing.
    """
    if isinstance(data, Path):
        raw: str | bytes = data.read_bytes()
    else:
        raw = data
    return hashlib.sha256(_normalize(raw)).hexdigest()
