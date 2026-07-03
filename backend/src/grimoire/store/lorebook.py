"""Translate SillyTavern world-info into grimoire entities.

Two ST entry schemas are normalized: the standalone world-info export (entries
keyed by index, fields `key`/`comment`/`disable`) and the V3 `character_book`
(entries as a list, fields `keys`/`name`/`enabled`). Both become editable
entities with a markdown body + comma-joined `keys` — the triggers the context
builder already consumes. `constant` -> keyless (always-on); disabled/blank
entries are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import cards, entities


class LorebookError(Exception):
    pass


def _entries_container(book):
    if isinstance(book, dict):
        inner = book.get("entries", book)
    else:
        inner = book
    if isinstance(inner, dict):
        return list(inner.values())
    if isinstance(inner, list):
        return inner
    return []


def _normalize(book) -> list[dict]:
    out: list[dict] = []
    for e in _entries_container(book):
        if not isinstance(e, dict):
            continue
        enabled = e.get("enabled", True) and not e.get("disable", False)
        content = e.get("content", "")
        if not enabled or not isinstance(content, str) or not content.strip():
            continue
        keys = e.get("keys") or e.get("key") or []
        keys = [str(k) for k in keys if str(k).strip()]
        name = e.get("comment") or e.get("name") or (keys[0] if keys else "Imported entry")
        out.append({
            "name": name,
            "keys": [] if e.get("constant") else keys,
            "body": content,
            "category": "lore",
        })
    return out


def parse(data: bytes, fmt: str) -> list[dict]:
    if fmt == "lorebook":
        try:
            book = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LorebookError(f"invalid lorebook JSON: {exc}") from exc
        return _normalize(book)
    if fmt in ("json", "png", "charx"):
        card = cards.loads(data, fmt)  # raises cards.CardParseError
        return _normalize(card.get("data", {}).get("character_book") or {})
    raise LorebookError(f"unknown format: {fmt}")


def from_character_book(book) -> list[dict]:
    """Normalize a card's embedded character_book into commit-ready entries."""
    return _normalize(book or {})


def _existing_signatures(root: Path, kind: str) -> set[tuple[str, str, str]]:
    sigs = set()
    for ref in entities.list_entities(root, kind):
        e = entities.read_entity(root, kind, ref["id"])
        sigs.add((e["meta"].get("name", ""), e["meta"].get("keys", ""), e["body"].strip()))
    return sigs


def commit(root: Path, entries: list[dict]) -> list[dict]:
    """Create entities for the entries, skipping exact duplicates -- an entry
    whose name, keys, and body all match an existing entity of the same
    category (or an earlier entry in the batch) is dropped, so re-importing
    the same book is a no-op instead of piling up slug-suffixed copies."""
    created: list[dict] = []
    seen: dict[str, set[tuple[str, str, str]]] = {}
    for e in entries:
        category = e.get("category", "lore")
        if category not in entities.ENTITY_KINDS:
            raise LorebookError(f"unknown category: {category}")
        if category not in seen:
            seen[category] = _existing_signatures(root, category)
        sig = (e.get("name", "Imported entry"), ",".join(e.get("keys", [])), e.get("body", "").strip())
        if sig in seen[category]:
            continue
        seen[category].add(sig)
        eid = entities.create_entity(root, category, e.get("name", "Imported entry"),
                                     e.get("body", ""), ",".join(e.get("keys", [])))
        created.append({"kind": category, "id": eid})
    return created
