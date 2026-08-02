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
                overlay, playstate, plot, relationships)
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
            plot.set_movement(cid, p["id"], title, p["status"], after, p["scene"])
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
    # itself. On a RESUME the earlier writes are this commit's own, already
    # landed -- so an edit that never ran can be judged against a store its
    # siblings moved. That errs the reported-not-applied way, which is the same
    # answer the reviewer would get by saving again.
    verdicts = conflicts.batch_verdicts(cid, edits)
    for i, e in enumerate(edits):
        slot = str(i)
        prior = outcomes.get(slot)
        if not isinstance(prior, dict):
            outcomes[slot] = {"state": "pending",
                              "id": e.get("id", "") if isinstance(e, dict) else ""}
            if checkpoint:
                checkpoint()
            prior = outcomes[slot] = _apply_one(cid, croot, e, sid, verdicts[i])
            for ref, rows in prior.pop("recorded", {}).items():
                recorded.setdefault(ref, []).extend(rows)
        if prior.get("state") == "applied":
            applied.append(prior.get("id", ""))
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
