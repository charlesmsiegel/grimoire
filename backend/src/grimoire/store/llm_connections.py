"""Named LLM connections: openrouter / claude / openai_compatible / anthropic profiles,
each remembering its own key+model so switching the active one never loses
credentials. Migrates the pre-connections flat config fields once. See
docs/superpowers/specs/2026-07-18-llm-connections-design.md for the full
rationale, especially around the `rev` token and the migration marker.
"""

from __future__ import annotations

import errno
import functools
import json
import secrets
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from . import atomic, config, inference_keys, inference_retired, locks, routing
from .frontmatter import dump_frontmatter, parse_frontmatter, read_record
from .paths import home, now_iso, safe_id, slugify, uniquify

#: Every connection kind a stored connection may declare. Public, because
#: whether a kind can carry an image is answered in two places -- `llm
#: .TEXT_ONLY_KINDS` for the fallback route and `store.image_drafts
#: .SUPPORTED_KINDS` for the primary -- and a test partitions THIS roster
#: between them, so a new kind cannot be added without classifying it.
KINDS = ("openrouter", "claude", "openai_compatible", "anthropic")
#: `vision` is "" (auto: the cached catalog decides), "on" or "off" -- whether
#: this connection's model may be sent post images (#377, `store.post_images`).
#: `prefill` is "true" or "" on disk and a bool once read -- whether a reply cut
#: short may be sent back as the start of the model's own turn (play controls
#: IV, "Keep writing"). Off unless the user turns it on: whether a trailing
#: assistant message is continued depends on the model, not the kind.
#: `preset` is the provider preset (`store/inference/providers.py`) the
#: connection was made from, and `billing` is "metered" or "subscription"
#: (spec 4.1); both are "" on a connection no build has stamped yet.
_FIELDS = ("kind", "name", "base_url", "api_key", "model", "post_process", "reasoning_effort",
           "sampler_preset", "sampler_support", "vision", "prefill", "preset", "billing")
#: The legacy per-connection fields that describe a MODEL rather than the
#: provider. Frozen for older builds once the store is at format 2, where the
#: model's behaviour comes from its facts and the presets instead, and refused
#: on write there (spec 11.3).
MODEL_FIELDS: tuple[str, ...] = ("model", "vision", "prefill", "post_process",
                                 "reasoning_effort", "sampler_preset")
#: Written by retirement's strip into the frontmatter it rewrites, in the same
#: atomic write that takes the legacy fields off (inference slice I), and only
#: when it recorded some: "this file's legacy model fields are in the
#: retirement record". A file whose legacy fields are absent may have been
#: stripped, or may have been created at format 2 with none -- only this says
#: which, so a strict planner lookup can refuse a stripped file whose record
#: entry has not arrived (`stripped`; `legacy_plan._Reader`) rather than plan
#: it as a connection with no model. Never part of a record as `_read` returns
#: it; kept by every write this module makes over the file (`_write_raw`). A
#: C-H build drops it on its own edits (it keeps only the fields it knows).
STRIPPED_KEY = "inference_stripped"
#: The fields describing how this connection SAMPLES rather than what it is.
#: An edit touching only these keeps the connection's `rev` (see
#: `update_connection`): the rev exists to invalidate the cached model catalog
#: and the health verdict, and neither describes a sampler preset.
SAMPLER_FIELDS = frozenset({"sampler_preset", "sampler_support"})
#: Every field whose edit keeps the `rev`: the sampler pair, and `vision`
#: (#377) -- an override describes neither the catalog nor the provider's
#: health, and bumping the rev would discard the cached catalog that "auto"
#: reads, so setting it back to auto would read "unknown" until a refresh.
#: `prefill` joins them for the same reason: it shapes one kind of prompt and
#: describes neither the catalog nor the provider's health. So do `name`,
#: `preset` and `billing` (spec 4.1): a label, where the record came from and
#: how it is paid for say nothing about the deployment behind it, and a rename
#: that threw away the catalog and every verified test would be a rename the
#: user pays for in test calls.
REV_NEUTRAL_FIELDS = SAMPLER_FIELDS | {"vision", "prefill", "name", "preset", "billing"}

