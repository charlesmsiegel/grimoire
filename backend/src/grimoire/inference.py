"""The inference operations a call site asks for by name: `generate` (spec 7.2)
and `decide` (spec 7.4).

A generation is free text, streamed or joined. `generate` is the only door
to it: it takes the resolution a call site already made (`require_inference`
or `override_inference`), refuses one made for another task or operation, or
one that resolved nothing, before any client call, and hands the facade
`resolved.chain`, the resolution's typed targets. `test_routing_guard.py`
holds that `client.stream`/`complete` are spelled only in this module; the
caller's meter, and the holder it hands in as `usage=`, stay at the call
site, where `test_usage_guard.py` reads them. With a `schema` (01f), the
facade is sent a per-call chain instead (`_structured_chain`: structured mode
on each attempt known to take it), the prompt must carry the schema, and a
route that refused the field is re-sent once without it
(`_without_refused_mode`, which `decide` shares).

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
stage only when its stage FAILED to answer it (`run_stages`). A task whose
code policy lists the primary's kind (`routing.TaskPolicy.native_first`, 01c)
asks a generating primary's decisions endpoint first, in an isolated stage
of its own (`stages`, `resolve.native_first`).

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
  (`llm.routes_failed`) -- an isolated (native-first) stage's failure is not
  composed into the error of an item a later stage took; once one has, a failed unit's items are `None` with
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
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal, NamedTuple, TypeVar, overload

from . import (
    decisions,
    llm,
    llm_errors,
    llm_sampling,
    model_guidance,
    prompts,
    schemas,
    store,
    wire,
)
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
#: out; the outcome is `decisions.outcome`'s record of it, with the call's
#: `stage` and `at` (its batch indices, 01b §3.2); the target is the
#: one the attempt that answered was sent (`llm.ATTEMPTED`, a fallback's or a
#: prompt-only re-send's included), or the stage's account-stamped primary
#: when the call failed -- a native stage's `without_sampling`, as it was
#: sent. One structured chunk is one call, its prompt-only re-send included;
#: each native item is one call.
Capture = Callable[[list[dict], dict, wire.Target], Awaitable[None]]


class Outcome(dict):
    """The outcome a `Capture` is handed: `decisions.outcome`'s record, equal
    to and JSON-encoded as that plain dict, with the failed call's `LLMError`
    beside it as `failure` (None when the call answered) -- never inside it.
    The record's `error` is `"kind: detail"`, and the detail is the
    provider's own text; a capture that persists keeps the kind, status and
    code read off `failure` instead (01b §3.3)."""

    failure: LLMError | None = None


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
                                   explain=wanted, kinds=decisions.kinds(items))},
        {"role": "user",
         "content": prompts.render("decide/user.j2", items=items, explain=explain)}]


class Stage(NamedTuple):
    """One stage of a decide chain: the backend (`mode`, a key of
    `_BACKENDS`), the chain it sends -- a primary, and the fallback the
    facade fails over to when one rides it -- and the primary route's retry
    count: None for the facade's own budget (the primary's `llm_retries`),
    0 on a fallback stage, which gets one attempt (spec 5.4, I3), and on a
    native-first one (01c §4.2.2)."""

    mode: str
    chain: wire.Chain
    retries: int | None
    #: A native-first stage (01c §4.2.1): whatever stops it is a statement
    #: about its decisions endpoint, so `run_stages` never marks its provider
    #: dead, and its failures are not composed into the error of an item a
    #: later stage took. False for every other stage.
    isolated: bool = False


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
    never answer.

    Native first (01c §4.2): a structured primary whose task's policy lists
    its adapter kind, and which is known able to decide natively
    (`resolve.native_first`), is asked through its decisions endpoint first
    -- an isolated stage on the same model, with no retries, sent the
    primary alone and unflagged (no structured envelope goes to a decisions
    endpoint) -- and the structured stage after it, exactly today's, takes
    the items that stage failed. No task's policy lists a kind yet, so no
    chain built today has that stage."""
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
        if resolve.native_first(resolved):
            target = primary.target
            alone = replace(target, structured=False) if target.structured else target
            chain.append(Stage(NATIVE, wire.Chain(alone), 0, isolated=True))
        chain.append(Stage(STRUCTURED, whole.alone() if apart else whole, None))
    if apart and fallback is not None:
        chain.append(Stage(fallback.decision_mode, wire.Chain(fallback.target), 0))
    return tuple(chain)


