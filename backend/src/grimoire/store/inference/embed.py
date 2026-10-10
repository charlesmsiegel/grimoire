"""The embed operation: the one metered door to the embeddings endpoint
(spec 7.3, 9.3, 9.4; slice D).

`embed_sync` is what every store module that embeds calls -- lore recall,
library search, art ranking and continuity similarity -- through a module's
synchronous `embeddings.EmbeddingsClient`, and `embed` is the same call,
natively async, through the app's `embeddings.AsyncEmbeddingsClient` for a
caller already in a coroutine (01h-C4b; no caller yet). The two share their
checks, meter, stamp, failure record and capture line, so they cannot
drift. Each call files **one** ledger row under its
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

**Options and queries** (roadmap 01h-S2). The endpoint dict carries the
model's stated embedding options (`space["options"]`, a `wire.EmbedOptions`;
the defaults when a test double's dict has none), the object its space id was
computed from, and the request is built from that object -- so what is sent
and where it is cached cannot disagree, and a dict whose options do not match
its id is refused before anything is metered. A caller puts its queries first
and says how many (`queries`); the rest are documents. A query vector is never
cached by any caller. NUL is removed from every text before it is sent, as
`vectors._path` removes it from the key. The capture line and a locally
counted prompt count what was sent, prefixes included. In the `param` input
type the client sends the queries and the documents as separate requests,
still one call: one row, one line. A reply that ignored a requested
`dimensions` is `missing_key`/`embeddings.DIMENSIONS_MISMATCH`, which no
caller retries, and its error row names that code (01h-S3).
"""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from ... import embeddings, llm_usage, wire
from .. import errors, logs, routing, tokens, usage

