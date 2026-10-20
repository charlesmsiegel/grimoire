"""The public generation operation and resolved-operation adapters."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable
from contextlib import aclosing
from dataclasses import replace
from typing import Literal, overload

from .. import store, wire
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..store.inference.resolved import ResolvedInference
from . import _requests


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
    chain = _requests._generating(task, resolved)
    if max_tokens is not None:
        _requests._check_cap(max_tokens)
    if schema is None:
        chain = _requests.call_chain(resolved, max_tokens=max_tokens)
        if stream:
            return client.stream(messages, chain, usage)
        return client.complete(messages, chain, usage)
    _requests._check_structured(messages, schema)
    chain = _requests.call_chain(resolved, schema=schema, max_tokens=max_tokens)

    # The re-sends, nested here so they forward this call's `usage` as the
    # facade calls above do (`test_usage_guard.FORWARDERS`).
    async def resend(sending: wire.Chain) -> str:
        with _requests._counting_earlier(usage):
            return await client.complete(messages, sending, usage, schema=schema)

    async def reopen(sending: wire.Chain) -> AsyncGenerator[str, None]:
        with _requests._counting_earlier(usage):
            async with aclosing(client.stream(messages, sending, usage,
                                              schema=schema)) as again:
                async for chunk in again:
                    yield chunk

    if stream:
        return _requests._streamed_structured(client.stream(messages, chain, usage, schema=schema),
                                    reopen)
    return _requests._joined_structured(client.complete(messages, chain, usage, schema=schema), resend)


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
