"""The inference operations a call site asks for by name: `generate` (spec 7.2)
and `decide` (spec 7.4).

A generation is free text, streamed or joined. `generate` is the only door
to it: it takes the resolution a call site already made (`require_inference`
or `override_inference`), refuses one made for another task or operation, or
one that resolved nothing, before any client call, and hands the facade
the call's chain (`call_chain`): `resolved.chain`, the resolution's typed
targets, with -- for a call that asks for JSON (`schema=`, 01f) -- each
capable attempt flagged for its provider's structured mode on a new target.
A refused field is re-sent without the mode through the helper `decide`
shares (`_without_refused_mode`). `test_routing_guard.py`
holds that `client.stream`/`complete` are spelled only in this module; the
caller's meter, and the holder it hands in as `usage=`, stay at the call
site, where `test_usage_guard.py` reads them.

A decision is a closed question answered from a set the caller fixed in
advance (`grimoire.decisions` holds that contract). `decide` is the operation:
it takes a resolution the call site already made -- `require_inference(task,
cid, operation="decide")`, so the seam's 409 and absorb's soft resolution come
before it -- and answers through a chain of stages (`stages`, spec 5.5): the
selection's one backend, then the role fallback's in one attempt. Two
backends: `structured` (slice F), `generate` (`LLMClient.complete(...,
schema=)`) over a prompt that carries the schema, read back by
`decisions.parse`; and `native` (slice H), a provider's own decisions endpoint
(`LLMClient.decide_native`), one request per item. An item moves to the next
stage only when its stage FAILED to answer it (`run_stages`).

Four rules `decide` keeps:

- **One meter per call, opened here** (ruling 9): per structured chunk, and
  per native item. A caller's time budget runs inside it through `around`, so
  an overrun is filed as an `error/timeout` row exactly as a budgeted
  `complete` is today, and `usage.Meter.done` stays the one place an LLM
  failure is recorded. `test_usage_guard.py` scans this file. A caller's
  capture (`Capture`) is handed each call once it settles, outside its
  meter and guarded, so a capture never turns an answered call into an
  `error` row.
- **Nothing is written into the holder.** The operation and the decision mode
  reach the ledger through each target's account (`wire.Account`), which
  `llm._stamp` files: the operation from the resolution, the mode stamped here,
  per call, on new targets (`_with_mode`, `wire.Chain.with_account`) -- a
  resolution's targets are frozen, and `stages` builds the chains it sends
  (`wire.Chain.alone`) rather than changing the resolution's.
- **Provider errors propagate.** A failed call's `LLMError` is re-raised when
  no item answered, composed with the later stages' failures
  (`llm.routes_failed`); once one has, a failed unit's items are `None` with
  reason `error`, and `Decision.errors` holds each unit's final error.
  Cancellation passes through untouched (every open meter files `aborted`).
  One error is not yet the chunk's: a chain that failed on every route, one
  of them by refusing the structured field (`llm.SchemaRefusalError`), is
  answered by sending each refusing attempt once more without the mode, as
  its own metered call.
- **Prompt text lives in `templates/decide/`**, rendered here -- in a worker
  thread, never on the event loop. Jinja's loader stats (and on first use
  reads) the template files, and `decide` is awaited by a detached turn (the
  speaker pick), whose loop must not block on file I/O; the render ran in the
  threadpool before the switch, and it still does. A native item's capture
  (its request body, JSON-encoded) is built in a worker thread too.

Nothing in `store/` or the gateway imports this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal, NamedTuple, TypeVar, overload

from . import decisions, llm, llm_errors, model_guidance, prompts, schemas, store, wire
from .llm import LLMClient
from .llm_errors import LLMError
from .store.inference import resolve
from .store.inference.resolved import ResolvedInference

log = logging.getLogger(__name__)

T = TypeVar("T")

STRUCTURED = decisions.STRUCTURED_BACKEND
NATIVE = decisions.NATIVE_BACKEND

#: How many native requests one stage keeps in flight: one request per item,
#: at most this many at once. Argued structurally: a continuity sweep sends
#: one item per row, and an unbounded fan-out would meet the provider's rate
#: limit as a burst of 429s that the retry budget then pays for. To be tuned
#: against real prompts later.
NATIVE_CONCURRENCY = 4

#: How many native items in a row may time out before the stage starts no
#: more (`_native`): one full wave. A timeout is not `_connection_wide` --
#: one slow item says nothing of the next, and the facade does not retry it
#: -- but a decisions endpoint that accepts connections and never answers
#: would otherwise cost every item the whole ceiling, `NATIVE_CONCURRENCY`
#: at a time: a continuity sweep's ceil(n / NATIVE_CONCURRENCY) full
#: ceilings for nothing. Any answer, or any other failure, resets the count.
#: Stage-local: a hung decisions endpoint is no reason to skip a generating
#: stage on the same connection (`run_stages`).
NATIVE_TIMEOUT_STOP = NATIVE_CONCURRENCY

#: Given the facade call and the meter's live holder, what to await instead --
#: how a caller's time budget runs inside the meter `decide` opens (I2). The
#: call yields the reply text on a structured stage and an `ItemResult` on a
#: native one; the callers' hooks (`budget.run`, `_bounded_call`) are generic.
Around = Callable[[Awaitable[Any], dict], Awaitable[Any]]

#: Handed each call once it settles (spec 9.4), answered or failed with an
#: `LLMError` -- never a cancelled one: `(messages, outcome, target)`. The
#: messages are the request as sent (a structured chunk's
#: `structured_messages`; a native item's normalised body, `llm.native_body`,
#: as one user message), and `[]` for a call refused before anything went
#: out; the outcome is `decisions.outcome`'s record of it; the target is the
#: one the attempt that answered was sent (`llm.ATTEMPTED`, a fallback's or a
#: prompt-only re-send's included), or the stage's account-stamped primary
#: when the call failed -- a native stage's `without_sampling`, as it was
#: sent. One structured chunk is one call, its prompt-only re-send included;
#: each native item is one call.
Capture = Callable[[list[dict], dict, wire.Target], Awaitable[None]]


def structured_messages(items: Sequence[decisions.Item], *,
                        explain: str = "") -> list[dict]:
    """The structured backend's prompt for one chunk: the system message
    carries the batch's JSON Schema (ruling 3: always, so a fallback without
    structured mode answers from the same prompt), the user message each
    item's context and questions, and `explain`, the rationale instruction."""
    wanted = bool(explain)
    return [
        {"role": "system",
         "content": prompts.render("decide/system.j2",
                                   schema=decisions.schema(items, explain=wanted),
                                   explain=wanted)},
        {"role": "user",
         "content": prompts.render("decide/user.j2", items=items, explain=explain)}]


