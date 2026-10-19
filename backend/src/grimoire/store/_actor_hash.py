"""Actor synchronization hashes preserve pair order and concatenate without separators."""

from __future__ import annotations

import hashlib


def content_hash(files: list[tuple[str, str]]) -> str:
    """Hash ordered UTF-8 name/text pairs exactly as actor snapshots do."""
    h = hashlib.sha256()
    for name, text in files:
        h.update(name.encode("utf-8"))
        h.update(text.encode("utf-8"))
    return h.hexdigest()
