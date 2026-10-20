"""Metered structured and native requests with guarded settled-call capture."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from typing import NamedTuple

from .. import decisions, llm, llm_errors, prompts, store, wire
from ..llm_errors import LLMError
from . import _contracts, _requests
from ._contracts import Outcome as Outcome
from ._contracts import _Answered as _Answered
from ._contracts import _Call as _Call

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


def _with_mode(chain: wire.Chain, mode: str) -> wire.Chain:
    """`chain` with `decision_mode` laid over the account of each target on
    it: new targets throughout (I7), so the resolution's are left as they
    were."""
    return chain.with_account(decision_mode=mode)


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
    records.append(_record(call, _contracts.STRUCTURED, unit, m.row, error))
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
        return await _requests._without_refused_mode(error, send)
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
    out = _contracts.Outcome({**record, "stage": call.stage, "at": list(call.batch(unit)),
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
        _contracts.log.warning("could not capture a %s decision: %s", call.task, exc)


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
    chain = _with_mode(call.chain, _contracts.STRUCTURED)
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
            answered = [replace(result, backend=_contracts.STRUCTURED, served=server)
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
                        partial(_outcome, call, unit, _contracts.STRUCTURED, chain.primary, holder,
                                answered, error),
                        ran if isinstance(ran, wire.Target) else chain.primary)
        if error is not None:
            # Filed by the meter already; the chunk's fate waits on the chain.
            failed.append((unit, error))
            if _stops_chunks(error, chain):
                stopped = error
    return _contracts._Answered(tuple(results), tuple(failed), tuple(rows), tuple(served), stopped,
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
    named = _with_mode(call.chain, _contracts.NATIVE).primary.without_sampling()
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
        records.append(_record(call, _contracts.NATIVE, (index,), m.row, errors[index]))
        # Settled, and out of the gate (a cancel never reaches here): the
        # request as sent, built off the loop -- or none, when the call was
        # refused before it went out (`Capture`).
        answered = results[index]
        await _captured(
            call, partial(_native_request, item, named) if store.usage.sent(m.usage) else [],
            partial(_outcome, call, (index,), _contracts.NATIVE, named, holders[index],
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
    return _contracts._Answered(tuple(results), failed,
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
