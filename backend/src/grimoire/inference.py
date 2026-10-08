"""The inference operations a call site asks for by name: `decide` (spec 7.4).

A decision is a closed question answered from a set the caller fixed in
advance (`grimoire.decisions` holds that contract). `decide` is the operation:
it takes a resolution the call site already made -- `require_inference(task,
cid, operation="decide")`, so the seam's 409 and absorb's soft resolution come
before it -- and answers through a backend chosen by the resolution's
`decision_mode`. Slice F registers one backend, `structured`: `generate`
(`LLMClient.complete(..., schema=)`) over a prompt that carries the schema,
read back by `decisions.parse`. Slice H adds `native` (a provider's own
decisions endpoint) beside it in `_BACKENDS`, and `decide`'s signature does not
change.

Four rules this module keeps:

- **One meter per call, opened here** (ruling 9). A caller's time budget runs
  inside it through `around`, so an overrun is filed as an `error/timeout` row
  exactly as a budgeted `complete` is today, and `usage.Meter.done` stays the
  one place an LLM failure is recorded. `test_usage_guard.py` scans this file.
- **Nothing is written into the holder.** The operation and the decision mode
  reach the ledger through the account block (`llm_usage.ACCOUNT_KEY`), which
  `llm._stamp` files: the operation from the resolution, the mode stamped here,
  per call, on a COPY of each attempt's block (`_with_mode`) -- a resolution's
  blocks are never mutated (`llm_usage.with_account`).
- **Provider errors propagate.** A chunk's `LLMError` is re-raised when no
  chunk answered; once one has, a failed chunk's items are `None` with reason
  `error`. Cancellation passes through untouched (the meter files `aborted`).
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
from dataclasses import dataclass

from . import decisions, llm, llm_usage, prompts, store
from .llm import LLMClient
from .llm_errors import LLMError
from .store.inference.resolved import ResolvedInference

#: Given the facade call and the meter's live holder, what to await instead --
#: how a caller's time budget runs inside the meter `decide` opens (I2).
Around = Callable[[Awaitable[str], dict], Awaitable[str]]

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


@dataclass(frozen=True)
class _Call:
    """The keyword arguments `decide` received, handed to a backend whole."""

    task: str
    client: LLMClient
    resolved: ResolvedInference
    explain: str
    campaign: str
    scene: str
    post: int | None
    round_id: str
    capture: Capture | None
    around: Around | None


#: A backend: answers every item, in order, and says what answered it.
Backend = Callable[[tuple[decisions.Item, ...], _Call], Awaitable[decisions.Decision]]


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
            pending = client.complete(messages, sending, m.usage, schema=schema)
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


async def _structured(items: tuple[decisions.Item, ...], call: _Call) -> decisions.Decision:
    """The structured backend: one metered `complete(schema=)` per chunk
    (`decisions.chunks`), each read back by `decisions.parse`. Each chunk runs
    down the attempt chain on its own, so what answered is collected per chunk
    (`decisions.Decision.served`), never read off the last one, and so is
    each failed chunk's own error (`decisions.Decision.errors`): the chunk's
    final word, after `_ask`'s re-sends, never one of the calls it made on
    the way there."""
    assert call.resolved.conn is not None   # `decide` refused a None conn
    conn = _with_mode(call.resolved.conn, "structured")
    explain = bool(call.explain)
    results: list[decisions.ItemResult | None] = [None] * len(items)
    rows: list[dict] = []
    failed: list[tuple[int, tuple[decisions.Item, ...]]] = []
    errors: list[LLMError] = []
    served: list[tuple[str, str]] = []
    for offset, chunk in decisions.chunks(items):
        # Off the loop: the template loader touches the filesystem.
        messages = await asyncio.to_thread(structured_messages, chunk, explain=call.explain)
        if call.capture is not None:
            await call.capture(messages)
        schema = decisions.schema(chunk, explain=explain)
        text, holder, error = await _ask(call, conn, messages, schema, rows)
        if holder is not None:
            for index, result in enumerate(decisions.parse(text, chunk, explain=explain)):
                results[offset + index] = result
            if (by := _served_by(holder)) not in served:
                served.append(by)
        if error is not None:
            # Filed by the meter already; the chunk's fate waits on the others.
            errors.append(error)
            failed.append((offset, chunk))
    if not served:
        # No chunk answered: the provider's error is the caller's (ruling 8).
        raise errors[0]
    for offset, chunk in failed:
        for index, result in enumerate(decisions.unanswered(chunk, "error")):
            results[offset + index] = result
    # One selection answered every chunk that answered: it is the decision's.
    # Several (a fallback took one chunk, the primary another): none is.
    provider, model = served[0] if len(served) == 1 else ("", "")
    return decisions.Decision(items=tuple(r for r in results if r is not None),
                              backend="structured", provider=provider, model=model,
                              usage=tuple(rows), served=tuple(served),
                              errors=tuple(errors))


#: Every backend `decide` can dispatch to, by `ResolvedInference.decision_mode`.
#: Slice H adds "native" (and spec 5.5's chain) here.
_BACKENDS: dict[str, Backend] = {"structured": _structured}


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
    sees each call's messages before they are sent; `around` is handed each
    facade call and the meter's live holder, and returns what to await instead
    (a caller's time budget).
    """
    if resolved.task != task:
        raise ValueError(f"a resolution of {resolved.task!r} cannot decide {task!r}")
    if resolved.operation != "decide":
        raise ValueError(f"a {resolved.operation!r} resolution cannot decide")
    if resolved.conn is None:
        raise ValueError(f"{task!r} resolved to no connection")
    items = tuple(items)
    decisions.validate(items)
    backend = _BACKENDS.get(resolved.decision_mode or "")
    if backend is None:
        raise ValueError(f"no decision backend for mode {resolved.decision_mode!r}")
    return await backend(items, _Call(task=task, client=client, resolved=resolved,
                                      explain=explain, campaign=campaign, scene=scene,
                                      post=post, round_id=round_id, capture=capture,
                                      around=around))
