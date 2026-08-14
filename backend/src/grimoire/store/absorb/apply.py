"""Applying approved StagedEdits back into the campaign copies.

One branch per edit kind, each writing through the store module that owns that
record. Sheet edits are handed to `audit/apply.py`, which owns the sheet
conflict contract; weather rows go to `weather._apply_weather`.

Each edit resolves to ONE outcome value rather than three side effects, because
that is the unit `PUT /chronicle`'s commit journal records and the unit the
reviewer is shown -- see `_apply_one` and #271.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import (cards, changes, characters, commitments, dossiers, entities, facts,
                groupstate, overlay, playstate, plot, provenance, relationships,
                undo as undo_store, voice_drift)
# Aliased because `apply_edits` binds `journal` to the commit progress dict --
# the crash-resume ledger, which is a different thing from the change journal
# and predates it. Two `journal`s in one function would read as one.
from .. import journal as change_journal
from ..appearances import (paths as appearances_paths,
                           transitions as appearances_transitions,
                           versions as appearances_versions)
from ..audit import apply as audit_apply
from ..campaigns import paths as campaigns_paths
from ..scenes import moment as scenes_moment, read as scenes_read
from ..sheets import paths as sheets_paths
from . import conflicts, materializer, weather

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


def _apply_one(cid: str, croot, e: dict, sid: str | None,
               verdict: dict | None = None) -> dict:
    """Apply ONE approved StagedEdit and say what became of it.

    The outcome is the unit the commit journals and the reviewer is shown, so it
    is a value rather than three side effects:

    - ``{"state": "applied", "id", "recorded": {ref: [rows]}, "journalled": row}``
      -- it landed; `recorded` is its write-back delta when the kind is
      browsable, and `journalled` is its change-journal row, which every kind
      gets and which carries the reversal when the kind has one (#31).
    - ``{"state": "skipped"}`` -- nothing was written and nothing was lost: a
      re-guard rejecting a forged row, a blank reply that must not erase a good
      record, a weather span with no usable date.
    - ``{"state": "failed", "id", "kind", "reason"}`` -- the reviewer approved
      it and it did not land. Every kind reports; #271's silence was that only
      sheets and dossiers did. A non-None `verdict` (#111) lands here too: the
      row's target moved since it was staged and nobody has answered for it.

    Never raises: the caller has already recorded the chronicle entry, so one
    broken edit must not sink the rest of the commit.
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
    if e.get("kind") == "sheet":
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
        kind, target, after = e["kind"], e["target"], e.get("after", "")
        extra_fields: list[dict] = []
        if kind == "weather":
            if not weather._apply_weather(cid, e, after):
                return {"state": "skipped"}   # skipped, not applied: nothing was written
        elif kind == "character_state":
            playstate.write_state(croot, target["id"], after)
        elif kind == "group_state":
            groupstate.write_state(croot, target["id"], after)
        elif kind == "dossier":
            # This row reaches us from a client-supplied PUT body, and
            # dossiers.write() creates its parent dir -- so the target has to
            # name a character that actually exists, or a forged row conjures
            # a dossier-only phantom under characters/.
            if not after.strip():
                return {"state": "skipped"}   # a blank reply must not erase a good dossier
            if target.get("kind") != "characters":
                return {"state": "skipped"}
            # Staging the dossier (#235) moved the write from absorb time to
            # save time, so the write order is now the SAVE order and can
            # invert the absorb order: two reviews open on the same NPC, the
            # newer saved first, and this one would overwrite it with
            # earlier-scene state. The staged `before` dates the proposal --
            # if it no longer matches, a newer dossier already landed and
            # this one is stale. (Replaying one save twice lands here too.)
            try:
                characters.read_character(
                    overlay.char_root(cid, target["id"]), target["id"])
            except characters.CharacterNotFound:
                return {"state": "failed", "id": eid, "kind": "error",
                        "reason": "that character no longer exists in this campaign"}
            if dossiers.read(croot, target["id"]) != e.get("before", ""):
                return {"state": "failed", "id": eid, "kind": "conflict",
                        "reason": "this dossier changed since the scene was absorbed"}
            dossiers.write(croot, target["id"], after)
        elif kind == "voice_drift":
            # The one kind whose blank `after` is the POINT: it clears the flag
            # on a character who came back into voice. So there is no "a blank
            # must not erase" guard here, unlike the dossier above -- an
            # approved clear that silently did nothing would leave the next
            # scene still being corrected for a scene that went fine.
            if target.get("kind") != "characters":
                return {"state": "skipped"}   # a forged row must not conjure a phantom
            payload = e.get("payload") or {}
            # The row's INTENT comes from the staged `op`, never from whether
            # `after` happens to be blank. The reviewer can edit the note, and a
            # raise edited down to empty text would otherwise reclassify itself
            # as a clear and unlink the standing corrective -- a deletion nobody
            # asked for. A raise whose text was blanked is unusable (the note IS
            # the corrective), so it is reported.
            op = payload.get("op") or ("clear" if not after.strip() else "raise")
            if op == "raise" and not after.strip():
                return {"state": "failed", "id": eid, "kind": "error",
                        "reason": "a voice-drift note cannot be blank — uncheck the row "
                                  "to skip it, or leave the corrective in place"}
            if len(after) > voice_drift.MAX_NOTE:
                return {"state": "failed", "id": eid, "kind": "error",
                        "reason": f"a voice-drift note cannot be longer than "
                                  f"{voice_drift.MAX_NOTE} characters — it is put in front "
                                  f"of every following turn"}
            # The guards below key on what this row WRITES, not on how it is
            # labelled. `op` catches a raise whose text the reviewer emptied
            # (above); it must not also decide whether the write is checked,
            # because a reviewer can type INTO a clear row -- leaving
            # `op == "clear"` on a row that now stores a flag, which would slip
            # past both checks and write one unverified.
            #
            # Nonblank text is a raise whatever the row calls itself: it ADDS
            # text to future prompts, so it needs a real target and a current
            # anchor. A blank write only removes text, needs neither, and
            # refusing it would block exactly the cleanup these checks argue for.
            if after.strip():
                # A row naming a character that does not exist must not conjure
                # one. `voice_drift.write` creates its parent dir, and every
                # remaining guard passes for an invented id (an absent flag
                # reads as "", matching a forged `before` of ""), so without
                # this a PUT body could litter characters/ with flag-only
                # phantoms.
                try:
                    characters.read_character(
                        overlay.char_root(cid, target["id"]), target["id"])
                except characters.CharacterNotFound:
                    return {"state": "failed", "id": eid, "kind": "error",
                            "reason": "that character no longer exists in this campaign"}
                # A raise is judged against a specific anchor, and the anchor is
                # editable while the review sits open. Writing a note reasoned
                # from the old reference would inject it alongside the new one
                # on the very next turn -- and since an anchor removed and later
                # restored reactivates whatever flag it left behind, the stale
                # note can come back long after the standard it cites is gone.
                #
                # Provenance is REQUIRED, not optional. A row is client-supplied,
                # and an absent fingerprint is stored as "" -- which
                # `_voice_notes` reads as "predates the field" and therefore
                # always-valid, so the note would go on being injected past
                # every later anchor change. A flag written NOW must not be able
                # to masquerade as legacy data.
                judged = payload.get("anchor")
                record = overlay.voice_anchor_record(cid, target["id"])
                if not judged:
                    return {"state": "failed", "id": eid, "kind": "error",
                            "reason": "this voice-drift finding does not record which "
                                      "anchor it was judged against"}
                if not voice_drift.fingerprint_matches(
                        judged, record["text"], record["id"]):
                    return {"state": "failed", "id": eid, "kind": "conflict",
                            "reason": "the voice anchor changed since the scene was "
                                      "absorbed — re-absorb to judge against it"}
            # Same conflict discipline as the dossier: the staged `before` dates
            # the proposal, so a mismatch means a newer verdict already landed
            # and this one is stale.
            #
            # The PROVENANCE is part of that comparison, not just the note. A
            # provenance-only refresh leaves the text identical, so an older
            # clear staged against the previous anchor would still match
            # `before` and delete a flag another review just revalidated.
            # Compared only when the row recorded what it expected.
            #
            # Both compare-and-swaps read the SAME snapshot: taken separately
            # they could straddle a concurrent save and pass a pairing of note
            # and provenance that never existed on disk (voice_drift.read_record).
            current = voice_drift.read_record(croot, target["id"])
            if current["note"] != e.get("before", ""):
                return {"state": "failed", "id": eid, "kind": "conflict",
                        "reason": "this voice-drift finding changed since the scene "
                                  "was absorbed"}
            expected_fp = payload.get("before_anchor")
            if expected_fp is not None and current["anchor"] != expected_fp:
                return {"state": "failed", "id": eid, "kind": "conflict",
                        "reason": "this voice-drift finding was re-confirmed against a "
                                  "different anchor since the scene was absorbed"}
            # The judged fingerprint rides into the file so the corrective can be
            # suppressed if the anchor moves after this commit -- the guard above
            # only covers up to it.
            voice_drift.write(croot, target["id"], after, payload.get("anchor") or "")
        elif kind == "lore":
            overlay.update_entity(cid, target["kind"], target["id"], body=after)
        elif kind == "authored":
            if e["field"] not in materializer._CARD_FIELDS:
                return {"state": "skipped"}  # re-guard: PUT edits are client-supplied
            vid = appearances_versions.locked_version(cid, "characters", target["id"])
            aroot = appearances_paths.locked_actor_root(cid)   # locked -> materialized
            card = characters.read_card(aroot, target["id"], vid)
            card["data"][e["field"]] = after
            characters.update_version(aroot, target["id"], vid, card)
        elif kind == "relationship":
            p = e["payload"]
            relationships.set_feeling(cid, p["from"], p["to"], p["trust"], p["affection"],
                                      p["tension"], p.get("note", ""))
        elif kind == "bond":
            p = e["payload"]
            relationships.set_bond(cid, p["a"], p["b"], p["type"])
        elif kind == "plot":
            p = e["payload"]
            # An absorb never renames an existing thread: `materialize` stages
            # the STORED title for one that already exists (`cur.get("title") or
            # title or pid`), so the staged title carries no intent to rename and
            # writing it back is a no-op -- except when somebody renamed the
            # thread between staging and saving, where `set_movement` overwrites
            # any non-blank title and silently reverts them. A blank title leaves
            # the stored one alone and still falls back to the id for a brand-new
            # thread, so the rename is not a conflict to resolve; it stands.
            title = "" if plot.get(cid, p["id"]) else p["title"]
            # `sid` in preference to the staged `payload.scene`, which
            # `materialize` sets to the very same scene. They differ in one case:
            # the scene was RENAMED between a crashed commit and its retry. The
            # ledger follows a rename (`commits.repoint_scenes`) so the retry is
            # accepted, but the body cannot change -- the fingerprint refuses any
            # retry whose body differs -- so the payload still names the old id.
            # Writing that would stamp the beat and `last_scene` with a scene
            # that no longer exists, and `plot.repoint_scenes` has already run,
            # so nothing would come back for it. The payload stays the fallback
            # for a caller that passes no `sid`.
            plot.set_movement(cid, p["id"], title, p["status"], after, sid or p["scene"])
        elif kind == "commitment":
            p = e["payload"]
            # No staleness check here: `commitment` is registered in
            # `conflicts._REASONS`, so the one-pass gate in `apply_edits` already
            # refused this row if the record moved since it was staged. It had a
            # bespoke check inline before #111 landed; keeping that would have
            # meant two definitions of "has this commitment moved" drifting
            # apart.
            #
            # Blank title for the same reason as the plot branch above, and
            # `.get` for the three fields materialize added last: a row that
            # round-tripped through the reviewer's PUT body may predate them, and
            # `set_movement` reads a blank as "keep what is stored" rather than
            # as a value -- so the beat still lands instead of the whole edit
            # being dropped.
            #
            # A title is suppressed only when there is a READABLE one to
            # preserve. A truthy non-dict record -- a hand-edited `[1]` -- is not
            # one: `materialize` skips it and stages the model's title as a new
            # commitment, `set_movement` then replaces the record wholesale, and
            # blanking on mere truthiness would leave a row the reviewer approved
            # as "The debt" stored under its id.
            #
            # The read sits inside the handler on purpose: `commitments.json` can
            # be valid when the review is staged and unparseable by the time it
            # is saved (a hand edit, a sync). Both conflict passes call that
            # unjudgeable, so the read here is where it surfaces -- and since
            # #271 the generic handler below reports it, which is why this branch
            # no longer carries a bespoke one of its own.
            # The TARGET id, not the payload's. They are the same record for
            # anything `materialize` built, but an edit reaches here from a
            # client-supplied PUT body typed as an unrestricted dict -- and
            # everything that judged this row read `target`: `conflicts`
            # surveyed it, `target_key` keyed the journal by it, and the
            # reviewer's basis describes it. A payload naming a different record
            # would be written without any of that having looked at it, so the
            # write goes where the checks went.
            mid = target["id"]
            # A row STAGED AS NEW (`before` empty) whose id is now taken is not
            # the same commitment: two reviews open at once can both propose one
            # whose title slugs alike -- `Pay Mara` and `Pay, Mara` -- and the
            # second conflicts once the first saves. If its reviewer answers
            # Replace, writing to the taken id appends their beat, kind, status
            # and deadline onto somebody else's commitment: two unrelated
            # promises merged into one record, under whichever title landed
            # first. So the id is reallocated by the SAME predicate materialize
            # allocates with -- a collision is honoured only when the stored
            # record is unresolved and carries the same title, which is the case
            # where they really are one commitment.
            #
            # This is the one place the write moves off `target`, and it moves
            # only to an id nothing holds: the target check exists to stop an
            # existing record being overwritten unseen, and creating a new one
            # overwrites nothing.
            if not str(e.get("before", "")).strip():
                mid = materializer._new_commitment_id(
                    commitments.read(cid), {}, mid, p.get("title", ""))
                if mid != target["id"]:
                    # The write moved off the record the reversal was snapshotted
                    # from, so that snapshot describes a commitment this edit
                    # never touched. Dropped rather than re-taken: the new id is
                    # unheld by construction, so there is nothing to put back,
                    # and a reversal here would be a deletion -- the same thing
                    # the `new_*` kinds are declined for.
                    reversal = None
                    target = {**target, "id": mid}
            cur = commitments.get(cid, mid)
            stored_title = cur.get("title") if isinstance(cur, dict) else None
            title = "" if isinstance(stored_title, str) and stored_title.strip() \
                else p["title"]
            # `sid` over the staged `payload.scene`, for the reason the plot
            # branch above gives at length: a retry after a rename must stamp the
            # beat with the id the scene has now, since `commitments.repoint_scenes`
            # has already moved every stored reference onto it.
            commitments.set_movement(cid, mid, title, p.get("kind", ""),
                                     p.get("status", ""), p.get("due"),
                                     after, sid or p["scene"])
        elif kind == "fact":
            p = e["payload"]
            # The TARGET id, not the payload's, for the reason the commitment
            # branch gives at length: everything that judged this row read
            # `target` -- `conflicts` surveyed it, `target_key` keyed the
            # journal by it, the reviewer's basis describes it -- so the one
            # write that can destroy something has to go where the checks went.
            fid = target["id"]
            # No staleness check here: `fact` is registered in
            # `conflicts._REASONS`, so the one-pass gate in `apply_edits` has
            # already refused this row if the fact it retires moved since it was
            # staged.
            text = after.strip()
            # The row's INTENT comes from the staged payload, never from whether
            # `after` happens to be blank -- the rule the voice_drift branch
            # above keeps, and for the same reason. The reviewer can edit the
            # text, and a replacement fact edited down to empty would otherwise
            # reclassify itself as a bare retirement: the old fact would go off
            # the ledger with nothing recorded in its place, which is a deletion
            # nobody asked for. A retirement the reviewer typed a replacement
            # INTO is the opposite case and is honoured -- they wrote the fact
            # that replaces it, which is exactly what a supersession is.
            if str(p.get("text", "")).strip() and not text:
                return {"state": "failed", "id": eid, "kind": "error",
                        "reason": "a fact cannot be blank — uncheck the row to skip it, "
                                  "or leave the replacement text in place"}
            if not text:
                if not fid:
                    return {"state": "skipped"}   # retires nothing, records nothing
                if not facts.retire(cid, fid, sid or p.get("scene", "")):
                    # Two ways in, and the message has to be true of both: an id
                    # the ledger never held (a forged or hand-built body, or a
                    # record deleted by hand), and a fact something else retired
                    # first -- which the gate above refuses UNLESS the reviewer
                    # answered for it, or the row carried no `before` to judge.
                    # Reported rather than passed over: the end state they asked
                    # for holds, but this scene is not what brought it about, and
                    # a silent success would date the retirement to the wrong
                    # scene in the reviewer's mind if not on the record.
                    return {"state": "failed", "id": eid, "kind": "error",
                            "reason": "that fact is not standing on this campaign's "
                                      "ledger — it was already retired, or it is gone"}
            else:
                # A replacement that says what the fact already says is not a
                # replacement. `materialize` drops the row when the MODEL writes
                # one, but the reviewer can edit the text into a restatement
                # afterwards and that path never goes back through it -- and
                # this is the one that takes a client-supplied body. Left
                # unchecked it retires a correctly dated fact and re-records the
                # same sentence under this scene's later date, manufacturing a
                # lifecycle change out of a truth that did not move.
                #
                # Reported rather than skipped: the reviewer approved a
                # retirement, it is not going to happen, and #271's rule is that
                # an approved change which does not land says so.
                if fid and facts.restates(facts.get(cid, fid), text):
                    return {"state": "failed", "id": eid, "kind": "error",
                            "reason": "this says what the fact it replaces already says — "
                                      "edit it to what is true now, or uncheck the row"}
                scene = sid or p.get("scene", "")
                # A row that retires nothing and whose text this scene already
                # holds writes nothing at all: `facts.record` dedupes on
                # (scene, text) and returns the id already there. Reported
                # rather than passed over as applied, because the row promised a
                # NEW entry with its own date and did not make one -- the
                # duplicate-approval gap `materialize` closes for the text the
                # MODEL wrote, reopened one layer down by the reviewer editing
                # `after` into something already recorded, or editing two
                # approved rows into the same sentence.
                #
                # Only when `fid` is empty. A row that also retires something
                # did real work even when its text deduped onto an existing
                # record -- that is the chain case `record` documents -- so
                # calling it failed would be the opposite lie.
                if not fid and facts.find(facts.read(cid), scene, text):
                    return {"state": "failed", "id": eid, "kind": "error",
                            "reason": "this scene already records that fact — edit it to "
                                      "something else, or uncheck the row"}
                # `sid` over the staged `payload.scene`, for the reason the plot
                # branch gives: a retry after a rename must stamp the fact with
                # the id the scene has now, since `facts.repoint_scenes` has
                # already moved every stored reference onto it.
                #
                # A supersession whose predecessor something else already
                # retired records the fact and leaves that retirement alone --
                # first writer wins, and `record` is where that is enforced.
                # The reviewer sees it coming: this is a conflict, and reaching
                # here means they answered for it. Re-aiming `superseded_by` on
                # their say-so would erase the only record of what actually
                # replaced the fact first, to no gain -- both replacements are
                # on the ledger either way, and a human can retire the one the
                # story did not keep.
                facts.record(cid, text, str(p.get("date", "")), scene, supersedes=fid)
        elif kind == "new_character":
            p = e["payload"]
            card = characters.blank_card(p["name"])
            after = materializer._new_character_provenance(after, p)
            card["data"]["description"] = after
            card["data"]["personality"] = p.get("personality", "")
            card["data"]["mes_example"] = p.get("mes_example", "")
            card["data"]["extensions"]["sd_prompt"] = p.get("sd_prompt", "")
            # Generated example dialogue uses the {{char}} macro; scene assembly
            # expands a stored {{char}} to the whole present cast, so bake the
            # card's own name in now (same as card import).
            cards.bake_char_name(card)
            # personality/mes_example are card writes too — log them (post-bake) so
            # the Changes panel shows the whole new card, not just the description.
            extra_fields = [
                {"field": f, "label": f"{e.get('label', '')} — {noun}",
                 "before": "", "after": card["data"][f]}
                for f, noun in (("personality", "personality"),
                                ("mes_example", "example dialogue"))
                if card["data"][f]]
            new_cid, new_vid = overlay.create_character(cid, p["name"], "default", card)
            try:
                dossiers.write(croot, new_cid, materializer._new_character_dossier(p["name"], p))
            except Exception:  # noqa: BLE001 — seed is best-effort: a failure here
                pass  # must not strand the created character (retry would duplicate it)
            if sid:
                appearances_transitions.appear(cid, sid, "characters", new_cid, new_vid, "npc", narrate=False)
            target = {"kind": "characters", "id": new_cid}
        elif kind == "new_location":
            p = e["payload"]
            new_eid = overlay.create_entity(cid, "locations", p["name"], after,
                                            p.get("keys", ""), sd_prompt=p.get("sd_prompt", ""))
            if sid and p.get("current_setting") and not scenes_read.get_location_history(cid, sid):
                scenes_moment.set_location(cid, sid, new_eid)
            target = {"kind": "locations", "id": new_eid}
        elif kind == "new_lore":
            p = e["payload"]
            new_eid = overlay.create_entity(cid, "lore", p["name"], after, p.get("keys", ""))
            target = {"kind": "lore", "id": new_eid}
        else:
            return {"state": "failed", "id": eid, "kind": "error",
                    "reason": f"this campaign does not know how to apply a "
                              f"{kind!r} change"}
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
    return {"state": "applied", "id": eid, "recorded": recorded, "journalled": journalled}


#: What a resumed commit says about a step it journalled and never confirmed.
#: Repeating it could duplicate an append, so the reviewer is told instead.
UNCONFIRMED = ("an earlier save started this and did not say whether it landed — "
               "check the record before applying it again")


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
    `conflicts.check_conflicts` first. When `sid` is given, the before/after
    of each applied *browsable* edit (characters/lore/locations) is captured into
    changes.json (the latest write-back delta per record); sheet edits are never
    browsable and never land there -- the sheet itself is the record.

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
    recorded: dict = journal.setdefault("recorded", {})
    # The citations behind the rows that land. Journalled beside `recorded` and
    # published in the same guarded block below, because it is the same kind of
    # rolling upsert with the same replay hazard.
    cited: dict = journal.setdefault("cited", {})
    # The append-only change journal's rows (#31), carried here for the same
    # reason `recorded` is: they are published in one append once the whole list
    # is through, so a commit that died mid-list must not lose the history of
    # what it had already written.
    journalled: list = journal.setdefault("journalled", [])
    applied: list[str] = []
    failures: list[dict] = []
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
    verdicts = journal.get("verdicts")
    readings = journal.get("readings")
    replaying = (isinstance(verdicts, list) and len(verdicts) == len(edits)
                 and isinstance(readings, list) and len(readings) == len(edits))
    if not replaying:
        verdicts, readings = conflicts.batch_survey(cid, edits)
        # Durable before the first write: the checkpoint below runs ahead of
        # edit 0, and a crash before that leaves nothing applied to be judged
        # against, so a recomputation there is judging the same store anyway.
        journal["verdicts"], journal["readings"] = verdicts, readings
    landed: set[tuple] = set()
    for i, e in enumerate(edits):
        slot = str(i)
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
            for ref, rows in prior.pop("recorded", {}).items():
                recorded.setdefault(ref, []).extend(rows)
            row = prior.pop("journalled", None)
            if row:
                journalled.append({"scene": sid or "", "source": "absorb", **row})
            if prior.get("state") == "applied":
                # Journalled with the outcome: what the target reads now this
                # edit has landed is how a LATER slot, resuming after a crash,
                # recognises the movement as this commit's own work.
                prior["read"] = conflicts.current_value(cid, e)
        if prior.get("state") == "applied":
            applied.append(prior.get("id", ""))
            # Every kind, not just the browsable ones `recorded` covers: a fact
            # and a plot beat are exactly the continuity lines this exists to
            # make checkable, and neither is browsable.
            if sid:
                pkey = provenance.key(e)
                prow = provenance.row(e, sid) if pkey else None
                if pkey and prow:
                    cited[pkey] = prow
            read, key = prior.get("read"), conflicts.target_key(e)
            # Recomputed from the edit rather than journalled beside the reading:
            # the token's fingerprint has already refused any retry whose body
            # differs, so this list is the one the first attempt worked from.
            if isinstance(read, str) and key is not None:
                landed.add((key, read))
        elif prior.get("state") == "pending":
            failures.append({"id": prior.get("id", ""), "kind": "error",
                             "reason": UNCONFIRMED})
        elif prior.get("state") == "failed":
            failures.append({"id": prior.get("id", ""),
                             "kind": prior.get("kind", "error"),
                             "reason": prior.get("reason", "")})
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
    logs = bool(recorded or cited or journalled)
    if logs and prior_changes is None:
        journal["changes"] = "pending"
    if checkpoint:
        checkpoint()      # the last edit's outcome, so a crash before `record` resumes clean
    if logs:
        if isinstance(prior_changes, dict):
            # An earlier attempt tried and got a definite answer. changes.record
            # publishes by atomic rename, so its exception PROVED nothing landed
            # -- a fact worth keeping over the vaguer "unconfirmed", even though
            # the response is the same refusal to replay the stale upsert.
            failures.append({"id": "changes", "kind": "error",
                             "reason": str(prior_changes.get("reason", ""))})
        elif prior_changes == "pending":
            failures.append({"id": "changes", "kind": "error", "reason": UNCONFIRMED})
        else:
            try:
                changes.record(cid, sid, recorded)
                # After the diffs, and inside their try: both are display logs
                # written once the edits have already landed, both are rolling
                # upserts, and a failure of either is the same class of loss —
                # the record is right and the panel explaining it is stale.
                provenance.record(cid, cited)
                # Last of the three, and the only one that is append-only: it
                # goes after the two upserts so a failure here cannot leave the
                # panel showing a delta the history has no row for.
                change_journal.append(cid, journalled)
            except Exception as exc:  # noqa: BLE001 — the edits landed; the delta log did not
                reason = f"the changes panel could not be updated: {exc}"
                # Settled, not left pending: if this commit also fails to record
                # its result, the retry should report what actually happened
                # rather than the ambiguity `pending` stands for.
                journal["changes"] = {"state": "failed", "reason": reason}
                if checkpoint:
                    checkpoint()
                failures.append({"id": "changes", "kind": "error", "reason": reason})
    return applied, failures
