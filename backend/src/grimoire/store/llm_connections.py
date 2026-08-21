"""Named LLM connections: openrouter / claude / openai_compatible profiles,
each remembering its own key+model so switching the active one never loses
credentials. Migrates the pre-connections flat config fields once. See
docs/superpowers/specs/2026-07-18-llm-connections-design.md for the full
rationale, especially around the `rev` token and the migration marker.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from . import atomic, config, routing
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import home, now_iso, safe_id, slugify, uniquify

#: Every connection kind a stored connection may declare. Public, because
#: whether a kind can carry an image is answered in two places -- `llm
#: .TEXT_ONLY_KINDS` for the fallback route and `store.image_drafts
#: .SUPPORTED_KINDS` for the primary -- and a test partitions THIS roster
#: between them, so a new kind cannot be added without classifying it.
KINDS = ("openrouter", "claude", "openai_compatible")
_FIELDS = ("kind", "name", "base_url", "api_key", "model", "post_process")


class ConnectionNotFound(Exception):
    pass


def _dir() -> Path:
    return home() / "llm_connections"


def _path(id: str) -> Path:
    return _dir() / f"{id}.md"


def _sidecar_path(id: str) -> Path:
    return _dir() / f"{id}.models.json"


def _write_raw(id: str, **fields: str) -> None:
    """Unconditional write: stamps a fresh rev and clears any sidecar for
    this id, on every call (create AND update) — simpler than conditioning
    the sidecar clear on which field changed, and no less correct: the rev
    bump alone already makes any stale sidecar invisible on read (see
    cached_models below), so clearing it here is pure hygiene either way."""
    meta = {k: fields.get(k, "") for k in _FIELDS}
    meta["rev"] = secrets.token_hex(8)
    _dir().mkdir(parents=True, exist_ok=True)
    _sidecar_path(id).unlink(missing_ok=True)
    atomic.write_text(_path(id), dump_frontmatter(meta, ""))


def _read(id: str) -> dict | None:
    """None for unsafe, missing, unreadable, or unrecognized-kind files — all
    four count as "not a valid seeded/created connection", used both by normal
    lookups and by migration's crash-recovery check.

    The `exists()` is INSIDE the try, which review caught it not being: an id
    longer than the filesystem's NAME_MAX raises ENAMETOOLONG from the stat
    itself, and pathlib does not swallow that one. `safe_id` does not bound
    length — nothing about a long name lets it escape its directory — so a
    caller-supplied id from a request body (#77's reroll override) reached this
    and escaped as a 500 where the route documents a 400. A name the filesystem
    cannot hold is a name no connection has, which is the answer every other
    branch here gives.
    """
    # #240's rule, which this module was outside: never join a caller-supplied
    # id onto a path unchecked. Every read reaches a connection through here,
    # and an id now arrives in a REQUEST BODY as well as a URL segment (#77's
    # reroll override) -- which is precisely the case `test_path_guard_store`'s
    # docstring calls out as getting no protection from the router's path
    # matching. `..`, `a/b` and the Windows drive-relative forms all name
    # something that is not a child of the connections directory, and "not a
    # connection" is the honest answer for every one of them.
    if not safe_id(id):
        return None
    p = _path(id)
    try:
        if not p.exists():
            return None
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    if meta.get("kind") not in KINDS:
        return None
    return {"id": id, **{k: meta.get(k, "") for k in _FIELDS}, "rev": meta.get("rev", "")}


def _mask(conn: dict) -> dict:
    out = {k: v for k, v in conn.items() if k != "api_key"}
    out["key_set"] = bool(conn["api_key"])
    return out


def list_connections() -> list[dict]:
    ensure_migrated()
    out = []
    if _dir().exists():
        for p in sorted(_dir().glob("*.md")):
            conn = _read(p.stem)
            if conn is not None:
                out.append(_mask(conn))
    return out


def read_connection(id: str) -> dict:
    ensure_migrated()
    conn = _read(id)
    if conn is None:
        raise ConnectionNotFound(id)
    return {**_mask(conn), **cached_models(id)}


def read_connection_raw(id: str) -> dict:
    ensure_migrated()
    conn = _read(id)
    if conn is None:
        raise ConnectionNotFound(id)
    return conn


def create_connection(kind: str, name: str, **fields) -> str:
    ensure_migrated()

    def exists(c: str) -> bool:
        return _path(c).exists()

    id = uniquify(slugify(name), exists)
    _write_raw(id, kind=kind, name=name, **fields)
    return id


def update_connection(id: str, **fields) -> None:
    ensure_migrated()
    conn = _read(id)
    if conn is None:
        raise ConnectionNotFound(id)
    fields = {k: v for k, v in fields.items() if v is not None}
    base_url_changed = "base_url" in fields and fields["base_url"] != conn["base_url"]
    if base_url_changed:
        # A custom endpoint's base_url is user-editable (unlike OpenRouter's
        # fixed URL) — carrying the old key over to a newly-pointed host
        # would silently leak it, so repointing always drops the key unless
        # this same call also supplies a fresh one.
        fields.setdefault("api_key", "")
    elif not fields.get("api_key"):
        # "type to replace" convention: an omitted OR empty api_key means
        # "keep the stored one" whenever base_url isn't changing. Dropping
        # it from `fields` here (rather than filtering only None above) is
        # what makes that true — otherwise an explicit api_key="" from any
        # caller that always serializes the field would silently erase a
        # working credential on an unrelated update (e.g. a rename).
        fields.pop("api_key", None)
    merged = {**conn, **fields}
    _write_raw(id, **{k: merged[k] for k in _FIELDS})


def delete_connection(id: str) -> None:
    ensure_migrated()
    # Guarded like `_read`, and separately from it, because this is the one
    # caller-id path join that does not go through a read first -- and it is
    # the one that unlinks.
    if not safe_id(id):
        raise ConnectionNotFound(id)
    p = _path(id)
    if not p.exists():
        raise ConnectionNotFound(id)
    # Every config key that names a connection, not just the active one:
    # `embeddings_connection_id` (semantic recall) points here too, as does
    # `fallback_connection_id` (#144), and a dangling one leaves the layer
    # silently off while the Configuration page still shows it configured. A
    # list rather than two branches, so the next key that references a
    # connection is one entry rather than a third copy of this reasoning.
    cfg = config.read_config()
    # The three single-purpose keys, plus every per-task route (#142) -- built
    # from `routing.CONFIG_KEYS` rather than listed, so a route added later is
    # swept by construction instead of by somebody remembering this line.
    #
    # A campaign's own routes are NOT reachable from here, and deliberately not
    # chased: sweeping them would mean rewriting every campaign.md on a delete,
    # under every campaign's lock. `routing.resolve` walks past a reference to a
    # connection that no longer exists for exactly this reason.
    named = ("active_connection_id", "embeddings_connection_id",
             "fallback_connection_id", *routing.CONFIG_KEYS)
    dangling = {key: "" for key in named if cfg.get(key) == id}
    if dangling:
        # Clear these BEFORE unlinking the file, not after — otherwise a
        # failure between the two steps (disk error, process death) leaves
        # the file gone (its slug now reusable) while config.md still
        # references it, reproducing the exact dangling-reference bug this
        # exists to close, just via a partial-failure window instead of
        # never having the fix at all. With this ordering, every failure
        # window is retry-safe: fail here and nothing changed yet (clean
        # retry); fail during the unlink below and the references are
        # already correctly cleared even though the file still exists (a
        # retriable "delete didn't finish" state, not a dangling reference).
        config.write_config(**dangling)
    p.unlink()
    _sidecar_path(id).unlink(missing_ok=True)


def get_active() -> dict | None:
    ensure_migrated()
    id = config.read_config().get("active_connection_id", "")
    if not id:
        return None
    return _read(id)


def cached_models(id: str) -> dict:
    """The sole read path for the model-list cache — gates on `rev` here,
    not at write time, so there's no check-then-act gap for a concurrent
    update/delete/recreate to land in (see the design spec's §5)."""
    empty = {"models": [], "fetched_at": ""}
    p = _sidecar_path(id)
    if not p.exists():
        return empty
    try:
        sidecar = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    conn = _read(id)
    if conn is None or sidecar.get("rev") != conn["rev"]:
        return empty
    return {"models": sidecar["models"], "fetched_at": sidecar["fetched_at"]}


def set_cached_models(id: str, models: list[dict], rev: str) -> None:
    """Writes unconditionally, tagged with the rev captured before the
    fetch that produced `models` — staleness is judged later, on read, by
    cached_models(), not here."""
    payload = {"models": models, "fetched_at": now_iso(), "rev": rev}
    atomic.write_text(_sidecar_path(id), json.dumps(payload, indent=2) + "\n")


def ensure_migrated() -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    marker = _dir() / ".migrated"
    if marker.exists():
        return
    # Read the pre-migration fields directly off the frontmatter file — NOT
    # via config.read_config(), whose narrowed key set no longer returns
    # them (see Task 1's config.py edit). A file that predates this change
    # still has them physically present; parse_frontmatter returns whatever
    # keys exist regardless of the "official" schema.
    path = config._config_path()
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
    if _read("openrouter") is None:
        _write_raw("openrouter", kind="openrouter", name="OpenRouter",
                    api_key=meta.get("openrouter_key", ""),
                    model=meta.get("model", config.DEFAULT_MODEL),
                    base_url="", post_process="none")
    if _read("claude") is None:
        _write_raw("claude", kind="claude", name="Claude",
                    model=meta.get("claude_model", config.DEFAULT_CLAUDE_MODEL),
                    base_url="", api_key="", post_process="none")
    if not meta.get("active_connection_id"):
        # Truthiness, not presence: this whole block only ever runs once,
        # gated by the `.migrated` marker check above — there is no
        # post-migration "explicit clear" that can reach this code path,
        # since by construction the marker would already exist by then. So
        # any falsy value here — the key wholly absent (a genuine
        # pre-migration/legacy file), or present-but-"" (because
        # config.read_config()'s own defaults bootstrap already wrote this
        # file with active_connection_id: "" before migration ever ran,
        # e.g. via GET /api/config's read_config()-before-get_active() call
        # order) — equally means "not yet decided", so seed it from the
        # legacy `provider` field either way. A presence check would treat
        # that bootstrap-written "" as an intentional decision and skip
        # seeding, leaving a brand-new install with no active connection.
        active = "openrouter" if meta.get("provider", "openrouter") == "openrouter" else "claude"
        config.write_config(active_connection_id=active)
    atomic.write_text(marker, "1")
