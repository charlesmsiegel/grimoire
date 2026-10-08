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
  One error is not yet the chunk's: a provider refusing the structured field
  with no attempt left to fall to (`llm.SchemaRefusalError`) is answered by
  sending that attempt once more without the mode, as its own metered call.
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
    fallback behind it (a schema refusal is only raised with none left)."""
    return {k: v for k, v in conn.items()
            if k not in (llm.STRUCTURED_KEY, llm.FALLBACK_KEY)}


def _served_by(holder: dict) -> tuple[str, str]:
    """`(provider id, effective model)` of the attempt that answered, read from
    the connection the facade stamped into `holder` (`llm.ATTEMPTED`)."""
    conn = holder.get(llm.ATTEMPTED)
    if not isinstance(conn, dict):
        return "", ""
    return str(conn.get("id", "") or ""), llm.effective_model(conn)


async def _ask(call: _Call, conn: dict, messages: list[dict], schema: dict,
               rows: list[dict]) -> tuple[str, dict | None, LLMError | None]:
    """One chunk's call: `(text, the answering holder, None)`, or `("", None,
    the error)`. Each call's ledger row is appended to `rows`.

    The attempt, and -- only when its provider refused the structured field
    with nowhere left to fall (`llm.SchemaRefusalError`) -- that same attempt
    once more without the mode (spec M-4, ruling 3): the schema is in the
    prompt, so the reply still parses. Each is its own metered call."""
    # Named `client`, the receiver `test_usage_guard.py` recognises.
    client = call.client
    error: LLMError | None = None
    for sending in (conn, _without_mode(conn)):
        m = store.usage.meter(call.task, campaign=call.campaign, scene=call.scene,
                              post=call.post, round_id=call.round_id)
        try:
            with m:
                pending = client.complete(messages, sending, m.usage, schema=schema)
                text = await (call.around(pending, m.usage) if call.around else pending)
        except llm.SchemaRefusalError as exc:
            error = exc
            retry = sending is conn
        except LLMError as exc:
            error, retry = exc, False
        else:
            error, retry = None, False
        if m.row is not None:
            rows.append(m.row)
        if error is None:
            return text, m.usage, None
        if not retry:
            break
    return "", None, error


async def _structured(items: tuple[decisions.Item, ...], call: _Call) -> decisions.Decision:
    """The structured backend: one metered `complete(schema=)` per chunk
    (`decisions.chunks`), each read back by `decisions.parse`."""
    assert call.resolved.conn is not None   # `decide` refused a None conn
    conn = _with_mode(call.resolved.conn, "structured")
    explain = bool(call.explain)
    results: list[decisions.ItemResult | None] = [None] * len(items)
    rows: list[dict] = []
    failed: list[tuple[int, tuple[decisions.Item, ...]]] = []
    first_error: LLMError | None = None
    answered: dict | None = None
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
            answered = holder
        if error is not None:
            # Filed by the meter already; the chunk's fate waits on the others.
            first_error = first_error or error
            failed.append((offset, chunk))
    if answered is None:
        # No chunk answered: the provider's error is the caller's (ruling 8).
        assert first_error is not None
        raise first_error
    for offset, chunk in failed:
        for index, result in enumerate(decisions.unanswered(chunk, "error")):
            results[offset + index] = result
    provider, model = _served_by(answered)
    return decisions.Decision(items=tuple(r for r in results if r is not None),
                              backend="structured", provider=provider, model=model,
                              usage=tuple(rows))


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