class Stage(NamedTuple):
    """One stage of a decide chain: the backend (`mode`, a key of
    `_BACKENDS`), the chain it sends -- a primary, and the fallback the
    facade fails over to when one rides it -- and the primary route's retry
    count: None for the facade's own budget (the primary's `llm_retries`),
    0 on a fallback stage, which gets one attempt (spec 5.4, I3)."""

    mode: str
    chain: wire.Chain
    retries: int | None


def stages(resolved: ResolvedInference) -> tuple[Stage, ...]:
    """The decide chain of `resolved` (spec 5.5): the selection's one backend,
    then -- when it is a stage of its own -- the role fallback's, in one
    attempt. Pure: nothing in `resolved` is changed.

    The primary is served natively when its `decision_mode` says so, and
    structured when it `generates` (`resolve.generates`); its structured
    stage sends the resolution's chain as it is, so a structured fallback the
    resolver attached rides the facade, exactly as slice F sends it. The
    fallback is a stage of its own only when either attempt is native (a
    structured stage cannot carry a native attempt behind it, nor a native
    one anything), and then the primary is sent alone (`wire.Chain.alone`).
    A fallback known incapable (`fallback_missing`) is never a stage (spec
    5.3).

    No structured stage follows a native one on the same selection: a native
    attempt is one that cannot generate (ruling 1, C1), so that stage could
    never answer. Trying native first on a model that also generates is a
    later user decision (spec 16), which would add it back here."""
    whole = resolved.chain
    if whole is None:
        return ()
    primary = resolved.attempts[0]
    fallback = (resolved.attempts[1] if len(resolved.attempts) > 1
                and not resolved.fallback_missing else None)
    apart = (fallback is not None and fallback.decision_mode in decisions.BACKENDS
             and NATIVE in (primary.decision_mode, fallback.decision_mode))
    chain: list[Stage] = []
    if primary.decision_mode == NATIVE:
        chain.append(Stage(NATIVE, whole.alone(), None))
    elif resolve.generates(primary):
        chain.append(Stage(STRUCTURED, whole.alone() if apart else whole, None))
    if apart and fallback is not None:
        chain.append(Stage(fallback.decision_mode, wire.Chain(fallback.target), 0))
    return tuple(chain)


@dataclass(frozen=True)
class _Call:
    """The keyword arguments `decide` received, handed to a backend whole,
    with the stage's own chain and retry count (`Stage`)."""

    task: str
    client: LLMClient
    explain: str
    campaign: str
    scene: str
    post: int | None
    round_id: str
    capture: Capture | None
    around: Around | None
    chain: wire.Chain
    retries: int | None


class _Answered(NamedTuple):
    """What one stage made of the items it was given, by position: each
    item's result (None for an item its stage failed to answer), each failed
    unit -- a chunk on a structured stage, an item on a native one -- as its
    items' positions and its final error, in order, and the stage's ledger
    rows and what answered (`decisions.Decision.served`). Every unanswered
    item is in exactly one failed unit. `stopped` is the failure that made
    the stage start nothing more, or None -- `run_stages` reads it to skip a
    later stage that would only meet it again."""

    results: tuple[decisions.ItemResult | None, ...]
    failed: tuple[tuple[tuple[int, ...], LLMError], ...]
    rows: tuple[dict, ...]
    served: tuple[tuple[str, str], ...]
    stopped: LLMError | None = None


#: A backend: one stage's attempt at every item it is given.
Backend = Callable[[tuple[decisions.Item, ...], _Call], Awaitable[_Answered]]


def _with_mode(chain: wire.Chain, mode: str) -> wire.Chain:
    """`chain` with `decision_mode` laid over the account of each target on
    it: new targets throughout (I7), so the resolution's are left as they
    were."""
    return chain.with_account(decision_mode=mode)


def _without_mode(attempt: wire.Target) -> wire.Chain:
    """The attempt a schema refusal hands back (`llm.SchemaRefusalError`), as
    the same attempt without structured mode, and alone: no fallback behind
    it (the re-send is that one attempt's, and every other route has already
    had its turn)."""
    return wire.Chain(replace(attempt, structured=False))


class _Reply(NamedTuple):
    """One chunk's last word: the reply text and the holder that answered,
    or the error -- and whether any request went out for it (`sent`: a
    holder the facade stamped, the same test `store.usage.Meter` files a row
    by)."""

    text: str
    holder: dict | None
    error: LLMError | None
    sent: bool


def _served_by(holder: dict) -> tuple[str, str]:
    """`(provider id, effective model)` of the attempt that answered, read from
    the target the facade stamped into `holder` (`llm.ATTEMPTED`)."""
    target = holder.get(llm.ATTEMPTED)
    if isinstance(target, wire.Target):
        return target.provider_id, target.model
    return "", ""


