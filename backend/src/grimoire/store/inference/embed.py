"""The embed operation: the one metered door to the embeddings endpoint
(spec 7.3, 9.3, 9.4; slice D).

`embed_sync` is what every store module that embeds calls -- lore recall,
library search, art ranking and continuity similarity -- and `embed` is the
same call in a worker thread. Each call files **one** ledger row under its
embed task (`routing.EMBED_TASKS`) with `operation: "embed"`, covering every
batch the client splits it into, and writes one Debug-level capture line.

**The space is handed in, never resolved here.** Every caller reads its
vector cache under a space before it embeds; resolving the Embedding role
again inside the call could embed with one model and save the vectors under
another space's key (rule 4: embeddings never mix spaces). So the caller passes
the `space` it read with (`embed_space.endpoint()`) and the `client` it already
owns -- each module's client is also its test seam. Deadlines, the vector cache
and degradation stay with the caller.

**Privacy (CLAUDE.md, Observability; spec 9.4).** A failure is recorded where
every LLM failure is, at `usage.Meter.done`, and its recorded detail is the
error kind and HTTP status only (`record_failure`): a provider's error body can echo the
text it was sent, and a redirect's `Location` can carry a key. The exception
itself is re-raised untouched, so the caller -- and the model test's verdict --
still sees the provider's own words. The capture line carries counts, never
text, a provider message or a URL.

**Nothing sent, nothing filed** (spec 9.3). An empty input returns ``[]``
with no row and no line, and a deadline already spent when the call starts
raises the client's own "not sent" error before any meter opens. A deadline
that lapses after the meter opened but before the first request
(`embeddings.NOT_SENT`) files nothing either -- no row, no error, no capture
line: no request went out.

**The caller's clock is not the provider's failure.** When a `budgeted`
deadline -- the caller's own budget, as absorb's identity check and the
continuity sweep hand in -- cuts a request that did go out
(`embeddings.DEADLINE`), the row is `aborted` and nothing reaches the error
store, the line `routes.scenes._embedding_reason` draws for the absorb. A
deadline that only bounds the provider (the client's own `TIMEOUT`, or lore
recall's and search's, which share one `TIMEOUT` across a retry) cutting a slow
provider is that provider failing: an error like any other.

**Never on the app's loop** (roadmap 01h-C4a). The client is synchronous, so
an `embed_sync` made on the lifespan's event loop would freeze every other
request for as long as the endpoint took. `runner.install` marks that loop
(`mark_app_loop`) and the lifespan's exit unmarks it, and a call made while it
is the running loop is refused before any meter as `network`/`ON_LOOP`, which
every caller already reads as "the endpoint is unavailable, do not retry", so
the turn degrades to keyword rather than stalling. It is the one exception to
"nothing sent, nothing filed": the refusal writes ONE error row (the task, the
kind, the code, the campaign and scene it was handed, and the caller's
innermost frames as `file:line in function` -- never its text), because it
records a programming error rather than a call, and the frames are what find
the caller a dozen frames below a route. A worker, a CLI, a thread calling
through a portal and a private `asyncio.run` loop are never refused; an
`async def` caller reaches a compose or an embed through `run_in_threadpool`.
"""

from __future__ import annotations

import asyncio
import threading
import time
import traceback
from pathlib import PurePath
from typing import Any

from ... import embeddings, llm_usage, wire
from .. import errors, logs, routing, tokens, usage

#: The `code` of the refusal on the app's loop (see the module docstring). Its
#: kind is `network`.
ON_LOOP = "on_loop"

#: How many of the caller's frames the refusal's error row keeps. Enough to
#: reach from `embed_sync` up past the whole compose path (recall is about a
#: dozen frames below the route function that composed) to the route module
#: that called it; each is one short `file:line in function` line, so the row
#: stays far inside `logs.MAX_TRACE` and is never clipped from the caller's end.
ON_LOOP_FRAMES = 16