#: A raw-connection reader: a connection id to its raw record (legacy fields
#: included), or None for no such connection. The type of what
#: `read_connection_raw` / `read_connection_strict` are wrapped into for one
#: resolution or one plan (`resolve.connection_lookup`, `legacy_plan.lookup`);
#: whether a file that cannot be read raises or reads as None is the
#: wrapper's to say.
Lookup = Callable[[str], dict | None]


#: The one serialization boundary over a connection's record and the files
#: beside it. Every write that stamps a rev (`_write_raw`: create, update,
#: migration) or unlinks the record or a sidecar (delete) holds it, and so
#: does `inference.facts.record_verified`'s compare-and-write -- which is what
#: keeps a probe run that started on an old rev from filing its verdicts after
#: an edit moved the rev (replacing the new rev's), or after a delete
#: (recreating an orphan facts file). Reentrant: `ensure_migrated` writes and
#: is called from inside the other writers.
#:
#: It IS `locks.config_lock()` -- the same object, not a second lock taken
#: before it. Two servers on one store are supported (docs/store-guarantees.md,
#: "A second process on the same store"), and the model settings are spread
#: over files that are read, checked and rewritten together: a connection and
#: `config.md` (a delete's sweep, the migration's switch, the settings write's
#: existence check), a connection and its facts (a verdict's rev check), the
#: catalog cache. A process-local lock here serialized none of that across
#: processes, and an order of two locks (this, then `config_lock`) was one
#: more rule for every holder to keep. One cross-process lock makes each read
#: -> check -> write span whole, and `config.format_hold` is it with the
#: format read inside. Taken inside a campaign lock (the campaign settings
#: write), never around one. `inference.facts`' own `_lock` is taken under it,
#: and so is `inference_retired._lock` (the retirement record): both
#: process-local and innermost.
LOCK = locks.config_lock()


class ConnectionNotFound(Exception):
    pass


class ConnectionUnreadableError(OSError):
    """A connection's file is there but could not be read or decoded (a strict
    read, `_read`). An `OSError`, so a caller that already stops on I/O
    errors stops on this one; the message names the connection."""

    def __init__(self, conn_id: str, exc: BaseException):
        super().__init__(f"connection {conn_id} could not be read: {exc}")
        self.conn_id = conn_id


class ModelFieldsRefusedError(Exception):
    """A write that would set `MODEL_FIELDS` on a store at format 2 (or one a
    newer build wrote): they reach older builds only and change nothing here
    (spec 11.3). `fields` names them; `newer` says which store refused."""

    def __init__(self, fields: list[str], *, newer: bool = False):
        super().__init__(", ".join(fields))
        self.fields = fields
        self.newer = newer


def _refuse_model_fields(names: set[str]) -> None:
    """Raise `ModelFieldsRefusedError` when `names` is not empty and the store is
    past the legacy layout. Called under `LOCK`, in the hold that writes: the
    migration's switch (`inference.migrate._switch`) takes `LOCK` too, so the
    format read here is still the store's when the write lands."""
    if not names:
        return
    cfg = config.read_config()
    newer = inference_keys.is_newer(cfg)
    if newer or inference_keys.is_current(cfg):
        raise ModelFieldsRefusedError(sorted(names), newer=newer)


def _named_model_fields(fields: dict) -> set[str]:
    """The `MODEL_FIELDS` a create body sets to something other than a blank
    default (`post_process` "none" is its default spelled out)."""
    return {f for f in MODEL_FIELDS
            if fields.get(f) not in (None, "", False)
            and not (f == "post_process" and fields.get(f) == "none")}


def _dir() -> Path:
    return home() / "llm_connections"


def _path(id: str) -> Path:
    return _dir() / f"{id}.md"


def _sidecar_path(id: str) -> Path:
    return _dir() / f"{id}.models.json"


def regex_path(conn_id: str) -> Path:
    """The connection's output-processing rules (`store/regex/layers.py`). Its
    own file for the same reason as the sidecar: the record's frontmatter is
    flat strings, and a rule list does not fit it."""
    return _dir() / f"{conn_id}.regex.json"


def facts_path(conn_id: str) -> Path:
    """The connection's per-model facts (`store/inference/facts.py`): what was
    verified of each model, and what the user said about it. Its own file for
    the same reason as the sidecar -- one JSON document per provider, not a
    flat string in the record's frontmatter."""
    return _dir() / f"{conn_id}.facts.json"


