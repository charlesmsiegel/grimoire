"""Turning the parsed sections into StagedEdits — the diff the reviewer sees.

The file is named for the role, not the function, because `materialize` is a
public function this package re-exports: a submodule spelled the same way
would be overwritten by that export, and a later `from ..absorb import
materialize` would bind the function rather than the module.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from .. import (
    characters,
    commitments,
    entities,
    errors,
    facts,
    groupstate,
    overlay,
    pcs,
    playstate,
    plot,
    relationships,
)
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..continuity import effective as continuity_effective
from ..paths import slugify
from . import conflicts, parse, routing, weather

_CARD_FIELDS = ("description", "personality", "scenario")

#: `continuity.identity.AS_NEW_KEY`: the private row key an accepted
#: ``existing`` retarget carries its original row under, popped here to build
#: the as-new alternative. Spelled out rather than imported because identity
#: imports this module (it shares `assign_ids`), so the edge cannot run back.
AS_NEW_KEY = "_identity_as_new"

#: The parsed sections the identity check examines, and the record kind each
#: opens -- `continuity.identity.SECTIONS`, restated for the same reason.
_IDENTITY_SECTIONS = {"plot_movements": "thread", "commitment_movements": "commitment"}

#: The `identity_check` decisions a row stages at band ``low`` whatever its
#: citation says: a verdict that it may be a duplicate, and a row the check
#: never answered. Every examined row has a plausible candidate, so an
#: unanswered one is a possible duplicate nobody ruled out; keeping its
#: routing band would pre-approve it outside NEEDS YOU.
_LOW_DECISIONS = ("uncertain", "unchecked")

#: The reason a staged accepted ``existing`` is downgraded with when its
#: target stopped being live between the resolver's answer and staging.
CLOSED_SINCE = "that record was closed while the review was being prepared"


def _char_name(cid: str, char_id: str) -> str:
    """Overlay-aware: a thin campaign's NPC is usually still inherited (never
    materialized croot-side), so the display name must resolve across the union."""
    try:
        return characters.read_character(overlay.char_root(cid, char_id), char_id)["meta"].get("name", char_id)
    except characters.CharacterNotFound:
        return char_id


def _actor_exists(cid: str, token: str) -> bool:
    """Overlay-aware: a thin campaign's cast is mostly inherited (never
    materialized croot-side), so existence must be checked across the union,
    not just the campaign's own copy."""
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            pcs.read_pc(overlay.pc_root(cid, aid), aid)
        elif kind == "characters":
            characters.read_character(overlay.char_root(cid, aid), aid)
        else:
            return False
        return True
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return False


#: Every world record a `lore_edits` append may land on, in the order a BARE id
#: is tried. All five of `entities.ENTITY_KINDS` (#224): a body append is the
#: only edit that can evolve a record of any kind, and resolving just the two
#: left items, groups and creatures unevolvable — a group could only ever move
#: through its campaign-side state, and an item or creature was static from the
#: day it was created.
#:
#: A fixed order rather than ENTITY_KINDS' own, because ids are per-kind
#: directories: `lore/the-ledger` and `items/the-ledger` are two records, and a
#: bare id has to pick one. Lore and locations lead in the order they were tried
#: before the other three joined them, so an append that has been landing on a
#: lore entry cannot start landing on a same-slugged location instead. The pick
#: is not hidden from the reviewer either way — the staged label names the kind
#: it resolved to.
#:
#: A model that means one of the others says so: `<kind>/<id>` (or `<kind>:<id>`,
#: the form `group_state_edits` already accepts) resolves in that kind alone.
#: `test_absorb_store.py` holds this tuple to ENTITY_KINDS, so a sixth kind
#: cannot join the store and quietly arrive unevolvable the way these three did.
APPEND_KINDS: tuple[str, ...] = ("lore", "locations", "items", "groups", "creatures")


def _entity_target(cid: str, raw_id: str) -> tuple[str, str, dict] | None:
    """`(kind, eid, record)` for a `lore_edits` id, or None when nothing answers.

    A `<kind>/<id>`-qualified id is resolved in that kind ONLY. Falling back to
    the bare scan when the named kind has no such record would answer a
    question the model did not ask: it named a kind because the id alone is
    ambiguous, so a miss there means the record it meant does not exist, not
    that some other kind's same-slugged record will do.

    The record comes back with the pair rather than being re-read by the
    caller, because resolving is not free: a bare id that misses is up to five
    overlay reads, and each croot miss reads the campaign's tombstone list
    before falling through to the world.
    """
    kind, sep, rest = raw_id.partition("/")
    if not sep:
        kind, sep, rest = raw_id.partition(":")
    if sep and rest and kind in APPEND_KINDS:
        candidates: tuple[tuple[str, str], ...] = ((kind, rest),)
    else:
        candidates = tuple((k, raw_id) for k in APPEND_KINDS)
    for k, eid in candidates:
        try:
            return k, eid, overlay.read_entity(cid, k, eid)
        except entities.EntityNotFound:
            continue
    return None


def _text(value) -> str:
    """A stored field as text, or "" for anything that is not a string.

    commitments.json is hand-editable and read by a bare `json.loads`, so every
    field inside a record is whatever the file says — a list-valued `status`
    concatenated into a label, or a list-valued `due` handed to `.strip()`,
    raises from inside `materialize`. That is AFTER the extraction call and is
    not caught by the absorb route, so one malformed record turns a paid-for
    absorb into a 500. Checking the document's top-level shape does not reach
    this; the fields have to be coerced where they are read.
    """
    return value.strip() if isinstance(value, str) else ""


