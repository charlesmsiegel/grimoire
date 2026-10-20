"""An assigned response lands only behind its claim, with authority, rewrite and provenance accounting."""

from __future__ import annotations

import copy
import logging

from ... import store, wire
from ...llm import ATTEMPTED
from .. import streaming
from .. import tracker as tracker_routes
from ._router import router as router

_log = logging.getLogger('grimoire.character_turns')


def _save(claim, turn, status):
    """Persist the contribution in flight's variant, as `status`: what
    `turn.watcher` read of the reply to `turn.record`. A key the tracker marked
    `pending` for it is appended to `turn.tracked`, for `_frames` to start
    once the turn's terminal frames are out (see `_start_tracking`).
    `turn.served` is the id of the connection that served it, recorded on the
    variant and its post."""
    cid, sid = claim.cid, claim.sid
    record, watcher, continuation = turn.record, turn.watcher, turn.continuation
    connection = turn.served
    with store.locks.campaign_lock(cid):
        claim.fence()
        text, authority_issue, rewrite = _normalise(cid, sid, record, watcher.narration,
                                                    connection)
        if authority_issue:
            watcher.handoff = None
            watcher.issue = authority_issue
        if not text and status == "complete":
            status = "incomplete"
            watcher.handoff = None
            watcher.issue = "empty response"
        saved = lambda: store.responses.save_variant(
            cid,
            sid,
            record["id"],
            text,
            status,
            handoff=watcher.handoff if status == "complete" else None,
            issue=watcher.issue,
            part=continuation or "",
            reasoning=watcher.reasoning + watcher.preparation_note,
            connection=connection,
            made_by=turn.made_by,
            tool=turn.tool_record,
        )
        before = streaming._active_variant(cid, sid, record["id"]) if continuation else ""
        if continuation and status == "complete":
            if not store.proposals.commit_narration(cid, sid, continuation, saved):
                raise store.responses.ResponseConflict(
                    "proposal_stale", "The roll continuation is stale."
                )
        else:
            saved()
        # Keyed by the part, as the transcript message it describes is: a
        # resumed continuation is a second message under the same response,
        # and its record must not replace the first part's.
        key = store.regex.rewrites.key_for(
            {"response_id": record["id"], "response_part": continuation or ""})
        if text and rewrite:
            streaming._record_rewrite(cid, sid, key, *rewrite, text,
                                      streaming._active_variant(cid, sid, record["id"]))
        if before:
            streaming._carry_rewrite(cid, sid, record["id"], before, skip=key)
        if continuation:
            # `commit_narration` trims an earlier attempt at the continuation.
            streaming._prune_rewrites(cid, sid)
        streaming._turn_settled(cid)
        if text:
            # Marked HERE, in the hold that wrote the variant, where the
            # tracker's lock acquisition is reentrant: anywhere later on this
            # path it would be a fresh wait in front of the player's frames.
            marked = tracker_routes.mark_response(cid, sid, record["id"])
            if marked:
                turn.tracked.append(marked)
        return streaming._tail_length(cid, sid) if text else None


def _normalise(cid, sid, record, text, connection=""):
    """The reply as it is stored, the authority issue if it spoke for another
    actor, and `(original, fired rule ids)` when the store phase rewrote it
    (else None).

    The store phase runs LAST, on the text that would otherwise have been
    stored, rather than beside the macro expansion: what it records is what
    Restore original writes back, so it has to be stored text already -- not
    text still carrying markers this function is about to strip. `connection`
    is the one that served the reply, for its connection-level rules."""
    text, _ = store.state_fence.split_block(text)
    text = store.context.resolve_art_handles(cid, text, sid)
    text = store.context.expand_macros(
        text, store.context.scene_substitutions(cid, sid), cid, sid
    ).strip()
    markers = store.scenes._markers(text)
    parts = [text[: markers[0].start()].strip()] if markers else [text]
    issue = None
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        label = marker.group(1)
        allowed = label == record["speaker"] and label != "You"
        if record["actor_ref"] == "grimoire":
            allowed = label == "Grimoire" and not marker.group(2)
        if allowed:
            parts.append(text[marker.end() : end].strip())
        else:
            issue = "response attempted to speak for another actor"
    text = "\n\n".join(part for part in parts if part)
    stored, fired = store.regex.view.store_phase(text, cid=cid, role="model",
                                                 connection=connection)
    return (stored.strip(), issue, (text, fired)) if fired else (text, issue, None)