async def _once(call: _Call, sending: wire.Chain, messages: list[dict], schema: dict,
                rows: list[dict]) -> _Reply:
    """One metered facade call: its text and the answering holder, or its
    error. Its ledger row, when the meter filed one, is appended to
    `rows`."""
    # Named `client`, the receiver `test_usage_guard.py` recognises.
    client = call.client
    m = store.usage.meter(call.task, campaign=call.campaign, scene=call.scene,
                          post=call.post, round_id=call.round_id)
    error: LLMError | None = None
    text = ""
    try:
        with m:
            # The retry count only when the stage names one (a fallback
            # stage's 0): every primary stage calls `complete` exactly as
            # slice F did, so a fake written before `retries=` still fits.
            pending = (client.complete(messages, sending, m.usage, schema=schema)
                       if call.retries is None else
                       client.complete(messages, sending, m.usage, schema=schema,
                                       retries=call.retries))
            text = await (call.around(pending, m.usage) if call.around else pending)
    except LLMError as exc:
        error = exc
    if m.row is not None:
        rows.append(m.row)
    sent = bool(m.usage)
    return (_Reply(text, m.usage, None, sent) if error is None
            else _Reply("", None, error, sent))


def _refused(error: llm.SchemaRefusalError) -> list[tuple[int, wire.Chain]]:
    """The attempts `error` names as having refused the structured field, by
    route position, each as the chain its re-send is sent (`_without_mode`):
    in route order, the primary first."""
    return [(index, _without_mode(attempt)) for index, attempt in enumerate(error.attempts)
            if isinstance(attempt, wire.Target)]


async def _without_refused_mode(error: llm.SchemaRefusalError,
                                send: Callable[[wire.Chain], Awaitable[T]]) -> T:
    """Answer a call that failed on every route, one of them by refusing the
    structured field (01f-C2, spec M-4): each refusing attempt once more,
    alone and without the mode, in route order, through `send`; the first
    that answers wins. The schema is in the prompt, so the reply still
    parses. Should every re-send fail, `llm.routes_failed(words)` with each
    re-sent route's word replaced by its re-send's failure.

    `decide` (`_ask`) and `generate` share it: what a re-send IS -- its own
    metered call, or one more call through the caller's holder -- is
    `send`'s."""
    words = list(error.words)
    for index, again in _refused(error):
        try:
            return await send(again)
        except LLMError as exc:
            words[index] = exc
    raise llm.routes_failed(words)


async def _stream_without_refused_mode(
        error: llm.SchemaRefusalError,
        open_stream: Callable[[wire.Chain], AsyncIterator[str]]) -> AsyncIterator[str]:
    """`_without_refused_mode`'s streamed twin: the same attempts in the same
    order and the same composed error, but a re-send's failure is found while
    iterating it. A re-send that fails before any text moves on to the next;
    one that fails after text reached the caller raises as it is -- that text
    cannot be taken back, as `llm._resilient` will not retry past it either."""
    words = list(error.words)
    for index, again in _refused(error):
        stream = open_stream(again)
        yielded = False
        try:
            async for chunk in stream:
                yielded = yielded or bool(chunk)
                yield chunk
            return
        except LLMError as exc:
            if yielded:
                raise
            words[index] = exc
        finally:
            await _aclose(stream)
    raise llm.routes_failed(words)


async def _aclose(stream: object) -> None:
    """Close `stream` when it can be closed (an async generator)."""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


async def _ask(call: _Call, chain: wire.Chain, messages: list[dict], schema: dict,
               rows: list[dict]) -> _Reply:
    """One chunk's call: its text and the answering holder, or its error,
    and whether any of the calls it made went out. Each call's ledger row is
    appended to `rows`.

    The attempt chain, and -- only when every route failed and a route's
    failure was its provider refusing the structured field
    (`llm.SchemaRefusalError`) -- each such attempt once more, alone and
    without the mode (`_without_refused_mode`, spec M-4, ruling 3): the
    schema is in the prompt, so the reply still parses. A primary that
    refused is re-sent after its fallback failed too; a fallback that
    refused, after the primary failed for any reason. Each re-send is its own
    metered call, and none is re-sent twice. Should those fail too, the error
    is the routes' failures composed afresh (`llm.routes_failed`), each
    re-sent route's word now its own failure."""
    first = await _once(call, chain, messages, schema, rows)
    error = first.error
    if not isinstance(error, llm.SchemaRefusalError) or not error.attempts:
        return first
    sent = [first.sent]

    async def send(again: wire.Chain) -> _Reply:
        reply = await _once(call, again, messages, schema, rows)
        sent[0] = sent[0] or reply.sent
        if reply.error is not None:
            raise reply.error
        return reply

    try:
        answered = await _without_refused_mode(error, send)
    except LLMError as exc:
        return _Reply("", None, exc, sent[0])
    return answered._replace(sent=sent[0])


def _outcome(mode: str, target: wire.Target, holder: dict | None,
             results: Sequence[decisions.ItemResult], error: LLMError | None) -> dict:
    """A settled call's record for the capture (`decisions.outcome`): what
    answered it and its results, or -- failed -- the primary its stage sent
    (`target`) and its error."""
    if error is not None:
        return decisions.outcome(mode, target.provider_id, target.model,
                                 error=f"{error.kind}: {error.detail}")
    provider, model = _served_by(holder or {})
    return decisions.outcome(mode, provider, model, results)