def reports_distribution(resolved: ResolvedInference) -> bool:
    """Whether `resolved`'s first decide stage is native (01c §4.2): a
    native-only primary, or a native-first one (`resolve.native_first`).
    Pure, and sends nothing. A forecast, not a guarantee: an item the native
    stage fails is answered by a structured stage with no distribution, so a
    sampling caller still goes through `draws.draw` per item."""
    planned = stages(resolved)
    return bool(planned) and planned[0].mode == NATIVE


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
    #: The stage's index in the chain `run_stages` was handed, and its
    #: positions mapped back to batch indices (`positions[p]` is the batch
    #: index of the stage's item `p`; empty means the identity).
    stage: int = 0
    positions: tuple[int, ...] = ()

    def batch(self, unit: Sequence[int]) -> tuple[int, ...]:
        """`unit`'s stage positions as batch indices."""
        if not self.positions:
            return tuple(unit)
        return tuple(self.positions[p] for p in unit)


class _Answered(NamedTuple):
    """What one stage made of the items it was given, by position: each
    item's result (None for an item its stage failed to answer), each failed
    unit -- a chunk on a structured stage, an item on a native one -- as its
    items' positions and its final error, in order, and the stage's ledger
    rows and what answered (`decisions.Decision.served`). Every unanswered
    item is in exactly one failed unit. `stopped` is the failure that made
    the stage start nothing more, or None -- `run_stages` reads it to skip a
    later stage that would only meet it again. `calls` is one
    `decisions.CallRecord` per metered request the stage made, in the order
    they settled."""

    results: tuple[decisions.ItemResult | None, ...]
    failed: tuple[tuple[tuple[int, ...], LLMError], ...]
    rows: tuple[dict, ...]
    served: tuple[tuple[str, str], ...]
    stopped: LLMError | None = None
    calls: tuple[decisions.CallRecord, ...] = ()


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


def _resends(error: llm.SchemaRefusalError) -> Iterator[tuple[int, wire.Chain]]:
    """The re-sends a schema refusal asks for (01f-C2): each route whose
    failure was its structured field refused (`error.attempts[i]`), in route
    order, as `(i, that attempt alone and without the mode)`."""
    for index, attempt in enumerate(error.attempts):
        if isinstance(attempt, wire.Target):
            yield index, _without_mode(attempt)


async def _without_refused_mode(error: llm.SchemaRefusalError,
                                send: Callable[[wire.Chain], Awaitable[T]]) -> T:
    """Answer a call that failed on every route, one of them by refusing the
    structured field (`error`): each refusing attempt once more, alone and
    without the mode, in route order (`_resends`), through `send`; the first
    that answers wins (01f-C2, spec M-4, ruling 3 -- the schema rides the
    prompt, so the reply still parses). A primary that refused is re-sent
    after its fallback failed too; a fallback that refused, after the primary
    failed for any reason. None is re-sent twice.

    Should every re-send fail, `llm.routes_failed(words)`: the routes'
    failures composed afresh, each re-sent route's word replaced by its
    re-send's failure. `decide`'s `_ask` and `generate` share this; each
    brings its own `send` (a metered call per re-send for `decide`, the
    caller's holder for `generate`)."""
    words = list(error.words)
    for index, chain in _resends(error):
        try:
            return await send(chain)
        except LLMError as exc:
            words[index] = exc
    raise llm.routes_failed(words)


