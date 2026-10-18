"""Applying approved StagedEdits back into the campaign copies.

One writer per ordinary edit kind in `writers`, each using the store module that
owns its record. This layer snapshots and seals the actual write into an outcome;
the positional batch journal coordinates replay and publication. Sheet edits keep
`audit/apply.py`'s conflict contract, and membership keeps its recovery barrier.

Each edit resolves to ONE outcome value rather than three side effects, because
that is the unit `PUT /chronicle`'s commit journal records and the unit the
reviewer is shown -- see `_apply_one` and #271.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .. import (
    changes,
    entities,
    group_refs,
    provenance,
    relationship_history,
    relationships,
)

# Aliased because `apply_edits` binds `journal` to the commit progress dict --
# the crash-resume ledger, which is a different thing from the change journal
# and predates it. Two `journal`s in one function would read as one.
from .. import journal as change_journal
from .. import undo as undo_store
from ..audit import apply as audit_apply
from ..campaigns import paths as campaigns_paths
from ..sheets import paths as sheets_paths
from . import conflicts, membership, writers

#: Kept as a name here because `absorb` re-exports it, but the list itself now
#: lives with the log it describes (`changes.BROWSABLE_KINDS`) -- `store.undo`
#: needs the same answer, and two literals would drift.
_BROWSABLE_KINDS = changes.BROWSABLE_KINDS


def _display(value) -> str:
    """A before/after side as text the panel can diff. Everything reaching here
    has already been written, so a non-string (a weather axis is a number) is
    rendered rather than dropped -- the journal is the record of what happened,
    and "" would claim the field was empty."""
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def _outside_drift(cid: str, e: dict, reading, landed: set[tuple]) -> dict | None:
    """The conflict somebody else's write introduced while this commit was down,
    or None.

    A resume replays the verdicts the interrupted attempt computed, so a row
    whose target one of its OWN earlier edits moved is not refused as a conflict
    with the batch's own work. That reasoning covers this commit's writes and
    nothing else: between the crash and the retry a direct entity route, or
    another device writing into the same synced store, can move a target this
    commit has not reached yet. Replaying the stale "no conflict" over that is
    the silent lost update `conflicts` exists to stop -- and the one a fresh save
    would have caught, so a resume must not be the weaker path.

    The target is therefore read again, and a value that is neither what the
    first attempt saw nor something this commit has since written is an outside
    change, judged fresh so the reviewer is shown the real `stored` and
    `merged`.

    `landed` holds (target, value) pairs, and BOTH have to match for a change to
    count as this commit's own. Matching on the value alone would let an outside
    write to a different record pass whenever it happened to store text one of
    these edits had already written -- and with "" and other common state values
    in play, that collision is ordinary rather than exotic. An edit whose target
    `conflicts.target_key` cannot name is never exempted at all: unrecognised
    means judged, not waved through.
    """
    if not isinstance(reading, str):
        return None   # nothing was read the first time: no drift to prove either way
    now = conflicts.current_value(cid, e)
    key = conflicts.target_key(e)
    if now is None or now == reading or (key is not None and (key, now) in landed):
        return None
    return conflicts.conflict_row(cid, e)


#: The edit kinds whose write creates a record, and so whose outcome says which.
_CREATING_KINDS = frozenset({"new_character", "new_location", "new_lore"})


def _apply_membership(cid: str, e: dict, sid: str | None, eid: str) -> dict:
    """Keep uncertain membership writes pending and identity failures conflicted."""
    try:
        return membership.apply(cid, e, e.get("_membership_neutral", set()), sid=sid, undo_store=undo_store)
    except OSError as exc:
        if e.get("_membership_attempted"):
            return {"state": "pending", "id": eid}
        return {"state": "failed", "id": eid, "kind": "error", "reason": str(exc)}
    except (entities.EntityNotFound, group_refs.GroupIdentityConflict, ValueError) as exc:
        return {"state": "failed", "id": eid, "kind": "conflict", "reason": str(exc)}


def _apply_sheet(cid: str, e: dict, sid: str | None, eid: str) -> dict:
    """Keep the sheet's error/baseline contract outside ordinary record handling."""
    if not eid:
        # rejected before apply_delta runs: a nameless mutation can never land
        return {"state": "failed", "id": "", "kind": "error",
                "reason": "sheet edit missing id"}
    if not sid:
        return {"state": "failed", "id": eid, "kind": "error",
                "reason": "sheet edits need a scene id"}
    try:
        audit_apply.apply_delta(cid, sid, e)
    except sheets_paths.SheetConflict as exc:
        return {"state": "failed", "id": eid, "kind": "conflict", "reason": str(exc)}
    except sheets_paths.SheetError as exc:
        return {"state": "failed", "id": eid, "kind": "error", "reason": str(exc)}
    # Journalled without a reversal, like every kind that carries none: the
    # history is "every change this campaign made", and a sheet edit leaving
    # no row would be the same silence `changes.json` kept about plot beats.
    # Its own baseline (`audit/baselines.py`) is what reverses it.
    target = e.get("target")
    target = target if isinstance(target, dict) else {}
    return {"state": "applied", "id": eid, "recorded": {},
            "journalled": {
                "kind": "sheet",
                "ref": {"kind": _display(target.get("kind")),
                        "id": _display(target.get("id"))},
                "field": _display(e.get("field")), "label": _display(e.get("label")),
                "before": _display(e.get("before")), "after": _display(e.get("after")),
                "undo": None, "why": undo_store.why("sheet")}}