def _new_record_id(stored: dict, staged: dict[str, str], slug: str, title: str, *,
                   settled: Callable[[dict], bool],
                   aliases: dict[str, str] | None = None) -> str:
    """`slug`, or the first `slug-N` that is free or holds the SAME record.

    The one allocator for both record types that absorb can open by title (spec
    §10.5) -- plot threads and commitments differ only in what `settled` means:
    a closed thread, a resolved commitment. It was commitments' alone until a
    plot row whose title merely slugged like a stored thread's was merged into
    it; what follows is written in commitments' terms and holds for threads
    word for word, with "closed" for "resolved" and `plot_snapshot` (which
    offers only open and advanced threads) for `commitment_snapshot`.

    Only for a movement the model opened WITHOUT an id, where the id is derived
    from the title and a collision may be an accident rather than a reference.
    A collision is honoured only when the stored record is unresolved AND its
    title is the one the model wrote: then the model saw that record in the
    snapshot under that title, and treating the movement as a beat on it is
    what "one edit per commitment per scene" means. Anything else gets a fresh
    id, for two different reasons:

    - a **resolved** record cannot have been meant: `commitment_snapshot` offers
      only unresolved ones, so the model was never shown it. Approving the row
      would reopen a fulfilled promise and file the new beat into the closed
      record's history.
    - a **different title** is a slug accident. `slugify` strips everything that
      is not `[a-z0-9]`, so it is not merely near-misses that collide: every
      title with no ASCII letters at all — a CJK or Cyrillic one, say — maps to
      the literal `untitled`, and the second such commitment a campaign opens
      would otherwise be swallowed by the first, keeping the first's title and
      leaving the new one with no record of its own.

    Titles are compared case- and space-insensitively; a rename between the two
    absorbs looks like a different commitment here, and opening a second record
    is the safe direction — nothing is lost or overwritten, and the reviewer can
    see both rows.

    `staged` is {id: folded title} for the rows this same batch has already
    placed, and closes the same collision one scope in: two new commitments in
    ONE absorb are both absent from `owed`, so slug-alone would hand them the
    same id and the caller's one-edit-per-commitment dedup would drop the
    second outright. A candidate this batch already took is reusable only when
    it was taken under the same title, which is the case the dedup is for.

    A movement that DOES carry an id keeps pointing where it says, resolved or
    not: that is a reference, and silently redirecting it would be the opposite
    mistake.

    `aliases` ({source id: canonical id}, this record type's live alias map)
    adds one case (spec §7.2): a candidate that is a merged record's SOURCE is
    honoured -- and `_assign_section` then stages onto the canonical -- only
    when the canonical passes the same predicate (unsettled, and titled like the
    row or like the source). A source whose canonical is settled is taken, not
    honoured: following it would reopen the canonical on a title match nobody
    examined, the route §10.5 closes, so the row is allocated `slug-N` and the
    identity step sees the settled canonical as a candidate instead.
    """
    want = title.strip().casefold()

    def _free(candidate: str) -> bool:
        if candidate in staged:
            return staged[candidate] == want
        if aliases and candidate in aliases:
            return _merge_honoured(stored, candidate, aliases[candidate], want, settled)
        cur = stored.get(candidate)
        if not isinstance(cur, dict):
            return True
        if settled(cur):
            return False
        return _text(cur.get("title")).casefold() == want

    n, candidate = 1, slug
    while not _free(candidate):
        n += 1
        candidate = f"{slug}-{n}"
    return candidate


def _merge_honoured(stored: dict, source: str, canonical: str, want: str,
                    settled: Callable[[dict], bool]) -> bool:
    """Whether a slug landing on alias `source` may follow it to `canonical`."""
    cur = stored.get(canonical)
    if not isinstance(cur, dict) or settled(cur):
        return False
    titles = {_text(cur.get("title")).casefold()}
    src = stored.get(source)
    if isinstance(src, dict):
        titles.add(_text(src.get("title")).casefold())
    return want in titles


def _commitment_settled(record: dict) -> bool:
    # `.lower()` for the reason `commitments.open_commitments` folds too, and it
    # has to be repeated because this is a SECOND reader of the same field: a
    # hand-edited `"Fulfilled"` is hidden from the snapshot by that fix, and if
    # this allocator still read it as unresolved the model's new commitment of
    # the same title would land on the record it was never shown -- reopening
    # it, which is exactly what the resolved check exists to prevent. `_text`
    # because the file is hand-editable: a list-valued status reads as "" (not
    # settled) rather than raising out of a paid-for absorb.
    return _text(record.get("status")).lower() in commitments.RESOLVED


def _thread_settled(record: dict) -> bool:
    return _text(record.get("status")).lower() == "closed"


def _new_commitment_id(owed: dict, staged: dict, slug: str, title: str,
                       aliases: dict[str, str] | None = None) -> str:
    """`_new_record_id` for commitments: a resolved record is settled. Kept by
    name because `apply` reallocates a row staged as new through it."""
    return _new_record_id(owed, staged, slug, title, settled=_commitment_settled,
                          aliases=aliases)


def _new_thread_id(threads: dict, staged: dict, slug: str, title: str,
                   aliases: dict[str, str] | None = None) -> str:
    """`_new_record_id` for plot threads: a closed thread is settled. `apply`
    reallocates a plot row staged as new through it, as it does commitments."""
    return _new_record_id(threads, staged, slug, title, settled=_thread_settled,
                          aliases=aliases)


class Assigned(NamedTuple):
    """The id one plot/commitment row stages onto. `existing` says whether that
    id names a stored record; `merged_from` is the alias source the row named
    (by id, or by an honoured slug) when `id` is that source's canonical."""
    id: str
    existing: bool
    merged_from: str | None


def _aliases(live: dict[str, str] | None, kind: str) -> dict[str, str]:
    """`live` (ref -> ref) narrowed to one record type, as bare ids."""
    prefix = f"{kind}:"
    return {src.removeprefix(prefix): dst.removeprefix(prefix)
            for src, dst in (live or {}).items()
            if isinstance(src, str) and isinstance(dst, str)
            and src.startswith(prefix) and dst.startswith(prefix)}


def _redirect(rid: str, stored: dict, aliases: dict[str, str]) -> tuple[str, str | None]:
    """(`rid`'s live canonical, `rid`) when it is a merged record's source and
    the canonical is stored; else (`rid`, None)."""
    canonical = aliases.get(rid)
    if canonical is not None and isinstance(stored.get(canonical), dict):
        return canonical, rid
    return rid, None


def _assign_section(rows: list, stored: dict, section: str,
                    allocate: Callable[[dict, dict, str, str, dict[str, str]], str],
                    out: dict[tuple[str, int], Assigned | None],
                    aliases: dict[str, str]) -> dict[str, str]:
    """Assign one section's rows in order, writing into `out`; returns the
    staged-title map (id -> folded title) the batch built.

    A row naming a merged record's source stages onto its live canonical
    (spec §7.2) -- an explicit id whatever the canonical's status (it is a
    reference; the label says when it reopens one), a slug only where
    `_new_record_id` honoured it. Redirected before the one-edit-per-record
    check, so a source and its canonical in one batch are one edit."""
    seen: set[str] = set()
    staged: dict[str, str] = {}   # id -> folded title, for rows in THIS batch
    for i, e in enumerate(rows):
        out[(section, i)] = None
        beat = (e.get("beat", "") or "").strip()
        if not beat:
            continue
        given = (e.get("id", "") or "").strip()
        title = (e.get("title", "") or "").strip()
        if given:
            # Redirected BEFORE the reservation below, so what is reserved is
            # the canonical under its stored title -- the record the row will
            # actually move.
            rid, source = _redirect(given, stored, aliases)
            # An explicit id is RESERVED too, not just remembered as seen. The
            # allocator consults the store and this map; an explicit id naming
            # a record that does not exist yet is in neither, so a later new
            # row whose title slugs to it was handed the same id -- and then
            # dropped outright by the one-edit-per-record check below, never
            # reaching the reviewer. Reserved under a title, so the same title
            # still merges (that is what the dedup is for) and a different one
            # gets a suffix.
            #
            # Which title is what the id MEANS. For a stored record that is the
            # STORED title, whatever this row says: `materialize` stages the
            # stored title for an existing record and apply never renames, so
            # a row that paraphrases it (`"The old map"` beside `"id":
            # "the-map"`) or omits it (`{"id": "the-debt", "beat": ...}`)
            # carries no title of its own. Reserved under the row's words
            # instead, the same record named by its real title later in the
            # batch missed the merge and staged `the-map-2` -- two rows with one
            # label that the reviewer would approve into a duplicate. The row's
            # title counts only for an id that names nothing stored, where it
            # becomes the new record's title.
            cur = stored.get(rid)
            meant = _text(cur.get("title")).strip() if isinstance(cur, dict) else ""
            staged.setdefault(rid, (meant or title).casefold())
            if source is not None:
                # The SOURCE id is reserved too, under its own stored title. A
                # later id-less row titled like the source slugs onto it; the
                # reservation is checked before the alias map, so the slug is
                # taken, redirected to the canonical, and dropped as a second
                # move of it. Unreserved, `_free` asks `_merge_honoured`, which
                # refuses a settled canonical -- and the row stages `slug-N`, a
                # new record duplicating the source this row already moved.
                src = stored.get(source)
                said = _text(src.get("title")).strip() if isinstance(src, dict) else ""
                staged.setdefault(source, (said or title).casefold())
        elif any(c.isalnum() for c in title):
            # New record — needs a title with real content, and an id that does
            # not land on somebody else's record.
            rid = allocate(stored, staged, slugify(title), title, aliases)
            staged[rid] = title.strip().casefold()
            rid, source = _redirect(rid, stored, aliases)
        else:
            continue  # no id and no usable title -> drop
        if rid in seen:
            continue  # one edit per record per scene (avoids duplicate ids / double-apply)
        seen.add(rid)
        out[(section, i)] = Assigned(rid, isinstance(stored.get(rid), dict), source)
    return staged