async def _captured(call: _Call, messages: list[dict] | Callable[[], list[dict]],
                    outcome: Callable[[], dict], target: wire.Target) -> None:
    """Hand one settled call to `call.capture` (spec 9.4): `messages` as
    they are, or -- a callable -- built in a worker thread; `outcome` built
    here. Called outside the call's meter, and guarded as `llm._observe` is:
    a capture that raises costs the capture and nothing else (I4), never an
    answered decision, and never turns its `ok` row into an error."""
    if call.capture is None:
        return
    try:
        sent = await asyncio.to_thread(messages) if callable(messages) else messages
        await call.capture(sent, outcome(), target)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("could not capture a %s decision: %s", call.task, exc)


async def _structured(items: tuple[decisions.Item, ...], call: _Call) -> _Answered:
    """The structured backend: one metered `complete(schema=)` per chunk
    (`decisions.chunks`), each read back by `decisions.parse` and stamped
    `backend="structured"`. Each chunk runs down the attempt chain on its
    own, so what answered is collected per chunk, never read off the last
    one, and so is each failed chunk's own error: the chunk's final word,
    after `_ask`'s re-sends, never one of the calls it made on the way
    there. After a chunk fails with what every later chunk would meet
    (`_stops_chunks`), no later chunk is sent: each stays pending, carrying
    that failure, for the next stage or for `error`."""
    chain = _with_mode(call.chain, STRUCTURED)
    explain = bool(call.explain)
    results: list[decisions.ItemResult | None] = [None] * len(items)
    rows: list[dict] = []
    failed: list[tuple[tuple[int, ...], LLMError]] = []
    served: list[tuple[str, str]] = []
    stopped: LLMError | None = None
    for offset, chunk in decisions.chunks(items):
        unit = tuple(range(offset, offset + len(chunk)))
        if stopped is not None:
            # A failure every later chunk would meet: never sent, no row, and
            # pending for the next stage with the failure that held it back.
            failed.append((unit, stopped))
            continue
        # Off the loop: the template loader touches the filesystem.
        messages = await asyncio.to_thread(structured_messages, chunk, explain=call.explain)
        schema = decisions.schema(chunk, explain=explain)
        text, holder, error, sent = await _ask(call, chain, messages, schema, rows)
        answered: list[decisions.ItemResult] = []
        if holder is not None:
            answered = [replace(result, backend=STRUCTURED)
                        for result in decisions.parse(text, chunk, explain=explain)]
            for index, result in enumerate(answered):
                results[offset + index] = result
            if (by := _served_by(holder)) not in served:
                served.append(by)
        # Settled: what went out, if anything did (`Capture`), on the target
        # the facade sent for the attempt that answered (`llm.ATTEMPTED`: a
        # fallback, or a re-send without the mode, names itself, with the
        # preset it was really sent); a failed chunk names its stage's
        # primary.
        ran = holder.get(llm.ATTEMPTED) if holder is not None else None
        await _captured(call, messages if sent else [],
                        partial(_outcome, STRUCTURED, chain.primary, holder, answered, error),
                        ran if isinstance(ran, wire.Target) else chain.primary)
        if error is not None:
            # Filed by the meter already; the chunk's fate waits on the chain.
            failed.append((unit, error))
            if _stops_chunks(error, chain):
                stopped = error
    return _Answered(tuple(results), tuple(failed), tuple(rows), tuple(served), stopped)


def _connection_wide(exc: LLMError) -> bool:
    """Whether every other item on the connection `exc` came from would meet
    it too, so a native stage starts no further item (G's final review): the
    key refused or missing, a rate limit the facade has already retried as
    far as it would (`decide_native` raises one only then, or when the
    provider's window is past `llm.RETRY_AFTER_CAP`), the account refused for
    money (`llm_errors.account_limit`), or a call the caller's own clock
    refused unsent (absorb's `BudgetRefused`, which declares itself no
    failure: `store.usage.NOT_A_FAILURE`).

    A failure composed from several routes' (`LLMError.words`: a primary and
    the fallback the facade sent, or a structured chunk's prompt-only
    re-sends) is connection-wide only when every route's own failure is: one
    route that failed for a reason of its own may serve the next call.

    A `timeout` is not one, whether the provider's read bound or the
    caller's ceiling (`around`) raised it: one slow item says nothing of the
    next, and a chat endpoint on the same connection is not the decisions
    endpoint that hung. A native stage stops on a run of them instead
    (`NATIVE_TIMEOUT_STOP`), and only itself. The absorb clock's own overrun
    is the clock's, and its next call is refused unsent, which is."""
    if exc.words:
        return all(_connection_wide(word) for word in exc.words)
    return (exc.kind in ("auth", "missing_key", "rate_limit")
            or llm_errors.account_limit(exc)
            or _refused_unsent(exc))


def _refused_unsent(exc: LLMError) -> bool:
    """Whether `exc` is a call the caller's own clock refused before it was
    sent (absorb's `BudgetRefused`: `store.usage.NOT_A_FAILURE` is False)."""
    return getattr(exc, store.usage.NOT_A_FAILURE, True) is False


def _stops_chunks(error: LLMError, chain: wire.Chain) -> bool:
    """Whether a structured chunk's `error` means no later chunk on the stage
    is sent (`_connection_wide`). A chunk sent with a fallback behind it
    (`chain.fallback`) stops the stage only when its error names both
    routes (`words`): a bare error from such a call is one the facade did not
    fall back on -- the fallback dropped as the primary's own route, or a
    double that serves one attempt -- so nothing says the fallback would
    fail the next chunk. The caller's clock refusing a call unsent stops the
    stage whatever stood behind it."""
    if _refused_unsent(error):
        return True
    if chain.fallback is not None and not error.words:
        return False
    return _connection_wide(error)