def _describe_written(cid: str, e: dict, sid: str | None, written: writers.Written,
                      *, reversal: dict | None, prior, eid: str) -> dict:
    """Seal one actual write into the three logs the batch publishes together."""
    kind, target, after = e["kind"], written.target, written.after
    extra_fields, pair = written.extra_fields, written.relationship
    if written.reallocated:
        reversal = None
    # Inside the handler with the write it describes: `target` comes from a
    # client-supplied PUT body, so a malformed one must report like any
    # other bad edit rather than escape and 500 a commit that has already
    # recorded the chronicle entry.
    recorded: dict[str, list[dict]] = {}
    if sid and kind in _BROWSABLE_KINDS:
        ref = f"{target['kind']}/{target['id']}"
        # `replaced_value`, not the staged `before`: on a row the reviewer
        # answered over a conflict, the text this write actually replaced is
        # the one they were shown. Logging the staged `before` instead would
        # render everything that had landed in between as part of what this
        # edit added.
        recorded[ref] = [{"field": e.get("field", ""), "label": e.get("label", ""),
                          "before": conflicts.replaced_value(e), "after": after},
                         *extra_fields]
    # The journal row for this edit, whatever its kind: the gap `changes`
    # left is that it covers only the browsable kinds, and a plot beat or a
    # feeling is exactly the continuity line somebody wants back (#31). The
    # reversal is completed here, by reading the record once more now the
    # write has landed -- that reading is what a later undo is held to.
    journalled = {
        "kind": kind, "ref": {"kind": _display(target.get("kind")),
                              "id": _display(target.get("id"))},
        "field": e.get("field", "") if isinstance(e.get("field"), str) else "",
        "label": e.get("label", "") if isinstance(e.get("label"), str) else "",
        "before": _display(conflicts.replaced_value(e)), "after": _display(after),
        "undo": undo_store.seal(cid, reversal, prior) if reversal is not None else None}
    journalled["why"] = "" if journalled["undo"] else undo_store.why(kind)
    # The timeline row for the same write. Both ends are the RECORD: `after`
    # was read back after the write above, and `before` is `prior`, the
    # reading `undo_store.snapshot` took at the last moment the replaced
    # value still existed -- per edit, and inside this function.
    #
    # Not the staged `before`, and the conflict gate is not a substitute for
    # this even though `relationship` and `bond` are both in
    # `conflicts._REASONS`. That gate is ONE PASS OVER THE WHOLE BATCH
    # BEFORE THE FIRST WRITE, so it says nothing about a batch that moves
    # one pair twice: nothing dedupes `relationship_deltas` by pair the way
    # `plot_movements` are deduped by id, both rows are staged with the same
    # `before`, both pass the gate against the untouched record, and the
    # second then writes over the first. The staged pair would record
    # `original -> first` and `original -> second`, and the arc -- the whole
    # point of this ledger -- would have a break in it that nothing later
    # can find.
    #
    # `prior` is trustworthy only when the probe produced a reversal: a
    # probe that could not read its target yields `(None, None)`, which is
    # indistinguishable from "there was no record". The staged text is the
    # fallback there, being what the journal shows.
    history = relationship_history.row(
        pair["kind"], pair["a"], pair["b"], label=journalled["label"],
        before=(relationships.render_standing(pair["kind"], prior)
                if reversal is not None else journalled["before"]),
        after=pair["after"]) if pair else None
    return {"state": "applied", "id": eid, "recorded": recorded,
            "journalled": journalled, "relationship": history}


