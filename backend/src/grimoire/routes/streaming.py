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

A note for tests that patch the helpers below: importers bind them by value
(``from .streaming import _persist_reply``), so patch the module where the name
is *looked up*, not necessarily where it is defined. Patching
``routes.streaming._persist_reply`` covers the turn strategies here but not
``greetings.post_first_post``, which holds its own reference.
"""

from __future__ import annotations

import json

import anyio
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import store
from ..llm import LLMClient
from ..llm_errors import LLMError

# An SSE comment: framing every proxy on the path understands as traffic, and
# that `parseSSEChunk` already skips (it only reads `data:` lines), so the
# client needs no matching change to tolerate one. Emitted whenever the facade
# reports it is still waiting on the model (#95) -- before the first token, a
# generation is otherwise indistinguishable from a dead connection.
_HEARTBEAT = ": heartbeat\n\n"


async def _flush_on_abort(hook, watcher) -> None:
    """Persist a turn's partial output while the stream is being torn down.

    The disconnect path, not the error path: a client that navigates away or
    presses Cancel makes Starlette cancel the task driving this generator, and
    the resulting ``CancelledError``/``GeneratorExit`` is not an ``LLMError``,
    so the handler that persists partials never used to run and the text was
    dropped on the floor (#95).

    Shielded, because the cancellation is already in flight: inside a cancelled
    scope the next await would re-raise before the write could start. The wait
    is bounded by the store lock's own timeout rather than a second one here --
    ``run_in_threadpool`` is not cancellable, so a bound around it would only
    lie about when this returns.
    """
    if hook is None:
        return
    try:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(hook, watcher)
    except Exception:  # noqa: BLE001 - a failed rescue must not replace the teardown
        # Nothing left to tell anyone: the client is gone and the exception that
        # brought us here is about to be re-raised, so anything raised in here
        # would only mask it. Broad rather than `StoreBusy` alone (the error
        # path's narrower catch, #234) because this one runs during unwinding,
        # where the loop may be shutting down under it -- and losing a partial
        # is the outcome this path already accepts.
        pass


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
                  client: LLMClient, finalize, on_error=None, on_abort=None):
    """Stream one persisted turn while watching for a ```roll fence.

    Deltas are routed through a FenceWatcher, so an opener (even split across
    chunks) is never emitted and streaming stops once a fence closes. When the
    stream ends, `finalize(watcher)` (called with the lock/persist strategy of
    the caller — initial turn vs continuation) returns the trailing SSE frames
    (proposal / done). `on_error(watcher)` decides what to persist on an
    upstream LLM failure. Fence watching runs on persisted turns only;
    `_ephemeral_stream` is deliberately untouched.

    `on_abort(watcher)` is the same decision for a *disconnect* — the client
    cancelled, or the connection died — which arrives as cancellation rather
    than as an `LLMError` and so needs its own handler. It is deliberately a
    second hook and not a reuse of `on_error`: a hard failure and a deliberate
    cancel want different things done with a turn that produced nothing (a
    failure's orphaned user post is rolled back, a cancelled one is kept so the
    player can retry it), and only `on_error` is reached with a frame still to
    send.
    """
    async def event_stream():
        watcher = store.fence.FenceWatcher()
        try:
            async for delta in client.stream(messages, conn):
                if not delta:
                    yield _HEARTBEAT  # the facade is still waiting on the model
                    continue
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
                try:
                    await run_in_threadpool(on_error, watcher)
                except store.locks.StoreBusy:
                    # on_error persists the partial reply, which now takes a
                    # cross-process lock and can therefore raise (#234). The
                    # response has already started, so the global 409 handler
                    # cannot convert it -- letting it escape would truncate the
                    # stream with no error frame at all. The partial reply is
                    # lost; the frame below still tells the user why.
                    pass
            yield _sse({"error": {"detail": exc.detail, "kind": exc.kind}})
            return
        except BaseException:
            # Cancellation, `GeneratorExit`, or anything else that ends this
            # generator without going through the branch above. No frame can be
            # emitted -- the socket is gone and a generator being closed may not
            # yield -- so the only thing left to do is save the text, then let
            # the teardown continue: swallowing it here would tell Starlette the
            # response ended normally.
            watcher.finish()
            await _flush_on_abort(on_abort, watcher)
            raise
        try:
            # In a worker thread, not inline (#234). `finalize` is synchronous
            # and now waits on a cross-process lock, whose retry loop sleeps for
            # up to LOCK_TIMEOUT. This is an async generator driven by the event
            # loop, so an inline call would block that loop for the full 30s --
            # freezing every unrelated request and every other live stream on
            # this backend, not just the contended campaign.
            #
            # list(), not a bare call: finalize() runs OUTSIDE the try above,
            # and if it ever returns a generator, guarding only the call would
            # let StoreBusy escape from the `for` below -- outside this handler,
            # aborting the stream with no frame emitted at all. Materializing in
            # the worker also keeps any lazy body off the event loop.
            frames = await run_in_threadpool(lambda: list(finalize(watcher)))
        except store.locks.StoreBusy as exc:
            # Deliberately NOT routed through on_error: that persists
            # watcher.narration, and narration whose roll fence has no proposal
            # record destroys the proposal-before-narration guarantee this
            # ordering exists for.
            #
            # What this does NOT claim (review caught the overclaim): that
            # nothing was persisted. `finalize` is not transactional --
            # `_chat_stream` writes the proposal under one lock, releases it,
            # then `_persist_reply` takes it again, so contention on that second
            # acquisition leaves a proposal on disk with no narration. That is
            # the *sanctioned* direction: it is exactly the recoverable state
            # the documented fence crash-window already produces, and the
            # opposite order is the one that loses data. What is guaranteed is
            # that the busy path adds no narration without its proposal, and
            # that the stream ends with a frame saying so instead of dying (#234).
            yield _sse({"error": {"detail": str(exc), "kind": "busy"}})
            return
        for frame in frames:
            yield frame

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _chat_stream(cid: str, sid: str, messages: list[dict], conn: dict, client: LLMClient,
                 undo_user_post=None):
    """A normal persisted turn. A ```roll fence cuts the stream: the pending
    proposal record is written *before* the pre-fence narration persists, so a
    transcript that ends at a mechanical decision point always has a
    recoverable proposal (see the crash-window disclosure above).

    `undo_user_post` makes the turn transactional (#95). The caller appends the
    player's message before the stream starts, because the context builders
    render history out of the transcript and so need it already there; that
    leaves a window where an upstream failure strands a post with no reply and
    no way to tell it apart from one the model simply ignored. When a turn fails
    having produced *nothing*, this callback takes that post back off, so the
    scene ends up where it started. A turn that produced even a partial reply
    keeps both halves — the post is answered, just not fully — and a cancelled
    turn keeps its post too, since the player asked to stop and will likely
    retry from exactly there.
    """
    def finalize(watcher) -> list[str]:
        frames: list[str] = []
        if watcher.complete or watcher.truncated:
            with store.locks.campaign_lock(cid):
                payload = _make_proposal(cid, sid, watcher)
                rec = store.proposals.new(cid, sid, payload)  # heals before replacing
            _persist_reply(cid, sid, watcher.narration)
            frames.append(_sse({"proposal": {**payload, "id": rec["id"]}}))
        elif watcher.narration.strip():
            _persist_reply(cid, sid, watcher.narration)
        frames.append(_sse({"done": True}))
        return frames

    def on_error(watcher) -> None:
        if watcher.narration.strip():  # a normal turn keeps its partial reply
            _persist_reply(cid, sid, watcher.narration)
        elif undo_user_post is not None:
            undo_user_post()

    def on_abort(watcher) -> None:
        # The persist half of on_error and nothing else: see `undo_user_post`
        # above for why a cancel keeps the post a failure would have removed.
        if watcher.narration.strip():
            _persist_reply(cid, sid, watcher.narration)

    return _fence_stream(cid, sid, messages, conn, client, finalize, on_error, on_abort)


def _continuation_stream(cid: str, sid: str, pid: str, messages: list[dict],
                         conn: dict, client: LLMClient):
    """Stream a proposal's continuation and commit it atomically. A supersede
    that lands mid-stream makes ``commit_narration`` return False and the
    streamed text is dropped. A follow-up fence in the continuation hands off
    under one lock: commit the old record's narration, then mint the new
    pending record, then emit its proposal event."""
    def finalize(watcher) -> list[str]:
        frames: list[str] = []
        persist = lambda: _persist_reply(cid, sid, watcher.narration)
        if watcher.complete or watcher.truncated:
            with store.locks.campaign_lock(cid):
                if store.proposals.commit_narration(cid, sid, pid, persist):
                    payload = _make_proposal(cid, sid, watcher)
                    # new() heals the record it is about to erase; the lock is
                    # reentrant, so that projection is safe under ours
                    rec = store.proposals.new(cid, sid, payload)
                    frames.append(_sse({"proposal": {**payload, "id": rec["id"]}}))
        else:
            store.proposals.commit_narration(cid, sid, pid, persist)
        frames.append(_sse({"done": True}))
        return frames

    # No on_error, and no on_abort either: an upstream failure or a disconnect
    # mid-continuation drops the partial (nothing persisted) and leaves the
    # record resolved/declined, so a retry re-streams a fresh continuation
    # cleanly. Persisting here would be worse than losing the text — narration
    # committed outside `commit_narration` is narration a supersede can no
    # longer displace.
    return _fence_stream(cid, sid, messages, conn, client, finalize)


def _ephemeral_stream(messages: list[dict], conn: dict, client: LLMClient):
    """Stream a generation without persisting it to any scene (used by the opener)."""
    async def event_stream():
        try:
            async for delta in client.stream(messages, conn):
                if not delta:
                    yield _HEARTBEAT  # still waiting on the model (#95)
                    continue
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except LLMError as exc:
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- roll-proposal derivation and projection ----
# Used by the turn strategies above and by the roll-proposal routes in
# `mechanics`, which is why they live here rather than beside those routes.
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


