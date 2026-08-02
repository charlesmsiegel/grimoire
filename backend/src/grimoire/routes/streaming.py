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

import itertools
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

# Which turn each scene currently belongs to, newest claim wins. A cancelled
# turn's flush runs after its socket has closed, so it has to be able to ask
# whether it is still the turn the scene belongs to before writing anything.
#
# Transcript length cannot answer that, which review caught after it was first
# used for exactly this: three of the four `_chat_stream` callers -- retry,
# regenerate, and the director-note/empty send -- append nothing of their own,
# so two overlapping ones capture an identical length and the older one's abort
# would sail through the check and persist into the newer turn.
#
# In-process only, and that is the right scope: it distinguishes two turns
# racing inside one backend, which is what a player generates. Two *processes*
# on one scene is the case `store/locks.py` already documents as beyond what
# this app can serialize. Entries are overwritten per turn and never removed --
# one small entry per scene ever streamed, which is not worth reaping.
_turn_tokens: dict[tuple[str, str], int] = {}
_turn_seq = itertools.count(1)


def _claim_turn(cid: str, sid: str) -> int:
    """Make this the scene's current turn and return the token proving it."""
    token = next(_turn_seq)   # atomic under the GIL; claims come from request threads
    _turn_tokens[(cid, sid)] = token
    return token


def _owns_turn(cid: str, sid: str, token: int) -> bool:
    return _turn_tokens.get((cid, sid)) == token


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
    third hook and not a reuse of `on_error`: a hard failure and a deliberate
    cancel want different things done with a turn that produced nothing (a
    failure's orphaned user post is rolled back, a cancelled one is kept so the
    player can retry it), and only `on_error` is reached with a frame still to
    send. Callers whose abort case wants the ordinary end-of-turn writes pass
    `finalize` itself here and let its frames fall on the floor.
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
            note: dict = {}
            if on_error is not None:
                try:
                    note = await run_in_threadpool(on_error, watcher) or {}
                except (store.locks.StoreBusy, store.scenes.SceneNotFound):
                    # on_error writes to the scene -- it persists the partial
                    # reply, and now may also take the user post back off (#95)
                    # -- so it can fail two ways. StoreBusy: the cross-process
                    # lock is contended (#234). SceneNotFound: the scene was
                    # renamed mid-turn, which mints a new id and moves the file
                    # out from under both calls (review caught this on the
                    # rollback; the persist has always had it).
                    #
                    # Neither may escape. The response has already started, so
                    # the global 409 handler cannot convert it, and an exception
                    # here would end the generator with no frame at all --
                    # truncating the stream instead of reporting the upstream
                    # failure that brought us here. The write is lost; the frame
                    # below still tells the user why.
                    pass
            # `note` carries what the handler *did*, not what went wrong: today
            # just `post_returned`, so the client can put the player's words
            # back in the composer instead of losing them (#95). Reported rather
            # than recomputed on the client, because only this side knows
            # whether the rollback actually fired -- it declines when the post
            # is no longer the tail, and a client guessing "failed with no text
            # ⇒ rolled back" would restore a prompt that is still in the
            # transcript and have the player send it twice.
            yield _sse({"error": {"detail": exc.detail, "kind": exc.kind, **note}})
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
    # What the abort path checks before writing (see `on_abort`): this turn's
    # claim on the scene, and how long the transcript was when it began. Both,
    # because they catch different intruders — the token catches another turn,
    # including the three kinds that append nothing and so leave the length
    # unchanged; the length catches everything that is not a turn at all, like a
    # manual roll or a transition line. Read here, while the caller is still
    # synchronous and nothing else can be mid-write.
    turn_token = _claim_turn(cid, sid)
    owned_tail = len(store.scenes.read_scene(cid, sid)["messages"])

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

    def on_error(watcher) -> dict:
        """What to do with a turn the provider failed, and what to tell the
        client about it.

        The rollback is gated on this turn still holding the scene's claim, for
        the reason `on_abort` is: an overlapping retry or director note appends
        nothing, so index-and-tail still match A's post while B is generating
        from it, and the undo would delete the very post B is answering. Failing
        the check leaves an orphan, which is the direction this is allowed to be
        wrong in.

        Gating only the *rollback*, not the partial-reply persist beside it. The
        persist is additive and predates this branch; dropping it under
        contention would be a new way to lose text, decided as a rider on a fix
        for a different problem. Deleting is what needs the discipline.
        """
        if watcher.narration.strip():  # a normal turn keeps its partial reply
            _persist_reply(cid, sid, watcher.narration)
        elif undo_user_post is not None and _owns_turn(cid, sid, turn_token):
            if undo_user_post():
                return {"post_returned": True}
        return {}

    def on_abort(watcher) -> list[str]:
        """Finish a cancelled turn exactly as a completed one — but only while
        it still owns the scene's tail.

        `finalize` itself, frames and all: they are discarded, since the socket
        that would have carried them is gone. Review caught the version that
        only persisted narration — a fence can close in the same chunk that
        carries the pre-fence text, so `watcher.complete` is already true at the
        yield a disconnect lands on, and persisting only the narration would end
        the transcript at a mechanical decision whose proposal record was never
        written: the check silently lost, and proposal-before-narration broken
        from the one direction the StoreBusy path takes such care to avoid.

        The ownership checks are the second half of the same review, in two
        rounds. Stop-then-send is a natural sequence, and this teardown runs
        after the socket closes — so the new turn can already be streaming, and
        may have appended its user post. Persisting then would file the
        cancelled turn's narration under a question it never answered, and a
        closed fence would mint a proposal displacing the live one.

        So: this turn must still hold the scene's claim (`_turn_tokens`), and
        the transcript must still be the length it was when this turn began. The
        token is what catches an overlapping retry or director note, neither of
        which moves the length; the length is what catches a writer that is not
        a turn at all, like a manual roll or a transition line from the
        inspector. Failing either, the partial is dropped — the same trade
        `_continuation_stream` makes, for the same reason.

        Under the campaign lock so the checks and the writes cannot be split.
        The lock is reentrant, so `finalize` re-taking it is free.
        """
        with store.locks.campaign_lock(cid):
            if not _owns_turn(cid, sid, turn_token):
                return []
            if len(store.scenes.read_scene(cid, sid)["messages"]) != owned_tail:
                return []
            return finalize(watcher)

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