async def _native(items: tuple[decisions.Item, ...], call: _Call) -> _Answered:
    """The native backend: one metered `decide_native` per item, at most
    `NATIVE_CONCURRENCY` in flight, all in one `asyncio.TaskGroup` (I5).

    Each item catches only `LLMError`, which leaves that item unanswered with
    its error; anything else propagates and the group cancels the other
    items, and each open meter files `aborted` -- as a cancel does. No
    request the group owns outlives the batch; an `around` that detaches its
    call into a task of its own (`routes.common._bounded_call`) abandons
    that task when cancelled rather than awaiting it, so its request may
    unwind after the batch has returned -- `_bounded_call`'s documented
    trade, and its meter still files `aborted`. Once an item fails with an
    error every other item would meet (`_connection_wide`), or once
    `NATIVE_TIMEOUT_STOP` items in a row have timed out, no further item is
    started: those in flight finish, and the rest are never sent, file no
    row, and leave the stage carrying the failure that stopped them, for the
    next stage to take up."""
    # The mode stamped on new targets (`with_account`), and the stage's
    # primary without the sampler preset a native call never sends (M4):
    # what the capture names, and what is sent, so the ledger row files none
    # (`llm_usage.account`).
    named = _with_mode(call.chain, NATIVE).primary.without_sampling()
    # Named `client`, the receiver `test_usage_guard.py` recognises.
    client = call.client
    gate = asyncio.Semaphore(NATIVE_CONCURRENCY)
    results: list[decisions.ItemResult | None] = [None] * len(items)
    errors: list[LLMError | None] = [None] * len(items)
    rows: list[dict | None] = [None] * len(items)
    holders: list[dict | None] = [None] * len(items)
    started = [False] * len(items)
    stopped: list[LLMError] = []
    timeouts = [0]      # how many items in a row have timed out

    async def one(index: int, item: decisions.Item) -> None:
        async with gate:
            if stopped:
                return
            m = store.usage.meter(call.task, campaign=call.campaign, scene=call.scene,
                                  post=call.post, round_id=call.round_id)
            started[index] = True
            try:
                with m:
                    pending = client.decide_native(item, named, m.usage,
                                                   retries=call.retries)
                    results[index] = await (call.around(pending, m.usage) if call.around
                                            else pending)
                    holders[index] = m.usage
                timeouts[0] = 0
            except LLMError as exc:
                # Filed by the meter already; the item's fate waits on the chain.
                errors[index] = exc
                timeouts[0] = timeouts[0] + 1 if exc.kind == "timeout" else 0
                if not stopped and (_connection_wide(exc)
                                    or timeouts[0] >= NATIVE_TIMEOUT_STOP):
                    stopped.append(exc)
            finally:
                rows[index] = m.row
        # Settled, and out of the gate (a cancel never reaches here): the
        # request as sent, built off the loop -- or none, when the call was
        # refused before it went out (`Capture`).
        answered = results[index]
        await _captured(
            call, partial(_native_request, item, named) if m.usage else [],
            partial(_outcome, NATIVE, named, holders[index],
                    () if answered is None else (answered,), errors[index]),
            named)

    try:
        async with asyncio.TaskGroup() as group:
            for index, item in enumerate(items):
                group.create_task(one(index, item))
    except BaseExceptionGroup as grouped:
        # One item's unexpected exception (the group has cancelled the rest):
        # raised as itself, so a caller's `except` sees what it would have
        # seen from a single call -- an `Abandoned` stays an `Abandoned`.
        raise grouped.exceptions[0] from None
    if any(s and r is None and e is None for s, r, e in zip(started, results, errors,
                                                           strict=True)):
        # An item that raised `CancelledError` of its own: its task ended
        # cancelled, which a TaskGroup does not propagate. Cancellation
        # passes through untouched, as it would from a single call.
        raise asyncio.CancelledError()
    # An item never started carries the failure that stopped it.
    failed = tuple(((index,), errors[index] or stopped[0])
                   for index, result in enumerate(results) if result is None)
    served = tuple(dict.fromkeys(_served_by(h) for h in holders if h is not None))
    return _Answered(tuple(results), failed,
                     tuple(row for row in rows if row is not None), served,
                     stopped[0] if stopped else None)


def _native_request(item: decisions.Item, target: wire.Target) -> list[dict]:
    """A native call's request as the capture records it: the normalised
    body (`llm.native_body`: the model, the context, the questions; no key or
    URL) as one user message. Not `decide/user.j2`'s prose, which the model
    never saw."""
    return [{"role": "user",
             "content": json.dumps(llm.native_body(item, target), indent=2,
                                   ensure_ascii=False)}]


def _final(words: Sequence[LLMError]) -> LLMError:
    """One unit's final error from its failure on each stage it reached:
    `llm.routes_failed`'s composition -- except when every one of them is the
    caller's clock refusing the call unsent (`_refused_unsent`), which is the
    first of them as it is. A composed error is a plain `LLMError`, and a
    phase that tells "never sent" from "failed" by type (absorb's `except
    BudgetRefused`) would read the clock's refusal as a provider failure."""
    if all(_refused_unsent(word) for word in words):
        return words[0]
    return llm.routes_failed(words)


def _same_connection(provider_id: str, other: str) -> bool:
    """Whether two stages send to the same connection, by provider id --
    `llm._same_route`'s rule: an empty id never matches."""
    return bool(provider_id) and provider_id == other


def _settle(got: _Answered, pending: list[int], results: list[decisions.ItemResult | None],
            words: dict[int, list[LLMError]]) -> list[tuple[tuple[int, ...], LLMError]]:
    """Lay one stage's answers over `results` (`pending` maps the stage's
    positions back to the batch's), add each failed unit's error to its
    items' `words`, and return the failed units in batch indices."""
    for position, result in enumerate(got.results):
        if result is not None:
            results[pending[position]] = result
    failed = [(tuple(pending[p] for p in unit), error) for unit, error in got.failed]
    for unit, error in failed:
        for index in unit:
            words.setdefault(index, []).append(error)
    return failed