async def _stream_without_refused_mode(
        error: llm.SchemaRefusalError,
        open_stream: Callable[[wire.Chain], AsyncGenerator[str, None]]
) -> AsyncGenerator[str, None]:
    """`_without_refused_mode`'s streamed twin: the same re-sends in the same
    order (`_resends`) and the same composed error, but a re-send's failure is
    found while iterating it. A re-send that has yielded prose and then fails
    is re-raised as it is, and no later route is re-sent: the caller has seen
    that text, and nothing retracts it (`llm._resilient`'s rule)."""
    words = list(error.words)
    for index, chain in _resends(error):
        shown = False
        try:
            async with aclosing(open_stream(chain)) as again:
                async for chunk in again:
                    shown = shown or bool(chunk)
                    yield chunk
            return
        except LLMError as exc:
            if shown:
                raise
            words[index] = exc
    raise llm.routes_failed(words)


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


def _server(target: object) -> tuple[()] | tuple[str, str, str]:
    """`(kind, provider id, model)` of `target`: the `ItemResult.served` of an
    item it answered (01d §5.6), read as `_served_by` reads a call's. `()`
    for anything that is not a `wire.Target` -- a holder nothing stamped --
    and for one whose three fields are not all strings, so provenance, which
    nothing in the chain reads, can never fail an answered decision."""
    if not isinstance(target, wire.Target):
        return ()
    kind, provider_id, model = target.kind, target.provider_id, target.model
    if not (isinstance(kind, str) and isinstance(provider_id, str)
            and isinstance(model, str)):
        return ()
    return (kind, provider_id, model)


def _record(call: _Call, mode: str, unit: Sequence[int], row: dict | None,
            error: LLMError | None) -> decisions.CallRecord:
    """One settled request's `decisions.CallRecord`: its error's kind and
    status only, never its detail (the provider's own text)."""
    return decisions.CallRecord(
        stage=call.stage, mode=mode, items=call.batch(unit), row=row,
        error_kind=error.kind if error is not None else "",
        error_status=error.status if error is not None else None)


async def _once(call: _Call, sending: wire.Chain, messages: list[dict], schema: dict,
                rows: list[dict], *, records: list[decisions.CallRecord],
                unit: tuple[int, ...]) -> _Reply:
    """One metered facade call: its text and the answering holder, or its
    error. Its ledger row, when the meter filed one, is appended to
    `rows`, and its `CallRecord` (over the stage positions `unit`) to
    `records`."""
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
    records.append(_record(call, STRUCTURED, unit, m.row, error))
    sent = bool(m.usage)
    return (_Reply(text, m.usage, None, sent) if error is None
            else _Reply("", None, error, sent))


async def _ask(call: _Call, chain: wire.Chain, messages: list[dict], schema: dict,
               rows: list[dict], *, records: list[decisions.CallRecord],
               unit: tuple[int, ...]) -> _Reply:
    """One chunk's call: its text and the answering holder, or its error,
    and whether any of the calls it made went out. Each call's ledger row is
    appended to `rows`, and its `CallRecord` to `records`.

    The attempt chain, and -- only when every route failed and a route's
    failure was its provider refusing the structured field
    (`llm.SchemaRefusalError`) -- each such attempt once more, alone and
    without the mode (spec M-4, ruling 3): the schema is in the prompt, so the
    reply still parses. A primary that refused is re-sent after its fallback
    failed too; a fallback that refused, after the primary failed for any
    reason. Each re-send is its own metered call, and none is re-sent twice.
    Should those fail too, the error is the routes' failures composed afresh
    (`llm.routes_failed`), each re-sent route's word now its own failure.
    The re-sends are `_without_refused_mode`'s, which `generate` shares."""
    first = await _once(call, chain, messages, schema, rows, records=records, unit=unit)
    error = first.error
    if not isinstance(error, llm.SchemaRefusalError) or not error.attempts:
        return first
    sent = first.sent

    async def send(sending: wire.Chain) -> _Reply:
        nonlocal sent
        again = await _once(call, sending, messages, schema, rows, records=records, unit=unit)
        sent = sent or again.sent
        if again.error is not None:
            raise again.error
        return again._replace(sent=sent)

    try:
        return await _without_refused_mode(error, send)
    except LLMError as exc:
        return _Reply("", None, exc, sent)