def assign_ids(threads: dict, owed: dict | None, parsed: dict,
               live: dict[str, str] | None = None,
               staged: dict[str, dict[str, str]] | None = None,
               ) -> dict[tuple[str, int], Assigned | None]:
    """The ONE id assignment for `plot_movements` and `commitment_movements`.

    Keyed `(section, row index)`. `None` is a row `materialize` drops: a blank
    beat; no id and no usable title; every commitment when the store was
    unreadable (`owed is None`); a later row whose id an earlier row already
    took (one edit per record per scene). Shared so that anything asking which
    record a row will stage onto -- the identity step, materialize itself --
    gets the same answer, explicit-id reservations and `slug-N` allocation
    included. `live` is `continuity.effective.live_canon` -- the alias redirect
    (spec §7.2), which both callers must pass the same map for.

    `staged`, when given, receives each section's staged-title map (id ->
    folded title), so a later allocation in the same batch -- materialize's
    as-new alternatives -- sees every id this batch already reserved.
    """
    out: dict[tuple[str, int], Assigned | None] = {}
    titles: dict[str, dict[str, str]] = {"commitment_movements": {}}
    titles["plot_movements"] = _assign_section(
        parsed.get("plot_movements", []), threads, "plot_movements",
        _new_thread_id, out, _aliases(live, "thread"))
    rows = parsed.get("commitment_movements", [])
    if owed is None:
        out.update({("commitment_movements", i): None for i in range(len(rows))})
    else:
        titles["commitment_movements"] = _assign_section(
            rows, owed, "commitment_movements", _new_commitment_id, out,
            _aliases(live, "commitment"))
    if staged is not None:
        staged.update(titles)
    return out


def _live_canon(cid: str) -> dict[str, str]:
    """The alias map staging redirects through, or {} when it cannot be read.

    Not redirecting hides nothing: a continuity.json the reader cannot parse
    reads as no aliases for every effective reader too, so the source is a
    record of its own everywhere and writing to it is visible."""
    try:
        return continuity_effective.live_canon(cid)
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _merged_head(stored: dict | None, merged: str | None, title: str) -> str:
    """The title a staged row's label starts with: the alias source's own,
    for a row that named it, so the reviewer sees what the model wrote."""
    if merged is None:
        return title
    src = (stored or {}).get(merged)
    return (_text(src.get("title")) if isinstance(src, dict) else "") or merged


def _merged_tail(cur: dict | None, merged: str | None, title: str,
                 settled: Callable[[dict], bool]) -> str:
    """`→ merged into <title>` for a row redirected off an alias source, with
    the canonical's stored status when approving the row would reopen it."""
    if merged is None or not isinstance(cur, dict):
        return ""
    tail = f" → merged into {title}"
    if settled(cur):
        tail += f" ({_text(cur.get('status'))})"
    return tail


def _plot_edit(sid: str, row: dict, pid: str, threads: dict, *,
               merged: str | None = None) -> dict:
    """The staged edit (without its review block) moving thread `pid` by `row`.

    One body for a primary row and for every alternative the identity check
    offers, so an alternative's label, payload and `before` token are
    byte-identical to what a primary row on that thread would carry.
    `merged` is the alias source the row named, when `pid` is its canonical.
    """
    beat = (row.get("beat", "") or "").strip()
    title = (row.get("title", "") or "").strip()
    status = row.get("status", "open")
    cur = threads.get(pid)
    # An existing thread: named by id, or by a new title whose slug collides
    # with an OPEN thread of the SAME title -- the only collision
    # `_new_thread_id` honours (§10.5); any other collision was given a
    # fresh `slug-N` and lands in the else branch as a new thread.
    if isinstance(cur, dict):
        # Rendered by `conflicts`, not here: the staleness check recomputes
        # this same line at save time, and two copies of the format would
        # let a harmless reformat read as a contradiction (#111).
        before = conflicts.plot_line(cur)
        disp_title = cur.get("title") or title or pid  # keep the stored title
    else:
        before, disp_title = "", title or pid
    label = (f"{_merged_head(threads, merged, disp_title)} — {status}"
             f"{_merged_tail(cur, merged, disp_title, _thread_settled)}")
    return {"id": f"plot:{pid}", "kind": "plot",
            "target": {"kind": "plot", "id": pid},
            "label": label,
            "field": "beat", "before": before, "after": beat, "authored": False,
            "payload": {"id": pid, "title": disp_title, "status": status,
                        "scene": sid}}


