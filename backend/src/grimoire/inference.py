"""The inference operations a call site asks for by name: `decide` (spec 7.4).

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

Four rules this module keeps:

- **One meter per call, opened here** (ruling 9): per structured chunk, and
  per native item. A caller's time budget runs inside it through `around`, so
  an overrun is filed as an `error/timeout` row exactly as a budgeted
  `complete` is today, and `usage.Meter.done` stays the one place an LLM
  failure is recorded. `test_usage_guard.py` scans this file.
- **Nothing is written into the holder.** The operation and the decision mode
  reach the ledger through the account block (`llm_usage.ACCOUNT_KEY`), which
  `llm._stamp` files: the operation from the resolution, the mode stamped here,
  per call, on a COPY of each attempt's block (`_with_mode`) -- a resolution's
  blocks are never mutated (`llm_usage.with_account`), and `stages` copies
  the dicts it changes rather than popping from them.
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
  threadpool before the switch, and it still does.

Nothing in `store/` or the gateway imports this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from . import decisions, llm, llm_errors, llm_usage, prompts, store
from .llm import LLMClient
from .llm_errors import LLMError
from .store.inference import resolve
from .store.inference.resolved import ResolvedInference

STRUCTURED = decisions.STRUCTURED_BACKEND
NATIVE = decisions.NATIVE_BACKEND

#: How many native requests one stage keeps in flight: one request per item,
#: at most this many at once. Argued structurally: a continuity sweep sends
#: one item per row, and an unbounded fan-out would meet the provider's rate
#: limit as a burst of 429s that the retry budget then pays for. To be tuned
#: against real prompts later.
NATIVE_CONCURRENCY = 4

#: Given the facade call and the meter's live holder, what to await instead --
#: how a caller's time budget runs inside the meter `decide` opens (I2). The
#: call yields the reply text on a structured stage and an `ItemResult` on a
#: native one; the callers' hooks (`budget.run`, `_bounded_call`) are generic.
Around = Callable[[Awaitable[Any], dict], Awaitable[Any]]

#: Called with each chunk's messages before they are sent (spec 9.4).
Capture = Callable[[list[dict]], Awaitable[None]]


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
    `_BACKENDS`), the connection dict it sends, and the primary route's retry
    count -- None for the facade's own budget (the primary's `llm_retries`),
    0 on a fallback stage, which gets one attempt (spec 5.4, I3)."""

    mode: str
    conn: dict
    retries: int | None


def _alone(conn: dict) -> dict:
    """A COPY of `conn` without the fallback it carries (`llm.FALLBACK_KEY`):
    never a pop, so the resolution's own dict keeps what the resolver wrote
    (M2)."""
    return {k: v for k, v in conn.items() if k != llm.FALLBACK_KEY}


def stages(resolved: ResolvedInference) -> tuple[Stage, ...]:
    """The decide chain of `resolved` (spec 5.5): the selection's one backend,
    then -- when it is a stage of its own -- the role fallback's, in one
    attempt. Pure: nothing in `resolved` is changed.

    The primary is served natively when its `decision_mode` says so, and
    structured when it `generates` (`resolve.generates`); its structured dict
    is sent as it is, so a structured fallback the resolver attached
    (`FALLBACK_KEY`) rides the facade, exactly as slice F sends it. The
    fallback is a stage of its own only when either attempt is native (a
    structured stage cannot carry a native attempt behind it, nor a native
    one anything), and then the primary's dict is sent without it. A fallback
    known incapable (`fallback_missing`) is never a stage (spec 5.3).

    No structured stage follows a native one on the same selection: a native
    attempt is one that cannot generate (ruling 1, C1), so that stage could
    never answer. Trying native first on a model that also generates is a
    later user decision (spec 16), which would add it back here.

    While slice F's decide skip exists, a skipped resolution is F's one stage
    on the fallback it skipped to (`resolved.conn`)."""
    if resolved.skipped:
        if resolved.conn is None:
            raise ValueError("a skipped resolution names no connection to send")
        return (Stage(STRUCTURED, resolved.conn, None),)
    if not resolved.attempts:
        return ()
    primary = resolved.attempts[0]
    fallback = (resolved.attempts[1] if len(resolved.attempts) > 1
                and not resolved.fallback_missing else None)
    apart = (fallback is not None and fallback.decision_mode in decisions.BACKENDS
             and NATIVE in (primary.decision_mode, fallback.decision_mode))
    chain: list[Stage] = []
    if primary.decision_mode == NATIVE:
        chain.append(Stage(NATIVE, _alone(primary.conn), None))
    elif resolve.generates(primary):
        chain.append(Stage(STRUCTURED, _alone(primary.conn) if apart else primary.conn, None))
    if apart and fallback is not None:
        chain.append(Stage(fallback.decision_mode, _alone(fallback.conn), 0))
    return tuple(chain)


@dataclass(frozen=True)
class _Call:
    """The keyword arguments `decide` received, handed to a backend whole,
    with the stage's own connection and retry count (`Stage`)."""

    task: str
    client: LLMClient
    explain: str
    campaign: str
    scene: str
    post: int | None
    round_id: str
    capture: Capture | None
    around: Around | None
    conn: dict
    retries: int | None