#: The app loops, each with how many live lifespans marked it.
#:
#: Module state, not `app.state`, deliberately: the spec puts the registry
#: here, and the guard runs frames below any route, in store code that has no
#: app in hand. The loop OBJECT rather than its thread's ident, because an
#: ident is reused once its thread ends and a loop object is not. A count
#: rather than a set because a test suite builds an app per test, and two apps
#: sharing one loop (or one lifespan re-entered) must not unmark each other's.
_APP_LOOPS: dict[asyncio.AbstractEventLoop, int] = {}
_APP_LOOPS_LOCK = threading.Lock()


def mark_app_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Say that `loop` is an app's lifespan loop (`runner.install`)."""
    with _APP_LOOPS_LOCK:
        _APP_LOOPS[loop] = _APP_LOOPS.get(loop, 0) + 1


def unmark_app_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Undo one `mark_app_loop(loop)`; a no-op for a loop nobody marked."""
    with _APP_LOOPS_LOCK:
        left = _APP_LOOPS.get(loop, 0) - 1
        if left > 0:
            _APP_LOOPS[loop] = left
        else:
            _APP_LOOPS.pop(loop, None)


def app_loop_marked(loop: asyncio.AbstractEventLoop) -> bool:
    """Whether `loop` is marked as an app loop."""
    return loop in _APP_LOOPS


def _on_app_loop() -> bool:
    """Whether this call runs on a marked app loop: the loop running in this
    thread, if any, is one `runner.install` marked.

    Not "is any loop running": a private `asyncio.run` loop serves nobody
    else, and blocking it blocks no one. A worker thread, a CLI, and a thread
    calling through a portal run no loop of their own, so none is refused.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return app_loop_marked(loop)


def _caller_frames() -> str:
    """The caller's innermost `ON_LOOP_FRAMES` frames, outermost first, as
    `<dir>/<module>:<line> in <function>` -- no source text and no locals. This
    module's own two frames are left out."""
    frames = traceback.extract_stack()[:-2][-ON_LOOP_FRAMES:]
    return "".join(f"{'/'.join(PurePath(f.filename).parts[-2:])}:{f.lineno} in {f.name}\n"
                   for f in frames)


def _refuse_on_loop(task: str, *, campaign: str, scene: str) -> None:
    """Raise the `ON_LOOP` refusal when called on an app's loop, after
    writing its one error row (the module docstring says why it files one).
    The exception is marked recorded, so a caller that records it again adds
    nothing."""
    if not _on_app_loop():
        return
    exc = embeddings.EmbeddingsError("network", "embedding refused on the event loop",
                                     code=ON_LOOP)
    errors.record(task, "network", ON_LOOP, campaign=campaign, scene=scene, task=task,
                  trace=_caller_frames())
    errors.mark_recorded(exc)
    raise exc


def _stamp(holder: dict, space: dict) -> None:
    """Stamp the meter's holder with the endpoint about to be called.

    The identity keys `llm._stamp` writes for a chat attempt -- `model`,
    `connection`, `provider`, `attempts` -- plus `operation`. A stamped holder
    is what makes `Meter.done` file a row at all (an empty one means "never
    sent"). The provider fields are read with `.get`, because a test double's
    space may carry only the four keys `embed_space.resolve` returns.

    Then what served the call (slice E, spec 9.3): `requested_model`, the
    model asked for (the client refills `model` with what the endpoint
    named), and `llm_usage.account` over the attempt's `target` --
    `provider_id`, the provider's `billing`, and the account
    `resolve.embedding` stamped (`operation: "embed"`, `role: "embedding"`).
    A space without a `target` (a test double's) files no account fields; the
    identity keys still make the row. If what those keys mean changes,
    `llm._stamp` and this change together.
    """
    holder.update({
        "model": space["model"],
        "connection": space.get("provider_name") or space.get("provider", ""),
        "provider": space.get("provider_kind", ""),
        "attempts": 1,
        "operation": usage.EMBED_OPERATION,
        "requested_model": space["model"],
    })
    target = space.get("target")
    if isinstance(target, wire.Target):
        llm_usage.account(holder, target)