def _apply_one(cid: str, croot, e: dict, sid: str | None,
               verdict: dict | None = None) -> dict:
    """Apply ONE approved StagedEdit and say what became of it.

    The outcome is the unit the commit journals and the reviewer is shown, so it
    is a value rather than three side effects:

    - ``{"state": "applied", "id", "recorded": {ref: [rows]}, "journalled": row,
      "relationship": row | None}`` -- it landed; `recorded` is its write-back
      delta when the kind is browsable, `journalled` is its change-journal row,
      which every kind gets and which carries the reversal when the kind has one
      (#31), and `relationship` is its relationship-timeline row for the two
      kinds that move `relationships.json` and None for the rest (#63).
    - ``{"state": "skipped"}`` -- nothing was written and nothing was lost: a
      re-guard rejecting a forged row, a blank reply that must not erase a good
      record, a weather span with no usable date.
    - ``{"state": "failed", "id", "kind", "reason"}`` -- the reviewer approved
      it and it did not land. Every kind reports; #271's silence was that only
      sheets and dossiers did. A non-None `verdict` (#111) lands here too: the
      row's target moved since it was staged and nobody has answered for it.

    Ordinary write failures are outcomes: the caller has already recorded the
    chronicle entry, so one broken edit must not sink the rest of the commit.
    Membership and sheets retain their own narrower exception contracts.
    """
    if not isinstance(e, dict):
        return {"state": "skipped"}   # a malformed batch item: not a change that was lost
    if verdict is not None:
        # #111: this row's target moved since it was staged and the reviewer has
        # not answered for it. Reported, never silently dropped -- they approved
        # it and would otherwise read the save as a success.
        return {"state": "failed", "id": e.get("id", ""), "kind": "conflict",
                "reason": verdict["reason"]}
    eid = e.get("id", "")
    eid = eid if isinstance(eid, str) else ""
    if e.get("kind") == "membership":
        return _apply_membership(cid, e, sid, eid)
    if e.get("kind") == "sheet":
        return _apply_sheet(cid, e, sid, eid)
    return _apply_record(cid, croot, e, sid, eid)