class _Answered(NamedTuple):
    """What one stage made of the items it was given, by position: each
    item's result (None for an item its stage failed to answer), each failed
    unit -- a chunk on a structured stage, an item on a native one -- as its
    items' positions and its final error, in order, and the stage's ledger
    rows and what answered (`decisions.Decision.served`). Every unanswered
    item is in exactly one failed unit."""

    results: tuple[decisions.ItemResult | None, ...]
    failed: tuple[tuple[tuple[int, ...], LLMError], ...]
    rows: tuple[dict, ...]
    served: tuple[tuple[str, str], ...]


#: A backend: one stage's attempt at every item it is given.
Backend = Callable[[tuple[decisions.Item, ...], _Call], Awaitable[_Answered]]


def _with_mode(conn: dict, mode: str) -> dict:
    """`conn` with `decision_mode` laid over its account block, and over the
    block of the fallback it carries (`llm.FALLBACK_KEY`): new dicts and new
    blocks throughout (I7), so the resolution's own are left as they were."""
    out = llm_usage.with_account(conn, decision_mode=mode)
    if llm.FALLBACK_KEY in out:
        out[llm.FALLBACK_KEY] = llm_usage.with_account(conn[llm.FALLBACK_KEY],
                                                        decision_mode=mode)
    return out


def _without_mode(conn: dict) -> dict:
    """`conn` as the same attempt without structured mode, and alone: no
    fallback behind it (the re-send is that one attempt's, and every other
    route has already had its turn)."""
    return {k: v for k, v in conn.items()
            if k not in (llm.STRUCTURED_KEY, llm.FALLBACK_KEY)}


def _served_by(holder: dict) -> tuple[str, str]:
    """`(provider id, effective model)` of the attempt that answered, read from
    the connection the facade stamped into `holder` (`llm.ATTEMPTED`)."""
    conn = holder.get(llm.ATTEMPTED)
    if not isinstance(conn, dict):
        return "", ""
    return str(conn.get("id", "") or ""), llm.effective_model(conn)


async def _once(call: _Call, sending: dict, messages: list[dict], schema: dict,
                rows: list[dict]) -> tuple[str, dict | None, LLMError | None]:
    """One metered facade call: `(text, the answering holder, None)`, or `("",
    None, the error)`. Its ledger row, when the meter filed one, is appended
    to `rows`."""
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
    return (text, m.usage, None) if error is None else ("", None, error)