def facts_files() -> dict[str, Path]:
    """Every provider's facts file, as `{connection id: path}`.

    A glob of the connections directory: no connection record is read, so this
    is cheap enough for the rate reader that sits on the shell's navigation
    path (`pricing.provider_rates`). `delete_connection` unlinks a deleted
    connection's facts file, so the ids here are live providers. A file whose
    stem is not a safe id (a hand-dropped stray) is skipped.
    """
    suffix = ".facts.json"
    try:
        found = sorted(_dir().glob(f"*{suffix}"))
    except OSError:
        return {}
    return {p.name[:-len(suffix)]: p for p in found
            if p.is_file() and safe_id(p.name[:-len(suffix)])}


def _write_raw(id: str, keep_rev: str = "", **fields: str | bool) -> None:
    """Unconditional write: stamps a fresh rev and clears any sidecar for
    this id, on every call (create AND update) — simpler than conditioning
    the sidecar clear on which field changed, and no less correct: the rev
    bump alone already makes any stale sidecar invisible on read (see
    cached_models below), so clearing it here is pure hygiene either way.
    Under `LOCK`, whoever called it."""
    meta = {k: str(fields.get(k, "")) for k in _FIELDS}
    meta["prefill"] = "true" if fields.get("prefill") in (True, "true") else ""
    # An empty legacy model field is not written (slice I, I2): a read
    # defaults every one of them to "" anyway, and on a retired store a key
    # left in the file -- even empty -- would read as a legacy field still to
    # strip. A non-empty one is written as given: below format 2 they are the
    # planner's input.
    for field in MODEL_FIELDS:
        if not meta[field]:
            del meta[field]
    # `keep_rev` is the one exception, for an edit nothing the rev guards has
    # seen: the sidecar and the rev both survive it (see `REV_NEUTRAL_FIELDS`).
    meta["rev"] = keep_rev or secrets.token_hex(8)
    with LOCK:
        # Retirement's strip marker outlives an edit: the fields it says the
        # record holds are still there, whatever else changed (`STRIPPED_KEY`).
        if _carries_strip_marker(_path(id)):
            meta[STRIPPED_KEY] = "1"
        _dir().mkdir(parents=True, exist_ok=True)
        if not keep_rev:
            _sidecar_path(id).unlink(missing_ok=True)
        atomic.write_text(_path(id), dump_frontmatter(meta, ""))


def _carries_strip_marker(p: Path) -> bool:
    """Whether the file at `p` carries `STRIPPED_KEY`, fail-soft: a file that
    is absent or cannot be read carries none (what `_write_raw` keeps)."""
    try:
        if not p.exists():
            return False
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return False
    return str(meta.get(STRIPPED_KEY, "") or "").strip() == "1"


def stripped(conn_id: str) -> bool:
    """Whether connection `conn_id`'s file carries retirement's strip marker
    (`STRIPPED_KEY`): its legacy model fields were taken off and recorded in
    the retirement record. Strict: a file that is there and cannot be read
    raises `ConnectionUnreadableError`; an absent one, or an unsafe id, is
    not stripped. Reads only, and never seeds."""
    if not safe_id(conn_id):
        return False
    p = _path(conn_id)
    try:
        if not p.exists():
            return False
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        if getattr(exc, "errno", None) == errno.ENAMETOOLONG:
            return False
        raise ConnectionUnreadableError(conn_id, exc) from exc
    return str(meta.get(STRIPPED_KEY, "") or "").strip() == "1"