def _apply_record(cid: str, croot: Path, e: dict, sid: str | None, eid: str) -> dict:
    """Shape, probe, write and describe an ordinary edit under one failure barrier."""
    # Shape first, and a bad one is a SKIP rather than a failure. #271 made every
    # write that fails report, but a row this garbled was never a coherent edit
    # to begin with -- it cannot come from the review panel, only from a forged
    # or corrupted body, and there is nothing about it to tell a reviewer. Doing
    # it here rather than letting the handler below raise is what keeps the two
    # apart: past this point an exception really is a write that failed.
    if (not isinstance(e.get("kind"), str) or not isinstance(e.get("target"), dict)
            or (e.get("payload") is not None and not isinstance(e.get("payload"), dict))):
        return {"state": "skipped"}
    # BEFORE the write, because this is the last moment the value it replaces
    # still exists (#31). Never raises: an unreadable target costs the entry its
    # Undo button, and must not cost the reviewer their approved edit.
    reversal, prior = undo_store.snapshot(cid, e)
    try:
        written = writers.write(writers.Context(cid, croot, sid), e)
        if isinstance(written, dict):
            return written
        out = _describe_written(cid, e, sid, written, reversal=reversal, prior=prior, eid=eid)
    except entities.EntityNotFound:
        # Named apart from the generic handler because its message would
        # otherwise be the bare ref: this is the commonest way an approved edit
        # fails, and the reviewer needs to read it. Same answer the dossier
        # branch has given for a vanished character since #235.
        return {"state": "failed", "id": eid, "kind": "error",
                "reason": "that record no longer exists in this campaign"}
    except Exception as exc:  # noqa: BLE001 — full disk, permissions, a deleted target, ...
        # #271: this used to be a bare `continue`. Every kind but sheet and
        # dossier failed here in silence, and the save still returned 200 --
        # so an approved change vanished with nothing on screen to say so.
        return {"state": "failed", "id": eid, "kind": "error",
                "reason": f"could not apply this change: {exc}"}
    if written.reallocated:
        # The record this write actually went to, reported only when it is not
        # the staged one: everything `apply_edits` derives after the write --
        # the citation, the resume reading -- has to name the record that now
        # holds the beat, not the one it moved off. Only the two reallocations
        # set it; a `new_*` kind's created id is reported as `created` below.
        out["target"] = written.target
    if e["kind"] in _CREATING_KINDS:
        # The staged `target.id` of a creation is "" -- the id exists only now.
        # Named here so the provenance step can key the citation on the record
        # that was made rather than on nothing.
        out["created"] = {"kind": written.target["kind"], "id": written.target["id"]}
    return out


def _as_written(e: dict, outcome: dict) -> dict:
    """The staged edit `e` with its target replaced by the one `_apply_one`
    reports having written instead. An outcome that reports none -- the write
    went where it was staged, or the journal predates the field -- leaves the
    staged edit as it was."""
    target = outcome.get("target")
    if not isinstance(e, dict) or not isinstance(target, dict):
        return e
    return {**e, "target": target}


#: What a resumed commit says about a step it journalled and never confirmed.
#: Repeating it could duplicate an append, so the reviewer is told instead.
UNCONFIRMED = ("an earlier save started this and did not say whether it landed — "
               "check the record before applying it again")


def _restore_membership(e: dict, slot: str, journal: dict, neutral: dict) -> None:
    """Restore the reserved target and neutral consent before this slot runs."""
    if isinstance(e, dict) and e.get("kind") == "membership":
        saved = journal.get("membership_targets", {}).get(slot)
        if isinstance(saved, dict) and "_membership_target" in e:
            e["_membership_target"] = saved
            e["target"] = {"kind": saved["kind"], "id": saved["id"]}
        target = e.get("target")
        e["_membership_neutral"] = (neutral.get((str(target.get("kind")), str(target.get("id"))), set())
                                    if isinstance(target, dict) else set())


def _retain_copy(e: dict, prior: dict, journal: dict) -> None:
    """Propagate a landed first-copy receipt to the matching reservations."""
    copy_receipt = prior.pop("membership_copy", None)
    if copy_receipt:
        for saved in journal.get("membership_targets", {}).values():
            if saved.get("kind") == e["target"]["kind"] and saved.get("id") == e["target"]["id"] and saved.get("source") == copy_receipt["source"]:
                saved["copy_receipt"] = {**copy_receipt, "token": saved.get("token")}


