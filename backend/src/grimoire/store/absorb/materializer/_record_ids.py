"""One id assignment for continuity rows, canonical redirects and collision/settled-record decisions."""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from ... import commitments, fieldtext
from ...continuity import effective as continuity_effective
from ...paths import slugify


# Coerce hand-edited commitment fields at every read: a malformed status or due
# must not turn the already-paid extraction into a 500 after its top-level read succeeds.
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
        return fieldtext.text(cur.get("title")).casefold() == want

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
    titles = {fieldtext.text(cur.get("title")).casefold()}
    src = stored.get(source)
    if isinstance(src, dict):
        titles.add(fieldtext.text(src.get("title")).casefold())
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
    return fieldtext.text(record.get("status")).lower() in commitments.RESOLVED


def _thread_settled(record: dict) -> bool:
    return fieldtext.text(record.get("status")).lower() == "closed"


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
            meant = fieldtext.text(cur.get("title")).strip() if isinstance(cur, dict) else ""
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
                said = fieldtext.text(src.get("title")).strip() if isinstance(src, dict) else ""
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