def _read(id: str, *, strict: bool = False) -> dict | None:
    """None for unsafe, missing, unreadable, or unrecognized-kind files — all
    four count as "not a valid seeded/created connection", used both by normal
    lookups and by migration's crash-recovery check.

    `strict` keeps "unreadable" apart from the other three: a file that is
    there but cannot be read or decoded -- or that names no `kind` at all,
    which every written record does (a zero-byte or unfenced sync placeholder)
    -- raises `ConnectionUnreadableError` instead of reading as no connection. For a caller
    that persists what it reads (`inference.migrate`): a sync client holding
    the file is a reason to try again, not a dangling reference to write down.

    A strict read raises `ConnectionUnreadableError`, except for a name too long
    for the filesystem, which is no connection either way (below).

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
    except (OSError, UnicodeDecodeError) as exc:
        if strict and getattr(exc, "errno", None) != errno.ENAMETOOLONG:
            raise ConnectionUnreadableError(id, exc) from exc
        return None
    if strict and "kind" not in meta:
        # Every record this module writes states its kind, so a file with none
        # -- zero bytes, or a frontmatter fence that never arrived, a sync
        # placeholder mid-download -- is one not yet readable, not one absent.
        raise ConnectionUnreadableError(
            id, ValueError("the file holds no connection record (empty, or unfenced)"))
    if meta.get("kind") not in KINDS:
        return None
    return {"id": id, **{k: meta.get(k, "") for k in _FIELDS},
            "prefill": meta.get("prefill") == "true", "rev": meta.get("rev", "")}


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


def own_preset(raw: dict) -> str:
    """The sampler preset connection record `raw` names as its own
    (`sampler_preset`, one of `MODEL_FIELDS`), stripped; "" for none.

    What the record carries, for the connection editor's readout
    (`resolve.own_target`) -- never a selection: since slice I the resolver
    takes a selection's preset from the format-2 settings alone, which the
    planner (`inference.legacy_plan`) maps a legacy connection's preset into.
    Read here, beside the fields it belongs to, so nothing outside the
    planner reads a legacy field of a connection by name."""
    return str(raw.get("sampler_preset", "") or "").strip()


def read_connection_strict(conn_id: str) -> dict | None:
    """The raw connection, or None when none by that id exists (an unsafe id,
    no file, an unknown kind). A file that exists but cannot be read or
    decoded raises `ConnectionUnreadableError` -- see `_read`'s `strict`."""
    ensure_migrated()
    return _read(conn_id, strict=True)


def list_connections_strict() -> list[dict]:
    """Every connection, raw and read as `read_connection_strict` reads one:
    a record that cannot be read raises rather than being left out."""
    ensure_migrated()
    out = []
    if _dir().exists():
        for p in sorted(_dir().glob("*.md")):
            conn = _read(p.stem, strict=True)
            if conn is not None:
                out.append(conn)
    return out


def _files() -> list[Path]:
    try:
        return sorted(_dir().glob("*.md")) if _dir().exists() else []
    except OSError:
        return []


def unreadable_connections() -> dict[str, str]:
    """Connection id -> why, for every connection file that is there but that
    a strict read refuses (`ConnectionUnreadableError`). Reads only: unlike
    every other reader here it never seeds (`ensure_migrated` writes), so a
    status reader that must not write can ask it (`inference.retire.left`)."""
    out: dict[str, str] = {}
    for p in _files():
        try:
            _read(p.stem, strict=True)
        except ConnectionUnreadableError as exc:
            out[p.stem] = str(exc)
    return out


def legacy_fields_on_disk(conn_id: str | None = None) -> dict[str, tuple[str, ...]]:
    """Connection id -> the legacy model fields (`MODEL_FIELDS`) its file
    holds with a non-empty value, for every connection that holds any (or
    only `conn_id`'s, which reads that one file): what retirement's strip
    has left to do. Reads only, and never seeds; a file that cannot be read,
    or holds no connection, is left out (`unreadable_connections` names the
    first kind)."""
    if conn_id is not None:
        if not safe_id(conn_id):
            return {}
        found = [_path(conn_id)]
    else:
        found = _files()
    out: dict[str, tuple[str, ...]] = {}
    for p in found:
        try:
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if meta.get("kind") not in KINDS:
            continue
        held = tuple(f for f in MODEL_FIELDS if str(meta.get(f, "") or "").strip())
        if held:
            out[p.stem] = held
    return out