def _retain_outcome(cid: str, e: dict, prior: dict, sid: str | None, journal: dict) -> None:
    """Move a fresh outcome's logs into the durable batch, then read its target."""
    recorded, journalled, timeline = (journal["recorded"], journal["journalled"],
                                      journal["relationships"])
    _retain_copy(e, prior, journal)
    for ref, rows in prior.pop("recorded", {}).items():
        recorded.setdefault(ref, []).extend(rows)
    row = prior.pop("journalled", None)
    if row:
        journalled.append({"scene": sid or "", "source": "absorb", **row})
    moved = prior.pop("relationship", None)
    if moved:
        timeline.append({**moved, "scene": sid or "", "source": "absorb"})
    if prior.get("state") == "applied":
        # Journalled with the outcome: what the target reads now this
        # edit has landed is how a LATER slot, resuming after a crash,
        # recognises the movement as this commit's own work.
        prior["read"] = conflicts.current_value(cid, _as_written(e, prior))


def _account_applied(e: dict, prior: dict, sid: str | None, *,
                     cited: dict, landed: set[tuple]) -> None:
    """Cite the actual written record and remember its reading for later slots."""
    # The edit as it landed: a reallocated plot or commitment row wrote
    # to an id other than the one it was staged at, and its citation and
    # its reading belong to that record -- keyed off the staged target,
    # the quote would overwrite the citation of the thread it moved OFF,
    # which is somebody else's and does not contain this beat.
    written = _as_written(e, prior)
    # Every kind, not just the browsable ones `recorded` covers: a fact
    # and a plot beat are exactly the continuity lines this exists to
    # make checkable, and neither is browsable.
    if sid:
        # A created record's staged target id is "", so key on the id the
        # write handed back; every other kind cites what it wrote.
        created = prior.get("created")
        cite_edit = ({**written, "target": created} if isinstance(created, dict)
                     else written)
        pkey = provenance.key(cite_edit)
        prow = provenance.row(written, sid) if pkey else None
        if pkey and prow:
            cited[pkey] = prow
    read, key = prior.get("read"), conflicts.target_key(written)
    # Recomputed from the edit rather than journalled beside the reading:
    # the token's fingerprint has already refused any retry whose body
    # differs, so this list is the one the first attempt worked from.
    if isinstance(read, str) and key is not None:
        landed.add((key, read))


def _publish_logs(cid: str, sid: str | None, journal: dict,
                  checkpoint: Callable[[], None] | None) -> dict | None:
    """Fence and publish the batch logs once, preserving uncertain intent."""
    recorded, cited, journalled, timeline = (journal["recorded"], journal["cited"],
                                              journal["journalled"], journal["relationships"])
    # changes.record is an upsert of "the latest write-back per record", so a
    # resumed commit must not replay it: between the crash and the retry another
    # scene may have installed a genuinely newer entry for the same record, and
    # this one would overwrite it while leaving the record itself at the newer
    # value. Journalling the INTENT (rather than the completion) costs no extra
    # write -- the checkpoint below was already due for the last edit's outcome
    # -- and errs the safe way: a resume that cannot tell reports a possibly
    # stale panel instead of rewriting it.
    #
    # The journal append rides in the same guarded block rather than in one of
    # its own. It is append-only, so replaying it would DUPLICATE history rather
    # than staling it -- the opposite failure with the same fix, and one flag
    # settles both. A resume that cannot tell whether the block ran reports it
    # and writes neither.
    prior_changes = journal.get("changes")
    logs = bool(recorded or cited or journalled or timeline)
    if logs and prior_changes is None:
        journal["changes"] = "pending"
    if checkpoint:
        checkpoint()      # the last edit's outcome, so a crash before `record` resumes clean
    membership.fence_publication(cid, journal)
    recorded = {ref: rows for ref, rows in recorded.items() if rows}
    logs = bool(recorded or cited or journalled or timeline)
    if logs:
        if isinstance(prior_changes, dict):
            # An earlier attempt tried and got a definite answer. changes.record
            # publishes by atomic rename, so its exception PROVED nothing landed
            # -- a fact worth keeping over the vaguer "unconfirmed", even though
            # the response is the same refusal to replay the stale upsert.
            return {"id": "changes", "kind": "error",
                    "reason": str(prior_changes.get("reason", ""))}
        if prior_changes == "pending":
            return {"id": "changes", "kind": "error", "reason": UNCONFIRMED}
        try:
            changes.record(cid, sid, recorded)
            # After the diffs, and inside their try: both are display logs
            # written once the edits have already landed, both are rolling
            # upserts, and a failure of either is the same class of loss —
            # the record is right and the panel explaining it is stale.
            provenance.record(cid, cited)
            # After the two upserts, and the first of the two append-only
            # logs: it goes last of the three that existed before #63 so a
            # failure here cannot leave the panel showing a delta the
            # history has no row for.
            change_journal.append(cid, journalled)
            # Last, after the history it is a projection of: both are
            # append-only, and a failure here must not be able to leave a
            # timeline row standing for a change the journal has no entry
            # for. The reverse order is recoverable -- the journal row is
            # the fuller record -- and this one is not.
            relationship_history.append(cid, timeline)
        except Exception as exc:  # noqa: BLE001 — the edits landed; the delta log did not
            reason = f"the changes panel could not be updated: {exc}"
            # Settled, not left pending: if this commit also fails to record
            # its result, the retry should report what actually happened
            # rather than the ambiguity `pending` stands for.
            journal["changes"] = {"state": "failed", "reason": reason}
            if checkpoint:
                checkpoint()
            return {"id": "changes", "kind": "error", "reason": reason}
    return None