#: What an error `code` must look like to be filed beside the kind and status
#: (`record_failure`): a token this side names, never a sentence.
_CODE = re.compile(r"[a-z_]{1,40}")

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
    is what makes `Meter.done` file a row at all (an unstamped one means "never
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
    HTTP status ONLY, with the error's `code` when it is a fixed token
    (`dimensions_mismatch`) -- never `str(exc)`, which can hold a provider body
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
    detail = f"{kind} (HTTP {status})" if status else kind
    if isinstance(code, str) and _CODE.fullmatch(code):
        # A fixed token this side named (`embeddings.DIMENSIONS_MISMATCH`),
        # never provider text: what lets the error store say why (01h §3.4).
        detail += f", code {code}"
    meter.done("error", kind, detail=detail, exc=exc)
    return kind


def _sendable(space: dict, texts: list[str],
              queries: int) -> tuple[wire.EmbedOptions, list[str]]:
    """`(options, texts as sent)` for a call, or a `ValueError` before anything
    is sent or metered: a `queries` out of range, options this build cannot
    send, or options that do not agree with the space id they ride beside --
    the id carries the `embopt1` tag exactly when the options' document side
    is not the default, and then their digest (`resolve.space_of`). The texts
    lose any NUL, which no provider takes and which would let the NUL-joined
    vector key collide."""
    embeddings.check_queries(queries, len(texts))
    options = space.get("options")
    options = wire.EmbedOptions() if options is None else options
    embeddings.check_sendable(options)
    tag = f"\0{wire.EMBED_OPTIONS_TAG}:"
    space_id = str(space["space"])
    agrees = (tag not in space_id if options.is_default()
              else space_id.endswith(tag + options.digest()))
    if not agrees:
        raise ValueError("the embedding space and its options disagree")
    return options, [t.replace("\0", "") for t in texts]


def _admit(task: str, texts: list[str], space: dict,
           queries: int) -> tuple[wire.EmbedOptions, list[str]]:
    """What both doors check before anything else: an embed task, then
    `_sendable`. A `ValueError` before anything is sent or metered."""
    if task not in routing.EMBED_TASKS:
        raise ValueError(f"{task!r} is not an embed task")
    return _sendable(space, texts, queries)


def _not_sent_if_spent(deadline: float | None) -> None:
    """The client's own kind and wording for a deadline already spent, raised
    outside any meter: nothing was sent, so nothing is filed."""
    if deadline is not None and time.monotonic() >= deadline:
        raise embeddings.EmbeddingsError(
            "network", "embeddings deadline passed before the request",
            code=embeddings.NOT_SENT)


def _meter(task: str, space: dict, *, campaign: str, scene: str, post: int | None,
           run_id: str) -> usage.Meter:
    """The call's one meter, stamped by the caller (`_stamp`) once it is open."""
    return usage.meter(task, campaign=campaign, scene=scene, model=space["model"],
                       post=post, run_id=run_id)


def _sync_client(client: object) -> None:
    """A sync client is never awaited, and an async one never handed to
    `embed_sync` (01h, Required by)."""
    if inspect.iscoroutinefunction(getattr(client, "embed", None)):
        raise TypeError("embed_sync takes a synchronous embeddings client; await embed()")


def embed_sync(task: str, texts: list[str], *, space: dict,
               client: embeddings.EmbeddingsClient, deadline: float | None = None,
               budgeted: bool = False, campaign: str = "", scene: str = "",
               cached: int | None = None,
               uncached: int | None = None, queries: int = 0,
               run_id: str = "", post: int | None = None) -> list[list[float]]:
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
    chunk of the same run passes neither. `queries` is how many of `texts`,
    from the first, are queries (01h); the rest are documents. `run_id`
    names the caller's run (01g-C3) on the row and the line; `post` is the
    transcript index of the player post a turn is answering, filed on the row
    as a turn's generation rows file it. Neither resolves anything.

    Raises `ValueError` for a task that is not an embed task, before anything
    else, and for a `queries` out of range or a space whose options cannot be
    sent or do not match it (`_sendable`), before anything is metered;
    `TypeError` for an async client; `EmbeddingsError(..., code=ON_LOOP)` on
    an app's loop, before anything is sent or metered (see the module
    docstring); and whatever the client raises, unchanged.
    """
    options, clean = _admit(task, texts, space, queries)
    if not clean:
        return []
    _sync_client(client)
    _refuse_on_loop(task, campaign=campaign, scene=scene)
    _not_sent_if_spent(deadline)
    vectors: list[list[float]] | None = None
    # What goes out, prefixes included: what the line and a local count count.
    sent = embeddings.prepare_inputs(clean, options, queries)
    # "" while nothing failed; None once a failure turned out to send nothing.
    error: str | None = ""
    try:
        with _meter(task, space, campaign=campaign, scene=scene, post=post,
                    run_id=run_id) as m:
            _stamp(m.usage, space)
            try:
                vectors = client.embed(clean, space["model"], space["key"], space["base_url"],
                                       deadline=deadline, usage=m.usage,
                                       options=options, queries=queries)
            except Exception as exc:
                error = record_failure(m, exc, own_deadline=budgeted and deadline is not None)
                raise
            estimate_prompt(m.usage, sent)
    finally:
        if error is not None:
            _capture(task, sent, space, campaign=campaign, scene=scene, vectors=vectors,
                     error=error, cached=cached, uncached=uncached, run_id=run_id)
    # A meter never swallows what it records (`Meter.__exit__` returns False),
    # so reaching here means the client returned.
    assert vectors is not None
    return vectors


def _capture(task: str, texts: list[str], space: dict, *, campaign: str, scene: str,
             vectors: list[list[float]] | None, error: str,
             cached: int | None, uncached: int | None, run_id: str = "") -> None:
    """The call's one Debug-level line (spec 9.4): what was sent, counted,
    and never the text, a provider's message or a URL. Its keys are the
    spec's: `inputs`, `bytes`, `dimension`, `space_id`, `cached` and
    `uncached` (a caller's whole run's hits and misses, which it hands to the
    run's first call only, so a reader summing lines counts them once), the
    campaign and scene, the caller's `run_id` when it names one, and `error`
    -- the kind, or `aborted`."""
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
    if run_id:
        extra["run_id"] = run_id
    logs.record("debug", "embed", "Embedding call", kind="embed", task=task,
                campaign=campaign, scene=scene, inputs=len(texts),
                bytes=sum(len(t.encode("utf-8")) for t in texts),
                space_id=space["space"], ok=vectors is not None, **extra)


async def embed(task: str, texts: list[str], *, space: dict,
                client: embeddings.AsyncEmbeddingsClient, deadline: float | None = None,
                budgeted: bool = False, campaign: str = "", scene: str = "",
                cached: int | None = None,
                uncached: int | None = None, queries: int = 0,
                run_id: str = "", post: int | None = None) -> list[list[float]]:
    """`embed_sync`, natively async (roadmap 01h-C4b): the same keywords,
    checks, row, failure record and capture line, through the app's
    `embeddings.AsyncEmbeddingsClient` (`routes.get_embeddings`), whose whole
    call runs under one deadline. Never refused on the loop -- it is the
    loop's door.

    A cancel while the request is out files the meter `aborted` (never an
    error row; `usage.Meter.__exit__`) and the line `error: "aborted"`, and
    propagates. A locally estimated prompt count is taken in a worker
    thread, since a loaded encoder counts synchronously. Raises `TypeError`
    for a synchronous client."""
    options, clean = _admit(task, texts, space, queries)
    if not clean:
        return []
    if not inspect.iscoroutinefunction(getattr(client, "embed", None)):
        raise TypeError("embed takes an async embeddings client; call embed_sync instead")
    _not_sent_if_spent(deadline)
    vectors: list[list[float]] | None = None
    sent = embeddings.prepare_inputs(clean, options, queries)
    error: str | None = ""
    try:
        with _meter(task, space, campaign=campaign, scene=scene, post=post,
                    run_id=run_id) as m:
            _stamp(m.usage, space)
            try:
                vectors = await client.embed(clean, space["model"], space["key"],
                                             space["base_url"], deadline=deadline,
                                             usage=m.usage, options=options, queries=queries)
            except asyncio.CancelledError:
                # The meter files it `aborted` on the way out; never an error.
                error = "aborted"
                raise
            except Exception as exc:
                error = record_failure(m, exc, own_deadline=budgeted and deadline is not None)
                raise
            await asyncio.to_thread(estimate_prompt, m.usage, sent)
    finally:
        if error is not None:
            _capture(task, sent, space, campaign=campaign, scene=scene, vectors=vectors,
                     error=error, cached=cached, uncached=uncached, run_id=run_id)
    assert vectors is not None
    return vectors


# ---- cross-campaign attribution (roadmap 01h-C5) ----

@dataclass(frozen=True)
class EmbedGroup:
    """Documents one campaign's spend covers: one ledger row, one capture
    line, and never a request shared with another group. `campaign` "" is
    unattributed: a text several campaigns (or none) claim, whose spend
    counts toward no campaign's budget -- the global Costs totals only, as
    library-wide search's rows do."""

    campaign: str
    scene: str = ""
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupResult:
    """What became of one group: its vectors in the group's text order, or
    None with the error kind -- `not_sent` for a group the run stopped
    before (nothing sent, nothing filed)."""

    vectors: list[list[float]] | None
    error: str = ""


#: The `GroupResult.error` of a group never sent.
GROUP_NOT_SENT = embeddings.NOT_SENT


def attribute(claims: Iterable[tuple[str, str]]) -> list[EmbedGroup]:
    """Group `(campaign, text)` claims by who pays for each text.

    A text claimed by exactly one campaign goes to that campaign's group; a
    text claimed by several, or by none (`""`, world-scoped), goes to the
    unattributed group, embedded once. Charging it to whichever campaign
    sorted first would put one campaign's spend in another's total, and
    apportioning a provider's whole-request figure would charge each a number
    nobody reported. The unattributed group comes FIRST -- its texts serve
    several campaigns, so a run cut short should not always starve them --
    then the campaigns by id; texts keep the order they were first claimed
    in. Deterministic for the same claims."""
    order: list[str] = []
    owners: dict[str, set[str]] = {}
    for campaign, text in claims:
        if text not in owners:
            order.append(text)
            owners[text] = set()
        owners[text].add(campaign)
    by: dict[str, list[str]] = {}
    for text in order:
        who = owners[text]
        payer = next(iter(who)) if len(who) == 1 else ""
        by.setdefault(payer, []).append(text)
    return [EmbedGroup(campaign, texts=tuple(by[campaign]))
            for campaign in sorted(by, key=lambda c: (c != "", c))]


def embed_groups_sync(task: str, groups: Sequence[EmbedGroup], *, space: dict,
                      client: embeddings.EmbeddingsClient, deadline: float | None = None,
                      budgeted: bool = False, run_id: str = "",
                      cached: int | None = None, uncached: int | None = None,
                      on_group: Callable[[EmbedGroup, GroupResult], None] | None = None,
                      ) -> list[GroupResult]:
    """Embed each group's documents as its own `embed_sync` call, in order,
    under ONE deadline taken at entry (`embeddings.TIMEOUT` from now when
    none is given -- never a fresh one per group): one row and one capture
    line per group, attributed to its campaign and scene, and no request
    that mixes groups. Documents only: a query belongs to one campaign's
    turn, which uses `embed_sync`.

    A `bad_response` (the input's fault) fails its group and the next still
    runs. Any other failure -- `auth`, `missing_key` (a width-ignoring
    endpoint's `dimensions_mismatch` included), `rate_limit`, `network`, the
    deadline -- stops the run: every later group is `not_sent` and files
    nothing, the split `semantic._embed` makes. `on_group(group, result)` is
    called for each group that was tried, as its result lands and before the
    next is sent, so the caller can save what landed; one that raises stops
    the run the same way (its exception is recorded, not raised). `cached`
    and `uncached` go to the first group's call only, and `run_id` to every
    row. Raises what `embed_sync` raises before sending (a task that is not
    an embed task, a space whose options do not match it), unchanged."""
    if task not in routing.EMBED_TASKS:
        raise ValueError(f"{task!r} is not an embed task")
    if deadline is None:
        deadline = time.monotonic() + embeddings.TIMEOUT
    out: list[GroupResult] = []
    stopped = False
    for n, group in enumerate(groups):
        if stopped:
            out.append(GroupResult(None, GROUP_NOT_SENT))
            continue
        try:
            got = GroupResult(embed_sync(
                task, list(group.texts), space=space, client=client, deadline=deadline,
                budgeted=budgeted, campaign=group.campaign, scene=group.scene, run_id=run_id,
                cached=cached if n == 0 else None, uncached=uncached if n == 0 else None))
        except embeddings.EmbeddingsError as exc:
            not_sent = getattr(exc, "code", None) == embeddings.NOT_SENT
            got = GroupResult(None, GROUP_NOT_SENT if not_sent else exc.kind)
            stopped = exc.kind != "bad_response"
        out.append(got)
        if on_group is not None and got.error != GROUP_NOT_SENT:
            try:
                on_group(group, got)
            except Exception as exc:  # noqa: BLE001 - the docstring's rule
                errors.record_exception(exc, task, campaign=group.campaign, task=task)
                stopped = True
    return out