def strip_model_fields(conn_id: str) -> bool:
    """Take the legacy model fields (`MODEL_FIELDS`) off connection `conn_id`
    for good (inference slice I, retirement's strip); returns whether it wrote.

    All three steps in one hold of `LOCK` -- `config_lock`, cross-process,
    with the store's format read inside it (`config.format_hold`) -- so a
    `PUT /llm-connections/{id}` that meets it waits (or, after 30 s, is
    refused with 409), and is never overwritten (N4):

    1. the file's RAW frontmatter, read strictly
       (`frontmatter.read_record(..., require="kind")`): a file that is there
       but holds no record is never written over;
    2. its non-empty legacy fields into the retirement record
       (`inference_retired.record_fields`), read strictly there;
    3. the same frontmatter minus those fields, written atomically: every
       other key, `rev` included, kept as it was, so the cached catalog,
       every verified test and every vector space survive -- and, when step 2
       recorded anything, `STRIPPED_KEY` added in that same write, so a
       device that has the file but not yet the record's entry for it can
       tell (`stripped`).

    A connection that holds no legacy key at all writes nothing; one holding
    only empty ones is rewritten without them, recording nothing and marking
    nothing (there is no entry for a lookup to wait for)."""
    if not safe_id(conn_id):
        raise ConnectionNotFound(conn_id)
    with LOCK, config.format_hold():
        p = _path(conn_id)
        if not p.exists():
            raise ConnectionNotFound(conn_id)
        meta, body = read_record(p, f"connection {conn_id}", require="kind")
        if not any(field in meta for field in MODEL_FIELDS):
            return False
        values = {f: meta[f] for f in MODEL_FIELDS if str(meta.get(f, "") or "").strip()}
        if values:
            inference_retired.record_fields(conn_id, values)
        kept = {k: v for k, v in meta.items() if k not in MODEL_FIELDS}
        if values:
            kept[STRIPPED_KEY] = "1"
        atomic.write_text(p, dump_frontmatter(kept, body))
    return True


def create_connection(kind: str, name: str, *, refuse_model_fields: bool = False,
                      **fields) -> str:
    """Create a connection; its id is the name's slug, made unique.

    `refuse_model_fields` (the API's writes): a body setting any of
    `MODEL_FIELDS` on a store at format 2 raises `ModelFieldsRefusedError`, checked
    in the hold that writes. Off for the store's own callers (migration, test
    fixtures), which write what they mean at either format.

    A create is always a brand-new provider, so a retirement-record entry
    under the id it claims can only be a dead provider's -- one deleted
    while the record was elsewhere, whose entry a sync brought back. It is
    forgotten in the same hold, BEFORE the file is written
    (`inference_retired.forget_fields`): left, it would be merged under the
    new provider by the next strip ("first values win") and answer for it.
    Strict: a record that cannot be read raises
    `inference_retired.RecordUnreadableError` and nothing is written."""
    ensure_migrated()

    def exists(c: str) -> bool:
        return _path(c).exists()

    # The slug is chosen and claimed in one hold: two creates of one name
    # would otherwise both find it free and the second overwrite the first.
    with LOCK, config.format_hold():
        if refuse_model_fields:
            _refuse_model_fields(_named_model_fields(fields))
        id = uniquify(slugify(name), exists)
        inference_retired.forget_fields(id)
        _write_raw(id, kind=kind, name=name, **fields)
    return id


#: What `update_connection` hands a guard as the `rev` of an edit that will
#: restamp it: never a real rev (those are hex), so it equals no stored one.
NEW_REV = "(new)"


def update_connection(id: str, *, refuse_model_fields: bool = False,
                      keep_key_on_same_host: bool = False,
                      guard: Callable[[dict, dict], None] | None = None,
                      **fields) -> None:
    """Merge `fields` (None leaves one as it is) into the connection.

    `refuse_model_fields`: as `create_connection`, for a field this edit
    CHANGES -- resending what is stored writes nothing and is not refused,
    because an editor sends every field on every save.

    `keep_key_on_same_host`: a new `base_url` on the host (and port) the
    stored one names keeps the key, where any repoint otherwise drops it --
    for a provider-preset move between two plans of one service, which the
    caller has decided is one. The hosts are compared here, in the hold that
    writes, so an address changed after the caller read it is judged as it
    now is.

    `guard(before, after)`: called in the hold that writes, once the edit is
    known to write, with the stored connection and what it will become
    (`after["rev"]` is the rev the write stamps: the stored one for a
    rev-neutral edit, `NEW_REV` otherwise); whatever it raises refuses the
    write. The hold covers `config.format_hold` too (this lock, then
    `config_lock`: `LOCK`'s order), so a guard reading `config.md` -- the
    Embedding role's confirmation, `routes.config.put_connection` -- reads
    the value no settings write can change before this one lands, and a store
    a newer build switched is refused (`config.NewerFormatError`)."""
    ensure_migrated()
    # The read is the merge's base, so it sits in the hold with the write.
    # In `config.format_hold`, so a store a newer build switched meanwhile is
    # refused here rather than rewritten -- `_write_raw` keeps only the fields
    # this build knows.
    with LOCK, config.format_hold():
        _update(id, fields, refuse_model_fields=refuse_model_fields,
                keep_key_on_same_host=keep_key_on_same_host, guard=guard)


