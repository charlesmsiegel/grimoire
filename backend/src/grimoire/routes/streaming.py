"""SSE framing, the persisted-turn strategies, and the roll-proposal
machinery they share.

Crash-window disclosure (accepted risk, per the phase-4 spec). The fence
handoffs are serialized against concurrent writers by the per-campaign
proposals lock, but are NOT crash-atomic across files (proposals.json and
the scene transcript are separate writes; grimoire is a local single-process
app with no cross-file journal). Two microsecond-wide windows exist, both
bounded and non-corrupting:

  - initial fence: a crash between writing the pending record and persisting
    the pre-fence narration leaves a recoverable chip whose last narration
    beat is missing — the player can still adjudicate or decline;
  - follow-up fence: a crash between the old record's `narrated` write and
    the new pending record leaves the continuation fully persisted and the
    follow-up check simply lost; play continues on the next send.

The guaranteed invariant: no roll is ever duplicated or lost once logged, no
narration is attributed to a superseded decision, and no crash leaves an
unrecoverable or corrupted state. Full journaling was rejected as
disproportionate for a local single-user store.

This module holds no routes; ``scenes``, ``mechanics`` and ``greetings``
import from it.
"""

from __future__ import annotations

import json

from fastapi.responses import StreamingResponse

from .. import store
from ..llm import LLMClient
from ..llm_errors import LLMError


def _persist_reply(cid: str, sid: str, text: str) -> None:
    """Split one model reply into per-speaker posts and append them (#744).
    Macros are expanded before persisting (#137): {{roll}}/{{random}} must be
    resolved once, not re-rolled on every future context build that re-reads
    this now-historical message. Goes through append_reply so the generation
    records its own turn boundary for drift measurement."""
    players = frozenset(store.appearances.player_names(cid, sid))
    subs = store.context.scene_substitutions(cid, sid)
    segments = [{"speaker": seg["speaker"],
                 "content": store.context.expand_macros(seg["content"], subs, cid, sid)}
                for seg in store.scenes.split_reply(text, players)]
    store.scenes.append_reply(cid, sid, segments)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _sse_response(frames: list[str]):
    """A StreamingResponse that just replays already-computed SSE frames (used
    for the immediate-done / error-frame branches of the proposal route)."""
    async def event_stream():
        for f in frames:
            yield f
    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _fence_stream(cid: str, sid: str, messages: list[dict], conn: dict,
                  client: LLMClient, finalize, on_error=None):
    """Stream one persisted turn while watching for a ```roll fence.

    Deltas are routed through a FenceWatcher, so an opener (even split across
    chunks) is never emitted and streaming stops once a fence closes. When the
    stream ends, `finalize(watcher)` (called with the lock/persist strategy of
    the caller — initial turn vs continuation) returns the trailing SSE frames
    (proposal / done). `on_error(watcher)` decides what to persist on an
    upstream LLM failure. Fence watching runs on persisted turns only;
    `_ephemeral_stream` is deliberately untouched.
    """
    async def event_stream():
        watcher = store.fence.FenceWatcher()
        try:
            async for delta in client.stream(messages, conn):
                out = watcher.feed(delta)
                if out:
                    yield _sse({"delta": out})
                if watcher.complete:
                    break  # stop-after-fence: ignore anything past the close
            tail = watcher.finish()
            if tail:
                yield _sse({"delta": tail})
        except LLMError as exc:
            watcher.finish()
            if on_error is not None:
                on_error(watcher)
            yield _sse({"error": {"detail": exc.detail, "kind": exc.kind}})
            return
        for frame in finalize(watcher):
            yield frame

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _heal_current_proposal(cid: str, sid: str) -> None:
    """Complete the scene's current record's projection (roll + 🎲 line +
    metadata) before the record is retired or replaced. The record is the only
    recovery handle for a projection crash (roll tagged, line missing): the
    stale-retry heal in the POST roll-proposal route matches on the record's
    id and only projects superseded records that still carry a resolution, so
    once ``new`` overwrites ``data[sid]`` (or the frontend stops offering the
    superseded record) the roll would stand in rolls.json without its
    transcript line forever. Projection is idempotent pure file I/O, so
    healing an already-complete record is a cheap no-op. Only records whose
    resolution carries a roll ``result`` can project — declined records never
    store a resolution (and would have no roll to project if they somehow
    did), and pending/resolving records have nothing resolved yet; those are
    retired/replaced as before.

    INVARIANT (keep it when adding call sites): a record with a projectable
    resolution is never retired (``proposals.supersede``) nor replaced
    (``proposals.new``) before its projection completes — every retirement or
    replacement path calls this first. The heal is idempotent, and a crash
    during the heal leaves the record current, so the next attempt re-heals.
    The only remaining loss windows are the spec's two accepted fence-handoff
    ones.

    The call sites are ``_chat_stream`` / ``_continuation_stream`` below and
    the three generating routes in ``scenes`` (chat, retry, regenerate); every
    ``proposals.new`` / ``proposals.supersede`` in the package is one of
    those. ``tests/test_routes.py`` covers each crash window."""
    rec = store.proposals.get(cid, sid)
    if (isinstance(rec, dict) and isinstance(rec.get("resolution"), dict)
            and "result" in rec["resolution"]):
        _project_resolution(cid, sid, rec["id"])


