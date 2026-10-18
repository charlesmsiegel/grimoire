"""Per-kind approved record writes, before common outcome sealing.

The caller snapshots Undo first and keeps the ordinary-write exception barrier.
Each writer returns either its existing skipped/failed outcome or the actual
record/text it wrote. Those actual values, not the staged labels or payload IDs,
are what the common outcome layer journals and cites.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import (
    cards,
    characters,
    commitments,
    dossiers,
    facts,
    groupstate,
    overlay,
    playstate,
    plot,
    relationships,
    voice_drift,
)
from ..appearances import paths as appearances_paths
from ..appearances import transitions as appearances_transitions
from ..appearances import versions as appearances_versions
from ..scenes import moment as scenes_moment
from ..scenes import read as scenes_read
from . import materializer, parse, weather


@dataclass(frozen=True)
class Context:
    """The campaign/root/scene snapshot shared by one edit's record write."""
    cid: str
    root: Path
    sid: str | None


@dataclass(frozen=True)
class Written:
    """The actual write, carried into common outcome/Undo composition.

    A reallocated thread/commitment moved away from the snapshotted record, so
    its old reversal cannot describe the new one. Relationship values are read
    back from the record; a client may supply a different display `after`.
    """
    # The result carries dict/list values; it is a write description, never a key.
    __hash__ = None  # type: ignore[assignment]

    target: dict
    after: str
    extra_fields: list[dict] = field(default_factory=list)
    # Which two actors this edit moved and what it left them at, for the
    # relationship timeline (#63). Set by the two writers that move
    # `relationships.json` and left None by every other kind.
    #
    # Every field comes from the RECORD rather than from the staged edit:
    # the tokens from the payload the write used (not `target`), and the
    # standing read back after the write (not `after`). They agree for
    # anything `materialize` staged -- it renders `after` from the same
    # payload -- but an edit reaches here from a client-supplied PUT body
    # typed as an unrestricted dict, where the two can disagree. The journal
    # can carry that disagreement as display text and lose nothing; an
    # append-only timeline would carry it forever, claiming a standing
    # `relationships.json` does not hold and never will.
    relationship: dict | None = None
    reallocated: bool = False


def _weather(context: Context, e: dict) -> Written | dict:
    """Skip a weather span that has no usable date instead of reporting a write."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    if not weather._apply_weather(cid, e, after):
        return {"state": "skipped"}   # skipped, not applied: nothing was written
    return Written(target, after)


def _character_state(context: Context, e: dict) -> Written | dict:
    """Write the chosen actor's campaign-local state, including PC storage."""
    target, after = e["target"], e.get("after", "")
    croot = context.root
    playstate.write_state(croot, target["id"], after,
                          kind=target.get("kind", "characters"))
    return Written(target, after)


def _group_state(context: Context, e: dict) -> Written | dict:
    """Write the group's campaign-local state sections."""
    target, after = e["target"], e.get("after", "")
    croot = context.root
    groupstate.write_state(croot, target["id"], after)
    return Written(target, after)


def _dossier(context: Context, e: dict) -> Written | dict:
    """Replace a live NPC's dossier only while the review's basis still matches."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    croot = context.root
    eid = e.get("id", "") if isinstance(e.get("id"), str) else ""
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
    return Written(target, after)


def _voice_drift(context: Context, e: dict) -> Written | dict:
    """Honor raise/clear intent, anchor provenance and the flag's paired CAS."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    croot = context.root
    eid = e.get("id", "") if isinstance(e.get("id"), str) else ""
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
    return Written(target, after)


def _lore(context: Context, e: dict) -> Written | dict:
    """Write the resolved entity body through its inherited-record overlay."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    overlay.update_entity(cid, target["kind"], target["id"], body=after)
    return Written(target, after)


def _authored(context: Context, e: dict) -> Written | dict:
    """Write only an approved card field on the campaign's locked version."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    if e["field"] not in materializer._CARD_FIELDS:
        return {"state": "skipped"}  # re-guard: PUT edits are client-supplied
    vid = appearances_versions.locked_version(cid, "characters", target["id"])
    aroot = appearances_paths.locked_actor_root(cid)   # locked -> materialized
    card = characters.read_card(aroot, target["id"], vid)
    card["data"][e["field"]] = after
    characters.update_version(aroot, target["id"], vid, card)
    return Written(target, after)


def _relationship(context: Context, e: dict) -> Written | dict:
    """Write a feeling and read its actual standing for the append-only timeline."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    p = e["payload"]
    relationships.set_feeling(cid, p["from"], p["to"], p["trust"], p["affection"],
                              p["tension"], p.get("note", ""))
    pair = {"kind": "feeling", "a": p["from"], "b": p["to"],
            "after": relationships.render_standing(
                "feeling", relationships.get_feeling(cid, p["from"], p["to"]))}
    return Written(target, after, relationship=pair)


def _bond(context: Context, e: dict) -> Written | dict:
    """Write the pair's bond and return its actual standing for the timeline."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    p = e["payload"]
    relationships.set_bond(cid, p["a"], p["b"], p["type"])
    pair = {"kind": "bond", "a": p["a"], "b": p["b"],
            "after": relationships.render_standing(
                "bond", relationships.get_bond(cid, p["a"], p["b"]))}
    return Written(target, after, relationship=pair)