def _authority(url: object) -> tuple[str, int | None]:
    """`(hostname, port)` a base URL names; `("", None)` for a blank or
    unparsable one, which shares a host with nothing."""
    if not isinstance(url, str) or not url.strip():
        return "", None
    try:
        parts = urlsplit(url.strip())
        return (parts.hostname or "").lower(), parts.port
    except ValueError:
        return "", None


def _update(conn_id: str, fields: dict, *, refuse_model_fields: bool = False,
            keep_key_on_same_host: bool = False,
            guard: Callable[[dict, dict], None] | None = None) -> None:
    conn = _read(conn_id)
    if conn is None:
        raise ConnectionNotFound(conn_id)
    fields = {k: v for k, v in fields.items() if v is not None}
    base_url_changed = "base_url" in fields and fields["base_url"] != conn["base_url"]
    same_host = (keep_key_on_same_host and base_url_changed
                 and _authority(conn["base_url"])[0] != ""
                 and _authority(conn["base_url"]) == _authority(fields["base_url"]))
    if base_url_changed and not same_host:
        # A custom endpoint's base_url is user-editable (unlike OpenRouter's
        # fixed URL) — carrying the old key over to a newly-pointed host
        # would silently leak it, so repointing drops the key unless this
        # same call also supplies a fresh one -- or the caller asked for it
        # kept on a new address on the same host (`keep_key_on_same_host`).
        fields.setdefault("api_key", "")
    elif not fields.get("api_key"):
        # "type to replace" convention: an omitted OR empty api_key means
        # "keep the stored one" whenever the key is not being dropped. Dropping
        # it from `fields` here (rather than filtering only None above) is
        # what makes that true — otherwise an explicit api_key="" from any
        # caller that always serializes the field would silently erase a
        # working credential on an unrelated update (e.g. a rename).
        fields.pop("api_key", None)
    merged = {**conn, **fields}
    changed = {k for k in _FIELDS if merged[k] != conn[k]}
    if refuse_model_fields:
        _refuse_model_fields(changed & set(MODEL_FIELDS))
    if not changed and conn["rev"]:
        # Nothing to write: a no-op edit keeps the rev, and the file, as they
        # are (a rewrite of identical fields is upload traffic in a synced
        # store, and a fresh rev would drop the catalog and every verdict).
        return
    keep = conn["rev"] if changed <= REV_NEUTRAL_FIELDS and conn["rev"] else ""
    if guard is not None:
        guard(conn, {**merged, "rev": keep or NEW_REV})
    _write_raw(conn_id, keep_rev=keep, **{k: merged[k] for k in _FIELDS})


def delete_connection(id: str) -> None:
    ensure_migrated()
    # Guarded like `_read`, and separately from it, because this is the one
    # caller-id path join that does not go through a read first -- and it is
    # the one that unlinks.
    if not safe_id(id):
        raise ConnectionNotFound(id)
    # The whole delete is one hold, sidecars included: a verdict filed between
    # the record's unlink and the facts file's would recreate the file for a
    # connection that no longer exists (`inference.facts.record_verified`).
    with LOCK, config.format_hold():
        _delete(id)


def _delete(conn_id: str) -> None:
    p = _path(conn_id)
    if not p.exists():
        raise ConnectionNotFound(conn_id)
    # Every config key that names a connection, not just the active one:
    # `embeddings_connection_id` (semantic recall) points here too, as does
    # `fallback_connection_id` (#144), and a dangling one leaves the layer
    # silently off while the Configuration page still shows it configured. A
    # list rather than two branches, so the next key that references a
    # connection is one entry rather than a third copy of this reasoning.
    #
    # Read and cleared in one `config_lock` hold: a role written between the
    # read and the write would otherwise be merged over by a stale sweep.
    #
    # The retirement record is read strictly FIRST, before anything is
    # written (6b re-review N-1): its entry for this id must be forgotten in
    # this hold (below), and a record that cannot be read refuses the whole
    # delete -- the references, the record and the file all as they were.
    inference_retired.read(strict=True)
    with locks.config_lock():
        cfg = config.read_config()
        dangling = _dangling(cfg, conn_id)
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
    # The retirement record's fields for this id go in the same hold, after
    # the references and before the unlink (N20, 6b review M-6): a provider
    # created later under the same slug must not be answered with this one's
    # legacy model.
    inference_retired.forget_fields(conn_id)
    p.unlink()
    _sidecar_path(conn_id).unlink(missing_ok=True)
    regex_path(conn_id).unlink(missing_ok=True)
    facts_path(conn_id).unlink(missing_ok=True)


