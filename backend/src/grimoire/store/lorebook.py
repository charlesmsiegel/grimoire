"""Translate SillyTavern world-info into grimoire entities.

Two ST entry schemas are normalized: the standalone world-info export (entries
keyed by index, fields `key`/`comment`/`disable`) and the V3 `character_book`
(entries as a list, fields `keys`/`name`/`enabled`). Both become editable
entities with a markdown body + comma-joined `keys` — the triggers the context
builder already consumes. `constant` -> keyless (always-on); disabled/blank
entries are skipped.

The advanced ST activation fields ride along in an `st_extensions` stash, and
that stash is also what `adopt` maps native activation fields from (the
`lore_fields` catalog; the mapping table is spec 4.1). A new import has those
native fields written beside the stash; `pending_adopt` serves the old imports,
whose stash has not been applied yet.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import cards, entities, lore_fields


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


def _importable(e) -> bool:
    """Whether `_normalize` would yield an entry for this one: the single
    definition of "this entry survives import", so a count can be taken
    without building the entries (see `importable_count`)."""
    if not isinstance(e, dict):
        return False
    enabled = e.get("enabled", True) and not e.get("disable", False)
    content = e.get("content", "")
    return bool(enabled and isinstance(content, str) and content.strip())


# The advanced ST activation fields, in both spellings the two schemas use
# (V3 character_book / standalone world-info). Preserved verbatim under an
# `extensions` stash at parse time and an `st_extensions` frontmatter key at
# commit (#20). `adopt` maps the rows spec 4.1 marks honoured (secondary keys
# and logic, scan depth, sticky, cooldown, priority, keep, recursion) onto
# native fields; the rest are stashed un-honoured, because dropping them at
# import is lossy and irreversible and keeping them lets a future change use
# them without a re-import.
_ST_EXTENSION_FIELDS = (
    "secondary_keys", "keysecondary", "selective", "selectiveLogic",
    "position", "insertion_order", "order", "priority",
    "probability", "useProbability", "case_sensitive", "caseSensitive",
    "constant", "use_regex", "useRegex", "excludeRecursion",
    "exclude_recursion", "preventRecursion", "prevent_recursion",
    "delayUntilRecursion", "delay_until_recursion",
    "scanDepth", "scan_depth", "depth", "role",
    "matchWholeWords", "match_whole_words",
    # Fields the first stash dropped when they sat at the top level of an entry
    # (spec 4.1 "lost" rows). Most are not honoured, but keeping them means a
    # later change can honour them without a re-import.
    "sticky", "cooldown", "delay", "ignoreBudget", "ignore_budget",
    "group", "groupOverride", "group_override", "groupWeight", "group_weight",
    "useGroupScoring", "use_group_scoring", "vectorized",
    "characterFilter", "character_filter", "triggers",
    "automationId", "automation_id",
)

# Stashed fields that no part of grimoire honours: `adopt` reports whichever of
# these a stash holds, so the editor can say what an import carried that will
# not take effect. Every spelling, since the stash keeps the one it was given.
# Each must also be in `_ST_EXTENSION_FIELDS` (a test holds the two together).
_UNHONOURED = frozenset((
    "delay", "delayUntilRecursion", "delay_until_recursion",
    "probability", "useProbability", "position", "depth", "role",
    "caseSensitive", "case_sensitive", "matchWholeWords", "match_whole_words",
    "use_regex", "useRegex",
    "group", "groupOverride", "group_override", "groupWeight", "group_weight",
    "useGroupScoring", "use_group_scoring",
    "vectorized", "characterFilter", "character_filter", "triggers",
    "automationId", "automation_id",
))

# ST's `selectiveLogic` numbering, onto the catalog's operators.
_SELECTIVE_LOGIC = {0: "and_any", 1: "not_all", 2: "not_any", 3: "and_all"}


def _normalize(book) -> list[dict]:
    out: list[dict] = []
    for e in _entries_container(book):
        if not _importable(e):
            continue
        content = e["content"]                      # _importable proved it non-blank
        keys = e.get("keys") or e.get("key") or []
        keys = [str(k) for k in keys if str(k).strip()]
        name = e.get("comment") or e.get("name") or (keys[0] if keys else "Imported entry")
        entry = {
            "name": name,
            "keys": [] if e.get("constant") else keys,
            "body": content,
            "category": "lore",
        }
        ext = {k: e[k] for k in _ST_EXTENSION_FIELDS if k in e}
        if e.get("extensions"):
            # The V3 entry's own extensions object (sticky/cooldown/delay and
            # whatever else a frontend filed there) -- the spec says importers
            # SHOULD preserve it, and it nests inside the stash as itself.
            # Only when non-empty: V3 entries routinely carry `extensions: {}`,
            # and stashing that would put frontmatter on every simple import.
            ext["extensions"] = e["extensions"]
        if e.get("constant") and keys:
            # `constant` still means keyless activation above, but the raw keys
            # it suppressed are recorded so "keyless because constant" stays
            # distinguishable from "keyless because no keys".
            ext["keys"] = keys
        if ext:                                     # simple imports stay unchanged
            entry["extensions"] = ext
        out.append(entry)
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


def importable_count(book) -> int:
    """`len(from_character_book(book))` without building the entries.

    `read_character` reports this per version and it is called per actor in
    loops that only want `["meta"]` (rosters, birthdays, cast assembly), so
    the count must not allocate a normalized copy of every lorebook in the
    world. Shares `_importable` with `_normalize` rather than restating the
    rule -- `test_importable_count_matches_what_normalize_yields` fails if
    the two ever part company."""
    return sum(1 for e in _entries_container(book or {}) if _importable(e))


@dataclass(frozen=True)
class AdoptResult:
    fields: dict[str, str]          # native activation fields, frontmatter-shaped
    unmapped: tuple[str, ...]       # stashed, recognised, and not honoured


def _scopes(stash: Mapping[str, object]):
    """The places ST files an advanced field: the top level, then the V3
    entry's own `extensions` object."""
    yield stash
    nested = stash.get("extensions")
    if isinstance(nested, Mapping):
        yield nested