def _commitment_edit(sid: str, row: dict, mid: str, owed: dict, *,
                     merged: str | None = None) -> dict:
    """The staged edit (without its review block) moving commitment `mid`.

    Same shape as `_plot_edit`, and deliberately a second function rather
    than a parameterized shared one: the two record types agree on "id or a
    slugged title, one edit per record per scene" -- which `assign_ids`
    decides for both -- and on nothing else -- the label, the payload and the
    vocabulary the status is drawn from all differ, so the factored version
    would be a function whose body is mostly branches on which of the two
    called it.
    """
    beat = (row.get("beat", "") or "").strip()
    title = (row.get("title", "") or "").strip()
    # Blank means "the model said nothing" -- see parse.py. Carried into the
    # payload AS blank so `set_movement` keeps the stored value; the label
    # below shows the resolved value the reviewer will actually get.
    kind = (row.get("kind", "") or "").strip()
    status = (row.get("status", "") or "").strip()
    # None, not "": the key's PRESENCE is the signal (see parse.py). "" is
    # an instruction to clear the deadline; absent means leave it alone.
    due = _text(row["due"]) if "due" in row else None
    cur = owed.get(mid)
    if isinstance(cur, dict):  # existing commitment (by id, or a colliding new title)
        # The STORED head, deadline included: `due` is applied on save and
        # then steers the ledger and every later scene prompt, so a model
        # that invents or overwrites one must not be able to do it in a row
        # whose only visible text is the beat. Here it is what the deadline
        # was; the label below is what it will be. It doubles as the
        # staleness token `apply_edits` re-checks at save time.
        before = conflicts.commitment_line(cur)
        stored_due = _text(cur.get("due"))
        disp_title = _text(cur.get("title")) or title or mid  # keep the stored title
        # What the record will read AFTER the save: the model's value where
        # it gave one, the stored value where it did not.
        disp_kind = kind or _text(cur.get("kind")) or "promise"
        disp_status = status or _text(cur.get("status")) or "open"
        disp_due = stored_due if due is None else due
    else:
        before, disp_title = "", title or mid
        disp_kind = kind or "promise"      # set_movement's own defaults, for
        disp_status = status or "open"     # a commitment being created here
        disp_due = due or ""
    label = f"{_merged_head(owed, merged, disp_title)} — {disp_kind}, {disp_status}"
    if disp_due:
        label += f", due {disp_due}"
    label += _merged_tail(cur, merged, disp_title, _commitment_settled)
    return {"id": f"commitment:{mid}", "kind": "commitment",
            "target": {"kind": "commitments", "id": mid},
            "label": label,
            "field": "beat", "before": before, "after": beat, "authored": False,
            "payload": {"id": mid, "title": disp_title, "kind": kind,
                        "status": status, "due": due, "scene": sid}}


def _accepted_existing(ic) -> bool:
    return (isinstance(ic, dict) and ic.get("decision") == "existing"
            and ic.get("status") == "accepted")


def _recheck_accepted(parsed: dict, threads: dict, owed: dict | None,
                      live: dict[str, str]) -> dict:
    """`parsed` with every accepted ``existing`` row whose target is no longer
    live put back as the new row the model wrote, at ``uncertain``.

    `materialize` runs after the slowest absorb phase, so a record the
    resolver's answer named can be closed or resolved in that window -- a
    Ledger edit, another scene's review save, neither refused by this scene's
    busy flag. Identity resolution never reopens a record (spec §10.2), so the
    target is re-read here, through the CURRENT alias map, and a row whose
    target is gone or settled stages as its `AS_NEW_KEY` original with a
    ``downgraded`` check -- band ``low``, its live candidates still offered.
    A shallow copy; `parsed` is not mutated. A row without its original is
    not one `identity.rewritten` wrote, and is left alone."""
    out = dict(parsed)
    for section, kind in _IDENTITY_SECTIONS.items():
        rows = parsed.get(section)
        stored = threads if kind == "thread" else owed
        if isinstance(rows, list) and isinstance(stored, dict):
            out[section] = _recheck_section(rows, stored, kind, _aliases(live, kind))
    return out


def _recheck_section(rows: list, stored: dict, kind: str, aliases: dict[str, str]) -> list:
    rows = list(rows)
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not _accepted_existing(row.get("identity_check")):
            continue
        original = row.get(AS_NEW_KEY)
        target = _text(row.get("id"))
        cur = stored.get(_redirect(target, stored, aliases)[0])
        if not isinstance(original, dict) or (
                isinstance(cur, dict) and continuity_effective.is_live(kind, cur.get("status"))):
            continue
        rows[i] = {**original, "identity_check": {
            **row["identity_check"], "decision": "uncertain", "status": "downgraded",
            "reason": CLOSED_SINCE}}
        # `identity.rewritten` retargeted this row's siblings -- later rows
        # dropped only because the proposed row held their id -- onto the same
        # record, so they would stay a dropped second move. With the row back
        # on its own id they would instead stage onto the settled target and
        # reopen it, so they are dropped here, as they were before any check.
        # Nothing else in the batch can name the target: an accepted
        # ``existing`` is downgraded when another row already moves it.
        for j in range(i + 1, len(rows)):
            sib = rows[j]
            if (isinstance(sib, dict) and "identity_check" not in sib
                    and _text(sib.get("id")) == target):
                rows[j] = {**sib, "beat": ""}
    return rows


def _onto_existing(kind: str, row: dict, rid: str) -> dict:
    """The §10.2 rewrite of `row` onto the existing record `rid` -- the rules
    `continuity.identity._onto_existing` applies to an accepted row, restated
    because materializer cannot import identity. The title is blank (keep
    stored); a plot status is the model's ``closed`` or ``advanced``, else
    ``advanced``, never ``open``; a commitment keeps its stored kind and a
    status only when it resolves the record; ``due`` stays as present or
    absent."""
    new = {**row, "id": rid, "title": ""}
    status = row.get("status")
    if kind == "thread":
        new["status"] = status if status in ("closed", "advanced") else "advanced"
    else:
        new["kind"] = ""
        new["status"] = status if status in commitments.RESOLVED else ""
    return new


def _fresh_id(stored: dict, staged: dict[str, str], slug: str) -> str:
    """`slug`, or the first `slug-N` naming nothing stored and nothing this
    batch reserved -- under any title. Not `_new_*_id`, which honours a
    same-titled open record and reuses a same-titled staged id: either would
    make the as-new alternative something other than a new record."""
    n, candidate = 1, slug
    while candidate in stored or candidate in staged:
        n += 1
        candidate = f"{slug}-{n}"
    return candidate


def _candidate_alternatives(sid: str, kind: str, original: dict, ic: dict, stored: dict,
                            live: dict[str, str], taken: set[tuple[str, str]]) -> list[dict]:
    """One staged row per candidate that is still a live, untaken record.

    Revalidated against the state materialize read, never the examination's
    snapshot: each candidate is followed through the CURRENT alias map, and
    its canonical must be stored, live, and moved by no edit in this batch
    (this row's own target included). Two candidates that now share a
    canonical are one alternative."""
    build = _plot_edit if kind == "thread" else _commitment_edit
    target_kind = "plot" if kind == "thread" else "commitments"
    alts: list[dict] = []
    for cand in ic.get("candidates") or []:
        ref = cand.get("ref") if isinstance(cand, dict) else None
        prefix, _, rid = live.get(ref, ref).partition(":") if isinstance(ref, str) else ("", "", "")
        cur = stored.get(rid) if prefix == kind else None
        if (not isinstance(cur, dict) or (target_kind, rid) in taken
                or not continuity_effective.is_live(kind, cur.get("status"))):
            continue
        taken = taken | {(target_kind, rid)}
        alts.append(build(sid, _onto_existing(kind, original, rid), rid, stored))
    return alts