def _chat_stream(cid: str, sid: str, messages: list[dict], conn: dict, client: LLMClient):
    """A normal persisted turn. A ```roll fence cuts the stream: the pending
    proposal record is written *before* the pre-fence narration persists, so a
    transcript that ends at a mechanical decision point always has a
    recoverable proposal (see the crash-window disclosure above)."""
    def finalize(watcher) -> list[str]:
        frames: list[str] = []
        if watcher.complete or watcher.truncated:
            with store.locks.campaign_lock(cid):
                payload = _make_proposal(cid, sid, watcher)
                _heal_current_proposal(cid, sid)  # new() erases the recovery handle
                rec = store.proposals.new(cid, sid, payload)
            _persist_reply(cid, sid, watcher.narration)
            frames.append(_sse({"proposal": {**payload, "id": rec["id"]}}))
        elif watcher.narration.strip():
            _persist_reply(cid, sid, watcher.narration)
        frames.append(_sse({"done": True}))
        return frames

    def on_error(watcher) -> None:
        if watcher.narration.strip():  # a normal turn keeps its partial reply
            _persist_reply(cid, sid, watcher.narration)

    return _fence_stream(cid, sid, messages, conn, client, finalize, on_error)


def _continuation_stream(cid: str, sid: str, pid: str, messages: list[dict],
                         conn: dict, client: LLMClient):
    """Stream a proposal's continuation and commit it atomically. A supersede
    that lands mid-stream makes ``commit_narration`` return False and the
    streamed text is dropped. A follow-up fence in the continuation hands off
    under one lock: commit the old record's narration, then mint the new
    pending record, then emit its proposal event."""
    def finalize(watcher) -> list[str]:
        frames: list[str] = []
        persist = lambda: _persist_reply(cid, sid, watcher.narration)  # noqa: E731
        if watcher.complete or watcher.truncated:
            with store.locks.campaign_lock(cid):
                if store.proposals.commit_narration(cid, sid, pid, persist):
                    payload = _make_proposal(cid, sid, watcher)
                    # the lock is reentrant, so healing (projection) is safe
                    # here; new() below erases the recovery handle
                    _heal_current_proposal(cid, sid)
                    rec = store.proposals.new(cid, sid, payload)
                    frames.append(_sse({"proposal": {**payload, "id": rec["id"]}}))
        else:
            store.proposals.commit_narration(cid, sid, pid, persist)
        frames.append(_sse({"done": True}))
        return frames

    # No on_error: an upstream failure mid-continuation drops the partial
    # (nothing persisted) and leaves the record resolved/declined, so a retry
    # re-streams a fresh continuation cleanly.
    return _fence_stream(cid, sid, messages, conn, client, finalize)


def _ephemeral_stream(messages: list[dict], conn: dict, client: LLMClient):
    """Stream a generation without persisting it to any scene (used by the opener)."""
    async def event_stream():
        try:
            async for delta in client.stream(messages, conn):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except LLMError as exc:
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- roll-proposal derivation and projection ----
# Used by the turn strategies above and by the roll-proposal routes in
# `mechanics`, which is why they live here rather than beside those routes.
def _scene_messages(cid: str, sid: str) -> list[dict]:
    return store.scenes.read_scene(cid, sid)["messages"]


def _roll_label(res: dict) -> str:
    return f"{res['actor_label']} — {res['check_label']}"