def _pick(stash: Mapping[str, object], *names: str) -> object:
    """The first value present under any of `names`, top level before nested.
    A null is "absent" -- ST exports `scanDepth: null` for "use the default"."""
    for scope in _scopes(stash):
        for name in names:
            if scope.get(name) is not None:
                return scope[name]
    return None


def _int(value: object) -> int | None:
    # A bool is an int to Python and a flag to ST; neither reading is a number.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _clamped(key: str, value: object) -> str | None:
    n = _int(value)
    if n is None:
        return None
    lo, hi = lore_fields.BOUNDS[key]
    return str(min(max(n, lo), hi))


def _secondary_keys(stash: Mapping[str, object]) -> str | None:
    raw = _pick(stash, "keysecondary", "secondary_keys")
    if not isinstance(raw, list):
        return None
    keys = [k.strip() for k in raw if isinstance(k, str) and k.strip()]
    return ", ".join(keys) or None


def _set_by_author(name: str, value: object) -> bool:
    """Whether a stashed value says anything. ST exports write every field with
    its default (`group: ""`, `vectorized: false`, `triggers: []`,
    `characterFilter: {}`), and listing those would flag every entry; a
    probability of 100 is "always fires", which is what grimoire does."""
    if value is None or value is False:
        return False
    if isinstance(value, (str, list, dict)) and not value:
        return False
    return not (name == "probability" and value == 100)