def _batch_survey(cid: str, edits: list[dict], journal: dict) -> tuple[list, list, bool]:
    """Reuse a complete original survey, or take one before any batch writes."""
    # One pass over the whole batch before the first write (#111): applying one
    # edit can move the record the next was staged against, so a check
    # interleaved with the writes would report the batch as contradicting
    # itself.
    #
    # Journalled for the same reason it is computed up front. A resume's earlier
    # writes are this commit's OWN, already landed, so recomputing would judge a
    # not-yet-run edit against a store its siblings moved -- and two rows may
    # legitimately target one record, which `batch_verdicts` supports on purpose.
    # The second row would then be refused as a conflict although both passed
    # together before the first write, and an uninterrupted attempt would have
    # applied both. Keeping the original verdicts makes the resume decide what
    # the interrupted attempt decided.
    #
    # The readings beside them are what keeps that from also replaying a stale
    # "no conflict" over an OUTSIDE write -- see `_outside_drift`.
    verdicts, readings = journal.get("verdicts"), journal.get("readings")
    if (isinstance(verdicts, list) and len(verdicts) == len(edits)
            and isinstance(readings, list) and len(readings) == len(edits)):
        return verdicts, readings, True
    verdicts, readings = conflicts.batch_survey(cid, edits)
    # Durable before the first write: the checkpoint below runs ahead of
    # edit 0, and a crash before that leaves nothing applied to be judged
    # against, so a recomputation there is judging the same store anyway.
    journal["verdicts"], journal["readings"] = verdicts, readings
    return verdicts, readings, False