def _dangling(cfg: dict, conn_id: str) -> dict[str, str]:
    """Every global key naming `conn_id`, cleared: `{key: ""}`.

    The three single-purpose keys, plus every per-task route (#142) -- built
    from `routing.CONFIG_KEYS` rather than listed, so a route added later is
    swept by construction instead of by somebody remembering this line.

    A campaign's own routes are NOT reachable from here, and deliberately not
    chased: sweeping them would mean rewriting every campaign.md on a delete,
    under every campaign's lock. the resolver (`store.inference`) walks past a reference to a
    connection that no longer exists for exactly this reason.
    """
    named = ("active_connection_id", "embeddings_connection_id",
             "fallback_connection_id", *routing.CONFIG_KEYS)
    out = {key: "" for key in named if cfg.get(key) == conn_id}
    # The new layout's selections (spec 11.3): a role, a role's fallback, a
    # route's pin and the Embedding role, each cleared WHOLE -- a model or a
    # preset left behind would describe a selection with no provider. A
    # route's `use_<k>` stays: it chose its pin, and an empty pin resolves as
    # nothing chosen. Campaign selections dangle, as the routes always have.
    selections = [functools.partial(inference_keys.role_key, r)
                  for r in inference_keys.GENERATIVE_ROLES]
    selections += [functools.partial(inference_keys.fallback_key, r)
                   for r in inference_keys.GENERATIVE_ROLES]
    selections += [functools.partial(inference_keys.pin_key, r.key) for r in routing.ROUTES]
    for key in selections:
        if str(cfg.get(key("provider"), "") or "").strip() == conn_id:
            out.update((key(part), "") for part in inference_keys.PARTS)
    embedding = functools.partial(inference_keys.role_key, "embedding")
    if str(cfg.get(embedding("provider"), "") or "").strip() == conn_id:
        out.update((embedding(part), "") for part in inference_keys.EMBEDDING_PARTS)
    return out


def cached_models(id: str) -> dict:
    """The sole read path for the model-list cache — gates on `rev` here,
    not at write time, so there's no check-then-act gap for a concurrent
    update/delete/recreate to land in (see the design spec's §5)."""
    empty = {"models": [], "fetched_at": "", "fetched_by": ""}
    p = _sidecar_path(id)
    if not p.exists():
        return empty
    try:
        sidecar = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    # A sidecar is a file a sync or a hand can mangle into any JSON at all. It
    # is read on every OpenRouter generation now (the sampler split asks it
    # which parameters the model takes), so a scalar, an array or a missing
    # key must read as "nothing cached" -- never as an exception that fails
    # the turn.
    if not isinstance(sidecar, dict) or not isinstance(sidecar.get("models"), list):
        return empty
    conn = _read(id)
    if conn is None or sidecar.get("rev") != conn["rev"]:
        return empty
    return {"models": sidecar["models"], "fetched_at": sidecar.get("fetched_at", ""),
            # `.get`, not `[...]`: a sidecar written before #398 has no
            # `fetched_by`, and a refresh is not worth failing over a field
            # that only decides whether a lost response can be recovered.
            "fetched_by": sidecar.get("fetched_by", "")}


def cached_row(conn_id: str, model: str) -> dict | None:
    """`model`'s row in the connection's cached catalog, or None when there is
    none -- the one lookup the resolver, the capability resolver and the test
    call's price estimate all make.

    Never raises. It runs on every resolution, and whatever slips past
    `cached_models`' own shape check (an over-long id's stat, a row list a
    sync mangled) costs the catalog, never the turn."""
    if not conn_id or not isinstance(conn_id, str):
        return None
    try:
        models = cached_models(conn_id)["models"]
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return None
    if not isinstance(models, list):
        return None
    return next((r for r in models if isinstance(r, dict) and r.get("id") == model), None)