#: Every backend a decide stage can dispatch to, by `Stage.mode`. Each fills
#: `_Answered.failed` with every unit whose items it leaves unanswered:
#: `run_stages` builds `Decision.errors` from it, and
#: `routes.common._decide_error` reports a batch's failure from the first, so
#: a unit left out would lose a rate limit's kind and window (spec 7.4,
#: "Result").
_BACKENDS: dict[str, Backend] = {STRUCTURED: _structured, NATIVE: _native}


async def run_stages(task: str, items: Sequence[decisions.Item], chain: Sequence[Stage], *,
                     client: LLMClient, explain: str = "",
                     campaign: str = "", scene: str = "", post: int | None = None,
                     round_id: str = "", capture: Capture | None = None,
                     around: Around | None = None) -> decisions.Decision:
    """Answer `items` down `chain`, stage by stage (spec 5.5): each stage runs
    the items still pending through its own backend, and an item moves on
    only when its stage FAILED to answer it -- an answer, a `refused` or a
    `None` from a well-formed reply included, is final.

    An invalid request is a `decisions.DecideRequestError`, and an empty
    chain a `ValueError`, both before any meter opens. A
    `llm.PresetRefusalError` ends the chain where it is met, as it stops the
    facade (ruling 7): the preset is the user's to fix, and a native stage
    that takes no sampling would only hide it. So does the caller's clock
    refusing a call unsent (`_refused_unsent`, absorb's `BudgetRefused`):
    it refuses every later call too, so a later stage could only add another
    refusal, and the one it has keeps its type. A stage that stopped on a
    failure every call on its connection would meet (`_connection_wide`:
    the key, the account's money, an exhausted rate limit) skips each later
    stage on that SAME connection (`_same_connection`): its items keep the
    failures they have, as items held back inside a stage do.

    When no item answered, the first failed unit's final error is raised
    (F's ruling 8): its failures on every stage it reached, composed
    (`llm.routes_failed`) -- the first stage's kind and window, the later
    stages' failures named after it (M3), and each kept in `words` -- or,
    when every one of them is the clock's refusal, the first of them as it
    is (`_final`), so a caller's `except BudgetRefused` still sees it.
    Otherwise each unanswered item is `None` with reason `error`, and
    `Decision.errors` holds each failed unit's final error.

    `decide` calls this with `stages(resolved)`; an eval that forces a
    backend builds its own chain."""
    items = tuple(items)
    decisions.validate(items)
    if not chain:
        raise ValueError(f"{task!r} has no decide stage to answer it")
    call = _Call(task=task, client=client, explain=explain,
                 campaign=campaign, scene=scene, post=post, round_id=round_id,
                 capture=capture, around=around, chain=chain[0].chain,
                 retries=chain[0].retries)
    results: list[decisions.ItemResult | None] = [None] * len(items)
    #: Per item, the failure of each stage that failed it, in stage order.
    words: dict[int, list[LLMError]] = {}
    rows: list[dict] = []
    served: dict[tuple[str, str], None] = {}     # ordered, each once
    failed: list[tuple[tuple[int, ...], LLMError]] = []
    pending = list(range(len(items)))
    #: The provider of each stage that stopped on a connection-wide failure.
    dead: list[str] = []
    for stage in chain:
        if not pending:
            break
        if any(_same_connection(stage.chain.primary.provider_id, d) for d in dead):
            # It would meet the failure that stopped the stage before it.
            continue
        got = await _BACKENDS[stage.mode](
            tuple(items[i] for i in pending),
            replace(call, chain=stage.chain, retries=stage.retries))
        rows.extend(got.rows)
        served.update(dict.fromkeys(got.served))
        failed = _settle(got, pending, results, words)
        pending = [i for i in pending if results[i] is None]
        if any(isinstance(error, llm.PresetRefusalError) or _refused_unsent(error)
               for _unit, error in failed):
            break
        if got.stopped is not None and _connection_wide(got.stopped):
            dead.append(stage.chain.primary.provider_id)
    # Each failed unit of the last stage its items reached, with the failures
    # of every stage before it: a chunk's earlier failures are its first
    # item's (a native stage before it failed each item on its own).
    errors = tuple(_final(words[unit[0]]) for unit, _error in failed)
    # Each backend stamps the items it answered (`ItemResult.backend`).
    answered_by = list(dict.fromkeys(r.backend for r in results if r is not None))
    if not answered_by:
        # No item answered: the provider's error is the caller's (ruling 8).
        raise errors[0]
    for index in pending:
        results[index] = decisions.unanswered((items[index],), "error")[0]
    # One backend, and one route, answered: it is the decision's. Several
    # (the fallback stage took the items the primary failed): none is.
    backend = answered_by[0] if len(answered_by) == 1 else ""
    provider, model = next(iter(served)) if len(served) == 1 else ("", "")
    return decisions.Decision(items=tuple(r for r in results if r is not None),
                              backend=backend, provider=provider, model=model,
                              usage=tuple(rows), served=tuple(served), errors=errors)