def apply_edits(cid: str, edits: list[dict], sid: str | None = None,
                progress: dict | None = None,
                checkpoint: Callable[[], None] | None = None,
                ) -> tuple[list[str], list[dict]]:
    """Apply each approved StagedEdit to the campaign copies. Best-effort: one broken
    edit never sinks the rest. Returns (applied ids, failures) -- every edit that the
    reviewer approved and that did not land is reported as
    {"id", "reason", "kind": "conflict"|"error"}, so a save can no longer return 200
    while quietly dropping a change (#271). Every kind whose write REPLACES a stored
    value is held to its staged `before` by `conflicts` (#111) -- an edit whose target
    moved is reported rather than applied, unless the row carries the reviewer's
    `resolve`. Callers wanting the reviewer to choose before anything lands run
    `conflicts.check_conflicts` first. When `sid` is given, the before/after of
    each applied *browsable* edit -- a character record, or the body of any of
    the five entity kinds -- is captured into changes.json (the latest
    write-back delta per record); sheet edits are never browsable and never
    land there -- the sheet itself is the record.

    Every applied edit of every kind also appends one row to the append-only
    change journal (`store/journal.py`), carrying the reversal `store/undo.py`
    snapshotted before the write. That is the history changes.json cannot keep:
    it is a rolling upsert, so the second scene to touch a record erases the
    first scene's delta and any chance of putting it back (#31).

    `progress` is the commit journal (#271). Each edit's outcome is written into it
    by position -- positions, not ids, because the commit token's fingerprint has
    already refused any retry whose body differs, and ids are client-supplied and
    need not be unique. `checkpoint` is called to make the journal durable just
    before each edit is attempted, so a crash leaves that edit marked attempted and
    a resume reports it rather than repeating a write that may already have landed.
    """
    croot = campaigns_paths.campaign_root(cid)
    journal = progress if progress is not None else {}
    outcomes: dict = journal.setdefault("edits", {})
    # Carried in the journal too: changes.record only runs once the whole list is
    # through, so a commit that died mid-list would otherwise lose the deltas of
    # everything it had already applied.
    journal.setdefault("recorded", {})
    # The citations behind the rows that land. Journalled beside `recorded` and
    # published in the same guarded block below, because it is the same kind of
    # rolling upsert with the same replay hazard.
    cited: dict = journal.setdefault("cited", {})
    # The append-only change journal's rows (#31), carried here for the same
    # reason `recorded` is: they are published in one append once the whole list
    # is through, so a commit that died mid-list must not lose the history of
    # what it had already written.
    #
    # It costs what `recorded` already costs, roughly doubled: a row carries the
    # record's prior value and its post-write reading on top of the display
    # before/after, and `checkpoint` republishes this whole dict once per edit --
    # so the bytes a commit writes here grow with the square of its edit count.
    # Accepted rather than optimised: the alternative to carrying the snapshot is
    # not carrying it, and it cannot be recomputed after the write it describes.
    journal.setdefault("journalled", [])
    # And the relationship timeline's (#63), carried here for the same reason:
    # it is append-only, so a resumed commit replaying it would duplicate the
    # arc rather than stale it -- the same failure `journalled` guards against,
    # settled by the same flag below.
    journal.setdefault("relationships", [])
    applied: list[str] = []
    failures: list[dict] = []
    verdicts, readings, replaying = _batch_survey(cid, edits, journal)
    landed: set[tuple] = set()
    neutral = membership.neutral_actors(cid, edits)
    for i in membership.apply_order(cid, edits):
        e = edits[i]
        slot = str(i)
        _restore_membership(e, slot, journal, neutral)
        prior = outcomes.get(slot)
        if not isinstance(prior, dict):
            verdict = verdicts[i]
            if replaying and verdict is None:
                verdict = _outside_drift(cid, e, readings[i], landed)
            outcomes[slot] = {"state": "pending",
                              "id": e.get("id", "") if isinstance(e, dict) else ""}
            if checkpoint:
                checkpoint()
            prior = outcomes[slot] = _apply_one(cid, croot, e, sid, verdict)
            _retain_outcome(cid, e, prior, sid, journal)
        if prior.get("state") == "applied":
            applied.append(prior.get("id", ""))
            _account_applied(e, prior, sid, cited=cited, landed=landed)
        elif prior.get("state") == "pending":
            failures.append({"id": prior.get("id", ""), "kind": "error",
                             "reason": UNCONFIRMED})
        elif prior.get("state") == "failed":
            failures.append({"id": prior.get("id", ""),
                             "kind": prior.get("kind", "error"),
                             "reason": prior.get("reason", "")})
    publication_failure = _publish_logs(cid, sid, journal, checkpoint)
    if publication_failure is not None:
        failures.append(publication_failure)
    return applied, failures
