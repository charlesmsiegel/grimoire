"""Generic entity CRUD + content hashing over an arbitrary container root.

A "container root" is a world dir or a campaign dir; entities live at
`<root>/<kind>/<id>.md`. Entity ids are stable for life (rename changes only the
`name` frontmatter) so sync refs `(kind, id)` line up across world and campaign.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify

ENTITY_KINDS: tuple[str, ...] = ("locations", "lore")

# Everything copy-on-create / sync tracks as a flat `<kind>/<id>.md` file:
# generic entities plus greetings (which keep their own CRUD module).
SYNCED_KINDS: tuple[str, ...] = ENTITY_KINDS + ("greetings",)


class EntityNotFound(Exception):
    pass


class UnknownKind(Exception):
    pass


def _check_kind(kind: str) -> None:
    if kind not in ENTITY_KINDS:
        raise UnknownKind(kind)


def _safe_id(eid: str) -> bool:
    """Reject ids that could escape the kind directory (defense in depth)."""
    return eid not in ("", ".", "..") and "/" not in eid and "\\" not in eid


def _kind_dir(root: Path, kind: str) -> Path:
    return root / kind


def _entity_path(root: Path, kind: str, eid: str) -> Path:
    return root / kind / f"{eid}.md"


def list_entities(root: Path, kind: str) -> list[dict]:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    out: list[dict] = []
    if d.exists():
        for p in sorted(d.glob("*.md")):
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            out.append({"id": p.stem, "name": meta.get("name", p.stem), **meta})
    return out


def read_entity(root: Path, kind: str, eid: str) -> dict:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not _safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": {"id": eid, **meta}, "body": body}


def create_entity(root: Path, kind: str, name: str, body: str = "", keys: str = "", owners: str = "") -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)
    eid = uniquify(slugify(name), lambda c: _entity_path(root, kind, c).exists())
    meta = {"name": name}
    if keys:
        meta["keys"] = keys
    if owners:
        meta["owners"] = owners
    _entity_path(root, kind, eid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return eid


def update_entity(
    root: Path, kind: str, eid: str, name: str | None = None,
    body: str | None = None, keys: str | None = None, owners: str | None = None,
) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not _safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if keys is not None:
        meta["keys"] = keys
    if owners is not None:
        meta["owners"] = owners
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")


def delete_entity(root: Path, kind: str, eid: str) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not _safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    p.unlink()


def entity_hash(root: Path, kind: str, eid: str) -> str | None:
    if not _safe_id(eid):
        return None
    p = _entity_path(root, kind, eid)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def all_refs(root: Path) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for kind in ENTITY_KINDS:
        d = _kind_dir(root, kind)
        if d.exists():
            for p in sorted(d.glob("*.md")):
                refs.append((kind, p.stem))
    return refs


def synced_refs(root: Path) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for kind in SYNCED_KINDS:
        d = _kind_dir(root, kind)
        if d.exists():
            refs.extend((kind, p.stem) for p in sorted(d.glob("*.md")))
    return refs


def entity_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind in ENTITY_KINDS:
        d = _kind_dir(root, kind)
        counts[kind] = len(list(d.glob("*.md"))) if d.exists() else 0
    return counts