async def decide(task: str, items: Sequence[decisions.Item], *, client: LLMClient,
                 resolved: ResolvedInference, explain: str = "", campaign: str = "",
                 scene: str = "", post: int | None = None, round_id: str = "",
                 capture: Capture | None = None,
                 around: Around | None = None) -> decisions.Decision:
    """Answer `items` (spec 7.4): one `ItemResult` per item, in input order.

    `resolved` is the call site's own resolution of `task` for the `decide`
    operation; a resolution of another task or operation, or one that resolved
    nothing, is a `ValueError`. An invalid request is a
    `decisions.DecideRequestError`. Both are raised before any meter opens, so
    neither files a ledger row.

    `explain` is the rationale instruction ("" asks for none). `campaign`,
    `scene`, `post` and `round_id` attribute each call's ledger row. `capture`
    is handed each call once it settles (`Capture`); `around` is handed each
    facade call and the meter's live holder, and returns what to await
    instead (a caller's time budget).
    """
    if resolved.task != task:
        raise ValueError(f"a resolution of {resolved.task!r} cannot decide {task!r}")
    if resolved.operation != "decide":
        raise ValueError(f"a {resolved.operation!r} resolution cannot decide")
    if resolved.chain is None:
        raise ValueError(f"{task!r} resolved to no connection")
    chain = stages(resolved)
    if not chain:
        raise ValueError(f"no decision backend for mode {resolved.decision_mode!r}")
    return await run_stages(task, items, chain, client=client,
                            explain=explain, campaign=campaign, scene=scene, post=post,
                            round_id=round_id, capture=capture, around=around)


# ---- generate (spec 7.2; 01f) ----

def _generating(task: str, resolved: ResolvedInference) -> wire.Chain:
    """The chain `resolved` sends for `task`'s generation (its primary's
    target, and the fallback's where it rides), or the `ValueError`
    `generate` raises for a resolution it cannot send."""
    if resolved.task != task:
        raise ValueError(f"a resolution of {resolved.task!r} cannot generate {task!r}")
    if resolved.operation != "generate":
        raise ValueError(f"a {resolved.operation!r} resolution cannot generate")
    chain = resolved.chain
    if chain is None:
        raise ValueError(f"{task!r} resolved to no connection")
    return chain


def call_chain(resolved: ResolvedInference, *, schema: dict | None = None) -> wire.Chain:
    """The chain one `generate` call sends (01f, 3.2): `resolved.chain`, with
    -- when the call asks for a `schema` -- each target whose attempt is
    `resolve.structured_capable` replaced by a NEW target flagged for its
    provider's structured mode. The primary is `attempts[0]`, the fallback,
    when it rides, `attempts[1]`.

    Pure, and per call: a resolution's targets are frozen and shared, and
    the flag is a property of this call rather than of the route, so the
    resolution is never changed. A caller that records the prompt hands the
    same chain to `routes.common._record_prompt`, so the log says what this
    call was sent. A resolution of nothing is a `ValueError`."""
    chain = resolved.chain
    if chain is None:
        raise ValueError(f"{resolved.task!r} resolved to no connection")
    if schema is None:
        return chain
    flagged = [replace(target, structured=resolve.structured_capable(attempt))
               for target, attempt in zip(chain.attempts, resolved.attempts, strict=False)]
    if flagged == list(chain.attempts):
        return chain
    return wire.Chain(*flagged)


def _says(message: object, text: str) -> bool:
    """Whether a system or user `message` carries `text` in its content (a
    string, or a list of parts whose text parts are searched)."""
    if not isinstance(message, dict) or message.get("role") not in ("system", "user"):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return text in content
    if isinstance(content, list):
        return any(isinstance(part, dict) and isinstance(part.get("text"), str)
                   and text in part["text"] for part in content)
    return False


def _refuse_unschemable(schema: dict, messages: list[dict], chain: wire.Chain) -> None:
    """The `ValueError`s a `generate(schema=)` call is refused with before
    any client call (01f, 3.3): a schema outside the portable subset
    (`schemas.check`); a prompt no system or user message of which carries
    `schemas.render(schema)` -- an attempt without the mode answers from the
    prompt alone, so a schema only the wire saw would get two answers; and a
    prompt ending in an assistant turn, or one with per-attempt tails, since
    structured output does not continue a prefill (01, 7.2)."""
    schemas.check(schema)
    spelled = schemas.render(schema)
    if not any(_says(message, spelled) for message in messages):
        raise ValueError("a generate schema must be in the prompt, rendered with the "
                         "`schema_json` filter (schemas.render)")
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant":
        raise ValueError("a structured generation cannot continue an assistant turn "
                         "(a prefill)")
    if (isinstance(messages, model_guidance.PreparedMessages)
            and messages.mode_for(chain.primary) is not None):
        raise ValueError("a structured generation cannot carry per-attempt tails")


def _take_attempts(usage: dict | None) -> int:
    """The attempts the call before a re-send made, taken out of the holder
    (its last stamp's `attempts`), so the re-send's own stamp is its own."""
    if usage is None:
        return 0
    made = usage.pop("attempts", 0)
    return made if isinstance(made, int) and not isinstance(made, bool) else 0


def _add_attempts(usage: dict | None, before: int) -> None:
    """Add the attempts the calls before a re-send made to the holder's own
    count: the row the caller's meter files says how many attempts the whole
    call took, the refused ones included."""
    if usage is None:
        return
    own = usage.get("attempts", 0)
    usage["attempts"] = before + (own if isinstance(own, int) and not isinstance(own, bool)
                                  else 0)


@overload
def generate(task: str, messages: list[dict], *, client: LLMClient,
             resolved: ResolvedInference, usage: dict | None = None,
             schema: dict | None = None,
             stream: Literal[True] = True) -> AsyncIterator[str]: ...


@overload
def generate(task: str, messages: list[dict], *, client: LLMClient,
             resolved: ResolvedInference, usage: dict | None = None,
             schema: dict | None = None,
             stream: Literal[False]) -> Awaitable[str]: ...


