"""Bytes and pre-read provenance for the membership inverse (spec 07).

This leaf knows flat group files, not campaign inheritance. Overlay selects
sources; captured bytes, never a later read, supply both metadata and digest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import frontmatter, paths, statcache


class GroupSource(BaseModel):
    id: str
    path: Path
    layer: Literal["world", "campaign"]
    raw: bytes


class GroupCapture(BaseModel):
    sources: tuple[GroupSource, ...]
    stamps: tuple[statcache.Stamp, ...] | None
    roots: tuple[Path, ...]
    deleted: frozenset[str]
    detached: frozenset[str]
    ledger_digest: str
    groups_digest: str
    store_root: Path | None = None


def pre_stamps(path: Path, parent: Path) -> tuple[statcache.Stamp, ...] | None:
    """Stamp the absence witness before testing the input, too.

    Missing inputs can be vouched for by their existing parent. An inaccessible
    stat cannot be mistaken for absence, even if the subsequent read succeeds.
    """
    parent_stamp = statcache.stamp(parent)
    own_stamp = statcache.stamp(path)
    if own_stamp is not None:
        return (own_stamp,)
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None if parent_stamp is None else (parent_stamp,)
    except OSError:
        return None
    return None


def collect(root: Path, layer: Literal["world", "campaign"]
            ) -> tuple[tuple[GroupSource, ...], tuple[statcache.Stamp, ...] | None]:
    directory = root / "groups"
    stamps = pre_stamps(directory, root)
    captured: list[GroupSource] = []
    try:
        listed = sorted(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        listed = []
    # glob suppresses directory access failures on newer Python. A denied
    # collection is unavailable, never proof that every group disappeared.
    for path in listed:
        if not path.match("*.md") or not paths.safe_id(path.stem):
            continue
        before = statcache.stamp(path)
        if stamps is not None:
            stamps = None if before is None else (*stamps, before)
        captured.append(GroupSource(id=path.stem, path=path, layer=layer, raw=path.read_bytes()))
    return tuple(captured), stamps


def combined_stamps(*parts: tuple[statcache.Stamp, ...] | None
                    ) -> tuple[statcache.Stamp, ...] | None:
    if any(part is None for part in parts):
        return None
    return tuple(dict.fromkeys(entry for part in parts if part is not None for entry in part))


def content_digest(sources: tuple[GroupSource, ...]) -> str:
    # Layer and file identity are inputs even when two records contain the same
    # bytes. Hash raw bytes before universal-newline parsing, including prose.
    values = [(source.layer, source.id, hashlib.sha256(source.raw).hexdigest())
              for source in sources]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def rows(sources: tuple[GroupSource, ...], *, include_body: bool = False) -> list[dict]:
    summaries = []
    for source in sources:
        text = source.raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        meta, body = frontmatter.parse_frontmatter(text)
        # A hand-written `id:` is content, not permission to rename the file.
        row = {"name": meta.get("name", source.id), **meta, "id": source.id, "layer": source.layer}
        if include_body:
            row["body"] = body
        summaries.append(row)
    return summaries
