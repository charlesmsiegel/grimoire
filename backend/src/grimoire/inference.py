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

A policy-declared escalation (roadmap 01d, `decide(escalation=)`) is not a
stage: after the unchanged chain has answered, an ANSWERED item whose own
report says it is unsure (`decisions.triggers`) is handed once to a stronger
resolver -- the escalation role's primary alone, or a caller's `Resolver` --
and a hop that fails, garbles or declines leaves the original answer
standing. One hop, never to the model that answered, recorded in
`Decision.escalations`; `Decision.errors` stays the chain's.

Nothing in `store/` or the gateway imports this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import weakref
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal, NamedTuple, TypeVar, overload

from . import (
    claude_agent,
    deadline,
    decisions,
    llm,
    llm_errors,
    llm_reasoning,
    llm_sampling,
    model_guidance,
    prompts,
    schemas,
    store,
    tool_calls,
    wire,
)
from .llm import LLMClient
from .llm_errors import LLMError
from .store.inference import probes, resolve
from .store.inference.resolved import Attempt, ResolvedInference

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

#: A caller-supplied next resolver (01d §5.2, `routing.CALLER`): handed the
#: triggered items it is to answer and their triggers, in the order taken;
#: its reply is one result per item (`decisions.ResolverReply`). It opens its
#: own meters, captures its own calls and runs under its own budget.
Resolver = Callable[[tuple[decisions.Item, ...], tuple[decisions.Trigger, ...]],
                    Awaitable[decisions.ResolverReply]]

