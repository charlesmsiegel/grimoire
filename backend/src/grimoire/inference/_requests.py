"""Per-call target preparation and refused-schema resends shared by operations."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import aclosing, contextmanager
from dataclasses import replace
from typing import TypeVar

from .. import llm, llm_sampling, model_guidance, schemas, wire
from ..llm_errors import LLMError
from ..store.inference import resolve
from ..store.inference.resolved import ResolvedInference

T = TypeVar("T")


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


# ---- generate (spec 7.2) ----

def _capped(target: wire.Target, max_tokens: int) -> wire.Target:
    return target.with_output_cap(max_tokens, most=clamp_to_max_output(target, max_tokens))


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


def _tool_chain(resolved: ResolvedInference, chain: wire.Chain
                ) -> tuple[wire.Chain, wire.Target | None]:
    """`chain` as a tool run sends it, and the fallback left out: one known
    (`no`) unable to call tools never rides (the resolver drops one only on a
    route that requires them). The loop's `_chain` and the spend preflight
    (`tool_run_refusal`) share it, so a run is never refused over a model it
    would not send."""
    if chain.fallback is not None and resolve.known_lacks(resolved, "tools", fallback=True):
        return wire.Chain(chain.primary), chain.fallback
    return chain, None