def _as_new_alternative(sid: str, kind: str, original: dict, stored: dict,
                        staged: dict[str, str]) -> dict:
    """The model's own row on a record of its own: what an accepted
    ``existing`` is one click from, if the match was wrong.

    Its explicit id is kept only when free and slug-shaped -- a model-written
    id such as ``plot/the-map`` would create a record the Ledger's routes
    cannot address. The id is reserved in `staged`, so two as-new variants
    never share one."""
    given, title = _text(original.get("id")), _text(original.get("title"))
    if given and slugify(given) == given and given not in stored and given not in staged:
        rid = given
    else:
        rid = _fresh_id(stored, staged, slugify(title or given))
    staged[rid] = (title or rid).casefold()
    build = _plot_edit if kind == "thread" else _commitment_edit
    return build(sid, {**original, "id": rid}, rid, stored)


def _attach_alternatives(sid: str, out: list[dict], rows_by_edit: dict[str, dict],
                         threads: dict, owed: dict, live: dict[str, str],
                         staged_plot_titles: dict[str, str],
                         staged_titles: dict[str, str]) -> None:
    """Fill each examined row's `identity_check.alternatives` (spec §10.3).

    A second pass, after every primary row is staged, so "not targeted
    elsewhere in the batch" and the as-new allocation see the whole batch.
    Alternatives are built by the same helpers as primary rows, so their
    labels, payloads and `before` tokens are what a primary row on that record
    would carry. Each copies its row's review block and carries no
    `identity_check`. Assigned only once every row's list is built, so a
    failure part-way leaves every list at ``[]``."""
    taken = {(e["target"]["kind"], e["target"]["id"]) for e in out
             if e.get("kind") in ("plot", "commitment")}
    found: dict[str, list[dict]] = {}
    for edit in out:
        ic, row = edit.get("identity_check"), rows_by_edit.get(edit["id"])
        if not isinstance(ic, dict) or row is None:
            continue
        kind = "thread" if edit["kind"] == "plot" else "commitment"
        stored, staged = (threads, staged_plot_titles) if kind == "thread" else (owed, staged_titles)
        original = row.get(AS_NEW_KEY) if _accepted_existing(ic) else None
        alts = _candidate_alternatives(sid, kind, original if isinstance(original, dict) else row,
                                       ic, stored, live, taken)
        if isinstance(original, dict):
            alts.append(_as_new_alternative(sid, kind, original, stored, staged))
        found[edit["id"]] = [{**alt, "review": dict(edit["review"])} for alt in alts]
    for edit in out:
        if edit["id"] in found:
            edit["identity_check"]["alternatives"] = found[edit["id"]]


def _recorded_here(ledger: dict, sid: str, text: str) -> bool:
    """Whether this scene has EVER recorded this standing fact.

    Ever, not "and it is still standing" -- the same rule `facts.record`'s own
    dedup keeps, and for the reason spelled out there: a fact this scene
    recorded and a LATER scene retired is invisible to an active-only lookup, so
    re-absorbing this scene would stage the sentence again and put a truth the
    later scene ended back on the ledger. The scene id is what separates a
    re-extraction from a genuine re-establishment, so status has no work to do
    in this predicate.

    Case-insensitive, like `_new_commitment_id` compares titles: the two absorbs
    of one scene are two model replies, and a re-extraction that differs only in
    capitalisation is the same fact rather than a second one.
    """
    return bool(facts.find(ledger, sid, text))


def _fact_label(text: str, supersedes: str, date: str) -> str:
    """What a fact row is doing, in the reviewer's words.

    Deliberately short and carrying no fact text of its own: the row's
    before/after IS the two facts, rendered as a diff, so repeating either here
    would only give the panel a second, truncated copy to disagree with.
    """
    label = "Fact retired" if not text else ("Fact superseded" if supersedes else "New fact")
    return f"{label} — {date}" if date else label


def _pc_name(cid: str, pid: str) -> str | None:
    """The persona name at the version the campaign locked, or None when the PC
    is not in the appearance record or its persona will not read -- which is
    what a state edit for a PC requires, the way a character's requires its
    card."""
    vid = appearances_versions.locked_version(cid, "pcs", pid)
    if vid is None:
        return None
    try:
        persona = pcs.read_persona(overlay.pc_root(cid, pid), pid, vid)
    except (pcs.PCNotFound, pcs.PCVersionNotFound):
        return None
    name = persona.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else pid


def _character_state_edit(cid: str, kind: str, char_id: str, before: str, after: str,
                          name: str | None = None) -> dict:
    """A `character_state` row for a character or, with `kind="pcs"`, a player
    character. The id keeps its old spelling for characters, so a review staged
    before PCs had state still names the same row."""
    eid = f"character_state:{char_id}" if kind == "characters" else f"character_state:pcs:{char_id}"
    label = name or (_char_name(cid, char_id) if kind == "characters" else char_id)
    return {"id": eid, "kind": "character_state",
            "target": {"kind": kind, "id": char_id},
            "label": f"{label} — current state",
            "field": "current_state",
            "before": before, "after": after, "authored": False}