#: What `decide(escalation=)` awaits once some item triggered (01d §5.2):
#: the escalation role's resolution of the task -- or, for a `CALLER`
#: policy, a `Resolver` -- or None and the sentence why there is none. Soft:
#: a refusal is `(None, sentence)`, never a raised 409.
Escalator = Callable[[], Awaitable[tuple[ResolvedInference | Resolver | None, str]]]


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
    #: `decisions.HOP_ESCALATION` on a call an escalation hop makes (01d
    #: §5.5): its `CallRecord.hop` and its capture outcome's "hop". "" on a
    #: call of the chain itself, which then records and captures nothing new.
    hop: str = ""
    #: Attribution every meter of the call files (02 §3.5, 01g-C3): the
    #: reply a decision serves, and the caller's run and its turn.
    response_id: str = ""
    run_id: str = ""
    loop_turn: int | None = None

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
    holder the facade stamped, `store.usage.sent`, the same test
    `store.usage.Meter` files a row by)."""

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


def _meter(call: _Call) -> store.usage.Meter:
    """The meter for one request `call` makes: the one place `decide` opens
    one, so every meter files the same attribution."""
    return store.usage.meter(call.task, campaign=call.campaign, scene=call.scene,
                             post=call.post, round_id=call.round_id,
                             response_id=call.response_id, run_id=call.run_id,
                             loop_turn=call.loop_turn)


def _record(call: _Call, mode: str, unit: Sequence[int], row: dict | None,
            error: LLMError | None) -> decisions.CallRecord:
    """One settled request's `decisions.CallRecord`: its error's kind and
    status only, never its detail (the provider's own text)."""
    return decisions.CallRecord(
        stage=call.stage, mode=mode, items=call.batch(unit), row=row,
        error_kind=error.kind if error is not None else "",
        error_status=error.status if error is not None else None, hop=call.hop)


async def _once(call: _Call, sending: wire.Chain, messages: list[dict], schema: dict,
                rows: list[dict], *, records: list[decisions.CallRecord],
                unit: tuple[int, ...]) -> _Reply:
    """One metered facade call: its text and the answering holder, or its
    error. Its ledger row, when the meter filed one, is appended to
    `rows`, and its `CallRecord` (over the stage positions `unit`) to
    `records`."""
    # Named `client`, the receiver `test_usage_guard.py` recognises.
    client = call.client
    m = _meter(call)
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
    sent = store.usage.sent(m.usage)
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
    the BATCH indices of the stage positions `unit` it carried (01b §3.2),
    and -- on an escalation hop's call only -- its `hop` (01d §5.7).
    The error itself rides beside the record (`Outcome.failure`)."""
    if error is not None:
        record = decisions.outcome(mode, target.provider_id, target.model,
                                   error=f"{error.kind}: {error.detail}")
    else:
        provider, model = _served_by(holder or {})
        record = decisions.outcome(mode, provider, model, results)
    out = Outcome({**record, "stage": call.stage, "at": list(call.batch(unit)),
                   **({"hop": call.hop} if call.hop else {})})
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
            m = _meter(call)
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
            call, partial(_native_request, item, named) if store.usage.sent(m.usage) else [],
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
                     around: Around | None = None, response_id: str = "",
                     run_id: str = "", loop_turn: int | None = None
                     ) -> decisions.Decision:
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
                 retries=chain[0].retries, response_id=response_id, run_id=run_id,
                 loop_turn=loop_turn)
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
                 around: Around | None = None,
                 escalation: Escalator | None = None, response_id: str = "",
                 run_id: str = "", loop_turn: int | None = None) -> decisions.Decision:
    """Answer `items` (spec 7.4): one `ItemResult` per item, in input order.

    `resolved` is the call site's own resolution of `task` for the `decide`
    operation; a resolution of another task or operation, or one that resolved
    nothing, is a `ValueError`. An invalid request is a
    `decisions.DecideRequestError`. Both are raised before any meter opens, so
    neither files a ledger row.

    `explain` is the rationale instruction ("" asks for none). `campaign`,
    `scene`, `post`, `round_id` and `response_id` attribute each call's
    ledger row, and `run_id` / `loop_turn` the caller's run and its turn
    (01g-C3), which the call's incoming-response capture names too. `capture`
    is handed each call once it settles (`Capture`); `around` is handed each
    facade call and the meter's live holder, and returns what to await
    instead (a caller's time budget).

    `escalation` is handed in exactly when the task's code policy escalates
    (`routing.policy(task).escalate_to`; 01d §5.2): a mismatch, an item that
    does not ask the policy's deciding `question`, or a policy whose trigger
    arguments `decisions.triggers` refuses is a `ValueError` before any meter
    opens. It is awaited at most once, after the chain, and only when an item
    triggered: the items it hands over are answered once more by the
    escalation role's primary alone, or by the `Resolver` it returns, and
    what became of each is `Decision.escalations` (`_escalate`).
    """
    items = tuple(items)
    if resolved.task != task:
        raise ValueError(f"a resolution of {resolved.task!r} cannot decide {task!r}")
    if resolved.operation != "decide":
        raise ValueError(f"a {resolved.operation!r} resolution cannot decide")
    if resolved.chain is None:
        raise ValueError(f"{task!r} resolved to no connection")
    policy = _policy_for(task, items, escalation)
    chain = stages(resolved)
    if not chain:
        raise ValueError(f"no decision backend for mode {resolved.decision_mode!r}")
    base = await run_stages(task, items, chain, client=client,
                            explain=explain, campaign=campaign, scene=scene, post=post,
                            round_id=round_id, capture=capture, around=around,
                            response_id=response_id, run_id=run_id, loop_turn=loop_turn)
    if policy is None or escalation is None:
        return base
    found = decisions.triggers(base.items, question=policy.question,
                               escalate_on=policy.escalate_on,
                               margins=dict(policy.margins),
                               answers=policy.escalate_answers)
    if not found:
        return base
    # The hop's own call, built once, with the base's attribution: one
    # attempt (`retries=0`, 01d §11 Q4), after the chain's last stage, and
    # marked as the hop on every record and capture it makes.
    call = _Call(task=task, client=client, explain=explain, campaign=campaign,
                 scene=scene, post=post, round_id=round_id, capture=capture,
                 around=around, chain=chain[0].chain, retries=0, stage=len(chain),
                 hop=decisions.HOP_ESCALATION, response_id=response_id, run_id=run_id,
                 loop_turn=loop_turn)
    return await _escalate(call, items, base, found, policy, escalation, chain)


# ---- the escalation hop (roadmap 01d-C2b) ----

def _policy_for(task: str, items: tuple[decisions.Item, ...],
                escalation: Escalator | None) -> store.routing.TaskPolicy | None:
    """`task`'s policy when it escalates, else None -- after every check that
    must come before any meter opens (01d §5.2, S2's handoff): `escalation`
    is given exactly when the policy escalates, every item asks its deciding
    `question` (so `decisions.triggers` cannot raise after a paid call), and
    `triggers` accepts the policy's arguments (run here over no results)."""
    policy = store.routing.policy(task)
    if bool(policy.escalate_to) != (escalation is not None):
        raise ValueError(f"{task!r}'s policy escalates to {policy.escalate_to!r}, and the "
                         f"call {'passed' if escalation else 'passed no'} escalation=")
    if not policy.escalate_to:
        return None
    decisions.validate(items)
    missing = [index for index, item in enumerate(items)
               if all(q.id != policy.question for q in item.questions)]
    if missing:
        raise ValueError(f"items {missing} do not ask {task!r}'s deciding question "
                         f"{policy.question!r}")
    decisions.triggers((), question=policy.question, escalate_on=policy.escalate_on,
                       margins=dict(policy.margins), answers=policy.escalate_answers)
    return policy


class _Settled(NamedTuple):
    """One triggered item's escalation, and its merged result when the hop's
    answer replaced the base's (None otherwise)."""

    escalation: decisions.Escalation
    merged: decisions.ItemResult | None = None


class _HopDone(NamedTuple):
    """What a hop did: each triggered item's `_Settled`, keyed by batch
    index; the hop's ledger rows and `CallRecord`s; every `(provider id,
    model)` that answered one of its calls; and the backend of every result
    it returned."""

    settled: dict[int, _Settled]
    rows: tuple[dict, ...] = ()
    calls: tuple[decisions.CallRecord, ...] = ()
    served: tuple[tuple[str, str], ...] = ()
    backends: tuple[str, ...] = ()


def _skipped(found: Sequence[decisions.Trigger], base: decisions.Decision,
             detail: str) -> dict[int, _Settled]:
    """Each of `found` skipped, with `detail`: no hop ran for it."""
    return {t.index: _Settled(decisions.Escalation(t.index, t.trigger, t.margin,
                                                   base.items[t.index], "skipped", detail))
            for t in found}


async def _escalate(call: _Call, items: tuple[decisions.Item, ...], base: decisions.Decision,
                    found: tuple[decisions.Trigger, ...], policy: store.routing.TaskPolicy,
                    escalation: Escalator, chain: Sequence[Stage]) -> decisions.Decision:
    """The hop after the chain (01d §5.3): await the escalator once, then
    hand `found` to what it returned, and lay the result over `base`.

    The base is paid for, so nothing the escalator does loses it: a thunk
    that raises is every trigger skipped, `unresolved: <kind>` (its kind,
    never its text); `(None, why)` is every trigger skipped with `why`, for
    either kind of policy. Only a value of the wrong shape is a caller bug:
    a `Resolver` for a role policy or a resolution for a `CALLER` one is a
    `TypeError`, and a resolution of another task or operation a
    `ValueError` -- each raised before any hop call."""
    try:
        target, why = await escalation()
    except Exception as exc:  # noqa: BLE001 - the paid base answers must survive
        kind = exc.kind if isinstance(exc, LLMError) else type(exc).__name__
        return _escalated(base, found, _HopDone(_skipped(found, base, f"unresolved: {kind}")))
    if target is None:
        return _escalated(base, found, _HopDone(_skipped(found, base, why)))
    if policy.escalate_to == store.routing.CALLER:
        if isinstance(target, ResolvedInference) or not callable(target):
            raise TypeError(f"{call.task!r} escalates to a caller resolver, "
                            f"and the escalator returned {type(target).__name__}")
        return _escalated(base, found, await _caller_hop(call, items, base, found, policy, target))
    if not isinstance(target, ResolvedInference):
        raise TypeError(f"{call.task!r} escalates to the {policy.escalate_to!r} role, "
                        f"and the escalator returned {type(target).__name__}")
    if target.task != call.task or target.operation != "decide":
        raise ValueError(f"a {target.operation!r} resolution of {target.task!r} cannot "
                         f"escalate {call.task!r}")
    return _escalated(base, found, await _role_hop(call, items, base, found, policy, target, chain))


def _hop_stage(target: ResolvedInference) -> Stage | None:
    """The role hop's one stage (01d §5.3): the escalation primary ALONE --
    no fallback -- with no retries, its backend by its `decision_mode`, and
    its account marked as the hop. None when nothing resolved, or the
    primary can neither generate nor decide natively. Never native-first
    (01c): the stage is built here, not by `stages`."""
    chain = target.chain
    if chain is None or not target.attempts:
        return None
    mode = target.attempts[0].decision_mode
    if mode not in decisions.BACKENDS:
        return None
    return Stage(mode, wire.Chain(chain.primary.with_account(hop=decisions.HOP_ESCALATION)), 0)


def _on_dead_connection(base: decisions.Decision, chain: Sequence[Stage],
                        provider_id: str) -> bool:
    """Whether the base met a failure every call on `provider_id`'s
    connection would meet (`_connection_wide`) and `provider_id` is the
    connection of one of the base's stages: a hop there would only meet it
    again."""
    if not any(isinstance(e, LLMError) and _connection_wide(e) for e in base.errors):
        return False
    return any(_same_connection(provider_id, target.provider_id)
               for stage in chain for target in stage.chain.attempts)


def _take(found: tuple[decisions.Trigger, ...], items: tuple[decisions.Item, ...], most: int,
          mode: str) -> tuple[tuple[decisions.Trigger, ...], tuple[decisions.Trigger, ...]]:
    """`(taken, capped)` of `found`, in its priority order (01d §5.3, S6):
    at most `most`, and on a structured hop only those `decisions.chunks`
    puts in its FIRST chunk -- so the hop is one structured call (plus at
    most one prompt-only re-send)."""
    taken = found[:most]
    if mode == STRUCTURED and taken:
        taken = taken[:len(decisions.chunks([items[t.index] for t in taken])[0][1])]
    return taken, found[len(taken):]


def _merge(before: decisions.ItemResult, hop: decisions.ItemResult, question: str,
           reads_declines: bool) -> tuple[decisions.ItemResult | None, str]:
    """The item as the hop leaves it (01d §5.3), or None and why it stays.

    Replaced only when the hop's `question` answer is a value, or a decline
    (`abstained`, `refused`) on a task that reads one. The deciding answer,
    rationale, backend and server are then the hop's, and every other
    question keeps the base's answer unless the hop gave it a VALUE -- not
    merely one it `was_read`: a garbled or missing key reads as an
    `unreadable` that `was_read`, and it must not replace a good base answer
    (the plan's Decision 7; each question of an item stands alone)."""
    answer = hop.answers[question]
    if answer.answer is None:
        if answer.reason not in ("abstained", "refused"):
            return None, (f"{answer.reason}: {answer.detail}" if answer.detail
                          else answer.reason)
        if not reads_declines:
            return None, decisions.FAILED_DECLINED
    merged = {qid: (hop.answers[qid] if qid == question or hop.answers[qid].answer is not None
                    else old)
              for qid, old in before.answers.items()}
    return replace(hop, answers=merged), ""


def _settle_one(trigger: decisions.Trigger, before: decisions.ItemResult,
                result: decisions.ItemResult | None, error: LLMError | None,
                policy: store.routing.TaskPolicy,
                sent: tuple[()] | tuple[str, str, str]) -> _Settled:
    """One taken item's escalation, from the hop's `result` for it (None when
    its unit failed with `error`, or a caller gave none). `sent` names the
    target a failed role hop was sent (`()` for a caller)."""
    def record(outcome: str, detail: str,
               served: tuple[()] | tuple[str, str, str]) -> decisions.Escalation:
        return decisions.Escalation(trigger.index, trigger.trigger, trigger.margin, before,
                                    outcome, detail, served)

    if result is None:
        detail = (f"{error.kind}: {error.detail}" if error is not None
                  else decisions.FAILED_NO_RESULT)
        return _Settled(record("failed", detail, sent))
    merged, why = _merge(before, result, policy.question, policy.reads_declines)
    if merged is None:
        return _Settled(record("failed", why, result.served or sent))
    return _Settled(record("answered", "", merged.served), merged)


async def _role_hop(call: _Call, items: tuple[decisions.Item, ...], base: decisions.Decision,
                    found: tuple[decisions.Trigger, ...], policy: store.routing.TaskPolicy,
                    target: ResolvedInference, chain: Sequence[Stage]) -> _HopDone:
    """The hop to the policy's role (01d §5.3, §5.4): its one stage, sent
    the triggered items that are not on the model that answered them
    (`same_model`) and that fit the cap, through the stage's backend --
    the same dispatch `run_stages` makes, with a `_Call` whose `positions`
    are the batch indices taken, so its records and captures name the
    batch. A failed unit leaves its items' originals standing."""
    stage = _hop_stage(target)
    if stage is None:
        return _HopDone(_skipped(found, base, decisions.SKIPPED_INCAPABLE))
    primary = stage.chain.primary
    if _on_dead_connection(base, chain, primary.provider_id):
        return _HopDone(_skipped(found, base, decisions.SKIPPED_DEAD_CONNECTION))
    server = (primary.provider_id, primary.model)
    same = tuple(t for t in found if base.items[t.index].served[1:] == server)
    taken, capped = _take(tuple(t for t in found if t not in same), items,
                          policy.escalate_max, stage.mode)
    settled = {**_skipped(same, base, decisions.SKIPPED_SAME_MODEL),
               **_skipped(capped, base, decisions.SKIPPED_CAP)}
    if not taken:
        return _HopDone(settled)
    got = await _BACKENDS[stage.mode](
        tuple(items[t.index] for t in taken),
        replace(call, chain=stage.chain, positions=tuple(t.index for t in taken)))
    # `got.results` and `got.failed` are by SUBSET position p (the hop's own
    # item order): item p is the batch's `taken[p].index`. Only `got.calls`,
    # built through `call.positions`, already name batch indices.
    errors = {p: error for unit, error in got.failed for p in unit}
    sent = _server(primary)
    for p, trigger in enumerate(taken):
        settled[trigger.index] = _settle_one(trigger, base.items[trigger.index],
                                             got.results[p], errors.get(p), policy, sent)
    return _HopDone(settled, got.rows, got.calls, got.served,
                    tuple(r.backend for r in got.results if r is not None))


def _checked_result(result: object, item: decisions.Item) -> None:
    """A caller result is None, or an `ItemResult` from one of `BACKENDS`
    whose question ids are exactly `item`'s (so `_merge` reads every one)."""
    if result is None:
        return
    if not isinstance(result, decisions.ItemResult) or result.backend not in decisions.BACKENDS:
        raise ValueError(f"a caller resolver's result must be an ItemResult from one of "
                         f"{decisions.BACKENDS}, not {result!r}")
    if set(result.answers) != {q.id for q in item.questions}:
        raise ValueError(f"a caller resolver's result answers {sorted(result.answers)}, "
                         f"not the item's questions")


def _checked_reply(reply: object, taken: tuple[decisions.Trigger, ...],
                   items: tuple[decisions.Item, ...]) -> decisions.ResolverReply:
    """A caller resolver's reply, held to its contract (01d §5.2): one result
    per item handed over (`_checked_result`), and every row it filed marked
    as the hop's -- a row that does not say so would be counted as the
    chain's own call. A `ValueError` otherwise."""
    if not isinstance(reply, decisions.ResolverReply):
        raise ValueError(f"a caller resolver returns a ResolverReply, not {reply!r}")
    if len(reply.results) != len(taken):
        raise ValueError(f"a caller resolver was handed {len(taken)} items and returned "
                         f"{len(reply.results)} results")
    for trigger, result in zip(taken, reply.results, strict=True):
        _checked_result(result, items[trigger.index])
    if not all(isinstance(row, dict) and row.get("hop") == decisions.HOP_ESCALATION
               for row in reply.rows):
        raise ValueError("every row a caller resolver files carries hop: escalation")
    return reply


def _row_record(call: _Call, row: dict, taken: tuple[decisions.Trigger, ...]
                ) -> decisions.CallRecord:
    """A caller resolver's ledger row as a `CallRecord` of the hop: every item
    it was handed, its backend as the row says (else `structured`, which a
    tool loop ending in a generated answer stamps), and its error kind."""
    mode = row.get("decision_mode")
    return decisions.CallRecord(
        stage=call.stage, mode=mode if mode in decisions.BACKENDS else STRUCTURED,
        items=tuple(t.index for t in taken), row=row,
        error_kind=str(row.get("error") or "") if row.get("status") == "error" else "",
        hop=decisions.HOP_ESCALATION)


async def _caller_hop(call: _Call, items: tuple[decisions.Item, ...], base: decisions.Decision,
                      found: tuple[decisions.Trigger, ...], policy: store.routing.TaskPolicy,
                      resolver: Resolver) -> _HopDone:
    """The hop to a caller's `Resolver` (01d §5.2): at most `escalate_max`
    items, once, with no same-model check (the resolver is not one model).
    An `LLMError` it raises leaves every original standing; a cancel, and
    anything else, passes through."""
    taken, capped = found[:policy.escalate_max], found[policy.escalate_max:]
    settled = _skipped(capped, base, decisions.SKIPPED_CAP)
    try:
        reply = await resolver(tuple(items[t.index] for t in taken), taken)
    except LLMError as exc:
        for trigger in taken:
            settled[trigger.index] = _settle_one(trigger, base.items[trigger.index], None,
                                                 exc, policy, ())
        return _HopDone(settled)
    reply = _checked_reply(reply, taken, items)
    for trigger, result in zip(taken, reply.results, strict=True):
        settled[trigger.index] = _settle_one(trigger, base.items[trigger.index], result,
                                             None, policy, ())
    answered = [r for r in reply.results if r is not None]
    return _HopDone(settled, reply.rows,
                    tuple(_row_record(call, row, taken) for row in reply.rows),
                    tuple((r.served[1], r.served[2]) for r in answered if r.served),
                    tuple(r.backend for r in answered))


def _escalated(base: decisions.Decision, found: tuple[decisions.Trigger, ...],
               hop: _HopDone) -> decisions.Decision:
    """`base` with the hop laid over it (01d §5.5): each replaced item, the
    hop's rows after the base's in `usage` and its records after the base's
    in `calls`, `served` the union in first-answered order of the base's
    and every server that answered a hop call (whether or not the merge kept
    its answer), and `Decision.escalations` in `found`'s priority order.
    `errors` stay the base's: a failed hop is not an unanswered unit.
    `provider`/`model` stand only when one route answered every call, and
    `backend` only when one backend did -- `run_stages`' own rule."""
    items = list(base.items)
    for index, done in hop.settled.items():
        if done.merged is not None:
            items[index] = done.merged
    served = dict.fromkeys(base.served)
    served.update(dict.fromkeys(hop.served))
    provider, model = base.provider, base.model
    if len(served) != len(base.served):
        provider, model = next(iter(served)) if len(served) == 1 else ("", "")
    backend = base.backend if all(b == base.backend for b in hop.backends) else ""
    return replace(base, items=tuple(items), backend=backend, provider=provider, model=model,
                   usage=base.usage + hop.rows, served=tuple(served),
                   calls=base.calls + hop.calls,
                   escalations=tuple(hop.settled[t.index].escalation for t in found))


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


# ==== the tool loop (01g-S4; spec 3.6-3.11) ====
#
# `run_tools` is the one door to a bounded loop of model turns and tool calls.
# The caller supplies the tools, the executor that runs them and the run id;
# the loop sends each turn through the facade (`client.complete`, spelled only
# in this module) under a meter of its own, checks the run's budget before
# every send and every execution, and hands back a `tool_calls.LoopResult`.
# A tool never runs inside an adapter: the loop hands each validated call to
# the caller's `execute`.

#: Handed each turn once it settles (spec 3.10), outside its meter and
#: guarded: the messages as sent (the tool definitions first, as one system
#: message), the outcome `{finish, calls: [{name, arguments}], text_chars}`,
#: and the target that answered (`llm.ATTEMPTED`).
TurnCapture = Callable[[list[dict], dict, wire.Target], Awaitable[None]]

#: The tasks running a loop now: a loop does not start inside another (spec
#: 3.12, no recursion). A set of tasks rather than a `ContextVar`, which an
#: async generator does not carry across its consumer's steps.
_LOOPING: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()


@dataclass(frozen=True)
class Price:
    """What one attempt costs per token, as the spend guard prices it (spec
    3.9): its catalog's per-token prices (`basis` "catalog"), or the user's
    rates entry (`basis` "rates", priced by `pricing.estimate`)."""
    basis: Literal["catalog", "rates"]
    prompt: float = 0.0
    completion: float = 0.0
    entry: dict | None = None


def price_for(attempt: Attempt) -> Price | None:
    """`attempt`'s price, in the model-test preview's order: its cached
    catalog row's per-token prices (a stated 0 is a free model), else -- only
    for a provider that does not report its own price -- the user's rates
    (`pricing.rate_for_call`: the model's own, then `pricing.json`), else None
    (unpriceable). For a provider that reports its price the ledger never
    prices its calls from the user's rates, so a `"": 0` default meant for
    local models must not project a billed turn at nothing. Reads the store:
    run it off the event loop."""
    row = store.llm_connections.cached_row(attempt.provider_id, attempt.model)
    if isinstance(row, dict):
        prompt, completion = probes._rate(row.get("prompt")), probes._rate(row.get("completion"))
        if prompt is not None and completion is not None:
            return Price("catalog", prompt=prompt, completion=completion)
    preset = store.inference.providers.PRESETS.get(attempt.provider_preset)
    if preset is not None and preset.reports_price:
        return None
    entry = store.pricing.rate_for_call(store.pricing.read_pricing(),
                                        store.pricing.provider_rates(),
                                        provider_id=attempt.provider_id, model=attempt.model)
    if entry is None or store.pricing.estimate(entry, prompt_tokens=1,
                                               completion_tokens=1) is None:
        return None
    return Price("rates", entry=entry)


def prices_for(resolved: ResolvedInference) -> dict[tuple[str, str], Price | None]:
    """Every attempt's `price_for`, keyed by `(provider_id, model)` -- both
    the attempt's and the model its target sends. Reads the store."""
    out: dict[tuple[str, str], Price | None] = {}
    for attempt in resolved.attempts:
        price = price_for(attempt)
        out[(attempt.provider_id, attempt.model)] = price
        out[(attempt.target.provider_id, attempt.target.model)] = price
    return out


def _cost(price: Price | None, prompt: float, completion: float) -> float | None:
    """`prompt` and `completion` tokens at `price`, or None unpriced."""
    if price is None:
        return None
    if price.basis == "catalog":
        return prompt * price.prompt + completion * price.completion
    return store.pricing.estimate(price.entry, prompt_tokens=math.ceil(prompt),
                                  completion_tokens=math.ceil(completion))


def _prompt_count(messages: list[dict], defs: Sequence[dict]) -> int:
    """`messages` and `defs` counted as the guard counts a prompt
    (`tokens.count_if_loaded`, which never starts an encoder load)."""
    text = "\n".join([json.dumps(m, ensure_ascii=False, default=str) for m in messages]
                     + [json.dumps(d, ensure_ascii=False) for d in defs])
    return store.tokens.count_if_loaded(text)


def tool_run_refusal(task: str, resolved: ResolvedInference, budget: tool_calls.RunBudget,
                     prices: dict[tuple[str, str], Price | None] | None = None
                     ) -> tool_calls.RunRefused | None:
    """Why a tool run on `resolved` would be refused before anything is sent
    (spec 3.9), or None: with a spend ceiling, an attempt with no price this
    side knows (`price_for`) or whose output cap is not sent (`cap_sent`:
    without it the completion has no bound). The same check `run_tools`
    makes at entry, as a preflight a `def` route runs BEFORE it reserves, so
    an unpriceable run is a 409 rather than a 202 that fails. `prices`, when
    not given, are read from the store."""
    if budget.spend_ceiling_usd is None:
        return None
    chain = call_chain(resolved, max_tokens=budget.max_output_tokens)
    known = prices if prices is not None else prices_for(resolved)
    for target in chain.attempts:
        name = f"{target.model} on {target.label}"
        if known.get((target.provider_id, target.model)) is None:
            return tool_calls.RunRefused(
                "unpriceable", f"{task}: {name} has no price this side knows -- set rates "
                               "for it, or run without a spend ceiling")
        if not cap_sent(target):
            return tool_calls.RunRefused(
                "unpriceable", f"{task}: {name} cannot be sent an output cap, so a turn "
                               "has no price bound -- run without a spend ceiling")
    return None


class _WallSpentError(LLMError):
    """The run's own wall clock ran out while a turn was in flight: a budget
    stop, not a failure, so the turn's meter files `aborted`."""

    #: `store.usage.NOT_A_FAILURE`, as absorb's `BudgetRefused` declares it.
    llm_call_failed = False


class _ToolTimeoutError(Exception):
    """A tool's wait ran out (`deadline.bounded`'s overrun)."""


def finalize_message(schema: dict | None, *, stopped: bool) -> dict:
    """The finalize turn's instruction (spec 3.7 step 7), as the user message
    appended after the run's last results: `templates/tools/finalize.j2`,
    carrying `schema` (rendered with `schema_json`) when there is one. Reads a
    template, so callers run it in a worker thread."""
    return {"role": "user",
            "content": prompts.render("tools/finalize.j2", schema=schema, stopped=stopped)}


def _check_loop_prompt(messages: list[dict]) -> None:
    """A prompt a loop can extend: not ending in an assistant turn (a
    prefill), not chosen per attempt, and -- a `PreparedMessages` -- frozen,
    so turn 2 can append to it (`snapshot` raises for one that is not)."""
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant":
        raise ValueError("a tool loop cannot start from a prompt ending in an assistant turn")
    if isinstance(messages, model_guidance.PreparedMessages):
        if messages.tailed:
            raise ValueError("a tool loop cannot carry per-attempt tails")
        messages.snapshot()


def _check_loop_route(task: str, resolved: ResolvedInference) -> None:
    """A route that requires tools, or lists them as optional
    (`routing.TOOLS_OPTIONAL`), on a primary not KNOWN unable to call them."""
    route = store.routing.route(task)
    if route is None or not ("tools" in route.requires
                             or route.key in store.routing.TOOLS_OPTIONAL):
        raise ValueError(f"{task!r} is on a route that neither requires tools nor offers them")
    if resolve.known_lacks(resolved, "tools"):
        raise ValueError(f"{task!r} resolved to a model known unable to call tools")


def _loop_refusal(task: str, messages: list[dict], toolset: tool_calls.Toolset,
                  resolved: ResolvedInference, run_id: str, final_schema: dict | None,
                  tool_choice: str) -> None:
    """Every refusal `run_tools` makes before anything is sent (spec 3.7), each
    a `ValueError`, so no meter opens."""
    _generating(task, resolved)
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("a tool loop runs under the caller's run id, and none was given")
    if not isinstance(toolset, tool_calls.Toolset) or not toolset.tools:
        raise ValueError("a tool loop needs at least one tool")
    if tool_choice not in ("auto", "required"):
        raise ValueError(f"a loop's tool_choice is auto or required, not {tool_choice!r}")
    if final_schema is not None:
        schemas.check(final_schema)
    _check_loop_prompt(messages)
    _check_loop_route(task, resolved)
    if any(a.target.kind == "claude" for a in resolved.attempts):
        long = [t.name for t in toolset.tools if len(t.name) > claude_agent.MAX_TOOL_NAME]
        if long:
            raise ValueError(f"a Claude subscription takes tool names of at most "
                             f"{claude_agent.MAX_TOOL_NAME} characters: {', '.join(long)}")
    current = asyncio.current_task()
    if current is not None and current in _LOOPING:
        raise ValueError("a tool loop cannot start inside another")


@dataclass(frozen=True)
class _Turn:
    """One settled model turn."""
    turn: int
    text: str
    calls: tuple[tool_calls.ToolCall, ...]
    finish: str
    opaque: dict | None
    target: wire.Target | None
    #: Whether the turn streamed visible text (01g-C6).
    shown: bool = False


class _Loop:
    """One run's state; `events` runs it (spec 3.7)."""

    def __init__(self, task: str, messages: list[dict], *, toolset: tool_calls.Toolset,
                 execute: tool_calls.Execute, client: LLMClient,
                 resolved: ResolvedInference, budget: tool_calls.RunBudget, run_id: str,
                 attribution: dict, scene_identity: str,
                 cancelled: Callable[[], bool] | None, final_schema: dict | None,
                 tool_choice: str, capture: TurnCapture | None,
                 prices: dict[tuple[str, str], Price | None] | None = None,
                 streaming: bool = False, decline_after_text: bool = False,
                 reasoning: llm_reasoning.Buffer | None = None):
        prices = prices or {}
        #: 01g-C6: each turn through `client.stream`, the caller's display
        #: reasoning buffer in each turn's holder, and visible text final.
        self.streaming, self.decline_after_text = streaming, decline_after_text
        self.reasoning = reasoning
        self.shown = False
        self.task, self.base, self.toolset, self.execute = task, messages, toolset, execute
        self.client, self.resolved, self.budget, self.run_id = client, resolved, budget, run_id
        self.attribution, self.scene_identity = attribution, scene_identity
        self.cancelled, self.final_schema, self.capture = cancelled, final_schema, capture
        self.choice = tool_choice
        self.defs = toolset.definitions()
        self.appended: list[dict] = []
        self.rows: list[dict] = []
        self.trace: list[tool_calls.TraceEntry] = []
        self.proposals: list[dict] = []
        self.emit: Callable[[object], None] = lambda _item: None
        self.turns = self.calls_used = self.result_chars = self.minted = 0
        self.finalized = self.fallen = False
        self.writers: set[tuple[str, str]] = set()
        self.limit = self.text = self.root = ""
        self.wall = self.call_budget = 0.0
        self.t0 = time.monotonic()
        self._hist: list[dict] | None = None
        self._hist_len = 0
        #: The spend axis (01g-S5): each attempt's price, read at entry, each
        #: sent turn's `(projection, row)`, and the prompt count the next
        #: projection builds on.
        self.prices = prices
        self.spends: list[tuple[float, dict | None]] = []
        self.last_prompt: float | None = None
        self.next_prompt = 0.0
        self.sent_len = 0

    # ---- the run ----
    async def events(self) -> AsyncGenerator[tool_calls.LoopEvent, None]:
        """The run, as events (spec 3.8): `text` deltas as they arrive, a
        `turn_end` per turn, `tool_start`/`tool_end` per call, and `done`
        last, after every meter has filed. The run itself is driven in a task
        of its own (`_drive`), so that while a turn or a tool is in flight
        this yields an empty `text` every `llm.HEARTBEAT_INTERVAL` -- a stream
        is never silent past the interval an SSE consumer's guard protects."""
        self.root, self.wall, self.call_budget = await asyncio.to_thread(self._pin)
        queue: asyncio.Queue = asyncio.Queue()
        self.emit = queue.put_nowait
        runner = asyncio.create_task(self._drive())
        try:
            while True:
                item = await _next_or_beat(queue)
                if item is None:
                    yield tool_calls.LoopEvent("text", turn=self.turns, delta="")
                    continue
                if isinstance(item, BaseException):
                    raise item
                assert isinstance(item, tool_calls.LoopEvent)
                yield item
                if item.kind == "done":
                    return
        finally:
            await _stopped(runner)

    async def _drive(self) -> None:
        """Every step of the run, each event handed to `events`; its result as
        `done`, or the exception that ended it."""
        me = asyncio.current_task()
        if me is not None:
            _LOOPING.add(me)
        try:
            result: tool_calls.LoopResult | None = None
            while result is None:
                result = await self._step()
        except BaseException as exc:
            # Handed to the consumer, which raises it there.
            self.emit(exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        log.info("tool run %s (%s) ended %s%s: %d turns, %d tool calls, %.1fs",
                 self.run_id, self.task, result.status,
                 f" on {result.limit}" if result.limit else "", self.turns,
                 self.calls_used, time.monotonic() - self.t0)
        self.emit(tool_calls.LoopEvent("done", turn=self.turns, result=result))

    def _pin(self) -> tuple[str, float, float]:
        """The store root, the run's wall and the per-call ceiling, read once
        off the event loop (`store.home` can read the bootstrap pointer)."""
        wall = self.budget.wall_seconds
        call = store.config.llm_call_budget()
        return str(store.home()), (call if wall is None else wall), call

    def _root_held(self) -> bool:
        return str(store.home()) == self.root

    async def _step(self) -> tool_calls.LoopResult | None:
        self._check_cancelled()
        stop = self._turn_refusal()
        if stop:
            self.limit = stop
            return await self._finalize_or_stop()
        if not await asyncio.to_thread(self._root_held):
            return self._result("failed", error=LLMError(
                "bad_response", "the store moved during the run", code="store_moved"))
        turn = await self._turn(final=False)
        if turn is None:
            return await self._finalize_or_stop()
        if isinstance(turn, tool_calls.LoopResult):
            return turn
        return await self._settle(turn)

    def _check_cancelled(self) -> None:
        if self.cancelled is not None and self.cancelled():
            raise asyncio.CancelledError("the run was cancelled")

    def _elapsed(self) -> float:
        return time.monotonic() - self.t0

    def _wall_short(self, need: float) -> bool:
        return self.wall > 0 and self._elapsed() + need > self.wall

    def _turn_refusal(self) -> str:
        """The limit that refuses the next tool turn (spec 3.9), or ""."""
        if self.limit:
            return self.limit
        reserve = 1 if self.budget.reserve_final and not self.finalized else 0
        if self.turns + 1 + reserve > self.budget.max_turns:
            return "turns"
        if self._wall_short(tool_calls.MIN_TURN_SECONDS):
            return "wall"
        return ""

    def _final_fits(self) -> bool:
        return (self.budget.reserve_final and not self.finalized
                and self.turns + 1 <= self.budget.max_turns
                and not self._wall_short(tool_calls.MIN_TURN_SECONDS))

    async def _finalize_or_stop(self) -> tool_calls.LoopResult:
        if self._final_fits():
            return await self._finalize(stopped=True)
        return self._result("budget_exhausted")

    async def _finalize(self, *, stopped: bool) -> tool_calls.LoopResult:
        """The finalize turn (spec 3.7 step 7)."""
        self.finalized = True
        note = await asyncio.to_thread(partial(finalize_message, self.final_schema,
                                               stopped=stopped))
        turn = await self._turn(final=True, extra=note)
        if turn is None:
            return self._result("budget_exhausted")
        if isinstance(turn, tool_calls.LoopResult):
            return turn
        status: Literal["completed", "budget_exhausted", "failed"] = (
            "budget_exhausted" if stopped else "completed")
        if self.final_schema is None:
            return self._result(status)
        value = schemas.find_value(turn.text)
        if isinstance(value, dict) and tool_calls.conforms(value, self.final_schema):
            return self._result(status, final=value)
        return self._result("failed", error=LLMError(
            "bad_response", "the final answer did not match its schema",
            code="final_unreadable"))

    async def _settle(self, turn: _Turn) -> tool_calls.LoopResult | None:
        """A turn's calls answered, or the run ended by its answer."""
        if not turn.calls:
            return await self._answered(turn)
        if self.decline_after_text and turn.shown:
            # Visible text is final (01g-C6): the calls it ended with are
            # declined, and no turn follows it.
            for call in turn.calls:
                self._trace(turn.turn, "tool", call.name, call.id, ok=False,
                            note="declined: after_text")
            return self._result("completed", declined=turn.calls)
        if turn.finish != "length":
            ended = self._terminal(turn)
            if ended is not None:
                return ended
        results = [await self._one(turn, call) for call in turn.calls]
        self._append(turn, results)
        if self.calls_used >= self.budget.max_tool_calls:
            self.limit = self.limit or "tool_calls"
        self._note_switch(turn.target)
        return None

    async def _answered(self, turn: _Turn) -> tool_calls.LoopResult:
        if self.final_schema is None:
            return self._result("completed")
        value = schemas.find_value(turn.text)
        if isinstance(value, dict) and tool_calls.conforms(value, self.final_schema):
            return self._result("completed", final=value)
        if not self.finalized and self.turns + 1 <= self.budget.max_turns:
            self._extend([{"role": "assistant", "content": turn.text}])
            return await self._finalize(stopped=False)
        return self._result("failed", error=LLMError(
            "bad_response", "the final answer did not match its schema",
            code="final_unreadable"))

    def _terminal(self, turn: _Turn) -> tool_calls.LoopResult | None:
        """A valid call to a terminal tool ends the run, unexecuted: handed
        back as `final_call`, the turn's other calls recorded `not_run`."""
        for call in turn.calls:
            spec = self.toolset.get(call.name)
            if spec is None or not spec.terminal or self._problem(call, spec):
                continue
            for other in turn.calls:
                if other is not call:
                    self._trace(turn.turn, "tool", other.name, other.id, ok=False,
                                note="not_run")
            return self._result("completed", final_call=call)
        return None

    # ---- one model turn ----
    async def _turn(self, *, final: bool, extra: dict | None = None
                    ) -> _Turn | tool_calls.LoopResult | None:
        """One model turn, or None when the spend ceiling refused it unsent
        (`limit` says `spend`)."""
        chain, notes = self._chain(final)
        choice = "none" if final else self._choice(chain, notes)
        sending = self._outgoing(extra)
        projection = await self._projected(chain, sending, extra)
        if projection is not None and self._spent() + projection > (
                self.budget.spend_ceiling_usd or 0.0):
            self.limit = self.limit or "spend"
            return None
        k = self.turns = self.turns + 1
        self.shown = False
        if extra is not None:
            self.appended.append(extra)
        self.sent_len = len(self.appended)
        collector = tool_calls.Collector()
        started = time.monotonic()
        try:
            text, holder = await self._metered(k, chain, sending, choice, collector, final,
                                               projection)
        except _WallSpentError:
            self._trace(k, "model", ok=False, note="wall", started=started)
            self.limit = "wall"
            return self._result("budget_exhausted")
        except LLMError as exc:
            self._trace(k, "model", ok=False, note=exc.kind, started=started)
            if k == 1:
                raise
            return self._result("failed", error=exc)
        return await self._settled_turn(k, text, holder, collector, final, notes, sending,
                                        started)

    async def _settled_turn(self, k: int, text: str, holder: dict,
                            collector: tool_calls.Collector, final: bool, notes: list[str],
                            sending: list[dict], started: float) -> _Turn:
        target = holder.get(llm.ATTEMPTED)
        target = target if isinstance(target, wire.Target) else None
        calls = tuple(self._named(c) for c in collector.calls()) if not final else ()
        turn = _Turn(k, text, calls, collector.finish_reason, collector.opaque(), target,
                     self.shown)
        self.text = text
        if target is not None and self._switched_to(target):
            notes.append("fell back")
        self._trace(k, "model", target.model if target else "", chars=len(text),
                    note=", ".join(notes), started=started)
        await self._captured(sending, turn)
        if text and not self.streaming:
            self.emit(tool_calls.LoopEvent("text", turn=k, delta=text))
        self.emit(tool_calls.LoopEvent("turn_end", turn=k, finish=turn.finish,
                                       interstitial=bool(calls)))
        return turn

    # ---- the spend ceiling (01g-S5; spec 3.9) ----
    async def _projected(self, chain: wire.Chain, sending: list[dict],
                         extra: dict | None) -> float | None:
        """What the next send could cost, as the guard prices it: None with no
        ceiling. Prompt tokens are counted in a worker thread with the margin
        -- the whole prompt on the first send, else what was appended since
        the last, on top of that send's reported (else projected) count --
        and the completion is the turn's cap; the maximum over the attempts
        that could serve it."""
        if self.budget.spend_ceiling_usd is None:
            return None
        if self.last_prompt is None:
            counted = await asyncio.to_thread(_prompt_count, list(sending), self.defs)
            prompt = counted * tool_calls.PROJECTION_MARGIN
        else:
            fresh = [*self.appended[self.sent_len:], *([extra] if extra is not None else [])]
            counted = await asyncio.to_thread(_prompt_count, fresh, ())
            prompt = self.last_prompt + counted * tool_calls.PROJECTION_MARGIN
        self.next_prompt = prompt
        costs = [_cost(self.prices.get((t.provider_id, t.model)), prompt,
                       clamp_to_max_output(t, self.budget.max_output_tokens))
                 for t in chain.attempts]
        known = [c for c in costs if c is not None]
        # Every attempt was priced at entry (`tool_run_refusal`); one that
        # still is not counts as the dearest known, never as free.
        return max(known) if known else float("inf")

    def _spent(self) -> float:
        """Every sent turn's cost as the guard prices it: its row's counts at
        its attempt's price, else that turn's projection. Never a reported
        figure (`cost_usd`) -- the guard keeps one unit."""
        total = 0.0
        for projection, row in self.spends:
            cost = self._row_cost(row)
            total += cost if cost is not None else projection
        return total

    def _row_cost(self, row: dict | None) -> float | None:
        if not isinstance(row, dict):
            return None
        prompt, completion = row.get("prompt_tokens"), row.get("completion_tokens")
        if not (isinstance(prompt, int) and isinstance(completion, int)):
            return None
        provider = row.get("provider_id", "")
        price = (self.prices.get((provider, row.get("requested_model", "")))
                 or self.prices.get((provider, row.get("model", ""))))
        return _cost(price, prompt, completion)

    def _note_spend(self, projection: float | None, row: dict | None) -> None:
        if projection is None:
            return
        self.spends.append((projection, row))
        prompt = row.get("prompt_tokens") if isinstance(row, dict) else None
        self.last_prompt = (float(prompt) if isinstance(prompt, int)
                            and not isinstance(prompt, bool) else self.next_prompt)

    def _turn_seconds(self) -> tuple[float | None, bool]:
        """This turn's wait (spec 3.9, joined): `min(llm_call_budget, wall
        left)`, either absent at `<= 0`, and whether the run's wall is the
        binding one."""
        left = self.wall - self._elapsed() if self.wall > 0 else None
        if self.call_budget > 0 and (left is None or self.call_budget <= left):
            return self.call_budget, False
        return left, left is not None

    async def _metered(self, k: int, chain: wire.Chain, sending: list[dict], choice: str,
                       collector: tool_calls.Collector, final: bool,
                       projection: float | None = None) -> tuple[str, dict]:
        """One turn through the facade, under its own meter carrying the run
        id and the turn (01g-C3), bounded by `_turn_seconds`."""
        schema = self.final_schema if final else None
        seconds, by_wall = self._turn_seconds()
        meter = store.usage.meter(self.task, **self.attribution, run_id=self.run_id,
                                  loop_turn=k)
        try:
            with meter as m:
                m.usage[tool_calls.KEY] = collector

                def overrun(limit: float) -> LLMError:
                    if by_wall:
                        return _WallSpentError("timeout", "the run's wall clock ran out")
                    error = LLMError("timeout", f"the reply did not finish within "
                                                f"{limit:g}s — giving up")
                    self._note_overrun(m.usage, chain, error)
                    return error

                async def resend(again: wire.Chain) -> str:
                    with _counting_earlier(m.usage):
                        return await self.client.complete(sending, again, m.usage, schema=schema,
                                                          tools=self.defs, tool_choice=choice)

                async def reopen(again: wire.Chain) -> AsyncGenerator[str, None]:
                    with _counting_earlier(m.usage):
                        async with aclosing(self.client.stream(
                                sending, again, m.usage, schema=schema, tools=self.defs,
                                tool_choice=choice)) as deltas:
                            async for delta in deltas:
                                yield delta

                if self.reasoning is not None:
                    m.usage[llm_reasoning.KEY] = self.reasoning
                try:
                    if self.streaming:
                        source = (self.client.stream(sending, chain, m.usage, tools=self.defs,
                                                     tool_choice=choice) if schema is None
                                  else _streamed_structured(self.client.stream(
                                      sending, chain, m.usage, schema=schema, tools=self.defs,
                                      tool_choice=choice), reopen))
                        text = await self._streamed(k, source, seconds, overrun)
                    else:
                        work: Awaitable[str] = (
                            self.client.complete(sending, chain, m.usage, tools=self.defs,
                                                 tool_choice=choice) if schema is None
                            else _joined_structured(self.client.complete(
                                sending, chain, m.usage, schema=schema, tools=self.defs,
                                tool_choice=choice), resend))
                        text = await deadline.bounded(work, seconds, overrun)
                finally:
                    m.tool_calls = len(collector.calls())
        finally:
            if meter.row is not None:
                self.rows.append(meter.row)
            self._note_spend(projection, meter.row)
        return text, m.usage

    async def _streamed(self, k: int, source: AsyncGenerator[str, None], seconds: float | None,
                        overrun: Callable[[float], LLMError]) -> str:
        """One streamed turn (01g-C6): each delta handed on as it arrives,
        heartbeats included, as `generate(stream=True)` yields them. The
        turn's bound holds only until visible text: a turn that has shown
        text is never cut (spec 3.9), so the wall and the per-call ceiling
        are checked at each delta before the first visible one -- as often as
        the provider or the facade's heartbeat speaks."""
        parts: list[str] = []
        started = time.monotonic()
        async with aclosing(source) as deltas:
            async for delta in deltas:
                if delta:
                    self.shown = True
                elif (not self.shown and seconds is not None
                      and time.monotonic() - started > seconds):
                    raise overrun(seconds)
                parts.append(delta)
                self.emit(tool_calls.LoopEvent("text", turn=k, delta=delta))
        return "".join(parts)

    def _note_overrun(self, holder: dict, chain: wire.Chain, error: LLMError) -> None:
        """An `llm_call_budget` overrun, filed against the attempt that was
        running (`routes.common._noting`'s rule)."""
        try:
            attempted = holder.get(llm.ATTEMPTED)
            self.client.note_outcome(attempted if isinstance(attempted, wire.Target)
                                     else chain.primary, error)
        except Exception as exc:  # noqa: BLE001 - bookkeeping on the failure path
            log.warning("could not file a tool turn's timeout: %s", type(exc).__name__)

    def _chain(self, final: bool) -> tuple[wire.Chain, list[str]]:
        """The turn's chain (spec 3.9, 3.11): the per-turn output cap through
        `call_chain` (with the final schema on a finalize turn), the fallback
        alone once it has served, and thinking off on a target that inherits
        another model's tool turns."""
        notes: list[str] = []
        full = call_chain(self.resolved, schema=self.final_schema if final else None,
                          max_tokens=self.budget.max_output_tokens)
        if self.fallen and full.fallback is not None:
            full = wire.Chain(full.fallback)
        sent = [self._inheriting(t, notes) for t in full.attempts]
        return wire.Chain(sent[0], sent[1] if len(sent) > 1 else None), notes

    def _inheriting(self, target: wire.Target, notes: list[str]) -> wire.Target:
        if any(w != (target.provider_id, target.model) for w in self.writers):
            notes.append(f"thinking off on {target.model}")
            return target.with_thinking_off()
        return target

    def _choice(self, chain: wire.Chain, notes: list[str]) -> str:
        """`required` downgraded to `auto` for a chain holding an attempt that
        would be refused it -- forced tool use beside thinking, implicit
        adaptive thinking included (`probes.tool_choice`'s rule)."""
        if self.choice != "required" or not any(self._auto_only(t) for t in chain.attempts):
            return self.choice
        notes.append("required sent as auto")
        return "auto"

    def _auto_only(self, target: wire.Target) -> bool:
        preset = next((a.provider_preset for a in self.resolved.attempts
                       if (a.provider_id, a.model) == (target.provider_id, target.model)), "")
        sent = llm_sampling.effective(target)["effective"].get("thinking")
        return probes.tool_choice(preset, target.kind, target.model_features, sent,
                                  target.model) == "auto"

    def _named(self, call: tool_calls.ToolCall) -> tool_calls.ToolCall:
        """A call with an id every provider takes: the provider's own while it
        is one (spec 3.11 rule 2), else the loop's."""
        if tool_calls.SAFE_ID.match(call.id):
            return call
        return replace(call, id=self._mint())

    def _mint(self) -> str:
        self.minted += 1
        return tool_calls.loop_id(self.run_id, self.minted)

    def _switched_to(self, target: wire.Target) -> bool:
        primary = self.resolved.chain.primary if self.resolved.chain is not None else None
        return (not self.fallen and primary is not None
                and (target.provider_id, target.model) != (primary.provider_id, primary.model))

    def _note_switch(self, target: wire.Target | None) -> None:
        """Once the fallback has served, it serves alone (spec 3.11 rule 1),
        and every id in the history is the loop's own from then on (rule 2)."""
        if target is None or not self._switched_to(target):
            return
        self.fallen = True
        mapping = {c["id"]: self._mint() for m in self.appended
                   for c in m.get("tool_calls", ()) if isinstance(c, dict) and c.get("id")}
        self.appended = tool_calls.rewrite_ids(self.appended, mapping)
        self._hist = None

    def _outgoing(self, extra: dict | None) -> list[dict]:
        history = self._history()
        if extra is None:
            return history
        if isinstance(history, model_guidance.PreparedMessages):
            return history.with_appended_keeping(extra)
        return [*history, extra]

    def _history(self) -> list[dict]:
        """The prompt and every turn the loop appended, as one prompt: a
        `PreparedMessages` extended message by message so every variant gets
        the same tail (`with_appended_keeping`), cached between turns."""
        if self._hist is None:
            self._hist, self._hist_len = self.base, 0
        for message in self.appended[self._hist_len:]:
            self._hist = (self._hist.with_appended_keeping(message)
                          if isinstance(self._hist, model_guidance.PreparedMessages)
                          else [*self._hist, message])
        self._hist_len = len(self.appended)
        return self._hist

    def _extend(self, messages: list[dict]) -> None:
        self.appended.extend(messages)

    def _append(self, turn: _Turn, results: list[dict]) -> None:
        said: dict = {"role": "assistant", "content": turn.text,
                      "tool_calls": [{"id": c.id, "name": c.name,
                                      "arguments": c.arguments or {}} for c in turn.calls]}
        if turn.opaque is not None:
            said["_opaque"] = turn.opaque
        self._extend([said, *results])
        if turn.target is not None:
            self.writers.add((turn.target.provider_id, turn.target.model))

    async def _captured(self, sending: list[dict], turn: _Turn) -> None:
        """The caller's `TurnCapture`, guarded: a capture never fails a turn."""
        if self.capture is None:
            return
        try:
            shown = [{"role": "system", "content": json.dumps(list(self.defs))}, *list(sending)]
            outcome = {"finish": turn.finish, "text_chars": len(turn.text),
                       "calls": [{"name": c.name, "arguments": c.arguments} for c in turn.calls]}
            target = turn.target or (self.resolved.chain.primary
                                     if self.resolved.chain is not None else None)
            if target is not None:
                await self.capture(shown, outcome, target)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("could not capture tool turn %d of run %s: %s", turn.turn,
                        self.run_id, type(exc).__name__)

    # ---- one tool call ----
    def _problem(self, call: tool_calls.ToolCall, spec: tool_calls.ToolSpec | None) -> str:
        if spec is None:
            return f"there is no tool named {call.name!r}"
        if call.arguments is None:
            return "your arguments were not a JSON object"
        return tool_calls.violation(call.arguments, spec.parameters)

    def _refused_call(self) -> str:
        """The limit that refuses the next execution (spec 3.9), or ""."""
        if self.limit:
            return self.limit
        if self.calls_used + 1 > self.budget.max_tool_calls:
            return "tool_calls"
        if self._wall_short(0):
            return "wall"
        return ""

    async def _one(self, turn: _Turn, call: tool_calls.ToolCall) -> dict:
        """One call answered: run, refused or failed, every call id gets a
        result (every provider requires one)."""
        self._check_cancelled()
        refused = self._refused_call()
        if refused:
            self.limit = refused
            self._trace(turn.turn, "tool", call.name, call.id, ok=False, note="not_run")
            return self._result_message(call, tool_calls.NOT_RUN, error=True)
        self.calls_used += 1
        spec = self.toolset.get(call.name)
        problem = (tool_calls.CUT_OFF if turn.finish == "length"
                   else self._problem(call, spec))
        if problem or spec is None:
            self._trace(turn.turn, "tool", call.name, call.id, ok=False, note="refused")
            return self._result_message(call, problem, error=True)
        return await self._executed(turn.turn, call, spec)

    async def _executed(self, k: int, call: tool_calls.ToolCall,
                        spec: tool_calls.ToolSpec) -> dict:
        self.emit(tool_calls.LoopEvent("tool_start", turn=k, call_id=call.id, name=call.name))
        started = time.monotonic()
        output, ok = await self._run_tool(call, spec)
        text = tool_calls.truncate(output.text, spec.max_result_chars)
        room = self.budget.max_result_chars_total - self.result_chars
        if len(text) > room:
            text = tool_calls.truncate(text, room)
            self.limit = self.limit or "result_chars"
        self.result_chars += len(text)
        if ok and spec.effect == "propose" and isinstance(output.proposal, dict):
            self.proposals.append(output.proposal)
        self._trace(k, "tool", call.name, call.id, ok=ok, refs=output.refs, chars=len(text),
                    started=started)
        self.emit(tool_calls.LoopEvent("tool_end", turn=k, call_id=call.id, name=call.name,
                                       ok=ok, chars=len(text), refs=output.refs))
        return self._result_message(call, text, error=not ok)

    async def _run_tool(self, call: tool_calls.ToolCall, spec: tool_calls.ToolSpec
                        ) -> tuple[tool_calls.ToolOutput, bool]:
        """The caller's executor, bounded: a wait, never a kill (spec 3.6). A
        `ToolError` reaches the model as itself; anything else as a generic
        failure, logged by type only (a message can quote record text)."""
        end = self.t0 + self.wall if self.wall > 0 else float("inf")
        ctx = tool_calls.ToolContext(campaign=self.attribution["campaign"],
                                     scene_identity=self.scene_identity, run_id=self.run_id,
                                     deadline=end, root=self.root)
        seconds = min(spec.timeout, end - time.monotonic())

        async def marked() -> tool_calls.ToolOutput:
            # The wait runs the executor in a task of its own: marked as a
            # loop's, so a tool cannot start a loop inside this one.
            me = asyncio.current_task()
            if me is not None:
                _LOOPING.add(me)
            return await self.execute(call, ctx)

        try:
            output = await deadline.bounded(marked(), seconds,
                                            lambda _s: _ToolTimeoutError())
        except _ToolTimeoutError:
            return tool_calls.ToolOutput(tool_calls.TIMED_OUT), False
        except tool_calls.ToolError as exc:
            return tool_calls.ToolOutput(str(exc) or tool_calls.TOOL_FAILED), False
        except Exception as exc:  # noqa: BLE001 - a tool's failure is the model's to hear
            log.error("tool %s failed in run %s: %s", call.name, self.run_id,
                      type(exc).__name__)
            return tool_calls.ToolOutput(tool_calls.TOOL_FAILED), False
        if not isinstance(output, tool_calls.ToolOutput) or not isinstance(output.text, str):
            return tool_calls.ToolOutput(tool_calls.TOOL_FAILED), False
        return output, True

    @staticmethod
    def _result_message(call: tool_calls.ToolCall, text: str, *, error: bool) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "name": call.name,
                "content": text, "is_error": error}

    # ---- bookkeeping ----
    def _trace(self, turn: int, kind: Literal["model", "tool", "decide", "stop"],
               name: str = "", call_id: str = "", *, ok: bool = True,
               refs: tuple[str, ...] = (), chars: int = 0, note: str = "",
               started: float | None = None) -> None:
        elapsed = int((time.monotonic() - started) * 1000) if started is not None else 0
        self.trace.append(tool_calls.TraceEntry(turn, kind, name, call_id, ok, tuple(refs),
                                                chars, elapsed, note))

    def _result(self, status: Literal["completed", "budget_exhausted", "failed"], *,
                final: dict | None = None, final_call: tool_calls.ToolCall | None = None,
                error: Exception | None = None,
                declined: tuple[tool_calls.ToolCall, ...] = ()) -> tool_calls.LoopResult:
        limit = self.limit if status == "budget_exhausted" else ""
        self._trace(self.turns, "stop", limit, note=status)
        return tool_calls.LoopResult(
            status=status, limit=limit, text=self.text, final=final, final_call=final_call,
            declined=declined,
            proposals=tuple(self.proposals), messages=tuple(self.appended),
            trace=tuple(self.trace), rows=tuple(self.rows), error=error, run_id=self.run_id)


def _next_or_beat(queue: asyncio.Queue) -> Awaitable[object | None]:
    """The queue's next item, or None after `llm.HEARTBEAT_INTERVAL` of
    silence (none with an interval of 0)."""
    async def wait() -> object | None:
        interval = llm.HEARTBEAT_INTERVAL
        if interval <= 0:
            return await queue.get()
        try:
            return await asyncio.wait_for(queue.get(), interval)
        except TimeoutError:
            return None
    return wait()


#: How long a closed or cancelled run's driver is given to unwind (its turn's
#: meter files `aborted`) before it is left to finish on its own.
_DRIVER_UNWIND_S = 5.0


async def _stopped(runner: asyncio.Task) -> None:
    """`runner` cancelled if still running, and briefly awaited, so a closed
    or cancelled run's open meter files before the caller moves on -- never
    longer than `_DRIVER_UNWIND_S`."""
    if not runner.done():
        runner.cancel()
        await asyncio.wait({runner}, timeout=_DRIVER_UNWIND_S)
    runner.add_done_callback(lambda t: None if t.cancelled() else t.exception())


async def _loop_events(task: str, messages: list[dict], options: dict
                       ) -> AsyncGenerator[tool_calls.LoopEvent, None]:
    """`stream_tools`' body after its entry checks: the spend preflight, then
    the run's events. The consuming task is marked as running a loop."""
    budget, resolved = options["budget"], options["resolved"]
    prices: dict[tuple[str, str], Price | None] = {}
    if budget.spend_ceiling_usd is not None:
        prices = await asyncio.to_thread(prices_for, resolved)
        refused = tool_run_refusal(task, resolved, budget, prices)
        if refused is not None:
            raise refused
    loop = _Loop(task, messages, prices=prices, **options)
    current = asyncio.current_task()
    if current is not None:
        _LOOPING.add(current)
    try:
        async with aclosing(loop.events()) as events:
            async for event in events:
                yield event
    finally:
        if current is not None:
            _LOOPING.discard(current)


def stream_tools(task: str, messages: list[dict], *, toolset: tool_calls.Toolset,
                 execute: tool_calls.Execute, client: LLMClient,
                 resolved: ResolvedInference, budget: tool_calls.RunBudget, run_id: str,
                 campaign: str = "", scene: str = "", scene_identity: str = "",
                 post: int | None = None, round_id: str = "", response_id: str = "",
                 cancelled: Callable[[], bool] | None = None,
                 final_schema: dict | None = None,
                 tool_choice: Literal["auto", "required"] = "auto",
                 capture: TurnCapture | None = None, decline_after_text: bool = False,
                 reasoning: llm_reasoning.Buffer | None = None,
                 streaming: bool = True) -> AsyncGenerator[tool_calls.LoopEvent, None]:
    """`run_tools`' loop as it happens (spec 3.8; 01g-C6): an async iterator
    of `tool_calls.LoopEvent`s in the order `text* turn_end (tool_start
    tool_end)* ... done`, `done` last and carrying the `LoopResult`.

    Every turn is sent through `client.stream` -- the path
    `generate(stream=True)` takes, with the same retries, idle bound and
    `""` heartbeats -- and its deltas are `text` events as they arrive
    (`tool_calls.text_deltas` turns the events back into that plain
    iterator). Between turns and while a tool runs, an empty `text` comes
    every `llm.HEARTBEAT_INTERVAL`. `reasoning`, a caller's display buffer,
    rides each turn's holder; `llm._stamp` resets it at each turn's first
    attempt, so no continuity across turns is promised. The run's wall clock
    never cuts a turn that has shown text: it only refuses the next send.

    With `decline_after_text`, visible text is final: a turn that showed text
    and ended in tool calls ends the run `completed` with that text, its calls
    unexecuted, in `LoopResult.declined` and the trace as declined
    `after_text`. A call before any visible text runs normally.

    The entry checks (`ValueError`) are made here, before an iterator is
    returned; the spend preflight (`RunRefused`) on the first step.
    `streaming=False` is `run_tools`' joined path."""
    _loop_refusal(task, messages, toolset, resolved, run_id, final_schema, tool_choice)
    options = {"toolset": toolset, "execute": execute, "client": client,
               "resolved": resolved, "budget": budget, "run_id": run_id,
               "attribution": {"campaign": campaign, "scene": scene, "post": post,
                               "round_id": round_id, "response_id": response_id},
               "scene_identity": scene_identity, "cancelled": cancelled,
               "final_schema": final_schema, "tool_choice": tool_choice, "capture": capture,
               "streaming": streaming, "decline_after_text": decline_after_text,
               "reasoning": reasoning}
    return _loop_events(task, messages, options)


async def run_tools(task: str, messages: list[dict], *, toolset: tool_calls.Toolset,
                    execute: tool_calls.Execute, client: LLMClient,
                    resolved: ResolvedInference, budget: tool_calls.RunBudget, run_id: str,
                    campaign: str = "", scene: str = "", scene_identity: str = "",
                    post: int | None = None, round_id: str = "", response_id: str = "",
                    cancelled: Callable[[], bool] | None = None,
                    final_schema: dict | None = None,
                    tool_choice: Literal["auto", "required"] = "auto",
                    capture: TurnCapture | None = None, decline_after_text: bool = False,
                    reasoning: llm_reasoning.Buffer | None = None) -> tool_calls.LoopResult:
    """Run a bounded loop of model turns and tool calls for `task` (spec 3.7;
    01g-C2a, C3, C4 less spend).

    `resolved` is the call site's resolution of `task` for `generate`, on a
    route that requires tools or lists them as optional, with a primary not
    known unable to call them. The caller supplies the tools (`toolset`), the
    executor that runs them (`execute`; `tool_calls.registered` is the
    default) and the run id (`run_id`, normally the detached run's `Run.id`):
    the loop never executes a tool itself and never mints a run id.

    Each model turn is one facade call (`client.complete`) under a meter of
    its own carrying `run_id` and `loop_turn` (and the caller's round and
    reply ids), with the turn's output capped at `budget.max_output_tokens`.
    Before each send the run's budget is checked (cancelled, turns, wall);
    before each execution, the tool-call budget and the wall. A limit makes
    the next turn the reserved finalize turn when one fits, and otherwise
    stops the run `budget_exhausted` with the limit named. A call to a
    `terminal` tool ends the run unexecuted (`final_call`). With
    `final_schema`, the answer is read and checked against it, with one
    finalize turn for one that does not conform.

    With `budget.spend_ceiling_usd` (01g-S5), each send is projected first
    (`price_for`; a guard, never accounting) and refused when it would carry
    the run past the ceiling; a chain with an unpriceable attempt is
    `tool_calls.RunRefused("unpriceable")` before anything is sent
    (`tool_run_refusal`, which a route runs before it reserves).

    Raises only for invalid input (`ValueError`, before any meter opens),
    `RunRefused`, an `LLMError` on turn 1, and cancellation -- `cancelled()` returning True
    included, as `asyncio.CancelledError`. Every later failure is a `failed`
    result with the rows filed so far.

    It is `stream_tools` drained -- one implementation -- with each turn
    joined (`client.complete`) and bounded whole."""
    events = stream_tools(
        task, messages, toolset=toolset, execute=execute, client=client, resolved=resolved,
        budget=budget, run_id=run_id, campaign=campaign, scene=scene,
        scene_identity=scene_identity, post=post, round_id=round_id,
        response_id=response_id, cancelled=cancelled, final_schema=final_schema,
        tool_choice=tool_choice, capture=capture, decline_after_text=decline_after_text,
        reasoning=reasoning, streaming=False)
    result: tool_calls.LoopResult | None = None
    async with aclosing(events) as running:
        async for event in running:
            if event.kind == "done":
                result = event.result
    assert result is not None
    return result