def _plot(context: Context, e: dict) -> Written | dict:
    """Move the judged thread, reallocating a staged-new row after a collision."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    sid = context.sid
    reallocated = False
    p = e["payload"]
    # The TARGET id, and a row STAGED AS NEW reallocated off an id that
    # is taken since -- both for the reasons the commitment branch below
    # gives at length, with the same allocator predicate (§10.5): a
    # collision is honoured only when the stored thread is not closed
    # and carries the same title. Otherwise `set_movement` would append
    # this beat (and status) onto somebody else's thread.
    if not str(e.get("before", "")).strip():
        new = materializer._new_thread_id(
            plot.read(cid), {}, target["id"], p.get("title", ""))
        if new != target["id"]:
            # Snapshotted from the record the write moved off: no
            # reversal, as for a reallocated commitment.
            reallocated = True
            target = {**target, "id": new}
            p = {**p, "id": new}
    # An absorb never renames an existing thread: `materialize` stages
    # the STORED title for one that already exists (`cur.get("title") or
    # title or pid`), so the staged title carries no intent to rename and
    # writing it back is a no-op -- except when somebody renamed the
    # thread between staging and saving, where `set_movement` overwrites
    # any non-blank title and silently reverts them. A blank title leaves
    # the stored one alone and still falls back to the id for a brand-new
    # thread, so the rename is not a conflict to resolve; it stands.
    title = "" if plot.get(cid, target["id"]) else p["title"]
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
    plot.set_movement(cid, target["id"], title, p["status"], after, sid or p["scene"])
    return Written(target, after, reallocated=reallocated)


def _commitment(context: Context, e: dict) -> Written | dict:
    """Move the judged commitment while preserving renames and new-record identity."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    sid = context.sid
    reallocated = False
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
            reallocated = True
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
    return Written(target, after, reallocated=reallocated)


def _fact(context: Context, e: dict) -> Written | dict:
    """Record, supersede or retire facts without restatements or empty approvals."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    sid = context.sid
    eid = e.get("id", "") if isinstance(e.get("id"), str) else ""
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
    return Written(target, after)


def _new_character(context: Context, e: dict) -> Written | dict:
    """Create the reviewed card, best-effort dossier seed and optional scene cast."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    croot = context.root
    sid = context.sid
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
    # Seed is best-effort: a failure here must not strand the created character
    # (retry would duplicate it).
    with contextlib.suppress(Exception):
        dossiers.write(croot, new_cid, materializer._new_character_dossier(p["name"], p))
    if sid:
        appearances_transitions.appear(cid, sid, "characters", new_cid, new_vid, "npc", narrate=False)
    target = {"kind": "characters", "id": new_cid}
    return Written(target, after, extra_fields=extra_fields)


def _new_location(context: Context, e: dict) -> Written | dict:
    """Create the location and link it only if the scene still has no setting."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    sid = context.sid
    p = e["payload"]
    new_eid = overlay.create_entity(cid, "locations", p["name"], after,
                                    p.get("keys", ""), sd_prompt=p.get("sd_prompt", ""))
    if sid and p.get("current_setting") and not scenes_read.get_location_history(cid, sid):
        scenes_moment.set_location(cid, sid, new_eid)
    target = {"kind": "locations", "id": new_eid}
    return Written(target, after)


def _new_lore(context: Context, e: dict) -> Written | dict:
    """Recheck the reviewed kind/name before creating its campaign-local record."""
    target, after = e["target"], e.get("after", "")
    cid = context.cid
    eid = e.get("id", "") if isinstance(e.get("id"), str) else ""
    p = e["payload"]
    # A client-edited payload is clamped like the model's word is.
    lore_kind = parse.new_lore_kind(p.get("kind"))
    # In the kind it lands as, which the reviewer may have changed since
    # staging deduped it -- reported, not suffixed into a duplicate.
    taken = materializer.record_taken(cid, lore_kind, p["name"])
    if taken:
        return {"state": "failed", "id": eid, "kind": "conflict", "reason": taken}
    new_eid = overlay.create_entity(cid, lore_kind, p["name"], after, p.get("keys", ""))
    target = {"kind": lore_kind, "id": new_eid}
    return Written(target, after)


_WRITERS: dict[str, Callable[[Context, dict], Written | dict]] = {
    "weather": _weather,
    "character_state": _character_state,
    "group_state": _group_state,
    "dossier": _dossier,
    "voice_drift": _voice_drift,
    "lore": _lore,
    "authored": _authored,
    "relationship": _relationship,
    "bond": _bond,
    "plot": _plot,
    "commitment": _commitment,
    "fact": _fact,
    "new_character": _new_character,
    "new_location": _new_location,
    "new_lore": _new_lore,
}


def write(context: Context, edit: dict) -> Written | dict:
    """Write a well-shaped ordinary edit; the caller owns its exception barrier."""
    kind = edit["kind"]
    writer = _WRITERS.get(kind)
    if writer is None:
        eid = edit.get("id", "") if isinstance(edit.get("id"), str) else ""
        return {"state": "failed", "id": eid, "kind": "error",
                "reason": f"this campaign does not know how to apply a {kind!r} change"}
    return writer(context, edit)