def materialize(cid: str, sid: str, parsed: dict,
                messages: list[dict] | None = None,
                player_label: str | None = None, *,
                on_identity_error: Callable[[BaseException], None] | None = None,
                ) -> list[dict]:
    """Turn the parsed edit lists into before/after StagedEdits against the campaign
    copies. Targets that don't exist are dropped (tolerated, not an error).

    `messages` is the transcript the extraction call was SHOWN. Pass it whenever
    the caller has it -- the citations are judged against it, and the scene can
    move between rendering the prompt and this call (see `routing.speaker_index`).
    `player_label` is the same snapshot one layer down: the name the prompt's
    transcript put on the player's unstamped posts, which a rename landing
    mid-call would otherwise change underneath the citations.

    Rows the identity step examined carry `identity_check`; they are staged
    with it (alternatives added), at band ``low`` when the check found a
    possible duplicate or never answered. The two identity-only passes --
    re-checking accepted targets, building alternatives -- run outside every
    absorb phase boundary, so a defect in either stages the rows without it
    rather than failing a paid-for extraction: it is recorded and reported
    to `on_identity_error`, and the return shape is unchanged.
    """
    croot = campaigns_paths.campaign_root(cid)
    out: list[dict] = []
    # Once per absorb, not once per edit: every row is checked against the same
    # transcript, and the index costs a scene read and a cast read.
    index = routing.speaker_index(cid, sid, messages, player_label)

    def _staged(edit: dict, row: dict, *subjects: str) -> dict:
        """One StagedEdit, stamped with the review block the panel routes on.

        Every `out.append` in this function goes through this rather than
        writing the key itself. A row that skipped it would arrive at the
        reviewer looking like the rows that genuinely have no citation to check
        -- the dossier, voice and sheet proposals staged elsewhere -- and so be
        pre-approved on the strength of a signal that was actually present and
        simply dropped. `subjects` are the actors the record BELONGS to (see
        `routing.authority`); a record that belongs to nobody passes none.

        A row the identity step examined carries its `identity_check` onto
        the edit (alternatives filled in later), and is forced to band
        ``low`` when the check found a possible duplicate or never answered;
        the citation's own score and quote are reported unchanged.
        """
        row = dict(row)
        row.pop(AS_NEW_KEY, None)
        edit["review"] = routing.review(index, row, subjects)
        ic = row.get("identity_check")
        if isinstance(ic, dict):
            edit["identity_check"] = {**ic, "alternatives": []}
            if ic.get("decision") in _LOW_DECISIONS:
                edit["review"] = {**edit["review"], "band": "low"}
        return edit

    def _identity_failed(exc: BaseException) -> None:
        errors.record_exception(exc, "continuity-identity", campaign=cid, scene=sid)
        if on_identity_error is not None:
            on_identity_error(exc)

    for e in parsed.get("character_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        # The model echoes ids from the "Present: <kind>/<id>, ..." context line (or,
        # less reliably, a bare id) — strip any "characters/", "characters:", "pcs/" or
        # "pcs:" prefix so every form resolves. A bare id is a character's. A PC keeps
        # its current state only (Knows/Suspects stay non-player), filed under pcs/ so
        # it is never misfiled under characters/ with the PC's id as a character slug.
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        if kind not in ("characters", "pcs"):
            kind, rest = "characters", raw_id
        char_id = rest
        name: str | None = None
        if kind == "pcs":
            name = _pc_name(cid, char_id)
            if name is None:
                continue
        else:
            try:
                # overlay-aware: a thin campaign's NPC is usually still inherited
                # (never appeared/materialized), and a state edit for it must not
                # be silently dropped just because croot lacks the character dir
                characters.read_character(overlay.char_root(cid, char_id), char_id)
            except characters.CharacterNotFound:
                continue
        st = playstate.read_state(croot, char_id, kind)
        if kind == "pcs":
            cur_knows = cur_suspects = knows = suspects = ""
        else:
            cur_knows = st["knows"] if st else ""
            cur_suspects = st["suspects"] if st else ""
            # Keep-on-omit: an omitted knows/suspects preserves the stored value; an
            # explicit "" clears it. Prevents an absorb that only touches
            # current_state from silently erasing established knowledge.
            knows = e["knows"] if "knows" in e else cur_knows
            suspects = e["suspects"] if "suspects" in e else cur_suspects
        after = playstate.compose_body(e.get("current_state", ""), knows, suspects)
        before = playstate.compose_body(st["current_state"], cur_knows, cur_suspects) if st else ""
        # An empty result is dropped for a character, whose state the play
        # loop also feeds. A PC's state.md is written by absorb and nothing
        # else, so an explicit "" (the state is over) is the only way it ever
        # stops standing: staged when there is a state to clear. An omitted
        # current_state stays "nothing to say", as knows/suspects' omission is.
        clears = kind == "pcs" and "current_state" in e and bool(before)
        if not after and not clears:
            continue
        if before == after:
            continue
        out.append(_staged(_character_state_edit(cid, kind, char_id, before, after, name),
                           e, f"{kind}:{char_id}"))

    for e in parsed.get("group_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        gid = rest if kind == "groups" else raw_id
        try:
            name = overlay.read_entity(cid, "groups", gid)["meta"].get("name", gid)
        except entities.EntityNotFound:
            continue
        st = groupstate.read_state(croot, gid)
        cur = {k: (st[k] if st else "") for k in groupstate.FIELDS}
        new = {k: (e[k] if k in e else cur[k]) for k in groupstate.FIELDS}
        after = groupstate.compose_body(new)
        if not after:
            continue
        before = groupstate.compose_body(cur) if st else ""
        if before == after:
            continue
        out.append(_staged({"id": f"group_state:{gid}", "kind": "group_state",
                            "target": {"kind": "groups", "id": gid},
                            "label": f"{name} — group state", "field": "group_state",
                            "before": before, "after": after, "authored": False}, e))

    for e in parsed.get("lore_edits", []):
        raw_id, append = e.get("id", ""), (e.get("append", "") or "").strip()
        if not raw_id or not append:
            continue
        target = _entity_target(cid, raw_id)
        if not target:
            continue
        kind, eid, ent = target
        before = ent["body"].strip()
        after = (before + "\n\n" + append).strip()
        # The staged id carries the KIND as well as the record's, because two
        # kinds can hold the same slug and a qualified id can reach both of them
        # in one absorb. The reviewer's panel keys a row by this id (its React
        # key, and the lookup that hangs a contradiction warning on it), so two
        # rows sharing one would hand one record's warning to the other's row.
        out.append(_staged({"id": f"lore:{kind}/{eid}", "kind": "lore",
                            "target": {"kind": kind, "id": eid},
                            "label": f"{ent['meta'].get('name', eid)} — {kind}", "field": "body",
                            "before": before, "after": after, "authored": False}, e))

    for e in parsed.get("authored_edits", []):
        char_id, field, text = e.get("id", ""), e.get("field", ""), (e.get("text", "") or "").strip()
        if not char_id or field not in _CARD_FIELDS or not text:
            continue
        vid = appearances_versions.locked_version(cid, "characters", char_id)
        if not vid:
            continue
        try:
            # locked_version returned a version, so the actor is in the appearance
            # record and its card is materialized campaign-side
            before = characters.read_card(appearances_paths.locked_actor_root(cid),
                                          char_id, vid)["data"].get(field, "").strip()
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        out.append(_staged({"id": f"authored:{char_id}:{field}", "kind": "authored",
                            "target": {"kind": "characters", "id": char_id},
                            "label": f"{_char_name(cid, char_id)} — {field} (card edit)",
                            "field": field, "before": before, "after": text, "authored": True},
                           e, f"characters:{char_id}"))

    for e in parsed.get("relationship_deltas", []):
        frm, to = e.get("from", ""), e.get("to", "")
        if not _actor_exists(cid, frm) or not _actor_exists(cid, to):
            continue
        payload = {"from": frm, "to": to, "trust": e.get("trust", 0), "affection": e.get("affection", 0),
                   "tension": e.get("tension", 0), "note": e.get("note", "")}
        after = relationships._render_feeling(payload)
        cur = relationships.get_feeling(cid, frm, to)
        before = relationships._render_feeling(cur) if cur else ""
        if before == after:
            continue
        # `frm` alone is the subject: the feeling is the FROM side's, so the TO
        # side describing it is a third party's read of somebody else's heart.
        out.append(_staged({"id": f"feeling:{relationships.feeling_key(frm, to)}",
                            "kind": "relationship",
                            "target": {"kind": "relationships",
                                       "id": relationships.feeling_key(frm, to)},
                            "label": f"{relationships.actor_name(cid, frm)} → {relationships.actor_name(cid, to)}",
                            "field": "feeling", "before": before, "after": after,
                            "authored": False, "payload": payload}, e, frm))

    for e in parsed.get("bond_changes", []):
        a_tok, b_tok, typ = e.get("a", ""), e.get("b", ""), (e.get("type", "") or "").strip()
        if not typ or not _actor_exists(cid, a_tok) or not _actor_exists(cid, b_tok):
            continue
        cur = relationships.get_bond(cid, a_tok, b_tok)
        before = cur["type"] if cur else ""
        if before == typ:
            continue
        # Both ends are subjects, unlike a feeling: a bond is the pair's shared
        # relationship type, so either of them naming it is first-hand.
        out.append(_staged({"id": f"bond:{relationships.bond_key(a_tok, b_tok)}", "kind": "bond",
                            "target": {"kind": "relationships",
                                       "id": relationships.bond_key(a_tok, b_tok)},
                            "label": f"{relationships.actor_name(cid, a_tok)} & {relationships.actor_name(cid, b_tok)}",
                            "field": "bond", "before": before, "after": typ, "authored": False,
                            "payload": {"a": a_tok, "b": b_tok, "type": typ}},
                           e, a_tok, b_tok))

    try:
        threads = plot.read(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json: skip plot movements, don't 500
        threads = {}
    try:
        owed = commitments.read(cid)
    except Exception:  # noqa: BLE001 — garbled commitments.json: skip these, don't 500
        owed = None
    if not isinstance(owed, dict):
        # `read` is a bare json.loads, so a commitments.json holding `[]` is
        # valid JSON of the wrong shape: it raises nothing and `owed.get` below
        # would then throw. That happens AFTER the extraction call, turning a
        # paid-for absorb into a 500 rather than a dropped section.
        owed = None
    # An UNREADABLE store stages nothing, where an empty one stages normally.
    # Falling back to {} conflates the two and every movement is staged as a new
    # commitment -- a row whose `before` says "nothing is stored" when the truth
    # is unknown, and whose save is worse than the lie: `apply_edits` hits the
    # same broken read, its per-edit `except` swallows it, and the reviewer's
    # panel closes on a 200 with the approved commitment gone and no failure
    # reported. Staging nothing costs this section (the same price a garbled
    # file already pays in `render_open` and the ledger) and cannot lose an
    # approval, because there is no approval to lose.
    #
    # Which record each plot/commitment row stages onto, decided once for both
    # sections by the same `assign_ids` the identity step asks -- so the two can
    # never disagree about which slug collisions are honoured.
    #
    # A row naming a merged record's source is staged onto its live canonical
    # (spec §7.2): the id, payload and `before` are the canonical's, the PHYSICAL
    # record `apply` writes and `conflicts` re-reads at save time; the label
    # keeps the source's title and says where the beat is going.
    #
    # An accepted identity retarget whose record stopped being live since the
    # resolver answered is put back first, so it is assigned as the new row.
    live = _live_canon(cid)
    try:
        parsed = _recheck_accepted(parsed, threads, owed, live)
    except Exception as exc:  # noqa: BLE001 -- an identity-only pass; a defect stages the rows without it, never fails the absorb
        _identity_failed(exc)
    staged_titles: dict[str, dict[str, str]] = {}
    assigned = assign_ids(threads, owed, parsed, live, staged_titles)
    rows_by_edit: dict[str, dict] = {}   # plot/commitment edit id -> its parsed row
    for i, e in enumerate(parsed.get("plot_movements", [])):
        slot = assigned[("plot_movements", i)]
        if slot is None:
            continue  # blank beat, no usable id or title, or a second edit to one thread
        edit = _plot_edit(sid, e, slot.id, threads, merged=slot.merged_from)
        out.append(_staged(edit, e))
        rows_by_edit[edit["id"]] = e

    for i, e in enumerate(parsed.get("commitment_movements", []) if owed is not None else []):
        slot = assigned[("commitment_movements", i)]
        if slot is None:
            continue  # blank beat, no usable id or title, or a second edit to one commitment
        # `or {}` only for the type checker: this loop runs when `owed` is a dict.
        edit = _commitment_edit(sid, e, slot.id, owed or {}, merged=slot.merged_from)
        out.append(_staged(edit, e))
        rows_by_edit[edit["id"]] = e

    # The identity check's alternatives, once the whole batch is staged.
    try:
        _attach_alternatives(sid, out, rows_by_edit, threads, owed or {}, live,
                             staged_titles["plot_movements"],
                             staged_titles["commitment_movements"])
    except Exception as exc:  # noqa: BLE001 -- an identity-only pass; a defect stages the rows without it, never fails the absorb
        _identity_failed(exc)

    # The fact ledger (#114). One section and one edit kind covering two
    # operations, because a row does one thing to one record and only the row
    # can say which: text RECORDS a standing fact -- retiring the fact it names,
    # if it names one -- and a bare `supersedes` retires that fact outright,
    # with nothing put in its place. Splitting them would double the contract
    # the model has to hold, the branch `apply` has to write and the vocabulary
    # the reviewer has to read, to distinguish two rows that already read
    # differently in the diff.
    try:
        ledger = facts.read(cid)
    except Exception:  # noqa: BLE001 — garbled facts.json: skip these, don't 500
        ledger = None
    if not isinstance(ledger, dict):
        # An UNREADABLE ledger stages nothing, for the reason spelled out over
        # `owed` above: falling back to {} would stage every supersession as an
        # ordinary new fact, hiding the retirement the reviewer approved behind
        # a row that claims to retire nothing.
        ledger = None
    retiring: set[str] = set()
    staged_texts: set[str] = set()   # folded text of the new facts THIS batch stages
    for n, e in enumerate(parsed.get("facts", []) if ledger is not None else []):
        text, date = _text(e.get("text")), _text(e.get("date"))
        sup = _text(e.get("supersedes"))
        prior = ledger.get(sup) if sup else None
        if text and facts.is_active(prior) and facts.restates(prior, text):
            # A RESTATEMENT, not a supersession -- see `facts.restates` for what
            # it costs. The prompt says not to report one and the model does
            # anyway, which is why this is in code rather than only in the
            # prompt. Nothing about the world moved, so the row does not either.
            #
            # `apply` checks again rather than trusting this: the reviewer can
            # edit the replacement text into a restatement after the row was
            # staged, and that path never comes back through here.
            #
            # `text and` guards the BARE RETIREMENT, whose "" would otherwise
            # match a stored fact whose own text reads as "" -- which `record`
            # never writes but a hand-edited or malformed record supplies, and
            # that is exactly the record most worth being able to retire.
            continue
        if not facts.is_active(prior) or facts.recorded_after(prior, sid):
            # A `supersedes` naming a fact that is retired, missing or malformed
            # is dropped rather than obeyed. The snapshot offers only standing
            # facts, so the model was never shown that record, and retiring an
            # already-retired fact would overwrite the pointer saying what
            # really replaced it.
            #
            # So is one recorded AFTER this scene, which `facts.record` will
            # refuse to write (see `recorded_after`): the snapshot is scoped to
            # this scene precisely so the model is not offered it, and a row
            # that names one anyway has to be STAGED as what it will actually
            # do. Left labelled a supersession, the reviewer approves a
            # retirement that silently does not happen.
            #
            # The row's other half survives either way: what is left is an
            # ordinary new fact, or -- when the row carried no text either --
            # nothing, and it is dropped below.
            sup, prior = "", None
        if not text and not sup:
            continue
        if sup:
            if sup in retiring:
                continue   # one retirement per fact per scene: the second would
            retiring.add(sup)   # retire a record the first already retired
        elif text.casefold() in staged_texts or _recorded_here(ledger, sid, text):
            # Two ways for a row to have nothing left to do, and both end as a
            # duplicate the reviewer approves and does not get:
            #
            # - the scene ALREADY recorded this fact. Absorbing a scene twice is
            #   supported (`POST .../absorb?force`) and re-proposes every fact
            #   the first pass found -- the `timeline.md` re-append this ledger
            #   exists to improve on.
            # - this BATCH already stages it. A reply that says the same thing
            #   in two rows is invisible to the check above, which reads a
            #   ledger neither row has reached yet; `facts.record` would dedupe
            #   the second onto the first at save time and report both as
            #   applied, so two approvals produce one fact with nothing saying
            #   so.
            #
            # `facts.record` dedupes as well and has to: this reads a snapshot
            # that can be stale. What happens here is keeping the row off the
            # panel in the first place.
            continue
        # Recorded for every row that survives, but CONSULTED only by rows that
        # retire nothing. Two rows superseding two different facts with the same
        # replacement text are both real work -- `facts.record` files one fact
        # and each row retires its own predecessor onto it -- so dropping the
        # second would leave a fact standing that the scene ended.
        if text:
            staged_texts.add(text.casefold())
        # A row that retires something addresses THAT record, so that is what
        # `target` names and what `conflicts` judges -- the write it authorizes
        # is the retirement. Recording the new fact creates a record and
        # overwrites nothing, so it needs no target and gets its id at save time
        # (`new_character` stages the same way, and for the same reason).
        # Through `_staged` like every other row: a fact reaches the reviewer as
        # a StagedEdit, so it is routed on the same evidence as the rest. No
        # subjects -- a standing truth about the world belongs to nobody, so no
        # speaker can be first-hand about it (see `routing.authority`).
        out.append(_staged({"id": f"fact:{sup}" if sup else f"fact:{sid}:{n}", "kind": "fact",
                    "target": {"kind": "facts", "id": sup},
                    "label": _fact_label(text, sup, date),
                    "field": "text",
                    # `before` is the retired fact's line, `after` the new
                    # fact's text: the diff reads as the replacement it is, and
                    # a retirement with nothing to replace it reads as the
                    # deletion it is.
                    "before": conflicts.fact_line(prior) if prior else "",
                    "after": text, "authored": False,
                    "payload": {"text": text, "date": date, "supersedes": sup,
                                "scene": sid}}, e))

    # Names only, so the roster: the full listing's per-character image scan
    # would be read here for nothing.
    existing_char_names = {c["name"].strip().lower() for c in overlay.character_roster(cid)}
    for e in parsed.get("new_characters", []):
        name = (e.get("name", "") or "").strip()
        description = (e.get("description", "") or "").strip()
        if not name or not description:
            continue
        if name.lower() in existing_char_names:
            continue
        candidate_id = slugify(name)
        try:
            characters.read_character(overlay.char_root(cid, candidate_id), candidate_id)
            continue  # id already taken -- treat as the same character
        except characters.CharacterNotFound:
            pass
        # The reviewed description is the W++ block plus the generated history, so the
        # staged diff shows the full text that lands in the card's description field.
        history = (e.get("history", "") or "").strip()
        after = f"{description}\n\n{history}" if history else description
        # No subject: the person has no record yet, so nothing they said in the
        # scene can be first-hand ABOUT a record. Their own lines still
        # corroborate the citation, which is what separates a proposal drawn
        # from dialogue from one drawn from nowhere.
        out.append(_staged({"id": f"new_character:{candidate_id}", "kind": "new_character",
                            "target": {"kind": "characters", "id": ""},
                            "label": f"New character — {name}", "field": "description",
                            "before": "", "after": after, "authored": False,
                            "payload": {"name": name, "sd_prompt": e.get("sd_prompt", ""),
                                        "personality": e.get("personality", ""),
                                        "mes_example": e.get("mes_example", ""),
                                        "evidence": e.get("evidence", ""),
                                        "confidence": parse._confidence(e.get("confidence", "")),
                                        "open_questions": e.get("open_questions", "")}}, e))

    for kind, parsed_key, prefix, label_noun in (
        ("locations", "new_locations", "new_location", "location"),
        ("lore", "new_lore", "new_lore", "lore entry"),
    ):
        existing_names = {ent["name"].strip().lower() for ent in overlay.list_entities(cid, kind)}
        for e in parsed.get(parsed_key, []):
            name = (e.get("name", "") or "").strip()
            body = (e.get("body", "") or "").strip()
            if not name or not body:
                continue
            if name.lower() in existing_names:
                continue
            candidate_id = slugify(name)
            try:
                overlay.read_entity(cid, kind, candidate_id)
                continue
            except entities.EntityNotFound:
                pass
            payload = {"name": name, "keys": e.get("keys", "")}
            if kind == "locations":
                payload["sd_prompt"] = e.get("sd_prompt", "")
                payload["current_setting"] = e.get("current_setting", False)
            out.append(_staged({"id": f"{prefix}:{candidate_id}", "kind": prefix,
                                "target": {"kind": kind, "id": ""},
                                "label": f"New {label_noun} — {name}", "field": "body",
                                "before": "", "after": body, "authored": False,
                                "payload": payload}, e))

    out.extend(weather._weather_edits(cid, sid, parsed, index))
    return out


def _new_character_provenance(after: str, payload: dict) -> str:
    lines = []
    evidence = (payload.get("evidence", "") or "").strip()
    confidence = parse._confidence(payload.get("confidence", ""))
    open_questions = (payload.get("open_questions", "") or "").strip()
    if evidence:
        lines.append(f"Evidence: {evidence}")
    lines.append(f"Confidence: {confidence}")
    if open_questions:
        lines.append(f"Open questions: {open_questions}")
    return (after.rstrip() + "\n\n## Play Provenance\n" + "\n".join(lines)).strip()


def _new_character_dossier(name: str, payload: dict) -> str:
    confidence = parse._confidence(payload.get("confidence", ""))
    evidence = (payload.get("evidence", "") or "").strip()
    open_questions = (payload.get("open_questions", "") or "").strip()
    parts = [f"{name} was introduced through play as a {confidence} emergent character."]
    if evidence:
        parts.append(f"Scene evidence: {evidence}")
    if open_questions:
        parts.append(f"Open questions: {open_questions}")
    return " ".join(parts)