def generate(task: str, messages: list[dict], *, client: LLMClient,
             resolved: ResolvedInference, usage: dict | None = None,
             schema: dict | None = None,
             stream: bool = True) -> AsyncIterator[str] | Awaitable[str]:
    """Generate free text for `task` (spec 7.2): with `stream=True` an async
    iterator of the reply's deltas, with `stream=False` an awaitable of the
    joined reply.

    `resolved` is the call site's own resolution of `task` for the
    `generate` operation (`require_inference`, `override_inference`, or
    `for_task` for a sibling task on the same route). A resolution of another
    task or operation, or one that resolved nothing, is a `ValueError`, raised
    here -- before any client call, so no request goes out and no holder is
    stamped.

    `usage` is the caller's meter's holder (`store.usage.Meter.usage`), which
    the facade fills; the meter stays at the call site, around this call, as
    `test_usage_guard.py` requires.

    `schema` asks for JSON matching a JSON Schema (01f-C1). It must be inside
    the portable subset and rendered into the prompt with `schema_json`, and
    the prompt may not end in an assistant turn or carry per-attempt tails --
    each a `ValueError` here, before any client call (`_refuse_unschemable`).
    Each attempt whose model's `structured_output` is `yes` is sent its
    provider's structured mode on a new target (`call_chain`); every other
    attempt is sent what it would have been without the schema. The reply is
    still text, and nothing guarantees it conforms: the caller parses it
    (`schemas.find_value`). A call that failed on every route, one of them by
    refusing the field, is re-sent without the mode (`_without_refused_mode`,
    01f-C2) through the same holder, so the caller's meter files ONE row: the
    attempt that answered, its `attempts` counting every attempt the call
    made. On the streamed path that happens only before any text reached the
    caller, which `llm._resilient` guarantees.

    The facade is sent the call's chain positionally, with `schema=` only
    when one is given, so a call without one is the request, ledger row and
    capture it was before 01f."""
    base = _generating(task, resolved)
    if schema is None:
        chain = call_chain(resolved)
        if stream:
            return client.stream(messages, chain, usage)
        return client.complete(messages, chain, usage)
    _refuse_unschemable(schema, messages, base)
    chain = call_chain(resolved, schema=schema)
    # The facade calls stay here, so `test_usage_guard.FORWARDERS` sees each
    # one forward this function's own `usage`; the re-send around them is
    # `_joined` / `_streamed`'s.

    def complete(attempt: wire.Chain) -> Awaitable[str]:
        return client.complete(messages, attempt, usage, schema=schema)

    def open_stream(attempt: wire.Chain) -> AsyncIterator[str]:
        return client.stream(messages, attempt, usage, schema=schema)

    if stream:
        return _streamed(chain, open_stream, usage)
    return _joined(chain, complete, usage)


async def _joined(chain: wire.Chain, complete: Callable[[wire.Chain], Awaitable[str]],
                  usage: dict | None) -> str:
    """`generate(stream=False)` with a schema: the call, and -- refused on
    every route, one of them by refusing the field -- its re-sends without the
    mode through the same holder (`_without_refused_mode`), the row's
    `attempts` counting every attempt made."""
    try:
        return await complete(chain)
    except llm.SchemaRefusalError as error:
        if not error.attempts:
            raise
        refused = error

    async def send(again: wire.Chain) -> str:
        before = _take_attempts(usage)
        try:
            return await complete(again)
        finally:
            _add_attempts(usage, before)

    return await _without_refused_mode(refused, send)


async def _streamed(chain: wire.Chain, open_stream: Callable[[wire.Chain], AsyncIterator[str]],
                    usage: dict | None) -> AsyncIterator[str]:
    """`_joined`, streamed: a refusal re-sends only while nothing has reached
    the caller -- the only time `llm._resilient` raises one -- so nothing is
    taken back (`_stream_without_refused_mode`)."""
    first = open_stream(chain)
    yielded = False
    try:
        async for chunk in first:
            yielded = yielded or bool(chunk)
            yield chunk
        return
    except llm.SchemaRefusalError as error:
        if yielded or not error.attempts:
            raise
        refused = error
    finally:
        await _aclose(first)

    async def again(attempt: wire.Chain) -> AsyncIterator[str]:
        before = _take_attempts(usage)
        inner = open_stream(attempt)
        try:
            async for chunk in inner:
                yield chunk
        finally:
            await _aclose(inner)
            _add_attempts(usage, before)

    async for chunk in _stream_without_refused_mode(refused, again):
        yield chunk


def note_outcome(client: LLMClient, resolved: ResolvedInference,
                 error: LLMError | None) -> None:
    """File an outcome the facade did not itself observe (#146) -- a bounded
    call's ceiling, which cancels the call from outside -- against the attempt
    `resolved` sends first: its chain's primary target, alone. A resolution of
    nothing has no attempt to file it against."""
    chain = resolved.chain
    if chain is not None:
        client.note_outcome(chain.primary, error)


def for_task(resolved: ResolvedInference, task: str) -> ResolvedInference:
    """`resolved` as the resolution of `task`, a task on the SAME route.

    The resolver answers per route, never per task (`routing.route`): two
    tasks of one route resolve to the same attempts, read from the same
    settings. So a call site that resolved once and generates under a sibling
    task's label -- a director turn on the send's resolution of `chat`, a
    group round's contributions (`chat` or `continuation`) on whatever turn
    opened the round -- says so here rather than handing `generate` a
    resolution of another task, which it refuses. A task on another route, or
    one no route claims, is a `ValueError`: that resolution would not be
    where `task` runs."""
    if resolved.task == task:
        return resolved
    route = store.routing.route(task)
    if route is None or not resolved.route or route.key != resolved.route:
        raise ValueError(f"{task!r} is not on the route {resolved.task!r} resolved "
                         f"({resolved.route or 'none'!r})")
    return replace(resolved, task=task)