def set_cached_models(id: str, models: list[dict], rev: str,
                      attempt: str = "") -> None:
    """Writes unconditionally, tagged with the rev captured before the
    fetch that produced `models` — staleness is judged later, on read, by
    cached_models(), not here.

    `attempt` is WHICH refresh wrote this, and it is the only durable trace a
    `draft` leaves anywhere. The refresh is the one draft whose result outlives
    its run, so a client whose run was reaped has to ask the store whether its
    own attempt landed — and "the catalog is newer than it was" cannot answer
    that, because a second tab refreshing the same connection advances the
    timestamp too. Empty for a caller that names none, which then simply
    cannot be recovered.

    In `config.format_hold`: capability resolution reads this cache, so a
    store a newer build switched is not this build's to rewrite it in
    (`config.NewerFormatError`).
    """
    payload = {"models": models, "fetched_at": now_iso(), "rev": rev,
               "fetched_by": attempt}
    with LOCK, config.format_hold():
        atomic.write_text(_sidecar_path(id), json.dumps(payload, indent=2) + "\n")


def ensure_migrated() -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    if (_dir() / ".migrated").exists():
        return
    # Checked again in the hold: two first reads would otherwise both seed.
    # The hold is the model-settings one (`LOCK` is `config_lock`), and the
    # format is read inside it as `config.format_hold` reads it -- but from
    # the file as it is, never materializing a missing `config.md` first,
    # which is this seed's own write to make. A store a newer build wrote is
    # left alone: seeding would create or replace its records and write
    # `active_connection_id` into its `config.md`. Silently, unlike the
    # writers' 409: every read of a connection comes through here.
    with LOCK:
        if _newer_on_disk():
            return
        _migrate()


def _newer_on_disk() -> bool:
    """Whether `config.md`, as it stands, is a newer build's (no file: no)."""
    path = config._config_path()
    if not path.exists():
        return False
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return inference_keys.is_newer(meta)


def _legacy_on_disk(path: Path, meta: dict[str, str]) -> bool:
    """Whether `config.md` (at `path`, its frontmatter `meta`) is a legacy
    store's: there, and not at the current format or past it. A missing one
    is born current (`inference_keys.born_current`)."""
    return (path.exists() and not inference_keys.is_current(meta)
            and not inference_keys.is_newer(meta))


def _migrate() -> None:
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
    # The model fields and `active_connection_id` are the legacy layout's, so
    # they are seeded below format 2 only (slice I, ruling 7, I2): at format 2
    # a provider names no model of its own -- a role's selection does -- and a
    # store born there is born retired (`config.birth_fields`), so a seeded
    # legacy value would be one more thing for retirement to archive and
    # strip on every fresh install. A missing `config.md` is born at format 2.
    legacy = _legacy_on_disk(path, meta)
    if _read("openrouter") is None:
        own = ({"model": meta.get("model", config.DEFAULT_MODEL), "post_process": "none"}
               if legacy else {})
        _write_raw("openrouter", kind="openrouter", name="OpenRouter",
                    api_key=meta.get("openrouter_key", ""), base_url="", **own)
    if _read("claude") is None:
        own = ({"model": meta.get("claude_model", config.DEFAULT_CLAUDE_MODEL),
                "post_process": "none"} if legacy else {})
        _write_raw("claude", kind="claude", name="Claude", base_url="", api_key="", **own)
    if not meta.get("active_connection_id") and legacy:
        # Below format 2 only (above).
        #
        # Truthiness, not presence: this whole block only ever runs once,
        # gated by the `.migrated` marker check above — there is no
        # post-migration "explicit clear" that can reach this code path,
        # since by construction the marker would already exist by then. So
        # any falsy value here — the key wholly absent (a genuine
        # pre-migration/legacy file), or present-but-"" (because
        # config.read_config()'s own defaults bootstrap already wrote this
        # file with active_connection_id: "" before migration ever ran,
        # e.g. via a read_config() that ran before the first connection
        # read) — equally means "not yet decided", so seed it from the
        # legacy `provider` field either way. A presence check would treat
        # that bootstrap-written "" as an intentional decision and skip
        # seeding, leaving a brand-new install with no active connection.
        active = "openrouter" if meta.get("provider", "openrouter") == "openrouter" else "claude"
        config.write_config(active_connection_id=active)
    atomic.write_text(marker, "1")