def _outcome(call: _Call, unit: Sequence[int], mode: str, target: wire.Target,
             holder: dict | None, results: Sequence[decisions.ItemResult],
             error: LLMError | None) -> Outcome:
    """A settled call's record for the capture (`decisions.outcome`): what
    answered it and its results, or -- failed -- the primary its stage sent
    (`target`) and its error; and, either way, the call's `stage` and `at`,
    the BATCH indices of the stage positions `unit` it carried (01b §3.2).
    The error itself rides beside the record (`Outcome.failure`)."""
    if error is not None:
        record = decisions.outcome(mode, target.provider_id, target.model,
                                   error=f"{error.kind}: {error.detail}")
    else:
        provider, model = _served_by(holder or {})
        record = decisions.outcome(mode, provider, model, results)
    out = Outcome({**record, "stage": call.stage, "at": list(call.batch(unit))})
    out.failure = error
    return out


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
    records: list[decisions.CallRecord] = []
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
        text, holder, error, sent = await _ask(call, chain, messages, schema, rows,
                                               records=records, unit=unit)
        answered: list[decisions.ItemResult] = []
        if holder is not None:
            # Each item names the server that answered its chunk: the
            # attempt the facade sent (`llm.ATTEMPTED`), a fallback or a
            # re-send without the mode included.
            server = _server(holder.get(llm.ATTEMPTED))
            answered = [replace(result, backend=STRUCTURED, served=server)
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
                        partial(_outcome, call, unit, STRUCTURED, chain.primary, holder,
                                answered, error),
                        ran if isinstance(ran, wire.Target) else chain.primary)
        if error is not None:
            # Filed by the meter already; the chunk's fate waits on the chain.
            failed.append((unit, error))
            if _stops_chunks(error, chain):
                stopped = error
    return _Answered(tuple(results), tuple(failed), tuple(rows), tuple(served), stopped,
                     tuple(records))


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
    records: list[decisions.CallRecord] = []    # in the order the items settle
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
                    got = await (call.around(pending, m.usage) if call.around else pending)
                    # The server is the stage's one target as sent, whatever
                    # the adapter's result claimed.
                    results[index] = replace(got, served=_server(named))
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
        # Settled (a cancel or an unexpected exception never reaches here).
        records.append(_record(call, NATIVE, (index,), m.row, errors[index]))
        # Settled, and out of the gate (a cancel never reaches here): the
        # request as sent, built off the loop -- or none, when the call was
        # refused before it went out (`Capture`).
        answered = results[index]
        await _captured(
            call, partial(_native_request, item, named) if m.usage else [],
            partial(_outcome, call, (index,), NATIVE, named, holders[index],
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
                     stopped[0] if stopped else None, tuple(records))


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


def _all_dead(stage: Stage, dead: Sequence[str]) -> bool:
    """Whether every route `stage` sends -- its primary, and on a structured
    stage the fallback riding it -- is on a connection a stage before it
    stopped dead (01c §4.2.1). A native stage sends its primary alone
    (`_native` reads only `chain.primary`), so only that counts there. Today
    only a first stage carries a riding fallback, so for every chain built
    this asks whether the primary's connection is dead."""
    sent = (stage.chain.primary,) if stage.mode == NATIVE else stage.chain.attempts
    return all(any(_same_connection(target.provider_id, d) for d in dead)
               for target in sent)


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
    refusal, and the one it has keeps its type -- an isolated stage
    included. A stage that stopped on a failure every call on its connection
    would meet (`_connection_wide`: the key, the account's money, an
    exhausted rate limit) skips each later stage every route of which is on
    that SAME connection (`_all_dead`, `_same_connection`): its items keep
    the failures they have, as items held back inside a stage do.

    An isolated stage (native first, 01c §4.2.1) is a statement about its
    decisions endpoint only: it never marks its connection dead -- a key
    refused there may serve every chat call -- and its failures are set
    aside, composed into an item's error only when no later stage reached
    that item.

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
    #: An isolated stage's failures, per item: an item's words only when no
    #: later stage reached it (01c §4.2.1).
    aside: dict[int, list[LLMError]] = {}
    rows: list[dict] = []
    records: list[decisions.CallRecord] = []
    served: dict[tuple[str, str], None] = {}     # ordered, each once
    failed: list[tuple[tuple[int, ...], LLMError]] = []
    pending = list(range(len(items)))
    #: The provider of each stage that stopped on a connection-wide failure.
    dead: list[str] = []
    for number, stage in enumerate(chain):
        if not pending:
            break
        if _all_dead(stage, dead):
            # Every route it sends would meet the failure that stopped a
            # stage before it.
            continue
        got = await _BACKENDS[stage.mode](
            tuple(items[i] for i in pending),
            replace(call, chain=stage.chain, retries=stage.retries, stage=number,
                    positions=tuple(pending)))
        rows.extend(got.rows)
        records.extend(got.calls)
        served.update(dict.fromkeys(got.served))
        failed = _settle(got, pending, results, aside if stage.isolated else words)
        pending = [i for i in pending if results[i] is None]
        if any(isinstance(error, llm.PresetRefusalError) or _refused_unsent(error)
               for _unit, error in failed):
            break
        if (got.stopped is not None and _connection_wide(got.stopped)
                and not stage.isolated):
            dead.append(stage.chain.primary.provider_id)
    # Each failed unit of the last stage its items reached, with the failures
    # of every stage before it: a chunk's earlier failures are its first
    # item's (a native stage before it failed each item on its own).
    # An isolated stage's failure counts only for an item no later stage
    # reached (the chain ended right after it): `words` is then empty for it.
    errors = tuple(_final(words.get(unit[0]) or aside[unit[0]]) for unit, _error in failed)
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
                              usage=tuple(rows), served=tuple(served), errors=errors,
                              calls=tuple(records))


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