def _make_proposal(cid: str, sid: str, watcher) -> dict:
    """Build the proposal payload from a closed/truncated fence: parse the
    body, resolve the actor against the scene's available checks (exact
    kind:id, then case-insensitive label), and collect `problems`. A proposal
    is never silently dropped — a bad one just opens the chip in Modify."""
    fields, problems = store.fence.parse_roll_body(watcher.body or "")
    if watcher.truncated:
        problems = [*problems, "roll fence truncated"]

    actors = store.checks.available_checks(cid, sid)
    available = {a["ref"]: a["checks"] for a in actors}

    actor_raw = fields.get("actor")
    actor, actor_label = None, actor_raw
    if actor_raw:
        for a in actors:
            if a["ref"] == actor_raw:
                actor, actor_label = a["ref"], a["label"]
                break
        if actor is None:
            for a in actors:
                if a["label"].lower() == str(actor_raw).strip().lower():
                    actor, actor_label = a["ref"], a["label"]
                    break
    if actor is None:
        problems = [*problems, "actor could not be resolved"]

    mid = store.modules.resolve(cid)
    check_labels: dict[str, str] = {}
    if mid is not None:
        pack = store.modules.load_pack(mid)
        cd = pack["checks"] if isinstance(pack["checks"], dict) else {}
        check_labels = {k: (v.get("label", k) if isinstance(v, dict) else k)
                        for k, v in cd.items() if k != "_defaults"}

    check = fields.get("check")
    if check is not None:
        if check not in check_labels:
            problems = [*problems, "unknown check id"]
        elif actor is not None and check not in dict(available.get(actor, [])):
            problems = [*problems, "check unavailable to this actor"]
    elif fields:
        # fields is non-empty (e.g. valid JSON with an actor but no `check`
        # key) yet carries no check id — parse_roll_body's own tolerant path
        # already flags this same case (see fence.py); a wholly unparseable
        # body (fields == {}) is left alone here since "roll request was
        # unparseable" already covers it and a second problem would be noise.
        problems = [*problems, "roll request had no check id"]

    return {"check": check, "check_label": check_labels.get(check, check),
            "actor": actor, "actor_label": actor_label,
            "difficulty": fields.get("difficulty"), "modifier": fields.get("modifier", 0),
            "reason": fields.get("reason", ""), "available": available,
            "problems": problems}


def _project_resolution(cid: str, sid: str, pid: str) -> dict | None:
    """Idempotent, crash-recoverable projection of a resolved proposal into the
    roll log and transcript. Runs entirely under the proposals per-campaign
    lock (pure file I/O, no LLM), so concurrent retries serialize. The updated
    resolution is carried forward across each CAS — never rebuilt from a stale
    local — so the roll_id survives the line_intent write.

    Defensive re-validation: the caller checks status *before* acquiring this
    lock, so a supersede + brand-new record for the scene can land in that
    narrow window. If the scene's current record no longer carries this
    proposal id, or has no stored resolution yet, another actor won — return
    None and do nothing (no roll append, no line). Deliberately NOT a status
    check: a record that still carries this id but was superseded after
    resolving (status "superseded") must still project — its roll stands in
    the transcript as history per spec; only the automatic continuation is
    cancelled (by commit_narration), not the roll projection itself. The
    roll_id/line_intent backfills persist via ``update_resolution``, which
    writes metadata without touching terminal status, so a same-id superseded
    record keeps them (a status CAS would silently lose and drop them)."""
    with store.proposals.locked(cid):
        rec = store.proposals.get(cid, sid)
        if (rec is None or rec.get("id") != pid
                or rec.get("status") not in ("resolved", "superseded")
                or not isinstance(rec.get("resolution"), dict)):
            return None
        res = dict(rec["resolution"])
        entry = store.rolls.find_or_append_by_proposal(
            cid, sid, _roll_label(res), res["result"], proposal=pid, tier=res.get("tier"))
        res = {**res, "roll_id": entry["id"]}
        store.proposals.update_resolution(cid, sid, pid, res)
        if "line_intent" not in res:
            res = {**res, "line_intent": len(_scene_messages(cid, sid))}
            store.proposals.update_resolution(cid, sid, pid, res)
        line = store.checks.format_check_roll(res)
        if not any(m.get("speaker") == store.scenes.ROLL_SPEAKER and m["content"] == line
                   for m in _scene_messages(cid, sid)[res["line_intent"]:]):
            store.scenes.append_message(cid, sid, "assistant", line,
                                        speaker=store.scenes.ROLL_SPEAKER)
        return res
