"""Provider-agnostic LLM surface: the dispatch facade over the providers.

The shared error type lives in `llm_errors.py`, not here — see its docstring.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from .claude_agent import ClaudeAgentClient
from .llm_errors import LLMError
from .openai_compatible import OpenAICompatibleClient
from .openrouter import OpenRouterClient

# Fallback for a client constructed with no timeout of its own. The real value
# comes from config.md via the resolver routes injects (#243); this only covers
# callers that inject nothing.
DEFAULT_TIMEOUT = 120.0
# How long the facade waits for the next provider frame before telling the
# caller it is still alive (#95). Chosen well under the idle timeouts proxies
# and load balancers apply to a quiet connection (commonly 30-60s), because the
# gap this covers -- connect plus time-to-first-token, or a model reasoning
# silently -- otherwise looks exactly like a hung stream from outside.
# ``tick <= 0`` disables it.
HEARTBEAT_INTERVAL = 15.0
# Grace period for a provider to unwind. aclose() throws GeneratorExit into it,
# which unwinds httpx's stream context manager — normally instant, but the
# connection being closed is by definition a sick one, so cleanup gets its own
# bound.
_CLOSE_TIMEOUT = 5.0


def _swallow(task: asyncio.Task) -> None:
    """Retrieve a finished cleanup's exception so asyncio doesn't log it as
    never-retrieved. Cleanup failures are deliberately not re-raised: by the
    time we are closing, the caller either has its reply or is already being
    told about a timeout, and neither should turn into a different error."""
    if not task.cancelled():
        task.exception()


def _close_when_settled(task: asyncio.Task, agen) -> None:
    """Close an abandoned pull's iterator as soon as that pull finally lets go.

    Without this, skipping the close (which is mandatory while __anext__ runs)
    would mean never closing at all, and each wedged call would strand its
    connection for the life of the process.
    """
    _swallow(task)
    try:
        asyncio.ensure_future(_aclose(agen)).add_done_callback(_swallow)
    except RuntimeError:
        pass  # no running loop (shutdown): nothing left to close into


async def _settle(task: asyncio.Task | None, agen) -> bool:
    """Make sure a pull is over, and report whether its iterator may be closed.

    ``asyncio.wait_for`` is not usable here, and neither is a plain
    ``await task`` after cancelling: both wait for the cancellation they
    request to *finish*, so an upstream that ignores CancelledError turns
    right back into the indefinitely-held request this module exists to
    prevent. So the cancel gets a grace period and is then abandoned — but an
    abandoned pull is still running inside the iterator, and closing an
    iterator mid-``__anext__`` raises, so the caller is told not to close it.
    A detached task is a leak we can live with; a wedged request is not.
    """
    if task is None:
        return True
    if not task.done():
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=_CLOSE_TIMEOUT)
        if not done:
            task.add_done_callback(lambda t: _close_when_settled(t, agen))
            return False
    _swallow(task)
    return True


async def _aclose(agen) -> None:
    """Close the provider under the same grace-then-abandon rule as _settle:
    cleanup that will not unwind must not be allowed to hold the caller."""
    task = asyncio.ensure_future(agen.aclose())
    task.add_done_callback(_swallow)
    done, _ = await asyncio.wait({task}, timeout=_CLOSE_TIMEOUT)
    if not done:
        task.cancel()  # asked to stop; deliberately not awaited


async def _guard(agen, timeout: float, tick: float | None = None) -> AsyncIterator[str]:
    """Bound the gap between deltas, whatever the provider underneath.

    The wait covers connect + time-to-first-token on the first pull and
    mid-stream stalls on every later one, so a wedged upstream surfaces as an
    LLMError instead of holding an SSE connection open forever (#243). It is
    deliberately an *idle* bound, not a total one: a slow-but-progressing
    generation is healthy and must not be cut off mid-prose. Callers that need
    a ceiling on total duration impose it themselves (see routes' absorb
    budget). ``timeout <= 0`` disables the bound entirely.

    The bound counts provider *activity*, not visible text: a provider yields
    an empty string for a frame that carries no content (a keep-alive, or the
    reasoning a model can stream for minutes before its first word), which
    resets the wait and is dropped here rather than reaching the caller. Timing
    only yielded text would cancel healthy long-reasoning generations that the
    providers' old HTTP read timeout let run.

    Deltas already received are yielded before the timeout raises, so a partial
    reply stays recoverable by the fence watcher's on_error path.

    Every `tick` seconds without caller-visible text, an empty string is yielded:
    the facade's own liveness signal (#95), for callers that have to keep a
    connection of their own visibly alive. A provider's empty frame does NOT
    satisfy it and is still not forwarded — the two carry different information,
    and only this one is on a schedule the caller picked. So `""` reaching a
    caller means "no text yet, still connected", and never anything else;
    `complete()` joins it away for callers that only want the finished string.

    The two clocks are deliberately different, and conflating them was a bug
    caught in review. The idle bound measures *provider* activity, so every
    frame resets it, empty ones included. The tick measures what the *caller*
    has seen, so it spans pulls and only text resets it. Reset per pull instead
    and the adapters defeat it completely: they yield "" for every upstream SSE
    line (see `openrouter.stream`), so a model streaming reasoning fires frames
    far faster than the interval, restarts the clock each time, and the caller's
    connection stays silent for exactly as long as it would have without a
    heartbeat at all.
    """
    # Read at call time, not bound as a default: the constant is the knob tests
    # and any future config path turn, and a default argument would freeze
    # whatever it happened to be at import.
    tick = HEARTBEAT_INTERVAL if tick is None else tick
    it = agen.__aiter__()
    pull: asyncio.Task | None = None
    next_tick = (time.monotonic() + tick) if tick > 0 else None
    try:
        while True:
            pull = asyncio.ensure_future(it.__anext__())
            # The idle bound restarts here, on each new pull. The tick does not.
            deadline = None if timeout <= 0 else time.monotonic() + timeout
            while True:
                # Whichever clock is nearer sets the sleep; a tick therefore
                # consumes part of the timeout's budget rather than extending it
                # -- ticking forever is exactly the "never times out" failure the
                # bound exists to prevent.
                due = [t for t in (deadline, next_tick) if t is not None]
                wait = max(0.0, min(due) - time.monotonic()) if due else None
                done, _ = await asyncio.wait({pull}, timeout=wait)
                if done:
                    break
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise LLMError(
                        "timeout", f"the model sent nothing for {timeout:g}s — giving up")
                if next_tick is not None and now >= next_tick:
                    next_tick = now + tick
                    yield ""  # still waiting on the provider; tell the caller so
            try:
                chunk = pull.result()  # re-raises the provider's own LLMError
            except StopAsyncIteration:
                return
            if chunk:
                next_tick = (time.monotonic() + tick) if tick > 0 else None
                yield chunk
            elif next_tick is not None and time.monotonic() >= next_tick:
                # A provider frame that carries no text, arriving on a stream so
                # chatty that the wait above never expires. The pull loop is the
                # only other place the clock can be read, so an overdue tick has
                # to fire from here or a busy-but-silent model never produces one.
                next_tick = time.monotonic() + tick
                yield ""
    finally:
        # Every exit settles the outstanding pull and closes the provider: on a
        # timeout the pull is cancelled here, and on a caller-side close (an SSE
        # client disconnecting) this is what still propagates the close down to
        # httpx — which iterating the provider directly used to do for free.
        if await _settle(pull, it):
            await _aclose(it)


class LLMClient:
    """Dispatches each call to the resolved connection's kind."""

    def __init__(self, openrouter=None, claude=None, openai_compatible=None, timeout=None):
        self._openrouter = openrouter if openrouter is not None else OpenRouterClient()
        self._claude = claude if claude is not None else ClaudeAgentClient()
        self._openai_compatible = (openai_compatible if openai_compatible is not None
                                    else OpenAICompatibleClient())
        # A number, or a callable returning one. Callable is how routes hands
        # over the config.md setting without this module importing the store —
        # the gateway's imports are kept acyclic and store-free on purpose
        # (#239) — and resolving per call is also what lets a Configuration-page
        # change land without a restart.
        self._timeout = timeout

    def _timeout_seconds(self) -> float:
        if self._timeout is None:
            return DEFAULT_TIMEOUT
        return float(self._timeout() if callable(self._timeout) else self._timeout)

    def _dispatch(self, messages: list[dict], conn: dict):
        kind = conn.get("kind", "openrouter")
        if kind == "claude":
            return self._claude.stream(messages, conn.get("model") or "opus")
        if kind == "openai_compatible":
            return self._openai_compatible.stream(
                messages, conn.get("model", ""), conn.get("api_key", ""),
                conn.get("base_url", ""), strict=conn.get("post_process") == "strict")
        return self._openrouter.stream(messages, conn["model"], conn.get("api_key", ""))

    def stream(self, messages: list[dict], conn: dict):
        """Every provider stream leaves the facade idle-bounded — the one place
        the bound is provider-independent (the Claude SDK has no httpx client
        to configure at all)."""
        return _guard(self._dispatch(messages, conn), self._timeout_seconds())

    async def complete(self, messages: list[dict], conn: dict) -> str:
        return "".join([chunk async for chunk in self.stream(messages, conn)])

    async def aclose(self) -> None:
        await self._openrouter.aclose()
        await self._openai_compatible.aclose()