# ---- generate (spec 7.2) ----

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


def _structured_chain(resolved: ResolvedInference) -> wire.Chain:
    """`resolved.chain` with each target replaced by a NEW target, flagged
    `structured` exactly when its attempt is `resolve.structured_capable`
    (01f 3.2): the primary is `attempts[0]`, the fallback -- when it rides --
    `attempts[1]`. The flag is this call's, never the route's, so the
    resolution is left as it is: its targets are frozen and shared, and a
    generate resolution's still say what they said before slice F."""
    chain = resolved.chain
    if chain is None:
        raise ValueError(f"{resolved.task!r} resolved to no connection")
    primary = replace(chain.primary,
                      structured=resolve.structured_capable(resolved.attempts[0]))
    fallback = (None if chain.fallback is None else
                replace(chain.fallback,
                        structured=resolve.structured_capable(resolved.attempts[1])))
    return wire.Chain(primary, fallback)


#: `llm`'s, where the fallback's preset is re-capped (`llm.fallback_sampling`),
#: and named here too because a `generate` caller reads them off this module.
MAX_OUTPUT_CAP = llm.MAX_OUTPUT_CAP
clamp_to_max_output = llm.clamp_to_max_output


def call_chain(resolved: ResolvedInference, *, schema: dict | None = None,
               max_tokens: int | None = None) -> wire.Chain:
    """The chain a `generate` call sends (01f 3.7), pure: with a `schema`,
    each attempt flagged for its provider's structured mode when its model
    takes it (`_structured_chain`); with `max_tokens`, each attempt capped
    (`wire.Target.with_output_cap`, at `clamp_to_max_output`'s figure). Both
    on new targets, so the resolution is never mutated; with neither, the
    resolution's own chain, as it always was. A caller that records its
    prompt hands this to `routes.common._record_prompt(conn=...)`, so the
    prompt log reports the cap and the flag the call was sent with."""
    chain = _structured_chain(resolved) if schema is not None else resolved.chain
    if chain is None:
        raise ValueError(f"{resolved.task!r} resolved to no connection")
    if max_tokens is None:
        return chain
    # The cap asked for is recorded as asked (`Sampling.call_cap`); what is
    # sent is held to each attempt's own maximum.
    primary = chain.primary.with_output_cap(
        max_tokens, most=clamp_to_max_output(chain.primary, max_tokens))
    fallback = (None if chain.fallback is None else chain.fallback.with_output_cap(
        max_tokens, most=clamp_to_max_output(chain.fallback, max_tokens)))
    return wire.Chain(primary, fallback)


