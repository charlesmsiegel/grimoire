"""Canonical continuity movement edits, live-target rechecks and review alternatives between the existing identity-only barriers."""

from __future__ import annotations

from collections.abc import Callable

from ... import commitments, fieldtext, plot
from ...continuity import effective as continuity_effective
from ...continuity import retarget
from ...paths import slugify
from .. import conflicts, staging
from . import _contracts, _record_ids


def _merged_head(stored: dict | None, merged: str | None, title: str) -> str:
    """The title a staged row's label starts with: the alias source's own,
    for a row that named it, so the reviewer sees what the model wrote."""
    if merged is None:
        return title
    src = (stored or {}).get(merged)
    return (fieldtext.text(src.get("title")) if isinstance(src, dict) else "") or merged


def _merged_tail(cur: dict | None, merged: str | None, title: str,
                 settled: Callable[[dict], bool]) -> str:
    """`→ merged into <title>` for a row redirected off an alias source, with
    the canonical's stored status when approving the row would reopen it."""
    if merged is None or not isinstance(cur, dict):
        return ""
    tail = f" → merged into {title}"
    if settled(cur):
        tail += f" ({fieldtext.text(cur.get('status'))})"
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
             f"{_merged_tail(cur, merged, disp_title, _record_ids._thread_settled)}")
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
    due = fieldtext.text(row["due"]) if "due" in row else None
    cur = owed.get(mid)
    if isinstance(cur, dict):  # existing commitment (by id, or a colliding new title)
        # The STORED head, deadline included: `due` is applied on save and
        # then steers the ledger and every later scene prompt, so a model
        # that invents or overwrites one must not be able to do it in a row
        # whose only visible text is the beat. Here it is what the deadline
        # was; the label below is what it will be. It doubles as the
        # staleness token `apply_edits` re-checks at save time.
        before = conflicts.commitment_line(cur)
        stored_due = fieldtext.text(cur.get("due"))
        disp_title = fieldtext.text(cur.get("title")) or title or mid  # keep the stored title
        # What the record will read AFTER the save: the model's value where
        # it gave one, the stored value where it did not.
        disp_kind = kind or fieldtext.text(cur.get("kind")) or "promise"
        disp_status = status or fieldtext.text(cur.get("status")) or "open"
        disp_due = stored_due if due is None else due
    else:
        before, disp_title = "", title or mid
        disp_kind = kind or "promise"      # set_movement's own defaults, for
        disp_status = status or "open"     # a commitment being created here
        disp_due = due or ""
    label = f"{_merged_head(owed, merged, disp_title)} — {disp_kind}, {disp_status}"
    if disp_due:
        label += f", due {disp_due}"
    label += _merged_tail(cur, merged, disp_title, _record_ids._commitment_settled)
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

    A merge in the same window can do worse than close the target: fold it
    into a record another row of the batch already moves. The resolver's
    one-move-per-record guard ran against the old alias map, and `assign_ids`
    would canonicalize both rows onto one record and silently drop the
    second -- a beat that never reaches the review. So the accepted row is
    put back the same way when any other row's current target (assigned as
    `assign_ids` would, through the same map) is its canonical.

    A shallow copy; `parsed` is not mutated. A row without its original is
    not one `identity.rewritten` wrote, and is left alone."""
    out = dict(parsed)
    for section, kind in _contracts._IDENTITY_SECTIONS.items():
        rows = parsed.get(section)
        stored = threads if kind == "thread" else owed
        if isinstance(rows, list) and isinstance(stored, dict):
            out[section] = _recheck_section(rows, stored, kind, _record_ids._aliases(live, kind))
    return out


def _siblings(rows: list, i: int, target: str) -> list[int]:
    """The rows after `i` that `identity.rewritten` retargeted onto `target`
    because row `i` held their id: unexamined, and naming that same id."""
    return [j for j in range(i + 1, len(rows))
            if isinstance(rows[j], dict) and "identity_check" not in rows[j]
            and fieldtext.text(rows[j].get("id")) == target]


def _moved_elsewhere(rows: list, i: int, stored: dict, kind: str,
                     aliases: dict[str, str], canonical: str) -> bool:
    """Whether a row other than `i` (and its siblings) stages onto `canonical`.

    Asked of `_assign_section` itself, on the batch with row `i` and its
    siblings blanked, so every other row's target is the one `assign_ids` will
    give it -- explicit ids and honoured slugs alike, through the same map."""
    skip = {i, *_siblings(rows, i, fieldtext.text(rows[i].get("id")))}
    probe = [{**r, "beat": ""} if j in skip and isinstance(r, dict) else r
             for j, r in enumerate(rows)]
    allocate = _record_ids._new_thread_id if kind == "thread" else _record_ids._new_commitment_id
    out: dict[tuple[str, int], _record_ids.Assigned | None] = {}
    _record_ids._assign_section(probe, stored, "", allocate, out, aliases)
    return any(slot is not None and slot.id == canonical for slot in out.values())


def _recheck_section(rows: list, stored: dict, kind: str, aliases: dict[str, str]) -> list:
    rows = list(rows)
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not _accepted_existing(row.get("identity_check")):
            continue
        original = row.get(_contracts.AS_NEW_KEY)
        target = fieldtext.text(row.get("id"))
        if not isinstance(original, dict):
            continue
        canonical = _record_ids._redirect(target, stored, aliases)[0]
        cur = stored.get(canonical)
        if not (isinstance(cur, dict) and continuity_effective.is_live(kind, cur.get("status"))):
            reason = _contracts.CLOSED_SINCE
        elif _moved_elsewhere(rows, i, stored, kind, aliases, canonical):
            reason = _contracts.MOVED_SINCE
        else:
            continue
        rows[i] = {**original, "identity_check": {
            **row["identity_check"], "decision": "uncertain", "status": "downgraded",
            "reason": reason}}
        # `identity.rewritten` retargeted this row's siblings -- later rows
        # dropped only because the proposed row held their id -- onto the same
        # record, so they would stay a dropped second move. With the row back
        # on its own id they would instead stage onto the settled target and
        # reopen it (or collide with the row that moves its canonical), so
        # they are dropped here, as they were before any check. Nothing else
        # in the batch can name the target: an accepted ``existing`` is
        # downgraded when another row already moves it.
        for j in _siblings(rows, i, target):
            rows[j] = {**rows[j], "beat": ""}
    return rows


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


def _latest_beat(rec: dict) -> str:
    """The text of `rec`'s last appended beat, or "" -- a stored record's own
    `latest_beat`, as `plot.open_threads` projects it."""
    beats = rec.get("beats")
    last = beats[-1] if isinstance(beats, list) and beats else None
    return fieldtext.text(last.get("text")) if isinstance(last, dict) else ""


def _current_candidates(kind: str, ic: dict, stored: dict,
                        live: dict[str, str]) -> list[dict]:
    """`ic`'s candidates as the records they are at staging, not at the
    examination.

    Each ref is followed through the CURRENT alias map -- the one the
    alternatives are built against -- so a candidate merged away while the
    absorb ran is listed as the canonical its alternative writes; the client
    reads an alternative missing from this list as the as-new variant. Two
    that now share a canonical are one, keeping the first one's signals; one
    whose canonical is not stored is dropped. A closed or resolved one stays:
    the examination shows settled records on purpose (the reviewer is told
    the match is closed), so only its alternative is withheld. Title and
    status are the stored canonical's; the latest beat is too wherever the
    ref moved, and is otherwise the examined one (which a merged group's
    effective record supplies)."""
    out: list[dict] = []
    seen: set[str] = set()
    for cand in ic.get("candidates") or []:
        ref = cand.get("ref") if isinstance(cand, dict) else None
        if not isinstance(ref, str):
            continue
        canonical = live.get(ref, ref)
        prefix, _, rid = canonical.partition(":")
        cur = stored.get(rid) if prefix == kind else None
        if not isinstance(cur, dict) or canonical in seen:
            continue
        seen.add(canonical)
        moved = canonical != ref
        out.append({**cand, "ref": canonical, "title": fieldtext.text(cur.get("title")) or rid,
                    "status": fieldtext.text(cur.get("status")),
                    "latest_beat": (_latest_beat(cur) if moved
                                    else cand.get("latest_beat", _latest_beat(cur)))})
    return out


def _candidate_alternatives(sid: str, kind: str, original: dict, candidates: list[dict],
                            stored: dict, taken: set[tuple[str, str]]) -> list[dict]:
    """One staged row per candidate that is still a live, untaken record.

    `candidates` are `_current_candidates`: already followed through the
    current alias map, deduplicated, and stored. Each must also be live and
    moved by no edit in this batch (this row's own target included)."""
    build = _plot_edit if kind == "thread" else _commitment_edit
    target_kind = "plot" if kind == "thread" else "commitments"
    alts: list[dict] = []
    for cand in candidates:
        rid = cand["ref"].partition(":")[2]
        cur = stored[rid]
        if ((target_kind, rid) in taken
                or not continuity_effective.is_live(kind, cur.get("status"))):
            continue
        alts.append(build(sid, retarget.onto_existing(kind, original, rid), rid, stored))
    return alts


def _as_new_alternative(sid: str, kind: str, original: dict, stored: dict,
                        staged: dict[str, str]) -> dict:
    """The model's own row on a record of its own: what an accepted
    ``existing`` is one click from, if the match was wrong.

    Its explicit id is kept only when free and slug-shaped -- a model-written
    id such as ``plot/the-map`` would create a record the Ledger's routes
    cannot address. The id is reserved in `staged`, so two as-new variants
    never share one."""
    given, title = fieldtext.text(original.get("id")), fieldtext.text(original.get("title"))
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
    """Fill each examined row's `identity_check.alternatives` (spec §10.3),
    and bring its `candidates` up to the same state (`_current_candidates`),
    so every alternative onto a record names a listed candidate.

    A second pass, after every primary row is staged, so "not targeted
    elsewhere in the batch" and the as-new allocation see the whole batch.
    Alternatives are built by the same helpers as primary rows, so their
    labels, payloads and `before` tokens are what a primary row on that record
    would carry. Each copies its row's review block and carries no
    `identity_check`. Assigned only once every row's lists are built, so a
    failure part-way leaves every alternatives list at ``[]`` (and the
    candidates as examined)."""
    taken = {(e["target"]["kind"], e["target"]["id"]) for e in out
             if e.get("kind") in ("plot", "commitment")}
    found: dict[str, tuple[list[dict], list[dict]]] = {}
    for edit in out:
        ic, row = edit.get("identity_check"), rows_by_edit.get(edit["id"])
        if not isinstance(ic, dict) or row is None:
            continue
        kind = "thread" if edit["kind"] == "plot" else "commitment"
        stored, staged = (threads, staged_plot_titles) if kind == "thread" else (owed, staged_titles)
        original = row.get(_contracts.AS_NEW_KEY) if _accepted_existing(ic) else None
        candidates = _current_candidates(kind, ic, stored, live)
        alts = _candidate_alternatives(sid, kind, original if isinstance(original, dict) else row,
                                       candidates, stored, taken)
        if isinstance(original, dict):
            alts.append(_as_new_alternative(sid, kind, original, stored, staged))
        found[edit["id"]] = (candidates,
                             [{**alt, "review": dict(edit["review"])} for alt in alts])
    for edit in out:
        if edit["id"] in found:
            candidates, alts = found[edit["id"]]
            edit["identity_check"]["candidates"] = candidates
            edit["identity_check"]["alternatives"] = alts


def _continuity_records(cid: str) -> tuple[dict, dict | None]:
    """Read threads and commitments, retaining unreadable versus empty."""
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
    return threads, owed


def _continuity_edits(cid: str, sid: str, parsed: dict, out: list[dict], *,
                      stamp: staging.Stamp,
                      identity_failed: Callable[[BaseException], None]) -> dict:
    """Stage canonical continuity rows between the two identity-only barriers."""
    threads, owed = _continuity_records(cid)
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
    live = _record_ids._live_canon(cid)
    try:
        parsed = _recheck_accepted(parsed, threads, owed, live)
    except Exception as exc:  # noqa: BLE001 -- an identity-only pass; a defect stages the rows without it, never fails the absorb
        identity_failed(exc)
    staged_titles: dict[str, dict[str, str]] = {}
    assigned = _record_ids.assign_ids(threads, owed, parsed, live, staged_titles)
    rows_by_edit: dict[str, dict] = {}   # plot/commitment edit id -> its parsed row
    for i, e in enumerate(parsed.get("plot_movements", [])):
        slot = assigned[("plot_movements", i)]
        if slot is None:
            continue  # blank beat, no usable id or title, or a second edit to one thread
        edit = _plot_edit(sid, e, slot.id, threads, merged=slot.merged_from)
        out.append(stamp(edit, e))
        rows_by_edit[edit["id"]] = e

    for i, e in enumerate(parsed.get("commitment_movements", []) if owed is not None else []):
        slot = assigned[("commitment_movements", i)]
        if slot is None:
            continue  # blank beat, no usable id or title, or a second edit to one commitment
        # `or {}` only for the type checker: this loop runs when `owed` is a dict.
        edit = _commitment_edit(sid, e, slot.id, owed or {}, merged=slot.merged_from)
        out.append(stamp(edit, e))
        rows_by_edit[edit["id"]] = e

    # The identity check's alternatives, once the whole batch is staged.
    try:
        _attach_alternatives(sid, out, rows_by_edit, threads, owed or {}, live,
                             staged_titles["plot_movements"],
                             staged_titles["commitment_movements"])
    except Exception as exc:  # noqa: BLE001 -- an identity-only pass; a defect stages the rows without it, never fails the absorb
        identity_failed(exc)

    return parsed