async def _ask(call: _Call, conn: dict, messages: list[dict], schema: dict,
               rows: list[dict]) -> tuple[str, dict | None, LLMError | None]:
    """One chunk's call: `(text, the answering holder, None)`, or `("", None,
    the error)`. Each call's ledger row is appended to `rows`.

    The attempt chain, and -- only when every route failed and a route's
    failure was its provider refusing the structured field
    (`llm.SchemaRefusalError`) -- each such attempt once more, alone and
    without the mode (spec M-4, ruling 3): the schema is in the prompt, so the
    reply still parses. A primary that refused is re-sent after its fallback
    failed too; a fallback that refused, after the primary failed for any
    reason. Each re-send is its own metered call, and none is re-sent twice.
    Should those fail too, the error is the routes' failures composed afresh
    (`llm.routes_failed`), each re-sent route's word now its own failure."""
    text, holder, error = await _once(call, conn, messages, schema, rows)
    if not isinstance(error, llm.SchemaRefusalError) or not error.attempts:
        return text, holder, error
    words = list(error.words)
    for index, attempt in enumerate(error.attempts):
        if attempt is None:
            continue
        text, holder, again = await _once(call, _without_mode(attempt), messages, schema, rows)
        if again is None:
            return text, holder, None
        words[index] = again
    return "", None, llm.routes_failed(words)


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
    conn = _with_mode(call.conn, STRUCTURED)
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
        if call.capture is not None:
            await call.capture(messages)
        schema = decisions.schema(chunk, explain=explain)
        text, holder, error = await _ask(call, conn, messages, schema, rows)
        if holder is not None:
            for index, result in enumerate(decisions.parse(text, chunk, explain=explain)):
                results[offset + index] = replace(result, backend=STRUCTURED)
            if (by := _served_by(holder)) not in served:
                served.append(by)
        if error is not None:
            # Filed by the meter already; the chunk's fate waits on the chain.
            failed.append((unit, error))
            if _stops_chunks(error, conn):
                stopped = error
    return _Answered(tuple(results), tuple(failed), tuple(rows), tuple(served))


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
    route that failed for a reason of its own may serve the next call."""
    if exc.words:
        return all(_connection_wide(word) for word in exc.words)
    return (exc.kind in ("auth", "missing_key", "rate_limit")
            or llm_errors.account_limit(exc)
            or _refused_unsent(exc))


def _refused_unsent(exc: LLMError) -> bool:
    """Whether `exc` is a call the caller's own clock refused before it was
    sent (absorb's `BudgetRefused`: `store.usage.NOT_A_FAILURE` is False)."""
    return getattr(exc, store.usage.NOT_A_FAILURE, True) is False


def _stops_chunks(error: LLMError, conn: dict) -> bool:
    """Whether a structured chunk's `error` means no later chunk on the stage
    is sent (`_connection_wide`). A chunk sent with a fallback behind it
    (`llm.FALLBACK_KEY`) stops the stage only when its error names both
    routes (`words`): a bare error from such a call is one the facade did not
    fall back on -- the fallback dropped as the primary's own route, or a
    double that serves one attempt -- so nothing says the fallback would
    fail the next chunk. The caller's clock refusing a call unsent stops the
    stage whatever stood behind it."""
    if _refused_unsent(error):
        return True
    if llm.FALLBACK_KEY in conn and not error.words:
        return False
    return _connection_wide(error)


async def _native(items: tuple[decisions.Item, ...], call: _Call) -> _Answered:
    """The native backend: one metered `decide_native` per item, at most
    `NATIVE_CONCURRENCY` in flight, all in one `asyncio.TaskGroup` (I5).

    Each item catches only `LLMError`, which leaves that item unanswered with
    its error; anything else propagates and the group cancels the other
    items, so no request outlives the batch, and each open meter files
    `aborted` -- as a cancel does. Once an item fails with an error every
    other item would meet (`_connection_wide`), no further item is started:
    those in flight finish, and the rest are never sent, file no row, and
    leave the stage carrying the failure that stopped them, for the next
    stage to take up."""
    # The mode stamped on a copy of the stage's block (`with_account`).
    conn = llm_usage.with_account(call.conn, decision_mode=NATIVE)
    # Named `client`, the receiver `test_usage_guard.py` recognises.
    client = call.client
    gate = asyncio.Semaphore(NATIVE_CONCURRENCY)
    results: list[decisions.ItemResult | None] = [None] * len(items)
    errors: list[LLMError | None] = [None] * len(items)
    rows: list[dict | None] = [None] * len(items)
    holders: list[dict | None] = [None] * len(items)
    started = [False] * len(items)
    stopped: list[LLMError] = []

    async def one(index: int, item: decisions.Item) -> None:
        async with gate:
            if stopped:
                return
            m = store.usage.meter(call.task, campaign=call.campaign, scene=call.scene,
                                  post=call.post, round_id=call.round_id)
            started[index] = True
            try:
                with m:
                    pending = client.decide_native(item, conn, m.usage, retries=call.retries)
                    results[index] = await (call.around(pending, m.usage) if call.around
                                            else pending)
                    holders[index] = m.usage
            except LLMError as exc:
                # Filed by the meter already; the item's fate waits on the chain.
                errors[index] = exc
                if not stopped and _connection_wide(exc):
                    stopped.append(exc)
            finally:
                rows[index] = m.row

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
                     tuple(row for row in rows if row is not None), served)


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
    that takes no sampling would only hide it.

    When no item answered, the first failed unit's final error is raised
    (F's ruling 8): its failures on every stage it reached, composed
    (`llm.routes_failed`) -- the first stage's kind and window, the later
    stages' failures named after it (M3), and each kept in `words`.
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
                 capture=capture, around=around, conn=chain[0].conn,
                 retries=chain[0].retries)
    results: list[decisions.ItemResult | None] = [None] * len(items)
    #: Per item, the failure of each stage that failed it, in stage order.
    words: dict[int, list[LLMError]] = {}
    rows: list[dict] = []
    served: dict[tuple[str, str], None] = {}     # ordered, each once
    failed: list[tuple[tuple[int, ...], LLMError]] = []
    pending = list(range(len(items)))
    for stage in chain:
        if not pending:
            break
        got = await _BACKENDS[stage.mode](
            tuple(items[i] for i in pending),
            replace(call, conn=stage.conn, retries=stage.retries))
        rows.extend(got.rows)
        served.update(dict.fromkeys(got.served))
        failed = _settle(got, pending, results, words)
        pending = [i for i in pending if results[i] is None]
        if any(isinstance(error, llm.PresetRefusalError) for _unit, error in failed):
            break
    # Each failed unit of the last stage its items reached, with the failures
    # of every stage before it: a chunk's earlier failures are its first
    # item's (a native stage before it failed each item on its own).
    errors = tuple(llm.routes_failed(words[unit[0]]) for unit, _error in failed)
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
    sees each structured call's messages before they are sent; `around` is
    handed each facade call and the meter's live holder, and returns what to
    await instead (a caller's time budget).
    """
    if resolved.task != task:
        raise ValueError(f"a resolution of {resolved.task!r} cannot decide {task!r}")
    if resolved.operation != "decide":
        raise ValueError(f"a {resolved.operation!r} resolution cannot decide")
    if resolved.conn is None:
        raise ValueError(f"{task!r} resolved to no connection")
    chain = stages(resolved)
    if not chain:
        raise ValueError(f"no decision backend for mode {resolved.decision_mode!r}")
    return await run_stages(task, items, chain, client=client,
                            explain=explain, campaign=campaign, scene=scene, post=post,
                            round_id=round_id, capture=capture, around=around)