def cap_sent(target: wire.Target) -> bool:
    """Whether `target`'s adapter puts `max_tokens` (or its translation) on
    the wire -- asked of the target as a call sends it (`call_chain`), so a
    capped attempt says whether its cap is a bound. Not on the Claude Agent
    SDK, which takes no sampling, nor on an OpenRouter model whose cached
    catalog omits the parameter (dropped as unsupported). Pure."""
    return "max_tokens" in llm_sampling.sent_names(target)


def _check_cap(max_tokens: object) -> None:
    """`generate`'s refusal of an output cap that is not a positive int,
    before any client call. A cap above what can be sent is not refused: it
    is held to `MAX_OUTPUT_CAP` and the model's own maximum
    (`clamp_to_max_output`), so "as much as the model allows" is a cap a
    caller can ask for."""
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise ValueError(f"max_tokens must be a positive whole number, not {max_tokens!r}")


def _texts(content: object) -> Iterator[str]:
    """The text a message's `content` carries: the string itself, or each
    text part of a content-part list."""
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                yield part["text"]


def _check_structured(messages: list[dict], schema: dict) -> None:
    """The refusals `generate(schema=)` makes before any client call (01f
    3.3), each a `ValueError`:

    - a schema outside the portable subset (`schemas.check`'s
      `SchemaError`);
    - a prompt in which no `system` or `user` message carries
      `schemas.render(schema)` -- the schema rides the prompt (ruling 3), so
      a fallback without the mode, a re-send after a refusal and an
      `unknown` model answer the same question; a template renders it with
      the `schema_json` filter, which is the same function;
    - a prompt ending in an assistant turn, or one whose ending is chosen per
      attempt (`PreparedMessages.tailed`): structured output is incompatible
      with prefill (01 section 7.2), and a tail chooser could prefill one
      attempt and not another."""
    schemas.check(schema)
    if isinstance(messages, model_guidance.PreparedMessages) and messages.tailed:
        raise ValueError("a structured generation cannot carry per-attempt tails "
                         "(a prefill is incompatible with structured output)")
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant":
        raise ValueError("a structured generation cannot end in an assistant turn "
                         "(a prefill is incompatible with structured output)")
    shown = schemas.render(schema)
    if not any(shown in text for message in messages
               if isinstance(message, dict) and message.get("role") in ("system", "user")
               for text in _texts(message.get("content"))):
        raise ValueError("the prompt does not carry the schema: render it with the "
                         "`schema_json` filter in a system or user message")


def _attempts_made(usage: dict | None) -> int:
    """How many attempts the holder's last stamp says the call has made
    (`llm._stamp`), 0 for none."""
    made = usage.get("attempts") if usage is not None else None
    return made if isinstance(made, int) and not isinstance(made, bool) else 0


@contextmanager
def _counting_earlier(usage: dict | None) -> Iterator[None]:
    """Around one re-send on the caller's holder: add the attempts the
    refused call had made (read as the re-send starts, while the holder still
    holds that call's last stamp) to the re-send's own count, whatever the
    re-send's outcome, so the one row the caller's meter files counts every
    attempt the call made (01f-C2). Nothing else of the refused call
    survives the re-send's stamp: a failed attempt's charge is not in the
    ledger (`llm._stamp`)."""
    earlier = _attempts_made(usage)
    try:
        yield
    finally:
        if usage is not None and earlier:
            usage["attempts"] = _attempts_made(usage) + earlier


async def _joined_structured(first: Awaitable[str],
                             resend: Callable[[wire.Chain], Awaitable[str]]) -> str:
    """`generate(schema=, stream=False)`'s body: `first`, the call down the
    whole chain, and -- when every route failed and one refused the field --
    the refusing attempts re-sent through `resend` (`_without_refused_mode`)."""
    try:
        return await first
    except llm.SchemaRefusalError as error:
        if not error.attempts:
            raise
        refused = error
    return await _without_refused_mode(refused, resend)