def _made_by(
    meter, task: str, composed: str, note: str, guidance: str = "", *,
    settings: dict | None = None, extra: dict | None = None,
) -> dict | None:
    """What wrote a variant, for its `made_by`: the route the meter's holder
    says served the call, read after `meter.done()`.

    The holder rather than `meter.row`, because the row is None when the ledger
    append failed while the holder still has the values; and the holder rather
    than the connection the route resolved, because `llm._stamp` resets it per
    attempt, so it names a fallback that answered. A key the holder lacks is
    left out, never guessed (`effective_model` would be a guess). No reads -- it
    runs on the event loop -- and fail-soft (it logs): provenance never fails a
    turn.

    `settings` is given only for a resume-composed call: the record holds ONE
    `resume_settings`, which a later roll fence's recomposition overwrites, so
    a variant written from an earlier resume carries a copy of what its prompt
    rendered. A primary-composed variant reads the record's `settings`, which
    nothing overwrites, so it carries no copy.

    `extra` is merged last: a Keep writing variant's `extends` and `mode`."""
    if meter is None:
        return None
    try:
        usage = meter.usage
        served = {
            "connection_id": _attempted_id(usage),
            "connection": usage.get("connection"),
            "model": usage.get("model"),
            "provider": usage.get("provider"),
        }
        return {
            "task": task,
            **{key: value for key, value in served.items() if value},
            "composed": composed,
            "guidance": (guidance or "")[: store.alternates.MAX_GUIDANCE_CHARS],
            "note": (note or "")[: store.alternates.MAX_GUIDANCE_CHARS],
            **({"settings": copy.deepcopy(settings)} if settings is not None else {}),
            **copy.deepcopy(extra or {}),
        }
    except Exception:  # noqa: BLE001 - provenance must never fail a turn
        _log.exception("could not record what made a %s response", task)
        return None


def _attempted_id(usage: dict) -> str | None:
    """The provider id of the attempt the facade stamped into `usage`
    (`llm.ATTEMPTED`), or None when it stamped none."""
    attempted = usage.get(ATTEMPTED)
    return attempted.provider_id if isinstance(attempted, wire.Target) else None


def _land_variant(cid, sid, rid, text, *, handoff, issue, reasoning, tracked=None,
                  made_by=None, connection="",
                  rewrite: tuple[str, list[str]] | None = None, tool=None) -> None:
    """Save `text` as a new complete variant of `rid` and make it the one shown.

    The tail a reroll and a Keep writing continuation share. Called inside the
    caller's campaign-lock hold, after its fence. `connection` is the one that
    served the call; `rewrite` is `(original, fired rule ids)` when the store
    phase rewrote `text`, recorded under the response for the new variant --
    activating folds the response into one message, keyed by its bare id."""
    variant = store.responses.save_variant(
        cid,
        sid,
        rid,
        text,
        "complete",
        handoff=handoff,
        issue=issue,
        activate=False,
        reasoning=reasoning,
        connection=connection,
        made_by=made_by,
        tool=tool,
    )
    store.responses.activate(cid, sid, rid, variant["id"])
    if rewrite:
        streaming._record_rewrite(cid, sid, rid, *rewrite, text, variant["id"])
    streaming._prune_rewrites(cid, sid)
    # A reroll is a swipe to a new variant: every later tracker record was
    # built on the one it replaced. In this hold, with the swap; fail-soft.
    tracker_routes.after_swipe(cid, sid, rid)
    streaming._turn_settled(cid)
    if tracked is not None:
        # In this hold, for `_save`'s reason: reentrant here, a fresh wait
        # in front of the frames anywhere after it.
        marked = tracker_routes.mark(
            cid, sid, store.tracker.paths.response_key(rid, variant["id"]))
        if marked:
            tracked.append(marked)