def adopt(stash: Mapping[str, object]) -> AdoptResult:
    """The native activation fields a stash maps to (spec 4.1), as the flat
    strings frontmatter holds, plus the recognised-but-unhonoured fields it
    carries. A value of the wrong type is skipped, as if it were absent; an
    out-of-range number is clamped (an editor save refuses instead -- an
    import is not the author typing it)."""
    fields: dict[str, str] = {}

    # `selective` absent is ST's default (on); off means the secondary list is
    # ignored, which is the same as not having one.
    selective = _pick(stash, "selective")
    if selective is not False:
        secondary = _secondary_keys(stash)
        if secondary:
            fields["secondary_keys"] = secondary
            code = _int(_pick(stash, "selectiveLogic"))
            if code in _SELECTIVE_LOGIC:
                fields["key_logic"] = _SELECTIVE_LOGIC[code]

    scan_depth = _clamped("scan_depth", _pick(stash, "scanDepth", "scan_depth"))
    if scan_depth is not None:
        fields["scan_depth"] = scan_depth
    for key in ("sticky", "cooldown"):
        value = _clamped(key, _pick(stash, key))
        if value is not None:
            fields[key] = value
    priority = _clamped("priority", _pick(stash, "priority", "order", "insertion_order"))
    if priority is not None:
        fields["priority"] = priority

    if _pick(stash, "ignoreBudget", "ignore_budget") is True:
        fields["keep"] = "true"

    # exclude = no entry's body may pull this one in; prevent = this entry's
    # body pulls nothing in.
    not_pulled = _pick(stash, "excludeRecursion", "exclude_recursion") is True
    not_pulling = _pick(stash, "preventRecursion", "prevent_recursion") is True
    if not_pulled or not_pulling:
        fields["recursion"] = ("none" if not_pulled and not_pulling
                               else "pulls_only" if not_pulled else "pulled_only")

    unmapped = sorted({name for scope in _scopes(stash) for name in _UNHONOURED
                       if _set_by_author(name, scope.get(name))})
    return AdoptResult(fields, tuple(unmapped))


def pending_adopt(meta: Mapping[str, object]) -> AdoptResult:
    """What adopting a record's stash would still change: `adopt` over its
    `st_extensions`, less every field the record already has. An author's edit
    beats a stale import, so a field with any value is left alone. No stash, or
    one that is not a JSON object, is nothing to adopt."""
    raw = meta.get("st_extensions")
    try:
        stash = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except ValueError:
        stash = None
    if not isinstance(stash, dict):
        return AdoptResult({}, ())
    res = adopt(stash)
    held = {k for k in lore_fields.FIELD_KEYS if str(meta.get(k) or "").strip()}
    return AdoptResult({k: v for k, v in res.fields.items() if k not in held},
                       res.unmapped)


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
    # Every category checked BEFORE anything is written, the way
    # `scenario.apply` does it: the check used to sit inside the create loop, so
    # a bad category on the third row returned 400 with the first two already
    # on disk. Reachable from the review table in one direction only -- a bundle
    # NEWER than the backend serving it, whose `/entity-kinds` read failed, so
    # its dropdown fell back to its own longer list and offered a kind this
    # server does not have. (The other direction cannot: an older bundle is
    # handed a superset it simply shows, and both parse paths clamp an incoming
    # category to this tuple anyway.)
    for e in entries:
        category = e.get("category", "lore")
        if category not in entities.ENTITY_KINDS:
            raise LorebookError(f"unknown category: {category}")

    created: list[dict] = []
    seen: dict[str, set[tuple[str, str, str]]] = {}
    for e in entries:
        category = e.get("category", "lore")
        if category not in seen:
            seen[category] = _existing_signatures(root, category)
        # `extensions` is deliberately NOT in the signature: it is metadata
        # about the same entry, and including it would make a re-import of a
        # pre-#20 book create a slug-suffixed twin of every entry instead of
        # skipping it. The cost of that choice is that a re-import cannot
        # backfill `st_extensions` onto entries imported before the stash
        # existed -- that needs an update path, not a second create.
        sig = (e.get("name", "Imported entry"), ",".join(e.get("keys", [])), e.get("body", "").strip())
        if sig in seen[category]:
            continue
        seen[category].add(sig)
        # The advanced ST fields ride as one opaque JSON frontmatter key, so the
        # round trip through parse/dump_frontmatter (single-line string scalars)
        # cannot reshape them. Absent entirely when there is nothing to stash --
        # a simple import's frontmatter is byte-identical to what it always was.
        # The honoured ones are also written natively (`adopt`), so a new import
        # takes effect without the author pressing Apply.
        ext = e.get("extensions") or {}
        fields = ({"st_extensions": json.dumps(ext, sort_keys=True), **adopt(ext).fields}
                  if ext else None)
        eid = entities.create_entity(root, category, e.get("name", "Imported entry"),
                                     e.get("body", ""), ",".join(e.get("keys", [])),
                                     fields=fields)
        created.append({"kind": category, "id": eid})
    return created