def estimate_prompt(holder: dict, texts: list[str]) -> None:
    """Count locally the prompt a provider did not report, for an embed call
    that returned (spec 9.1; slice E owns this, ruling I1).

    Only when the holder carries no usable `prompt_tokens` -- a reported count
    is never replaced -- and only by an encoder that has already loaded, else
    the characters/4 heuristic (`tokens.count_if_loaded`): never a load on the
    request path. The row then says the count was estimated
    (`tokens_estimated`), as a chat attempt's does. Never a completion count:
    an embedding generates nothing, and `usage._completion_count` reads its
    absence as the structural zero it is. Never raises -- bookkeeping beside a
    call that has already returned."""
    try:
        if llm_usage.tokens(holder.get("prompt_tokens")) is not None:
            return
        count = sum(tokens.count_if_loaded(t) for t in texts if isinstance(t, str))
        # Ended on its own: the one kind of attempt `Meter` lets carry the flag.
        holder[llm_usage.ENDED_KEY] = True
        llm_usage.fill(holder, count, None)
    except Exception:  # noqa: BLE001 - see the docstring
        return


def record_failure(meter: usage.Meter, exc: BaseException, *,
                   own_deadline: bool = False) -> str | None:
    """Finish `meter` for an embed that raised `exc`, and return what the
    capture line names it: `aborted`, the error kind, or None when nothing
    was sent and there is no line to write.

    `embeddings.NOT_SENT` files nothing at all: the deadline lapsed before the
    first request, so nothing failed and nothing was spent (spec 9.3). The
    holder is cleared first, because a stamped holder is what makes
    `Meter.done` write a row. `embeddings.DEADLINE` with `own_deadline` (a
    `budgeted` deadline: the caller's own budget) is `aborted`: a row for the request that
    went out, and no error, because the caller's clock is not the provider
    failing. Anything else is an error whose recorded detail is the kind and
    HTTP status ONLY -- never `str(exc)`, which can hold a provider body
    echoing the input or a redirect's `Location` carrying a key (I1). The
    caller re-raises `exc` unchanged; `Meter.__exit__` then finds the meter
    done.

    Shared with the model test's embed probe (`routes/config._embed_probe`),
    so both doors record a failure the same way; the probe hands no deadline
    in, so a slow provider there is an error.
    """
    code = getattr(exc, "code", None)
    if code == embeddings.NOT_SENT:
        meter.usage.clear()
        meter.done("aborted")
        return None
    if code == embeddings.DEADLINE and own_deadline:
        meter.done("aborted")
        return "aborted"
    kind = getattr(exc, "kind", None) or type(exc).__name__
    status = getattr(exc, "status", None)
    meter.done("error", kind, detail=f"{kind} (HTTP {status})" if status else kind, exc=exc)
    return kind


