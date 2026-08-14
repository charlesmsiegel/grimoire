"""Generic entity CRUD + content hashing over an arbitrary container root.

A "container root" is a world dir or a campaign dir; entities live at
`<root>/<kind>/<id>.md`. Entity ids are stable for life (rename changes only the
`name` frontmatter) so sync refs `(kind, id)` line up across world and campaign.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import atomic, statcache, tokens
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import safe_id, slugify, uniquify

ENTITY_KINDS: tuple[str, ...] = ("locations", "lore", "items", "groups", "creatures")

# Everything copy-on-create / sync tracks as a flat `<kind>/<id>.md` file:
# generic entities plus greetings (which keep their own CRUD module).
SYNCED_KINDS: tuple[str, ...] = ENTITY_KINDS + ("greetings",)

# ---- secrecy (#49): who an activated entry is *for*, orthogonal to `owners` ----
# `owners` says what puts an entry in the prompt; `secrecy` says how the prompt
# is allowed to use it once there:
#   public  — the default, and the way every entry has always behaved
#   secret  — activates exactly as before, but renders under a heading telling
#             the model not to let uninvolved characters voice or act on it
#   gm-only — its BODY never reaches a prompt: `context.activate` drops it,
#             `_assemble` suppresses it as the current setting, and
#             `absorb.snapshots.group_snapshot` skips it
#
# Secrecy gates the body, not the record's existence. A gm-only location still
# has a NAME, and the app still uses it where it must refer to the place at all
# -- a mechanics sheet label for a location with a sheet
# (`context.mechanics`), the scene-suggestion picker (`suggest`). Suppressing
# those would break mechanics and make the location unpickable, which is a
# worse failure than naming a room the scene is already set in.
PUBLIC, SECRET, GM_ONLY = "public", "secret", "gm-only"
SECRECY_LEVELS: tuple[str, ...] = (PUBLIC, SECRET, GM_ONLY)


def normalize_secrecy(value: str | None) -> str:
    """One of `SECRECY_LEVELS` for a stored or incoming value; `PUBLIC` for
    anything else — a missing key, a blank, a hand-typo in the frontmatter.

    Lenient here and strict at the save boundary, the same split
    `entity_schema.invalid_values` documents: frontmatter is hand-editable and
    a typo in it must not take a turn down, but a typo arriving over HTTP is
    reported (`routes.entities._check_secrecy`) rather than silently downgraded
    to public — silently downgrading *secrecy* is the one direction that leaks.
    """
    v = (value or "").strip().lower()
    return v if v in SECRECY_LEVELS else PUBLIC


class EntityNotFound(Exception):
    pass


class UnknownKind(Exception):
    pass


def _check_kind(kind: str) -> None:
    if kind not in ENTITY_KINDS:
        raise UnknownKind(kind)


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
            if not safe_id(p.stem):   # enumeration agrees with the resolvers
                continue
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
            # `tokens` LAST, after **meta: it is a measurement of the record,
            # not a field of it, so a file that happens to carry a `tokens:`
            # line in its frontmatter does not get to report its own cost.
            #
            # Unconditional rather than opt-in, and that is what makes the
            # memo load-bearing: this listing is on the turn loop's path too
            # (`context.world_state._world_info` sweeps all five kinds through
            # the overlay before deciding what activates), so an encode per
            # record per turn is the cost being avoided. Keyed on the same
            # signature `read_entity` uses, so the pair costs one encode
            # between them, not two.
            out.append({"id": p.stem, "name": meta.get("name", p.stem), **meta,
                        "tokens": tokens.record_tokens(p, body)})
    return out


def read_entity(root: Path, kind: str, eid: str) -> dict:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    # Beside `meta`, not inside it: `meta` is the frontmatter this record would
    # be written back with, and every writer here round-trips it.
    return {"meta": {"id": eid, **meta}, "body": body,
            "tokens": tokens.record_tokens(p, body)}


def create_entity(root: Path, kind: str, name: str, body: str = "", keys: str = "", owners: str = "",
                  sd_prompt: str = "", taken=None, fields: dict[str, str] | None = None,
                  secrecy: str = "") -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        # `taken` widens the id namespace (overlay: world files + tombstones).
        # The record DIRECTORY counts as taken too, exactly as it does for an
        # actor (`characters.create_character` keys on `_char_dir`): it holds
        # the previous record-of-that-slug's assets and campaign-local sidecars,
        # so handing the id out again adopts them (#225). It also keeps a failed
        # `instantiate` rollback honest -- that rollback deletes the record
        # directory, and it must never be one that predates the create (Codex
        # review).
        return (_entity_path(root, kind, c).exists() or _kind_dir(root, kind).joinpath(c).is_dir()
                or (taken is not None and taken(c)))

    eid = uniquify(slugify(name), exists)
    meta = {"name": name}
    if keys:
        meta["keys"] = keys
    if owners:
        meta["owners"] = owners
    # Public is stored as the ABSENCE of the key, so an unmarked record is
    # byte-identical to one written before secrecy existed -- which keeps the
    # world->campaign sync (which hashes whole files) from seeing every entity
    # as edited the first time anyone saves one.
    level = normalize_secrecy(secrecy)
    if level != PUBLIC:
        meta["secrecy"] = level
    if sd_prompt:
        meta["sd_prompt"] = sd_prompt
    for k, v in (fields or {}).items():
        if v:
            meta[k] = v
    atomic.write_text(_entity_path(root, kind, eid), dump_frontmatter(meta, body))
    return eid


def update_entity(
    root: Path, kind: str, eid: str, name: str | None = None,
    body: str | None = None, keys: str | None = None, owners: str | None = None,
    sd_prompt: str | None = None, fields: dict[str, str] | None = None,
    secrecy: str | None = None,
) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if keys is not None:
        meta["keys"] = keys
    if owners is not None:
        meta["owners"] = owners
    if secrecy is not None:
        level = normalize_secrecy(secrecy)
        if level == PUBLIC:
            meta.pop("secrecy", None)   # "back to public" removes the key
        else:
            meta["secrecy"] = level
    if sd_prompt is not None:
        meta["sd_prompt"] = sd_prompt
    for k, v in (fields or {}).items():
        if v:
            meta[k] = v
        else:
            meta.pop(k, None)
    new_body = cur_body if body is None else body
    atomic.write_text(p, dump_frontmatter(meta, new_body))


def delete_entity(root: Path, kind: str, eid: str) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    p.unlink()


def content_hash(text: str) -> str:
    """`entity_hash` of a record you are holding rather than one on disk. A
    copier can then record a sync base that provably describes the bytes it
    copied, instead of re-reading a source that may have moved (#247)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entity_hash(root: Path, kind: str, eid: str) -> str | None:
    if not safe_id(eid):
        return None
    p = _entity_path(root, kind, eid)
    sig = statcache.signature(p)
    if sig is None:
        return None
    return statcache.memo(
        "entity_hash", sig,
        lambda: content_hash(p.read_text(encoding="utf-8")))


def all_refs(root: Path) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for kind in ENTITY_KINDS:
        d = _kind_dir(root, kind)
        if d.exists():
            for p in sorted(d.glob("*.md")):
                if safe_id(p.stem):
                    refs.append((kind, p.stem))
    return refs


def synced_refs(root: Path) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for kind in SYNCED_KINDS:
        d = _kind_dir(root, kind)
        if d.exists():
            refs.extend((kind, p.stem) for p in sorted(d.glob("*.md")) if safe_id(p.stem))
    return refs


def entity_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind in ENTITY_KINDS:
        d = _kind_dir(root, kind)
        counts[kind] = sum(1 for p in d.glob("*.md") if safe_id(p.stem)) if d.exists() else 0
    return counts
