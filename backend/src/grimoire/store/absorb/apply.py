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

from .. import (cards, changes, characters, dossiers, entities, groupstate,
                overlay, playstate, plot, relationships, voice_drift)
from ..appearances import (paths as appearances_paths,
                           transitions as appearances_transitions,
                           versions as appearances_versions)
from ..audit import apply as audit_apply
from ..campaigns import paths as campaigns_paths
from ..scenes import moment as scenes_moment, read as scenes_read
from ..sheets import paths as sheets_paths
from . import conflicts, materializer, weather

_BROWSABLE_KINDS = ("character_state", "dossier", "lore", "authored", "new_character",
                    "new_location", "new_lore")


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

    - ``{"state": "applied", "id", "recorded": {ref: [rows]}}`` -- it landed;
      `recorded` is its write-back delta when the kind is browsable.
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
        return {"state": "applied", "id": eid, "recorded": {}}
    # Shape first, and a bad one is a SKIP rather than a failure. #271 made every
    # write that fails report, but a row this garbled was never a coherent edit
    # to begin with -- it cannot come from the review panel, only from a forged
    # or corrupted body, and there is nothing about it to tell a reviewer. Doing
    # it here rather than letting the handler below raise is what keeps the two
    # apart: past this point an exception really is a write that failed.
    if (not isinstance(e.get("kind"), str) or not isinstance(e.get("target"), dict)
            or (e.get("payload") is not None and not isinstance(e.get("payload"), dict))):
        return {"state": "skipped"}
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
    return {"state": "applied", "id": eid, "recorded": recorded}


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
            if prior.get("state") == "applied":
                # Journalled with the outcome: what the target reads now this
                # edit has landed is how a LATER slot, resuming after a crash,
                # recognises the movement as this commit's own work.
                prior["read"] = conflicts.current_value(cid, e)
        if prior.get("state") == "applied":
            applied.append(prior.get("id", ""))
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
    prior_changes = journal.get("changes")
    if sid and recorded and prior_changes is None:
        journal["changes"] = "pending"
    if checkpoint:
        checkpoint()      # the last edit's outcome, so a crash before `record` resumes clean
    if sid and recorded:
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