def embed_sync(task: str, texts: list[str], *, space: dict,
               client: embeddings.EmbeddingsClient, deadline: float | None = None,
               budgeted: bool = False, campaign: str = "", scene: str = "",
               cached: int | None = None,
               uncached: int | None = None) -> list[list[float]]:
    """Embed `texts` at `space` through `client`: one vector per input, in
    input order. Metered under `task`, which must be an embed task.

    `space` is the endpoint dict the caller read its cache under
    (`embed_space.endpoint()`); it is never re-resolved here. `deadline` is an
    absolute `time.monotonic()` instant, handed to the client; `budgeted` says
    it is the caller's own budget, so a request it cuts is `aborted` rather
    than a provider failure (see the module docstring). `campaign` and
    `scene` attribute the row and the capture line (never `cid`: nothing here
    mutates a campaign). `cached` and `uncached` are the caller's cache hits
    and misses, for the capture line only -- misses include texts left outside
    this call's warm window, so neither is `len(texts)`. They count a run, so
    a caller hands them to the run's first call alone; a retry or a later
    chunk of the same run passes neither.

    Raises `ValueError` for a task that is not an embed task, before anything
    else; `EmbeddingsError(..., code=ON_LOOP)` on an app's loop, before
    anything is sent or metered (see the module docstring); and whatever the
    client raises, unchanged.
    """
    if task not in routing.EMBED_TASKS:
        raise ValueError(f"{task!r} is not an embed task")
    if not texts:
        return []
    _refuse_on_loop(task, campaign=campaign, scene=scene)
    if deadline is not None and time.monotonic() >= deadline:
        # The client's own kind and wording, raised outside any meter: nothing
        # was sent, so nothing is filed.
        raise embeddings.EmbeddingsError(
            "network", "embeddings deadline passed before the request",
            code=embeddings.NOT_SENT)
    vectors: list[list[float]] | None = None
    # "" while nothing failed; None once a failure turned out to send nothing.
    error: str | None = ""
    try:
        with usage.meter(task, campaign=campaign, scene=scene, model=space["model"]) as m:
            _stamp(m.usage, space)
            try:
                vectors = client.embed(texts, space["model"], space["key"], space["base_url"],
                                       deadline=deadline, usage=m.usage)
            except Exception as exc:
                error = record_failure(m, exc, own_deadline=budgeted and deadline is not None)
                raise
            estimate_prompt(m.usage, texts)
    finally:
        if error is not None:
            _capture(task, texts, space, campaign=campaign, scene=scene, vectors=vectors,
                     error=error, cached=cached, uncached=uncached)
    # A meter never swallows what it records (`Meter.__exit__` returns False),
    # so reaching here means the client returned.
    assert vectors is not None
    return vectors


def _capture(task: str, texts: list[str], space: dict, *, campaign: str, scene: str,
             vectors: list[list[float]] | None, error: str,
             cached: int | None, uncached: int | None) -> None:
    """The call's one Debug-level line (spec 9.4): what was sent, counted,
    and never the text, a provider's message or a URL. Its keys are the
    spec's: `inputs`, `bytes`, `dimension`, `space_id`, `cached` and
    `uncached` (a caller's whole run's hits and misses, which it hands to the
    run's first call only, so a reader summing lines counts them once), the
    campaign and scene, and `error` -- the kind, or `aborted`."""
    # Absent, not None, when a count does not apply: `logs.record` would write
    # a None through as `null`. `Any` because `**` into a signature with typed
    # keywords of its own is otherwise checked against every one of them.
    extra: dict[str, Any] = {}
    if vectors:
        extra["dimension"] = len(vectors[0])
    if error:
        extra["error"] = error
    if cached is not None:
        extra["cached"] = cached
    if uncached is not None:
        extra["uncached"] = uncached
    logs.record("debug", "embed", "Embedding call", kind="embed", task=task,
                campaign=campaign, scene=scene, inputs=len(texts),
                bytes=sum(len(t.encode("utf-8")) for t in texts),
                space_id=space["space"], ok=vectors is not None, **extra)


async def embed(task: str, texts: list[str], *, space: dict,
                client: embeddings.EmbeddingsClient, deadline: float | None = None,
                budgeted: bool = False, campaign: str = "", scene: str = "",
                cached: int | None = None,
                uncached: int | None = None) -> list[list[float]]:
    """`embed_sync` in a worker thread: the client is synchronous by design
    (`embeddings`' docstring), so this is the form for a caller on the loop."""
    return await asyncio.to_thread(
        embed_sync, task, texts, space=space, client=client, deadline=deadline,
        budgeted=budgeted, campaign=campaign, scene=scene, cached=cached,
        uncached=uncached)