async def _streamed_structured(
        first: AsyncGenerator[str, None],
        reopen: Callable[[wire.Chain], AsyncGenerator[str, None]]
) -> AsyncGenerator[str, None]:
    """`generate(schema=)`'s streamed body: `first`'s deltas, or -- when it
    raised a schema refusal, which `llm._resilient` does only before any
    prose reached the caller, so nothing is retracted -- the re-sends'
    (`_stream_without_refused_mode`) through `reopen`."""
    try:
        async with aclosing(first) as deltas:
            async for chunk in deltas:
                yield chunk
        return
    except llm.SchemaRefusalError as error:
        if not error.attempts:
            raise
        refused = error
    async with aclosing(_stream_without_refused_mode(refused, reopen)) as again:
        async for chunk in again:
            yield chunk


@overload
def generate(task: str, messages: list[dict], *, client: LLMClient,
             resolved: ResolvedInference, usage: dict | None = None,
             schema: dict | None = None, max_tokens: int | None = None,
             stream: Literal[True] = True) -> AsyncIterator[str]: ...


@overload
def generate(task: str, messages: list[dict], *, client: LLMClient,
             resolved: ResolvedInference, usage: dict | None = None,
             schema: dict | None = None, max_tokens: int | None = None,
             stream: Literal[False]) -> Awaitable[str]: ...


def generate(task: str, messages: list[dict], *, client: LLMClient,
             resolved: ResolvedInference, usage: dict | None = None,
             schema: dict | None = None, max_tokens: int | None = None,
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

    Without a `schema`, the facade is sent `resolved.chain` -- the primary's
    target, and the fallback's where it rides (`ResolvedInference.rides`) --
    positionally and with no `schema=`, so every request, ledger row and
    capture is what that chain describes, exactly as before 01f.

    With a `schema` (01f-C1), the reply is asked for as JSON matching it. The
    request is refused before any client call when the schema or the prompt
    cannot carry it (`_check_structured`). Each attempt is sent its
    provider's structured mode exactly when its model's `structured_output`
    is `yes`, on a per-call chain of new targets (`_structured_chain`); every
    other attempt is sent what it would have been sent without the schema,
    which the prompt carries. A route that fails by refusing the field is
    re-sent once without it (`_without_refused_mode`, 01f-C2): through the
    caller's holder, so the one row filed describes the attempt that
    answered and counts every attempt the call made. The reply is text, as
    ever: nothing guarantees it conforms, and the caller parses it
    (`schemas.find_value`).

    With `max_tokens` (01f 3.9), with or without a schema, each attempt's
    output is capped at `min(preset's, max_tokens)` -- and the model's own
    maximum where known (`clamp_to_max_output`) -- on new per-call targets,
    where that attempt's adapter sends `max_tokens` (`cap_sent`). A value
    that is not a positive whole number is a `ValueError`
    before any call. A provider refusing a cap only the call carried is
    `llm.CapRefusalError`, worded as the call's; it is never re-sent without
    the cap. The chain sent is exactly `call_chain(resolved, schema=,
    max_tokens=)`."""
    chain = _generating(task, resolved)
    if max_tokens is not None:
        _check_cap(max_tokens)
    if schema is None:
        chain = call_chain(resolved, max_tokens=max_tokens)
        if stream:
            return client.stream(messages, chain, usage)
        return client.complete(messages, chain, usage)
    _check_structured(messages, schema)
    chain = call_chain(resolved, schema=schema, max_tokens=max_tokens)

    # The re-sends, nested here so they forward this call's `usage` as the
    # facade calls above do (`test_usage_guard.FORWARDERS`).
    async def resend(sending: wire.Chain) -> str:
        with _counting_earlier(usage):
            return await client.complete(messages, sending, usage, schema=schema)

    async def reopen(sending: wire.Chain) -> AsyncGenerator[str, None]:
        with _counting_earlier(usage):
            async with aclosing(client.stream(messages, sending, usage,
                                              schema=schema)) as again:
                async for chunk in again:
                    yield chunk

    if stream:
        return _streamed_structured(client.stream(messages, chain, usage, schema=schema),
                                    reopen)
    return _joined_structured(client.complete(messages, chain, usage, schema=schema), resend)


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
